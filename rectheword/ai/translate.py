"""Real-time translation of recognized text to Simplified Chinese."""

from __future__ import annotations

import re
import threading
from typing import Optional

from .client import ChatError, OpenAICompatibleChat
from ..config import AISettings

TRANSLATE_SYSTEM = (
    "你是一个专业翻译。把用户给你的文本翻译成简体中文，"
    "只输出译文本身，不要解释、不要保留原文、不要添加任何前缀。"
    "如果文本是中文，原样输出。"
)

# a "sentence-ish" boundary: end punctuation or a long run of words
_SENT_END = re.compile(r"[。！？!?…；;]+$")
_MIN_LEN = 4
_MAX_LEN = 120


def should_translate(text: str) -> bool:
    """Decide whether a partial/complete text is worth translating now."""
    text = (text or "").strip()
    if len(text) < _MIN_LEN:
        return False
    if _SENT_END.search(text):
        return True
    if len(text) >= _MAX_LEN:
        return True
    return False


class Translator:
    """Incremental, throttled translator.

    Call :meth:`feed` with newly recognized text (per stream). It buffers and
    fires an async translation once a sentence boundary / length threshold is
    reached, invoking ``on_result`` on a worker thread.
    """

    def __init__(self, settings: AISettings, on_result, enabled: bool = True) -> None:
        self._settings = settings
        self._on_result = on_result  # callable(translated_text)
        self._client: Optional[OpenAICompatibleChat] = None
        self._buffer = ""
        self._lock = threading.Lock()
        self._busy = False
        self._enabled = enabled and settings.configured
        if self._enabled:
            self._client = OpenAICompatibleChat(settings)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def feed(self, text: str) -> None:
        if not self._enabled or not text:
            return
        with self._lock:
            self._buffer += text
            fire = should_translate(self._buffer)
            payload = self._buffer if fire else ""
        if not fire or not payload:
            return
        with self._lock:
            self._buffer = ""
        threading.Thread(target=self._translate, args=(payload,), daemon=True).start()

    def flush(self) -> None:
        """Translate any remaining buffered text (e.g. at stop time)."""
        if not self._enabled:
            return
        with self._lock:
            payload = self._buffer
            self._buffer = ""
        if payload:
            threading.Thread(target=self._translate, args=(payload,),
                             daemon=True).start()

    def _translate(self, text: str) -> None:
        if self._client is None:
            return
        try:
            out = self._client.chat_single(TRANSLATE_SYSTEM, text)
        except ChatError as exc:
            # surface but don't crash the app
            self._on_result(f"（翻译失败：{exc}）")
            return
        self._on_result(out)
