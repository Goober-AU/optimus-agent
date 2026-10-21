#!/usr/bin/env python3
"""Local (Windows VM) key-rotation watcher for the OCC-3 deployment.

Polls the DGX controller's status.json through the existing SSH
wrapper. When (and only when) it reports result == "SUCCESS", the
Windows user environment variable GLM53_API_KEY is updated to the new
key (read from a local 0600 file, never logged). On FAILED or timeout
the watcher exits without touching anything: the preserved container
still uses the old key, so no local change is needed.

This watcher exists because the Optimus session itself runs on the GLM
server being restarted: no AI action is possible during the swap, so
the local side of the key rotation must be model-independent too.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SSH = r"C:\Program Files\Git\usr\bin\ssh.exe"
KEY = r"<LOCAL_SSH_KEY_PATH>"
KH = r"C:\Users\openclawuser\.ssh\known_hosts"
REMOTE = "nvidia@<ALLOCATION_ID>@<LAUNCHPAD_GATEWAY_HOST>"

PY_WRAPPER = Path(sys.argv[2])  # dgx_ssh.py wrapper path
LOCAL_PYTHON = sys.executable

STATUS_CMD = ("cat /data/neo-glm53/recovery/occ3-active-deploy/status.json "
              "2>/dev/null || echo none")

POLL_S = 20
TIMEOUT_S = 3600


def remote_status() -> dict | None:
    proc = subprocess.run(
        [LOCAL_PYTHON, str(PY_WRAPPER), "run", STATUS_CMD, "--timeout", "30"],
        capture_output=True, text=True, timeout=90,
    )
    if proc.returncode != 0:
        return None
    try:
        # the wrapper prints an envelope {"rc":..,"stdout":..,"stderr":..};
        # the status document is inside stdout
        envelope = json.loads(proc.stdout)
        payload = envelope.get("stdout", "").strip()
        if not payload or payload == "none":
            return None
        return json.loads(payload)
    except (ValueError, AttributeError):
        return None


def set_user_env(name: str, value: str) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "[Environment]::SetEnvironmentVariable($args[0], $args[1], 'User'); "
         "Write-Output ok", "-", name, value],
        capture_output=True, text=True, timeout=60, check=True,
    )


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: occ3_key_watcher.py <new-key-file> <dgx_ssh.py-path>",
              file=sys.stderr)
        return 3
    new_key_file = Path(sys.argv[1])
    log_path = new_key_file.parent / "watcher.log"
    new_key = new_key_file.read_text(encoding="utf-8").strip()

    def log(message: str) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(stamp + " " + message + "\n")
        print(stamp, message, flush=True)

    deadline = time.time() + TIMEOUT_S
    log("watcher start; polling DGX deployment status every " + str(POLL_S) + "s")
    while time.time() < deadline:
        status = remote_status()
        if status is None:
            log("status unreachable; retrying")
        else:
            result = status.get("result")
            phase = status.get("phase")
            if result == "SUCCESS":
                log("deployment SUCCESS; rotating GLM53_API_KEY (User scope)")
                set_user_env("GLM53_API_KEY", new_key)
                log("rotation done; deleting local key copy")
                new_key_file.unlink(missing_ok=True)
                return 0
            if result == "FAILED":
                log("deployment FAILED (" + str(phase) + "); no local change")
                return 1
            if phase == "gate8" or phase == "COMPLETE_GATE8":
                # controller moved past deployment into gate8 probing;
                # the service swap itself succeeded
                log("gate8 phase reached; rotating GLM53_API_KEY (User scope)")
                set_user_env("GLM53_API_KEY", new_key)
                log("rotation done; deleting local key copy")
                new_key_file.unlink(missing_ok=True)
                return 0
            log("phase=" + str(phase) + " result=" + str(result))
        time.sleep(POLL_S)
    log("watcher timed out without a terminal status; no local change")
    return 2


if __name__ == "__main__":
    sys.exit(main())
