"""Scenario generation for the production-path multicycle adapter.

A scenario is a JSON document the Rust adapter
(crates/pi-coding-agent/tests/rot2_multicycle.rs) consumes via
ROT2_SCENARIO. Generation is fully deterministic for a given
(scenario_id, seed): seeds derive from blake2b of stable identifiers.
"""

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import SCORING_VERSION


def stable_seed(*parts: str) -> int:
    """Deterministic seed from blake2b of the identifier parts (fixes M5)."""
    digest = hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


@dataclass(frozen=True)
class Probe:
    id: str
    anchor: str
    gold: str
    kind: str  # constraint | decision | evidence | supersession | task
    authority: str = "user"  # who authored the source line


@dataclass
class ScenarioSpec:
    scenario_id: str
    cycles: int = 5
    arms: Sequence[str] = ("default", "taskState")
    n_constraints: int = 8
    n_corrections: int = 2
    n_decisions: int = 4
    n_evidence: int = 4
    n_filler_turns: int = 3
    filler_size: int = 120


CONSTRAINT_TEMPLATES = [
    "CONSTRAINT: never drop the {name} ledger {tag}",
    "CONSTRAINT: always run the {name} suite before publishing {tag}",
    "CONSTRAINT: do not modify files under {name}/ without review {tag}",
    "CONSTRAINT: keep the {name} protocol version at v2 {tag}",
    "CONSTRAINT: the {name} migration must be reversible {tag}",
    "CONSTRAINT: no new dependencies in {name} without approval {tag}",
    "CONSTRAINT: preserve the {name} audit trail verbatim {tag}",
    "CONSTRAINT: the {name} service stays single-writer {tag}",
]

DECISION_TEMPLATES = [
    "Decision: use architecture {name} for the parser {tag}",
    "Decision: keep the {name} client in-process {tag}",
    "Decision: reject the {name} rewrite; extend instead {tag}",
    "Decision: the {name} fix lands behind a feature flag {tag}",
]

EVIDENCE_TEMPLATES = [
    "cargo test: {count} passed; 0 failed. Verified evidence {tag}",
    "build succeeded with {count} warnings. Verified evidence {tag}",
    "audit report {name}: {count} findings, all closed. Verified evidence {tag}",
    "benchmark {name}: p95 {count}ms. Verified evidence {tag}",
]

NAMES = [
    "payments", "billing", "ledger", "auth", "gateway", "scheduler",
    "reporting", "indexer", "reconciler", "notifier", "archiver", "validator",
]


def _tag(scenario_id: str, kind: str, index: int) -> str:
    digest = hashlib.blake2b(
        f"{scenario_id}|{kind}|{index}".encode("utf-8"), digest_size=4
    ).hexdigest()
    return f"R2-{kind.upper()[:4]}-{digest}"


def build_scenario(spec: ScenarioSpec) -> Dict[str, Any]:
    rng = random.Random(stable_seed(spec.scenario_id, "v1"))

    names = list(NAMES)
    rng.shuffle(names)

    probes: List[Probe] = []
    seed_entries: List[Dict[str, Any]] = []

    # Opening turn: goal + constraints (user authority).
    constraint_lines = []
    for i in range(spec.n_constraints):
        name = names[i % len(names)]
        tag = _tag(spec.scenario_id, "constraint", i)
        template = CONSTRAINT_TEMPLATES[i % len(CONSTRAINT_TEMPLATES)]
        constraint_lines.append(template.format(name=name, tag=tag))
        probes.append(
            Probe(
                id=f"q-constraint-{i}",
                anchor=tag,
                gold=template.format(name=name, tag=""),
                kind="constraint",
                authority="user",
            )
        )
    opening = (
        "TASK GOAL: migrate the billing service to API v2 before Friday.\n"
        + "\n".join(constraint_lines)
    )
    seed_entries.append({"kind": "user", "text": opening})
    seed_entries.append(
        {"kind": "assistant", "text": "Started the migration plan.", "usageInput": 12.0}
    )

    # Decisions (assistant authority, later verified by evidence).
    for i in range(spec.n_decisions):
        name = names[(i + 3) % len(names)]
        tag = _tag(spec.scenario_id, "decision", i)
        template = DECISION_TEMPLATES[i % len(DECISION_TEMPLATES)]
        text = template.format(name=name, tag=tag)
        probes.append(
            Probe(
                id=f"q-decision-{i}",
                anchor=tag,
                gold=template.format(name=name, tag=""),
                kind="decision",
                authority="assistant",
            )
        )
        seed_entries.append({"kind": "assistant", "text": text, "usageInput": 40.0})

    # Evidence (tool results, verified authority).
    for i in range(spec.n_evidence):
        name = names[(i + 6) % len(names)]
        tag = _tag(spec.scenario_id, "evidence", i)
        count = 4000 + (i * 137) % 900
        template = EVIDENCE_TEMPLATES[i % len(EVIDENCE_TEMPLATES)]
        text = template.format(name=name, count=count, tag=tag)
        probes.append(
            Probe(
                id=f"q-evidence-{i}",
                anchor=tag,
                gold=str(count),
                kind="evidence",
                authority="tool",
            )
        )
        seed_entries.append(
            {
                "kind": "toolResult",
                "text": text,
                "toolCallId": f"call-evidence-{i}",
                "toolName": "bash",
            }
        )

    # Corrections (user authority, explicit supersession markers) with a
    # superseded older decision that must NOT be treated as current (M2 fix:
    # the authority rule is explicit and each probe has a unique gold).
    for i in range(spec.n_corrections):
        name = names[(i + 9) % len(names)]
        old_tag = _tag(spec.scenario_id, "superseded", i)
        new_tag = _tag(spec.scenario_id, "correction", i)
        old_line = f"Decision: use architecture A for the {name} module {old_tag}"
        new_line = (
            f"CORRECTION: forget that; use architecture B for the {name} module "
            f"instead {new_tag}. The earlier A decision is superseded."
        )
        seed_entries.append({"kind": "assistant", "text": old_line, "usageInput": 20.0})
        seed_entries.append({"kind": "user", "text": new_line})
        probes.append(
            Probe(
                id=f"q-correction-{i}",
                anchor=new_tag,
                gold="architecture B",
                kind="supersession",
                authority="user",
            )
        )

    # Subagent report mid-conversation.
    seed_entries.append(
        {
            "kind": "agentMessage",
            "text": "Subagent reviewer-1 finished section 3; artifact evidence/reviewer-1.json",
            "fromName": "reviewer-1",
        }
    )
    probes.append(
        Probe(
            id="q-subagent",
            anchor="evidence/reviewer-1.json",
            gold="reviewer-1 finished section 3",
            kind="task",
            authority="child",
        )
    )

    # Boundary-crossing retained turn.
    seed_entries.append({"kind": "user", "text": "Continue the migration."})
    seed_entries.append(
        {
            "kind": "assistant",
            "text": "Section 3 verified; publishing next.",
            "usageInput": 17_000.0,
        }
    )

    # Filler cycles: unrelated historical discussion with no anchors.
    cycle_entries: List[List[Dict[str, Any]]] = []
    for cycle in range(spec.cycles):
        turn: List[Dict[str, Any]] = []
        for turn_index in range(spec.n_filler_turns):
            filler = "unrelated historical discussion. "
            text = (
                f"status update {cycle}.{turn_index}: integration notes. "
                + filler * spec.filler_size
            )
            turn.append({"kind": "user", "text": text})
            turn.append(
                {"kind": "assistant", "text": f"Noted; continuing ({cycle}).", "usageInput": 30.0}
            )
        cycle_entries.append(turn)

    return {
        "name": spec.scenario_id,
        "arms": list(spec.arms),
        "cycles": spec.cycles,
        "contextWindow": 20_000.0,
        "reserveTokens": 4_000.0,
        "keepRecentTokens": 20.0,
        "seedEntries": seed_entries,
        "cycleEntries": cycle_entries,
        "probes": [
            {
                "id": p.id,
                "anchor": p.anchor,
                "gold": p.gold,
                "kind": p.kind,
                "authority": p.authority,
            }
            for p in probes
        ],
        "scoringVersion": SCORING_VERSION,
    }


def write_scenario(scenario: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(scenario, handle, indent=1)


def load_result(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
