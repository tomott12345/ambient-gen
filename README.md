# ambient-gen

Generative ambient music in Python, rendered straight to WAV files of any
length, from a 2-minute sketch to an 8-hour sleep track. Turn on the groove
and it also makes lo-fi / trap beats (drums, 808 slides, hi-hat rolls, vinyl
crackle) or long-form techno for hour-long jams (four-on-the-floor, acid
lines, builds and drops).

Every run composes a new piece: chords drift through a scale, melodies
wander, the filters breathe. Every piece can be reproduced exactly from its
seed. The sounds come from a small, tunable synth engine, and every setting
can be changed from presets, the command line, or (eventually) a Go web
front end.

```bash
pip3 install -r requirements.txt
python3 ambient.py --preset presets/dark_ambient.json --minutes 6
```

---

## Contents

- [Quick start](#quick-start)
- [Presets](#presets)
- [How it works](#how-it-works)
- [Beats: the groove mode](#beats-the-groove-mode)
- [Techno: long tracks and jams](#techno-long-tracks-and-jams)
- [Playing a hardware synth (MIDI)](#playing-a-hardware-synth-midi)
- [The synth engine](#the-synth-engine)
- [Patches](#patches)
- [Changing settings](#changing-settings)
- [Writing your own presets and patches](#writing-your-own-presets-and-patches)
- [Reproducibility](#reproducibility)
- [Long renders and file formats](#long-renders-and-file-formats)
- [Driving it from a front end](#driving-it-from-a-front-end)
- [Reference: piece, layer and effect settings](#reference-piece-layer-and-effect-settings)
- [Reference: synth settings](#reference-synth-settings)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)

---

## Quick start

You need Python 3.10+ with numpy, scipy and numba (plus mido and
python-rtmidi if you want MIDI):

```bash
pip3 install -r requirements.txt
```

Then:

```bash
python3 ambient.py                                   # 30 minutes with the default sound
python3 ambient.py --minutes 6                       # 6 minutes
python3 ambient.py --preset presets/night.json       # a preset
python3 ambient.py --preset presets/sleep.json --minutes 480      # an 8-hour sleep track
python3 ambient.py --minutes 10 --seed 1234          # a specific, repeatable piece
python3 ambient.py --set bells.enabled=false         # change any single setting
python3 ambient.py --set pad.patch=warm_strings --set pad.synth.cutoff_hz=500
python3 ambient.py --preset presets/lofi_trap.json --minutes 30      # lo-fi trap beats
python3 ambient.py --list-presets                    # every preset, as JSON
python3 ambient.py --list-patches                    # every synth patch, with descriptions
python3 ambient.py --list-params                     # every setting, as JSON
```

Each render writes two files to `renders/`:

| File | What it is |
|---|---|
| `ambient_<preset>_<seed>.wav` | The audio: 24-bit / 48 kHz stereo by default |
| `ambient_<preset>_<seed>.json` | The exact settings and seed used, plus render stats (peak level, speed, note counts) |

Use `--out path/to/file.wav` to choose the output name.

While it renders you'll see the key, scale, seed and note counts, a progress
line, and finally the peak level and render speed:

```
6 min of A# major, seed 1433086701: 12 chords, 8 bell notes -> renders/ambient_tape_memory_1433086701.wav
  rendering 100.0%  (6.0 of 6.0 min)
done: 6.0 min in 30.8 s (11.7x realtime), peak -4.69 dBFS
```

---

## Presets

A preset is a small JSON file that sets the mood: scale, tempo of change,
which patches play, and how big the room is. Presets live in `presets/`.

| Preset | Key / scale | Mood | Layers |
|---|---|---|---|
| `night` | A minor pentatonic | Soft and dark: muted pad, sparse felt piano, long gentle reverb | pad: `soft_pad`, bass: `round_bass`, bells: `felt_piano`, air |
| `dark_ambient` | C phrygian | Ominous: growling resonant drone, low church bells, 16 s cave reverb | pad: `abyss_drone`, bass: `round_bass`, bells: `church_bell`, air |
| `deep_drone` | C# phrygian | Very slow, low chords and nothing else; meditative | pad: `soft_pad`, bass: `round_bass`, air |
| `sleep` | D major pentatonic | For 8+ hour renders: chords held 1.5–3 min, very sparse, quiet, 2-min fades | pad: `warm_strings`, bass: `round_bass`, bells: `felt_piano`, air |
| `rain_window` | F minor | Grey and cozy: rain-like hiss, small room, soft piano | pad: `soft_pad`, bass: `round_bass`, bells: `felt_piano`, air |
| `tape_memory` | A# major | Nostalgic lo-fi: warped tape strings with wow, drift and hiss | pad: `tape_strings`, bass: `round_bass`, bells: `felt_piano`, air |
| `underwater` | G dorian | Submerged: sweeping resonant sub pad, hollow drops, heavily muffled | pad: `sub_swell`, bells: `hollow_drop`, air |
| `cathedral` | G major | Sacred: drawbar organ, low bells, 18 s hall | pad: `organ`, bass: `round_bass`, bells: `church_bell` |
| `koto_garden` | E hirajoshi | Sparse and Japanese: hirajoshi plucks over a faint pad | pad: `soft_pad`, bells: `koto_pluck`, air |
| `glass` | E lydian | Bright and busy: lydian supersaw, crystalline FM bells | pad: `shimmer_pad`, bells: `glass_crystal`, air |
| `aurora` | B lydian | Wide and shimmering: lydian pad, glass bells, 15 s reverb | pad: `shimmer_pad`, bass: `round_bass`, bells: `glass_crystal`, air |
| `lofi_trap` | F dorian | Beat: 76 BPM, worn e-piano chords, 808 slides, hi-hat rolls, vinyl, sampler grit | pad: `lofi_keys`, bass: `sub_808`, bells: `felt_piano`, drums: `kick_punch`, `snare_lofi`, `hat_closed`, vinyl |
| `lofi_chill` | D minor | Beat: 70 BPM, swung and laid back, tape strings, soft kick, no rolls, more vinyl | pad: `tape_strings`, bass: `sub_808`, bells: `lofi_keys`, drums: `kick_soft`, `snare_lofi`, `hat_closed`, vinyl |
| `lofi_trap_ensemble` | D# dorian | Beat: lofi_trap in D# dorian with strings, soft piano and a flute lead | pad: `warm_strings`, bass: `sub_808`, bells: `soft_piano`, lead: `flute`, drums: `kick_punch`, `snare_lofi`, `hat_closed`, vinyl |
| `techno` | A minor | Techno: 130 BPM, 909 kit, acid line with drifting filter, offbeat/rolling bass, builds and drops | pad: `techno_pad`, bass: `techno_bass`, bells: `dub_stab`, lead: `acid_303`, drums: `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909` |
| `dub_techno` | F minor | Techno: 122 BPM, deep chord stabs into long reverb, offbeat bass, vinyl, no acid | pad: `techno_pad`, bass: `techno_bass`, bells: `dub_stab`, drums: `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909`, air, vinyl |
| `techno_punchy` | A minor | Techno: 132 BPM, driven tight kick, deeper pump, busier and louder | pad: `techno_pad`, bass: `techno_bass`, bells: `dub_stab`, lead: `acid_303`, drums: `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909` |
| `minimal_techno` | G minor | Techno: 125 BPM, sparse and dry, rolling bass, percussion-led, no acid | pad: `techno_pad`, bass: `techno_bass`, bells: `dub_stab`, drums: `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909` |
| `acid_techno` | F# minor | Techno: 135 BPM, busy squelchy 303 line with slides and wide filter sweeps | pad: `techno_pad`, bass: `techno_bass`, bells: `dub_stab`, lead: `acid_303`, drums: `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909` |
| `hypnotic_techno` | D minor | Techno: 128 BPM, 32-bar phrases, one chord at a time, deep and slow-changing | pad: `techno_pad`, bass: `techno_bass`, bells: `dub_stab`, lead: `acid_303`, drums: `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909` |
| `industrial_techno` | E phrygian | Techno: 140 BPM, overdriven kick and clap, heavy saturation, bit-crush | pad: `techno_pad`, bass: `techno_bass`, bells: `dub_stab`, lead: `acid_303`, drums: `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909` |
| `melodic_techno` | F# minor | Techno: 124 BPM, plucked arpeggio sequence over shimmering pads and stabs | pad: `shimmer_pad`, bass: `techno_bass`, bells: `dub_stab`, lead: `koto_pluck`, drums: `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909` |

```bash
python3 ambient.py --preset presets/aurora.json --minutes 20
python3 ambient.py --preset presets/dark_ambient.json --minutes 60 --set bells.per_minute=0.5
```

Any setting can still be overridden on top of a preset with `--set`.

---

## How it works

`ambient.py` reads top to bottom in the same order the music is made:

```
 settings ──► COMPOSE ──────────────► RENDER (10-second blocks) ──────────────► WAV
 (defaults,   chords + bass + bells   for each block:
  patches,    as a list of notes        synth.render() every sounding note
  preset,     (a few thousand notes     + air noise
  --set)       even for hours)          → reverb → warmth filter → fades → soft limiter
                                        → write block to disk
```

### 1. Composing

The whole piece is planned up front, before any audio is made. That step is
cheap: hours of music come to a few thousand note events.

- **Harmony.** Chords are built by stacking every other note of the scale
  (1-3-5-7-9) on a scale degree. The next chord's root is picked by a weighted
  random walk (`CHORD_MOVES`) that favours moves of a 4th or 5th, which sound
  "inevitable", over 1-step moves, which sound like drifting. Each chord lasts
  a random time between `harmony.chord_seconds_min` and `..._max`.
- **Voicing.** Chord notes are placed inside one octave so consecutive chords
  move smoothly. With `pad.open_voicing`, every other note is lifted an octave
  for a wider, more open sound.
- **Key changes.** Every `harmony.modulate_minutes`, the key moves up a 4th or
  a 5th, the smoothest kind of key change, and the progression restarts on the
  new tonic.
- **Bass.** The bass plays the root of each chord, in its own octave.
- **Bells.** Bell timing is random (a Poisson process), thinned by a "density"
  value that wanders slowly between 10 % and 100 % every 30 seconds, so the
  bells come and go in waves. Each new pitch is chosen near the previous one,
  favouring chord notes (`bells.chord_bias`) and avoiding repeats. That
  produces a meandering melody rather than random plinks.
- **Lead** (optional, `lead.enabled`). A second melody with the same pitch
  logic, but its notes are *held* for `lead.note_beats` beats and each one
  lets go when the next begins, like a single player phrasing. It is made
  for sustained instruments such as `flute` or strings, so a piece can mix
  chords, a struck melody and a held lead. `lofi_trap_ensemble` uses
  strings (pad), piano (bells) and flute (lead):

  ```bash
  python3 ambient.py --preset presets/lofi_trap_ensemble.json --minutes 30
  python3 ambient.py --preset presets/night.json --set lead.enabled=true --set lead.per_minute=6
  ```

### 2. Rendering

The timeline is rendered in 10-second blocks. For each block, the script:

1. Finds every note sounding in that block.
2. Renders each one with the synth engine using its layer's patch, then adds
   it to a dry mix and, scaled by that layer's `reverb_send`, to a reverb
   send.
3. Adds the **air** layer: band-passed stereo noise with a slow swell.
4. Runs the send through the **reverb**, a convolution with a synthetic
   impulse response (decaying stereo noise whose highs decay faster than its
   lows). The reverb tail carries over into the next block.
5. Runs the **master** stage: a gentle lowpass for warmth, fade-in/out, gain,
   and a `tanh` soft limiter that is transparent at normal levels and rounds
   off peaks.
6. Writes the block to disk.

Memory use therefore stays flat however long the piece is. Everything that
must carry over between blocks is kept and passed on: filter memory, reverb
tail, voice phases, and noise position, so there are no seams. The synth
voices, drums, vinyl, sidechain and crusher all render **bit-identically
however the timeline is cut into blocks**. The one exception is the `air`
layer, whose noise is drawn in block-sized chunks. That doesn't affect
reproducibility, because the block size is always the same 10 seconds.

---

## Beats: the groove mode

With `groove.enabled` on, the same machinery makes beat music. The
`lofi_trap` and `lofi_chill` presets use it. With it off (the default),
every other preset renders exactly as before.

```bash
python3 ambient.py --preset presets/lofi_trap.json --minutes 30
python3 ambient.py --preset presets/lofi_chill.json --minutes 60 --set groove.bpm=68
python3 ambient.py --preset presets/night.json --set groove.enabled=true     # put a beat under any preset
```

**What changes when the groove is on**

- **Tempo and grid.** Everything sits on a 4/4 grid of 16th notes at
  `groove.bpm`. Every off-beat 16th is delayed by `groove.swing`. The snare
  is on beat 3, so 70-90 BPM gives the half-time lo-fi/trap pocket.
- **Chord loops.** A progression of `groove.loop_chords` chords, each
  `groove.bars_per_chord` bars long, repeats `groove.loop_repeats` times
  before a new one is made, the way a beat loops a sample. With
  `pad.restrike_beats` the chord is played again every N beats, like a
  keys player comping, instead of being held.
- **Sections.** Every `groove.section_bars` bars, the energy changes:

  | Energy | What plays |
  |---|---|
  | 0 | No drums: intro, breakdowns, outro (chords, melody, vinyl) |
  | 1 | Kick, snare, 8th-note hats, 808 |
  | 2 | Full beat: 16th hats, hi-hat rolls, snare fills at phrase ends, syncopated 808 pushes |

  A piece starts and ends at energy 0, and in between it walks randomly
  from section to section (`ENERGY_MOVES` in `groove.py`).
  `groove.breakdown_chance` sets how often a full beat drops out.
- **Drums.** Each section gets a base pattern drawn from per-step
  probabilities (`KICK_PROB` and friends in `groove.py`). Every bar varies it
  slightly, so the groove stays recognizable without looping exactly. Hits
  are humanized by `groove.humanize_ms`. Hi-hat rolls use 16th triplets,
  32nds or 32nd triplets, rising or falling in velocity, mostly on beat 4
  and at phrase ends.
- **808.** The `bass` layer becomes an 808 line. It hits mostly with the
  kick, plays the chord root (sometimes the octave or the 5th), holds until
  the next hit, and slides into new pitches (`groove.slide_chance`). Use it
  with the `sub_808` patch.
- **Melody.** The `bells` and `lead` layers are quantized to 8th notes.
- **Sidechain pump.** The pad, 808 and melody dip by `groove.duck_depth` on
  every kick and recover over `groove.duck_release` seconds.

**The drums are synth patches.** `kick`, `snare` and `hats` are layers like
the pad, so every drum is tunable with the same settings:

```bash
python3 ambient.py --preset presets/lofi_trap.json \
  --set kick.synth.pitch_env_semitones=40 --set kick.synth.decay=0.6 \
  --set hats.synth.cutoff_hz=9000 --set hats.synth.decay=0.12
```

**Drum kits.** `groove.kit` swaps all three drum patches at once:

| Kit | Kick / snare / hats | Sound |
|---|---|---|
| `custom` (default) | each layer's own `.patch` | whatever the preset chose |
| `lofi` | `kick_soft`, `snare_lofi`, `hat_closed` | soft and dusty |
| `trap` | `kick_punch`, `snare_lofi`, `hat_closed` | tighter, punchier kick |
| `909` | `kick_909`, `snare_909`, `hat_909` (+ `openhat_909`, `rim_909`) | TR-909 style: deep swept kick with a click, snappy two-tone snare, metallic hats |
| `techno` | `kick_909`, `clap_909`, `hat_909`, `openhat_909`, `rim_909` | The 909 kit with a clap instead of the snare, for techno |

```bash
python3 ambient.py --preset presets/lofi_trap.json --set groove.kit=909
```

A kit overrides the three drum patches (even ones a preset sets); set it
back to `custom` to pick them one by one. Fine-tuning still works on top,
for example `--set kick.synth.transpose=-2` for a lower 909 kick. The drum
patterns stay lo-fi/trap whichever kit plays them.

**Lo-fi texture** (works with or without the groove):

| Setting | Effect |
|---|---|
| `vinyl.enabled`, `vinyl.level`, `vinyl.hiss`, `vinyl.crackle_per_second` | Record surface: hiss, crackle and the occasional pop, over the finished mix |
| `master.saturation` | Tape-style saturation of the whole mix |
| `master.bitcrush_bits` | Bit reduction for sampler grit (12 is subtle, 8 is crunchy; 0 = off) |
| `master.downsample_hz` | Sample-and-hold to a lower rate, like an old sampler (around 26000 is subtle; 0 = off) |
| `master.lowpass_hz` | Darker top end; lo-fi usually sits at 6-9 kHz |

---

## Techno: long tracks and jams

`groove.style=techno` swaps the lo-fi beat for a four-on-the-floor
arrangement built to run for an hour or more without going stale. Eight
presets cover the main styles:

| Preset | Key | BPM | Style |
|---|---|---|---|
| `techno` | A minor | 130 | The all-rounder: 909 kit, acid line, stabs, offbeat/rolling bass |
| `techno_punchy` | A minor | 132 | Harder-hitting `techno`: driven tight kick, deeper pump, busier, louder master |
| `minimal_techno` | G minor | 125 | Sparse, dry and clicky: rolling bass, percussion-led, no acid, few elements at once |
| `acid_techno` | F# minor | 135 | The 303 up front: busy, accented, sliding, extra resonance and drive, wide filter sweeps |
| `hypnotic_techno` | D minor | 128 | Deep and slow-changing: 32-bar phrases, one chord at a time, rare breakdowns, long intro/outro |
| `industrial_techno` | E phrygian | 140 | Hard and distorted: overdriven kick and clap, rolling bass, heavy saturation, bit-crush |
| `melodic_techno` | F# minor | 124 | Plucked arpeggio sequence over shimmering pads and stabs, 4-chord progressions |
| `dub_techno` | F minor | 122 | Deep chord stabs into long reverb, offbeat bass, vinyl, no acid |

```bash
python3 ambient.py --preset presets/techno.json --minutes 60
python3 ambient.py --preset presets/hypnotic_techno.json --minutes 120     # a two-hour deep set
python3 ambient.py --preset presets/acid_techno.json --set groove.bpm=140 --set techno.density=0.85   # harder, busier
```

All of them are built only from settings (see `presets/`), so they are a
good starting point for your own. Copy one and change the BPM, key,
density, kit or patches.

**The elements**

| Element | Layer | What it plays |
|---|---|---|
| Kick | `kick` | Four on the floor, dead on the grid (never humanized); now and then drops the last beat of a phrase |
| Clap | `snare` | Beats 2 and 4, as three quick bursts like an analog clap (`techno.clap_flam_ms`) |
| Closed hats | `hats` | 16ths with offbeat accents |
| Open hat | `openhat` | Every offbeat (the "tss" between kicks); the closed hat steps aside for it |
| Percussion | `perc` | A syncopated rimshot pattern, new each phrase, with its own stereo position |
| Bass | `bass` | Offbeat or rolling 16ths on the chord root (`techno.bass_style`; `auto` changes per phrase) |
| Acid | `lead` | A 303-style 16-step sequence with accents and slides, mutating a step at a time |
| Stabs | `bells` | Short chord stabs on a syncopated rhythm, made for a long reverb |
| Pad | `pad` | The chord, held, for atmosphere |

**How a long track evolves**

- **Phrases.** Every `techno.phrase_bars` bars (16 by default, about 30 s at
  130 BPM), one element enters, leaves, or is swapped for another. The
  number playing at once follows a slow random walk scaled by
  `techno.density`. The backbone (hats, clap, bass) is the least likely to
  be taken out; flourishes (stabs, perc, pad) come and go most.
- **Intro and outro.** The first `techno.intro_bars` and last
  `techno.outro_bars` bars are kick and hats only, so a track is easy to mix
  in and out of.
- **Breakdowns and drops.** With chance `techno.breakdown_chance` per phrase
  (never in the first few minutes, and at least four phrases apart), a
  phrase becomes a breakdown: kick, bass and beat stop, and the pad, acid
  and stabs carry on. Its last two bars are a snare roll (8ths, then 16ths,
  getting louder) into the drop, where the kick returns.
- **Patterns breathe.** The perc, stab and acid patterns and the bass style
  are made fresh each phrase and repeat bar by bar with small mutations:
  hypnotic, but never an exact loop.
- **The acid filter drifts.** The acid line's filter cutoff follows its own
  slow random walk over minutes, up to `techno.acid_sweep_octaves` octaves
  open or shut, and accented notes open it further. This is the "hand on
  the cutoff knob" of a live jam.
- **Harmony** moves slowly: with the presets' `groove.bars_per_chord=16`
  and `loop_chords=2`, the chord changes every 16 bars and a new pair of
  chords comes every 256 bars (about 8 minutes).

Every part is a synth patch, so the whole kit is tunable:

```bash
python3 ambient.py --preset presets/techno.json \
  --set lead.synth.resonance=0.9 --set lead.synth.drive=0.7 \
  --set kick.synth.decay=0.8 --set techno.acid_slide=0.4
```

---

## Playing a hardware synth (MIDI)

The composer plans every note before any audio is made, so any layer can
be sent to a real synthesizer instead of (or as well as) the built-in
engine. `midi_out.py` does this, either live or as a MIDI file. It was built
around the **M-VAVE FM-1** on stock firmware (a 6-operator FM synth that
plays one sound at a time, on one MIDI channel, with 12 voices), but it
works with any MIDI instrument.

```bash
python3 ambient.py --list-midi-ports                         # what this computer can see
python3 ambient.py --preset presets/techno.json --minutes 30 --midi-out FM-1 --midi-layers lead
python3 ambient.py --preset presets/night.json --minutes 20 --midi-out FM-1 --midi-layers pad --midi-voices 12
python3 ambient.py --preset presets/lofi_trap.json --minutes 10 --export-midi lofi.mid --no-audio
```

**Live (`--midi-out PORT`).** `PORT` is any part of the port name, e.g.
`FM-1`. The layers in `--midi-layers` (default `pad`) are sent on
`--midi-channel` (default 1), in real time. Everything else is rendered
to a **backing track** (`..._backing.wav`) and played through the Mac's
speakers at the same moment, so the synth plays its part over the rest of
the piece. Add `--no-audio` for the synth alone.

- **Note length.** Each note gets a note-on at its start and a note-off at
  its release; the synth's own envelope plays the fade-out.
- **Voice limit (`--midi-voices`, default 12).** If more notes would hold
  at once than the synth has voices, the oldest is released first, so
  nothing hangs. Use `0` for no limit.
- **Clock (`--midi-clock`).** Sends MIDI clock and start/stop at the
  piece's BPM, so the synth's arpeggiator or sequencer can follow along.
- **Sync (`--midi-offset-ms`).** The synth and the backing track start
  together; if your setup has a little latency, shift the MIDI later
  (positive) or earlier (negative) until they line up by ear.
- **Stopping.** Ctrl-C stops cleanly: every sounding note is released and
  an all-notes-off is sent.

**MIDI file (`--export-midi PATH`).** Saves the whole composition as a
standard `.mid` file with one track per layer: pad on channel 1, bass 2,
bells 3, lead 4, and drums on channel 10 using General MIDI drum notes.
Load it into any DAW to play it on hardware or software instruments, edit
it, or record the result.

**With the FM-1 (stock firmware)**

1. Connect the FM-1 over USB-C. It is class-compliant, so it shows up
   without drivers; check with `--list-midi-ports`.
2. In the FM-1's Global menu, set the **Note Channel** to 1 (or pass
   `--midi-channel` to match it).
3. Pick the sound on the FM-1 itself. The stock firmware doesn't take
   program changes, and it plays one sound at a time.
4. Send it one part. With 12 voices and one sound, these work best:

   | Style | Send | A good FM-1 sound |
   |---|---|---|
   | Techno (`techno`, `acid_techno`, ...) | `lead` (the acid/arp sequence) or `bass` | Bass or plucked lead |
   | Lo-fi (`lofi_trap`, ...) | `pad` (the chords, restruck) or `bells` (melody) | Electric piano |
   | Ambient (`night`, `aurora`, ...) | `pad` or `bells` | Pad, bell or e-piano |

   Several layers can go to it at once (`--midi-layers pad,bells`), but
   they will all use the FM-1's one sound.
5. Drums stay in the backing track: the stock firmware has no drum sounds.
   (Community firmwares such as Felucca and Melodee add multiple parts and
   drums; this module could map layers to their channels later.)

---

## The synth engine

`synth.py` is one subtractive/FM synth that plays the **pad**, **bass**,
**bells**, **lead**, **kick**, **snare** and **hats** layers. Each layer picks a patch, and you can tweak any
setting of that patch per layer.

Signal flow for one note:

```
 ┌──────────────── unison voices (1-7), each detuned and panned ────────────────┐
 │  saw + square/pulse + triangle + sine(FM: modulator at fm_ratio × pitch)      │
 │  pitch: vibrato + slow random "analog" drift per voice                        │
 └───────────────────────────────────────────────────────────────────────────────┘
   pitch drop at the start (kick/808 punch) + glide from the previous note (808 slides)
          + sub sine (one octave down)     + white noise
                              │
                              ▼
     drive (saturation)
                              │
                              ▼
     resonant filter: lowpass / bandpass / highpass  (or off)
       cutoff = cutoff_hz × key tracking × 2^(envelope + slow LFO)
                              │
                              ▼
     amp envelope: attack → decay → sustain … release  ×  slow "breathing"
                              │
                              ▼
                         × gain_db trim
```

Some details:

- **Oscillators** use PolyBLEP anti-aliasing, so saws and pulses stay clean
  in the high registers.
- **FM** applies only to the sine. With `fm_index` 0 it's a pure sine; raise it
  for overtones. `fm_ratio` sets the character: 1 = electric piano,
  2 = hollow, 3.5 = bell, 7 = glass, and non-whole ratios like 1.41 sound
  metallic. `fm_index_decay` makes the shimmer fade before the tone, the way a
  struck bell does.
- **Unison** spreads 1-7 copies of each note across `detune_cents` and the
  stereo field (`width`). Volume is normalised, so adding voices makes the
  sound wider, not louder.
- **The filter** is a zero-delay-feedback state-variable filter (the
  Simper/Cytomic design), compiled with numba. Its cutoff can move every
  sample without zipper noise, and it stays stable at any resonance.
- **Key tracking** (`key_track`) makes higher notes open the filter further,
  as on an analog synth. `cutoff_hz` is the cutoff at middle C.
- **Filter envelope**: rises over `filter_attack`, then falls over
  `filter_decay` and moves the cutoff by `filter_env_octaves` (negative
  values close it instead). A short decay gives a pluck; a long attack gives
  a pad that blooms.
- **Filter LFO**: a slow sine sweep (`filter_lfo_hz`, `filter_lfo_octaves`)
  with a random start per note, so chord notes don't sweep in lockstep.
- **Amp envelope**: `sustain` 0 makes a struck or plucked note that dies away
  over `decay`; `sustain` 1 makes a held pad. Struck notes on the bells layer
  are held for their `decay` time, then released.
- **Pitch drop** (`pitch_env_semitones`, `pitch_env_decay`): each note starts
  above its pitch and falls to it. A few semitones adds punch to an 808;
  24-36 semitones over a few tens of milliseconds makes a kick drum.
- **Glide** (`glide_seconds`): when a note has a previous pitch to slide from
  (808 slides), it bends from it over this time.
- **Drive** saturates the oscillators before the filter, from warm
  (0.1-0.2) to gritty (0.5+).
- **Breathing**: a slow volume swell, with the rate varied a little per note.
- **Per-note randomness** (voice phases, drift rates, LFO starts) comes from
  each note's own seed, so it is the same on every render.

---

## Patches

A patch is a JSON file in `patches/` that sets synth settings. Any setting it
leaves out takes the engine's default.

| Patch | Best on | Sound |
|---|---|---|
| `abyss_drone` | pad | Dark, growling drone: detuned saw + pulse + sub under a low resonant filter that crawls open and shut |
| `organ` | pad | Square + sine + sub drawbar organ, steady and sacred |
| `shimmer_pad` | pad | Wide saw + pulse supersaw, bright and airy, filter opens as each chord arrives |
| `soft_pad` | pad | Warm detuned saw through a gentle lowpass, slow swell (the original pad) |
| `sub_swell` | pad | Triangle + sub under a resonant lowpass that sweeps slowly: deep and submerged |
| `tape_strings` | pad | Warm strings played off worn tape: pitch wow, drift and a little hiss |
| `techno_pad` | pad | Dark techno pad: wide detuned saw and triangle under a slowly sweeping resonant filter |
| `warm_strings` | pad | Five-voice saw ensemble with a slowly breathing filter |
| `lofi_keys` | pad (restruck) or bells | Worn electric piano for chords: FM tines, tape wow and a dark top |
| `bass_guitar` | bass | Fingered electric bass: saw/pulse/sine body with a plucked filter thump that settles into a warm ring |
| `round_bass` | bass | Sine with a touch of triangle: felt more than heard (the original bass) |
| `techno_bass` | bass | Tight techno bass: saw, sine and sub with a short filter pluck |
| `sub_808` | bass (808) | 808 bass: sine with a pitch-drop punch, saturated, long boom; slides between notes |
| `church_bell` | bells | Low inharmonic FM bell (ratio 1.41) with a long, slow hum |
| `felt_piano` | bells | Soft FM electric piano with the hammers wrapped in felt |
| `fm_bell` | bells | Two-operator FM bell, 3.5 ratio, shimmer fades before the tone (the original bells) |
| `glass_crystal` | bells | High-ratio FM: glassy, crystalline, long ring |
| `hollow_drop` | bells | Hollow FM tone (ratio 2) through a bandpass: like a drop in a cave |
| `koto_pluck` | bells | Plucked string: bright pulse snapping shut through a fast filter envelope |
| `soft_piano` | bells | Soft acoustic-style piano: bright hammer attack that mellows, paired strings |
| `dub_stab` | bells (stabs) | Dub-techno chord stab: detuned saw and pulse, a quick filter blip, made for long reverb |
| `flute` | lead | Breathy flute: triangle and sine with air noise, vibrato and a soft attack chiff |
| `acid_303` | lead (acid) | 303-style acid: saw through a squelchy, resonant filter with a punchy envelope; slides |
| `kick_909` | kick | TR-909 style kick: deep sine pitch sweep around 52 Hz with a sharp click and driven body |
| `kick_punch` | kick | Tight trap kick: fast deep pitch drop, a little drive, short tail |
| `kick_soft` | kick | Round, soft kick: a sine that drops two octaves, with a gentle click |
| `snare_909` | snare | TR-909 style snare: two-tone body with a fast pitch drop under bright, snappy noise |
| `snare_lofi` | snare | Dusty snare: a short tone body under filtered noise |
| `clap_909` | snare (clap) | TR-909 style clap: band-passed noise bursts (the pattern plays three in a quick flam) |
| `hat_909` | hats | TR-909 style closed hat: metallic FM partials and noise through a resonant highpass |
| `hat_closed` | hats | Closed hi-hat: bright noise through a resonant highpass, very short |
| `openhat_909` | openhat | TR-909 style open hat: the metallic closed hat, left to ring |
| `rim_909` | perc | TR-909 style rimshot: a short, knocking click with a woody tone |

Pick a patch per layer:

```bash
python3 ambient.py --set pad.patch=abyss_drone --set bells.patch=church_bell
```

Every patch carries a `gain_db` trim, measured so that all pads play at about
the same loudness (around −18 LUFS on their own) and all struck sounds do too
(around −24 LUFS). Swapping patches therefore won't cause big volume jumps.
The drum patches are balanced against each other for a lo-fi mix.

---

## Changing settings

Settings are applied in this order, each overriding the last:

```
 built-in defaults  →  each layer's patch  →  preset file  →  --set flags
```

Every setting has a dotted path:

| Path | Example | Meaning |
|---|---|---|
| `<setting>` | `minutes=60` | The whole piece |
| `harmony.<setting>` | `harmony.scale=lydian` | Keys, scales, chords |
| `<layer>.<setting>` | `bells.per_minute=2` | A layer's musical role: level, register, density, reverb send |
| `<layer>.patch` | `pad.patch=organ` | Which synth patch a layer plays |
| `<layer>.synth.<param>` | `pad.synth.resonance=0.7` | Tweak one synth setting of that layer's patch |
| `groove.*` | `groove.bpm=82` | Tempo, style (lofi/techno), kit, loops, sections and drum behaviour |
| `techno.*` | `techno.density=0.8` | Techno arrangement: phrases, intro/outro, breakdowns, bass style, acid line |
| `air.*`, `vinyl.*`, `reverb.*`, `master.*` | `reverb.seconds=15` | Effects and output |

The layers are `pad`, `bass`, `bells`, `lead` (off by default), and (with
the groove on) `kick`, `snare` and `hats`, plus `openhat` and `perc` in
techno style. Values are type- and range-checked:
`--set pad.level=2` stops with `pad.level: 2.0 is outside 0..1`.

Some recipes:

```bash
# darker, slower, emptier
python3 ambient.py --set harmony.chord_seconds_min=60 --set harmony.chord_seconds_max=120 \
                   --set pad.synth.cutoff_hz=400 --set bells.per_minute=1

# a resonant, sweeping version of the default pad
python3 ambient.py --set pad.synth.resonance=0.7 --set pad.synth.filter_lfo_octaves=1.5 \
                   --set pad.synth.filter_lfo_hz=0.03

# turn the bells into plucks and the pad into an organ
python3 ambient.py --set bells.patch=koto_pluck --set pad.patch=organ --set pad.open_voicing=false

# drone only: one long chord at a time, no key changes, no melody
python3 ambient.py --set harmony.chord_seconds_min=300 --set harmony.chord_seconds_max=600 \
                   --set harmony.modulate_minutes=0 --set bells.enabled=false
```

---

## Writing your own presets and patches

### A preset

Copy one from `presets/` and edit it. A preset lists only what it changes,
nested by group. Synth tweaks go under the layer's `synth` key:

```json
{
  "harmony": { "key": "C", "scale": "phrygian", "chord_notes": 3,
               "chord_seconds_min": 50, "chord_seconds_max": 120, "modulate_minutes": 0 },
  "pad":     { "patch": "abyss_drone", "octave": 2, "open_voicing": false, "level": 0.55 },
  "bass":    { "octave": 1, "synth": { "attack": 15, "release": 20 } },
  "bells":   { "patch": "church_bell", "octave": 4, "per_minute": 1.5 },
  "air":     { "level": 0.07, "cutoff_hz": 600, "swell_seconds": 150 },
  "reverb":  { "seconds": 16, "damping": 0.75, "level": 0.75 },
  "master":  { "gain_db": -3, "lowpass_hz": 4500, "fade_in": 40, "fade_out": 90 }
}
```

### A patch

Copy one from `patches/`, rename it, and change the values. The file name is
the patch name. List only what differs from the engine defaults (a plain sine
with a short envelope); see [Reference: synth settings](#reference-synth-settings).

```json
{
  "description": "Dark, growling drone: detuned saw + pulse + sub under a low resonant filter",
  "saw": 0.6, "square": 0.4, "pulse_width": 0.4, "sine": 0.0, "sub": 0.7, "noise": 0.04,
  "unison": 3, "detune_cents": 20, "width": 0.9, "drift_cents": 8,
  "filter": "lowpass", "cutoff_hz": 220, "resonance": 0.6, "key_track": 0.2,
  "filter_lfo_hz": 0.015, "filter_lfo_octaves": 1.5,
  "attack": 15, "sustain": 1.0, "release": 20,
  "breath_hz": 0.04, "breath_depth": 0.25,
  "gain_db": -1.0
}
```

New patches appear automatically in `--list-patches` and as choices for
`<layer>.patch`. Remember `"sine": 0.0` if you don't want the default sine
under your other oscillators. Set `gain_db` by ear, or render the patch solo
and compare its loudness with the others:

```bash
python3 ambient.py --minutes 1.5 --set pad.patch=my_patch \
  --set bass.enabled=false --set bells.enabled=false --set air.enabled=false
```

### Going deeper in the code

- **New scale:** add a line to `SCALES` in `ambient.py`.
- **Different chord movement:** change the weights in `CHORD_MOVES`.
- **New synth setting:** add it to `PARAMS` in `synth.py` and use it in
  `synth.render()`. It shows up automatically as `<layer>.synth.<name>` for
  every layer.
- **New layer setting:** add a `K(...)` line to `KNOBS` in `ambient.py`.

---

## Reproducibility

- `seed` 0 (the default) picks a fresh random seed. The seed actually used is
  printed, put in the file name, and saved in the `.json` sidecar.
- The same settings with the same seed give a **byte-identical WAV**.
- The sidecar stores every resolved setting, including every synth value, so
  it replays exactly even after you edit the patch files:

  ```bash
  python3 ambient.py --preset renders/ambient_tape_memory_1433086701.json
  ```

- The randomness is split into independent streams (harmony, bells, lead,
  drums, vinyl, air, reverb, dither). Changing the air or reverb settings doesn't change the
  notes; turning a layer off doesn't change what the others play.

---

## Long renders and file formats

- **Memory** stays flat at any length, because audio is rendered and written
  in 10-second blocks.
- **Speed** is about 5-25x faster than realtime on an Apple Silicon Mac,
  depending on the patches; the lo-fi presets run at about 10-20x. Wide unison pads (`shimmer_pad` with 5-note
  chords) are the slowest; FM bells are nearly free. One hour takes roughly
  3-12 minutes.
- **Formats:** `bit_depth` 24 (default) or 16 (with triangular dither), at
  `sample_rate` 44100, 48000 or 96000 Hz.
- **Files over 4 GB** (about 4 h at 24-bit/48 kHz, or 6.7 h at 16-bit/44.1 kHz)
  are written as **RF64**, the standard extension for large WAVs. ffmpeg,
  Audacity, Reaper, Logic and most DAWs read it. For other tools, use 16-bit
  or split the render.
- **Ctrl-C** finalises the header, so you keep a valid, shorter WAV.
- **Levels:** the raw render peaks around −4 to −6 dBFS and sits between
  −14 and −25 LUFS depending on the preset. For platform-ready masters
  (YouTube loudness, MP3/FLAC), run the WAV through the
  [process-audio](https://github.com/tomott12345/process-audio) pipelines.

---

## Driving it from a front end

The command-line interface is designed to be driven by another program,
such as a Go web UI. It uses the same progress protocol as `process-audio`,
so that project's Go runner can drive this one unchanged.

**Discover the controls**

```bash
python3 ambient.py --list-params    # every setting
python3 ambient.py --list-patches   # patch names + descriptions
```

`--list-params` returns one entry per setting, which is enough to build a
slider, toggle or dropdown for each:

```json
{ "path": "pad.synth.cutoff_hz", "default": 750.0, "type": "float",
  "min": 20, "max": 20000, "choices": null, "help": "Filter cutoff at middle C" }
```

Paths group naturally into UI sections: `harmony`, `groove`, `pad`, `pad.synth`,
`bass`, `bells`, `kick`, `snare`, `hats`, `air`, `vinyl`, `reverb` and `master`.

**Run a render**

Write the UI's values as a preset JSON (nested or flat dotted keys both
work), then:

```bash
python3 ambient.py --preset job.json --out jobs/123/out.wav --progress-json
```

**Follow progress**

With `--progress-json`, stdout carries one JSON event per line, each
prefixed with `@@progress `. Human-readable progress goes to stderr.

```
@@progress {"event": "plan", "steps": [{"step": "compose", "label": "Composing"}, {"step": "render", "label": "Rendering audio"}]}
@@progress {"event": "step_start", "step": "compose", "label": "Composing"}
@@progress {"event": "step_end", "step": "compose", "seconds": 0.01}
@@progress {"event": "step_start", "step": "render", "label": "Rendering audio"}
@@progress {"event": "progress", "step": "render", "done": 12, "total": 36}
@@progress {"event": "step_end", "step": "render", "seconds": 30.8}
@@progress {"event": "output", "kind": "master", "format": "wav24", "path": "/abs/path/out.wav"}
@@progress {"event": "manifest", "files": [{"kind": "master", "format": "wav24", "path": "..."},
                                         {"kind": "settings", "format": "json", "path": "..."}]}
```

A failed step emits `step_error` with a message. Invalid settings exit
non-zero with a one-line explanation on stderr.

---

## Reference: piece, layer and effect settings

Synth settings (`<layer>.synth.*`) are listed in the next section.

| Setting | Default | Range | What it does |
|---|---|---|---|
| `minutes` | 30 | 0.1 – 720 | Length of the piece in minutes |
| `seed` | 0 | 0 – 2147483647 | Random seed. 0 picks a fresh one; the seed actually used is saved in the .json |
| `sample_rate` | 48000 | `44100`, `48000`, `96000` | Samples per second |
| `bit_depth` | 24 | `16`, `24` | WAV bit depth |
| `harmony.key` | D | `C`, `C#`, `D`, `D#`, `E`, `F`, `F#`, `G`, `G#`, `A`, `A#`, `B` | Starting key |
| `harmony.scale` | dorian | `major`, `minor`, `dorian`, `lydian`, `mixolydian`, `phrygian`, `major_pentatonic`, `minor_pentatonic`, `hirajoshi` | Scale every note is drawn from |
| `harmony.chord_notes` | 4 | 2 – 5 | Notes per chord: 3 = triad, 4 = seventh, 5 = ninth |
| `harmony.chord_seconds_min` | 20 | 2 – 600 | Shortest time a chord is held |
| `harmony.chord_seconds_max` | 45 | 2 – 600 | Longest time a chord is held |
| `harmony.modulate_minutes` | 12 | 0 – 240 | Move to a neighbouring key this often (0 = never) |
| `pad.enabled` | true | true / false | Play the pad |
| `pad.level` | 0.5 | 0 – 1 | Pad volume |
| `pad.octave` | 3 | 1 – 5 | Register: 3 sits around C3-C4 |
| `pad.open_voicing` | true | true / false | Spread chord notes over two octaves instead of one |
| `pad.restrike_beats` | 0 | 0 – 16 | With the groove on: re-strike the chord every N beats (0 = hold it) |
| `pad.reverb_send` | 0.6 | 0 – 1 | How much pad goes into the reverb |
| `bass.enabled` | true | true / false | Play the bass |
| `bass.level` | 0.28 | 0 – 1 | Bass volume |
| `bass.octave` | 2 | 0 – 4 | Register: 2 sits around C2-C3 |
| `bass.reverb_send` | 0.1 | 0 – 1 | How much bass goes into the reverb |
| `bells.enabled` | true | true / false | Play the bells |
| `bells.level` | 0.35 | 0 – 1 | Bell volume |
| `bells.per_minute` | 5 | 0 – 60 | Notes per minute at their busiest |
| `bells.octave` | 5 | 3 – 7 | Register: 5 sits around C5-G6 |
| `bells.chord_bias` | 0.7 | 0 – 1 | Chance of picking a chord note over any scale note |
| `bells.reverb_send` | 0.8 | 0 – 1 | How much bell goes into the reverb |
| `lead.enabled` | false | true / false | Play the lead melody |
| `lead.level` | 0.3 | 0 – 1 | Lead volume |
| `lead.per_minute` | 10 | 0 – 60 | Notes per minute at their busiest |
| `lead.octave` | 5 | 1 – 7 | Register: 5 sits around C5-G6 (an acid line sits at 2) |
| `lead.note_beats` | 1.5 | 0.25 – 8 | How long each note is held, in beats at groove.bpm (cut short by the next note) |
| `lead.chord_bias` | 0.75 | 0 – 1 | Chance of picking a chord note over any scale note |
| `lead.reverb_send` | 0.5 | 0 – 1 | How much lead goes into the reverb |
| `groove.enabled` | false | true / false | Turn on the beat: tempo, bar-aligned chord loops, drums, 808 |
| `groove.style` | lofi | `lofi`, `techno` | Beat style: lofi (half-time, rolls, 808) or techno (four-on-the-floor, acid, long builds) |
| `groove.kit` | custom | `custom`, `lofi`, `trap`, `909`, `techno` | Drum kit: sets the kick, snare and hats patches together ('custom' = use each layer's own patch) |
| `groove.bpm` | 78 | 50 – 180 | Tempo. With the snare on beat 3, 70-90 BPM feels like lo-fi/trap half-time |
| `groove.swing` | 0.12 | 0 – 0.6 | Delay of every off-beat 16th, as a fraction of a 16th |
| `groove.bars_per_chord` | 2 | 1 – 16 | Bars each chord lasts |
| `groove.loop_chords` | 4 | 1 – 8 | Chords in the looping progression |
| `groove.loop_repeats` | 4 | 1 – 64 | Times the progression loops before a new one is made |
| `groove.section_bars` | 8 | 2 – 64 | Bars per section; the energy (no drums / beat / full beat) changes between sections |
| `groove.breakdown_chance` | 0.2 | 0 – 1 | Chance that a full-beat section is followed by a drumless breakdown |
| `groove.kick_density` | 1 | 0 – 2.5 | Scales the chance of the optional kicks |
| `groove.hat_rolls` | 0.35 | 0 – 1 | Chance per bar of a trap hi-hat roll (in full-beat sections) |
| `groove.slide_chance` | 0.3 | 0 – 1 | Chance that the 808 slides into a new pitch |
| `groove.humanize_ms` | 8 | 0 – 40 | Random timing wobble of each drum hit |
| `groove.duck_depth` | 0.35 | 0 – 1 | Sidechain: how far pad, bass and melody dip on each kick |
| `groove.duck_release` | 0.3 | 0.02 – 2 | Sidechain: seconds to recover after each kick |
| `kick.enabled` | true | true / false | Play the kick |
| `kick.level` | 0.6 | 0 – 1 | Kick volume |
| `kick.midi` | 36 | 12 – 72 | Kick pitch (MIDI note; 36 = C2) |
| `kick.reverb_send` | 0.05 | 0 – 1 | How much kick goes into the reverb |
| `snare.enabled` | true | true / false | Play the snare |
| `snare.level` | 0.45 | 0 – 1 | Snare volume |
| `snare.midi` | 52 | 24 – 84 | Snare body pitch (MIDI note) |
| `snare.reverb_send` | 0.2 | 0 – 1 | How much snare goes into the reverb |
| `hats.enabled` | true | true / false | Play the hi-hats |
| `hats.level` | 0.25 | 0 – 1 | Hi-hat volume |
| `hats.midi` | 72 | 36 – 108 | Hi-hat pitch (only matters for patches with a tone) |
| `hats.pan` | 0.2 | -1 – 1 | Hi-hat position: -1 left .. +1 right |
| `hats.reverb_send` | 0.1 | 0 – 1 | How much hi-hat goes into the reverb |
| `openhat.enabled` | true | true / false | Play the open hi-hat (techno style) |
| `openhat.level` | 0.25 | 0 – 1 | Open hi-hat volume |
| `openhat.midi` | 72 | 36 – 108 | Open hi-hat pitch (only matters for patches with a tone) |
| `openhat.pan` | -0.15 | -1 – 1 | Open hi-hat position: -1 left .. +1 right |
| `openhat.reverb_send` | 0.15 | 0 – 1 | How much open hi-hat goes into the reverb |
| `perc.enabled` | true | true / false | Play the percussion: rimshots and similar (techno style) |
| `perc.level` | 0.3 | 0 – 1 | Percussion volume |
| `perc.midi` | 76 | 36 – 108 | Percussion pitch (MIDI note) |
| `perc.reverb_send` | 0.3 | 0 – 1 | How much percussion goes into the reverb |
| `techno.phrase_bars` | 16 | 4 – 64 | Bars per phrase; one element enters or leaves at each phrase boundary |
| `techno.intro_bars` | 32 | 0 – 512 | Kick-and-hats-only bars at the start (DJ-friendly intro) |
| `techno.outro_bars` | 32 | 0 – 512 | Kick-and-hats-only bars at the end |
| `techno.density` | 0.6 | 0.1 – 1 | How many elements tend to play at once |
| `techno.breakdown_chance` | 0.15 | 0 – 1 | Chance per phrase of a breakdown (no kick) that builds into a drop |
| `techno.bass_style` | auto | `auto`, `offbeat`, `rolling` | Bassline rhythm: offbeat, rolling 16ths, or auto (changes per phrase) |
| `techno.acid_density` | 0.65 | 0.1 – 1 | Share of the 16 acid steps that play |
| `techno.acid_accent` | 0.3 | 0 – 1 | Share of acid notes that are accented (louder, brighter) |
| `techno.acid_slide` | 0.2 | 0 – 1 | Share of acid notes that slide into the next |
| `techno.acid_sweep_octaves` | 2 | 0 – 4 | How far the acid filter drifts open and shut over time |
| `techno.clap_flam_ms` | 11 | 2 – 30 | Spacing of the clap's three bursts |
| `vinyl.enabled` | false | true / false | Add vinyl surface noise |
| `vinyl.level` | 0.5 | 0 – 2 | Vinyl noise volume |
| `vinyl.hiss` | 0.3 | 0 – 1 | Hiss under the crackle |
| `vinyl.crackle_per_second` | 8 | 0 – 100 | Crackles per second |
| `air.enabled` | true | true / false | Play the air layer |
| `air.level` | 0.05 | 0 – 1 | Air volume |
| `air.cutoff_hz` | 2500 | 200 – 16000 | Top of the noise band; lower = darker |
| `air.swell_seconds` | 40 | 4 – 600 | Length of one slow rise-and-fall |
| `air.reverb_send` | 0.5 | 0 – 1 | How much air goes into the reverb |
| `reverb.level` | 0.6 | 0 – 1.5 | Wet reverb volume |
| `reverb.seconds` | 7 | 0.3 – 20 | Decay time (RT60) |
| `reverb.damping` | 0.5 | 0 – 1 | How much faster the highs fade than the lows |
| `reverb.predelay_ms` | 25 | 0 – 250 | Gap before the reverb starts |
| `master.gain_db` | -4 | -24 – 12 | Output gain before the soft limiter |
| `master.lowpass_hz` | 11000 | 1000 – 20000 | Gentle top-end roll-off for warmth |
| `master.saturation` | 0 | 0 – 1 | Tape-style saturation of the whole mix |
| `master.bitcrush_bits` | 0 | 0 – 16 | Reduce to this many bits for sampler grit (0 = off) |
| `master.downsample_hz` | 0 | 0 – 48000 | Sample-and-hold to this rate for old-sampler aliasing (0 = off) |
| `master.fade_in` | 8 | 0 – 600 | Fade-in seconds at the start |
| `master.fade_out` | 25 | 0 – 600 | Fade-out seconds at the end |
| `pad.patch` | soft_pad | see `--list-params` | Synth patch (patches/*.json); it fills in pad.synth.* |
| `bass.patch` | round_bass | see `--list-params` | Synth patch (patches/*.json); it fills in bass.synth.* |
| `bells.patch` | fm_bell | see `--list-params` | Synth patch (patches/*.json); it fills in bells.synth.* |
| `lead.patch` | flute | see `--list-params` | Synth patch (patches/*.json); it fills in lead.synth.* |
| `kick.patch` | kick_soft | see `--list-params` | Synth patch (patches/*.json); it fills in kick.synth.* |
| `snare.patch` | snare_lofi | see `--list-params` | Synth patch (patches/*.json); it fills in snare.synth.* |
| `hats.patch` | hat_closed | see `--list-params` | Synth patch (patches/*.json); it fills in hats.synth.* |
| `openhat.patch` | openhat_909 | see `--list-params` | Synth patch (patches/*.json); it fills in openhat.synth.* |
| `perc.patch` | rim_909 | see `--list-params` | Synth patch (patches/*.json); it fills in perc.synth.* |

---

## Reference: synth settings

You can use these in a patch file, or per layer as `pad.synth.<name>`,
`bass.synth.<name>` or `bells.synth.<name>`. "Default" is the engine default
a patch starts from.

| Setting | Default | Range | What it does |
|---|---|---|---|
| `saw` | 0 | 0 – 1 | Sawtooth level: bright, string-like |
| `square` | 0 | 0 – 1 | Square/pulse level: hollow, woody |
| `pulse_width` | 0.5 | 0.05 – 0.95 | Square's duty cycle; away from 0.5 gets thinner and nasal |
| `triangle` | 0 | 0 – 1 | Triangle level: soft, flute-like |
| `sine` | 1 | 0 – 1 | Sine level (the FM carrier when fm_index > 0) |
| `sub` | 0 | 0 – 1 | Sine one octave below: weight |
| `noise` | 0 | 0 – 1 | White noise level: breath, bow or tape hiss |
| `fm_ratio` | 1 | 0.25 – 12 | Modulator pitch / note pitch. 1 = electric piano, 2 = hollow, 3.5 = bell, 7 = glassy |
| `fm_index` | 0 | 0 – 10 | FM amount; 0 = pure sine, higher = more overtones |
| `fm_index_decay` | 0 | 0 – 60 | Seconds for the FM shimmer to die away (0 = it stays) |
| `unison` | 1 | 1 – 7 | Copies of each note, detuned against each other |
| `detune_cents` | 0 | 0 – 60 | Spread between the outermost unison voices |
| `width` | 0 | 0 – 1 | Stereo spread of the unison voices |
| `drift_cents` | 0 | 0 – 40 | Slow random pitch wander per voice (analog / tape feel) |
| `vibrato_hz` | 5 | 0.05 – 12 | Vibrato speed |
| `vibrato_cents` | 0 | 0 – 60 | Vibrato depth |
| `pitch_env_semitones` | 0 | 0 – 48 | Each note starts this far above its pitch and drops to it: kick and 808 punch |
| `pitch_env_decay` | 0.05 | 0.001 – 2 | Seconds for that pitch drop |
| `glide_seconds` | 0.08 | 0.005 – 2 | Slide time when a note glides in from the previous pitch (808 slides) |
| `drive` | 0 | 0 – 1 | Saturation before the filter: from warmth to grit |
| `filter` | off | `off`, `lowpass`, `bandpass`, `highpass` | Filter type |
| `cutoff_hz` | 2000 | 20 – 20000 | Filter cutoff at middle C |
| `resonance` | 0.1 | 0 – 0.95 | Emphasis at the cutoff; near 1 it whistles |
| `key_track` | 0.5 | 0 – 1 | How much the cutoff follows the pitch: 0 = fixed, 1 = fully |
| `filter_env_octaves` | 0 | -6 – 6 | How far the filter envelope moves the cutoff (negative closes it) |
| `filter_attack` | 1 | 0.001 – 60 | Seconds for the filter envelope to reach its peak |
| `filter_decay` | 4 | 0.01 – 120 | Seconds for the filter envelope to fall back |
| `filter_lfo_hz` | 0.05 | 0.005 – 10 | Speed of the slow filter sweep |
| `filter_lfo_octaves` | 0 | 0 – 4 | Depth of the slow filter sweep |
| `attack` | 0.01 | 0.001 – 60 | Seconds to fade in |
| `decay` | 1 | 0.01 – 60 | Seconds to fall from the peak to the sustain level |
| `sustain` | 1 | 0 – 1 | Level held while the note is held (0 = a plucked/struck note) |
| `release` | 1 | 0.01 – 60 | Seconds to fade out after the note ends |
| `breath_hz` | 0.1 | 0.01 – 5 | Speed of the slow volume swell (each note varies it a little) |
| `breath_depth` | 0 | 0 – 1 | Depth of the slow volume swell |
| `transpose` | 0 | -48 – 48 | Shift every note by this many semitones (lets a drum patch carry its own tuning) |
| `gain_db` | 0 | -24 – 24 | Patch loudness trim, so patches can be swapped at the same level |

---

## Project layout

```
ambient-gen/
├── ambient.py        the script: scales, settings, composing, rendering, command line
├── synth.py          the synth engine: PARAMS, oscillators, filter, envelopes
├── groove.py         beats: grid, sections, drum patterns, 808 line, vinyl, sidechain, crusher
├── midi_out.py       MIDI: play layers live on a hardware synth, export .mid files
├── ambient_io.py     plumbing: streaming WAV/RF64 writer, @@progress events
├── patches/          synth sounds (one JSON per patch)
├── presets/          moods (one JSON per preset)
├── renders/          output WAVs + sidecars (git-ignored)
└── requirements.txt  numpy, scipy, numba (+ mido, python-rtmidi for MIDI)
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: numba` | `pip3 install -r requirements.txt` |
| `MIDI needs two packages` | `pip3 install mido python-rtmidi` |
| `no MIDI output matching 'FM-1'` | Is the synth plugged in and switched on? `--list-midi-ports` shows what the computer sees; pass any part of that name |
| The synth and backing track drift apart by a fixed amount | Use `--midi-offset-ms` (e.g. `40` or `-40`) to line them up |
| The first render pauses for a moment before starting | numba is compiling the filter. The compiled code is cached in `__pycache__/`, so later runs start immediately. |
| `unknown setting 'pad.brightness'` | That setting is from before the synth engine. Use `pad.synth.cutoff_hz` (and the other `pad.synth.*` settings) instead. Sidecars from that version can be replayed by checking out commit `af7ebda`. |
| Render is slow | Lower `pad.synth.unison` or `harmony.chord_notes`, or use a patch without a filter. Unison voices × chord notes is the main cost. |
| Too quiet or too loud | Change `master.gain_db`. Peaks are softened by the limiter rather than clipped, and the sidecar's `samples_softened_by_limiter` tells you how much it is working. |
| A patch is much louder or quieter than the others | Change its `gain_db` in the patch file. |
| A player won't open a very long file | It's RF64 (over 4 GB). Use `--set bit_depth=16`, render shorter pieces, or convert it with ffmpeg. |
