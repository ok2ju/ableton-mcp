"""MCP stdio server: 12 tools that drive Ableton Live through the AbletonMCP Remote Script.

Every tool returns a CallToolResult. Bridge failures (LiveUnavailable, LiveTimeout, LiveError)
become isError results with the message, so the agent can read the advice and recover.
Tests replace the module global `client` with a fake.
"""

import json
from dataclasses import asdict, dataclass

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import midifile
from .live_client import LiveClient, LiveError, LiveTimeout, LiveUnavailable

LITE_TRACK_LIMIT = 8

client = LiveClient()
mcp = MCPServer("ableton-mcp")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, open_world_hint=False)


@dataclass
class Note:
    pitch: int  # 0-127; in a Drum Rack 36 = kick, 38 = snare, 42 = closed hi-hat
    start_time: float  # beats from the clip start
    duration: float  # beats
    velocity: int = 100  # 1-127


def _ok(result) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(text=json.dumps(result, ensure_ascii=False))],
        structured_content=result,
    )


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(text=message)], is_error=True)


def _call(op: str, args: dict | None = None) -> CallToolResult:
    try:
        return _ok(client.call(op, args or {}))
    except (LiveUnavailable, LiveTimeout, LiveError) as e:
        return _error(str(e))


@mcp.tool(annotations=READ_ONLY)
def live_status() -> CallToolResult:
    """Check that Live is reachable. Returns the Live version, edition ("Lite", "Standard", "Suite") and supported features. Call this first."""
    return _call("hello")


@mcp.tool(annotations=READ_ONLY)
def get_set_overview() -> CallToolResult:
    """Describe the open Set: tempo, time signature, playing state, and every track with its index, type, devices and clip slots. Indices are 0-based."""
    return _call("get_overview")


@mcp.tool(annotations=WRITES)
def create_midi_track(index: int = -1, name: str | None = None) -> CallToolResult:
    """Add a new MIDI track. index -1 puts it at the end. Live Lite allows only 8 audio+MIDI tracks, so reuse an existing empty track when you can."""
    try:
        hello = client.call("hello", {})
        overview = client.call("get_overview", {})
    except (LiveUnavailable, LiveTimeout, LiveError) as e:
        return _error(str(e))
    used = sum(1 for t in overview["tracks"] if t["type"] in ("midi", "audio"))
    if hello["variant"] == "Lite" and used >= LITE_TRACK_LIMIT:
        return _error(
            f"Lite allows {LITE_TRACK_LIMIT} tracks and this Set already has {used}; "
            "delete a track or reuse an existing one."
        )
    args = {"index": index}
    if name is not None:
        args["name"] = name
    return _call("create_midi_track", args)


@mcp.tool(annotations=WRITES)
def create_midi_clip(track: int, slot: int, length_beats: float = 4.0, name: str | None = None) -> CallToolResult:
    """Create an empty MIDI clip in an empty clip slot of a MIDI track. Length is in beats (4 beats = 1 bar in 4/4)."""
    args = {"track": track, "slot": slot, "length_beats": length_beats}
    if name is not None:
        args["name"] = name
    return _call("create_clip", args)


@mcp.tool(annotations=WRITES)
def write_notes(track: int, slot: int, notes: list[Note]) -> CallToolResult:
    """Replace ALL notes in the clip with these notes. Times and durations are in beats (1.0 = one quarter note).
    In a Drum Rack, pitch 36 is the kick, 38 the snare and 42 the closed hi-hat."""
    return _call("replace_notes", {"track": track, "slot": slot, "notes": [asdict(n) for n in notes]})


@mcp.tool(annotations=READ_ONLY)
def browse_library(root: str, path: list[str] | None = None) -> CallToolResult:
    """List one level of Live's browser. root is one of: instruments, drums, audio_effects, midi_effects,
    sounds, samples, clips, packs, user_library, plugins, max_for_live, current_project.
    path is the folder names to open, e.g. []. Navigate one level at a time: add one folder name per call."""
    return _call("browse", {"root": root, "path": path or []})


@mcp.tool(annotations=WRITES)
def load_from_library(track: int, root: str, path: list[str]) -> CallToolResult:
    """Load a browser item onto a track, e.g. a drum kit from root "drums". path must end at an item that
    browse_library marked is_loadable. Returns the names of the devices that were added."""
    return _call("load_item", {"track": track, "root": root, "path": path})


@mcp.tool(annotations=WRITES)
def set_tempo(bpm: float) -> CallToolResult:
    """Set the song tempo in BPM (20-999)."""
    return _call("set_tempo", {"bpm": bpm})


@mcp.tool(annotations=WRITES)
def play_clip(track: int, slot: int) -> CallToolResult:
    """Start playing the clip in this slot. Live starts the transport if it is stopped."""
    return _call("fire_clip", {"track": track, "slot": slot})


@mcp.tool(annotations=WRITES)
def stop() -> CallToolResult:
    """Stop playback."""
    return _call("stop")


@mcp.tool(annotations=WRITES)
def undo_last() -> CallToolResult:
    """Undo the last change in Live. Each tool call that changes the Set is one undo step."""
    return _call("undo")


@mcp.tool(annotations=WRITES)
def write_midi_file(name: str, notes: list[Note], bpm: float = 120.0) -> CallToolResult:
    """Save notes as a .mid file in ~/Music/ableton-mcp/midi/ without using Live (works even when Live is closed).
    The user can drag the file into Live. The file holds notes only, so tell the user to drop it on a track that has an instrument (e.g. a Drum Rack kit) or it plays silent. Times are in beats. name: letters, digits, space, '_' or '-'."""
    try:
        path = midifile.write_midi_file(name, [asdict(n) for n in notes], bpm, directory=midifile.MIDI_DIR)
    except ValueError as e:
        return _error(str(e))
    except OSError as e:  # e.g. no permission or disk full; without this the SDK hides the reason
        return _error(f"Could not write the MIDI file: {e}")
    return _ok({"path": str(path), "note_count": len(notes)})


def main():
    mcp.run("stdio")
