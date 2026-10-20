"""Deterministic scoring for production-path multicycle runs (r2).

Category discipline (M-fix): a probe is classified
- retained: its anchor appears in the restored provider context.
- lost: the anchor is absent everywhere in the final context.

The live-model phase adds retrieval/interpretation/action categories on
top of the same records; those are scored separately and never mixed with
retention (which measures the pipeline, not the model).
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from . import SCORING_VERSION


@dataclass(frozen=True)
class ArmScore:
    arm: str
    probes_total: int
    retained: int
    by_kind: Dict[str, Dict[str, int]]
    cycles_run: int
    ledger_cycles: int
    mean_request_chars: float
    mean_context_tokens: float


def score_arm(result_arm: Dict[str, Any]) -> ArmScore:
    probes = result_arm["probes"]
    by_kind: Dict[str, Dict[str, int]] = {}
    retained = 0
    for probe in probes:
        kind = "unknown"
        # kind comes from the scenario; the adapter result carries it via
        # the probe record's presence in the scenario file, so the caller
        # joins them. Here we fall back to counting found_in only.
        bucket = by_kind.setdefault(kind, {"total": 0, "retained": 0})
        bucket["total"] += 1
        if probe["retained"]:
            retained += 1
            bucket["retained"] += 1
    cycles = [c for c in result_arm["cycles"] if c["ran"]]
    return ArmScore(
        arm=result_arm["arm"],
        probes_total=len(probes),
        retained=retained,
        by_kind=by_kind,
        cycles_run=len(cycles),
        ledger_cycles=sum(1 for c in cycles if c.get("ledgerPresent")),
        mean_request_chars=(
            sum(c["summarizerRequestChars"] for c in cycles) / len(cycles) if cycles else 0.0
        ),
        mean_context_tokens=(
            sum(c["compactedContextTokens"] for c in cycles) / len(cycles) if cycles else 0.0
        ),
    )


def score_result(result: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Join adapter output with scenario kinds and score every arm."""
    kinds = {p["id"]: (p.get("kind", "unknown"), p.get("authority", "unknown")) for p in scenario["probes"]}
    arms = []
    for arm in result["arms"]:
        by_kind: Dict[str, Dict[str, int]] = {}
        retained = 0
        for probe in arm["probes"]:
            kind, authority = kinds.get(probe["id"], ("unknown", "unknown"))
            bucket = by_kind.setdefault(kind, {"total": 0, "retained": 0, "lost": 0})
            bucket["total"] += 1
            if probe["retained"]:
                retained += 1
                bucket["retained"] += 1
            else:
                bucket["lost"] += 1
        cycles = [c for c in arm["cycles"] if c["ran"]]
        arms.append(
            {
                "arm": arm["arm"],
                "probesTotal": len(arm["probes"]),
                "retained": retained,
                "retentionRate": round(retained / len(arm["probes"]), 4) if arm["probes"] else 0.0,
                "byKind": by_kind,
                "cyclesRun": len(cycles),
                "ledgerCycles": sum(1 for c in cycles if c.get("ledgerPresent")),
                "meanRequestChars": (
                    round(sum(c["summarizerRequestChars"] for c in cycles) / len(cycles), 1)
                    if cycles
                    else 0.0
                ),
                "meanContextTokens": (
                    round(sum(c["compactedContextTokens"] for c in cycles) / len(cycles), 1)
                    if cycles
                    else 0.0
                ),
                "probes": arm["probes"],
            }
        )
    return {
        "scenario": result.get("scenario", scenario["name"]),
        "scoringVersion": SCORING_VERSION,
        "arms": arms,
    }


def write_score(score: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(score, handle, indent=1)
