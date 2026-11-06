"""`ableton-mcp doctor`: check each link from the Live install to Claude Code.

Each check returns (status, message, fix). run() prints one line per check, a `fix:` line under
each FAIL or WARN, and a summary, and returns 1 if any check failed. Every default path comes from
Path.home() at call time, so tests can point HOME at a tmp dir.
Read-only: the only bridge op is `hello`. Never prints the connection token or arbitrary Log.txt
lines (the log also holds licence lines), only the marker lines it matched.
"""

import filecmp
import json
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

from .cli import SCRIPT_SRC
from .live_client import (
    CONTROL_SURFACE_HINT, INSTALL_HINT, PROTOCOL_VERSION, LiveClient, LiveError, LiveTimeout, LiveUnavailable,
)

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
REPO_ROOT = SCRIPT_SRC.parents[1]
REGISTER_CMD = f"claude mcp add ableton -- uv --directory {shlex.quote(str(REPO_ROOT))} run ableton-mcp"
LISTENING = "AbletonMCP listening on"


def check_live_app(app_dirs):
    """PASS if any `Ableton Live *.app` in app_dirs is version 12.1 or later."""
    found = []
    for app in sorted(p for d in app_dirs for p in Path(d).glob("Ableton Live *.app")):
        try:
            with open(app / "Contents" / "Info.plist", "rb") as f:
                version = plistlib.load(f).get("CFBundleShortVersionString", "")
        except (OSError, plistlib.InvalidFileException):
            continue
        m = re.match(r"(\d+)\.(\d+)", version)
        if m:
            found.append((tuple(map(int, m.groups())), app.name, version.split()[0]))
    ok = [f for f in found if f[0] >= (12, 1)]
    if ok:
        return PASS, ", ".join(f"{name} {v}" for _, name, v in ok), None
    if found:
        return FAIL, "needs Live 12.1+, found " + ", ".join(f"{name} {v}" for _, name, v in found), "Update Live to 12.1 or newer"
    return FAIL, "no Ableton Live *.app in " + " or ".join(map(str, app_dirs)), "Install Live 12.1 or newer"


def check_live_log(prefs_dir):
    """Read the last Live session in the newest Log.txt: did AbletonMCP start, and did it log tracebacks?"""
    logs = list(Path(prefs_dir).glob("Live */Log.txt"))
    if not logs:
        return FAIL, f"no Log.txt under {prefs_dir}", "Start Live once"
    log = max(logs, key=lambda p: p.stat().st_mtime)
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError as e:
        return FAIL, f"cannot read {log}: {e.strerror}", "Check the file's permissions"
    starts = [i for i, line in enumerate(lines) if "Started: Live" in line]
    first = starts[-1] if starts else 0
    session = lines[first:]
    listening = next((line for line in session if LISTENING in line), None)
    if listening is None:
        if any('Control Surface="AbletonMCP"' in line for line in session):
            return FAIL, f"AbletonMCP is selected but did not start ({log})", (
                f"Run `uv run ableton-mcp install-script`, restart Live, and read {log}"
            )
        return FAIL, f"AbletonMCP did not start in the last Live session ({log})", CONTROL_SURFACE_HINT
    message = listening[listening.index(LISTENING):]
    if any("GlobalExit(): End" in line for line in session):  # a clean quit; a crash leaves no marker
        message += " (last session, Live has quit)"
    tracebacks = [first + i + 1 for i, line in enumerate(session) if "Traceback" in line]
    if tracebacks:
        return WARN, f"{message}; {len(tracebacks)} Traceback line(s), first at {log}:{tracebacks[0]}", (
            "Read the traceback in Log.txt; re-run install-script if it points at AbletonMCP"
        )
    return PASS, message, None


def check_remote_script(dest):
    """The installed copy at dest must match the repo's remote_script/AbletonMCP byte for byte."""
    if not (dest / "__init__.py").is_file():
        return FAIL, f"not installed at {dest}", INSTALL_HINT
    if not (SCRIPT_SRC / "__init__.py").is_file():
        return FAIL, f"Remote Script source not found at {SCRIPT_SRC}", "Run doctor from the repo checkout"

    def py_files(root):
        return {p.relative_to(root) for p in root.rglob("*.py") if "__pycache__" not in p.parts}

    src, inst = py_files(SCRIPT_SRC), py_files(dest)
    problems = [f"missing {p}" for p in sorted(src - inst)]
    problems += [f"extra {p}" for p in sorted(inst - src)]
    problems += [f"differs {p}" for p in sorted(src & inst) if not filecmp.cmp(SCRIPT_SRC / p, dest / p, shallow=False)]
    if problems:
        return FAIL, f"{dest} does not match the repo: " + ", ".join(problems), INSTALL_HINT
    return PASS, f"{dest} matches the repo", None


def check_connection_file(conn_file):
    """connection.json must exist, be mode 0600 and hold a port and token. Prints the port only."""
    if not conn_file.is_file():
        return FAIL, f"{conn_file} missing: Live is not running or AbletonMCP is not selected", CONTROL_SURFACE_HINT
    mode = stat.S_IMODE(conn_file.stat().st_mode)
    if mode != 0o600:
        return FAIL, f"{conn_file} has mode {mode:o}, expected 600", f"chmod 600 '{conn_file}'"
    try:
        conn = json.loads(conn_file.read_text())
        port = int(conn["port"])
        if not isinstance(conn["token"], str) or not conn["token"]:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        return FAIL, f"{conn_file} is not valid JSON with port and token", "Restart Live; the script rewrites the file"
    return PASS, f"port {port}", None


def check_bridge(conn_file, client):
    """Send the read-only `hello` and compare its protocol with this server's."""
    try:
        port = json.loads(conn_file.read_text())["port"]
    except (OSError, ValueError, KeyError, TypeError):
        return FAIL, f"{conn_file} changed or vanished since the last check", "Run doctor again"
    try:
        hello = client.call("hello")
    except LiveUnavailable:
        return FAIL, f"stale connection.json: nothing listens on port {port} (Live quit or crashed)", (
            f"It is replaced when Live starts with AbletonMCP. {CONTROL_SURFACE_HINT}"
        )
    except LiveTimeout:
        return FAIL, "Live accepted the connection but did not answer `hello`", "Close any open dialog in Live"
    except LiveError as e:
        return FAIL, f"hello failed: {e.code}: {e.message}", "Restart Live; if it persists, read Log.txt"
    installed = hello.get("protocol_version")
    if installed != PROTOCOL_VERSION:
        return FAIL, f"Installed Remote Script speaks protocol {installed}, this server needs {PROTOCOL_VERSION}", INSTALL_HINT
    python = str(hello.get("python", "")).split(" ")[0]
    return PASS, (
        f"Live {hello.get('live_version')} {hello.get('variant')}, script {hello.get('script_version')}, "
        f"protocol {installed}, Python {python}"
    ), None


def check_claude_code():
    """`claude mcp get ableton` from the repo dir: is the server registered and does it start?"""
    if shutil.which("claude") is None:
        return WARN, "claude CLI not found; registration not checked", f"Install Claude Code, then: {REGISTER_CMD}"
    try:
        proc = subprocess.run(
            ["claude", "mcp", "get", "ableton"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        return WARN, "`claude mcp get ableton` took over 30 s", "Run `claude mcp get ableton` yourself"
    if proc.returncode != 0:
        return FAIL, "no MCP server named `ableton` for this repo", REGISTER_CMD
    if "✘" in proc.stdout:
        status = next((line.strip() for line in proc.stdout.splitlines() if "Status:" in line), "✘")
        return WARN, f"registered, but {status}", "Run `claude mcp get ableton` and read the error"
    return PASS, "registered as `ableton`", None


def run(user_library=None):
    """Run every check in order, print the report, and return the exit code (1 if any FAIL)."""
    home = Path.home()
    user_library = user_library or home / "Music" / "Ableton" / "User Library"
    conn_file = home / ".ableton-mcp" / "connection.json"
    results = [
        ("live-app", check_live_app([Path("/Applications"), home / "Applications"])),
        ("live-log", check_live_log(home / "Library" / "Preferences" / "Ableton")),
        ("remote-script", check_remote_script(user_library / "Remote Scripts" / "AbletonMCP")),
        ("connection-file", check_connection_file(conn_file)),
    ]
    if results[-1][1][0] == FAIL:
        results.append(("bridge", (SKIP, "connection-file failed", None)))
    else:
        results.append(("bridge", check_bridge(conn_file, LiveClient(conn_file, budget_s=3))))
    results.append(("claude-code", check_claude_code()))

    for name, (status, message, fix) in results:
        print(f"{status:<5} {name:<16} {message}")
        if fix and status in (FAIL, WARN):
            print(f"      fix: {fix}")
    counts = {s: sum(r[0] == s for _, r in results) for s in (PASS, WARN, FAIL, SKIP)}
    print(f"{counts[PASS]} passed, {counts[WARN]} warning(s), {counts[FAIL]} failed, {counts[SKIP]} skipped")
    return 1 if counts[FAIL] else 0
