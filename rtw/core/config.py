"""配置加载：config.yaml（锚定仓库根）+ 默认值合并。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

APP_ROOT = Path(__file__).resolve().parent.parent.parent  # 仓库根（main.py 旁）


@dataclass
class AudioCfg:
    source: str = "wasapi"            # wasapi | replay（测试注入）
    mic_device: str | None = None    # None = 系统默认输入
    sys_device: str | None = None    # None = 默认输出环回
    sample_rate: int = 16000
    chunk_ms: int = 32
    replay_mic: str | None = None    # 回放音频文件（相对 APP_ROOT）
    replay_sys: str | None = None
    replay_speed: float = 1.0


@dataclass
class VadCfg:
    threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 300
    max_speech_ms: int = 8000        # 强制断句上限
    window_ms: int = 32


@dataclass
class AsrCfg:
    model: str = "Qwen/Qwen3-ASR-0.6B"
    device: str = "cpu"
    compute_type: str = "int8"
    threads: int = 8
    segment_policy: str = "balanced"  # aggressive(1.5s) | balanced(3s)
    max_new_tokens: int = 128


@dataclass
class ApiCfg:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    translate_timeout_s: float = 15.0
    minutes_timeout_s: float = 120.0


@dataclass
class UiCfg:
    overlay_theme: str = "glass"     # glass | solid | outline | light
    font_size: int = 28
    show_original: bool = True


@dataclass
class SessionCfg:
    dir: str = "~/.rectheword/sessions"


@dataclass
class Config:
    audio: AudioCfg = field(default_factory=AudioCfg)
    vad: VadCfg = field(default_factory=VadCfg)
    asr: AsrCfg = field(default_factory=AsrCfg)
    api: ApiCfg = field(default_factory=ApiCfg)
    ui: UiCfg = field(default_factory=UiCfg)
    session: SessionCfg = field(default_factory=SessionCfg)

    def models_dir(self) -> Path:
        return APP_ROOT / "models"


def _merge(dc_obj: Any, data: dict) -> None:
    for k, v in (data or {}).items():
        if hasattr(dc_obj, k):
            setattr(dc_obj, k, v)


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else APP_ROOT / "config.yaml"
    cfg = Config()
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        _merge(cfg.audio, raw.get("audio", {}))
        _merge(cfg.vad, raw.get("vad", {}))
        _merge(cfg.asr, raw.get("asr", {}))
        _merge(cfg.api, raw.get("api", {}))
        _merge(cfg.ui, raw.get("ui", {}))
        _merge(cfg.session, raw.get("session", {}))
    return cfg
