"""LME-S batch-3 disjoint-slice runner: ONE question per invocation.

Usage: run_one.py <qid> <run_suffix>
Runs the banked config (smoke5 base + KU/A+G4/H1/H2b ov-banked) typeB via
the wrapper run_lmes3.py (S/M disambiguation + captureProtocol forcing).
Controller exit codes: 0 pass | 2 error | 3 fail | 4 flagged.
"""
import os
import subprocess
import sys
import time

ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = ROOT + r"/reports/continuation/iteration4/lmes3-scratch"
VENV_PY = ROOT + r"/repo-continuation/bench/.venv/Scripts/python.exe"
DRIVER = ROOT + r"/imp4b-target8k/release/examples/memory_bench.exe"
WORKTREE = ROOT + r"/imp4b-lmes3"
MODELS_JSON = ROOT + r"/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
JUDGE_MODELS = ROOT + r"/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
ATTEMPTS_ROOT = r"C:/Users/openclawuser/lmesq3-att"
CACHE_ROOT = ROOT + r"/reports/continuation/iteration4/ql-typeb-cache"
LEDGER = ROOT + r"/reports/continuation/iteration4/QUESTION-LEDGER.json"
EXPECT_SHA = "4d2ba13f"

qid, suffix = sys.argv[1], sys.argv[2]
run_id = f"lmesq3b-{suffix}"
assert len(run_id) <= 20, run_id

if len(sys.argv) > 3:  # stagger gate: wait until HH:MM:SS local before spawning the driver
    target = time.mktime(time.strptime(time.strftime("%Y%m%d ") + sys.argv[3], "%Y%m%d %H:%M:%S"))
    wait_s = target - time.time()
    if wait_s > 0:
        print(f"[{qid}] stagger gate: waiting {wait_s:.0f}s until {sys.argv[3]}")
        time.sleep(wait_s)

import hashlib
h = hashlib.sha256()
with open(DRIVER, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
actual = h.hexdigest()
if not actual.startswith(EXPECT_SHA):
    print(f"FATAL: driver sha {actual[:12]} != {EXPECT_SHA}")
    sys.exit(2)

cmd = [VENV_PY, SCRATCH + r"/run_lmes3.py", qid,
       "--driver", DRIVER,
       "--driver-workdir", WORKTREE,
       "--models-json", MODELS_JSON,
       "--judge-models-json", JUDGE_MODELS,
       "--bench-venv", VENV_PY,
       "--scoring-cwd", ROOT,
       "--attempts-root", ATTEMPTS_ROOT,
       "--cache-root", CACHE_ROOT,
       "--ledger", LEDGER,
       "--mode", "typeB",
       "--base-settings", SCRATCH + r"/base-smoke5.json",
       "--settings", SCRATCH + r"/ov-banked.json",
       "--worker", "lmesq3b",
       "--run-id", run_id]

env = dict(os.environ)
env["MEMORY_BENCH_MODEL_PROVIDER"] = "dgx-glm53"
env["MEMORY_BENCH_MODEL_ID"] = "glm-5.3"

t0 = time.time()
with open(os.path.join(SCRATCH, f"step-{suffix}.log"), "w", encoding="utf-8", newline="\n") as sl:
    rc = subprocess.call(cmd, stdout=sl, stderr=subprocess.STDOUT, env=env, cwd=SCRATCH)
print(f"[{qid}] controller exit {rc} after {(time.time()-t0)/60:.1f} min (step-{suffix}.log)")
sys.exit(rc)
