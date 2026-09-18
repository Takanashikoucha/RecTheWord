"""用 edge-tts（微软神经语音）生成 83s 双通道对话——自然真人音色，贴近真实会议。

mic 通道：中文女声（zh-CN-XiaoxiaoNeural）
sys 通道：英文男声（en-US-GuyNeural）
输出 24kHz mp3 → 转 16kHz int16 mono wav（pipeline 期望格式）。
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

FIX = Path(__file__).resolve().parent / "fixtures" / "meeting_neural"
FIX.mkdir(parents=True, exist_ok=True)

SCRIPT = [
    ("mic", "zh-CN-XiaoxiaoNeural", "大家好，现在开始本周的项目例会。"),
    ("sys", "en-US-GuyNeural", "Morning everyone, let's get started."),
    ("mic", "zh-CN-XiaoxiaoNeural", "先同步一下上周的进度，后端服务迁移已经完成百分之八十。"),
    ("sys", "en-US-GuyNeural", "Great. And the client rollout is on schedule for Friday."),
    ("mic", "zh-CN-XiaoxiaoNeural", "好的。另外提醒一下，数据库扩容的预算还需要财务确认。"),
    ("sys", "en-US-GuyNeural", "Noted. I will loop in finance by end of day."),
    ("mic", "zh-CN-XiaoxiaoNeural", "谢谢。接下来讨论一下测试环境的稳定性问题。"),
    ("sys", "en-US-GuyNeural", "Agreed. The staging cluster has been flaky this week."),
    ("mic", "zh-CN-XiaoxiaoNeural", "我这边看到的日志显示，是夜间批处理任务占了太多资源。"),
    ("sys", "en-US-GuyNeural", "Yes, we should move batch jobs to a dedicated node pool."),
    ("mic", "zh-CN-XiaoxiaoNeural", "同意，那就这么定了，这周内把批处理迁出去。"),
    ("sys", "en-US-GuyNeural", "Perfect. Anything else before we wrap up?"),
    ("mic", "zh-CN-XiaoxiaoNeural", "没有了，散会。大家辛苦了。"),
    ("sys", "en-US-GuyNeural", "Thanks all, see you next week."),
]

GAP_S = 1.0


async def synth_one(voice: str, text: str, out_mp3: Path) -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, voice)
    await comm.save(str(out_mp3))


def mp3_to_16k(mp3: Path, out_wav: Path) -> float:
    raw = out_wav.with_suffix(".raw.wav")
    subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ac", "1", "-ar", "16000",
                   "-sample_fmt", "s16", str(raw)], check=True, capture_output=True)
    data, sr = sf.read(str(raw), dtype="float32")
    sf.write(str(out_wav), data, 16000)
    raw.unlink()
    mp3.unlink()
    return len(data) / 16000.0


def main() -> None:
    import edge_tts  # noqa
    t = 0.3
    mic_entries: list[tuple[float, float, np.ndarray]] = []
    sys_entries: list[tuple[float, float, np.ndarray]] = []
    for lane, voice, text in SCRIPT:
        mp3 = FIX / "_tmp.mp3"
        asyncio.run(synth_one(voice, text, mp3))
        dur = mp3_to_16k(mp3, FIX / "_tmp.wav")
        clip, _ = sf.read(str(FIX / "_tmp.wav"), dtype="float32")
        (mic_entries if lane == "mic" else sys_entries).append((t, dur, clip))
        print(f"[{lane:3s}] {dur:5.2f}s  {text}")
        t += dur + GAP_S
        (FIX / "_tmp.wav").unlink()

    def build(entries, out: Path) -> None:
        total = int(max(e[0] + e[1] for e in entries) * 16000) + 16000
        buf = np.zeros(total, dtype=np.float32)
        for off, dur, clip in entries:
            n = int(dur * 16000)
            buf[int(off * 16000):int(off * 16000) + n] = clip[:n]
        sf.write(str(out), buf, 16000)

    build(mic_entries, FIX / "mic_neural.wav")
    build(sys_entries, FIX / "sys_neural.wav")
    print(f"\n总时长 ≈ {t - GAP_S:.1f}s")


if __name__ == "__main__":
    main()
