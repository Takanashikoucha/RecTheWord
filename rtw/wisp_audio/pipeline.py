"""把「采集源 → 通道 → DSP → int16 PCM → VAD」串起来的薄胶水层。

这是 Wisp 音频获取链路的 Python 落地：设备枚举（devices）选出采集源
（mic / loopback / replay），采集源在自己线程里把 16k 单声道帧送进
FrameChannel（有界 drop-oldest 传输），本模块的消费端从通道取帧、（必要时）
再做抗混叠重采样与 ASR 归一化、转成 VAD 要的 int16 mono 16k PCM 喂给
SileroVad.feed()。

设计为可插拔：任何 AudioSource 都能接进来；非 Windows 下用 ReplaySource 即可
端到端跑通（硬件门控的真机采集在 Windows 上用 MicSource / WasapiLoopbackSource）。
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from .channel import FrameChannel
from .dsp import Resampler, TARGET_SAMPLE_RATE
from .frame import AudioFrame, AudioSource
from .normalize import normalize_for_asr

log = logging.getLogger(__name__)


def to_int16_pcm(samples: list[float]) -> bytes:
    """把 f32 样本（[-1,1]）转成 int16 PCM 字节（小端）。"""
    if not samples:
        return b""
    arr = np.asarray(samples, dtype=np.float32)
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767).astype("<i2").tobytes()


class VadFeeder:
    """从 AudioSource 消费帧，转成 int16 PCM 喂给 VAD 的桥接器。

    用法：
        feeder = VadFeeder(source, vad, lane="mic", normalize=True)
        feeder.start()          # 起消费线程
        ... 运行中 ...
        feeder.stop()          # 停止并 join

    每取到一帧：（若源速率 ≠ 16k）抗混叠重采样 → （可选）normalize_for_asr
    → 转 int16 PCM → vad.feed(pcm, lane)，把切出的语音段交给 on_segment 回调。
    """

    def __init__(self, source: AudioSource, vad, *, lane: str = "mic",
                 normalize: bool = True,
                 on_segment=None) -> None:
        self.source = source
        self.vad = vad
        self.lane = lane
        self.normalize = normalize
        self.on_segment = on_segment
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._resampler = Resampler(TARGET_SAMPLE_RATE)
        self.n_segments = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"wisp:vadfeeder:{self.lane}")
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                frame = self.source.next_frame()
                if frame is None:
                    break  # 源结束
                samples = frame.samples
                if frame.sample_rate != TARGET_SAMPLE_RATE:
                    samples = self._resampler.process(frame)
                if self.normalize:
                    samples = normalize_for_asr(samples, TARGET_SAMPLE_RATE)
                pcm = to_int16_pcm(samples)
                if pcm:
                    for seg in self.vad.feed(pcm, self.lane):
                        self.n_segments += 1
                        if self.on_segment:
                            self.on_segment(seg)
        except Exception as e:  # noqa: BLE001
            log.warning("VadFeeder 消费异常：%s", e)
        finally:
            # 冲刷 VAD 内部残余（尽力而为）
            try:
                for seg in self.vad.feed(b"", self.lane):
                    self.n_segments += 1
                    if self.on_segment:
                        self.on_segment(seg)
            except Exception:  # noqa: BLE001
                pass
