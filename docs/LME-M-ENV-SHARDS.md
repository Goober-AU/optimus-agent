# LME-M per-env manifest sharding (500 parallel workers)

Branch: `improve/mem3-lmem-envshards` (worktree `imp3-lmemenv`, based on the
imp3 HEAD `08b568ad6` whose `memory_bench.exe` release build drives the runs).

Problem: the full LongMemEval-M run exists as 20 shard manifests of 25 envs
each (`lme_m_full_v1_s00..s19.json`). One driver process ingests a shard's
envs sequentially, so the run is bounded by 20 concurrent drivers and
~25 x 8 h of wall clock per shard - ~8.3 days at concurrency 20, and the
12-day sequential-shard plan is unacceptable. The structural fix from
`docs/LMEM-SHARD-LAUNCHER.md` section 4: re-shard to ONE manifest per
environment so worker count is bounded only by endpoint tolerance.

## 1. The splitter

`scripts/lmem_env_shard_splitter.py` (stdlib-only, read-only on the shard
manifests; run data untouched). It reads the 20 shard manifests and emits
500 per-env driver manifests plus an `_index.json` sidecar (env_id ->
run_id/shard/session count/validation summary):

    python scripts/lmem_env_shard_splitter.py            # split + validate
    python scripts/lmem_env_shard_splitter.py --check-only   # re-validate only

Copied verbatim from each shard manifest: `captureProtocol`
(`optimus-memory-capture/temporal-authority/1.0.0`), `benchmark`,
`variant`, `model` (`dgx-glm53`/`glm-5.3`), `seed` (20261010), the full
settings block and the env body + gold-free question declaration.
Changed per env manifest:

  - `run_id`: `lme_m_env_<sha256(env_id)[:8]>` (e.g. `lme_m_env_97d15ab4`)
  - `output_dir`: `<iteration3-inputs>/runs/<run_id>`
  - `envs`/`questions`: exactly the one env / one question of that run
  - `settings.maxImportChunkChars`: 15000 -> **24000** (J's measured lever,
    see `docs/LMEM-SHARD-LAUNCHER.md` section 3: ~26% fewer extraction
    calls, worst-case output ~18.2k tokens vs the un-raisable 32000
    `maxExtractionTokens` cap). Pass `--keep-shard-chunk-chars` to copy the
    shard value instead; fall back to 20000 if `Truncated` failures appear
    in the first hour of the real run.

Output: `reports/continuation/iteration3-inputs/manifests/lme_m_env/`
(500 x ~5.9 MB + `_index.json`).

### Path budget (deliberate layout deviation)

Run dirs live directly under `runs/`, NOT under `runs/lme_m_env_runs/`.
The driver's deepest artifact is
`<output_dir>\envs\<qid>\agent\memory\projects\project_<24hex>\jobs\import_<24hex>.source.jsonl`
= `output_dir + 111 + len(qid)` chars (measured on the dev5 run: max 248
with its 125-char output dir). Full-set qid lengths: 8 (x368), 12 (x25),
13 (x102), 17 (x5, `gpt4_*_abs`). A nested
`runs/lme_m_env_runs/lme_m_env_<hash8>` output dir is 135 chars ->
263-char paths for 17-char qids (over the 260 Windows limit, hosts has
LongPathsEnabled=0) and 259 for 13-char qids (1-char margin). Direct
`runs/<run_id>` gives output_dir 121 chars and a measured worst case of
249 - the same margin as the shard layout itself. The splitter refuses to
emit any manifest whose worst-case artifact path reaches 260.

## 2. Validation (all passed, 2026-10-12)

- Count: 500 env manifests == sum of shard envs (20 x 25).
- Every qid appears exactly once across the set (multiset-equal to the
  shard qids; also env_id == qid pairing intact per manifest).
- run_ids unique, output_dirs unique, settings uniform across all 500.
- Worst-case artifact path 249 < 260.
- Schema: 3 deterministic samples field-by-field vs their shard manifests
  (scalars, model, seed, env deep-equal, question deep-equal, settings
  deep-equal modulo the documented chunk override) - first-by-run_id,
  longest-qid (`gpt4_70e84552_abs`), median.
- Independent full-coverage re-check (separate script, not the splitter's
  code path): sha256 of every env object and every question declaration
  multiset-equal between shards and env set; top-level scalar tuple equal.

Sessions per env: min 460, median 476, max 490, mean 475.3.

## 3. Launcher changes (backward compatible)

`scripts/lmem_parallel_shard_launcher.py` needed two small fixes to truly
"drive ANY manifest set" via `--manifest-dir`:

1. `run_dir` (import-job monitoring location) is now read from each
   manifest's own `output_dir`; the old `<run-root>/<shard-prefix><name>`
   derivation never matched per-env run dirs (`runs/lme_m_env_<hash8>`).
2. `--shards all` in a non-shard manifest dir now falls back to every
   plain `*.json` (skipping `_`-prefixed sidecars); previously the
   `lme_m_full_v1_s*.json` glob matched nothing. The token generator also
   only strips the `lme_m_full_v1_` prefix when actually present - the old
   unconditional strip turned `lme_m_env_97d15ab4` into `5ab4` and
   collided ("duplicate shards selected").

Verified: `--shards all --manifest-dir .../manifests/lme_m_env --dry-run`
resolves 500 manifests; `--shards s00-s02` in the shard dir is unchanged.

## 4. Canary (one env, 10 min, cleaned)

Launched env `001be529` (median 476 sessions, shard s00, run_id
`lme_m_env_97d15ab4`) with the patched launcher at concurrency 1 and
`--time-limit 600`:

    python scripts/lmem_parallel_shard_launcher.py --shards lme_m_env_97d15ab4.json \
        --manifest-dir <...>/manifests/lme_m_env --run-root <...>/runs \
        --concurrency 1 --time-limit 600 --status-interval 60 \
        --log-dir <tmp>/canary-logs --summary-json <tmp>/canary-summary.json

Observed (log + output dir):

- Fresh-output check passed; `[run lme_m_env_97d15ab4] ... envs=1 questions=1`.
- `[preflight] noJev isolation verified` (no Jev anywhere in the run).
- `[preflight] memory settings matched` with `maxImportChunkChars: 24000`
  in both requested and effective settings.
- `[preflight] dgx-glm53/glm-5.3 reachable` (shared DGX endpoint).
- `[env 1/1] 001be529 ingesting 476 session(s)` within ~1 min of launch.
- Import jobs flowed the whole 10 minutes: first job applied ~75 s after
  launch, then steady - **18 applied / 19 seen at kill (t+600 s), zero
  FATAL lines, zero RETRY lines, no stalls**. That is 1.8 jobs/min with 7
  unrelated dev5 drivers on the endpoint (8 concurrent total). The 22.9 KB
  first session fit ONE chunk at the 24000 budget (would be 2 at 15000).
- Launcher's JOBS column tracked the real output dir (manifest-driven
  run_dir works).
- `--time-limit 600` killed the driver tree at t+600 s (KILLED, launcher
  exit 0; driver exit 1 from taskkill); the process list dropped back to
  exactly the 7 pre-existing drivers.
- Post-kill relaunch probe (same manifest, fresh 60 s launcher run):
  driver exits immediately with `error: New capture protocol requires
  fresh output; do not resume or relabel old captures`.
- The run output dir was then deleted and `runs/` re-verified back to its
  9 pre-canary entries.

Retry/resume caveat (empirically confirmed by the relaunch probe): under
`captureProtocol temporal-authority/1.0.0` the driver is fresh-output-only
- relaunching a manifest whose output dir already contains `envs/`,
`env.jsonl` or `run.jsonl` fails at startup. `--retries` therefore only
helps for failures before first output (e.g. endpoint down at launch); a
driver that dies mid-env needs `rmdir /s /q <output_dir>` and a relaunch.
Per-env sharding makes that cheap: the blast radius of any restart is one
env (~8 h), not a 25-env shard (~8 days of work).

## 5. Parallelism plan

Job math: one import job = one session (jobs.rs `prepare()` takes a single
session file), so an env is ~475 jobs (460-490). NOTE: the "~240 import
jobs/env" planning figure that circulated earlier is wrong by ~2x - at
0.5 jobs/min that error hides a factor of two in wall clock.

Measured per-driver rates (import jobs applied per minute):

- 0.5 jobs/min: parent's conservative planning floor.
- 1.13-1.33 jobs/min: J's dry run, 9 concurrent drivers on the endpoint.
- ~1.17 jobs/min: lme_m dev5 driver in the same window (42 jobs / 36 min).
- 1.8 jobs/min: this change set's 10-min canary (18 applied / 10 min) with
  8 concurrent drivers - single sample, take with the 24000-chunk lever
  reducing per-job work vs the older measurements.

Time per env: 475 jobs / rate = **6.8 h** (at 1.17/min), **7.9 h** (at
1.0/min), **15.8 h** (at the 0.5/min floor).

Wall clock for 500 envs = ceil(500/C) batches x per-env time (plus tail):

| C drivers | batches | @0.5/min | @1.0/min | @1.17/min |
|---|---|---|---|---|
| 9 (validated today) | 56 | 37.0 d | 18.5 d | 15.8 d |
| 20 (recommended start) | 25 | 16.5 d | **8.3 d** | 7.1 d |
| 30 | 17 | 11.2 d | 5.6 d | 4.8 d |
| 40 | 13 | 8.6 d | 4.3 d | 3.7 d |
| 50 (if endpoint holds) | 10 | 6.6 d | **3.3 d** | 2.8 d |

Endpoint constraint: DGX GLM-5.3 (LaunchPad K3, shared). Validated
evidence so far: 9 concurrent drivers with zero retry warnings. 7
unrelated dev5 drivers were running during the canary - the canary itself
was the 8th. Anything above 9 concurrent drivers is UNVALIDATED.

Recommended schedule:

1. **Start at 20 concurrent drivers** (count any other workstream drivers
   currently on the endpoint against this - if the dev5 validations are
   still up, either wait or start at 20 minus those).
2. **Watch the RETRY column** (and FATAL/IDLE) at every status tick for
   the first hour. Scaling signal: zero retry growth, no STALLED flags,
   per-driver jobs/min not degrading more than ~30% vs the solo rate.
3. **Scale to 30-40** once stable for >= 1 h; re-watch for another hour
   after each step. 40 concurrent = ~4.3 d at 1.0 jobs/min.
4. **Roll back to 9-12** if retries climb steadily, stalls appear, or the
   endpoint starts returning errors; 9 is the validated-safe floor.
5. 50 is the ceiling worth attempting (10 batches x 8 h = 3.3 d headline);
   beyond ~50 the shared endpoint is speculative and per-driver rate decay
   likely eats the gain.
6. Use `--retries 1` (covers pre-output failures only - see the fresh-
   output caveat above) and `--status-interval 300` for long phases.

Full-run command (NOT executed in this change set - canary only):

    python scripts/lmem_parallel_shard_launcher.py --shards all \
        --manifest-dir <...>/iteration3-inputs/manifests/lme_m_env \
        --run-root <...>/iteration3-inputs/runs \
        --concurrency 20 --retries 1 --status-interval 300 \
        --log-dir <...>/envrun/logs --summary-json <...>/envrun/summary.json

Scoring note: the env runs produce 500 `run.jsonl` files (one per env)
instead of 20; the scoring pass merges them the same way it planned to
merge per-shard files. `_index.json` maps env_id -> run_id/output_dir.

## 6. What was NOT done

- The full 500-env set was NOT launched (by instruction). Only the single
  canary env ran, and its output dir was deleted afterwards.
- No changes to the converter; if the per-env layout is ever regenerated
  from the dataset, rerun the splitter (it is idempotent with `--force`).
