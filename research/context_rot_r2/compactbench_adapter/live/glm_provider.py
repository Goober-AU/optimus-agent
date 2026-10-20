"""CUSTOM-OPTIMUS LIVE-MODEL task-model provider for CompactBench (W26).

Drives the benchmark's task-model calls (evaluation answers and drift-cycle
continuations) against the same live GLM 5.3 endpoint the Rust harness uses
for its summarizer. Registered in-process by ``run_live.py`` because the
shared CompactBench checkout is read-only for this worker (file-map rule);
the runner itself is the stock ``compactbench.runner.run.run_experiment``.

Live-model practice run ONLY: every artifact this provider helps produce
carries the CUSTOM-OPTIMUS + LIVE-MODEL labels and is not a leaderboard
number.

GLM 5.3 bills ``reasoning_content`` tokens against ``max_tokens`` and puts
the answer in ``content``, so a rung that runs out of budget ends with
``finish_reason: "length"`` and an empty answer. The provider walks a small
max_tokens ladder (requested -> 8192 -> 32768) and counts every HTTP request
(including ladder rungs and 429/5xx transport retries) against the budget
guard. Every request is logged as one JSON line to ``ROT2_TASK_CALL_LOG``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, ClassVar

import httpx

from compactbench.providers.base import CompletionRequest, CompletionResponse, Provider

_LADDER = (32768.0,)
_TRANSPORT_RETRIES = 3
_TRANSPORT_BACKOFF_S = (2.0, 4.0, 8.0)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"glm-live provider requires {name}")
    return value


class GlmLiveProvider(Provider):
    """OpenAI-compatible chat-completions client for the GLM 5.3 endpoint."""

    key: ClassVar[str] = "glm-live"

    def __init__(self) -> None:
        self._base_url = _require_env("ROT2_LIVE_BASE_URL").rstrip("/")
        self._api_key = _require_env("ROT2_LIVE_API_KEY")
        self._pomerium = _require_env("ROT2_LIVE_POMERIUM")
        self._model = os.environ.get("ROT2_LIVE_MODEL", "glm-5.3")
        self._timeout = float(os.environ.get("ROT2_LIVE_HTTP_TIMEOUT_S", "600"))
        self._log_path = os.environ.get("ROT2_TASK_CALL_LOG") or None
        self._arm = os.environ.get("ROT2_TASK_LOG_ARM", "unknown")
        self._max_requests = int(os.environ.get("ROT2_TASK_MAX_REQUESTS", "0") or 0)
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "X-Pomerium-Authorization": self._pomerium,
            },
        )
        self._lock = asyncio.Lock()
        self._requests = 0
        self._backoff_events = 0
        self._ladder_rungs = 0

    @property
    def endpoint_kind(self) -> str:
        return "custom"

    @staticmethod
    def _purpose(request: CompletionRequest) -> str:
        if request.cached_prefix:
            return "eval"
        if request.prompt.startswith("You have the following summary"):
            return "continuation"
        return "task"

    def _log(self, record: dict[str, Any]) -> None:
        if not self._log_path:
            return
        line = json.dumps(record, ensure_ascii=False)
        with open(self._log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def stats(self) -> dict[str, int]:
        return {
            "requests": self._requests,
            "backoff_events": self._backoff_events,
            "ladder_rungs": self._ladder_rungs,
        }

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        purpose = self._purpose(request)
        user = (request.cached_prefix or "") + request.prompt
        # GLM reasoning tokens bill against max_tokens; the runner's default
        # 2048 length-finished 128/167 times in the first live arm, so the
        # ladder starts at 8192 (the rung the Rust summarizer starts at).
        rungs: list[float] = [float(max(request.max_tokens, 8192))]
        for step in _LADDER:
            if step > rungs[0]:
                rungs.append(step)
        last_failure = "no attempt made"
        for rung_index, rung in enumerate(rungs):
            if rung_index > 0:
                self._ladder_rungs += 1
            retries = 0
            while True:
                async with self._lock:
                    self._requests += 1
                    if self._max_requests and self._requests > self._max_requests:
                        raise RuntimeError(
                            f"budget guard: arm {self._arm} exceeded "
                            f"{self._max_requests} task-model HTTP requests; aborting"
                        )
                started = time.perf_counter()
                messages: list[dict[str, str]] = []
                if request.system:
                    messages.append({"role": "system", "content": request.system})
                messages.append({"role": "user", "content": user})
                error: str | None = None
                response: httpx.Response | None = None
                try:
                    response = await self._client.post(
                        f"{self._base_url}/chat/completions",
                        json={
                            "model": self._model,
                            "messages": messages,
                            "temperature": request.temperature,
                            "max_tokens": int(rung),
                            "stream": False,
                        },
                    )
                except httpx.HTTPError as exc:
                    error = f"network error: {exc}"
                latency_ms = int((time.perf_counter() - started) * 1000)
                if response is not None and (
                    response.status_code == 429 or 500 <= response.status_code < 600
                ):
                    error = f"retryable status {response.status_code}"
                if error is not None:
                    self._log(
                        {
                            "tsMs": int(time.time() * 1000),
                            "purpose": purpose,
                            "arm": self._arm,
                            "model": self._model,
                            "maxTokens": int(rung),
                            "rungIndex": rung_index,
                            "latencyMs": latency_ms,
                            "transportRetries": retries,
                            "error": error,
                        }
                    )
                    if retries < _TRANSPORT_RETRIES:
                        retries += 1
                        self._backoff_events += 1
                        await asyncio.sleep(_TRANSPORT_BACKOFF_S[retries - 1])
                        continue
                    raise RuntimeError(
                        f"glm-live provider: {purpose} call failed after "
                        f"{retries} retries: {error}"
                    )
                assert response is not None
                body = response.json()
                choice = (body.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                content = message.get("content") or ""
                reasoning = message.get("reasoning_content") or ""
                finish = choice.get("finish_reason") or ""
                usage = body.get("usage") or {}
                self._log(
                    {
                        "tsMs": int(time.time() * 1000),
                        "purpose": purpose,
                        "arm": self._arm,
                        "model": self._model,
                        "maxTokens": int(rung),
                        "rungIndex": rung_index,
                        "httpStatus": response.status_code,
                        "latencyMs": latency_ms,
                        "transportRetries": retries,
                        "finishReason": finish,
                        "contentChars": len(content),
                        "reasoningChars": len(reasoning),
                        "promptTokens": usage.get("prompt_tokens"),
                        "completionTokens": usage.get("completion_tokens"),
                        "reasoningTokens": usage.get("reasoning_tokens"),
                    }
                )
                if finish == "stop" and content.strip():
                    return CompletionResponse(
                        text=content,
                        prompt_tokens=int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0),
                        model=str(body.get("model") or self._model),
                        raw={
                            "provider": "glm-live",
                            "finish_reason": finish,
                            "rung_index": rung_index,
                            "max_tokens": int(rung),
                        },
                    )
                last_failure = (
                    f"finish {finish!r} with {len(content.strip())} content chars "
                    f"at max_tokens {int(rung)}"
                )
                if finish == "length" or not content.strip():
                    # Advance to the next rung: `break` leaves the transport
                    # retry loop, and the for-loop takes the bigger budget.
                    # (`continue` here would re-roll the SAME rung, which is
                    # what burned the first arm's request budget.)
                    break
                raise RuntimeError(
                    f"glm-live provider: {purpose} call failed: finish {finish!r}"
                )
        raise RuntimeError(f"glm-live provider: {purpose} call failed: {last_failure}")
