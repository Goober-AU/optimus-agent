#!/usr/bin/env python3
"""Repeat the winning E3 arm (pool500, no expansion, no distillation) twice."""
import subprocess, os

BENCH_PY = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/repo-continuation/bench/.venv/Scripts/python.exe"
LOOP = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4/scripts/memory_question_loop.py"
DRIVER = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4-targetR/release/examples/memory_bench.exe"
WORKDIR = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
SCRATCH = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration4/lpq232-scratch"
LEDGER = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration4/QUESTION-LEDGER.json"

for tag in ("r2", "r3"):
    run_id = f"lpq232-e3-pool500raw-{tag}"
    cmd = [BENCH_PY, LOOP, "lp_232", "--mode", "typeA",
           "--settings", os.path.join(SCRATCH, "settings-e3-pool500-raw.json"),
           "--driver", DRIVER, "--driver-workdir", WORKDIR,
           "--attempts-root", os.path.join(SCRATCH, "ql-attempts"),
           "--ledger", LEDGER,
           "--run-id", run_id, "--worker", "glm-lpq232",
           "--lp-judge-max-tokens", "4096"]
    print(f"=== E3 {tag} -> {run_id} ===", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout[-5000:], flush=True)
    if r.stderr.strip():
        print("STDERR:", r.stderr[-2000:], flush=True)
    print(f"=== E3 {tag} exit {r.returncode} ===", flush=True)
print("E3 REPEATS DONE", flush=True)
