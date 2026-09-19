"""音频预处理：慢启动快释放压缩器（单压缩，抬高电平）。

目的：提升弱句首的信噪比——真人录音句首常音量偏低，Silero VAD 易把弱 onset
判成静音 → 句首丢失。压缩器平滑地"压扁"动态范围：
  - 弱信号（低于门限）→ 自动提升增益（变响）
  - 强信号（高于门限）→ 自动压缩增益（防削顶）
  - attack 慢（不强压瞬态峰值）、release 快（快速回落，跟上语音起伏）

相比"门限+增益"：纯压缩器是连续函数，无硬分段，不会误伤弱句首。
"""
from __future__ import annotations
import numpy as np


def _from_db(db: float) -> float:
    return 10.0 ** (db / 20.0)


def preprocess(pcm: bytes, *,
               threshold_db: float = -18.0,
               ratio: float = 3.0,
               attack_ms: float = 50.0,
               release_ms: float = 10.0,
               makeup_db: float = 3.0,
               sample_rate: int = 16000) -> bytes:
    """对一段 int16 PCM 做慢启动快释放压缩，返回处理后 int16 PCM。

    Args:
        threshold_db: 压缩门限（dBFS），超过此电平的强信号被压缩。
        ratio: 压缩比（>1，越高压缩越强）。
        attack_ms: 攻击时间（慢，不强压瞬态峰值）。
        release_ms: 释放时间（快，快速回落跟上起伏）。
        makeup_db: makeup gain（dB），压缩后整体抬升。
        sample_rate: 采样率。
    """
    if not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size == 0:
        return pcm

    attack_coeff = np.exp(-1.0 / max(1.0, sample_rate * attack_ms / 1000.0))
    release_coeff = np.exp(-1.0 / max(1.0, sample_rate * release_ms / 1000.0))
    thr = _from_db(threshold_db)
    inv_ratio = 1.0 / ratio

    env = 0.0
    z = np.empty_like(x)
    for i in range(x.size):
        mag = abs(x[i])
        # envelope follower：上升用 attack（慢），下降用 release（快）
        if mag > env:
            env = attack_coeff * env + (1.0 - attack_coeff) * mag
        else:
            env = release_coeff * env + (1.0 - release_coeff) * mag
        # 压缩：超过门限的部分按比例压低
        if env > thr:
            excess = env - thr
            reduction = excess * (1.0 - inv_ratio)
            g = 1.0 - reduction / max(env, 1e-8)
        else:
            g = 1.0
        z[i] = x[i] * g

    # makeup gain（整体抬升）
    z = z * _from_db(makeup_db)

    # 峰值保护（防削顶）
    peak = float(np.max(np.abs(z))) if z.size else 0.0
    if peak > 0.98:
        z = z / peak * 0.98

    return (np.clip(z, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
