# FINAL REPORT — context rot and compaction fidelity in Optimus (iteration 2, 2026-10-19/20)

Entry point for the r2 campaign. Iteration 1
(`reports/context-rot/FINAL-REPORT.md`) measured compaction policies through Python
mirrors and found one decisive improvement (the evidence-recovery excerpt) and one
negative result (the v1 task-state ledger: no recall gain, a token cost). This
campaign closed the mirror gap: every multicycle number below marked PRODUCTION-PATH
was produced by the real `prepare_compaction` -> `compact` -> `append_compaction` ->
`SessionManager` restore pipeline, offline, through the committed adapter, and a
live family (R2-E2, R2-E3, R2-E3ADV, the W26 CompactBench practice run, the W27
ContextBench practice run) put the same prompts and the same pipeline against GLM
5.3. The headline is split again, and sharper than iteration 1's: repeated
compaction is a confirmed information-loss mechanism on the production path, the
v1 ledger is not a safe fix for it (under a live model it inverted a user
correction; with the W11 supersession fix it renders clean offline but caps
authoritative corrections out of the render - defect D21), at the W34 refresh thirteen
audited defects were addressed - twelve with behaviour changes and regression
guards, D2 verified intact and pinned by tests - and since then the
D18/D19/D20 recovery (W30) and the D21-D29 fix wave landed, leaving D7, D10's
"never retry" sub-item, D30 and the cycle-5 budget caveat as the open items;
and the designed replacement (typed state
v2) is wired end to end as an opt-in mode, measured live (R2-E5), then
red-teamed: the W35 adversarial suite pinned eight guarantees and filed seven
defects (D22-D28), and the D21-D29 fix wave that followed leaves only D30 and
the cycle-5 budget caveat open (W46 in flight).

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
recovery (W30), the D21 render-cap fix (W32) and the D22-D29 red-team fixes.
In flight at this writing and NOT in this tree: the D30 and cycle-5
budget-caveat recovery (W46; `rot2/w46-d30` has no commits over this head)
and the W16 supersession work (`rot2/w16-supersession`, no commits over this
head).
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)

## 1. Hypotheses

| id | hypothesis | test | verdict |
| --- | --- | --- | --- |
| H-r2-1 | the default summary-of-summary path loses protected information over repeated compactions on the production path | R2-E1: 19 anchors, 5 cycles, deterministic lossy summarizer; E-INC-1/2a: 8 cycles, 2 seeds | supported as a worst-case bound (0/19 after 5 cycles, 0/19 already after 2); NOT confirmed live - R2-E2's real summarizer retained 19/19 and 37/37 anchors at every cycle |
| H-r2-2 | the v1 ledger does not safely protect state | R2-E2 base+hard (live GLM 5.3); E-INC-2b/2c (W9-F2 id collisions, W9-F4 mutual retirement); the W8 calibration (the health heuristic, AUC 0.604, is no safety net either); R2-E3ADV (adversarial corrections, live); R2-E2-RM (post-W11 renders, offline) | strongly supported: the ledger can invert a user correction (pre-W11, live); retention is seed- and placement-dependent; nothing else predicts the loss. Post-W11 it either renders clean (E2-RM base) or caps the corrections out of the rendered ACTIVE list (E2-RM hard, D21); when supersession cannot pair at all it does nothing and the model argues the stale item down (E3ADV, 30/30 both arms at +1.5k-1.9k request chars/cycle) |
| H-r2-3 | provenance-aware typed state fixes the mechanism | typed_state v2 module (design + 74 unit tests at this tip, `c948905a3` + the wave); the W15 integration wiring (`338b920c3`, `40bead288`, `4be9623b0`); W11's content-derived id and correction-safe supersession fixes in v1 (`1754a3e1b`), re-measured offline on the live corpus by R2-E2-RM (`2fbb14f33`); R2-E5 (the typed-arm measurement, `EXPERIMENT-LEDGER.md`); the W35 red team and the D21-D29 fix wave (section 4.4) | partially supported: wired end to end, verifier-gated, and R2-E5 part 1 beats control B on the fixture (15/19 vs 14/19) with live parts 2-3 correct where they ran; the first live run also falsified two pre-registrations (TASK-E halt, the D24 id-churn mechanism; no SUPERSEDED render in E3ADV C1, the D29 token-overlap pairing) - both now fixed, with D29 live-confirmed on the C1 cell (`621dc462f`); the red team then found D22-D28, all fixed, leaving D30 and the cycle-5 budget caveat open (W46). The E3ADV structural goal is achieved post-D29; no full post-fix R2-E5 re-run exists |
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)
| H-r2-4 | pressure/cycle health does not predict semantic loss | W8 calibration corpus: 66 labelled cases scored by the production scorer | supported on synthetic labels: ROC-AUC 0.604 against `lost_constraint` |

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
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)

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
`c948905a3` at 3 033 lines and 60 unit tests; 74 unit tests at this tip after
the D21-D29 wave, section 4.4): 14 `TypedInformationKind` values with role-gated
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
classifier gap is open at this tip: a decision stated in USER text falls through
to LowPriorityEpisode (Step scope) and is retired at the first merge while the
same sentence from the assistant classifies as VerifiedDecision (Task scope) -
defect D30, filed `7bec70f09`, assigned to W46. The wave's fixes then went
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
| D29 token-overlap pairing | `4315f4154` (W41; worker `e616a3126`) + `e71c21563` | `rot2_typed_adversarial` (3 pins) + live C1 cell `621dc462f` |

The mechanisms are recorded in `EXPERIMENT-LEDGER.md` DEFECT-WAVE-2. The W49
verification battery (this branch, at the integration tip `71e770c5e`,
every number this worker's own run): `rot2_typed_adversarial` 23 passed,
0 failed, 0 ignored (the six formerly-`#[ignore]`d defect tests are green);
`rot2_typed_integration` 9/0; `rot2_native_typed_chain` 2/0;
`rot2_multicycle` 5 passed + 1 ignored (the research adapter);
`rot2_render_cap` 7/0; `rot2_ledger_fixes` 7/0; `--lib typed_state` 74/0;
`--lib core::compaction` 160/0; plus the `compaction_summary_path` 8/8 and
`compaction_parity_suite` 14/14 spot checks (REGRESSION-GATES.md section 10).
Open after the wave: D30 (a decision stated in user text classifies as
LowPriorityEpisode, Step scope, and is retired at the first merge; filed
`7bec70f09`) and the W41 cycle-5 budget-overflow caveat (`621dc462f`); both
assigned to W46 (`rot2/w46-d30`, in flight with no commits over this head).
A full post-fix R2-E5 live re-run was NOT executed; the D29 fix is
live-confirmed on the E3ADV C1 cell only.

## 5. What a reviewer should check

1. The typed mode is wired (`338b920c3`, `40bead288`, `4be9623b0`; section
   4.2), measured (R2-E5: fixture 15/19 typed vs 14/19 v1 vs 0/19 default;
   live parts 2-3) and red-teamed (W35 `f7bc6306c`: eight guarantees pinned,
   seven defects D22-D28 filed, six of them red-first). Every defect the
   measurements and the red team surfaced in the typed path is fixed at this
   tip (D21-D29, section 4.4), and the W49 battery re-runs green
   (REGRESSION-GATES.md section 10): adversarial 23/0/0, integration 9/0,
   native 2/0, multicycle 5+1, render_cap 7/0, ledger_fixes 7/0, lib
   typed_state 74/0, lib core::compaction 160/0. Still open: D30 (user-text
   decisions classify as LowPriorityEpisode and retire at the first merge,
   `7bec70f09`) and the W41 cycle-5 budget-overflow caveat (`621dc462f`);
   W46 (`rot2/w46-d30`) is in flight with no commits over this head. A full
   post-fix R2-E5 live re-run was NOT executed — the D29 fix is
   live-confirmed on the E3ADV C1 cell only (`621dc462f`).
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
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)
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
   provider checkpoint replays pre-compaction messages; unfixed at this
   tip) and D10's "never retry" sub-item. The D18/D19/D20 recovery landed
   (W30: `8c558dd7b`, `4769f42f4`, `e44faff08`) and the D21 render-cap fix
   landed (`a9035ce8a`, W32); the red-team wave D22-D28 plus the D29
   token-overlap pairing are fixed (section 4.4). The typed mode's remaining
   open items are D30 (user-text decisions classify as LowPriorityEpisode
   and retire at the first merge, `7bec70f09`) and the W41 cycle-5
   budget-overflow caveat (`621dc462f`); W46 (`rot2/w46-d30`) is in flight
   with no commits over this head.
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)

## 6. Recommendation for defaults

| mechanism | default | decision | reason |
| --- | --- | --- | --- |
| `compaction.taskState` v1 ledger | `off` (unchanged) | KEEP OFF | iteration-1: no recall gain, +913..1 751 tokens/cycle; R2-E2: the ledger inverted a user correction under a live model (89/95 vs default 95/95) and its summarizer calls burn 8k-32k reasoning tokens with latency up to 137.8 s; E3ADV: when its supersession cannot pair it does nothing and the model argues the stale item down (+1.5k-1.9k request chars/cycle); the W11 fix renders clean offline on the base corpus (E2-RM) but caps corrections out of the hard render (D21) and is unproven live (the D21 cap was later fixed by `a9035ce8a`, W32; the KEEP OFF verdict rests on the inversion, the cost and the E3ADV dead-weight result, all unchanged by that fix) |
| typed state v2 | `off`; opt-in via `compaction.taskState = "typed"` | OPT-IN; measured and red-teamed, not a default | wired end to end (`338b920c3`, `40bead288`, `4be9623b0`; section 4.2) and measured: R2-E5 part 1 beats control B on the fixture (typed 15/19 vs v1 14/19 vs default 0/19; verifier 5/5 cycles, stable 777/800 hot-token budget, +886 mean request chars) and parts 2-3 are live-correct wherever they ran, but TASK-E halted at cycle 2 pre-fix (the D24 id-churn mechanism, since fixed) and the E3ADV C1 cell showed no SUPERSEDED render pre-D29 (since fixed and live-confirmed `621dc462f`). The W35 red team then found seven more defects (D22-D28), all fixed (section 4.4), with the W49 battery green at `71e770c5e`. Still open: D30 and the cycle-5 budget caveat (W46 in flight); a full post-fix R2-E5 live re-run was NOT executed. Other evidence remains prompt-level or v1-level (W26, W27, E2-RM). No default change before D30 lands and the full re-run beats control B at comparable or lower token cost |
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)
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
  end. H-r2-3 is supported by the wired integration, the R2-E5 fixture win
  (15/19 vs control B's 14/19) and live-correct cells, but R2-E5 part 2 also
  produced the campaign's first live falsification of a typed-mode
  pre-registration (TASK-E halted at cycle 2, the D24 mechanism, since fixed)
  and no full post-fix live re-run exists. The red team found eight more
  defects in the typed path after the wiring (D22-D28), all fixed
  (section 4.4), with D30 and the cycle-5 budget caveat still open. The
  W26/W27 live results are prompt-level ledger effects, not typed-arm
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)
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
- Not a claim that all audited defects are fixed: D7 and D10's "never retry"
  sub-item remain open; the typed path's D30 and cycle-5 budget caveat are open
  (W46 in flight); W9-F1 (keyword-less constraint capture) is a v1 design gap
  that typed v2 removes by classifying on role - except custom `agent_message`
  entries and (D30) decisions stated in user text, which the committed
  classifier still does not handle (section 4.2). D18/D19/D20 landed (W30),
  D21-D29 landed (W32-W41, section 4.4).
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)
- Not reproducible from the drivers alone: item seeds derive from salted blake2b
  digests recorded per row (iteration-1 M5, fixed for r2; `scenario.py`).

## 8. Report index

- `BASELINE.md` - the commit under test, the measurement frame, the category
  labels, the R2-E1 numbers, the inherited iteration-1 context, the TypeCompact
  reproduction baseline (W14).
- `BUG-LEDGER.md` - D1-D20 status on this base, the new findings N1-N3, the W9
  findings, the harness findings HF1-HF6, and section 4's W35 red-team wave
  (D22-D29, all fixed; D30 open).
- `REVIEW-AUDIT.md` - the W42 pre-PR hostile-reviewer pass (blocker B1, should-fix
  S1-S7, nits) plus the W45 appendix: the D22-D29 red-team wave record and the
  integrator's commit-subject options.
- `CODING-TASK-RESULTS.md` - what coding-task evaluation exists (ContextBench
  pipeline, CompactBench custom runs) and the exact blockers for everything else.
- `COMPATION-CORRECTNESS.md` - the D-defect narrative: what is fixed on the branch,
  what sat on worker branches at its writing, what remains open, and the
  fix-to-guard map.
- `EXPERIMENT-LEDGER.md` - every arm and every NOT-EXECUTED item, now including
  the post-W22 sections (R2-E3, R2-E3ADV, R2-E2-RM, the W26 live CompactBench
  run and the W27 live ContextBench run) with Status lines citing their commits
  and artifact paths.
- `FINAL-REPORT.md` - this file, the campaign entry point.
- `MULTICYCLE-DRIFT.md` - the offline multicycle runs consolidated: decay regimes,
  growth model, late material, late supersession, token cost (W14).
- `REGRESSION-GATES.md` - the test-suite state: the W25 full pass at `29f1f0c92`
  (per-suite SHAs back to `f7bc6306c`) plus the W49 wave-2 battery at `71e770c5e`
  (section 10).
- In flight at this writing, not in this tree: the D30 and cycle-5
  budget-caveat recovery (W46, `rot2/w46-d30` has no commits over this head)
  and the W16 supersession work. The W21 scorecards were
  integrated at `7263a58c5` and their factual staleness is refreshed by this
  worker; the W19 PR draft (`PR-BODY.md`, integrated at `483e6de35`) predates the
  post-W22 results and needs its own refresh. If a file named here is missing at
  read time, the experiment ledger and the cited artefacts hold its content.
f638de7b9 (docs(context-rot): refresh PR-BODY and FINAL-REPORT to the landed fixes)
