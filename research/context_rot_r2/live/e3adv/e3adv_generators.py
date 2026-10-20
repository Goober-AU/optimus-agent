"""E3ADV adversarial correction generators (R2-E3ADV, live GLM 5.3).

Three adversarial coding-task scenario families that remove W17-E3's
pairing luck (W17-E3 finding 2: the correction text shared the token
"synchronous" with the old decision, so the v1 ledger's token-based
supersession paired them by LUCK).  Every correction here shares NO
anchor token with the decision it replaces:

  ADV-C1  semantic swap  - approach A (two-phase commit coordinator) is
          chosen and implemented (tool diff), then corrected to B (saga
          pattern with compensating transactions).  The correction
          clauses share ZERO word tokens with the old decision, so the
          supersession score is 0.
  ADV-C2  numeric parameter - the retry budget of 3 attempts is
          corrected to 5 attempts with exponential backoff.  The only
          shared tokens are the generic parameter name ("retry",
          "budget"): score 2, below the ledger's pairing threshold 3.
  ADV-C3  retraction only - the user retracts the audit-log requirement
          entirely, naming no replacement.  The retraction shares only
          the generic words "audit"/"log" with the requirement and its
          build decision: max score 2, no retirement.

Verified offline (selftest): for every family, at every cycle,
ledger["superseded"] == [] - the old decision stays in DECISIONS
(current), the correction lands in ACTIVE CONSTRAINTS, and the render
carries no SUPERSEDED marker.  The taskState arm therefore hands the
summarizer contradictory currents every cycle, exactly the shape
W17-E3 could not measure.

Envelope identical to W17-E3 (filler and cycle-final helpers are
verbatim copies from e3/scenarios.py so the cut-point behavior matches;
e3 itself stays untouched): 2 arms (default, taskState) x 5 cycles x
probe-every-cycle, deterministic blake2b cosmetic seeds, fixed golds
and anchors.  Probes test ACTION, not recall.
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

FAMILY_IDS = ("C1", "C2", "C3")

# Fixed scenario text constants (golds and anchors are fixed; only
# cosmetic values draw from the blake2b seed).  The selftest asserts the
# adversarial property against these exact strings.
C1_DECISION_TEXT = "Decision: resolve the double-charges with a two-phase commit coordinator."
C1_CORRECTION_TEXT = (
    "CORRECTION: scrap that plan - blocking round-trips stall the checkout path "
    "under load. Use the saga pattern with compensating transactions instead. SGA-KX1"
)
C2_DECISION_TEXT = "Decision: the retry budget is 3 for the webhook client."
C2_CORRECTION_TEXT = (
    "CORRECTION: 3 attempts die out during endpoint brownouts - short bursts outrun "
    "the recoveries. The retry budget is 5 attempts with exponential backoff instead. RB5-KX1"
)
C3_REQUIREMENT_TEXT = "CONSTRAINT: every mutation must be recorded in the immutable audit log. AUD-REQ1"
C3_DECISION_TEXT = "Decision: build the audit log as an append-only events table with a database trigger."
C3_RETRACTION_TEXT = (
    "SCOPE CHANGE: we no longer need the audit log - drop it entirely. "
    "The CSV export and the per-tenant usage metering stay in scope. RT9-Q3"
)

# Stale anchors the ledger keeps asserting as current (selftest evidence).
STALE_DECISION_STEMS = {
    "C1": ("two", "phase", "commit", "coordinator"),
    "C2": ("retry", "budget", "3"),
    "C3": ("audit",),
}


def _filler_turn(cycle: int, turn: int) -> List[Dict[str, Any]]:
    """Verbatim from e3/scenarios.py (coding-session texture filler)."""
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
    """Verbatim from e3/scenarios.py (keeps the compaction cut on a user
    entry; see the W17-E3 deviation note 1)."""
    text = f"status update {cycle}.final: moving to the next batch. " + FILLER_SENTENCE * 30
    return {"kind": "user", "text": text}


def _cycle_entries(extra_first_cycle: Optional[List[Dict[str, Any]]] = None) -> List[List[Dict[str, Any]]]:
    """Verbatim from e3/scenarios.py (3 filler turns, cycle-final user)."""
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


def _scenario(name: str, seed_entries: List[Dict[str, Any]], probes: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "name": name,
        "family": "coding-task-adversarial",
        "arms": ["default", "taskState"],
        "cycles": CYCLES,
        "contextWindow": CONTEXT_WINDOW,
        "reserveTokens": RESERVE_TOKENS,
        "keepRecentTokens": KEEP_RECENT_TOKENS,
        "seedEntries": seed_entries,
        "cycleEntries": _cycle_entries(),
        "probes": probes,
        "restartAfterCycle": None,
        "scoringVersion": "r2.3+e3adv-v1",
    }


def build_family_c1() -> Dict[str, Any]:
    """Semantic-swap correction sharing zero tokens with the old decision."""
    rng = random.Random(stable_seed("r2-e3adv-c1", "v1"))
    coordinator_lines = 70 + rng.randrange(50)
    checkout_lines = 8 + rng.randrange(10)
    tests_passed = 10 + rng.randrange(10)
    seed_entries = [
        {"kind": "user", "text": "TASK GOAL: eliminate double-charges in the payments flow of billing-api before the Friday release."},
        {"kind": "assistant", "text": C1_DECISION_TEXT + " TPC-DEC1", "usageInput": 20.0},
        {
            "kind": "toolResult",
            "text": (
                "git apply --stat payments-fix.patch\n"
                f" src/payments/2pc_coordinator.rs | {coordinator_lines} +++++++++\n"
                f" src/payments/checkout.rs      | {checkout_lines} +-\n"
                " 2 files changed\n"
                f"test result: {tests_passed} passed; 0 failed"
            ),
            "toolCallId": "call-c1-apply",
            "toolName": "bash",
        },
        {"kind": "assistant", "text": "Two-phase commit coordinator wired in; running the payments load test next.", "usageInput": 30.0},
        {"kind": "user", "text": C1_CORRECTION_TEXT},
        {"kind": "assistant", "text": "Understood - switching to the saga pattern with compensating transactions on the billing service.", "usageInput": 26.0},
    ]
    probes = [
        {
            "id": "q-c1-approach",
            "kind": "action-approach",
            "anchor": "SGA-KX1",
            "authority": "user",
            "question": "Which approach do you implement next for the double-charge fix? Answer with the approach in a few words.",
            "gold": "the saga pattern with compensating transactions (two-phase commit coordinator rejected)",
            "positives": ["compensat", "saga"],
            "forbiddenStems": ["commit", "coordinator", "2pc"],
        }
    ]
    return _scenario("r2-e3adv-c1", seed_entries, probes)


def build_family_c2() -> Dict[str, Any]:
    """Numeric-parameter correction: retry budget 3 -> 5 attempts."""
    rng = random.Random(stable_seed("r2-e3adv-c2", "v1"))
    client_lines = 26 + rng.randrange(20)
    tests_passed = 8 + rng.randrange(8)
    seed_entries = [
        {"kind": "user", "text": "TASK GOAL: harden the orders-api webhook client against transient endpoint failures before the Friday release."},
        {"kind": "assistant", "text": C2_DECISION_TEXT + " RTB-DEC1", "usageInput": 20.0},
        {
            "kind": "toolResult",
            "text": (
                "git apply --stat retry-fix.patch\n"
                f" src/webhook/client.rs | {client_lines} +++++\n"
                " 1 file changed\n"
                f"test result: {tests_passed} passed; 0 failed"
            ),
            "toolCallId": "call-c2-apply",
            "toolName": "bash",
        },
        {"kind": "assistant", "text": "Retry cap of 3 wired into the client; soak-testing brownout windows next.", "usageInput": 30.0},
        {"kind": "user", "text": C2_CORRECTION_TEXT},
        {"kind": "assistant", "text": "Understood - the retry budget is 5 attempts with exponential backoff.", "usageInput": 26.0},
    ]
    probes = [
        {
            "id": "q-c2-budget",
            "kind": "action-number",
            "anchor": "RB5-KX1",
            "authority": "user",
            "question": "What is the current retry budget for the webhook client, in number of attempts? Answer with the number and its unit.",
            "gold": "5 attempts (with exponential backoff)",
            "goldNumber": "5",
            "forbiddenNumbers": ["3"],
        }
    ]
    return _scenario("r2-e3adv-c2", seed_entries, probes)


def build_family_c3() -> Dict[str, Any]:
    """Retraction-only scope change: no replacement named."""
    rng = random.Random(stable_seed("r2-e3adv-c3", "v1"))
    audit_lines = 60 + rng.randrange(50)
    csv_lines = 30 + rng.randrange(20)
    metering_lines = 45 + rng.randrange(30)
    tests_passed = 18 + rng.randrange(10)
    seed_entries = [
        {"kind": "user", "text": "TASK GOAL: ship the v2 milestone for the orders-api reporting service with three features: a CSV export, per-tenant usage metering, and an immutable audit log."},
        {"kind": "user", "text": C3_REQUIREMENT_TEXT},
        {"kind": "assistant", "text": C3_DECISION_TEXT + " AUD-DEC1", "usageInput": 20.0},
        {
            "kind": "toolResult",
            "text": (
                "git apply --stat v2-features.patch\n"
                f" src/reporting/audit_log.rs | {audit_lines} +++++++\n"
                f" src/reporting/csv_export.rs | {csv_lines} +++++\n"
                f" src/reporting/metering.rs  | {metering_lines} ++++++\n"
                " 3 files changed\n"
                f"test result: {tests_passed} passed; 0 failed"
            ),
            "toolCallId": "call-c3-apply",
            "toolName": "bash",
        },
        {"kind": "assistant", "text": "All three v2 features implemented - audit log, CSV export, and usage metering; running the verification suite next.", "usageInput": 30.0},
        {"kind": "user", "text": C3_RETRACTION_TEXT},
        {"kind": "assistant", "text": "Understood - dropping the audit log; the CSV export and the usage metering stay in scope.", "usageInput": 26.0},
    ]
    probes = [
        {
            "id": "q-c3-scope",
            "kind": "action-acceptable",
            "anchor": "RT9-Q3",
            "authority": "user",
            "question": "Which features remain in scope for the v2 milestone? List only the remaining features, comma-separated.",
            "gold": "CSV export, per-tenant usage metering (audit log retracted)",
            "acceptable": ["csv", "export", "metering", "usage"],
            "forbiddenStems": ["audit"],
        }
    ]
    return _scenario("r2-e3adv-c3", seed_entries, probes)


BUILDERS = {
    "C1": build_family_c1,
    "C2": build_family_c2,
    "C3": build_family_c3,
}


def build_e3adv_scenario(family_id: str) -> Dict[str, Any]:
    return BUILDERS[family_id.upper()]()
