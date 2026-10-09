# ambient-gen

Generative ambient music in pure Python (numpy + scipy), rendered straight to
a WAV file of any length. Every run is a new piece. Every piece can be
reproduced exactly from its seed.

```bash
pip3 install -r requirements.txt
python3 ambient.py                                  # 30 min, default sound
python3 ambient.py --minutes 240 --seed 7           # 4 hours, reproducible
python3 ambient.py --preset presets/night.json      # a saved sound
python3 ambient.py --set pad.brightness=0.2 --set bells.per_minute=2
```

Output goes to `renders/ambient_<preset>_<seed>.wav`. Next to it, a `.json`
file records the exact settings, the seed, and a few render stats. To render
the identical piece again, pass that `.json` back with `--preset`.

## What you hear

| Layer     | What it is |
|-----------|------------|
| **pad**   | Slow chords. Each note is 3 detuned wavetable voices spread across the stereo field, and each note "breathes" on its own cycle. |
| **bass**  | The chord root, a sine with a little 2nd harmonic. |
| **bells** | Sparse FM-synthesis notes. Their timing is random, in waves of activity. Each pitch is a short step from the last one, favouring chord notes. |
| **air**   | Band-passed stereo noise with a slow swell. |
| **reverb**| A long synthetic convolution hall. Each layer has its own send level. |

The chords come from a weighted random walk over the scale (`CHORD_MOVES`).
They can modulate to a neighbouring key every N minutes.

## Tweaking

Everything you can change lives in the `KNOBS` list near the top of
`ambient.py`. Each knob has a default, a range, and a one-line help text. You
have three ways to change one, from most permanent to least:

1. Edit the default in `KNOBS`.
2. Put it in a preset JSON. Presets are nested by group; see `presets/`.
3. Pass `--set group.name=value` on the command line.

To add a scale, add a line to `SCALES`. To make the harmony move differently,
change the weights in `CHORD_MOVES`. To add an instrument, write a method on
`Instruments` named after its knob group (it gets a `Note` and an `age`
array). Then append `Note`s with that `kind` in `compose()` and add
`<kind>.level` / `<kind>.reverb_send` knobs.

## Long files

Audio is rendered and written in 10-second blocks, so memory use stays flat
for any length. Rendering runs about 20-30x faster than realtime on an M-series
Mac, so an hour takes roughly 2-3 minutes. A plain WAV tops out at 4 GB, which
is about 4 h at 24-bit/48 kHz or 6.7 h at 16-bit/44.1 kHz. Longer renders are
written as RF64, which ffmpeg, Audacity and most DAWs read. Ctrl-C still
leaves a valid (shorter) WAV.

To master the output for YouTube, use the `process-audio` pipelines. The raw
render peaks around -5 dBFS and sits near -15 LUFS with the defaults.

## Front-end contract (for a Go UI)

The same protocol as `process-audio`, so `web/internal/runner` can drive this as-is:

- `python3 ambient.py --list-params` prints every knob as JSON:
  `path, default, type, min, max, choices, help`. A UI can build its sliders,
  toggles and dropdowns from this list instead of hard-coding them.
- To run a render, write the UI's values to a JSON file and call
  `ambient.py --preset that.json --out <path> --progress-json`. Repeated
  `--set path=value` flags work too.
- `--progress-json` prints `@@progress {...}` lines on stdout: `plan`,
  `step_start` / `step_end` for `compose` and `render`, `progress`
  (`done`/`total` blocks), `output`, and a final `manifest` listing the WAV
  and its settings `.json`. Human-readable progress goes to stderr.
- A bad setting exits non-zero with a one-line message on stderr.
