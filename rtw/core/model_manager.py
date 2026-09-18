"""模型管理器：只从 ModelScope 下载（用户约束），断点续传，进度回报给 StatusMachine。"""
from __future__ import annotations

import logging
from pathlib import Path

from .status_machine import StatusMachine

log = logging.getLogger(__name__)

MODELS = {
    "qwen3_asr": ("Qwen/Qwen3-ASR-0.6B", "models/Qwen3-ASR-0.6B"),
    "qwen3_asr_streaming": ("qfuxa/qwen3-asr-0.6b-streaming", "models/qwen3-asr-0.6b-streaming"),
    "silero_vad": ("pengzhendong/silero-vad", "models/silero_vad"),
}


class ModelManager:
    def __init__(self, models_dir: Path, bus) -> None:
        self.dir = models_dir
        self.bus = bus

    def ensure(self, key: str) -> Path:
        repo, local_rel = MODELS[key]
        dest = self.dir / local_rel
        if dest.exists() and any(dest.iterdir()):
            return dest
        sm = StatusMachine(self.bus, f"model:{key}")
        sm.begin(f"正在从 ModelScope 下载 {repo}", total=None)
        try:
            from modelscope import snapshot_download
            path = snapshot_download(repo, local_dir=str(dest))
            sm.update(message="下载完成，校验中")
            sm.finish()
            return Path(path)
        except Exception as e:  # noqa: BLE001
            sm.error(f"下载失败：{e}")
            raise
