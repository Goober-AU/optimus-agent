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

## NOT EXECUTED (updated at the W34 refresh, again at W49)

Executed since this list was first written: the live-model arms (R2-E2 base+hard
below, R2-E3, R2-E3ADV, R2-E5 typed), the offline re-measure R2-E2-RM, the
typed-arm re-measure R2-E5-RM (W47: the E1 fixture on the integrated tip plus
the two affected live cells), the CompactBench practice suites (W12 mock, W26
live) and the ContextBench live practice run (W27) — sections below. Still not
executed:
- any official / leaderboard-comparable number: every CompactBench run is
  CUSTOM-OPTIMUS (public practice suites, custom adapter, scorer 2.0.0; the W26
  live run is 8 of 20 starter cases per arm, one live draw per case); every
  ContextBench number is the offline pipeline or the W27 practice run (synthetic
  retention trajectories, patch_success UNAVAILABLE).
- ContextBench repository checkouts, free-form agent trajectories and the holdout
  run (the W10 blockers, unchanged).
- the unaffected E5 live cells re-run post-fix (e3 tasks A/C: E5-RM re-ran the
  affected cells only; A/C scored 5/5 in the committed run and are id-stable
  there, e5/README.md Finding 2).
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
  message, never a silent substitution); the typed arm was later wired for
  R2-E5 (adapter `08f16adf0`, mirror `fe6b6d966`), whose results are the
  section below.

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
  default 0/19, taskState v1 14/19, typed 15/19 at the pre-D25 implementation (W29, committed
  E5); 13/19 on the integrated tip (W46/W47 re-measurements: the D25 step boundary correctly
  retires two Step-scoped episode-classified probes, q-constraint-6/7 — episodes by classifier
  design, they carry no constraint marker, retired at the first merge that does not restate
  them, not a regression; decisions 4/4, evidence 1/4, supersession 2/2 unchanged; verifier 5/5
  both — R2-E5-RM below). The pre-D25 typed ledger holds 23 records at a stable 777/800
  hot-token budget (min(2048, floor(3200/4))); the verifier passed 5/5 cycles with zero repairs;
  +886 mean request chars over v1 (on the tip: 23 records at cycle 1, then 19 from cycle 2 at
  665 hot tokens, verifier still 5/5 with zero findings). All six pre-registrations confirmed at
  the pre-D25 implementation (15/19 with the predicted composition: q-evidence-0 recovered via
  the cargo-test-line classifier; the other three
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
- Verdict (at the pre-D25 implementation): typed v2 strictly dominates v1 on the fixture
  (verifier-gated, stable budget, id-stable corrections within a cycle) and matches default live
  wherever it ran; but two integration defects bound its current availability (D24 id
  instability under restatement-merge; D29 token-overlap pairing), and the E3ADV structural fix
  was not achieved by the W15 implementation. On the integrated tip the fixture count is 13/19
  vs v1's unchanged 14/19 (the D25 step boundary trades the two Step-scoped episode-classified
  probes for a bounded, budget-stable ledger — R2-E5-RM below), and both integration defects and
  the E3ADV structural gap are fixed (D24/D29, post-wave note below).
- Budget: 123 + 21 = 144 new wire calls (within the 250/80 caps).
- Post-wave note (W49): both integration defects this run surfaced are fixed —
  D24 by `891702c32` (W37) and D29 by `4315f9154` (W41, live-confirmed
  `621dc462f`) — see DEFECT-WAVE-2 below. A full post-fix re-run of this
  benchmark was NOT executed at that writing; R2-E5-RM (W47, below) has since
  re-measured the affected cells on the fixed tree (fixture 13/19, TASK-E no
  halt, C1 SUPERSEDED at every cycle).

## DEFECT-WAVE-2 — the typed-state red team and the D21-D29 fix wave (verified W49)

- Status: EXECUTED. Red team `f7bc6306c` (W35); fixes D21 `a9035ce8a` (W32),
  D25 `a3997b5d6` (W36), D23+D24 `891702c32` (W37), D22+D26+D27 `51c9a6a59`
  (W38), D28 `1e6e5ff9d` (W39), D29 `4315f9154` (W41); every number in the
  battery below is this worker's own re-run in the worktree
  `optimus-rot2-w49-wavereport` at the integration tip `71e770c5e` (103
  commits over base `6000d6bc1`), 2026-10-19.
- Category: PRODUCTION-PATH (offline unit/integration): the suites drive the
  real `prepare_compaction` -> `compact` -> `append_compaction` ->
  `SessionManager` restore pipeline in-process — `rot2_native_typed_chain`
  offline through a dual-mode fixture API (deterministic text summarizer plus
  a registered compact handler with `supports_compaction`) — or the
  typed-state module directly. No model calls.

### The red team (W35, `f7bc6306c`)

One adversarial test per attack surface against the typed (v2)
protected-state integration: role-gate poisoning via tool output, starved
hot-state budgets, the repair bound, three-cycle supersession merges,
v1/typed details interop, env precedence and the W27 ledger-population
concern (`crates/pi-coding-agent/tests/rot2_typed_adversarial.rs`, 1 151
lines at introduction). Eight tests pinned the guarantees that hold. Six
failed red-first and became BUG-LEDGER D22-D27, each `#[ignore]`d with its
D-number as the reason and runnable red with `-- --ignored`; D28 was
code-cited in the same pass (no offline fixture was reachable within the
worker's file map). With D21 (the render cap, found by R2-E2-RM) and D29
(token-overlap pairing, found by R2-E5 part 3) the wave's scope was D21-D29:

| id | red finding |
| --- | --- |
| D21 | CONSTRAINTS_CAP=12 could omit the correction carriers from the rendered ACTIVE list while their stale targets stayed visible as SUPERSEDED replacement texts (R2-E2-RM hard corpus: 26 active constraints, cap 12, the four corrections cap-omitted) |
| D22 | `record_line` matched the `UserCorrection \| CurrentTask` kind arm before the `status == Superseded` arm, so a superseded correction or task rendered `(supersedes [X])` with X = the record that retired it — the retirement direction inverted in the render the summarizer copies |
| D23 | a bracketed ticket-style token in user text (`[JX-4f1c2a]`, also entry-shaped `[issue42]`) collided with the verifier's reference grammar: the obedient verbatim copy failed verification with an unresolved reference the bounded repair cannot fix |
| D24 | `reconcile` recomputed every record id from the current text, so a tool-derived record whose text a restatement replaced churned its id at the next merge (`RS-920746` -> `RS-1cdf29`), stranding the `[id]` references earlier summaries carry — the R2-E5 part-2 TASK-E halt mechanism |
| D25 | `RecordScope` was decorative (`scope()` assigned, never read): Step-scoped PENDING_ACTIONs accumulated CURRENT across merges, were `required_present` in every later summary and re-appended unbudgeted by the repair — unbounded ledger and summary growth ending in a permanent unsafe-allocation failure |
| D26 | a `details.typedState` payload that failed strict deserialization was silently dropped through `.ok()`, losing every durable record below the boundary with no trace |
| D27 | `is_heading_line` treated any all-caps line as a heading, so an all-caps line claiming verified material evaded the fabricated-claim check |
| D28 | the native arm of `compact_with_metrics` returned `typed_state: None`: one native provider compaction silently dropped the typed durable chain the episode had merged from the persisted entry |
| D29 | the typed supersession pairing was token-overlap-based: the zero-overlap E3ADV C1 correction scored 0, the stale decision stayed CURRENT at every cycle and no SUPERSEDED section ever rendered |

### The fixes (integrated shas; worker-branch shas in parentheses)

| defect | fix | mechanism |
| --- | --- | --- |
| D21 | `a9035ce8a` (W32) | `render_ledger` orders every capped section through `prioritize_corrections`: correction carriers always render ahead of the non-correction items and the cap applies to the remaining slots; the dropped-count line states how many corrections were protected; a section without corrections is byte-identical to the previous plain head-take. Guards: `rot2_render_cap` (7 tests); offline replay `0b824f358` |
| D25 | `a3997b5d6` (W36; worker `a6da817e3`) | `merge` is the step boundary: a Step-scoped record carried from a summarized window retires unless the new window restates it; retired record/entry ids move to the bounded `retired_refs` (512) so `[id]`/`[@entry]` references the previous summary still quotes stay resolvable; defense-in-depth `MAX_DURABLE_RECORDS` = 512 with the dropped count rendered (the D21 idiom) |
| D23+D24 | `891702c32` (W37; worker `8bc0811fb`) | the verifier's reference grammar matches exactly what the renderer emits (kind prefix plus six lowercase hex; `@` sigil for entries; a bracketed token the ledger itself quotes inside a record's text is exempt as verbatim source material); identity is assigned at first sight and `merge` only fills empty ids — the text is a version, not the identity |
| D22+D26+D27 | `51c9a6a59` (W38) | render direction depends on status (CURRENT renders `supersedes [target]`; Superseded renders `superseded by [retiring]`); `parse_typed_state` salvages per record (positional fields repaired in place, unrecoverable slots counted, diagnostics surfaced as `details.typedVerification.malformedPriorLedger` with droppedRecords/repairedFields); heading detection requires structural shape (markdown/bold prefix or an exact `SECTION_TITLE_LINES` match) |
| D28 | `1e6e5ff9d` (W39; worker `d41c74c83`) | the native arm persists the ledger it received unchanged (deadline-fallback carry-through semantics, no typedVerification record); new suite `rot2_native_typed_chain.rs` drives the real pipeline offline with the dual-mode fixture API |
| D29 | `4315f9154` (W41; worker `e616a3126`) + `e71c21563` (test helper) | two marked retire paths: token (non-correction kinds outrank corrections as targets, so a later correction retires its stale decision instead of swallowing the previous correction) and provenance (the most recent prior CURRENT decision, when the correction is the only un-retired correction introduced since it and no other decision intervened); both paired records carry `retire_path`, rendered as a pairing annotation |

D29 verification: offline mirror port and C1 cell `f932b746e`; live
confirmation `621dc462f` (the E3ADV C1 typed cell re-run on live GLM 5.3,
22 wire calls of a 25 budget, temperature 0, ladder 8192->65536: every probe
correct 3/3 strict at every cycle plus baseline, the stale decision retired
at every cycle with the replaced-by link and retirePath provenance,
SUPERSEDED rendered at cycles 1-4).

Wave support: `c5136ce50` repaired the D25/D23 verify-merge brace damage;
`7bec70f09` re-pointed the multicycle typed-arm smoke at the step boundary
and filed D30; `fa3634b7a` (W42) dropped the four campaign-introduced
warnings; `03ba7abfa` (W42) is the pre-PR reviewer-simulation audit;
`71e770c5e` deduped the D22-D29 BUG-LEDGER rows after the merges.

### The verification battery (this worker's run, `71e770c5e`, 2026-10-19)

| suite | command | result |
| --- | --- | --- |
| adversarial red-team | `--test rot2_typed_adversarial` | 23 passed, 0 failed, 0 ignored (0.30 s) |
| typed integration | `--test rot2_typed_integration` | 9 passed, 0 failed, 0 ignored (0.29 s) |
| native typed chain (D28) | `--test rot2_native_typed_chain` | 2 passed, 0 failed, 0 ignored (0.25 s) |
| multicycle adapter | `--test rot2_multicycle` | 5 passed, 0 failed, 1 ignored (0.76 s) |
| render cap (D21) | `--test rot2_render_cap` | 7 passed, 0 failed, 0 ignored (0.05 s) |
| v1 ledger fixes (W11) | `--test rot2_ledger_fixes` | 7 passed, 0 failed, 0 ignored (0.03 s) |
| typed_state units | `--lib typed_state` | 74 passed, 0 failed, 3 845 filtered out (2.88 s) |
| compaction module units | `--lib core::compaction` | 160 passed, 0 failed, 3 759 filtered out (2.34 s) |

The ignored `rot2_multicycle` test is the research adapter
(`rot2_production_multicycle`, `ROT2_SCENARIO`/`ROT2_RESULT` env). The six
formerly-`#[ignore]`d D22-D27 defect tests are un-ignored and green; no
defect-`#[ignore]` remains in the adversarial suite. Spot checks because the
wave touched `compaction.rs` (D26 salvage, D28 native carry):
`compaction_summary_path` 8 passed, 0 failed (0.01 s);
`compaction_parity_suite` 14 passed, 0 failed (8.32 s; the W43 T05.4 fixture
re-pin `31ce39b84` holds at this tip).

### Open after the wave

- D30 (filed `7bec70f09`): a decision stated in USER text falls through the
  classifier to LowPriorityEpisode (Step scope) and is retired at the first
  merge, while the same sentence from the assistant classifies as
  VerifiedDecision (Task scope) and survives — the multicycle typed-arm smoke
  decides via the assistant entry to stay green.
- the W41 cycle-5 budget-overflow caveat (`621dc462f`): the tier-8 history
  line drops to the fixed 800-token hot budget in the E3ADV C1 cell; budgets
  and caps were out of D29's scope.
- both were assigned to W46 (`rot2/w46-d30`), in flight at that writing
  with no commits over the integration head `71e770c5e` - both since
  resolved on this branch: D30 fixed (`10e2b36e6`) and the cycle-5 caveat
  accepted-and-pinned (`d00ff2067`).
- a full post-fix R2-E5 live re-run was NOT executed at that writing: only
  the D29 C1 cell was re-run live (`621dc462f`); R2-E5-RM (W47, below) has
  since re-measured the affected cells on the fixed tree.



## R2-E5-RM — typed-arm re-run on the fixed implementation (fixture + live)

Branch `rot2/w47-e5rm` (integration tip 71e770c5e). The committed R2-E5
results measured the W15 typed implementation before D24 (stable ids) and
D29 (provenance pairing) landed; E5's two negative findings were the
TASK-E cycle-2 halt (unresolved refs from id churn) and the E3ADV C1
no-SUPERSEDED render. This re-run updates the record for the affected
cells on the fixed tree. Artifacts: `research/context_rot_r2/live/e5/rm/`
(README with the full tables, offline cell results, render snapshots,
live results and raw calls).

Method: the typed mirror was ported to the tip's typed_state.rs first
(commit 0bd183a5e: D22 render direction, D23 verify grammar, D24 id
carrying, D25 merge step boundary + retiredRefs + durable cap, D27
heading shape; D26 salvage out of scope for well-formed payloads; D29
was already ported). Validation: the pre-port mirror reproduced the
committed E5 numbers exactly (E1 15/19 - the pre-D25 number - via run_arm_e3
+ the lossy fixture summarizer; the C1 offline cell byte-matched d29-c1-offline-results.json;
the TASK-E chain reproduced the Finding 2 churn); post-port the e3/e3adv
selftests (14+15) stay green.

- Part 1 (PRODUCTION-PATH-MIRROR, offline, deterministic):
  - E1 19-probe fixture, typed arm: **13/19** (was 15/19 at E5).
    constraints 6/8, decisions 4/4, evidence 1/4, supersession 2/2, task
    0/1; verifier passed 5/5, ledger 23 then 19 records. The two-probe
    drop (q-constraint-6/7) is the D25 step boundary retiring the two
    LOW_PRIORITY_EPISODE-classified constraint probes at the first
    merge that does not restate them — a tip behavior, not a D24/D29
    regression; every probe the v1 ledger protected minus those two,
    plus the cargo-test evidence line.
  - E3ADV C1 offline: D29 holds on the tip — the decision retires by
    provenance at every cycle, and the SUPERSEDED section renders at
    all five cycles (the W41 run's cycle-5 tier-8 overflow is gone: the
    step boundary keeps the ledger 3 records smaller, 697/800 hot).
  - TASK-E id chain: the D24 sentinel (VT-1cfcd1) is present at every
    cycle 1-5; previous-render refs resolve at cycles 3-5; cycle 2
    strands exactly two refs ([VT-25d467], [@d09cc350]) — the
    restatement-absorbed tests_01 record, recorded as a defect candidate
    (no tombstone for absorbed ids; distinct from D24/D25).
- Part 2 (LIVE-MODEL, GLM 5.3, 46 wire calls of the <= 120 budget,
  pre-registrations in the rm/README written before the calls):
  - TASK-E typed: **5/5 cycles, no halt** (was: halt at cycle 2). The
    verifier passed every cycle (cycles 1-2 via the deterministic
    missing-item repair; 0 unresolved refs anywhere; was 5/3/3/3 at E5).
    Probe q-e-error correct at every cycle point + baseline, 3/3 strict
    reps; the exact assertion survives verbatim. The pre-registered
    residual risk (absorbed-record refs) did not trigger: GLM cites the
    absorbed record as `[VT-25d467 @d09cc350-...]`, a bracket containing
    a space, outside the verifier reference grammar.
  - E3ADV C1 typed: **SUPERSEDED rendered at every cycle 1-5** (W41:
    cycles 1-4), decision retired by provenance every cycle, every
    accepted summary copies the retirement (no GLM reconciliation
    needed), verifier passed every cycle with zero findings/repairs and
    zero retries (W41 needed retries on two cycles), probes 5/5 +
    baseline 3/3 strict, anchor SGA-KX1 retained.
- Verdict: both E5 negative findings are resolved on the integrated tip —
  D24 removes the TASK-E halt (live-confirmed end to end) and D29 renders
  the C1 retirement (live-confirmed, now at all five cycles). The typed
  arm's fixture score on the tip is 13/19 (D25 trades the two
  step-scoped constraint episodes for a smaller, budget-stable ledger);
  the absorbed-record stranding is the one open defect candidate.
