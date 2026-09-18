"""音频设备管理：枚举 / 刷新 / 运行时热切换（WASAPI 输入 + 环回）。

设计：
  - DeviceManager 封装 PyAudioWPatch（Windows WASAPI），暴露两类设备：
      mic  = 输入设备（麦克风）
      sys  = 输出设备的环回（loopback，捕获扬声器播放的声音）
  - refresh()：重新枚举（拔插耳机 / 切换输出后调用）。
  - WasapiSource：某一路的采集源，与 ReplaySource 同接口（start/stop/join +
    往 RingBuffer 写 PCM16），支持 switch(device_id) 热切换（自动重连）。
  - 非 Windows 环境（开发机 / 测试）：enumerate 返回空列表 + 友好提示，
    WasapiSource 不可用时回退 replay，不影响链路测试。

PCM 约定：16kHz mono int16，32ms chunk（512 样本 / 1024 字节），与 ReplaySource 一致。
"""
from __future__ import annotations

import logging
import platform
import threading
import time

import numpy as np

from .ring_buffer import RingBuffer

log = logging.getLogger(__name__)

TARGET_SR = 16000
CHUNK_MS = 32
CHUNK_SAMPLES = TARGET_SR * CHUNK_MS // 1000  # 512
IS_WINDOWS = platform.system() == "Windows"


class WasapiSource:
    """单路 WASAPI 采集源（输入或环回），与 ReplaySource 同接口。

    热切换：switch(new_index) 停止当前流、用新设备重启，RingBuffer 不清空
    （下游 VAD 连续）。设备失效（拔线）→ 自动重连循环（指数退避，封顶 5s）。
    """

    def __init__(self, ring: RingBuffer, kind: str, device_index: int | None = None,
                 device_manager: "DeviceManager | None" = None) -> None:
        self.ring = ring
        self.kind = kind  # "mic" | "sys"
        self.device_index = device_index
        self.dm = device_manager
        self.done = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.active = False
        self.last_error: str = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"wasapi:{self.kind}")
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def stop(self) -> None:
        self._stop.set()

    def switch(self, new_index: int | None) -> None:
        """运行时热切换设备：重置停止标志、换设备、重启采集线程。"""
        self.device_index = new_index
        self._stop.clear()
        self.done.clear()
        if self._thread and self._thread.is_alive():
            # 让旧线程自然退出（它检测到 _stop 会重读 device_index）
            pass
        else:
            self.start()

    def _find_loopback(self, pa) -> dict | None:
        """按官方 API 找输出设备对应的环回虚拟输入设备。

        PyAudioWPatch 把环回设备作为独立虚拟输入设备（名字带 [Loopback] 后缀）。
        默认输出 → get_default_wasapi_loopback()；指定输出索引 → 按名称匹配。
        """
        if self.device_index is None:
            try:
                lb = pa.get_default_wasapi_loopback()
                if lb:
                    return lb
            except Exception:
                pass
            try:
                wasapi = pa.get_host_api_info_by_type(pa.paWASAPI)
                out_name = pa.get_device_info_by_index(
                    wasapi["defaultOutputDevice"])["name"]
            except Exception:
                return None
            for lb in pa.get_loopback_device_info_generator():
                if out_name in lb.get("name", ""):
                    return lb
            return None
        try:
            out_name = pa.get_device_info_by_index(self.device_index)["name"]
        except Exception:
            return None
        for lb in pa.get_loopback_device_info_generator():
            if out_name in lb.get("name", ""):
                return lb
        return None

    def _open_stream(self):
        """打开一路 WASAPI 流（mic=输入 / sys=官方环回虚拟设备）。"""
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        if self.kind == "mic":
            stream = pa.open(format=pyaudio.paInt16, channels=1,
                             rate=TARGET_SR, input=True,
                             input_device_index=self.device_index,
                             frames_per_buffer=CHUNK_SAMPLES)
        else:  # sys = 输出设备环回（独立虚拟输入设备）
            lb = self._find_loopback(pa)
            if lb is None:
                raise RuntimeError("未找到可用的 WASAPI 环回设备")
            stream = pa.open(format=pyaudio.paInt16, channels=1,
                             rate=lb.get("defaultSampleRate", TARGET_SR),
                             input=True,
                             input_device_index=lb["index"],
                             frames_per_buffer=CHUNK_SAMPLES)
        return pa, stream

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            pa = None
            stream = None
            try:
                pa, stream = self._open_stream()
                self.active = True
                self.last_error = ""
                backoff = 0.5
                while not self._stop.is_set():
                    data = stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
                    self.ring.write(data)
            except Exception as e:  # 拔线 / 设备忙 / 切换
                self.active = False
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("wasapi[%s] 断开：%s，%.1fs 后重连", self.kind, self.last_error, backoff)
            finally:
                if stream:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                if pa:
                    try:
                        pa.terminate()
                    except Exception:
                        pass
            if self._stop.is_set():
                return
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)
        self.done.set()


class DeviceManager:
    """设备枚举 + 刷新。非 Windows 返回空列表（UI 显示提示，链路走 replay）。"""

    def __init__(self) -> None:
        self._pa = None
        self._lock = threading.Lock()

    def _ensure_pa(self):
        if self._pa is None and IS_WINDOWS:
            import pyaudiowpatch as pyaudio
            self._pa = pyaudio.PyAudio()
        return self._pa

    def enumerate(self) -> dict[str, list[dict]]:
        """返回 {'mic': [{index,name}], 'sys': [{index,name}]}。非 Windows → 空。"""
        if not IS_WINDOWS:
            return {"mic": [], "sys": []}
        pa = self._ensure_pa()
        if pa is None:
            return {"mic": [], "sys": []}
        with self._lock:
            mic, sys_dev = [], []
            try:
                for i in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(i)
                    name = info.get("name", f"device {i}")
                    if info.get("maxInputChannels", 0) > 0 and \
                            not info.get("isLoopbackDevice", False):
                        mic.append({"index": i, "name": name})
            except Exception as e:
                log.warning("设备枚举失败：%s", e)
            # sys = 环回虚拟输入设备（官方 API，名字带 [Loopback]）
            try:
                for lb in pa.get_loopback_device_info_generator():
                    sys_dev.append({"index": lb["index"],
                                   "name": lb.get("name", "")})
            except Exception as e:
                log.warning("环回设备枚举失败：%s", e)
            return {"mic": mic, "sys": sys_dev}

    def refresh(self) -> dict[str, list[dict]]:
        """重新枚举（释放并重建 PyAudio，确保拿到最新设备拓扑）。"""
        with self._lock:
            if self._pa is not None:
                try:
                    self._pa.terminate()
                except Exception:
                    pass
                self._pa = None
        return self.enumerate()

    def default(self, kind: str) -> int | None:
        """默认设备索引（None = 系统默认）。"""
        if not IS_WINDOWS:
            return None
        pa = self._ensure_pa()
        if pa is None:
            return None
        try:
            if kind == "mic":
                return pa.get_default_input_device_info()["index"]
            return pa.get_default_output_device_info()["index"]
        except Exception:
            return None
