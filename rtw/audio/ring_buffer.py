"""零拷贝音频环形缓冲：采集线程写、消费线程读，永不互相阻塞。

head/tail/count 三元组实现（无歧义判满）：
- _tail：写指针；_head：读指针；_count：已存字节数
- 写满时覆盖最旧数据（实时优先）
"""
from __future__ import annotations

import threading


class RingBuffer:
    def __init__(self, capacity_samples: int) -> None:
        self.capacity = capacity_samples
        self._buf = bytearray(capacity_samples)
        self._head = 0
        self._tail = 0
        self._count = 0
        self._cond = threading.Condition()

    def write(self, pcm: bytes) -> None:
        n = len(pcm)
        with self._cond:
            if n >= self.capacity:
                self._buf[:] = pcm[-self.capacity:]
                self._head = 0
                self._tail = self.capacity
                self._count = self.capacity
            else:
                for i in range(n):
                    self._buf[self._tail] = pcm[i]
                    self._tail = (self._tail + 1) % self.capacity
                    if self._count < self.capacity:
                        self._count += 1
                    else:
                        # 已满：覆盖最旧，读指针前移
                        self._head = (self._head + 1) % self.capacity
            self._cond.notify_all()

    def drain(self) -> bytes:
        """取出全部可读数据并清空。"""
        with self._cond:
            if self._count == 0:
                return b""
            if self._head + self._count <= self.capacity:
                data = bytes(self._buf[self._head:self._head + self._count])
            else:
                data = (bytes(self._buf[self._head:]) +
                       bytes(self._buf[:self._head + self._count - self.capacity]))
            self._head = self._tail
            self._count = 0
            return data

    def wait_some(self, timeout: float = 0.1) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._count > 0, timeout)
