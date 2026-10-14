# loco-timeform: FINAL8 — q57 ISO-date leak closed (FINAL7 + recently-anchoring clause)

Worker `timeform` | worktree `imp4b-timeform` (branch improve/mem4b-timeform, base 6b999a1b2 = imp4 HEAD
after the loco-full2 merge) | model dgx-glm53/glm-5.3 (only) | driver `imp4-targetR` memory_bench.exe
(sha256 b57dc607..., shared post-merge fleet binary).

**Stack under test**: identical to the validated loco-full2 full stack — warm cache d4b542a8e98deca6
(fresh typeB full re-ingest of conv-26, 176 entries, KU fact-update + attribute v2 importInstructions;
skipIngest copy-out; never re-ingested or mutated) + deterministic recall (extract-only, 24-entry/24000-char)
+ `recallQueryStemming: true` — with the single change: **FINAL8 answer instruction** (FINAL7 + one added
sentence). FINAL8's importInstructions is byte-identical to FINAL7's, so the cache key stays d4b542a8e98deca6
(verified before any run).

## The added sentence (FINAL8 vs FINAL7, answerInstruction only)

> For when-questions whose notes say something happened 'recently' or with similar wording for a span shorter
> than a year, reply with that wording anchored to the note's conversation date in words, like
> 'recently before 3 July 2023' - never with a bare date or any year-month-day form in the answer.

Wording iteration (q57 x3 each):
- v1 (general precedence override: "the relative phrase ... wins over every other rule"): 0/.6/.6 — draw 1 still
  leaked the bare ISO date '2023-08-17'. The note's leading "On 2023-08-17, ... recently had ..." beats a generic clause.
- v2 (trigger named 'recently' verbatim; date format unspecified): .6/.25/.6 — no bare ISO, but draw 2 anchored with
  the raw ISO token ('recently before 2023-08-17').
- **v3 (v2 + "conversation date in words")**: **.6/.6/.6 — 'recently before 17 August 2023' 3/3, no ISO anywhere. SHIPPED.**

## q57 (the one regression vs DD's gate)

Before (loco-full2, FINAL7): 0/.6/0 median 0.0 — 2/3 draws leaked bare ISO '2023-08-17'
(gold 'The week before 25 August 2023'; the hike note says "On 2023-08-17, Caroline told Melanie that she
recently had a not-so-great experience on a hike" and recalls at rank #1 every draw).
After (FINAL8 v3): .6/.6/.6 median **0.6 >= 0.5**, zero ISO forms — the answer form now matches the
note's own 'recently' wording anchored to the chat date, which is what the DD-gate 0.6 draw also produced.

## Full battery (19 q; x1, x3 on disagreements vs loco-full2 medians; medians; official locomo local-F1)

| q | cat | baseline | GG FULL2 | FINAL8 | d(bl) | d(GG) |
|----|----|---------:|----:|----:|----:|----:|
| q1 | 2/temporal | 0.000 | 1.000 | 1.000 | +1.000 | 0.000 |
| q2 | 3/open | 0.000 | 0.286 | 0.286 | +0.286 | 0.000 |
| q18 | 1/multi | 0.269 | 1.000 | 1.000 | +0.732 | 0.000 |
| q26 | 2/temporal | 0.061 | 1.000 | 1.000 | +0.939 | 0.000 |
| q33 | 2/temporal | 0.068 | 1.000 | 0.667 | +0.599 | **-0.333** |
| q37 | 1/multi | 0.000 | 0.000 | 0.000 | +0.000 | 0.000 |
| q39 | 1/multi | 0.109 | 0.533 | 0.533 | +0.425 | 0.000 |
| q55 | 1/multi | 0.000 | 1.000 | 1.000 | +1.000 | 0.000 |
| q57 | 2/temporal | 0.056 | 0.000 | **0.600** | +0.544 | **+0.600** |
| q66 | 1/multi | 0.000 | 0.000 | 0.000 | +0.000 | 0.000 |
| q77 | 3/open | 0.000 | 0.000 | 0.000 | +0.000 | 0.000 |
| q99 | 4/single | 0.257 | 0.483 | 0.483 | +0.225 | 0.000 |
| q144 | 4/single | 0.293 | 0.571 | **0.571** (3/3 .4/.571/.571) | +0.279 | 0.000 |
| q149 | 4/single | 0.074 | 0.800 | **0.800** | +0.726 | 0.000 |
| q54 | 2/temporal | 0.182 | 1.000 | 1.000 | +0.818 | 0.000 |
| q126 | 4/single | 0.211 | 1.000 | 1.000 | +0.790 | 0.000 |
| q135 | 4/single | 0.316 | 1.000 | 1.000 | +0.684 | 0.000 |
| q141 | 4/single | 0.375 | 0.526 | 0.556 | +0.181 | +0.029 |
| q143 | 4/single | — | 1.000 | 1.000 | — | 0.000 |

## Aggregates (18-q comparison set, no q143)

- FINAL8 mean **0.6386** vs baseline 0.1260 → **delta +0.5126** (target >= +0.49: **MET**, and above
  loco-full2's +0.4962).
- vs loco-full2 mean 0.6222: **+0.016 more**, i.e. q57's +0.6 recovery minus q33's -0.333 dip, spread over 18.

## Per-target verdicts

- Aggregate >= +0.49: **MET** (+0.5126).
- q149 >= 0.8: **MET** (0.800, 'strength, motivation, support').
- q144 >= 0.5: **MET** (0.571 median over 3 draws; one draw 'Really scary, but thankfully he was okay.' = 0.4 —
  feeling-first how wording variance, not clause interaction: the other 2 draws give the FINAL7-identical
  'Scared, but thankfully he was okay').
- q18 = 1.0: **MET** ('forest, beach, mountains').
- q55 >= 0.5: **MET** (1.000, 'sunsets, abstract paintings').
- Fixed set: **HOLDS** (q1/q54/q126 1.0; q135 1.0; q141 0.556; q143 1.0).
- q57 median >= 0.5 with no bare ISO dates: **MET** (.6/.6/.6, 'recently before 17 August 2023').

## Dips vs loco-full2 (diagnosed per task 3)

- **q33 0.667 vs GG's 1.0 (-0.333): draw variance, not clause interaction.** All 3 FINAL8 draws answer
  'the week before 3 July 2023, the Friday before 14 August 2023' (0.667) — the same two-parade-listing form
  the question wobbles between across ALL configs (ledger q33 history: 15 prior attempts span
  'the week before 3 July 2023' (1.0) vs the two-item list (0.667) vs 'not mentioned' (0.0) under FINAL7,
  DD, tempan, stemsweep alike). GG's 1.0 was itself a single x1 draw of the 1.0 form. Still +0.599 vs baseline.
  The FINAL8 sentence does not touch q33: its notes ('the week before', 'previous Friday') are not
  'recently'-worded, and no FINAL8 draw changed form vs FINAL7's distribution.
- q141 +0.029 (0.556 vs 0.526): phrasing variance, non-target, still above its 0.5 bar.
- No other dip; every other question matches GG's number exactly.

## q37 (task 4: report, not gated)

NOT converted (0.0, 'not mentioned', same as GG). The sunset note recalls at rank #1 with stemming, but the
answer stage prefers the horse note's literal 'painted a horse painting recently' over the sunset note's
'the week before'. This is a what-question; the FINAL8 clause is scoped to when-questions and did not help.
Same result as the DD gate. No cheap conversion available from the answer instruction alone.

## Verdict

**VALIDATED.** FINAL8 = FINAL7 + the recently-anchoring sentence closes the single loco-full2 regression
(q57 0.0 → 0.6 median, zero ISO leaks in 3/3 draws) while holding every loco-full2 target: aggregate
+0.5126 (>= +0.49), q149 0.8 and q144 0.571 together, q18 1.0, q55 1.0, fixed set intact. The only
vs-GG movement is q33's known two-parade list-form wobble (-0.333, draw variance, still +0.599 vs baseline)
and q141's +0.029. This is the FINAL answer-instruction revision for the LoCoMo round-4 evidence package.

Artifacts: FINAL8.json, fullstack-final8-stem.json (settings), jobs-q57x3*.json / results-q57x3*.json
(v1/v2/v3 wording iterations), jobs-battery-r1.json / jobs-battery-r23.json, results-battery-r1.json /
results-battery-r23.json, run-battery.py, build-table.py, gate-table.json; attempts under
reports/continuation/iteration4/ql-attempts-lcotf8 (campaign root, outside worktrees); ledger auto-appended
worker=timeform.
