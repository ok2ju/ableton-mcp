"""Minimal fakes of Live's Python API, enough for ops and bridge tests."""

import copy
import sys
import types


class Device:
    def __init__(self, name, parameters=(), class_name=None):
        self.name = name
        self.class_name = class_name or name
        self.parameters = list(parameters)


class DrumPad:
    def __init__(self, note, name, chains=()):
        self.note, self.name, self.chains = note, name, list(chains)


class Chain:
    def __init__(self, devices, name=""):
        self.devices = list(devices)
        self.name = name

    def delete_device(self, index):
        self.devices = self.devices[:index] + self.devices[index + 1:]


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
    """Continuous params display on the volume/send curve: 0.85 = 0 dB, 1.0 = +6 dB, 0 = -inf. Pan uses min -1, max 1.
    Quantized params (items given) display their item name."""

    def __init__(self, value, min=0.0, max=1.0, name="", items=None, enabled=True):
        self.value, self.min, self.max, self.name = value, min, max, name
        self.is_quantized = items is not None
        self.value_items = tuple(items or ())
        self.is_enabled = enabled

    def str_for_value(self, value):
        if self.is_quantized:
            return self.value_items[int(value)]
        return "-inf dB" if value == 0 else f"{(value - 0.85) * 40:.1f} dB"

    def __str__(self):
        return self.str_for_value(self.value)


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


class Envelope:
    """Records insert_step calls as (time, length, value)."""

    def __init__(self):
        self.steps = []

    def insert_step(self, time, length, value):
        self.steps.append((time, length, value))


class Clip:
    def __init__(self, length, is_midi_clip=True):
        self.name = ""
        self.color = 0
        self.length = length
        self.is_midi_clip = is_midi_clip
        self._start_marker, self._end_marker = 0.0, float(length)
        self._loop_start, self._loop_end = 0.0, float(length)
        self.looping = True
        self.file_path = None  # audio clips only, as are the fields below
        self.warping = not is_midi_clip
        self.warp_mode = 0
        self.available_warp_modes = (0, 1, 2, 3, 4, 6)  # no REX for a plain file
        self.gain = 0.4
        self.pitch_coarse = 0
        self.pitch_fine = 0
        self.notes = []
        self._next_id = 1
        self.envelopes = {}  # param -> Envelope
        self.cleared = []  # params passed to clear_envelope

    # Live refuses a start at or past its end, like the real setters.
    def _check(self, start, end):
        if not start < end:
            raise RuntimeError("Invalid marker position")

    start_marker = property(lambda self: self._start_marker,
                            lambda self, v: (self._check(v, self._end_marker), setattr(self, "_start_marker", v)))
    end_marker = property(lambda self: self._end_marker,
                          lambda self, v: (self._check(self._start_marker, v), setattr(self, "_end_marker", v)))
    loop_start = property(lambda self: self._loop_start,
                          lambda self, v: (self._check(v, self._loop_end), setattr(self, "_loop_start", v)))
    loop_end = property(lambda self: self._loop_end,
                        lambda self, v: (self._check(self._loop_start, v), setattr(self, "_loop_end", v)))

    @property
    def gain_display_string(self):
        return f"{(self.gain - 0.4) * 60:.1f} dB"

    def add_new_notes(self, specs):
        assert isinstance(specs, tuple)
        for spec in specs:
            self.notes.append(MidiNote(self._next_id, spec))
            self._next_id += 1

    def get_all_notes_extended(self):
        return tuple(self.notes)

    def remove_notes_by_id(self, ids):
        self.notes = [n for n in self.notes if n.note_id not in set(ids)]

    # Clip envelopes as probed on Live 12.4.16 (docs/ableton-mcp-research.md 3.4).
    def automation_envelope(self, param):
        return self.envelopes.get(param)

    def create_automation_envelope(self, param):
        if param in self.envelopes:
            raise RuntimeError("Envelope already exists")
        self.envelopes[param] = Envelope()
        return self.envelopes[param]

    def clear_envelope(self, param):
        self.cleared.append(param)
        self.envelopes.pop(param, None)


class ClipSlot:
    def __init__(self):
        self.clip = None
        self.fired = False

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        self.clip = Clip(length)

    def create_audio_clip(self, path):
        if path.endswith("broken.wav"):  # stands in for a file Live cannot decode
            raise RuntimeError("Could not read the audio file")
        self.clip = Clip(8.0, is_midi_clip=False)
        self.clip.name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        self.clip.file_path = path

    def delete_clip(self):
        self.clip = None

    def fire(self):
        self.fired = True

    def duplicate_clip_to(self, target):
        target.clip = copy.deepcopy(self.clip)


class Track:
    """Fake Live.Track.Track with the 12.4 API. install_fake_live_module(version) decides what hello sees."""

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
        self.view = types.SimpleNamespace(selected_device=None)
        self.clip_slots = [ClipSlot() for _ in range(scenes)]
        self.arrangement_clips = []

    def create_midi_clip(self, start_time, length):  # arrangement, 12.1.10+
        raise NotImplementedError

    def duplicate_clip_to_arrangement(self, clip, destination_time):  # hello's `arrangement` feature
        placed = copy.deepcopy(clip)
        placed.start_time = destination_time
        self.arrangement_clips = self.arrangement_clips + [placed]

    def delete_clip(self, clip):  # arrangement clips
        self.arrangement_clips = [c for c in self.arrangement_clips if c is not clip]

    def insert_device(self, device_name, target_index=None):  # 12.3+, native devices only
        if device_name not in NATIVE_DEVICES:
            raise RuntimeError(f"Device {device_name} not available.")  # Live 12.4.16's text, ends with "."
        device = Device(device_name)
        if target_index is None:
            self.devices = self.devices + [device]
        else:
            self.devices = self.devices[:target_index] + [device] + self.devices[target_index:]

    def delete_device(self, index):
        self.devices = self.devices[:index] + self.devices[index + 1:]


NATIVE_DEVICES = ("Limiter", "Channel EQ", "Reverb", "Simpler")


class Sample:
    def __init__(self, file_path):
        self.file_path = file_path
        self.sample_rate = 44100
        self.slicing_style = 0
        self.slicing_beat_division = 4
        self.slicing_sensitivity = 0.5
        self.slicing_region_count = 8
        self.warping = False
        self.warp_mode = 0
        self.slices = (0, 22050, 44100, 66150)  # sample frames


class SimplerDevice(Device):
    def __init__(self, name="Simpler", file_path=None):
        super().__init__(name, class_name="OriginalSimpler")
        self.file_path = file_path
        self.sample = Sample(file_path) if file_path else None  # an empty Simpler has no sample
        self.playback_mode = 0
        self.slicing_playback_mode = 0
        self.calls = []  # reverse / crop, in order

    def replace_sample(self, file_path):  # 12.4+
        self.file_path = file_path
        self.sample = Sample(file_path)

    def reverse(self):
        self.calls.append("reverse")

    def crop(self):
        self.calls.append("crop")


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
        self.clips_stopped = False
        self.back_to_arranger = True
        self.current_song_time = 0.0
        self.tracks = []
        self.scenes = [Scene() for _ in range(8)]
        self.return_tracks = [Track("A-Reverb", has_midi_input=False, scenes=0), Track("B-Delay", has_midi_input=False, scenes=0)]
        self.master_track = Track("Main", has_midi_input=False, scenes=0)
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

    def create_audio_track(self, index):
        track = Track(name=f"{len(self.tracks) + 1}-Audio", has_midi_input=False, scenes=len(self.scenes))
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

    def start_playing(self):
        self.is_playing = True

    def stop_all_clips(self):
        self.clips_stopped = True

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
        if item.devices:
            track.view.selected_device = track.devices[-1]


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


class Track12_1:
    """What hello sees of Track on Live 12.1.10: arrangement create_midi_clip, no insert_device."""
    create_midi_clip = Track.create_midi_clip


def install_fake_live_module(version=(12, 1, 10)):
    """Put a fake `Live` package into sys.modules and return it. Below 12.4 the classes hello inspects
    lack insert_device, replace_sample, create_audio_clip and clip envelopes; the fake objects themselves always have them."""
    live = types.ModuleType("Live")
    modern = version >= (12, 4)
    live.Track = types.SimpleNamespace(Track=Track if modern else Track12_1)
    live.SimplerDevice = types.SimpleNamespace(SimplerDevice=SimplerDevice if modern else type("SimplerDevice", (), {}))
    live.ClipSlot = types.SimpleNamespace(ClipSlot=ClipSlot if modern else type("ClipSlot", (), {}))
    live.Clip = types.SimpleNamespace(MidiNoteSpecification=MidiNoteSpecification,
                                      Clip=Clip if modern else type("Clip", (), {}))
    sys.modules["Live"] = live
    return live
