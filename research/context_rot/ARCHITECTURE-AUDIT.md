# ARCHITECTURE-AUDIT — Optimus context assembly, compaction, retention, Jev, memory

Scope: read-only audit of the context-rot surface in worktree `C:/Users/openclawuser/optimus-rot-20261018`. Method: read the 17 named files in full or in
targeted full-function slices; no code modified; no build run; no whole-tree search. Two files outside the list were read because a listed file names them and a
claim depends on them: `crates/pi-coding-agent/src/core/messages.rs` (imported by `compaction.rs:48-52`) and `crates/pi-agent-core/src/agent.rs` (the request
snapshot point). Every claim carries `ABBREV:line`.

Citation keys (all paths under the worktree, prefix `crates/pi-coding-agent/src/core/` unless stated):
```
CC  = compaction/compaction.rs (3514)   CU  = compaction/utils.rs (812)
CP  = compaction/parallel.rs (373)      CB  = compaction/branch_summarization.rs (696)
CK  = compaction/checkpoint.rs (169)    CM  = compaction/metrics.rs (351)
JC  = jev_compaction.rs (1004)          JR  = jev_retrieval.rs (742)
CT  = context_tree.rs (882)             RC  = rlm_continuation.rs (619)
PR  = prompts/rlm.rs (423)              SM  = session_manager.rs (7003)
AS  = agent_session.rs (27840)          MS  = memory/search.rs (2757)
MV  = memory/service.rs (2527)          MST = memory/store.rs (2468)
HP  = prime-agent-runtime/src/rlm/harness.py (1253)
MSG = messages.rs (one-level: imported by CC)
AG  = crates/pi-agent-core/src/agent.rs (one-level: the request snapshot)
```

Legends: PROOF = the code shows it; SUSPICION = plausible, not established from the files read. Defaults quoted below are the shipped defaults in
`default_compaction_settings` (CC:1040-1051): `enabled = true`, `reserve_tokens = 16384`, `keep_recent_tokens = 20000`, `summary_update_policy = "off"`, `prompt
= "legacy"`, `summary_budget_mode = "legacy"`, `trigger_threshold = None`, `deadline_ms = 300000`.

## 1. CONTEXT-FLOW MAP
### 1.1 What the provider actually receives

The request is a snapshot of live agent state, not a re-derived structure: `AgentContext { system_prompt, messages, tools }` (AG:1157-1167), taken at AG:1110
and AG:1127. So each turn sends exactly three things:

1. `state.system_prompt` — a string, rebuilt out-of-band and assigned into state (AS:8771-8773 build, AS:8945 `build_system_prompt`, AS:8560-8562 assign; other
  assigns at AS:3313-3316, 3393-3396, 3509-3512, 3944-3947, 11970). It contains the RLM prompt text (PR:72-255: role lines, tool doctrine, working dir, depth,
  skill roster, agent-messaging and recursion blocks, REPL control block PR:25) plus sub-agent guidance (PR:270-297). It is NOT a session entry, so it is never
  summarized and never compacted.
2. `state.messages` — `Vec<AgentMessage>` (the live list), converted per request by `convert_to_llm` (MSG:1238-1285; installed as the agent's converter at
  AS:440 / AS:19578).
3. `state.tools` — tool schemas, regenerated per request from the active tool set.

The order in which NEW messages are appended within one turn (before the next request) is: extension `before_agent_start` messages (AS:4548-4560), prefix
records (AS:10761-10769), next-turn context records with the harness digest first (AS:11890-11896, spliced AS:11923-11929), then the primary user text and
images (AS:10639-10657), with `agent.prompt(prepared_messages)` as the single push site (AS:12043).

No pinning mechanism exists anywhere in the assembly: no pin, never-drop or protected flag is applied to history (only the file-ops `pinned` field, CC:519-557,
which is a summary input, not a retention rule). The only retention rule is the `firstKeptEntryId` window (SM:1267-1281).

The mapping `AgentMessage -> Message` is where roles are decided (MSG:1288-1419):

- `Message::*` passes through unchanged (MSG:1415).
- `BashExecution` becomes a **user** message carrying fenced command output (MSG:1291-1323), unless `exclude_from_context` (MSG:1301-1302).
- Every other `Custom` message becomes a **user** message (MSG:1325-1360) — except four types that are dropped from model context entirely: slash-command,
  slash-command-result, compaction-outcome, refinement-outcome (MSG:1331-1336).
- `BranchSummary` becomes a user message `BRANCH_SUMMARY_PREFIX + summary + suffix` (MSG:1362-1371, prefixes MSG:33-36).
- `CompactionSummary` becomes a user message `COMPACTION_SUMMARY_PREFIX + summary + suffix` (MSG:1372-1399, prefixes MSG:29-31), optionally with the harness
  digest block prepended to the same text (MSG:1379-1391) and optionally with `provider_context` attached to the user message (MSG:1397). When a provider
  checkpoint exists AND a digest exists, the digest is emitted as a SECOND user message (MSG:1400-1413), because a replayed opaque window replaces the carrier
  text.

Consequence: memories, harness state, subagent reports, continuation prompts, kernel-state notices and bash output are all indistinguishable, at provider level,
from a real user turn. Only compaction outcomes and slash-command noise are suppressed.
### 1.2 Assembly order (context vector)

`build_session_context` (SM:1022-1348, entry AS:8594-8605) builds the list, in this order:

1. Walk leaf -> root by `parentId`, then reverse (SM:1062-1071). The leaf is the last entry (SM:1053). A parent that is missing ends the walk (SM:1068); a
  repeated id breaks the loop (SM:1067).
2. Scan the path for the effective compaction entry: the LAST `compaction` entry whose provider checkpoint (if any) matches the target model (SM:1146-1164).
3. With a compaction at index `k`: emit the compaction-summary message (SM:1284-1310), then `path[firstKeptEntryId..k]` as retained verbatim messages
  (SM:1267-1282) — but ONLY when there is no provider checkpoint (SM:1268); then `path[k+1..]` (SM:1313). Without a compaction: every path entry (SM:1315-1317).
4. Entries that contribute a message: `message`, `custom_message`, `branch_summary` (SM:1191-1250). Everything else (`model_change`, `thinking_level_change`,
  `child_usage_attributed`, `agent_status`, `git_state`, `session_info`, `label`, `custom`) contributes nothing — `_ => None` (SM:1248).
5. Live-state fix-ups after the rebuild: late ipython agent messages are re-applied (AS:8601-8603) and unpersisted outcomes are merged in (AS:8604, definition
  at AS:16736-16739).
### 1.3 Where each item class enters

| Item | Entry point (write) | Enters context at | Pinned? |
|---|---|---|---|
| User message | appended as `message` entry (SM:4342) | SM:1193-1202 | only while after `firstKeptEntryId` |
| Assistant text/thinking/tool calls | same | SM:1193-1202 | same |
| Tool result | same | SM:1193-1202 | same; never a cut point (CC:1349) |
| Summary (text) | `append_compaction` (SM:4427-4485) | SM:1284-1310 | always, as the forward boundary |
| Harness digest / memory block | `create_harness_digest_message` (MSG:157-167), staleness check AS:16176-16207 | MSG:1325-1360 as a user message | no |
| Knowledge/memory recall | memory recall custom message | MSG:1325-1360 as a user message | no |
| Subagent report (agent_message) | custom message entry | MSG:1325-1360 as a user message | no |
| RLM continuation state | custom message `prime-agent.rlm-continuation-state` (RC:6) | MSG:1325-1360 | no |
| Kernel-state notice | `sync_kernel_state_after_compaction` (AS:13739-13838, type `ipython_state`) | MSG:1325-1360 | no |
| Provider checkpoint | `details.providerCheckpoint` (CC:62, SM:4456-4458) | SM:1261-1262 + MSG:1372-1399 | yes, as an opaque carrier |
| Compaction outcome / failure notice | AS:16689-16752 | **never** (MSG:1333) | n/a |

## 2. LENGTH MEASUREMENT

Two independent numbers exist and they are not the same.

`calculate_context_tokens` (CC:1058-1064): `usage.total_tokens` when non-zero, else `input + output + cache_read + cache_write`. Output is included on purpose
(CC:1056-1057).

`estimate_context_tokens` (CC:1110-1167): find the newest assistant message whose stop reason is neither `aborted` nor `error` (CC:1068-1076), take its usage as
the base, and add `estimate_tokens` for every message after it (CC:1155-1159). With no usable usage, sum every message (CC:1111-1152), optionally in parallel
above 512 messages / 20 MiB (CC:1119-1130, CP:214-215).

`estimate_tokens` is `ceil(chars/4)` per message (CC:1231-1330): text, thinking, tool-call name+JSON args, tool-result text, `BashExecution` command+output,
custom content; images inside custom content count a flat 4800 chars (CC:1243) while images in a USER message are dropped and count ZERO (CC:1265); a user message with `provider_context` and a compaction summary with
`provider_context` instead report the checkpoint's own `estimated_tokens` (CC:1254-1256, CC:1316-1321).

Where each is used:

- Threshold decision: `get_threshold_context_tokens` (AS:16459-16474) -> `should_compact_for_model` (CC:1207-1229).
- `tokens_before` for a compaction: the preparation closure calls `estimate_context_tokens` on `messages_without_harness_digests` (AS:18056-18066).
- Pre-turn with no assistant yet: `agent.model_and_context_tokens()` (AS:9068).
- UI/telemetry: `compute_context_usage_from_entries` (CT:220-273), which reports `tokens: None` right after a compaction until a post-compaction assistant usage
  exists (CT:239-262).
- Chunk sizing inside the summarizer uses yet a third divisor, chars/3 (CC:1903-1910).

Error modes (PROOF unless marked):

1. Coverage gap: the estimate is computed over messages only (CC:1251-1330). The system prompt and the tool schemas are part of the same request (AG:1162-1166)
  and are never counted. Measured context therefore understates the real prompt by the whole prompt-plus-tools block.
2. Divisor mismatch: 4 chars/token is optimistic for code, CJK and JSON; the chunker uses 3 (CC:1906), so the two paths disagree about the same bytes by 33%.
3. Images: user-attached images count ZERO (CC:1265); images inside custom content count a flat 4800 chars = 1200 tokens regardless of size (CC:1243). A screenshot-heavy turn is under-counted, and a custom-message-heavy turn is mis-counted.
4. Staleness: the base figure is the LAST assistant usage, which reflects the prompt of a previous request; every message added since is estimated. After a
  provider-checkpoint compaction the base may describe the pre-compaction window (CC:1316-1321 prefers the checkpoint estimate only when the carrier still
  carries one).
5. Usage semantics are provider-managed: `total_tokens` may include cached and reasoning tokens; nothing in these files normalizes it (CC:1058-1064).

## 3. TRIGGER ARITHMETIC

The single decision function is `should_compact_with_cap` (CC:1183-1205):
```
if !enabled                -> false                                   (CC:1189-1191)
if context_window <= 0     -> false                                   (CC:1192-1194)
if trigger_threshold valid -> tokens >= min(cap, threshold*window)    (CC:1198-1203)
else                       -> tokens >= min(cap, window - reserve)    (CC:1204)
```

`cap`: 250_000 (CC:635). The only provider-specific override raises it to 400_000 for `azure-openai-managed | azure-foundry-managed | azure-openai-responses`
with a model id starting `gpt-` (CC:1212-1220, constant CC:636). The cap is then lowered, never raised, by an explicit output allowance: `cap = min(cap,
(context_window - max_tokens).max(0))` when `max_tokens_explicit` (CC:1223-1227). `window` is `get_model_input_limit(model)` (CC:1228), not `context_window`.

Worked numbers with the shipped defaults: a 1,050,000-token window triggers at `min(250000, 1050000-16384) = 250000`. A 200,000-token window triggers at
`min(250000, 183616) = 183616`. So for every window above 266,384 tokens the 250k cap binds and the reserve term is dead; below that, the reserve binds. The system prompt and tool schemas are outside the measurement (D17), so the real request is already past the trigger when the measured value reaches it.

Call sites (three, all in agent_session):

1. Pre-turn: `run_pre_turn_compaction` (AS:9053-9078) — runs before a prompt is sent; uses the last assistant message if any, else the live token count
  (AS:9067-9076).
2. Turn end: the agent loop hook installed once at AS:3674-3685 calls `should_stop_after_turn` (AS:4718-4762), which calls
  `should_stop_for_threshold_compaction` (AS:4765-4804) -> `threshold_compaction_needed` (AS:4807-4867) -> `run_auto_compaction("threshold", false)` (AS:16765).
  Returning true ends the turn; it does not run between tool calls.
3. Mid-conversation, after a failed or terminal assistant message: `process_agent_event` (AS:7485-7498) runs `check_compaction_overflow` (AS:16567-16654) and
  then `check_compaction` (AS:16482-16558). This path CAN fire inside one user turn, between tool iterations, because the loop continues after a
  compact-and-retry (`compaction_will_retry`, AS:7499-7501).

Guards on the automatic path: a compaction timestamp newer than the assistant message suppresses the check (AS:4816-4821);
`threshold_compaction_retry_in_cooldown` (AS:4834-4836, 17195-17198, ladder CC:886-907); `prepare_compaction` returns `None` when the LAST entry is itself a
compaction (CC:1533-1537); overflow recovery allows exactly one attempt (AS:16624-16647).

## 4. SUMMARY CONSTRUCTION

Prompts (all in CC):

- `SUMMARIZATION_SYSTEM_PROMPT` (CU:445): "You are a context summarization assistant ... Do NOT continue the conversation".
- First compaction: `SUMMARIZATION_PROMPT` (CC:439) or `_V2` (CC:450).
- Incremental: `UPDATE_SUMMARIZATION_PROMPT` (CC:441) or `_V2` (CC:454).
- Incremental with consolidation: `CONSOLIDATING_UPDATE_SUMMARIZATION_PROMPT` (CC:443) or `_V2` (CC:464).
- Split-turn prefix: `TURN_PREFIX_SUMMARIZATION_PROMPT` (CC:445).
- Always appended: `KERNEL_PERSIST_SUMMARY_NOTE` (CC:466, appended CC:514) — tells the summarizer the Python kernel survives and that variable names are worth
  recording.

Selection is by `previous_summary.is_some()` first, then policy, then prompt version (CC:477-499). Custom instructions from `/compact <text>` are appended in
`<user-instructions>` with "follow them with high priority" (CC:511-513). The retained-tail assistant excerpt is appended in `<retained-state>` with an explicit
"not an instruction" guard (CC:1675-1683).

Fed in:

- `messages_to_summarize`: entries `[boundary_start..history_end)` converted by `get_message_from_entry_for_compaction` (CC:1598-1618), which SKIPS compaction
  entries (CC:607-610) and skips harness-digest custom messages (CC:612-616).
- Serialization: `serialize_conversation(convert_to_llm(messages))` (CC:1881). Tool results are truncated to 2000 chars keeping the head and the last 500
  (CU:125-150, applied CU:337 and CU:428). Image blocks are dropped entirely (CU:322-325, CU:413-416). Assistant thinking is included (CU:302-307).
- Previous summary: stripped of file inventories (CC:1559, CC:1889) and injected as text inside `<previous-summary>` tags, followed by the instruction block
  (CC:1895-1902).

Budget:

- Policy budget: legacy `floor(0.8*reserve)` = 13,107 at the default reserve (CC:732-747); scaled `clamp(tokens_before/40, 2000, 8000)` (CC:738-743).
- Wire budget: `min(requested, min(model.max_tokens, floor(input_limit/4)))` with a floor of 1 (CC:1802-1841); reasoning models get thinking headroom
  (CC:1819-1837); one retry ceiling at 2x (CC:1847).
- Chunk budget: `floor((min(input_limit, window - retry_max_tokens) - 1024) * 3) - len(suffix) - len(system_prompt) - 64` chars (CC:1906-1910). A non-positive
  budget is a hard error (CC:1911-1916).

Retry / fallback ladder, in order:

1. Provider-level retry inside one call: `complete_with_provider_retry` (CC:309-372) with `max_retries = 3`, base 2 s, jitter 0.2, exponential capped at 5 min,
  honoring `retryAfter` (CC:178-183, 217-255). Permanent kinds are not retried (CC:304-306, 344-347).
2. Empty-but-successful response is converted to an error so the same retry owner handles it (CC:2020-2028).
3. One length retry with the doubled wire budget (CC:2059-2070), gated on a confirmed `max_output_tokens`/`length` terminal and no tool calls.
4. Structural validation: `validate_summary` (CC:2207-2299) requires all six headings with non-empty bodies, tolerating fences and repeated headings; failure
  returns "Summarization returned an unusable handoff (...); existing conversation preserved." (CC:2208).
5. Episode deadline: `tokio::select!` against `settings.deadline_ms` (default 300 s, CC:761); on expiry the in-flight calls are cancelled and
  `deadline_truncation_result` returns a boilerplate summary (CC:2571-2578, 2620-2645).
6. Failure classification + pacing: `classify_summary_failure` understands only the validator's own formats (CC:2158-2203); backoff is 30 s -> 2 min -> 5 min
  with a 10-minute cooldown after 3 consecutive failures, and a 60 s -> 30 min ladder for filtered/refused (CC:886-907, 792-819).

What happens on failure: the compaction is not persisted; the previous context stays live; `end_compaction_unsuccessfully` (AS:16661-16686) persists a
`compaction_outcome` custom message (AS:16689-16752), which is EXCLUDED from provider context (MSG:1333) — so the model is never told that compaction failed or
why. Overflow recovery gets one attempt and then reports a hard failure (AS:16633-16644).

## 5. PERSISTENCE AND RESTORATION
### 5.1 The Compaction entry

Written by `append_compaction` (SM:4427-4485); the entry is an untyped JSON map (SM:462-463), keys:
```
type = "compaction"        (SM:4439)
id, parentId, timestamp    (SM:4440-4445)
summary                    (SM:4446)
firstKeptEntryId           (SM:4447)
tokensBefore               (SM:4448)
details? { readFiles, modifiedFiles, providerCheckpoint?, deadlineFallback? }  (CC:56-68, SM:4456-4458)
fromHook?                  (SM:4459-4461)
customInstructions?        (SM:4462-4467)
usage?                     (SM:4468-4473)
harnessDigest?             (SM:4474-4479)
```

Not stored: any `tokensAfter` (no writer or reader exists), any record of which entry ids were summarized, any content hash of the dropped region, and any flag
that survives a reader (`deadlineFallback` is written at CC:2641 and read by none of CC/SM/AS).
### 5.2 What `firstKeptEntryId` implies

It is a forward pointer to the first entry the new context keeps verbatim. Everything before it exists only as `summary` prose. Two readers:

- `prepare_compaction` (CC:1561-1567): `boundary_start` = index of that entry, so the NEXT compaction summarizes only material at or after it; if the id is not
  found, `prev_compaction_index + 1`.
- `build_session_context` (SM:1269-1281): retained window `path[first_kept..k]`; an unresolved or empty id collapses the window to nothing
  (`unwrap_or(compaction_idx)`, SM:1279).

Because the boundary is the only durable record, "what was dropped" is not recoverable from the entry. It is recomputable only by replaying the path — which is
exactly what `prepare_compaction` does.
### 5.3 How a restored session rebuilds context

`load_entries_from_file` -> path walk -> `build_session_context_with_entry_ids` (SM:1022). Restoration is therefore deterministic from the JSONL: leaf = last
entry (SM:1053), branch = parentId chain (SM:1062-1071), effective compaction = last matching one (SM:1146-1164), then the three-part assembly described in 1.2.
Three consequences:

- Earlier compactions vanish from context completely: entries of type `compaction` other than the chosen one yield `None` (SM:1248). No double summary, and no
  duplicated pre-compaction text (correct).
- An empty `summary` still emits a `CompactionSummary` (SM:1284-1310), so a compaction written by a foreign writer with no summary collapses the whole prefix
  into nothing.
- A missing parent silently truncates the branch: the walk simply stops (SM:1068) and older entries disappear with no diagnostic.
### 5.4 Provider-native checkpoints at restore

Presence is decided by key existence, not validity: `has_provider_checkpoint` is true for `providerCheckpoint: null` (CK:10-17). The selector then requires a
parseable checkpoint that matches the target model (SM:1151-1162, CK:19-66, `compaction_matches_target`); if either fails the entry is SKIPPED and the branch is
rebuilt from the raw transcript (SM:1315-1317). See defect D7.

## 6. REPEATED SUMMARIES — 2nd, 3rd, N-th
### 6.1 The mechanism (identical under both policies)

At compaction N>1, `prepare_compaction` (CC:1528-1657) does four things that decide the answer:

1. It finds the previous compaction as the last `compaction` entry WITHOUT a provider checkpoint (CC:1540-1548).
2. It reads that entry's `summary`, strips the file inventory, and keeps it as `previous_summary` (CC:1552-1560).
3. It sets `boundary_start` to that entry's `firstKeptEntryId` index (CC:1561-1567).
4. It extracts `messages_to_summarize` from `[boundary_start..history_end)` and explicitly EXCLUDES compaction entries (CC:1598-1618 via CC:607-610).

So the wire call for compaction N contains: the new messages that were retained verbatim last time plus everything produced since, and — as text — the single
previous summary. The original messages behind that summary are never re-sent. In `generate_bounded_summary` the previous summary is placed in
`<previous-summary>` tags immediately before the instruction block (CC:1895-1902), and once chunking is active the loop feeds the model's own previous output
back in on every iteration (CC:1891-2096, summary reassigned CC:2092).
### 6.2 Under `summary_update_policy = "off"` (the shipped default)

`previous_summary.is_some()` selects `UPDATE_SUMMARIZATION_PROMPT` (CC:477-489, text CC:441 / CC:454). That prompt says "PRESERVE all existing information from
the previous summary" AND "If something is no longer relevant, you may remove it". Policy `off` therefore does not mean "no iteration"; it means "iterate with
the legacy instruction set, including the deletion license".
### 6.3 Under `consolidate-repeated-v1`

Same data path; only the instruction text changes to `CONSOLIDATING_UPDATE_SUMMARIZATION_PROMPT` (CC:479-484, text CC:443 / CC:464). It adds: preserve every
enduring user constraint, correction and acceptance gate; preserve decisions with provenance; "Do not make another merge pass"; consolidate demonstrable
repetition; never drop a unique anchor; stay within the stated budget (v2 only, CC:500-509). It is still a rewrite of the previous summary into a new one.
### 6.4 Answer

Summary of summary: YES under both policies. Proof chain: previous summary read from the entry (CC:1552-1560) -> injected as prompt text (CC:1895-1902) -> old
source messages excluded from the call (CC:607-610, 1598-1618) -> new summary replaces the old one as the only carrier (SM:1284-1310). The N-th summary is the
N-th model rewrite of prose, never a second reading of the transcript. Under `off` the deletion license makes loss monotone; under `consolidate-repeated-v1` the
loss is bounded by explicit preservation rules but the compounding still happens.

Two escapes exist and matter for the audit:

- If the previous compaction carries a provider checkpoint, step 1 skips it (CC:1543-1547); with no other compaction the search yields `None`, `boundary_start =
  0`, and the next text compaction re-summarizes from the ORIGINAL messages, because a native compaction never removes entries (similar to source). That path is
  lossless with respect to the transcript and is the only re-read-from-source path in the design.
- Branch summaries are separate prose (CB:290-319) and are treated as ordinary user-role messages by the next compaction (CC:578-585), so they compound as text
  too.

## 7. RETENTION — how `keep_recent_tokens` carves the tail

`find_cut_point` (CC:1414-1479) walks BACKWARD from `end_index`, adding `estimate_tokens` per entry, and stops at the first index where the running total
reaches `keep_recent_tokens` (default 20,000, CC:1044). Valid cut points are user, assistant, bashExecution, custom, branchSummary and compactionSummary
messages (CC:1342-1362) plus `BranchSummary`/`CustomMessage` entries (CC:1355-1361). Tool results are never cut points (CC:1349), which correctly prevents an
orphaned result at the head of the retained window.

Three properties of that walk matter:

1. Only `Message` entries contribute tokens: the `let ... else { continue }` at CC:1435-1437 skips `CustomMessage`, `BranchSummary`, `Compaction` and `Other`
  entries without adding anything. Injected blocks (memory digest, recall blocks, kernel-state notices, agent messages) are therefore FREE for the carve, and
  the retained tail can be far larger than 20,000 tokens. PROOF, defect D3.
2. When the total is reached at index i, the cut is the first cut point >= i (CC:1441-1447), with the last cut point as fallback; if the walk never reaches the
  budget, the default `cut_points[0]` keeps everything (CC:1430).
3. A cut that is not a user message opens a split turn (CC:1463-1478): the prefix from the turn start to the cut is summarized by a second call under
  `TURN_PREFIX_SUMMARIZATION_PROMPT`, and the two summaries are concatenated with a `**Turn Context (split turn):**` marker (CC:2493-2542).

What can be silently dropped (i.e. replaced by prose the model wrote about it):

- Any user message older than the retained window, including a correction or a constraint stated once (assigned at SM:1281 to the retained window; everything
  else only via `summary`).
- Any tool result older than the window, including a failing test log or a stack trace, already truncated to 2000 chars before the summarizer saw it
  (CU:125-150, CU:337).
- Any subagent report older than the window: it is a custom message, summarized like any other text (CC:571-577), with no dedicated section in the prompt except
  the generic 'Critical Context'.
- Any mid-turn tool-call/result pair whose assistant message fell before the cut; the retained window always starts at a non-toolResult cut point, so pairs are
  dropped whole, not split.
- Everything in the dropped region when the deadline fallback fires: the replacement is a fixed boilerplate plus the file inventory (CC:2625-2629), with no
  model-written content at all.
- Python kernel variables above the per-variable snapshot limit, pruned at compaction time (AS:13747) and announced only by name in a custom message
  (AS:13780-13782).

## 8. JEV: what the two modules actually do
### 8.1 `jev_compaction` (JC)

Its header states the contract: "Reversible Jev compaction of provider context, not durable session history" (JC:1-4). `compact_context` (JC:637-642) is called
at the provider-context seam and takes and returns `Vec<serde_json::Value>`; durable `AgentState` and entries are untouched (JC:637).

What it may do to a candidate pair (a successful read/search tool result with exactly one earlier matching call): drop the call AND the result, or truncate the
result to `truncate_head_chars` with a marker that says `[... N characters omitted from this tool result; original content remains in session history.]`
(JC:550-595, marker JC:586).

Eligibility is deliberately narrow: index 0 and the newest `preserve_recent_messages` are pinned (JC:176-178); everything up to the last provider-context
message is excluded (JC:314-318); errors, aborted turns, `kernelRestarted`, non-ok status, `stderr`, `backgroundOutput`, `diffs`, `attachments`,
`sentAgentMessages` are never touched (JC:262-285, 439-459); signed/redacted blocks are never touched (JC:180-188, 477);
`.md`/`.mdx`/`agents`/`instruction`/`prompt` paths are protected (JC:250-260); unknown tools and `ipython` code that does not match one literal
`Path(...).read_text()` shape are skipped (JC:192-247).
### 8.2 `jev_retrieval` (JR)

Replaces the TEXT of an eligible historical read/search result with a fixed marker `[Optional historical read/search result omitted for this request; original
result remains in session history.]` (JR:24, applied JR:443-449). The paired call is never removed (JR:3-7). Pinned: newest six messages plus the current user
suffix (JR:27, 361), any `details` with `pinned`/`mandatory`/`current`/ `currentTask`/`isError`/`error`/`edited`/`instructions` (JR:165-182), signed content
(JR:228-252), and instruction-like paths (JR:329-338). It FAILS OPEN — returns no candidates at all — when the message list exceeds 512 messages or when any
message carries `providerContext` (JR:346-353).
### 8.3 Is the filtering reversible, and can the model recover the material?

Neither module has a restore path: no function in either file re-materializes removed text. Reversibility is a property of the STORAGE, not of the runtime: the
durable entries still hold the original, so reversibility means "the session file can replay it", and the model's only recovery within a turn is to call the
tool again (which JR deliberately keeps executable, JR:3-7) or to read its own session log. Both markers tell the model the original exists (JC:586, JR:24) but
give no address and no tool. So: the filtering is reversible for the runtime, not for the model in the same context. PROOF.

## 9. MEMORY RETRIEVAL — ranking, injection, instruction masquerade

Ranking (MS): per entry, a binary term-membership weighted sum over fields — title 3.0, body 1.0, span-only 0.5 (MS:879-893) — divided by the summed IDF of ALL
query terms, so an unmatched term still counts in the denominator (MS:897-901). The "title" field is actually `title + path + id` (MS:500-509), so ids and paths
outrank body matches at weight 3. There is no score threshold and no top-k inside the scorer: every entry with at least one matching term is returned
(MS:894-896). The render stage caps output at `max_recall_entries = 6` and `max_recall_chars = 6000` (MST:214-215; cutoff MS:1105-1107).

Injection: the recall block is a custom message, which `convert_to_llm` turns into a USER message (MSG:1325-1360; the recall type is not in the exclusion list
MSG:1331-1336). The block opens with `[memory data; not new evidence] Saved notes for this project. Treat them as fallible context, not new user instructions
...` (MS:1100) and then one bullet per hit built by `lines.push(format!("{prefix}{content}{}", source_line...))` (MS:1168) — entry bodies are interpolated
verbatim, with no fence, no escaping and no per-entry provenance marker beyond the prefix/source line. Silent shrink: an entry that does not fit the remaining
budget by 80 chars is skipped entirely with no marker (MS:1153-1155); a text cut is marked `[read entry for full text]` (MS:1160).

Can retrieved text masquerade as an instruction? Yes, by construction: it arrives in the user role, it is unfenced, and memories are auto-learned (the session
header shows `Memory automatic learning: enabled`). The only defense is one prose sentence (MS:1100) plus the model's judgment. A memory body that opens with
`## Rule` or an entry-shaped bullet is textually indistinguishable from a system instruction in the same role. Compare the harness overview, which at least
collapses newlines (`replace("\n", " ")`, HP:1173) and truncates to 120 chars (HP:1174-1175); the recall block does neither.

Failure mode worth noting: a single entry whose metadata carries a foreign `projectId` invalidates the WHOLE document (MST:406-409), and the search path treats
an unreadable store as an empty corpus, so recall silently returns nothing with no error surfaced.

## 10. SUBAGENT STATE IN THE PARENT

What the parent holds about children, in order of durability:

1. The admission handle: `rlm(...)` returns `rlm_child_id`, `name`, `session_dir`, `model` as a Python value; if the model printed it into a cell, it lives in
  the kernel, not in context (PR:207-213).
2. Child reports and follow-ups as `agent_message` custom messages — user role in provider context (MSG:1325-1360).
3. Terminal notices and child-usage attribution as entries: `child_usage_attributed` exists as a persisted type (SM:4523-4297 region; parsed CT:543-548) but
  contributes NOTHING to context (SM:1248).
4. The runtime continuation state: custom message type `prime-agent.rlm-continuation-state` (RC:6) with `tasks[]` (id, receivedAt, replied, result,
  resultDelivery), `continuation_count`, `terminal_status`, `compaction_reason`, `pending_continuation`, `pending_result` (RC:16-83). Max 3 continuations
  (RC:5). A continuation is itself a synthetic USER message (RC:182-208) telling the child to stop work and send a partial report — so runtime control also
  arrives as user text.
5. Subagent SPECS (not live children) in harness state: kind `subagent` (HP:46, 1071-1082), rendered in the prompt block as `subagent: <count>` plus bullets
  (HP:1169-1171, 1188-1191) — a count of specs, not of running children.

Survival across compaction — traced: a report is a `custom_message` entry, so it is (a) a valid cut point (CC:1355-1361), (b) included in the summarizer input
as a synthesized message (CC:571-577, CC:1614-1618), and (c) dropped from context once it falls before `firstKeptEntryId` (SM:1281). Nothing in the summarizer
prompts has a subagent-specific section (CC:439-464); child facts survive only if the model chose to write them into 'Critical Context' or 'Progress'.

The design acknowledges this: the prompt tells the model to use `rlm.list_subagents()` after a kernel restart or compaction (PR:283), and `rlm.collect` is
described as returning bounded previews (PR:287). Child-visible text delivered to the parent is additionally bounded at 6000 chars with a `[truncated by Prime
runtime]` marker (RC:211-221) — so long child answers are truncated before they ever become a message.

Verdict: the parent's context retains a lossy prose trace of children. The authoritative child state is on disk (`sub-*/`) and in the continuation-state
message, not in context; after compaction the only in-context child facts are whatever the summarizer kept.

## 11. PROVIDER CHECKPOINTS

Storage: `CompactionDetails.provider_checkpoint` (CC:62), serialized camelCase (CC:57) into `details.providerCheckpoint` on the compaction entry (SM:4456-4458).
Legacy extension checkpoints store `strategy: openai-responses-compaction-v2` plus an opaque `compactedWindow` array and are converted on read (CK:6-17, 30-66).
The opaque payload is `items` — arbitrary provider JSON that may contain `encrypted_content` (CK:37-43); `estimatedTokens` is a local estimate (CK:52-60).

Replay: the checkpoint never becomes text. `build_session_context` attaches it to the carrier `CompactionSummary` message (SM:1261-1262, 1304-1309) and
SUPPRESSES the retained window (SM:1268-1282), because the server-side window already contains it. `convert_to_llm` copies `provider_context` onto the resulting
user message (MSG:1397) and, if a harness digest exists, emits the digest as a separate message because "opaque checkpoint replay replaces the carrier's text"
(MSG:1400-1413). After a compaction the live state is rebuilt twice on purpose: `build_session_context` first (AS:18289-18294), then
`restore_provider_context_for_model` (AS:16370-16396), which also stamps `provider_context_rebuilt_at` (AS:16394) and re-ensures the digest (AS:16395).

What must never be flattened: (1) `items`, including encrypted content (CK:37-43); (2) the provider/model/api/baseUrl/endpoint identity, which decides whether
the window may be replayed at all (SM:1127-1162); (3) the distinction between "compaction with checkpoint" and "compaction without" — the former forbids
re-adding the retained messages (SM:1268). Jev honours this: everything up to the last provider-context message is excluded from compaction (JC:314-318), it
refuses to drop a call covered by the checkpoint but pins the covering result (JC:397-408), and retrieval fails open whenever any message carries
`providerContext` (JR:348-351).

## 12. DEFECT LIST
Severity: HIGH = silent loss of task-critical state, or work stops with no recovery path; MED = lossy but bounded or narrow; LOW = accounting/quality. PROOF
means the code establishes it; SUSPICION is labelled.
### D1 (HIGH, PROOF) Threshold trigger and threshold executor disagree
`threshold_compaction_needed` gates on `settings.enabled` only (AS:4811-4814) and makes the hook return true, which stops the turn (AS:4718-4762). The executor
also requires `auto_compaction_enabled` (AS:16498, flag AS:2829, default true AS:3201, public toggle AS:17224-17226). With `compaction.enabled` true and
auto-compaction toggled off, every turn past the trigger is terminated and nothing compacts: the session stalls at the cap with no compaction and no message.
### D2 (HIGH, PROOF) A normal run that crosses the trigger is stopped and not resumed
Post-compaction continuation is scheduled only when `should_continue_after_compaction` or queued work exists (AS:16827-16833); that flag is set only by
RLM-child, goal or autonomous continuations (AS:4839-4865, 16526-16553). `will_continue` (AS:16903-16906) is false for a plain interactive turn with an
assistant-last history and nothing queued, so the only post-compaction action is an auto-refine check (AS:16933-16936). Result: the answer stops at the trigger
point and the agent goes idle mid-task, with the summary already persisted in place of the history.
### D3 (HIGH, PROOF) Retention accounting ignores non-message entries
`find_cut_point` adds tokens only for `Message` entries (CC:1433-1438); `CustomMessage`, `BranchSummary` and `Other` entries cost zero. A tail dominated by
injected custom text (memory digest, recall block, kernel-state notice, agent messages) satisfies `keep_recent_tokens` while keeping megabytes. The retained
tail is a lower-bounded, never upper-bounded, quantity, and the region actually summarized is much smaller than the budget implies.
### D4 (HIGH, PROOF) Summary-of-summary under both policies
Previous prose is read from the last non-checkpoint compaction (CC:1552-1560), the original messages are excluded from the call (CC:607-610, 1598-1618), the
prose is injected as text (CC:1895-1902), and the new summary replaces the old as the only carrier (SM:1284-1310). Policy `off` keeps the deletion license
(CC:441, 454: "If something is no longer relevant, you may remove it"); `consolidate-repeated-v1` constrains it but still rewrites. Loss compounds in N and the
entry stores no drop manifest (SM:4438-4479).
### D5 (HIGH, PROOF of mechanism) Compaction can run with nothing new to summarize
If the tail already satisfies the budget at the boundary, the cut equals `boundary_start` and `messages_to_summarize` is empty (CC:1574-1618);
`prepare_compaction` returns `None` only when `previous_summary` is also `None` (CC:1630-1635). Each later trigger therefore pays a full summary call to rewrite
the previous summary with no new material — the operation that worsens D4. Frequency is SUSPICION; the mechanism is proven.
### D6 (HIGH, PROOF) An extension compaction bypasses validation
`compact_with_metrics` uses an extension-supplied `summary` and `first_kept_entry_id` verbatim (AS:18171-18186) without `validate_summary` (CC:2207-2299).
`build_session_context` emits a `CompactionSummary` even for an empty summary (SM:1284-1310) and always prefers the last compaction (SM:1163). A hook that
returns an empty or files-only summary (stripped to '' by CU:117-122) deletes the entire prefix.
### D7 (MED-HIGH, PROOF) A null or incompatible provider checkpoint disables the whole compaction
Presence is decided by key existence (CK:10-17). The selector needs a parseable, model-matching checkpoint and otherwise `continue`s (SM:1151-1162); with no
other compaction the raw transcript is replayed (SM:1315-1317). After a model or endpoint switch this replays a transcript that was already too large, and the
stored text summary is ignored.
### D8 (MED-HIGH, PROOF, legacy-only) Migration can drop `firstKeptEntryId` permanently
When a legacy `firstKeptEntryIndex` resolves to the session header, no `firstKeptEntryId` is written and the index field is removed anyway (SM:3281-3292), then
flushed (SM:3836-3838). Restore reads the empty id as "keep nothing" (SM:1273-1280) and the retained tail is lost.
### D9 (MED-HIGH, PROOF) The deadline fallback writes a write-only flag
On deadline expiry the dropped region is replaced by boilerplate plus file lists (CC:2571-2578, 2620-2645) and `details.deadlineFallback = true` (CC:2641). No
reader exists in CC, SM or AS. Later compactions treat the boilerplate as `previous_summary` (CC:1559-1560) and can merge it away; restore cannot distinguish a
real summary from a truncation marker.
### D10 (MED, PROOF) Branch summaries truncate silently, skip tool results, never retry
Newest-to-oldest walk BREAKS at the budget so the oldest branch messages vanish with no marker (CB:258-281); tool results are dropped before summarization
(CB:154-156); output is capped at 2048 tokens (CB:388); the retry policy is accepted then discarded (`let _ = retry;` CB:426); output is not structure-validated
(CB:445-456).
### D11 (MED, PROOF) Stale harness digests stay in provider context
A digest is appended only when its text changes (AS:16176-16207) and old digest messages are never removed. Digest messages are excluded from summarizer input
(CC:612-616, MSG:1175-1194) but not from provider context (MSG:1325-1360 excludes only four types, MSG:1331-1336). A digest inside the retained tail is replayed
next to the fresh one, both claiming to be "the persistent memories produced across this session so far" (MSG:152-153).
### D12 (MED, PROOF of form) Memory and harness text arrive as unfenced user turns
MSG:1325-1360 plus verbatim bodies (MS:1168) plus newline-collapse-only sanitizing (HP:1173). Retrieved or learned text is instruction-shaped and role-identical
to a user message. Existing mitigations: prose header (MS:1100), 120-char truncation in the harness overview (HP:1174-1175), and the retained-state "not an
instruction" guard (CC:1678). Model resistance is empirical (SUSPICION); the channel is proven.
### D13 (MED, PROOF) Memory recall fails closed and silently
One entry with a foreign `projectId` invalidates the whole memory document (MST:406-409); the recall path treats an unreadable store as an empty corpus, so the
recall block disappears with no error, warning or in-prompt marker. Budget skips are also silent: an entry short by 80 chars is omitted entirely (MS:1153-1155).
### D14 (MED, PROOF) The read-files inventory in every summary is always empty
Only `edited` is ever populated (CU:30-39 for ipython diffs, CU:57-62 for `edit` calls); nothing inserts into `file_ops.read` or `file_ops.written`, so
`compute_file_lists` returns an empty read list (CU:68-80) and `format_file_operations` never emits `<read-files>` (CU:108-113) although the summary appends the
inventory (CC:2584).
### D15 (MED, PROOF) The summarizer cannot see the middle of a tool result, or any image
Tool results are truncated to 2000 chars head-plus-tail (CU:125-150, applied CU:337, 428); image blocks are filtered out (CU:322-325, 413-416). An error line in
the elided middle, or the screenshot that carried the answer, is unrepresentable in the summary.
### D16 (MED, PROOF) User-attached images are counted as zero tokens
`estimate_tokens` for a user message keeps only text blocks and drops image blocks entirely (CC:1265), so an image-only or screenshot-heavy user turn measures
as ~0 tokens; a custom message's images are counted at a flat 4800 chars (CC:1243). Image-heavy sessions therefore reach the real provider limit long before the
250k trigger (CC:1198-1204).
### D17 (LOW-MED, PROOF) The estimate excludes the system prompt and tool schemas
`estimate_tokens` covers messages only (CC:1251-1330) while the request also carries `system_prompt` and `tools` (AG:1162-1166). With the RLM prompt plus skill
roster plus subagent guidance (PR:72-255, 270-297) the real prompt exceeds the measured value by that block, eroding the reserve assumed at CC:1204.
### D18 (LOW, PROOF) Compaction failure is invisible to the model
Failure notices are persisted (AS:16689-16752) but their type is excluded from provider context (MSG:1333). The model sees the old transcript and no statement
that compaction was attempted or failed; only the user is told.
### D19 (MED, PROOF of growth) Two live-list producers have no drain or cap
`unpersisted_outcomes` is pushed on every failed outcome persist (AS:16736-16739) and merged into every context rebuild (AS:8609-8627); no drain exists in the
regions read (field AS:2704, init AS:3091). `pending_next_turn_messages` is pushed without a cap (AS:9676-9679) while the flusher returns early whenever input
admission is paused or the pump is suspended (AS:9629-9640). Both grow the live `state.messages` list, which is what the provider receives (AG:1163-1165).
### D20 (MED, PROOF) The summarizer's request is not the request the model saw
`compaction_provider_context` rebuilds the provider context by re-running only `emit_context` and silently dropping entries that fail to round-trip
(`filter_map(...ok())`, AS:17910-17924); the live path applies the same extension pass plus the model-tool-output policy and the provider-seam transforms (Jev
compaction JC:638-642, Jev retrieval JR:443-449). Native compaction therefore summarizes a different, typically larger list than the session actually sent,
while `tokensBefore` is measured before either transform (AS:18056-18065).
### Handled correctly (do not "fix")

- The cut is never a tool result (CC:1349), so the retained window never starts orphaned.
- Exactly one compaction is expanded at restore (SM:1146-1164, SM:1248): no double summary, no duplicated pre-compaction text.
- The anti-retrigger timestamp guard blocks immediate re-compaction on the same message (AS:4816-4821).
- Harness digests are excluded from summarizer input (CC:612-616) and regenerated on the new head (AS:18261, 18278).
- Checkpoints stay opaque and identity-gated (CK:37-43, SM:1127-1162); Jev treats the checkpoint prefix as untouchable (JC:314-318, 397-408; JR:348-351).
- Jev's protection set is the strongest guard in the system (JC:176-188, 250-285, 439-459; JR:165-194, 228-252, 303-341): errors, signatures, instructions and
  subagent sends cannot be dropped.
- Parallel compaction paths are semantically inert (CP:10-19, 123-191); performance only.
- `CM` records content-free timings only (CM:1, 90-101).

## 13. INJECTION POINTS (smallest safe seams)
### (a) Bounded structured task-state ledger
Write it where the compaction entry is written, not in the request path: `CompactionDetails` (CC:56-68) gains one bounded field; it is populated in
`compact_with_metrics` where the summary string is assembled (`summary + format_file_operations`, CC:2583-2584) from the preparation that already knows the
boundary and the file inventory (CC:1636-1643); it is persisted by `append_compaction` (SM:4427-4485, details arm SM:4456-4458); and it is emitted as its own
message immediately before the summary carrier in `build_session_context_with_entry_ids` (SM:1284-1310, same place the harness digest is attached). Do NOT add
it in `convert_to_llm` (MSG:1238): that function also feeds the summarizer, so the ledger would be paraphrased into the next summary.
### (b) Context-health signal
The measurement already exists: `ContextUsageEstimate` from `estimate_context_tokens` (CC:1091-1097, CC:1110-1167). The single request-assembly point is
`create_context_snapshot` (AG:1157-1167); the clean session-side seam is `agent_session::refresh_request_system_prompt` / the prompt rebuild (AS:8797, AS:8945
`build_system_prompt`), which is re-derived per request and already owned by the session. Compute `tokens / min(cap, window - reserve)` from the same constants
the trigger uses (CC:635-636, CC:1198-1204) so the signal cannot disagree with the decision.
### (c) Tool-output durable-reference retention
Two truncation sites need the same treatment: Jev's truncation marker (JC:576-593, marker text JC:586) and the summarizer's head/tail truncation (CU:129-150,
call sites CU:337, CU:428). Both should carry a durable handle (path, artifact id, or session entry id) instead of only a character count. The precedent already
exists in `BashExecution.full_output_path` (MSG:1297) — copy that pattern rather than inventing a store.
### (d) Adaptive trigger logic
One function decides: `should_compact_with_cap` (CC:1183-1205); its only inputs are `context_tokens`, `context_window`, `settings` and the cap. Add the adaptive
term there (for example, growth per turn from successive `ContextUsageEstimate` values, CC:1091-1097) and leave every caller untouched: AS:4831
(`threshold_compaction_needed`), AS:16512 (`check_compaction`), AS:9070 (`run_pre_turn_compaction`), CC:1170-1181 (`should_compact`), CC:1207-1229
(`should_compact_for_model`). Do not duplicate the arithmetic in the callers: the 250k/400k/`max_tokens_explicit` interaction (CC:1212-1227) is easy to get
wrong twice.
### Anti-recommendation
Do not extend `keep_recent_tokens` accounting by treating custom messages as free (D3) after adding a ledger: a ledger message large enough to matter would then
also escape the carve. Fix the accounting (CC:1433-1438) before adding any per-turn block.
