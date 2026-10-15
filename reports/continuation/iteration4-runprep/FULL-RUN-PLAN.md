# FULL-RUN-PLAN.md — full official benchmark runs (round-4/5 banked config)

Worktree `imp4b-runprep`, branch `improve/mem4b-runprep` (from campaign HEAD
`240e9fbc9` = `improve/memory-iteration-4`, merged with origin/main; code delta
vs main is evidence-only). Prepared 2026-10-15 by the run-prep analyst.

**Scope: planning + manifests only. No run in this change set is launched.**
Every launch below is USER-GATED. This document is the operating plan for the
full official runs: per-benchmark execution plans under the 12-job endpoint
cap, warm-store reuse inventory (cache keys re-verified offline), the
recommended cheapest-evidence-first sequence, stagger/spawn rules, the
ledger-lock protocol, and a go/no-go checklist per run.

---

## 0. Ground rules (apply to every run in this plan)

| rule | value / source |
|---|---|
| endpoint cap | **12 concurrent jobs**; 1 job = 1 running `memory_bench` driver process. A subagent may hold several. Judge calls ride the same DGX endpoint (short, 1–8 s) — avoid running judge storms while 12 drivers saturate it. |
| model | `dgx-glm53` / `glm-5.3` ONLY (never Jev). Judge = same provider via `reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json`. |
| fleet binary | `imp4-targetR/release/examples/memory_bench.exe`, sha256-prefix `b57dc607` (post-merge build, tests lib memory 138/138 + example 75/75). A second binary is in live campaign use: `imp4b-target8k/.../memory_bench.exe` sha `4d2ba13f` (built 2026-10-14 06:02; used by writecamp/writefull/lmesq3b/lpc2). **Pin per benchmark (see each section) and record the sha in the run receipt.** If rebuilding: isolated `CARGO_TARGET_DIR imp4b-runprep-target`; NEVER `imp4/target` or another worker's target. |
| spawn pattern | **Session-owned**: drivers are bash-handle children of the LIVE agent session. WMI-detached/detached launches get reaped by harness cleanup (lmev2full ing2/ing3/ing4 lesson). |
| stagger | **5 minutes between driver spawns** (endpoint ramp); the campaign tools' 5-second stagger (`--stagger 5`) is for short campaign batches only. |
| run-ids | ≤ 20 chars, unique (driver refuses non-fresh output on relaunch under the new capture protocol). |
| attempts/run roots | OUTSIDE git worktrees, total path ≤ ~190 chars (Windows MAX_PATH, LongPathsEnabled=0). typeA attempts-roots outside worktrees (project-identity bug). |
| disk discipline | per `reports/continuation/iteration4/PATHS.json`: `rg` only with explicit small target dir + `-g` filters; never walk `campaign-root/reports//runs/`. |
| no sleep loops | monitoring via agent-owned heartbeats + bash-handle follow-ups. Long phases get a heartbeat (interval ≥ 10 min) that checks driver liveness + progress and escalates on stall (30 min no-progress default). |
| scoring | `[repo-continuation bench venv python] -m bench.eval.evaluate --bench <family> --run <run.jsonl> --variant improved --qs-dir <qs-dir> --out <scores.json> --models-json <judge-models.json> --judge-profile <profile>`; cwd = campaign root. LP needs `LP_JUDGE_MAX_TOKENS=4096` minimum (8192 for long-reasoning questions). **Question-scoped ONLY for partial/ledger work** (1-record qs-dir copies; never score a partial run vs a full qs-dir). Final official scores: full run.jsonl vs the full official qs-dir. |
| ledger | `reports/continuation/iteration4/QUESTION-LEDGER.json` (campaign-root, outside worktrees). Per-question campaign runs append attempts under the sidecar lock; full-run drivers do NOT write the ledger — they write receipts (see §6). |

**Current live occupancy (measured 2026-10-15 ~00:30, must be re-checked at every go/no-go):**
8 of 12 slots busy — 3x `lmev2_ing3.exe` (lmev2 web 3-shard ingest, s1/s2/s3) + 5x `memory_bench.exe`
(lmesq6 gate, lmesq3b batch x3, lpcamp2, wc5). Official phases must either wait for
drain or size themselves into free slots.

---

## 1. Per-benchmark plans

Populations (verified from `data/common/*` and the ledger):

| family | official population | answered today | warm stores under CURRENT banked write config |
|---|---|---|---|
| locomo | 300 dev questions (dev300 declarations; 10 convs x 17–40 q) | partial (iteration-3 gate runs; not banked-config) | **10/10 conversations WARM** |
| locomo_plus (dev) | 120 questions (each its own env, ~26.5 sessions/env; 3,177 sessions total) | 5/120 (dev5, frozen+fresh E3) | **5/120 WARM** |
| lme_v2 | 451 questions (240 web + 211 enterprise; 2 envs x 100 sessions) | 0 under banked | web ingest ~65% done (3-shard); enterprise not started |
| longmemeval_s | 500 questions (ledger 498 + 2 smoke5 known-correct kept out of ledger) | 29–30 answered (campaign configs; not all under banked read) | **14 WARM** typeB caches (+ ~6 in flight) |
| longmemeval_m | 500 questions (ledger 499 + 1 dev5 known-correct) | 2/500 under v1b settings (bc8a6e93_abs, e61a7584, both passed) | **2/500 WARM only if config pinned to lmem v1b** (no W1/W2); 0 if the family clause set is adopted |

Warm-cache keys in this section were recomputed offline with the controller's
`typeb_cache_key` (sha256 of env-spec, env-id, importInstructions,
extraction settings, model) against the on-disk cache dirs — not trusted from notes.

### 1.1 LoCoMo — 300 dev questions, answer-only (CHEAPEST full official result)

- **Config (banked, frozen)**: write = clause-set stores (writecamp/writefull;
  FINAL9-stem importInstructions), read = FINAL9 + stem + deterministic recall
  window 24 / 24000 chars. Settings:
  `reports/continuation/iteration4-experiments/loco-writecamp/base-det-settings.json` +
  `fullstack-final9-stem.json` (in the imp4b-writecamp branch; committed copies
  also at `loco-writefull/` in imp4b-writefull).
- **Warm stores (verified)**: `reports/continuation/iteration4/ql-typeb-cache/`
  entries `conv-26--d4b542a8…`, `conv-30--62b3e5ec…`, `conv-41--f3e2ba05…`,
  `conv-42--beda05a4…`, `conv-43--cfbee6dc…`, `conv-44--15430ae5…`,
  `conv-47--3f790289…`, `conv-48--029159a9…`, `conv-49--543cedbc…`,
  `conv-50--a3366924…` — all 10 match the banked settings hash (computed keys
  equal the on-disk dir names; W1/W2 LME-S clause changes do NOT touch the
  LoCoMo clause set, so these stay valid).
- **Plan**: 10 per-conversation answer manifests (skipIngest=true, the conv's
  dev300 gold-free declarations, banked read settings), each run dir a fresh
  copy-out of the warm store (controller's `copy_store_into_scratch` pattern:
  copy + projects.json rebind). 10 drivers, one per conv, staggered 5 min.
- **Wall-clock**: ~2–4 min/question → 30 q/driver ≈ **1–2 h**; + local-F1
  scoring (minutes, no LLM). Say **≈2 h at 10 slots**.
- **Endpoint calls**: ~300–600 answer calls (+ blank-rescue re-asks,
  hard-capped 4096 tokens). **Zero extraction calls, zero judge calls.**
- **Scoring**: local-F1 (no judge config), qs-dir = iteration-2 gate common
  locomo dir (declarations sha `9e88a6fd…`), merge 10 run.jsonl → score.
- **Binary**: pin `4d2ba13f` (the binary the writecamp/writefull answers were
  validated with) OR `b57dc607` after a 1-conv spot-check (both post-merge;
  read path identical in code — verify by re-running 2 gate questions and
  comparing answers before the full pass).

### 1.2 LMEv2 — 451 questions, 2 envs (web + enterprise)

- **Config (banked)**: `imp4b-lmev2full/reports/continuation/iteration4-inputs/manifests/lme_v2_dev5_full_v4.settings.json`
  (page-structure import clauses, captureAxtreeMaxChars etc.). Target typeB
  cache: `web--46c8aec059fe9cec` (key computed by the lmev2full controller).
- **State**: web 3-shard ingest RUNNING (started 2026-10-14 08:46; ~65/100
  sessions applied at last check: s1 21/34, s2 26/33, s3 18/33). ing2–ing5
  single-driver attempts all died (budget-exhausted refinement retries +
  external taskkills); the shard drivers survive as session-owned processes.
  **Enterprise env: not started** (100 sessions, same shape).
- **Plan**: (a) let the web shards finish (est. 4–12 h remaining at the
  observed ~1.3 sessions/h/driver; retry storms are the risk — see go/no-go);
  (b) merge the 3 shard stores into the `web--46c8aec0` cache entry (W's
  merge pattern: harness_state entries + env.jsonl, then `cache_state` must
  report warm); (c) repeat the 3-shard pattern for enterprise (3 more
  manifests, 33/34/33 sessions); (d) answer phase: 451 questions from the two
  warm stores, sharded 60–90 questions per driver (6–8 drivers), skipIngest
  copy-outs; (e) full scoring vs
  `reports/continuation/lme-v2-comparison-inputs/private-scoring-questions`.
- **Wall-clock**: web finish 4–12 h + enterprise 12–30 h + answers
  451 x ~3–5 min ÷ 6 ≈ 4–6 h + judges (~2.4 s mean) ≈ **1.5–2 days at 3–8 slots**.
- **Endpoint calls**: extraction ≈ 2,450 calls/env (measured basis) x 2 envs
  ≈ ~4,900, x1.5–2 retry factor ≈ **7–10k**; ~450–900 answer calls; ~450–900
  judge calls.
- **Binary**: `b57dc607` (the running shards use renamed copies of exactly
  this sha — keep it).

### 1.3 LoCoMo-Plus dev — 120 questions, 115 fresh ingests

- **Config (banked)**: dev5 v3 base settings
  (`reports/continuation/iteration3-inputs/manifests/loco_plus_dev5_improved_v3.json`
  settings) + E3 read overrides (`imp4b-lpcamp/.../lpcamp/e3-settings.json`:
  pool-500 raw). LP judge `official-lp-v1`, `LP_JUDGE_MAX_TOKENS=4096`
  (8192 for long-reasoning questions).
- **Warm stores (verified)**: `lpcamp-scratch/cache/` — `lp_18--9e2926e8…`,
  `lp_69--93b181ed…`, `lp_118--58328ea3…`, `lp_159--225ee5dd…`,
  `lp_232--dbbe2bef…` (fresh cue-backed, 98.9–99.3% cue coverage, gold
  present; dev5 pair-pass 5/5). Keys verified against dev5-manifest env specs
  + v3+E3 settings.
- **Plan**: replicate the lpcamp ingest pattern for the remaining 115 envs
  (`ingest_one.py` pattern: per-env typeB ingest, cue-coverage + gold
  verification, cache-root `lpcamp-scratch/cache`), N=10 drivers staggered
  5 min; then 120 answers from warm stores (copy-outs) + full scoring vs the
  120-file dev qs-dir (`data/common/locomo_plus/` top level; the 5-file
  `.../scoring` dir is the dev5 subset only).
- **Wall-clock**: measured lpcamp per-store ingest ≈ 3–5 h at 2–4 concurrent
  drivers → 115 x ~3.5 h ÷ 10 ≈ 40 h; answers ~1–2 h. **≈ 1.5–2 days at 10 slots.**
- **Endpoint calls**: 3,050 session-ingests x ~15–25 extraction calls ≈
  **45–75k** extraction calls; 120 answers; 120 judges (~7.7 s mean).
- **Known risk**: rerank-invalid flake (fresh 4/12 vs frozen 8/40 observed);
  PR2 (rerank parse-retry) not merged — official run carries this exposure;
  document, do not block.
- **Binary**: `b57dc607` (lpcamp's binary).

### 1.4 LME-S — 500 questions (~471 to answer; ~486 fresh typeB ingests, 14 warm)

- **Config (banked per round-5 q5)**: write = W1 (category-word) + W2
  (battery-word) clauses; read = H2b + H3d + H4 (settings
  `lmesseq-scratch/ov-banked.json` over `base-smoke5.json`).
  **DIVERGENCE TO RESOLVE BEFORE THE OFFICIAL RUN**: the live lmesq6/lmesq3b
  workers run answerInstruction = `ov-h5-count.json` (a newer H5 read
  clause). Freeze the read clause (H4-banked vs H5-candidate) first; the
  typeB caches are read-clause-independent, so ingest work is not wasted
  either way.
- **Warm caches (verified under W1+W2 import)**: `ql-typeb-cache/`
  031748ae_abs, 06db6396, 07741c44, 07741c45, 09d032c9, 0a995998, 0bb5a684,
  0ea62687, 0edc2aef, 10d9b85a, 10e09553, 118b2229, 1192316e, 18dcd5a5
  (**14 warm**; ~6 of those are in-flight answers right now). All pre-W2
  caches (17 more qids: 06878be2, 06f04340, 078150f1, 07b6f563, 0862e8bf,
  0862e8bf_abs, 08e075c7, 08f4fc43, 0977f2af, 099778bb, 09ba9854,
  09ba9854_abs, 0a34ad58, 0db4c65d, 0ddfec37, 0ddfec37_abs, 0e4e4c46,
  0e5e2d1a) are STALE — the W1/W2 importInstructions change re-keys them.
- **Plan**: the official result = the completed sequential queue under the
  frozen banked config (the ledger campaign IS the official population walk:
  `run_lmes_seq.py` wrapper, official sorted qs order, question-scoped
  scoring, judge `official-lme-v1`). Scale from the current 1–4 workers to
  10–12 ledger workers (queue slices by qid ranges; the ledger lock makes
  claim-and-run safe), then assemble the 500-record run.jsonl from the
  attempt dirs and score once against the full
  `data/common/longmemeval/full` qs-dir.
- **Wall-clock**: fresh typeB ~45–60 min/question (median 50-session haystack
  @ ~1 session/min + answer + judge). ~486 fresh + ~14 warm x ~52 min avg:
  - at 12 slots ≈ **35 h (~1.5 days)**
  - at 8 slots ≈ 51 h; at 4 slots ≈ 4.3 days.
- **Endpoint calls**: ~470 x ~50 sessions x 1–3 extraction calls ≈
  **25–70k**; ~500 answers; ~500–1,000 judge calls.
- **Binary**: `b57dc607` (lmesq6's binary; lmesq3b uses `4d2ba13f` — freeze
  one and record it).
- **Note**: 2 open FAILED questions (00ca467f, 0e5e2d1a) still get answered
  under the banked config (expected fail; recorded honestly).

### 1.5 LME-M — 500 questions, ~500 fresh per-env ingests (THE LONG POLE)

- **Config decision needed FIRST (user/parent gate)**:
  - **Option A (family-consistent, recommended)**: W1+W2 write clauses +
    LME banked read → the 2 existing warm stores are INVALID (re-keyed);
    all 500 envs need fresh ingest.
  - **Option B**: pin `lmem_base_settings_v1b.json` (no clause set) →
    `bc8a6e93_abs--8abf2d78…` and `e61a7584--3dd84f83…` stay warm (2/500).
  - Either way: no LME-M answer exists under a banked config today.
- **Tooling (proven, in-tree)**: `scripts/lmem_env_shard_splitter.py`
  (500 per-env manifests at
  `reports/continuation/iteration3-inputs/manifests/lme_m_env/`, path-budget
  validated, worst-case artifact path 249 < 260) +
  `scripts/lmem_parallel_shard_launcher.py` (N concurrent drivers, stagger,
  stall timer, resume). Sessions/env: min 460 / median 476 / max 490
  (~237k import jobs total). Ingest rate: 1.0–1.17 jobs/min/driver
  (validated at 9 concurrent drivers; anything above 9 is unvalidated — and
  now capped at 12 anyway).
- **Plan**: per-env drivers via the launcher at `--concurrency 12` (minus
  live campaign slots), `--retries 1` (pre-output failures only — the new
  capture protocol is fresh-output-only; a mid-env death needs
  `rmdir /s /q <output_dir>` + relaunch, blast radius one env). Answers
  after each env's ingest (the per-env manifests carry the 1 gold-free
  question; the controller-style scoring is question-scoped; final full
  score vs `data/common/longmemeval_m/full`).
- **Env-sharding (W's lmev2 pattern) — when to use it**: splitting ONE env's
  ~475 sessions across N shard drivers does NOT reduce total driver-hours
  under a slot-capped regime (total ≈ 500 x 6.8–7.9 h ≈ 3,400–3,950
  driver-hours regardless). Use it only for (a) the run TAIL when fewer than
  12 envs remain, and (b) blast-radius control on flaky envs. Under the cap,
  plain per-env drivers are simpler and validated.
- **Wall-clock**: 3,400–3,950 driver-hours ÷ 12 slots ≈ **283–328 h ≈ 12–14 days**
  (at 8 slots ≈ 18–21 days). Plus ~500 answers (~3–5 min each) and judges.
- **Endpoint calls**: ~237k session-ingests x 1–3 extraction calls ≈
  **240–700k** extraction calls — by far the dominant endpoint cost of the
  whole campaign. Budget/monitor accordingly (this is ~5–10x all other
  families combined).
- **Binary**: `b57dc607`.

---

## 2. What can be assembled WITHOUT new ingests (cheapest first)

| # | result | new ingest needed | endpoint cost | wall-clock | status |
|---|---|---|---|---|---|
| 1 | **LoCoMo full official 300-question score** | **NONE** (10/10 warm stores verified) | ~300–600 answer calls, 0 judges | ~2 h at 10 slots | full official result, ready at first GO |
| 2 | LME-S warm-cache answers (14–20 qids) | none for those qids | ~20–60 answer+judge calls | <1 h | partial only — incremental ledger progress, NOT an official score |
| 3 | LP dev5 answers (5/120) | none (5 warm) | ~5–10 answer+judge calls | <30 min | partial only; the frozen+fresh E3 evidence already covers these |
| 4 | LME-M 2/500 answers | none IF config pinned to v1b | ~2–5 answer+judge calls | <30 min | partial only; config decision required first |
| 5 | LMEv2 | web store incomplete | — | — | NOT assemblable |

**Only LoCoMo is a complete official benchmark result with zero new ingest.**
Everything else needs the ingest work priced in §1.

---

## 3. Recommended sequence (cheapest-evidence-first)

1. **GO gate**: user approves official runs; freeze configs (LME-S read
   clause H4 vs H5; LME-M Option A/B); pin binaries per benchmark.
2. **LoCoMo** (≈2 h, 10 slots) — first full official score banked.
3. **LMEv2 completion** (≈1.5–2 days, 3–8 slots) — web shards finish +
   merge; enterprise 3-shard; 451 answers.
4. **LP dev** (≈1.5–2 days, 10 slots) — 115 fresh cue-backed stores + 120
   answers + full dev scoring.
5. **LME-S** (≈1.5 days at 12 slots, longer at fewer) — scale the live
   sequential ledger campaign to the frozen banked config through all 500.
6. **LME-M** (≈12–14 days at 12 slots) — start only after 2–5 are banked;
   this is the only multi-week phase and it wants all 12 slots.

Headline totals: phases 2–5 ≈ **4–5.5 days**; adding LME-M ≈ **17–19 days
end-to-end**. Starting LME-M early at reduced slots does NOT help (it is
slot-bound: total driver-hours are fixed; early start only steals slots from
the cheap phases and delays their evidence).

---

## 4. Stagger / spawn rules (official runs)

1. **Session-owned only**: every driver is a `bash()` handle child of the
   live agent session. No WMI/detached spawns (reaped by harness cleanup).
2. **5-minute stagger** between driver spawns (endpoint ramp; watch first-hour
   retry/FATAL rates before spawning the rest).
3. **Batch spawns**: launch at most 3–4 drivers per wave; confirm preflight
   lines (`noJev isolation verified`, `memory settings matched`,
   `dgx-glm53/glm-5.3 reachable`, correct session counts) in each driver.log
   before the next wave.
4. **Slot accounting before every wave**: `tasklist` count of live
   `memory_bench`/driver exes + the run-prep receipts (below) must be
   ≤ 12 − (drivers about to spawn). Re-check after each wave settles.
5. **Heartbeat monitoring** for phases > 1 h: agent-owned heartbeat
   (≥ 10 min interval) per phase, checking: driver processes alive,
   run.jsonl record growth, import-job growth (no 30-min stalls), retry/FATAL
   lines. Escalate on stall: kill → clean output dir → relaunch (fresh-output
   rule), never auto-retry mid-env ingests more than once.
6. **Kill discipline**: `taskkill /F /T` on the driver tree; then delete the
   output dir before any relaunch (capture-protocol fresh-output rule).

## 5. Ledger-lock protocol (per-question campaign runs: LME-S, LME-M options)

- The controller's `append_attempt` acquires the sidecar lock
  (`QUESTION-LEDGER.json.lock`, msvcrt/fcntl), re-reads, appends the attempt,
  writes temp + atomic replace. Workers NEVER hold the lock across driver
  runs (claim → run → append; duplicate attempts are tolerated and visible).
- Status semantics: FAILED / RESOLVED / UNTESTED only. Official-run full
  drivers (multi-question manifests) do not write the ledger.
- Queue claiming for scaled LME-S/LME-M workers: slice by qid ranges
  (deterministic, disjoint), not by "next UNTESTED" racing.
- **Run receipts (full runs)**: every launched phase writes
  `reports/continuation/iteration4-runprep/LAUNCHES.json` entries
  (run_id, manifest, binary sha256, settings sha256, cache keys reused,
  started_at, pid, log path) — same pattern as the gate3/LP launchers.

## 6. Go/no-go checklist (per run — ALL boxes required)

1. [ ] User GO for this specific phase.
2. [ ] Config frozen: settings file(s) + sha256 recorded; LME-S read clause
       resolved (H4-banked vs H5); LME-M Option A/B resolved.
3. [ ] Binary pinned + sha256 verified on disk (b57dc607 / 4d2ba13f per §1);
       if rebuilt, tests green (lib memory + example) and sha recorded.
4. [ ] Warm caches to reuse re-verified: recompute `typeb_cache_key` for each
       env (offline, controller code) == on-disk cache dir; `cache_state`
       warm (env.jsonl + store document present).
5. [ ] Slot headroom: live driver count + receipts ≤ 12 − planned drivers.
6. [ ] Fresh output dirs (nonexistent or empty) for every driver; run-ids
       ≤ 20 chars; attempt/run roots outside worktrees; worst-case artifact
       path < 260 (splitter validates for LME-M; check equivalents elsewhere).
7. [ ] Manifests validated: population == official (300 / 120 / 451 / 500 /
       500), declarations gold-free, skipIngest consistent with store reuse,
       captureProtocol `optimus-memory-capture/temporal-authority/1.0.0`
       where required (LME family).
8. [ ] Scoring recipe staged: bench venv python (repo-continuation), judge
       models json, `LP_JUDGE_MAX_TOKENS` for LP, full qs-dir for final
       scores; question-scoped qs-dirs for any partial scoring.
9. [ ] Monitoring armed: heartbeat(s) + expected stall thresholds; kill/relaunch
       procedure agreed.
10. [ ] Endpoint sanity: one canary question passes end-to-end (answer +
        judge + score) before the full wave spawns.

---

## 7. Cost summary

| family | ingest driver-hours | answer calls | judge calls | wall-clock (slots) |
|---|---|---|---|---|
| locomo | 0 (10 warm) | ~300–600 | 0 (local F1) | ~2 h (10) |
| lme_v2 | ~40–90 h | ~450–900 | ~450–900 | 1.5–2 d (3–8) |
| locomo_plus dev | ~350–460 h | 120 | 120 | 1.5–2 d (10) |
| longmemeval_s | ~375–470 h | ~500 | ~500–1,000 | 1.5 d (12) |
| longmemeval_m | ~3,400–3,950 h | ~500 | ~500 | 12–14 d (12) |
| **total** | **~4,200–5,000 driver-hours** | **~2,000–2,600** | **~1,600–2,500** | **≈17–19 days** |

Prepared without launching anything: no endpoint calls were made, no
drivers started, no memory_bench runs touched. All cache-key verification
was offline (controller `typeb_cache_key` recomputation).
