# FULL-RUN DECISION — iteration-3 launch packet (LP 401 / LME-M 500 / LME-S 500)

One clean view for the parent's go/no-go before committing days of DGX GLM-5.3
compute. Every setting below was read from the actual manifests in
`reports/continuation/iteration3-inputs/manifests/` (not from memory of the
builders' notes); every command is the launcher's documented interface; every
cost figure carries its measurement basis. Companion doc:
`EXPERIMENT-LEDGER.md` (experiment states feeding the gates).

## 0. Cross-run invariants

- **Model / judge**: `dgx-glm53/glm-5.3` for ALL LLM work (driver + judge + any
  helper); no Jev (every run log carries the noJev preflight receipt).
- **Driver binary**: all launchers default to
  `C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp3/target/release/examples/memory_bench.exe`
  = sealed build `ab1e1474` (from commit `bdffcad50`, built 2026-10-12 13:22).
  It contains: temporal discipline, prospective-index plumbing, expansion/
  extract/distill/rerank, LMEv2 span-verify, LME-M converter support. It does
  NOT contain: source spans (E3-05) or the prospective-cue parser fix (E3-03).
  **Never rebuild while any run executes** (the linker replacing a locked exe
  killed a run pair on 2026-10-12). See §5 for the rebuild decision.
- **Fresh-output rule**: under capture protocol `temporal-authority/1.0.0` the
  driver refuses to resume/relabel an output dir that already has `envs/`,
  `env.jsonl` or `run.jsonl`. A mid-run death needs
  `rmdir /s /q <output_dir>` + relaunch. `--retries` only covers pre-output
  failures. The driver DOES resume completed envs/jobs on a clean relaunch of a
  still-fresh dir.
- **Endpoint**: shared DGX LaunchPad K3. Validated concurrency so far: 9
  drivers with zero retry warnings (J's dry run + 7 dev5 drivers). Anything
  above 9 is unvalidated; the LME-M plan below ramps with a watch rule.
- **Scoring** (all three families, run after the runs finish):
  `python -m bench.eval.evaluate --bench <family> --run <run.jsonl> --full
  --out <scores.json> --models-json <models.json> --allow-network`
  (or `--qs-dir` for dev sets). Judges: LME-S/-M = official-lme-v1 (glm-5.3,
  temp 0, 2048 max); LP = local cognitive judge (glm-5.3, temp 0, 512 max);
  run records carry `question_sha256` + binding provenance for the scorer.

---

## 1. LP full run — LoCoMo-Plus, 401 examples

**Stack** (verified in all 41 shard manifests + aggregate + smoke5, byte-identical
settings; launcher `--validate-only` reports 0 violations, 401 envs / 10,899
sessions / 401 questions, sha256 receipt match):

| Setting | Value |
|---|---|
| answerInstruction | official INSTRUCTION_COGNITIVE verbatim ("Your task: This is a memory-aware dialogue setting...") — **present in 41/41 shards** |
| importInstructions | one-entry-per-DISTINCT-fact, both speakers, no summarizing |
| recallProspectiveIndex | true (E3-03 mechanism; needs the parser fix in the binary, see gate) |
| recallQueryExpansion / Draws | true / 2 |
| recallQueryExtract / Distillation | true / true |
| recallRerank / Candidates | true / 200 |
| maxRecallEntries / maxRecallChars | 20 / 24000 |
| evidenceTimestamps | true |
| answerMaxTokens / maxExtractionTokens / maxImportChunkChars | 8192 / 32000 / 15000 |
| questionTimeoutS / ingestTimeoutS / importRunTimeoutS | 300 / 21600 / 900 |
| model / seed | dgx-glm53/glm-5.3 / locomo-plus-three-arm-inputs-v1 |

Not carried (deliberate or pending): the ANSWER-PROTOCOLS LP variant (c) — the
official Cognitive text was chosen instead (matches the Kumiho 93.3% setup; the
variant remains available as an A/B follow-up); source spans (binary-gated);
type router (worker D, unmerged).

**Prerequisites (all three, in order):**
1. dev5 v3 (`loco_plus_dev5_improved_v3`) per-question scores — parent gate:
   > 0 on cognitive questions (retrieval must surface the cue).
2. Cue-fix smoke PASS (`loco_plus_cuefix_smoke.json`, lp_18 replica on an
   isolated build with fix `b1df68a2d`): essentially all new entries must carry
   `prospectiveCues`. Without the fix the live binary lands only 11-17% cue
   coverage — the LP mechanism is load-bearing (raw-lexical top-6 gold hit
   rate is 1.0%), so **do not launch LP full on build `ab1e1474`**.
3. Rebuild including the cue fix (see §5) + driver tests green.

**Launcher** (K's `scripts/lp_full_shard_launcher.py`, from the imp3 worktree):

```
# a) inventory + technique-stack validation (no processes launched)
python scripts/lp_full_shard_launcher.py --shards all --validate-only

# b) decision-gated 2-env dry run (kills children after 15 min, exit 0)
python scripts/lp_full_shard_launcher.py --shards s00,s01 --concurrency 2     --retries 1 --log-dir <dir>/dryrun-logs --summary-json <dir>/dryrun-summary.json

# c) real full run
python scripts/lp_full_shard_launcher.py --shards all --concurrency 6     --retries 1 --status-interval 300     --log-dir <dir>/fullrun-logs --summary-json <dir>/fullrun-summary.json
```

`--binary/--workdir/--models-json/--manifest-dir` defaults already point at the
campaign layout (binary: sealed `ab1e1474` path — **pass `--binary` explicitly
after the §5 rebuild**).

**Recommended concurrency: 6** (8 viable if the RETRY column stays flat for the
first hour; each +2 drivers ≈ −0.7 d wall clock). Watch: FATAL/RETRY/IDLE
columns, per-shard jobs/min, 30-min stall timer (default).

**Wall-clock** (basis: 4.7 min/APPLIED session measured live with the endpoint
shared by 7-9 drivers; 3.8 min/session column if the endpoint is less loaded):

| Concurrency | Wall-clock |
|---|---|
| 4 | ~7-9 days |
| 6 | ~4.9-6 days |
| 8 | ~3.7-4.5 days |

Recalibrate from the launcher status table after the first hour.

**Cost (tokens)**: ~355M input / ~280M output across the run (basis: dev5 v2
measured ~249k in / 602k out per ~28-session env for ingest; v3 adds ~815 cue
calls/env ≈ +0.65M in / +0.12M out; 401 envs, 10,899 sessions; question stage
negligible). Extraction output dominates.

---

## 2. LME-M full run — LongMemEval-M, 500 questions (per-env sharding)

**Stack** (verified in dev5 + all 20 shard manifests + all 500 per-env
manifests): recall ON, distill ON, rerank ON **pool 25** (AgentOS K=5/pool-25
discipline), extract ON, expansion ON (2 draws), 6 entries / 8000 chars,
maxImportChunkChars 15000 (dev5 + shards) / **24000** (per-env set, J's measured
lever: ~26% fewer extraction calls, worst-case output ~18.2k tokens vs the
un-raisable 32000 cap), ingestTimeoutS 43200, questionTimeoutS 240, model
dgx-glm53/glm-5.3, seed 20261010. `evidenceTimestamps` is not set in these
manifests but the driver default is TRUE — same effective setting as the S/LP
sets.

**FLAG — the answer protocol is MISSING.** All 521 LME-M manifest files were
built BEFORE the protocols landed and carry the old S-inherited instruction
("...Reply with only the answer itself... reply with exactly: UNKNOWN") instead
of the validated LME protocol variant (a). Verified: 1 dev5 + 20 shard + 500
per-env files, 521/521 old. The E3-06 A/B (1.00 non-regressive, +951 output
tokens/answer) is the evidence FOR adopting the protocol on this family.

**Files needing regeneration (521 + 1 sidecar):**

| Set | Files |
|---|---|
| dev5 manifest | `iteration3-inputs/manifests/lme_m_dev5_improved_v1.json` |
| full shards | `iteration3-inputs/manifests/lme_m_full_v1_s00.json` .. `lme_m_full_v1_s19.json` (20) |
| per-env set | `iteration3-inputs/manifests/lme_m_env/lme_m_env_*.json` (500) + `_index.json` (content unchanged — no hashes inside; run_ids unchanged) |

**Exact regeneration commands** (converter edit + deterministic rebuild; run
from a worktree on improve/memory-iteration-3 lineage, e.g. `imp3-lmem` or a
fresh branch; the converter's isolated venv exists in `imp3`):

```
# 1) Point ANSWER_INSTRUCTION in bench/converters/longmemeval_m.py at the LME
#    protocol variant (a) text from ANSWER-PROTOCOLS.md (bench/ANSWER-PROTOCOLS.md
#    @ 0f13fa7e0). recallQueryExtract: true is already set — keep it paired.
#    Commit the converter change on the lmem workstream branch.

# 2) Regenerate dev5 + the 20 shard manifests (deterministic from the pinned
#    dataset; ONLY settings.answerInstruction changes):
cd C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp3
bench/.venv/Scripts/python.exe -m bench.converters.longmemeval_m     --data-root C:/Users/openclawuser/optimus-memory-bench-20261007-040424/data     --run-manifest-root C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration3-inputs     --full-shards 25

# 3) Re-split the 500 per-env manifests (defaults point at the campaign paths;
#    24000 chunk lever is the splitter default; --force overwrites):
python scripts/lmem_env_shard_splitter.py --force

# 4) Re-validate the split (counts, qid multiset, path budget 249 < 260):
python scripts/lmem_env_shard_splitter.py --check-only
```

Side effects of step 2 (identical content, deterministic): rewrites
`canonical-envs/`, `private-scoring-questions/`, `gold-free-questions/`
companions and `data/common/longmemeval_m/full/qs_*.jsonl`. The live dev5 run
is unaffected (it read its manifest at startup); its answers will still use the
old instruction, so treat it as the retrieval/capture signal only.

**Prerequisites:** dev5 M signal (R4, live — first-ever M run), the
regeneration above, fresh output dirs (never relaunched into a used dir), and
run-queue clearance (LP v3 + LMEv2 v3/v4 + gate2 first, per the parent's
ordering — LME-M is priority 3).

**Launcher** (J's `scripts/lmem_parallel_shard_launcher.py` driving the per-env
set via `--manifest-dir`; requires the post-`21640c4e5` launcher patch —
present at improve/memory-iteration-3 HEAD `ec917a26a`):

```
python scripts/lmem_parallel_shard_launcher.py --shards all     --manifest-dir C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration3-inputs/manifests/lme_m_env     --run-root C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration3-inputs/runs     --concurrency 20 --retries 1 --status-interval 300     --log-dir <dir>/envrun/logs --summary-json <dir>/envrun/summary.json
```

**Recommended concurrency: start at 20, ramp to 30-40 after ≥1 h stable,
ceiling 50** (scale signal: zero retry growth, no STALLED, per-driver jobs/min
not degrading >30% vs solo; roll back to 9-12 if retries climb — 9 is the
validated-safe floor). Count any other workstream drivers on the endpoint
against these numbers.

**Wall-clock** (basis: ~475 jobs/env measured at 0.5-1.8 jobs/min per driver;
per-env 6.8-7.9 h at 1.0-1.17 jobs/min):

| Concurrency | @0.5 jobs/min | @1.0 jobs/min | @1.17 jobs/min |
|---|---|---|---|
| 20 (recommended start) | 16.5 d | **8.3 d** | 7.1 d |
| 30 | 11.2 d | 5.6 d | 4.8 d |
| 40 | 8.6 d | 4.3 d | 3.7 d |
| 50 (ceiling) | 6.6 d | 3.3 d | 2.8 d |

**Cost (tokens)**: extraction-dominated. Basis: ~6.5M serialized chars/env
(~475 sessions, median 12.4 KB, 1.03-1.24x serialization); measured extraction
throughput 0.40-0.45 input and ~0.40-0.55 output tokens per input char at the
24000-chunk budget; the converter's planning figure (~1.2M input/env) is
chars/4 and underestimates measured input. Estimate: **~2.5-3.0M input and
~2.2-2.7M output per env -> ~1.3-1.5B input and ~1.1-1.4B output across the
500-env run** (question stage: 500 x ~8k in / ~1.3k out ≈ 4M in / 0.7M out —
negligible). This is the largest single token commitment of the three runs;
it is the reason per-env parallelism and the 24000-chunk lever matter.

**Scoring note**: the env runs produce 500 `run.jsonl` files (one per env);
the scoring pass merges them via `_index.json` (env_id -> run_id/output_dir).

---

## 3. LME-S full run — LongMemEval-S, 500 questions

**Stack** (verified in the aggregate + all 20 shards + smoke5; receipt
sha256s in `imp3-lmesfull/reports/lmesfull/manifests-receipt.json`; settings =
dev5 improved_v2 baseline + the additions below):

| Setting | Value |
|---|---|
| answerInstruction | **LME answer protocol variant (a)** (ANSWER-PROTOCOLS.md) — present in 20/20 shards + aggregate |
| recallQueryExtract | true (protocol pairing requirement) |
| recallQueryExpansion / Draws | true / 2 — **flag: the A/B validated protocol+extract with expansion OFF; expansion was validated separately on other families. Residual risk accepted by M's set; cheap A/B on dev5 if the parent wants it closed first** |
| recallQueryDistillation / recallRerank | true / true, pool 200 |
| maxRecallEntries / maxRecallChars | 8 / 8000 |
| answerMaxTokens / maxExtractionTokens / maxImportChunkChars | 8192 / 32000 / 15000 |
| questionTimeoutS | 240 |
| ingestTimeoutS | **not set -> driver default 3600 s — flag: measured 39.3 s/session x ~48 sessions ≈ 31 min/env solo; under c8 contention an env can approach the 60-min cap. Mitigation: raise to 43200 (LME-M-style) via a settings patch, or accept and watch for ingest-timeout fatals** |
| importInstructions | absent (matches the dev5 improved_v2 baseline — S-family has never carried it) |
| model | dgx-glm53/glm-5.3 |

**Prerequisites:** E3-06 A/B confirmed (protocol 1.00, non-regressive) — DONE;
run-queue clearance (this is the cheapest full run, ~1.3 days at c8); fresh
output dirs. A 5-env mechanics smoke exists (`lme_s_full_smoke5_v1.json`,
envs 001be529..01493427) if a 15-min `--time-limit 900` shakedown is wanted
first.

**Launcher** (J's `scripts/lmem_parallel_shard_launcher.py`; NOTE: its default
shard glob is `lme_m_full_v1_s*.json` — pass the LME-S shard names explicitly or
it selects the wrong family):

```
# smoke (optional, ~15 min):
python scripts/lmem_parallel_shard_launcher.py --shards lme_s_full_smoke5_v1.json     --concurrency 1 --time-limit 900 --status-interval 60     --log-dir <dir>/smoke-logs --summary-json <dir>/smoke-summary.json

# full run (20 shards, explicit names):
python scripts/lmem_parallel_shard_launcher.py     --shards lme_s_full_v1_s00,lme_s_full_v1_s01,lme_s_full_v1_s02,lme_s_full_v1_s03,lme_s_full_v1_s04,lme_s_full_v1_s05,lme_s_full_v1_s06,lme_s_full_v1_s07,lme_s_full_v1_s08,lme_s_full_v1_s09,lme_s_full_v1_s10,lme_s_full_v1_s11,lme_s_full_v1_s12,lme_s_full_v1_s13,lme_s_full_v1_s14,lme_s_full_v1_s15,lme_s_full_v1_s16,lme_s_full_v1_s17,lme_s_full_v1_s18,lme_s_full_v1_s19     --concurrency 8 --retries 1 --status-interval 300     --log-dir <dir>/lmesfull/logs --summary-json <dir>/lmesfull/summary.json
```

**Recommended concurrency: 8** (J's validated guidance; 6 if the dev5
validations are still on the endpoint).

**Wall-clock**: **~33 h at c8 / ~44 h at c6** (receipt cost model: 262
driver-hours, 23,882 sessions, 500 envs, 39.3 s/session measured on dev5
improved_v2; c8 wall assumes continuous packing — 3 shard batches at ~13 h
each gives ~39 h worst case).

**Cost (tokens)**: receipt models input only: **~61.2M est ingest input tokens**
(244.9M chars / 4). Output is not modeled in the receipt; applying the M-family
measured output/input ratios (0.9-1.6x) gives **~55-100M output**. Question
stage: ~4M input / ~0.7M output (scratchpad included). Smallest of the three
runs by ~20x.

---

## 4. Verification results (task from the parent, verbatim)

1. **LP full manifests carry the Cognitive instruction (F's set): YES.** All 41
   shards + `loco_plus_full_improved_v3.json` + `loco_plus_full_smoke5_v3.json`
   carry the official INSTRUCTION_COGNITIVE text verbatim; all 41 shards are
   settings-uniform (prospective ON, expansion 2, extract/distill/rerank ON,
   pool 200, 20 entries / 24000 chars, evidenceTimestamps, importInstructions,
   dgx-glm53/glm-5.3) and pass K's launcher validation (401/401, 0 violations).
   Not carried: ANSWER-PROTOCOLS LP variant (c) (deliberate — official text
   instead), source spans (binary-gated), type router (unmerged).
2. **LME-M env manifests have the LME protocol: NO.** All 500 per-env files +
   20 shards + dev5 carry the old S-inherited instruction. Regeneration list +
   exact commands: §2 above (521 files: edit one constant, one converter run,
   one splitter run, one check).
3. **LME-S manifests have the protocol: YES** (variant (a), 20/20 shards +
   aggregate), with two flagged deltas vs the validated A/B: expansion ON
   (A/B ran OFF) and ingestTimeoutS default 3600.

## 5. Rebuild decision (applies to all three runs)

The sealed binary `ab1e1474` lacks the cue parser fix — mandatory for LP full —
and source spans. After the current wave (R1-R5) completes:

- **Option A (recommended)**: rebuild from improve/memory-iteration-3 HEAD
  (`ec917a26a`) — includes the cue fix + source spans (E3-05) + launchers.
  Accepts the unmeasured-but-tested source spans in the LP/LME full stacks
  (additive: extra search arm at 0.5 weight + "Source:" render lines). Re-run
  cargo tests (B's gates: 119 lib + 68 driver), then point `--binary` at the
  new build for all three full runs.
- **Option B (strict stack parity)**: build from `bdffcad50` + cherry-picked
  `b1df68a2d` only (cue fix without spans) — keeps the full-run stacks identical
  to their dev5 validations (LP dev5 v3 ran span-less), at the cost of a custom
  build and deferring E3-05's first measurement.
- Binary parity note for LME-M: the live dev5 M run (R4) uses `ab1e1474`
  (span-less). Whatever option is chosen, apply it to the full M run too, and
  record the build fingerprint in the run (the driver stamps
  `memory_feature_realization.build_fingerprint` per record either way).

## 6. Sequencing and total commitment

Order (parent's queue): current wave (R1-R5) -> cue-fix smoke (R6) -> rebuild
(§5) -> **LP full 401** (gate: dev5 v3 scores + smoke) -> **LME-M full 500**
(gate: dev5 M signal + §2 regeneration) -> **LME-S full 500** (gate: queue
clearance; cheapest, can slot into endpoint gaps).

Rough totals if all three launch: ~29-33 days of wall-clock at the recommended
concurrency each (they do NOT run simultaneously on one endpoint — sequence
them), ~1.7-1.9B input + ~1.4-1.7B output tokens (LME-M dominates ~85% of it).

| Run | Env/sessions | Concurrency | Wall-clock | Input tokens | Output tokens |
|---|---|---|---|---|---|
| LP full 401 | 401 / 10,899 | 6 | ~4.9-6 d | ~355M | ~280M |
| LME-M full 500 | 500 / ~237,500 | 20 -> 40 | 4.3-8.3 d | ~1.3-1.5B | ~1.1-1.4B |
| LME-S full 500 | 500 / 23,882 | 8 | ~33-39 h | ~61M | ~55-100M |

## 7. Risk register (short)

- **Cue coverage (LP)**: without the fix in the binary, LP full is a wasted
  5-day run. Gate R6 exists for exactly this.
- **LME-M protocol gap**: launching on the old instruction burns the largest
  token budget without the validated E3-06 improvement; regeneration is one
  constant + two commands.
- **ingestTimeoutS 3600 (LME-S)**: contention at c8 can time out an env late
  in ingest; watch FATAL lines or pre-raise the setting.
- **Expansion-ON combo (LME-S)**: protocol+expansion never ran together on S;
  cheap dev5 A/B if the parent wants it closed before 33 h of compute.
- **Concurrency >9**: unvalidated above 9 drivers; ramp rule in §2 applies to
  every run, not just LME-M.
- **Fresh-output rule**: any restarted env needs `rmdir /s /q <output_dir>`
  first; per-env sharding keeps the blast radius at one env.
