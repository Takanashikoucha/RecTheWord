"""Tests for ffmpeg command construction."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rectheword.audio.capture import CaptureConfig, build_ffmpeg_args


def test_build_ffmpeg_args_dual():
    cfg = CaptureConfig(mic_device="Mic A", spk_device="Speaker B",
                        out_wav="/tmp/x.wav", enable_loopback=True)
    args = build_ffmpeg_args(cfg)
    joined = " ".join(args)
    # wasapi shared for both inputs
    assert joined.count("-f wasapi") >= 2
    assert "-shared" in args
    # loopback prefix applied to the speaker
    assert "loopback:Speaker B" in args
    # stereo out + mono mix (filter_complex is one argv item)
    assert "join=inputs=2:channel_layouts=stereo[out]" in joined
    assert "amix=inputs=2:normalize=0[mix]" in joined
    assert "-f" in args and "f32le" in args
    assert cfg.out_wav in args


def test_build_ffmpeg_args_mic_only():
    cfg = CaptureConfig(mic_device="Mic A", spk_device="", out_wav="/tmp/x.wav",
                        enable_loopback=False)
    args = build_ffmpeg_args(cfg)
    joined = " ".join(args)
    assert joined.count("-f wasapi") == 1
    assert "loopback:" not in joined
    assert "join=" not in joined


def test_loopback_prefix_only_once():
    cfg = CaptureConfig(mic_device="Mic", spk_device="loopback:Already",
                        out_wav="/tmp/x.wav")
    args = build_ffmpeg_args(cfg)
    assert "loopback:loopback:Already" not in args
    assert "loopback:Already" in args
