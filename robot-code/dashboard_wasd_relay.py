"""Visible terminal relay for tablet/dashboard directions and ST3215 motors."""
from __future__ import annotations

import json
import os
import select
import sys
import time

from python_st3215 import ST3215

PORT = "/dev/ttyACM0"
RIGHT_ID, LEFT_ID = 1, 2
SPEED = 1000
WATCHDOG_SECONDS = 0.30


def main(fifo_path: str) -> None:
    bus = ST3215(PORT)
    right, left = bus.wrap_servo(RIGHT_ID), bus.wrap_servo(LEFT_ID)

    def stop() -> None:
        right.sram.torque_disable()
        left.sram.torque_disable()

    def set_speed(right_speed: int, left_speed: int) -> None:
        right.sram.write_running_speed(right_speed)
        left.sram.write_running_speed(left_speed)
        right.sram.torque_enable()
        left.sram.torque_enable()

    def apply(direction: str) -> None:
        # These signs are copied from the user's physically verified WASD code.
        if direction == "forward":
            set_speed(SPEED, -SPEED)
        elif direction == "backward":
            set_speed(-SPEED, SPEED)
        elif direction == "left":
            set_speed(SPEED, SPEED)
        elif direction == "right":
            set_speed(-SPEED, -SPEED)
        else:
            stop()
        print(f"RECEIVED: {direction.upper()}", flush=True)

    right.eeprom.write_operating_mode(1)
    left.eeprom.write_operating_mode(1)
    stop()
    print("=" * 44)
    print(" T-BOT DASHBOARD → DIRECT SERVO TERMINAL")
    print(" Waiting for FORWARD / BACKWARD / LEFT / RIGHT")
    print("=" * 44, flush=True)
    fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
    stream = os.fdopen(fd, "r", buffering=1)
    last_command = time.monotonic()
    last_direction = "stop"
    try:
        while True:
            ready, _, _ = select.select([stream], [], [], 0.05)
            if ready:
                line = stream.readline()
                if not line:
                    time.sleep(0.02)
                    continue
                command = json.loads(line)
                if command.get("action") == "quit":
                    break
                linear = float(command.get("linear", 0))
                angular = float(command.get("angular", 0))
                direction = (
                    "forward" if linear > 0.01 else
                    "backward" if linear < -0.01 else
                    "left" if angular > 0.01 else
                    "right" if angular < -0.01 else "stop"
                )
                if direction != last_direction:
                    apply(direction)
                    last_direction = direction
                last_command = time.monotonic()
            if last_direction != "stop" and time.monotonic() - last_command > WATCHDOG_SECONDS:
                apply("stop")
                last_direction = "stop"
    finally:
        stop()
        stream.close()
        bus.close()
        print("Robot controller closed.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: dashboard_wasd_relay.py FIFO_PATH")
    main(sys.argv[1])
