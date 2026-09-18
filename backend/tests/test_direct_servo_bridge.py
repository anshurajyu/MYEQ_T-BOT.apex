"""Mac USB launcher tests use fake children and never touch a serial device."""
import importlib
import io
import json
from pathlib import Path
import types

import pytest


@pytest.fixture
def mac_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv('TBOT_DATA', str(tmp_path / 'data'))
    module = importlib.import_module('backend.usb_transport')
    python = tmp_path / 'python'
    python.touch()
    monkeypatch.setattr(module, 'sys', types.SimpleNamespace(platform='darwin', executable=str(python)))
    monkeypatch.delenv('TBOT_SERIAL_PORT', raising=False)
    monkeypatch.setenv('TBOT_SERVO_PYTHON', str(python))
    monkeypatch.setenv('TBOT_SERVO_PORT', '/dev/cu.usbserial-test')
    exists = Path.exists
    monkeypatch.setattr(Path, 'exists', lambda path: str(path) == '/dev/cu.usbserial-test' or exists(path))
    calls = []

    class Child:
        returncode = None
        def __init__(self, output='TBOT_SERVO_READY\n'):
            self.stdout = io.StringIO(output)
            self.stdin = io.StringIO()
        def poll(self): return self.returncode
        def wait(self, timeout): self.returncode = 0
        def terminate(self): self.returncode = -15

    child = Child()
    def launch(args, **kwargs):
        calls.append(('launch', args, kwargs))
        return child
    def check(args, **kwargs):
        calls.append(('check', args, kwargs))
        return types.SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    monkeypatch.setattr(module.subprocess, 'run', check)
    # Run the output reader synchronously for deterministic readiness tests.
    monkeypatch.setattr(module.threading, 'Thread', lambda target, args, **_: types.SimpleNamespace(start=lambda: target(*args)))
    return module, module.DirectServoBridge(), child, calls


def test_mac_bridge_uses_configured_interpreter_and_ready_stdio(mac_bridge):
    _, bridge, child, calls = mac_bridge
    bridge.enable()
    assert bridge.status()['enabled']
    assert calls[0][1] == [bridge.python, '-c', 'import python_st3215']
    args = calls[1][1]
    assert args[0] == bridge.python and args[-3:] == ['--stdio', '--port', '/dev/cu.usbserial-test']
    assert 'gnome-terminal' not in ' '.join(args)
    bridge.send(.06, -.2)
    payload=json.loads(child.stdin.getvalue())
    assert payload['linear']==.06 and payload['angular']==-.2 and payload['seq']==1
    assert payload['expires_at'] > 0
    assert bridge.status()['last_motor_write'] is None, 'Pipe flush is not an SDK ACK'
    bridge.disable()
    assert not bridge.enabled and child.returncode == 0


@pytest.mark.parametrize('candidates,expected', [([], ''), (['/dev/cu.usbmodem1'], '/dev/cu.usbmodem1'), (['/dev/cu.usbmodem1', '/dev/cu.usbmodem2'], '')])
def test_mac_port_autoselection_requires_exactly_one(mac_bridge, monkeypatch, candidates, expected):
    _, bridge, _, calls = mac_bridge
    monkeypatch.delenv('TBOT_SERVO_PORT')
    from backend import usb_transport
    monkeypatch.setattr(usb_transport, 'serial_port_details', lambda: [{'device':p} for p in candidates])
    monkeypatch.setenv('TBOT_SERVO_VID','1a86')
    monkeypatch.setenv('TBOT_SERVO_PID','7523')
    assert bridge.selected_port() == expected
    if not expected:
        with pytest.raises(ValueError, match='exactly one'):
            bridge.enable()
        assert calls == []


def test_mac_bridge_reports_import_error_without_launch(mac_bridge, monkeypatch):
    module, bridge, _, calls = mac_bridge
    monkeypatch.setattr(module.subprocess, 'run', lambda *_args, **_kwargs: types.SimpleNamespace(returncode=1, stderr='No module named python_st3215'))
    with pytest.raises(ValueError, match='TBOT_SERVO_PYTHON'):
        bridge.enable()
    assert not bridge.enabled and not calls


def test_mac_bridge_reports_failed_child_setup(mac_bridge, monkeypatch):
    module, bridge, child, _ = mac_bridge
    child.stdout = io.StringIO('Permission denied opening USB adapter\n')
    monkeypatch.setattr(module.threading.Event, 'wait', lambda self, timeout: False)
    with pytest.raises(ValueError, match='Permission denied opening USB adapter'):
        bridge.enable()
    assert not bridge.enabled and child.returncode == 0


def test_exited_child_rejects_motion_until_explicit_reconnect(mac_bridge):
    _, bridge, child, _ = mac_bridge
    bridge.enable()
    child.returncode = 1
    assert not bridge.status()['enabled']
    with pytest.raises(ValueError, match='reconnect explicitly'):
        bridge.send(.06, 0)
    bridge.stop()
    assert child.stdin.getvalue() == ''


def test_broken_pipe_rejects_motion_and_marks_failed(mac_bridge):
    _, bridge, child, _ = mac_bridge
    bridge.enable()
    child.stdin.close()
    with pytest.raises(ValueError, match='disconnected'):
        bridge.send(.06, 0)
    assert bridge.failed and not bridge.enabled
    bridge.stop()


def test_nonexistent_linux_override_reports_missing_device_on_mac(mac_bridge, monkeypatch):
    _, bridge, _, calls = mac_bridge
    monkeypatch.setenv('TBOT_SERVO_PORT', '/dev/ttyACM0')
    with pytest.raises(ValueError, match='not found at /dev/ttyACM0'):
        bridge.enable()
    assert calls == []


def test_missing_sdk_confirmation_faults_and_closes_pipe(mac_bridge, monkeypatch):
    module, bridge, child, _ = mac_bridge
    now=[10.]
    monkeypatch.setattr(module.time,'monotonic',lambda:now[0])
    bridge.enable();bridge.send(.06,0)
    assert bridge.status()['last_motor_write'] is None
    now[0]+=.46
    assert not bridge.status()['enabled'] and bridge.failed
    assert child.stdin.closed
    with pytest.raises(ValueError,match='confirmation timed out'):
        bridge.send(.06,0)


def test_sdk_event_is_distinct_from_pipe_flush_and_clears_pending(mac_bridge):
    module, bridge, child, _ = mac_bridge
    bridge.enable();bridge.send(.06,0)
    assert bridge.status()['pending_writes']==1
    child.stdout=io.StringIO(json.dumps({'event':'motor_write','seq':1,'direction':'forward','right_speed':1000,'left_speed':-1000})+'\n')
    bridge.read_output(child,module.threading.Event(),module.threading.Event())
    state=bridge.status()
    assert state['pending_writes']==0
    assert state['last_motor_write']['direction']=='forward'
    assert state['last_motor_write']['right_speed']==1000
    assert state['enabled']
