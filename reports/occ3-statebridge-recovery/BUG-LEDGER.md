# Bug ledger

| id | defect | evidence | status |
|---|---|---|---|
| RC-1 | Receiver continuous prefix was prepended before the system turn with no natural-language framing, so the model had no referent telling it that rows 0..K-1 were a sender message, where it ended, or how to use it. | `latent_worker.admit_request()` prepended reserved zero-id rows at row 0; `inject_target_embeddings()` overwrote them; nothing in the rendered prompt named the span. References frame it in text inside a turn (`StateBridge prompts.py:53-56`). | FIXED in this branch (marker layout + framing paragraph in the live harness). Offline verified; live effect unverified. |
| RC-2 | Captured span is the tail of the whole completed response (`whole_completed_response_tail_v1`), so the retained rows are dominated by the `occ3_handoff` tool-call JSON and closing markers instead of communicative content. | `attachment_contract.py` admits exactly one span policy; `latent_worker.align()` keeps the last `min(accepted_count, max_prefix_tokens)` rows. Reference drops reasoning at the `</think>` boundary (`state_bridge.py:579-597`). | OPEN. Auditor implemented (`occ3_sglang/span_audit.py`, 46 tests); wiring needs a tokenizer-attested marker id set, which does not exist in the runtime today. |
| RC-3 | Vocabulary anchoring is off in the shipped profile (`anchor_alpha: 0.0`), while the reference uses 0.3 and reports its own ablation. | `deploy/native-build-inputs/native-profile-input.json`; `latent_types.py` default. | OPEN as an experiment variable. No code change; needs its own window. |
| RC-4 | Per-message thin Procrustes reproduces the reference token embeddings almost exactly, and the output depends on the hidden states only weakly. | `analysis/align_audit.json`: holdout reconstruction error 1.407 vs in-sample 0.019 (73x); sensitivity to `E` 1.007 vs to `H` 0.016 (63x); unrelated-random-`H` output changes 1.8% while row cosine to `E` stays 0.9999. | MEASURED, not repaired. No change is justified by these numbers alone; the live token-reference-only control is the decisive test. |
| RC-5 | Predictor/token pairing could be off by one. | Pinned SGLang: prefill and verify both pair the pre-norm state at position `p` with the token predicted for `p+1`; `commit_result()` refuses on any position mismatch. | NOT A DEFECT (source-attested). Regression tests added. |
| RC-6 | The solver omits the reference's `det(R) < 0` proper-rotation guard. | `statebridge.align_hidden_sequence` vs `state_bridge.py:275-277`. | OPEN, low priority. The guard changes only the sign of one unused complement direction in the thin subspace; no evidence it affects the receiver. Do not change without an ablation. |
| RC-7 | `tests/conftest.py::_install_stubs` replaced `sys.modules["torch"]` and the `sglang` namespace by assignment inside a fixture, with no teardown, so every later test in the session ran against a stub `torch`. | Full session: `AttributeError: module 'torch' has no attribute 'arange'` in `test_only_accepted_verify_rows_enter_the_retained_ring`; the same file passes alone; `test_chat_attachment.py` collects 53 errors after `test_native_research_regressions.py`. | FIXED in this branch (save/restore of exactly the overwritten modules). Full suite green: 1986 passed, 105 skipped, 2 xfailed, 0 failed. Not caused by the OCC-3 changes, but it hid their regression signal. |

## Repairs deliberately not attempted

- KV-cache communication, cache fusion, trained projectors, learned adapters, fine-tuning,
  extra checkpoints, a parallel tensor server: excluded by the method constraint.
- Changing ridge, whitening, eigen floor or anchoring on the strength of an `UNKNOWN` answer:
  no evidence.
- Reinterpreting existing state handles under new mathematics: any mathematical change must
  bump the method identifier and the admission checks.
