"""int8 量化预验证：对比 fp32 vs int8（torch 动态量化）的性能 + 识别效果。

方法：
  - fp32：原生 from_pretrained(dtype=float32)
  - int8：加载 fp32 后对 nn.Linear 做 torch.quantization.quantize_dynamic
  用同一段真实音频（zh 短句 + en 短句）分别跑，对比 RTF 与识别文本一致性。

用法：python -m tests.test_int8_bench
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import soundfile as sf
import torch


def load_fp32(model_dir: str):
    from qwen_asr import Qwen3ASRModel
    torch.set_num_threads(8)
    return Qwen3ASRModel.from_pretrained(model_dir, dtype=torch.float32,
                                          device_map="cpu", max_new_tokens=128)


def quantize_int8(wrapper) -> None:
    """对底层 nn.Module 的所有 nn.Linear 做动态 int8 量化（weight-only）。"""
    from torch.ao.quantization import quantize_dynamic
    target = wrapper.model if hasattr(wrapper, "model") and isinstance(wrapper.model, torch.nn.Module) else wrapper
    n = 0
    def _walk(m):
        nonlocal n
        for k, child in list(m._modules.items()):
            if isinstance(child, torch.nn.Linear):
                m._modules[k] = quantize_dynamic(child, {torch.nn.Linear}, dtype=torch.qint8)
                n += 1
            elif child is not None:
                _walk(child)
    _walk(target)
    print(f"  量化了 {n} 个 Linear 层")


def bench(model, pcm: np.ndarray, label: str) -> tuple[float, str]:
    t0 = time.monotonic()
    results = model.transcribe(audio=(pcm, 16000), language=None)
    dt = time.monotonic() - t0
    r = results[0]
    return dt, (r.text or "").strip()


def main() -> None:
    model_dir = str(ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B/snapshots/master")
    zh = sf.read(str(ROOT / "tests/fixtures/meeting_sim/zh.wav"), dtype="float32")[0]
    en = sf.read(str(ROOT / "tests/fixtures/meeting_sim/en.wav"), dtype="float32")[0]
    # 截取前 4s 做基准（控制变量）
    zh4 = zh[:4 * 16000]
    en4 = en[:4 * 16000]

    print("== fp32 ==")
    m32 = load_fp32(model_dir)
    t_zh, tx_zh = bench(m32, zh4, "zh")
    t_en, tx_en = bench(m32, en4, "en")
    print(f"  zh: {t_zh:.2f}s  RTF={t_zh/4:.3f}  文本={tx_zh[:30]}")
    print(f"  en: {t_en:.2f}s  RTF={t_en/4:.3f}  文本={tx_en[:30]}")
    del m32
    torch.cuda.empty_cache() if torch.cuda.is_available() else None

    print("\n== int8（动态量化）==")
    m8 = load_fp32(model_dir)
    try:
        quantize_int8(m8)
    except Exception as e:
        print(f"  量化失败：{e}（qwen-asr 内部结构可能不含标准 nn.Linear 暴露面）")
        print("  → int8 需走 ONNX 路线或模型原生支持，见报告")
        return
    # 预热
    bench(m8, zh4[:16000], "warm")
    t_zh8, tx_zh8 = bench(m8, zh4, "zh")
    t_en8, tx_en8 = bench(m8, en4, "en")
    print(f"  zh: {t_zh8:.2f}s  RTF={t_zh8/4:.3f}  文本={tx_zh8[:30]}")
    print(f"  en: {t_en8:.2f}s  RTF={t_en8/4:.3f}  文本={tx_en8[:30]}")

    print("\n== 对比 ==")
    print(f"  zh 加速比: {t_zh/t_zh8:.2f}x   文本一致: {_sim(tx_zh, tx_zh8):.0%}")
    print(f"  en 加速比: {t_en/t_en8:.2f}x   文本一致: {_sim(tx_en, tx_en8):.0%}")


def _sim(a: str, b: str) -> float:
    a, b = a.replace(" ", ""), b.replace(" ", "")
    if not a or not b:
        return 0.0
    inter = len(set(a) & set(b))
    return inter / min(len(set(a)), len(set(b)))


if __name__ == "__main__":
    main()
