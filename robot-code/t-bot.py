from python_st3215 import ST3215
import time

PORT = "/dev/ttyACM0"

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