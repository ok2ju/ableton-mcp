"""Estimate a sample's tempo and key from its audio: analyze(path).

Needs the optional `hearing` extra (librosa), imported only when analyze() runs, so the base install stays `mcp`
only. Tempo is librosa's beat tracker, with half and double candidates because beat trackers often pick one of
those. Key is the file's mean chroma correlated with the 24 rotated Krumhansl-Kessler major and minor profiles;
relative major/minor mix-ups are likely, so the result carries the winning correlation as key_confidence.
"""

INSTALL_HINT = "analyze_sample needs the hearing extra (librosa). Install it with: uv sync --extra hearing"
NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Kessler probe-tone ratings, tonic first (Krumhansl 1990, Cognitive Foundations of Musical Pitch).
MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def analyze(path: str) -> dict:
    """bpm (with bpm_half, bpm_double), key ("A minor"), key_confidence (Pearson r, -1..1) and duration_s.
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
        "key": key,
        "key_confidence": round(best, 3) if key else None,  # flat chroma gives NaN correlations and no key
        "duration_s": round(len(y) / sr, 3),
    }
