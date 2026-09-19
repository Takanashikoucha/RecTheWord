"""Silero VAD（ONNX Runtime）封装：32ms 窗、流式判停、强制断句。

模型文件 silero_vad.onnx 由模型管理器从 ModelScope 预取到 models/ 目录。
输入张量：input(1,512) float32、sr(1,) 、state(1,64,1) 、chunk_length(1,) int64。
"""
from __future__ import annotations

import logging
import os
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
                 pre_pad_ms: int = 120,
                 merge_gap_ms: int = 0,
                 tail_pad_ms: int = 0,
                 hard_boundary: bool = True,
                 boundary_follows_asr: bool = False,
                 sample_rate: int = 16000) -> None:
        import onnxruntime as ort
        self.sr = sample_rate
        self.win = int(0.032 * sample_rate)
        self.threshold = threshold
        self.min_speech_win = max(1, min_speech_ms // 32)
        self.min_silence_win = max(1, min_silence_ms // 32)
        self.max_speech_win = max(2, max_speech_ms // 32)
        # 前置 padding：语音确认后向前多看 N 毫秒，保住句首（发音常早于确认点）
        self.pre_pad_win = max(0, pre_pad_ms // 32)
        # 后置 padding：断句时向后多留 N 毫秒，保住弱尾音/尾字
        self.tail_pad_win = max(0, tail_pad_ms // 32)
        # 相邻段合并：两段间隔 < merge_gap_ms 时合并为一段（治 TTS 句内停顿被切碎）
        self.merge_gap_win = max(0, merge_gap_ms // 32)
        # 硬边界开关：True=pre_pad 起点不越过上一句终点（防重复）；False=方案B不夹紧
        self.hard_boundary = hard_boundary
        # 方案A：边界跟随 ASR——VAD 出段时不推进 _last_end_win，由 ASR 解码完成后推进
        self.boundary_follows_asr = boundary_follows_asr
        self._last_end_win: int | None = None
        self._pending_end_win: int | None = None  # 方案A：段出时的终点，待 ASR 确认
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
        self._seg_anchor_win = 0  # 段起点锚点（不被 merge_gap 重置，供 max_speech 判断实际时长）
        self._silence_run = 0
        self._speech_run = 0
        self._chunks: list[bytes] = []
        self._recent: list[tuple[int, bytes]] = []  # 滑动窗缓存（win_idx, raw），供前置 padding
        self._win_idx = 0          # 绝对窗序号
        self._last_end_win = None  # 上一段终点（供相邻段合并）

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

    def set_pre_pad_ms(self, ms: int) -> None:
        """动态调整前置 padding（实验：跟随 ASR 延迟，双通道共用一个值）。"""
        self.pre_pad_win = max(0, int(ms) // 32)

    def confirm_segment_processed(self) -> None:
        """方案A：ASR 解码完成某段后调用，才推进硬边界（边界跟随 ASR 实际进度）。"""
        if self.boundary_follows_asr and self._pending_end_win is not None:
            if self._last_end_win is None or self._pending_end_win > self._last_end_win:
                self._last_end_win = self._pending_end_win
            self._pending_end_win = None

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
        # 维护滑动窗缓存（始终保留，供语音确认时向前取 padding）
        self._recent.append((self._win_idx, raw))
        # 冷启动：录音最初 ~1s 保留更多历史，确保第一段 pre_pad 能追到 win 0（治开场丢字）
        if self._win_idx < 32:  # 前 ~1s（32 窗 × 32ms）
            keep = self._win_idx + 1  # 保留从 win 0 至今全部
        else:
            keep = self.min_speech_win + self.pre_pad_win + 2
        while len(self._recent) > keep:
            self._recent.pop(0)

        voiced = pred >= self.threshold
        if voiced:
            self._speech_run += 1
            self._silence_run = 0
            # 冷启动：录音最初 ~1.5s 且尚无上一段时，降低 min_speech 让第一句更快确认，
            # 并用满 pre_pad 追到 win 0（治开场"靠！"丢句首）
            cold = (self._last_end_win is None and self._win_idx < 48)  # 48 窗 ≈ 1.5s
            eff_min_speech = 4 if cold else self.min_speech_win  # 冷启动只需 ~128ms 语音
            if not self._in_speech and self._speech_run >= eff_min_speech:
                self._in_speech = True
                confirm_win = self._win_idx - self._speech_run + 1
                # 句首起点 = 确认点向前回退 pre_pad_win 个窗（保住发音起始）
                start = confirm_win - self.pre_pad_win
                if cold:
                    start = max(0, start)  # 冷启动直接追到 win 0
                # 硬边界（方案A/当前）：pre_pad 起点绝不越过上一句终点（杜绝跨句重叠→重复句）
                # 方案B：hard_boundary=False 时不夹紧，靠 tail_pad + pre_pad 封顶防重复
                if self.hard_boundary and self._last_end_win is not None and start < self._last_end_win:
                    start = self._last_end_win
                self._seg_start_win = start
                self._seg_anchor_win = start  # 锚点（max_speech 用，不被 merge 重置）
                self._chunks = []
                # 把 padding 窗的 PCM 预先填入 chunks（这些窗早于确认点，尚未被下方追加）
                for wi, wr in self._recent:
                    if wi < confirm_win and wi >= self._seg_start_win:
                        self._chunks.append(wr)
        else:
            self._silence_run += 1
            self._speech_run = 0

        if self._in_speech:
            self._chunks.append(raw)
            ended = False
            over_max = (self._win_idx - self._seg_anchor_win) >= self.max_speech_win
            if self._silence_run >= self.min_silence_win:
                # 相邻段合并：若距上一段终点太近（< merge_gap）且未超 max_speech，不收尾
                if (self.merge_gap_win > 0 and self._last_end_win is not None
                        and (self._seg_start_win - self._last_end_win) < self.merge_gap_win
                        and not over_max):
                    self._seg_start_win = self._last_end_win  # 并回上一段起点
                    # 补回被丢弃的间隙 PCM 不现实（已释放），直接从合并起点继续累积
                else:
                    ended = True
            elif over_max:
                ended = True  # 强制断句（用锚点，不受 merge_gap 重置 _seg_start_win 影响）
                if os.environ.get("RTW_DEBUG_VAD"):
                    print(f"[VAD-DBG] FORCE-CUT at win {self._win_idx} "
                          f"(span {self._win_idx-self._seg_start_win} wins, "
                          f"max_speech_win={self.max_speech_win})", flush=True)
            if ended:
                # 句尾保护：tail_pad_win>0 时少回退几个静音窗，留住弱尾音/尾字
                retreat = max(0, self._silence_run - self.tail_pad_win)
                end_win = self._win_idx - retreat
                start_ms = (self._seg_start_win * 32)
                end_ms = (end_win * 32)
                out.append(SpeechSegment(start_ms, end_ms, b"".join(self._chunks), lane))
                if self.boundary_follows_asr:
                    # 方案A：不立即推进硬边界，记录待 ASR 确认（避免 VAD 快切导致边界超前）
                    self._pending_end_win = end_win
                else:
                    self._last_end_win = end_win
                self._in_speech = False
                self._chunks = []
        self._win_idx += 1
