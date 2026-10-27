# OCC-3 source span audit: what the accepted window actually contains

Status: **offline design + implementation + tests**. No GPU, no DGX session, no
deployment, no wiring into `latent_worker.py` or `chat_adapter.py`, no receipt
change. Everything below is read from this worktree at the digests in
[§1](#1-anchor) or reproduced by `tests/test_span_audit.py`.

Legend: **VERIFIED** = read at the cited `file:line` in this worktree, or
reproduced by the cited test. **INFERENCE** = reasoned from verified facts but
not executed (no live window, no Rust build, no full-suite run). **UNKNOWN** =
no evidence found.

## 1. Anchor

Line numbers are pinned to these file digests, because the worktree changed
underneath this audit (see [§8](#8-open-risks)).

| file | lines | sha256[:16] (state A) | other state seen during the audit |
| --- | --- | --- | --- |
| `extensions/sglang-occ3/occ3_sglang/latent_worker.py` | 962 | `f5c95bfffc9f7efc` | `86b5f972f28eecdb` (989 lines) |
| `extensions/sglang-occ3/occ3_sglang/latent_backend.py` | 283 | `87e813da2625f89a` | `6ffb2f594165f656` (285 lines) |
| `extensions/sglang-occ3/occ3_sglang/attachment_contract.py` | 486 | `2bec5c8f99736aec` | `b4c6a8fe66ad3758` (491 lines) |
| `extensions/sglang-occ3/occ3_sglang/use_receipts.py` | 494 | `78985d206bd947f4` | unchanged |
| `extensions/sglang-occ3/occ3_sglang/chat_adapter.py` | 1603 | `d5c51a162d9e52f7` | `d8eb3cc9056e2c60` (1647 lines) |
| `extensions/sglang-occ3/occ3_sglang/latent_types.py` | 225 | `d6dfc9e2dc3e9e54` | unchanged |
| `crates/pi-ai/src/source_capture.rs` | 676 | `199017cbf6145957` | unchanged |
| `crates/pi-coding-agent/src/core/agent_session/occ3_state.rs` | 829 | `f969aa2882f31ac5` | `4f8ba6a5eaf00f1c` |
| `extensions/sglang-occ3/occ3_sglang/span_audit.py` (new) | 623 | `ed0b0e43183ecf3d` | content-stable (CRLF rewrite only) |
| `extensions/sglang-occ3/tests/test_span_audit.py` (new) | 359 | `9554911480e6d68e` | content-stable (CRLF rewrite only) |

All line citations below are for **state A**. The tree alternated between A and
the other state four times during this audit; when a file does not match the
state-A digest, re-derive each citation with `rg -n '<symbol>' <file>` (the
alternate snapshot puts `latent_worker.align` at 304, `latent_backend`'s source
enrollment at 209 and `CaptureWorkerDescriptor` at 403). Digests of the two new
files are over content with `\n` line endings, so a CRLF rewrite does not change
them.

## 2. What we can and cannot classify today

### 2.1 Available to the worker (VERIFIED)

- The retained window is the **last `n` accepted rows**, `n = min(accepted_count,
  max_prefix_tokens)` — `latent_worker.py:308`, ring order at `313-319`,
  `start = end - n` at `345-346`. Nothing selects or labels a class of row.
- The ring holds **token ids and hidden states only** (`latent_worker.py:743-745`,
  write at `747-750`). Absolute positions are *not* stored in the ring.
- Positions do exist at commit time: `latent_worker.py:722-723` builds
  `arange(len(origin_input_ids) + output_start - 1, ... + output_end - 1)` and
  `724-725` requires it to equal the accepted row positions. So accepted
  positions are unit-stride and reconstructable as `len(origin_input_ids) - 1 + i`,
  but no code keeps them past the chunk. (**INFERENCE** for the reconstruction
  formula's global validity; **VERIFIED** that the equality check makes each
  committed chunk unit-stride.)
- The commit digest covers the retained ids only, `sequence_digest =
  digest(list(req.output_ids[start:end]))` (`latent_worker.py:347`). It proves the
  window, never its content.
- After alignment the ids are **dropped**: `record.retained.clear()`
  (`latent_worker.py:362`). A post-commit audit is impossible without a new
  capture; classification must run inside `align()` or be given the ids again.

### 2.2 Not available anywhere today (VERIFIED)

- **No span or class metadata exists.** The source receipt is 21 fields
  (`attachment_contract.py:294-345`); the only span facts are
  `accepted_span_start` (`301`), `accepted_span_end`, `retained_count` (`303`)
  and their consistency checks (`330-332`). The worker metadata dict
  (`latent_worker.py:349-356`) adds no class data. The Rust mirror is
  `source_capture.rs:88-113` with `#[serde(deny_unknown_fields)]` (`89`).
- **No marker ids reach the extension.** `rg` over `occ3_sglang/` finds no use of
  `reasoning_end_token_ids`; the only occurrence in the repo is a test stub
  (`tests/test_chat_attachment.py:192`). Pinned SGLang fills that field only for
  `reasoning_parser == "k2_horizon"` (`.../serving_chat.py:1435-1459`,
  `.../serving_chat.py:1444`), while the recorded DGX launch uses
  `--reasoning-parser glm45 --tool-call-parser glm47`
  (`reports/occ3/DGX-ENVIRONMENT.md:24`). So the field is empty for this
  deployment (**VERIFIED** that it is k2_horizon-gated; **INFERENCE** that no
  other producer exists — a `rg` for `think_end_token` finds only
  `serving_chat.py:1380,1444,2803`).
- **No join key between the frontend stream and worker rows.** The frontend does
  see reasoning/content/tool deltas, and `NativeOutputAudit._source_tools`
  (`use_receipts.py:135-163`) keeps at most one call's `id` and `name` for the
  whole response (`SourceToolProvenance`, `use_receipts.py:375-389`). It keeps no
  offsets, no token counts, no boundaries, and the receipt has no position field
  to bind them to.
- **The span policy is a single constant.** `CaptureIntent.span_policy` defaults
  to and must equal `"whole_completed_response_tail_v1"`
  (`attachment_contract.py:151,163`); the descriptor repeats the check
  (`417,432`); the backend copies it
  (`latent_backend.py:222`). No policy enum exists.
- **Correction to the task brief:** `SourceEnrollment` has **no** `span_policy`,
  `max_prefix_tokens` or `retained_count_limit` field
  (`attachment_contract.py:256-274`). Those live on `CaptureIntent`
  (`147,151`) and `CaptureWorkerDescriptor` (`417-419`); the enrollment carries
  identity and layout only, and `latent_backend.py:224-226` builds it that way.

### 2.3 The consequence (INFERENCE)

A receiver today gets `retained_count` hidden rows and cannot tell whether row 0
is mid-reasoning, whether the tail is a tool-call JSON payload, or whether the
window even starts at the beginning of the message. Any semantic claim that
treats the prefix as "the answer" is unfalsifiable.

## 3. The auditor delivered by this change

`extensions/sglang-occ3/occ3_sglang/span_audit.py` — pure Python, no torch, no
sglang, no I/O. `classify_accepted(token_ids, positions, markers, *,
accepted_total=None) -> SpanAudit` compares **integer id sequences only**; a
marker matches at `i` only when every id equals, so there is no text, no
substring search and no decoding.

Classes (fixed order): `THINKING`, `CONTENT`, `TOOL_CALL`, `TOOL_ARGUMENTS`,
`CONTROL_MARKERS`, `UNKNOWN`. The first occurrence of each supplied marker's
tokens become `CONTROL_MARKERS`; the regions between markers get the class the
marker proves; anything unproven stays `UNKNOWN`. With no markers supplied every
row is `UNKNOWN` — the module never guesses from token values or counts.

`SpanAudit` carries counts, per-class index/position ranges, maximal runs, the
selected window positions, and the booleans `contains_final_content`,
`contains_tool_call`, `message_truncated`, `contiguous`, `tool_call_complete`.
`summary()` returns exactly `SUMMARY_KEYS`, 18 int/bool entries.

Fail-closed (`SpanContractError`, a `LatentError` **and** `ValueError`, code
`"lineage"`):

| condition | result |
| --- | --- |
| length mismatch, empty window, non-int id, negative id, bool id | decline |
| positions not strictly increasing | decline |
| `tool_call_end` without `tool_call_begin` | decline |
| tool call begins before thinking ends (incl. overlap) | decline |
| tool call end before begin end | decline |
| message end before thinking end / before tool begin / before tool end | decline |
| marker longer than 32 ids, text/bytes passed as a marker | decline |
| more than `2**20` accepted rows | `quota` |
| unknown policy name, non-`SpanAudit` argument | decline |

Bounds: O(n) time, one `bytearray(n)` scratch, runs capped at 16 (overflow sets
`runs_truncated` and makes content policies return `None` instead of guessing).

Policies (`span_from_policy`): `last_accepted`, `final_content`,
`final_content_no_tool`, `final_content_last64`, `final_content_last32`. Each
returns a `SpanSelection` (`start_index`, `end_index`, `contiguous`, `clamped`,
`requested`) or `None`. `contiguous=False` whenever the selected rows are not one
absolute position apart, so a policy can never fabricate a contiguous claim.

## 4. Metadata that must be added to the source receipt

Nothing below is implemented. It is the minimum to make [§2](#2-what-we-can-and-cannot-classify-today)
classifiable, and every field is a bounded int/bool/identifier — no ids, no text.

### 4.1 Public receipt (`occ3.source.v2`)

| field | type / bound | purpose |
| --- | --- | --- |
| `span_schema` | const `"occ3.span.v1"` | version of the span block |
| `span_policy` | enum of the 5 policies | what the window claims to be |
| `span_marker_profile` | identifier ≤ 64 | which reviewed marker table was used |
| `span_thinking_count` | u32 ≤ 128 | per-class counts |
| `span_content_count` | u32 ≤ 128 | |
| `span_tool_call_count` | u32 ≤ 128 | |
| `span_tool_arguments_count` | u32 ≤ 128 | |
| `span_control_count` | u32 ≤ 128 | |
| `span_unknown_count` | u32 ≤ 128 | |
| `span_window_start_position` | u64 | absolute position of retained row 0 |
| `span_window_end_position` | u64 | exclusive end position |
| `span_contiguous` | bool | retained rows are one position apart |
| `span_contains_final_content` | bool | |
| `span_contains_tool_call` | bool | |
| `span_message_truncated` | bool | earlier accepted rows are not in the window |

Cross-field invariants both ends must enforce:

- `sum(counts) == retained_count`;
- `retained_count == accepted_span_end - accepted_span_start` (today only
  `retained_count <= width` is checked, `attachment_contract.py:332`,
  `source_capture.rs:513`);
- `span_window_end_position - span_window_start_position == retained_count` when
  `span_contiguous`, and `>= retained_count` otherwise;
- `span_policy == "final_content_no_tool"` implies `span_contains_tool_call` is
  false;
- `span_contains_final_content` false implies every content policy selects
  nothing.

Cost: the maximum-width receipt measures **1234 bytes** with
`source_receipt_upper_bound(2048)` (VERIFIED, executed against this tree). A
14-field variant of the block serialises to **551 bytes** as canonical JSON, so
the worst case projects to ≈ **1.8 KB**, under `MAX_RECEIPT_BYTES = 2048`
(`attachment_contract.py:11`). Whether it still fits the adapter's header budget
(`chat_adapter.py:225`, reserve check near `235-240`) is **INFERENCE** — not
executed against a live ASGI header set.

Migration cost (**VERIFIED**, not done): the Python parser rejects any unknown or
missing key (`SourceCaptureReceipt.parse`, `attachment_contract.py:341-342`), and
Rust denies unknown fields (`source_capture.rs:89`). A receipt change is therefore
a schema bump on both sides plus `source_receipt_upper_bound`
(`use_receipts.py:436-448`), `validate_source_evidence` (`467-494`) and
`validate_receipt` (`source_capture.rs:471-530`). Do not add optional fields with
defaults; both ends are exact-set parsers.

### 4.2 Internal control payload (not the receipt)

The marker ids themselves must reach the worker through the worker descriptor,
never through the HTTP receipt. `CaptureWorkerDescriptor` (`attachment_contract.py:398-438`)
is already an exact-set, strictly validated struct, `normalize_worker_descriptor`
(`attachment_contract.py:468`) rejects non-`str`/`int`/`float` leaves, and
`bounded_payload` (`latent_types.py:67-93`) caps dict keys at 64, lists at 128
items and the canonical form at 16384 bytes; four markers of ≤ 32 ints each
(≈ 2.5 KB canonical) fit. Cheaper and more auditable: send `marker_profile`
(identifier) and keep the reviewed id table inside the extension, so ids never
travel on any wire.

### 4.3 Where the values come from

The tokenizer-manager process must encode the template's structural strings once
per request and publish ids to (a) the descriptor for the worker and (b) the
receipt's bounded summary. Pinned SGLang already encodes such strings for one
parser only (`serving_chat.py:1443-1459`, gated at `1435`) and exposes the
detector's `think_end_token` at `2800-2804`; for `glm45`/`glm47` the extension must
do the encoding itself. The exact `glm47` structural token table was **not
located** in the pinned extract (**UNKNOWN**).

## 5. Privacy rules

1. Never log, attach or persist token ids, decoded text, hidden states, or a
   rendered marker. The auditor's records are counts, ranges and flags only
   (VERIFIED by construction; `test_summary_is_a_fixed_int_bool_allowlist`).
2. Log `SpanAudit.summary()` or `repr(SpanAudit)`, never `token_ids`, never the
   sequences passed to `classify_accepted`.
3. `SpanMarkers` prints lengths only (`span_audit.py` `__repr__`), because the
   record does hold ids in memory. `test_frozen_records_carry_no_ids` asserts
   that neither marker ids nor token ids appear in any repr.
4. Declines carry a fixed code only, following the existing
   `_decline_code` rule (`latent_worker.py:27-33`) and the sanitised log line at
   `chat_adapter.py:768`. A `SpanContractError` message is the code string.
5. The receipt carries `marker_profile` and counts; it must never carry marker
   ids. Do not put a digest of the marker id sequence on the public wire either:
   short special-token sequences are low entropy and trivially confirmable
   against a public tokenizer. If a digest is wanted, digest `SpanMarkers.wire()`
   (lengths only).
6. Hidden states never enter this module; it only reads ids and positions
   (VERIFIED: no tensor or state import in `span_audit.py`).
7. `summary()` is bounded and allowlisted so it can be logged verbatim; do not
   widen it with free-form strings.

## 6. Ranked span policies (safest first)

Ranking is by **semantic risk to a receiver that treats the prefix as "the
answer"**, not by implementation risk (all five are equally cheap).

| # | policy | selects | satisfiable when | risk |
| --- | --- | --- | --- | --- |
| 1 | `final_content_no_tool` | last `CONTENT` run, only if the window holds no tool-call syntax | reasoning turn with a text answer | **LOW** — refuses the common tool-call turn (`None`) instead of guessing. Cost: rarely usable on `occ3_handoff` turns. |
| 2 | `final_content` | last `CONTENT` run, tool call or not | `thinking_end` observed and content non-empty | **MEDIUM** — on a mixed turn it can select pre-tool prose that is *not* the turn's final output; the caller must also read `contains_tool_call`. |
| 3 | `final_content_last64` | last 64 rows of that run (`clamped` when shorter) | as above | **MEDIUM** — bounded and tail-exact, but a 64-token suffix can cut mid-answer and drop the fact; interacts with `retained_count_limit` ≤ 128. |
| 4 | `final_content_last32` | last 32 rows | as above | **HIGH** — shortest window; only defensible for latency experiments, not for semantic evaluation. |
| 5 | `last_accepted` (today) | every retained row | always | **HIGHEST** — mixes reasoning, content, tool syntax and arguments in one unattributed prefix. This is the current production profile (`whole_completed_response_tail_v1`), so it is the status quo risk, not a regression. |

All five report `contiguous=False` rather than claim a contiguous span when the
retained rows have position gaps. In the current commit path the window is
unit-stride by construction (`latent_worker.py:722-725`), so `contiguous=False`
would itself be a lineage signal.

## 7. Test evidence

Command (exact):

```
cd C:/Users/openclawuser/optimus-occ3-latent-validation/extensions/sglang-occ3
.venv/Scripts/python.exe -m pytest tests/test_span_audit.py -q
```

Result: **46 passed**, exit code 0, 0.22-0.34 s wall across repeat runs
(VERIFIED, run after the final restore of both files).

Coverage includes marker at index 0, marker at end, marker absent, marker
repeated, thinking-end-then-tool-call, content-only selection, `last64`/`last32`
clamping both ways, discontiguous positions (window-level and inner run), the
unknown-marker case, the fixed int/bool summary allowlist, ten fail-closed
input classes, six invalid-marker classes, unknown policy names, `accepted_total`
truncation, frozen-record and id-free repr checks, an `array`/`tuple` input
parity check, run-list overflow, and a performance guard.

Performance guard: 4096 ids with four markers, best of three inside the test —
**< 50 ms asserted**; a separate seven-run measurement gave **1.84 ms** for the
same input and **0.06 ms** for a 4-row window (VERIFIED, this machine).

Not covered: no GPU, no real tokenizer, no live window, no Rust build, no full
extension suite, no wiring.

## 8. Open risks

1. **The worktree is being modified by another actor.** During this audit
   `latent_worker.py`, `latent_backend.py`, `attachment_contract.py` and
   `chat_adapter.py` gained and then lost a `prefix_layout`/`prefix_offset`
   change, alternating four times (962 ↔ 989 lines for `latent_worker.py`, 283 ↔
   285, 486 ↔ 491, 1603 ↔ 1647). Both new files of this task were deleted once
   (restored) and later rewritten with CRLF endings (content identical). Line
   numbers in this report match state A of [§1](#1-anchor); re-derive by symbol if
   the digests differ, and do not trust any line number that was copied out of
   this report after the tree moved again.
2. **Marker provenance is unproven.** The auditor trusts the ids it is given.
   A wrong marker table silently misclassifies while every invariant still holds.
   Mitigations: `marker_profile` on the receipt, the `sum(counts) ==
   retained_count` invariant, and `UNKNOWN` when a marker is absent.
3. **No `glm45`/`glm47` marker ids exist yet** in this repo or, as far as the
   pinned extract shows, in the served parser path (UNKNOWN). Until they are
   produced by the tokenizer manager, every audit will be all-`UNKNOWN`, which is
   the honest outcome.
4. **Repeated markers** are resolved by first occurrence; a template that emits
   a terminator twice relies on that rule, and the repeat count is reported only
   as `marker_repeats`.
5. **Mixed turns**: `CONTENT` before a tool call is still `CONTENT`, so
   `final_content` can name prose that is not the turn's final output. Use
   `final_content_no_tool` when that distinction matters.
6. **A 128-row window is small.** If reasoning fills the window, no content may
   be visible at all; `message_truncated` says so, but the receiver still cannot
   see the answer. This is a product decision, not a classifier defect.
7. **Nothing is wired.** `span_audit` is imported by its test only; the worker,
   the adapter, the receipt and the Rust validator are untouched.

## 9. Ordered next steps (smallest first)

1. Add `marker_profile` + the four bounded marker tuples to
   `CaptureWorkerDescriptor` and validate them in `normalize_worker_descriptor`;
   no receipt change yet.
2. Compute a `SpanAudit` inside `align()` from the ring ids plus reconstructed
   positions, log `summary()` only, and assert `sum(counts) == n` before
   `consensus`. Still no receipt change.
3. Only then bump the receipt to `occ3.source.v2` with [§4.1](#41-public-receipt-occ3sourcev2),
   in lockstep with `source_capture.rs` and both validators.
4. Add the live-window regression that a tool-call turn yields
   `final_content is None` and `contains_tool_call is True`.
