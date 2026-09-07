"""Speaker name labeling.

FunASR diarization yields *anonymous* per-recording speaker indices
(``spk=0,1,2...``). This module maps those indices to human-readable names
(real names, or a fallback like "说话人0"), persists the mapping, and applies
it to segments and to the text sent to the AI for meeting minutes.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .asr.engine import Segment

# prefix used when a speaker has no explicit name
DEFAULT_PREFIX = "说话人"


def fallback_name(index: int, prefix: str = DEFAULT_PREFIX) -> str:
    return f"{prefix}{index}"


class SpeakerLabels:
    """Manage ``spk index -> name`` for one recording session.

    The mapping is stored as ``{str(index): name}`` so it serializes cleanly to
    JSON. An empty name means "use fallback".
    """

    def __init__(self, mapping: Optional[Dict[str, str]] = None,
                 default_prefix: str = DEFAULT_PREFIX) -> None:
        self._map: Dict[str, str] = {str(k): (v or "") for k, v in
                                     (mapping or {}).items()}
        self._prefix = default_prefix

    # ------------------------------------------------------------- mapping
    def set_name(self, index: int, name: str) -> None:
        self._map[str(index)] = (name or "").strip()

    def name_for(self, index: int) -> str:
        return self._map.get(str(index), "") or fallback_name(index, self._prefix)

    @property
    def mapping(self) -> Dict[str, str]:
        return dict(self._map)

    @property
    def prefix(self) -> str:
        return self._prefix

    def distinct_speakers(self, segments: List[Segment]) -> List[int]:
        """Sorted unique speaker indices present in ``segments``."""
        return sorted({s.spk for s in segments})

    # -------------------------------------------------------------- apply
    def apply(self, segments: List[Segment]) -> List[Dict]:
        """Return a list of dicts with ``speaker`` (display name) added."""
        out = []
        for s in segments:
            out.append({
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "spk": s.spk,
                "speaker": self.name_for(s.spk),
                "text": s.text,
            })
        return out

    def to_minutes_text(self, segments: List[Segment]) -> str:
        """Build the labeled transcript sent to the AI for meeting minutes."""
        lines = []
        for d in self.apply(segments):
            t = _format_time(d["start_ms"])
            lines.append(f"[{t}] {d['speaker']}: {d['text']}")
        return "\n".join(lines)

    def to_transcript_text(self, segments: List[Segment]) -> str:
        """Build a readable transcript (grouped by speaker, with timestamps)."""
        lines = []
        for d in self.apply(segments):
            t = _format_time(d["start_ms"])
            lines.append(f"[{t}] {d['speaker']}：{d['text']}")
        return "\n".join(lines)


def _format_time(ms: int) -> str:
    total_s, ms_rem = divmod(int(ms), 1000)
    m, s = divmod(total_s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
