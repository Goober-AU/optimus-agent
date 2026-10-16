#!/usr/bin/env python3
"""Deterministic message corpus generator for OCC-2 evaluation.

Builds msgs_corpus.json: a corpus of typed `optimus.agent-comm.v1` messages
(pre-encoding contract fields, NOT pre-encoded OCC-1 text) compatible with
the existing fidelity fixture schema in
crates/pi-coding-agent/tests/fixtures/occ/scenarios.json:

  [{"id": ..., "holdout": ..., "plain_english": ..., "payload": {...}}, ...]

Payload fields follow the fixture exactly: task_id, kind, status,
message_id, trace_id, sender_identity, recipient_identity, findings
(list of {statement, confidence, evidence}; no id, positional F1..Fn on
encoding), evidence (list of {id, kind, locator, line_range?, digest?,
revision?, verified?}), artifacts, verification (null or
{claim, status, method, receipt?}), dependencies, next_actions, sequence,
timestamp, plus optional parent_task_id and expiry.

Coverage: all 9 message kinds, all 7 task statuses, all 8 evidence kinds,
verification records with pass/fail/unknown, next actions, dependencies,
parent task ids, expiry stamps, holdout flag on a subset of stories.

Determinism: all randomness flows from sha256-derived seeds. The
self-check validates every message against the contract invariants
(mirroring contract.rs validate()), checks OCC-1 encodability with a
Python port of the encoder field order, verifies plain_english rendering
is pure ASCII, and confirms a second build is identical.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import random
import re
import sys

SEED_BASE = "occ23-msgs-corpus-v1"

KINDS = (
    "TASK", "PROGRESS", "RESULT", "EVIDENCE", "REQUEST_DETAIL",
    "ACK", "CORRECTION", "BLOCKED", "CANCELLED",
)
STATUSES = (
    "pending", "in_progress", "done", "partial", "blocked", "failed", "cancelled",
)
EVIDENCE_KINDS = (
    "src", "commit", "log", "build", "tool", "artifact", "lines", "finding",
)
VERIFICATION_STATUSES = ("pass", "fail", "unknown")

COORD = "daemon://coordinator"
AUDITOR = "agent://security-auditor"
BENCH = "agent://bench-runner"
WORKERS = {
    "a": "agent://worker-a",
    "b": "agent://worker-b",
    "c": "agent://worker-c",
    "d": "agent://worker-d",
    "e": "agent://worker-e",
    "f": "agent://worker-f",
}

HEX_DIGITS = "0123456789abcdef"


def derive_seed(*parts):
    joined = "|".join((SEED_BASE,) + tuple(parts))
    return int(hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16], 16)


def rand_hex(rng, n):
    return "".join(rng.choice(HEX_DIGITS) for _ in range(n))


def finding(statement, confidence=None, evidence=None):
    return {"statement": statement, "confidence": confidence, "evidence": evidence or []}


def evidence(eid, kind, locator, **optional):
    entry = {"id": eid, "kind": kind, "locator": locator}
    for key in ("line_range", "digest", "revision", "verified"):
        if key in optional and optional[key] is not None:
            entry[key] = optional[key]
    return entry


def verification(claim, status, method, receipt=None):
    record = {"claim": claim, "status": status, "method": method}
    if receipt is not None:
        record["receipt"] = receipt
    return record


# ---------------------------------------------------------------- stories
# Each story is one delegated task. "chain" holds the message specs in
# order; the composer assigns message ids, sequences, timestamps and
# trace ids. Content mirrors the campaign reality of the OCC-2/OCC-3 work.

STORIES = [
    {
        "task_id": "occ2-01",
        "title": "dialect discovery prototype",
        "worker": "b",
        "holdout": False,
        "deps": ["t39"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("implement the OCC-2 dialect discovery prototype behind a feature flag")],
                "next_actions": [
                    "survey the existing OCC-1 call sites",
                    "draft the offline discovery loop",
                    "keep the feature flag off by default",
                ],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the discovery loop now enumerates the top 40 field pairs per trace", 0.8, ["E1"]),
                    finding("the feature flag defaults to off in every build profile", None, []),
                ],
                "evidence": [
                    evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/occ1.rs",
                             line_range=(88, 120), revision="ce8480d59"),
                ],
                "next_actions": ["rank the pairs by frequency", "cap the symbol table at 48 entries"],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("the prototype discovers a stable symbol table for 27 of 28 fidelity scenarios", 0.9, ["E1", "E2"]),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-01-discovery-run.txt", verified=True),
                    evidence("E2", "src", "crates/pi-coding-agent/src/core/occ/dialect.rs",
                             line_range=(14, 61), revision="ce8480d59"),
                ],
                "artifacts": ["research/occ2/dialect-table.json"],
                "verification": verification(
                    "the discovered dialect round-trips all 28 fidelity scenarios", "pass", "cargo_test",
                    "reports/occ2-01-verify.txt"),
                "next_actions": ["review the symbol table before any wire use"],
            },
            {
                "kind": "EVIDENCE",
                "status": "done",
                "findings": [],
                "evidence": [
                    evidence("E1", "commit", "4f2a91c37", verified=True),
                    evidence("E2", "artifact", "research/occ2/dialect-table.json"),
                ],
                "artifacts": ["research/occ2/dialect-table.json"],
            },
            {
                "kind": "CORRECTION",
                "status": "in_progress",
                "findings": [
                    finding("the stable count is 26 of 28, not 27; scenario s19 needs a fallback symbol", 0.95, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "finding", "ACK_OF:2"),
                ],
                "next_actions": ["add the fallback symbol for s19", "re-run the discovery loop"],
            },
        ],
    },
    {
        "task_id": "occ2-02",
        "title": "tokenizer measurement",
        "worker": "c",
        "holdout": False,
        "deps": [],
        "parent": "t77",
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("measure real tokenizer counts for OCC-1 versus plain English on the model gateway")],
                "next_actions": [
                    "batch the 28 fidelity scenarios",
                    "record usage.prompt_tokens per encoding",
                    "write the comparison table",
                ],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("batch one of two is measured; OCC-1 prompt tokens run 40 to 48 percent below plain English", 0.7, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-02-tokens-batch1.log", verified=False),
                ],
                "next_actions": ["run batch two after the gateway restart window"],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the gateway health check passes between batches", 1.0, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-02-health.txt", verified=True),
                ],
            },
            {
                "kind": "RESULT",
                "status": "partial",
                "findings": [
                    finding("14 of 28 scenarios are measured; the gateway restart interrupts long batches", 0.85, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-02-tokens-batch2.log", verified=False),
                ],
                "artifacts": ["reports/occ2-02-tokens-partial.md"],
                "verification": verification(
                    "token counts are reproducible across gateway restarts", "unknown", "manual_review"),
                "next_actions": ["resume batch two", "cross-check three scenarios by hand"],
            },
            {
                "kind": "ACK",
                "status": "done",
                "sender": WORKERS["c"],
                "findings": [
                    finding("receipt of the assignment is confirmed; the first batch has started", 1.0, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "finding", "ACK_OF:0"),
                ],
                "next_actions": ["finish the remaining batch within 24 hours"],
            },
        ],
    },
    {
        "task_id": "occ2-03",
        "title": "fidelity corpus extension",
        "worker": "e",
        "holdout": False,
        "deps": ["occ2-01"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("extend the fidelity fixture corpus with at least 60 new typed messages")],
                "next_actions": [
                    "cover all nine message kinds",
                    "cover all seven task statuses",
                    "keep the fixture schema unchanged",
                ],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("41 of 60 messages are drafted; kinds TASK and RESULT are fully covered", 0.75, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "build", "target/occ2-03-corpus-build.txt", verified=True),
                ],
                "next_actions": ["draft the remaining REQUEST_DETAIL and CORRECTION messages"],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("60 new messages cover all nine kinds and all seven statuses with zero schema drift", 0.95, ["E1", "E2"]),
                ],
                "evidence": [
                    evidence("E1", "src", "crates/pi-coding-agent/tests/fixtures/occ/scenarios.json",
                             line_range=(1, 40), revision="ce8480d59"),
                    evidence("E2", "log", "logs/occ2-03-schema-check.log", verified=True),
                ],
                "artifacts": ["research/occ2/corpus-extension.json"],
                "verification": verification(
                    "the extended corpus passes the fidelity round-trip test", "pass", "cargo_test",
                    "reports/occ2-03-verify.txt"),
            },
            {
                "kind": "EVIDENCE",
                "status": "done",
                "findings": [],
                "evidence": [
                    evidence("E1", "commit", "9b41d02ef", verified=True),
                    evidence("E2", "artifact", "research/occ2/corpus-extension.json"),
                ],
            },
        ],
    },
    {
        "task_id": "occ2-04",
        "title": "compression benchmark",
        "worker": "f",
        "holdout": False,
        "deps": ["occ2-02"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("benchmark OCC-1 versus JSON versus plain English on the new corpus")],
                "next_actions": [
                    "reuse the 100-iteration latency harness",
                    "report bytes and proxy tokens per encoding",
                ],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the harness runs 100 iterations per message with warm caches", 0.8, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-04-harness.txt", verified=True),
                ],
            },
            {
                "kind": "REQUEST_DETAIL",
                "status": "in_progress",
                "sender": WORKERS["f"],
                "recipient": WORKERS["c"],
                "findings": [
                    finding("request the exact gateway route and model used for the tokenizer measurement", None, []),
                ],
                "next_actions": ["reply with the route and the model name"],
            },
            {
                "kind": "ACK",
                "status": "done",
                "sender": WORKERS["c"],
                "recipient": WORKERS["f"],
                "findings": [
                    finding("receipt of the detail request is confirmed", 1.0, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "finding", "ACK_OF:2"),
                ],
                "next_actions": ["send the route and model name today"],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("OCC-1 saves 38.2 percent bytes against plain English on the new corpus", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-04-compression.log", verified=True),
                ],
                "artifacts": ["reports/occ2-04-compression.md"],
                "verification": verification(
                    "the byte and token savings reproduce within one percent", "pass", "bench_harness",
                    "receipts/occ2-04-bench.txt"),
            },
        ],
    },
    {
        "task_id": "occ2-05",
        "title": "pruning policy port",
        "worker": "a",
        "holdout": False,
        "deps": ["t41"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("port the periodic fractional pruning skeleton behind measured utility")],
                "next_actions": [
                    "wire the never-prune exemption list",
                    "gate every prune on a measured utility signal",
                ],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the never-prune exemption list is wired and covered by tests", 0.85, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/prune.rs",
                             line_range=(20, 74), revision="ce8480d59"),
                ],
            },
            {
                "kind": "RESULT",
                "status": "failed",
                "findings": [
                    finding("the utility signal is too noisy to gate pruning safely", 0.8, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-05-utility-noise.log", verified=False),
                ],
                "verification": verification(
                    "pruning with the measured utility never drops a needed message", "fail", "cargo_test",
                    "reports/occ2-05-verify.txt"),
                "next_actions": ["collect a longer utility trace", "keep pruning disabled"],
            },
        ],
    },
    {
        "task_id": "occ2-06",
        "title": "latent channel probe",
        "worker": "b",
        "holdout": True,
        "deps": [],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("probe whether the local models can share hidden states through the daemon transport")],
                "next_actions": ["check transport capabilities", "document the hard limits"],
            },
            {
                "kind": "BLOCKED",
                "status": "blocked",
                "findings": [
                    finding("the daemon transport carries text only; tensor access is unavailable at this layer", 1.0, ["E1"]),
                    finding("no latent handoff can run without a same-process runtime", None, []),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-06-transport-check.txt", verified=True),
                ],
                "next_actions": ["request a design review of the transport boundary"],
            },
            {
                "kind": "RESULT",
                "status": "partial",
                "findings": [
                    finding("a text-only proxy probe ran; latent transfer remains unsupported on this transport", 0.6, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "build", "target/occ2-06-proxy-probe.txt", verified=False),
                ],
                "verification": verification(
                    "the proxy probe matches the latent baseline", "unknown", "manual_review"),
            },
        ],
    },
    {
        "task_id": "occ2-07",
        "title": "retention policy change",
        "worker": "d",
        "holdout": False,
        "deps": [],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("update the retention policy from 30 days to 90 days before pruning")],
                "next_actions": ["read the window from the policy file", "re-run the prune tests"],
            },
            {
                "kind": "CORRECTION",
                "status": "in_progress",
                "findings": [
                    finding("the retention requirement changed from 30 days to 90 days before pruning", 0.95, ["E1"]),
                    finding("the prune threshold must be computed from the policy file, not the compiled-in constant", None, []),
                ],
                "evidence": [
                    evidence("E1", "finding", "ACK_OF:0"),
                ],
                "next_actions": ["update the policy loader", "re-run the prune tests"],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("the policy loader reads the retention window from the policy file", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/prune.rs",
                             line_range=(96, 140), revision="ce8480d59"),
                ],
                "verification": verification(
                    "the 90 day window is enforced by the prune tests", "pass", "cargo_test",
                    "reports/occ2-07-verify.txt"),
            },
        ],
    },
    {
        "task_id": "occ2-08",
        "title": "signing vault",
        "worker": "a",
        "holdout": False,
        "deps": [],
        "expiry": True,
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("sign the OCC-2 artifacts for the internal registry")],
                "next_actions": ["request vault approval", "sign each artifact digest"],
            },
            {
                "kind": "BLOCKED",
                "status": "blocked",
                "findings": [
                    finding("the signing key vault is sealed pending policy approval", 1.0, ["E1"]),
                    finding("no artifacts can be signed until the vault unseals", None, []),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-08-vault-status.txt", verified=True),
                ],
                "next_actions": ["request vault approval from the release manager"],
            },
            {
                "kind": "CANCELLED",
                "status": "cancelled",
                "findings": [
                    finding("artifact signing was cancelled; the registry now accepts unsigned internal builds", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "commit", "c31e0a8b4", verified=True),
                ],
                "next_actions": ["archive the signing branch"],
            },
        ],
    },
    {
        "task_id": "occ2-09",
        "title": "telegram parity",
        "worker": "e",
        "holdout": True,
        "deps": [],
        "parent": "t77",
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("map the parity harness fields onto the telegram transport")],
                "next_actions": ["list the 22 harness fields", "write the mapping table"],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the parity harness maps 18 of 22 fields", 0.7, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-09-parity.log", verified=False),
                ],
            },
            {
                "kind": "REQUEST_DETAIL",
                "status": "in_progress",
                "sender": WORKERS["e"],
                "recipient": WORKERS["f"],
                "findings": [
                    finding("request the upstream API changelog entries for the four unmapped fields", None, []),
                ],
                "next_actions": ["reply with the changelog links"],
            },
            {
                "kind": "CANCELLED",
                "status": "cancelled",
                "findings": [
                    finding("parity work was cancelled because the upstream API changed", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "commit", "8c31d0af2", verified=True),
                ],
                "next_actions": ["archive the branch improve/telegram-parity"],
            },
        ],
    },
    {
        "task_id": "occ2-10",
        "title": "race window investigation",
        "worker": "a",
        "holdout": False,
        "deps": ["t41"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("investigate the reported race window in the session token refresh path")],
                "next_actions": ["read the refresh path", "attempt a reproduction"],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the refresh mutex is held across an await point, widening the race window", 0.75, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "src", "crates/pi-coding-agent/src/core/auth_storage.rs",
                             line_range=(218, 235), revision="ce8480d59",
                             digest="0ec5bee4d4abaec6d8e81ebdd5c6b81ac6ecaab1330ed3dd6775f87250e267a0",
                             verified=True),
                ],
                "next_actions": ["write a stress reproduction"],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("the race window is confirmed by reading but not reproducible with the current regression test", 0.9, ["E1", "E2"]),
                ],
                "evidence": [
                    evidence("E1", "src", "crates/pi-coding-agent/src/core/auth_storage.rs",
                             line_range=(218, 235), revision="ce8480d59", verified=True),
                    evidence("E2", "log", "logs/occ2-10-refresh-stress.log", verified=False),
                ],
                "artifacts": ["reports/occ2-10-race-window.md"],
                "verification": verification(
                    "the refresh race reproduces under the existing regression test", "fail", "cargo_test",
                    "reports/occ2-10-verify.txt"),
                "next_actions": [
                    "inspect the mutex guarding refresh",
                    "add a regression test for concurrent refresh",
                ],
            },
        ],
    },
]

STORIES.extend([
    {
        "task_id": "occ2-11",
        "title": "stress harness",
        "worker": "c",
        "holdout": True,
        "deps": ["occ2-10"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("raise the stress harness to 500 iterations per configuration")],
                "next_actions": ["run the bad-interleaving configuration", "keep the run log"],
            },
            {
                "kind": "EVIDENCE",
                "status": "done",
                "findings": [],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-11-stress-500.log", verified=True),
                    evidence("E2", "build", "target/occ2-11-stress-build.txt", verified=True),
                ],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("the bad interleaving shows up twice in 500 iterations", 0.85, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-11-stress-500.log", verified=True),
                ],
                "verification": verification(
                    "the stress run completes within the latency budget", "pass", "cargo_test",
                    "reports/occ2-11-verify.txt"),
            },
        ],
    },
    {
        "task_id": "occ2-12",
        "title": "metrics dashboard",
        "worker": "f",
        "holdout": False,
        "deps": ["occ2-04"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("wire the fidelity metrics into the campaign dashboard")],
                "next_actions": ["read the aggregate headline report", "render per-split numbers"],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the dashboard reads the aggregate headline report", 0.8, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-12-dashboard.txt", verified=True),
                ],
            },
            {
                "kind": "CORRECTION",
                "status": "in_progress",
                "findings": [
                    finding("the dashboard showed pass counts, not assertion counts; the metric name is corrected", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "finding", "ACK_OF:1"),
                ],
                "next_actions": ["rename the metric", "backfill the last seven days"],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the corrected metric renders for both splits", None, []),
                ],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("the dashboard now shows assertion counts per split", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "lines", "reports/occ2-12-metrics.md", line_range=(12, 18)),
                ],
                "verification": verification(
                    "the dashboard numbers match the fidelity suite output", "pass", "digest_check",
                    "receipts/occ2-12-digest.txt"),
            },
        ],
    },
    {
        "task_id": "occ2-13",
        "title": "gate migration",
        "worker": "b",
        "holdout": False,
        "deps": ["occ2-03"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("migrate the OCC-1 regression gate to an isolated target directory")],
                "next_actions": ["point CARGO_TARGET_DIR at the isolated directory", "record pass and fail counts"],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("the gate now builds into the isolated target directory", 0.85, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "build", "target/occ2-13-gate-build.txt", verified=True),
                ],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("the gate runs green with 7 passed and 0 failed", 1.0, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-13-gate-run.log", verified=True),
                ],
                "verification": verification(
                    "the occ_comm_fidelity gate is green in the isolated target", "pass", "cargo_test",
                    "reports/occ2-13-verify.txt"),
            },
        ],
    },
    {
        "task_id": "occ2-14",
        "title": "flamegraph capture",
        "worker": "d",
        "holdout": False,
        "deps": [],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("capture a flamegraph of the codec hot loop")],
                "next_actions": ["profile encode and decode", "store the svg artifact"],
            },
            {
                "kind": "EVIDENCE",
                "status": "done",
                "findings": [],
                "evidence": [
                    evidence("E1", "artifact", "target/occ2-14-flamegraph.svg"),
                    evidence("E2", "src", "crates/pi-coding-agent/src/core/occ/occ1.rs",
                             line_range=(122, 180), revision="ce8480d59"),
                ],
                "artifacts": ["target/occ2-14-flamegraph.svg"],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("the hot loop spends 62 percent of samples in digest verification", 0.7, ["E1", "E2"]),
                ],
                "evidence": [
                    evidence("E1", "artifact", "target/occ2-14-flamegraph.svg"),
                    evidence("E2", "src", "crates/pi-coding-agent/src/core/occ/occ1.rs",
                             line_range=(122, 180), revision="ce8480d59"),
                ],
                "verification": verification(
                    "the flamegraph attribute counts sum to the total samples", "pass", "manual_review"),
            },
        ],
    },
    {
        "task_id": "occ2-15",
        "title": "spec document",
        "worker": "e",
        "holdout": True,
        "deps": ["occ2-01"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("write the OCC-2 dialect specification draft")],
                "next_actions": ["draft sections 1 through 6", "leave the grammar section for review"],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("sections 1 through 4 are drafted", 0.7, []),
                ],
            },
            {
                "kind": "RESULT",
                "status": "partial",
                "findings": [
                    finding("the symbol table grammar section remains open pending the fallback design", 0.8, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "lines", "docs/occ2-dialect-spec.md", line_range=(88, 121)),
                ],
                "artifacts": ["docs/occ2-dialect-spec.md"],
                "verification": verification(
                    "the draft matches the implemented prototype", "unknown", "manual_review"),
                "next_actions": ["close the grammar section after the fallback lands"],
            },
        ],
    },
    {
        "task_id": "occ2-16",
        "title": "dependency map",
        "worker": "d",
        "holdout": False,
        "deps": [],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("map the cross-task dependencies for the campaign")],
                "next_actions": ["list every task and its declared deps", "render the graph"],
            },
            {
                "kind": "RESULT",
                "status": "failed",
                "findings": [
                    finding("the dependency graph has 20 nodes and 31 edges, but the ledger lists 33 edges", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-16-depmap.log", verified=False),
                ],
                "artifacts": ["reports/occ2-16-depmap.md"],
                "verification": verification(
                    "the dependency graph matches the campaign ledger", "fail", "manual_review",
                    "reports/occ2-16-verify.txt"),
                "next_actions": ["reconcile the two missing edges with the ledger"],
            },
        ],
    },
    {
        "task_id": "occ2-17",
        "title": "rollback drill",
        "worker": "f",
        "holdout": True,
        "deps": [],
        "parent": "t77",
        "expiry": True,
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("run the dialect rollback drill on the staging gateway")],
                "next_actions": ["confirm the staging window", "measure the rollback time"],
            },
            {
                "kind": "BLOCKED",
                "status": "blocked",
                "findings": [
                    finding("the drill needs the staging gateway, which is under maintenance", 1.0, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-17-staging-status.txt", verified=True),
                ],
                "next_actions": ["hold the drill until the maintenance ends"],
            },
            {
                "kind": "ACK",
                "status": "done",
                "findings": [
                    finding("receipt of the blocked report is confirmed; the drill stays on hold", 1.0, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "finding", "ACK_OF:0"),
                ],
            },
            {
                "kind": "CANCELLED",
                "status": "cancelled",
                "findings": [
                    finding("the drill is cancelled; the staging window closed before maintenance ended", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "commit", "e04b7c911", verified=True),
                ],
            },
        ],
    },
    {
        "task_id": "occ2-18",
        "title": "cross-fleet trial",
        "worker": "c",
        "holdout": False,
        "deps": ["occ2-04"],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("run the compression benchmark on the second gateway fleet")],
                "next_actions": ["clone the harness config", "compare with fleet one"],
            },
            {
                "kind": "PROGRESS",
                "status": "in_progress",
                "findings": [
                    finding("fleet two runs the same 100-iteration harness", 0.8, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-18-fleet2.txt", verified=True),
                ],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("savings hold within one percent of fleet one", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "log", "logs/occ2-18-fleet2.log", verified=True),
                ],
                "verification": verification(
                    "fleet two reproduces the fleet one savings", "pass", "bench_harness",
                    "receipts/occ2-18-bench.txt"),
            },
        ],
    },
    {
        "task_id": "occ2-19",
        "title": "receipt audit",
        "worker": "auditor",
        "holdout": False,
        "deps": [],
        "chain": [
            {
                "kind": "TASK",
                "status": "pending",
                "findings": [finding("audit the verification receipts of the campaign so far")],
                "next_actions": ["list every receipt path", "spot-check five receipts"],
            },
            {
                "kind": "EVIDENCE",
                "status": "done",
                "findings": [],
                "evidence": [
                    evidence("E1", "finding", "ACK_OF:0"),
                    evidence("E2", "tool", "receipts/occ2-19-audit-run.txt", verified=True),
                ],
            },
            {
                "kind": "ACK",
                "status": "done",
                "sender": COORD,
                "recipient": AUDITOR,
                "findings": [
                    finding("receipt of the audit plan is confirmed", 1.0, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "finding", "ACK_OF:0"),
                ],
                "next_actions": ["deliver the audit table by Friday"],
            },
            {
                "kind": "RESULT",
                "status": "done",
                "findings": [
                    finding("all 16 verification receipts resolve; three spot-checks matched byte for byte", 0.95, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "tool", "receipts/occ2-19-audit-run.txt", verified=True),
                ],
                "artifacts": ["reports/occ2-19-receipt-audit.md"],
                "verification": verification(
                    "every receipt path in the corpus resolves on disk", "pass", "manual_review"),
            },
        ],
    },
    {
        "task_id": "occ2-20",
        "title": "archive sweep",
        "worker": "b",
        "holdout": True,
        "deps": [],
        "chain": [
            {
                "kind": "CANCELLED",
                "status": "cancelled",
                "findings": [
                    finding("the archive sweep was cancelled; retention now covers the same ground", 0.9, ["E1"]),
                ],
                "evidence": [
                    evidence("E1", "commit", "5d90cc3aa", verified=True),
                ],
                "next_actions": ["drop the sweep from the campaign board"],
            },
        ],
    },
])


# ---------------------------------------------------------------- composer

def _parse_stamp(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def _format_stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_corpus():
    """Compose the full corpus deterministically; returns fixture-style entries."""
    entries = []
    message_ids = {}  # (task_id, chain_index) -> message_id
    next_id = 201
    base_day = _parse_stamp("2026-10-16T08:00:00Z")

    for story_index, story in enumerate(STORIES):
        task_id = story["task_id"]
        worker_key = story["worker"]
        if worker_key == "auditor":
            worker_identity = AUDITOR
        else:
            worker_identity = WORKERS[worker_key]
        rng = random.Random(derive_seed("story", task_id))
        story_start = base_day + datetime.timedelta(minutes=17 * story_index)

        # First pass: assign message ids so ACK_OF references can resolve.
        for chain_index in range(len(story["chain"])):
            message_ids[(task_id, chain_index)] = "m-%d" % (next_id + chain_index)
        next_id += len(story["chain"])

        moment = story_start
        for chain_index, spec in enumerate(story["chain"]):
            kind = spec["kind"]
            moment = moment + datetime.timedelta(minutes=rng.randint(5, 75))
            payload = {
                "task_id": task_id,
                "kind": kind,
                "status": spec["status"],
                "message_id": message_ids[(task_id, chain_index)],
                "trace_id": "trace-" + task_id,
                "sender_identity": spec.get("sender", COORD if kind == "TASK" else worker_identity),
                "recipient_identity": spec.get("recipient", worker_identity if kind == "TASK" else COORD),
                "findings": [dict(f) for f in spec.get("findings", [])],
                "evidence": [],
                "artifacts": list(spec.get("artifacts", [])),
                "verification": dict(spec["verification"]) if spec.get("verification") else None,
                "dependencies": list(story.get("deps", [])) if kind == "TASK" else list(spec.get("deps", [])),
                "next_actions": list(spec.get("next_actions", [])),
                "sequence": chain_index + 1,
                "timestamp": _format_stamp(moment),
            }
            if story.get("parent") and kind == "TASK":
                payload["parent_task_id"] = story["parent"]

            # Evidence: resolve ACK_OF references and enrich some digests.
            for entry in spec.get("evidence", []):
                entry = dict(entry)
                if entry["locator"].startswith("ACK_OF:"):
                    target = int(entry["locator"].split(":", 1)[1])
                    entry["locator"] = message_ids[(task_id, target)]
                if "digest" not in entry and entry["kind"] in ("src", "artifact") and rng.random() < 0.5:
                    entry["digest"] = rand_hex(rng, 64)
                if "line_range" in entry:
                    entry["line_range"] = list(entry["line_range"])
                payload["evidence"].append(entry)

            # Expiry stamps: TASK messages of flagged stories; one ACK carries one.
            if kind == "TASK" and story.get("expiry"):
                payload["expiry"] = _format_stamp(moment + datetime.timedelta(days=3))
            if (task_id, chain_index) == ("occ2-02", 4):
                payload["expiry"] = _format_stamp(moment + datetime.timedelta(days=1))

            entry = {
                "id": "c%02d" % (len(entries) + 1),
                "holdout": bool(story["holdout"]),
                "plain_english": render_plain(payload),
                "payload": payload,
            }
            entries.append(entry)
    return entries


# ---------------------------------------------------------------- renderer

def _describe_evidence(entry):
    kind = entry["kind"]
    locator = entry["locator"]
    verified = entry.get("verified")
    if kind == "src":
        text = "The relevant lines are in " + locator
        if "line_range" in entry:
            text += ", lines " + str(entry["line_range"][0]) + " to " + str(entry["line_range"][1])
        if "revision" in entry:
            text += ", read at revision " + entry["revision"]
        if "digest" in entry:
            text += "; the sha256 digest of the file is " + entry["digest"]
        if verified is True:
            text += " and I confirmed the digest again after the run so the line numbers are trustworthy"
        elif verified is False:
            text += ", though that copy has not been independently re-checked"
        return text + "."
    if kind == "commit":
        return "This is backed by commit " + locator + (", which I verified" if verified is True else "") + "."
    if kind == "log":
        return ("The supporting log is at " + locator
                + (" and I re-checked it after the run" if verified is True
                   else ", though it has not been independently re-checked") + ".")
    if kind == "build":
        return "The build result is recorded at " + locator + "."
    if kind == "tool":
        return "The tool receipt lives at " + locator + "."
    if kind == "artifact":
        if "digest" in entry:
            return "The artifact at " + locator + " carries sha256 digest " + entry["digest"] + "."
        return "The artifact at " + locator + " was recorded."
    if kind == "lines":
        text = "The exact change is pinned by line range "
        if "line_range" in entry:
            text += str(entry["line_range"][0]) + " to " + str(entry["line_range"][1]) + " in " + locator
        else:
            text += "in " + locator
        return text + "."
    if kind == "finding":
        return "This follows from the earlier message " + locator + "."
    return "Evidence " + entry.get("id", "?") + " points at " + locator + "."


def render_plain(payload):
    """Deterministic plain-English rendering of one payload (OCC-0 style)."""
    task = payload["task_id"]
    kind = payload["kind"]
    status = payload["status"]
    sentences = []
    if kind == "TASK":
        sentences.append("Task " + task + " is a new assignment and is currently " + status + ".")
    elif kind == "PROGRESS":
        sentences.append("Task " + task + " is in progress and this is an update.")
    elif kind == "RESULT":
        if status == "done":
            sentences.append("Task " + task + " is finished and I am reporting the result now.")
        elif status == "partial":
            sentences.append("Task " + task + " is partially complete and scope remains.")
        else:
            sentences.append("Task " + task + " finished unsuccessfully and I am reporting that now.")
    elif kind == "EVIDENCE":
        sentences.append("Task " + task + " is reported with evidence only and no new claims.")
    elif kind == "REQUEST_DETAIL":
        sentences.append("Task " + task + " needs additional detail from the peer.")
    elif kind == "ACK":
        sentences.append("Task " + task + " is acknowledged: receipt of the earlier message is confirmed.")
    elif kind == "CORRECTION":
        sentences.append("Task " + task + " has a correction to an earlier claim.")
    elif kind == "BLOCKED":
        sentences.append("Task " + task + " is blocked and cannot proceed right now.")
    else:
        sentences.append("Task " + task + " was cancelled before completion.")

    by_id = {entry["id"]: entry for entry in payload["evidence"]}
    for index, item in enumerate(payload["findings"]):
        text = "Point " + str(index + 1) + ": " + item["statement"]
        confidence = item.get("confidence")
        if confidence is not None:
            text += ", and I am about " + str(int(round(confidence * 100))) + " percent sure of this"
        sentences.append(text + ".")
        for eid in item["evidence"]:
            if eid in by_id:
                sentences.append(_describe_evidence(by_id[eid]))

    linked = {eid for item in payload["findings"] for eid in item["evidence"]}
    for entry in payload["evidence"]:
        if entry["id"] not in linked:
            sentences.append(_describe_evidence(entry))

    if payload["artifacts"]:
        sentences.append("The artifacts produced are " + ", ".join(payload["artifacts"]) + ".")
    verification = payload.get("verification")
    if verification:
        text = ("To be explicit about verification: the claim that " + verification["claim"]
                + " is currently marked " + verification["status"]
                + "; the method was " + verification["method"])
        if verification.get("receipt"):
            text += " and the receipt lives at " + verification["receipt"]
        sentences.append(text + ".")
    if payload["dependencies"]:
        sentences.append("This work depended on " + ", ".join(payload["dependencies"]) + " being finished first.")
    if payload["next_actions"]:
        sentences.append("Next actions, in priority order: " + "; ".join(payload["next_actions"]) + ".")
    return " ".join(sentences)


# ---------------------------------------------------------------- occ1 port

_OCC1_FIELD_ORDER_NOTE = "mirrors AgentCommMessage::to_occ1 in contract.rs"


def to_occ1_fields(payload):
    """Typed payload -> ordered OCC-1 KEY=VALUE fields (pre-wire text)."""
    fields = []

    def push(key, value):
        fields.append((key, str(value)))

    push("MSGID", payload["message_id"])
    push("TASK", payload["task_id"])
    if payload.get("parent_task_id"):
        push("PARENT", payload["parent_task_id"])
    push("TRACE", payload["trace_id"])
    push("SENDER", payload["sender_identity"])
    push("RECIPIENT", payload["recipient_identity"])
    push("KIND", payload["kind"])
    push("STATUS", payload["status"])
    push("SEQ", payload["sequence"])
    for index, item in enumerate(payload["findings"]):
        n = index + 1
        push("F%d" % n, item["statement"])
        if item.get("confidence") is not None:
            push("FC%d" % n, "%.3f" % item["confidence"])
        if item["evidence"]:
            push("FE%d" % n, ",".join(item["evidence"]))
    for index, entry in enumerate(payload["evidence"]):
        n = index + 1
        push("E%d" % n, entry["kind"] + ":" + entry["locator"])
        if "line_range" in entry:
            push("EL%d" % n, "%d-%d" % (entry["line_range"][0], entry["line_range"][1]))
        if "digest" in entry:
            push("ED%d" % n, entry["digest"])
        if "revision" in entry:
            push("ER%d" % n, entry["revision"])
        if entry.get("verified") is not None:
            push("EV%d" % n, "pass" if entry["verified"] else "fail")
    for index, artifact in enumerate(payload["artifacts"]):
        push("ART%d" % (index + 1), artifact)
    verification = payload.get("verification")
    if verification:
        push("V", verification["claim"])
        push("VS", verification["status"])
        push("VM", verification["method"])
        if verification.get("receipt"):
            push("VR", verification["receipt"])
    for index, dependency in enumerate(payload["dependencies"]):
        push("DEP%d" % (index + 1), dependency)
    for action in payload["next_actions"]:
        push("NEXT", action)
    push("TS", payload["timestamp"])
    if payload.get("expiry"):
        push("EXP", payload["expiry"])
    return fields


_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def occ1_encode(payload):
    """Encode to the wire grammar; raises on any bound violation."""
    fields = to_occ1_fields(payload)
    if len(fields) > 128:
        raise ValueError("field count %d exceeds 128" % len(fields))
    lines = ["OCC/1"]
    for key, value in fields:
        if not _KEY_RE.match(key) or len(key.encode("utf-8")) > 32:
            raise ValueError("bad key %r" % key)
        raw = value.encode("utf-8")
        if len(raw) > 4096:
            raise ValueError("value of %s exceeds 4096 bytes" % key)
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("control character in value of %s" % key)
        lines.append(key + "=" + value)
    text = "\n".join(lines)
    if len(text.encode("utf-8")) > 64 * 1024:
        raise ValueError("message exceeds 64 KiB")
    return text


# ---------------------------------------------------------------- validation

_ALLOWED_PAYLOAD_KEYS = {
    "task_id", "kind", "status", "message_id", "trace_id", "sender_identity",
    "recipient_identity", "findings", "evidence", "artifacts", "verification",
    "dependencies", "next_actions", "sequence", "timestamp", "parent_task_id",
    "expiry",
}


def validate_entry(entry, seen_message_ids):
    """Mirror of the contract invariants; returns a list of violations."""
    problems = []
    payload = entry["payload"]
    if set(payload) - _ALLOWED_PAYLOAD_KEYS:
        problems.append("unexpected payload keys: %s" % sorted(set(payload) - _ALLOWED_PAYLOAD_KEYS))
    if entry["id"] != "c%02d" % int(entry["id"][1:]):
        problems.append("bad id")
    if payload["kind"] not in KINDS:
        problems.append("unknown kind %r" % payload["kind"])
    if payload["status"] not in STATUSES:
        problems.append("unknown status %r" % payload["status"])
    if not payload["task_id"] or any(ord(ch) < 32 for ch in payload["task_id"]):
        problems.append("bad task_id")
    if payload["message_id"] in seen_message_ids:
        problems.append("duplicate message_id %r" % payload["message_id"])
    seen_message_ids.add(payload["message_id"])
    if payload["kind"] == "EVIDENCE" and not payload["evidence"]:
        problems.append("EVIDENCE message without evidence")
    evidence_ids = [e["id"] for e in payload["evidence"]]
    if len(evidence_ids) != len(set(evidence_ids)):
        problems.append("duplicate evidence ids")
    for item in payload["findings"]:
        if set(item) - {"statement", "confidence", "evidence"}:
            problems.append("unexpected finding keys: %s" % sorted(set(item) - {"statement", "confidence", "evidence"}))
        confidence = item.get("confidence")
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            problems.append("confidence out of range: %r" % confidence)
        for eid in item["evidence"]:
            if eid not in evidence_ids:
                problems.append("finding references unknown evidence %r" % eid)
    for ev in payload["evidence"]:
        if ev["kind"] not in EVIDENCE_KINDS:
            problems.append("unknown evidence kind %r" % ev["kind"])
        if "line_range" in ev and len(ev["line_range"]) != 2:
            problems.append("bad line_range")
    verification = payload.get("verification")
    if verification is not None:
        if verification["status"] not in VERIFICATION_STATUSES:
            problems.append("unknown verification status %r" % verification["status"])
    try:
        _parse_stamp(payload["timestamp"])
    except ValueError:
        problems.append("bad timestamp %r" % payload["timestamp"])
    if payload.get("expiry"):
        try:
            _parse_stamp(payload["expiry"])
        except ValueError:
            problems.append("bad expiry %r" % payload["expiry"])
    if not entry["plain_english"] or not entry["plain_english"].isascii():
        problems.append("plain_english is empty or not ASCII")
    try:
        occ1_encode(payload)
    except ValueError as error:
        problems.append("not OCC-1 encodable: %s" % error)
    if payload["sequence"] < 1:
        problems.append("bad sequence")
    return problems


def validate_corpus(entries):
    problems = []
    seen_message_ids = set()
    for entry in entries:
        for problem in validate_entry(entry, seen_message_ids):
            problems.append("%s: %s" % (entry["id"], problem))
    return problems


# ---------------------------------------------------------------- stats

def corpus_stats(entries):
    from collections import Counter
    kinds = Counter()
    statuses = Counter()
    evidence_kinds = Counter()
    verification_statuses = Counter()
    plain_bytes = 0
    json_bytes = 0
    occ1_bytes = 0
    findings_total = 0
    max_occ1 = 0
    for entry in entries:
        payload = entry["payload"]
        kinds[payload["kind"]] += 1
        statuses[payload["status"]] += 1
        for ev in payload["evidence"]:
            evidence_kinds[ev["kind"]] += 1
        if payload["verification"]:
            verification_statuses[payload["verification"]["status"]] += 1
        findings_total += len(payload["findings"])
        plain_bytes += len(entry["plain_english"].encode("utf-8"))
        json_bytes += len(json.dumps(payload, sort_keys=True).encode("utf-8"))
        occ1 = len(occ1_encode(payload).encode("utf-8"))
        occ1_bytes += occ1
        max_occ1 = max(max_occ1, occ1)
    holdout = sum(1 for entry in entries if entry["holdout"])
    return {
        "count": len(entries),
        "holdout": holdout,
        "train": len(entries) - holdout,
        "kinds": dict(sorted(kinds.items())),
        "statuses": dict(sorted(statuses.items())),
        "evidence_kinds": dict(sorted(evidence_kinds.items())),
        "verification_statuses": dict(sorted(verification_statuses.items())),
        "findings_total": findings_total,
        "plain_bytes": plain_bytes,
        "json_bytes": json_bytes,
        "occ1_bytes": occ1_bytes,
        "max_occ1_bytes": max_occ1,
    }


# ---------------------------------------------------------------- main

def main():
    entries = build_corpus()
    problems = validate_corpus(entries)
    if problems:
        for problem in problems:
            sys.stderr.write("CORPUS VALIDATION FAILURE %s\n" % problem)
        return 1
    if build_corpus() != entries:
        sys.stderr.write("CORPUS VALIDATION FAILURE: generator is not deterministic\n")
        return 1

    stats = corpus_stats(entries)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "msgs_corpus.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(entries, handle, indent=2, ensure_ascii=True)
        handle.write("\n")

    print("wrote %s (%d messages, %d holdout / %d train)" % (path, stats["count"], stats["holdout"], stats["train"]))
    print("kinds: %s" % json.dumps(stats["kinds"]))
    print("statuses: %s" % json.dumps(stats["statuses"]))
    print("evidence kinds: %s" % json.dumps(stats["evidence_kinds"]))
    print("verification: %s" % json.dumps(stats["verification_statuses"]))
    print("findings total: %d" % stats["findings_total"])
    print("bytes plain=%d json=%d occ1=%d (max occ1 message %d bytes)"
          % (stats["plain_bytes"], stats["json_bytes"], stats["occ1_bytes"], stats["max_occ1_bytes"]))
    print("validation: all contract invariants and OCC-1 encodability checks passed; determinism verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
