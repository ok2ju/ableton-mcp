"""Estimate a sample's tempo and key from its audio: analyze(path).

Needs the optional `hearing` extra (librosa), imported only when analyze() runs, so the base install stays `mcp`
only. Tempo is librosa's beat tracker, with half and double candidates because beat trackers often pick one of
those. Key is the file's mean chroma correlated with the 24 rotated Krumhansl-Kessler major and minor profiles;
relative major/minor mix-ups are likely, so the result carries the winning correlation as key_confidence.

bpm_loop assumes the file is a loop of 1, 2, 4, 8 or 16 bars of 4/4: exactly one such tempo lies in 80..160 BPM,
so it ignores the beat tracker (a 174 BPM loop shows as 87). On Live 12.4's Core Library loops it matched the
filename BPM (within 2, or half/double) for about 158 of 170 files, against 83 for the beat tracker alone.
"""

INSTALL_HINT = "analyze_sample needs the hearing extra (librosa). Install it with: uv sync --extra hearing"
NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Kessler probe-tone ratings, tonic first (Krumhansl 1990, Cognitive Foundations of Musical Pitch).
MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
LOOP_BEATS = (4, 8, 16, 32, 64)  # 1..16 bars of 4/4
LOOP_BPM_RANGE = (80, 160)  # one octave, so at most one whole-bar tempo fits


def loop_bpm(duration_s: float) -> float | None:
    """The tempo in 80..160 at which duration_s is a whole number of 4/4 bars (1..16); None if none fits."""
    for beats in LOOP_BEATS:
        bpm = beats * 60 / duration_s
        if LOOP_BPM_RANGE[0] <= bpm < LOOP_BPM_RANGE[1]:
            return round(bpm, 1)
    return None


def analyze(path: str) -> dict:
    """bpm (with bpm_half, bpm_double), bpm_loop (see module doc), key ("A minor"), key_confidence (Pearson r, -1..1) and duration_s.
    Raises ImportError without librosa, and ValueError for audio it cannot read."""
    import librosa
    import numpy as np

    try:
        y, sr = librosa.load(path, sr=22050, mono=True)
    except Exception as e:  # librosa surfaces soundfile/audioread errors of many types
        raise ValueError(f"Cannot read audio from {path}: {e}") from e
    if not np.any(y):
        raise ValueError(f"{path} is silent; there is no tempo or key to hear")
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    # beat_track's tempo sits on a coarse grid (about 117.5 or 123 near 120 BPM); the mean beat interval is finer.
    bpm = 60 * (len(beats) - 1) / (beats[-1] - beats[0]) if len(beats) > 2 else float(np.atleast_1d(tempo)[0])
    bpm = round(float(bpm), 1)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    best, key = -2.0, None
    for mode, profile in (("major", MAJOR), ("minor", MINOR)):
        for tonic in range(12):
            r = float(np.corrcoef(chroma, np.roll(profile, tonic))[0, 1])
            if r > best:
                best, key = r, f"{NOTES[tonic]} {mode}"
    return {
        "bpm": bpm,
        "bpm_half": round(bpm / 2, 1),
        "bpm_double": round(bpm * 2, 1),
        "bpm_loop": loop_bpm(len(y) / sr),
        "key": key,
        "key_confidence": round(best, 3) if key else None,  # flat chroma gives NaN correlations and no key
        "duration_s": round(len(y) / sr, 3),
    }
