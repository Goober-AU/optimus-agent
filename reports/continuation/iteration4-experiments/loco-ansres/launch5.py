#!/usr/bin/env python3
"""loco-ansres round-5 orchestrator: staggered conversation launches + completion wait.

Writes one run5-<conv>.cmd launcher per conversation (self-contained quoting),
launches it WMI-detached (session-owned), 5-minute stagger, live-driver fleet
check (cap 11) before each launch, then waits for every conversation's .done
sentinel (global timeout 4h). Writes launch5-summary.json.
"""
import json, os, subprocess, time

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + "/imp4b-ansres"
EXP = WT + "/reports/continuation/iteration4-experiments/loco-ansres"
BENCH_VENV = ROOT + "/repo-continuation/bench/.venv/Scripts/python.exe"
CONVS = ["26", "41", "42", "43", "44", "47", "48", "50"]
STAGGER_S = 300
FLEET_CAP = 11
GLOBAL_TIMEOUT_S = 4 * 3600

def bs(p):
    return p.replace("/", "\\")

def live_drivers():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq memory_bench.exe", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=30)
        return sum(1 for line in r.stdout.splitlines()
                   if line.strip().startswith('"memory_bench.exe"'))
    except Exception:
        return 0

def wait_fleet_slot(log):
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        n = live_drivers()
        if n < FLEET_CAP:
            return n
        log("fleet at %d (cap %d); waiting 60s" % (n, FLEET_CAP))
        time.sleep(60)
    n = live_drivers()
    log("fleet gate deadline exceeded (fleet=%d); launching anyway per LAUNCH NOW" % n)
    return n

def write_cmd_launcher(conv, jobs, out, logpath):
    cmdfile = EXP + ("/run5-%s.cmd" % conv)
    body = ("@echo off\r\n"
            '"%s" "%s" "%s" "%s" > "%s" 2>&1\r\n' % (
                bs(BENCH_VENV), bs(EXP + "/run5.py"), bs(jobs), bs(out), bs(logpath)))
    with open(cmdfile, "w", encoding="ascii", newline="") as f:
        f.write(body)
    return cmdfile

def wmi_launch(cmdfile):
    ps = ("Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{ CommandLine = 'cmd.exe /c " + bs(cmdfile) + "' }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    return (r.stdout.strip() + " " + r.stderr.strip()[:200]).replace("\n", " ")

def main():
    t0 = time.time()
    def log(msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(EXP + "/launch5.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    launched = {}
    for i, conv in enumerate(CONVS):
        target = t0 + i * STAGGER_S
        while time.time() < target:
            time.sleep(min(30, max(0.1, target - time.time())))
        n = wait_fleet_slot(log)
        jobs = EXP + "/jobs-r5-%s.json" % conv
        out = EXP + "/results-r5-%s.json" % conv
        lg = EXP + "/run5-%s.log" % conv
        cmdfile = write_cmd_launcher(conv, jobs, out, lg)
        res = wmi_launch(cmdfile)
        launched[conv] = {"cmd": cmdfile, "jobs": jobs, "results": out, "wmi": res}
        log("launched conv-%s (fleet was %d) | %s" % (conv, n, res[:160]))

    deadline = time.time() + GLOBAL_TIMEOUT_S
    pending = CONVS[:]
    while time.time() < deadline and pending:
        pending = [c for c in CONVS
                   if not os.path.exists(EXP + ("/results-r5-%s.json.done" % c))]
        if pending:
            time.sleep(30)

    summary = {"launched": launched, "pending_at_exit": pending,
               "elapsed_s": round(time.time() - t0, 1), "finished": not pending}
    with open(EXP + "/launch5-summary.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    log("orchestrator exit: finished=%s pending=%s elapsed=%.0fs" % (
        not pending, pending, time.time() - t0))

if __name__ == "__main__":
    main()
