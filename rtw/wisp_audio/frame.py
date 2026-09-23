"""音频帧与 AudioSource 抽象（复刻 Wisp wisp-core/src/audio.rs）。

AudioFrame 承载一段 PCM 音频：交错 f32 样本（取值 [-1.0, 1.0]）、采样率、
通道数、相对流起点的偏移。AudioSource 是任何 PCM 生产者（麦克风 / 系统环回 /
文件读取）的统一接口——新的采集源实现这个接口即可插入，其余不变。

与 Wisp 的差异：Python 无 trait，用 ABC 表达；timestamp 用 float 秒（Wisp 用
Duration），duration() 据此计算。
"""
from __future__ import annotations

import abc


class AudioFrame:
    """一段 PCM 音频：交错 f32 样本（[-1.0, 1.0]）。"""

    __slots__ = ("samples", "sample_rate", "channels", "timestamp")

    def __init__(self, samples: list[float] | "tuple[float, ...]",
                 sample_rate: int, channels: int, timestamp: float) -> None:
        self.samples = list(samples)
        self.sample_rate = sample_rate
        self.channels = max(1, channels)
        self.timestamp = timestamp

    @property
    def frame_count(self) -> int:
        """样本瞬间数（= 每通道样本数）。"""
        return len(self.samples) // self.channels

    def duration(self) -> float:
        """本帧包含的音频时长（秒）。"""
        if self.sample_rate == 0:
            return 0.0
        return self.frame_count / self.sample_rate

    def is_empty(self) -> bool:
        return len(self.samples) == 0

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (f"AudioFrame(n={len(self.samples)}, sr={self.sample_rate}, "
                f"ch={self.channels}, ts={self.timestamp:.3f}s)")


class AudioSourceInfo:
    """描述一个 AudioSource 的元信息（展示与路由用）。"""

    __slots__ = ("kind", "name")

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind  # "mic" | "sys" | "file"
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"AudioSourceInfo(kind={self.kind}, name={self.name!r})"


class AudioSource(abc.ABC):
    """AudioFrame 的生产者。

    实现方在自己的采集线程上运行；next_frame() 阻塞到有音频可取，流结束时
    返回 None。
    """

    @abc.abstractmethod
    def info(self) -> AudioSourceInfo:
        """描述本源的元信息。"""

    @abc.abstractmethod
    def next_frame(self) -> AudioFrame | None:
        """阻塞取下一帧；流结束返回 None。"""
