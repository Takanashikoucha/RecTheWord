"""生成 4 分钟（240s）中日英三语 TTS 对话（edge-tts 神经语音）。

通道分配：
  mic 通道：中文（zh-CN-XiaoxiaoNeural）+ 日文（ja-JP-NanamiNeural）交替
  sys 通道：英文（en-US-GuyNeural）
目标翻译语言 = 中文（ja/en → zh，zh 原文直通）。

原文保存到 tests/fixtures/meeting_tri/script.txt（供用户判断识别效果）。
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

FIX = Path(__file__).resolve().parent / "fixtures" / "meeting_tri"
FIX.mkdir(parents=True, exist_ok=True)

# (通道, 语言, voice, 原文) — 按时序，三语混合会议（扩到 ~4 分钟）
SCRIPT = [
    ("mic", "zh", "zh-CN-YunxiNeural", "各位好，我们现在开始今天的三方协调会。"),
    ("sys", "en", "en-US-GuyNeural", "Good morning everyone, let's get started with today's coordination meeting."),
    ("mic", "ja", "ja-JP-NanamiNeural", "よろしくお願いします。まず全体の進捗報告をさせてください。"),
    ("sys", "en", "en-US-GuyNeural", "Thank you. Please go ahead with the overall status report."),
    ("mic", "zh", "zh-CN-YunxiNeural", "好的。首先汇报后端服务，迁移工作已经完成百分之九十了。"),
    ("sys", "en", "en-US-GuyNeural", "That's great progress. Is the client rollout still on track for Friday?"),
    ("mic", "ja", "ja-JP-NanamiNeural", "はい、クライアントのリリースは金曜日に予定通り進行しています。"),
    ("sys", "en", "en-US-GuyNeural", "Perfect. And what about the budget approval from the finance team?"),
    ("mic", "zh", "zh-CN-YunxiNeural", "数据库扩容的预算，财务那边还在走审批流程，预计明天能有结果。"),
    ("sys", "en", "en-US-GuyNeural", "Noted. I will personally follow up with finance to speed things up today."),
    ("mic", "ja", "ja-JP-NanamiNeural", "次に、テスト環境の安定性について話し合いたいと思います。"),
    ("sys", "en", "en-US-GuyNeural", "Sure, let's discuss it. The staging cluster has been quite unstable this week."),
    ("mic", "zh", "zh-CN-YunxiNeural", "我查看了监控日志，发现问题是夜间批处理任务占用了太多的计算资源。"),
    ("sys", "en", "en-US-GuyNeural", "Yes, that matches what we see. We should move batch jobs to a separate node pool."),
    ("mic", "ja", "ja-JP-NanamiNeural", "その方針に賛成です。今週中にバッチジョブを専用プールへ移動しましょう。"),
    ("sys", "en", "en-US-GuyNeural", "Agreed. We will also add resource quotas to prevent future overflows."),
    ("mic", "zh", "zh-CN-YunxiNeural", "另外提醒一下，下周的压测计划需要提前预约测试集群的时间窗口。"),
    ("sys", "en", "en-US-GuyNeural", "Good reminder. I will book the test cluster window for next Tuesday."),
    ("mic", "ja", "ja-JP-NanamiNeural", "それから、ドキュメントの更新も忘れずにお願いします。"),
    ("sys", "en", "en-US-GuyNeural", "Will do. Documentation updates are assigned to the engineering team."),
    ("mic", "zh", "zh-CN-YunxiNeural", "还有一个补充，关于监控告警的阈值，我建议这周重新校准一遍。"),
    ("sys", "en", "en-US-GuyNeural", "Good idea. We will recalibrate the alert thresholds based on the new baselines."),
    ("mic", "ja", "ja-JP-NanamiNeural", "あと、セキュリティの監査結果についても共有しておきます。"),
    ("sys", "en", "en-US-GuyNeural", "Please share the security audit findings with the whole team as well."),
    ("mic", "zh", "zh-CN-YunxiNeural", "审计结果显示有两处权限配置需要收紧，我会在今晚之前提交修复。"),
    ("sys", "en", "en-US-GuyNeural", "Appreciated. Please make sure the fixes are reviewed before merging."),
    ("mic", "ja", "ja-JP-NanamiNeural", "了解しました。それでは以上で終わりにさせていただきます。"),
    ("sys", "en", "en-US-GuyNeural", "Thank you. That covers everything for today's agenda."),
    ("mic", "zh", "zh-CN-YunxiNeural", "好的，那今天的议题就到这里，感谢大家的投入，我们下次再见。"),
    ("sys", "en", "en-US-GuyNeural", "Thanks everyone for your time. See you all at the next meeting."),
]

GAP_S = 1.0


async def synth_one(voice: str, text: str, out_mp3: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice).save(str(out_mp3))


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
    # 保存原文（供用户判断识别效果）
    lang_cn = {"zh": "中文", "ja": "日文", "en": "英文"}
    with open(FIX / "script.txt", "w", encoding="utf-8") as f:
        f.write("4 分钟中日英三语会议 — TTS 原文（edge-tts 神经语音）\n")
        f.write("mic 通道 = 中文 + 日文；sys 通道 = 英文；目标翻译语言 = 中文\n\n")
        for i, (lane, lang, _, text) in enumerate(SCRIPT, 1):
            f.write(f"{i:2d}. [{lane:3s}|{lang_cn[lang]}] {text}\n")

    t = 0.3
    mic_entries: list[tuple[float, float, np.ndarray]] = []
    sys_entries: list[tuple[float, float, np.ndarray]] = []
    for lane, lang, voice, text in SCRIPT:
        mp3 = FIX / "_tmp.mp3"
        asyncio.run(synth_one(voice, text, mp3))
        dur = mp3_to_16k(mp3, FIX / "_tmp.wav")
        clip, _ = sf.read(str(FIX / "_tmp.wav"), dtype="float32")
        (mic_entries if lane == "mic" else sys_entries).append((t, dur, clip))
        print(f"[{lane:3s}|{lang:2s}] {dur:5.2f}s  {text}")
        t += dur + GAP_S
        (FIX / "_tmp.wav").unlink()

    def build(entries, out: Path) -> None:
        total = int(max(e[0] + e[1] for e in entries) * 16000) + 16000
        buf = np.zeros(total, dtype=np.float32)
        for off, dur, clip in entries:
            n = int(dur * 16000)
            buf[int(off * 16000):int(off * 16000) + n] = clip[:n]
        sf.write(str(out), buf, 16000)

    build(mic_entries, FIX / "mic_tri.wav")
    build(sys_entries, FIX / "sys_tri.wav")
    print(f"\n总时长 ≈ {t - GAP_S:.1f}s（目标 240s）")
    print(f"原文已保存: {FIX / 'script.txt'}")


if __name__ == "__main__":
    main()
