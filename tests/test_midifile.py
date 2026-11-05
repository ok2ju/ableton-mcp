import struct

import pytest

from ableton_mcp.midifile import PPQ, write_midi_file

NOTES = [{"pitch": 36 + i, "start_time": i, "duration": 0.5, "velocity": 100} for i in range(4)]


def _events(data):
    """Parse the single MTrk into (abs_tick, status, data1) tuples for channel events."""
    assert data[:4] == b"MThd"
    _, fmt, ntrks, ppq = struct.unpack(">IHHH", data[4:14])
    assert (fmt, ntrks, ppq) == (0, 1, PPQ)
    assert data[14:18] == b"MTrk"
    assert data.count(b"MTrk") == 1
    (length,) = struct.unpack(">I", data[18:22])
    body, i, tick, out = data[22 : 22 + length], 0, 0, []
    while i < len(body):
        delta = 0
        while True:
            b = body[i]
            i += 1
            delta = (delta << 7) | (b & 0x7F)
            if not b & 0x80:
                break
        tick += delta
        status = body[i]
        if status == 0xFF:
            i += 3 + body[i + 2]
        else:
            out.append((tick, status & 0xF0, body[i + 1]))
            i += 3
    return out


def test_four_notes_at_120(tmp_path):
    path = write_midi_file("beat", NOTES, 120, directory=tmp_path)
    data = path.read_bytes()
    assert b"\xff\x51\x03\x07\xa1\x20" in data  # 500000 us/quarter = 120 BPM
    ev = _events(data)
    ons = [(t, p) for t, s, p in ev if s == 0x90]
    offs = [(t, p) for t, s, p in ev if s == 0x80]
    assert ons == [(i * PPQ, 36 + i) for i in range(4)]
    assert offs == [(i * PPQ + PPQ // 2, 36 + i) for i in range(4)]


@pytest.mark.parametrize("bad", ["../x.mid", "../x", "a/b", "/abs", "..", ""])
def test_rejects_traversal(tmp_path, bad):
    target = tmp_path / "midi"
    with pytest.raises(ValueError):
        write_midi_file(bad, NOTES, 120, directory=target)
    assert not (tmp_path / "x.mid").exists()
    assert list(tmp_path.rglob("*.mid")) == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 600_000.0])
def test_rejects_unwritable_times(tmp_path, bad):
    with pytest.raises(ValueError):
        write_midi_file("t", [{"pitch": 36, "start_time": bad, "duration": 1}], 120, directory=tmp_path)
