# FINAL REPORT — context rot and compaction fidelity in Optimus (iteration 2, 2026-10-19/20)

Entry point for the r2 campaign. Iteration 1
(`reports/context-rot/FINAL-REPORT.md`) measured compaction policies through Python
mirrors and found one decisive improvement (the evidence-recovery excerpt) and one
negative result (the v1 task-state ledger: no recall gain, a token cost). This
campaign closed the mirror gap: every multicycle number below marked PRODUCTION-PATH
was produced by the real `prepare_compaction` -> `compact` -> `append_compaction` ->
`SessionManager` restore pipeline, offline, through the committed adapter, and a
live family (R2-E2, R2-E3, R2-E3ADV, R2-E5, the W26 CompactBench practice run, the
W27 ContextBench practice run) put the same prompts and the same pipeline against GLM
5.3. The headline is split again, and sharper than iteration 1's: repeated
compaction is a confirmed information-loss mechanism on the production path, and
the v1 ledger is not a safe fix for it (under a live model it inverted a user
correction; the W11 repair renders clean offline, the render cap it exposed was
fixed as D21, and the W16 clause-level supersession refinement - integrated
with its `rot2_supersession` suite at 4/4 - closes the misclassification
family, but the ledger's live token cost and its E3ADV dead-weight behaviour
stand, so it stays off and is documented as legacy). Of the campaign's
numbered defects, every fixable one is closed at this tip except D7
(documented-deferred), D33 (fix landed by W54 `f0b125a84` after this pass) and D34 (the battery-found regression, fix in flight): the
thirteen audited defects of the W34 refresh - twelve with behaviour changes
and regression guards, D2 verified intact and pinned by tests - the
D18/D19/D20 recovery (W30), the D21-D29 fix wave, D30 with the cycle-5 budget
caveat accepted-and-pinned (W46) and the post-wave fixes D31 (W52) and D32
(W53) all landed with regression guards. The designed replacement (typed state
v2) is wired end to end as an opt-in mode, measured live (R2-E5, re-measured
post-fix by R2-E5-RM, W47), red-teamed (the W35 adversarial suite pinned eight
guarantees and filed seven defects, D22-D28, all fixed) and documented for
users (the README and the settings reference). D10's "never retry" sub-item and
the D12/D20 partial-fix residues stay disclosed in the bug ledger.

Base `6000d6bc1` (v0.1.36 + #181/#182). First written on branch `rot2/w22-final`
at `021678796` (40 commits over base); refreshed on branch `rot2/w34-reportrefresh`
at `f8ad670c8` (64 commits over base), adding the live coding-task suite R2-E3,
R2-E3ADV, the R2-E2-RM re-measure, the W15 typed-state integration, the D6
call-site wiring, D11, the W26 live CompactBench practice run and the W27 live
ContextBench practice run; this wave-2 refresh on branch `rot2/w49-wavereport`
at the integration tip `71e770c5e` (103 commits over base, `git rev-list --count
6000d6bc1..HEAD`), adding the W35 typed-state red team, the D21-D29 fix wave
and the W49 verification battery (section 4.4; REGRESSION-GATES.md section 10).
Landed since the W34 edition: R2-E5 (the live typed-arm run), the D18/D19/D20
recovery (W30), the D21 render-cap fix (W32) and the D22-D29 red-team fixes;
landed since the W49/W50 refreshes: the D30 fix with the cycle-5 budget
caveat accepted-and-pinned (W46, `10e2b36e6` + `d00ff2067`), the typed-arm
re-measure R2-E5-RM (W47, section 3.14: fixture 13/19 on the integrated tip,
TASK-E no halt, E3ADV C1 SUPERSEDED at every cycle), the W16 v1 supersession
refinement (integrated by W51: `a112034dc` + `611d2312c` + `3de7ef222`, the
`rot2_supersession` suite 4/4), the post-wave fixes D31 (W52, `6041f71c9`)
and D32 (W53, `53ab2e3d9`), the typed mode's user documentation (README
`a5362aa63`, settings reference `0a35fbfa6`), the W57 post-D25 number ripple,
the W59 audit resolution and the W60 experiment-ledger verification. This
closing pass is on branch `rot2/w62-finalreport` at the integration tip
`90fcd17db` (143 commits over base, `git rev-list --count 6000d6bc1..HEAD`).
In flight at this writing and NOT in this tree: the D33 fix (`rot2/w54-d33`).

## 1. Hypotheses

| id | hypothesis | test | verdict |
| --- | --- | --- | --- |
| H-r2-1 | the default summary-of-summary path loses protected information over repeated compactions on the production path | R2-E1: 19 anchors, 5 cycles, deterministic lossy summarizer; E-INC-1/2a: 8 cycles, 2 seeds | supported as a worst-case bound (0/19 after 5 cycles, 0/19 already after 2); NOT confirmed live - R2-E2's real summarizer retained 19/19 and 37/37 anchors at every cycle |
| H-r2-2 | the v1 ledger does not safely protect state | R2-E2 base+hard (live GLM 5.3); E-INC-2b/2c (W9-F2 id collisions, W9-F4 mutual retirement); the W8 calibration (the health heuristic, AUC 0.604, is no safety net either); R2-E3ADV (adversarial corrections, live); R2-E2-RM (post-W11 renders, offline) | strongly supported: the ledger can invert a user correction (pre-W11, live); retention is seed- and placement-dependent; nothing else predicts the loss. Post-W11 it either renders clean (E2-RM base) or caps the corrections out of the rendered ACTIVE list (E2-RM hard, D21); when supersession cannot pair at all it does nothing and the model argues the stale item down (E3ADV, 30/30 both arms at +1.5k-1.9k request chars/cycle) |
| H-r2-3 | provenance-aware typed state fixes the mechanism | typed_state v2 module (design + 60 unit tests at introduction, `c948905a3`; 74 at the W49 verification tip, 78 at this closing tip); the W15 integration wiring (`338b920c3`, `40bead288`, `4be9623b0`); W11's content-derived id and correction-safe supersession fixes in v1 (`1754a3e1b`), re-measured offline on the live corpus by R2-E2-RM (`2fbb14f33`); R2-E5 (the typed-arm measurement, `EXPERIMENT-LEDGER.md`); the W35 red team and the D21-D29 fix wave (section 4.4) | partially supported: wired end to end, verifier-gated, and R2-E5 part 1 beats control B on the fixture at the pre-D25 implementation (15/19 vs 14/19; 13/19 on the integrated tip - see the footnote below the table) with live parts 2-3 correct where they ran; the first live run also falsified two pre-registrations (TASK-E halt, the D24 id-churn mechanism; no SUPERSEDED render in E3ADV C1, the D29 token-overlap pairing) - both fixed and resolved live by the E5-RM re-measure (W47: TASK-E 5/5 cycles no halt; SUPERSEDED rendered at every cycle 1-5); the red team then found D22-D28, all fixed, and D30 is fixed with the cycle-5 budget caveat accepted-and-pinned (W46). The E3ADV structural goal is achieved post-D29 |
| H-r2-4 | pressure/cycle health does not predict semantic loss | W8 calibration corpus: 66 labelled cases scored by the production scorer | supported on synthetic labels: ROC-AUC 0.604 against `lost_constraint` |

H-r2-3 fixture footnote: the typed arm's E1-fixture score is 15/19 at the
pre-D25 implementation (W29, committed E5) and 13/19 on the integrated tip
(W46/W47 re-measurements: the D25 step boundary correctly retires two
Step-scoped episode-classified probes; decisions 4/4, evidence 1/4,
supersession 2/2 unchanged; verifier 5/5 both). The fixture measures pipeline
protection under the deterministic lossy summarizer; the two retired probes
(q-constraint-6/7) are Step-scoped episodes by classifier design, not a
regression (section 3.14, EXPERIMENT-LEDGER R2-E5-RM).

## 2. What was measured

Category labels (defined in `reports/context-rot-r2/BASELINE.md` section 2, extended
here; never mixed within one table):

- SYNTHETIC-DIAGNOSTIC - deterministic fixture, zero model calls; measures
  mechanism, not model quality.
- PRODUCTION-PATH - produced by the real Rust compaction pipeline in this repo.
- CUSTOM-OPTIMUS - official external benchmark infrastructure with custom Optimus
  adapters/settings; not comparable to official scores.
- LIVE-MODEL - real model endpoint as summarizer and/or probe answerer.
- SYNTHETIC-CALIBRATION - generated labels scored by the production scorer.

| id | question | arms / runs | model | artefacts | label |
| --- | --- | --- | --- | --- | --- |
| R2-E1 | does the pipeline protect anchors over 5 compactions | 2 arms (default, taskState), 1 run, 19 probes | none (deterministic lossy fixture) | `research/context_rot_r2/runs/r2-e1-multicycle.{scenario,result,score}.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| R2-E0 | adapter smoke | 2 arms, 3 probes, 5 cycles | none (fixture) | superseded by R2-E1 (same mechanism, smaller probe set) | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-1 | growth, decay, request composition, policy exposure | 2 arms x 2 seeds + env-policy probe (3 runs) | none (fixture) | `research/context_rot_r2/incremental/results/e-inc1.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-2a | decay curves, determinism, path independence | 7 cycle-truncated runs + 1 byte-equal rerun | none (fixture) | `incremental/results/e-inc2a.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-2b | late-planted constraints | 2 runs (`w9-{a,b}-late3c`) | none (fixture) | `incremental/results/e-inc2b.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-2c | late supersession | 3 runs (`w9-{a,b}-latecorr5c`, `w9-a-8c-retired`) | none (fixture) | `incremental/results/e-inc2c.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-3 | token cost | accounting over all 16 adapter runs | none (fixture) | `incremental/results/e-inc3.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| R2-E2 base | live multicycle, 19 probes x 5 cycles | 2 arms, 95 round records per arm | GLM 5.3 (DGX K3 LaunchPad, SGLang, temperature 0) | `research/context_rot_r2/live/` (`results.json`, `raw-calls.jsonl`, `r2-e2-multicycle.*`) | LIVE-MODEL |
| R2-E2 hard | live multicycle, 37 probes (20 constraints) x 5 cycles | 2 arms, 111 round records per arm | GLM 5.3 (as above) | `live/r2-e2-hard.*`, `live/results.json` | LIVE-MODEL |
| R2-E3 | live coding-task action retention (next-file, corrected approach, boundary evidence, acceptable set, restart) | 5 tasks x 2 arms x 5 cycles, 50 probe points per arm, 3 reps each | GLM 5.3 (as above) | `research/context_rot_r2/live/e3/` (`results.json`, `raw-calls.jsonl`, `README.md`; commits `d8e318f81`, `346f6c4f3`, `c5107fc31`) | LIVE-MODEL |
| R2-E3ADV | adversarial corrections sharing no anchor with the decision they replace | 3 families x 2 arms x 5 cycles, 30 cycle points per arm, 3 reps each | GLM 5.3 (as above) | `live/e3adv/` (`results.json`, `raw-calls.jsonl`, `README.md`; commits `76eefbf9f`, `754bb75a1`) | LIVE-MODEL |
| R2-E2-RM | does the W11 fix remove the R2-E2 inversion (offline re-measure on the live corpus) | 2 scenarios x 2 passes x 5 cycles (fixture pass + post-W11 pass) | none (corpus replay, no model calls) | `live/remeasure/` (`results.json`, `README.md`, `w11_port.py`, `replay.py`; commit `2fbb14f33`) | PRODUCTION-PATH-MIRROR |
| R2-E5 | the typed arm: fixture retention, live action parity, adversarial supersession | E1 19-probe x 5-cycle three-arm pass (fixture) + e3 tasks A/C/E typed-vs-default x 5 cycles x 3 reps + the E3ADV C1 typed cell (live) | GLM 5.3 (as above) for the live parts; none for the fixture pass | `live/e5/` (`adapter/`, `live/`, `live-d29/`, `README.md`; worker commits `4f2e6935e`, `bf0ba53f6`, `89e8f7273`; integrated as `fe6b6d966`, `59832805f`, `c248a101f`; re-measured by R2-E5-RM, W47, `live/e5/rm/`) | PRODUCTION-PATH + LIVE-MODEL |
| W26 live CompactBench | custom practice run, GLM as summarizer (production path) and task/eval model | 4 arms, starter slots 0-1 (8 cases/arm), 3 drift cycles, one live draw per case | GLM 5.3 (DGX K3 LaunchPad, SGLang, temperature 0) | `compactbench_adapter/live/` (`run-config.json`, `results/`, `raw-calls.jsonl`, `README.md`; commits `6ea85dc10`, `4b8f2165f`) | CUSTOM-OPTIMUS + LIVE-MODEL |
| W27 live ContextBench | retention of gold context through compaction, paired arms | 26 paired dev instances x 2 arms x 4 cycles (208 cycle attempts) | GLM 5.3 (as above, summarizer; synthetic gold-derived trajectories) | `contextbench/live/` (`run_config.json`, `results.json`, `raw-calls.jsonl`, `README.md`; commits `da38b1edf`, `3e5880dd5`, `963ca577a`, `32a430f08`) | CUSTOM-OPTIMUS + LIVE-MODEL |
| W12 suites | CompactBench public practice suites, 3 drift cycles | 9 arms x 2 suites (18 runs; starter 20 cases, elite 100) | mock-deterministic (mock eval provider, scorer 2.0.0) | `research/context_rot_r2/compactbench_adapter/results/w12/`, `SUITE-RESULTS.md` | CUSTOM-OPTIMUS (PRODUCTION-PATH pipeline) |
| W6 validation | adapter vs harness, 1 drift cycle | 5 arms, 8 starter cases | mock-deterministic | `compactbench_adapter/results/*.jsonl`, README "Validation runs" | CUSTOM-OPTIMUS (superseded mode, kept for the record) |
| W10 pipeline | ContextBench retrieval metrics, arms A/B | 0 runs (offline scaffold; arms C/D emit blocked records only) | none (no trajectories committed) | `research/context_rot_r2/contextbench/` | CUSTOM-OPTIMUS |
| W8 calibration | does the context-health score predict constraint loss | 1 heuristic, 66 cases (30 positives) | none (production scorer over generated labels) | `research/context_rot_r2/health/context_health_calibration.json` | SYNTHETIC-CALIBRATION |
| TypeCompact repro | external reference: pinning outside the summarizer | 4 strategies, n = 20 configs + full suite n = 197 | none (their regex reference classifier) | `research/context_rot_r2/repro/typecompact_repro{,_full_suite}.json` | SYNTHETIC-DIAGNOSTIC (external) |

Wire-call budgets: R2-E2 581 (526 distinct after content-keyed dedupe; the rest
are length-ladder retries) against a ~600-call guide; R2-E3 209 of 400; R2-E3ADV
124 of 250; W26 676 of 768 (one fixed-and-resumed provider incident,
`compactbench_adapter/live/README.md` section 3); W27 581 of 600 (439 in the
corrected run plus an invalidated blind first attempt and smoke,
`contextbench/live/README.md` section 5). Zero probe extraction failures in the
probe-protocol runs (R2-E2, R2-E3, R2-E3ADV; `live/README.md` "Budget accounting"
and each suite's README). Everything else in this table is offline and
deterministic; seeds are blake2b-digest based (iteration-1 measurement defect M5
fixed, `research/context_rot_r2/scenario.py`).

## 3. What was found

### 3.1 R2-E1: the fixture bound

With an even mildly lossy summarizer, five compactions on the default arm lose every
unprotected anchor: 0/19 retained (one anchor survives the first compaction as a
head/tail excerpt; the second compaction's 400-char carried-summary decay erases the
rest), while the v1 ledger holds 14/19 flat - constraints 8/8, decisions 4/4,
supersession 2/2, but evidence 0/4 and subagent artifact 0/1 - at 18 429 vs
14 765 mean request chars (+3 664, about +916 tokens/cycle, matching iteration-1's
+913..1 751) (`runs/r2-e1-multicycle.score.json`). The two 0-shaped gaps are the v1
design's, not the fixture's: `EVIDENCE_RE` misses `cargo test: N passed` /
`build succeeded` shapes (defect N1) and `entry_text` is blind to `CustomMessage`
subagent entries (N2) (`reports/context-rot-r2/BUG-LEDGER.md` section 2).

### 3.2 E-INC-1/2a/3: the bound is structural, not an artifact of five cycles

Over 8 cycles and 2 seeds the two decay regimes are stable: default decays 1/19 ->
0/19 by the second compaction and stays 0 at every cycle count; taskState holds
14/19 (seed a) and 13/19 (seed b) flat through cycle 8
(`incremental/results/e-inc1.json`). The k-cycle reruns equal cycle k-1 of the
longer run on both arms (path independence) and an identical rerun reproduced
every field (determinism) (`e-inc2a.json`). Summaries reach a fixed point at cycle 1
(default 1 236 chars / 309 tokens; taskState 2 846 chars / 712 tokens), so growth is
non-compounding; the taskState arm pays the ledger twice per steady cycle (render +
instruction 2 091 + 274 chars, plus the protected block copied into the carried
summary), a net +3 979 chars (+995 tokens) per cycle (`e-inc1.json`
`requestComposition`). `summary_update_policy` is not reachable through the
adapter: a `policy-consolidate-repeated-v1` arm and the env var were byte-identical
to `default`, so the policy's designed effect remains NOT EXECUTED.

### 3.3 E-INC-2b/2c: retention is placement- and seed-dependent

Constraints planted in cycle 3 instead of the opening turn survive worse, not
better: taskState constraint retention drops 8/8 -> 4/8 (seed a) and 7/8 -> 3/8
(seed b), total 10/19 and 9/19 (`incremental/results/e-inc2b.json`). Two verified
mechanisms: W9-F1, keyword-less constraint lines are invisible to `CONSTRAINT_RE` /
`SUPERSEDE_RE` when planted as separate entries (baseline capture works only
because all constraints share one "never drop ..." message); W9-F2, ledger ids are
numbered by insertion and the merge's blanket `retain(!retired.contains(id))`
silently deletes a newly captured item that reuses a retired id. Late supersession
(cycle 5) keeps the new decision but does not retire the old one: the two CORRECTION
lines share more tokens with each other than with the old decision and retire each
other (W9-F4), 15/21 vs 16/21 for early placement, the difference being one more
W9-F2 id collision (W9-F5) (`e-inc2c.json`). Anchor-presence scoring also
overstates current-state correctness: surviving baseline anchors can sit in
SUPERSEDED render lines (W9-F3).

### 3.4 R2-E2: the live model is stronger than the bound, and the ledger is worse than nothing on corrections

With the real GLM 5.3 as summarizer and probe answerer, the default arm shows no
measurable rot at this scale: base 95/95, hard 111/111, anchor retention 19/19 and
37/37 at every cycle on both arms (`live/results.json`). R2-E1's 0/19 is therefore a
pipeline-worst-case bound, not a prediction about this model. The taskState arm
scores 89/95 on the base scenario: 19/19 at rounds 1-2, then 17/19 at rounds 3, 4
and 5, failing exactly the correction probes. The headline finding: the v1 ledger
inverts the user's correction. The user says "forget that; use architecture B ... The
earlier A decision is superseded"; `SUPERSEDE_RE` is token/positional, pairs the two
CORRECTION entries with each other (C12 <-> C10) while ordinary constraint lines
churn into spurious pairs, the render lists the corrections under SUPERSEDED
(history) and the pre-correction "architecture A" decision under DECISIONS (current),
and the summarizer - following the protected state faithfully - writes "use
architecture A ... the B correction is superseded" from cycle 3 on. The probe
"After the correction, which architecture is the current choice?" is answered
`architecture A` (3-rep majority at round 5). The default arm handles the same
correction correctly at every cycle (`live/README.md` Finding 1). The hard scenario
does not contradict this: the same defective rule mis-fires there as constraint
churn (15 of 20 constraint lines in spurious pairs, 5 survive ACTIVE) plus
ambiguity, which the model resolves from the raw transcript text (111/111);
correctness under the v1 ledger is luck of how the regex pairing lands. R2-E2
measured the pre-W11 ledger (the mirror was committed before `1754a3e1b`); the W11
fixes target exactly this mechanism and were re-measured offline on this corpus by
R2-E2-RM (section 3.11).
Operationally (Finding 3): GLM 5.3 reasoning tokens bill against `max_tokens`;
ledger-bearing compactions burned 8k-32k reasoning tokens, the production 8192
budget with a single 2x retry failed `validate_summary` on 3 of 5 base cycles, a
bounded ladder to 65 536 completed all cycles, and taskState summarizer latency
reached 137.8 s per call.

### 3.5 W12 suites: plumbing validated, quality degenerate by construction

All quality scores are bit-identical across all nine arms (overall 0.375 starter /
0.3326 elite) because the mock eval provider returns a constant; these runs validate
plumbing only and no quality claim is made or possible
(`compactbench_adapter/SUITE-RESULTS.md`). The W26 live practice run (section
3.12) later broke exactly this degeneracy, on 8 of the 20 starter cases per arm.
The mock-independent observables do separate the arms: zero method warnings across
all 1 920 custom-arm cycles (every
`compact()` landed a valid cut on the ladder's first candidate), task-state
artifacts are the largest (compression 0.48x starter / 0.74x elite), then plain
(0.55x / 0.90x), oracle (0.78x / 0.79x), echo-first-n (0.83x / 1.12x), and the
built-in baselines collapse to the null floor (108.76x / 178.24x). Per-cycle token
trajectories flatten after cycle 1 for the Rust arms while the oracle grows
monotonically, confirming delta-append mode engaged.

### 3.6 W10 pipeline: ready, empty

The committed ContextBench pipeline loads the 500-instance verified subset of the
pinned upstream commit with sha256 verification, splits dev/holdout 308/192 over
disjoint repo sets (byte-reproducible manifest), mirrors the upstream gold-context
metric layer function by function with citations, and provides a matched A/B
harness with an exact sign test and a tie-corrected Wilcoxon
(`contextbench/dataset.py`, `metrics.py`, `harness.py`). 127 offline tests pass
(`86e8ad09b`). No agent trajectories exist (the W27 practice run of section 3.13
used synthetic gold-derived trajectories instead), arms C (typed state) and D
(source-grounded reconstruction) emit blocked records only, and `patch_success` is
UNAVAILABLE by construction, so no retrieval number is claimed anywhere
(`CODING-TASK-RESULTS.md` section 2).

### 3.7 W8 calibration: the health heuristic does not predict semantic loss

The production context-health score, read as a risk predictor (risk = 1 - score/100)
against `lost_constraint` on the 66-case synthetic corpus (30 positives), scores
ROC-AUC 0.604, Brier 0.252, 10-bin ECE 0.146, and at threshold 0.5 precision 0.542 /
recall 0.433 / FPR 0.306 / FNR 0.567
(`research/context_rot_r2/health/context_health_calibration.json`; generator
`crates/pi-coding-agent/src/core/context_health_calibration.rs`). Utilisation and
compaction cycles are poor predictors of semantic constraint loss. The labels are
generated (they encode the R2-E1 finding that loss is dominated by an unobservable
restatement factor), so this is a harness result, not a field result; nothing in
production reads these metrics.

### 3.8 TypeCompact reproduction: external confirmation of the pinning principle

A deterministic offline reproduction of the CIKM 26 Knowledge Triage
multiturn-stability experiment (upstream pinned `a6ceb01`, their regex reference
classifier, 50 % compaction ratio, seed 11) shows pinned constraints holding
1.000 -> 0.8408 constraint recall by cycle 5 (0.8482 on the 197-config full suite,
stable to cycle 10) while truncation baselines cliff to 0.0071-0.0284
(`repro/typecompact_repro{,_full_suite}.json`). Their committed LLM-arm numbers are
carried as labelled reference-only blocks and are not claimed as ours.

### 3.9 R2-E3: no action-level rot at this scale, and the ledger passes by pairing luck

Five coding-task scenarios (next-file-to-edit under a constraint, corrected
approach, boundary-adjacent exact error, acceptable-set exclusion, post-restart
approach) x 2 arms x 5 cycles: 50/50 action-probe points correct on BOTH arms,
strict == primary on every point, 0 extraction failures, no split majorities,
anchor retention 1/1 at every cycle (`live/e3/results.json`,
`live/e3/README.md` "Results (final)"). TASK-F's simulated restart (session
serialize -> fresh reload) left the restored provider context byte-identical on
both arms (`contextRoundTripIdentical: true`, 39 entries round-tripped). The
taskState arm handled the TASK-C/TASK-F corrections correctly, but by pairing
luck: the correction texts happen to share specific anchors with the old
decision ("synchronous", "nightly"), so the same token/positional supersession
rule that inverted the E2 base correction paired correctly here - the E3 suite
measures baseline action retention, not the ledger's known failure modes.
TASK-E's exact failing-test assertion survived only through the summarizer's
verbatim-anchor discipline; the ledger's evidence regex takes the first matching
line of the tool result and never saw the assertion (N1 again). Ops: 3 of 50
summarizer cycles hit the 8192 budget and completed on the 16384 ladder step,
max summarizer latency 45.3 s, 209 wire calls (155 probes, 53 summarizers, 1
pong). The typed arm was a deliberate stub in this run (`--arm typed` exits 2
with a loud message, never a silent substitution); the typed-arm run
(R2-E5) has since been executed and recorded in `EXPERIMENT-LEDGER.md`.

### 3.10 R2-E3ADV: when supersession cannot pair, the ledger does nothing and the model argues it down

Three adversarial correction families whose corrections share zero or
generic-only tokens with the decision they replace (supersession score 0-2,
below the pairing threshold 3 - proven offline against the real mirror ledger
before the run, with predictions pre-registered), 2 arms x 5 cycles x 3 reps:
30/30 cycle points correct on BOTH arms, 0 extraction failures, anchor
retention 1/1 every cycle (`live/e3adv/results.json`). The mechanism, verified
in `live/e3adv/raw-calls.jsonl`: the ledger retired NOTHING - the stale
decision stayed DECISIONS-current at every cycle while the render header
promised "A SUPERSEDED item is history" with no superseded items to show - and
GLM 5.3 resolved the contradiction in all 15 taskState summarizer outputs by
annotating the stale item as history in its own words ("Treat as history
only", "superseded in practice", "no longer applies to the current
deliverable"). Correctness is the summarizer's achievement AGAINST the
ledger, not a property of it, at +1.5k-1.9k request chars and +0.6k-1.2k summary
chars per cycle (mean summarizer request 16.8k-17.0k chars taskState vs
15.1k-15.3k default). Combined with R2-E2: the v1 ledger on corrections is
EITHER a coin-flip that can invert user intent (shared-anchor luck pairs
wrong: the E2 inversion) OR dead weight the model must argue down (no shared
anchor: the stale decision re-asserted verbatim every cycle). 124 wire calls,
all summarizers finished at 8192 (no ladder steps).

### 3.11 R2-E2-RM: the W11 fix removes the inversion offline; the render cap is the new defect (D21)

A Python port of the current Rust ledger (`live/remeasure/w11_port.py`,
identical to the W11 commit `1754a3e1b`) replayed over the committed W7 corpus:
the fixture pass byte-matched every corpus summarizer request (5 cycles x 2
arms x 2 scenarios plus ladder retries) and reproduced every cycle field of the
committed `results.json`; the post-W11 pass swaps only `build_ledger` at the
exact call site W11 changed. No model calls (`live/remeasure/`, commit
`2fbb14f33`). Verdict, base scenario (the one with the live failure): CLEAN at
every cycle - both corrections are ACTIVE constraints (rendered within the
cap), the pre-correction A decisions are retired to SUPERSEDED with the
corrections as replacements, the mutual pair and the constraint churn are gone,
and applying the same scoring to the PRE-W11 renders reproduces the live
failure exactly. Hard scenario: structurally fixed (all four A decisions
retired by their corrections, 17 spurious pairs -> 4) but with a new
render-level defect: with the churn gone, 26 constraints are active and
CONSTRAINTS_CAP=12 omits the four corrections from the rendered ACTIVE list
(they appear only as SUPERSEDED replacement texts; 9 of 20 constraint lines
are also omitted) - defect candidate D21, since fixed by the correction-carrier
cap exemption (`a9035ce8a`; the replay re-measured strict containment 20/20,
`0b824f358`; see section 5.9 for the refreshed status).
Pre-existing and W11-independent: `EVIDENCE_RE` never matched this corpus's
evidence shapes, so the VERIFIED EVIDENCE section is empty in every render
pre and post; the W11 id-collision half is not exercised by this corpus.

### 3.12 W26 live CompactBench: the arms separate under a live model, and the ledger arm wins

The first model-backed CompactBench run: GLM 5.3 as BOTH the summarizer
(behind the Rust compaction-harness binary - the production path, via a new
env-configured HTTP provider) and the task/eval model (behind the stock
runner), starter slots 0-1 (8 of 20 cases per arm, the ruled Option B budget),
3 drift cycles, one live draw per case, 676 HTTP requests of a 768 ceiling
with one fixed-and-resumed provider incident. Labels CUSTOM-OPTIMUS +
LIVE-MODEL: a practice run, NOT a leaderboard number. Overall:
task-state 0.6844, echo-first-n 0.6531, full-transcript-oracle 0.5781, plain
0.4906; the 0.194 Rust-arm gap is driven by exactly the ledger-mediated
checks (`recall_latest_decision` 0.625 vs 0.125, `assign_a/b` 0.375 vs 0.125,
drift resistance 0.99 vs 0.76, contradiction rate 0.094 vs 0.172)
(`compactbench_adapter/live/README.md` sections 4-5). The mock degeneracy of
section 3.5 is broken: with a real eval model the quality columns separate.
The verbatim oracle is not the ceiling under live eval (worst arm on
`recall_constraint`, 0.500). GLM writes tighter summaries than the lossy
fixture (compression 0.46x -> 0.67x task-state on identical cycle-0
transcripts). Live single-draw runs are not bit-reproducible even at
temperature 0 (two identical smoke draws scored 0.4 vs 0.1); with n = 8 per
arm only the Rust-arm gap is a robust signal.

### 3.13 W27 live ContextBench: the ledger prompt biases retention toward gold lines

A model-backed practice run of the dev subset through the compaction-harness
binary with the live GLM 5.3 summarizer, evaluated by the W10 metric mirror:
26 paired dev instances (deterministic blake2b slice of the 110 size-window
dev candidates; 7 languages, 15 repos; holdout untouched) x 2 arms x 4
append+compact cycles, delta-append sessions, 439 summarizer requests for 208
cycle attempts, mean latency 20.4 s, 0 transport retries, 47 ladder climbs, 0
error calls (`contextbench/live/`). Labels CUSTOM-OPTIMUS + LIVE-MODEL. Arms:
A = baseline (`taskState off`) vs B = task-state-ledger (`taskState prompt`)
- a documented substitution, since W10's offline arm B (jev-relevance) is not
expressible through the compaction surface. Method caveat: no repository
checkouts exist under the offline waiver, so trajectories are synthetic
explorations built from the gold annotations plus same-repo distractor reads;
the metrics measure RETENTION of the gold context through compaction, not
free-form exploration, and `patch_success` stays UNAVAILABLE by construction.
Headline: gold file references survive compaction almost fully in both arms
(coverage 0.974) and file granularity barely separates them; line granularity
does - arm B keeps a significantly larger share of gold lines among retained
lines (line precision 0.586 vs 0.320, 18+/4-, sign p = 0.004, Wilcoxon p =
0.003; by raw counts arm B ends 69% gold / 31% distractor lines, arm A 47% /
53%). The parsed ledger itself populated in only 34 of 104 arm-B cycles, so
the effect observed is prompt-level, not ledger-level.

### 3.14 R2-E5: the typed arm measured - 15/19 pre-D25, 13/19 on the integrated tip; two live defects found and forced

Three parts (`live/e5/`, EXPERIMENT-LEDGER R2-E5). Part 1, the E1 fixture
(19 probes x 5 cycles, all three arms in one pass, offline adapter): default
0/19, v1 taskState 14/19, typed 15/19 at the pre-D25 implementation (W29,
committed E5); 13/19 on the integrated tip (W46/W47 re-measurements: the D25
step boundary correctly retires two Step-scoped episode-classified probes,
q-constraint-6/7; decisions 4/4, evidence 1/4, supersession 2/2 unchanged;
verifier 5/5 both). The pre-D25 typed ledger holds 23 records at a stable
777/800 hot-token budget (min(2048, floor(3200/4))); the verifier passed 5/5
cycles with zero repairs, at +886 mean request chars over v1 (on the tip the
ledger runs 23 records at cycle 1, then 19 from cycle 2 at 665 hot tokens,
verifier still 5/5 with zero findings). The fixture measures pipeline
protection under the deterministic lossy summarizer; the two retired probes
are Step-scoped episodes by classifier design (they carry no constraint
marker), retired at the first merge that does not restate them - the
documented classifier-scoping outcome under the D25 step boundary, not a
regression. All six pre-registrations confirmed at the pre-D25
implementation: the composition is exactly the predicted 15/19 (q-evidence-0
recovered through the cargo-test-line classifier; the other three evidence
lines match no typed tool category and are lost in both arms; the subagent
probe is lost - agentMessage custom entries are machine text by design).
Part 2, live (e3 tasks A/C/E, typed vs default, 5 cycles, 3 reps, 123 wire
calls): default 15/15 plus baselines; typed correct on
every point that ran (A 5/5, C 5/5, E cycle-1 plus baselines), 0 extraction
failures - but TASK-E typed halted at cycle 2 with unresolved-reference
verifier failures (5/3/3/3), the availability bug recorded as D24 (id
re-derivation under restatement-merge; failure direction safe - conversation
preserved, compaction refused), the first live falsification of a
typed-mode pre-registration. Part 3, the E3ADV C1 typed cell (21 wire calls):
correct 5/5 plus baseline, 3/3 reps strict, anchor retained - but no
SUPERSEDED section at any cycle (all 6 typed summarizer requests): the
pairing was still token-overlap-based, recorded as D29. Both defects are
fixed since the run (D24: W37 `891702c32`, worker `8bc0811fb`; D29: W41
`4315f9154`, worker `e616a3126`,
live-verified on the same C1 cell - 5/5 cycles retired with the replaced-by
link and SUPERSEDED rendered at cycles 1-4, all probes 3/3 strict), so the
numbers above describe the pre-fix module; the re-measure E5-RM (W47,
EXPERIMENT-LEDGER R2-E5-RM, `research/context_rot_r2/live/e5/rm/`) has since
landed on the integrated tip: TASK-E typed completes 5/5 cycles with the
verifier passing every cycle (the D24 halt is gone; 0 unresolved references
anywhere), and the C1 cell renders SUPERSEDED at every cycle 1-5 with every
accepted summary copying the retirement. The E5-RM fixture cell holds the
typed arm at 13/19 (the Part 1 note above); the unaffected A/C live cells
were not re-run (both 5/5 in the committed run). Budget: 144 new wire calls
for E5 (within the 250/80 caps) plus 46 for the E5-RM live cells (of its
<= 120).

## 4. What shipped

### 4.1 Defect fixes (all on this branch; guards named)

| defect | fix (commit) | guard tests |
| --- | --- | --- |
| D1 threshold hook vs executor (turn stall) | `0a896e639` shared predicate `threshold_auto_compaction_ready` gates both | `d1_toggle_off_over_threshold_does_not_stop_the_turn` (`tests/compaction_turn_flow.rs`) |
| D2 stopped turn not resumed | verified intact on this base; pinned by tests, no production change (`b6487b9cd`) | `d2_interactive_turn_crossing_threshold_compacts_and_continues`, `d2_queued_user_prompt_completes_after_threshold_compaction` |
| D5 no-op compaction is a paid model call | `3b08e3f2e` empty summarize window skipped regardless of `previous_summary` | `repeated_trigger_with_no_new_material_resolves_to_no_op`, `new_material_after_the_boundary_still_prepares`, `a_no_op_skip_leaves_the_next_real_compaction_boundaries_intact` (`tests/compaction_summary_path.rs`) |
| D6 extension summaries bypass validation | `289f3c2e1` `validate_extension_compaction_summary` (same structural handoff check as the model path); the `agent_session.rs` extension compaction arm now applies it (`6e1c3a139`) | `extension_summary_guard_rejects_empty_files_only_and_unsectioned_summaries` |
| D8 migration drops the compaction boundary | `a56631631` falls back to the first entry after the compaction | `migration_keeps_a_durable_boundary_when_first_kept_index_is_out_of_bounds`, `migration_ignores_a_boundary_pointing_at_the_session_header` (`src/core/session_manager.rs`) |
| D9 fallback boilerplate becomes `previous_summary` | `720428967` dedicated walk skips `deadlineFallback` and provider checkpoints | `deadline_fallback_boilerplate_is_not_the_next_previous_summary`, `fallback_only_history_yields_no_previous_summary` |
| D10 branch summaries lose tool output | `0e25a7db1` tool results pair with their assistant calls, evidence-aware excerpt, budget walk keeps the call of a kept result (the "never retry" sub-item remains open) | `branch_summary_input_carries_the_failing_tool_result`, `branch_summary_input_skips_tool_results_with_no_text` (`tests/rot2_branch_summary.rs`) |
| D12 memory recall is unfenced instruction-shaped text | `021678796` MEMORY_RECALL_TYPE custom text fenced as `<memory_data>` at `convert_to_llm` time (soft-fence residual documented) | `convert_to_llm_fences_memory_recall_text_but_not_other_custom_types` (`src/core/messages.rs`) |
| D13 recall fails closed silently | `157316268` fail-closed surfaces (foreign-only corpus, short-skip, stale-source) emit rate-limited warnings with counts; partial skips stay silent | `foreign_only_corpus_fails_closed_and_surfaces_the_reason`, `budget_short_skip_fails_closed_and_surfaces_the_reason`, `partial_recall_skips_stay_silent` (`src/core/memory/search.rs`) |
| D14 read-files inventory always empty | `439808412` reads recorded structurally from `path`/`paths` arguments of non-edit tool calls | `non_edit_path_tool_calls_populate_the_read_inventory`, `compact_persists_a_non_empty_read_files_inventory`, `path_arguments_of_non_edit_tool_calls_record_reads` (`src/core/compaction/utils.rs`) |
| D16 user images cost zero tokens | `fa4ccc8f2`/`0bb973e43` shared `IMAGE_BLOCK_CHARS` (4096 chars = 1024 tokens) across estimator paths; `4dea91d72` aligns kernel parity | `rot2_user_image_blocks_cost_tokens_d16` (`tests/rot2_multicycle.rs`), `image_blocks_cost_4096_chars_1024_tokens` (`request_tokens.rs`), `compaction_kernel_parity` 23/23 |
| D17 estimate excludes system prompt and tool schemas | `fa4ccc8f2` `request_tokens.rs` request-level estimator; `0bb973e43` carries the split on `ContextUsageEstimate`, adds parts on the no-usage fallback path only; `2200211fe` documents the tray underestimate | `rot2_request_level_estimate_adds_system_prompt_and_tools_d17`, `message_list_estimates_understate_request_utilisation`, `empty_inputs_score_zero`...`total_is_exactly_the_sum_of_its_parts` |
| W9-F2/F4/F5 ledger ids and supersession (v1) | `1754a3e1b` content-derived ids (`C-a1b2c3d4`, sha256 of the dedupe key), correction carriers excluded as supersession targets, newest correction wins the same target; old positional ids still deserialize | `w9_f4_corrections_supersede_the_older_decisions_not_each_other`, `w9_f2_a_late_decision_reusing_a_retired_id_is_not_deleted`, `w9_f2_and_f5_late_constraints_reusing_retired_ids_survive_with_anchors`, `ids_are_content_derived_and_stable_across_merges`, `a_later_correction_wins_the_same_target_and_keeps_the_earlier_correction`, `a_retired_id_suppresses_only_the_exact_same_content`, `old_positional_ids_round_trip_and_merge_without_losing_new_items` (`tests/rot2_ledger_fixes.rs`) |
| D11 stale harness digests accumulate in the provider context | `30588707d` prune superseded digest user turns from the provider context (k harness changes leave exactly one live digest) | `k_harness_changes_leave_exactly_one_digest_in_the_provider_context`, `compaction_keeps_the_pruned_provider_context_digest_clean` (`tests/rot2_harness_digest.rs`, `d9ceea230`) |
| harness F1/F4/F5 | `49f473d8d` `continue_recent` session persistence, chars/4 usage anchors, flush-on-append | `probe_harness_behavior.py` + the 13 adapter checks (`research/context_rot_r2/compactbench_adapter/`) |
| D18 compaction failure invisible to the model | `8c558dd7b` one bounded model-visible live notice (240-char failure excerpt) replacing its predecessor; skipped/cancelled silent, success drops it | `d18_failure_notice_is_model_visible_and_bounded` (`agent_session.rs`), `d18_failed_auto_compaction_surfaces_a_bounded_notice_to_the_model`, `d18_successful_compaction_does_not_surface_a_failure_notice` (`tests/rot2_defect_wave2.rs`) |
| D19 live-list producers without drain/cap | `4769f42f4` both producers (deferred `pending_next_turn_messages`, unpersisted outcomes) capped at 200 entries with the evicted count folded into a front `live_disclosure_dropped` marker | `d19_live_disclosure_lists_are_bounded_with_a_dropped_marker` (`agent_session.rs`) |
| D20 summarizer request divergence | `e44faff08` the summarizer wire request is byte-exact-pinned to `serialize_conversation` over `convert_to_llm` of the same preparation | `d20_summarizer_request_equals_the_serialized_model_visible_conversation` (`tests/rot2_defect_wave2.rs`) |
| D21 render cap omits authoritative corrections | `a9035ce8a` correction carriers exempt from the section caps and priority-ordered ahead of plain items | the seven `rot2_render_cap` cases; offline replay of the hard corpus: strict containment 20/20 (`0b824f358`, `live/remeasure/d21_render.py`) |
| D22-D27, D29 (W35 red-team, typed mode) | D22 `51c9a6a59` (superseded-render direction), D23 `891702c32` (reference grammar), D24 `891702c32` (ids stable across restatement), D25 `a3997b5d6` (step-scoped records retire at the merge boundary, `retiredRefs`), D26 `51c9a6a59` (malformed payload salvaged, not dropped), D27 `51c9a6a59` (all-caps claims cannot evade the fabricated-verified check), D29 `4315f9154` (provenance-based supersession pairing, live-verified) | `tests/rot2_typed_adversarial.rs` (the six former red tests un-ignored and green, plus the D25/D29 pins), `--lib typed_state` |
| D28 native compaction resets the typed chain | `1e6e5ff9d` the native path persists the ledger it received unchanged | `tests/rot2_native_typed_chain.rs` (2/2) |

Production-path verification of the W11 fix: the `w9-a-late3c` rerun regains the
id-collision constraint (4/8 -> 5/8; the remaining three are the known CONSTRAINT_RE
capture gap) and `w9-a-latecorr5c` keeps both corrections current (`1754a3e1b`
commit message). R2-E2-RM (section 3.11) extends this to the live corpus offline:
the post-W11 renders are clean on the base scenario at every cycle. Subagent
constraint propagation across compaction is pinned
end-to-end by `tests/rot2_subagent_constraints.rs` (`0c920a95e`):
`rot2_w8_parent_compaction_then_child_task_prompt_carries_constraints`,
`rot2_w8_agent_message_task_crosses_compaction_mid_child`,
`rot2_w8_child_result_after_parent_compaction_resolves_on_compacted_context`.

### 4.2 Typed state v2: shipped and wired end to end

`crates/pi-coding-agent/src/core/compaction/typed_state.rs` (introduced by
`c948905a3` at 3 033 lines and 60 unit tests; 74 after the D21-D29 wave at
the W49 verification tip and 78 at this closing tip (the D30 fix and its
budget-caveat pin added the four user-stated-decision tests), section 4.4):
14 `TypedInformationKind` values with role-gated
classification (only user-role text yields user constraints / corrections / safety
boundaries / acceptance criteria; tool output saying "must" never does; validated on
a 71-snippet labelled corpus with per-class precision/recall >= 0.9 asserted), a
cap-free ledger with stable content-derived sha256 ids, dedupe plus fuzzy
restatement merge, explicit supersession (later user correction wins, both records
kept), task succession and revival, a priority budget allocator with an
`OverflowReport` and `UnsafeCompaction` when mandatory material cannot fit, a
deterministic verifier of the compact artifact against the ledger, and stable
renders (`render_hot_state`, `render_durable_index`, `render_allocation`). The
module is pure and deterministic (no I/O, no clocks, no randomness).

The W15 integration wires it end to end - opt-in, default off:

- settings + kernel: `compaction.taskState` accepts `"typed"` alongside
  `"off"`/`"prompt"` (`resolve_task_state_mode`; the settings manager routes the
  value through it so typed reaches the kernel, `4be9623b0`; unknown values fail
  safe to off). `prepare_compaction` builds via `build_typed_state` over the
  leaving window and merges with the previous compaction's persisted ledger
  (`details.typedState`, a new field that never overloads the v1 taskState
  payload); `compact()` renders the budgeted allocation as the protected block,
  appended outside `<user-instructions>` with the typed copy instruction at the
  v1 `LEDGER_INSTRUCTION` attachment point (`338b920c3`).
- budget: the hot-state budget is derived from the summary output budget
  (min(2048, floor(summary_budget/4)) tokens) so the block always fits alongside
  the summary the same request produces; an unsafe allocation (mandatory material
  dropped) fails the episode BEFORE any model call through the existing `Err`
  channel, with the overflow detail (ids, kinds, budget) in the error text.
- verifier: after the summary passes structural `validate_summary`,
  `verify(summary, ledger)` runs; a missing-required-item failure gets one
  bounded deterministic repair (missing protected lines appended verbatim to
  their matching summary sections, no model call) and is re-verified; any other
  failure, or a still-failing repair, is a summary-validation failure. The
  outcome persists on the entry as `typedVerification` (passed / repaired,
  finding counts). Post-wave behaviour (W37 `891702c32`): the reference
  grammar matches exactly what the renderer emits, so a verbatim copy of
  user text containing a bracketed ticket token verifies cleanly (D23), and
  record ids carry across merges so a summary quoting an earlier `[id]`
  still resolves (D24); malformed prior ledgers surface as
  `typedVerification.malformedPriorLedger` with dropped/repaired counts
  instead of failing silently (D26, `51c9a6a59`).
- guard: `crates/pi-coding-agent/tests/rot2_typed_integration.rs` (`40bead288`,
  928 lines, 9 tests) drives the real pipeline over the real `SessionManager`
  JSONL persistence: mode resolution and fail-safe
  (`typed_mode_resolution_accepts_typed_and_fails_safe_on_unknown_values`), the
  budget formula
  (`typed_hot_state_budget_is_a_capped_quarter_of_the_summary_budget`), cargo-test
  lines retained as VERIFIED_TOOL_RESULT (the v1 N1 gap) and subagent tool reports
  as SUBAGENT_STATE
  (`prepare_compaction_builds_a_typed_ledger_from_tool_results_and_subagent_state`),
  the protected block in the captured summarizer request and the ledger
  round-tripping into a second `prepare_compaction` by record id
  (`compact_typed_mode_feeds_the_protected_block_and_round_trips_the_ledger`),
  unsafe allocation failing the episode
  (`unsafe_typed_allocation_fails_the_episode_before_any_model_call`), the
  deterministic repair (`missing_constraint_triggers_the_deterministic_repair`),
  a superseded decision presented as current failing verification
  (`superseded_decision_presented_as_current_fails_verification`), and v1 interop
  (`v1_task_state_details_still_deserialize_alongside_the_typed_fields`,
  `v1_prompt_mode_still_works_unchanged`).

Known boundary, pinned by that suite: custom `agent_message` entries are not
classified (`entry_role_text` classifies only `Message` entries), so a child-agent
report is not retained as SUBAGENT_STATE by the committed module. One further
classifier gap was open at the W49 writing - defect D30, a decision stated in
USER text falling through to LowPriorityEpisode (Step scope) and retired at
the first merge while the same sentence from the assistant classified as
VerifiedDecision (Task scope), filed `7bec70f09` - and is fixed at this tip
by W46 (`10e2b36e6`): a user unit matching DECISION_RE now classifies as
VerifiedDecision with Task scope, with the assistant path remaining the
corroboration path. The wave's fixes then went
far past that boundary (section 4.4): merge is the enforced step boundary
with a 512-record durable cap (`a3997b5d6`), the verifier's reference
grammar matches exactly what the renderer emits (`891702c32`), malformed
prior ledgers salvage loudly instead of dropping (`51c9a6a59`), and native
provider compaction persists the typed ledger unchanged (`1e6e5ff9d`)
while the deadline fallback keeps its carry-through semantics.

### 4.3 Benchmark infrastructure

1. The multicycle adapter `crates/pi-coding-agent/tests/rot2_multicycle.rs`
   (`e249930d9`): research entry point `rot2_production_multicycle` (`#[ignore]`d,
   driven by `ROT2_SCENARIO`/`ROT2_RESULT`), executing the full pipeline
   in-process; the D16/D17 guards live in the same file. Python orchestration:
   `research/context_rot_r2/orchestrate.py`, `scenario.py` (blake2b seeds),
   `scoring_r2.py` (`scoringVersion r2.1`).
2. The harness binary `crates/pi-coding-agent/src/bin/compaction_harness.rs`
   (`b04d1f040` + `49f473d8d`): offline external-driver access to the real pipeline
   (append, compact, restore) with the F1/F4/F5 fixes.
3. The live driver `research/context_rot_r2/live/` (`fb089ee6e`): a Python mirror
   of the Rust path (`rust_mirror.py`, prompts auto-extracted in `rust_prompts.py`)
   validated byte-for-byte against the stored R2-E1 result
   (`fixture-validation.json`), a one-line `ANSWER:` probe protocol (`live-v1`
   matchers, majority scoring), and the orchestrator `run_r2e2.py` with retries,
   resume and a raw-call log. The same mirror hosts the E3 coding-task suite
   (`live/e3/`, `d8e318f81` + `346f6c4f3`, results `c5107fc31`), the E3ADV
   adversarial suite (`live/e3adv/`, `76eefbf9f` + `754bb75a1`) and the E2-RM
   re-measure harness (`live/remeasure/`, `2fbb14f33`).
4. The incremental driver `research/context_rot_r2/incremental/` (`0229dee3a`,
   `91ac4b2cb`): 16 adapter runs with analysis outputs.
5. The CompactBench adapter `research/context_rot_r2/compactbench_adapter/`
   (`00cbdd3ab`, `1ac57fe3c`): adapter 0.2.0 in delta-append mode inside the
   official runner, the harness-behavior probe, and 13 offline checks.
6. The ContextBench pipeline `research/context_rot_r2/contextbench/`
   (`ab71234bc`..`86e8ad09b`): dataset, metrics, A/B harness (section 3.6), and
   its live practice driver `contextbench/live/` (`da38b1edf` + `3e5880dd5`,
   artifacts `32a430f08`).
7. The calibration harness
   `crates/pi-coding-agent/src/core/context_health_calibration.rs` (`6f0d592c6`,
   17 tests) with the committed corpus under `research/context_rot_r2/health/`.
8. Live-model support in the harness binary: `summarizer: "live"` in
   `crates/pi-coding-agent/src/bin/compaction_harness.rs` routes the production
   compaction path through an env-configured OpenAI-compatible endpoint with the
   8192->65536 ladder and 2s/4s/8s backoff, offline by default, no new
   dependencies (`6ea85dc10`); the fixture mode stays byte-identical.

### 4.4 The typed-state red team and the D21-D29 fix wave

The W35 red team (`f7bc6306c`) added
`crates/pi-coding-agent/tests/rot2_typed_adversarial.rs` (1 151 lines): one
adversarial test per attack surface against the typed integration (role-gate
poisoning via tool output, starved hot-state budgets, the repair bound,
three-cycle supersession merges, v1/typed details interop, env precedence,
the W27 ledger-population concern). Eight tests pinned the guarantees that
hold; six failed red-first and became D22-D27 (each `#[ignore]`d with its
D-number, runnable red with `-- --ignored`), and D28 was code-cited in the
same pass. With D21 (found by R2-E2-RM) and D29 (found by R2-E5 part 3) the
wave's scope was the nine defects D21-D29, all fixed on this branch:

| defect | fix (commit) | guards |
| --- | --- | --- |
| D21 render cap omits corrections | `a9035ce8a` (W32) | `rot2_render_cap` (7); offline replay `0b824f358` |
| D22 inverted retirement render | `51c9a6a59` (W38) | `rot2_typed_adversarial` |
| D23 ticket tokens fail verification | `891702c32` (W37; worker `8bc0811fb`) | `rot2_typed_adversarial` + lib grammar tests |
| D24 record id churn across merges | `891702c32` (W37) | `rot2_typed_adversarial` + lib tests |
| D25 unbounded step records | `a3997b5d6` (W36; worker `a6da817e3`) | `rot2_typed_adversarial` + lib tests |
| D26 silent malformed-ledger drop | `51c9a6a59` (W38) | `rot2_typed_adversarial` + lib salvage tests |
| D27 all-caps heading evasion | `51c9a6a59` (W38) | `rot2_typed_adversarial` |
| D28 native compaction drops the ledger | `1e6e5ff9d` (W39; worker `d41c74c83`) | `rot2_native_typed_chain` (new suite, 2 tests) |
| D29 token-overlap pairing | `4315f9154` (W41; worker `e616a3126`) + `e71c21563` | `rot2_typed_adversarial` (3 pins) + live C1 cell `621dc462f` |

The mechanisms are recorded in `EXPERIMENT-LEDGER.md` DEFECT-WAVE-2. The W49
verification battery (this branch, at the integration tip `71e770c5e`,
every number this worker's own run): `rot2_typed_adversarial` 23 passed,
0 failed, 0 ignored (the six formerly-`#[ignore]`d defect tests are green);
`rot2_typed_integration` 9/0; `rot2_native_typed_chain` 2/0;
`rot2_multicycle` 5 passed + 1 ignored (the research adapter);
`rot2_render_cap` 7/0; `rot2_ledger_fixes` 7/0; `--lib typed_state` 74/0;
`--lib core::compaction` 160/0; plus the `compaction_summary_path` 8/8 and
`compaction_parity_suite` 14/14 spot checks (REGRESSION-GATES.md section 10).
Open after the wave at the W49 writing: D30 (a decision stated in user
text classifies as LowPriorityEpisode, Step scope, and is retired at the
first merge; filed `7bec70f09`) and the W41 cycle-5 budget-overflow caveat
(`621dc462f`). Both are since resolved by W46 on this branch: D30 fixed
(`10e2b36e6`, user-stated decisions classify as VERIFIED_DECISION with Task
scope) and the caveat accepted-and-pinned (`d00ff2067`,
`tight_budget_drops_superseded_history_but_keeps_the_pairing_line`). The
affected E5 cells were re-measured by R2-E5-RM (W47, section 3.14); the
D29 fix was already live-confirmed on the E3ADV C1 cell (`621dc462f`). The
W48/W47 post-wave audits filed D31-D33: D31 fixed (W52, `6041f71c9`,
retired ids no longer vouch for verified claims, red test un-ignored and
green), D32 fixed (W53, `53ab2e3d9`, the native arm reports the salvage of a
malformed prior ledger) and D33 open (absorbed-record reference stranding,
`2e727e0b6`; fix in flight on `rot2/w54-d33`, not in this tree). The W16 v1
supersession refinement landed between the waves (W51: `a112034dc` +
`611d2312c` + `3de7ef222`) and closes the W9-F1/F3/F4 family in the v1
ledger (rot2_supersession 4/4, lib task_state 21/21, rot2_ledger_fixes 7/7,
rot2_render_cap 7/7).

## 5. What a reviewer should check

1. The typed mode is wired (`338b920c3`, `40bead288`, `4be9623b0`; section
   4.2), measured (R2-E5: fixture 15/19 typed vs 14/19 v1 vs 0/19 default at
   the pre-D25 implementation, W29, committed E5 - 13/19 on the integrated
   tip per the W46/W47 re-measurements: the D25 step boundary correctly
   retires two Step-scoped episode-classified probes; decisions 4/4,
   evidence 1/4, supersession 2/2 unchanged; verifier 5/5 both; live parts
   2-3) and red-teamed (W35 `f7bc6306c`: eight guarantees pinned,
   seven defects D22-D28 filed, six of them red-first). Every defect the
   measurements and the red teams surfaced in the typed path is fixed at
   this tip (D21-D29, section 4.4; D30 by W46 `10e2b36e6`; D31 by W52
   `6041f71c9`; D32 by W53 `53ab2e3d9`) except D34 (fix in flight, W71): D33 was fixed by W54 (`f0b125a84`), closing the post-wave set
   in flight on `rot2/w54-d33`, not in this tree). The W49 battery is the
   recorded green run at `71e770c5e` (REGRESSION-GATES.md section 10):
   adversarial 23/0/0, integration 9/0, native 2/0, multicycle 5+1,
   render_cap 7/0, ledger_fixes 7/0, lib typed_state 74/0, lib
   core::compaction 160/0; each later fix (D30, D31, D32) landed with its
   own guards run green by its fixing worker (BUG-LEDGER rows), and the
   landed-tip full battery is the closing gates worker's record. The W41
   cycle-5 budget-overflow caveat is accepted-and-pinned (`d00ff2067`).
   The E5-RM re-measure (W47, EXPERIMENT-LEDGER R2-E5-RM) re-ran the
   affected live cells on the integrated tip: TASK-E typed 5/5 cycles with
   no halt (verifier passed every cycle, 0 unresolved refs) and the E3ADV
   C1 cell rendering SUPERSEDED at every cycle 1-5 with the retirement
   copied; the unaffected A/C live cells stand at their committed 5/5. The
   typed mode is user-documented (`README.md` `a5362aa63`; the settings
   reference `resources/agent/docs/settings.md` and the settings-manager doc
   comments `0a35fbfa6`), and the v1 ledger's W9-F1/F3/F4 family is closed
   by the W16 refinement (W51, `rot2_supersession` 4/4).
2. Gate coverage: `REGRESSION-GATES.md` records the full W25 pass at
   `29f1f0c92` (with per-suite SHAs back to `f7bc6306c`) and the wave-2
   battery at the final tip `71e770c5e` (W49, section 10): the wave suites
   plus the `compaction_summary_path` and `compaction_parity_suite` spot
   checks, all green. The W25-recorded suites whose sources the D21-D29 wave
   did not touch (the other `compaction_*` gates, the wider `--lib compaction`
   filter, the Python research suites) were not re-run in the wave pass and
   stand at their recorded SHAs. The two known `--lib compaction` failures
   are the documented environment pair (uv missing on the test child's PATH;
   the scheduling-sensitive painted-frame tray test that passes serialized)
   and predate this campaign.
3. R2-E2 is a Python mirror, not the Rust binary: it was validated byte-for-byte
   against the stored R2-E1 fixture result, but the live run documents seven
   deviations (`live/README.md` "Deviations from the Rust path"): summarizer budget
   8192 vs the formula's 3200, probe-context rendering with flattened orphan tool
   results, a research probe system prompt, prompt variant `v2` (the adapter keeps
   `legacy`), no retry/checkpoint mirroring, deterministic session ids, and UTF-8
   byte-length request sizes. The inversion finding is nevertheless verified against
   the rendered ledger text itself, not only against probe answers. It measured
   the pre-W11 ledger; R2-E2-RM (section 3.11) replayed exactly this corpus with
   the W11 port (the E3/E3ADV family inherits the same mirror and deviations;
   the W26/W27 runs instead used the built harness binary on the production
   path).
4. The hard-scenario first run scored 26/37 because probe questions 8-19 aliased
   onto probes 0-7; the fix re-ran the affected probes and produced the final
   111/111 (`live/README.md` "Post-hoc fixes" item 4). The base scenario was
   unaffected.
5. The calibration corpus labels are generated, not observed; the corpus encodes
   the R2-E1 finding by construction. Treat AUC 0.604 as a harness result; replace
   the generator with real outcome labels before citing it as a model of live loss.
6. CompactBench mock scores measure nothing about retention (constant answers);
   only the mock-independent observables (method warnings, compression, per-cycle
   trajectories) separate arms (`SUITE-RESULTS.md` caveats). The W26 live run
   (section 3.12) is the only committed break of that degeneracy, on 8 of 20
   starter cases per arm, one live draw per case - practice-scale evidence only.
7. Anchor-presence scoring overstates current-state correctness (W9-F3): an anchor
   surviving inside a SUPERSEDED render line counts as retained.
8. The live practice runs carry their own caveats: W26 is single-draw and not
   bit-reproducible even at temperature 0 (two identical smoke draws scored 0.4 vs
   0.1); W27's effect is prompt-level (the parsed ledger populated in 34 of 104
   arm-B cycles) over synthetic gold-derived trajectories; E3ADV's correctness is
   the summarizer arguing the ledger down, not ledger behaviour. None of the three
   is confirmatory at its sample size.
9. The open defects that can still damage a real session: D7 (incompatible
   provider checkpoint replays pre-compaction messages; verified-open, fix
   deferred with the rationale in BUG-LEDGER section 1) and D10's "never
   retry" sub-item. The D18/D19/D20 recovery landed (W30: `8c558dd7b`,
   `4769f42f4`, `e44faff08`), the D21 render-cap fix landed (`a9035ce8a`,
   W32), the red-team wave D22-D28 plus the D29 token-overlap pairing are
   fixed (section 4.4), D30 is fixed (W46 `10e2b36e6`, user-text decisions
   now classify as VERIFIED_DECISION with Task scope) with the W41 cycle-5
   budget-overflow caveat accepted-and-pinned (`d00ff2067`), and the
   post-wave findings D31 (W52 `6041f71c9`) and D32 (W53 `53ab2e3d9`) are
   fixed - leaving D34 (the W63 battery regression) the typed mode's one open item (fix in flight on
   `rot2/w54-d33`, not in this tree).

## 6. Recommendation for defaults

| mechanism | default | decision | reason |
| --- | --- | --- | --- |
| `compaction.taskState` v1 ledger | `off` (unchanged) | KEEP OFF | iteration-1: no recall gain, +913..1 751 tokens/cycle; R2-E2: the ledger inverted a user correction under a live model (89/95 vs default 95/95) and its summarizer calls burn 8k-32k reasoning tokens with latency up to 137.8 s; E3ADV: when its supersession cannot pair it does nothing and the model argues the stale item down (+1.5k-1.9k request chars/cycle); the W11 fix renders clean offline on the base corpus (E2-RM), the D21 cap it exposed is fixed (`a9035ce8a`, W32) and the W16 clause-level refinement (W51, `rot2_supersession` 4/4) closes the W9-F1/F3/F4 misclassification family - but the E3ADV corpus was never re-run on the repaired ledger, the token cost stands, and the mode is documented as legacy in the settings reference; the KEEP OFF verdict rests on the live inversion, the live cost and the E3ADV dead-weight result, none retracted by any fix |
| typed state v2 | `off`; opt-in via `compaction.taskState = "typed"` | OPT-IN; measured and red-teamed, not a default | wired end to end (`338b920c3`, `40bead288`, `4be9623b0`; section 4.2) and measured: R2-E5 part 1 beats control B on the fixture at the pre-D25 implementation (typed 15/19 vs v1 14/19 vs default 0/19; verifier 5/5 cycles, stable 777/800 hot-token budget, +886 mean request chars) and scores 13/19 on the integrated tip (W46/W47 re-measurements: the D25 step boundary correctly retires two Step-scoped episode-classified probes; decisions 4/4, evidence 1/4, supersession 2/2 unchanged; verifier 5/5 both - the two probes are Step-scoped episodes by classifier design, not a regression). Live parts 2-3: TASK-E halted at cycle 2 pre-fix (the D24 id-churn mechanism) and the E3ADV C1 cell showed no SUPERSEDED render pre-D29 - both resolved live by the E5-RM re-measure (W47): TASK-E 5/5 cycles, no halt, verifier passed every cycle; SUPERSEDED rendered at every cycle 1-5 with the retirement copied. The W35 red team then found seven more defects (D22-D28), all fixed (section 4.4), with the W49 battery green at `71e770c5e`; D30 is fixed (W46 `10e2b36e6`) and the cycle-5 budget caveat is accepted-and-pinned (`d00ff2067`). Open at this tip: D33 only (absorbed-record reference stranding, filed by E5-RM itself; fix in flight, not in this tree) - D31 and D32 are fixed (W52 `6041f71c9`, W53 `53ab2e3d9`). Other evidence remains prompt-level or v1-level (W26, W27, E2-RM). No default change on this evidence: the live record is one model at practice scale, and the tip fixture count sits below control B (13/19 vs 14/19, the two-probe delta being the D25 step boundary retiring Step-scoped episode-classified probes - honest scoping: the fixture measures pipeline protection under the deterministic lossy summarizer, and the two retired probes are episodes by classifier design, not a regression). The opt-in is what ships: the README and the settings reference document `compaction.taskState = "typed"` as the non-default mode (`a5362aa63`, `0a35fbfa6`) |
| defect fixes D1/D5/D6/D8/D9/D10/D11/D12/D13/D14/D16/D17 + W11 ids + harness F1/F4/F5 | always on | KEEP | correctness fixes with regression guards, not policies; none adds a model call |
| evidence-recovery tool excerpt (iteration-1) | always on | KEEP | 0/4 -> 4/4 at the same budget (iteration-1 E4); unchanged this campaign |
| CONTEXT HEALTH indicator | display only | KEEP, DO NOT GATE | calibration AUC 0.604: the pressure/cycle heuristic does not predict semantic loss, so it must not gate any compaction decision |
| CompactBench adapter + harness binary | opt-in tooling | KEEP | validated end to end (13/13 checks, 1 920 clean mock cycles) and exercised live once at practice scale (W26, 676 requests); fixture mode unchanged and offline |

## 7. What this report does not claim

- No official scores. Every CompactBench run is CUSTOM-OPTIMUS (mock eval provider
  or the W26 single-draw live practice run; public practice suites, custom adapter
  methods, scorer 2.0.0); every ContextBench number is the offline scaffolding or
  the W27 live practice run over synthetic gold-derived trajectories; RepoQA,
  SWE-bench and Terminal-Bench were not attempted.
- No cross-model claims. The only live-model evidence is GLM 5.3 on one DGX K3
  endpoint, temperature 0, with the documented repetition plans (3 reps on final
  points, 1 on trend points; each run's README). The iteration-1 surfaces were
  not re-run on it.
- No claim that rot was eliminated or that the typed design is proven end to
  end. H-r2-3 is supported by the wired integration, the R2-E5 fixture win at
  the pre-D25 implementation (15/19 vs control B's 14/19; 13/19 on the
  integrated tip - the D25 step boundary retiring the two Step-scoped
  episode-classified probes, episodes by classifier design, not a regression)
  and live-correct cells, but R2-E5 part 2 also produced the campaign's
  first live falsification of a typed-mode pre-registration (TASK-E halted
  at cycle 2, the D24 mechanism, since fixed and resolved live by the E5-RM
  re-measure, W47: 5/5 cycles, no halt). The red team found eight more
  defects in the typed path after the wiring (D22-D28), all fixed
  (section 4.4; D30 fixed and the cycle-5 budget caveat accepted-and-pinned
  by W46; D31 and D32 fixed by W52/W53, D33 by W54 `f0b125a84`, leaving D34 (W63 battery) the one open post-wave
  finding, its fix in flight). The
  W26/W27 live results are prompt-level ledger effects, not typed-arm
  evidence.
- R2-E1/E-INC numbers are pipeline mechanics under a deliberately lossy
  deterministic fixture: worst-case bounds on what the pipeline alone protects, not
  predictions about a specific model. The live result (19/19 anchors at every cycle)
  is the counterweight.
- The 89/95 vs 95/95 base-scenario difference is 6 records over 95 with the
  campaign's repetition plan; it is cited as a mechanism finding (the inversion is
  verified in the rendered ledger), not as a distributional effect size.
- The calibration corpus is synthetic (SYNTHETIC-CALIBRATION); its AUC/Brier/ECE
  describe the heuristic against generated labels only.
- Not a measurement of the shipped binary for the E2/E3/E3ADV family: those are a
  Python mirror of the Rust path (fixture-validated; deviations listed in section
  5.3). The W26 and W27 practice runs DID use the built harness binary on the
  production path, at practice scale.
- Not a claim that all audited defects are fixed: D7 (deferred), D33 (fix in
  flight) and D10's "never retry" sub-item remain open, and D12/D17/D20 carry
  disclosed partial-fix residues (section 8.2 has the closed count). The typed
  path's post-wave findings D31 (W52) and D32 (W53) are fixed; D30 landed with
  the cycle-5 budget caveat accepted-and-pinned (W46). W9-F1 (keyword-less
  constraint capture), W9-F3 and W9-F4 are now fixed in v1 itself by the W16
  clause-level refinement (W51, `rot2_supersession` 4/4); typed v2 removes
  them by classifying on role - except custom `agent_message` entries, which
  the committed classifier still does not handle (section 4.2; decisions
  stated in user text are handled since the D30 fix, W46 `10e2b36e6`).
  D18/D19/D20 landed (W30), D21-D29 landed (W32-W41, section 4.4), D30
  landed (W46), D31-D32 landed (W52/W53).
- Not reproducible from the drivers alone: item seeds derive from salted blake2b
  digests recorded per row (iteration-1 M5, fixed for r2; `scenario.py`).

## 8. Campaign close-out

This is the campaign's closing record; the counts below were verified against
BUG-LEDGER.md and the tree at the tip of this pass (`90fcd17db`).

### 8.1 The worker roster (what the eight waves delivered)

The campaign ran as eight worker waves of eight workers each (W1-W64) plus the
integrator. Three recovery/restart workers re-landed work lost to dead workers
and a disk incident (W30, W31, W32), and W51 recovered the W16 branch through a
12-conflict integration. Wave by wave:

| wave | delivered |
| --- | --- |
| 1 (W1-W8) | turn-flow and summary-path correctness (D1/D2, D5/D6/D9/D14), the request-level token estimator (D16/D17), the typed-state v2 design, the reference research (Knowledge Triage, CompactBench, ContextBench), the CompactBench adapter, the live R2-E2 run and the health calibration corpus |
| 2 (W9-W16) | the incremental production-path experiments (E-INC), the ContextBench offline pipeline, the W11 v1 id/supersession repair, the mock practice suites, the D10-D13 fix/verification wave, the first scorecards, the W15 typed integration and the W16 v1 supersession refinement (landed later by W51) |
| 3 (W17-W24) | the live coding-task suite R2-E3, the D18/D19/D20 wave (recovered by W30), the first PR artifacts, the D11 digest prune, the remaining scorecards, this report's first edition, the adversarial R2-E3ADV run and the offline R2-E2-RM re-measure |
| 4 (W25-W32) | the full-tip gate record, the W26 live CompactBench and W27 live ContextBench practice runs, the D21 render-cap fix, the R2-E5 typed-arm run and the recovery/restart workers |
| 5 (W33-W40) | the PR and report refreshes at the typed tip, the W35 red team, the D25, D23/D24, D22/D26/D27 and D28 fixes of its wave, and the F3 harness-probe sweep |
| 6 (W41-W48) | the D29 provenance-pairing fix, the W42 pre-PR audit, the T05.4 fixture re-pin, the D17 claim rescoping, the audit should-fix wave, the D30 fix, the R2-E5-RM re-measure and the W48 fix-code red team (D31/D32) |
| 7 (W49-W56) | the wave-2 report refresh with the verification battery, the cross-report consistency sweep, the W16 integration, the D31 and D32 fixes, the D33 filing, and the gate-matrix and PR-assembly preparations |
| 8 (W57-W64) | the post-D25 13/19 ripple through every report, the typed-mode user documentation, the W59 audit resolution, the W60 ledger verification, this closing refresh and the final gates, reviewer and PR passes |

The closing tail (the PR assembly, the landed-tip gate battery, and the
reviewer, hygiene and scorecard passes) runs alongside or after this document;
they can only change how the record is presented, not what was found.

### 8.2 The defect ledger, closed

Verified against BUG-LEDGER.md at this tip: 32 numbered defects recorded
(D1-D34; iteration-1's D3 and D15 carried no r2 scope).

- 25 FIXED with regression guards: D1, D5, D6, D8, D9, D10, D11, D13, D14,
  D16, D17 (estimator), D18, D19, D21, D22, D23, D24, D25, D26, D27, D28,
  D29, D30, D31, D32.
- D2 VERIFIED-GUARDED (already intact on this base; pinned by tests).
- D4 CONFIRMED on the production path - the rot mechanism itself, the finding
  the typed design exists to address, not a code fix.
- Partial-fix residues, disclosed in their rows: D12 (soft fence), D17
  (production call-site wiring, follow-up), D20 (residue outside
  agent_session.rs), D10's "never retry" sub-item.
- D7 VERIFIED-OPEN, fix DEFERRED (the incompatible-checkpoint replay is a
  heavy fallback, not a lossy one; the fix needs dedicated restore coverage).
- D33 FIXED (W54, `f0b125a84`) - restatement-absorbed record ids
  strand references (filed from the E5-RM offline TASK-E chain; the live cells
  did not trigger it); the fix is in flight on `rot2/w54-d33` and is not in
  this tree.
- The non-D families: N1-N3 (v1 design constraints, closed in typed v2 by
  role-based classification), W9-F2/F5 fixed by W11, W9-F1/F3/F4 fixed by the
  W16 refinement, HF1/HF4/HF5 fixed, HF2/HF3/HF6 documented.

### 8.3 Residual risks at close

1. D7 (deferred): an incompatible or null provider checkpoint replays
   pre-compaction messages after restore - a heavy fallback, not a lossy one.
2. D33 (open, fix in flight): absorbed-record reference stranding; observed
   offline only (the live E5-RM cells cite the absorbed record outside the
   verifier's reference grammar).
3. D17 production wiring (follow-up): the request-aware estimator is verified
   and adapter-exercised, but the six production estimator call sites still
   use the message-only path (usage anchors win where present either way).
4. No official scorecard is authorized: every CompactBench and ContextBench
   number in this campaign is CUSTOM-OPTIMUS practice-scale (custom adapters
   and settings, scorer 2.0.0, single-draw live runs, synthetic retention
   trajectories); no leaderboard-comparable number exists, none is claimed,
   and RepoQA, SWE-bench and Terminal-Bench were not attempted.
5. The ContextBench holdout is untouched: 192 instances over disjoint repos,
   no repository checkouts exist under the offline waiver, and patch_success
   is UNAVAILABLE by construction.
6. The disclosed partial-fix residues of 8.2 (D10's sub-item, D12's soft
   fence, D20's residue) and the single-model scope of every live number
   (GLM 5.3, one endpoint, temperature 0 - section 7).
7. Operational: the five live raw-call logs ride git-lfs; the first push of
   this branch must carry `git lfs push` (REVIEW-AUDIT.md S5/R8).

The campaign's verdicts stand as recorded: repeated compaction loses
unprotected state on the production path (H-r2-1), the v1 ledger is not a
safe fix for it (H-r2-2), typed state v2 fixes the mechanism and ships as a
measured, red-teamed, user-documented opt-in (H-r2-3, partially supported -
the tip fixture count sits below control B and every live number is
single-model practice-scale), and the health heuristic must not gate anything
(H-r2-4).

## 9. Report index

Every file in `reports/context-rot-r2/` at this writing (20 files):

- `BASELINE.md` - report writer W14, status-passed by the later sweeps:
  the commit under test, the measurement frame, the category labels, the
  R2-E1 numbers, the inherited iteration-1 context and the TypeCompact
  reproduction baseline.
- `BUG-LEDGER.md` - the defect ledger and the campaign's status source of
  truth, opened with the first fix wave's verified statuses and appended
  to by every fix wave since: D1-D34 rows (D33 fixed by W54, D34
  verified-open from the W63 battery), the new findings N1-N3, the W9
  findings, the harness findings HF1-HF6, section 4's W35 red-team wave
  (D22-D30, all fixed) and section 5's W48 post-wave audit (D31/D32).
- `CITATION-INTEGRITY.md` - worker W70: every test name cited by the r2
  reports checked against the tip's test inventory - 16 report files, 114
  distinct citations, 113 verified; the one miss
  (`absorption_then_citation_resolves`) was the then-unlanded W54 D33 pin,
  on the tree since `6d85cc48f`.
- `CODING-TASK-RESULTS.md` - report writer W14 (refreshed by W34,
  reconciled by the W50 sweep): what coding-task evaluation exists (the
  ContextBench pipeline, the CompactBench custom runs) and the exact
  blockers for everything else.
- `COMPACTBENCH-RESULTS.md` - report writer W21 (scope updated by W34):
  the CompactBench family consolidated - the W6 adapter validation runs,
  the W12 mock practice suites and the W26 live practice run.
- `COMPACTION-CORRECTNESS.md` - report writer W14 (refreshed by W34,
  reconciled by W50): the D-defect narrative - what is fixed on the branch,
b2c0a435a (docs(context-rot): refresh the report index to the full 20-file set and strip the W62 paste debris (W78))
  what sat on worker branches at its writing, what remains open, and the
  fix-to-guard map. A W76 pass renames this file to
  `COMPACTION-CORRECTNESS.md` (the HYGIENE-AUDIT L3 typo repair); this
  index uses the name on disk at its writing.
- `CONTEXT-HEALTH-CALIBRATION.md` - report writer W21 (the instrument is
  W8's): the calibration corpus, metric definitions and verdict for the
  context-health score.
- `CONTEXTBENCH-RESULTS.md` - report writer W21 (the pipeline is W10's;
  sections 3 and 5 updated by W34): the W10 offline pipeline, its blocked
  records, and the W27 live practice run.
- `EXPERIMENT-LEDGER.md` - every arm and every NOT-EXECUTED item, opened
  with the R2-E1 harness landing (`3eeed6f3f`) and kept current by the
  wave refreshes: the post-W22 sections (R2-E3, R2-E3ADV, R2-E2-RM, the
  W26 live CompactBench run, the W27 live ContextBench run, R2-E5 and
  R2-E5-RM) with Status lines citing their commits and artifact paths,
  the DEFECT-WAVE-2 tables, and the W60 verification pass (every sha
  resolves, every artifact path exists).
- `FINAL-REPORT.md` - this file, the campaign entry point: first edition
  in wave 3, the W62 closing pass, the W64-blocker resolution and this
  W78 index pass.
- `HYGIENE-AUDIT.md` - worker W65: the AGENTS.md-compliance and
  commit-hygiene audit at `90fcd17db` (clippy, rustfmt,
  `git diff --check`, marker and secret scans; no HIGH findings, two
  MEDIUM, four LOW, five informational).
- `KNOWLEDGE-TRIAGE-REPRODUCTION.md` - report writer W21 (the reproduced
  experiment is W5's): the TypeCompact reproduction - setup, substitutions,
  numbers, and the kind-mapping to typed_state v2.
- `MULTICYCLE-DRIFT.md` - report writer W14: the offline multicycle runs
  consolidated - decay regimes, growth model, late material, late
  supersession, token cost.
- `PR-BODY.md` - the PR body, drafted in wave 3 (`483e6de35`) and
  assembled by W61 at `90fcd17db`: the summary, the per-defect fix map,
  the testing section and the reviewer checklist.
- `PRE-PUSH-VERIFICATION.md` - worker W68: the pre-push technical
  verification at `cab09bdd2` - branch topology, LFS, worktree hygiene,
  changelog-fragment format, PR-body size and the gh-absent web fallback;
  every item PASS.
- `PUSH-CHECKLIST.md` - worker W61: the integrator-only push checklist -
  preconditions, the push and PR-open commands, and the web fallback.
- `REGRESSION-GATES.md` - worker W25 (the restart pass): the test-suite
  state - the W25 full pass at `29f1f0c92` (per-suite SHAs back to
  `f7bc6306c`), the W49 wave-2 battery at `71e770c5e` (section 10), the
  W63 landed-tip battery at `90fcd17db` (section 11, RED on the D34
  smoke) and the W72 python suites at the landed tip (section 11.10,
  green).
- `RETRIEVAL-SCORECARD.md` - report writer W21: the consolidated retrieval
  scorecard, iteration 1 to r2 (W10's offline draft kept in place under
  `research/`).
- `REVIEW-AUDIT.md` - worker W42: the pre-PR hostile-reviewer pass
  (blocker B1, should-fix S1-S7, nits), the W45 appendix (the D22-D29
  red-team wave record and the integrator's commit-subject options) and
  the W59 final resolution (the B1 and S1-S7 dispositions, the PR-BODY
  cross-check, the residual-risk list R1-R8).
- `REVIEWER-PASS.md` - worker W64: the whole-diff hostile review at
  `90fcd17db` - the B1-B4 blockers (paste debris, stale D31-D33 statuses,
  fragment duplication, the W16 misstatement), should-fix S1-S8, the
  prior-audit disposition check and the verification spot-runs at the
  final tip.

Landed on this branch since the W62 closing pass: the D33 fix (W54; on
`rot2/w54-d33` as `f0b125a84` + `1de36e893`, integrated as `cea3a4496` +
`6d85cc48f` with the ledger row `510fd168d`), the PR assembly and push
checklist (W61), the pre-push technical verification (W68), the
whole-diff reviewer pass (W64), the hygiene audit (W65), the
citation-integrity pass (W70) and the W63/W72 gate records - the closing
tail that section 8.1 recorded as running alongside this document has all
landed. In flight at this writing: the W76 filename repair
(`COMPACTION-CORRECTNESS.md` to `COMPACTION-CORRECTNESS.md`), the W69
production-path trace (`PRODUCTION-PATH-TRACE.md`, not yet on this branch)
and the tail passes after them; this index uses the names on disk at its
writing. If a file named here is missing at read time, the experiment
ledger and the cited artefacts hold its content.
