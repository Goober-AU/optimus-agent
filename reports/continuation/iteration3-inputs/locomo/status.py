"""LoCoMo gate progress: run.jsonl/env.jsonl row counts + last log lines.

Usage: python status.py [gate-root]
"""
import json, os, sys

gate = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
runs = os.path.join(gate, "runs")
logs = os.path.join(gate, "logs")

def count_jsonl(p):
    n = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n

names = sorted(os.listdir(runs)) if os.path.isdir(runs) else []
for name in names:
    rd = os.path.join(runs, name)
    runp, envp = os.path.join(rd, "run.jsonl"), os.path.join(rd, "env.jsonl")
    nq = count_jsonl(runp) if os.path.isfile(runp) else 0
    ne = count_jsonl(envp) if os.path.isfile(envp) else 0
    ingested = []
    if os.path.isfile(envp):
        with open(envp, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    ingested.append(row.get("env_id"))
    log = os.path.join(logs, name + ".log")
    tail = ""
    if os.path.isfile(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            tail = " | ".join(f.read().strip().splitlines()[-2:])
    print(f"{name}: questions={nq} envs_done={ne} ingested={ingested}")
    if tail:
        print(f"    last-log: {tail[-300:]}")
print("(none)" if not names else "")
