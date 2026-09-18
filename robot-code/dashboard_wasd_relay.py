"""Managed, sequenced motor relay; orderly faults stop and latch until restart.

The watchdog needs a running process and working serial link. SIGKILL, host
failure and physical coasting require independent hardware protection/testing.
"""
from __future__ import annotations

import json
import math
import os
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.serial_owner import OwnedST3215 as ST3215
from backend.usb_transport import select_servo_port

RIGHT_ID, LEFT_ID = 1, 2
SPEED = 1000
WATCHDOG_SECONDS = 0.30


def event(kind: str, **fields) -> None:
    print(json.dumps({'event': kind, **fields}), flush=True)


def checked(response, servo_id: int, operation: str):
    # python-st3215 returns None on silence and may return a nonzero servo error
    # without raising. Neither is a successful motor write.
    if not isinstance(response, dict) or response.get('id') != servo_id or response.get('error') != 0 or response.get('checksum_valid') is not True:
        raise OSError(f'{operation} was not acknowledged by motor {servo_id}')
    return response


def validate_command(command, now: float, last_seq: int):
    if not isinstance(command, dict):
        raise ValueError('Motor command must be an object')
    if command == {'action': 'quit'}:
        return None
    if set(command) != {'linear', 'angular', 'seq', 'expires_at'}:
        raise ValueError('Motor command requires only linear, angular, seq and expires_at')
    linear, angular, seq, expires = (command[key] for key in ('linear', 'angular', 'seq', 'expires_at'))
    if type(seq) is not int or seq < 1:
        raise ValueError('Motor command sequence must be a positive integer')
    for name, value in (('linear', linear), ('angular', angular), ('expires_at', expires)):
        if type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError(f'{name} must be a finite number')
    if abs(linear) > .15 or abs(angular) > .8:
        raise ValueError('Motor direction exceeds gateway velocity bounds')
    # An explicit zero always stops, even if delayed or repeated. It cannot
    # lower the last accepted sequence or make later stale motion acceptable.
    if linear or angular:
        if seq <= last_seq:
            raise ValueError('Stale motor command sequence; reconnect explicitly')
        if expires <= now or expires > now + WATCHDOG_SECONDS:
            raise ValueError('Expired or invalid motor deadline; reconnect explicitly')
    direction = ('forward' if linear > .01 else 'backward' if linear < -.01 else
                 'left' if angular > .01 else 'right' if angular < -.01 else 'stop')
    return seq, expires, direction


def main(fifo_path: str | None = None, port: str | None = None, command_stream=None) -> None:
    bus = right = left = stream = None
    last_seq = 0
    failure = None

    def stop() -> None:
        errors = []
        # Preserve the existing torque-off Stop. Attempt both motors before
        # clearing stored speeds, even if one motor/operation has failed.
        for operation in ('torque_disable', 'write_running_speed'):
            for servo, servo_id in ((right, RIGHT_ID), (left, LEFT_ID)):
                if servo is not None:
                    try:
                        method = getattr(servo.sram, operation)
                        result = method(0) if operation == 'write_running_speed' else method()
                        checked(result, servo_id, operation)
                    except Exception as exc:
                        errors.append(str(exc))
        if errors:
            raise OSError('; '.join(errors))

    def wheel_mode(servo, servo_id):
        def read_mode():
            # The SDK convenience read drops servo error status; retain it here.
            result = checked(servo.send(0x02, [0x21, 1]), servo_id, 'Read operating mode')
            params = result.get('parameters')
            if not isinstance(params, (bytes, bytearray)) or len(params) != 1 or params[0] not in (0, 1, 2, 3):
                raise OSError(f'Invalid operating-mode read from motor {servo_id}')
            return params[0]
        if read_mode() != 1:
            checked(servo.sram.unlock(), servo_id, 'Unlock EEPROM')
            try:
                checked(servo.eeprom.write_operating_mode(1), servo_id, 'Write wheel mode')
            finally:
                checked(servo.sram.lock(), servo_id, 'Lock EEPROM')
            if read_mode() != 1:
                raise OSError(f'Wheel-mode readback failed for motor {servo_id}')

    def apply(direction, seq, expires):
        speeds = {'forward': (SPEED, -SPEED), 'backward': (-SPEED, SPEED),
                  'left': (SPEED, SPEED), 'right': (-SPEED, -SPEED), 'stop': (0, 0)}[direction]
        if direction == 'stop':
            stop()
        else:
            checked(right.sram.write_running_speed(speeds[0]), RIGHT_ID, 'Write speed')
            checked(left.sram.write_running_speed(speeds[1]), LEFT_ID, 'Write speed')
            for servo, servo_id in ((right, RIGHT_ID), (left, LEFT_ID)):
                if time.monotonic() >= expires:
                    raise ValueError('Motor deadline expired during delivery; reconnect explicitly')
                checked(servo.sram.torque_enable(), servo_id, 'Enable torque')
            if time.monotonic() >= expires:
                raise ValueError('Motor deadline expired during delivery; reconnect explicitly')
        event('motor_write', seq=seq, direction=direction, right_speed=speeds[0], left_speed=speeds[1])

    try:
        port = select_servo_port(port)
        bus = ST3215(port)
        # Bound pyserial writes where the installed SDK exposes its descriptor.
        if hasattr(bus, 'ser'):
            bus.ser.write_timeout = .10
        # Stop both IDs before EEPROM changes. The acknowledged stop verifies
        # reachability without a failed second ping bypassing the first stop.
        right = bus.wrap_servo(RIGHT_ID, verify=False)
        left = bus.wrap_servo(LEFT_ID, verify=False)
        stop()
        wheel_mode(right, RIGHT_ID)
        wheel_mode(left, LEFT_ID)
        stop()
        if command_stream is not None:
            stream = command_stream
        elif fifo_path:
            fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
            stream = os.fdopen(fd, 'rb', buffering=0)
        else:
            raise ValueError('Choose a command stream or FIFO path')
        event('motor_write', seq=0, direction='stop', right_speed=0, left_speed=0)
        print('TBOT_SERVO_READY', flush=True)
        last_command = time.monotonic()
        last_direction = 'stop'
        received_command = command_stream is not None
        pending = b''
        while True:
            ready, _, _ = select.select([stream], [], [], .05)
            if last_direction != 'stop' and time.monotonic() - last_command >= WATCHDOG_SECONDS:
                raise TimeoutError('Motor command watchdog expired; reconnect explicitly')
            if not ready:
                continue
            chunk = os.read(stream.fileno(), 4096)
            if not chunk:
                if received_command:
                    break
                time.sleep(.02)
                continue
            pending += chunk
            if len(pending) > 8192:
                raise ValueError('Servo command exceeded pipe buffer limit')
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                parsed = validate_command(json.loads(line), time.monotonic(), last_seq)
                received_command = True
                if parsed is None:
                    return
                seq, expires, direction = parsed
                apply(direction, seq, expires)
                last_seq = max(last_seq, seq)
                last_direction = direction
                last_command = time.monotonic()
    except Exception as exc:
        failure = exc
        raise
    finally:
        try:
            stop()
            if right is not None or left is not None:
                event('motor_write', seq=last_seq, direction='stop', right_speed=0, left_speed=0)
        except Exception as exc:
            failure = failure or exc
        finally:
            for resource in (stream, bus):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        failure = failure or exc
            if failure is not None:
                event('fault', error=str(failure))
                raise failure


if __name__ == '__main__':
    import argparse
    import signal
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fifo', nargs='?')
    parser.add_argument('--stdio', action='store_true')
    parser.add_argument('--port', help='Verified device; otherwise TBOT_SERIAL_PORT/TBOT_SERVO_PORT or one configured VID/PID match')
    options = parser.parse_args()
    if bool(options.fifo) == options.stdio:
        parser.error('Choose either FIFO_PATH or --stdio')
    def terminate(_signal, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    main(options.fifo, port=options.port, command_stream=sys.stdin if options.stdio else None)
