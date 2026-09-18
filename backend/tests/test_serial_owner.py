"""Cross-process ownership uses temporary names, never a serial device."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import uuid

import pytest

from backend.serial_owner import SerialOwner, OwnedST3215, canonical_port, lock_path, request_os_exclusive
from backend.usb_transport import serial_port_details, select_servo_port


def test_only_one_process_owns_canonical_port_and_exit_releases(tmp_path):
    device = str(tmp_path / f'servo-{uuid.uuid4()}')
    alias = tmp_path / 'usb-by-id'
    alias.symlink_to(device)
    root = str(Path(__file__).resolve().parents[2])
    script = "from backend.serial_owner import SerialOwner; import sys; lock=SerialOwner(sys.argv[1], 'test child').acquire(); print('OWNED', flush=True); sys.stdin.readline()"
    child = subprocess.Popen([sys.executable, '-c', script, device], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'OWNED'
        with pytest.raises(ValueError, match=f'test child.*PID {child.pid}'):
            SerialOwner(str(alias), 'competitor').acquire()
        child.stdin.write('\n')
        child.stdin.flush()
        child.wait(timeout=3)
        # File and metadata remain, but the kernel lock is released on exit.
        with SerialOwner(str(alias), 'next owner') as owner:
            assert owner.path == lock_path(device)
            assert json.loads(owner.path.read_text())['pid'] == os.getpid()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def test_sigkill_releases_lock_without_claiming_motor_stop(tmp_path):
    device = str(tmp_path / 'killed-servo')
    script = "from backend.serial_owner import SerialOwner; import sys; SerialOwner(sys.argv[1]).acquire(); print('OWNED',flush=True); sys.stdin.readline()"
    child = subprocess.Popen([sys.executable, '-c', script, device], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'OWNED'
        child.kill()
        child.wait(timeout=3)
        with SerialOwner(device):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()


def test_same_process_duplicate_bus_rejected_before_sdk_open(monkeypatch, tmp_path):
    opens = []
    class Bus:
        def __init__(self, port): opens.append(port)
        def close(self): pass
    monkeypatch.setitem(sys.modules, 'python_st3215', types.SimpleNamespace(ST3215=Bus))
    device = str(tmp_path / 'servo')
    first = OwnedST3215(device)
    try:
        with pytest.raises(ValueError, match='already owned'):
            OwnedST3215(device)
        assert opens == [device]
    finally:
        first.close()
    second = OwnedST3215(device)
    second.close()
    assert opens == [device, device]


@pytest.mark.parametrize('filename,args', [
    ('t-bot.py', []), ('t-bot_wasd.py', []), ('motor.rpm.py', []),
    ('dashboard_servo_bridge.py', []), ('dashboard_wasd_relay.py', ['--stdio']),
])
def test_standalone_script_cannot_bypass_running_motor_owner(tmp_path, filename, args):
    root = Path(__file__).resolve().parents[2]
    device = str(tmp_path / 'servo')
    # If an entry point opens the SDK before locking, this distinct error fails
    # the assertion. This also exercises the same process launch as Code Runner.
    (tmp_path / 'python_st3215.py').write_text("class ST3215:\n def __init__(self,*a,**k): raise AssertionError('SDK OPENED BEFORE OWNERSHIP CHECK')\n")
    environment = {**os.environ, 'TBOT_SERVO_PORT': device, 'PYTHONPATH': str(tmp_path)}
    with SerialOwner(device, 'active gateway relay'):
        result = subprocess.run([sys.executable, str(root / 'robot-code' / filename), *args],
                                cwd=str(tmp_path), env=environment, capture_output=True, text=True, timeout=3)
    assert result.returncode != 0
    assert 'already owned by active gateway relay' in result.stderr
    assert 'SDK OPENED BEFORE OWNERSHIP CHECK' not in result.stderr


def test_failed_sdk_open_and_failed_close_release_owner(monkeypatch, tmp_path):
    device = str(tmp_path / 'servo')
    class FailedOpen:
        def __init__(self, port): raise OSError('offline')
    monkeypatch.setitem(sys.modules, 'python_st3215', types.SimpleNamespace(ST3215=FailedOpen))
    with pytest.raises(OSError, match='offline'):
        OwnedST3215(device)
    with SerialOwner(device):
        pass
    class FailedClose:
        def __init__(self, port): pass
        def close(self): raise OSError('unplugged')
    monkeypatch.setitem(sys.modules, 'python_st3215', types.SimpleNamespace(ST3215=FailedClose))
    bus = OwnedST3215(device)
    with pytest.raises(OSError, match='unplugged'):
        bus.close()
    with SerialOwner(device):
        pass


def test_mac_tty_and_cu_aliases_share_lock():
    assert canonical_port('/dev/tty.usbserial-tbot-test') == canonical_port('/dev/cu.usbserial-tbot-test')
    assert lock_path('/dev/tty.usbserial-tbot-test') == lock_path('/dev/cu.usbserial-tbot-test')


def fake_port(device, vid=0x1a86, pid=0x7523, serial='servo'):
    return types.SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial,
                                 description='USB adapter', manufacturer='Test', location='1-2')


@pytest.fixture
def port_list(monkeypatch):
    from serial.tools import list_ports
    for name in ('TBOT_SERIAL_PORT', 'TBOT_SERVO_PORT', 'TBOT_SERVO_VID', 'TBOT_SERVO_PID'):
        monkeypatch.delenv(name, raising=False)
    values = []
    monkeypatch.setattr(list_ports, 'comports', lambda: values)
    return values


def test_usb_metadata_filters_and_never_guesses_between_adapters(port_list, monkeypatch):
    port_list.extend([fake_port('/dev/ttyUSB1'), fake_port('/dev/ttyACM0', vid=0x1234), fake_port('/dev/ttyS0', vid=None, pid=None)])
    with pytest.raises(ValueError, match='exactly one'):
        select_servo_port()
    monkeypatch.setenv('TBOT_SERVO_VID', '0x1a86')
    monkeypatch.setenv('TBOT_SERVO_PID', '7523')
    assert select_servo_port() == '/dev/ttyUSB1'
    assert serial_port_details()[0]['serial_number'] == 'servo'
    monkeypatch.setenv('TBOT_SERVO_PORT', '/dev/serial/by-id/verified-controller')
    assert select_servo_port() == '/dev/serial/by-id/verified-controller'


def test_no_usb_adapter_and_invalid_filters_fail_clearly(port_list, monkeypatch):
    with pytest.raises(ValueError, match='none detected'):
        select_servo_port()
    assert select_servo_port(required=False) == ''
    monkeypatch.setenv('TBOT_SERVO_PID', 'not-a-product-id')
    with pytest.raises(ValueError, match='hexadecimal'):
        serial_port_details()


def test_mac_port_aliases_count_as_one_candidate(port_list,monkeypatch):
    monkeypatch.setenv("TBOT_SERVO_VID","1a86")
    monkeypatch.setenv("TBOT_SERVO_PID","7523")
    port_list.extend([fake_port('/dev/tty.usbserial-1'), fake_port('/dev/cu.usbserial-1')])
    assert select_servo_port() == '/dev/cu.usbserial-1'


def test_exclusive_descriptor_request_uses_sdk_serial_without_opening(monkeypatch):
    import backend.serial_owner as module
    calls = []
    monkeypatch.setattr(module.os, 'isatty', lambda fd: fd == 42)
    monkeypatch.setattr(module.fcntl, 'ioctl', lambda fd, operation: calls.append((fd, operation)))
    bus = types.SimpleNamespace(port=types.SimpleNamespace(ser=types.SimpleNamespace(fileno=lambda: 42)))
    assert request_os_exclusive(bus)
    assert calls == [(42, module.termios.TIOCEXCL)]
    assert not request_os_exclusive(types.SimpleNamespace())


def test_actual_sdk_exposes_ser_descriptor_for_exclusive_request(monkeypatch, tmp_path):
    import backend.serial_owner as module
    calls = []
    class FakeSerial:
        is_open = True
        timeout = .002
        def fileno(self): return 42
        def close(self): self.is_open = False
    serial = FakeSerial()
    monkeypatch.setattr(module.os, 'isatty', lambda fd: fd == 42)
    monkeypatch.setattr(module.fcntl, 'ioctl', lambda fd, op: calls.append((fd, op)))
    bus = OwnedST3215(str(tmp_path / 'fake-sdk-bus'), ser=serial)
    assert bus.ser is serial and bus.os_exclusive
    assert calls == [(42, module.termios.TIOCEXCL)]
    bus.close()
    assert not serial.is_open


def test_every_repository_motor_entrypoint_uses_owner_wrapper():
    root = Path(__file__).resolve().parents[2]
    for path in (root / 'robot-code').glob('*.py'):
        tree = ast.parse(path.read_text())
        constructors = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'ST3215']
        if not constructors:
            continue
        imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == 'backend.serial_owner']
        assert any(alias.name == 'OwnedST3215' and alias.asname == 'ST3215' for node in imports for alias in node.names), path


def test_one_unidentified_usb_candidate_is_not_silently_opened(port_list):
    port_list.append(fake_port('/dev/cu.usbserial-could-be-lidar'))
    with pytest.raises(ValueError,match='explicitly configured VID and PID'):
        select_servo_port()
    assert select_servo_port(required=False)==''
