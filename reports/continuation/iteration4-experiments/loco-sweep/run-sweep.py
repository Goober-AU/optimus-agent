#!/usr/bin/env python3
"""loco-sweep master: conversation batches (FAILED re-answer, round-4 full stack,
frozen typeA stores) + interleaved batch gates (previously-passing spot-checks).

Order: conv-41, 43 (stem-scan conversions first), then 49, 50, then 30, 42, 44, 47, 48.
Gate after every ~2 conversations (3 spot-checks, gate ledger, same stack).
Writes results-c<conv>.json / results-gate<g>.json incrementally.
"""
import json, os, subprocess, sys, time

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + "/imp4b-locosweep"
EXP = WT + "/reports/continuation/iteration4-experiments/loco-sweep"
GATE_LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER-locosweep-gate.json"

ENV = {
    "MEMORY_BENCH_MODEL_PROVIDER": "dgx-glm53",
    "MEMORY_BENCH_MODEL_ID": "glm-5.3",
    "MEMORY_BENCH_ROOT": ROOT,
    "LSW_LEDGER": None,  # set per phase
}

def run_phase(jobs_file, out_file, gate):
    env = dict(os.environ); env.update({k: v for k, v in ENV.items() if v is not None})
    if gate:
        env["LSW_LEDGER"] = GATE_LEDGER
    else:
        env.pop("LSW_LEDGER", None)
    cmd = [sys.executable, EXP + "/run-battery.py", jobs_file, out_file, "3"]
    print("=== phase %s -> %s ===" % (os.path.basename(jobs_file), out_file), flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=WT, env=env)
    print("=== phase %s done rc=%s (%.0fs) ===" % (os.path.basename(jobs_file), r.returncode, time.time()-t0), flush=True)
    return r.returncode

plan = [(41, None), (43, 1), (49, None), (50, 2), (30, None), (42, 3), (44, None), (47, 4), (48, 5)]
for conv, gate in plan:
    rc = run_phase(EXP + "/jobs-c%d.json" % conv, EXP + "/results-c%d.json" % conv, gate=False)
    if rc != 0:
        print("!! batch c%d rc=%s (continuing)" % (conv, rc), flush=True)
    if gate:
        rc = run_phase(EXP + "/jobs-gate%d.json" % gate, EXP + "/results-gate%d.json" % gate, gate=True)
        if rc != 0:
            print("!! gate %d rc=%s (continuing)" % (gate, rc), flush=True)

print("=== SWEEP COMPLETE ===", flush=True)
