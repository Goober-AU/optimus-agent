#!/usr/bin/env python3
"""Benchmark harness for OCC-1 / OCC-2 / OCC-3 agent communication.

Arms
----
text
    Plain-text baseline. The sender message is delivered verbatim and the
    receiver prefills it together with the task through POST /generate.
occ1
    OCC-1 compact structured English codec (optimus.agent-comm.v1; kernel
    codec rlm.occ1). The receiver prefills the delivered envelope through
    POST /generate.
occ2
    Local stand-in for the Optimus occ2::encode_dual seam: a dual symbolic
    envelope (compact adaptive symbolic header plus verbatim body). The
    receiver prefills the delivered envelope through POST /generate.
occ3
    Latent communication. The sender writes message KV into a salted
    radix-cache namespace (one prefill) via POST /occ3/v1/send; the
    receiver reads it zero-copy as a prefix hit (verified by cached_tokens
    covering the carrier) via POST /occ3/v1/recv and prefills only the
    task suffix. Same-exact-model only.

Measured dimensions (see README.md in this directory)
----------------------------------------------------
a) wire_chars                  characters in the sender's outbound payload.
b) send_ms / recv_ms / e2e_ms  send-to-recv latency.
c) receiver_prefill_tokens     tokens the receiver actually prefilled
                               (prompt_tokens - cached_tokens).
d) success / failed_checks     semantic fidelity on asymmetric tasks.
e) hit / cache_miss_rate       OCC-3 prefix-hit stability.

The API key is read from the environment only (OCC3_API_KEY or
SGLANG_API_KEY; override the primary name with --api-key-env). It is
never written to files and never printed.

Dependencies: Python 3.11+ and either httpx or requests.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
import time
import uuid
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

try:  # httpx is preferred; requests is the fallback; neither is needed for --selftest.
    import httpx as _httpx
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore[assignment]

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]

ARM_CHOICES = ("text", "occ1", "occ2", "occ3")
DEFAULT_TASKS_FILE = Path(__file__).resolve().parent / "tasks_asymmetric.json"
NUMERIC_METRICS = (
    "wire_chars",
    "encode_ms",
    "send_ms",
    "recv_ms",
    "e2e_ms",
    "prompt_tokens",
    "cached_tokens",
    "completion_tokens",
    "receiver_prefill_tokens",
    "carrier_tokens",
)
# OCC-3 plugin decode cap (occ3_sglang/security.py MAX_RECV_NEW_TOKENS).
OCC3_PLUGIN_MAX_RECV_NEW_TOKENS = 1024


class BenchmarkError(RuntimeError):
    """Fatal harness error (bad input, missing dependency, missing key)."""


class TransportError(RuntimeError):
    """The endpoint could not be reached or answered unusably."""


class HttpError(TransportError):
    """The endpoint was reached but returned an HTTP error status."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], pct: float) -> float:
    """Return the linearly interpolated percentile (numpy default method)."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    frac = rank - lower
    return ordered[lower] * (1.0 - frac) + ordered[upper] * frac


def failed_checks(output_text: str, eval_checks: Sequence[str]) -> list[str]:
    """Return the eval substrings missing from output_text (case-insensitive)."""
    lowered = (output_text or "").lower()
    return [check for check in eval_checks if check.lower() not in lowered]


def _as_int(*candidates: Any) -> int | None:
    """Return the first non-bool integer-ish candidate, else None."""
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _clean_body(body: str) -> str:
    """Collapse whitespace so values stay single-line (OCC-1 forbids newlines)."""
    return " ".join(body.split())


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """One asymmetric-information benchmark task.

    The receiver can only answer from the sender message; eval_checks are
    case-insensitive substrings that must appear in the receiver output.
    """

    task_id: str
    sender_msg: str
    receiver_task: str
    eval_checks: tuple[str, ...]
    domain: str = "general"


def load_tasks(path: Path) -> list[Task]:
    """Load tasks from a JSON list of {sender_msg, receiver_task, eval_checks}.

    Optional per-task keys: id (defaults to task_NN) and domain. Raises
    ValueError or OSError on bad input; never returns partially valid tasks.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("tasks file must contain a JSON list of task objects")
    tasks: list[Task] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"task #{index} is not an object")
        missing = [
            key for key in ("sender_msg", "receiver_task", "eval_checks") if key not in item
        ]
        if missing:
            raise ValueError(f"task #{index} is missing required field(s): {', '.join(missing)}")
        sender = str(item["sender_msg"])
        receiver = str(item["receiver_task"])
        checks = [str(check) for check in item["eval_checks"]]
        if not sender or not receiver or not checks:
            raise ValueError(f"task #{index} has an empty required field")
        tasks.append(
            Task(
                task_id=str(item.get("id") or f"task_{index:02d}"),
                sender_msg=sender,
                receiver_task=receiver,
                eval_checks=tuple(checks),
                domain=str(item.get("domain") or "general"),
            )
        )
    return tasks


# ---------------------------------------------------------------------------
# Codecs: OCC-1 mirror, OCC-2 stand-in
# ---------------------------------------------------------------------------

# Lazily resolved kernel codec: False = not tried yet, None = unavailable,
# otherwise the imported rlm.occ1 module.
_REAL_OCC1: Any = False


def _real_occ1_codec() -> Any:
    """Return the kernel rlm.occ1 module when importable, else None."""
    global _REAL_OCC1
    if _REAL_OCC1 is False:
        try:
            from rlm import occ1 as codec

            _REAL_OCC1 = codec if hasattr(codec, "Occ1Message") else None
        except Exception:
            _REAL_OCC1 = None
    return _REAL_OCC1


def encode_occ1(task: Task) -> str:
    """Encode the sender message as an OCC-1 envelope (optimus.agent-comm.v1).

    Grammar: line 1 is exactly OCC/1; every following line is KEY=VALUE with
    KEY matching [A-Z][A-Z0-9_]*; repeated keys keep order; values are UTF-8
    without newlines or control characters. Uses the kernel rlm.occ1 codec
    when importable and a grammar-identical local mirror otherwise. Sender
    identity is daemon-derived in production and deliberately absent.
    """
    body = _clean_body(task.sender_msg)
    codec = _real_occ1_codec()
    if codec is not None:
        message = codec.Occ1Message()
        message.push("TASK", task.task_id)
        message.push("KIND", "TASK")
        message.push("STATUS", "pending")
        message.push("F1", body)
        try:
            return str(message.encode())
        except Exception:
            pass  # fall back to the local mirror below
    chunks = [body[i : i + 4000] for i in range(0, len(body), 4000)] or [""]
    lines = ["OCC/1", f"TASK={task.task_id}", "KIND=TASK", "STATUS=pending"]
    for index, chunk in enumerate(chunks, start=1):
        lines.append(f"F{index}={chunk}")
    return "\n".join(lines)


def encode_occ2(task: Task) -> str:
    """Encode the sender message with a local stand-in for occ2::encode_dual.

    The production seam lives inside the Optimus native transport and is not
    importable from a standalone script. This stand-in keeps the documented
    dual character of OCC-2: a compact adaptive symbolic header (source,
    destination, type, size and checksum tags) followed by the verbatim
    message body, so the wire stays decodable as text. It measures envelope
    overhead, not production adaptive compression.
    """
    body = _clean_body(task.sender_msg)
    digest = format(zlib.crc32(body.encode("utf-8")) & 0xFFFFFFFF, "08x")
    lines = [
        "OCC2/1",
        f"@src=bench-sender @dst=bench-receiver @typ=TASK @ref={task.task_id}",
        f"@sym words={len(body.split())} chars={len(body)} sum={digest} book=shared-v1",
        "@txt",
        body,
    ]
    return "\n".join(lines)


def encode_message(arm: str, task: Task) -> str:
    """Return the sender's delivered payload for one arm."""
    if arm == "text":
        return task.sender_msg
    if arm == "occ1":
        return encode_occ1(task)
    if arm == "occ2":
        return encode_occ2(task)
    if arm == "occ3":
        return task.sender_msg  # delivered as latent KV, not as text
    raise BenchmarkError(f"unknown arm: {arm!r}")


def make_channel_id(task: Task, trial_idx: int) -> str:
    """Build a plugin-safe channel id matching [A-Za-z0-9._-]{1,64}."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", task.task_id)[:32]
    return f"bench-{safe}-{trial_idx:03d}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# HTTP transport and clients
# ---------------------------------------------------------------------------


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    """Issue one JSON HTTP request via httpx or requests.

    Raises TransportError on connection failure or a non-JSON body (with a
    clear, actionable message), HttpError on HTTP error status, and
    BenchmarkError when no HTTP library is installed.
    """
    if _httpx is None and _requests is None:
        raise BenchmarkError("no HTTP client available: install httpx or requests")
    try:
        if _httpx is not None:
            response = _httpx.request(method, url, json=payload, headers=headers, timeout=timeout)
        else:
            response = _requests.request(method, url, json=payload, headers=headers, timeout=timeout)
    except Exception as exc:
        raise TransportError(
            f"cannot reach {url} ({type(exc).__name__}: {exc}). Check --endpoint, "
            "confirm the server is running, and check network or firewall reachability."
        ) from exc
    if response.status_code in (401, 403):
        raise HttpError(
            f"{url} returned HTTP {response.status_code} (authentication failed): the API "
            "key is missing or rejected. Set OCC3_API_KEY or SGLANG_API_KEY in the "
            "environment; the key is never printed."
        )
    if response.status_code >= 400:
        try:
            detail = str(response.json())[:200]
        except Exception:
            detail = str(getattr(response, "text", "") or "")[:200]
        raise HttpError(f"{url} returned HTTP {response.status_code}: {detail}")
    try:
        return response.json()
    except Exception as exc:
        raise TransportError(
            f"{url} returned a non-JSON body ({type(exc).__name__})."
        ) from exc


@dataclass
class GenerationResult:
    """Normalized reply from one receiver-side generation call."""

    text: str
    prompt_tokens: int | None
    cached_tokens: int | None
    completion_tokens: int | None


class SglangBenchClient:
    """HTTP client for the SGLang server and its OCC-3 plugin routes.

    The API key is kept in memory only; it is never printed or written.
    """

    def __init__(self, endpoint: str, api_key: str | None, timeout: float = 180.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._flush_failed = False

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.endpoint}{path}"

    def model_info(self) -> dict[str, Any]:
        """GET /get_model_info; raises with detail when unreachable."""
        return _request_json(
            "GET",
            self._url("/get_model_info"),
            headers=self._headers(),
            payload=None,
            timeout=min(self.timeout, 30.0),
        )

    def probe(self) -> bool:
        """Return True when the server answers with any HTTP status."""
        try:
            self.model_info()
            return True
        except HttpError:
            return True  # reachable; the route itself is optional
        except TransportError:
            return False

    def generate(
        self,
        prompt: str,
        *,
        max_new_tokens: int,
        temperature: float,
        seed: int | None = None,
    ) -> GenerationResult:
        """Run one plain POST /generate prefill plus decode (receiver side of text arms)."""
        sampling: dict[str, Any] = {"temperature": temperature, "max_new_tokens": max_new_tokens}
        if seed is not None:
            sampling["seed"] = seed
        body = _request_json(
            "POST",
            self._url("/generate"),
            headers=self._headers(),
            payload={"text": prompt, "sampling_params": sampling},
            timeout=self.timeout,
        )
        meta = body.get("meta_info") if isinstance(body.get("meta_info"), dict) else {}
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        details = usage.get("prompt_tokens_details")
        cached_details = details.get("cached_tokens") if isinstance(details, dict) else None
        text = body.get("text")
        if isinstance(text, list):
            text = "".join(str(part) for part in text)
        return GenerationResult(
            text="" if text is None else str(text),
            prompt_tokens=_as_int(meta.get("prompt_tokens"), usage.get("prompt_tokens")),
            cached_tokens=_as_int(meta.get("cached_tokens"), cached_details),
            completion_tokens=_as_int(meta.get("completion_tokens"), usage.get("completion_tokens")),
        )

    def occ3_status(self) -> dict[str, Any]:
        """GET /occ3/v1/status: plugin version, identity and metrics."""
        return _request_json(
            "GET",
            self._url("/occ3/v1/status"),
            headers=self._headers(),
            payload=None,
            timeout=self.timeout,
        )

    def occ3_capabilities(self) -> dict[str, Any]:
        """GET /occ3/v1/capabilities: capability advertisement."""
        return _request_json(
            "GET",
            self._url("/occ3/v1/capabilities"),
            headers=self._headers(),
            payload=None,
            timeout=self.timeout,
        )

    def occ3_send(self, channel: str, text: str, *, ttl_s: float | None = None) -> dict[str, Any]:
        """POST /occ3/v1/send: write one message KV into a salted radix-cache namespace."""
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if ttl_s is not None:
            payload["ttl_s"] = ttl_s
        return _request_json(
            "POST",
            self._url("/occ3/v1/send"),
            headers=self._headers(),
            payload=payload,
            timeout=self.timeout,
        )

    def occ3_recv(
        self, channel: str, suffix: str, *, max_new_tokens: int, temperature: float
    ) -> dict[str, Any]:
        """POST /occ3/v1/recv: read the channel as a zero-copy prefix hit and decode."""
        payload = {
            "channel": channel,
            "suffix": suffix,
            "max_new_tokens": max_new_tokens,
            "temperature": temperature,
        }
        return _request_json(
            "POST",
            self._url("/occ3/v1/recv"),
            headers=self._headers(),
            payload=payload,
            timeout=self.timeout,
        )

    def occ3_release(self, channel: str) -> None:
        """POST /occ3/v1/release: drop the channel (best effort)."""
        _request_json(
            "POST",
            self._url("/occ3/v1/release"),
            headers=self._headers(),
            payload={"channel": channel},
            timeout=self.timeout,
        )

    def flush_cache(self) -> bool:
        """POST /flush_cache so the next prefill is cold; warns once if unavailable."""
        if self._flush_failed:
            return False
        try:
            _request_json(
                "POST",
                self._url("/flush_cache"),
                headers=self._headers(),
                payload={},
                timeout=self.timeout,
            )
            return True
        except TransportError as exc:
            self._flush_failed = True
            print(
                f"WARNING: radix-cache flush unavailable ({exc}); receiver_prefill_tokens "
                "for later trials may include prefix hits from earlier trials. Use --no-flush "
                "to silence this.",
                file=sys.stderr,
            )
            return False

    def fill(self, n: int, *, max_new_tokens: int = 1) -> None:
        """Issue n filler generations to create radix-cache eviction pressure."""
        for index in range(max(0, int(n))):
            filler = (
                f"Radix cache pressure filler {index}. "
                + "filler sentence about nothing in particular. " * 24
            )
            self.generate(filler, max_new_tokens=max_new_tokens, temperature=0.0)


# ---------------------------------------------------------------------------
# Benchmark core
# ---------------------------------------------------------------------------


@dataclass
class RunSettings:
    """Immutable per-run benchmark settings.

    seed only applies to the text-arm sampling params; the OCC-3 recv route
    has no seed parameter in the plugin API.
    """

    arm: str
    temperature: float
    max_new_tokens: int
    seed: int
    flush_cache: bool
    eviction_fill: int
    ttl_s: float | None


@dataclass
class TrialRecord:
    """One measured send-to-recv trial."""

    task_id: str
    domain: str
    arm: str
    trial: int
    wire_chars: int
    encode_ms: float
    send_ms: float
    recv_ms: float
    e2e_ms: float
    prompt_tokens: int | None
    cached_tokens: int | None
    completion_tokens: int | None
    receiver_prefill_tokens: int | None
    carrier_tokens: int | None
    hit: bool | None
    success: bool
    failed_checks: list[str]
    error: str | None


def run_trial(
    settings: RunSettings,
    task: Task,
    trial_idx: int,
    client: SglangBenchClient | StubBenchClient,
) -> TrialRecord:
    """Run and measure one send-to-recv trial.

    occ3: optional flush, send (one carrier prefill into the salted
    radix-cache namespace), optional eviction filler traffic, recv (prefix
    hit plus suffix prefill plus decode), release. text/occ1/occ2: optional
    flush, encode, then one /generate prefill of the delivered envelope
    plus the receiver task. The optional radix-cache flush and the channel
    release stay outside the measured window so they never pollute e2e.
    """
    if settings.flush_cache:
        client.flush_cache()

    started = time.perf_counter()
    encode_ms = 0.0
    send_ms = 0.0
    recv_ms = 0.0
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    completion_tokens: int | None = None
    carrier_tokens: int | None = None
    hit: bool | None = None
    output_text = ""
    error: str | None = None
    delivered = task.sender_msg
    channel = make_channel_id(task, trial_idx)

    try:
        if settings.arm == "occ3":
            t_send = time.perf_counter()
            send_resp = client.occ3_send(channel, task.sender_msg, ttl_s=settings.ttl_s)
            send_ms = (time.perf_counter() - t_send) * 1000.0
            carrier_tokens = _as_int(send_resp.get("carrier_tokens"))
            if settings.eviction_fill > 0:
                client.fill(settings.eviction_fill)
            t_recv = time.perf_counter()
            recv_resp = client.occ3_recv(
                channel,
                task.receiver_task,
                max_new_tokens=settings.max_new_tokens,
                temperature=settings.temperature,
            )
            recv_ms = (time.perf_counter() - t_recv) * 1000.0
            output_text = str(recv_resp.get("output_text") or "")
            cached_tokens = _as_int(recv_resp.get("cached_tokens"))
            prompt_tokens = _as_int(recv_resp.get("prompt_tokens"))
            completion_tokens = _as_int(recv_resp.get("completion_tokens"))
            output_ids = recv_resp.get("output_ids")
            if completion_tokens is None and isinstance(output_ids, list):
                completion_tokens = len(output_ids)
            hit_value = recv_resp.get("hit")
            hit = bool(hit_value) if hit_value is not None else None
        else:
            t_encode = time.perf_counter()
            delivered = encode_message(settings.arm, task)
            encode_ms = (time.perf_counter() - t_encode) * 1000.0
            send_ms = encode_ms  # local codec delivery; no network send
            prompt = f"{delivered}\n\n{task.receiver_task}"
            t_recv = time.perf_counter()
            generation = client.generate(
                prompt,
                max_new_tokens=settings.max_new_tokens,
                temperature=settings.temperature,
                seed=settings.seed,
            )
            recv_ms = (time.perf_counter() - t_recv) * 1000.0
            output_text = generation.text
            prompt_tokens = generation.prompt_tokens
            cached_tokens = generation.cached_tokens
            completion_tokens = generation.completion_tokens
    except Exception as exc:  # per-trial resilience: record the error and continue
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if settings.arm == "occ3":
            try:
                client.occ3_release(channel)
            except Exception:
                pass  # best effort; the channel TTL bounds its lifetime anyway

    e2e_ms = (time.perf_counter() - started) * 1000.0
    receiver_prefill: int | None = None
    if prompt_tokens is not None and cached_tokens is not None:
        receiver_prefill = max(0, prompt_tokens - cached_tokens)
    misses = (
        list(task.eval_checks)
        if error is not None
        else failed_checks(output_text, task.eval_checks)
    )
    return TrialRecord(
        task_id=task.task_id,
        domain=task.domain,
        arm=settings.arm,
        trial=trial_idx,
        wire_chars=len(delivered),
        encode_ms=round(encode_ms, 6),
        send_ms=round(send_ms, 6),
        recv_ms=round(recv_ms, 6),
        e2e_ms=round(e2e_ms, 6),
        prompt_tokens=prompt_tokens,
        cached_tokens=cached_tokens,
        completion_tokens=completion_tokens,
        receiver_prefill_tokens=receiver_prefill,
        carrier_tokens=carrier_tokens,
        hit=hit,
        success=error is None and not misses,
        failed_checks=misses,
        error=error,
    )


def aggregate_records(records: Sequence[TrialRecord]) -> dict[str, Any]:
    """Aggregate trial records into medians, p10/p90 and rates."""
    summary: dict[str, Any] = {
        "n_trials": len(records),
        "n_errors": sum(1 for record in records if record.error),
    }
    for metric in NUMERIC_METRICS:
        values: list[float] = []
        for record in records:
            value = getattr(record, metric)
            if value is not None:
                values.append(float(value))
        if values:
            summary[metric] = {
                "median": percentile(values, 50),
                "p10": percentile(values, 10),
                "p90": percentile(values, 90),
                "mean": statistics.fmean(values),
                "min": min(values),
                "max": max(values),
                "count": len(values),
            }
    if records:
        summary["success_rate"] = sum(1 for record in records if record.success) / len(records)
        occ3_records = [record for record in records if record.arm == "occ3"]
        if occ3_records:
            hits = sum(1 for record in occ3_records if record.hit is True)
            summary["hit_rate"] = hits / len(occ3_records)
            summary["cache_miss_rate"] = 1.0 - summary["hit_rate"]
    return summary


def build_result(
    args: argparse.Namespace,
    tasks: Sequence[Task],
    records: Sequence[TrialRecord],
    *,
    key_env: str | None,
    selftest: bool = False,
) -> dict[str, Any]:
    """Assemble the results.json document (no secrets; env var name only)."""
    return {
        "schema": "occ-bench-results/1",
        "meta": {
            "arm": "selftest" if selftest else args.arm,
            "endpoint": args.endpoint,
            "tasks_file": str(args.tasks_file),
            "trials_per_task": args.trials,
            "temperature": args.temperature,
            "max_new_tokens": args.max_new_tokens,
            "seed": args.seed,
            "eviction_fill": args.eviction_fill,
            "flush_cache": not args.no_flush,
            "ttl_s": args.ttl_s,
            "api_key_env": key_env,
            "selftest": selftest,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "tasks": [
            {"id": task.task_id, "domain": task.domain, "eval_checks": list(task.eval_checks)}
            for task in tasks
        ],
        "aggregate": aggregate_records(records),
        "trials": [asdict(record) for record in records],
    }


# ---------------------------------------------------------------------------
# Stub client (offline selftest)
# ---------------------------------------------------------------------------


class StubBenchClient:
    """Deterministic in-process stub of the bench client for --selftest.

    Implements the same method surface as SglangBenchClient. The stub
    answers a receiver prompt with a canned sentence containing the task's
    eval checks whenever the delivered message is present in the prompt,
    and with a refusal sentence otherwise. occ3 recv misses stochastically
    once filler traffic has been issued, which exercises the eviction-miss
    path. No GPU, no server, no network.
    """

    _REFUSAL = "I cannot determine that from the provided message."

    def __init__(
        self, tasks: Sequence[Task], seed: int = 7, miss_probability: float = 0.5
    ) -> None:
        self._tasks = list(tasks)
        self._answers = {task.sender_msg: self._answer_for(task) for task in self._tasks}
        self._rng = random.Random(seed)
        self._miss_probability = miss_probability
        self._channels: dict[str, str] = {}
        self._fill_calls = 0

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate (about four characters per token)."""
        return max(1, len(text) // 4)

    @classmethod
    def _answer_for(cls, task: Task) -> str:
        if task.domain == "forced_refusal":
            return cls._REFUSAL
        joined = ", ".join(task.eval_checks)
        return f"Based on the message above: {joined}."

    def _lookup(self, prompt: str) -> str:
        for task in self._tasks:
            body = task.sender_msg
            if body and (body in prompt or _clean_body(body) in prompt):
                return self._answers[body]
        return self._REFUSAL

    def model_info(self) -> dict[str, Any]:
        return {"model_path": "stub-model", "backend": "stub"}

    def probe(self) -> bool:
        return True

    def generate(
        self,
        prompt: str,
        *,
        max_new_tokens: int,
        temperature: float,
        seed: int | None = None,
    ) -> GenerationResult:
        answer = self._lookup(prompt)
        return GenerationResult(
            text=answer,
            prompt_tokens=self._estimate_tokens(prompt),
            cached_tokens=0,
            completion_tokens=self._estimate_tokens(answer),
        )

    def occ3_status(self) -> dict[str, Any]:
        return {"version": "stub", "identity": "stub", "metrics": {}}

    def occ3_capabilities(self) -> dict[str, Any]:
        return {"stub": True}

    def occ3_send(self, channel: str, text: str, *, ttl_s: float | None = None) -> dict[str, Any]:
        self._channels[channel] = text
        return {
            "channel": channel,
            "salt": uuid.uuid4().hex,
            "carrier_tokens": self._estimate_tokens(text),
            "cached_tokens": 0,
        }

    def occ3_recv(
        self, channel: str, suffix: str, *, max_new_tokens: int, temperature: float
    ) -> dict[str, Any]:
        carrier = self._channels.get(channel)
        hit = carrier is not None
        if hit and self._fill_calls > 0 and self._rng.random() < self._miss_probability:
            hit = False
        carrier_tokens = self._estimate_tokens(carrier) if carrier is not None else 0
        if hit:
            answer = self._lookup(carrier or "")
            cached = carrier_tokens
        else:
            answer = self._REFUSAL
            cached = 0
        return {
            "channel": channel,
            "hit": hit,
            "output_text": answer,
            "output_ids": [1] * self._estimate_tokens(answer),
            "cached_tokens": cached,
            "carrier_tokens": carrier_tokens,
            "prompt_tokens": carrier_tokens + self._estimate_tokens(suffix),
            "completion_tokens": self._estimate_tokens(answer),
        }

    def occ3_release(self, channel: str) -> None:
        self._channels.pop(channel, None)

    def flush_cache(self) -> bool:
        return True

    def fill(self, n: int, *, max_new_tokens: int = 1) -> None:
        self._fill_calls += max(0, int(n))


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------


def _builtin_selftest_tasks() -> list[Task]:
    """Two minimal tasks used when the tasks file is unavailable."""
    return [
        Task(
            task_id="t_builtin_1",
            sender_msg="The pickup gate is 12 and the crate weighs 41 kg.",
            receiver_task="Using only the message above, state the gate and the weight.",
            eval_checks=("12", "41"),
            domain="logistics",
        ),
        Task(
            task_id="t_builtin_2",
            sender_msg="The sync moved to Thursday 10:30 and now lasts 45 minutes.",
            receiver_task="Using only the message above, state the new day, time, and length.",
            eval_checks=("thursday", "10:30", "45"),
            domain="scheduling",
        ),
    ]


def run_selftest(args: argparse.Namespace) -> int:
    """Validate the harness offline with a stub client; no GPU, no server."""
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        print(("ok   " if condition else "FAIL ") + label)
        if not condition:
            failures.append(label)

    # 1) Pure helpers.
    check(abs(percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) - 3.0) < 1e-9, "percentile median of [1..5]")
    check(abs(percentile([1.0, 2.0, 3.0, 4.0], 10) - 1.3) < 1e-9, "percentile p10 interpolation")
    check(percentile([7.0], 90) == 7.0, "percentile of singleton")
    check(failed_checks("The answer is Matte Black, cap 9000.", ["matte black", "9000"]) == [], "fidelity: case-insensitive hits")
    check(failed_checks("I do not know.", ["matte black", "9000"]) == ["matte black", "9000"], "fidelity: misses reported")

    # 2) Codecs.
    sample = Task(
        task_id="t_sample",
        sender_msg="The gate is 12 and the crate weighs 41 kg. Keep it upright.",
        receiver_task="Using only the message above, state the gate and the weight.",
        eval_checks=("12", "41"),
    )
    enc1 = encode_occ1(sample)
    check(enc1.splitlines()[0] == "OCC/1", "occ1 first line is OCC/1")
    check(any(line.startswith("F1=") and "41 kg" in line for line in enc1.splitlines()), "occ1 body preserved in F1")
    enc2 = encode_occ2(sample)
    check(enc2.splitlines()[0] == "OCC2/1", "occ2 first line is OCC2/1")
    check("The gate is 12" in enc2, "occ2 body preserved")
    check(len(enc1) > len(sample.sender_msg) and len(enc2) > len(sample.sender_msg), "envelopes add header overhead")

    # 3) Task file.
    tasks_path = Path(args.tasks_file)
    tasks: list[Task] = []
    if tasks_path.exists():
        try:
            tasks = load_tasks(tasks_path)
            check(len(tasks) >= 12, f"tasks file loads with at least 12 tasks (got {len(tasks)})")
        except (OSError, ValueError) as exc:
            check(False, f"tasks file load failed: {exc}")
    else:
        print("note: tasks file not found; using built-in mini tasks")
    if len(tasks) < 2:
        tasks = _builtin_selftest_tasks()

    forced = Task(
        task_id="t_forced_refusal",
        sender_msg="The access codeword for the north entrance is zephyr-42 and it expires at midnight.",
        receiver_task="Using only the message above, state the access codeword.",
        eval_checks=("zephyr-42",),
        domain="forced_refusal",
    )
    mini = tasks[:2] + [forced]

    # 4) Stub run over all arms (occ3 also exercises eviction misses).
    stub = StubBenchClient(tasks=mini)
    records: list[TrialRecord] = []
    for arm in ARM_CHOICES:
        settings = RunSettings(
            arm=arm,
            temperature=0.0,
            max_new_tokens=64,
            seed=7,
            flush_cache=True,
            eviction_fill=2 if arm == "occ3" else 0,
            ttl_s=None,
        )
        for task in mini:
            for trial in range(2):
                records.append(run_trial(settings, task, trial, stub))
    check(len(records) == len(ARM_CHOICES) * len(mini) * 2, "every arm/task/trial combination produced a record")
    check(
        all(r.e2e_ms >= 0.0 and r.send_ms >= 0.0 and r.recv_ms >= 0.0 and r.encode_ms >= 0.0 for r in records),
        "latency fields non-negative",
    )
    check(all(r.wire_chars > 0 for r in records), "wire_chars populated for every record")
    by_id = {task.task_id: task for task in mini}
    check(
        all(r.wire_chars > len(by_id[r.task_id].sender_msg) for r in records if r.arm in ("occ1", "occ2")),
        "occ1/occ2 wire larger than raw message",
    )
    check(
        all(r.wire_chars == len(by_id[r.task_id].sender_msg) for r in records if r.arm in ("text", "occ3")),
        "text/occ3 wire equals raw message length",
    )
    occ3_records = [r for r in records if r.arm == "occ3"]
    check(bool(occ3_records) and all(isinstance(r.hit, bool) for r in occ3_records), "occ3 records carry a boolean hit flag")
    check(any(r.success for r in records), "stub produces successful trials")
    forced_records = [r for r in records if r.task_id == forced.task_id]
    check(bool(forced_records) and all(not r.success for r in forced_records), "forced-refusal task fails fidelity in every arm")

    # 5) Aggregation and serialization.
    aggregate = aggregate_records(records)
    check("e2e_ms" in aggregate and "median" in aggregate["e2e_ms"], "aggregate contains e2e_ms median")
    check(0.0 <= aggregate.get("success_rate", -1.0) <= 1.0, "success_rate bounded in [0, 1]")
    check("hit_rate" in aggregate and 0.0 <= aggregate["hit_rate"] <= 1.0, "hit_rate present and bounded")
    check(aggregate.get("n_trials") == len(records), "trial count matches")
    document = build_result(args, mini, records, key_env=None, selftest=True)
    try:
        json.dumps(document)
        check(True, "results document is JSON serializable")
    except (TypeError, ValueError) as exc:
        check(False, f"results document is JSON serializable: {exc}")

    # 6) Graceful degradation on an unreachable endpoint (loopback, port 1).
    if _httpx is not None or _requests is not None:
        dead = SglangBenchClient("http://127.0.0.1:1", api_key=None, timeout=2.0)
        check(dead.probe() is False, "probe reports False for an unreachable endpoint")
    else:
        print("note: no HTTP library installed; skipping reachability check")

    if failures:
        print(f"SELFTEST FAILED: {len(failures)} check(s) failed")
        return 1
    print("SELFTEST PASSED")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def resolve_api_key(primary_env: str) -> tuple[str | None, str | None]:
    """Resolve the server API key from the environment; never log its value.

    Order: the --api-key-env name first, then OCC3_API_KEY, then
    SGLANG_API_KEY. Returns (key, env_var_name_used).
    """
    names: list[str] = []
    for name in (primary_env, "OCC3_API_KEY", "SGLANG_API_KEY"):
        if name and name not in names:
            names.append(name)
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return None, None


def build_parser() -> argparse.ArgumentParser:
    """Build the benchmark CLI parser."""
    parser = argparse.ArgumentParser(
        prog="bench_occ3.py",
        description="Benchmark OCC-1 / OCC-2 / OCC-3 agent communication on one SGLang server.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:30000", help="SGLang base URL")
    parser.add_argument("--arm", default="occ3", choices=ARM_CHOICES, help="delivery arm to measure")
    parser.add_argument("--tasks-file", default=str(DEFAULT_TASKS_FILE), help="JSON file with asymmetric-information tasks")
    parser.add_argument("--trials", type=int, default=20, help="trials per task (use at least 20 for reported medians)")
    parser.add_argument("--out", default="results.json", help="output JSON path")
    parser.add_argument("--api-key-env", default="OCC3_API_KEY", help="primary environment variable holding the server API key (fallback: SGLANG_API_KEY)")
    parser.add_argument("--temperature", type=float, default=0.0, help="receiver sampling temperature (0 for eval)")
    parser.add_argument("--max-new-tokens", type=int, default=256, help="matched decode budget for every arm")
    parser.add_argument("--seed", type=int, default=1234, help="sampling seed passed to the server (text arms)")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-request HTTP timeout in seconds")
    parser.add_argument("--eviction-fill", type=int, default=0, help="filler generations between OCC-3 send and recv (eviction probe)")
    parser.add_argument("--ttl-s", type=float, default=None, help="OCC-3 channel TTL in seconds (server default applies when omitted)")
    parser.add_argument("--no-flush", action="store_true", help="do not flush the radix cache before each trial")
    parser.add_argument("--selftest", action="store_true", help="run the offline selftest (stub client; no GPU, no network)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on partial errors, 2 on fatal setup errors."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest(args)

    if args.trials < 1:
        print("ERROR: --trials must be at least 1", file=sys.stderr)
        return 2
    args.eviction_fill = max(0, args.eviction_fill)
    if args.arm == "occ3" and args.max_new_tokens > OCC3_PLUGIN_MAX_RECV_NEW_TOKENS:
        print(
            f"WARNING: --max-new-tokens {args.max_new_tokens} exceeds the OCC-3 plugin decode "
            f"cap ({OCC3_PLUGIN_MAX_RECV_NEW_TOKENS}); the server may reject the recv request",
            file=sys.stderr,
        )

    tasks_path = Path(args.tasks_file)
    try:
        tasks = load_tasks(tasks_path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot load tasks file {tasks_path}: {exc}", file=sys.stderr)
        return 2
    if not tasks:
        print(f"ERROR: tasks file {tasks_path} contains no tasks", file=sys.stderr)
        return 2

    api_key, key_env = resolve_api_key(args.api_key_env)
    if args.arm == "occ3" and not api_key:
        print(
            "ERROR: arm 'occ3' requires the server API key. Set OCC3_API_KEY or SGLANG_API_KEY "
            "in the environment (or point --api-key-env at the right variable). The key is read "
            "from the environment only and is never printed or stored.",
            file=sys.stderr,
        )
        return 2

    client = SglangBenchClient(args.endpoint, api_key, timeout=args.timeout)

    # Reachability probe with clear failure messages.
    if args.arm == "occ3":
        try:
            status = client.occ3_status()
        except HttpError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Hint: the /occ3/v1 routes did not answer; confirm the OCC-3 plugin is deployed and enabled on this SGLang server.", file=sys.stderr)
            return 2
        except TransportError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Hint: check --endpoint, confirm the SGLang server is running, and check network or firewall reachability.", file=sys.stderr)
            return 2
        version = status.get("version") if isinstance(status, dict) else None
        print(f"OCC-3 plugin status: version={version}")
    else:
        try:
            client.model_info()
        except HttpError as exc:
            print(f"note: /get_model_info answered an HTTP error ({exc}); continuing.", file=sys.stderr)
        except TransportError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Hint: check --endpoint, confirm the SGLang server is running, and check network or firewall reachability.", file=sys.stderr)
            return 2

    settings = RunSettings(
        arm=args.arm,
        temperature=args.temperature,
        max_new_tokens=args.max_new_tokens,
        seed=args.seed,
        flush_cache=not args.no_flush,
        eviction_fill=args.eviction_fill,
        ttl_s=args.ttl_s,
    )

    records: list[TrialRecord] = []
    for task in tasks:
        for trial in range(args.trials):
            record = run_trial(settings, task, trial, client)
            records.append(record)
            tag = "ok" if record.success else ("error" if record.error else "fail")
            prefill = record.receiver_prefill_tokens if record.receiver_prefill_tokens is not None else "?"
            cached = record.cached_tokens if record.cached_tokens is not None else "?"
            line = (
                f"[{task.task_id}] {trial + 1}/{args.trials} arm={args.arm} {tag} "
                f"e2e_ms={record.e2e_ms:.1f} prefill={prefill} cached={cached}"
            )
            if record.hit is not None:
                line += f" hit={record.hit}"
            if record.error:
                line += f" error={record.error[:140]}"
            print(line)

    result = build_result(args, tasks, records, key_env=key_env)
    out_path = Path(args.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot write {out_path}: {exc}", file=sys.stderr)
        return 2

    aggregate = result["aggregate"]
    print("")
    print(f"--- summary arm={args.arm} tasks={len(tasks)} trials={len(records)} ---")
    for metric in ("wire_chars", "send_ms", "recv_ms", "e2e_ms", "receiver_prefill_tokens", "cached_tokens", "carrier_tokens"):
        stats = aggregate.get(metric)
        if stats:
            print(f"{metric}: median={stats['median']:.3f} p10={stats['p10']:.3f} p90={stats['p90']:.3f} n={stats['count']}")
    print(f"success_rate={aggregate.get('success_rate', 0.0):.3f}")
    if "hit_rate" in aggregate:
        print(f"hit_rate={aggregate['hit_rate']:.3f} cache_miss_rate={aggregate['cache_miss_rate']:.3f}")
    print(f"wrote {out_path}")

    n_errors = sum(1 for record in records if record.error)
    if n_errors:
        print(f"WARNING: {n_errors}/{len(records)} trials ended in transport errors", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
