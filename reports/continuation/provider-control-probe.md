# Native provider-control feasibility probe

## Scope

New example: `crates/pi-coding-agent/examples/memory_provider_probe.rs`.
It makes one native `pi_ai::stream::stream_simple` call to explicit
`dgx-glm53` / `glm-5.3`. The only prompt is the fixed synthetic request
`Reply with exactly READY.` There is no arbitrary prompt/evaluation input.
There are no AgentSession, extension, Jev, memory, import, or search calls.
No production file, dependency, or manifest is changed.

## CLI

```text
memory_provider_probe --config ABS_CONFIG --scratch-dir NEW_ABS_DIRECTORY
  --output NEW_ABS_FILE --max-tokens 1..1024
  [--thinking off|unspecified] [--timeout-seconds 1..90] --allow-network
```

- `--thinking` defaults to `off`. `unspecified` is an explicit comparator.
- The timeout defaults to 90 seconds. It bounds async auth and provider work.
  Local provider settlement uses at most 250 ms of the remaining budget.
- All three paths are explicit and absolute. Parent directories must exist.
- Scratch must be new. Output uses `create_new`; existing attempts are refused.
- Without `--allow-network`, execution fails with `network_not_allowed` before
  config reads, scratch creation, auth resolution, or runtime/provider startup.
- Exit 0 means a terminal non-error result. Inspect `stop_reason` and
  `text_is_exact_ready` separately. Exit 2 means a stable failure category.
- There is no request retry or alternate-provider selection.

Only the parent may authorize and run a network invocation. The worker ran none.

## Isolation and native reasoning control

The program hashes the exact original config bytes using SHA-256. It extracts
only the `providers.dgx-glm53` entry into a transient scratch `models.json`.
The target model must occur exactly once in that entry's explicit `models`.
The selected provider must have a nonempty explicit `apiKey` reference. The
resolved endpoint must use HTTPS and contain no URL userinfo. These checks
precede native auth resolution. Other provider entries are not copied or loaded. This is **not** a claim that
the registry loaded the original document unchanged.

Native `ModelRegistry::create` eagerly loads definitions and stores request
key/header references in memory (`load_custom_models`, `parse_models`, and
`store_provider_request_config`). The transient file is removed immediately
after construction, before auth resolution. This probe never refreshes the
registry. Scratch auth starts as `{}` and is opened with the native
`FileAuthStorageBackend` and `AuthStorage::from_storage`, with Prime CLI config
disabled. It avoids `AuthStorage::create`'s default-profile-path comparison.
No installed model/auth/profile fallback is used. Explicit authorized helper
references remain in memory and are resolved only after the allow-network gate.
No helper reference is inspected or executed by these synthetic tests.

The selected API must be `openai-completions`. Model reasoning capability,
thinking-level maps, provider/model compatibility, and role settings are not
modified. Explicit off fails before auth if reasoning capability is false,
`off` is absent from native supported levels, or native
`clamp_thinking_level(model, "off")` would choose another level. Supported-level
membership also rejects an all-null map whose clamp falls back to `off`.

`SimpleStreamOptions` has no `reasoning_enabled` member. Supported native off
is `reasoning: Some("off")`. The native simple adapter maps it to
`OpenAICompletionsOptions { reasoning_enabled: Some(false), reasoning_effort:
None, ... }`. Unspecified uses `reasoning: None`. No invented extra-body
fields or numeric thinking budgets are used.

## Content-free JSON (`memory-provider-probe/v1`)

- Identity: `provider`, `model`, `config_sha256` (exact original bytes).
- `requested`: thinking selector, intended nullable `reasoning_enabled`, null
  `reasoning_effort`, cap, timeout. These are requested controls, not acceptance.
- `payload`: actual native `on_payload` whitelist, or null before observation:
  `max_tokens`, `max_completion_tokens`, allowlisted scalar `reasoning_effort`,
  boolean `enable_thinking`, enum `thinking.type`, boolean
  `chat_template_kwargs.enable_thinking` / `preserve_thinking`, and
  `message_roles`. Unknown roles become `unknown`. Other values are omitted.
- `payload_observation_count`: useful for the one-payload invariant, not a
  count of remote generations or an HTTP-acceptance guarantee.
- `status`: stable local category only. Raw errors and diagnostics are not logged.
- `stop_reason`: `stop`, `length`, `toolUse`, `error`, `aborted`, or `unknown`.
  It is null without a terminal message.
- `text_scalar_count` and `text_is_exact_ready`: terminal text blocks joined
  without separators; count is Unicode scalars, not bytes/tokens; comparison
  uses `.trim() == "READY"`. Neither text nor its hash is emitted.
- Reasoning: terminal `reasoning_block_count`, `reasoning_scalar_count`, and
  `reasoning_block_scalar_counts`. No reasoning/signature content is emitted.
- `usage`: nullable `input`, `output`, `reasoningTokens`, `totalTokens` from
  `on_usage_observation` only. `usage_source` is null or `provider_observation`;
  `usage_observation_count` counts callbacks, not tokens.
- `elapsed_ms`: local elapsed time, including setup.

Usage follows the native observer schema: outer `None` retains the previous
field, `Some(None)` clears it, and `Some(Some(n))` stores finite nonnegative
values, including zero. Observations are never summed. No normalized
`AssistantMessage.usage`, output-length estimate, or derived total is used.
Missing counters remain null on errors/cancellation. Without a terminal,
text/reasoning counts and the exact-match flag also remain null.

Successful payload emission or READY text does not prove that a server honored
thinking control. Toggle dialects can emit the same disabling field for off
and unspecified. Missing raw reasoning usage is unknown, not zero. Local
cancellation is not proof of remote cancellation. Native blocking credential
helpers retain their own process limits; runtime shutdown does not wait for a
blocked helper or claim that its descendants stopped. No provider dispatch
occurs after the auth future times out. Abrupt process failure can leave a
reserved empty output; it must not be overwritten.

## Offline validation handoff

Frozen example file SHA-256: `c4c6675d0c1114d029615c3efba6fc93d03a036f211d31e37449a67c131ecd5d`.

Private `rustfmt --edition 2021` passed. No Cargo/build/test command was run by
this worker, per the parent-owned serial-build restriction. No live call,
credential/profile read, dependency install, or commit was performed.

Nine native example tests cover gate-before-IO and numeric bounds; preserving
existing attempts; native explicit registry isolation, config preservation and
transient cleanup; all-null thinking maps and unsafe endpoints; missing explicit
auth; latest-present nullable usage; whitelist/error redaction;
an actual registered fake provider with exactly one dispatch and payload,
including success/error/cancel/timeout cases; and actual native payload
serialization for off/unspecified and supported dialects. The native payload
test holds `on_payload` pending and cancels owned tasks before the transport
line is reachable. It does not contact even a local HTTP server.

Parent commands (use the existing private environment and temporary root):

```text
cargo test --locked --offline -p pi-coding-agent --example memory_provider_probe -- --test-threads=1
cargo build --locked --offline -p pi-coding-agent --example memory_provider_probe
```

The broader `bash scripts/check.sh` rule is deferred to the parent alongside
focused compilation/tests. Offline tests establish wiring only, not DGX backend
behavior. Config variants and any subsequent network authorization are external.
