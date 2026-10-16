# SELECTION-INTEGRATION — Adaptive OCC-1 vs OCC-2 selection (policy v1) + parent-applied integration patch

**Worktree:** `optimus-occ2opt-20261016` · **Date:** 2026-10-16 · **Mode:** offline, deterministic; no model calls, no network, no git commits.
**Mission sections 12 + 16.** New module `crates/pi-coding-agent/src/core/occ/selection.rs` (self-contained; `agent_messages.rs`, `subagent_runs.rs` and daemon files untouched by me). This document specifies the small parent-applied patch that wires it into the send seams after the arch lane merges.

## 1. What landed (owned files)

| File | Change |
|---|---|
| `crates/pi-coding-agent/src/core/occ/selection.rs` | NEW: `SelectionPolicy`, `SelectionContext`, `select_with_policy`, `DialectEvaluationRecord`, `validated_registry`, `validated_registry_with`, measured constants, 19 unit tests |
| `crates/pi-coding-agent/src/core/occ/mod.rs` | `pub mod selection;` + one doc bullet |
| `crates/pi-coding-agent/tests/occ2_selection_policy.rs` | NEW: 9 integration tests (production/dev behavior, validated registry, promoted-card selection, model restrictions, fixed-overhead table) |
| `.changes/occ2-adaptive-selection.md` | NEW: changelog fragment |

## 2. Policy v1 — decision order (all thresholds named constants)

| # | Condition (checked in order) | Decision | Constant |
|---|---|---|---|
| 1 | Listener unknown (`None` capability) | OCC-1 | — |
| 2 | No shared dialect version (`OccCapability::negotiate` = `None`) | OCC-1 | — |
| 3 | Negotiated dialect not in registry | OCC-1 | — |
| 4 | Record unevaluated AND policy refuses unevaluated (production) | OCC-1 | `MIN_VALIDATION_SAMPLES = 30`, `MIN_VALIDATION_FIDELITY = 1.0` |
| 5 | Card `speaker_models`/`listener_models` non-empty and exclude the pair | OCC-1 | — |
| 6 | Strike ledger non-empty for the pair (`strikes > 0`) | OCC-1 | — |
| 7 | `message_chars < SMALL_MESSAGE_CHAR_FLOOR` | OCC-1 | `SMALL_MESSAGE_CHAR_FLOOR = 220` |
| 8a | Expected per-message savings <= 0 | OCC-1 | model below |
| 8b | Codebook not in context AND `messages_remaining < ceil(CODEBOOK_TOKENS / savings)` | OCC-1 | `CODEBOOK_TOKENS = 161`, `EXPECTED_REMAINING_MESSAGES = 8` |
| 9 | otherwise | OCC-2 | — |

Savings model (linear, v1, both constants measured offline):
`savings = (1 - token_ratio) * OCC1_TOKENS_PER_CHAR * message_chars`, with `OCC1_TOKENS_PER_CHAR = 0.4336` (corpus: 153.1 tok / 353.1 B).

- Evaluated dialect: `token_ratio` = the record's `wire_tokens_vs_occ1`.
- Unevaluated + production: `CORPUS_REFERENCE_TOKEN_RATIO = 1.0923` (167.1/153.1 — symbolic loses tokens on prose; the economics gate can never pass, which is intended).
- Unevaluated + development: `SILO_REFERENCE_TOKEN_RATIO = 0.6032` (~76/126 tok — the key-dense profile the evolution harness tunes for), so the dev flag still exercises the wire end to end.

Break-even arithmetic (from CODEBOOK-COST.md): at SILO-profile savings ~50 tok/msg, `ceil(161/50) = 4` messages; at the corpus mean ~60.7 tok/msg, 3 messages. With the codebook amortized in context (arch's seam), gate 8b is skipped.

## 3. Fixed-overhead table (measured, Qwen2.5-0.5B tokenizer, offline)

| Overhead | Value | Where it applies | Enters selection? |
|---|---|---|---|
| sym-v1 codebook line | 602 B / 161 tok | injected into the listener prompt on every OCC-2 message today (`agent_session_message_prompt_body`) | yes: gate 8b |
| OCC-2 wire header | 22 tok (`OCC/1` = 4 tok) | per symbolic message | inside the measured token ratios |
| Symbolic payload (corpus) | 337.9 B / 167.1 tok vs OCC-1 353.1 B / 153.1 tok | per message | `CORPUS_REFERENCE_TOKEN_RATIO` |
| Symbolic payload (SILO profile) | 153.8 B / ~76 tok vs OCC-1 289.8 B / ~126 tok | per message | `SILO_REFERENCE_TOKEN_RATIO` |
| Dual-payload fallback | 2.09x wire chars (mean 707.0 vs 337.9 B) | wire/storage only; `split_occ2_dual_body` strips it from prompts | no |
| Decoder instructions (codebook restated per message) | = the 161-tok codebook | listener prompt today; removed by the amortization seam | yes: gate 8b |
| Listener scaffolding | 350 B / 211 tok | identical under OCC-1 and OCC-2 | no (cancels) |
| Model-side (reasoning, clarifications, latency) | MEASURE-ON-DGX | not modeled in v1 | no |

## 4. Dialect evaluation record + validated registry

`DialectEvaluationRecord { samples, listener_fidelity_mean, wire_tokens_vs_occ1, last_evaluated }` (daemon owns storage; the module reads it, never stamps time). A record is validated iff `samples >= 30 && listener_fidelity_mean >= 1.0`. Cards without a record fall back to their own `DialectMetrics` (both shipped dialects: `samples = 0`).

`validated_registry()` builds on `promoted_registry()` (sym-v1 + sym-v1-pt-pt-r1) and admits only validated cards. **Today it returns an empty registry** — both dialects are honestly unevaluated — so the production policy selects OCC-1 everywhere until offline evaluation data lands. The development policy (`allow_unevaluated = true` + SILO reference) preserves today's dev/test behavior while still enforcing the floor, strike and amortization guards.

## 5. Parent-applied integration patch (after the arch lane merges)

### 5.1 `crates/pi-coding-agent/src/core/agent_session/subagent_runs.rs`

Line 99-105 (S1 target seam) and 115-122 (S2 spawn seam) currently hardcode `allow_unevaluated = true`:

```rust
let registry = DialectRegistry::with_sym_v1();
match select_occ2_dialect(&OccCapability::here(), peer.as_ref(), &registry, true) {
    Some(dialect) => {
        Occ2SendPolicy::for_dialect(registry, dialect, DEFAULT_AGENT_MESSAGE_MAX_CHARS)
    }
    None => Occ2SendPolicy::disabled(),
}
```

Replace both with (S1 shown; S2 analogous with `Some(&local)`):

```rust
let registry = DialectRegistry::with_sym_v1();
let evaluations = <daemon evaluation store read, or &BTreeMap::new()>;
let context = SelectionContext {
    local: &OccCapability::here(),
    listener: peer.as_ref(),
    speaker_model: <local model id if known>,
    listener_model: <target model id if known>,
    registry: &registry,
    evaluations,
    message_chars: <estimated outgoing OCC-1 body size>,
    kind: <message kind or "TASK">,
    codebook_in_context: <arch amortization flag for this listener>,
    strikes: <Occ2StrikeLedger incidents for this pair>,
    messages_remaining: EXPECTED_REMAINING_MESSAGES,
};
let policy = if dev_flag { SelectionPolicy::development() } else { SelectionPolicy::default() };
match select_with_policy(&context, &policy) {
    SelectionDecision::Occ2 { dialect, .. } => {
        Occ2SendPolicy::for_dialect(registry, dialect, DEFAULT_AGENT_MESSAGE_MAX_CHARS)
    }
    SelectionDecision::Occ1(_) => Occ2SendPolicy::disabled(),
}
```

Notes for the applier:
- Keep `select_occ2_dialect` importable; it stays the negotiation-only helper. The new path adds cost gating on top of its negotiation result (the selector performs the same `negotiate` internally, so call it INSTEAD of `select_occ2_dialect`, not in addition).
- The strike input: `Occ2StrikeLedger::incidents(pair)` (already public in agent_messages.rs) for the (speaker, listener) pair.
- `message_chars`: the OCC-1 body length before wrapping (`body.chars().count()` of the outgoing contract text); for the S2 spawn seam use the spawn prompt length.
- dev_flag: today's effective behavior is the development policy; production behavior (OCC-1 until evaluation) is the new default and should ship behind the existing `agentCommunication.occ2Enabled` semantics.

### 5.2 `crates/pi-coding-agent/src/core/agent_messages.rs` (line ~721, `select_occ2_dialect`)

The tier-2 gate `let evaluated = card.metrics.samples > 0 && card.metrics.round_trip_fidelity >= 1.0;` becomes reachable once callers stop hardcoding `allow_unevaluated = true`. After the patch lands, EITHER:

(a) keep `select_occ2_dialect` unchanged and route all send seams through `select_with_policy` (recommended: one decision path), or

(b) make `select_occ2_dialect` delegate: `allow_unevaluated` stays as the dev flag, but the metrics check becomes `DialectEvaluationRecord::from_metrics(&card.metrics).is_validated_with(MIN_VALIDATION_SAMPLES, MIN_VALIDATION_FIDELITY)` so the two gates cannot drift apart.

Either way the strike-ledger and cost gates live in `selection.rs` only; agent_messages.rs keeps negotiation.

### 5.3 Daemon evaluation store (daemon lane, later)

`DialectEvaluationRecord` values keyed by `(dialect_id, version)`. `validated_registry_with(&records)` is the constructor to call at send-seam build time. Until a store exists, pass an empty map: both dialects stay unevaluated and production stays OCC-1.

## 6. Round-trip / behavior evidence

- 20 unit tests in `selection.rs` cover every branch: unknown listener, no shared dialect, unregistered, unevaluated (production refuse / dev pass), model-pair mismatch and match, strike, small-message floor (0/1/219 chars), corpus-ratio no-savings, codebook-not-amortized, codebook-amortized, validated selection, low-fidelity refusal, validated-registry emptiness, break-even arithmetic, reason display, record fallback.
- 9 integration tests in `tests/occ2_selection_policy.rs` exercise the same over real registries (sym-v1 + promoted `sym-v1-pt-pt-r1`), pin the measured constants, and assert the promoted card selects OCC-2 only when evaluated.

## 7. Gate counts

| Gate | Result |
|---|---|
| `cargo test -p pi-coding-agent --test occ2_selection_policy` | **9 passed, 0 failed** |
| `cargo test -p pi-coding-agent --lib occ::` | **242 passed, 1 failed** — the failure is `core::occ::session_settings::tests::occ_session_resolution_reports_provenance_and_round_trips`, sibling-owned (session_settings.rs, 107-line diff), pre-existing and reproducible in isolation; `selection`: 20/20 green |
| `cargo test -p pi-coding-agent --test occ2_dialect_promotion` (regression) | **8 passed, 0 failed** |
| `cargo test -p pi-coding-agent --test occ2_comm_fidelity` (regression) | **27 passed, 0 failed** (sibling grew this suite from 19 to 27 mid-campaign) |
| `cargo test -p pi-coding-agent --test occ_comm_fidelity` (regression) | **7 passed, 0 failed** |

Concurrent-edit note: `agent_messages.rs` / `modes/interactive/components/agent_message.rs` were mid-edit by the arch/daemon lanes during gating; two short waits for their files to settle were needed before the lib gate could run. No file outside my ownership was modified.


---

## 8. APPLIED note (occ2-arch, 2026-10-16, selection wiring task)

Status: sections 5.1, 5.2(a) and 5.3 LANDED, with one deliberate deviation (below). Files:

| File | What landed |
|---|---|
| `core/occ/selection.rs` | serde derives on `DialectEvaluationRecord` (the store's record type) - the only edit to the module; the pure selector is untouched. |
| `core/agent_messages.rs` | `occ2_send_policy_from_selection(context, experimental)` - the one decision assembly (production policy first; the experimental `/occ 2` opt-in additionally runs the development policy ONLY when production refused with `UnevaluatedDialect`; a validated selection or any measured refusal is final). Eval store: `occ2-evals.json` (schema `optimus.occ2-evals.v1`, atomic write, fail-open bounded read, `load_occ2_evaluations` / `save_occ2_evaluations` / `occ2_evaluations_path`). `occ2_strike_pair_incidents` (decay-applied) for the selection input. Trait `occ2_send_policy(target, message)` now receives the body; default impl unchanged (disabled). |
| `core/agent_session/subagent_runs.rs` | Both seams rebuilt on a shared `occ2_selected_send_policy`: real `SelectionContext` (peer capability from the listing; registry = `promoted_registry()` falling back to sym-v1; speaker model from the session's model identity; evals + strikes from the sender's artifacts; `message_chars` = body/prompt length; `codebook_in_context = false` - the sender cannot cheaply know the listener's transcript, so the amortization gate decides via `messages_remaining = 8`). S1 resolves the listener's canonical session id from the listing so the pair key matches the delivery-side key. |
| `tests/occ2_selection_wiring.rs` | NEW, 7 tests (w01-w07): production default OCC-1 with empty store; experimental path selects OCC-2 (with and without in-context codebook); strike-disabled pair OCC-1 even experimental, read from a real persisted ledger; validated record flips production to OCC-2 through the real store; store round-trip + fail-open; validated-but-losing record stays OCC-1 on both policies; small-message floor. |
| `.changes/v0.1.34-occ2-selection-wiring.md` | Changelog fragment. |

Deviation from section 5.1: the seams pass `experimental = true` (they run only behind the `/occ 2` opt-in, i.e. `occ2_enabled`), NOT a second flag. Consequence: the production policy's OCC-2 branch (validated economics) and the dev fallback are both reachable from the seams; a production-only mode (no experimental) exists in `occ2_send_policy_from_selection(.., false)` and is what w01/w04 exercise. Rationale: the `/occ` toggle is the only opt-in surface today; making it mean "experimental" preserves the current observable behavior for the toggle while the new gates (floor, strikes, economics, validation) make it strictly safer.

Remaining (not landed, for the owning lanes):
- `DaemonAgentMessageController` (daemon_mode.rs) still has no `occ2_send_policy` override and keeps the trait default (disabled) - pre-existing, unchanged by this task. Daemon-bridged sends therefore stay OCC-1 until that controller forwards to the daemon-side selection.
- `listener_model` is `None` at both seams (the roster summary carries no model identity); model-pair restrictions on listener_models cannot fire from the seams yet. Speaker model IS wired.
- `activity.rs` telemetry still re-decodes bodies instead of reading `details["occ2Delivery"]` (strike task follow-up).
- The eval store is per-sender-session; a daemon-owned shared store is the natural v2 (the loader API is unchanged either way).

Gate evidence (target `optimus-occ2opt-target-w4`):
| Gate | Result |
|---|---|
| `--test occ2_comm_fidelity` | 27 passed / 0 failed |
| `--test occ_comm_fidelity` | 7 passed / 0 failed |
| `--test occ2_selection_policy` (codebook regression) | 9 passed / 0 failed |
| `--test occ2_selection_wiring` (new) | 7 passed / 0 failed |
| `--lib occ::` | 242 passed / 1 failed - `session_settings::occ_session_resolution_reports_provenance_and_round_trips`, occ2-daemon's in-flight test, pre-existing; `occ::selection` 20/20 green |
| `--lib communication_` | 16 passed / 0 failed |
| `--lib agent_messages` | 27 passed / 0 failed |
