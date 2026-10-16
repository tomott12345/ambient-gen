# ambient-gen web app (Go)

A browser front end for ambient-gen. Go handles the form, the job queue,
live progress and downloads; Python does the audio work:

- **Song:** `ambient.py` composes and renders the WAV.
- **Release** (optional): [process-audio](https://github.com/tomott12345/process-audio)'s
  music pipeline masters the song (WAV/FLAC/MP3), cuts a Short/Reel around
  its strongest moment, renders visualizer videos (16:9 YouTube, 9:16
  Short/Reel, 1:1 Instagram) and writes captions.

## Using it

1. **Pick a sound:** the presets, grouped into Ambient, Lo-fi beats and
   Techno.
2. **Shape it:** the **Easy** section has the everyday settings: title,
   length, key, scale, tempo and drum kit for beats, room size and chord
   length for ambient, and the seed. **Advanced** (collapsed) has every
   one of ambient-gen's ~440 settings, grouped by layer, with search and an
   "only changed" filter. Each starts at the preset's value; changed ones
   are highlighted with a *reset* link. Changing a layer's patch updates
   its synth settings, just as the command line does.
3. **Master & videos:** switch the release stage on or off, pick the music
   type (it follows the preset), what to make, and the audio formats. The
   options come from process-audio itself, with the same Basic/Advanced
   split (loudness target, Short length, visual style, palette, glow,
   preview length...). Captions and tags name the song's starting key and
   scale (e.g. "F dorian"), not process-audio's estimate. You can change it
   under Advanced → Song key, or clear it to use the estimate. This needs
   a process-audio version with `--key`; older ones just get the estimate.
4. **Also export:** an **MPC project** (a zip that opens in MPC 2 / MPC
   Beats: the sequence, a drum program with the song's drum sounds, and
   keygroup instruments sampled from its synth) and/or a **MIDI file**
   (drums on one track), and/or **Strudel** code with an *Open in Strudel*
   button (strudel.cc, the browser live-coding environment).
5. **Show commands** previews exactly what will run; **Create song** starts
   it. Progress streams live for both stages, and you can cancel. Results
   come with players, per-file downloads and a zip. **Edit & run again**
   loads a song back into the form; **Same settings, new seed** makes a new
   piece with the same sound.

The **Songs** panel lists every job, survives restarts, and can delete old
ones and their files.

## Run it with Docker (easiest)

From the ambient-gen folder:

```bash
docker compose up -d --build
```

Then open http://127.0.0.1:8766. Songs live in the `ambient-gen-data`
volume, so they survive restarts and rebuilds. Or without compose:

```bash
docker build -t ambient-gen .
docker run -d --name ambient-gen -p 127.0.0.1:8766:8766 -v ambient-gen-data:/data ambient-gen
```

The image is built on the published `ghcr.io/tomott12345/process-audio`
image, which brings ffmpeg, fonts and the release pipelines. To build on a
local process-audio checkout instead:

```bash
docker build -t process-audio ../process-audio
docker build --build-arg PROCESS_AUDIO_IMAGE=process-audio -t ambient-gen .
```

Keep the port on `127.0.0.1` as shown: the app has no login, and anyone who
can reach the port can start jobs. It runs next to process-audio's own app
(port 8765) without a clash.

## Run it directly

```bash
cd web
go build -o bin/agweb ./cmd/agweb
bin/agweb -python /path/to/python3          # http://127.0.0.1:8766
```

Needs Go 1.24+, a Python with ambient-gen's dependencies (and, for the
release stage, process-audio's), and `ffmpeg` on PATH.

| Flag | Default | Notes |
|---|---|---|
| `-addr` | `127.0.0.1:8766` | Loopback only (no login) |
| `-ambient` | found by walking up | Folder holding `ambient.py` |
| `-process-audio` | `$PROCESS_AUDIO`, or `../process-audio` next to ambient-gen | Without it, the release stage is switched off |
| `-python` | `python3` | Interpreter with numpy/scipy/numba (+ process-audio's librosa etc.) |
| `-data` | `<ambient>/web/data` | Jobs and their files (git-ignored) |
| `-workers` | `1` | Jobs at once; renders and videos already use several cores |
| `-container` | off | Inside a container: allow listening on all interfaces |

## API

| Method & path | Purpose |
|---|---|
| `GET /api/capabilities` | `compose` (ambient.py `--describe`: settings, presets with resolved values, patches with values, kits) and `release` (process-audio's music pipeline, or why it's unavailable) |
| `POST /api/plan` | Job body → the commands it would run and the seed, nothing rendered |
| `POST /api/jobs` | `{title, preset, values, release?: {type, values, outputs, formats}, mpc?, midi?, strudel?}` → queued job |
| `GET /api/jobs` · `GET /api/jobs/{id}` · `DELETE` | List / state / remove a finished job |
| `POST /api/jobs/{id}/cancel` | Stops the running stage and every process it started |
| `GET /api/jobs/{id}/events` | Server-sent events: `job` snapshots (steps, progress, files) and `log` lines |
| `GET /api/jobs/{id}/files/{n}` | Download output `n` (Range supported; `?inline=1` to play in the page) |
| `GET /api/jobs/{id}/zip` | All outputs as one zip (`song/` and `release/` folders) |

`values` holds only the settings the user changed (dotted paths, as
`--set` takes them); each is checked against its type, range and choices.
A random seed is picked on the server when none is given, so every job
records the seed that recreates it. Every POST/DELETE must send
`X-Requested-With: ambient-gen` and, if the browser sends an `Origin`, it
must be this server's own.

## How it fits together

- `internal/describe`: runs `ambient.py --describe` and
  `release_bridge.py caps`, caches both, and reloads them when a script,
  preset or patch changes. Validates compose values.
- `release_bridge.py`: the release command is built by process-audio's own
  `build_argv()`, so its rules live in one place.
- `internal/jobs`: the queue; each job runs its song stage, then its
  release stage, folding both stages' `@@progress` events into one step
  list and their manifests into one file list. State is persisted to
  `job.json`. Only manifest files that are real files inside the job folder
  can be downloaded.
- `internal/runner`: process-group launch and cancel, `@@progress` parsing
  (from process-audio/web).
- `internal/httpapi`: the routes above, security headers, the CSRF check.
- `ui/static`: `index.html`, `app.css` (shared look with process-audio)
  and `app.js`, embedded into the binary. Plain ES module, no framework or
  build step. Everything on screen is built from `/api/capabilities`;
  `resolve()` in `app.js` ports ambient.py's `load_settings()` so the form
  shows exactly the values a song renders with. Rebuild the binary after
  editing them.

## Tests

```bash
go test -short ./...                              # unit tests
AG_PYTHON=/path/to/python3 go test ./...          # plus a real song through the job manager
```
