"""W26 live-model practice-run driver (CUSTOM-OPTIMUS + LIVE-MODEL labels).

Runs the W12 arm grid through the stock CompactBench runner
(``run_experiment``; the CLI is a thin wrapper over exactly these RunArgs)
with GLM 5.3 as BOTH the task/eval model (this process, via
``glm_provider.GlmLiveProvider``) and the summarizer (the Rust
compaction-harness binary, via ``optimus_compactor_live``).

Ruling (parent, 2026-10-19): Option B - 4 custom arms x starter suite slots
0-1 (``--case-count 2`` selects the deterministic W12 seed prefix: slots 0
and 1 of the same ``derive_case_seed`` namespace), 3 drift cycles, 512
planned logical calls (448 task-model + 64 summarizer), no elite, no
built-in baselines live. Amendment-1: if exactly the two Rust arms' (or
another conclusion-relevant pair's) aggregate quality finishes within 0.05
AND the ranking changes the report's conclusion, extend those two arms to
slots 2-3 (total <= 768 HTTP requests). Amendment-2: every HTTP request -
length-ladder rungs included - is counted and logged. Amendment-3: PONG
before each arm; 429/5xx backoff max 3 per call; pause and report if >10%
of calls back off.

Usage (cb-venv python, from the worktree root, with ROT2_LIVE_* exported):
    python research/context_rot_r2/compactbench_adapter/live/run_live.py --phase smoke
    python research/context_rot_r2/compactbench_adapter/live/run_live.py --phase suite
    python research/context_rot_r2/compactbench_adapter/live/run_live.py --phase amend1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

from glm_provider import GlmLiveProvider  # noqa: E402
import compactbench.providers as _cbp  # noqa: E402
from compactbench.dsl import DifficultyLevel  # noqa: E402
from compactbench.runner.run import RunArgs, run_experiment  # noqa: E402
from compactbench.runner.persistence import to_run_result  # noqa: E402

# The stock runner resolves the provider key through this registry; the
# shared checkout is read-only for this worker, so the custom live provider
# is registered in-process instead of editing providers/__init__.py.
_cbp._REGISTRY[GlmLiveProvider.key] = GlmLiveProvider

WORKTREE = HERE.parents[3]
ADAPTER_DIR = HERE.parent
# The CompactBench checkout is machine-local, so the benchmarks directory
# comes from the environment only (ROT2_COMPACTBENCH_BENCHMARKS, checked in
# main): point it at `<compactbench-checkout>/benchmarks/public`.
BENCHMARKS_ENV = "ROT2_COMPACTBENCH_BENCHMARKS"
BENCHMARKS = Path(os.environ.get(BENCHMARKS_ENV, ""))
RESULTS = HERE / "results"
RAW = HERE / "raw"

ARMS: list[tuple[str, str, bool]] = [
    # (slug, compactor class, uses the harness/summarizer)
    ("optimus-rust-task-state", "OptimusRustCompactorTaskState", True),
    ("optimus-rust-plain", "OptimusRustCompactorPlain", True),
    ("echo-first-n", "EchoFirstNCompactor", False),
    ("full-transcript-oracle", "FullTranscriptOracleCompactor", False),
]
LIVE_METHOD = str(HERE / "optimus_compactor_live.py")

PLANNED_TASK_CALLS = 112  # 4 templates x 2 cases x (4N+3) task-model calls
PLANNED_SUMMARIZER_CALLS = 32  # 4 compactions x 8 cases per Rust arm
# The parent ruling's absolute ceiling (Option B 512 planned; Amendment-1
# allows up to 768). The first arm's ladder defect (same-rung re-rolls, since
# fixed) consumed ~130 unplanned requests, so the suite runs under the 768
# absolute ceiling and Amendment-1's extension headroom is treated as spent.
DEFAULT_HTTP_CAP = 768
EXTENSION_HTTP_CAP = 768


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing environment variable {name}")
    return value


def count_log_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def total_http_requests() -> int:
    """Every HTTP request logged so far (task + summarizer + pong)."""
    total = 0
    if RAW.exists():
        for path in RAW.rglob("*.jsonl"):
            total += count_log_lines(path)
    return total


def backoff_ratio() -> float:
    calls = 0
    backoffs = 0
    if RAW.exists():
        for path in RAW.rglob("*.jsonl"):
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    calls += 1
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get("transportRetries") or record.get("error"):
                        backoffs += 1
    return (backoffs / calls) if calls else 0.0


async def pong_verify(log_path: Path) -> None:
    """Amendment-3: verify the endpoint immediately before each arm."""
    base = _env("ROT2_LIVE_BASE_URL").rstrip("/")
    started = time.perf_counter()
    response = await httpx.AsyncClient(
        timeout=float(os.environ.get("ROT2_LIVE_HTTP_TIMEOUT_S", "600")),
        headers={
            "Authorization": f"Bearer {_env('ROT2_LIVE_API_KEY')}",
            "X-Pomerium-Authorization": _env("ROT2_LIVE_POMERIUM"),
        },
    ).post(
        f"{base}/chat/completions",
        json={
            "model": os.environ.get("ROT2_LIVE_MODEL", "glm-5.3"),
            "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
            "temperature": 0.0,
            "max_tokens": 8192,
            "stream": False,
        },
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    body = response.json()
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    record = {
        "tsMs": int(time.time() * 1000),
        "purpose": "pong",
        "model": os.environ.get("ROT2_LIVE_MODEL", "glm-5.3"),
        "maxTokens": 8192,
        "httpStatus": response.status_code,
        "latencyMs": latency_ms,
        "finishReason": ((body.get("choices") or [{}])[0].get("finish_reason")),
        "contentChars": len(content),
    }
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    if response.status_code != 200 or "PONG" not in content.upper():
        raise SystemExit(f"PONG verification failed: http {response.status_code}, content {content[:80]!r}")
    print(f"PONG ok ({latency_ms} ms)")


def run_arm(slug: str, compactor: str, *, suite: str, case_count: int, drift: int,
            benchmarks_dir: Path, output: Path, resume: bool, http_cap: int,
            provider: str, model: str, method_file: str | None = None) -> None:
    """One arm through the stock runner with per-arm logging and guards."""
    task_log = RAW / f"{slug}-task.jsonl"
    for stale in ("ROT2_TASK_CALL_LOG", "ROT2_TASK_LOG_ARM", "ROT2_TASK_MAX_REQUESTS"):
        os.environ.pop(stale, None)
    if provider == "glm-live":
        os.environ["ROT2_TASK_CALL_LOG"] = str(task_log)
        os.environ["ROT2_TASK_LOG_ARM"] = slug
        # Planned task calls + 50% slack for ladder rungs before the guard trips.
        os.environ["ROT2_TASK_MAX_REQUESTS"] = str(int(PLANNED_TASK_CALLS * case_count / 2 * 1.5))
    summarizer_dir = RAW / f"{slug}-summarizer"
    summarizer_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ROT2_LIVE_CALL_LOG_DIR"] = str(summarizer_dir)
    args = RunArgs(
        method_spec=f"{method_file or LIVE_METHOD}:{compactor}",
        suite_key=suite,
        provider_key=provider,
        model=model,
        difficulty=DifficultyLevel.MEDIUM,
        drift_cycles=drift,
        case_count_per_template=case_count,
        seed_group="default",
        benchmarks_dir=benchmarks_dir,
        output_path=output,
        resume=resume,
        concurrency=2,
    )
    started = time.perf_counter()
    asyncio.run(run_experiment(args))
    elapsed = time.perf_counter() - started
    print(f"arm {slug}: wrote {output} in {elapsed:.0f}s")


def budget_check(label: str, http_cap: int) -> None:
    used = total_http_requests()
    ratio = backoff_ratio()
    print(f"budget [{label}]: {used} HTTP requests so far (cap {http_cap}), backoff ratio {ratio:.1%}")
    if used > http_cap:
        raise SystemExit(f"budget guard tripped: {used} > {http_cap} HTTP requests")
    if ratio > 0.10:
        raise SystemExit(
            f"Amendment-3 pause: {ratio:.1%} of calls needed backoff (>10%). "
            "Pausing for a parent ruling; raw logs under research/context_rot_r2/compactbench_adapter/live/raw/"
        )


def phase_smoke() -> None:
    """One scenario, one cycle, through the full live path."""
    smoke_dir = HERE / "smoke-benchmarks"
    smoke_dir.mkdir(exist_ok=True)
    (smoke_dir / "starter").mkdir(exist_ok=True)
    template = BENCHMARKS / "starter" / "buried_constraint_starter_v1.yaml"
    target = smoke_dir / "starter" / "buried_constraint_starter_v1.yaml"
    if not target.exists():
        target.write_bytes(template.read_bytes())
    output = RESULTS / "smoke.jsonl"
    if output.exists():
        output.unlink()
    asyncio.run(pong_verify(RAW / "smoke.jsonl"))
    run_arm(
        "smoke", "OptimusRustCompactorTaskState",
        suite="starter", case_count=1, drift=0,
        benchmarks_dir=smoke_dir, output=output, resume=False,
        http_cap=DEFAULT_HTTP_CAP, provider="glm-live", model="glm-5.3",
    )
    result = to_run_result(output)
    case = result.cases[0]
    print(f"smoke case {case.case_id}: score {case.case_score:.3f}, "
          f"cycles {len(case.cycles)}, compression {result.compression_ratio:.2f}x")
    for cycle in case.cycles:
        for item in cycle.scorecard.item_scores:
            print(f"  cycle {cycle.cycle_number} item {item.item_key}: {item.score}")


def phase_suite() -> None:
    """The ruled Option B: 4 custom arms x starter slots 0-1, 3 drift cycles."""
    for slug, compactor, _uses_harness in ARMS:
        output = RESULTS / f"{slug}.jsonl"
        # Idempotent: an interrupted arm resumes and skips its completed cases
        # (case ids are seed-derived, so the resumed set is exactly the rest).
        resume = output.exists() and count_log_lines(output) > 0
        asyncio.run(pong_verify(RAW / f"{slug}-task.jsonl"))
        run_arm(
            slug, compactor,
            suite="starter", case_count=2, drift=3,
            benchmarks_dir=BENCHMARKS, output=output, resume=resume,
            http_cap=DEFAULT_HTTP_CAP, provider="glm-live", model="glm-5.3",
        )
        budget_check(slug, DEFAULT_HTTP_CAP)


def _overall(path: Path) -> float:
    return to_run_result(path).overall_score


def phase_amend1() -> None:
    """Pre-registered extension decision (aggregate scores only, no per-case detail)."""
    scores = {slug: _overall(RESULTS / f"{slug}.jsonl") for slug, _, _ in ARMS}
    print("aggregate overalls:", json.dumps({k: round(v, 4) for k, v in scores.items()}, indent=1))
    ts = scores["optimus-rust-task-state"]
    plain = scores["optimus-rust-plain"]
    echo = scores["echo-first-n"]
    oracle = scores["full-transcript-oracle"]
    decision = {
        "scores": scores,
        "rust_gap": abs(ts - plain),
        "rust_within_0_05": abs(ts - plain) <= 0.05,
        "rust_ranking_matters": True,
        "bracket_gap": abs(echo - oracle),
        "bracket_ranking_matters": False,
    }
    extend = decision["rust_within_0_05"]
    decision["extend"] = extend
    decision["rationale"] = (
        "task-state vs plain within 0.05 and their order decides the report's verdict -> extend both"
        if extend else
        "task-state vs plain differ by more than 0.05 -> no extension"
    )
    (HERE / "amend1-decision.json").write_text(json.dumps(decision, indent=2), encoding="utf-8")
    print(json.dumps(decision, indent=2))
    if not extend:
        return
    for slug in ("optimus-rust-task-state", "optimus-rust-plain"):
        base_output = RESULTS / f"{slug}.jsonl"
        extended = RESULTS / f"{slug}-extended.jsonl"
        # Resume requires case_count_per_template to match; copy the finished
        # 2-case file, bump its run_start to 4, and resume so only slots 2-3 run.
        lines = base_output.read_text(encoding="utf-8").splitlines()
        patched = []
        for line in lines:
            record = json.loads(line)
            if record.get("event") == "run_start":
                record["case_count_per_template"] = 4
            patched.append(json.dumps(record))
        extended.write_text("\n".join(patched) + "\n", encoding="utf-8")
        asyncio.run(pong_verify(RAW / f"{slug}-task.jsonl"))
        run_arm(
            slug, dict((s, c) for s, c, _ in ARMS)[slug],
            suite="starter", case_count=4, drift=3,
            benchmarks_dir=BENCHMARKS, output=extended, resume=True,
            http_cap=EXTENSION_HTTP_CAP, provider="glm-live", model="glm-5.3",
        )
        budget_check(f"{slug}-extension", EXTENSION_HTTP_CAP)


def phase_mock_baseline() -> None:
    """Offline mock baseline on THIS binary for the same 8-case subset."""
    from compactbench.compactors import get_built_in  # noqa: F401  (parity with W12 commands)

    adapter = str(ADAPTER_DIR / "optimus_compactor.py")
    for slug, compactor, _uses_harness in ARMS:
        output = RESULTS / f"mock-{slug}.jsonl"
        if output.exists():
            output.unlink()
        run_arm(
            slug, compactor,
            suite="starter", case_count=2, drift=3,
            benchmarks_dir=BENCHMARKS, output=output, resume=False,
            http_cap=DEFAULT_HTTP_CAP, provider="mock", model="mock-deterministic",
            method_file=adapter,
        )
    print("mock baseline done (fixture summarizer, zero model calls)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True,
                        choices=["smoke", "suite", "amend1", "mock-baseline"])
    args = parser.parse_args()
    RESULTS.mkdir(exist_ok=True)
    RAW.mkdir(exist_ok=True)
    if not str(BENCHMARKS).strip() or not BENCHMARKS.is_dir():
        raise SystemExit(
            f"set {BENCHMARKS_ENV} to <compactbench-checkout>/benchmarks/public; "
            "there is no machine default"
        )
    if not os.environ.get("HARNESS", "").strip():
        raise SystemExit(
            "set HARNESS to the compaction-harness binary built from this "
            "worktree (cargo build -p pi-coding-agent --bin compaction-harness); "
            "there is no machine default"
        )
    os.environ.setdefault("ROT2_RESERVE_TOKENS", "4000")
    os.environ.setdefault("ROT2_HARNESS_TIMEOUT_S", "1800")
    if args.phase == "smoke":
        phase_smoke()
    elif args.phase == "suite":
        phase_suite()
    elif args.phase == "amend1":
        phase_amend1()
    else:
        phase_mock_baseline()


if __name__ == "__main__":
    main()
