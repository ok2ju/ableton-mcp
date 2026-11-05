"""Minimal fakes of Live's Python API, enough for ops and bridge tests."""

import sys
import types


class Device:
    def __init__(self, name):
        self.name = name


class MidiNoteSpecification:
    def __init__(self, pitch, start_time, duration, velocity=100, mute=False):
        self.pitch, self.start_time, self.duration, self.velocity, self.mute = pitch, start_time, duration, velocity, mute


class MidiNote(MidiNoteSpecification):
    def __init__(self, note_id, spec):
        super().__init__(spec.pitch, spec.start_time, spec.duration, spec.velocity, spec.mute)
        self.note_id = note_id


class Clip:
    def __init__(self, length, is_midi_clip=True):
        self.name = ""
        self.length = length
        self.is_midi_clip = is_midi_clip
        self.notes = []
        self._next_id = 1

    def add_new_notes(self, specs):
        assert isinstance(specs, tuple)
        for spec in specs:
            self.notes.append(MidiNote(self._next_id, spec))
            self._next_id += 1

    def get_all_notes_extended(self):
        return tuple(self.notes)

    def remove_notes_by_id(self, ids):
        self.notes = [n for n in self.notes if n.note_id not in set(ids)]


class ClipSlot:
    def __init__(self):
        self.clip = None
        self.fired = False

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        self.clip = Clip(length)

    def fire(self):
        self.fired = True


class Track:
    """Fake Live.Track.Track. No insert_device, like Live 12.1.10."""

    def __init__(self, name="1-MIDI", has_midi_input=True, scenes=8):
        self.name = name
        self.has_midi_input = has_midi_input
        self.has_audio_input = not has_midi_input
        self.is_foldable = False
        self.devices = []
        self.clip_slots = [ClipSlot() for _ in range(scenes)]

    def create_midi_clip(self, start_time, length):  # arrangement, 12.1.10+
        raise NotImplementedError


class SimplerDevice:
    pass


class SongView:
    def __init__(self):
        self.selected_track = None


class Song:
    def __init__(self):
        self.tempo = 120.0
        self.signature_numerator = 4
        self.signature_denominator = 4
        self.root_note = 0
        self.scale_name = "Major"
        self.is_playing = False
        self.tracks = []
        self.scenes = [object()] * 8
        self.view = SongView()
        self.undo_log = []  # "begin" / "end" markers
        self.undo_count = 0
        self.can_undo = True

    def begin_undo_step(self):
        self.undo_log.append("begin")

    def end_undo_step(self):
        self.undo_log.append("end")

    def create_midi_track(self, index):
        track = Track(name=f"{len(self.tracks) + 1}-MIDI", scenes=len(self.scenes))
        if index == -1:
            self.tracks.append(track)
        else:
            self.tracks.insert(index, track)

    def stop_playing(self):
        self.is_playing = False

    def undo(self):
        self.undo_count += 1


class BrowserItem:
    def __init__(self, name, children=(), loadable=False, devices=()):
        self.name = name
        self.children = list(children)
        self.is_folder = bool(children)
        self.is_loadable = loadable
        self.devices = list(devices)  # what loading this item adds to the selected track


class Browser:
    def __init__(self, song):
        self._song = song
        self.drums = BrowserItem("Drums", [
            BrowserItem("Kits", [BrowserItem("808 Core Kit.adg", loadable=True, devices=["808 Core Kit"])]),
            BrowserItem("Drum Rack", loadable=True, devices=["Drum Rack"]),
        ])
        self.instruments = BrowserItem("Instruments", [])

    def load_item(self, item):
        track = self._song.view.selected_track
        track.devices = track.devices + [Device(n) for n in item.devices]


class Application:
    def __init__(self, variant="Lite", version=(12, 1, 10), song=None):
        self._variant = variant
        self._version = version
        self.browser = Browser(song)

    def get_major_version(self):
        return self._version[0]

    def get_minor_version(self):
        return self._version[1]

    def get_bugfix_version(self):
        return self._version[2]

    def get_variant(self):
        return self._variant


def install_fake_live_module():
    """Put a fake `Live` package into sys.modules and return it."""
    live = types.ModuleType("Live")
    live.Track = types.SimpleNamespace(Track=Track)
    live.SimplerDevice = types.SimpleNamespace(SimplerDevice=SimplerDevice)
    live.Clip = types.SimpleNamespace(MidiNoteSpecification=MidiNoteSpecification)
    sys.modules["Live"] = live
    return live
