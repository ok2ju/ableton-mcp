"""Turn a sample named the way browse_library shows it into a file path Live can load.

Live's browser items carry a name and a uri, never a file path, and the `samples` root is a flat list of
file names whose files sit in nested folders on disk. So the server finds the file itself, by exact name,
in Live's own library folders. Every path comes from Path.home() at call time, so tests can point it elsewhere.
"""

import os
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
