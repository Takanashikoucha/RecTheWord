"""ASR engines.

Two engines:

* :class:`StreamingEngine` - real-time captions. One instance per audio
  stream (microphone / speaker). Uses ``paraformer-zh-streaming`` with a
  per-instance ``cache`` and 600 ms chunks. Low latency on CPU.
* :class:`OfflineEngine` - full speaker diarization. Uses SenseVoice (zh/en/ja)
  + FSMN-VAD + CT-PUNC + CAM++ and returns per-sentence ``spk`` + timestamps.

Both lazily construct the FunASR ``AutoModel`` on first use, so importing this
module does not pull in torch until a model is actually needed.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..config import ASRSettings

log = logging.getLogger(__name__)

# streaming chunk config (600 ms at 16 kHz -> stride = 10 * 960 = 9600 samples)
STREAM_CHUNK_SIZE = [0, 10, 5]
STREAM_STRIDE = STREAM_CHUNK_SIZE[1] * 960  # 9600
ENCODER_LOOK_BACK = 4
DECODER_LOOK_BACK = 1


@dataclass
class Segment:
    """One recognized sentence (offline diarization output)."""
    start_ms: int
    end_ms: int
    spk: int
    text: str

    def __post_init__(self):
        self.start_ms = int(self.start_ms)
        self.end_ms = int(self.end_ms)
        self.spk = int(self.spk)


class StreamingEngine:
    """Real-time streaming ASR for a single mono stream.

    Usage::

        eng = StreamingEngine(settings)
        eng.load(progress_cb)          # downloads + loads model (blocking)
        for chunk in reader:           # each chunk is 9600 float32 samples
            eng.feed(chunk)            # returns newly decoded text (or "")
        eng.finalize()                 # flush with is_final=True
    """

    def __init__(self, settings: ASRSettings) -> None:
        self._settings = settings
        self._model = None
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._loaded = False
        self._finalized = False

    # ------------------------------------------------------------ lifecycle
    def load(self, progress_cb=None) -> None:
        """Load (and if needed download) the streaming model. Blocking."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            from funasr import AutoModel  # deferred import

            model_dir = None
            if self._settings.model_dir:
                model_dir = self._settings.model_dir
            kwargs = dict(
                model=self._settings.real_time_model,
                device=self._settings.device,
                ncpu=max(1, int(self._settings.ncpu)),
                disable_update=True,
            )
            if self._settings.hub:
                kwargs["hub"] = self._settings.hub
            if model_dir:
                kwargs["model"] = self._resolve_model_path(model_dir,
                                                          self._settings.real_time_model)
            if progress_cb:
                progress_cb("loading streaming model ...")
            self._model = AutoModel(**kwargs)
            self._cache = {}
            self._loaded = True
            self._finalized = False
            if progress_cb:
                progress_cb("streaming model ready")
        log.info("StreamingEngine loaded: %s", self._settings.real_time_model)

    @property
    def loaded(self) -> bool:
        return self._loaded

    @staticmethod
    def _resolve_model_path(model_dir: str, model: str) -> str:
        import os
        # allow a direct directory or <model_dir>/<model>
        direct = os.path.join(model_dir, model)
        if os.path.isdir(direct):
            return direct
        if os.path.isdir(model_dir):
            return model_dir
        return model

    # ------------------------------------------------------------------ feed
    def feed(self, chunk) -> str:
        """Feed one 600 ms chunk (float32, 9600 samples). Returns new text."""
        with self._lock:
            if not self._loaded or self._finalized:
                return ""
            if self._model is None:
                return ""
            res = self._model.generate(
                input=chunk,
                cache=self._cache,
                is_final=False,
                chunk_size=STREAM_CHUNK_SIZE,
                encoder_chunk_look_back=ENCODER_LOOK_BACK,
                decoder_chunk_look_back=DECODER_LOOK_BACK,
                batch_size=1,
            )
            return _extract_text(res)

    def finalize(self) -> str:
        """Flush pending state with ``is_final=True``. Returns final text."""
        with self._lock:
            if not self._loaded or self._finalized or self._model is None:
                return ""
            try:
                res = self._model.generate(
                    input=_empty_chunk(),
                    cache=self._cache,
                    is_final=True,
                    chunk_size=STREAM_CHUNK_SIZE,
                    encoder_chunk_look_back=ENCODER_LOOK_BACK,
                    decoder_chunk_look_back=DECODER_LOOK_BACK,
                    batch_size=1,
                )
                out = _extract_text(res)
            finally:
                # the streaming model re-initializes its cache after finalization
                self._cache = {}
                self._finalized = True
            return out

    def reset(self) -> None:
        """Discard cache and allow a new stream."""
        with self._lock:
            self._cache = {}
            self._finalized = False


# ---------------------------------------------------------------------------
# Offline diarization
# ---------------------------------------------------------------------------

class OfflineEngine:
    """Offline ASR + punctuation + speaker diarization.

    Produces per-sentence segments with anonymous ``spk`` indices and
    millisecond timestamps.
    """

    def __init__(self, settings: ASRSettings) -> None:
        self._settings = settings
        self._model = None
        self._loaded = False

    def load(self, progress_cb=None) -> None:
        if self._loaded:
            return
        from funasr import AutoModel

        kwargs = dict(
            model=self._settings.offline_model,
            vad_model=self._settings.vad_model or None,
            punc_model=self._settings.punc_model or None,
            spk_model=self._settings.spk_model or None,
            device=self._settings.device,
            ncpu=max(1, int(self._settings.ncpu)),
            disable_update=True,
        )
        if self._settings.hub:
            kwargs["hub"] = self._settings.hub
        if progress_cb:
            progress_cb("loading offline model (SenseVoice + VAD + PUNC + CAM++) ...")
        self._model = AutoModel(**kwargs)
        self._loaded = True
        if progress_cb:
            progress_cb("offline model ready")
        log.info("OfflineEngine loaded: %s", self._settings.offline_model)

    @property
    def loaded(self) -> bool:
        return self._loaded

    def transcribe(self, wav_path: str, progress_cb=None) -> List[Segment]:
        """Run diarization over a mono 16 kHz WAV. Returns segments."""
        if self._model is None:
            raise RuntimeError("OfflineEngine not loaded")
        if progress_cb:
            progress_cb("running offline diarization ...")
        gen_kwargs: Dict[str, Any] = dict(input=wav_path, return_spk_res=True)
        if self._settings.preset_spk_num and self._settings.preset_spk_num > 0:
            gen_kwargs["preset_spk_num"] = int(self._settings.preset_spk_num)
        results = self._model.generate(**gen_kwargs)
        segments: List[Segment] = []
        for r in _iter_results(results):
            for info in r.get("sentence_info") or []:
                seg = _make_segment(info)
                if seg is not None:
                    segments.append(seg)
        segments.sort(key=lambda s: s.start_ms)
        if progress_cb:
            progress_cb(f"offline diarization done: {len(segments)} sentences")
        return segments


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _empty_chunk():
    import numpy as np
    return np.zeros(STREAM_STRIDE, dtype="float32")


def _extract_text(res) -> str:
    """Pull the decoded text out of a streaming ``generate`` result."""
    text = ""
    for r in _iter_results(res):
        if isinstance(r, dict):
            t = r.get("text", "")
            if t:
                text += t
    return text.strip()


def _iter_results(res):
    """Normalize the various shapes FunASR returns into an iterable of dicts."""
    if res is None:
        return []
    if isinstance(res, dict):
        return [res]
    try:
        return list(res)
    except TypeError:
        return [res]


def _make_segment(info: Dict[str, Any]) -> Optional[Segment]:
    if not isinstance(info, dict):
        return None
    start = info.get("start")
    end = info.get("end")
    text = info.get("text", info.get("sentence", "")).strip()
    spk = info.get("spk")
    if text == "" and start is None:
        return None
    return Segment(
        start_ms=int(start or 0),
        end_ms=int(end or 0),
        spk=int(spk) if spk is not None else 0,
        text=text,
    )
