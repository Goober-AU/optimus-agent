# Context rot iteration 2: compaction defect fixes, production-path benchmarks, typed-state v2 module

Branch: `improve/context-rot-iteration-2` (base `6000d6bc1`, v0.1.36 + #181/#182).
Campaign reports live in `reports/context-rot-r2/`; every number below cites a
committed artefact. This body describes the current tip; the integrator adjusts
if later commits land before merge.

## Summary

Iteration 1 (`reports/context-rot/FINAL-REPORT.md`) measured compaction policies
through Python mirrors of the production prompts. This PR is its follow-up and
does four things:

1. Fixes seven audited compaction defects (D1, D5, D8, D9, D14, D16, D17),
   adds the D6 extension-summary guard (the `agent_session.rs` call-site wiring
   is pending), and pins the D2 continuation contract with regression tests.
2. Adds production-path benchmark infrastructure: a multicycle adapter that
   drives the real `prepare_compaction -> compact -> append_compaction ->
   SessionManager restore` pipeline (`crates/pi-coding-agent/tests/rot2_multicycle.rs`),
   a `compaction-harness` binary for offline external drivers, a CompactBench
   adapter that runs the real pipeline inside the official runner, an offline
   ContextBench evaluation pipeline, an incremental-variant experiment driver,
   and a live-model driver for the R2-E2 experiment.
3. Adds the typed-state v2 module
   (`crates/pi-coding-agent/src/core/compaction/typed_state.rs`): the
   provenance-aware replacement for the v1 keyword-regex task-state ledger. It
   is a pure library module with its own tests; it is NOT yet wired into the
   compaction flow (the wiring is the next step).
4. Records the R2-E1 and R2-E2 experiment results (below) and what they show:
   repeated compaction is a real information-loss mechanism on the production
   path, the v1 ledger mitigates it for the kinds it captures but can INVERT a
   user correction under a live model, and therefore the shipped default
   (`taskState = "off"`) is correct and the typed-state v2 path is the
   replacement.

## Defect -> fix -> test (evidence table)

Sources: `reports/context-rot-r2/BUG-LEDGER.md`, commit messages, the test
files. Statuses are for this branch's tip.

| defect | fix (commit) | guard test |
| --- | --- | --- |
| D1 (HIGH): threshold stop hook gated on `settings.enabled` alone, so runtime-disabled auto compaction stopped turns past the trigger with nothing compacting | `0a896e639` extracts one shared predicate `threshold_auto_compaction_ready` consulted by both the hook and the executor | `d1_toggle_off_over_threshold_does_not_stop_the_turn` (`tests/compaction_turn_flow.rs`) |
| D2 (HIGH): a turn crossing the threshold could stop and never resume | verified intact on this base (mid-task resume + queued-prompt paths); pinned by tests, no production change | `d2_interactive_turn_crossing_threshold_compacts_and_continues`, `d2_queued_user_prompt_completes_after_threshold_compaction` (same file) |
| D5 (HIGH): compaction ran as a paid no-op when the summarize window held no new material | `3b08e3f2e` skips the empty window regardless of `previous_summary` (no model call, no turn stop, no appended entry) | `repeated_trigger_with_no_new_material_resolves_to_no_op`, `new_material_after_the_boundary_still_prepares`, `a_no_op_skip_leaves_the_next_real_compaction_boundaries_intact` (`tests/compaction_summary_path.rs`) |
| D6 (HIGH): extension-supplied summaries bypassed validation; an empty hook summary deleted the prefix it should summarize | `289f3c2e` adds `validate_extension_compaction_summary` (the same structural handoff check the model path enforces). NOTE: the `agent_session.rs` extension call site does not apply it yet; that wiring is the remaining step | `extension_summary_guard_rejects_empty_files_only_and_unsectioned_summaries` (same file) |
| D8 (MED-HIGH): `migrate_v1_to_v2` dropped the compaction boundary when the legacy index was out of bounds or the session header, so every later compaction re-summarized retained history | `a56631631` falls back to the first entry after the compaction | `migration_keeps_a_durable_boundary_when_first_kept_index_is_out_of_bounds`, `migration_ignores_a_boundary_pointing_at_the_session_header` (`src/core/session_manager.rs`) |
| D9 (MED-HIGH): deadline-fallback boilerplate became the next summarizer's `previous_summary` | `720428967` selects previous prose through a dedicated walk that skips fallback markers and provider checkpoints | `deadline_fallback_boilerplate_is_not_the_next_previous_summary`, `fallback_only_history_yields_no_previous_summary` (`tests/compaction_summary_path.rs`) |
| D14 (MED): the read-files inventory in compaction details was always empty | `439808412` records reads structurally from any tool call with `path`/`paths` arguments (`edit` stays the one name-based write rule) | `non_edit_path_tool_calls_populate_the_read_inventory`, `compact_persists_a_non_empty_read_files_inventory` (same file) + `path_arguments_of_non_edit_tool_calls_record_reads` (`src/core/compaction/utils.rs`) |
| D16 (MED): user-attached images cost zero tokens in `estimate_tokens` while `find_cut_point` charged 4096 chars | `fa4ccc8f2`/`0bb973e43` introduce the shared `IMAGE_BLOCK_CHARS` (4096 chars = 1024 tokens) across all estimator paths; `4dea91d72` aligns the kernel-parity reference | `rot2_user_image_blocks_cost_tokens_d16` (`tests/rot2_multicycle.rs`), `image_blocks_cost_4096_chars_1024_tokens` (`request_tokens.rs`), `compaction_kernel_parity` suite (23/23) |
| D17 (LOW-MED): the context estimate ignored the system prompt and tool schemas | `fa4ccc8f2` adds `request_tokens.rs` (`estimate_request_tokens`, `RequestTokenEstimate`); `0bb973e43` carries the split on `ContextUsageEstimate` and adds the parts on the no-usage fallback path only (usage-anchored path keeps provider totals); `2200211fe` documents the tray's inherited underestimate | `rot2_request_level_estimate_adds_system_prompt_and_tools_d17` (`tests/rot2_multicycle.rs`), `message_list_estimates_understate_request_utilisation` (`src/core/context_health.rs`), `empty_inputs_score_zero`...`total_is_exactly_the_sum_of_its_parts` (`request_tokens.rs`) |
| harness F1/F4/F5: the harness binary forked a session per invocation, read 0 tokens, lost appends on error paths | `49f473d8d` (`continue_recent` session persistence, chars/4 usage anchors, flush-on-append) | `probe_harness_behavior.py` + the 13 adapter checks (`research/context_rot_r2/compactbench_adapter/`) |

Still open on this branch (tracked in `BUG-LEDGER.md`): D7, D10, D18, D19, D20
(deferred/verified-open), the D6 call-site wiring above, and the v1 ledger
defects N1/N2 + W9-F1..F5 (the id-collision and supersession semantics fixes
exist on worker branches and are not integrated here; the typed-state v2 module
is the designed replacement path).

## Experiment results

Category labels (defined in `reports/context-rot-r2/BASELINE.md`, never mixed
within one table):

- SYNTHETIC-DIAGNOSTIC - deterministic fixture, no model calls; measures
  pipeline mechanics, not model quality.
- PRODUCTION-PATH - produced by the real Rust compaction pipeline in this
  repo, not a Python mirror.
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
lands, not a property the ledger guarantees.

Consequences drawn in the reports: the shipped default `taskState = "off"` is
the correct setting; the v1 ledger must not be enabled by default; the
typed-state v2 module (this PR) is the replacement path (role/provenance-based
classification, stable content-derived ids, explicit supersession with both
sides kept), with wiring and its own live evaluation as the next step.

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

### ContextBench offline pipeline (CUSTOM-OPTIMUS)

The committed pipeline (`research/context_rot_r2/contextbench/`): parquet
loading with sha256 verification of the 500-instance verified subset of the
pinned upstream commit, a repo-level dev/holdout split (308/192 instances, no
repo overlap, byte-reproducible manifest), the upstream gold-context metric
layer mirrored function-by-function with citations, and a matched A/B harness
with exact sign test and tie-corrected Wilcoxon. Arms A/B are scaffolded for
recorded trajectories; arms C (typed state) and D (source-grounded
reconstruction) emit blocked records only. No trajectories exist (live phase),
so no retrieval numbers are claimed.

## What is NOT claimed

- No official benchmark scores. Every CompactBench run uses the mock eval
  provider on the public practice suites with custom adapter methods; every
  ContextBench arm is offline scaffolding without trajectories. Nothing here
  is leaderboard-comparable.
- No cross-model claims. The only live-model evidence is GLM 5.3 on one DGX K3
  endpoint, temperature 0, with the documented repetition plan
  (`research/context_rot_r2/live/README.md`).
- R2-E1/E-INC numbers are pipeline mechanics under a deliberately lossy
  deterministic fixture. They bound what the pipeline alone protects; they are
  not predictions about any specific model.
- RepoQA, SWE-bench and Terminal-Bench: not executed in this campaign.
- The typed-state v2 module changes no runtime behavior yet: it is a pure,
  deterministic library module awaiting integration.

## Testing

Gate table run offline in this PR's worktree on the branch tip (see
`reports/context-rot-r2/REGRESSION-GATES.md` for the method; counts below are
this pass):

| suite (command filter) | result | notes |
| --- | --- | --- |
| `--lib compaction` | 257 passed, 2 failed, 3 619 filtered out | the two failures are the documented environment pair below; not r2 regressions |
| `--lib typed_state` | 60 passed | the new module's unit tests |
| `--lib migration` | 20 passed | includes both D8 regression tests |
| `--test rot2_multicycle` | 4 passed, 1 ignored | ignored = the `#[ignore]`d research entry point `rot2_production_multicycle` (scenario/result driven by env); passing: smoke multicycle, D16, D17, lossy-fixture |
| `--test compaction_turn_flow` | 3 passed | D1 reproduction + both D2 guards |
| `--test compaction_summary_path` | 8 passed | D5 (3), D6 (1), D9 (2), D14 (2) guards, now on this branch |
| `--test compaction_recovery` | 9 passed | adjacent gate, untouched |
| `--test compaction_model_threshold` | 10 passed | adjacent gate, untouched |
| `--test compaction_fidelity` | 1 passed | adjacent gate, untouched |
| `--test compaction_summary_guard` | 2 passed | adjacent gate, untouched |
| `--test compaction_kernel_parity` | 23 passed | reference updated for the shared D16 image cost (`4dea91d72`) |

Python suites:

| suite | result |
| --- | --- |
| `research/context_rot_r2/tests/` (pytest, system python) | 5 passed |
| `research/context_rot_r2/incremental/tests/` (pytest, system python) | 6 passed |
| `research/context_rot_r2/contextbench/tests/` (pytest, system python) | 124 passed, 3 skipped |
| same, integration tests on (cb-venv, `CB_REFS_ROOT` pinned checkout) | 127 passed, 0 skipped |
| `research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py` (cb-venv, `HARNESS` = the binary built from this branch) | 13/13 checks passed |

Environment: Windows git-bash, rustc 1.98.1 (48a229cea 2026-09-01), isolated
cargo target dir; the `compaction-harness.exe` used by the adapter checks was
built from this branch tip. The `#[ignore]`d research entry point was not run
(experiment driver; the committed runs under `research/context_rot_r2/runs/`
are the campaign data).

Known environment-bound failures (pre-existing on the base, documented at
`reports/context-rot/REGRESSION-GATES.md` and r2's section 4): the
`enabled_skill_is_importable` test needs `uv` on the test child's PATH (absent
on this host), and the painted-frame tray test is scheduling-sensitive and
passes serialized. Neither file is touched by this campaign.

## Reviewer notes

- Everything new is inert by default. The `taskState` setting keeps its
  `"off"` default (values: `"off"` | `"prompt"`; env
  `PRIME_AGENT_COMPACTION_TASK_STATE`); the typed-state module is not
  referenced from the compaction flow; the `compaction-harness` binary is
  opt-in tooling.
- D17 detail: `ContextUsageEstimate.system_prompt_tokens` /
  `tool_schema_tokens` are non-zero only on the no-usage fallback path - the
  usage-anchored path keeps 0.0 to avoid double-counting provider-reported
  usage.
- D16 detail: the shared constant is `IMAGE_BLOCK_CHARS` (4096 chars = 1024
  tokens at chars/4) in `src/core/compaction/request_tokens.rs`.
- Where to look, in order: `src/core/agent_session.rs`
  (`threshold_auto_compaction_ready`), `src/core/session_manager.rs`
  (`migrate_v1_to_v2`), `src/core/compaction/compaction.rs` (empty-window
  guard, `previous_summary_prose`, `validate_extension_compaction_summary`,
  estimator paths), `src/core/compaction/utils.rs` (read-files inventory),
  `src/core/compaction/request_tokens.rs`, `src/core/compaction/typed_state.rs`,
  the new and updated test files
  (`tests/rot2_multicycle.rs`, `tests/compaction_turn_flow.rs`,
  `tests/compaction_summary_path.rs`, `tests/compaction_kernel_parity.rs`),
  `src/bin/compaction_harness.rs`, then
  `research/context_rot_r2/` and `reports/context-rot-r2/`.
