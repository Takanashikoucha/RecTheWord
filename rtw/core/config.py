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
    source: str = ""                 # 缺省=平台自适应（Windows→wasapi / Linux→alsa）；
                                    # 也可显式 wasapi | alsa | replay（测试注入）
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
    subtitle_theme: str = "light"    # 主窗外观：light（暖白纸感，v3 默认）| dark（旧深色）
    font_size: int = 28
    show_original: bool = True
    target_lang: str = "zh"          # 翻译目标语言（修复：原先 getattr fallback 恒为 zh）
    source_lang: str = ""            # 源语言；空 = 自动检测（信任 ASR 每段返回的 language）


@dataclass
class SessionCfg:
    dir: str = "~/.rectheword/sessions"


@dataclass
class BacklogCfg:
    """ASR 积压自适应恢复（背压 + drop-oldest + 状态机）。

    当 ASR 处理跟不上 VAD 产段速度时，seg_q 会堆积 → 延迟无限增长。
    这里用高低水位做背压：超过高水位就丢弃最旧的段（保最新实时段），
    回落到低水位以下视为恢复。maxsize 是有界兜底，防内存爆炸。
    """
    enabled: bool = True
    high_watermark: int = 8      # 队列深度超过此值 → 进入 backlogged（L1 减量）
    low_watermark: int = 3       # 队列深度回落到此值以下 → 恢复 normal
    maxsize: int = 16            # seg_q 硬上限（兜底，绝不超过）
    recover_grace_s: float = 2.0  # 维持低压多久才宣告 recovered（防抖）
    # L1 源头减量：积压时动态调 VAD，少产段、少喂 ASR（首选，不丢数据）
    boost_threshold: float = 0.62    # 减压时的 VAD 阈值（高于默认 0.5 → 更不敏感）
    boost_min_silence_ms: int = 500  # 减压时的最短静音门限（长于默认 300 → 晚断句）
    # L2 drop-oldest 兜底：L1 减量后仍超此深度才开始丢最旧段
    drop_watermark: int = 12


@dataclass
class Config:
    audio: AudioCfg = field(default_factory=AudioCfg)
    vad: VadCfg = field(default_factory=VadCfg)
    asr: AsrCfg = field(default_factory=AsrCfg)
    api: ApiCfg = field(default_factory=ApiCfg)
    ui: UiCfg = field(default_factory=UiCfg)
    session: SessionCfg = field(default_factory=SessionCfg)
    backlog: BacklogCfg = field(default_factory=BacklogCfg)

    def models_dir(self) -> Path:
        return APP_ROOT / "models"


def _merge(dc_obj: Any, data: dict) -> None:
    for k, v in (data or {}).items():
        if hasattr(dc_obj, k):
            setattr(dc_obj, k, v)


def load_config(path: str | Path | None = None,
                user_overrides: dict | None = None) -> Config:
    """加载配置：defaults(config.yaml) ← user(settings.yaml 覆盖层)。

    user_overrides 的结构与 config.yaml 同形（顶层键为 section 名），
    其中的值优先于 config.yaml（用户级持久化设置）。
    """
    p = Path(path) if path else APP_ROOT / "config.yaml"
    cfg = Config()
    merged: dict = {}
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        merged.update(raw)
    if user_overrides:
        # 浅合并：section 级覆盖（用户值优先）
        for sec, val in user_overrides.items():
            if isinstance(val, dict):
                merged.setdefault(sec, {})
                if isinstance(merged[sec], dict):
                    merged[sec].update(val)
                else:
                    merged[sec] = val
            else:
                merged[sec] = val
    _merge(cfg.audio, merged.get("audio", {}))
    _merge(cfg.vad, merged.get("vad", {}))
    _merge(cfg.asr, merged.get("asr", {}))
    _merge(cfg.api, merged.get("api", {}))
    _merge(cfg.ui, merged.get("ui", {}))
    _merge(cfg.session, merged.get("session", {}))
    _merge(cfg.backlog, merged.get("backlog", {}))
    return cfg


# ---- 用户级持久化（~/.rectheword/settings.yaml，不污染仓库 config.yaml）----

USER_SETTINGS_DIR = Path("~/.rectheword").expanduser()
USER_SETTINGS_FILE = USER_SETTINGS_DIR / "settings.yaml"

# 面板可持久化的 section（只 dump 这些，避免把模型路径等敏感/绝对路径写出去）
_PERSIST_SECTIONS = ("ui", "api", "vad")


def _settings_path() -> Path:
    return USER_SETTINGS_FILE


def load_user_settings() -> dict:
    """读取用户级设置（不存在/损坏 → 空 dict，不影响启动）。"""
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_user_settings(cfg: Config) -> Path:
    """把面板涉及的 section 持久化到用户级 settings.yaml。

    只写 _PERSIST_SECTIONS（ui/api/vad），不写死整个 Config。
    """
    data: dict = {}
    for sec in _PERSIST_SECTIONS:
        dc = getattr(cfg, sec)
        data[sec] = {k: v for k, v in vars(dc).items()}
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p


# 热生效 vs 需重启 的字段分组（供 UI 决定是否提示重启）
HOT_APPLICABLE = {
    "ui": ("target_lang", "source_lang", "subtitle_theme", "overlay_theme",
           "font_size", "show_original"),
    "api": ("base_url", "api_key", "model", "translate_timeout_s",
            "minutes_timeout_s"),
    "vad": ("threshold", "min_silence_ms"),
}
RESTART_REQUIRED = {
    "asr": ("model", "device", "compute_type", "threads", "segment_policy"),
    "audio": ("source", "sample_rate", "chunk_ms"),
    "backlog": ("enabled", "high_watermark", "low_watermark", "drop_watermark",
                "maxsize", "recover_grace_s", "boost_threshold",
                "boost_min_silence_ms"),
    "session": ("dir",),
}


def classify_changes(old: Config, new: Config) -> tuple[list[str], list[str]]:
    """对比新旧配置，返回 (热生效项, 需重启项) 的字段路径列表。"""
    hot: list[str] = []
    restart: list[str] = []
    for sec, fields in HOT_APPLICABLE.items():
        o, n = getattr(old, sec), getattr(new, sec)
        for f in fields:
            if getattr(o, f, None) != getattr(n, f, None):
                hot.append(f"{sec}.{f}")
    for sec, fields in RESTART_REQUIRED.items():
        o, n = getattr(old, sec), getattr(new, sec)
        for f in fields:
            if getattr(o, f, None) != getattr(n, f, None):
                restart.append(f"{sec}.{f}")
    return hot, restart
