"""One function per bridge op, each (song, app, args) -> JSON-able result.

`Live` is imported inside functions so tests can inject a fake module via sys.modules.
"""

import sys

from .bridge import OpError

SCRIPT_VERSION = "0.4.0"
PROTOCOL_VERSION = 4


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


def _track(song, args, key="track"):
    i = _index(args, key)
    tracks = list(song.tracks)
    if i >= len(tracks):
        raise OpError("NOT_FOUND", f"no track {i}; the Set has {len(tracks)} tracks")
    return tracks[i]


def _slot(song, args, track_key="track", slot_key="slot"):
    track = _track(song, args, track_key)
    i = _index(args, slot_key)
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


def _scene(song, args, key="scene"):
    i = _index(args, key)
    scenes = list(song.scenes)
    if i >= len(scenes):
        raise OpError("NOT_FOUND", f"no scene {i}; the Set has {len(scenes)} scenes")
    return scenes[i]


SEND_LETTERS = "ABCDEFGHIJKL"  # Live names return tracks A, B, ... in order


def _db(param, value):
    """dB as Live displays it for this value, e.g. "-6.0 dB" -> -6.0, "-inf dB" -> -inf.
    Uses Live's own str_for_value, so no fader curve is hard-coded."""
    text = param.str_for_value(value)
    try:
        return float(str(text).replace("\u2212", "-").replace("dB", "").strip())
    except ValueError:
        raise RuntimeError(f"could not read dB from {text!r}")


def _round_db(db):
    return None if db == float("-inf") else round(db, 1)  # null: the bridge refuses non-finite JSON


def _max_db(param):
    return _db(param, param.max)


def _lowest_value(param, above):
    """Lowest value whose displayed dB passes above(db), by bisection (assumes the curve rises with value)."""
    lo, hi = param.min, param.max
    for _ in range(40):
        mid = (lo + hi) / 2
        if above(_db(param, mid)):
            hi = mid
        else:
            lo = mid
    return hi


def _set_db(param, db):
    """Set the value Live displays as db. Live rounds the display to 0.1 dB, so aim at the middle
    of the values that display as db, not at its edge."""
    start = _lowest_value(param, lambda d: d >= db)
    end = _lowest_value(param, lambda d: d > db)
    param.value = (start + end) / 2


def _pan(param):
    return round((param.value - param.min) / (param.max - param.min) * 100 - 50)


def _mix(song, track):
    m = track.mixer_device
    return {
        "volume_db": _round_db(_db(m.volume, m.volume.value)),
        "pan": _pan(m.panning),
        "mute": bool(track.mute),
        "solo": bool(track.solo),
        "sends": {SEND_LETTERS[i]: _round_db(_db(p, p.value)) for i, p in enumerate(m.sends)},
    }


def _track_type(track):
    if track.is_foldable:
        return "group"
    return "midi" if track.has_midi_input else "audio"


def _find_drum_rack(devices, depth):
    """First Drum Rack among devices; then, depth levels down, inside other racks' chains."""
    for d in devices:
        if getattr(d, "can_have_drum_pads", False):
            return d
    if depth > 0:
        for d in devices:
            for chain in getattr(d, "chains", ()) if getattr(d, "can_have_chains", False) else ():
                rack = _find_drum_rack(chain.devices, depth - 1)
                if rack is not None:
                    return rack
    return None


def _drum_pads(track):
    """Pads with chains in the track's Drum Rack, or None if it has none.
    Searches top level, then one rack deep: 58 of 141 Lite kits wrap their Drum Rack in an Instrument Rack."""
    rack = _find_drum_rack(track.devices, depth=1)
    if rack is None:
        return None
    return [{"note": p.note, "name": p.name} for p in rack.drum_pads if p.chains]


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
            "drum_pads": _drum_pads(t),
            "mix": _mix(song, t),
            "clip_slots": slots,
        })
    return {
        "tempo": song.tempo,
        "signature_numerator": song.signature_numerator,
        "signature_denominator": song.signature_denominator,
        "is_playing": song.is_playing,
        "scale": scale,
        "tracks": tracks,
        "scenes": [{"index": i, "name": sc.name} for i, sc in enumerate(song.scenes)],
        "scene_count": len(song.scenes),
        "return_tracks": [
            {"send": SEND_LETTERS[i], "name": r.name, "devices": [d.name for d in r.devices]}
            for i, r in enumerate(song.return_tracks)
        ],
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


def delete_track(song, app, args):
    track = _track(song, args)
    if len(song.tracks) == 1:
        raise OpError("LIVE_STATE", "Live cannot delete the only track in the Set")
    if track.is_foldable:  # Live would delete every track inside the group too
        raise OpError("LIVE_STATE", "that is a group track; delete the tracks inside it one by one, or ungroup it in Live")
    name = track.name
    song.delete_track(args["track"])
    return {"deleted": name, "track_count": len(song.tracks)}


def delete_clip(song, app, args):
    slot = _slot(song, args)
    if not slot.has_clip:
        raise OpError("NOT_FOUND", "that clip slot is empty")
    slot.delete_clip()
    return {"track": args["track"], "slot": args["slot"]}


def get_notes(song, app, args):
    clip = _midi_clip(song, args)
    notes = sorted(clip.get_all_notes_extended(), key=lambda n: (n.start_time, n.pitch))
    return {
        "track": args["track"],
        "slot": args["slot"],
        "length": clip.length,
        "notes": [
            # velocity as int: write_notes takes ints, so the result round-trips unchanged
            {"pitch": n.pitch, "start_time": n.start_time, "duration": n.duration, "velocity": int(round(n.velocity))}
            for n in notes
        ],
    }


def _note_specs(notes, clip_length):
    """Validate notes and build Live note specs. Returns (specs, count of notes starting past clip_length)."""
    import Live

    specs, outside = [], 0
    for n in notes:
        pitch, velocity = int(n["pitch"]), float(n.get("velocity", 100))
        start, duration = float(n["start_time"]), float(n["duration"])
        if not 0 <= pitch <= 127 or not 1 <= velocity <= 127 or start < 0 or duration <= 0:
            raise OpError("INVALID_ARG", f"invalid note: {n}")
        outside += start >= clip_length  # these never sound
        specs.append(Live.Clip.MidiNoteSpecification(
            pitch=pitch, start_time=start, duration=duration, velocity=velocity, mute=False
        ))
    return specs, outside


def replace_notes(song, app, args):
    clip = _midi_clip(song, args)
    specs, outside = _note_specs(args["notes"], clip.length)
    # Remove by id: covers notes outside the loop range too.
    old_ids = [note.note_id for note in clip.get_all_notes_extended()]
    if old_ids:
        clip.remove_notes_by_id(old_ids)
    if specs:
        clip.add_new_notes(tuple(specs))
    return {"track": args["track"], "slot": args["slot"], "note_count": len(specs), "notes_outside_clip": outside}


def write_clip(song, app, args):
    """Create a MIDI clip with notes in one step, replacing a MIDI clip already in the slot."""
    track = _track(song, args)
    if not track.has_midi_input:
        raise OpError("LIVE_STATE", "clips with notes need a MIDI track")
    slot = _slot(song, args)
    if slot.has_clip and not slot.clip.is_midi_clip:
        raise OpError("LIVE_STATE", "that slot holds an audio clip")
    length = float(args["length_beats"])
    if not 0 < length <= 1024:
        raise OpError("INVALID_ARG", "length_beats must be in (0, 1024]")
    specs, outside = _note_specs(args["notes"], length)  # before deleting: a bad note leaves the slot as it was
    replaced = slot.has_clip
    if replaced:
        slot.delete_clip()
    slot.create_clip(length)
    clip = slot.clip
    if args.get("name"):
        clip.name = str(args["name"])
    if specs:
        clip.add_new_notes(tuple(specs))
    return {
        "track": args["track"], "slot": args["slot"], "name": clip.name, "length": clip.length,
        "note_count": len(specs), "notes_outside_clip": outside, "replaced": replaced,
    }


def set_scale(song, app, args):
    root, name = args["root_note"], args["scale_name"]
    if isinstance(root, bool) or not isinstance(root, int) or not 0 <= root <= 11:
        raise OpError("INVALID_ARG", "root_note must be an integer 0..11")
    if not isinstance(name, str) or not name:
        raise OpError("INVALID_ARG", "scale_name must be a non-empty string")
    if not all(hasattr(song, a) for a in ("scale_name", "scale_mode", "scale_intervals")):
        raise OpError("LIVE_STATE", "this Live version has no Set scale API")
    song.scale_name = name
    if song.scale_name != name:  # Live ignores names it does not know
        raise OpError("INVALID_ARG", f"Live did not accept scale name {name!r}")
    song.root_note = root
    song.scale_mode = True
    return {
        "root_note": song.root_note, "name": song.scale_name,
        "intervals": list(song.scale_intervals), "scale_mode": song.scale_mode,
    }


def _number(args, key):
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpError("INVALID_ARG", f"{key} must be a number")
    return float(value)


def _check_db(param, db, label):
    top = _max_db(param)
    if db > top:
        raise OpError("INVALID_ARG", f"{label} max is {top:+.1f} dB")


def set_mix(song, app, args):
    track = _track(song, args)
    m = track.mixer_device
    if not any(k in args for k in ("volume_db", "pan", "mute", "solo", "sends")):
        raise OpError("INVALID_ARG", "give at least one of volume_db, pan, mute, solo, sends")
    # Validate everything first, so a bad field changes nothing.
    volume = pan = None
    if "volume_db" in args:
        volume = _number(args, "volume_db")
        _check_db(m.volume, volume, "volume")
    if "pan" in args:
        pan = _number(args, "pan")
        if not -50 <= pan <= 50:
            raise OpError("INVALID_ARG", "pan must be -50 (50L) .. 50 (50R)")
    for key in ("mute", "solo"):
        if key in args and not isinstance(args[key], bool):
            raise OpError("INVALID_ARG", f"{key} must be true or false")
    sends = args.get("sends", {})
    if not isinstance(sends, dict) or ("sends" in args and not sends):
        raise OpError("INVALID_ARG", "sends must be an object like {\"A\": -12}")
    params = dict(zip(SEND_LETTERS, m.sends))
    for letter in sends:
        if letter not in params:
            raise OpError("INVALID_ARG", f"no send {letter!r}; this Set has sends {', '.join(params) or 'none'}")
        _check_db(params[letter], _number(sends, letter), f"send {letter}")
    if volume is not None:
        _set_db(m.volume, volume)
    if pan is not None:
        p = m.panning
        p.value = p.min + (pan + 50) / 100 * (p.max - p.min)
    if "mute" in args:
        track.mute = args["mute"]
    if "solo" in args:  # Live bypasses exclusive solo here: other tracks stay soloed
        track.solo = args["solo"]
    for letter, db in sends.items():
        _set_db(params[letter], float(db))
    return {"track": args["track"], **_mix(song, track)}


def create_scene(song, app, args):
    count = len(song.scenes)
    if "copy_of" in args:
        if "index" in args:
            raise OpError("INVALID_ARG", "give index or copy_of, not both: the copy always goes directly below")
        _scene(song, args, "copy_of")
        song.duplicate_scene(args["copy_of"])
        new_index = args["copy_of"] + 1
    else:
        index = args.get("index", -1)
        if isinstance(index, bool) or not isinstance(index, int) or not -1 <= index <= count:
            raise OpError("INVALID_ARG", f"index must be -1 (end) or 0..{count}")
        song.create_scene(index)
        new_index = count if index == -1 else index
    scene = song.scenes[new_index]
    if args.get("name"):
        scene.name = str(args["name"])
    return {"scene": new_index, "name": scene.name, "scene_count": len(song.scenes)}


def fire_scene(song, app, args):
    scene = _scene(song, args)
    scene.fire()
    return {"scene": args["scene"], "name": scene.name}


def copy_clip(song, app, args):
    src = _slot(song, args)
    if not src.has_clip:
        raise OpError("NOT_FOUND", "the source clip slot is empty")
    dst_track = _track(song, args, "to_track")
    if dst_track.is_foldable:
        raise OpError("LIVE_STATE", "group tracks hold no clips")
    dst = _slot(song, args, "to_track", "to_slot")
    if dst.has_clip:
        raise OpError("LIVE_STATE", "the target clip slot has a clip; delete_clip it first or pick an empty slot")
    if src.clip.is_midi_clip != bool(dst_track.has_midi_input):
        kind = "MIDI" if src.clip.is_midi_clip else "audio"
        raise OpError("LIVE_STATE", f"a {kind} clip needs a {kind} target track")
    src.duplicate_clip_to(dst)
    return {"track": args["to_track"], "slot": args["to_slot"], "name": dst.clip.name, "length": dst.clip.length}


def set_label(song, app, args):
    has = {k for k in ("track", "slot", "scene") if args.get(k) is not None}
    if has == {"track"}:
        target, obj = "track", _track(song, args)
    elif has == {"track", "slot"}:
        slot = _slot(song, args)
        if not slot.has_clip:
            raise OpError("NOT_FOUND", "that clip slot is empty")
        target, obj = "clip", slot.clip
    elif has == {"scene"}:
        target, obj = "scene", _scene(song, args)
    else:
        raise OpError("INVALID_ARG", "give track (a track), track and slot (a clip), or scene (a scene)")
    name, color = args.get("name"), args.get("color")
    if name is None and color is None:
        raise OpError("INVALID_ARG", "give name, color or both")
    if color is not None and (isinstance(color, bool) or not isinstance(color, int) or not 0 <= color <= 0xFFFFFF):
        raise OpError("INVALID_ARG", "color must be an RGB integer 0..0xFFFFFF")
    if name is not None:
        obj.name = str(name)
    if color is not None:
        obj.color = color  # Live snaps it to the nearest colour in its chooser
    return {"target": target, "name": obj.name}


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
# Transport ops (fire_clip, fire_scene, stop) and undo itself are not undoable, so not wrapped.
OPS = {
    "hello": (hello, False),
    "get_overview": (get_overview, False),
    "create_midi_track": (create_midi_track, True),
    "create_clip": (create_clip, True),
    "delete_track": (delete_track, True),
    "delete_clip": (delete_clip, True),
    "create_scene": (create_scene, True),
    "copy_clip": (copy_clip, True),
    "get_notes": (get_notes, False),
    "replace_notes": (replace_notes, True),
    "write_clip": (write_clip, True),
    "set_scale": (set_scale, True),
    "set_tempo": (set_tempo, True),
    "fire_clip": (fire_clip, False),
    "fire_scene": (fire_scene, False),
    "stop": (stop, False),
    "undo": (undo, False),
    "browse": (browse, False),
    "load_item": (load_item, True),
    "set_mix": (set_mix, True),
    "set_label": (set_label, True),
}
