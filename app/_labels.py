"""Speaker name labeling.

Maps anonymous speaker cluster ids (from offline diarization) to real names.
Persists per session (``labels.json``) so names survive across reopenings and
are applied to both the refined transcript and the generated minutes.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("RecTheWord.Labels")


class SpeakerLabels:
    def __init__(self, path: Path = None):
        self._path = Path(path) if path else None
        self._names = {}  # str(spk) -> name

    # ── mutation ──

    def set_name(self, spk: int, name: str):
        self._names[str(int(spk))] = name.strip()

    def name_for(self, spk: int, default: str = None) -> str:
        return self._names.get(str(int(spk)), default or f"说话人{int(spk)}")

    def names(self) -> dict:
        return dict(self._names)

    # ── application ──

    def apply(self, segments: list) -> list:
        """segments: [{start_ms, end_ms, spk, text}] -> adds 'speaker' field."""
        out = []
        for seg in segments:
            seg = dict(seg)
            seg["speaker"] = self.name_for(seg.get("spk", 0))
            out.append(seg)
        return out

    def to_minutes_text(self, segments: list) -> str:
        """Timestamped, speaker-named transcript for the minutes prompt."""
        lines = []
        for seg in segments:
            spk = seg.get("speaker") or self.name_for(seg.get("spk", 0))
            start = int(seg.get("start_ms", 0)) // 1000
            mm, ss = divmod(start, 60)
            hh, mm = divmod(mm, 60)
            stamp = f"{hh:02d}:{mm:02d}:{ss:02d}"
            lines.append(f"[{stamp}] {spk}: {seg.get('text','').strip()}")
        return "\n".join(lines)

    # ── persistence ──

    def save(self, path: Path = None):
        path = Path(path or self._path)
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._names, fh, ensure_ascii=False, indent=2)
        except OSError as e:
            log.error(f"Failed to save labels {path}: {e}")

    @classmethod
    def load(cls, path: Path) -> "SpeakerLabels":
        inst = cls(path)
        path = Path(path)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                inst._names = {str(k): str(v) for k, v in data.items()}
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"Failed to load labels {path}: {e}")
        return inst
