"""Minimal custom Compactor used to validate CompactBench's external-method
resolution path offline (the same path an Optimus adapter would take).

Not a compaction method: it echoes the last turn and fills the structured
state with one deterministic item so the artifact is non-empty. Purpose is
mechanical: prove `--method <path>:<ClassName>` loads an out-of-tree class and
returns a contract-valid CompactionArtifact under the mock provider, with no
network.

Run from the compactbench repo root with the scratch venv on PATH:

  compactbench run \
    --method research/context_rot_r2/repro/compactbench_smoke_compactor.py:SmokeEchoCompactor \
    --suite starter --provider mock --model mock-deterministic \
    --case-count 1 --drift-cycles 0 -o /tmp/cb-smoke.jsonl

(the relative path works when the command is issued from the Optimus worktree
root with --benchmarks-dir pointing at the compactbench checkout; see
COMPACTBENCH-ADAPTER-DESIGN.md for the full command)
"""
from __future__ import annotations

from typing import Any, ClassVar

from compactbench.compactors.base import Compactor
from compactbench.contracts import CompactionArtifact, StructuredState, Transcript


class SmokeEchoCompactor(Compactor):
    """Echoes the final turn into the summary; one deterministic state entry."""

    name: ClassVar[str] = "smoke-echo"
    version: ClassVar[str] = "0.1.0"

    async def compact(
        self,
        transcript: Transcript,
        config: dict[str, Any] | None = None,
        previous_artifact: CompactionArtifact | None = None,
    ) -> CompactionArtifact:
        turns = transcript.turns
        last = turns[-1] if turns else None
        summary = f"user said: {last.content}" if last else "(empty transcript)"
        entity_map = {"last_speaker": last.role.value} if last is not None else {}
        state = StructuredState(entity_map=entity_map)
        return CompactionArtifact(
            summaryText=summary,
            structured_state=state,
            selectedSourceTurnIds=[t.id for t in turns],
            warnings=[],
            methodMetadata={"method": self.name, "calls": 0},
        )
