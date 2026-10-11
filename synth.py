"""
synth.py -- the tunable synth engine behind the pad, bass and bells.

One engine, many sounds. A *patch* (a JSON file in patches/) is a set of
values for the PARAMS below. Signal flow for one note:

    unison voices (1-7), each detuned and panned:
        saw + square + triangle + sine(FM) mix, with vibrato and slow drift,
        an optional pitch drop at the start and a glide from the previous note
    + sub sine an octave down + noise
    -> drive (saturation)
    -> resonant filter (lowpass / bandpass / highpass)
         cutoff follows the key, an attack-decay envelope, and a slow LFO
    -> amp envelope (attack, decay, sustain, release) x slow "breathing"

Everything that has to carry on from one 10-second block to the next
(filter memory, voice phases, random offsets) lives on the note's Voice. So a
note renders the same however the timeline is cut into blocks.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from numba import njit

TAU = 2 * math.pi
PATCH_DIR = Path(__file__).parent / "patches"
FILTER_MODES = {"off": -1, "lowpass": 0, "bandpass": 1, "highpass": 2}


def P(name, default, help, lo=None, hi=None, choices=None):
    return {"name": name, "default": default, "help": help, "min": lo, "max": hi, "choices": choices}


# Every synth setting. A patch file only lists the ones it changes; the rest
# take these defaults (which are a plain sine with a short envelope).
PARAMS = [
    # oscillators: how much of each waveform
    P("saw", 0.0, "Sawtooth level: bright, string-like", 0, 1),
    P("square", 0.0, "Square/pulse level: hollow, woody", 0, 1),
    P("pulse_width", 0.5, "Square's duty cycle; away from 0.5 gets thinner and nasal", 0.05, 0.95),
    P("triangle", 0.0, "Triangle level: soft, flute-like", 0, 1),
    P("sine", 1.0, "Sine level (the FM carrier when fm_index > 0)", 0, 1),
    P("sub", 0.0, "Sine one octave below: weight", 0, 1),
    P("noise", 0.0, "White noise level: breath, bow or tape hiss", 0, 1),
    # FM on the sine: a second sine wobbles its phase
    P("fm_ratio", 1.0, "Modulator pitch / note pitch. 1 = electric piano, 2 = hollow, 3.5 = bell, 7 = glassy", 0.25, 12),
    P("fm_index", 0.0, "FM amount; 0 = pure sine, higher = more overtones", 0, 10),
    P("fm_index_decay", 0.0, "Seconds for the FM shimmer to die away (0 = it stays)", 0, 60),
    # unison and pitch movement
    P("unison", 1, "Copies of each note, detuned against each other", 1, 7),
    P("detune_cents", 0.0, "Spread between the outermost unison voices", 0, 60),
    P("width", 0.0, "Stereo spread of the unison voices", 0, 1),
    P("drift_cents", 0.0, "Slow random pitch wander per voice (analog / tape feel)", 0, 40),
    P("vibrato_hz", 5.0, "Vibrato speed", 0.05, 12),
    P("vibrato_cents", 0.0, "Vibrato depth", 0, 60),
    P("pitch_env_semitones", 0.0, "Each note starts this far above its pitch and drops to it: kick and 808 punch", 0, 48),
    P("pitch_env_decay", 0.05, "Seconds for that pitch drop", 0.001, 2),
    P("glide_seconds", 0.08, "Slide time when a note glides in from the previous pitch (808 slides)", 0.005, 2),
    P("drive", 0.0, "Saturation before the filter: from warmth to grit", 0, 1),
    # filter
    P("filter", "off", "Filter type", choices=list(FILTER_MODES)),
    P("cutoff_hz", 2000.0, "Filter cutoff at middle C", 20, 20000),
    P("resonance", 0.1, "Emphasis at the cutoff; near 1 it whistles", 0, 0.95),
    P("key_track", 0.5, "How much the cutoff follows the pitch: 0 = fixed, 1 = fully", 0, 1),
    P("filter_env_octaves", 0.0, "How far the filter envelope moves the cutoff (negative closes it)", -6, 6),
    P("filter_attack", 1.0, "Seconds for the filter envelope to reach its peak", 0.001, 60),
    P("filter_decay", 4.0, "Seconds for the filter envelope to fall back", 0.01, 120),
    P("filter_lfo_hz", 0.05, "Speed of the slow filter sweep", 0.005, 10),
    P("filter_lfo_octaves", 0.0, "Depth of the slow filter sweep", 0, 4),
    # amplitude
    P("attack", 0.01, "Seconds to fade in", 0.001, 60),
    P("decay", 1.0, "Seconds to fall from the peak to the sustain level", 0.01, 60),
    P("sustain", 1.0, "Level held while the note is held (0 = a plucked/struck note)", 0, 1),
    P("release", 1.0, "Seconds to fade out after the note ends", 0.01, 60),
    P("breath_hz", 0.1, "Speed of the slow volume swell (each note varies it a little)", 0.01, 5),
    P("breath_depth", 0.0, "Depth of the slow volume swell", 0, 1),
    P("transpose", 0.0, "Shift every note by this many semitones (lets a drum patch carry its own tuning)", -48, 48),
    P("gain_db", 0.0, "Patch loudness trim, so patches can be swapped at the same level", -24, 24),
]
PARAM_BY_NAME = {p["name"]: p for p in PARAMS}


def available_patches() -> list[str]:
    return sorted(f.stem for f in PATCH_DIR.glob("*.json"))


def patch_info() -> list[dict]:
    """Name + description of every patch, for --list-patches and a UI picker."""
    return [{"name": n, "description": json.loads((PATCH_DIR / f"{n}.json").read_text()).get("description", "")}
            for n in available_patches()]


def load_patch(name: str) -> dict:
    """The full parameter set for a patch: PARAMS defaults, overlaid with the file."""
    path = PATCH_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"no patch named {name!r} (have: {', '.join(available_patches())})")
    data = json.loads(path.read_text())
    values = {p["name"]: p["default"] for p in PARAMS}
    for key, v in data.items():
        if key == "description":
            continue
        if key not in PARAM_BY_NAME:
            raise SystemExit(f"patches/{name}.json: unknown synth setting {key!r}")
        values[key] = type(PARAM_BY_NAME[key]["default"])(v)
    return values


# --- small helpers -----------------------------------------------------------

def midi_to_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def pan_gains(pan):
    """Equal-power panning: -1 = left, 0 = centre, +1 = right."""
    a = (np.asarray(pan) + 1) * math.pi / 4
    return np.cos(a), np.sin(a)


def _blep(p, dt):
    """PolyBLEP: a tiny correction around each jump in a saw or square that
    removes most of the harsh aliasing a naive digital waveform has."""
    out = np.zeros_like(p)
    lo = p < dt
    t = p[lo] / dt
    out[lo] = t + t - t * t - 1
    hi = p > 1 - dt
    t = (p[hi] - 1) / dt
    out[hi] = t * t + t + t + 1
    return out


def _hash_noise(index, seed: int):
    """White noise from the absolute sample number. That makes it repeatable
    whichever block a sample lands in. (splitmix64)"""
    x = index.astype(np.uint64) + np.uint64((seed * 0x9E3779B97F4A7C15) % 2**64)
    x ^= x >> np.uint64(30)
    x *= np.uint64(0xBF58476D1CE4E5B9)
    x ^= x >> np.uint64(27)
    x *= np.uint64(0x94D049BB133111EB)
    x ^= x >> np.uint64(31)
    return (x >> np.uint64(11)).astype(np.float64) * (2.0 / 2**53) - 1.0


def _lfo_phase(age, rate_hz, cents, start_phase=0.0):
    """Extra phase (in cycles, per Hz of pitch) from a sine pitch LFO. It is
    integrated in closed form, so it needs no state between blocks."""
    depth = cents * math.log(2) / 1200
    w = TAU * rate_hz
    return depth * (math.cos(start_phase) - np.cos(w * age + start_phase)) / w


@njit(cache=True)
def _svf(x, cutoff, resonance, mode, sr, state):
    """Zero-delay-feedback state-variable filter (Simper/Cytomic). Its cutoff
    can change every sample and it stays stable at any setting.
    `state` holds its two memory cells and is updated in place."""
    y = np.empty_like(x)
    k = 2.0 - 2.0 * resonance
    ic1, ic2 = state[0], state[1]
    for i in range(x.shape[0]):
        g = math.tan(math.pi * cutoff[i] / sr)
        a1 = 1.0 / (1.0 + g * (g + k))
        a2 = g * a1
        a3 = g * a2
        v3 = x[i] - ic2
        v1 = a1 * ic1 + a2 * v3
        v2 = ic2 + a2 * ic1 + a3 * v3
        ic1 = 2.0 * v1 - ic1
        ic2 = 2.0 * v2 - ic2
        if mode == 0:
            y[i] = v2
        elif mode == 1:
            y[i] = k * v1
        else:
            y[i] = x[i] - k * v1 - v2
    state[0] = ic1
    state[1] = ic2
    return y


# --- the voice ---------------------------------------------------------------

class Voice:
    """Everything one note needs to remember between blocks. Its random
    choices come from the note's seed, so they're the same on every run."""

    def __init__(self, note, p: dict):
        seed = note.seed
        rng = np.random.default_rng(seed)
        u = p["unison"]
        spread = np.linspace(-1, 1, u) if u > 1 else np.zeros(1)
        self.ratio = 2 ** (spread * p["detune_cents"] / 2 / 1200)
        self.gl, self.gr = pan_gains(np.clip(spread * p["width"] + note.pan, -1, 1))
        self.phase0 = rng.random(u)
        self.drift_hz = rng.uniform(0.02, 0.09, u)
        self.drift_phase = rng.random(u) * TAU
        self.breath_hz = p["breath_hz"] * rng.uniform(0.6, 1.4)
        self.breath_phase = rng.random() * TAU
        self.lfo_phase = rng.random() * TAU
        self.filter_state = np.zeros(4)  # two cells per channel
        self.seed = seed


def render(note, age, p: dict, sr: int):
    """Render `note` at the times in `age` (seconds since the note started).
    Returns (left, right). `p` is the layer's full synth parameter dict."""
    if note.voice is None:
        note.voice = Voice(note, p)
    v = note.voice
    hz = midi_to_hz(note.midi + p["transpose"])
    left = np.zeros(len(age))
    right = np.zeros(len(age))

    fm_index = None
    if p["sine"] > 0 and p["fm_index"] > 0:
        fm_index = p["fm_index"] * (np.exp(-6.9 * age / p["fm_index_decay"]) if p["fm_index_decay"] > 0 else 1.0)
    vibrato = _lfo_phase(age, p["vibrato_hz"], p["vibrato_cents"]) if p["vibrato_cents"] > 0 else 0.0
    # pitch drop and glide, as extra cycles (per Hz of pitch), integrated in closed form:
    # frequency x (1 + A*e^(-t/tau)) and x (1 + (from/to - 1)*e^(-t/tau))
    sweep = 0.0
    if p["pitch_env_semitones"] > 0:
        tau = p["pitch_env_decay"] / 3
        sweep = sweep + (2 ** (p["pitch_env_semitones"] / 12) - 1) * tau * (1 - np.exp(-age / tau))
    if note.glide_from is not None:
        tau = p["glide_seconds"] / 3
        sweep = sweep + (midi_to_hz(note.glide_from + p["transpose"]) / hz - 1) * tau * (1 - np.exp(-age / tau))

    for i in range(len(v.ratio)):
        f = hz * v.ratio[i]
        cycles = age + vibrato
        if p["drift_cents"] > 0:
            cycles = cycles + _lfo_phase(age, v.drift_hz[i], p["drift_cents"], v.drift_phase[i])
        if p["pitch_env_semitones"] > 0 or note.glide_from is not None:
            cycles = cycles + sweep
        cycles = f * cycles + v.phase0[i]
        ph = cycles % 1.0
        dt = f / sr
        x = 0.0
        if p["saw"] > 0:
            x = x + p["saw"] * (2 * ph - 1 - _blep(ph, dt))
        if p["square"] > 0:
            w = p["pulse_width"]
            sq = np.where(ph < w, 1.0, -1.0) + _blep(ph, dt) - _blep((ph - w) % 1.0, dt) - (2 * w - 1)
            x = x + p["square"] * sq
        if p["triangle"] > 0:
            x = x + p["triangle"] * (4 * np.abs(ph - 0.5) - 1)
        if p["sine"] > 0:
            mod = fm_index * np.sin(TAU * cycles * p["fm_ratio"]) if fm_index is not None else 0.0
            x = x + p["sine"] * np.sin(TAU * cycles + mod)
        left += x * v.gl[i]
        right += x * v.gr[i]
    norm = 1 / math.sqrt(len(v.ratio))
    left *= norm
    right *= norm

    if p["sub"] > 0:
        s = p["sub"] * 0.707 * np.sin(TAU * (hz / 2 * age + v.phase0[0]))
        left += s
        right += s
    if p["noise"] > 0:
        index = np.round((note.start + age) * sr).astype(np.int64)
        left += p["noise"] * 0.5 * _hash_noise(index, v.seed)
        right += p["noise"] * 0.5 * _hash_noise(index, v.seed + 1)

    if p["drive"] > 0:
        k = 1 + 9 * p["drive"]
        left = np.tanh(left * k) / math.tanh(k)
        right = np.tanh(right * k) / math.tanh(k)

    mode = FILTER_MODES[p["filter"]]
    if mode >= 0:
        octaves = 0.0
        if p["filter_env_octaves"] != 0:
            fa = p["filter_attack"]
            env = np.where(age < fa, age / fa, np.exp(-6.9 * (age - fa) / p["filter_decay"]))
            octaves = octaves + p["filter_env_octaves"] * env
        if p["filter_lfo_octaves"] > 0:
            octaves = octaves + p["filter_lfo_octaves"] * np.sin(TAU * p["filter_lfo_hz"] * age + v.lfo_phase)
        if note.filter_octaves:  # per-note offset, e.g. a techno acid line's slow filter sweep and accents
            octaves = octaves + note.filter_octaves
        base = p["cutoff_hz"] * 2 ** (p["key_track"] * (note.midi - 60) / 12)
        cutoff = np.clip(base * 2.0 ** (octaves + np.zeros(len(age))), 20, 0.45 * sr)
        left = _svf(left, cutoff, p["resonance"], mode, sr, v.filter_state[0:2])
        right = _svf(right, cutoff, p["resonance"], mode, sr, v.filter_state[2:4])

    # held: attack/decay/sustain; after `hold`: fade out from wherever it got to
    hold = note.release - note.start
    level_at_release = _ads(np.array([hold]), p)[0]
    env = np.where(age <= hold, _ads(age, p), level_at_release * _smooth(1 - (age - hold) / p["release"]))
    if p["breath_depth"] > 0:
        env = env * (1 + p["breath_depth"] * np.sin(TAU * v.breath_hz * age + v.breath_phase))
    g = env * note.vel * 10 ** (p["gain_db"] / 20)
    return left * g, right * g


def _smooth(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def _ads(age, p):
    """Attack (smooth rise), then an exponential fall to the sustain level."""
    a, s = p["attack"], p["sustain"]
    falling = s + (1 - s) * np.exp(-6.9 * np.maximum(age - a, 0) / p["decay"])
    return np.where(age < a, _smooth(age / a), falling)
