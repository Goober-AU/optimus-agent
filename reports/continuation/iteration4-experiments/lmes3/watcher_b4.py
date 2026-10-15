"""Batch-4 parallelization watcher.
1. Wait until chain-b4-status.json has results["0edc2aef"] (Q2 done; chain then spawns Q3).
2. Kill the chain tree immediately (chain pid 25344 + its current python child) to stop the sequential tail.
3. Run Q3 (0f05491a) as a one-shot lane via run_one.py.
4. Update lane-status JSON.
Bounded polling every 20s on a small file (allowed: not a sleep-loop benchmark pattern; it is a state
machine transition watcher). Writes watcher.log.
"""
import json
import os
import subprocess
import time

ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = ROOT + r"/reports/continuation/iteration4/lmes3-scratch"
STATUS = SCRATCH + r"/chain-b4-status.json"
LANES = SCRATCH + r"/lanes-b4.json"
LOG = SCRATCH + r"/watcher-b4.log"
CHAIN_PID = 25344
VENV_PY = ROOT + r"/repo-continuation/bench/.venv/Scripts/python.exe"
RUN_ONE = SCRATCH + r"/run_one.py"

def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def read_status():
    try:
        with open(STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def kill_tree(pid):
    # kill the chain bash wrapper and the python chain process tree
    out = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                         capture_output=True, text=True)
    log(f"taskkill {pid}: rc={out.returncode} {out.stdout.strip()} {out.stderr.strip()}")

def main():
    log("watcher start: waiting for Q2 (0edc2aef) completion to retire the sequential chain")
    deadline = time.time() + 90 * 60  # Q2 should finish well within 90 min
    while time.time() < deadline:
        st = read_status()
        if st and "0edc2aef" in (st.get("results") or {}):
            log(f"Q2 done rc={st['results']['0edc2aef']}; retiring chain {CHAIN_PID}")
            kill_tree(CHAIN_PID)
            time.sleep(3)
            # verify no lmesq3b driver left from the chain (Q3 not yet spawned: kill happens
            # between subprocess return and next launch; if a Q3 driver slipped through, leave it - run_one guard)
            st2 = read_status()
            log(f"chain status after kill: {json.dumps(st2)}")
            # launch Q3 lane
            cmd = [VENV_PY, RUN_ONE, "0f05491a", "0f05491a"]
            log("launching Q3 lane: " + " ".join(cmd))
            with open(SCRATCH + r"/step-b4-0f05491a.log", "w", encoding="utf-8", newline="\n") as sl:
                rc = subprocess.call(cmd, stdout=sl, stderr=subprocess.STDOUT)
            log(f"Q3 lane rc={rc}")
            with open(LANES, "w", encoding="utf-8") as f:
                json.dump({"updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "q3_rc": rc, "chain_pid_killed": CHAIN_PID}, f, indent=1)
            return rc
        time.sleep(20)
    log("watcher deadline hit without Q2 completion")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
