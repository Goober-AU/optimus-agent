"""LME-S batch-3 chain runner: 5 disjoint-slice questions, sequential, stop at
first failure (rc 3/4 stop immediately; rc 2 retried twice with 5-min stagger).
One bash handle for the whole queue; status in chain-status.json, log chain.log.
Controller exit codes: 0 pass | 2 error | 3 fail | 4 flagged.
"""
import json
import os
import subprocess
import time

ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = ROOT + r"/reports/continuation/iteration4/lmes3-scratch"
VENV_PY = ROOT + r"/repo-continuation/bench/.venv/Scripts/python.exe"
RUN_ONE = SCRATCH + r"/run_one.py"
STATUS = SCRATCH + r"/chain-status.json"
LOG = SCRATCH + r"/chain.log"

SLICE = [
    ("0db4c65d", "0db4c65d"),
    ("0ddfec37", "0ddfec37"),
    ("0ddfec37_abs", "0ddfe-abs"),
    ("0e4e4c46", "0e4e4c46"),
    ("0e5e2d1a", "0e5e2d1a"),
]
FIRST_GATE = "12:31:00"  # >= 5 min after writefull driver start 12:23:26
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
    step_log = os.path.join(SCRATCH, f"step-{suffix}.log")
    t0 = time.time()
    with open(step_log, "w", encoding="utf-8", newline="\n") as sl:
        rc = subprocess.call(cmd, stdout=sl, stderr=subprocess.STDOUT)
    log(f"[{qid}] suffix={suffix} rc={rc} after {(time.time()-t0)/60:.1f} min (step-{suffix}.log)")
    return rc


def main():
    log("chain start: 5 questions sequential, stop at first failure")
    results = {}
    for i, (qid, suffix) in enumerate(SLICE):
        write_status(phase=f"running-{qid}", current_qid=qid, results=results)
        rc = run_one(qid, suffix, gate=FIRST_GATE if i == 0 else None)
        if rc == 2:
            for r in range(2, 2 + MAX_RETRY):
                log(f"[{qid}] controller error (rc=2); retry {r}/{MAX_RETRY} after 300s stagger")
                time.sleep(300)
                rc = run_one(qid, f"{suffix[:4]}-r{r}")
                if rc != 2:
                    break
        results[qid] = rc
        write_status(results=results, current_qid=qid,
                     last_rc=rc)
        if rc == 0:
            log(f"[{qid}] PASS")
            continue
        log(f"[{qid}] STOP chain (rc={rc}); first-failure rule")
        write_status(phase=f"stopped-{qid}", results=results)
        return rc
    log("chain complete: all 5 questions ran")
    write_status(phase="done", results=results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
