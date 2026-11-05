import pytest

from AbletonMCP import ops
from AbletonMCP.bridge import OpError
from fake_live import (
    Application, Chain, Device, DrumPad, DrumRack, InstrumentRack, MidiNoteSpecification, Song, Track, install_fake_live_module,
)


@pytest.fixture
def live():
    install_fake_live_module()
    song = Song()
    return song, Application(song=song)


def note(pitch, start, dur=0.25):
    return {"pitch": pitch, "start_time": start, "duration": dur, "velocity": 100}


def test_create_track_clip_and_overview(live):
    song, app = live
    assert ops.create_midi_track(song, app, {"name": "Drums"}) == {"index": 0, "name": "Drums"}
    clip = ops.create_clip(song, app, {"track": 0, "slot": 0, "length_beats": 4})
    assert clip["length"] == 4
    ov = ops.get_overview(song, app, {})
    assert ov["tempo"] == 120.0 and ov["scene_count"] == 8
    assert ov["tracks"][0]["type"] == "midi"
    assert ov["tracks"][0]["clip_slots"][0] == {"slot": 0, "has_clip": True, "name": "", "length": 4}


def test_replace_notes_leaves_only_new(live):
    song, app = live
    song.create_midi_track(-1)
    song.tracks[0].clip_slots[0].create_clip(4.0)
    clip = song.tracks[0].clip_slots[0].clip
    clip.add_new_notes(tuple(MidiNoteSpecification(60 + i, i, 0.5) for i in range(3)))
    out = ops.replace_notes(song, app, {"track": 0, "slot": 0, "notes": [note(36, 0), note(38, 1)]})
    assert out["note_count"] == 2
    assert sorted((n.pitch, n.start_time) for n in clip.notes) == [(36, 0.0), (38, 1.0)]


def test_replace_notes_rejects_bad_pitch(live):
    song, app = live
    song.create_midi_track(-1)
    song.tracks[0].clip_slots[0].create_clip(4.0)
    with pytest.raises(OpError) as e:
        ops.replace_notes(song, app, {"track": 0, "slot": 0, "notes": [note(128, 0)]})
    assert e.value.code == "INVALID_ARG"


def test_missing_track_and_empty_slot(live):
    song, app = live
    with pytest.raises(OpError) as e:
        ops.fire_clip(song, app, {"track": 3, "slot": 0})
    assert e.value.code == "NOT_FOUND"
    song.create_midi_track(-1)
    with pytest.raises(OpError) as e:
        ops.fire_clip(song, app, {"track": 0, "slot": 0})
    assert e.value.code == "NOT_FOUND"


def test_clip_on_audio_track_is_live_state(live):
    song, app = live
    song.tracks.append(Track(has_midi_input=False))
    with pytest.raises(OpError) as e:
        ops.create_clip(song, app, {"track": 0, "slot": 0})
    assert e.value.code == "LIVE_STATE"


def test_set_tempo_bounds(live):
    song, app = live
    assert ops.set_tempo(song, app, {"bpm": 95}) == {"tempo": 95.0}
    with pytest.raises(OpError):
        ops.set_tempo(song, app, {"bpm": 5})


def test_load_item_returns_only_new_devices(live):
    song, app = live
    song.create_midi_track(-1)
    song.tracks[0].devices = [Device("EQ Eight")]
    out = ops.load_item(song, app, {"track": 0, "root": "drums", "path": ["Kits", "808 Core Kit.adg"]})
    assert out["devices_added"] == ["808 Core Kit"]
    assert song.view.selected_track is song.tracks[0]


def test_load_item_rejects_folder(live):
    song, app = live
    song.create_midi_track(-1)
    with pytest.raises(OpError) as e:
        ops.load_item(song, app, {"track": 0, "root": "drums", "path": ["Kits"]})
    assert e.value.code == "INVALID_ARG"


def test_browse_one_level(live):
    song, app = live
    out = ops.browse(song, app, {"root": "drums", "path": []})
    assert out["items"] == [
        {"name": "Kits", "is_folder": True, "is_loadable": False},
        {"name": "Drum Rack", "is_folder": False, "is_loadable": True},
    ]


def test_browse_missing_path_not_found(live):
    song, app = live
    with pytest.raises(OpError) as e:
        ops.browse(song, app, {"root": "drums", "path": ["Nope"]})
    assert e.value.code == "NOT_FOUND"


def test_browse_bad_root(live):
    song, app = live
    with pytest.raises(OpError) as e:
        ops.browse(song, app, {"root": "../etc", "path": []})
    assert e.value.code == "INVALID_ARG"


def test_hello_reports_missing_insert_device(live):
    song, app = live
    out = ops.hello(song, app, {})
    assert out["features"]["insert_device"] is False
    assert out["features"]["arrangement_create_midi_clip"] is True
    assert out["variant"] == "Lite"


def test_undo_with_nothing_to_undo(live):
    song, app = live
    song.can_undo = False
    with pytest.raises(OpError) as e:
        ops.undo(song, app, {})
    assert e.value.code == "LIVE_STATE" and song.undo_count == 0


def _midi_clip_with(song, specs, length=4.0):
    song.create_midi_track(-1)
    slot = song.tracks[-1].clip_slots[0]
    slot.create_clip(length)
    slot.clip.add_new_notes(tuple(specs))
    return slot.clip


def test_get_notes_returns_write_notes_shape(live):
    song, app = live
    _midi_clip_with(song, [MidiNoteSpecification(42, 1.0, 0.25, 89.6), MidiNoteSpecification(36, 0.0, 0.5),
                           MidiNoteSpecification(38, 1.0, 0.25)])
    out = ops.get_notes(song, app, {"track": 0, "slot": 0})
    assert out == {"track": 0, "slot": 0, "length": 4.0, "notes": [
        {"pitch": 36, "start_time": 0.0, "duration": 0.5, "velocity": 100},
        {"pitch": 38, "start_time": 1.0, "duration": 0.25, "velocity": 100},
        {"pitch": 42, "start_time": 1.0, "duration": 0.25, "velocity": 90},
    ]}


def test_get_notes_empty_slot_and_audio_clip(live):
    song, app = live
    song.create_midi_track(-1)
    with pytest.raises(OpError) as e:
        ops.get_notes(song, app, {"track": 0, "slot": 0})
    assert e.value.code == "NOT_FOUND"
    song.tracks[0].clip_slots[1].create_clip(4.0)
    song.tracks[0].clip_slots[1].clip.is_midi_clip = False
    with pytest.raises(OpError) as e:
        ops.get_notes(song, app, {"track": 0, "slot": 1})
    assert e.value.code == "LIVE_STATE"


def test_overview_drum_pads(live):
    song, app = live
    song.create_midi_track(-1)
    song.create_midi_track(-1)
    song.tracks[0].devices = [DrumRack("808 Core Kit", [DrumPad(36, "Kick", chains=["c"]), DrumPad(37, "Rim")])]
    song.tracks[1].devices = [Device("Operator")]
    tracks = ops.get_overview(song, app, {})["tracks"]
    assert tracks[0]["drum_pads"] == [{"note": 36, "name": "Kick"}]
    assert tracks[0]["devices"] == ["808 Core Kit"]
    assert tracks[1]["drum_pads"] is None


def test_delete_track(live):
    song, app = live
    song.create_midi_track(-1)
    song.create_midi_track(-1)
    with pytest.raises(OpError) as e:
        ops.delete_track(song, app, {"track": 2})
    assert e.value.code == "NOT_FOUND"
    assert ops.delete_track(song, app, {"track": 0}) == {"deleted": "1-MIDI", "track_count": 1}
    assert [t.name for t in song.tracks] == ["2-MIDI"]
    with pytest.raises(OpError) as e:
        ops.delete_track(song, app, {"track": 0})
    assert e.value.code == "LIVE_STATE"


def test_delete_group_track_refused(live):
    song, app = live
    song.create_midi_track(-1)
    song.create_midi_track(-1)
    song.tracks[0].is_foldable = True
    with pytest.raises(OpError) as e:
        ops.delete_track(song, app, {"track": 0})
    assert e.value.code == "LIVE_STATE" and len(song.tracks) == 2


def test_delete_clip(live):
    song, app = live
    _midi_clip_with(song, [])
    with pytest.raises(OpError) as e:
        ops.delete_clip(song, app, {"track": 0, "slot": 1})
    assert e.value.code == "NOT_FOUND"
    assert ops.delete_clip(song, app, {"track": 0, "slot": 0}) == {"track": 0, "slot": 0}
    assert not song.tracks[0].clip_slots[0].has_clip


def test_replace_notes_counts_notes_outside_clip(live):
    song, app = live
    _midi_clip_with(song, [])
    out = ops.replace_notes(song, app, {"track": 0, "slot": 0, "notes": [note(36, 0), note(36, 4), note(36, 5)]})
    assert out["notes_outside_clip"] == 2


def test_write_clip_empty_slot(live):
    song, app = live
    song.create_midi_track(-1)
    out = ops.write_clip(song, app, {"track": 0, "slot": 0, "length_beats": 8, "name": "Beat",
                                     "notes": [note(36, 0), note(38, 9)]})
    assert out == {"track": 0, "slot": 0, "name": "Beat", "length": 8.0, "note_count": 2,
                   "notes_outside_clip": 1, "replaced": False}
    clip = song.tracks[0].clip_slots[0].clip
    assert clip.length == 8.0 and [n.pitch for n in clip.notes] == [36, 38]


def test_write_clip_replaces_midi_clip(live):
    song, app = live
    old = _midi_clip_with(song, [MidiNoteSpecification(60, 0, 1)], length=4.0)
    out = ops.write_clip(song, app, {"track": 0, "slot": 0, "length_beats": 8, "notes": [note(36, 0)]})
    clip = song.tracks[0].clip_slots[0].clip
    assert out["replaced"] is True
    assert clip is not old and clip.length == 8.0 and [n.pitch for n in clip.notes] == [36]


def test_write_clip_audio_clip_and_audio_track(live):
    song, app = live
    _midi_clip_with(song, []).is_midi_clip = False
    with pytest.raises(OpError) as e:
        ops.write_clip(song, app, {"track": 0, "slot": 0, "length_beats": 8, "notes": []})
    assert e.value.code == "LIVE_STATE"
    song.tracks.append(Track(has_midi_input=False))
    with pytest.raises(OpError) as e:
        ops.write_clip(song, app, {"track": 1, "slot": 0, "length_beats": 8, "notes": []})
    assert e.value.code == "LIVE_STATE"


def test_write_clip_invalid_note_leaves_clip(live):
    song, app = live
    old = _midi_clip_with(song, [MidiNoteSpecification(60, 0, 1)], length=4.0)
    with pytest.raises(OpError) as e:
        ops.write_clip(song, app, {"track": 0, "slot": 0, "length_beats": 8, "notes": [note(36, 0), note(128, 1)]})
    assert e.value.code == "INVALID_ARG"
    assert song.tracks[0].clip_slots[0].clip is old and [n.pitch for n in old.notes] == [60]


def test_set_scale(live):
    song, app = live
    out = ops.set_scale(song, app, {"root_note": 9, "scale_name": "Minor"})
    assert out == {"root_note": 9, "name": "Minor", "intervals": [0, 2, 3, 5, 7, 8, 10], "scale_mode": True}
    assert (song.root_note, song.scale_name, song.scale_mode) == (9, "Minor", True)


def test_set_scale_rejects_unknown_name_and_bad_root(live):
    song, app = live
    with pytest.raises(OpError) as e:
        ops.set_scale(song, app, {"root_note": 0, "scale_name": "Hungarian Gypsy"})
    assert e.value.code == "INVALID_ARG" and song.scale_mode is False
    with pytest.raises(OpError) as e:
        ops.set_scale(song, app, {"root_note": 12, "scale_name": "Minor"})
    assert e.value.code == "INVALID_ARG"


def test_set_scale_without_scale_api_changes_nothing():
    import types

    song = types.SimpleNamespace(root_note=0, scale_name="Major")  # an older Live: no scale_mode/scale_intervals
    with pytest.raises(OpError) as e:
        ops.set_scale(song, None, {"root_note": 9, "scale_name": "Minor"})
    assert e.value.code == "LIVE_STATE" and (song.root_note, song.scale_name) == (0, "Major")


def test_overview_drum_pads_inside_instrument_rack(live):
    song, app = live
    song.create_midi_track(-1)
    song.create_midi_track(-1)
    inner = DrumRack("Drum Rack", [DrumPad(36, "", chains=["c"])])
    song.tracks[0].devices = [InstrumentRack("Ahlimba Kit", [Chain([Device("Utility"), inner])])]
    deeper = InstrumentRack("Outer", [Chain([InstrumentRack("Inner", [Chain([inner])])])])
    song.tracks[1].devices = [deeper]
    tracks = ops.get_overview(song, app, {})["tracks"]
    assert tracks[0]["drum_pads"] == [{"note": 36, "name": ""}]
    assert tracks[1]["drum_pads"] is None  # only one rack level deep
