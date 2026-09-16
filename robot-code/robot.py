"""Raspberry Pi hardware tab for MYEQUATION T-BOT.

Put your motor, LED, sensor, and servo code here.  Pin numbers below use the
Raspberry Pi's BCM GPIO numbering, not the physical pin numbers printed on
the board header.
"""

from gpiozero import LED

# This is your first Arduino-style component declaration.
# GPIO 17 is physical header pin 11.  Connect an LED plus resistor to test it.
status_led = LED(17)


def setup() -> None:
    """Runs once when the robot starts (similar to Arduino setup())."""
    status_led.off()


def loop() -> None:
    """Runs repeatedly (similar to Arduino loop())."""
    status_led.blink(on_time=0.5, off_time=0.5, background=False)


if __name__ == "__main__":
    setup()
    try:
        loop()
    except KeyboardInterrupt:
        status_led.off()
