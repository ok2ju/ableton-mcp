import base64
import json
import math

import pytest

from ableton_mcp import vital


def test_build_preset_minimal():
    p = vital.build_preset("Pad", {"osc_1_unison_voices": 7}, [], [], "1.5.5")
    s = p["settings"]
    assert p["synth_version"] == "1.5.5"
    assert p["preset_name"] == "Pad"
    assert s["osc_1_unison_voices"] == 7
    assert [w["name"] for w in s["wavetables"]] == ["Saw", "Saw", "Saw"]
    assert len(s["lfos"]) == 8
    assert s["sample"]["length"] == 64
    assert len(base64.b64decode(s["sample"]["samples"])) == 64 * 2  # int16
    assert s["modulations"] == []
    json.dumps(p, allow_nan=False)  # plain JSON Vital can parse


def test_unknown_control_suggests_close_name():
    with pytest.raises(ValueError, match="osc_1_unison_voices"):
        vital.build_preset("x", {"osc_1_unison_voice": 7}, [], [], "1.5.5")


@pytest.mark.parametrize("value", [200, -1, math.nan, math.inf])
def test_out_of_range_control_gives_range(value):
    with pytest.raises(ValueError, match="8–136"):
        vital.build_preset("x", {"filter_1_cutoff": value}, [], [], "1.5.5")


def test_modulation_route_and_amount():
    route = {"source": "lfo_1", "destination": "filter_1_cutoff", "amount": 0.2}
    s = vital.build_preset("x", {}, [route], [], "1.5.5")["settings"]
    assert s["modulations"][0] == {"source": "lfo_1", "destination": "filter_1_cutoff"}
    assert s["modulation_1_amount"] == 0.2


@pytest.mark.parametrize("route, message", [
    ({"source": "lfo_9", "destination": "filter_1_cutoff", "amount": 0.2}, "unknown modulation source"),
    ({"source": "lfo_1", "destination": "filter_1_cutof", "amount": 0.2}, "filter_1_cutoff"),
    ({"source": "lfo_1", "destination": "filter_1_cutoff", "amount": 1.5}, "between -1 and 1"),
    ({"source": "lfo_1", "destination": "filter_1_on", "amount": 0.5}, "cannot modulate"),
    ({"source": "lfo_1", "destination": "polyphony", "amount": 0.5}, "cannot modulate"),
])
def test_bad_modulation_is_error(route, message):
    with pytest.raises(ValueError, match=message):
        vital.build_preset("x", {}, [route], [], "1.5.5")


def test_too_many_modulations():
    route = {"source": "lfo_1", "destination": "filter_1_cutoff", "amount": 0.1}
    with pytest.raises(ValueError, match="at most 64"):
        vital.build_preset("x", {}, [route] * 65, [], "1.5.5")


def test_waves_fill_oscillators_in_order():
    s = vital.build_preset("x", {}, [], ["sine", "square"], "1.5.5")["settings"]
    assert [w["name"] for w in s["wavetables"]] == ["Sine", "Square", "Saw"]
    with pytest.raises(ValueError, match="unknown wave"):
        vital.build_preset("x", {}, [], ["noise"], "1.5.5")


def test_waves_are_valid_line_generators():
    for w in vital.WAVES.values():
        assert len(w["points"]) == 2 * w["num_points"] == 2 * len(w["powers"])
        assert all(0 <= v <= 1 for v in w["points"])
    assert vital.WAVES["sine"]["num_points"] == 33


def test_write_preset_never_overwrites(tmp_path):
    preset = vital.build_preset("Pad", {}, [], [], "1.5.5")
    path = vital.write_preset("Pad", preset, tmp_path / "User" / "Presets")
    assert path == tmp_path / "User" / "Presets" / "Pad.vital"
    before = path.read_bytes()
    assert json.loads(before)["preset_name"] == "Pad"

    with pytest.raises(ValueError, match="already exists"):
        vital.write_preset("Pad", vital.build_preset("Pad", {"polyphony": 2}, [], [], "1.5.5"), path.parent)
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["../x", "a/b", "", "x" * 65])
def test_write_preset_rejects_unsafe_names(tmp_path, name):
    with pytest.raises(ValueError, match="name may only contain"):
        vital.write_preset(name, {}, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_config_reads_vital_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "Library" / "Application Support" / "vital" / "Vital.config"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"synth_version": "1.6.0", "data_directory": "/data/vital"}))
    assert vital.config() == ("1.6.0", vital.Path("/data/vital"))


@pytest.mark.parametrize("content", [None, "not json", "[]"])
def test_config_falls_back(monkeypatch, tmp_path, content):
    monkeypatch.setenv("HOME", str(tmp_path))
    if content is not None:
        cfg = tmp_path / "Library" / "Application Support" / "vital" / "Vital.config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(content)
    assert vital.config() == ("1.5.5", tmp_path / "Music" / "Vital")


def test_read_preset_round_trip(tmp_path):
    controls = {"osc_1_unison_voices": 7, "filter_1_cutoff": 76}
    routes = [{"source": "lfo_1", "destination": "filter_1_cutoff", "amount": 0.2},
              {"source": "env_2", "destination": "osc_1_level", "amount": -0.5}]
    vital.write_preset("Pad", vital.build_preset("Pad", controls, routes, ["sine"], "1.5.5"), tmp_path)

    for ref in ("Pad", "Pad.vital", str(tmp_path / "Pad.vital")):
        got = vital.read_preset(ref, tmp_path)
        assert got["name"] == "Pad"
        assert got["synth_version"] == "1.5.5"
        assert got["controls"] == controls
        assert got["modulations"] == routes
        assert got["wavetables"] == ["Sine", "Saw", "Saw"]


def test_read_preset_skips_empty_slots_and_uses_file_name(tmp_path):
    settings = {"polyphony": 4, "modulation_2_amount": 0.3, "modulations": [
        {"source": "", "destination": ""}, {"source": "velocity", "destination": "filter_1_cutoff"}]}
    (tmp_path / "Factory Bass.vital").write_text(json.dumps({"synth_version": "1.0.0", "settings": settings}))

    got = vital.read_preset("Factory Bass", tmp_path)
    assert got["name"] == "Factory Bass"
    assert got["controls"] == {"polyphony": 4}
    assert got["modulations"] == [{"source": "velocity", "destination": "filter_1_cutoff", "amount": 0.3}]
    assert got["wavetables"] == []


@pytest.mark.parametrize("content", [b"not json", b"[]", b'{"synth_version": "1.5.5"}', b"\xff\xfe"])
def test_read_preset_rejects_non_presets(tmp_path, content):
    (tmp_path / "x.vital").write_bytes(content)
    with pytest.raises(ValueError, match="not a Vital preset"):
        vital.read_preset("x", tmp_path)


def test_read_preset_bad_refs(tmp_path):
    with pytest.raises(ValueError, match="no preset at"):
        vital.read_preset("missing", tmp_path)
    with pytest.raises(ValueError, match="must end in .vital"):
        vital.read_preset("/etc/passwd", tmp_path)


class FakeProcesses:
    def __init__(self, running):
        self.running = running
        self.popens = []

    def run(self, cmd, **kwargs):
        assert cmd == ["pgrep", "-x", "Vital"]
        return vital.subprocess.CompletedProcess(cmd, 0 if self.running else 1)

    def popen(self, cmd, **kwargs):
        self.popens.append((cmd, kwargs))


@pytest.mark.parametrize("running", [True, False])
def test_launch_only_when_vital_is_closed(monkeypatch, tmp_path, running):
    binary = tmp_path / "Vital"
    binary.write_text("")
    monkeypatch.setattr(vital, "VITAL_BINARY", binary)
    fake = FakeProcesses(running)
    monkeypatch.setattr(vital.subprocess, "run", fake.run)
    monkeypatch.setattr(vital.subprocess, "Popen", fake.popen)

    assert vital.launch(tmp_path / "Pad.vital") is (not running)
    if running:
        assert fake.popens == []
    else:
        [(cmd, kwargs)] = fake.popens
        assert cmd == [str(binary), str(tmp_path / "Pad.vital")]
        assert kwargs["start_new_session"] is True


def test_launch_without_vital_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(vital, "VITAL_BINARY", tmp_path / "missing")
    monkeypatch.setattr(vital.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start"))
    assert vital.launch(tmp_path / "Pad.vital") is False


def test_launch_os_error_means_not_loaded(monkeypatch, tmp_path):
    monkeypatch.setattr(vital, "VITAL_BINARY", tmp_path)  # exists
    monkeypatch.setattr(vital.subprocess, "run", lambda *a, **k: vital.subprocess.CompletedProcess(a[0], 1))

    def popen(*a, **k):
        raise PermissionError("not executable")
    monkeypatch.setattr(vital.subprocess, "Popen", popen)
    assert vital.launch(tmp_path / "Pad.vital") is False
