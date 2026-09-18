"""识别质量定量评估：神经语音（自然音色）1x 实时，逐句算 CER（字符错误率）。

CER = 编辑距离 / max(len(ref), len(hyp))，越低越好（0=完全一致）。
对照：espeak-ng 机器人腔 vs edge-tts 神经语音，证明识别质量取决于音频质量而非引擎。
"""
from __future__ import annotations

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

EXPECTED = {
    "mic": ["大家好，现在开始本周的项目例会。",
            "先同步一下上周的进度，后端服务迁移已经完成百分之八十。",
            "好的。另外提醒一下，数据库扩容的预算还需要财务确认。",
            "谢谢。接下来讨论一下测试环境的稳定性问题。",
            "我这边看到的日志显示，是夜间批处理任务占了太多资源。",
            "同意，那就这么定了，这周内把批处理迁出去。",
            "没有了，散会。大家辛苦了。"],
    "sys": ["Morning everyone, let's get started.",
            "Great. And the client rollout is on schedule for Friday.",
            "Noted. I will loop in finance by end of day.",
            "Agreed. The staging cluster has been flaky this week.",
            "Yes, we should move batch jobs to a dedicated node pool.",
            "Perfect. Anything else before we wrap up?",
            "Thanks all, see you next week."],
}


def cer(ref: str, hyp: str) -> float:
    ref, hyp = ref.replace(" ", "").lower(), hyp.replace(" ", "").lower()
    if not ref:
        return 1.0
    # Levenshtein
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j-1] + 1, prev[j-1] + (rc != hc)))
        prev = cur
    return prev[-1] / max(len(ref), len(hyp))


def main() -> None:
    cfg = load_config()
    cfg.audio.source = "replay"
    cfg.audio.replay_mic = "tests/fixtures/meeting_neural/mic_neural.wav"
    cfg.audio.replay_sys = "tests/fixtures/meeting_neural/sys_neural.wav"
    cfg.audio.replay_speed = 1.0
    bus = EventBus()
    model_dir = ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B"
    proc = subprocess.Popen([sys.executable, "-m", "tests.mock_api_server", "--port", "8809"],
                           cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    api = LlmApiClient("http://127.0.0.1:8809/v1", "sk", "mock")
    results: list[dict] = []
    bus.subscribe("asr", lambda p: results.append(p))
    pipe = Pipeline(cfg, bus, str(model_dir), api, target_lang="zh",
                   session_base=str(ROOT / "tmp" / "sess_cer"))
    pipe.start_session({"case": "cer_neural"})
    pipe.setup_lanes()
    pipe.start()
    pipe.run_lane_workers()
    deadline = time.monotonic() + 77 + 40
    quiet = None
    while time.monotonic() < deadline:
        bus.pump(0.05)
        reps_done = all(l.rep.done.is_set() for l in pipe.lanes if l.rep)
        if reps_done and all(l.seg_q.empty() for l in pipe.lanes) and pipe.stats["asr_done"] > 0:
            if quiet is None:
                quiet = time.monotonic()
            elif time.monotonic() - quiet > 5.0:
                break
        else:
            quiet = None
        time.sleep(0.5)
    pipe.stop()
    proc.terminate()

    # 按通道聚合（VAD 可能把一句切成多段，拼接后比对）
    from collections import defaultdict
    agg: dict[str, list[str]] = defaultdict(list)
    for r in sorted(results, key=lambda x: int(x["seg_id"].rsplit("-", 1)[-1])):
        agg[r["lane"]].append(r["text"])

    print(f"ASR 段数: {pipe.stats['asr_done']}，错误: {pipe.stats['errors']}")
    print("\n逐句 CER（神经语音，1x 实时）：")
    tot_cer = []
    for lane in ("mic", "sys"):
        joined = "".join(agg[lane])
        # 按脚本句数切分近似：直接整体 CER + 逐句贪心对齐
        exp = EXPECTED[lane]
        for i, e in enumerate(exp):
            # 在 joined 里找最接近的子串
            best = 1.0
            for h in _windows(joined, len(e)):
                best = min(best, cer(e, h))
            tot_cer.append(best)
            mark = "✓" if best < 0.2 else ("△" if best < 0.5 else "✗")
            print(f"  {mark} [{lane}] CER={best:.2f}  原文:{e[:24]:24s}")
    avg = sum(tot_cer) / len(tot_cer)
    print(f"\n平均 CER: {avg:.2f}（<0.15 优秀 / <0.3 良好 / >0.5 差）")
    print("=" * 50)
    print("NEURAL CER PASS" if avg < 0.3 else "NEURAL CER 偏高")
    sys.exit(0 if avg < 0.3 else 1)


def _windows(s: str, n: int):
    """滑动窗口候选（长度 n±40%）。"""
    if not s:
        yield ""
        return
    lo, hi = max(0, int(n * 0.6)), min(len(s), int(n * 1.4) + 1)
    seen = set()
    for start in range(0, len(s) - lo + 1, max(1, n // 4)):
        for length in range(lo, hi + 1, max(1, n // 8)):
            w = s[start:start + length]
            if w not in seen:
                seen.add(w)
                yield w


if __name__ == "__main__":
    main()
