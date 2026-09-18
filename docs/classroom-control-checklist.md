# T-BOT classroom startup and acceptance

The confirmed hardware arrangement is **servo USB adapter plugged into the Mac; the motor Python program runs on that Mac**. Keyboard and joystick have already moved the robot. SSH accesses the Pi separately; SSH is not the motor route for this setup. The team has also reported a working iPhone control chain. That does not establish the Android gesture-to-wheel test or voice-to-wheel acceptance.

The current route is **phone camera / Mac keyboard, gamepad or microphone → dashboard → Mac gateway → managed USB relay → two servos**. Keep the motor IDs, signs and controller mapping that worked. Software tests and SDK acknowledgements cannot confirm wheel motion, direction or stopping distance.

## One-command startup

From the repository root, after the dependencies and offline assets have been prepared:

```bash
./scripts/tbot-classroom.sh
```

The launcher discovers this checkout and the installed Python/Node runtimes, checks packages/models, starts the gateway on `127.0.0.1:8001` and dashboard on `127.0.0.1:5173`, verifies their identities and `/api` connection, and opens the dashboard. New processes start in **SIMULATION** with ROS disabled; starting the app does not open the USB adapter. It does not install packages, change motor settings, or guess another computer's hostname.

| Command | Purpose |
| --- | --- |
| `./scripts/tbot-classroom.sh --no-open` | Start without opening a browser. |
| `./scripts/tbot-classroom.sh preflight` | Check installed runtimes, packages and offline assets; report secure-phone availability. |
| `./scripts/tbot-classroom.sh status` | Identify compatible running servers and whether this launcher owns them. |
| `./scripts/tbot-classroom.sh stop` | Stop only processes this launcher recorded and still verifies. |

Compatible existing servers can be reused. An unrelated server, another checkout, old protocol or mismatched public URL causes a conflict message instead of killing it. Stop that server in its original terminal, then retry. Launcher logs are `.tbot-data/runtime/backend.log` and `.tbot-data/runtime/frontend.log`. `stop` does not remove models, project files, data or Tailscale routes.

- Local dashboard: `http://127.0.0.1:5173/mission-control`; `/` redirects here.
- Gateway health: `http://127.0.0.1:8001/health`; proxied health: `http://127.0.0.1:5173/api/health`.
- API inspector: `http://127.0.0.1:8001/docs`.
- `/demo` contains the legacy page; `/prototype` is a visual prototype. Neither is the classroom control entry point.

Use an installed Node version at least **22.13**; **24.19.0** is the tested pin in `.node-version`. The launcher selects `node` from PATH and `.venv-tbot/bin/python` when present. `TBOT_NODE` and `TBOT_PYTHON` can select other installed executables. No personal Desktop path or Codex cache path is required. The classroom launcher targets macOS/Linux and uses Bash and Python.

### Prepare a fresh checkout before class

Skip installations when preflight already passes. A new computer needs its own Python environment; copying a different computer's virtual environment is not a portable installation.

```bash
npm ci
python3 -m venv .venv-tbot
.venv-tbot/bin/python -m pip install -r backend/requirements.txt
.venv-tbot/bin/python scripts/tbot-assets.py
```

An installed pnpm CLI with `pnpm install --frozen-lockfile` is the web-install alternative. Use one package manager and its lockfile. The model-download step requires internet once; the hand model/WASM files and Vosk model are then local. Preflight reports missing files without downloading them.

For **DIRECT_USB**, install the optional SDK into the interpreter that will run the relay, or keep the interpreter from the successful motor test:

```bash
.venv-tbot/bin/python -m pip install -r backend/requirements-direct-usb.txt
```

That optional file pins `python-st3215==1.2.1`. This checkout's `.venv-tbot` has been prepared with it. An alternative working servo interpreter can be selected with `TBOT_SERVO_PYTHON`; the gateway need not replace that environment. Import checks do not open the serial port.

## Select the real USB adapter

Stop any separate motor program first. Inspect the devices on the computer where the USB adapter is actually plugged in:

```bash
.venv-tbot/bin/python -m serial.tools.list_ports -v
```

Identify the **servo adapter**, not a LiDAR or another serial peripheral. Set the verified path before starting the launcher:

```bash
export TBOT_SERIAL_PORT='REPLACE_WITH_VERIFIED_DEVICE_PATH'
./scripts/tbot-classroom.sh
```

`TBOT_SERVO_PORT` remains a supported legacy alias. If both are set, `TBOT_SERIAL_PORT` takes precedence. Alternatively, set **both** `TBOT_SERVO_VID` and `TBOT_SERVO_PID` to the adapter's verified hexadecimal USB IDs; automatic selection then requires exactly one matching candidate. A lone unfiltered USB device is not trusted automatically. Mac device names and Linux device names differ; no fixed `/dev/ttyACM0` or `/dev/cu...` name is assumed.

If needed, set `TBOT_SERVO_PYTHON` to the absolute path of the successful motor interpreter before launch. After changing device/interpreter variables, stop and restart the gateway; reusing an existing process does not change its environment.

Both macOS and Linux use a managed relay over standard input. GNOME Terminal and a graphical Pi session are unnecessary. The relay owns the serial device exclusively; stop the original WASD/gesture script before selecting USB output. Do not launch a second motor script from Code Runner against the same device.

## Choose output, then choose an input

The **Output** selector has three distinct modes:

| Output | What it controls |
| --- | --- |
| **SIMULATION** | Virtual robot, map, LiDAR and odometry. No USB motor output. Use this for rehearsal. |
| **DIRECT_USB** | The verified adapter on this gateway's computer. No invented map, odometry or sensor telemetry. Manual directions and timed voice are supported. |
| **ROS_PI** | The configured ROS gateway and commissioned robot sensors/navigation. It does not automatically SSH into the Pi. |

For the classroom USB demo, lift and support the driven wheels, have physical motor-power isolation available, select **DIRECT_USB**, and wait for **MOTOR TRANSPORT READY**. The **CONNECT USB ADAPTER** button is another way to select this output. Connecting performs motor communication/setup and a Stop before it declares readiness. Changing output stops and disarms control; explicitly select the input again.

The preserved motor convention is right ID **1**, left ID **2**, raw directional speed **1000**:

| Direction | Right speed | Left speed |
| --- | ---: | ---: |
| Forward | +1000 | −1000 |
| Backward | −1000 | +1000 |
| Left | +1000 | +1000 |
| Right | −1000 | −1000 |
| Stop | torque off, stored speed 0 | torque off, stored speed 0 |

These are SDK settings, not measured wheel speed in metres per second. Distance, angle, Home and autonomous navigation need real feedback and are rejected in DIRECT_USB. A successful API reply means the gateway accepted a command. **Last SDK-confirmed output** adds motor-protocol response evidence; neither proves that a wheel physically turned.

## Phone camera and secure pairing

Connect Tailscale on the laptop and phone to the same private tailnet. Startup discovers the current laptop's authenticated, online DNS name and configures private HTTPS **`/` → `http://127.0.0.1:5173`**. The dashboard's `/api` proxy then reaches port 8001, including both WebSocket paths. An already-correct `/api` Serve mount to port 8001 is preserved; it is not required for a new setup.

The launcher preserves unrelated Serve routes and refuses to repurpose conflicting root/API/port-443 or public-Funnel configuration. Missing, logged-out or offline Tailscale produces **Secure phone camera unavailable** while local controls remain available. Complete any Tailscale HTTPS setup in its app, then rerun startup. `./scripts/tbot-tailscale-dev.sh` only verifies/configures the same private route for an existing dashboard; it does not start the servers.

Pairing now obtains the verified HTTPS origin from the **gateway**, so the laptop may stay on localhost when generating a QR. In **Gesture Control**, choose **CREATE TABLET LINK** or **NEW PAIRING CODE**, then open the fresh link on the phone. The link must name the current private HTTPS host, never the phone's localhost or an ordinary `http://192.168...` address. Camera permission requires a secure browser context.

On the phone:

1. Wait for **PAIRED**, press **START CAMERA**, and allow camera permission.
2. Keep the index fingertip centered. Camera detection alone never arms movement.
3. Press **REQUEST GESTURE CONTROL** and wait for **GESTURE AUTHORITY ACTIVE**.
4. Move the index fingertip into a directional zone. The phone requires at least 75% confidence and 180 ms of stable direction (with diagonal hysteresis). Center, outside the pad or no hand sends zero.
5. Press **STOP NOW** before changing input. Reconnect, hidden tabs, stopped/frozen cameras and transport faults require explicit control selection again.

If another source owns control, press laptop **STOP** first. The laptop's gesture-transfer button does not replace the phone's explicit arming step. The separate legacy `robot-code/t-bot.gusture.py` uses raised-finger counting; its legend is not the tablet's five-zone pad.

## Switch inputs and test voice

Press **STOP**, wait for its acknowledgement, then select the next input. There is one active owner/source. Returning to a tab, reconnecting a socket or receiving a late reply must not restore movement automatically.

| Input | Acquire control and issue commands |
| --- | --- |
| Keyboard | **Drive → Take keyboard control**, focus the page, use the established WASD mapping. Release keys to stop; Esc is Stop. |
| Joystick | Connect the controller to the laptop, press a button so the browser detects it, then **Take gamepad control**. Keep the known working mapping and deadman button. |
| Phone gesture | Pair, start camera, explicitly request control on the phone. Use the displayed zones. |
| Voice | Open **Voice**. First type an exact command and select **RUN COMMAND**. For speech, **START RECORDING → speak → FINISH RECORDING** after allowing microphone access. |

Voice uses the microphone on the browser that starts recording, usually the laptop. It sends audio to local Vosk; camera frames remain on the phone. Recognized commands execute immediately. Plain `Forward`, `Backward`, `Left` and `Right` request a **five-second** timed movement, so interrupt early with **STOP** during initial physical tests. `Stop` cancels pending recording/transcription and movement. Start with typed `Stop` and a simulator direction before enabling hardware.

`/health` reports `voice_ready` from model-directory presence; the live voice handshake and transcription also check whether the decoder can load. If typed commands work but speech fails, check microphone permission, captured audio, offline model loading and exact recognized text before changing motor settings.

## Wheels-up classroom acceptance

Rehearse the controls in SIMULATION first. On the actual robot, support the driven wheels clear of the ground and keep motor-power isolation accessible. Use one controller at a time, with brief movement and a person watching both wheels.

For **each** of keyboard, joystick, Android gesture and voice, use this exact sequence:

**Stop → Right → Stop → Left → Stop → Forward → Stop → Backward → Stop.**

After every Stop, wait for wheel motion to cease, then explicitly reacquire the input before the next direction. For voice, type the sequence first, then repeat with the microphone. Record the command, selected output/source, SDK-confirmed motor result, actual left/right wheel behavior and whether Stop required intervention. Keep the previously working keyboard/joystick mapping; the legacy `t-bot_wasd.py` printed W/S legend differs from its branches, so do not rewire the robot to compensate for a label.

Then verify these interruptions, wheels still lifted:

1. Keyboard release; gamepad stick neutral and deadman release.
2. Gesture centered, no hand, camera stopped, phone tab hidden, and phone network disconnected.
3. **STOP** while a voice result is pending and during its timed movement.
4. Dashboard/gateway disconnect, followed by reconnect. Confirm motion stays stopped until explicit re-arm; stale queued input must not move the robot.
5. Switch output/source only after Stop. Confirm two browser sessions cannot silently take ownership from each other.

The software independently rejects stale command proofs after **750 ms**, expires manual drive after **250 ms**, gives relay packets a **250 ms** delivery deadline, and has a **300 ms** relay command watchdog. These are software conditions, not a guaranteed physical braking time. A timeout/fault latches Stop; reconnect the relay if it faulted, then explicitly choose control again.

**Stop disables torque and writes zero stored speed.** A wheel can coast; instant braking is not established. The relay watchdog needs a running process and working serial link. `SIGKILL`, OS failure, loss of host power or an interrupted serial link can prevent stop bytes from reaching the motors. An independent physical E-stop/power cutoff or verified hardware/servo watchdog is required to cover those failures. Do not claim hardware stopping protection from a simulated test.

Only after the lifted-wheel checks pass should a supervised, clear-floor demonstration proceed. Android hand recognition in the classroom, microphone/noise accuracy, wheel directions and physical stopping remain hardware acceptance items.

## Troubleshooting

| Symptom | Next check |
| --- | --- |
| Startup reports a port conflict | `status`; stop the identified old server in its original terminal. The launcher never kills an arbitrary port owner. |
| Dashboard loads but gateway is disconnected | Direct and proxied health URLs; backend/frontend logs; verify both report this checkout and protocol 2. |
| Secure phone camera unavailable | Tailscale authenticated/online, current DNS name, private Serve root to 5173, HTTPS on 443, no conflicting API route or public Funnel. |
| Camera permission unavailable | Phone HTTPS URL, browser permission, and another app holding the camera. |
| Directions appear but wheels do not move | DIRECT_USB transport ready, explicit phone authority, command acknowledgement, then SDK write result and observed wheels. Detection telemetry alone is not motor output. |
| Servo device cannot be selected | Verified explicit device or both VID/PID filters; do not choose a sensor port. |
| Serial device is busy | Stop the other motor program/ROS base process before reconnecting. Do not bypass the single-owner lock. |
| SDK import/response fails | Correct `TBOT_SERVO_PYTHON`, optional SDK installation, power, motor IDs, serial device and bus connection. Silence/error/checksum failure is not success. |
| Motion stops after a network delay | Inspect fault text, wait for recovery, reconnect the relay if required and explicitly reacquire control. Do not lengthen watchdogs to hide the fault. |
| Typed voice succeeds but microphone does not | Microphone permission, Vosk readiness and recognized phrase; the motor path already works for the typed input. |

Offline regression entry point: `node scripts/tbot-check.mjs`. The optional browser checks and verification limits are documented in [the audit](tbot-audit.md).

## Separate Pi/ROS work

SSH remains useful for inspecting the Pi and its ROS nodes. It does not relocate a USB adapter plugged into the Mac. If USB is moved to a Pi/Linux host, configure and verify that host's gateway and serial device explicitly; the managed stdio relay supports Linux too. The classroom launcher's ownership checks deliberately reject an unrelated SSH-forwarded gateway at port 8001.

Full ROS navigation needs Ubuntu/ROS 2 Jazzy, the actual sensor drivers, measured geometry, fresh odometry, TF, localization, Nav2 and the velocity guard. The production installer does not install ROS itself, Tailscale, `ydlidar_ros2_driver` or the BNO055 driver. The older production web route on port 8787 is a separate commissioning path; current classroom pairing validation expects the root route on 5173.

**The supplied hardware PDF describes BNO055 I²C wiring, while `backend/hardware.launch.py` configures a UART driver** (default `TBOT_IMU_DEVICE=/dev/ttyUSB1`). Check the actual wiring and driver before changing it; changing a USB path cannot convert I²C to UART. The LiDAR default is `/dev/ttyUSB0` through `TBOT_LIDAR_DEVICE`. ROS odometry prefers `/odometry/filtered`, then `/odom`, then `/wheel/odom`; `TBOT_ODOM_TOPIC` selects an explicit topic. No guessed wiring changes are part of this manual USB demo.
