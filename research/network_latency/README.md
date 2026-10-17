# Network and IPC latency research (campaign 2026-10-17)

Baseline: telemusai/optimus-agent v0.1.34 @ 6423654c12dc2be814f370662bd2a31b76bd1562.

Scope: PATH A provider communication (Optimus -> local model gateway -> remote
provider) and PATH B agent IPC (worker -> daemon/supervisor -> recipient worker).

Layout:
- audits/    transport topology audits and evidence
- ipc/       local IPC benchmarks and scorecards (results/ for raw JSON)
- provider/  provider transport benchmarks and scorecards (results/ for raw JSON)
- EXPERIMENT-LEDGER.md  machine-readable experiment ledger

Rules: every claim links to real measured output; negative results are kept;
OCC encoding is held fixed (v0.1.34 default) while transport is benchmarked.
