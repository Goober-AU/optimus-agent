# IMPL-CODEBOOK-AMORT — OCC-2 codebook amortization (as-built)

Worker: occ2-arch. Date: 2026-10-16. Worktree: `optimus-occ2opt-20261016` (branch `improve/occ2-efficiency`), target dir `optimus-occ2opt-target-w4`.
Offline, deterministic: no model calls, no network, no git commits, no repo-wide greps.

## 1. Design as-built

**Goal.** `agent_session_message_prompt_body()` (agent_messages.rs:796-816 pre-change) injected the full 602-char sym-v1 codebook legend into every strict-decode OCC-2 body, and the legend was baked into each stored transcript record's content — O(M*T) prompt growth and the measured +32% total prompt tokens. The implementation amortizes the legend to one full injection per codebook identity per listener context.

**Identity (design item 2).** `Occ2CodebookId` (agent_messages.rs:828-842) = dialect id + version + the leading 12 hex chars (`CODEBOOK_PREFIX_LEN`) of the codebook sha256 — the exact identity the OCC-2 wire header already carries (`OCC/2 D=sym-v1@1 C=1efce9992ee2`). Any change to id, version or codebook content produces a different identity and re-injects (t15).

**Legend / reference (design item 1).**
- `occ2_codebook_legend(card)` (agent_messages.rs:855-869): the full legend, now prefixed with its identity — `OCC-2 sym-v1@1 codebook (sha256:1efce9992ee2): <17 slot mappings>. Indexed fields use 1-based indices; suffixes: <9 subfield mappings>. Values follow '='; …` (626 chars).
- `occ2_codebook_reference(identity)` (agent_messages.rs:871-883): `OCC-2 sym-v1@1 codebook (sha256:1efce9992ee2) previously provided in this conversation; decode rules unchanged.` (111 chars).
- `parse_occ2_codebook_legend(line)` (agent_messages.rs:885-916) parses identity back from a **legend line only**; a reference line does not parse, so a surviving reference without its legend is not treated as presence.

**Presence derivation (design items 3, 4, 5 — compaction/restart/model-change safety).** `occ2_codebooks_present(messages)` (agent_messages.rs:918-941) scans the listener's live message list: every `agent_message` custom record whose rendered content contains a parseable legend line contributes that identity. Presence is **derived from what is actually in the context**, never from a volatile flag:
- Compaction/removal of the legend-bearing record removes the identity; the next delivery re-injects (t16). A summarizer that drops the record loses the legend, and the next render pays the full legend again — correct by construction, no hook needed.
- Restart/replay re-derives the same single identity from the stored transcript (t17).
- Model change / context rebuild: these operations rebuild context from the message list, so the scan reflects whatever survived; if the rebuild keeps the records, presence holds, otherwise it re-injects. No synthetic lifecycle flags were invented — the message list IS the hook.
- Both seams record as `agent_message` custom messages (delivered messages via `create_agent_session_message*`, spawn prompts via the spawn seam's custom message), so one scan covers both.

**Render core (design item 8 — redundant work removed).** `render_agent_session_message_prompt_body` (agent_messages.rs:961-1006) splits the dual body once, strict-decodes once as the validity gate, then renders legend-or-reference + symbolic. The fallback branch consumes the already-split embedded OCC-1 section (decoding it to verify, mirroring `occ2::occ1_fallback` semantics) instead of re-splitting. `occ2_body_dialect_card` (agent_messages.rs:943-959) resolves the body's advertised card from its wire header — after a strict decode succeeded this cannot miss.

**Public surface.**
- `agent_session_message_prompt_body(message)` (agent_messages.rs:802): unchanged signature, now always-teach (delegates with an empty presence set). The spawn seam keeps its per-spawn semantics for free (child context starts empty) and stays byte-identical for a first exposure.
- `agent_session_message_prompt_body_with_codebooks(message, &codebooks)` (agent_messages.rs:1008) and `..._with_registry_and_codebooks` (agent_messages.rs:1021) for delivery and tests.
- `create_agent_session_message_prompt_with_codebooks` (agent_messages.rs:1131) / `create_agent_session_message_with_codebooks` (agent_messages.rs:1192): prompt text and durable record built from the same presence set, so the delivered text and the stored content match.

**Delivery wiring.** `daemon_mode.rs` `accept_agent_session_message` (single delivery site in the daemon): derives `codebooks = occ2_codebooks_present(&self.session_of(target_state).messages())` from the target session's live messages and calls the two `_with_codebooks` builders. Presence is computed per delivery, so cleared/restarted/compacted targets are always re-scanned.

**Spawn seam (design item 6).** No change to subagent_runs.rs: the spawn seam's `agent_session_message_prompt_body(&dual_prompt)` remains the stateless always-teach render (correct: a fresh child has an empty context), and the spawn record is an `agent_message` custom message whose content carries the legend, so `occ2_codebooks_present` sees it and the first delivered message references instead of re-teaching (t18). No double injection.

**Behavior preserved (design item 7).** Strict decode, `#FALLBACK-OCC1` sentinel, three-strike ledger, telemetry classification (`activity.rs` untouched), wire format, and the `occ2Enabled=false` byte-identical path (the render gate `starts_with(OCC2_HEADER)` is unchanged; with the flag off no OCC-2 bodies exist) are all untouched. Fallback/passthrough renders are presence-independent (t19).

## 2. Exact hunks (files + ownership)

Owned files (mine alone):
- `crates/pi-coding-agent/src/core/agent_messages.rs` — imports (line 20: +`DialectCard`, +`CODEBOOK_PREFIX_LEN`); new amortization block agent_messages.rs:816-1032; builders 1131-1209 (doc-commented wrappers; the legacy `create_agent_session_message*` keep their signatures and delegate with an empty presence set, so every other caller — tests, activity, session list — is unchanged).
- `crates/pi-coding-agent/tests/occ2_comm_fidelity.rs` — module doc t13-t19 (lines 36-43); import block (lines 48-64); new tests t13-t19 (lines 1025-1471).

Out-of-ownership edit (minimal, 2 hunks in `modes/daemon/daemon_mode.rs`, as pre-declared in EXPERIMENT-LEDGER.md):
1. Import block (daemon_mode.rs:55-61): added `create_agent_session_message_prompt_with_codebooks`, `create_agent_session_message_with_codebooks`, `occ2_codebooks_present`; removed the two now-unused plain builders from that import list.
2. `accept_agent_session_message` (daemon_mode.rs:15686-15690): presence derivation + the two `_with_codebooks` calls replacing the plain ones. Nothing else in the function changed.

No other files were touched by this worker.

## 3. Test evidence (deterministic, offline)

New tests in `tests/occ2_comm_fidelity.rs` (19 total in the binary now):
- t13 first-full-then-reference: cold render carries the legend whose sha prefix matches the wire header; the record reports presence; the warm render carries the reference only, and the reference line does not parse as a legend; prompt and record agree; prompt-id round-trips.
- t14 compactness + identity pinning: legend > 550 chars, reference < 200 and < 1/4 of the legend; identity = sym-v1@1 + 12-hex prefix = head of the card sha256; stateless render deterministic and always-teach.
- t15 dialect switch re-injects: same dialect references; a different dialect (`sym-b@1`) re-injects full; both-present references both; a re-tuned sym-v1@1 (different codebook sha) is a different identity.
- t16 compaction simulation: legend-bearing record removed by a filter that keeps the reference-bearing record; presence is empty; next delivery re-injects full.
- t17 replay/restart: 8 deliveries derive presence per delivery → exactly 1 full legend + 7 references; presence re-derived after "restart" from the stored transcript is the same identity; the 9th delivery still references; every record still carries its dual body verbatim.
- t18 spawn seam: the spawn prompt (stateless render, always-teach) counts as presence; the first delivered message references — no double injection.
- t19 fallback and passthrough: unknown dialect, hash-mismatched header, plain text, plain OCC-1 and broken OCC-2 render identically with and without presence.

Pre-existing tests all stay green (t01-t12 in occ2_comm_fidelity; t01-t07 in occ_comm_fidelity; the agent_messages unit tests, including `occ2_prompt_uses_valid_fallback_on_bad_dialect_and_teaches_valid_symbols` which pins the stateless always-teach behavior).

## 4. Gate results

| Gate | Command | Result |
|---|---|---|
| 1 | `cargo test -p pi-coding-agent --test occ2_comm_fidelity` | **19 passed / 0 failed** (12 pre-existing + 7 new) |
| 2 | `cargo test -p pi-coding-agent --test occ_comm_fidelity` | **7 passed / 0 failed** |
| 3 | `cargo test -p pi-coding-agent --lib occ::` | **222 passed / 1 failed** — the failure is `core::occ::session_settings::tests::occ_session_resolution_reports_provenance_and_round_trips` at session_settings.rs:220, in the occ2-daemon worker's in-flight file (their set_preferences rename; not this worker's code; my entire `core::occ::*` and agent-adjacent surface passed) |
| 4 | `cargo test -p pi-coding-agent --lib communication_` | **16 passed / 0 failed** |
| 5 (extra) | `cargo test -p pi-coding-agent --lib agent_messages` | **27 passed / 0 failed** |

Concurrent-tree status: gates were run while the occ2-daemon and occ2-codebook workers were landing their own changes (session_settings.rs, dialect_load.rs, daemon protocol files). At gate time the lib and all test binaries compiled; the single gate-3 failure is the sibling's test, not a compile error and not this change.

## 5. Cold-vs-warm token math (updated from CODEBOOK-COST.md)

Anchors from CODEBOOK-COST.md (Qwen2.5-0.5B tokenizer, 69-msg corpus): old legend 602 ch / 161 tok; symbolic mean 167.1 tok/msg; OCC-1 mean 153.1 tok/msg. New sizes are char-exact from the implementation; token values scale by the measured 3.74 ch/tok of the legend class (labeled ~derived).

| Item | Before | After |
|---|---|---|
| Cold (first OCC-2 body) | codebook 161.0 tok + symbolic = 328.1 | legend ~167.4 tok (626 ch) + symbolic = **~334.5** (+6.4 tok, the identity prefix) |
| Warm (every later body) | 328.1 | reference ~29.7 tok (111 ch) + symbolic = **~196.8** |
| Warm saving | — | **-131.3 tok/msg (-40.0%)** |

Session message-content totals (listener scaffolding identical in all variants and cancels):

| n msgs | OCC-1 | OCC-2 before | OCC-2 after | saved vs before | saved vs OCC-1 |
|---:|---:|---:|---:|---:|---:|
| 2 | 306 | 656 | 531 | 125 (19%) | -225 |
| 5 | 766 | 1,641 | 1,122 | 518 (32%) | -356 |
| 10 | 1,531 | 3,281 | 2,106 | 1,175 (36%) | -575 |
| 36 (SILO run) | 5,512 | 11,812 | 7,374 | 4,438 (38%) | -1,862 |

- Corpus profile: amortized OCC-2 remains above OCC-1 in tokens (the symbolic payload itself is +14.0 tok vs OCC-1 — second-order, dialect-design scope), but the per-message codebook overhead drops from 161.0 to ~29.7 tok.
- SILO traffic profile (symbolic saves ~63 tok/msg vs OCC-1 per CODEBOOK-COST.md): warm body ~119.8 tok vs OCC-1 153.1 (**-21.8%/msg**); session breakeven vs OCC-1 at n >= ~5 messages (was: never).
- Re-injection events (compaction removed the legend, dialect switch, fresh spawn) cost one extra legend, not one per message.

**What this does and does not fix.** The per-message codebook injection — the primary suspect for the +32% total prompt tokens — is gone from the delivered stream. Remaining known costs, out of scope here: the symbolic payload's own token overhead on corpus-like traffic (dialect design), the dual body's ~2x wire/storage duplication (fallback stripping after a symbolic ACK), and the redundant per-send negotiation round trips (selection-path caching).
