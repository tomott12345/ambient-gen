"""
midi_out.py -- send a composition to a hardware synth over MIDI, or save it
as a standard MIDI file. Built for the M-VAVE FM-1 on stock firmware, which
plays one sound at a time on a single channel with 12 voices, but it works
with any MIDI instrument.

    python3 ambient.py --list-midi-ports
    python3 ambient.py --preset presets/techno.json --midi-out FM-1 --midi-layers lead
    python3 ambient.py --preset presets/night.json --export-midi night.mid --no-audio

The composer already plans every note (layer, pitch, start, release,
velocity) before any audio exists. This module turns those notes into MIDI:
note-on at the start and note-off at the release, so the synth's own
envelope plays the fade-out.

Needs:  pip3 install mido python-rtmidi
"""

from __future__ import annotations

import subprocess
import sys
import time

DRUM_LAYERS = ("kick", "snare", "hats", "openhat", "perc")
# General MIDI drum notes, used for drum layers (on channel 10 in exported files)
GM_DRUMS = {"kick": 36, "snare": 38, "hats": 42, "openhat": 46, "perc": 37}
# channel (1-16) of each layer in an exported .mid file
EXPORT_CHANNELS = {"pad": 1, "bass": 2, "bells": 3, "lead": 4, **{d: 10 for d in DRUM_LAYERS}}
DRUM_GATE = 0.05  # seconds a drum note is held (drums ignore note length anyway)


def _mido():
    try:
        import mido
        return mido
    except ImportError:
        raise SystemExit("MIDI needs two packages:  pip3 install mido python-rtmidi")


def list_ports() -> list[str]:
    return _mido().get_output_names()


def find_port(name: str) -> str:
    """The first output port whose name contains `name` (any case), e.g. "fm-1"."""
    ports = list_ports()
    for p in ports:
        if name.lower() in p.lower():
            return p
    found = "\n  ".join(ports) if ports else "(none: is the synth plugged in and switched on?)"
    raise SystemExit(f"no MIDI output matching {name!r}. Available:\n  {found}")


def note_events(notes, channel_for: dict, voices: int | None = None):
    """Turn Notes into a time-sorted list of (seconds, "on"/"off", channel, pitch, velocity).

    - Only layers in `channel_for` are included; drum layers get GM drum notes.
    - Overlapping notes of the same pitch on one channel are cut where the
      next one starts, so a note-off never silences the newer note.
    - With `voices`, at most that many notes sound at once per channel: when
      a new note would exceed it, the oldest held note is released (voice
      stealing, as on the synth itself, but chosen here so nothing hangs)."""
    picked = []
    for n in notes:
        if n.kind not in channel_for:
            continue
        drum = n.kind in DRUM_LAYERS
        pitch = GM_DRUMS[n.kind] if drum else int(n.midi)
        off = n.start + DRUM_GATE if drum else max(n.release, n.start + 0.01)
        vel = max(1, min(127, round(n.vel * 127)))
        picked.append([n.start, off, channel_for[n.kind], pitch, vel])
    picked.sort(key=lambda x: x[0])

    last_by_pitch = {}
    for p in picked:  # same pitch, same channel: end the older one when the new one starts
        key = (p[2], p[3])
        prev = last_by_pitch.get(key)
        if prev is not None and prev[1] > p[0]:
            prev[1] = p[0]
        last_by_pitch[key] = p

    if voices:
        held: dict[int, list] = {}
        for p in picked:
            live = [q for q in held.get(p[2], []) if q[1] > p[0]]
            while len(live) >= voices:
                oldest = live.pop(0)
                oldest[1] = p[0]
            live.append(p)
            held[p[2]] = live
    # a note cut or stolen the moment it started never sounds: drop it rather than send a zero-length note
    picked = [p for p in picked if p[1] > p[0]]

    events = []
    for start, off, ch, pitch, vel in picked:
        events.append((start, "on", ch, pitch, vel))
        events.append((off, "off", ch, pitch, 0))
    # at the same moment, note-offs go first so a re-struck note isn't cut off
    events.sort(key=lambda e: (e[0], e[1] == "on"))
    return events


def collapse_flams(notes, window: float = 0.04):
    """Drum hits of the same kind closer together than `window` seconds (the
    techno clap's three quick bursts) become one hit: the loudest. That suits
    a sample-based drum machine, where retriggering a clap sample three times
    in 20 ms sounds smeared rather than like a clap."""
    keep, by_kind = [], {}
    for n in notes:
        (by_kind.setdefault(n.kind, []) if n.kind in DRUM_LAYERS else keep).append(n)
    for hits in by_kind.values():
        hits.sort(key=lambda n: n.start)
        cluster = [hits[0]] if hits else []
        for n in hits[1:]:
            if n.start - cluster[-1].start < window:
                cluster.append(n)
            else:
                keep.append(max(cluster, key=lambda h: h.vel))
                cluster = [n]
        if cluster:
            keep.append(max(cluster, key=lambda h: h.vel))
    return keep


def export(notes, path, bpm: float, layers=None, merge_drums: bool = False):
    """Write a type-1 .mid file: a tempo track, then one track per layer.

    merge_drums puts every drum layer on one "drums" track (channel 10), the
    way a drum machine or MPC drum program expects: one track, a pad per
    sound (GM notes: kick 36, snare/clap 38, rim 37, closed hat 42, open hat 46)."""
    mido = _mido()
    notes = collapse_flams(notes)
    present = sorted({n.kind for n in notes}, key=list(EXPORT_CHANNELS).index)
    layers = [l for l in present if layers is None or l in layers]
    groups = [(l, [l]) for l in layers if not (merge_drums and l in DRUM_LAYERS)]
    drums = [l for l in layers if l in DRUM_LAYERS]
    if merge_drums and drums:
        groups.append(("drums", drums))
    ppq = 480
    mid = mido.MidiFile(type=1, ticks_per_beat=ppq)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="ambient-gen", time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mid.tracks.append(meta)
    for name, members in groups:
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        now = 0
        for t, kind, ch, pitch, vel in note_events(notes, {l: EXPORT_CHANNELS[l] for l in members}):
            tick = round(t * bpm / 60 * ppq)
            msg = "note_on" if kind == "on" else "note_off"
            track.append(mido.Message(msg, channel=ch - 1, note=pitch, velocity=vel, time=max(0, tick - now)))
            now = max(now, tick)
        mid.tracks.append(track)
    mid.save(path)
    return [name for name, _ in groups]


def play(notes, port_name: str, layers: list[str], channel: int, bpm: float, voices: int | None = 12,
         clock: bool = False, backing_wav=None, offset_ms: float = 0.0, verbose: bool = True):
    """Play the chosen layers live on a synth, all on one channel (the FM-1's
    stock firmware listens on a single channel).

    clock: also send MIDI clock (24 per beat) plus start/stop, so the
           synth's arpeggiator or sequencer follows the song's tempo.
    backing_wav: a WAV to play through the Mac's speakers at the same time
           (with `afplay`). offset_ms shifts the MIDI later (positive) or
           earlier (negative) against it, to line the two up by ear."""
    mido = _mido()
    events = note_events(notes, {l: channel for l in layers}, voices)
    if not events:
        raise SystemExit(f"nothing to play: no notes in layers {', '.join(layers)}")
    end = max(e[0] for e in events)
    tick = 60 / bpm / 24
    port = mido.open_output(port_name)
    player = None
    sounding = set()
    try:
        if backing_wav:
            player = subprocess.Popen(["afplay", str(backing_wav)])
        t0 = time.perf_counter() + 0.2 + offset_ms / 1000  # a moment for afplay to start
        if clock:
            _wait(t0)
            port.send(mido.Message("start"))
        i, next_tick, next_report = 0, 0.0, 0.0
        while i < len(events):
            t_event = events[i][0]
            if clock and next_tick <= t_event:
                _wait(t0 + next_tick)
                port.send(mido.Message("clock"))
                next_tick += tick
                continue
            _wait(t0 + t_event)
            _, kind, ch, pitch, vel = events[i]
            if kind == "on":
                port.send(mido.Message("note_on", channel=ch - 1, note=pitch, velocity=vel))
                sounding.add((ch, pitch))
            else:
                port.send(mido.Message("note_off", channel=ch - 1, note=pitch, velocity=0))
                sounding.discard((ch, pitch))
            i += 1
            if verbose and t_event >= next_report:
                print(f"\r  MIDI {t_event / 60:5.1f} of {end / 60:.1f} min", end="", file=sys.stderr)
                next_report = t_event + 1.0
        if player:
            player.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for ch, pitch in sounding:  # nothing left hanging
            port.send(mido.Message("note_off", channel=ch - 1, note=pitch, velocity=0))
        port.send(mido.Message("control_change", channel=channel - 1, control=123, value=0))  # all notes off
        if clock:
            port.send(mido.Message("stop"))
        port.close()
        if player and player.poll() is None:
            player.terminate()
        if verbose:
            print(file=sys.stderr)


def _wait(target: float):
    """Sleep until perf_counter() reaches target: coarse sleep, then a short spin for accuracy."""
    while True:
        left = target - time.perf_counter()
        if left <= 0:
            return
        time.sleep(left - 0.002 if left > 0.003 else 0)
