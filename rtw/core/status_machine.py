"""等待态状态机：任何异步操作的显式用户告知（方案 §2-6 的贯穿性机制）。

状态：IDLE → WORKING(文案+进度/ETA) → DONE / ERROR
所有订阅者（UI toast、状态栏、splash 进度条）通过 EventBus 的 "status" 事件接收。
"""
from __future__ import annotations

import enum
import itertools
import threading
import time
from dataclasses import dataclass, field

from .events import EventBus


class Phase(enum.Enum):
    IDLE = "idle"
    WORKING = "working"
    DONE = "done"
    ERROR = "error"


@dataclass
class StatusState:
    op: str
    phase: Phase = Phase.IDLE
    message: str = ""
    progress: float | None = None      # 0.0-1.0，None = 不定进度
    eta_s: float | None = None
    detail: str = ""
    ts: float = field(default_factory=time.monotonic)


class StatusMachine:
    """每个异步操作一个实例；begin/update/finish 自动广播。"""

    def __init__(self, bus: EventBus, op: str) -> None:
        self.bus = bus
        self.op = op
        self.state = StatusState(op)
        self._lock = threading.Lock()
        self._count = itertools.count()

    def begin(self, message: str, total: float | None = None) -> None:
        with self._lock:
            self.state = StatusState(self.op, Phase.WORKING, message,
                                    0.0 if total else None, None, "", time.monotonic())
            self._total = total
        self._emit()

    def update(self, done: float | None = None, message: str | None = None,
               eta_s: float | None = None) -> None:
        with self._lock:
            if message:
                self.state.message = message
            if done is not None and getattr(self, "_total", None):
                self.state.progress = min(1.0, done / self._total)
            elif done is not None:
                self.state.progress = min(1.0, done)
            if eta_s is not None:
                self.state.eta_s = eta_s
            self.state.ts = time.monotonic()
        self._emit()

    def finish(self, message: str = "完成") -> None:
        with self._lock:
            self.state.phase = Phase.DONE
            self.state.message = message
            self.state.progress = 1.0
            self.state.ts = time.monotonic()
        self._emit()

    def error(self, message: str) -> None:
        with self._lock:
            self.state.phase = Phase.ERROR
            self.state.message = message
            self.state.detail = ""
            self.state.ts = time.monotonic()
        self._emit()

    def _emit(self) -> None:
        seq = next(self._count)
        self.bus.publish("status", (self.op, seq, self.state))
