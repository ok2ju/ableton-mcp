"""Find sample files on disk: resolve() and scan().

resolve() turns a sample named the way browse_library shows it into a file path Live can load.
Live's browser items carry a name and a uri, never a file path, and the `samples` root is a flat list of
file names whose files sit in nested folders on disk. So the server finds the file itself, by exact name,
in Live's own library folders. Every path comes from Path.home() at call time, so tests can point it elsewhere.

scan() lists the audio files in a folder the user names, each with a guess from its filename at its role,
kind, BPM and key, and its duration where the standard library can read it.
"""

import os
import re
import wave
from pathlib import Path

AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg")
CORE_SAMPLES = Path("Contents", "App-Resources", "Core Library", "Samples")
MAX_LISTED = 5


def user_library() -> Path:
    return Path.home() / "Music" / "Ableton" / "User Library"


def library_dirs() -> list[Path]:
    """Folders searched for root "samples": each Live app's Core Library samples, Factory Packs, User Library."""
    home = Path.home()
    apps = sorted(p for d in (Path("/Applications"), home / "Applications") for p in d.glob("Ableton Live *.app"))
    return [app / CORE_SAMPLES for app in apps] + [home / "Music" / "Ableton" / "Factory Packs", user_library()]


def _audio_file(path: Path) -> Path:
    if path.is_dir():
        raise ValueError(f"{path} is a folder; browse into it and pick a file")
    if not path.is_file():
        raise ValueError(f"No file at {path}")
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"{path.name} is not an audio file; use one of {', '.join(AUDIO_EXTENSIONS)}")
    return path


def _find(name: str) -> list[Path]:
    # ponytail: full walk per call (~1 s worst case); cache when it measurably hurts.
    found = {}  # key -> path; the same Core Library file in two Live installs counts once
    for top in library_dirs():
        for folder, _, files in os.walk(top):
            if name in files:
                path = Path(folder, name)
                key = path.relative_to(top) if top.parts[-len(CORE_SAMPLES.parts):] == CORE_SAMPLES.parts else path
                found.setdefault(key, path)
    return sorted(found.values())


def resolve(root: str | None = None, path: list[str] | None = None, file_path: str | None = None) -> Path:
    """Exactly one form: root ("samples" or "user_library") + path as browse_library shows it, or an absolute
    file_path. Returns an existing audio file. Raises ValueError with advice the agent can act on."""
    if (root is None) == (file_path is None) or (file_path is not None and path):
        raise ValueError("Give either root and path (as browse_library shows them) or file_path, not both")
    if file_path is not None:
        p = Path(file_path).expanduser()
        if not p.is_absolute():
            raise ValueError(f"file_path must be an absolute path, got {file_path!r}")
        return _audio_file(p)
    if not path or not all(isinstance(n, str) and n for n in path):
        raise ValueError("path must list the folder and file names, ending at a file")
    if root == "user_library":
        return _audio_file(user_library().joinpath(*path))
    if root != "samples":
        raise ValueError(f"root must be 'samples' or 'user_library', got {root!r}")
    name = path[-1]
    if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:  # also keeps .asd analysis files out
        raise ValueError(f"{name} is not an audio file; use one of {', '.join(AUDIO_EXTENSIONS)}")
    matches = _find(name)
    if not matches:
        raise ValueError(f"No file named {name!r} in Live's library folders; pass its full path as file_path")
    if len(matches) > 1:
        listed = "\n".join(str(m) for m in matches[:MAX_LISTED])
        more = f"\n...and {len(matches) - MAX_LISTED} more" if len(matches) > MAX_LISTED else ""
        raise ValueError(f"{len(matches)} files are named {name!r}; pass one as file_path:\n{listed}{more}")
    return matches[0]


ONE_SHOT_MAX_S = 2.0
BPM_RE = re.compile(r"(?<!\d)(\d{2,3})\s*-?\s*bpm", re.IGNORECASE)
# A tone with an accidental and/or a mode: "F#", "Bb", "Am", "C maj". A bare letter ("Pad A") is too often a variant.
KEY_RE = re.compile(r"(?<![A-Za-z0-9])([A-G])([#b]?)[\s_-]?((?i:major|maj|minor|min)|m|)(?![A-Za-z0-9#])")
MODES = {"m": "minor", "min": "minor", "minor": "minor", "maj": "major", "major": "major"}
ROLE_WORDS = {
    "kick": "kick", "kicks": "kick", "snare": "snare", "snares": "snare", "clap": "clap", "claps": "clap",
    "hat": "hat", "hats": "hat", "hihat": "hat", "hihats": "hat", "hh": "hat",
    "perc": "perc", "percs": "perc", "percussion": "perc", "bass": "bass", "808": "bass",
    "vocal": "vocal", "vocals": "vocal", "vox": "vocal", "pad": "pad", "pads": "pad",
    "chord": "chord", "chords": "chord", "lead": "lead", "leads": "lead", "fx": "fx", "riser": "fx",
}


def _duration(path: Path) -> float | None:
    """Seconds, from stdlib wave for integer-PCM WAV; None for anything it cannot read."""
    if path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path)) as w:
            return round(w.getnframes() / w.getframerate(), 3)
    except (wave.Error, EOFError, OSError, ZeroDivisionError):
        return None


def _key(stem: str) -> str | None:
    for tone, accidental, mode in KEY_RE.findall(stem):
        if accidental or mode:
            return f"{tone}{accidental} {MODES[mode.lower()]}" if mode else tone + accidental
    return None


def describe(path: Path) -> dict:
    """A file's path, name and duration, plus guesses from its filename at bpm, key, role and kind."""
    stem = path.stem
    words = re.findall(r"[a-z]+|\d+", stem.lower())
    bpm = BPM_RE.search(stem)
    duration = _duration(path)
    if "loop" in words or "loops" in words or bpm:
        kind = "loop"
    elif duration is not None and duration < ONE_SHOT_MAX_S:
        kind = "one_shot"
    else:
        kind = "unknown"
    return {
        "path": str(path),
        "name": path.name,
        "duration_s": duration,
        "bpm": int(bpm.group(1)) if bpm else None,
        "key": _key(stem),
        "role": next((ROLE_WORDS[w] for w in words if w in ROLE_WORDS), None),
        "kind": kind,
    }


def scan(folder: str, limit: int = 200) -> dict:
    """Audio files under an absolute folder, recursively, sorted by path. Skips hidden files and folders and
    Live's .asd files. Returns {total, files} with at most `limit` files. Raises ValueError with advice."""
    top = Path(folder).expanduser()
    if not top.is_absolute():
        raise ValueError(f"folder must be an absolute path, got {folder!r}")
    if not top.is_dir():
        raise ValueError(f"No folder at {top}")
    if limit < 1:
        raise ValueError(f"limit must be 1 or more, got {limit}")
    found = []
    for parent, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        found += [Path(parent, f) for f in files if not f.startswith(".") and Path(f).suffix.lower() in AUDIO_EXTENSIONS]
    if not found:
        raise ValueError(f"No audio files ({', '.join(AUDIO_EXTENSIONS)}) in {top}")
    found.sort()
    return {"total": len(found), "files": [describe(p) for p in found[:limit]]}
