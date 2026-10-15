# OCC: optional compact agent communication + RLM depth-limit degradation

Branch `improve/rlm-multi-agent-comm` (10 commits, base `159ce68cc` = v0.1.30 main). 22 files changed, 12,023 insertions(+), 3 deletions(-). No dependency changes.

## Summary

This PR adds an optional, offline-validated communication layer for multi-agent runs: the OCC (Optimus Compact Communication) modules under `crates/pi-coding-agent/src/core/occ/` — the OCC-1 compact structured English codec, the `optimus.agent-comm.v1` typed task/result contract, delta-based messaging, MOC-inspired evidence consolidation and AgentPrune-inspired redundant-message pruning — plus a graceful-degradation change to the RLM recursion depth limit (an RLM at max depth is told to answer directly instead of failing to delegate). Everything is pure, deterministic, offline-testable logic: the daemon transport stays authoritative, sender identity, family scoping, reach restrictions and the plain-English default are unchanged, and no model API was called for any number in this PR. All claims below come from the offline suites in this branch.

## Motivation

- Multi-agent traffic pays a token tax on every hop: plain-English progress/result messages re-send known context, and ad-hoc formats are neither validatable nor corruption-detectable.
- Research analysis (in-repo: `docs/research/agent-communication-landscape.md`) shows the recurring design across the reference systems is a typed message-passing contract behind a compact encoding, with corruption rejected rather than guessed. No public reference provides an offline-verifiable accuracy claim, so this PR targets transport properties: round-trip fidelity, size, latency, and corruption safety.
- The RLM reference (alexzhang13/rlm, MIT) treats maximum recursion depth as degradation, not failure: the deepest agent answers directly from its context. The existing behavior returned a bare error, wasting a turn at the depth boundary.

## Research references

Eight references were cloned at exact SHAs and analyzed offline; full per-reference analysis in `docs/research/agent-communication-landscape.md`.

| Reference | SHA | License | Decision |
|---|---|---|---|
| alexzhang13/rlm | `d04208afbad29ca675ab13478c40ee8bebc84bfe` | MIT | Adopt (techniques): depth-limit degradation ported in this PR; budgets/batching documented as next steps |
| yao-guan/MOC (Guan et al., PMLR v306 guan26i) | `9c67c92507570704a7df73e452552a3f49e83897` | no license file | Experiment: ISM merge-loop skeleton only; the LLM/embedding merge core is replaced by a deterministic structural merge |
| Gen-Verse/LatentMAS (Zou et al.) | `9a9e4d331eb11430bd9e64754c6b252b06d73031` | Apache-2.0 | Reject for this transport: requires same-model KV-cache/hidden-state access, impossible over hosted APIs and text transport |
| yanweiyue/AgentPrune (ICLR'25) | `c544dd6a1858c02c6d5d371d23c6e6ff55e0be21` | no license file | Experiment (offline variant): never-prune exemption list and suppression skeleton only; no gradient training |
| ulab-uiuc/MARBLE | `8d60fa17b5596b44458a52d4296061b9fc13d6f2` | MIT | Adopt (metrics ideas only): milestone/KPI decomposition informed the fidelity suite's assertions |
| jwyjohn/acl26-silo-bench | `e74127782ed1c42fff474249961f022c063d76f2` | Unlicense (public domain) | Adopt (offline harness ideas): round-engine shape and S/P/C/D metrics informed the scenario corpus |
| multi-agent-systems-failure-taxonomy/MAST | `a70542e541b2104ef8fcd785778179e173fb8d70` | no license file | Adopt (test checklist): failure modes grounded the fault-injection matrix |
| pzqpzq/LSF_MDia | n/a | n/a | Repo does not exist (GitHub 404; no name-variant matches). Fallback: CLSR paper (Pei et al., "When LLMs Develop Languages: Symbolic Communication for Efficient Multi-Agent Reasoning", PMLR v306 pei26a), used as design vocabulary only |

License handling: rlm, MARBLE and silo-bench are permissive (small algorithmic adaptations with attribution). LatentMAS is Apache-2.0 (no code copied). MOC, AgentPrune and MAST ship no license file and were treated as all-rights-reserved: ideas only, everything here is a re-implementation from the papers' descriptions, with citation.

## Code changes

All new OCC code is under `crates/pi-coding-agent/src/core/occ/`; the daemon transport is untouched.

- `crates/pi-coding-agent/src/core/mod.rs` — registers the new `occ` module.
- `crates/pi-coding-agent/src/core/occ/mod.rs` (new, 270 lines) — OCC definition and level model: OCC-0 plain English (default, unchanged), OCC-1 compact structured English (implemented), OCC-2 learned symbolic dialects and OCC-3 latent communication (reserved, not implemented). States the campaign rules: pure deterministic offline-testable logic, no network/model APIs/filesystem, never silently drop contradictory evidence or uncertainty, reject instead of inventing values, ordered collections only. Carries the frozen API specs for the worker modules.
- `crates/pi-coding-agent/src/core/occ/occ1.rs` (new, 537 lines) — the OCC-1 line grammar codec: first line exactly `OCC/1`, `KEY=VALUE` lines with `[A-Z][A-Z0-9_]*` keys, order-preserving repeated keys, UTF-8 values without newlines or C0/C1 control characters, CRLF tolerated, enforced bounds (128 fields, 32-byte keys, 4096-byte values, 64 KiB per message), and `Occ1Dialect` required-field validation per message kind.
- `crates/pi-coding-agent/src/core/occ/contract.rs` (new, 792 lines) — the `optimus.agent-comm.v1` typed contract: `AgentCommMessage` with 9 kinds (TASK, PROGRESS, RESULT, EVIDENCE, REQUEST_DETAIL, ACK, CORRECTION, BLOCKED, CANCELLED), `Finding` (statement, confidence, evidence references), `EvidenceRef` (kind, locator, line range, digest, revision, verified), `Verification`, `DeliveryReceipt`, and first-class `next_actions`; `validate()`, `to_occ1()`/`from_occ1()`. Security model: sender identity is established by the daemon, never trusted from message text; parsers never invent missing required values.
- `crates/pi-coding-agent/src/core/occ/delta.rs` (new, 1122 lines) — delta-based messaging: `Delta`, `FieldUpdate`, `DeltaTracker`. Sequences change-only updates per task (contiguous from `base_sequence + 1`), buffers out-of-order deltas (cap 1024), treats replays as idempotent duplicates, rejects wrong-task deltas, distinguishes stale bases from recoverable gaps, exposes exactly-once receipts via `drain_ready`, and recovers exactly across restarts through serializable `state()`/`restore()`. OCC-1 wire encoding is strict: positional finding/evidence ids, canonical update order, self-checking evidence reference values, `EADD=E<n>` for evidence introduced by the same delta.
- `crates/pi-coding-agent/src/core/occ/consolidation.rs` (new, 405 lines) — MOC-inspired evidence consolidation, deterministic: normalized-statement merging gated on verification-status agreement, explicit `Conflict` records preserving every position verbatim when agents disagree, uncertainty extraction (low confidence, unknown verification), evidence dedup by locator/digest/revision key modes, first-appearance ordering, inputs never mutated.
- `crates/pi-coding-agent/src/core/occ/prune.rs` (new, 322 lines) — AgentPrune-inspired redundant-message suppression: safety rules run before any heuristic and bias toward delivery. Never suppresses BLOCKED/CANCELLED/CORRECTION/ACK kinds, FAILED status, verification failures, contradictory findings, messages carrying unseen dependencies, or a task's first message; duplicate detection is per task over stable sha256 digests; bounded history, so eviction re-delivers rather than silently dropping.
- `crates/pi-coding-agent/src/core/occ/codec_tests.rs` (new, 103 tests) — adversarial codec suite: grammar conformance, bounds, pinned error variants, dialect/encoder alignment, index-gap rejection, JSON round-trips, property tests over generated messages.
- `crates/pi-coding-agent/src/core/occ/delta_tests.rs` (23 tests), `consolidation_tests.rs` (18 tests), `prune_tests.rs` (21 tests), plus inline tests in `contract.rs` (7) and `occ1.rs` (6) — 178 tests total in `core::occ`.
- `crates/pi-coding-agent/tests/fixtures/occ/scenarios.json` (new) — 28 deterministic scenarios, 7 holdout (rule: id divisible by 4, asserted by the suite), covering all 9 kinds and 7 statuses, including conflicting reports, corrections, negative constraints, digests/line ranges/revisions, partial status, unknowns, security-relevant identities and sequence gaps.
- `crates/pi-coding-agent/tests/occ_comm_fidelity.rs` (new, 7 tests t01-t07) — offline communication-fidelity suite: fixture shape and holdout rule, exact round-trip equality, encoding-size comparison (plain vs concise English vs JSON vs OCC-1), fault injection, semantic faults, latency budget, aggregate report.
- RLM depth-limit degradation: `crates/pi-coding-agent/src/core/rlm_max_depth.rs` (new `rlm_depth_limit_reached_error` — machine-readable prefix byte-identical to the old error, plus degradation guidance), `crates/pi-coding-agent/src/core/agent_session/subagent_runs.rs` (spawn guard now calls it), `crates/pi-coding-agent/src/core/prompts/rlm.rs` (max-depth prompts state the deepest-agent duty when `allow_recursion` is false).
- Documentation: `docs/research/agent-communication-landscape.md` (720 lines), `reports/occ-campaign/EXPERIMENT-LEDGER.md`, `reports/occ-campaign/BUG-LEDGER.md`, `reports/occ-campaign/FINAL-REPORT.md`, `.changes/occ-compact-communication.md`, `.changes/rlm-depth-degradation.md`.

## Bugs fixed

Both were found by the new test suites in this PR, not by inspection first. Details in `reports/occ-campaign/BUG-LEDGER.md`.

- BUG-001 — index-gap silent truncation (high severity, fixed in `cc1ec0870`). `AgentCommMessage::from_occ1` accepted a message with `F1` and `F3` but no `F2`: `indexed_count` counted contiguous presence, stopped at the first missing index, and silently dropped `F3`, decoding a corrupted message as a shorter valid one. Fix: `indexed_count` now validates that every index up to the highest present exists and returns `Result<usize, ContractError>`; all four call sites (F/E/ART/DEP families) propagate. Corrupted compact messages are now rejected safely, never partially accepted. Regression tests: `index_gaps_are_rejected_not_silently_truncated`, adversarial gap cases, and fidelity t05.
- BUG-002 — dialect/encoder misalignment (fixed in `cc1ec0870`). The standard `Occ1Dialect` required-field table demanded GOAL/REF/WANT/WHY fields that the contract encoder never emits, so contract-encoded messages failed standard dialect validation. Fix: the standard table now mirrors exactly what the encoder emits for every kind (`TASK`, `KIND`, `STATUS`, plus `E1` for evidence-bearing messages), and the contract additionally requires EVIDENCE-kind messages to carry at least one evidence reference. Regression tests: `every_contract_encoded_kind_passes_the_standard_dialect`, `evidence_kind_without_evidence_is_rejected`, dialect table tests.
- Related correction in the same commit: `FieldUpdate::AddEvidence` gained its `EADD=E<n>` wire form, closing the last delta variant that could not round-trip through OCC-1; references to evidence the delta does not introduce stay rejected.

## Benchmark baseline

Recorded at `159ce68cc` (clean main) on the campaign host before any campaign change; ledger in `reports/occ-campaign/EXPERIMENT-LEDGER.md` (BASE-000).

| Check | Result |
|---|---|
| `cargo check --locked --workspace --all-targets` | PASS (warnings only) |
| `python -m unittest discover -s tests -p 'test_*.py'` | 282 tests, OK (10 skipped) |
| `uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test` | 465 tests: 8 failures, 17 errors, 30 skipped (pre-existing, Windows env: bash guard path handling, MCP stdio reaping, REPL interrupt timing) |
| `cargo test --locked --workspace --lib` (no env var) | 3252 passed, 22 failed, 7 ignored (all 22 = missing `KERNEL_CONTAINMENT_TEST_PYTHON`) |
| same, `KERNEL_CONTAINMENT_TEST_PYTHON` set (clean worktree) | 3266 passed, 8 failed, 7 ignored |

The 8 remaining failures are pre-existing Windows-only test-harness/environment defects (legacy directory-lock reclaim, Git Bash PATH order assertion, missing `.port-env/tmp` fixture dir, ipython bootstrap restore, extensions session-shutdown env, admission-waiter snapshot, nested kernel_job, shell bin-dir prepend). None touch OCC modules or files modified here; they are recorded as the clean-main Windows baseline (BASELINE-001). Platform blockers: `scripts/test-native-subagents.mjs` and `scripts/test-subagent-modes.py` assert Linux `/proc` and cannot run on this host; recorded, not run.

## Benchmark improvement

All numbers are offline and deterministic, measured by the fidelity suite over the 28-scenario corpus (train and holdout reported separately in the suite output; both splits show the same direction). Not claimed: real tokenizer counts (proxy tokens are a whitespace-plus-punctuation approximation), agent comprehension of OCC-1, and official RLM/SILO/MARBLE scores — those require an online phase that was out of scope.

| Metric | Denominator | Result |
|---|---|---|
| Round-trip fidelity | 28 scenarios (7 holdout) | 28/28 exact `PartialEq` equality through validate -> to_occ1 -> encode -> decode -> from_occ1 |
| Wire corruption | 224 fixed corruptions (14 scenarios x 16) | 128 rejected with pinned variants; 96 grammatical-but-changed; 0 decoded equal to the original; 0 panics |
| Semantic faults | 25 header-valid but semantically broken messages | 25/25 rejected with `ContractError` |
| Encoding size vs plain English | 28 scenarios | -47.7% bytes, -40.6% proxy tokens; `occ1 <= plain` in 28/28 |
| Encoding size vs JSON | 28 scenarios | -36.0% bytes, -29.6% proxy tokens; `occ1 <= json` in 28/28 (suite threshold: 80%) |
| Codec latency | 100 iterations/scenario | encode mean 24.8 us, decode mean 40.5 us (suite budget 5 ms/op) |

Aggregate corpus totals: plain English 28,022 bytes / 5,171 proxy tokens; concise English 13,665 / 2,710; JSON 22,904 / 4,357; OCC-1 14,659 / 3,069. Concise English is smaller but untyped: it is not machine-validatable, cannot express evidence references or required next actions structurally, and corruption is not detectable. OCC-1 trades roughly 7% bytes versus concise English for a typed, validatable, corruption-detecting contract.

## Accuracy impact

- Round-trip exactness: 28/28 scenarios (train and holdout alike) decode back to the exact typed message (asserted with `PartialEq`), with explicit checks that evidence references, next actions and uncertainty survive the round trip.
- Conflict and uncertainty preservation: the consolidation tests pin that same-statement disagreements become explicit `Conflict` records with every position verbatim (never merged away), and that low-confidence/unknown-verification findings are carried into `uncertainties` as well as preserved in the findings.
- The suites assert transport properties, not task accuracy: no model-in-the-loop claim is made anywhere in this PR.

## Token savings

- OCC-1 versus plain English: -47.7% bytes, -40.6% proxy tokens.
- OCC-1 versus JSON: -36.0% bytes, -29.6% proxy tokens.
- Per-scenario rule: OCC-1 is never larger than the plain-English rendering (28/28) and never larger than JSON in 28/28 scenarios here (the suite enforces an 80% floor).
- Caveat: "tokens" are proxy tokens, a deterministic whitespace-plus-punctuation split documented in the suite. Real tokenizer counts depend on the model and were not measured. No claim is made about tokens billed by any provider or about model comprehension.

## Latency impact

- Codec means: 24.8 us encode, 40.5 us decode (mean of per-scenario means, 100 iterations per scenario; worst case and the 5 ms/op budget are asserted by the suite).
- This is negligible against network and model latency on any agent-message path; the codec is not on a hot loop today because the OCC modules are not yet wired into delivery.

## Reliability impact

- Fault injection: 224 deterministic corruptions per the MAST-grounded matrix (truncation at every 1/8 boundary, empty input, header/separator/boundary byte flips, NUL insertion, blank-line insertion, lowercased key, header removal, duplicated field line). Every corruption is either rejected with a pinned error variant or decodes to a grammatical-but-changed message; none decodes equal to the original; none panics.
- Never-drop invariants, tested: consolidation never drops contradictory evidence or uncertainty; the pruner's must-deliver classes (BLOCKED/CANCELLED/CORRECTION/ACK kinds, FAILED status, verification failures, contradictory findings, unseen dependencies, first message per task) are never suppressed and history eviction biases toward re-delivery; the delta tracker never applies a delta to the wrong task, buffers gaps, collapses duplicate replays, and recovers exactly across restarts.
- Delivery receipts stay separate from narrative: `DeliveryReceipt` states are transport lifecycle facts — `accepted`/`delivered` is not proof the receiving agent completed the task; only `processed`/`result_reported`/`verified` carry stronger meaning, and verification is kept separate from narrative claims. Parsers never invent missing required values.

## Regression tests

Gate commands (set the env var first; it is the recorded BASELINE-001 regression gate):

```
export KERNEL_CONTAINMENT_TEST_PYTHON=<controlled CPython 3.11 interpreter>

cargo test --locked -p pi-coding-agent --lib core::occ -- --test-threads=1
# expected: 178 passed, 0 failed

cargo test --locked -p pi-coding-agent --test occ_comm_fidelity -- --test-threads=1
# expected: 7 passed

cargo test --locked --workspace --lib -- --test-threads=1
# expected: no new failures beyond the 8 recorded pre-existing Windows failures
# (BASELINE-001: dir_lock legacy reclaim, bash spawn_context_env PATH order,
# sdk faux-provider .port-env/tmp, ipython bootstrap restore, extensions
# session-shutdown env, rlm_session_t10 admission waiter, kernel_job nested
# runtime bash/worker, shell bin-dir prepend)

python -m unittest discover -s tests -p 'test_*.py'
# expected: 282 tests OK (10 skipped)

uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test
# expected: the pre-existing 8 failures / 17 errors / 30 skips, unchanged
```

Recorded campaign result on this branch: 178/178 in `core::occ`, 7/7 fidelity, and no new failures beyond the recorded 8-failure Windows baseline. The depth-limit change adds 3 tests (2 unit, 1 prompt) with the machine-readable error prefix asserted byte-identical.

## Known limitations

- Windows-host-only verification in this phase; the Linux CI environment is expected to be cleaner than the recorded Windows baseline, and the Linux-only probe scripts were not executed here.
- `scripts/check.sh` invokes `python3`, which is not on PATH on this Windows host; the equivalent steps were run with `python` directly (recorded in the final report).
- `scripts/test-native-subagents.mjs` and `scripts/test-subagent-modes.py` assert Linux `/proc`; untested here, recorded as a platform blocker.
- The OCC modules are not yet wired into live `agent_message` delivery. This PR is the foundation plus suites: codec, contract, delta, consolidation, prune and the fidelity harness. The daemon transport, family scoping and plain-English defaults are unchanged; integration comes in a follow-up.
- GOAL/WANT/REF/WHY remain legal optional OCC-1 grammar extras (the codec tests pin that they stay legal) but are not yet first-class contract fields.

## Experimental feature flags

None added. No feature flag, config switch or daemon capability gates OCC in this PR: it is a pure optional library module (`core::occ`) with no runtime integration yet. Wiring OCC-1 into `agent_message` delivery will come as a follow-up behind a proper capability gate, per the daemon protocol rules.
