# LongMemEval-S FULL (500) — Run-Input Infrastructure

Worker: lme-s-full (worktree `imp3-lmesfull`, branch `improve/mem3-lmes-full`, base = imp3 HEAD `4c87c9e78`).
Date: 2026-10-12. Offline only: no model, no network, no Jev, no reference-repo execution.

## 1. What exists now

- **Generator:** `bench/converters/longmemeval_s_full.py` (this worktree; stdlib-only).
- **Manifests (22 files)** in `<campaign>/reports/continuation/iteration3-inputs/manifests/`:
  - `lme_s_full_smoke5_v1.json` — smoke: first 5 qids (001be529..031748ae).
  - `lme_s_full_v1.json` — one combined manifest, all 500 envs / 500 questions.
  - `lme_s_full_v1_s00.json` .. `lme_s_full_v1_s19.json` — 20 shards of 25 envs, each an
    independent driver run (own run_id + output_dir) so the full set can run as parallel
    single-process workers; scoring merges the per-shard run.jsonl files.
- **Receipt:** `imp3-lmesfull/reports/lmesfull/manifests-receipt.json` (paths + sha256 + counts
  + cost model).
- Run outputs land in `<campaign>/reports/continuation/iteration3-inputs/runs/<run_id>/`
  (all target dirs verified fresh/nonexistent at generation time).

## 2. S-haystack structure (official repo, static read)

Verified from the official LongMemEval repo clone
(`optimus-memory-references/longmemeval-official/README.md`, static inspection only):

- `longmemeval_s.json` — the **S variant: per-question session subset**, ~40 history
  sessions average, ~115k tokens concatenated per question (Llama 3 tokenizer);
- `longmemeval_m.json` — the **M variant: ~500-session full haystack** (~1.2M est. tokens);
- `longmemeval_oracle.json` — evidence sessions only;
- each file holds **500 evaluation instances** with question_id / question_type /
  question / answer / question_date / haystack_session_ids / haystack_dates /
  haystack_sessions (turns with role+content; evidence turns carry `has_answer: true`) /
  answer_session_ids;
- **cleaned variants**: the 2025/09 revision `xiaowu0162/longmemeval-cleaned`
  (`longmemeval_s_cleaned.json` etc.) re-cleaned history sessions to prevent
  interference on answer correctness; results on older circulating copies are not
  comparable (official change log).

Local campaign pin: `data/longmemeval_s.json` is the cleaned S file (sha256
`08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894`, pinned in the
longmemeval-comparison-inputs PREP receipt; 278.0 MB on disk vs the official 277.4 MB
listing; the sibling `longmemeval_m.json` matches the cleaned M byte size exactly and
A's M converter documents it as the cleaned variant).

Measured structure of the 500 environments (rebuilt from the raw file):
- 500 questions, 500 unique qids; categories: multi-session 133, temporal-reasoning 133,
  knowledge-update 78, single-session-user 70, single-session-assistant 56,
  single-session-preference 30; 6 injected-decoy abstention items (`_abs`).
- Haystack sessions per env: min 39 / max 66 / mean 50.2 (mean 47.8 after the adapter's
  empty-session skip; 23,882 sessions ingest in total).
- Mean ~489.7k chars (~122k est. tokens) per env; 244.9M chars (~61.2M tokens) total.

## 3. Verification performed (all fail-closed, in the generator + independently)

1. Raw dataset: 500 rows, unique qids, aligned haystack arrays, pinned sha256.
2. Env contract: 500/500 rebuilt envs pass chronology (ts-sorted sessions), roles,
   nonempty events, ground_truth_refs resolving to event ext_ids.
3. Canonical set cross-check: 500/500 rebuilt envs deep-equal
   `data/common/longmemeval/full/env_<qid>.json`; scoring questions equal on all
   content fields (qid/env_id/question/answer/evidence/category/abstention/
   question_date). The pinned common qs rows are legacy-format (they predate the
   2.0.0 protocol annotations, as documented in the longmemeval-comparison-inputs
   README), so the sanctioned comparison is env bytes + question content fields —
   the same rule used when the dev5 manifests were staged.
4. Planning-manifest cross-check: per-row n_sessions / n_msgs / chars / category /
   abstention / evidence all agree with the raw-dataset rebuild.
5. dev5 replica: rebuilding the five dev5 envs + question declarations from the raw
   dataset and diffing against `longmemeval_dev5_improved_v2.json` is **EXACT** — env
   lists byte-equal (order + content), question declarations byte-equal including
   question_sha256.
6. Schema: every emitted manifest has the same key sets as the dev5 improved_v2
   reference at manifest / env / session / event / question level (smoke5 + all 20
   shards + combined, validated in-generator and re-validated independently).
7. Coverage: shard union = 500 unique qids = combined set; no cross-shard overlap;
   smoke5 = first five qids; the five dev5 qids are included.
8. Declarations: all 500 unique gold-free declarations EXACT vs a fresh
   raw-dataset rebuild; env_id == qid pairing everywhere.
9. Output dirs: 22 distinct, all fresh; worst-case driver scratch path 248 chars
   (< 260, LongPathsEnabled=0 host).

## 4. Settings + model

Settings = dev5 improved_v2 (`longmemeval_dev5_improved_v2.json`, the arm that scored
1.00 on dev5 with the official judge; the earlier improved_v1 rescored 0.80 with
helpers off) **verbatim** — recallOn, recallQueryDistillation, recallRerank,
recallRerankCandidates 200, maxRecallChars 8000, maxRecallEntries 8,
maxExtractionTokens 32000, maxImportChunkChars 15000, disableTools, autoRefine off,
questionTimeoutS 240, skipIngest false — **plus** the iteration-3 upgrades:

- `recallQueryExtract: true` (question-only recall query; required pairing for the
  protocol instruction),
- `recallQueryExpansion: false` — **OFF for strict E3-06 A/B parity** (regenerated
  2026-10-12 by imp3-lmemreg; the original set carried true + 2 draws): the A/B that
  validated the protocol (1.00, 5/5, non-regressive, official-lme-v1 judge) ran with
  extract ON and expansion OFF, and expansion has never been measured on the S family
  in any arm. The draws key is dropped, exactly matching the A/B manifest. The LME-M
  set keeps expansion ON (residual risk accepted in FULL-RUN-DECISION.md section 2);
  S deliberately differs so the full 500-q run executes the exact validated
  answer-stage stack,
- `answerMaxTokens: 8192`,
- `ingestTimeoutS: 43200` — FULL-RUN-DECISION.md mitigation for the c8-contention
  timeout risk (measured 39.3 s/session x ~48 sessions ~= 31 min/env solo vs the
  3600 s driver default; LME-M-style; the frozen-store A/B never exercised ingest,
  so this does not affect its parity),
- `answerInstruction` = **LME answer protocol variant (a)** from
  `reports/continuation/iteration3-research/ANSWER-PROTOCOLS.md` (144 words:
  latest-"at"-wins supersession, visible date scratchpad, count enumeration,
  entity-mismatch guard, current-value-first, premise check, "available memory does
  not record it" abstention style).

17 settings keys total. Model dgx-glm53/glm-5.3 only. Seed 20261010 (reference seed).
captureProtocol `optimus-memory-capture/temporal-authority/1.0.0`, benchmark
`longmemeval`, variant `improved` — identical to the reference.

## 5. Cost model (measured, ingest-dominated)

Anchor: the dev5 improved_v2 run (same S haystacks, distill+rerank arm, GLM-5.3) —
243 sessions ingested in 9548.3 s = 39.3 s/session (1909.7 s/env, ~32 min); questions
averaged 6.5 s.

- Ingest: 23,882 sessions x 39.3 s = ~938,400 s = ~260.7 driver-hours.
- Questions: 500 x ~12 s (dev5 6.5 s + expansion call + protocol-(a) scratchpad
  ~+500 output tokens, per the ANSWER-PROTOCOLS validation) = ~1.7 driver-hours.
- **Total: ~262 driver-hours**; wall-clock ~44 h at concurrency 6, **~33 h at
  concurrency 8** (20 shards = 2.5 waves x ~13.2 h/shard), assuming per-driver
  throughput holds (A's LME-M launcher validated 8-9 concurrent drivers; recalibrate
  from the first-hour status table).
- Disk: ~29 MB/env measured on dev5 v2 -> ~14.4 GB scratch for the full run.
- Abstention: 30 qids carry the official `_abs` suffix; the campaign's stricter
  decoy rule (suffix + single-session-user + `_abs` answer session) flags 6, landing
  in s00/s01/s02/s03/s12/s15 — spread, no dedicated shard.

## 6. How to emit / re-shard

From the worktree root (`imp3-lmesfull`), any stdlib Python 3:

```
python -B -m bench.converters.longmemeval_s_full     --campaign-root <campaign> --single --shard-size 25 --smoke 5
```

- `--shard-size N` re-shards (500/N rounded up); `--single` the combined manifest;
  `--smoke N` the first-N smoke manifest; `--dry-run` validates only.
- The script refuses to overwrite existing manifests; the receipt lands in
  `reports/lmesfull/manifests-receipt.json`.
- Defaults: campaign root = worktree parent; reference manifest/settings =
  iteration2-inputs/manifests/longmemeval_dev5_improved_v2.json; out dir =
  iteration3-inputs/manifests; runs root = iteration3-inputs/runs.

## 7. Launch (driver) and scoring

Binary: built from THIS worktree (`cargo build --release --example memory_bench
-p pi-coding-agent`, Rust env per campaign convention). Launch one driver per shard
with the frozen models config (see `scripts/lmem_parallel_shard_launcher.py` for the
parallel wrapper; `--shard-glob` generalization exists in `lp_full_shard_launcher.py`):

```
MEMORY_BENCH_MODELS_JSON=<models.json> <worktree>/target/release/examples/memory_bench.exe     <manifests>/lme_s_full_v1_s00.json
```

Scoring: merge the per-shard run.jsonl files (qid-unique concatenation), then the
standard `bench.eval.evaluate --bench longmemeval` pass against the full question set
(`data/common/longmemeval/full`); judge glm-5.3 via DGX.

## 8. Validation summary

- Driver parse validation: `reports/lmesfull/driver-parse-validation.txt` — binary
  built from THIS worktree (12m19s, exit 0), MEMORY_BENCH_MODELS_JSON deliberately
  unset; **22/22 manifests exit 2 at the provider-config gate** AFTER all
  manifest-side checks, per the iteration-2 convention. Isolation skeletons
  cleaned; all 22 output dirs are fresh/nonexistent again.
- The benchmark run itself was NOT launched (this worker's scope ends at validated,
  committed run inputs).

## 9. Manifest inventory (sha256 in `manifests-receipt.json`)

| file | envs | questions |
|---|---|---|
| lme_s_full_smoke5_v1.json | 5 | 5 |
| lme_s_full_v1.json | 500 | 500 |
| lme_s_full_v1_s00..s19.json (20 files) | 25 each | 25 each |

Total 22 files, 589.6 MB. Shard qid ranges: s00 = 001be529..0bb5a684 ... s19 =
gpt4_b5700ca9..gpt4_fe651585_abs (globally sorted-qid order, contiguous
non-overlapping 25-env slices).

## 10. Regeneration (2026-10-12, worker imp3-lmemreg)

Regenerated from the same pinned dataset and code lineage (worktree
`imp3-lmemreg`, branch `improve/mem3-lmem-protocol`, base = improve/
memory-iteration-3 HEAD `baa0f4449`) to apply the two O-flagged deltas from
FULL-RUN-DECISION.md section 3:

- `recallQueryExpansion` true(2 draws) -> **false** (strict E3-06 A/B parity; see
  section 4 above for the full justification),
- `ingestTimeoutS` absent (driver default 3600) -> **43200** (c8 timeout-risk
  mitigation).

Verification of the regenerated set (all 22 files): protocol (a) verbatim
22/22, expansion false 22/22, draws key absent 22/22, ingestTimeoutS 43200
22/22; old-vs-new JSON diff shows ONLY the three settings deltas above -
envs, questions, run_ids, output_dirs, model and seed are identical to the
original set. The receipt (`manifests-receipt.json`) was rewritten with the
new sha256s and settings note; the driver-parse validation of section 8 was
performed on the original emission and the settings deltas are all
pre-validated driver keys (the LME-M full set runs ingestTimeoutS 43200 and
the E3-06 A/B ran recallQueryExpansion false), so it was not repeated.

