"""Tests for stereo splitting and chunk buffering (pure numpy, no Qt)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rectheword.pipeline.split import split_stereo_f32le


def test_split_stereo_full():
    # 2 channels, 3 frames
    left = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    right = np.array([4.0, 5.0, 6.0], dtype=np.float32)
    inter = np.stack([left, right], axis=1).reshape(-1)
    data = inter.tobytes()
    channels = split_stereo_f32le(data, 2)
    assert channels is not None
    assert len(channels) == 2
    np.testing.assert_allclose(channels[0], left)
    np.testing.assert_allclose(channels[1], right)


def test_split_stereo_partial_returns_none():
    # 1.5 frames worth (odd byte length relative to a full frame)
    data = np.zeros(1, dtype=np.float32).tobytes() * 3  # 3 samples = 1.5 frames
    channels = split_stereo_f32le(data, 2)
    # 3 samples / 2 channels = 1.5 -> 1 full frame
    assert channels is not None
    assert len(channels[0]) == 1


def test_split_empty():
    assert split_stereo_f32le(b"", 2) is None


def test_chunking_accumulates():
    # Simulate feeding small pieces until a 9600-sample chunk forms.
    CHUNK = 9600
    buf = np.zeros(0, dtype=np.float32)
    emitted = []
    for piece in range(5):
        # each piece: 4000 samples on left, 4000 on right -> 4000 frames
        mono = np.full(4000, piece, dtype=np.float32)
        buf = np.concatenate([buf, mono])
        while len(buf) >= CHUNK:
            out, buf = buf[:CHUNK], buf[CHUNK:]
            emitted.append(out)
    # 5*4000 = 20000 -> two full chunks of 9600, 800 leftover
    assert len(emitted) == 2
    assert len(buf) == 800
