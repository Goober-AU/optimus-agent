# Driver helper observability

## Scope

Changed only `crates/pi-coding-agent/examples/memory_bench.rs` and this report.
The driver consumes the additive custom recall diagnostics from the helper worker.
No provider calls, network, credentials, installed profiles, Cargo/builds, or commits were used by this worker.
`TEMP`, `TMP`, and `TMPDIR` point to `C:\Users\openclawuser\optimus-memory-bench-20261007-040424\tmp-continuation`.

The original security/frozen/NoJev gate prefix and all 25 original tests remain unchanged against this worker's entry baseline.
Main/helper model budgets, reasoning options, settings, identity, gold-free constraints, single-use claims, hashes, no-learning checks, and explicit-model requirements remain unchanged.

## Accepted helper schema

Input: `data.recallHelpers.queryDistillation` and `.rerank` in the existing memory recall custom entry.
Output: `memory_feature_realization.llm_helper_observations.recallQueryDistillation` and `.recallRerank`.

| Field | Accepted observation |
| --- | --- |
| `enabled`, `attempted` | Exact booleans; existing required fields |
| `outcome` | Existing bounded outcome vocabulary; unchanged |
| `stopReason` | `stop`, `length`, `toolUse`, `error`, `aborted`, `unknown`, or null |
| `emittedTextChars` | Unsigned integer Unicode scalar count, including observed zero; no text copied |
| `latencyMs` | Finite nonnegative number, including zero |
| `usage.input`, `.output`, `.totalTokens`, `.reasoningTokens` | Nullable finite nonnegative provider-observed counters, including explicit zero |
| `usageSource` | Only `provider_observation`, otherwise null/unavailable |
| `cancelled`, `providerError` | Boolean or null; local cancellation is not inferred from provider abort |

Optional fields are validated separately. Missing additions do not erase legacy helper state.
Malformed values become unavailable. Unknown strings and extra fields are not copied.
No completion, error message, reasoning text, or query is copied by helper telemetry consumption.
The old normalized-assistant usage proposal is **not** implemented: counters require the presence-aware provider source.
No total is synthesized from input/output. Reasoning usage is not inferred from output.

Cache outcomes or `data.cacheHit=true` suppress all new-call telemetry, even if stale values were attached.
Unattempted rows cannot contribute terminal/text/latency/provider-error/usage evidence.
An explicitly observed local cancellation can survive a pre-dispatch cancellation.

## Coverage and accounting

- Snapshot the branch before the question and exclude historical entries afterward.
- Deduplicate current custom-entry envelopes by nonempty entry ID.
- Keep ID-less observations without treating them as unique calls; the pre-question prefix is excluded.
- Preserve each helper's observation, including partial usage without a terminal response.
- Collect already-persisted diagnostics before answer-error or timeout returns. In-flight helper data may remain unavailable.
- Record per-helper observation counts and counts for each token field under `llm_helper_usage_coverage`.
- Counts describe available observations, not token totals or billable request counts.
- Status is `unobserved`, `partial`, `no_dispatch_reported`, or `reported_attempts_have_token_fields`.
- Even the last status covers reported terminal attempts only. It does not prove that every provider call was observed.
- Every new question row gets `usage_coverage`, including non-frozen rows and returned error rows.
- Helper token/latency aggregates and total-model tokens remain null. `cost_complete` remains false.
- Main answer usage remains the existing normalized last-assistant-message snapshot, explicitly not presence-aware or cost-complete.

No diagnostic values are summed. Cache or historical observations cannot inflate a helper token total.
Null/absent means unavailable, not zero. Observed provider zero remains zero.
Existing append-only/resume behavior and old records remain unchanged.

## Offline validation

Eight new synthetic tests are under the `helper_telemetry_` filter:

- `helper_telemetry_keeps_allowlisted_fields_and_observed_zero`
- `helper_telemetry_rejects_wrong_types_and_unknown_stop_reasons`
- `helper_telemetry_requires_provider_source_and_never_infers_usage`
- `helper_telemetry_keeps_partial_cancelled_and_terminal_error_evidence`
- `helper_telemetry_cache_and_unattempted_rows_never_replay_call_usage`
- `helper_telemetry_excludes_history_deduplicates_entry_ids_and_never_sums`
- `helper_telemetry_coverage_never_promotes_missing_or_partial_diagnostics`
- `helper_telemetry_question_coverage_stays_cost_incomplete_on_success_or_error`

The source now contains 33 tests: original 25 plus these eight.
No test was run by this worker; parent owns serial native validation after both source files freeze.
Suggested parent command in its approved native environment:

```text
cargo test --offline --locked -p pi-coding-agent --example memory_bench -- --test-threads=1
```

Checks performed:

- Native `rustfmt.exe --edition 2021 --emit stdout crates/pi-coding-agent/examples/memory_bench.rs`: exit 0 (parse/format generation).
- Applied only changed diagnostic/test spans from the formatter output. Retained unrelated baseline formatting and normalized source line endings to LF.
- Static comparison: original gate prefix and all 25 original test bodies unchanged.
- Final `git diff --check -- crates/pi-coding-agent/examples/memory_bench.rs reports/continuation/driver-helper-observability.md`: exit 0, no output.
- No Cargo tests or builds run here. Native tests/full checks are pending parent validation; this cut is not released.

## Frozen source

SHA256: `ebb6fac97863f7101d153ef5d17fa3faa88e795fd3e068a5e63131e3846c5892`.
