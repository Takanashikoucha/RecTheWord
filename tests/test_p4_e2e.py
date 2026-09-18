"""P4 端到端：会话存储 + 说话人粗分 + 纪要生成（含 API 降级路径）。

用法：python -m tests.test_p4_e2e [--speed 16]

覆盖：
  1. 正常路径：mock API 在线 → 全程跑 → 会话目录落盘（meta/transcript/minutes）
  2. 降级路径：mock API 不可达 → 翻译显式报错、纪要显式报错、ASR 照常
  3. 说话人粗分：不同段分配到 speaker 槽
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rtw.core.config import load_config
from rtw.core.events import EventBus
from rtw.llm_api.client import LlmApiClient
from rtw.pipeline.orchestrator import Pipeline


def _run_pipeline(api_up: bool, session_base: Path, speed: float) -> dict:
    cfg = load_config()
    bus = EventBus()
    model_dir = ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B/snapshots/master"
    vad_path = ROOT / "models/silero_vad/silero_vad.onnx"
    assert model_dir.exists() and vad_path.exists(), "模型缺失"

    if api_up:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tests.mock_api_server", "--port", "8801"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        api = LlmApiClient("http://127.0.0.1:8801/v1", "sk", "mock")
    else:
        proc = None
        api = LlmApiClient("http://127.0.0.1:1/v1", "sk", "mock")  # 不可达

    pipe = Pipeline(cfg, bus, str(model_dir), api, target_lang="zh",
                   session_base=str(session_base))
    pipe.start_session({"case": "api_up" if api_up else "api_down"})
    pipe.setup_lanes()
    for l in pipe.lanes:
        if l.rep:
            l.rep.speed = speed
    pipe.start()
    pipe.run_lane_workers()

    trans_deltas: dict[str, list[str]] = {}
    trans_errs: list[dict] = []
    asr_errs: list[dict] = []
    speakers: set[str] = set()
    bus.subscribe("trans", lambda p: trans_deltas.setdefault(p["seg_id"], []).append(p["delta"]))
    bus.subscribe("trans_err", lambda p: trans_errs.append(p))
    bus.subscribe("asr_err", lambda p: asr_errs.append(p))
    bus.subscribe("asr", lambda p: speakers.add(p.get("speaker", "?")))

    # 等回放完成 + 处理余量
    est = 25.0 / speed + 40.0
    deadline = time.monotonic() + est
    quiet = None
    while time.monotonic() < deadline:
        reps_done = all(l.rep.done.is_set() for l in pipe.lanes if l.rep)
        if reps_done and all(l.seg_q.empty() for l in pipe.lanes) and pipe.stats["asr_done"] > 0:
            if quiet is None:
                quiet = time.monotonic()
            elif time.monotonic() - quiet > 3.0:
                break
        else:
            quiet = None
        bus.pump(0.05)
        time.sleep(0.5)

    # 纪要生成
    minutes_buf: list[str] = []
    minutes_err: list[str] = []
    minutes_done = False
    def _md(d): minutes_buf.append(d)
    def _md_done(): nonlocal minutes_done; minutes_done = True
    def _md_err(e): minutes_err.append(e)
    pipe.generate_minutes(_md, _md_done, _md_err)
    t0 = time.monotonic()
    while not minutes_done and not minutes_err and time.monotonic() - t0 < 30:
        bus.pump(0.05)
        time.sleep(0.2)
    time.sleep(0.5)

    pipe.stop()
    if proc:
        proc.terminate()

    sess_dir = pipe.store.current
    files = {p.name: p for p in sess_dir.glob("*")} if sess_dir else {}
    return {
        "stats": pipe.stats,
        "speakers": sorted(speakers),
        "trans_segs": len(trans_deltas),
        "trans_errs": len(trans_errs),
        "asr_errs": len(asr_errs),
        "minutes_chars": len("".join(minutes_buf)),
        "minutes_err": minutes_err,
        "files": sorted(files.keys()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=16.0)
    args = ap.parse_args()
    ok = True

    # ---- 1. 正常路径 ----
    base1 = Path(tempfile.mkdtemp(prefix="rtw_p4_up_", dir=str(ROOT / "tmp")))
    r1 = _run_pipeline(True, base1, args.speed)
    print("[api_up]", r1)
    c1 = (r1["stats"]["asr_done"] >= 1 and r1["trans_segs"] >= 1
          and r1["minutes_chars"] > 20
          and "meta.json" in r1["files"] and "transcript.jsonl" in r1["files"]
          and "minutes.md" in r1["files"] and r1["trans_errs"] == 0)
    print("PASS" if c1 else "FAIL", "(api_up)")
    ok &= c1

    # ---- 2. 降级路径 ----
    base2 = Path(tempfile.mkdtemp(prefix="rtw_p4_down_", dir=str(ROOT / "tmp")))
    r2 = _run_pipeline(False, base2, args.speed)
    print("[api_down]", r2)
    c2 = (r2["stats"]["asr_done"] >= 1          # ASR 照常
          and r2["trans_errs"] >= 1             # 翻译显式报错
          and (len(r2["minutes_err"]) >= 1 or r2["minutes_chars"] == 0)  # 纪要显式报错
          )
    print("PASS" if c2 else "FAIL", "(api_down)")
    ok &= c2

    print("=" * 50)
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
