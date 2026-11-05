"""Write Standard MIDI Files (type 0) with the stdlib only."""

import math
import re
import struct
from pathlib import Path

MIDI_DIR = Path.home() / "Music" / "ableton-mcp" / "midi"
PPQ = 480  # ticks per quarter note (one beat)
MAX_TICK = 0x0FFFFFFF  # largest delta a 4-byte VLQ can hold
_SAFE_NAME = re.compile(r"[A-Za-z0-9 _-]{1,64}")


def _vlq(value: int) -> bytes:
    """Encode a MIDI variable-length quantity."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def build_smf(notes: list[dict], bpm: float, channel: int = 0) -> bytes:
    """Return SMF type 0 bytes for notes given in beats.

    Each note: {"pitch": 0-127, "start_time": beats, "duration": beats, "velocity": 1-127 (default 100)}.
    """
    if not 20 <= bpm <= 999:
        raise ValueError("bpm must be between 20 and 999")
    events = []  # (tick, order, bytes); order puts note-off before note-on at the same tick
    for n in notes:
        pitch, vel = int(n["pitch"]), int(n.get("velocity", 100))
        start, dur = float(n["start_time"]), float(n["duration"])
        if not (0 <= pitch <= 127 and 1 <= vel <= 127 and math.isfinite(start) and math.isfinite(dur)
                and start >= 0 and dur > 0):
            raise ValueError(f"invalid note: {n}")
        on = round(start * PPQ)
        off = max(on + 1, round((start + dur) * PPQ))
        if off > MAX_TICK:
            raise ValueError(f"note ends too late for a MIDI file: {n}")
        events.append((on, 1, bytes([0x90 | channel, pitch, vel])))
        events.append((off, 0, bytes([0x80 | channel, pitch, 0])))
    events.sort(key=lambda e: (e[0], e[1]))

    tempo = round(60_000_000 / bpm)  # microseconds per quarter note
    track = bytearray(b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big"))
    last = 0
    for tick, _, data in events:
        track += _vlq(tick - last) + data
        last = tick
    track += b"\x00\xff\x2f\x00"  # end of track
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ)
    return header + b"MTrk" + struct.pack(">I", len(track)) + bytes(track)


def write_midi_file(name: str, notes: list[dict], bpm: float, directory: Path = MIDI_DIR) -> Path:
    """Write notes to <directory>/<name>.mid and return the path. Rejects unsafe names and never overwrites."""
    stem = name[:-4] if name.lower().endswith(".mid") else name
    if not _SAFE_NAME.fullmatch(stem):
        raise ValueError("name may only contain letters, digits, space, '_' and '-' (max 64)")
    data = build_smf(notes, bpm)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.mid"
    if path.resolve().parent != directory.resolve():
        raise ValueError("name escapes the MIDI directory")
    if path.exists():  # no undo for files, so never replace one
        raise ValueError(f"{path.name} already exists; pick another name")
    path.write_bytes(data)
    return path
