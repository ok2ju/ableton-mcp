import pytest

librosa = pytest.importorskip("librosa")
import numpy as np  # noqa: E402  (numpy and soundfile come with librosa)
import soundfile  # noqa: E402

from ableton_mcp import hearing  # noqa: E402

SR = 22050


def _write(path, y):
    soundfile.write(str(path), y.astype(np.float32), SR)
    return str(path)


def test_bpm_of_120_bpm_click(tmp_path):
    y = librosa.clicks(times=np.arange(0, 10, 0.5), sr=SR, length=10 * SR)
    result = hearing.analyze(_write(tmp_path / "click.wav", y))
    assert any(abs(b - 120) <= 2 for b in (result["bpm"], result["bpm_half"], result["bpm_double"])), result
    assert result["duration_s"] == 10.0


def test_key_of_a_minor_triad(tmp_path):
    t = np.arange(4 * SR) / SR
    y = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 261.63, 329.63)) / 3  # A3 C4 E4
    result = hearing.analyze(_write(tmp_path / "triad.wav", y))
    assert result["key"] == "A minor", result
    assert 0 < result["key_confidence"] <= 1


def test_unreadable_file_raises_value_error(tmp_path):
    (tmp_path / "junk.wav").write_bytes(b"not audio")
    with pytest.raises(ValueError):
        hearing.analyze(str(tmp_path / "junk.wav"))


def test_silence_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="silent"):
        hearing.analyze(_write(tmp_path / "silence.wav", np.zeros(SR)))
