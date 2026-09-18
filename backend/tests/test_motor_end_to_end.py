"""Real gateway/controller/stdio bridge/relay chain, with only the motor SDK faked.

The child imports an isolated fake python_st3215 module. Its port is a regular
pytest temporary file. No real serial descriptor or physical motor is opened.
"""
import importlib
import json
import os
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from backend.controller import Controller, DirectUsbAdapter
from backend.storage import Store
from backend.usb_transport import DirectServoBridge

FAKE_SDK = '''
import json, os
from pathlib import Path

def log(kind, **fields):
    with open(os.environ['TBOT_TEST_MOTOR_EVENTS'], 'a') as out:
        out.write(json.dumps({'kind':kind, **fields})+'\\n')

class Servo:
    def __init__(self, servo_id):
        self.servo_id=servo_id
        self.sram=self.eeprom=self
    def reply(self):return {'id':self.servo_id,'error':0,'checksum_valid':True}
    def send(self, instruction, parameters):
        assert instruction==0x02 and parameters==[0x21,1]
        return {**self.reply(),'parameters':bytes([1])}
    def write_running_speed(self,value):
        log('speed',id=self.servo_id,value=value)
        return self.reply()
    def torque_enable(self):
        log('torque',id=self.servo_id,enabled=True)
        return self.reply()
    def torque_disable(self):
        log('torque',id=self.servo_id,enabled=False)
        return self.reply()
    def unlock(self):return self.reply()
    def lock(self):return self.reply()
    def write_operating_mode(self,value):return self.reply()

class ST3215:
    def __init__(self,port):
        assert Path(port).resolve()==Path(os.environ['TBOT_TEST_FAKE_PORT']).resolve()
        log('open',port=port)
    def wrap_servo(self,servo_id,verify=False):return Servo(servo_id)
    def close(self):log('close')
'''
ORIGIN = {'origin':'http://localhost:5173'}


@pytest.fixture
def motor_gateway(monkeypatch,tmp_path):
    fake_sdk=tmp_path/'sdk';fake_sdk.mkdir()
    (fake_sdk/'python_st3215.py').write_text(FAKE_SDK)
    fake_port=tmp_path/'fake-servo-device';fake_port.touch()
    events=tmp_path/'motor-events.jsonl'
    monkeypatch.setenv('PYTHONPATH',str(fake_sdk)+os.pathsep+os.environ.get('PYTHONPATH',''))
    monkeypatch.setenv('TBOT_TEST_MOTOR_EVENTS',str(events))
    monkeypatch.setenv('TBOT_TEST_FAKE_PORT',str(fake_port))
    monkeypatch.setenv('TBOT_SERIAL_PORT',str(fake_port))
    monkeypatch.setenv('TBOT_SERVO_PYTHON',sys.executable)
    module=importlib.import_module('backend.app')
    bridge=DirectServoBridge()
    store=Store(tmp_path/'data')
    adapter=DirectUsbAdapter(lambda:bridge.status()['enabled'])
    control=Controller(adapter,store)
    control.motion_output=bridge.send
    for name,value in {'ROOT':tmp_path/'data','store':store,'adapter':adapter,'control':control,'direct_servo':bridge,'requested_mode':'direct_usb'}.items():
        monkeypatch.setattr(module,name,value)
    for name in ('sessions','acks','pairings','pair_failures','device_state'):
        monkeypatch.setattr(module,name,{})
    monkeypatch.setattr(module,'secure_network',lambda:{'public_url':'https://test-machine.test-tailnet.ts.net','reason':''})
    monkeypatch.setattr(module,'wasd_terminal',types.SimpleNamespace(stop=lambda:None))
    bridge.enable()
    try:
        with TestClient(module.app) as client:
            yield module,client,bridge,events
    finally:
        bridge.disable()


def wait_write(bridge,direction,minimum_seq):
    deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        status=bridge.status();written=status['last_motor_write']
        if written and written['direction']==direction and written['seq']>=minimum_seq:return written
        assert status['enabled'],status
        time.sleep(.005)
    raise AssertionError(f'Relay never confirmed {direction}: {bridge.status()}')


def stamped(action,proof,seq,**fields):
    return {'id':f'{action}-{seq}','action':action,'seq':seq,
            **{key:proof[key] for key in ('epoch','generation','permit')},**fields}


def receive_ack(ws):
    while True:
        response=ws.receive_json()
        if response['type']=='ack':return response


@pytest.mark.parametrize('source',['keyboard','gamepad','gesture','voice'])
@pytest.mark.parametrize('phrase,linear,angular,expected',[
    ('forward',.06,0.,(1000,-1000)),('backward',-.045,0.,(-1000,1000)),
    ('left',0.,.35,(1000,1000)),('right',0.,-.35,(-1000,-1000)),
])
def test_all_controls_reach_actual_relay_signed_motor_writes(motor_gateway,source,phrase,linear,angular,expected):
    module,client,bridge,events=motor_gateway
    session=client.post('/session',headers=ORIGIN).json()
    headers={'authorization':'Bearer '+session['token']}
    if source=='gesture':
        pair=client.post('/pairing',headers={**ORIGIN,**headers}).json()['code']
        session=client.post('/session',params={'role':'tablet','pair':pair},headers=ORIGIN).json()
        headers={'authorization':'Bearer '+session['token']}
    movement={'action':'drive','source':source,'linear':linear,'angular':angular}
    if source=='voice':
        movement=client.post('/voice/interpret',headers=headers,json={'text':phrase}).json()['command']
        assert movement['action']=='timed'

    def exercise(send):
        ack=send(stamped('claim',session['proof'],1,source=source))
        assert ack['ok'] and ack['armed'],ack
        action=movement['action']
        payload=stamped(action,ack['proof'],2,**{key:value for key,value in movement.items() if key!='action'})
        minimum=bridge.sequence+1
        ack=send(payload)
        assert ack['ok'],ack
        written=wait_write(bridge,phrase,minimum)
        assert (written['right_speed'],written['left_speed'])==expected
        records=[json.loads(line) for line in events.read_text().splitlines()]
        speeds=[(record['id'],record['value']) for record in records if record['kind']=='speed' and record['value']]
        assert speeds[-2:]==[(1,expected[0]),(2,expected[1])]
        assert all(any(record=={'kind':'torque','id':servo_id,'enabled':True} for record in records) for servo_id in (1,2))
        minimum=bridge.sequence+1
        stopped=send({'id':'final-stop','action':'stop'})
        assert stopped['ok'] and not stopped['armed'] and module.control.owner is None
        assert wait_write(bridge,'stop',minimum)['right_speed']==0

    if source=='gesture':
        exercise(lambda payload:client.post('/command',headers=headers,json=payload).json())
    else:
        with client.websocket_connect('/ws',headers=ORIGIN) as ws:
            ws.send_json({'token':session['token']})
            def send(payload):
                ws.send_json(payload)
                return receive_ack(ws)
            exercise(send)
