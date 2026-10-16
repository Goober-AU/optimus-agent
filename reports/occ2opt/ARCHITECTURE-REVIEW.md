# OCC-2 Architecture Review (efficiency campaign)

Reviewer: architecture-review child agent (read-only). Date: 2026-10-16.
Worktree: `C:/Users/openclawuser/optimus-occ2opt-20261016`, branch `improve/occ2-efficiency`, base `1863b5dea` (main v0.1.33 incl. #155/#158/#159/#160/#161).
Method: read only the 15 assigned files; no repo-wide search, no builds, no model/network calls. Every claim below cites `file:line` from those files or from the two campaign reports also in scope. Benchmark anchor (SILO-BENCH Level I, n2, glm-5.3-flash, from FINAL-REPORT.md:89/92 and GAP-AUDIT.md:181-183): OCC-1 191,634 prompt tokens / 97 calls / case p50 43.5 s vs OCC-2 sym-v1 253,864 tokens (+32.5%) / 88 calls / p50 65.6 s (+51%), while inter-agent wire payloads shrank 289.8 -> 153.8 B/msg. This report maps where that inversion comes from in the code.

---

## 1. Codebook repetition map (primary suspect confirmed)

### 1.1 The injection site

`agent_session_message_prompt_body()` — `crates/pi-coding-agent/src/core/agent_messages.rs:796-816`:

- `798-800`: gate — only bodies starting with `OCC/2` (`OCC2_HEADER`) take the OCC-2 path; everything else is passed through byte-identical.
- `801`: constructs a **fresh `DialectRegistry::with_sym_v1()` on every call** (no cache; includes the sha256 codebook hash re-computation path, `dialect.rs:544-549`).
- `802`: runs a **full strict `occ2::decode`** of the dual body (which decodes the symbolic section *and* re-parses the embedded OCC-1 fallback and compares them, `occ2.rs:254-278`) — used only as an `is_ok()` validity check; the decoded value is discarded.
- `803`: `split_occ2_dual_body` (`agent_messages.rs:780-793`, a private re-implementation of `occ2.rs:824-837`) keeps the symbolic section.
- `804-808`: **the codebook injection**. Comment at 804 says "The model needs the codebook even when this is its first OCC-2 task"; lines 806-807 render all 17 slot mappings (`symbol:name`, BTreeMap order) and all 9 subfield mappings; line 808 prepends:

  `OCC-2 sym-v1 codebook: G:artifact, D:dependency, E:evidence.ref, X:expiry, F:finding.statement, K:kind, M:message_id, N:next_action, P:parent_task_id, B:recipient, A:sender, Q:sequence, S:status, T:task_id, C:timestamp, R:trace_id, V:verification.claim. Indexed fields use 1-based indices; suffixes: evidence.digest:d, evidence.line_range:l, evidence.revision:r, evidence.verified:v, finding.confidence:c, finding.evidence:e, verification.method:m, verification.receipt:r, verification.status:s. Values follow '='; evidence references are comma-separated. Reply using OCC-1; transport handles encoding.`

  Measured locally from the sym-v1 table (`dialect.rs:285-314`): **602 chars, 48 words** — the campaign's own measurement is ~200-300 model tokens per injection (FINAL-REPORT.md:228, "a dialect card costs ~200-300 tokens in context").
- `810-814`: if strict decode fails but `occ1_fallback` succeeds, the render is the embedded OCC-1 section only (no codebook).
- `815`: a body with the OCC/2 header that decodes neither way is passed through raw (model sees sentinel + both sections).

### 1.2 Callers (every one)

| Caller | Site | Frequency |
|---|---|---|
| `create_agent_session_message_prompt()` | `agent_messages.rs:948` (function 911-950) | Once per **delivered** agent-to-agent message; output becomes the stored transcript `content` |
| `create_agent_session_message()` | `agent_messages.rs:952-970` (content at 965) | Wraps the above with header lines (`[from …]`, `Agent-to-agent message received.`, `Source/From/To/Message id`); this `CustomAgentMessage` is appended to the recipient transcript |
| Spawn seam (S2) | `subagent_runs.rs:556-558`: `occ2_spawn_send_policy()` -> `wrap_outgoing_body(&current.prompt, …)` -> `agent_session_message_prompt_body(&dual_prompt)` | Once per RLM child spawn whose prompt starts with `OCC/1`; `content = "[task from parent]\n\n{codebook+symbolic}"` (559), full dual kept in details (565) |
| Tests | `agent_messages.rs:1865-1871`; `tests/occ2_comm_fidelity.rs:687-692` (t06) | Test-only |

There is **no other injection site**: the codebook text does not appear in any system prompt, daemon protocol, or skill file in scope. `agent_session_message_prompt_body()` is the single choke point.

### 1.3 How often per message / per session, and what state exists

- **Per message**: every valid OCC-2 message rendered into a prompt gets one fresh 602-char codebook line. The function is pure (`&str -> Cow<str>`); it takes no session, no cache, no "already taught" flag. **No state anywhere prevents repeat injection.** The comment at `agent_messages.rs:804` frames this as intentional ("even when this is its first OCC-2 task") but the implementation applies it to *every* message, not the first.
- **Per session / per turn**: the codebook is baked into the stored `CustomAgentMessage` content at delivery time (`agent_messages.rs:965`). Transcript replay then re-feeds that content to the model on **every subsequent turn**, so a session with M delivered OCC-2 messages carries M codebook copies in the prompt from then on — **O(M·T) cumulative token growth** (M messages × T remaining turns). With ~250 tok per codebook this matches the SILO delta: +62,230 prompt tokens over 36 inter-agent messages (≈1.7k tokens of accumulated codebook+symbolic overhead per message across remaining turns). The 7-9-message break-even computed in FINAL-REPORT.md:228-230 ("~32 tokens/message saved") assumes the card is paid **once per session**; the code pays it **once per message**, so the amortization never happens.
- The model is told (`agent_messages.rs:808`) "Reply using OCC-1; transport handles encoding" — i.e. the recipient never needs to *encode* symbolically, only to read it. A full 17-slot + 9-subfield encoding legend per message is overhead beyond reading comprehension; a one-line reminder after first exposure would suffice.

---

## 2. Dual-payload analysis

### 2.1 Where the dual payload is built

- `occ2::encode_dual()` — `occ2.rs:232-248`: symbolic section (`encode_symbolic`, `occ2.rs:376-…`) + `\n#FALLBACK-OCC1\n` (sentinel const `occ2.rs:70`, 16 B) + **complete OCC-1 wire text** (`message.to_occ1()` at 237 -> `contract.rs:386-459` -> `occ1.rs:220-257`).
- Wire headers: OCC-2 `OCC/2 D=sym-v1@1 C=<12 hex>` ≈ 28 B (`occ2.rs:383-389`, `CODEBOOK_PREFIX_LEN=12` at `dialect.rs:51`) vs OCC-1 `OCC/1` 6 B (`occ1.rs:28,227`).
- Built at the send seams by `wrap_outgoing_body()` (`agent_messages.rs:749-775`): requires `OCC1_HEADER` prefix (756), full `Occ1Message::decode` + `AgentCommMessage::from_occ1` (759-762), registry card lookup (765), `encode_dual` (768), and the R1 transport-bound guard (771-773: dual over 16,384 chars (`DEFAULT_AGENT_MESSAGE_MAX_CHARS`, `agent_messages.rs:57`) is never sent — plain OCC-1 instead, never truncated).

Send call sites: direct arm `agent_messages.rs:1405-1406`; broadcast arm `1311-1315` (per recipient, each with its own policy); spawn seam `subagent_runs.rs:557`.

### 2.2 Where it is stored and re-inflated

- **Durable transcript (incoming)**: `create_agent_session_message()` keeps the **full dual verbatim** in `details.message` (`agent_messages.rs:958`) *and* stores the rendered prompt (codebook + symbolic) as the message `content` (965). Both live in the same `CustomAgentMessage`; see t06/t08 assertions (`tests/occ2_comm_fidelity.rs:687-701, 787-801`, "dual body stored verbatim").
- **Durable transcript (spawn)**: `subagent_runs.rs:565` stores `dual_prompt` in the custom-message details `"message"` field; the visible `content` (559) holds codebook + symbolic.
- **Receipts**: `create_agent_session_message_receipt()` echoes `payload.message` (the dual) again (`agent_messages.rs:1023`).
- **Re-inflation**: `agent_session_message_prompt_body()` re-decodes on every render (see 1.1); the telemetry path `activity.rs:57-73` (`classify_with_registry`) strict-decodes yet again per admitted message (bounded to last 128 transcript entries, `activity.rs:12,84`).

### 2.3 Per-message cost stack (OCC-1 = 1.0x baseline)

| Layer | Content | Size vs OCC-1 | Evidence |
|---|---|---|---|
| Wire (what SILO counts as inter-agent bytes) | symbolic section only, as rendered into the recipient prompt? No — the wire body **is** the dual; SILO's 153.8 B/msg tracks the symbolic-section payload class | 0.53x on SILO msgs (153.8 vs 289.8); 0.957x aggregate on the 69-msg corpus (23,316 vs 24,361 B) | GAP-AUDIT.md:181-183; FINAL-REPORT.md:57-58, 92 |
| Durable storage per delivered message | `details.message` (dual) + `content` (codebook 602 B + symbolic) | dual ≈ 1.6-2.0x + codebook+symbolic ≈ 0.5-1.0x -> **~2.5x** OCC-1 per message | agent_messages.rs:958/965; FINAL-REPORT.md:226-227 "a dual body is ~2x OCC-1" |
| Recipient prompt per turn | codebook (602 B ≈ 200-300 tok) + symbolic, for **every historical OCC-2 message, every turn** | ~250 tok + ~32 tok per message vs OCC-1's ~90 tok/msg class — net **worse per message even before replay**; O(M·T) cumulative | agent_messages.rs:804-808; FINAL-REPORT.md:228-230 |
| CPU per render | full dual decode (symbolic + OCC-1 fallback + equality check) discarded, then re-split; registry + sha256 rebuilt per call | 82.3 us decode + 137.5 us encode measured (within budget, but paid per message per render, 2-3x per send+render+telemetry round trip) | occ2.rs:254-278; agent_messages.rs:801-803; FINAL-REPORT.md:59 |

The wire saving is real but small in absolute terms (-136 B/msg on SILO); the prompt-side codebook adds ~250 tok per message per turn — three orders of magnitude larger in token terms. The dual fallback section is insurance against listeners without the dialect; every recipient that *does* have sym-v1 still stores and re-parses it.

---

## 3. Strike ledger call-site audit

- Definition: `Occ2StrikeLedger` + `OCC2_STRIKE_LIMIT = 3` — `agent_messages.rs:818-862`. Pure in-memory `BTreeMap<String, usize>` + `BTreeSet<String>`; no I/O (doc comment 822-823: "Session state, no I/O; the runtime recorder wiring is a daemon-side follow-up").
- The **only** place a strike is incremented: `decode_agent_session_message_body()` — `agent_messages.rs:893-909`, specifically 902-904 (`Err(_) => ledger.record_incident(pair)`); pair-disable is consulted at 899 (`!ledger.is_disabled(pair)`).
- **Production callers: none.** Grepping the assigned runtime files (`agent_messages.rs` non-test, `subagent_runs.rs`, `daemon_protocol.rs`, `occ_control.rs`, `activity.rs`, `session_settings.rs`), `decode_agent_session_message_body` is referenced only by tests: `agent_messages.rs:1875-1901` and `tests/occ2_comm_fidelity.rs:799, 807, 865-886` (t08/t10). The daemon never decodes delivered bodies — the receiving prompt path validates via `agent_session_message_prompt_body` (which counts no strikes) and telemetry classifies via `activity.rs` (also no strikes). This matches FINAL-REPORT.md:242-243: "the three-strike ledger is pure and tested but has no runtime recorder yet (daemon never decodes bodies today)".
- **Does the 3-strike disable work at real call sites?** The mechanism is correct in isolation (`record_incident` 847-856 returns true at >= 3; disabled pairs skip straight to fallback at 899-908, verified by t10 at `occ2_comm_fidelity.rs:853-886`), but since no production code path invokes it, **no pair is ever disabled in reality**. A receiver that cannot strict-decode still gets OCC/2-rendered prompt bodies forever (fallback render at `agent_messages.rs:810-814` is the only production mitigation, and it never feeds back into the sender's policy).
- **Restart/compaction behavior**: not persisted. t08 models restart by constructing a **fresh ledger** (`occ2_comm_fidelity.rs:806-808`, "A separate ledger models the restarted session's state") — i.e. strikes reset to zero on every daemon/session restart. The durable dual body itself survives compaction (t08: encode -> serialize -> deserialize -> decode, 787-809), and `activity.rs:82-100` re-derives classification from stored `details.message`, but ledger counts are lost. No serde on `Occ2StrikeLedger`, no artifact-file write, no session-state field carries it.

---

## 4. Dialect selection path

- `DialectRegistry::with_sym_v1()` — `dialect.rs:594-600`: inserts the single built-in card `DialectCard::sym_v1()` (`dialect.rs:280-338`). That card ships with `DialectMetrics::default()` (`dialect.rs:335`): `samples = 0`, `round_trip_fidelity = 0.0` — i.e. **unevaluated by its own schema** (`dialect.rs:214-227`: "Defaults mark the dialect as unevaluated, not as good").
- `select_occ2_dialect()` — `agent_messages.rs:721-736`: negotiates via `OccCapability::negotiate` (`dialect.rs:708-727`, highest shared version), looks up the card (729), then the tier-2 gate: `evaluated = card.metrics.samples > 0 && card.metrics.round_trip_fidelity >= 1.0` (730) — `allow_unevaluated` bypasses it (731). Doc (714-720): "Model-pair and task-family matching on evolved cards is a daemon-side follow-up."
- `occ2_target_send_policy()` (S1, per-target sends) — `subagent_runs.rs:80-106`: checks the chat flag `occ2_enabled()` (84), then **calls `controller.list_agents()` on every send** (90) to find the peer's advertised `occ_capability` (94-98, from `AgentSessionMessageAgentSummary.occ_capability`, `agent_messages.rs:186-190`), then calls `select_occ2_dialect(&OccCapability::here(), peer, &registry, /* allow_unevaluated */ **true**)` at **100** — the dev flag is **hardcoded true in production**. Registry rebuilt per call (99).
- `occ2_spawn_send_policy()` (S2, spawn seam) — `subagent_runs.rs:111-123`: negotiates `OccCapability::here()` against itself (same-build child), again with `allow_unevaluated = true` hardcoded at **117**, registry rebuilt per call (115).
- **What is unevaluated-permitted**: the entire production surface. Because the built-in card has `samples = 0`, the `evaluated` branch at `agent_messages.rs:730` is never true; every runtime negotiation relies on the bypass. There is no code path anywhere in scope that sets `DialectMetrics` on a registered card — no evaluation feed, no metrics file loader, no daemon card store.
- **What is missing for validated promotion** (per code comments + GAP-AUDIT):
  1. A metrics source: fill `DialectCard.metrics` (dialect.rs:216-227) from an evaluation run; the campaign's `eval.py` was never run (GAP-AUDIT.md §2.4: no `eval-results.json`; 10-cell pair matrix planned, 5 implemented, 0 executed).
  2. Holdout fidelity 1.0 per (speaker, listener, family) triple required by the S4.5 tier-2 enablement policy — never measured (GAP-AUDIT.md §4.2: "with no holdout numbers that policy can never fire").
  3. Model-pair / task-family matching on evolved cards (`agent_messages.rs:718-720` comment; `speaker_models`/`listener_models`/`task_types` fields exist at `dialect.rs:262-269` but are consulted by no runtime code).
  4. A session-length / break-even heuristic — FINAL-REPORT.md:228-230: "break-even is ~7-9 structured messages per session. Sessions with fewer should stay OCC-1 (selection-policy input, not yet implemented)".
  5. Flip the hardcoded `true` at `subagent_runs.rs:100,117` to a real gate once 1-4 exist.

---

## 5. `/occ` session-settings flow (and the PR #158 finding)

Flow for `/occ [status|1|2 on|2 off]` — `crates/pi-coding-agent/src/modes/interactive/occ_control.rs:30-55`:

1. `parse()` (12-21) accepts the modes; 36-38 gates on `connection.supports_occ2_communication()` (schema-40 + `Occ2Communication` capability; `daemon_protocol.rs:78-81`, compat test 3136-3156).
2. `connection.get_state()` (39) yields the TUI-side `session_file` (40).
3. Read: `session_settings::load(session_file)` (41) — `session_settings.rs:25-61`: reads `<artifact-dir>/occ.json` (`preference_path`, 18-20, via `crate::core::session_manager::get_session_artifact_path_for_file(session_file, None)`), else walks `parentSession` ancestry (up to 64 hops, 28) reading bounded JSONL headers (48-58); depth-0 forks never inherit (52-55). Missing = off; load errors fail closed (`enabled()`, 64-66).
4. Write: `session_settings::save(session_file, requested)` (occ_control.rs:53; save at `session_settings.rs:68-80`, atomic tempfile in the **same client-side artifact directory**).

Daemon-side consumption: `AgentSession::occ2_enabled()` — `subagent_runs.rs:70-72` — reads `session_settings::enabled(self.session_file())`, i.e. the **daemon process's** artifact path for the daemon-owned session file. The send policies consult it at `subagent_runs.rs:84, 112`.

**Where the PR #158 P1 manifests now**: the write in occ_control.rs:39-53 and the read in subagent_runs.rs:70-72 resolve the "session artifact dir" in **two different processes**. `get_session_artifact_path_for_file(session_file, None)` (session_settings.rs:18-20) resolves against the invoking process's session-manager root (the TUI's default profile). When the connection targets a daemon session with a foreign `agentDir` (client attached to another profile, or session created with an explicit agentDir), `/occ 2 on` writes occ.json into the **client-local** directory while the daemon's session keeps reading its **own** occ.json — the toggle reports success, subsequent messages stay OCC-1, and an unrelated local profile's directory gains a stray occ.json. This is the same class as the original review finding (client writing local `SettingsManager`/settings.json instead of the daemon-owned settings); PR #159 moved the storage from global settings.json to per-chat occ.json but kept it a **client-side filesystem write with no daemon command** — nothing in `daemon_protocol.rs` exposes an occ-preference get/set (the command surface at 2221-2235 has none), so the daemon-owned-storage fix the review asked for is still open.
**PR #159's second finding also stands**: occ_control.rs:36's capability check passes for an old schema-40 daemon that advertises `occ2_communication` but predates occ.json semantics — a new client writes occ.json the old daemon never reads, and an old client writes settings.json the new daemon ignores; both directions report success with no effect. The storage-semantics change shipped without a distinct capability or schema revision.

---

## 6. Ranked gap list (top 10)

1. **[codebook] Per-message codebook re-injection, no dedup state.** `agent_messages.rs:804-808` — every OCC-2 message rendered into a prompt prepends the 602-char (~200-300 tok) legend; the function is pure and no session state tracks "already taught". Stored in transcript content (965) and replayed every turn: O(M·T) prompt growth. Root cause of +32.5% prompt tokens (191,634 -> 253,864). Fix direction: inject once per session (first OCC-2 message or system preamble), then a one-line "OCC-2 sym-v1 as previously defined" reminder.
2. **[codebook] Verbose legend for a read-only consumer.** `agent_messages.rs:806-808` — the legend teaches 17 slots + 9 subfields in long form, yet line 808 itself says "Reply using OCC-1; transport handles encoding": the model only needs to *read* the symbolic section. A compressed legend (symbol:name pairs only, or a versioned reference) cuts most of the 250 tok/message.
3. **[dual-payload] Dual stored verbatim plus rendered content in the same transcript entry.** `agent_messages.rs:958` (details.message = dual) + `:965` (content = codebook+symbolic); spawn twin at `subagent_runs.rs:559/565`. ~2.5x OCC-1 durable bytes per delivered message; the OCC-1 fallback section (16 B sentinel + full OCC-1, `occ2.rs:232-248`) is pure insurance re-stored and re-parsed even for capability-confirmed peers. No path strips the fallback after the recipient ACKs symbolic decode.
4. **[selection] Tier-2 evaluated-card gate is dead code in production.** `subagent_runs.rs:100` and `:117` hardcode `allow_unevaluated = true`; the built-in card is unevaluated by construction (`dialect.rs:335` samples=0), and no runtime path sets `DialectMetrics`. The `evaluated` branch (`agent_messages.rs:730`) can never fire; the design's "production requires an evaluated card" (subagent_runs.rs:77-79) is not enforced.
5. **[ledger] Strike ledger unwired at every real call site.** Only `decode_agent_session_message_body` increments (`agent_messages.rs:902-904`) and only tests call it (1875-1901; `occ2_comm_fidelity.rs:799/807/865`); the daemon never decodes bodies (FINAL-REPORT.md:242-243), telemetry (`activity.rs:57-73`) bypasses the ledger, and the ledger is not persisted — strikes reset on restart (t08 restarts with a fresh ledger, `occ2_comm_fidelity.rs:806-808`) and survive compaction only as re-derivable classification, not counts.
6. **[daemon-compat] `/occ` writes client-local storage for daemon-owned sessions.** occ_control.rs:39-53 + `session_settings.rs:18-20` (client-side artifact path, `None` agent-dir) vs `subagent_runs.rs:70-72` (daemon-side session file) — foreign agentDir => toggle is a silent no-op (PR #158 P1, still unfixed post-#159); plus no distinct capability gates the occ.json semantics, so old schema-40 daemons pass occ_control.rs:36 while never reading occ.json (PR #159 finding; `daemon_protocol.rs:78-81`).
7. **[selection] Per-send negotiation round trips and per-call registry rebuilds.** `occ2_target_send_policy` fetches the full family listing per send (`subagent_runs.rs:90-98`), the send handler fetches the roster again (`agent_messages.rs:1309/1371`), and the broadcast arm repeats the policy lookup per recipient (`1311-1315`) — O(recipients) listing round trips per broadcast; `DialectRegistry::with_sym_v1()` is reconstructed (with validation) at `subagent_runs.rs:99/115` and `agent_messages.rs:801`. No negotiated-dialect cache per target. Contributes to the +51% median case latency alongside the token growth.
8. **[other] Double decode per render; validation decode discarded.** `agent_session_message_prompt_body` runs the full strict dual decode (symbolic + fallback + equality, `occ2.rs:254-278`) at `agent_messages.rs:802` and discards the result, then re-splits the body (803); a failed strict decode attempts `occ1_fallback` again (810). Every send+render+telemetry cycle parses the same payload 2-3 times (also `activity.rs:65-69`).
9. **[other] Spawn seam only fires for OCC-1-structured prompts.** `wrap_outgoing_body` requires the `OCC1_HEADER` prefix (`agent_messages.rs:756`), and deltas are excluded in v1 (OQ4; asserted `occ2_comm_fidelity.rs:620-622`). Plain-English task briefs — the common RLM spawn case — pass through unwrapped (`subagent_runs.rs:557` is a no-op for them), so S2 savings rarely materialize and mixed sessions pay both formats.
10. **[selection] No break-even / session-shape heuristic.** FINAL-REPORT.md:228-230: the card amortizes only after ~7-9 structured messages (saving ~32 tok/message vs OCC-1); the selection path (`select_occ2_dialect`, `agent_messages.rs:721-736`) has no message-count, session-length, or expected-turnover input, so short sessions take a guaranteed loss under OCC-2. Combined with gap 1 (per-message instead of per-session card cost), the loss is unbounded.

---

### Evidence boundary

Read-only review of the 15 assigned files; `agent_session.rs`, `session_manager.rs`, kernel prompt assembly, and the SILO engine adapter were out of scope, so claims about transcript-to-model-prompt replay rely on the stored `content`/`details` contract asserted by t06/t08 and the stored-message tests in `agent_messages.rs:1843-1858`. SILO token/byte/latency numbers are quoted from `reports/occ23-campaign/FINAL-REPORT.md` and `GAP-AUDIT.md` as the campaign's own measurements.
