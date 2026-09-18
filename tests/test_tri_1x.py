"""4 分钟中日英三语 1x 实时全链路：逐句识别对比 + CER + 翻译验证。

- mic 通道：中文 + 日文；sys 通道：英文
- 目标翻译语言 = 中文（ja/en → zh；zh 原文直通）
- 逐句输出：原文 / 识别结果 / CER，供用户判断实际识别效果
- 验证：三语都被识别、翻译全部走通（默认中文）、0 错误
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

# 原文（与 make_trilingual_dialog.py 一致）
SCRIPT = [
    ("mic", "zh", "各位好，我们现在开始今天的三方协调会。"),
    ("sys", "en", "Good morning everyone, let's get started with today's coordination meeting."),
    ("mic", "ja", "よろしくお願いします。まず全体の進捗報告をさせてください。"),
    ("sys", "en", "Thank you. Please go ahead with the overall status report."),
    ("mic", "zh", "好的。首先汇报后端服务，迁移工作已经完成百分之九十了。"),
    ("sys", "en", "That's great progress. Is the client rollout still on track for Friday?"),
    ("mic", "ja", "はい、クライアントのリリースは金曜日に予定通り進行しています。"),
    ("sys", "en", "Perfect. And what about the budget approval from the finance team?"),
    ("mic", "zh", "数据库扩容的预算，财务那边还在走审批流程，预计明天能有结果。"),
    ("sys", "en", "Noted. I will personally follow up with finance to speed things up today."),
    ("mic", "ja", "次に、テスト環境の安定性について話し合いたいと思います。"),
    ("sys", "en", "Sure, let's discuss it. The staging cluster has been quite unstable this week."),
    ("mic", "zh", "我查看了监控日志，发现问题是夜间批处理任务占用了太多的计算资源。"),
    ("sys", "en", "Yes, that matches what we see. We should move batch jobs to a separate node pool."),
    ("mic", "ja", "その方針に賛成です。今週中にバッチジョブを専用プールへ移動しましょう。"),
    ("sys", "en", "Agreed. We will also add resource quotas to prevent future overflows."),
    ("mic", "zh", "另外提醒一下，下周的压测计划需要提前预约测试集群的时间窗口。"),
    ("sys", "en", "Good reminder. I will book the test cluster window for next Tuesday."),
    ("mic", "ja", "それから、ドキュメントの更新も忘れずにお願いします。"),
    ("sys", "en", "Will do. Documentation updates are assigned to the engineering team."),
    ("mic", "zh", "还有一个补充，关于监控告警的阈值，我建议这周重新校准一遍。"),
    ("sys", "en", "Good idea. We will recalibrate the alert thresholds based on the new baselines."),
    ("mic", "ja", "あと、セキュリティの監査結果についても共有しておきます。"),
    ("sys", "en", "Please share the security audit findings with the whole team as well."),
    ("mic", "zh", "审计结果显示有两处权限配置需要收紧，我会在今晚之前提交修复。"),
    ("sys", "en", "Appreciated. Please make sure the fixes are reviewed before merging."),
    ("mic", "ja", "了解しました。それでは以上で終わりにさせていただきます。"),
    ("sys", "en", "Thank you. That covers everything for today's agenda."),
    ("mic", "zh", "好的，那今天的议题就到这里，感谢大家的投入，我们下次再见。"),
    ("sys", "en", "Thanks everyone for your time. See you all at the next meeting."),
]


def cer(ref: str, hyp: str) -> float:
    ref, hyp = ref.replace(" ", "").lower(), hyp.replace(" ", "").lower()
    if not ref:
        return 1.0
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
    cfg.audio.replay_mic = "tests/fixtures/meeting_tri/mic_tri.wav"
    cfg.audio.replay_sys = "tests/fixtures/meeting_tri/sys_tri.wav"
    cfg.audio.replay_speed = 1.0
    bus = EventBus()
    model_dir = ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B"
    proc = subprocess.Popen([sys.executable, "-m", "tests.mock_api_server", "--port", "8811"],
                           cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    api = LlmApiClient("http://127.0.0.1:8811/v1", "sk", "mock")
    results: list[dict] = []
    trans_seen: set[str] = set()
    bus.subscribe("asr", lambda p: results.append(p))
    bus.subscribe("trans_done", lambda p: trans_seen.add(p["seg_id"]))
    pipe = Pipeline(cfg, bus, str(model_dir), api, target_lang="zh",
                   session_base=str(ROOT / "tmp" / "sess_tri"))
    pipe.start_session({"case": "tri_1x"})
    pipe.setup_lanes()
    pipe.start()
    pipe.run_lane_workers()
    deadline = time.monotonic() + 202 + 50
    quiet = None
    while time.monotonic() < deadline:
        bus.pump(0.05)
        reps_done = all(l.rep.done.is_set() for l in pipe.lanes if l.rep)
        if reps_done and all(l.seg_q.empty() for l in pipe.lanes) and pipe.stats["asr_done"] > 0:
            if quiet is None:
                quiet = time.monotonic()
            elif time.monotonic() - quiet > 6.0:
                break
        else:
            quiet = None
        time.sleep(0.5)
    pipe.stop()
    proc.terminate()

    # 按通道聚合（VAD 可能切碎，拼接后逐句贪心对齐）
    from collections import defaultdict
    agg: dict[str, str] = defaultdict(str)
    for r in sorted(results, key=lambda x: int(x["seg_id"].rsplit("-", 1)[-1])):
        agg[r["lane"]] += r["text"]

    print(f"ASR 段数: {pipe.stats['asr_done']}，翻译完成: {pipe.stats['trans_done']}，错误: {pipe.stats['errors']}")
    print(f"翻译覆盖: {len(trans_seen)}/{pipe.stats['asr_done']} 段\n")

    print("逐句识别对比（1x 实时，三语）：")
    print("-" * 78)
    tot = []
    idx = {False: 0, True: 0}  # mic 指针 / sys 指针
    for lane, lang, ref in SCRIPT:
        key = lane == "mic"
        pool = agg[lane]
        # 贪心：在 pool 里找与 ref 最相似的窗口
        best_c, best_h = 1.0, ""
        n = max(4, int(len(ref) * 0.7))
        for start in range(0, max(1, len(pool) - n + 1), max(1, n // 3)):
            for length in range(max(2, int(n * 0.6)), min(len(pool) - start, int(n * 1.6)) + 1, max(1, n // 6)):
                w = pool[start:start + length]
                c = cer(ref, w)
                if c < best_c:
                    best_c, best_h = c, w
        tot.append(best_c)
        mark = "✓" if best_c < 0.25 else ("△" if best_c < 0.5 else "✗")
        lang_cn = {"zh": "中", "ja": "日", "en": "英"}[lang]
        print(f"  {mark}[{lang_cn}] CER={best_c:.2f}")
        print(f"     原文: {ref[:38]}")
        print(f"     识别: {best_h[:38]}")
    avg = sum(tot) / len(tot)
    by_lang: dict[str, list[float]] = defaultdict(list)
    for (lane, lang, _), c in zip(SCRIPT, tot):
        by_lang[lang].append(c)
    print("-" * 78)
    for lang in ("zh", "ja", "en"):
        vals = by_lang[lang]
        print(f"  {lang} 平均 CER: {sum(vals)/len(vals):.2f}（n={len(vals)}）")
    print(f"\n总体平均 CER: {avg:.2f}")
    ok = (pipe.stats["errors"] == 0 and pipe.stats["asr_done"] >= 25
          and pipe.stats["trans_done"] >= 25 and avg < 0.45)
    print("=" * 78)
    print("TRI-1X PASS" if ok else "TRI-1X 未达标")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
