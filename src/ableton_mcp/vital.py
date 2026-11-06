"""Write and read Vital synth presets (.vital JSON) with the stdlib only.

Vital has no remote control, so a preset file is the way in. Format notes, local checks and design:
docs/vital-mcp-research.md. Vital loads any control the file leaves out at its default, so a preset
only carries the controls the caller sets.
"""

import base64
import difflib
import json
import math
import subprocess
from pathlib import Path

from .midifile import _SAFE_NAME

# Ranges are engine values (what the file stores), hand-copied from Vital's parameter table:
# https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp
# Some ranges are narrower than Vital's where only part of the range means anything (osc destination).
CONTROLS: dict[str, tuple[float, float, str]] = {}
for _n in (1, 2, 3):
    CONTROLS |= {
        f"osc_{_n}_on": (0, 1, "0 off, 1 on (only osc 1 is on by default)"),
        f"osc_{_n}_transpose": (-48, 48, "semitones, whole numbers"),
        f"osc_{_n}_tune": (-1, 1, "fine tune in semitones"),
        f"osc_{_n}_level": (0, 1, "amplitude = value**2; default 0.707"),
        f"osc_{_n}_pan": (-1, 1, "-1 left, 1 right"),
        f"osc_{_n}_unison_voices": (1, 16, "voices, whole numbers; default 1"),
        f"osc_{_n}_unison_detune": (0, 10, "detune % = value**2; default 4.472 (20%)"),
        f"osc_{_n}_unison_blend": (0, 1, "level of the detuned voices against the centre one; default 0.8"),
        f"osc_{_n}_destination": (0, 4, "0 filter 1, 1 filter 2, 2 both filters, 3 effects, 4 direct out; "
                                        "defaults: osc 1 -> 0, osc 2 -> 1, osc 3 -> 3"),
    }
for _n in (1, 2):
    CONTROLS |= {
        f"filter_{_n}_on": (0, 1, "0 off (default), 1 on"),
        f"filter_{_n}_cutoff": (8, 136, "MIDI note number; Hz = 440 * 2**((value - 69) / 12); default 60"),
        f"filter_{_n}_resonance": (0, 1, "default 0.5"),
        f"filter_{_n}_drive": (0, 20, "dB; default 0"),
        f"filter_{_n}_blend": (0, 2, "0 low-pass, 1 band-pass, 2 high-pass (analog, dirty, ladder, digital models)"),
        f"filter_{_n}_model": (0, 7, "0 analog, 1 dirty, 2 ladder, 3 digital, 4 diode, 5 formant, 6 comb, 7 phaser"),
        f"filter_{_n}_style": (0, 9, "for analog/dirty/ladder/digital: 0 12dB, 1 24dB slope"),
    }
for _n in (1, 2, 3):
    CONTROLS |= {
        f"env_{_n}_attack": (0, 2.37842, "seconds = value**4; default 0.1495"
                                         + (" (env 1 is the amp envelope)" if _n == 1 else "")),
        f"env_{_n}_decay": (0, 2.37842, "seconds = value**4; default 1.0"),
        f"env_{_n}_sustain": (0, 1, "level; default 1"),
        f"env_{_n}_release": (0, 2.37842, "seconds = value**4; default 0.5476"),
    }
for _n in (1, 2):
    CONTROLS |= {
        f"lfo_{_n}_sync": (0, 4, "0 seconds, 1 tempo (default), 2 dotted, 3 triplet, 4 keytrack"),
        f"lfo_{_n}_frequency": (-7, 9, f"Hz = 2**value; used when lfo_{_n}_sync is 0; default 1"),
        f"lfo_{_n}_tempo": (0, 12, "used when sync is 1-3: 0 freeze, 1 32/1, 2 16/1, 3 8/1, 4 4/1, 5 2/1, "
                                   "6 1/1, 7 1/2, 8 1/4, 9 1/8, 10 1/16, 11 1/32, 12 1/64; default 7"),
    }
CONTROLS |= {
    "chorus_on": (0, 1, "0 off (default), 1 on"),
    "chorus_dry_wet": (0, 1, "mix; default 0.5"),
    "delay_on": (0, 1, "0 off (default), 1 on"),
    "delay_dry_wet": (0, 1, "mix; default 0.333"),
    "delay_feedback": (-1, 1, "default 0.5"),
    "delay_tempo": (4, 12, "note length: 4 4/1, 5 2/1, 6 1/1, 7 1/2, 8 1/4, 9 1/8 (default), 10 1/16, "
                           "11 1/32, 12 1/64"),
    "reverb_on": (0, 1, "0 off (default), 1 on"),
    "reverb_dry_wet": (0, 1, "mix; default 0.25"),
    "reverb_size": (0, 1, "default 0.5"),
    "reverb_decay_time": (-6, 6, "seconds = 2**value; default 0 (1 s)"),
    "distortion_on": (0, 1, "0 off (default), 1 on"),
    "distortion_type": (0, 5, "0 soft clip, 1 hard clip, 2 linear fold, 3 sine fold, 4 bit crush, 5 down sample"),
    "distortion_drive": (-30, 30, "dB; default 0"),
    "distortion_mix": (0, 1, "default 1"),
    "macro_control_1": (0, 1, "macro knob; default 0"),
    "macro_control_2": (0, 1, "macro knob; default 0"),
    "macro_control_3": (0, 1, "macro knob; default 0"),
    "macro_control_4": (0, 1, "macro knob; default 0"),
    "volume": (0, 7399.4404, "dB = sqrt(value) - 80; default 5473 (-6 dB)"),
    "polyphony": (1, 32, "voices; default 8"),
}

# Modulation sources, from synth_voice_handler.cpp at the same commit (createStatusOutput and the
# env/lfo/random/macro modules), and seen in the user's local presets.
MOD_SOURCES = frozenset(
    [f"env_{i}" for i in range(1, 7)] + [f"lfo_{i}" for i in range(1, 9)]
    + [f"random_{i}" for i in range(1, 5)] + [f"macro_control_{i}" for i in range(1, 5)]
    + ["velocity", "note", "note_in_octave", "aftertouch", "slide", "lift", "mod_wheel", "pitch_wheel",
       "random", "stereo"]
)
MAX_MODULATIONS = 64
# Switches and selectors: Vital drops a route to them. ponytail: inferred from the user's presets, which never
# route to these (a sweep of ~/Music/Vital); check synth_parameters.cpp if a new control needs it.
NOT_MODULATABLE_SUFFIXES = ("_on", "_model", "_style", "_sync", "_destination", "distortion_type", "polyphony")


def _line(name: str, points: list[float]) -> dict:
    """Line-generator JSON. Vital builds a wavetable from it directly. y=0 is the top, y=1 the bottom."""
    n = len(points) // 2
    return {"name": name, "num_points": n, "points": points, "powers": [0.0] * n, "smooth": False}


WAVES: dict[str, dict] = {
    "saw": _line("Saw", [0.0, 0.0, 1.0, 1.0]),
    "square": _line("Square", [0.0, 0.0, 0.5, 0.0, 0.5, 1.0, 1.0, 1.0]),
    "triangle": _line("Triangle", [0.0, 0.5, 0.25, 0.0, 0.75, 1.0, 1.0, 0.5]),
    "sine": _line("Sine", [v for i in range(33)
                           for v in (i / 32, 0.5 - 0.5 * math.sin(2 * math.pi * i / 32))]),
}
_TRIANGLE_LFO = _line("Triangle", [0.0, 1.0, 0.5, 0.0, 1.0, 1.0])
_SILENT_SAMPLE = {  # 64 int16 zeros
    "name": "Silence", "length": 64, "sample_rate": 44100, "samples": base64.b64encode(bytes(128)).decode(),
}

DEFAULT_VERSION = "1.5.5"
VITAL_BINARY = Path("/Applications/Vital.app/Contents/MacOS/Vital")


def config() -> tuple[str, Path]:
    """Return (synth_version, data_directory) from Vital.config, or the defaults if it is missing or bad."""
    home = Path.home()  # read per call, so tests can point HOME elsewhere
    default_dir = home / "Music" / "Vital"
    try:
        data = json.loads((home / "Library" / "Application Support" / "vital" / "Vital.config").read_text())
        return str(data.get("synth_version") or DEFAULT_VERSION), Path(data.get("data_directory") or default_dir)
    except (OSError, ValueError, AttributeError):  # AttributeError: valid JSON that is not an object
        return DEFAULT_VERSION, default_dir


def _check_control(name: str, value) -> float:
    if name not in CONTROLS:
        close = difflib.get_close_matches(name, CONTROLS, n=3)
        raise ValueError(f"unknown control {name!r}" + (f"; did you mean {', '.join(close)}?" if close else ""))
    low, high, _ = CONTROLS[name]
    value = float(value)
    if not (math.isfinite(value) and low <= value <= high):
        raise ValueError(f"{name} is out of range {low:g}–{high:g}: got {value:g}")
    return value


def build_preset(name: str, controls: dict, modulations: list[dict], waves: list[str], version: str) -> dict:
    """Return preset JSON. modulations: [{source, destination, amount -1..1}]. waves: up to 3 WAVES keys."""
    settings = {k: _check_control(k, v) for k, v in controls.items()}
    if len(modulations) > MAX_MODULATIONS:
        raise ValueError(f"at most {MAX_MODULATIONS} modulations")
    routes = []
    for i, m in enumerate(modulations, start=1):
        if m["source"] not in MOD_SOURCES:
            raise ValueError(f"unknown modulation source {m['source']!r}; use one of {', '.join(sorted(MOD_SOURCES))}")
        if m["destination"] not in CONTROLS:
            _check_control(m["destination"], 0)  # raises with close matches
        if m["destination"].endswith(NOT_MODULATABLE_SUFFIXES):
            raise ValueError(f"{m['destination']} is a switch or selector; Vital cannot modulate it")
        amount = float(m["amount"])
        if not (math.isfinite(amount) and -1 <= amount <= 1):
            raise ValueError(f"modulation amount must be between -1 and 1, got {amount:g}")
        routes.append({"source": m["source"], "destination": m["destination"]})
        settings[f"modulation_{i}_amount"] = amount
    if len(waves) > 3:
        raise ValueError("at most 3 waves, one per oscillator")
    for w in waves:
        if w not in WAVES:
            raise ValueError(f"unknown wave {w!r}; use one of {', '.join(WAVES)}")
    waves = list(waves) + ["saw"] * (3 - len(waves))
    settings |= {
        "modulations": routes,
        "wavetables": [WAVES[w] for w in waves],
        "lfos": [_TRIANGLE_LFO] * 8,
        "sample": _SILENT_SAMPLE,
    }
    return {
        "synth_version": version, "preset_name": name, "author": "ableton-mcp", "comments": "",
        "preset_style": "", "macro1": "MACRO 1", "macro2": "MACRO 2", "macro3": "MACRO 3", "macro4": "MACRO 4",
        "settings": settings,
    }


def write_preset(name: str, preset: dict, directory: Path) -> Path:
    """Write <directory>/<name>.vital and return the path. Rejects unsafe names and never overwrites."""
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError("name may only contain letters, digits, space, '_' and '-' (max 64)")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.vital"
    try:
        with open(path, "x") as f:  # "x" fails if the file exists: no undo for files
            json.dump(preset, f)
    except FileExistsError:
        raise ValueError(f"{path.name} already exists; pick another name") from None
    return path


def read_preset(name_or_path: str, directory: Path) -> dict:
    """Summarise a preset: a bare name (with or without .vital) means <directory>/<name>.vital; anything with '/' is a path.

    Returns the CONTROLS keys the file holds, its non-empty modulation routes and its wavetable names.
    Wavetable and sample data are never returned (factory files are about 1.6 MB).
    """
    if "/" in name_or_path:
        path = Path(name_or_path).expanduser()
        if path.suffix != ".vital":
            raise ValueError("a preset path must end in .vital")
    else:
        stem = name_or_path[:-6] if name_or_path.endswith(".vital") else name_or_path
        path = directory / f"{stem}.vital"
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise ValueError(f"no preset at {path}") from None
    except ValueError:  # bad JSON or bad UTF-8
        raise ValueError(f"{path.name} is not a Vital preset") from None
    settings = data.get("settings") if isinstance(data, dict) else None
    if not isinstance(settings, dict):
        raise ValueError(f"{path.name} is not a Vital preset")
    routes = settings.get("modulations") or []
    return {
        "name": data.get("preset_name") or path.stem,  # factory files leave preset_name out
        "author": data.get("author", ""),
        "synth_version": data.get("synth_version", ""),
        "controls": {k: settings[k] for k in CONTROLS if k in settings},
        "modulations": [
            {"source": r["source"], "destination": r["destination"],
             "amount": settings.get(f"modulation_{i}_amount", 0.0)}
            for i, r in enumerate(routes, start=1)
            if isinstance(r, dict) and r.get("source") and r.get("destination")
        ],
        "wavetables": [w.get("name", "") if isinstance(w, dict) else "" for w in settings.get("wavetables") or []],
    }


def launch(path: Path) -> bool:
    """Start the Vital standalone with the preset loaded, only if Vital is installed and not running.

    Vital 1.5.5 ignores macOS open-document events, and starting the binary while Vital runs opens a
    second instance (research §5.4), so a running Vital means: do nothing, the user picks the preset.
    """
    if not VITAL_BINARY.exists():
        return False
    try:
        if subprocess.run(["pgrep", "-x", "Vital"], capture_output=True).returncode == 0:  # 0: a process matched
            return False
        subprocess.Popen([str(VITAL_BINARY), str(path)], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:  # no pgrep, or the binary is not executable: the preset is written, the user loads it
        return False
    return True
