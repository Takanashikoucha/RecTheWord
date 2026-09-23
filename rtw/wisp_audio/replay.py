"""文件回放注入源（复刻 Wisp 的 MediaSource / WavSource 角色）。

把音频文件按实时时钟喂进 FrameChannel，与真实采集（WASAPI mic / loopback）
同接口（AudioSource）。用途有二：
  1. 非 Windows 环境（开发机 / 测试）端到端验证整条链路
     （文件 → 通道 → DSP → VAD），对应 Wisp 的 hardware-gated 测试哲学。
  2. 生产环境切回真实采集时的对照基准。

读取 → 降混单声道 → 抗混叠重采样到 16k → 按 chunk_ms 切帧 → 按 speed 倍速
实时送进 FrameChannel。播完（或 loop=False 走完一遍）关闭通道，使下游消费者
看到流结束。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .channel import FrameChannel
from .dsp import Resampler, TARGET_SAMPLE_RATE
from .frame import AudioFrame, AudioSource, AudioSourceInfo

log = logging.getLogger(__name__)

#: 采集→管线帧通道的有界容量（同 mic/loopback）。
FRAME_CHANNEL_CAPACITY = 1024

#: 每帧时长（毫秒）——与 VAD 的 32ms 窗对齐的倍数（此处 100ms，10 窗）。
CHUNK_MS = 100


class ReplaySource:
    """按 speed 倍速把音频文件推送进 FrameChannel 的 AudioSource。"""

    def __init__(self, path: str | Path, *,
                 target_sr: int = TARGET_SAMPLE_RATE,
                 speed: float = 1.0, loop: bool = False) -> None:
        self.path = Path(path)
        self.target_sr = target_sr
        self.speed = speed
        self.loop = loop
        self._rx = FrameChannel(FRAME_CHANNEL_CAPACITY)
        self._stop = threading.Event()
        self._handle: threading.Thread | None = None
        self.done = threading.Event()

    def start(self) -> None:
        self._handle = threading.Thread(target=self._run, daemon=True,
                                        name=f"wisp:replay:{self.path.name}")
        self._handle.start()

    def join(self, timeout: float | None = None) -> None:
        if self._handle:
            self._handle.join(timeout)

    def _frames(self) -> list[AudioFrame]:
        """读文件 → 降混单声道 → 抗混叠重采样到 target_sr → 切帧。"""
        data, sr = sf.read(str(self.path), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        resampler = Resampler(self.target_sr)
        frame = AudioFrame(data.tolist(), int(sr), 1, 0.0)
        mono = resampler.process(frame)
        # 按 CHUNK_MS 切帧
        per = max(1, self.target_sr * CHUNK_MS // 1000)
        out: list[AudioFrame] = []
        for i in range(0, len(mono), per):
            chunk = mono[i:i + per]
            ts = (i / self.target_sr) if self.target_sr else 0.0
            out.append(AudioFrame(chunk, self.target_sr, 1, ts))
        return out

    def _run(self) -> None:
        try:
            frames = self._frames()
        except Exception as e:  # noqa: BLE001
            log.warning("replay 读取失败：%s", e)
            self._rx.close()
            self.done.set()
            return
        per_dur = (CHUNK_MS / 1000.0) / self.speed
        while not self._stop.is_set():
            for f in frames:
                if self._stop.is_set():
                    return
                self._rx.send(f)
                time.sleep(per_dur)
            if not self.loop:
                break
        self._rx.close()
        self.done.set()

    def stop(self) -> None:
        self._stop.set()

    # ---- AudioSource 接口 ----
    def info(self) -> AudioSourceInfo:
        return AudioSourceInfo("file", self.path.name)

    def next_frame(self) -> AudioFrame | None:
        return self._rx.recv()
