"""
mpc_export.py -- write a composition as an Akai MPC project (MPC 2 / MPC
Beats, file version 2.1), ready to open and play:

    <Name>.xpj                      the project (tempo, key, file list)
    <Name>_[ProjectData]/
        All Sequences & Songs.xal   the sequence list
        1.sxq                       the sequence: one MIDI track per layer
        <Name> Drums.Drum.xpm       a drum program: ambient-gen's own drum sounds on pads
        <Name> Pad.Keygroup.xpm     keygroup programs: each melodic layer sampled from
        <Name> Bass.Keygroup.xpm      ambient-gen's synth across its range (looped when
        ...                           the sound sustains)
        *.WAV                       the samples
        Audio 001.Audio.xpm, Project Settings.xml, Midi Controller.mcn

The MPC format isn't documented; it was read from projects saved by MPC 2.14
(templates in mpc_templates/). What we learned:

  .sxq  a standard MIDI file (960 ticks per beat) whose content is in
        Akai sequencer-specific events ("Ga" + type):
          Ga 0a / Ga 00   sequence header; Ga 00 holds the length in bars
          Ga 10           a track header: 47 61 10 02 <track> <program type>
                          ... <name length> <program name>  (type 0 drum, 1 keygroup)
          Ga 11           a note: 47 61 11 02 <note> <velocity> 00 00 00 40
                          <length, 7-bit little-endian, 3 bytes> 00 00 00 00 00 00 64
          Ga 13           the project's program list, one per extra track
  .xpm  XML programs; keygroup sample RootNote is the MIDI note + 1.

What carries over: every note, the arrangement, tempo, key, and each layer's
sound (sampled). What doesn't: the reverb and master effects, per-note filter
movement (the acid sweep), 808 glides, and the air/vinyl layers -- add MPC
effects to taste.
"""

from __future__ import annotations

import copy
import json
import math
import shutil
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

import synth

TEMPLATES = Path(__file__).parent / "mpc_templates"
PPQ = 960
MAX_BARS = 999                      # an MPC sequence's limit
MELODIC = ("pad", "bass", "bells", "lead")
DRUMS = ("kick", "snare", "hats", "openhat", "perc")
GM_NOTE = {"kick": 36, "snare": 38, "hats": 42, "openhat": 46, "perc": 37}
TRACK_LABEL = {"pad": "Pad", "bass": "Bass", "bells": "Bells", "lead": "Lead", "drums": "Drums"}
PROGRAM_TYPE = {"Drum": 0, "Keygroup": 1}
NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


# ---------------------------------------------------------------- sequence (.sxq)

def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def _seqspec(data: bytes) -> bytes:
    return _meta(0x7F, data)


def _track(events: list[tuple[int, bytes]]) -> bytes:
    """events: (absolute tick, event bytes) -> an MTrk chunk."""
    body, last = b"", 0
    for tick, ev in sorted(events, key=lambda e: e[0]):
        body += _vlq(tick - last) + ev
        last = tick
    body += _vlq(0) + _meta(0x2F, b"")
    return b"MTrk" + struct.pack(">I", len(body)) + body


def _seven(n: int, count: int) -> bytes:
    """n as `count` 7-bit bytes, least significant first (Akai's packing)."""
    return bytes((n >> (7 * i)) & 0x7F for i in range(count))


def _key_signature(key: str, scale: str) -> bytes:
    """MIDI key signature (sharps/flats, major/minor) for a key + scale."""
    pc = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"].index(key)
    minor = scale in ("minor", "dorian", "phrygian", "minor_pentatonic", "hirajoshi")
    major_pc = (pc + 3) % 12 if minor else pc               # relative major
    sharps = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 5: -1, 10: -2, 3: -3, 8: -4, 1: -5}[major_pc]
    return bytes([sharps & 0xFF, 1 if minor else 0])


def write_sequence(path: Path, tracks: list[dict], programs: list[str], bpm: float, bars: int,
                   seq_name: str, key: str, scale: str):
    """tracks: [{name, program, type, notes: [(tick, note, velocity, length_ticks)]}]
    programs: every program in the project (its pool)."""
    tb = json.loads((TEMPLATES / "sequence_bytes.json").read_text())
    num, den, cpc, n32 = tb["time_signature"]
    header = (bytes.fromhex("47610000") + _seven(bars, 2) + b"\x01\x00" + _seven(bars + 1, 2)
              + b"\x01\x00\x00\x00\x00")
    t0 = _track([
        (0, _seqspec(bytes.fromhex(tb["seq_header"]))),
        (0, _meta(0x03, seq_name.encode()[:60] + b"\x00")),
        (0, _meta(0x59, _key_signature(key, scale))),
        (0, _meta(0x51, struct.pack(">I", round(60_000_000 / bpm))[1:])),
        (0, _seqspec(header)),
        (0, _meta(0x58, bytes([num, int(math.log2(den)), cpc, n32]))),
        (0, _seqspec(bytes.fromhex(tb["seq_flags"]))),
    ])
    chunks = [t0]
    prefix = bytearray.fromhex(tb["track_header_prefix"])
    for i, tr in enumerate(tracks):
        head = bytearray(prefix)
        head[4], head[5] = i, PROGRAM_TYPE[tr["type"]]      # track index, program type
        pname = tr["program"].encode()
        ga15 = bytearray.fromhex(tb["track_ga15"])
        ga15[3] = i
        events = [  # (MPC adds a "pytT" record only to plugin tracks; ours are drum/keygroup)
            (0, _meta(0x03, tr["name"].encode()[:40] + b"\x00")),
            (0, _seqspec(bytes(head) + bytes([len(pname)]) + pname)),
            (0, _seqspec(bytes(ga15))),
            (0, _meta(0x70, b"\x00")),
        ]
        for tick, note, vel, length in tr["notes"]:
            body = (b"Ga\x11\x02" + bytes([note & 0x7F, max(1, min(127, vel))]) + b"\x00\x00\x00\x40"
                    + _seven(max(1, min(length, (1 << 21) - 1)), 3) + b"\x00" * 6 + b"\x64")
            events.append((tick, _seqspec(body)))
        chunks.append(_track(events))
    for name in programs:                                    # the program pool
        b = name.encode()
        chunks.append(_track([(0, _seqspec(b"Ga\x13" + bytes([len(b) + 2, 0]) + b + b"\x00\x00"))]))
    path.write_bytes(b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), PPQ) + b"".join(chunks))


# ---------------------------------------------------------------- programs (.xpm)

def _program(kind: str, name: str):
    """A blank program from the template, with its single blank instrument
    cloned to 128."""
    tree = ET.parse(TEMPLATES / f"{kind.lower()}.xpm")
    prog = tree.getroot().find("Program")
    prog.find("ProgramName").text = name
    insts = prog.find("Instruments")
    blank = insts.find("Instrument")
    insts.remove(blank)
    for n in range(1, 129):
        inst = copy.deepcopy(blank)
        inst.set("number", str(n))
        insts.append(inst)
    return tree, prog


def _set(el, tag: str, value):
    child = el.find(tag)
    if child is not None:
        child.text = value if isinstance(value, str) else (f"{value:.6f}" if isinstance(value, float) else str(value))


def _layer(inst, sample: str, frames: int, root: int = 0, loop_start: int | None = None, crossfade: int = 0):
    layer = inst.find("Layers").find("Layer")
    _set(layer, "Active", "True")
    _set(layer, "SampleName", sample)
    _set(layer, "SampleFile", "")
    _set(layer, "RootNote", root)
    _set(layer, "SliceStart", 0)
    _set(layer, "SliceEnd", frames)
    _set(layer, "SliceLoop", 1 if loop_start is not None else 0)
    _set(layer, "SliceLoopStart", loop_start or 0)
    _set(layer, "SliceLoopCrossFadeLength", crossfade)
    _set(layer, "VelStart", 0)
    _set(layer, "VelEnd", 127)


def _write_program(tree, path: Path):
    ET.indent(tree, space="  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


# ---------------------------------------------------------------- samples

def _write_wav(path: Path, x: np.ndarray, sr: int):
    """24-bit stereo WAV (x: (2, n), -1..1)."""
    x = np.clip(x, -1, 1)
    ints = np.ascontiguousarray(np.round(x.T * 8388607)).astype("<i4")
    data = ints.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, sr, sr * 6, 6, 24))
        f.write(b"data" + struct.pack("<I", len(data)) + data)


def _render_note(make_note, kind: str, midi: int, hold: float, params: dict, sr: int, level: float):
    """One note of a layer's synth patch, held for `hold` seconds then released."""
    note = make_note(kind, 0.0, hold, hold + params["release"], midi)
    n = int((hold + params["release"]) * sr) + 1
    age = np.arange(n) / sr
    left, right = synth.render(note, age, params, sr)
    return np.stack([left, right]) * level


def _trim_tail(x: np.ndarray, sr: int, floor_db: float = -70) -> np.ndarray:
    """Cut silence after a sound has died away (keeps samples small)."""
    env = np.abs(x).max(axis=0)
    above = np.nonzero(env > 10 ** (floor_db / 20))[0]
    end = min(x.shape[1], (above[-1] + int(0.02 * sr)) if len(above) else int(0.05 * sr))
    return x[:, :end]


# ---------------------------------------------------------------- export

def export(S: dict, notes: list, out_dir: Path, title: str, make_note, patches: dict,
           log=print) -> Path:
    """Write the MPC project for a composed piece into out_dir/<title>/ and
    return the .xpj path. S: resolved settings, notes: ambient.compose()
    output, patches: {layer: synth params}, make_note: ambient.make_note."""
    sr = S["sample_rate"]
    bpm = float(S["groove.bpm"])
    name = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:40] or "ambient-gen"
    root = out_dir / name
    data = root / f"{name}_[ProjectData]"
    if root.exists():
        shutil.rmtree(root)
    data.mkdir(parents=True)

    tick = lambda t: round(t * bpm / 60 * PPQ)
    end_tick = max([tick(n.end) for n in notes] + [tick(S["minutes"] * 60)])
    bars = min(MAX_BARS, max(1, math.ceil(end_tick / (4 * PPQ))))
    if end_tick > MAX_BARS * 4 * PPQ:
        log(f"mpc: the piece is longer than an MPC sequence ({MAX_BARS} bars at {bpm:g} BPM); "
            f"notes after bar {MAX_BARS} are left out")

    tracks, programs, files = [], [], []

    # drums: one program, each sound a one-shot on the pad its GM note plays
    drum_notes = [n for n in notes if n.kind in DRUMS]
    if drum_notes:
        pname = f"{name} Drums"
        tree, prog = _program("Drum", pname)
        pad_of = {int(p.findtext("Note")): int(p.get("number")) for p in prog.find("PadNoteMap").findall("PadNote")}
        insts = prog.find("Instruments").findall("Instrument")
        for kind in DRUMS:
            if not any(n.kind == kind for n in drum_notes):
                continue
            p = patches[kind]
            x = _render_note(make_note, kind, S[f"{kind}.midi"], p["decay"], p, sr, S[f"{kind}.level"])
            x = _trim_tail(x, sr)
            sample = f"{name} {kind.title()}"
            _write_wav(data / f"{sample}.WAV", x, sr)
            inst = insts[pad_of[GM_NOTE[kind]] - 1]
            _layer(inst, sample, x.shape[1])
        _write_program(tree, data / f"{pname}.Drum.xpm")
        programs.append(pname)
        files.append(f"{pname}.Drum.xpm")
        sixteenth = PPQ // 4
        tracks.append({"name": "Drums", "program": pname, "type": "Drum",
                       "notes": [(tick(n.start), GM_NOTE[n.kind], round(n.vel * 127), sixteenth) for n in drum_notes]})

    # melodic layers: keygroup programs sampled every 3 semitones over the notes used
    for layer in MELODIC:
        lnotes = [n for n in notes if n.kind == layer]
        if not lnotes:
            continue
        p = patches[layer]
        pname = f"{name} {TRACK_LABEL[layer]}"
        tree, prog = _program("Keygroup", pname)
        insts = prog.find("Instruments").findall("Instrument")
        lo, hi = min(int(n.midi) for n in lnotes), max(int(n.midi) for n in lnotes)
        roots = list(range(lo + 1, hi + 2, 3)) or [lo]
        sustains = p["sustain"] > 0.05
        # a held sound gets a few seconds and a loop; a struck one rings out in full
        hold = min(8.0, max(2.5, p["attack"] + 2.0)) if sustains else max(p["decay"], 0.3)
        level = S[f"{layer}.level"] * (0.6 if layer == "pad" else 1.0)  # a pad's chord notes stack up
        for k, r in enumerate(roots):
            x = _render_note(make_note, layer, r, hold, p, sr, level)
            if sustains:
                x = x[:, : int(hold * sr)]            # the held part; the MPC's envelope does the release
            else:
                x = _trim_tail(x, sr)
            sample = f"{name} {TRACK_LABEL[layer]} {NOTE_NAMES[r % 12]}{r // 12 - 1}"
            _write_wav(data / f"{sample}.WAV", x, sr)
            inst = insts[k]
            _set(inst, "LowNote", 0 if k == 0 else r - 1)
            _set(inst, "HighNote", 127 if k == len(roots) - 1 else r + 1)
            if sustains:
                start = int(max(p["attack"] + 0.3, hold * 0.45) * sr)
                start = min(start, x.shape[1] - int(0.5 * sr))
                _layer(inst, sample, x.shape[1], root=r + 1, loop_start=start, crossfade=int(0.25 * sr))
                _set(inst, "VolumeAttack", 0.0)
                _set(inst, "VolumeSustain", 1.0)
                # the MPC's release knob (0..1) for the patch's release time; roughly sqrt-shaped
                _set(inst, "VolumeRelease", float(min(1.0, math.sqrt(p["release"] / 20))))
            else:
                _layer(inst, sample, x.shape[1], root=r + 1)
                _set(inst, "VolumeSustain", 1.0)
                _set(inst, "VolumeRelease", float(min(1.0, math.sqrt(max(p["release"], 0.1) / 20))))
            _set(inst, "TriggerMode", 2)
        _set(prog, "KeygroupNumKeygroups", len(roots))
        _write_program(tree, data / f"{pname}.Keygroup.xpm")
        programs.append(pname)
        files.append(f"{pname}.Keygroup.xpm")
        tracks.append({"name": TRACK_LABEL[layer], "program": pname, "type": "Keygroup",
                       "notes": [(tick(n.start), int(n.midi), round(n.vel * 127),
                                  max(1, tick(n.release) - tick(n.start))) for n in lnotes]})

    # sequence, sequence list, and the project
    key, scale = S["harmony.key"], S["harmony.scale"]
    seq_name = name[:24]
    write_sequence(data / "1.sxq", tracks, programs + ["Audio 001"], bpm, bars, seq_name, key, scale)
    (data / "All Sequences & Songs.xal").write_text(
        (TEMPLATES / "sequences.xal").read_text().replace("{{SEQ_NAME}}", _xml(seq_name)))
    for src, dst in [("audio.xpm", "Audio 001.Audio.xpm"), ("project_settings.xml", "Project Settings.xml"),
                     ("midi_controller.mcn", "Midi Controller.mcn")]:
        shutil.copy(TEMPLATES / src, data / dst)
    listed = ["All Sequences &amp; Songs.xal"] + [_xml(f) for f in files] + \
             ["Audio 001.Audio.xpm", "Midi Controller.mcn", "Project Settings.xml"]
    filelist = "".join(f"    <File>./{_xml(name)}_[ProjectData]/{f}</File>\n" for f in listed)
    mpc_key = f"{key.replace('#', '#')} {'Minor' if _key_signature(key, scale)[1] else 'Major'}"
    xpj = (TEMPLATES / "project.xpj").read_text()
    for k, v in {"{{FILELIST}}": filelist, "{{BPM}}": f"{bpm:.6f}", "{{KEY}}": mpc_key, "{{TITLE}}": _xml(title),
                 "{{GENRE}}": "ambient-gen",
                 "{{NOTES}}": _xml(f"Made with ambient-gen: seed {S['seed']}, {key} {scale.replace('_', ' ')}")}.items():
        xpj = xpj.replace(k, v)
    path = root / f"{name}.xpj"
    path.write_text(xpj)
    log(f"mpc: {path} ({len(tracks)} tracks, {bars} bars, {len(list(data.glob('*.WAV')))} samples)")
    return path


def zip_project(xpj: Path) -> Path:
    """The project folder as one zip (stored, not deflated: it's mostly WAV)."""
    import zipfile
    root = xpj.parent
    out = root.parent / f"{root.name} (MPC project).zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for f in sorted(root.rglob("*")):
            if f.is_file():
                z.write(f, Path(root.name) / f.relative_to(root))
    return out


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
