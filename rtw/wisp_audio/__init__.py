"""rtw.wisp_audio — 复刻 Wisp（github.com/ppXD/Wisp）的 Windows 音频获取链路。

覆盖 Wisp 音频获取的完整过程：设备枚举 → 音频获取（麦克风 / WASAPI 环回 /
文件回放）→ 有界 drop-oldest 传输 → 公共 DSP（降混 + 抗混叠重采样 + ASR
归一化 + Dugan 混音）→ 喂给 VAD。

采集后端用 pyaudiowpatch（WASAPI 的 Python 绑定，Wisp 用 cpal 的 Python 等价
物）；非 Windows 下优雅降级（枚举返回空、采集源抛友好异常），真实 WASAPI 采集
在 Windows 真机激活。验证哲学同 Wisp：headless 跑单元/集成（replay 源 → 通道
→ DSP → VAD 端到端），真机跑硬件 E2E。

模块一览：
  frame      AudioFrame + AudioSource 抽象
  channel    FrameChannel（有界 drop-oldest 帧队列）
  devices    设备枚举（输入设备 + WASAPI 环回，走 pyaudiowpatch）
  mic        MicSource（WASAPI 麦克风采集）
  loopback   WasapiLoopbackSource（WASAPI 环回，系统音）
  replay     ReplaySource（文件回放注入源，供非 Windows 端到端验证）
  dsp        downmix_to_mono / resample_linear / Resampler（抗混叠）/ to_mono_16k
  normalize  normalize_for_asr（高通去直流 + 语音门控 RMS 归一化 + 峰值封顶）
  mixer      MeetingMixer（Dugan 增益共享自动混音）
  pipeline   VadFeeder（源 → 通道 → DSP → int16 PCM → VAD 的胶水层）
"""
from .frame import AudioFrame, AudioSource, AudioSourceInfo
from .channel import FrameChannel
from .devices import (
    list_input_devices, list_loopback_devices,
    default_input, default_output, find_loopback,
)
from .mic import MicSource
from .loopback import WasapiLoopbackSource
from .replay import ReplaySource
from .dsp import (
    TARGET_SAMPLE_RATE, FRAME_CHUNK_MS,
    downmix_to_mono, resample_linear, to_mono_16k, Resampler, chunk_into_frames,
)
from .normalize import (
    normalize_for_asr, normalize_for_asr_in_place,
    TARGET_RMS, PEAK_CEILING, MAX_GAIN,
)
from .mixer import MeetingMixer
from .pipeline import VadFeeder, to_int16_pcm

__all__ = [
    # frame
    "AudioFrame", "AudioSource", "AudioSourceInfo",
    # channel
    "FrameChannel",
    # devices
    "list_input_devices", "list_loopback_devices",
    "default_input", "default_output", "find_loopback",
    # sources
    "MicSource", "WasapiLoopbackSource", "ReplaySource",
    # dsp
    "TARGET_SAMPLE_RATE", "FRAME_CHUNK_MS",
    "downmix_to_mono", "resample_linear", "to_mono_16k",
    "Resampler", "chunk_into_frames",
    # normalize
    "normalize_for_asr", "normalize_for_asr_in_place",
    "TARGET_RMS", "PEAK_CEILING", "MAX_GAIN",
    # mixer
    "MeetingMixer",
    # pipeline
    "VadFeeder", "to_int16_pcm",
]
