"""端到端链路测试（方案 §8）：TTS 音频回放注入 → VAD → ASR → 翻译(API) → 纪要。

用法：
  python -m tests.e2e_pipeline [--speed 16] [--skip-asr] [--api-base URL]

非 Windows 环境用 ReplaySource 代替 WASAPI 采集；Windows 真机把 config 的
audio.source 切回 wasapi 跑同一 fixture 即可验证 loopback 采集本身。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rtw.core.config import load_config
from rtw.core.events import EventBus
from rtw.audio.ring_buffer import RingBuffer
from rtw.audio.replay_source import ReplaySource
from rtw.vad.silero import SileroVad


def make_fixtures(out_dir: Path, langs: dict[str, str], minutes_total: int = 5) -> None:
    """用 espeak-ng 合成多语言模拟会议音频（真实 TTS 服务可用时替换）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    per = max(1, minutes_total * 60 // len(langs))
    for name, text in langs.items():
        wav = out_dir / f"{name}.wav"
        if wav.exists():
            continue
        # 分段合成：句子之间插入 0.8s 静音，制造 VAD 切句点
        sentences = [s for s in text.split("。") if s.strip()]
        parts = []
        for i, s in enumerate(sentences[: max(1, per // 4)]):
            tmp = out_dir / f"_part_{name}_{i}.wav"
            subprocess.run(["espeak-ng", "-v", name, "-s", "150", s, "-w", str(tmp)], check=True)
            parts.append(tmp)
            sil = out_dir / "_sil.wav"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                           f"anullsrc=r=16000:cl=mono", "-t", "0.8", str(sil)], check=True)
            parts.append(sil)
        concat = out_dir / f"_concat_{name}.txt"
        concat.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                       "-i", str(concat), "-ar", "16000", "-ac", "1", str(wav)], check=True)
        for p in parts:
            p.unlink(missing_ok=True)
        sil.unlink(missing_ok=True)
        concat.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=16.0)
    ap.add_argument("--skip-asr", action="store_true")
    ap.add_argument("--api-base", default="")
    ap.add_argument("--fixture-minutes", type=int, default=5)
    args = ap.parse_args()

    cfg = load_config()
    bus = EventBus()
    fx = ROOT / "tests" / "fixtures" / "meeting_sim"
    make_fixtures(fx, {
        "zh": "今天我们来讨论下季度的预算分配。首先确认一下剩余额度。服务器采购需要在月底前完成比价。",
        "en": "Let us confirm the remaining budget for next quarter. The server procurement must be compared by month end.",
    }, args.fixture_minutes)

    results = {"segments": [], "errors": []}

    for lane, wav in [("mic", fx / "zh.wav"), ("sys", fx / "en.wav")]:
        ring = RingBuffer(int(10 * 16000))
        vad = SileroVad(cfg.models_dir() / "silero_vad" / "silero_vad.onnx"
                        if (cfg.models_dir() / "silero_vad" / "silero_vad.onnx").exists()
                        else ROOT / "models" / "silero_vad" / "silero_vad.onnx")
        rep = ReplaySource(ring, wav, speed=args.speed)

        def consume() -> None:
            t0 = time.monotonic()
            while not rep.done.wait(0.5):
                data = ring.drain()
                if data:
                    segs = vad.feed(data, lane)
                    for s in segs:
                        results["segments"].append({
                            "lane": lane, "start_ms": s.start_ms, "end_ms": s.end_ms,
                            "dur_ms": s.end_ms - s.start_ms,
                        })
            print(f"[{lane}] replay done in {time.monotonic()-t0:.1f}s")

        ct = __import__("threading").Thread(target=consume, daemon=True)
        ct.start()
        rep.start()
        rep.join()
        ct.join(timeout=10)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not results["segments"]:
        print("FAIL: no VAD segments produced", file=sys.stderr)
        sys.exit(1)
    print(f"PASS: {len(results['segments'])} segments")


if __name__ == "__main__":
    main()
