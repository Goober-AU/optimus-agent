# Final report — OCC-3 StateBridge semantic recovery

Date: 2026-10-27. Branch `fix/occ3-statebridge-semantic-recovery` from `origin/main`
`7a158b0e559eb5ab38fd3b9ae3e98384dcb3181c` (PR #203 merge).

## Verdict

- NATIVE TRANSPORT: previously verified on PR #203 and preserved.
- RECEIVER INTERPRETATION: **NOT VERIFIED**. This branch removes the strongest untested
  structural obstacle and adds the instrumentation needed to measure interpretation, but no
  supervised DGX window has run against this build.
- HIDDEN-STATE SEMANTIC TRANSFER: **NOT VERIFIED**, and now additionally *questioned*: the
  offline audit shows the aligned prefix is dominated by the reference token embeddings.

## Work completed offline

1. **Placement and framing (RC-1).** The receiver prefix is no longer forced to absolute row 0
   with no text referent. A marker in the rendered prompt selects a mid-sequence layout with a
   natural-language framing sentence, matching the reference implementations; absence of the
   marker keeps the legacy layout byte-identically. The offset travels adapter -> reservation ->
   descriptor -> scheduler reservation -> injection -> terminal metadata, and a mismatch refuses
   the turn. New `occ3_sglang/prefix_layout.py` holds the marker resolution and the generalised
   chunk/draft span arithmetic, which reduces exactly to the previous formula at offset 0.
2. **Predictor/token pairing (RC-5) closed as not-a-defect.** Source-attested proof against
   pinned SGLang `e00930c` that both capture paths pair the state at `p` with the token at
   `p+1`, and that the commit-time position assertion is the correct check.
3. **Span audit (RC-2) instrumented, not wired.** `occ3_sglang/span_audit.py` classifies accepted
   positions by exact token-id markers into thinking / content / tool-call / arguments / control
   / unknown, with a fixed-size, privacy-preserving summary and fail-closed behaviour when no
   markers exist. Wiring it needs a frontend-attested marker id set, which the runtime does not
   publish today.
4. **Alignment audit (RC-4) measured.** Thin solver matches the dense reference on full-rank
   data (4.95e-05) but is an in-sample interpolator: held-out reconstruction error 1.407 vs
   0.019 in-sample; sensitivity to the reference embeddings is 63x its sensitivity to the hidden
   states; replacing the hidden states with unrelated random rows changes the output by 1.8%.
   Anchoring at alpha=0.3 moves the output towards the nearest token rows (cos 0.99976 ->
   0.99988) and does not add a hidden-state channel.
5. **Likelihood path specified (TEST 12).** A first-token forced-choice margin over the four
   candidate classes is available in one authenticated request under pinned SGLang, with exact
   preconditions (temperature in {0,1}, no penalties, no grammar, single-token labels), and with
   the refusals the adapter must add so a scoring request cannot silently degrade.
6. **Leakage and control matrix.** 54 offline cases assert that the private fact, the answer, the
   nonce and the handle never appear in an ordinary payload, and that fallback communication
   cannot pass an OCC-3 success predicate. Two strict xfails record real gaps: the phase-suite
   no-state arm does not share the positive arm's ordinary text, and the semantic answer set is
   enumerable from the prompt (only the animal->class binding is protected).

## Numbers

| gate | result |
|---|---|
| `test_prefix_placement.py` | 15 passed |
| `test_predictor_alignment.py` | 22 passed |
| `test_span_audit.py` | 46 passed |
| `test_semantic_leakage.py` | 52 passed, 2 xfailed |
| `test_chat_attachment.py` (source-attested) | 53 passed |
| focused extension set (14 files) | 556 passed, 4 skipped, 2 xfailed, 18 subtests |
| full extension suite after the conftest isolation fix | 1986 passed, 105 skipped, 2 xfailed, 77 subtests, 0 failed |
| full extension suite before that fix | 1732 passed, 358 skipped, 1 ordering-dependent failure (RC-7) |
| `cargo check` / `scripts/check.sh` | NOT RUN — no Rust toolchain on the authoring host |

The single full-suite failure (`test_only_accepted_verify_rows_enter_the_retained_ring`) passes
alone and as a file, and the same class of session pollution reproduces between two files this
branch does not modify.

## Honest limits

- No live GPU execution in this session: no image build, no swap, no window. The original
  production container was not touched; no descriptor, proof pin or state handle was used.
- The framed layout has never been executed by GLM-5.3. Its effect on interpretation is unknown.
- The alignment audit uses synthetic GLM-shaped data. It proves the estimator's structure, not
  a property of real GLM-5.3 states.
- The Rust example change that emits the framing sentence was not compiled here.

## Next actions, in order

1. Rebuild the candidate image and the diagnostic harness on a machine with the pinned toolchain;
   run the focused suites there.
2. One supervised window, one swap: framed positive / no-state / full-text, then legacy-prepend
   positive / no-state, then token-reference-only. Add the first-token margin to every arm.
3. Only after a semantic hit: span policy change, `alpha=0.3`, unrelated/shuffled-state arms,
   then benchmarks and a two-subagent end-to-end run.

No PR is labelled as semantic success. Nothing is merged.
