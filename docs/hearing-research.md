# Research: giving the agent "hearing"

Research date: 2026-11-09. Scope: Ableton Live 12.1+ on macOS, with Live Lite as the primary target. This repo has a stdlib-only Remote Script on Live's embedded Python 3.11 and a stdio MCP server whose only dependency is `mcp`, with CI on Python 3.11 and 3.13. Every factual claim links to its source. Claims I could not confirm are marked **Unverified**. Statements marked *assessment* are my judgement, not sourced facts.

A note on source quality. The Live Object Model (LOM) facts come from Cycling '74's official reference. Edition facts come from Ableton's comparison table, whose checkmarks are SVG icons. I read their `aria-label` ("Included" / "Not included") from the page HTML, in the column order Lite, Intro, Standard, Suite, the same method as [ableton-mcp-research.md](ableton-mcp-research.md) section 1.1. Ableton's Help Center (KB) returned HTTP 403 to every fetch this time, so no KB article is cited as newly checked. I got library facts from each project's repo (LICENSE, pyproject, README) and from PyPI metadata. I checked dependency resolution locally with `uv pip compile` for macOS arm64 on Python 3.11 and 3.13 (section 2.4). These are resolve checks only. Nothing was installed or run.

---

## Summary

- **The agent already reads the Set as symbols.** Notes, devices and parameters come from `read_notes`, `read_device` and `get_set_overview`. Hearing only adds value where that view loses information: timbre, mix balance, the effect of effects, and what a parameter change actually did. The model gets no raw audio. So "hearing" means DSP features rendered as text and numbers. A spectrogram image for the model's vision is possible, but I treat it as unreliable (*assessment*).
- **Two separate subsystems.** (A) *Live hearing* of Live's output while it plays. (B) *Offline analysis* of audio files, which runs without Live, like `src/ableton_mcp/vital.py`. They share nothing except the output format.
- **Level meters are free.** `Track.output_meter_left`/`_right` are "smoothed momentary peak" values, and `output_meter_level` is a "hold peak" with a 1-second hold. All three are floats from 0.0 to 1.0, get/observe ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)). Because of the hold, polling at ~10 Hz cannot miss a peak. The docs do not map 0..1 to dBFS. Calibration runs in Live 12.4.16 Lite measured an exact mapping that is linear in dB: value = (dBFS + 70) / 76, so 0 dBFS reads 0.921 and anything at or below −70 dBFS reads 0 (section 6). The meters work on Main and on return tracks. A MIDI track without an instrument has only `output_meter_level`.
- **Live's own Audio to MIDI does not help on Lite.** The edition table marks "Audio to MIDI" (Convert Harmony/Melody/Drums) as not included in Lite or Intro ([Ableton: Upgrade Live](https://www.ableton.com/en/upgrade-live/)). The LOM has no convert function either: "convert" does not appear on the Track, Clip, ClipSlot, Song or Application pages ([LOM](https://docs.cycling74.com/apiref/lom/)). Stem Separation (12.3) is Suite only, and Spectrum and EQ Eight are not in Lite ([Upgrade Live](https://www.ableton.com/en/upgrade-live/)). Use cases 2 and 3 therefore need outside libraries.
- **You can capture Live's output without extra software.** The manual says the "Resampling" option is in "any audio track's Input Type chooser" ([manual 17.4](https://www.ableton.com/en/live-manual/12/routing-and-i-o/)). `ClipSlot.fire` "starts recording if slot is empty and track is armed" ([LOM: ClipSlot](https://docs.cycling74.com/apiref/lom/clipslot/)), and `Clip.file_path` returns the recorded file ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)). Live 12.4.16 Lite offers Resampling. A recording driven only from the LOM captured a −6 dBFS test tone at −6.00 dBFS, as 24-bit WAV that stdlib `wave` reads (section 6). The capture track uses one of Lite's 8 track slots.
- **Licence and packaging blockers.** madmom's *models* are CC BY-NC-SA 4.0, which is non-commercial ([LICENSE](https://github.com/CPJKU/madmom/blob/main/LICENSE)). Its last release is from 2018 and is sdist-only, and it fails to build in a clean resolve (section 2.4). Essentia is AGPL-3.0 ([README](https://github.com/MTG/essentia/blob/master/README.md)). omnizart depends on madmom and TensorFlow. basic-pitch (Apache-2.0) resolves on 3.11 but **not on 3.13**, because it pins TensorFlow below 2.15.1 ([pyproject](https://github.com/spotify/basic-pitch/blob/main/pyproject.toml)). On 3.11 it ran and transcribed a test melody exactly, with its ONNX backend and `setuptools<81` (2.4). librosa 1.0 and numpy 2.5 require Python 3.12 or newer ([PyPI librosa](https://pypi.org/project/librosa/), [PyPI numpy](https://pypi.org/project/numpy/)), so the 3.11 CI leg gets older versions.
- **stdlib alone cannot read most audio files.** `wave` handles uncompressed PCM WAV. IEEE float WAV support arrives only in 3.15 ([wave docs](https://docs.python.org/3/library/wave.html)). `aifc` and `audioop` were removed in 3.13 ([What's New 3.13](https://docs.python.org/3/whatsnew/3.13.html), [PEP 594](https://peps.python.org/pep-0594/)).
- **Recommendation.** First, add a read-only `meters` op to the Remote Script, with no bridge change. The MCP server polls it ~10×/s while a scene plays and reports silent tracks, Main near clipping, and one track far louder than the rest (use case 4). Second, add offline BPM and key estimation from a file behind an optional `hearing` extra (librosa), keeping the core install at `mcp` only.

---

## 1. Framing: what hearing adds

The symbolic view already answers "which notes, which device, which parameter value". It cannot answer these questions:

| Lost in the symbolic view | Example | Signal that recovers it |
|---|---|---|
| Timbre | "Drift preset sounds thin" | Spectral centroid, band energies |
| Mix balance | "Bass is inaudible" | Per-track level, ideally per frequency band |
| Effects | Reverb tail, saturation, a Limiter pumping | Level over time, crest factor, spectrum |
| Result of a change | "Did lowering the cutoff remove the mud?" | Before/after band energy |
| External audio | A file or a mic recording | All of the above, plus tempo, key and notes |

The output goes to a language model, so every feature must reduce to a short JSON or text answer, for example `{"main_peak": 0.93, "band_200_500_db": -14.2}`. You can render a spectrogram PNG and pass it as an image. But reading exact frequencies or levels from a picture is error-prone, so treat it as a last resort (*assessment*).

Subsystem A (live) depends on Live running and on what the LOM exposes. Subsystem B (offline) is a pure function from file to features and can be tested in CI with generated WAVs.

---

## 2. Building blocks

### 2.1 LOM meters (subsystem A, zero dependencies)

| Property | Type / access | Documented meaning |
|---|---|---|
| `output_meter_left`, `output_meter_right` | float, get/observe | "Smoothed momentary peak value of [left/right] channel output meter, 0.0 to 1.0. For tracks with audio output only. This value corresponds to the meters shown in Live." Plus a warning: these meters "add a significant load to Live GUI resource usage" ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)) |
| `output_meter_level` | float, get/observe | "Hold peak value of output meters of audio and MIDI tracks, 0.0 to 1.0. For audio tracks, it is the maximum of the left and right channels. The hold time is 1 second." ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)) |
| `input_meter_left/_right/_level` | float, get/observe | Same definitions on the input side ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)) |
| `output_meter_peak_level` | — | **Does not exist** on the Track page |
| `has_audio_output` | bool, get | "1 for audio tracks and MIDI tracks with in[struments]" ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)) |

These properties are present in Live 12's Python API, with `add_output_meter_*_listener` methods (unofficial [structure-void Track dump](https://midiremotescripts.structure-void.com/reference/live12/Live.Track.runtime/)).

What the docs do not say, measured in Live 12.4.16 Lite (section 6):

- **Master and return tracks.** The Track page lists no meter restriction for them ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)). In Lite all three meters read correctly on `Song.master_track` and on `Song.return_tracks`.
- **MIDI tracks without an instrument.** Reading `output_meter_left` on such a track raises `RuntimeError: Tracks with MIDI output have no 'output_meter_left' property!`. `output_meter_level` still works. Guard each read, or check `has_audio_output` first.
- **Scale.** The value is not linear amplitude. It is linear in dB over a 76 dB range: `value = (dbfs + 70) / 76`, so `dbfs = 76 × value − 70`. Sines from 0 to −60 dBFS matched this to 3 decimals. 1.0 is +6 dBFS. The floor is −70 dBFS: −70 and −80 dBFS sines both read 0.0, so a 0.0 reading means "−70 dBFS or quieter", not digital silence.
- **Hold.** `output_meter_level` held a peak for about 1.4–1.8 s, then fell to 0 within about 0.5 s. That is longer than the documented 1 s. The smoothed L/R values fell to 0 within about 0.3 s of silence.
- **Sampling.** The Remote Script handles requests once per ~100 ms tick (README), so 10 Hz is the ceiling: the test loop reached 9.8 Hz. That is enough for `output_meter_level`, because it holds peaks. The smoothed L/R values are only coarse snapshots. Meters measure level only: they say nothing about frequency, so "muddy" is out of reach.

### 2.2 Capturing Live's output as audio (bridge from A to B)

- **No export from the LOM.** There is no export or render function. Export Audio/Video is a UI dialog. See [ableton-mcp-research.md section 3.6](ableton-mcp-research.md) and the [manual](https://www.ableton.com/en/live-manual/12/managing-files-and-sets/). Bounce to New Track / Bounce Track in Place are menu commands ([manual ch. 20](https://www.ableton.com/en/live-manual/12/bounce-to-audio/)), and "bounce" appears in neither the official LOM pages nor the structure-void dumps I checked.
- **Resampling route.** The "Resampling" Input Type routes Main to an audio track. "The recording track's own output will be suppressed while resampling", and recordings go to the Project's `Samples/Recorded`, or to the Temporary Folder until the Set is saved ([manual 17.4](https://www.ableton.com/en/live-manual/12/routing-and-i-o/)). An audio track can also take its input from another track ([manual 17.5](https://www.ableton.com/en/live-manual/12/routing-and-i-o/)), which allows per-track capture. All of this can be driven from the LOM: set `input_routing_type` from `available_input_routing_types` (audio/MIDI tracks only), set `arm`, then call `ClipSlot.fire(record_length)` on an empty slot ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/), [LOM: ClipSlot](https://docs.cycling74.com/apiref/lom/clipslot/)). `Clip.file_path` gives the file ([LOM: Clip](https://docs.cycling74.com/apiref/lom/clip/)). Costs: the capture track takes one of Lite's 8 audio/MIDI tracks ([ableton-mcp-research.md 1.1](ableton-mcp-research.md)), recording happens in real time, and it writes files into the user's Project. In Live 12.4.16 Lite an audio track lists `Resampling`, `Main`, the other audio tracks, the returns and `No Input` in `available_input_routing_types`, and the whole chain works from the LOM (section 6).
- **Restoring routing can fail.** The test track started on `Ext. In`, which was not in `available_input_routing_types`. Setting the saved `Ext. In` object back raised `ValueError: Invalid routing type`. A capture tool should therefore use its own capture track rather than borrow and restore a user's track.
- **File format of recordings.** File Type and Bit Depth are user settings under Record, Warp & Launch ([manual 19.7](https://www.ableton.com/en/live-manual/12/recording-new-clips/)). On this machine a fresh recording was RIFF WAV, integer PCM, 24-bit, 48 kHz (the audio device rate), and stdlib `wave` read it. Whether these are the factory defaults was not checked. If a user picks AIFF or 32-bit float, stdlib cannot read the file (2.3).
- **BlackHole loopback.** BlackHole is a macOS virtual loopback driver. Its source is GPL-3.0, its official binaries are "All rights reserved", it installs via a .pkg or `brew install blackhole-2ch`, and you need a Multi-Output Device in Audio MIDI Setup to keep hearing your speakers ([LICENSE](https://github.com/ExistentialAudio/BlackHole/blob/master/LICENSE), [README](https://github.com/ExistentialAudio/BlackHole/blob/master/README.md)). The MCP server would read it with python-sounddevice (MIT; "If you install the sounddevice module with pip on macOS or Windows, the PortAudio library will be installed automagically" ([installation](https://github.com/spatialaudio/python-sounddevice/blob/master/doc/installation.rst), [PyPI](https://pypi.org/project/sounddevice/))). That requires a system driver install, audio-routing changes and a microphone-permission prompt for the terminal ([Apple: microphone access](https://support.apple.com/guide/mac-help/control-access-to-the-microphone-on-mac-mchla1b1e1fe/mac)). That is too much setup for a beginner (*assessment*), so keep it as a fallback only.

### 2.3 Reading audio files (subsystem B)

- `wave` "supports uncompressed PCM and IEEE floating-point WAV formats". Float support was added in 3.15, and `WAVE_FORMAT_EXTENSIBLE` with a PCM subtype in 3.12 ([wave docs](https://docs.python.org/3/library/wave.html)). On this repo's 3.11 and 3.13, stdlib therefore reads integer PCM WAV only.
- `aifc` and `audioop` are gone in 3.13 ([What's New 3.13](https://docs.python.org/3/whatsnew/3.13.html), schedule in [PEP 594](https://peps.python.org/pep-0594/)). AIFF, FLAC, MP3 and float WAV all need a library. soundfile (BSD-3-Clause, wheels bundle libsndfile for macOS arm64 and x86_64) covers them ([PyPI soundfile](https://pypi.org/project/soundfile/)).

### 2.4 Analysis libraries

Resolve checks: `uv pip compile --python-version {3.11,3.13} --python-platform aarch64-apple-darwin`, run 2026-11-09.

| Library | Licence | Pulls in | py3.11 / py3.13 resolve | Notes |
|---|---|---|---|---|
| numpy | BSD-3-Clause and others ([PyPI](https://pypi.org/project/numpy/)) | — | 2.4.6 / 2.5.4 | 2.5 requires ≥3.12 ([PyPI](https://pypi.org/project/numpy/)) |
| soundfile | BSD-3-Clause ([PyPI](https://pypi.org/project/soundfile/)) | cffi, libsndfile in wheel | 0.14.0 / 0.14.0 | File I/O only |
| librosa | ISC ([repo](https://github.com/librosa/librosa)) | numba, numpy, scipy, scikit-learn, soundfile, soxr, pooch, … ([pyproject](https://github.com/librosa/librosa/blob/main/pyproject.toml)) | 0.11.0 / 1.0.0 | 1.0 requires ≥3.12 ([PyPI](https://pypi.org/project/librosa/)). Has `beat.beat_track` (Ellis dynamic-programming tracker) ([docs](https://librosa.org/doc/latest/api/generated/librosa.beat.beat_track.html)), `pyin`, `estimate_tuning`, `feature.chroma_cqt`, `feature.spectral_centroid`, `feature.rms`. **No key estimator**: the 1.0.0 index has only `key_to_notes`/`key_to_degrees`, which spell notes for a key that is already known ([index](https://librosa.org/doc/latest/genindex.html), [key_to_notes](https://librosa.org/doc/latest/api/generated/librosa.key_to_notes.html)). Key detection is DIY: chroma plus a profile correlation |
| pyloudnorm | MIT ([PyPI](https://pypi.org/project/pyloudnorm/)) | numpy, scipy | 0.2.0 / 0.2.0 | "Implementation of ITU-R BS.1770-4". The current ITU edition was approved 2023-11 ([ITU BS.1770](https://www.itu.int/rec/R-REC-BS.1770)) |
| basic-pitch | Apache-2.0 ([README](https://github.com/spotify/basic-pitch/blob/main/README.md)) | librosa, mir_eval, pretty_midi, resampy, scikit-learn, scipy, plus coremltools on macOS or TensorFlow <2.15.1 ([pyproject](https://github.com/spotify/basic-pitch/blob/main/pyproject.toml)) | 0.4.0 (coremltools 9.0) / **no solution** | Ships TF, CoreML, TFLite and ONNX models. Classifiers stop at 3.11, the README says "For Mac M1 hardware, we currently only support python version 3.10", and the last release was 2024-08-16 ([README](https://github.com/spotify/basic-pitch/blob/main/README.md), [PyPI](https://pypi.org/project/basic-pitch/)) |
| madmom | Code BSD-style, **models CC BY-NC-SA 4.0**; commercial use requires contacting JKU ([LICENSE](https://github.com/CPJKU/madmom/blob/main/LICENSE)) | Cython build | **build fails** / **build fails** (undeclared Cython build dependency) | Last release 0.16.1, 2018-11-14, sdist only ([PyPI](https://pypi.org/project/madmom/)) |
| essentia | **AGPL-3.0** ([README](https://github.com/MTG/essentia/blob/master/README.md)) | numpy | old dev builds / old dev builds | Has a `Key` algorithm with Krumhansl, Temperley, EDMA and other profiles ([docs](https://essentia.upf.edu/reference/std_Key.html)). Latest PyPI build 2.1b6.dev1438 has cp314 wheels only; the last cp311/cp313 macOS wheels are dev1389 (PyPI file list) |
| omnizart | MIT ([PyPI](https://pypi.org/project/omnizart/)) | madmom, TensorFlow, pyfluidsynth, vamp, … ([PyPI](https://pypi.org/project/omnizart/)) | **fails** via madmom / **fails** | sdist only |

Takeaway: a light numpy + soundfile stack, or librosa, resolves on both CI Pythons. ML transcription does not resolve on 3.13 today. If you want it, run it as an external CLI with its own Python, for example `uvx --python 3.11 basic-pitch …`, rather than as a dependency of this repo (*assessment*).

Install tests on 2026-11-09, macOS arm64, in throwaway venvs:

- **basic-pitch 0.4.0, Python 3.11.** The install pulls 401 MB. The first run failed with `ModuleNotFoundError: No module named 'pkg_resources'`, because its dependency resampy imports `pkg_resources`, which setuptools 84 no longer ships. Pinning `setuptools<81` fixed the import. The default CoreML backend then failed with `RuntimeError: Error compiling model: "Failed to create a working directory appropriate for URL: file:///var/folders/…/T/"`. The cause was the Claude Code sandbox blocking the system temp folder. Outside the sandbox, `uvx --python 3.11 --with "setuptools<81" basic-pitch` ran on the default CoreML backend. Both CoreML and ONNX (`--with onnxruntime`, `--model-serialization onnx`) transcribed a generated C4–E4–G4–C5 sine melody (0.5 s per note) exactly and identically: pitches 60, 64, 67, 72, onsets within 15 ms. On a 2 s file, the run time is mostly the import, so the backends cannot be compared for speed (CoreML 16 s CPU, ONNX 10 s CPU, excluding downloads). A working recipe is `uvx --python 3.11 --with "setuptools<81" basic-pitch <out_dir> <file>`. The first run downloads about 400 MB.
- **essentia 2.1b6.dev1389.** It installs from wheels on both 3.11 and 3.13 (about 75 MB). `KeyExtractor` found C major (strength 0.85) on the same melody on both. Its AGPL-3.0 licence is still the blocker.

### 2.5 What Live already does, and whether the agent can reach it

| Live feature | In Lite? | In LOM? |
|---|---|---|
| Convert Harmony / Melody / Drums to New MIDI Track ([manual ch. 13](https://www.ableton.com/en/live-manual/12/converting-audio-to-midi/)) | No ("Audio to MIDI": Standard and Suite) | No function found ([LOM](https://docs.cycling74.com/apiref/lom/)) |
| Stem Separation (12.3) | No (Suite only) | Not checked |
| Spectrum, EQ Eight | No | No analyzer readout documented |
| Tuner | Yes | No `TunerDevice` class in the LOM class list ([LOM](https://docs.cycling74.com/apiref/lom/)) |
| Capture MIDI | Yes | `Song.capture_midi(destination)` ([LOM: Song](https://docs.cycling74.com/apiref/lom/song/)) |

Edition columns come from [Upgrade Live](https://www.ableton.com/en/upgrade-live/). Capture MIDI matters for use case 3: a user with a MIDI keyboard needs no audio transcription at all.

---

## 3. Use cases

### 3.1 Verify own edits, map feedback to measurements (closed loop)

- **Problem.** The agent changes a filter or a volume and cannot tell whether it worked. The user says "muddy" or "I can't hear the bass".
- **Signal.** Per-track level gives "bass vs kick": meters are enough if they sit on separate tracks. "Muddy" needs band energy, for example 200–500 Hz relative to the total, before and after the change. That needs captured audio and an FFT. The word-to-band mapping is a heuristic table the server documents, not a standard (*assessment*).
- **Feasibility.** Level checks are feasible now with meters. The spectral loop needs the Resampling capture (2.2), then offline analysis.
- **Cost.** Meters: none. Spectral: numpy + soundfile, one capture track, and a few seconds of real-time recording per check.
- **Risks.** Capture writes files into the Project and uses a track slot under the Lite limit. The agent may chase numbers instead of listening, so always report the numbers *and* ask the user.

### 3.2 "Make something like X"

- **X is a track name.** No hearing needed: the model's own knowledge gives tempo, feel and instrumentation, and the existing generators do the rest.
- **X is an audio file.**
  - *BPM*: librosa `beat_track` ([docs](https://librosa.org/doc/latest/api/generated/librosa.beat.beat_track.html)). Feasible. Half/double-tempo errors are common in beat trackers (*assessment*), so report candidates.
  - *Key*: chroma plus a 24-key profile correlation, the method behind Essentia's `Key` profiles ([docs](https://essentia.upf.edu/reference/std_Key.html)). It is DIY in librosa (2.4). Feasible as a rough estimate. Relative major/minor confusion is likely (*assessment*). The result feeds `set_scale`.
  - *Drums*: onset times are feasible. A rough kick/snare/hat split by band energy at each onset is feasible but crude (*assessment*). Live's Convert Drums would do this but is not in Lite (2.5).
  - *Chords/melody*: needs ML transcription. basic-pitch is the realistic candidate and does not resolve on 3.13 (2.4).
  - *Synth timbre to a Vital preset*: full inverse synthesis is research-grade (*assessment*). What is realistic: rough descriptors (brightness, attack time, noisiness, pitch stability) mapped by hand onto `vital_create_preset` controls (filter cutoff, envelope attack, noise level, unison detune).
- **Cost.** librosa extra for BPM, key and onsets. An external Python 3.11 CLI for transcription.
- **Risks.** Extract style parameters (tempo, key, groove density, instrument roles), not the melody itself. Do not reproduce someone else's melody or chord sequence note for note. Lossy input degrades results. Ableton gives the same advice for its own converters ([manual 13.5](https://www.ableton.com/en/live-manual/12/converting-audio-to-midi/)).

### 3.3 Hum, sing or play into a mic, get a MIDI clip

- **Problem.** A beginner can hum an idea but cannot enter notes.
- **Signal.** Monophonic pitch track to notes. librosa `pyin` covers one voice ([index](https://librosa.org/doc/latest/genindex.html)); basic-pitch covers polyphony.
- **Feasibility.** Record the take in Live itself: arm an audio track with the mic as input, `fire` an empty slot, read `Clip.file_path` (2.2). Then transcribe offline and `write_notes`. No sounddevice is needed.
- **Cost.** pyin via librosa extra. Microphone permission for Live, which the user already grants for recording. One track slot.
- **Risks.** Hummed pitch drifts and needs quantising to the Set's scale. If the user has a MIDI keyboard, `Song.capture_midi` makes this unnecessary (2.5).

### 3.4 Sanity checks after generation

- **Problem.** After `create_drum_pattern` and friends, the agent cannot tell whether a track is silent (empty Drum Rack pad, muted track, wrong instrument), whether Main is near clipping, or whether one track drowns the others.
- **Signal.** `output_meter_level` per track, per return and on Main, polled while a scene plays (2.1).
- **Feasibility.** High. Only read-only LOM properties are involved, and the 1 s hold makes 10 Hz polling safe for peaks.
- **Cost.** One new op and one MCP tool. No dependencies, no user setup.
- **Risks.** The 0..1 scale is not documented in dB; use the measured mapping (2.1) and re-check it on new Live versions. A level is not loudness. MIDI tracks without an instrument have only `output_meter_level`. Playback must run in real time: a 4-bar check at 120 BPM takes 8 s.

### 3.5 Sample selection and Simpler root-note tuning

- **Problem.** `browse_library` returns names only. Picking "a bright 120 BPM loop" or tuning a one-shot to the key needs analysis.
- **Signal.** Loop BPM (onsets/tempo), spectral centroid for brightness, and the fundamental (`pyin`/`estimate_tuning`) for root-note offsets. All are offline on the file.
- **Feasibility.** Offline is feasible. Browser items carry a name and a `uri`, never a file path. This repo already maps a `samples` or `user_library` browser path to a file on disk by searching Live's library folders (`src/ableton_mcp/samples.py:1-5`, `resolve`), so offline analysis can reuse that. `Sample.file_path` is readable once a sample is loaded into Simpler ([LOM: Sample](https://docs.cycling74.com/apiref/lom/sample/)). Live's warping already guesses tempo for loops: `create_audio_clip` reports `warping` and `length_beats` (README). So BPM from Live may make that part redundant.
- **Cost.** librosa extra. Analysing many files is slow, so cache results by path and mtime.
- **Risks.** Pitch detection fails on noisy or inharmonic one-shots (drums), so restrict it to tonal samples.

### 3.6 Mix assistant

- **Problem.** "Is my mix too loud or too quiet, is the kick fighting the bass, does it fall apart in mono?"
- **Signal.** Integrated LUFS on Main (BS.1770, via pyloudnorm). Kick vs bass masking: band-energy overlap in 40–120 Hz between two captured tracks. Mono compatibility: L/R correlation and the level drop of L+R. All need captured stereo audio. Meters cannot give any of them.
- **Feasibility.** Medium. Needs per-track or Main capture (2.2) and offline analysis.
- **Cost.** numpy, soundfile and pyloudnorm, plus capture time and track slots.
- **Risks.** Loudness targets differ by platform and are a moving target. Report numbers with plain explanations, not verdicts (*assessment*).

### 3.7 Lessons and ear training

- **Problem.** The lessons (`lessons.py`) explain concepts but cannot confirm what the user hears.
- **Signal.** Mostly meters ("the hi-hat track is now 6 dB quieter"). Occasionally one before/after spectral capture ("turning the filter down removed most energy above 2 kHz").
- **Feasibility.** Meters-based confirmation can ship with use case 4. Spectral confirmation follows the capture path.
- **Cost.** Same as 3.4 and 3.1.
- **Risks.** Numbers can replace listening. The lesson text should ask the user to listen first, then show the number.

---

## 4. Priority table

| # | Use case | Signal | Cost |
|---|---|---|---|
| 4 | Sanity checks | LOM meters (live) | Zero deps. One read-only op and a tool |
| 2 (part) | BPM and key from a file | Tempo + chroma/key profile (offline) | Optional librosa extra. No Live needed |
| 7 | Lessons confirmation (level) | LOM meters (live) | Reuses use case 4 |
| 1 | Closed loop, level part | LOM meters | Reuses use case 4 |
| 5 | Sample selection and tuning | Tempo, centroid, f0 (offline) | librosa extra. File paths via `samples.resolve` |
| 1 | Closed loop, spectral part | Band energy on captured audio | numpy + soundfile, Resampling capture, one track slot |
| 6 | Mix assistant | LUFS, band overlap, L/R correlation | + pyloudnorm, capture |
| 3 | Hum to MIDI | pyin (mono) / basic-pitch (poly) | librosa extra. basic-pitch only as an external 3.11 CLI |
| 2 (rest) | Chords, melody, timbre matching | ML transcription, descriptors | External CLI. Timbre matching rough only |

---

## 5. Recommendation

1. **Ship use case 4 first.**
   - In `remote_script/AbletonMCP/ops.py`, add a `meters` op registered as `"meters": (meters, False)` in `OPS`: read-only, no undo step, no change to `bridge.py` or the wire protocol. It returns `output_meter_level`, `output_meter_left` and `output_meter_right` for every track (with `has_audio_output`), every return track and `master_track`, wrapping each read in a try so a missing property comes back as `null` instead of failing the whole call.
   - In the MCP server, a tool such as `check_levels(scene, seconds=4)` fires the scene, polls `meters` about every 100 ms, stops, and returns per-track max and mean. It also returns flags: `silent` (max below a small threshold while the track has clips), `main_hot` (Main max at or above the calibrated clip value), and `outlier` (one track far above the median). Do not use listeners, because of the documented GUI load of the L/R meters ([LOM: Track](https://docs.cycling74.com/apiref/lom/track/)).
   - Convert meter values with the measured mapping `dbfs = 76 × value − 70` (section 6), and report dBFS, not raw values. Report a 0.0 reading as "below −70 dBFS". A `silent` threshold must sit above that floor, for example −60 dBFS (*assessment*). Keep the calibration as a manual check to repeat on new Live versions: generated sine WAVs at known dBFS (stdlib `wave` writes integer PCM), loaded with `create_audio_clip`.
2. **Then BPM and key from a file.** Put it in a new `src/ableton_mcp/hearing.py` behind an optional extra `hearing = ["librosa"]`, imported lazily. If the extra is missing, the tool returns an install hint, and the base install stays `mcp` only. Use `beat_track` for BPM (report the half and double candidates), and chroma averaged over the file correlated with 24 rotated major/minor profiles for key. The output feeds `set_tempo` and `set_scale`. Test in CI with generated WAVs (a click track at a known BPM, a scale run in a known key) on both 3.11 (librosa 0.11) and 3.13 (librosa 1.0).
3. **Defer** audio capture via Resampling, the mix assistant and transcription until a use case needs them. Resampling works in Lite (section 6), so capture is not blocked. Do not add madmom, essentia or omnizart (licences, builds). Keep basic-pitch out of the dependency tree. If it is needed, call it as an external CLI.

---

## 6. Checks run in Live 12.4.16 Lite

Run on 2026-11-09 on macOS with Live 12.4.16 Lite (embedded Python 3.11) and a new, unsaved default Set: 2 MIDI tracks, 2 audio tracks, returns A-Reverb and B-Delay. A temporary `probe` op in the Remote Script ran Python on Live's main thread. It was removed afterwards and never committed. Track faders, Main and clip gain were at 0 dB, pan centre.

- **Routing.** `available_input_routing_types` of an audio track: `Resampling`, the other audio track, `A-Reverb`, `B-Delay`, `Main`, `No Input`.
- **Meter calibration.** A 1 kHz stereo sine, 16-bit 44.1 kHz WAV, 2 s at each of 0, −6, −12 and −20 dBFS, was loaded with `create_audio_clip` with warping off. It was played and polled at 9.8 Hz:

  A second run used a 24-bit WAV, so that quiet levels keep their resolution: 1 kHz, 2 s at each level from −10 to −80 dBFS in 10 dB steps. The table gives the median of `output_meter_left` over the middle second of each tone. Main read the same values.

  | Sine (dBFS) | Meter value | `(dbfs + 70) / 76` |
  |---|---|---|
  | 0 | 0.921 | 0.921 |
  | −6 | 0.842 | 0.842 |
  | −10 | 0.7895 | 0.7895 |
  | −12 | 0.763 | 0.763 |
  | −20 | 0.658 / 0.6579 | 0.6579 |
  | −30 | 0.5263 | 0.5263 |
  | −40 | 0.3947 | 0.3947 |
  | −50 | 0.2632 | 0.2632 |
  | −60 | 0.1316 | 0.1316 |
  | −70 | 0.0 | 0.0 |
  | −80 | 0.0 | clamped |

  Each 10 dB step is exactly 0.1316 = 10/76, so the scale is linear in dB over a 76 dB range, from −70 to +6 dBFS. The track meter matched the Main meter exactly, so a centred pan costs nothing at the meter.
- **Return meters.** With track 4's send A at maximum, `A-Reverb` read L 0.733, R 0.681, level 0.830.
- **MIDI track meters.** On an empty MIDI track, `output_meter_left` raised `RuntimeError: Tracks with MIDI output have no 'output_meter_left' property!`. `output_meter_level` returned 0.0.
- **Capture.** On audio track 3, these steps ran during playback of the test clip: set `input_routing_type` to `Resampling`, set `arm = True`, and call `clip_slots[0].fire(record_length=4.0)`. The recording started on the next bar (global quantisation), stopped by itself after 4 beats, and produced a clip with `file_path` `~/Music/Ableton/Live Recordings/<date> Temp Project/Samples/Recorded/3-Audio 0001 [<timestamp>].wav`. That is the Temporary Folder, because the Set was unsaved. File: RIFF WAV, PCM, 2 channels, 24-bit, 48 kHz. stdlib `wave` opened it. Its peak was −6.00 dBFS while the −6 dBFS segment played, so Resampling captures the level unchanged.
- **Side effects to handle in a real tool.** Restoring the original `Ext. In` input failed (`ValueError: Invalid routing type`, see 2.2). Importing a clip named `cal` into the unnamed track `4-Audio` renamed the track to `4-cal`. Test clips were deleted afterwards and the name was restored with `set_label`. The recording stays in Live's temporary folder.

---

## Open questions

Questions 1–6 of the first draft were answered by the checks in section 6 and the install tests in 2.4. These remain:

1. Are WAV and 24-bit Live's factory recording defaults? This machine is set to File Type WAV, Bit Depth 24 and sample rate 48000 Hz, but its owner may have changed them. Either way, a capture tool must read the file header and refuse formats it cannot read (AIFF, 32-bit float) with a clear message, instead of assuming WAV.
2. Does the meter mapping `(dbfs + 70) / 76` hold on Live 12.1–12.3 and on Standard and Suite?
3. Should the `hearing` extra use librosa (one dependency, but heavy: numba, scipy, scikit-learn) or numpy + soundfile with hand-written tempo and chroma (lighter, more code)?

## Unverified items, in one place

- Whether this machine's recording format is Live's factory default (2.2). The meter mapping on Live versions and editions other than 12.4.16 Lite (2.1). Ableton Help Center articles returned HTTP 403 to automated fetches, so no claim here relies on them. The Lite questions they would have answered were checked in Live itself (section 6).

---

## Sources

**Ableton (official)**
- Edition comparison: https://www.ableton.com/en/upgrade-live/
- Live 12 manual: [Routing and I/O (17.4 Resampling, 17.5 Internal Routings)](https://www.ableton.com/en/live-manual/12/routing-and-i-o/), [Recording New Clips (19.7 File Types)](https://www.ableton.com/en/live-manual/12/recording-new-clips/), [Converting Audio to MIDI](https://www.ableton.com/en/live-manual/12/converting-audio-to-midi/), [Bounce to Audio](https://www.ableton.com/en/live-manual/12/bounce-to-audio/), [Managing Files and Sets](https://www.ableton.com/en/live-manual/12/managing-files-and-sets/)
- Live 12 release notes: https://www.ableton.com/en/release-notes/live-12/

**Cycling '74 (official LOM)**
- [LOM index](https://docs.cycling74.com/apiref/lom/), [Track](https://docs.cycling74.com/apiref/lom/track/), [Clip](https://docs.cycling74.com/apiref/lom/clip/), [ClipSlot](https://docs.cycling74.com/apiref/lom/clipslot/), [Song](https://docs.cycling74.com/apiref/lom/song/), [Sample](https://docs.cycling74.com/apiref/lom/sample/), [Application](https://docs.cycling74.com/apiref/lom/application/)

**Unofficial Python API evidence**
- structure-void Live 12 [Track](https://midiremotescripts.structure-void.com/reference/live12/Live.Track.runtime/), [Clip](https://midiremotescripts.structure-void.com/reference/live12/Live.Clip.runtime/), [Song](https://midiremotescripts.structure-void.com/reference/live12/Live.Song.runtime/)

**Python**
- [wave](https://docs.python.org/3/library/wave.html), [What's New in 3.13](https://docs.python.org/3/whatsnew/3.13.html), [PEP 594](https://peps.python.org/pep-0594/)

**Capture**
- BlackHole: [LICENSE](https://github.com/ExistentialAudio/BlackHole/blob/master/LICENSE), [README](https://github.com/ExistentialAudio/BlackHole/blob/master/README.md)
- python-sounddevice: [installation](https://github.com/spatialaudio/python-sounddevice/blob/master/doc/installation.rst), [PyPI](https://pypi.org/project/sounddevice/)
- Apple: [Control access to the microphone on Mac](https://support.apple.com/guide/mac-help/control-access-to-the-microphone-on-mac-mchla1b1e1fe/mac)

**Analysis libraries**
- numpy: [PyPI](https://pypi.org/project/numpy/); soundfile: [PyPI](https://pypi.org/project/soundfile/)
- librosa: [repo](https://github.com/librosa/librosa), [pyproject](https://github.com/librosa/librosa/blob/main/pyproject.toml), [PyPI](https://pypi.org/project/librosa/), [API index](https://librosa.org/doc/latest/genindex.html), [beat_track](https://librosa.org/doc/latest/api/generated/librosa.beat.beat_track.html), [key_to_notes](https://librosa.org/doc/latest/api/generated/librosa.key_to_notes.html)
- basic-pitch: [README](https://github.com/spotify/basic-pitch/blob/main/README.md), [pyproject](https://github.com/spotify/basic-pitch/blob/main/pyproject.toml), [PyPI](https://pypi.org/project/basic-pitch/)
- madmom: [LICENSE](https://github.com/CPJKU/madmom/blob/main/LICENSE), [PyPI](https://pypi.org/project/madmom/)
- Essentia: [README](https://github.com/MTG/essentia/blob/master/README.md), [Key algorithm](https://essentia.upf.edu/reference/std_Key.html)
- omnizart: [PyPI](https://pypi.org/project/omnizart/)
- pyloudnorm: [PyPI](https://pypi.org/project/pyloudnorm/); ITU-R BS.1770: https://www.itu.int/rec/R-REC-BS.1770
