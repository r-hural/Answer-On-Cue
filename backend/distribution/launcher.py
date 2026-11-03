"""Desktop launcher (started by pythonw): open the app, starting its server first if needed."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Literal

DEFAULT_PORT = 8770
FALLBACK_PORTS = range(8771, 8790)
START_TIMEOUT = 90.0
LOCK_MAX_AGE = 120.0
TITLE = "AnswerOnCue"
# The address the browser opens; the server and the probe stay on 127.0.0.1.
APP_HOST = "answer-on-cue.localhost"
NO_PORT_TEXT = "Ports 8770–8789 are in use by other programs. Close one of them and try again."

Probe = Callable[[int], Literal["ours", "other", "free"]]


def app_url(port: int) -> str:
    return f"http://{APP_HOST}:{port}/"


def start_failure_text(log_path: Path) -> str:
    return f"Could not start AnswerOnCue. Details are in the file: {log_path}"


def message_box(text: str) -> None:
    if sys.platform != "win32":
        print(text, file=sys.stderr)
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(0, text, TITLE, 0x10)


def install_root() -> Path:
    override = os.environ.get("AOC_INSTALL_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4]


def backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def probe(port: int) -> Literal["ours", "other", "free"]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        if sock.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2.0) as reply:
            data = json.loads(reply.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return "other"
    if isinstance(data, dict) and data.get("app") == "answeroncue":
        return "ours"
    return "other"


def choose_port(state: dict, probe: Probe = probe) -> int | None:
    saved = int(state.get("port") or DEFAULT_PORT)
    if probe(saved) in ("ours", "free"):
        return saved
    for candidate in FALLBACK_PORTS:
        if candidate != saved and probe(candidate) in ("ours", "free"):
            state["port"] = candidate
            return candidate
    return None


def server_command(backend_dir: Path, port: int) -> list[str]:
    pythonw = backend_dir.parent / ".venv" / "Scripts" / "pythonw.exe"
    return [
        str(pythonw),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def server_env(root: Path, state: dict) -> dict[str, str]:
    env = dict(os.environ)
    env["AOC_DATA_DIR"] = str(root / "data")
    env["AOC_INSTALL_ROOT"] = str(root)
    env["PYTHONUTF8"] = "1"
    if state.get("port"):
        env["AOC_SERVER_PORT"] = str(state["port"])
    if state.get("release_source"):
        env["AOC_RELEASE_SOURCE"] = str(state["release_source"])
    else:
        env.pop("AOC_RELEASE_SOURCE", None)
    return env


def rotate_logs(logs_dir: Path, name: str = "server.log", keep: int = 5) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    current = logs_dir / name
    for index in range(keep - 1, 0, -1):
        source = current if index == 1 else logs_dir / f"{name}.{index - 1}"
        target = logs_dir / f"{name}.{index}"
        if source.exists():
            if target.exists():
                target.unlink()
            source.rename(target)
    for extra in logs_dir.glob(f"{name}.*"):
        suffix = extra.name.removeprefix(f"{name}.")
        if suffix.isdigit() and int(suffix) >= keep:
            extra.unlink()
    return current


def wait_ready(port: int, timeout: float = START_TIMEOUT, probe: Probe = probe) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if probe(port) == "ours":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def _load_state(data_dir: Path) -> dict:
    try:
        data = json.loads((data_dir / "launcher.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    state = {"port": DEFAULT_PORT, "version": None, "previous": None, "release_source": None}
    if isinstance(data, dict):
        state.update(data)
    return state


def _save_state(data_dir: Path, state: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "launcher.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _starting_elsewhere(lock: Path) -> bool:
    try:
        return time.time() - lock.stat().st_mtime < LOCK_MAX_AGE
    except OSError:
        return False


def main(
    argv: list[str] | None = None,
    *,
    opener: Callable[[str], object] = webbrowser.open,
    alert: Callable[[str], object] = message_box,
    spawn: Callable[..., object] = subprocess.Popen,
) -> int:
    parser = argparse.ArgumentParser(prog="distribution.launcher")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    root = install_root()
    data_dir = root / "data"
    state = _load_state(data_dir)
    saved_port = state["port"]
    port = choose_port(state, lambda candidate: probe(candidate))
    if port is None:
        alert(NO_PORT_TEXT)
        return 1
    if port != saved_port:
        _save_state(data_dir, state)

    if probe(port) != "ours":
        logs_dir = data_dir / "logs"
        log_path = logs_dir / "server.log"
        lock = data_dir / "launcher.lock"
        if not _starting_elsewhere(lock):
            log_path = rotate_logs(logs_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            lock.write_text(str(os.getpid()), encoding="utf-8")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "DETACHED_PROCESS", 0
            )
            with open(log_path, "ab") as log:
                spawn(
                    server_command(backend_dir(), port),
                    cwd=str(backend_dir()),
                    env=server_env(root, state),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
        ready = wait_ready(port, START_TIMEOUT, lambda candidate: probe(candidate))
        try:
            lock.unlink()
        except OSError:
            pass
        if not ready:
            alert(start_failure_text(log_path))
            return 1

    if not args.no_browser:
        opener(app_url(port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
