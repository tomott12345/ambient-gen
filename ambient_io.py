"""Plumbing for ambient.py: a streaming WAV writer and machine-readable
progress events. Nothing musical lives here.

Progress uses the same protocol as process-audio/pipeline_io.py, so the Go
runner there can drive this script unchanged. With --progress-json, single
lines like these are printed to stdout:

  @@progress {"event": "plan", "steps": [{"step": "compose", "label": "Composing"}, ...]}
  @@progress {"event": "step_start", "step": "render", "label": "Rendering audio"}
  @@progress {"event": "progress", "step": "render", "done": 120, "total": 1800}
  @@progress {"event": "step_end", "step": "render", "seconds": 41.2}
  @@progress {"event": "output", "kind": "master", "format": "wav24", "path": "..."}
  @@progress {"event": "manifest", "files": [{"kind": "master", "format": "wav24", "path": "..."}]}
"""

from __future__ import annotations

import json
import struct
import time
from contextlib import contextmanager

import numpy as np

PROGRESS_PREFIX = "@@progress "
_progress = False


def enable_progress() -> None:
    global _progress
    _progress = True


def emit(event: str, **fields) -> None:
    if _progress:
        print(PROGRESS_PREFIX + json.dumps({"event": event, **fields}), flush=True)


@contextmanager
def step(name: str, label: str):
    t0 = time.monotonic()
    emit("step_start", step=name, label=label)
    try:
        yield
    except BaseException as exc:
        emit("step_error", step=name, message=str(exc) or exc.__class__.__name__)
        raise
    emit("step_end", step=name, seconds=round(time.monotonic() - t0, 2))


class WavWriter:
    """Write a PCM WAV file one block at a time, so memory use doesn't grow
    with the length of the piece. Sizes go into the header on close().

    A plain WAV can't be larger than 4 GB, which is about 4 hours of 24-bit
    48 kHz stereo. Longer files are written as RF64, the standard extension
    for this case, which ffmpeg, Audacity, Reaper, Logic and most DAWs read.
    The 28-byte JUNK chunk keeps room in the header for RF64's size fields.
    """

    HEADER_BYTES = 12 + 36 + 24 + 8  # RIFF/WAVE + JUNK/ds64 + fmt + data header

    def __init__(self, path, sample_rate: int, bit_depth: int, channels: int = 2, seed: int = 0):
        if bit_depth not in (16, 24):
            raise ValueError("bit_depth must be 16 or 24")
        self.path = path
        self.sample_rate = sample_rate
        self.bit_depth = bit_depth
        self.channels = channels
        self.frames = 0
        self._dither = np.random.default_rng(seed)
        self._f = open(path, "wb")
        self._f.write(b"\0" * self.HEADER_BYTES)  # placeholder until close()

    def write(self, block: np.ndarray) -> None:
        """block: float array shaped (channels, frames), in the range -1..1."""
        x = np.ascontiguousarray(block.T)  # interleave L R L R ...
        if self.bit_depth == 16:
            # triangular dither hides the quantization of quiet ambient tails
            x = x * 32767 + self._dither.random(x.shape) - self._dither.random(x.shape)
            data = np.clip(np.round(x), -32768, 32767).astype("<i2").tobytes()
        else:
            ints = np.clip(np.round(x * 8388607), -8388608, 8388607).astype("<i4")
            data = ints.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()  # keep the low 3 bytes
        self._f.write(data)
        self.frames += x.shape[0]

    def close(self) -> None:
        if self._f.closed:
            return
        block_align = self.channels * self.bit_depth // 8
        data_size = self.frames * block_align
        riff_size = self.HEADER_BYTES - 8 + data_size
        rf64 = riff_size > 0xFFFFFFFF

        h = bytearray()
        h += b"RF64" if rf64 else b"RIFF"
        h += struct.pack("<I", 0xFFFFFFFF if rf64 else riff_size)
        h += b"WAVE"
        if rf64:
            h += b"ds64" + struct.pack("<IQQQI", 28, riff_size, data_size, self.frames, 0)
        else:
            h += b"JUNK" + struct.pack("<I", 28) + b"\0" * 28
        h += b"fmt " + struct.pack(
            "<IHHIIHH", 16, 1, self.channels, self.sample_rate,
            self.sample_rate * block_align, block_align, self.bit_depth,
        )
        h += b"data" + struct.pack("<I", 0xFFFFFFFF if rf64 else data_size)
        assert len(h) == self.HEADER_BYTES

        self._f.seek(0)
        self._f.write(h)
        self._f.close()
