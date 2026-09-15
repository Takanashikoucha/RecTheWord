"""Meeting WAV recorder.

Accumulates both audio lanes (16 kHz mono float32 chunks) in memory and, at
stop time, writes three WAV files:

* ``mix.wav`` - both lanes summed (input for offline diarization / submission)
* ``mic.wav`` - microphone lane (self)
* ``sys.wav`` - system loopback lane (other people)

Files land in the session directory supplied by the caller.
"""

import logging
import struct
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("RecTheWord.Recorder")

SAMPLE_RATE = 16000


class Recorder:
    def __init__(self, out_dir: Path, prefix: str):
        self._out_dir = Path(out_dir)
        self._prefix = prefix
        self._buffers = {"mic": [], "sys": []}
        self._closed = False

    # ── capture side ──

    def add_chunk(self, lane: str, chunk: np.ndarray):
        if self._closed or lane not in self._buffers:
            return
        arr = np.asarray(chunk, dtype=np.float32).ravel()
        if arr.size:
            self._buffers[lane].append(arr)

    # ── stop side ──

    def _concat(self, lane: str) -> np.ndarray:
        bufs = self._buffers.get(lane, [])
        if not bufs:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(bufs)

    def finish(self) -> dict:
        """Write the WAV files; returns {name: path}."""
        if self._closed:
            return {}
        self._closed = True
        mic = self._concat("mic")
        sys_ = self._concat("sys")
        if not len(mic) and not len(sys_):
            log.info("No audio captured; skipping WAV write")
            return {}
        self._out_dir.mkdir(parents=True, exist_ok=True)
        n = max(len(mic), len(sys_))
        if n:
            mic = np.pad(mic, (0, n - len(mic))) if len(mic) < n else mic
            sys_ = np.pad(sys_, (0, n - len(sys_))) if len(sys_) < n else sys_
            mix = mic + sys_
            peak = float(np.max(np.abs(mix))) if mix.size else 0.0
            if peak > 1.0:
                mix = mix / peak
        else:
            mix = np.zeros(0, dtype=np.float32)

        written = {}
        for name, data in (("mix", mix), ("mic", mic), ("sys", sys_)):
            path = self._out_dir / f"{self._prefix}_{name}.wav"
            try:
                self._write_wav(path, data)
                written[name] = str(path)
            except OSError as e:
                log.error(f"Failed to write {path}: {e}")
        # Free memory
        self._buffers = {"mic": [], "sys": []}
        return written

    @staticmethod
    def _write_wav(path: Path, data: np.ndarray):
        """Write 16-bit PCM mono WAV."""
        pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
