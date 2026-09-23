"""有界音频帧通道：满了丢弃最旧帧（复刻 Wisp wisp-core/src/channel.rs）。

采集回调实时产帧，但转写消费者可能短暂落后（引擎 pass 是同步的）。无界队列
会让积压无限增长——延迟与内存爬升直至流看起来卡住。本通道给积压封顶，溢出时
丢弃**最旧**帧，于是采集保持实时（丢陈旧音频而非卡住）。close() 唤醒阻塞的
接收方，使停止/出错的源干净结束而非挂死。

容量取宽裕余量（约 10-20 秒音频），使慢引擎转写一句时不会丢掉下一句的音频；
drop-oldest 仍给内存兜底。
"""
from __future__ import annotations

import collections
import threading

from .frame import AudioFrame


class FrameChannel:
    """有界帧通道：最多容纳 capacity 帧，溢出丢弃最旧帧。

    send 端可廉价克隆（多线程采集回调并发发送）；recv 端阻塞取帧。
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = max(1, capacity)
        self._lock = threading.Lock()
        self._avail = threading.Condition(self._lock)
        self._frames: collections.deque[AudioFrame] = collections.deque()
        self._closed = False

    def send(self, frame: AudioFrame) -> None:
        """推入一帧，缓冲区满则丢弃最旧帧。已 close 则为空操作。"""
        with self._avail:
            if self._closed:
                return
            while len(self._frames) >= self._capacity:
                self._frames.popleft()
            self._frames.append(frame)
            self._avail.notify_all()

    def close(self) -> None:
        """关闭通道并唤醒阻塞的接收方（其排空后返回 None）。"""
        with self._avail:
            self._closed = True
            self._avail.notify_all()

    def recv(self) -> AudioFrame | None:
        """阻塞直到有帧可取；通道关闭且排空后返回 None。"""
        with self._avail:
            while True:
                if self._frames:
                    return self._frames.popleft()
                if self._closed:
                    return None
                self._avail.wait()

    def try_recv(self) -> AudioFrame | None:
        """立即可取则返回一帧，否则 None；从不阻塞。

        与 recv() 不同，None 不区分「空」与「已关闭且排空」——只抽干当下缓冲，
        不等待。用于在不阻塞调用方的前提下拉取最新参考音频。
        """
        with self._avail:
            return self._frames.popleft() if self._frames else None

    @property
    def size(self) -> int:
        with self._avail:
            return len(self._frames)
