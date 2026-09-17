import os
import tempfile
os.environ['TBOT_DATA']=tempfile.mkdtemp(prefix='tbot-test-')
os.environ['TBOT_MODE']='offline'
from fastapi.testclient import TestClient
from backend.app import app

def test_origin_auth_storage_and_socket():
    with TestClient(app) as c:
        assert c.post('/session',headers={'origin':'https://untrusted.example'}).status_code==403
        token=c.post('/session',headers={'origin':'http://localhost:5173'}).json()['token'];headers={'authorization':'Bearer '+token}
        assert c.get('/library').status_code==401
        mission={'name':'Test','map_id':'demo','points':[{'x':0,'y':1}],'return_home':False}
        assert c.post('/missions',headers=headers,json=mission).status_code==200
        assert c.get('/library',headers=headers).json()['missions'][0]['name']=='Test'
        with c.websocket_connect('/ws',headers={'origin':'http://localhost:5173'}) as ws:
            ws.send_json({'token':token});ws.send_json({'id':'1','action':'claim'})
            msg=ws.receive_json()
            while msg['type']!='ack':msg=ws.receive_json()
            assert msg['ok']
            ws.send_json({'id':'2','action':'drive','linear':.1})
            msg=ws.receive_json()
            while msg['type']!='ack':msg=ws.receive_json()
            assert not msg['ok'] and 'LiDAR' in msg['error']
            ws.send_json({'id':'2','action':'drive','linear':.1})
            msg=ws.receive_json()
            while msg['type']!='ack':msg=ws.receive_json()
            assert not msg['ok']

def test_tablet_pairing_readiness_and_profile():
    with TestClient(app) as c:
        origin={'origin':'http://localhost:5173'}
        token=c.post('/session',headers=origin).json()['token']
        headers={'authorization':'Bearer '+token}
        readiness=c.get('/readiness',headers=headers).json()
        assert readiness['ready'] is False
        assert 'wheel_separation_m' in readiness['missing_calibration']
        pairing=c.post('/pairing',headers={**origin,**headers}).json()
        assert len(pairing['code'])==6 and '/tablet?pair=' in pairing['url']
        tablet=c.post('/session?role=tablet&pair='+pairing['code'],headers=origin)
        assert tablet.status_code==200
        # Mobile camera permissions can recreate the socket; a pairing code
        # remains valid until its short expiry so that reconnect succeeds.
        assert c.post('/session?role=tablet&pair='+pairing['code'],headers=origin).status_code==200
        profile=c.get('/hardware-profile',headers=headers).json()
        assert profile['wheel_circumference_m']==.21038
        assert profile['right_motor_id']==1 and profile['left_motor_id']==2
