"""Chain: wait for lp_232 repair -> run ingest queue v2 -> relaunch full pipeline."""
import json
import os
import sys
import time

WT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lpcamp"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = os.path.join(ROOT, "lpcamp-scratch")
REPAIR_STATE = os.path.join(SCRATCH, "state", "repair-lp232.json")
LOG = os.path.join(SCRATCH, "logs", "chain-after-repair.log")

_log = open(LOG, "a", buffering=1, encoding="utf-8", newline="\n")
sys.stdout = _log
sys.stderr = _log

sys.path.insert(0, os.path.join(WT, "reports/continuation/iteration4/lpcamp"))


def log(msg):
    print(f"[chain {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def repair_done():
    try:
        with open(REPAIR_STATE, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        return st.get("phase") in ("complete", "failed")
    except (OSError, json.JSONDecodeError):
        return False


def wait_repair(timeout_s=6 * 3600):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if repair_done():
            return True
        time.sleep(60)
    return False



def kill_early_pipeline(queue_running):
    """Kill the E3 pipeline driver if it starts while the ingest queue still runs."""
    import subprocess
    ps = os.path.join(SCRATCH, "find-lpcamp.ps1")
    try:
        cp = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", ps], capture_output=True, text=True, timeout=60)
        for line in (cp.stdout or "").splitlines():
            line = line.strip()
            if "launch_e3_and_finalize.py" in line:
                pid = line.split()[0]
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"Stop-Process -Id {pid} -Force"], timeout=30)
                log(f"killed early pipeline driver pid={pid} (queue still running)")
    except Exception as exc:
        log(f"early-pipeline guard error: {exc!r}")


def main():
    log("waiting for lp_232 repair to finish")
    ok = wait_repair()
    log(f"repair done={ok}")
    import launch_ingest_v2
    import threading

    stop_flag = threading.Event()

    def guard():
        while not stop_flag.is_set():
            kill_early_pipeline(queue_running=True)
            stop_flag.wait(60)

    guard_thread = threading.Thread(target=guard, daemon=True)
    guard_thread.start()
    try:
        rc = launch_ingest_v2.main()
    finally:
        stop_flag.set()
        guard_thread.join(timeout=70)
    log(f"ingest queue v2 rc={rc}")
    import subprocess
    ps1 = os.path.join(WT, "reports/continuation/iteration4/lpcamp/wmi-launch-pipeline2.ps1")
    cp = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-File", ps1], capture_output=True, text=True, timeout=60)
    log(f"pipeline relaunch: {(cp.stdout or '').strip()}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
