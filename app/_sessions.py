"""Meeting session store.

A session is one recording. Layout under ``~/.rectheword/sessions/``::

    index.json                          # [{id, start, end, duration_s, devices, status}]
    <YYYYMMDD-HHMMSS>/
        meta.json       # devices, ASR backend, translation config snapshot
        mix.wav / mic.wav / sys.wav
        transcript.jsonl
        refined.json    # (manual refine) [{start_ms, end_ms, spk, text}]
        labels.json     # speaker name map
        minutes.md      # (manual minutes)

Stopping a session archives it with ZERO background computation; refine and
minutes are triggered manually later (here or on a historical session).
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger("RecTheWord.Sessions")


def sessions_root() -> Path:
    base = Path(os.environ.get("RECTHEWORD_HOME", Path.home() / ".rectheword"))
    d = base / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


class SessionStore:
    def __init__(self, root: Path = None):
        self.root = Path(root) if root else sessions_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    # ── index ──

    def _read_index(self) -> list:
        if not self.index_path.exists():
            return []
        try:
            with open(self.index_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_index(self, entries: list):
        tmp = self.index_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.index_path)

    def list_sessions(self) -> list:
        """Newest first."""
        entries = self._read_index()
        entries.sort(key=lambda e: e.get("start", ""), reverse=True)
        return entries

    def get_entry(self, session_id: str) -> dict:
        for e in self._read_index():
            if e.get("id") == session_id:
                return e
        return {}

    # ── lifecycle ──

    def create(self, meta: dict) -> Path:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        sid = ts
        d = self.root / sid
        d.mkdir(parents=True, exist_ok=True)
        meta = dict(meta)
        meta["id"] = sid
        meta["start"] = datetime.now().isoformat(timespec="seconds")
        meta.setdefault("status", "recording")
        self._save_meta(sid, meta)
        entries = self._read_index()
        entries.append(
            {
                "id": sid,
                "start": meta["start"],
                "duration_s": 0,
                "devices": meta.get("devices", {}),
                "status": "recording",
            }
        )
        self._write_index(entries)
        return d

    def close(self, session_id: str, duration_s: float = 0.0,
              status: str = "archived"):
        entries = self._read_index()
        for e in entries:
            if e.get("id") == session_id:
                e["end"] = datetime.now().isoformat(timespec="seconds")
                e["duration_s"] = int(duration_s)
                e["status"] = status
                break
        self._write_index(entries)
        # persist into meta too
        meta_path = self.root / session_id / "meta.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                meta["end"] = datetime.now().isoformat(timespec="seconds")
                meta["duration_s"] = int(duration_s)
                meta["status"] = status
                self._save_meta(session_id, meta)
            except (json.JSONDecodeError, OSError):
                pass

    def mark_status(self, session_id: str, status: str, **fields):
        entries = self._read_index()
        for e in entries:
            if e.get("id") == session_id:
                e["status"] = status
                e.update(fields)
                break
        self._write_index(entries)

    def delete(self, session_id: str):
        d = self.root / session_id
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        entries = [e for e in self._read_index() if e.get("id") != session_id]
        self._write_index(entries)

    # ── per-session files ──

    def path(self, session_id: str) -> Path:
        return self.root / session_id

    def _save_meta(self, session_id: str, meta: dict):
        p = self.root / session_id / "meta.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def load_meta(self, session_id: str) -> dict:
        p = self.root / session_id / "meta.json"
        if not p.exists():
            return {}
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}

    def integrity(self, session_id: str) -> dict:
        """Which expected artifacts exist (for the 'incomplete' badge)."""
        d = self.root / session_id
        return {
            "meta": (d / "meta.json").exists(),
            "mix_wav": (d / "mix.wav").exists(),
            "transcript": (d / "transcript.jsonl").exists(),
            "refined": (d / "refined.json").exists(),
            "minutes": (d / "minutes.md").exists(),
        }
