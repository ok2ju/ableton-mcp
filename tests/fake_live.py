"""Minimal fakes of Live's Python API, enough for ops and bridge tests."""

import copy
import sys
import types


class Device:
    def __init__(self, name):
        self.name = name


class DrumPad:
    def __init__(self, note, name, chains=()):
        self.note, self.name, self.chains = note, name, list(chains)


class Chain:
    def __init__(self, devices):
        self.devices = list(devices)


class InstrumentRack(Device):
    can_have_chains = True

    def __init__(self, name, chains):
        super().__init__(name)
        self.chains = list(chains)


class DrumRack(Device):
    can_have_drum_pads = True

    def __init__(self, name, pads):
        super().__init__(name)
        self.drum_pads = list(pads)


class MidiNoteSpecification:
    def __init__(self, pitch, start_time, duration, velocity=100, mute=False):
        self.pitch, self.start_time, self.duration, self.velocity, self.mute = pitch, start_time, duration, velocity, mute


class MidiNote(MidiNoteSpecification):
    def __init__(self, note_id, spec):
        super().__init__(spec.pitch, spec.start_time, spec.duration, spec.velocity, spec.mute)
        self.note_id = note_id


class DeviceParameter:
    """Volume/send fake: a monotonic curve with 0.85 = 0 dB, 1.0 = +6 dB, 0 = -inf. Pan uses min -1, max 1."""

    def __init__(self, value, min=0.0, max=1.0):
        self.value, self.min, self.max = value, min, max

    def str_for_value(self, value):
        return "-inf dB" if value == 0 else f"{(value - 0.85) * 40:.1f} dB"


class MixerDevice:
    def __init__(self, sends=2):
        self.volume = DeviceParameter(0.85)
        self.panning = DeviceParameter(0.0, -1.0, 1.0)
        self.sends = [DeviceParameter(0.0) for _ in range(sends)]


class Scene:
    def __init__(self, name=""):
        self.name = name
        self.color = 0
        self.fired = False

    def fire(self):
        self.fired = True


class Clip:
    def __init__(self, length, is_midi_clip=True):
        self.name = ""
        self.color = 0
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

    def delete_clip(self):
        self.clip = None

    def fire(self):
        self.fired = True

    def duplicate_clip_to(self, target):
        target.clip = copy.deepcopy(self.clip)


class Track:
    """Fake Live.Track.Track. No insert_device, like Live 12.1.10."""

    def __init__(self, name="1-MIDI", has_midi_input=True, scenes=8):
        self.name = name
        self.has_midi_input = has_midi_input
        self.has_audio_input = not has_midi_input
        self.is_foldable = False
        self.mute = False
        self.solo = False
        self.color = 0
        self.mixer_device = MixerDevice()
        self.devices = []
        self.clip_slots = [ClipSlot() for _ in range(scenes)]

    def create_midi_clip(self, start_time, length):  # arrangement, 12.1.10+
        raise NotImplementedError


class SimplerDevice:
    pass


class SongView:
    def __init__(self):
        self.selected_track = None


SCALE_INTERVALS = {
    "Major": [0, 2, 4, 5, 7, 9, 11],
    "Minor": [0, 2, 3, 5, 7, 8, 10],
    "Dorian": [0, 2, 3, 5, 7, 9, 10],
    "Minor Pentatonic": [0, 3, 5, 7, 10],
}


class Song:
    def __init__(self):
        self.tempo = 120.0
        self.signature_numerator = 4
        self.signature_denominator = 4
        self.root_note = 0
        self._scale_name = "Major"
        self.scale_mode = False
        self.is_playing = False
        self.tracks = []
        self.scenes = [Scene() for _ in range(8)]
        self.return_tracks = [Track("A-Reverb", has_midi_input=False, scenes=0), Track("B-Delay", has_midi_input=False, scenes=0)]
        self.view = SongView()
        self.undo_log = []  # "begin" / "end" markers
        self.undo_count = 0
        self.can_undo = True

    @property
    def scale_name(self):
        return self._scale_name

    @scale_name.setter
    def scale_name(self, name):
        if name in SCALE_INTERVALS:  # Live ignores unknown names
            self._scale_name = name

    @property
    def scale_intervals(self):
        return tuple(SCALE_INTERVALS[self._scale_name])

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

    def delete_track(self, index):
        del self.tracks[index]

    def create_scene(self, index):
        index = len(self.scenes) if index == -1 else index
        self.scenes.insert(index, Scene())
        for t in self.tracks:
            t.clip_slots.insert(index, ClipSlot())
        return self.scenes[index]

    def duplicate_scene(self, index):
        self.scenes.insert(index + 1, Scene(self.scenes[index].name))
        for t in self.tracks:
            slot = ClipSlot()
            if t.clip_slots[index].has_clip:
                t.clip_slots[index].duplicate_clip_to(slot)
            t.clip_slots.insert(index + 1, slot)

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
