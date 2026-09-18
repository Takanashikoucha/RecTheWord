"""轻量事件总线：线程安全的 publish/subscribe，管道各域间唯一通信方式之一。"""
from __future__ import annotations

import queue
import threading
from collections import defaultdict
from typing import Any, Callable


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Any], None]]] = defaultdict(list)
        self._lock = threading.Lock()
        self._q: queue.Queue[tuple[str, Any]] = queue.Queue()

    def subscribe(self, topic: str, fn: Callable[[Any], None]) -> None:
        with self._lock:
            self._subs[topic].append(fn)

    def publish(self, topic: str, payload: Any = None) -> None:
        self._q.put((topic, payload))

    def pump(self, timeout: float = 0.0) -> int:
        """处理积压事件，返回处理数量。UI 定时器周期调用。"""
        n = 0
        while True:
            try:
                topic, payload = self._q.get(timeout=timeout)
            except queue.Empty:
                return n
            with self._lock:
                fns = list(self._subs.get(topic, ()))
            for fn in fns:
                fn(payload)
            n += 1
