"""Persistent application configuration.

Stored as JSON under ``~/.rectheword/config.json`` (never committed to git).
The speaker name mapping lives here so it can be reused across sessions.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


def config_dir() -> Path:
    """Return (and create) the user config directory."""
    base = Path(os.environ.get("RECTHEWORD_HOME", Path.home() / ".rectheword"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class AISettings:
    """External OpenAI-compatible chat endpoint settings."""
    enabled: bool = False
    base_url: str = ""            # e.g. https://api.openai.com/v1
    api_key: str = ""
    model: str = ""
    temperature: float = 0.2
    timeout: int = 30

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key)


@dataclass
class ASRSettings:
    """ASR engine / model selection."""
    real_time_model: str = "paraformer-zh-streaming"
    offline_model: str = "iic/SenseVoiceSmall"
    vad_model: str = "fsmn-vad"
    punc_model: str = "ct-punc"
    spk_model: str = "cam++"
    hub: str = "ms"               # "ms" (ModelScope) or "hf" (HuggingFace)
    device: str = "cpu"
    ncpu: int = 4
    model_dir: str = ""           # optional local models root (else default cache)
    preset_spk_num: int = 0       # 0 = auto


@dataclass
class AppConfig:
    mic_device: str = ""
    spk_device: str = ""
    ai: AISettings = field(default_factory=AISettings)
    asr: ASRSettings = field(default_factory=ASRSettings)
    # speaker index -> display name (e.g. {"0": "Alice", "1": "Bob"})
    speaker_labels: Dict[str, str] = field(default_factory=dict)
    default_speaker_names: List[str] = field(
        default_factory=lambda: ["说话人", ""]  # prefix for unnamed speakers
    )

    # ------------------------------------------------------------------ io
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # keep the key name stable as "asr"
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AppConfig":
        d = dict(d or {})
        ai = AISettings(**{k: v for k, v in (d.get("ai") or {}).items()
                           if k in AISettings.__dataclass_fields__})
        asr = ASRSettings(**{k: v for k, v in (d.get("asr") or {}).items()
                             if k in ASRSettings.__dataclass_fields__})
        labels = d.get("speaker_labels") or {}
        labels = {str(k): str(v) for k, v in labels.items()}
        names = d.get("default_speaker_names") or []
        cfg = cls(
            mic_device=d.get("mic_device", ""),
            spk_device=d.get("spk_device", ""),
            ai=ai,
            asr=asr,
            speaker_labels=labels,
        )
        if names:
            cfg.default_speaker_names = [str(x) for x in names]
        return cfg

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or config_path()
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        path = path or config_path()
        if not path.exists():
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return cls.from_dict(json.load(fh))
        except (json.JSONDecodeError, OSError):
            return cls()


# ---------------------------------------------------------------------------
# Session artifact layout
# ---------------------------------------------------------------------------

def recordings_dir() -> Path:
    """Per-app recordings dir under the config dir (not in the repo)."""
    d = config_dir() / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_model_root() -> Path:
    """Where ASR models are cached (configurable via asr.model_dir)."""
    d = config_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d
