"""OpenAI 兼容 API 客户端：翻译（流式 SSE 逐字回传）+ 纪要（结构化）。

唯一的翻译/纪要后端（用户约束：只走 API）。含超时、重试、显式降级。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque

import httpx

log = logging.getLogger(__name__)


class _EnvProxyFix:
    """httpx 0.28 的 no_proxy 解析器遇到 '::1,[::1]' 这类括号写法会崩
    （把端口解析成 ':1]'）。请求期间临时归一化 no_proxy，结束后还原。"""

    def __enter__(self) -> "_EnvProxyFix":
        import os
        self._saved = {}
        for k in ("no_proxy", "NO_PROXY"):
            v = os.environ.get(k, "")
            if v:
                self._saved[k] = v
                items = [x.strip() for x in v.split(",")
                        if x.strip() and not x.strip().startswith("[")]
                os.environ[k] = ",".join(dict.fromkeys(
                    ["localhost", "127.0.0.1"] +
                    [x for x in items if x not in ("localhost", "127.0.0.1")]))
        return self

    def __exit__(self, *exc) -> None:
        import os
        for k, v in self._saved.items():
            os.environ[k] = v


class LlmApiClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 translate_timeout: float = 15.0, minutes_timeout: float = 120.0) -> None:
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.model = model
        self.translate_timeout = translate_timeout
        self.minutes_timeout = minutes_timeout
        self.misconfigured = not self.base  # 未配置 base_url
        self.available = not self.misconfigured  # 未配置 → 直接不可用（不逐句报错）
        self.last_error = ""
        self._ctx_history: deque[str] = deque(maxlen=10)
        self._env_lock = threading.Lock()

    def health(self) -> bool:
        try:
            with self._env_lock, _EnvProxyFix():
                r = httpx.get(f"{self.base}/models", headers=self._hdr(), timeout=5.0)
            self.available = r.status_code < 500
        except Exception as e:  # noqa: BLE001
            self.available = False
            self.last_error = str(e)
        return self.available

    def _hdr(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def translate_stream(self, text: str, source_lang: str, target_lang: str,
                        on_delta, on_done, on_error) -> None:
        """流式翻译；on_delta(delta_str) 逐字回调，只在新字符到达时调用。"""
        system = (
            f"你是专业字幕翻译。把{source_lang or '源语言'}翻译成{target_lang}，"
            "口语化、简洁、不加解释，只输出译文。"
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "max_tokens": 256,
            "stream": True,
        }
        emitted = 0

        def _run() -> None:
            nonlocal emitted
            try:
                with self._env_lock, _EnvProxyFix():
                    with httpx.stream("POST", f"{self.base}/chat/completions",
                                     headers=self._hdr(), json=body,
                                     timeout=self.translate_timeout) as r:
                        for line in r.iter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                            except Exception:  # noqa: BLE001
                                continue
                            if delta:
                                new_part = delta[emitted:] if len(delta) > emitted else delta
                                if new_part:
                                    emitted = max(emitted, len(delta))
                                    on_delta(new_part)
                self._ctx_history.append(text)
                on_done()
            except Exception as e:  # noqa: BLE001
                self.available = False
                self.last_error = str(e)
                on_error(str(e))

        threading.Thread(target=_run, daemon=True).start()

    def translate_blocking(self, text: str, source_lang: str, target_lang: str) -> str:
        out: list[str] = []
        done = threading.Event()

        def _done() -> None:
            done.set()

        def _err(e: str) -> None:
            done.set()

        self.translate_stream(text, source_lang, target_lang,
                             lambda d: out.append(d), _done, _err)
        done.wait(self.translate_timeout + 5)
        return "".join(out)

    def generate_minutes(self, transcript_md: str, on_delta, on_done, on_error) -> None:
        """把整场 transcript 交给 LLM 生成结构化纪要（摘要/决议/待办）。"""
        prompt = (
            "根据以下会议记录生成中文会议纪要，严格输出三段 markdown："
            "## 会议摘要\\n## 决议事项\\n## 待办跟进（- [人名] 事项）。\n\n"
            + transcript_md
        )
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 2048,
            "stream": True,
        }

        def _run() -> None:
            try:
                with self._env_lock, _EnvProxyFix():
                    with httpx.stream("POST", f"{self.base}/chat/completions",
                                     headers=self._hdr(), json=body,
                                     timeout=self.minutes_timeout) as r:
                        for line in r.iter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                            except Exception:  # noqa: BLE001
                                continue
                            if delta:
                                on_delta(delta)
                on_done()
            except Exception as e:  # noqa: BLE001
                self.available = False
                self.last_error = str(e)
                on_error(str(e))

        threading.Thread(target=_run, daemon=True).start()
