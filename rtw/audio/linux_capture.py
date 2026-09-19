"""Linux 音频采集后端：PipeWire / PulseAudio（monitor 环回）+ PortAudio（ALSA 麦克风）。

与 Windows 侧 ``devices.py``（WASAPI + 环回）对称，提供两类设备：
  mic = 输入设备（麦克风）—— 走 PortAudio(ALSA) 直接采集
  sys = 输出设备的环回（loopback）—— 走 PulseAudio/PipeWire 的 sink monitor source

设计要点（与 WasapiSource 同接口，便于 orchestrator 无缝替换）：
  - LinuxCapture：单路采集源，start()/stop()/join()/switch(device_id) 热切换，
    往 RingBuffer 写 PCM16 16kHz mono，chunk 512 样本 / 1024 字节。
    设备失效（拔线 / 服务重启）→ 指数退避自动重连（封顶 5s）。
  - LinuxDeviceManager：枚举 mic（PortAudio 输入设备）与 sys（sink monitor）。
  - 采集实现用子进程（pacat / pw-cat / arecord）而非纯 Python 绑定：
    避免引入 portaudio 原生扩展的构建负担，且与系统音频栈解耦、稳健。
    采样率统一重采样到 16kHz（scipy.signal.resample_poly），与 ReplaySource 一致。

PCM 约定：16kHz mono int16，32ms chunk（512 样本 / 1024 字节），与 ReplaySource 一致。

非 Linux 环境：probe() 返回不可用，enumerate 返回空列表（UI 显示提示，链路走 replay）。
"""
from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
import threading
import time

import numpy as np

try:
    from scipy.signal import resample_poly
except Exception:  # pragma: no cover - scipy 一定在（requirements）
    resample_poly = None

from .ring_buffer import RingBuffer

log = logging.getLogger(__name__)

TARGET_SR = 16000
CHUNK_MS = 32
CHUNK_SAMPLES = TARGET_SR * CHUNK_MS // 1000  # 512
IS_LINUX = platform.system() == "Linux"


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


_NODE_NAME_RE = re.compile(r'node\.name\s*=\s*"([^"]*)"')


def re_node_name(line: str) -> str | None:
    """从 pw-cli ls 的一行提取 node.name（无则返回 None）。"""
    m = _NODE_NAME_RE.search(line)
    return m.group(1) if m else None


def probe_backend() -> str:
    """探测可用的音频后端：'pipewire' | 'pulse' | 'alsa' | ''。

    优先级：PipeWire（现代发行版默认，Pulse 为其兼容层）> PulseAudio > 纯 ALSA。
    只要 pacat 或 pw-cat 任一存在即认为有 monitor 能力（二者协议同源）。
    """
    if _have("pw-cat") or _have("pw-record"):
        return "pipewire"
    if _have("pacat"):
        return "pulse"
    if _have("arecord"):
        return "alsa"
    return ""


def _resample_to_target(x: np.ndarray, sr: int) -> np.ndarray:
    """把任意采样率的 float32 单声道重采样到 16kHz（整数因子 poly 重采样）。"""
    if sr == TARGET_SR:
        return x
    g = np.gcd(int(sr), TARGET_SR)
    up, down = TARGET_SR // g, int(sr) // g
    if resample_poly is None:  # 兜底：线性插值
        n = int(len(x) * TARGET_SR / sr)
        xp = np.linspace(0.0, len(x) - 1, n)
        return np.interp(xp, np.arange(len(x)), x).astype(np.float32)
    return resample_poly(x, up, down).astype(np.float32)


class LinuxCapture:
    """单路 Linux 采集源（mic=PortAudio/ALSA 输入 / sys=sink monitor 环回），与 WasapiSource 同接口。

    热切换：switch(new_id) 停止当前流、用新设备重启，RingBuffer 不清空（下游 VAD 连续）。
    设备失效 → 自动重连循环（指数退避，封顶 5s）。
    """

    def __init__(self, ring: RingBuffer, kind: str, device_id: int | None = None,
                 device_manager: "LinuxDeviceManager | None" = None) -> None:
        self.ring = ring
        self.kind = kind  # "mic" | "sys"
        self.device_id = device_id
        self.dm = device_manager
        self.done = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.active = False
        self.last_error: str = ""

    # ---- 对外接口（与 WasapiSource 对齐）----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"linuxcap:{self.kind}")
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def stop(self) -> None:
        self._stop.set()

    def switch(self, new_id: int | None) -> None:
        """运行时热切换设备：重置停止标志、换设备、重启采集线程。"""
        self.device_id = new_id
        self._stop.clear()
        self.done.clear()
        if self._thread and self._thread.is_alive():
            pass  # 旧线程检测到 _stop 会重读 device_id 重连
        else:
            self.start()

    # ---- 采集实现 ----
    def _device_label(self) -> str | None:
        """把 device_id 解析成具体设备名（None = 系统默认）。"""
        if self.device_id is None or self.dm is None:
            return None
        lst = self.dm.enumerate().get(self.kind, [])
        for d in lst:
            if d["index"] == self.device_id:
                return d["name"]
        return None

    def _open_stream(self):
        """打开一路采集子进程，返回 (proc, in_sr)。失败抛异常。"""
        backend = probe_backend()
        if self.kind == "mic":
            return self._open_mic(backend)
        return self._open_sys(backend)

    def _open_mic(self, backend: str):
        """mic：走 arecord（ALSA 直接采集，最稳健，与音频栈解耦）。

        请求 16kHz / S16_LE / raw；指定设备时用 -D（ALSA 设备名，如 plughw:0,0）。
        设备不支持 16k 时 arecord 会报错 → 上层 _run 捕获后退避重连。
        """
        if not _have("arecord"):
            raise RuntimeError("arecord 不可用（未安装 alsa-utils）")
        label = self._device_label()
        cmd = ["arecord", "-f", "S16_LE", "-e", "signed", "-t", "raw",
               "-r", str(TARGET_SR), "-d", "0"]
        if label:
            cmd += ["-D", label]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return proc, TARGET_SR

    def _open_sys(self, backend: str):
        """sys：监听默认（或指定）sink 的 monitor source，捕获本机播放的声音。

        monitor source 名形如 "<sink>.monitor"；无指定设备时用 @DEFAULT_MONITOR@。
        优先 pacat（Pulse/PipeWire 兼容层，flag 最稳）；pw-cat 需 -r 记录模式 + s16 格式。
        """
        label = self._device_label()
        mon = f"{label}.monitor" if label else "@DEFAULT_MONITOR@"
        if _have("pacat"):
            cmd = ["pacat", "-r", "-d", mon, "--rate", str(TARGET_SR),
                   "--format", "s16le", "--channels", "1", "--raw"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            return proc, TARGET_SR
        if _have("pw-cat"):
            cmd = ["pw-cat", "-r", "--rate", str(TARGET_SR), "--format", "s16",
                   "--channels", "1", mon]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            return proc, TARGET_SR
        raise RuntimeError(f"无可用 monitor 后端（backend={backend}）")

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            proc = None
            in_sr = TARGET_SR
            try:
                proc, in_sr = self._open_stream()
                self.active = True
                self.last_error = ""
                backoff = 0.5
                # 持续读 PCM 帧，重采样到 16k 后写入 RingBuffer
                acc = bytearray()
                while not self._stop.is_set():
                    raw = proc.stdout.read(CHUNK_SAMPLES * 2)
                    if not raw:
                        raise RuntimeError("采集流 EOF（设备断开或服务重启）")
                    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    x = _resample_to_target(x, in_sr)
                    pcm16 = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                    self.ring.write(pcm16)
            except Exception as e:  # 拔线 / 服务重启 / 切换
                self.active = False
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("linuxcap[%s] 断开：%s，%.1fs 后重连", self.kind, self.last_error, backoff)
            finally:
                if proc:
                    try:
                        proc.terminate()
                        proc.wait(timeout=2)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            if self._stop.is_set():
                return
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)
        self.done.set()


class LinuxDeviceManager:
    """设备枚举 + 刷新。非 Linux 返回空列表（UI 显示提示，链路走 replay）。"""

    def __init__(self) -> None:
        self.backend = probe_backend() if IS_LINUX else ""

    def enumerate(self) -> dict[str, list[dict]]:
        """返回 {'mic': [{index,name}], 'sys': [{index,name}]}。非 Linux / 无后端 → 空。"""
        if not IS_LINUX or not self.backend:
            return {"mic": [], "sys": []}
        mic = self._list_mics()
        sinks = self._list_sinks()
        sys_dev = [{"index": i, "name": s} for i, s in enumerate(sinks)]
        return {"mic": mic, "sys": sys_dev}

    def _list_mics(self) -> list[dict]:
        """列出可用麦克风（输入）设备。

        优先 pactl list short source（Pulse/PipeWire 兼容层，最可靠）；
        无 pactl 时回退 arecord -l（纯 ALSA）。
        """
        if _have("pactl"):
            try:
                out = subprocess.check_output(["pactl", "list", "short", "source"],
                                            stderr=subprocess.DEVNULL, timeout=5).decode()
                mics = []
                for line in out.splitlines():
                    parts = line.split()
                    # 形如: <idx>\t<source-name>\tPipeWire\t<s16le 2ch 48000Hz>\tRUNNING
                    if len(parts) >= 2:
                        mics.append({"index": int(parts[0]), "name": parts[1]})
                return mics
            except subprocess.CalledProcessError:
                return []  # 无源（无麦克风）不算错误
            except Exception as e:
                log.warning("pactl 麦克风枚举失败：%s", e)
        if _have("arecord"):
            try:
                out = subprocess.check_output(["arecord", "-l"],
                                            stderr=subprocess.DEVNULL, timeout=5).decode()
                mics = []
                cur_card = None
                for line in out.splitlines():
                    s = line.strip()
                    if s.startswith("card #"):
                        cur_card = s.split()[1]
                    elif s.startswith("card ") and cur_card is not None:
                        # 形如 "card 0 - PCH [HDA Intel PCH]"
                        desc = s.split("-", 1)[1].strip() if "-" in s else f"card {cur_card}"
                        mics.append({"index": int(cur_card), "name": desc})
                        cur_card = None
                return mics
            except Exception as e:
                log.warning("arecord 麦克风枚举失败：%s", e)
        return []

    def _list_sinks(self) -> list[str]:
        """列出可用 sink（扬声器）名，monitor 名即 <sink>.monitor。"""
        if _have("pactl"):
            try:
                out = subprocess.check_output(["pactl", "list", "short", "sinks"],
                                            stderr=subprocess.DEVNULL, timeout=5).decode()
                names = []
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        names.append(parts[1])
                return names
            except Exception:
                pass
        if _have("pw-cli"):
            try:
                out = subprocess.check_output(["pw-cli", "ls"],
                                            stderr=subprocess.DEVNULL, timeout=5).decode()
                # 输出型节点：node.name 后跟 port.output=true（且非 monitor）
                names, cur = [], None
                for line in out.splitlines():
                    s = line.strip()
                    nm = re_node_name(s)
                    if nm:
                        cur = nm
                    elif s.startswith("port.output = \"true\"") and cur:
                        names.append(cur)
                        cur = None
                return names
            except Exception:
                pass
        return []

    def refresh(self) -> dict[str, list[dict]]:
        """重新枚举（Linux 下每次直接重跑命令即可）。"""
        return self.enumerate()

    def default(self, kind: str) -> int | None:
        """默认设备索引（None = 系统默认）。"""
        if not IS_LINUX or not self.backend:
            return None
        return None  # 默认走系统默认输入 / 默认 sink monitor
