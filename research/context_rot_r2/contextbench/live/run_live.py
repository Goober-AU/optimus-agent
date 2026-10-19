"""CLI for the W27 ContextBench live practice run (CUSTOM-OPTIMUS).

Subcommands (run from the repository root with the cb-venv interpreter):

    plan     build the deterministic slice + run plans (needs the pinned
             parquet; writes runs/w27-live/<stamp>/plan.json - gold
             contents stay in the gitignored runs/ tree)
    run      execute cycles for every (instance, arm) pair; fixture mode
             for the offline plumbing check, live mode for GLM 5.3
    evaluate evaluate all pairs with the W10 mirror, write results.json
             and raw-calls.jsonl into contextbench/live/
    selftest offline deterministic checks (see selftest.py)

Resumable: each finished pair is written under runs/<root>/pairs/<arm>/
and skipped on re-run. The wire-call hard cap is enforced across the
whole run.
"""

import argparse
import json
import os
import sys
import threading
import time
from typing import Dict, List

from . import (
    CYCLES,
    LIVE_LABEL,
    PLANNED_WIRE_CALLS,
    SLICE_SIZE,
    WIRE_CALL_HARD_CAP,
)
from . import driver as live_driver
from . import harness_client
from . import scenario as live_scenario

REFS_ROOT_DEFAULT = "C:/Users/openclawuser/optimus-rot2-w5-research/refs/ContextBench"
RUNS_DIR_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs", "w27-live"
)


def cmd_plan(args) -> int:
    rows, dev_ids = live_scenario.load_rows_and_dev_ids(args.refs_root)
    slice_rows = live_scenario.select_slice(rows, dev_ids)
    plans = [live_scenario.build_plan(row, rows, dev_ids) for row in slice_rows]
    os.makedirs(args.run_root, exist_ok=True)
    payload = {
        "label": LIVE_LABEL,
        "slice": {
            "size": len(plans),
            "seedParts": list(live_scenario.SLICE_SEED_PARTS),
            "instanceIds": [p["instance_id"] for p in plans],
            "languages": sorted({p["language"] for p in plans}),
        },
        "cycles": CYCLES,
        "plannedWireCalls": PLANNED_WIRE_CALLS,
        "wireCallHardCap": WIRE_CALL_HARD_CAP,
        "plans": plans,
    }
    path = os.path.join(args.run_root, "plan.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print(f"plan: {len(plans)} instances -> {path}")
    for plan in plans:
        print(
            f"  {plan['instance_id']} lang={plan['language']} "
            f"goldItems={len(plan['gold_items'])} "
            f"distractors={len(plan['distractors'])} "
            f"turns={len(plan['turns'])} "
            f"estTokens={plan['estimated_tokens']:.0f} "
            f"reserve={plan['settings']['reserveTokens']} "
            f"keep={plan['settings']['keepRecentTokens']}"
        )
    return 0


def _load_plan(run_root: str) -> List[dict]:
    with open(os.path.join(run_root, "plan.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)["plans"]


def cmd_run(args) -> int:
    plans = _load_plan(args.run_root)
    if args.instances:
        wanted = set(args.instances.split(","))
        plans = [p for p in plans if p["instance_id"] in wanted]
    binary = os.path.abspath(args.binary)
    if not os.path.isfile(binary):
        print(f"harness binary not found: {binary}", file=sys.stderr)
        return 2
    budget = live_driver.CallBudget(hard_cap=args.hard_cap)
    if args.summarizer == "live":
        bearer, pomerium = harness_client.resolve_credentials()
    else:
        bearer = pomerium = "fixture"

    pairs = [(plan, arm) for plan in plans for arm in ("A", "B")]
    if args.arms:
        wanted = set(args.arms.split(","))
        pairs = [(p, a) for p, a in pairs if a in wanted]
    # resume: skip pairs whose result file already exists and is complete
    def pair_done(plan, arm):
        path = os.path.join(args.run_root, "pairs", arm, plan["instance_id"] + ".json")
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as handle:
                result = json.load(handle)
            return result.get("completedCycles", 0) >= CYCLES or result.get("stops")
        except ValueError:
            return False

    todo = [(p, a) for p, a in pairs if not pair_done(p, a)]
    print(
        f"run: {len(todo)} pairs to do ({len(pairs) - len(todo)} already done), "
        f"summarizer={args.summarizer}, workers={args.workers}, "
        f"hard cap {args.hard_cap}"
    )
    lock = threading.Lock()
    cursor = {"index": 0}
    errors = []

    def worker():
        while True:
            with lock:
                if cursor["index"] >= len(todo) or budget.stopped:
                    return
                plan, arm = todo[cursor["index"]]
                cursor["index"] += 1
                index = cursor["index"]
            started = time.time()
            try:
                result = live_driver.run_pair(
                    plan,
                    arm,
                    binary,
                    args.run_root,
                    args.summarizer,
                    budget,
                    bearer,
                    pomerium,
                    timeout_s=args.cycle_timeout_s,
                )
                stats = live_driver.call_stats(result["calls"])
                print(
                    f"[{index}/{len(todo)}] {arm} {plan['instance_id']}: "
                    f"cycles={result['completedCycles']} calls={stats['count']} "
                    f"meanLatency={stats.get('latencyMsMean')}ms "
                    f"({time.time() - started:.0f}s)"
                    + (f" STOPS={result['stops']}" if result["stops"] else "")
                )
            except Exception as error:  # noqa: BLE001 - recorded, never silent
                errors.append(f"{arm} {plan['instance_id']}: {error!r}")
                print(f"[{index}/{len(todo)}] {arm} {plan['instance_id']}: ERROR {error!r}")

    threads = [threading.Thread(target=worker) for _ in range(args.workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"run finished; wire calls used: {budget.used}/{args.hard_cap}")
    if budget.stopped:
        print("STOPPED: wire-call hard cap reached; some pairs are incomplete")
    if errors:
        print(f"{len(errors)} pair errors:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
    return 1 if errors else 0


def cmd_evaluate(args) -> int:
    from .. import gold as cb_gold

    plans = _load_plan(args.run_root)
    plan_by_id = {p["instance_id"]: p for p in plans}
    records = {"A": [], "B": []}
    calls = []
    pair_meta = {}
    missing = []
    for plan in plans:
        for arm in ("A", "B"):
            path = os.path.join(
                args.run_root, "pairs", arm, plan["instance_id"] + ".json"
            )
            if not os.path.isfile(path):
                missing.append(f"{arm}:{plan['instance_id']}")
                continue
            with open(path, "r", encoding="utf-8") as handle:
                pair = json.load(handle)
            gold = cb_gold.gold_from_row(
                {
                    "instance_id": plan["instance_id"],
                    "original_inst_id": plan["instance_id"],
                    "repo": plan["repo"],
                    "repo_url": "",
                    "base_commit": plan["base_commit"],
                    "language": plan["language"],
                    "source": plan["source"],
                    "gold_context": json.dumps(plan["gold_items"]),
                    "patch": plan.get("patch") or "",
                    "test_patch": "",
                }
            )
            record = live_driver.evaluate_pair(pair, gold, tool_calls=len(plan["reads"]))
            records[arm].append(record)
            calls.extend(pair["calls"])
            pair_meta[f"{arm}:{plan['instance_id']}"] = {
                "cycles": pair["completedCycles"],
                "firedCycles": sum(1 for c in pair["cycles"] if c["fired"]),
                "keepUsed": [
                    c.get("keepRecentTokens") for c in pair["cycles"]
                ],
                "summaryChars": [c.get("summaryChars") for c in pair["cycles"]],
                "stops": pair["stops"],
                "calls": len(pair["calls"]),
            }
    if missing:
        print(f"WARNING: {len(missing)} pairs missing: {missing[:5]}", file=sys.stderr)

    from .. import harness as cb_harness

    aggregate = {
        arm: cb_harness.summarize_arm(records[arm]) for arm in ("A", "B")
    }
    comparisons = {}
    for metric_path in (
        ("final", "file", "coverage"),
        ("final", "file", "precision"),
        ("final", "line", "coverage"),
        ("final", "line", "precision"),
        ("trajectory", "auc_coverage", "file"),
        ("trajectory", "auc_coverage", "line"),
        ("trajectory", "redundancy", "file"),
        ("trajectory", "redundancy", "line"),
        ("editloc", "recall"),
        ("editloc", "precision"),
    ):
        comparisons[".".join(metric_path)] = cb_harness.compare_arms(
            records["A"], records["B"], metric_path
        )

    out_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    live_dir = os.path.join(out_dir, "live")
    os.makedirs(live_dir, exist_ok=True)
    results = {
        "label": LIVE_LABEL,
        "runRoot": os.path.relpath(args.run_root, out_dir),
        "missingPairs": missing,
        "aggregate": aggregate,
        "comparisons": comparisons,
        "records": records,
        "pairMeta": pair_meta,
        "callStats": {
            "overall": live_driver.call_stats(calls),
            "A": live_driver.call_stats([c for c in calls if c.get("arm") == "A"]),
            "B": live_driver.call_stats([c for c in calls if c.get("arm") == "B"]),
        },
    }
    with open(os.path.join(live_dir, "results.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(live_dir, "raw-calls.jsonl"), "w", encoding="utf-8", newline="\n") as handle:
        for call in calls:
            handle.write(json.dumps(call, sort_keys=True) + "\n")
    print(
        f"evaluate: A n={aggregate['A'].get('num_valid')} "
        f"B n={aggregate['B'].get('num_valid')}; "
        f"calls={results['callStats']['overall']['count']} -> {live_dir}"
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--refs-root", default=REFS_ROOT_DEFAULT)
    plan.add_argument("--run-root", default=RUNS_DIR_DEFAULT)
    plan.set_defaults(func=cmd_plan)

    run = sub.add_parser("run")
    run.add_argument("--binary", required=True)
    run.add_argument("--run-root", default=RUNS_DIR_DEFAULT)
    run.add_argument("--summarizer", choices=["fixture", "live"], default="live")
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--hard-cap", type=int, default=WIRE_CALL_HARD_CAP)
    run.add_argument("--cycle-timeout-s", type=float, default=2400.0)
    run.add_argument("--instances", default="")
    run.add_argument("--arms", default="")
    run.set_defaults(func=cmd_run)

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--run-root", default=RUNS_DIR_DEFAULT)
    evaluate.set_defaults(func=cmd_evaluate)

    selftest = sub.add_parser("selftest")
    selftest.set_defaults(func=lambda args: _selftest())
    selftest.add_argument("--binary", default="")

    args = parser.parse_args(argv)
    return args.func(args)


def _selftest() -> int:
    from . import selftest as selftest_module

    return selftest_module.main()


if __name__ == "__main__":
    sys.exit(main())
