"""Model adapters.

* :class:`ModelAdapter` - the protocol the harness uses.
* :class:`OpenAICompatAdapter` - real endpoint driver (``/chat/completions``).
* :class:`OfflineEchoAdapter` - wiring probe, always says ABSTAIN.
* :class:`FakeAdapter` - deterministic scripted responder for offline tests.

The fake adapter is not a model. It looks the answer up in a table supplied by
the caller and injects deterministic errors, so offline runs only validate the
harness plumbing, never model quality.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple, runtime_checkable

from .corpus import estimate_messages_tokens, estimate_tokens


class AdapterError(RuntimeError):
    """Transport or protocol failure while talking to a model endpoint."""

    def __init__(self, message: str, status: Optional[int] = None,
                 body: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class AdapterResponse:
    text: str
    usage: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    model: str = ""
    provider: str = ""
    synthetic: bool = False      # True when produced offline (fake/echo)
    raw: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "usage": dict(self.usage),
            "latency_ms": round(float(self.latency_ms), 3),
            "model": self.model,
            "provider": self.provider,
            "synthetic": bool(self.synthetic),
        }


@runtime_checkable
class ModelAdapter(Protocol):
    name: str
    provider: str

    def complete(self, messages: List[dict], json_style: bool = False,
                 temperature: float = 0.0, max_tokens: Optional[int] = None) -> AdapterResponse:
        ...


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint
# ---------------------------------------------------------------------------

class OpenAICompatAdapter:
    """Drive any OpenAI-compatible ``/chat/completions`` endpoint.

    ``base_url`` may include a version prefix, e.g.
    ``http://127.0.0.1:43120/v1``. The adapter appends ``/chat/completions``.
    ``extra_headers`` supports gateways that need routing or auth headers.
    A ``session`` with a ``post`` method can be injected (used by offline tests).
    """

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None,
                 extra_headers: Optional[Mapping[str, str]] = None,
                 timeout: float = 120.0, provider: str = "openai-compat",
                 session: Any = None, name: Optional[str] = None):
        if not base_url:
            raise ValueError("base_url is required")
        if not model:
            raise ValueError("model is required")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.extra_headers = dict(extra_headers or {})
        self.timeout = float(timeout)
        self.provider = provider
        self.name = name or "openai-compat:%s" % model
        self._session = session

    @property
    def endpoint(self) -> str:
        return self.base_url + "/chat/completions"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key
        headers.update(self.extra_headers)
        return headers

    def build_request(self, messages: List[dict], json_style: bool = False,
                      temperature: float = 0.0,
                      max_tokens: Optional[int] = None) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": [dict(m) for m in messages],
            "temperature": float(temperature),
            "stream": False,
        }
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        if json_style:
            body["response_format"] = {"type": "json_object"}
        return self.endpoint, self._headers(), body

    def _post(self, url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Any:
        if self._session is not None:
            return self._session.post(url, headers=headers, json=body, timeout=self.timeout)
        import requests  # imported lazily so offline tests never need network

        return requests.post(url, headers=headers, json=body, timeout=self.timeout)

    def complete(self, messages: List[dict], json_style: bool = False,
                 temperature: float = 0.0,
                 max_tokens: Optional[int] = None) -> AdapterResponse:
        url, headers, body = self.build_request(messages, json_style, temperature, max_tokens)
        started = time.perf_counter()
        try:
            response = self._post(url, headers, body)
        except Exception as exc:  # requests transport errors and injected doubles
            raise AdapterError("request failed: %s" % (exc,)) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        status = getattr(response, "status_code", None)
        text_body = getattr(response, "text", "")
        if status is not None and status >= 400:
            raise AdapterError("HTTP %s from %s" % (status, url), status=status, body=text_body)
        try:
            payload = response.json()
        except Exception as exc:
            raise AdapterError("invalid JSON response", status=status, body=text_body) from exc
        try:
            choice = payload["choices"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterError("response has no choices", status=status, body=text_body) from exc
        message = choice.get("message") if isinstance(choice, dict) else None
        content = ""
        if isinstance(message, dict):
            content = message.get("content") or ""
            if not content and message.get("reasoning_content"):
                content = ""
        if not content and isinstance(choice, dict):
            content = choice.get("text") or ""
        usage = payload.get("usage") or {}
        return AdapterResponse(
            text=str(content),
            usage={
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "source": "api",
            },
            latency_ms=latency_ms,
            model=str(payload.get("model") or self.model),
            provider=self.provider,
            synthetic=False,
            raw={"finish_reason": (choice or {}).get("finish_reason") if isinstance(choice, dict) else None},
        )


# ---------------------------------------------------------------------------
# Offline adapters
# ---------------------------------------------------------------------------

class OfflineEchoAdapter:
    """Minimal wiring probe: never answers. Always returns ABSTAIN."""

    name = "offline-echo"
    provider = "offline"

    def __init__(self) -> None:
        self.calls: List[dict] = []

    def complete(self, messages: List[dict], json_style: bool = False,
                 temperature: float = 0.0,
                 max_tokens: Optional[int] = None) -> AdapterResponse:
        self.calls.append({"messages": messages, "json_style": json_style})
        question = _find_question(messages)
        answer = "ABSTAIN" if not json_style else json.dumps({"answer": "ABSTAIN"})
        text = "Offline echo adapter. Question length: %d.\n%s" % (len(question), answer)
        return AdapterResponse(
            text=text,
            usage={"prompt_tokens": estimate_messages_tokens(messages),
                   "completion_tokens": estimate_tokens(text),
                   "total_tokens": estimate_messages_tokens(messages) + estimate_tokens(text),
                   "source": "estimate"},
            latency_ms=0.0,
            model=self.name,
            provider=self.provider,
            synthetic=True,
        )


_QUESTION_MARKER = "QUESTION:\n"


def _find_question(messages: List[dict]) -> str:
    """Recover the question text from the rendered prompt (no gold involved)."""
    for message in reversed(messages):
        content = str(message.get("content", ""))
        if _QUESTION_MARKER in content:
            tail = content.split(_QUESTION_MARKER, 1)[1]
            return tail.split("\n\n", 1)[0].strip()
    return ""


def _stable_unit(*parts: Any) -> float:
    """Deterministic float in [0, 1) from the given parts."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _wrong_answer(answer: str, answer_kind: str, salt: str) -> str:
    """Deterministic plausible wrong answer, used to model failures."""
    if answer_kind == "numeric":
        try:
            return str(int(answer) + 2)
        except ValueError:
            return "0"
    if answer_kind == "time":
        hh, mm = answer.split(":")
        return "%02d:%02d" % (int(hh), (int(mm) + 13) % 60)
    if answer_kind == "multi":
        return ", ".join(part.split("-")[0] if "-" in part else part for part in answer.split("|"))
    return answer[:-1] + "x" if answer else "unknown"


class FakeAdapter:
    """Deterministic scripted responder for offline tests and selftest.

    ``answers_by_question`` maps the exact question text to the gold answer and
    answer kind. Accuracy follows an explicit, documented model:

        p_correct = base_accuracy - length_penalty_per_1k * (prompt_tokens / 1000)

    clamped to [0, 1], evaluated with a hash of (seed, question) so repeated
    runs are identical. Output style is varied on purpose (reasoning preamble,
    delimiter, bare last line, JSON) so extraction rules get exercised.
    """

    provider = "fake"

    def __init__(self, answers_by_question: Mapping[str, Tuple[str, str]],
                 seed: int = 0, base_accuracy: float = 1.0,
                 length_penalty_per_1k: float = 0.0, name: str = "fake-adapter"):
        self.answers = dict(answers_by_question)
        self.seed = int(seed)
        self.base_accuracy = float(base_accuracy)
        self.length_penalty_per_1k = float(length_penalty_per_1k)
        self.name = name
        self.calls = 0

    def _lookup(self, question: str) -> Optional[Tuple[str, str]]:
        if question in self.answers:
            return self.answers[question]
        return None

    def complete(self, messages: List[dict], json_style: bool = False,
                 temperature: float = 0.0,
                 max_tokens: Optional[int] = None) -> AdapterResponse:
        started = time.perf_counter()
        self.calls += 1
        question = _find_question(messages)
        prompt_tokens = estimate_messages_tokens(messages)
        found = self._lookup(question)
        if found is None:
            text = self._render("ABSTAIN", json_style, style=0, reasoning=False)
        else:
            answer, answer_kind = found
            p_correct = self.base_accuracy - self.length_penalty_per_1k * (prompt_tokens / 1000.0)
            p_correct = min(1.0, max(0.0, p_correct))
            # Draw per cell, not per question: the same question at a different
            # context length or position can land on a different outcome.
            draw = _stable_unit("cell", self.seed, question, prompt_tokens, json_style)
            style = int(_stable_unit("style", self.seed, question, prompt_tokens) * 4)
            if draw < p_correct:
                value = answer
            elif _stable_unit("abstain", self.seed, question) < 0.35:
                value = "ABSTAIN"
            else:
                value = _wrong_answer(answer, answer_kind, question)
            text = self._render(value, json_style, style=style, reasoning=style >= 2)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return AdapterResponse(
            text=text,
            usage={"prompt_tokens": prompt_tokens,
                   "completion_tokens": estimate_tokens(text),
                   "total_tokens": prompt_tokens + estimate_tokens(text),
                   "source": "estimate"},
            latency_ms=latency_ms,
            model=self.name,
            provider=self.provider,
            synthetic=True,
        )

    def _render(self, value: str, json_style: bool, style: int, reasoning: bool) -> str:
        if json_style:
            body = json.dumps({"answer": value})
            if reasoning:
                return "Working through the context first.\n" + body
            return body
        if style == 0:
            return value
        if style == 1:
            return "FINAL ANSWER: %s" % value
        if style == 2:
            return ("Let me think. I scanned the context for the relevant note and "
                    "checked the stated rule.\n%s" % value)
        return ("Thinking process:\n1. locate the note\n2. apply the rule\n"
                "FINAL ANSWER: %s" % value)
