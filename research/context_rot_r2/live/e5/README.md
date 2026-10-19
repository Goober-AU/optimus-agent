# R2-E5 — the typed-arm benchmark (W15 live experiment)

Branch `rot2/w29-typedarm`. The campaign's final experiment: measure the
W15 typed protected-state integration (commits 338b920c3 + 40bead288 +
4be9623b0, already on the integration branch) as a **production mode** on
the three harness layers where every earlier arm was measured:

- Part 1 (PRODUCTION-PATH): the offline multicycle adapter
  (`crates/pi-coding-agent/tests/rot2_multicycle.rs`) over the real Rust
  compaction pipeline, on the R2-E1 19-probe fixture scenario;
- Part 2 (LIVE-MODEL): the live GLM 5.3 harness (W17-E3 `e3/` package)
  with `taskState: "typed"`, TASK-A/C/E, typed vs default;
- Part 3 (LIVE-MODEL, decisive cell): the E3ADV adversarial correction
  family C1 (semantic swap, zero shared tokens) with the typed arm.

## Wiring (all ports cite the Rust originals)

- `crates/pi-coding-agent/tests/rot2_multicycle.rs` (Part 1):
  `arm_settings` line ~642 accepts the `typed` arm and maps it to
  `taskState: "typed"` (mirrors `resolve_task_state_mode`, compaction.rs);
  the lossy fixture routes the typed protected block's own section titles
  (`typed_section_bucket`, line ~555 — the typed render's intro orders
  "Copy every item below into the matching summary section", so every
  typed section maps to its summary bucket; v1 titles keep their original
  contains() routing, so the v1 arm is byte-identical to the committed
  E1 run); the result records the typed ledger (`details.typedState`,
  `ledger_items_value` counts its records) and the verifier outcome
  (`details.typedVerification`) per cycle (`CycleRecord.typed_verification`,
  line ~153). New always-on smoke test
  `rot2_smoke_typed_arm_persists_typed_ledger_and_verifies` (line ~1035).
- `live/typed_mirror.py` (new, the port): `typed_state.rs` —
  `classify_user_unit` (Rust line 566), `reconcile` (933),
  `build_typed_state` (1166), `merge` (1177), `allocate` (1353),
  `verify` (1495), `render_allocation` (1643), `estimate_tokens` (779);
  `compaction.rs` (W15 commit 338b920c3) — `TYPED_STATE_INSTRUCTION`
  verbatim (line 746), `typed_hot_state_budget_tokens` = min(2048,
  floor(summary_budget/4)) (764), `typed_allocation_failure` (777),
  `typed_verification_failure` (795), `typed_summary_section` (809),
  `typed_protected_line` (825), `summary_section_insert_offset` (839),
  `append_missing_protected_items` (883), the `details.typedState` merge
  in `prepare_compaction` (2060) and the allocate/unsafe gate in
  `compact_with_metrics` (2975).
- `live/rust_mirror.py`: `arm_settings` (line ~85) maps the `typed` arm to
  the typed mode; `prepare_compaction` (line ~933) builds the typed ledger
  over the leaving window and merges it with the previous cycle's
  `details.typedState`; `generate_bounded_summary` (line ~1028) takes the
  mode-selected copy instruction; the lossy fixture's typed routing
  (line ~1197) mirrors the Rust adapter change.
- `live/e3/driver.py`: the typed render branch exactly where
  `render_ledger(task_state)` was called — `tm.allocate` at line ~185 with
  `typed_hot_state_budget_tokens`, the unsafe-allocation failure BEFORE
  any model call, `render_allocation` + `TYPED_STATE_INSTRUCTION` as the
  protected block; the verify gate after the summary (`verify_and_repair`,
  line ~225; a rejected summary is discarded, mirroring `compact()`'s Err
  channel); `details.typedState`/`typedVerification` persistence (line ~239).
- `live/e3/run_e3.py` + `live/e3adv/run_e3adv.py`: the typed arm is a
  first-class arm (e3: any task; e3adv: family C1 only, enforced in
  `main()` via `TYPED_FAMILY`, run_e3adv.py line ~49).

Documented deviation: the mirror `Entry` carries no tool `is_error` flag
(the scenario schema has no such field), so tool results classify as
non-error, exactly as the v1 mirror does.

## Pre-registrations (written BEFORE the runs; not edited after)

Offline evidence used: the Python typed mirror over the exact committed
scenario documents (renders and ledgers only, no model calls), preserved
in `adapter/renders/`.

### Part 1 — E1 fixture suite, typed arm (19 probes x 5 cycles, offline)

1. **typed >= 14/19** (the taskState v1 result). Expected composition:
   constraints 8/8, decisions 4/4, corrections 2/2 — the same fourteen the
   v1 ledger protected — **plus evidence-0** = **15/19**.
2. **Evidence recovery is 1/4, not 4/4.** Mechanism: the typed
   `EVIDENCE_LINE_RE` (typed_state.rs) is case-insensitive, so
   `cargo test: 4000 passed; 0 failed` matches via `(?i)FAILED` where the
   v1 `EVIDENCE_RE` (case-sensitive `FAILED`, `test result:`) missed it —
   the "cargo-test lines the regexes missed" recovery. The other three
   evidence probes (build-warnings / audit-report / benchmark lines) match
   no typed tool category (they are not test output), so they stay lost in
   BOTH arms: classifier scoping, not pipeline failure.
3. **q-subagent stays lost (0/1).** The subagent report is an
   `agentMessage` (custom message = machine text); `entry_role_text`
   classifies only user/assistant/tool messages. Same as v1.
4. **Verifier: passed on every cycle, no repair.**
5. **Budget fits:** ~23 records, ~777 of the 800-token hot budget
   (min(2048, floor(3200/4))); filler units dedupe by content key so no
   growth at cycles 2-5. Risk noted: c6 (`preserve ... verbatim`) and c7
   (`stays single-writer`) carry no typed constraint marker, so they
   classify as LOW_PRIORITY_EPISODES (tier 7, non-mandatory); retained
   only while the budget holds.
6. The E1 corrections retire the old-A decisions under typed (shared
   `architecture`/module tokens reach the rule's threshold), so the render
   shows SUPERSEDED entries with replaced-by links, as v1 did.

### Part 2 — live E3 typed arm (TASK-A/C/E, typed vs default, 5 cycles, 3 reps)

1. **typed arm: correct at every cycle point on all three tasks (15/15)
   and all 3 baselines.** Rationale: every probe's gold is present in the
   cycle-1 hot block as typed material (A: constraint + decision; C:
   correction + superseded marker; E: the exact assertion), the
   instruction orders verbatim copying, and cycles 2-5 re-derive from the
   cycle-1 summary the same way the default arm's 50/50 run did.
2. **default arm: correct at every cycle point (15/15)** — replication of
   the committed W17-E3 result on the same tasks.
3. **0 probe extraction failures.**
4. **Verifier: passes with no repair on most cycles**; a repair, if it
   fires, appends the missing protected line verbatim (deterministic, no
   model call) and the re-verify passes. A hard verification failure
   (mis-superseded / unresolved / fabricated) would surface as a cycle
   failure and is NOT expected on these scenarios.
5. **Ops:** some typed summarizer cycles may need the 16384 ladder step
   (the typed block adds ~800 prompt tokens over the default arm);
   reasoning tokens bill against max_tokens.

### Part 3 — E3ADV C1 typed arm (the decisive cell)

**Correction to the brief's expectation, pre-registered.** The brief
expected: "typed retires the stale decision by provenance (role +
supersession), so the render should show SUPERSEDED with a replaced-by
link at cycle 1 ... no GLM reconciliation needed." The W15 code does not
do this: the typed supersession rule is still token-overlap-based
(`3 * shared_anchors + shared_content_tokens >= 4` with an anchor, or 2
shared tokens incl. one specific), and `vague_correction_does_not_retire`
(typed_state.rs test suite) pins the no-overlap case. ADV-C1's adversarial
property is exactly zero shared tokens / anchors between the correction
and the two-phase-commit decision, so:

1. **Prediction: the typed ledger does NOT retire the C1 stale decision.**
   The cycle-1 render (verified offline, `adapter/renders/`) shows
   `USER CORRECTIONS` with both correction clauses (provenance-tagged,
   [CO-id] + [@entry]) and `DECISIONS (CURRENT)` still carrying the
   two-phase-commit decision; no SUPERSEDED section, no replaced-by link,
   at every cycle.
2. **Prediction: correct at every cycle point anyway (5/5 + baseline)** —
   GLM reconciles, as it did on BOTH arms in the committed W23-E3ADV run
   (30/30). "No GLM reconciliation needed" is therefore NOT expected; the
   honest claim under test is that the typed markup does not BREAK the
   reconciliation and the anchor (SGA-KX1) survives verbatim.
3. Budget: <= 80 calls for this cell.

## Results

### Part 1 — E1 fixture suite, typed arm (adapter, PRODUCTION-PATH, offline)

Command: the R2-E1 scenario document (byte-identical to the committed
`runs/r2-e1-multicycle.scenario.json` except `arms`) through
`cargo test -p pi-coding-agent --test rot2_multicycle
rot2_production_multicycle -- --ignored` with `ROT2_SCENARIO`/
`ROT2_RESULT`. Artifacts: `adapter/r2-e5-e1-typedarm.{scenario,result,
score}.json`.

Per-arm totals (19 probes x 5 cycles, deterministic lossy summarizer):

| arm | retained | constraint (8) | decision (4) | evidence (4) | supersession (2) | task (1) | ledger records | mean request chars |
|---|---|---|---|---|---|---|---|---|
| default | 0/19 | 0 | 0 | 0 | 0 | 0 | - | 14,765 |
| taskState (v1) | 14/19 | 8 | 4 | 0 | 2 | 0 | 22 | 18,701 |
| **typed (W15)** | **15/19** | **8** | **4** | **1** | **2** | 0 | 23 | 19,587 |

Per-probe (typed arm; v1 differs only where marked):

| probe | default | taskState v1 | typed | note |
|---|---|---|---|---|
| q-constraint-0..7 (8) | lost | retained | retained | typed classifies c0/c5 as USER_CORRECTION / c6 as SAFETY (no new deps without approval), c1-c4 as USER_CONSTRAINT, c7 as LOW_PRIORITY_EPISODE — all retained within budget |
| q-decision-0..3 (4) | lost | retained | retained | DECISIONS (CURRENT), unverified (no tool corroboration) |
| q-evidence-0 | lost | lost | **retained** | `cargo test: 4000 passed; 0 failed` — the `(?i)FAILED` match v1's case-sensitive regex missed |
| q-evidence-1..3 | lost | lost | lost | build/audit/benchmark lines match no typed tool category (classifier scoping, both arms) |
| q-correction-0..1 | lost | retained | retained | the corrections retire the old-A decisions (SUPERSEDED with replaced-by links, as v1) |
| q-subagent | lost | lost | lost | agentMessage = machine text; `entry_role_text` classifies only Message entries |

Typed-arm cycle detail: 5/5 cycles ran; ledger 23 records at every cycle
(stable across cycles 2-5: filler units dedupe by content key); hot
allocation 777 of the 800-token budget; **verifier passed on every cycle
with zero findings and zero repairs** (`typedVerification`:
`{passed: true, repaired: false, missing: 0, misSuperseded: 0,
unresolvedRefs: 0, fabricated: 0}` at cycles 1-5).

Post-D25 re-measurement (W46, rot2/w46-d30): re-running this exact
scenario document through the current integration tip reproduces default
0/19 and taskState 14/19, but the typed arm scores **13/19** with a
19-record ledger from cycle 2 on. The committed 15/19 above was measured
on the pre-D25 W29 code, where Step-scoped records accumulated across
cycles. Under the D25 step boundary (W36 a6da817e3) the four Step-scoped
seed records that the filler never restates retire at the first merge:
the two episode-classified constraints (c6 `preserve ... verbatim`,
c7 `stays single-writer`), the assistant episode (`Started the migration
plan.`) and the pending action (`Section 3 verified; publishing next.`),
verified offline against the exact seed window (23 records at cycle 1,
identical classification; zero user-stated decisions in the chain). This
is D25 working as designed, not a regression: the E1 fixture's c6/c7
carry no constraint marker, so their loss is the documented
classifier-scoping outcome now compounded by the step boundary. The W46
D30 gate is not implicated (no E1 user unit matches the decision
vocabulary outside the correction gate).

Pre-registration verdict: predictions 1-6 **all CONFIRMED** (15/19 with
exactly the predicted composition and the 1/4 evidence mechanism).

### Part 2 — live E3 typed arm (LIVE-MODEL, GLM 5.3)

Tasks A/C/E, arms typed vs default, 5 cycles, 3 reps, 1-rep baseline.
Artifacts: `live/e3-typed-results.json`,
`live/e3-typed-raw-calls.jsonl` (typed-arm wire calls only; the e3
package's own results/raw log are restored to the committed W17-E3 state
afterwards — E5 keeps its own snapshots).

| arm | task | gold (action level) | base | c1 | c2 | c3 | c4 | c5 |
|---|---|---|---|---|---|---|---|---|
| default | A | edit `src/parser.rs`, never `src/auth.rs` | correct | correct | correct | correct | correct | correct |
| default | C | current approach: idempotency keys | correct | correct | correct | correct | correct | correct |
| default | E | exact error `assertion failed: depth == 4096 (got 3072)` | correct | correct | correct | correct | correct | correct |
| **typed** | A | (same golds) | correct | correct | correct | correct | correct | correct |
| **typed** | C | | correct | correct | correct | correct | correct | correct |
| **typed** | E | | correct | correct | **halt** | **halt** | **halt** | **halt** |

- Every probe point that ran was correct (11/11 cycle points + 3 baselines,
  3/3 reps at every point, strict == primary, 0 extraction failures).
- **TASK-E typed halted at cycle 2: cycles 2-5 failed the typed verifier**
  (see Finding 2). "halt" = the compaction episode failed before persisting
  (production keeps the session unchanged), so no probe round ran at that
  cycle. The cycle-1 context (which carried the exact assertion verbatim)
  remained the restored context for the rest of the run; final anchor
  retention 1/1.
- Verifier totals on the typed arms: 11 of 15 e3 cycles passed with zero
  findings and zero repairs (A: 5/5, C: 5/5, E: cycle 1); 4 failed (E
  cycles 2-5, unresolved references: 5, 3, 3, 3).
- Typed allocation growth (records / hot tokens of 800): A 17/592 ->
  30/791 (hit the 800 cap at cycle 4; the allocator dropped low-tier
  filler evidence by design, no unsafe compaction); C 13/419 -> 26/772;
  E 11/310 -> halted.
- Ops: E typed hit the 8192 length budget repeatedly (11 of its 35
  summarizer wire records; ladder completed each time at 16384);
  A/C typed needed no ladder step. Usage (prompt/completion/reasoning,
  whole chain incl. probes): A typed 49,829/22,597/15,213 vs default
  30,064/10,458/6,430; C typed 47,035/28,757/21,647 vs default
  30,528/11,055/6,745 — the typed arm roughly doubles token spend at
  this scale (longer summaries that cite [id]/[@entry] refs drive the
  completion side).
- Budget: 123 new wire calls (71 typed A/C/E + 22 E re-run after a
  wiring fix + 30 fresh default resamples), within the <= 250 Part 2
  budget; the e3 raw log total reached 332 of its own 400 guard.

Pre-registration verdict: prediction 2 (default 15/15) CONFIRMED;
prediction 3 (0 extraction failures) CONFIRMED; prediction 5 (ops)
CONFIRMED; prediction 1 (typed 15/15) **NOT CONFIRMED as stated** — A and
C confirmed 5/5 each, but E's pipeline halted after cycle 1 (every point
that ran was correct; the halt is a verifier rejection, not a probe
failure); prediction 4 (no hard verification failure expected) **NOT
CONFIRMED** — the E family triggers exactly that failure mode (Finding 2).

### Part 3 — E3ADV C1 typed arm (the decisive cell, LIVE-MODEL)

Artifacts: `live/e3adv-typed-c1-results.json`,
`live/e3adv-typed-raw-calls.jsonl`.

| arm | family | gold (action level) | base | c1 | c2 | c3 | c4 | c5 |
|---|---|---|---|---|---|---|---|---|
| default (committed W23) | C1 | saga pattern, never the 2PC coordinator | correct | correct | correct | correct | correct | correct |
| taskState (committed W23) | C1 | | correct | correct | correct | correct | correct | correct |
| **typed (E5)** | C1 | | correct | correct | correct | correct | correct | correct |

- 21 wire calls (5 cycles x (1 summarizer + 3 probes) + 1 baseline; one
  cycle needed the 16384 ladder step), within the <= 80 cell budget.
- Every cycle point correct, 3/3 reps, strict clean ("Saga pattern with
  compensating transactions"; the stale two-phase-commit approach never
  asserted). The anchor SGA-KX1 retained at every cycle.
- **The typed render carried NO `SUPERSEDED (HISTORY, NOT CURRENT)`
  section at any cycle** (verified in all 6 typed summarizer requests in
  the raw log): the zero-overlap correction scores 0 under the
  token-overlap supersession rule, so the stale decision stays in
  DECISIONS (CURRENT), exactly as pre-registered (correction to the
  brief's expectation). GLM reconciled anyway — annotating the stale
  decision as history in its own words, the same behavior as the
  committed v1 arm.

Pre-registration verdict: predictions 1-3 **all CONFIRMED** (including
the pre-registered correction: no retirement, no SUPERSEDED link, GLM
reconciles despite the ledger).

## v1-vs-typed comparison (against the committed campaign numbers)

| benchmark (committed unless marked E5) | default | taskState v1 | typed (W15, E5) |
|---|---|---|---|
| E1 adapter, 19-probe offline fixture | 0/19 | 14/19 | **15/19** |
| E1 adapter, evidence probes | 0/4 | 0/4 | **1/4** (cargo-test line recovered) |
| E1 adapter, constraint/decision/supersession | 0/14 | 14/14 | 14/14 |
| E1 adapter, ledger prompt cost (mean request chars) | 14,765 | 18,701 (+3,936) | 19,587 (+4,822) |
| E1 adapter, ledger stability | - | 22 items, fixed caps (v1 caps: 12/10/8/8) | 23 records, cap-free, 777/800 budgeted |
| E2 live recall probes (base scenario) | 19/19 all cycles | 19/19 c1-c2, 17/19 c3-c5 | not run (out of E5 scope) |
| E3 live action probes, A/C/E | 15/15 (W17) | 15/15 (W17) | A 5/5, C 5/5, E 1/5 ran (verifier halt; all ran points correct) |
| E3ADV C1 adversarial correction | 5/5 (W23) | 5/5 (W23, no retirement) | 5/5 (no retirement; provenance markup only) |
| Verifier gating | none | none | 16 passed / 4 failed live cycles (the 4 = E c2-c5, unresolved refs) |

## Findings

1. **Part 1 (offline, production path): typed beats v1 exactly as
   pre-registered — 15/19 vs 14/19 — and the gain is precisely one
   evidence probe.** The brief's expectation "typed recovers the evidence
   probes (v1: 0/4)" holds only for the cargo-test-shaped line: the
   typed `EVIDENCE_LINE_RE` is case-insensitive (`(?i)FAILED`), so
   `cargo test: 4000 passed; 0 failed` now classifies as
   VERIFIED_TOOL_RESULT, while the v1 case-sensitive regex missed it. The
   other three evidence probes (build-warnings / audit-report /
   benchmark-p95 lines) match no typed tool category at all — they are
   lost in both arms for the same reason (classifier scoping), so the
   typed ledger's evidence recovery is a one-line regex fix's worth of
   gain on this fixture, not a structural one. Everything else v1
   protected stayed protected (constraints 8/8, decisions 4/4,
   corrections 2/2 with the old-A decisions retired), at +886 mean
   request chars over v1 (19,587 vs 18,701) — inside the
   913..1751-prompt-token band the v1 ledger measured. The ledger was
   budget-stable (23 records, 777/800 hot tokens, no growth across
   cycles: filler dedupes by content key) and the verifier passed with
   zero findings on all five cycles.
2. **Part 2 (live): TASK-E exposes a record-id instability failure mode
   in the W15 typed ledger — the significant negative result of E5.**
   Mechanism, reproduced deterministically offline with the mirror and
   visible in the raw calls:
   - The failing-suite line `test result: 55 passed; 1 failed` has a
     4-token content key that is a strict subset of every recurring
     telemetry line's key (`cargo test --lib telemetry::tests_NN: test
     result: N passed; 0 failed`, 8 tokens). typed_state.rs's
     restatement rule (`shared == min(|a|,|b|)` and `shared >= 3`)
     therefore merges the failing line into the telemetry record, and
     tool-derived kinds keep the LATEST text.
   - At cycle 1 the merged record holds the failing text under the
     telemetry record's id (`VT-1cfcd1`); GLM's cycle-1 summary cites
     `[VT-1cfcd1]` and passes verification. At cycle 2 the new telemetry
     lines overwrite the record's text again, and `reconcile()` re-derives
     the id from the CURRENT text key — so the record's id changes
     (`VT-1cfcd1` -> `VT-2632d8`) and the previously-cited id no longer
     resolves in the merged ledger.
   - GLM's cycle-2 summary (correctly) preserves the previous summary's
     `[VT-1cfcd1]` citations; `verify()` flags them as unresolved
     references (5 findings, then 3, 3, 3 on the retries). The one
     deterministic repair covers only MISSING items, so the episode fails
     safely four cycles in a row: no compaction, conversation preserved,
     probe rounds 2-5 never run.
   - The same flow fails in the Rust production path:
     `prepare_compaction` merges `details.typedState`, `reconcile`
     reassigns ids from text, and `verify` rejects the stale references —
     so this is a W15 integration defect (record ids are not stable
     identifiers when restatement-merges occur), not a mirror artifact.
     The failure direction is safe (nothing is lost; the conversation is
     preserved), but compaction stalls, so the context grows instead of
     compacting — an availability bug on exactly the evidence-heavy,
     repeated-CI-output sessions the typed ledger was built for. The fix
     direction is clear: persist the id with the record across merges
     (or verify against the union of current and previous-cycle ids).
   - Contrast: TASK-A and TASK-C are id-stable (no restatement-merge
     churn), passed verification at every cycle with zero repairs, and
     scored 5/5 — so the failure mode is specific to recurring
     near-duplicate tool lines whose token sets nest.
3. **Part 2 (live): where the typed pipeline completes, typed == default
   == taskState at 100% on action probes** (A and C 5/5 each, baselines
   correct, 0 extraction failures) — consistent with W17-E3/W23-E3ADV:
   GLM 5.3 retains action-level facts through five compaction cycles
   regardless of the ledger. The measurable typed-arm delta is cost, not
   correctness: roughly double the token spend on these chains (A:
   49.8k vs 30.1k prompt tokens; completion grows faster because the
   summaries cite [id]/[@entry] references), plus the length-ladder
   pressure on evidence-heavy requests (11 of 35 E-family typed
   summarizer wire records hit the 8192 budget; A/C needed no ladder).
4. **Part 3 (live): the decisive-cell outcome is that the typed ledger's
   supersession is still token-overlap-based, so the adversarial C1
   correction retires nothing — and GLM reconciles anyway.** The render
   carried no SUPERSEDED section at any cycle (verified in the raw log);
   the stale two-phase-commit decision stayed in DECISIONS (CURRENT)
   with an `unverified` provenance tag next to user-authority
   `[CO-...]` corrections. GLM's summaries annotated the stale decision
   as history and restated the operative approach at every cycle, 3/3
   reps, strict clean — identical to the committed v1-arm behavior. So
   the typed ledger's contribution on C1 is provenance markup and stable
   record identity, not arbitration; the correctness remains the
   summarizer's achievement against the ledger (W23-E3ADV's conclusion,
   now confirmed under the W15 implementation). The typed markup did not
   hurt: no probe regressed, the anchor survived verbatim, and the
   verifier passed every C1 cycle with zero findings.

## Files

- `adapter/` — Part 1: `r2-e5-e1-typedarm.{scenario,result,score}.json`
  (the scenario is the committed E1 document with `arms` extended; all
  three arms re-run in one adapter pass), `renders/` (offline
  cycle-1 typed hot blocks and ledgers for TASK-A/C/E and ADV-C1, the
  pre-registration evidence).
- `live/` — Parts 2-3: `e3-typed-results.json`,
  `e3adv-typed-c1-results.json` (result snapshots + budget notes),
  `e3-typed-raw-calls.jsonl`, `e3adv-typed-raw-calls.jsonl`
  (typed-arm wire calls only; the e3/e3adv packages' own results and raw
  logs are restored to their committed W17/W23 state — E5 keeps its own
  snapshots).
- `../typed_mirror.py`, `../rust_mirror.py`, `../e3/driver.py`,
  `../e3/run_e3.py`, `../e3adv/run_e3adv.py` — the mirror wiring
  (see "Wiring").
- `../../../crates/pi-coding-agent/tests/rot2_multicycle.rs` — Part 1.

## Reproduction

```
# Part 1 (offline; from the repo root, isolated target dir)
ROT2_SCENARIO=research/context_rot_r2/live/e5/adapter/r2-e5-e1-typedarm.scenario.json ROT2_RESULT=/tmp/e5-result.json cargo test -p pi-coding-agent --test rot2_multicycle rot2_production_multicycle -- --ignored --nocapture
cargo test -p pi-coding-agent --test rot2_multicycle   # includes the typed smoke test

# Parts 2-3 (live; from research/, GLM 5.3 endpoint, credentials per campaign brief)
python -m context_rot_r2.live.e3.run_e3 --arm typed --tasks A,C,E
python -m context_rot_r2.live.e3.run_e3 --arm default --tasks A,C,E
python -m context_rot_r2.live.e3adv.run_e3adv --arm typed --families C1

# Offline self-tests (fixture summarizer; no model calls)
python -m unittest context_rot_r2.live.e3.selftest context_rot_r2.live.e3adv.e3adv_selftest
```
