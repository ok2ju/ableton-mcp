"""Texts for the server instructions and the guided-lesson prompts.

Plain strings only. The lessons follow the chapter order of Ableton's Learning Music site
(https://learningmusic.ableton.com). The site states no licence, so it is linked and never quoted:
every text here is written in our own words. Names in backticks must be tool names
(tests/test_server.py checks this), so do not backtick anything else in snake_case.
"""

LEARNING_MUSIC = "https://learningmusic.ableton.com"
CHAPTER = {
    "beats": f"{LEARNING_MUSIC}/make-beats/make-beats.html",
    "scales": f"{LEARNING_MUSIC}/notes-and-scales/notes-and-scales.html",
    "chords": f"{LEARNING_MUSIC}/chords/chords.html",
    "bass": f"{LEARNING_MUSIC}/make-basslines/make-basslines.html",
    "melody": f"{LEARNING_MUSIC}/make-melodies/make-melodies.html",
    "structure": f"{LEARNING_MUSIC}/song-structure/song-structure.html",
}

INSTRUCTIONS = """\
This server drives the user's open Ableton Live Set in Session View. Assume the user is a beginner.

- Before changing anything, call `live_status`, then `get_set_overview`. Call `get_set_overview` again before you use track, slot or scene indices from an older answer.
- After each change, say in one or two plain sentences what changed and what the user will hear. Explain each music word (beat, bar, scale, chord, root...) the first time you use it. Never paste tool JSON to the user.
- Name tracks, clips and scenes by their role (Drums, Bass, Chords, Verse), and colour them with `set_label`.
- Live Lite allows 8 tracks and 16 scenes (see limits in `live_status`): reuse empty ones before you create more.
- Each change is one undo step. When the user dislikes a result, offer `undo_last`.
- Prefer the one-call tools when they fit: `create_drum_pattern`, `create_bassline`, `create_chord_progression`, `load_instrument`, `set_scale`. Use `write_notes` for anything they cannot do.
- For a synth sound the user describes ("a warm pad"), build a Vital preset with `vital_create_preset`; it needs no Live. If the Vital app is closed, the tool opens it with the preset; otherwise pass on its how_to_load text (pick the preset in Vital's browser under User). When the user plays Vital inside Live, load Vital on the track with `load_from_library` (plugins root) and pass `open=false`.
- Live cannot be saved from here. When you finish a task that changed the Set, end with one short reminder to press Cmd+S (Ctrl+S on Windows). Do not remind after each tool call, and do not remind when nothing changed.

Guided lessons, in order (MCP prompts the user starts from the / menu): lesson-first-beat, lesson-notes-and-scales, lesson-chords, lesson-bassline, lesson-melody, lesson-song-structure. explain-my-set describes the Set without changing it.
"""


def _closing(chapter_url: str, next_prompt: str | None) -> str:
    text = (
        "\n\nTo finish: offer the matching Learning Music chapter for hands-on practice in the browser, "
        f"{chapter_url}, and remind the user once to press Cmd+S (Ctrl+S on Windows) to save the Set."
    )
    if next_prompt:
        text += f" Then name the next lesson: the {next_prompt} prompt (/ableton:{next_prompt})."
    return text


FIRST_BEAT = """\
Teach me lesson 1 of 6: my first beat. I am new to making music.

Goal: a one-bar drum loop playing in Live, built by me step by step.

Idea to explain first, in two or three sentences of your own: a beat is a short pattern that repeats. A bar in 4/4 holds four beats. The kick drum usually lands on the beats, the snare or clap on beats 2 and 4, and the hi-hats fill the gaps and keep time.

Steps (stop after each one so I can listen and choose):
1. Check Live with `live_status` and `get_set_overview`. Find an empty MIDI track, or create one with `create_midi_track` (mind the Lite track limit).
2. Load a drum kit: `load_instrument` with kind drum_rack and a kit preset, e.g. "808 Core Kit", or let me pick one from `browse_library` root drums and load it with `load_from_library`.
3. Write the kick alone with `create_drum_pattern`: style null and one layer (voice kick, pulses 4, steps 16). Start it with `play_clip` and ask me how it feels.
4. Add the snare or clap on beats 2 and 4 (a layer with pulses 2, steps 16, rotation 4), then the hi-hats. Each call replaces the clip, so send every layer again each time. Say what you added and where it falls in the bar.
5. Ask me for a tempo (for example 90 for hip hop, 124 for house) and set it with `set_tempo`. Explain what tempo (BPM) means.
6. Name the clip and track by role with `set_label`.

Variation to try: swap to a style with `create_drum_pattern` (rock, house, hip_hop, trap, reggaeton, breakbeat) and compare it with my own beat.""" + _closing(CHAPTER["beats"], "lesson-notes-and-scales")

NOTES_AND_SCALES = """\
Teach me lesson 2 of 6: notes and scales.

Goal: I hear what a scale is and pick a key for my song.

Idea to explain first, in two or three sentences of your own: notes repeat in octaves, twelve steps apart. A scale picks seven (or five) of those twelve notes, and music built from one scale sounds like it belongs together. The key is the scale plus its home note, the root.

Steps (stop after each one so I can listen and choose):
1. Read the Set's current key from `get_set_overview`.
2. Find or create an empty MIDI track and load a simple melodic instrument with `load_instrument` (drift is in every edition).
3. Write one clip with `write_notes` that walks up the C Major scale one note per beat, then play it with `play_clip`.
4. Rewrite the same clip as A Minor and play it. Ask me which mood I prefer, and explain why the two sound different.
5. Set my chosen key with `set_scale` (Major or Minor: the chord lesson needs a seven-note scale), so Live highlights its notes in the MIDI editor. Tell me where to look in Live to see that.

Variation to try: play a pentatonic scale (Major Pentatonic or Minor Pentatonic) with `write_notes`, and explain why five notes are hard to get wrong. Keep the Set's key seven-note.""" + _closing(CHAPTER["scales"], "lesson-chords")

CHORDS = """\
Teach me lesson 3 of 6: chords.

Goal: a four-chord progression in my key, on its own track.

Idea to explain first, in two or three sentences of your own: a chord is three or more notes played together. A triad stacks every other note of the scale on top of a root. Chords are numbered by the scale degree of their root, so 1-6-4-5 is the same shape in any key.

Steps (stop after each one so I can listen and choose):
1. Read the key from `get_set_overview` (or ask me, and set it with `set_scale`). If it is pentatonic or blues, use its parent Major or Minor.
2. Find or create a MIDI track for chords and load a pad or keys sound with `load_instrument`.
3. Write the degrees 1, 6, 4, 5 with `create_chord_progression`, play it with `play_clip`, and name each chord for me (for example C, Am, F, G).
4. Ask me to choose a different order or degree, rewrite, and compare.
5. Play it together with my beat if I have one, and label the clip and track with `set_label`.

Variation to try: sevenths on, and what the extra note adds.""" + _closing(CHAPTER["chords"], "lesson-bassline")

BASSLINE = """\
Teach me lesson 4 of 6: a bassline.

Goal: a bassline that follows my chords and locks with my drums.

Idea to explain first, in two or three sentences of your own: the bass plays low notes, mostly the root of each chord, so it ties harmony and rhythm together. Its rhythm often answers the kick drum.

Steps (stop after each one so I can listen and choose):
1. Find my chord clip in `get_set_overview` and read it with `read_notes`, so the bass follows the same chord roots (tell me which degrees you found).
2. Find or create a MIDI track for bass and load a bass sound with `load_instrument` (for example drift "Deep Bass").
3. Write one root per bar, played as quarter notes, with `create_bassline` (pattern quarters), then play bass, chords and drums together.
4. Try the eighths and offbeat patterns, and ask me which one grooves best with the kick.
5. Label the clip and track with `set_label`.

Variation to try: the octaves or arpeggio pattern, or a passing note before each chord change written with `write_notes`.""" + _closing(CHAPTER["bass"], "lesson-melody")

MELODY = """\
Teach me lesson 5 of 6: a melody.

Goal: a short melody in my key that fits over my chords.

Idea to explain first, in two or three sentences of your own: a melody is the line you can sing. It sounds settled on notes of the current chord and creates pull on the notes in between. Repeating a short idea with a small change makes it memorable.

Steps (stop after each one so I can listen and choose):
1. Read the key and my chord clip with `get_set_overview` and `read_notes`.
2. Find or create a MIDI track for the melody and load a lead sound with `load_instrument`.
3. Write a two-bar idea with `write_notes` using only scale notes, mostly steps between neighbours and one leap. Play it over the chords with `play_clip`.
4. Ask me what to change (higher, lower, more rests, longer notes) and rewrite it.
5. Repeat the idea in bars 3 and 4 with a different ending, then label the clip and track with `set_label`.

Variation to try: end the phrase on the root, then on a different chord note, and hear the difference.""" + _closing(CHAPTER["melody"], "lesson-song-structure")

SONG_STRUCTURE = """\
Teach me lesson 6 of 6: song structure.

Goal: my loops arranged as song sections (scenes) that I can launch one after another.

Idea to explain first, in two or three sentences of your own: songs repeat sections such as intro, verse and chorus. Contrast comes from adding or removing parts, not only from new notes. In Session View, each scene (a row of clips) can hold one section.

Steps (stop after each one so I can listen and choose):
1. Look at my tracks, clips and scenes in `get_set_overview`.
2. Make my current loops the chorus scene: name and colour it with `set_label`.
3. Make a verse: copy the scene with `create_scene` (copy_of), then thin it out with `delete_clip` or change a part with `read_notes` and `write_notes`. Use `copy_clip` to copy single clips into other slots.
4. Make an intro with only one or two parts. Name every scene by its section.
5. Play the song section by section with `fire_scene`, and ask me which section needs more contrast. Balance the parts with `set_track_mix`.

Variation to try: a break scene with drums only, or a last chorus with one extra part.""" + _closing(CHAPTER["structure"], None)

EXPLAIN_MY_SET = """\
Explain my Live Set to me. Do not change anything: use only `live_status`, `get_set_overview`, `read_notes` and `read_device`.

1. Tell me the tempo, the time signature and the key in plain words.
2. Go through each track: its role (drums, bass, chords, melody...), its instrument, and what its clips play (rhythm, notes, range).
3. If there are scenes, say what each one seems to be for.
4. Point out one thing that works well and one idea to try next.
5. Recommend the Learning Music chapter that fits that idea (https://learningmusic.ableton.com) and the matching lesson prompt from this server's list.
"""
