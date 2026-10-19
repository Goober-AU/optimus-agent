"""Offline validation for the Optimus Rust CompactBench adapter.

Runs the real harness binary through the adapter and checks the CompactionArtifact
contract. No network, no model calls. Run with the campaign's CompactBench venv
(has compactbench + pydantic):

    C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv/Scripts/python.exe \
        research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py

Exits non-zero on the first failure. HARNESS (or ROT2_HARNESS_BIN) selects the
binary, defaulting to the shared debug build.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compactbench.contracts import CompactionArtifact, Transcript, Turn, TurnRole  # noqa: E402

from optimus_compactor import (  # noqa: E402
    EchoFirstNCompactor,
    FullTranscriptOracleCompactor,
    OptimusRustCompactorPlain,
    OptimusRustCompactorTaskState,
    structured_state_from_ledger,
    tail_candidates,
)

HARNESS_NOTE = "requires the harness binary (see README.md)"


def _turns(pairs: list[tuple[str, str]]) -> list[Turn]:
    return [
        Turn(id=index, role=TurnRole(role), content=content)
        for index, (role, content) in enumerate(pairs)
    ]


def _transcript(pairs: list[tuple[str, str]]) -> Transcript:
    return Transcript(turns=_turns(pairs))


MARKED = [
    ("user", "Project setup. CONSTRAINT: never store raw passwords. DECISION: we use PostgreSQL 16."),
    ("assistant", "Understood; passwords stay hashed."),
    ("user", "EVIDENCE: staging runs 12 nodes. Please keep replies terse."),
    ("assistant", "Noted. Anything else?"),
    ("user", "Add the export feature next."),
    ("assistant", "Export feature queued."),
    ("user", "Thanks, that is all for now."),
    ("assistant", "Great, I will keep the constraints in mind."),
]

STARTER_SHAPE = [
    ("user", "Let's plan the pricing revamp with Gabriel."),
    ("assistant", "Happy to help. What constraints should I keep in mind?"),
    ("user", "Critical: never ignore rate limits. That is a hard rule."),
    ("user", "Do you happen to know what a quokka is?"),
    ("assistant", "Sure, I can address that. Is there anything more on the main thread?"),
    ("user", "Quick tangent: is it true that pandas have thumbs?"),
    ("assistant", "Quick aside, sure. Anything else pressing?"),
    ("user", "OK, given all that, what do you suggest as the next step?"),
]


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_task_state_arm() -> None:
    compactor = OptimusRustCompactorTaskState(None, "unused")
    artifact = asyncio.run(compactor.compact(_transcript(MARKED)))
    check(artifact.summary_text.strip() != "", "task-state arm: empty summary")
    check("## Goal" in artifact.summary_text, "task-state arm: fixed sections missing")
    check(
        artifact.selected_source_turn_ids == list(range(len(MARKED))),
        f"task-state arm: selected ids {artifact.selected_source_turn_ids} != all turn ids",
    )
    check(
        any("raw passwords" in item for item in artifact.structured_state.forbidden_behaviors),
        f"task-state arm: constraint not mapped, got {artifact.structured_state.forbidden_behaviors}",
    )
    check(
        any("PostgreSQL" in item for item in artifact.structured_state.locked_decisions),
        f"task-state arm: decision not mapped, got {artifact.structured_state.locked_decisions}",
    )
    check(artifact.structured_state.entity_map == {}, "task-state arm: entity_map must stay empty")
    check(artifact.method_metadata["taskState"] == "prompt", "task-state arm: wrong taskState")
    check(artifact.method_metadata["fallback"] is None, "task-state arm: unexpected fallback")
    check(artifact.method_metadata["harnessCalls"] >= 1, "task-state arm: harness never invoked")
    compactor.close()
    print(f"PASS task-state arm (attempts={len(artifact.method_metadata['attempts'])}, "
          f"state entries={len(artifact.structured_state.forbidden_behaviors)})")


def test_plain_arm() -> None:
    compactor = OptimusRustCompactorPlain(None, "unused")
    artifact = asyncio.run(compactor.compact(_transcript(MARKED)))
    check(artifact.summary_text.strip() != "", "plain arm: empty summary")
    check(
        artifact.structured_state.forbidden_behaviors == [],
        f"plain arm: ledger must be null, got {artifact.structured_state.forbidden_behaviors}",
    )
    check(artifact.method_metadata["taskState"] == "off", "plain arm: wrong taskState")
    compactor.close()
    print("PASS plain arm (structured state empty as expected)")


def test_starter_shape_compacts() -> None:
    compactor = OptimusRustCompactorTaskState(None, "unused")
    artifact = asyncio.run(compactor.compact(_transcript(STARTER_SHAPE)))
    check(artifact.method_metadata["fallback"] is None, "starter shape: fell back to full render")
    check(
        artifact.selected_source_turn_ids == list(range(len(STARTER_SHAPE))),
        "starter shape: selected ids must cover the transcript",
    )
    check(
        any("rate limits" in item for item in artifact.structured_state.forbidden_behaviors),
        f"starter shape: critical constraint lost, got {artifact.structured_state.forbidden_behaviors}",
    )
    compactor.close()
    print("PASS starter-shaped transcript (non-alternating roles) compacts with a valid cut")


def _mask_entry_hashes(text: str) -> str:
    """Mask per-session entry ids: the pipeline stamps ledger lines with the
    session entry hash (e.g. "- C1 [940ab4db]: ..."), which differs on every
    harness invocation. Everything else in the output is deterministic."""
    import re

    return re.sub(r"\[[0-9a-f]{8}\]", "[entry]", text)


def test_determinism() -> None:
    first = OptimusRustCompactorTaskState(None, "unused")
    second = OptimusRustCompactorTaskState(None, "unused")
    a1 = asyncio.run(first.compact(_transcript(MARKED)))
    a2 = asyncio.run(second.compact(_transcript(MARKED)))
    check(
        _mask_entry_hashes(a1.summary_text) == _mask_entry_hashes(a2.summary_text),
        "determinism: summaries differ across instances (beyond entry ids)",
    )
    check(a1.structured_state == a2.structured_state, "determinism: structured state differs")
    check(
        a1.selected_source_turn_ids == a2.selected_source_turn_ids,
        "determinism: selected ids differ across instances",
    )
    first.close()
    second.close()
    print("PASS determinism (same transcript -> identical artifact modulo session entry ids)")


def test_small_transcript_fallback() -> None:
    compactor = OptimusRustCompactorTaskState(None, "unused")
    small = [("user", "Only one exchange here."), ("assistant", "Understood.")]
    artifact = asyncio.run(compactor.compact(_transcript(small)))
    check(
        artifact.method_metadata["fallback"] == "uncompacted-transcript",
        f"small transcript: expected fallback, got {artifact.method_metadata['fallback']}",
    )
    check("Only one exchange here." in artifact.summary_text, "small transcript: turn text lost")
    check("Understood." in artifact.summary_text, "small transcript: turn text lost")
    check(
        any("no compaction" in warning.lower() or "harness" in warning.lower()
            for warning in artifact.warnings),
        f"small transcript: fallback must warn, got {artifact.warnings}",
    )
    check(
        artifact.selected_source_turn_ids == [0, 1],
        "small transcript: selected ids must be the full transcript",
    )
    compactor.close()
    print("PASS small transcript falls back to the un-compacted render with warnings")


def test_missing_binary_falls_back() -> None:
    saved = os.environ.get("HARNESS")
    os.environ["HARNESS"] = str(Path(__file__).with_name("does-not-exist.exe"))
    try:
        compactor = OptimusRustCompactorTaskState(None, "unused")
        artifact = asyncio.run(compactor.compact(_transcript(MARKED)))
        check(
            artifact.method_metadata["fallback"] == "uncompacted-transcript",
            "missing binary: expected fallback artifact",
        )
        check(
            any("spawn failed" in warning for warning in artifact.warnings),
            f"missing binary: spawn failure must be warned, got {artifact.warnings}",
        )
        check("## Goal" not in artifact.summary_text, "missing binary: summary must be the render")
        compactor.close()
    finally:
        if saved is None:
            os.environ.pop("HARNESS", None)
        else:
            os.environ["HARNESS"] = saved
    print("PASS missing harness binary degrades to fallback with a loud warning")


def test_ladder_candidates() -> None:
    turns = _turns(STARTER_SHAPE)
    candidates = tail_candidates(turns)
    check(len(candidates) >= 3, f"ladder: expected several candidates, got {candidates}")
    check(all(value > 0 for value in candidates), "ladder: candidates must be positive")
    check(
        candidates == sorted(candidates),
        "ladder: candidates must be ordered most aggressive first",
    )
    print(f"PASS cut-point ladder ({len(candidates)} candidates: {[round(c, 1) for c in candidates]})")


def test_ledger_mapping_shapes() -> None:
    ledger = {
        "constraints": [{"id": "C1", "text": "never store raw passwords", "entry": "e"}],
        "decisions": [{"id": "D1", "text": "use PostgreSQL 16", "entry": "e"}],
        "superseded": [{"id": "S1", "text": "we used MySQL", "entry": "e"}],
        "evidence": [{"id": "E1", "text": "p95 latency is 480 ms", "entry": "e"}],
        "anchors": ["API"],
        "openWork": [{"id": "W1", "text": "pick a cache library", "entry": "e"}],
        "retractions": [{"id": "R1", "text": "ignore the MySQL note", "entry": "e"}],
    }
    state = structured_state_from_ledger(ledger)
    check(state.forbidden_behaviors == ["never store raw passwords"], "mapping: constraints")
    check(state.locked_decisions == ["use PostgreSQL 16"], "mapping: decisions")
    check(state.deferred_items == ["we used MySQL"], "mapping: superseded")
    check(state.immutable_facts == ["p95 latency is 480 ms", "API"], "mapping: evidence+anchors")
    check(state.unresolved_items == ["pick a cache library"], "mapping: openWork")
    check(state.entity_map == {}, "mapping: entity_map must stay empty")
    empty = structured_state_from_ledger(None)
    check(empty.forbidden_behaviors == [], "mapping: null ledger must map to empty state")
    bare = structured_state_from_ledger({"constraints": ["bare string"], "anchors": "not-a-list"})
    check(bare.forbidden_behaviors == ["bare string"], "mapping: bare strings tolerated")
    print("PASS ledger mapping (dict entries, anchors, null, bare strings)")


def test_controls() -> None:
    pairs = [(role, f"turn {i}") for i, (role, _) in enumerate(
        [("user", ""), ("assistant", "")] * 6)]
    transcript = _transcript(pairs)
    echo = EchoFirstNCompactor(None, "unused")
    artifact = asyncio.run(echo.compact(transcript))
    check(artifact.selected_source_turn_ids == list(range(10)), "echo-first-n: ids must be first 10")
    check("turn 9" in artifact.summary_text and "turn 10" not in artifact.summary_text,
          "echo-first-n: boundary turn missing/excess")
    oracle = FullTranscriptOracleCompactor(None, "unused")
    artifact = asyncio.run(oracle.compact(transcript))
    check(artifact.selected_source_turn_ids == list(range(12)), "oracle: ids must be all turns")
    check("turn 11" in artifact.summary_text, "oracle: last turn missing")
    print("PASS controls (echo-first-n head truncation, full-transcript oracle)")


def main() -> int:
    tests = [
        test_ladder_candidates,
        test_ledger_mapping_shapes,
        test_task_state_arm,
        test_plain_arm,
        test_starter_shape_compacts,
        test_determinism,
        test_small_transcript_fallback,
        test_missing_binary_falls_back,
        test_controls,
    ]
    for test in tests:
        test()
    print(f"\n{len(tests)}/{len(tests)} adapter checks passed ({HARNESS_NOTE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
