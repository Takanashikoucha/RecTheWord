"""Windows 系统音频采集（WASAPI 环回；复刻 Wisp crates/wisp-loopback/src/lib.rs）。

记录默认渲染端点（扬声器）正在播放的内容（即扬声器的「环回」），并作为
AudioSource 暴露，使 Windows 获得与 Mac 用 ScreenCaptureKit 相同的「一键捕获
会议」Live 体验——无需虚拟设备、无需配置。仅 Windows。

WASAPI 交还的 COM 接口非 Send，故（如同 macOS ScreenCaptureKit 源）它们完全
待在专门的采集线程里，向通道喂帧；公开句柄只持接收端 + 停止标志，保持 Send。

Python 侧用 pyaudiowpatch 的环回虚拟输入设备实现同等能力：环回设备是输出设备
的虚拟输入镜像，名字带 [Loopback] 后缀。采集线程打开该虚拟输入流，读到的帧
经 downmix + 抗混叠重采样到 16k 单声道后送进 FrameChannel。
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from .channel import FrameChannel
from .devices import _import_pa, find_loopback
from .dsp import Resampler, TARGET_SAMPLE_RATE
from .frame import AudioFrame, AudioSource, AudioSourceInfo

log = logging.getLogger(__name__)

#: 采集→管线帧通道的有界容量。溢出丢最旧帧，使消费者短暂停顿时采集保持实时；
#: 取宽裕余量（约 10-20 秒音频）。
FRAME_CHANNEL_CAPACITY = 1024

#: 采集线程醒来排空缓冲包的频率。远低于缓冲长度，环回音频不会溢出，同时保持
#: 低延迟。
POLL_S = 0.010

#: 启动超时：等待采集线程报告「已就绪」的最长时间。WASAPI 初始化（无输出设备、
#: COM 失败）应在短时间内完成；有界等待让调用方回退到仅麦克风，而非永远卡住。
STARTUP_TIMEOUT_S = 6.0


class WasapiLoopbackSource:
    """通过 WASAPI 环回采集系统音频的 AudioSource。"""

    def __init__(self, rx: FrameChannel, stop: threading.Event,
                 handle: threading.Thread | None = None) -> None:
        self._rx = rx
        self._stop = stop
        self._handle = handle

    @classmethod
    def new(cls, output_name: str | None = None) -> "WasapiLoopbackSource":
        """开始采集默认渲染端点的音频。WASAPI 无法初始化（无输出设备、COM
        失败）时报错，调用方可降级到仅麦克风。

        output_name=None → 默认输出环回；否则按名称匹配对应环回。
        """
        pa_mod = _import_pa()
        if pa_mod is None:
            raise RuntimeError("WASAPI 不可用（非 Windows 或未安装 pyaudiowpatch）")
        lb = find_loopback(output_name)
        if lb is None:
            raise RuntimeError("未找到可用的 WASAPI 环回设备")

        rx = FrameChannel(FRAME_CHANNEL_CAPACITY)
        stop = threading.Event()
        ready = threading.Event()
        err_box: list[str] = []

        def _capture_loop() -> None:
            pa = pa_mod.PyAudio()
            resampler = Resampler(TARGET_SAMPLE_RATE)
            try:
                in_sr = lb.get("defaultSampleRate", TARGET_SR_DEFAULT)
                stream = pa.open(format=pa.paFloat32, channels=1,
                                rate=in_sr, input=True,
                                input_device_index=lb["index"],
                                frames_per_buffer=512)
                ready.set()
                while not stop.is_set():
                    data = stream.read(512, exception_on_overflow=False)
                    arr = np.frombuffer(data, dtype=np.float32)
                    frame = AudioFrame(arr.tolist(), in_sr, 1, 0.0)
                    # 抗混叠重采样到 16k（环回常是 44.1/48k）
                    mono = resampler.process(frame)
                    rx.send(AudioFrame(mono, TARGET_SAMPLE_RATE, 1, 0.0))
                stream.stop_stream()
                stream.close()
            except Exception as e:  # noqa: BLE001
                if not ready.is_set():
                    err_box.append(f"{type(e).__name__}: {e}")
                else:
                    log.warning("WASAPI 环回采集错误（%s）；停止系统音频", e)
                rx.close()
            finally:
                try:
                    pa.terminate()
                except Exception:  # noqa: BLE001
                    pass

        handle = threading.Thread(target=_capture_loop, daemon=True,
                                  name="wisp:loopback")
        handle.start()
        t0 = time.monotonic()
        while not ready.is_set() and not err_box:
            if time.monotonic() - t0 > STARTUP_TIMEOUT_S:
                stop.set()
                raise RuntimeError(
                    f"WASAPI 环回未在 {STARTUP_TIMEOUT_S}s 内启动（采集守护可能卡死）")
            time.sleep(0.01)
        if err_box:
            stop.set()
            raise RuntimeError(err_box[0])
        return cls(rx, stop, handle)

    def info(self) -> AudioSourceInfo:
        return AudioSourceInfo("sys", "System audio")

    def next_frame(self) -> AudioFrame | None:
        return self._rx.recv()

    def stop(self) -> None:
        self._stop.set()
        if self._handle and self._handle.is_alive():
            self._handle.join(timeout=2)

    def __enter__(self) -> "WasapiLoopbackSource":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


# 环回设备未报告采样率时的兜底（WASAPI 共享混合格式常见 48k）。
TARGET_SR_DEFAULT = 48_000
