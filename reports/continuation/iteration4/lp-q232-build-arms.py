#!/usr/bin/env python3
"""Run the three lp_232 read-time arms sequentially (glm-lpq232 worker)."""
import subprocess, sys, os

BENCH_PY = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/repo-continuation/bench/.venv/Scripts/python.exe"
LOOP = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4/scripts/memory_question_loop.py"
DRIVER = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4-targetR/release/examples/memory_bench.exe"
WORKDIR = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
SCRATCH = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration4/lpq232-scratch"
LEDGER = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration4/QUESTION-LEDGER.json"

ARMS = [('e1-pool500-lp', 'lpq232-e1-pool500lp'), ('e2-pool500-noexp', 'lpq232-e2-pool500noexp'), ('e3-pool500-raw', 'lpq232-e3-pool500raw')]

for tag, run_id in ARMS:
    settings = os.path.join(SCRATCH, f"settings-{tag}.json")
    cmd = [BENCH_PY, LOOP, "lp_232", "--mode", "typeA",
           "--settings", settings,
           "--driver", DRIVER, "--driver-workdir", WORKDIR,
           "--attempts-root", os.path.join(SCRATCH, "ql-attempts"),
           "--ledger", LEDGER,
           "--run-id", run_id, "--worker", "glm-lpq232",
           "--lp-judge-max-tokens", "4096"]
    print(f"=== ARM {tag} -> {run_id} ===", flush=True)
    print(" ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout[-6000:], flush=True)
    if r.stderr.strip():
        print("STDERR:", r.stderr[-3000:], flush=True)
    print(f"=== ARM {tag} exit {r.returncode} ===", flush=True)
print("ALL ARMS DONE", flush=True)
