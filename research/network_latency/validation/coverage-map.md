# Round-2 coverage map: partial-message snapshots + shared HTTP pool policy

Agent: coverage-auditor (read-only audit, no code edits, no gate use).
HEAD audited: 522913929 (improve/network-ipc-performance, tree clean at audit
start; the round-2 lanes had not landed edits yet, so every guard below is the
pre-round-2 baseline the lanes must not regress).

Scope: the three round-2 lanes.
- completions-quad: `openai_completions.rs` `partial_message` consumer
  (openai_completions.rs:2756-2760, called from 13 sites: 2114, 2145, 2195,
  2256, 2343, 2369, 2409, 2480, 2536, 2781, 2820, 2842, 2887, plus the
  `Start` clone at 2652-2654).
- responses-quad: the same pattern on the responses path, `partial:
  output.clone()` at every event push inside `process_responses_stream`
  (openai_responses_shared.rs:748, 758, 773, 809, 838, 857, 904, 936, 958,
  987, 1027, 1062, 1115; `Start` clone at openai_responses.rs:379-381).
- tcp-pool: `shared_http.rs` pool/keepalive policy (POOL_IDLE_TIMEOUT
  shared_http.rs:15, `ClientPolicy::builder` 61-83, policy-keyed cache
  `try_shared_client` 55-66).

The audited behavior contract (both quad lanes): every `AssistantMessageEvent`
carries a `partial` snapshot equal to `output` at that moment with `content`
replaced by the accumulated blocks (completions: `partial.content =
state.content()`, openai_completions.rs:1339-1348 maps `state.blocks` in block
order; responses: content lives in `output.content` directly). Downstream,
`pi-agent-core/src/agent_loop.rs:1440-1467` overwrites the context tail with
each `partial` and emits `MessageUpdate`, and `agent_loop.rs:545-573` rebuilds
the aborted message from a `partial`'s content/usage, so a wrong partial is
user-visible mid-stream, not only at `Done`.

## 1. Existing guards (behavior -> test, file:line)

### 1.1 SSE framing (round-1 change; not the quad-clone target, but adjacent)

| Behavior | Guard | Asserts | Sufficient for quad-clone fix? |
|---|---|---|---|
| Completions reader payload sequences | `reader_matches_reference_on_text_fixtures` sse_parse_bench.rs:537; `reader_matches_reference_on_reasoning_and_tool_fixtures` :543; CRLF/CR :549; UTF-8 :560; boundary splits :569; comments/empty :579; invalid UTF-8 :588; callback stop :597; tail drop :626; empty chunks :794 | byte-identical payload sequences old vs new reader across deterministic chunkings | No: reader only, no consumer, no partials |
| Responses `SseBuffer` framing | `responses_buffer_matches_reference_on_typical_bodies` sse_parse_bench.rs:737; split CRLF/UTF-8 :745; multi-data join :757; skip_lf empty-chunk :766 | byte-identical event sequences | No: framing only |
| `SseBuffer` unit behavior | `sse_buffer_accepts_crlf_and_preserves_split_unicode_immediately` openai_responses.rs:1394; mixed newlines :1403; splits frames :1411 | immediate event availability, join, [DONE] skip | No |
| Reader units | `sse_line_reader_*` openai_completions.rs:5311, 5322, 5334, 5354 | join/comments/terminators/callback stop | No |

### 1.2 Consumer + event semantics (the quad-clone surface)

| Behavior | Guard | Asserts | Sufficient? |
|---|---|---|---|
| Typed vs Value consumer parity | `typed_and_value_paths_agree_on_weird_but_accepted_chunks` openai_completions.rs:3539-3588 (harness `consume_through_both_paths` 3449-3537); duplicate keys :3590 | event JSON (partial included) equal across both arms, single chunks | No: BOTH arms call the same `partial_message`, so a change inside `partial_message` passes parity trivially; payloads are single-chunk so at most one block exists |
| Event kind sequence, multi-block final output | `done_settles_held_open_body_after_usage_and_drains_each_event_once` openai_completions.rs:3257-3299 | kinds order (:3290), final `output.content` blocks (:3284-3286), usage, task receipt; runs both `stream` and `simple` paths | Partially: order + final state yes; NO partial-content assertion |
| Multi-block e2e | `t15_stream_events_cover_text_reasoning_tool_usage` openai_completions.rs:5536-5620 | deltas, ToolCallEnd fields, Done stop_reason/usage; kinds presence only (:5612-5615), not order | Partially: same blind spot (no partials) |
| Mixed e2e with strict order | `stream_event_sequence_is_stable_for_mixed_fixture` sse_parse_bench.rs:815-952 | kinds + deltas + Done message response_id/usage (:923-924) | Partially: no partial assertions (all `partial` fields ignored in the match) |
| Tool-arg accumulation to END | `large_completions_arguments_flush_on_success_and_failure` openai_completions.rs:3798-3833 | ToolCallEnd `partial.content[0] == ContentBlock::ToolCall(tool_call)` (:3822) and final arguments, across finish/done/drop/error endings | Partially: THE ONLY partial-content assertion in pi-ai; single block, END event only |
| Partial output preserved on error/cancel | `in_stream_error_event_fails_the_stream_and_keeps_partial_output` openai_completions.rs:4002-4042 (:4032-4037); `cancellation_closes_a_stalled_stream_and_preserves_partial_output` :3934-4001 (:3988); `incomplete_completions_fail_without_executing_partial_tool_calls` :3782-3796 | terminal message content | No mid-stream partial assertions |
| reasoning_details encode/decode | `reasoning_details_round_trip` openai_completions.rs:5143; foreign signature rejection :5155 | helper round trip only | No: no stream-path coverage of the reasoning-details Thinking block |
| Responses event types + final output | `text_stream_emits_start_delta_end_and_maps_usage` openai_responses_shared.rs:1774-1832 (`drain_events` 2069-2075 records `event_type()` only); `reasoning_items_keep_summary_text_and_signature` :2154-2197; `tool_call_blocks_persist_parsed_arguments_without_partial_json` :2117-2152; `large_tool_arguments_are_complete_on_end_error_and_early_eof` :2093-2115; refusal :1834; `on_output_item_done_runs_before_finalizing_the_block` :2411 | event type sequences and FINAL `output.content`/usage | No partial-content assertion exists anywhere on the responses path |
| Consumer bench (perf, not correctness) | `bench_consumer` sse_parse_bench.rs:178-237; growth probe :330-341 | events/s, no-op sink, no content assertions | No (measurement only) |

### 1.3 shared_http pool/keepalive policy

| Behavior | Guard | Asserts | Sufficient? |
|---|---|---|---|
| Same-policy reuse | `shared_client_reuses_one_connection_across_calls` shared_http.rs:120-153 | 2 requests, 1 accept, Default policy | Yes for Default only |
| Every policy builds | `try_shared_client_returns_ok_for_every_policy` shared_http.rs:158-165 | Ok for all 5 policy variants | Build success only, no policy semantics |
| Shared vs fresh through real path | `provider_transport_bench_connection_reuse_proof` provider_transport_bench.rs:626-658 | accept counts 1 vs 1+20 | Yes for reuse-vs-fresh |
| Real-path SSE + errors | `provider_transport_bench_fixture_shapes` provider_transport_bench.rs:1031-1061 | 3 text deltas, phase presence, terminal count | Transport-level; no partial assertions |
| Idle expiry behavior | `provider_transport_bench_idle_expiry` provider_transport_bench.rs:857 (PROV-06, #[ignore]d 95 s run) | reconnect after idle | Slow-path only; not in default suite |
| Per-request credentials + phase observability | `reuse_headers_body_and_rotation` openai_speed_regressions.rs:163-223 (phases :184-185, connections :208-212); `ordinary_generation_keeps_upstream_pool_and_per_request_headers` :343-373 (:371) | `http_client_created`/`http_client_reused` phases, connection ids, per-request Authorization | Yes for credential isolation + upstream pool |
| Bedrock policy -> builder options | `force_http1_pins_the_client_to_http1` amazon_bedrock.rs:3701-3725; `aws_bedrock_force_http1_env_reaches_the_client_builder` :3728-3753 | ClientBuilder Debug contains `http1_only: true` | Covers Bedrock variant only |

## 2. Gap verdicts for the four probes

(a) Multi-block partial snapshots mid-stream (reasoning block then text then
tool calls): NOT COVERED. The only partial-content assertion in pi-ai is
`partial.content[0]` at ToolCallEnd (openai_completions.rs:3822, single
block). All multi-block tests (`done_settles` 3284-3286, `t15`, the e2e at
sse_parse_bench.rs:815) assert only the FINAL `output.content`/Done message.
On the responses path there is no partial-content assertion at all; the test
helper `drain_events` (openai_responses_shared.rs:2069) discards every
`partial`. A quad-clone fix that rebuilds content from a cached snapshot and
drops, duplicates, reorders, or freezes earlier blocks would pass the entire
current suite.

(b) Tool-call argument accumulation interleaved with text: FINAL state only.
`large_completions` (3798) streams tool fragments with no interleaved text and
asserts only the END partial; `done_settles`/`t15` interleave blocks but not
argument fragments. No test asserts a `ToolCallDelta` partial's arguments
mid-accumulation, and none interleaves text deltas between argument fragments.
On the responses side `large_tool_arguments` (2093) and
`tool_call_blocks_persist_parsed_arguments` (2117) assert final
`output.content` only; the delta-diff branch of
`response.function_call_arguments.done` (openai_responses_shared.rs:981-990)
has no partial assertion either.

(c) Block finishing then a NEW block starting (cache invalidation after
finish): NOT COVERED. Structural note: on the completions path blocks finish
only at end-of-stream (`finish_block` loop openai_completions.rs:2726-2728),
so this probe applies to the RESPONSES path, where `response.output_item.done`
closes a block mid-stream (current_block = None at
openai_responses_shared.rs:1029, 1064, 1111) and a later
`response.output_item.added` starts a new one; the reasoning variant also
REWRITES `thinking.thinking` from the joined summary (1009-1019). No test
asserts any partial AFTER an item-done event, so a stale-snapshot bug there is
invisible today.

(d) usage/response_id/reasoning_details surviving into partials: NOT COVERED.
Every fixture that carries usage delivers it in a terminal chunk with empty
choices (sse_parse_bench.rs:824, openai_completions.rs:3264, 5544), so the
only post-usage event is Done; no test asserts `partial.usage` or
`partial.response_id` on any event (the e2e asserts the Done message only,
sse_parse_bench.rs:923-924). No stream-path test exercises `reasoning_details`
at all (only encode/decode units at 5143/5155 and single-chunk typed/value
parity at 3577-3579); the redacted Thinking block it creates (2247-2273) and
its `thinking_signature` encoding never appear in an asserted partial.
On responses, usage arrives only at terminal `response.completed`, so (d) is
a completions-only concern.

Additional blind spot (cross-cutting): the typed/Value parity harness
compares two arms that share `partial_message`, so it cannot catch a
regression inside `partial_message` itself; and no test anywhere pins the
full event JSON (partial included) against a golden sequence.

## 3. Must-have new tests (per lane)

Placements respect lane file ownership: each list goes into the lane's own
test module in the same file it edits.

### 3.1 completions-quad (openai_completions.rs, mod tests near 3672 / provider_settlement_tests)

1. `partial_snapshots_match_a_golden_multi_block_event_sequence`
   Fixture: reasoning_content "think-1", "think-2"; content "Hel", "lo";
   tool_calls index 0 with arguments fragments `{"path":"a` then `b"}` with a
   text delta BETWEEN the fragments; finish_reason "tool_calls"; usage; DONE.
   Consume through the real `stream_openai_completions` (local SSE fixture
   server like `local_sse` at 3059) and serialize every event with
   `serde_json::to_value` (as `consume_through_both_paths` 3462-3468 does).
   Assert the FULL event JSON sequence equals a golden Vec<Value> written out
   by hand: expected kinds, content_index values, deltas, and partials. The
   golden partials must show, in block order, Thinking("think-1think-2"),
   Text("Hello"), ToolCall with incrementally parsed arguments
   (`{"path":"a"}` after fragment 1 via partial-json, `{"path":"ab"}` after
   fragment 2 - StreamingJsonAccumulator::append parses eagerly below 8 KiB,
   crates/pi-ai/src/utils/json_parse.rs:155-163). This single test catches
   wrong partial content, dropped/duplicated events, wrong order, and stale
   block state.
2. `text_delta_after_a_tool_call_reuses_the_first_text_block`
   Order: content "first", tool_call fragment, content "second". Assert the
   second TextDelta has `content_index == 0` and `partial.content` equals
   [Text("firstsecond"), ToolCall(...)] - block order, not event order. A
   rebuild-by-event-order or content_index renumbering regression fails here.
3. `usage_and_response_id_reach_every_later_partial`
   Chunk 1 carries id + usage AND a content delta in the same chunk; chunk 2
   carries another content delta. Assert every TextDelta partial from chunk 2
   has `usage.input == 10`, `usage.output == 5`, `response_id ==
   Some("chatcmpl-u")` (also `response_model` when the chunk model differs).
   Guards snapshot completeness of non-content fields.
4. `reasoning_details_partial_carries_the_redacted_signature_block`
   Stream `reasoning_details` [{type:"reasoning.text", text:"a"}], then a
   content delta, then `reasoning_details` [{type:"reasoning.text",
   text:"b"}]. Assert a mid-stream partial contains the redacted Thinking
   block (redacted == Some(true), thinking_signature == the encoded details)
   plus the Text block, and that the LATER partial's Thinking signature
   reflects both merged details (merge at 2214-2232 / 2437-2450). Guards the
   second-thinking-block creation path and the merge cache.
5. `start_partial_is_empty_and_done_matches_the_final_output`
   Assert the Start event partial has empty content and the Done message
   equals the final output (cheap completeness check for the 2652 Start
   clone).

### 3.2 responses-quad (openai_responses_shared.rs mod tests; covers azure/codex responses transitively)

6. `responses_partials_accumulate_blocks_across_items`
   Events via `event_stream` (1764): reasoning item (added, summary part
   added, summary_text.delta "th", item.done), message item (added,
   content_part.added, output_text.delta "He", "llo", item.done),
   function_call item (added, arguments.delta `{"x":` then `1}`, item.done),
   response.completed. Assert the second output_text.delta partial.content ==
   [Thinking("th"), Text("He")] and the second arguments.delta partial.content
   == [Thinking("th"), Text("Hello"), ToolCall{arguments {"x":1}}] (exact
   equality, AssistantMessage derives PartialEq). Closes probe (a)+(b) for
   the responses path.
7. `responses_partial_reflects_a_finalized_item_before_the_next_starts`
   After the reasoning item.done (which rewrites thinking.thinking to the
   joined summary, 1009-1019), the TextStart partial of the NEXT message item
   must contain the finalized Thinking block (post-rewrite text and
   signature), not the pre-done delta state. Closes probe (c): the
   cache-invalidation-after-finish case.
8. `responses_function_call_done_partial_carries_the_full_arguments`
   Stream arguments.delta `{"x":` then function_call_arguments.done with
   `{"x":1,"y":2}` (delta-diff branch 981-990): assert the ToolCallDelta
   emitted by the done branch has partial.content[tool].arguments == the full
   object and delta == `1,"y":2}`.
9. `responses_multi_item_partial_content_matches_done_message`
   After the full multi-item stream, assert the last pre-terminal partial
   (e.g. ToolCallEnd) has content equal to the final output.content - pins
   the invariant that partials converge to the terminal message.

### 3.3 tcp-pool (shared_http.rs mod tests)

10. `no_policy_ever_sets_a_client_builder_total_timeout`
    For Default, RedirectNone, both Bedrock variants: assert
    `format!("{:?}", policy.builder())` contains neither `timeout` nor
    `connect_timeout`. For Kiro: assert it contains `connect_timeout` (15 s
    is intended) and contains no bare total `timeout` field (check by
    removing the `connect_timeout` substring first). ClientBuilder Debug
    prints total `timeout` and `connect_timeout` when set (vendored reqwest
    0.12.28 async_impl/client.rs fmt_fields 2747-2806). This pins the
    documented invariant in shared_http.rs:9-13 that no shared client may
    impose one total deadline on every request using it.
11. `keepalive_retention_is_stated_and_testable`
    If the lane changes POOL_IDLE_TIMEOUT (15) or adds pool sizing: add a
    unit assertion that the constant equals the documented policy, plus (if
    the value becomes injectable) a fast reconnect test: request, idle past
    the retention, request -> 2 accepts; idle below it -> 1 accept. Today
    this behavior is only the #[ignore]d 95 s bench (PROV-06,
    provider_transport_bench.rs:857). Note: reqwest's ClientBuilder Debug
    does NOT print pool_idle_timeout or pool_max_idle_per_host, so the
    keepalive policy cannot be asserted via Debug; it needs the behavioral
    reconnect test or the constant check.
12. `distinct_policies_do_not_share_pooled_connections`
    Against one fixture server: shared_client(Default) request +
    shared_client(RedirectNone) request must open two connections (2
    accepts) while a second Default request reuses the first (still 2
    accepts). reqwest::Client has no PartialEq, so identity is asserted via
    accept counts. Guards the policy-keyed HashMap if the key or cache
    changes.

## 4. Honesty constraints for the lanes (from the audit)

- TCP_NODELAY is reqwest DEFAULT ON (vendored reqwest 0.12.28
  async_impl/client.rs:1668-1674). Never present enabling it as a win.
- The `partial_message` deep clone per event is semantic (each event's
  partial carries the full snapshot; the TypeScript did the same, audit
  sse-path.md and bench doc sse-parse-bench.md). Any fix must preserve the
  full-snapshot contract; the golden-sequence test above is the guard.
- The perf motivation is measured and documented: 6.0x per-event growth
  across a text-4KB-x200 stream (sse-parse-bench.md, measured under
  throttled gate (4 CPUs shared, -j 2, BelowNormal)); the quad-clone fix
  should re-run that probe for the after number, not re-derive it.
- `bench_consumer` (sse_parse_bench.rs:178) never drains its stream; if the
  fix changes event residency (e.g. CoW), keep the bench comparing like with
  like (events stay resident by design there).

## 5. Verdict

The round-1 guards are strong for framing (byte-identical reader equivalence)
and for terminal-state semantics, but they do not pin mid-stream partial
snapshots at all: one single-block END-event partial assertion
(openai_completions.rs:3822) and zero on the responses path. All four probes
(a)-(d) are open. The 12 tests above are the minimum the lanes should add;
tests 1, 6 and 7 are the three that directly catch the highest-risk
regression classes of the quad-clone fix (stale/dropped blocks in a
multi-block snapshot, and stale cache after an item finishes).
