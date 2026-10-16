"""OCC-2 run driver for the SILO-BENCH adapter.

Same discipline as adapter/pilot_run.py: each case runs in its own
subprocess with a hard wall-clock timeout (the official LLM client retries
forever), timed-out cases are recorded honestly as status=timeout and count
as failures, existing summaries are skipped (delete to rerun). The only
difference from the pilot driver: cases are executed through occ2_case.py,
which applies the OCC-2 message-encoding hook between init and the first
round. Scoring reuses the adapter scorer unchanged.

    python occ2_run.py --config occ2_config.json --run occ2-...-occ1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ADAPTER = HERE.parent / "adapter"
PY = sys.executable


def drive_run(cfg: dict, run_spec: dict, only_tasks=None, config_path=None) -> dict:
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
            print(f"[occ2] {tf}: summary exists, skipping (delete to rerun)")
            with open(summary_path, "r", encoding="utf-8") as f:
                summaries.append(json.load(f))
            continue
        cmd = [
            PY, str(HERE / "occ2_case.py"),
            "--config", str(config_path),
            "--run", run_name,
            "--task-file", tf,
            "--workspace", str(workspace),
            "--summary-out", str(summary_path),
        ]
        t0 = time.monotonic()
        print(f"[occ2] {tf}: starting (timeout {run_spec['case_timeout_s']}s)", flush=True)
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
                    f"[occ2] {tf}: done status={summary.get('status')} "
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
                    "comm_mode": run_spec["comm_mode"],
                    "status": "runner_error",
                    "timeout": False,
                    "elapsed_s": round(dt, 3),
                    "error": (proc.stderr or proc.stdout or "")[-2000:],
                }
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2)
                print(f"[occ2] {tf}: RUNNER ERROR rc={proc.returncode}", flush=True)
                print((proc.stderr or "")[-1500:], flush=True)
        except subprocess.TimeoutExpired:
            dt = time.monotonic() - t0
            summary = {
                "task_file": tf,
                "protocol": run_spec["protocol"],
                "model": run_spec["model"],
                "comm_mode": run_spec["comm_mode"],
                "status": "timeout",
                "timeout": True,
                "elapsed_s": round(dt, 3),
                "error": f"exceeded per-case wall-clock budget of {run_spec['case_timeout_s']}s",
            }
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            print(f"[occ2] {tf}: TIMEOUT after {dt:.1f}s", flush=True)
        summaries.append(summary)

    return {"run_name": run_name, "summaries": summaries}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(HERE / "occ2_config.json"))
    ap.add_argument("--run", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    run_spec = next((r for r in cfg["runs"] if r["name"] == args.run), None)
    if run_spec is None:
        print(f"run spec not found: {args.run}", file=sys.stderr)
        return 2

    result = drive_run(cfg, run_spec, args.only, config_path=str(Path(args.config).resolve()))

    sys.path.insert(0, str(ADAPTER))
    from scorer import score_run, write_scorecards

    score = score_run(result["summaries"], run_spec)
    out_dir = (HERE / cfg.get("output_root", "../runs")).resolve() / args.run
    json_path, md_path = write_scorecards(score, out_dir)
    print(f"[occ2] scorecard: {json_path}")
    print(f"[occ2] scorecard: {md_path}")
    agg = score["adapter_aggregates"]
    print(
        f"[occ2] SUMMARY {args.run}: tasks={score['task_count']} "
        f"accuracy={agg['case_accuracy']} meanS={agg['mean_S']} "
        f"prompt_tokens={agg['total_prompt_tokens']} "
        f"llm_calls={agg['total_llm_calls']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
