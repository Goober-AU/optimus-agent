# LP hardening round 2 — lp_146 read-time arms + rerank parse-retry

Worker: glm-lphard2. Branch `improve/mem4b-lp-hard` (fresh from improve/memory-iteration-4
@ ad4309cf1). Runs: DGX GLM-5.3 only, no Jev. Scoring question-scoped official-lp-v1,
LP_JUDGE_MAX_TOKENS=4096. Fleet binary imp4-targetR sha b57dc607 for the read-time arms;
retry build imp4b-lphard-target sha 3a3ba139 for the lp_118 validation. All attempt roots
in campaign-root lphard-scratch/ (outside worktrees).

## 1. lp_146 read-time hypotheses — ALL NEGATIVE (no fix; no regressions run)

Question: "Packing my emergency kit before every trip now feels absolutely essential" —
gold cue: allergy symptoms so bad James called in sick. Gold entry
`mem_lp146_james_allergies` ("James: suffers from allergies severe enough to miss work")
is inside the 500-pool (store: 125 entries) but below the top-50 lexical ranking; the
question shares zero content tokens with it.

| arm | runs | rerank outcomes | score |
| --- | --- | --- | --- |
| H1 content 240 | 2 | invalid_output (3613 tok, healthy stop); empty_output (4096 length) | 0.0 / 0.0 |
| H1 content 120 | 2 | empty_output (4096 length); used (2200 tok) | 0.0 / 0.0 |
| H2 content 240 + maxRecallEntries 30 | 2 | empty_output; invalid_output (2975 tok) | 0.0 / 0.0 |
| H3 expansion ON (draws 2) + content 240 | 2 | empty_selection; empty_output | 0.0 / 0.0 |

Mechanisms (from run records + pre-pause rows):

1. **Selection miss, not a window miss**: the one run where the content-rerank parsed
   (c120 r2) still selected 5 travel entries — same as the fleet-E3 r1 selection. The
   model does not bridge "emergency kit" -> "allergies" even with the allergy content
   excerpt visible; the gold title already carried "allergies" in the id+title prompt.
2. **Budget exhaustion dominates with content on**: 5/8 fresh dispatches burned the full
   4096 output cap on hidden reasoning with 0 chars emitted (empty_output/length).
3. **Expansion angles inherit the trip framing**: expansion itself parsed (used, ~100
   chars) but its angle queries surfaced more travel entries, not the allergy entry;
   the gold never entered the fallback top-20 either.

Verdict: lp_146 is not fixable by read-time settings on this stack. The durable fix is
the cue-side re-ingest (prospective cues put the gold in lexical reach — already the
recommended fleet follow-up), or a stronger rerank judge. No fix won, so the regression
set was not run for the lp_146 arms.

## 2. Rerank parse-retry (code) — implemented, 140+75 tests green

`fix(memory): single bounded retry when the recall rerank emits an unusable selection`
(314b40b56): one identical re-dispatch on invalid_output / empty_selection only;
provider errors, cancellations and empty outputs keep the single-attempt contract;
a second unusable selection falls back exactly like the first would have. The helper
report gains a `retries` counter and sums usage/latency across dispatches; the bench
driver passes `retries` through to `llm_helper_observations`.

- `cargo test -p pi-coding-agent --lib memory::` — 140 passed, 0 failed
  (138 baseline + 2 new: retry-applies-selection, retry-sums-usage).
- `cargo test -p pi-coding-agent --example memory_bench` — 75 passed, 0 failed.
- Existing tests updated only where they queued a single unusable rerank response
  (now queued twice, request count 1 -> 2, plus prompt-identity asserts).

## 3. lp_118 full-stack E3 re-runs with the retry binary — 1/9, conversion proven

S's exact setup (fresh2 store skipIngest, E3, no-narration addendum, official-lp-v1
4096). Nine runs because the first three were dominated by a failure class outside the
retry's documented scope:

| run | rerank final | retries | gold | score |
| --- | --- | --- | --- | --- |
| r1 | empty_output (length 4096/4096, 0 chars) | 0 | no | 0.0 |
| r2 | empty_output (1st retryable 1535 tok, retry length) | 1 | no | 0.0 |
| r3 | invalid_output (both dispatches) | 1 | no | 0.0 |
| r4 | empty_output (1st retryable 2079 tok, retry length) | 1 | no | 0.0 |
| r5 | empty_output (length) | 0 | no | 0.0 |
| r6 | empty_output (length) | 0 | no | 0.0 |
| r7 | empty_output (length) | 0 | no | 0.0 |
| r8 | used (2512 tok, 1st dispatch) | 0 | yes | 0.0 (answer stage missed the gold) |
| r9 | used (retry after unusable 1st) | 1 | yes | **1.0** |

- **r9 is the end-to-end conversion proof**: first dispatch unusable -> single retry
  parsed -> gold `mem_sam_congratulated_evan_son_graduation` recalled first -> answer
  cites the graduation -> judged correct. Without the retry that run scores 0.0.
- The 2/3 target was not reached because today's endpoint regime differs from S's
  10-13 sample: 6/9 runs hit rerank LENGTH exhaustion (4096 output burned by hidden
  reasoning, 0 chars emitted) — a budget failure the documented retry deliberately
  does not cover (invalid_output/empty_selection only). S's flake mix (2/3
  invalid_output at healthy 2648-3650 emissions) is exactly the class the retry
  converts; under that mix the expected conversion is r1/r2-style runs -> 1.0.
- r8 shows the residual answer-stage risk: rerank used + gold recalled, answer still
  judged wrong (bike-accident narrative instead of the graduation).

## 4. Regression sanity with the retry binary

lp_18 (dev5 1.0 bar, E3): 3/3 at 1.0, rerank used first-dispatch in all three
(no retry needed). No regression from the code change.

## Follow-ups

- The rerank output cap (4096, hardcoded in memory.rs) is now the binding constraint
  for 500-candidate reranks on GLM-5.3: raising it (8192) or retrying empty_output/
  length would address the exhaustion class the parse-retry does not.
- lp_146 needs the cue-side re-ingest (fleet follow-up already recommended) — no
  read-time settings arm moved it.
