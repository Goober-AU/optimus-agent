# FINAL-REPORT

OPTIMUS AGENT - OCC-3 ACTIVATION REPORT

Repository: https://github.com/telemusai/optimus-agent
Baseline: PR #205 (merged, main `0bfd1035c9012288d98fa83ee539f49550c1ed8c`)
Final branch: `feat/occ3-experimental-activation`
Final commit: see the PR head commit
PR: opened against `main`, not merged, awaiting Telemus review

## Status table

| Item | Status |
|---|---|
| OCC3 LIGHT | IMPLEMENTED |
| OCC3 CAPABILITY NEGOTIATION | PASS (offline, 7 gate tests) |
| EXPERIMENTAL OCC3 ENABLEMENT | PASS (offline, settings plus activation path) |
| REAL NATIVE SOURCE CAPTURE | NOT RUN (no live window) |
| REAL RECEIVER CONSUMPTION | NOT RUN (no live window) |
| REAL OCC3 ACTIVITY EVENT | PASS offline end to end against a receipt fixture; NOT RUN live |
| PERSISTENT DGX DEPLOYMENT | CONFIGURED (mode implemented and tested offline); NOT ACTIVE |
| ORIGINAL CONTAINER PRESERVED | YES - production `neo-glm53` was never stopped or replaced |
| STATEBRIDGE CALIBRATION | calibrated dual-ridge v1 registered, integrity-bound, dispatchable; live measurement outstanding |
| TURN-DISJOINT HELDOUT R2 | not measured on the real model; 0.833 on a synthetic informative fixture only |
| SEMANTIC TRANSFER | NOT VERIFIED |
| HELLO SMOKE TEST | NOT RUN |
| FRESH PRIVATE FACT TEST | NOT RUN |
| NEGATIVE CONTROLS | library implemented and separated on a synthetic fixture; NOT RUN live |
| BENCHMARKS | not run |
| REGRESSION TESTS | see below |
| PR STATUS | OPEN, do not merge |
| NEXT ACTION | one authorised live window |

## What was built

1. **OCC3 indicator.** A fourth segment in the existing neon indicator, fed by
   `CommunicationKind::Latent`. Reuses the existing pulse state machine, family scoping, duplicate
   suppression, epoch reset and reconnect baseline. 17 tests.
2. **Receipt-backed activity events.** A content-free `occ3Delivery` marker, stamped `pending` before
   the receiver is prompted and rewritten in place to `latent_native` only when the completed-use
   receipt exists. Inconsistent markers project nothing rather than a guess. 7 integration tests,
   one of which drives the real connector against a loopback fixture that returns
   `x-optimus-occ3-receipt` only on a verified path.
3. **Experimental activation.** `core/occ3_connectors/activation.rs`: 12 named decline codes covering
   policy, configuration, protocol, same-model rule, evidence state, endpoint shape, live readiness,
   advertised method and verified identity. A live handshake with a 5 s bound, cached per endpoint
   and model. Settings `agentCommunication.occ3Enabled` and `occ3Experimental`, both required. 10
   tests across two modules.
4. **Persistent DGX deployment.** `PERSIST_CANDIDATE_ON_SUCCESS` alongside the existing rollback
   policy, separate timeout types for source capture, receiver inference, proof deadline,
   maintenance window and health, and a `semantic_experiment` versus `service_health` signal split so
   a wrong answer can never restart a healthy model. 20 tests.
5. **Calibrated method.** Registered as `statebridge.glm53.final-prenorm-predictor.calibrated-dual-ridge.v1`
   with its own identity, artifact integrity binding, turn-disjoint evaluation harness, negative
   control library, and real dispatch at the single alignment call site. A calibrated refusal never
   degrades to per-message output.

## Regression results

| Gate | Result |
|---|---|
| `git diff --check` | clean |
| `cargo check --locked --workspace --all-targets` | no errors |
| `cargo test -p pi-coding-agent occ3` | 72 passed, 0 failed, 2 ignored |
| `cargo test -p pi-coding-agent --lib communication` | 26 passed, 0 failed |
| `cargo test -p pi-coding-agent --test occ3_latent_activity` | 7 passed, 0 failed |
| `modes::interactive` serial | 748 passed, 0 failed, 1 ignored |
| extension suite | 115 failed / 1808 passed; pristine baseline 116 failed / 1719 passed, same per-file attribution |
| repository python suite | 76 failed / 293 run; pristine baseline 70 failed, same failing test functions |

All failures above are pre-existing and reproduced on a pristine `git archive HEAD`. Details in
`REGRESSION-GATES.md` and `BUG-LEDGER.md`.

One test assertion was intentionally replaced:
`native_send_acknowledges_a_queued_admission_without_requiring_consumption` asserted that the daemon
never verifies the `Consumed` outcome, which is the behaviour the campaign forbids. It now also
asserts that a queued admission projects no latent completion.

## Known limitations

| Id | Limitation |
|---|---|
| L-1 | The indicator needs one more status row at widths 104 to 110; the row decision lives in `subagent_summary_line.rs` |
| L-2 | The activity marker is live agent state only; the persisted session entry is not rewritten, so after a reload a record reads `pending`. This is why no stale event can be resurrected |
| L-3 | The activation path is wired at `add_runtime`. Interactive-mode activation and the interactive host's own lifecycle were not exercised |
| L-4 | `deploy/v3/native_validator.py` still pins its own method literal, so a calibrated `native_profile.json` carrying the artifact reference is refused there |
| L-5 | The calibrated device move is lazy and has no CUDA coverage here; only CPU tensors were exercised |
| L-6 | No live DGX window, no image rebuild, no attestation run. Production was not touched |
| L-7 | The calibrated branch has never run against a real GLM-5.3 capture |
| L-8 | Model identity is verified to download-metadata depth, not byte depth, as recorded in `reports/occ3/MODEL-COMPATIBILITY.md` |

## What is explicitly not claimed

- No semantic success. The previous window's INCONCLUSIVE verdict and 0-of-3 recovery stand.
- No promotion to trusted production communication.
- No OCC-3 performance or cost numbers.
- No live deployment of the persistence mode.

## Next action

One authorised live window, in this order: rebuild and attest a candidate image containing the
calibrated method; deploy it with `persist_candidate_on_success`; verify startup, ordinary inference,
authentication, native readiness, TP8 ranks and runtime identity; run TEST 1 to TEST 24 in order with
framed-versus-legacy layout before any alignment comparison; collect pooled calibration rows at
M = 512, 1024 and 2048; report turn-disjoint held-out R2 and copier cosine; then run the matched
negative controls and the balanced private-fact task. Leave the healthy candidate running, and keep
the restore path ready.
