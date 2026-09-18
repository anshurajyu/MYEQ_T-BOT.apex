"""Line-delimited dashboard bridge for the USB ST3215 bus.

It is deliberately small: only direction vectors enter over stdin, it uses a
300 ms command watchdog, and orderly exits stop then disable torque. A killed
process or a failed OS still requires an independent hardware/servo watchdog.
"""
from __future__ import annotations

import json
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.serial_owner import OwnedST3215 as ST3215
from backend.usb_transport import select_servo_port

PORT = "/dev/ttyACM0"
RIGHT_ID, LEFT_ID = 1, 2
MAX_SPEED = 1200  # conservative for the first PC-connected test
WATCHDOG_SECONDS = 0.30


def wheel_speeds(linear: float, angular: float) -> tuple[int, int]:
    # Your mirror-mounted wheel signs: right +, left -. Positive linear is
    # physical forward; positive angular turns left.
    right = max(-1.0, min(1.0, linear + angular * 0.55))
    left = max(-1.0, min(1.0, linear - angular * 0.55))
    return int(right * MAX_SPEED), int(-left * MAX_SPEED)


def main() -> None:
    port = select_servo_port()
    bus = ST3215(port)
    right, left = bus.wrap_servo(RIGHT_ID), bus.wrap_servo(LEFT_ID)

    def apply(linear: float = 0.0, angular: float = 0.0) -> None:
        r, l = wheel_speeds(linear, angular)
        right.sram.write_running_speed(r)
        left.sram.write_running_speed(l)
        right.sram.torque_enable()
        left.sram.torque_enable()

    def shutdown() -> None:
        try:
            apply()
            time.sleep(0.05)
            right.sram.torque_disable(); left.sram.torque_disable()
        finally:
            bus.close()

    try:
        right.eeprom.write_operating_mode(1); left.eeprom.write_operating_mode(1)
        apply(); print(json.dumps({"ready": True, "port": port}), flush=True)
        last = time.monotonic()
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if ready:
                line = sys.stdin.readline()
                if not line: break
                command = json.loads(line)
                if command.get("action") == "quit": break
                apply(float(command.get("linear", 0)), float(command.get("angular", 0)))
                last = time.monotonic()
            if time.monotonic() - last > WATCHDOG_SECONDS:
                apply(); last = time.monotonic()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
