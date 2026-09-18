from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.serial_owner import OwnedST3215 as ST3215
from backend.usb_transport import select_servo_port
import cv2
import mediapipe as mp
import time
# ==========================================
# T-BOT HARDWARE CONFIGURATION
# ==========================================
PORT = select_servo_port()
RIGHT_ID = 1
LEFT_ID = 2
# Motor mounting direction correction
RIGHT_SIGN = +1
LEFT_SIGN = -1
# Start LOW
SPEED = 1200
TURN_SPEED = 1000
# Robot stops if hand disappears
NO_HAND_TIMEOUT = 0.30
# Gesture must remain stable for
# several camera frames before movement
STABLE_FRAMES = 4
# ==========================================
# ST3215 SETUP
# ==========================================
bus = ST3215(PORT)
right = bus.wrap_servo(RIGHT_ID)
left = bus.wrap_servo(LEFT_ID)
def set_wheels(right_physical, left_physical):
    """
    Positive value = physical forward
    Negative value = physical backward
    Motor mounting correction is applied here.
    """
    right_raw = int(
        RIGHT_SIGN * right_physical
    )
    left_raw = int(
        LEFT_SIGN * left_physical
    )
    right.sram.write_running_speed(
        right_raw
    )
    left.sram.write_running_speed(
        left_raw
    )
    right.sram.torque_enable()
    left.sram.torque_enable()
def active_stop():
    """
    Normal STOP.
    Commands zero speed while
    motor control remains enabled.
    """
    set_wheels(0, 0)
def shutdown_motors():
    """
    Used when exiting program.
    """
    try:
        active_stop()
        time.sleep(0.1)
        right.sram.torque_disable()
        left.sram.torque_disable()
    except Exception:
        pass
# ==========================================
# ROBOT MOTION COMMANDS
# ==========================================
def execute_command(command):
    if command == "FORWARD":
        set_wheels(
            SPEED,
            SPEED
        )
    elif command == "BACKWARD":
        set_wheels(
            -SPEED,
            -SPEED
        )
    elif command == "LEFT":
        # Right wheel forward
        # Left wheel backward
        set_wheels(
            TURN_SPEED,
            -TURN_SPEED
        )
    elif command == "RIGHT":
        # Right wheel backward
        # Left wheel forward
        set_wheels(
            -TURN_SPEED,
            TURN_SPEED
        )
    else:
        active_stop()
# ==========================================
# MEDIAPIPE HAND DETECTION
# ==========================================
mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils
def finger_states(hand):
    """
    Returns states of:
    index
    middle
    ring
    pinky
    True  = finger extended
    False = finger folded
    """
    lm = hand.landmark
    index = lm[8].y < lm[6].y
    middle = lm[12].y < lm[10].y
    ring = lm[16].y < lm[14].y
    pinky = lm[20].y < lm[18].y
    return (
        index,
        middle,
        ring,
        pinky
    )
def classify_gesture(hand):
    index, middle, ring, pinky = (
        finger_states(hand)
    )
    # Horizontal position of hand
    # Landmark 9 = middle finger base
    hand_x = hand.landmark[9].x
    # ==================================
    # OPEN PALM = STOP
    # ==================================
    if (
        index
        and middle
        and ring
        and pinky
    ):
        return "STOP"
    # ==================================
    # INDEX FINGER ONLY
    # ==================================
    if (
        index
        and not middle
        and not ring
        and not pinky
    ):
        # Hand on left side
        if hand_x < 0.38:
            return "LEFT"
        # Hand on right side
        elif hand_x > 0.62:
            return "RIGHT"
        # Hand in centre
        else:
            return "FORWARD"
    # ==================================
    # TWO FINGERS = BACKWARD
    # ==================================
    if (
        index
        and middle
        and not ring
        and not pinky
    ):
        return "BACKWARD"
    # Anything unknown = STOP
    return "STOP"
# ==========================================
# MAIN PROGRAM
# ==========================================
try:
    # Set both servos into
    # continuous/wheel mode
    right.eeprom.write_operating_mode(1)
    left.eeprom.write_operating_mode(1)
    active_stop()
    print("""
========================================
        MYEQUATION T-BOT
        GESTURE CONTROL
========================================
☝️ Index centre  = FORWARD
☝️ Index left    = TURN LEFT
☝️ Index right   = TURN RIGHT
✌️ Two fingers   = BACKWARD
✋ Open palm      = STOP
Unknown gesture  = STOP
No hand > 0.30s  = STOP
Q / ESC           = QUIT
========================================
""")
    # ======================================
    # OPEN CAMERA
    # ======================================
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise RuntimeError(
            "Could not open camera"
        )
    last_hand_time = time.monotonic()
    current_command = "STOP"
    candidate_command = "STOP"
    candidate_count = 0
    # ======================================
    # HAND TRACKER
    # ======================================
    with mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.70,
        min_tracking_confidence=0.70
    ) as hands:
        while True:
            success, frame = camera.read()
            if not success:
                active_stop()
                continue
            # Mirror camera image
            frame = cv2.flip(
                frame,
                1
            )
            rgb = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB
            )
            results = hands.process(rgb)
            raw_command = None
            # ==================================
            # HAND DETECTED
            # ==================================
            if results.multi_hand_landmarks:
                hand = (
                    results
                    .multi_hand_landmarks[0]
                )
                last_hand_time = (
                    time.monotonic()
                )
                raw_command = (
                    classify_gesture(hand)
                )
                mp_draw.draw_landmarks(
                    frame,
                    hand,
                    mp_hands.HAND_CONNECTIONS
                )
            # ==================================
            # NO HAND DETECTED
            # ==================================
            else:
                elapsed = (
                    time.monotonic()
                    -
                    last_hand_time
                )
                if elapsed > NO_HAND_TIMEOUT:
                    raw_command = "STOP"
            # ==================================
            # GESTURE FILTER
            # ==================================
            if raw_command is not None:
                # STOP always happens immediately
                if raw_command == "STOP":
                    candidate_command = "STOP"
                    candidate_count = 0
                    if current_command != "STOP":
                        current_command = "STOP"
                        execute_command(
                            current_command
                        )
                        print(
                            "COMMAND: STOP"
                        )
                # Movement gesture must remain
                # stable for several frames
                else:
                    if (
                        raw_command
                        ==
                        candidate_command
                    ):
                        candidate_count += 1
                    else:
                        candidate_command = (
                            raw_command
                        )
                        candidate_count = 1
                    if (
                        candidate_count
                        >=
                        STABLE_FRAMES
                        and
                        candidate_command
                        !=
                        current_command
                    ):
                        current_command = (
                            candidate_command
                        )
                        execute_command(
                            current_command
                        )
                        print(
                            "COMMAND:",
                            current_command
                        )
            # ==================================
            # SHOW CAMERA + COMMAND
            # ==================================
            cv2.putText(
                frame,
                f"COMMAND: {current_command}",
                (30, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.1,
                (0, 255, 0),
                3
            )
            cv2.putText(
                frame,
                f"SPEED: {SPEED}",
                (30, 90),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2
            )
            cv2.imshow(
                "MYEQUATION T-BOT Gesture Control",
                frame
            )
            key = (
                cv2.waitKey(1)
                &
                0xFF
            )
            # Q or ESC exits
            if (
                key == ord("q")
                or
                key == 27
            ):
                break
except KeyboardInterrupt:
    print(
        "\nStopped using Ctrl+C"
    )
except Exception as e:
    print(
        f"\nERROR: {e}"
    )
finally:
    print(
        "\nStopping robot..."
    )
    shutdown_motors()
    try:
        camera.release()
    except:
        pass
    cv2.destroyAllWindows()
    bus.close()
    print(
        "Robot controller closed."
    )
