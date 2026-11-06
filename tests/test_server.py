import asyncio
import json
import re

import pytest
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from ableton_mcp import lessons, midifile, server, theory
from ableton_mcp.live_client import CONTROL_SURFACE_HINT, LiveError, LiveUnavailable
from AbletonMCP import ops

TOOL_NAMES = [
    "live_status",
    "get_set_overview",
    "create_midi_track",
    "create_midi_clip",
    "delete_track",
    "delete_clip",
    "create_scene",
    "copy_clip",
    "write_notes",
    "read_notes",
    "set_scale",
    "create_drum_pattern",
    "create_bassline",
    "create_chord_progression",
    "browse_library",
    "load_from_library",
    "load_instrument",
    "read_device",
    "set_device_parameters",
    "set_track_mix",
    "set_label",
    "set_tempo",
    "play_clip",
    "fire_scene",
    "stop",
    "undo_last",
    "write_midi_file",
]
READ_ONLY_TOOLS = {"live_status", "get_set_overview", "read_notes", "browse_library", "read_device"}
DESTRUCTIVE_TOOLS = {
    "write_notes", "delete_track", "delete_clip",
    "create_drum_pattern", "create_bassline", "create_chord_progression",
}


class FakeClient:
    """Records every call. Answers from `results`, or raises `error` if set."""

    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error
        self.calls = []

    def call(self, op, args=None):
        self.calls.append((op, args))
        if self.error is not None:
            raise self.error
        return self.results[op]


def call_tool(name, args=None):
    return asyncio.run(server.mcp.call_tool(name, args or {}))


def test_tools_list_is_fixed_and_annotated():
    first = asyncio.run(server.mcp.list_tools())
    second = asyncio.run(server.mcp.list_tools())
    assert [t.name for t in first] == TOOL_NAMES
    assert [t.name for t in second] == TOOL_NAMES
    for t in first:
        assert t.annotations.open_world_hint is False
        assert t.annotations.read_only_hint is (t.name in READ_ONLY_TOOLS)
        # Read-only tools leave destructive_hint unset (None); MCP only reads it when read_only_hint is false.
        assert (t.annotations.destructive_hint is True) is (t.name in DESTRUCTIVE_TOOLS), t.name


def test_protocol_versions_match():
    assert server.PROTOCOL_VERSION == ops.PROTOCOL_VERSION


def test_live_status_old_script_is_error(monkeypatch):
    monkeypatch.setattr(server, "client", FakeClient({"hello": {"protocol_version": 1, "variant": "Lite"}}))

    result = call_tool("live_status")

    assert result.is_error is True
    assert "protocol 1" in result.content[0].text and "install-script" in result.content[0].text


def read_resource(uri):
    (content,) = asyncio.run(server.mcp.read_resource(uri))
    return content


@pytest.mark.parametrize("variant, tracks, scenes", [("Lite", 8, 16), ("Suite", None, None)])
def test_live_status_and_capabilities_give_limits(monkeypatch, variant, tracks, scenes):
    hello = {"protocol_version": server.PROTOCOL_VERSION, "version": "12.1.10", "variant": variant}
    monkeypatch.setattr(server, "client", FakeClient({"hello": hello}))
    expected = {**hello, "limits": {"audio_midi_tracks": tracks, "scenes": scenes}}

    result = call_tool("live_status")
    resource = read_resource("live://capabilities")

    assert result.is_error is False and result.structured_content == expected
    assert resource.mime_type == "application/json" and json.loads(resource.content) == expected


def test_resources_list():
    resources = asyncio.run(server.mcp.list_resources())
    assert {str(r.uri): r.mime_type for r in resources} == {
        "live://capabilities": "application/json",
        "live://set/overview": "application/json",
    }


def test_set_overview_resource(monkeypatch):
    overview = {"tempo": 120.0, "tracks": []}
    monkeypatch.setattr(server, "client", FakeClient({"get_overview": overview}))

    assert json.loads(read_resource("live://set/overview").content) == overview


def test_resources_keep_error_hints(monkeypatch):
    monkeypatch.setattr(server, "client", FakeClient(error=LiveUnavailable(CONTROL_SURFACE_HINT)))
    with pytest.raises(ResourceError, match="Control Surface"):
        read_resource("live://set/overview")

    monkeypatch.setattr(server, "client", FakeClient({"hello": {"protocol_version": 1, "variant": "Lite"}}))
    with pytest.raises(ResourceError, match="install-script"):
        read_resource("live://capabilities")


def test_unknown_op_gets_install_hint(monkeypatch):
    monkeypatch.setattr(server, "client", FakeClient(error=LiveError("INVALID_ARG", "unknown op: get_notes")))

    result = call_tool("read_notes", {"track": 0, "slot": 0})

    assert result.is_error is True
    assert "install-script" in result.content[0].text


def test_create_midi_track_refuses_ninth_track_in_lite(monkeypatch):
    tracks = [{"index": i, "name": str(i), "type": "midi" if i % 2 else "audio"} for i in range(8)]
    tracks.append({"index": 8, "name": "Group", "type": "group"})
    fake = FakeClient({"hello": {"variant": "Lite"}, "get_overview": {"tracks": tracks}})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_midi_track", {"name": "Drums"})

    assert result.is_error is True
    assert "Lite allows 8 tracks" in result.content[0].text and "delete_track" in result.content[0].text
    assert [op for op, _ in fake.calls] == ["hello", "get_overview"]


def test_create_midi_track_no_limit_outside_lite(monkeypatch):
    tracks = [{"index": i, "name": str(i), "type": "midi"} for i in range(8)]
    fake = FakeClient(
        {
            "hello": {"variant": "Standard"},
            "get_overview": {"tracks": tracks},
            "create_midi_track": {"index": 8, "name": "Drums"},
        }
    )
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_midi_track", {"name": "Drums"})

    assert result.is_error is False
    assert fake.calls[-1][0] == "create_midi_track"


def test_create_midi_track_passes_through_below_limit(monkeypatch):
    fake = FakeClient(
        {
            "hello": {"variant": "Lite"},
            "get_overview": {"tracks": [{"index": 0, "name": "1", "type": "midi"}]},
            "create_midi_track": {"index": 1, "name": "Drums"},
        }
    )
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_midi_track", {"name": "Drums"})

    assert result.is_error is False
    assert result.structured_content == {"index": 1, "name": "Drums"}
    assert fake.calls[-1] == ("create_midi_track", {"index": -1, "name": "Drums"})


def test_get_set_overview_with_live_down_is_error(monkeypatch):
    monkeypatch.setattr(server, "client", FakeClient(error=LiveUnavailable(CONTROL_SURFACE_HINT)))

    result = call_tool("get_set_overview")

    assert result.is_error is True
    assert "Control Surface" in result.content[0].text


def test_write_notes_sends_plain_note_dicts(monkeypatch):
    fake = FakeClient({"replace_notes": {"track": 0, "slot": 0, "note_count": 1}})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool(
        "write_notes", {"track": 0, "slot": 0, "notes": [{"pitch": 36, "start_time": 0, "duration": 0.25}]}
    )

    assert result.is_error is False
    assert fake.calls == [
        (
            "replace_notes",
            {"track": 0, "slot": 0, "notes": [{"pitch": 36, "start_time": 0.0, "duration": 0.25, "velocity": 100}]},
        )
    ]


def test_write_midi_file_writes_locally_without_live(monkeypatch, tmp_path):
    fake = FakeClient()
    monkeypatch.setattr(server, "client", fake)
    monkeypatch.setattr(midifile, "MIDI_DIR", tmp_path)
    notes = [{"pitch": 36, "start_time": i, "duration": 0.5} for i in range(4)]

    result = call_tool("write_midi_file", {"name": "beat", "notes": notes, "bpm": 120})

    assert result.is_error is False
    assert (tmp_path / "beat.mid").read_bytes().startswith(b"MThd")
    assert fake.calls == []


def test_write_midi_file_bad_name_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(midifile, "MIDI_DIR", tmp_path)

    result = call_tool("write_midi_file", {"name": "../x", "notes": [], "bpm": 120})

    assert result.is_error is True
    assert list(tmp_path.iterdir()) == []


def test_write_midi_file_os_error_is_error(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setattr(midifile, "MIDI_DIR", blocker / "midi")  # mkdir under a file fails

    result = call_tool(
        "write_midi_file", {"name": "x", "notes": [{"pitch": 36, "start_time": 0, "duration": 1}]}
    )

    assert result.is_error is True
    assert "Could not write the MIDI file" in result.content[0].text


def test_protocol_version_is_5():
    assert server.PROTOCOL_VERSION == ops.PROTOCOL_VERSION == 5


PADS_808 = [
    {"note": 36, "name": "Bass Drum"}, {"note": 37, "name": "Rim Shot"},
    {"note": 38, "name": "Snare Drum"}, {"note": 39, "name": "Hand Clap"},
    {"note": 42, "name": "Closed Hi Hat"}, {"note": 46, "name": "Open Hi Hat"},
    {"note": 49, "name": "Cymbal"}, {"note": 51, "name": "Claves"},
]


def overview(drum_pads=None, numerator=4, scale=None):
    return {
        "signature_numerator": numerator,
        "signature_denominator": 4,
        "scale": scale or {"root_note": 0, "name": "Major"},
        "tracks": [{"index": 0, "name": "Kit", "type": "midi", "drum_pads": drum_pads}],
    }


def write_clip_echo(args):
    return {"track": args["track"], "slot": args["slot"], "name": args["name"], "length": args["length_beats"],
            "note_count": len(args["notes"]), "notes_outside_clip": 0, "replaced": False}


class EchoClient(FakeClient):
    """FakeClient whose write_clip answer is built from the request."""

    def call(self, op, args=None):
        if op == "write_clip":
            self.calls.append((op, args))
            return write_clip_echo(args)
        return super().call(op, args)


def test_create_drum_pattern_writes_to_kit_pads(monkeypatch):
    fake = EchoClient({"get_overview": overview(PADS_808)})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_drum_pattern", {"style": "house", "track": 0, "slot": 1})

    assert result.is_error is False, result.content[0].text
    assert [op for op, _ in fake.calls] == ["get_overview", "write_clip"]
    written = fake.calls[1][1]
    assert written["slot"] == 1 and written["length_beats"] == 4
    assert {n["pitch"] for n in written["notes"]} <= {p["note"] for p in PADS_808}
    assert result.structured_content["voices"]["kick"]["pad"] == "Bass Drum"
    assert result.structured_content["missing_voices"] == []


def test_create_drum_pattern_layer(monkeypatch):
    fake = EchoClient({"get_overview": overview(PADS_808)})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_drum_pattern", {
        "style": None, "layers": [{"voice": "kick", "pulses": 3, "steps": 8}], "track": 0, "slot": 0,
    })

    assert result.is_error is False, result.content[0].text
    assert [n["start_time"] for n in fake.calls[1][1]["notes"]] == [0, 0.75, 1.5, 2, 2.75, 3.5]


def test_create_drum_pattern_needs_drum_rack(monkeypatch):
    fake = EchoClient({"get_overview": overview(None)})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_drum_pattern", {"track": 0, "slot": 0})

    assert result.is_error is True and "load_from_library" in result.content[0].text
    assert [op for op, _ in fake.calls] == ["get_overview"]


def test_generators_refuse_non_4_4(monkeypatch):
    fake = EchoClient({"get_overview": overview(PADS_808, numerator=3)})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_drum_pattern", {"track": 0, "slot": 0})

    assert result.is_error is True and "4/4" in result.content[0].text
    assert [op for op, _ in fake.calls] == ["get_overview"]


def test_create_chord_progression_uses_set_scale(monkeypatch):
    fake = EchoClient({"get_overview": overview(scale={"root_note": 2, "name": "Dorian"})})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_chord_progression", {"degrees": [1, 4], "track": 0, "slot": 0})

    assert result.is_error is False, result.content[0].text
    assert result.structured_content["chords"][0] == "Dm"
    assert fake.calls[1][1]["name"] == "Dm G"


def test_create_bassline_file_needs_no_live(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "client", FakeClient(error=AssertionError("Live must not be called")))
    monkeypatch.setattr(midifile, "MIDI_DIR", tmp_path)

    ok = call_tool("create_bassline", {"pattern": "eighths", "degrees": [1, 4], "file_name": "bass", "root": "A", "scale": "Minor"})
    no_root = call_tool("create_bassline", {"file_name": "bass2", "scale": "Minor"})
    both = call_tool("create_bassline", {"file_name": "bass3", "track": 0, "slot": 0, "root": "A", "scale": "Minor"})
    neither = call_tool("create_bassline", {"root": "A", "scale": "Minor"})

    assert ok.is_error is False, ok.content[0].text
    assert (tmp_path / "bass.mid").read_bytes().startswith(b"MThd")
    assert ok.structured_content["note_count"] == 16
    assert no_root.is_error is True and both.is_error is True and neither.is_error is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bass.mid"]


def test_set_scale(monkeypatch):
    fake = FakeClient({"set_scale": {"root_note": 3, "name": "Minor Pentatonic", "intervals": [0, 3, 5, 7, 10], "scale_mode": True}})
    monkeypatch.setattr(server, "client", fake)

    ok = call_tool("set_scale", {"root": "Eb", "scale": "Minor Pentatonic"})
    bad = call_tool("set_scale", {"root": "H", "scale": "Major"})

    assert ok.is_error is False
    assert bad.is_error is True
    assert fake.calls == [("set_scale", {"root_note": 3, "scale_name": "Minor Pentatonic"})]


def test_literals_match_theory_tables():
    from typing import get_args

    assert get_args(server.ScaleName) == tuple(theory.SCALES)
    assert get_args(server.DrumStyle) == tuple(theory.DRUM_STYLES)
    assert get_args(server.DrumVoice) == tuple(theory.DRUM_VOICES)
    assert get_args(server.BassPattern) == tuple(theory.BASS_PATTERNS)
    tool = {t.name: t for t in asyncio.run(server.mcp.list_tools())}["set_scale"]
    assert "Minor Pentatonic" in json.dumps(tool.input_schema)


def test_generator_refusals_never_write(monkeypatch):
    cases = [
        ({"get_overview": overview([])}, "create_drum_pattern", {"track": 0, "slot": 0}, "no pad"),
        ({"get_overview": overview(PADS_808)}, "create_drum_pattern",
         {"style": None, "layers": [{"voice": "kick", "pulses": 9, "steps": 8}], "track": 0, "slot": 0}, "euclid"),
        ({"get_overview": overview(PADS_808)}, "create_drum_pattern", {"track": 5, "slot": 0}, "No track 5"),
        ({"get_overview": {**overview(), "scale": None}}, "create_bassline", {"track": 0, "slot": 0}, "no Set scale"),
        ({"get_overview": overview(scale={"root_note": 0, "name": "Bhairav"})}, "create_bassline",
         {"track": 0, "slot": 0}, "'Bhairav' is not supported"),
    ]
    for results, tool, args, message in cases:
        fake = EchoClient(results)
        monkeypatch.setattr(server, "client", fake)
        result = call_tool(tool, args)
        assert result.is_error is True and message in result.content[0].text, (tool, args, result.content[0].text)
        assert [op for op, _ in fake.calls] == ["get_overview"]


def test_write_clip_error_is_passed_back(monkeypatch):
    class Refuses(FakeClient):
        def call(self, op, args=None):
            if op == "write_clip":
                raise LiveError("LIVE_STATE", "that clip is not a MIDI clip")
            return super().call(op, args)

    monkeypatch.setattr(server, "client", Refuses({"get_overview": overview(PADS_808)}))

    result = call_tool("create_drum_pattern", {"track": 0, "slot": 0})

    assert result.is_error is True and "not a MIDI clip" in result.content[0].text


def test_create_drum_pattern_file_uses_gm_notes(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "client", FakeClient(error=AssertionError("Live must not be called")))
    monkeypatch.setattr(midifile, "MIDI_DIR", tmp_path)

    result = call_tool("create_drum_pattern", {"style": "rock", "file_name": "beat"})

    assert result.is_error is False, result.content[0].text
    assert result.structured_content["voices"]["kick"] == {"pitch": 36, "pad": None, "hits": 3}
    assert (tmp_path / "beat.mid").exists()


def test_create_scene_refuses_seventeenth_scene_in_lite(monkeypatch):
    for args in ({"name": "Chorus"}, {"copy_of": 0}):
        fake = FakeClient({"hello": {"variant": "Lite"}, "get_overview": {"tracks": [], "scene_count": 16}})
        monkeypatch.setattr(server, "client", fake)

        result = call_tool("create_scene", args)

        assert result.is_error is True and "Lite allows 16 scenes" in result.content[0].text, args
        assert [op for op, _ in fake.calls] == ["hello", "get_overview"]


def test_create_scene_no_limit_outside_lite(monkeypatch):
    fake = FakeClient({
        "hello": {"variant": "Standard"},
        "get_overview": {"tracks": [], "scene_count": 16},
        "create_scene": {"scene": 1, "name": "", "scene_count": 17},
    })
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_scene", {"copy_of": 0})

    assert result.is_error is False
    assert fake.calls[-1] == ("create_scene", {"copy_of": 0})


def test_set_label_maps_color_name(monkeypatch):
    fake = FakeClient({"set_label": {}})
    monkeypatch.setattr(server, "client", fake)

    ok = call_tool("set_label", {"track": 0, "color": "red"})
    # Schema validation raises ToolError here; the MCP protocol layer sends it to the client as isError.
    with pytest.raises(ToolError, match="mauve"):
        call_tool("set_label", {"track": 0, "color": "mauve"})

    assert ok.is_error is False
    assert fake.calls == [("set_label", {"track": 0, "color": server.COLORS["red"]})]


def test_set_track_mix_sends_only_given_fields(monkeypatch):
    fake = FakeClient({"set_mix": {"volume_db": 0.0, "pan": 10, "mute": False, "solo": False, "sends": {}}})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("set_track_mix", {"track": 1, "pan": 10})

    assert result.is_error is False
    assert fake.calls == [("set_mix", {"track": 1, "pan": 10})]


# (root, path) -> [(name, is_folder, is_loadable)], shaped like the real Lite browser.
BROWSE = {
    ("instruments", ("Drift",)): [("Bass", True, False), ("Pad", True, False)],
    ("instruments", ("Drift", "Bass")): [("808 Drifter.adg", False, True), ("Deep Bass.adv", False, True)],
    ("instruments", ("Drift", "Pad")): [("Warm Pad.adv", False, True)],
    ("instruments", ("Impulse",)): [("Impulse 808.adv", False, True)],
    ("drums", ()): [("Drum Hits", True, False), ("Drum Rack", False, True), ("808 Core Kit.adg", False, True)],
    ("drums", ("Drum Hits",)): [("Kick 808.wav", False, True)],
}


class BrowseClient(FakeClient):
    """FakeClient that answers browse from BROWSE."""

    def __init__(self, variant="Lite"):
        super().__init__({"hello": {"variant": variant},
                          "load_item": {"track": 0, "item": "x", "devices_added": ["x"], "device_index": 0}})

    def call(self, op, args=None):
        if op != "browse":
            return super().call(op, args)
        self.calls.append((op, args))
        rows = BROWSE[(args["root"], tuple(args["path"]))]
        return {"items": [{"name": n, "is_folder": f, "is_loadable": ld} for n, f, ld in rows],
                "truncated": args["path"] == ["Drift", "Pad"]}


def loads(fake):
    return [args for op, args in fake.calls if op == "load_item"]


def test_load_instrument_lite_refuses_operator(monkeypatch):
    lite, suite = BrowseClient("Lite"), BrowseClient("Suite")
    monkeypatch.setattr(server, "client", lite)
    refused = call_tool("load_instrument", {"track": 0, "kind": "operator"})
    monkeypatch.setattr(server, "client", suite)
    ok = call_tool("load_instrument", {"track": 0, "kind": "operator"})

    assert refused.is_error is True and "drift" in refused.content[0].text
    assert loads(lite) == []
    assert ok.is_error is False and loads(suite) == [{"track": 0, "root": "instruments", "path": ["Operator"]}]


def test_load_instrument_bare_device(monkeypatch):
    fake = BrowseClient()
    monkeypatch.setattr(server, "client", fake)
    result = call_tool("load_instrument", {"track": 0, "kind": "drift"})
    assert result.is_error is False
    assert fake.calls == [("hello", {}), ("load_item", {"track": 0, "root": "instruments", "path": ["Drift"]})]
    assert result.structured_content["path"] == ["Drift"] and result.structured_content["device_index"] == 0


@pytest.mark.parametrize("kind, preset, root, path", [
    ("drift", "deep bass", "instruments", ["Drift", "Bass", "Deep Bass.adv"]),
    ("impulse", "Impulse 808", "instruments", ["Impulse", "Impulse 808.adv"]),
    ("drum_rack", "808 Core Kit", "drums", ["808 Core Kit.adg"]),
])
def test_load_instrument_preset(monkeypatch, kind, preset, root, path):
    fake = BrowseClient()
    monkeypatch.setattr(server, "client", fake)
    result = call_tool("load_instrument", {"track": 0, "kind": kind, "preset": preset})
    assert result.is_error is False
    assert loads(fake) == [{"track": 0, "root": root, "path": path}]


def test_load_instrument_unknown_preset(monkeypatch):
    fake = BrowseClient()
    monkeypatch.setattr(server, "client", fake)
    result = call_tool("load_instrument", {"track": 0, "kind": "drift", "preset": "Nope"})
    text = result.content[0].text
    assert result.is_error is True and "Bass" in text and "Pad" in text and "browse_library" in text
    assert "cut at 500" in text
    assert loads(fake) == []


def test_device_tools_pass_through(monkeypatch):
    fake = FakeClient({"get_device": {}, "set_parameters": {}})
    monkeypatch.setattr(server, "client", fake)
    call_tool("read_device", {"track": 2})
    call_tool("set_device_parameters", {"track": 2, "device_path": [0, 1, 0], "values": {"Filter Freq": 0.5, "Shape": "Saw"}})
    assert fake.calls == [
        ("get_device", {"track": 2, "device_path": [0]}),
        ("set_parameters", {"track": 2, "device_path": [0, 1, 0], "values": {"Filter Freq": 0.5, "Shape": "Saw"}}),
    ]


PROMPT_NAMES = [
    "lesson-first-beat",
    "lesson-notes-and-scales",
    "lesson-chords",
    "lesson-bassline",
    "lesson-melody",
    "lesson-song-structure",
    "explain-my-set",
]


def prompt_text(name):
    (message,) = asyncio.run(server.mcp.get_prompt(name)).messages
    return message.content.text


def test_prompts_list_is_fixed_in_lesson_order():
    first = asyncio.run(server.mcp.list_prompts())
    second = asyncio.run(server.mcp.list_prompts())
    assert [p.name for p in first] == PROMPT_NAMES
    assert [p.name for p in second] == PROMPT_NAMES
    for p in first:
        assert p.description
        assert not any(a.required for a in p.arguments or [])


def test_lessons_link_chapter_save_and_next():
    lesson_names = PROMPT_NAMES[:6]
    for name, url in zip(lesson_names, lessons.CHAPTER.values(), strict=True):
        text = prompt_text(name)
        assert url.startswith("https://learningmusic.ableton.com/") and url in text, name
        assert "Cmd+S" in text, name
    for name, next_name in zip(lesson_names[:5], lesson_names[1:]):
        assert next_name in prompt_text(name), name


def test_explain_my_set_names_no_write_tool():
    text = prompt_text("explain-my-set")
    for forbidden in ("write_notes", "create_midi_track", "delete_"):
        assert forbidden not in text
    named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", text))
    assert named and named <= READ_ONLY_TOOLS, named - READ_ONLY_TOOLS


def test_texts_name_only_real_tools():
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    texts = [server.mcp.instructions] + [prompt_text(n) for n in PROMPT_NAMES]
    named = {m for t in texts for m in re.findall(r"`([a-z]+(?:_[a-z]+)+)`", t)}
    assert named and named <= tools, named - tools


def test_instructions_fit_claude_code_limit():
    text = server.mcp.instructions
    assert len(text) < 4096
    assert "Cmd+S" in text and "lesson-first-beat" in text
