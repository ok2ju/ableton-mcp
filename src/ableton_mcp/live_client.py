"""Client for the AbletonMCP Remote Script bridge (JSON lines over loopback TCP).

Opens a fresh connection per call and re-reads connection.json each time, so a Live
restart (new port, new token) needs no reconnect logic. Never retries a request.
"""

import json
import socket
import threading
import time
import uuid
from pathlib import Path

CONN_FILE = Path.home() / ".ableton-mcp" / "connection.json"
DEFAULT_BUDGET_S = 10.0
GRACE_S = 2.0  # client waits budget + grace, so it always sees the script's EXPIRED reply
CONTROL_SURFACE_HINT = (
    "Open Live and select AbletonMCP under Settings → Link, Tempo & MIDI → Control Surface"
)
# Here (stdlib-only) rather than in server.py, so `doctor` can use them without importing mcp.
PROTOCOL_VERSION = 7  # must equal remote_script/AbletonMCP/ops.py PROTOCOL_VERSION
INSTALL_HINT = "Run `uv run ableton-mcp install-script` and restart Live."


class LiveUnavailable(Exception):
    """Live is not running or the AbletonMCP script is not loaded."""


class LiveTimeout(Exception):
    """Live accepted the request but did not answer in time. The request may still have run."""


class LiveError(Exception):
    """The script ran the request and reported an error code."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class LiveClient:
    def __init__(self, conn_file=CONN_FILE, budget_s=DEFAULT_BUDGET_S, clock=time.time):
        self._conn_file = Path(conn_file)
        self._budget_s = budget_s
        self._clock = clock
        self._lock = threading.Lock()  # one request at a time

    def call(self, op, args=None):
        """Send one request and return its result. Raises LiveUnavailable, LiveTimeout or LiveError."""
        with self._lock:
            try:
                conn = json.loads(self._conn_file.read_text())
                port, token = int(conn["port"]), str(conn["token"])
            except (OSError, ValueError, KeyError, TypeError):
                raise LiveUnavailable(CONTROL_SURFACE_HINT) from None
            req_id = uuid.uuid4().hex
            request = {
                "id": req_id,
                "token": token,
                "op": op,
                "args": args or {},
                "deadline_ms": int((self._clock() + self._budget_s) * 1000),
            }
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=2)
            except OSError:
                raise LiveUnavailable(CONTROL_SURFACE_HINT) from None
            with sock:
                sock.settimeout(self._budget_s + GRACE_S)
                try:
                    sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
                    response = _read_line(sock)
                except socket.timeout:
                    raise LiveTimeout(
                        f"Live did not answer '{op}' within {self._budget_s + GRACE_S:.0f} s. "
                        "It may still have run: check get_set_overview before retrying. "
                        "If every call times out, check Live for an open dialog."
                    ) from None
                except OSError as e:
                    raise LiveUnavailable(f"Connection to Live lost ({e}). {CONTROL_SURFACE_HINT}") from None
        if response.get("id") != req_id:
            raise LiveError("PROTOCOL", f"response id mismatch: {response.get('id')!r}")
        if not response.get("ok"):
            err = response.get("error") or {}
            raise LiveError(err.get("code", "INTERNAL"), err.get("message", ""))
        return response.get("result")


def _read_line(sock):
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("Live closed the connection")
        buf += chunk
    try:
        response = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise LiveError("PROTOCOL", f"bad response from Live: {e}") from None
    if not isinstance(response, dict):
        raise LiveError("PROTOCOL", "response is not a JSON object")
    return response
