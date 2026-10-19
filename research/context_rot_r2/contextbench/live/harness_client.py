"""compaction-harness binary client (W27 live practice).

One subprocess per append+compact cycle, mirroring the W26 live wiring:
`summarizer: "live"` in the request plus the `ROT2_LIVE_*` environment
(Bearer + X-Pomerium-Authorization headers, model, HTTP timeout, call-log
dir). The binary writes one JSONL line per HTTP request into
`ROT2_LIVE_CALL_LOG_DIR`; this client collects those lines after the
subprocess exits and attributes them to the cycle.

Credentials are resolved once from the two PowerShell helpers and are
passed to the child environment only; they are never logged, printed or
written to any artifact.
"""

import json
import os
import subprocess
from typing import Dict, List, Optional, Tuple

CRED_BEARER_SCRIPT = (
    "C:/Users/openclawuser/Optimus-Assistant/profile/agent/skill-state/"
    "dgx-k3-login/glm53-resolvers/cd5ef6501b924449917676c6ddb998a8.ps1"
)
CRED_POMERIUM_SCRIPT = (
    "C:/Users/openclawuser/optimus-rust-test-20260915/profile/agent/"
    "credentials/Get-K3PomeriumJwt.ps1"
)

DEFAULT_BASE_URL = (
    "https://a8349d0f-f602-991e-52c5-963eb2f2e949-in-docker-k3.apps."
    "launchpad.nvidia.com/v1"
)
DEFAULT_MODEL = "glm-5.3"
DEFAULT_HTTP_TIMEOUT_S = 900.0


def _run_ps1(path: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
        capture_output=True,
        text=True,
        timeout=180,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError(
            f"credential helper produced no token: {os.path.basename(path)} "
            f"(stderr tail: {result.stderr.strip()[-120:]})"
        )
    return token


def resolve_credentials() -> Tuple[str, str]:
    """(bearer, pomerium) - resolved fresh, never stored or printed."""
    return _run_ps1(CRED_BEARER_SCRIPT), _run_ps1(CRED_POMERIUM_SCRIPT)


def cycle_request(
    session_dir: str,
    new_turns: List[dict],
    settings: Dict[str, object],
    summarizer: str,
) -> dict:
    """One harness request: append `new_turns`, then compact."""
    return {
        "sessionDir": session_dir,
        "newTurns": [
            {
                "role": turn["role"],
                "content": turn["content"],
                "turnId": turn_id,
            }
            for turn_id, turn in enumerate(new_turns)
        ],
        "settings": {
            "reserveTokens": settings["reserveTokens"],
            "keepRecentTokens": settings["keepRecentTokens"],
            "taskState": settings["taskState"],
        },
        "summarizer": summarizer,
    }


def run_cycle(
    binary: str,
    request: dict,
    log_dir: str,
    bearer: str,
    pomerium: str,
    timeout_s: float,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
) -> Tuple[Optional[dict], str, List[dict]]:
    """Run one harness cycle.

    Returns (parsed response or None, stderr tail, call-log records). The
    call-log records are the JSONL lines the binary wrote into `log_dir`,
    annotated with nothing (the caller adds instance/cycle context).
    """
    os.makedirs(log_dir, exist_ok=True)
    env = dict(os.environ)
    env["ROT2_LIVE_BASE_URL"] = base_url
    env["ROT2_LIVE_API_KEY"] = bearer
    env["ROT2_LIVE_POMERIUM"] = pomerium
    env["ROT2_LIVE_MODEL"] = model
    env["ROT2_LIVE_CALL_LOG_DIR"] = log_dir
    env["ROT2_LIVE_HTTP_TIMEOUT_S"] = str(DEFAULT_HTTP_TIMEOUT_S)
    env["ROT2_LIVE_MAX_TOKENS_START"] = "8192"
    try:
        completed = subprocess.run(
            [binary],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
        stdout, stderr = completed.stdout, completed.stderr
        returncode = completed.returncode
    except subprocess.TimeoutExpired as expired:
        stderr = f"harness subprocess timed out after {timeout_s}s"
        stdout = (expired.stdout or b"").decode("utf-8", "replace") if isinstance(
            expired.stdout, bytes
        ) else (expired.stdout or "")
        returncode = -1
    response = None
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                response = json.loads(line)
            except ValueError:
                response = None
            if response is not None:
                break
    records = read_call_log(log_dir)
    if response is None:
        response = {
            "status": "error",
            "error": f"harness produced no response (returncode {returncode})",
            "summary": "",
            "summarizedTurnIds": [],
            "keptTurnIds": [],
            "ledger": None,
            "warnings": [stderr.strip()[-300:]] if stderr.strip() else [],
        }
    return response, stderr[-400:], records


def read_call_log(log_dir: str) -> List[dict]:
    """All JSONL call records the binary wrote under `log_dir`."""
    records: List[dict] = []
    if not os.path.isdir(log_dir):
        return records
    for name in sorted(os.listdir(log_dir)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(log_dir, name)
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    records.append({"parseError": line[:200]})
    return records
