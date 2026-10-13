# lp_118 diagnosis — LoCoMo-Plus dev5 v3 failure (state, official-lp-v1)

Worker: glm-lpq118 (round 4, question queue `evidence`). Branch `improve/mem4-lp-q118`.
All runs used DGX GLM-5.3 only (`dgx-glm53/glm-5.3`), no Jev, driver `memory_bench`
from the shared iteration-4 build `imp4-targetA/release/examples/memory_bench.exe`
(sha256 03ced5d131da628248a36ce7479d7802de98a0539630b19ee20573b8ee9ec770,
imp4 HEAD 874a67e3d = origin/main with PR #136 merged).

## What was asked (diagnosis only)

`lp_118` (`state|rank_tercile_1`): the speaker says the house is quiet now that
"him" is no longer around. The official Cognitive judge (profile `official-lp-v1`)
checks whether the answer links to the cue dialogue: Evan's son graduated the
month before the cue conversation and Evan felt proud. A correct continuation
acknowledges the son's graduation / the empty-nest change.

## Frozen run evidence (reports/.../runs/loco_plus_dev5_improved_v3, old sealed binary)

- v3 answer was tool-intent narration ("I'll first inspect the workspace ...")
  -> judged `wrong`, score 0.0.
- Recall: 20 entries / 8038 chars (lexical top-20). The three gold entries
  (`mem_evan_son_graduated`, `mem_evan_proud_son_graduation`,
  `mem_sam_graduation_reaction`) exist in the frozen store (572 entries) but
  were NOT recalled; they are absent even from the top-50 lexical ranking
  (`search_ranked`). The question shares only the token "Evan" with them.
- Recall helper observations in the same row: distillation `empty_output`
  stop=length 64/64 tokens; expansion `empty_output` 256/256; rerank
  `empty_output` 512/512 (old-binary rerank budget was 512).

So the needed fact is IN the store (extraction worked) but unreachable by the
lexical recall window; this is a retrieval miss, not a B-extraction miss.

## Arms (Type A: skipIngest, private copy of the frozen lp_118 store, cue-fixed binary)

Settings identical to the v3 manifest (recall helpers on, rerank pool 200,
maxRecall 24000/20), `skipIngest=true`, `answerInstruction` = the official
Cognitive instruction (sha256 753f3a7e9478405578db53cbbaa0c0c20788212740795255a4f26df2cec9541b;
verbatim text not copied into this repo). Arm "nonarr" appends the addendum
"Answer directly with the final answer only — never narrate your reasoning or
describe what you will do." (sha256 4407c1cdc0ab4011e5749a0b81f21dc5beff0854134188fc6727d5958601e9ac).

| run | instruction | rerank outcome | gold recalled | score (official-lp-v1) |
| --- | --- | --- | --- | --- |
| v3 (old binary) | official | empty 512/512 | no | 0.0 wrong (narration) |
| lpq118_v4A_base | official | empty 2048/2048 | no | 0.0 wrong |
| lpq118_v4A_base_r2 | official | used 1166 | **yes** (2 entries) | **1.0 correct** |
| lpq118_v4A_base_r3 | official | used 1292 | **yes** (2 entries) | **1.0 correct** |
| lpq118_v4A_nonarr | +addendum | invalid 1585 | no | 1.0 correct (lenient judge: answer grounded on the soccer-accident memory, not the cue) |
| lpq118_v4A_nonarr_r2 | +addendum | invalid 856 | no | 0.0 wrong |
| lpq118_v4A_nonarr_r3 | +addendum | used 1773 (picked non-gold) | no | 0.0 wrong |

Distillation (64 tokens) was `empty_output` in every run; expansion (256 tokens)
was empty/invalid except one `used` (250 tokens) whose angles surfaced other
son-related entries but not the gold.

## Verdict on the hypotheses

1. Binary delta alone: REFUTED — same recall as v3 when the rerank helper
   fails; score 0.0.
2. No-narration addendum (G): REFUTED as the fix — 1/3 vs 2/3 across arms is
   noise; correctness tracks gold-in-recall, not instruction style. The v3
   narration itself was stochastic: 5/6 re-runs with the UNCHANGED official
   instruction produced direct conversational answers.
3. Root cause: the gold fact is lexically unreachable (rank > 50; only shared
   token "Evan") and the only helper that can lift it out of the 200-wide pool
   (recall rerank) is unreliable on GLM-5.3 because hidden reasoning consumes
   its token budget before any visible output. Observed rerank outcomes across
   6 runs on the new binary: used at 1166/1292/1773 tokens (2 of those selected
   the gold entry -> both scored 1.0), empty at 2048/2048, invalid at 856 and 1585.

## Regression check (addendum, frozen stores)

| qid | v3 baseline | with addendum |
| --- | --- | --- |
| lp_18 | eval_error (judge; cuefix re-smoke 1.0 twice) | 1.0 correct |
| lp_159 | 1.0 | 1.0 correct |
| lp_69 | 1.0 | 1.0 correct |

No regressions from the addendum; it is safe but not sufficient for lp_118.

## Recommended general fix (not applied here; no rebuild allowed under the
shared-binary policy)

Raise the recall-helper token budgets in
`crates/pi-coding-agent/src/core/extensions/builtin/memory.rs`
(distillation 64.0, expansion 256.0, rerank 2048.0 today) to reasoning-proof
values (e.g. 2048/2048/4096): the cue generator already proved 2048 is the
GLM-5.3 viability threshold, and rerank used up to 1773 tokens on success but
exhausted 2048 once. Operational alternative validated for lp_18: re-ingest
stores with the cue-fixed binary so gold entries carry prospective cues and
become lexically reachable (cuefix smoke = 1.0). The lp_118 fresh re-ingest
arm is `lpq118_v4A_fresh` (result appended below when complete).

## Judge note

`official-lp-v1` judge calls on GLM-5.3 need `LP_JUDGE_MAX_TOKENS=4096`
(default 512 produces empty-content eval_errors, matching the ledger's
lp_18/lp_232 EVAL_ERROR notes). One judge call still returned empty content at
4096 and scored 0.0/wrong on retry; judge output is itself somewhat stochastic
(one lenient `correct` on an answer with no gold recall).
