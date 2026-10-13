# lp_232 diagnosis — LoCoMo-Plus dev5 v3 failure (goal, official-lp-v1)

Worker: glm-lpq232 (round 4, question queue `regression`). Branch `improve/mem4-lp-q232`.
All runs: DGX GLM-5.3 only (`dgx-glm53/glm-5.3`), no Jev, driver `memory_bench.exe`
from the fleet binary `imp4-targetR` (sha256 c04ab6318ee7008b212e16bd539dfafd250d2390a529219140693a2b73a6a941,
contains the ad5106de6 recall-helper budget raises; predates 4540f8ac1 content-aware rerank).
Scoring question-scoped official-lp-v1, `LP_JUDGE_MAX_TOKENS=4096`.

## Question

`lp_232` (`goal|rank_tercile_1`): "John: Even with all my efforts, I still feel guilty
every time I order takeout." The judge's evidence cue is John's commitment to reducing
his carbon footprint this year; a correct continuation connects the takeout guilt to
that environmental goal.

## Frozen store state (task 1)

- Store: `envs/lp_232` of `loco_plus_dev5_improved_v3`, 1092 memory entries, single
  project `project_b1b38a31cab5c77a094331ce`.
- Cue coverage: **158/1092 = 14.5%** of entries carry `metadata.prospectiveCues`
  (lp_118 frozen: 15.9%; lp_18 16.1%, lp_159 14.4%, lp_69 12.2% — the whole dev5
  batch was ingested before the cue-budget fix).
- The gold `john-carbon-footprint-goal` (source `m_0021:0`, import_533809f96d40a4f261864b1e)
  has **NO cues and no sourceSpan**. The store's refinement history shows the cue pass
  ran on only 32/64 import jobs and indexed only ~5 entries per job — the cue-budget
  starvation class fixed later on the driver path (512 -> 2048, commits c3736613d/
  b08efe9f7, "99% coverage proven"); the v3 run predates the effective fix.
- Question↔gold lexical overlap: the single token `john` (df 839/1092, idf 1.26).
  `df(takeout)=df(guilt)=0` across the whole store; `carbon`/`footprint` appear in
  only 2 entries (the gold + `maria-carbon-goal-reaction`). No cue anywhere mentions
  carbon, footprint, takeout or guilt.

## Search-path reading (task 2a)

`crates/pi-coding-agent/src/core/memory/search.rs`:
- `prospective_cue_words()` (line 209) folds cue words into the body set; they score
  like body words (weight 1.0 vs title 3.0, span 0.5) — line 291.
- There is **no cue-weight setting**: `recallProspectiveIndex` (store.rs:92) is a
  write-time boolean for cue generation, not a weight. Task 3's weight-bump branch is
  therefore inapplicable.

Python replication of the scorer matches the runtime `search_ranked` byte-for-byte
(top-3 scores identical to 6+ decimals). Gold position under the raw question:
**rank 422/935, score 0.0570, matched=[john]** — below the top-50 report and below
the default 200-wide rerank pool. This confirms R's gate finding.

## Read-time hypotheses (3 tried, tasks 2b/2c)

All arms type-A on a private copy of the frozen store (skipIngest, no re-ingest),
official-lp-v1 judge, 4096 judge tokens.

| arm | settings delta vs v3 | rerank outcome | gold recalled | score |
| --- | --- | --- | --- | --- |
| gate4a x5 (R) | none (pool 200, helpers on) | empty_selection x5 | no | 0.0 x5 |
| gate4a-nohelp x2 (R) | helpers off | disabled | no | 0.0 / 1.0 (judge luck) |
| E1 pool500-lp | `recallRerankCandidates: 500` | empty_selection | no | 0.0 |
| E2 pool500-noexp | pool 500 + `recallQueryExpansion: false` | empty_selection | no | 0.0 |
| **E3 pool500-raw** | pool 500 + expansion off + distillation off | **used** (1388 tok) | **yes, rank 1** | **1.0** |
| E3-r2 | same | invalid_output (1535 tok) | no (lexical fallback) | 0.0 |
| E3-r3 | same | used (1680 tok) | **yes, rank 1** | **1.0** |

1. **H1 (cue-weight bump): not applicable** — the gold has no cues, so no weight
   change can touch it, and no weight knob exists anyway.
2. **H2 (query-extract expansion angles): refuted** — E1 kept the LP helper stack and
   only widened the pool; expansion still surfaced the food-drive cluster and the
   judge rejected the off-topic weave ("focuses entirely on unrelated content — a
   food drive"). Same as gate4's 0/5.
3. **H3 (rerank-pool widening + deterministic query): the win** — with the 500-cap
   pool (validation bound 1..500, store.rs:317) the gold at lexical rank 422 enters
   the rerank candidate list; with the raw question as the rerank query GLM-5.3
   bridges takeout-guilt → carbon-footprint and selects the gold first (2/2 when the
   rerank output parses). Distillation ON (E2) made the rerank return an empty
   selection — the distilled query loses the phrasing that makes the bridge
   inferable.

Winning read-time config (over the v3 LP base): `recallRerankCandidates: 500,
recallQueryExpansion: false, recallQueryDistillation: false` (recall, extract, rerank
stay on; maxRecall 24000/20). Pass rate 2/3; the single miss is a rerank
`invalid_output` (JSON parse flake at 1535/4096 output tokens), not a semantic
refusal — same GLM-5.3 rerank reliability class lp_118 documented
(used/empty/invalid mix). Gold-in-recall tracked every outcome: both passes recalled
the gold first; the miss fell back to lexical top-20.

## Regression (task 5, winning config)

| qid | v3 baseline | winning config |
| --- | --- | --- |
| lp_18 | eval_error (judge; cuefix re-smoke 1.0) | **1.0 correct** (rerank used, 2-entry recall) |
| lp_159 | 1.0 | **1.0 correct** (rerank used, 2-entry recall) |
| lp_69 | 1.0 | **1.0 correct** (rerank used, 2-entry recall) |

No regressions. Run dirs: `lpq232-scratch/ql-attempts/lpq232-reg-*`.

## Re-ingest recommendation (task 4)

A fresh re-ingest (the lp_118 fix class) is **justified as the durable fix**, but the
read-time win makes it optional for lp_232 itself:

- Simulation on the frozen store: giving the gold a cue containing "guilty … takeout"
  lifts it to lexical rank 2/935 (direct recall, no rerank needed); softer
  differently-worded cues still reach rank ~85 (inside even the default 200 pool).
  Cues make the question robust to the rerank's empty/invalid flakiness.
- Cost: ~100 min per store re-ingest; the parent should schedule it together with
  the LP full-launch decision (all five dev5 stores sit at 12-16% cue coverage, so
  one re-ingest campaign fixes the class, not just lp_232).
- Do NOT treat the E3 read-time config as a judge fix: the LP judge still flips on
  cue-ignoring empathy answers (gate4 nohelp-r1 'wrong' vs r2 'correct' on identical
  recall). Gold-in-recall is the durable gate; E3 delivers it 2/3.

## Judge note

`LP_JUDGE_MAX_TOKENS=4096` used everywhere (the 512 default produced the ledger's
eval_errors). Judge latency 2-24s per call; no empty-content judge outputs in these
12 attempts.
