"""P2 端到端全链路测试：回放注入 → VAD → ASR 整句解码 → mock API 翻译（SSE 流式）。

用法：
  python -m tests.test_e2e_full [--api-port 8799] [--speed 16]

验证点：
  1. VAD 切出 ≥1 段
  2. ASR 出文本（整句解码）
  3. 翻译 API 流式回传（SSE 增量）且最终有译文
  4. 状态机事件齐全（asr_load / api_health 的 begin→finish）
  5. 延迟统计：first_update（VAD 段结束→ASR 文本）、translation lag
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rtw.core.config import load_config
from rtw.core.events import EventBus
from rtw.pipeline.orchestrator import Pipeline


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-port", type=int, default=8799)
    ap.add_argument("--speed", type=float, default=16.0)
    args = ap.parse_args()

    # 启动 mock API
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "tests.mock_api_server", "--port", str(args.api_port)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)

    cfg = load_config()
    cfg.audio.replay_speed = args.speed
    bus = EventBus()

    from rtw.llm_api.client import LlmApiClient
    api = LlmApiClient(f"http://127.0.0.1:{args.api_port}/v1", "sk-test", "mock-translate-1")

    model = str(ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B/snapshots/master")
    pipe = Pipeline(cfg, bus, model, api, target_lang="zh")

    seen_status: list[tuple[str, str]] = []
    asr_events: list[dict] = []
    trans_deltas: dict[str, list[str]] = {}
    trans_errors: list[dict] = []
    seg_events: list[dict] = []
    t_start = time.monotonic()
    bus_subscribe_helper = {}
    def _seg_cb(p):
        p2 = dict(p)
        p2["t_rel"] = round(time.monotonic() - t_start, 1)
        seg_events.append(p2)
    def _asr_cb(p):
        p2 = dict(p)
        p2["t_rel"] = round(time.monotonic() - t_start, 1)
        asr_events.append(p2)

    bus.subscribe("status", lambda p: seen_status.append((p[0], p[2].phase.value)))
    bus.subscribe("asr", _asr_cb)
    bus.subscribe("seg", _seg_cb)
    bus.subscribe("trans", lambda p: trans_deltas.setdefault(p["seg_id"], []).append(p["delta"]))
    bus.subscribe("trans_err", lambda p: trans_errors.append(p))
    asr_errors: list[dict] = []
    def _asr_err_cb(p):
        p2 = dict(p)
        p2["t_rel"] = round(time.monotonic() - t_start, 1)
        asr_errors.append(p2)
    bus.subscribe("asr_err", _asr_err_cb)

    def pump() -> None:
        while True:
            bus.pump(0.1)

    pt = threading.Thread(target=pump, daemon=True)
    pt.start()

    t0 = time.monotonic()
    pipe.setup_lanes()
    pipe.start()
    pipe.run_lane_workers()

    # 等回放结束（fixture ~22.6s / speed）+ 处理余量；
    # 终止条件：回放源全部完成 且 无段在处理中（seg_q 空 且 无新 asr 事件 3s）
    est = 25.0 / args.speed + 45.0
    deadline = time.monotonic() + est
    quiet_since = None
    while time.monotonic() < deadline:
        reps_done = all(l.rep.done.is_set() for l in pipe.lanes if l.rep)
        qs_empty = all(l.seg_q.empty() for l in pipe.lanes)
        if reps_done and qs_empty and pipe.stats["asr_done"] > 0:
            if quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since > 3.0:
                break
        else:
            quiet_since = None
        time.sleep(0.5)
    time.sleep(2.0)
    pipe.stop()

    elapsed = time.monotonic() - t0
    result = {
        "elapsed_s": round(elapsed, 1),
        "stats": pipe.stats,
        "status_ops": sorted(set(seen_status)),
        "asr_texts": [e["text"] for e in asr_events],
        "asr_latencies_ms": [e["t_first_ms"] for e in asr_events],
        "trans_seg_count": len(trans_deltas),
        "trans_sample": {k: "".join(v)[:80] for k, v in list(trans_deltas.items())[:3]},
        "trans_errors": trans_errors,
        "asr_errors": asr_errors,
        "seg_timeline": seg_events,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    ok = (pipe.stats["segs"] >= 1 and pipe.stats["asr_done"] >= 1
         and pipe.stats["trans_done"] >= 1
         and any("asr_load" in o for o, _ in seen_status)
         and any("api_health" in o for o, _ in seen_status))
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
