"""MCP stdio server: 19 tools that drive Ableton Live through the AbletonMCP Remote Script.

Every tool returns a CallToolResult. Bridge failures (LiveUnavailable, LiveTimeout, LiveError)
become isError results with the message, so the agent can read the advice and recover.
Tests replace the module global `client` with a fake.
"""

import json
from dataclasses import asdict, dataclass
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import midifile, theory
from .live_client import LiveClient, LiveError, LiveTimeout, LiveUnavailable

LITE_TRACK_LIMIT = 8
PROTOCOL_VERSION = 3  # must equal remote_script/AbletonMCP/ops.py PROTOCOL_VERSION
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


# Literal over the theory tables' keys, so the tool schema lists the exact choices.
ScaleName = Literal[tuple(theory.SCALES)]
DrumStyle = Literal[tuple(theory.DRUM_STYLES)]
DrumVoice = Literal[tuple(theory.DRUM_VOICES)]
BassPattern = Literal[tuple(theory.BASS_PATTERNS)]


@dataclass
class DrumLayer:
    voice: DrumVoice
    pulses: int  # hits spread as evenly as possible over steps (a Euclidean rhythm)
    steps: int = 16  # pattern length in sixteenth notes; it repeats over the clip
    rotation: int = 0  # shift the pattern left by this many steps


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


@mcp.tool(annotations=WRITES)
def set_scale(root: str, scale: ScaleName) -> CallToolResult:
    """Set the Set's key, e.g. root "A", scale "Minor", and turn on Scale Mode so Live's MIDI editor highlights the scale's notes.
    create_bassline and create_chord_progression use this key when you leave root and scale out."""
    try:
        root_note = theory.parse_root(root)
    except ValueError as e:
        return _error(str(e))
    return _call("set_scale", {"root_note": root_note, "scale_name": scale})


def _live_target(track: int | None, slot: int | None, file_name: str | None):
    """Check there is exactly one destination. For Live, read the Set and return (overview, track info);
    for a file, return (None, None). Refusals raise ValueError."""
    if file_name is not None:
        if track is not None or slot is not None:
            raise ValueError("Give either track and slot (write into Live) or file_name (write a .mid file), not both.")
        return None, None
    if track is None or slot is None:
        raise ValueError("Give track and slot to write into Live, or file_name to write a .mid file.")
    overview = client.call("get_overview", {})
    signature = (overview["signature_numerator"], overview["signature_denominator"])
    if signature != (4, 4):
        raise ValueError(f"Generators support 4/4 only; this Set is in {signature[0]}/{signature[1]}.")
    if not 0 <= track < len(overview["tracks"]):
        raise ValueError(f"No track {track}; the Set has {len(overview['tracks'])} tracks.")
    return overview, overview["tracks"][track]


def _key(root: str | None, scale: str | None, overview: dict | None) -> tuple[str, str]:
    """Missing root or scale comes from the Set's scale."""
    if root is not None and scale is not None:
        return root, scale
    if overview is None:
        raise ValueError("Without Live, give both root and scale.")
    if overview.get("scale") is None:
        raise ValueError("This Live version reports no Set scale; give both root and scale.")
    current = overview["scale"]
    if scale is None and current["name"] not in theory.SCALES:
        raise ValueError(f"The Set's scale {current['name']!r} is not supported; pass scale (one of: {', '.join(theory.SCALES)}).")
    return (root if root is not None else theory.NOTE_NAMES[current["root_note"]],
            scale if scale is not None else current["name"])


def _write_generated(notes, length, name, summary, track, slot, file_name) -> CallToolResult:
    if file_name is not None:
        try:
            path = midifile.write_midi_file(file_name, notes, 120, directory=midifile.MIDI_DIR)
        except ValueError as e:
            return _error(str(e))
        except OSError as e:
            return _error(f"Could not write the MIDI file: {e}")
        return _ok({"path": str(path), "note_count": len(notes), **summary})
    result = _call("write_clip", {"track": track, "slot": slot, "length_beats": length, "name": name, "notes": notes})
    if result.is_error:
        return result
    return _ok({**result.structured_content, **summary})


@mcp.tool(annotations=DESTRUCTIVE)
def create_drum_pattern(
    style: DrumStyle | None = "house",
    bars: int = 1,
    layers: list[DrumLayer] | None = None,
    track: int | None = None,
    slot: int | None = None,
    file_name: str | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Write a drum beat in a style, repeated over bars. The track must hold a Drum Rack kit; hits go to the pads
    that match each voice (kick, snare, hats...), and voices the kit lacks are listed in missing_voices.
    layers add Euclidean rhythms, e.g. {voice: "perc", pulses: 3, steps: 8}; a layer replaces the style's part for
    that voice. style null with layers gives layers only. The result's about explains the style for a beginner.
    Destination: track + slot writes a clip into Live and replaces any clip in that slot (one undo_last restores it);
    file_name instead writes a 120 BPM .mid file to ~/Music/ableton-mcp/midi/ and works with Live closed. Never creates tracks. 4/4 only."""
    try:
        overview, info = _live_target(track, slot, file_name)
        if info is not None and info.get("drum_pads") is None:
            raise ValueError(
                f"Track {track} has no Drum Rack. Load a kit first with load_from_library(track={track}, root=\"drums\", ...)."
            )
        pitches = theory.pick_drum_pitches(info["drum_pads"] if info else None)
        notes, length, summary = theory.drum_notes(style, bars, [asdict(x) for x in layers or []], pitches)
        if not notes:  # never replace a clip with an empty one
            raise ValueError(f"The kit has no pad for any voice this pattern uses: {', '.join(summary['missing_voices'])}.")
    except (ValueError, LiveUnavailable, LiveTimeout, LiveError) as e:
        return _error(str(e))
    return _write_generated(notes, length, name or style or "drums", summary, track, slot, file_name)


@mcp.tool(annotations=DESTRUCTIVE)
def create_bassline(
    pattern: BassPattern = "eighths",
    degrees: list[int] | None = None,
    root: str | None = None,
    scale: ScaleName | None = None,
    bars: int | None = None,
    octave: int = 1,
    track: int | None = None,
    slot: int | None = None,
    file_name: str | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Write a bassline in a key. degrees are scale degrees, one per bar, cycled (default [1]; e.g. [1, 6, 4, 5]).
    bars defaults to len(degrees). root and scale default to the Set's scale (see set_scale); without Live, give both.
    octave 1 puts A at MIDI 45. The track needs an instrument (e.g. a bass synth), or the clip plays silent.
    Destination: track + slot writes a clip into Live and replaces any clip in that slot (one undo_last restores it);
    file_name instead writes a 120 BPM .mid file to ~/Music/ableton-mcp/midi/ and works with Live closed. Never creates tracks. 4/4 only."""
    try:
        overview, _ = _live_target(track, slot, file_name)
        root, scale = _key(root, scale, overview)
        notes, length, summary = theory.bass_notes(root, scale, pattern, [1] if degrees is None else degrees, bars, octave)
    except (ValueError, LiveUnavailable, LiveTimeout, LiveError) as e:
        return _error(str(e))
    return _write_generated(notes, length, name or "bass", summary, track, slot, file_name)


@mcp.tool(annotations=DESTRUCTIVE)
def create_chord_progression(
    degrees: list[int],
    root: str | None = None,
    scale: ScaleName | None = None,
    beats_per_chord: float = 4.0,
    sevenths: bool = False,
    octave: int = 3,
    track: int | None = None,
    slot: int | None = None,
    file_name: str | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Write held root-position chords built from scale degrees, e.g. [1, 6, 4, 5] in C Major gives C Am F G.
    The result's chords names each chord. Needs a 7-note scale (not the pentatonics or Minor Blues).
    root and scale default to the Set's scale (see set_scale); without Live, give both. The track needs an instrument.
    Destination: track + slot writes a clip into Live and replaces any clip in that slot (one undo_last restores it);
    file_name instead writes a 120 BPM .mid file to ~/Music/ableton-mcp/midi/ and works with Live closed. Never creates tracks. 4/4 only."""
    try:
        overview, _ = _live_target(track, slot, file_name)
        root, scale = _key(root, scale, overview)
        notes, length, summary = theory.chord_notes(root, scale, degrees, beats_per_chord, sevenths, octave)
    except (ValueError, LiveUnavailable, LiveTimeout, LiveError) as e:
        return _error(str(e))
    return _write_generated(notes, length, name or " ".join(summary["chords"]), summary, track, slot, file_name)


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
