"""Offline speaker diarization (thin wrapper over :class:`OfflineEngine`)."""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from ..asr.engine import OfflineEngine, Segment
from ..config import ASRSettings

log = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]


def diarize_wav(wav_path: str, settings: ASRSettings,
                progress_cb: ProgressCb = None) -> List[Segment]:
    """Load (if needed) the offline engine and run diarization.

    The engine is cached on the settings object so repeated stops reuse the
    already-loaded model.
    """
    cache = getattr(settings, "_offline_engine", None)
    if cache is None or not cache.loaded:
        engine = OfflineEngine(settings)
        engine.load(progress_cb)
        settings._offline_engine = engine  # type: ignore[attr-defined]
        cache = engine
    return cache.transcribe(wav_path, progress_cb)
