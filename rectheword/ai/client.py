"""OpenAI-compatible chat client (works with any compatible endpoint)."""

from __future__ import annotations

import logging
from typing import List, Optional

import requests

from ..config import AISettings

log = logging.getLogger(__name__)


class ChatError(RuntimeError):
    pass


class OpenAICompatibleChat:
    """Minimal ``/chat/completions`` client.

    ``base_url`` may be given as e.g. ``https://api.openai.com/v1`` or
    ``http://localhost:8000``; a ``/chat/completions`` path is appended if the
    url does not already end with it.
    """

    def __init__(self, settings: AISettings) -> None:
        self._settings = settings

    @property
    def available(self) -> bool:
        return self._settings.configured

    def _url(self) -> str:
        base = (self._settings.base_url or "").strip().rstrip("/")
        if not base:
            raise ChatError("AI base_url is empty")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    def chat(self, messages: List[dict], model: Optional[str] = None,
             temperature: Optional[float] = None,
             max_retries: int = 2) -> str:
        """Send a chat completion request and return the assistant text."""
        if not self.available:
            raise ChatError("AI not configured (base_url / api_key missing)")
        payload = {
            "model": model or self._settings.model or "gpt-4o-mini",
            "messages": messages,
            "temperature": self._settings.temperature
            if temperature is None else temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        url = self._url()
        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(
                    url, json=payload, headers=headers,
                    timeout=self._settings.timeout,
                )
                if resp.status_code != 200:
                    raise ChatError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content.strip()
            except (requests.RequestException, KeyError, IndexError,
                    ValueError) as exc:  # noqa: BLE001
                last_err = exc
                log.warning("chat attempt %d failed: %s", attempt + 1, exc)
        raise ChatError(f"AI request failed after {max_retries + 1} attempts: {last_err}")

    def chat_single(self, system: str, user: str) -> str:
        return self.chat([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
