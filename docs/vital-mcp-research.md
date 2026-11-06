# Research: an MCP server that lets an AI agent build patches in Vital

Research date: 2026-11-06. Scope: Vital by Matt Tytel, version 1.5.5 on macOS, as installed on the user's machine (standalone `/Applications/Vital.app`, plus CLAP, AU, VST and VST3 plug-ins). The goal is narrow: the user says "make a warm detuned supersaw pad" to Claude Code, and Vital ends up with that patch loaded. Every factual claim links to its source. Claims I could not confirm are marked **Unverified**.

A note on source quality. Vital publishes its source code on GitHub under GPLv3, but the published code is old. The README says "This repository is updated on a delay after binary releases" ([README](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/README.md)). The last commit is from 2022-04-20 ([commit 636ca0e](https://github.com/mtytel/vital/commit/636ca0ef517a4db087a6a08a6a8a5e704e21f836)), the project file gives version `1.0.6` and still names the product "Vial" ([standalone/vital.jucer](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/standalone/vital.jucer), [plugin/vital.jucer](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/plugin/vital.jucer)). The installed binary is 1.5.5 (`CFBundleShortVersionString` in `/Applications/Vital.app/Contents/Info.plist`, checked locally). Everything this document says about code behaviour comes from the 1.0.6 source. Whether 1.5.5 still behaves that way is **Unverified** unless a local check says otherwise. Vital publishes no changelog for 1.5. The 1.5 announcement post just says "You can get it now at https://account.vital.audio" ([forum: Vital 1.5 public release](https://forum.vital.audio/t/vital-1-5-public-release/10674)).

Source links below are pinned to commit `636ca0e` and abbreviated as **src:** `path#lines`. Each one links to `https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/<path>`.

---

## Summary

- **Yes, it can be done. The simple way is to write `.vital` preset files.** A `.vital` preset is plain JSON. It holds every knob, the modulation routing, the three wavetables, the eight LFO shapes and the sample. Vital loads a preset by parsing that JSON. Any knob the file leaves out is set to its default ([src: load_save.cpp#L153-L168](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L153-L168)). This makes generating a patch from scratch practical, not just mutating a template.
- **Vital has no remote control interface.** It has no OSC, no scripting API, no socket and no IPC in the published source. The only ways in from outside are: (1) preset files, (2) host automation parameters when Vital runs as a plug-in, (3) MIDI notes, the mod wheel and MIDI-learned CCs, and (4) a small command line (open a file, `--render`, `--tabletowav`). Details are in section 1.
- **Host automation exposes about 770 parameters, but not the whole patch.** The plug-in registers one automatable parameter for every engine control ([src: synth_plugin.cpp#L22-L38](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/synth_plugin.cpp#L22-L38)). Wavetable contents, LFO shapes, the sample and *which* modulation source goes to *which* destination are not parameters. They only exist in the preset JSON or the plug-in state blob ([src: load_save.cpp#L94-L151](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L94-L151)). So parameter-only control can tweak a patch but cannot build one.
- **Driving Vital through this repo's Ableton tools is weak.** Live opens plug-ins that have more than 64 parameters with an empty panel. The user must add parameters by hand in Configure Mode, separately for each instance ([Live 12 manual: Using Plug-Ins](https://www.ableton.com/en/live-manual/12/using-plug-ins/#plug-in-configure-mode)). The Live Object Model only exposes automatable parameters ([LOM: Device](https://docs.cycling74.com/apiref/lom/device/)). Community reports say that for VST3/AU plug-ins this means only the configured ones (**Unverified**, [Ableton forum, May 2026](https://forum.ableton.com/viewtopic.php?p=1837238)). The LOM has no call to set a plug-in's state blob.
- **Recommendation.** Build a small `vital` module in this repo that writes presets into `~/Music/Vital/User/Presets/`. Vital's own browser scans that folder ([src: load_save.cpp#L1800-L1812](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L1800-L1812)). If the standalone app is not running, the module starts the Vital binary with the preset path as its argument, which loads it (local check, 5.4). `open -a Vital <file>` does not work on 1.5.5 (5.4). Inside Ableton, the module loads the Vital plug-in with the existing `load_from_library` tool. The user then picks the preset in Vital's browser or with **Open External Preset** (one click). Do not fork Vital, and do not add a plug-in host or a MIDI layer for version 1.
- **Licensing is not a blocker for that design.** A program that only writes data files in Vital's format links no Vital code. The GPL FAQ says the copyright status of a program's output follows its input, not the program ([GPL FAQ #GPLOutput](https://www.gnu.org/licenses/gpl-faq.html#GPLOutput)). Two things to avoid: do not copy Vital source into this MIT repo, and do not ship Vital's factory presets as templates. Their licence forbids redistribution ([README](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/README.md)).
- **Checked by hand on 2026-11-06 (5.4):** a preset built from scratch loads in 1.5.5 with every value correct; a new file in `User/Presets` appears in an open Vital's browser without a restart; `open -a Vital` is ignored; the binary started with a path loads it; `--render` crashes.

---

## 1. Does Vital expose a control surface?

### 1.1 OSC, scripting, sockets, IPC: no

The published source has no OSC, socket or scripting component. The standalone entry point handles only command-line flags and file arguments ([src: standalone/main.cpp#L255-L534](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/standalone/main.cpp#L255-L534)). The plug-in talks to the outside only through the JUCE `AudioProcessor` interface: parameters, MIDI and state ([src: synth_plugin.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/synth_plugin.cpp)). I searched the source tree and found no OSC or network control code. A full-tree check against 1.5.5 is impossible because 1.5's source is not published (**Unverified** for 1.5.5). The author also says "I will not take any pull requests" ([README](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/README.md)), so upstream will not add an interface.

### 1.2 Command line (standalone binary)

The standalone app understands these flags ([src: standalone/main.cpp#L262-L283](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/standalone/main.cpp#L262-L283)):

| Flag | Effect |
|---|---|
| `-v`, `--version` | Prints the version and quits. Local check: `/Applications/Vital.app/Contents/MacOS/Vital --version` printed `Vital 1.5.5`. |
| `--headless` | Runs without a window. |
| `--render <preset.vital> [-m note] [-l seconds] [-b bpm] [--images]` | Loads the preset, plays one note (default 48, 6 s, 120 bpm) and writes `<preset name>.wav` into the working directory ([src: standalone/main.cpp#L456-L497](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/standalone/main.cpp#L456-L497)). There is no `-o` flag in this path. |
| `--tabletowav`, `--tableimages` | Converts a `.vitaltable` wavetable to WAV or images. |
| `<file>` | Any other existing path is opened with `MainWindow::loadFile`, which runs `loadFromFile` ([src: standalone/main.cpp#L500-L534](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/standalone/main.cpp#L500-L534), [L228-L243](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/standalone/main.cpp#L228-L243)). |

The 1.5.5 binary still contains these help strings (local `strings` check: `--render`, `--headless`, `--tabletowav`, `--tableimages`, `-m, --midi`).

**Local test of `--render` failed.** It ran on a copy of a factory preset inside Claude Code's macOS sandbox. Without `--headless`, it exited cleanly but wrote no WAV. With `--headless`, it crashed with exit code 139. The sandbox also printed `sandbox_extension_issue_file_to_process failed`, so the failure may be the sandbox's fault. Outside the sandbox it also crashes with `segmentation fault`, with and without `--headless` (5.4). `--render` is broken on 1.5.5.

**Opening a file in a running standalone.** On macOS, JUCE never allows a second instance of an app. A second launch calls `anotherInstanceStarted()` in the running one instead ([JUCE: JUCEApplicationBase](https://docs.juce.com/master/classJUCEApplicationBase.html)). Vital implements that callback by loading the file named on the command line ([src: standalone/main.cpp#L531-L533](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/standalone/main.cpp#L526-L528)). The 1.5.5 app also registers `.vital` as a document type it edits (`CFBundleDocumentTypes` → `vital`, role `Editor`, checked locally in Info.plist). So `open -a Vital /path/x.vital` should load the preset into the running app. **It does not on 1.5.5**: the user's check found it ignored both with Vital running and at launch. Passing the path straight to the binary works when Vital is closed, and starts a second instance when it is open (5.4).

### 1.3 MIDI

From `MidiManager::processMidiMessage` ([src: midi_manager.cpp#L228-L311](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/midi_manager.cpp#L228-L311)):

- **Handled:** note on and off, polyphonic aftertouch, channel pressure, pitch bend, mod wheel (CC1), sustain, sostenuto, all notes off and all sounds off. MPE slide and pressure are also handled.
- **Program change is ignored** (`case kProgramChange: return;`). MIDI cannot switch presets.
- **MIDI learn.** Every other CC goes to `midiInput()`. If a control is armed, the CC number is mapped to it. The map is then written to Vital's config file ([src: midi_manager.cpp#L77-L98](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/midi_manager.cpp#L77-L98)). A CC value 0–127 is scaled linearly onto the control's min..max, and indexed controls are rounded. The resolution is 7-bit and the map is keyed by CC number only, with no channel.
- **The MIDI learn map is global.** `saveMidiMapConfig` stores `"midi_learn": [{source: cc, destinations: [{destination, min_range, max_range}]}]` in the config JSON ([src: load_save.cpp#L1374-L1400](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L1374-L1400)). It is read back at startup ([src: load_save.cpp#L1402-L1431](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L1402-L1431)). On this Mac the config is `~/Library/Application Support/vital/Vital.config` (checked locally). The file currently has no `midi_learn` key. In principle a tool could pre-write a CC map there. That means editing a file Vital owns while Vital may be running, which is fragile.

### 1.4 Host automation parameters (plug-in builds)

- **What is exposed.** The constructor walks `vital::Parameters` and adds one `ValueBridge` (a JUCE `AudioProcessorParameter`) for each name that is also an engine control ([src: synth_plugin.cpp#L22-L38](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/synth_plugin.cpp#L22-L38)). `isAutomatable()` always returns true ([src: value_bridge.h#L87-L89](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/value_bridge.h#L87-L89)).
- **How many.** The list is built from global controls plus per-module groups. There are 3 oscillators, 2 filters plus the FX filter, 6 envelopes, 8 LFOs, 4 random LFOs and 64 modulation slots with 5 controls each ([src: synth_parameters.cpp#L535-L600](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L535-L600), [src: synth_constants.h#L25-L37](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_constants.h#L25-L37)). Local evidence: the 1.0.0-era presets in the user's library hold **771 numeric control keys**, and the preset writer emits one key per control ([src: load_save.cpp#L94-L99](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L94-L99)). So the plug-in exposes about 770 parameters. The exact count for 1.5.5 is **Unverified**.
- **Names and order.** `getName()` returns the control's display name. Parameters are sorted by the version that added them, then by name ([src: synth_parameters.cpp#L35-L40](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L35-L40)). Parameters added later therefore append, and saved automation keeps working.
- **Value mapping.** The host's 0..1 value is linear over the engine range: `plugin = (engine − min) / (max − min)`. Indexed controls round ([src: value_bridge.h#L107-L119](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/value_bridge.h#L107-L119)). The skew to display units (Hz, seconds, %) is applied only in `getText()`. Controls with an indexed scale and fewer than 300 steps report as discrete.
- **Not exposed.** The plug-in has one program: `getNumPrograms()` returns 1 and `setCurrentProgram` does nothing ([src: synth_plugin.h#L60-L62](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/synth_plugin.h#L60-L62)). The whole patch is the state blob. `getStateInformation` writes the same JSON as a preset, plus `tuning`, as a string. `setStateInformation` parses it back ([src: synth_plugin.cpp#L189-L218](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/synth_plugin.cpp#L189-L218)).

---

## 2. The `.vital` preset format

### 2.1 Structure (confirmed on local files)

All 75 `.vital` files under `~/Music/Vital` parse as JSON (`file` reports "JSON data"). Most are about 1–1.6 MB, because wavetables and samples are embedded as base64. Top level, as written by `LoadSave::stateToJson` ([src: load_save.cpp#L94-L151](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L94-L151)):

```text
{
  "synth_version": "1.0.0",          // string, gates loading (2.4)
  "preset_name", "author", "comments", "preset_style",   // strings, optional on load
  "macro1".."macro4",                // macro display names
  "settings": {
    "<control name>": <number>,      // ~771 keys: osc_1_unison_voices, filter_1_cutoff, env_1_attack, ...
    "modulations": [ {"source": "lfo_1", "destination": "filter_1_cutoff", "line_mapping"?: {...}}, ... ],  // 64 slots
    "wavetables":  [ <osc 1>, <osc 2>, <osc 3> ],
    "lfos":        [ <8 line shapes> ],
    "sample":      { "name", "length", "sample_rate", "samples" (base64 int16), "samples_stereo"? }
  }
}
```

Local observations: the files carry `synth_version` values from `0.6.1` to `1.0.0`, with 50 of 75 at `1.0.0`. None is from 1.5.x. Factory files do not store `preset_name`; on load, the name comes from the file name ([src: synth_base.cpp#L354-L381](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_base.cpp#L354-L381)).

### 2.2 Control names and ranges

Every control is a `ValueDetails` entry: name, min, max, default, value scale, units and display name ([src: synth_parameters.h](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.h)). Group entries get prefixes such as `osc_1_`, `filter_2_`, `env_3_`, `lfo_4_` and `modulation_12_`. The values stored in a preset are **engine values**, not 0..1. Key controls for a pad:

| Key (per osc / filter / env) | Range, default | Scale and meaning | Source |
|---|---|---|---|
| `osc_N_on` | 0–1, osc 1 defaults on | switch | [L462](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L462), [L586](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L586) |
| `osc_N_transpose` | −48–48, 0 | semitones, indexed | [L464](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L464) |
| `osc_N_unison_voices` | 1–16, 1 | voices, indexed | [L476](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L476) |
| `osc_N_unison_detune` | 0–10, 4.472 | quadratic: display % = value² (default shows 20%) | [L474](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L474) |
| `osc_N_unison_blend` | 0–1, 0.8 | % | [L478](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L478) |
| `osc_N_stack_style` | indexed | unison stack type | [L472](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L472) |
| `osc_N_level` | 0–1, 0.707 | quadratic | [L484](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L484) |
| `osc_N_wave_frame` | 0–256, 0 | wavetable position (257 frames) | [L492](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L492), [synth_constants.h#L27](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_constants.h#L27) |
| `filter_N_cutoff` | 8–136, 60 | semitones (MIDI note number), linear | [L421](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L421) |
| `filter_N_resonance` | 0–1, 0.5 | % | [L423](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L423) |
| `filter_N_model`, `filter_N_style` | indexed | filter type and slope | [L429-L432](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L429-L432) |
| `filter_N_on` | 0–1, 0 | switch | [L433](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L433) |
| `env_N_attack`, `env_N_decay`, `env_N_release` | 0–2.378 | quartic: seconds = value⁴ (max about 32 s) | [L354-L360](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L354-L360) |
| `env_N_sustain` | 0–1, 1 | level | [L368](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L368) |
| `polyphony` | 1–32, 8 | voices | [L115](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L115) |
| `volume` | 0–7399.44, 5473.04 | square-root scale, dB display | [L201](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L201) |
| `chorus_dry_wet`, `reverb_dry_wet` (+ `chorus_on`, `reverb_on`) | 0–1 | % | [L147](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L147), [L243](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L243) |
| `modulation_K_amount` / `_power` / `_bipolar` / `_stereo` / `_bypass` | amount −1–1 | per modulation slot K = 1..64 | [L522-L533](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp#L522-L533) |

### 2.3 Modulation, wavetable, LFO and sample encoding

- **Modulations.** `settings.modulations` is a list of up to 64 `{source, destination}` pairs, with an optional `line_mapping` curve ([src: load_save.cpp#L102-L115](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L102-L115)). List index *i* goes with the controls `modulation_{i+1}_amount` and so on. On load, a pair whose source or destination the engine does not know is **silently skipped** ([src: load_save.cpp#L170-L196](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L170-L196)). An agent's typo therefore produces a missing route, not a crash. The tool should still validate the names. Sources seen in local files: `env_2`, `lfo_1`, `velocity`, `macro_control_1`. Destinations are control names, and can even be other modulation amounts (`modulation_3_amount`).
- **Wavetables.** There is one entry per oscillator: `{name, author, version, remove_all_dc, full_normalize, groups: [{components: [...]}]}`. Component types seen locally: `Wave Source` (144), `Audio File Source` (65), `Line Source` (16), `Wave Folder`, `Frequency Filter`, `Wave Warp`, `Phase Shift` and `Wave Window`. A `Wave Source` keyframe stores `wave_data` as base64 of 2048 little-endian float32 samples ([src: wave_source.cpp#L190-L210](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/wavetable/wave_source.cpp#L190-L210)). I confirmed this locally by decoding to exactly 2048 floats. A `Line Source` keyframe is a point list (`{"line": {"points": [x0,y0,x1,y1,...], "powers": [...], "num_points": N, "smooth": false}}`).
- **Shortcut for generated wavetables.** If a wavetable entry is itself valid line-generator JSON, `WavetableCreator::jsonToState` builds the table from that line ([src: wavetable_creator.cpp#L480-L486](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/wavetable/wavetable_creator.cpp#L480-L486)). A saw, square or triangle can then be written as a few points, with no base64. The factory presets' default oscillator table is named `Init`, and its first samples ramp down, so it is a saw (decoded locally). A supersaw therefore needs only `osc_1_unison_voices` and `osc_1_unison_detune`.
- **LFOs.** There are 8 line shapes, for example `{"name":"Triangle","num_points":3,"points":[0,1,0.25,0,1,1],"powers":[...],"smooth":false}` (local file).
- **Sample.** `{name, length, sample_rate, samples}`, where `samples` is base64 int16 PCM. `Sample::jsonToState` reads `length`, `sample_rate` and `samples` without checking that they exist ([src: sample_source.cpp#L324-L333](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/synthesis/producers/sample_source.cpp#L324-L333)). A generated preset must include a sample object. A tiny silent one should do (**Unverified**).

### 2.4 How Vital loads a preset, and what that means for generated files

`SynthBase::loadFromFile` parses the JSON. On a parse error it reports "Preset file is corrupted". Otherwise it calls `LoadSave::jsonToState` ([src: synth_base.cpp#L354-L381](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_base.cpp#L354-L381), [src: load_save.cpp#L1027-L1053](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L1027-L1053)):

1. **Version gate.** If the file's `synth_version` has a newer *feature* version than the running synth, loading is refused with "Preset was created with a newer version". If it is older, `updateFromOldVersion` rewrites old keys ([src: load_save.cpp#L269-L1025](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L269-L1025)). → Write the installed version. `Vital.config` holds `"synth_version": "1.5.5"` on this machine (local check). Writing an old version would run migrations on keys that are already current.
2. **Controls.** Each known control takes the file's value, or **its default if the key is missing** ([src: load_save.cpp#L153-L168](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L153-L168)). Unknown keys are ignored, because the loop walks the engine's controls, not the file's keys. → A generator only has to write the knobs it changes.
3. **Modulations** are cleared and then rebuilt from the list (2.3).
4. **Wavetables and LFOs** are loaded by iterating the arrays ([src: load_save.cpp#L204-L225](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L204-L225)). If the arrays are missing, the loop runs zero times and the *previous* patch's wavetables stay loaded. I infer this from nlohmann JSON iteration semantics; it is **Unverified** at runtime. → Always write all 3 wavetables and all 8 LFOs, so the result does not depend on the previous patch.

**So generation from scratch is feasible.** The minimal file is: `synth_version`, `settings` with the changed controls, `modulations`, 3 wavetables (line-generator JSON is enough), 8 LFOs and one sample object. Mutating a template also works and is safer for odd keys. The template must be the user's own saved Init preset, not a factory preset (section 4). I found no published schema document and no official parser other than Vital's own source. The format is defined by `load_save.cpp`.

### 2.5 Where presets live and how Vital finds them

- **Data directory.** It is set by `data_directory` in `Vital.config`. On macOS the fallback is `~/Music/Vial` in the 1.0.6 source ([src: load_save.cpp#L1734-L1759](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L1734-L1759)). On this machine the config says `/Users/sundayfunday/Music/Vital` (local check).
- **Browser folders.** The preset browser scans `<data dir>/Presets` and `<data dir>/<any subfolder>/Presets` ([src: load_save.cpp#L1761-L1782](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L1761-L1782)). That covers the user's `Factory/Presets` and `Glorkglunk/Presets`. Vital's own saves go to `<data dir>/User/Presets`, and `getUserPresetDirectory()` creates that folder when missing ([src: load_save.cpp#L1800-L1812](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L1800-L1812)). On this machine, `~/Music/Vital/User` does not exist yet. A file written to `~/Music/Vital/User/Presets/x.vital` is therefore in a scanned folder.
- **When the list refreshes.** `PresetList::reloadPresets()` runs when the browser is built and after Vital itself saves, deletes, imports a bank or changes the data folder. Showing the browser only refreshes its cache ([src: preset_browser.cpp#L357-L364](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/interface/editor_sections/preset_browser.cpp#L357-L364), [L800-L808](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/interface/editor_sections/preset_browser.cpp#L800-L808), [L851-L890](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/interface/editor_sections/preset_browser.cpp#L851-L890), [full_interface.cpp#L299-L301](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/interface/editor_sections/full_interface.cpp#L299-L301)). An externally written file may not appear until the Vital window is rebuilt: reopen the plug-in window, or restart the standalone. 1.5 is said to have reworked preset browsing (user comment, [forum](https://forum.vital.audio/t/vital-1-5-public-release/10674)), so this is **Unverified** for 1.5.5.
- **Loading routes that need no rescan.** You can use the standalone's File → Open ([src: standalone/main.cpp#L196-L210](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/standalone/main.cpp#L196-L210)), the preset menu's **Open External Preset** in both plug-in and standalone ([src: synth_preset_selector.cpp#L196-L203](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/interface/editor_components/synth_preset_selector.cpp#L196-L203), [L285-L296](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/interface/editor_components/synth_preset_selector.cpp#L285-L296)), or `open -a Vital file.vital` (1.2). I found no handler for dropping a `.vital` file onto the editor in the source. **Unverified** for 1.5.5.

---

## 3. Candidate architectures

| | Approach | Reaches full patch? | Works in Ableton? | Effort | Verdict |
|---|---|---|---|---|---|
| a | MCP writes `.vital` files and opens them | Yes: everything in 2.1 | Yes, with one user click in Vital's browser or menu | Small: 1 Python module, stdlib only | **Build this** |
| b | Vital in Ableton through existing `set_device_parameters` | No: knobs only, and only those configured by hand | Yes | Small code, big UX friction | Optional add-on for live tweaks |
| c | MIDI CC through a virtual MIDI port | No: knobs only, 7-bit, each CC must be learned first | Yes | Medium, plus 1 dependency | Skip |
| d | Fork Vital and add an OSC or socket server | Yes, live | Only as a renamed plug-in build | Large: C++/JUCE build, 2021-era code base | Skip |
| e | Custom host (pedalboard / DawDreamer) loads Vital VST3 | Yes, through the state blob | No: it is a separate process | Medium, plus a GPL dependency | Later, for offline previews only |

### (a) Write `.vital` files (recommended)

- **How.** Build the JSON in Python (2.4), validate the names and ranges against a small table, write `~/Music/Vital/User/Presets/<name>.vital`, and run `open -a Vital <file>` for the standalone. The `open` route relies on 1.2 and is **Unverified**.
- **Pros.** It covers the whole patch: wavetables, routing, LFOs, effects. It needs no runtime connection to Vital and works when nothing is running. Presets are durable, diffable files the user can keep. The same file works in the standalone and in every plug-in format. It fits the repo's existing "offline `.mid` writer" pattern (see [ableton-mcp-research.md](./ableton-mcp-research.md)).
- **Cons.** It is not live: each tweak is a new file plus a reload. The agent cannot hear the result. Inside a host, the user must click to load the file, because hosts have no "load this state blob" call that this repo can reach. The format is undocumented and can change between versions; pin `synth_version` and test against the installed version.

### (b) Vital hosted in Ableton through ableton-mcp's device tools

- **What exists.** `load_from_library` with root `plugins` can place a plug-in on a track ([src/ableton_mcp/server.py:379](../src/ableton_mcp/server.py)). `read_device` and `set_device_parameters` read and write a device's LOM parameters ([server.py:444-462](../src/ableton_mcp/server.py)). The earlier research noted one report of VST3 loading by URI failing in another project ([ableton-mcp-research.md](./ableton-mcp-research.md), [ahujasid#79](https://github.com/ahujasid/ableton-mcp/issues/79)).
- **Blockers.** Vital has about 770 parameters. Live opens plug-ins with more than 64 parameters with an empty panel. Parameters must be added in Configure Mode, and the selection is per instance and saved with the Set ([Live 12 manual](https://www.ableton.com/en/live-manual/12/using-plug-ins/#plug-in-configure-mode)). An `Options.txt` switch, `PluginAutoPopulateThreshold`, can raise auto-population to at most 128 parameters. Forum users say it is unreliable for VST3, and with more than 128 parameters nothing is populated (**Unverified**, [Ableton forum](https://forum.ableton.com/viewtopic.php?p=1837238)). The LOM lists only automatable parameters ([LOM: Device](https://docs.cycling74.com/apiref/lom/device/)). `PluginDevice` adds only `presets`, `selected_preset_index` and `is_editor_open` ([LOM: PluginDevice](https://docs.cycling74.com/apiref/lom/plugindevice/)). Vital reports a single program, so preset switching through `selected_preset_index` does nothing ([src: synth_plugin.h#L60-L62](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/synth_plugin.h#L60-L62)). There is no LOM function to set plug-in state.
- **Where it still helps.** Suppose the user configures Vital's 4 macros once, and saves that configuration as a Live preset (Ableton KB article "[Saving plug-in parameter configurations](https://help.ableton.com/hc/en-us/articles/209073089-Saving-plug-in-parameter-configurations)"; the page returned 403, so the content is **Unverified**). Generated presets can then map their key moves (brightness, detune, space) to `macro_control_1..4`. The existing `set_device_parameters` could then move those macros live. Values are 0..1 and linear over the engine range (1.4).

### (c) MIDI CC through a virtual port

- **How.** Create a CoreMIDI virtual port, or use the IAC Driver ([Apple: Audio MIDI Setup, transfer MIDI between apps](https://support.apple.com/guide/audio-midi-setup/transfer-midi-information-between-apps-ams1013/mac)). `mido` with the rtmidi backend can create virtual ports everywhere except Windows ([mido: RtMidi backend](https://mido.readthedocs.io/en/stable/backends/rtmidi.html)). Current versions are `mido` 1.3.3 and `python-rtmidi` 1.5.8, both MIT-licensed ([PyPI mido](https://pypi.org/project/mido/), [PyPI python-rtmidi](https://pypi.org/project/python-rtmidi/)).
- **Cons.** CCs reach only MIDI-learned controls, and learning happens in the UI or by pre-writing `midi_learn` into Vital's config (1.3). The resolution is 7-bit. MIDI cannot change wavetables or routing, and program change is ignored. It also adds a native dependency to a repo whose only dependency is `mcp` (`pyproject.toml`). Inside Ableton, the repo can already write notes and set parameters, so MIDI adds nothing there.

### (d) Fork Vital (GPLv3) and add an OSC or socket server

- **Licence.** The fork must stay GPLv3. It must not use the names "Vital", "Vital Audio", "Tytel" or "Matt Tytel" for distribution, and must not call vital.audio web services ([README](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/README.md)). An MCP server that talks to the fork over a socket is a separate program. The FSF says the line depends on "the mechanism of communication (exec, pipes, rpc, function calls within a shared address space, etc.) and the semantics of the communication" ([GPL FAQ #MereAggregation](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)). Socket JSON is the usual arm's-length case. That is a legal reading, not legal advice.
- **Version gotcha.** The public source is 1.0.6, last updated 2022, and has no CLAP target ([plugin/vital.jucer](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/plugin/vital.jucer)). The user runs 1.5.5. A fork means downgrading to 1.0.6 features and keeping a second synth installed. Presets saved by 1.5.5 may be refused by the 1.0.6-based fork because of the feature-version gate (2.4). Building JUCE C++ for macOS (signing, AU validation) is a project in itself.
- **Verdict.** Live control of the full engine is the only gain, and it costs the most. Skip.

### (e) Custom host: pedalboard or DawDreamer loads the Vital VST3

- **What it gives.** pedalboard can load VST3 and AU on macOS. Its `raw_state` gets or sets "the plugin's internal state", and it can render an instrument from a list of MIDI messages ([pedalboard reference](https://spotify.github.io/pedalboard/reference/pedalboard.html)). DawDreamer offers `set_parameter`, `add_midi_note` and `render` ([DawDreamer README](https://github.com/DBraun/DawDreamer)). Vital's own state blob is the preset JSON (1.4). Whether JUCE's VST3 host-side `raw_state` is that raw JSON, or a wrapper around it, is **Unverified**.
- **Cons.** It runs Vital in a separate, headless process, so the user still does not see the patch in their Vital. Its main value is an offline audio preview, for example analysing a render's spectrum or loudness to sanity-check "warm". Vital's own `--render` may give the same preview without a new dependency (1.2, **Unverified**). Both libraries are GPLv3 (pedalboard 0.9.26, dawdreamer 0.9.0, per [PyPI pedalboard](https://pypi.org/project/pedalboard/) and [PyPI dawdreamer](https://pypi.org/project/dawdreamer/)). Distributing this MIT repo with a hard GPL dependency puts GPL terms on the combined distribution. Keep it optional or out.

---

## 4. Licensing and version gotchas (checklist)

- **The Vital source is GPLv3** ([GitHub repo licence](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/LICENSE)). This repo is MIT (`LICENSE`). Writing JSON in Vital's format does not copy Vital code. Do not paste source excerpts or generated tables of Vital's code into the repo. Hand-write the small range table with links to the lines above. Facts such as names and ranges are data, but keep the table minimal.
- **Factory presets may not be redistributed** ([README](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/README.md)). Never ship one as a template. Third-party packs in `~/Music/Vital` (Glorkglunk, Billain and others) have their own licences. Read them at runtime if useful, never commit them.
- **The binary and the source differ.** The binary is 1.5.5 and the source is 1.0.6. The 1.5.5 binary still has the `--render` and `--headless` strings (local check). CLAP exists only in 1.5.x (`Vital.clap` is installed locally) and is not in the published source.
- **The version gate is one-way.** A file stamped `1.5.5` will not load in an older Vital (2.4). Read `synth_version` from the user's `Vital.config` and stamp files with it.
- **Do not write `Vital.config` or `Vital.library`.** Vital owns them, and they also hold account details. Read only `data_directory` and `synth_version`.
- **The `~/Music/Vital/User` folder is not present yet.** Create `User/Presets` with `mkdir -p`. That is what Vital itself does ([src: load_save.cpp#L1800-L1812](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp#L1800-L1812)).

---

## 5. Recommendation

### 5.1 The simplest approach that works

Implemented as `src/ableton_mcp/vital.py` plus the `vital_create_preset` and `vital_read_preset` tools. Differences from this section: only saw, square, triangle and sine waves, 94 accepted controls (the `CONTROLS` table in `vital.py`, including comb/formant filter controls and the sample source), a `sample` argument (`"silence"` or seeded `"white_noise"`, generated in code, no factory data), and `open=false` for Vital inside Live. Comb/formant meanings come from the 1.0.6 source and are checked by hand on 1.5.5 (README smoke step 30); the formant "The Mouth" style is left undescribed because the open-source VocalTract is a pass-through. User-facing limits are in the README's Limits section.

Use architecture (a), and reuse (b) only for what this repo already does:

1. `vital_create_preset(name, controls, modulations=[], wavetables=["saw"]*3, open=True)`:
   - Starts from an in-code init patch with no copied factory data. That means 3 line-generator wavetables (saw, saw, sine), 8 triangle LFOs, a silent 1-sample `sample` and 64 empty modulation slots.
   - Applies `controls`, a map from control name to engine value. The tool validates every name against a hand-written table of the 30–50 controls an agent needs: oscillators, unison, filters, envelopes 1–3, LFO 1–2 rate, chorus, delay, reverb, macros, volume and polyphony. It rejects unknown names instead of letting Vital ignore them.
   - Writes `~/Music/Vital/User/Presets/<name>.vital`, refusing to overwrite unless asked, and stamps `synth_version` from `Vital.config`.
   - When `open=True` and no `Vital` process runs (`pgrep -x Vital`), starts `/Applications/Vital.app/Contents/MacOS/Vital <file>`. If Vital already runs, it only writes the file and tells the user to pick it in the browser: starting the binary again opens a second instance (5.4).
2. `vital_read_preset(path)`: returns the non-default controls, the active routes and the wavetable names, so the agent can start from any existing preset ("make this one darker").
3. Inside Ableton, chain the existing tools. `load_from_library(track, "plugins", [... "Vital"])` puts Vital on a MIDI track, then tell the user to click **Browse Presets** (or **Open External Preset**) and pick the new file. `write_notes` / `create_chord_progression` then give the pad something to play.

Example for "warm detuned supersaw pad". Values are engine values from 2.2; the intent is shown in brackets.

```json
{"osc_1_unison_voices": 7, "osc_1_unison_detune": 4.0,   // 16% detune
 "osc_1_unison_blend": 0.7, "osc_2_on": 1, "osc_2_transpose": -12, "osc_2_level": 0.5,
 "filter_1_on": 1, "filter_1_cutoff": 76, "filter_1_resonance": 0.2,   // about E5, mellow
 "env_1_attack": 0.95, "env_1_release": 1.3,                            // about 0.8 s / 2.9 s
 "chorus_on": 1, "chorus_dry_wet": 0.35, "reverb_on": 1, "reverb_dry_wet": 0.35}
```

with `modulations: [{"source": "lfo_1", "destination": "filter_1_cutoff"}]` and `modulation_1_amount: 0.1` for slow movement.

### 5.2 Same repo or separate server?

**Put it in this repo as one module (`src/ableton_mcp/vital.py`) plus 2 tools in `server.py`.**

- The preset writer is stdlib-only (`json`, `base64`, `struct`, `pathlib`, `subprocess` for `open`), so the dependency list stays at `mcp` alone.
- The main user story, a pad playing in Live, needs both halves: Vital for the sound, ableton-mcp for the track, the plug-in load and the notes. One server lets one agent turn do all of it.
- It mirrors the existing offline `write_midi_file` fallback: useful even when Live is closed.
- Split it out only if Vital work grows a live-control path (fork or host), which brings C++ or GPL dependencies this repo should not carry.

### 5.3 How to validate (do these before writing the tools)

1. Hand-write the minimal preset from 2.4 with only `osc_1_unison_voices: 7` changed. Load it with Vital 1.5.5's **Open External Preset**. Expected: 7 unison voices, saw table, everything else at init. If it fails with "corrupted", add the missing top-level pieces one at a time.
2. Write a second file into `~/Music/Vital/User/Presets/` while Vital is open. Check whether the browser lists it, and whether reopening the window is needed (2.5).
3. Run `open -a Vital ~/Music/Vital/User/Presets/test.vital` from a normal terminal with the standalone running. Check that it loads into the running window (1.2).
4. Run `cd "$TMPDIR" && /Applications/Vital.app/Contents/MacOS/Vital --headless --render test.vital -m 48 -l 3` from a normal terminal. If a WAV appears, an offline preview needs no extra dependency (1.2).
5. In Live, check that `load_from_library` can reach Vital under the `plugins` root, and run `read_device` to see which parameters are exposed before and after configuring macros (3b).


### 5.4 Results of the manual checks (2026-11-06, Vital 1.5.5, outside the sandbox)

The test presets were built by a script with no factory data: `synth_version: "1.5.5"`, changed controls only, empty `modulations`, three 2-point line-generator saw wavetables, eight triangle LFOs and a 64-sample silent `sample`.

| Check | Result |
|---|---|
| 1. Minimal preset (`osc_1_unison_voices: 7` only) via **Open External Preset** | **Pass.** Name, saw table, 7v unison, silent sample and triangle LFO all loaded; every other control showed its default. |
| 1b. Pad preset (unison, detune, filter 1 on, cutoff, attack, release, reverb) | **Pass.** All values loaded as written. |
| 2. File copied into `~/Music/Vital/User/Presets/` while Vital is open | **Pass.** The browser listed it with no restart. 1.5.5 refreshes where the 1.0.6 source suggested it might not (2.5). |
| 3. `open -a Vital <file>` with Vital running | **Fail.** Nothing happens. |
| 3b. `open -a Vital <file>` with Vital closed | **Fail.** Vital starts with the init preset. 1.5.5 ignores macOS open-document events. |
| 3c. `/Applications/Vital.app/Contents/MacOS/Vital <file>` with Vital closed | **Pass.** Vital starts with the preset loaded. |
| 3d. Same command with Vital already running | Starts a **second instance**. The tool must check `pgrep -x Vital` first. |
| 4. `--headless --render <file> -m 48 -l 3` | **Fail.** `segmentation fault`, no WAV. |
| 4b. `--render <absolute file> -m 48 -l 3` (no `--headless`) | **Fail.** `segmentation fault`, no WAV. Offline render is out for 1.5.5. |

Findings for the line editor: in line-generator points, `y=0` is the top of the editor and `y=1` the bottom (the triangle `[0,1, 0.5,0, 1,1]` shows its peak in the middle).

Check 5 (Vital inside Live through `load_from_library` and `read_device`) is not done yet.

---

## Unverified items, in one place

- 1.5.5 behaviour the 1.0.6 source predicts and 5.4 did not test: the version gate, the absence of OSC/IPC, and the parameter count. (Defaults for missing keys, browser refresh and the silent sample are now confirmed, 5.4.)
- Whether missing `wavetables`/`lfos` arrays leave the previous patch's data in place.
- Live's handling of plug-ins with more than 128 parameters, `PluginAutoPopulateThreshold` with VST3, and whether LOM `parameters` lists only configured plug-in parameters (forum sources only).
- The format of pedalboard's `raw_state` for a JUCE VST3.

## Sources

- Vital source, commit 636ca0e: [README](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/README.md), [LICENSE](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/LICENSE), [load_save.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/load_save.cpp), [synth_base.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_base.cpp), [synth_parameters.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_parameters.cpp), [synth_constants.h](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/synth_constants.h), [midi_manager.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/midi_manager.cpp), [synth_plugin.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/synth_plugin.cpp), [value_bridge.h](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/plugin/value_bridge.h), [standalone/main.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/standalone/main.cpp), [preset_browser.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/interface/editor_sections/preset_browser.cpp), [synth_preset_selector.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/interface/editor_components/synth_preset_selector.cpp), [wavetable_creator.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/common/wavetable/wavetable_creator.cpp), [sample_source.cpp](https://github.com/mtytel/vital/blob/636ca0ef517a4db087a6a08a6a8a5e704e21f836/src/synthesis/producers/sample_source.cpp)
- Vital official: [vital.audio](https://vital.audio/) (system requirements only, no changelog), [forum: 1.5 release](https://forum.vital.audio/t/vital-1-5-public-release/10674)
- JUCE: [JUCEApplicationBase](https://docs.juce.com/master/classJUCEApplicationBase.html)
- Ableton: [Live 12 manual: Using Plug-Ins](https://www.ableton.com/en/live-manual/12/using-plug-ins/), [KB: Saving plug-in parameter configurations](https://help.ableton.com/hc/en-us/articles/209073089-Saving-plug-in-parameter-configurations) (403, content not read), [forum thread on plug-in parameters, May 2026](https://forum.ableton.com/viewtopic.php?p=1837238)
- Live Object Model: [Device](https://docs.cycling74.com/apiref/lom/device/), [PluginDevice](https://docs.cycling74.com/apiref/lom/plugindevice/)
- GNU: [GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html)
- Apple: [Audio MIDI Setup: transfer MIDI between apps](https://support.apple.com/guide/audio-midi-setup/transfer-midi-information-between-apps-ams1013/mac)
- Libraries: [mido RtMidi backend](https://mido.readthedocs.io/en/stable/backends/rtmidi.html), [pedalboard reference](https://spotify.github.io/pedalboard/reference/pedalboard.html), [DawDreamer](https://github.com/DBraun/DawDreamer), PyPI pages for [mido](https://pypi.org/project/mido/), [python-rtmidi](https://pypi.org/project/python-rtmidi/), [pedalboard](https://pypi.org/project/pedalboard/), [dawdreamer](https://pypi.org/project/dawdreamer/)
- MCP: the current spec and Python SDK versions are covered in [ableton-mcp-research.md](./ableton-mcp-research.md). The design above adds tools only and needs nothing new from the spec.
