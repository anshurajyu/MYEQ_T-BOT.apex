from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.serial_owner import OwnedST3215 as ST3215
from backend.usb_transport import select_servo_port
import time
import termios
import tty
import select

PORT = select_servo_port()

RIGHT_ID = 1
LEFT_ID = 2

SPEED = 10000

bus = ST3215(PORT)

right = bus.wrap_servo(RIGHT_ID)
left = bus.wrap_servo(LEFT_ID)


def stop():
    right.sram.torque_disable()
    left.sram.torque_disable()


def set_speed(right_speed, left_speed):
    right.sram.write_running_speed(right_speed)
    left.sram.write_running_speed(left_speed)

    right.sram.torque_enable()
    left.sram.torque_enable()


def get_key():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)

        if select.select([sys.stdin], [], [], 0.1)[0]:
            return sys.stdin.read(1).lower()

        return None

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


try:
    # Motor mode
    right.eeprom.write_operating_mode(1)
    left.eeprom.write_operating_mode(1)

    stop()

    print("""
================================
       MYEQUATION T-BOT
        WASD CONTROL
================================

W = Forward
S = Backward
A = Left
D = Right
X = Stop
Q = Quit

Press keys directly.
No Enter required.
================================
""")

    while True:

        key = get_key()

        if key == "s":
            print("FORWARD")
            set_speed(SPEED, -SPEED)

        elif key == "w":
            print("BACKWARD")
            set_speed(-SPEED, SPEED)

        elif key == "a":
            print("TURN LEFT")
            set_speed(SPEED, SPEED)

        elif key == "d":
            print("TURN RIGHT")
            set_speed(-SPEED, -SPEED)

        elif key == "x":
            print("STOP")
            stop()

        elif key == "q":
            print("QUIT")
            break

except KeyboardInterrupt:
    print("\nStopped by Ctrl+C")

finally:
    stop()
    bus.close()

print("Robot controller closed.")
