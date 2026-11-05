import asyncio

from ableton_mcp import midifile, server
from ableton_mcp.live_client import CONTROL_SURFACE_HINT, LiveUnavailable

TOOL_NAMES = [
    "live_status",
    "get_set_overview",
    "create_midi_track",
    "create_midi_clip",
    "write_notes",
    "browse_library",
    "load_from_library",
    "set_tempo",
    "play_clip",
    "stop",
    "undo_last",
    "write_midi_file",
]
READ_ONLY_TOOLS = {"live_status", "get_set_overview", "browse_library"}


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


def test_create_midi_track_refuses_ninth_track_in_lite(monkeypatch):
    tracks = [{"index": i, "name": str(i), "type": "midi" if i % 2 else "audio"} for i in range(8)]
    tracks.append({"index": 8, "name": "Group", "type": "group"})
    fake = FakeClient({"hello": {"variant": "Lite"}, "get_overview": {"tracks": tracks}})
    monkeypatch.setattr(server, "client", fake)

    result = call_tool("create_midi_track", {"name": "Drums"})

    assert result.is_error is True
    assert "Lite allows 8 tracks" in result.content[0].text
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
