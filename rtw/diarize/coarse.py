"""实时粗分说话人：按通道 + 能量/停顿启发式（零额外算力）。

会后精修（pyannote）为独立可选模块 refine.py，P4 实现。
"""
from __future__ import annotations

import math


class CoarseDiarizer:
    """每通道维护一个活动说话人槽；能量突变 + 长停顿触发切换。"""

    def __init__(self, energy_jump_db: float = 6.0, pause_ms: int = 2500) -> None:
        self.energy_jump_db = energy_jump_db
        self.pause_ms = pause_ms
        self.enabled = True  # 会后聚类标注开关（UI 可控）
        self._slots: dict[str, dict] = {}
        self._counter = 0

    def _slot(self, lane: str) -> dict:
        if lane not in self._slots:
            self._counter += 1
            self._slots[lane] = {"id": f"S{self._counter}", "energy": None, "last_ts": 0}
        return self._slots[lane]

    def assign(self, lane: str, rms: float, ts_ms: int) -> str:
        slot = self._slot(lane)
        db = 20 * math.log10(max(rms, 1e-6))
        if slot["energy"] is not None and abs(db - slot["energy"]) > self.energy_jump_db:
            self._counter += 1
            slot["id"] = f"S{self._counter}"
        slot["energy"] = db
        if slot["last_ts"] and ts_ms - slot["last_ts"] > self.pause_ms:
            self._counter += 1
            slot["id"] = f"S{self._counter}"
        slot["last_ts"] = ts_ms
        return slot["id"]
