"""麦克风采集（WASAPI，走 pyaudiowpatch；复刻 Wisp wisp-audio/src/mic.rs 的架构）。

Wisp 用 cpal 采集默认输入设备，因其 Stream 非 Send，由专门采集线程持有并向
通道喂帧；MicSource 只持接收端 + 停止标志，从而可安全移到管线线程。本模块用
pyaudiowpatch（WASAPI 的 Python 绑定）实现同样的「专线程采集 → FrameChannel」
架构：
  - 采集线程打开 WASAPI 输入流（设备原生速率/通道），读到的帧经 downmix +
    抗混叠重采样到 16k 单声道后送进 FrameChannel。
  - MicSource 只持 FrameChannel 接收端 + 停止事件，next_frame() 阻塞取帧。
  - 帧以设备原生速率/通道发出，转 16k 单声道在下游（本模块内已做，便于直接
    喂 VAD）。

非 Windows / pyaudiowpatch 不可用：from_default()/from_device() 抛友好异常，
调用方可降级到 replay 注入源。
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from .channel import FrameChannel
from .devices import _import_pa, find_loopback
from .dsp import Resampler, TARGET_SAMPLE_RATE
from .frame import AudioFrame, AudioSource, AudioSourceInfo

log = logging.getLogger(__name__)

#: 采集→管线帧通道的有界容量。溢出丢最旧帧，使消费者短暂停顿时采集保持实时；
#: 取宽裕余量（约 10-20 秒音频），慢引擎转写一句时不会丢掉下一句的音频。
FRAME_CHANNEL_CAPACITY = 1024


class MicSource:
    """通过 WASAPI 采集系统默认输入设备的 AudioSource。"""

    def __init__(self, rx: FrameChannel, stop: threading.Event,
                 info: AudioSourceInfo,
                 handle: threading.Thread | None = None) -> None:
        self._rx = rx
        self._stop = stop
        self._handle = handle
        self.info = info

    @classmethod
    def from_default(cls) -> "MicSource":
        """打开系统默认输入设备并开始采集。无默认输入设备则抛异常。"""
        pa_mod = _import_pa()
        if pa_mod is None:
            raise RuntimeError("WASAPI 不可用（非 Windows 或未安装 pyaudiowpatch）")
        p = pa_mod.PyAudio()
        try:
            dev_idx = p.get_default_input_device_info()["index"]
        except Exception as e:  # noqa: BLE001
            p.terminate()
            raise RuntimeError(f"无默认输入设备：{e}") from e
        return cls._from_device_index(p, pa_mod, dev_idx)

    @classmethod
    def from_device(cls, name: str) -> "MicSource":
        """按名称打开指定输入设备并开始采集。

        用于承载系统/会议音频的环回/虚拟设备（如 BlackHole）。
        """
        pa_mod = _import_pa()
        if pa_mod is None:
            raise RuntimeError("WASAPI 不可用（非 Windows 或未安装 pyaudiowpatch）")
        p = pa_mod.PyAudio()
        try:
            host = p.get_host_api_info_by_type(p.paWASAPI)
            dev_idx = None
            for i in range(host["deviceCount"]):
                info = p.get_device_info_by_host_api_device_index(host["index"], i)
                if info.get("maxInputChannels", 0) > 0 and \
                        info.get("name") == name:
                    dev_idx = info["index"]
                    break
            if dev_idx is None:
                raise RuntimeError(f"输入设备 '{name}' 未找到")
            return cls._from_device_index(p, pa_mod, dev_idx)
        finally:
            # 设备索引已记下，PyAudio 句柄交给采集线程重建（避免跨线程共享）
            p.terminate()

    @classmethod
    def _from_device_index(cls, p, pa_mod, dev_idx: int) -> "MicSource":
        """已拿到 PyAudio 句柄与设备索引 → 起采集线程并等待就绪。"""
        dev_name = p.get_device_info_by_index(dev_idx).get("name", "input")
        p.terminate()  # 采集线程自行重建 PyAudio（COM 对象不跨线程）

        rx = FrameChannel(FRAME_CHANNEL_CAPACITY)
        stop = threading.Event()
        ready = threading.Event()
        err_box: list[str] = []

        def _capture_loop() -> None:
            pa = pa_mod.PyAudio()
            resampler = Resampler(TARGET_SAMPLE_RATE)
            try:
                stream = pa.open(format=pa.paFloat32, channels=1,
                                rate=TARGET_SAMPLE_RATE, input=True,
                                input_device_index=dev_idx,
                                frames_per_buffer=512)
                ready.set()
                while not stop.is_set():
                    data = stream.read(512, exception_on_overflow=False)
                    arr = np.frombuffer(data, dtype=np.float32)
                    frame = AudioFrame(arr.tolist(), TARGET_SAMPLE_RATE, 1, 0.0)
                    rx.send(frame)
                stream.stop_stream()
                stream.close()
            except Exception as e:  # noqa: BLE001
                if not ready.is_set():
                    err_box.append(f"{type(e).__name__}: {e}")
                else:
                    log.warning("麦克风流错误，关闭源：%s", e)
                # 关闭通道，唤醒阻塞的消费者，使其干净结束
                rx.close()
            finally:
                try:
                    pa.terminate()
                except Exception:  # noqa: BLE001
                    pass

        handle = threading.Thread(target=_capture_loop, daemon=True,
                                  name="wisp:mic")
        handle.start()
        # 等待就绪（有界，避免卡死）
        import time
        t0 = time.monotonic()
        while not ready.is_set() and not err_box:
            if time.monotonic() - t0 > 6.0:
                stop.set()
                raise RuntimeError("麦克风采集线程未在时限内就绪")
            time.sleep(0.01)
        if err_box:
            stop.set()
            raise RuntimeError(err_box[0])
        return cls(rx, stop, AudioSourceInfo("mic", dev_name), handle)

    def info(self) -> AudioSourceInfo:
        return self.info

    def next_frame(self) -> AudioFrame | None:
        return self._rx.recv()

    def stop(self) -> None:
        self._stop.set()
        if self._handle and self._handle.is_alive():
            self._handle.join(timeout=2)

    def __enter__(self) -> "MicSource":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
