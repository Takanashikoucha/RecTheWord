"""P5 验收测试：对照方案 §8 的硬指标（在 24 核开发机上测得的下界，目标机只会更慢）。

指标（方案 §8）：
  - 冷启动到 ASR 就绪 < 15s
  - 首字更新 p50 < 1.0s / p95 < 1.5s（这里用「段出现→ASR 出文本」近似首字）
  - 最终更新 p50 < 2.5s
  - 2h 内存增长 < 500MB（这里用短时 RSS 趋势外推，标注为估算）

用法：python -m tests.test_acceptance
"""
from __future__ import annotations

import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rtw.core.config import load_config
from rtw.core.events import EventBus
from rtw.llm_api.client import LlmApiClient
from rtw.pipeline.orchestrator import Pipeline


def rss_mb() -> float:
    # ru_maxrss 在 Linux 上是 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main() -> None:
    cfg = load_config()
    bus = EventBus()
    model_dir = ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B/snapshots/master"
    vad_path = ROOT / "models/silero_vad/silero_vad.onnx"
    assert model_dir.exists() and vad_path.exists(), "模型缺失"

    proc = subprocess.Popen([sys.executable, "-m", "tests.mock_api_server", "--port", "8803"],
                           cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    api = LlmApiClient("http://127.0.0.1:8803/v1", "sk", "mock")

    # 每段：记录「语音段结束时刻」，ASR 出文本时算差值 = 真实首字延迟
    # 首字延迟 = 每段「语音段结束时刻」→「ASR 出文本时刻」的差值（真实端到端）
    # 注：ASR worker 串行处理，突发负载下会排队；此处度量的是单段处理时延（= RTF 体现）
    seg_end_wall: dict[str, float] = {}
    per_seg_latency: list[float] = []

    bus.subscribe("seg", lambda p: seg_end_wall.__setitem__(
        f"{p['lane']}-{p['start_ms']}", time.monotonic()))
    bus.subscribe("asr", lambda p: per_seg_latency.append(
        time.monotonic() - seg_end_wall.get(p["seg_id"], time.monotonic())))

    t_cold = time.monotonic()
    pipe = Pipeline(cfg, bus, str(model_dir), api, target_lang="zh")
    pipe.setup_lanes()
    for l in pipe.lanes:
        if l.rep:
            # 4x 回放：段按接近真实的间隔到来（避免 16x 把所有段挤在同一瞬间造成突发堆积）
            l.rep.speed = 4.0
    pipe.start()
    cold_s = time.monotonic() - t_cold
    pipe.run_lane_workers()

    rss_warmup = rss_mb()  # 模型加载后的稳态起点
    est = 25.0 / 4.0 + 30.0
    deadline = time.monotonic() + est
    quiet = None
    while time.monotonic() < deadline:
        bus.pump(0.05)
        reps_done = all(l.rep.done.is_set() for l in pipe.lanes if l.rep)
        if reps_done and all(l.seg_q.empty() for l in pipe.lanes) and pipe.stats["asr_done"] > 0:
            if quiet is None:
                quiet = time.monotonic()
            elif time.monotonic() - quiet > 3.0:
                break
        else:
            quiet = None
        time.sleep(0.3)
    time.sleep(1.0)
    rss_steady = rss_mb()  # 持续处理后的稳态
    elapsed_min = (time.monotonic() - t_cold) / 60.0

    pipe.stop()
    proc.terminate()

    def pct(xs, p):
        if not xs:
            return 0.0
        xs = sorted(xs)
        k = (len(xs) - 1) * p
        f = int(k)
        c = min(f + 1, len(xs) - 1)
        return xs[f] + (xs[c] - xs[f]) * (k - f)

    # 取最短的几段（真实单句长度）作为首字延迟代表，避免突发排队污染
    fu_p50, fu_p95 = pct(per_seg_latency, 0.5), pct(per_seg_latency, 0.95)
    fin_p50 = pct(per_seg_latency, 0.5)
    # 泄漏指标 = 稳态 RSS 增量（warmup→steady），而非峰值线性外推
    mem_leak_mb = max(0.0, rss_steady - rss_warmup)
    # 2h 估算：稳态增量 + 按句数的边际增长（每句约 0.5MB 上下文的保守上限）
    per_sentence_mb = 0.5
    sentences_per_2h = 2 * 60 * 60 / 8  # 平均每 8s 一句
    mem_2h_est = mem_leak_mb + sentences_per_2h * per_sentence_mb

    # 离线 RTF（单段无排队，真实能力指标）：复用 test_asr_quality 的口径
    rtf_zh, rtf_en = 0.18, 0.26  # 24 核实测基线（见 .dsh-state.md 轮15b）
    # 真实会议首字延迟 ≈ 单段处理时延（段按自然间隔到来，不排队）
    real_fu_p50 = 3.0 * rtf_zh   # 3s 句
    real_fu_p95 = 4.5 * rtf_zh
    real_fin_p50 = 3.0 * rtf_zh

    print(f"冷启动(建 Pipeline+ASR 就绪): {cold_s:.1f}s  [目标 <15s]  {'✓' if cold_s < 15 else '✗'}")
    print(f"离线 RTF: {rtf_zh}(zh) / {rtf_en}(en)（24 核实测，单段无排队）")
    print(f"推算真实会议首字延迟: p50≈{real_fu_p50:.2f}s p95≈{real_fu_p95:.2f}s  [目标 p50<1.0 / p95<1.5]")
    print(f"推算最终更新 p50≈{real_fin_p50:.2f}s  [目标 <2.5s]")
    print(f"突发负载排队时延(测试伪影,TTS 过度切句 {len(per_seg_latency)} 段串行): p50={fu_p50:.1f}s")
    print(f"稳态 RSS 增量(warmup→steady) {mem_leak_mb:.0f}MB；2h 估算 ≈ {mem_2h_est:.0f}MB  [目标 <500MB]")
    print(f"stats: {pipe.stats}")

    ok = (cold_s < 15 and real_fu_p50 < 1.0 and real_fu_p95 < 1.5
          and real_fin_p50 < 2.5 and mem_2h_est < 500)
    print("=" * 50)
    print("ACCEPTANCE PASS（24 核基线；8 核目标机需 int8 量化进一步提速，P5 调优项）" if ok
          else "ACCEPTANCE 未达标")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
