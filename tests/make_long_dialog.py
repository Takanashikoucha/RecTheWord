"""生成 1 分钟双通道模拟会议音频（TTS 交替发言 + 自然停顿）。

mic 通道：中文为主（cmn 男声），sys 通道：英文为主（en-us 女声），
模拟一场中英双语周会：双方交替发言，句间 0.8-1.5s 自然停顿。
输出 16kHz int16 mono wav，各约 60s。
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

FIX = Path(__file__).resolve().parent / "fixtures" / "meeting_long"
FIX.mkdir(parents=True, exist_ok=True)

# (通道, 语言voice, 文本) — 按时序排列，交替发言
SCRIPT = [
    ("mic", "cmn", "大家好，现在开始本周的项目例会。"),
    ("sys", "en-us", "Morning everyone, let's get started."),
    ("mic", "cmn", "先同步一下上周的进度，后端服务迁移已经完成百分之八十。"),
    ("sys", "en-us", "Great. And the client rollout is on schedule for Friday."),
    ("mic", "cmn", "好的。另外提醒一下，数据库扩容的预算还需要财务确认。"),
    ("sys", "en-us", "Noted. I will loop in finance by end of day."),
    ("mic", "cmn", "谢谢。接下来讨论一下测试环境的稳定性问题。"),
    ("sys", "en-us", "Agreed. The staging cluster has been flaky this week."),
    ("mic", "cmn", "我这边看到的日志显示，是夜间批处理任务占了太多资源。"),
    ("sys", "en-us", "Yes, we should move batch jobs to a dedicated node pool."),
    ("mic", "cmn", "同意，那就这么定了，这周内把批处理迁出去。"),
    ("sys", "en-us", "Perfect. Anything else before we wrap up?"),
    ("mic", "cmn", "没有了，散会。大家辛苦了。"),
    ("sys", "en-us", "Thanks all, see you next week."),
]

GAP_S = 1.2  # 句间停顿（清晰的句边界，供 VAD 切句）


def trim_silence(data: np.ndarray, sr: int, thr: float = 0.02, pad_ms: int = 60) -> np.ndarray:
    """去掉短语首尾静音，压缩 espeak-ng 机器人腔的长空档。"""
    mask = np.abs(data) > thr
    if not mask.any():
        return data
    idx = np.where(mask)[0]
    lo, hi = idx[0], idx[-1]
    pad = int(pad_ms / 1000 * sr)
    lo = max(0, lo - pad)
    hi = min(len(data), hi + pad)
    return data[lo:hi]


def synth(voice: str, text: str, out: Path) -> None:
    wav = out.with_suffix(".raw.wav")
    subprocess.run(["espeak-ng", "-v", voice, "-s", "165", "-w", str(wav), text],
                   check=True, capture_output=True)
    import soundfile as sf
    from scipy.signal import resample_poly
    import numpy as np
    data, sr = sf.read(str(wav), dtype="float32")
    g = np.gcd(16000, sr)
    data = resample_poly(data, 16000 // g, sr // g)
    data = trim_silence(data, 16000)
    sf.write(str(out), data.astype(np.float32), 16000)
    wav.unlink()
    return len(data) / 16000.0


def build_channel(entries: list[tuple[str, float]], out: Path) -> None:
    """entries: [(起始偏移s, 时长s)] → 拼成带静音的整轨。"""
    import numpy as np
    import soundfile as sf
    total = int(max(e[0] + e[1] for e in entries) * 16000) + 16000
    buf = np.zeros(total, dtype=np.float32)
    for (off, dur, clip) in entries:
        n = int(dur * 16000)
        buf[int(off * 16000):int(off * 16000) + n] = clip[:n]
    sf.write(str(out), buf, 16000)


def main() -> None:
    import numpy as np
    import soundfile as sf

    t = 0.3
    mic_entries: list[tuple[float, float, np.ndarray]] = []
    sys_entries: list[tuple[float, float, np.ndarray]] = []
    for lane, voice, text in SCRIPT:
        tmp = FIX / "_tmp.wav"
        synth(voice, text, tmp)
        clip, _ = sf.read(str(tmp), dtype="float32")
        dur = len(clip) / 16000.0
        if lane == "mic":
            mic_entries.append((t, dur, clip))
        else:
            sys_entries.append((t, dur, clip))
        print(f"[{lane:3s}] {dur:5.2f}s  {text}")
        t += dur + GAP_S
        tmp.unlink()

    build_channel(mic_entries, FIX / "mic_60s.wav")
    build_channel(sys_entries, FIX / "sys_60s.wav")
    print(f"\n总时长 ≈ {t - GAP_S:.1f}s（含停顿）")


if __name__ == "__main__":
    main()
