# Optimus Agent v0.1.39

Prepared from Optimus main `71ae54aef82d02d01f06eebc4e68e67dd7afd035` in the isolated `release/v0.1.39` worktree. This is Optimus's independent Rust implementation. Prime's changes were reviewed as behavioral reports, not imported patches.

## Status fixes

- Linux CPU: sysinfo 0.32.1 did not calculate process CPU for the selected-PID refresh path on Linux. The same probe stayed at zero on 0.32.1 and reported approximately one busy core on 0.33.1. Upgrade to 0.33.1 preserves targeted sampling and process-identity checks. A busy-process regression checks nonzero CPU without relying on the installed daemon or changing a real session.
- STREAM TPS: the headline now adds recent rates only for the selected session and its currently streaming descendants. A two-second window uses fresh counter deltas and the larger of receipt time and producer streaming time; unchanged counters decay and become zero after three seconds. Attach-time history, buffered first chunks, completed agents and final usage corrections do not count. A new stream without enough measurement displays an em dash. Positive rates retain the estimate marker. Per-agent rows continue to show their own historical averages.
- Heartbeat: an enabled user or agent heartbeat has a red circle before HB. A newly observed run flashes for 1.4 seconds. Attach history and duplicate or older snapshots do not restart the flash; paused or removed heartbeats have no active circle.
- TXT, OCC1 and OCC2 retain their activity behavior with the light before each label.

## Branding and packaging

Telegram help, notifications, authorization guidance and worker messages now say Optimus. Command guidance, update notices, feature hints, missing-tool guidance and telemetry disclosure were also corrected. `APP_COMMAND` separates the displayed command from internal runtime identifiers. Third-party Prime Inference and Prime Agent Traces names still identify those actual services. This is not a migration of persisted configuration, environment-variable names, protocol namespaces or the Python distribution name.

The workspace, resource manifest and Python runtime versions are all 0.1.39. The runtime now declares `dill` as an installation dependency, correcting the confirmed packaging gap reported upstream as #3479. Its existing locked version is retained.

Tokio is raised from 1.53.1 to the patched 1.53.3 for [RUSTSEC-2026-0386](https://rustsec.org/advisories/RUSTSEC-2026-0386.html). This is a security exception to the routine seven-day dependency age rule. No direct application use of the affected AsyncFd constructors was found; this update does not claim a reproduced Optimus exploit. Unlike Prime's broader update, it stays on the existing Tokio minor release. The sysinfo release is well beyond the routine age threshold.

## Validation and release boundary

Validation uses an isolated profile, synthetic credentials and offline fixtures. Detailed command results are kept in `/home/anthony/optimus-v039-validation/`. The native portable archive is staged only after the required checks and focused tests pass. Staging validates the release binary's source and payload provenance.

Recorded checks:

- Final `bash scripts/check.sh`: passed workspace/all-target checking, 282 Python packaging tests, whitespace and native resource layout checks.
- Changed Rust paths: 207 passed. Heartbeat, auth, clipboard and session-service checks: 51 passed; one isolated-process fixture is ignored in the parent run and invoked successfully by its wrapper.
- Initial complete interactive suite: 729 passed, one ignored. Final focused checks cover the subsequently adjusted heartbeat replay handling and dependency update.
- Python runtime: 68 shell/journal tests and two agent-message tests passed. Built wheel metadata identifies 0.1.39 and includes `dill`; offline lock validation passes.
- Upstream regression comparisons: 144 provider/input/event/length-stop tests and one strict-tool omission test passed. The repeated-character input probe also passed for raw and bracketed input at every byte split.
- Read-only sampling of the already running worker PID 1473364 returned 1.0038% CPU. Separate busy-process regression verifies nonzero CPU deterministically. Captured classic and neon frame text verifies the reordered lights and idle STREAM TPS zero.

The compiler still reports pre-existing unused/dead-code warnings across the workspace. No new warning was identified in the edited paths.

The initial optimized Linux candidate and portable staging passed, including version/help, source/payload provenance and a read-only list of 32 sessions through the existing v0.1.38 daemon. The final release also includes the approved Jev wording change and is rebuilt, checked and staged from the committed release source. Final build, archive, installation and resume verification receipts are retained in `/home/anthony/optimus-v039-final-release/` and `/home/anthony/optimus-v039-validation/`.

Release publication and local installation were authorized after review. GitHub Actions remains disabled, matching the v0.1.38 release process. The published binary bundle and local runtime validation are Linux x86_64; Windows and macOS users build from the tagged source, and interactive runtime validation requires their respective hosts.

## Surface and protocol status

The status presentation is shared Optimus terminal code for Linux, Windows and macOS; the CPU adapter is supplied by sysinfo. Telegram branding is shared across its supported desktop hosts. There is no daemon command, capability or payload-shape change. Older daemons without throughput metadata continue to show the unavailable indicator.

3D/Web/TUI parity: Optimus's TUI is affected. Telemus's 3D and Web dashboard surfaces in the separate vr-ai-chat repository are unaffected; no native renderer or platform abstraction was changed.

Jev now displays “standard mode” when ordinary non-Jev behavior takes over. Status, help and statistics use that terminology and retain the amber warning colour. Internal state names, stored telemetry fields and diagnostic reason codes are unchanged.
