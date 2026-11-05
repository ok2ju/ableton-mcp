"""Music theory and pattern generators: pure functions and data, no I/O.

Pitches use Live's numbering (60 = C3), so octave o starts at 12 * (o + 2).
Generators return (notes, length_beats, summary); notes have the write_notes shape.
Only 4/4: one bar = 4 beats = 16 sixteenth steps.
"""

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_LETTERS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Names as spelled in Live's Current Scale Name chooser.
SCALES = {
    "Major": [0, 2, 4, 5, 7, 9, 11],
    "Minor": [0, 2, 3, 5, 7, 8, 10],
    "Dorian": [0, 2, 3, 5, 7, 9, 10],
    "Phrygian": [0, 1, 3, 5, 7, 8, 10],
    "Lydian": [0, 2, 4, 6, 7, 9, 11],
    "Mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "Locrian": [0, 1, 3, 5, 6, 8, 10],
    "Harmonic Minor": [0, 2, 3, 5, 7, 8, 11],
    "Melodic Minor": [0, 2, 3, 5, 7, 9, 11],
    "Major Pentatonic": [0, 2, 4, 7, 9],
    "Minor Pentatonic": [0, 3, 5, 7, 10],
    "Minor Blues": [0, 3, 5, 6, 7, 10],
}

MAX_BARS = 64
MAX_EUCLID_STEPS = 64
STEPS_PER_BAR = 16
STEP = 0.25  # one sixteenth, in beats


def parse_root(name: str) -> int:
    """'C', 'c#', 'Db', 'F#' -> 0..11. Anything else raises ValueError."""
    s = name.strip()
    if not s or s[0].upper() not in _LETTERS or s[1:] not in ("", "#", "b"):
        raise ValueError(f"root must be a note name like C, F# or Bb, got {name!r}")
    return (_LETTERS[s[0].upper()] + {"": 0, "#": 1, "b": -1}[s[1:]]) % 12


def _intervals(scale: str) -> list[int]:
    if scale not in SCALES:
        raise ValueError(f"scale must be one of: {', '.join(SCALES)}")
    return SCALES[scale]


def _scale_pitch(root: int, intervals: list[int], index: int, octave: int) -> int:
    """Pitch of the 0-based scale index; indices past the scale continue into the next octave."""
    n = len(intervals)
    return 12 * (octave + 2) + root + intervals[index % n] + 12 * (index // n)


def _check_degrees(degrees: list[int], intervals: list[int]):
    if not degrees:
        raise ValueError("degrees must not be empty")
    for d in degrees:
        if isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= len(intervals):
            raise ValueError(f"degree must be 1..{len(intervals)} for this scale, got {d!r}")


def _check_bars(bars: int):
    if isinstance(bars, bool) or not isinstance(bars, int) or not 1 <= bars <= MAX_BARS:
        raise ValueError(f"bars must be 1..{MAX_BARS}")


def _note(pitch: int, start: float, duration: float, velocity: int) -> dict:
    if not 0 <= pitch <= 127:
        raise ValueError(f"pitch {pitch} is outside 0..127; pick a different octave")
    return {"pitch": pitch, "start_time": start, "duration": duration, "velocity": velocity}


# --- chords ---------------------------------------------------------------

_CHORD_SUFFIX = {
    (4, 7): "", (3, 7): "m", (3, 6): "dim", (4, 8): "aug",
    (4, 7, 11): "maj7", (4, 7, 10): "7", (3, 7, 10): "m7", (3, 6, 10): "m7b5",
    (3, 6, 9): "dim7", (3, 7, 11): "mMaj7", (4, 8, 11): "maj7#5",
}


def _chord_intervals(scale: str) -> list[int]:
    intervals = _intervals(scale)
    if len(intervals) != 7:
        raise ValueError(f"chords need a 7-note scale; {scale} has {len(intervals)} notes")
    return intervals


def chord(root: str, scale: str, degree: int, sevenths: bool = False, octave: int = 3) -> list[int]:
    """Root-position chord stacked in thirds over the scale degree (1-based)."""
    intervals = _chord_intervals(scale)
    _check_degrees([degree], intervals)
    steps = (0, 2, 4, 6) if sevenths else (0, 2, 4)
    return [_scale_pitch(parse_root(root), intervals, degree - 1 + s, octave) for s in steps]


def chord_name(pitches: list[int]) -> str:
    """[57, 60, 64] -> 'Am'. Root position only."""
    shape = tuple(p - pitches[0] for p in pitches[1:])
    return NOTE_NAMES[pitches[0] % 12] + _CHORD_SUFFIX[shape]


def chord_notes(root: str, scale: str, degrees: list[int], beats_per_chord: float = 4.0,
                sevenths: bool = False, octave: int = 3):
    if not 0 < beats_per_chord <= 16:
        raise ValueError("beats_per_chord must be in (0, 16]")
    _check_degrees(degrees, _chord_intervals(scale))
    if len(degrees) * beats_per_chord > MAX_BARS * 4:
        raise ValueError(f"the progression is longer than {MAX_BARS} bars; use fewer degrees or a smaller beats_per_chord")
    notes, names = [], []
    for i, degree in enumerate(degrees):
        pitches = chord(root, scale, degree, sevenths, octave)
        names.append(chord_name(pitches))
        notes += [_note(p, i * beats_per_chord, beats_per_chord, 100) for p in pitches]
    summary = {"root": root, "scale": scale, "chords": names}
    return notes, len(degrees) * beats_per_chord, summary


# --- bass -----------------------------------------------------------------

# pattern -> eighth-note steps per bar as (start_beat, scale-index offset from the degree, octave shift)
BASS_PATTERNS = {
    "quarters": [(b, 0, 0) for b in range(4)],
    "eighths": [(b / 2, 0, 0) for b in range(8)],
    "octaves": [(b / 2, 0, b % 2) for b in range(8)],
    "offbeat": [(b + 0.5, 0, 0) for b in range(4)],
    "arpeggio": [(b / 2, (0, 2, 4, 2)[b % 4], 0) for b in range(8)],
}


def bass_notes(root: str, scale: str, pattern: str, degrees: list[int], bars: int | None = None, octave: int = 1):
    """One scale degree per bar, cycled over the bars."""
    if pattern not in BASS_PATTERNS:
        raise ValueError(f"pattern must be one of: {', '.join(BASS_PATTERNS)}")
    intervals = _intervals(scale)
    _check_degrees(degrees, intervals)
    bars = len(degrees) if bars is None else bars
    _check_bars(bars)
    steps = BASS_PATTERNS[pattern]
    duration = 1.0 if pattern == "quarters" else 0.5
    notes, roots = [], []
    for bar in range(bars):
        index = degrees[bar % len(degrees)] - 1
        roots.append(NOTE_NAMES[_scale_pitch(parse_root(root), intervals, index, octave) % 12])
        for start, offset, up in steps:
            pitch = _scale_pitch(parse_root(root), intervals, index + offset, octave) + 12 * up
            notes.append(_note(pitch, bar * 4 + start, duration, 100))
    summary = {"root": root, "scale": scale, "pattern": pattern, "bar_roots": roots}
    return notes, bars * 4.0, summary


# --- drums ----------------------------------------------------------------

# voice -> (General MIDI note, keywords matched against the pad name, lowercased with spaces/'-'/'_' removed)
DRUM_VOICES = {
    "kick": (36, ("kick", "bassdrum")),
    "rim": (37, ("rim", "sidestick")),
    "snare": (38, ("snare",)),
    "clap": (39, ("clap",)),
    "closed_hat": (42, ("closedhihat", "hihatclosed", "closedhat", "hatclosed", "closedhh", "hhc", "chh")),
    "tom": (45, ("tom",)),
    "open_hat": (46, ("openhihat", "hihatopen", "openhat", "hatopen", "openhh", "hho", "ohh")),
    "ride": (51, ("ride",)),  # before crash, so "Ride Cymbal" goes to ride
    "crash": (49, ("crash", "cymbal")),
    "shaker": (70, ("shaker", "maraca")),
    "cowbell": (56, ("cowbell",)),
    "perc": (63, ("perc", "conga", "bongo", "clave")),
}


def _matches(voice: str, pad_name: str) -> bool:
    key = pad_name.lower().replace(" ", "").replace("-", "").replace("_", "")
    return any(k in key for k in DRUM_VOICES[voice][1])


def pick_drum_pitches(drum_pads: list[dict] | None) -> dict:
    """{voice: (pitch, pad_name)}. None (no Live) gives GM notes with pad None.
    Pass 1: first free pad whose name matches the voice. Pass 2: the pad on the voice's GM note,
    if no voice claims that pad by name. Voices with neither are left out."""
    if drum_pads is None:
        return {v: (gm, None) for v, (gm, _) in DRUM_VOICES.items()}
    picked, taken = {}, set()
    for voice in DRUM_VOICES:
        pad = next((p for p in drum_pads if p["note"] not in taken and _matches(voice, p["name"])), None)
        if pad:
            picked[voice] = (pad["note"], pad["name"])
            taken.add(pad["note"])
    named = {p["note"] for p in drum_pads if any(_matches(v, p["name"]) for v in DRUM_VOICES)}
    for voice, (gm, _) in DRUM_VOICES.items():
        pad = next((p for p in drum_pads if p["note"] == gm), None)
        if voice not in picked and pad and gm not in taken and gm not in named:
            picked[voice] = (gm, pad["name"])
            taken.add(gm)
    return picked


def euclid(pulses: int, steps: int, rotation: int = 0) -> list[bool]:
    """Spread pulses as evenly as possible over steps, rotated left by rotation. euclid(3, 8) = x..x..x."""
    if not 1 <= steps <= MAX_EUCLID_STEPS or not 0 <= pulses <= steps:
        raise ValueError(f"euclid needs 1 <= steps <= {MAX_EUCLID_STEPS} and 0 <= pulses <= steps")
    hits = [(i * pulses) % steps < pulses for i in range(steps)]
    r = rotation % steps
    return hits[r:] + hits[:r]


_TRESILLO = "".join("x" if h else "." for h in euclid(3, 8)) * 2  # x..x..x.x..x..x.

# 16 sixteenth steps per voice: "X" accent (velocity 120), "x" normal (90), "." rest.
DRUM_STYLES = {
    "rock": {
        "about": "Kick on 1, 3 and the 'and' of 3, snare on 2 and 4 (the backbeat), straight eighth-note hats.",
        "grid": {"kick": "X.......X.X.....", "snare": "....X.......X...", "closed_hat": "x.x.x.x.x.x.x.x."},
    },
    "house": {
        "about": "Four-on-the-floor kick on every beat, clap on 2 and 4, open hat on every offbeat.",
        "grid": {
            "kick": "X...X...X...X...", "clap": "....X.......X...",
            "closed_hat": "x...x...x...x...", "open_hat": "..X...X...X...X.",
        },
    },
    "hip_hop": {
        "about": "Boom bap: syncopated kick, hard snare on 2 and 4, steady eighth-note hats. Sounds best at 85-95 BPM.",
        "grid": {"kick": "X......X..X.....", "snare": "....X.......X...", "closed_hat": "x.x.x.x.x.x.x.x."},
    },
    "trap": {
        "about": "Half-time feel: clap only on beat 3, sparse kicks, rapid sixteenth-note hats. Sounds best at 130-150 BPM.",
        "grid": {"kick": "X.....X...X.....", "clap": "........X.......", "closed_hat": "xxXxxxXxxxXxxxXx"},
    },
    "reggaeton": {
        "about": "Dembow: four-on-the-floor kick under a snare on the 'a' of 1 and the 'and' of 2, rim on the tresillo (3 over 8). Sounds best at 90-100 BPM.",
        "grid": {"kick": "X...X...X...X...", "snare": "...X..X....X..X.", "rim": _TRESILLO},
    },
    "breakbeat": {
        "about": "A funk break: kick on 1, the 'and' of 1 and around 3; snare on 2 and 4 with ghost notes around them.",
        "grid": {"kick": "X.X.......XX....", "snare": "....X..x.x..X..x", "closed_hat": "x.x.x.x.x.x.x.x."},
    },
}

_VELOCITY = {"X": 120, "x": 90}
LAYER_VELOCITY = 100


def drum_notes(style: str | None, bars: int, layers: list[dict] | None, pitches: dict):
    """style grid repeated over bars; each layer {voice, pulses, steps=16, rotation=0} is a Euclidean
    rhythm in sixteenths, repeated over the clip, and replaces the style's notes for that voice.
    pitches comes from pick_drum_pitches; voices with no pitch go to missing_voices."""
    if style is not None and style not in DRUM_STYLES:
        raise ValueError(f"style must be one of: {', '.join(DRUM_STYLES)}")
    _check_bars(bars)
    layers = layers or []
    if style is None and not layers:
        raise ValueError("give a style, layers, or both")
    total = bars * STEPS_PER_BAR
    hits = {}  # voice -> [(step, velocity)]
    if style is not None:
        for voice, row in DRUM_STYLES[style]["grid"].items():
            hits[voice] = [(s, _VELOCITY[row[s % 16]]) for s in range(total) if row[s % 16] != "."]
    for layer in layers:
        voice = layer["voice"]
        if voice not in DRUM_VOICES:
            raise ValueError(f"voice must be one of: {', '.join(DRUM_VOICES)}")
        pattern = euclid(layer["pulses"], layer.get("steps", 16), layer.get("rotation", 0))
        hits[voice] = [(s, LAYER_VELOCITY) for s in range(total) if pattern[s % len(pattern)]]
    notes, voices, missing = [], {}, []
    for voice, voice_hits in hits.items():
        if voice not in pitches:
            missing.append(voice)
            continue
        pitch, pad = pitches[voice]
        notes += [_note(pitch, s * STEP, STEP, v) for s, v in voice_hits]
        voices[voice] = {"pitch": pitch, "pad": pad, "hits": len(voice_hits)}
    summary = {
        "style": style,
        "about": DRUM_STYLES[style]["about"] if style else None,
        "voices": voices,
        "missing_voices": missing,
    }
    return notes, bars * 4.0, summary
