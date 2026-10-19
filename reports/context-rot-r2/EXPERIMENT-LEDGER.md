# EXPERIMENT LEDGER — context rot iteration 2

Every arm, every run, every NOT-EXECUTED item. Production-path measurements
run the real Rust pipeline (`prepare_compaction` -> `compact` ->
`append_compaction` -> `SessionManager` restore) via
`crates/pi-coding-agent/tests/rot2_multicycle.rs`; Python only orchestrates.

## R2-E1 — multicycle retention, default vs taskState (deterministic lossy summarizer)

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

- same adapter, smaller scenario; default lost all anchors by cycle 2;
  taskState retained constraint+decision, lost evidence. Superseded by R2-E1
  (same mechanism, larger probe set).

## NOT EXECUTED (yet)

- live-model arms (real GLM 5.3 summarizer + real probe answering): BLOCKED on
  typed-state integration; planned as R2-E2 with the delimiter answer protocol.
- CompactBench official suite: adapter design complete (see
  KNOWLEDGE-TRIAGE-STUDY.md / COMPACTBENCH-ADAPTER-DESIGN.md), not yet run.
- ContextBench coding tasks: eval design complete, not yet run.

## E-INC — incremental/selective compaction experiments (W9, offline, production path)

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
  other) target exactly this mechanism; W7's raw-calls.jsonl is the regression corpus.

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
