"""MCP stdio server: 15 tools that drive Ableton Live through the AbletonMCP Remote Script.

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
PROTOCOL_VERSION = 2  # must equal remote_script/AbletonMCP/ops.py PROTOCOL_VERSION
INSTALL_HINT = "Run `uv run ableton-mcp install-script` and restart Live."

client = LiveClient()
mcp = MCPServer("ableton-mcp")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


@dataclass
class Note:
    pitch: int  # 0-127; in a Drum Rack, see drum_pads in get_set_overview for which pitch plays which pad
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
    except LiveError as e:
        if e.message.startswith("unknown op"):  # installed script is older than this server
            return _error(f"{e} The installed Remote Script is out of date. {INSTALL_HINT}")
        return _error(str(e))
    except (LiveUnavailable, LiveTimeout) as e:
        return _error(str(e))


@mcp.tool(annotations=READ_ONLY)
def live_status() -> CallToolResult:
    """Check that Live is reachable. Returns the Live version, edition ("Lite", "Standard", "Suite") and supported features. Call this first."""
    result = _call("hello")
    if result.is_error:
        return result
    installed = result.structured_content.get("protocol_version")
    if installed != PROTOCOL_VERSION:
        return _error(f"Installed Remote Script speaks protocol {installed}, this server needs {PROTOCOL_VERSION}. {INSTALL_HINT}")
    return result


@mcp.tool(annotations=READ_ONLY)
def get_set_overview() -> CallToolResult:
    """Describe the open Set: tempo, time signature, playing state, and every track with its index, type, devices and clip slots. Indices are 0-based.
    drum_pads lists {note, name} for each used pad of the track's Drum Rack (null when the track has none): use those notes as pitches in write_notes."""
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
            "use delete_track or reuse an existing track."
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


@mcp.tool(annotations=DESTRUCTIVE)
def delete_track(track: int) -> CallToolResult:
    """Delete a track with its clips and devices. One undo_last restores it. Use it to free a track under Live Lite's 8-track limit.
    Group tracks are refused. Later tracks' indices shift down by one, so call get_set_overview again before the next delete."""
    return _call("delete_track", {"track": track})


@mcp.tool(annotations=DESTRUCTIVE)
def delete_clip(track: int, slot: int) -> CallToolResult:
    """Delete the clip in this clip slot. One undo_last restores it."""
    return _call("delete_clip", {"track": track, "slot": slot})


@mcp.tool(annotations=DESTRUCTIVE)
def write_notes(track: int, slot: int, notes: list[Note]) -> CallToolResult:
    """Replace ALL notes in the clip with these notes. To edit existing notes, read_notes first and send the full changed list.
    Times and durations are in beats (1.0 = one quarter note). For a Drum Rack, take pitches from drum_pads in get_set_overview.
    The result's notes_outside_clip counts notes that start at or after the clip length; they never play (assumes the loop starts at beat 0)."""
    return _call("replace_notes", {"track": track, "slot": slot, "notes": [asdict(n) for n in notes]})


@mcp.tool(annotations=READ_ONLY)
def read_notes(track: int, slot: int) -> CallToolResult:
    """Return the clip's length and its notes, in the same shape write_notes takes, sorted by start_time then pitch.
    Mute and probability are not returned, so a read_notes + write_notes round trip resets them."""
    return _call("get_notes", {"track": track, "slot": slot})


@mcp.tool(annotations=READ_ONLY)
def browse_library(root: str, path: list[str] | None = None) -> CallToolResult:
    """List one level of Live's browser. root is one of: instruments, drums, audio_effects, midi_effects,
    sounds, samples, clips, packs, user_library, plugins, max_for_live, current_project.
    path is the folder names to open, e.g. []. Navigate one level at a time: add one folder name per call."""
    return _call("browse", {"root": root, "path": path or []})


@mcp.tool(annotations=WRITES)
def load_from_library(track: int, root: str, path: list[str]) -> CallToolResult:
    """Load a browser item onto a track, e.g. a drum kit from root "drums". path must end at an item that
    browse_library marked is_loadable. Returns the names of the devices that were added.
    Loading an instrument replaces the track's current instrument; undo_last restores it."""
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
    The user can drag the file into Live. The file holds notes only, so tell the user to drop it on a track that has an instrument (e.g. a Drum Rack kit) or it plays silent. Times are in beats. name: letters, digits, space, '_' or '-'. An existing file with that name is never overwritten: pick a new name."""
    try:
        path = midifile.write_midi_file(name, [asdict(n) for n in notes], bpm, directory=midifile.MIDI_DIR)
    except ValueError as e:
        return _error(str(e))
    except OSError as e:  # e.g. no permission or disk full; without this the SDK hides the reason
        return _error(f"Could not write the MIDI file: {e}")
    return _ok({"path": str(path), "note_count": len(notes)})


def main():
    mcp.run("stdio")
