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
import queue
import threading
import time
from dataclasses import dataclass, field

from ..core.config import Config
from ..core.events import EventBus
from ..core.status_machine import StatusMachine
from ..audio.ring_buffer import RingBuffer
from ..audio.replay_source import ReplaySource
from ..audio.devices import DeviceManager, WasapiSource
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
    rep: ReplaySource | None = None
    wasapi: WasapiSource | None = None
    lin: LinuxCapture | None = None
    seg_q: "queue.Queue[SpeechSegment]" = field(default_factory=queue.Queue)
    backlog: "BacklogGuard | None" = None  # 积压自适应守护（setup_lanes 装配）


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

    def start_session(self, meta: dict | None = None) -> None:
        """开会话（meta 缺省自动生成）。"""
        m = meta or {"app": "RecTheWord", "target_lang": self.target_lang,
                     "asr_engine": "qwen3-0.6b", "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.store.start(m)

    def end_session(self) -> None:
        self.store.close()

    # ---------- 启动 ----------

    def setup_lanes(self) -> None:
        """按 config 建立各声道（wasapi / alsa(Linux) 采集 或 replay 注入）。

        平台自适应：source 缺省/为空时，Windows 走 wasapi，Linux 走 alsa。
        """
        a = self.cfg.audio
        src = (a.source or "").lower()
        if src not in ("wasapi", "alsa", "replay"):
            import platform
            src = "wasapi" if platform.system() == "Windows" else "alsa"
            a.source = src
        self.device_mgr = DeviceManager() if src == "wasapi" else LinuxDeviceManager()
        pairs = [("mic", a.replay_mic), ("sys", a.replay_sys)]
        # VAD 加载显式广播（splash 第 2 步）
        sm_vad = StatusMachine(self.bus, "vad_load")
        sm_vad.begin("加载语音活动检测…")
        self._vad_ready = False
        for name, replay_file in pairs:
            if a.source == "replay" and not replay_file:
                continue
            ring = RingBuffer(int(a.sample_rate * a.chunk_ms / 1000 * 30))  # 30s 缓冲
            # P5 性能调优：激进切句（1.5s 上限）降低首字延迟；均衡 3s
            max_speech = 1500 if self.cfg.asr.segment_policy == "aggressive" else 3000
            vad = SileroVad(self._vad_path(),
                            threshold=self.cfg.vad.threshold,
                            min_speech_ms=self.cfg.vad.min_speech_ms,
                            min_silence_ms=self.cfg.vad.min_silence_ms,
                            max_speech_ms=max_speech)
            lane = LaneState(name=name, ring=ring, vad=vad)
            # 积压自适应：有界队列 + 背压守护（drop-oldest 保最新实时段）
            bl = self.cfg.backlog
            lane.seg_q = queue.Queue(maxsize=max(bl.maxsize, bl.high_watermark + 1))
            lane.backlog = BacklogGuard(bl, lane.seg_q)
            if a.source == "wasapi":
                dev_idx = getattr(a, f"{name}_device", None)
                lane.wasapi = WasapiSource(ring, name, device_index=dev_idx,
                                          device_manager=self.device_mgr)
            elif a.source == "alsa":
                dev_idx = getattr(a, f"{name}_device", None)
                lane.lin = LinuxCapture(ring, name, device_id=dev_idx,
                                       device_manager=self.device_mgr)
            elif a.source == "replay" and replay_file:
                from pathlib import Path
                p = Path(replay_file)
                if not p.is_absolute():
                    p = Path(__file__).resolve().parent.parent.parent / p
                lane.rep = ReplaySource(ring, p, speed=a.replay_speed)
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
            if l.name == lane and l.wasapi:
                l.wasapi.switch(device_index)
                return
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
            if lane.rep:
                lane.rep.start()
            if lane.wasapi:
                lane.wasapi.start()
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
            data = lane.ring.drain()
            if data:
                for seg in lane.vad.feed(data, lane.name):
                    self.bus.publish("seg", {
                        "lane": lane.name, "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms, "dur_ms": seg.end_ms - seg.start_ms,
                    })
                    # 背压入队：L2 超 drop 水位 drop-oldest（保最新实时段）
                    if lane.backlog is not None:
                        lane.backlog.enqueue(seg)
                    else:
                        lane.seg_q.put(seg)
                    self.stats["segs"] += 1
                    self._publish_backlog(lane)
            if lane.rep and lane.rep.done.is_set():
                # 回放结束：排空残余后退出
                time.sleep(0.2)
                data = lane.ring.drain()
                if data:
                    for seg in lane.vad.feed(data, lane.name):
                        if lane.backlog is not None:
                            lane.backlog.enqueue(seg)
                        else:
                            lane.seg_q.put(seg)
                break
            time.sleep(0.005)
        # 收尾：确保 VAD 恢复基线灵敏度
        if reduced:
            lane.vad.set_threshold(base_thr)
            lane.vad.set_min_silence_ms(base_min_sil)

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

    def _asr_worker_loop(self, lane: LaneState) -> None:
        """ASR 识别循环：取段 → 整句解码 → 广播 → 送翻译。"""
        assert self.asr is not None
        while not self._stop.is_set():
            try:
                seg = lane.seg_q.get(timeout=1.0)
            except queue.Empty:
                continue
            t0 = time.monotonic()
            try:
                res = self.asr.transcribe(seg.pcm, lane.name,
                                         timeout=max(30.0, (seg.end_ms - seg.start_ms) / 1000 * 3))
            except Exception as e:  # noqa: BLE001
                self.stats["errors"] += 1
                self.bus.publish("asr_err", {"lane": lane.name,
                                            "error": f"{type(e).__name__}: {e!r}"})
                continue
            t_first = time.monotonic() - t0  # 整句模式：一次性出全文
            text = res.get("text", "").strip()
            if not text:
                continue
            self.stats["asr_done"] += 1
            seg_id = f"{lane.name}-{seg.start_ms}"
            # P4：段能量（RMS）→ 粗分说话人
            import numpy as np
            pcm_i16 = np.frombuffer(seg.pcm, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(pcm_i16 ** 2))) if len(pcm_i16) else 0.0
            speaker = self.diarizer.assign(lane.name, rms, seg.start_ms)
            self._translations[seg_id] = []
            self.bus.publish("asr", {
                "lane": lane.name, "seg_id": seg_id,
                "text": text, "language": res.get("language", ""),
                "speaker": speaker,
                "t_first_ms": int(t_first * 1000), "t_final_ms": int(t_first * 1000),
            })
            # P4：落盘（译文稍后由 _translate 完成时补写；seg_id 供精确回填）
            self.store.add_line(lane.name, seg.start_ms, text,
                               res.get("language", ""), speaker,
                               seg_id=seg_id)
            self._translate(lane.name, seg_id, text, res.get("language", ""))

    def _translate(self, lane: str, seg_id: str, text: str, src_lang: str) -> None:
        """fire-and-forget 调翻译 API（流式）；不阻塞 ASR worker。
        API 不可用时显式降级提示（等待态机制的一部分）。"""
        if not self.api.available:
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
        """为每个声道启动 ASR worker 线程（start() 之后调用）。"""
        for lane in self.lanes:
            t = threading.Thread(target=self._asr_worker_loop, args=(lane,), daemon=True)
            t.start()
            self._workers.append(t)
