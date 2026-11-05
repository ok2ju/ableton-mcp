import pytest

from ableton_mcp import theory

# Real 808 Core Kit pads, read from Lite 12.1.10 (slice-3 notes.md).
PADS_808 = [
    {"note": 36, "name": "Bass Drum"}, {"note": 37, "name": "Rim Shot"},
    {"note": 38, "name": "Snare Drum"}, {"note": 39, "name": "Hand Clap"},
    {"note": 42, "name": "Closed Hi Hat"}, {"note": 46, "name": "Open Hi Hat"},
    {"note": 49, "name": "Cymbal"}, {"note": 51, "name": "Claves"},
]


def _grid(hits):
    return "".join("x" if h else "." for h in hits)


def test_euclid():
    assert _grid(theory.euclid(3, 8)) == "x..x..x."
    assert [i for i, h in enumerate(theory.euclid(4, 16)) if h] == [0, 4, 8, 12]
    assert sum(theory.euclid(5, 8)) == 5
    base = theory.euclid(3, 8)
    assert theory.euclid(3, 8, rotation=1) == base[1:] + base[:1]
    assert theory.euclid(0, 8) == [False] * 8
    with pytest.raises(ValueError):
        theory.euclid(9, 8)


def test_scales_and_roots():
    assert theory.SCALES["Major"] == [0, 2, 4, 5, 7, 9, 11]
    assert theory.SCALES["Minor"] == [0, 2, 3, 5, 7, 8, 10]
    assert len(theory.SCALES) == 12
    assert theory.parse_root("Db") == theory.parse_root("c#") == 1
    assert theory.parse_root("B") == 11 and theory.parse_root("Cb") == 11
    for bad in ("H", "", "C##", "Bx"):
        with pytest.raises(ValueError):
            theory.parse_root(bad)


def test_chords_c_major():
    got = [theory.chord("C", "Major", d) for d in (1, 6, 4, 5)]
    assert got == [[60, 64, 67], [69, 72, 76], [65, 69, 72], [67, 71, 74]]
    assert [theory.chord_name(p) for p in got] == ["C", "Am", "F", "G"]
    assert theory.chord_name(theory.chord("C", "Major", 5, sevenths=True)) == "G7"
    assert theory.chord_name(theory.chord("C", "Major", 2, sevenths=True)) == "Dm7"
    assert theory.chord_name(theory.chord("C", "Major", 7, sevenths=True)) == "Bm7b5"
    with pytest.raises(ValueError, match="7-note"):
        theory.chord("A", "Minor Pentatonic", 1)


def test_every_seven_note_chord_has_a_name():
    for scale, intervals in theory.SCALES.items():
        if len(intervals) == 7:
            for degree in range(1, 8):
                for sevenths in (False, True):
                    theory.chord_name(theory.chord("C", scale, degree, sevenths))


def test_chord_notes():
    notes, length, summary = theory.chord_notes("C", "Major", [1, 6, 4, 5], beats_per_chord=2)
    assert length == 8 and summary["chords"] == ["C", "Am", "F", "G"]
    assert {n["start_time"] for n in notes} == {0, 2, 4, 6}
    assert all(n["duration"] == 2 for n in notes)


def test_pick_drum_pitches_808():
    got = theory.pick_drum_pitches(PADS_808)
    assert got["kick"] == (36, "Bass Drum")
    assert [got[v][0] for v in ("rim", "snare", "clap", "closed_hat", "open_hat")] == [37, 38, 39, 42, 46]
    assert len({p for p, _ in got.values()}) == len(got)  # no pad used twice


def test_pick_drum_pitches_gm_fallback():
    pads = [{"note": 36, "name": "Kick Felt"}, {"note": 38, "name": ""}, {"note": 42, "name": "Hihat Closed"}]
    got = theory.pick_drum_pitches(pads)
    assert got["kick"] == (36, "Kick Felt")
    assert got["snare"] == (38, "")
    assert got["closed_hat"] == (42, "Hihat Closed")
    assert "open_hat" not in got
    offline = theory.pick_drum_pitches(None)
    assert {v: p for v, (p, _) in offline.items()} == {v: gm for v, (gm, _) in theory.DRUM_VOICES.items()}


def test_drum_styles_are_valid_grids():
    for style in theory.DRUM_STYLES.values():
        assert style["about"]
        for voice, row in style["grid"].items():
            assert voice in theory.DRUM_VOICES
            assert len(row) == 16 and set(row) <= set("Xx.")


def test_house_two_bars():
    notes, length, summary = theory.drum_notes("house", 2, None, theory.pick_drum_pitches(None))
    assert length == 8
    assert sorted(n["start_time"] for n in notes if n["pitch"] == 36) == [0, 1, 2, 3, 4, 5, 6, 7]
    assert summary["voices"]["kick"]["hits"] == 8 and summary["missing_voices"] == []


def test_euclid_layer_replaces_style_voice():
    layer = {"voice": "kick", "pulses": 3, "steps": 8}
    notes, _, summary = theory.drum_notes("house", 1, [layer], theory.pick_drum_pitches(None))
    assert sorted(n["start_time"] for n in notes if n["pitch"] == 36) == [0, 0.75, 1.5, 2, 2.75, 3.5]
    assert summary["voices"]["kick"]["hits"] == 6


def test_missing_voices():
    pads = [{"note": 36, "name": "Kick"}]
    notes, _, summary = theory.drum_notes("house", 1, None, theory.pick_drum_pitches(pads))
    assert {n["pitch"] for n in notes} == {36}
    assert set(summary["missing_voices"]) == {"clap", "closed_hat", "open_hat"}


def test_bass_eighths():
    notes, length, summary = theory.bass_notes("A", "Minor", "eighths", [1, 4], bars=2, octave=1)
    assert length == 8 and len(notes) == 16
    assert [n["pitch"] for n in notes] == [45] * 8 + [50] * 8
    assert summary["bar_roots"] == ["A", "D"]


def test_every_bass_pattern_stays_in_scale():
    for scale, intervals in theory.SCALES.items():
        degrees = list(range(1, len(intervals) + 1))
        for pattern in theory.BASS_PATTERNS:
            notes, _, _ = theory.bass_notes("E", scale, pattern, degrees)
            assert {(n["pitch"] - 4) % 12 for n in notes} <= set(intervals), (scale, pattern)


def test_bad_generator_args():
    with pytest.raises(ValueError):
        theory.bass_notes("A", "Minor Pentatonic", "eighths", [6])
    with pytest.raises(ValueError):
        theory.drum_notes(None, 1, None, {})
    with pytest.raises(ValueError):
        theory.drum_notes("house", 0, None, {})
    with pytest.raises(ValueError, match="octave"):
        theory.chord_notes("C", "Major", [1], octave=9)


def test_review_edge_cases():
    assert theory.pick_drum_pitches([{"note": 51, "name": "Ride Cymbal"}])["ride"] == (51, "Ride Cymbal")
    with pytest.raises(ValueError, match="7-note"):
        theory.chord_notes("C", "Major Pentatonic", [6])
    with pytest.raises(ValueError, match="longer than"):
        theory.chord_notes("C", "Major", [1] * 300, beats_per_chord=16)
    with pytest.raises(ValueError):
        theory.euclid(1, 10**9)
