"""Loopback JSON-lines bridge, polled from Live's main thread. Stdlib only, no Live import.

Request:  {"id", "token", "op", "args", "deadline_ms"} + "\n"
Response: {"id", "ok": true, "result"} or {"id", "ok": false, "error": {"code", "message"}} + "\n"
"""

import hmac
import json
import math
import os
import secrets
import socket
import time
from pathlib import Path

CONN_FILE = Path.home() / ".ableton-mcp" / "connection.json"
MAX_LINE = 1024 * 1024
MAX_LINES_PER_TICK = 64  # bounds main-thread time per tick; the rest waits for the next tick
MAX_OUTBUF = 8 * 1024 * 1024  # a peer that never reads gets dropped
RECV_SIZE = 65536


class OpError(Exception):
    """An op failure with a protocol error code (NOT_FOUND, LIVE_STATE, INVALID_ARG, ...)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Bridge:
    def __init__(self, get_song, get_app, ops, log=print, conn_file=CONN_FILE, clock=time.time):
        """ops: {name: (fn(song, app, args), mutating: bool)}."""
        self._get_song = get_song
        self._get_app = get_app
        self._ops = ops
        self._log = log
        self._conn_file = Path(conn_file)
        self._clock = clock
        self._token = secrets.token_hex(16)
        self._server = None
        self._client = None
        self._inbuf = b""
        self._outbuf = b""

    def start(self):
        """Listen on an ephemeral loopback port, publish {port, token}, return the port."""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(4)
        self._server.setblocking(False)
        port = self._server.getsockname()[1]
        self._conn_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self._conn_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"port": port, "token": self._token}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._conn_file)
        return port

    def close(self):
        self._drop_client()
        if self._server:
            self._server.close()
            self._server = None
        # Delete the file only if it is ours: another instance may have replaced it.
        try:
            if json.loads(self._conn_file.read_text()).get("token") == self._token:
                self._conn_file.unlink()
        except (OSError, ValueError, AttributeError):
            pass

    def tick(self):
        """Accept, read, run every complete request, flush. Never blocks."""
        if not self._server:
            return
        self._accept()
        if not self._client:
            return
        self._flush()
        try:
            while len(self._inbuf) <= MAX_LINE:  # stop reading once a backlog builds up
                chunk = self._client.recv(RECV_SIZE)
                if not chunk:  # peer closed
                    self._drop_client()
                    return
                self._inbuf += chunk
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            self._drop_client()
            return
        lines = self._inbuf.split(b"\n")  # one split: linear in buffer size
        tail = lines.pop()  # incomplete last line (b"" if the buffer ended with \n)
        if len(tail) > MAX_LINE or any(len(line) > MAX_LINE for line in lines):
            self._log("AbletonMCP: request line over 1 MB, closing connection")
            self._drop_client()
            return
        now, rest = lines[:MAX_LINES_PER_TICK], lines[MAX_LINES_PER_TICK:]
        self._inbuf = b"".join(line + b"\n" for line in rest) + tail
        for line in now:
            if line.strip():
                self._send(self.handle_line(line))
            if len(self._outbuf) > MAX_OUTBUF:
                self._log("AbletonMCP: client is not reading replies, closing connection")
                self._drop_client()
                return
        self._flush()

    def handle_line(self, line):
        """Run one request line and return the response dict. Public for tests."""
        req_id = None
        try:
            req = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
            if not isinstance(req, dict):
                raise OpError("INVALID_ARG", "request must be a JSON object")
            req_id = req.get("id")
            if not hmac.compare_digest(str(req.get("token", "")).encode("utf-8"), self._token.encode("utf-8")):
                raise OpError("AUTH", "bad token")
            deadline = req.get("deadline_ms")
            if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
                raise OpError("INVALID_ARG", "deadline_ms is required")
            if self._clock() * 1000 > deadline:
                raise OpError("EXPIRED", "request arrived after its deadline; nothing was run")
            op = req.get("op")
            if op not in self._ops:
                raise OpError("INVALID_ARG", f"unknown op: {op}")
            args = req.get("args") or {}
            if not isinstance(args, dict):
                raise OpError("INVALID_ARG", "args must be an object")
            fn, mutating = self._ops[op]
            song, app = self._get_song(), self._get_app()
            if mutating:
                song.begin_undo_step()
                try:
                    result = fn(song, app, args)
                finally:
                    song.end_undo_step()
            else:
                result = fn(song, app, args)
            return {"id": req_id, "ok": True, "result": result}
        except OpError as e:
            return _error(req_id, e.code, str(e))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return _error(req_id, "INVALID_ARG", f"bad request: {e}")
        except (ValueError, TypeError, KeyError) as e:
            # Logged too: Live's own ArgumentError is a TypeError, not always the caller's fault.
            self._log(f"AbletonMCP: invalid argument: {type(e).__name__}: {e}")
            return _error(req_id, "INVALID_ARG", f"{type(e).__name__}: {e}")
        except Exception as e:  # anything Live raises, e.g. RuntimeError on bad state
            self._log(f"AbletonMCP: op failed: {type(e).__name__}: {e}")
            return _error(req_id, "INTERNAL", f"{type(e).__name__}: {e}")

    def _accept(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except (BlockingIOError, InterruptedError):
                return
            self._drop_client()  # a new client replaces the old one
            conn.setblocking(False)
            self._client = conn

    def _send(self, response):
        try:
            text = json.dumps(response, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as e:  # an op returned something JSON cannot carry
            self._log(f"AbletonMCP: unserialisable result: {e}")
            text = json.dumps(_error(response.get("id"), "INTERNAL", f"unserialisable result: {e}"))
        self._outbuf += (text + "\n").encode("utf-8")

    def _flush(self):
        while self._outbuf and self._client:
            try:
                sent = self._client.send(self._outbuf)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                self._drop_client()
                return
            self._outbuf = self._outbuf[sent:]

    def _drop_client(self):
        if self._client:
            self._client.close()
            self._client = None
        self._inbuf = b""
        self._outbuf = b""


def _reject_constant(name):
    raise ValueError(f"{name} is not allowed")


def _error(req_id, code, message):
    return {"id": req_id, "ok": False, "error": {"code": code, "message": message}}
