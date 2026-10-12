# LoCoMo-Plus full 401-example run: shard launcher + cost model

Branch: `improve/mem3-lp-launcher` (worktree `imp3-lplaunch`, based on imp3
HEAD `00d6a6a9a`).  The launcher adapts J's
`scripts/lmem_parallel_shard_launcher.py` (`improve/mem3-lmem-launcher`,
worktree `imp3-lmemlaunch`) rather than duplicating it: same process-per-shard
pattern, log tailing, import-job counting, failure detection and retry
semantics.  LP-specific changes are listed in the launcher docstring.

## 1. Run shape (verified from `data/common/locomo_plus/full/`)

- **401 envs, 1 question each** (not ~41 envs x 10 questions): the LP family
  isolates each question in its own stitched env (cue session + LoCoMo base).
- 10,899 sessions total; sessions per env: min 19, median 29, mean 27.2, max 32.
- Sharded manifests are owned by F (glm-lpfull, branch `improve/mem3-lp-full`,
  worktree `imp3-lpfull`) and HAVE LANDED (commit `e389ad92a`, generator
  `bench/converters/locomo_plus_full.py`):
  `lp_full_v3_s00.json` .. `lp_full_v3_s40.json` = 41 shards (40 x 10 envs +
  1 x 1 env), plus a combined single 401-env manifest
  `loco_plus_full_improved_v3.json` and a `loco_plus_full_smoke5_v3.json`.
  The launcher's `--shard-glob` defaults to `lp_full_v3_s*.json` (the 41
  shards, NOT the combined manifest).

Inventory result (launcher `--validate-only`, this branch): all 41 shards
pass - 401 envs, 10,899 sessions, 401 questions, 0 technique-stack
violations; env ids unique across shards; every question's env present; all
43 manifest files byte-identical to F's `manifests-receipt.json` sha256s.

## 2. Launcher

`scripts/lp_full_shard_launcher.py` (stdlib-only Python 3, Windows-aware).

Each shard runs exactly like the sequential baseline:

```
cd <imp3> && <imp3>/target/release/examples/memory_bench.exe <shard manifest> > <log> 2>&1
   with MEMORY_BENCH_MODELS_JSON=<models.json>
```

Features inherited from the LME-M launcher:

- `--concurrency N` parallel driver processes with launch staggering
  (`--stagger`, default 5 s).
- Per-shard log files with incremental tailing (last line in every table).
- Progress parsed from driver stderr: `[env i/N] ... ingesting/ingested`,
  `[q i/N] ... asking/done`.
- Import-job flow counting: polls
  `<output_dir>/envs/*/agent/memory/projects/*/jobs/import_*.json`,
  reports `jobs_applied/jobs_seen` (terminal `applied` is sticky).
- Failure detection: non-zero exit codes, fatal log lines (`error:`,
  `panicked at`), driver retry warnings, and a stall timer (default 30 min
  with no log growth AND no new applied jobs; LP cue batches sit silent for
  only ~2-3 min per session, so 30 min leaves margin).
- `--time-limit T` kills all children (`taskkill /F /T`) and exits 0.
- `--retries N` relaunches failed shards; the driver resumes (env.jsonl skip,
  applied jobs short-circuit, entries that already carry `prospectiveCues`
  are skipped by the cue pass).
- Periodic status table, final summary table, `--summary-json`.

LP-specific additions:

- Shard discovery by `--shard-glob` instead of a hardcoded prefix.
- Each shard manifest is parsed at startup; `run_id`, `output_dir`, env and
  question totals come from the manifest itself (the driver writes wherever
  `output_dir` points, so the launcher never guesses the run dir).
- The status table includes a per-shard question column and manifest-declared
  totals.
- `--validate-only`: full inventory + technique-stack validation.  Checks,
  per shard and in aggregate: benchmark/variant/captureProtocol/model match
  the reference manifest; settings match BOTH the reference manifest and the
  hardcoded expected stack (Cognitive `answerInstruction` marker
  "memory-aware dialogue setting", `importInstructions`,
  `recallProspectiveIndex`, `recallQueryExpansion` (+draws 2),
  `recallQueryExtract`, `recallQueryDistillation`, `recallRerank`,
  `recallRerankCandidates=200`, `maxRecallEntries=20`, `maxRecallChars=24000`,
  `questionTimeoutS=300`, `maxExtractionTokens=32000`,
  `maxImportChunkChars=15000`, `ingestTimeoutS=21600`, `importRunTimeoutS=900`,
  `disableTools`, `evidenceTimestamps`, `skipIngest=false`); env ids unique
  across shards; every question's env present; qids unique; output dirs not
  shared; totals vs `--expect-envs/--expect-questions` (default 401/401).

Usage:

```
# 1. after F's manifests land: inventory + technique-stack validation
python scripts/lp_full_shard_launcher.py --shards all --validate-only

# 2. decision-gated 2-env dry run (parent gate: dev5 v3 scores promising)
python scripts/lp_full_shard_launcher.py --shards s00,s01 --concurrency 2 \
    --retries 1 --log-dir <dir>/dryrun-logs --summary-json <dir>/dryrun-summary.json

# 3. real full run, 6 concurrent drivers
python scripts/lp_full_shard_launcher.py --shards all --concurrency 6 \
    --retries 1 --status-interval 300 \
    --log-dir <dir>/fullrun-logs --summary-json <dir>/fullrun-summary.json
```

Defaults for `--binary`/`--workdir`/`--models-json`/`--manifest-dir` point at
the current bench layout (the same sealed pre-B `memory_bench.exe` the live
dev5 v3 run uses — do NOT rebuild while runs execute).

## 3. Cost estimate (wall-clock and tokens)

Calibration data:

- dev5 v2 (stack without cues, 1 driver): 1.06-2.0 min/session ingest,
  44-60 min/env; questions 6-30 s each.
- live dev5 v3 (stack with cues, endpoint shared by 7-9 drivers): observed
  ~4.5-4.7 min per APPLIED session (import job files appear at
  import_prepare; the applied status lands ~4.5-5 min later - extraction
  plus apply plus the per-session cue batch, which lands ~140 s after its
  import event and costs ~4.7 s per created entry); i.e. the mean env
  (27.2 sessions) takes ~2.1 h of driver work.
- v2 token usage per env (~28 sessions): ~249k input / ~602k output (ingest).
  v3 adds ~815 cue calls/env (one per created entry): ~+0.65M input /
  +0.12M output.

Totals for the full 401-env run:

| quantity | value |
|---|---|
| sessions | 10,899 |
| driver work (ingest+cues+questions) | ~640-860 h |
| input tokens | ~355M |
| output tokens | ~280M |

Wall-clock at concurrency C (throughput assumed to scale to the endpoint's
tolerance; J's LME-M guidance: 6 concurrent drivers is the recommended point,
8 is viable if retry warnings stay flat):

| C | wall-clock (4.7 min/session) | wall-clock (3.8 min/session, endpoint less loaded) |
|---|---|---|
| 4 | ~9 days | ~7 days |
| 6 | ~6 days | ~4.9 days |
| 8 | ~4.5 days | ~3.7 days |

Notes:

- The 4.7 min/session figure was measured while the endpoint also served the
  LME-M full run (2 shards) and 4 dev5 drivers.  Running LP alone at C=6
  should land between the two columns; recalibrate from the launcher's
  status table after the first hour.
- Sequencing: the LME-M full run (~16 h at C=6) and the live dev runs should
  finish or be paused before the LP full run starts, both for endpoint
  headroom and because the parent gate (dev5 v3 scores) is not expected
  before those runs complete anyway.
- Token totals are rough (extraction output dominates; cue calls add ~20%
  input).  They size the DGX load, not a dollar figure.

## 4. Dry-run status (decision gate)

The 2-env benchmark dry run is PREPARED but NOT LAUNCHED: dev5 v3
(`loco_plus_dev5_improved_v3`, live since 13:22) was still ingesting env 1/5
at preparation time, so no scores exist yet.  Command above, step 2.  Launch
only after the parent confirms dev5 v3 per-question scores are promising
(> 0 on cognitive questions).

A separate 5-minute launcher-mechanics smoke (2 synthetic single-env
manifests derived from the dev5 v3 manifest, `--time-limit 300`) was run and
PASSED: both drivers launched (5 s stagger), passed noJev preflight, started
ingesting; status tables showed live ENVS/Q/JOBS columns; import-job counting
updated in real time (1 applied -> 2 seen); the time-limit kill reaped both
process trees (exit 0, no orphans); `dryrun/summary.json` written.  Evidence
kept in `dryrun/` (untracked): `logs/s0.log`, `logs/s1.log`, `summary.json`,
the two smoke manifests.

## 5. Open coordination items

- F's manifests have landed and passed validation + sha256 receipt checks;
  nothing further needed from F unless the parent wants a different shard
  granularity (41 shards balances 6-8 concurrent drivers well).
- Observed in the live dev5 v3 env lp_18: only ~15% of entries carried
  `prospectiveCues` after 5 sessions (cue LLM calls partially failing,
  non-fatal).  This is a technique-stack concern for the dev5 gate, not a
  launcher concern; the timing model above already includes the observed
  per-session cue cost.
- The 2-env benchmark dry run and the full run both wait on the parent's
  decision gate (dev5 v3 per-question scores).
