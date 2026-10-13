#!/usr/bin/env python3
"""LoCoMo full-stack gate battery: warm-cache controller runs, one question at a time."""
import json, os, subprocess, sys, time

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + "/imp4-locofull"
EXPDIR = WT + "/reports/continuation/iteration4-experiments/loco-full"
CTRL = WT + "/scripts/memory_question_loop.py"
GATE_MAN = ROOT + "/imp3-gate/reports/continuation/iteration3-inputs/locomo/manifests/locomo-gate3-improved-s0.json"
DRIVER = ROOT + "/imp4-targetR/release/examples/memory_bench.exe"
ATT_ROOT = ROOT + "/reports/continuation/iteration4/ql-attempts-locofull"
CACHE_ROOT = ROOT + "/reports/continuation/iteration4/ql-typeb-cache"
BATTERY = ['q1', 'q2', 'q18', 'q26', 'q33', 'q37', 'q39', 'q55', 'q57', 'q66', 'q77', 'q99', 'q144', 'q54', 'q126', 'q135', 'q141', 'q143']
TAG = sys.argv[1] if len(sys.argv) > 1 else "a"
ONLY = sys.argv[2].split(",") if len(sys.argv) > 2 else None

results = {}
out_path = EXPDIR + f"/battery-{TAG}.json"
for q in BATTERY:
    if ONLY and q not in ONLY:
        continue
    qid = "conv-26_" + q
    run_id = f"lf4-{q}-{TAG}"[:20]
    cmd = ["python", CTRL, qid, "--mode", "typeB",
           "--base-settings", EXPDIR + "/base-det-settings.json",
           "--settings", EXPDIR + "/fullstack-write-read.json",
           "--env-from-manifest", GATE_MAN,
           "--driver", DRIVER, "--driver-workdir", ROOT + "/imp4",
           "--attempts-root", ATT_ROOT, "--cache-root", CACHE_ROOT,
           "--run-id", run_id, "--worker", "locofull"]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=WT)
    dt = time.time() - t0
    # pull the score from the ledger tail (last attempt for this qid by this worker)
    score = None
    L = json.load(open(ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json", encoding="utf-8"))
    e = [x for x in L["entries"] if x["qid"] == qid][0]
    att = e["attempts"][-1]
    results[q] = {"run_id": run_id, "rc": r.returncode, "score": att.get("score"),
                   "answer": att.get("answer_excerpt"), "outcome": att.get("outcome"),
                   "stage": att.get("failure_stage"), "recall_n": att.get("recall_ids_count"),
                   "latency_s": round(dt, 1)}
    print(f"[{q}] score={att.get('score')} outcome={att.get('outcome')} ({dt:.0f}s)", flush=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(results, f, indent=1)
        f.write("\n")
print("battery done ->", out_path)
