#!/usr/bin/env python3
"""Parallel shard launcher for the LongMemEval-M full run (memory_bench).

Runs N `memory_bench.exe` shard drivers concurrently on a single Windows box.
Each shard process is launched exactly like the sequential baseline:

    cd <workdir> && <binary> <shard manifest> > <log> 2>&1
    with MEMORY_BENCH_MODELS_JSON=<models.json>

Features
  - parameterized concurrency (--concurrency N) with launch staggering
  - per-shard log files with incremental tailing (last line shown live)
  - progress metrics parsed from driver stdout/stderr:
      [env i/N] <id> ingesting M session(s)   -> env started
      [env i/N] <id> ingested in Xms (...)    -> env finished
      [q i/N] <qid> asking / done             -> question progress
    plus on-disk import-job counting (runs/<shard>/envs/*/agent/memory/
    projects/*/jobs/import_*.json, status applied) so "import jobs flowing"
    is visible even though the driver logs nothing per session.
  - failure detection: non-zero exit codes, fatal log lines ("error:",
    "panicked at"), retry warnings, and a no-progress stall timer
  - --time-limit for dry runs: kills all children after T seconds and
    reports KILLED (exit code 0)
  - --retries: relaunch failed shards (the driver resumes: completed envs
    are skipped via env.jsonl, applied import jobs short-circuit)
  - periodic status table + final summary table + summary JSON

Examples
  # dry run (what was used to validate the launcher):
  python scripts/lmem_parallel_shard_launcher.py --shards s00,s01 \
      --concurrency 2 --time-limit 900 --log-dir <dir>/dryrun-logs

  # real 20-shard run, 6 concurrent drivers:
  python scripts/lmem_parallel_shard_launcher.py --shards all \
      --concurrency 6 --retries 1 --log-dir <dir>/fullrun-logs

Only stdlib is used. Windows: children are killed with `taskkill /F /T`
so the whole process tree dies.
"""

from __future__ import annotations

import argparse
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
DEFAULT_RUN_ROOT = BENCH_ROOT + "/reports/continuation/iteration3-inputs/runs"
DEFAULT_MODELS_JSON = (
    BENCH_ROOT
    + "/reports/continuation/c06-instruction-boundary/inputs/runs/"
    + "locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
)
SHARD_PREFIX = "lme_m_full_v1_"

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


class Shard:
    def __init__(self, name: str, manifest: str, log_path: str, run_dir: str):
        self.name = name
        self.manifest = manifest
        self.log_path = log_path
        self.run_dir = run_dir
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
        pattern = os.path.join(
            self.run_dir, "envs", "*", "agent", "memory", "projects", "*", "jobs", "import_*.json"
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


def manifest_run_dir(manifest_path: str) -> str | None:
    """Run location for a manifest: its own output_dir when declared.

    Per-env (or any non-shard) manifest sets write to the output_dir recorded
    inside the manifest, which does not follow the <run-root>/<shard> layout;
    reading it keeps import-job monitoring pointed at the real run directory.
    Returns None for unreadable/undeclaring manifests so callers fall back to
    the historical shard-layout derivation.
    """
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("output_dir") or None
    except (OSError, ValueError):
        return None


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
        f"{'SHARD':<5} {'STATE':<9} {'ELAPSED':>7} {'EXIT':>4} {'ENVS':>7} "
        f"{'JOBS':>10} {'FATAL':>5} {'RETRY':>5} {'IDLE':>6}  LAST LOG LINE",
    ]
    for sh in shards:
        elapsed = None
        if sh.started_at:
            elapsed = (sh.ended_at or now) - sh.started_at
        idle = "-"
        if sh.state == "RUNNING" and sh.last_activity:
            idle = fmt_elapsed(max(0, now - sh.last_activity))
        envs = f"{sh.envs_done}/{sh.env_total or '?'}"
        jobs = f"{sh.jobs_applied}/{sh.jobs_seen}"
        last = sh.last_line.replace("\t", " ")
        if len(last) > 110:
            last = last[:107] + "..."
        lines.append(
            f"{sh.name:<5} {sh.state:<9} {fmt_elapsed(elapsed):>7} "
            f"{str(sh.exit_code if sh.exit_code is not None else '-'):>4} {envs:>7} "
            f"{jobs:>10} {sh.fatal_lines:>5} {sh.retry_lines:>5} {idle:>6}  {last}"
        )
        if sh.launch_error:
            lines.append(f"      launch error: {sh.launch_error}")
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run LongMemEval-M shard drivers concurrently.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--shards", default="all",
                        help="comma list/range of shards (s00, s00-s04, all) or manifest file names")
    parser.add_argument("--concurrency", type=int, default=6, help="concurrent driver processes")
    parser.add_argument("--manifest-dir", default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--run-root", default=DEFAULT_RUN_ROOT,
                        help="root dir that holds runs/<shard> output dirs")
    parser.add_argument("--binary", default=DEFAULT_BINARY)
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR, help="cwd for each driver process")
    parser.add_argument("--models-json", default=DEFAULT_MODELS_JSON,
                        help="value for MEMORY_BENCH_MODELS_JSON")
    parser.add_argument("--log-dir", required=True, help="directory for per-shard logs")
    parser.add_argument("--stagger", type=float, default=3.0,
                        help="seconds between process launches")
    parser.add_argument("--status-interval", type=float, default=60.0)
    parser.add_argument("--time-limit", type=float, default=0.0,
                        help="kill everything after T seconds (dry runs); 0 = unlimited")
    parser.add_argument("--stall-minutes", type=float, default=45.0,
                        help="flag a RUNNING shard as STALLED after this many minutes "
                             "without log growth or new applied import jobs")
    parser.add_argument("--kill-stalled", action="store_true",
                        help="kill stalled shards instead of just flagging them")
    parser.add_argument("--retries", type=int, default=0,
                        help="relaunch a failed shard up to N times (driver resumes)")
    parser.add_argument("--summary-json", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the planned commands and exit without launching")
    args = parser.parse_args()

    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")

    # Resolve shards. A manifest dir without the canonical shard naming (e.g.
    # the per-env manifest set) falls back to every plain *.json except
    # _-prefixed sidecars, so --shards all drives any manifest set.
    all_files = sorted(glob.glob(os.path.join(args.manifest_dir, SHARD_PREFIX + "s*.json")))
    if not all_files:
        all_files = sorted(
            p for p in glob.glob(os.path.join(args.manifest_dir, "*.json"))
            if not os.path.basename(p).startswith("_")
        )
    by_key: dict[str, str] = {}
    for path in all_files:
        stem = os.path.splitext(os.path.basename(path))[0]
        by_key[stem] = path
        if stem.startswith(SHARD_PREFIX):
            by_key[stem[len(SHARD_PREFIX):]] = path
    tokens: list[str] = []
    for tok in args.shards.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok == "all":
            tokens.extend(
                stem[len(SHARD_PREFIX):] if stem.startswith(SHARD_PREFIX) else stem
                for stem in (os.path.splitext(os.path.basename(p))[0] for p in all_files)
            )
            continue
        range_match = re.fullmatch(r"(s\d+)-(s\d+)", tok)
        if range_match:
            start = int(range_match.group(1)[1:])
            end = int(range_match.group(2)[1:])
            tokens.extend(f"s{i:02d}" for i in range(start, end + 1))
            continue
        tokens.append(tok)
    if not tokens:
        parser.error("no shards selected")
    shards: list[Shard] = []
    for tok in tokens:
        path = by_key.get(tok) or by_key.get(SHARD_PREFIX + tok)
        if path is None:
            if tok.endswith(".json") and os.path.isfile(os.path.join(args.manifest_dir, tok)):
                path = os.path.join(args.manifest_dir, tok)
                tok = os.path.splitext(tok)[0]
            else:
                parser.error(f"unknown shard: {tok}")
        name = tok[len(SHARD_PREFIX):] if tok.startswith(SHARD_PREFIX) else tok
        log_path = os.path.join(args.log_dir, name + ".log")
        run_dir = manifest_run_dir(path) or os.path.join(args.run_root, SHARD_PREFIX + name)
        shards.append(Shard(name, path, log_path, run_dir))
    if len({sh.name for sh in shards}) != len(shards):
        parser.error("duplicate shards selected")

    os.makedirs(args.log_dir, exist_ok=True)

    log(f"launcher: {len(shards)} shard(s), concurrency={args.concurrency}")
    log(f"binary: {args.binary}")
    log(f"workdir: {args.workdir}")
    log(f"models_json: {args.models_json}")
    log(f"log_dir: {args.log_dir}")

    if args.dry_run:
        for sh in shards:
            log(f"[{sh.name}] would run: cd {args.workdir} && {args.binary} {sh.manifest} "
                f"> {sh.log_path} 2>&1  (MEMORY_BENCH_MODELS_JSON={args.models_json})")
        return 0

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
                    "run_dir": sh.run_dir,
                    "state": sh.state,
                    "exit_code": sh.exit_code,
                    "attempts": sh.attempts,
                    "started_at": sh.started_at,
                    "ended_at": sh.ended_at,
                    "log_lines": sh.lines,
                    "envs_started": sh.envs_started,
                    "envs_done": sh.envs_done,
                    "env_total": sh.env_total,
                    "questions_done": sh.q_done,
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
