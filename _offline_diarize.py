"""Offline refined-transcript generation (manual trigger, lazy-loaded).

Runs the FunASR family (SenseVoice + FSMN-VAD + CT-PUNC + CAM++) over the
session's mixed WAV to produce per-speaker, timestamped segments. The heavy
model is constructed on demand and released afterwards to keep the realtime
memory footprint small.
"""

import logging
import time
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger("RecTheWord.OfflineDiariZe")


def diarize_wav(
    wav_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    ncpu: int = 4,
) -> List[dict]:
    """Returns [{start_ms, end_ms, spk, text}]. Lazy-loads FunASR."""
    wav_path = str(wav_path)
    if not Path(wav_path).exists():
        raise FileNotFoundError(f"WAV not found: {wav_path}")

    def _p(msg):
        if progress_cb:
            progress_cb(msg)

    _p("Loading offline diarization model (first run may download)...")
    from funasr import AutoModel  # deferred: heavy import

    t0 = time.time()
    model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        spk_model="cam++",
        device="cpu",
        ncpu=ncpu,
        disable_update=True,
        disable_pbar=True,
    )
    _p(f"Model loaded in {time.time() - t0:.0f}s, recognizing...")

    if cancel_flag and cancel_flag():
        raise InterruptedError("cancelled")

    res = model.generate(
        input=wav_path,
        return_spk_res=True,
        batch_size_s=300,
        disable_pbar=True,
    )

    segments: List[dict] = []
    for item in _iter_results(res):
        for info in item.get("sentence_info", []):
            seg = _make_segment(info)
            if seg:
                segments.append(seg)
    _p(f"Diarization done: {len(segments)} segments")
    return segments


def _iter_results(res):
    if res is None:
        return []
    if isinstance(res, dict):
        return [res]
    try:
        return list(res)
    except TypeError:
        return [res]


def _make_segment(info: dict) -> Optional[dict]:
    if not isinstance(info, dict):
        return None
    start = info.get("start")
    end = info.get("end")
    text = (info.get("text") or info.get("sentence") or "").strip()
    spk = info.get("spk")
    if not text and start is None:
        return None
    return {
        "start_ms": int(start or 0),
        "end_ms": int(end or 0),
        "spk": int(spk) if spk is not None else 0,
        "text": text,
    }
