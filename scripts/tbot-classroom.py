#!/usr/bin/env python3
"""Portable classroom launcher. Standard library only; never installs or opens USB."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = 2
SERVICES = {"backend": (8001, "/health"), "frontend": (5173, "/__tbot/status")}
LOCAL_URL = "http://127.0.0.1:5173/mission-control"


class LaunchError(RuntimeError):
    pass


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=15, **kwargs)


def executable(value):
    found = shutil.which(value)
    if found:
        return str(Path(found).absolute())
    raise LaunchError(f"Executable unavailable: {value}")


def runtimes(root=ROOT, env=None):
    env = os.environ if env is None else env
    local_python = root / ".venv-tbot/bin/python"
    python = executable(env.get("TBOT_PYTHON") or (str(local_python) if local_python.exists() else sys.executable))
    return python, executable(env.get("TBOT_NODE") or "node")


def preflight(root=ROOT, env=None):
    python, node = runtimes(root, env)
    version = run([node, "--version"])
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", version.stdout)
    if version.returncode or not match or tuple(map(int, match.groups())) < (22, 13, 0):
        raise LaunchError("Node 22.13.0 or newer is required; 24.19.0 is the tested version. Select an installed version with TBOT_NODE; no runtime is installed automatically.")
    if version.stdout.strip() != "v24.19.0":
        print(f"Node {version.stdout.strip()} selected; this repo was verified with 24.19.0 (.node-version).")
    dependencies = run([python, "-c", "import fastapi,uvicorn,numpy,qrcode,vosk"], cwd=root)
    if dependencies.returncode:
        raise LaunchError("Backend packages are missing from " + python + ". Install backend/requirements.txt into .venv-tbot before class.\n" + dependencies.stderr.strip()[-500:])
    needed = ["node_modules/vinext/dist/cli.js", "node_modules/vite/bin/vite.js",
              "node_modules/@mediapipe/tasks-vision/package.json", "public/models/hand_landmarker.task",
              "public/mediapipe/wasm/vision_wasm_internal.js", "public/mediapipe/wasm/vision_wasm_internal.wasm",
              "public/mediapipe/wasm/vision_wasm_nosimd_internal.js", "public/mediapipe/wasm/vision_wasm_nosimd_internal.wasm",
              ".tbot-data/models/vosk-model-small-en-us-0.15/am/final.mdl",
              ".tbot-data/models/vosk-model-small-en-us-0.15/conf/model.conf",
              ".tbot-data/models/vosk-model-small-en-us-0.15/graph/HCLr.fst",
              ".tbot-data/models/vosk-model-small-en-us-0.15/graph/Gr.fst"]
    missing = [name for name in needed if not (root / name).is_file() or not (root / name).stat().st_size]
    if missing:
        raise LaunchError("Required offline packages/assets are missing:\n  " + "\n  ".join(missing) + "\nInstall project packages and run scripts/tbot-assets.py before class. Nothing was downloaded.")
    print(f"Offline preflight passed. Python: {python}; Node: {node}")
    return python, node


def tailscale_binary(env=None):
    env = os.environ if env is None else env
    if env.get("TBOT_TAILSCALE_BIN"):
        return executable(env["TBOT_TAILSCALE_BIN"])
    found = shutil.which("tailscale")
    if found:
        return found
    mac_app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    if mac_app.is_file() and os.access(mac_app, os.X_OK):
        return str(mac_app)
    raise LaunchError("Tailscale is not installed or its CLI is unavailable.")


def tailscale_json(binary, *args):
    result = run([binary, *args], env={**os.environ, "TAILSCALE_BE_CLI": "1"})
    if result.returncode:
        raise LaunchError(result.stderr.strip() or "Tailscale command failed.")
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("Expected a status object")
        return data
    except (ValueError, TypeError) as exc:
        raise LaunchError("Tailscale did not return valid JSON.") from exc


def discover_tailscale():
    binary = tailscale_binary()
    status = tailscale_json(binary, "status", "--json")
    own = status.get("Self") or {}
    if status.get("BackendState") != "Running" or own.get("Online") is not True:
        raise LaunchError("Tailscale must be signed in, connected, and online on this computer.")
    name = str(own.get("DNSName") or "").rstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net", name):
        raise LaunchError("Tailscale has no valid private DNS name for this computer.")
    return binary, name


def inspect_serve(config, name):
    port = (config.get("TCP") or {}).get("443")
    if port and (port.get("HTTPS") is not True or port.get("TCPForward")):
        raise LaunchError("Existing Tailscale TCP port 443 belongs to another service; it was preserved.")
    if (config.get("AllowFunnel") or {}).get(name + ":443") is True:
        raise LaunchError("Existing public Funnel on port 443 was preserved. Disable it explicitly before using this private robot dashboard.")
    handlers = (config.get("Web", {}).get(name + ":443", {}) or {}).get("Handlers", {})
    root = handlers.get("/")
    if root and root.get("Proxy", "").rstrip("/") != "http://127.0.0.1:5173":
        raise LaunchError("Existing Tailscale Serve / route belongs to another target; it was preserved. Resolve it explicitly before sharing T-BOT.")
    for path in ("/api", "/api/"):
        if path in handlers and handlers[path].get("Proxy", "").rstrip("/") != "http://127.0.0.1:8001":
            raise LaunchError("Existing Tailscale Serve /api route conflicts with the gateway; it was preserved.")
    return bool(root)


def secure_url(configure=False):
    """Return a verified private URL, or explain why only local startup is ready."""
    try:
        binary, name = discover_tailscale()
        configured = inspect_serve(tailscale_json(binary, "serve", "status", "--json"), name)
        if not configured and configure:
            result = run([binary, "serve", "--bg", "--https=443", "--set-path=/", "http://127.0.0.1:5173"], env={**os.environ, "TAILSCALE_BE_CLI": "1"})
            if result.returncode:
                raise LaunchError(result.stderr.strip() or "Tailscale Serve could not be enabled.")
            configured = inspect_serve(tailscale_json(binary, "serve", "status", "--json"), name)
        if not configured:
            raise LaunchError("Private Serve / is not configured for the dashboard.")
        return "https://" + name
    except (LaunchError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Secure phone camera unavailable: {exc} Local dashboard can still run.")
        return ""


def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def occupied(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.4):
            return True
    except OSError:
        return False


def endpoint(name):
    port, path = SERVICES[name]
    return f"http://127.0.0.1:{port}{path}"


def compatible(name, status, root, public_url=None):
    if not isinstance(status, dict):
        return False
    instance = status.get("instance") or {}
    key = "project_root" if name == "backend" else "root"
    if instance.get("protocol") != PROTOCOL or instance.get(key) != str(root.resolve()) or not isinstance(instance.get("pid"), int):
        return False
    return public_url is None or (status.get("public_url") or "") == public_url


def process_identity(pid):
    if not isinstance(pid, int) or pid <= 1:
        return None
    result = run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "args="])
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def runtime_dir(root):
    folder = root / ".tbot-data/runtime"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_records(root):
    try:
        record = json.loads((root / ".tbot-data/runtime/processes.json").read_text())
        if record.get("root") != str(root.resolve()) or record.get("protocol") != PROTOCOL:
            return {}
        return record.get("services", {})
    except (OSError, ValueError):
        return {}


def save_records(root, records):
    target = runtime_dir(root) / "processes.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"root": str(root.resolve()), "protocol": PROTOCOL, "services": records}, indent=2))
    temporary.replace(target)


def owns_process(record):
    return bool(record.get("identity") and process_identity(record.get("pid")) == record["identity"])


@contextlib.contextmanager
def operation_lock(root):
    with (runtime_dir(root) / "launcher.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LaunchError("Another classroom launcher operation is running.") from exc
        yield


def stop_record(record):
    if not owns_process(record):
        return False
    os.kill(record["pid"], signal.SIGTERM)
    for _ in range(50):
        if not owns_process(record):
            return True
        time.sleep(.1)
    raise LaunchError(f"Owned process {record['pid']} has not stopped yet; no force-kill was sent.")


def stop(root=ROOT):
    with operation_lock(root):
        records = load_records(root)
        for name in ("frontend", "backend"):
            if name not in records:
                print(f"{name}: no launcher-owned process to stop")
                continue
            try:
                stopped = stop_record(records[name])
                print(f"{name}: stopped" if stopped else f"{name}: stale or changed PID; no signal sent")
                records.pop(name)
                save_records(root, records)
            except (OSError, LaunchError) as exc:
                print(f"{name}: {exc}")
        return records == {}


def status(root=ROOT):
    records, okay = load_records(root), True
    for name, (port, _) in SERVICES.items():
        data = get_json(endpoint(name))
        if compatible(name, data, root):
            owned = name in records and owns_process(records[name]) and records[name]["pid"] == data["instance"]["pid"]
            print(f"{name}: healthy on 127.0.0.1:{port}; {'launcher-owned' if owned else 'independently started (will not be stopped)'}")
            if data.get("public_url"):
                print(f"  Private URL: {data['public_url']}/mission-control")
        else:
            okay = False
            print(f"{name}: {'incompatible port owner; no action taken' if occupied(port) else 'not running'}")
    print(f"Local dashboard: {LOCAL_URL}")
    return okay


def verify_proxy(root, public_url, backend_pid):
    data = get_json("http://127.0.0.1:5173/api/health")
    if not compatible("backend", data, root, public_url) or data["instance"]["pid"] != backend_pid:
        raise LaunchError("Dashboard /api proxy does not reach this checkout's running gateway. Check vite.config.ts and frontend.log; no commands were sent.")


def start(root=ROOT, open_browser=True):
    python, node = preflight(root)
    with operation_lock(root):
        records, launched = load_records(root), []
        reusable = {}
        # Never share or replace an unrelated existing service, including one
        # from a different checkout. Inspect ports before changing Serve.
        for name, (port, _) in SERVICES.items():
            data = get_json(endpoint(name))
            if occupied(port):
                if not compatible(name, data, root):
                    raise LaunchError(f"Port {port} is occupied by an incompatible or stale {name}. Stop it in its original terminal, then retry. Expected this checkout and protocol 2. No process was killed.")
                reusable[name] = data
        public_url = secure_url(configure=True)
        for name, data in reusable.items():
            if not compatible(name, data, root, public_url):
                raise LaunchError(f"Existing {name} has a different public URL. Stop it in its original terminal, then retry with {public_url or 'local-only mode'}. No process was killed.")
        env = {**os.environ, "TBOT_MODE": "virtual", "TBOT_ROS": "0", "TBOT_PUBLIC_URL": public_url,
               "TBOT_DATA": str(root / ".tbot-data"), "NEXT_PUBLIC_TBOT_API": "", "PYTHONUNBUFFERED": "1"}
        commands = {
            "backend": [python, "-m", "uvicorn", "backend.app:app", "--app-dir", str(root), "--host", "127.0.0.1", "--port", "8001"],
            "frontend": [node, str(root / "scripts/run-framework.mjs"), "dev", "--hostname", "127.0.0.1", "--port", "5173"],
        }
        try:
            for name in SERVICES:
                if name in reusable:
                    print(f"Reusing compatible {name} (PID {reusable[name]['instance']['pid']}).")
                    if name in records and not owns_process(records[name]):
                        records.pop(name)
                    continue
                logfile = runtime_dir(root) / f"{name}.log"
                with logfile.open("ab") as output:
                    process = subprocess.Popen(commands[name], cwd=root, env=env, stdin=subprocess.DEVNULL,
                                               stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                identity = process_identity(process.pid)
                if not identity:
                    raise LaunchError(f"{name} exited before startup. See {logfile}")
                records[name] = {"pid": process.pid, "identity": identity, "command": commands[name]}
                launched.append(name)
                save_records(root, records)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    data = get_json(endpoint(name))
                    if compatible(name, data, root, public_url) and data["instance"]["pid"] == process.pid:
                        break
                    if process.poll() is not None:
                        raise LaunchError(f"{name} exited with code {process.returncode}. See {logfile}")
                    time.sleep(.25)
                else:
                    raise LaunchError(f"{name} did not become ready. See {logfile}")
                print(f"Started {name}; log: {logfile}")
            backend = get_json(endpoint("backend"))
            if not compatible("backend", backend, root, public_url):
                raise LaunchError("Gateway health changed during startup; retry after checking backend.log.")
            verify_proxy(root, public_url, backend["instance"]["pid"])
            save_records(root, records)
        except BaseException:
            for name in reversed(launched):
                try:
                    if stop_record(records[name]):
                        records.pop(name)
                except (OSError, LaunchError):
                    pass
            save_records(root, records)
            raise
    print(f"Local dashboard: {LOCAL_URL}")
    if public_url:
        print(f"Private phone/camera dashboard: {public_url}/mission-control")
        print("Open that private URL on the laptop, then create the phone pairing link in Gesture mode.")
    else:
        print("Secure phone camera unavailable; use local keyboard/joystick/voice until Tailscale is ready.")
    if open_browser:
        webbrowser.open((public_url + "/mission-control") if public_url else LOCAL_URL)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("start", "status", "stop", "preflight", "secure"), default="start")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser after startup")
    args = parser.parse_args(argv)
    try:
        if args.action == "start":
            return 0 if start(open_browser=not args.no_open) else 1
        if args.action == "status":
            return 0 if status() else 1
        if args.action == "stop":
            return 0 if stop() else 1
        if args.action == "secure":
            url = secure_url(configure=True)
            if url:
                print(url + "/mission-control")
            return 0 if url else 1
        preflight()
        print("Private camera URL:", secure_url() or "unavailable; local controls remain usable")
        return 0
    except (LaunchError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"T-BOT startup: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
