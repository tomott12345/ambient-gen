#!/usr/bin/env python3
"""
ambient.py -- generative ambient music, rendered straight to a long WAV file.

    python3 ambient.py                               # 30 minutes, default sound
    python3 ambient.py --minutes 180 --seed 7        # 3 hours, reproducible
    python3 ambient.py --preset presets/night.json   # a saved sound
    python3 ambient.py --set pad.brightness=0.2 --set bells.per_minute=2
    python3 ambient.py --set pad.patch=warm_strings --set pad.synth.cutoff_hz=500
    python3 ambient.py --list-params                 # every knob as JSON (for a UI)
    python3 ambient.py --list-patches                # every synth patch
    python3 ambient.py --preset presets/techno.json --midi-out FM-1 --midi-layers lead   # play a hardware synth
    python3 ambient.py --preset presets/night.json --export-midi night.mid --no-audio    # save a MIDI file

Every run also writes <name>.json next to the WAV with the exact settings and
seed. Pass that file back with --preset to render the identical piece again.

The file reads top to bottom:
  1. VOCABULARY   note names, scales, and how chords tend to move
  2. KNOBS        every setting you can tweak, with its range and a short help
  3. COMPOSE      plan the whole piece up front as a list of notes. This is
                  cheap: hours of music are only a few thousand notes.
  4. INSTRUMENTS  pad, bass and bells are played by the synth engine in
                  synth.py, each with its own patch (patches/*.json); "air"
                  noise and the reverb live here
  5. RENDER       walk through time in 10-second blocks. Each block mixes the
                  notes sounding in it, adds reverb, and is written to disk, so
                  a 10-hour file needs no more memory than a 10-second one.
  6. COMMAND LINE
"""

from __future__ import annotations

import argparse
import bisect
import zlib
import json
import math
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import signal

import ambient_io
import groove
import midi_out
import synth

TAU = 2 * math.pi


# =============================================================================
# 1. VOCABULARY -- add your own scales here; they appear in --list-params
# =============================================================================

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

SCALES = {  # semitones above the key's root
    "major":            [0, 2, 4, 5, 7, 9, 11],
    "minor":            [0, 2, 3, 5, 7, 8, 10],
    "dorian":           [0, 2, 3, 5, 7, 9, 10],   # minor, but hopeful
    "lydian":           [0, 2, 4, 6, 7, 9, 11],   # dreamy, floating
    "mixolydian":       [0, 2, 4, 5, 7, 9, 10],
    "phrygian":         [0, 1, 3, 5, 7, 8, 10],   # dark, exotic
    "major_pentatonic": [0, 2, 4, 7, 9],          # can't sound wrong
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "hirajoshi":        [0, 2, 3, 7, 8],          # Japanese, sparse
}

# How the chord root moves from one chord to the next, in scale steps, and
# how likely each move is. Moves of 3 and 4 steps (a 4th/5th) sound the most
# "inevitable"; 1-step moves sound like drifting.
CHORD_MOVES = {+3: 3, -3: 2, +4: 2, +1: 2, -1: 2, +2: 1, -2: 1}


# =============================================================================
# 2. KNOBS -- the single list of everything tweakable.
#    Change a default here, or override it per run with a preset or --set.
# =============================================================================

def K(path, default, help, lo=None, hi=None, choices=None):
    return {"path": path, "default": default, "help": help, "min": lo, "max": hi, "choices": choices}


KNOBS = [
    # the piece
    K("minutes", 30.0, "Length of the piece in minutes", 0.1, 720),
    K("seed", 0, "Random seed. 0 picks a fresh one; the seed actually used is saved in the .json", 0, 2**31 - 1),
    K("sample_rate", 48000, "Samples per second", choices=[44100, 48000, 96000]),
    K("bit_depth", 24, "WAV bit depth", choices=[16, 24]),

    # harmony
    K("harmony.key", "D", "Starting key", choices=NOTE_NAMES),
    K("harmony.scale", "dorian", "Scale every note is drawn from", choices=list(SCALES)),
    K("harmony.chord_notes", 4, "Notes per chord: 3 = triad, 4 = seventh, 5 = ninth", 2, 5),
    K("harmony.chord_seconds_min", 20.0, "Shortest time a chord is held", 2, 600),
    K("harmony.chord_seconds_max", 45.0, "Longest time a chord is held", 2, 600),
    K("harmony.modulate_minutes", 12.0, "Move to a neighbouring key this often (0 = never)", 0, 240),

    # pad: the slow, sustained chords
    K("pad.enabled", True, "Play the pad"),
    K("pad.level", 0.5, "Pad volume", 0, 1),
    K("pad.octave", 3, "Register: 3 sits around C3-C4", 1, 5),
    K("pad.open_voicing", True, "Spread chord notes over two octaves instead of one"),
    K("pad.restrike_beats", 0, "With the groove on: re-strike the chord every N beats (0 = hold it)", 0, 16),
    K("pad.reverb_send", 0.6, "How much pad goes into the reverb", 0, 1),

    # bass: the chord root, an octave or two down
    K("bass.enabled", True, "Play the bass"),
    K("bass.level", 0.28, "Bass volume", 0, 1),
    K("bass.octave", 2, "Register: 2 sits around C2-C3", 0, 4),
    K("bass.reverb_send", 0.1, "How much bass goes into the reverb", 0, 1),

    # bells: sparse melodic notes that come and go in waves
    K("bells.enabled", True, "Play the bells"),
    K("bells.level", 0.35, "Bell volume", 0, 1),
    K("bells.per_minute", 5.0, "Notes per minute at their busiest", 0, 60),
    K("bells.octave", 5, "Register: 5 sits around C5-G6", 3, 7),
    K("bells.chord_bias", 0.7, "Chance of picking a chord note over any scale note", 0, 1),
    K("bells.reverb_send", 0.8, "How much bell goes into the reverb", 0, 1),

    # lead: a second melody of held notes in phrases (flute, strings...), off by default
    K("lead.enabled", False, "Play the lead melody"),
    K("lead.level", 0.3, "Lead volume", 0, 1),
    K("lead.per_minute", 10.0, "Notes per minute at their busiest", 0, 60),
    K("lead.octave", 5, "Register: 5 sits around C5-G6 (an acid line sits at 2)", 1, 7),
    K("lead.note_beats", 1.5, "How long each note is held, in beats at groove.bpm (cut short by the next note)", 0.25, 8),
    K("lead.chord_bias", 0.75, "Chance of picking a chord note over any scale note", 0, 1),
    K("lead.reverb_send", 0.5, "How much lead goes into the reverb", 0, 1),

    # groove: tempo, beat grid and drums (off = pure ambient, as before)
    K("groove.enabled", False, "Turn on the beat: tempo, bar-aligned chord loops, drums, 808"),
    K("groove.style", "lofi", "Beat style: lofi (half-time, rolls, 808) or techno (four-on-the-floor, acid, long builds)",
      choices=["lofi", "techno"]),
    K("groove.kit", "custom", "Drum kit: sets the kick, snare and hats patches together ('custom' = use each layer's own patch)",
      choices=["custom"] + list(groove.KITS)),
    K("groove.bpm", 78.0, "Tempo. With the snare on beat 3, 70-90 BPM feels like lo-fi/trap half-time", 50, 180),
    K("groove.swing", 0.12, "Delay of every off-beat 16th, as a fraction of a 16th", 0, 0.6),
    K("groove.bars_per_chord", 2, "Bars each chord lasts", 1, 16),
    K("groove.loop_chords", 4, "Chords in the looping progression", 1, 8),
    K("groove.loop_repeats", 4, "Times the progression loops before a new one is made", 1, 64),
    K("groove.section_bars", 8, "Bars per section; the energy (no drums / beat / full beat) changes between sections", 2, 64),
    K("groove.breakdown_chance", 0.2, "Chance that a full-beat section is followed by a drumless breakdown", 0, 1),
    K("groove.kick_density", 1.0, "Scales the chance of the optional kicks", 0, 2.5),
    K("groove.hat_rolls", 0.35, "Chance per bar of a trap hi-hat roll (in full-beat sections)", 0, 1),
    K("groove.slide_chance", 0.3, "Chance that the 808 slides into a new pitch", 0, 1),
    K("groove.humanize_ms", 8.0, "Random timing wobble of each drum hit", 0, 40),
    K("groove.duck_depth", 0.35, "Sidechain: how far pad, bass and melody dip on each kick", 0, 1),
    K("groove.duck_release", 0.3, "Sidechain: seconds to recover after each kick", 0.02, 2),

    # drums: synth layers like the pad, each with its own patch (only play with the groove on)
    K("kick.enabled", True, "Play the kick"),
    K("kick.level", 0.6, "Kick volume", 0, 1),
    K("kick.midi", 36, "Kick pitch (MIDI note; 36 = C2)", 12, 72),
    K("kick.reverb_send", 0.05, "How much kick goes into the reverb", 0, 1),
    K("snare.enabled", True, "Play the snare"),
    K("snare.level", 0.45, "Snare volume", 0, 1),
    K("snare.midi", 52, "Snare body pitch (MIDI note)", 24, 84),
    K("snare.reverb_send", 0.2, "How much snare goes into the reverb", 0, 1),
    K("hats.enabled", True, "Play the hi-hats"),
    K("hats.level", 0.25, "Hi-hat volume", 0, 1),
    K("hats.midi", 72, "Hi-hat pitch (only matters for patches with a tone)", 36, 108),
    K("hats.pan", 0.2, "Hi-hat position: -1 left .. +1 right", -1, 1),
    K("hats.reverb_send", 0.1, "How much hi-hat goes into the reverb", 0, 1),
    K("openhat.enabled", True, "Play the open hi-hat (techno style)"),
    K("openhat.level", 0.25, "Open hi-hat volume", 0, 1),
    K("openhat.midi", 72, "Open hi-hat pitch (only matters for patches with a tone)", 36, 108),
    K("openhat.pan", -0.15, "Open hi-hat position: -1 left .. +1 right", -1, 1),
    K("openhat.reverb_send", 0.15, "How much open hi-hat goes into the reverb", 0, 1),
    K("perc.enabled", True, "Play the percussion: rimshots and similar (techno style)"),
    K("perc.level", 0.3, "Percussion volume", 0, 1),
    K("perc.midi", 76, "Percussion pitch (MIDI note)", 36, 108),
    K("perc.reverb_send", 0.3, "How much percussion goes into the reverb", 0, 1),

    # techno: only used with groove.style = techno
    K("techno.phrase_bars", 16, "Bars per phrase; one element enters or leaves at each phrase boundary", 4, 64),
    K("techno.intro_bars", 32, "Kick-and-hats-only bars at the start (DJ-friendly intro)", 0, 512),
    K("techno.outro_bars", 32, "Kick-and-hats-only bars at the end", 0, 512),
    K("techno.density", 0.6, "How many elements tend to play at once", 0.1, 1),
    K("techno.breakdown_chance", 0.15, "Chance per phrase of a breakdown (no kick) that builds into a drop", 0, 1),
    K("techno.bass_style", "auto", "Bassline rhythm: offbeat, rolling 16ths, or auto (changes per phrase)",
      choices=["auto", "offbeat", "rolling"]),
    K("techno.acid_density", 0.65, "Share of the 16 acid steps that play", 0.1, 1),
    K("techno.acid_accent", 0.3, "Share of acid notes that are accented (louder, brighter)", 0, 1),
    K("techno.acid_slide", 0.2, "Share of acid notes that slide into the next", 0, 1),
    K("techno.acid_sweep_octaves", 2.0, "How far the acid filter drifts open and shut over time", 0, 4),
    K("techno.clap_flam_ms", 11.0, "Spacing of the clap's three bursts", 2, 30),

    # vinyl: record crackle and hiss, laid over the finished mix
    K("vinyl.enabled", False, "Add vinyl surface noise"),
    K("vinyl.level", 0.5, "Vinyl noise volume", 0, 2),
    K("vinyl.hiss", 0.3, "Hiss under the crackle", 0, 1),
    K("vinyl.crackle_per_second", 8.0, "Crackles per second", 0, 100),

    # air: filtered noise that swells slowly, like wind or tape hiss
    K("air.enabled", True, "Play the air layer"),
    K("air.level", 0.05, "Air volume", 0, 1),
    K("air.cutoff_hz", 2500.0, "Top of the noise band; lower = darker", 200, 16000),
    K("air.swell_seconds", 40.0, "Length of one slow rise-and-fall", 4, 600),
    K("air.reverb_send", 0.5, "How much air goes into the reverb", 0, 1),

    # reverb: a long synthetic hall shared by every layer
    K("reverb.level", 0.6, "Wet reverb volume", 0, 1.5),
    K("reverb.seconds", 7.0, "Decay time (RT60)", 0.3, 20),
    K("reverb.damping", 0.5, "How much faster the highs fade than the lows", 0, 1),
    K("reverb.predelay_ms", 25.0, "Gap before the reverb starts", 0, 250),

    # master
    K("master.gain_db", -4.0, "Output gain before the soft limiter", -24, 12),
    K("master.lowpass_hz", 11000.0, "Gentle top-end roll-off for warmth", 1000, 20000),
    K("master.saturation", 0.0, "Tape-style saturation of the whole mix", 0, 1),
    K("master.bitcrush_bits", 0, "Reduce to this many bits for sampler grit (0 = off)", 0, 16),
    K("master.downsample_hz", 0.0, "Sample-and-hold to this rate for old-sampler aliasing (0 = off)", 0, 48000),
    K("master.fade_in", 8.0, "Fade-in seconds at the start", 0, 600),
    K("master.fade_out", 25.0, "Fade-out seconds at the end", 0, 600),
]

# Each synth-played layer picks a patch, and every synth setting can also be
# overridden per layer: pad.synth.cutoff_hz, bells.synth.decay, ...
# The knob defaults come from the layer's default patch.
LAYER_PATCHES = {"pad": "soft_pad", "bass": "round_bass", "bells": "fm_bell", "lead": "flute",
                 "kick": "kick_soft", "snare": "snare_lofi", "hats": "hat_closed",
                 "openhat": "openhat_909", "perc": "rim_909"}
DRUMS = ("kick", "snare", "hats", "openhat", "perc")

for _layer, _patch in LAYER_PATCHES.items():
    KNOBS.append(K(f"{_layer}.patch", _patch, f"Synth patch (patches/*.json); it fills in {_layer}.synth.*",
                   choices=synth.available_patches()))
    _values = synth.load_patch(_patch)
    for _p in synth.PARAMS:
        KNOBS.append(K(f"{_layer}.synth.{_p['name']}", _values[_p['name']], _p["help"], _p["min"], _p["max"],
                       _p["choices"]))

KNOB_BY_PATH = {k["path"]: k for k in KNOBS}
DEFAULTS = {k["path"]: k["default"] for k in KNOBS}


def coerce(path: str, value):
    """Turn a value from JSON or the command line into the knob's type, and range-check it."""
    knob = KNOB_BY_PATH.get(path)
    if knob is None:
        raise SystemExit(f"unknown setting {path!r} (see --list-params)")
    kind = type(knob["default"])
    try:
        if kind is bool:
            v = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
        elif kind is int:
            v = int(float(value))
        elif kind is float:
            v = float(value)
        else:
            v = str(value)
    except ValueError:
        raise SystemExit(f"{path}: expected a {kind.__name__}, got {value!r}")
    if knob["choices"] is not None and v not in knob["choices"]:
        raise SystemExit(f"{path}: {v!r} is not one of {knob['choices']}")
    if knob["min"] is not None and not (knob["min"] <= v <= knob["max"]):
        raise SystemExit(f"{path}: {v} is outside {knob['min']}..{knob['max']}")
    return v


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for key, v in d.items():
        path = f"{prefix}{key}"
        out.update(flatten(v, path + ".") if isinstance(v, dict) else {path: v})
    return out


def nest(flat: dict) -> dict:
    out: dict = {}
    for path, v in flat.items():
        *parents, leaf = path.split(".")
        node = out
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = v
    return out


def load_settings(preset: str | None, overrides: list[str]) -> dict:
    """defaults <- each layer's patch <- preset file <- --set overrides.
    Returns a flat {"pad.level": 0.5, "pad.synth.cutoff_hz": 750.0, ...} dict."""
    given = {}
    if preset:
        data = json.loads(Path(preset).read_text())
        data = data.get("settings", data)  # also accepts a render's .json sidecar
        data = {k: v for k, v in data.items() if k != "description"}  # a preset's one-line description
        given.update(flatten(data))
    for item in overrides:
        path, sep, v = item.partition("=")
        if not sep:
            raise SystemExit(f"--set expects path=value, got {item!r}")
        given[path.strip()] = v.strip()

    S = dict(DEFAULTS)
    kit = groove.KITS.get(coerce("groove.kit", given.get("groove.kit", S["groove.kit"])), {})
    for layer in LAYER_PATCHES:  # a chosen patch (or the kit's) replaces the whole synth section...
        patch = kit.get(layer) or given.get(f"{layer}.patch", S[f"{layer}.patch"])
        S[f"{layer}.patch"] = coerce(f"{layer}.patch", patch)
        for name, v in synth.load_patch(S[f"{layer}.patch"]).items():
            S[f"{layer}.synth.{name}"] = v
    for path, v in given.items():  # ...then individual settings tweak it
        if path.endswith(".patch") and path.split(".")[0] in kit:
            continue  # the kit decides these
        S[path] = coerce(path, v)
    if S["harmony.chord_seconds_max"] < S["harmony.chord_seconds_min"]:
        raise SystemExit("harmony.chord_seconds_max must be >= harmony.chord_seconds_min")
    return S


# =============================================================================
# 3. COMPOSE -- decide every note before any audio is made
# =============================================================================

@dataclass
class Note:
    kind: str        # "pad", "bass", "bells", "kick", "snare", "hats": the layer that plays it
    start: float     # seconds from the beginning of the piece
    release: float   # when the fade-out begins (pad/bass)
    end: float       # when it's silent and can be forgotten
    midi: int        # pitch, 60 = middle C
    vel: float = 1.0
    pan: float = 0.0           # -1 left .. +1 right
    seed: int = 0              # the note's own randomness (voice phases, drift), same in every block
    glide_from: int | None = None  # slide in from this pitch (808 slides)
    filter_octaves: float = 0.0    # per-note filter offset (techno acid sweep and accents)
    voice: object = field(default=None, repr=False)  # synth state carried between blocks


def make_note(kind, start, release, end, midi, **kw) -> Note:
    seed = zlib.crc32(f"{kind}:{start:.6f}:{midi}".encode())
    return Note(kind, start, release, end, midi, seed=seed, **kw)


def chord_pitch_classes(key: int, scale: list[int], degree: int, size: int) -> list[int]:
    """Stack every other scale note on `degree`: 1-3-5-7-9. Root first."""
    return [(key + scale[(degree + 2 * i) % len(scale)]) % 12 for i in range(size)]


def voice_chord(pcs: list[int], octave: int, open_voicing: bool) -> list[int]:
    """Put the chord's notes in one octave so chord changes move smoothly,
    then optionally lift every other note an octave for a wider sound."""
    notes = sorted(12 * (octave + 1) + pc for pc in pcs)
    if open_voicing:
        notes = [m + 12 * (i % 2) for i, m in enumerate(notes)]
    return notes


def compose(S: dict, rng_harmony, rng_bells, rng_drums, rng_lead=None):
    if S["groove.enabled"]:
        return compose_groove(S, rng_harmony, rng_bells, rng_drums, rng_lead)
    total = S["minutes"] * 60
    scale = SCALES[S["harmony.scale"]]
    key = NOTE_NAMES.index(S["harmony.key"])
    moves, weights = zip(*CHORD_MOVES.items())
    weights = np.array(weights) / sum(weights)
    every = S["harmony.modulate_minutes"] * 60
    next_modulation = every or math.inf

    notes: list[Note] = []
    chords = []  # (start, end, key, pitch classes): the bells read this
    degree, t = 0, 0.0
    while t < total:
        if t >= next_modulation:
            key = (key + int(rng_harmony.choice([5, 7]))) % 12  # up a 4th or 5th: the smoothest key change
            degree = 0
            next_modulation += every
        end = min(t + rng_harmony.uniform(S["harmony.chord_seconds_min"], S["harmony.chord_seconds_max"]), total)
        pcs = chord_pitch_classes(key, scale, degree, S["harmony.chord_notes"])
        chords.append((t, end, key, pcs))

        if S["pad.enabled"]:
            for m in voice_chord(pcs, S["pad.octave"], S["pad.open_voicing"]):
                notes.append(make_note("pad", t, end, end + S["pad.synth.release"], m, vel=1 / math.sqrt(len(pcs))))
        if S["bass.enabled"]:
            notes.append(make_note("bass", t, end, end + S["bass.synth.release"], 12 * (S["bass.octave"] + 1) + pcs[0]))

        degree = (degree + int(rng_harmony.choice(moves, p=weights))) % len(scale)
        t = end

    if S["bells.enabled"] and S["bells.per_minute"] > 0:
        notes += compose_melody(S, "bells", rng_bells, chords, total)
    if S["lead.enabled"] and S["lead.per_minute"] > 0:
        notes += compose_melody(S, "lead", rng_lead, chords, total)
    notes.sort(key=lambda n: n.start)
    return notes, chords


def compose_groove(S: dict, rng_harmony, rng_bells, rng_drums, rng_lead=None):
    """The beat version of compose(): chords loop on bar lines, sections
    switch between no drums, a beat, and a full beat, and drums, 808 and a
    quantized melody are written on top (see groove.py)."""
    total = S["minutes"] * 60
    grid = groove.Grid(S)
    n_bars = math.ceil(total / grid.bar)
    scale = SCALES[S["harmony.scale"]]
    key = NOTE_NAMES.index(S["harmony.key"])
    moves, weights = zip(*CHORD_MOVES.items())
    weights = np.array(weights) / sum(weights)
    every = S["harmony.modulate_minutes"] * 60
    next_modulation = every or math.inf
    per_chord = S["groove.bars_per_chord"]

    notes: list[Note] = []
    chords = []
    degree, bar, loop, plays = 0, 0, [], S["groove.loop_repeats"]
    while bar < n_bars:
        if plays >= S["groove.loop_repeats"]:  # time for a new progression
            if bar * grid.bar >= next_modulation:
                key = (key + int(rng_harmony.choice([5, 7]))) % 12
                degree = 0
                next_modulation += every
            loop = []
            for _ in range(S["groove.loop_chords"]):
                loop.append(degree)
                degree = (degree + int(rng_harmony.choice(moves, p=weights))) % len(scale)
            plays = 0
        for d in loop:
            if bar >= n_bars:
                break
            start, end = bar * grid.bar, min((bar + per_chord) * grid.bar, total)
            pcs = chord_pitch_classes(key, scale, d, S["harmony.chord_notes"])
            chords.append((start, end, key, pcs))
            if S["pad.enabled"]:
                every_beats = S["pad.restrike_beats"]
                strikes = list(np.arange(start, end, every_beats * grid.beat)) if every_beats else [start]
                for i, s0 in enumerate(strikes):
                    s1 = strikes[i + 1] if i + 1 < len(strikes) else end
                    for m in voice_chord(pcs, S["pad.octave"], S["pad.open_voicing"]):
                        notes.append(make_note("pad", float(s0), float(s1), float(s1) + S["pad.synth.release"], m,
                                               vel=1 / math.sqrt(len(pcs))))
            bar += per_chord
        plays += 1

    if S["groove.style"] == "techno":
        return compose_techno(S, rng_drums, grid, n_bars, notes, chords, scale, total)

    energies = groove.section_energies(S, rng_drums, n_bars)
    hits, kick_steps = groove.drum_hits(S, rng_drums, grid, energies, total)
    for kind, t, vel, pan in hits:
        if S[f"{kind}.enabled"]:
            hold = t + S[f"{kind}.synth.decay"]
            notes.append(make_note(kind, t, hold, hold + S[f"{kind}.synth.release"], S[f"{kind}.midi"],
                                   vel=vel, pan=pan))

    chord_starts = [c[0] for c in chords]
    root_at = lambda t: chords[bisect.bisect_right(chord_starts, t) - 1][3][0]
    for t, until, midi, glide in groove.bass_line(S, rng_drums, grid, energies, kick_steps, root_at, total):
        if S["bass.enabled"]:
            notes.append(make_note("bass", t, until, until + S["bass.synth.release"], midi, glide_from=glide))

    if S["bells.enabled"] and S["bells.per_minute"] > 0:
        notes += compose_melody(S, "bells", rng_bells, chords, total, grid=grid)
    if S["lead.enabled"] and S["lead.per_minute"] > 0:
        notes += compose_melody(S, "lead", rng_lead, chords, total, grid=grid)
    notes.sort(key=lambda n: n.start)
    return notes, chords


def compose_techno(S: dict, rng, grid, n_bars: int, notes: list, chords: list, scale, total: float):
    """Techno on top of the bar-aligned chords: groove.techno_arrangement()
    decides which elements play in each bar, groove.techno_events() writes
    their notes. The pad only plays in bars where it's in the arrangement;
    the bells layer plays chord stabs and the lead layer an acid line."""
    arrangement = groove.techno_arrangement(S, rng, n_bars)
    notes = [n for n in notes if n.kind != "pad" or "pad" in arrangement[min(int(n.start // grid.bar), n_bars - 1)][0]]
    chord_starts = [c[0] for c in chords]

    def chord_at(t):
        _, _, key, pcs = chords[bisect.bisect_right(chord_starts, t) - 1]
        return key, pcs

    for kind, t, hold, midi, vel, pan, glide, fo in groove.techno_events(S, rng, grid, arrangement, chord_at, scale, total):
        if not S[f"{kind}.enabled"]:
            continue
        if hold is None:  # struck: rings for the patch's decay
            hold = t + S[f"{kind}.synth.decay"]
        notes.append(make_note(kind, t, hold, hold + S[f"{kind}.synth.release"], midi, vel=vel, pan=pan,
                               glide_from=glide, filter_octaves=fo))
    notes.sort(key=lambda n: n.start)
    return notes, chords


def compose_melody(S: dict, layer: str, rng, chords, total: float, grid=None) -> list[Note]:
    """A melody for the bells or lead layer. Random (Poisson) timing, thinned
    by a slowly wandering density, so notes come in waves. Each pitch is a
    short step from the last one, favouring chord notes. That makes a
    meandering melody instead of random plinks.

    Bells are struck: each note rings for its patch's decay. Lead notes are
    held for lead.note_beats and one note ends where the next begins, like a
    single player phrasing (legato)."""
    scale = SCALES[S["harmony.scale"]]
    lo = 12 * (S[f"{layer}.octave"] + 1)
    hi = lo + 19
    bias = S[f"{layer}.chord_bias"]
    lead = layer == "lead"

    density = [0.5]  # one value per 30 s, bounded random walk 0.1..1
    for _ in range(int(total // 30) + 1):
        density.append(float(np.clip(density[-1] + rng.normal(0, 0.25), 0.1, 1.0)))

    chord_starts = [c[0] for c in chords]
    gap = 60 / S[f"{layer}.per_minute"]
    notes, prev, last = [], (lo + hi) / 2, -1.0
    t = rng.exponential(gap)
    while t < total:
        when = t
        if grid is not None:  # with the groove on, play on the nearest 8th note
            when = round(t / (2 * grid.step)) * 2 * grid.step
        if when > last and when < total and rng.random() < density[int(t // 30)]:
            _, _, key, pcs = chords[bisect.bisect_right(chord_starts, t) - 1]
            pool = np.array([m for m in range(lo, hi + 1) if (m - key) % 12 in scale])
            in_chord = np.isin(pool % 12, pcs)
            dist = np.abs(pool - prev)
            w = np.exp(-dist / 4) * np.where(in_chord, bias, 1 - bias) * np.where(dist == 0, 0.3, 1) + 1e-9
            m = int(rng.choice(pool, p=w / w.sum()))
            if lead:
                hold = when + S["lead.note_beats"] * 60 / S["groove.bpm"]
                notes.append(make_note("lead", when, hold, hold + S["lead.synth.release"], m,
                                       vel=rng.uniform(0.6, 1.0), pan=rng.uniform(-0.15, 0.15)))
            else:
                # a struck note is "held" for its decay time, then released
                hold = when + S["bells.synth.decay"]
                notes.append(make_note("bells", when, hold, hold + S["bells.synth.release"], m,
                                       vel=rng.uniform(0.4, 1.0), pan=rng.uniform(-0.7, 0.7)))
            prev, last = m, when
        t += rng.exponential(gap)
    if lead:  # one player: each note lets go when the next one starts
        for a, b in zip(notes, notes[1:]):
            if a.release > b.start:
                a.release = b.start
                a.end = b.start + S["lead.synth.release"]
    return notes


# =============================================================================
# 4. INSTRUMENTS -- pad, bass and bells: synth.render() with each layer's
#    patch (see synth.py). Air and reverb are effects, so they live here.
# =============================================================================


class Air:
    """Band-passed stereo noise with a slow swell. The filter keeps its
    state between blocks, so there are no seams."""

    def __init__(self, S: dict, sr: int, rng):
        top = min(S["air.cutoff_hz"], 0.45 * sr)
        self.sos = signal.butter(2, [top / 4, top], btype="bandpass", fs=sr, output="sos")
        self.zi = np.zeros((self.sos.shape[0], 2, 2))
        self.rng = rng
        self.period = S["air.swell_seconds"]
        self.offset = rng.random() * TAU

    def render(self, t):
        noise = self.rng.standard_normal((2, len(t)))
        x, self.zi = signal.sosfilt(self.sos, noise, axis=-1, zi=self.zi)
        swell = 0.55 + 0.45 * np.sin(TAU * t / self.period + self.offset)
        return x * swell


class Reverb:
    """Convolution reverb with a synthetic impulse response: decaying stereo
    noise, with the highs decaying faster than the lows (damping). Each block
    is convolved and its tail is carried into the next block (overlap-add)."""

    def __init__(self, S: dict, sr: int, rng):
        rt = S["reverb.seconds"]
        n = int(rt * sr)
        t = np.arange(n) / sr
        noise = rng.standard_normal((2, n))  # independent L/R noise = wide stereo
        low = signal.sosfilt(signal.butter(2, 2000, fs=sr, output="sos"), noise, axis=-1)
        high = noise - low
        high_rt = rt * (1 - 0.85 * S["reverb.damping"])
        ir = low * np.exp(-6.9 * t / rt) + high * np.exp(-6.9 * t / high_rt)
        ir *= np.clip(t / 0.01, 0, 1)  # 10 ms fade-in, no click
        ir = np.concatenate([np.zeros((2, int(S["reverb.predelay_ms"] / 1000 * sr))), ir], axis=1)
        self.ir = ir / np.sqrt((ir ** 2).sum(axis=1, keepdims=True))  # unity energy
        self.tail = np.zeros((2, self.ir.shape[1] - 1))

    def process(self, x):
        y = signal.fftconvolve(x, self.ir, axes=1)
        y[:, : self.tail.shape[1]] += self.tail
        n = x.shape[1]
        self.tail = y[:, n:]
        return y[:, :n]


# =============================================================================
# 5. RENDER
# =============================================================================

BLOCK_SECONDS = 10


def render(S: dict, notes: list[Note], out_path: Path, rngs) -> dict:
    sr = S["sample_rate"]
    total_samples = int(S["minutes"] * 60 * sr)
    block = BLOCK_SECONDS * sr
    n_blocks = math.ceil(total_samples / block)

    # each synth layer's settings: {"saw": 1.0, "cutoff_hz": 750.0, ...}
    patches = {layer: {p["name"]: S[f"{layer}.synth.{p['name']}"] for p in synth.PARAMS} for layer in LAYER_PATCHES}
    air = Air(S, sr, rngs["air"]) if S["air.enabled"] else None
    reverb = Reverb(S, sr, rngs["reverb"])
    vinyl = groove.Vinyl(S, sr, int(rngs["vinyl"].integers(2**31))) if S["vinyl.enabled"] else None
    crusher = groove.Crusher(S, sr) if S["master.bitcrush_bits"] or S["master.downsample_hz"] else None
    # sidechain: pad, bass and melody dip on every kick
    ducking = S["groove.enabled"] and S["groove.duck_depth"] > 0
    kick_times = np.array(sorted(n.start for n in notes if n.kind == "kick"))
    lowpass = signal.butter(2, min(S["master.lowpass_hz"], 0.45 * sr), fs=sr, output="sos")
    lowpass_zi = np.zeros((lowpass.shape[0], 2, 2))
    gain = 10 ** (S["master.gain_db"] / 20)
    total_s = total_samples / sr

    writer = ambient_io.WavWriter(out_path, sr, S["bit_depth"], seed=int(rngs["dither"].integers(2**31)))
    peak, over, next_note, active = 0.0, 0, 0, []
    t_start = time.monotonic()
    interactive = sys.stderr.isatty()
    try:
        for b in range(n_blocks):
            s0 = b * block
            n = min(block, total_samples - s0)
            t0, t1 = s0 / sr, (s0 + n) / sr
            t = (s0 + np.arange(n)) / sr

            # which notes sound in this block?
            while next_note < len(notes) and notes[next_note].start < t1:
                active.append(notes[next_note])
                next_note += 1
            active = [x for x in active if x.end > t0]  # finished notes (and their synth state) are dropped

            dry = np.zeros((2, n))
            send = np.zeros((2, n))
            ducked_dry = np.zeros((2, n)) if ducking else dry  # without ducking these are the same arrays
            ducked_send = np.zeros((2, n)) if ducking else send
            for note in active:
                i0 = max(0, math.ceil((note.start - t0) * sr))
                i1 = min(n, math.ceil((note.end - t0) * sr))
                if i1 <= i0:
                    continue
                left, right = synth.render(note, t[i0:i1] - note.start, patches[note.kind], sr)
                level, rsend = S[f"{note.kind}.level"], S[f"{note.kind}.reverb_send"]
                d, sd = (dry, send) if note.kind in DRUMS else (ducked_dry, ducked_send)
                d[0, i0:i1] += left * level
                d[1, i0:i1] += right * level
                sd[0, i0:i1] += left * level * rsend
                sd[1, i0:i1] += right * level * rsend
            if ducking:
                g = groove.duck_gain(t, kick_times, S["groove.duck_depth"], S["groove.duck_release"])
                dry += ducked_dry * g
                send += ducked_send * g
            if air:
                a = air.render(t) * S["air.level"]
                dry += a
                send += a * S["air.reverb_send"]

            mix = dry + reverb.process(send) * S["reverb.level"]
            index = s0 + np.arange(n, dtype=np.int64)
            if vinyl:
                mix += vinyl.render(index)

            # master: saturation, crusher, warmth filter, fades, gain, soft limiter
            if S["master.saturation"] > 0:
                k = 1 + 4 * S["master.saturation"]
                mix = np.tanh(mix * k) / k
            if crusher:
                mix = crusher.process(mix, index)
            mix, lowpass_zi = signal.sosfilt(lowpass, mix, axis=-1, zi=lowpass_zi)
            fade = np.ones(n)
            if S["master.fade_in"] > 0:
                fade = np.minimum(fade, t / S["master.fade_in"])
            if S["master.fade_out"] > 0:
                fade = np.minimum(fade, (total_s - t) / S["master.fade_out"])
            mix *= np.clip(fade, 0, 1) * gain
            peak = max(peak, float(np.abs(mix).max()))
            over += int((np.abs(mix) > 0.9).sum())
            writer.write(np.tanh(mix))  # tanh: transparent when quiet, rounds off peaks

            done = b + 1
            ambient_io.emit("progress", step="render", done=done, total=n_blocks)
            pct = 100 * done / n_blocks
            if interactive:
                print(f"\r  rendering {pct:5.1f}%  ({t1 / 60:.1f} of {total_s / 60:.1f} min)", end="", file=sys.stderr)
            elif done == n_blocks or done % max(1, n_blocks // 10) == 0:
                print(f"  rendering {pct:5.1f}%", file=sys.stderr)
    finally:
        writer.close()  # a Ctrl-C still leaves a valid, shorter WAV
        if interactive:
            print(file=sys.stderr)

    elapsed = time.monotonic() - t_start
    return {
        "seconds_rendered": round(writer.frames / sr, 2),
        "render_seconds": round(elapsed, 1),
        "speed_x_realtime": round(writer.frames / sr / max(elapsed, 1e-9), 1),
        "peak_dbfs_before_limiter": round(20 * math.log10(max(peak, 1e-9)), 2),
        "samples_softened_by_limiter": over,
    }


# =============================================================================
# 6. COMMAND LINE
# =============================================================================

def describe() -> dict:
    """Everything a front end needs to build its form, in one document:
    every setting (as --list-params), every preset with its description,
    its own settings and the fully resolved values it renders with, and
    every patch with its synth values (so a UI can show what a layer's
    synth settings become when its patch changes)."""
    presets = []
    for f in sorted(Path(__file__).parent.glob("presets/*.json")):
        raw = json.loads(f.read_text())
        presets.append({"name": f.stem, "description": raw.get("description", ""),
                        "settings": flatten({k: v for k, v in raw.items() if k != "description"}),
                        "resolved": load_settings(str(f), [])})
    return {
        "params": [{**k, "type": type(k["default"]).__name__} for k in KNOBS],
        "layers": {layer: {"default_patch": patch, "drum": layer in DRUMS} for layer, patch in LAYER_PATCHES.items()},
        "kits": groove.KITS,
        "presets": presets,
        "patches": [{**p, "values": synth.load_patch(p["name"])} for p in synth.patch_info()],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generative ambient music to a long WAV file.")
    p.add_argument("--minutes", type=float, help="length (overrides the preset)")
    p.add_argument("--seed", type=int, help="random seed (overrides the preset; 0 = fresh)")
    p.add_argument("--preset", help="JSON file of settings (a render's .json sidecar works too)")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                   help="override one setting, e.g. --set pad.synth.cutoff_hz=500 (repeatable)")
    p.add_argument("--out", help="output WAV path (default: renders/ambient_<preset>_<seed>.wav)")
    p.add_argument("--list-params", action="store_true", help="print every knob as JSON and exit")
    p.add_argument("--list-patches", action="store_true", help="print every synth patch as JSON and exit")
    p.add_argument("--list-presets", action="store_true", help="print every preset in presets/ as JSON and exit")
    p.add_argument("--describe", action="store_true",
                   help="print settings, presets (with resolved values) and patches (with values) as one JSON, for a UI")
    p.add_argument("--progress-json", action="store_true", help="print @@progress events for a front end")
    m = p.add_argument_group("MIDI (see midi_out.py)")
    m.add_argument("--export-midi", metavar="PATH", help="also save the composition as a .mid file, one track per layer")
    m.add_argument("--midi-merge-drums", action="store_true",
                   help="with --export-midi: all drums on one track, as an MPC or drum machine expects")
    m.add_argument("--midi-out", metavar="PORT", help="play layers live on a MIDI synth (part of the port name, e.g. FM-1)")
    m.add_argument("--midi-layers", default="pad", help="layers to send with --midi-out, comma-separated (default: pad)")
    m.add_argument("--midi-channel", type=int, default=1, help="MIDI channel for --midi-out, 1-16 (default 1)")
    m.add_argument("--midi-voices", type=int, default=12, help="most notes held at once; 0 = no limit (default 12, the FM-1's)")
    m.add_argument("--midi-clock", action="store_true", help="also send MIDI clock so the synth's arp/sequencer follows the tempo")
    m.add_argument("--midi-offset-ms", type=float, default=0.0, help="shift the MIDI against the backing track (+ = later)")
    m.add_argument("--list-midi-ports", action="store_true", help="print the MIDI outputs this computer can see and exit")
    p.add_argument("--no-audio", action="store_true", help="don't render a WAV (e.g. MIDI only)")
    args = p.parse_args(argv)

    if args.list_midi_ports:
        print("\n".join(midi_out.list_ports()) or "(no MIDI outputs found)")
        return 0

    if args.list_params:
        print(json.dumps([{**k, "type": type(k["default"]).__name__} for k in KNOBS], indent=2))
        return 0
    if args.describe:
        print(json.dumps(describe()))
        return 0
    if args.list_presets:
        presets = sorted(Path(__file__).parent.glob("presets/*.json"))
        print(json.dumps([{"name": f.stem, "path": str(f), "settings": json.loads(f.read_text())} for f in presets], indent=2))
        return 0
    if args.list_patches:
        print(json.dumps(synth.patch_info(), indent=2))
        return 0
    if args.progress_json:
        ambient_io.enable_progress()

    overrides = list(args.set)
    if args.minutes is not None:
        overrides.append(f"minutes={args.minutes}")
    if args.seed is not None:
        overrides.append(f"seed={args.seed}")
    S = load_settings(args.preset, overrides)
    if S["seed"] == 0:
        S["seed"] = secrets.randbelow(2**31 - 1) + 1

    name = Path(args.preset).stem if args.preset else "default"
    out = Path(args.out) if args.out else Path("renders") / f"ambient_{name}_{S['seed']}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)

    midi_layers = [l.strip() for l in args.midi_layers.split(",") if l.strip()]
    if args.midi_out:
        unknown = [l for l in midi_layers if l not in LAYER_PATCHES]
        if unknown:
            raise SystemExit(f"--midi-layers: unknown layer(s) {unknown}; choose from {', '.join(LAYER_PATCHES)}")
        if not 1 <= args.midi_channel <= 16:
            raise SystemExit("--midi-channel must be 1-16")
        port = midi_out.find_port(args.midi_out)  # fail now, not after a long render
        out = out.with_name(out.stem + "_backing.wav")  # the synth plays midi_layers; the WAV is everything else

    # independent random streams, so e.g. changing the air layer doesn't change the melody
    streams = np.random.SeedSequence(S["seed"]).spawn(8)
    rngs = dict(zip(["harmony", "bells", "air", "reverb", "dither", "drums", "vinyl", "lead"],
                    map(np.random.default_rng, streams)))

    ambient_io.emit("plan", steps=[{"step": "compose", "label": "Composing"},
                                   {"step": "render", "label": "Rendering audio"}])
    with ambient_io.step("compose", "Composing"):
        notes, chords = compose(S, rngs["harmony"], rngs["bells"], rngs["drums"], rngs["lead"])
    counts = {k: sum(n.kind == k for n in notes) for k in LAYER_PATCHES}
    beat = f"{S['groove.bpm']:g} BPM, " if S["groove.enabled"] else ""
    drums = f", {sum(counts[k] for k in DRUMS)} drum hits" if S["groove.enabled"] else ""
    lead = f", {counts['lead']} lead notes" if S["lead.enabled"] else ""
    print(f"{S['minutes']:g} min of {S['harmony.key']} {S['harmony.scale']}, {beat}seed {S['seed']}: "
          f"{len(chords)} chords, {counts['bells']} bell notes{lead}{drums} -> {out}", file=sys.stderr)

    files = []
    if args.export_midi:
        layers = midi_out.export(notes, args.export_midi, S["groove.bpm"], merge_drums=args.midi_merge_drums)
        print(f"MIDI file: {args.export_midi} ({', '.join(layers)})", file=sys.stderr)
        files.append({"kind": "midi", "format": "mid", "path": str(Path(args.export_midi).resolve())})

    if args.midi_out:
        if not any(n.kind in midi_layers for n in notes):
            raise SystemExit(f"--midi-layers {', '.join(midi_layers)}: this piece has no notes in those layers "
                             f"(is the layer enabled? in techno, an element may only enter later in a long piece)")
        audio_notes = [n for n in notes if n.kind not in midi_layers]
    else:
        audio_notes = notes

    stats = {}
    if not args.no_audio:
        with ambient_io.step("render", "Rendering audio"):
            stats = render(S, audio_notes, out, rngs)
        fmt = f"wav{S['bit_depth']}"
        ambient_io.emit("output", kind="master", format=fmt, path=str(out.resolve()))
        files.append({"kind": "master", "format": fmt, "path": str(out.resolve())})
        print(f"done: {stats['seconds_rendered'] / 60:.1f} min in {stats['render_seconds']} s "
              f"({stats['speed_x_realtime']}x realtime), peak {stats['peak_dbfs_before_limiter']} dBFS",
              file=sys.stderr)

    sidecar = out.with_suffix(".json")  # settings + seed, so the piece can always be made again
    midi_info = {"midi_out": {"port": port, "layers": midi_layers, "channel": args.midi_channel,
                              "note": "these layers were played on the synth and left out of the WAV"}} if args.midi_out else {}
    sidecar.write_text(json.dumps({"settings": nest(S), "chords": len(chords), "notes": counts, **midi_info, **stats},
                                  indent=2))
    files.append({"kind": "settings", "format": "json", "path": str(sidecar.resolve())})
    ambient_io.emit("manifest", files=files)

    if args.midi_out:
        backing = None if args.no_audio else out
        print(f"playing {', '.join(midi_layers)} on {port} (channel {args.midi_channel})"
              f"{' with the backing track' if backing else ''}. Ctrl-C stops.", file=sys.stderr)
        midi_out.play(notes, port, midi_layers, args.midi_channel, S["groove.bpm"],
                      voices=args.midi_voices or None, clock=args.midi_clock,
                      backing_wav=backing, offset_ms=args.midi_offset_ms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
