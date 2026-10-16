"""Gateway client for the local model gateway (OCC-2 research).

Talks to the OpenAI-compatible chat endpoint at
http://127.0.0.1:43120/ollama/v1/chat/completions. The bearer token is read
from a file at runtime and is never logged or embedded.

Token accounting rules (see RESEARCH-NOTES.md section 2, measurement caveat):
- usage.prompt_tokens is measured by the server-side real tokenizer and is
  used as the wire-token cost of the text sent as user content.
- usage.completion_tokens includes hidden reasoning tokens; it is recorded
  as a cost/latency proxy only and is never used for wire-token claims.
"""

import concurrent.futures
import json
import os
import pathlib
import threading
import time

import requests

DEFAULT_BASE_URL = "http://127.0.0.1:43120"
DEFAULT_TOKEN_FILE = (
    r"C:\Users\openclawuser\optimus-rust-test-20260915\cloud-model-gateway\local-token.txt"
)
CHAT_PATH = "/ollama/v1/chat/completions"
HEALTH_PATH = "/health"

# Models verified live on 2026-10-16 (deepseek-v4-flash retired, Azure 503).
LIVE_MODELS = [
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
    "deepseek-v4-pro",
    "deepseek-v4.1-flash",
]


class GatewayError(Exception):
    """Gateway call failed after retries."""


class GatewayClient:
    """Robust chat client: retries with backoff, timeouts, connection reuse."""

    def __init__(self, base_url=DEFAULT_BASE_URL, token_file=None, timeout=240.0):
        self.base_url = base_url.rstrip("/")
        token_file = token_file or os.environ.get("OCC2_TOKEN_FILE") or DEFAULT_TOKEN_FILE
        self._token = pathlib.Path(token_file).read_text(encoding="utf-8").strip()
        if not self._token:
            raise GatewayError("token file is empty: %s" % token_file)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + self._token
        self._baseline_lock = threading.Lock()
        self._token_baseline = {}

    # -- basic requests ------------------------------------------------

    def health(self):
        response = self.session.get(self.base_url + HEALTH_PATH, timeout=30)
        response.raise_for_status()
        return response.json()

    def chat(self, model, messages, max_tokens=1024, temperature=0.0,
             extra_body=None, retries=4, timeout=None):
        """One chat completion. Returns a dict with content/usage/latency.

        Retries on connection errors, 5xx, 429 and 503 with exponential
        backoff. The token is only ever sent in the Authorization header.
        """
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if extra_body:
            body.update(extra_body)
        last_error = None
        for attempt in range(retries):
            start = time.time()
            try:
                response = self.session.post(
                    self.base_url + CHAT_PATH, json=body,
                    timeout=timeout or self.timeout,
                )
                if response.status_code == 200:
                    payload = response.json()
                    return {
                        "content": payload["choices"][0]["message"]["content"],
                        "usage": payload.get("usage", {}),
                        "latency": time.time() - start,
                        "model": model,
                    }
                last_error = GatewayError(
                    "HTTP %s: %s" % (response.status_code, response.text[:200])
                )
                if response.status_code not in (429, 500, 502, 503, 504):
                    raise last_error
            except requests.RequestException as error:
                last_error = GatewayError("request failed: %s" % error)
            time.sleep(min(2.0 ** attempt, 8.0))
        raise last_error or GatewayError("chat failed")

    # -- token measurement ---------------------------------------------

    def _empty_baseline(self, model):
        """prompt_tokens for an empty user message, per model, cached."""
        with self._baseline_lock:
            if model not in self._token_baseline:
                result = self.chat(
                    model,
                    [{"role": "user", "content": ""}],
                    max_tokens=1,
                    temperature=0.0,
                )
                self._token_baseline[model] = result["usage"]["prompt_tokens"]
            return self._token_baseline[model]

    def measure_tokens(self, model, text, retries=4):
        """Real tokenizer count of `text` minus the empty-prompt baseline."""
        result = self.chat(
            model,
            [{"role": "user", "content": text}],
            max_tokens=1,
            temperature=0.0,
            retries=retries,
        )
        baseline = self._empty_baseline(model)
        usage = result["usage"]
        return {
            "tokens": usage["prompt_tokens"] - baseline,
            "raw_prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "latency": result["latency"],
        }

    # -- concurrency ----------------------------------------------------

    def run_concurrent(self, fn, items, workers=8):
        """Map fn over items with a thread pool; exceptions are captured."""
        results = [None] * len(items)
        errors = [None] * len(items)

        def _run(index, item):
            try:
                results[index] = fn(item)
            except Exception as error:  # recorded, not raised
                errors[index] = "%s: %s" % (type(error).__name__, error)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_run, index, item) for index, item in enumerate(items)
            ]
            for future in futures:
                future.result()
        return results, errors
