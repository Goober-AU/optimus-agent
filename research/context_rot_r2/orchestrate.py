"""Run the production-path adapter arms from Python.

Python orchestrates; the policy under evaluation always runs in the real
Rust pipeline (cargo test --test rot2_multicycle rot2_production_multicycle
-- --ignored with ROT2_SCENARIO/ROT2_RESULT). Nothing here re-implements
compaction policy.
"""

import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

from .scenario import ScenarioSpec, build_scenario, write_scenario
from .scoring_r2 import score_result, write_score

DEFAULT_REPO = os.environ.get(
    "ROT2_REPO", r"C:/Users/openclawuser/optimus-rot2-20261020"
)


def run_scenario(
    spec: ScenarioSpec,
    repo: str = DEFAULT_REPO,
    runs_dir: Optional[str] = None,
) -> Dict[str, Any]:
    runs_dir = runs_dir or os.path.join(repo, "research", "context_rot_r2", "runs")
    os.makedirs(runs_dir, exist_ok=True)
    scenario_path = os.path.join(runs_dir, f"{spec.scenario_id}.scenario.json")
    result_path = os.path.join(runs_dir, f"{spec.scenario_id}.result.json")
    score_path = os.path.join(runs_dir, f"{spec.scenario_id}.score.json")

    scenario = build_scenario(spec)
    write_scenario(scenario, scenario_path)

    env = dict(os.environ)
    env["ROT2_SCENARIO"] = scenario_path
    env["ROT2_RESULT"] = result_path
    env.setdefault("CARGO_TARGET_DIR", r"C:/Users/openclawuser/rot2-shared-target")
    env.setdefault("RUSTUP_HOME", r"C:/Users/openclawuser/optimus-rust-toolchain/rustup")
    env.setdefault("CARGO_HOME", r"C:/Users/openclawuser/optimus-rust-toolchain/cargo")

    completed = subprocess.run(
        [
            "cargo",
            "test",
            "-p",
            "pi-coding-agent",
            "--test",
            "rot2_multicycle",
            "rot2_production_multicycle",
            "--",
            "--ignored",
            "--nocapture",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "adapter run failed (rc=%d)\nstdout:\n%s\nstderr:\n%s"
            % (completed.returncode, completed.stdout[-4000:], completed.stderr[-4000:])
        )

    with open(result_path, encoding="utf-8") as handle:
        result = json.load(handle)
    score = score_result(result, scenario)
    write_score(score, score_path)
    return score


def run_matrix(
    specs: List[ScenarioSpec],
    repo: str = DEFAULT_REPO,
    runs_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return [run_scenario(spec, repo=repo, runs_dir=runs_dir) for spec in specs]
