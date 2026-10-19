# Context rot iteration 2: compaction defect fixes, production-path benchmarks, typed-state v2 wired as an opt-in mode

Branch: `improve/context-rot-iteration-2` (base `6000d6bc1`, v0.1.36 + #181/#182).
Campaign reports live in `reports/context-rot-r2/`; every number below cites a
committed artefact. This body was written against the integration head
`f8ad670c8` and refreshed at the current tip on `rot2/w45-auditfix`: the
D18/D19/D20/D21 recovery wave, the R2-E5 typed-arm measurement and the
D22-D29 red-team fix wave are all ancestors of this head and are reflected
below. The integrator adjusts if later commits land before merge.
78ccdf254 (docs(context-rot): cross-report consistency sweep at the W50 tip)

## Summary

Iteration 1 (`reports/context-rot/FINAL-REPORT.md`) measured compaction policies
through Python mirrors of the production prompts. This PR is its follow-up and
does four things:

1. Fixes twelve audited compaction defects (D1, D5, D6, D8, D9, D10, D11, D12,
   D13, D14, D16, D17), pins the D2 continuation contract with regression
   tests, and repairs the v1 task-state ledger's id and supersession semantics
   (W9-F2/F4/F5): content-derived ids that survive merges, and corrections
   that retire older decisions instead of each other. Subagent task
   constraints surviving parent compaction are pinned end to end
   (`tests/rot2_subagent_constraints.rs`).
2. Adds production-path benchmark infrastructure: a multicycle adapter that
   drives the real `prepare_compaction -> compact -> append_compaction ->
   SessionManager restore` pipeline (`crates/pi-coding-agent/tests/rot2_multicycle.rs`),
   a `compaction-harness` binary for offline external drivers, a CompactBench
   adapter that runs the real pipeline inside the official runner, an offline
   ContextBench evaluation pipeline, an incremental-variant experiment driver,
   live-model drivers for the R2-E2 multicycle, R2-E3 coding-task and
   R2-E3ADV adversarial-correction experiments, an offline replay
   (R2-E2-RM) that re-measures the ledger fix on the committed live corpus,
   and live-model practice runs of both external benchmarks: a CompactBench
   starter practice run and a ContextBench dev-slice retention run, both
   through the production `compaction-harness` with a GLM 5.3 summarizer.
3. Wires the typed-state v2 ledger into the compaction flow as an opt-in
   `taskState = "typed"` mode (build/merge/verify in `338b920c3`, settings
   routing in `4be9623b0`, integration suite `40bead288`): the
   provenance-aware replacement for the v1 keyword-regex ledger, with a
   budgeted protected block, a deterministic verifier with one bounded repair,
   and unsafe-allocation refusal before any model call. The default stays
   `"off"`; `"prompt"` (v1) is unchanged.
4. Records the experiment results (below) and what they show: repeated
   compaction is a real information-loss mechanism on the production path
   under a lossy summarizer; the v1 ledger mitigates the kinds it captures but
   is unsafe on corrections (live inversion in R2-E2, argued-down dead weight
   in R2-E3ADV); the W11 id/supersession fix removes the measured inversion
   mechanisms on the live corpus offline (R2-E2-RM); and therefore the
   shipped default (`taskState = "off"`) stays correct until the typed arm
   clears its measurement and red-team bars - R2-E5 has since landed
   (15/19 offline vs 14/19 control at the pre-D25 implementation, W29,
   committed E5; 13/19 on the integrated tip, the W46/W47 re-measurements -
   the D25 step boundary correctly retires two Step-scoped episode-classified
   probes; decisions 4/4, evidence 1/4, supersession 2/2 unchanged; verifier
   5/5 both; live A/C clean; see the typed-mode section and the red-team
   wave disclosure) and the default still stays `"off"` pending cross-model
   and longer-session evidence.
78ccdf254 (docs(context-rot): cross-report consistency sweep at the W50 tip)

## Defect -> fix -> test (evidence table)

Sources: `reports/context-rot-r2/BUG-LEDGER.md`, commit messages, the test
files. Statuses are for this branch's head.

| defect | fix (commit) | guard test |
| --- | --- | --- |
| D1 (HIGH): threshold stop hook gated on `settings.enabled` alone, so runtime-disabled auto compaction stopped turns past the trigger with nothing compacting | `0a896e639` extracts one shared predicate `threshold_auto_compaction_ready` consulted by both the hook and the executor | `d1_toggle_off_over_threshold_does_not_stop_the_turn` (`tests/compaction_turn_flow.rs`) |
| D2 (HIGH): a turn crossing the threshold could stop and never resume | verified intact on this base (mid-task resume + queued-prompt paths); pinned by tests, no production change | `d2_interactive_turn_crossing_threshold_compacts_and_continues`, `d2_queued_user_prompt_completes_after_threshold_compaction` (same file) |
| D5 (HIGH): compaction ran as a paid no-op when the summarize window held no new material | `3b08e3f2e` skips the empty window regardless of `previous_summary` (no model call, no turn stop, no appended entry) | `repeated_trigger_with_no_new_material_resolves_to_no_op`, `new_material_after_the_boundary_still_prepares`, `a_no_op_skip_leaves_the_next_real_compaction_boundaries_intact` (`tests/compaction_summary_path.rs`) |
| D6 (HIGH): extension-supplied summaries bypassed validation; an empty hook summary deleted the prefix it should summarize | `289f3c2e` adds `validate_extension_compaction_summary` (the same structural handoff check the model path enforces); `6e1c3a139` applies it at the extension compaction call site (an unused-harness-settings cleanup rides along) | `extension_summary_guard_rejects_empty_files_only_and_unsectioned_summaries` (same file) |
| D8 (MED-HIGH): `migrate_v1_to_v2` dropped the compaction boundary when the legacy index was out of bounds or the session header, so every later compaction re-summarized retained history | `a56631631` falls back to the first entry after the compaction | `migration_keeps_a_durable_boundary_when_first_kept_index_is_out_of_bounds`, `migration_ignores_a_boundary_pointing_at_the_session_header` (`src/core/session_manager.rs`) |
| D9 (MED-HIGH): deadline-fallback boilerplate became the next summarizer's `previous_summary` | `720428967` selects previous prose through a dedicated walk that skips fallback markers and provider checkpoints | `deadline_fallback_boilerplate_is_not_the_next_previous_summary`, `fallback_only_history_yields_no_previous_summary` (`tests/compaction_summary_path.rs`) |
| D10 (MED): branch summaries were built with every tool result removed, so the evidence needed to continue a branch never reached the summarizer | `0e25a7db1` pairs tool results with their assistant calls in the branch-summary input under the same evidence-aware excerpt policy as the main path, and the budget walk keeps the call of a kept result (the "never retry" sub-item remains open) | `branch_summary_input_carries_the_failing_tool_result`, `branch_summary_input_skips_tool_results_with_no_text` (`tests/rot2_branch_summary.rs`) |
| D11 (MED): only the newest harness digest was compared and superseded digest turns were never pruned, so k harness changes left k digest user turns (k-1 stale) in the provider context | `30588707d` prunes superseded digest customs from the live provider context on every digest turn (the durable transcript keeps the full history) | `k_harness_changes_leave_exactly_one_digest_in_the_provider_context`, `compaction_keeps_the_pruned_provider_context_digest_clean` (`tests/rot2_harness_digest.rs`, landed with `d9ceea230`) |
| D12 (MED): memory-recall text reached the provider as unfenced, instruction-shaped user turns (stored project data could read as fresh user direction) | `021678796` fences `MEMORY_RECALL_TYPE` custom text as `<memory_data>` at `convert_to_llm` time, mirroring the harness-digest fence (soft-fence residual documented) | `convert_to_llm_fences_memory_recall_text_but_not_other_custom_types` (`src/core/messages.rs`) |
| D13 (MED): fail-closed recall paths were silent (foreign-only corpus, short-skip space, stale-source) | `157316268` surfaces one rate-limited warning with drop counts when hits existed but nothing rendered; partial skips stay silent | `foreign_only_corpus_fails_closed_and_surfaces_the_reason`, `budget_short_skip_fails_closed_and_surfaces_the_reason`, `partial_recall_skips_stay_silent` (`src/core/memory/search.rs`) |
| D14 (MED): the read-files inventory in compaction details was always empty | `439808412` records reads structurally from any tool call with `path`/`paths` arguments (`edit` stays the one name-based write rule) | `non_edit_path_tool_calls_populate_the_read_inventory`, `compact_persists_a_non_empty_read_files_inventory` (same file) + `path_arguments_of_non_edit_tool_calls_record_reads` (`src/core/compaction/utils.rs`) |
| D16 (MED): user-attached images cost zero tokens in `estimate_tokens` while `find_cut_point` charged 4096 chars | `fa4ccc8f2`/`0bb973e43` introduce the shared `IMAGE_BLOCK_CHARS` (4096 chars = 1024 tokens) across all estimator paths; `4dea91d72` aligns the kernel-parity reference | `rot2_user_image_blocks_cost_tokens_d16` (`tests/rot2_multicycle.rs`), `image_blocks_cost_4096_chars_1024_tokens` (`request_tokens.rs`), `compaction_kernel_parity` suite (23/23) |
| D17 (LOW-MED): the context estimate ignored the system prompt and tool schemas | `fa4ccc8f2` adds `request_tokens.rs` (`estimate_request_tokens`, `RequestTokenEstimate`); `0bb973e43` carries the split on `ContextUsageEstimate` and adds the parts on the no-usage fallback path only (usage-anchored path keeps provider totals); `2200211fe` documents the tray's inherited underestimate | `rot2_request_level_estimate_adds_system_prompt_and_tools_d17` (`tests/rot2_multicycle.rs`), `message_list_estimates_understate_request_utilisation` (`src/core/context_health.rs`), `empty_inputs_score_zero`...`total_is_exactly_the_sum_of_its_parts` (`request_tokens.rs`) |
| W9-F2/F4/F5 (v1 ledger ids and supersession): ids renumbered per merge so a retired id could be re-issued and silently deleted; corrections superseded each other while the corrected decisions stayed current | `1754a3e1b` content-derived ids (`C-a1b2c3d4`, sha256 of the dedupe key), correction carriers excluded as supersession targets, newest correction wins the same target; old positional ids still deserialize | `w9_f4_corrections_supersede_the_older_decisions_not_each_other`, `w9_f2_a_late_decision_reusing_a_retired_id_is_not_deleted`, `w9_f2_and_f5_late_constraints_reusing_retired_ids_survive_with_anchors`, plus id-stability, correction-precedence and old-id interop cases (7 total, `tests/rot2_ledger_fixes.rs`) |
| D21 (MED, new, found post-W11): with the churn fixed, `CONSTRAINTS_CAP=12` omitted the four corrections from the rendered ACTIVE list on the hard scenario (visible only as SUPERSEDED replacement texts; 9 of 20 constraint lines also cap-omitted; strict containment 11/20) | `a9035ce8a` exempts correction carriers from the render caps and priority-orders them ahead of plain items (W32) | 7 tests in `tests/rot2_render_cap.rs` (priority order, exemption beyond the cap, hard-corpus shape, superseded-replacement link, byte-equal no-corrections render) + the offline replay re-measure: strict containment 20/20 (`0b824f358`) |
| harness F1/F4/F5: the harness binary forked a session per invocation, read 0 tokens, lost appends on error paths | `49f473d8d` (`continue_recent` session persistence, chars/4 usage anchors, flush-on-append) | `probe_harness_behavior.py` + the 13 adapter checks (`research/context_rot_r2/compactbench_adapter/`) |

Fixed later on this branch and integrated here (the W30/W32 recovery wave):
D18 (bounded model-visible notice when auto-compaction fails, `8c558dd7b`),
D19 (live disclosure lists capped at 200 entries with a dropped-count marker,
`4769f42f4`), the D20 wire-request contract (byte-exact pin, `e44faff08`;
residue documented - the text form cannot carry image blocks) and D21 above.
Still open (tracked in `BUG-LEDGER.md`): D7 (deferred with rationale), D10's
"never retry" sub-item, D20's documented residue, and the v1 render's
keyword-less capture gap W9-F1 (typed v2 removes it by classifying on role).

The deeper v1 supersession-semantics rework that was drafted on
`rot2/w16-supersession` is superseded by the typed-mode red-team fixes below;
what this PR claims for the v1 ledger is exactly the W11 fixes the offline
re-measure validated (below). The typed-arm end-to-end measurement (R2-E5,
`59832805f`) landed after the first edition of this body; its numbers are
summarized in "What is NOT claimed" below and carried in full by
`research/context_rot_r2/live/e5/README.md` and FINAL-REPORT section 4.4.
78ccdf254 (docs(context-rot): cross-report consistency sweep at the W50 tip)

## The typed-state v2 mode (opt-in, wired)

The module landed first as a pure library (`c948905a3`); it is now wired into
the production compaction flow:

- Mode resolution: `compaction.taskState` accepts `"typed"` alongside
  `"off"`/`"prompt"` through `resolve_task_state_mode` (setting or the
  `PRIME_AGENT_COMPACTION_TASK_STATE` env; unrecognized values fail safe to
  `"off"`; the default stays `"off"`) - `338b920c3` - and the settings path
  routes through the same resolver so a configured `typed` reaches the kernel
  - `4be9623b0`. `"prompt"` keeps the v1 keyword ledger unchanged.
- Build and merge: `prepare_compaction` builds via `build_typed_state` over
  the leaving window and merges the result with the previous compaction's
  persisted ledger (deserialized from `details.typedState`, a new field that
  never overloads the v1 `taskState` payload), carried on `CompactionPreparation`.
- Budget: the protected block is the budgeted allocation (`render_allocation`
  of `allocate`) with the hot-state budget derived from the summary output
  budget: `min(2048, floor(summary_budget/4))` tokens
  (`typed_hot_state_budget_tokens`), so the block always fits alongside the
  summary the same request produces. An unsafe allocation (mandatory material
  dropped) fails the episode before any model call through the existing `Err`
  channel, with the overflow detail (ids, kinds, budget) in the error text.
- Verifier: after the summary passes structural `validate_summary`,
  `verify(summary, ledger)` gates acceptance. A failure that is only missing
  required items gets one bounded, deterministic repair (missing protected
  lines appended verbatim to their matching summary sections, no model call)
  and is re-verified; any other failure, or a still-failing repair, is a
  summary-validation failure. The outcome persists as
  `details.typedVerification` (passed/repaired with finding counts).
- Parity: native provider compaction and the deadline fallback keep v1
  behavior (no typed verification); the fallback still persists the ledger so
  the next cycle merges it.
- Guards: `tests/rot2_typed_integration.rs` (`40bead288`, nine tests) drives
  the real pipeline over real `SessionManager` JSONL persistence: mode
  resolution and fail-safe, the budget formula, `prepare_compaction`
  classifying cargo-test lines as `VERIFIED_TOOL_RESULT` and subagent tool
  reports as `SUBAGENT_STATE`, the protected block and its copy instruction in
  the captured summarizer request, the `details.typedState` round-trip merging
  by record id, the unsafe-allocation refusal, the deterministic repair, a
  superseded-decision-presented-as-current summary failing verification, and
  v1 interop (old `details.taskState` entries deserialize; `"prompt"` mode
  works unchanged end to end).
- Known boundary (deliberate, pinned by the suite): custom `agent_message`
  entries are not classified (`entry_role_text` reads only `Message` entries),
  so a child-agent report is not retained as `SUBAGENT_STATE` by the committed
  module.

### Adversarial red-team wave (W35): eight defects found, all fixed

After the green integration, a dedicated adversarial self-review of the typed
mode found eight defects that the positive tests missed. Each was reproduced
by a failing-first test committed in
`crates/pi-coding-agent/tests/rot2_typed_adversarial.rs` (written red under
`#[ignore = "Dnn: ..."]`, then un-ignored by its fix; 24 tests green, zero
ignored, at this head):

| # | one-line finding | fix |
| --- | --- | --- |
| D22 (MED) | supersession rendered with the retirement direction inverted (the retired record shown as superseding its replacer) | `51c9a6a59` (W38) |
| D23 (MED) | bracketed ticket tokens in ordinary user text collided with the verifier's reference grammar, failing the episode permanently | `891702c32` (W37) |
| D24 (MED) | tool-derived record ids recomputed from evolving text, stranding every reference earlier summaries carried | `891702c32` (W37) |
| D25 (MED-HIGH) | step-scoped pending actions stayed CURRENT forever, growing ledger and summaries until mandatory material exceeded the hot-state budget and compaction failed permanently | `a3997b5d6` (W36) |
| D26 (MED) | a malformed `details.typedState` payload silently dropped every durable record below the boundary (now salvaged field-wise with a persisted warning) | `51c9a6a59` (W38) |
| D27 (LOW) | all-caps fabricated "verified" claims evaded the fabrication check | `51c9a6a59` (W38) |
| D28 (LOW-MED) | native provider compaction returned `typed_state: None`, resetting the durable chain | `1e6e5ff9d` (W39; pinned in `tests/rot2_native_typed_chain.rs`) |
| D29 (MED-HIGH) | supersession pairing was token-overlap-only: a zero-overlap correction retired nothing (the adversarial no-anchor case this campaign set out to fix) | `4315f9154` (W41), live-verified: E3ADV C1 5/5 cycles retired with the pairing link rendered |

Disclosed, not claimed away: the D30 classifier gap (an unprefixed decision
in plain user text classifies as step-scoped low-priority material and
retires at the first merge; the smoke scenario states it through the
assistant entry, `7bec70f09`) and the W41 cycle-5 budget caveat (the E3ADV
tier-8 history line overflows the fixed 800-token hot budget; the pairing
line still renders and verification passes). Full findings and evidence:
`BUG-LEDGER.md` section 4.

## Experiment results

Category labels (defined in `reports/context-rot-r2/BASELINE.md`, never mixed
within one table):

- SYNTHETIC-DIAGNOSTIC - deterministic fixture, no model calls; measures
  pipeline mechanics, not model quality.
- PRODUCTION-PATH - produced by the real Rust compaction pipeline in this
  repo, not a Python mirror.
- PRODUCTION-PATH-MIRROR - a Python port of the current Rust ledger replayed
  over a committed live corpus; validated byte-for-byte against the stored
  fixture before use; no model calls.
- CUSTOM-OPTIMUS - official external benchmark infrastructure with custom
  Optimus adapters/settings (mock provider, practice suites, custom scorer);
  not comparable to official scores.
- LIVE-MODEL - measured with a real model endpoint (GLM 5.3, one DGX K3
  endpoint, temperature 0) as summarizer and probe answerer; single-model
  evidence, not a cross-model claim.

### R2-E1 - multicycle retention, default vs taskState (SYNTHETIC-DIAGNOSTIC, PRODUCTION-PATH)

19 probes (8 constraints, 4 decisions, 4 evidence, 2 corrections, 1 subagent
artifact), 5 compaction cycles, deterministic lossy summarizer, zero model
calls (`research/context_rot_r2/runs/r2-e1-multicycle.*`).

| arm | retention | constraints | decisions | evidence | supersession | subagent | mean req chars |
| --- | --- | --- | --- | --- | --- | --- | --- |
| default (`taskState = "off"`) | 0/19 | 0/8 | 0/4 | 0/4 | 0/2 | 0/1 | 14 765 |
| taskState (`taskState = "prompt"`) | 14/19 | 8/8 | 4/4 | 0/4 | 2/2 | 0/1 | 18 429 |

What this shows: five repeated compactions with an even mildly lossy summarizer
lose every unprotected anchor (D4 confirmed on the production path); the v1
ledger fully protects the kinds it captures (14/14) at about +916 tokens per
cycle, but is blind to tool-result evidence (regex gap N1) and subagent custom
entries (N2). The 8-cycle, 2-seed extension (E-INC, `research/context_rot_r2/
incremental/`) confirms: default decays to 0/19 after two compactions and stays
there; taskState holds 14/19 flat through 8 cycles; runs are deterministic and
path-independent; the ledger costs +995 tokens/cycle steady state.

### R2-E2 - live-model multicycle (LIVE-MODEL, GLM 5.3 only)

Same pipeline mirrored in Python and validated byte-for-byte against the stored
R2-E1 fixture, then run with the real GLM 5.3 endpoint as summarizer AND probe
answerer (`research/context_rot_r2/live/`, 581 wire calls, zero probe
extraction failures).

| scenario | arm | probe records | correct |
| --- | --- | --- | --- |
| base (19 probes) | default | 95 | 95 (100%) |
| base | taskState | 95 | 89 (93.7%) |
| hard (37 probes) | default | 111 | 111 (100%) |
| hard | taskState | 111 | 111 (100%) |

Headline finding: in the base scenario the v1 ledger INVERTS the user's
correction. The user says "forget that; use architecture B ... the earlier A
decision is superseded"; the ledger's token-overlap supersession rule pairs the
two corrections with each other, renders them under SUPERSEDED (history) while
the pre-correction "architecture A" decision stays current, and the summarizer
faithfully propagates "use architecture A; the B correction is superseded" from
cycle 3 onward. The default arm handles the same correction correctly at every
cycle. The hard scenario does not reproduce the inversion only because the same
defective rule mis-fires as ambiguity-plus-omission there, which the model
recovers from: correctness under the v1 ledger is luck of how the regex pairing
lands, not a property the ledger guarantees. R2-E2 measured the pre-W11 ledger;
the W11 fix is re-measured offline on this corpus below (R2-E2-RM).

### R2-E3 - coding-task action probes (LIVE-MODEL, GLM 5.3 only)

Five coding scenarios x 2 arms x 5 compaction cycles with action-level probes
(next file to edit under a constraint, corrected approach, boundary-adjacent
exact error, acceptable-set exclusion, post-restart approach), 3 reps per
point, majority scoring, one simulated process restart after cycle 3
(`research/context_rot_r2/live/e3/`; 209 wire calls = 155 probes + 53
summarizer records, 3 of them length-ladder retries, + 1 pong).

| arm | cycle points | correct | strict | extraction failures |
| --- | --- | --- | --- | --- |
| default | 50 | 50 (100%) | 50 | 0 |
| taskState | 50 | 50 (100%) | 50 | 0 |

Anchor retention 1/1 at every cycle on both arms (including the exact
failing-test assertion); the TASK-F serialize -> reload round-trip left the
restored provider context byte-identical on both arms. What this shows: no
measurable action-level rot at this scale with the real summarizer, on either
arm. Two mechanisms behind the taskState arm's clean sheet: the corrections in
these scenarios happen to pair correctly under the token-overlap rule (pairing
luck, not design - the adversarial variant below breaks it), and the exact
error survived through the summarizer's verbatim-anchor discipline while the
ledger's VERIFIED EVIDENCE section never captured it (the evidence regex takes
the first matching line of a tool result; the assertion below it is invisible).

### R2-E3ADV - adversarial corrections with no shared anchor (LIVE-MODEL, GLM 5.3 only)

Three adversarial correction families (semantic swap, numeric correction,
retraction-only) where the correction shares no anchor token with the decision
it replaces (supersession score 0-2 < the pairing threshold 3); predictions
pre-registered before any live call, and the offline selftest proves the
zero-pairing property against the real mirror ledger (superseded == [] at
every cycle). 2 arms x 3 families x 5 cycles, 3 reps
(`research/context_rot_r2/live/e3adv/`; 124 wire calls, 0 HTTP retries, all
summarizers within the 8192 budget).

| arm | cycle points | correct | strict | extraction failures |
| --- | --- | --- | --- | --- |
| default | 15 | 15 (100%) | 15 | 0 |
| taskState | 15 | 15 (100%) | 15 | 0 |

The mechanism, verified in `raw-calls.jsonl`: the ledger retired NOTHING. The
stale decision stayed DECISIONS-current at every cycle while the render header
promised "A SUPERSEDED item is history" with no superseded items to show. GLM
5.3 resolved the contradiction in all 15 taskState summarizer outputs by
annotating the stale item as history in its own words ("Treat as history
only", "superseded in practice"). Correctness is the summarizer's achievement
AGAINST the ledger, not a property of it, and the dead weight costs +1.5k-1.9k
request chars per cycle (mean summarizer request 16.8k-17.0k chars vs
15.1k-15.3k default). Combined with R2-E2: the v1 ledger on corrections is
either a coin flip that can invert user intent (shared-anchor mispairing) or
dead weight the model must argue down (no shared anchor). In neither case does
it do the correction's work.

### R2-E2-RM - offline re-measure of the W11 fix on the live corpus (PRODUCTION-PATH-MIRROR)

A Python port of the post-W11 Rust ledger (`w11_port.py`, commit `1754a3e1b`)
replayed over the committed R2-E2 raw-call corpus, no model calls
(`research/context_rot_r2/live/remeasure/`). Gate: the pre-W11 mirror replay
byte-matches every corpus summarizer request and reproduces every cycle field
of the committed results.

- base (the live-inversion scenario): clean at every cycle. Pre-W11 both
  corrections were inverted at every cycle (mutual supersession pair); the
  live failure reproduced exactly by the pre-W11 replay. Post-W11: corrections
  ACTIVE and rendered (12/12 within cap), the superseded architecture-A
  decisions retired to SUPERSEDED with the correction as replacement, the
  constraint churn gone.
- hard: structurally fixed (mutual pair gone, all four A decisions retired,
  17 spurious pairs -> 4 correct pairs) but a new render-level defect appears:
  26 constraints stay active and `CONSTRAINTS_CAP=12` omits the four
  corrections from the rendered ACTIVE list (visible only as SUPERSEDED
  replacement texts; 9 of 20 constraint lines also cap-omitted; strict
  containment 11/20). Filed as D21 in the table above.
- secondary (W11-independent): `EVIDENCE_RE` never matched this corpus's
  evidence shapes, so VERIFIED EVIDENCE was empty in every render pre and
  post; the live evidence correctness came from the summary chain.

### TypeCompact reproduction (SYNTHETIC-DIAGNOSTIC, external reference)

Deterministic offline reproduction of the CIKM 26 Knowledge Triage
multiturn-stability experiment on their pinned artifact suite
(`research/context_rot_r2/repro/typecompact_repro*.json`): constraint recall
1.000 -> 0.841 by cycle 5 (0.848 on the 197-config full suite) while the
truncation baselines cliff to 0.007-0.028 - external confirmation that pinning
must-keep material outside the summarizer survives repeated compaction. Their
committed LLM-arm numbers are carried as labelled reference-only blocks, never
claimed as ours.

### CompactBench practice suites (CUSTOM-OPTIMUS, PRODUCTION-PATH pipeline)

Full public practice suites (starter: 4 templates x 5 cases; elite_practice:
20 x 5) with 3 drift cycles on nine arms through the committed adapter and
harness binary (`research/context_rot_r2/compactbench_adapter/SUITE-RESULTS.md`).
All quality scores are degenerate by construction (mock eval provider returns a
constant; every arm scores identically); these runs validate plumbing only.
The mock-independent observables separate the arms: zero method warnings
across all 1 920 custom-arm cycles (every `compact()` landed a valid cut),
task-state artifacts are the largest (compression 0.48x starter / 0.74x elite,
plain 0.55x / 0.90x, oracle 0.78x / 0.79x, echo 0.83x / 1.12x, built-ins
collapse to the null floor), and per-cycle token trajectories confirm
delta-append mode engaged.

### CompactBench live practice run (CUSTOM-OPTIMUS + LIVE-MODEL)

A model-backed practice run on the public starter suite (8 of 20 cases per
arm, one live draw per case): GLM 5.3 as BOTH the summarizer - behind the
Rust `compaction-harness` binary on the production path, via a new
env-configured HTTP provider that is offline by default (`6ea85dc10`) - and
the task/eval model behind the stock runner
(`research/context_rot_r2/compactbench_adapter/live/`; 676 HTTP requests
against a 768 ceiling, one fixed-and-resumed provider incident). NOT a
leaderboard number and not comparable to any 1.0.0-scored run. The live run
does what the mock could not - the arms' quality separates:

| arm | LIVE overall | drift_res | constraint_ret | compression |
| --- | --- | --- | --- | --- |
| optimus-rust-task-state | 0.6844 | 0.990 | 0.800 | 0.67x |
| echo-first-n | 0.6531 | 0.833 | 0.800 | 1.60x |
| full-transcript-oracle | 0.5781 | 0.721 | 0.650 | 0.92x |
| optimus-rust-plain | 0.4906 | 0.757 | 0.650 | 0.72x |

The two Rust arms differ by 0.194 overall, driven by the ledger-mediated
checks (`recall_latest_decision` 0.625 vs 0.125, assign checks 0.375 vs
0.125, drift resistance 0.99 vs 0.76, contradiction rate 0.094 vs 0.172).
GLM writes tighter summaries than the lossy fixture (task-state compression
0.46x mock -> 0.67x live on identical cycle-0 transcripts), and the verbatim
oracle is not the ceiling under live eval (0.578). Single-draw runs are not
bit-reproducible even at temperature 0, so with n = 8 per arm only the
Rust-arm gap is treated as signal; the pre-registered extension amendment did
not trigger.

### ContextBench offline pipeline (CUSTOM-OPTIMUS)

The committed pipeline (`research/context_rot_r2/contextbench/`): parquet
loading with sha256 verification of the 500-instance verified subset of the
pinned upstream commit, a repo-level dev/holdout split (308/192 instances, no
repo overlap, byte-reproducible manifest), the upstream gold-context metric
layer mirrored function-by-function with citations, and a matched A/B harness
with exact sign test and tie-corrected Wilcoxon. Arms A/B are scaffolded for
recorded trajectories; arms C (typed state) and D (source-grounded
reconstruction) emit blocked records only.

### ContextBench live practice run (CUSTOM-OPTIMUS + LIVE-MODEL)

26 paired dev instances (a deterministic blake2b-seeded slice of the 110
size-window dev candidates; 7 languages, 15 repos; the holdout untouched),
2 arms x 4 append+compact cycles through the real `compaction-harness`
binary with a live GLM 5.3 summarizer, evaluated by the offline-validated
mirror (`research/context_rot_r2/contextbench/live/`). Arms: A = baseline,
B = task-state-ledger (`prompt`; a documented substitution - the offline
arm B, jev-relevance, is not expressible through the compaction surface).
No repository checkouts exist under the offline waiver, so the trajectories
are synthetic explorations built from the gold annotations and the metrics
measure RETENTION of gold context through compaction, not free-form agent
exploration; `patch_success` stays UNAVAILABLE in every record.

| metric | A baseline | B task-state-ledger | delta B-A | sign p | Wilcoxon p |
| --- | --- | --- | --- | --- | --- |
| final file coverage | 0.974 | 0.974 | +0.000 | 1.000 | 1.000 |
| final line coverage | 0.852 | 0.916 | +0.069 | 0.455 | 0.196 |
| final line precision | 0.320 | 0.586 | +0.097 | 0.004 | 0.003 |

Gold file references survive compaction almost fully in both arms (file
coverage 0.974); gold LINE retention separates them (line precision 0.586 vs
0.320, sign p = 0.004; by raw counts arm B ends 69% gold / 31% distractor
lines vs 47% / 53% in arm A). The parsed ledger itself populated in only 34
of 104 arm-B cycles, so the observed effect is prompt-level. Summarizer
cost: 439 requests for 208 cycle attempts, mean latency 20.4 s, 47
max_tokens ladder climbs, 0 error calls.

## What is NOT claimed

- No official benchmark scores. Every CompactBench run - mock or live - uses
  the public practice suites with custom adapter methods (the live run: 8 of
  20 starter cases per arm, one draw); the ContextBench live practice run
  measures gold-context retention on synthetic trajectories, not free-form
  agent exploration, and `patch_success` is UNAVAILABLE in every record.
  Nothing here is leaderboard-comparable.
- No cross-model claims. The only live-model evidence is GLM 5.3 on one DGX K3
  endpoint, temperature 0, with the documented repetition plans
  (`research/context_rot_r2/live/README.md`, `live/e3/README.md`,
  `live/e3adv/README.md`, `compactbench_adapter/live/README.md`,
  `contextbench/live/README.md`). The CompactBench live run is a single draw
  per case; the ContextBench live run is one paired dev slice.
- R2-E1/E-INC numbers are pipeline mechanics under a deliberately lossy
  deterministic fixture. They bound what the pipeline alone protects; they are
  not predictions about any specific model.
- RepoQA, SWE-bench and Terminal-Bench: not executed in this campaign.
- The typed-arm measurement exists (R2-E5, `59832805f`: offline three-arm
  adapter suite typed 15/19 vs taskState v1 14/19 vs default 0/19 at the
  pre-D25 implementation, W29, committed E5 - 13/19 on the integrated tip,
  the W46/W47 re-measurements: the D25 step boundary correctly retires two
  Step-scoped episode-classified probes; decisions 4/4, evidence 1/4,
  supersession 2/2 unchanged; verifier 5/5 both; live TASK-A/C 5/5 with
  zero verifier repairs, E3ADV C1 correct 5/5) but it is single-model,
  practice-scale and, on the decisive adversarial cell, only correct
  after the D29 provenance pairing fix; TASK-E halted live at cycle 2
  pre-fix (D24) and completes 5/5 cycles on the integrated tip (E5-RM,
  W47). The default stays `"off"` pending cross-model and longer-session
  evidence (the post-wave findings D31-D33 open).
78ccdf254 (docs(context-rot): cross-report consistency sweep at the W50 tip)
- The W11 ledger fix is validated offline on the live corpus (R2-E2-RM), not
  by a fresh live run; the model-dependent prediction (live corrections
  answered correctly at every point) is stated as a prediction in the
  experiment ledger, not as a measurement.
- The v1 ledger's deeper supersession semantics are not reworked in this PR
  (the drafted `rot2/w16-supersession` line was superseded by the typed-mode
  fixes); D18/D19/D20 and D21 are fixed on this branch and disclosed above.
78ccdf254 (docs(context-rot): cross-report consistency sweep at the W50 tip)
- Not a measurement of the shipped binary for R2-E2/E3/E3ADV: those runs are
  a Python mirror of the Rust path, fixture-validated byte-for-byte against
  the stored R2-E1 result, with the documented deviations in each README.

## Testing

Workspace-wide verification (the campaign had not run one before):
`cargo check --workspace --all-targets` (Windows git-bash, rustc 1.98.1,
isolated target dir) finishes with 0 errors and 908 warnings at the
integration head `f8ad670c8`, with an identical count at `4be9623b0` (the
tip before the D11/W26/W27 landings; the head was verified from a clean
`git archive` extraction); the same command at the base `6000d6bc1` records
903 warnings. The delta is 4 distinct diagnostics, all in files this branch
adds: two unused imports (one in the `compaction-harness` binary, one in
the research adapter test) and two never-read struct fields in
`tests/rot2_multicycle.rs`; every pre-existing target reports the identical
warning count as the base, with pre-existing warnings only shifted by
inserted lines. The campaign's code footprint is one crate: `git diff
--name-only origin/main..f8ad670c8` lists 29 files under
`crates/pi-coding-agent` plus `.changes/`, `reports/context-rot-r2/` and
`research/context_rot_r2/` only - no other workspace crate is touched, so no
other crate's tests were re-run.

Focused gate table - the last committed full pass ran at the `4b0add723` tip
(see `reports/context-rot-r2/REGRESSION-GATES.md` for the method; the
final-tip matrix has since landed there: the W25 pass at `29f1f0c92` with the
W43 parity-fix addendum):

| suite (command filter) | result | notes |
| --- | --- | --- |
| `--lib compaction` | 257 passed, 2 failed, 3 619 filtered out | the two failures are the documented environment pair below; not r2 regressions |
| `--lib typed_state` | 60 passed | the typed module's unit tests |
| `--lib migration` | 20 passed | includes both D8 regression tests |
| `--test rot2_multicycle` | 4 passed, 1 ignored | ignored = the `#[ignore]`d research entry point `rot2_production_multicycle` (scenario/result driven by env); passing: smoke multicycle, D16, D17, lossy-fixture |
| `--test compaction_turn_flow` | 3 passed | D1 reproduction + both D2 guards |
| `--test compaction_summary_path` | 8 passed | D5 (3), D6 (1), D9 (2), D14 (2) guards |
| `--test compaction_recovery` | 9 passed | adjacent gate, untouched |
| `--test compaction_model_threshold` | 10 passed | adjacent gate, untouched |
| `--test compaction_fidelity` | 1 passed | adjacent gate, untouched |
| `--test compaction_summary_guard` | 2 passed | adjacent gate, untouched |
| `--test compaction_kernel_parity` | 23 passed | reference updated for the shared D16 image cost (`4dea91d72`) |

Suites added since that pass, all with guards named in the defect table above:
`rot2_ledger_fixes` (W11), `rot2_branch_summary` (D10),
`rot2_subagent_constraints`, `--lib context_health_calibration`,
`rot2_typed_integration` (the typed wiring, nine tests) and
`rot2_harness_digest` (D11); their numbers belong to the final-tip gate pass
that `REGRESSION-GATES.md` records.

Python suites at this tip (offline, system python 3.11):

| suite | result |
| --- | --- |
| `research/context_rot_r2/tests/` (unittest) | 5 passed |
| `research/context_rot_r2/incremental/tests/` (unittest) | 6 passed |
| `research/context_rot_r2/live/e3/selftest.py` (unittest) | 13 passed |
| `research/context_rot_r2/live/e3adv/e3adv_selftest.py` (unittest) | 15 passed |
| `research/context_rot_r2/contextbench/tests/` (pytest, system python) | 124 passed, 3 skipped (the 3 skips are the `RealParquetTests` needing `CB_REFS_ROOT`) |
| same, integration tests on (cb-venv unittest, `CB_REFS_ROOT` = the pinned ContextBench checkout) | 127 passed, 0 skipped |
| `research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py` (cb-venv, `HARNESS` = the binary built from this branch) | 13/13 checks passed (at the `4b0add723` pass; the adapter package is byte-identical since, and the live-run report re-verified 13/13 at its head) |

Environment: Windows git-bash, rustc 1.98.1 (48a229cea 2026-09-01), isolated
cargo target dir; the `compaction-harness.exe` used by the adapter checks was
built from the branch tip of that pass. The `#[ignore]`d research entry points
were not run (experiment drivers; the committed runs under
`research/context_rot_r2/runs/` and `live/` are the campaign data).

Known environment-bound failures (pre-existing on the base, documented at
`reports/context-rot/REGRESSION-GATES.md` and r2's section 5): the
`enabled_skill_is_importable` test needs `uv` on the test child's PATH (absent
on this host), and the painted-frame tray test is scheduling-sensitive and
passes serialized. Neither file is touched by this campaign.

## Reviewer notes

- The defaults are unchanged: `taskState` keeps its `"off"` default (values:
  `"off"` | `"prompt"` | `"typed"`; env `PRIME_AGENT_COMPACTION_TASK_STATE`).
  The typed mode is reachable by opting in through settings or the env
  (`resolve_task_state_mode` fails safe to `"off"` on unknown values);
  `"prompt"` (v1) behaves exactly as before; the `compaction-harness` binary
  is opt-in tooling.
- The typed-state wiring to check first, in order: `resolve_task_state_mode`
  and `typed_hot_state_budget_tokens` in `src/core/compaction/compaction.rs`
  (mode + budget), the `typed_state` field on `CompactionPreparation`
  (`338b920c3`), `verify` + the deterministic repair on the compact path, the
  `details.typedState`/`details.typedVerification` persistence, and
  `tests/rot2_typed_integration.rs` (`40bead288`) for the end-to-end guards.
- The E3ADV finding to keep in mind when weighing the v1 ledger: with no
  shared anchor, the ledger keeps the stale decision current and GLM argues
  it down in its own words - correctness against the ledger, not because of
  it. The W11 fixes remove the measured inversion mechanisms (R2-E2-RM,
  offline on the live corpus); the deeper v1 semantics rework was superseded
  by the typed-mode red-team fixes (see the red-team wave table above).
78ccdf254 (docs(context-rot): cross-report consistency sweep at the W50 tip)
- D17 detail: `ContextUsageEstimate.system_prompt_tokens` /
  `tool_schema_tokens` are non-zero only on the no-usage fallback path - the
  usage-anchored path keeps 0.0 to avoid double-counting provider-reported
  usage.
- D16 detail: the shared constant is `IMAGE_BLOCK_CHARS` (4096 chars = 1024
  tokens at chars/4) in `src/core/compaction/request_tokens.rs`.
- Where to look, in order: `src/core/agent_session.rs`
  (`threshold_auto_compaction_ready`, the D11 digest prune), `src/core/session_manager.rs`
  (`migrate_v1_to_v2`), `src/core/compaction/compaction.rs` (empty-window
  guard, `previous_summary_prose`, `validate_extension_compaction_summary`,
  estimator paths, typed wiring), `src/core/compaction/utils.rs` (read-files
  inventory), `src/core/compaction/request_tokens.rs`,
  `src/core/compaction/typed_state.rs`,
  `src/core/compaction/branch_summarization.rs` (D10),
  `src/core/messages.rs` (D12), `src/core/memory/search.rs` (D13),
  `src/core/context_health_calibration.rs`, the new and updated test files
  (`tests/rot2_multicycle.rs`, `tests/rot2_typed_integration.rs`,
  `tests/rot2_ledger_fixes.rs`, `tests/rot2_branch_summary.rs`,
  `tests/rot2_subagent_constraints.rs`, `tests/rot2_harness_digest.rs`,
  `tests/compaction_turn_flow.rs`, `tests/compaction_summary_path.rs`,
  `tests/compaction_kernel_parity.rs`), `src/bin/compaction_harness.rs`,
  then `research/context_rot_r2/` and `reports/context-rot-r2/`.
