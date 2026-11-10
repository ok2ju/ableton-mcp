"""MCP stdio server: 39 tools. 36 drive Ableton Live through the AbletonMCP Remote Script; 2 write and read
Vital synth presets as files (vital.py) and 1 lists a folder of the user's samples (samples.py); those 3 work
without Live.

Every tool returns a CallToolResult. Bridge failures (LiveUnavailable, LiveTimeout, LiveError)
become isError results with the message, so the agent can read the advice and recover.
Server instructions (sent once per session), 7 guided-lesson prompts and 2 read-only resources
(live://capabilities, live://set/overview) are registered below; their texts live in lessons.py.
Tests replace the module global `client` with a fake.
"""

import json
from dataclasses import asdict, dataclass
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import lessons, midifile, samples, theory, vital
from .live_client import INSTALL_HINT, PROTOCOL_VERSION, LiveClient, LiveError, LiveTimeout, LiveUnavailable

LITE_TRACK_LIMIT = 8
LITE_SCENE_LIMIT = 16

client = LiveClient()
mcp = MCPServer("ableton-mcp", instructions=lessons.INSTRUCTIONS)

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

# Live snaps any RGB to the nearest colour in its chooser, so names keep the result predictable.
COLORS = {
    "red": 0xFF3636, "orange": 0xF66C03, "yellow": 0xFFF034, "lime": 0xBFFB00, "green": 0x3DC300,
    "cyan": 0x00BFAF, "blue": 0x10A4EE, "purple": 0x886CE4, "pink": 0xFF39D4, "grey": 0x969696,
}
Color = Literal[tuple(COLORS)]

# Instrument kind -> its name in Live's browser (instruments root).
INSTRUMENTS = {
    "drift": "Drift", "simpler": "Simpler", "impulse": "Impulse", "drum_rack": "Drum Rack",
    "drum_sampler": "Drum Sampler", "instrument_rack": "Instrument Rack",
    "operator": "Operator", "wavetable": "Wavetable", "analog": "Analog", "meld": "Meld", "sampler": "Sampler",
}
# Kinds Live Lite lacks -> the Lite kind to use instead. Refused before calling Live (non-Lite devices block saving).
NOT_IN_LITE = {"operator": "drift", "wavetable": "drift", "analog": "drift", "meld": "drift", "sampler": "simpler"}
InstrumentKind = Literal[tuple(INSTRUMENTS)]
SampleRoot = Literal["samples", "user_library"]
# Clip and Sample warp_mode 0..6 by name; same order as remote_script ops.WARP_MODES.
WARP_MODES = ("Beats", "Tones", "Texture", "Re-Pitch", "Complex", "REX", "Complex Pro")
WarpMode = Literal[WARP_MODES]
# Simpler and Sample choices by name; same order as remote_script ops (a test checks).
PLAYBACK_MODES = ("Classic", "One-Shot", "Slicing")
SLICING_PLAYBACK_MODES = ("Mono", "Poly", "Thru")
SLICING_STYLES = ("Transient", "Beat", "Region", "Manual")
BEAT_DIVISIONS = ("1/16", "1/16T", "1/8", "1/8T", "1/4", "1/4T", "1/2", "1/2T", "1 Bar", "2 Bars", "4 Bars")


@dataclass
class Section:
    scene: int  # scene index, as in get_set_overview
    bars: int  # 1-256 bars of 4/4


@dataclass
class Modulation:
    source: str  # lfo_1-8, env_1-6, random_1-4, macro_control_1-4, velocity, note, aftertouch, mod_wheel, ...
    destination: str  # a control name from vital_create_preset, e.g. filter_1_cutoff
    amount: float  # -1..1, a share of the destination's whole range


WaveName = Literal[tuple(vital.WAVES)]
SampleName = Literal[tuple(vital.SAMPLES)]


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
    """Check that Live is reachable. Returns the Live version, edition ("Lite", "Standard", "Suite") and supported features. Call this first.
    limits gives the edition's caps: audio_midi_tracks and scenes (null = no cap)."""
    result = _call("hello")
    if result.is_error:
        return result
    hello = result.structured_content
    installed = hello.get("protocol_version")
    if installed != PROTOCOL_VERSION:
        return _error(f"Installed Remote Script speaks protocol {installed}, this server needs {PROTOCOL_VERSION}. {INSTALL_HINT}")
    lite = hello.get("variant") == "Lite"
    return _ok({**hello, "limits": {
        "audio_midi_tracks": LITE_TRACK_LIMIT if lite else None,
        "scenes": LITE_SCENE_LIMIT if lite else None,
    }})


@mcp.tool(annotations=READ_ONLY)
def get_set_overview() -> CallToolResult:
    """Describe the open Set: tempo, time signature, playing state, and every track with its index, type, devices and clip slots. Indices are 0-based.
    drum_pads lists {note, name} for each used pad of the track's Drum Rack (null when the track has none): use those notes as pitches in write_notes.
    Each track's mix is {volume_db, pan, mute, solo, sends}: dB values with null = -inf, pan -50..50 (50L..50R),
    sends keyed by return letter ("A", "B"...). scenes lists {index, name}; return_tracks lists {send, name, devices}."""
    return _call("get_overview")


def _resource_text(result: CallToolResult) -> str:
    # ResourceError keeps the message (Control Surface or install hint); any other exception loses it.
    if result.is_error:
        raise ResourceError(result.content[0].text)
    return result.content[0].text


@mcp.resource("live://capabilities", name="capabilities", mime_type="application/json",
              description="Same as the live_status tool: Live version, edition, features and limits.")
def capabilities_resource() -> str:
    return _resource_text(live_status())


@mcp.resource("live://set/overview", name="set-overview", mime_type="application/json",
              description="Same as the get_set_overview tool: tempo, key, tracks, devices, clips, scenes and mix.")
def set_overview_resource() -> str:
    return _resource_text(get_set_overview())


def _hello_and_overview() -> tuple[dict, dict]:
    return client.call("hello", {}), client.call("get_overview", {})


def _create_track(op: str, index: int, name: str | None) -> CallToolResult:
    """Refuse a ninth audio+MIDI track on Lite before calling Live, then call op."""
    try:
        hello, overview = _hello_and_overview()
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
    return _call(op, args)


@mcp.tool(annotations=WRITES)
def create_midi_track(index: int = -1, name: str | None = None) -> CallToolResult:
    """Add a new MIDI track. index -1 puts it at the end. Live Lite allows only 8 audio+MIDI tracks, so reuse an existing empty track when you can."""
    return _create_track("create_midi_track", index, name)


@mcp.tool(annotations=WRITES)
def create_audio_track(index: int = -1, name: str | None = None) -> CallToolResult:
    """Add a new audio track, for audio clips from create_audio_clip. index -1 puts it at the end.
    Live Lite allows only 8 audio+MIDI tracks, so reuse an existing empty audio track when you can."""
    return _create_track("create_audio_track", index, name)


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


@mcp.tool(annotations=WRITES)
def create_scene(index: int | None = None, name: str | None = None, copy_of: int | None = None) -> CallToolResult:
    """Add a scene (a row of clip slots across all tracks). index -1 or none puts it at the end.
    copy_of copies that scene with all its clips and puts the copy directly below it; later scenes shift down by one.
    Give index or copy_of, not both. Live Lite allows only 16 scenes, so name and reuse empty scenes when you can."""
    try:
        hello, overview = _hello_and_overview()
    except (LiveUnavailable, LiveTimeout, LiveError) as e:
        return _error(str(e))
    count = overview["scene_count"]
    if hello["variant"] == "Lite" and count >= LITE_SCENE_LIMIT:
        return _error(
            f"Lite allows {LITE_SCENE_LIMIT} scenes and this Set has {count}; "
            "name and reuse an empty scene with set_label, or undo_last."
        )
    args = {k: v for k, v in {"index": index, "name": name, "copy_of": copy_of}.items() if v is not None}
    return _call("create_scene", args)


@mcp.tool(annotations=WRITES)
def copy_clip(track: int, slot: int, to_track: int, to_slot: int) -> CallToolResult:
    """Copy the clip in track/slot to the empty slot to_track/to_slot, e.g. to reuse a loop in another scene.
    An occupied target is refused: delete_clip it first. A MIDI clip needs a MIDI target track."""
    return _call("copy_clip", {"track": track, "slot": slot, "to_track": to_track, "to_slot": to_slot})


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
def read_clip(track: int, slot: int) -> CallToolResult:
    """Read a clip's settings: name, length, is_audio, start_marker/end_marker (the part that plays), loop_start/loop_end, looping.
    Positions are in beats; on an unwarped audio clip they are in seconds. Audio clips also give file, gain (raw 0..1)
    with gain_display (Live's dB text), pitch_coarse (semitones), pitch_fine (cents), warping, warp_mode and available_warp_modes."""
    return _call("get_clip", {"track": track, "slot": slot})


@mcp.tool(annotations=WRITES)
def set_clip(
    track: int, slot: int,
    start_marker: float | None = None, end_marker: float | None = None,
    loop_start: float | None = None, loop_end: float | None = None, looping: bool | None = None,
    gain: float | None = None, pitch_coarse: int | None = None, pitch_fine: int | None = None,
    warping: bool | None = None, warp_mode: WarpMode | None = None,
) -> CallToolResult:
    """Trim, loop, pitch or warp a clip; give only what you want to change, all in one undo step.
    Positions are in beats (4 = 1 bar in 4/4) as read_clip shows them; each start must be before its end.
    To play only bar 2 of a loop: start_marker 4, end_marker 8, loop_start 4, loop_end 8, looping true.
    Audio clips only: gain raw 0..1 (check gain_display in the result), pitch_coarse -48..48 semitones,
    pitch_fine -50..49 cents, warping (follow the Set's tempo; set it alone, as it changes the markers' unit), warp_mode from available_warp_modes
    (Beats for drums, Complex for full mixes, Re-Pitch changes pitch with speed). An unwarped audio clip cannot loop,
    and loop_start/loop_end need looping true.
    If any value is invalid, nothing changes. Returns the clip as read_clip does. Reversing a clip is not possible:
    load the sample into a Simpler and use set_simpler reverse."""
    fields = {
        "start_marker": start_marker, "end_marker": end_marker, "loop_start": loop_start, "loop_end": loop_end,
        "looping": looping, "gain": gain, "pitch_coarse": pitch_coarse, "pitch_fine": pitch_fine,
        "warping": warping, "warp_mode": warp_mode,
    }
    return _call("set_clip", {"track": track, "slot": slot, **{k: v for k, v in fields.items() if v is not None}})


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
    browse_library marked is_loadable. Returns the names of the devices that were added and device_index,
    the position of the first one (use [device_index] as device_path in read_device).
    Loading an instrument replaces the track's current instrument; undo_last restores it.
    For instruments and their presets, load_instrument does this in one call."""
    return _call("load_item", {"track": track, "root": root, "path": path})


def _preset_key(name: str) -> str:
    return name.lower().removesuffix(".adv").removesuffix(".adg")


def _find_preset(root: str, start: list[str], preset: str) -> tuple[list[str] | None, list[str], bool]:
    """Search start and each folder one level below it, in browser order.
    Returns (path or None, folder names, whether any listing was cut at browse's item cap)."""
    want = _preset_key(preset)
    level = client.call("browse", {"root": root, "path": start})
    truncated = level.get("truncated", False)
    for item in level["items"]:
        if item["is_loadable"] and _preset_key(item["name"]) == want:
            return start + [item["name"]], [], truncated
    folders = [item["name"] for item in level["items"] if item["is_folder"]]
    for folder in folders:
        listing = client.call("browse", {"root": root, "path": start + [folder]})
        truncated = truncated or listing.get("truncated", False)
        for item in listing["items"]:
            if item["is_loadable"] and _preset_key(item["name"]) == want:
                return start + [folder, item["name"]], folders, truncated
    return None, folders, truncated


@mcp.tool(annotations=WRITES)
def load_instrument(track: int, kind: InstrumentKind, preset: str | None = None) -> CallToolResult:
    """Put an instrument on a MIDI track, replacing its current instrument; undo_last restores it.
    Without preset you get the bare device. preset is a preset name from the kind's browser folders, e.g. drift
    "Deep Bass" or drum_rack "808 Core Kit" (a kit); case and the .adv/.adg ending are ignored.
    Live Lite has drift, simpler, impulse, drum_rack, drum_sampler and instrument_rack only; other kinds are refused there.
    Returns the added devices, device_index (for read_device) and the browser root and path that were loaded."""
    name = INSTRUMENTS[kind]
    try:
        if client.call("hello", {})["variant"] == "Lite" and kind in NOT_IN_LITE:
            return _error(f"{name} is not in Live Lite; use kind '{NOT_IN_LITE[kind]}'.")
        root, path = "instruments", [name]
        if preset is not None:
            if kind == "drum_rack":  # kits live in the drums root; instruments/Drum Rack is empty
                root, path = "drums", []
            found, folders, truncated = _find_preset(root, path, preset)
            if found is None:
                where = "/".join([root, *path])
                cut = " Some folder listings were cut at 500 items, so the preset may be past the cut." if truncated else ""
                return _error(
                    f"No preset '{preset}' in {where} or its folders ({', '.join(folders) or 'none'}).{cut} "
                    f"Use browse_library('{root}', {path + ['<folder>']}) to see the names."
                )
            path = found
    except (LiveUnavailable, LiveTimeout, LiveError) as e:
        return _error(str(e))
    result = _call("load_item", {"track": track, "root": root, "path": path})
    if result.is_error:
        return result
    return _ok({**result.structured_content, "root": root, "path": path})


@mcp.tool(annotations=READ_ONLY)
def read_device(track: int, device_path: list[int] | None = None) -> CallToolResult:
    """Read one device's parameters: {index, name, value, min, max, display, quantized, enabled}; switches
    (quantized) also list their choices in items. display is Live's own text for the value, e.g. "1.20 kHz".
    device_path defaults to [0], the track's first device (get_set_overview lists the devices). Add
    [chain, device] to go into a rack: [0, 0, 1] is device 1 in chain 0 of rack 0. chains lists a rack's chains
    and their devices (null when the device is not a rack). enabled false means a macro or lock controls the
    parameter: change the macro instead."""
    return _call("get_device", {"track": track, "device_path": device_path or [0]})


@mcp.tool(annotations=WRITES)
def set_device_parameters(track: int, device_path: list[int], values: dict[str, float | str]) -> CallToolResult:
    """Change parameters of one device (device_path as in read_device), all in one undo step.
    values maps a parameter name from read_device (case ignored) to a raw value between its min and max,
    or, for a switch, one of its items, e.g. {"Filter Freq": 0.6, "Osc 1 Shape": "Saw"}.
    Raw values are not units: check display in the result and set again if it is off.
    If any entry is invalid, nothing changes. undo_last restores all of them."""
    return _call("set_parameters", {"track": track, "device_path": device_path, "values": values})


def _needs(feature: str, version: str) -> CallToolResult | None:
    """None if the connected Live reports feature in live_status, else the error to return. Runs before any write."""
    try:
        hello = client.call("hello", {})
    except (LiveUnavailable, LiveTimeout, LiveError) as e:
        return _error(str(e))
    if hello.get("protocol_version") != PROTOCOL_VERSION:
        return _error(f"The installed Remote Script is out of date. {INSTALL_HINT}")
    if not hello.get("features", {}).get(feature):
        return _error(f"This needs Live {version}+; this is Live {hello.get('live_version')}.")
    return None


def _sample_file(root: str | None, path: list[str] | None, file_path: str | None) -> str | CallToolResult:
    try:
        return str(samples.resolve(root, path, file_path))
    except ValueError as e:
        return _error(str(e))


@mcp.tool(annotations=READ_ONLY)
def list_samples(folder: str, limit: int = 200) -> CallToolResult:
    """List the audio files under a folder of the user's samples, recursively; works without Live.
    folder: an absolute path. Returns total (all files found) and files (sorted by path, at most limit), each with
    path, name, duration_s (WAV only, else null) and guesses from the filename: role (kick, snare, clap, hat, perc,
    bass, vocal, pad, chord, lead, fx or null), kind (loop, one_shot or unknown), bpm and key (null when the name
    does not say). Pass a file's path as file_path to create_audio_clip or replace_sample."""
    try:
        return _ok(samples.scan(folder, limit))
    except ValueError as e:
        return _error(str(e))


@mcp.tool(annotations=WRITES)
def create_audio_clip(
    track: int, slot: int, root: SampleRoot | None = None, path: list[str] | None = None,
    file_path: str | None = None, name: str | None = None,
) -> CallToolResult:
    """Put an audio sample into an empty Session clip slot of an audio track (get_set_overview type "audio").
    Name the sample in one of two ways: root ("samples" or "user_library") and path exactly as browse_library
    shows them, e.g. root "samples", path ["Atmos Calm 130 bpm.wav"]; or file_path, an absolute path to any
    .wav, .aif, .aiff, .flac, .mp3 or .ogg file.
    Live chooses whether to warp (follow the Set's tempo): the result reports warping and length_beats. Needs Live 12.4+."""
    refused = _needs("audio_clip", "12.4")
    if refused:
        return refused
    file = _sample_file(root, path, file_path)
    if isinstance(file, CallToolResult):
        return file
    args = {"track": track, "slot": slot, "file_path": file}
    if name is not None:
        args["name"] = name
    return _call("create_audio_clip", args)


@mcp.tool(annotations=DESTRUCTIVE)
def replace_sample(
    track: int, device_path: list[int] | None = None, pad_note: int | None = None,
    root: SampleRoot | None = None, path: list[str] | None = None, file_path: str | None = None,
) -> CallToolResult:
    """Swap the sample a Simpler plays. Pick the Simpler by device_path (as in read_device) or by pad_note,
    the note of a Drum Rack pad (get_set_overview drum_pads), not both. The old sample is gone; undo_last restores it.
    Name the sample as in create_audio_clip: root and path from browse_library, or an absolute file_path. Needs Live 12.4+."""
    refused = _needs("replace_sample", "12.4")
    if refused:
        return refused
    file = _sample_file(root, path, file_path)
    if isinstance(file, CallToolResult):
        return file
    return _call("replace_sample", {"track": track, "device_path": device_path, "pad_note": pad_note, "file_path": file})


@mcp.tool(annotations=DESTRUCTIVE)
def set_simpler(
    track: int, device_path: list[int] | None = None, pad_note: int | None = None,
    playback_mode: Literal[PLAYBACK_MODES] | None = None,
    slicing_style: Literal[SLICING_STYLES] | None = None,
    slicing_beat_division: Literal[BEAT_DIVISIONS] | None = None,
    slicing_sensitivity: float | None = None,
    slicing_region_count: int | None = None,
    slicing_playback_mode: Literal[SLICING_PLAYBACK_MODES] | None = None,
    warping: bool | None = None,
    warp_mode: Literal[tuple(m for m in WARP_MODES if m != "REX")] | None = None,
    reverse: bool = False,
    crop: bool = False,
) -> CallToolResult:
    """Set how a Simpler plays its sample, all in one undo step. Pick the Simpler as in replace_sample: device_path or pad_note, not both.
    playback_mode: Classic (plays the sample pitched by the key), One-Shot (plays it once, whole), Slicing (cuts it into slices, one per note).
    To chop a drum loop: playback_mode "Slicing", slicing_style "Beat", slicing_beat_division "1/8"; then write_notes from note 36 (C1)
    upward, one note per slice (36 plays slice 1, 37 slice 2, ...). slicing_style Transient cuts at hits (slicing_sensitivity 0..1: higher gives more slices),
    Region cuts into slicing_region_count equal parts. slicing_playback_mode: Mono (one slice at a time), Poly, Thru (plays on past the slice end).
    warping and warp_mode make the sample follow the Set's tempo. reverse plays the sample backwards; crop drops the audio outside the
    start and end (destructive); both write a new sample file and run after the settings. If any value is invalid, nothing changes.
    Returns the settings plus slice_count and slice_times_s (seconds from the sample start); outside Slicing mode Live reports no slices.
    One undo_last undoes the whole call, reverse and crop included."""
    fields = {
        "device_path": device_path, "pad_note": pad_note, "playback_mode": playback_mode, "slicing_style": slicing_style,
        "slicing_beat_division": slicing_beat_division, "slicing_sensitivity": slicing_sensitivity,
        "slicing_region_count": slicing_region_count, "slicing_playback_mode": slicing_playback_mode,
        "warping": warping, "warp_mode": warp_mode, "reverse": reverse or None, "crop": crop or None,
    }
    return _call("set_simpler", {"track": track, **{k: v for k, v in fields.items() if v is not None}})


@mcp.tool(annotations=WRITES)
def insert_device(track: int | Literal["main"], device_name: str, index: int = -1) -> CallToolResult:
    """Add a native Live audio or MIDI effect, e.g. "Limiter", "Channel EQ" or "Reverb", by its browser name.
    The track's instrument stays. index -1 adds it after the last device. track "main" is the Main track,
    where a Limiter keeps the whole mix from clipping. Plug-ins are refused. Needs Live 12.3+."""
    refused = _needs("insert_device", "12.3")
    if refused:
        return refused
    return _call("insert_device", {"track": track, "device_name": device_name, "index": index})


@mcp.tool(annotations=DESTRUCTIVE)
def delete_device(track: int | Literal["main"], device_path: list[int]) -> CallToolResult:
    """Remove one device (effect or instrument) from a track; one undo_last restores it.
    device_path as in read_device: [1] is the track's second device, [0, 0, 1] is device 1 in chain 0 of rack 0.
    track "main" is the Main track. Later devices' indices shift down by one, so read the result's devices list
    (or get_set_overview) before the next delete."""
    return _call("delete_device", {"track": track, "device_path": device_path})


@mcp.tool(annotations=WRITES)
def set_track_mix(
    track: int,
    volume_db: float | None = None,
    pan: float | None = None,
    mute: bool | None = None,
    solo: bool | None = None,
    sends: dict[str, float] | None = None,
) -> CallToolResult:
    """Set a track's mixer; give only what you want to change. volume_db in dB (0 is Live's default, max +6).
    pan -50..50 (-50 = 50L, 50 = 50R). sends maps a return letter to dB, e.g. {"A": -12}. To silence a track use mute.
    solo is not exclusive: Live does not unsolo other tracks, so unsolo them yourself. Returns the track's resulting mix."""
    fields = {"volume_db": volume_db, "pan": pan, "mute": mute, "solo": solo, "sends": sends}
    return _call("set_mix", {"track": track, **{k: v for k, v in fields.items() if v is not None}})


@mcp.tool(annotations=WRITES)
def set_label(
    track: int | None = None,
    slot: int | None = None,
    scene: int | None = None,
    name: str | None = None,
    color: Color | None = None,
) -> CallToolResult:
    """Rename and/or colour a track (track alone), a clip (track + slot) or a scene (scene alone).
    Live snaps the colour to the nearest one in its chooser. Colour by role, e.g. drums red, bass blue, chords green,
    and scenes by song section (intro, verse, chorus)."""
    fields = {"track": track, "slot": slot, "scene": scene, "name": name,
              "color": None if color is None else COLORS[color]}
    return _call("set_label", {k: v for k, v in fields.items() if v is not None})


@mcp.tool(annotations=WRITES)
def set_tempo(bpm: float) -> CallToolResult:
    """Set the song tempo in BPM (20-999)."""
    return _call("set_tempo", {"bpm": bpm})


@mcp.tool(annotations=WRITES)
def play_clip(track: int, slot: int) -> CallToolResult:
    """Start playing the clip in this slot. Live starts the transport if it is stopped."""
    return _call("fire_clip", {"track": track, "slot": slot})


@mcp.tool(annotations=WRITES)
def fire_scene(scene: int) -> CallToolResult:
    """Launch every clip in this scene (one song section). Live starts the transport if it is stopped. Not an undo step."""
    return _call("fire_scene", {"scene": scene})


@mcp.tool(annotations=WRITES)
def stop() -> CallToolResult:
    """Stop playback."""
    return _call("stop")


@mcp.tool(annotations=DESTRUCTIVE)
def arrange_scenes(sections: list[Section], replace: bool = False) -> CallToolResult:
    """Lay scenes out as song sections on the Arrangement (the timeline), end to end from bar 1, e.g.
    [{"scene": 0, "bars": 8}, {"scene": 1, "bars": 16}]. Each track's clip in that scene repeats to fill the section;
    a track with an empty slot is silent there. Each clip's length in beats must divide bars*4 (loop it with set_clip
    if not), and unwarped audio clips are refused. Adds a locator at each section start, named after its scene ("Section N"
    if the scene has no name), and keeps the song position. An Arrangement that already has clips or locators is refused
    unless replace=true, which deletes them all first. Returns each section's start_bar and locator (null if Live placed
    none) and the total_bars. One undo step; then
    play_arrangement to hear it. Assumes 4/4. Needs Live 12.4+."""
    refused = _needs("arrangement", "12.4")
    if refused:
        return refused
    return _call("arrange", {"sections": [asdict(s) for s in sections], "replace": replace})


@mcp.tool(annotations=WRITES)
def play_arrangement(from_bar: int = 1) -> CallToolResult:
    """Play the Arrangement (the timeline) from this bar; 1 is the start. Stops every Session clip first,
    because playing Session clips override the Arrangement. Assumes 4/4. Not an undo step."""
    refused = _needs("arrangement", "12.4")
    if refused:
        return refused
    return _call("play_arrangement", {"from_bar": from_bar})


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


@mcp.tool(annotations=WRITES)
def vital_create_preset(
    name: str,
    controls: dict[str, float],
    modulations: list[Modulation] | None = None,
    waves: list[WaveName] | None = None,
    sample: SampleName = "silence",
    open: bool = True,
) -> CallToolResult:
    """Build a Vital synth preset from scratch and save it to Vital's User presets folder (works without Live).
    controls: engine values, not 0..1. Any control left out stays at Vital's default. N = osc 1-3, filter 1-2, env 1-3, lfo 1-2.
    - osc_N_on 0/1 (only osc 1 on by default); osc_N_transpose -48..48 semitones; osc_N_tune -1..1; osc_N_level 0..1 (default 0.707); osc_N_pan -1..1
    - osc_N_unison_voices 1..16; osc_N_unison_detune 0..10 (detune % = value**2, default 4.47 = 20%); osc_N_unison_blend 0..1
    - osc_N_destination 0 filter 1, 1 filter 2, 2 both, 3 effects, 4 direct out (defaults: osc 1 -> 0, osc 2 -> 1, osc 3 -> 3)
    - filter_N_on 0/1 (off by default); filter_N_cutoff 8..136 (MIDI note: 60 = 262 Hz, 76 = 659 Hz, 96 = 2.1 kHz); filter_N_resonance 0..1; filter_N_drive 0..20 dB
    - filter_N_model 0 analog, 1 dirty, 2 ladder, 3 digital, 4 diode, 5 formant, 6 comb, 7 phaser; filter_N_keytrack -1..1 (1 = cutoff follows the played note)
    - analog/dirty/ladder/digital: filter_N_style 0 12dB, 1 24dB, 2 notch blend, 3 notch spread, 4 B/P/N; filter_N_blend 0 low-pass, 1 band-pass, 2 high-pass (diode too; diode style 0 low shelf, 1 low cut)
    - formant: filter_N_style 0 AOIE, 1 AIUO vowels; filter_N_formant_x (0 left, 1 right), filter_N_formant_y (0 bottom, 1 top) pick the vowel (default 0.5); blend unused.
      Corners (x, y): AOIE (0,0) ah, (1,0) ee, (0,1) oo, (1,1) oh; AIUO (0,0) ah, (1,0) oh, (0,1) ee, (1,1) eh. "ah" to "ee": AOIE, y 0, route an LFO to formant_x from x 0.
    - comb: filter_N_cutoff = the pitch it rings at, filter_N_resonance = feedback (0.99 rings long); filter_N_style 0 low-high comb, 1/2 low-high flange+/-, 3 band-spread comb, 4/5 band-spread flange+/-;
      filter_N_blend low-high: 0 low only, 1 both, 2 high only; band spread: 0 narrow, 2 wide; filter_N_blend_transpose 0..84 semitones above cutoff (damping filter or band centre, default 42)
    - phaser: filter_N_style 0 positive, 1 negative
    - env_N_attack / env_N_decay / env_N_release 0..2.378 (seconds = value**4: 0.5 = 0.06 s, 1 = 1 s, 1.3 = 2.9 s); env_N_sustain 0..1. env 1 is the amp envelope.
    - lfo_N_sync 0 seconds, 1 tempo (default); lfo_N_frequency -7..9 (Hz = 2**value, sync 0 only); lfo_N_tempo 0..12 (7 = 1/2, 8 = 1/4, 9 = 1/8)
    - sample_on 0/1 (needs sample="white_noise"); sample_level 0..1 (default 0.707); sample_destination 0..4 like osc (default 3 effects, so it skips the filters);
      sample_transpose -48..48; sample_keytrack, sample_loop (default 1), sample_random_phase 0/1
    - chorus_on, delay_on, reverb_on, distortion_on 0/1 (all off by default); chorus_dry_wet, delay_dry_wet, reverb_dry_wet 0..1
    - delay_feedback -1..1; delay_tempo 4..12 (8 = 1/4, 9 = 1/8); reverb_size 0..1; reverb_decay_time -6..6 (seconds = 2**value)
    - distortion_type 0..5 (soft clip, hard clip, linear fold, sine fold, bit crush, down sample); distortion_drive -30..30 dB; distortion_mix 0..1
    - macro_control_1..4 0..1; volume 0..7399 (dB = sqrt(value) - 80, default 5473 = -6 dB); polyphony 1..32
    modulations: up to 64 routes. Sources: lfo_1..8, env_1..6, random_1..4, macro_control_1..4, velocity, note, note_in_octave, aftertouch, slide, lift, mod_wheel, pitch_wheel, random, stereo.
    Destinations: any control above except switches and selectors (_on, _model, _style, _sync, _destination, distortion_type, polyphony, sample_keytrack, sample_loop, sample_random_phase). waves: one of saw, square, triangle, sine per oscillator 1-3 (missing ones are saw).
    sample: the audio the sample source plays, "silence" (default) or "white_noise" (needs sample_on 1).
    Plucked string recipe: osc_1_on 0; sample="white_noise", sample_on 1, sample_level 0, sample_destination 0; route env_2 -> sample_level amount 1 with env_2_attack 0, env_2_decay 0.5, env_2_sustain 0;
    filter_1_on 1, filter_1_model 6, filter_1_style 3, filter_1_keytrack 1, filter_1_resonance 0.99, filter_1_cutoff 60, filter_1_blend 1.
    open: start the Vital standalone with the preset if it is closed. Pass false when the user plays Vital inside Live.
    If the preset is not loaded, tell the user the how_to_load text. name: letters, digits, space, '_' or '-'; never overwrites."""
    version, data_dir = vital.config()
    try:
        preset = vital.build_preset(name, controls, [asdict(m) for m in modulations or []], waves or [], version, sample)
        path = vital.write_preset(name, preset, data_dir / "User" / "Presets")
    except ValueError as e:
        return _error(str(e))
    except OSError as e:
        return _error(f"Could not write the preset: {e}")
    loaded = open and vital.launch(path)
    if loaded:
        how = ""
    elif vital.VITAL_BINARY.exists():
        how = f"In Vital (standalone or plug-in), open the preset browser and pick User > {name}."
    else:
        how = f"Vital standalone not found. In the Vital plug-in, open the preset browser and pick User > {name}."
    return _ok({"path": str(path), "preset_name": name, "loaded": loaded, "how_to_load": how})


@mcp.tool(annotations=READ_ONLY)
def vital_read_preset(name_or_path: str) -> CallToolResult:
    """Read a Vital preset: its controls (only those vital_create_preset knows), modulation routes, wavetable names and sample name.
    name_or_path: a preset name in Vital's User presets folder, or a full path to any .vital file (e.g. a factory preset).
    Use it to start from an existing sound ("make this one darker"): copy the values into vital_create_preset under a new name."""
    _, data_dir = vital.config()
    try:
        return _ok(vital.read_preset(name_or_path, data_dir / "User" / "Presets"))
    except ValueError as e:
        return _error(str(e))
    except OSError as e:
        return _error(f"Could not read the preset: {e}")



# Guided lessons as MCP prompts, registered in Learning Music's chapter order (list_prompts keeps it).
# Claude Code shows them as /ableton:<name>. No arguments: the lesson asks the user instead.
@mcp.prompt(name="lesson-first-beat", title="Lesson 1 of 6: your first beat")
def lesson_first_beat() -> str:
    """Build a one-bar drum loop step by step: kick, snare, hi-hats and tempo."""
    return lessons.FIRST_BEAT


@mcp.prompt(name="lesson-notes-and-scales", title="Lesson 2 of 6: notes and scales")
def lesson_notes_and_scales() -> str:
    """Hear what a scale is, compare major and minor, and pick a key for the song."""
    return lessons.NOTES_AND_SCALES


@mcp.prompt(name="lesson-chords", title="Lesson 3 of 6: chords")
def lesson_chords() -> str:
    """Build a four-chord progression in the song's key."""
    return lessons.CHORDS


@mcp.prompt(name="lesson-bassline", title="Lesson 4 of 6: a bassline")
def lesson_bassline() -> str:
    """Write a bassline that follows the chords and locks with the drums."""
    return lessons.BASSLINE


@mcp.prompt(name="lesson-melody", title="Lesson 5 of 6: a melody")
def lesson_melody() -> str:
    """Write a short melody in the key that fits over the chords."""
    return lessons.MELODY


@mcp.prompt(name="lesson-song-structure", title="Lesson 6 of 6: song structure")
def lesson_song_structure() -> str:
    """Turn the loops into song sections (scenes) and play them in order."""
    return lessons.SONG_STRUCTURE


@mcp.prompt(name="explain-my-set", title="Explain my Set")
def explain_my_set() -> str:
    """Describe the open Set in plain words (tempo, key, each track's role) without changing it."""
    return lessons.EXPLAIN_MY_SET


def main():
    mcp.run("stdio")
