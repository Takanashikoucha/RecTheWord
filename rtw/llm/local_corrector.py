"""本地纠错 + 翻译 Worker 子进程：Qwen3 0.6B（transformers，关闭 thinking）。

用途（比双 ASR 实例更有价值的辅助通道）：
  1. 纠错：拿前面 N 句识别结果作为上下文，让 LLM 修复当前句的识别错误
     （错字 / 漏字 / 语种混杂 / 断句残缺），输出修正后的原文。
  2. 本地翻译：没配置翻译 API 时，用它做本地化翻译（中/英/日互译）。

协议（Pipe，pickle，镜像 asr/worker.py）：
  父 → 子: {"cmd":"load","model":..,"threads":..}
           | {"cmd":"correct","id":..,"context":[..],"text":..}
           | {"cmd":"translate","id":..,"text":..,"target":"zh"}
           | {"cmd":"shutdown"}
  子 → 父: {"event":"ready"} | {"event":"result","id":..,"text":..}
           | {"event":"error","id":..,"msg":..} | {"event":"bye"}

关闭 thinking：Qwen3 的 chat template 支持 enable_thinking 参数，设为 False
跳过思维链，直接出答案（提速关键）。
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import queue
import threading

log = logging.getLogger(__name__)


def _child_main(conn) -> None:
    """子进程入口：独立推理域，避免 fork 继承父进程状态。"""
    import os
    import torch
    torch.set_num_threads(os.cpu_count() or 4)
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as e:  # noqa: BLE001
        conn.send({"event": "fatal", "msg": f"transformers import failed: {e}"})
        return
    model = None
    tokenizer = None
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            return
        cmd = msg.get("cmd")
        if cmd == "load":
            try:
                torch.set_num_threads(msg.get("threads", 8))
                tokenizer = AutoTokenizer.from_pretrained(msg["model"])
                # 关闭 thinking：Qwen3 chat template 支持 enable_thinking
                if hasattr(tokenizer, "chat_template") and "enable_thinking" in (tokenizer.chat_template or ""):
                    tokenizer.chat_template = tokenizer.chat_template.replace(
                        "{{ enable_thinking }}", "False")
                model = AutoModelForCausalLM.from_pretrained(
                    msg["model"],
                    torch_dtype=torch.float32,
                    device_map="cpu",
                )
                model.eval()
                conn.send({"event": "ready"})
            except Exception as e:  # noqa: BLE001
                conn.send({"event": "fatal", "msg": str(e)})
                return
        elif cmd in ("correct", "translate", "fix_translate") and model is not None:
            rid = msg["id"]
            try:
                if cmd == "fix_translate":
                    # 一次请求同时纠错 + 翻译，返回 {"fixed":..,"translated":..}
                    r = _fix_and_translate(model, tokenizer,
                                          msg.get("context", []),
                                          msg.get("text", ""),
                                          msg.get("target", "zh"))
                    conn.send({"event": "result", "id": rid,
                              "fixed": r.get("fixed", ""),
                              "translated": r.get("translated", "")})
                elif cmd == "correct":
                    text = _correct(model, tokenizer, msg.get("context", []),
                                   msg.get("text", ""))
                    conn.send({"event": "result", "id": rid, "text": text})
                else:
                    text = _translate(model, tokenizer, msg.get("text", ""),
                                     msg.get("target", "zh"))
                    conn.send({"event": "result", "id": rid, "text": text})
            except Exception as e:  # noqa: BLE001
                conn.send({"event": "error", "id": rid, "msg": str(e)})
        elif cmd == "shutdown":
            conn.send({"event": "bye"})
            return


def _gen(model, tokenizer, prompt: str, max_new_tokens: int = 256) -> str:
    """生成（Qwen3 官方推荐采样参数 + 关闭 thinking）。
    官方推荐（Qwen3 介绍页）：temperature=0.7, top_p=0.8, top_k=20。
    关闭 thinking：chat template 的 enable_thinking=False（跳过思维链，提速）。"""
    import torch
    messages = [
        {"role": "system",
         "content": "你是语音识别纠错与翻译助手。只输出最终结果本身：不解释、不复述题目、"
                   "不复述上下文、不编号、不加引号、无思考过程。"},
        {"role": "user", "content": prompt},
    ]
    # Qwen3 关闭 thinking：通过 chat template 的 enable_thinking=False
    try:
        text_in = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
            enable_thinking=False)
    except TypeError:
        # 老版 chat template 不支持该参数，退回普通拼接
        text_in = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text_in, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            # Qwen3 官方推荐采样参数
            do_sample=True,
            temperature=0.7,
            top_p=0.8,
            top_k=20,
            repetition_penalty=1.0,
        )
    gen = out[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(gen, skip_special_tokens=True).strip()


def _extract_json_fixed(out: str) -> str | None:
    """从 LLM 输出中提取 {"fixed": "..."} 的 fixed 字段（容错：找不到返回 None）。"""
    import re
    # 优先匹配 JSON 对象
    m = re.search(r'\{\s*"fixed"\s*:\s*"([^"]*)"', out)
    if m:
        return m.group(1).strip()
    # 退回：匹配任意 JSON 字符串值
    m = re.search(r'"fixed"\s*:\s*"([^"]*)"', out)
    if m:
        return m.group(1).strip()
    return None


def _correct(model, tokenizer, context: list[str], text: str) -> str:
    """纠错：以最近 N 句为上下文，修复当前句的识别错误。
    严格约束：只许改错字/补全被截断的词尾，禁止添加新信息/解释/元评论。
    用 JSON 输出强制格式，杜绝复述指令。"""
    ctx = "\n".join(f"- {c}" for c in context[-3:]) if context else "（无前文）"
    prompt = (
        f"会议语音识别最近几句话（仅供理解语境）：\n{ctx}\n\n"
        f"待修正的这一句：{text}\n\n"
        f"规则（严格遵守，宁可保守）：\n"
        f"1. 只改正明显的错字/同音字错误。除此之外一律不动。\n"
        f"2. 绝对禁止：补全缺失内容、添加新信息、解释、评论、猜测、复述上下文。\n"
        f"3. 若句子不完整（被截断）或无明显错字，原样返回，不要试图补全。\n"
        f"4. 保持原语种、原句式，改动越少越好。\n"
        f"输出格式：只输出一个 JSON 对象 {{\"fixed\": \"修正后的句子\"}}，"
        f"不要输出 JSON 以外的任何文字。"
    )
    out = _gen(model, tokenizer, prompt, max_new_tokens=200)
    fixed = _extract_json_fixed(out)
    return fixed if fixed is not None else text  # 解析失败回退原文


def _translate(model, tokenizer, text: str, target: str) -> str:
    """本地翻译：中/英/日互译，JSON 输出强制格式。"""
    tgt = {"zh": "中文", "en": "英文", "ja": "日文"}.get(target, "中文")
    prompt = (
        f"把下面这句话翻译成{tgt}。\n"
        f"原文：{text}\n"
        f"只输出一个 JSON 对象 {{\"translated\": \"译文\"}}，不要输出 JSON 以外的任何文字。"
    )
    out = _gen(model, tokenizer, prompt, max_new_tokens=200)
    import re
    m = re.search(r'"translated"\s*:\s*"([^"]*)"', out)
    return m.group(1).strip() if m else out.strip()


def _fix_and_translate(model, tokenizer, context: list[str], text: str,
                      target: str) -> dict:
    """一次请求同时纠错 + 翻译（省一次 LLM 往返，降延迟）。
    返回 {"fixed": 修正后原文, "translated": 中文译文}。"""
    import re
    tgt = {"zh": "中文", "en": "英文", "ja": "日文"}.get(target, "中文")
    ctx = "\n".join(f"- {c}" for c in context[-3:]) if context else "（无前文）"
    prompt = (
        f"会议语音识别最近几句话（仅供理解语境）：\n{ctx}\n\n"
        f"待处理这一句：{text}\n\n"
        f"任务（一次完成两件事）：\n"
        f"A. 纠错：只改正明显的错字/同音字错误；禁止补全缺失内容、添加新信息、"
        f"解释、评论、猜测、复述上下文；若句子不完整或无明显错字则原样保留。"
        f"保持原语种、原句式，改动越少越好。\n"
        f"B. 翻译：把纠错后的句子翻译成{tgt}。\n\n"
        f"只输出一个 JSON 对象：{{\"fixed\": \"纠错后的原文\", "
        f"\"translated\": \"{tgt}译文\"}}，不要输出 JSON 以外的任何文字。"
    )
    out = _gen(model, tokenizer, prompt, max_new_tokens=300)
    mf = re.search(r'"fixed"\s*:\s*"([^"]*)"', out)
    mt = re.search(r'"translated"\s*:\s*"([^"]*)"', out)
    return {
        "fixed": mf.group(1).strip() if mf else text,
        "translated": mt.group(1).strip() if mt else "",
    }


class LocalCorrectorClient:
    """Qwen3 0.6B 本地纠错 + 翻译客户端（镜像 AsrWorkerClient）。"""

    def __init__(self, model: str, threads: int = 8,
                 ready_timeout: float = 300.0) -> None:
        self.ready = threading.Event()
        self.error_msg: str | None = None
        ctx = mp.get_context("spawn")
        self.parent_conn, child_conn = ctx.Pipe(duplex=True)
        self.proc = ctx.Process(target=_child_main, args=(child_conn,), daemon=True)
        self.proc.start()
        self._resp_q: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.parent_conn.send({"cmd": "load", "model": model, "threads": threads})
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
            elif ev == "fatal":
                self.error_msg = msg.get("msg", "unknown")
                self.ready.set()
            elif ev in ("result", "error"):
                self._resp_q.put(msg)
            elif ev == "bye":
                break

    def _call(self, cmd: str, payload: dict, timeout: float) -> str:
        rid = f"{cmd}-{threading.get_ident()}-{getattr(self, '_n', 0)}"
        self._n = getattr(self, "_n", 0) + 1
        self.parent_conn.send({**payload, "cmd": cmd, "id": rid})
        msg = self._resp_q.get(timeout=timeout)
        if msg.get("event") == "error":
            raise RuntimeError(msg.get("msg", "llm error"))
        return msg.get("text", "")

    def correct(self, context: list[str], text: str, timeout: float = 30.0) -> str:
        """纠错：返回修正后的句子（失败抛异常，调用方回退原文）。"""
        return self._call("correct", {"context": context, "text": text}, timeout)

    def alive(self) -> bool:
        """子进程是否存活（用于运行时健康检查，避免用坏掉的 corrector 卡死）。"""
        return self.proc is not None and self.proc.is_alive()

    def fix_translate(self, context: list[str], text: str, target: str = "zh",
                     timeout: float = 30.0) -> dict:
        """一次请求同时纠错 + 翻译，返回 {"fixed":.., "translated":..}。"""
        rid = f"fix_translate-{threading.get_ident()}-{getattr(self, '_n', 0)}"
        self._n = getattr(self, "_n", 0) + 1
        self.parent_conn.send({"cmd": "fix_translate", "id": rid,
                              "context": context, "text": text, "target": target})
        msg = self._resp_q.get(timeout=timeout)
        if msg.get("event") == "error":
            raise RuntimeError(msg.get("msg", "llm error"))
        return {"fixed": msg.get("fixed", ""), "translated": msg.get("translated", "")}

    def translate(self, text: str, target: str = "zh", timeout: float = 30.0) -> str:
        """本地翻译：返回译文（失败抛异常，调用方降级）。"""
        return self._call("translate", {"text": text, "target": target}, timeout)

    def shutdown(self) -> None:
        try:
            self.parent_conn.send({"cmd": "shutdown"})
        finally:
            self.proc.join(timeout=5)
