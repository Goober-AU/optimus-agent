"""W9 variant driver: run scenario variants through the real adapter.

Every measurement in the W9 experiments comes from the production pipeline
(`prepare_compaction` -> `compact` -> `SessionManager` persistence) via the
committed adapter `crates/pi-coding-agent/tests/rot2_multicycle.rs`, invoked
through its documented env contract (`ROT2_SCENARIO` / `ROT2_RESULT`), exactly
like `research/context_rot_r2/orchestrate.py` does. Nothing here re-implements
compaction policy.

Differences from orchestrate.py, required by the W9 brief:
- the repo is this worktree (not a sibling checkout),
- cargo uses the isolated W9 target dir `C:/Users/openclawuser/rot2-w9-target`,
- the environment is scrubbed of `PRIME_AGENT_*` overrides so runs are
  reproducible (one deliberate probe sets `PRIME_AGENT_SUMMARY_UPDATE_POLICY`
  to demonstrate the adapter path ignores it).
"""

import json
import os
import subprocess
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RUNS_DIR = os.path.join(os.path.dirname(__file__), "runs")

CARGO_TARGET_DIR = r"C:\Users\openclawuser\rot2-w9-target"
RUSTUP_HOME = r"C:\Users\openclawuser\optimus-rust-toolchain\rustup"
CARGO_HOME = r"C:\Users\openclawuser\optimus-rust-toolchain\cargo"
TOOLCHAIN_BIN = r"C:\Users\openclawuser\optimus-rust-toolchain\cargo\bin"

ADAPTER_CMD = [
    os.path.join(TOOLCHAIN_BIN, "cargo.exe"),
    "test",
    "-p",
    "pi-coding-agent",
    "--test",
    "rot2_multicycle",
    "rot2_production_multicycle",
    "--",
    "--ignored",
    "--nocapture",
]


def adapter_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PRIME_AGENT_"):
            del env[key]
    env["CARGO_TARGET_DIR"] = CARGO_TARGET_DIR
    env["RUSTUP_HOME"] = RUSTUP_HOME
    env["CARGO_HOME"] = CARGO_HOME
    env["PATH"] = TOOLCHAIN_BIN + os.pathsep + env.get("PATH", "")
    if extra:
        env.update(extra)
    return env


def write_scenario(scenario: Dict[str, Any], tag: str) -> str:
    os.makedirs(RUNS_DIR, exist_ok=True)
    path = os.path.join(RUNS_DIR, f"{tag}.scenario.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(scenario, handle, indent=1)
    return path


def run_adapter(
    scenario: Dict[str, Any],
    tag: str,
    env_extra: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Write the scenario, invoke the adapter once, return the parsed result.

    `tag` names the run pair `<tag>.scenario.json` / `<tag>.result.json` under
    `runs/`. The scenario dict is used verbatim; pass `arms` in the scenario to
    select which adapter arms run.
    """
    scenario_path = write_scenario(scenario, tag)
    result_path = os.path.join(RUNS_DIR, f"{tag}.result.json")
    if os.path.exists(result_path):
        os.remove(result_path)
    env = adapter_env(env_extra)
    env["ROT2_SCENARIO"] = scenario_path
    env["ROT2_RESULT"] = result_path
    completed = subprocess.run(
        ADAPTER_CMD,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not os.path.exists(result_path):
        raise RuntimeError(
            "adapter run %s failed (rc=%d)\nstdout tail:\n%s\nstderr tail:\n%s"
            % (
                tag,
                completed.returncode,
                completed.stdout[-3000:],
                completed.stderr[-3000:],
            )
        )
    with open(result_path, encoding="utf-8") as handle:
        return json.load(handle)


def load_result(tag: str) -> Dict[str, Any]:
    with open(os.path.join(RUNS_DIR, f"{tag}.result.json"), encoding="utf-8") as handle:
        return json.load(handle)


def load_scenario(tag: str) -> Dict[str, Any]:
    with open(os.path.join(RUNS_DIR, f"{tag}.scenario.json"), encoding="utf-8") as handle:
        return json.load(handle)


def compare_cycle_measurements(
    left: List[Dict[str, Any]], right: List[Dict[str, Any]]
) -> List[str]:
    """Field-by-field comparison of two CycleRecord lists, excluding the
    random session-entry ids (`firstKeptEntryId`) and the ledger item dump."""
    diffs = []
    if len(left) != len(right):
        return [f"cycle count {len(left)} != {len(right)}"]
    fields = [
        "cycle",
        "ran",
        "summarizedEntries",
        "retainedEntries",
        "summaryChars",
        "summarizerRequestChars",
        "compactedContextTokens",
        "ledgerPresent",
        "ledgerItems",
    ]
    for a, b in zip(left, right):
        for field in fields:
            if a.get(field) != b.get(field):
                diffs.append(f"cycle {a.get('cycle')}: {field} {a.get(field)} != {b.get(field)}")
        if a.get("anchorsPresent") != b.get("anchorsPresent"):
            diffs.append(f"cycle {a.get('cycle')}: anchorsPresent differ")
    return diffs
