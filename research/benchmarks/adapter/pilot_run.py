"""SILO-BENCH adapter pilot driver.

Runs each case of a config run spec in its own subprocess with a hard
wall-clock timeout, so a hung LLM call (the official client retries
indefinitely) cannot block the whole run. Timed-out cases are recorded
honestly as status=timeout and score 0.

    python pilot_run.py --config config.json --run pilot-levelI-n2-msg-glm53
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PY = sys.executable


def drive_run(cfg: dict, run_spec: dict, only_tasks: list[str] | None = None) -> dict:
    run_name = run_spec["name"]
    out_root = (HERE / cfg.get("output_root", "../runs")).resolve()
    run_dir = out_root / run_name
    cases_dir = run_dir / "cases"
    workspace = run_dir / "workspace"
    cases_dir.mkdir(parents=True, exist_ok=True)

    task_files = list(run_spec["task_files"])
    if only_tasks:
        task_files = [t for t in task_files if t in only_tasks]

    summaries = []
    for tf in task_files:
        stem = Path(tf).stem
        summary_path = cases_dir / f"case-{stem}.json"
        if summary_path.exists():
            print(f"[pilot] {tf}: summary exists, skipping (delete to rerun)")
            with open(summary_path, "r", encoding="utf-8") as f:
                summaries.append(json.load(f))
            continue
        cmd = [
            PY, str(HERE / "runner.py"),
            "--config", str(HERE / "config.json"),
            "--run", run_name,
            "--task-file", tf,
            "--workspace", str(workspace),
            "--summary-out", str(summary_path),
        ]
        t0 = time.monotonic()
        print(f"[pilot] {tf}: starting (timeout {run_spec['case_timeout_s']}s)", flush=True)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=run_spec["case_timeout_s"],
                cwd=str(HERE),
            )
            dt = time.monotonic() - t0
            if proc.returncode == 0 and summary_path.exists():
                with open(summary_path, "r", encoding="utf-8") as f:
                    summary = json.load(f)
                summary["timeout"] = False
                print(
                    f"[pilot] {tf}: done status={summary.get('status')} "
                    f"rounds={(summary.get('official') or {}).get('rounds_executed')} "
                    f"S={((summary.get('official') or {}).get('metrics') or {}).get('S_success_rate')} "
                    f"in {dt:.1f}s",
                    flush=True,
                )
            else:
                summary = {
                    "task_file": tf,
                    "protocol": run_spec["protocol"],
                    "model": run_spec["model"],
                    "comm_mode": run_spec.get("comm_mode"),
                    "status": "runner_error",
                    "timeout": False,
                    "elapsed_s": round(dt, 3),
                    "error": (proc.stderr or proc.stdout or "")[-2000:],
                }
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2)
                print(f"[pilot] {tf}: RUNNER ERROR rc={proc.returncode}", flush=True)
                print((proc.stderr or "")[-1500:], flush=True)
        except subprocess.TimeoutExpired:
            dt = time.monotonic() - t0
            summary = {
                "task_file": tf,
                "protocol": run_spec["protocol"],
                "model": run_spec["model"],
                "comm_mode": run_spec.get("comm_mode"),
                "status": "timeout",
                "timeout": True,
                "elapsed_s": round(dt, 3),
                "error": f"exceeded per-case wall-clock budget of {run_spec['case_timeout_s']}s",
            }
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            print(f"[pilot] {tf}: TIMEOUT after {dt:.1f}s", flush=True)
        summaries.append(summary)

    return {
        "run_name": run_name,
        "summaries": summaries,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--run", required=True)
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these task files")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    run_spec = next((r for r in cfg["runs"] if r["name"] == args.run), None)
    if run_spec is None:
        print(f"run spec not found: {args.run}", file=sys.stderr)
        return 2

    result = drive_run(cfg, run_spec, args.only)

    sys.path.insert(0, str(HERE))
    from scorer import score_run, write_scorecards

    score = score_run(result["summaries"], run_spec)
    out_dir = (HERE / cfg.get("output_root", "../runs")).resolve() / args.run
    json_path, md_path = write_scorecards(score, out_dir)
    print(f"[pilot] scorecard: {json_path}")
    print(f"[pilot] scorecard: {md_path}")
    agg = score["adapter_aggregates"]
    print(
        f"[pilot] SUMMARY {args.run}: tasks={score['task_count']} "
        f"accuracy={agg['case_accuracy']} meanS={agg['mean_S']} "
        f"prompt_tokens={agg['total_prompt_tokens']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
