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

`uv run ableton-mcp` runs the stdio server with 27 tools, in this order:

| Tool | What it does | Read-only |
|------|--------------|-----------|
| `live_status` | Live version, edition, features, edition `limits` (`audio_midi_tracks`, `scenes`; `null` = no cap); error if the installed Remote Script is out of date | yes |
| `get_set_overview` | Tempo, tracks, devices, Drum Rack pads (`drum_pads`), clip slots, each track's `mix`, `scenes`, `return_tracks` | yes |
| `create_midi_track` | Add a MIDI track (refused in Lite once 8 audio+MIDI tracks exist) | no |
| `create_midi_clip` | Empty MIDI clip in a slot (length in beats) | no |
| `delete_track` | Delete a track with its clips and devices; refuses group tracks (destructive, one undo step) | no |
| `delete_clip` | Delete the clip in a slot (destructive, one undo step) | no |
| `create_scene` | Add a scene at an index, or `copy_of` a scene with its clips (copy goes directly below; refused in Lite at 16 scenes) | no |
| `copy_clip` | Copy a clip to an empty slot; an occupied target is refused | no |
| `write_notes` | Replace all notes in a clip (beats; pitches from `drum_pads`; destructive) | no |
| `read_notes` | Clip length and notes, in the shape `write_notes` takes | yes |
| `set_scale` | Set the Set's key (root + scale) and turn on Scale Mode; the melodic generators default to it | no |
| `create_drum_pattern` | Drum beat in a style (rock, house, hip_hop, trap, reggaeton, breakbeat), plus optional Euclidean layers, on a Drum Rack track or to a `.mid` (destructive) | no |
| `create_bassline` | Bassline from scale degrees, one per bar (patterns: quarters, eighths, octaves, offbeat, arpeggio), into a slot or a `.mid` (destructive) | no |
| `create_chord_progression` | Held root-position chords from scale degrees (optional sevenths), into a slot or a `.mid` (destructive) | no |
| `browse_library` | List one level of Live's browser | yes |
| `load_from_library` | Load a browser item onto a track; returns `device_index` of the first added device | no |
| `load_instrument` | Load an instrument by kind (drift, simpler, impulse, drum_rack, ...), optionally a preset by name; refuses non-Lite kinds on Lite | no |
| `read_device` | A device's parameters (name, raw value, min/max, Live's `display` text, switch `items`) and a rack's chains; `device_path` reaches into racks | yes |
| `set_device_parameters` | Set several parameters of one device by name, in one undo step; all-or-nothing | no |
| `set_track_mix` | Set a track's volume (dB), pan (-50..50), mute, solo and sends (dB, by return letter); one undo step | no |
| `set_label` | Rename and/or colour a track, a clip (track + slot) or a scene | no |
| `set_tempo` | Set BPM (20-999) | no |
| `play_clip` | Fire a clip | no |
| `fire_scene` | Launch every clip in a scene (not an undo step) | no |
| `stop` | Stop playback | no |
| `undo_last` | Undo the last change | no |
| `write_midi_file` | Write a `.mid` to `~/Music/ableton-mcp/midi/` without Live | no |

If Live or the Remote Script is not reachable, or the script reports an error, the tool returns an `isError` result with the reason.

Tools that delete tracks or clips, replace a clip's notes, or write a generated clip (`create_drum_pattern`, `create_bassline`, `create_chord_progression` replace any clip in the target slot) carry `destructive_hint: true`, so the client can ask before it runs them. Every other write tool carries `destructive_hint: false`.

## Lessons and resources

The server also sends instructions to the agent once per session (`src/ableton_mcp/lessons.py`). They tell it to check Live before changing anything, explain each change in plain words, reuse tracks under Lite's limits, offer `undo_last`, and remind you once to press Cmd+S at the end of a task that changed the Set.

Guided lessons are MCP prompts. In Claude Code, type `/` and pick one. They take no arguments: the lesson asks you instead.

| Command | What you build |
|---------|----------------|
| `/ableton:lesson-first-beat` | A one-bar drum loop: kick, snare, hi-hats, tempo |
| `/ableton:lesson-notes-and-scales` | Hear a scale, compare major and minor, pick a key |
| `/ableton:lesson-chords` | A four-chord progression in your key |
| `/ableton:lesson-bassline` | A bassline that follows the chords |
| `/ableton:lesson-melody` | A short melody over the chords |
| `/ableton:lesson-song-structure` | Song sections as scenes, played in order |
| `/ableton:explain-my-set` | A plain-words tour of your Set; changes nothing |

The lessons follow the chapter order of Ableton's [Learning Music](https://learningmusic.ableton.com) and link each chapter for hands-on practice. The site states no licence, so the lesson texts are our own words and only link to it.

Two read-only resources return the same JSON as their tools. Attach one to a message with `@`:

- `@ableton:live://capabilities`: same as `live_status`.
- `@ableton:live://set/overview`: same as `get_set_overview`.

The `ableton:` prefix is the name the server was registered under (`claude mcp add ableton ...` in Install). Under another name, the commands and mentions use that name instead.

## Limits

- Live Lite allows 8 audio+MIDI tracks. `create_midi_track` checks this before it calls Live; free a track with `delete_track` or reuse one.
- Live Lite allows 16 scenes. `create_scene` checks this before it calls Live; name and reuse empty scenes with `set_label`.
- Live Lite allows 2 return tracks (A and B). Return and master track mixing is not supported; `set_track_mix` covers audio, MIDI and group tracks.
- Volume and send levels are in dB. In `get_set_overview`, `null` means -inf (silent). `set_track_mix` takes numbers only; to silence a track, use `mute`.
- Solo is not exclusive: `set_track_mix(solo=true)` does not unsolo other tracks. Unsolo them yourself.
- Colours are 10 names (red, orange, yellow, lime, green, cyan, blue, purple, pink, grey). Live snaps each to the nearest colour in its chooser.
- `live_status` compares the Remote Script's protocol version with the server's. After an upgrade, run `install-script` again and restart Live.
- `read_notes` does not return mute or probability, so a `read_notes` + `write_notes` round trip resets them. Notes that start at or after the clip length never play; `write_notes` reports them as `notes_outside_clip`.
- `drum_pads` comes from the first Drum Rack on the track: top level first, then one level inside other racks (many Lite kits wrap their Drum Rack in an Instrument Rack). Deeper nesting is not searched.
- The LOM cannot save the Set. Press Cmd+S yourself.
- Devices load only through the browser (`load_from_library`, `load_instrument`), so a load replaces the current instrument and is one undo step. `Track.insert_device` (Live 12.3) is not used; `live_status` reports it under `features`.
- `load_instrument` kinds: drift, simpler, impulse, drum_rack, drum_sampler and instrument_rack are in every edition. operator, wavetable, analog and meld (use drift) and sampler (use simpler) are refused on Lite before Live is called. On Standard/Suite they load by browser name; that path is untested.
- `load_instrument` presets: it searches the kind's browser folder and each folder one level below it (drum_rack: the `drums` root), one `browse` call per folder (14 for Drift on Lite). The first name match in browser order wins; the result returns the loaded `path`. Other presets: `browse_library` + `load_from_library`.
- Device parameters are raw values between `min` and `max`, not units. Read `display` (Live's own text) after a set and adjust. `display_value` (Live 12.2+) is not used. A parameter with `enabled: false` is controlled by a macro or locked; `set_device_parameters` refuses it.
- Some Core Library rack presets (e.g. "Basic Analog Bass", "808 Drifter") hide their chains: Live shows only the macros and the LOM reports `chains: []`. Use the macros. Racks with visible chains (e.g. "Ahlimba Kit") can be read inside with `device_path` [0, chain, device].
- A `.mid` from `write_midi_file` holds notes only. Drop it on a track with an instrument, or it plays silent. It never overwrites an existing file; reuse of a name returns an error.
- `load_from_library` with an instrument replaces the track's current instrument. `undo_last` restores it.
- Generators are 4/4 only and refuse a Set in another signature. Their `.mid` files are always 4/4 at 120 BPM.
- Scales: Major, Minor, Dorian, Phrygian, Lydian, Mixolydian, Locrian, Harmonic Minor, Melodic Minor, Major Pentatonic, Minor Pentatonic, Minor Blues. Any other name is refused before Live is called. `create_chord_progression` needs a 7-note scale.
- `create_drum_pattern` picks each voice's pad by a keyword in the pad name first (e.g. "Kick", "Snare", "Hihat Closed"), then by its General MIDI note (36 kick, 38 snare, 42 closed hat, 46 open hat, ...) if no other voice claims that pad by name. Kits with odd or empty pad names can leave voices unresolved; the result lists them in `missing_voices`.
- Generators replace the clip in the target slot as one undo step. They never create tracks or load instruments. Melodic clips need an instrument on the track, or they play silent.
- If Live shows a modal dialog, calls time out. A timed-out change may still have run: check `get_set_overview` before retrying.

## Manual smoke checklist (real Live)

0. Upgrade only: with the old script still installed, `live_status` returns the `install-script` hint.
1. `install-script`, restart Live, select AbletonMCP. `Log.txt` shows `AbletonMCP listening` and Python 3.11.
2. `call hello` shows your Live version, edition (`variant`) and `protocol_version: 5`.
3. `call browse '{"root":"drums","path":[]}'` lists the drum kits.
4. In Claude Code, ask "make a 1-bar 4/4 drum beat on a new track and play it". You get a new track, a kit, a clip with notes, and sound. Cmd+S still saves.
5. `undo_last` removes only the last tool's change.
6. Lite only: with 8 tracks, asking for another returns the edition-limit error and Live shows no dialog. In Claude Code, "add a bass track" makes the agent delete or reuse a track.
7. `call get_overview` shows `drum_pads` names (e.g. "Kick", "Snare") for a kit track. `call get_notes '{"track":N,"slot":0}'` lists the drum clip's notes.
8. `call delete_track '{"track":N}'` removes the track; one `call undo` restores it with its clip and kit.
9. Quit Live. `get_set_overview` returns the Control Surface hint, and `write_midi_file` still writes a `.mid` that you can drag into Live.
10. A `uv run python -c` loop calls `LiveClient().call("set_scale", ...)` for each of the 12 scales and prints any whose returned `intervals` differ from `theory.SCALES`. It prints nothing; the MIDI editor highlights the last scale.
11. On an 808 Core Kit track, `create_drum_pattern(style="house", bars=2, track=0, slot=1)` returns no `missing_voices` and plays as house. One `undo_last` removes the clip. On an occupied slot, one `undo_last` restores the old clip and its notes.
12. Load a Lite kit with empty pad names (e.g. "Ahlimba Kit"). `create_drum_pattern(style="rock")` resolves kick, snare and closed_hat.
13. In a fresh Claude Code session, "make a hip hop beat, a bassline and chords in A minor" calls `set_scale`, `create_drum_pattern`, `create_bassline` and `create_chord_progression` (no `write_notes`). All three clips play in key.
14. With Live closed, "write me a house beat as a MIDI file" writes a `.mid` in `~/Music/ableton-mcp/midi/` that plays on a kit when dragged in.
15. `call get_overview` (read-only) shows `mix.volume_db` 0.0 and `pan` 0 for a default track, `return_tracks` A/B with their names, and 8 `scenes`. A dB parse error shows here, before any setter runs.
16. `set_track_mix(track=0, volume_db=-6, pan=25, sends={"A": -12})`: Live shows -6.0 dB, 25R and send A at -12 dB. One `undo_last` restores all three.
17. `create_scene(copy_of=0)` adds a copy below scene 0 with the same clips. `fire_scene(scene=1)` plays that row.
18. `set_label` with `color="red"` on a track, a clip and a scene shows red in Live. `copy_clip` into an empty slot plays. Cmd+S still saves.
19. Lite only: with 16 scenes, `create_scene` returns the Lite error and Live shows no dialog.
20. In Claude Code, "turn my loop into a song with intro, verse and chorus" names the scenes, copies or varies the clips, colours them by role and fires the scenes in order.
21. `call get_device '{"track":1,"device_path":[0]}'` on a rack preset (e.g. "Basic Analog Bass") lists its macro parameters and `chains: []` (its chains are hidden). On "Ahlimba Kit", `[0,0,0]` reaches the Drum Rack and `[0,0,1]` the Saturator. No `display` is blank.
22. In Claude Code, "put a Drift on track 5" calls `load_instrument(4, "drift")` and track 5 shows Drift. "load the Deep Bass preset on track 6" loads it with one `load_instrument` call and no `browse_library`.
23. `set_device_parameters` on two Drift parameters: the returned `display` matches Live's UI. One `undo_last` restores both.
24. Lite only: "use Operator for the lead" gets the refusal, and the agent loads Drift instead.
25. `uv run python -c "import asyncio; from ableton_mcp import server; print(asyncio.run(server.mcp.read_resource('live://capabilities')))"` shows your Live version, edition and `limits` (`"audio_midi_tracks": 8` on Lite).
26. In Claude Code, after `/mcp` reconnect: typing `/lesson` lists the six `/ableton:lesson-*` commands, `/ableton:explain-my-set` is there, and typing `@ableton:` offers both `live://` resources.
27. `/ableton:lesson-first-beat` builds a playing beat step by step, explains the drum roles and tempo, links the Beats chapter, and ends with one Cmd+S reminder and the next lesson's command. `/ableton:explain-my-set` makes no write tool call.
28. In a fresh session with no lesson, "add an open hi-hat on the off-beats" gets a plain-words summary of the change and one save reminder at the end, not one after each tool call.

## Development

```sh
uv sync
uv run pytest -q
```
