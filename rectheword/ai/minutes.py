"""Meeting-minutes generation from a speaker-labeled transcript."""

from __future__ import annotations

from typing import List

from .client import ChatError, OpenAICompatibleChat
from ..config import AISettings
from ..labels import SpeakerLabels
from ..asr.engine import Segment

MINUTES_SYSTEM = (
    "你是一个专业的会议纪要整理助手。根据用户提供的、带时间戳和说话人标注的"
    "对话文字稿，整理出一份结构化的中文会议纪要，包含以下部分："
    "1. 会议概要（一段话）\n"
    "2. 议题列表（每个议题一行）\n"
    "3. 各议题的讨论要点（要点式）\n"
    "4. 结论与待办事项（负责人如有）\n"
    "使用 Markdown 格式输出。只输出纪要本身，不要额外的解释。"
)


def generate_minutes(settings: AISettings, segments: List[Segment],
                     labels: SpeakerLabels) -> str:
    """Generate meeting minutes Markdown from labeled segments.

    Raises :class:`ChatError` if the AI is not configured or the request fails.
    """
    if not settings.configured:
        raise ChatError("AI not configured; cannot generate meeting minutes")
    client = OpenAICompatibleChat(settings)
    transcript = labels.to_minutes_text(segments)
    if not transcript.strip():
        return "（没有可整理的对话内容）"
    user_prompt = (
        "以下是带时间戳和说话人标注的对话文字稿：\n\n"
        f"{transcript}\n\n"
        "请据此生成中文会议纪要。"
    )
    return client.chat_single(MINUTES_SYSTEM, user_prompt)
