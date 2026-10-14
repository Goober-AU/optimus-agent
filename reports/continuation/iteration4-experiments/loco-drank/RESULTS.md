# loco-drank: D-class window lane on fresh stores (gold in store, outside the 24-entry window)

Worker `drank` | worktree `imp4b-drank` (branch improve/mem4b-drank, base 85c63983a = imp4 HEAD post
blank-rescue merge) | model dgx-glm53/glm-5.3 (only) | driver `imp4b-target8k` memory_bench.exe (sha256
4d2ba13f, verified) | controller `scripts/memory_question_loop.py` (upstream, unchanged ba8ebd6a0..HEAD).
Stores: writecamp's 7 warm typeB caches (keys in writecamp SESSION-STATE.json) + conv-26 fresh
`d4b542a8e98deca6` (176 entries, locofull ingest); all runs warm cache-reuse (skipIngest copy-out), cache keys
re-verified by recomputing sha256(env_spec, importInstructions, extraction, model) before launch.
Settings: FINAL9 answer + recallQueryStemming + window arm (writecamp `fullstack-final9-stem.json` family);
conv-26 battery uses the locofull G4-family (`fullstack-write-read.json` + stem + window) to match AA's trap
measurement family. Attempts: reports/continuation/iteration4/ql-attempts-drank (87 H1/H3 runs, run-ids <= 20).

## Task 1 - rank distributions (offline search_ranked replay; search_replay.py, verified 50/50 vs binary)

The writecamp's 6 "ranking misses" decompose under exact replay into 2 true deep-rank misses + 4 answer-stage:

| qid | gold best rank (fresh store) | w32 admits gold? | verdict |
|----|----:|----|----|
| conv-42_q151 | 60 (nate-time-off-belief) | no | TRUE deep-rank miss (query "celebrate winning" has zero lexical overlap with "time off to chill with pets"; matched = [how, nate] only) |
| conv-44_q24 | 50/67/166 (3 carriers) | no | TRUE deep-rank miss (12+ current-dog notes outrank the childhood/family-dog notes) |
| conv-42_q20 | 1 (nate-adopted-dog-max) | (already in) | answer-stage: note has no date; "not mentioned" persists at every window |
| conv-47_q34 | 1-2 (canada/nuuk notes) | (already in) | answer-stage: "Nuuk" vs gold "Greenland" naming |
| conv-47_q138 | 1 (witcher3-inspired-game) | (already in) | answer-stage: padding |
| conv-50_q10 | 2 (calvin-home-flood) | (already in) | answer-stage: date form |

Population sweep over ALL 144 ledger-FAILED questions on the 8 fresh stores (evidence D-refs mapped to
store entries via import sourceIds): best gold-carrier in top-24 = 133 (answer-stage failures), 25-32 = 1
(conv-50_q28, and it is 1 of 3 gold parts), 33-36 = 1 (conv-47_q97), beyond 36 = 7 (ranks 49-176:
conv-41_q50@51, conv-42_q56@50, conv-47_q11@54, conv-48_q185@49, conv-50_q40@176, q151@60, q24@50),
no extracted gold = 2 (conv-42_q184, conv-50_q140 - write-time gaps). w32/w36 admit 8/4 extra entries per
question; on the 6 misses those bands contain only topic-adjacent distractors, never the gold.

## H1 - w32 on the 6 misses (x3 draws, medians) + gates

Misses (median; writecamp w24 baseline in parens): q151 0.0 (0.0), q24 0.0 (0.0), q20 0.0 (0.0),
q34 0.667 (0.333 - pass, but gold was ALREADY in-window at w24 ranks 1-2; pure answer variance, no
Greenland note enters 25-32), q138 0.444 (0.286-0.444), q10 0.4 (0.4; one 0.909 draw). The one w32-admit
candidate conv-50_q28: 0.352 median (long-drives note admitted, but fixing-cars@150 and nature@122 stay
out; 3-part list answer cannot form). Net: ZERO window-driven conversions.

Gates (writecamp 16 held + q22 wobble-hold, x2 draws at w32): 15/17 HOLD. REGRESSIONS:
- conv-43_q38 (314-entry store): 1.0 (w24) -> 0.0/0.0 "not mentioned" - gold notes stay in-window
  (ranks 11/13) but 8 entering travel distractors (Galway, Paris, Universal Studios) flip the answer.
- conv-48_q65 (277-entry store): 1.0/1.0 -> 1.0 then 0.0 (runaway "I'll inspect the workspace..." with
  5.8k output tokens; answer-discipline collapse under dilution).
conv-26 canary battery on fresh d4b542a8 (176 entries, G4 family, w24 vs w32, x2 draws each):
q1/q54/q126/q143 1.0 both arms; **q135 1.0/1.0 (w24) -> 1.0/0.556 (w32)** - the note-echo form
"She got hurt and had to take a break from pottery" drifts to "An injury that forced her to take a break
from pottery" exactly as in AA's frozen-store trap (1.0 -> 0.4 x2); q141 0.526->0.526/0.556; q144
0.333/0.333 -> 0.333/0.5; q149 0.5/0.5 -> 0.571/0.5. Trap verdict: the canary does NOT hold at w32.

## H2 - per-store adaptive window (24 for <=150 entries, 32 for >200): REJECTED

The widening side of the rule is exactly where H1 found regressions: conv-43 (314) q38 hard-fails,
conv-48 (277) q65 wobbles, conv-26 fresh (176) canary drifts. Stores where all cells held (206-303
entries) show no size boundary - the trap is cell-specific dilution sensitivity, not store size. And the
rule buys nothing: the only 25-32-band gold (q28, 1 of 3 parts) does not convert even with the gold
admitted. No runs needed beyond H1's - the size-scaled rule is dominated by w24 on both sides.

## H3 - content-aware rerank (recallRerankWithContent, contentChars 240, window 24)

Pool 30 (N's guidance bound): q151/q24 golds (ranks 50-60) are outside the pool - provably unreachable;
runs confirm 0.0 x2 each (rerank returns 1 id, the top tournament note). Pool 70 (reaches rank 60):
q151 converts 1/3 draws (0.625, "He took the weekend off to chill with his pets"; recall_n=2 with the
gold note verified inside), but the same draw set gives 0.0 when the rerank drops the gold (1-id recall),
and one draw WITH the gold in recall still answers "not mentioned" (the celebrate->weekend-off bridge is
inferential, not lexical). q24: the rerank selects the right note (family-dog) in 3/3 draws but returns a
1-entry recall and the model still answers "not mentioned" 3/3. q28: 0.33-0.39, no fix. The rerank also
over-filters (recall_n collapses to 1-2 on 7/15 draws) and burns up to 7.7k output tokens on the filter
step. Content-aware rerank is a variance amplifier here, not a ranking fix.

## Verdict

The D-class window hypothesis is DEAD on the fresh-store population: golds are either already in-window
(133/144 FAILED cells - answer-stage work) or 49-176 deep (9 cells - no safe window reaches them; the two
writecamp residuals q151/q24 are here). Widening (global or size-adaptive) converts nothing and breaks
note-echo/dilution-sensitive cells (q38, q65, q135 canary) precisely on the biggest stores. Residual
misses decompose as: answer-stage (q151 inferential bridge, q24 evidence sufficiency, q20 missing date,
q34 naming, q138 padding, q10 date form) + write-time (conv-42_q184, conv-50_q140 unextracted).

Artifacts: task1-rank-analysis.json (gold ranks + 144-question sweep + w32/w36 admission bands),
search_replay.py (verified offline replay), jobs-armA-D + results-armA-D + armA-D logs,
drank-*.json settings, run-drank-jobs.py runner. No fleet-config changes proposed; no ledger status
flips (all drank arms are experimental configs; the controller only appended attempts).


## Reactivation (2026-10-14, round-5 candidate): offline write-time consolidation measurement

OFFLINE ONLY (python replay, zero driver/endpoint runs). Stores: the 10 fresh typeB caches (writecamp 7 +
conv-26 d4b542a8 + writefull's conv-30 62b3e5ec / conv-49 543cedbc); replay re-verified 50/50 vs binary
search_ranked on writefull's two stores (4 run records) -> 10/10 stores verified. Population: 158 ledger-FAILED
questions on the 10 stores; 8-store subset reproduces the banked 133/1/1/7/2 buckets exactly.

Consolidation model: transitive clustering over date-stripped title-token similarity (containment =
|A&B|/min(|A|,|B|)); merge = latest member canonical, content/sourceIds/sources/spans/cues unioned; four
aggressiveness levels (ct080 near-duplicate series, ct070, ct065, ct050 pathological bound), each in
title-union and title-first variants. Full data: consolidation-measurement.json.

| config | N cut | q50 | q56 | q11 | q185 | q40 | q151 | q24 | 8-store pop (133 in-window) |
|----|----:|----:|----:|----:|----:|----:|----:|----|
| before | - | 51 | 50 | 54 | 49 | 176 | 60 | 50 | 133 held |
| ct080 | -3% | 51 | **55** | 54 | 48 | 175 | 55 | 43 | 132 held, conv-48_q102 24->25 |
| ct070 | -7% | 51 | 54 | 54 | 44 | 171 | 53 | 37-38 | 131-132 held (first: conv-43_q84 21->42) |
| ct065 | -12% | 48 | 46-47 | 54 | 44 | 147 | 47-48 | 37 | 130-132 held (first: conv-41_q131 **2->35**) |
| ct050 | -57% | **1** | **18** | 49 | **1** | 89 | **24** | **1** | 133 "held" but via mega-blobs |

Findings:
1. NO realistic config (3-12% N cut) brings ANY of the 7 deep-rank targets into the top-24 window. Best
   movement is q24 50->37 and q151 60->47; the targets sit deep from lexical non-overlap, not store density.
2. Only the pathological ct050 (57% N cut, 101/157-member mega-clusters) reaches 4 of 7 targets - and
   q11 (->49) and q40 (->89) stay out even there. Mega-blobs also destroy granularity: q28's 28->3 at ct065
   is a 21-member Calvin blob whose other two gold parts sit at ranks 101/124 inside it.
3. Consolidation has a real dilution cost at every level: merged distractors inherit union term sets and
   jump above golds (conv-42_q56 50->55 via a 2-member joanna-lactose merge matching 5 query terms);
   title-first variants lose date-bearing titles (conv-41_q131 2->35: 10-member cluster kept only the
   latest title, dropping the 'July 2023' terms that made the note rank 2).
4. One genuine win mechanism exists: complement merges (conv-30_q3: mem_jon_lost_banking_job +
   mem_jon_job_loss_push_to_dance -> rank 130->2 at ct080). None of the 7 targets has a complement
   near-duplicate in its store, so the mechanism does not reach them.

VERDICT: NO-GO for building the consolidation write-lane as a D-class ranking fix. The rank gains at
safe merge levels are 5-15 positions where 25-50 are needed, each level costs 1-3 in-window golds, and
the level that works is store destruction (prior H1 evidence: dilution already breaks answer quality at
w32 with full granularity). Consolidation may still pay for itself on store size / recall-budget
efficiency / complement merges, but that is a different lane with a different gate.
