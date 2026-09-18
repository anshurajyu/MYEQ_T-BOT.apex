"""Actual relay loop with mocked SDK/serial; no motor hardware is opened."""
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest


def packet(linear=.06, angular=0, seq=1, expires=1.25):
    return json.dumps({'linear': linear, 'angular': angular, 'seq': seq, 'expires_at': expires}).encode() + b'\n'


def load_relay(monkeypatch, chunks, ready_flags=None, *, modes=(1, 1), failure=None, readback=False):
    events, now, servos = [], [1.], {}

    class Servo:
        def __init__(self, sid):
            self.id = sid
            self.mode = modes[sid - 1]
            self.sram = self.eeprom = self
        def result(self, operation, value=None):
            events.append((operation, self.id) if value is None else (operation, self.id, value))
            if failure and failure(operation, self.id, value, events):
                return None
            return {'id': self.id, 'error': 0, 'checksum_valid': True}
        def write_operating_mode(self, mode):
            response = self.result('mode', mode)
            if not readback: self.mode = mode
            return response
        def write_running_speed(self, speed): return self.result('speed', speed)
        def torque_enable(self): return self.result('enable')
        def torque_disable(self): return self.result('disable')
        def unlock(self): return self.result('unlock')
        def lock(self): return self.result('lock')
        def send(self, instruction, parameters):
            assert (instruction, parameters) == (0x02, [0x21, 1])
            response = self.result('read_mode')
            if response is not None: response['parameters'] = bytes([self.mode])
            return response

    class Bus:
        def __init__(self, port): events.append(('port', port))
        def wrap_servo(self, sid, verify=False):
            assert verify is False
            servos[sid] = Servo(sid)
            return servos[sid]
        def close(self): events.append(('close',))

    class Stream:
        def fileno(self): return 42
        def close(self): events.append(('stream_close',))

    stream = Stream()
    monkeypatch.setitem(sys.modules, 'python_st3215', types.SimpleNamespace(ST3215=Bus))
    path = Path(__file__).resolve().parents[2] / 'robot-code/dashboard_wasd_relay.py'
    spec = importlib.util.spec_from_file_location('tbot_test_relay', path)
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    chunks = iter(chunks)
    flags = iter(ready_flags or [True] * 30)
    def ready(*_args):
        available = next(flags)
        now[0] += .01 if available else .31
        events.append(('poll', available))
        return ([stream] if available else []), [], []
    monkeypatch.setattr(relay, 'os', types.SimpleNamespace(read=lambda *_: next(chunks, b''),
                        open=lambda *_: 42, fdopen=lambda *_args, **_kwargs: stream, O_RDONLY=0, O_NONBLOCK=1))
    monkeypatch.setattr(relay, 'select', types.SimpleNamespace(select=ready))
    monkeypatch.setattr(relay, 'time', types.SimpleNamespace(monotonic=lambda: now[0], sleep=lambda _: None))
    monkeypatch.setattr(relay, 'select_servo_port', lambda value=None: value or '/dev/cu.usbserial-test')
    return relay, stream, events, servos


def emitted(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]


def assert_clean_stop(events):
    assert events[-6:] == [('disable', 1), ('disable', 2), ('speed', 1, 0), ('speed', 2, 0), ('stream_close',), ('close',)]


@pytest.mark.parametrize('initial_empty', [False, True])
def test_fifo_writer_disconnect_stops_motors(monkeypatch, initial_empty):
    chunks = ([b''] if initial_empty else []) + [packet(), b'']
    relay, _, events, _ = load_relay(monkeypatch, chunks)
    relay.main('/fake/fifo')
    assert ('speed', 1, 1000) in events and ('speed', 2, -1000) in events
    assert_clean_stop(events)


@pytest.mark.parametrize('linear,angular,speeds', [(.06, 0, (1000, -1000)), (-.06, 0, (-1000, 1000)), (0, .2, (1000, 1000)), (0, -.2, (-1000, -1000))])
def test_stdio_burst_stop_preserves_working_motor_directions(monkeypatch, capsys, linear, angular, speeds):
    commands = packet(linear, angular) + packet(0, 0, 2) + b'{"action":"quit"}\n'
    relay, stream, events, _ = load_relay(monkeypatch, [commands])
    relay.main(command_stream=stream)
    assert events[0] == ('port', '/dev/cu.usbserial-test')
    assert [item for item in events if item[0] == 'speed' and item[2]] == [('speed', 1, speeds[0]), ('speed', 2, speeds[1])]
    assert events.count(('poll', True)) == 1
    assert_clean_stop(events)
    written = emitted(capsys)
    assert written[1] == {'event': 'motor_write', 'seq': 1, 'direction': ('forward' if linear > 0 else 'backward' if linear < 0 else 'left' if angular > 0 else 'right'), 'right_speed': speeds[0], 'left_speed': speeds[1]}
    assert written[2]['seq'] == 2 and written[2]['direction'] == 'stop'


def test_stdio_partial_command_is_assembled_without_blocking(monkeypatch):
    data = packet()
    relay, stream, events, _ = load_relay(monkeypatch, [data[:12], data[12:], b''])
    relay.main(command_stream=stream)
    assert events.index(('speed', 1, 1000)) > [i for i, item in enumerate(events) if item[0] == 'poll'][1]
    assert_clean_stop(events)


def test_stdio_silent_writer_watchdog_stops_and_exits_before_old_motion(monkeypatch, capsys):
    relay, stream, events, _ = load_relay(monkeypatch, [packet(), packet(seq=2, expires=2.)], [True, False, True])
    with pytest.raises(TimeoutError, match='watchdog'):
        relay.main(command_stream=stream)
    assert events.count(('speed', 1, 1000)) == 1
    assert events.count(('poll', True)) == 1
    assert_clean_stop(events)
    assert emitted(capsys)[-1]['event'] == 'fault'


def test_stdio_eof_without_first_command_stops_cleanly(monkeypatch):
    relay, stream, events, _ = load_relay(monkeypatch, [b''])
    relay.main(command_stream=stream)
    assert not any(item[0] == 'enable' for item in events)
    assert_clean_stop(events)


def test_setup_disables_both_before_mode_change_and_skips_unchanged_eeprom(monkeypatch, capsys):
    relay, stream, events, _ = load_relay(monkeypatch, [b''], modes=(0, 1))
    relay.main(command_stream=stream)
    assert events[1:5] == [('disable', 1), ('disable', 2), ('speed', 1, 0), ('speed', 2, 0)]
    assert events.count(('mode', 1, 1)) == 1
    assert ('mode', 2, 1) not in events
    assert events.index(('unlock', 1)) < events.index(('mode', 1, 1)) < events.index(('lock', 1))
    assert events.count(('read_mode', 1)) == 2
    assert 'TBOT_SERVO_READY' in capsys.readouterr().out


def test_mode_readback_failure_never_announces_ready(monkeypatch, capsys):
    relay, stream, events, _ = load_relay(monkeypatch, [], modes=(0, 1), readback=True)
    with pytest.raises(OSError, match='readback'):
        relay.main(command_stream=stream)
    output = capsys.readouterr().out
    assert 'TBOT_SERVO_READY' not in output
    assert '"event": "fault"' in output
    assert not any(item[0] == 'enable' for item in events)
    assert ('disable', 2) in events and ('speed', 2, 0) in events


@pytest.mark.parametrize('bad', [
    [], {'action': 'forward'}, {'linear': .06, 'angular': 0},
    {'linear': float('nan'), 'angular': 0, 'seq': 1, 'expires_at': 1.25},
    {'linear': float('inf'), 'angular': 0, 'seq': 1, 'expires_at': 1.25},
    {'linear': True, 'angular': 0, 'seq': 1, 'expires_at': 1.25},
    {'linear': .06, 'angular': 0, 'seq': True, 'expires_at': 1.25},
    {'linear': .06, 'angular': 0, 'seq': 1, 'expires_at': 0},
    {'linear': .06, 'angular': 0, 'seq': 1, 'expires_at': 99},
    {'linear': .06, 'angular': 0, 'seq': 1, 'expires_at': float('nan')},
    {'linear': 1, 'angular': 0, 'seq': 1, 'expires_at': 1.25},
])
def test_invalid_packets_fault_stop_without_enabling(monkeypatch, capsys, bad):
    relay, stream, events, _ = load_relay(monkeypatch, [json.dumps(bad).encode() + b'\n'])
    with pytest.raises((ValueError, OSError)):
        relay.main(command_stream=stream)
    assert not any(item[0] == 'enable' for item in events)
    assert_clean_stop(events)
    assert emitted(capsys)[-1]['event'] == 'fault'


def test_delayed_stop_is_honored_but_cannot_reset_sequence(monkeypatch, capsys):
    relay, stream, events, _ = load_relay(monkeypatch, [packet(seq=5) + packet(0, 0, seq=1, expires=0) + packet(seq=4)])
    with pytest.raises(ValueError, match='sequence'):
        relay.main(command_stream=stream)
    written = emitted(capsys)
    assert any(item.get('seq') == 1 and item.get('direction') == 'stop' for item in written)
    assert events.count(('speed', 1, 1000)) == 1
    assert_clean_stop(events)


def test_repeated_direction_refreshes_both_sdk_writes(monkeypatch, capsys):
    relay, stream, events, _ = load_relay(monkeypatch, [packet(seq=1) + packet(seq=2), b''])
    relay.main(command_stream=stream)
    assert events.count(('speed', 1, 1000)) == 2 and events.count(('speed', 2, -1000)) == 2
    assert [item['seq'] for item in emitted(capsys) if item.get('direction') == 'forward'] == [1, 2]


@pytest.mark.parametrize('failed_id', [1, 2])
def test_partial_speed_failure_stops_both_and_never_reports_success(monkeypatch, capsys, failed_id):
    relay, stream, events, _ = load_relay(monkeypatch, [packet()], failure=lambda op, sid, value, _: op == 'speed' and sid == failed_id and value != 0)
    with pytest.raises(OSError, match='not acknowledged'):
        relay.main(command_stream=stream)
    assert not any(item[0] == 'enable' for item in events)
    assert_clean_stop(events)
    written = emitted(capsys)
    assert not any(item.get('direction') == 'forward' for item in written)
    assert written[-1]['event'] == 'fault'


def test_one_failed_stop_still_attempts_other_motor_and_clears_both_speeds(monkeypatch, capsys):
    relay, stream, events, _ = load_relay(monkeypatch, [], failure=lambda op, sid, *_: op == 'disable' and sid == 1)
    with pytest.raises(OSError, match='motor 1'):
        relay.main(command_stream=stream)
    assert ('disable', 2) in events and ('speed', 1, 0) in events and ('speed', 2, 0) in events
    assert events[-1] == ('close',)
    assert 'TBOT_SERVO_READY' not in capsys.readouterr().out


@pytest.mark.parametrize('failed_id', [1, 2])
def test_partial_torque_enable_failure_stops_both(monkeypatch, capsys, failed_id):
    relay, stream, events, _ = load_relay(monkeypatch, [packet()], failure=lambda op, sid, *_: op == 'enable' and sid == failed_id)
    with pytest.raises(OSError, match='not acknowledged'):
        relay.main(command_stream=stream)
    assert_clean_stop(events)
    assert not any(item.get('direction') == 'forward' for item in emitted(capsys))


def test_unreadable_mode_never_unlocks_or_writes_eeprom(monkeypatch, capsys):
    relay, stream, events, _ = load_relay(monkeypatch, [], failure=lambda op, *_: op == 'read_mode')
    with pytest.raises(OSError, match='Read operating mode'):
        relay.main(command_stream=stream)
    assert not any(item[0] in ('unlock', 'mode', 'enable') for item in events)
    assert 'TBOT_SERVO_READY' not in capsys.readouterr().out


def test_disconnect_on_unchanged_direction_is_detected(monkeypatch, capsys):
    def lost_bus(op, sid, value, events):
        return op == 'speed' and sid == 1 and value == 1000 and events.count(('speed', 1, 1000)) == 2
    relay, stream, events, _ = load_relay(monkeypatch, [packet() + packet(seq=2)], failure=lost_bus)
    with pytest.raises(OSError, match='not acknowledged'):
        relay.main(command_stream=stream)
    assert_clean_stop(events)
    assert [item['seq'] for item in emitted(capsys) if item.get('direction') == 'forward'] == [1]


@pytest.mark.parametrize('response', [None, {}, {'id': 2, 'error': 0, 'checksum_valid': True},
                                      {'id': 1, 'error': 8, 'checksum_valid': True},
                                      {'id': 1, 'error': 0, 'checksum_valid': False}])
def test_invalid_sdk_responses_are_not_motor_confirmation(monkeypatch, response):
    relay, _, _, _ = load_relay(monkeypatch, [])
    with pytest.raises(OSError, match='not acknowledged'):
        relay.checked(response, 1, 'test')


@pytest.mark.parametrize('linear,angular,expected', [
    (.06, 0, ('ffff0105032ee803dd', 'ffff0205032ee8835c')),
    (-.06, 0, ('ffff0105032ee8835d', 'ffff0205032ee803dc')),
    (0, .2, ('ffff0105032ee803dd', 'ffff0205032ee803dc')),
    (0, -.2, ('ffff0105032ee8835d', 'ffff0205032ee8835c')),
])
def test_actual_sdk_emits_golden_st3215_direction_bytes(monkeypatch, capsys, linear, angular, expected):
    # Actual installed SDK encoder/parser, injected serial object: no port open.
    from python_st3215 import ST3215 as RealSDK

    class FakeSerial:
        is_open = True
        timeout = .002
        def __init__(self): self.writes = []; self.latest = None
        def write(self, data):
            self.latest = bytes(data)
            self.writes.append(self.latest)
            return len(data)
        def flush(self): pass
        def read(self, _size):
            command = self.latest
            sid = command[2]
            parameters = [1] if command[4] == 0x02 else []
            body = [sid, 2 + len(parameters), 0, *parameters]
            return bytes([255, 255, *body, (~sum(body)) & 255])
        def close(self): self.is_open = False

    serial = FakeSerial()
    sdk = RealSDK(ser=serial)
    relay, stream, _, _ = load_relay(monkeypatch, [packet(linear, angular) + packet(0, 0, 2) + b'{"action":"quit"}\n'])
    monkeypatch.setattr(relay, 'ST3215', lambda port: sdk)
    relay.main(command_stream=stream)
    speed_writes = [data.hex() for data in serial.writes if data[4] == 3 and data[5] == 0x2e and (data[6] or data[7])]
    assert speed_writes == list(expected)
    assert serial.writes[0].hex() == 'ffff0104032800cf'
    assert serial.writes[1].hex() == 'ffff0204032800ce'
    assert not any(data[4] == 3 and data[5] == 0x21 for data in serial.writes), 'Already-correct EEPROM must not be rewritten'
    assert serial.writes[-4:].count(bytes.fromhex('ffff0104032800cf')) == 1
    assert serial.writes[-2:].count(bytes.fromhex('ffff0105032e0000c8')) == 1
    assert not serial.is_open and serial.write_timeout == .10
    assert any(item.get('seq') == 1 and item.get('direction') != 'stop' for item in emitted(capsys))
