"""Launcher tests use fake processes/network: no local servers or USB are touched."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "scripts/tbot-classroom.py"
spec = importlib.util.spec_from_file_location("tbot_classroom_tests", LAUNCHER)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def result(stdout="", code=0, stderr=""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


def marker(name, root, pid=123, public_url=""):
    key = "project_root" if name == "backend" else "root"
    return {"instance": {key: str(root.resolve()), "protocol": 2, "pid": pid}, "public_url": public_url}


def serve(name="new-laptop.tail123.ts.net", handlers=None):
    return {"Web": {name + ":443": {"Handlers": handlers or {"/": {"Proxy": "http://127.0.0.1:5173"}}}}}


@pytest.fixture
def installed(tmp_path, monkeypatch):
    files = ["node_modules/vinext/dist/cli.js", "node_modules/vite/bin/vite.js",
             "node_modules/@mediapipe/tasks-vision/package.json", "public/models/hand_landmarker.task"]
    files += ["public/mediapipe/wasm/vision_wasm_" + name for name in ("internal.js", "internal.wasm", "nosimd_internal.js", "nosimd_internal.wasm")]
    files += [".tbot-data/models/vosk-model-small-en-us-0.15/" + name for name in ("am/final.mdl", "conf/model.conf", "graph/HCLr.fst", "graph/Gr.fst")]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("installed")
    monkeypatch.setattr(launcher, "runtimes", lambda *args: ("/installed/python", "/installed/node"))
    return tmp_path


@pytest.mark.parametrize("version", ["v22.13.0", "v24.19.0", "v26.8.2"])
def test_preflight_supports_supported_node_without_install_or_downgrade(installed, monkeypatch, version):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return result(version if args[-1] == "--version" else "")
    monkeypatch.setattr(launcher, "run", run)
    assert launcher.preflight(installed) == ("/installed/python", "/installed/node")
    assert len(calls) == 2
    assert all("install" not in args for args in calls)


def test_preflight_rejects_old_node_before_imports_or_downloads(installed, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "run", lambda args, **kwargs: calls.append(args) or result("v22.12.0"))
    with pytest.raises(launcher.LaunchError, match="22.13"):
        launcher.preflight(installed)
    assert len(calls) == 1


def test_preflight_detects_missing_offline_model(installed, monkeypatch):
    (installed / "public/models/hand_landmarker.task").unlink()
    monkeypatch.setattr(launcher, "run", lambda args, **kwargs: result("v24.19.0" if args[-1] == "--version" else ""))
    with pytest.raises(launcher.LaunchError, match="hand_landmarker.task"):
        launcher.preflight(installed)


def test_preflight_reports_dependency_failure_without_installing(installed, monkeypatch):
    monkeypatch.setattr(launcher, "run", lambda args, **kwargs: result("v24.19.0") if args[-1] == "--version" else result(code=1, stderr="No module named vosk"))
    with pytest.raises(launcher.LaunchError, match="No module named vosk"):
        launcher.preflight(installed)


def test_runtime_discovery_uses_relocated_checkout_or_explicit_environment(tmp_path, monkeypatch):
    python = tmp_path / "another user/checkout/.venv-tbot/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("fake executable")
    monkeypatch.setattr(launcher, "executable", lambda value: value)
    assert launcher.runtimes(python.parents[2], {}) == (str(python), "node")
    assert launcher.runtimes(python.parents[2], {"TBOT_PYTHON": "/chosen/python", "TBOT_NODE": "/chosen/node"}) == ("/chosen/python", "/chosen/node")


@pytest.mark.parametrize("state,online", [("NeedsLogin", True), ("Stopped", True), ("Running", False), ("Running", None)])
def test_tailscale_requires_authenticated_online_state(monkeypatch, state, online):
    monkeypatch.setattr(launcher, "tailscale_binary", lambda: "/installed/tailscale")
    monkeypatch.setattr(launcher, "tailscale_json", lambda *args: {"BackendState": state, "Self": {"Online": online, "DNSName": "new-laptop.tail123.ts.net."}})
    with pytest.raises(launcher.LaunchError, match="online"):
        launcher.discover_tailscale()


def test_tailscale_discovers_current_hostname_and_strips_final_dot(monkeypatch):
    monkeypatch.setattr(launcher, "tailscale_binary", lambda: "/installed/tailscale")
    monkeypatch.setattr(launcher, "tailscale_json", lambda *args: {"BackendState": "Running", "Self": {"Online": True, "DNSName": "OTHER-MAC.tail123.ts.net."}})
    assert launcher.discover_tailscale() == ("/installed/tailscale", "other-mac.tail123.ts.net")


def test_serve_adds_only_root_and_preserves_existing_api_and_other_routes(monkeypatch):
    name = "new-laptop.tail123.ts.net"
    handlers = {"/api": {"Proxy": "http://127.0.0.1:8001"}, "/other": {"Proxy": "http://127.0.0.1:9999"}}
    config, calls = serve(name, handlers), []
    monkeypatch.setattr(launcher, "discover_tailscale", lambda: ("/installed/tailscale", name))
    monkeypatch.setattr(launcher, "tailscale_json", lambda *args: config)
    def run(args, **kwargs):
        calls.append(args)
        handlers["/"] = {"Proxy": "http://127.0.0.1:5173"}
        return result()
    monkeypatch.setattr(launcher, "run", run)
    assert launcher.secure_url(configure=True) == "https://" + name
    assert calls == [["/installed/tailscale", "serve", "--bg", "--https=443", "--set-path=/", "http://127.0.0.1:5173"]]
    assert handlers["/other"]["Proxy"] == "http://127.0.0.1:9999"
    assert handlers["/api"]["Proxy"] == "http://127.0.0.1:8001"
    assert launcher.secure_url(configure=True) == "https://" + name
    assert len(calls) == 1, "an already-correct Serve configuration must not be rewritten"


@pytest.mark.parametrize("path", ["/", "/api", "/api/"])
def test_conflicting_serve_mount_is_preserved_and_local_mode_remains_available(monkeypatch, path, capsys):
    monkeypatch.setattr(launcher, "discover_tailscale", lambda: ("/installed/tailscale", "new-laptop.tail123.ts.net"))
    config = serve(handlers={path: {"Proxy": "http://127.0.0.1:9999"}})
    monkeypatch.setattr(launcher, "tailscale_json", lambda *args: config)
    monkeypatch.setattr(launcher, "run", lambda *args, **kwargs: pytest.fail("Must not change conflicting Serve routes"))
    assert launcher.secure_url(configure=True) == ""
    assert config["Web"]["new-laptop.tail123.ts.net:443"]["Handlers"][path]["Proxy"].endswith(":9999")
    assert "Local dashboard can still run" in capsys.readouterr().out


def test_missing_tailscale_keeps_local_startup_available(monkeypatch):
    def missing():
        raise launcher.LaunchError("Tailscale is not installed")
    monkeypatch.setattr(launcher, "discover_tailscale", missing)
    assert launcher.secure_url(configure=True) == ""


@pytest.mark.parametrize("config", [
    {"TCP": {"443": {"TCPForward": "127.0.0.1:9000"}}},
    {"AllowFunnel": {"new-laptop.tail123.ts.net:443": True}},
])
def test_existing_tcp_service_or_public_funnel_is_not_repurposed(monkeypatch, config):
    monkeypatch.setattr(launcher, "discover_tailscale", lambda: ("/installed/tailscale", "new-laptop.tail123.ts.net"))
    monkeypatch.setattr(launcher, "tailscale_json", lambda *args: config)
    monkeypatch.setattr(launcher, "run", lambda *args, **kwargs: pytest.fail("Conflicting port configuration must remain untouched"))
    assert launcher.secure_url(configure=True) == ""


@pytest.mark.parametrize("change", ["root", "protocol", "public_url"])
def test_reuse_rejects_foreign_checkout_protocol_or_public_url(tmp_path, change):
    data = marker("backend", tmp_path)
    if change == "root":
        data["instance"]["project_root"] = "/some/other/checkout"
    elif change == "protocol":
        data["instance"]["protocol"] = 1
    else:
        data["public_url"] = "https://old-laptop.tail123.ts.net"
    assert not launcher.compatible("backend", data, tmp_path, "")


def test_foreign_port_conflict_starts_nothing_and_changes_no_serve_config(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "preflight", lambda root: ("/installed/python", "/installed/node"))
    monkeypatch.setattr(launcher, "get_json", lambda url: marker("backend", tmp_path / "other-checkout"))
    monkeypatch.setattr(launcher, "occupied", lambda port: True)
    monkeypatch.setattr(launcher, "secure_url", lambda **kwargs: pytest.fail("Do not share a foreign service"))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Do not spawn on an occupied port"))
    with pytest.raises(launcher.LaunchError, match="8001"):
        launcher.start(tmp_path, open_browser=False)


def test_healthy_compatible_servers_are_reused_but_not_adopted_for_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "preflight", lambda root: ("python", "node"))
    monkeypatch.setattr(launcher, "secure_url", lambda **kwargs: "")
    monkeypatch.setattr(launcher, "occupied", lambda port: True)
    monkeypatch.setattr(launcher, "get_json", lambda url: marker("frontend" if url.endswith("/__tbot/status") else "backend", tmp_path))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Compatible processes should be reused"))
    monkeypatch.setattr(launcher.os, "kill", lambda *args: pytest.fail("Independently started process must never be signaled"))
    assert launcher.start(tmp_path, open_browser=False)
    assert launcher.load_records(tmp_path) == {}
    assert launcher.stop(tmp_path)


def test_proxy_identity_must_match_direct_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "get_json", lambda url: marker("backend", tmp_path, pid=99))
    with pytest.raises(launcher.LaunchError, match="/api proxy"):
        launcher.verify_proxy(tmp_path, "", backend_pid=88)


def test_safe_stop_does_not_signal_a_reused_pid(tmp_path, monkeypatch):
    launcher.save_records(tmp_path, {"backend": {"pid": 321, "identity": "yesterday python old checkout"}})
    monkeypatch.setattr(launcher, "process_identity", lambda pid: "today another users process")
    monkeypatch.setattr(launcher.os, "kill", lambda *args: pytest.fail("Reused PID must never receive a signal"))
    assert launcher.stop(tmp_path)
    assert launcher.load_records(tmp_path) == {}


def test_safe_stop_signals_only_the_recorded_child(monkeypatch):
    identity, signals = ["start python this checkout"], []
    monkeypatch.setattr(launcher, "process_identity", lambda pid: identity[0])
    def kill(pid, sig):
        signals.append((pid, sig))
        identity[0] = None
    monkeypatch.setattr(launcher.os, "kill", kill)
    assert launcher.stop_record({"pid": 321, "identity": identity[0]})
    assert signals == [(321, launcher.signal.SIGTERM)]


def test_start_uses_new_checkout_and_forces_simulation_without_ros(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "preflight", lambda root: ("/new/python", "/new/node"))
    monkeypatch.setattr(launcher, "secure_url", lambda **kwargs: "")
    monkeypatch.setattr(launcher, "occupied", lambda port: False)
    monkeypatch.setenv("TBOT_MODE", "hardware")
    monkeypatch.setenv("TBOT_ROS", "1")
    monkeypatch.setenv("TBOT_DATA", "/old/checkout/data")
    monkeypatch.setenv("NEXT_PUBLIC_TBOT_API", "https://old-host.invalid")
    running, calls = {}, []
    def popen(args, **kwargs):
        name = "backend" if "uvicorn" in args else "frontend"
        pid = 101 if name == "backend" else 102
        running[name] = pid
        calls.append((args, kwargs))
        return SimpleNamespace(pid=pid, poll=lambda: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    monkeypatch.setattr(launcher, "process_identity", lambda pid: f"start current-checkout process-{pid}")
    def health(url):
        name = "frontend" if url.endswith("/__tbot/status") else "backend"
        return marker(name, tmp_path, running[name]) if name in running else None
    monkeypatch.setattr(launcher, "get_json", health)
    assert launcher.start(tmp_path, open_browser=False)
    assert len(calls) == 2
    for args, options in calls:
        assert options["cwd"] == tmp_path
        assert options["start_new_session"] is True
        assert options["env"]["TBOT_MODE"] == "virtual"
        assert options["env"]["TBOT_ROS"] == "0"
        assert options["env"]["TBOT_DATA"] == str(tmp_path / ".tbot-data")
        assert options["env"]["NEXT_PUBLIC_TBOT_API"] == ""
        assert "127.0.0.1" in args and "0.0.0.0" not in args
    assert calls[0][0][0] == "/new/python"
    assert calls[1][0][1] == str(tmp_path / "scripts/run-framework.mjs")
    assert set(launcher.load_records(tmp_path)) == {"backend", "frontend"}


def test_copied_launcher_discovers_fresh_root_from_any_working_directory(tmp_path):
    root = tmp_path / "fresh clone with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in ("tbot-classroom.py", "tbot-classroom.sh"):
        shutil.copy(LAUNCHER.with_name(name), scripts / name)
    command = ["bash", str(scripts / "tbot-classroom.sh"), "--help"]
    output = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert output.returncode == 0
    assert "preflight" in output.stdout
    isolated = subprocess.run(["python3", "-c", "import runpy,sys; print(runpy.run_path(sys.argv[1])['ROOT'])", str(scripts / "tbot-classroom.py")], cwd=tmp_path, capture_output=True, text=True)
    assert isolated.returncode == 0
    assert isolated.stdout.strip() == str(root)
