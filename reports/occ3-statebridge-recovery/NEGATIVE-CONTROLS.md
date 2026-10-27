# Negative controls and semantic-leakage gate for the OCC-3 receiver payload

What this file defines: the exact matched control matrix for an OCC-3 receiver
trial, what is held constant, what is allowed to differ, which controls are
**not** matched today with `file:line` evidence, and the test that pins each
claim.

Test suite: `extensions/sglang-occ3/tests/test_semantic_leakage.py` (new, 54
cases: 52 pass, 2 `xfail(strict=True)` for requirements the current code does
not meet). Run from `extensions/sglang-occ3`:

```
.venv/Scripts/python.exe -m pytest tests/test_semantic_leakage.py -q
52 passed, 2 xfailed in 0.60s
```

Offline only: no GPU, no network, no SGLang install, no Rust build. Tests that
need `OCC3_SGLANG_SOURCE` are not used; the shared wire fixture
`test_chat_attachment.attachment()` is imported instead.

**This worktree is being edited concurrently.** `chat_adapter.py` and
`occ3_native_handoff.rs` both changed while this file was written. Line numbers
below are for the digests in section 0; trust the digest and the symbol name,
not the number.

## 0. Read snapshot

| file | sha256 (first 16) | bytes |
|---|---|---|
| `extensions/sglang-occ3/occ3_sglang/chat_adapter.py` | `d8eb3cc9056e2c60` | 82873 |
| `extensions/sglang-occ3/occ3_sglang/use_receipts.py` | `78985d206bd947f4` | 22354 |
| `extensions/sglang-occ3/occ3_sglang/attachment_contract.py` | `b4c6a8fe66ad3758` | 18229 |
| `crates/pi-coding-agent/examples/occ3_native_phase_suite.rs` | `f2be2a32d1141ba6` | 160641 |
| `crates/pi-coding-agent/examples/occ3_native_handoff.rs` | `1b06b453c26a9933` | 49214 |
| `crates/pi-coding-agent/src/modes/daemon/occ3_native.rs` | `1583e83c8840d0ce` | 11224 |
| `crates/pi-coding-agent/src/core/agent_session/occ3_state.rs` | `f969aa2882f31ac5` | 38146 |
| `crates/pi-coding-agent/src/core/agent_messages.rs` | `10017a667d37d148` | 147453 |
| `crates/pi-coding-agent/src/core/occ/occ3/inference.rs` | `eff4fedafd8a3c03` | 16792 |
| `crates/pi-ai/src/providers/openai_completions.rs` | `6a23abee2331d330` | 273213 |
| `extensions/sglang-occ3/tests/test_semantic_leakage.py` (new) | `da2aec3bfb57cec1` | 40056 |

## 1. Requirement → test map

| id | requirement | test node(s) in `test_semantic_leakage.py` | kind | result |
|---|---|---|---|---|
| A | detector fails on exact, case-insensitive and whitespace-normalised matches | `test_detector_rejects_exact_case_and_whitespace_variants` (7), `test_detector_accepts_clean_text` (4), `test_detector_refuses_unusably_short_secrets` | helper | pass |
| B | positive receiver ordinary payload carries no fact / answer class / nonce / handle | `test_positive_receiver_payload_has_no_private_value` (2), `test_expected_answer_class_is_never_bound_to_the_private_fact`, `test_positive_receiver_payload_is_one_ordinary_user_message`, `test_real_adapter_keeps_the_private_values_out_of_the_model_prompt` | mirror + real | pass |
| B' | the detector is sensitive: it fires on the suite's own leak arms | `test_detector_flags_the_full_text_leak_control`, `test_detector_flags_a_tool_result_leak` | mirror | pass |
| C | native record is an exact allowlist with no plaintext secret | `test_attachment_wire_allowlist_is_pinned`, `test_native_receipt_header_is_an_exact_allowlist_without_plaintext`, `test_the_occ3_field_never_reaches_the_model_visible_body` | real | pass |
| D | positive vs no-state ordinary prompt parity | `test_state_bearing_controls_are_byte_identical_to_the_positive_payload`, `test_parity_ignores_only_the_delivery_record_envelope`, `test_native_record_is_the_only_difference_between_matched_cases`, `test_every_control_has_exactly_one_ordinary_user_message` | mirror | pass |
| D' | the same, against the *current* no-state text | `test_parity_ordinary_payload_is_byte_identical_positive_vs_no_state` | mirror | **xfail (unmet, §6.1)** |
| E | OCC-1 / OCC-2 / TXT fallback cannot pass as native | `test_fallback_bodies_are_not_accepted_as_a_native_control_summary` (5), `test_only_the_pinned_control_summary_is_accepted`, `test_fallback_and_text_only_evidence_cannot_pass_the_success_predicate` (6), `test_a_turn_with_fallback_evidence_returns_no_receipt`, `test_receipt_shaped_bytes_alone_are_not_a_success_predicate` | real + mirror | pass |
| F | an answer without a `Consumed` lease is not a success | `test_answer_without_a_consumed_lease_is_not_a_success` (5), `test_answer_with_a_consumed_lease_and_valid_evidence_is_a_success`, `test_success_needs_both_the_lease_and_the_evidence_record`, `test_answers_other_than_the_exact_nonce_are_not_a_success` (4), `test_no_state_control_passes_only_with_the_exact_unknown_answer` | mirror | pass |

## 2. The acceptance predicate, in one place

A trial is a *success* only when four independent gates all pass. No single gate
is a success predicate.

1. **Transport evidence (Python, this repo).**
   `validate_evidence` (`use_receipts.py:62-91`) requires
   `completed is True` (:79), `fallback_used is False`, `num_retractions == 0`,
   `private_cache_namespace is True`, `gpu_resident is True`, and
   `0 < injected_prefix_count <= 128` (:82, 70). A text-only or fallback
   delivery has no injected prefix, so it cannot satisfy this gate.
2. **Adapter receipt (Python, this repo).** The `x-optimus-occ3-receipt` header is
   emitted only after generation, inner success and `validate_evidence`
   (`chat_adapter.py:879`, header at `:884`). The bytes are the fixed
   `receipt_bytes()` blob (`use_receipts.py:54-59`), computed before generation at
   `chat_adapter.py:488`.
3. **Delivery state (Rust controller).** `send_prepared_native_handoff` refuses
   unless `native.outcome(id)? == Consumed` (`occ3_native.rs:217`); the audit
   projection reports `lease_state` as the `Debug` name of the lease state
   (`inference.rs:298`, enum at `pi-ai/src/inference_attachment.rs:51-57`, so the
   only accepted string is `Consumed`).
4. **Scorer.** Phase suite `phase_suite.rs:1497-1508` (positive) and
   `:1578-1587` (no-state); handoff suite `handoff.rs:186-263`.

Note: the two scorers disagree on answer normalisation. The phase suite requires
exact equality after ASCII trim (`:1473`, `:1573`); the handoff suite accepts a
substring (`:581`, `answer.contains(&nonce)`) and a trimmed case-insensitive
match (`:579`). A trial can pass one suite and fail the other. §6.7.

## 3. Matched control matrix

Every arm below runs the same receiver, the same system prompt, the same ordinary
task text and the same single user turn. Only the reserved OCC-3 record (the
`occ3` request field, never prompt text) changes.

| arm | ordinary text | reserved record | expected answer | passes only if | matched today |
|---|---|---|---|---|---|
| **correct state** | `CONTROL_SUMMARY` (`agent_messages.rs:2068`) | valid handle, `Consumed` | the nonce / the class | all four gates in §2 | yes (phase suite) |
| **no state** | should be `CONTROL_SUMMARY`; today `NO_STATE_TASK` (`phase_suite.rs:1564`) | absent | `UNKNOWN` | answer `UNKNOWN`, 0 messages, 0 lease records, 0 tool calls | **no** — text differs (§6.1) and framing differs (§6.2) |
| **unrelated state** | `CONTROL_SUMMARY` | another session's capture handle | `UNKNOWN` | as no-state | **absent** (§6.4) |
| **shuffled state** | `CONTROL_SUMMARY` | rows permuted / wrong layer / wrong span | `UNKNOWN` | as no-state | **absent** (§6.4) |
| **token-reference-only** | `CONTROL_SUMMARY` | handle id only, no latent rows | `UNKNOWN` | as no-state | **absent** (§6.4) |
| **random vectors** | `CONTROL_SUMMARY` | random tensor with the right geometry | `UNKNOWN` | as no-state | **absent** (§6.4) |
| **full text** | `CONTROL_SUMMARY` + the fact (`handoff.rs:85-86`) | absent | the nonce / the class | *must fail* `source_facts_absent` (`handoff.rs:219`) | present but deliberately unmatched — it is the detector-sensitivity arm, never a negative control that "passed" |

Parity is defined on the **ordinary task text**, not on the raw request body:
`parity_sha256()` in the test file removes the reserved field and the
agent-session delivery envelope, then hashes the canonical JSON. The reserved
field and the envelope cannot be part of a parity proof because the envelope
contains a per-delivery message id (`agent_messages.rs:1741-1778`).

## 4. Held constant (must not vary)

| item | value / evidence |
|---|---|
| receiver system prompt | byte-identical across arms; phase suite `:1440 == :1539`; handoff suite builds it from one expression with the same framing suffix (`handoff.rs:430`, `:434`) |
| ordinary task text | `CONTROL_SUMMARY` (`agent_messages.rs:2068`; the daemon connector pins the same string at `occ3_connectors/sglang.rs:849`), appended framing constant `RECEIVER_PREFIX_FRAMING` (`handoff.rs:72-80`), no-state task constant (`handoff.rs:84`) |
| tool list | `occ3_handoff` is pushed onto **every** family member (`occ3_native.rs:73`), so a one-member no-state family has the same one-tool payload |
| sampling and budget | `agent_options` differs only in `system_prompt` (`phase_suite.rs:1295-1309`); `max_tokens == 131072` is asserted for every request (`:1507`, `:1586`, `handoff.rs:221`) |
| request count | exactly one native request per arm (`:1508`, `:1587`) |
| user-turn count | exactly one ordinary user message in every arm (`handoff.rs:466`, gate at `:220`) |
| tool use | receiver 0 calls, source exactly 1 (`:1503-1504`) |
| detector input | every request payload, not just the first (`phase_suite.rs:1341` aggregates `all`) |
| forbidden value set | the private fact and the nonce (`:1320`, `handoff.rs:465`); lower-cased before the check |

## 5. Allowed to differ

* the reserved `occ3` record: `state_handle`, `request_id`, `message_id`,
  `runtime_generation`, `expires_at_epoch` (`attachment_contract.py:81-117`);
* the delivery-record envelope: message id, sender/target descriptors
  (`agent_messages.rs:1741-1778`);
* family shape: member count, `depth`, `parent_session_id`
  (`phase_suite.rs:1378-1379` vs `:1548`) — not model-visible;
* source-side state: lease state, capture commit id, publication id;
* wall-clock timing and `expires_at_epoch`.

## 6. Controls that are NOT matched today

### 6.1 The phase suite's no-state arm reads a different task text

`phase_suite.rs:1564` sends
`"Continue the authorized task. No state attachment is available for this negative control."`
The positive arm's receiver reads the envelope control summary
`"Continue the authorized task using the attached state."`
(`agent_messages.rs:2068`). Measured with the suite's mirror:

| quantity | positive | no-state |
|---|---|---|
| ordinary task text | `Continue the authorized task using the attached state.` | `Continue the authorized task. No state attachment is available for this negative control.` |
| `parity_sha256` | `e60fbfdd8f631c9e882f84e607b9703b06665fad810877d6e2fdbac34dcfde92` | `b1a5166058439cc0d0bca8fabe5e10884dd34dc68cc98df544df384839527004` |
| `payload_sha256` | `12b0459ab16502e25921c24102a5b08af57e120dd412bfaf0f869353c7f21dfb` | `b1a5166058439cc0d0bca8fabe5e10884dd34dc68cc98df544df384839527004` |

The no-state prompt also *announces* the missing state, so the arm differs in
both content and prior. Pinned by the strict xfail
`test_parity_ordinary_payload_is_byte_identical_positive_vs_no_state`.
The newer handoff suite already fixes the text (`handoff.rs:84`) and the framing
(`:72-80`); the phase suite did not.

### 6.2 The phase suite's receiver framing differs between arms

Positive: the receiver is prompted with one `AgentMessage::Custom` delivery
record (`occ3_native.rs:212-214`), rendered as
`[from child:...]` / `Agent-to-agent message received.` /
`Source:` / `From:` / `To:` / `Message id:` / blank line / body
(`agent_messages.rs:1741-1778`).
No-state: `PromptInput::Text` with no envelope (`phase_suite.rs:1561-1567`).
The framing is ordinary text the model reads, and it differs. The handoff suite
keeps the envelope out of the ordinary turn entirely and moves the framing into
the constant system-prompt suffix (`handoff.rs:72-80`, appended at `:430`/`:434`).

### 6.3 The semantic (animal-category) arm's answer is enumerable from the prompt

`handoff.rs:70` lists every candidate answer
(`BIRD, MAMMAL, FISH, or REPTILE`) in the receiver system prompt, and the
in-repo test `handoff.rs:980-985` asserts exactly that
(`assert!(CATEGORY_QUESTION.contains(class))`). A class-only answer is therefore
reachable with no state at all; only the animal→class *binding* is protected.
Pinned by the strict xfail `test_semantic_case_answer_is_not_enumerable_from_the_system_prompt`.
The suite still enforces the strongest sound check: the expected class may occur
exactly once, inside the fixed enumeration, and never in a message that also
carries the animal (`test_expected_answer_class_is_never_bound_to_the_private_fact`).
The arm is also marked `controller_proof_eligible: false` (`handoff.rs:577`), so it
can never be quoted as a controller proof.

### 6.4 Four of the seven matrix arms do not exist

`rg` over `crates/pi-coding-agent/examples` and `scripts` finds no
`unrelated`, `shuffled`, `random-vector` or `token-reference` arm. The only
negative control in the phase suite is the *stale source lease* path
(`phase_suite.rs:1712-1800`), which is source-side and never reaches the receiver.
`handoff.rs` has three cases only: `positive`, `no-state`, `full-text`. So
"unrelated state", "shuffled state", "token-reference-only" and "random vectors"
are defined here but not implemented anywhere.

### 6.5 No arm exercises a *present but unusable* record

The no-state arm sends no `occ3` field at all. That cannot distinguish
"state absent" from "state silently dropped by the adapter, connector or
provider". A present-but-expired or foreign handle (which the backend must
refuse, `chat_adapter.py` `:488` onward, and `use_receipts.py:62-91`) is not
covered by any arm. Without it, a broken attachment path is indistinguishable
from a correct negative control.

### 6.6 The phase suite's leak detector is case- and form-fragile

`phase_suite.rs:1319-1320`:

```rust
let serialized = payload.to_string();
let absent = !serialized.contains(&forbidden);
```

Exact-case substring, one value, one encoding. It misses upper-cased, title-cased,
whitespace-split, zero-width-padded, JSON-escaped, base64 and re-spaced forms, and
it misses the semantic arm's answer entirely (§6.3). The suite here replaces it
with `assert_no_private_fact` (exact / casefold / NFKC / whitespace-collapsed /
whitespace-removed / invisible characters stripped).

### 6.7 The two scorers disagree on answer normalisation

`phase_suite.rs:1473` requires `answer == nonce` (exact, ASCII-trimmed by
`normalized_final_answer`, `:1236-1257`). `handoff.rs:581` requires only
`answer.contains(&nonce)`, and `:579` accepts a trimmed case-insensitive match of
the class. A trial whose answer is `"the code is vault-…"` passes the handoff
suite and fails the phase suite. `test_answers_other_than_the_exact_nonce_are_not_a_success`
pins the strict form; the report-level requirement is one shared normalisation.

### 6.8 The audit hook cannot see the reserved channel

`on_payload` runs before pi-ai inserts `occ3`
(`openai_completions.rs:3036-3041`, insert at `:3070`, equality re-check at
`:2210`). So `source_facts_absent` cannot cover the reserved record — which is
correct today only because the record is a typed attachment and never text.
If insertion moved earlier, the audit would not notice. The receipt/allowlist
tests (`test_native_receipt_header_is_an_exact_allowlist_without_plaintext`,
`test_attachment_wire_allowlist_is_pinned`) cover that channel instead.

### 6.9 `ordinary_user_messages == 1` does not prove framing identity

The gate is satisfied by a plain user turn and by a rendered delivery record
alike (`handoff.rs:220`, `:466`), so it cannot detect the §6.2 mismatch. Parity
must compare hashes, which is what `test_parity_*` does.

## 7. What would close the gaps

1. Replace `phase_suite.rs:1564` with the control summary text and give the
   no-state arm the same delivery-record framing (or remove the envelope from
   both arms). Then delete the strict xfail in `test_parity_...`.
2. Add an "unrelated state" arm: publish a second capture from a different source
   turn and attach *that* handle to the receiver turn.
3. Add a "present but expired / foreign handle" arm, so "refused state" is
   distinguished from "no state".
4. Add "shuffled", "token-reference-only" and "random vectors" arms with the
   identical ordinary text (§3).
5. Replace the substring detector in the phase suite with the multi-form
   `assert_no_private_fact` and apply it to every request.
6. Make the semantic arm's expected answer non-enumerable (random token per
   trial), or keep it strictly diagnostic and never score it as a proof.
7. Unify the answer normalisation between the two suites (§6.7).

## 8. What is real and what is mirrored in the test file

Real `occ3_sglang` code: `attachment_contract` (`InferenceAttachment`,
`parse_opt_in`, `Reservation`, `ConsumeWorkerDescriptor`, `canonical`),
`use_receipts` (`receipt_bytes`, `validate_evidence`, `TerminalUseEvidence`,
`NativeOutputAudit` through the adapter), and the real `ChatAttachmentAdapter`
driven through a local hook harness. The harness reproduces the hash-pinned
`sglang.srt.plugins.hook_registry._wrap_fn` contract (`AFTER`, `AROUND`) and the
pinned streaming kick-start (`serving_chat.py:_handle_streaming_request`); it
makes no installed-SGLang, CUDA or TP claim.

Mirrors (Rust, cited at the definition site): `delivery_record_text`
(`agent_messages.rs:1741-1778`), `control_payload` (`:2076-2085`),
`ordinary_payload` (pi-ai body before the reserved field insertion,
`openai_completions.rs:3036-3070`), `payload_sha256` (`handoff.rs:468`
`body_sha256_before_attachment`), `prepared_handoff_accepts`
(`agent_messages.rs:2064-2070`), `positive_case_passed`
(`phase_suite.rs:1497-1508`), `no_state_case_passed` (`:1578-1587`).

## 9. Reproduce

```
cd C:/Users/openclawuser/optimus-occ3-latent-validation/extensions/sglang-occ3
.venv/Scripts/python.exe -m pytest tests/test_semantic_leakage.py -q
# 52 passed, 2 xfailed in 0.60s   (54 collected, 0 failed)
```

Both xfails are `strict=True`: fixing §6.1 or §6.3 turns them into failures and
forces this file to be updated, so the report can never drift silently.
