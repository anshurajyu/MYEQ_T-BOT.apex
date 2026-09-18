from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.serial_owner import OwnedST3215 as ST3215
from backend.usb_transport import select_servo_port
import time

PORT = select_servo_port()

RIGHT_ID = 1
LEFT_ID = 2

SPEED = 10000

bus = ST3215(PORT)

right = bus.wrap_servo(RIGHT_ID)
left = bus.wrap_servo(LEFT_ID)

try:
    right.eeprom.write_operating_mode(1)
    left.eeprom.write_operating_mode(1)

    # Mirror-mounted motors
    right.sram.write_running_speed(SPEED)
    left.sram.write_running_speed(-SPEED)

    print("FORWARD - 2 SECONDS")

    right.sram.torque_enable()
    left.sram.torque_enable()

    time.sleep(2)

finally:
    right.sram.torque_disable()
    left.sram.torque_disable()
    bus.close()

print("STOPPED")
