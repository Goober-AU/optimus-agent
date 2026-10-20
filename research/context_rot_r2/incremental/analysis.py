"""Shared analysis for the W9 experiments.

All functions are pure post-processing of adapter result JSONs; nothing here
touches the pipeline. Chars are converted to tokens at 4 chars/token as
directed by the W9 brief (a fixed conversion factor, not a tokenizer).
"""

import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from research.context_rot_r2.scenario import load_result  # noqa: E402

CHARS_PER_TOKEN = 4.0

# Prompt constant lengths in Unicode chars, measured from the read-only source
# crates/pi-coding-agent/src/core/compaction/compaction.rs (legacy variants,
# which `default_compaction_settings()` pins) and
# crates/pi-coding-agent/src/core/compaction/task_state.rs (LEDGER_INSTRUCTION).
PROMPT_CHARS = {
    "first": 879,       # SUMMARIZATION_PROMPT (no previous summary)
    "update": 1257,     # UPDATE_SUMMARIZATION_PROMPT (policy=off, previous exists)
    "consolidating": 2650,  # CONSOLIDATING_UPDATE_SUMMARIZATION_PROMPT (policy=consolidate-repeated-v1)
    "kernelNote": 276,  # KERNEL_PERSIST_SUMMARY_NOTE (always appended)
    "ledgerInstruction": 274,  # appended after the protected ledger block
}
PREVIOUS_SUMMARY_DECAY_KEEP_CHARS = 400  # adapter fixture: carried-summary decay


def tokens(chars: float) -> float:
    return round(chars / CHARS_PER_TOKEN, 1)


def arm(result: Dict[str, Any], arm_name: str) -> Optional[Dict[str, Any]]:
    for entry in result.get("arms", []):
        if entry["arm"] == arm_name:
            return entry
    return None


def ran_cycles(arm_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in arm_result["cycles"] if c["ran"]]


def kind_of(scenario: Dict[str, Any]) -> Dict[str, str]:
    return {p["id"]: p.get("kind", "unknown") for p in scenario["probes"]}


def retention_by_cycle(
    arm_result: Dict[str, Any], scenario: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Per-cycle probe retention, overall and by kind, from anchorsPresent."""
    kinds = kind_of(scenario)
    total_by_kind: Dict[str, int] = {}
    for kind in kinds.values():
        total_by_kind[kind] = total_by_kind.get(kind, 0) + 1
    rows = []
    for cycle in ran_cycles(arm_result):
        present = set(cycle["anchorsPresent"])
        by_kind: Dict[str, Dict[str, int]] = {}
        for probe_id, kind in kinds.items():
            bucket = by_kind.setdefault(kind, {"total": 0, "present": 0})
            bucket["total"] += 1
            if probe_id in present:
                bucket["present"] += 1
        rows.append(
            {
                "cycle": cycle["cycle"],
                "retained": len(present),
                "probes": len(kinds),
                "rate": round(len(present) / len(kinds), 4),
                "byKind": {
                    kind: {
                        "present": bucket["present"],
                        "total": bucket["total"],
                        "rate": round(bucket["present"] / bucket["total"], 4),
                    }
                    for kind, bucket in sorted(by_kind.items())
                },
            }
        )
    return rows


def probe_last_cycle_present(
    arm_result: Dict[str, Any], scenario: Dict[str, Any]
) -> Dict[str, Optional[int]]:
    """Last cycle index after which each probe is still present (None = never)."""
    kinds = kind_of(scenario)
    last: Dict[str, Optional[int]] = {}
    for probe in scenario["probes"]:
        seen = [
            c["cycle"]
            for c in ran_cycles(arm_result)
            if probe["id"] in c["anchorsPresent"]
        ]
        last[probe["id"]] = max(seen) if seen else None
    return last


def growth_model(cycles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Summary growth model: stabilization cycle and per-cycle growth rates."""
    chars = [c["summaryChars"] for c in cycles]
    if not chars:
        return {}
    deltas = [chars[i] - chars[i - 1] for i in range(1, len(chars))]
    stabilize = None
    for start in range(1, len(chars)):
        if all(abs(chars[k] - chars[k - 1]) == 0 for k in range(start, len(chars))):
            stabilize = start - 1
            break
    return {
        "summaryChars": chars,
        "deltas": deltas,
        "initial": chars[0],
        "steady": chars[-1] if stabilize is not None else None,
        "stabilizeAfterCycle": stabilize,
        "totalGrowth": chars[-1] - chars[0],
        "meanGrowthPerCycleAfterFirst": (
            round((chars[-1] - chars[1]) / (len(chars) - 2), 2) if len(chars) > 2 else None
        ),
    }


def request_decomposition(
    default_cycles: List[Dict[str, Any]],
    task_state_cycles: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Decompose the per-cycle summarizer request size.

    The request body is `<conversation> chunk + <previous-summary> carried
    summary + prompt + (ledger block)`. The chunk is the new material, the
    carried summary is policy-invariant, and the prompt is the only
    policy-dependent term. Additivity is verified on cycle-over-cycle deltas.
    """
    requests = [c["summarizerRequestChars"] for c in default_cycles]
    summaries = [c["summaryChars"] for c in default_cycles]
    deltas = [requests[i] - requests[i - 1] for i in range(1, len(requests))]
    carry_deltas = [
        summaries[i - 1] - summaries[i - 2] for i in range(2, len(summaries))
    ]
    residuals = [
        deltas[i] - carry_deltas[i - 1] for i in range(1, len(deltas))
    ]
    out: Dict[str, Any] = {
        "requestChars": requests,
        "requestDeltas": deltas,
        "carryResiduals": residuals,
        "additivityHolds": all(abs(r) == 0 for r in residuals) if residuals else None,
        # Chunk-plus-fixed-overhead terms (conversation serialization wrapper
        # and separators included; small constants, ~33-73 chars).
        "chunkAndOverheadCycle0": requests[0] - PROMPT_CHARS["first"] - PROMPT_CHARS["kernelNote"],
        "chunkAndOverheadSteady": None,
        "ledgerTerm": None,
    }
    if len(requests) >= 2:
        out["chunkAndOverheadSteady"] = (
            requests[1] - summaries[0] - PROMPT_CHARS["update"] - PROMPT_CHARS["kernelNote"]
        )
    if task_state_cycles and len(task_state_cycles) == len(default_cycles):
        out["ledgerTerm"] = [
            ts["summarizerRequestChars"] - d["summarizerRequestChars"]
            for ts, d in zip(task_state_cycles, default_cycles)
        ]
    return out
