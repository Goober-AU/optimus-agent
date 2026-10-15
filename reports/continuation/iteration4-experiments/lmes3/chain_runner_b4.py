"""LME-S batch-4 chain runner: 5 disjoint-slice questions (idx 30-34), sequential,
stop at first failure (rc 3/4 stop immediately; rc 2 retried twice with 5-min stagger).
Config: banked ov-banked.json from imp4 HEAD 4cd56bd67 (W1+W2 write, H2b+H3d+H4 read).
"""
import json
import os
import subprocess
import time

ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = ROOT + r"/reports/continuation/iteration4/lmes3-scratch"
VENV_PY = ROOT + r"/repo-continuation/bench/.venv/Scripts/python.exe"
RUN_ONE = SCRATCH + r"/run_one.py"
STATUS = SCRATCH + r"/chain-b4-status.json"
LOG = SCRATCH + r"/chain-b4.log"

SLICE = [
    ("0ea62687", "0ea62687"),
    ("0edc2aef", "0edc2aef"),
    ("0f05491a", "0f05491a"),
    ("10d9b85a", "10d9b85a"),
    ("10e09553", "10e09553"),
]
FIRST_GATE = "23:07:00"  # BB driver spawned 22:19:21; keep spawn separation
MAX_RETRY = 2


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_status(**kw):
    st = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if os.path.isfile(STATUS):
        try:
            with open(STATUS, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            pass
    st.update(kw)
    st["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATUS)


def run_one(qid, suffix, gate=None):
    cmd = [VENV_PY, RUN_ONE, qid, suffix] + ([gate] if gate else [])
    step_log = os.path.join(SCRATCH, f"step-b4-{suffix}.log")
    t0 = time.time()
    with open(step_log, "w", encoding="utf-8", newline="\n") as sl:
        rc = subprocess.call(cmd, stdout=sl, stderr=subprocess.STDOUT)
    log(f"[{qid}] suffix={suffix} rc={rc} after {(time.time()-t0)/60:.1f} min (step-b4-{suffix}.log)")
    return rc


def main():
    log("chain b4 start: 5 questions (idx 30-34), stop at first failure, W1+W2/H2b+H3d+H4 config")
    results = {}
    for i, (qid, suffix) in enumerate(SLICE):
        write_status(phase=f"running-{qid}", current_qid=qid, results=results)
        rc = run_one(qid, suffix, gate=FIRST_GATE if i == 0 else None)
        if rc == 2:
            for r in range(2, 2 + MAX_RETRY):
                log(f"[{qid}] controller error (rc=2); retry {r}/{MAX_RETRY} after 300s stagger")
                time.sleep(300)
                rc = run_one(qid, f"{suffix[:4]}b{r}")
                if rc != 2:
                    break
        results[qid] = rc
        write_status(results=results, current_qid=qid, last_rc=rc)
        if rc == 0:
            log(f"[{qid}] PASS")
            continue
        log(f"[{qid}] STOP chain (rc={rc}); first-failure rule")
        write_status(phase=f"stopped-{qid}", results=results)
        return rc
    log("chain b4 complete: all 5 questions ran")
    write_status(phase="done", results=results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
