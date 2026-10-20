# W69 — end-to-end production-path trace: `compaction.taskState = "typed"` -> kernel

Verification wave for the campaign's central claim: the `taskState = "typed"`
setting flows from user settings to the production compaction kernel end to
end, and the ledger it produces survives to the next compaction cycle.
Method: reading code only, at the current integration tip
(`cab09bdd2`, branch `rot2/w69-trace`). Each hop lists file, function, line,
and the test that pins it. Unpinned hops are flagged explicitly.

Paths are relative to `crates/pi-coding-agent/src/core/`.

## Hop 1 — the settings files

`settings_manager.rs:598-600` (`FileSettingsStorage`): global settings live at
`<agentDir>/settings.json`, project settings at `<cwd>/<config-dir>/settings.json`.
A user writes:

```json
{ "compaction": { "taskState": "typed" } }
```

into either scope (each scope's read/write path is pinned by
`save_writes_global_settings_without_an_explicit_flush` and
`project_save_writes_settings_without_an_explicit_flush`,
`settings_manager.rs`). The typed serde mirror of the object
(`settings_manager.rs:74-113`, field `task_state` at line 100) documents the
key; the runtime reads it as a raw nested string (hop 3).

## Hop 2 — load and deep merge (project overrides global)

`SettingsManager::reload_sync` (`settings_manager.rs:1207-1239`) loads both
scopes and merges at line 1238:

```rust
self.settings = deep_merge_settings(&self.global_settings, &self.project_settings);
```

`deep_merge_settings` (`settings_manager.rs:539-559`): project wins per key;
nested objects merge recursively (a shallow spread per level), so a project
`compaction.taskState` overrides the global value while global-only
`compaction` siblings survive — and a global-only `taskState` is still visible
because the global `compaction` object contributes its keys to the merge. Both
scopes are read; the merged view is `self.settings`.

- Pin: `deep_merge_merges_nested_objects_and_overrides_scalars`
  (`settings_manager.rs:2804`) — uses the `compaction` object itself as the
  nested example (project `reserveTokens` overrides, global `enabled` survives).

## Hop 3 — read the merged key

`get_compaction_task_state` (`settings_manager.rs:2053-2062`) reads
`nested_string(&self.settings, "compaction", "taskState")`
(`settings_manager.rs:1565-1572`; `keys::COMPACTION = "compaction"`). A wrong
JSON type resolves to `None` (fails safe), not a panic.

## Hop 4 — resolve the mode (D6 wiring, commit 4be9623b0)

`get_compaction_task_state` delegates to
`crate::core::compaction::compaction::resolve_task_state_mode(configured,
env_flag("PRIME_AGENT_COMPACTION_TASK_STATE"))`
(`settings_manager.rs:2058-2061`). Before D6 the settings side accepted only
`"prompt"`; `"typed"` failed safe to `"off"` and never reached the kernel.

`resolve_task_state_mode` (`compaction.rs:728-743`): `"typed"` ->
`TASK_STATE_MODE_TYPED` (constant at `compaction.rs:720`). Precedence:
explicit setting wins; an unrecognized setting fails safe to `off` without
consulting the environment; only an absent setting lets the environment
through; default `off`.

- Pins:
  - `compaction_task_state_accepts_typed_and_fails_safe`
    (`settings_manager.rs:3882`): `{"compaction":{"taskState":"typed"}}` ->
    `"typed"` — the settings-side half of the D6 fix.
  - `compaction_task_state_unknown_value_fails_safe_to_off`
    (`settings_manager.rs:3892`), `compaction_task_state_defaults_to_off`
    (`settings_manager.rs:3902`).
  - `task_state_mode_precedence_is_explicit_then_environment_then_off`
    (`compaction.rs:4154`, inline `task_state_mode_tests`).
  - `typed_mode_resolution_accepts_typed_and_fails_safe_on_unknown_values`
    (`rot2_typed_integration.rs:43`).
  - `explicit_off_setting_wins_over_the_typed_environment_variable`
    (`rot2_typed_adversarial.rs:1292`): full precedence matrix including the
    environment wins only when the setting is absent.

## Hop 5 — pack into the resolved settings record

`get_compaction_settings` (`settings_manager.rs:2143-2157`) packs
`task_state: self.get_compaction_task_state()` (line 2153) into
`ResolvedCompactionSettings` (field at `settings_manager.rs:349`). The value
is one of the three canonical strings, always resolved, never raw.

## Hop 6 — the session adapter (FLAGGED: no task_state-specific pin)

`AgentSession::compaction_settings` (`agent_session.rs:19514-19531`) locks the
session's `settings_manager` (`agent_session.rs:1542`, shared from the session
config at `agent_session.rs:3045`), calls `get_compaction_settings()`, and maps
the resolved record into the kernel's `CompactionSettings`
(`compaction.rs:1283-1308`, field `task_state: Option<TaskStateMode>` at line
1307):

```rust
task_state: Some(resolved.task_state.clone()),   // agent_session.rs:19529
```

Every production compaction entry point reads this one adapter, so the wiring
is single-voiced:

- threshold gate at turn end: `threshold_compaction_needed`
  (`agent_session.rs:4871-4878`, `compaction_settings()` at 4875); overflow
  check (`agent_session.rs:7530`); pre-turn compaction (`agent_session.rs:9098`,
  `9111`); `compact.run` scheduling validation (`agent_session.rs:5257`,
  `prepare_compaction` at 5263).
- the shared compaction body `perform_compaction_unmeasured_full`
  (`agent_session.rs:18145`): `let settings = self.compaction_settings();`
  at line 18179, passed to `prepare_compaction` at lines 18218-18224 and
  onwards to `compact_with_metrics` at lines 18414-18434.
- manual/requested compaction funnels into the same body
  (`perform_compaction_manual` -> `perform_compaction_unmeasured_full`,
  `agent_session.rs:17979-17998`).

Verdict: wired and single-voiced, but no test constructs a session whose
settings manager carries `compaction.taskState` and asserts the value seen by
the compaction body. The session-level tests that call
`compaction_settings()` (`agent_session.rs:22852`, `22939`) assert other
fields. This is the one hop whose pin is indirect: settings side pinned by
hop 4, kernel side pinned by hops 7-10, the field copy in between unasserted.
Flagged for a follow-up integration test; not a wiring defect.

## Hop 7 — prepare_compaction: the mode gates the typed build/merge

`prepare_compaction` (`compaction.rs:1914-2098`) resolves the mode at line
2037 (`resolve_task_state_mode(settings.task_state.as_deref(), None)` —
`settings.task_state` is always `Some(resolved)` from hop 6, so the
environment is not re-read here) and gates the typed ledger at lines 2067-2084
(`task_state_mode == TASK_STATE_MODE_TYPED`):

1. previous durable ledger: `details.get("typedState")` of the previous
   compaction entry (line 2072 — never the v1 `taskState` field), parsed by
   `parse_typed_state` (`typed_state.rs:1543-1572`): a payload that fails the
   strict parse is salvaged record-by-record (D26, `salvage_record` at
   `typed_state.rs:1578-1606`) instead of silently dropped.
2. window ledger: `build_typed_state(&path_entries[boundary_start..history_end])`
   (line 2079; `typed_state.rs:1377` = `reconcile(classify_window(entries))`).
3. `merge(previous_ledger.as_ref(), window)` (line 2080;
   `typed_state.rs:1404-1450`): durable records carry forward by id, new
   records renumber after the previous maximum, the D25 step boundary retires
   unrestated Step-scoped records, `enforce_durable_cap` bounds the ledger.
4. an empty merged ledger yields `None` (line 2081), matching "the window held
   nothing the classifier recognises".

The ledger rides on `CompactionPreparation.typed_state`
(`compaction.rs:1821`, fields documented at the struct) so the prompt, the
verifier and the persisted entry use one deterministic artefact.

- Pins:
  - `prepare_compaction_builds_a_typed_ledger_from_tool_results_and_subagent_state`
    (`rot2_typed_integration.rs:491`): typed ledger from a realistic window;
    also pins that the v1 field stays `None` in typed mode.
  - `compact_typed_mode_feeds_the_protected_block_and_round_trips_the_ledger`
    (`rot2_typed_integration.rs:558`): the second `prepare_compaction` merges
    the cycle-1 ledger carried through `details.typedState` by record id
    (lines 614-658).
  - `details_with_both_v1_and_typed_payloads_keep_both_and_the_mode_selects`
    (`rot2_typed_adversarial.rs:1017`): both payloads deserialize, survive a
    round trip, and the mode selects its own field.
  - D26: `malformed_typed_state_payload_silently_drops_durable_records`
    (`rot2_typed_adversarial.rs:1109`, salvage repair reported),
    `unrecoverable_typed_state_records_drop_loudly_with_a_count`,
    `malformed_prior_ledger_flag_surfaces_in_details_typed_verification`.
  - D25 merge bounds: `step_scoped_records_stay_bounded_across_twelve_merges`,
    `retired_step_records_keep_summary_references_resolvable`,
    `pending_actions_do_not_accumulate_forever_across_merges`
    (all `rot2_typed_adversarial.rs`).

## Hop 8 — compact_with_metrics: allocate -> render -> prompt -> verify -> persist

`compact_with_metrics` (`compaction.rs:2825-3239`), destructuring
`typed_state` at line 2850:

- 8a Budget: `typed_hot_state_budget_tokens(summary_budget_tokens)`
  (lines 3005-3015; quarter of the summary output budget, capped at 2048;
  `compaction.rs:770-777`).
  Pin: `typed_hot_state_budget_is_a_capped_quarter_of_the_summary_budget`
  (`rot2_typed_integration.rs:79`).
- 8b Allocate: `allocate(state, budget)` (line 3015;
  `typed_state.rs:1781-1854`): priority-ranked inclusion; a CURRENT
  mandatory record that does not fit raises `UnsafeCompaction` and fails the
  episode before any model call (lines 3016-3020, failure text at
  `compaction.rs:783-795`).
  Pins: `unsafe_typed_allocation_fails_the_episode_before_any_model_call`
  (`rot2_typed_integration.rs:666`),
  `starved_budget_fails_closed_before_any_model_call_and_the_retry_merges`
  (`rot2_typed_adversarial.rs`), the W24 cap/corrections suite
  (`rot2_render_cap.rs`).
- 8c Render: `render_allocation` (lines 3021-3024;
  `typed_state.rs:2141-2173`, header `## Typed Protected State
  (deterministic, provenance-tagged)` at `typed_state.rs:2135`).
  Pin: `compact_typed_mode_feeds_the_protected_block_and_round_trips_the_ledger`
  asserts the block, the constraint and the copy instruction reached the
  summarizer's wire requests (`rot2_typed_integration.rs:569-584`).
- 8d Attach: `protected_state` selection (lines 3027-3034; v1 block and typed
  block are mutually exclusive; the typed block pairs with
  `TYPED_STATE_INSTRUCTION`, `compaction.rs:752-756`).
  `generate_summary_with_options` (`compaction.rs:2181-2241`) appends
  `{prompt}\n\n{state}\n\n{instruction}` outside the `<user-instructions>`
  block (lines 2212-2222) — recovered state, not user instructions — and
  `generate_bounded_summary` carries it into the model wire call.
  Pin: same round-trip test (the fixture summarizer's captured requests carry
  the block); `v1_prompt_mode_still_works_unchanged`
  (`rot2_typed_integration.rs:854`) pins that v1 mode attaches only the v1
  block.
- 8e Verify: `verify(&artifact, state)` (lines 3158-3178;
  `typed_state.rs:1942`): required records must be present, superseded
  records may not appear without their supersession, bracketed references
  must resolve, verified claims must be backed by live records. A failure
  whose findings are only missing items gets one bounded deterministic
  repair (`append_missing_protected_items`, `compaction.rs:889`, applied at
  3172) with no model call; any other failure, or a repair that still fails,
  fails the episode (`typed_verification_failure`, `compaction.rs:801`).
  Pins: `missing_constraint_triggers_the_deterministic_repair`,
  `superseded_decision_presented_as_current_fails_verification`
  (`rot2_typed_integration.rs`);
  `repair_is_bounded_to_one_attempt_with_no_second_model_call`,
  `repair_that_still_fails_verification_fails_the_episode`,
  `all_caps_verified_claims_cannot_evade_the_fabricated_check`
  (`rot2_typed_adversarial.rs`).
- 8f Persist on the result: `CompactionResult.details`
  (lines 3224-3238) carries `typed_state: typed_state.clone()` (line 3234)
  and `typed_verification` (line 3235). `CompactionDetails`
  (`compaction.rs:65-92`) is `#[serde(rename_all = "camelCase")]`, so the
  JSON fields are `typedState` and `typedVerification` — a distinct field,
  never overloading the v1 `taskState`.
  Pins: `rot2_typed_integration.rs:608-612` (both keys present, v1 key
  absent in typed mode);
  `v1_task_state_details_still_deserialize_alongside_the_typed_fields`
  (`rot2_typed_integration.rs:798`) pins old v1 entries still deserialize.

## Hop 9 — the native-provider arm (D28 carry + D32 salvage report)

When the provider answers a native compaction (lines 2857-2986), the returned
`CompactionDetails` keeps `typed_state: typed_state.clone()` (line 2958): the
server-side summary replaces only the conversation text; the merged durable
chain persists unchanged across the native boundary. No model-written summary
was produced locally, so nothing is verified; the only `typed_verification`
written is the D32 salvage rider, which surfaces the D26 diagnostics of a
malformed prior ledger through `details.typedVerification.malformedPriorLedger`
with `passed: true` and zero findings (lines 2970-2981).

- Pins:
  - `native_compaction_between_typed_cycles_keeps_the_durable_chain`
    (`rot2_native_typed_chain.rs:468`): three cycles with a native compaction
    in the middle; the cycle-1 record survives by id through the native
    boundary into cycle 3.
  - `native_compaction_entry_round_trips_the_ledger_through_the_session_file`
    (`rot2_native_typed_chain.rs:591`): reopen the real session file and read
    `details.typedState` off the persisted native entry.
  - `native_compaction_reports_the_salvage_of_a_malformed_prior_ledger`
    (`rot2_wave_adversarial.rs:716`): the salvaged record carries through the
    native path and the diagnostics surface.

## Hop 10 — the ledger reaches the next cycle

`agent_session.rs:18448-18462`: `perform_compaction_unmeasured_full` persists
the result through `session_manager.append_compaction(...,
serde_json::to_value(details))`, so the ledger lands on the compaction entry
of the session file. The next cycle's `prepare_compaction` walks back to the
previous compaction (lines 1926-1934), reads `details.typedState` (hop 7.1)
and merges. The loop is closed: settings -> mode -> ledger -> summary ->
verifier -> persisted entry -> next cycle's merge input.

- Pins:
  - the round-trip halves of the hop-7 and hop-9 tests above;
  - `rot2_smoke_typed_arm_persists_typed_ledger_and_verifies`
    (`rot2_multicycle.rs:1039`): the typed arm of the multicycle harness runs
    the production `prepare_compaction` + `compact` pipeline offline with
    `task_state = Some("typed")` (`arm_settings`, `rot2_multicycle.rs:623-636`)
    and asserts every cycle persists the ledger, records a passed
    verification, and retains every planted probe across the cycles.
    RUN RESULT at tip `cab09bdd2` (this wave, focused run): FAILS
    deterministically — `compact` rejects the lossy fixture's summary with
    "typed protected state not represented: 0 missing, 0 mis-superseded,
    0 unresolved references, 1 unbacked verified claims"
    (`rot2_multicycle.rs:751`). Pre-existing at the tip (this worktree has no
    source changes; the report commit is docs-only) and isolated to the
    typed arm: the sibling smoke test over the same scenario
    (`rot2_smoke_multicycle_runs_all_cycles_and_reports_probes`, default +
    prompt arms) passes. The interaction is between the typed verifier's
    fabricated-verified-claim scan (`typed_state.rs:2053-2087`) and the
    deterministic lossy summarizer after D30 (`10e2b36e6`) added the
    user-stated verified-decision seed; this read-only wave did not
    root-cause it. All other typed-path pins (16 tests across five binaries)
    pass at the tip; see "Test run evidence" below.

## The environment-variable path

`PRIME_AGENT_COMPACTION_TASK_STATE=typed`:

- Production route: `get_compaction_task_state` reads it via `env_flag`
  (`settings_manager.rs:1599-1601`, call at 2060) and passes it as the
  `environment` argument to `resolve_task_state_mode`. With the setting
  absent, env `typed` resolves to `"typed"` and flows through hops 5-10
  unchanged.
- Library route: `resolve_task_state_mode` itself falls back to
  `std::env::var(TASK_STATE_ENV)` when its environment argument is `None`
  (`compaction.rs:735-737`). The `prepare_compaction` call site passes `None`
  (line 2037), but production `settings.task_state` is always `Some(resolved)`
  from hop 6, so this fallback serves direct library callers; the settings
  manager's read is the production env route.
- Pin coverage: the precedence matrix (setting absent + env `typed` ->
  `typed`; env applies only when the setting is unset; unrecognized env fails
  safe) is pinned by
  `explicit_off_setting_wins_over_the_typed_environment_variable`
  (`rot2_typed_adversarial.rs:1292`) and
  `typed_mode_resolution_accepts_typed_and_fails_safe_on_unknown_values`
  (`rot2_typed_integration.rs:43`).
- FLAG: no test exports the real process variable
  (`PRIME_AGENT_COMPACTION_TASK_STATE`) and asserts the settings manager
  resolves it; the `rot2_typed_adversarial.rs` test documents why (the
  readback at lines 1316-1322 only asserts when the host has not exported the
  variable). The env hop is pinned at the resolver level, not at the
  process-env integration level.

## Verdict table

| Hop | What | File:line | Pin | Verdict |
|-----|------|-----------|-----|---------|
| 1 | settings files (global + project) | settings_manager.rs:598-600 | save_writes_global_settings_without_an_explicit_flush; project_save_writes_settings_without_an_explicit_flush | wired |
| 2 | deep merge, project over global | settings_manager.rs:1238, 539-559 | deep_merge_merges_nested_objects_and_overrides_scalars (2804) | wired, pinned |
| 3 | read `compaction.taskState` | settings_manager.rs:2054, 1565-1572 | compaction_task_state_* trio (3882/3892/3902) | wired, pinned |
| 4 | resolve "typed" -> mode | compaction.rs:728-743 (D6 4be9623b0) | settings_manager.rs:4154 inline; rot2_typed_integration.rs:43; rot2_typed_adversarial.rs:1292 | wired, pinned |
| 5 | pack ResolvedCompactionSettings | settings_manager.rs:2143-2157 | indirect (hop 3+4 pins) | wired |
| 6 | session adapter -> CompactionSettings | agent_session.rs:19514-19531 (19529) | none for task_state | wired, FLAGGED |
| 7 | mode gates typed build/merge in prepare | compaction.rs:2037, 2067-2084 | rot2_typed_integration.rs:491/558; rot2_typed_adversarial.rs:1017/1109 | wired, pinned |
| 8 | budget/allocate/render/attach/verify/persist | compaction.rs:3005-3239 | rot2_typed_integration.rs:666/79/558/798/854 + adversarial | wired, pinned |
| 9 | native arm D28 carry + D32 salvage | compaction.rs:2943-2983 | rot2_native_typed_chain.rs:468/591; rot2_wave_adversarial.rs:716 | wired, pinned |
| 10 | persist entry; next cycle reads typedState | agent_session.rs:18448-18462; compaction.rs:1926-1934, 2072 | round trips (pass); rot2_multicycle.rs:1039 FAILS at tip | wired, pinned by round trips; smoke pin failing at tip (pre-existing) |
| env | PRIME_AGENT_COMPACTION_TASK_STATE | settings_manager.rs:2060; compaction.rs:735-737 | resolver matrix only | wired, FLAGGED |

## Test run evidence (this wave, focused runs at tip `cab09bdd2`)

| Run | Result |
|-----|--------|
| `cargo test -p pi-coding-agent --lib compaction_task_state` | 3 passed |
| `cargo test -p pi-coding-agent --lib task_state_mode` | 1 passed |
| `cargo test -p pi-coding-agent --test rot2_typed_integration` | 9 passed |
| `cargo test -p pi-coding-agent --test rot2_native_typed_chain` | 2 passed |
| `cargo test -p pi-coding-agent --test rot2_wave_adversarial native_compaction_reports` | 1 passed |
| `cargo test -p pi-coding-agent --test rot2_multicycle rot2_smoke_typed_arm` | FAILED (pre-existing at tip, typed-arm only; see hop 10) |
| `cargo test -p pi-coding-agent --test rot2_multicycle rot2_smoke_multicycle` (sibling, same scenario, default+prompt arms) | 1 passed |

## Flags (no wiring defect found)

1. Hop 6 (`AgentSession::compaction_settings`, `agent_session.rs:19514`): no
   test asserts `task_state` through the session adapter (settings file ->
   session compaction). A one-test integration gap, not a code gap.
2. Env path: no test exports the real process variable; the pin is the
   resolver-argument matrix.
3. The A7 deadline-fallback arm (`deadline_truncation_result`,
   `compaction.rs:3246-3276`) carries `typed_state` (line 3271) and skips
   verification, mirroring the D28 native carry — that carry is code-verified
   but has no dedicated pin (the native carry does).
4. Pre-existing test failure at the tip (not a trace finding, but a reviewer
   must know): `rot2_smoke_typed_arm_persists_typed_ledger_and_verifies`
   fails deterministically on "1 unbacked verified claim" while its sibling
   default+prompt smoke passes; the always-on typed-path pins
   (`rot2_typed_integration`, `rot2_native_typed_chain`, the adversarial
   suites) all pass. Needs a follow-up wave to root-cause the
   verifier/fixture interaction introduced or exposed by D30
   (`10e2b36e6`).

## Reviewer one-liner

The setting reaches the kernel and the ledger reaches the next cycle.
