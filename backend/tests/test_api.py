"""Gateway API checks use isolated storage and fake motor output only."""
import importlib
import inspect
import types
import time
import pytest
from fastapi.testclient import TestClient
from backend.controller import Controller, DirectUsbAdapter, OfflineAdapter
from backend.storage import Store

ORIGIN = {'origin': 'http://localhost:5173'}

@pytest.fixture
def api(monkeypatch, tmp_path):
    module = importlib.import_module('backend.app')
    store = Store(tmp_path)
    adapter = OfflineAdapter('ROS disabled in API tests')
    controller = Controller(adapter, store)
    moves = []
    bridge = types.SimpleNamespace(enabled=False, send=lambda v=0., w=0.: moves.append((v,w)), disable=lambda: None)
    controller.motion_output = bridge.send
    for name, value in {'ROOT':tmp_path,'store':store,'adapter':adapter,'control':controller,'direct_servo':bridge,'requested_mode':'hardware'}.items():
        monkeypatch.setattr(module, name, value)
    for name in ('sessions','acks','pairings','pair_failures','device_state'):
        monkeypatch.setattr(module, name, {})
    monkeypatch.setattr(module, 'secure_network', lambda: {'public_url':'https://laptop.test-tailnet.ts.net','reason':''})
    monkeypatch.setattr(module, 'wasd_terminal', types.SimpleNamespace(stop=lambda: None))
    with TestClient(module.app) as client:
        yield module, client, bridge, moves


def create_session(client, role='cockpit', pair=''):
    result = client.post('/session', params={'role':role,'pair':pair}, headers=ORIGIN)
    assert result.status_code == 200, result.text
    session = result.json()
    return session, {'authorization':'Bearer '+session['token']}


def command(action, proof, seq, command_id=None, **fields):
    if action in ('drive','timed'):fields={'linear':0.,'angular':0.,**fields}
    return {'id':command_id or f'{action}-{seq}','action':action,
            **{key:proof[key] for key in ('epoch','generation','permit')},'seq':seq,**fields}


def ws_ack(ws):
    while True:
        result = ws.receive_json()
        if result['type'] == 'ack':return result


def test_origin_auth_storage_and_socket(api):
    _, client, _, _ = api
    assert client.post('/session', headers={'origin':'https://untrusted.example'}).status_code == 403
    session, headers = create_session(client)
    assert client.get('/library').status_code == 401
    mission = {'name':'Test','map_id':'demo','points':[{'x':0,'y':1}],'return_home':False}
    assert client.post('/missions', headers=headers, json=mission).status_code == 200
    assert client.get('/library', headers=headers).json()['missions'][0]['name'] == 'Test'
    with client.websocket_connect('/ws', headers=ORIGIN) as ws:
        ws.send_json({'token':session['token']})
        ws.send_json(command('claim', session['proof'], 1))
        ack = ws_ack(ws)
        assert ack['ok'] and ack['armed'] and ack['authority']
        drive = command('drive', ack['proof'], 2, linear=.1)
        ws.send_json(drive)
        ack = ws_ack(ws)
        assert not ack['ok'] and 'LiDAR' in ack['error']
        ws.send_json(drive)
        assert not ws_ack(ws)['ok']


def test_tablet_pairing_readiness_profile_and_explicit_resume(api):
    module, client, _, _ = api
    _, headers = create_session(client)
    readiness = client.get('/readiness', headers=headers).json()
    assert readiness['ready'] is False and 'wheel_separation_m' in readiness['missing_calibration']
    pairing = client.post('/pairing', headers={**ORIGIN,**headers}).json()
    assert len(pairing['code']) == 6 and pairing['url'].startswith('https://laptop.test-tailnet.ts.net/tablet?pair=')
    assert pairing['secure'] and pairing['expires_seconds'] == 300
    assert client.get('/pairing/qr.svg', params={'code':pairing['code']}).status_code == 401
    tablet, tablet_headers = create_session(client, 'tablet', pairing['code'])
    assert client.post('/session', params={'role':'tablet','pair':pairing['code']}, headers=ORIGIN).status_code == 403
    ack = client.post('/command', headers=tablet_headers, json=command('claim', tablet['proof'], 1, source='gesture')).json()
    assert ack['ok'] and module.control.armed
    resumed = client.post('/session/resume', headers={**ORIGIN,**tablet_headers})
    assert resumed.status_code == 200
    replacement = resumed.json()
    assert replacement['token'] != tablet['token'] and replacement['role'] == 'tablet'
    assert not replacement['armed'] and not replacement['authority']
    assert module.control.owner is None and not module.control.armed
    assert client.post('/heartbeat', headers=tablet_headers).status_code == 401
    profile = client.get('/hardware-profile', headers=headers).json()
    assert profile['wheel_circumference_m'] == .21038
    assert profile['right_motor_id'] == 1 and profile['left_motor_id'] == 2


def test_pairing_requires_known_https_and_exact_origin(api, monkeypatch):
    module, client, _, _ = api
    _, headers = create_session(client)
    assert client.post('/session', headers={'origin':'https://different-host.ts.net'}).status_code == 403
    monkeypatch.setattr(module, 'secure_network', lambda: {'public_url':None,'reason':'Configure the exact HTTPS address'})
    response = client.post('/pairing', headers={**ORIGIN,**headers})
    assert response.status_code == 503 and 'HTTPS' in response.json()['detail']


def use_direct_usb(module, bridge, monkeypatch):
    bridge.enabled = True
    adapter = DirectUsbAdapter(lambda: True)
    monkeypatch.setattr(module, 'adapter', adapter)
    monkeypatch.setattr(module, 'requested_mode', 'direct_usb')
    module.control.adapter = adapter


@pytest.mark.parametrize('stop_action', ['stop','cancel'])
def test_delayed_forward_is_rejected_after_stop_and_rearm(api, monkeypatch, stop_action):
    module, client, bridge, moves = api
    use_direct_usb(module, bridge, monkeypatch)
    session, headers = create_session(client)
    claim = client.post('/command', headers=headers, json=command('claim', session['proof'], 1)).json()
    delayed = command('drive', claim['proof'], 2, linear=.06)
    stopped = client.post('/command', headers=headers, json={'id':'global-stop','action':stop_action}).json()
    assert stopped['ok'] and not stopped['armed'] and module.control.owner is None
    rearmed = client.post('/command', headers=headers, json=command('claim', stopped['proof'], 3)).json()
    assert rearmed['ok'] and rearmed['armed']
    count = len(moves)
    rejected = client.post('/command', headers=headers, json=delayed)
    assert rejected.status_code == 409 and 'Stale control generation' in rejected.json()['error']
    assert len(moves) == count
    duplicate_stop = client.post('/command', headers=headers, json={'id':'global-stop','action':stop_action}).json()
    assert duplicate_stop['ok'] and not duplicate_stop['armed']
    assert module.control.owner is None and moves[-1] == (0.,0.)


def test_cached_claim_never_reports_authority_after_stop(api):
    module, client, _, _ = api
    session, headers = create_session(client)
    original = command('claim', session['proof'], 1)
    assert client.post('/command', headers=headers, json=original).json()['authority']
    client.post('/command', headers=headers, json={'id':'stop','action':'stop'})
    retried = client.post('/command', headers=headers, json=original).json()
    assert not retried['authority'] and not retried['armed']
    assert not module.control.armed and module.control.owner is None


def test_fresh_command_proof_comes_from_authenticated_heartbeat(api):
    module, client, _, _ = api
    session, headers = create_session(client)
    heartbeat = client.post('/heartbeat', headers=headers).json()
    assert heartbeat['proof']['epoch'] == session['proof']['epoch']
    assert heartbeat['proof']['expires_in_ms'] == 750 and not heartbeat['armed']
    response = client.post('/command', headers=headers, json=command('claim', heartbeat['proof'], 1))
    assert response.json()['ok'] and module.control.armed
    assert response.json()['proof']['generation'] > heartbeat['proof']['generation']


def test_gateway_rejects_expired_and_out_of_order_commands(api, monkeypatch):
    module, client, bridge, moves = api
    use_direct_usb(module, bridge, monkeypatch)
    session, headers = create_session(client)
    claim = client.post('/command', headers=headers, json=command('claim', session['proof'], 1)).json()
    expired = command('drive', claim['proof'], 2, linear=.06)
    module.control.command_permits[expired['permit']]['until'] = time.monotonic() - .1
    response = client.post('/command', headers=headers, json=expired)
    assert response.status_code == 409 and 'freshness permit' in response.json()['error']
    fresh = response.json()['proof']
    accepted = client.post('/command', headers=headers, json=command('drive', fresh, 4, linear=.06)).json()
    assert accepted['ok'] and moves[-1] == (.06,0.)
    count = len(moves)
    old_sequence = client.post('/command', headers=headers, json=command('drive', accepted['proof'], 3, linear=-.06))
    assert old_sequence.status_code == 409 and 'sequence' in old_sequence.json()['error']
    assert len(moves) == count


def test_mode_switch_revokes_in_flight_motion(api, monkeypatch):
    module, client, bridge, moves = api
    use_direct_usb(module, bridge, monkeypatch)
    session, headers = create_session(client)
    claim = client.post('/command', headers=headers, json=command('claim', session['proof'], 1)).json()
    delayed = command('drive', claim['proof'], 2, linear=.06)
    changed = client.post('/mode', headers=headers, json={'mode':'simulation'})
    assert changed.status_code == 200 and not module.control.armed
    count = len(moves)
    rejected = client.post('/command', headers=headers, json=delayed)
    assert rejected.status_code == 409 and 'Stale control generation' in rejected.json()['error']
    assert len(moves) == count


def test_failed_usb_enable_can_retry_explicitly_and_stays_disarmed(api, monkeypatch):
    module, client, bridge, _ = api
    session, headers = create_session(client)
    assert client.post('/command', headers=headers, json=command('claim',session['proof'],1)).json()['ok']
    attempts=[]
    def enable():
        attempts.append(True)
        assert not module.control.armed and isinstance(module.control.adapter,OfflineAdapter)
        if len(attempts)==1:raise ValueError('Mock USB not ready')
        bridge.enabled=True
    monkeypatch.setattr(bridge,'enable',enable,raising=False)
    monkeypatch.setattr(bridge,'disable',lambda:setattr(bridge,'enabled',False))
    monkeypatch.setattr(bridge,'status',lambda:{'enabled':bridge.enabled},raising=False)
    first=client.post('/direct-servo?enabled=true',headers=headers)
    assert first.status_code==422 and not module.control.armed and not bridge.enabled
    second=client.post('/direct-servo?enabled=true',headers=headers)
    assert second.status_code==200 and second.json()['enabled']
    assert len(attempts)==2 and not module.control.armed and module.control.owner is None
    # An exited relay must also be explicitly retryable in the same output mode.
    bridge.enabled=False
    third=client.post('/direct-servo?enabled=true',headers=headers)
    assert third.status_code==200 and len(attempts)==3 and not module.control.armed


def test_mode_endpoints_serialize_mutations_and_finish_disarmed(api, monkeypatch):
    module, client, bridge, _ = api
    session, headers = create_session(client)
    assert inspect.iscoroutinefunction(module.select_mode)
    assert inspect.iscoroutinefunction(module.set_direct_servo)
    during=[]
    def enable():
        proof=module.control.issue_permit(session['token'])
        during.append(module.command_result(command('claim',proof,1),session['token']))
        bridge.enabled=True
    monkeypatch.setattr(bridge,'enable',enable,raising=False)
    monkeypatch.setattr(bridge,'disable',lambda:setattr(bridge,'enabled',False))
    monkeypatch.setattr(bridge,'status',lambda:{'enabled':bridge.enabled},raising=False)
    result=client.post('/direct-servo?enabled=true',headers=headers)
    assert result.status_code==200
    # The async route contains no await during the transition, so another HTTP
    # command cannot actually interleave. The injected callback exercises the
    # additional final invalidation before the new adapter becomes usable.
    assert during and module.control.generation > during[0]['proof']['generation']
    assert not module.control.armed and module.control.owner is None


@pytest.mark.parametrize('action',['drive','timed'])
@pytest.mark.parametrize('omitted',[('linear',),('angular',),('linear','angular')])
def test_gateway_rejects_missing_motion_axes(api,monkeypatch,action,omitted):
    module,client,bridge,moves=api
    use_direct_usb(module,bridge,monkeypatch)
    session,headers=create_session(client)
    claim=client.post('/command',headers=headers,json=command('claim',session['proof'],1,source='voice')).json()
    payload=command(action,claim['proof'],2,source='voice',linear=.06,angular=0.,value=1)
    for key in omitted:payload.pop(key)
    count=len(moves)
    response=client.post('/command',headers=headers,json=payload)
    assert response.status_code==409 and 'explicit linear and angular' in response.json()['error']
    assert len(moves)==count
