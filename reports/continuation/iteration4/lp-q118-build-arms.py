#!/usr/bin/env python3
"""Build the lp_118 Type-A arm manifests (round 4, glm-lpq118).

Reads the official Cognitive answerInstruction from the frozen v3 manifest at
runtime; the verbatim instruction text is never copied into this repo.

Usage:
    python lp-q118-build-arms.py <scratch-root> <frozen-v3-manifest.json>

Creates <scratch-root>/manifests/lpq118_v4A_base.json (original instruction)
and <scratch-root>/manifests/lpq118_v4A_nonarr.json (instruction + no-narration
addendum), both skipIngest=true against a private copy of the frozen envs/lp_118
store (cwd remapped in agent/memory/projects.json), plus question-scoped
qs-only dirs for official-lp-v1 scoring.
"""
import json
import sys
from pathlib import Path

ADDENDUM = (
    "Answer directly with the final answer only — never narrate your reasoning "
    "or describe what you will do."
)


def correct_key(run_dir: str, env_id: str) -> str:
    core = run_dir.replace("/", "\\") + f"\\envs\\{env_id}\\workspace"
    return "path:" + "\\\\?\" + core


def fix_projects(run_dir: str, env_id: str) -> None:
    mem = Path(run_dir) / "envs" / env_id / "agent" / "memory"
    projs = mem / "projects"
    frozen = None
    for child in projs.iterdir():
        if child.name.endswith(".lock.guard"):
            continue
        if (child / "harness_state.json").is_file():
            frozen = child.name
    if frozen is None:
        raise SystemExit(f"no frozen project in {projs}")
    (mem / "projects.json").write_text(
        json.dumps({correct_key(str(run_dir), env_id): frozen}, indent=2), encoding="utf-8"
    )


def main() -> None:
    scratch = Path(sys.argv[1])
    v3_path = Path(sys.argv[2])
    v3 = json.loads(v3_path.read_text(encoding="utf-8"))
    env = next(e for e in v3["envs"] if e["env_id"] == "lp_118")
    q = next(x for x in v3["questions"] if x["qid"] == "lp_118")
    settings = dict(v3["settings"])
    settings["skipIngest"] = True

    def arm(run_id: str, addendum: bool) -> dict:
        s = dict(settings)
        if addendum:
            s["answerInstruction"] = s["answerInstruction"] + " " + ADDENDUM
        return {
            "run_id": run_id,
            "output_dir": str(scratch / "runs" / run_id),
            "benchmark": v3["benchmark"],
            "variant": v3["variant"],
            "seed": v3["seed"],
            "model": v3["model"],
            "captureProtocol": v3["captureProtocol"],
            "envs": [{
                "env_id": env["env_id"], "family": env["family"], "notes": env["notes"],
                "question_protocol": env["question_protocol"], "sessions": [],
                "ground_truth_refs": env["ground_truth_refs"],
            }],
            "questions": [q],
            "settings": s,
        }

    out = scratch / "manifests"
    out.mkdir(parents=True, exist_ok=True)
    for run_id, addendum in [("lpq118_v4A_base", False), ("lpq118_v4A_nonarr", True)]:
        (out / f"{run_id}.json").write_text(
            json.dumps(arm(run_id, addendum), indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print("wrote", out / f"{run_id}.json")


if __name__ == "__main__":
    main()
