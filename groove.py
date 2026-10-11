"""
groove.py -- the rhythm side of ambient-gen: beat grid, sections, drum
patterns, the 808 line, and the lo-fi effects (vinyl, sidechain, crusher).

Only used when groove.enabled is on. Then ambient.py lines the chords up on
bars and calls into here for everything rhythmic. The drums themselves are
ordinary synth notes (kick/snare/hats layers, each with a patch), so they
are tuned exactly like the pad: --set kick.synth.pitch_env_semitones=36

Time is counted in 16th-note "steps", 16 per bar of 4/4. With the snare on
beat 3, a 78 BPM groove feels half-time, the usual lo-fi/trap pocket.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal

from synth import _hash_noise

# chance of a kick on each 16th step of a bar (step 0 always). Tweak to
# change the groove's feel; groove.kick_density scales the optional ones.
KICK_PROB = [1.0, 0, 0, 0.12, 0, 0, 0.3, 0.1, 0, 0, 0.55, 0.12, 0, 0.18, 0.05, 0]
SNARE_STEP = 8          # beat 3: half-time backbeat
GHOST_SNARE_PROB = {7: 0.06, 15: 0.12, 14: 0.05}

# how the energy of the next section is chosen, from the current one
#   0 = no drums (intro / breakdown), 1 = kick, snare, 8th hats, 2 = full beat with rolls
ENERGY_MOVES = {0: {1: 0.7, 2: 0.3}, 1: {2: 0.65, 1: 0.25, 0: 0.1}, 2: {2: 0.55, 1: 0.25, 0: 0.2}}

ROLL_SUBDIVISIONS = [6, 8, 8, 12]   # hits per beat in a hat roll: 16th triplets, 32nds, 32nd triplets


class Grid:
    """Bar/step <-> seconds, with swing on every off-16th."""

    def __init__(self, S: dict):
        self.beat = 60 / S["groove.bpm"]
        self.bar = 4 * self.beat
        self.step = self.beat / 4
        self.swing = S["groove.swing"]

    def at(self, bar: int, step: float) -> float:
        late = self.swing * self.step if int(step) % 2 == 1 and step == int(step) else 0.0
        return bar * self.bar + step * self.step + late


def section_energies(S: dict, rng, n_bars: int) -> list[int]:
    """Energy for every bar, decided per section of groove.section_bars bars:
    an intro without drums, then a random walk between energies, and an
    outro without drums."""
    sec = S["groove.section_bars"]
    n_sec = math.ceil(n_bars / sec)
    energies, e = [], 0
    for i in range(n_sec):
        if i > 0:
            moves = dict(ENERGY_MOVES[e])
            if e == 2:  # breakdown_chance sets how often the full beat drops out
                moves[0] = S["groove.breakdown_chance"]
            choices, w = zip(*moves.items())
            e = int(rng.choice(choices, p=np.array(w) / sum(w)))
        if n_sec >= 3 and i == 0:
            e = 0                      # intro
        if n_sec >= 4 and i == n_sec - 1:
            e = 0                      # outro
        energies += [e] * sec
    return energies[:n_bars]


def drum_hits(S: dict, rng, grid: Grid, energies: list[int], total: float):
    """Every drum hit as (kind, time, velocity, pan), plus the kick steps of
    each bar (the 808 follows them).

    Each section gets a base pattern, sampled once from the probabilities
    above. Each bar then varies it a little, so the groove stays
    recognizable without looping exactly. Rolls and fills land mostly at
    the ends of phrases."""
    hits, kick_steps = [], {}
    sec = S["groove.section_bars"]
    jitter = S["groove.humanize_ms"] / 1000
    density = S["groove.kick_density"]

    def hit(kind, bar, step, vel, pan=0.0):
        t = grid.at(bar, step) + (rng.normal(0, jitter) if jitter > 0 else 0.0)
        t = max(t, 0.0)
        if t < total:
            hits.append((kind, t, float(np.clip(vel, 0.05, 1.0)), pan))

    base = None
    for bar, e in enumerate(energies):
        if bar % sec == 0:  # new section: new base pattern
            base = {
                "kick": [s for s in range(16) if s == 0 or rng.random() < KICK_PROB[s] * density],
                "hats16": [s for s in range(1, 16, 2) if rng.random() < 0.75],
            }
        if e == 0:
            kick_steps[bar] = []
            continue
        last_of_phrase = bar % sec == sec - 1

        # kick: the base pattern, with an optional hit added or dropped now and then
        kicks = [s for s in base["kick"] if s == 0 or rng.random() > 0.1]
        kicks += [s for s in range(1, 16) if s not in kicks and rng.random() < 0.03 * density]
        kick_steps[bar] = sorted(kicks)
        for s in kicks:
            hit("kick", bar, s, (1.0 if s == 0 else 0.85) * rng.uniform(0.9, 1.0))

        # snare on beat 3, rare ghost notes, a little fill at a phrase end
        hit("snare", bar, SNARE_STEP, rng.uniform(0.85, 1.0))
        for s, p in GHOST_SNARE_PROB.items():
            if rng.random() < p:
                hit("snare", bar, s, rng.uniform(0.2, 0.35))
        if e == 2 and last_of_phrase and rng.random() < 0.5:
            for s in (13, 14, 15):
                if rng.random() < 0.6:
                    hit("snare", bar, s, rng.uniform(0.3, 0.6))

        # hats: 8ths at energy 1, 8ths + most 16ths at energy 2, with rolls
        pan = S["hats.pan"]
        roll_beat = None
        if e == 2 and rng.random() < S["groove.hat_rolls"] * (1.6 if last_of_phrase else 1.0):
            roll_beat = int(rng.choice([1, 3, 3]))  # beat 2 or (more often) beat 4
        for s in range(16):
            if roll_beat is not None and roll_beat * 4 <= s < roll_beat * 4 + 4:
                continue
            if s % 2 == 0:
                hit("hats", bar, s, (0.85 if s % 4 == 0 else 0.65) * rng.uniform(0.85, 1.0), pan)
            elif e == 2 and s in base["hats16"]:
                hit("hats", bar, s, 0.45 * rng.uniform(0.8, 1.0), pan)
        if roll_beat is not None:
            n = int(rng.choice(ROLL_SUBDIVISIONS))
            rising = rng.random() < 0.6
            for i in range(n):
                ramp = (i + 1) / n if rising else 1 - i / n
                hit("hats", bar, roll_beat * 4 + 4 * i / n, 0.3 + 0.5 * ramp, pan)
    return hits, kick_steps


def bass_line(S: dict, rng, grid: Grid, energies: list[int], kick_steps: dict, root_at, total: float):
    """The 808: hits mostly with the kick, plays the chord root (sometimes
    the octave or the 5th), holds until the next hit, and sometimes slides
    into the new pitch. Returns (start, hold_until, midi, glide_from) tuples."""
    base_octave = 12 * (S["bass.octave"] + 1)
    events = []
    for bar, e in enumerate(energies):
        if e == 0:
            continue
        steps = {s for s in kick_steps.get(bar, []) if s == 0 or rng.random() < 0.85}
        if e == 2 and rng.random() < 0.35:
            steps.add(int(rng.choice([3, 7, 11, 14])))  # a syncopated push
        for s in sorted(steps):
            t = grid.at(bar, s)
            if t >= total:
                continue
            root = root_at(t)
            midi = base_octave + root
            r = rng.random()
            if r < 0.1:
                midi += 12
            elif r < 0.18:
                midi += 7
            events.append([t, None, midi, None])
    for i, ev in enumerate(events):
        nxt = events[i + 1][0] if i + 1 < len(events) else ev[0] + grid.bar
        ev[1] = min(nxt, ev[0] + grid.bar, total)  # hold to the next hit, at most a bar
        if i > 0 and events[i - 1][2] != ev[2] and nxt - ev[0] >= grid.step and rng.random() < S["groove.slide_chance"]:
            ev[3] = events[i - 1][2]
    return [tuple(e) for e in events]


# --- lo-fi effects -----------------------------------------------------------

def duck_gain(t, kick_times, depth: float, release: float):
    """Sidechain pump: dips by `depth` on every kick, recovers over `release`
    seconds. Computed from the kick times directly, so it needs no state."""
    g = np.ones(len(t))
    if depth <= 0 or len(kick_times) == 0:
        return g
    window = 6 * release  # each dip lasts exactly this long, wherever the block boundaries fall
    lo = np.searchsorted(kick_times, t[0] - window)
    hi = np.searchsorted(kick_times, t[-1], side="right")
    for k in kick_times[lo:hi]:
        i0, i1 = np.searchsorted(t, k), np.searchsorted(t, k + window)
        x = t[i0:i1] - k
        shape = np.exp(-x / release) * np.minimum(1.0, x / 0.004)  # 4 ms dip, exponential recovery
        g[i0:i1] = np.minimum(g[i0:i1], 1 - depth * shape)
    return g


class Vinyl:
    """Record surface noise: a hiss bed plus random crackle and the
    occasional pop. All of it comes from the absolute sample number (hash
    noise), and the filters keep their state between blocks, so it renders
    the same at any block size."""

    def __init__(self, S: dict, sr: int, seed: int):
        self.sr = sr
        self.seed = seed
        self.level = S["vinyl.level"]
        self.hiss = S["vinyl.hiss"]
        self.rate = S["vinyl.crackle_per_second"]
        self.hiss_sos = signal.butter(1, [400, min(9000, 0.45 * sr)], btype="bandpass", fs=sr, output="sos")
        self.click_sos = signal.butter(2, [1500, min(12000, 0.45 * sr)], btype="bandpass", fs=sr, output="sos")
        self.pop_sos = signal.butter(2, 600, fs=sr, output="sos")
        self.zi = {k: np.zeros((sos.shape[0], 2, 2))
                   for k, sos in (("hiss", self.hiss_sos), ("click", self.click_sos), ("pop", self.pop_sos))}

    def _impulses(self, index, rate, seed):
        """Random clicks at about `rate` per second, mostly quiet, some loud."""
        u = (_hash_noise(index, seed) + 1) / 2
        amp = np.abs(_hash_noise(index, seed + 1)) ** 3
        return np.where(u < rate / self.sr, amp, 0.0) * np.sign(_hash_noise(index, seed + 2))

    def render(self, index):
        hiss = np.stack([_hash_noise(index, self.seed + 10), _hash_noise(index, self.seed + 11)])
        hiss, self.zi["hiss"] = signal.sosfilt(self.hiss_sos, hiss, axis=-1, zi=self.zi["hiss"])
        clicks = np.stack([self._impulses(index, self.rate / 2, self.seed + 20),
                           self._impulses(index, self.rate / 2, self.seed + 30)])
        clicks, self.zi["click"] = signal.sosfilt(self.click_sos, clicks, axis=-1, zi=self.zi["click"])
        pops = np.stack([self._impulses(index, 0.08, self.seed + 40)] * 2)
        pops, self.zi["pop"] = signal.sosfilt(self.pop_sos, pops, axis=-1, zi=self.zi["pop"])
        return self.level * (0.15 * self.hiss * hiss + 3.0 * clicks + 6.0 * pops)


class Crusher:
    """Sampler-style grit: sample-and-hold downsampling plus bit reduction.
    The held value carries across blocks."""

    def __init__(self, S: dict, sr: int):
        self.ratio = S["master.downsample_hz"] / sr if S["master.downsample_hz"] > 0 else 0.0
        self.bits = S["master.bitcrush_bits"]
        self.last_group = -1
        self.last_value = np.zeros(2)

    def process(self, x, index):
        if self.ratio > 0:
            group = np.floor(index * self.ratio).astype(np.int64)
            prev = np.concatenate([[self.last_group], group[:-1]])
            starts = np.where(group != prev, np.arange(len(group)), -1)
            src = np.maximum.accumulate(starts)
            held = x[:, np.maximum(src, 0)]
            if (src < 0).any():  # still holding the previous block's value
                held[:, src < 0] = self.last_value[:, None]
            x = held
            self.last_group = int(group[-1])
            self.last_value = x[:, -1].copy()
        if self.bits > 0:
            q = 2 ** (self.bits - 1)
            x = np.round(x * q) / q
        return x
