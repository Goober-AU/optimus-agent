# LoCoMo full-stack milestone gate (conv-26 fresh re-ingest + full battery)

Date: 2026-10-13 | worker: locofull | model: dgx-glm53/glm-5.3 (only)
Worktree: imp4-locofull (improve/mem4-loco-full, base 62ead4f50) | driver: imp4-targetR memory_bench.exe
Store: fresh typeB re-ingest of conv-26 (19 sessions, captureProtocol temporal-authority/1.0.0,
cache key d4b542a8..., 22 min wall) — 176 entries vs frozen 119. WRITE stack: KU fact-update +
attribute v2 clauses in importInstructions. READ stack: det recall (extract-only 24/24000) +
G4 combined answer instruction + h7-combine clause. Battery x1, x2/x3 on disagreements (medians).

## Table (fresh = median of draws; baseline/frozen from loco-h2h)

| q | cat | baseline | frozen det | fresh | d(fz) | d(bl) |
|----|----|---------:|----:|----:|----:|----:|
| q1 | 2/temporal | 0.000 | 1.000 | 1.000 | +0.000 | +1.000 |
| q2 | 3/open | 0.000 | 0.250 | 0.250 | +0.000 | +0.250 |
| q18 | 1/multi | 0.269 | 0.444 | 0.000 | -0.444 | -0.269 |
| q26 | 2/temporal | 0.061 | 0.000 | 1.000 | +1.000 | +0.939 |
| q33 | 2/temporal | 0.068 | 0.000 | 0.667 | +0.667 | +0.599 |
| q37 | 1/multi | 0.000 | 0.000 | 0.000 | +0.000 | +0.000 |
| q39 | 1/multi | 0.109 | 0.450 | 0.533 | +0.083 | +0.425 |
| q55 | 1/multi | 0.000 | 1.000 | 0.000 | -1.000 | +0.000 |
| q57 | 2/temporal | 0.056 | 0.600 | 0.600 | +0.000 | +0.544 |
| q66 | 1/multi | 0.000 | 0.000 | 0.000 | +0.000 | +0.000 |
| q77 | 3/open | 0.000 | 0.000 | 0.000 | +0.000 | +0.000 |
| q99 | 4/single | 0.257 | 0.412 | 0.512 | +0.100 | +0.254 |
| q144 | 4/single | 0.293 | 0.364 | 0.333 | -0.030 | +0.041 |
| q149 | 4/single | 0.074 | 0.000 | 0.571 | +0.571 | +0.497 |
| q54 | 2/temporal | 0.182 | 1.000 | 1.000 | +0.000 | +0.818 |
| q126 | 4/single | 0.210 | 1.000 | 1.000 | +0.000 | +0.789 |
| q135 | 4/single | 0.316 | 0.556 | 1.000 | +0.444 | +0.684 |
| q141 | 4/single | 0.375 | 0.526 | 0.526 | +0.000 | +0.151 |
| q143 | 4/single | — | 0.500 | 1.000 | +0.500 | — |

## Aggregates (18-q comparison set, no q143)

- Fresh mean 0.4996 vs frozen det 0.4223.
- Mean delta vs baseline: fresh **+0.3736** vs frozen det +0.296 → target MET.

## Targets

- q149 >= 0.8: **MISS** — 0.5714 stable (3/3 draws, 'love, support, strength, motivation, joy');
  write-time structural fix IS validated (0.0 -> 0.571; both session-18 gold statements in store),
  the full store's extra attribute notes dilute the combine (5 attrs vs gold 2).
- Fixed set: q1/q54/q126 = 1.0 MET; q135 = 1.0, q141 = 0.526 (median of 0.556/0.526/0.222), q143 = 1.0 MET;
  **q144 = 0.333 MISS** (draws 0.333/0.5/0.333, answer-stage phrasing wobble on correct rank-0 recall).
- Aggregate: MET (+0.374 > +0.296).

## Regression analysis (task 5)

- q55 (1.0 -> 0.0): recall-width. Caroline's sunset note falls outside the 24 window amid 57 new
  attribute notes. Diagnostic window 32 -> 1.0 'sunsets'. Not write-time.
- q18 (0.444 -> 0.0): recall-depth + extraction-selection. All three location notes exist but rank
  below even a 32 window; the frozen 0.444 relied on a locations cross-reference inside the frozen
  what-she-loves note, not reproduced by the fresh extraction. Partially write-time (note selection),
  partially window crowding.
- q144 (0.364 -> 0.333): answer stage; recall correct at rank 0; note-faithful phrasing caps ~0.5-0.615.
- q149 (target 0.8 -> 0.5714): answer stage over-combination; write-time capture validated.

## Verdict

PARTIAL VALIDATION for the round-4 PR evidence package: aggregate and 5/7 fixed bars hold, the
write-time clause set is validated (q149 gold statements captured; KU update note present), but the
read-time 24-entry window needs a bump (32 restores q55) for >150-entry stores, and q144/q149 need
answer-stage tightening before the full stack is called validated.

Artifacts: gate-spec.json, fullstack-ingest-manifest.json, fullstack-write-read.json,
base-det-settings.json, gate-table.json, battery-*.json, diag-window32.json, verify-store.py,
run-battery.py, build-table.py; ledger attempts auto-appended (worker locofull / locofull-diag);
attempts under reports/continuation/iteration4/ql-attempts-locofull (campaign root, outside worktrees).
