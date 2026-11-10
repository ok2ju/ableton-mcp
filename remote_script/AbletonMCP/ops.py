"""One function per bridge op, each (song, app, args) -> JSON-able result.

`Live` is imported inside functions so tests can inject a fake module via sys.modules.
"""

import difflib
import os
import sys

from .bridge import OpError

SCRIPT_VERSION = "0.7.0"
PROTOCOL_VERSION = 9


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
            "audio_clip": hasattr(Live.ClipSlot.ClipSlot, "create_audio_clip"),
            "arrangement": hasattr(Live.Track.Track, "duplicate_clip_to_arrangement"),
            "clip_envelopes": hasattr(Live.Clip.Clip, "create_automation_envelope"),
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


def _db_value(param, db):
    """The raw value Live displays as db. Live rounds the display to 0.1 dB, so aim at the middle
    of the values that display as db, not at its edge."""
    start = _lowest_value(param, lambda d: d >= db)
    end = _lowest_value(param, lambda d: d > db)
    return (start + end) / 2


def _set_db(param, db):
    param.value = _db_value(param, db)


def _pan_value(param, pan):
    """Raw value for pan -50 (50L) .. 50 (50R)."""
    return param.min + (pan + 50) / 100 * (param.max - param.min)


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


def _audio_file(args):
    # The server resolved and checked this path; check again because Live runs in another process.
    path = args["file_path"]
    if not isinstance(path, str) or not os.path.isabs(path) or not os.path.isfile(path):
        raise OpError("INVALID_ARG", f"no audio file at {path!r}; give an absolute path to an existing file")
    return path


def create_audio_clip(song, app, args):
    """Put an audio file into an empty Session clip slot of an audio track."""
    track = _track(song, args)
    if track.is_foldable:
        raise OpError("LIVE_STATE", "group tracks hold no clips")
    if track.has_midi_input:
        raise OpError("LIVE_STATE", "audio clips need an audio track")
    slot = _slot(song, args)
    if slot.has_clip:
        raise OpError("LIVE_STATE", "that clip slot has a clip; delete_clip it first or pick an empty slot")
    path = _audio_file(args)
    try:
        slot.create_audio_clip(path)
    except Exception as e:  # one Live call; its exception type is unverified
        raise OpError("INVALID_ARG", f"Live could not load {path}: {e}")
    clip = slot.clip
    if args.get("name"):
        clip.name = str(args["name"])
    return {
        "track": args["track"], "slot": args["slot"], "name": clip.name, "file": clip.file_path,
        "length_beats": clip.length, "warping": bool(clip.warping),
    }


def _new_track(song, args, create):
    index = args.get("index", -1)
    count = len(song.tracks)
    if isinstance(index, bool) or not isinstance(index, int) or not -1 <= index <= count:
        raise OpError("INVALID_ARG", f"index must be -1 (end) or 0..{count}")
    create(index)
    new_index = len(song.tracks) - 1 if index == -1 else index
    track = song.tracks[new_index]
    if args.get("name"):
        track.name = str(args["name"])
    return {"index": new_index, "name": track.name}


def create_midi_track(song, app, args):
    return _new_track(song, args, song.create_midi_track)


def create_audio_track(song, app, args):
    return _new_track(song, args, song.create_audio_track)


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


WARP_MODES = ("Beats", "Tones", "Texture", "Re-Pitch", "Complex", "REX", "Complex Pro")  # Clip.warp_mode 0..6
AUDIO_CLIP_FIELDS = ("gain", "pitch_coarse", "pitch_fine", "warping", "warp_mode")


def _clip(song, args):
    slot = _slot(song, args)
    if not slot.has_clip:
        raise OpError("NOT_FOUND", "that clip slot is empty")
    return slot.clip


def _named(names, i):
    return names[i] if 0 <= i < len(names) else i  # a value newer than the table stays a number


def _warp_name(mode):
    return _named(WARP_MODES, mode)


def _clip_info(args, clip):
    """Markers and loop are in beats; on an unwarped audio clip they are in seconds."""
    info = {
        "track": args["track"], "slot": args["slot"], "name": clip.name, "length": clip.length,
        "is_audio": not clip.is_midi_clip,
        "start_marker": clip.start_marker, "end_marker": clip.end_marker,
        "loop_start": clip.loop_start, "loop_end": clip.loop_end, "looping": bool(clip.looping),
    }
    if not clip.is_midi_clip:
        info.update({
            "file": clip.file_path, "gain": clip.gain, "gain_display": clip.gain_display_string,
            "pitch_coarse": clip.pitch_coarse, "pitch_fine": clip.pitch_fine, "warping": bool(clip.warping),
            "warp_mode": _warp_name(clip.warp_mode),
            "available_warp_modes": [_warp_name(m) for m in clip.available_warp_modes],
        })
    return info


def get_clip(song, app, args):
    return _clip_info(args, _clip(song, args))


def _whole(args, key, lo, hi):
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise OpError("INVALID_ARG", f"{key} must be a whole number {lo}..{hi}")
    return value


def _flag(args, key):
    if not isinstance(args[key], bool):
        raise OpError("INVALID_ARG", f"{key} must be true or false")
    return args[key]


def _set_range(clip, start_key, end_key, start, end):
    """Set a checked start < end pair. Live refuses a start at or past the end, so move the end first when moving right."""
    if start < getattr(clip, end_key):
        setattr(clip, start_key, start)
        setattr(clip, end_key, end)
    else:
        setattr(clip, end_key, end)
        setattr(clip, start_key, start)


def set_clip(song, app, args):
    """Change a clip's markers, loop and, on audio clips, gain, pitch and warping. Validates all before setting any."""
    clip = _clip(song, args)
    keys = ("start_marker", "end_marker", "loop_start", "loop_end", "looping") + AUDIO_CLIP_FIELDS
    given = [k for k in keys if args.get(k) is not None]
    if not given:
        raise OpError("INVALID_ARG", f"give at least one of {', '.join(keys)}")
    audio_only = [k for k in given if k in AUDIO_CLIP_FIELDS]
    if clip.is_midi_clip and audio_only:
        raise OpError("INVALID_ARG", f"{', '.join(audio_only)} work on audio clips only; this is a MIDI clip")
    values = {k: _number(args, k) for k in ("start_marker", "end_marker", "loop_start", "loop_end", "gain") if k in given}
    for k in ("looping", "warping"):
        if k in given:
            values[k] = _flag(args, k)
    if "pitch_coarse" in given:
        values["pitch_coarse"] = _whole(args, "pitch_coarse", -48, 48)
    if "pitch_fine" in given:
        values["pitch_fine"] = _whole(args, "pitch_fine", -50, 49)
    if "gain" in values and not 0 <= values["gain"] <= 1:
        raise OpError("INVALID_ARG", "gain is Live's raw 0..1 (read_clip gain_display shows dB)")
    if "warp_mode" in given:
        name = args["warp_mode"]
        available = [_warp_name(m) for m in clip.available_warp_modes]
        if name not in available:
            raise OpError("INVALID_ARG", f"warp_mode must be one of {', '.join(map(str, available))} for this clip")
        values["warp_mode"] = list(clip.available_warp_modes)[available.index(name)]
    marker_keys = [k for k in ("start_marker", "end_marker", "loop_start", "loop_end") if k in values]
    if "warping" in values and marker_keys:  # warping switches markers between seconds and beats, and Live applies it later
        raise OpError("INVALID_ARG", "set warping in its own set_clip call, then read_clip and set the markers")
    warping = values.get("warping", bool(getattr(clip, "warping", True)))
    if values.get("looping") and not clip.is_midi_clip and not warping:
        raise OpError("INVALID_ARG", "Live cannot loop an unwarped audio clip; set warping true first")
    if not values.get("looping", bool(clip.looping)) and ("loop_start" in values or "loop_end" in values):
        # On an unlooped clip Live treats loop_start/loop_end as the clip's start and end.
        raise OpError("INVALID_ARG", "loop_start and loop_end need looping true; use start_marker and end_marker to trim")
    ranges = []
    for start_key, end_key in (("start_marker", "end_marker"), ("loop_start", "loop_end")):
        if start_key in values or end_key in values:
            start, end = values.get(start_key, getattr(clip, start_key)), values.get(end_key, getattr(clip, end_key))
            if not start < end:
                raise OpError("INVALID_ARG", f"{start_key} ({start}) must be before {end_key} ({end})")
            ranges.append((start_key, end_key, start, end))
    for k in ("warping", "warp_mode", "gain", "pitch_coarse", "pitch_fine"):
        if k in values:
            setattr(clip, k, values[k])
    if values.get("looping"):  # loop on before writing the loop range, off after it
        clip.looping = True
    for r in ranges:
        _set_range(clip, *r)
    if values.get("looping") is False:
        clip.looping = False
    return _clip_info(args, clip)


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
        m.panning.value = _pan_value(m.panning, pan)
    if "mute" in args:
        track.mute = args["mute"]
    if "solo" in args:  # Live bypasses exclusive solo here: other tracks stay soloed
        track.solo = args["solo"]
    for letter, db in sends.items():
        _set_db(params[letter], float(db))
    return {"track": args["track"], **_mix(song, track)}


ENVELOPE_STEP = 0.25  # beats: a 1/16 note


def _envelope_target(song, args):
    """(param, unit) for a clip envelope target: "volume" and "send A".. in dB, "pan" in -50..50,
    and with device_path a device parameter by name in raw units."""
    if args.get("device_path") is not None:
        return _param_by_name(_device(song, args), args["parameter"])[1], "raw"
    track, name = _track(song, args), args["parameter"]
    m = track.mixer_device
    if name == "volume":
        return m.volume, "db"
    if name == "pan":
        return m.panning, "pan"
    sends = dict(zip(SEND_LETTERS, m.sends))
    letter = name[5:] if isinstance(name, str) and name.startswith("send ") else None
    if letter in sends:
        return sends[letter], "db"
    if letter is not None:
        raise OpError("INVALID_ARG", f"no send {letter!r}; this Set has sends {', '.join(sends) or 'none'}")
    raise OpError("INVALID_ARG", 'parameter must be "volume", "pan" or "send A", "send B", ...')


def set_envelope(song, app, args):
    slot = _slot(song, args)
    if not slot.has_clip:
        raise OpError("NOT_FOUND", "that clip slot is empty")
    clip = slot.clip
    param, unit = _envelope_target(song, args)
    points = args["points"]
    if not isinstance(points, list):
        raise OpError("INVALID_ARG", "points must be a list of {time, value}")
    # Validate every point first, so a bad one writes nothing.
    times, values = [], []
    for i, point in enumerate(points):
        if not isinstance(point, dict):
            raise OpError("INVALID_ARG", f"point {i} must be an object {{time, value}}")
        time = _number(point, "time")
        if unit == "raw":  # the device parameter's own min..max, or an item name for a quantized one
            if "value" not in point:
                raise OpError("INVALID_ARG", f"point {i} needs a value")
            try:
                value = _param_value(param, point["value"])
            except OpError as e:
                raise OpError(e.code, f"point {i}: {e}")
        else:
            value = _number(point, "value")
        if not 0 <= time <= clip.length:
            raise OpError("INVALID_ARG", f"point {i} time {time:g} is outside the clip (0..{clip.length:g} beats)")
        if times and time <= times[-1]:
            raise OpError("INVALID_ARG", f"point {i} time {time:g} must be later than the point before it")
        if unit == "pan" and not -50 <= value <= 50:
            raise OpError("INVALID_ARG", f"point {i} pan must be -50 (50L) .. 50 (50R)")
        if unit == "db":
            _check_db(param, value, f"point {i} {args['parameter']}")
        times.append(time)
        values.append(value)
    if clip.automation_envelope(param) is not None:
        clip.clear_envelope(param)  # replace, not merge
    if not points:
        return {"track": args["track"], "slot": args["slot"], "parameter": args["parameter"], "steps": 0}
    envelope = clip.automation_envelope(param) or clip.create_automation_envelope(param)
    raw = {}  # display value rounded to 0.1 -> raw; the dB search is 80 str_for_value calls

    def step(start, length, value):
        if unit == "raw":
            envelope.insert_step(start, length, value)
            return
        key = round(value, 1)
        if key not in raw:
            raw[key] = _db_value(param, key) if unit == "db" else _pan_value(param, key)
        envelope.insert_step(start, length, raw[key])

    # Ramp each pair in 1/16 steps, interpolated in display (or raw) units; each step takes the value at its start.
    # A quantized device parameter does not interpolate: it holds each point's value until the next point.
    quantized = unit == "raw" and param.is_quantized
    count = 0
    for t0, v0, t1, v1 in zip(times, values, times[1:], values[1:]):
        k = 0
        while t0 + k * ENVELOPE_STEP < t1:
            start = t0 + k * ENVELOPE_STEP
            value = v0 if quantized else v0 + (v1 - v0) * (start - t0) / (t1 - t0)
            step(start, min(ENVELOPE_STEP, t1 - start), value)
            count += 1
            k += 1
    if times[-1] < clip.length:  # hold the last value to the clip end
        step(times[-1], clip.length - times[-1], values[-1])
        count += 1
    return {"track": args["track"], "slot": args["slot"], "parameter": args["parameter"], "steps": count}


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


MAX_BAR = 394_200  # Live's last Arrangement position, 1576800 beats, in 4/4 bars


def play_arrangement(song, app, args):
    bar = _whole(args, "from_bar", 1, MAX_BAR)
    song.stop_all_clips()  # playing Session clips would override the Arrangement
    song.back_to_arranger = False
    song.current_song_time = (bar - 1) * 4.0  # 4/4, as the generators assume
    song.start_playing()
    return {"from_bar": bar, "song_time": song.current_song_time, "is_playing": True}


MAX_SECTION_BARS = 256


def arrange(song, app, args):
    """Lay scenes end to end on the Arrangement from beat 0. Each track's clip in a section's scene is copied
    and repeated to fill the section; an empty slot is silent there. Validates all before writing any. Assumes 4/4."""
    sections = args["sections"]
    if not isinstance(sections, list) or not sections:
        raise OpError("INVALID_ARG", "sections must be a non-empty list of {scene, bars}")
    replace = args.get("replace", False)
    if not isinstance(replace, bool):
        raise OpError("INVALID_ARG", "replace must be true or false")
    tracks = [(t, track) for t, track in enumerate(song.tracks) if not track.is_foldable]  # group tracks hold no clips
    placements, result, start = [], [], 0.0  # placements: (track, clip, first beat, copies)
    for section in sections:
        scene = _scene(song, section)
        i = section["scene"]
        bars = _whole(section, "bars", 1, MAX_SECTION_BARS)
        beats = bars * 4.0
        for t, track in tracks:
            slot = list(track.clip_slots)[i]
            if not slot.has_clip:
                continue
            clip = slot.clip
            if not clip.is_midi_clip and not clip.warping:
                raise OpError("LIVE_STATE", f"track {t}, scene {i}: the audio clip is unwarped, so its length is in "
                                            "seconds; set_clip warping true first")
            copies = beats / clip.length
            if copies != int(copies):
                raise OpError("INVALID_ARG", f"track {t}, scene {i}: the {clip.length:g}-beat clip does not fill "
                                             f"{bars} bars ({beats:g} beats) evenly; loop it to a length that divides "
                                             "it with set_clip, or change bars")
            placements.append((track, clip, start, int(copies)))
        result.append({"scene": i, "name": scene.name, "start_bar": int(start) // 4 + 1, "bars": bars})
        start += beats
    if start > MAX_BAR * 4:
        raise OpError("INVALID_ARG", f"the sections add up to {int(start) // 4} bars; Live's Arrangement ends at bar {MAX_BAR}")
    existing = [(track, clip) for _, track in tracks for clip in track.arrangement_clips]
    cues = list(song.cue_points)
    if (existing or cues) and not replace:
        raise OpError("LIVE_STATE", f"the Arrangement already has {len(existing)} clips and {len(cues)} locators; "
                                    "pass replace=true to delete them first")
    for track, clip in existing:
        track.delete_clip(clip)
    for track, clip, first, copies in placements:
        for n in range(copies):
            track.duplicate_clip_to_arrangement(clip, first + n * clip.length)
    # Live toggles a cue at the playback position, so move it there first and put it back after.
    saved_time = song.current_song_time
    for cue in cues:
        _toggle_cue(song, cue.time)
    for n, section in enumerate(result):
        cue = _toggle_cue(song, (section["start_bar"] - 1) * 4.0)
        if cue is not None:
            cue.name = section["name"] or f"Section {n + 1}"
        section["locator"] = cue.name if cue is not None else None  # None: Live put no locator at the section start
    song.current_song_time = saved_time
    return {"sections": result, "total_bars": int(start) // 4}


def _toggle_cue(song, time):
    """Toggle the cue point at beat `time`; returns the cue now there, or None."""
    song.current_song_time = time
    song.set_or_delete_cue()
    return next((c for c in song.cue_points if c.time == time), None)


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
    devices = list(track.devices)
    keys = [_device_key(d) for d in devices]
    added = [(i, d.name) for i, (k, d) in enumerate(zip(keys, devices)) if k not in before]
    selected = getattr(track.view, "selected_device", None)
    if not added and selected is not None and _device_key(selected) in keys:
        # Replacing an instrument on real Live adds no new _live_ptr (seen on 12.1.10 Lite), so the diff is empty.
        # Live selects the device it just loaded; report that one.
        i = keys.index(_device_key(selected))
        added = [(i, devices[i].name)]
    return {
        "track": args["track"], "item": item.name, "devices_added": [name for _, name in added],
        "device_index": added[0][0] if added else None,
    }


def _walk_devices(track, path):
    """Walk device_path [device, chain, device, ...] from the track's devices into nested rack chains.
    Returns (container, index, device): container is the track or chain whose devices[index] is device."""
    if (not isinstance(path, list) or len(path) % 2 == 0
            or any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in path)):
        raise OpError("INVALID_ARG", "device_path must be [device, chain, device, ...]: an odd number of non-negative integers")
    container, where = track, "the track"
    for n in range(0, len(path), 2):
        devices = list(container.devices)
        if path[n] >= len(devices):
            raise OpError("NOT_FOUND", f"no device {path[n]}; {where} has {len(devices)} devices")
        device = devices[path[n]]
        if n + 1 < len(path):
            chains = list(device.chains) if getattr(device, "can_have_chains", False) else []
            j = path[n + 1]
            if j >= len(chains):
                raise OpError("NOT_FOUND", f"no chain {j}; '{device.name}' has {len(chains)} chains")
            container, where = chains[j], f"chain {j} of '{device.name}'"
    return container, path[-1], device


def _device(song, args):
    return _walk_devices(_track(song, args), args["device_path"])[2]


SIMPLER_CLASS = "OriginalSimpler"


def _pad_simpler(track, note):
    """The first Simpler in the first chain of the Drum Rack pad that plays note."""
    if isinstance(note, bool) or not isinstance(note, int) or not 0 <= note <= 127:
        raise OpError("INVALID_ARG", "pad_note must be a MIDI note 0..127")
    rack = _find_drum_rack(track.devices, depth=1)
    if rack is None:
        raise OpError("NOT_FOUND", "that track has no Drum Rack; give device_path instead")
    used = [p for p in rack.drum_pads if p.chains]
    pad = next((p for p in used if p.note == note), None)
    if pad is None:
        listed = ", ".join(f"{p.note} {p.name}" for p in used) or "none"
        raise OpError("NOT_FOUND", f"no pad {note} in use in '{rack.name}'; used pads: {listed}")
    devices = list(pad.chains[0].devices)
    simpler = next((d for d in devices if d.class_name == SIMPLER_CLASS), None)
    if simpler is None:
        names = ", ".join(d.name for d in devices) or "nothing"
        raise OpError("INVALID_ARG", f"pad {note} '{pad.name}' holds {names}, not a Simpler")
    return simpler


def _simpler(song, args):
    """The Simpler given by device_path or by the Drum Rack pad note that plays it."""
    by_path, by_pad = args.get("device_path") is not None, args.get("pad_note") is not None
    if by_path == by_pad:
        raise OpError("INVALID_ARG", "give device_path or pad_note, not both")
    if by_pad:
        return _pad_simpler(_track(song, args), args["pad_note"])
    device = _device(song, args)
    if device.class_name != SIMPLER_CLASS:
        raise OpError("INVALID_ARG", f"'{device.name}' is not a Simpler")
    return device


def replace_sample(song, app, args):
    """Swap the sample in a Simpler, given by device_path or by the Drum Rack pad note that plays it."""
    device = _simpler(song, args)
    path = _audio_file(args)
    try:
        device.replace_sample(path)
    except Exception as e:  # one Live call; its exception type is unverified
        raise OpError("INVALID_ARG", f"Live could not load {path}: {e}")
    return {
        "track": args["track"], "device_path": args.get("device_path"), "pad_note": args.get("pad_note"),
        "device": device.name, "file": path,
    }


PLAYBACK_MODES = ("Classic", "One-Shot", "Slicing")  # SimplerDevice.playback_mode
SLICING_PLAYBACK_MODES = ("Mono", "Poly", "Thru")  # SimplerDevice.slicing_playback_mode
SLICING_STYLES = ("Transient", "Beat", "Region", "Manual")  # Sample.slicing_style
BEAT_DIVISIONS = ("1/16", "1/16T", "1/8", "1/8T", "1/4", "1/4T", "1/2", "1/2T", "1 Bar", "2 Bars", "4 Bars")
DEVICE_CHOICES = {"playback_mode": PLAYBACK_MODES, "slicing_playback_mode": SLICING_PLAYBACK_MODES}
SAMPLE_CHOICES = {"slicing_style": SLICING_STYLES, "slicing_beat_division": BEAT_DIVISIONS, "warp_mode": WARP_MODES}
SAMPLE_FIELDS = tuple(SAMPLE_CHOICES) + ("slicing_sensitivity", "slicing_region_count", "warping")


def _simpler_info(args, device):
    info = {
        "track": args["track"], "device_path": args.get("device_path"), "pad_note": args.get("pad_note"),
        "device": device.name, "playback_mode": _named(PLAYBACK_MODES, device.playback_mode),
        "slicing_playback_mode": _named(SLICING_PLAYBACK_MODES, device.slicing_playback_mode),
    }
    sample = device.sample
    if sample is None:
        return {**info, "file": None}
    slices = list(sample.slices)
    return {
        **info, "file": sample.file_path,
        "slicing_style": _named(SLICING_STYLES, sample.slicing_style),
        "slicing_beat_division": _named(BEAT_DIVISIONS, sample.slicing_beat_division),
        "slicing_sensitivity": sample.slicing_sensitivity, "slicing_region_count": sample.slicing_region_count,
        "warping": bool(sample.warping), "warp_mode": _warp_name(sample.warp_mode),
        "slice_count": len(slices), "slice_times_s": [round(f / sample.sample_rate, 4) for f in slices],
    }


def set_simpler(song, app, args):
    """Set a Simpler's playback and slicing, then optionally reverse and/or crop its sample. Validates all before setting any."""
    device = _simpler(song, args)
    sample = device.sample
    given = [k for k in (*DEVICE_CHOICES, *SAMPLE_FIELDS) if args.get(k) is not None]
    actions = [k for k in ("reverse", "crop") if args.get(k) is not None and _flag(args, k)]
    if not given and not actions:
        raise OpError("INVALID_ARG", f"give at least one of {', '.join((*DEVICE_CHOICES, *SAMPLE_FIELDS))}, reverse, crop")
    needs_sample = [k for k in given if k in SAMPLE_FIELDS] + actions
    if sample is None and needs_sample:
        raise OpError("LIVE_STATE", f"'{device.name}' has no sample for {', '.join(needs_sample)}; replace_sample first")
    values = {}
    for k in given:
        names = DEVICE_CHOICES.get(k) or SAMPLE_CHOICES.get(k)
        if names is not None:
            if args[k] not in names or args[k] == "REX":  # REX is a clip mode only: the Sample API lists 0-4 and 6
                allowed = [n for n in names if n != "REX"]
                raise OpError("INVALID_ARG", f"{k} must be one of {', '.join(allowed)}")
            values[k] = names.index(args[k])
    if "slicing_sensitivity" in given:
        values["slicing_sensitivity"] = _number(args, "slicing_sensitivity")
        if not 0 <= values["slicing_sensitivity"] <= 1:
            raise OpError("INVALID_ARG", "slicing_sensitivity must be 0..1")
    if "slicing_region_count" in given:
        # ponytail: LOM gives no range; 128 is a sanity cap. Live may clamp lower: the result shows what it kept.
        values["slicing_region_count"] = _whole(args, "slicing_region_count", 1, 128)
    if "warping" in given:
        values["warping"] = _flag(args, "warping")
    for k in DEVICE_CHOICES:
        if k in values:
            setattr(device, k, values[k])
    for k in ("warping", "warp_mode", "slicing_style", "slicing_beat_division", "slicing_sensitivity", "slicing_region_count"):
        if k in values:
            setattr(sample, k, values[k])
    for action in actions:  # after the settings; each writes a new sample file
        getattr(device, action)()
    return _simpler_info(args, device)


def insert_device(song, app, args):
    """Add a native Live device by its browser name, after the existing devices unless index says otherwise.
    track "main" is the Main (master) track."""
    track = song.master_track if args["track"] == "main" else _track(song, args)
    name = args["device_name"]
    if not isinstance(name, str) or not name.strip():
        raise OpError("INVALID_ARG", "device_name must be a device name such as 'Limiter'")
    index = args.get("index", -1)
    if isinstance(index, bool) or not isinstance(index, int) or index < -1:
        raise OpError("INVALID_ARG", "index must be -1 (end) or a device position 0, 1, ...")
    before = {_device_key(d) for d in track.devices}
    append = index == -1 or index >= len(before)
    try:
        if append:
            track.insert_device(name)
        else:
            track.insert_device(name, index)
    except Exception as e:  # one Live call; its exception type is unverified
        raise OpError("INVALID_ARG", (
            f"Live could not insert '{name}': {str(e).rstrip('.')}. insert_device takes native Live devices by their browser name, "
            "not plug-ins, and Lite lacks some (e.g. EQ Eight; use Channel EQ)"
        ))
    devices = list(track.devices)
    new = [i for i, d in enumerate(devices) if _device_key(d) not in before]
    at = new[0] if new else (len(devices) - 1 if append else index)
    return {"track": args["track"], "device": devices[at].name, "device_index": at}


def delete_device(song, app, args):
    """Remove the device at device_path from its track or rack chain. track "main" is the Main track."""
    track = song.master_track if args["track"] == "main" else _track(song, args)
    container, index, device = _walk_devices(track, args["device_path"])
    name = device.name
    try:
        container.delete_device(index)
    except Exception as e:  # one Live call; its exception type is unverified
        raise OpError("LIVE_STATE", f"Live could not delete '{name}': {str(e).rstrip('.')}")
    return {
        "track": args["track"], "device_path": args["device_path"], "deleted": name,
        "devices": [d.name for d in container.devices],
    }


def _param_info(i, p):
    info = {
        "index": i, "name": p.name, "value": p.value, "min": p.min, "max": p.max,
        "display": str(p), "quantized": bool(p.is_quantized), "enabled": bool(p.is_enabled),
    }
    if p.is_quantized:
        info["items"] = list(p.value_items)
    return info


def get_device(song, app, args):
    device = _device(song, args)
    chains = None
    if getattr(device, "can_have_chains", False):
        chains = [
            {"index": j, "name": c.name, "devices": [{"index": k, "name": d.name} for k, d in enumerate(c.devices)]}
            for j, c in enumerate(device.chains)
        ]
    return {
        "track": args["track"], "device_path": args["device_path"], "name": device.name,
        "class_name": device.class_name,
        "parameters": [_param_info(i, p) for i, p in enumerate(device.parameters)],
        "chains": chains,
    }


def _param_value(p, value):
    """Check value against p and return the raw value to set. Quantized params also take a value_items name."""
    if p.is_quantized and isinstance(value, str):
        items = [str(v) for v in p.value_items]
        lowered = [v.lower() for v in items]
        if value.lower() not in lowered:
            choices = ", ".join(items) if items else f"a whole number in {p.min}..{p.max}"
            raise OpError("INVALID_ARG", f"'{p.name}' takes one of: {choices}")
        return p.min + lowered.index(value.lower())
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpError("INVALID_ARG", f"'{p.name}' takes a number" + (" or one of its items" if p.is_quantized else ""))
    if not p.min <= value <= p.max:
        raise OpError("INVALID_ARG", (
            f"'{p.name}' range is {p.min}..{p.max} ({p.str_for_value(p.min)} to {p.str_for_value(p.max)}), got {value}"
        ))
    if p.is_quantized and value != int(value):
        raise OpError("INVALID_ARG", f"'{p.name}' takes a whole number")
    return value


def _param_by_name(device, name):
    """(index, param) for a read_device parameter name, case ignored. Refuses unknown, ambiguous and disabled names."""
    params = list(device.parameters)
    names = [p.name.lower() for p in params]
    hits = [i for i, n in enumerate(names) if n == str(name).lower()]
    if not hits:
        close = difflib.get_close_matches(str(name).lower(), names, n=3)
        hint = f"did you mean {', '.join(params[names.index(c)].name for c in close)}?" if close else "read_device lists the names"
        raise OpError("NOT_FOUND", f"'{device.name}' has no parameter '{name}'; {hint}")
    if len(hits) > 1:
        raise OpError("INVALID_ARG", f"'{device.name}' has {len(hits)} parameters named '{name}'")
    p = params[hits[0]]
    if not p.is_enabled:
        raise OpError("LIVE_STATE", f"'{p.name}' is controlled by a macro or locked")
    return hits[0], p


def set_parameters(song, app, args):
    device = _device(song, args)
    values = args["values"]
    if not isinstance(values, dict) or not values:
        raise OpError("INVALID_ARG", "values must map parameter names to values")
    todo = []
    for name, value in values.items():  # validate everything before setting anything
        index, p = _param_by_name(device, name)
        if any(i == index for i, _, _ in todo):
            raise OpError("INVALID_ARG", f"'{name}' is given twice")
        todo.append((index, p, _param_value(p, value)))
    for _, p, value in todo:
        p.value = value
    return {"track": args["track"], "device_path": args["device_path"], "changed": [_param_info(i, p) for i, p, _ in todo]}


# name -> (function, mutating). Mutating ops run inside one undo step.
# Transport ops (fire_clip, fire_scene, stop, play_arrangement) and undo itself are not undoable, so not wrapped.
OPS = {
    "hello": (hello, False),
    "get_overview": (get_overview, False),
    "create_midi_track": (create_midi_track, True),
    "create_audio_track": (create_audio_track, True),
    "create_clip": (create_clip, True),
    "delete_track": (delete_track, True),
    "delete_clip": (delete_clip, True),
    "create_scene": (create_scene, True),
    "copy_clip": (copy_clip, True),
    "get_notes": (get_notes, False),
    "get_clip": (get_clip, False),
    "set_clip": (set_clip, True),
    "replace_notes": (replace_notes, True),
    "write_clip": (write_clip, True),
    "set_scale": (set_scale, True),
    "set_tempo": (set_tempo, True),
    "fire_clip": (fire_clip, False),
    "fire_scene": (fire_scene, False),
    "stop": (stop, False),
    "play_arrangement": (play_arrangement, False),
    "arrange": (arrange, True),
    "undo": (undo, False),
    "browse": (browse, False),
    "load_item": (load_item, True),
    "get_device": (get_device, False),
    "set_parameters": (set_parameters, True),
    "create_audio_clip": (create_audio_clip, True),
    "replace_sample": (replace_sample, True),
    "set_simpler": (set_simpler, True),
    "insert_device": (insert_device, True),
    "delete_device": (delete_device, True),
    "set_mix": (set_mix, True),
    "set_envelope": (set_envelope, True),
    "set_label": (set_label, True),
}
