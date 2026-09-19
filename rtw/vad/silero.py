"""Silero VAD（ONNX Runtime）封装：32ms 窗、流式判停、强制断句。

模型文件 silero_vad.onnx 由模型管理器从 ModelScope 预取到 models/ 目录。
输入张量：input(1,512) float32、sr(1,) 、state(1,64,1) 、chunk_length(1,) int64。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class SpeechSegment:
    start_ms: int
    end_ms: int
    pcm: bytes          # int16 mono 16k
    lane: str           # "mic" | "sys"


class SileroVad:
    def __init__(self, model_path: str | Path, *,
                 threshold: float = 0.5,
                 min_speech_ms: int = 250,
                 min_silence_ms: int = 300,
                 max_speech_ms: int = 8000,
                 sample_rate: int = 16000) -> None:
        import onnxruntime as ort
        self.sr = sample_rate
        self.win = int(0.032 * sample_rate)
        self.threshold = threshold
        self.min_speech_win = max(1, min_speech_ms // 32)
        self.min_silence_win = max(1, min_silence_ms // 32)
        self.max_speech_win = max(2, max_speech_ms // 32)
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(str(model_path), sess_options=opts)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._sr = np.array(self.sr, dtype=np.int64)
        self._in_speech = False
        self._seg_start_win = 0
        self._silence_run = 0
        self._speech_run = 0
        self._chunks: list[bytes] = []
        self._win_idx = 0          # 绝对窗序号

    # ---- 运行时可调（积压自适应：源头减量）----

    def set_threshold(self, t: float) -> None:
        """动态调整语音判定阈值（越高越不敏感 → 少产段，用于积压减压）。"""
        self.threshold = max(0.0, min(1.0, float(t)))

    def set_min_silence_ms(self, ms: int) -> None:
        """动态调整最短静音断句门限（越长越晚断句 → 段更少更长，减段数）。"""
        self.min_silence_win = max(1, int(ms) // 32)

    def set_min_speech_ms(self, ms: int) -> None:
        """动态调整最短语音时长门限（越长越忽略短促噪声 → 少产段）。"""
        self.min_speech_win = max(1, int(ms) // 32)

    def feed(self, pcm: bytes, lane: str = "mic") -> list[SpeechSegment]:
        """送入一段 PCM（任意长度），返回新产生的语音段（可能为空）。"""
        out: list[SpeechSegment] = []
        arr = pcm
        i = 0
        nwin = self.win * 2
        while i + nwin <= len(arr):
            chunk = np.frombuffer(arr[i:i + nwin], dtype="<i2").astype(np.float32) / 32768.0
            outs = self.sess.run(None, {
                "input": chunk.reshape(1, -1),
                "sr": self._sr,
                "state": self._state,
            })
            pred = float(outs[0][0][0])
            self._state = outs[1].copy()
            i += nwin
            self._tick(pred, arr[i - nwin:i], lane, out)
        return out

    def _tick(self, pred: float, raw: bytes, lane: str,
              out: list[SpeechSegment]) -> None:
        voiced = pred >= self.threshold
        if voiced:
            self._speech_run += 1
            self._silence_run = 0
            if not self._in_speech and self._speech_run >= self.min_speech_win:
                self._in_speech = True
                self._seg_start_win = self._win_idx - self._speech_run + 1
                self._chunks = []
        else:
            self._silence_run += 1
            self._speech_run = 0

        if self._in_speech:
            self._chunks.append(raw)
            ended = False
            if self._silence_run >= self.min_silence_win:
                ended = True
            elif (self._win_idx - self._seg_start_win) >= self.max_speech_win:
                ended = True  # 强制断句
            if ended:
                start_ms = (self._seg_start_win * 32)
                end_ms = ((self._win_idx - self._silence_run) * 32)
                out.append(SpeechSegment(start_ms, end_ms, b"".join(self._chunks), lane))
                self._in_speech = False
                self._chunks = []
        self._win_idx += 1
