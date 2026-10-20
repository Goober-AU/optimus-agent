"""E3 coding-task scenario generators (R2-E3, live GLM 5.3).

Five coding-task scenarios from the campaign brief, in the same scenario
document shape the W7 harness consumes (context_rot_r2/scenario.py +
context_rot_r2/live/driver.py).  Generation is deterministic per scenario
id: cosmetic values (line numbers, counts) draw from a blake2b-seeded rng;
golds and anchors are fixed constants, so every probe has an explicit
action-level gold.

Probes test ACTION, not recall:
  TASK-A  constraint during implementation - the next file to edit must
          respect "do not touch the auth module";
  TASK-C  correction after implementation started - the current approach is
          the corrected one (B), never the implemented-then-rejected one;
  TASK-E  evidence near boundary - the exact failing assertion sits in the
          last summarized entry before the cycle-1 compaction cut;
  TASK-H  avoid rejected approach - the acceptable set excludes the
          user-rejected approach;
  TASK-F  restart - same correction semantic as TASK-C, with a simulated
          restart (session serialize -> reload) after cycle 3.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from context_rot_r2.scenario import stable_seed

CYCLES = 5
CONTEXT_WINDOW = 20_000.0
RESERVE_TOKENS = 4_000.0
KEEP_RECENT_TOKENS = 20.0
FILLER_TURNS = 3
FILLER_SIZE = 100
FILLER_SENTENCE = "unrelated historical discussion. "

TASK_IDS = ("A", "C", "E", "H", "F")


def _filler_turn(cycle: int, turn: int) -> List[Dict[str, Any]]:
    """One filler turn: unrelated status + ack + benign CI tool result."""
    text = f"status update {cycle}.{turn}: sprint notes. " + FILLER_SENTENCE * FILLER_SIZE
    return [
        {"kind": "user", "text": text},
        {"kind": "assistant", "text": f"Noted; continuing ({cycle}).", "usageInput": 30.0},
        {
            "kind": "toolResult",
            "text": f"cargo test --lib telemetry::tests_{cycle}{turn}: test result: {18 + cycle} passed; 0 failed",
            "toolCallId": f"call-filler-{cycle}-{turn}",
            "toolName": "bash",
        },
    ]


def _cycle_final_user(cycle: int) -> Dict[str, Any]:
    """Cycle-closing user message (large): keeps the compaction cut on a
    user entry.  With keepRecentTokens=20 a trailing tool result would move
    the cut onto an assistant entry (split turn), the one shape the W7
    mirror never exercised; every cycle therefore ends with this user turn.
    """
    text = f"status update {cycle}.final: moving to the next batch. " + FILLER_SENTENCE * 30
    return {"kind": "user", "text": text}


def _cycle_entries(extra_first_cycle: Optional[List[Dict[str, Any]]] = None) -> List[List[Dict[str, Any]]]:
    cycles: List[List[Dict[str, Any]]] = []
    for cycle in range(CYCLES):
        turn_entries: List[Dict[str, Any]] = []
        for turn in range(FILLER_TURNS):
            turn_entries.extend(_filler_turn(cycle, turn))
        if cycle == 0 and extra_first_cycle:
            turn_entries.extend(extra_first_cycle)
        turn_entries.append(_cycle_final_user(cycle))
        cycles.append(turn_entries)
    return cycles


def _scenario(name: str, seed_entries: List[Dict[str, Any]], probes: List[Dict[str, Any]],
               extra_first_cycle: Optional[List[Dict[str, Any]]] = None,
               restart_after_cycle: Optional[int] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "family": "coding-task",
        "arms": ["default", "taskState"],
        "cycles": CYCLES,
        "contextWindow": CONTEXT_WINDOW,
        "reserveTokens": RESERVE_TOKENS,
        "keepRecentTokens": KEEP_RECENT_TOKENS,
        "seedEntries": seed_entries,
        "cycleEntries": _cycle_entries(extra_first_cycle),
        "probes": probes,
        "restartAfterCycle": restart_after_cycle,
        "scoringVersion": "r2.3+e3-v1",
    }


def build_task_a() -> Dict[str, Any]:
    """Constraint during implementation: never edit src/auth.rs."""
    rng = random.Random(stable_seed("r2-e3-task-a", "v1"))
    parser_line = 60 + rng.randrange(40)
    auth_line = 150 + rng.randrange(80)
    lexer_passed = 8 + rng.randrange(10)
    seed_entries = [
        {
            "kind": "user",
            "text": (
                "TASK GOAL: implement strict JSON schema validation for the orders-api "
                "service so invalid configs are rejected at load time.\n\n"
                "CONSTRAINT: do not touch the auth module (src/auth.rs) - the security team "
                "owns it and every change there must go through their review. R2E3-A-C1\n"
                "CONSTRAINT: keep the public REST API backward compatible. R2E3-A-C2"
            ),
        },
        {
            "kind": "assistant",
            "text": "Plan: reproduce the failing validation test, locate the violating frame, then fix the check.",
            "usageInput": 24.0,
        },
        {
            "kind": "toolResult",
            "text": (
                "cargo test --lib parser\n"
                "running 1 test\n"
                "test parser::tests::validate_nested ... FAILED\n\n"
                "failures:\n\n"
                "---- parser::tests::validate_nested stdout ----\n"
                "schema violation at depth 2: field 'limits' expects u64\n"
                "stack backtrace:\n"
                f"   0: orders_api::parser::validate at src/parser.rs:{parser_line}\n"
                f"   1: orders_api::auth::check_scope at src/auth.rs:{auth_line}\n"
                "   2: parser::tests::validate_nested at tests/parser.rs:12"
            ),
            "toolCallId": "call-a-test",
            "toolName": "bash",
        },
        {
            "kind": "assistant",
            "text": (
                "Decision: the scope check at src/auth.rs:" + str(auth_line) + " is the root cause, "
                "but src/auth.rs is off-limits; the fix goes in src/parser.rs - validate the "
                "'limits' field before the auth call."
            ),
            "usageInput": 40.0,
        },
        {"kind": "user", "text": "Also verify src/lexer.rs handles the new schema - but make no changes there if it is clean."},
        {
            "kind": "toolResult",
            "text": f"cargo test --lib lexer\ntest result: {lexer_passed} passed; 0 failed (src/lexer.rs clean - no changes needed)",
            "toolCallId": "call-a-lexer",
            "toolName": "bash",
        },
        {
            "kind": "assistant",
            "text": "Lexer verified clean; continuing with the implementation.",
            "usageInput": 33.0,
        },
    ]
    probes = [
        {
            "id": "q-a-file",
            "kind": "action-file",
            "anchor": "R2E3-A-C1",
            "authority": "user",
            "question": "Which file do you edit next for the parser fix? Answer with the single file path.",
            "gold": "src/parser.rs (never src/auth.rs: the user forbade touching the auth module)",
            "goldStem": "parser",
            "goldPath": "src/parser.rs",
            "forbiddenStems": ["auth"],
        }
    ]
    return _scenario("r2-e3-task-a", seed_entries, probes)


def build_task_c() -> Dict[str, Any]:
    """Correction after implementation started: current approach is B."""
    rng = random.Random(stable_seed("r2-e3-task-c", "v1"))
    lock_lines = 48 + rng.randrange(40)
    handler_lines = 8 + rng.randrange(10)
    tests_passed = 8 + rng.randrange(10)
    seed_entries = [
        {"kind": "user", "text": "TASK GOAL: fix duplicate orders in the checkout flow of orders-api before the Friday release."},
        {
            "kind": "assistant",
            "text": "Decision: fix the duplicates with a synchronous lock on the order table. R2E3-C-D1",
            "usageInput": 20.0,
        },
        {
            "kind": "toolResult",
            "text": (
                "git apply --stat orders-fix.patch\n"
                f" src/orders/lock.rs    | {lock_lines} +++++++++\n"
                f" src/orders/handler.rs | {handler_lines} +-\n"
                " 2 files changed\n"
                f"test result: {tests_passed} passed; 0 failed"
            ),
            "toolCallId": "call-c-apply",
            "toolName": "bash",
        },
        {"kind": "assistant", "text": "Lock wrapper is in; running the checkout load test next.", "usageInput": 30.0},
        {
            "kind": "user",
            "text": (
                "CORRECTION: forget the synchronous lock - it serializes every checkout and "
                "the p99 collapses. Use idempotency keys on the request handler instead. "
                "The synchronous-lock approach is rejected. R2E3-C-K1"
            ),
        },
        {
            "kind": "assistant",
            "text": "Understood - switching the fix to idempotency keys on the request handler.",
            "usageInput": 28.0,
        },
    ]
    probes = [
        {
            "id": "q-c-approach",
            "kind": "action-approach",
            "anchor": "R2E3-C-K1",
            "authority": "user",
            "question": "What is the current approach for the duplicate-order fix? Answer with the approach in a few words.",
            "gold": "idempotency keys on the request handler (the synchronous lock was rejected)",
            "positives": ["idempot"],
            "forbiddenStems": ["lock"],
        }
    ]
    return _scenario("r2-e3-task-c", seed_entries, probes)


def build_task_e() -> Dict[str, Any]:
    """Evidence near boundary: the exact failing assertion is the last
    summarized entry before the cycle-1 compaction cut."""
    rng = random.Random(stable_seed("r2-e3-task-e", "v1"))
    total_tests = 50 + rng.randrange(16)
    passed_tests = total_tests - 1
    parser_line = 180 + rng.randrange(60)
    error_text = "assertion failed: depth == 4096 (got 3072)"
    seed_entries = [
        {"kind": "user", "text": "TASK GOAL: make the orders-api integration suite green before the release cut."},
        {"kind": "assistant", "text": "Running the full suite now.", "usageInput": 18.0},
    ]
    extra_first_cycle = [
        {
            "kind": "assistant",
            "text": "Suite finished: one failure - details next.",
            "usageInput": 20.0,
        },
        {
            "kind": "toolResult",
            "text": (
                "cargo test --lib\n"
                f"running {total_tests} tests\n"
                f"test result: {passed_tests} passed; 1 failed\n\n"
                "failures:\n\n"
                "---- parser::tests::parse_nested stdout ----\n"
                f"thread 'parser::tests::parse_nested' panicked at src/parser.rs:{parser_line}:\n"
                f"{error_text}\n"
                "note: run with `RUST_BACKTRACE=1` for a full backtrace"
            ),
            "toolCallId": "call-e-suite",
            "toolName": "bash",
        },
    ]
    probes = [
        {
            "id": "q-e-error",
            "kind": "action-error",
            "anchor": error_text,
            "authority": "tool",
            "question": "What exact error must you fix? Quote the exact failing assertion.",
            "gold": error_text,
            "requiredNumbers": ["4096", "3072"],
            "numberGapChars": 60,
            "keywords": ["assert", "depth", "panic"],
        }
    ]
    return _scenario("r2-e3-task-e", seed_entries, probes, extra_first_cycle=extra_first_cycle)


def build_task_h() -> Dict[str, Any]:
    """Avoid rejected approach: the acceptable set excludes the blob cache."""
    rng = random.Random(stable_seed("r2-e3-task-h", "v1"))
    p95 = 4200 + rng.randrange(1200)
    seed_entries = [
        {
            "kind": "user",
            "text": (
                "TASK GOAL: cut the orders-api report generation p95 from 4.8s to under 500ms.\n\n"
                "REJECTED APPROACH: do not cache the whole report as one blob - it goes stale "
                "within minutes and breaks correctness for downstream totals. R2E3-H-R1"
            ),
        },
        {
            "kind": "toolResult",
            "text": f"benchmark report-generation: p95 = {p95}ms (baseline before optimization)",
            "toolCallId": "call-h-bench",
            "toolName": "bash",
        },
        {
            "kind": "assistant",
            "text": (
                "Speedup options: (1) per-fragment caching keyed on the query hash; "
                "(2) incremental materialized views refreshed on order writes; "
                "(3) precomputed per-customer rollups. Benchmarking all three next; no pick yet."
            ),
            "usageInput": 36.0,
        },
        {"kind": "user", "text": "Good - keep all three options open until the benchmarks are in."},
        {"kind": "assistant", "text": "Benchmark plan set; running the option comparison next.", "usageInput": 22.0},
    ]
    probes = [
        {
            "id": "q-h-acceptable",
            "kind": "action-acceptable",
            "anchor": "R2E3-H-R1",
            "authority": "user",
            "question": "Which approaches are acceptable? List only the acceptable approaches, comma-separated.",
            "gold": "per-fragment caching, incremental materialized views, precomputed per-customer rollups (whole-report blob cache excluded)",
            "acceptable": ["fragment", "materialized", "rollup"],
            "forbiddenStems": ["blob"],
        }
    ]
    return _scenario("r2-e3-task-h", seed_entries, probes)


def build_task_f() -> Dict[str, Any]:
    """Restart: correction semantic plus a simulated session restart after cycle 3."""
    rng = random.Random(stable_seed("r2-e3-task-f", "v1"))
    reconcile_lines = 70 + rng.randrange(40)
    scheduler_lines = 6 + rng.randrange(8)
    tests_passed = 6 + rng.randrange(8)
    seed_entries = [
        {"kind": "user", "text": "TASK GOAL: fix the inventory sync drift between orders-api and the warehouse service."},
        {
            "kind": "assistant",
            "text": "Decision: fix the sync drift with a nightly reconciliation job. R2E3-F-D1",
            "usageInput": 20.0,
        },
        {
            "kind": "toolResult",
            "text": (
                "git apply --stat sync-fix.patch\n"
                f" src/sync/reconcile.rs | {reconcile_lines} +++++\n"
                f" src/sync/scheduler.rs  | {scheduler_lines} +-\n"
                " 2 files changed\n"
                f"test result: {tests_passed} passed; 0 failed"
            ),
            "toolCallId": "call-f-apply",
            "toolName": "bash",
        },
        {"kind": "assistant", "text": "Nightly reconciliation job implemented and scheduled.", "usageInput": 30.0},
        {
            "kind": "user",
            "text": (
                "CORRECTION: a nightly job drifts up to 24 hours - too stale for inventory. "
                "Use event-driven sync over the change-data-capture stream instead. "
                "The nightly reconciliation approach is rejected. R2E3-F-K1"
            ),
        },
        {
            "kind": "assistant",
            "text": "Switching the sync fix to the change-data-capture stream.",
            "usageInput": 26.0,
        },
    ]
    probes = [
        {
            "id": "q-f-approach",
            "kind": "action-approach",
            "anchor": "R2E3-F-K1",
            "authority": "user",
            "question": "What is the current approach for the inventory sync fix? Answer with the approach in a few words.",
            "gold": "event-driven sync over the change-data-capture (CDC) stream (nightly reconciliation rejected)",
            "positives": ["cdc", "change data capture", "capture", "event driven"],
            "forbiddenStems": ["nightly"],
        }
    ]
    return _scenario("r2-e3-task-f", seed_entries, probes, restart_after_cycle=3)


BUILDERS = {
    "A": build_task_a,
    "C": build_task_c,
    "E": build_task_e,
    "H": build_task_h,
    "F": build_task_f,
}


def build_e3_scenario(task_id: str) -> Dict[str, Any]:
    return BUILDERS[task_id.upper()]()
