"""双路线 ASR 对比实测（P2b）：整句解码 vs causal 流式。

用法：
  python -m tests.test_asr_compare [--fixture DIR] [--tower PATH] [--model PATH]

输出：每条路线的 RTF、首句延迟（流式）、识别文本，供架构选型决策。
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


def load_wav(path: Path) -> np.ndarray:
    data, sr = sf.read(str(path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        import scipy.signal
        data = scipy.signal.resample(data, int(len(data) * 16000 / sr))
    return data


def bench_offline(model_path: str, fx: Path) -> dict:
    """路线 A：VAD 切句后整句解码（qwen-asr 原生）。"""
    import torch
    torch.set_num_threads(8)
    from qwen_asr import Qwen3ASRModel

    t0 = time.monotonic()
    model = Qwen3ASRModel.from_pretrained(model_path, dtype=torch.float32,
                                         device_map="cpu", max_new_tokens=128)
    t_load = time.monotonic() - t0
    out = {"route": "offline_sentence", "load_s": round(t_load, 1), "langs": {}}
    for name in ("zh", "en"):
        wav = fx / f"{name}.wav"
        if not wav.exists():
            continue
        pcm = load_wav(wav)
        dur = len(pcm) / 16000
        t0 = time.monotonic()
        res = model.transcribe(audio=(pcm, 16000), language=None)
        dt = time.monotonic() - t0
        r = res[0]
        out["langs"][name] = {
            "dur_s": round(dur, 2), "infer_s": round(dt, 2),
            "rtf": round(dt / dur, 4), "text": r.text,
            "latency_note": "final_after_segment_stop≈infer_s（整句）",
        }
    return out


def bench_causal(model_path: str, tower_path: str, fx: Path) -> dict:
    """路线 B：causal 流式（qwen3_asr_causal 包，需单独安装）。"""
    out = {"route": "causal_streaming", "langs": {}}
    try:
        from qwen3_asr_causal.asr import Qwen3StreamingASR
    except ImportError as e:
        out["error"] = f"qwen3_asr_causal not installed: {e}"
        return out
    import torch
    torch.set_num_threads(8)
    from qwen3_asr_causal.online import Qwen3StreamingOnlineProcessor

    for name in ("zh", "en"):
        wav = fx / f"{name}.wav"
        if not wav.exists():
            continue
        pcm = load_wav(wav)
        t0 = time.monotonic()
        asr = Qwen3StreamingASR(
            lan=name,
            model_dir=model_path,
            qwen3_streaming_audio_backend="causal",
            qwen3_streaming_tower_checkpoint=tower_path,
            qwen3_streaming_device="cpu",
            qwen3_streaming_dtype="float32",
        )
        if name == "zh":
            out["load_s"] = round(time.monotonic() - t0, 1)
        proc = Qwen3StreamingOnlineProcessor(asr)
        first_text_at: float | None = None
        committed: list[str] = []
        t0 = time.monotonic()
        chunk = int(0.5 * 16000)
        for i in range(0, len(pcm), chunk):
            c = pcm[i:i + chunk]
            t_end = (i + len(c)) / 16000
            proc.insert_audio_chunk(c, t_end)
            toks, _ = proc.process_iter(is_last=(i + chunk >= len(pcm)))
            for tk in toks:
                txt = getattr(tk, "text", str(tk))
                committed.append(txt)
                if first_text_at is None and txt.strip():
                    first_text_at = time.monotonic() - t0
        buf = proc.get_buffer()
        total = time.monotonic() - t0
        text = "".join(committed)
        if buf and buf.text:
            text += buf.text
        out["langs"][name] = {
            "dur_s": round(len(pcm) / 16000, 2),
            "wall_s": round(total, 2),
            "rtf": round(total / (len(pcm) / 16000), 4),
            "first_text_wall_s": round(first_text_at, 2) if first_text_at else None,
            "text": text,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=str(ROOT / "tests/fixtures/meeting_sim"))
    ap.add_argument("--model", default=str(ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B/snapshots/master"))
    ap.add_argument("--tower", default=str(ROOT / "models/qwen3_asr_streaming"))
    ap.add_argument("--only", choices=["offline", "causal"], default="")
    args = ap.parse_args()

    fx = Path(args.fixture)
    results = []
    if args.only in ("", "offline"):
        results.append(bench_offline(args.model, fx))
    if args.only in ("", "causal"):
        results.append(bench_causal(args.model, args.tower, fx))

    print("\n===== COMPARISON =====")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
