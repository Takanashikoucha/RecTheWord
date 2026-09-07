"""Tests for speaker labeling."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rectheword.asr.engine import Segment
from rectheword.labels import SpeakerLabels, fallback_name


def test_fallback_name():
    assert fallback_name(0) == "说话人0"
    assert fallback_name(2, prefix="Spk") == "Spk2"


def test_name_override_and_persist():
    labels = SpeakerLabels(mapping={})
    labels.set_name(0, "Alice")
    labels.set_name(1, "")
    assert labels.name_for(0) == "Alice"
    assert labels.name_for(1) == "说话人1"  # empty -> fallback
    m = labels.mapping
    assert m == {"0": "Alice", "1": ""}


def test_apply_segments():
    labels = SpeakerLabels(mapping={"0": "Alice", "1": "Bob"})
    segs = [Segment(1000, 2000, 0, "你好"), Segment(3000, 4000, 1, "hello")]
    out = labels.apply(segs)
    assert out[0]["speaker"] == "Alice"
    assert out[1]["speaker"] == "Bob"
    assert out[0]["text"] == "你好"
    assert out[1]["start_ms"] == 3000


def test_minutes_text_includes_names_and_time():
    labels = SpeakerLabels(mapping={"0": "Alice"})
    segs = [Segment(61000, 62000, 0, "议题开始")]
    text = labels.to_minutes_text(segs)
    assert "[01:01]" in text
    assert "Alice" in text
    assert "议题开始" in text


def test_distinct_speakers():
    labels = SpeakerLabels()
    segs = [Segment(0, 1, 2, "a"), Segment(1, 2, 0, "b"), Segment(2, 3, 2, "c")]
    assert labels.distinct_speakers(segs) == [0, 2]
