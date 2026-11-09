import pytest

from AbletonMCP import ops
from AbletonMCP.bridge import OpError
from fake_live import (
    Application, Chain, Clip, Device, DeviceParameter, DrumPad, DrumRack, InstrumentRack, MidiNoteSpecification, SimplerDevice,
    Song, Track,
    install_fake_live_module,
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
    assert out["device_index"] == 1
    assert song.view.selected_track is song.tracks[0]


def test_load_item_replace_reports_selected_device(live):
    song, app = live
    song.create_midi_track(-1)
    old = Device("Drift")
    old._live_ptr = 7
    song.tracks[0].devices = [Device("EQ Eight"), old]

    def replace(item):  # real Live: the new device keeps the old _live_ptr and is selected
        new = Device("808 Core Kit")
        new._live_ptr = 7
        song.tracks[0].devices = [song.tracks[0].devices[0], new]
        song.tracks[0].view.selected_device = new

    app.browser.load_item = replace
    out = ops.load_item(song, app, {"track": 0, "root": "drums", "path": ["Kits", "808 Core Kit.adg"]})
    assert out["devices_added"] == ["808 Core Kit"] and out["device_index"] == 1


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


def _err(fn, song, app, args):
    with pytest.raises(OpError) as e:
        fn(song, app, args)
    return e.value


def test_overview_mix_scenes_returns(live):
    import json

    song, app = live
    song.create_midi_track(-1)
    song.create_midi_track(-1)
    song.tracks[1].mixer_device.volume.value = 0
    ov = ops.get_overview(song, app, {})
    assert ov["tracks"][0]["mix"] == {"volume_db": 0.0, "pan": 0, "mute": False, "solo": False, "sends": {"A": None, "B": None}}
    assert ov["tracks"][1]["mix"]["volume_db"] is None
    assert ov["scenes"][0] == {"index": 0, "name": ""} and ov["scene_count"] == 8
    assert [(r["send"], r["name"]) for r in ov["return_tracks"]] == [("A", "A-Reverb"), ("B", "B-Delay")]
    json.dumps(ov, allow_nan=False)


def test_db_parses_unicode_minus():
    class P:
        def str_for_value(self, v):
            return "−6.0 dB"

    assert ops._db(P(), 0) == -6.0


def test_set_mix(live):
    song, app = live
    song.create_midi_track(-1)
    m = song.tracks[0].mixer_device
    out = ops.set_mix(song, app, {"track": 0, "volume_db": -12, "pan": -25, "sends": {"A": -6}, "mute": True, "solo": True})
    assert abs(m.volume.value - 0.55) < 0.001 and out["volume_db"] == -12.0
    assert m.panning.value == -0.5 and out["pan"] == -25
    assert out["sends"]["A"] == -6.0 and out["sends"]["B"] is None
    assert song.tracks[0].mute is True and song.tracks[0].solo is True


def test_set_mix_refusals_change_nothing(live):
    song, app = live
    song.create_midi_track(-1)
    e = _err(ops.set_mix, song, app, {"track": 0, "volume_db": 7})
    assert e.code == "INVALID_ARG" and "6.0" in str(e)
    e = _err(ops.set_mix, song, app, {"track": 0, "pan": 10, "sends": {"C": 0}})
    assert e.code == "INVALID_ARG" and "A, B" in str(e)
    assert song.tracks[0].mixer_device.panning.value == 0.0
    assert _err(ops.set_mix, song, app, {"track": 0}).code == "INVALID_ARG"
    assert _err(ops.set_mix, song, app, {"track": 0, "mute": 1}).code == "INVALID_ARG"
    assert _err(ops.set_mix, song, app, {"track": 0, "sends": {}}).code == "INVALID_ARG"


def test_create_scene(live):
    song, app = live
    assert ops.create_scene(song, app, {"name": "Outro"}) == {"scene": 8, "name": "Outro", "scene_count": 9}
    assert song.scenes[8].name == "Outro"
    song.create_midi_track(-1)
    song.tracks[0].clip_slots[0].create_clip(4.0)
    song.scenes[0].name = "old0"
    assert ops.create_scene(song, app, {"index": 0, "name": "Intro"})["scene"] == 0
    assert [s.name for s in song.scenes[:2]] == ["Intro", "old0"]
    assert not song.tracks[0].clip_slots[0].has_clip and song.tracks[0].clip_slots[1].has_clip


def test_create_scene_copy_of(live):
    song, app = live
    song.create_midi_track(-1)
    song.tracks[0].clip_slots[0].create_clip(8.0)
    song.tracks[0].clip_slots[0].clip.name = "beat"
    song.scenes[1].name = "old1"
    out = ops.create_scene(song, app, {"copy_of": 0, "name": "Verse"})
    assert out == {"scene": 1, "name": "Verse", "scene_count": 9}
    copy = song.tracks[0].clip_slots[1].clip
    assert copy.name == "beat" and copy.length == 8.0 and copy is not song.tracks[0].clip_slots[0].clip
    assert song.scenes[2].name == "old1"
    assert _err(ops.create_scene, song, app, {"copy_of": 0, "index": 2}).code == "INVALID_ARG"
    assert _err(ops.create_scene, song, app, {"copy_of": 99}).code == "NOT_FOUND"
    assert _err(ops.create_scene, song, app, {"index": 99}).code == "INVALID_ARG"


def test_fire_scene(live):
    song, app = live
    song.scenes[2].name = "Chorus"
    assert ops.fire_scene(song, app, {"scene": 2}) == {"scene": 2, "name": "Chorus"}
    assert song.scenes[2].fired
    assert _err(ops.fire_scene, song, app, {"scene": 99}).code == "NOT_FOUND"


def test_copy_clip(live):
    song, app = live
    song.create_midi_track(-1)
    song.tracks.append(Track(has_midi_input=False))
    slots = song.tracks[0].clip_slots
    slots[0].create_clip(8.0)
    slots[0].clip.name = "bass"
    assert ops.copy_clip(song, app, {"track": 0, "slot": 0, "to_track": 0, "to_slot": 3}) == {
        "track": 0, "slot": 3, "name": "bass", "length": 8.0,
    }
    slots[1].create_clip(4.0)
    slots[1].clip.name = "keep"
    e = _err(ops.copy_clip, song, app, {"track": 0, "slot": 0, "to_track": 0, "to_slot": 1})
    assert e.code == "LIVE_STATE" and slots[1].clip.name == "keep"
    assert _err(ops.copy_clip, song, app, {"track": 0, "slot": 0, "to_track": 1, "to_slot": 0}).code == "LIVE_STATE"
    assert _err(ops.copy_clip, song, app, {"track": 0, "slot": 5, "to_track": 0, "to_slot": 6}).code == "NOT_FOUND"
    song.tracks[1].clip_slots[0].clip = Clip(4.0, is_midi_clip=False)
    assert _err(ops.copy_clip, song, app, {"track": 1, "slot": 0, "to_track": 0, "to_slot": 7}).code == "LIVE_STATE"
    song.tracks[1].is_foldable = True
    assert _err(ops.copy_clip, song, app, {"track": 1, "slot": 0, "to_track": 1, "to_slot": 2}).code == "LIVE_STATE"


def test_set_label(live):
    song, app = live
    song.create_midi_track(-1)
    song.tracks[0].clip_slots[0].create_clip(4.0)
    assert ops.set_label(song, app, {"track": 0, "name": "Drums", "color": 0xFF0000}) == {"target": "track", "name": "Drums"}
    assert song.tracks[0].color == 0xFF0000
    assert ops.set_label(song, app, {"track": 0, "slot": 0, "name": "beat"})["target"] == "clip"
    assert song.tracks[0].clip_slots[0].clip.name == "beat"
    assert ops.set_label(song, app, {"scene": 1, "color": 0x00FF00})["target"] == "scene"
    assert song.scenes[1].color == 0x00FF00


def test_set_label_refusals(live):
    song, app = live
    song.create_midi_track(-1)
    assert _err(ops.set_label, song, app, {"track": 0, "slot": 1, "name": "x"}).code == "NOT_FOUND"
    assert _err(ops.set_label, song, app, {"slot": 0, "name": "x"}).code == "INVALID_ARG"
    assert _err(ops.set_label, song, app, {"track": 0, "scene": 0, "name": "x"}).code == "INVALID_ARG"
    assert _err(ops.set_label, song, app, {"track": 0, "color": 0x1000000}).code == "INVALID_ARG"
    assert _err(ops.set_label, song, app, {"track": 0}).code == "INVALID_ARG"


def drift():
    return Device("Drift", class_name="Drift", parameters=[
        DeviceParameter(1, 0, 1, "Device On", items=["Off", "On"]),
        DeviceParameter(0.85, 0.0, 1.0, "Filter Freq"),
        DeviceParameter(0, 0, 3, "Osc 1 Shape", items=["Sine", "Triangle", "Saw", "Square"]),
        DeviceParameter(0.2, 0.0, 1.0, "Env Amount", enabled=False),
    ])


@pytest.fixture
def drift_set(live):
    song, app = live
    song.create_midi_track(-1)
    rack = InstrumentRack("Basic Analog Bass", [Chain([drift()], name="Analog")])
    rack.class_name = "InstrumentGroupDevice"
    song.tracks[0].devices = [drift(), rack]
    return song, app


def test_get_device_lists_parameters(drift_set):
    song, app = drift_set
    out = ops.get_device(song, app, {"track": 0, "device_path": [0]})
    assert out["name"] == "Drift" and out["class_name"] == "Drift" and out["chains"] is None
    assert out["parameters"][1] == {
        "index": 1, "name": "Filter Freq", "value": 0.85, "min": 0.0, "max": 1.0,
        "display": "0.0 dB", "quantized": False, "enabled": True,
    }
    assert [p.get("items") for p in out["parameters"]] == [["Off", "On"], None, ["Sine", "Triangle", "Saw", "Square"], None]
    assert out["parameters"][3]["enabled"] is False


def test_get_device_walks_rack_chains(drift_set):
    song, app = drift_set
    rack = ops.get_device(song, app, {"track": 0, "device_path": [1]})
    assert rack["chains"] == [{"index": 0, "name": "Analog", "devices": [{"index": 0, "name": "Drift"}]}]
    assert ops.get_device(song, app, {"track": 0, "device_path": [1, 0, 0]})["class_name"] == "Drift"
    assert _err(ops.get_device, song, app, {"track": 0, "device_path": [5]}).code == "NOT_FOUND"
    assert _err(ops.get_device, song, app, {"track": 0, "device_path": [1, 3, 0]}).code == "NOT_FOUND"
    assert _err(ops.get_device, song, app, {"track": 0, "device_path": [0, 0, 0]}).code == "NOT_FOUND"  # Drift has no chains
    for bad in ([1, 0], ["a"], [], [True], [-1]):
        assert _err(ops.get_device, song, app, {"track": 0, "device_path": bad}).code == "INVALID_ARG", bad


def test_set_parameters_by_name_and_item(drift_set):
    song, app = drift_set
    out = ops.set_parameters(song, app, {"track": 0, "device_path": [1, 0, 0], "values": {"filter freq": 0.5, "Osc 1 Shape": "saw"}})
    params = song.tracks[0].devices[1].chains[0].devices[0].parameters
    assert params[1].value == 0.5 and params[2].value == 2
    assert [(c["name"], c["display"]) for c in out["changed"]] == [("Filter Freq", "-14.0 dB"), ("Osc 1 Shape", "Saw")]


def test_set_parameters_refusals_change_nothing(drift_set):
    song, app = drift_set
    params = song.tracks[0].devices[0].parameters
    args = {"track": 0, "device_path": [0]}
    e = _err(ops.set_parameters, song, app, {**args, "values": {"Filter Freq": 0.5, "Osc 1 Shape": 7}})
    assert e.code == "INVALID_ARG" and "0..3" in str(e) and "Sine to Square" in str(e)
    assert params[1].value == 0.85
    e = _err(ops.set_parameters, song, app, {**args, "values": {"Filter Freq": 1.5}})
    assert e.code == "INVALID_ARG" and "0.0..1.0" in str(e)
    assert _err(ops.set_parameters, song, app, {**args, "values": {"Env Amount": 0.5}}).code == "LIVE_STATE"
    e = _err(ops.set_parameters, song, app, {**args, "values": {"Fliter Freq": 0.5}})
    assert e.code == "NOT_FOUND" and "Filter Freq" in str(e)
    e = _err(ops.set_parameters, song, app, {**args, "values": {"Osc 1 Shape": "Noise"}})
    assert e.code == "INVALID_ARG" and "Sine, Triangle, Saw, Square" in str(e)
    for bad in ({"Filter Freq": True}, {"Filter Freq": "loud"}, {"Osc 1 Shape": 1.5}, {}, {"filter freq": 0.1, "Filter Freq": 0.2}):
        assert _err(ops.set_parameters, song, app, {**args, "values": bad}).code == "INVALID_ARG", bad
    params.append(DeviceParameter(0.5, name="filter freq"))
    assert _err(ops.set_parameters, song, app, {**args, "values": {"Filter Freq": 0.5}}).code == "INVALID_ARG"
    assert [p.value for p in params] == [1, 0.85, 0, 0.2, 0.5]


def test_hello_on_12_4_reports_sample_features():
    install_fake_live_module((12, 4, 16))
    song = Song()
    out = ops.hello(song, Application(version=(12, 4, 16), song=song), {})
    assert out["live_version"] == "12.4.16"
    assert out["features"]["insert_device"] is True
    assert out["features"]["replace_sample"] is True
    assert out["features"]["audio_clip"] is True


def test_hello_old_live_lacks_audio_clip(live):
    song, app = live
    assert ops.hello(song, app, {})["features"]["audio_clip"] is False


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "Atmos Calm 130 bpm.wav"
    path.write_bytes(b"RIFF")
    return str(path)


@pytest.fixture
def audio_set(live):
    song, app = live
    song.tracks = [Track("1-Audio", has_midi_input=False), Track("2-MIDI")]
    return song, app


def test_create_audio_clip(audio_set, wav):
    song, app = audio_set
    out = ops.create_audio_clip(song, app, {"track": 0, "slot": 1, "file_path": wav, "name": "Pad"})
    assert out == {"track": 0, "slot": 1, "name": "Pad", "file": wav, "length_beats": 8.0, "warping": True}
    assert song.tracks[0].clip_slots[1].clip.is_midi_clip is False
    assert ops.OPS["create_audio_clip"] == (ops.create_audio_clip, True)  # bridge wraps it in one undo step


def test_create_audio_clip_refusals(audio_set, wav, tmp_path):
    song, app = audio_set
    cases = [
        ({"track": 1, "slot": 0, "file_path": wav}, "LIVE_STATE", "audio track"),
        ({"track": 0, "slot": 0, "file_path": str(tmp_path / "missing.wav")}, "INVALID_ARG", "no audio file"),
        ({"track": 0, "slot": 0, "file_path": "kick.wav"}, "INVALID_ARG", "no audio file"),
    ]
    song.tracks[0].clip_slots[2].clip = Clip(4.0, is_midi_clip=False)
    cases.append(({"track": 0, "slot": 2, "file_path": wav}, "LIVE_STATE", "delete_clip"))
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"x")
    cases.append(({"track": 0, "slot": 0, "file_path": str(broken)}, "INVALID_ARG", "Could not read"))
    song.tracks.append(Track("Group", has_midi_input=False))
    song.tracks[2].is_foldable = True
    cases.append(({"track": 2, "slot": 0, "file_path": wav}, "LIVE_STATE", "group tracks"))
    for args, code, text in cases:
        with pytest.raises(OpError) as e:
            ops.create_audio_clip(song, app, args)
        assert e.value.code == code and text in str(e.value), args
    assert not song.tracks[0].clip_slots[0].has_clip


@pytest.fixture
def kit_set(live):
    song, app = live
    song.create_midi_track(-1)
    kick = DrumPad(36, "Kick", [Chain([SimplerDevice("Kick", "/old/kick.wav")])])
    snare = DrumPad(38, "Snare", [Chain([Device("Drum Sampler", class_name="DrumSampler")])])
    rack = InstrumentRack("Garage Kit", [Chain([DrumRack("Drums", [kick, snare, DrumPad(40, "Empty")])])])
    song.tracks[0].devices = [rack, SimplerDevice("Lead")]
    return song, app


def test_replace_sample_by_device_path_and_pad(kit_set, wav):
    song, app = kit_set
    out = ops.replace_sample(song, app, {"track": 0, "device_path": [1], "file_path": wav})
    assert out == {"track": 0, "device_path": [1], "pad_note": None, "device": "Lead", "file": wav}
    assert song.tracks[0].devices[1].file_path == wav
    out = ops.replace_sample(song, app, {"track": 0, "pad_note": 36, "file_path": wav})
    assert out["device"] == "Kick" and out["pad_note"] == 36
    assert song.tracks[0].devices[0].chains[0].devices[0].drum_pads[0].chains[0].devices[0].file_path == wav
    assert ops.OPS["replace_sample"][1] is True


def test_replace_sample_refusals(kit_set, wav):
    song, app = kit_set
    cases = [
        ({"device_path": [0]}, "INVALID_ARG", "'Garage Kit' is not a Simpler"),
        ({"pad_note": 38}, "INVALID_ARG", "holds Drum Sampler"),
        ({"pad_note": 40}, "NOT_FOUND", "used pads: 36 Kick, 38 Snare"),
        ({"pad_note": 200}, "INVALID_ARG", "0..127"),
        ({}, "INVALID_ARG", "not both"),
        ({"device_path": [1], "pad_note": 36}, "INVALID_ARG", "not both"),
    ]
    song.create_midi_track(-1)
    with pytest.raises(OpError) as e:
        ops.replace_sample(song, app, {"track": 1, "pad_note": 36, "file_path": wav})
    assert e.value.code == "NOT_FOUND" and "no Drum Rack" in str(e.value)
    for extra, code, text in cases:
        with pytest.raises(OpError) as e:
            ops.replace_sample(song, app, {"track": 0, "file_path": wav, **extra})
        assert e.value.code == code and text in str(e.value), extra
    assert song.tracks[0].devices[1].file_path is None


def test_insert_device_appends_to_track_and_main(live):
    song, app = live
    song.create_midi_track(-1)
    song.tracks[0].devices = [Device("Drift")]
    assert ops.insert_device(song, app, {"track": 0, "device_name": "Reverb"}) == {
        "track": 0, "device": "Reverb", "device_index": 1}
    assert ops.insert_device(song, app, {"track": 0, "device_name": "Channel EQ", "index": 9})["device_index"] == 2
    assert ops.insert_device(song, app, {"track": 0, "device_name": "Limiter", "index": 1})["device_index"] == 1
    assert [d.name for d in song.tracks[0].devices] == ["Drift", "Limiter", "Reverb", "Channel EQ"]
    out = ops.insert_device(song, app, {"track": "main", "device_name": "Limiter"})
    assert out == {"track": "main", "device": "Limiter", "device_index": 0}
    assert [d.name for d in song.master_track.devices] == ["Limiter"]
    assert ops.OPS["insert_device"][1] is True


def test_insert_device_maps_live_error(live):
    song, app = live
    song.create_midi_track(-1)
    with pytest.raises(OpError) as e:
        ops.insert_device(song, app, {"track": 0, "device_name": "EQ Eight"})
    assert e.value.code == "INVALID_ARG" and "native Live devices" in str(e.value) and ".." not in str(e.value)
    for args in ({"track": 0, "device_name": ""}, {"track": 0, "device_name": "Limiter", "index": -2}):
        with pytest.raises(OpError) as e:
            ops.insert_device(song, app, args)
        assert e.value.code == "INVALID_ARG"
    assert song.tracks[0].devices == []
