"""Tests for dshow listing parsing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rectheword.audio.devices import parse_dshow_listing

SAMPLE = '''
ffmpeg version 6.0
[dshow @ 000001] Direct Show video devices :
[dshow @ 000001]  "Microsoft Video Recorder"
[dshow @ 000001]  "OBS Virtual Camera"
[dshow @ 000001] Direct Show audio devices :
[dshow @ 000001]  "Microphone (Realtek High Definition Audio)"
[dshow @ 000001]  "Line In (Realtek High Definition Audio)"
[dshow @ 000001]  "Speakers (Realtek High Definition Audio)"
[dshow @ 000001]  "Stereo Mix (Realtek High Definition Audio)"
'''


def test_parse_mics():
    d = parse_dshow_listing(SAMPLE)
    assert "Microphone (Realtek High Definition Audio)" in d.microphones
    assert "Line In (Realtek High Definition Audio)" in d.microphones
    # video devices must not leak into mics
    assert not any("Video Recorder" in m for m in d.microphones)


def test_parse_speakers():
    d = parse_dshow_listing(SAMPLE)
    # speaker heuristic should catch "Speakers" and "Stereo Mix"
    assert any("Speakers" in s for s in d.speakers)
    assert any("Stereo Mix" in s for s in d.speakers)


def test_empty_listing():
    d = parse_dshow_listing("")
    assert d.microphones == []
    assert d.speakers == []


def test_dedup():
    text = ('[dshow @ x] Direct Show audio devices :\n'
            '[dshow @ x]  "Mic A"\n'
            '[dshow @ x]  "Mic A"\n')
    d = parse_dshow_listing(text)
    assert d.microphones.count("Mic A") == 1
