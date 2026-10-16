# PR-BODY-DRAFT.md - improve/occ2-efficiency

Draft PR description (text only). Title suggestion:
`OCC-2: adaptive compact agent communication with amortized codebooks`

## Summary

Agent-to-agent messages cost the same regardless of content today: every
send encodes the full OCC-1 body and every receive pays for it. This PR
adds OCC-2, a second-generation compact communication path that carries
agent messages as symbolized wire bodies backed by amortized per-dialect
codebooks, and selects between OCC-1 and OCC-2 per send with a
conservative, economics-driven policy. On the 60-item dev live eval, OCC-2
cut end-to-end wire cost to 88.57 tokens per item versus 144.17 for OCC-1
(0.6143x, -38.6%), and on the sealed 54-item holdout split, 87.52 versus
144.30 (0.6065x, -39.4%) with identical perfect gates. On the dev split the speaker emitted
60/60 codebook-conformant wires, strict-decode fidelity was clean, and all
evaluation gates (quality assurance, comprehension, canaries) stayed
green. OCC-2 ships default OFF; nothing changes for any session that
does not opt in.

## Key numbers

| Metric | Value |
|---|---|
| Wire tokens per item (60-item dev live eval) | 88.57 vs 144.17 OCC-1 (0.6143x, -38.6%) |
| Holdout speaker strict decode | 54/54 clean |
| Holdout listener QA / comprehension | 162/162, 54/54 exact-rebuild |
| Holdout wire tokens | 87.52 vs 144.30 tok/item (0.6065x, -39.4%) |
| Codebook amortization | -40% warm prompt tokens per codebook injection |
| Codebook break-even | ~21.5 items per injection |
| Live eval gates | speaker 60/60, QA 180/180, comprehension 60/60, canaries 12/12 (no drift) |

## What changed, by area

1. Codebook amortization (`agent_messages.rs`): OCC-2 bodies reference a
   per-dialect codebook injected once into the conversation instead of
   carrying full vocabularies per message. The codebook identity is
   pinned (`dialect@version` + sha prefix) so both sides detect the same
   legend, presence is derived from live messages (compaction- and
   restart-safe), and warm injections cost -40% prompt tokens. Break-even
   is ~21.5 items per injection, after which every additional message is
   net savings.
2. Dialect v2 + card clarification (`occ/dialect_load.rs`,
   `occ/selection.rs`): the sym-v2-d2-r1 dialect (compact key/value wire
   with symbol families) joins sym-v1 in the promoted registry. The
   rendered codebook card was hardened after live eval found three
   ambiguities (canonical line order, never-compress scope, link format)
   and one factual error (epoch-36 timestamps are recoded even on
   never-compress kinds); wire format unchanged, 18/18 promotion tests
   including the new regression pins.
3. Selection wiring + strike ledger (`agent_messages.rs`,
   `subagent_runs.rs`): every send seam now resolves through one shared
   selection function. The strike ledger persists per-pair decode
   failures with 24 h decay; an eval store carries offline dialect
   evaluations. Production selects OCC-2 only for dialects with
   validated economics; `/occ 2` additionally allows unevaluated
   dialects for development.
4. Daemon /occ lane (`daemon_mode.rs`, `daemon_protocol.rs`,
   `occ_control.rs`, `session_settings.rs`): the daemon owns OCC
   preferences across chats and restarts, `/occ` controls the opt-in,
   and daemon-bridged sends use the same selection path as in-process
   sends. Fixes a real bug found by the new contract tests: OCC
   commands were registered in the worker but missing from the native
   supervisor's `DAEMON_COMMAND_TYPES` allowlist, so real clients got
   "Unknown daemon command" before reaching the worker.
5. Telemetry (`occ/activity.rs`, `agent_messages.rs`): delivery
   classification is recorded once at delivery time
   (`details.occ2Delivery`) and read back by the communication
   activity projection, removing the double decode; legacy records
   without the field fall back to body classification.

## Safety

- OCC-2 is default OFF. Sessions stay OCC-1 until `/occ` opts in.
- Selection is conservative by default: production refuses unevaluated
  dialects; only validated records (>= 30 samples, exact-round-trip
  fidelity, token ratio < 1.0) select OCC-2. Strikes and the
  small-message floor block regardless.
- The evaluation store is fail-open: a missing or unreadable store
  degrades to "unevaluated" (OCC-1), never errors a send.
- The dual-payload fallback is preserved: every OCC-2 delivery carries
  the OCC-1 body alongside the symbolic wire, and a decode failure
  consumes the OCC-1 fallback while recording a strike.

## Test evidence

Full quiesced-tree gate table: `reports/occ2opt/COMBINED-GATES.md`; raw
gate outputs: `reports/occ2opt/gates/combined_*.log`. Headlines: 6/6
integration binaries (76/0: occ_comm_fidelity 7, occ2_comm_fidelity 30,
occ2_dialect_promotion 18, occ2_selection_policy 9, occ2_selection_wiring
7, occ2_daemon_compat 5); lib filters occ:: 251/0, communication_ 16/0,
agent_messages 27/0, session 531/1 (the 1 is a pre-existing
flaky-under-load lease test in an untouched file, passes solo);
python `test_occ1_codec.py` 44/44; build passes with +26 warnings over
the 351 baseline (+5 attributed to campaign files).

## Limitations

- None on the evaluation side: the sealed holdout split is complete
  (54/54 speaker strict, 54/54 listener QA+comprehension, gates GREEN).
- Heterogeneous model pairs are deferred: this campaign measured
  homogeneous DGX GLM 5.3 pairs; cross-model OCC-2 negotiation is
  implemented but unmeasured.
- Clippy was not run (component absent in the offline toolchain; CI has
  no clippy gate - `cargo check --locked --workspace --all-targets` is
  the check job, and it is covered by the passing build gate).
