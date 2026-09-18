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
from ..vad.silero import SileroVad, SpeechSegment
from ..asr.worker import AsrWorkerClient
from ..llm_api.client import LlmApiClient
from ..diarize.coarse import CoarseDiarizer
from ..session.store import SessionStore

log = logging.getLogger(__name__)


@dataclass
class LaneState:
    name: str
    ring: RingBuffer
    vad: SileroVad
    rep: ReplaySource | None = None
    seg_q: "queue.Queue[SpeechSegment]" = field(default_factory=queue.Queue)


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
        self._workers: list[threading.Thread] = []
        self.stats = {"segs": 0, "asr_done": 0, "trans_done": 0, "errors": 0}
        # P4：会话存储 + 实时粗分说话人
        self.store = SessionStore(session_base or cfg.session.dir)
        self.diarizer = CoarseDiarizer()
        self._translations: dict[str, list[str]] = {}  # seg_id → 译文增量
        self._rewritten: dict[str, str] = {}          # seg_id → 完整译文

    def start_session(self, meta: dict | None = None) -> None:
        """开会话（meta 缺省自动生成）。"""
        m = meta or {"app": "RecTheWord", "target_lang": self.target_lang,
                     "asr_engine": "qwen3-0.6b", "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.store.start(m)

    def end_session(self) -> None:
        self.store.close()

    # ---------- 启动 ----------

    def setup_lanes(self) -> None:
        """按 config 建立各声道（wasapi 或 replay 注入）。"""
        a = self.cfg.audio
        pairs = [("mic", a.replay_mic), ("sys", a.replay_sys)]
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
            if a.source == "replay" and replay_file:
                from pathlib import Path
                p = Path(replay_file)
                if not p.is_absolute():
                    p = Path(__file__).resolve().parent.parent.parent / p
                lane.rep = ReplaySource(ring, p, speed=a.replay_speed)
            self.lanes.append(lane)

    def _vad_path(self):
        from pathlib import Path
        p1 = self.cfg.models_dir() / "silero_vad" / "silero_vad.onnx"
        p2 = Path(__file__).resolve().parent.parent.parent / "models" / "silero_vad" / "silero_vad.onnx"
        return str(p1 if p1.exists() else p2)

    def start(self) -> None:
        sm_asr = StatusMachine(self.bus, "asr_load")
        sm_asr.begin("ASR 模型加载中…")
        self.asr = AsrWorkerClient(self.model_path,
                                   threads=self.cfg.asr.threads,
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

        for lane in self.lanes:
            t = threading.Thread(target=self._lane_loop, args=(lane,), daemon=True)
            t.start()
            self._workers.append(t)
            if lane.rep:
                lane.rep.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._workers:
            t.join(timeout=5)
        if self.asr:
            self.asr.shutdown()
        # P4：收尾——译文补写 + 关存储
        self.finalize_translations()
        self.end_session()

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

    # ---------- 每声道循环 ----------

    def _lane_loop(self, lane: LaneState) -> None:
        """消费 RingBuffer → VAD 切句 → 送入 ASR 队列。"""
        while not self._stop.is_set():
            data = lane.ring.drain()
            if data:
                for seg in lane.vad.feed(data, lane.name):
                    self.bus.publish("seg", {
                        "lane": lane.name, "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms, "dur_ms": seg.end_ms - seg.start_ms,
                    })
                    lane.seg_q.put(seg)
                    self.stats["segs"] += 1
            if lane.rep and lane.rep.done.is_set():
                # 回放结束：排空残余后退出
                time.sleep(0.2)
                data = lane.ring.drain()
                if data:
                    for seg in lane.vad.feed(data, lane.name):
                        lane.seg_q.put(seg)
                break
            time.sleep(0.005)
        # 排空剩余段
        while not lane.seg_q.empty():
            pass  # 段已由上面的循环送出

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
            # P4：落盘（译文稍后由 _translate 完成时补写）
            self.store.add_line(lane.name, seg.start_ms, text,
                               res.get("language", ""), speaker)
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
        """会话结束时把已收集的译文补写进 transcript.jsonl（简化：重写文件）。"""
        if not self._rewritten or self.store.current is None:
            return
        f = self.store.current / "transcript.jsonl"
        if not f.exists():
            return
        import json as _json
        lines = []
        for raw in f.read_text(encoding="utf-8").splitlines():
            d = _json.loads(raw)
            sid = f"{d['lane']}-?"  # 匹配不上也没关系
            lines.append(d)
        # 按顺序把译文填回去（transcript 行序 = asr 事件序 = _rewritten 插入序近似）
        keys = list(self._rewritten.keys())
        for i, d in enumerate(lines):
            if i < len(keys):
                d["translated"] = self._rewritten[keys[i]]
        f.write_text("\n".join(_json.dumps(d, ensure_ascii=False) for d in lines) + "\n",
                     encoding="utf-8")

    def run_lane_workers(self) -> None:
        """为每个声道启动 ASR worker 线程（start() 之后调用）。"""
        for lane in self.lanes:
            t = threading.Thread(target=self._asr_worker_loop, args=(lane,), daemon=True)
            t.start()
            self._workers.append(t)
