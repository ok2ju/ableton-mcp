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
