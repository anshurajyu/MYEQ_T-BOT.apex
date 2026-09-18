"""Real relay process exits with a fake SDK; no physical stopping claims."""
import json
import os
import sys
import time

import pytest

from backend.tests.test_motor_end_to_end import FAKE_SDK, wait_write
from backend.usb_transport import DirectServoBridge


@pytest.fixture
def motor_process(monkeypatch, tmp_path):
    sdk = tmp_path / 'sdk'
    sdk.mkdir()
    (sdk / 'python_st3215.py').write_text(FAKE_SDK)
    port = tmp_path / 'fake-device'
    port.touch()
    events = tmp_path / 'motor-events.jsonl'
    monkeypatch.setenv('PYTHONPATH', str(sdk) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    monkeypatch.setenv('TBOT_TEST_MOTOR_EVENTS', str(events))
    monkeypatch.setenv('TBOT_TEST_FAKE_PORT', str(port))
    monkeypatch.setenv('TBOT_SERIAL_PORT', str(port))
    monkeypatch.setenv('TBOT_SERVO_PYTHON', sys.executable)
    bridge = DirectServoBridge()
    bridge.enable()
    bridge.send(.06, 0)
    wait_write(bridge, 'forward', bridge.sequence)
    try:
        yield bridge, events
    finally:
        bridge.disable()


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize('failure', ['sigterm', 'writer_eof'])
def test_orderly_signal_and_parent_pipe_loss_stop_both_motors(motor_process, failure):
    bridge, path = motor_process
    child = bridge.process
    if failure == 'sigterm':
        child.terminate()
    else:
        bridge.writer.close()
    assert child.wait(timeout=3) == 0
    logged = records(path)
    # These are successful fake-SDK calls executed by the actual child cleanup.
    assert logged[-5:] == [
        {'kind': 'torque', 'id': 1, 'enabled': False},
        {'kind': 'torque', 'id': 2, 'enabled': False},
        {'kind': 'speed', 'id': 1, 'value': 0},
        {'kind': 'speed', 'id': 2, 'value': 0},
        {'kind': 'close'},
    ]
    assert not bridge.status()['enabled'] and bridge.failed
    with pytest.raises(ValueError, match='reconnect explicitly'):
        bridge.send(.06, 0)


def test_killed_relay_faults_bridge_without_restart_or_replay(motor_process):
    bridge, path = motor_process
    child = bridge.process
    child.kill()
    assert child.wait(timeout=3) < 0
    status = bridge.status()
    assert not status['enabled'] and bridge.failed
    assert 'reconnect explicitly' in status['error']
    before = records(path)
    for _ in range(3):
        with pytest.raises(ValueError, match='reconnect explicitly'):
            bridge.send(.06, 0)
        bridge.status()
    time.sleep(.03)
    assert bridge.process is child and child.poll() is not None
    assert records(path) == before
    assert len([item for item in before if item['kind'] == 'open']) == 1
    # Deliberately no assertion that SIGKILL stopped a physical wheel: the
    # killed process cannot execute SDK cleanup or its software watchdog.
