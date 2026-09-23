"""面向 ASR 的音频预处理：去除隆隆声/直流偏置 + 电平归一化
（复刻 Wisp wisp-audio/src/preprocess.rs）。

真实会议音频的电平千差万别——安静的笔记本麦、过热的界面、带直流偏置或低频
隆隆声的录音。先把音频带到一致的健康电平，ASR 模型解码会更可靠。

链路刻意**温和**：一阶高通（去掉直流与亚语音隆隆声）+ 语音门控的电平归一化
到目标 RMS，并由峰值封顶保护，结果永不削顶。我们有意**不做**谱降噪——
Whisper 类模型在带噪音频上训练、对其鲁棒，而激进降噪会引入反而**降低**精度
的人工痕迹。是清理，不是手术。
"""
from __future__ import annotations

import math

#: 高通截止（Hz）：以下是直流偏置与隆隆声（空调、处理噪声），不是语音。男声
#: 基频从 ~85 Hz 起，故 70 Hz 是安全下限。
HPF_CUTOFF_HZ = 70.0

#: 目标 RMS 电平，线性（≈ -20 dBFS）。留有峰值余量的健康语音电平。
TARGET_RMS = 0.1

#: 峰值封顶，线性（≈ -1 dBFS）。增益后信号缩放使其峰值不超过此值——不削顶、
#: 不失真（单次线性缩放，非限幅器）。
PEAK_CEILING = 0.891

#: 最大增益（线性，≈ +20 dB）。限制安静片段被放大多少，以免把几乎听不见
#: 材料的噪声底吹爆。
MAX_GAIN = 10.0

#: 低于此值的帧（均方，≈ -60 dBFS RMS）测量语音电平时视作数字静音。
SILENCE_FLOOR_MS = 1.0e-6

#: 只有能量在最大帧的这个比例内（≈ -25 dB）的帧才计入语音电平。这屏蔽掉静音
#: 与房间底噪，使电平瞄准语音而非空隙。
GATE_REL_MS = 3.16e-3


def normalize_for_asr(samples: list[float], sample_rate: int) -> list[float]:
    """对 samples（单声道 f32）做 ASR 归一化：去直流/隆隆声，再把语音电平带到
    一致目标而不削顶。返回清理后的信号；静音或空输入基本原样返回。分配一份
    副本——对调用方可变的整段缓冲，优先用 normalize_for_asr_in_place（省去额外
    的整段分配）。"""
    out = list(samples)
    normalize_for_asr_in_place(out, sample_rate)
    return out


def normalize_for_asr_in_place(samples: list[float], sample_rate: int) -> None:
    """就地 normalize_for_asr：去直流/隆隆声并归一化语音电平，**不**分配第二份
    全长缓冲——高通与增益直接写回 samples。把长文件解码成一个大 Vec 后就地
    归一化，避免了在随后（很长的）转写与说话人分离过程中持有一份冗余的整段
    副本。空或零速率输入原样不动。"""
    if not samples or sample_rate == 0:
        return

    _high_pass_in_place(samples, HPF_CUTOFF_HZ, sample_rate)

    rms = _gated_rms(samples, sample_rate)
    if rms is None:
        return  # 无语音电平内容——保留（现已去隆隆声的）信号不动

    gain = min(TARGET_RMS / rms, MAX_GAIN)
    _apply_gain_capped_in_place(samples, gain)


def _high_pass_in_place(samples: list[float], cutoff_hz: float,
                        sample_rate: int) -> None:
    """一阶高通滤波器，就地。以温和的 6 dB/oct 斜率去除直流与 cutoff_hz 以下的
    频率——足以清除隆隆声而不碰语音。递推读取每个样本的原值，故前一输入在槽位
    被覆盖前先存入标量。"""
    if not samples:
        return

    dt = 1.0 / sample_rate
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    alpha = rc / (rc + dt)

    prev_in = samples[0]
    prev_out = 0.0
    samples[0] = 0.0  # 从零静止起步；首样本的直流被丢弃
    for i in range(1, len(samples)):
        x = samples[i]
        y = alpha * (prev_out + x - prev_in)
        samples[i] = y
        prev_out = y
        prev_in = x


def _gated_rms(samples: list[float], sample_rate: int) -> float | None:
    """语音门控 RMS：按 20 ms 帧测电平，只统计在 GATE_REL_MS 内的帧，使静音与
    房间底噪不把估计拉低。整段低于 SILENCE_FLOOR_MS 时返回 None（无可归一化）。"""
    frame = max(1, sample_rate // 50)  # 20 ms
    frame_ms = [_mean_square(samples[i:i + frame]) for i in range(0, len(samples), frame)]

    max_ms = max(frame_ms) if frame_ms else 0.0
    if max_ms <= SILENCE_FLOOR_MS:
        return None

    gate = max_ms * GATE_REL_MS
    active = [m for m in frame_ms if m >= gate]
    if not active:
        return None

    mean = sum(active) / len(active)
    return math.sqrt(mean)


def _apply_gain_capped_in_place(samples: list[float], gain: float) -> None:
    """就地施加 gain，但若会使峰值超过 PEAK_CEILING，则整体缩小使峰值恰好落在
    封顶上。永不削顶。"""
    peak = max(abs(x) for x in samples) if samples else 0.0
    if peak * gain > PEAK_CEILING:
        gain = PEAK_CEILING / peak if peak > 0 else 0.0
    for i in range(len(samples)):
        samples[i] *= gain


def _mean_square(frame: list[float]) -> float:
    """一帧的均方（其能量/功率）。"""
    if not frame:
        return 0.0
    return sum(x * x for x in frame) / len(frame)
