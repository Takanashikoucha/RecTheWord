"""ASR 实机识别质量 + RTF 实测（P2b）。

用法：
  python -m tests.test_asr_quality [--model PATH] [--fixture DIR]

指标口径（FunASR 实时基准约定）：
  - RTF = 推理耗时 / 音频时长（离线整句）
  - 首句延迟 = 音频开始 → 第一句文本产出（流式时才有意义，此处整句模式近似为总耗时）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import soundfile as sf


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        import scipy.signal
        n = int(len(data) * 16000 / sr)
        data = scipy.signal.resample(data, n)
    return data, 16000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B/snapshots/master"))
    ap.add_argument("--fixture", default=str(ROOT / "tests/fixtures/meeting_sim"))
    args = ap.parse_args()

    import torch
    torch.set_num_threads(8)
    from qwen_asr import Qwen3ASRModel

    t0 = time.monotonic()
    model = Qwen3ASRModel.from_pretrained(args.model, dtype=torch.float32,
                                         device_map="cpu", max_new_tokens=128)
    t_load = time.monotonic() - t0
    print(f"[load] {t_load:.1f}s")

    fx = Path(args.fixture)
    results = []
    for name in ("zh", "en"):
        wav = fx / f"{name}.wav"
        if not wav.exists():
            print(f"SKIP {name}: no fixture")
            continue
        pcm, sr = load_wav(wav)
        dur = len(pcm) / sr
        t0 = time.monotonic()
        res = model.transcribe(audio=(pcm, sr), language=None)
        dt = time.monotonic() - t0
        r = res[0]
        results.append({
            "lang": name, "duration_s": round(dur, 2),
            "infer_s": round(dt, 2), "rtf": round(dt / dur, 4),
            "text": r.text, "detected_lang": getattr(r, "language", ""),
        })
        print(f"\n[{name}] dur={dur:.1f}s infer={dt:.1f}s RTF={dt/dur:.3f}")
        print(f"  text: {r.text[:200]}")

    print("\n=== SUMMARY ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    worst = max((r["rtf"] for r in results), default=0)
    print(f"WORST_RTf={worst:.3f}")
    if worst > 1.0:
        print("WARN: RTF > 1.0，整句模式无法实时", file=sys.stderr)


if __name__ == "__main__":
    main()
