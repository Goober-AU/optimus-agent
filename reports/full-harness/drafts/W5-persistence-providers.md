# W5 — Session Persistence, Compaction, Daemon, Provider Streaming (READ-ONLY audit)

Scope: static code reading only. Base commit c227b0ae3 (v0.1.34), worktree
C:/Users/openclawuser/optimus-fh-20261017, branch improve/full-harness-validation.
All line numbers refer to the files at that commit. Nothing was executed; no
build, no network, no product file modified.

Audited file set (per assignment): core/session_manager.rs, core/session_action_store.rs,
core/session_lease.rs, core/compaction/* (8 files), core/rlm_continuation.rs,
core/rlm_max_depth.rs, daemon/ (8 files read: native_supervisor.rs,
daemon_supervisor.rs, daemon_supervisor_ownership.rs, daemon_worker_client.rs [symbol map +
decoder sections], supervisor_maintenance.rs, worker_recovery_journal.rs,
daemon_subagents.rs, daemon_mode.rs [targeted sections]),
pi-ai/providers/openai_completions.rs, pi-ai/providers/openai_responses_shared.rs,
plus two direct dependencies read for the streaming focus: pi-ai/src/utils/json_parse.rs,
pi-ai/src/utils/sse_frames.rs (signature only).

---

## 1. CAPABILITY INVENTORY

### 1.1 Session persistence (crates/pi-coding-agent/src/core/session_manager.rs, 6784 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Atomic file write | `write_file_atomic_sync` (77-96) | temp file + rename onto target, Windows EPERM/EACCES retry x5 (112-139) | no fsync of temp/target (option field dead); rename durability not guaranteed on power loss | inline: `appends_after_a_rewrite_land_on_the_rewritten_file` (6049) | fsync option is accepted but never honored (see S2) |
| JSONL parse | `parse_entries_from_buffer` (1439-1452), async variant (1453-1476) | split on \n, skip malformed/blank lines, rehydrate inline tool refs | malformed middle lines silently dropped, no telemetry | `parse_session_entries_skips_malformed_lines_and_applies_attributions` (6432) | mid-file corruption counter/observability; torn FIRST line behavior (see S1) |
| Crash repair | `tail_looks_damaged` (1482-1537), `repair_jsonl_damage_checked` (1543-1641) | 1MB suspicion window; NUL runs stripped, torn tail re-terminated or dropped; atomic rewrite with (len,mtime) before_rename guard | repair only triggers on damaged TAIL; concurrent-append race fails open with typed error; head/mid corruption not repaired | safety tests: `session_safety_tail_repair_preserves_prefix_and_allows_distinct_append` (safety_tests.rs:123), `session_safety_required_repair_failure_blocks_open_and_preserves_current_manager` (158) | torn first line; NUL byte beyond last 1MB |
| Write buffering | `SessionWriteBuffer` (3392-3603), `drain` (3431), `drain_through` (3500), `push_line` (3556) | coalesce lines, cap-flush, drop-on-failure semantics (never retry a partial write), pending_error surfaced by next drain | one flush period of lines is the documented crash window | `buffered_appends_drain_identical_bytes` (5721), `drain_open_failure_drops_the_batch_and_recovers_via_rewrite` (5914), `timed_flusher_lands_buffered_lines_within_the_period` (5869), `dropping_the_manager_drains_buffered_appends` (5895) | timed-flusher vs `rewrite_file` interleaving (dedup invariant, see G3) |
| Entry append + rollback | `persist` (4160-4194), `append_entry` (4195-4208), `append_entry_with_rollback` (4209-4230), `rollback_last_append` (4231-4247) | no-assistant guard (user msgs in memory until first assistant or flush_now); on error retain entry, force rewrite; rollback pops entry and rewrites | user messages before first assistant response lost on crash (documented TS parity) | `failed_rewrite_never_acknowledges_persistence` (transcript_t06.rs:412) | crash between user prompt and first assistant (acceptance: documented) |
| Branching/forks | `branch` (5058), `branch_with_summary` (5070), `create_branched_session` (5115-5275), `fork_from` (5415-5530) | parentId chains, labels replicated, git_state relinked on fork, unique file targets | fork writes line-by-line with ignored writeln errors (see S4) | `branch_and_tree_shapes_follow_parent_ids` (6257), `fork_from_relinks_children_around_git_state` (6603), `create_branched_session_writes_labels_and_returns_a_new_file` (6648), safety: `session_safety_fork_bounds_cycles_in_removed_git_entries` (109) | fork write failure path; fork crash mid-write |
| Open/reopen | `set_session_file` (3688-3781), `open` (5285), `open_async` (5340), `continue_recent` (5382) | repair gate, preloaded entries invalidation, migration v1->v3, rlmDepth backfill rewrite | empty/invalid-header load DESTRUCTIVELY resets file (see S1) | `load_entries_requires_a_valid_header` (6448), `appends_after_reloading_the_same_path_land_on_the_reloaded_file` (6094), `migration_upgrades_v1_and_v2_sessions` (6405) | torn-header reset data loss; open_async fallback double-parse |
| Context build | `build_session_context_with_entry_ids` (1006-1334), cycle guard (1035-1040) | leaf-to-root parent walk with visited set; compaction selection with model-target matching; parallel per-entry parse with sequential fallback | damaged ancestry bounded; compaction chosen is last matching on path | `build_session_context_walks_the_leaf_to_root` (6163), `compaction_keeps_retained_messages_after_the_summary` (6187), `history_snapshot_places_the_summary_at_the_retained_boundary` (6218) | provider-checkpoint mismatch fallback ordering |
| Inline tool-text dedup | `serialize_session_file_entry` (657-709), `rehydrate_session_file_entry` (747-816) | toolResult details stdout/stderr/result/backgroundOutput replaced by content-reference (UTF-16 units + sha256) | unreconstructable reference -> isError diagnostic text | `inline_tool_text_references_shrink_and_rehydrate` (6346), `rehydration_reports_an_unreconstructable_reference` (6375), transcript_t06.rs:238 `unicode_inline_tool_refs_roundtrip` | none material |
| Usage attribution | `apply_child_usage_attributions` (1694-1727), `append_child_usage_attribution` (4453) | child_usage_attributed entries re-apply aggregate usage to target assistant message on load | target missing -> error at append; load silently skips | `child_usage_attribution_updates_the_target_message` (6318) | none material |
| RLM depth | `resolve_session_rlm_depth` (1888-1971) | header rlmDepth, else legacy path segments, else env | none observed | `explicit_rlm_depth_comes_from_the_env_when_absent` (5686), `child_depth_derives_from_the_parent_header` (5699), `legacy_depth_falls_back_to_sub_session_segments` (5710) | none material |
| Session scan/catalog | `scan_session_info` (2396-2500), `fold_session_scan_line` (2610), streaming load threshold 128MB (37-38) | incremental scan with per-path queue, file identity (dev/ino) revalidation, oversized-message summary extraction | torn tail tolerated in catalog view | `session_info_scan_reads_name_state_and_usage` (6470), `streamed_catalog_scan_preserves_torn_tail_and_append_metadata` (6536), `streamed_catalog_scan_obeys_captured_size_during_append` (6565), transcript_t07.rs:694 `catalog_detects_atomic_replacement` | none material |

### 1.2 Session action store (core/session_action_store.rs, 1782 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Lifecycle state machine | `legal_transitions` (373-427), `transition_session_action` (455-513) | queued->selected->preparing->committing->running->terminal; illegal transitions rejected | none observed | `legal_transitions_are_enforced` (1341) | none material |
| Rollback proof | `transition_session_action` Committing->Queued (466-505) | requires settled dispatch AND primary message NOT durable in transcript (replay/loss prevention) | proof forgery (settled=true with stale transcript) | `committing_rollback_requires_settled_dispatch_and_clean_transcript` (1363) | stale-transcript proof race (concurrent persist between proof build and transition) |
| Ticket promises | `ActionTicketController` (622-705), `Deferred` (592) | settle-once, rejections retained for every awaiter | none observed | `tickets_settle_once_and_report_the_first_outcome` (1534), `ticket_promises_retain_rejections_for_every_awaiter` (1547) | none material |
| Store lanes | `enqueue` (786-807 priority insert), `enqueue_tail` (810), `enqueue_front` (823), `select_first` (842), `remove` (864), `swap_queued` (900), `move_queued` (924) | two lanes (next_turn_boundary, when_run_idle); only overtake trailing lower-priority queued work; recovery uses enqueue_tail | duplicate id rejected | `backlog_human_priority_preserves_fifo_active_work_and_recovery_order` (1296), `enqueue_rejects_duplicates_and_nonqueued_actions` (1393), `select_first_prefers_the_next_turn_boundary_lane` (1411), `swap_and_move_only_apply_to_queued_actions_in_one_lane` (1471) | concurrent select vs enqueue ordering |
| Eviction policy | `is_idle_eviction_threshold_met` (1160), `can_passivate_session` (1180), `can_evict_worker` (1192), `can_select_session_action` (1213) | pure predicates: idle minutes, no clients, no cron, no parent/descendants for passivation; whole-worker eviction requires Ready+connected+no owner+all sessions idle | stale snapshot inputs decided by callers (supervisor refreshes first, native_supervisor.rs:910-955) | `idle_eviction_thresholds_and_policies` (1670), `action_selection_requires_a_fully_idle_runtime` (1755) | negative/NaN minutes; `last_activity_at` NaN |

### 1.3 Session lease (core/session_lease.rs, 1305 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Lease acquisition | `acquire_session_lease` (699-830) | atomic dir rename (candidate->lock), guard coordination, live-owner AlreadyActive, dead-owner reclaim, 3 attempts | guard stale-removal can preempt a >5s action (compromise flag never set, see S3) | `acquires_then_detects_a_live_owner_and_releases` (inline 1025), `rename_contention_matches_platform_codes` (inline) | guard preemption; concurrent acquire interleaving |
| Process identity | `get_windows_process_start_id` (296), native (328), `get_ps_process_start_id` (349), `get_process_start_id` (376) | pid + start id (win: .NET ticks via GetProcessTimes; linux: /proc stat field; mac/BSD: ps lstart pinned TZ) | probe failure -> "alive" (fail-safe) | `dead_process_identity_never_launches_ps` (405, linux-only) | Windows native path untested in-map (UNCERTAIN) |
| Guard lock | `acquire_lease_guard` (535-563), `with_acquired_lease_guard` (582-611) | .guard dir as mutex, 5s stale removal, 100x10ms retries | `compromised` flag never set (dead logic, S3) | indirect via acquire tests | compromised-guard detection |
| Sweep | `sweep_dead_owner_leases` (854-915) | bounded 256 dirs/250ms, 30s min age, unreadable never reclaimed | starvation beyond 256 dirs in read_dir order | (module tests exist for basics; sweep bounds tests not found in-map) | sweep bound/ordering; fresh-lease protection |

### 1.4 Compaction (crates/pi-coding-agent/src/core/compaction/)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Trigger thresholds | `should_compact` (1170), `should_compact_with_cap` (1183), `should_compact_for_model` (1207) | explicit trigger threshold fraction of context window; hard cap 250k (Azure GPT 400k); explicit max_tokens reserve | negative/NaN handled (finite checks) | tests/compaction_model_threshold.rs (10 tests: azure caps, reserves, policy recompute) | none material |
| Token estimation | `estimate_tokens` (1251), `estimate_context_tokens` (1110), `calculate_context_tokens` (1058) | chars/4 heuristic, usage-anchored estimate + trailing estimate | f64 semantics pinned to JS | tests/compaction_kernel_parity.rs (45 fns incl. randomized property) | none material |
| Cut points | `find_valid_cut_points` (1337), `find_turn_start_index` (1369), `find_cut_point` (1414) | walk back until keepRecentTokens; cut only at user/assistant/custom/branchSummary; never toolResult; split-turn prefix | degenerate entries fallback to first cut point | `compaction_keeps_retained_messages_after_the_summary` (session_manager tests 6187) | cut at assistant-with-toolcalls keeping orphan results (designed, untested in-map) |
| Preparation | `prepare_compaction` (1528-1706) | previous-summary chain from last non-checkpoint compaction, boundary from firstKeptEntryId, split-turn turn-prefix messages, retained-state anchor (last 2000 chars of kept assistant text) | no-history compaction returns None | tests/compaction_fidelity.rs:34, compaction_parity_suite.rs (73 fns) | none material |
| Summary generation | `generate_summary_with_options` (1740), `generate_bounded_summary` (1856), summary budget (732) | prompt v1/v2, update policy off/consolidate-repeated-v1, bounded output budgets, length-retry | refusal/empty/error handoff rejected by guard | tests/compaction_length_recovery.rs (8 fns), compaction_chat_length_recovery.rs, compaction_anthropic_length_recovery.rs, compaction_summary_guard.rs | none material |
| Native provider compaction | `compact_with_metrics` (2329-2620) native branch (2360-2453) | provider checkpoint via compact_simple; incompatible checkpoint -> typed error; unsupported -> text fallback with notice | checkpoint model mismatch | compaction_parity_suite.rs fixture providers | none material |
| Deadline fallback (A7) | `deadline_truncation_result` (2620-2650), select! (2596-2607) | keep-recent-tail truncation when summary phase exceeds deadline; deadline_fallback flag in details | cancels summary signal | (parity suite covers A7 scenarios; see coverage notes) | restart-after-deadline-fallback context shape |
| Tool-output compaction | utils.rs `TOOL_RESULT_MAX_CHARS` (125), `truncate_for_summary` (129), `serialize_conversation` (179-346); jev compact flow | tool results truncated to 2000 chars for summary text; file ops inventory extracted/re-attached | char-boundary safety via scan (charcount) | jev_compaction_tests.rs (31 fns: pinning, unicode multi-block, malformed pairs, checkpoint boundaries) | none material |
| Char counting | charcount.rs `count_noncontinuation` (580), `scan_chars_forward` (610), backend self-check (495-580) | SIMD/scalar non-continuation byte count, bit-identical to chars().count(); startup self-check fixtures | mis-dispatch guarded by differential fixtures | inline differential corpus (622+) | none material |
| Branch summarization | branch_summarization.rs `collect_entries_for_branch_summary` (105), `prepare_branch_entries` (216), `generate_branch_summary` (322) | branch exploration summary inserted on return | budget-bound | (session_manager branch tests; suite) | none material |
| Parallel helpers | parallel.rs `try_parallel_map` (thresholds), fault injection (82-96) | identical-list parallel map with sequential fallback; fallback counter | fallback recorded | tests/compaction_parallel_fallback.rs (11 fns) | none material |

### 1.5 RLM continuation/depth (core/rlm_continuation.rs 400 lines; core/rlm_max_depth.rs 105 lines)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Terminal classification | `classify_rlm_child_terminal` (132-172) | stop+marker -> terminal; LENGTH never terminal (cannot certify under cut); ERROR/ABORTED -> failed terminal | marker regex requires end-of-text (174-180) | (callers outside map; inline rlm tests not in-map) | LENGTH-with-marker precedence test in-map |
| Continuation messages | `create_rlm_child_continuation_message` (182-210) | length-recovery vs no-marker continuation prompts, bounded attempts | none observed | none in-map | wave-2 unit |
| State validation | `parse_rlm_continuation_state` (271-361) | strict version/tasks/continuationCount/taskHadLength/phase enum validation; pendingResult shape | malformed -> None (state dropped) | none in-map | wave-2 unit |
| Depth limit | `rlm_depth_limit_reached_error` (rlm_max_depth.rs:37) | machine-readable degradation message | none | inline tests (rlm_max_depth.rs:42-62) | none |

### 1.6 Daemon supervisor (modes/daemon/)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| Worker close/recovery ladder | `handle_worker_close` (native_supervisor.rs:559-595), `recover_worker` (626-769), `schedule_deferred_worker_recovery` (769-782) | [250,1000,5000ms] retries; process identity Current/Unknown/Gone; Unknown keeps probing (deferred, 10 rounds x 5s); Gone+owned parks failed awaiting owner; reconnect -> Ready + persist | park leaves descriptor + journals | supervisor_core_backlog_tests.rs:162 `core_supervisor_recovery_parking_preserves_replacement_descriptor`, 87 `late_create_never_revives_stopped_or_replaced_generation` | recovery during ownership loss; deferred round exhaustion timing |
| Reconnect | `reconnect_worker` (792-816), `connected_client` (987-1023) | new DaemonWorkerClient, authenticate, subscribe, refresh; exact-identity gate before relaunch | authenticate fail -> close_now | daemon_parity_tests / supervisor_maintenance_tests (in-map names not enumerated) | none material |
| Idle eviction | `sweep_idle_workers` (910-955), `worker_eviction_snapshot` (872) | double refresh (30s timeout) before decision; non-destructive on refresh failure; archive+graceful stop; child passivation cap 2 | long sweeps (serial N x 30s) | session_action_store eviction tests + maintenance tests | sweep duration bound |
| stop_worker | `stop_worker` (2502-2626) | assert ownership + registration identity (pid/start_id/instance_id/stop_requested_at); graceful 10s (win), force SIGKILL + 1s; timeout -> background finalizer; journal retirement; roster flip; descriptor removal | parked stop retains journals | `core_supervisor_permanent_stop_failure_parks_and_survives_adoption` (108), `core_supervisor_transient_stop_failure_has_finite_retry_budget` (136), worker_stop_safety_tests.rs | none material |
| Orphan cleanup | supervisor_maintenance.rs `recover_uncertain_worker_operations` (147-231), `record_deferred_orphans` (30-90), `reclaim_worker_registration` (320-372), `cancel_session_tree` (373-...), `settle_ephemeral_cancel_intents` | never kill by bare pid; provable-identity kills only; foreign/unproven pids deferred to bounded side record; dead-registration assertions between each step; journals retained on failure | capacity refusal (64 records/256KB) blocks cleanup (intentional) | sessionkernel_t04_process_cleanup.rs (12 fns: journaled+reaped, hardened taskkill, failed signal keeps journal) | deferred-orphan side-record crash-retry; foreign-record supersede |
| PR #168 get_state | native_supervisor.rs:3140-3180 | bounded 5s forward to connected worker; transport error -> roster summary (with rosterStatus/lastHeardFromAt via `summary_from_entry` 3617); worker failure response propagates authoritatively; unknown session still fails | roster fallback fabricates sessionActions queuedCount=0 (see S5); no explicit stale marker | supervisor_core_backlog_tests.rs:176 saturated fallback, 206 unknown, 213 dead-client | staleness fidelity (messageCount, sessionActions, queued steering) under saturation |
| Ownership | daemon_supervisor_ownership.rs `acquire_daemon_supervisor_ownership` (794-900), `assert_daemon_supervisor_owner_current` (930-975), registry guard (653-724), `mutate_daemon_supervisor_owner` (754) | candidate-dir atomic rename; stale owners reclaimed only when provably dead (zombie cache 902-929); token-checked mutation; shutdown admission blocks acquisition | lock_is_stale windows (725-733) | daemon_supervisor_parity_tests.rs (in-map, not enumerated) | guard stale-window preemption (same class as S3) |
| Worker recovery journal | worker_recovery_journal.rs `record` (92-118), `compact` (139-152) | latest-per-active-session dedup, append+sync_all, bounded 512KB compact even with busy entry | append errors silently swallowed while in-memory latest still updated (see S6) | inline tests (records dedup, busy-bound, malformed skip) | write-failure visibility |
| Subagent state restore | daemon_subagents.rs `create_rlm_subagent_runtime` (44-...), `complete_rlm_subagent_runtime`, `release_rlm_subagent_runtime`, `delete_rlm_subagent_runtime` | Weak owner refs; spawn-ledger record with admission rollback + artifact deletion on cancel; retained-stop generation checks | owner dispose mid-spawn | (rlm_ledger/daemon tests outside enumerated set) | cancel-during-admission race |
| Snapshot streaming | supervisor_maintenance.rs `stream_cached_attach` | disk-backed chunk cache, sequence int coercion, drain before chunk (30s), disconnect cancellation | client stall -> 30s timeout error | daemon_snapshot_backlog_tests.rs | none material |

### 1.7 Provider streaming (crates/pi-ai/src/providers/)

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests | Missing tests |
|---|---|---|---|---|---|
| SSE framing | openai_completions.rs `SseLineReader` (1538-1650) | chunk-split tolerant, CRLF strip, `:` comments, event-only flush, invalid UTF-8 -> U+FFFD; unterminated tail dropped per SSE spec | none observed | `split_utf8_crlf_done_finishes_without_eof_and_body_errors_still_fail` (3328), `done_marker_finishes_without_waiting_for_the_server_to_close` (3216) | data-line-without-blank-line at EOF (spec discard) is untested in-map |
| Stream loop | `run_stream_body` (2598-2766) | typed fast path, in-stream error -> StreamError with headers, idle-timeout reset per chunk, [DONE] handling, dropped-stream detection (2753-2755) | silent body abort | `read_sse_data_aborts_a_silent_body_and_drops_the_connection` (3150), `in_stream_error_does_not_leave_sse_waiting_for_more_body` (3456), `done_settles_held_open_body_after_usage_and_drains_each_event_once` (3283) | none material |
| Retry classification | `map_stop_reason` (1245-1323), StreamError kinds (1384-1512), `sse_idle_timeout_error_classifies_as_retryable_not_permanent` (3125) | malformed_response dropped-stream error distinct from retryable transport | none observed | `phase_observation_does_not_turn_deferred_builder_errors_into_success` (3258) | none material |
| Partial snapshots (PR #167) | `partial_message` (2766-2782) field-wise build; per-event partials carry live state | byte-identical event payloads vs reference consumer | remaining per-event full-snapshot cost (documented) | partial_snapshot_tests (5650+): `partial_snapshots_match_a_golden_multi_block_event_sequence` (5843), `start_partial_is_empty_and_done_matches_the_final_output` (6034), `text_delta_after_a_tool_call_reuses_the_first_text_block` (6077), `usage_and_response_id_reach_every_later_partial` (6106), `reasoning_details_partial_carries_the_redacted_signature_block` (6124) | deep-block partial cost; multi-MB text partial |
| Fragmented tool args | `handle_value_chunk` tool_calls (2185-2225) + `StreamingJsonAccumulator` (json_parse.rs:114-150) + partial-json port (154+) | incremental arg parse, eager <=8KiB then 1/16 growth refresh, flush at finish; repair path for control chars/escapes | malformed final JSON -> empty Map (parse_streaming_json never errors) | `large_tool_arguments_are_complete_on_end_error_and_early_eof` (openai_responses_shared tests 2298), `tool_call_blocks_persist_parsed_arguments_without_partial_json` (2322) | UTF-16 boundary delta split at accumulator refresh threshold; accumulator drop between refresh and flush |
| Token usage | `observe_chunk_usage` (1728), chunk.usage + choice.usage fallback (Moonshot, 2103-2113) | usage captured from any chunk, cached_tokens detail, cache-write cost | usage after finish preserved | `usage_and_response_id_reach_every_later_partial` (6106), `pending_usage_observer_is_owned_after_terminal_result` (3407), `normalized_usage_cannot_have_negative_input_or_a_missing_total` (ORS 2282) | none material |
| Response identity | response_id from first chunk id (2060-2066), response_model guard, `response.created` id (ORS 752-756) | first non-empty id wins; model mismatch recorded once | id changes mid-stream ignored | `usage_and_response_id_reach_every_later_partial` (6106) | none material |
| Responses stream | openai_responses_shared.rs `process_responses_stream` (731-1290), `append_part_text` (612-626) in-place Value::String append (PR #167), refusal handling, output_item.done finalization, function_call args done/flush (965-1090) | multi-item accumulation; finalized item before next starts; done partials carry full arguments | arguments.done not prefix-consistent -> no delta emitted (partial still correct at item done) | `responses_partials_accumulate_blocks_across_items` (2099), `responses_partial_reflects_a_finalized_item_before_the_next_starts` (2171), `responses_function_call_done_partial_carries_the_full_arguments` (2216), `responses_multi_item_partial_content_matches_done_message` (2250) | refusal+text mixed item partials; unicode split in append_part_text (in-place push_str is boundary-safe, untested) |

---

## 2. RISK-RANKED GAPS

P0: none found. The persistence core (atomic rename, repair gate, rollback proof,
orphan-kill identity checks) holds up under static reading.

P1
- G1 (data loss, narrow trigger): A session file whose FIRST line is torn or whose
  header is invalid is destructively replaced on open — no backup, no quarantine.
  Evidence: parse skips the bad line (session_manager.rs:1427-1435);
  `finalize_loaded_entries` drops everything when entries[0] is not a valid header
  (1682-1693); `set_session_file` then calls `new_session` + `rewrite_file` over the
  same path (3727-3737). Tail repair (1482) never examines the head. Trigger: crash
  during `fork_from` header append (5526-5530), disk corruption, or manual edit.
- G2 (PR #168 fidelity): get_state roster fallback fabricates `sessionActions:
  {queuedCount: 0, steering: [], followUps: []}` unconditionally
  (native_supervisor.rs:3603 within `session_summary_from_roster_row` 3599-3616;
  fallback arm 3172-3177). A saturated worker with queued steering serves
  queuedCount=0. There is no explicit "stale" marker on the fallback response;
  staleness is only implicit via rosterStatus/statusLabel/lastHeardFromAt
  (3617-3619). Task brief expected the roster fallback to be "marked stale, not
  authoritative" — the marker does not exist (UNCERTAIN: UI may tolerate).
- G3 (integrity invariant, unproven): `rewrite_file` (3961-3998) clears the write
  buffer AFTER the atomic rename without holding the buffer lock across the
  rename. The timed flusher thread (register_timed_flush, 3930-3939) drains
  concurrently under the same lock. If the flusher drains pending bytes to the
  path between the rename and the clear, those lines duplicate in the new file.
  Invariants (bytes cleared on failure, pending_error gate) make the window
  appear unreachable, but nothing tests or documents the interleaving.

P2
- G4: `write_file_atomic_sync` in session_manager.rs accepts `fsync: bool` but
  never calls sync_all (77-96); every call site passes `fsync: false` (1632,
  3982). Session-file rewrites are not fsynced; only the daemon-side delivery
  boundary (drain_write_buffer + external fsync, contract at 4002-4016,
  unit-tested at 5772-5821) provides durability, and its caller is outside this
  audit's file map (UNCERTAIN coverage).
- G5: Mid-file JSONL corruption is silently dropped (1427-1449): no counter,
  no log, no repair — only the tail is examined. A valid-header file with a
  corrupted middle loses those entries invisibly on every load.
- G6: `fork_from` ignores per-line write errors (`let _ = writeln!(file, ...)`,
  5526-5530 and the entry loop 5540-5565): a failed fork write leaves a truncated
  file that the next open "repairs" by dropping the torn tail, losing fork
  content without any error surfaced to the caller.
- G7: session_lease guard compromise flag is dead code (see S3): the mutual
  exclusion between a >5s lease action and a concurrent stale-guard removal
  (5s, LEASE_GUARD_STALE_MS at 24) is unenforced. The underlying candidate->lock
  rename still serializes acquisition, so double-acquire is unlikely, but the
  read-reclaim-write sequence can interleave.
- G8: worker recovery journal: `record` inserts into in-memory `latest` even when
  `append` silently failed (worker_recovery_journal.rs:92-118, append 126-136);
  after restart the busy evidence is absent while the running process believed
  it recorded it.

P3
- G9: get_state 5s bound changes behavior for slow-but-healthy workers: a worker
  answering at 5-30s (within the UI deadline) previously eventually succeeded;
  it now gets the (possibly staler) roster view. Deliberate trade-off per commit
  a0a26e34c, but no test pins the chosen 5s bound vs the client 30s deadline.
- G10: `sweep_dead_owner_leases` bounded to 256 dirs / 250ms in read_dir order
  (854-872): leases beyond the first 256 entries are never swept in a pass;
  starvation is order-dependent.
- G11: idle eviction sweep refreshes workers serially with a 30s timeout each
  (native_supervisor.rs:913): N unresponsive workers stretch one sweep to N*30s.
- G12: `SessionManager::persist` no-assistant guard (4172-4186): user turns
  before the first assistant response exist only in memory; a crash there loses
  the prompt (documented TS parity — acceptance decision, not a bug).

P4
- G13: `open_async` falls back to sync `open` when the file is missing or loads
  empty (5344-5348, 5353), re-reading and re-repairing; benign double parse.
- G14: responses `function_call_arguments.done` emits no delta when the done
  arguments are not a byte-prefix of the accumulated partial
  (openai_responses_shared.rs:986-994); the ToolCallEnd partial still carries
  full arguments, so only the delta stream is affected.

---

## 3. SUSPECTED BUGS

S1. Destructive reset of a corrupt-header session file (P1)
- Where: crates/pi-coding-agent/src/core/session_manager.rs:3727-3737 (reset path),
  1682-1693 (`finalize_loaded_entries` empty-on-invalid-header), 1427-1435 (parse
  skips malformed first line).
- What looks wrong: one torn FIRST line (crash during fork header write, partial
  disk write) makes the loader return zero entries; `set_session_file` then
  overwrites the user's file with a fresh session header. Original entries are
  not preserved anywhere. TS-parity behavior, but the port has no backup and no
  telemetry for the reset.
- Reproduce/verify (test): write a JSONL file whose line 1 is `{"type":"sess` and
  lines 2..n are valid message entries with a parentId chain; call
  `SessionManager::open`. Assert: current behavior replaces the file with only a
  new header (entries gone). Wave-2 decision: assert the data loss explicitly or
  assert a quarantine/backup if the team changes the contract.

S2. Dead fsync option in session_manager's atomic write (P2)
- Where: session_manager.rs:70-96 (`WriteFileAtomicOptions.fsync` never read;
  no `sync_all` in the module), call sites 1632 and 3982 pass `fsync: false`.
  Contrast: utils/atomic_file (used by supervisor_maintenance.rs:83-87) supports
  `fsync: true, fsync_dir: true`.
- What looks wrong: the private port dropped the fsync half of
  writeFileAtomicSync; the field suggests durability that does not exist.
- Verify (test): instrument/monkeypatch is not possible in Rust — write an
  integration test that kills power semantics is out of scope; instead assert
  via code review contract or add a `sync_all` when `fsync` is set and unit-test
  that the temp file content is flushed before rename (observable through a
  fault-injecting FS layer if available).

S3. Lease guard compromise detection never fires (P2)
- Where: session_lease.rs:524-563 (`compromised` created false, never stored
  true anywhere in the file — verified by rg: only lines 526, 537, 543, 577, 588,
  589, 599, 600 reference it), stale removal at 546-553 (5s).
- What looks wrong: proper-lockfile marks a lock compromised when it is removed
  or its mtime changes mid-action; the port removed that detection. An action
  holding the guard longer than 5s (PowerShell start-id probe, cold start) can
  have the guard removed underneath by a concurrent acquirer; both then run the
  read-reclaim-acquire sequence.
- Reproduce (test): unit test — acquire guard; sleep 6s; from a second thread
  call `acquire_lease_guard` on the same directory; assert current behavior:
  second acquire succeeds while first still holds it (documents the gap). Then
  assert the candidate->lock rename still yields exactly one winner
  (`acquire_session_lease` from two threads; exactly one Ok lease).

S4. fork_from swallows write errors (P2)
- Where: session_manager.rs:5526-5530 (header writeln ignored), 5540-5565
  (entry loop `let _ = writeln!(...)`).
- What looks wrong: a full disk or permission error mid-fork produces a
  truncated file and `fork_from` still returns Ok; the loss surfaces only later
  as a repair-drop.
- Reproduce (test): make the fork target directory read-only after N bytes
  (platform-specific) or use a small tmpfs quota; assert fork_from returns Err
  or at minimum that the failure is observable. At minimum add a regression test
  asserting every source entry appears in the forked file.

S5. get_state roster fallback fabricates queue state (P1, PR #168)
- Where: native_supervisor.rs:3599-3616 (`session_summary_from_roster_row`
  inserts `sessionActions {queuedCount: 0, ...}` unconditionally), fallback arm
  3172-3177; RosterSessionSummary (agent_roster.rs:80-120) has no queue fields.
- What looks wrong: during worker saturation — exactly when the fallback fires —
  the UI is told there are no queued actions even if steering/follow-ups are
  queued. No stale marker distinguishes the fallback from an authoritative
  answer (the task brief states the fallback should be marked stale).
- Reproduce (test): extend supervisor_core_backlog_tests fixture — saturated
  worker with a queued steering message recorded; assert the get_state fallback
  response either carries the true queuedCount or an explicit stale/roster flag;
  today it will show 0.

S6. Worker recovery journal records state it failed to persist (P2)
- Where: worker_recovery_journal.rs:92-118 (`record` appends then inserts into
  `latest` regardless of append success) and 126-136 (`append` swallows open/
  write errors).
- What looks wrong: after an append failure, the running supervisor answers
  `get_latest`/compaction decisions with busy state that a restart will not see.
- Reproduce (test): make the journal path unwritable (read-only dir), call
  `record`, assert `get_latest` still contains the record while
  `read_latest(path)` does not; decide whether `record` should surface the
  failure.

S7. Timed-flusher vs rewrite duplication window (P3, UNCERTAIN)
- Where: session_manager.rs:3961-3998 (`rewrite_file` renames, then clears the
  buffer under its own lock) vs `SessionWriteBuffer::drain` (3431-3497, holds
  the same lock across open+write).
- What looks wrong: a drain that opens the append handle after the rename but
  before the clear writes already-rewritten lines again (duplicate JSONL rows;
  by_id dedupes on load but file_entries/get_entries/export show duplicates).
  The reachability is blocked only by the flushed/pending_error invariants.
- Reproduce (test): unit test with a zero-period buffer plus an injected
  rewrite concurrent with a pending buffered line (fault-injection similar to
  compaction/parallel.rs set_parallel_fault_injection); assert the final file
  contains each entry exactly once.

---

## 4. TEST PLAN (wave 2)

Unit tier (target file: crates/pi-coding-agent/src/core/session_manager.rs test modules
or tests/transcript_t08.rs):
1. `torn_first_line_reset_is_explicit` — assert the S1 destructive reset exactly
   (entries replaced, no backup) or the new quarantine behavior. Tier: unit.
2. `midfile_corruption_drop_is_observable` — valid header + one malformed middle
   line; assert loaded entries skip it and (new) a counter/log hook fires.
   Tier: unit.
3. `fork_write_failure_is_surfaced` — S4: fault-inject the fork file write; assert
   Err or a detectable marker. Tier: unit.
4. `rewrite_clears_buffer_before_rename_or_documents_the_invariant` — S7 race
   test. Tier: unit.
5. `open_async_preloaded_entries_match_sync_load` — parity of open vs open_async
   on a repaired file. Tier: unit.

Unit tier (crates/pi-coding-agent/src/core/session_lease.rs):
6. `guard_stale_removal_cannot_preempt_an_active_action` — S3: >5s guard holder
   vs second acquirer; assert single lease winner and (if implemented) a
   compromised-guard error. Tier: unit.
7. `sweep_respects_age_floor_and_bounds` — 300 fake lease dirs; assert <=256
   scanned, fresh leases untouched, unreadable never reclaimed. Tier: unit.

Unit tier (crates/pi-ai/src/providers/openai_responses_shared.rs tests):
8. `mixed_refusal_text_item_partial_sequence` — refusal part then output_text
   part; pin partial events and stop_reason_raw. Tier: unit.
9. `unicode_delta_splits_across_accumulator_refresh` — tool args with multibyte
   code points split at the 8KiB refresh boundary; assert final arguments
   complete and identical to a single-shot parse (completions + responses).
   Tier: unit.

Integration tier (crates/pi-coding-agent/tests/):
10. `get_state_fallback_reports_staleness_or_true_queue_counts` — S5/PR #168
    extension of supervisor_core_backlog_tests.rs fixture. Tier: integration.
11. `worker_recovery_journal_append_failure_is_visible` — S6: unwritable journal
    path; assert record() outcome contract. Tier: integration.
12. `restart_after_deadline_compaction_fallback` — session with
    deadline_fallback: true compaction entry; reopen and assert context builds
    summary-first with retained tail and no side-effect duplication of usage
    attribution. Tier: integration.
13. `compaction_during_subagent_activity_defers_or_gates` — the action-store
    gate (`can_select_session_action` requires !compaction, 1213-1221) plus a
    live subagent stream; assert no interleaved compaction commit while a child
    run is active. Tier: integration.
14. `session_lease_blocks_second_process_on_same_file` — two processes (or
    process-abstraction injection) with leases enabled; assert AlreadyActive and
    dead-owner reclaim latency bound. Tier: integration.

E2E tier (tests/rlm-daemon_t11.rs style):
15. `daemon_reconnect_after_worker_kill_preserves_transcript_and_queue` — kill
    worker mid-turn; assert recovery ladder restores the session, queued
    steering survives via restore_actions, and no duplicate side effects (bash
    tool results appear once). Tier: e2e.
16. `streaming_provider_partial_snapshot_regression_pr167` — golden full-sequence
    pin already exists (openai_completions.rs:5843); add the responses-side
    golden for append_part_text in-place growth (multi-KB text, per-delta
    partials byte-identical vs a reference consumer). Tier: e2e-ish integration.

---

## 5. COVERAGE NOTES

NOT EXECUTED:
- No cargo build/check/test (per constraints; root agent serializes builds).
- No runtime verification of any suspected bug; all evidence is static reading.
- daemon_mode.rs (18,751 lines) was read only in targeted sections: command
  handlers for steer/follow_up/restore_next_turn/restore_actions/append_custom_message
  (5150-5330), get_state-adjacent status plumbing, and symbol/constant maps.
  The full session-spawn, attach, and queue-restore state machines inside it
  were NOT audited line-by-line.
- native_supervisor.rs dispatch body (2627-3140, 3280-3450) was skimmed for
  command routing only; prompt-admission and family-catalog paths not audited.
- Auto-compaction trigger site (who calls should_compact each turn) lives in
  the agent loop outside the assigned file map — NOT INSPECTED.
- The delivery-boundary fsync caller of `drain_write_buffer`/
  `record_rlm_delivery_sync` (session_manager.rs:4002-4016) is outside the map —
  NOT INSPECTED; the drain+record contract is only covered by the inline test
  at session_manager.rs:5772-5821.
- Providers other than openai_completions.rs / openai_responses_shared.rs
  (anthropic, google, bedrock, codex_responses, kiro, openai_responses.rs,
  responses_transport.rs, openai_compaction.rs) were NOT read; sse_frames.rs
  was signature-checked only.
- pi-ai/src/utils/json_parse.rs partial-json parser internals were read once;
  the differential corpus inside it was not re-derived.
- Existing test files were surveyed by name/signature (rg within assigned
  paths), not by full-body reading: compaction_parity_suite.rs (73 fns),
  long_session_soak.rs (139 fns), transcript_t06/t07, jev_compaction_tests.rs,
  compaction_kernel_parity.rs, compaction_observability.rs,
  compaction_parallel_fallback.rs, compaction_*_length_recovery.rs,
  compaction_model_threshold.rs, compaction_summary_guard.rs,
  sessionkernel_t04_process_cleanup.rs, rlm-daemon_t10.rs,
  session_metadata_allocations.rs, supervisor_core_backlog_tests.rs.
  daemon_parity_tests.rs, daemon_supervisor_parity_tests.rs,
  supervisor_maintenance_tests.rs, supervisor_messaging_safety_tests.rs,
  worker_stop_safety_tests.rs, supervisor_adoption_retry_tests.rs were listed
  but not opened (8-file daemon read budget spent on production code).
- Windows-specific behaviors (rename retry, taskkill hardening, native
  process-start-id) are cited from code; no Windows live probing was done.

Key uncertainty: G2/S5 "marked stale" — the parent brief describes the PR #168
fallback as "roster fallback marked stale, not authoritative". No explicit stale
flag exists in the audited code path; staleness is only implicit through
rosterStatus/statusLabel/lastHeardFromAt fields. Either the brief refers to
that implicit marking, or the marker lives in a file outside this audit's map
(UNCERTAIN).
