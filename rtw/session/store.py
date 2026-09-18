"""会话存储：~/.rectheword/sessions/<ts>/{meta.json, mic.wav, sys.wav, transcript.jsonl, minutes.md, labels.json}"""
from __future__ import annotations

import json
import time
from pathlib import Path


class SessionStore:
    def __init__(self, base: str | Path = "~/.rectheword/sessions") -> None:
        self.base = Path(base).expanduser()
        self.current: Path | None = None
        self._transcript_f = None

    def start(self, meta: dict) -> Path:
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.current = self.base / ts
        self.current.mkdir(parents=True, exist_ok=True)
        (self.current / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self._transcript_f = (self.current / "transcript.jsonl").open("a", encoding="utf-8")
        return self.current

    def add_line(self, lane: str, ts_ms: int, text: str, language: str = "",
                speaker: str = "", translated: str = "") -> None:
        if not self._transcript_f:
            return
        self._transcript_f.write(json.dumps(
            {"ts": ts_ms, "lane": lane, "text": text, "lang": language,
             "speaker": speaker, "translated": translated},
            ensure_ascii=False) + "\n")
        self._transcript_f.flush()

    def save_labels(self, labels: list) -> None:
        if self.current:
            (self.current / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_minutes(self, md: str) -> None:
        if self.current:
            (self.current / "minutes.md").write_text(md, encoding="utf-8")

    def close(self) -> None:
        if self._transcript_f:
            self._transcript_f.close()
            self._transcript_f = None

    def export_markdown(self) -> str:
        if not self.current:
            return ""
        f = self.current / "transcript.jsonl"
        if not f.exists():
            return ""
        lines = []
        for raw in f.read_text(encoding="utf-8").splitlines():
            d = json.loads(raw)
            t = time.strftime("%H:%M:%S", time.localtime(d["ts"] / 1000))
            spk = f"【{d['speaker']}】" if d.get("speaker") else f"【{d['lane']}】"
            lines.append(f"- `{t}` {spk} {d['text']}")
            if d.get("translated"):
                lines.append(f"  - 译：{d['translated']}")
        return "\n".join(lines)
