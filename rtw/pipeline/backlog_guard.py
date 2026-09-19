"""ASR 积压自适应恢复（背压 + drop-oldest + 状态机）。

问题：seg_q 是无界队列，ASR 处理跟不上 VAD 产段速度时队列无限堆积 →
端到端延迟无限增长 + 内存膨胀 + 实时性崩坏。

机制（三件套）：
1. 背压（backpressure）：lane 循环喂段前查队列深度，超过高水位就 drop-oldest
   （丢弃最旧的段，保住最新的实时段）——实时字幕宁可丢旧的，不能让延迟无限涨。
2. 有界兜底：seg_q 设 maxsize，极端情况下 put 阻塞前由 guard 预先 drop，绝不撑爆。
3. 自适应状态机：normal → backlogged（超高水位）→ recovered（持续低压 grace 秒）。
   状态迁移经 EventBus 广播 "backlog" 事件，UI 据此提示"处理积压，已自适应丢弃"。

设计取舍：
- drop-oldest 而非 drop-newest：实时场景里"最新的语音"最有价值，旧段迟到就没意义。
- 高低水位滞回（hysteresis）：避免在水位线附近抖动反复进出 backlogged。
- recover_grace_s 防抖：短暂回落不算恢复，须持续低压一段时间才宣告 recovered。
"""
from __future__ import annotations

import enum
import queue
import time
from dataclasses import dataclass, field

from ..core.config import BacklogCfg


class BacklogState(enum.Enum):
    NORMAL = "normal"
    BACKLOGGED = "backlogged"
    RECOVERED = "recovered"


@dataclass
class BacklogStats:
    enqueued: int = 0
    dropped: int = 0
    peak_depth: int = 0
    backlogged_since: float | None = None   # 进入 backlogged 的时刻
    low_since: float | None = None          # 连续低于低水位的起始时刻


class BacklogGuard:
    """单个声道的积压守护：包住该声道的 seg_q，提供背压入队 + 状态机。"""

    def __init__(self, cfg: BacklogCfg, q: "queue.Queue") -> None:
        self.cfg = cfg
        self.q = q
        self.state = BacklogState.NORMAL
        self.stats = BacklogStats()
        self._recover_armed = False  # 是否已发出 backlogged（用于 recovered 只发一次）

    # ---- 背压入队（两级：L1 源头减量 由调用方调 VAD；L2 drop-oldest 在此）----

    def should_reduce(self) -> bool:
        """L1 决策：队列超过高水位 → 调用方应调 VAD 减量（抬阈值/拉长时间门限）。"""
        return self.cfg.enabled and self.q.qsize() > self.cfg.high_watermark

    def should_drop(self) -> bool:
        """L2 决策：L1 减量后仍超过 drop 水位 → 开始 drop-oldest 兜底。"""
        return self.cfg.enabled and self.q.qsize() > self.cfg.drop_watermark

    def enqueue(self, seg) -> bool:
        """入队一个段；L2 超 drop 水位时 drop-oldest（保最新实时段）。

        返回 True=已入队，False=被丢弃。
        L1（源头减量）不在这里做——由 lane 循环根据 should_reduce() 调 VAD 实现，
        因为减量作用于"还没产生的段"，而非已入队的段。
        """
        if not self.cfg.enabled:
            self.q.put(seg)
            self.stats.enqueued += 1
            return True

        # L2 兜底：超过 drop 水位才 drop-oldest（L1 减量通常能把深度压在高水位附近）
        if self.q.qsize() > self.cfg.drop_watermark:
            target = self.cfg.high_watermark
            while self.q.qsize() >= max(target, 1):
                try:
                    self.q.get_nowait()  # 丢最旧
                    self.stats.dropped += 1
                except queue.Empty:
                    break
        # 硬上限兜底
        if self.q.qsize() >= self.cfg.maxsize:
            try:
                self.q.get_nowait()
                self.stats.dropped += 1
            except queue.Empty:
                pass

        self.q.put(seg)
        self.stats.enqueued += 1
        self.stats.peak_depth = max(self.stats.peak_depth, self.q.qsize())
        self._update_state()
        return True

    def drain_oldest(self, n: int) -> int:
        """丢弃最旧的 n 个段（供外部强制清空，如暂停/重置）。返回实际丢弃数。"""
        k = 0
        for _ in range(n):
            try:
                self.q.get_nowait()
                k += 1
            except queue.Empty:
                break
        self.stats.dropped += k
        return k

    # ---- 状态机 ----

    def _update_state(self) -> None:
        depth = self.q.qsize()
        now = time.monotonic()
        hw, lw = self.cfg.high_watermark, self.cfg.low_watermark

        if self.state == BacklogState.BACKLOGGED:
            # 持续低于低水位 grace 秒 → recovered
            if depth <= lw:
                if self.stats.low_since is None:
                    self.stats.low_since = now
                elif (now - self.stats.low_since) >= self.cfg.recover_grace_s:
                    self._transition(BacklogState.RECOVERED)
            else:
                self.stats.low_since = None
        elif self.state in (BacklogState.NORMAL, BacklogState.RECOVERED):
            # 超过高水位 → backlogged
            if depth > hw:
                self.stats.low_since = None
                self._transition(BacklogState.BACKLOGGED)
            else:
                self.stats.low_since = None

    def _transition(self, new: BacklogState) -> None:
        if new == self.state:
            return
        self.state = new
        if new == BacklogState.BACKLOGGED:
            self.stats.backlogged_since = time.monotonic()
            self._recover_armed = True
        elif new == BacklogState.RECOVERED:
            self._recover_armed = False

    # ---- 供编排器读取/广播 ----

    def level(self) -> int:
        """当前应对层级：0=正常，1=L1 源头减量，2=L2 drop-oldest 兜底。"""
        if not self.cfg.enabled:
            return 0
        if self.q.qsize() > self.cfg.drop_watermark:
            return 2
        if self.q.qsize() > self.cfg.high_watermark:
            return 1
        return 0

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "level": self.level(),
            "depth": self.q.qsize(),
            "dropped": self.stats.dropped,
            "enqueued": self.stats.enqueued,
            "peak_depth": self.stats.peak_depth,
            "high_watermark": self.cfg.high_watermark,
            "low_watermark": self.cfg.low_watermark,
        }

    def reset(self) -> None:
        """重置守护状态（不清队列，队列由调用方清）。"""
        self.state = BacklogState.NORMAL
        self.stats = BacklogStats()
        self._recover_armed = False
