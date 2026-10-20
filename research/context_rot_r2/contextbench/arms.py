"""Retrieval-policy arms for the matched A/B harness.

Arm definitions follow the design doc section 5 (matched A/B: identical
model, endpoint, temperature, seeds, budgets, tools; only the retrieval /
context policy differs). Arms C and D are scaffolding: their policy
implementations do not exist in this tree yet, so their records are
reported as PLACEHOLDER and never carry numbers.

Arm identities are stable strings so run manifests stay comparable across
checkouts.
"""

from dataclasses import dataclass
from typing import Dict, List

RUNNABLE = "RUNNABLE"
PLACEHOLDER = "PLACEHOLDER"


@dataclass(frozen=True)
class ArmConfig:
    arm: str
    name: str
    status: str
    description: str
    blocked_reason: str = ""

    @property
    def runnable(self) -> bool:
        return self.status == RUNNABLE


ARM_A = ArmConfig(
    arm="A",
    name="baseline",
    status=RUNNABLE,
    description=(
        "Unmodified production retrieval/compaction policy (current "
        "defaults): no Jev relevance filtering on the evaluated surfaces."
    ),
)

ARM_B = ArmConfig(
    arm="B",
    name="jev-relevance",
    status=RUNNABLE,
    description=(
        "Current Jev relevance filtering as shipped in this tree "
        "(crates/pi-jev evidence/search relevance path)."
    ),
)

ARM_C = ArmConfig(
    arm="C",
    name="type-aware-task-context",
    status=PLACEHOLDER,
    description=(
        "Type-aware task context: retention driven by the typed task "
        "state. Placeholder until typed-state lands."
    ),
    blocked_reason="typed-state retention policy not landed",
)

ARM_D = ArmConfig(
    arm="D",
    name="source-grounded-reconstruction",
    status=PLACEHOLDER,
    description=(
        "Source-grounded reconstruction: summaries rebuilt from pinned "
        "source spans instead of model prose. Placeholder."
    ),
    blocked_reason="source-grounded reconstruction policy not implemented",
)

ARMS: Dict[str, ArmConfig] = {
    a.arm: a for a in (ARM_A, ARM_B, ARM_C, ARM_D)
}


def get_arm(arm: str) -> ArmConfig:
    if arm not in ARMS:
        raise KeyError(f"unknown arm {arm!r}; known arms: {sorted(ARMS)}")
    return ARMS[arm]


def runnable_arms() -> List[ArmConfig]:
    return [a for a in ARMS.values() if a.runnable]


def placeholder_arms() -> List[ArmConfig]:
    return [a for a in ARMS.values() if not a.runnable]
