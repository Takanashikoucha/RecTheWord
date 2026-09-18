"""回放注入源：把 TTS 合成的音频文件按实时时钟喂进 RingBuffer。

与真实采集（WASAPI mic / loopback）同接口，使非 Windows 环境也能端到端
测试整条链路（方案 §8）。生产环境切回 wasapi 即可。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .ring_buffer import RingBuffer


class ReplaySource:
    """按 speed 倍速把音频文件推送进 RingBuffer；start() 启动 daemon 线程。"""

    def __init__(self, ring: RingBuffer, path: str | Path,
                 target_sr: int = 16000, speed: float = 1.0,
                 loop: bool = False) -> None:
        self.ring = ring
        self.path = Path(path)
        self.target_sr = target_sr
        self.speed = speed
        self.loop = loop
        self.done = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"replay:{self.path.name}")
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        data, sr = sf.read(str(self.path), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != self.target_sr:
            n = int(len(data) * self.target_sr / sr)
            xp = np.linspace(0.0, len(data) - 1, n)
            data = np.interp(xp, np.arange(len(data)), data).astype(np.float32)
        pcm16 = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        frame = 1024  # 64ms @16k
        while not self._stop.is_set():
            t0 = time.monotonic()
            for i in range(0, len(pcm16), frame * 2):
                if self._stop.is_set():
                    return
                self.ring.write(pcm16[i:i + frame * 2])
                time.sleep((frame / self.target_sr) / self.speed)
            elapsed = time.monotonic() - t0
            if not self.loop:
                self.done.set()
                return
            # 循环模式下补偿漂移
            ideal = len(pcm16) / (frame * 2) * (frame / self.target_sr) / self.speed
            time.sleep(max(0.0, ideal - elapsed))

    def stop(self) -> None:
        self._stop.set()
