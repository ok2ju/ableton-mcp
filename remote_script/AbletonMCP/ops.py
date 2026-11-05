"""One function per bridge op, each (song, app, args) -> JSON-able result.

`Live` is imported inside functions so tests can inject a fake module via sys.modules.
"""

import sys

from .bridge import OpError

SCRIPT_VERSION = "0.1.0"
PROTOCOL_VERSION = 1


def hello(song, app, args):
    import Live

    version = f"{app.get_major_version()}.{app.get_minor_version()}.{app.get_bugfix_version()}"
    return {
        "script_version": SCRIPT_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "live_version": version,
        "variant": app.get_variant() if hasattr(app, "get_variant") else None,
        "python": sys.version,
        "features": {
            "insert_device": hasattr(Live.Track.Track, "insert_device"),
            "replace_sample": hasattr(Live.SimplerDevice.SimplerDevice, "replace_sample"),
            "arrangement_create_midi_clip": hasattr(Live.Track.Track, "create_midi_clip"),
        },
    }


BROWSER_ROOTS = (
    "instruments", "drums", "audio_effects", "midi_effects", "sounds", "samples", "clips",
    "packs", "user_library", "plugins", "max_for_live", "current_project",
)
MAX_BROWSE_ITEMS = 500


def _index(args, key):
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OpError("INVALID_ARG", f"{key} must be a non-negative integer")
    return value


def _track(song, args):
    i = _index(args, "track")
    tracks = list(song.tracks)
    if i >= len(tracks):
        raise OpError("NOT_FOUND", f"no track {i}; the Set has {len(tracks)} tracks")
    return tracks[i]


def _slot(song, args):
    track = _track(song, args)
    i = _index(args, "slot")
    slots = list(track.clip_slots)
    if i >= len(slots):
        raise OpError("NOT_FOUND", f"no clip slot {i}; the track has {len(slots)}")
    return slots[i]


def _midi_clip(song, args):
    slot = _slot(song, args)
    if not slot.has_clip:
        raise OpError("NOT_FOUND", "that clip slot is empty")
    if not slot.clip.is_midi_clip:
        raise OpError("LIVE_STATE", "that clip is not a MIDI clip")
    return slot.clip


def _track_type(track):
    if track.is_foldable:
        return "group"
    return "midi" if track.has_midi_input else "audio"


def get_overview(song, app, args):
    scale = None
    if hasattr(song, "scale_name"):
        scale = {"root_note": song.root_note, "name": song.scale_name}
    tracks = []
    for i, t in enumerate(song.tracks):
        slots = []
        for j, cs in enumerate(t.clip_slots):
            clip = cs.clip if cs.has_clip else None
            slots.append({
                "slot": j,
                "has_clip": clip is not None,
                "name": clip.name if clip else None,
                "length": clip.length if clip else None,
            })
        tracks.append({
            "index": i,
            "name": t.name,
            "type": _track_type(t),
            "devices": [d.name for d in t.devices],
            "clip_slots": slots,
        })
    return {
        "tempo": song.tempo,
        "signature_numerator": song.signature_numerator,
        "signature_denominator": song.signature_denominator,
        "is_playing": song.is_playing,
        "scale": scale,
        "tracks": tracks,
        "scene_count": len(song.scenes),
    }


def create_midi_track(song, app, args):
    index = args.get("index", -1)
    count = len(song.tracks)
    if isinstance(index, bool) or not isinstance(index, int) or not -1 <= index <= count:
        raise OpError("INVALID_ARG", f"index must be -1 (end) or 0..{count}")
    song.create_midi_track(index)
    new_index = len(song.tracks) - 1 if index == -1 else index
    track = song.tracks[new_index]
    if args.get("name"):
        track.name = str(args["name"])
    return {"index": new_index, "name": track.name}


def create_clip(song, app, args):
    track = _track(song, args)
    if not track.has_midi_input:
        raise OpError("LIVE_STATE", "clips with notes need a MIDI track")
    slot = _slot(song, args)
    if slot.has_clip:
        raise OpError("LIVE_STATE", "that clip slot already has a clip")
    length = float(args.get("length_beats", 4.0))
    if not 0 < length <= 1024:
        raise OpError("INVALID_ARG", "length_beats must be in (0, 1024]")
    slot.create_clip(length)
    clip = slot.clip
    if args.get("name"):
        clip.name = str(args["name"])
    return {"track": args["track"], "slot": args["slot"], "name": clip.name, "length": clip.length}


def replace_notes(song, app, args):
    import Live

    clip = _midi_clip(song, args)
    specs = []
    for n in args["notes"]:
        pitch, velocity = int(n["pitch"]), float(n.get("velocity", 100))
        start, duration = float(n["start_time"]), float(n["duration"])
        if not 0 <= pitch <= 127 or not 1 <= velocity <= 127 or start < 0 or duration <= 0:
            raise OpError("INVALID_ARG", f"invalid note: {n}")
        specs.append(Live.Clip.MidiNoteSpecification(
            pitch=pitch, start_time=start, duration=duration, velocity=velocity, mute=False
        ))
    # Remove by id: covers notes outside the loop range too.
    old_ids = [note.note_id for note in clip.get_all_notes_extended()]
    if old_ids:
        clip.remove_notes_by_id(old_ids)
    if specs:
        clip.add_new_notes(tuple(specs))
    return {"track": args["track"], "slot": args["slot"], "note_count": len(specs)}


def set_tempo(song, app, args):
    bpm = float(args["bpm"])
    if not 20 <= bpm <= 999:
        raise OpError("INVALID_ARG", "bpm must be between 20 and 999")
    song.tempo = bpm
    return {"tempo": song.tempo}


def fire_clip(song, app, args):
    slot = _slot(song, args)
    if not slot.has_clip:
        raise OpError("NOT_FOUND", "that clip slot is empty")
    slot.fire()
    return {"track": args["track"], "slot": args["slot"]}


def stop(song, app, args):
    song.stop_playing()
    return {"is_playing": False}


def undo(song, app, args):
    if not song.can_undo:
        raise OpError("LIVE_STATE", "nothing to undo")
    song.undo()
    return {"undone": True}


def _browser_item(app, args):
    root = args["root"]
    if root not in BROWSER_ROOTS:
        raise OpError("INVALID_ARG", f"root must be one of {', '.join(BROWSER_ROOTS)}")
    path = args.get("path") or []
    if not isinstance(path, list) or not all(isinstance(p, str) for p in path):
        raise OpError("INVALID_ARG", "path must be a list of names")
    item = getattr(app.browser, root)
    for name in path:
        match = next((c for c in item.children if c.name == name), None)
        if match is None:
            raise OpError("NOT_FOUND", f"'{name}' not found under {root}/{'/'.join(path)}")
        item = match
    return root, path, item


def browse(song, app, args):
    """List one level of a browser folder. Never walks recursively (large trees stall Live's UI)."""
    root, path, item = _browser_item(app, args)
    items, truncated = [], False
    for child in item.children:
        if len(items) == MAX_BROWSE_ITEMS:
            truncated = True
            break
        items.append({"name": child.name, "is_folder": child.is_folder, "is_loadable": child.is_loadable})
    return {"root": root, "path": path, "items": items, "truncated": truncated}


def _device_key(device):
    # Live hands out fresh Python wrappers; _live_ptr identifies the underlying object.
    return getattr(device, "_live_ptr", id(device))


def load_item(song, app, args):
    track = _track(song, args)
    root, path, item = _browser_item(app, args)
    if not item.is_loadable:
        raise OpError("INVALID_ARG", f"'{item.name}' is a folder or not loadable; browse into it")
    before = {_device_key(d) for d in track.devices}
    song.view.selected_track = track
    app.browser.load_item(item)
    added = [d.name for d in track.devices if _device_key(d) not in before]
    return {"track": args["track"], "item": item.name, "devices_added": added}


# name -> (function, mutating). Mutating ops run inside one undo step.
# Transport ops (fire_clip, stop) and undo itself are not undoable, so not wrapped.
OPS = {
    "hello": (hello, False),
    "get_overview": (get_overview, False),
    "create_midi_track": (create_midi_track, True),
    "create_clip": (create_clip, True),
    "replace_notes": (replace_notes, True),
    "set_tempo": (set_tempo, True),
    "fire_clip": (fire_clip, False),
    "stop": (stop, False),
    "undo": (undo, False),
    "browse": (browse, False),
    "load_item": (load_item, True),
}
