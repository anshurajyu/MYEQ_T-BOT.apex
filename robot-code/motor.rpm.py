from python_st3215 import ST3215
import time

# =========================
# CONFIGURATION
# =========================

PORT = "/dev/ttyACM0"

RIGHT_ID = 1
LEFT_ID = 2

SPEED = 1000

# Wheel circumference
WHEEL_CIRCUMFERENCE_CM = 21.038

# Desired distance
DISTANCE_CM = 10.0

# =========================
# CALIBRATION
# =========================

# Previous test:
# Commanded = 10 cm
# Actual    = 13 cm
#
# Correction factor = 10 / 13
CALIBRATION_FACTOR = 10.0 / 13.0

# Waveshare approximation:
# 50 steps/s ≈ 0.732 RPM
RPM = (SPEED / 50.0) * 0.732
RPS = RPM / 60.0

REVOLUTIONS = DISTANCE_CM / WHEEL_CIRCUMFERENCE_CM

# Theoretical runtime
THEORETICAL_TIME = REVOLUTIONS / RPS

# Calibrated runtime
RUN_TIME = THEORETICAL_TIME * CALIBRATION_FACTOR


# =========================
# DISPLAY
# =========================

print("================================")
print("       MYEQUATION T-BOT")
print("       CALIBRATED MOVE")
print("================================")

print(f"Speed:               {SPEED} steps/s")
print(f"Approx RPM:          {RPM:.2f}")
print(f"Wheel circumference: {WHEEL_CIRCUMFERENCE_CM:.3f} cm")
print(f"Target distance:     {DISTANCE_CM:.2f} cm")
print(f"Required rotations:  {REVOLUTIONS:.4f}")
print(f"Theoretical time:    {THEORETICAL_TIME:.2f} s")
print(f"Calibration factor:  {CALIBRATION_FACTOR:.4f}")
print(f"Calibrated runtime:  {RUN_TIME:.2f} s")

print("================================")


# =========================
# CONNECT
# =========================

print("\nConnecting to servo bus...")

bus = ST3215(PORT)

right = bus.wrap_servo(RIGHT_ID)
left = bus.wrap_servo(LEFT_ID)

print("Connected.")
print(f"Right motor: ID {RIGHT_ID}")
print(f"Left motor:  ID {LEFT_ID}")


# =========================
# MOVE
# =========================

try:
    # Motor mode
    right.eeprom.write_operating_mode(1)
    left.eeprom.write_operating_mode(1)

    # Mirror-mounted wheels
    right.sram.write_running_speed(SPEED)
    left.sram.write_running_speed(-SPEED)

    print(f"\nFORWARD {DISTANCE_CM:.0f} CM")
    print(f"Running for {RUN_TIME:.2f} seconds...")

    time.sleep(1)

    # Enable torque
    right.sram.torque_enable()
    left.sram.torque_enable()

    # Run
    time.sleep(RUN_TIME)

finally:
    print("\nStopping motors...")

    # Set speed to zero first
    right.sram.write_running_speed(0)
    left.sram.write_running_speed(0)

    time.sleep(0.1)

    # Disable torque
    right.sram.torque_disable()
    left.sram.torque_disable()

    bus.close()

    print("Servo bus closed.")

print("\nSTOPPED")
print("Test complete.")