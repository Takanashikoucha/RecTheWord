"""Dual-stream audio pipeline.

Reads interleaved **stereo f32le** PCM from the capture engine's stdout and
splits it into two mono streams (left = microphone, right = speaker loopback),
then feeds 600 ms chunks to per-stream callbacks.

Run as a Qt ``QThread`` so the UI stays responsive. Emits Qt signals:

* ``chunk(channel, samples, offset_s)`` - one 600 ms mono chunk
* ``finished()`` - stream ended (EOF or error)
* ``error(str)``
"""

from __future__ import annotations

import logging
import struct
from typing import List, Optional

import numpy as np

from PySide6.QtCore import QThread, Signal

from ..audio.capture import CHUNK_SAMPLES, SAMPLE_RATE, CaptureEngine
from .split import split_stereo_f32le  # re-export for convenience

log = logging.getLogger(__name__)


class DualAudioPipeline(QThread):
    """Pull stereo PCM from capture, split, chunk, and emit per-channel chunks."""

    chunk = Signal(str, object, float)   # (channel, np.ndarray mono, offset_s)
    finished = Signal()
    error = Signal(str)

    def __init__(self, capture: CaptureEngine, enable_loopback: bool = True,
                 parent=None) -> None:
        super().__init__(parent)
        self._capture = capture
        self._channels = 2 if enable_loopback else 1
        self._enable_loopback = enable_loopback
        self._stop = False
        # per-channel leftover samples that don't yet make a full chunk
        self._buf: dict = {0: np.zeros(0, dtype=np.float32),
                           1: np.zeros(0, dtype=np.float32)}
        self._chunk_count: dict = {0: 0, 1: 0}

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D401 - Qt thread entry
        try:
            self._run_loop()
        except Exception as exc:  # noqa: BLE001
            log.exception("pipeline failed")
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def _run_loop(self) -> None:
        capture = self._capture
        # read enough bytes to guarantee at least one full stereo frame
        read_size = self._channels * CHUNK_SAMPLES * 4
        while not self._stop and capture.running:
            data = capture.read_stdout(read_size)
            if not data:
                # EOF: ffmpeg closed (stop or crash)
                break
            split = split_stereo_f32le(data, self._channels)
            if split is None:
                # not a full frame yet; read more
                continue
            # feed each channel
            for ch, mono in enumerate(split):
                if self._enable_loopback:
                    # channel 0 = mic, 1 = speaker
                    channel_name = "mic" if ch == 0 else "spk"
                else:
                    channel_name = "mic"
                self._feed_channel(channel_name, ch, mono)
            if self._stop:
                break
        # flush any leftover buffered samples as a final partial chunk
        for ch in (0, 1):
            self._flush_channel(ch)

    def _feed_channel(self, channel_name: str, ch: int, mono: np.ndarray) -> None:
        buf = np.concatenate([self._buf[ch], mono])
        self._buf[ch] = buf
        # emit as many full 9600-sample chunks as available
        while len(buf) >= CHUNK_SAMPLES:
            out, buf = buf[:CHUNK_SAMPLES], buf[CHUNK_SAMPLES:]
            offset = self._chunk_count[ch] * CHUNK_SAMPLES / float(SAMPLE_RATE)
            self._chunk_count[ch] += 1
            self.chunk.emit(channel_name, out.copy(), offset)
        self._buf[ch] = buf

    def _flush_channel(self, ch: int) -> None:
        buf = self._buf[ch]
        if len(buf) == 0:
            return
        channel_name = "mic" if ch == 0 else "spk"
        if not self._enable_loopback:
            channel_name = "mic"
        # pad to a full chunk so the streaming model gets a clean is_final feed
        if len(buf) < CHUNK_SAMPLES:
            buf = np.concatenate([buf, np.zeros(CHUNK_SAMPLES - len(buf),
                                                dtype=np.float32)])
        offset = self._chunk_count[ch] * CHUNK_SAMPLES / float(SAMPLE_RATE)
        self._chunk_count[ch] += 1
        self.chunk.emit(channel_name, buf.copy(), offset)
