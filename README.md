# ableton-mcp

MCP server that lets an MCP client (Claude Code) drive Ableton Live 12.1+ (Lite included) through a stdlib-only Remote Script, plus an offline `.mid` writer that works with Live closed.

Tested on Live 12.1.10 Lite (embedded Python 3.11.6), macOS. Session View only.

## How it works

- `remote_script/AbletonMCP/`: Live loads this as a Control Surface. It listens on `127.0.0.1` on a random port. It writes `{port, token}` to `~/.ableton-mcp/connection.json` (mode 0600) and removes the file when Live quits. On each Live timer tick (~100 ms) it handles up to 64 pending JSON-lines requests on Live's main thread. Each change is one undo step. A request that arrives after its deadline is refused, so a timed-out write never lands late.
- `src/ableton_mcp/`: the MCP server (stdio). It reads `connection.json` on every call and never retries a request.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Live 12.1 or newer.

```sh
uv sync
uv run ableton-mcp install-script          # copies the script to ~/Music/Ableton/User Library/Remote Scripts/AbletonMCP
# other User Library location: uv run ableton-mcp install-script --user-library "/path/to/User Library"
```

Restart Live. Open Settings → Link, Tempo & MIDI, and pick **AbletonMCP** in a Control Surface slot (leave Input/Output as None). `Log.txt` (`~/Library/Preferences/Ableton/Live 12.x.y/Log.txt`) should then show `AbletonMCP listening on 127.0.0.1:<port> (Python 3.11.x)`.

Re-run `install-script` and restart Live after every change to `remote_script/`.

Register with Claude Code:

```sh
claude mcp add ableton -- uv --directory /path/to/ableton-mcp run ableton-mcp
```

Check the bridge without an MCP client:

```sh
uv run ableton-mcp call hello
uv run ableton-mcp call browse '{"root":"drums","path":[]}'
```

## Tools

`uv run ableton-mcp` runs the stdio server with 15 tools, in this order:

| Tool | What it does | Read-only |
|------|--------------|-----------|
| `live_status` | Live version, edition, features; error if the installed Remote Script is out of date | yes |
| `get_set_overview` | Tempo, tracks, devices, Drum Rack pads (`drum_pads`), clip slots | yes |
| `create_midi_track` | Add a MIDI track (refused in Lite once 8 audio+MIDI tracks exist) | no |
| `create_midi_clip` | Empty MIDI clip in a slot (length in beats) | no |
| `delete_track` | Delete a track with its clips and devices; refuses group tracks (destructive, one undo step) | no |
| `delete_clip` | Delete the clip in a slot (destructive, one undo step) | no |
| `write_notes` | Replace all notes in a clip (beats; pitches from `drum_pads`; destructive) | no |
| `read_notes` | Clip length and notes, in the shape `write_notes` takes | yes |
| `browse_library` | List one level of Live's browser | yes |
| `load_from_library` | Load a browser item onto a track | no |
| `set_tempo` | Set BPM (20-999) | no |
| `play_clip` | Fire a clip | no |
| `stop` | Stop playback | no |
| `undo_last` | Undo the last change | no |
| `write_midi_file` | Write a `.mid` to `~/Music/ableton-mcp/midi/` without Live | no |

If Live or the Remote Script is not reachable, or the script reports an error, the tool returns an `isError` result with the reason.

Tools that delete tracks or clips, or replace a clip's notes, carry `destructive_hint: true`, so the client can ask before it runs them. Every other write tool carries `destructive_hint: false`.

## Limits

- Live Lite allows 8 audio+MIDI tracks. `create_midi_track` checks this before it calls Live; free a track with `delete_track` or reuse one.
- `live_status` compares the Remote Script's protocol version with the server's. After an upgrade, run `install-script` again and restart Live.
- `read_notes` does not return mute or probability, so a `read_notes` + `write_notes` round trip resets them. Notes that start at or after the clip length never play; `write_notes` reports them as `notes_outside_clip`.
- `drum_pads` comes from the first top-level Drum Rack on the track. Racks nested inside other racks are not searched.
- The LOM cannot save the Set. Press Cmd+S yourself.
- Devices load only through the browser (`load_from_library`). `Track.insert_device` arrives in Live 12.3; `live_status` reports it under `features`.
- A `.mid` from `write_midi_file` holds notes only. Drop it on a track with an instrument, or it plays silent. It never overwrites an existing file; reuse of a name returns an error.
- `load_from_library` with an instrument replaces the track's current instrument. `undo_last` restores it.
- If Live shows a modal dialog, calls time out. A timed-out change may still have run: check `get_set_overview` before retrying.

## Manual smoke checklist (real Live)

0. Upgrade only: with the old script still installed, `live_status` returns the `install-script` hint.
1. `install-script`, restart Live, select AbletonMCP. `Log.txt` shows `AbletonMCP listening` and Python 3.11.
2. `call hello` shows your Live version and edition (`variant`).
3. `call browse '{"root":"drums","path":[]}'` lists the drum kits.
4. In Claude Code, ask "make a 1-bar 4/4 drum beat on a new track and play it". You get a new track, a kit, a clip with notes, and sound. Cmd+S still saves.
5. `undo_last` removes only the last tool's change.
6. Lite only: with 8 tracks, asking for another returns the edition-limit error and Live shows no dialog. In Claude Code, "add a bass track" makes the agent delete or reuse a track.
7. `call get_overview` shows `drum_pads` names (e.g. "Kick", "Snare") for a kit track. `call get_notes '{"track":N,"slot":0}'` lists the drum clip's notes.
8. `call delete_track '{"track":N}'` removes the track; one `call undo` restores it with its clip and kit.
9. Quit Live. `get_set_overview` returns the Control Surface hint, and `write_midi_file` still writes a `.mid` that you can drag into Live.

## Development

```sh
uv sync
uv run pytest -q
```
