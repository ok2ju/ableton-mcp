import wave

import pytest

from ableton_mcp import samples

ATMOS = "Atmos Calm 130 bpm.wav"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    core = tmp_path / "core"
    packs = tmp_path / "packs"
    user = tmp_path / "user"
    nested = core / "Loops" / "Tonal" / "Synth"
    nested.mkdir(parents=True)
    (nested / ATMOS).write_bytes(b"RIFF")
    (nested / (ATMOS + ".asd")).write_bytes(b"x")
    (user / "Samples" / "Mine").mkdir(parents=True)
    (user / "Samples" / "Mine" / "kick.aif").write_bytes(b"FORM")
    (user / "notes.txt").write_text("hi")
    packs.mkdir()
    monkeypatch.setattr(samples, "library_dirs", lambda: [core, packs, user])
    monkeypatch.setattr(samples, "user_library", lambda: user)
    return tmp_path


def test_samples_name_resolves_to_nested_file(lib):
    assert samples.resolve("samples", [ATMOS]) == lib / "core" / "Loops" / "Tonal" / "Synth" / ATMOS


def test_asd_is_never_returned(lib):
    with pytest.raises(ValueError, match="not an audio file"):
        samples.resolve("samples", [ATMOS + ".asd"])


def test_user_library_path_is_joined(lib):
    assert samples.resolve("user_library", ["Samples", "Mine", "kick.aif"]) == lib / "user" / "Samples" / "Mine" / "kick.aif"


def test_user_library_folder_refused(lib):
    with pytest.raises(ValueError, match="browse into it"):
        samples.resolve("user_library", ["Samples"])


def test_file_path_checks(lib):
    assert samples.resolve(file_path=str(lib / "user" / "Samples" / "Mine" / "kick.aif")).name == "kick.aif"
    with pytest.raises(ValueError, match="absolute"):
        samples.resolve(file_path="kick.aif")
    with pytest.raises(ValueError, match="No file"):
        samples.resolve(file_path=str(lib / "missing.wav"))
    with pytest.raises(ValueError, match="not an audio file"):
        samples.resolve(file_path=str(lib / "user" / "notes.txt"))


def test_exactly_one_form(lib):
    with pytest.raises(ValueError, match="either"):
        samples.resolve()
    with pytest.raises(ValueError, match="either"):
        samples.resolve("samples", [ATMOS], file_path="/x.wav")


def test_not_found_suggests_file_path(lib):
    with pytest.raises(ValueError, match="file_path"):
        samples.resolve("samples", ["Nope.wav"])


def test_same_core_file_in_two_live_apps_counts_once(tmp_path, monkeypatch):
    apps = [tmp_path / name / samples.CORE_SAMPLES for name in ("Ableton Live 11.app", "Ableton Live 12 Lite.app")]
    for core in apps:
        (core / "Loops").mkdir(parents=True)
        (core / "Loops" / ATMOS).write_bytes(b"RIFF")
    monkeypatch.setattr(samples, "library_dirs", lambda: apps)
    assert samples.resolve("samples", [ATMOS]) == apps[0] / "Loops" / ATMOS


def test_same_name_twice_lists_both(lib):
    dup = lib / "packs" / "Pack" / ATMOS
    dup.parent.mkdir()
    dup.write_bytes(b"RIFF")
    with pytest.raises(ValueError) as e:
        samples.resolve("samples", [ATMOS])
    assert "2 files" in str(e.value) and str(dup) in str(e.value)
    assert str(lib / "core" / "Loops" / "Tonal" / "Synth" / ATMOS) in str(e.value)


def _wav(path, seconds, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "Loops").mkdir()
    _wav(tmp_path / "Kick 01.wav", 0.5)
    _wav(tmp_path / "Loops" / "Dusty Loop 92 BPM Am.wav", 4.0)
    _wav(tmp_path / "Pad A.wav", 1.0)
    (tmp_path / "Kick 01.wav.asd").write_bytes(b"x")
    (tmp_path / ".hidden.wav").write_bytes(b"RIFF")
    (tmp_path / "notes.txt").write_text("hi")
    return tmp_path


def test_scan_lists_audio_with_filename_guesses(folder):
    result = samples.scan(str(folder))
    assert result["total"] == 3
    kick, loop, pad = result["files"]  # sorted by path: "Kick 01.wav" < "Loops/..." < "Pad A.wav"
    assert [kick["name"], loop["name"], pad["name"]] == ["Kick 01.wav", "Dusty Loop 92 BPM Am.wav", "Pad A.wav"]
    assert (kick["role"], kick["kind"]) == ("kick", "one_shot")
    assert (loop["bpm"], loop["key"], loop["kind"]) == (92, "A minor", "loop")
    assert pad["key"] is None and pad["role"] == "pad"
    assert kick["path"] == str(folder / "Kick 01.wav")


def test_scan_duration_from_pcm_wav(folder):
    pad = next(f for f in samples.scan(str(folder))["files"] if f["name"] == "Pad A.wav")
    assert pad["duration_s"] == 1.0


def test_duration_null_for_unreadable_or_non_wav(tmp_path):
    (tmp_path / "x.wav").write_bytes(b"not a wav")
    (tmp_path / "y.mp3").write_bytes(b"ID3")
    assert samples._duration(tmp_path / "x.wav") is None
    assert samples._duration(tmp_path / "y.mp3") is None


def test_scan_limit_keeps_total(folder):
    result = samples.scan(str(folder), limit=1)
    assert result["total"] == 3 and len(result["files"]) == 1


@pytest.mark.parametrize("name, key", [
    ("F# Lead", "F#"), ("Bb_bass", "Bb"), ("C maj chord", "C major"), ("Ab-minor", "Ab minor"),
    ("Pad A", None), ("FM Lead", None), ("Bass 808", None),
])
def test_key_needs_accidental_or_mode(name, key):
    assert samples._key(name) == key


def test_scan_errors(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        samples.scan("Samples")
    with pytest.raises(ValueError, match="No folder"):
        samples.scan(str(tmp_path / "missing"))
    (tmp_path / "notes.txt").write_text("hi")
    with pytest.raises(ValueError, match="No audio files"):
        samples.scan(str(tmp_path))
