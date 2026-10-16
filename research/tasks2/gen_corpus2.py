#!/usr/bin/env python3
"""Deterministic expanded message corpus generator (OCC-2 optimization campaign).

Builds msgs_corpus2_evolve.json / msgs_corpus2_dev.json / msgs_corpus2_holdout.json
per research/tasks2/manifest.json (frozen BEFORE any generation; see the manifest
for seed bases, split targets, story roster and the holdout discipline).

Each entry extends the v1 fixture shape ({id, holdout, plain_english, payload})
with split / story / scenarios / qa:

  [{"id": "e2-e01-01", "split": "evolve", "story": "occ2b-e01",
    "scenarios": ["parent_to_child"], "holdout": false,
    "plain_english": "...", "payload": {...v1 shape...},
    "qa": [{"q": "...", "a": "...", "family": "A"}]}, ...]

Payload fields follow the v1 fixture exactly (contract-compatible with
crates/pi-coding-agent/tests/fixtures/occ/scenarios.json). The payload shape
is NEVER modified by this generator; only new top-level entry keys are added.

Phases (holdout is generated LAST and independently):
  --phase evolvedev   writes msgs_corpus2_evolve.json + msgs_corpus2_dev.json
  --phase holdout     writes msgs_corpus2_holdout.json
  --phase stats       prints stats for whatever files exist (no writes)

Determinism: all randomness flows from sha256-derived seeds; no wall-clock
input. The self-check validates every message against the contract invariants
(mirroring contract.rs validate()), round-trips every payload through the
kernel OCC-1 codec (via research/symbolic_comm/contract.py, imported
read-only), checks QA gold answers with research/tasks/checkers.py, and
verifies a second build is identical.

Read-only imports (frozen prior-campaign evidence, never modified):
  research/tasks/gen_corpus.py     render_plain + finding/evidence/verification helpers
  research/tasks/checkers.py       QA family checkers
  research/symbolic_comm/contract.py typed contract + round-trip oracle
  prime-agent-runtime/src/rlm/occ1.py kernel OCC-1 codec (loaded by contract.py)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import random
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

for _p in ("research/tasks", "research/symbolic_comm"):
    _full = os.path.join(_ROOT, _p)
    if _full not in sys.path:
        sys.path.insert(0, _full)

import gen_corpus as GC          # v1 helpers (render_plain, finding, evidence, ...)
import contract as SYM           # typed contract + kernel-codec round-trip oracle

SEED_BASE_ED = "occ2opt-corpus2-ed-v1"
SEED_BASE_HO = "occ2opt-corpus2-ho-v1"
BASE_DAY = datetime.datetime(2026, 11, 2, 8, 0, 0, tzinfo=datetime.timezone.utc)
MANIFEST_PATH = os.path.join(_HERE, "manifest.json")

SCENARIO_TYPES = (
    "parent_to_child", "child_to_parent", "sibling_handoff", "multi_hop",
    "large_findings", "small_findings", "contradictory_evidence",
    "source_code_evidence", "failed_tests", "successful_tests",
    "partial_completion", "cancellation", "new_dependencies", "state_updates",
    "missing_evidence", "multiple_source_refs", "task_reassignment",
    "long_history", "repeated_messages", "model_switching",
)
SCENARIO_SET = set(SCENARIO_TYPES)
SPLITS = ("evolve", "dev", "holdout")
SPLIT_TARGETS = {"evolve": 90, "dev": 60, "holdout": 54}

COORD = "daemon://coordinator"
AUDITOR = "agent://security-auditor"
BENCH = "agent://bench-runner"
WORKERS = {
    "a": "agent://worker-a", "b": "agent://worker-b", "c": "agent://worker-c",
    "d": "agent://worker-d", "e": "agent://worker-e", "f": "agent://worker-f",
}
SPECIAL = {"auditor": AUDITOR, "bench": BENCH}

HEX_DIGITS = "0123456789abcdef"
_QA_FAMILIES = ("A", "D", "E")


def derive_seed2(base, *parts):
    joined = "|".join((base,) + tuple(parts))
    return int(hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16], 16)


def rand_hex(rng, n):
    return "".join(rng.choice(HEX_DIGITS) for _ in range(n))


def worker_identity(key):
    if key in SPECIAL:
        return SPECIAL[key]
    return WORKERS[key]


# story spec helpers -------------------------------------------------------

def finding(statement, confidence=None, evidence=None):
    return {"statement": statement, "confidence": confidence, "evidence": evidence or []}


def evidence(eid, kind, locator, **optional):
    return GC.evidence(eid, kind, locator, **optional)


def verification(claim, status, method, receipt=None):
    return GC.verification(claim, status, method, receipt)


def msg(kind, status, scenarios=(), findings=None, evidence=None,
        artifacts=None, verification=None, next_actions=None,
        sender=None, recipient=None, deps=None, task=None, expiry=False):
    """One message spec inside a story chain."""
    spec = {
        "kind": kind,
        "status": status,
        "scenarios": list(scenarios),
        "findings": findings or [],
        "evidence": evidence or [],
        "artifacts": artifacts or [],
        "verification": verification,
        "next_actions": next_actions or [],
    }
    if sender is not None:
        spec["sender"] = sender
    if recipient is not None:
        spec["recipient"] = recipient
    if deps is not None:
        spec["deps"] = list(deps)
    if task is not None:
        spec["task"] = task
    if expiry:
        spec["expiry"] = True
    return spec


def story(task_id, title, split, worker, chain, pace="normal", deps=None,
          parent=None, reassign=None):
    """Story spec. `reassign` = (new_task_id, new_worker) for reassignment stories."""
    return {
        "task_id": task_id,
        "title": title,
        "split": split,
        "worker": worker,
        "chain": chain,
        "pace": pace,
        "deps": list(deps or []),
        "parent": parent,
        "reassign": reassign,
    }


# ============================================================ EVOLVE stories
# 12 stories, 90 messages. Content mirrors the OCC-2 optimization campaign.

STORIES_EVOLVE = [

    story(
        "occ2b-e01", "codebook amortization rollout", "evolve", "b",
        deps=["t77"],
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("amortize the OCC-2 codebook injection across a session instead of rebuilding it for every message")],
                next_actions=[
                    "profile the per-message injection site",
                    "design the session-scoped codebook cache",
                    "keep OCC-1 sessions byte-identical",
                ]),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[
                    finding("the per-message codebook injection now reads from a session-scoped cache", 0.8, ["E1"]),
                ],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/agent_messages.rs",
                                   line_range=(804, 808), revision="1863b5dea")],
                next_actions=["wire cache invalidation on dialect change", "measure the token delta"]),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[
                    finding("cache invalidation is wired and the stored dual payload no longer re-bakes the codebook per message", 0.75, ["E1"]),
                ],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/agent_messages.rs",
                                   line_range=(965, 1011), revision="1863b5dea")],
                next_actions=["run the fidelity gates on the rebased tree"]),
            msg("ACK", "in_progress", [],
                findings=[finding("receipt of the cache design is confirmed; proceed with the measurements")],
                sender=COORD, recipient="b"),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[
                    finding("token measurements show the session-scoped codebook saves roughly 175 tokens per message from the third message onward", 0.9, ["E1"]),
                ],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e01-token-delta.txt", verified=True)],
                next_actions=[]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "artifact", "research/occ2opt/codebook-amortization-report.md"),
                    evidence("E2", "build", "reports/occ2b-e01-build.txt", verified=True),
                ]),
            msg("REQUEST_DETAIL", "in_progress", [],
                findings=[finding("the final gate receipts are needed before the task can close")],
                sender=COORD, recipient="b",
                next_actions=["attach the gate log", "attach the build log"]),
            msg("RESULT", "done", ["child_to_parent", "successful_tests"],
                findings=[
                    finding("the session-scoped codebook cache is active and the token cost per message is flat from the third message", 0.95, ["E1", "E2"]),
                ],
                evidence=[
                    evidence("E1", "tool", "reports/occ2b-e01-gates.txt", verified=True),
                    evidence("E2", "build", "reports/occ2b-e01-build.txt", verified=True),
                ],
                artifacts=["research/occ2opt/codebook-amortization-report.md"],
                verification=verification("the codebook cache keeps all fidelity gates green", "pass", "cargo_test",
                                          "reports/occ2b-e01-gates.txt")),
        ]),

    story(
        "occ2b-e02", "fidelity gate triage", "evolve", "c",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("triage the failing occ2_comm_fidelity gate on the rebased tree")],
                next_actions=["reproduce the failures", "split regressions from environment noise", "report the split"]),
            msg("PROGRESS", "failed", ["failed_tests", "multiple_source_refs"],
                findings=[
                    finding("the fidelity gate is red after the rebase", None, []),
                    finding("three of twelve scenarios fail the dual round trip", 0.85, ["E1", "E2"]),
                ],
                evidence=[
                    evidence("E1", "build", "reports/occ2b-e02-gate-run.txt", verified=True),
                    evidence("E2", "src", "crates/pi-coding-agent/tests/occ2_comm_fidelity.rs",
                             line_range=(40, 96), revision="1863b5dea"),
                ],
                next_actions=["compare each failure against the base commit"]),
            msg("PROGRESS", "in_progress", ["source_code_evidence"],
                findings=[
                    finding("one failure is a real regression introduced by the mid-edit tree and two are environment failures from a stale target directory", 0.7, ["E1"]),
                ],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/agent_messages.rs",
                                   line_range=(965, 1025), revision="1863b5dea")],
                next_actions=["rebuild in a fresh target dir", "rerun the gate"]),
            msg("CORRECTION", "in_progress", [],
                findings=[
                    finding("the earlier count of three failures was wrong; the fresh target directory run shows one real regression and two pre-existing environment failures", 0.9, ["E1"]),
                ],
                evidence=[evidence("E1", "log", "reports/occ2b-e02-rerun.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the regression is narrowed to the prompt body renderer and a fix is in flight", 0.8, [])],
                next_actions=["land the fix", "rerun the full gate"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "commit", "9c41f0ab2", verified=True),
                    evidence("E2", "build", "reports/occ2b-e02-gate-green.txt", verified=True),
                ]),
            msg("RESULT", "done", ["child_to_parent", "multiple_source_refs"],
                findings=[
                    finding("the gate is green again; one regression was real and is fixed, the other two failures were environment noise", 0.95, ["E1", "E2"]),
                ],
                evidence=[
                    evidence("E1", "build", "reports/occ2b-e02-gate-green.txt", verified=True),
                    evidence("E2", "commit", "9c41f0ab2", verified=True),
                ],
                verification=verification("the fidelity gate passes 12 of 12 scenarios", "pass", "cargo_test",
                                           "reports/occ2b-e02-gate-green.txt")),
        ]),

    story(
        "occ2b-e03", "cross-worker wire migration", "evolve", "d",
        deps=["occ2b-e01"],
        chain=[
            msg("TASK", "pending", ["parent_to_child", "new_dependencies"],
                findings=[finding("migrate the remaining OCC-1 call sites to the shared wire helpers")],
                next_actions=["survey the call sites", "split the work across workers", "keep the wire bytes stable"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the survey found fourteen call sites; nine are in the daemon path and five in the session path", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e03-callgraph.txt", verified=True)],
                next_actions=["split the sites between workers"]),
            msg("PROGRESS", "in_progress", ["sibling_handoff"],
                findings=[
                    finding("handing off the delta encoding call sites to worker e", None, []),
                    finding("the consolidation sites stay with me", None, []),
                ],
                sender="d", recipient="e",
                next_actions=["acknowledge the handoff", "start with delta.rs"]),
            msg("ACK", "in_progress", ["sibling_handoff", "multi_hop"],
                findings=[finding("the handoff is acknowledged and the delta sites are queued after my current task")],
                evidence=[evidence("E1", "finding", "ACK_OF:2")],
                sender="e", recipient="d"),
            msg("PROGRESS", "in_progress", ["multi_hop"],
                findings=[finding("the delta encoding sites are migrated with no wire byte drift", 0.9, ["E1"])],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/delta.rs",
                                  line_range=(14, 61), revision="1863b5dea")],
                sender="e", recipient=COORD),
            msg("REQUEST_DETAIL", "in_progress", [],
                findings=[finding("the remaining site list is needed to schedule the review")],
                sender=COORD, recipient="d"),
            msg("PROGRESS", "in_progress", ["multi_hop", "new_dependencies"],
                findings=[
                    finding("the remaining sites are listed and a new dependency on the session settings work was discovered", 0.8, ["E1"]),
                ],
                evidence=[evidence("E1", "artifact", "research/occ2opt/wire-migration-sites.md")],
                deps=["occ2b-e06"],
                next_actions=["wait for the session settings design"]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("all fourteen call sites now use the shared wire helpers and the wire bytes are stable", 0.95, ["E1"])],
                evidence=[evidence("E1", "commit", "b7d902e41", verified=True)],
                verification=verification("wire bytes are identical before and after the migration", "pass", "cargo_test",
                                          "reports/occ2b-e03-wire-diff.txt")),
        ]),

    story(
        "occ2b-e04", "delta encoding revision", "evolve", "a",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("revise the delta encoding so small updates stay cheaper than a full re-encode")],
                next_actions=["benchmark the current encoding", "design the incremental path", "guard the size bound"]),
            msg("PROGRESS", "in_progress", ["source_code_evidence"],
                findings=[finding("the incremental path is drafted on top of the existing EADD wire", 0.8, ["E1"])],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/delta.rs",
                                  line_range=(88, 140), revision="1863b5dea")],
                next_actions=["measure against the full re-encode"]),
            msg("PROGRESS", "in_progress", ["contradictory_evidence"],
                findings=[
                    finding("the delta path beats the full re-encode on the fixture corpus", 0.7, ["E1"]),
                    finding("the same benchmark on the live traffic profile shows the delta path losing by eight percent", 0.65, ["E2"]),
                ],
                evidence=[
                    evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/delta.rs",
                             line_range=(88, 140), revision="1863b5dea"),
                    evidence("E2", "src", "crates/pi-coding-agent/src/core/occ/delta.rs",
                             line_range=(88, 140), revision="9c41f0ab2"),
                ]),
            msg("CORRECTION", "in_progress", ["contradictory_evidence"],
                findings=[finding("the conflicting measurements came from two revisions; the newer revision contains the size guard and is authoritative", 0.9, ["E1"])],
                evidence=[evidence("E1", "commit", "9c41f0ab2", verified=True)]),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[finding("the size guard is in and the delta path now wins on both profiles for updates under forty percent of the message", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e04-bench.txt", verified=True)],
                next_actions=["run the fidelity gates"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "build", "reports/occ2b-e04-gates.txt", verified=True),
                    evidence("E2", "lines", "crates/pi-coding-agent/src/core/occ/delta.rs",
                             line_range=(140, 158)),
                ]),
            msg("RESULT", "partial", ["child_to_parent", "partial_completion"],
                findings=[finding("the incremental delta path is landed and guarded; the open question about the crossover threshold on mixed traffic is deferred", 0.8, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-e04-gates.txt", verified=True)],
                next_actions=["measure the crossover threshold on mixed traffic"]),
        ]),

    story(
        "occ2b-e05", "strike ledger resurrection", "evolve", "f",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("resurrect the strike ledger so repeated wire faults disable a session dialect before corruption spreads")],
                next_actions=["find why only tests call the ledger", "wire it into the decode path", "add the three-strike gate"]),
            msg("PROGRESS", "in_progress", ["large_findings"],
                findings=[
                    finding("the ledger struct exists but the production decode path bypasses it", 0.9, []),
                    finding("the test suite exercises the ledger through a direct call", 0.9, []),
                    finding("the decode path is the only production entry point for wire faults", 0.8, []),
                    finding("the ledger needs a session-scoped home to survive across messages", 0.75, []),
                    finding("the three-strike rule is already specified in the contract tests", 0.85, []),
                    finding("no production state currently persists strike counts", 0.95, []),
                ],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/occ2.rs",
                                   line_range=(200, 260), revision="1863b5dea")],
                next_actions=["collect the decode fault receipts"]),
            msg("PROGRESS", "in_progress", ["missing_evidence"],
                findings=[finding("the ledger wiring is drafted but no production fault receipt exists yet to prove the disable path", 0.6, [])],
                next_actions=["collect the receipts before claiming the ledger is live"]),
            msg("REQUEST_DETAIL", "in_progress", ["missing_evidence"],
                findings=[finding("the fault receipts from the parallel lane are needed to validate the disable path")],
                sender=COORD, recipient="f"),
            msg("BLOCKED", "blocked", ["missing_evidence"],
                findings=[finding("the task is blocked until the parallel lane publishes its fault receipts", None, [])],
                next_actions=["wait for the receipts"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the receipts arrived and the task is unblocked")],
                sender=COORD, recipient="f"),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[finding("the disable path is validated against the published receipts and the ledger is live in the decode path", 0.9, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e05-disable-path.txt", verified=True)],
                next_actions=["run the three-strike gate"]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the strike ledger is live in the decode path and the three-strike gate is green", 0.95, ["E1"])],
                evidence=[evidence("E1", "commit", "d51cc7e90", verified=True)],
                verification=verification("the three-strike session disable works on injected faults", "pass", "cargo_test",
                                          "reports/occ2b-e05-strike-gate.txt")),
        ]),

    story(
        "occ2b-e06", "session settings daemonization", "evolve", "b",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("move the /occ session settings from client-local state to daemon-owned state")],
                next_actions=["design the capability handshake", "spec the get and set RPCs", "keep the client profile as fallback"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the design follows the existing session-plane RPC pattern", 0.8, ["E1"])],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/modes/daemon/daemon_protocol.rs",
                                  line_range=(1200, 1260), revision="1863b5dea")],
                next_actions=["implement the get path first"]),
            msg("PROGRESS", "in_progress", ["multiple_source_refs"],
                findings=[finding("the get path reads daemon-owned state and echoes it back with an authoritative flag", 0.85, ["E1", "E2", "E3"])],
                evidence=[
                    evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/session_settings.rs",
                             line_range=(1, 60), revision="1863b5dea"),
                    evidence("E2", "src", "crates/pi-coding-agent/src/modes/daemon/daemon_mode.rs",
                             line_range=(9820, 9900), revision="1863b5dea"),
                    evidence("E3", "tool", "receipts/occ2b-e06-get-path.txt", verified=True),
                ]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "log", "reports/occ2b-e06-handshake.txt", verified=True),
                    evidence("E2", "artifact", "research/occ2opt/daemon-settings-design.md"),
                ]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the set path is drafted but the durable write needs the state store layout", 0.7, [])],
                next_actions=["define the durable layout"]),
            msg("RESULT", "partial", ["partial_completion", "child_to_parent"],
                findings=[finding("the get path is done and the set path remains; the client-local profile stays the fallback until the set path lands", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e06-get-path.txt", verified=True)],
                verification=verification("the daemon-owned get path returns authoritative settings", "unknown", "manual_probe",
                                         None),
                next_actions=["implement the set path", "add the migration for client-local profiles"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the partial scope is acknowledged; the set path is scheduled for the next phase")],
                sender=COORD, recipient="b"),
        ]),

    story(
        "occ2b-e07", "benchmark corpus expansion", "evolve", "b",
        pace="long",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("expand the message corpus to roughly three times its size with new evolve dev and holdout splits")],
                next_actions=["freeze the generation manifest", "author the story roster", "generate the holdout last"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the manifest is frozen with seed bases and split targets before any generation", 0.95, ["E1"])],
                evidence=[evidence("E1", "artifact", "research/tasks2/manifest.json")],
                next_actions=["author the evolve and dev stories"]),
            msg("PROGRESS", "in_progress", ["small_findings"],
                findings=[finding("the evolve and dev stories are authored", 0.9, [])],
                next_actions=["generate the splits"]),
            msg("PROGRESS", "in_progress", ["repeated_messages"],
                findings=[finding("the evolve and dev stories are authored", 0.9, [])],
                next_actions=["generate the splits"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the duplicate update was received; consolidation applies")],
                sender=COORD, recipient="b"),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the evolve and dev files are generated and validated", 0.9, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-e07-selftest.txt", verified=True)],
                next_actions=["generate the holdout split last"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "artifact", "research/tasks2/msgs_corpus2_evolve.json"),
                    evidence("E2", "artifact", "research/tasks2/msgs_corpus2_dev.json"),
                ]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the holdout split is generated from an independent seed base after evolve and dev are frozen", 0.9, [])],
                next_actions=["run the full self-test"]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the expanded corpus has 204 messages across three splits with a green deterministic self-test", 0.95, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-e07-selftest.txt", verified=True)],
                verification=verification("all corpus invariants and round trips pass", "pass", "selftest",
                                          "reports/occ2b-e07-selftest.txt")),
        ]),

    story(
        "occ2b-e08", "model gateway migration", "evolve", "e",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("migrate the model gateway calls to the new endpoint without losing the running session state")],
                next_actions=["snapshot the session state", "cut over the endpoint", "verify the trace continuity"]),
            msg("PROGRESS", "in_progress", ["model_switching"],
                findings=[
                    finding("the session switched from glm-5.3 to deepseek-v4-pro mid-task; the trace continues on the new model", 0.9, ["E1"]),
                    finding("the context was re-read from the durable transcript so no work was lost", 0.85, []),
                ],
                evidence=[evidence("E1", "log", "reports/occ2b-e08-model-switch.txt", verified=True)],
                next_actions=["verify the trace continuity"]),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[finding("the trace id is unchanged across the switch and the sequence numbers continue without a gap", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-e08-trace-check.txt", verified=True)]),
            msg("CORRECTION", "in_progress", [],
                findings=[finding("the earlier statement that no work was lost was too strong; one in-flight call was duplicated and is now deduplicated", 0.85, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-e08-duplicate-call.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the gateway cutover is complete and the latency profile matches the old endpoint", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e08-latency.txt", verified=True)]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "log", "reports/occ2b-e08-cutover.txt", verified=True),
                    evidence("E2", "build", "reports/occ2b-e08-gates.txt", verified=True),
                ]),
            msg("RESULT", "done", ["child_to_parent", "successful_tests"],
                findings=[finding("the gateway migration is complete with trace continuity verified across the model switch", 0.95, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-e08-cutover.txt", verified=True)],
                verification=verification("trace ids and sequence numbers survive the model switch", "pass", "cargo_test",
                                          "reports/occ2b-e08-gates.txt")),
        ]),

    story(
        "occ2b-e09", "holdout discipline audit", "evolve", "auditor",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("audit that no evaluation or tuning decision was adapted to holdout content after its generation")],
                next_actions=["pull the manifest and receipts", "interview the generation order", "report the verdict"]),
            msg("EVIDENCE", "done", ["source_code_evidence"],
                findings=[],
                evidence=[
                    evidence("E1", "src", "research/tasks2/manifest.json",
                             line_range=(1, 40)),
                    evidence("E2", "commit", "1863b5dea", verified=True),
                ]),
            msg("PROGRESS", "in_progress", ["contradictory_evidence"],
                findings=[
                    finding("the receipt log shows a holdout read at a time before the holdout generation timestamp", 0.6, ["E1"]),
                    finding("the manifest freeze timestamp precedes every split generation", 0.9, ["E2"]),
                ],
                evidence=[
                    evidence("E1", "log", "reports/occ2b-e09-receipt-log.txt"),
                    evidence("E2", "artifact", "research/tasks2/manifest.json"),
                ]),
            msg("PROGRESS", "in_progress", ["missing_evidence"],
                findings=[finding("the clock source of the receipt log is not recorded so the ordering cannot be concluded from timestamps alone", 0.7, [])],
                next_actions=["request the clock provenance"]),
            msg("CORRECTION", "in_progress", ["contradictory_evidence"],
                findings=[finding("the apparent early read was clock skew between the receipt host and the generation host; the content hashes prove the holdout did not exist at that time", 0.9, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e09-clock-skew.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the generation order is confirmed: manifest first then evolve and dev then holdout last", 0.95, ["E1"])],
                evidence=[evidence("E1", "artifact", "research/tasks2/receipts.json")]),
            msg("RESULT", "done", ["child_to_parent", "successful_tests"],
                findings=[finding("no holdout leakage is found; the discipline holds and the audit is clean", 0.95, ["E1"])],
                evidence=[evidence("E1", "artifact", "research/tasks2/receipts.json")],
                verification=verification("the holdout discipline audit finds no leakage", "pass", "manual_audit",
                                          "reports/occ2b-e09-verdict.txt")),
        ]),

    story(
        "occ2b-e10", "worker pool rebalance", "evolve", "a",
        reassign=("occ2b-e10b", "c"),
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("rebalance the worker pool so the overloaded lane hands its queued work to an idle worker")],
                next_actions=["list the queued tasks", "pick the idle worker", "reassign with a parent task id"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the queued work is listed and worker c is idle", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e10-queue-snapshot.txt", verified=True)],
                next_actions=["cancel the current assignment"]),
            msg("CANCELLED", "cancelled", ["cancellation"],
                findings=[finding("the assignment on the overloaded worker is cancelled so the work can move to an idle worker")],
                sender=COORD, recipient="a",
                next_actions=["reissue the work to worker c"]),
            msg("TASK", "pending", ["task_reassignment", "parent_to_child"],
                findings=[finding("the rebalanced work is reissued to the idle worker under a child task id")],
                sender=COORD, recipient="c",
                task="occ2b-e10b",
                next_actions=["resume from the queue snapshot"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the reassigned task is acknowledged and the queue snapshot was received")],
                evidence=[evidence("E1", "finding", "ACK_OF:3")],
                sender="c", recipient=COORD),
            msg("PROGRESS", "in_progress", ["new_dependencies"],
                findings=[finding("the rebalanced work depends on the corpus expansion output for its measurement step", 0.8, [])],
                deps=["occ2b-e07"],
                sender="c", recipient=COORD),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the rebalanced work is ahead of the original schedule", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e10b-progress.txt", verified=True)],
                sender="c", recipient=COORD),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the rebalance is complete and the pool is evenly loaded", 0.95, ["E1"])],
                evidence=[evidence("E1", "commit", "0aa3d5c77", verified=True)],
                sender="c", recipient=COORD),
        ]),

    story(
        "occ2b-e11", "codec fuzz corpus", "evolve", "f",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("build a deterministic fuzz corpus for the OCC-1 codec bounds")],
                next_actions=["derive inputs from the field grammar", "run the fuzzer offline", "triage any crash"]),
            msg("PROGRESS", "in_progress", ["small_findings"],
                findings=[finding("the grammar-derived inputs are generated", 0.9, [])],
                next_actions=["run the fuzzer"]),
            msg("PROGRESS", "in_progress", ["failed_tests", "source_code_evidence"],
                findings=[finding("one input trips a panic in the key length check", 0.9, ["E1"])],
                evidence=[evidence("E1", "src", "prime-agent-runtime/src/rlm/occ1.py",
                                   line_range=(150, 180), revision="1863b5dea")],
                verification=verification("the fuzz corpus passes without panics", "fail", "fuzz",
                                         None),
                next_actions=["triage the panic"]),
            msg("CORRECTION", "in_progress", [],
                findings=[finding("the panic is not a codec bug; the fuzz generator emitted a key above the documented bound and the rejection is correct behavior", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-e11-triage.txt", verified=True)]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[evidence("E1", "build", "reports/occ2b-e11-fuzz-run.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the fuzz corpus is green after the generator fix and the codec rejects all out-of-bound inputs cleanly", 0.95, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-e11-fuzz-run.txt", verified=True)],
                verification=verification("the fuzz run completes with zero codec panics", "pass", "fuzz",
                                          "reports/occ2b-e11-fuzz-run.txt")),
        ]),

    story(
        "occ2b-e12", "evolution loop tuning", "evolve", "b",
        pace="long",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("tune the dialect evolution loop so generations converge before the evaluation budget is spent")],
                next_actions=["profile the loop", "hand the harness to the bench runner", "pick the winner by the frozen rule"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the loop spends most of its budget on redundant re-scoring of unchanged dialects", 0.85, ["E1"])],
                evidence=[evidence("E1", "src", "research/symbolic_comm/evolve.py",
                                  line_range=(1, 80), revision="1863b5dea")],
                next_actions=["cache unchanged scores"]),
            msg("PROGRESS", "in_progress", ["sibling_handoff"],
                findings=[finding("handing the measurement harness to the bench runner for the sweep", None, [])],
                sender="b", recipient="bench",
                next_actions=["run the sweep on the evolve split"]),
            msg("ACK", "in_progress", ["multi_hop"],
                findings=[finding("the harness is received and the sweep is queued on the bench lane")],
                evidence=[evidence("E1", "finding", "ACK_OF:2")],
                sender="bench", recipient="b"),
            msg("PROGRESS", "in_progress", ["large_findings"],
                findings=[
                    finding("caching unchanged scores cuts the loop budget by forty percent", 0.9, []),
                    finding("generation five converges on the same winner as generation nine", 0.85, []),
                    finding("the fresh-group rule prevents exemplar memorization", 0.9, []),
                    finding("the selection rule needs no change", 0.95, []),
                    finding("the wire size of the winner is within two percent of OCC-1", 0.8, []),
                    finding("the latency budget holds at the ninety-ninth percentile", 0.85, []),
                ],
                evidence=[evidence("E1", "tool", "receipts/occ2b-e12-sweep.txt", verified=True)],
                next_actions=["freeze the winner"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the winner is frozen and logged with its checksum", 0.9, ["E1"])],
                evidence=[evidence("E1", "artifact", "research/symbolic_comm/evolution-winner.json")]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "log", "reports/occ2b-e12-loop-profile.txt", verified=True),
                    evidence("E2", "build", "reports/occ2b-e12-gates.txt", verified=True),
                ]),
            msg("RESULT", "done", ["child_to_parent", "successful_tests"],
                findings=[finding("the evolution loop converges within budget and the frozen winner passes every gate", 0.95, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-e12-gates.txt", verified=True)],
                verification=verification("the frozen dialect winner passes all fidelity gates", "pass", "cargo_test",
                                          "reports/occ2b-e12-gates.txt")),
        ]),
]


# ============================================================ DEV stories
# 8 stories, 60 messages.

STORIES_DEV = [

    story(
        "occ2b-d01", "daemon compat matrix", "dev", "c",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("run the daemon compatibility matrix across old and new client and daemon builds")],
                next_actions=["enumerate the version pairs", "reproduce each offline", "record the verdicts"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the matrix has four pairs: new client to old daemon and the three reverse combinations", 0.85, ["E1"])],
                evidence=[evidence("E1", "artifact", "research/occ2opt/DAEMON-COMPAT.md")],
                next_actions=["reproduce the silent no-op pair"]),
            msg("PROGRESS", "in_progress", ["sibling_handoff"],
                findings=[finding("handing the reproduction scripts for the reverse pairs to worker d", None, [])],
                sender="c", recipient="d",
                next_actions=["run the reverse pairs"]),
            msg("ACK", "in_progress", ["multi_hop"],
                findings=[finding("the scripts are received and queued after the current probe")],
                evidence=[evidence("E1", "finding", "ACK_OF:2")],
                sender="d", recipient="c"),
            msg("PROGRESS", "in_progress", ["new_dependencies"],
                findings=[finding("the matrix depends on the schema version work to pin the capability bytes", 0.8, [])],
                deps=["occ2b-e06"],
                sender="d", recipient=COORD),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "log", "reports/occ2b-d01-matrix-run.txt", verified=True),
                    evidence("E2", "build", "reports/occ2b-d01-repro.txt", verified=True),
                ]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("six of seven scenarios reproduce offline with five distinct defects", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-d01-matrix-run.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the matrix is complete; the silent no-op and the client-profile pollution are the two blocking defects", 0.95, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-d01-matrix-run.txt", verified=True)],
                verification=verification("all four version pairs are exercised offline", "pass", "manual_repro",
                                          "reports/occ2b-d01-matrix-run.txt")),
        ]),

    story(
        "occ2b-d02", "prune policy regression", "dev", "a",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("fix the prune policy regression where verification messages are suppressed mid-chain")],
                next_actions=["reproduce the suppression", "find the policy branch", "add the regression test"]),
            msg("PROGRESS", "failed", ["failed_tests"],
                findings=[finding("the prune gate is red; verification messages vanish when the next action list is empty", 0.9, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-d02-prune-gate.txt", verified=True)],
                next_actions=["inspect the policy branch"]),
            msg("CORRECTION", "in_progress", ["contradictory_evidence"],
                findings=[
                    finding("the first reading blamed the consolidation rules", 0.6, ["E1"]),
                    finding("the trace shows the prune rules are the actual cause; the consolidation output is correct", 0.85, ["E2"]),
                ],
                evidence=[
                    evidence("E1", "log", "reports/occ2b-d02-first-reading.txt"),
                    evidence("E2", "log", "reports/occ2b-d02-trace.txt", verified=True),
                ]),
            msg("PROGRESS", "in_progress", ["source_code_evidence"],
                findings=[finding("the policy branch drops verification messages when no next action follows", 0.9, ["E1"])],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/prune.rs",
                                  line_range=(40, 88), revision="1863b5dea")],
                next_actions=["narrow the drop condition"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[evidence("E1", "commit", "4f3dc4f83", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the drop condition now requires a verified terminal state before suppressing", 0.85, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-d02-prune-green.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the prune policy regression is fixed with a pinned regression test", 0.95, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-d02-prune-green.txt", verified=True)],
                verification=verification("verification messages survive the prune chain", "pass", "cargo_test",
                                          "reports/occ2b-d02-prune-green.txt")),
        ]),

    story(
        "occ2b-d03", "latency budget rework", "dev", "e",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("rework the encode latency budget so the ninety-ninth percentile stays under the cap")],
                next_actions=["profile the encode path", "find the hot allocation", "re-measure"]),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[finding("the profile shows the wire build dominates with repeated string copies", 0.85, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-d03-profile.txt", verified=True)],
                next_actions=["eliminate the copies"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[evidence("E1", "build", "reports/occ2b-d03-first-fix.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the tail comes from the fault injection path which only runs on decode errors", 0.85, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-d03-tail-analysis.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the second fix short-circuits the fault path for clean messages", 0.9, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d03-bench2.txt", verified=True)],
                next_actions=["re-measure the full budget"]),
            msg("RESULT", "partial", ["partial_completion", "child_to_parent"],
                findings=[finding("the encode path meets the budget on the corpus profile; the mixed traffic profile still shows a tail above the cap", 0.8, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d03-bench2.txt", verified=True)],
                next_actions=["profile the mixed traffic tail"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the partial scope is acknowledged; the mixed traffic tail is scheduled next")],
                sender=COORD, recipient="e"),
        ]),

    story(
        "occ2b-d04", "capacity headroom check", "dev", "f",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("check the capacity headroom for the corpus evaluation under the four subagent cap")],
                next_actions=["measure the per-item cost", "project the full run", "report the headroom"]),
            msg("PROGRESS", "in_progress", ["small_findings"],
                findings=[finding("the per-item cost is measured", 0.9, [])],
                next_actions=["project the full run"]),
            msg("PROGRESS", "in_progress", ["missing_evidence"],
                findings=[finding("the projection is drafted but the receipt for the per-item measurement is missing so the numbers cannot be pinned", 0.6, [])],
                next_actions=["collect the measurement receipt"]),
            msg("REQUEST_DETAIL", "in_progress", ["missing_evidence"],
                findings=[finding("the per-item measurement receipt is needed to pin the projection")],
                sender=COORD, recipient="f"),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d04-per-item.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the projection shows the full run fits inside the cap with one slot to spare", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d04-per-item.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent", "successful_tests"],
                findings=[finding("the headroom check passes; the full run fits the four subagent cap with margin", 0.95, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d04-per-item.txt", verified=True)],
                verification=verification("the projected run fits the subagent cap", "pass", "manual_projection",
                                          "receipts/occ2b-d04-per-item.txt")),
        ]),

    story(
        "occ2b-d05", "token cost audit", "dev", "bench",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("audit the token cost of the current message pipeline end to end")],
                next_actions=["collect the per-message token counts", "break them down by field", "report the drivers"]),
            msg("PROGRESS", "in_progress", ["model_switching"],
                findings=[
                    finding("the audit session switched from glm-5.3-flash to glm-5.3 for the long-context items; the audit continues on the new model", 0.9, ["E1"]),
                ],
                evidence=[evidence("E1", "log", "reports/occ2b-d05-audit-switch.txt", verified=True)],
                next_actions=["collect the counts"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the per-message token counts are collected for both models", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d05-counts.txt", verified=True)]),
            msg("PROGRESS", "in_progress", ["large_findings"],
                findings=[
                    finding("the per-message codebook alone explains the largest share of the increase", 0.95, []),
                    finding("the dual payload doubles the durable bytes but is stripped from prompts", 0.9, []),
                    finding("the symbolic body itself is slightly larger than OCC-1", 0.85, []),
                    finding("once-per-session codebook injection beats OCC-1 from the third message on session-profile traffic", 0.9, []),
                    finding("on corpus-profile traffic the once-per-session variant never beats OCC-1", 0.9, []),
                    finding("the prompt body remains the second largest driver", 0.8, []),
                ],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d05-counts.txt", verified=True)],
                next_actions=["write the report"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "artifact", "reports/occ2opt/CODEBOOK-COST.md"),
                    evidence("E2", "tool", "receipts/occ2b-d05-counts.txt", verified=True),
                ]),
            msg("PROGRESS", "in_progress", ["multiple_source_refs"],
                findings=[finding("the breakdown cross-checks against the gate receipts and the analysis artifacts", 0.9, ["E1", "E2", "E3"])],
                evidence=[
                    evidence("E1", "build", "reports/occ2b-d05-gates.txt", verified=True),
                    evidence("E2", "artifact", "reports/occ2opt/CODEBOOK-COST.md"),
                    evidence("E3", "tool", "receipts/occ2b-d05-counts.txt", verified=True),
                ]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the report is drafted with the verdict and the second-order issue for dialect design", 0.9, ["E1"])],
                evidence=[evidence("E1", "artifact", "reports/occ2opt/CODEBOOK-COST.md")]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the token cost audit is complete; the codebook injection is confirmed as the primary driver", 0.95, ["E1"])],
                evidence=[evidence("E1", "artifact", "reports/occ2opt/CODEBOOK-COST.md")]),
        ]),

    story(
        "occ2b-d06", "worker timeout policy", "dev", "b",
        reassign=("occ2b-d06b", "d"),
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("define the worker timeout policy for delegated subtasks so slow lanes release their slots")],
                next_actions=["measure the current tail", "pick the timeout", "define the release rule"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the current tail shows two lanes holding slots past their useful life", 0.85, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-d06-tail.txt", verified=True)],
                next_actions=["draft the release rule"]),
            msg("CANCELLED", "cancelled", ["cancellation"],
                findings=[finding("the assignment is cancelled because the lane is needed for the timeout measurement itself")],
                sender=COORD, recipient="b"),
            msg("TASK", "pending", ["task_reassignment", "parent_to_child"],
                findings=[finding("the timeout policy work is reissued to the free worker under a child task id")],
                sender=COORD, recipient="d",
                task="occ2b-d06b",
                next_actions=["resume from the tail measurement"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the reissued task is acknowledged and the tail measurement was received")],
                evidence=[evidence("E1", "finding", "ACK_OF:3")],
                sender="d", recipient=COORD),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the timeout is picked from the tail distribution and the release rule is written", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-d06b-policy.txt", verified=True)],
                sender="d", recipient=COORD),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the timeout policy is defined and the slow lanes release their slots under it", 0.95, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-d06b-policy.txt", verified=True)],
                sender="d", recipient=COORD),
        ]),

    story(
        "occ2b-d07", "trace consolidation", "dev", "c",
        pace="long",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("consolidate the long session traces so repeated updates collapse into a single durable record")],
                next_actions=["define the consolidation rule", "implement the collapse", "verify the replay"]),
            msg("PROGRESS", "in_progress", ["small_findings"],
                findings=[finding("the consolidation rule is defined", 0.9, [])],
                next_actions=["implement the collapse"]),
            msg("PROGRESS", "in_progress", ["repeated_messages"],
                findings=[finding("the consolidation rule is defined", 0.9, [])],
                next_actions=["implement the collapse"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the duplicate update was received; the consolidation rule applies to it")],
                sender=COORD, recipient="c"),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[finding("the collapse is implemented and the repeated updates merge into one durable record", 0.85, ["E1"])],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/consolidation.rs",
                                  line_range=(20, 70), revision="1863b5dea")],
                next_actions=["verify the replay"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "build", "reports/occ2b-d07-replay.txt", verified=True),
                    evidence("E2", "log", "reports/occ2b-d07-collapse.txt", verified=True),
                ]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the replay of the consolidated trace matches the original state machine", 0.9, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-d07-replay.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the trace consolidation is done and the replay is verified", 0.95, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-d07-replay.txt", verified=True)],
                verification=verification("the consolidated trace replays to the same state", "pass", "cargo_test",
                                          "reports/occ2b-d07-replay.txt")),
        ]),

    story(
        "occ2b-d08", "evidence digest rotation", "dev", "a",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("rotate the evidence digests after the repository rebase so old receipts stop matching")],
                next_actions=["list the stale digests", "recompute them", "record the mapping"]),
            msg("PROGRESS", "in_progress", ["source_code_evidence"],
                findings=[finding("the digest list is recomputed for the rebased sources", 0.85, ["E1"])],
                evidence=[evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/delta.rs",
                                  line_range=(14, 61), revision="9c41f0ab2")],
                next_actions=["compare against the stored receipts"]),
            msg("PROGRESS", "in_progress", ["missing_evidence"],
                findings=[finding("two stored digests no longer match any recomputed value and the original tool runs are gone", 0.7, [])],
                next_actions=["find the original runs or re-execute them"]),
            msg("REQUEST_DETAIL", "in_progress", [],
                findings=[finding("the original tool run logs are needed to re-derive the two stale digests")],
                sender=COORD, recipient="a"),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d08-digest-map.txt", verified=True)]),
            msg("CORRECTION", "in_progress", [],
                findings=[finding("the two digests were rotated by the rebase itself; the mapping table recovers them without re-execution", 0.9, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d08-digest-map.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("all digests verify against the rebased tree", 0.95, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d08-digest-map.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the evidence digests are rotated and verified against the rebased tree", 0.95, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-d08-digest-map.txt", verified=True)]),
        ]),
]


# ============================================================ HOLDOUT stories
# 7 stories, 54 messages. Generated LAST and from an independent seed base
# (SEED_BASE_HO); nothing downstream of this section is tuned against it.

STORIES_HOLDOUT = [

    story(
        "occ2b-h01", "cold cache replay", "holdout", "b",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("replay the session corpus from a cold cache to verify the amortized codebook path")],
                next_actions=["clear the cache", "replay the corpus", "compare against the warm run"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the cold replay is slower only on the first two messages as designed", 0.85, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h01-cold-run.txt", verified=True)],
                next_actions=["compare the totals"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the totals match the warm run within one percent", 0.9, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h01-compare.txt", verified=True)]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "log", "reports/occ2b-h01-cold-run.txt", verified=True),
                    evidence("E2", "log", "reports/occ2b-h01-warm-run.txt", verified=True),
                ]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the first-message penalty is exactly one codebook injection", 0.9, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h01-compare.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the replay completes without cache drift across sessions", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h01-cold-run.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent", "successful_tests"],
                findings=[finding("the cold cache replay matches the warm run and the amortization holds", 0.95, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h01-compare.txt", verified=True)],
                verification=verification("cold and warm replays agree within one percent", "pass", "manual_replay",
                                          "receipts/occ2b-h01-compare.txt")),
        ]),

    story(
        "occ2b-h02", "wire compression trial", "holdout", "d",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("trial the wire compression on the longest messages in the corpus")],
                next_actions=["pick the longest messages", "apply the trial codec", "measure the savings"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the longest messages cluster in the large-finding scenarios", 0.8, ["E1"])],
                evidence=[evidence("E1", "artifact", "research/tasks2/msgs_corpus2_holdout.json")],
                next_actions=["apply the trial codec"]),
            msg("PROGRESS", "in_progress", ["source_code_evidence", "multiple_source_refs"],
                findings=[finding("the trial codec cuts the largest messages by a fifth without breaking the field grammar", 0.85, ["E1", "E2"])],
                evidence=[
                    evidence("E1", "src", "crates/pi-coding-agent/src/core/occ/occ1.rs",
                             line_range=(1, 60), revision="1863b5dea"),
                    evidence("E2", "src", "crates/pi-coding-agent/src/core/occ/contract.rs",
                             line_range=(20, 90), revision="1863b5dea"),
                ]),
            msg("PROGRESS", "in_progress", ["small_findings"],
                findings=[finding("the short messages see no benefit", 0.9, [])],
                next_actions=["record the crossover"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "tool", "receipts/occ2b-h02-compression.txt", verified=True),
                    evidence("E2", "lines", "crates/pi-coding-agent/src/core/occ/occ1.rs",
                             line_range=(88, 120)),
                ]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the crossover sits at six hundred bytes; below it the header cost dominates", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h02-compression.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the round trip holds on every trialed message", 0.9, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-h02-roundtrip.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the compression trial is recorded with its crossover and the round trip is intact", 0.95, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h02-compression.txt", verified=True)]),
        ]),

    story(
        "occ2b-h03", "foreign daemon probe", "holdout", "c",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("probe how a foreign daemon reacts to our OCC-2 session messages")],
                next_actions=["craft the probe message", "send it to the foreign daemon", "record the reaction"]),
            msg("PROGRESS", "in_progress", ["sibling_handoff"],
                findings=[finding("handing the probe result to worker e for the reverse direction", None, [])],
                sender="c", recipient="e",
                next_actions=["run the reverse probe"]),
            msg("ACK", "in_progress", ["multi_hop"],
                findings=[finding("the probe result is received and the reverse probe is queued")],
                evidence=[evidence("E1", "finding", "ACK_OF:2")],
                sender="e", recipient="c"),
            msg("PROGRESS", "in_progress", ["multi_hop"],
                findings=[finding("the foreign daemon silently drops the occ2 message body and stores only the plain fallback", 0.8, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h03-drop.txt", verified=True)],
                sender="e", recipient=COORD),
            msg("PROGRESS", "in_progress", ["missing_evidence"],
                findings=[finding("no error receipt exists on the foreign side so the drop reason is not yet provable", 0.6, [])],
                next_actions=["request the foreign side logs"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[evidence("E1", "log", "reports/occ2b-h03-drop.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the foreign logs arrive and confirm the schema mismatch as the drop cause", 0.85, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h03-foreign-logs.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the foreign daemon drops occ2 bodies on a schema mismatch and keeps the plain fallback", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h03-foreign-logs.txt", verified=True)]),
        ]),

    story(
        "occ2b-h04", "verification receipt gap", "holdout", "auditor",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("audit the verification receipts for the gate runs claimed in the last report")],
                next_actions=["list the claimed gates", "match each claim to a receipt", "report the gaps"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("eleven of twelve claims have receipts", 0.85, ["E1"])],
                evidence=[evidence("E1", "artifact", "reports/occ2opt/BASELINE-GATES.md")],
                next_actions=["chase the missing receipt"]),
            msg("PROGRESS", "failed", ["failed_tests"],
                findings=[finding("the remaining claim fails its receipt check; the referenced log file does not exist", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h04-missing.txt", verified=True)],
                next_actions=["trace the log path"]),
            msg("PROGRESS", "in_progress", ["contradictory_evidence"],
                findings=[
                    finding("the claim text says the log lives under reports", 0.6, ["E1"]),
                    finding("the receipts index says the log was written to the scratch directory", 0.8, ["E2"]),
                ],
                evidence=[
                    evidence("E1", "artifact", "reports/occ2opt/BASELINE-GATES.md"),
                    evidence("E2", "tool", "receipts/occ2b-h04-index.txt", verified=True),
                ]),
            msg("PROGRESS", "in_progress", ["missing_evidence"],
                findings=[finding("the referenced log cannot be produced from either location so the claim stays unverified", 0.75, [])],
                next_actions=["mark the claim unverified"]),
            msg("CORRECTION", "in_progress", [],
                findings=[finding("the scratch copy was found after all; the claim verifies and the earlier failure is retracted", 0.9, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h04-scratch-copy.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("all twelve claims verify; one needed the scratch copy and is now receipted", 0.95, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h04-scratch-copy.txt", verified=True)]),
        ]),

    story(
        "occ2b-h05", "schedule window migration", "holdout", "e",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("migrate the scheduled maintenance window without dropping queued work")],
                next_actions=["drain the queue", "move the window", "verify the drained items"]),
            msg("PROGRESS", "in_progress", ["model_switching"],
                findings=[
                    finding("the migration session switched from deepseek-v4-pro to glm-5.3 for the window operations; the queue state carries over unchanged", 0.85, ["E1"]),
                ],
                evidence=[evidence("E1", "log", "reports/occ2b-h05-switch.txt", verified=True)],
                next_actions=["drain the queue"]),
            msg("PROGRESS", "in_progress", ["state_updates"],
                findings=[finding("the queue drains and the window moves with no lost items", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h05-drain.txt", verified=True)]),
            msg("PROGRESS", "in_progress", ["new_dependencies"],
                findings=[finding("the verification step now depends on the trace consolidation work to compare queue states", 0.8, [])],
                deps=["occ2b-d07"],
                next_actions=["wait for the consolidation result"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[evidence("E1", "log", "reports/occ2b-h05-drain.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the drained items replay to the same queue state under the consolidation rules", 0.85, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-h05-replay.txt", verified=True)]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the new window holds and no work is dropped", 0.9, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h05-window.txt", verified=True)]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the maintenance window migration is complete with the queue intact", 0.95, ["E1"])],
                evidence=[evidence("E1", "log", "reports/occ2b-h05-window.txt", verified=True)]),
        ]),

    story(
        "occ2b-h06", "abandoned feature branch", "holdout", "f",
        reassign=("occ2b-h06b", "a"),
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("revive the abandoned feature branch or close it with a partial record")],
                next_actions=["assess the branch state", "decide revive or close", "record the outcome"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the branch is four weeks stale and conflicts with the current main", 0.85, ["E1"])],
                evidence=[evidence("E1", "commit", "ce8480d59", verified=True)],
                next_actions=["attempt the rebase"]),
            msg("RESULT", "partial", ["partial_completion", "child_to_parent"],
                findings=[finding("the rebase lands but two features on the branch no longer apply; the record stays partial", 0.8, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-h06-rebase.txt", verified=True)],
                next_actions=["split the surviving parts"]),
            msg("CANCELLED", "cancelled", ["cancellation"],
                findings=[finding("the branch work is cancelled; the surviving parts move to a fresh task")],
                sender=COORD, recipient="f"),
            msg("TASK", "pending", ["task_reassignment", "parent_to_child"],
                findings=[finding("the surviving parts are reissued under a child task id to the free worker")],
                sender=COORD, recipient="a",
                task="occ2b-h06b",
                next_actions=["port the two surviving features"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the reissued task is acknowledged and the rebase record was received")],
                evidence=[evidence("E1", "finding", "ACK_OF:4")],
                sender="a", recipient=COORD),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the first surviving feature ports cleanly", 0.85, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-h06b-port.txt", verified=True)],
                sender="a", recipient=COORD),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("both surviving features are ported and the abandoned branch is closed with a partial record", 0.9, ["E1"])],
                evidence=[evidence("E1", "commit", "f37ed6a79", verified=True)],
                sender="a", recipient=COORD),
        ]),

    story(
        "occ2b-h07", "long trace archival", "holdout", "a",
        pace="long",
        chain=[
            msg("TASK", "pending", ["parent_to_child"],
                findings=[finding("archive the longest session traces with their repeated updates preserved")],
                next_actions=["select the longest traces", "archive with dedup metadata", "verify the restore"]),
            msg("PROGRESS", "in_progress", [],
                findings=[finding("the longest traces are selected by message count", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h07-selection.txt", verified=True)],
                next_actions=["archive with dedup metadata"]),
            msg("PROGRESS", "in_progress", ["repeated_messages"],
                findings=[finding("the traces are archived with the repeated updates preserved", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h07-selection.txt", verified=True)],
                next_actions=["verify the restore"]),
            msg("PROGRESS", "in_progress", ["repeated_messages"],
                findings=[finding("the traces are archived with the repeated updates preserved", 0.85, ["E1"])],
                evidence=[evidence("E1", "tool", "receipts/occ2b-h07-selection.txt", verified=True)],
                next_actions=["verify the restore"]),
            msg("ACK", "in_progress", [],
                findings=[finding("the duplicate archival notice was received; the dedup metadata covers it")],
                sender=COORD, recipient="a"),
            msg("PROGRESS", "in_progress", ["large_findings"],
                findings=[
                    finding("the archive preserves all nine message kinds", 0.9, []),
                    finding("the repeated updates restore as distinct messages with shared content digests", 0.85, []),
                    finding("the evidence chains restore with their line ranges", 0.9, []),
                    finding("the verification records restore with their receipts", 0.9, []),
                    finding("the trace ids and sequence numbers restore without gaps", 0.95, []),
                    finding("the restore time stays under the archival budget", 0.85, []),
                ],
                evidence=[evidence("E1", "build", "reports/occ2b-h07-restore.txt", verified=True)],
                next_actions=["sign off the archive"]),
            msg("EVIDENCE", "done", [],
                findings=[],
                evidence=[
                    evidence("E1", "artifact", "research/occ2opt/trace-archive/"),
                    evidence("E2", "build", "reports/occ2b-h07-restore.txt", verified=True),
                ]),
            msg("RESULT", "done", ["child_to_parent"],
                findings=[finding("the long trace archival is complete and the restore is verified", 0.95, ["E1"])],
                evidence=[evidence("E1", "build", "reports/occ2b-h07-restore.txt", verified=True)],
                verification=verification("every archived trace restores byte-identically", "pass", "manual_restore",
                                          "reports/occ2b-h07-restore.txt")),
        ]),
]


import checkers  # research/tasks/checkers.py (frozen, read-only)


# ============================================================ composer

_QA_SEED_BASE = "occ2opt-corpus2-qa-v1"
_ID_RE = re.compile(r"^e2-([edh])(\d{2})-(\d{2})$")
_ALLOWED_PAYLOAD_KEYS = {
    "task_id", "kind", "status", "message_id", "trace_id", "sender_identity",
    "recipient_identity", "findings", "evidence", "artifacts", "verification",
    "dependencies", "next_actions", "sequence", "timestamp", "parent_task_id",
    "expiry",
}


def _parse_stamp(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def _format_stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def compose_story(st, split, seed_base):
    # Compose one story into entries, deterministically (story-local rng).
    task_id = st["task_id"]
    suffix = task_id.rsplit("-", 1)[1]
    worker_id = worker_identity(st["worker"])
    rng = random.Random(derive_seed2(seed_base, "story", task_id))
    story_start = BASE_DAY + datetime.timedelta(minutes=rng.randint(0, 60 * 24 * 13))
    delta_lo, delta_hi = (30, 240) if st.get("pace") == "long" else (5, 75)

    def message_id_at(chain_index):
        return "m2-%s-%02d" % (suffix, chain_index + 1)

    entries = []
    moment = story_start
    for chain_index, spec in enumerate(st["chain"]):
        kind = spec["kind"]
        msg_task = spec.get("task", task_id)
        sender = spec.get("sender") or (COORD if kind == "TASK" else worker_id)
        recipient = spec.get("recipient") or (worker_id if kind == "TASK" else COORD)
        moment = moment + datetime.timedelta(minutes=rng.randint(delta_lo, delta_hi))

        payload = {
            "task_id": msg_task,
            "kind": kind,
            "status": spec["status"],
            "message_id": message_id_at(chain_index),
            "trace_id": "trace-" + task_id,
            "sender_identity": sender,
            "recipient_identity": recipient,
            "findings": [dict(item) for item in spec.get("findings", [])],
            "evidence": [],
            "artifacts": list(spec.get("artifacts", [])),
            "verification": dict(spec["verification"]) if spec.get("verification") else None,
            "dependencies": list(st.get("deps", [])) if kind == "TASK" else list(spec.get("deps", [])),
            "next_actions": list(spec.get("next_actions", [])),
            "sequence": chain_index + 1,
            "timestamp": _format_stamp(moment),
        }
        if msg_task != task_id:
            payload["parent_task_id"] = task_id
        elif st.get("parent") and kind == "TASK":
            payload["parent_task_id"] = st["parent"]

        for entry_spec in spec.get("evidence", []):
            entry = dict(entry_spec)
            if entry["locator"].startswith("ACK_OF:"):
                target = int(entry["locator"].split(":", 1)[1])
                entry["locator"] = message_id_at(target)
            if "digest" not in entry and entry["kind"] in ("src", "artifact") and rng.random() < 0.5:
                entry["digest"] = rand_hex(rng, 64)
            if "line_range" in entry:
                entry["line_range"] = list(entry["line_range"])
            payload["evidence"].append(entry)

        eid = "e2-%s-%02d" % (suffix, chain_index + 1)
        scenarios = list(spec.get("scenarios", []))
        if st.get("pace") == "long" and chain_index + 1 >= 6 and "long_history" not in scenarios:
            # Deep chain position in a long-pace story: this message only
            # exists because the session history is long.
            scenarios.append("long_history")
        record = {
            "id": eid,
            "split": split,
            "story": task_id,
            "scenarios": scenarios,
            "holdout": split == "holdout",
            "plain_english": GC.render_plain(payload),
            "payload": payload,
        }
        record["qa"] = make_qa(record)
        entries.append(record)
    return entries


# ============================================================ QA generation

def make_qa(record):
    # Deterministic QA items for one entry; gold answers derive from the payload.
    rng = random.Random(derive_seed2(_QA_SEED_BASE, record["id"]))
    payload = record["payload"]
    pool = [
        ("A", "What is the status of the task in message {id}?", payload["status"]),
        ("A", "What kind of message is message {id}?", payload["kind"]),
        ("A", "Who is the sender of message {id}?", payload["sender_identity"]),
        ("A", "Which task is message {id} about?", payload["task_id"]),
        ("E", "How many findings does message {id} carry?", str(len(payload["findings"]))),
        ("E", "How many evidence entries does message {id} carry?", str(len(payload["evidence"]))),
        ("E", "What is the sequence number of message {id} in its trace?", str(payload["sequence"])),
        ("D", "Which task dependencies does message {id} declare? Answer with a comma-separated list, or none.",
              ",".join(payload["dependencies"]) or "none"),
        ("D", "Which artifacts does message {id} report? Answer with a comma-separated list, or none.",
              ",".join(payload["artifacts"]) or "none"),
    ]
    if payload["evidence"]:
        kinds = sorted({item["kind"] for item in payload["evidence"]})
        pool.append(("D", "Which evidence kinds does message {id} cite? Answer with a comma-separated list.",
                     ",".join(kinds)))
    else:
        pool.append(("D", "Which evidence kinds does message {id} cite? Answer with a comma-separated list, or none.",
                     "none"))
    if payload.get("verification"):
        pool.append(("A", "What verification status does message {id} record?", payload["verification"]["status"]))
    for index, item in enumerate(payload["findings"]):
        if item.get("confidence") is not None:
            pool.append(("E", "What is the confidence value of point %d in message {id}? Answer as a decimal number."
                          % (index + 1), "%.3f" % item["confidence"]))
    rng.shuffle(pool)
    take = 3 if len(pool) >= 3 else len(pool)
    chosen = sorted(pool[:take], key=lambda row: row[1])
    return [
        {"family": family, "q": question.format(id=record["id"]), "a": answer}
        for family, question, answer in chosen
    ]


# ============================================================ validation

def roundtrip_payload(payload):
    # payload -> typed -> OCC-1 wire -> decode -> typed; (ok, wire-or-detail).
    try:
        message = SYM.AgentCommMessage.from_payload(payload)
        wire = message.to_occ1().encode()
        decoded = SYM.Occ1Message.decode(wire)
        back = SYM.AgentCommMessage.from_occ1(decoded)
        if not SYM.equal(message, back):
            return False, "round trip changed the message"
        return True, wire
    except Exception as error:  # noqa: BLE001 - any failure is a corpus defect
        return False, "%s: %s" % (type(error).__name__, error)


def validate_entry(record, seen_message_ids, expected_split):
    # Contract invariants mirror (v1 rules + v2 keys); returns violation list.
    problems = []
    payload = record["payload"]
    if set(payload) - _ALLOWED_PAYLOAD_KEYS:
        problems.append("unexpected payload keys: %s" % sorted(set(payload) - _ALLOWED_PAYLOAD_KEYS))
    if _ID_RE.match(record["id"]) is None:
        problems.append("bad entry id %r" % record["id"])
    if record["split"] != expected_split:
        problems.append("split mismatch: %r != %r" % (record["split"], expected_split))
    if record["holdout"] != (expected_split == "holdout"):
        problems.append("holdout flag mismatch")
    if not record["story"] or not record["story"].startswith("occ2b-"):
        problems.append("bad story id %r" % record["story"])
    unknown = set(record["scenarios"]) - SCENARIO_SET
    if unknown:
        problems.append("unknown scenario tags: %s" % sorted(unknown))
    if payload["kind"] not in GC.KINDS:
        problems.append("unknown kind %r" % payload["kind"])
    if payload["status"] not in GC.STATUSES:
        problems.append("unknown status %r" % payload["status"])
    if not payload["task_id"] or any(ord(ch) < 32 for ch in payload["task_id"]):
        problems.append("bad task_id")
    if payload["message_id"] in seen_message_ids:
        problems.append("duplicate message_id %r" % payload["message_id"])
    seen_message_ids.add(payload["message_id"])
    if payload["kind"] == "EVIDENCE" and not payload["evidence"]:
        problems.append("EVIDENCE message without evidence")
    evidence_ids = [item["id"] for item in payload["evidence"]]
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
    for item in payload["evidence"]:
        if item["kind"] not in GC.EVIDENCE_KINDS:
            problems.append("unknown evidence kind %r" % item["kind"])
        if "line_range" in item and len(item["line_range"]) != 2:
            problems.append("bad line_range")
    verification = payload.get("verification")
    if verification is not None and verification["status"] not in GC.VERIFICATION_STATUSES:
        problems.append("unknown verification status %r" % verification["status"])
    try:
        _parse_stamp(payload["timestamp"])
    except ValueError:
        problems.append("bad timestamp %r" % payload["timestamp"])
    if not record["plain_english"] or not record["plain_english"].isascii():
        problems.append("plain_english is empty or not ASCII")
    if payload["sequence"] < 1:
        problems.append("bad sequence")
    ok, detail = roundtrip_payload(payload)
    if not ok:
        problems.append("round trip failed: %s" % detail)
    qa = record.get("qa", [])
    if len(qa) < 2:
        problems.append("fewer than 2 qa items")
    for item in qa:
        if item["family"] not in _QA_FAMILIES:
            problems.append("bad qa family %r" % item["family"])
        if record["id"] not in item["q"]:
            problems.append("qa question does not name the message id")
        if not isinstance(item["a"], str) or not item["a"]:
            problems.append("qa answer is not a non-empty string")
        if not checkers.check_answer({"family": item["family"], "gold_answer": item["a"]}, item["a"]):
            problems.append("qa gold answer rejected by its own checker")
    return problems


def validate_split(entries, expected_split):
    problems = []
    seen_message_ids = set()
    for record in entries:
        for problem in validate_entry(record, seen_message_ids, expected_split):
            problems.append("%s: %s" % (record["id"], problem))
    covered = set()
    for record in entries:
        covered.update(record["scenarios"])
    missing = set(SCENARIO_TYPES) - covered
    if missing:
        problems.append("split %s missing scenario coverage: %s" % (expected_split, sorted(missing)))
    if len(entries) != SPLIT_TARGETS[expected_split]:
        problems.append("split %s has %d entries, target is %d"
                        % (expected_split, len(entries), SPLIT_TARGETS[expected_split]))
    return problems


# ============================================================ build / write

def build_split(split):
    lists = {
        "evolve": STORIES_EVOLVE,
        "dev": STORIES_DEV,
        "holdout": STORIES_HOLDOUT,
    }
    seed_base = SEED_BASE_HO if split == "holdout" else SEED_BASE_ED
    entries = []
    for st in lists[split]:
        entries.extend(compose_story(st, split, seed_base))
    return entries


def split_stats(entries):
    from collections import Counter
    kinds = Counter()
    statuses = Counter()
    evidence_kinds = Counter()
    verification_statuses = Counter()
    scenarios = Counter()
    plain_bytes = 0
    json_bytes = 0
    occ1_bytes = 0
    max_occ1 = 0
    findings_total = 0
    qa_total = 0
    for record in entries:
        payload = record["payload"]
        kinds[payload["kind"]] += 1
        statuses[payload["status"]] += 1
        for item in payload["evidence"]:
            evidence_kinds[item["kind"]] += 1
        if payload["verification"]:
            verification_statuses[payload["verification"]["status"]] += 1
        for tag in record["scenarios"]:
            scenarios[tag] += 1
        findings_total += len(payload["findings"])
        qa_total += len(record["qa"])
        plain_bytes += len(record["plain_english"].encode("utf-8"))
        json_bytes += len(json.dumps(payload, sort_keys=True).encode("utf-8"))
        ok, detail = roundtrip_payload(payload)
        if ok:
            occ1 = len(detail.encode("utf-8"))
            occ1_bytes += occ1
            max_occ1 = max(max_occ1, occ1)
    return {
        "count": len(entries),
        "kinds": dict(sorted(kinds.items())),
        "statuses": dict(sorted(statuses.items())),
        "evidence_kinds": dict(sorted(evidence_kinds.items())),
        "verification_statuses": dict(sorted(verification_statuses.items())),
        "scenarios": dict(sorted(scenarios.items())),
        "findings_total": findings_total,
        "qa_total": qa_total,
        "plain_bytes": plain_bytes,
        "json_bytes": json_bytes,
        "occ1_bytes": occ1_bytes,
        "max_occ1_bytes": max_occ1,
    }


def write_split_file(split, entries):
    stats = split_stats(entries)
    meta = {
        "artifact": "occ2opt msgs_corpus2",
        "split": split,
        "manifest": "research/tasks2/manifest.json",
        "seed_base": SEED_BASE_HO if split == "holdout" else SEED_BASE_ED,
        "count": len(entries),
        "scenario_types_covered": len(stats["scenarios"]),
    }
    path = os.path.join(_HERE, "msgs_corpus2_%s.json" % split)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"meta": meta, "messages": entries}, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    return path, meta


def main():
    parser = argparse.ArgumentParser(description="occ2opt expanded message corpus generator")
    parser.add_argument("--phase", required=True, choices=("evolvedev", "holdout", "stats"))
    args = parser.parse_args()

    if args.phase == "stats":
        for split in SPLITS:
            path = os.path.join(_HERE, "msgs_corpus2_%s.json" % split)
            if not os.path.isfile(path):
                print("%s: missing" % path)
                continue
            doc = json.loads(open(path, encoding="utf-8").read())
            stats = split_stats(doc["messages"])
            print("%s: %d messages, %d qa items, occ1 max %d bytes"
                  % (split, stats["count"], stats["qa_total"], stats["max_occ1_bytes"]))
        return 0

    splits = ("evolve", "dev") if args.phase == "evolvedev" else ("holdout",)
    for split in splits:
        entries = build_split(split)
        problems = validate_split(entries, split)
        if problems:
            for problem in problems:
                sys.stderr.write("CORPUS VALIDATION FAILURE %s\n" % problem)
            return 1
        if build_split(split) != entries:
            sys.stderr.write("CORPUS VALIDATION FAILURE: generator is not deterministic\n")
            return 1
        path, meta = write_split_file(split, entries)
        stats = split_stats(entries)
        print("wrote %s (%d messages, %d scenarios, %d qa items)"
              % (path, stats["count"], len(stats["scenarios"]), stats["qa_total"]))
        print("  kinds: %s" % json.dumps(stats["kinds"]))
        print("  statuses: %s" % json.dumps(stats["statuses"]))
        print("  evidence: %s" % json.dumps(stats["evidence_kinds"]))
        print("  verification: %s" % json.dumps(stats["verification_statuses"]))
        print("  bytes plain=%d json=%d occ1=%d (max occ1 message %d bytes)"
              % (stats["plain_bytes"], stats["json_bytes"], stats["occ1_bytes"], stats["max_occ1_bytes"]))
        print("  validation: invariants, round trip, qa golds, determinism all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
