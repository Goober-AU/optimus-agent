"""Content-reconstruction proxy for the local model gateway.

Why this exists: the gateway in front of the GLM cloud models extracts
SILO-BENCH's fenced XML tool-call blocks from the model's visible output and
re-packages them as native OpenAI `tool_calls` (finish_reason="tool_calls",
empty `message.content`). The official SILO-BENCH engine only parses
`message.content` (src/utils/parsing.py), so without reconstruction every GLM
round looks like "no valid tool calls" and cases never converge.

What it does: a localhost HTTP reverse proxy that forwards each
/chat/completions request VERBATIM to the gateway (same body, same auth
header) and rewrites only the response: when `tool_calls` entries carry the
extracted SILO XML (function.name contains the complete
<tool>...</tool><parameters>...</parameters> block), the blocks are re-wrapped
in the official fence and appended back to `message.content`; `tool_calls` is
removed and finish_reason is normalized to "stop".

The request bytes are never modified, so `usage.prompt_tokens` remain the
real server-side counts of exactly what the official engine sent. The
response `usage` is passed through untouched. Every rewrite is logged to a
JSONL file for honest reporting.

deepseek models do not need this proxy (fences pass through natively); it is
only enabled for GLM runs.
"""

from __future__ import annotations

import json
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


class ReconstructionProxy:
    def __init__(
        self,
        upstream_base: str,
        fence_open: str,
        fence_close: str,
        rewrite_log: Path | None = None,
        bearer: str | None = None,
    ):
        self.upstream_base = upstream_base.rstrip("/")
        self.fence_open = fence_open
        self.fence_close = fence_close
        self.rewrite_log = rewrite_log
        self.bearer = bearer
        self.rewrites = 0
        self.forwards = 0
        self._lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # silence default stderr noise
                pass

            def _reply(self, code: int, body: bytes, content_type: str):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                path = self.path

                headers = {}
                for key in ("Authorization", "Content-Type", "Accept", "User-Agent"):
                    if self.headers.get(key):
                        headers[key] = self.headers[key]
                if not headers.get("Authorization") and proxy.bearer:
                    headers["Authorization"] = "Bearer " + proxy.bearer

                try:
                    resp = httpx.post(
                        proxy.upstream_base + path,
                        headers=headers,
                        content=body,
                        timeout=3600.0,
                    )
                except Exception as e:
                    err = json.dumps({"error": f"proxy upstream failure: {e}"}).encode()
                    self._reply(502, err, "application/json")
                    return

                with proxy._lock:
                    proxy.forwards += 1

                out_body = resp.content
                is_json = "application/json" in (resp.headers.get("content-type") or "")
                try:
                    req_json = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    req_json = {}
                if is_json and not req_json.get("stream"):
                    try:
                        data = json.loads(resp.content)
                    except json.JSONDecodeError:
                        data = None
                    if data and isinstance(data.get("choices"), list):
                        n_blocks, added_chars = proxy._reconstruct(data)
                        if n_blocks:
                            out_body = json.dumps(data).encode("utf-8")
                            with proxy._lock:
                                proxy.rewrites += 1
                            if proxy.rewrite_log is not None:
                                rec = {
                                    "ts": datetime.now(timezone.utc).isoformat(),
                                    "model": req_json.get("model"),
                                    "n_blocks": n_blocks,
                                    "added_chars": added_chars,
                                }
                                with open(proxy.rewrite_log, "a", encoding="utf-8") as f:
                                    f.write(json.dumps(rec) + "\n")

                ctype = resp.headers.get("content-type") or "application/json"
                self._reply(resp.status_code, out_body, ctype)

            def do_GET(self):
                if self.path.endswith("/healthz"):
                    out = json.dumps({
                        "forwards": proxy.forwards,
                        "rewrites": proxy.rewrites,
                    }).encode()
                    self._reply(200, out, "application/json")
                else:
                    self._reply(404, b'{"error": "not found"}', "application/json")

        self._handler = Handler

    def _reconstruct(self, data: dict) -> tuple[int, int]:
        """Merge extracted SILO XML tool_calls back into message.content.

        The gateway extracts the model's fenced XML in (at least) two shapes:
        A) function.name carries the complete block
           "<tool>...</tool>\n<parameters>...</parameters>";
        B) function.name carries only "<tool>...</tool>" and the
           <parameters>...</parameters> block ends up inside function.arguments
           as a JSON string (often as a dict key with empty value).
        Both are re-wrapped in the official fence here.
        """
        n_blocks = 0
        added_chars = 0
        for choice in data.get("choices", []):
            msg = choice.get("message")
            if not isinstance(msg, dict):
                continue
            tcs = msg.get("tool_calls")
            if not tcs:
                continue
            blocks = []
            for tc in tcs:
                block = self._block_from_tool_call(tc)
                if block:
                    blocks.append(self.fence_open + "\n" + block + "\n" + self.fence_close)
            if blocks:
                content = msg.get("content") or ""
                sep = "\n\n" if content.strip() else ""
                msg["content"] = content + sep + "\n\n".join(blocks)
                added_chars += len(msg["content"]) - len(content)
                n_blocks += len(blocks)
            msg.pop("tool_calls", None)
            if choice.get("finish_reason") == "tool_calls":
                choice["finish_reason"] = "stop"
        return n_blocks, added_chars

    def _block_from_tool_call(self, tc: dict) -> str | None:
        """Rebuild the inner XML block from one extracted tool_call entry."""
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        if "<tool>" not in name or "</tool>" not in name:
            return None
        if "<parameters>" in name:
            return name
        # Shape B: look for the parameters block inside function.arguments.
        args_raw = fn.get("arguments") or ""
        if not isinstance(args_raw, str):
            args_raw = str(args_raw)
        candidates: list[str] = []
        try:
            args_obj = json.loads(args_raw) if args_raw.strip() else None
        except json.JSONDecodeError:
            args_obj = None
        if isinstance(args_obj, dict):
            candidates.extend(str(k) for k in args_obj.keys())
            candidates.extend(str(v) for v in args_obj.values() if isinstance(v, str))
        elif isinstance(args_obj, str):
            candidates.append(args_obj)
        if not candidates:
            candidates.append(args_raw)
        for c in candidates:
            if "<parameters>" in c and "</parameters>" in c:
                return name + "\n  " + c
        return None

    def start(self, host: str = "127.0.0.1", port: int = 43250, max_port_tries: int = 21) -> int:
        """Start the server; returns the bound port."""
        last_err = None
        for p in range(port, port + max_port_tries):
            try:
                srv = ThreadingHTTPServer((host, p), self._handler)
            except OSError as e:
                last_err = e
                continue
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            self._server = srv
            self.port = p
            return p
        raise RuntimeError(f"could not bind proxy port {port}..{port + max_port_tries - 1}: {last_err}")

    def stop(self):
        srv = getattr(self, "_server", None)
        if srv is not None:
            srv.shutdown()
            srv.server_close()
