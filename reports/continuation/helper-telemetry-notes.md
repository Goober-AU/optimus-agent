# Recall helper telemetry follow-up

## Scope and compatibility

Only `crates/pi-coding-agent/src/core/extensions/builtin/memory.rs` changes product code. This report is the other owned file.

The change adds optional observations to existing `prime-agent.memory-diagnostic` custom session entries. It preserves `enabled`, `attempted`, and every existing `outcome` value. Older diagnostic readers can ignore the new keys. Older entries can omit all new keys.

**Classification: backward-compatible custom-entry metadata, not a daemon wire/schema change.** `diagnostic()` calls `ExtensionApi::append_entry`. The action in `core/agent_session/runtime_members.rs` only calls `SessionManager::append_custom_entry`. That method persists an opaque `serde_json::Value` under the existing `data` key. No daemon command, event, response type, capability, protocol version, or schema revision changes. A future typed daemon exposure would need a separate protocol review.

## Agreed fields

Each entry's `data.recallHelpers.queryDistillation` and `data.recallHelpers.rerank` retains the three old fields and adds:

| Field | Meaning |
| --- | --- |
| `stopReason` | Terminal assistant message's normalized stop reason: `stop`, `length`, `toolUse`, `error`, `aborted`, or allowlisted `unknown`. Null if no terminal message was observed. Never copies `stopReasonRaw`. |
| `emittedTextChars` | Unicode scalar count of untrimmed text blocks in the terminal message, including partial error/aborted text. Excludes reasoning and tool blocks. Zero means an observed terminal message had no text. Null means no terminal message was observed. This is not a token count or a count of unseen streamed deltas. |
| `latencyMs` | Monotonic local elapsed milliseconds from the single dispatch through terminal-result delivery or local cancellation. Includes synchronous dispatch work. Excludes auth, search, and rerank parsing. Null if no dispatch occurred. |
| `usage.input` | Provider-observed input counter, or null. |
| `usage.output` | Provider-observed output counter, or null. |
| `usage.totalTokens` | Provider-observed total counter, or null. Never computed from input/output. |
| `usage.reasoningTokens` | Provider-observed reasoning token counter, or null. Never inferred from output or empty text. |
| `usageSource` | `provider_observation` when at least one native usage callback was observed; otherwise null. This does not imply all counters are known. |
| `cancelled` | Observed local cancellation-token state. True for local cancellation, false when checked and not cancelled, null when no check occurred (including cache reuse). A provider `aborted` result can have `cancelled=false`. |
| `providerError` | Whether an observed terminal result has an error message or `stopReason=error`. Null without a terminal result. The error message itself is never recorded. |

No prompt, query, completion, reasoning content, raw error, model identifier, credential, header, or raw provider stop string is added to diagnostics.

## Usage presence and cancellation

The existing `StreamOptions.on_usage_observation` callback supplies `ProviderUsageObservation`. It distinguishes absent fields, explicit unknowns, and reported values. The callback records only numeric fields, synchronously, in state owned by this one helper dispatch. It returns a no-op future and adds no request payload settings.

- Finite nonnegative values are preserved, including explicitly reported zero.
- Each field keeps its **latest explicit observation**. Absent fields retain the previous observation. Explicit null or invalid values clear that field to null.
- Repeated callbacks are snapshots, not extra requests or deltas. They are never summed.
- No callback or unsupported provider means unknown usage, not zero.
- `AssistantMessage.usage` is not a fallback. Its required zero defaults do not prove a provider reported usage; its total may be computed.
- Output and reasoning counters can overlap. Do not add reasoning tokens to output tokens or treat this report as a cost ledger.
- Local cancellation takes a nonblocking snapshot. Usage observed before cancellation can be partial. Null `stopReason`, `emittedTextChars`, and `providerError` preserve the lack of a terminal result.
- If a terminal result was already delivered when cancellation won the select, the helper records its true normalized stop reason and text length but still discards its text for recall. It does not fabricate `aborted`.
- No cancellation acknowledgement or late usage is awaited. Reported usage is only the observation available at helper return, not proof of final remote billing or final provider termination.

## Attempts and cache reuse

`attempted=true` still means exactly one `stream_simple` dispatch for that helper. False means no dispatch. There are no retries or new attempt counters.

The existing top-level `cacheHit` and helper `outcome=cache_hit` distinguish repeated context events. Cached enabled helpers retain `enabled=true` and `attempted=false`. All new observations are null on cache reuse (the `usage` object contains four null counters). Cached calls do not repeat usage or latency. Consumers must not sum cached or duplicate session entries. An overall failed recall diagnostic without helper reports does not establish zero helper usage.

## Preserved behavior

- Default settings, selected models, reasoning settings, and 64/512 helper output budgets are unchanged.
- Disabled helpers do not dispatch.
- Each enabled helper has at most one dispatch; extraction retry behavior is unchanged.
- Query, lexical-recall, empty-output, provider-error, aborted, and invalid-selection fallbacks are unchanged.
- Rerank still uses only the transmitted top 20 records and rejects ambiguous/cross-scope/duplicate IDs.
- No driver, store, settings, or Jev code is changed.

## Offline runtime coverage

The tests load the real memory extension, bind an `ExtensionRunner`, and dispatch through a registered synthetic provider. They use isolated temporary memory stores and synthetic auth. They perform no real model/network/credential/profile access.

New cases cover both helpers' length-plus-empty, normal empty, provider-error, and provider-aborted outcomes; terminal-vs-local-cancel distinction; Unicode/whitespace text counts; privacy allowlisting; no observed usage versus explicit zero; reasoning counters; repeated snapshots; omitted fields; explicit null; invalid/NaN/infinite counters; no normalized-usage fallback; no fabricated totals; and local cancellation without a terminal reply but with partial usage. Existing cancellation/cache tests also assert nullable fields and exact attempted counts without duplicating cached observations. Existing runtime safety assertions remain.

Parent-owned validation command:

```text
cargo test --locked -p pi-coding-agent --lib core::extensions::builtin::memory::recall_runtime_tests -- --test-threads=1
```

Set `TEMP`, `TMP`, and `TMPDIR` to `C:\Users\openclawuser\optimus-memory-bench-20261007-040424\tmp-continuation` and use the parent's configured native Rust environment. The parent owns all Cargo/build execution. This worker did not run Cargo or a live provider.

Validation status at CODE READY:

- Native `rustfmt --edition 2021 --config skip_children=true` parsed/formatted a temporary full-file copy successfully. Changed production/test spans match that native output. Unrelated pre-existing formatting was left untouched.
- Scoped `git diff --check` passed with no output. Both owned files use LF.
- Static comparison confirms the complete existing recall selection/context/cache-key/settings/refinement/command region is unchanged. All 18 existing runtime tests remain; four new runtime tests bring the focused suite to 22.
- Cargo/runtime execution is pending the parent's serial test run. No Cargo/builds were run by this worker.
