"""LP campaign parallel finisher.

Waits until all 5 dev5 stores are ingested + verified (lp_232 via the repair
driver's state, the rest via state/ingest-<qid>.json), auto-(re)starting any
store whose task is missing while fleet driver count < 11, then aggregates
the ingest-progress.json done map, marks phase=ingest-complete, and launches
the E3+finalize pipeline via Task Scheduler.
"""
import json
import os
import subprocess
import sys
import time

WT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lpcamp"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = os.path.join(ROOT, "lpcamp-scratch")
LOG = os.path.join(SCRATCH, "logs", "parallel-finish.log")
STATE = os.path.join(SCRATCH, "state", "parallel-finish.json")
INGEST_STATE = os.path.join(SCRATCH, "state", "ingest-progress.json")
FLEET_CAP = 11
STORES = ["lp_118", "lp_18", "lp_159", "lp_69"]

_log = open(LOG, "a", buffering=1, encoding="utf-8", newline="\n")
sys.stdout = _log
sys.stderr = _log


def log(msg):
    print(f"[finish {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def write_state(payload):
    payload = dict(payload)
    payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, STATE)


def fleet_drivers():
    ps = os.path.join(SCRATCH, "count-drivers.ps1")
    try:
        cp = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", ps], capture_output=True, text=True, timeout=60)
        for line in (cp.stdout or "").splitlines():
            if line.startswith("count:"):
                return int(line.split(":")[1].strip())
    except Exception as exc:
        log(f"fleet count error: {exc!r}")
    return 999  # unknown -> do not start anything


def task_running(name):
    ps = os.path.join(SCRATCH, "task-state.ps1")
    try:
        cp = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", ps, name], capture_output=True, text=True, timeout=60)
        return (cp.stdout or "").strip().endswith("Running")
    except Exception:
        return True  # unknown -> assume running (safe)


def start_store_task(qid):
    cp = subprocess.run(["powershell", "-NoProfile", "-Command",
                         f"Start-ScheduledTask -TaskName 'lpcamp-one-{qid}'"],
                        capture_output=True, text=True, timeout=60)
    log(f"started {qid}: rc={cp.returncode} {(cp.stdout or '').strip()} {(cp.stderr or '').strip()}")


def store_state(qid):
    try:
        with open(os.path.join(SCRATCH, "state", f"ingest-{qid}.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def repair_state():
    try:
        with open(os.path.join(SCRATCH, "state", "repair-lp232.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def launch_pipeline():
    cmd = os.path.join(WT, "reports/continuation/iteration4/lpcamp", "run-lpcamp-pipeline2.cmd")
    ps1 = f"""
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c {cmd}'
Register-ScheduledTask -TaskName 'lpcamp-pipeline' -Action $action -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5)) -Force | Out-Null
Start-ScheduledTask -TaskName 'lpcamp-pipeline'
"pipeline started"
"""
    path = os.path.join(SCRATCH, "ts-pipeline.ps1")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ps1)
    cp = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-File", path], capture_output=True, text=True, timeout=120)
    log(f"pipeline launched: {(cp.stdout or '').strip()}")


def main():
    log("parallel finisher start")
    write_state({"phase": "waiting"})
    started = set()
    while True:
        # lp_232 via repair driver
        rep = repair_state() or {}
        lp232_ok = (rep.get("phase") == "complete" and
                    (rep.get("verify") or {}).get("ok"))
        lp232_failed = rep.get("phase") == "failed"
        # other stores
        done = {}
        all_ok, any_failed = lp232_ok, False
        if lp232_ok:
            done["lp_232"] = {"rc": "repair", "verify": rep.get("verify")}
        for qid in STORES:
            st = store_state(qid)
            if st and st.get("phase") == "ok":
                all_ok = all_ok and True
                done[qid] = {"rc": "parallel", "verify": st.get("verify")}
            elif st and st.get("phase") == "failed":
                any_failed = True
            else:
                all_ok = False
                # auto-start missing store when a slot frees
                if qid not in started and not task_running(f"lpcamp-one-{qid}"):
                    if fleet_drivers() < FLEET_CAP:
                        start_store_task(qid)
                        started.add(qid)
                        time.sleep(300)  # stagger: avoid DLL-init burst
                    else:
                        log(f"fleet at cap; {qid} waiting for a slot")
        if all_ok:
            log("all stores verified; writing ingest-complete")
            payload = {"phase": "ingest-complete", "current": None, "done": done}
            tmp = INGEST_STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            os.replace(tmp, INGEST_STATE)
            write_state({"phase": "complete", "done": {k: True for k in done}})
            launch_pipeline()
            log("finisher complete")
            return 0
        if any_failed:
            log("a store failed 3 attempts; aborting (heartbeat will diagnose)")
            write_state({"phase": "failed-store"})
            return 3
        write_state({"phase": "waiting", "stores_ok": sorted(done.keys())})
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
