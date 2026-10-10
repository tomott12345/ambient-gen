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
    K("master.fade_in", 8.0, "Fade-in seconds at the start", 0, 600),
    K("master.fade_out", 25.0, "Fade-out seconds at the end", 0, 600),
]

# Each synth-played layer picks a patch, and every synth setting can also be
# overridden per layer: pad.synth.cutoff_hz, bells.synth.decay, ...
# The knob defaults come from the layer's default patch.
LAYER_PATCHES = {"pad": "soft_pad", "bass": "round_bass", "bells": "fm_bell"}

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
        given.update(flatten(data))
    for item in overrides:
        path, sep, v = item.partition("=")
        if not sep:
            raise SystemExit(f"--set expects path=value, got {item!r}")
        given[path.strip()] = v.strip()

    S = dict(DEFAULTS)
    for layer in LAYER_PATCHES:  # a chosen patch replaces the whole synth section...
        S[f"{layer}.patch"] = coerce(f"{layer}.patch", given.get(f"{layer}.patch", S[f"{layer}.patch"]))
        for name, v in synth.load_patch(S[f"{layer}.patch"]).items():
            S[f"{layer}.synth.{name}"] = v
    for path, v in given.items():  # ...then individual settings tweak it
        S[path] = coerce(path, v)
    if S["harmony.chord_seconds_max"] < S["harmony.chord_seconds_min"]:
        raise SystemExit("harmony.chord_seconds_max must be >= harmony.chord_seconds_min")
    return S


# =============================================================================
# 3. COMPOSE -- decide every note before any audio is made
# =============================================================================

@dataclass
class Note:
    kind: str        # "pad", "bass" or "bells": the instrument, and its knob group
    start: float     # seconds from the beginning of the piece
    release: float   # when the fade-out begins (pad/bass)
    end: float       # when it's silent and can be forgotten
    midi: int        # pitch, 60 = middle C
    vel: float = 1.0
    pan: float = 0.0           # -1 left .. +1 right
    seed: int = 0              # the note's own randomness (voice phases, drift), same in every block
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


def compose(S: dict, rng_harmony, rng_bells):
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
        notes += compose_bells(S, rng_bells, chords, total)
    notes.sort(key=lambda n: n.start)
    return notes, chords


def compose_bells(S: dict, rng, chords, total: float) -> list[Note]:
    """Random (Poisson) timing, thinned by a slowly wandering density, so bells
    come in waves. Each pitch is a short step from the last one, favouring
    chord notes. That makes a meandering melody instead of random plinks."""
    scale = SCALES[S["harmony.scale"]]
    lo = 12 * (S["bells.octave"] + 1)
    hi = lo + 19
    bias = S["bells.chord_bias"]

    density = [0.5]  # one value per 30 s, bounded random walk 0.1..1
    for _ in range(int(total // 30) + 1):
        density.append(float(np.clip(density[-1] + rng.normal(0, 0.25), 0.1, 1.0)))

    chord_starts = [c[0] for c in chords]
    gap = 60 / S["bells.per_minute"]
    notes, prev = [], (lo + hi) / 2
    t = rng.exponential(gap)
    while t < total:
        if rng.random() < density[int(t // 30)]:
            _, _, key, pcs = chords[bisect.bisect_right(chord_starts, t) - 1]
            pool = np.array([m for m in range(lo, hi + 1) if (m - key) % 12 in scale])
            in_chord = np.isin(pool % 12, pcs)
            dist = np.abs(pool - prev)
            w = np.exp(-dist / 4) * np.where(in_chord, bias, 1 - bias) * np.where(dist == 0, 0.3, 1) + 1e-9
            m = int(rng.choice(pool, p=w / w.sum()))
            # a struck note is "held" for its decay time, then released
            hold = t + S["bells.synth.decay"]
            notes.append(make_note("bells", t, hold, hold + S["bells.synth.release"], m,
                                   vel=rng.uniform(0.4, 1.0), pan=rng.uniform(-0.7, 0.7)))
            prev = m
        t += rng.exponential(gap)
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
            for note in active:
                i0 = max(0, math.ceil((note.start - t0) * sr))
                i1 = min(n, math.ceil((note.end - t0) * sr))
                if i1 <= i0:
                    continue
                left, right = synth.render(note, t[i0:i1] - note.start, patches[note.kind], sr)
                level, rsend = S[f"{note.kind}.level"], S[f"{note.kind}.reverb_send"]
                dry[0, i0:i1] += left * level
                dry[1, i0:i1] += right * level
                send[0, i0:i1] += left * level * rsend
                send[1, i0:i1] += right * level * rsend
            if air:
                a = air.render(t) * S["air.level"]
                dry += a
                send += a * S["air.reverb_send"]

            mix = dry + reverb.process(send) * S["reverb.level"]

            # master: warmth filter, fades, gain, soft limiter
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

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generative ambient music to a long WAV file.")
    p.add_argument("--minutes", type=float, help="length (overrides the preset)")
    p.add_argument("--seed", type=int, help="random seed (overrides the preset; 0 = fresh)")
    p.add_argument("--preset", help="JSON file of settings (a render's .json sidecar works too)")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                   help="override one setting, e.g. --set pad.brightness=0.2 (repeatable)")
    p.add_argument("--out", help="output WAV path (default: renders/ambient_<preset>_<seed>.wav)")
    p.add_argument("--list-params", action="store_true", help="print every knob as JSON and exit")
    p.add_argument("--list-patches", action="store_true", help="print every synth patch as JSON and exit")
    p.add_argument("--list-presets", action="store_true", help="print every preset in presets/ as JSON and exit")
    p.add_argument("--progress-json", action="store_true", help="print @@progress events for a front end")
    args = p.parse_args(argv)

    if args.list_params:
        print(json.dumps([{**k, "type": type(k["default"]).__name__} for k in KNOBS], indent=2))
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

    # independent random streams, so e.g. changing the air layer doesn't change the melody
    streams = np.random.SeedSequence(S["seed"]).spawn(5)
    rngs = dict(zip(["harmony", "bells", "air", "reverb", "dither"], map(np.random.default_rng, streams)))

    ambient_io.emit("plan", steps=[{"step": "compose", "label": "Composing"},
                                   {"step": "render", "label": "Rendering audio"}])
    with ambient_io.step("compose", "Composing"):
        notes, chords = compose(S, rngs["harmony"], rngs["bells"])
    counts = {k: sum(n.kind == k for n in notes) for k in ("pad", "bass", "bells")}
    print(f"{S['minutes']:g} min of {S['harmony.key']} {S['harmony.scale']}, seed {S['seed']}: "
          f"{len(chords)} chords, {counts['bells']} bell notes -> {out}", file=sys.stderr)

    with ambient_io.step("render", "Rendering audio"):
        stats = render(S, notes, out, rngs)

    sidecar = out.with_suffix(".json")
    sidecar.write_text(json.dumps({"settings": nest(S), "chords": len(chords), "notes": counts, **stats}, indent=2))
    fmt = f"wav{S['bit_depth']}"
    ambient_io.emit("output", kind="master", format=fmt, path=str(out.resolve()))
    ambient_io.emit("manifest", files=[{"kind": "master", "format": fmt, "path": str(out.resolve())},
                                       {"kind": "settings", "format": "json", "path": str(sidecar.resolve())}])
    print(f"done: {stats['seconds_rendered'] / 60:.1f} min in {stats['render_seconds']} s "
          f"({stats['speed_x_realtime']}x realtime), peak {stats['peak_dbfs_before_limiter']} dBFS",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
