import json
import socket
import threading
import time

import pytest

from AbletonMCP.bridge import Bridge
from AbletonMCP.ops import OPS
from ableton_mcp import cli
from ableton_mcp.live_client import CONTROL_SURFACE_HINT, LiveClient, LiveError, LiveTimeout, LiveUnavailable
from fake_live import Application, Song, install_fake_live_module


@pytest.fixture
def live(tmp_path):
    """Real Bridge on loopback, ticked every 10 ms from a test thread."""
    install_fake_live_module()
    conn_file = tmp_path / "connection.json"
    song, app = Song(), Application()
    bridge = Bridge(lambda: song, lambda: app, OPS, log=lambda m: None, conn_file=conn_file)
    bridge.start()
    stop = threading.Event()

    def run():
        while not stop.is_set():
            bridge.tick()
            time.sleep(0.01)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    yield conn_file
    stop.set()
    t.join()
    bridge.close()


def test_hello_round_trip(live):
    result = LiveClient(conn_file=live).call("hello")
    assert result["variant"] == "Lite"
    assert result["live_version"] == "12.1.10"


def test_missing_connection_file(tmp_path):
    with pytest.raises(LiveUnavailable, match="Control Surface"):
        LiveClient(conn_file=tmp_path / "nope.json").call("hello")
    assert "Link, Tempo & MIDI" in CONTROL_SURFACE_HINT


def test_connection_refused(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens on this port now
    conn_file = tmp_path / "connection.json"
    conn_file.write_text(json.dumps({"port": port, "token": "t"}))
    with pytest.raises(LiveUnavailable, match="Control Surface"):
        LiveClient(conn_file=conn_file).call("hello")


def test_timed_out_write_sent_exactly_once(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    conn_file = tmp_path / "connection.json"
    conn_file.write_text(json.dumps({"port": server.getsockname()[1], "token": "t"}))
    received = []

    def silent_live():  # reads requests, never answers
        server.settimeout(3)
        try:
            while True:
                conn, _ = server.accept()
                conn.settimeout(3)
                buf = b""
                try:
                    while chunk := conn.recv(65536):
                        buf += chunk
                except OSError:
                    pass
                received.extend(buf.splitlines())
                conn.close()
        except OSError:
            pass

    t = threading.Thread(target=silent_live, daemon=True)
    t.start()
    client = LiveClient(conn_file=conn_file, budget_s=0.1)
    with pytest.raises(LiveTimeout):
        client.call("set_tempo", {"bpm": 90})
    server.close()
    t.join(timeout=5)
    assert len(received) == 1
    assert json.loads(received[0])["op"] == "set_tempo"


def test_garbage_response_is_protocol_error(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    conn_file = tmp_path / "connection.json"
    conn_file.write_text(json.dumps({"port": server.getsockname()[1], "token": "t"}))

    def bad_live():
        conn, _ = server.accept()
        conn.recv(65536)
        conn.sendall(b"not json\n")
        conn.close()

    t = threading.Thread(target=bad_live, daemon=True)
    t.start()
    with pytest.raises(LiveError) as e:
        LiveClient(conn_file=conn_file).call("hello")
    assert e.value.code == "PROTOCOL"
    t.join(timeout=2)
    server.close()


def test_install_script_replaces_old_copy(tmp_path):
    old = tmp_path / "Remote Scripts" / "AbletonMCP"
    old.mkdir(parents=True)
    (old / "stale.py").write_text("")
    dest = cli.install_script(tmp_path)
    assert dest == old
    assert (dest / "__init__.py").is_file() and (dest / "bridge.py").is_file()
    assert not (dest / "stale.py").exists()
    assert not list(dest.rglob("__pycache__"))
