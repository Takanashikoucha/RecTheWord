"""Mock OpenAI 兼容 API 服务端（测试期由 agent 侧充当翻译/纪要后端）。

能力：
  GET  /v1/models                → 健康检查
  POST /v1/chat/completions     → 流式(SSE) / 非流式
      - 翻译请求（system 含"字幕翻译"）：按规则生成译文（逐字 SSE 推送，模拟真实延迟）
      - 纪要请求（含"会议纪要"）：返回结构化 markdown
  故障注入（用于测试等待态/降级）：
      ?delay=N  额外延迟 N 秒（模拟慢响应）
      ?fail=1   直接 500（模拟宕机）

用法：
  python -m tests.mock_api_server --port 8799
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def fake_translate(text: str, src: str, tgt: str) -> str:
    """确定性伪翻译：测试只需验证链路与时序，不需要真翻译质量。"""
    if "budget" in text.lower() or "预算" in text:
        return f"[{tgt}] 预算相关：{text[:20]}"
    return f"[{tgt}] {text[:30]}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # 静默
        pass

    def _send_json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        p = self.path.split("?")[0]
        if p in ("/models", "/v1/models"):
            self._send_json({"object": "list", "data": [{"id": "mock-translate-1"}]})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self.path.startswith("/v1/chat/completions"):
            self._send_json({"error": "not found"}, 404)
            return
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages", [])
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        user = next((m["content"] for m in msgs if m["role"] == "user"), "")

        if "fail=1" in self.path:
            self._send_json({"error": "injected failure"}, 500)
            return
        delay = 0.0
        if "delay=" in self.path:
            try:
                delay = float(self.path.split("delay=")[1].split("&")[0])
            except ValueError:
                pass
        if delay:
            time.sleep(delay)

        is_translation = "字幕翻译" in system
        if is_translation:
            src = "src"
            tgt = "tgt"
            for tok in system.replace(" ", "").split("翻译成"):
                if tok:
                    src = tok[:2]
            # 简化：直接从 system 里抓不到就用占位
            out = fake_translate(user, "src", "tgt")
        else:
            out = ("## 会议摘要\n（mock 纪要）讨论了预算与采购。\n\n"
                   "## 决议事项\n- 月底前完成服务器比价\n\n"
                   "## 待办跟进\n- [张三] 采购比价\n- [李四] 预算确认\n")

        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            cid = "mock-1"
            for ch in out:
                chunk = {"id": cid, "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"content": ch}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.01)  # 模拟逐字网络延迟
            end = {"id": cid, "object": "chat.completion.chunk",
                  "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(end, ensure_ascii=False)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self._send_json({
                "id": "mock-1", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": out},
                            "finish_reason": "stop"}],
            })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock api listening on 127.0.0.1:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
