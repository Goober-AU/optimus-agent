#!/usr/bin/env python3
"""Benchmark harness for OCC-1 / OCC-2 / OCC-3 agent communication.

Eight arms (spec: SEMANTIC COMMUNICATION TESTS)
-----------------------------------------------
no_comm
    No communication. The receiver sees only the task prompt; nothing is
    delivered. Chance-level accuracy floor.
random_latent
    Random latent state. A deterministic pseudorandom information-free
    carrier is written through the OCC-3 send/recv channel mechanics, so
    the channel works but carries no task information.
unrelated_latent
    Unrelated latent state. The carrier is the sender-only evidence of a
    DIFFERENT task (fixed rotation), so real fluent text flows through the
    latent channel with wrong content.
zeroed_latent
    Zeroed latent state. A constant information-free carrier (repeated
    neutral symbol) flows through the same channel mechanics. This is a
    text-level stand-in: the public plugin API accepts text only, so it is
    not a literal all-zero KV tensor (see README Caveats).
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
    covering the carrier) via POST /occ3/v1/recv and prefills only the task
    suffix. Same-exact-model only.
full_text
    Full relevant text upper bound (alias: text). The sender message is
    delivered verbatim and the receiver prefills it together with the
    task through POST /generate.

Arms are first-class config objects (ArmSpec: id, role, delivery, carrier
source, expected channel behavior), so the same tasks run identically
across arms. Random/zeroed latent controls are the significance floor:
OCC-3 must beat them to claim semantic transfer (see analyze.py).

Measured dimensions (see README.md in this directory)
----------------------------------------------------
a) wire_chars                  characters in the sender's outbound payload.
b) send_ms / recv_ms / e2e_ms  send-to-recv latency.
c) receiver_prefill_tokens     tokens the receiver actually prefilled
                               (prompt_tokens - cached_tokens).
d) success / failed_checks     semantic fidelity on asymmetric tasks,
                               per verifier (exact-match or scripted).
e) hit / cache_miss_rate       OCC-3 prefix-hit stability.

Ledger: every run can append one entry (JSONL) in schema
optimus.occ3.benchmark-ledger.v1 with hypothesis, revisions, seed, arms,
accuracy, latency, token usage, failure rate, confidence interval and a
KEEP/REVISE/REVERT/INCONCLUSIVE/BLOCKED decision.

The API key is read from the environment only (OCC3_API_KEY or
SGLANG_API_KEY; override the primary name with --api-key-env). It is
never written to files and never printed.

Dependencies: Python 3.11+ and either httpx or requests (not needed for
--selftest).
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
from dataclasses import asdict, dataclass, field
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

# Canonical arm order (matches the spec's control list, protocols, upper bound).
ARM_ORDER = (
    "no_comm",
    "random_latent",
    "unrelated_latent",
    "zeroed_latent",
    "occ1",
    "occ2",
    "occ3",
    "full_text",
)
# Backward-compatible alias for results and CLI input written before the
# arm registry existed.
ARM_ALIASES = {"text": "full_text"}
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

# Task-set schemas: v1 was a bare JSON list; v2 is an object with tiers and
# per-task verifiers. The loader accepts both.
TASKS_SCHEMA_V2 = "optimus.occ3.tasks.v2"

# Ledger schema (JSONL; one entry per recorded run).
LEDGER_SCHEMA = "optimus.occ3.benchmark-ledger.v1"
LEDGER_DECISIONS = ("KEEP", "REVISE", "REVERT", "INCONCLUSIVE", "BLOCKED")
LEDGER_REQUIRED_FIELDS = (
    "schema",
    "entry_id",
    "recorded_at_utc",
    "benchmark",
    "hypothesis",
    "code_revision",
    "model_revision",
    "sglang_revision",
    "runtime_configuration",
    "dataset",
    "task_count",
    "random_seed",
    "arm",
    "treatment_arm",
    "control_arms",
    "accuracy",
    "accuracy_by_tier",
    "latency",
    "token_usage",
    "gpu_memory",
    "failure_rate",
    "confidence_interval",
    "implementation_cost",
    "decision",
    "notes",
)

# Significance-floor arms for the semantic-transfer gate (analyze.py and the
# live campaign use the same default).
DEFAULT_FLOOR_ARMS = ("random_latent", "zeroed_latent")

# Canonical digest of the 12 original v1 tasks (id, domain, sender_msg,
# receiver_task, eval_checks) that must stay unchanged inside the v2 file.
ORIGINAL_TASKS_DIGEST = "c966680fdf2956e5906dc1686f84d87a29d9f9179cd70081d48e03bbc68c4eef"
ORIGINAL_TASK_IDS = (
    "t01_logistics_dock_move",
    "t02_scheduling_sync_move",
    "t03_coding_flaky_test",
    "t04_measurements_niche",
    "t05_preferences_client",
    "t06_priorities_sprint",
    "t07_travel_platform",
    "t08_inventory_count",
    "t09_budget_offsite",
    "t10_safety_solvent",
    "t11_service_rename",
    "t12_dinner_booking",
)

# Known pre-existing issues inside the 12 unchanged original tasks. The 12
# originals are kept byte-identical per campaign scope, so these are
# allowlisted in the selftest and documented in the README instead of fixed.
# (task_id, check that appears in the task's own receiver prompt)
KNOWN_ORIGINAL_SELF_LEAKS = {
    "t09_budget_offsite": ("catering",),
    "t12_dinner_booking": ("vegan",),
}
# (check_owner_task_id, sender_owner_task_id, check) pairs where one original
# task's check appears inside another original task's sender message. This
# only weakens the live unrelated-latent floor for those pairs; full-task
# verifiers still fail without the right evidence.
KNOWN_ORIGINAL_SENDER_COLLISIONS = {
    ("t04_measurements_niche", "t09_budget_offsite", "34"),
    ("t05_preferences_client", "t04_measurements_niche", "teal"),
    ("t08_inventory_count", "t02_scheduling_sync_move", "14"),
    ("t10_safety_solvent", "t04_measurements_niche", "18"),
    ("t10_safety_solvent", "t09_budget_offsite", "25"),
}


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


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file's bytes."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_originals_digest(tasks: Sequence["Task"]) -> str:
    """Digest the v1-relevant fields of the first 12 tasks (order preserved)."""
    import hashlib

    payload = []
    for task in list(tasks)[: len(ORIGINAL_TASK_IDS)]:
        payload.append(
            {
                "id": task.task_id,
                "domain": task.domain,
                "sender_msg": task.sender_msg,
                "receiver_task": task.receiver_task,
                "eval_checks": list(task.eval_checks),
            }
        )
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, indent=1).encode("utf-8")
    ).hexdigest()


def wilson_interval(successes: int, n: int, z: float = 1.96) -> dict[str, float] | None:
    """Wilson score interval for a binomial proportion; None when n is 0."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denominator
    margin = (z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))) / denominator
    return {
        "level": 0.95,
        "low": max(0.0, center - margin),
        "high": min(1.0, center + margin),
        "n": n,
        "successes": successes,
    }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verifier:
    """Output verifier for one task.

    exact_match: every task.eval_checks substring must appear in the
    receiver output (case-insensitive).

    scripted: all_of substrings must all appear; each any_of group needs
    at least one member present; no none_of substring may appear.
    """

    type: str = "exact_match"
    all_of: tuple[str, ...] = ()
    any_of_groups: tuple[tuple[str, ...], ...] = ()
    none_of: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        if self.type == "exact_match":
            return {"type": "exact_match"}
        return {
            "type": "scripted",
            "all_of": list(self.all_of),
            "any_of_groups": [list(group) for group in self.any_of_groups],
            "none_of": list(self.none_of),
        }


@dataclass(frozen=True)
class Task:
    """One asymmetric-information benchmark task.

    The receiver can only answer from the delivered sender evidence; the
    verifier decides success. The receiver prompt never contains the
    answers (no leakage by construction; enforced by the selftest).
    """

    task_id: str
    sender_msg: str
    receiver_task: str
    eval_checks: tuple[str, ...]
    domain: str = "general"
    tier: int = 1
    verifier: Verifier = field(default_factory=Verifier)

    def check_parts(self) -> list[str]:
        """Substrings a maximally cooperative answer would contain (stub mode)."""
        if self.verifier.type == "exact_match":
            return list(self.eval_checks)
        parts = list(self.verifier.all_of)
        for group in self.verifier.any_of_groups:
            if group:
                parts.append(group[0])
        return parts


def _parse_verifier(item: dict[str, Any], eval_checks: Sequence[str], where: str) -> Verifier:
    """Parse and validate one task's verifier object."""
    raw = item.get("verifier") or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: verifier must be an object")
    vtype = str(raw.get("type") or "exact_match")
    if vtype not in ("exact_match", "scripted"):
        raise ValueError(f"{where}: unknown verifier type {vtype!r}")
    if vtype == "exact_match":
        if not eval_checks:
            raise ValueError(f"{where}: exact_match verifier requires eval_checks")
        return Verifier(type="exact_match")

    def _strings(value: Any, label: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise ValueError(f"{where}: verifier.{label} must be a list of non-empty strings")
        return tuple(value)

    all_of = _strings(raw.get("all_of"), "all_of")
    none_of = _strings(raw.get("none_of"), "none_of")
    groups_raw = raw.get("any_of_groups") or []
    if not isinstance(groups_raw, list):
        raise ValueError(f"{where}: verifier.any_of_groups must be a list of lists")
    groups: list[tuple[str, ...]] = []
    for index, group in enumerate(groups_raw):
        if not isinstance(group, list) or not group:
            raise ValueError(f"{where}: any_of_groups[{index}] must be a non-empty list")
        members = _strings(group, f"any_of_groups[{index}]")
        if not members:
            raise ValueError(f"{where}: any_of_groups[{index}] must be a non-empty list")
        groups.append(members)
    if not all_of:
        raise ValueError(f"{where}: scripted verifier requires a non-empty all_of")
    return Verifier(type="scripted", all_of=all_of, any_of_groups=tuple(groups), none_of=none_of)


def _task_from_item(item: dict[str, Any], index: int, *, default_tier: int = 1) -> Task:
    if not isinstance(item, dict):
        raise ValueError(f"task #{index} is not an object")
    missing = [key for key in ("sender_msg", "receiver_task") if key not in item]
    if missing:
        raise ValueError(f"task #{index} is missing required field(s): {', '.join(missing)}")
    sender = str(item["sender_msg"])
    receiver = str(item["receiver_task"])
    checks = [str(check) for check in (item.get("eval_checks") or [])]
    verifier = _parse_verifier(item, checks, f"task #{index}")
    if verifier.type == "exact_match" and not checks:
        raise ValueError(f"task #{index}: exact_match tasks require eval_checks")
    if not sender or not receiver:
        raise ValueError(f"task #{index} has an empty required field")
    tier = item.get("tier", default_tier)
    if not isinstance(tier, int) or isinstance(tier, bool) or tier < 1:
        raise ValueError(f"task #{index}: tier must be a positive integer")
    return Task(
        task_id=str(item.get("id") or f"task_{index:02d}"),
        sender_msg=sender,
        receiver_task=receiver,
        eval_checks=tuple(checks),
        domain=str(item.get("domain") or "general"),
        tier=int(tier),
        verifier=verifier,
    )


def load_tasks(path: Path) -> list[Task]:
    """Load tasks from a v2 object or a v1 JSON list.

    v2: {"schema": "optimus.occ3.tasks.v2", "tasks": [task, ...]} with
    per-task tier and verifier. v1: a bare list of task objects (tier 1,
    exact match). Raises ValueError or OSError on bad input; never returns
    partially valid tasks.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    default_tier = 1
    if isinstance(data, dict):
        schema = str(data.get("schema") or "")
        if schema != TASKS_SCHEMA_V2:
            raise ValueError(
                f"tasks file schema {schema!r} is not {TASKS_SCHEMA_V2!r}; "
                "a JSON object must carry the v2 schema"
            )
        items = data.get("tasks")
        if not isinstance(items, list):
            raise ValueError("tasks file must contain a 'tasks' list")
    elif isinstance(data, list):
        items = data  # v1 bare list
    else:
        raise ValueError("tasks file must contain a JSON list (v1) or object (v2)")
    tasks: list[Task] = []
    for index, item in enumerate(items):
        tasks.append(_task_from_item(item, index, default_tier=default_tier))
    return tasks


def verify_output(task: Task, output_text: str) -> tuple[bool, list[str]]:
    """Verify receiver output against the task's verifier.

    Returns (success, failed_labels). Matching is case-insensitive
    substring containment, identical to the v1 harness semantics.
    """
    lowered = (output_text or "").lower()
    verifier = task.verifier
    if verifier.type == "exact_match":
        missing = [check for check in task.eval_checks if check.lower() not in lowered]
        return (not missing, missing)
    failed: list[str] = []
    for check in verifier.all_of:
        if check.lower() not in lowered:
            failed.append(f"all_of:{check}")
    for index, group in enumerate(verifier.any_of_groups):
        if not any(member.lower() in lowered for member in group):
            failed.append(f"any_of_group_{index}")
    for check in verifier.none_of:
        if check.lower() in lowered:
            failed.append(f"none_of:{check}")
    return (not failed, failed)


# ---------------------------------------------------------------------------
# Arm registry: first-class arm config objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmSpec:
    """Configuration for one benchmark arm.

    arm_id         unique CLI id (alias resolved before lookup).
    label          human-readable name.
    role           floor | latent-control | protocol | upper-bound.
    delivery       none | text | latent (how the message reaches the receiver).
    carrier_source mock/live data source for the delivered payload:
                   none | task-message | random | unrelated | zeroed.
    requires_api_key  whether live runs need the server API key.
    description    expected channel behavior.
    """

    arm_id: str
    label: str
    role: str
    delivery: str
    carrier_source: str
    requires_api_key: bool
    description: str

    def describe(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "label": self.label,
            "role": self.role,
            "delivery": self.delivery,
            "carrier_source": self.carrier_source,
            "requires_api_key": self.requires_api_key,
            "description": self.description,
        }


_ARM_SPECS = (
    ArmSpec(
        arm_id="no_comm",
        label="No communication",
        role="floor",
        delivery="none",
        carrier_source="none",
        requires_api_key=False,
        description=(
            "Nothing is delivered; the receiver sees only its task prompt. "
            "Expected: chance-level accuracy; any arm must beat this to claim "
            "the channel matters at all."
        ),
    ),
    ArmSpec(
        arm_id="random_latent",
        label="Random latent state",
        role="latent-control",
        delivery="latent",
        carrier_source="random",
        requires_api_key=True,
        description=(
            "A deterministic pseudorandom information-free carrier is written "
            "through the OCC-3 send/recv channel mechanics. Expected: the "
            "channel works (prefix hit) but accuracy stays at chance; this is "
            "the primary significance floor for semantic transfer."
        ),
    ),
    ArmSpec(
        arm_id="unrelated_latent",
        label="Unrelated latent state",
        role="latent-control",
        delivery="latent",
        carrier_source="unrelated",
        requires_api_key=True,
        description=(
            "The carrier is the sender-only evidence of a different task "
            "(fixed rotation), so real fluent text flows through the latent "
            "channel with wrong content. Expected: chance-level accuracy; "
            "separates latent activity from task-relevant transfer."
        ),
    ),
    ArmSpec(
        arm_id="zeroed_latent",
        label="Zeroed latent state",
        role="latent-control",
        delivery="latent",
        carrier_source="zeroed",
        requires_api_key=True,
        description=(
            "A constant information-free carrier (repeated neutral symbol) "
            "flows through the same channel mechanics. Text-level stand-in "
            "for a zero-information KV state, not a literal all-zero tensor. "
            "Expected: chance-level accuracy; second significance floor."
        ),
    ),
    ArmSpec(
        arm_id="occ1",
        label="OCC-1 compact structured English",
        role="protocol",
        delivery="text",
        carrier_source="task-message",
        requires_api_key=False,
        description=(
            "The message is encoded with the OCC-1 codec (optimus.agent-comm.v1) "
            "and the receiver prefills the delivered envelope plus the task."
        ),
    ),
    ArmSpec(
        arm_id="occ2",
        label="OCC-2 dual symbolic envelope (stand-in)",
        role="protocol",
        delivery="text",
        carrier_source="task-message",
        requires_api_key=False,
        description=(
            "Local stand-in for occ2::encode_dual: compact symbolic header plus "
            "verbatim body; the receiver prefills the delivered envelope plus "
            "the task. Measures envelope overhead, not production compression."
        ),
    ),
    ArmSpec(
        arm_id="occ3",
        label="OCC-3 latent communication",
        role="protocol",
        delivery="latent",
        carrier_source="task-message",
        requires_api_key=True,
        description=(
            "The sender writes message KV into a salted radix-cache namespace "
            "(one prefill) via POST /occ3/v1/send; the receiver reads it "
            "zero-copy as a prefix hit via POST /occ3/v1/recv and prefills "
            "only the task suffix. Same-exact-model only."
        ),
    ),
    ArmSpec(
        arm_id="full_text",
        label="Full relevant text (upper bound)",
        role="upper-bound",
        delivery="text",
        carrier_source="task-message",
        requires_api_key=False,
        description=(
            "The sender evidence is delivered verbatim as text and the "
            "receiver prefills it together with the task. Accuracy upper "
            "bound for the same receiver model. Alias: text."
        ),
    ),
)

ARM_REGISTRY: dict[str, ArmSpec] = {spec.arm_id: spec for spec in _ARM_SPECS}
ARM_CHOICES = tuple(ARM_ORDER)


# ---------------------------------------------------------------------------
# Carrier data sources (shared by live and stub modes)
# ---------------------------------------------------------------------------

_RANDOM_SOUP_ALPHABET = "~^=+*#%&@$<>"
UNRELATED_FALLBACK_TEXT = (
    "The quarterly facilities review covered parking assignments, plant watering "
    "schedules, and the kitchenette coffee machine lease; nothing in it relates "
    "to any benchmark question."
)


def random_carrier(task: Task, trial_idx: int, seed: int) -> str:
    """Deterministic pseudorandom information-free carrier text.

    Uses only neutral symbols so no alphanumeric eval check can ever be a
    substring of the carrier. Length tracks the task's sender message.
    """
    rng = random.Random(f"occ3-random-latent|{seed}|{task.task_id}|{trial_idx}")
    target = max(64, len(task.sender_msg))
    words: list[str] = []
    total = 0
    while total < target:
        length = rng.randint(3, 9)
        words.append("".join(rng.choice(_RANDOM_SOUP_ALPHABET) for _ in range(length)))
        total += length + 1
    return " ".join(words)


def zeroed_carrier(task: Task) -> str:
    """Constant information-free carrier for the zeroed-latent control."""
    return "~" * max(64, len(task.sender_msg))


def unrelated_carrier(task: Task, all_tasks: Sequence[Task]) -> str:
    """Sender-only evidence of a different task (deterministic rotation)."""
    others = [candidate for candidate in all_tasks if candidate.task_id != task.task_id]
    if not others:
        return UNRELATED_FALLBACK_TEXT
    position = 0
    for index, candidate in enumerate(all_tasks):
        if candidate.task_id == task.task_id:
            position = index
            break
    return others[position % len(others)].sender_msg


def carrier_text(spec: ArmSpec, task: Task, all_tasks: Sequence[Task], trial_idx: int, seed: int) -> str:
    """Return the payload sent through the arm's channel for one trial."""
    if spec.carrier_source == "task-message":
        return task.sender_msg
    if spec.carrier_source == "random":
        return random_carrier(task, trial_idx, seed)
    if spec.carrier_source == "unrelated":
        return unrelated_carrier(task, all_tasks)
    if spec.carrier_source == "zeroed":
        return zeroed_carrier(task)
    raise BenchmarkError(f"arm {spec.arm_id!r} has no carrier source")


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
    """Return the sender's delivered text payload for a text-delivery arm."""
    if arm == "full_text":
        return task.sender_msg
    if arm == "occ1":
        return encode_occ1(task)
    if arm == "occ2":
        return encode_occ2(task)
    raise BenchmarkError(f"arm {arm!r} does not deliver text")


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

    seed drives text-arm sampling params and the deterministic control-arm
    carriers; the OCC-3 recv route has no seed parameter in the plugin API.
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
    tier: int
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
    all_tasks: Sequence[Task] | None = None,
) -> TrialRecord:
    """Run and measure one send-to-recv trial for any arm.

    Latent arms (occ3 and the random/unrelated/zeroed controls): optional
    flush, send the arm's carrier (one prefill into the salted radix-cache
    namespace), optional eviction filler traffic, recv (prefix hit plus
    suffix prefill plus decode), release. Text arms (full_text/occ1/occ2):
    optional flush, encode, then one /generate prefill of the delivered
    envelope plus the receiver task. no_comm: optional flush and one
    /generate of the receiver task alone. The optional radix-cache flush
    and the channel release stay outside the measured window so they never
    pollute e2e.
    """
    spec = ARM_REGISTRY.get(settings.arm)
    if spec is None:
        raise BenchmarkError(f"unknown arm: {settings.arm!r}")
    if all_tasks is None:
        all_tasks = [task]

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
    delivered = ""
    channel = make_channel_id(task, trial_idx)

    try:
        if spec.delivery == "latent":
            carrier = carrier_text(spec, task, all_tasks, trial_idx, settings.seed)
            delivered = carrier
            t_send = time.perf_counter()
            send_resp = client.occ3_send(channel, carrier, ttl_s=settings.ttl_s)
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
        elif spec.delivery == "text":
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
        else:  # no_comm: the receiver task alone, nothing delivered
            t_recv = time.perf_counter()
            generation = client.generate(
                task.receiver_task,
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
        if spec.delivery == "latent":
            try:
                client.occ3_release(channel)
            except Exception:
                pass  # best effort; the channel TTL bounds its lifetime anyway

    e2e_ms = (time.perf_counter() - started) * 1000.0
    receiver_prefill: int | None = None
    if prompt_tokens is not None and cached_tokens is not None:
        receiver_prefill = max(0, prompt_tokens - cached_tokens)
    if error is not None:
        failed: list[str] = ["error"]
    else:
        _, failed = verify_output(task, output_text)
    return TrialRecord(
        task_id=task.task_id,
        domain=task.domain,
        tier=task.tier,
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
        success=error is None and not failed,
        failed_checks=failed,
        error=error,
    )


def aggregate_records(records: Sequence[TrialRecord]) -> dict[str, Any]:
    """Aggregate trial records into medians, p10/p90, rates and tier splits."""
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
        arm = records[0].arm
        spec = ARM_REGISTRY.get(arm)
        if spec is not None and spec.delivery == "latent":
            hits = sum(1 for record in records if record.hit is True)
            summary["hit_rate"] = hits / len(records)
            summary["cache_miss_rate"] = 1.0 - summary["hit_rate"]
        by_tier: dict[str, dict[str, Any]] = {}
        for record in records:
            bucket = by_tier.setdefault(str(record.tier), {"n_trials": 0, "n_success": 0})
            bucket["n_trials"] += 1
            if record.success:
                bucket["n_success"] += 1
        for bucket in by_tier.values():
            bucket["success_rate"] = bucket["n_success"] / bucket["n_trials"]
        summary["by_tier"] = by_tier
    return summary


def build_result(
    args: argparse.Namespace,
    tasks: Sequence[Task],
    records: Sequence[TrialRecord],
    *,
    key_env: str | None,
    selftest: bool = False,
    arm: str | None = None,
) -> dict[str, Any]:
    """Assemble the results.json document (no secrets; env var name only)."""
    spec = ARM_REGISTRY.get(arm or args.arm)
    arm_meta: dict[str, Any] = {}
    if spec is not None:
        arm_meta = spec.describe()
    return {
        "schema": "occ-bench-results/2",
        "meta": {
            "arm": "selftest" if selftest else (arm or args.arm),
            "arm_spec": arm_meta,
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
            {
                "id": task.task_id,
                "domain": task.domain,
                "tier": task.tier,
                "eval_checks": list(task.eval_checks),
                "verifier": task.verifier.describe(),
            }
            for task in tasks
        ],
        "aggregate": aggregate_records(records),
        "trials": [asdict(record) for record in records],
    }


# ---------------------------------------------------------------------------
# Ledger (optimus.occ3.benchmark-ledger.v1)
# ---------------------------------------------------------------------------


def build_ledger_entry(
    *,
    arm: str,
    records: Sequence[TrialRecord],
    tasks: Sequence[Task],
    tasks_file: str,
    tasks_sha256: str,
    endpoint: str,
    temperature: float,
    max_new_tokens: int,
    seed: int,
    trials_per_task: int,
    eviction_fill: int,
    flush_cache: bool,
    ttl_s: float | None,
    api_key_env: str | None,
    selftest: bool,
    hypothesis: str,
    decision: str,
    code_revision: str,
    model_revision: str,
    sglang_revision: str,
    gpu_memory_mib: float | None,
    control_arms: Sequence[str],
    treatment_arm: str,
    notes: str = "",
) -> dict[str, Any]:
    """Build one ledger entry from a completed arm run.

    Implements the SCIENTIFIC EXPERIMENT LEDGER fields from the campaign
    spec: hypothesis, revisions, runtime configuration, benchmark, dataset,
    task count, seed, control/treatment arms, accuracy, latency, GPU memory,
    token usage, failure rate, confidence interval, implementation cost and
    the KEEP/REVISE/REVERT/INCONCLUSIVE/BLOCKED decision.
    """
    aggregate = aggregate_records(records)
    accuracy = float(aggregate.get("success_rate", 0.0))
    n = int(aggregate.get("n_trials", 0))
    successes = sum(1 for record in records if record.success)
    by_tier = {
        tier: round(float(bucket.get("success_rate", 0.0)), 6)
        for tier, bucket in (aggregate.get("by_tier") or {}).items()
    }

    def metric_stats(metric: str) -> dict[str, float] | None:
        entry = aggregate.get(metric)
        if not isinstance(entry, dict):
            return None
        return {
            "median": entry.get("median"),
            "p10": entry.get("p10"),
            "p90": entry.get("p90"),
        }

    return {
        "schema": LEDGER_SCHEMA,
        "entry_id": uuid.uuid4().hex,
        "recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "benchmark": "bench_occ3",
        "hypothesis": hypothesis,
        "code_revision": code_revision,
        "model_revision": model_revision,
        "sglang_revision": sglang_revision,
        "runtime_configuration": {
            "arm": arm,
            "endpoint": endpoint,
            "temperature": temperature,
            "max_new_tokens": max_new_tokens,
            "random_seed": seed,
            "trials_per_task": trials_per_task,
            "eviction_fill": eviction_fill,
            "flush_cache": flush_cache,
            "ttl_s": ttl_s,
            "api_key_env": api_key_env,
            "selftest": selftest,
        },
        "dataset": {
            "file": str(Path(tasks_file).name),
            "sha256": tasks_sha256,
            "task_count": len(tasks),
        },
        "task_count": len(tasks),
        "random_seed": seed,
        "arm": arm,
        "treatment_arm": treatment_arm,
        "control_arms": list(control_arms),
        "accuracy": accuracy,
        "accuracy_by_tier": by_tier,
        "latency": {
            "e2e_ms": metric_stats("e2e_ms"),
            "send_ms": metric_stats("send_ms"),
            "recv_ms": metric_stats("recv_ms"),
        },
        "token_usage": {
            "receiver_prefill_tokens": metric_stats("receiver_prefill_tokens"),
            "completion_tokens": metric_stats("completion_tokens"),
            "carrier_tokens": metric_stats("carrier_tokens"),
        },
        "gpu_memory": {"mib": gpu_memory_mib} if gpu_memory_mib is not None else None,
        "failure_rate": (float(aggregate.get("n_errors", 0)) / n) if n else 0.0,
        "confidence_interval": wilson_interval(successes, n),
        # Proxy only: wire size and encode time approximate implementation
        # cost; see README Caveats.
        "implementation_cost": {
            "wire_chars": metric_stats("wire_chars"),
            "encode_ms": metric_stats("encode_ms"),
        },
        "decision": decision,
        "notes": notes,
    }


def validate_ledger_entry(entry: Any) -> list[str]:
    """Return a list of validation problems (empty when valid)."""
    problems: list[str] = []
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    for name in LEDGER_REQUIRED_FIELDS:
        if name not in entry:
            problems.append(f"missing field: {name}")
    if problems:
        return problems
    if entry.get("schema") != LEDGER_SCHEMA:
        problems.append(f"schema must be {LEDGER_SCHEMA!r}")
    if entry.get("decision") not in LEDGER_DECISIONS:
        problems.append(f"decision must be one of {LEDGER_DECISIONS}")
    accuracy = entry.get("accuracy")
    if not isinstance(accuracy, (int, float)) or isinstance(accuracy, bool) or not 0.0 <= float(accuracy) <= 1.0:
        problems.append("accuracy must be a number in [0, 1]")
    if not isinstance(entry.get("task_count"), int) or entry.get("task_count", 0) < 1:
        problems.append("task_count must be a positive integer")
    if not isinstance(entry.get("control_arms"), list) or not entry.get("control_arms"):
        problems.append("control_arms must be a non-empty list")
    for name in ("hypothesis", "code_revision", "model_revision", "sglang_revision", "treatment_arm", "arm", "benchmark", "entry_id", "recorded_at_utc"):
        if not isinstance(entry.get(name), str) or not entry.get(name):
            problems.append(f"{name} must be a non-empty string")
    return problems


def append_ledger(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Validate and append one entry as a JSONL line; returns the entry."""
    problems = validate_ledger_entry(entry)
    if problems:
        raise BenchmarkError("invalid ledger entry: " + "; ".join(problems))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    return entry


def read_ledger(path: Path) -> list[dict[str, Any]]:
    """Read and validate a ledger JSONL file; returns the entries."""
    entries: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BenchmarkError(f"ledger line {line_number} is not JSON: {exc}") from exc
            problems = validate_ledger_entry(entry)
            if problems:
                raise BenchmarkError(f"ledger line {line_number} invalid: {'; '.join(problems)}")
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Stub client (offline selftest)
# ---------------------------------------------------------------------------


class StubBenchClient:
    """Deterministic in-process stub of the bench client for --selftest.

    Implements the same method surface as SglangBenchClient. The stub
    answers a receiver prompt with a canned sentence containing the task's
    verifier parts whenever the delivered message is present in the prompt,
    and with a refusal sentence otherwise. occ3 recv misses stochastically
    once filler traffic has been issued, which exercises the eviction-miss
    path. No GPU, no server, no network.

    The canned answers prove harness plumbing (arms, verifiers, ledger,
    aggregation), not model semantic ability; no live claims can be made
    from stub results.
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
        """Canned maximally-cooperative answer for one task (stub mode)."""
        if task.domain == "forced_refusal":
            return cls._REFUSAL
        joined = ", ".join(task.check_parts())
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


def _check_task_file(tasks: list[Task], check) -> list[str]:
    """Validate the loaded task set; returns problem labels (empty when ok)."""
    problems: list[str] = []
    if len(tasks) < 24:
        problems.append(f"task set has fewer than 24 tasks (got {len(tasks)})")
    tiers = sorted({task.tier for task in tasks})
    if len(tiers) < 3:
        problems.append(f"task set must span at least 3 difficulty tiers (got {tiers})")

    # The 12 original tasks stay unchanged (digest over the v1 fields).
    original_ids = [task.task_id for task in tasks[: len(ORIGINAL_TASK_IDS)]]
    if original_ids != list(ORIGINAL_TASK_IDS):
        problems.append("first 12 task ids are not the original v1 ids in order")
    else:
        digest = canonical_originals_digest(tasks)
        if digest != ORIGINAL_TASKS_DIGEST:
            problems.append("original 12 tasks were modified (digest mismatch)")

    stub = StubBenchClient(tasks=tasks)
    answers = {task.task_id: stub._answer_for(task) for task in tasks}  # noqa: SLF001 - test-local
    senders = {task.task_id: task.sender_msg.lower() for task in tasks}
    receivers = {task.task_id: task.receiver_task.lower() for task in tasks}

    for task in tasks:
        # No leakage: no check may appear in the task's own receiver prompt.
        for part in task.check_parts():
            if part.lower() in receivers[task.task_id]:
                if part in KNOWN_ORIGINAL_SELF_LEAKS.get(task.task_id, ()):
                    continue  # pre-existing v1 weakness, documented in README
                problems.append(f"leak: {task.task_id} receiver prompt contains check {part!r}")
        # Answerability: every required substring must exist in the evidence.
        if task.verifier.type == "exact_match":
            for part in task.eval_checks:
                if part.lower() not in senders[task.task_id]:
                    problems.append(f"unanswerable: {task.task_id} check {part!r} not in sender msg")
        else:
            for part in task.verifier.all_of:
                if part.lower() not in senders[task.task_id]:
                    problems.append(f"unanswerable: {task.task_id} all_of {part!r} not in sender msg")
            for index, group in enumerate(task.verifier.any_of_groups):
                if not any(member.lower() in senders[task.task_id] for member in group):
                    problems.append(f"unanswerable: {task.task_id} any_of group {index} has no member in sender msg")
            for part in task.verifier.none_of:
                if part.lower() in answers[task.task_id]:
                    problems.append(f"construction: {task.task_id} none_of {part!r} appears in stub answer")
        # No redundant nested checks inside one task.
        parts = list(task.eval_checks) if task.verifier.type == "exact_match" else list(task.verifier.all_of)
        for i, first in enumerate(parts):
            for j, second in enumerate(parts):
                if i != j and first.lower() in second.lower():
                    problems.append(f"redundant: {task.task_id} check {first!r} nested in {second!r}")

    # Cross-task: no verifier of task i may pass on the canned answer of
    # task j (content-disjoint controls; the unrelated-latent floor).
    for task_i in tasks:
        for task_j in tasks:
            if task_i.task_id == task_j.task_id:
                continue
            ok, _ = verify_output(task_i, answers[task_j.task_id])
            if ok:
                problems.append(f"collision: {task_i.task_id} verifier passes on {task_j.task_id} answer")

    # Cross-task senders: a check appearing inside another task's evidence
    # only weakens the live unrelated-latent floor; pre-existing v1 pairs
    # are allowlisted, new tasks must stay clean.
    for task_i in tasks:
        for task_j in tasks:
            if task_i.task_id == task_j.task_id:
                continue
            parts = list(task_i.eval_checks) if task_i.verifier.type == "exact_match" else list(task_i.verifier.all_of)
            for part in parts:
                if part.lower() in senders[task_j.task_id]:
                    if (task_i.task_id, task_j.task_id, part) in KNOWN_ORIGINAL_SENDER_COLLISIONS:
                        continue
                    problems.append(f"sender-collision: {task_i.task_id} check {part!r} appears in {task_j.task_id} sender msg")
    return problems


def run_selftest(args: argparse.Namespace) -> int:
    """Validate the harness offline with a stub client; no GPU, no server.

    Proves the harness runs all arms end-to-end with fake data: task-set
    construction rules, verifiers, arm registry, per-arm expectations,
    ledger append/read, aggregation and serialization. The stub answers
    are constructed to pass or fail per arm by design; they validate
    plumbing, not model semantics.
    """
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        print(("ok   " if condition else "FAIL ") + label)
        if not condition:
            failures.append(label)

    # 1) Pure helpers and verifiers.
    check(abs(percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) - 3.0) < 1e-9, "percentile median of [1..5]")
    check(abs(percentile([1.0, 2.0, 3.0, 4.0], 10) - 1.3) < 1e-9, "percentile p10 interpolation")
    check(percentile([7.0], 90) == 7.0, "percentile of singleton")
    check(failed_checks("The answer is Matte Black, cap 9000.", ["matte black", "9000"]) == [], "fidelity: case-insensitive hits")
    check(failed_checks("I do not know.", ["matte black", "9000"]) == ["matte black", "9000"], "fidelity: misses reported")
    scripted_task = Task(
        task_id="t_verifier_unit",
        sender_msg="The function frob has a null-deref defect; the corrected line is x = y.or_default().",
        receiver_task="State the function, the defect class, and the corrected line.",
        eval_checks=(),
        verifier=Verifier(
            type="scripted",
            all_of=("frob", "y.or_default()"),
            any_of_groups=(("null dereference", "null-deref"),),
            none_of=("i do not know",),
        ),
    )
    ok, failed = verify_output(scripted_task, "frob has a null dereference; x = y.or_default()")
    check(ok and not failed, "scripted verifier: all_of plus any_of group satisfied")
    ok, failed = verify_output(scripted_task, "frob is fine; x = y.or_default()")
    check(not ok and any(label.startswith("any_of_group") for label in failed), "scripted verifier: missing any_of group reported")
    ok, failed = verify_output(scripted_task, "frob has a null dereference; x = y.or_default(); i do not know why")
    check(not ok and any(label.startswith("none_of") for label in failed), "scripted verifier: none_of violation reported")

    # 2) Arm registry.
    check(len(ARM_REGISTRY) == 8, "arm registry defines exactly 8 arms")
    check(ARM_ORDER == tuple(ARM_REGISTRY), "arm order matches registry")
    required_arms = {"no_comm", "random_latent", "unrelated_latent", "zeroed_latent", "occ1", "occ2", "occ3", "full_text"}
    check(required_arms.issubset(ARM_REGISTRY), "spec-required arms present (no/random/unrelated/zeroed/occ1/occ2/occ3/full text)")
    check(ARM_ALIASES.get("text") == "full_text", "text alias maps to full_text")
    check(all(spec.carrier_source in ("none", "task-message", "random", "unrelated", "zeroed") for spec in ARM_REGISTRY.values()), "every arm declares a carrier source")
    check(all(spec.delivery in ("none", "text", "latent") for spec in ARM_REGISTRY.values()), "every arm declares a delivery mode")

    # 3) Codecs.
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

    # 4) Task file (v2 schema, tiers, construction rules).
    tasks_path = Path(args.tasks_file)
    tasks: list[Task] = []
    if tasks_path.exists():
        try:
            tasks = load_tasks(tasks_path)
            check(len(tasks) >= 24, f"tasks file loads with at least 24 tasks (got {len(tasks)})")
        except (OSError, ValueError) as exc:
            check(False, f"tasks file load failed: {exc}")
    else:
        print("note: tasks file not found; using built-in mini tasks")
    if len(tasks) < 2:
        tasks = _builtin_selftest_tasks()
    else:
        tier_counts: dict[int, int] = {}
        for task in tasks:
            tier_counts[task.tier] = tier_counts.get(task.tier, 0) + 1
        print(f"note: task tiers: {dict(sorted(tier_counts.items()))}")
        for problem in _check_task_file(tasks, check):
            check(False, problem)

    forced = Task(
        task_id="t_forced_refusal",
        sender_msg="The access codeword for the north entrance is zephyr-42 and it expires at midnight.",
        receiver_task="Using only the message above, state the access codeword.",
        eval_checks=("zephyr-42",),
        domain="forced_refusal",
    )
    run_tasks = list(tasks) + [forced]

    # 5) Stub run over all 8 arms (occ3 also exercises eviction misses).
    import tempfile

    artifacts_dir = Path(getattr(args, "selftest_dir", None) or tempfile.mkdtemp(prefix="occ3-bench-selftest-"))
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    stub = StubBenchClient(tasks=run_tasks, seed=7)
    records_by_arm: dict[str, list[TrialRecord]] = {}
    for arm in ARM_ORDER:
        settings = RunSettings(
            arm=arm,
            temperature=0.0,
            max_new_tokens=64,
            seed=7,
            flush_cache=True,
            eviction_fill=0,
            ttl_s=None,
        )
        arm_records: list[TrialRecord] = []
        for task in run_tasks:
            for trial in range(2):
                arm_records.append(run_trial(settings, task, trial, stub, run_tasks))
        records_by_arm[arm] = arm_records
        n_ok = sum(1 for record in arm_records if record.success)
        print(f"note: arm={arm} trials={len(arm_records)} successes={n_ok}")
    all_records = [record for arm in ARM_ORDER for record in records_by_arm[arm]]
    check(len(all_records) == len(ARM_ORDER) * len(run_tasks) * 2, "every arm/task/trial combination produced a record")
    check(
        all(r.e2e_ms >= 0.0 and r.send_ms >= 0.0 and r.recv_ms >= 0.0 and r.encode_ms >= 0.0 for r in all_records),
        "latency fields non-negative",
    )
    check(all(r.wire_chars >= 0 for r in all_records), "wire_chars populated for every record")

    by_id = {task.task_id: task for task in run_tasks}
    check(
        all(r.wire_chars > len(by_id[r.task_id].sender_msg) for r in all_records if r.arm in ("occ1", "occ2")),
        "occ1/occ2 wire larger than raw message",
    )
    check(
        all(r.wire_chars == len(by_id[r.task_id].sender_msg) for r in all_records if r.arm in ("full_text", "occ3")),
        "full_text/occ3 wire equals raw message length",
    )
    check(
        all(r.wire_chars == 0 for r in all_records if r.arm == "no_comm"),
        "no_comm wire is zero",
    )
    control_arms = ("random_latent", "unrelated_latent", "zeroed_latent")
    check(
        all(r.wire_chars >= 64 for r in all_records if r.arm in control_arms),
        "latent control carriers are non-trivially sized",
    )
    check(
        all(isinstance(r.hit, bool) for r in all_records if r.arm in control_arms + ("occ3",)),
        "latent arms carry a boolean hit flag",
    )
    check(
        all(r.hit is True for r in all_records if r.arm in control_arms),
        "latent control channels hit without eviction (mechanics work, content is uninformative)",
    )
    check(
        all(not r.success for r in all_records if r.arm == "no_comm"),
        "no_comm floor: no successes with nothing delivered",
    )
    check(
        all(not r.success for r in all_records if r.arm in control_arms),
        "latent controls: random/unrelated/zeroed carriers produce no successes",
    )
    informative_arms = ("full_text", "occ1", "occ2", "occ3")
    check(
        all(r.success == (r.task_id != forced.task_id) for r in all_records if r.arm in informative_arms),
        "informative arms succeed on every task except the forced refusal",
    )
    check(any(r.success for r in all_records), "stub produces successful trials")
    check(
        all(not r.success for r in all_records if r.task_id == forced.task_id),
        "forced-refusal task fails fidelity in every arm",
    )

    # 6) OCC-3 eviction-miss path: filler traffic between send and recv.
    evict_settings = RunSettings(
        arm="occ3",
        temperature=0.0,
        max_new_tokens=64,
        seed=7,
        flush_cache=True,
        eviction_fill=4,
        ttl_s=None,
    )
    evict_records: list[TrialRecord] = []
    for task in run_tasks[:4]:
        for trial in range(6):
            evict_records.append(run_trial(evict_settings, task, trial, stub, run_tasks))
    misses = [record for record in evict_records if record.hit is False]
    check(bool(misses), "eviction filler produces at least one cache miss")
    check(all(not record.success for record in misses), "cache-miss trials fail fidelity")
    check(
        all(record.success == (record.task_id != forced.task_id) for record in evict_records if record.hit is True),
        "hit trials still succeed under eviction pressure (except forced refusal)",
    )

    # 7) Aggregation, tier splits and serialization.
    aggregate = aggregate_records(all_records)
    check("e2e_ms" in aggregate and "median" in aggregate["e2e_ms"], "aggregate contains e2e_ms median")
    check(0.0 <= aggregate.get("success_rate", -1.0) <= 1.0, "success_rate bounded in [0, 1]")
    check("by_tier" in aggregate and set(aggregate["by_tier"]) == {"1", "2", "3"}, "aggregate contains per-tier success rates")
    check(aggregate.get("n_trials") == len(all_records), "trial count matches")
    document = build_result(args, run_tasks, all_records, key_env=None, selftest=True)
    try:
        json.dumps(document)
        check(True, "results document is JSON serializable")
    except (TypeError, ValueError) as exc:
        check(False, f"results document is JSON serializable: {exc}")
    check(document["schema"] == "occ-bench-results/2", "results schema is occ-bench-results/2")
    check(document["meta"]["arm_spec"].get("arm_id") in ARM_REGISTRY, "results meta carries the arm spec")

    # 8) Ledger: write one entry per arm, read back, validate.
    ledger_path = artifacts_dir / "ledger.jsonl"
    for arm in ARM_ORDER:
        entry = build_ledger_entry(
            arm=arm,
            records=records_by_arm[arm],
            tasks=run_tasks,
            tasks_file=str(args.tasks_file),
            tasks_sha256=sha256_file(tasks_path) if tasks_path.exists() else "selftest",
            endpoint=args.endpoint,
            temperature=0.0,
            max_new_tokens=64,
            seed=7,
            trials_per_task=2,
            eviction_fill=0,
            flush_cache=True,
            ttl_s=None,
            api_key_env=None,
            selftest=True,
            hypothesis="selftest: harness runs all arms and writes a ledger",
            decision="INCONCLUSIVE",
            code_revision="selftest",
            model_revision="stub-model",
            sglang_revision="stub",
            gpu_memory_mib=None,
            control_arms=DEFAULT_FLOOR_ARMS,
            treatment_arm=arm,
            notes="stub data; validates plumbing only, not model semantics",
        )
        append_ledger(ledger_path, entry)
    try:
        entries = read_ledger(ledger_path)
        check(len(entries) == len(ARM_ORDER), "ledger round-trip returns one valid entry per arm")
        check(all(entry["schema"] == LEDGER_SCHEMA for entry in entries), "ledger entries carry the v1 schema id")
        check(all(entry["decision"] in LEDGER_DECISIONS for entry in entries), "ledger decisions are in the spec enum")
        check(all("confidence_interval" in entry and entry["confidence_interval"] is not None for entry in entries), "ledger entries carry a confidence interval")
    except BenchmarkError as exc:
        check(False, f"ledger round-trip: {exc}")

    # 9) Per-arm result files for analyze.py (offline pipeline proof).
    for arm in ARM_ORDER:
        result = build_result(args, run_tasks, records_by_arm[arm], key_env=None, selftest=True, arm=arm)
        (artifacts_dir / f"results_{arm}.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8", newline=""
        )
    check(
        all((artifacts_dir / f"results_{arm}.json").exists() for arm in ARM_ORDER),
        "per-arm selftest results written for analysis",
    )
    print(f"note: selftest artifacts dir: {artifacts_dir}")

    # 10) Graceful degradation on an unreachable endpoint (loopback, port 1).
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


def resolve_arms(raw: str) -> list[str]:
    """Resolve the --arm value (single arm, alias, comma list or 'all')."""
    text = (raw or "").strip()
    if not text:
        raise BenchmarkError("no arm selected")
    if text.lower() == "all":
        return list(ARM_ORDER)
    arms: list[str] = []
    for token in text.split(","):
        arm = ARM_ALIASES.get(token.strip().lower(), token.strip().lower())
        if not arm:
            continue
        if arm not in ARM_REGISTRY:
            raise BenchmarkError(
                f"unknown arm: {token.strip()!r} (choices: {', '.join(ARM_ORDER)}, comma lists, or 'all')"
            )
        if arm not in arms:
            arms.append(arm)
    if not arms:
        raise BenchmarkError("no arm selected")
    return arms


def build_parser() -> argparse.ArgumentParser:
    """Build the benchmark CLI parser."""
    parser = argparse.ArgumentParser(
        prog="bench_occ3.py",
        description="Benchmark OCC-1 / OCC-2 / OCC-3 agent communication on one SGLang server.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:30000", help="SGLang base URL")
    parser.add_argument(
        "--arm",
        default="occ3",
        help="delivery arm to measure: one of %s, a comma list, or 'all' (alias: text=full_text)" % ", ".join(ARM_ORDER),
    )
    parser.add_argument("--tasks-file", default=str(DEFAULT_TASKS_FILE), help="JSON file with asymmetric-information tasks (v2 object or v1 list)")
    parser.add_argument("--trials", type=int, default=20, help="trials per task (use at least 20 for reported medians)")
    parser.add_argument("--out", default="results.json", help="output JSON path (per-arm suffix is added when several arms run)")
    parser.add_argument("--api-key-env", default="OCC3_API_KEY", help="primary environment variable holding the server API key (fallback: SGLANG_API_KEY)")
    parser.add_argument("--temperature", type=float, default=0.0, help="receiver sampling temperature (0 for eval)")
    parser.add_argument("--max-new-tokens", type=int, default=256, help="matched decode budget for every arm")
    parser.add_argument("--seed", type=int, default=1234, help="sampling seed passed to the server (text arms) and control-carrier seed")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-request HTTP timeout in seconds")
    parser.add_argument("--eviction-fill", type=int, default=0, help="filler generations between latent send and recv (eviction probe)")
    parser.add_argument("--ttl-s", type=float, default=None, help="OCC-3 channel TTL in seconds (server default applies when omitted)")
    parser.add_argument("--no-flush", action="store_true", help="do not flush the radix cache before each trial")
    parser.add_argument("--ledger", default=None, help="append one benchmark-ledger JSONL entry per arm run (schema optimus.occ3.benchmark-ledger.v1)")
    parser.add_argument("--hypothesis", default="OCC-3 latent communication preserves task-relevant information at lower receiver prefill cost than text delivery", help="hypothesis recorded in the ledger")
    parser.add_argument("--decision", default="INCONCLUSIVE", choices=LEDGER_DECISIONS, help="ledger decision for this run")
    parser.add_argument("--code-rev", default="unknown", help="code revision recorded in the ledger")
    parser.add_argument("--model-rev", default="unknown", help="model revision recorded in the ledger")
    parser.add_argument("--sglang-rev", default="unknown", help="SGLang revision recorded in the ledger")
    parser.add_argument("--gpu-memory-mib", type=float, default=None, help="observed GPU memory in MiB recorded in the ledger (the harness does not measure it itself)")
    parser.add_argument("--control-arms", default=",".join(DEFAULT_FLOOR_ARMS), help="comma list of control arms recorded in the ledger (significance floor)")
    parser.add_argument("--treatment-arm", default=None, help="treatment arm recorded in the ledger (defaults to the run arm)")
    parser.add_argument("--notes", default="", help="free-form notes recorded in the ledger")
    parser.add_argument("--selftest-dir", default=None, help="directory for selftest artifacts (results, ledger); a kept temp dir is used when omitted")
    parser.add_argument("--selftest", action="store_true", help="run the offline selftest (stub client; no GPU, no network)")
    return parser


def _probe_arm(arm: str, client: SglangBenchClient) -> int:
    """Probe the endpoint for one arm; returns 2 on fatal errors, else 0."""
    spec = ARM_REGISTRY[arm]
    if spec.delivery == "latent":
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
    return 0


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
    try:
        arms = resolve_arms(args.arm)
    except BenchmarkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for arm in arms:
        if ARM_REGISTRY[arm].delivery == "latent" and args.max_new_tokens > OCC3_PLUGIN_MAX_RECV_NEW_TOKENS:
            print(
                f"WARNING: --max-new-tokens {args.max_new_tokens} exceeds the OCC-3 plugin decode "
                f"cap ({OCC3_PLUGIN_MAX_RECV_NEW_TOKENS}); the server may reject the recv request for arm {arm}",
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
    try:
        tasks_sha = sha256_file(tasks_path)
    except OSError:
        tasks_sha = "unknown"

    api_key, key_env = resolve_api_key(args.api_key_env)
    latent_arms = [arm for arm in arms if ARM_REGISTRY[arm].delivery == "latent"]
    if latent_arms and not api_key:
        print(
            f"ERROR: latent arms ({', '.join(latent_arms)}) require the server API key. Set OCC3_API_KEY "
            "or SGLANG_API_KEY in the environment (or point --api-key-env at the right variable). "
            "The key is read from the environment only and is never printed or stored.",
            file=sys.stderr,
        )
        return 2

    client = SglangBenchClient(args.endpoint, api_key, timeout=args.timeout)

    out_path = Path(args.out)
    worst = 0
    for arm in arms:
        # Reachability probe with clear failure messages.
        probe_result = _probe_arm(arm, client)
        if probe_result:
            return probe_result

        settings = RunSettings(
            arm=arm,
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
                record = run_trial(settings, task, trial, client, tasks)
                records.append(record)
                tag = "ok" if record.success else ("error" if record.error else "fail")
                prefill = record.receiver_prefill_tokens if record.receiver_prefill_tokens is not None else "?"
                cached = record.cached_tokens if record.cached_tokens is not None else "?"
                line = (
                    f"[{arm}] [{task.task_id}] {trial + 1}/{args.trials} {tag} "
                    f"e2e_ms={record.e2e_ms:.1f} prefill={prefill} cached={cached}"
                )
                if record.hit is not None:
                    line += f" hit={record.hit}"
                if record.error:
                    line += f" error={record.error[:140]}"
                print(line)

        result = build_result(args, tasks, records, key_env=key_env, arm=arm)
        if len(arms) == 1:
            arm_out = out_path
        else:
            arm_out = out_path.with_name(f"{out_path.stem}_{arm}{out_path.suffix}")
        try:
            arm_out.parent.mkdir(parents=True, exist_ok=True)
            arm_out.write_text(json.dumps(result, indent=2), encoding="utf-8", newline="")
        except OSError as exc:
            print(f"ERROR: cannot write {arm_out}: {exc}", file=sys.stderr)
            return 2

        aggregate = result["aggregate"]
        print("")
        print(f"--- summary arm={arm} tasks={len(tasks)} trials={len(records)} ---")
        for metric in ("wire_chars", "send_ms", "recv_ms", "e2e_ms", "receiver_prefill_tokens", "cached_tokens", "carrier_tokens"):
            stats = aggregate.get(metric)
            if stats:
                print(f"{metric}: median={stats['median']:.3f} p10={stats['p10']:.3f} p90={stats['p90']:.3f} n={stats['count']}")
        print(f"success_rate={aggregate.get('success_rate', 0.0):.3f}")
        if "hit_rate" in aggregate:
            print(f"hit_rate={aggregate['hit_rate']:.3f} cache_miss_rate={aggregate['cache_miss_rate']:.3f}")
        print(f"wrote {arm_out}")

        if args.ledger:
            try:
                entry = build_ledger_entry(
                    arm=arm,
                    records=records,
                    tasks=tasks,
                    tasks_file=str(args.tasks_file),
                    tasks_sha256=tasks_sha,
                    endpoint=args.endpoint,
                    temperature=args.temperature,
                    max_new_tokens=args.max_new_tokens,
                    seed=args.seed,
                    trials_per_task=args.trials,
                    eviction_fill=args.eviction_fill,
                    flush_cache=not args.no_flush,
                    ttl_s=args.ttl_s,
                    api_key_env=key_env,
                    selftest=False,
                    hypothesis=args.hypothesis,
                    decision=args.decision,
                    code_revision=args.code_rev,
                    model_revision=args.model_rev,
                    sglang_revision=args.sglang_rev,
                    gpu_memory_mib=args.gpu_memory_mib,
                    control_arms=[item.strip() for item in args.control_arms.split(",") if item.strip()],
                    treatment_arm=args.treatment_arm or arm,
                    notes=args.notes,
                )
                append_ledger(Path(args.ledger), entry)
                print(f"appended ledger entry to {args.ledger}")
            except (BenchmarkError, OSError) as exc:
                print(f"ERROR: cannot append ledger {args.ledger}: {exc}", file=sys.stderr)
                worst = max(worst, 2)

        n_errors = sum(1 for record in records if record.error)
        if n_errors:
            print(f"WARNING: {n_errors}/{len(records)} trials ended in transport errors", file=sys.stderr)
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
