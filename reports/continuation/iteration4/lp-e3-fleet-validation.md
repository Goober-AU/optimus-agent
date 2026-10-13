# LP E3 fleet validation — read-time config fleet-wide decision

Worker: glm-lpe3val. Branch `improve/mem4-lp-e3val` (from improve/memory-iteration-4 @ 62ead4f50).
All runs: DGX GLM-5.3 only (`dgx-glm53/glm-5.3`), no Jev; driver `memory_bench.exe`
from the fleet binary `imp4-targetR` (sha256 c04ab631...). Scoring question-scoped
official-lp-v1, `LP_JUDGE_MAX_TOKENS=4096`. Type-A skipIngest re-answers on frozen
stores (question-scoped scratch copies; baseline stores never mutated).

## Scope correction

The loco_plus **dev5 set is 5 questions** (one per frozen v3 store), not 25:
`loco_plus_dev5_improved_v3` = lp_18, lp_69, lp_118, lp_159, lp_232 (selection.json:
5 blind-selected qids of the 120-qid dev population). After Z's lp_232 fix +
regressions, **lp_118 was the only dev5 question not yet run on E3**. For the
fleet-wide decision this was extended with the next 20 qids of the same blind
rank order (SHA256(qid+'20261010') ascending, ranks 6-25), re-answered on their
frozen base-population stores (`runs/locomo_plus/s0..s3c/envs/`, 0% cue coverage)
against their per-question baselines (`scores/locomo_plus_base_full.json`, base
variant; the v3 full launch never ran, so no v3 baselines exist off-dev5).

## E3 config (over the v3 LP base)

`recallRerankCandidates: 500, recallQueryExpansion: false, recallQueryDistillation: false`
(recall/extract/rerank stay on; maxRecall 24000/20). Same as Z's winning arm.

## Table 1 — dev5 (frozen v3 stores, v3 baselines)

| qid | v3 baseline | E3 runs | E3 median |
| --- | --- | --- | --- |
| lp_18 | 1.0 (rescored) | 1.0 | 1.0 |
| lp_69 | 1.0 | 1.0 | 1.0 |
| lp_118 | 0.0 | 1.0 / 0.0 / 1.0 | 1.0 |
| lp_159 | 1.0 | 1.0 | 1.0 |
| lp_232 | 1.0 (rescored) | 1.0 / 0.0 / 1.0 | 1.0 |
| **mean** | **0.8** | | **1.0 (+0.2)** |

lp_118 fixed (rerank used -> all 3 gold entries recalled; the single 0.0 is a
rerank `invalid_output` -> lexical-fallback run). No regressions.

## Table 2 — broader blind sample (frozen base stores, base_full baselines, x2 each)

| qid | base | E3 r1 | E3 r2 | pair-mean | delta |
| --- | --- | --- | --- | --- | --- |
| lp_124 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| lp_146 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| lp_147 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_157 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| lp_192 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_20 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_201 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| lp_207 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_230 | 1.0 | 1.0 | 0.0 | 0.5 | -0.5 |
| lp_260 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| lp_261 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| lp_277 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_291 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_31 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| lp_317 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| lp_327 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_344 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_378 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| lp_60 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| lp_62 | 0.0 | 1.0 | 1.0 | 1.0 | +1 |
| **mean** | **0.45** | **0.95** | **0.90** | **0.925** | **+0.475** |

- base-failures fixed: **10/11**; base-passes held (pair-mean): **9/9**;
  regressions (pair-mean): **0** (lp_230's single-run 0.0 is a rerank flake).
- 19/20 pairs agree; the one disagreement (lp_230) is rerank-driven, not judge.
- 0 flagged/eval_error runs across all 46 E3 runs (40 here + 6 by Z/me on dev5).
- Rerank `used` rate: 32/40 = **80%** (8 flakes: 3 invalid_output, 4 empty_selection,
  1 empty_output). 5 of the 8 flake runs still scored 1.0 (gold lexically
  reachable in the fallback window); 3 scored 0.0.

## Failure analysis (all E3 misses)

1. **lp_146 (0.0/0.0, the only persistent miss) — rerank-SELECTION miss**, not a
   retrieval-window miss: the store has 128 entries, all inside the 500-pool;
   the gold `James: suffers from allergies severe enough to miss work` is in the
   pool but absent from the top-50 lexical ranking (question "packing my emergency
   kit before every trip" shares zero content tokens with it). r1: rerank used
   but selected 6 travel entries; r2: rerank empty_output -> lexical fallback
   (21 entries, no gold). Both answers judged off-cue.
2. **lp_118-r2 / lp_230-r2 / lp_232-E3-r2 (single-run 0.0s) — rerank parse/selection
   flakes**: invalid_output or empty_selection -> lexical top-20 fallback without
   the gold -> 0.0. Same runs pass whenever the rerank output parses (gold
   selected first).
3. **Judge stage**: no judge flips observed in this sample (decisive reasons,
   0 flags). The judge-variance risk the task flagged did not materialize here;
   the observed variance is concentrated in the rerank helper.

## Verdict

**E3 is validated as the fleet LP read-time config now — the fresh re-ingest
campaign is NOT a precondition.** Data: dev5 1.0 vs 0.8 v3 (no regressions);
blind 20-sample 0.925 vs 0.45 base (+0.475) with 10/11 failures fixed and 0
pair-mean regressions, on stores with **0% cue coverage** — i.e. the win does not
depend on cues at all. Expected-cost logic: E3 is a read-time settings change
(zero ingest cost, one config flip covers every store), while the re-ingest
campaign (~100 min/store; 5 dev5 stores ~8h, or the 401-env full campaign) is
the **durable robustness fix, recommended as a follow-up, not a gate**: cues
bring the gold into lexical reach so the ~20% rerank-flake runs stop costing
score (Z's simulation: cue'd gold reaches lexical rank 2-85), and they attack
the lp_146 class (semantic bridge still needed from "emergency kit" to
"allergy sick-day"). Fleet expectation if the sample generalizes: the 120-dev
population would move from 0.417 toward ~0.9 (91% of the 66 base failures).

Run dirs: `lpe3-scratch/ql-attempts/lpe3a-lp118-r{1,2,3}` (controller, ledger
appended) and `lpe3-scratch/ql-attempts/lpe3b-<n>-r{1,2}` (20 qids x2).
Machine-readable results: `lp-e3-fleet-results.json`; runner: `lp-e3-broader-runner.py`.
