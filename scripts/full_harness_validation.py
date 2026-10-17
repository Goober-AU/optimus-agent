#!/usr/bin/env python3
"""Full-harness validation runner for the optimus-agent campaign (W8).

Drives every test suite of the repository from one CLI with bounded
timeouts, per-file fresh subprocesses, isolated profile state, and
JSON artifacts under reports/full-harness/artifacts/.

Design constraints (campaign wave 1/2):
  - stdlib only; runs under Windows python and Git-Bash python;
  - one subprocess per suite (Python test files each get a fresh
    process, which avoids Windows desktop-heap exhaustion);
  - every suite gets a bounded timeout; timeouts kill the whole
    process tree (taskkill /T on Windows, process group on POSIX);
  - all writable state (profiles, sessions, harness stores, TEMP)
    lives under a per-run campaign directory; the runner never
    writes to the user's real profile, memory stores, or sessions;
  - suites whose prerequisites are missing are BLOCKED (reason
    recorded), never silently dropped; PASS/FAIL/BLOCKED/NOT
    EXECUTED are distinguished in artifacts and in --report;
  - offline by construction: no suite installed here talks to a
    network provider; the memory_bench driver is listed but stays
    BLOCKED unless MEMORY_BENCH_MODELS_JSON is explicitly provided
    (it would perform live model calls).

Usage:
  python scripts/full_harness_validation.py --list
  python scripts/full_harness_validation.py --smoke
  python scripts/full_harness_validation.py --full
  python scripts/full_harness_validation.py --performance
  python scripts/full_harness_validation.py --report
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMPAIGN = os.path.join(REPO, "reports", "full-harness")
ARTIFACTS = os.path.join(CAMPAIGN, "artifacts")
LOGS = os.path.join(ARTIFACTS, "logs")
TMP_ROOT = os.path.join(CAMPAIGN, "tmp")

SCHEMA = "full-harness-suite/1"
RUN_SCHEMA = "full-harness-run/1"

# Bounded per-kind timeouts (seconds). Scale with --timeout-scale.
DEFAULT_TIMEOUTS = {
    "cargo-lib": 3600,
    "cargo-it": 1800,
    "cargo-example": 3600,
    "py-root": 900,
    "py-rt": 1200,
    "py-bench": 1800,
    "py-pytest": 900,
    "node": 300,
    "layout": 120,
    "check-sh": 3600,
    "parity": 900,
}

RUST_CRATES = ["pi-ai", "pi-agent-core", "pi-jev", "pi-tui", "pi-coding-agent"]

# Fast, build-free suites used by --smoke.
SMOKE_PY_ROOT = {"test_jev_launcher_env.py", "test_standalone_install.py"}
SMOKE_PY_RT = {"test_agent_message_skill.py", "test_observation_aliases.py", "test_collect.py"}

# Files that are pytest-style: plain-assert classes/functions that
# `unittest discover` never executes (documented campaign gap).
PYTEST_STYLE_ROOT_FILES = {"test_memory_question_loop.py"}

TAIL_CHARS = 8000


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)


def find_tool(name: str, extra_paths=()):
    """Resolve an executable: PATH first, then documented fallbacks."""
    found = shutil.which(name)
    if found:
        return found
    for path in extra_paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _python_has_dill(python_path: str) -> bool:
    """Light probe: can this interpreter import dill (kernel snapshot dep)?"""
    try:
        probe = subprocess.run(
            [python_path, "-c", "import dill"],
            capture_output=True, timeout=30,
        )
        return probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _resolve_kernel_python():
    """Pick the prepared kernel Python for kernel-spawning Rust tests.

    Priority: --kernel-python flag, KERNEL_CONTAINMENT_TEST_PYTHON,
    the repo's own locked runtime venv (prime-agent-runtime/.venv, dev
    group ships dill), then the ambient PRIME_AGENT_KERNEL_PYTHON. The
    candidate must exist and import dill; otherwise kernel suites stay
    BLOCKED with an explicit reason instead of panicking mid-test.
    """
    candidates = []
    if ARGS is not None and getattr(ARGS, "kernel_python", None):
        candidates.append(("flag --kernel-python", ARGS.kernel_python))
    env_campaign = os.environ.get("KERNEL_CONTAINMENT_TEST_PYTHON")
    if env_campaign:
        candidates.append(("KERNEL_CONTAINMENT_TEST_PYTHON", env_campaign))
    runtime_venv = (
        os.path.join(REPO, "prime-agent-runtime", ".venv", "Scripts", "python.exe")
        if os.name == "nt"
        else os.path.join(REPO, "prime-agent-runtime", ".venv", "bin", "python")
    )
    candidates.append(("prime-agent-runtime/.venv", runtime_venv))
    ambient = os.environ.get("PRIME_AGENT_KERNEL_PYTHON")
    if ambient:
        candidates.append(("ambient PRIME_AGENT_KERNEL_PYTHON", ambient))
    for source, path in candidates:
        if path and os.path.isfile(path) and _python_has_dill(path):
            return path
    return None


def _home_candidates():
    """User-home candidates.

    An orchestrating agent session often redirects HOME/USERPROFILE to a
    private profile, which hides the real per-user tool install locations
    (uv at <real-home>/.local/bin). Probe the redirect first, then derive
    the real profile from APPDATA, then the documented campaign machine
    path.
    """
    homes = []
    expanded = os.path.expanduser("~")
    if expanded:
        homes.append(expanded)
    appdata = os.environ.get("APPDATA")
    if appdata:
        derived = os.path.dirname(os.path.dirname(os.path.abspath(appdata)))
        homes.append(derived)
    homes.append(r"C:\Users\openclawuser")
    seen, ordered = set(), []
    for home in homes:
        key = os.path.normcase(os.path.abspath(home))
        if key not in seen:
            seen.add(key)
            ordered.append(home)
    return ordered


def resolve_tools() -> dict:
    """Read-only prerequisite probe. Never executes anything heavy."""
    cargo_paths = [os.environ.get("FULL_HARNESS_CARGO")]
    rustc_paths = [os.environ.get("FULL_HARNESS_RUSTC")]
    uv_paths = [os.environ.get("FULL_HARNESS_UV")]
    for home in _home_candidates():
        cargo_paths.append(os.path.join(home, ".cargo", "bin", "cargo.exe"))
        rustc_paths.append(os.path.join(home, ".cargo", "bin", "rustc.exe"))
        uv_paths.append(os.path.join(home, ".local", "bin", "uv.exe"))
    cargo_paths.append(r"C:\Users\openclawuser\optimus-rust-toolchain\cargo\bin\cargo.exe")
    rustc_paths.append(r"C:\Users\openclawuser\optimus-rust-toolchain\cargo\bin\rustc.exe")
    cargo = find_tool("cargo", tuple(cargo_paths))
    rustc = find_tool("rustc", tuple(rustc_paths))
    uv = find_tool("uv", tuple(uv_paths))
    node = find_tool("node")
    bash = find_tool("bash")
    git = find_tool("git")
    pytest = find_tool("pytest")
    clang = find_tool("clang") or find_tool("clang-repl") or find_tool("clang-17")
    clang_repl = os.environ.get("OPTIMUS_CLANG_REPL") or clang

    kernel_python = _resolve_kernel_python()

    bench_venv_python = None
    for candidate in (
        os.path.join(REPO, "bench", ".venv", "Scripts", "python.exe"),
        os.path.join(REPO, "bench", ".venv", "bin", "python"),
    ):
        if os.path.isfile(candidate):
            bench_venv_python = candidate
            break

    port_env_python = None
    for candidate in (
        os.path.join(REPO, ".port-env", "python", "Scripts", "python.exe"),
        os.path.join(REPO, ".port-env", "python", "bin", "python"),
    ):
        if os.path.isfile(candidate):
            port_env_python = candidate
            break

    models_json = os.environ.get("MEMORY_BENCH_MODELS_JSON")
    if models_json and not os.path.isfile(models_json):
        models_json = None
    bootstrap_tools = os.environ.get("PARITY_BOOTSTRAP_TOOLS_DIR")
    if bootstrap_tools and not os.path.isdir(bootstrap_tools):
        bootstrap_tools = None

    return {
        "python": sys.executable,
        "cargo": cargo,
        "rustc": rustc,
        "uv": uv,
        "node": node,
        "bash": bash,
        "git": git,
        "pytest": pytest,
        "clang": clang_repl,
        "kernel_python": kernel_python,
        "bench_venv_python": bench_venv_python,
        "port_env_python": port_env_python,
        "memory_bench_models_json": models_json,
        "parity_bootstrap_tools_dir": bootstrap_tools,
    }


# ---------------------------------------------------------------------------
# Suite planning
# ---------------------------------------------------------------------------


def list_rust_integration_files(crate: str):
    directory = os.path.join(REPO, "crates", crate, "tests")
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.endswith(".rs"))


def list_python_files(directory: str):
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.startswith("test_") and f.endswith(".py"))


def list_node_files():
    directory = os.path.join(REPO, "tests")
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.endswith(".test.mjs"))


def build_suites(tools: dict, args) -> list:
    """Plan every suite. Pure function of the repo tree, tools, and flags."""
    suites = []

    def add(sid, kind, command, modes, prereq=None, reason=None, note=None, needs_ignored=False,
            requires=None, cwd=REPO, env_extra=None):
        suites.append(
            {
                "id": sid,
                "kind": kind,
                "modes": modes,
                "command": command,
                "cwd": cwd,
                "prereq": prereq or [],
                "reason": reason,
                "note": note,
                "needs_ignored": needs_ignored,
                "requires": requires or [],
                "env_extra": env_extra or {},
            }
        )

    cargo = tools["cargo"]
    rustc = tools["rustc"]

    # --- Rust unit (lib) suites -------------------------------------------
    for crate in RUST_CRATES:
        add(
            f"cargo-lib-{crate}",
            "cargo-lib",
            [cargo, "test", "--locked", "-p", crate, "--lib", "--", "--test-threads=1"],
            ["full"],
            prereq=["cargo", "rustc"],
            note="workspace unit tests via test.sh lane; first run pays build cost",
        )

    # --- Rust integration suites (one cargo target per file) ---------------
    special = {
        "bootstrap_t01.rs": ("parity_bootstrap_tools_dir", "PARITY_BOOTSTRAP_TOOLS_DIR not set; build fixtures with tests/bootstrap_support/build-fixtures.cmd"),
        "bootstrap_t02.rs": ("parity_bootstrap_tools_dir", "PARITY_BOOTSTRAP_TOOLS_DIR not set; build fixtures with tests/bootstrap_support/build-fixtures.cmd"),
        "kernel_metrics_legacy_export.rs": ("kernel_python", "PRIME_AGENT_KERNEL_PYTHON not provided"),
    }
    for crate in RUST_CRATES:
        for fname in list_rust_integration_files(crate):
            stem = fname[:-3]
            prereq = ["cargo", "rustc"]
            note = None
            needs_ignored = False
            env_extra = {}
            if fname in special:
                key, why = special[fname]
                prereq.append(key)
                note = why
            if fname == "live_systemone.rs":
                needs_ignored = True
                note = "live network probe; #[ignore] by default; needs TYPESAFE_API_KEY + --with-ignored"
            if fname in ("execution_mode.rs", "native_images.rs"):
                note = "contains #[ignore] tests needing PRIME_AGENT_KERNEL_PYTHON or OPTIMUS_CLANG_REPL; run with --with-ignored to include"
            add(
                f"cargo-it-{crate}-{stem}",
                "cargo-it",
                [cargo, "test", "--locked", "-p", crate, "--test", stem, "--", "--test-threads=1"],
                ["full"],
                prereq=prereq,
                note=note,
                needs_ignored=needs_ignored,
                env_extra=env_extra,
            )

    # --- Repository-root Python suites (fresh process per file) -----------
    for fname in list_python_files(os.path.join(REPO, "tests")):
        stem = fname[:-3]
        if fname in PYTEST_STYLE_ROOT_FILES:
            # pytest-style: recover via pytest when available.
            add(
                f"py-pytest-{stem}",
                "py-pytest",
                [tools["pytest"], "-q", os.path.join("tests", fname)],
                ["full"],
                prereq=["pytest"],
                note="pytest-style file; `unittest discover` runs 0 tests from it (campaign finding W8-1)",
            )
            continue
        modes = ["full"]
        if fname in SMOKE_PY_ROOT:
            modes = ["smoke", "full"]
        if fname.startswith("test_memory_bench"):
            modes = ["full", "performance"]
        add(
            f"py-root-{stem}",
            "py-root",
            [sys.executable, "-m", "unittest", f"tests.{stem}"],
            modes,
        )

    # --- Kernel runtime suites (uv project, per-file fresh process) --------
    for fname in list_python_files(os.path.join(REPO, "prime-agent-runtime", "test")):
        stem = fname[:-3]
        modes = ["full"]
        if fname in SMOKE_PY_RT:
            modes = ["smoke", "full"]
        add(
            f"py-rt-{stem}",
            "py-rt",
            [
                tools["uv"], "run", "--locked", "--project", "prime-agent-runtime",
                "python", "-m", "unittest", "discover",
                "-s", "prime-agent-runtime/test", "-p", fname,
            ],
            modes,
            prereq=["uv"],
            note="PYTHONPATH cleared: an installed runtime shadows repo src (BASELINE.md)",
        )

    # --- Node suites -------------------------------------------------------
    for fname in list_node_files():
        add(
            f"node-{fname[:-9].replace('.', '-')}",
            "node",
            [tools["node"], "--test", os.path.join("tests", fname)],
            ["smoke", "full"],
            prereq=["node"],
        )

    # --- Layout / repo gates -------------------------------------------------
    add(
        "layout-check-rust-layout",
        "layout",
        [sys.executable, "scripts/check-rust-layout.py"],
        ["smoke", "full"],
        prereq=["git"],
        note="packaging/layout gate from scripts/check.sh",
    )
    add(
        "check-sh",
        "check-sh",
        [tools["bash"], "scripts/check.sh"],
        ["full"],
        prereq=["bash", "cargo", "rustc", "git"],
        note="full repo gate: whitespace, cargo check --workspace --all-targets, packaging tests",
    )

    # --- Bench suites (isolated bench venv, offline) -------------------------
    bench_py = tools["bench_venv_python"]
    add(
        "py-bench-portability",
        "py-bench",
        [bench_py, "-B", "-W", "error", "-m", "unittest", "discover",
         "-s", "tests", "-p", "test_memory_bench_portability.py"],
        ["full", "performance"],
        prereq=["bench_venv_python"],
        reason=None if bench_py else "bench/.venv python missing; create with: python -m venv --without-pip bench/.venv (or pass --ensure-bench-venv)",
        note="bench README documented invocation; 124 tests incl. imported scoring suite",
    )
    add(
        "py-bench-unit",
        "py-bench",
        [bench_py, "-B", "-W", "error", "-m", "unittest", "discover",
         "-s", "bench/tests", "-p", "test_*.py"],
        ["performance"],
        prereq=["bench_venv_python"],
        reason=None if bench_py else "bench/.venv python missing; create with: python -m venv --without-pip bench/.venv (or pass --ensure-bench-venv)",
        note="bench/tests: 74 local-scoring + 11 pmem-converter tests",
    )

    # --- Parity suites --------------------------------------------------------
    if tools["pytest"] and tools["port_env_python"]:
        add(
            "parity-kernel-client",
            "parity",
            [tools["pytest"], "-q", os.path.join("tests", "parity", "test_kernel_client.py")],
            ["full"],
            prereq=["pytest", "port_env_python"],
            note="private .port-env python; pytest-style (unittest discovery never runs it)",
        )
    else:
        add(
            "parity-kernel-client",
            "parity",
            [tools["pytest"] or "pytest", "-q", os.path.join("tests", "parity", "test_kernel_client.py")],
            ["full"],
            prereq=["pytest", "port_env_python"],
            reason="requires pytest and the private .port-env/python interpreter (scripts/port-isolation.ps1)",
        )
    add(
        "parity-differential-basic",
        "parity",
        [sys.executable, os.path.join("tests", "parity", "differential.py"),
         "--cases", os.path.join("tests", "parity", "cases", "basic.json")],
        ["full"],
        prereq=["port_env_python"],
        reason=None if tools["port_env_python"] else "requires the private .port-env/python interpreter and the TypeScript reference",
    )

    # --- Performance examples --------------------------------------------------
    add(
        "perf-mem-search-bench",
        "cargo-example",
        [cargo, "run", "--locked", "-p", "pi-coding-agent",
         "--example", "mem_search_bench", "--release", "--", "PERF_OUT_MEM_SEARCH"],
        ["performance"],
        prereq=["cargo", "rustc"],
        note="deterministic LLM-free lexical search bench; entry counts from --perf-mem-sizes",
    )
    gen_out = "PERF_OUT_COMPACTION_FIXTURES"
    add(
        "perf-compaction-replay-gen",
        "cargo-example",
        [cargo, "run", "--locked", "-p", "pi-coding-agent",
         "--example", "compaction_replay", "--release", "--", "gen-fixtures", "--out", gen_out],
        ["performance"],
        prereq=["cargo", "rustc"],
        note="deterministic compaction fixture generation (offline)",
    )
    add(
        "perf-compaction-replay",
        "cargo-example",
        [cargo, "run", "--locked", "-p", "pi-coding-agent",
         "--example", "compaction_replay", "--release", "--",
         "replay", "--fixture", gen_out, "--out", "PERF_OUT_COMPACTION_RESULTS"],
        ["performance"],
        prereq=["cargo", "rustc"],
        requires=["perf-compaction-replay-gen"],
        note="replays the real auto-compaction path offline against generated fixtures",
    )
    add(
        "perf-disk-write-bench",
        "cargo-example",
        [cargo, "run", "--locked", "-p", "pi-coding-agent",
         "--example", "disk_write_bench", "--release", "--"],
        ["performance"],
        prereq=["cargo", "rustc"],
        note="atomic-write durability bench on real code paths",
    )
    add(
        "perf-session-loop-bench",
        "cargo-example",
        [cargo, "run", "--locked", "-p", "pi-coding-agent",
         "--example", "session_loop_bench", "--release", "--", "run", "--out", "PERF_OUT_SESSION_LOOP"],
        ["performance"],
        prereq=["cargo", "rustc"],
        note="offline faux-provider agent-session loop bench",
    )
    add(
        "perf-memory-bench-driver",
        "cargo-example",
        [cargo, "run", "--locked", "-p", "pi-coding-agent",
         "--example", "memory_bench", "--release", "--", "PERF_MANIFEST"],
        ["performance"],
        prereq=["cargo", "rustc", "memory_bench_models_json"],
        reason="live-model driver: needs MEMORY_BENCH_MODELS_JSON (DGX GLM-5.3) and network authorization; BLOCKED in offline campaign",
        note="H2 memory benchmark driver; never run in offline validation",
    )
    return suites


def expand_perf_placeholders(command, state_dir, args, manifest=None):
    """Substitute PERF_* placeholders with run-scoped output paths."""
    resolved = []
    for part in command:
        if part == "PERF_OUT_MEM_SEARCH":
            part = os.path.join(state_dir, "perf", "mem_search")
        elif part == "PERF_OUT_COMPACTION_FIXTURES":
            part = os.path.join(state_dir, "perf", "compaction-fixtures")
        elif part == "PERF_OUT_COMPACTION_RESULTS":
            part = os.path.join(state_dir, "perf", "compaction-replay.json")
        elif part == "PERF_OUT_SESSION_LOOP":
            part = os.path.join(state_dir, "perf", "session-loop.json")
        elif part == "PERF_MANIFEST":
            part = manifest or os.path.join(state_dir, "perf", "MEMORY-BENCH-MANIFEST.json")
        if isinstance(part, str) and part.startswith(os.path.join(state_dir, "perf")):
            os.makedirs(os.path.dirname(part), exist_ok=True)
        resolved.append(part)
    if resolved and resolved[0:1] and "mem_search_bench" in " ".join(resolved):
        sizes = [s.strip() for s in re.split(r"[,\s]+", args.perf_mem_sizes) if s.strip()]
        resolved.extend(sizes)
    return resolved


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def kill_process_tree(process) -> None:
    """Best-effort termination of a whole process tree."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            process.kill()
        except OSError:
            pass


def build_isolated_env(state_dir: str, tools: dict) -> dict:
    """Environment for test subprocesses: campaign-isolated, PYTHONPATH-free.

    HOME/USERPROFILE are intentionally kept: uv and cargo need their caches,
    and the profile override (PRIME_AGENT_CODING_AGENT_DIR) already redirects
    every agent-path resolution away from the real profile.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    agent_dir = os.path.join(state_dir, "agent")
    sessions_dir = os.path.join(state_dir, "sessions")
    harness_dir = os.path.join(state_dir, "harness")
    tmp_dir = os.path.join(state_dir, "tmp")
    for directory in (agent_dir, sessions_dir, harness_dir, tmp_dir):
        os.makedirs(directory, exist_ok=True)
    env["PRIME_AGENT_CODING_AGENT_DIR"] = agent_dir
    env["PI_CODING_AGENT_DIR"] = agent_dir
    env["PRIME_AGENT_SESSION_DIR"] = sessions_dir
    env["PRIME_AGENT_CODING_AGENT_SESSION_DIR"] = sessions_dir
    env["RLM_SESSION_DIR"] = sessions_dir
    env["RLM_HARNESS_STATE_DIR"] = harness_dir
    env["RLM_GLOBAL_HARNESS_STATE_DIR"] = os.path.join(state_dir, "harness-global")
    os.makedirs(env["RLM_GLOBAL_HARNESS_STATE_DIR"], exist_ok=True)
    env["TEMP"] = tmp_dir
    env["TMP"] = tmp_dir
    env["TMPDIR"] = tmp_dir
    env["PRIME_AGENT_TELEMETRY"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if tools.get("kernel_python"):
        env["PRIME_AGENT_KERNEL_PYTHON"] = tools["kernel_python"]
    else:
        # Never let suites inherit the orchestrating agent's live kernel.
        env.pop("PRIME_AGENT_KERNEL_PYTHON", None)
    return env


def parse_unittest_output(stdout: str, stderr: str):
    combined = stdout + "\n" + stderr
    ran = None
    match = re.search(r"^Ran (\d+) tests?", combined, re.M)
    if match:
        ran = int(match.group(1))
    ok = bool(re.search(r"^OK(?:\s|$)", combined, re.M))
    failed = bool(re.search(r"^FAILED(?:\s|\()", combined, re.M))
    import_error = "ModuleNotFoundError" in combined or "ImportError" in combined
    return {"tests_ran": ran, "ok": ok, "failed": failed, "import_error": import_error}


def parse_cargo_output(stdout: str, stderr: str):
    combined = stdout + "\n" + stderr
    passed = failed = ignored = 0
    for match in re.finditer(r"test result: (ok|FAILED)\. (\d+) passed; (\d+) failed", combined):
        passed += int(match.group(2))
        failed += int(match.group(3))
    for match in re.finditer(r"; (\d+) ignored", combined):
        ignored += int(match.group(1))
    zero_tests = re.search(r"0 passed", combined) is not None and "test result:" in combined
    return {"tests_ran": passed + failed, "tests_passed": passed, "tests_failed": failed,
            "tests_ignored": ignored, "zero_tests": zero_tests}


def run_suite(suite, tools, state_dir, timeout_scale, with_ignored):
    record = {
        "schema": SCHEMA,
        "id": suite["id"],
        "kind": suite["kind"],
        "modes": suite["modes"],
        "command": None,
        "cwd": suite["cwd"],
        "status": "NOT EXECUTED",
        "reason": None,
        "exit_code": None,
        "duration_s": None,
        "timeout_s": None,
        "tests_ran": None,
        "zero_tests": False,
        "import_error": False,
        "stdout_tail": "",
        "stderr_tail": "",
        "stdout_log": None,
        "stderr_log": None,
        "started_at": None,
        "ended_at": None,
    }

    if suite["needs_ignored"] and not with_ignored:
        record["reason"] = "suite contains #[ignore]d live/optional tests; pass --with-ignored to include"
        return record

    missing = [key for key in suite["prereq"] if not tools.get(key)]
    if missing:
        record["reason"] = f"missing prerequisites: {', '.join(missing)}"
        record["status"] = "BLOCKED"
        if suite.get("reason"):
            record["reason"] += f" ({suite['reason']})"
        return record

    command = expand_perf_placeholders(suite["command"], state_dir, ARGS, None) if "PERF_" in " ".join(suite["command"]) else list(suite["command"])
    record["command"] = command
    timeout = int(DEFAULT_TIMEOUTS.get(suite["kind"], 900) * timeout_scale)
    record["timeout_s"] = timeout

    env = build_isolated_env(state_dir, tools)
    env.update(suite.get("env_extra") or {})

    os.makedirs(LOGS, exist_ok=True)
    out_path = os.path.join(LOGS, f"{suite['id']}.out")
    err_path = os.path.join(LOGS, f"{suite['id']}.err")

    record["started_at"] = now_iso()
    started = time.monotonic()
    log(f"RUN {suite['id']} ({suite['kind']})")
    try:
        process = subprocess.Popen(
            command,
            cwd=suite["cwd"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
    except OSError as error:
        record["status"] = "BLOCKED"
        record["reason"] = f"cannot start: {error}"
        record["ended_at"] = now_iso()
        return record

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(process)
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=30)
        except Exception:
            stdout_bytes, stderr_bytes = b"", b""
    record["duration_s"] = round(time.monotonic() - started, 1)
    record["ended_at"] = now_iso()
    stdout = (stdout_bytes or b"").decode("utf-8", "replace")
    stderr = (stderr_bytes or b"").decode("utf-8", "replace")
    with open(out_path, "w", encoding="utf-8", errors="replace") as handle:
        handle.write(stdout)
    with open(err_path, "w", encoding="utf-8", errors="replace") as handle:
        handle.write(stderr)
    record["stdout_log"] = os.path.relpath(out_path, REPO).replace("\\", "/")
    record["stderr_log"] = os.path.relpath(err_path, REPO).replace("\\", "/")
    record["stdout_tail"] = stdout[-TAIL_CHARS:]
    record["stderr_tail"] = stderr[-TAIL_CHARS:]
    record["exit_code"] = process.returncode

    if timed_out:
        record["status"] = "FAIL"
        record["reason"] = f"timeout after {timeout}s (process tree killed)"
        return record

    if suite["kind"].startswith("py-"):
        parsed = parse_unittest_output(stdout, stderr)
        record["tests_ran"] = parsed["tests_ran"]
        record["import_error"] = parsed["import_error"]
        record["zero_tests"] = parsed["tests_ran"] == 0
    elif suite["kind"].startswith("cargo"):
        parsed = parse_cargo_output(stdout, stderr)
        record["tests_ran"] = parsed["tests_ran"]
        record["zero_tests"] = parsed["zero_tests"] and parsed["tests_ran"] == 0

    if process.returncode == 0:
        record["status"] = "PASS"
    else:
        record["status"] = "FAIL"
        record["reason"] = f"exit code {process.returncode}"
    return record


def write_artifact(record) -> str:
    os.makedirs(ARTIFACTS, exist_ok=True)
    path = os.path.join(ARTIFACTS, f"{record['id']}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
    return path


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def load_artifacts():
    if not os.path.isdir(ARTIFACTS):
        return []
    records = []
    for fname in sorted(os.listdir(ARTIFACTS)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(ARTIFACTS, fname)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            records.append(data)
    return records


def git_info():
    info = {"branch": None, "commit": None}
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=30,
        )
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=30,
        )
        info["branch"] = branch.stdout.strip() or None
        info["commit"] = commit.stdout.strip() or None
    except Exception:
        pass
    return info


def write_report(mode_label):
    records = load_artifacts()
    if not records:
        print("No suite artifacts found under", ARTIFACTS)
        return 0
    info = git_info()
    counts = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    lines = []
    lines.append("# Full-Harness Validation Report")
    lines.append("")
    lines.append(f"Generated: {now_iso()}")
    lines.append(f"Repo: `{REPO}`")
    if info["branch"]:
        lines.append(f"Branch: `{info['branch']}`")
    if info["commit"]:
        lines.append(f"Commit: `{info['commit']}`")
    lines.append(f"Suites: {len(records)} (aggregated from artifacts/*.json)")
    lines.append("")
    lines.append("## Status summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    for status in ("PASS", "FAIL", "BLOCKED", "NOT EXECUTED"):
        lines.append(f"| {status} | {counts.get(status, 0)} |")
    lines.append("")
    lines.append("## Suites")
    lines.append("")
    lines.append("| Suite | Kind | Status | Exit | Secs | Tests | Reason / note |")
    lines.append("|---|---|---|---|---|---|---|")
    for record in sorted(records, key=lambda r: (r["kind"], r["id"])):
        tests = record.get("tests_ran")
        tests_text = "" if tests is None else str(tests)
        if record.get("zero_tests"):
            tests_text = "0 (WARNING)"
        reason = record.get("reason") or record.get("note") or ""
        reason = reason.replace("|", "/").replace("\n", " ")
        lines.append(
            f"| `{record['id']}` | {record['kind']} | {record['status']} | "
            f"{'' if record.get('exit_code') is None else record['exit_code']} | "
            f"{'' if record.get('duration_s') is None else record['duration_s']} | "
            f"{tests_text} | {reason} |"
        )
    lines.append("")
    zero = [r for r in records if r.get("zero_tests")]
    if zero:
        lines.append("## Zero-test warnings (suite passed but discovered 0 tests)")
        lines.append("")
        for record in zero:
            lines.append(f"- `{record['id']}`: 0 tests discovered; see {record.get('stdout_log')}")
        lines.append("")
    os.makedirs(CAMPAIGN, exist_ok=True)
    path = os.path.join(CAMPAIGN, "REPORT.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"Wrote {path} ({len(records)} suites: " +
          ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) + ")")
    return 1 if counts.get("FAIL", 0) else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def print_list(suites, tools, mode_label):
    print(f"full_harness_validation — planned suites ({mode_label})")
    print(f"repo: {REPO}")
    print("")
    print("Prerequisite resolution (read-only):")
    for key in ("python", "cargo", "rustc", "uv", "node", "bash", "git", "pytest",
                "clang", "kernel_python", "bench_venv_python", "port_env_python",
                "memory_bench_models_json", "parity_bootstrap_tools_dir"):
        value = tools.get(key)
        print(f"  {key:28s} {'OK  ' if value else 'MISS'} {value if value else ''}")
    print("")
    selected = [s for s in suites if mode_label in s["modes"] or mode_label == "all"]
    print(f"Planned suites for mode '{mode_label}': {len(selected)}")
    print("")
    print(f"{'Suite id':60s} {'Kind':14s} {'Modes':28s} {'T/O':>6s}  Command")
    print("-" * 150)
    for suite in selected:
        timeout = DEFAULT_TIMEOUTS.get(suite["kind"], 900)
        command = " ".join(str(part) for part in suite["command"])
        if len(command) > 80:
            command = command[:77] + "..."
        modes = "+".join(suite["modes"])
        print(f"{suite['id']:60s} {suite['kind']:14s} {modes:28s} {timeout:>6d}  {command}")
        if suite.get("note"):
            print(f"{'':60s} note: {suite['note']}")
        if suite.get("reason"):
            print(f"{'':60s} prereq-note: {suite['reason']}")
    print("")
    print("Safe dry run: nothing was executed. Suites marked with missing")
    print("prerequisites will be reported BLOCKED when a run mode is selected.")
    return 0


def ensure_bench_venv():
    """Create the offline bench venv if missing (no pip, no network)."""
    venv_dir = os.path.join(REPO, "bench", ".venv")
    if os.path.isdir(venv_dir):
        return True
    if not ARGS.ensure_bench_venv:
        return False
    log("creating offline bench venv (python -m venv --without-pip bench/.venv)")
    result = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", venv_dir],
        cwd=REPO, capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        log("bench venv creation failed: " + (result.stderr or "").strip()[:400])
        return False
    return True


ARGS = None


def main(argv=None):
    global ARGS
    parser = argparse.ArgumentParser(
        prog="full_harness_validation.py",
        description=(
            "Campaign test-suite runner with prerequisite checks, bounded "
            "timeouts, per-file fresh processes, and isolated profile state. "
            "Default action (no flags) is --list, a safe dry run."
        ),
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--smoke", action="store_true", help="fast build-free suites (python/node/layout)")
    modes.add_argument("--full", action="store_true", help="every suite including cargo build+test lanes")
    modes.add_argument("--performance", action="store_true", help="bench suites and offline performance examples")
    modes.add_argument("--report", action="store_true", help="aggregate artifacts/*.json into reports/full-harness/REPORT.md")
    modes.add_argument("--list", action="store_true", help="print the planned suites and exit (safe dry run)")
    parser.add_argument("--suite", action="append", default=None,
                        help="restrict to suite ids (repeatable; glob patterns allowed)")
    parser.add_argument("--timeout-scale", type=float, default=1.0,
                        help="multiply every per-kind timeout (default 1.0)")
    parser.add_argument("--with-ignored", action="store_true",
                        help="also plan suites whose tests are #[ignore]d (live/optional)")
    parser.add_argument("--ensure-bench-venv", action="store_true",
                        help="create bench/.venv with `python -m venv --without-pip` when missing (offline)")
    parser.add_argument("--keep-tmp", action="store_true",
                        help="keep the per-run temp state directory for diagnosis")
    parser.add_argument("--perf-mem-sizes", default="1000,5000",
                        help="entry counts for perf-mem-search-bench (default 1000,5000)")
    parser.add_argument("--kernel-python", default=None,
                        help="explicit prepared Python (with dill) for kernel-spawning tests; "
                             "overrides KERNEL_CONTAINMENT_TEST_PYTHON detection")
    args = parser.parse_args(argv)
    ARGS = args

    if not any([args.smoke, args.full, args.performance, args.report, args.list]):
        args.list = True

    tools = resolve_tools()
    suites = build_suites(tools, args)

    if args.list:
        return print_list(suites, tools, "all")

    if args.report:
        return write_report("report")

    if args.smoke:
        mode_label = "smoke"
    elif args.performance:
        mode_label = "performance"
    else:
        mode_label = "full"

    if args.suite:
        import fnmatch as _fnmatch
        patterns = args.suite
        suites = [
            s for s in suites
            if any(_fnmatch.fnmatch(s["id"], pattern) for pattern in patterns)
        ]
        if not suites:
            print("No suites match --suite filters:", patterns)
            return 2

    selected = [s for s in suites if mode_label in s["modes"]]
    if not selected:
        print(f"No suites planned for mode '{mode_label}'.")
        return 2

    if mode_label in ("full", "performance") and "cargo" in {p for s in selected for p in s["prereq"]}:
        if tools["cargo"] is None or tools["rustc"] is None:
            log("WARNING: cargo/rustc not found; cargo suites will be BLOCKED")

    if not ensure_bench_venv():
        if any(s["kind"] == "py-bench" for s in selected):
            log("NOTE: bench venv missing; py-bench suites run BLOCKED (see --ensure-bench-venv)")
        tools = resolve_tools()

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    state_dir = os.path.join(TMP_ROOT, f"run-{stamp}")
    os.makedirs(state_dir, exist_ok=True)
    log(f"mode={mode_label} suites={len(selected)} state={state_dir}")

    results = []
    completed = {}
    for suite in selected:
        if suite["requires"]:
            unmet = [dep for dep in suite["requires"] if completed.get(dep) != "PASS"]
            if unmet:
                record = {
                    "schema": SCHEMA, "id": suite["id"], "kind": suite["kind"],
                    "modes": suite["modes"], "command": None, "cwd": suite["cwd"],
                    "status": "NOT EXECUTED", "note": suite.get("note"),
                    "reason": f"required suites did not pass: {', '.join(unmet)}",
                    "exit_code": None, "duration_s": None, "timeout_s": None,
                    "tests_ran": None, "zero_tests": False, "import_error": False,
                    "stdout_tail": "", "stderr_tail": "", "stdout_log": None,
                    "stderr_log": None, "started_at": None, "ended_at": None,
                }
                write_artifact(record)
                results.append(record)
                completed[suite["id"]] = record["status"]
                continue
        record = run_suite(suite, tools, state_dir, args.timeout_scale, args.with_ignored)
        write_artifact(record)
        results.append(record)
        completed[suite["id"]] = record["status"]
        log(f"DONE {suite['id']}: {record['status']}"
            + (f" ({record['reason']})" if record.get("reason") else ""))

    run_record = {
        "schema": RUN_SCHEMA,
        "mode": mode_label,
        "started": stamp,
        "ended": now_iso(),
        "state_dir": state_dir,
        "tools": {k: (v if isinstance(v, (int, float, str, bool)) else str(v)) for k, v in tools.items()},
        "timeout_scale": args.timeout_scale,
        "with_ignored": args.with_ignored,
        "results": {r["id"]: r["status"] for r in results},
    }
    os.makedirs(ARTIFACTS, exist_ok=True)
    with open(os.path.join(ARTIFACTS, f"run-{stamp}.json"), "w", encoding="utf-8") as handle:
        json.dump(run_record, handle, indent=2)

    counts = {}
    for record in results:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    print("")
    print("Run summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if not args.keep_tmp:
        try:
            shutil.rmtree(state_dir, ignore_errors=True)
            log(f"cleaned run state {state_dir}")
        except OSError as error:
            log(f"could not clean run state: {error}")
    else:
        log(f"kept run state {state_dir} (--keep-tmp)")

    write_report(mode_label)
    return 1 if counts.get("FAIL", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
