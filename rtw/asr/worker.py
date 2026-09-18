"""ASR Worker 子进程：与 UI 进程零竞争的推理域（方案 §2 进程隔离）。

协议（Pipe，pickle）：
  父 → 子: {"cmd":"load","model":..,"compute":..} | {"cmd":"transcribe","id":..,"pcm":bytes,"lane":str} | {"cmd":"shutdown"}
  子 → 父: {"event":"ready"} | {"event":"result","id":..,"text":..,"language":..} | {"event":"error","id":..,"msg":..}

启动流程：spawn 子进程 → 发送 load → 等待 ready（期间 UI 显示"ASR 模型加载中"）。
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import queue
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def _child_main(conn) -> None:
    """子进程入口：保持独立，避免 fork 继承父进程状态。"""
    import os
    import torch
    torch.set_num_threads(os.cpu_count() or 4)  # 默认自适应；load 时按 config 覆盖
    try:
        from qwen_asr import Qwen3ASRModel
    except Exception as e:  # noqa: BLE001
        conn.send({"event": "fatal", "msg": f"qwen_asr import failed: {e}"})
        return
    model = None
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            return
        cmd = msg.get("cmd")
        if cmd == "load":
            try:
                torch.set_num_threads(msg.get("threads", 8))
                model = Qwen3ASRModel.from_pretrained(
                    msg["model"],
                    dtype=torch.float32,
                    device_map=msg.get("device", "cpu"),
                    max_new_tokens=msg.get("max_new_tokens", 128),
                )
                conn.send({"event": "ready"})
            except Exception as e:  # noqa: BLE001
                conn.send({"event": "fatal", "msg": str(e)})
                return
        elif cmd == "transcribe" and model is not None:
            rid = msg["id"]
            try:
                import numpy as np
                pcm = np.frombuffer(msg["pcm"], dtype="<i2").astype(np.float32) / 32768.0
                results = model.transcribe(audio=(pcm, 16000), language=None)
                r = results[0]
                conn.send({"event": "result", "id": rid,
                          "text": r.text, "language": getattr(r, "language", "")})
            except Exception as e:  # noqa: BLE001
                conn.send({"event": "error", "id": rid, "msg": str(e)})
        elif cmd == "shutdown":
            conn.send({"event": "bye"})
            return


class AsrWorkerClient:
    def __init__(self, model: str, compute: str = "int8",
                 max_new_tokens: int = 128, ready_timeout: float = 300.0,
                 threads: int = 8) -> None:
        self.ready = threading.Event()
        self.error_msg: str | None = None
        ctx = mp.get_context("spawn")
        self.parent_conn, child_conn = ctx.Pipe(duplex=True)
        self.proc = ctx.Process(target=_child_main, args=(child_conn,), daemon=True)
        self.proc.start()
        self._req_q: queue.Queue = queue.Queue()
        self._resp_q: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.parent_conn.send({"cmd": "load", "model": model,
                              "device": "cpu", "max_new_tokens": max_new_tokens,
                              "threads": threads})
        self.ready.wait(ready_timeout)

    def _read_loop(self) -> None:
        while True:
            try:
                msg = self.parent_conn.recv()
            except EOFError:
                break
            ev = msg.get("event")
            if ev == "ready":
                self.ready.set()
            elif ev in ("fatal",):
                self.error_msg = msg.get("msg", "unknown")
                self.ready.set()
            elif ev in ("result", "error"):
                self._resp_q.put(msg)
            elif ev == "bye":
                break

    def transcribe(self, pcm: bytes, lane: str = "mic", timeout: float = 60.0) -> dict:
        """阻塞等待一段音频的识别结果。"""
        with _ids_lock:
            _ids_counter[0] += 1
            n = _ids_counter[0]
        rid = f"{lane}-{threading.get_ident()}-{n}"
        self.parent_conn.send({"cmd": "transcribe", "id": rid, "pcm": pcm, "lane": lane})
        msg = self._resp_q.get(timeout=timeout)
        if msg.get("event") == "error":
            raise RuntimeError(msg.get("msg", "asr error"))
        return {"text": msg.get("text", ""), "language": msg.get("language", "")}

    def shutdown(self) -> None:
        try:
            self.parent_conn.send({"cmd": "shutdown"})
        finally:
            self.proc.join(timeout=5)


import itertools as _itertools
_ids_lock = threading.Lock()
_ids_counter = [0]
