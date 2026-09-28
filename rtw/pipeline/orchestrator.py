"""Pipeline 编排器（路线 A：VAD 切句 + 整句解码 + API 翻译）。

职责：把「音频源 → RingBuffer → VAD → ASR worker → 翻译 API」串起来，
并通过 EventBus 广播全部状态事件（等待态机制：任何异步等待都必须显式告知用户）。

事件（topic → payload）：
  status  → (op, seq, StatusState)            模型加载/API 健康等
  asr     → {lane, seg_id, text, language, t_first_ms, t_final_ms}
  trans   → {lane, seg_id, delta}             译文增量（SSE 逐字）
  trans_done → {lane, seg_id, text}
  trans_err → {lane, seg_id, error}
  seg     → {lane, start_ms, end_ms, dur_ms}  VAD 切出的语音段
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field

from ..core.config import Config
from ..core.events import EventBus
from ..core.status_machine import StatusMachine
from ..audio.ring_buffer import RingBuffer
from ..audio.linux_capture import LinuxDeviceManager, LinuxCapture, probe_backend
from ..vad.silero import SileroVad, SpeechSegment
from ..asr.worker import AsrWorkerClient
from ..llm_api.client import LlmApiClient
from ..diarize.coarse import CoarseDiarizer
from ..session.store import SessionStore
from .backlog_guard import BacklogGuard

log = logging.getLogger(__name__)


@dataclass
class LaneState:
    name: str
    ring: RingBuffer
    vad: SileroVad
    lin: LinuxCapture | None = None
    seg_q: "queue.Queue[SpeechSegment]" = field(default_factory=queue.Queue)
    backlog: "BacklogGuard | None" = None  # 积压自适应守护（setup_lanes 装配）
    front_ts: int | None = None  # 队首段时间戳（时间戳公平调度用）
    skip_short_gap_ms: int = 400  # 距上一段 <此值则跳过（治碎片，用户可调）
    last_seg_end_ms: int = 0      # 上一已处理段终点（跳过判定用）
    _peek: "list" = field(default_factory=list)  # 影子队列：镜像 seg_q 用于 peek 队首


class Pipeline:
    def __init__(self, cfg: Config, bus: EventBus,
                 model_path: str, api_client: LlmApiClient,
                 target_lang: str = "zh",
                 session_base: str | None = None) -> None:
        self.cfg = cfg
        self.bus = bus
        self.model_path = model_path
        self.api = api_client
        self.target_lang = target_lang
        self.lanes: list[LaneState] = []
        self.asr: AsrWorkerClient | None = None
        self.corrector = None  # 本地纠错+翻译（Qwen3 0.6B，warmup 初始化）
        self._recent: dict[str, list[str]] = {}  # lane → 最近识别文本（纠错上下文）
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._stopped_once = False
        self._recording = False
        self._workers: list[threading.Thread] = []
        self.stats = {"segs": 0, "asr_done": 0, "trans_done": 0, "errors": 0}
        # P4：会话存储 + 实时粗分说话人
        self.store = SessionStore(session_base or cfg.session.dir)
        self.diarizer = CoarseDiarizer()
        self._translations: dict[str, list[str]] = {}  # seg_id → 译文增量
        self._rewritten: dict[str, str] = {}          # seg_id → 完整译文
        self._backlog_last: dict[str, str] = {}       # lane → 最近一次广播的积压状态
        # ---- 动态 pre_pad 实验（双通道共用一个 ASR 延迟 EMA）----
        # 策略：fixed=固定120 / dyn_shared=共享EMA动态 / dyn_perlane=per-lane对照
        self.pad_strategy = getattr(cfg.vad, "pad_strategy", "dyn_shared")
        self._asr_delay_ema = 0.0          # 共享 ASR 延迟 EMA（ms）
        self._lane_asr_delay: dict[str, float] = {}  # per-lane 延迟（对照组）
        self._pad_alpha = 0.3           # EMA 平滑系数
        # 录制时是否做实时说话人粗分（False=只离线精修才区分）
        self.live_diarize = getattr(cfg.vad, "live_diarize", False)

    def start_session(self, meta: dict | None = None) -> None:
        """开会话（meta 缺省自动生成）。"""
        m = meta or {"app": "RecTheWord", "target_lang": self.target_lang,
                     "asr_engine": "qwen3-0.6b", "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.store.start(m)

    def end_session(self) -> None:
        self.store.close()

    # ---------- 启动 ----------

    def setup_lanes(self) -> None:
        """按 config 建立各声道（alsa 采集，Linux 单一后端）。

        source 缺省/为空时回落 alsa；非法值（如历史遗留 wasapi）也回落 alsa。
        """
        a = self.cfg.audio
        src = (a.source or "").lower()
        if src != "alsa":
            src = "alsa"
            a.source = src
        self.device_mgr = LinuxDeviceManager()
        pairs = [("mic", None), ("sys", None)]
        # VAD 加载显式广播（splash 第 2 步）
        sm_vad = StatusMachine(self.bus, "vad_load")
        sm_vad.begin("加载语音活动检测…")
        self._vad_ready = False
        for name, _ in pairs:
            ring = RingBuffer(int(a.sample_rate * a.chunk_ms / 1000 * 30))  # 30s 缓冲
            # 切句上限：fast 2s / balanced 3s / aggressive 1.5s（短段→ASR 解码快→延迟低）
            # 有电平衰减保护（FORCE-DEFER）后，2s 不易切断字词
            sp = self.cfg.asr.segment_policy
            max_speech = {"fast": 2000, "balanced": 3000, "aggressive": 1500}.get(sp, 3000)
            # 方案选择：current=硬边界(VAD实时) / A=硬边界跟随ASR / B=去硬边界+tail_pad
            #          C=硬边界+pre_pad200+tail_pad100 / D=参数化(阈值+大BASE+DYN封顶+硬边界)
            scheme = getattr(self.cfg.vad, "scheme", "current")
            if scheme == "B":
                tail_pad, hard_boundary, follow_asr = 150, False, False
            elif scheme == "A":
                tail_pad, hard_boundary, follow_asr = 0, True, True
            elif scheme == "C":
                tail_pad, hard_boundary, follow_asr = 100, True, False
            elif scheme in ("D", "E"):
                tail_pad, hard_boundary, follow_asr = 0, True, False  # 不往后补音频（VAD 结束不拖延）
            else:  # current
                tail_pad, hard_boundary, follow_asr = 0, True, False
            # 方案 C：min_silence 提到 650ms；方案 D/E：保持 500（靠 merge_gap 治碎片）
            if scheme == "C":
                min_silence = 650
            else:
                min_silence = self.cfg.vad.min_silence_ms
            # 方案 E：分通道参数（mic 真人易噪抗噪 / sys TTS 清晰更细）
            vcfg = self.cfg.vad
            if name == "mic":
                thr = getattr(vcfg, "mic_threshold", vcfg.threshold)
                mgap = getattr(vcfg, "mic_merge_gap_ms", getattr(vcfg, "merge_gap_ms", 0))
                base = getattr(vcfg, "mic_pad_base_ms", getattr(vcfg, "pad_base_ms", 250))
            else:  # sys
                thr = getattr(vcfg, "sys_threshold", vcfg.threshold)
                mgap = getattr(vcfg, "sys_merge_gap_ms", getattr(vcfg, "merge_gap_ms", 0))
                base = getattr(vcfg, "sys_pad_base_ms", getattr(vcfg, "pad_base_ms", 250))
            vad = SileroVad(self._vad_path(),
                            threshold=thr,
                            min_speech_ms=self.cfg.vad.min_speech_ms,
                            min_silence_ms=min_silence,
                            max_speech_ms=max_speech,
                            merge_gap_ms=mgap,
                            tail_pad_ms=tail_pad,
                            hard_boundary=hard_boundary,
                            boundary_follows_asr=follow_asr)
            lane = LaneState(name=name, ring=ring, vad=vad)
            lane.pad_base = base  # 本 lane 的 pre_pad 基底（lane loop 动态计算用）
            lane.skip_short_gap_ms = getattr(self.cfg.vad, "skip_short_gap_ms", 0)
            # 积压自适应：有界队列 + 背压守护（drop-oldest 保最新实时段）
            bl = self.cfg.backlog
            lane.seg_q = queue.Queue(maxsize=max(bl.maxsize, bl.high_watermark + 1))
            lane.backlog = BacklogGuard(bl, lane.seg_q)
            dev_idx = getattr(a, f"{name}_device", None)
            lane.lin = LinuxCapture(ring, name, device_id=dev_idx,
                                    device_manager=self.device_mgr)
            self.lanes.append(lane)
        sm_vad.finish("VAD 就绪")
        self._vad_ready = True

    # ---- 设备管理（UI 调用：刷新 / 热切换）----

    def list_devices(self) -> dict[str, list[dict]]:
        return self.device_mgr.enumerate()

    def refresh_devices(self) -> dict[str, list[dict]]:
        return self.device_mgr.refresh()

    def switch_device(self, lane: str, device_index: int | None) -> None:
        """运行时热切换某路设备（自动重连，不打断下游 VAD）。"""
        for l in self.lanes:
            if l.name == lane and l.lin:
                l.lin.switch(device_index)
                return
        raise ValueError(f"unknown lane or not a capture source: {lane}")

    def _vad_path(self):
        from pathlib import Path
        p1 = self.cfg.models_dir() / "silero_vad" / "silero_vad.onnx"
        p2 = Path(__file__).resolve().parent.parent.parent / "models" / "silero_vad" / "silero_vad.onnx"
        return str(p1 if p1.exists() else p2)

    def warmup(self) -> None:
        """预热：只加载 ASR 子进程 + 检查 API 健康（不开采集、不开会话）。

        对应 splash 的 asr_load / api_health 阶段；采集等 start_recording()。
        """
        sm_asr = StatusMachine(self.bus, "asr_load")
        sm_asr.begin("ASR 模型加载中…")
        import os
        threads = self.cfg.asr.threads or (os.cpu_count() or 4)
        self.asr = AsrWorkerClient(self.model_path,
                                   threads=threads,
                                   max_new_tokens=self.cfg.asr.max_new_tokens)
        if self.asr.error_msg:
            sm_asr.error(self.asr.error_msg)
            raise RuntimeError(f"ASR worker 启动失败: {self.asr.error_msg}")
        sm_asr.finish("ASR 就绪")

        # 本地纠错 + 翻译（Qwen3 0.6B，可选；模型缺失则优雅降级）
        ccfg = self.cfg.corrector
        if ccfg.enabled:
            sm_cor = StatusMachine(self.bus, "corrector_load")
            sm_cor.begin("加载本地纠错/翻译模型…")
            try:
                from ..llm.local_corrector import LocalCorrectorClient
                from ..core.config import APP_ROOT
                import os as _os
                # 模型路径解析：相对路径按仓库根解析；本地目录直接用；
                # 仅当本地不存在时才经 ModelManager（避免 ModelScope 下载卡死启动）
                model_path = ccfg.model
                if not _os.path.isabs(model_path):
                    model_path = str(APP_ROOT / model_path)
                if not _os.path.isdir(model_path):
                    from ..core.model_manager import ModelManager
                    mm = ModelManager(self.cfg.models_dir(), self.bus)
                    model_path = str(mm.ensure("qwen3_llm"))
                import os as _os_dbg
                if _os_dbg.environ.get("RTW_DEBUG_CORRECT"):
                    print(f"[CORRECT-DBG] 初始化 corrector，路径={model_path}", flush=True)
                self.corrector = LocalCorrectorClient(model_path, threads=ccfg.threads)
                if self.corrector.error_msg:
                    if _os_dbg.environ.get("RTW_DEBUG_CORRECT"):
                        print(f"[CORRECT-DBG] corrector error_msg={self.corrector.error_msg}",
                              flush=True)
                    sm_cor.update(message=f"纠错模型加载失败（{self.corrector.error_msg}），降级")
                    self.corrector.shutdown()
                    self.corrector = None
                else:
                    if _os_dbg.environ.get("RTW_DEBUG_CORRECT"):
                        print(f"[CORRECT-DBG] corrector 就绪", flush=True)
                    sm_cor.finish("纠错/翻译模型就绪")
            except Exception as e:  # noqa: BLE001
                import traceback
                if _os_dbg.environ.get("RTW_DEBUG_CORRECT"):
                    print(f"[CORRECT-DBG] corrector 初始化异常: {e!r}\n"
                          f"{traceback.format_exc()}", flush=True)
                sm_cor.update(message=f"纠错模型不可用（{e}），降级")
                self.corrector = None

        if self.api.base:
            sm_api = StatusMachine(self.bus, "api_health")
            sm_api.begin("翻译 API 连通性检查…")
            ok = self.api.health()
            if ok:
                sm_api.finish("翻译 API 就绪")
            else:
                sm_api.update(message="翻译 API 暂不可用（降级：只显示原文）")
                sm_api.finish()

    def start_recording(self) -> None:
        """开始会议：开会话 + 启动各声道采集与 lane 线程（幂等）。"""
        if self._recording:
            return
        self._recording = True
        self.start_session()
        for lane in self.lanes:
            t = threading.Thread(target=self._lane_loop, args=(lane,), daemon=True)
            t.start()
            self._workers.append(t)
            if lane.lin:
                lane.lin.start()

    # 兼容旧调用
    def start(self) -> None:
        self.warmup()
        self.start_recording()

    def pause(self) -> None:
        """暂停：丢弃音频（VAD 不积累陈旧语音），ASR 队列排空后空闲。"""
        self._paused.set()

    def resume(self) -> None:
        """继续：清空 VAD 内部状态，从头切句。"""
        for l in self.lanes:
            try:
                l.vad.reset()
            except Exception:
                pass
        self._paused.clear()

    def stop(self) -> None:
        """停止（幂等）：结束会话 + 归档 + 关 ASR。关窗/多次调用安全。"""
        if self._stopped_once:
            return
        self._stopped_once = True
        self._recording = False
        self._stop.set()
        for t in self._workers:
            t.join(timeout=5)
        # P4：收尾——译文补写 + 关存储（先补写再关文件）
        self.finalize_translations()
        self.end_session()
        if self.asr:
            self.asr.shutdown()
        if self.corrector:
            self.corrector.shutdown()

    def reset(self) -> None:
        """重置 pipeline 以便开始新会议（不关 ASR 子进程，复用已加载的模型）。"""
        # 等待旧的 worker 线程退出（只 join 已启动的线程）
        self._stop.set()
        for t in self._workers:
            if t.is_alive():
                t.join(timeout=3)
        self._workers.clear()
        # 重置状态
        self._stop.clear()
        self._paused.clear()
        self._stopped_once = False
        self._recording = False
        self._translations.clear()
        self._rewritten.clear()
        self._asr_delay_ema = 0.0
        self._lane_asr_delay.clear()
        self.stats = {"segs": 0, "asr_done": 0, "trans_done": 0, "errors": 0}
        # 重置 VAD 状态
        for lane in self.lanes:
            try:
                lane.vad.reset()
            except Exception:
                pass
            lane.seg_q.queue.clear()
            if lane.backlog is not None:
                lane.backlog.reset()
        self._backlog_last.clear()
        # 重置会话存储（新会话）
        self.store.close()
        self.store.current = None
        self.store._transcript_f = None
        log.info("Pipeline reset complete, ready for new session")

    def generate_minutes(self, on_delta, on_done, on_error) -> None:
        """P4：会议纪要（走 API，用户约束 ①）。流式累积并落盘 minutes.md。"""
        from ..minutes.generator import MinutesGenerator
        buf: list[str] = []

        def _delta(d: str) -> None:
            buf.append(d)
            on_delta(d)

        def _done() -> None:
            self.store.save_minutes("".join(buf))
            on_done()

        gen = MinutesGenerator(self.api, self.store)
        gen.generate(_delta, _done, on_error)

    def refine_speakers(self) -> dict:
        """会后离线精修说话人（手动，零依赖启发式）。

        以实时阶段的 S{n} 标签为基础做合并/拆分，保持标签连续性。
        读 transcript.jsonl，按（通道 + 原始 speaker 标签）聚簇，
        结果写 labels.json。返回 {n_speakers, labels}。
        """
        import json
        rows = self.store.iter_transcript()
        if not rows:
            return {"n_speakers": 0, "labels": {}}
        # 以实时阶段的 S{n} 为基础：相同 (lane, speaker) 的行属于同一簇
        clusters: dict[tuple, list[dict]] = {}
        cluster_order: list[tuple] = []
        for r in rows:
            key = (r.get("lane"), r.get("speaker", "unknown"))
            if key not in clusters:
                clusters[key] = []
                cluster_order.append(key)
            clusters[key].append(r)
        # 保持原始 S{n} 标签，按首次出现顺序编号
        labels = {}
        spk_ids: list[str] = []
        for key in cluster_order:
            orig_speaker = key[1]
            # 如果原始标签是 S{n} 格式，保留；否则重新编号
            if orig_speaker.startswith("S") and orig_speaker[1:].isdigit():
                sid = orig_speaker
            else:
                sid = f"S{len(spk_ids) + 1}"
            if sid not in spk_ids:
                spk_ids.append(sid)
            for r in clusters[key]:
                seg_key = f'{r.get("lane")}-{r.get("ts")}'
                labels[seg_key] = sid
        self.store.save_labels(
            [{"id": spk_ids[i], "members": len(clusters[cluster_order[i]])}
             for i in range(len(cluster_order))])
        return {"n_speakers": len(spk_ids), "labels": labels}

    # ---------- 每声道循环 ----------

    def _lane_loop(self, lane: LaneState) -> None:
        """消费 RingBuffer → VAD 切句 → 送入 ASR 队列。暂停时丢弃音频。

        积压自适应（两级）：
        - L1 源头减量：队列超高水位 → 动态抬 VAD 阈值/拉长时间门限，少产段少喂 ASR。
        - L2 drop-oldest：L1 后仍超 drop 水位 → 丢最旧段兜底（保最新实时段）。
        压力缓解（recovered）→ VAD 参数自动恢复正常灵敏度。
        """
        bl = self.cfg.backlog
        base_thr = self.cfg.vad.threshold
        base_min_sil = self.cfg.vad.min_silence_ms
        reduced = False
        while not self._stop.is_set():
            if self._paused.is_set():
                lane.ring.drain()  # 丢弃，避免恢复后处理陈旧语音
                if reduced:  # 暂停时恢复 VAD 灵敏度
                    lane.vad.set_threshold(base_thr)
                    lane.vad.set_min_silence_ms(base_min_sil)
                    reduced = False
                time.sleep(0.05)
                continue
            # L1 源头减量：根据积压状态动态调 VAD（每轮检查，平滑过渡）
            if lane.backlog is not None and bl.enabled:
                want_reduce = lane.backlog.should_reduce()
                if want_reduce and not reduced:
                    lane.vad.set_threshold(bl.boost_threshold)
                    lane.vad.set_min_silence_ms(bl.boost_min_silence_ms)
                    reduced = True
                elif (not want_reduce) and reduced:
                    # 队列回落到高水位以下 → 恢复正常灵敏度
                    lane.vad.set_threshold(base_thr)
                    lane.vad.set_min_silence_ms(base_min_sil)
                    reduced = False
            # 动态 pre_pad = 固定基底(per-lane) + 动态补充（封顶），双通道共用共享 EMA
            # 固定基底保底（方案 E 分通道：mic 300 / sys 250）；动态补充按 ASR 延迟补
            scheme = getattr(self.cfg.vad, "scheme", "current")
            if scheme == "C":
                base, cap = 200, 0  # 固定 200ms，无动态补充 → 低延迟
            elif scheme in ("D", "E"):
                base = getattr(lane, "pad_base", getattr(self.cfg.vad, "pad_base_ms", 250))
                cap = getattr(self.cfg.vad, "pad_dyn_cap_ms", 400)
            else:
                base, cap = 120, 800
            if self.pad_strategy == "fixed":
                pad = base
            elif self.pad_strategy == "dyn_perlane":
                raw = self._lane_asr_delay.get(lane.name, 0.0)
                pad = base + max(0.0, min(cap, raw))
            else:  # dyn_shared（默认）
                pad = base + max(0.0, min(cap, self._asr_delay_ema))
            lane.vad.set_pre_pad_ms(int(pad))
            data = lane.ring.drain()
            if data:
                # 音频预处理：有条件增益 + 慢启动快释放压缩（提升弱句首信噪比）
                from ..audio.preprocess import preprocess as _pp
                data = _pp(data)
                for seg in lane.vad.feed(data, lane.name):
                    self.bus.publish("seg", {
                        "lane": lane.name, "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms, "dur_ms": seg.end_ms - seg.start_ms,
                    })
                    self._enqueue_seg(lane, seg)
            time.sleep(0.005)
        # 收尾：确保 VAD 恢复基线灵敏度
        if reduced:
            lane.vad.set_threshold(base_thr)
            lane.vad.set_min_silence_ms(base_min_sil)

    def _enqueue_seg(self, lane: LaneState, seg) -> None:
        """入队封装：过短间隔段**合并到上一段**（治碎片+不丢音频）+ 维护调度状态。
        合并规则：距上一段终点 < skip_short_gap_ms 且上一段仍在队列（未被 ASR 取走）
        → 把本段 PCM 追加到上一段尾部、延长其 end_ms（音频不丢，碎片被吸收）。
        上一段已被处理 → 本段单独入队。"""
        merged = False
        if lane.skip_short_gap_ms > 0 and lane._peek:
            prev = lane._peek[-1]  # 队列里最后一段（即将/正在被处理的上一段）
            gap = seg.start_ms - prev.end_ms
            if prev is not None and gap < lane.skip_short_gap_ms:
                # 合并：延长上一段（原地修改 SpeechSegment 字段）
                prev.pcm = prev.pcm + seg.pcm
                prev.end_ms = seg.end_ms
                merged = True  # 不单独入队，音频已并入上一段
                if os.environ.get("RTW_DEBUG_VAD"):
                    print(f"[VAD-DBG] {lane.name} MERGE gap={gap}ms "
                          f"(seg {seg.start_ms}-{seg.end_ms} → 并入 prev 至 {prev.end_ms})",
                          flush=True)
        if os.environ.get("RTW_DEBUG_VAD") and not merged:
            print(f"[VAD-DBG] {lane.name} ENQUEUE {seg.start_ms}-{seg.end_ms} "
                  f"(dur {seg.end_ms-seg.start_ms}ms)", flush=True)
        if not merged:
            was_empty = lane.seg_q.empty()
            if lane.backlog is not None:
                lane.backlog.enqueue(seg)
            else:
                lane.seg_q.put(seg)
            lane._peek.append(seg)
            if was_empty:
                lane.front_ts = seg.start_ms
            self.stats["segs"] += 1
            self._publish_backlog(lane)

    def _publish_backlog(self, lane: LaneState) -> None:
        """积压状态/层级迁移时广播 "backlog" 事件（仅变化时发，避免刷屏）。"""
        g = lane.backlog
        if g is None:
            return
        key = (g.state.value, g.level())
        last = self._backlog_last.get(lane.name)
        if key != last:
            self._backlog_last[lane.name] = key
            snap = g.snapshot()
            self.bus.publish("backlog", {
                "lane": lane.name, "state": snap["state"], "level": snap["level"],
                "depth": snap["depth"], "dropped": snap["dropped"],
                "peak_depth": snap["peak_depth"],
            })

    def _translate(self, lane: str, seg_id: str, text: str, src_lang: str) -> None:
        """fire-and-forget 调翻译 API（流式）；不阻塞 ASR worker。
        API 不可用时显式降级提示（等待态机制的一部分）。"""
        if not self.api.available:
            # 本地翻译回退：无翻译 API 且开启 local_translate 且有纠错模型
            if (self.cfg.corrector.local_translate and self.corrector is not None
                    and src_lang.lower() != self.target_lang.lower()):
                try:
                    trans = self.corrector.translate(text, self.target_lang, timeout=20.0)
                    if trans and trans.strip():
                        self.bus.publish("trans", {"lane": lane, "seg_id": seg_id,
                                                  "delta": trans.strip()})
                        self._translations.setdefault(seg_id, []).append(trans.strip())
                        self.stats["trans_done"] += 1
                        self._rewritten[seg_id] = trans.strip()
                        return
                except Exception:  # noqa: BLE001
                    pass  # 本地翻译失败 → 落到下面的显式降级
            self.bus.publish("trans_err", {
                "lane": lane, "seg_id": seg_id,
                "error": "翻译 API 不可用，仅显示原文"})
            return

        def _delta(d: str) -> None:
            self.bus.publish("trans", {"lane": lane, "seg_id": seg_id, "delta": d})
            self._translations.setdefault(seg_id, []).append(d)

        def _done() -> None:
            self.stats["trans_done"] += 1
            # P4：译文补写进会话存储
            joined = "".join(self._translations.get(seg_id, []))
            if joined:
                self._rewritten[seg_id] = joined

        def _err(e: str) -> None:
            self.bus.publish("trans_err", {"lane": lane, "seg_id": seg_id, "error": e})

        self.api.translate_stream(text, src_lang, self.target_lang, _delta, _done, _err)

    def finalize_translations(self) -> None:
        """会话结束时把已收集的译文按 seg_id 精确补写进 transcript.jsonl。"""
        if not self._rewritten or self.store.current is None:
            return
        f = self.store.current / "transcript.jsonl"
        if not f.exists():
            return
        import json as _json
        lines = []
        for raw in f.read_text(encoding="utf-8").splitlines():
            d = _json.loads(raw)
            sid = d.get("seg_id", "")
            if sid and sid in self._rewritten:
                d["translated"] = self._rewritten[sid]
            lines.append(d)
        f.write_text("\n".join(_json.dumps(d, ensure_ascii=False) for d in lines) + "\n",
                     encoding="utf-8")

    def run_lane_workers(self) -> None:
        """启动 ASR 调度（start() 之后调用）。
        单实例：单一调度器串行服务两通道队列（省内存）。max_speech 3s 短段
        使单段解码快，排队堆积可控（p90 ~5s）。"""
        t = threading.Thread(target=self._asr_dispatcher, daemon=True)
        t.start()
        self._workers.append(t)

    def _asr_dispatcher(self) -> None:
        """（备用）时间戳公平调度：单实例串行模式。双实例模式下不使用。"""
        assert self.asr is not None
        lanes = self.lanes
        if not lanes:
            return
        # FIFO + 追赶机制：基础按入队先后（FIFO），但连续两次服务同一通道时，
        # 把另一通道队首往前提一位（防止某通道因段多而长期饿死另一通道）。
        last_lane = None
        last2_lane = None
        while not self._stop.is_set():
            # 收集各通道队首（用影子 peek）
            candidates = []
            for lane in lanes:
                if lane.seg_q.empty():
                    continue
                candidates.append(lane)
            if not candidates:
                time.sleep(0.01)
                continue
            # 追赶判定：连续两次都是同一通道 → 本次优先另一通道
            boosted = None
            if last_lane is not None and last_lane == last2_lane:
                other = [l for l in lanes if l is not last_lane and not l.seg_q.empty()]
                if other:
                    boosted = other[0]
            # 选择：有 boost 优先 boost；否则 FIFO（按 front_ts 最早，退化用入队序）
            if boosted is not None:
                chosen = boosted
            else:
                # FIFO：选 front_ts 最小的（最早入队的）
                chosen = min(candidates, key=lambda l: (l.front_ts if l.front_ts is not None else 1<<62))
            try:
                seg = chosen.seg_q.get_nowait()
            except queue.Empty:
                time.sleep(0.005)
                continue
            self._process_asr_seg(chosen, seg)
            last2_lane = last_lane
            last_lane = chosen

    def _recent_texts(self, lane_name: str) -> list[str]:
        """取纠错上下文：本 lane + 另一 lane 的最近句（交错，最多 10 条，排除当前句）。
        跨通道上下文帮助 LLM 理解对话语境（问答对应关系）。"""
        n = max(self.cfg.corrector.context_sentences, 10)
        mine = self._recent.get(lane_name, [])
        others = [l for l in self._recent if l != lane_name]
        other_flat = [t for l in others for t in self._recent.get(l, [])]
        # 本通道最近 + 他通道最近，合并去重（保序），取最后 n 条
        combined = list(dict.fromkeys(mine + other_flat))
        return combined[-n:]

    def _process_asr_seg(self, lane: LaneState, seg) -> None:
        """解码单个段（单实例：共享 self.asr 串行解码）。"""
        t0 = time.monotonic()
        try:
            res = self.asr.transcribe(seg.pcm, lane.name,
                                     timeout=max(30.0, (seg.end_ms - seg.start_ms) / 1000 * 3))
        except Exception as e:  # noqa: BLE001
            self.stats["errors"] += 1
            self.bus.publish("asr_err", {"lane": lane.name,
                                        "error": f"{type(e).__name__}: {e!r}"})
            return
        t_first = time.monotonic() - t0
        text = res.get("text", "").strip()
        if not text:
            return
        # 本地纠错（+ 无 API 时一并本地翻译）：拿前 N 句上下文（含另一通道）
        # 守门：①过短片段(<4字)跳过（残片易被脑补）②相似度守门——纠错结果与原文
        # 差异过大（<0.5）视为幻觉（用上下文替换/啰嗦重复），丢弃回退原文
        local_translated = ""
        corr_ok = (self.corrector is not None and self.corrector.alive())
        if corr_ok and len(text) >= 4:
            ctx = self._recent_texts(lane.name)
            # 无翻译 API 且开启本地翻译 → 一次请求同时纠错+翻译（省一次往返）
            src_lang = res.get("language", "")
            want_local_trans = (self.cfg.corrector.local_translate
                                and not self.api.available
                                and src_lang.lower() != self.target_lang.lower())
            try:
                if want_local_trans:
                    r = self.corrector.fix_translate(ctx, text, self.target_lang,
                                                   timeout=10.0)
                    fixed, local_translated = r.get("fixed", ""), r.get("translated", "")
                else:
                    fixed = self.corrector.correct(ctx, text, timeout=8.0)
                    local_translated = ""
                fixed = (fixed or "").strip()
                if fixed and fixed != text:
                    import difflib
                    sim = difflib.SequenceMatcher(None, text, fixed).ratio()
                    if sim < 0.5:
                        if os.environ.get("RTW_DEBUG_CORRECT"):
                            print(f"[CORRECT-DBG] {lane.name} 丢弃(幻觉 sim={sim:.2f}) "
                                  f"「{text}」→「{fixed}」", flush=True)
                        fixed = text  # 幻觉回退原文
                    else:
                        if os.environ.get("RTW_DEBUG_CORRECT"):
                            print(f"[CORRECT-DBG] {lane.name} 「{text}」→「{fixed}」",
                                  flush=True)
                        text = fixed
            except Exception:  # noqa: BLE001
                if os.environ.get("RTW_DEBUG_CORRECT"):
                    print(f"[CORRECT-DBG] {lane.name} 纠错失败，回退原文", flush=True)
                fixed, local_translated = text, ""
        elif os.environ.get("RTW_DEBUG_CORRECT"):
            print(f"[CORRECT-DBG] corrector 不可用（{'未初始化' if self.corrector is None else '子进程已退出'}），降级",
                  flush=True)
        lane.vad.confirm_segment_processed()
        self.stats["asr_done"] += 1
        # 维护调度状态：记录本段终点（跳过判定用）
        lane.last_seg_end_ms = max(lane.last_seg_end_ms, seg.end_ms)
        # 弹出影子队首，用真实的新队首更新时间戳（精确 peek，非近似）
        if lane._peek:
            lane._peek.pop(0)
        if lane._peek:
            lane.front_ts = lane._peek[0].start_ms
        else:
            lane.front_ts = None
        seg_id = f"{lane.name}-{seg.start_ms}"
        # 记录 ASR 延迟（供动态 pre_pad：共享 EMA + per-lane 对照）
        d_ms = t_first * 1000.0
        self._asr_delay_ema = (self._pad_alpha * d_ms
                              + (1 - self._pad_alpha) * self._asr_delay_ema)
        self._lane_asr_delay[lane.name] = d_ms
        # 说话人：录制时实时粗分（live_diarize）或仅离线精修
        speaker = ""
        if self.live_diarize:
            import numpy as np
            pcm_i16 = np.frombuffer(seg.pcm, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(pcm_i16 ** 2))) if len(pcm_i16) else 0.0
            speaker = self.diarizer.assign(lane.name, rms, seg.start_ms)
        # 维护纠错上下文（本 lane 最近 N 句）
        self._recent.setdefault(lane.name, []).append(text)
        self._recent[lane.name] = self._recent[lane.name][-8:]
        self._translations[seg_id] = []
        self.bus.publish("asr", {
            "lane": lane.name, "seg_id": seg_id,
            "text": text, "language": res.get("language", ""),
            "speaker": speaker,
            "t_first_ms": int(t_first * 1000), "t_final_ms": int(t_first * 1000),
        })
        # P4：落盘（译文稍后补写；seg_id 供精确回填）
        self.store.add_line(lane.name, seg.start_ms, text,
                           res.get("language", ""), speaker,
                           seg_id=seg_id)
        # 本地翻译已随纠错一并得到 → 直接发布，跳过 API 翻译
        if local_translated:
            self.bus.publish("trans", {"lane": lane.name, "seg_id": seg_id,
                                      "delta": local_translated})
            self._translations[seg_id] = [local_translated]
            self.stats["trans_done"] += 1
            self._rewritten[seg_id] = local_translated
        else:
            self._translate(lane.name, seg_id, text, res.get("language", ""))
