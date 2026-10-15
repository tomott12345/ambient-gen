#!/usr/bin/env python3
"""
release_bridge.py -- lets the Go web app use process-audio's music pipeline
(mastering, Short/Reel clip, visualizer videos, captions, WAV/FLAC/MP3)
without re-implementing any of it.

    python3 release_bridge.py --process-audio DIR caps
        -> process-audio's capabilities, trimmed to what the web app needs:
           the music pipeline, the audio formats, the dependency report.

    python3 release_bridge.py --process-audio DIR argv  < request.json
        -> {"argv": [...]} built by process-audio's own build_argv(), or
           {"error": "..."} if the request isn't allowed.
           request: {"type", "values", "outputs", "formats", "input", "out_dir", "plan"}

process-audio stays the single source of truth for its options and
command lines; this file only forwards to it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(process_audio: str):
    root = Path(process_audio).resolve()
    if not (root / "pipeline_capabilities.py").exists():
        raise SystemExit(json.dumps({"error": f"process-audio not found at {root}"}))
    sys.path.insert(0, str(root))
    import pipeline_capabilities  # noqa: E402  (from process-audio)
    return pipeline_capabilities


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--process-audio", required=True)
    ap.add_argument("command", choices=["caps", "argv"])
    args = ap.parse_args()
    pc = load(args.process_audio)
    caps = pc.capabilities()

    if args.command == "caps":
        music = next(p for p in caps["pipelines"] if p["id"] == "music")
        print(json.dumps({"version": caps["version"], "dependencies": caps["dependencies"],
                          "formats": caps["formats"], "run": caps["run"], "pipeline": music}))
        return 0

    req = json.load(sys.stdin)
    try:
        argv = pc.build_argv(caps, "music", req.get("type"), req.get("values") or {}, req.get("outputs") or [],
                             req.get("formats") or [], req["input"], req["out_dir"], plan=bool(req.get("plan")))
    except (ValueError, KeyError) as e:
        print(json.dumps({"error": str(e)}))
        return 0
    print(json.dumps({"argv": argv}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
