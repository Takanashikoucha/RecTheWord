"""FFmpeg capture engine.

One ``ffmpeg`` process captures BOTH the microphone and the speaker loopback
in WASAPI *shared* mode, and emits:

* **stdout** - interleaved **stereo f32le** PCM (left = mic, right = speaker
  loopback) at 16 kHz. The audio pipeline splits this into two mono streams.
* **wav file** - a **mixed mono** 16 kHz WAV (``amix`` of both), used as the
  input for offline speaker diarization at stop time.

Shared mode (``-shared`` / default for WASAPI) means we do not exclusively
own the device, so the rest of the system keeps working.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .devices import find_ffmpeg, wasapi_device_name

SAMPLE_RATE = 16000
# 600 ms of 16 kHz audio per streaming chunk (matches FunASR chunk stride)
CHUNK_SECONDS = 0.6
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_SECONDS)  # 9600


@dataclass
class CaptureConfig:
    mic_device: str
    spk_device: str
    out_wav: str
    sample_rate: int = SAMPLE_RATE
    # allow loopback to be disabled (e.g. headless test / no render device)
    enable_loopback: bool = True


def build_ffmpeg_args(cfg: CaptureConfig) -> List[str]:
    """Build the full ffmpeg argument list (no executable).

    Filter graph:
      [0:a] mic  -> mono 16k -> [m]
      [1:a] spk  -> mono 16k -> [s]
      [m][s] join -> stereo [out]   (stdout, f32le)
      [m][s] amix -> mono  [mix]    (wav file)
    """
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg not found; run scripts/download_ffmpeg_windows.py")

    args: List[str] = [
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "wasapi", "-shared", "1", "-sample_rate", str(cfg.sample_rate),
        "-i", wasapi_device_name(cfg.mic_device, "mic"),
    ]

    has_spk = cfg.enable_loopback and bool(cfg.spk_device)
    if has_spk:
        args += [
            "-f", "wasapi", "-shared", "1", "-sample_rate", str(cfg.sample_rate),
            "-i", wasapi_device_name(cfg.spk_device, "spk"),
        ]

    if has_spk:
        filt = (
            "[0:a]aformat=sample_rates=16000:channel_layouts=mono[m];"
            "[1:a]aformat=sample_rates=16000:channel_layouts=mono[s];"
            "[m][s]join=inputs=2:channel_layouts=stereo[out];"
            "[m][s]amix=inputs=2:normalize=0[mix]"
        )
        args += [
            "-filter_complex", filt,
            "-map", "[out]", "-ac", "2", "-ar", "16000", "-f", "f32le", "-",
            "-map", "[mix]", "-ac", "1", "-ar", "16000", "-f", "wav", cfg.out_wav,
        ]
    else:
        # microphone only (loopback disabled)
        filt = "[0:a]aformat=sample_rates=16000:channel_layouts=mono[out]"
        args += [
            "-filter_complex", filt,
            "-map", "[out]", "-ac", "2", "-ar", "16000", "-f", "f32le", "-",
            "-map", "[out]", "-ac", "1", "-ar", "16000", "-f", "wav", cfg.out_wav,
        ]

    return args


class CaptureEngine:
    """Manages the ffmpeg capture subprocess lifecycle."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._stderr_tail: List[str] = []
        self._lock = threading.Lock()
        self.last_error: str = ""
        self.started_at: Optional[float] = None
        # stdout is a file object the pipeline reads from (binary)
        self.stdout = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, cfg: CaptureConfig) -> None:
        if self.running:
            raise RuntimeError("capture already running")
        Path(cfg.out_wav).parent.mkdir(parents=True, exist_ok=True)
        args = build_ffmpeg_args(cfg)
        ff = find_ffmpeg()
        assert ff is not None
        cmd = [ff] + args
        # On Windows hide the console window.
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        self._stderr_tail = []
        self.last_error = ""
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self.stdout = self._proc.stdout
        self.started_at = time.monotonic()
        # spawn a stderr drain thread so the pipe never fills and blocks ffmpeg
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            for line in iter(self._proc.stderr.readline, b""):
                txt = line.decode("utf-8", errors="replace").rstrip()
                if not txt:
                    continue
                with self._lock:
                    self._stderr_tail.append(txt)
                    if len(self._stderr_tail) > 50:
                        self._stderr_tail.pop(0)
        except Exception:
            pass

    def error_tail(self, n: int = 20) -> str:
        with self._lock:
            return "\n".join(self._stderr_tail[-n:])

    def stop(self) -> None:
        proc = self._proc
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"stop error: {exc}"
        finally:
            for stream in (proc.stdout, proc.stderr, proc.stdin):
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass
            self._proc = None
            self.stdout = None

    def read_stdout(self, n: int) -> bytes:
        """Read up to ``n`` bytes from stdout (blocking)."""
        if not self.stdout:
            return b""
        data = self.stdout.read(n)
        if data is None:
            return b""
        return data
