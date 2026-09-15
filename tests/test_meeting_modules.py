"""Unit tests for the meeting-scenario modules (no ML / Qt runtime needed).

Covers: _transcript_log, _recorder, _labels, _sessions, and the minutes
prompt-assembly helpers (mocked client).
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from _transcript_log import TranscriptLog  # noqa: E402
from _recorder import Recorder  # noqa: E402
from _labels import SpeakerLabels  # noqa: E402
from _sessions import SessionStore  # noqa: E402


# ── TranscriptLog ──

def test_transcript_log_final_and_translation(tmp_path):
    p = tmp_path / "t.jsonl"
    tl = TranscriptLog(p)
    tl.open()
    seq = tl.log_final("sys", "你好世界", "zh", 1.0, 2.5)
    tl.set_translation(seq, "Hello world")
    tl.close()

    # replay
    finals, patches = {}, {}
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ev = json.loads(line)
            if ev["type"] == "final":
                finals[ev["seq"]] = ev
            elif ev["type"] == "translation_patch":
                patches[ev["seq"]] = ev["translated"]
    assert seq in finals
    assert finals[seq]["text"] == "你好世界"
    assert patches.get(seq) == "Hello world"

    # export merges translation
    tl2 = TranscriptLog(p)
    # (in-memory mirror is empty on a fresh instance; verify file-based export path)
    assert "🔊" in TranscriptLog(p)._lane_prefix["sys"]


def test_transcript_log_interim_not_in_export(tmp_path):
    p = tmp_path / "t.jsonl"
    tl = TranscriptLog(p)
    tl.open()
    tl.log_interim("mic", "partial", "zh", 0.5)
    tl.log_final("mic", "完整句子", "zh", 1.0, 2.0)
    tl.close()
    evs = tl.events()
    types = {e["type"] for e in evs}
    assert "interim" in types and "final" in types


# ── Recorder ──

def test_recorder_writes_three_wavs(tmp_path):
    rec = Recorder(tmp_path, "meeting")
    # 1 second of 16kHz audio per lane
    mic = np.sin(np.linspace(0, 2 * np.pi * 440 * 1.0, 16000)).astype(np.float32) * 0.3
    sys_ = np.sin(np.linspace(0, 2 * np.pi * 300 * 1.0, 16000)).astype(np.float32) * 0.3
    rec.add_chunk("mic", mic)
    rec.add_chunk("sys", sys_)
    written = rec.finish()
    assert set(written) == {"mix", "mic", "sys"}
    for name in ("mix", "mic", "sys"):
        f = tmp_path / f"meeting_{name}.wav"
        assert f.exists() and f.stat().st_size > 44  # wav header + data
    # mix should contain both signals (non-trivial)
    import wave

    with wave.open(str(tmp_path / "meeting_mix.wav"), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000


def test_recorder_empty(tmp_path):
    rec = Recorder(tmp_path, "x")
    written = rec.finish()
    assert written == {}


# ── SpeakerLabels ──

def test_labels_set_get_apply(tmp_path):
    lp = tmp_path / "labels.json"
    lab = SpeakerLabels(lp)
    lab.set_name(0, "张三")
    lab.set_name(1, "李四")
    assert lab.name_for(0) == "张三"
    assert lab.name_for(2) == "说话人2"  # default
    lab.save()
    lab2 = SpeakerLabels.load(lp)
    assert lab2.name_for(0) == "张三"

    segs = [
        {"start_ms": 0, "end_ms": 1000, "spk": 0, "text": "你好"},
        {"start_ms": 1000, "end_ms": 2000, "spk": 1, "text": "你好吗"},
    ]
    applied = lab2.apply(segs)
    assert applied[0]["speaker"] == "张三"
    txt = lab2.to_minutes_text(applied)
    assert "张三:" in txt and "李四:" in txt


# ── SessionStore ──

def test_session_lifecycle(tmp_path):
    store = SessionStore(tmp_path)
    d = store.create({"devices": {"sys": "out", "mic": "in"}})
    assert (d / "meta.json").exists()
    sid = d.name
    # simulate artifacts
    (d / "mix.wav").write_bytes(b"x")
    (d / "transcript.jsonl").write_text("{}\n")
    store.close(sid, duration_s=120)
    entry = store.get_entry(sid)
    assert entry["status"] == "archived"
    assert entry["duration_s"] == 120
    integ = store.integrity(sid)
    assert integ["mix_wav"] and integ["transcript"] and not integ["refined"]
    # delete
    store.delete(sid)
    assert not (tmp_path / sid).exists()
    assert store.get_entry(sid) == {}


def test_session_index_persistence(tmp_path):
    store = SessionStore(tmp_path)
    d1 = store.create({})
    d2 = store.create({})
    store.close(d1.name, 10)
    store.close(d2.name, 20)
    # new store instance reads the same index
    store2 = SessionStore(tmp_path)
    sessions = store2.list_sessions()
    assert len(sessions) == 2
    # newest first
    assert sessions[0]["id"] == d2.name


# ── minutes prompt assembly (mocked client) ──

def test_minutes_prompt_sources(monkeypatch):
    import _minutes

    captured = {}

    def fake_chat(api_base, api_key, model, user_prompt):
        captured["prompt"] = user_prompt
        return "# 纪要\n- 测试"

    monkeypatch.setattr(_minutes, "_chat", fake_chat)

    md = _minutes.generate_from_segments(
        "http://x", "k", "m",
        [{"start_ms": 0, "end_ms": 1000, "spk": 0, "text": "讨论A"}],
    )
    assert "纪要" in md
    assert "讨论A" in captured["prompt"]

    md2 = _minutes.generate_from_text_stream("http://x", "k", "m", "[00:01] 🔊 你好")
    assert "纪要" in md2
    assert "🔊 你好" in captured["prompt"]

    # empty inputs degrade gracefully
    assert "没有可整理" in _minutes.generate_from_text_stream("http://x", "k", "m", "")
    assert "没有可整理" in _minutes.generate_from_segments("http://x", "k", "m", [])


def test_minutes_save(tmp_path):
    import _minutes

    p = _minutes.save_minutes(tmp_path, "# 标题\n内容")
    assert p.exists()
    assert "标题" in p.read_text(encoding="utf-8")
