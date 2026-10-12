# LME-M full run: parallel shard launcher + throughput levers

Branch: `improve/mem3-lmem-launcher` (worktree `imp3-lmemlaunch`, based on the
imp3 HEAD `bdffcad50` whose `memory_bench.exe` release build is being driven).

Problem: a sequential 20-shard LongMemEval-M run was measured at ~2.0 import
jobs/min per driver process (~95 h total). This adds a parallel launcher and
evaluates the two cheap throughput levers.

## 1. Launcher

`scripts/lmem_parallel_shard_launcher.py` (stdlib-only Python 3, Windows-aware).

Each shard runs exactly like the sequential baseline:

```
cd <imp3> && <imp3>/target/release/examples/memory_bench.exe <shard manifest> > <log> 2>&1
   with MEMORY_BENCH_MODELS_JSON=<models.json>
```

Features:

- `--concurrency N` parallel driver processes with launch staggering (`--stagger`,
  default 3 s) so N preflight probes do not hit the endpoint simultaneously.
- Per-shard log files with incremental tailing; the last log line is shown in
  every status table.
- Progress metrics parsed from driver stderr (all driver progress lines are
  `eprintln`, i.e. unbuffered): `[env i/N] ... ingesting/ingested`,
  `[q i/N] ... asking/done`.
- Import-job flow counting: the driver logs nothing per session, so the launcher
  also polls `runs/<shard>/envs/*/agent/memory/projects/*/jobs/import_*.json`
  and reports `jobs_applied/jobs_seen` (only new/changed files are re-read;
  `applied` is terminal). This is the "import jobs flowing" signal.
- Failure detection: non-zero exit codes, fatal log lines (`error:`,
  `panicked at`), driver retry warnings (`failed (N consecutive)`,
  `timed out for job`), and a stall timer (no log growth AND no new applied
  jobs for `--stall-minutes`, default 45) - the driver can legitimately sit
  silent for a long time inside one env, hence the job-counting signal.
- `--time-limit T` kills all children (`taskkill /F /T`, whole tree) after T
  seconds and reports `KILLED` with exit code 0 (dry-run semantics).
- `--retries N` relaunches failed shards; safe because the driver resumes:
  completed envs are skipped via `env.jsonl` and already-applied import jobs
  short-circuit in `jobs.rs run()`.
- Periodic status table, final summary table, and `--summary-json`.

Usage:

```
# validated dry run (2 shards, 15 min, then kill):
python scripts/lmem_parallel_shard_launcher.py --shards s00,s01 --concurrency 2 \
    --time-limit 900 --status-interval 60 \
    --log-dir <dir>/dryrun/logs --summary-json <dir>/dryrun/summary.json

# real 20-shard run (recommended settings):
python scripts/lmem_parallel_shard_launcher.py --shards all --concurrency 6 \
    --retries 1 --status-interval 300 \
    --log-dir <dir>/fullrun/logs --summary-json <dir>/fullrun/summary.json
```

Defaults for `--manifest-dir`, `--run-root`, `--binary`, `--workdir` and
`--models-json` point at the current bench layout, so `--shards`,
`--concurrency` and `--log-dir` are the only required thinking.

## 2. Recommended concurrency: 8 (6 if retries appear)

The endpoint is assumed to tolerate ~4-8 concurrent driver processes. Each
driver is itself mostly sequential and has two retry layers below the launcher
(provider retry in `complete_with_provider_retry`, plus 3 driver-level
`importRunRetries` per invocation), so moderate saturation is absorbed rather
than fatal. Recommendation: run at **8 concurrent drivers** with `--retries 1` - the dry run
showed zero retry warnings and zero fatal lines while 9 drivers were
concurrently ingesting on the endpoint (2 dry-run shards + 7 dev5 drivers),
so the top of the assumed 4-8 range is validated. Fall back to 6 if the
`RETRY` column climbs during the first hour, or if the dev5 validations are
still running when the full run starts. Use `--retries 1` so an overnight shard
failure relaunches instead of idling a slot. During the validation dry run,
4 unrelated dev5 drivers were already running, so s00+s01 exercised the
endpoint at exactly the recommended 6 concurrent drivers.

## 3. maxImportChunkChars tradeoff (the cheap LLM-call lever)

One paragraph, as requested:

Raising `maxImportChunkChars` (a manifest settings field; currently 15000) cuts
LLM calls because chunks are packed to that budget of serialized evidence
chars (`jobs.rs prepare()`, hard-capped at 75000) and every chunk costs exactly
one extraction call with `max_output_tokens = maxExtractionTokens`. The danger
is that `maxExtractionTokens` is already at its store.rs validation maximum of
32000 (bounds `(maxExtractionTokens, 256, 32000)`), so the output budget
cannot be raised alongside the input, and a chunk whose edits-plus-thinking
response exceeds 32000 tokens hits `stop_reason=length`, which `plan_refinement`
treats as an immediate hard `Truncated` error with no JSON-repair retry; the
driver then retries the same oversized chunk up to 3 times, fails
deterministically each time, and the whole env (and shard run) aborts - so the
chunk size must be chosen so worst-case output stays comfortably under the cap.
Empirically (lme_m dev5 run, GLM-5.3): sessions serialize at 1.03-1.24x their
file bytes, sizes are median 12.4 KB / max 26.3 KB, extraction output runs
0.45-0.76 tokens per input char (mean ~0.55), and at the current 15000 budget
38% of sessions split into 2 chunks (~660 chunks per 480-session env).
Recommendation: **raise `maxImportChunkChars` from 15000 to 24000** - almost
every session then fits one chunk (~485 chunks/env, ~26% fewer extraction
calls, and roughly 30-40% fewer total output tokens since 2-chunk sessions
were observed to emit ~1.4-1.6x the output of comparable 1-chunk sessions
because each chunk pays its own thinking pass), while the worst-case
extrapolated output stays ~0.76 x 24000 = ~18.2k tokens, only 57% of the
un-raisable 32000 cap. Going to 30000 buys only ~1% more call reduction but
pushes the worst case to ~22.8k (71% of cap), and anything above ~30000 risks
deterministic Truncated failures; if any `Truncated`-category failures appear
in the first hour of the real run, fall back to 20000. The change is
manifest-only (`"maxImportChunkChars": 24000` in the settings block; the field
already exists in the shard manifests with value 15000), no code or rebuild
needed - but the manifests are run inputs owned by the lmem workstream, so
patched copies should be generated rather than editing the originals in place.

Supporting measurements (lme_m_dev5_improved_v1, env 15745da0_abs):

| session file | bytes | serialized chars | chunks @15k | edits | input tok | output tok |
|---|---|---|---|---|---|---|
| sharegpt_GrLzv0V_0.jsonl | 11,480 | 11,822 | 1 | 1 | 5,262 | 6,226 |
| fda75251_1.jsonl | 12,752 | 15,867 | 2 | 4 | 7,353 | 12,056 |
| f0e71553_5.jsonl | 18,028 | 21,143 | 2 | 4 | 9,711 | 9,414 |
| 561e7ba9.jsonl | 20,522 | 23,637 | 2 | 5 | 9,331 | 11,519 |

Code anchors: `crates/pi-coding-agent/src/core/memory/jobs.rs` (chunk packing,
`chunk_budget = max_import_chunk_chars.min(75000)`, per-chunk checkpointing),
`crates/pi-coding-agent/src/core/memory/store.rs` (settings bounds incl. the
32000 `maxExtractionTokens` ceiling), `crates/pi-coding-agent/src/core/memory/
extraction.rs` (`options.max_output_tokens = max_extraction_tokens`),
`crates/pi-coding-agent/src/core/refinement/refinement.rs`
(`StopReason::Length` -> immediate `Truncated` error, no repair retry).

## 4. Dry-run validation (s00+s01, concurrency 2, 15 min)

Command:

```
python scripts/lmem_parallel_shard_launcher.py --shards s00,s01 --concurrency 2 \
    --time-limit 900 --status-interval 60 --stagger 3 \
    --log-dir imp3-lmemlaunch/dryrun/logs --summary-json imp3-lmemlaunch/dryrun/summary.json
```

Results (exit code 0, wall 15:00, artifacts kept untracked in `dryrun/`):

- Both shards passed noJev/settings preflight and `[preflight] dgx-glm53/glm-5.3 reachable`.
- Both entered env 1/25 (476 / 477 sessions) within seconds of each other.
- Import jobs flowed the whole 15 minutes: **37 applied** (s00: 17, s01: 20),
  one more running at kill time; first job applied ~2 min after launch.
- Zero fatal lines and zero retry warnings in both logs at every status tick.
- Per-driver rate under load: 1.13 (s00) and 1.33 (s01) applied jobs/min,
  measured while 7 unrelated dev5 drivers were also hitting the endpoint
  (9 concurrent drivers total) - i.e. the endpoint absorbed 9 drivers with
  no retry storms. The parent's own lme_m dev5 driver sustained ~1.7 jobs/min
  in the same window, so aggregate throughput scaled ~5x over a single driver.
- `--time-limit 900` killed both children (`taskkill /F /T`) at t+900s;
  process count dropped 9 -> 7; both shards reported `KILLED`, launcher exit 0.
- Partial output dirs `runs/lme_m_full_v1_s00` and `runs/lme_m_full_v1_s01`
  were deleted; `runs/` verified byte-identical to the pre-run listing.

Note on honest wall-clock math: a shard is ~25 envs x ~477 sessions = ~11,900
import jobs; at the solo 2.0 jobs/min baseline that is ~99 h per shard, so
20 shards at concurrency C cost about `ceil(20/C) x 99 h` (C=6: ~16 days,
C=8: ~12 days) unless per-driver speed improves. The two levers that change
per-driver speed are the chunk size below (fewer extraction calls per
session) and endpoint contention (run at night with nothing else on the
endpoint). If that is still too slow, the next structural step is per-env
workers rather than per-shard workers (~500 env-manifests instead of 20
shard manifests; the driver already skips done envs via env.jsonl, so
re-sharding is cheap), which this launcher can drive unchanged via
`--manifest-dir` pointing at the env-level manifests.
