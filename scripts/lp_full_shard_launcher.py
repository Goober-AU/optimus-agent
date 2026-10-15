#!/usr/bin/env python3
"""Parallel shard launcher for the LoCoMo-Plus full 401-example run (memory_bench).

Adapted from `scripts/lmem_parallel_shard_launcher.py` (J's LongMemEval-M
launcher, worktree `imp3-lmemlaunch`, branch `improve/mem3-lmem-launcher`):
same process-per-shard pattern, per-shard log tailing, import-job counting,
failure detection, --time-limit/--retries semantics and status tables.  Kept
separate rather than edited in place because the LME-M launcher is J's
workstream; this file carries the LP-specific generalizations:

  - shards are discovered by glob (`--shard-glob`, default `lp_full_v3_s*.json`)
    instead of a hardcoded LME-M prefix, so any shard naming F produces works;
  - each shard manifest is parsed at startup: run_id, output_dir, env and
    question totals come from the manifest itself (the run dir the driver
    writes is whatever `output_dir` the manifest declares, not a name the
    launcher derives);
  - `--validate-only` inventories the selected manifests and checks the
    technique stack against a reference manifest (default: the live dev5 v3
    manifest) plus hardcoded expected values: the Cognitive answerInstruction,
    importInstructions, prospective+expansion+extract+distill+rerank ON,
    rerank pool 200, maxRecallEntries 20, maxRecallChars 24000,
    questionTimeoutS 300, and the dgx-glm53/glm-5.3 model;
  - the status table gains a per-shard question-progress column and shows the
    manifest-declared env/question totals.

Each shard process is launched exactly like the sequential baseline:

    cd <workdir> && <binary> <shard manifest> > <log> 2>&1
    with MEMORY_BENCH_MODELS_JSON=<models.json>

The driver resumes on relaunch (completed envs via env.jsonl, applied import
jobs short-circuit, entries that already carry prospectiveCues are skipped),
so `--retries` is safe.

Examples
  # inventory + technique-stack validation (no processes launched):
  python scripts/lp_full_shard_launcher.py --shards all --validate-only

  # print the plan without launching:
  python scripts/lp_full_shard_launcher.py --shards all --dry-run --log-dir <dir>

  # 2-env launcher-mechanics smoke (kills children after 15 min, exit 0):
  python scripts/lp_full_shard_launcher.py --shards s00,s01 --concurrency 2 \
      --time-limit 900 --log-dir <dir>/dryrun-logs --summary-json <dir>/summary.json

  # real full run, 6 concurrent drivers:
  python scripts/lp_full_shard_launcher.py --shards all --concurrency 6 \
      --retries 1 --status-interval 300 \
      --log-dir <dir>/fullrun-logs --summary-json <dir>/fullrun-summary.json

Only stdlib is used. Windows: children are killed with `taskkill /F /T`
so the whole process tree dies.
"""

from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BENCH_ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
DEFAULT_BINARY = BENCH_ROOT + "/imp3/target/release/examples/memory_bench.exe"
DEFAULT_WORKDIR = BENCH_ROOT + "/imp3"
DEFAULT_MANIFEST_DIR = BENCH_ROOT + "/reports/continuation/iteration3-inputs/manifests"
DEFAULT_REFERENCE_MANIFEST = (
    DEFAULT_MANIFEST_DIR + "/loco_plus_dev5_improved_v3.json"
)
DEFAULT_MODELS_JSON = (
    BENCH_ROOT
    + "/reports/continuation/c06-instruction-boundary/inputs/runs/"
    + "locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
)
DEFAULT_SHARD_GLOB = "lp_full_v3_s*.json"

# Technique-stack values the full-run manifests must carry (dev5 v3 stack:
# prospective + expansion + extract + distill + rerank + Cognitive answer
# instruction).  The reference manifest itself is checked against these.
EXPECTED_SETTINGS = {
    "recallOn": True,
    "memoryLearning": False,
    "answerMaxTokens": 8192,
    "maxExtractionTokens": 32000,
    "maxImportChunkChars": 15000,
    "maxRecallChars": 24000,
    "maxRecallEntries": 20,
    "questionTimeoutS": 300,
    "ingestTimeoutS": 21600,
    "importRunTimeoutS": 900,
    "disableTools": True,
    "evidenceTimestamps": True,
    "skipIngest": False,
    "recallQueryDistillation": True,
    "recallRerank": True,
    "recallRerankCandidates": 200,
    "recallQueryExtract": True,
    "recallQueryExpansion": True,
    "recallQueryExpansionDraws": 2,
    "recallProspectiveIndex": True,
}
EXPECTED_MODEL = {"provider": "dgx-glm53", "id": "glm-5.3"}
COGNITIVE_MARKER = "memory-aware dialogue setting"  # Cognitive answerInstruction

ENV_START_RE = re.compile(r"\[env (\d+)/(\d+)\] (\S+) ingesting (\d+) session")
ENV_DONE_RE = re.compile(r"\[env \d+/\d+\] \S+ ingested in ")
Q_START_RE = re.compile(r"\[q (\d+)/(\d+)\] \S+ asking ")
Q_DONE_RE = re.compile(r"\[q \d+/\d+\] \S+ done in ")
FATAL_RE = re.compile(r"error:|panicked at", re.IGNORECASE)
RETRY_RE = re.compile(r"failed \(\d+ consecutive\)|timed out for job", re.IGNORECASE)

PRINT_LOCK = threading.Lock()
LAUNCH_LOCK = threading.Lock()
_next_start = 0.0  # protected by LAUNCH_LOCK
SHUTDOWN = threading.Event()  # set when children are killed by --time-limit/interrupt


def log(msg: str) -> None:
    with PRINT_LOCK:
        print(msg, flush=True)


def load_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def settings_violations(settings: dict) -> list[str]:
    """Hardcoded technique-stack checks (also applied to the reference)."""
    out: list[str] = []
    for key, expected in EXPECTED_SETTINGS.items():
        actual = settings.get(key, "<missing>")
        if actual != expected:
            out.append(f"settings.{key}: expected {expected!r}, got {actual!r}")
    answer = settings.get("answerInstruction")
    if not isinstance(answer, str) or COGNITIVE_MARKER not in answer:
        out.append("settings.answerInstruction: Cognitive marker "
                   f"{COGNITIVE_MARKER!r} not found")
    return out


def manifest_violations(manifest: dict, reference: dict, path: str) -> list[str]:
    """Full checklist for one shard manifest against the reference stack."""
    out: list[str] = []
    if manifest.get("benchmark") != reference.get("benchmark"):
        out.append(f"benchmark: {manifest.get('benchmark')!r} != reference "
                   f"{reference.get('benchmark')!r}")
    if manifest.get("variant") != reference.get("variant"):
        out.append(f"variant: {manifest.get('variant')!r} != reference "
                   f"{reference.get('variant')!r}")
    if manifest.get("captureProtocol") != reference.get("captureProtocol"):
        out.append(f"captureProtocol: {manifest.get('captureProtocol')!r} != reference "
                   f"{reference.get('captureProtocol')!r}")
    if manifest.get("model") != reference.get("model"):
        out.append(f"model: {manifest.get('model')!r} != reference "
                   f"{reference.get('model')!r}")
    if manifest.get("model") != EXPECTED_MODEL:
        out.append(f"model: must be dgx-glm53/glm-5.3, got {manifest.get('model')!r}")
    actual_settings = manifest.get("settings") or {}
    ref_settings = reference.get("settings") or {}
    for key in sorted(set(EXPECTED_SETTINGS) | {"answerInstruction", "importInstructions"}):
        if key not in actual_settings:
            out.append(f"settings.{key}: missing")
            continue
        if key in ref_settings and actual_settings.get(key) != ref_settings.get(key):
            out.append(f"settings.{key}: differs from reference manifest")
    out.extend(settings_violations(actual_settings))

    envs = manifest.get("envs") or []
    questions = manifest.get("questions") or []
    if not envs:
        out.append("envs: empty")
    env_ids = [env.get("env_id") for env in envs]
    if len(set(env_ids)) != len(env_ids):
        dupes = sorted({e for e in env_ids if env_ids.count(e) > 1})
        out.append(f"envs: duplicate env_id(s): {dupes}")
    for env in envs:
        if not env.get("sessions"):
            out.append(f"envs.{env.get('env_id')}: no sessions")
    qids = [q.get("qid") for q in questions]
    if len(set(qids)) != len(qids):
        out.append("questions: duplicate qid(s)")
    env_id_set = set(env_ids)
    for q in questions:
        if q.get("env_id") not in env_id_set:
            out.append(f"questions.{q.get('qid')}: env_id {q.get('env_id')!r} not in envs")
    if not manifest.get("output_dir"):
        out.append("output_dir: missing")
    return out


class Shard:
    def __init__(self, name: str, manifest: str, log_path: str):
        self.name = name
        self.manifest = manifest
        self.log_path = log_path
        self.run_id: str | None = None
        self.run_dir: str | None = None  # manifest output_dir
        self.env_total_declared = 0
        self.q_total_declared = 0
        self.violations: list[str] = []
        self.state = "QUEUED"
        self.proc: subprocess.Popen | None = None
        self.launch_error: str | None = None
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.exit_code: int | None = None
        self.offset = 0
        self.lines = 0
        self.envs_started = 0
        self.envs_done = 0
        self.env_total = 0
        self.q_started = 0
        self.q_done = 0
        self.q_total = 0
        self.fatal_lines = 0
        self.retry_lines = 0
        self.last_line = ""
        self.last_activity: float | None = None
        self.jobs_applied = 0
        self.jobs_seen = 0
        self._job_status: dict[str, tuple[str, float]] = {}
        self._stall_sig: tuple | None = None
        self.attempts = 0

    # ---- manifest introspection -----------------------------------------
    def read_manifest(self, manifest: dict | None = None) -> None:
        if manifest is None:
            try:
                manifest = load_manifest(self.manifest)
            except (OSError, ValueError) as exc:
                self.launch_error = f"cannot read manifest: {exc}"
                self.violations = [self.launch_error]
                self.state = "FAILED"
                return
        self.run_id = manifest.get("run_id")
        self.run_dir = manifest.get("output_dir")
        self.env_total_declared = len(manifest.get("envs") or [])
        self.q_total_declared = len(manifest.get("questions") or [])

    # ---- log tailing ----------------------------------------------------
    def poll_log(self) -> None:
        try:
            size = os.path.getsize(self.log_path)
        except OSError:
            return
        if size <= self.offset:
            return
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
        except OSError:
            return
        self.offset += len(data)
        text = data.decode("utf-8", "replace")
        for line in text.splitlines():
            if not line.strip():
                continue
            self.lines += 1
            self.last_line = line
            if m := ENV_START_RE.search(line):
                self.envs_started += 1
                self.env_total = int(m.group(2))
            elif ENV_DONE_RE.search(line):
                self.envs_done += 1
            elif m := Q_START_RE.search(line):
                self.q_started += 1
                self.q_total = int(m.group(2))
            elif Q_DONE_RE.search(line):
                self.q_done += 1
            if FATAL_RE.search(line):
                self.fatal_lines += 1
            elif RETRY_RE.search(line):
                self.retry_lines += 1
        self.last_activity = time.time()

    # ---- import job counting -------------------------------------------
    def poll_jobs(self) -> None:
        if not self.run_dir:
            return
        pattern = os.path.join(
            self.run_dir, "envs", "*", "agent", "memory", "projects", "*", "jobs",
            "import_*.json",
        )
        for path in glob.glob(pattern):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            prev = self._job_status.get(path)
            if prev is not None:
                status, prev_mtime = prev
                if status == "applied" or prev_mtime == mtime:
                    continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    status = json.load(fh).get("status", "?")
            except (OSError, ValueError):
                continue
            self._job_status[path] = (status, mtime)
        self.jobs_applied = sum(
            1 for status, _ in self._job_status.values() if status == "applied"
        )
        self.jobs_seen = len(self._job_status)


def kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        try:
            proc.kill()
        except OSError:
            pass


def run_shard(sh: Shard, args: argparse.Namespace) -> None:
    global _next_start
    while True:
        if SHUTDOWN.is_set():
            if sh.state == "QUEUED":
                sh.state = "SKIPPED"
            return
        sh.attempts += 1
        # Stagger launches so N drivers do not hit the endpoint preflight at once.
        with LAUNCH_LOCK:
            now = time.monotonic()
            wait = max(0.0, _next_start - now)
            _next_start = now + wait + args.stagger
        if wait > 0:
            time.sleep(wait)
        if not os.path.isfile(sh.manifest):
            sh.launch_error = "manifest not found: " + sh.manifest
            sh.state = "FAILED"
            return
        env = dict(os.environ)
        env["MEMORY_BENCH_MODELS_JSON"] = args.models_json
        cmd = [args.binary, sh.manifest]
        log(f"[{sh.name}] launching (attempt {sh.attempts}): {' '.join(cmd)}")
        log(f"[{sh.name}] cwd={args.workdir} log={sh.log_path}")
        try:
            fh = open(sh.log_path, "wb")
        except OSError as exc:
            sh.launch_error = f"cannot open log: {exc}"
            sh.state = "FAILED"
            return
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=args.workdir,
                stdout=fh,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            sh.launch_error = f"launch failed: {exc}"
            sh.state = "FAILED"
            return
        finally:
            fh.close()
        sh.proc = proc
        sh.state = "RUNNING"
        sh.started_at = sh.started_at or time.time()
        sh.last_activity = time.time()
        rc = proc.wait()
        sh.exit_code = rc
        sh.ended_at = time.time()
        sh.proc = None
        if SHUTDOWN.is_set():
            # Killed via --time-limit, --kill-stalled or Ctrl+C; not a failure.
            if sh.state != "KILLED":
                sh.state = "KILLED"
            return
        if rc == 0:
            sh.state = "DONE"
            return
        sh.state = "FAILED"
        log(f"[{sh.name}] exited with code {rc} after {int(sh.ended_at - sh.started_at)}s")
        if sh.attempts <= args.retries:
            log(f"[{sh.name}] retrying (driver resumes completed envs/jobs)")
            sh.state = "QUEUED"
            continue
        return


def fmt_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def print_table(shards: list[Shard], title: str) -> None:
    now = time.time()
    lines = [
        f"==== {title} ====",
        f"{'SHARD':<8} {'STATE':<9} {'ELAPSED':>7} {'EXIT':>4} {'ENVS':>9} "
        f"{'Q':>9} {'JOBS':>10} {'FATAL':>5} {'RETRY':>5} {'IDLE':>6}  LAST LOG LINE",
    ]
    for sh in shards:
        elapsed = None
        if sh.started_at:
            elapsed = (sh.ended_at or now) - sh.started_at
        idle = "-"
        if sh.state == "RUNNING" and sh.last_activity:
            idle = fmt_elapsed(max(0, now - sh.last_activity))
        envs = f"{sh.envs_done}/{sh.env_total or sh.env_total_declared or '?'}"
        qs = f"{sh.q_done}/{sh.q_total or sh.q_total_declared or '?'}"
        jobs = f"{sh.jobs_applied}/{sh.jobs_seen}"
        last = sh.last_line.replace("\t", " ")
        if len(last) > 100:
            last = last[:97] + "..."
        lines.append(
            f"{sh.name:<8} {sh.state:<9} {fmt_elapsed(elapsed):>7} "
            f"{str(sh.exit_code if sh.exit_code is not None else '-'):>4} {envs:>9} "
            f"{qs:>9} {jobs:>10} {sh.fatal_lines:>5} {sh.retry_lines:>5} {idle:>6}  {last}"
        )
        if sh.launch_error:
            lines.append(f"         launch error: {sh.launch_error}")
    log("\n".join(lines))


def monitor(shards: list[Shard], args: argparse.Namespace, started: float,
            deadline: float | None) -> bool:
    """Returns True if the run ended by hitting --time-limit."""
    next_status = started + args.status_interval
    while any(sh.state in ("QUEUED", "RUNNING", "STARTING") for sh in shards):
        now = time.time()
        if deadline is not None and now >= deadline:
            log(f"time limit ({args.time_limit:.0f}s) reached; killing all children")
            SHUTDOWN.set()
            for sh in shards:
                if sh.state == "RUNNING":
                    kill_tree(sh.proc)
                    sh.state = "KILLED"
                    sh.ended_at = time.time()
            return True
        wake = min(next_status, deadline or now + args.status_interval)
        time.sleep(max(0.05, min(args.status_interval, wake - now)))
        for sh in shards:
            sh.poll_log()
            sh.poll_jobs()
            if (sh.state == "RUNNING" and sh.last_activity
                    and time.time() - sh.last_activity > args.stall_minutes * 60):
                stall_sig = (sh.lines, sh.jobs_applied)
                if sh._stall_sig != stall_sig:
                    sh._stall_sig = stall_sig
                    log(f"[{sh.name}] STALLED: no log growth and no new applied jobs "
                        f"for {args.stall_minutes:.0f} min "
                        f"(lines={sh.lines}, jobs_applied={sh.jobs_applied})")
                if args.kill_stalled:
                    kill_tree(sh.proc)
        if time.time() >= next_status:
            print_table(shards, f"status t+{int(time.time() - started)}s")
            next_status += args.status_interval
    return False


def resolve_shards(args: argparse.Namespace) -> list[Shard]:
    all_paths = sorted(
        p for p in glob.glob(os.path.join(args.manifest_dir, args.shard_glob))
        if os.path.isfile(p)
    )
    by_stem: dict[str, str] = {}
    for path in all_paths:
        by_stem[os.path.splitext(os.path.basename(path))[0]] = path
    tokens: list[str] = []
    for tok in args.shards.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok == "all":
            tokens.extend(by_stem)
            continue
        range_match = re.fullmatch(r"(s\d+)-(s\d+)", tok)
        if range_match:
            start = int(range_match.group(1)[1:])
            end = int(range_match.group(2)[1:])
            tokens.extend(f"s{i:02d}" for i in range(start, end + 1))
            continue
        tokens.append(tok)
    if not tokens:
        raise SystemExit("no shards selected")
    shards: list[Shard] = []
    for tok in tokens:
        path = by_stem.get(tok)
        if path is None and os.path.isfile(tok):
            path = os.path.abspath(tok)
        elif path is None and os.path.isfile(os.path.join(args.manifest_dir, tok)):
            path = os.path.join(args.manifest_dir, tok)
        if path is None:
            raise SystemExit(f"unknown shard: {tok}")
        shards.append(Shard(os.path.splitext(os.path.basename(path))[0], path,
                            ""))
    # Display names: strip the longest common prefix of all selected stems.
    stems = [sh.name for sh in shards]
    prefix = os.path.commonprefix(stems)
    prefix = prefix[:prefix.rfind("_") + 1] if "_" in prefix else ""
    for sh in shards:
        if prefix and sh.name.startswith(prefix):
            sh.name = sh.name[len(prefix):]
    if len({sh.name for sh in shards}) != len(shards):
        raise SystemExit("duplicate shards selected")
    return shards


def validate(shards: list[Shard], reference: dict, args: argparse.Namespace) -> int:
    total_envs = 0
    total_questions = 0
    total_sessions = 0
    seen_env_ids: dict[str, str] = {}
    seen_qids: dict[str, str] = {}
    seen_run_dirs: dict[str, str] = {}
    bad = 0
    for sh in shards:
        try:
            manifest = load_manifest(sh.manifest)
        except (OSError, ValueError) as exc:
            sh.violations = [f"cannot read manifest: {exc}"]
            sh.launch_error = sh.violations[0]
            sh.state = "FAILED"
            bad += 1
            log(f"[{sh.name}] {sh.launch_error}")
            continue
        sh.read_manifest(manifest)
        total_envs += sh.env_total_declared
        total_questions += sh.q_total_declared
        sh.violations = manifest_violations(manifest, reference, sh.manifest)
        for env in manifest.get("envs") or []:
            total_sessions += len(env.get("sessions") or [])
            eid = env.get("env_id")
            if eid in seen_env_ids and seen_env_ids[eid] != sh.name:
                sh.violations.append(f"env_id {eid} also in shard {seen_env_ids[eid]}")
            seen_env_ids[eid] = sh.name
        for q in manifest.get("questions") or []:
            qid = q.get("qid")
            if qid in seen_qids and seen_qids[qid] != sh.name:
                sh.violations.append(f"qid {qid} also in shard {seen_qids[qid]}")
            seen_qids[qid] = sh.name
        rd = manifest.get("output_dir")
        if rd:
            if rd in seen_run_dirs and seen_run_dirs[rd] != sh.name:
                sh.violations.append(f"output_dir {rd} also used by shard {seen_run_dirs[rd]}")
            seen_run_dirs[rd] = sh.name
            for name in ("envs", "env.jsonl", "run.jsonl"):
                if os.path.exists(os.path.join(rd, name)):
                    # Not a violation: relaunching an existing run dir resumes it.
                    log(f"    note: {rd} already contains {name} "
                        f"(resume of an existing run)")
        if sh.violations:
            bad += 1
        log(f"[{sh.name}] envs={sh.env_total_declared} questions={sh.q_total_declared} "
            f"run_id={sh.run_id} violations={len(sh.violations)}")
        for violation in sh.violations:
            log(f"    VIOLATION: {violation}")
    log(f"TOTALS: {len(shards)} shard(s), {total_envs} env(s), {total_sessions} session(s), "
        f"{total_questions} question(s)")
    if args.expect_envs is not None and total_envs != args.expect_envs:
        log(f"VIOLATION: expected {args.expect_envs} envs across shards, found {total_envs}")
        bad += 1
    if args.expect_questions is not None and total_questions != args.expect_questions:
        log(f"VIOLATION: expected {args.expect_questions} questions across shards, "
            f"found {total_questions}")
        bad += 1
    if bad:
        log(f"VALIDATION FAILED: {bad} shard(s) with violations")
        return 1
    log("VALIDATION PASSED: technique stack matches the reference manifest")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run LoCoMo-Plus full-run shard drivers concurrently.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--shards", default="all",
                        help="comma list/range of shards (s00, s00-s04, all) or manifest "
                             "file names/paths")
    parser.add_argument("--concurrency", type=int, default=6,
                        help="concurrent driver processes")
    parser.add_argument("--shard-glob", default=DEFAULT_SHARD_GLOB,
                        help="glob (inside --manifest-dir) that selects full-run shards")
    parser.add_argument("--manifest-dir", default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--reference-manifest", default=DEFAULT_REFERENCE_MANIFEST,
                        help="dev manifest whose settings/model define the expected "
                             "technique stack")
    parser.add_argument("--expect-envs", type=int, default=401,
                        help="expected total envs across selected shards "
                             "(validation); use -1 to skip")
    parser.add_argument("--expect-questions", type=int, default=401,
                        help="expected total questions across selected shards "
                             "(validation); use -1 to skip")
    parser.add_argument("--binary", default=DEFAULT_BINARY)
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR,
                        help="cwd for each driver process")
    parser.add_argument("--models-json", default=DEFAULT_MODELS_JSON,
                        help="value for MEMORY_BENCH_MODELS_JSON")
    parser.add_argument("--log-dir", default=None,
                        help="directory for per-shard logs (required unless "
                             "--validate-only)")
    parser.add_argument("--stagger", type=float, default=5.0,
                        help="seconds between process launches")
    parser.add_argument("--status-interval", type=float, default=300.0)
    parser.add_argument("--time-limit", type=float, default=0.0,
                        help="kill everything after T seconds (dry runs); 0 = unlimited")
    parser.add_argument("--stall-minutes", type=float, default=30.0,
                        help="flag a RUNNING shard as STALLED after this many minutes "
                             "without log growth or new applied import jobs")
    parser.add_argument("--kill-stalled", action="store_true",
                        help="kill stalled shards instead of just flagging them")
    parser.add_argument("--retries", type=int, default=0,
                        help="relaunch a failed shard up to N times (driver resumes)")
    parser.add_argument("--summary-json", default=None)
    parser.add_argument("--validate-only", action="store_true",
                        help="inventory + technique-stack validation, then exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the planned commands and exit without launching")
    args = parser.parse_args()

    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")

    shards = resolve_shards(args)

    try:
        reference = load_manifest(args.reference_manifest)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read reference manifest {args.reference_manifest}: {exc}")
    ref_violations = settings_violations(reference.get("settings") or {})
    if ref_violations:
        for violation in ref_violations:
            log(f"REFERENCE VIOLATION: {violation}")
        raise SystemExit("reference manifest does not satisfy the technique stack; "
                         "fix the reference before validating shards")
    log(f"reference manifest OK: {args.reference_manifest}")

    if args.expect_envs is not None and args.expect_envs < 0:
        args.expect_envs = None
    if args.expect_questions is not None and args.expect_questions < 0:
        args.expect_questions = None

    if args.validate_only:
        return validate(shards, reference, args)

    if not args.log_dir:
        parser.error("--log-dir is required (or use --validate-only)")
    for sh in shards:
        sh.log_path = os.path.join(args.log_dir, sh.name + ".log")
    os.makedirs(args.log_dir, exist_ok=True)

    log(f"launcher: {len(shards)} shard(s), concurrency={args.concurrency}")
    log(f"binary: {args.binary}")
    log(f"workdir: {args.workdir}")
    log(f"models_json: {args.models_json}")
    log(f"log_dir: {args.log_dir}")

    if args.dry_run:
        for sh in shards:
            sh.read_manifest()
            log(f"[{sh.name}] would run: cd {args.workdir} && {args.binary} {sh.manifest} "
                f"> {sh.log_path} 2>&1  (MEMORY_BENCH_MODELS_JSON={args.models_json})")
            log(f"        run_id={sh.run_id} output_dir={sh.run_dir} "
                f"envs={sh.env_total_declared} questions={sh.q_total_declared}")
        return 0

    # Read manifests once for run dirs/totals before launching.
    for sh in shards:
        sh.read_manifest()
        if sh.state == "FAILED":  # unreadable manifest
            log(f"[{sh.name}] {sh.launch_error}")

    started = time.time()
    deadline = started + args.time_limit if args.time_limit > 0 else None

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(run_shard, sh, args) for sh in shards]
        try:
            killed_by_limit = monitor(shards, args, started, deadline)
        except KeyboardInterrupt:
            log("interrupted; killing all children")
            SHUTDOWN.set()
            for sh in shards:
                if sh.state == "RUNNING":
                    kill_tree(sh.proc)
                    sh.state = "KILLED"
                    sh.ended_at = time.time()
            for fut in futures:
                fut.result()
            for sh in shards:
                sh.poll_log()
                sh.poll_jobs()
            print_table(shards, "FINAL SUMMARY (interrupted)")
            return 130
        for fut in futures:
            fut.result()

    for sh in shards:
        sh.poll_log()
        sh.poll_jobs()

    print_table(shards, "FINAL SUMMARY")
    failed = [sh for sh in shards if sh.state == "FAILED"]
    done = [sh for sh in shards if sh.state == "DONE"]
    killed = [sh for sh in shards if sh.state == "KILLED"]
    total_jobs = sum(sh.jobs_applied for sh in shards)
    log(f"result: {len(done)} done, {len(failed)} failed, {len(killed)} killed; "
        f"{total_jobs} import job(s) applied; wall={fmt_elapsed(time.time() - started)}")
    for sh in failed:
        log(f"  FAILED {sh.name}: exit={sh.exit_code} fatal_lines={sh.fatal_lines} "
            f"log={sh.log_path}")

    if args.summary_json:
        payload = {
            "started": started,
            "finished": time.time(),
            "killed_by_limit": killed_by_limit,
            "args": {
                "shards": [sh.name for sh in shards],
                "concurrency": args.concurrency,
                "time_limit": args.time_limit,
                "binary": args.binary,
                "workdir": args.workdir,
                "models_json": args.models_json,
            },
            "shards": [
                {
                    "name": sh.name,
                    "manifest": sh.manifest,
                    "log": sh.log_path,
                    "run_id": sh.run_id,
                    "run_dir": sh.run_dir,
                    "state": sh.state,
                    "exit_code": sh.exit_code,
                    "attempts": sh.attempts,
                    "started_at": sh.started_at,
                    "ended_at": sh.ended_at,
                    "log_lines": sh.lines,
                    "envs_started": sh.envs_started,
                    "envs_done": sh.envs_done,
                    "env_total": sh.env_total or sh.env_total_declared,
                    "questions_done": sh.q_done,
                    "question_total": sh.q_total or sh.q_total_declared,
                    "jobs_applied": sh.jobs_applied,
                    "jobs_seen": sh.jobs_seen,
                    "fatal_lines": sh.fatal_lines,
                    "retry_lines": sh.retry_lines,
                    "last_line": sh.last_line,
                }
                for sh in shards
            ],
        }
        with open(args.summary_json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        log(f"summary json: {args.summary_json}")

    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
