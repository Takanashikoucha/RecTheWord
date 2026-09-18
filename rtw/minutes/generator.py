"""会议纪要生成：transcript → 结构化 md（摘要/决议/待办），走 LLM API（用户约束）。"""
from __future__ import annotations

from ..llm_api.client import LlmApiClient
from ..session.store import SessionStore


class MinutesGenerator:
    def __init__(self, api: LlmApiClient, store: SessionStore) -> None:
        self.api = api
        self.store = store

    def generate(self, on_delta, on_done, on_error) -> None:
        md = self.store.export_markdown()
        if not md.strip():
            on_error("没有可生成的会议记录")
            return
        self.api.generate_minutes(md, on_delta, on_done, on_error)
