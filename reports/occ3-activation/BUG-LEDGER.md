# Bug ledger

Defects found while doing this work. Fixed here, left open deliberately, or recorded as pre-existing.

## Fixed

| Id | Defect | Evidence | Fix |
|---|---|---|---|
| B-1 | A native OCC-3 handoff delivery classified as `Text` because its body is a control string, so the TXT light pulsed for an OCC-3 event | `core/occ/activity.rs` classified by body text | receipt-bound `occ3Delivery` marker plus a projection branch; pending markers suppress rather than report text |
| B-2 | The daemon native handoff returned a delivered receipt without ever checking the receiver outcome | `daemon_mode.rs` `send_prepared_native_handoff` | `Consumed` and `Acknowledged` require the completed-use receipt digest; `Uncertain`, `Failed` and `Expired` return an error with no replay |
| B-3 | `CommunicationKind::Latent` was discarded by the indicator, and the pulse array had three slots | `communication_activity.rs` | fourth slot, `Latent` maps to index 3 |
| B-4 | OCC-3 had no production activation path: `enroll`, `install_experimental_native_occ3`, `negotiate_occ3` and the global connector registry were all reached only from tests and examples | `rg` over the tree at baseline | `occ3_connectors/activation.rs` plus a settings opt-in plus an `add_runtime` hook |
| B-5 | `latent_types.RuntimeProfile` hard-pinned the per-message method, so a calibrated profile could not be constructed | W5 GAP3 | profile carries an explicit advertised method id, default unchanged |
| B-6 | Nothing dispatched on `profile.method`; the runtime always ran the per-message aligner | W5 GAP_REMAINING3 | `alignment_dispatch.aligner_for` plus a single resolved aligner at the one call site, refusal instead of fallback |
| B-7 | The calibrated surface was not digest-attested and packaging kept its own method literal | W5 GAP2 | added to `_REQUIRED_PLUGIN`; `native_package` reads the registry |
| B-8 | `fit_calibration`'s Gram PSD guard used tolerance 0 and rejected legitimate `rows > dim` corpora | W5 defect 4 | tolerance `-1e-4 * lambda_max`, commented |
| B-9 | The controller could restore production only because the proof deadline elapsed, so a healthy candidate was discarded after every window | `deploy/v3` | persistence policy plus separated timeout types plus `semantic_experiment` versus `service_health` signals |

## Recorded, deliberately not fixed

| Id | Defect | Why open |
|---|---|---|
| O-1 | `receiver.outcome()` is strict about a bound lease and errors for a queued or admitted delivery | changing its semantics would affect every caller; `delivery_status()` answers in that state and the daemon uses it defensively |
| O-2 | `deploy/v3/native_validator.py` pins its own method literal and exact profile field set, so a calibrated `native_profile.json` carrying the artifact reference is refused there | outside the delegated scope; next step for the calibrated rollout |
| O-3 | At widths 104 to 110 the four-segment indicator needs one more status row than three segments did | the row decision lives in `subagent_summary_line.rs`; the right fix is caller-side ellipsis, not a header restructure |
| O-4 | `deploy/README.md` never mentions `deploy/v3` | documentation-only; the new mode is documented in `deploy/v3/README.md` |
| O-5 | A second attempt against the same original container id is refused by the durable original fence, so a later upgrade must target the persisted candidate id | the fence is intentional; the admissible path is proven by a test |
| O-6 | `align_diagnostics.py` carries an unused `LatentError` import | left as found |

## Pre-existing, outside this change

| Id | Defect | Evidence |
|---|---|---|
| P-1 | 115 extension test failures in `test_v3_native_package.py`, `test_v3_native_image_build.py`, `test_native_install.py` | identical per-file counts against a pristine `git archive HEAD`; Windows filesystem identity races and fastapi router internals |
| P-2 | 70 to 78 repository Python failures, all `test_memory_bench_snapshot_export` | same failing test functions on a pristine baseline; ids embed random temp paths |
| P-3 | 11 parallel-only `modes::interactive` failures from global theme-slot poisoning | 748 passed, 0 failed with `--test-threads=1` |
| P-4 | `test_v3_supervisor.py::test_controller_guard_binds_process_epoch_nonce_and_lease` is timing sensitive (`supervisor_lease_s 0.1`) | flapped once pre-change, passed in both post-change full runs |
