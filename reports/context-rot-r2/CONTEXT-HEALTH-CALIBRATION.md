# CONTEXT-HEALTH-CALIBRATION — the context-health score versus labelled outcomes

Scope: the calibration measurement for the tray's context-health score,
produced by W8 and committed on this branch. Report writer W21, 2026-10-20.
Every number cites a committed artefact; no new measurement was produced for
this file. Category for every number in this file: SYNTHETIC-DIAGNOSTIC
(the corpus labels are generated, not observed - see section 4).

Sources:

- module: `crates/pi-coding-agent/src/core/context_health_calibration.rs`
  (commit `6f0d592c6`, 702 lines, with its in-file test module), wired in
  `crates/pi-coding-agent/src/core/mod.rs` (same commit)
- artefact: `research/context_rot_r2/health/context_health_calibration.json`
- the scored heuristic: `crates/pi-coding-agent/src/core/context_health.rs`
  (pre-campaign, unchanged by r2)

## 1. Three concepts the score conflates (campaign section 16)

The tray renders one number, `CONTEXT HEALTH 92`. Three different concepts
hide behind it, and only the first two are what the score actually observes:

| concept | what it is | where it lives in the score | what it can honestly claim |
| --- | --- | --- | --- |
| capacity pressure | how full the window is (utilisation) | size penalty: 0 below `RELIABLE_UTILISATION` 0.35, maximal 45 at `FULL_PENALTY_UTILISATION` 0.95, linear between (`context_health.rs` constants) | an operational fact: "the window is N% full" |
| structural integrity | how much of the context has been rebuilt by the compaction machinery, and what protection structure survives | compaction penalty: `PER_COMPACTION_PENALTY` 6 per cycle, capped at `MAX_COMPACTION_PENALTY` 30 (`context_health.rs` constants) | an operational fact: "this context has been through N compactions" |
| empirically estimated quality risk | a calibrated probability that semantic material (constraints, evidence, decisions) has been lost | nothing - the score maps to risk as `risk = 1 - score/100` and that mapping has never been calibrated against an outcome | only what a calibration corpus licenses; today, nothing |

The band labels (Healthy >= 80, Straining >= 60, Degraded >= 40, Critical
below) read as quality claims. The calibration measurement exists to test
exactly that reading, and it fails it.

## 2. The instrument (W8, commit `6f0d592c6`)

`context_health_calibration.rs` adds, as pure data and pure functions with no
I/O and no production wiring:

- `HealthCase` / `TrueLabels`: the labelled-corpus types. `HealthCase`
  carries the real `ContextHealthInput` (from `context_health.rs`), so the
  corpus is scored by the production `context_health` scorer, not a copy.
  `TrueLabels` records `lost_constraint`, `lost_evidence`,
  `wrong_decision_followed`, `task_continued` per case.
- `synthetic_calibration_corpus()`: a deterministic 66-case sweep - the full
  cross product of six utilisations (0.20, 0.35, 0.50, 0.65, 0.80, 0.95)
  and eleven compaction-cycle counts (0..=10) - against a 200 000-token
  window, `is_compacting` false throughout (the corpus labels a settled
  state). Labels follow the R2-E1 finding: constraint loss never appears at
  cycle 0, and whether a cycled session lost the constraint is dominated by
  a restatement-in-tail property the score cannot observe (deterministic LCG
  bits, no seeds or clocks).
- Pure calibration metrics: rank-based ROC-AUC, Brier score, 10-bin expected
  calibration error, and at-threshold precision / recall / FPR / FNR.

Test coverage in the same commit (in-file test module):

- metric fixtures are hand-computed: a perfect ranking scores AUC 1.0, an
  all-tied one 0.5, a partial-tie case exactly 7/12, an inverted one 0.0,
  one-class corpora return `None`; Brier of `[0.1, 0.9]` vs `[false, true]`
  is 0.01 and a confident wrong pair costs 1.0; ECE hand cases 0.45 and 0.0,
  out-of-range probabilities clamp; threshold metrics are hand-counted with
  the inclusive threshold and the undefined-rate rules.
- corpus properties: determinism (two calls equal), unique scenario ids, at
  least 40 cases, exact sweep bounds, cycle-0 never loses a constraint,
  cycled cases mix losses and keeps, wrong decisions only ever follow
  losses, and the score recomputes exactly from the public constants
  (`corpus_scores_with_the_production_scorer`).
- the measurement itself is bounded by its test
  (`current_heuristic_scores_poorly_against_semantic_constraint_loss`):
  AUC must beat a coin flip and stay under 0.75, Brier >= 0.2, ECE >= 0.1 -
  i.e. the test asserts the heuristic is a POOR predictor, so the committed
  numbers cannot silently improve.

## 3. Results (SYNTHETIC-CALIBRATION, target `lost_constraint`)

From `research/context_rot_r2/health/context_health_calibration.json`
(corpus `SYNTHETIC-CALIBRATION`, 66 cases, 30 positives, scored with
`risk_probability = 1 - score/100`):

| metric | value |
| --- | --- |
| ROC-AUC | 0.604 |
| Brier score | 0.252 |
| ECE (10 bins) | 0.146 |
| threshold 0.5 - TP / FP / FN / TN | 13 / 11 / 17 / 25 |
| precision at 0.5 | 0.542 |
| recall at 0.5 | 0.433 |
| false-positive rate at 0.5 | 0.306 |
| false-negative rate at 0.5 | 0.567 |

Derived in this report from the committed `per_case` rows of the same file
(plain arithmetic over the 66 committed cases, no new run):

- the 6 cycle-0 cases contain 0 positives; the 60 cycled cases contain 30
  (50 %) - the cycle count carries essentially all of the signal.
- label side counts: `lost_evidence` 26, `wrong_decision_followed` 23,
  `task_continued` 63.
- score range 25-100, so the risk mapping only ever emits 0.00-0.75.
- component AUCs on the same rows: compaction cycles alone 0.633,
  utilisation alone 0.510, the composite risk 0.604. The composite is worse
  than its best component: the utilisation penalty contributes noise, and
  the 6-points-per-cycle penalty saturates at 5 cycles while label risk
  keeps growing with depth.

## 4. The SYNTHETIC-CALIBRATION caveat

The labels are generated, not observed. They encode the R2-E1 finding
(`research/context_rot_r2/runs/r2-e1-multicycle.score.json`, default arm
0/19 after 5 cycles; see `reports/context-rot-r2/BASELINE.md` section 3):
loss appears only after compaction cycles, and is then dominated by whether
the constraint was restated in the retained tail - a property no pressure
proxy can see. The corpus therefore assumes the mechanism it is used to
inspect. What AUC 0.604 licenses is: the measurement machinery works, and
the direction of the finding (pressure heuristics are a poor predictor of
semantic loss) holds on this corpus. It does NOT license a real-world
predictive-quality claim for the score.

## 5. What a real-outcome calibration requires

The live-phase label corpus. Concretely:

1. Run live sessions (real summarizer and real probe answering, the R2-E2 /
   R2-E3 live family - `research/context_rot_r2/live/`) through compaction.
2. For each case, fill `TrueLabels` from observed behaviour, not rules: did
   an outstanding constraint disappear from the working context
   (`lost_constraint`), did a depended-on evidence reference become
   unresolvable (`lost_evidence`), did the session act against a standing
   instruction (`wrong_decision_followed`), did the task still terminate
   (`task_continued`).
3. Replace `synthetic_calibration_corpus()` with that corpus. The types,
   the metrics and the report shape stay; the module was built for this
   swap (the generator is the only synthetic part).
4. Keep the measurement off the production path: nothing in the agent reads
   the calibration at runtime (module docs, commit `6f0d592c6`).

## 6. Recommendation

- Do not present the score as an empirically estimated quality risk. On the
  committed evidence the percentage would be a fake number: AUC 0.604 is
  barely better than a coin flip (and FNR 0.567 at the 0.5 threshold means
  more than half of actual losses go unflagged), and even that much rests on
  a synthetic corpus.
- Surface UNKNOWN plus factual diagnostics instead of a quality
  percentage: utilisation percent, compaction-cycle count, and the pressure
  band - the things the score actually observes - with quality explicitly
  unclaimed.
- The operational pressure indicator stays. `context_health` remains useful
  as a pressure gauge for the tray (a filling window, accumulating cycles -
  the reasons strings already say exactly this), and the calibration
  harness now exists so that any future quality claim about the score is
  tested against labels instead of assumed.

Status: SYNTHETIC-DIAGNOSTIC, measurement only. Nothing in production reads
these metrics.
