import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from distribution import launcher


class _Handler(BaseHTTPRequestHandler):
    body = b'{"status": "ok", "app": "answeroncue", "version": "0.1.0"}'

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


def _serve(body: bytes):
    handler = type("H", (_Handler,), {"body": body})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def ours():
    server = _serve(b'{"status": "ok", "app": "answeroncue", "version": "0.1.0"}')
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def other():
    server = _serve(b'{"hello": "world"}')
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "Zażółć gęślą jaźń" / "AnswerOnCue"
    path.mkdir(parents=True)
    monkeypatch.setenv("AOC_INSTALL_ROOT", str(path))
    monkeypatch.delenv("AOC_RELEASE_SOURCE", raising=False)
    monkeypatch.setattr(launcher, "START_TIMEOUT", 0.6)
    return path


def _write_state(root: Path, **state):
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "launcher.json").write_text(json.dumps(state), encoding="utf-8")


class Recorder:
    def __init__(self):
        self.spawned = []
        self.opened = []
        self.alerts = []

    def spawn(self, command, **kwargs):
        self.spawned.append((command, kwargs))

    def opener(self, url):
        self.opened.append(url)

    def alert(self, text):
        self.alerts.append(text)

    def run(self, argv=None):
        return launcher.main(argv or [], opener=self.opener, alert=self.alert, spawn=self.spawn)


def test_probe_ours_other_free(ours, other):
    assert launcher.probe(ours) == "ours"
    assert launcher.probe(other) == "other"
    assert launcher.probe(_free_port()) == "free"


def test_running_server_only_opens_browser(root, ours):
    _write_state(root, port=ours)
    rec = Recorder()
    assert rec.run() == 0
    assert rec.spawned == []
    assert rec.opened == [f"http://answer-on-cue.localhost:{ours}/"]


def test_second_click_while_starting(root, ours):
    _write_state(root, port=ours)
    (root / "data" / "launcher.lock").write_text("1", encoding="utf-8")
    rec = Recorder()
    assert rec.run() == 0
    assert rec.spawned == []
    assert len(rec.opened) == 1


def test_starts_hidden_server_then_opens(root, monkeypatch):
    port = _free_port()
    _write_state(root, port=port, release_source=str(root / "rel"))
    rec = Recorder()
    served = []

    def spawn(command, **kwargs):
        rec.spawn(command, **kwargs)
        served.append(_serve(b'{"status": "ok", "app": "answeroncue"}'))

    # the fake server must bind the chosen port, so patch probe for readiness
    monkeypatch.setattr(launcher, "probe", lambda p: "ours" if rec.spawned else "free")
    assert launcher.main([], opener=rec.opener, alert=rec.alert, spawn=spawn) == 0
    command, kwargs = rec.spawned[0]
    assert command[0].endswith("pythonw.exe")
    assert command[1:4] == ["-m", "uvicorn", "app.main:app"]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == str(port)
    assert kwargs["env"]["AOC_DATA_DIR"] == str(root / "data")
    assert kwargs["env"]["AOC_INSTALL_ROOT"] == str(root)
    assert kwargs["env"]["AOC_RELEASE_SOURCE"] == str(root / "rel")
    assert kwargs["env"]["PYTHONUTF8"] == "1"
    assert kwargs["cwd"] == str(launcher.backend_dir())
    assert (root / "data" / "logs" / "server.log").exists()
    assert rec.opened == [f"http://answer-on-cue.localhost:{port}/"]
    assert not (root / "data" / "launcher.lock").exists()
    for server in served:
        server.shutdown()
        server.server_close()


def test_other_program_on_port_picks_and_saves_fallback(root, other, monkeypatch):
    fallback = _free_port()
    monkeypatch.setattr(launcher, "FALLBACK_PORTS", range(fallback, fallback + 1))
    _write_state(root, port=other)
    state = launcher._load_state(root / "data")
    assert launcher.choose_port(state) == fallback
    rec = Recorder()
    monkeypatch.setattr(launcher, "probe", lambda p: "ours" if p == fallback else "other")
    assert rec.run() == 0
    assert json.loads((root / "data" / "launcher.json").read_text(encoding="utf-8"))["port"] == (
        fallback
    )
    assert rec.opened == [f"http://answer-on-cue.localhost:{fallback}/"]


def test_all_ports_taken_alerts(root, other, monkeypatch):
    monkeypatch.setattr(launcher, "FALLBACK_PORTS", range(other, other + 1))
    _write_state(root, port=other)
    rec = Recorder()
    assert rec.run() == 1
    assert rec.alerts == [
        "Ports 8770–8789 are in use by other programs. Close one of them and try again."
    ]
    assert rec.spawned == [] and rec.opened == []


def test_start_timeout_alerts_with_log_path(root):
    _write_state(root, port=_free_port())
    rec = Recorder()
    assert rec.run() == 1
    log_path = root / "data" / "logs" / "server.log"
    assert rec.alerts == [f"Could not start AnswerOnCue. Details are in the file: {log_path}"]
    assert rec.opened == []


def test_no_browser_flag(root, ours):
    _write_state(root, port=ours)
    rec = Recorder()
    assert rec.run(["--no-browser"]) == 0
    assert rec.opened == []


def test_log_rotation_keeps_5(tmp_path):
    logs = tmp_path / "logs"
    for index in range(10):
        path = launcher.rotate_logs(logs)
        path.write_text(f"run {index}", encoding="utf-8")
    files = sorted(item.name for item in logs.iterdir())
    assert files == ["server.log", "server.log.1", "server.log.2", "server.log.3", "server.log.4"]
    assert (logs / "server.log.1").read_text(encoding="utf-8") == "run 8"


def test_polish_path_with_spaces(root):
    assert "Zażółć gęślą jaźń" in str(launcher.install_root())
    env = launcher.server_env(launcher.install_root(), {"port": 8770})
    assert env["AOC_DATA_DIR"] == str(root / "data")
    command = launcher.server_command(root / "app" / "0.1.0" / "backend", 8770)
    assert command[0] == str(root / "app" / "0.1.0" / ".venv" / "Scripts" / "pythonw.exe")


def test_choose_port_prefers_saved_when_free_or_ours():
    assert launcher.choose_port({"port": 8770}, lambda p: "free") == 8770
    assert launcher.choose_port({"port": 8770}, lambda p: "ours") == 8770
    state = {"port": 8770}
    assert launcher.choose_port(state, lambda p: "other" if p == 8770 else "free") == 8771
    assert state["port"] == 8771
