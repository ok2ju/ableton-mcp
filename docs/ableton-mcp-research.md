# Research: an MCP server that lets an AI agent drive Ableton Live

Research date: 2026-11-05. Scope: Ableton Live 11 and 12, all editions, with Live Lite as the primary target. Every factual claim links to its source. Claims I could not confirm are marked **Unverified**. Where sources disagree, both are shown.

A note on source quality. Ableton documents its editions, its Max for Live API, and its new Extensions SDK. It does **not** document the Python API that MIDI Remote Scripts use. For that API this document relies on three kinds of unofficial evidence, and says so each time: decompiled copies of Ableton's own scripts, community API dumps taken by introspecting Live at runtime, and the source code of open-source projects that use the API.

---

## Summary

- **Live Lite cannot run Max for Live or Extensions, so neither can be the foundation.** Ableton states that "Max for Live is not supported within Live Lite or Intro" ([Ableton KB: Buying Max for Live](https://help.ableton.com/hc/en-us/articles/206407124)). Extensions "are not available in Live Standard, Intro, or Lite" and currently require the Live 12.4.5+ Suite **beta** ([Ableton KB: Extensions FAQ](https://help.ableton.com/hc/en-us/articles/27303428331420)).
- **A Python MIDI Remote Script is the only in-process integration that reaches Lite.** Ableton documents installing third-party Remote Scripts into a "Remote Scripts" folder in the User Library since Live 10.1.13, without any edition restriction ([Ableton KB: Installing third-party remote scripts](https://help.ableton.com/hc/en-us/articles/209072009)). Ableton never says explicitly that Lite loads third-party scripts. A public bug report shows a third-party script running on Live 12 Lite 12.4.6 ([ahujasid/ableton-mcp#138](https://github.com/ahujasid/ableton-mcp/issues/138)). Treat Lite support as community-confirmed, officially **Unverified**.
- **Lite's limits shape what the agent may build.** Lite allows 8 audio/MIDI tracks, 16 scenes and 2 send/return tracks. Its 5 instruments are Impulse, Instrument Rack, Simpler, Drift and Drum Rack. Operator, Wavetable, Analog and Sampler are not included ([Ableton: Upgrade Live comparison](https://www.ableton.com/en/upgrade-live/)). If a Set contains non-Lite elements, Lite disables saving and exporting ([Ableton KB: Third party Set won't open in Live Lite](https://help.ableton.com/hc/en-us/articles/115000258930)).
- **The script can detect the edition at runtime.** Live 12's Python API has `Application.get_variant()`, which returns one of `Lite`, `Intro`, `Standard`, `Suite`, `Trial` or `Beta`. There is also an `unavailable_features` property (unofficial [structure-void Live 12 Application reference](https://midiremotescripts.structure-void.com/reference/live12/Live.Application.runtime/)).
- **The embedded Python changed between versions, so Remote Script code must target Python 3.7 syntax to cover Live 11 and 12.** Live 11 moved scripts to Python 3 ([Ableton KB](https://help.ableton.com/hc/en-us/articles/209072009)). Unofficial sources put Live 11 at 3.7 ([structure-void](https://midiremotescripts.structure-void.com/)). Live 12.1 updated the interpreter to 3.11.6 ([Live 12 release notes, 12.1](https://www.ableton.com/en/release-notes/live-12/)).
- **The Live Object Model covers what a beginner needs, with gaps.** It can create tracks, clips and scenes. It can write notes with `Clip.add_new_notes` (Live 11+) and control transport, tempo and scale. It can load devices and presets through `Application.browser.load_item` (Python only). Since 12.3, native devices can be inserted by name with `Track.insert_device`, and since 12.4 Simpler samples can be replaced with `SimplerDevice.replace_sample` ([Cycling '74 LOM](https://docs.cycling74.com/apiref/lom/), [Live 12 release notes](https://www.ableton.com/en/release-notes/live-12/)). The LOM has **no** function to save the Set, export audio, or open another Set ([Cycling '74 LOM: Song](https://docs.cycling74.com/apiref/lom/song/)).
- **Existing MCP servers prove the approach but are not a good base to fork.** The most popular, [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp) (3,302 stars), uses unframed JSON and calls the LOM from a socket thread for reads. It writes notes with the deprecated `set_notes` and has no undo grouping. It also ships default-on anonymous telemetry. Better patterns exist in [bschoepke/ableton-live-mcp](https://github.com/bschoepke/ableton-live-mcp), [wstierhout/ableton-live-mcp](https://github.com/wstierhout/ableton-live-mcp) and [dreamrec/LivePilot](https://github.com/dreamrec/LivePilot): newline-delimited JSON on loopback, a queue plus `schedule_message`, cancel-on-timeout, fake-LOM tests, and batched undo steps. Details are in section 4.
- **MCP changed shape in 2026.** The current spec is **2026-07-28**. It removes the `initialize` handshake and protocol sessions, adds `server/discover`, and replaces server-initiated requests (elicitation, sampling) with "multi round-trip requests" ([MCP changelog 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/changelog)). Official SDKs: Python `mcp` 2.3.0 ([PyPI](https://pypi.org/project/mcp/)) and TypeScript `@modelcontextprotocol/server` 2.4.0 ([typescript-sdk README](https://github.com/modelcontextprotocol/typescript-sdk)). Both serve 2026-era and 2025-era clients ([Python SDK: What's new in v2](https://py.sdk.modelcontextprotocol.io/whats-new/)).
- **Recommendation.** Build a Python MCP server (stdio) that talks newline-delimited JSON over TCP on 127.0.0.1, with a token and request IDs, to your own small stdlib-only Remote Script. Inside Live, marshal every LOM call onto the main thread and group each tool call into one undo step. Add an offline `.mid` file writer as a fallback, so the agent can still hand the user drag-in material when Live is closed. Do not generate `.als` files.
- **Biggest blockers to confirm first, by hand:** (1) that a custom Remote Script loads on the user's own Lite install; (2) which browser items `load_item` can reach in Lite; (3) the exact Python version in the user's Live build (log `sys.version` at startup).

---

## 1. Ableton editions and their limits (Live 12)

The latest stable release on the research date is Live 12.4.16 (October 30, 2026), and the latest beta is 12.4.25b1 (October 29, 2026) ([Live 12 release notes](https://www.ableton.com/en/release-notes/live-12/), [Live 12 beta release notes](https://www.ableton.com/en/release-notes/live-12-beta/)).

### 1.1 Limits and included devices

Figures come from Ableton's own comparison table, which is the only official page that lists Lite ([Ableton: Upgrade Live](https://www.ableton.com/en/upgrade-live/)). I parsed the table's HTML directly, because its checkmarks are icons.

| Feature | Lite | Intro | Standard | Suite |
|---|---|---|---|---|
| Audio and MIDI tracks | 8 | 16 | Unlimited | Unlimited |
| Scenes | 16 | 16 | Unlimited | Unlimited |
| Send and return tracks | 2 | 2 | 12 | 12 |
| Mono audio inputs / outputs | 8 / 8 | 16 / 16 (see conflict note) | 256 / 256 | 256 / 256 |
| Software instruments (count) | 5 | 8 | 13 | 20 |
| Audio effects (count) | 16 | 27 | 42 | 58 |
| MIDI effects (count) | 10 | 12 | 13 | 14 |
| Max for Live | No | No | No (paid add-on) | Yes |
| Drum Rack, Simpler, Impulse, Drift, Instrument Rack | Yes | Yes | Yes | Yes |
| Drum Sampler, CV Instrument, External Instrument | No | Yes | Yes | Yes |
| Analog, Collision, Electric, Tension, Drum Synths | No | No | Yes | Yes |
| Operator, Wavetable, Sampler, Meld, Poli, Bass, Granulator III | No | No | No | Yes |
| Arpeggiator, Chord, Scale, Pitch, Random, Velocity, Note Length | Yes | Yes | Yes | Yes |
| Note Chance (note probability) | No | Yes | Yes | Yes |
| Quantize MIDI Transformation | No | Yes | Yes | Yes |
| Other MIDI Transformations and Generators (Arpeggiate, Rhythm, Seed, Stacks, Euclidean...) | No | No | Yes | Yes |
| Audio effects in Lite (selection) | Auto Filter, Beat Repeat, Channel EQ, Chorus-Ensemble, Compressor, Delay, EQ Three, Erosion, Limiter, Phaser-Flanger, Redux, Reverb, Saturator, Tuner, Utility, Audio Effect Rack | | | |
| Comping, Time signature changes, Group tracks, Multiple automation lanes | Yes | Yes | Yes | Yes |
| VST2, VST3 and AU plug-ins | Yes | Yes | Yes | Yes |
| MIDI remote control instant mapping | Yes | Yes | Yes | Yes |
| Stem Separation (12.3) | No | No | No | Yes |

**Conflict on Intro audio I/O.** The [Upgrade Live page](https://www.ableton.com/en/upgrade-live/) lists Intro at 16 mono inputs and 16 outputs. The [Compare Editions page](https://www.ableton.com/en/live/compare-editions/) lists Intro at 8 and 8. This does not affect the architecture.

**Unofficial track-count claims.** Third-party sites claim Lite has 8 scenes. Ableton's table says 16, and the official figure is used here.

**Note on "Note Chance".** The table marks Note Chance as not included in Lite ([Upgrade Live](https://www.ableton.com/en/upgrade-live/)). The note API still has a `probability` field ([Cycling '74 LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)). Whether Lite honours a probability value written through the API is **Unverified**. The agent should not rely on it in Lite.

### 1.2 Max for Live by edition

- Max for Live is bundled with Suite and is a paid add-on for Standard ([Ableton KB: Activating Max for Live with Standard](https://help.ableton.com/hc/en-us/articles/360001296900), [Live 12 manual: Max for Live](https://www.ableton.com/en/live-manual/12/max-for-live/)).
- "Max for Live is not supported within Live Lite or Intro" ([Ableton KB: Buying Max for Live](https://help.ableton.com/hc/en-us/articles/206407124)).
- Intro and Standard include a few Ableton-made devices that were built in Max (for example LFO, MPE Control and Expression Control). These run "like native devices" but users cannot edit them or load other `.amxd` devices ([Ableton KB: Max for Live Devices in Live 11 Intro and Standard](https://help.ableton.com/hc/en-us/articles/360019145319)). The same pattern appears in Live 12: the Euclidean Generator and Velocity Shaper are listed as "Max for Live MIDI Generators" yet included in Standard ([Upgrade Live](https://www.ableton.com/en/upgrade-live/)). This does not give Lite or Intro users a way to run a custom Max device.

### 1.3 Control surfaces and Remote Scripts by edition

- Control surfaces are "specially written scripts which enable tactile control of Live", and the article is marked "Live Versions: All" ([Ableton KB: Using Control Surfaces](https://help.ableton.com/hc/en-us/articles/209774285)).
- Up to six control surfaces can be active at the same time ([Live 12 manual: MIDI and Key Remote Control](https://www.ableton.com/en/live-manual/12/midi-and-key-remote-control/)).
- "As of Live 10.1.13, you can create a 'Remote Scripts' folder in Live's User Library. Third-party remote scripts placed in this folder will appear in Live's Preferences → MIDI." Ableton gives no edition restriction and offers no support for third-party scripts ([Ableton KB: Installing third-party remote scripts](https://help.ableton.com/hc/en-us/articles/209072009)).
- The Lite FAQ says Lite supports VST and AU plug-ins. It does not mention Remote Scripts either way ([Ableton KB: Live Lite FAQ](https://help.ableton.com/hc/en-us/articles/209071589)).
- **Field evidence for Lite:** issue [ahujasid/ableton-mcp#138](https://github.com/ahujasid/ableton-mcp/issues/138) reports running that project's third-party Remote Script on "Ableton Live 12 Lite (12.4.6), macOS 26". Issue [#100](https://github.com/ahujasid/ableton-mcp/issues/100) shows a user on Live 12.4 Intro (Windows) who could not get the script to appear. A commenter on macOS got it working from the User Library folder. The Intro user's problem is not resolved in the thread.

**Conclusion.** Every edition, Lite included, can run a Python Remote Script, but this is community-confirmed only. Ableton has not stated it. Confirm it on the user's machine before writing anything else (see section 8).

### 1.4 Edition behaviour the agent must respect

- If a Set contains elements that are not part of Lite, Lite shows an error and **saving and exporting are deactivated** until those elements are removed ([Ableton KB: Third party Set won't open in Live Lite](https://help.ableton.com/hc/en-us/articles/115000258930)). An agent that writes Suite-only devices into a Lite Set, by hand-generating an `.als` for example, would lock the user out of saving.
- When a Lite or Intro feature limit is reached, Live shows a dialog, which since 12.4.3 includes an "Upgrade Live" button ([Live 12 release notes](https://www.ableton.com/en/release-notes/live-12/)). A modal dialog can block the main thread. wstierhout's client warns that a timeout may mean a modal dialog is blocking Live ([wstierhout connection.py:157-163](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/connection.py#L157-L163)). The LOM can read and press dialog buttons through `Application.open_dialog_count`, `current_dialog_message` and `press_current_dialog_button` ([Cycling '74 LOM: Application](https://docs.cycling74.com/apiref/lom/application/)).
- Sets saved in a later version, including later point releases, cannot be opened in earlier versions ([Ableton KB: Backward Compatibility](https://help.ableton.com/hc/en-us/articles/360000841004)).

---

## 2. Integration options

### 2a. Python MIDI Remote Script (control surface)

**What it is.** A Python package that Live imports at startup and lists in Settings → Link, Tempo & MIDI → Control Surface. The package's `__init__.py` must define `create_instance(c_instance)`, which returns a `ControlSurface` subclass (unofficial: [structure-void "first script" guide](https://midiremotescripts.structure-void.com/guides/first-script/); Ableton's own scripts decompiled in [gluon/AbletonLive12_MIDIRemoteScripts](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts)). The script uses the `Live` module, which is the Python face of the Live Object Model.

**Official status.** Unsupported and undocumented. Ableton: "We cannot provide technical support for remote scripts not included with Live" ([Ableton KB](https://help.ableton.com/hc/en-us/articles/209072009)). CDM quotes Ableton calling the Python API unsupported while saying it "has to stay" because hardware support depends on it ([CDM: Ableton Extensions beta](https://cdm.link/ableton-extensions-beta/)).

**Install location.**

| Purpose | macOS | Windows | Source |
|---|---|---|---|
| Third-party scripts (use this) | `~/Music/Ableton/User Library/Remote Scripts/<Name>/` | `\Users\<user>\Documents\Ableton\User Library\Remote Scripts\<Name>\` | [Ableton KB 209072009](https://help.ableton.com/hc/en-us/articles/209072009) (the folder must be created manually; it survives Live upgrades) |
| `UserConfiguration.txt` mapping scripts (not Python) | `~/Library/Preferences/Ableton/Live x.x.x/User Remote Scripts` | `C:\Users\<user>\AppData\Roaming\Ableton\Live x.x.x\Preferences\User Remote Scripts` | [Ableton KB 206240184](https://help.ableton.com/hc/en-us/articles/206240184) |
| `Log.txt` (script errors and `log_message` output) | `~/Library/Preferences/Ableton/Live x.x.x/Log.txt` | `C:\Users\<user>\AppData\Roaming\Ableton\Live x.x.x\Preferences\Log.txt` | [Ableton KB: Reading crash reports](https://help.ableton.com/hc/en-us/articles/5301568366354) |

The User Library path is the default. Users can move their User Library, so an installer must find or ask for the real path. Live reads the custom location from its preferences. The exact file and key is **Unverified**.

**Embedded Python version.**

| Live version | Python | Source and confidence |
|---|---|---|
| 10 and earlier | Python 2 | Official: [Ableton KB 209072009](https://help.ableton.com/hc/en-us/articles/209072009) |
| 11.x | Python 3 (3.7) | "Python 3" is official ([KB](https://help.ableton.com/hc/en-us/articles/209072009)). "3.7" is unofficial: [structure-void](https://midiremotescripts.structure-void.com/), and wstierhout's changelog mentions "Live 11's embedded Python 3.7" ([CHANGELOG.md](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/CHANGELOG.md)) |
| 12.0.x | 3.7 (inferred) | Unofficial: decompiled Live 12 scripts carry the header "Python bytecode version base 3.7.0" ([gluon _Framework/ControlSurface.py](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts/blob/main/_Framework/ControlSurface.py)). Also consistent with 12.1's "updated" note |
| 12.1 and later | 3.11.6 | Official: "The embedded Python interpreter has been updated to version 3.11.6. Because of this, remote scripts that use compiled byte code (.pyc) need to be recompiled" ([Live 12 release notes, 12.1](https://www.ableton.com/en/release-notes/live-12/)). No later change appears in the 12.x notes up to 12.4.16 |

Consequences: ship plain `.py` sources, never `.pyc`. Write the script in the Python 3.7 subset (no walrus operator, no `match`, no `dict | dict`) if Live 11 and 12.0 matter. Log `sys.version` at startup.

**Threading model.**

- Live calls into the script on its main (UI) thread. The base `ControlSurface` gets an `update_display()` call every timer tick. The decompiled framework drives its task group with `Defaults.TIMER_DELAY = 0.1`, which is a 100 ms tick (unofficial: [gluon _Framework/ControlSurface.py:251](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts/blob/main/_Framework/ControlSurface.py#L251), [_Framework/Defaults.py:9](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts/blob/main/_Framework/Defaults.py#L9)).
- `schedule_message(delay_in_ticks, callback, parameter=None)` queues a callback to run on that tick ([gluon _Framework/ControlSurface.py:342](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts/blob/main/_Framework/ControlSurface.py#L342)). `schedule_message(0, fn)` runs the callback on the next tick from the main thread. This is how every well-behaved project gets LOM calls onto the main thread (section 4).
- Ableton publishes nothing on thread safety for the Python LOM. Everyone treats it as main-thread only. Community docs describe the Max-side Live API as "asynchronous, single-threaded (low priority), deferred" ([Cycling '74 forum](https://cycling74.com/forums/live-api-in-max-for-node), community wording, **Unverified** officially).
- **Sources conflict on background threads inside Live.** AbletonOSC's author writes that Live's embedded Python "does not appear to support threading, and beachballs when a thread is started", so AbletonOSC polls a non-blocking UDP socket from the tick instead ([AbletonOSC manager.py:110-119](https://github.com/ideoforms/AbletonOSC/blob/HEAD/manager.py#L110-L119)). ahujasid, bschoepke, wstierhout and LivePilot all run a `threading.Thread` socket server inside Live and only marshal LOM work to the main thread ([LivePilot server.py:198-202](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/server.py#L198-L202), [bschoepke bridge.py:143-157](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/Ableton_Live_MCP/bridge.py#L143-L157)). Both approaches are in daily use. The non-blocking-socket-on-tick approach is the safer default. A thread is acceptable if it never touches the LOM.
- Latency: with tick polling, each round trip can wait up to ~100 ms for the next tick ([AbletonOSC manager.py:111](https://github.com/ideoforms/AbletonOSC/blob/HEAD/manager.py#L111)). With a thread plus `schedule_message(0, ...)`, the wait is also bounded by the tick. Design tools to batch work into one request.

**Sockets and servers inside the script.** Python's stdlib `socket` works inside Live, as all the projects above show. Bind to `127.0.0.1`. LivePilot even documents a single-client design that replaces a stale connection ([LivePilot server.py:244-280](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/server.py#L244-L280)). Third-party packages are not available unless vendored. Assume stdlib only. One developer notes that spawning processes and win32 APIs are not possible in this environment (community claim via [search summary of Protocol0](https://github.com/lebrunthibault/protocol0), **Unverified**).

**Logging.** `ControlSurface.log_message()` writes to `Log.txt`, and `show_message()` writes to Live's status bar (unofficial: [structure-void first script](https://midiremotescripts.structure-void.com/guides/first-script/)). Tracebacks from failing scripts land in `Log.txt`. Because of Ableton's crash-log usage of the same file ([Ableton KB](https://help.ableton.com/hc/en-us/articles/5301568366354)), a dedicated log file next to the script (as AbletonOSC does, [manager.py:42-51](https://github.com/ideoforms/AbletonOSC/blob/HEAD/manager.py#L42-L51)) is easier for the user to send.

**Reload behaviour.** There is no hot reload. The workaround is to set the Control Surface slot to None and then reselect the script. New script folders are only discovered at startup, and a restart always clears the cache (unofficial: [structure-void first script](https://midiremotescripts.structure-void.com/guides/first-script/)). LivePilot records that Live caches Remote Script modules in `sys.modules`, so edits need a toggle or reinstall ([LivePilot BUGS.md](https://github.com/dreamrec/LivePilot/blob/HEAD/BUGS.md)). AbletonOSC implements its own `/live/api/reload` with `importlib.reload` and marks it development-only ([AbletonOSC manager.py:121-139](https://github.com/ideoforms/AbletonOSC/blob/HEAD/manager.py#L121-L139)). Implication: keep the in-Live script small and stable, and put logic that changes often in the external MCP server.

**Undo.**

- `Song.begin_undo_step()` and `Song.end_undo_step()` exist in the Python API with no documented semantics (unofficial: [structure-void Live 12 Song reference](https://midiremotescripts.structure-void.com/reference/live12/Live.Song.runtime/)).
- Ableton's own Push code nests clients and calls `begin_undo_step` once and `end_undo_step` when the last client leaves (decompiled [pushbase/undo_step_handler.py](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts/blob/main/pushbase/undo_step_handler.py)).
- `Song.undo()`, `Song.redo()`, `can_undo` and `can_redo` are documented ([Cycling '74 LOM: Song](https://docs.cycling74.com/apiref/lom/song/)).
- Whether a step that spans several ticks (several `schedule_message` callbacks) stays one undo step is **Unverified**. Keep each tool call inside one main-thread callback with `begin_undo_step` and `end_undo_step` in `try/finally`, as wstierhout does for batches ([wstierhout remote_script/__init__.py:1592-1611](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/remote_script/__init__.py#L1592-L1611)).

**Stability across versions.** structure-void's runtime diff of the Python API between Live 11 and 12 counts 1,427 members added, 84 removed and 1,573 signatures changed. Many of these are Python stdlib changes, not Live API changes ([structure-void: what changed 11 to 12](https://midiremotescripts.structure-void.com/guides/runtime-changes-11-to-12/)). Detect features with `hasattr` and version checks, not assumptions.

**Edition support:** all editions in practice (section 1.3).

### 2b. Max for Live device (LiveAPI, `js`/`v8`, `node.script`)

- Max for Live reaches the same LOM through `live.path`, `live.object` and `live.observer`, or through the JavaScript `LiveAPI` object in `js` ([Ableton KB: Controlling Live using Max for Live](https://help.ableton.com/hc/en-us/articles/5402681764242), [Cycling '74: Live API user guide](https://docs.cycling74.com/userguide/m4l/live_api/)).
- `live.object` "is designed to mimic user interactions with the Live Session (and adds to undo history)" ([Cycling '74: Live API](https://docs.cycling74.com/userguide/m4l/live_api/)).
- `node.script` (Node for Max) cannot call the Live API directly. Projects bridge it through a `js`/`v8` object and pass JSON over patch cords. Producer Pal does this and splits large JSON into chunks because Max messages have a maximum length ([Cycling '74 forum: Producer Pal](https://cycling74.com/forums/producer-pal-mcp-server-running-in-node-for-max-to-control-ableton-live), [forum: Live API in Node for Max](https://cycling74.com/forums/live-api-in-max-for-node); community sources).
- The Max-side LOM has **no Browser class**. The Cycling '74 LOM index lists no browser object ([Cycling '74 LOM](https://docs.cycling74.com/apiref/lom/)). agent4live, an M4L-based MCP server, ships an extra Remote Script helper "to expose the Browser API to the device" ([romsau/agent4live README](https://github.com/romsau/agent4live)). Without a browser, Max cannot load presets or samples from the library. Since 12.3, `Track.insert_device` can still insert native devices by name ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)).
- **Edition support: Suite, or Standard with the add-on. Ruled out for Lite and Intro** ([Ableton KB 206407124](https://help.ableton.com/hc/en-us/articles/206407124)).

### 2c. OSC bridges: AbletonOSC and pylive

- [ideoforms/AbletonOSC](https://github.com/ideoforms/AbletonOSC) is a Remote Script that exposes the LOM over OSC/UDP. It listens on port 11000 and replies on 11001 ([constants.py:5-6](https://github.com/ideoforms/AbletonOSC/blob/HEAD/abletonosc/constants.py#L5-L6)). It requires Live 11 or later ([README](https://github.com/ideoforms/AbletonOSC)). MIT licence, 843 stars, last push 2025-11-19, 74 open issues and PRs (GitHub API, 2026-11-05).
- **Coverage:** song, track, clip, clip_slot, scene, device (parameters only), view, a generic listener mechanism, and notes via `add_new_notes` and `get_notes_extended` ([clip.py:139-155](https://github.com/ideoforms/AbletonOSC/blob/HEAD/abletonosc/clip.py#L139-L155)). **No browser namespace**, so it cannot load instruments, effects, presets or samples. It also cannot traverse rack chains or group changes into undo steps (repo evaluation in section 4).
- **Protocol weaknesses for an agent:**
  - It binds `0.0.0.0` with no authentication ([osc_server.py:15](https://github.com/ideoforms/AbletonOSC/blob/HEAD/abletonosc/osc_server.py#L15)).
  - Setters send no acknowledgement, and `/live/error` messages carry no request correlation ([osc_server.py:92](https://github.com/ideoforms/AbletonOSC/blob/HEAD/abletonosc/osc_server.py#L92)).
  - Replies go to a fixed port 11001, so in practice only one client works at a time ([osc_server.py:94-95,160-165](https://github.com/ideoforms/AbletonOSC/blob/HEAD/abletonosc/osc_server.py#L160-L165)).
- [ideoforms/pylive](https://github.com/ideoforms/pylive) is a Python client that, "as of 2023, ... interface[s] exclusively with AbletonOSC" ([README](https://github.com/ideoforms/pylive)). 633 stars, last push 2025-07-23. The GitHub API reports no licence. It inherits all of AbletonOSC's limits.
- [nozomi-koborinai/ableton-osc-mcp](https://github.com/nozomi-koborinai/ableton-osc-mcp) ships a patch to AbletonOSC that adds browser find/load and preset loading. This confirms the gap in stock AbletonOSC.
- **Edition support:** same as any Remote Script.

### 2d. Offline file generation: `.als` and `.mid`

**`.als` (Live Set).**

- Ableton does not publish the format. Community tooling treats `.als`, `.adg` and `.adv` as gzip-compressed XML. wstierhout parses them with stdlib `gzip` and `xml.etree` ([_als_xml.py:22-41](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/tools/_als_xml.py#L22-L41)), and [closestfriend/ableton-proj-mcp](https://github.com/closestfriend/ableton-proj-mcp) does batch `.als` inspection. The format itself is **unofficial**.
- Risks of *writing* `.als`:
  - The schema is undocumented and changes with every point release. Sets saved by later versions cannot be opened by earlier ones ([Ableton KB: Backward Compatibility](https://help.ableton.com/hc/en-us/articles/360000841004)).
  - A generated Set that references non-Lite devices disables saving in Lite ([Ableton KB 115000258930](https://help.ableton.com/hc/en-us/articles/115000258930)).
  - Device state blobs are opaque.
- Reading `.als` for analysis is low risk. Writing is high risk. I found no project that writes `.als` from scratch reliably (the 8 evaluated repos only read).

**`.mid` (Standard MIDI File).**

- Live imports MIDI files through the browser or Create → Import MIDI File… In the Arrangement the file lands at the Insert Marker ([Live 12 manual: Managing Files and Sets](https://www.ableton.com/en/live-manual/12/managing-files-and-sets/)).
- Live exports a clip through File → Export MIDI Clip ([same manual chapter](https://www.ableton.com/en/live-manual/12/managing-files-and-sets/)).
- This works in every edition and with Live closed. The user drags the file onto a MIDI track. It carries notes only, with no devices or sounds. It is a good fallback and a teaching aid ("here is the drum pattern as a file").

### 2e. Plain MIDI and Ableton Link

- **Plain MIDI** through a virtual port can play notes and send CC to parameters that the user has MIDI-mapped. It can also send clock. It cannot create tracks, clips or devices, or read the Set. MIDI port setup is described in [Ableton KB: Live's MIDI Settings](https://help.ableton.com/hc/en-us/articles/209774205). Recording incoming MIDI requires an armed track and the user's involvement. This route is useful only for live auditioning.
- **Ableton Link** "synchronizes musical beat, tempo, and phase across multiple applications" ([Ableton/link README](https://github.com/Ableton/link)). The Link SDK is dual-licensed under GPLv2+ and a proprietary licence ([same README](https://github.com/Ableton/link)). Link carries no musical content and no control of the Set. Link is available in all editions, as is Link Audio, new in 12.4, which streams audio between Link-enabled devices on a local network ([Upgrade Live](https://www.ableton.com/en/upgrade-live/)). Neither builds or edits a project. **Not useful for this goal**, except perhaps as a tempo reference.

### 2f. Official scripting and extension APIs as of November 2026

- **Ableton Extensions SDK (public beta).** It was announced on June 2, 2026 with Live 12.4.5b3 ([Ableton blog: Introducing the Extensions SDK](https://www.ableton.com/en/blog/introducing-extensions-sdk/), [Live 12 beta release notes 12.4.5b3](https://www.ableton.com/en/release-notes/live-12-beta/)). Facts from Ableton:
  - Extensions are a JavaScript/TypeScript toolkit running on Node.js. Development needs Node.js v24.16.0 (LTS) or higher ([Ableton KB: Extensions FAQ](https://help.ableton.com/hc/en-us/articles/27303428331420)). The beta's bundled Node.js was updated to 24.22.1 in 12.4.25b1 ([beta release notes](https://www.ableton.com/en/release-notes/live-12-beta/)).
  - "Extensions are available in Live 12 Suite Beta, version 12.4.5 or later. Extensions are not available in Live Standard, Intro, or Lite." ([Extensions FAQ](https://help.ableton.com/hc/en-us/articles/27303428331420)).
  - Users run an Extension from a right-click context menu. "Triggering an extension causes it to run once, performing its task ... then stop." Extensions are "not designed for continuous, real-time, or signal-processing tasks", including "real-time interaction with transport" ([Extensions FAQ](https://help.ableton.com/hc/en-us/articles/27303428331420)). CDM adds that there is "no way to run an Extension programmatically", for example on startup ([CDM](https://cdm.link/ableton-extensions-beta/)).
  - Capabilities: tracks, clips, MIDI notes, devices, tempo, importing files, rendering audio from the Arrangement ([Extensions FAQ](https://help.ableton.com/hc/en-us/articles/27303428331420), [CDM](https://cdm.link/ableton-extensions-beta/)).
  - As of 12.4.16 the stable release notes contain no Extensions entries ([Live 12 release notes](https://www.ableton.com/en/release-notes/live-12/)). The SDK is still beta-only.
  - Community MCP projects on the SDK report missing pieces: no undo API ([philtzjp/live-connector](https://github.com/philtzjp/live-connector)), and no VST/AU or preset loading ([jasper-zheng/ableton-sdk-mcp](https://github.com/jasper-zheng/ableton-sdk-mcp)). These are README claims, **Unverified** against the SDK.
  - The SDK documentation is distributed through the beta portal and is not public ([ableton.github.io/extensions-sdk](https://ableton.github.io/extensions-sdk/)). I could not read the API reference.
  - **Verdict:** promising for Suite users later. Ruled out today for Lite, and not suitable for an always-on agent connection.
- **Max for Live Developer Mode** (12.4) adds "Copy Max for Live Path" on UI elements ([Ableton KB](https://help.ableton.com/hc/en-us/articles/29426709984796)). Suite only in practice.
- **No other official scripting API** was found. I searched the Live 12 release and beta notes and the Help Center. Ableton Move has a "Using Move to control Live" article, which is about hardware control, not scripting ([Ableton KB 14661553738524](https://help.ableton.com/hc/en-us/articles/14661553738524)).

### 2g. Comparison

| Option | Lite | Intro | Standard | Suite | Live 11 | Live 12 | Can build a project (tracks, devices, notes) | Official? |
|---|---|---|---|---|---|---|---|---|
| Python Remote Script (own) | Yes (community-confirmed) | Yes | Yes | Yes | Yes | Yes | Yes, incl. browser loading | No (unsupported API) |
| AbletonOSC (stock) | Yes | Yes | Yes | Yes | Yes | Yes (stated 11+) | Partly: no device or preset loading | No |
| Max for Live device | No | No | Add-on | Yes | Yes | Yes | Mostly, no browser | Yes (LOM documented) |
| Extensions SDK | No | No | No | Beta 12.4.5+ | No | Beta only | Partly; run-once, no undo API reported | Yes (beta) |
| `.mid` files | Yes | Yes | Yes | Yes | Yes | Yes | Notes only | Format is a standard |
| `.als` generation | Risky | Risky | Risky | Risky | Risky | Risky | In theory | No |
| Plain MIDI / Link | Yes | Yes | Yes | Yes | Yes | Yes | No | Yes |

---

## 3. Live Object Model capabilities for this use case

Primary reference: [Cycling '74 LOM](https://docs.cycling74.com/apiref/lom/), written for Max for Live. Python uses the same object model under `Live.*`, plus Python-only extras such as `Application.browser`. Python-only facts come from the unofficial [structure-void Live 12 reference](https://midiremotescripts.structure-void.com/reference/live12/Live.Song.runtime/). Several 12.x additions were announced as "Max for Live API". Python parity for them is shown by open-source projects calling them from Python, which is noted per item.

### 3.1 Tracks, scenes, transport, tempo, scale

- `Song.create_midi_track(index)`, `create_audio_track(index)` and `create_return_track()`. Use `index = -1` to append. Also `delete_track` and `duplicate_track` ([LOM: Song](https://docs.cycling74.com/apiref/lom/song/)).
- `Song.create_scene(index)` returns the new scene. Also `delete_scene`, `duplicate_scene` and `capture_and_insert_scene` ([LOM: Song](https://docs.cycling74.com/apiref/lom/song/)).
- Transport: `start_playing`, `stop_playing`, `continue_playing`, `stop_all_clips`, `tap_tempo`, and the properties `is_playing`, `metronome`, `loop`, `record_mode`, `session_record` and `current_song_time` ([LOM: Song](https://docs.cycling74.com/apiref/lom/song/)).
- Tempo and meter: `tempo`, `signature_numerator`, `signature_denominator`. Groove and swing: `groove_amount`, `swing_amount`, `groove_pool` ([LOM: Song](https://docs.cycling74.com/apiref/lom/song/)).
- Keys and Scales (Live 12): `root_note`, `scale_name`, `scale_intervals` and `scale_mode`. `scale_mode` was added in 12.0.5 ([LOM: Song](https://docs.cycling74.com/apiref/lom/song/), [Live 12 release notes 12.0.5](https://www.ableton.com/en/release-notes/live-12/)). Clip-level `root_note`, `scale_name`, `scale_mode` and `scale_intervals` were added in 12.4.15 ([release notes](https://www.ableton.com/en/release-notes/live-12/)). These are useful for teaching: set the Set's scale, and Live highlights the scale in the MIDI editor.
- Editions: the track and scene limits in section 1.1 apply. Creating a ninth track in Lite should hit a limit. The exact API behaviour (exception vs dialog) is **Unverified**.

### 3.2 Clips and notes

- Session clips: `ClipSlot.create_clip(length_in_beats)` on an empty slot of a MIDI track, plus `ClipSlot.fire()`, `stop()`, `delete_clip()` and `duplicate_clip_to(target)`. `ClipSlot.create_audio_clip(path)` creates an audio clip from an absolute file path ([LOM: ClipSlot](https://docs.cycling74.com/apiref/lom/clipslot/)). Verified 2026-11-09 on 12.4.16 Lite: present, and "Atmos Calm 130 bpm.wav" loaded warped, 32 beats long. The version that added it is still unverified.
- Arrangement clips:
  - `Track.create_midi_clip(start_time, length)`, added in 12.1.10 ([release notes](https://www.ableton.com/en/release-notes/live-12/)).
  - `Track.create_audio_clip(file_path, position)` ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)). The release notes do not say when it was added to Track. The 12.2 note on `create_audio_clip`/`create_midi_clip` concerns Take Lanes, and 12.4 fixed a crash in the Arrangement versions ([release notes](https://www.ableton.com/en/release-notes/live-12/)). Minimum version: **Unverified**.
  - `Track.duplicate_clip_to_arrangement(clip, destination_time)` ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)).
  - LivePilot gates its "Enhanced Arrangement" tier on 12.1.10 from Python, which is evidence of Python parity ([LivePilot README](https://github.com/dreamrec/LivePilot)).
- **Note API (Live 11+), the one to use:**
  - `Clip.add_new_notes({"notes": [...]})` takes note specifications with `pitch` (0–127, 60 = C3), `start_time` and `duration` in beats. Optional fields are `velocity` (default 100), `mute`, `probability`, `velocity_deviation` and `release_velocity`. It returns note IDs. "Available since Live 11.0" ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)).
  - In Python, the iterable holds `Live.Clip.MidiNoteSpecification` objects (unofficial: [structure-void Live 12 Clip](https://midiremotescripts.structure-void.com/reference/live12/Live.Clip.runtime/)). Working examples: [AbletonOSC clip.py:149-155](https://github.com/ideoforms/AbletonOSC/blob/HEAD/abletonosc/clip.py#L149-L155) and [LivePilot notes.py:52-54](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/notes.py#L52-L54).
  - Reading: `get_notes_extended(from_pitch, pitch_span, from_time, time_span)`, `get_all_notes_extended`, `get_selected_notes_extended` and `get_notes_by_id`. Live 11.0 and 11.1 additions ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)).
  - Editing: `apply_note_modifications` replaces "modifying notes with remove_notes followed by set_notes". Also `remove_notes_extended`, `remove_notes_by_id`, `duplicate_notes_by_id` and `duplicate_region` ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)).
  - Live 11 release notes: the new note API "is required for modifying notes without losing MPE data" ([Live 11 release notes 11.0](https://www.ableton.com/en/release-notes/live-11/)).
- **Deprecated:** `Clip.set_notes`, `get_notes` and `remove_notes` (tuple-based). The Cycling '74 LOM no longer documents `set_notes` as a function and mentions it only as what the new API replaces ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)). It still exists in Live 12's Python runtime (unofficial: [structure-void Live 12 Clip](https://midiremotescripts.structure-void.com/reference/live12/Live.Clip.runtime/)). Using it silently drops probability and velocity deviation ([ahujasid#138](https://github.com/ahujasid/ableton-mcp/issues/138)).
- **Quantize:** `Clip.quantize(quantization_grid, amount)` "taking the song's swing_amount into account", plus `quantize_pitch(pitch, grid, amount)` ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)). The integer values of `quantization_grid` are not listed on that page. **Unverified**: look them up in the Python enum before use.
- Clip properties include `name`, `color`, `length`, `looping`, `loop_start`, `loop_end`, `start_marker`, `end_marker`, `launch_mode` and `launch_quantization`. Also `groove`, `has_groove`, `velocity_amount`, `legato`, `muted`, `signature_numerator` and `signature_denominator` ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)).

### 3.3 Devices, the browser, presets

- **Browser (Python only).** `Application.browser` exposes these roots: `instruments`, `drums`, `audio_effects`, `midi_effects`, `sounds`, `samples`, `clips`, `packs`, `plugins`, `max_for_live`, `user_library`, `user_folders`, `current_project`, `colors` and `legacy_libraries`. Each root is a `BrowserItem` with `children`, `iter_children`, `is_loadable` and `uri`. `Browser.load_item(item)` "Loads the provided browser item", and there are also `preview_item(item)` and `hotswap_target` (unofficial: [structure-void Live 12 Browser](https://midiremotescripts.structure-void.com/reference/live12/Live.Browser.runtime/)).
- **How `load_item` targets a track.** Ableton's Push 2 code calls `load_item` either against the current `hotswap_target` or "right of selected" after setting the selection (decompiled [Push2/browser_component.py:534-544](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts/blob/main/Push2/browser_component.py#L534-L544)). Every MCP project sets `song.view.selected_track` first and then calls `load_item` ([ahujasid __init__.py:1305-1314](https://github.com/ahujasid/ableton-mcp/blob/HEAD/AbletonMCP_Remote_Script/__init__.py#L1305-L1314), [bschoepke bridge.py:1615-1621](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/Ableton_Live_MCP/bridge.py#L1615-L1621)). Loading is selection-dependent. Read back `track.devices` afterwards to confirm what was loaded, as bschoepke does by diffing device IDs ([bridge.py:1720-1723](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/Ableton_Live_MCP/bridge.py#L1720-L1723)).
- **Finding items is slow.** There is no search API. Projects walk the tree recursively with depth limits of 8–10. LivePilot's comments say an older 200k-node walk "could stall audio and GUI for several seconds" ([LivePilot browser.py:296-312](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/browser.py#L296-L312)). Use bounded walks, cache URIs, and index once at startup or on demand.
- **What is reachable** depends on installed Packs and the edition's library. Lite ships 1.3+ GB of content and 2 Packs, Beat Tools and Build and Drop ([Upgrade Live](https://www.ableton.com/en/upgrade-live/)). The exact browser tree in Lite is **Unverified**. Capture it on the user's machine with a read-only dump tool.
- **Insert native devices by name (Live 12.3+).** `Track.insert_device(device_name, target_index?)` and `Chain.insert_device(...)` take "the name as it appears in the UI of Live". "Only native Live devices can be inserted. Max for Live devices and plug-in are not supported." ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/), [LOM: Chain](https://docs.cycling74.com/apiref/lom/chain/)). Python usage: [bschoepke bridge.py:1450](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/Ableton_Live_MCP/bridge.py#L1450), and LivePilot gated on `(12,3,0)` ([version_detect.py:23](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/version_detect.py#L23)). This loads a default device, not a preset. Presets still need the browser. Verified 2026-11-09 on 12.4.16 Lite: `insert_device("Limiter")` with no index appends; an unknown or missing device raises RuntimeError "Device EQ Eight not available."; a second instrument raises "Device chains cannot have more than one instrument each." and changes nothing.
- **Device parameters:** `Device.parameters` gives `DeviceParameter` objects with `value`, `min`, `max`, `name`, `is_quantized` and `value_items`. `display_value` was added in 12.2 ([LOM: DeviceParameter](https://docs.cycling74.com/apiref/lom/deviceparameter/), [release notes 12.2](https://www.ableton.com/en/release-notes/live-12/)). Setting `value` works in raw parameter units. LivePilot reports "Invalid display value" problems ([BUGS.md BUG-F8](https://github.com/dreamrec/LivePilot/blob/HEAD/BUGS.md)), so convert carefully.
- **Racks and Drum Racks:**
  - `RackDevice.chains`, `drum_pads`, `visible_drum_pads`, `has_drum_pads`, macros and variations.
  - `RackDevice.insert_chain(index?)` (12.3) and `copy_pad(source, destination)` ([LOM: RackDevice](https://docs.cycling74.com/apiref/lom/rackdevice/)).
  - `DrumPad.note`, `name`, `mute`, `solo`, `chains` and `delete_all_chains` ([LOM: DrumPad](https://docs.cycling74.com/apiref/lom/drumpad/)).
  - `DrumChain.in_note`, get/set since 12.3 ([LOM: DrumChain](https://docs.cycling74.com/apiref/lom/drumchain/), [release notes 12.3](https://www.ableton.com/en/release-notes/live-12/)).
  - Building a kit from scratch on 12.3+: insert Drum Rack, `insert_chain`, set `in_note`, then `Chain.insert_device("Simpler")`, then `SimplerDevice.replace_sample(path)` (12.4). Each step is documented. The full sequence working end-to-end is **Unverified**.
- **Simpler:** `SimplerDevice.replace_sample(file_path)` was added in 12.4 ([release notes 12.4](https://www.ableton.com/en/release-notes/live-12/), [LOM: SimplerDevice](https://docs.cycling74.com/apiref/lom/simplerdevice/)). There are also `playback_mode`, `crop`, `reverse`, `warp_as`, `warp_double` and `warp_half`. Before 12.4, samples could only be loaded through the browser. LivePilot notes Simpler warp mode is not exposed via the Python LOM ([BUGS.md BUG-A2](https://github.com/dreamrec/LivePilot/blob/HEAD/BUGS.md)). Verified 2026-11-09 on 12.4.16 Lite: the Garage Kit pads hold `class_name` "OriginalSimpler" (pad 36 chain device 0), `replace_sample` swaps the sample, and Live renames the Simpler after the new file. Verified 2026-11-10 on 12.4.16 Lite (`set_simpler`): an empty Simpler has `sample` None. `playback_mode` Slicing with `slicing_style` Beat and `slicing_beat_division` 1/8 on a 4-bar 120 BPM loop gives 32 `slices`, 0.25 s apart; note 36 (C1) plays slice 1 and 37 plays slice 2 (by ear). Outside Slicing mode `slices` is empty. `reverse()` writes `<name> R.wav` to the project's `Samples/Processed/Reverse/`, renames the Simpler to match, and one undo restores the original sample (checked by ear).

### 3.4 Groove, warp, automation

- **Groove:** `Song.groove_pool.grooves` returns `Groove` objects with `timing_amount`, `velocity_amount`, `random_amount` and `quantization_amount`. Each clip has `groove` and `has_groove` ([LOM: GroovePool](https://docs.cycling74.com/apiref/lom/groovepool/), [LOM: Groove](https://docs.cycling74.com/apiref/lom/groove/)). Grooves first get into the pool by loading `.agr` files from the browser ([Live 12 manual: Using Grooves](https://www.ableton.com/en/live-manual/12/using-grooves/)). Whether a clip's `groove` can be set from the API is not shown in my extract. **Unverified**.
- **Warp:** audio clips have `warping`, `warp_mode`, `available_warp_modes` and `warp_markers`, plus `add_warp_marker`, `move_warp_marker` and `remove_warp_marker`. The allowed segment BPM range is [5, 999] ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)). Verified 2026-11-10 on 12.4.16 Lite (`set_clip`, "Akeem Groove 120 bpm.wav"): a library loop has `gain` 0.4 = "0.00 dB" and `available_warp_modes` 0-4 and 6 (no REX). Setting start/end markers and loop to 4..8 with `pitch_coarse` -2 and `warp_mode` Complex plays bar 2 only, 2 semitones down. Setting `warping` false takes effect at once (the LOM "deferred" note did not show up when the value was read straight after) and converts the markers to seconds (4 beats became 2 s at 120 BPM). One undo restores both.
- **Automation:** clips have `clear_envelope(param)`, `clear_all_envelopes()` and `has_envelopes` ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)). Writing envelope points from Python is done in wstierhout and LivePilot via clip envelopes. Arrangement track automation is a known gap in wstierhout ([PR #8](https://github.com/wstierhout/ableton-live-mcp/issues/8)). Keep automation out of the first version.

### 3.5 Arrangement vs Session

Session View is the better fit for a beginner tool. Its slots, scenes, `create_clip`, `fire` and scene launch have been in the API since before Live 11. Arrangement MIDI clip creation needs 12.1.10+. The minimum version for audio clip creation in the Arrangement is unclear (above). Locators: `Song.cue_points`, `set_or_delete_cue`, and jumping between cues. Arrangement-specific editing beyond creating and duplicating clips is thin. AbletonOSC lists "Interact with clips in arrangement view" as open ([#124](https://github.com/ideoforms/AbletonOSC/issues/124)).

### 3.6 What the LOM cannot do

| Not possible via LOM | Evidence | Workaround |
|---|---|---|
| Save the Set | No save function in [LOM: Song](https://docs.cycling74.com/apiref/lom/song/). wstierhout's changelog: saving through the Live API is not possible on 12.4 ([CHANGELOG.md](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/CHANGELOG.md)) | Ask the user to press Cmd/Ctrl+S. `Song.file_path` and `name` tell whether the Set was ever saved |
| Export or render audio | No export function in the LOM. Export Audio/Video is a UI dialog ([manual](https://www.ableton.com/en/live-manual/12/managing-files-and-sets/)). The Extensions SDK can render but is Suite beta ([CDM](https://cdm.link/ableton-extensions-beta/)) | User exports. Record via an armed audio track is possible (wstierhout `record_section`) |
| Open or create another Set | No such function in [LOM: Application](https://docs.cycling74.com/apiref/lom/application/) (absence; **Unverified** for hidden Python APIs) | User action |
| Synthesize new audio sample content | No API for writing audio data | Generate a `.wav` outside Live, then `create_audio_clip(path)` or `replace_sample(path)` |
| Insert plug-ins or M4L devices by name | `insert_device` is native-only ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)) | Browser `plugins` root + `load_item`. VST3 loading via URI failed in ahujasid [#79](https://github.com/ahujasid/ableton-mcp/issues/79) |
| Search the browser | Only tree traversal exists ([structure-void Browser](https://midiremotescripts.structure-void.com/reference/live12/Live.Browser.runtime/)) | Bounded walk + cache/index |
| Query or set the Main (master) track | "Live Master Tracks cannot be queried or set" (Max docs, [Cycling '74 Live API](https://docs.cycling74.com/userguide/m4l/live_api/)) | `Song.master_track` exists in the LOM ([Song](https://docs.cycling74.com/apiref/lom/song/)). The two Cycling '74 pages conflict. Verified 2026-11-09 on 12.4.16 Lite: `Song.master_track.insert_device("Limiter")` works from Python |
| Detect a modal dialog except by polling | `open_dialog_count` is observable ([LOM: Application](https://docs.cycling74.com/apiref/lom/application/)) | Check before mutating; report to the agent |

---

## 4. Existing MCP servers for Ableton

Method: I downloaded the source of the eight most relevant repositories and read it without running anything. Metadata comes from the GitHub REST API on 2026-11-05. Line citations point at the default branch HEAD on that date. A GitHub search for "ableton mcp" returned 236 repositories ([GitHub search](https://github.com/search?q=ableton+mcp&type=repositories)). The selection below covers the most-starred and every distinct architecture.

### 4.1 Comparison

| Repo | Stars / last push / licence | Live↔server | Framing | Main-thread marshalling | Note API | Undo grouping | Bind / auth | Tools |
|---|---|---|---|---|---|---|---|---|
| [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp) | 3,302 / 2026-09-22 / MIT | TCP 127.0.0.1:9877 | None (parse until valid) | Writes via `schedule_message`; **reads on socket thread** | Writes `set_notes` | No | localhost; no auth | 37 |
| [uisato/ableton-mcp-extended](https://github.com/uisato/ableton-mcp-extended) | 280 / 2026-05-07 / MIT | TCP 9877 (+UDP 9878) | None | As above | `set_notes` | No | localhost; no auth | 46 |
| [bschoepke/ableton-live-mcp](https://github.com/bschoepke/ableton-live-mcp) | 237 / 2026-10-06 / MIT | TCP 127.0.0.1:8765, JSON-RPC | Newline | `schedule_message(0)` + Event, single-flight, timeout skip | Extended only | No | localhost; **no auth + Python exec** | 38 |
| [wstierhout/ableton-live-mcp](https://github.com/wstierhout/ableton-live-mcp) | 69 / 2026-10-23 / MIT | TCP 127.0.0.1:9877 | None | Queue + `schedule_message`, cancel flag | Extended, legacy fallback | Batches only | localhost; no auth | 156 |
| [dreamrec/LivePilot](https://github.com/dreamrec/LivePilot) | 91 / 2026-11-05 / BSL-1.1 | TCP 127.0.0.1:9878; M4L UDP | Newline, 4 MB cap | Queue + `schedule_message(0)`, phantom-write protection | Extended | Partial | localhost; no TCP auth | 474 |
| [Simon-Kansara/ableton-live-mcp-server](https://github.com/Simon-Kansara/ableton-live-mcp-server) | 397 / 2025-03-26 / MIT | MCP → daemon → AbletonOSC | None | AbletonOSC tick | n/a | No | AbletonOSC 0.0.0.0 | 1 (broken, [#5](https://github.com/Simon-Kansara/ableton-live-mcp-server/issues/5)) |
| [xiaolaa2/ableton-copilot-mcp](https://github.com/xiaolaa2/ableton-copilot-mcp) | 94 / 2026-07-29 / MIT | ableton-js (UDP) | ableton-js | ableton-js tick | `set_notes` | No (own SQLite history) | localhost | 41 |
| [ideoforms/AbletonOSC](https://github.com/ideoforms/AbletonOSC) (backend) | 843 / 2025-11-19 / MIT | UDP 11000/11001 | OSC | Non-blocking socket on ~100 ms tick | Extended | No | **0.0.0.0**; no auth | n/a |

### 4.2 Per-project notes

**ahujasid/ableton-mcp: the original.** Most Python Ableton MCPs descend from it.
- Architecture: a FastMCP stdio server on the official `mcp[cli]>=1.9.0,<2.0.0` SDK talks TCP to a `_Framework.ControlSurface` script on port 9877 ([server.py:283](https://github.com/ahujasid/ableton-mcp/blob/HEAD/MCP_Server/server.py#L283), [__init__.py:19](https://github.com/ahujasid/ableton-mcp/blob/HEAD/AbletonMCP_Remote_Script/__init__.py#L19)). Messages are bare JSON, read until `json.loads` succeeds ([__init__.py:213-233](https://github.com/ahujasid/ableton-mcp/blob/HEAD/AbletonMCP_Remote_Script/__init__.py#L213-L233)).
- Read commands call the LOM directly on the socket thread ([__init__.py:283-290](https://github.com/ahujasid/ableton-mcp/blob/HEAD/AbletonMCP_Remote_Script/__init__.py#L283-L290)).
- Notes are written with legacy `set_notes` ([:804](https://github.com/ahujasid/ableton-mcp/blob/HEAD/AbletonMCP_Remote_Script/__init__.py#L804)).
- There are no scene or mixer tools and no undo grouping.
- Telemetry goes to the maintainer's Supabase project. The anonymous tier is **on by default**, with opt-out environment variables. A "dataset recording" tier collecting prompts and MIDI is opt-in ([telemetry.py:1-11](https://github.com/ahujasid/ableton-mcp/blob/HEAD/MCP_Server/telemetry.py#L1-L11), [README](https://github.com/ahujasid/ableton-mcp)).
- Known bugs: [#136](https://github.com/ahujasid/ableton-mcp/issues/136) (timeout override), [#138](https://github.com/ahujasid/ableton-mcp/issues/138) (dropped notes reported as success), [#134](https://github.com/ahujasid/ableton-mcp/issues/134) (success message reads state before it is applied), [#127](https://github.com/ahujasid/ableton-mcp/issues/127) (cannot load effects onto master), [#130](https://github.com/ahujasid/ableton-mcp/issues/130) (massive token usage).
- Reuse: the overall shape and the pytest `_Framework` stubs. Avoid: the framing, off-thread reads, `set_notes` and the telemetry.

**uisato/ableton-mcp-extended.** An ahujasid derivative without the upstream framing fixes: str-buffer decoding breaks on split UTF-8 ([__init__.py:136-160](https://github.com/uisato/ableton-mcp-extended/blob/HEAD/AbletonMCP_Remote_Script/__init__.py#L136-L160)). `pip install` is broken ([#22](https://github.com/uisato/ableton-mcp-extended/issues/22)). The ElevenLabs SDK is a hard dependency. Not recommended.

**bschoepke/ableton-live-mcp.** Strong engineering in the transport.
- Newline JSON-RPC with size caps ([src/bridge.py:123-124,219](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/src/bridge.py#L123-L124)).
- `schedule_message(0, invoke)` with a single-flight semaphore, "abandoned" flag and stall cooldown ([bridge.py:243-388](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/Ableton_Live_MCP/bridge.py#L243-L388)).
- An 83-test fake LOM ([tests/test_remote_bridge_fake_live.py](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/tests/test_remote_bridge_fake_live.py)).
- The primary surface, however, is generic `live_get`/`live_set`/`live_call`, plus **unauthenticated `live_eval`/`live_exec` that run arbitrary Python inside Live** ([bridge.py:1745-1771](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/Ableton_Live_MCP/bridge.py#L1745-L1771)). Any local process can do the same.
- The MCP layer is hand-rolled and echoes the client's protocol version ([mcp_stdio.py:74-119](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/src/mcp_stdio.py#L74-L119)).
- Tested only on Live Suite 12.3.8 ([README](https://github.com/bschoepke/ableton-live-mcp)).
- Reuse: the marshalling and the fake LOM. Avoid: exec and the generic setters.

**wstierhout/ableton-live-mcp.** The cleanest typed-tool design.
- Built on the official SDK (`mcp[cli]` locked at 1.29.1), with `ToolAnnotations` on tools ([tools/session.py:23](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/tools/session.py#L23)) and five MCP prompts ([tools/prompts.py](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/tools/prompts.py)).
- Explicit split dispatch tables for mutating and read-only commands ([remote_script/__init__.py:338,436](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/remote_script/__init__.py#L338)). Cancel-on-timeout ([:485-527](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/remote_script/__init__.py#L485-L527)).
- Batches run inside one undo step ([:1592-1611](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/remote_script/__init__.py#L1592-L1611)).
- Pure-Python music generators (drums, chords, basslines). Offline `.als` readers ([tools/offline.py](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/tools/offline.py)).
- CI with AST checks of the dispatch table.
- Weak points: undelimited framing and no fake LOM. Supports Live 11 and 12. No edition claims. 156 tools is a lot for a beginner.

**dreamrec/LivePilot.** The best Remote Script transport core, inside a huge product.
- Newline JSON on 127.0.0.1:9878, UTF-8-safe buffering, 4 MB line cap ([server.py:314-384](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/server.py#L314-L384)).
- Queue plus `schedule_message(0)` ([:414](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/server.py#L414)). Committed/cancelled flags prevent "phantom writes" after a client timeout ([:148,455-487](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/server.py#L455-L487)).
- An exception-to-error-code router ([router.py:88-111](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/router.py#L88-L111)), a version feature table ([version_detect.py](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/version_detect.py)), and a client that never retries ambiguous writes ([connection.py:199-300](https://github.com/dreamrec/LivePilot/blob/HEAD/mcp_server/connection.py#L199-L300)).
- But: 474 tools, a pin on `fastmcp>=4.0.11,<4.1.0` internals ([requirements.txt](https://github.com/dreamrec/LivePilot/blob/HEAD/requirements.txt)), a reverse-engineered private Splice GraphQL API ([http_bridge.py](https://github.com/dreamrec/LivePilot/blob/HEAD/mcp_server/splice_client/http_bridge.py)), and the **BSL-1.1** licence, which converts to MIT on 2030-04-13 ([LICENSE](https://github.com/dreamrec/LivePilot/blob/HEAD/LICENSE)). Do not copy code verbatim. Learn from the patterns.

**Simon-Kansara/ableton-live-mcp-server.** One tool, broken ([#5](https://github.com/Simon-Kansara/ableton-live-mcp-server/issues/5)), unmaintained since 2025-03. Its star count reflects early visibility. Avoid.

**xiaolaa2/ableton-copilot-mcp.** TypeScript on [ableton-js](https://github.com/leolabs/ableton-js) 4.0.4: UDP, UUID correlation, gzip chunking, its own Remote Script. Uses `set_notes`. Selection-dependent device loading ([PR #17](https://github.com/xiaolaa2/ableton-copilot-mcp/issues/17)). Reuse idea: ableton-js as a proven transport if you choose TypeScript.

**Newer projects (README claims only, Unverified against source):**
- [valentijnh/livebridge-ableton-mcp](https://github.com/valentijnh/livebridge-ableton-mcp): stdlib-only Remote Script, JSON lines on TCP 9880, random token, "every change is one undo step", claims every Live 12 edition.
- [Pantani/ableton-mind](https://github.com/Pantani/ableton-mind): TypeScript MCP, NDJSON JSON-RPC to a Python script, transactions.
- [Ziforge/ableton-liveapi-tools](https://github.com/Ziforge/ableton-liveapi-tools): GPL-3.0, queue drained in `update_display()`.
- Extensions SDK projects, all needing the 12.4.x Suite beta: [OthmanAdi/loophole](https://github.com/OthmanAdi/loophole), [jasper-zheng/ableton-sdk-mcp](https://github.com/jasper-zheng/ableton-sdk-mcp), [pnomolos/live-wire](https://github.com/pnomolos/live-wire), [philtzjp/live-connector](https://github.com/philtzjp/live-connector).
- Max for Live, Suite only: [romsau/agent4live](https://github.com/romsau/agent4live).

### 4.3 What to reuse, what to avoid, what is missing

- **Reuse (as patterns, re-implemented; check each licence before copying code):**
  - Newline-delimited JSON with request IDs and size caps (bschoepke, LivePilot).
  - Queue plus `schedule_message(0)` with cancel-if-not-started (wstierhout, LivePilot).
  - Undo-grouped batches (wstierhout).
  - Extended note API only (bschoepke, LivePilot, AbletonOSC).
  - `hasattr`/version feature gates (LivePilot).
  - Fake `Live` module for tests (bschoepke, LivePilot).
  - `ToolAnnotations` and MCP prompts (wstierhout).
- **Avoid:**
  - Parse-until-valid framing.
  - LOM access off the main thread.
  - `set_notes`.
  - Exec/eval tools.
  - `0.0.0.0` binds and unauthenticated sockets.
  - Telemetry.
  - Hundreds of tools, which inflates token use ([ahujasid#130](https://github.com/ahujasid/ableton-mcp/issues/130)).
- **Gaps nobody fills well:**
  - **Edition awareness.** No project checks `get_variant()` or adapts its tools to Lite's 8-track limit and instrument set.
  - **Beginner-oriented explanations** and readable Set summaries.
  - **Authenticated local transport** in the popular projects.
  - **Verified Lite testing.** Only one Lite bug report exists ([#138](https://github.com/ahujasid/ableton-mcp/issues/138)).

---

## 5. MCP specification facts

### 5.1 Version

- The current protocol version is **2026-07-28** ([MCP: Versioning](https://modelcontextprotocol.io/specification/versioning)). The previous revision was 2025-11-25.
- **Major changes** in 2026-07-28 ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)):
  - MCP is now stateless. The `initialize` handshake is gone. Every request carries its protocol version and client capabilities in `_meta`.
  - Servers MUST implement `server/discover`.
  - Protocol-level sessions (`Mcp-Session-Id`) are removed.
  - `subscriptions/listen` replaces resource subscribe/unsubscribe and the HTTP GET stream.
  - `ping` and `logging/setLevel` are removed.
  - Tasks move to an official extension.
  - "Multi round-trip requests" replace server-initiated requests: a server returns `InputRequiredResult` and the client retries with `inputResponses`.
  - Every result carries a `resultType`.
- **Deprecated:** Roots, Sampling and Logging. For stdio, the suggested migration for logging is "log to `stderr`" ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).
- Backward compatibility: clients and servers can detect the older handshake era and fall back ([stdio: Backward Compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)). Which MCP clients (Claude Desktop, Claude Code and others) speak 2026-07-28 natively on the research date is **Unverified**. Use an SDK that serves both eras (5.4).

### 5.2 Transports

- **stdio:** "the client launches the MCP server as a subprocess". Messages are newline-delimited and "MUST NOT contain embedded newlines". The server MUST NOT write anything to stdout that is not a valid MCP message, and MAY log to stderr ([stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)). Custom transports over TCP or Unix sockets "SHOULD reuse the stdio framing" ([transports overview](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)). This is a good argument for using the same newline framing on the server-to-Live link.
- **Streamable HTTP:** a single POST endpoint. Servers "MUST validate the Origin header". Locally they "SHOULD bind only to localhost (127.0.0.1)" and "SHOULD implement proper authentication" ([Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)). The older HTTP+SSE transport is Deprecated (same page).
- For a single-user desktop tool, stdio is the simpler choice. The MCP client starts the server, and there is no network listener on the MCP side.

### 5.3 Primitives and how they map to this domain

| Primitive | Who controls it ([spec](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)) | Use here |
|---|---|---|
| **Tools** | Model-controlled; the spec says a human SHOULD be able to deny invocations ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)) | Every action: `create_midi_track`, `write_drum_pattern`, `load_instrument`, `set_tempo`, `get_set_overview` |
| **Resources** | Application-driven; URI-addressed, with templates ([resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)) | Read-only context: `live://set/overview`, `live://capabilities` (edition, version, limits), `live://browser/instruments`, lesson texts. Note that resources depend on the client choosing to include them. Expose the same data as read-only tools as well |
| **Prompts** | User-controlled, typically slash commands ([prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)) | Guided lessons: `/lesson-first-beat`, `/lesson-bassline`, `/explain-my-set` |

Tool details ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [schema.ts](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts#L1912)):
- **Annotations:** `title`, `readOnlyHint` (default false), `destructiveHint` (default true, meaningful only when not read-only), `idempotentHint` (default false), `openWorldHint` (default true). Clients "MUST consider tool annotations to be untrusted unless they come from trusted servers". For this domain, mark reads `readOnlyHint: true`. Mark deletes `destructiveHint: true`. Set `openWorldHint: false` on everything, because the world is the local Set.
- **Structured output:** `outputSchema` (JSON Schema 2020-12) plus `structuredContent`. "Servers MUST provide structured results that conform to this schema". For backward compatibility, a tool returning structured content "SHOULD also return the serialized JSON in a TextContent block". For a beginner tool, return short human-readable text plus `structuredContent`.
- **Errors:** "Tool Execution Errors contain actionable feedback that language models can use to self-correct", returned with `isError: true`. Protocol errors are for malformed requests and unknown tools. Map "Lite allows only 8 tracks" to a tool execution error with a suggestion.
- **Ordering:** servers SHOULD return tools in a deterministic order to help prompt caching ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).
- **Progress:** `notifications/progress` with `progressToken`, `progress`, `total` and `message` ([schema](https://modelcontextprotocol.io/specification/2026-07-28/schema)). Useful for slow browser indexing.
- **Elicitation:** now delivered as an `InputRequiredResult` within `tools/call` (MRTR) ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)). Possible use: ask the user to confirm a destructive operation, or choose between two matching presets. Client support is **Unverified**, so do not depend on it.
- **Stateful tools:** "MCP has no protocol-level session". Use explicit handles passed as arguments ([tools: Stateful Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)). Here, the Live Set is the state. Address objects by index plus name, and re-read before writing.

### 5.4 Official SDKs

| SDK | Package and version (2026-11-05) | Install (official docs) | Notes |
|---|---|---|---|
| Python | `mcp` **2.3.0**, released 2026-10-02 ([PyPI](https://pypi.org/project/mcp/)) | `uv add "mcp[cli]"` or `pip install "mcp[cli]"` ([python-sdk README](https://github.com/modelcontextprotocol/python-sdk)) | High-level class renamed from `FastMCP` to `MCPServer` (`from mcp.server import MCPServer`). v2 "speaks the 2026-07-28 revision" and also "answers a 2025-era client's `initialize`" ([What's new in v2](https://py.sdk.modelcontextprotocol.io/whats-new/)). The 1.x line gets maintenance releases (1.30.0, [releases](https://github.com/modelcontextprotocol/python-sdk/releases)). **Python version conflict:** README says "Python 3.11+"; PyPI metadata says `>=3.10`. Use 3.11+. |
| TypeScript | v2: `@modelcontextprotocol/server` **2.4.0** and `@modelcontextprotocol/client` 2.4.0. v1: `@modelcontextprotocol/sdk` 1.32.2 ([npm](https://www.npmjs.com/package/@modelcontextprotocol/sdk), [releases](https://github.com/modelcontextprotocol/typescript-sdk/releases)) | `npm install @modelcontextprotocol/server` ([typescript-sdk README](https://github.com/modelcontextprotocol/typescript-sdk)) | v2 is "the stable release line, implementing the 2026-07-28 MCP spec". v1.x gets fixes for at least six months after the v2 release (2026-07-27) ([README](https://github.com/modelcontextprotocol/typescript-sdk)) |

Third-party `fastmcp` (jlowin) is at 4.1.6 ([PyPI](https://pypi.org/project/fastmcp/)). It is not the official SDK, and LivePilot shows the risk of pinning to its internals.

Language choice: the in-Live script must be Python anyway. A Python MCP server lets both sides share one codebase, one test harness and one protocol module. TypeScript would only pay off if reusing ableton-js.

---

## 6. Architecture analysis

### 6.1 Candidates

**A. MCP server ⇄ local TCP (JSON lines) ⇄ own Remote Script.**
```
MCP client (Claude) --stdio--> mcp-server (Python 3.11+, official SDK)
                                   |  TCP 127.0.0.1:<port>, newline JSON, token
                                   v
                         Remote Script in Live (stdlib only, Python 3.7 syntax)
                                   |  schedule_message -> main thread
                                   v
                              Live Object Model
```
- Pros:
  - Works in every edition (section 1.3). Full LOM plus browser access.
  - You own the protocol, so you get request IDs, acks, batching, undo grouping and capability reporting.
  - Most logic and all music theory live in the external process, which you can restart freely.
- Cons: the API is undocumented and unsupported. The script has no hot reload. You must install a folder into the User Library.

**B. MCP server ⇄ OSC/UDP ⇄ AbletonOSC (stock or patched).**
- Pros: a mature MIT Remote Script that you do not have to write. There is a Python client (pylive).
- Cons:
  - No browser or device loading without patching.
  - No write acks and no correlated errors.
  - Fixed reply port, so one client at a time.
  - Binds `0.0.0.0`.
  - No undo grouping.
  - Patching it means maintaining a fork of a project whose last push was 2025-11 (section 2c).

**C. Hybrid: A plus offline `.mid` generation (and optional `.als` reading).**
- Same as A, plus a `write_midi_file` tool that runs entirely in the MCP server. It works when Live is closed, when the Remote Script is not installed yet, or as a teaching artefact.
- `.als` reading could power "explain this Set file" without Live running.
- `.als` writing is excluded (section 2d).

Ruled out for the target audience: Max for Live (no Lite or Intro), and the Extensions SDK (Suite beta only, run-once).

### 6.2 Design concerns, applied to A/C

- **Process boundaries.** There are three processes: the MCP client, the MCP server and Live. The Remote Script should be thin: transport, dispatch, LOM calls and serialisation. Everything else goes in the MCP server: validation, music theory, explanations, edition rules and the browser index. This keeps the reload problem small (2a).
- **Protocol design.**
  - Framing: newline-delimited JSON, as the MCP spec recommends for custom byte-stream transports ([transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)), with a size cap per line.
  - Envelope: `{"v":1,"id":"…","op":"…","args":{…},"token":"…"}`, answered by `{"id":"…","ok":true,"result":…}` or `{"id":"…","ok":false,"error":{"code":"…","message":"…","data":…}}`.
  - A `hello` op returns the script version, protocol version, `get_version_string()`, `get_variant()` (Live 12) and a capability list.
  - A `batch` op runs a list of ops in one main-thread callback inside one undo step.
  - These choices follow the best patterns found in section 4.
- **Main-thread marshalling.**
  - Never touch the LOM off the main thread.
  - Either poll a non-blocking socket from a `schedule_message` tick (AbletonOSC style), or use a socket thread that enqueues work and calls `schedule_message(0, …)` (LivePilot style).
  - One command executes at a time.
  - Mark a command cancelled if the caller times out before it starts, so it never applies late ([LivePilot server.py:455-487](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/server.py#L455-L487)).
- **Timeouts and reconnects.**
  - Per-op timeouts. Reads ~10 s and writes ~15 s are the norm in the existing projects.
  - The client budget should exceed the script budget ([wstierhout connection.py:27-29](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/connection.py#L27-L29)).
  - Reconnect lazily on the next call.
  - **Never automatically retry a write after a timeout**, because the write may have happened ([LivePilot connection.py](https://github.com/dreamrec/LivePilot/blob/HEAD/mcp_server/connection.py)).
- **Live not running.** Every Live-dependent tool returns a tool execution error with a fix-it message ("Open Live and select <ScriptName> under Settings → Link, Tempo & MIDI → Control Surface"). Offline tools (`.mid` writing, theory helpers, lessons) still work.
- **Error propagation.** Map Python exceptions to stable codes in the script (`INVALID_ARG`, `NOT_FOUND`, `EDITION_LIMIT`, `LIVE_STATE`, `INTERNAL`), as LivePilot's router does ([router.py:88-111](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/router.py#L88-L111)). In the MCP server, turn them into `isError: true` results with guidance ([tools: Error Handling](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).
- **Undo granularity.** One tool call is one undo step, so "Cmd+Z" undoes exactly what the agent just did. Wrap each call in `begin_undo_step`/`end_undo_step` in `try/finally`. Expose an `undo_last` tool that calls `Song.undo()`. Multi-tick behaviour is **Unverified** (2a).
- **Idempotency.**
  - The Python LOM exposes no documented stable track or device IDs. Note IDs are stable within a clip ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)). Stable Python object IDs: **Unverified**.
  - Prefer "ensure"-style tools: `ensure_track(name="Drums", type="midi")` returns an existing match instead of creating a duplicate.
  - Make every write tool return the resulting state, so the agent can verify instead of retrying blindly.
  - "Replace notes in clip" is naturally idempotent. "Add notes" is not.
- **Security.**
  - Bind 127.0.0.1 only.
  - Generate a random token at script start and write it to a file readable only by the user, as LivePilot and valentijnh do ([LivePilot m4l_bridge.py:40-56](https://github.com/dreamrec/LivePilot/blob/HEAD/mcp_server/m4l_bridge.py#L40-L56), [valentijnh README](https://github.com/valentijnh/livebridge-ableton-mcp)).
  - No eval/exec op.
  - Allowlist directories for audio-file import.
  - Any local process can otherwise drive Live, as shown by bschoepke's exec tools (4.2).
- **Compatibility across Live 11/12 and editions.**
  - Detect at runtime: `Application.get_major_version()`, `get_minor_version()` and `get_bugfix_version()` ([LOM: Application](https://docs.cycling74.com/apiref/lom/application/)). On Live 12, use `get_variant()` for the edition ([structure-void](https://midiremotescripts.structure-void.com/reference/live12/Live.Application.runtime/)). Use `hasattr` for `insert_device` (12.3), `replace_sample` (12.4) and arrangement `create_midi_clip` (12.1.10).
  - Keep an edition table (section 1.1) in the MCP server. Refuse or adapt before calling Live, for example suggesting Drift instead of Wavetable in Lite.
  - Write the script in Python 3.7 syntax.
- **Testability.**
  - Put the protocol and dispatch logic in modules that import `Live` lazily.
  - Unit-test them against a fake `Live` module, which bschoepke and LivePilot both do ([bschoepke tests](https://github.com/bschoepke/ableton-live-mcp/blob/HEAD/tests/test_remote_bridge_fake_live.py), [LivePilot tests/test_remote_notes.py:52-81](https://github.com/dreamrec/LivePilot/blob/HEAD/tests/test_remote_notes.py#L52-L81)).
  - Test the MCP server against a fake bridge.
  - Keep a short manual smoke checklist for real Live (Lite and one full edition), because none of the projects automate tests against Live.
- **Packaging and installation.**
  - Distribute the MCP server as a Python package runnable with `uvx`/`uv`, using the SDK's documented install path ([python-sdk README](https://github.com/modelcontextprotocol/python-sdk)).
  - Provide an `install-remote-script` command that copies the script folder into `<User Library>/Remote Scripts/` ([Ableton KB 209072009](https://help.ableton.com/hc/en-us/articles/209072009)).
  - Tell the user to restart Live and pick the script in the Control Surface dropdown.
  - A `doctor` command checks the folder, the port and the token, and reads `Log.txt` for tracebacks. wstierhout ships one ([cli.py:124](https://github.com/wstierhout/ableton-live-mcp/blob/HEAD/ableton_live_mcp/cli.py#L124)).

### 6.3 Recommendation

**Choose C: architecture A plus offline `.mid` export.**
- A Python MCP server on the official `mcp` SDK (v2, stdio).
- A thin, stdlib-only Remote Script of your own, using the newline-JSON, token, request-ID protocol described above, with main-thread marshalling and one undo step per call.
- Edition and version capability detection in `hello`.
- An offline MIDI file writer.

Trade-offs accepted:
- You depend on an unsupported API. Mitigate with feature detection and a small script surface.
- Users must install a folder once.
- Some things remain manual: save, export, opening Sets.

Revisit the Extensions SDK if it reaches stable and lower editions. Ableton currently says it is Suite-only ([Extensions FAQ](https://help.ableton.com/hc/en-us/articles/27303428331420)).

---

## 7. What makes the agent useful to a beginner

- **Readable state first.** A `get_set_overview` tool should return a compact, plain-language description: tempo, time signature, scale, each track with its instrument, clip names and lengths, and which scene does what. Without it the agent guesses, and token-heavy raw dumps are a known problem ([ahujasid#130](https://github.com/ahujasid/ableton-mcp/issues/130)). Return text for the model and `structuredContent` for precision (5.3).
- **Few, task-shaped tools.**
  - Examples: `create_drum_pattern(style, bars)`, `create_bassline(root, scale, pattern)`, `create_chord_progression(key, degrees)`, `load_instrument(kind)`, `explain(target)`.
  - Avoid mirroring the whole LOM. Small typed tools with annotations follow wstierhout's design (4.2).
  - Edition-aware choices: Lite has Drum Rack, Simpler, Impulse and Drift but no Operator or Wavetable ([Upgrade Live](https://www.ableton.com/en/upgrade-live/)).
- **Built-in music theory.**
  - Scales as interval lists, chords as stacked thirds, General MIDI–style drum note numbers, and Euclidean rhythms.
  - These fit in a few hundred lines of plain Python, and Live 12 itself exposes `scale_intervals` and `root_note` ([LOM: Song](https://docs.cycling74.com/apiref/lom/song/)).
  - `music21` is BSD-3-Clause, but its wheel is about 20 MB ([PyPI music21 10.5.2](https://pypi.org/project/music21/)). That is not needed for this scope.
  - wstierhout's pure-Python generators show the scope needed ([section 4.2](https://github.com/wstierhout/ableton-live-mcp)).
  - Drum Rack note layout in a given kit varies by preset. Read `DrumPad.note` and `name` from the loaded kit instead of assuming ([LOM: DrumPad](https://docs.cycling74.com/apiref/lom/drumpad/)).
- **Explain what was built.** Every write tool returns what changed and why, in beginner terms ("I put the kick on beats 1 and 3 and the snare on 2 and 4, a classic backbeat"). Name clips and tracks descriptively. Use clip colours per role. Setting the Set's scale makes Live highlight scale notes, which supports "Keys and Scales" learning ([Upgrade Live](https://www.ableton.com/en/upgrade-live/)).
- **Guided lessons as MCP prompts.**
  - User-invoked prompts ([prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)) can follow the chapter order of Ableton's free [Learning Music](https://learningmusic.ableton.com/) course: Beats, Notes and scales, Chords, Basslines, Melodies, Song structure.
  - For sound design, point to [Learning Synths](https://learningsynths.ableton.com/), described by Ableton as "a web-based synth and accompanying lessons" ([Ableton site navigation](https://www.ableton.com/en/release-notes/live-12-beta/)).
  - Link the relevant manual chapters: [Session View](https://www.ableton.com/en/live-manual/12/session-view/), [Editing MIDI](https://www.ableton.com/en/live-manual/12/editing-midi/), [Instrument, Drum and Effect Racks](https://www.ableton.com/en/live-manual/12/instrument-drum-and-effect-racks/), [Live Instrument Reference](https://www.ableton.com/en/live-manual/12/live-instrument-reference/).
  - Live also has built-in lessons ("Help View" was renamed "Built-In Lessons" in 11.3, [Ableton KB: Live 11 Technical FAQ](https://help.ableton.com/hc/en-us/articles/360019140859)).
  - Learning Music states no reuse licence ([learningmusic.ableton.com](https://learningmusic.ableton.com/)), so link to it; do not copy its content.
- **Safe by default.**
  - Undo-grouped changes, so a beginner can press Cmd+Z.
  - Never delete without confirmation (`destructiveHint`).
  - Warn before exceeding Lite's 8 tracks.
  - Remind the user to save, because the agent cannot (3.6).

---

## 8. Risks and open questions

### 8.1 Risks

1. **Undocumented, unsupported Python API.** Signatures changed widely between 11 and 12 ([structure-void](https://midiremotescripts.structure-void.com/guides/runtime-changes-11-to-12/)). The interpreter jumped from 3.7 to 3.11.6 in 12.1 ([release notes](https://www.ableton.com/en/release-notes/live-12/)). Ableton gives no support ([KB](https://help.ableton.com/hc/en-us/articles/209072009)). Mitigations: feature detection, a small script, and a smoke test per Live update.
2. **Lite support is not officially stated.** It rests on community evidence ([#138](https://github.com/ahujasid/ableton-mcp/issues/138)). If a future Lite build blocked third-party scripts, the only fallback left would be `.mid` files.
3. **Lite limits:**
   - 8 tracks and 2 returns ([Upgrade Live](https://www.ableton.com/en/upgrade-live/)).
   - Missing instruments.
   - Saving disabled if non-Lite elements enter the Set ([KB 115000258930](https://help.ableton.com/hc/en-us/articles/115000258930)). Whether browser-loading a Pack preset that contains a non-Lite device triggers this is **Unverified**.
   - The API behaviour at the track limit is **Unverified**.
4. **Browser loading quirks:**
   - Selection-dependent targeting.
   - Slow recursive walks that can stall Live's UI ([LivePilot browser.py:296-312](https://github.com/dreamrec/LivePilot/blob/HEAD/remote_script/LivePilot/browser.py#L296-L312)).
   - No master or return targeting in naive code ([ahujasid#127](https://github.com/ahujasid/ableton-mcp/issues/127)).
   - Plug-in loading failures ([ahujasid#79](https://github.com/ahujasid/ableton-mcp/issues/79)).
   - Stale cached `BrowserItem`s after a library change (**Unverified**).
5. **Thread safety.** LOM access off the main thread is unsafe in principle, and Ableton documents nothing either way. Sources disagree on whether threads are safe at all inside Live (2a).
6. **Modal dialogs** (edition limits, missing samples) can block the main thread and make every request time out (1.4).
7. **Licensing of reference code:**
   - gluon's decompiled Ableton scripts carry no licence. The GitHub API reports none, and they are Ableton's code ([gluon repo](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts)).
   - structure-void's docs are CC BY-NC-SA 4.0 (site footer, [structure-void](https://midiremotescripts.structure-void.com/reference/live12/Live.Application.runtime/)).
   - LivePilot is BSL-1.1 ([LICENSE](https://github.com/dreamrec/LivePilot/blob/HEAD/LICENSE)).
   - Ziforge and live-wire are GPL-3.0 (4.2).
   - Read these for understanding. Copy code only from MIT projects (ahujasid, bschoepke, wstierhout, AbletonOSC), with attribution.
8. **MCP spec churn.** 2026-07-28 removed the handshake and sessions ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)). Client support varies (**Unverified**). Use the official SDK v2, which serves both eras.
9. **Local security.** Any unauthenticated localhost port lets any local process drive Live, and with exec, run code (4.2). Use a token.
10. **Decompiled source accuracy.** The decompiler output has artefacts, such as nonsense slicing in `ControlSurface.py`. The "Live 12" repo files carry 2024 compile dates and 3.7 bytecode headers, so they may reflect 12.0 rather than 12.4 ([gluon ControlSurface.py](https://github.com/gluon/AbletonLive12_MIDIRemoteScripts/blob/main/_Framework/ControlSurface.py)).

### 8.2 Questions for the user to decide

1. **Which Live versions to support?** Live 12 only (Python 3.11, `insert_device`, `get_variant`), or Live 11 too (Python 3.7 syntax, no edition detection, no `insert_device`)?
2. **Which edition and version does the user actually own?** Verify the script loads there before planning further.
3. **Minimum Live 12 point release?** 12.3 gives `insert_device`, and 12.4 gives `replace_sample`. Requiring 12.4+ simplifies device and sample handling a lot.
4. **Session View only at first, or Arrangement too?** Arrangement needs 12.1.10+ and has thinner API coverage.
5. **Tool budget:** how many tools, given token cost? I suggest 15–25 task-shaped tools over a LOM mirror.
6. **Include offline `.mid` export from day one?** (Recommended.)
7. **Distribution:** personal use only, or published? Publishing raises the bar on installer, `doctor`, docs and licensing of any reused code.
8. **Telemetry:** none (recommended), or opt-in only?

### 8.3 Unverified items that block planning

- Third-party Remote Script loading in Live **Lite** on the user's machine (officially unstated).
- The exact embedded Python version in the user's build (log `sys.version`).
- What `load_item` can reach in Lite's browser, and whether loading Pack presets can introduce non-Lite devices that disable saving.
- How Live reacts, through the API, when Lite's 8-track limit is exceeded.
- Whether a single `begin_undo_step`/`end_undo_step` pair spanning several ticks is one undo entry.

---

## Sources

**Ableton (official)**
- Upgrade Live / edition comparison incl. Lite: https://www.ableton.com/en/upgrade-live/
- Compare editions (Intro/Standard/Suite): https://www.ableton.com/en/live/compare-editions/
- Live 12 release notes: https://www.ableton.com/en/release-notes/live-12/
- Live 12 beta release notes: https://www.ableton.com/en/release-notes/live-12-beta/
- Live 11 release notes: https://www.ableton.com/en/release-notes/live-11/
- Blog, Introducing the Extensions SDK: https://www.ableton.com/en/blog/introducing-extensions-sdk/
- Extensions SDK landing page: https://ableton.github.io/extensions-sdk/
- Live 12 Lite product page: https://www.ableton.com/en/products/live-lite/
- Live 12 manual: [MIDI and Key Remote Control](https://www.ableton.com/en/live-manual/12/midi-and-key-remote-control/), [Managing Files and Sets](https://www.ableton.com/en/live-manual/12/managing-files-and-sets/), [Max for Live](https://www.ableton.com/en/live-manual/12/max-for-live/), [Using Grooves](https://www.ableton.com/en/live-manual/12/using-grooves/)
- Help Center:
  - [Installing third-party remote scripts](https://help.ableton.com/hc/en-us/articles/209072009)
  - [Using Control Surfaces](https://help.ableton.com/hc/en-us/articles/209774285)
  - [Creating your own Control Surface script](https://help.ableton.com/hc/en-us/articles/206240184)
  - [Live's MIDI Settings](https://help.ableton.com/hc/en-us/articles/209774205)
  - [Buying Max for Live](https://help.ableton.com/hc/en-us/articles/206407124)
  - [Max for Live Devices in Live 11 Intro and Standard](https://help.ableton.com/hc/en-us/articles/360019145319)
  - [Activating Max for Live with Standard](https://help.ableton.com/hc/en-us/articles/360001296900)
  - [Controlling Live using Max for Live](https://help.ableton.com/hc/en-us/articles/5402681764242)
  - [Max for Live Developer Mode](https://help.ableton.com/hc/en-us/articles/29426709984796)
  - [Live Lite FAQ](https://help.ableton.com/hc/en-us/articles/209071589)
  - [Live 12 Lite](https://help.ableton.com/hc/en-us/articles/360021524559)
  - [Third party Set won't open in Live Lite](https://help.ableton.com/hc/en-us/articles/115000258930)
  - [Backward Compatibility](https://help.ableton.com/hc/en-us/articles/360000841004)
  - [Ableton Extensions FAQ](https://help.ableton.com/hc/en-us/articles/27303428331420)
  - [Reading Ableton Live Crash Reports (Log.txt)](https://help.ableton.com/hc/en-us/articles/5301568366354)
  - [Live 11 Technical FAQ](https://help.ableton.com/hc/en-us/articles/360019140859)
  - [Using Move to control Ableton Live](https://help.ableton.com/hc/en-us/articles/14661553738524)
- Learning: https://learningmusic.ableton.com/, https://learningsynths.ableton.com/
- Ableton Link source: https://github.com/Ableton/link

**Cycling '74 (official LOM)**
- LOM index: https://docs.cycling74.com/apiref/lom/
- Class pages: [Song](https://docs.cycling74.com/apiref/lom/song/), [Track](https://docs.cycling74.com/apiref/lom/track/), [Clip](https://docs.cycling74.com/apiref/lom/clip/), [ClipSlot](https://docs.cycling74.com/apiref/lom/clipslot/), [Application](https://docs.cycling74.com/apiref/lom/application/), [Chain](https://docs.cycling74.com/apiref/lom/chain/), [RackDevice](https://docs.cycling74.com/apiref/lom/rackdevice/), [DrumPad](https://docs.cycling74.com/apiref/lom/drumpad/), [DrumChain](https://docs.cycling74.com/apiref/lom/drumchain/), [SimplerDevice](https://docs.cycling74.com/apiref/lom/simplerdevice/), [DeviceParameter](https://docs.cycling74.com/apiref/lom/deviceparameter/), [Groove](https://docs.cycling74.com/apiref/lom/groove/), [GroovePool](https://docs.cycling74.com/apiref/lom/groovepool/)
- Live API user guide: https://docs.cycling74.com/userguide/m4l/live_api/

**Unofficial Python API evidence (not endorsed by Ableton)**
- structure-void docs: https://midiremotescripts.structure-void.com/ ([first script](https://midiremotescripts.structure-void.com/guides/first-script/), [11→12 changes](https://midiremotescripts.structure-void.com/guides/runtime-changes-11-to-12/), [Live 12 Song](https://midiremotescripts.structure-void.com/reference/live12/Live.Song.runtime/), [Browser](https://midiremotescripts.structure-void.com/reference/live12/Live.Browser.runtime/), [Application](https://midiremotescripts.structure-void.com/reference/live12/Live.Application.runtime/), [Clip](https://midiremotescripts.structure-void.com/reference/live12/Live.Clip.runtime/))
- Decompiled Ableton scripts: https://github.com/gluon/AbletonLive12_MIDIRemoteScripts, https://github.com/gluon/AbletonLive11_MIDIRemoteScripts
- CDM, Ableton Extensions beta: https://cdm.link/ableton-extensions-beta/
- Cycling '74 forum threads: https://cycling74.com/forums/live-api-in-max-for-node, https://cycling74.com/forums/producer-pal-mcp-server-running-in-node-for-max-to-control-ableton-live

**MCP (official)**
- Versioning: https://modelcontextprotocol.io/specification/versioning
- 2026-07-28 changelog: https://modelcontextprotocol.io/specification/2026-07-28/changelog
- Transports: [overview](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports), [stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio), [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- Server features: [tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources), [prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts), [schema](https://modelcontextprotocol.io/specification/2026-07-28/schema), [schema.ts](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts)
- Python SDK: https://github.com/modelcontextprotocol/python-sdk, https://pypi.org/project/mcp/, https://py.sdk.modelcontextprotocol.io/whats-new/
- TypeScript SDK: https://github.com/modelcontextprotocol/typescript-sdk, https://www.npmjs.com/package/@modelcontextprotocol/sdk

**Evaluated projects**
- MCP servers: https://github.com/ahujasid/ableton-mcp, https://github.com/uisato/ableton-mcp-extended, https://github.com/bschoepke/ableton-live-mcp, https://github.com/wstierhout/ableton-live-mcp, https://github.com/dreamrec/LivePilot, https://github.com/Simon-Kansara/ableton-live-mcp-server, https://github.com/xiaolaa2/ableton-copilot-mcp
- OSC bridges: https://github.com/ideoforms/AbletonOSC, https://github.com/ideoforms/pylive
- Extensions SDK projects: https://github.com/OthmanAdi/loophole, https://github.com/jasper-zheng/ableton-sdk-mcp, https://github.com/pnomolos/live-wire, https://github.com/philtzjp/live-connector
- Other: https://github.com/romsau/agent4live, https://github.com/valentijnh/livebridge-ableton-mcp, https://github.com/Pantani/ableton-mind, https://github.com/Ziforge/ableton-liveapi-tools, https://github.com/nozomi-koborinai/ableton-osc-mcp, https://github.com/CanArslanDev/ableton-live-mcp, https://github.com/jpoindexter/ableton-mcp, https://github.com/closestfriend/ableton-proj-mcp
- Library references: https://github.com/leolabs/ableton-js, https://pypi.org/project/music21/, https://pypi.org/project/fastmcp/
