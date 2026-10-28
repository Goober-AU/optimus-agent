# OCC-3 activation

Status: experimental integration complete, semantic validation in progress.

This directory records the work that moves OCC-3 from a research-only implementation into an explicitly
enabled experimental Optimus capability, and the measurement work that decides whether it may ever be
promoted past experimental status.

## What was already true at the baseline

Baseline: merged PR #205, main `0bfd1035c9012288d98fa83ee539f49550c1ed8c`.

The bridge, the SGLang connector, the native session, the admission inbox, the receipt machinery and the
activity projection all existed and were tested. What did not exist was any production path that used them:

| Seam | State at baseline | Evidence |
|---|---|---|
| `Occ3NativeSession::enroll` | called only from examples and tests | `rg -n "Occ3NativeSession::enroll"` |
| `AgentSession::install_experimental_native_occ3` | called only from tests | `rg -n install_experimental_native_occ3` |
| `negotiate_occ3` | called only from its own tests | `rg -n negotiate_occ3` |
| process-wide connector registry | never populated | `rg -n with_global_registry` outside its own module |
| `CommunicationKind::Latent` | never produced | `core/occ/activity.rs` classified by body text only |

So "enable OCC-3" was integration work, not a flag flip, and the OCC3 indicator had nothing to display.

## What changed

1. `OCC3-INDICATOR.md` - the fourth indicator segment, in the existing neon style.
2. `NATIVE-DELIVERY-EVENTS.md` - a receipt-backed event path from completed native consumption to that segment.
3. `CAPABILITY-NEGOTIATION.md` and `MODEL-COMPATIBILITY.md` - the activation gates and the exact-model rule.
4. `PERSISTENT-DGX-DEPLOYMENT.md` - the supervised development mode that keeps a healthy candidate running.
5. `STATEBRIDGE-CALIBRATION.md` and `CALIBRATION-ARTIFACT.md` - the frozen independent-calibration method.
6. `SEMANTIC-VALIDATION.md`, `NEGATIVE-CONTROLS.md`, `HELLO-SMOKE-TEST.md` - the measurement position.
7. `BENCHMARK-METHODOLOGY.md`, `BENCHMARK-RESULTS.md` - the comparison contract and what has been measured.
8. `REGRESSION-GATES.md`, `BUG-LEDGER.md`, `FINAL-REPORT.md` - the gates, the defects found, the summary.

## What this PR does not claim

It does not claim semantic reliability. A model may support the native mechanism without having
demonstrated reliable transfer of sender-specific meaning, and at the baseline the live measurement was
INCONCLUSIVE with 0 of 3 diagnostic semantic trials recovered. OCC-3 therefore stays opt-in twice over:
`agentCommunication.occ3Enabled` and `agentCommunication.occ3Experimental` must both be true, the model must
carry a `models.json` `occ3` block with `validation: live-verified`, and the runtime must pass a live
readiness handshake.
