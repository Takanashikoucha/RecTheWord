"""设备枚举：输入设备（麦克风）+ WASAPI 环回虚拟设备（复刻 Wisp 的设备枚举）。

Wisp 用 cpal 的 `list_input_devices()` 列输入设备、用 WASAPI COM 找默认渲染端点
的环回。Python 侧用 pyaudiowpatch（WASAPI 的 Python 绑定，pyaudio 的 Windows 补丁
版）实现同等能力：
  - list_input_devices()：列所有输入设备（麦克风、环回/虚拟设备……）。
  - list_loopback_devices()：列 WASAPI 环回虚拟输入设备（名字带 [Loopback] 后缀）。
  - default_input() / default_output()：系统默认输入/输出设备名。

非 Windows 环境（开发机 / 测试）：全部返回空列表 / None，并给出友好提示，
采集源随之优雅降级（链路走 replay 注入源，见 replay.py）。
"""
from __future__ import annotations

import logging
import platform

log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"


def _import_pa():
    """导入 pyaudiowpatch；非 Windows 或未安装返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        import pyaudiowpatch as pyaudio  # type: ignore
        return pyaudio
    except Exception as e:  # noqa: BLE001
        log.warning("pyaudiowpatch 不可用：%s", e)
        return None


def list_input_devices() -> list[str]:
    """列出可用输入设备（麦克风、环回/虚拟设备……）的名称。

    非 Windows / pyaudiowpatch 不可用 → 空列表。
    """
    pa = _import_pa()
    if pa is None:
        return []
    names: list[str] = []
    p = pa.PyAudio()
    try:
        host = p.get_host_api_info_by_type(p.paWASAPI)
        for i in range(host["deviceCount"]):
            info = p.get_device_info_by_host_api_device_index(host["index"], i)
            if info.get("maxInputChannels", 0) > 0:
                names.append(info.get("name", f"device {i}"))
    except Exception as e:  # noqa: BLE001
        log.warning("输入设备枚举失败：%s", e)
    finally:
        p.terminate()
    return names


def list_loopback_devices() -> list[str]:
    """列出 WASAPI 环回虚拟输入设备（名字带 [Loopback] 后缀）。

    环回设备是输出设备的虚拟输入镜像，用来捕获本机播放的系统音频。
    非 Windows / pyaudiowpatch 不可用 → 空列表。
    """
    pa = _import_pa()
    if pa is None:
        return []
    names: list[str] = []
    p = pa.PyAudio()
    try:
        for lb in p.get_loopback_device_info_generator():
            names.append(lb.get("name", ""))
    except Exception as e:  # noqa: BLE001
        log.warning("环回设备枚举失败：%s", e)
    finally:
        p.terminate()
    return names


def default_input() -> str | None:
    """系统默认输入设备名（无则 None）。非 Windows → None。"""
    pa = _import_pa()
    if pa is None:
        return None
    p = pa.PyAudio()
    try:
        return p.get_default_input_device_info().get("name")
    except Exception:  # noqa: BLE001
        return None
    finally:
        p.terminate()


def default_output() -> str | None:
    """系统默认输出设备名（无则 None）。非 Windows → None。"""
    pa = _import_pa()
    if pa is None:
        return None
    p = pa.PyAudio()
    try:
        return p.get_default_output_device_info().get("name")
    except Exception:  # noqa: BLE001
        return None
    finally:
        p.terminate()


def find_loopback(output_name: str | None) -> dict | None:
    """按输出设备名找对应的环回虚拟输入设备（Wasp 官方 API）。

    output_name=None → 默认输出环回（get_default_wasapi_loopback）；否则按名称
    匹配。找不到返回 None。
    """
    pa = _import_pa()
    if pa is None:
        return None
    p = pa.PyAudio()
    try:
        if output_name is None:
            try:
                lb = p.get_default_wasapi_loopback()
                if lb:
                    return lb
            except Exception:  # noqa: BLE001
                pass
            # 回退：默认输出名匹配
            try:
                wasapi = p.get_host_api_info_by_type(p.paWASAPI)
                out_name = p.get_device_info_by_index(
                    wasapi["defaultOutputDevice"])["name"]
            except Exception:  # noqa: BLE001
                return None
        else:
            out_name = output_name
        for lb in p.get_loopback_device_info_generator():
            if out_name in lb.get("name", ""):
                return lb
        return None
    finally:
        p.terminate()
