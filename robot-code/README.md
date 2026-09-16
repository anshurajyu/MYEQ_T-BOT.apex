# MYEQUATION T-BOT Gesture Control

This directory is a preserved copy of the Python robot programs from
`/home/apex/Desktop/MYEQ_T-BOT.apex`.

## Program Index

### RPM and 10-centimeter distance calibration

[`motor.rpm.py`](motor.rpm.py) calculates approximate motor RPM, wheel
revolutions, theoretical travel time, and a measured correction factor before
driving the two ST3215 wheel motors for a calibrated 10-centimeter movement.

### WASD keyboard controller

[`t-bot_wasd.py`](t-bot_wasd.py) controls both ST3215 wheel motors directly
from the terminal. It supports forward, backward, left, right, stop, and quit
commands without requiring Enter after each key.

### Camera gesture controller

[`t-bot.gusture.py`](t-bot.gusture.py) uses OpenCV and MediaPipe hand tracking
to control the robot. It filters movement gestures across several frames and
immediately stops for an open palm, an unknown gesture, or a missing hand.

### Basic two-servo checker

[`t-bot.py`](t-bot.py) connects to servo IDs 1 and 2, puts them in continuous
motor mode, drives the mirror-mounted wheels for two seconds, and safely
disables torque afterward.

### Phone-to-host connection test

[`server.py`](server.py) provides the FastAPI browser page and `/test` JSON
endpoint used to confirm that a phone can reach the robot computer over Wi-Fi.

### Raspberry Pi hardware template

[`robot.py`](robot.py) provides Arduino-style `setup()` and `loop()` functions
using `gpiozero`, with a GPIO 17 status LED as the first hardware exercise.

### Python dependencies

[`requirements.txt`](requirements.txt) contains the FastAPI, Uvicorn, GPIO Zero,
and `lgpio` dependencies used by the connection server and Raspberry Pi
hardware template. The ST3215 and computer-vision programs additionally need
their matching motor, OpenCV, and MediaPipe packages in the robot environment.

This project has two deliberately separate parts:

- `server.py`: the phone-to-Pi web connection test.
- `robot.py`: your Raspberry Pi hardware tab. It is the place to write LED,
  sensor, servo, and motor code.

`robot.py` uses the beginner-friendly `gpiozero` library, which is much closer
to Arduino code than low-level GPIO programming.

## First-time installation

Run these commands **on the Raspberry Pi** (not on the phone). This installs
the project libraries into the existing virtual environment:

```bash
/home/apex/tbot_servo/venv/bin/python -m pip install -r requirements.txt
```

If your Pi uses the standard Raspberry Pi OS Python packages instead of that
virtual environment, create one in this project first:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

## Your Arduino-style Raspberry Pi tab

Open `robot.py`. Its `setup()` function runs once and `loop()` is where you
put behaviour that repeats. Run it from a terminal on the Pi:

```bash
.venv/bin/python robot.py
```

The supplied first exercise blinks an LED on **GPIO 17** (physical header pin
11). Connect the other LED leg through a 220–330 ohm resistor to a GND pin.
Stop it with `Ctrl+C`.

Raspberry Pi pins are **3.3 V only**. Never put 5 V into a GPIO pin, and do
not power motors or servos from a GPIO pin. Motors/servos need their own
appropriate power supply and a shared ground with the Pi.

Useful `gpiozero` equivalents are:

```python
from gpiozero import LED, Button, Servo

led = LED(17)             # led.on(), led.off(), led.blink()
button = Button(27)       # button.is_pressed
servo = Servo(18)         # servo.min(), servo.mid(), servo.max()
```

## Run the connection test

Use the project virtual environment after installing the dependencies:

```bash
.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Find the host's Wi-Fi IPv4 address, then open `http://HOST_IP:8000` on the
phone while it is on the same Wi-Fi network. Confirm the JSON endpoint at
`http://HOST_IP:8000/test`.

Camera work deliberately begins only after this is verified. A phone browser
typically requires HTTPS (a secure context) before it will grant camera access
over a LAN IP address.
