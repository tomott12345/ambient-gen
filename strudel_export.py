"""
strudel_export.py -- translate a composition into Strudel code (https://strudel.cc),
the browser live-coding environment, and a link that opens it there.

    python3 ambient.py --preset presets/lofi_trap.json --minutes 3 --export-strudel song.strudel.js
    -> song.strudel.js     the code (paste it into strudel.cc, or edit and live-code it)
       song.strudel.url    a strudel.cc link with the code in it (click, then press play)

How the translation works:
  - tempo: setcpm(bpm / 4), so one Strudel cycle is one bar of 4/4
  - every note is placed on a grid of 96 steps per bar (fine enough for hi-hat
    rolls and triplets) and written in mini-notation: "c3@24 ~@8 [e3,g3]@64"
  - the song is cut into blocks at bar lines no note is held across; each
    layer is an arrange() of those blocks, and a block that repeats is written
    once as a named constant
  - each layer's synth patch becomes the nearest Strudel sound (sawtooth,
    square, triangle, sine, supersaw, FM), filter, envelope and reverb; drums
    use the TR-909 or TR-808 sample banks; the kick ducks the other layers
    when the groove pumps

It's a translation, not a copy: Strudel's synths and effects sound different
from ambient-gen's engine, swing and humanize are snapped to the grid, and the
air/vinyl layers, 808 glides and per-note filter sweeps are left out.
"""

from __future__ import annotations

import base64
import math
import urllib.parse

STEPS = 96                      # grid steps per bar
MELODIC = ("pad", "bass", "bells", "lead")
DRUMS = ("kick", "snare", "hats", "openhat", "perc")
NAMES = ["c", "db", "d", "eb", "e", "f", "gb", "g", "ab", "a", "bb", "b"]


def note_name(midi: int) -> str:
    """Strudel note names: c4 = MIDI 60 (the same convention as ambient-gen)."""
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def share_url(code: str) -> str:
    """strudel.cc's own encoding (code2hash): UTF-8 -> base64 -> URI-encoded, in the hash."""
    b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return "https://strudel.cc/#" + urllib.parse.quote(b64, safe="")


def _num(x: float) -> str:
    return f"{x:.3g}"


# ---------------------------------------------------------------- notes -> mini-notation

def _voices(events: list[tuple[int, int, str]]) -> list[list[tuple[int, int, str]]]:
    """Split (start, length, token) events into voices that don't overlap,
    merging notes that start and end together into one chord token."""
    chords: dict[tuple[int, int], list[str]] = {}
    for start, length, tok in events:
        chords.setdefault((start, length), []).append(tok)
    merged = sorted((s, l, toks[0] if len(toks) == 1 else "[" + ",".join(sorted(set(toks))) + "]")
                    for (s, l), toks in chords.items())
    voices: list[list[tuple[int, int, str]]] = []
    for ev in merged:
        for v in voices:
            if v[-1][0] + v[-1][1] <= ev[0]:
                v.append(ev)
                break
        else:
            voices.append([ev])
    return voices


def _sequence(voice: list[tuple[int, int, str]], total: int) -> str:
    """One voice as a mini-notation sequence of exactly `total` steps."""
    out, pos = [], 0
    for start, length, tok in voice:
        if start > pos:
            out.append("~" if start - pos == 1 else f"~@{start - pos}")
        out.append(tok if length == 1 else f"{tok}@{length}")
        pos = start + length
    if pos < total:
        out.append("~" if total - pos == 1 else f"~@{total - pos}")
    return " ".join(out)


def _block_pattern(events, total: int) -> str:
    voices = _voices(events)
    seqs = [_sequence(v, total) for v in voices]
    return seqs[0] if len(seqs) == 1 else "[" + ", ".join(seqs) + "]"


def _blocks(spans: list[tuple[int, int]], bars: int) -> list[tuple[int, int]]:
    """Cut 0..bars into blocks at bar lines that no note crosses."""
    crossing = [False] * (bars + 1)
    for start, end in spans:
        for b in range(start // STEPS + 1, min(bars, (end - 1) // STEPS + 1)):
            crossing[b] = True
    cuts = [b for b in range(1, bars) if not crossing[b]] + [bars]
    out, prev = [], 0
    for c in cuts:
        out.append((prev, c))
        prev = c
    return out


# ---------------------------------------------------------------- sounds

def _synth_chain(p: dict) -> str:
    """The nearest Strudel sound for an ambient-gen synth patch."""
    mix = {"sawtooth": p["saw"], "square": p["square"], "triangle": p["triangle"], "sine": p["sine"]}
    wave = max(mix, key=mix.get)
    parts = []
    if wave == "sine" and p["fm_index"] > 0:
        parts.append(f's("sine").fm({_num(p["fm_index"])}).fmh({_num(p["fm_ratio"])})')
        if p["fm_index_decay"] > 0:
            parts.append(f'.fmdecay({_num(p["fm_index_decay"] * 0.4)}).fmsustain(0)')
    elif wave == "sawtooth" and p["unison"] >= 3:
        parts.append(f's("supersaw").unison({p["unison"]}).detune({_num(p["detune_cents"] / 100)})')
    else:
        parts.append(f's("{wave}")')
    if p["filter"] == "lowpass":
        parts.append(f'.lpf({round(p["cutoff_hz"])}).lpq({_num(p["resonance"] * 20)})')
        if p["filter_env_octaves"] > 0:
            parts.append(f'.lpenv({_num(p["filter_env_octaves"])}).lpa({_num(p["filter_attack"])})'
                         f'.lpd({_num(p["filter_decay"])}).lps(0)')
    elif p["filter"] == "highpass":
        parts.append(f'.hpf({round(p["cutoff_hz"])}).hpq({_num(p["resonance"] * 20)})')
    elif p["filter"] == "bandpass":
        parts.append(f'.bpf({round(p["cutoff_hz"])}).bpq({_num(1 + p["resonance"] * 10)})')
    parts.append(f'.attack({_num(p["attack"])}).decay({_num(p["decay"])})'
                 f'.sustain({_num(p["sustain"])}).release({_num(p["release"])})')
    if p["vibrato_cents"] > 0:
        parts.append(f'.vib({_num(p["vibrato_hz"])}).vibmod({_num(p["vibrato_cents"] / 100)})')
    if p["drive"] > 0.2:
        parts.append(f'.distort({_num(p["drive"] * 2)})')
    return "".join(parts)


def _drum_bank(S: dict) -> str:
    kit = S.get("groove.kit", "custom")
    style = S.get("groove.style", "lofi")
    if kit in ("909", "techno") or style == "techno" or "909" in S.get("kick.patch", ""):
        return "RolandTR909"
    return "RolandTR808"


def _drum_sample(kind: str, S: dict) -> str:
    if kind == "snare":
        return "cp" if "clap" in S.get("snare.patch", "") else "sd"
    return {"kick": "bd", "hats": "hh", "openhat": "oh", "perc": "rim"}[kind]


# ---------------------------------------------------------------- export

def export(S: dict, notes: list, title: str, patches: dict) -> tuple[str, str]:
    """Return (Strudel code, strudel.cc link) for a composed piece."""
    bpm = float(S["groove.bpm"])
    bar = 240.0 / bpm
    step = lambda t: max(0, round(t / bar * STEPS))
    end = max([n.end for n in notes] + [S["minutes"] * 60])
    bars = max(1, math.ceil(step(end) / STEPS))
    room = S["reverb.level"]
    size = min(10.0, S["reverb.seconds"])
    groove = S["groove.enabled"]
    duck = groove and S.get("groove.duck_depth", 0) > 0

    lines = [
        f"// {title} -- translated from ambient-gen (seed {S['seed']})",
        f"// {S['harmony.key']} {S['harmony.scale'].replace('_', ' ')}, {_num(bpm)} BPM, {bars} bars;"
        " one cycle = one bar of 4/4",
        "// Strudel's synths and effects differ from ambient-gen's, swing/humanize are on the grid,",
        "// and the air/vinyl layers, 808 glides and filter sweeps are left out. Edit away!",
        "",
        f"setcpm({_num(bpm)} / 4)",
        "",
    ]
    consts: dict[str, str] = {}

    def layer_arrangement(prefix: str, events: list[tuple[int, int, str]], spans) -> str:
        """arrange() of a layer's blocks; repeated blocks share a constant."""
        sections = []
        for b0, b1 in _blocks(spans, bars):
            lo, hi = b0 * STEPS, b1 * STEPS
            inside = [(s - lo, min(l, hi - s), t) for s, l, t in events if lo <= s < hi]
            k = b1 - b0
            if not inside:
                pat = "silence"
            else:
                mini = _block_pattern(inside, k * STEPS)
                pat = f'"{mini}"' + (f".slow({k})" if k > 1 else "")
            if pat != "silence":
                key = f"{prefix}:{pat}"
                if key not in consts:
                    consts[key] = f"{prefix}{sum(1 for c in consts if c.startswith(prefix + ':'))}"
                    lines.append(f"const {consts[key]} = {pat}")
                pat = consts[key]
            if sections and sections[-1][1] == pat:
                sections[-1][0] += k
            else:
                sections.append([k, pat])
        return "arrange(" + ", ".join(f"[{k}, {p}]" for k, p in sections) + ")"

    layers = []
    for layer in MELODIC:
        ln = [n for n in notes if n.kind == layer]
        if not ln:
            continue
        events, spans = [], []
        for n in ln:
            if (n.release - n.start) / bar * STEPS < 0.5:
                continue  # a rounding sliver (shorter than half a grid step): it would only click
            s0, s1 = step(n.start), max(step(n.start) + 1, step(n.release))
            events.append((s0, s1 - s0, note_name(int(n.midi))))
            spans.append((s0, s1))
        arr = layer_arrangement(layer, events, spans)
        p = patches[layer]
        sound = _synth_chain(p)                       # 's("...")...' -> chained as .s("...")
        chain = (f"\n  .{sound}"
                 f"\n  .gain({_num(S[f'{layer}.level'] * (0.7 if layer == 'pad' else 1.0))})"
                 f".room({_num(room * S[f'{layer}.reverb_send'])}).size({_num(size)})")
        if p["width"] > 0:
            chain += f".pan(sine.slow(8).range({_num(0.5 - p['width'] / 2.5)}, {_num(0.5 + p['width'] / 2.5)}))"
        if duck:
            chain += ".orbit(2)"
        layers.append(f"// {layer}: {S[f'{layer}.patch']}\n$: note({arr}){chain}")

    bank = _drum_bank(S)
    for kind in DRUMS:
        dn = [n for n in notes if n.kind == kind]
        if not dn:
            continue
        sample = _drum_sample(kind, S)
        events = sorted({(step(n.start), 1, sample) for n in dn})
        arr = layer_arrangement(kind, list(events), [(s, s + 1) for s, _, _ in events])
        chain = f'.bank("{bank}").gain({_num(S[f"{kind}.level"] * 1.2)})'
        if S[f"{kind}.reverb_send"] > 0.05:
            chain += f".room({_num(room * S[f'{kind}.reverb_send'])})"
        if kind == "kick" and duck:
            chain += f".duckorbit(2).duckdepth({_num(S['groove.duck_depth'])}).duckattack({_num(S['groove.duck_release'])})"
        layers.append(f"// {kind}: {sample} from the {bank} bank\n$: s({arr}){chain}")

    if consts:
        lines.append("")
    lines += [l + "\n" for l in layers]
    code = "\n".join(lines).rstrip() + "\n"
    return code, share_url(code)
