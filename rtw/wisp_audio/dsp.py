"""音频 DSP：通道降混与采样率转换到引擎输入格式（复刻 Wisp wisp-audio/src/dsp.rs）。

- downmix_to_mono：交错多通道按通道组平均降混到单声道。
- resample_linear：线性插值重采样（质量一般但零依赖，初版够用）。
- to_mono_16k：整段（文件）转换——降混 + 线性重采样到 16k。
- Resampler：流式、抗混叠的重采样器（windowed-sinc 低通 + 历史拼接），
  用于实时流——线性重采样会把高于输出奈奎斯特的成分折叠成混叠噪声，
  本类在下采样前先做有状态的低通滤波，使 >~0.45·to_rate 的成分不混叠，
  且同一流的连续帧无缝衔接。每流持有一个，按序喂帧。
"""
from __future__ import annotations

import math

from .frame import AudioFrame

#: ASR 引擎期望的采样率（Hz）：16 kHz、单声道。
TARGET_SAMPLE_RATE = 16_000

#: 文件源发出的默认帧长（毫秒）。
FRAME_CHUNK_MS = 100


def downmix_to_mono(samples: list[float], channels: int) -> list[float]:
    """把交错多通道样本按通道组平均降混到单声道。"""
    ch = max(1, channels)
    if ch == 1:
        return list(samples)
    out: list[float] = []
    for i in range(0, len(samples) - (len(samples) % ch), ch):
        group = samples[i:i + ch]
        out.append(sum(group) / len(group))
    return out


def resample_linear(input_: list[float], from_rate: int, to_rate: int) -> list[float]:
    """线性插值把单声道信号从 from_rate 重采样到 to_rate。

    线性插值质量中等但零依赖，初版够用；可在不破坏调用方的前提下换成更高
    质量的采样器（见 Resampler）。
    """
    if from_rate == 0 or to_rate == 0 or not input_:
        return []
    if from_rate == to_rate:
        return list(input_)

    ratio = to_rate / from_rate
    out_len = round(len(input_) * ratio)
    last = len(input_) - 1

    out: list[float] = []
    for i in range(out_len):
        src_pos = i / ratio
        idx = int(math.floor(src_pos))
        frac = src_pos - idx
        a = input_[min(idx, last)]
        b = input_[min(idx + 1, last)]
        out.append(a + (b - a) * frac)
    return out


def to_mono_16k(frame: AudioFrame) -> list[float]:
    """把任意速率/通道数的 AudioFrame 转成 TARGET_SAMPLE_RATE 单声道 f32。

    无状态、零依赖，适合整段（文件）转换。实时流请用 Resampler（抗混叠 +
    帧间无缝）。
    """
    mono = downmix_to_mono(frame.samples, frame.channels)
    return resample_linear(mono, frame.sample_rate, TARGET_SAMPLE_RATE)


class Resampler:
    """流式、抗混叠的单声道目标速率转换器。

    普通线性重采样（to_mono_16k）会把高于输出奈奎斯特的成分折叠回语音频带
    成为混叠噪声，ASR 引擎得与之对抗。本类在下采样前施加有状态的
    windowed-sinc 低通，使 >~0.45·to_rate 的成分不混叠，且同一流的连续帧
    无缝衔接。每流持有一个，按序喂帧；源速率确定后惰性设计滤波器一次，
    速率变化则重建。

    目标通常是 TARGET_SAMPLE_RATE（本地 ASR 速率）；接受更丰富音频的云引擎
    可指向更高速率（如 24 kHz）以保留 16 kHz 会丢掉的高频辅音带。
    """

    def __init__(self, to_rate: int) -> None:
        self.to_rate = to_rate
        self.from_rate = 0
        self.coeffs: list[float] = []
        self.history: list[float] = []

    def process(self, frame: AudioFrame) -> list[float]:
        """把 frame 降混到单声道并转到目标速率，下采样时抗混叠。"""
        mono = downmix_to_mono(frame.samples, frame.channels)

        # 上采样/同速率不会混叠，完全跳过滤波器（及其延迟）。
        if frame.sample_rate <= self.to_rate:
            return resample_linear(mono, frame.sample_rate, self.to_rate)

        if self.from_rate != frame.sample_rate:
            self.from_rate = frame.sample_rate
            self.coeffs = _design_lowpass(frame.sample_rate, self.to_rate)
            self.history = [0.0] * max(0, len(self.coeffs) - 1)

        filtered = self._low_pass(mono)
        return resample_linear(filtered, frame.sample_rate, self.to_rate)

    def _low_pass(self, input_: list[float]) -> list[float]:
        """有状态 FIR 卷积：卷积 [历史 ++ 输入]，保留新尾部作为下次调用的
        历史，使流在帧边界处保持连续。"""
        taps = len(self.coeffs)
        if taps == 0 or not input_:
            return list(input_)
        buf = self.history + input_
        out: list[float] = []
        for i in range(len(input_)):
            s = 0.0
            for c, x in zip(self.coeffs, buf[i:]):
                s += c * x
            out.append(s)
        self.history = buf[len(buf) - (taps - 1):] if taps > 1 else []
        return out


def _design_lowpass(from_rate: int, to_rate: int) -> list[float]:
    """单位直流增益的 windowed-sinc 低通 FIR，截止略低于 to_rate/2，采样率
    from_rate。"""
    TAPS = 63  # 奇数 → 对称、恒定（整数）群延迟

    # 截止取 0.45·to_rate，略低于 0.5·to_rate 的输出奈奎斯特，留出过渡带。
    fc = 0.45 * to_rate / from_rate  # 每输入样本的周期数
    mid = (TAPS - 1) / 2.0

    h: list[float] = []
    for i in range(TAPS):
        x = i - mid
        if x == 0.0:
            sinc = 2.0 * fc
        else:
            sinc = math.sin(2.0 * math.pi * fc * x) / (math.pi * x)
        hann = 0.5 - 0.5 * math.cos(2.0 * math.pi * i / (TAPS - 1))
        h.append(sinc * hann)

    s = sum(h)
    if s != 0.0:
        h = [c / s for c in h]
    return h


def chunk_into_frames(samples: list[float], sample_rate: int, channels: int,
                      chunk_ms: int) -> list[AudioFrame]:
    """把解码后的交错 samples 切成 chunk_ms 长的 AudioFrame，各盖一个相对起点
    的偏移。文件源（WAV 与媒体解码器）共用。"""
    ch = max(1, channels)
    instants_per_chunk = max(1, (sample_rate * chunk_ms // 1000))
    per_chunk = instants_per_chunk * ch

    frames: list[AudioFrame] = []
    instants_emitted = 0
    for i in range(0, len(samples), per_chunk):
        chunk = samples[i:i + per_chunk]
        timestamp = (instants_emitted / sample_rate) if sample_rate else 0.0
        frames.append(AudioFrame(chunk, sample_rate, ch, timestamp))
        instants_emitted += len(chunk) // ch
    return frames
