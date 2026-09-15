"""Meeting-minutes generation (manual trigger, dual input source).

Source A - refined transcript: offline diarization output (speaker +
timestamps), with speaker names applied.
Source B - live text stream: the session's final transcript-log events,
zero extra compute.

Both are submitted to the configured OpenAI-compatible endpoint and the
Markdown result is saved to ``minutes.md`` in the session directory.
"""

import logging
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("RecTheWord.Minutes")

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

MINUTES_SYSTEM = (
    "你是一个专业的会议纪要整理助手。根据用户提供的、带时间戳和说话人标注的"
    "对话文字稿，整理出一份结构化的中文会议纪要，包含以下部分："
    "1. 会议概要（一段话）\n"
    "2. 议题列表（每个议题一行）\n"
    "3. 各议题的讨论要点（要点式）\n"
    "4. 结论与待办事项（负责人如有）\n"
    "使用 Markdown 格式输出。只输出纪要本身，不要额外的解释。"
)


def _build_client(api_base: str, api_key: str, timeout: int = 120):
    if OpenAI is None:
        raise RuntimeError("openai package not installed")
    import httpx

    client = OpenAI(
        base_url=api_base,
        api_key=api_key or "empty",
        timeout=timeout,
        http_client=httpx.Client(trust_env=False),
    )
    return client


def generate_from_segments(
    api_base: str,
    api_key: str,
    model: str,
    segments: List[dict],
    labels=None,
) -> str:
    """Source A: refined (diarized) transcript -> minutes Markdown."""
    if labels is not None:
        transcript = labels.to_minutes_text(segments)
    else:
        from app._labels import SpeakerLabels

        transcript = SpeakerLabels().to_minutes_text(segments)
    if not transcript.strip():
        return "（没有可整理的对话内容）"
    user_prompt = (
        "以下是带时间戳和说话人标注的对话文字稿：\n\n"
        f"{transcript}\n\n"
        "请据此生成中文会议纪要。"
    )
    return _chat(api_base, api_key, model, user_prompt)


def generate_from_text_stream(
    api_base: str,
    api_key: str,
    model: str,
    text: str,
) -> str:
    """Source B: live text stream (lane-marked, timestamped) -> minutes."""
    if not text or not text.strip():
        return "（没有可整理的对话内容）"
    user_prompt = (
        "以下是会议中记录的实时文字流（🎤=本人，🔊=对方，带时间戳）：\n\n"
        f"{text}\n\n"
        "请据此生成中文会议纪要。"
    )
    return _chat(api_base, api_key, model, user_prompt)


def _chat(api_base: str, api_key: str, model: str, user_prompt: str) -> str:
    client = _build_client(api_base, api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": MINUTES_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def save_minutes(session_dir: Path, markdown: str) -> Path:
    p = Path(session_dir) / "minutes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    return p
