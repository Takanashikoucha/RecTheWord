"""Pure-numpy audio splitting (no Qt dependency, so it can be unit-tested)."""

from __future__ import annotations

from typing import List, Optional

import numpy as np


def split_stereo_f32le(data: bytes, channels: int = 2) -> Optional[List[np.ndarray]]:
    """Split interleaved float32 PCM into per-channel mono arrays.

    ``data`` length must be a multiple of ``channels * 4`` bytes; any trailing
    partial frame is dropped (it will be read on the next call).
    Returns a list of mono ``float32`` arrays (one per channel) or ``None`` if
    there isn't a full frame yet.
    """
    frame = channels * 4
    if len(data) < frame:
        return None
    n_frames = len(data) // frame
    raw = np.frombuffer(data[: n_frames * frame], dtype=np.float32)
    raw = raw.reshape(n_frames, channels)
    return [raw[:, c].copy() for c in range(channels)]
