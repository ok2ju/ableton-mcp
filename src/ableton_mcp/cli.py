"""Command line: run the MCP server, install the Remote Script, check the setup (doctor), or send one raw bridge call."""

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPT_SRC = Path(__file__).resolve().parents[2] / "remote_script" / "AbletonMCP"
DEFAULT_USER_LIBRARY = Path.home() / "Music" / "Ableton" / "User Library"


def install_script(user_library: Path) -> Path:
    """Copy the Remote Script into <user_library>/Remote Scripts/AbletonMCP, replacing any old copy."""
    if not user_library.is_dir():
        raise SystemExit(f"User Library not found: {user_library} (pass --user-library)")
    if not (SCRIPT_SRC / "__init__.py").is_file():
        raise SystemExit(f"Remote Script source not found at {SCRIPT_SRC}; run from the repo checkout")
    dest = user_library / "Remote Scripts" / "AbletonMCP"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SCRIPT_SRC, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ableton-mcp")
    sub = parser.add_subparsers(dest="command")
    p_install = sub.add_parser("install-script", help="copy the Remote Script into Live's User Library")
    p_install.add_argument("--user-library", type=Path, default=DEFAULT_USER_LIBRARY)
    p_doctor = sub.add_parser("doctor", help="check Live, the Remote Script, the bridge and Claude Code registration")
    p_doctor.add_argument("--user-library", type=Path, default=None, help=f"default: {DEFAULT_USER_LIBRARY}")
    p_call = sub.add_parser("call", help="send one raw bridge request (smoke tests)")
    p_call.add_argument("op")
    p_call.add_argument("args", nargs="?", default="{}", help="JSON object")
    ns = parser.parse_args(argv)

    if ns.command is None:
        from .server import main as serve

        serve()
    elif ns.command == "install-script":
        dest = install_script(ns.user_library)
        print(f"Installed to {dest}. Restart Live, then pick AbletonMCP under Settings → Link, Tempo & MIDI → Control Surface.")
    elif ns.command == "doctor":
        from . import doctor

        return doctor.run(ns.user_library)
    elif ns.command == "call":
        from .live_client import LiveClient, LiveError, LiveTimeout, LiveUnavailable

        try:
            result = LiveClient().call(ns.op, json.loads(ns.args))
        except (LiveError, LiveTimeout, LiveUnavailable, json.JSONDecodeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

