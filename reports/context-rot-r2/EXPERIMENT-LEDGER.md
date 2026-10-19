# EXPERIMENT LEDGER — context rot iteration 2

Every arm, every run, every NOT-EXECUTED item. Production-path measurements
run the real Rust pipeline (`prepare_compaction` -> `compact` ->
`append_compaction` -> `SessionManager` restore) via
`crates/pi-coding-agent/tests/rot2_multicycle.rs`; Python only orchestrates.

## R2-E1 — multicycle retention, default vs taskState (deterministic lossy summarizer)

- Status: EXECUTED (r2 research harness, scenario and experiment committed in
  `3eeed6f3f`; artifacts `research/context_rot_r2/runs/r2-e1-multicycle.{scenario,result,score}.json`).
- date: 2026-10-19
- scenario: `r2-e1-multicycle` (19 probes: 8 constraints, 4 decisions, 4 evidence,
  2 corrections/supersession, 1 subagent artifact), 5 compaction cycles, filler turns
  between cycles. Seeds: blake2b digest (M5 fixed). Scenario JSON:
  `research/context_rot_r2/runs/r2-e1-multicycle.scenario.json`.
- summarizer: deterministic lossy fixture (copies protected blocks verbatim, fixed
  head/tail loss elsewhere, previous-summary decay) — identical across arms, so
  differences measure PIPELINE protection, not model skill.
- model calls: none (offline fixture). This is a pipeline-mechanics result, NOT a
  model-quality result.

| arm | retention | constraints | decisions | evidence | supersession | subagent | mean req chars |
| --- | --- | --- | --- | --- | --- | --- | --- |
| default | 0/19 (0.0%) | 0/8 | 0/4 | 0/4 | 0/2 | 0/1 | 14 765 |
| taskState | 14/19 (73.7%) | 8/8 | 4/4 | 0/4 | 2/2 | 0/1 | 18 429 |

- finding 1 (D4 confirmed on the production path): with an even mildly lossy
  summarizer, FIVE repeated compactions lose every unprotected anchor in the
  default arm. Summary-of-summary is a real information-loss mechanism, not a
  theoretical one.
- finding 2: the v1 ledger fully protects constraints, decisions and explicit
  corrections (14/14) at +3 664 mean request chars (~916 tokens/cycle, matching
  iteration-1's +913..1 751).
- finding 3 (v1 gap A): tool-result evidence is lost (0/4). Root cause:
  `task_state.rs` `EVIDENCE_RE` matches only `test result:|error[E\d+]|N files
  changed|panicked at|FAILED`; `cargo test: N passed; 0 failed`, `build succeeded`
  and benchmark lines do not match. Evidence extraction is content-regex based
  instead of role-based.
- finding 4 (v1 gap B): subagent artifacts are lost (0/1). Root cause:
  `entry_text` handles only `Message` entries; `CustomMessage` entries
  (`agent_message` from children) are invisible to the ledger.
- consequence for typed_state v2 (W4): classify by role/provenance first
  (tool results => VERIFIED_TOOL_RESULT/SOURCE_EVIDENCE by role, not regex;
  custom agent messages => SUBAGENT_STATE), which closes both gaps.
- decision: KEEP the taskState arm as the CONTROL B baseline for r2; the typed
  state v2 arm must beat 14/19 with evidence+subagent retained, at comparable
  or lower token cost.

## R2-E0 — adapter smoke (d4-drift, 3 probes, 5 cycles)

- Status: EXECUTED (early adapter smoke inside the r2 research harness,
  `3eeed6f3f`; superseded by R2-E1 and kept for the record — the committed
  run artifacts are R2-E1's).
- same adapter, smaller scenario; default lost all anchors by cycle 2;
  taskState retained constraint+decision, lost evidence. Superseded by R2-E1
  (same mechanism, larger probe set).

## NOT EXECUTED (updated at the W34 refresh)

Executed since this list was first written: the live-model arms (R2-E2 base+hard
below, R2-E3, R2-E3ADV), the offline re-measure R2-E2-RM, the CompactBench
practice suites (W12 mock, W26 live) and the ContextBench live practice run (W27)
— sections below. Still not executed:

- the live TYPED arm (R2-E5, the decisive H-r2-3 measurement): `--arm typed` in
  the E3 orchestrator is a stub (exit 2, never a silent substitution),
  `arm_settings` in `tests/rot2_multicycle.rs` maps only `default`/`taskState`,
  and worker W29 has no commits over the integration head at this writing.
- any official / leaderboard-comparable number: every CompactBench run is
  CUSTOM-OPTIMUS (public practice suites, custom adapter, scorer 2.0.0; the W26
  live run is 8 of 20 starter cases per arm, one live draw per case); every
  ContextBench number is the offline pipeline or the W27 practice run (synthetic
  retention trajectories, patch_success UNAVAILABLE).
- ContextBench repository checkouts, free-form agent trajectories and the holdout
  run (the W10 blockers, unchanged).
- re-running the iteration-1 retrieval surfaces on a second model: the r2 live
  family ran on GLM 5.3, but the iteration-1 surfaces themselves were not re-run.

## E-INC — incremental/selective compaction experiments (W9, offline, production path)

- Status: EXECUTED (variant driver and offline tests `0229dee3a`, adapter runs and
  per-experiment analysis `91ac4b2cb`; artifacts
  `research/context_rot_r2/incremental/results/e-inc{1,2a,2b,2c,3}.json` and
  `incremental/runs/*.json`).
- 8 cycles x 2 seeds, both arms; 16 adapter runs. Growth is non-compounding
  (fixed point at cycle 1: default steady 309 tok, taskState 712-716 tok).
- default arm retention decays 1/19 -> 0/19 by the second compaction;
  taskState flat 14/19, 13/19 across all 8 cycles (R2-E1's finding extends to 8 cycles).
- determinism and path independence confirmed (reruns identical; k-cycle state
  equals cycle k-1 of the longer run).
- late-planted constraints survive WORSE (8/8 -> 4/8): W9-F1 keyword-less
  constraints are invisible to the v1 ledger when isolated; W9-F2 ledger ids
  renumber per merge and the blanket retired-set retain silently deletes newly
  captured items that reuse a retired id.
- late supersession (cycle 5): new decision kept 2/2, but the old decision is
  NOT retired in either placement: W9-F4 the two corrections retire EACH OTHER
  instead of the old decision (mutual-retirement misfire).
- policy variation (summary_update_policy) is NOT exposed to the scenario arms;
  the consolidate policy costs +348 tok per summarizer call after cycle 1.
- taskState pays the ledger twice per steady cycle (render + duplicated carry
  = +995 tok/cycle).

## New verified defects (bug-ledger candidates)

| id | finding | fix direction |
| --- | --- | --- |
| W9-F2 | build_ledger renumbers ids per merge; blanket retain(!retired.contains(id)) deletes newly captured items that reuse a retired id | stable content-hash ids (typed_state v2 already does this) |
| W9-F4 | supersession pass retires the two corrections' targets mutually instead of retiring the old decision | role/provenance-based retirement scoring (typed_state v2) |
| W9-F3 | baseline anchor retention overstates current-state correctness: surviving anchors sit in SUPERSEDED lines | verifier must distinguish current vs superseded presentation |
| W9-F5 | late placement loses one constraint anchor to the same id-renumbering collision as W9-F2 | same as W9-F2 |

## R2-E2 — LIVE-MODEL multicycle (real GLM 5.3 as summarizer and probe answerer, W7)

- Status: EXECUTED (live driver and Python mirror committed in `fb089ee6e`;
  artifacts `research/context_rot_r2/live/` — `results.json`, `raw-calls.jsonl`,
  `r2-e2-{multicycle,hard}.*`, README; the corpus was later replayed offline
  against the post-W11 ledger by R2-E2-RM below).
- 581 wire calls, 526 distinct, 0 probe extraction failures (delimiter protocol held).
- Python fixture mirror reproduced the stored R2-E1 result byte-exact (parity gate).
- base scenario (19 probes x 5 cycles): default 95/95, taskState 89/95 (17/19 at cycles 3-5).
- hard scenario (37 probes, 20 constraints, filler 200): default 111/111, taskState 111/111.
- anchor retention 19/19, 37/37 at every cycle, all arms: the REAL summarizer does not
  lose anchors the way the deterministic lossy fixture does. R2-E1's 0/19 default-arm
  result is therefore a PIPELINE-WORST-CASE bound, not the expected live behavior.

### Finding 1 (headline): the v1 ledger can INVERT a user correction in the model summary
- user correction: "use architecture B ... the earlier A decision is superseded"
- from cycle 3 the taskState summary asserts "use architecture A ... per the protected
  task state; the B correction ... superseded (history)" and the probe answers
  architecture A (3-rep majority). The DEFAULT arm handles the same correction correctly
  at every cycle.
- root cause (verified in the rendered ledger): SUPERSEDE_RE is token/positional; it
  matches ordinary constraint lines, producing mutual supersession pairs (C12<->C10);
  the render then lists the CORRECTIONS under SUPERSEDED while the pre-correction
  decisions stay in DECISIONS (current). GLM follows the protected state faithfully,
  so the ledger asserts the opposite of what the user said.
- finding 2 (hard scenario): the same rule misfires differently (15/20 constraints
  churned into spurious pairs); the model resolves the ambiguity from raw transcript
  text and answers correctly - correctness is luck of the regex pairing, not a property.

### Finding 3 (operational): GLM 5.3 reasoning tokens bill against max_tokens
- 8k-32k reasoning tokens per taskState compaction; the production 8192 budget +
  single 2x retry fails validate_summary on 3 of 5 base cycles; a bounded ladder to
  65536 completes all cycles. taskState summarizer latency up to 137.8 s; default ~2k.

### Consequences
- the v1 ledger is WORSE THAN NOTHING on corrections (it can invert user intent while
  presenting itself as authoritative). This is the strongest argument yet for typed_state
  v2's provenance-based supersession (W4) and for keeping taskState default OFF.
- the W11 fixes (stable ids, retirement by identity, corrections must not supersede each
  other) target exactly this mechanism; W7's raw-calls.jsonl is the regression corpus,
  and R2-E2-RM (below) replayed it offline: the inversion is removed on the base
  corpus, the render cap (D21) is the residual.

HEAD
## R2-E3 — coding-task scenario suite (live GLM 5.3, W17)

- Status: EXECUTED (worker branch `rot2/w17-e3`, integrated as `d8e318f81` +
  `346f6c4f3` + `c5107fc31`; artifacts `research/context_rot_r2/live/e3/` —
  `results.json`, `raw-calls.jsonl`, `README.md`, `r2-e3-task-{a,c,e,h,f}.scenario.json`).
- Category: LIVE-MODEL (glm-5.3, temperature 0; the W7 Python mirror of the
  production compaction path per cycle, `prepare_compaction` -> `compact` ->
  `append_compaction` -> restore, with the v1 ledger rendered as protected state on
  the taskState arm; probes are ACTION questions with negation-aware matchers,
  3 reps per point, majority >= 2 of 3).
- Question: does repeated compaction lose ACTION-level facts (next file to edit
  under a constraint, corrected approach, boundary-adjacent exact error,
  acceptable-set exclusion, post-restart approach) when the summarizer is a real
  model?
- Result: 50/50 probe points correct on BOTH arms (5 tasks x 2 arms x 5 cycles);
  strict == primary everywhere; 0 extraction failures; no split majorities; anchor
  retention 1/1 at every cycle, both arms, all tasks. TASK-F's simulated restart
  (serialize -> fresh reload) left the restored provider context byte-identical on
  both arms (`contextRoundTripIdentical: true`).
- Findings: no measurable action-level rot at this scale, either arm. The taskState
  arm handled the TASK-C/TASK-F corrections correctly by PAIRING LUCK — the
  correction texts happen to share specific anchors with the old decision
  ("synchronous", "nightly"), so the token/positional supersession rule that
  INVERTED W7-E2's base correction paired correctly here; the E3 suite therefore
  measures baseline action retention, not the ledger's known failure modes (the
  adversarial variant is R2-E3ADV below). TASK-E's exact failing-test assertion
  survived through summarizer verbatim-anchor discipline, not ledger protection
  (EVIDENCE_RE takes the first matching line of the tool result; N1 again). Ops:
  3 of 50 summarizer cycles needed the 16384 ladder step, max summarizer latency
  45.3 s, 209 wire calls (155 probes, 53 summarizers, 1 pong).
- Typed arm: deliberate stub in this run (`--arm typed` exits 2 with a loud
  message, never a silent substitution); the live typed-arm run is R2-E5 (W29,
  in flight at this writing).

## R2-E3ADV — adversarial correction suite (live GLM 5.3)

- Status: EXECUTED (branch rot2/w23-adv, commits 76eefbf9f + 754bb75a1; artifacts
  research/context_rot_r2/live/e3adv/ — results.json, raw-calls.jsonl, README.md).
- Category: LIVE-MODEL (glm-5.3, temperature 0, production compaction path per cycle:
  prepare_compaction -> compact -> append_compaction -> restore; the v1 ledger rendered
  as protected state on the taskState arm).
- Question: when a correction shares NO anchor token with the decision it replaces
  (supersession score 0-2 < pairing threshold 3), what does the v1 ledger do, and does
  the pipeline still answer correctly?
- Design: 3 adversarial families (semantic swap C1, numeric correction C2, retraction-only
  C3) x 2 arms x 5 cycles x probe-every-cycle x 3 reps; predictions pre-registered in
  commit 5c18dab81 BEFORE any live call; offline selftest proves the zero-pairing
  property against the real mirror ledger (superseded == [] at every cycle).
- Result: 30/30 cycle points correct BOTH arms; 0 extraction failures; strict == primary
  everywhere; anchor retention 1/1 every cycle.
- Finding (the mechanism, verified in raw-calls.jsonl): the ledger retired NOTHING;
  the stale decision stayed DECISIONS-current at every cycle while the render header
  promised "A SUPERSEDED item is history" with no superseded items to show. GLM 5.3
  resolved the contradiction in all 15 taskState summarizer outputs by annotating the
  stale item as history in its own words ("Treat as history only", "superseded in
  practice", "no longer applies to the current deliverable"). Correctness is the
  summarizer's achievement AGAINST the ledger, not a property of it — at +1.5k-1.9k
  request chars and +0.6k-1.2k summary chars per cycle (mean summarizer request
  16.8k-17.0k chars taskState vs 15.1k-15.3k default).
- Combined with R2-E2: the v1 ledger on corrections is EITHER a coin-flip that can
  invert user intent (shared-anchor luck pairs wrong: the E2 inversion) OR dead weight
  the model must argue down (no shared anchor: stale current re-asserted verbatim
  every cycle). In neither case does it DO the correction's work. The typed-state v2
  role/provenance supersession remains the fix direction.
- Budget: 124 wire calls (93 probes, 30 summarizers, 1 pong; cap 250). 0 HTTP retries;
  all summarizers finished at 8192 (no ladder steps).

## R2-E2-RM — offline re-measure: post-W11 v1 ledger on the R2-E2 live corpus (W24)

- PRODUCTION-PATH-MIRROR: a Python port of the CURRENT Rust ledger (w11_port.py, commit
  1754a3e1b: content-derived ids + correction-safe supersession) replayed over the committed
  W7 corpus (raw-calls.jsonl). Not the binary. No model calls.
- Fixture gate PASS: the pre-W11 mirror replay byte-matched every corpus summarizer request
  (5 cycles x 2 arms x 2 scenarios + ladder retries) and reproduced every cycle field of the
  committed results.json (entry ids, cut points, summary lengths, ledger item counts, final
  summaries). 4 corpus records left unconsumed = the abandoned first-run chain.
- Post-W11 pass: cut points/windows asserted identical to the fixture pass; the ledger never
  reads summaries, so the post-W11 ledgers are exact with the corpus summaries held fixed.

### Verdict (W22 open question 3: does W11 remove the R2-E2 inversion?)
- base (the scenario with the live failure): YES. Pre-W11 both corrections were inverted at
  every cycle (mutual supersession pair; A decisions stayed current) -> live incorrect at
  points 3-5 ("architecture A"). Post-W11: clean at every cycle — corrections ACTIVE and
  rendered (12/12 within cap), A decisions retired to SUPERSEDED with the correction as the
  replacement; the module-scoped live-v1 matcher passes at every cycle. Constraint churn
  (C8->C2->C3) also gone. Prediction (model-dependent): live corrections answered correctly
  at every point.
- hard: structurally YES (mutual pair gone, all four A decisions retired by their
  corrections, 17 spurious pairs -> 4 correct pairs) — but a NEW render-level defect
  candidate: with the churn gone, 26 constraints are active and CONSTRAINTS_CAP=12 omits
  the four corrections from the rendered ACTIVE list (visible only as SUPERSEDED replacement
  texts; 9 of 20 constraint lines also omitted). Not an inversion; a render-cap fidelity gap
  for authoritative corrections. Suggested direction: exempt correction carriers from the
  section caps.
- mechanism validation: the same scoring on the PRE-W11 renders reproduces the live failure
  exactly (inverted at every cycle where the live run failed at points 3-5; the live points
  1-2 passes were summary-chain inertia, which the render-level metric does not model).
- secondary (W11-independent): EVIDENCE_RE never matched this corpus's evidence shapes
  ("cargo test: N passed", "build succeeded with N warnings", "audit ... findings",
  "benchmark p95 Nms"), so VERIFIED EVIDENCE was empty in every render pre and post; live
  evidence correctness came from the summary chain. The task/subagent probe is outside the
  ledger's contract. The W11 id-collision half (W9-F2/F5) is NOT exercised by this corpus
  (no replanted retired content in later windows).

Artifacts: research/context_rot_r2/live/remeasure/{w11_port.py, replay.py, results.json,
README.md} (three-column verdict table, per-correction renders, port diff with Rust lines).
Integrated as `2fbb14f33` (worker branch `rot2/w24-remeasure`, worker commit 19add7970).

## W26 — live-model CompactBench practice run (GLM 5.3 in both roles)

- Status: EXECUTED (integration commits `6ea85dc10` live env-configured harness
  summarizer + `4b8f2165f` run artifacts and report; artifacts
  `research/context_rot_r2/compactbench_adapter/live/` — `README.md`,
  `run-config.json`, the four live `results/*.jsonl` plus the mock@HEAD
  baselines, `raw-calls.jsonl`, `aggregate.json`, `amend1-decision.json`;
  report section `reports/context-rot-r2/COMPACTBENCH-RESULTS.md` section 9).
- Category: CUSTOM-OPTIMUS + LIVE-MODEL — practice run on the public starter suite
  (slots 0-1 = 8 of 20 cases per arm, the ruled Option B budget), one live draw per
  case; NOT a leaderboard number, NOT comparable to any 1.0.0-scored run.
- Setup: GLM 5.3 as BOTH the summarizer (behind the Rust compaction-harness binary,
  production path, env-configured HTTP provider with the 8192->65536 ladder) and the
  task/eval model (in-process provider registration in the stock runner); 4 custom
  arms, 3 drift cycles, delta-append, scorer 2.0.0; 676 HTTP requests of a 768
  ceiling; one fixed-and-resumed provider incident (a `length` finish re-rolled the
  same 2048 rung instead of advancing the ladder; ~130 extra requests, no invalid
  answers recorded).
- Result (overall / drift_res / constraint_ret / contradiction / compression):
  task-state 0.6844 / 0.990 / 0.800 / 0.094 / 0.67x; echo-first-n 0.6531 / 0.833 /
  0.800 / 0.094 / 1.60x; full-transcript-oracle 0.5781 / 0.721 / 0.650 / 0.078 /
  0.92x; plain 0.4906 / 0.757 / 0.650 / 0.172 / 0.72x.
- Findings: the arms' quality separates under live eval (the W12 mock runs could not
  separate anything — every arm 0.375); the 0.194 Rust-arm gap is driven by exactly
  the ledger-mediated checks (`recall_latest_decision` 0.625 vs 0.125,
  `assign_a/b` 0.375 vs 0.125, drift resistance 0.99 vs 0.76, contradiction rate
  0.094 vs 0.172). GLM writes tighter summaries than the lossy fixture (compression
  0.46x -> 0.67x task-state on identical cycle-0 transcripts). The verbatim oracle is
  not the ceiling under live eval (0.578; worst arm on `recall_constraint` 0.500).
  Live single-draw runs are not bit-reproducible even at temperature 0 (two
  identical smoke draws scored 0.4 vs 0.1); with n = 8 per arm only the Rust-arm gap
  is a robust signal.

## W27 — live-model ContextBench practice run (retention through compaction)

- Status: EXECUTED (integration commits `da38b1edf` driver + `3e5880dd5` loader
  and sizing fix + `963ca577a` + `32a430f08` artifacts; report section
  `reports/context-rot-r2/CONTEXTBENCH-RESULTS.md` section 6; artifacts
  `research/context_rot_r2/contextbench/live/` — `README.md`, `run_config.json`,
  `results.json`, `raw-calls.jsonl`).
- Category: CUSTOM-OPTIMUS + LIVE-MODEL — practice run, not an official scorecard;
  no number is comparable to a ContextBench leaderboard result.
- Setup: 26 paired dev instances (deterministic blake2b slice of the 110 size-window
  dev candidates; 7 languages, 15 repos; holdout untouched) x 2 arms x 4
  append+compact cycles, delta-append sessions through the compaction-harness
  binary with the live GLM 5.3 summarizer (the W26 wiring), evaluated by the W10
  metric mirror; 439 requests for 208 cycle attempts (581/600 total including an
  invalidated blind first attempt plus smoke), mean latency 20.4 s, 0 transport
  retries, 47 ladder climbs, 0 error calls.
- Arms: A = baseline (taskState off) vs B = task-state-ledger (taskState prompt)
  — a documented substitution (W10's offline arm B, jev-relevance, is not
  expressible through the compaction surface).
- Method caveat: no repository checkouts exist under the offline waiver — the
  trajectories are synthetic explorations built from the gold annotations plus
  same-repo distractor reads, so the metrics measure RETENTION of the gold context
  through compaction, not free-form exploration; patch_success stays UNAVAILABLE by
  construction.
- Result (paired, n = 26; exact sign test / tie-corrected Wilcoxon): final line
  precision B 0.586 vs A 0.320 (+0.097, 18+/4-, sign p = 0.004, Wilcoxon p =
  0.003); final line coverage 0.916 vs 0.852 (p = 0.455 / 0.196); final file
  coverage 0.974 = 0.974; final file precision 0.326 vs 0.357 (p = 0.057 / 0.064);
  EditLoc via the gold-patch fallback (arm-independent).
- Findings: file-granularity retention barely separates the arms (the summarizer
  keeps naming every file it saw); line granularity does — arm B ends 69% gold /
  31% distractor retained lines vs arm A 47% / 53%. The parsed ledger itself
  populated in only 34 of 104 arm-B cycles, so the effect observed is prompt-level
  (the Protected Task State instructions), not ledger-level. Descriptive
  statistics, not confirmatory.

## R2-E5 — the typed-arm benchmark (fixture + live GLM 5.3)

- Status: EXECUTED (branch rot2/w29-typedarm; commits 4f2e6935e, bf0ba53f6, 89e8f7273;
  artifacts research/context_rot_r2/live/e5/).
- Part 1 (PRODUCTION-PATH, offline adapter, E1 19-probe x 5 cycles, all three arms one pass):
  default 0/19, taskState v1 14/19, typed 15/19. The typed ledger holds 23 records at a stable
  777/800 hot-token budget (min(2048, floor(3200/4))); the verifier passed 5/5 cycles with zero
  repairs; +886 mean request chars over v1. All six pre-registrations confirmed (15/19 with the
  predicted composition: q-evidence-0 recovered via the cargo-test-line classifier; the other three
  evidence lines match no typed tool category and are lost in both arms; the subagent probe is
  lost — agentMessage custom entries are machine text by design).
- Part 2 (LIVE-MODEL, e3 tasks A/C/E, typed vs default, 5 cycles, 3 reps, 123 wire calls):
  default 15/15 + 3 baselines correct; typed correct on every point that ran (A 5/5, C 5/5,
  E cycle-1 + baselines), 0 extraction failures. TASK-E typed HALTED at cycle 2: cycles 2-5
  failed the typed verifier with unresolved references (5/3/3/3). Mechanism (documented in
  e5/README.md): a failing-suite line's 4-token key restatement-merges into recurring 8-token
  telemetry lines; tool kinds keep the latest text; the id is re-derived from the merged text;
  the cited ids stop resolving; the repair only covers missing items. The same flow fails in the
  Rust production path. Failure direction is safe (conversation preserved, compaction refused),
  but compaction stalls: an availability bug on repeated-CI-output sessions. Pre-registrations 1
  and 4 (15/15; no hard verifier failure) NOT confirmed for E — recorded as the first live
  falsification of a typed-mode pre-registration.
- Part 3 (E3ADV C1 typed cell, 21 wire calls): correct 5/5 + baseline, 3/3 reps strict clean,
  anchor retained — but NO SUPERSEDED section at any cycle (verified in all 6 typed summarizer
  requests). The typed supersession pairing is still token-overlap-based: the zero-overlap
  correction scores 0, so the stale decision stays in DECISIONS (CURRENT) and GLM reconciled in
  its own words, exactly as in E3ADV-v1. The provenance-based retirement the campaign brief
  predicted does not hold of the implementation; recorded as defect D29.
- Verdict: typed v2 strictly dominates v1 on the fixture (verifier-gated, stable budget,
  id-stable corrections within a cycle) and matches default live wherever it ran; but two
  integration defects bound its current availability (D24 id instability under restatement-merge;
  D29 token-overlap pairing), and the E3ADV structural fix is NOT yet achieved.
- Budget: 123 + 21 = 144 new wire calls (within the 250/80 caps).
