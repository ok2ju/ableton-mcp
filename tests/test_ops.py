import pytest

from AbletonMCP import ops
from AbletonMCP.bridge import OpError
from fake_live import Application, Device, MidiNoteSpecification, Song, Track, install_fake_live_module


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
