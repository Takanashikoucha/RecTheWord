"""Structured meeting transcript log.

Records every recognized sentence (interim and final) as a JSONL event stream
plus a readable text export. One instance per meeting session.

Event shape (JSONL line)::

    {"type": "final", "seq": 12, "lane": "sys", "t_start": 123.4,
     "t_end": 127.9, "lang": "zh", "text": "...", "translated": "..."}

``translated`` is filled in asynchronously once the translation arrives.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("RecTheWord.TranscriptLog")


class TranscriptLog:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._fp = None
        self._events = []  # in-memory mirror (bounded)
        self._max_events = 5000
        self._next_seq = 0
        self._lane_prefix = {"sys": "🔊", "mic": "🎤"}

    # ── lifecycle ──

    def open(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self._path, "a", encoding="utf-8", buffering=1)
        self._fp.write(
            f'# Meeting transcript started at '
            f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
        )

    def close(self):
        with self._lock:
            if self._fp:
                try:
                    self._fp.flush()
                    self._fp.close()
                except Exception:
                    pass
                self._fp = None

    # ── events ──

    def _append(self, event: dict):
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                del self._events[: len(self._events) - self._max_events]
            if self._fp:
                try:
                    self._fp.write(json.dumps(event, ensure_ascii=False) + "\n")
                except OSError as e:
                    log.warning(f"Transcript log write failed: {e}")

    def next_seq(self) -> int:
        with self._lock:
            self._next_seq += 1
            return self._next_seq

    def log_interim(self, lane: str, text: str, lang: str, t_start: float):
        self._append(
            {
                "type": "interim",
                "seq": self.next_seq(),
                "lane": lane,
                "t_start": round(t_start, 3),
                "lang": lang,
                "text": text,
            }
        )

    def log_final(self, lane: str, text: str, lang: str,
                 t_start: float, t_end: float) -> int:
        seq = self.next_seq()
        self._append(
            {
                "type": "final",
                "seq": seq,
                "lane": lane,
                "t_start": round(t_start, 3),
                "t_end": round(t_end, 3),
                "lang": lang,
                "text": text,
            }
        )
        return seq

    def set_translation(self, seq: int, translated: str):
        if not translated:
            return
        with self._lock:
            for ev in reversed(self._events):
                if ev.get("seq") == seq:
                    ev["translated"] = translated
                    break
            if self._fp:
                # Append a patch line; readers merge by seq.
                try:
                    self._fp.write(
                        json.dumps(
                            {"type": "translation_patch", "seq": seq,
                             "translated": translated},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                except OSError as e:
                    log.warning(f"Transcript log write failed: {e}")

    # ── export ──

    def export_text(self) -> str:
        """Readable text with timestamps and lane markers (final events only)."""
        lines = []
        seen = set()
        translations = {}
        for ev in self._events:
            if ev.get("type") == "translation_patch":
                translations[ev["seq"]] = ev.get("translated", "")
        for ev in self._events:
            if ev.get("type") != "final" or ev["seq"] in seen:
                continue
            seen.add(ev["seq"])
            ts = datetime.fromtimestamp(0)  # placeholder, t is relative seconds
            mm, ss = divmod(int(ev.get("t_start", 0)), 60)
            stamp = f"{mm:02d}:{ss:02d}"
            prefix = self._lane_prefix.get(ev.get("lane", "sys"), "?")
            line = f"[{stamp}] {prefix} {ev.get('text','').strip()}"
            tr = translations.get(ev["seq"])
            if tr:
                line += f"\n    -> {tr.strip()}"
            lines.append(line)
        return "\n".join(lines)

    def events(self) -> list:
        with self._lock:
            return list(self._events)
