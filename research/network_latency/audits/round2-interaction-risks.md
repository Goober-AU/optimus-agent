# Round-2 interaction-risk audit (completions-quad, responses-quad, tcp-pool)

Worker: interaction-auditor. Read-only audit; no code changes, no gate runs.
Branch improve/network-ipc-performance @ 522913929 (clean worktree at audit
time: `git status --porcelain` empty). Round-1 commits reviewed in full:
61384d695 (SSE framing), aac27156b (guarded client pooling), plus the harness
commits f9d2a9af0 / f56c151ed / 522913929 and ace61379e (daemon IPC, out of
round-2 scope). Method: read the committed diffs, then read the current
working-tree code of every file the three round-2 lanes touch, and enumerate
where a round-2 change can interact with round-1 committed behavior. Every
claim below is code-verified; file:line references are the current tree.

## 0. Round-1 baseline the round-2 lanes build on

- Completions consumer: `StreamState` (openai_completions.rs:1313-1370) owns
  `Vec<StreamingBlock>`; `state.content()` (1339) rebuilds a fresh
  `Vec<ContentBlock>`; `partial_message` (2756) = `output.clone()` +
  `state.content()`; called from 19 sites (2114, 2145, 2195, 2256, 2343,
  2369, 2409, 2480, 2536, 2781, 2820, 2842, 2887 and the ensure/start paths).
  Terminal sync: `output.content = state.content()` on success (2742) and on
  error after flushing (2586).
- SSE framing: `SseLineReader` span-copy push/decode/flush
  (openai_completions.rs:1528-1650), `[DONE]` single check in
  `forward_sse_payload` (1758-1776), typed consumer `handle_typed_chunk`
  (2282) with the Value fallback `handle_value_chunk` (2041) and the
  routing split in `run_stream_body` (2642-2680: observer-attached streams
  keep the parsed-Value DOM path).
- Client pooling: `openai_http_client_pool.rs` (full file). `Key`
  (29-34: runtime, origin digest, environment digest, redirect), MAX_CLIENTS
  16 (13), MAX_CLIENT_AGE 300s (14), `build(Count)` = redirect none +
  `pool_max_idle_per_host(4)` (95-100), `build(Generation)` = reqwest
  defaults. `poolable()` gate (257-284), phase labels
  (`http_client_reused[_loopback|_https|_generation_loopback]`,
  `fresh_generation_policy`, `fresh_tls_policy`, `fresh_system_proxy_policy`).
- Shared clients: `shared_http.rs` - process-lifetime
  `HashMap<ClientPolicy, ...>` (33-45), no environment rotation at all;
  policies Default / RedirectNone / Bedrock{force_http1,no_proxy} / Kiro;
  documented invariant "No `ClientBuilder::timeout` is ever set here" (10-18);
  `POOL_IDLE_TIMEOUT` 90s (23). Callers: anthropic.rs:2623, google.rs:755,
  google_vertex.rs:1025, azure_openai_responses.rs:633, mistral.rs:527,
  openai_codex_responses.rs:1381, openai_completions.rs:1869,
  responses_transport.rs:121, bedrock_responses_client.rs:425 (RedirectNone),
  amazon_bedrock.rs:2233 (Bedrock), kiro/mod.rs:186 (Kiro).
- Redirect-none clients, five distinct builders: shared_http RedirectNone /
  Bedrock / Kiro, `responses_transport::http_client` opted-in client
  (responses_transport.rs:115-130, azure-openai-managed + github-copilot,
  90s idle), and `http_client_pool::build(Count)`.
- Guarded transport wiring: `post_chat_completions` picks
  `http_client_pool::acquire(url, Generation)` only when the SGLang audit is
  attached (openai_completions.rs:1862-1870); the count preflight uses
  `acquire(url, Count)` (openai_sglang_budget.rs:146). Everything else uses
  `shared_client(ClientPolicy::Default)`.
- Bench/evidence infrastructure: `sse_parse_bench.rs` (reader A/B with
  verbatim reference copies + full-consumer arm),
  `provider_transport_bench.rs` (reuse proof, acquisition cost, idle expiry,
  error paths), `client_pool_bench` inside openai_http_client_pool.rs tests.

## 1. completions-quad (openai_completions.rs partial_message/StreamState)

R1 - Cached `Vec<ContentBlock>` mirror can go stale (severity: HIGH).
`state.content()` is derived data; the source of truth is mutated at many
points after block creation. A mirror cache must be refreshed at every one:
text `push_str` (2112, 2341), thinking `push_str` (2143, 2367), tool-call
id/name fill-in (typed 2355-2366, value 2183-2195 area), tool arguments
replacement from `partial_args.append` (2191, 2405), `thought_signature` set
by the encrypted reasoning-details merge (2243, 2466), the
reasoning-details Thinking block creation (2253, 2477) and its
`thinking_signature` re-encode per reasoning delta (2271, 2495), block pushes
in ensure_* (2817, 2839, 2879, 2528). A missed site yields events with lagging
content and, if the final sync reads the cache, a wrong final message.
Catch: `typed_and_value_paths_agree_on_weird_but_accepted_chunks`,
`consume_through_both_paths`, `t15_stream_events_cover_text_reasoning_tool_usage`,
`reasoning_details_round_trip`; a debug-only equivalence assert
(`debug_assert_eq!(mirror, state.content())` in tests) makes drift loud.
Mitigation: funnel every mutation through one `refresh(index)` helper, or keep
deriving the snapshot but reuse one `Vec<ContentBlock>` allocation
(`clone_from`-style into a scratch buffer), which is stale-proof by
construction.

R2 - Flush ordering inside `finish_block` (severity: HIGH).
`finish_block` (2772) calls `flush_tool_arguments` (2762, mutates
`tool_call.arguments` from the streaming accumulator) BEFORE building the
`partial` (2781). If the quad hoists partial construction (or serves a cached
partial built at delta time), ToolCallEnd partials and everything after them
carry the un-flushed partial-JSON arguments. Same ordering exists in the
error path: flush all blocks first, then `output.content = state.content()`
(2576-2586).
Catch: `large_completions_arguments_flush_on_success_and_failure` (both
orders), `incomplete_completions_fail_without_executing_partial_tool_calls`.
Mitigation: preserve flush-then-snapshot; add an assert that the ToolCallEnd
partial's arguments equal the ToolCallEnd `tool_call.arguments`.

R3 - Cached partial header vs late usage/stop-reason updates (severity:
HIGH). `partial_message` clones the whole `output`, whose fields mutate
mid-stream: usage at 2075/2092 (Value) and 2310/2321 (typed) - usage commonly
arrives in the final chunk(s), sometimes AFTER `finish_reason` with empty
choices - plus `stop_reason`/`error_message` (2100, 2329), `response_id`
(2063, 2298), `response_model`. A quad that caches the scalar header of
the partial and patches only `content` will emit terminal partials with zero
usage, and if the Done message is assembled from that cache the persisted
usage is wrong.
Catch: `done_settles_held_open_body_after_usage_and_drains_each_event_once`,
`cancellation_at_recognized_done_preserves_prior_usage_and_is_aborted`,
`completions_monitoring_records_raw_phases_and_inclusive_usage_without_changing_output`
(observer/Value path), and the phase-sequence assert at 3879.
Mitigation: rebuild the header (cheap: four small strings + Usage) per event
or patch the cache at every `output.` write; never assemble the final
Done/Error message from the partial cache - clone the live `output`
(stream_openai_completions 2935-2960 already mutates `output` after
`run_stream` returns, so the live value is the only correct source).

R4 - Typed/Value path divergence (severity: MEDIUM).
The same rewrite must cover `handle_value_chunk` (2041) - observer-attached
monitoring streams and weird chunks route there (run_stream_body 2642-2680).
Optimizing only the typed path is a measurement-honesty trap: the committed
`bench_consumer` runs observer-absent, so a typed-only win would be reported
while monitoring runs see none.
Catch: `typed_and_value_paths_agree_on_weird_but_accepted_chunks`,
`typed_path_handles_duplicate_keys_via_the_value_fallback`,
`completions_monitoring_records_raw_phases_and_inclusive_usage_without_changing_output`.
Mitigation: shared bookkeeping helpers for both paths; if only the typed path
changes, label the bench result as typed-path-only.

R5 - Bench harness coupling (severity: MEDIUM).
`sse_parse_bench.rs::bench_consumer` binds the exact round-1 signatures
(`StreamState::new`, `parse_typed_chunk`, `handle_typed_chunk` with the
10-argument list). A signature change breaks the committed harness; the
in-binary A/B isolates only the READER (reference vs current), so the quad's
consumer delta is NOT isolated in-binary today - a before/after cross-run
under the gate is the fallback, which is weaker. Two quad lanes plus
sse-bench-ext may all edit this one file in a single worktree.
Mitigation: signature-preserving rewrite preferred; if signatures must
change, coordinate sse_parse_bench.rs ownership with sse-bench-ext; add a
verbatim round-1 consumer reference arm so the quad delta is isolated
in-binary like the reader A/B.

R6 - Event/memory semantics of the no-drain bench (severity: LOW).
The bench comment says fixtures are bounded because each event and its deep
partial stays resident. A quad that lowers per-event clone cost does not
change residency; keep fixture bounds as-is so numbers stay comparable.

## 2. responses-quad (openai_responses.rs "same for responses")

R7 - The hot code is NOT in openai_responses.rs (severity: HIGH, process).
`openai_responses.rs` contains only Start/Done/error clones (301, 380, 436)
and `SseBuffer` (850-925, already optimized in round 1). The 13 per-event
`partial: output.clone()` sites live in
`openai_responses_shared.rs::process_responses_stream` (720-1240; clones at
748, 758, 773, 809, 838, 857, 904, 936, 958, 987, 1027, 1062, 1115), which is
shared by four providers: openai_responses.rs:401,
azure_openai_responses.rs:291, amazon_bedrock_responses.rs:431,
openai_codex_responses.rs:1127 and 2763. Editing it changes bedrock, azure,
codex and github-copilot behavior simultaneously; the parent's ownership map
does not list this file for the lane.
Catch: the full responses test matrix in openai_responses_shared.rs (44
tests) plus azure/bedrock/codex provider tests.
Mitigation: parent must grant openai_responses_shared.rs to responses-quad
(as round 1 did for the SseBuffer region) and the lane must run the azure
and bedrock responses tests, not only the openai ones. If the lane stays
inside openai_responses.rs, the honest outcome for the consumer path is
NO-CHANGE; only the SseBuffer (already round-1 work) lives there.

R8 - `current_item` DOM mirrors `output.content` (severity: HIGH).
The consumer mutates two structures in parallel: the ContentBlock in
`output.content[index]` AND the `current_item` JSON (parts appended at 778+
and 860+; text at 877-906; summary 784-841). On `response.output_item.done`
the block is REPLACED from the item DOM (thinking at 1011, text at 1052,
tool arguments at 1086) and `thinking_signature` is `stringify(item)`. A
restructure that defers or caches either side desyncs End-event content and
signatures. `block_index(output)` (510) is computed per event and assumes
`output.content` is the live list.
Catch: `reasoning_items_keep_summary_text_and_signature`,
`refusal_marker_survives_deltas_or_done_items_and_successful_completion`,
`on_output_item_done_runs_before_finalizing_the_block`,
`text_stream_emits_start_delta_end_and_maps_usage`.
Mitigation: keep output_item.done as the single finalize point; if caching
partials, invalidate the cache on every output_item.added/done.

R9 - ToolCall argument finalization (severity: HIGH).
Arguments accumulate in `CurrentBlock::ToolCall { partial_json }` (766-773
start; delta append 940-958; done 961-990) and are finalized in place at
output_item.done (1086) before ToolCallEnd carries the finalized
`tool_call` (1112-1115). The `arguments.starts_with(previous_partial_json)`
delta-completion (981-990) can emit an extra ToolCallDelta AFTER the full
arguments are already in the block. Partial caching must not miss the
finalize-in-place mutation or the extra delta.
Catch: `large_tool_arguments_are_complete_on_end_error_and_early_eof`,
`tool_call_blocks_persist_parsed_arguments_without_partial_json`.
Mitigation: same flush-before-snapshot rule as R2; build ToolCallEnd from
`output.content[index]` after finalization, never from a cached partial.

R10 - Terminal enrichment after the last partial (severity: MEDIUM-HIGH).
`response.completed`/`incomplete` (1118-1218) rewrite `output.usage` (1153),
`stop_reason` (1182), `stop_reason_raw`, and may call
`note_empty_completion` (1217); `run_openai_responses` then pushes
`Done { message: output.clone() }` (openai_responses.rs:434-437) after
`process_responses_stream` returns. There are no partials after
response.completed, so a partial cache only has to be correct up to the last
event - but the Done message must come from the live output.
Catch: `incomplete_terminals_preserve_exact_reason_and_usage`,
`normalized_usage_cannot_have_negative_input_or_a_missing_total`,
`service_tier_pricing_uses_the_resolver`,
`incomplete_terminal_without_status_is_not_success_and_cannot_erase_refusal`,
`completed_empty_output_with_billed_tokens_is_labelled_and_stays_a_stop`.
Mitigation: terminal events always clone the accumulator; cache only
intra-item deltas.

R11 - SseBuffer coupling (severity: MEDIUM).
If responses-quad touches SseBuffer (it is in openai_responses.rs), three
round-1 properties must survive: the empty-chunk skip_lf guard, byte-identical
event sequences, and the error plumbing (`buffer.error` ->
`responses_request_interrupted` event in the scan closure at
openai_responses.rs:355-370). azure_openai_responses.rs:273 constructs the
same `SseBuffer::default()` - any signature change breaks azure.
Catch: `responses_buffer_matches_reference_on_typical_bodies`,
`responses_buffer_matches_reference_on_split_crlf_and_utf8`,
`responses_buffer_preserves_skip_lf_across_empty_chunks` (all in
sse_parse_bench.rs), plus azure stream tests.
Mitigation: keep `push(&[u8]) -> Vec<Value>`; if the consumer is restructured
to need owned events, add an adapter instead of changing the buffer.

## 3. tcp-pool (shared_http.rs keepalive/pool changes)

R12 - Honesty: TCP keepalive is already ON by default (severity: HIGH for
reporting). Vendored reqwest 0.12.28
`async_impl/client.rs` Config::default (lines 301-305):
`pool_idle_timeout: Some(90s)`, `tcp_keepalive: Some(15s)`,
`tcp_keepalive_interval: Some(15s)`, `tcp_keepalive_retries: Some(3)`. Every
client in this repo - shared and pooled - already carries TCP keepalive.
Presenting "enabled keepalive" as a round-2 win is the same class of error as
the TCP_NODELAY rule. What IS off by default: `http2_keep_alive_interval`
(None, line 353) and `http2_keep_alive_while_idle` (false) - but the local
gateway leg is HTTP/1.1 (PROV-08: server Keep-Alive timeout=5), so h2 PING
keepalive cannot help the measured local path and is unmeasurable offline
against remote providers.
Catch/measurement: none can prove a keepalive "win" on the loopback fixture;
the honest measurements are accept counts, reconnect latency after a
server-side close, and dead-peer detection latency with a silent-server
fixture (accept then stop reading, no FIN).
Mitigation: if the lane changes keepalive knobs, measure the observable
delta (detection latency, reconnect counts) and report it as such; a
NO-CHANGE verdict with this citation is a valid result.

R13 - Deadline-class options on shared clients (severity: CRITICAL if
introduced). shared_http.rs:10-18 documents the invariant: no
`ClientBuilder::timeout` (total deadline) on shared clients, because every
provider request on that policy would inherit it; SSE streams legitimately
run for many minutes. `read_timeout`/`timeout` added to Default would cap
anthropic/google/azure/mistral/codex/completions/responses streams at once.
`connect_timeout(15s)` exists only on Kiro, deliberately.
Catch: no existing test asserts absence of a timeout on the shared builders
(bedrock's Debug-form tests assert only http1_only); a regression shows up as
flaky stalls in `cancellation_closes_a_stalled_stream_and_preserves_partial_output`
and the sse idle-timeout tests, which is a weak net.
Mitigation: add a bedrock-style Debug-form assertion that the Default and
RedirectNone builders contain no `read_timeout`/`timeout` field, in
shared_http.rs tests; keep the invariant comment in sync with any new
option.

R14 - Evidence overwrite by client_pool_bench (severity: MEDIUM).
The `client_pool_bench` test inside openai_http_client_pool.rs writes
`research/network_latency/provider/results/client-pool-bench.json`
unconditionally (the fs::write at the end of `fn client_pool_bench`). That
file is committed round-1 evidence backing PROV-01/PROV-02 and the PR tables.
A tcp-pool re-run with changed builders silently replaces the round-1
numbers.
Catch: git diff on the results file after any bench run.
Mitigation: version the output filename for round 2 (or have the parent own
the overwrite at final integration); never re-run the bench casually.

R15 - Pool identity and phase labels (severity: MEDIUM).
`Key` (openai_http_client_pool.rs:29-34) does not encode builder options, so
adding options to `build()` is staleness-safe within a process (build is
deterministic). But the phase strings are load-bearing: they are asserted in
`acquisition_branches_and_phases` and `loopback_env_rotation_never_hands_out_a_stale_client`,
compared across rounds in the evidence tables, and streamed through the
observer (post_chat_completions 1864-1867, request_count in
openai_sglang_budget.rs:148). If tcp-pool restructures poolable()/phases()
or renames phases, round-1-vs-round-2 comparability and the PR narrative
break.
Catch: the pool unit tests above plus `no_proxy_coverage_matches_hyper_util_semantics`,
`no_proxy_coverage_matches_a_frozen_reqwest_matcher`,
`concurrent_loopback_acquisition_is_bounded`,
`bounded_eviction_age_runtime_and_redirect_keys`.
Mitigation: phase names are frozen API for this campaign; new behavior gets
new phase names, old names keep their meaning.

R16 - pool_idle_timeout / max-idle changes vs documented behavior (severity:
MEDIUM). Changing `POOL_IDLE_TIMEOUT` (shared_http.rs:23, 90s) or adding
`pool_max_idle_per_host` to Default changes: the f_idle_expiry scenario
(provider_transport_bench.rs:852-875, documents the 90s boundary, accepts 1
or 2 accepts after 95s - still passes but the documented claim in
EXPERIMENT-LEDGER PROV-06 and PR-DRAFT becomes stale); the reuse proof
(611-660, sequential 1 accept - still passes); and the concurrency block
("one connection per in-flight request" claim in provider-baseline.md).
Capping max-idle under c32 in-flight creates pool thrash (reconnect per
burst), measurable as a TTFT regression at levels 8/32.
Catch: re-run `provider_transport_bench_connection_reuse_proof` +
`provider_transport_bench_concurrency_levels` (e_concurrency) before/after;
update PROV-06/PR-DRAFT text with the new boundary.
Mitigation: any change here must re-measure c1/c8/c32 TTFT and idle expiry,
and the parent updates the ledger row rather than leaving round-1 text.

R17 - Environment-conditional options in shared_http (severity: HIGH design
risk). The shared CLIENTS map is process-lifetime with no environment
rotation (shared_http.rs:33-45), unlike http_client_pool's digest rotation.
If tcp-pool adds environment-reading builder options to shared policies
(proxy/TLS/no_proxy from env), the first build freezes them for the whole
process - exactly the staleness round 1 guards against with
`fresh_changed_policy`. The precedent for env-derived options is the Bedrock
policy, which resolves `force_http1`/`no_proxy` ONCE into the policy key
(amazon_bedrock.rs:2219-2233) so variants get distinct cache entries.
Catch: no test covers shared_http env rotation (by design there is none);
`aws_bedrock_force_http1_env_reaches_the_client_builder` covers only the
bedrock resolution.
Mitigation: shared builders stay environment-free; env-sensitive options
belong in http_client_pool's guarded path or a new ClientPolicy variant
resolved at call time like bedrock_client_policy.

R18 - Redirect-none clients blast radius (severity: LOW-MEDIUM).
Keepalive-style options are orthogonal to redirect policy, so RedirectNone /
Bedrock / Kiro / responses_transport opted-in clients are safe from
correctness changes; but any Default-policy change applies to all of them
via shared builders only where they share code - note responses_transport's
opted-in client (responses_transport.rs:115-130) hard-codes its own 90s idle
and does NOT go through shared_http; changing shared POOL_IDLE_TIMEOUT makes
the two redirect-none families diverge silently.
Catch: none asserts parity today.
Mitigation: if POOL_IDLE_TIMEOUT changes, either mirror it in
responses_transport or note the divergence in the PR description.

## 4. Cross-lane and campaign-process risks

R19 - Shared bench files, multiple writers (severity: MEDIUM).
sse_parse_bench.rs contains both the completions and responses arms;
completions-quad (R5), responses-quad (R11) and sse-bench-ext may all edit it
in one worktree. provider_transport_bench.rs is the tcp-pool measurement
surface while quad changes shift consumer timing measured in the same
scorecard (code-level coupling only; the serialized gate keeps runs
exclusive). EXPERIMENT-LEDGER.md and PR-DRAFT.md are multi-writer documents.
Mitigation: parent assigns per-file ownership for round 2 before edits land;
ledger edits are append-only rows (new IDs, e.g. PROV-09+), never edits of
round-1 rows; evidence JSON filenames versioned per round.

R20 - Consumer wins are JSON-dominated (severity: measurement honesty).
Round-1 evidence (SSE-01, sse-parse-bench.md) shows framing is ~1 percent of
full-consumer per-event cost; the partial_message clone is the dominant
non-JSON cost. A quad win is real but must be measured with the same
fixtures/labels as round 1 under the throttled gate, with best-of-N, and a
within-noise result reported as NO-CHANGE rather than claimed.

R21 - Nine agents, one worktree (severity: LOW, already mitigated by rules).
The audit found the working tree clean at HEAD; the only structural risk
left is bench/evidence file ownership (R19) and the R7 file-map mismatch.

## 5. Must-fix-before-final list

1. R7: resolve openai_responses_shared.rs ownership for responses-quad
   before its edits land; run azure + bedrock + codex responses tests with
   the change, not only the openai ones.
2. R13: no deadline-class builder option may land on shared clients; add
   the Debug-form absence assertion to shared_http tests in the same change.
3. R2/R3/R8/R9/R10: every quad must preserve flush-before-snapshot and
   late-usage/stop-reason patch points; extend the existing terminal-partial
   tests to assert the LAST event's partial equals the Done message content
   and usage (today only Done is asserted in places).
4. R12: keepalive claims must be stated as deltas from the reqwest defaults
   (TCP keepalive already on: 15s/15s/3); a no-delta result is NO-CHANGE.
5. R14: client_pool_bench must not overwrite round-1
   client-pool-bench.json; version the filename.
6. R15: phase label strings are frozen; new behavior needs new names.

## 6. Verification matrix (test -> risk)

| Existing test | Catches |
| --- | --- |
| large_completions_arguments_flush_on_success_and_failure | R1, R2 |
| incomplete_completions_fail_without_executing_partial_tool_calls | R2 |
| done_settles_held_open_body_after_usage_and_drains_each_event_once | R3 |
| cancellation_at_recognized_done_preserves_prior_usage_and_is_aborted | R3 |
| typed_and_value_paths_agree_on_weird_but_accepted_chunks | R1, R4 |
| consume_through_both_paths | R4 |
| completions_monitoring_records_raw_phases_and_inclusive_usage_without_changing_output | R3, R4 (observer path) |
| t15_stream_events_cover_text_reasoning_tool_usage | R1 |
| reasoning_details_round_trip | R1 (thought_signature, encode) |
| in_stream_error_event_fails_the_stream_and_keeps_partial_output | R2 error path |
| cancellation_closes_a_stalled_stream_and_preserves_partial_output | R13 early warning |
| large_tool_arguments_are_complete_on_end_error_and_early_eof | R9 |
| tool_call_blocks_persist_parsed_arguments_without_partial_json | R9 |
| reasoning_items_keep_summary_text_and_signature | R8 |
| refusal_marker_survives_deltas_or_done_items_and_successful_completion | R8 |
| on_output_item_done_runs_before_finalizing_the_block | R8 |
| incomplete_terminals_preserve_exact_reason_and_usage | R10 |
| service_tier_pricing_uses_the_resolver | R10 |
| responses_buffer_* (sse_parse_bench.rs) | R11 |
| acquisition_branches_and_phases / loopback_env_rotation... | R15 |
| provider_transport_bench_connection_reuse_proof | R16 |
| provider_transport_bench_idle_expiry (95s) | R16 |
| no_proxy_coverage_* | R15 regression net |
| force_http1_pins_the_client_to_http1 | R17 net |

Missing coverage worth adding by the lanes (test-only changes):
- shared_http Debug-form "no timeout/read_timeout" assertion (R13).
- completions: last-partial == Done content/usage equivalence (R3).
- responses: azure/bedrock/codex stream tests in the same run as the
  consumer rewrite (R7).

## 7. Sources read

git show 61384d695, aac27156b (full diffs); git show --stat for
159bc336d, ace61379e, f9d2a9af0, f56c151ed, 522913929; current tree of
openai_completions.rs, openai_responses.rs, openai_responses_shared.rs,
openai_http_client_pool.rs, shared_http.rs, responses_transport.rs,
openai_sglang_budget.rs (count preflight), provider_transport_bench.rs,
sse_parse_bench.rs, amazon_bedrock.rs (client-builder tests),
types.rs (AssistantMessage/ContentBlock/AssistantMessageEvent);
vendored reqwest 0.12.28 async_impl/client.rs defaults; Cargo.lock
(reqwest 0.12.28, hyper 1.11.1, socket2 0.6.5, hyper-util 0.1.20);
research/network_latency/ (ledger, PR-DRAFT, audits, provider/ipc evidence).
