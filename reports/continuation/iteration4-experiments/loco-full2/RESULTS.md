# loco-full2: LoCoMo round-4 full-stack re-gate (FINAL7 + stemming on the fresh full store)

Worker `locofull2` | worktree `imp4b-locofull2` (branch improve/mem4b-loco-full2, base 4875ed6c4 = imp4 HEAD
after the loco-ans2 merge) | model dgx-glm53/glm-5.3 (only) | driver `imp4-targetR` memory_bench.exe
(sha256 b57dc607..., post-merge fleet binary, mtime verified).

**The full stack in ONE config**: warm cache d4b542a8e98deca6 (fresh typeB full re-ingest of conv-26,
176 entries, KU fact-update + attribute v2 importInstructions — identical store to DD's partial gate,
skipIngest copy-out on every run, never re-ingested or mutated) + deterministic recall (extract-only,
24-entry/24000-char) + `recallQueryStemming: true` + FINAL7 answer instruction (loco-ans2 FINAL7.json,
byte-identical importInstructions). Config: `fullstack-final7-stem.json`; base: loco-full base-det-settings.

## Battery (19 q: 18 h2h set + q143; x1, x3 on disagreements; medians; official locomo local-F1)

| q | cat | baseline | frozen det | DD gate (no stem) | FULL2 (FINAL7+stem) | d(bl) | d(DD) |
|----|----|---------:|----:|----:|----:|----:|----:|
| q1 | 2/temporal | 0.000 | 1.000 | 1.000 | 1.000 | +1.000 | +0.000 |
| q2 | 3/open | 0.000 | 0.250 | 0.250 | 0.286 | +0.286 | +0.036 |
| q18 | 1/multi | 0.269 | 0.444 | 0.000 | **1.000** | +0.732 | +1.000 |
| q26 | 2/temporal | 0.061 | 0.000 | 1.000 | 1.000 (1/1/1) | +0.939 | +0.000 |
| q33 | 2/temporal | 0.068 | 0.000 | 0.667 | **1.000** | +0.932 | +0.333 |
| q37 | 1/multi | 0.000 | 0.000 | 0.000 | 0.000 (0/0/0) | +0.000 | +0.000 |
| q39 | 1/multi | 0.109 | 0.450 | 0.533 | 0.533 | +0.425 | +0.000 |
| q55 | 1/multi | 0.000 | 1.000 | 0.000 | **1.000** (1/1/1) | +1.000 | +1.000 |
| q57 | 2/temporal | 0.056 | 0.600 | 0.600 | 0.000 (0/.6/0) | -0.056 | **-0.600** |
| q66 | 1/multi | 0.000 | 0.000 | 0.000 | 0.000 | +0.000 | +0.000 |
| q77 | 3/open | 0.000 | 0.000 | 0.000 | 0.000 | +0.000 | +0.000 |
| q99 | 4/single | 0.257 | 0.412 | 0.512 | 0.483 (.706/.483/.457) | +0.225 | -0.029 |
| q144 | 4/single | 0.293 | 0.364 | 0.333 | **0.571** | +0.279 | +0.238 |
| q149 | 4/single | 0.074 | 0.000 | 0.571 | **0.800** | +0.726 | +0.229 |
| q54 | 2/temporal | 0.182 | 1.000 | 1.000 | 1.000 | +0.818 | +0.000 |
| q126 | 4/single | 0.211 | 1.000 | 1.000 | 1.000 | +0.790 | +0.000 |
| q135 | 4/single | 0.316 | 0.556 | 1.000 | 1.000 (.706/1/1) | +0.684 | +0.000 |
| q141 | 4/single | 0.375 | 0.526 | 0.526 | 0.526 | +0.151 | +0.000 |
| q143 | 4/single | — | 0.500 | 1.000 | 1.000 | — | +0.000 |

## Aggregates (18-q comparison set, no q143)

- FULL2 mean **0.6222** vs baseline 0.1260 → **delta +0.4962** (target >= +0.40: MET).
- vs DD's partial gate +0.3736 (no stem, G4+h7): **+0.123 more**, driven by q18 (+1.0), q55 (+1.0), q33 (+0.33).
- vs frozen det +0.296: +0.20 more, on a 176-entry fresh store.

## Per-target verdicts

- Aggregate >= +0.40: **MET** (+0.4962).
- q149 >= 0.8: **MET** (0.800 x1, "strength, motivation, support" — FINAL7's recency single-note combine + metaphor exclusion hold under stemming).
- q144 >= 0.5: **MET** (0.571, "Scared, but thankfully he was okay." — feeling-first how holds).
- Both bars hold TOGETHER in one config: **MET** (same run batch, same store, same binary).
- q18 >= 0.667: **MET** (1.000, "the forest, the beach, the mountains" — stemming restored the camped/camping conversion on the fresh store).
- Fixed set: **HOLDS** (q1/q54/q126 = 1.0; q135 1.0; q141 0.526; q143 1.0).
- q55 at w24: **1.000 median (3/3 draws)** — recorded, not chased. Stemming pulls both sunset notes
  (caroline-sunset-painting-beach r15, melanie-sunset-painting-october-2023 r19) into the 24-entry window;
  AA's w32 trap is no longer needed for this question in this config.

## Residuals (recorded, all non-target)

- q57 0.000 (0/.6/0) vs DD gate 0.6: FINAL7 wobble — 2/3 draws leak a bare ISO date "2023-08-17"
  (violates the relative-phrase time precedence; gold "The week before 25 August 2023"). Only question
  that regressed vs DD's gate; -0.6 on the aggregate is already absorbed.
- q37 0.000 (0/0/0): the stemsweep q37 conversion ran on the FROZEN store; on the fresh store the
  answer stage picks "a horse painting"/"not mentioned" for "recently". Same as DD gate (0.0).
- q99 0.483: flat vs DD 0.512 (partial-credit phrasing wobble .457-.706).
- q66/q77 0.000: write-time extraction gaps (q66) / open-domain miss (q77), unchanged across all configs.

## Verdict

**VALIDATED end-to-end.** The round-4 LoCoMo full stack — write clauses (KU fact-update + attribute v2) +
read stack (deterministic extract-only recall 24/24000 + recallQueryStemming) + FINAL7 answer instruction —
in ONE config on the fresh 176-entry full store beats every bar: aggregate +0.4962 vs baseline (target +0.40),
q149 0.8 and q144 0.571 together, q18 restored to 1.0 by stemming, the entire fixed set holds, and q55
recovers at w24 (stemming window effect) instead of regressing. This is the final PR-evidence-package claim
for LoCoMo round 4.

Artifacts: fullstack-final7-stem.json (config), jobs-battery-r1.json / jobs-battery-r23.json,
results-battery-r1.json / results-battery-r23.json, run-battery.py, build-table.py, gate-table.json;
attempts under reports/continuation/iteration4/ql-attempts-locofull2 (campaign root, outside worktrees);
ledger auto-appended worker=locofull2 (31 runs, 19 questions).
