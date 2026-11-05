import ast
import json
import socket
import stat
import sys
import time
from pathlib import Path

import pytest

from AbletonMCP.bridge import Bridge, OpError
from AbletonMCP.ops import OPS
from fake_live import Application, Song, install_fake_live_module

REMOTE_SCRIPT = Path(__file__).resolve().parents[1] / "remote_script"


def _set_tempo(song, app, args):
    song.tempo = float(args["bpm"])
    return {"tempo": song.tempo}


def _boom(song, app, args):
    song.tempo = 1.0
    raise OpError("LIVE_STATE", "nope")


TEST_OPS = {**OPS, "set_tempo": (_set_tempo, True), "boom": (_boom, True)}


@pytest.fixture
def env(tmp_path):
    install_fake_live_module()
    song, app = Song(), Application()
    bridge = Bridge(lambda: song, lambda: app, TEST_OPS, log=lambda m: None, conn_file=tmp_path / "c" / "connection.json")
    port = bridge.start()
    sock = socket.create_connection(("127.0.0.1", port))
    sock.settimeout(1)
    end = time.time() + 2
    while bridge._client is None and time.time() < end:
        bridge.tick()  # accept
    yield bridge, song, sock, json.loads((tmp_path / "c" / "connection.json").read_text())
    sock.close()
    bridge.close()


def req(token, op, args=None, rid=1, deadline=None):
    deadline = time.time() * 1000 + 5000 if deadline is None else deadline
    return {"id": rid, "token": token, "op": op, "args": args or {}, "deadline_ms": deadline}


def line(obj):
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def read_responses(bridge, sock, n):
    buf, end = b"", time.time() + 2
    while buf.count(b"\n") < n and time.time() < end:
        bridge.tick()
        try:
            sock.settimeout(0.02)
            buf += sock.recv(65536)
        except socket.timeout:
            pass
    return [json.loads(x) for x in buf.splitlines()]


def test_two_requests_in_one_chunk(env):
    bridge, song, sock, conn = env
    sock.sendall(line(req(conn["token"], "hello", rid=1)) + line(req(conn["token"], "hello", rid=2)))
    out = read_responses(bridge, sock, 2)
    assert [r["id"] for r in out] == [1, 2]
    assert all(r["ok"] for r in out)
    assert out[0]["result"]["live_version"] == "12.1.10"


def test_utf8_split_across_chunks(env):
    bridge, song, sock, conn = env
    data = line(req(conn["token"], "hello", rid="ключ-é"))
    cut = data.index("ю".encode()) + 1  # inside a 2-byte character
    sock.sendall(data[:cut])
    bridge.tick()
    sock.sendall(data[cut:])
    out = read_responses(bridge, sock, 1)
    assert out[0]["id"] == "ключ-é" and out[0]["ok"]


def test_oversize_line_closes_connection(env):
    bridge, song, sock, conn = env
    payload = memoryview(b"x" * (1024 * 1024 + 10))
    sock.setblocking(False)
    end, closed = time.time() + 5, False
    while time.time() < end and not closed:
        try:
            if payload:
                payload = payload[sock.send(payload) :]
        except BlockingIOError:
            pass
        except OSError:
            closed = True
            break
        bridge.tick()
        try:
            closed = sock.recv(10) == b""
        except BlockingIOError:
            pass
        except OSError:
            closed = True
    assert closed


def test_bad_token_returns_auth_and_op_not_run(env):
    bridge, song, sock, conn = env
    sock.sendall(line(req("wrong", "set_tempo", {"bpm": 90})))
    (out,) = read_responses(bridge, sock, 1)
    assert out["error"]["code"] == "AUTH"
    assert song.tempo == 120.0 and song.undo_log == []


def test_expired_deadline_runs_nothing(env):
    bridge, song, sock, conn = env
    sock.sendall(line(req(conn["token"], "set_tempo", {"bpm": 90}, deadline=time.time() * 1000 - 1)))
    (out,) = read_responses(bridge, sock, 1)
    assert out["error"]["code"] == "EXPIRED"
    assert song.tempo == 120.0 and song.undo_log == []


def test_mutating_op_one_undo_pair(env):
    bridge, song, sock, conn = env
    sock.sendall(line(req(conn["token"], "set_tempo", {"bpm": 90})))
    (out,) = read_responses(bridge, sock, 1)
    assert out["ok"] and song.tempo == 90.0
    assert song.undo_log == ["begin", "end"]


def test_mutating_op_that_raises_still_ends_undo(env):
    bridge, song, sock, conn = env
    sock.sendall(line(req(conn["token"], "boom")))
    (out,) = read_responses(bridge, sock, 1)
    assert out["error"]["code"] == "LIVE_STATE"
    assert song.undo_log == ["begin", "end"]


def test_bad_args_map_to_invalid_arg(env):
    bridge, song, sock, conn = env
    sock.sendall(line(req(conn["token"], "set_tempo", {})) + b"not json\n")
    out = read_responses(bridge, sock, 2)
    assert [r["error"]["code"] for r in out] == ["INVALID_ARG", "INVALID_ARG"]


def test_flood_of_short_lines_stays_bounded(env):
    bridge, song, sock, conn = env
    sock.sendall(b"1\n" * 80000)  # 160 KB of tokenless junk
    time.sleep(0.05)
    slowest = 0.0
    for _ in range(20):
        start = time.perf_counter()
        bridge.tick()
        slowest = max(slowest, time.perf_counter() - start)
        assert len(bridge._outbuf) <= 8 * 1024 * 1024
    assert slowest < 0.5


def test_nan_deadline_rejected(env):
    bridge, song, sock, conn = env
    raw = json.dumps(req(conn["token"], "set_tempo", {"bpm": 90})).replace('"deadline_ms": ', '"deadline_ms": NaN, "x": ')
    sock.sendall(raw.encode() + b"\n")
    (out,) = read_responses(bridge, sock, 1)
    assert out["error"]["code"] == "INVALID_ARG" and song.tempo == 120.0


def test_non_ascii_token_is_auth(env):
    bridge, song, sock, conn = env
    sock.sendall(line(req("é", "hello")))
    (out,) = read_responses(bridge, sock, 1)
    assert out["error"]["code"] == "AUTH"


def test_new_client_replaces_old(env):
    bridge, song, sock, conn = env
    port = json.loads(bridge._conn_file.read_text())["port"]
    second = socket.create_connection(("127.0.0.1", port))
    second.settimeout(1)
    try:
        second.sendall(line(req(conn["token"], "hello", rid=7)))
        (out,) = read_responses(bridge, second, 1)  # ticks until accepted and answered
        assert out["id"] == 7 and out["ok"]
        sock.settimeout(1)
        assert sock.recv(10) == b""  # old client was closed
    finally:
        second.close()


def test_unserialisable_result_is_internal(env):
    bridge, song, sock, conn = env
    bridge._ops = {**bridge._ops, "weird": (lambda s, a, args: {"x": object()}, False)}
    sock.sendall(line(req(conn["token"], "weird")))
    (out,) = read_responses(bridge, sock, 1)
    assert out["error"]["code"] == "INTERNAL"


def test_close_keeps_file_of_other_instance(tmp_path):
    path = tmp_path / "connection.json"
    b = Bridge(Song, Application, OPS, conn_file=path)
    b.start()
    path.write_text(json.dumps({"port": 1, "token": "someone-else"}))
    b.close()
    assert path.exists()


def test_connection_file_mode_0600(env, tmp_path):
    path = tmp_path / "c" / "connection.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_close_removes_connection_file(tmp_path):
    path = tmp_path / "connection.json"
    b = Bridge(Song, Application, OPS, conn_file=path)
    b.start()
    b.close()
    assert not path.exists()


def test_stdlib_only():
    allowed = set(sys.stdlib_module_names) | {"_Framework", "Live"}
    bad = []
    for py in REMOTE_SCRIPT.rglob("*.py"):
        # feature_version: Live 12 embeds Python 3.11, so reject newer syntax.
        for node in ast.walk(ast.parse(py.read_text(), feature_version=(3, 11))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            bad += [f"{py.name}: {n}" for n in names if n.split(".")[0] not in allowed]
    assert bad == []


def test_delete_track_one_undo_pair(env):
    bridge, song, sock, conn = env
    song.create_midi_track(-1)
    song.create_midi_track(-1)
    sock.sendall(line(req(conn["token"], "delete_track", {"track": 1})))
    (out,) = read_responses(bridge, sock, 1)
    assert out["ok"] and len(song.tracks) == 1
    assert song.undo_log == ["begin", "end"]
