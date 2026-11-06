import json
import plistlib
import subprocess

import pytest

from ableton_mcp import cli, doctor
from ableton_mcp.live_client import PROTOCOL_VERSION, LiveError, LiveTimeout, LiveUnavailable


def make_app(apps, name, version):
    contents = apps / f"{name}.app" / "Contents"
    contents.mkdir(parents=True)
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": version}, f)


def test_live_app(tmp_path):
    assert doctor.check_live_app([tmp_path])[0] == doctor.FAIL
    make_app(tmp_path, "Ableton Live 12 Lite", "12.0.5 (x)")
    assert doctor.check_live_app([tmp_path])[0] == doctor.FAIL
    new = tmp_path / "new"
    make_app(new, "Ableton Live 12 Lite", "12.1.10 (x)")
    status, message, _ = doctor.check_live_app([new])
    assert (status, message) == (doctor.PASS, "Ableton Live 12 Lite.app 12.1.10")


SESSION = "t: info: Started: Live 12.1.10 Build: x\nt: info: Licensing: Processed license line SECRET\n"
SELECTED = 't: info: MidiRemoteScript 1 [Control Surface="AbletonMCP" Input="None" Output="None"]\n'
LISTENING = "t: info: RemoteScriptMessage: (AbletonMCP) AbletonMCP listening on 127.0.0.1:5000 (Python 3.11.6)\n"


def write_log(tmp_path, text):
    folder = tmp_path / "Live 12.1.10"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Log.txt").write_text(text)


def test_live_log(tmp_path):
    assert doctor.check_live_log(tmp_path)[0] == doctor.FAIL

    write_log(tmp_path, SESSION + SELECTED + LISTENING + SESSION + SELECTED)
    status, message, fix = doctor.check_live_log(tmp_path)
    assert status == doctor.FAIL and "install-script" in fix

    write_log(tmp_path, SESSION + LISTENING + SESSION)
    status, message, fix = doctor.check_live_log(tmp_path)
    assert status == doctor.FAIL and "Control Surface" in fix

    write_log(tmp_path, SESSION + SESSION + SELECTED + LISTENING)
    status, message, fix = doctor.check_live_log(tmp_path)
    assert status == doctor.PASS and "Python 3.11.6" in message and "quit" not in message

    write_log(tmp_path, SESSION + LISTENING + "t: info: Live App: GlobalExit(): End (0)\n")
    status, message, fix = doctor.check_live_log(tmp_path)
    assert status == doctor.PASS and message.endswith("(last session, Live has quit)")

    write_log(tmp_path, SESSION + SESSION + LISTENING + "t: Traceback (most recent call last)\n")
    status, message, fix = doctor.check_live_log(tmp_path)
    assert status == doctor.WARN and "1 Traceback" in message and "Log.txt:6" in message
    assert "license" not in message.lower() and "SECRET" not in message


def test_remote_script(tmp_path):
    dest = tmp_path / "Remote Scripts" / "AbletonMCP"
    status, _, fix = doctor.check_remote_script(dest)
    assert status == doctor.FAIL and "install-script" in fix

    cli.install_script(tmp_path)
    assert doctor.check_remote_script(dest)[0] == doctor.PASS
    (dest / "__pycache__").mkdir(exist_ok=True)
    (dest / "__pycache__" / "x.py").write_text("ignored")
    assert doctor.check_remote_script(dest)[0] == doctor.PASS

    ops = dest / "ops.py"
    data = bytearray(ops.read_bytes())
    data[0] ^= 1
    ops.write_bytes(bytes(data))
    status, message, fix = doctor.check_remote_script(dest)
    assert status == doctor.FAIL and "ops.py" in message and "install-script" in fix


def write_conn(tmp_path, mode, token="tok-SECRET-123"):
    conn = tmp_path / "connection.json"
    conn.write_text(json.dumps({"port": 5000, "token": token}))
    conn.chmod(mode)
    return conn


def test_connection_file(tmp_path):
    assert doctor.check_connection_file(tmp_path / "connection.json")[0] == doctor.FAIL
    status, _, fix = doctor.check_connection_file(write_conn(tmp_path, 0o644))
    assert status == doctor.FAIL and "chmod 600" in fix
    result = doctor.check_connection_file(write_conn(tmp_path, 0o600))
    assert result[0] == doctor.PASS and "5000" in result[1]
    assert "tok-SECRET-123" not in repr(result)
    (tmp_path / "connection.json").write_text("{")
    assert doctor.check_connection_file(tmp_path / "connection.json")[0] == doctor.FAIL


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def call(self, op, args=None):
        self.calls.append(op)
        if self.error:
            raise self.error
        return self.result


HELLO = {"live_version": "12.1.10", "variant": "Lite", "script_version": "0.5.0", "python": "3.11.6 (main, x)"}


def test_bridge(tmp_path):
    conn = write_conn(tmp_path, 0o600)
    status, message, _ = doctor.check_bridge(conn, FakeClient(error=LiveUnavailable("x")))
    assert status == doctor.FAIL and "stale" in message and "5000" in message

    status, _, fix = doctor.check_bridge(conn, FakeClient(error=LiveTimeout("x")))
    assert status == doctor.FAIL and "dialog" in fix
    status, message, _ = doctor.check_bridge(conn, FakeClient(error=LiveError("AUTH", "bad token")))
    assert status == doctor.FAIL and "AUTH" in message

    status, _, fix = doctor.check_bridge(conn, FakeClient({**HELLO, "protocol_version": 1}))
    assert status == doctor.FAIL and "install-script" in fix

    client = FakeClient({**HELLO, "protocol_version": PROTOCOL_VERSION})
    status, message, _ = doctor.check_bridge(conn, client)
    assert status == doctor.PASS and message.endswith("Python 3.11.6") and client.calls == ["hello"]


def fake_run(rc, stdout):
    def run(cmd, **kwargs):
        assert cmd == ["claude", "mcp", "get", "ableton"] and kwargs["cwd"] == doctor.REPO_ROOT
        return subprocess.CompletedProcess(cmd, rc, stdout, "")

    return run


def test_claude_code(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.check_claude_code()[0] == doctor.WARN

    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(doctor.subprocess, "run", fake_run(1, "No MCP server named ableton"))
    status, _, fix = doctor.check_claude_code()
    assert status == doctor.FAIL and "claude mcp add ableton" in fix

    monkeypatch.setattr(doctor.subprocess, "run", fake_run(0, "ableton:\n  Status: ✘ Failed to connect\n"))
    status, message, _ = doctor.check_claude_code()
    assert status == doctor.WARN and "Failed to connect" in message

    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(doctor.subprocess, "run", timeout)
    assert doctor.check_claude_code()[0] == doctor.WARN

    monkeypatch.setattr(doctor.subprocess, "run", fake_run(0, "ableton:\n  Status: ✔ Connected\n"))
    assert doctor.check_claude_code()[0] == doctor.PASS


@pytest.fixture
def empty_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    return tmp_path


def test_doctor_empty_home(empty_home, monkeypatch, capsys):
    clients = []
    monkeypatch.setattr(doctor, "LiveClient", lambda *a, **k: clients.append(a) or FakeClient())
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  connection-file" in out and "SKIP  bridge" in out and "WARN  claude-code" in out
    assert clients == []


def test_doctor_all_pass(empty_home, monkeypatch, capsys):
    cli.install_script(empty_home)
    write_log(empty_home / "Library" / "Preferences" / "Ableton", SESSION + LISTENING)
    (empty_home / ".ableton-mcp").mkdir()
    write_conn(empty_home / ".ableton-mcp", 0o600)
    make_app(empty_home / "Applications", "Ableton Live 12 Lite", "12.1.10 (x)")
    monkeypatch.setattr(doctor, "LiveClient", lambda *a, **k: FakeClient({**HELLO, "protocol_version": PROTOCOL_VERSION}))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(doctor.subprocess, "run", fake_run(0, "Status: ✔ Connected"))
    assert cli.main(["doctor", "--user-library", str(empty_home)]) == 0
    out = capsys.readouterr().out
    assert "6 passed, 0 warning(s), 0 failed, 0 skipped" in out and "tok-SECRET" not in out
