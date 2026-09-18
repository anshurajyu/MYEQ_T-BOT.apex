"""Portable local USB transport; the child relay is the sole serial owner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import math

from .serial_owner import canonical_port


def _usb_id(name):
    value = os.environ.get(name, '').strip()
    if not value:
        return None
    try:
        result = int(value, 0) if value.lower().startswith('0x') else int(value, 16)
        if not 0 <= result <= 0xffff:
            raise ValueError()
        return result
    except ValueError as exc:
        raise ValueError(f'{name} must be a USB hexadecimal identifier such as 0x1a86') from exc


def serial_port_details():
    from serial.tools import list_ports
    vid, pid = _usb_id('TBOT_SERVO_VID'), _usb_id('TBOT_SERVO_PID')
    devices = {}
    for item in list_ports.comports():
        # Do not guess from tty numbers: LiDARs and other adapters also use USB.
        is_usb = item.vid is not None and item.pid is not None
        if not is_usb or vid is not None and item.vid != vid or pid is not None and item.pid != pid:
            continue
        path = canonical_port(item.device)
        devices[path] = {'device': path, 'description': item.description, 'vid': item.vid,
                         'pid': item.pid, 'serial_number': item.serial_number,
                         'manufacturer': item.manufacturer, 'location': item.location}
    return [devices[key] for key in sorted(devices)]


def select_servo_port(configured=None, *, required=True):
    explicit = configured or os.environ.get('TBOT_SERIAL_PORT', '').strip() or os.environ.get('TBOT_SERVO_PORT', '').strip()
    if explicit:
        return canonical_port(explicit)
    candidates = serial_port_details()
    if len(candidates) == 1 and os.environ.get('TBOT_SERVO_VID') and os.environ.get('TBOT_SERVO_PID'):
        return candidates[0]['device']
    if not required:
        return ''
    detail = ', '.join(item['device'] for item in candidates) or 'none detected'
    raise ValueError('Set TBOT_SERIAL_PORT (or TBOT_SERVO_PORT) to the verified servo adapter; automatic selection requires exactly one USB serial candidate matching explicitly configured VID and PID '
                     f'({detail}). TBOT_SERVO_VID and TBOT_SERVO_PID can narrow USB metadata; do not choose a sensor port.')


class DirectServoBridge:
    """Managed stdio on macOS/Linux; relay enforces a 300 ms command timeout.

    This software timeout cannot disable torque after the relay/OS loses
    execution or power. A hardware/servo watchdog is not asserted here.
    """
    def __init__(self):
        self.process = self.writer = None
        self.enabled = self.failed = False
        self.error = 'Direct servo control is off'
        self.port = self.output = self.python = ''
        self.sequence = 0
        self.pending_writes = {}
        self.last_motor_write = None
        self._write_lock = threading.RLock()

    def available_ports(self):
        return [item['device'] for item in serial_port_details()]

    def selected_port(self):
        explicit = os.environ.get('TBOT_SERIAL_PORT', '').strip() or os.environ.get('TBOT_SERVO_PORT', '').strip()
        if explicit:
            return canonical_port(explicit)
        return select_servo_port(required=False)

    def selected_python(self):
        return Path(os.environ.get('TBOT_SERVO_PYTHON', '').strip() or sys.executable).expanduser()

    def refresh(self):
        if self.enabled and (not self.process or self.process.poll() is not None):
            self.enabled = False
            self.failed = True
            self.error = 'Servo relay exited; reconnect explicitly. ' + self.output[-500:]
        if self.enabled and self.pending_writes and time.monotonic()-min(self.pending_writes.values()) > .45:
            self.enabled = False
            self.failed = True
            self.error = 'Motor write confirmation timed out; disconnect and reconnect explicitly.'
            # Closing stdin makes a live relay stop at EOF. Do not replay output.
            if self.writer:
                try:self.writer.close()
                except OSError:pass

    def status(self):
        self.refresh()
        try:
            details = serial_port_details()
            discovery_error = ''
        except (ImportError, OSError, ValueError) as exc:
            details, discovery_error = [], str(exc)
        return {'enabled': self.enabled, 'error': self.error, 'port': self.port or self.selected_port() if not discovery_error else self.port,
                'ports': [item['device'] for item in details], 'port_details': details,
                'discovery_error': discovery_error, 'python': self.python or str(self.selected_python()),
                'output': self.output[-2000:], 'transport': 'stdio', 'ready': self.enabled,
                'last_motor_write': self.last_motor_write, 'pending_writes':len(self.pending_writes),
                'right_id':1,'left_id':2,'raw_speed':1000,'watchdog_seconds':.30,
                'stop_behavior':'torque off + zero stored speed; wheel stopping distance requires hardware testing'}

    def read_output(self, process, ready, finished):
        try:
            for line in iter(process.stdout.readline, ''):
                if process is not self.process:
                    break
                self.output = (self.output + line)[-4000:]
                if line.strip() == 'TBOT_SERVO_READY':
                    ready.set()
                    finished.set()
                try: event=json.loads(line)
                except (ValueError,TypeError):continue
                if not isinstance(event,dict):continue
                if event.get('event')=='motor_write':
                    self.last_motor_write={**event,'received_at':time.time()}
                    self.pending_writes.pop(event.get('seq'),None)
                elif event.get('event')=='fault':
                    self.enabled=False;self.failed=True
                    self.error='Motor relay fault; reconnect explicitly: '+str(event.get('error','unknown fault'))
        finally:
            finished.set()
            process.stdout.close()

    def enable(self):
        self.refresh()
        if self.enabled:
            return
        # Dispose a prior exited/failed child's streams before reconnecting.
        if self.process is not None:
            self.disable()
        self.port = self.selected_port()
        python = self.selected_python()
        self.python = str(python)
        if not self.port:
            raise ValueError('Set TBOT_SERIAL_PORT or TBOT_SERVO_PORT to the verified adapter; automatic selection requires exactly one USB serial candidate matching TBOT_SERVO_VID and TBOT_SERVO_PID')
        if not Path(self.port).exists():
            raise ValueError(f'Waveshare servo adapter not found at {self.port}')
        if not python.is_file():
            raise ValueError(f'Servo interpreter missing at {python}; set TBOT_SERVO_PYTHON to the working interpreter')
        try:
            check = subprocess.run([str(python), '-c', 'import python_st3215'], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError('Cannot check servo Python environment: ' + str(exc)) from exc
        if check.returncode:
            raise ValueError(f'{python} cannot import python_st3215. Set TBOT_SERVO_PYTHON to your working interpreter. ' + check.stderr.strip()[-300:])
        script = Path(__file__).resolve().parents[1] / 'robot-code/dashboard_wasd_relay.py'
        self.output = ''
        self.failed = False
        self.pending_writes.clear();self.last_motor_write=None;self.sequence=0
        ready, finished = threading.Event(), threading.Event()
        try:
            self.process = subprocess.Popen([str(python), '-u', str(script), '--stdio', '--port', self.port],
                                            cwd=str(script.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, bufsize=1)
            self.writer = self.process.stdin
            threading.Thread(target=self.read_output, args=(self.process, ready, finished), daemon=True).start()
            finished.wait(timeout=5)
            if not ready.is_set() or self.process.poll() is not None:
                raise ValueError('Servo relay could not connect. ' + self.output.strip()[-700:])
        except (OSError, ValueError) as exc:
            detail = str(exc)
            self.disable()
            self.error = detail
            raise ValueError(detail) from exc
        self.enabled = True
        self.error = f'Servo connected on {self.port}'

    def send(self, linear=0., angular=0.):
        with self._write_lock:
            was_enabled = self.enabled
            self.refresh()
            if ((was_enabled and not self.enabled) or self.failed) and (linear or angular):
                raise ValueError(self.error)
            if not self.enabled:
                return
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (linear,angular)):
                raise ValueError('Motor velocities must be finite numbers')
            try:
                self.sequence+=1
                now=time.monotonic()
                self.pending_writes[self.sequence]=now
                self.writer.write(json.dumps({'linear': linear, 'angular': angular,'seq':self.sequence,'expires_at':now+.25},allow_nan=False) + '\n')
                self.writer.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self.enabled = False
                self.failed = True
                self.error = 'Servo bridge disconnected: ' + str(exc)
                raise ValueError(self.error) from exc

    def stop(self):
        self.send()

    def disable(self):
        with self._write_lock:
            try:
                self.stop()
            except ValueError:
                pass
            self.enabled = False
            self.failed = False
            process, writer = self.process, self.writer
            try:
                if process and process.poll() is None:
                    try:
                        if writer:
                            writer.write('{"action":"quit"}\n')
                            writer.flush()
                        process.wait(timeout=1)
                    except (OSError, ValueError, subprocess.TimeoutExpired):
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1)
            finally:
                if writer:
                    try:
                        writer.close()
                    except OSError:
                        pass
                self.writer = self.process = None
                self.error = 'Direct servo control is off'
