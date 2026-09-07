"""Tests for config round-trip and AI client request shaping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rectheword.config import AppConfig, AISettings, ASRSettings
from rectheword.ai.client import OpenAICompatibleChat


def test_config_roundtrip(tmp_path):
    cfg = AppConfig()
    cfg.mic_device = "Mic X"
    cfg.spk_device = "Spk Y"
    cfg.ai = AISettings(enabled=True, base_url="https://api.test/v1",
                        api_key="sk-123", model="gpt-4o-mini", temperature=0.3)
    cfg.asr = ASRSettings(hub="hf", ncpu=8, preset_spk_num=2)
    cfg.speaker_labels = {"0": "Alice"}
    p = tmp_path / "config.json"
    cfg.save(p)
    loaded = AppConfig.load(p)
    assert loaded.mic_device == "Mic X"
    assert loaded.ai.enabled is True
    assert loaded.ai.api_key == "sk-123"
    assert loaded.asr.hub == "hf"
    assert loaded.asr.ncpu == 8
    assert loaded.asr.preset_spk_num == 2
    assert loaded.speaker_labels == {"0": "Alice"}


def test_config_missing_returns_defaults(tmp_path):
    cfg = AppConfig.load(tmp_path / "does_not_exist.json")
    assert cfg.ai.enabled is False


def test_ai_url_normalization():
    s = AISettings(enabled=True, base_url="https://api.test", api_key="k", model="m")
    assert OpenAICompatibleChat(s)._url() == "https://api.test/v1/chat/completions"
    s2 = AISettings(enabled=True, base_url="https://api.test/v1", api_key="k", model="m")
    assert OpenAICompatibleChat(s2)._url() == "https://api.test/v1/chat/completions"
    s3 = AISettings(enabled=True, base_url="https://api.test/v1/chat/completions",
                    api_key="k", model="m")
    assert OpenAICompatibleChat(s3)._url() == "https://api.test/v1/chat/completions"


def test_ai_not_configured():
    s = AISettings(enabled=False, base_url="", api_key="")
    chat = OpenAICompatibleChat(s)
    assert chat.available is False
