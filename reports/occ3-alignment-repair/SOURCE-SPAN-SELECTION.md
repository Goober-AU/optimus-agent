# Source span selection: what the worker captures today, and the v2 policy

Status: **offline design, one new module, one new test file, one new probe.**  No
GPU, no DGX session, no network service, no live tokenizer, no capture-path
wiring, no `span_policy` semantics changed anywhere in the existing tree.

Evidence base: pinned SGLang `e00930c5489053f26d86b179cee0d087f846acbb`
(v0.5.21, attested by `extensions/sglang-occ3/patches/native-startup.v1.json:3-4`)
at `C:/Users/openclawuser/occ3-latent-validation/references/sglang`, plus this
worktree's `extensions/sglang-occ3`.  Pinned paths below are relative to
`references/sglang/python/sglang/`.

Legend:

- **SOURCE-ATTESTED** - read at the cited `file:line` in this tree.
- **OFFLINE MEASURED** - a number produced by a command I ran, stated with the
  command.
- **HYPOTHESIS** - reasoned from attested facts, not executed.

## 1. What the current span policy selects

### 1.1 The policy is a single constant, not a choice (SOURCE-ATTESTED)

| fact | site |
| --- | --- |
| `CaptureIntent.span_policy` defaults to `"whole_completed_response_tail_v1"` | `occ3_sglang/attachment_contract.py:151` |
| the intent refuses any other value (`unsupported_profile`, 409) | `attachment_contract.py:162-164` |
| `CaptureWorkerDescriptor.span_policy` repeats the refusal on the worker path | `attachment_contract.py:417,437` |
| the backend copies the intent value into the descriptor unchanged | `occ3_sglang/latent_backend.py:224` |
| the adapter requires descriptor and intent to be equal | `occ3_sglang/chat_adapter.py:751` |
| the receipt-side scope string repeats it | `occ3_sglang/use_receipts.py:377-383` |
| the Rust side pins the same constant and enforces it | `crates/pi-ai/src/source_capture.rs:22,53` |

So there is no policy enum, no per-request selection and no v2 name anywhere.

### 1.2 "Whole completed response tail" is literally the last N accepted rows
(SOURCE-ATTESTED)

- The capture ring holds exactly `descriptor.max_prefix_tokens` rows and is
  written round-robin: `destination = (record.accepted_count + i) %
  descriptor.max_prefix_tokens` (`occ3_sglang/latent_worker.py:767-774`), with
  `accepted_count += count` after the write (`latent_worker.py:785`).  Once the
  turn exceeds the ring, earlier accepted rows are overwritten and cannot be
  recovered.
- `WorkerRuntime.align` then selects `n = min(record.accepted_count,
  record.descriptor.max_prefix_tokens)` (`latent_worker.py:309`), reads the ring
  in ring order (`latent_worker.py:314-321`), and sets `end =
  record.accepted_count`, `start = end - n` (`latent_worker.py:346-347`).
- `n >= 2` is required (`latent_worker.py:310`).  Nothing else constrains the
  window: no class, no boundary, no marker.
- The window ends at `finished_len`, not at the last generated id:
  `output_end = self._visible_output_len(req)`
  (`patches/scheduler-cache-ipc.v1.json:479`, `_occ3_settle_result`), where
  `_visible_output_len` returns `req.finished_len`
  (`managers/scheduler_components/batch_result_processor.py:213-214`), and
  `finished_len` **includes** the matched stop token
  (`managers/schedule_batch.py:1438-1442`, "Stop position is included";
  `schedule_batch.py:1800-1802`).
- After alignment the ids are dropped: `record.retained.clear()`
  (`latent_worker.py:363`).  A post-hoc audit of the same window is impossible.

Window size is capped at 128 rows end to end: `1 <= retained_count_limit <= 128`
(`attachment_contract.py:159`), `latent_backend.py:214` admits only
`(8, 16, 32, 64, 128)`, and `SourceCaptureReceipt` requires
`1 <= retained_count <= 128` (`attachment_contract.py:330`).

### 1.3 Consequence for a tool-calling turn (SOURCE-ATTESTED, no measurement needed)

The GLM assistant turn is ordered `thinking_block text_block tool_call_blocks`
(`function_call/glm4_moe_detector.py:1057`), and one tool call is
`tool_call_unit ::= "<tool_call>" single_tool_call "</tool_call>"`
(`glm4_moe_detector.py:965`).  A turn whose `finish_reason` is `tool_calls`
therefore ends with the argument payload inside `</tool_call>`, then the stop
token.  Because the window is the **last** N rows and the last row is the stop
token, a tool-calling source turn always spends the end of its window on
`<arg_value> ... </arg_value>`, `</tool_call>` and the stop row - never on the
answer.  When the argument JSON is longer than the window, the window contains
*only* tool syntax.

`span_audit` already predicts this: with the four markers it understands, the
tail policy selects every row, while `final_content` returns `None` on a turn
with no content run (`span_audit.py:594-623`).  Nothing consumes that prediction;
`span_audit` is imported by its test only (verified by `rg` over
`occ3_sglang/`, one hit, the module itself; the only importer in the tree is
`tests/test_span_audit.py`).

### 1.4 OFFLINE MEASURED consequence

Command:

```
cd C:/Users/openclawuser/optimus-occ3-latent-validation/extensions/sglang-occ3
.venv/Scripts/python.exe ../../reports/occ3-alignment-repair/probes/probe_span_markers.py
```

Two synthetic windows, ids built from a resolved marker table (fake tokenizer,
`tests/test_span_markers.py`), classified by the real `classify_accepted`:

| window | accepted rows | CONTENT | TOOL_CALL | CONTROL | `last_accepted` | `final_content` |
| --- | --- | --- | --- | --- | --- | --- |
| content then one tool call | 48 | 21 | 23 | 4 | rows 0..48 | rows 1..22 |
| tool call only (`finish=tool_calls`) | 26 | 0 | 23 | 3 | rows 0..26 | `None` |

Reading: for the tool-only turn the current policy captures 26 rows of tool
syntax, 23 of them argument/name/id bytes, and the auditor says there is no
content at all.  For the mixed turn the current policy captures the content
**plus** the whole tool call, while `final_content` isolates the 21 content rows.

Two further measured facts from the same run, both relevant to the design:

- `TOOL_ARGUMENTS` is **0** in both windows.  `span_audit` assigns that class only
  to rows *after* `tool_call_end` (`span_audit.py:547-548`), and in a GLM turn the
  only row after `</tool_call>` is the stop token, which is a control marker.  The
  argument rows themselves are classified `TOOL_CALL`.  The class name overstates
  what the region rule can prove.
- `SpanMarkers` carries four fields (`span_audit.py:104-105,141-178`), so the four
  GLM argument delimiters cannot be marked as control rows today.  Any v2 rule of
  the form "the selected run contains no marker id" must consult the full marker
  table, not `SpanMarkers`.

## 2. The exact marker set glm45 and glm47 produce

The pinned source does **define** these strings.  This closes the `UNKNOWN` in
`reports/occ3-statebridge-recovery/SOURCE-SPAN-AUDIT.md` §4.3 ("The exact `glm47`
structural token table was **not located** in the pinned extract").

Deployment in scope: `--reasoning-parser glm45 --tool-call-parser glm47`
(`reports/occ3/DGX-ENVIRONMENT.md:24`).  `glm45` is the only GLM name in the
reasoning-parser registry (`parser/reasoning_parser_names.py:13`,
`parser/reasoning_parser.py:2264`); `glm47` exists only as a tool-call parser
(`function_call/parser_names.py:20-21`, `function_call/function_call_parser.py:85-86`).
The serving model is GLM-5.3, whose template keeps the GLM-4.5 prompt and
tool-call format (`parser/template_detection.py:358-368`).

| role | exact string | code points (thinking pair only) | SOURCE-ATTESTED at |
| --- | --- | --- | --- |
| thinking start | `"\x3cthink\x3e"` | U+003C U+0074 U+0068 U+0069 U+006E U+006B U+003E | `glm4_moe_detector.py:1028`; `parser/reasoning_parser.py:931` |
| thinking end | `"\x3c/think\x3e"` | U+003C U+002F U+0074 U+0068 U+0069 U+006E U+006B U+003E | `glm4_moe_detector.py:1029`; `parser/reasoning_parser.py:932` |
| tool-call start | `"<tool_call>"` | - | `glm4_moe_detector.py:174,1030`; `glm47_moe_detector.py:311` |
| tool-call end | `"</tool_call>"` | - | `glm4_moe_detector.py:175,1031`; `glm47_moe_detector.py:312` |
| argument key start | `"<arg_key>"` | - | `glm4_moe_detector.py:1032`; `glm47_moe_detector.py:315` |
| argument key end | `"</arg_key>"` | - | `glm4_moe_detector.py:1033`; `glm47_moe_detector.py:318` |
| argument value start | `"<arg_value>"` | - | `glm4_moe_detector.py:1034`; `glm47_moe_detector.py:318` |
| argument value end | `"</arg_value>"` | - | `glm4_moe_detector.py:1035`; `glm47_moe_detector.py:320` |
| assistant role | `"<|assistant|>"` | - | `glm4_moe_detector.py:1036` |
| reasoning exclusions | `"<eop>"`, `"<|user|>"`, `"<|endoftext|>"` | - | `parser/reasoning_parser.py:923-929` |
| turn end | the tokenizer's own `eos_token`, matched as a stop id | - | `managers/schedule_batch.py:1783-1803` |

The thinking pair is written with `\x3c` / `\x3e` escapes because several
editors and chat renderers silently rewrite the literal angle-bracket spelling of
a reasoning delimiter.  The code-point column is the ground truth: the tokens are
7 and 8 characters, `U+003C`/`U+002F` then `think` then `U+003E`.  The delivered
module builds them from `"\x3cthink\x3e"` and `"\x3c/think\x3e"` for the same
reason (`occ3_sglang/span_markers.py:THINKING_OPEN`) and
`tests/test_span_markers.py` imports those constants instead of retyping them, so
an invisible character cannot drift between the module and its test.

Four properties of this set matter for a span policy:

1. **`content` has no marker.**  The template orders `thinking_block`,
   `text_block`, `tool_call_blocks` (`glm4_moe_detector.py:1057`) and defines
   `text_block ::= ( "\n" text_without_special_tokens )?`
   (`glm4_moe_detector.py:1126-1128`).  Content is the region *between*
   `thinking_end` and `tool_call_start` (or the turn end).  Any design that
   demands a "content marker" would have to invent one.
2. **`<tool_call>` also ends reasoning.**  `Glm45Detector` passes
   `tool_start_token="<tool_call>"` (`parser/reasoning_parser.py:936`), and the
   parser interrupts reasoning at the first such token
   (`reasoning_parser.py:127-142,165-177,252-267`).  A thinking block can
   therefore close with `</tool_call>`'s sibling `<tool_call>` and **no**
   thinking-end marker.  SOURCE-ATTESTED.
3. **The thinking-start marker may be template-prefilled.**  For `glm47` with
   `accommodate_chat_template=True` the grammar is
   `thinking_block ::= thinking_block_content "</think>"` - the opener never comes
   from the model (`glm4_moe_detector.py:1096-1104`).  A window can legitimately
   contain no thinking-start marker.
4. **`<eop>` is not a text-turn marker.**  It appears as a *spatial* terminator in
   the GLM image pipeline ("description<sop>36 24<eop>",
   `multimodal_gen/runtime/pipelines_core/stages/model_specific_stages/glm_image.py:410`
   under `references/sglang/python/sglang/`)
   and as a reasoning exclusion for text (`reasoning_parser.py:926`).  Its status
   for a text tool turn is HYPOTHESIS: not used as a marker here, and not
   resolved by the delivered module.

## 3. Where each marker can come from at runtime

Nothing in `extensions/sglang-occ3` may encode a marker: the extension has no
tokenizer.  The ids must be produced by a process that owns one.  Three
attested routes exist; only one of them is already in place today.

### 3.1 Already resolved in the scheduler, at startup (SOURCE-ATTESTED)

`scheduler.py:916-932` builds `model_config.think_end_ids` from the reasoning
parser's own delimiter:

```
ReasoningParser(model_type=get_serving().reasoning_parser, ...)
think_end_ids = reasoning_parser.detector.get_think_end_token_ids(self.tokenizer)
self.model_config.think_end_ids = think_end_ids
```

`get_think_end_token_ids` is `tokenizer.encode(self.think_end_token,
add_special_tokens=False)` (`parser/reasoning_parser.py:117-118`).

Consequence: **the thinking-end ids for `glm45` already exist in the scheduler
process** and equal `encode("\x3c/think\x3e")`.  `ReasonerGrammarBackend` repeats
the same encode plus the exclusion list, and raises when a string does not encode
(`constrained/reasoner_grammar_backend.py:274-286,304-323`).

`req.reasoning_tokens` is the runtime's own token-level boundary: it counts
accepted output tokens through the end of the thinking-end match
(`managers/schedule_batch.py:2151-2170`), updated in prefill
(`batch_result_processor.py:360`) and in decode
(`batch_result_processor.py:1017`), gated on `req.require_reasoning`
(`batch_result_processor.py:1363`), which comes from
`processed_messages.require_reasoning = thinking_mode`
(`entrypoints/openai/serving_chat.py:1433`, passed at `:1272`).

So for a thinking-enabled GLM turn the scheduler already knows
`reasoning_tokens` - a boundary `span_audit` could cross-check.  A v2 policy that
ignores it throws away free evidence.

### 3.2 The private per-request channel that already carries marker ids
(SOURCE-ATTESTED)

`sampling_params.py:52-111` defines `__sglang_reasoning_end_token_ids` in
`custom_params`, with a 32-id bound (`:53`), validation (`:56-95`) and a setter
(`:98-111`).  The scheduler reads it through
`get_request_reasoning_end_token_ids(req.sampling_params.custom_params,
allowed_sequences=..., ...)` (`batch_result_processor.py:1367-1374`) and through
the grammar manager (`constrained/grammar_manager.py:131-140`).

`custom_params` is msgpack-safe by construction
(`CustomParamValue = scalar | List[scalar] | Dict[str, scalar]`,
`sampling_params.py:28-37`) and reaches the scheduler via `Req`
(`schedule_batch.py:1109-1114`).  That shape fits a marker table exactly: one
key per role, each value a list of at most 32 ints.

The comparison with the alternative matters: the worker descriptor cannot carry
ids today.  `normalize_worker_descriptor` rejects any leaf that is not
`str`/`int`/`float` (`attachment_contract.py:479-480`) and requires an exact key
set (`:485`), so a nested id list is refused before it ever reaches the worker.
Adding the ids to the descriptor would therefore be a descriptor-schema change,
while the `custom_params` route needs no schema change at all.

### 3.3 The tool-call markers have no scheduler-side resolver (SOURCE-ATTESTED)

`glm47_moe_detector.py:311-320` and `glm4_moe_detector.py:174-183` match these
markers as **text**, with `re.compile` over the detokenized stream, in the
tokenizer-manager/detokenizer path.  Nothing encodes them to ids.

The one field that could have carried them, `MessageProcessingResult.
reasoning_end_token_ids` (`entrypoints/openai/protocol.py:2268-2269`), is
produced only for `reasoning_parser == "k2_horizon"`
(`serving_chat.py:1435-1459`) and in the responses entrypoint
(`serving_responses.py:486-488`).  `rg reasoning_end_token_ids` over
`extensions/sglang-occ3` and `crates` returns only a test stub
(`tests/test_chat_attachment.py:192,237`), so this deployment's extension never
sees marker ids today.

Therefore the marker ids for thinking-start, tool-call start/end and the four
argument delimiters must be produced by new code in a tokenizer-owning process.
The natural split:

| role | producer today | producer required |
| --- | --- | --- |
| thinking end | scheduler startup (`scheduler.py:922-926`) | none, reuse `model_config.think_end_ids` |
| thinking start | none | tokenizer manager, once per process |
| tool-call start/end | none (text regex only) | tokenizer manager, once per process |
| argument key/value delimiters | none (text regex only) | tokenizer manager, once per process |
| turn end | `_check_token_based_finish` stop ids (`schedule_batch.py:1783-1803`) | none; a *set*, not a single sequence |

Turn end deserves its own note.  `_check_token_based_finish` matches
`stop_token_ids`, `eos_token_ids`, `tokenizer.eos_token_id` and
`tokenizer.additional_stop_token_ids` (`schedule_batch.py:1790-1799`), so a GLM
turn can end on more than one id.  One marker sequence cannot attest that, which
is why the delivered module resolves the turn-end *marker* from `eos_token` and
exposes the stop *set* separately.

## 4. `span_policy_v2`: versioned design

Name: `content_tail_v2` family.  Version token in the policy name, plus a marker
profile identifier, plus a marker schema identifier.  All three are needed
because the selection semantics, the marker table and the table's shape can
change independently.

```
marker schema   occ3.span.markers.v1
marker profile  glm45_glm47_xml_kv.v1
policies        content_tail_no_tool_v2   (default)
                content_tail_v2
                content_tail_last64_v2
                content_tail_last32_v2
retired         whole_completed_response_tail_v1  (v1 captures only)
```

The v2 family deliberately has **no** `last_accepted` member: a whole-response
tail is not a defensible claim, which is exactly bug RC-2
(`reports/occ3-statebridge-recovery/BUG-LEDGER.md:6`).

### 4.1 Inputs

1. the retained window ids, in ring order (`latent_worker.py:314-321`);
2. their absolute positions, unit-stride as `commit_result` already proves
   (`latent_worker.py:746-749`);
3. `accepted_total = record.accepted_count`;
4. `finished_len` equality: the window ends at the finish boundary
   (`batch_result_processor.py:213-214`);
5. an attested `MarkerTable` for the profile (section 5);
6. the stop-id set (`resolve_stop_ids`);
7. optionally `req.reasoning_tokens` for a cross-check (`schedule_batch.py:2151-2170`).

### 4.2 Rules, in order, first failure wins (fail-closed)

| # | rule | trigger | code | why |
| --- | --- | --- | --- | --- |
| R0 | attestation | no `MarkerTable` for the profile, or any required role unresolved | `profile` | a guessed marker table misclassifies silently while every count invariant still holds |
| R1 | bounds | `retained_count < 2`, or positions not strictly increasing | `lineage` | mirrors the existing `n >= 2` (`latent_worker.py:310`) and `_check_positions` (`span_audit.py:427-441`) |
| R2 | window identity | `retained_count != accepted_span_end - accepted_span_start` | `lineage` | today only `<=` is checked (`attachment_contract.py:332`) |
| R3 | turn end | last retained row's id is not in the stop set | `lineage` | proves the window ends at the finish boundary rather than mid-stream |
| R4 | reasoning terminator | neither thinking-end nor tool-call-start is in the window | `lineage` | without a terminator, content cannot be proven to start at a boundary |
| R5 | reasoning region | rows before the first terminator are REASONING, where the terminator is thinking-end **or** tool-call-start (`reasoning_parser.py:936,165-177`) | - | rules out calling reasoning "content" on a `<tool_call>`-terminated block |
| R6 | tool-call completeness | tool-call-start present without tool-call-end | `lineage` | the window would end inside a tool call |
| R7 | content region | the maximal run after R5 that contains no marker id, is before the first tool-call-start, and stops at the stop row | - | this is the only run a receiver may treat as "the answer" |
| R8 | non-empty | that run is empty | `lineage` | `final_content*` returns `None` today (`span_audit.py:612-614`); v2 must decline, not fall back |
| R9 | no-tool policy | `content_tail_no_tool_v2` and the window holds tool syntax | `lineage` | the safest policy must refuse a handoff turn, loudly |
| R10 | marker inside selection | any marker id sequence occurs at any offset inside the selected run | `lineage` | catches a partial marker at a run edge |
| R11 | cross-check | `req.reasoning_tokens` is available and disagrees with the R5 boundary | `lineage` | two independent measurements of the same boundary; a disagreement is a lineage fault |
| R12 | bounded resources | `runs_truncated`, or an unattainable tail limit | `lineage` | `span_audit` already refuses rather than guesses (`span_audit.py:283-293`) |
| R13 | default | any rule that cannot be evaluated | `lineage` | never fall back to `last_accepted` |

Codes are the existing `LatentError` vocabulary (`latent_types.py:19-21`); no new
code string is introduced.

### 4.3 Which `span_audit` primitives v2 can reuse, and what is missing

Reusable as-is: exact-id matching (`span_audit.py:375-408`), contradiction
checks (`:444-459`), per-class spans/runs, `contiguous`, `message_truncated`,
`tool_call_complete`, and the `final_content*` selections.

Missing, and therefore part of the v2 design rather than of this change:

1. **a second reasoning terminator.** R5 needs "thinking-end **or**
   tool-call-start".  `classify_accepted` knows one thinking-end marker
   (`span_audit.py:507-511`) and assigns pre-tool rows to `UNKNOWN`
   (`span_audit.py:542-554`).  Measured: on the tool-terminated window the
   reasoning rows are `UNKNOWN`, not `THINKING`
   (`tests/test_span_markers.py::test_integration_reasoning_terminated_by_tool_call_stays_unclassified`).
   v2 needs either an extra keyword on `classify_accepted` or a second call with
   the tool marker passed as `thinking_end_ids`.
2. **argument delimiters.** R10 needs all eight marker sequences; `SpanMarkers`
   carries four.  Widening `SpanMarkers` is a `span_audit` contract change (its
   `wire()` and `summary()` are allowlisted).
3. **a stop-row predicate.** `classify_accepted` has no notion of a stop id; R3
   needs the set from `resolve_stop_ids`.

None of the three is implemented here.

### 4.4 What each v2 policy selects

| policy | selects | satisfiable when | residual risk |
| --- | --- | --- | --- |
| `content_tail_no_tool_v2` | the R7 run, only if the window holds no tool syntax | a reasoning turn with a text answer | LOW. Refuses every `occ3_handoff` turn instead of guessing |
| `content_tail_v2` | the R7 run, tolerating tool syntax after it | content-then-tool-call turns | MEDIUM. The run is pre-tool prose, not the turn's final output; the receipt must say so |
| `content_tail_last64_v2` | last 64 rows of that run | as above | MEDIUM. A 64-row suffix can cut mid-answer |
| `content_tail_last32_v2` | last 32 rows | as above | HIGH. Experiment-only |

Ordering is by semantic risk to a receiver that treats the prefix as "the
answer", matching the v1 ranking in
`reports/occ3-statebridge-recovery/SOURCE-SPAN-AUDIT.md` §6.

## 5. The delivered marker table (`occ3_sglang/span_markers.py`)

New module, 514 lines, no `sglang` import, no torch, no file or network access.
It resolves and attests markers; it does not classify and does not select.

### 5.1 Contract

`resolve_marker_table(tokenizer, *, profile=DEFAULT_PROFILE,
require_roundtrip=True, allow_absent=()) -> MarkerTable` where `MarkerTable` is a
frozen dataclass with nine marker fields (one per role, `ROLES`), plus `profile`,
`schema`, `roundtrip_attested` and a `provenance` tuple.

Resolution rules, each raising `MarkerResolutionError`:

| condition | `reason` |
| --- | --- |
| encode is not callable, or does not accept `add_special_tokens=False` | `no_tokenizer` |
| encode raises | `encode_failed` |
| encode result is None/str/bytes, holds a bool, a negative id or a non-integer | `shape` |
| encode result is empty for a required role | `missing` |
| more than 32 ids, or a stop set above 32 ids | `bounds` |
| an id at or above the known vocabulary bound | `vocab` |
| two role spellings resolve to different sequences | `ambiguous` |
| two roles share a sequence, or one is a proper prefix of another | `ambiguous` / `prefix` |
| `decode(ids)` does not reproduce the marker string exactly | `unattested` |
| a role needing a tokenizer attribute has none (turn end has no `eos_token`) | `missing` |
| unknown profile, or `allow_absent` naming a non-absentable role | `profile` |

Three design choices are worth defending:

1. **Round-trip decode is required.**  An unknown-token substitution or a
   normalising decoder would otherwise pass as a marker.  The escape hatch is
   `require_roundtrip=False`, which is recorded in `roundtrip_attested` and in
   `wire()`.
2. **The turn-end role is attested only by the tokenizer's own `eos_token`.**
   A corroborating literal (`"<|endoftext|>"`) must agree with it, and a literal
   alone is refused: byte-fallback text encodes cleanly while the model may never
   emit it.
3. **Refusal messages carry no marker text and no ids.**  `code` is `"profile"`,
   `str()` is `"profile"`, and `repr()` shows only `code`, `reason`, `role`.

`MarkerTable.span_markers()` returns a `SpanMarkers` with the four fields
`span_audit` understands, so the two modules compose without either one changing.
`MarkerTable.wire()` returns profile, per-role length (or `None`) and per-role
source - never ids, never strings.

### 5.2 Test evidence (OFFLINE MEASURED)

Command:

```
cd C:/Users/openclawuser/optimus-occ3-latent-validation/extensions/sglang-occ3
.venv/Scripts/python.exe -m pytest tests/test_span_markers.py -q
```

Result: **40 passed**, exit code 0, 0.31 s.

Command:

```
.venv/Scripts/python.exe -m pytest tests/test_span_markers.py tests/test_span_audit.py -q
```

Result: **86 passed**, exit code 0, 0.53 s - the new file does not disturb the
existing 46 `span_audit` tests.

Coverage: all nine roles resolved with exact ids; `add_special_tokens=False`
asserted on every encode call; frozen-table and id-free `repr`/`wire`; derived
`content`/`arguments` views; the `SpanMarkers` bridge; a disagreeing
corroborating literal (`ambiguous`); shared and prefix sequences
(`ambiguous`/`prefix`, reachable only with `require_roundtrip=False` - with
round-trip attestation on, a collision is caught earlier as `unattested`, which
is itself asserted); multi-token markers attested through a byte fallback;
missing encoder, missing spelling, missing `eos_token`, literal-only turn end,
narrow vocabulary; normalised decode, absent decode, unknown substitution with
round-trip disabled; eight bad encode shapes; declared absence of the
thinking-start role and refusal to excuse any other role; the stop-id union,
empty set and out-of-range set; four integration tests against
`classify_accepted`; and a source scan for forbidden imports.

## 6. Receipt and lineage changes v2 requires

Nothing below is implemented.  All of it is a schema bump on both ends, because
both ends are exact-set parsers (`attachment_contract.py:341-342`,
`crates/pi-ai/src/source_capture.rs:89` `deny_unknown_fields`).

### 6.1 Descriptor and contract (internal)

1. `CaptureIntent.span_policy` must accept the v2 names and, when it does,
   require `schema == "occ3.capture.v2"`; `attachment_contract.py:151,162-164`.
   The Rust twin is a constant plus one comparison
   (`crates/pi-ai/src/source_capture.rs:22,53`), so it needs the same enum and a
   schema-gated branch.
2. `CaptureWorkerDescriptor.span_policy` likewise, plus a new
   `marker_profile: str`, and the resolved ids by the route in the next
   paragraph.  Since
   `normalize_worker_descriptor` refuses non-`str`/`int`/`float` leaves
   (`attachment_contract.py:479-480`), the ids should **not** travel there; use
   the existing private channel instead:
   `sampling_params.custom_params["__optimus_occ3_span_marker_<role>"] =
   [int]` for the eight non-turn-end roles, mirroring
   `__sglang_reasoning_end_token_ids` (`sampling_params.py:52-111`), bounded to
   32 ids per role.  One key per role keeps `CustomParamValue` honest
   (`sampling_params.py:33-37` allows `List[scalar]`, not `Dict[str, List]`).
3. `SourceToolProvenance.scope` must accept the v2 policy name
   (`use_receipts.py:377-383`).

### 6.2 Public receipt `occ3.source.v2`

Keep the 21 v1 fields and their meaning: `accepted_span_start/_end` stay the
**aligned** span, so `byte_count == retained_count * hidden_dim * dtype_bytes`
and `retained_count <= span width` keep holding (`attachment_contract.py:330-337`,
`crates/pi-ai/src/source_capture.rs:501-528`).  Add one bounded block:

| field | type / bound | purpose |
| --- | --- | --- |
| `span_schema` | const `"occ3.span.v1"` | version of the block |
| `span_policy` | enum of the four v2 names | what the window claims |
| `span_marker_profile` | identifier <= 64 | which reviewed table was used |
| `span_marker_schema` | const `"occ3.span.markers.v1"` | shape of that table |
| `span_reasoning_terminator` | enum `thinking_end` / `tool_call_start` / `none` | which R5 terminator fired |
| `span_turn_end_attested` | bool | R3 fired |
| `span_thinking_count` .. `span_unknown_count` | u32 <= 128 each | per-class counts |
| `span_window_start_position`, `span_window_end_position` | u64 | absolute positions of the **retained window**, which v1 cannot express |
| `span_contiguous` | bool | unit-stride claim |
| `span_contains_final_content`, `span_contains_tool_call`, `span_message_truncated` | bool | flags a receiver can act on |

Cross-field invariants (both ends): `sum(counts) == retained_count` **when the
aligned span equals the window**, otherwise `sum(counts) == window width`;
`retained_count <= window width <= accepted_total`; `span_policy` and
`span_reasoning_terminator` agree (a `content_tail_no_tool_v2` receipt with tool
syntax is invalid); `span_turn_end_attested` is false only when
`span_message_truncated` is true.

Headroom (OFFLINE MEASURED, same probe):

| measurement | bytes |
| --- | --- |
| max-width `occ3.source.v1` receipt (`source_receipt_upper_bound(2048)`) | 1234 |
| candidate v2 span block, canonical JSON, worst-case widths | 613 |
| projected v2 receipt | 1848 |
| `MAX_RECEIPT_BYTES` (`attachment_contract.py:11`) | 2048 |

So the block fits, with 200 bytes of slack.  Whether it still fits the adapter's
header budget under a live ASGI header set is **HYPOTHESIS**; not measured here.
The fixtures that would also need the new fields are
`extensions/sglang-occ3/tests/fixtures/occ3_source_v1.json` (14 receipt-like
objects, `rg -c span_policy`) and
`crates/pi-coding-agent/examples/occ3_native_phase_suite.rs:3573`.

### 6.3 Lineage

The commit digest today covers `reservation_id`, the window bounds, the window
token digest and the layout digest (`latent_worker.py:349`).  Under v2 the
aligned prefix is a **sub-range** of the window, so the digest must also bind
`span_policy`, `span_marker_profile`, the selected sub-range and the reasoning
terminator.  Without that, two different v2 selections over the same window
produce the same `capture_commit_id`, and the receipt cannot be re-derived from
the commit.

The consume path is unaffected: the receiver takes the row count from
`source["retained_count"] -> expected_prefix_tokens`
(`latent_backend.py:178`) and checks it against the descriptor
(`attachment_contract.py:393-395`), so a shorter aligned prefix stays a valid
prefix.  Placement is independent of length
(`latent_worker.py:710-713`).

## 7. What this task did not do

1. No wiring: `span_markers` is imported by its test only.  The worker, the
   adapter, the descriptor and the receipt are untouched.
2. No `span_policy` semantics changed: `attachment_contract.py`, `latent_backend.py`,
   `chat_adapter.py` and `use_receipts.py` still admit exactly
   `"whole_completed_response_tail_v1"` (SOURCE-ATTESTED by mtime and by `rg`: the
   only values in the tree are that constant and tests that assert it).
3. No tokenizer was run.  Every marker id in this report comes from the fake
   tokenizer in the test file.  The real GLM tokenizer's ids are unmeasured here;
   what is attested is the marker *string*, plus the code path that encodes it
   (`reasoning_parser.py:117-118`, `scheduler.py:922-926`).
4. Three `span_audit` gaps remain open (section 4.3).
5. `req.reasoning_tokens` is proposed as a cross-check (R11) but not verified
   against a live turn: for a `<tool_call>`-terminated thinking block the counter
   never matches and keeps counting, which is why R11 is a cross-check and not a
   source of truth (**HYPOTHESIS**, from `schedule_batch.py:2151-2170` plus
   `reasoning_parser.py:165-177`).

## 8. Reproduction

```
cd C:/Users/openclawuser/optimus-occ3-latent-validation/extensions/sglang-occ3

# 40 passed
.venv/Scripts/python.exe -m pytest tests/test_span_markers.py -q

# 86 passed (new file plus the existing span_audit suite)
.venv/Scripts/python.exe -m pytest tests/test_span_markers.py tests/test_span_audit.py -q

# every number in sections 1.4 and 6.2
.venv/Scripts/python.exe ../../reports/occ3-alignment-repair/probes/probe_span_markers.py
```

Files added by this task:

| file | lines | purpose |
| --- | --- | --- |
| `extensions/sglang-occ3/occ3_sglang/span_markers.py` | 514 | tokenizer-attested marker table, fail-closed |
| `extensions/sglang-occ3/tests/test_span_markers.py` | 486 | 40 tests, fake tokenizer, no SGLang |
| `reports/occ3-alignment-repair/probes/probe_span_markers.py` | 141 | reproduces every measured number |

No existing file was modified.
