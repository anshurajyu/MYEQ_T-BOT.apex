"""Offline regressions for browser commands reaching the common motor output.

All motors, ROS, speech recognition and network clients are fakes. Nothing in
this suite opens a serial device or starts a hardware service.
"""
import importlib
import io
import json
import sys
import time
import types
import wave

import pytest
from fastapi.testclient import TestClient

from backend.controller import Controller, OfflineAdapter
from backend.storage import Store


class Robot:
    def __init__(self):
        self.moves = []
        self.data = {
            'mode': 'virtual-lab', 'scan_age': 0., 'odom_age': 0.,
            'guard_ready': True, 'navigation_ready': True, 'nav': 'idle',
            'pose': {'x': 0., 'y': 0., 'yaw': 0., 'frame': 'map', 'transform_age': 0.},
            'odom': {'x': 0., 'y': 0., 'yaw': 0.},
        }

    def snapshot(self):
        return self.data.copy()

    def drive(self, linear, angular):
        self.moves.append((linear, angular))

    def stop(self):
        self.moves.append((0., 0.))


class Bridge:
    def __init__(self):
        self.enabled = True
        self.moves = []

    def send(self, linear=0., angular=0.):
        if self.enabled:
            self.moves.append((linear, angular))

    def stop(self):
        self.send()

    def disable(self):
        self.send()
        self.enabled = False


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    monkeypatch.setenv('TBOT_DATA', str(tmp_path))
    monkeypatch.setenv('TBOT_MODE', 'offline')
    monkeypatch.delenv('TBOT_ROS', raising=False)
    module = importlib.import_module('backend.app')
    robot, bridge = Robot(), Bridge()
    store = Store(tmp_path)
    controller = Controller(robot, store)
    controller.motion_output = bridge.send
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'store', store)
    monkeypatch.setattr(module, 'adapter', robot)
    monkeypatch.setattr(module, 'control', controller)
    monkeypatch.setattr(module, 'direct_servo', bridge)
    monkeypatch.setattr(module, 'requested_mode', 'simulation')
    monkeypatch.setattr(module, 'voice_model', None)
    monkeypatch.setattr(module, 'secure_network', lambda: {'public_url': 'https://test-machine.test-tailnet.ts.net', 'reason': ''})
    for name in ('sessions', 'acks', 'pairings', 'pair_failures', 'device_state'):
        monkeypatch.setattr(module, name, {})
    with TestClient(module.app) as client:
        # These delivery tests create new human commands. Replay/freshness tests
        # use unchanged envelopes separately in test_safety_protocol.py.
        post = client.post
        def fresh_post(url, **kwargs):
            if url == '/command' and 'json' in kwargs:
                token = kwargs.get('headers', {}).get('authorization', '').removeprefix('Bearer ')
                kwargs['json'] = fresh_payload(module, token, kwargs['json'])
            return post(url, **kwargs)
        monkeypatch.setattr(client, 'post', fresh_post)
        yield module, client, robot, bridge


ORIGIN = {'origin': 'http://localhost:5173'}


def session(client, tablet=False):
    token = client.post('/session', headers=ORIGIN).json()['token']
    headers = {'authorization': 'Bearer ' + token}
    if tablet:
        pair = client.post('/pairing', headers={**headers, **ORIGIN}).json()['code']
        token = client.post('/session?role=tablet&pair=' + pair, headers=ORIGIN).json()['token']
        headers = {'authorization': 'Bearer ' + token}
    return token, headers


def fresh_payload(module, token, payload):
    if payload.get('action') in ('drive','timed'):payload={'linear':0.,'angular':0.,**payload}
    if payload.get('action') == 'stop' or 'epoch' in payload:return payload
    proof = module.control.issue_permit(token);proof.pop('expires_in_ms')
    return {**payload, **proof, 'seq': module.control.command_sequences.get(token, 0) + 1}


def ws_ack(ws, payload, token):
    ws.send_json(fresh_payload(importlib.import_module('backend.app'), token, payload))
    while True:
        result = ws.receive_json()
        if result['type'] == 'ack':
            return result


@pytest.mark.parametrize('source', ['keyboard', 'gamepad', 'gesture'])
@pytest.mark.parametrize('transport', ['http', 'websocket'])
def test_manual_sources_reach_same_motor_output(gateway, source, transport):
    module, client, robot, bridge = gateway
    token, headers = session(client, tablet=source == 'gesture')
    claim = {'id': 'claim', 'action': 'claim', 'source': source}
    drive = {'id': 'move', 'action': 'drive', 'source': source, 'linear': .06, 'angular': .2}
    stop = {'id': 'stop', 'action': 'stop', 'source': source}
    if transport == 'http':
        assert client.post('/command', headers=headers, json=claim).json()['ok']
        assert client.post('/command', headers=headers, json=drive).json()['ok']
        assert bridge.moves[-1] == robot.moves[-1] == (.06, .2)
        assert client.post('/command', headers=headers, json=stop).json()['ok']
    else:
        with client.websocket_connect('/ws', headers=ORIGIN) as ws:
            ws.send_json({'token': token})
            assert ws_ack(ws, claim, token)['ok']
            assert ws_ack(ws, drive, token)['ok']
            assert bridge.moves[-1] == robot.moves[-1] == (.06, .2)
            assert ws_ack(ws, stop, token)['ok']
    assert bridge.moves[-1] == robot.moves[-1] == (0., 0.)
    assert module.control.state == 'idle'


@pytest.mark.parametrize('phrase,motion', [
    ('forward', (.06, 0.)), ('backward', (-.045, 0.)),
    ('turn left', (0., .35)), ('turn right', (0., -.35)),
])
def test_timed_voice_reaches_motor_output_and_stops(gateway, phrase, motion):
    module, client, robot, bridge = gateway
    _, headers = session(client)
    interpreted = client.post('/voice/interpret', headers=headers, json={'text': phrase}).json()
    assert client.post('/command', headers=headers, json={'id': 'claim', 'action': 'claim', 'source': 'voice'}).json()['ok']
    command = {'id': 'spoken', **interpreted['command']}
    assert client.post('/command', headers=headers, json=command).json()['ok']
    module.control.tick()
    assert bridge.moves[-1] == robot.moves[-1] == motion
    module.control.timed_until = time.monotonic() - 1
    module.control.tick()
    assert bridge.moves[-1] == robot.moves[-1] == (0., 0.)
    assert module.control.status == 'Voice movement complete'


def test_command_retry_does_not_restart_timed_motion(gateway):
    module, client, _, _ = gateway
    token, headers = session(client)
    client.post('/command', headers=headers, json={'id': 'claim', 'action': 'claim', 'source': 'voice'})
    command = {'id': 'same-command', 'action': 'timed', 'source': 'voice', 'value': 5, 'linear': .06}
    first = client.post('/command', headers=headers, json=command).json()
    deadline = module.control.timed_until
    # A fallback transport must acknowledge the original command without
    # extending its movement deadline or claiming control again.
    with client.websocket_connect('/ws', headers=ORIGIN) as ws:
        ws.send_json({'token': token})
        retried = ws_ack(ws, command, token)
        assert retried['ok'] == first['ok'] and retried['id'] == first['id']
        assert retried['action'] == first['action']
        assert module.control.timed_until == deadline


@pytest.mark.parametrize('reason', ['lease', 'manual_timeout', 'stale_sensor'])
def test_watchdogs_stop_motor_output(gateway, reason):
    module, client, robot, bridge = gateway
    _, headers = session(client)
    client.post('/command', headers=headers, json={'id': 'claim', 'action': 'claim'})
    client.post('/command', headers=headers, json={'id': 'move', 'action': 'drive', 'linear': .06})
    if reason == 'lease':
        module.control.last_heartbeat = time.monotonic() - 2
    elif reason == 'manual_timeout':
        module.control.last_drive = time.monotonic() - 1
    else:
        robot.data['scan_age'] = 2
    module.control.tick()
    assert module.control.state == 'idle'
    assert bridge.moves[-1] == (0., 0.)


def test_socket_disconnect_stops_output(gateway):
    module, client, _, bridge = gateway
    token, _ = session(client)
    with client.websocket_connect('/ws', headers=ORIGIN) as ws:
        ws.send_json({'token': token})
        assert ws_ack(ws, {'id': 'claim', 'action': 'claim'}, token)['ok']
        assert ws_ack(ws, {'id': 'move', 'action': 'drive', 'linear': .06}, token)['ok']
        assert bridge.moves[-1] == (.06, 0.)
    assert module.control.owner is None
    assert bridge.moves[-1] == (0., 0.)


def test_cockpit_takeover_and_source_mismatch(gateway):
    module, client, _, bridge = gateway
    tablet, tablet_headers = session(client, tablet=True)
    cockpit, cockpit_headers = session(client)
    client.post('/command', headers=tablet_headers, json={'id': 'claim', 'action': 'claim', 'source': 'gesture'})
    client.post('/command', headers=tablet_headers, json={'id': 'drive', 'action': 'drive', 'source': 'gesture', 'linear': .06})
    # Transfer is explicit: another browser cannot silently take armed control.
    client.post('/command', headers=cockpit_headers, json={'id': 'transfer-stop', 'action': 'stop'})
    assert client.post('/command', headers=cockpit_headers, json={'id': 'takeover', 'action': 'claim', 'source': 'gamepad'}).json()['ok']
    assert module.control.owner == cockpit
    assert bridge.moves[-1] == (0., 0.)
    old = client.post('/command', headers=tablet_headers, json={'id': 'stale', 'action': 'drive', 'source': 'gesture', 'linear': .06})
    assert old.status_code == 409 and 'Take control' in old.json()['detail']
    wrong = client.post('/command', headers=cockpit_headers, json={'id': 'wrong-source', 'action': 'drive', 'source': 'gesture', 'linear': .06})
    assert wrong.status_code == 409 and 'input source' in wrong.json()['detail']
    assert bridge.moves[-1] == (0., 0.)


@pytest.mark.parametrize('action', ['distance', 'angle', 'mission', 'home_go', 'explore', 'resume'])
def test_direct_servo_cannot_use_simulated_odometry(gateway, action):
    _, client, _, bridge = gateway
    _, headers = session(client)
    client.post('/command', headers=headers, json={'id': 'claim', 'action': 'claim', 'source': 'voice'})
    response = client.post('/command', headers=headers, json={'id': 'unsafe', 'action': action, 'source': 'voice', 'value': 20})
    assert response.status_code == 409
    assert 'no measured robot odometry' in response.json()['detail']
    assert not any(linear or angular for linear, angular in bridge.moves)


def test_disconnected_ros_never_acknowledges_drive_as_motion(gateway, monkeypatch):
    module, client, _, bridge = gateway
    offline = OfflineAdapter('ROS unavailable on this Mac')
    monkeypatch.setattr(module, 'adapter', offline)
    module.control.adapter = offline
    _, headers = session(client)
    client.post('/command', headers=headers, json={'id': 'claim', 'action': 'claim', 'source': 'gesture'})
    response = client.post('/command', headers=headers, json={'id': 'move', 'action': 'drive', 'source': 'gesture', 'linear': .06})
    assert response.status_code == 409 and 'LiDAR' in response.json()['detail']
    assert not any(linear or angular for linear, angular in bridge.moves)


def fake_vosk(monkeypatch, module, transcript='forward'):
    (module.ROOT / 'models' / 'vosk-model-small-en-us-0.15').mkdir(parents=True)

    class Recognizer:
        def __init__(self, _model, _rate): pass
        def AcceptWaveform(self, _data): return False
        def PartialResult(self): return json.dumps({'partial': transcript})
        def FinalResult(self): return json.dumps({'text': transcript})

    monkeypatch.setitem(sys.modules, 'vosk', types.SimpleNamespace(Model=lambda _: object(), KaldiRecognizer=Recognizer))


def test_live_voice_stop_reaches_motor_output(gateway, monkeypatch):
    module, client, _, bridge = gateway
    fake_vosk(monkeypatch, module, 'stop')
    token, headers = session(client)
    client.post('/command', headers=headers, json={'id': 'claim', 'action': 'claim'})
    client.post('/command', headers=headers, json={'id': 'move', 'action': 'drive', 'linear': .06})
    with client.websocket_connect('/voice/live', headers=ORIGIN) as ws:
        ws.send_json({'token': token})
        assert ws.receive_json()['ready']
        ws.send_bytes(b'\0\0' * 160)
        assert ws.receive_json()['stopped']
    assert module.control.state == 'idle'
    assert bridge.moves[-1] == (0., 0.)


def test_wav_transcription_only_interprets_then_command_moves(gateway, monkeypatch):
    module, client, _, bridge = gateway
    fake_vosk(monkeypatch, module)
    _, headers = session(client)
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 160)
    result = client.post('/voice/transcribe', headers={**headers, 'content-type': 'audio/wav'}, content=audio.getvalue())
    assert result.status_code == 200
    assert result.json()['command']['action'] == 'timed'
    assert bridge.moves == []
    bad = client.post('/voice/transcribe', headers=headers, content=b'not a wav file')
    assert bad.status_code == 422


@pytest.mark.parametrize('text', [None, 123, [], '', 'do not move forward', 'forward or backward', 'x' * 501])
def test_bad_voice_input_returns_error_without_output(gateway, text):
    _, client, _, bridge = gateway
    _, headers = session(client)
    response = client.post('/voice/interpret', headers=headers, json={'text': text})
    assert response.status_code == 422
    assert bridge.moves == []


def test_missing_voice_model_is_visible(gateway, monkeypatch):
    module, client, _, bridge = gateway
    monkeypatch.setitem(sys.modules, 'vosk', types.SimpleNamespace(Model=object, KaldiRecognizer=object))
    token, _ = session(client)
    with client.websocket_connect('/voice/live', headers=ORIGIN) as ws:
        ws.send_json({'token': token})
        assert 'model missing' in ws.receive_json()['error']
    assert bridge.moves == []


def test_live_voice_allows_permission_delay_then_bounds_audio(gateway, monkeypatch):
    """Drive the actual handler with virtual waits, without waiting 30 seconds."""
    import asyncio
    module, _, _, bridge = gateway
    fake_vosk(monkeypatch, module, '')
    module.sessions['voice-token'] = {'seen': time.monotonic(), 'role': 'cockpit'}
    waits = []
    real_wait = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        waits.append(timeout)
        return await real_wait(awaitable, .1)

    class Socket:
        headers = ORIGIN
        chunks = 0
        async def accept(self): pass
        async def receive_json(self): return {'token': 'voice-token'}
        async def send_json(self, value): pass
        async def receive_bytes(self):
            self.chunks += 1
            if self.chunks > 1: raise module.WebSocketDisconnect()
            return b'\0\0' * 160
        async def close(self): raise module.WebSocketDisconnect()

    monkeypatch.setattr(module.asyncio, 'wait_for', wait_for)
    asyncio.run(module.live_voice(Socket()))
    assert waits == [5, 30, 3]
    assert bridge.moves == []


def test_failed_motor_stop_cancels_timed_intention(gateway):
    module, client, robot, _ = gateway
    _, headers = session(client)
    client.post('/command', headers=headers, json={'id': 'claim', 'action': 'claim', 'source': 'voice'})
    client.post('/command', headers=headers, json={'id': 'timed', 'action': 'timed', 'source': 'voice', 'linear': .06, 'value': 5})
    def broken_output(*_): raise ValueError('Broken motor pipe')
    module.control.motion_output = broken_output
    try:
        result = client.post('/command', headers=headers, json={'id': 'stop-failed', 'action': 'stop'})
        assert result.status_code == 409
        assert module.control.state == 'idle'
        assert module.control.timed_until == 0 and module.control.timed_motion == (0., 0.)
        assert module.control.source is None and module.control.queue == []
        assert robot.moves[-1] == (0., 0.)
        module.control.motion_output = lambda *_: None
        before = robot.moves[:]
        module.control.tick()
        assert robot.moves == before, 'Restoring output must never resume the stopped timed command'
    finally:
        module.control.motion_output = None


def test_partial_drive_failure_stops_other_output(gateway):
    module, _, robot, _ = gateway
    def broken_output(*_): raise ValueError('Broken motor pipe')
    module.control.motion_output = broken_output
    try:
        with pytest.raises(ValueError, match='Broken motor pipe'):
            module.control.drive(.06, 0.)
        assert robot.moves[-1] == (0., 0.)
        assert module.control.state == 'idle'
    finally:
        module.control.motion_output = None


def test_ticker_survives_repeated_motor_stop_failure(monkeypatch, tmp_path):
    import asyncio
    module = importlib.import_module('backend.app')
    robot = Robot()
    controller = Controller(robot, Store(tmp_path))
    controller.state = 'timed'; controller.timed_until = time.monotonic() + 5
    def broken_output(*_): raise ValueError('Persistent stop failure')
    def failed_tick(): raise ValueError('Tick output failure')
    controller.motion_output = broken_output
    monkeypatch.setattr(controller, 'tick', failed_tick)
    monkeypatch.setattr(module, 'control', controller)
    monkeypatch.setattr(module, 'adapter', robot)

    async def check():
        task = asyncio.create_task(module.ticker())
        try:
            await asyncio.sleep(.12)
            assert not task.done(), 'Repeated failed Stop must not kill the watchdog ticker'
            assert controller.state == 'idle' and controller.timed_until == 0
            assert 'Persistent stop failure' in controller.status
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


def test_enabling_usb_stops_previous_simulation_motion(gateway, monkeypatch):
    module, client, robot, bridge = gateway
    _, headers = session(client)
    module.control.state = 'navigating'
    module.control.queue = [{'x': 1, 'y': 0}]
    module.control.timed_until = time.monotonic() + 5
    bridge.enabled = False
    def enable():
        assert module.control.state == 'idle' and module.control.queue == []
        assert module.control.timed_until == 0 and robot.moves[-1] == (0., 0.)
        bridge.enabled = True
    monkeypatch.setattr(bridge, 'enable', enable, raising=False)
    monkeypatch.setattr(bridge, 'status', lambda: {'enabled': bridge.enabled}, raising=False)
    result = client.post('/direct-servo?enabled=true', headers=headers)
    assert result.status_code == 200 and result.json()['enabled']


def test_shutdown_closes_bridge_after_stop_failure(monkeypatch, tmp_path):
    import asyncio
    module = importlib.import_module('backend.app')
    controller = Controller(Robot(), Store(tmp_path))
    bridge = Bridge()
    def broken_output(*_): raise ValueError('Shutdown pipe failed')
    controller.motion_output = broken_output
    monkeypatch.setattr(module, 'control', controller)
    monkeypatch.setattr(module, 'adapter', controller.adapter)
    monkeypatch.setattr(module, 'direct_servo', bridge)
    monkeypatch.setattr(module, 'wasd_terminal', types.SimpleNamespace(stop=lambda: None))
    async def check():
        async with module.lifespan(module.app):
            pass
    asyncio.run(check())
    assert not bridge.enabled and controller.state == 'idle'
    assert 'Shutdown pipe failed' in controller.status
