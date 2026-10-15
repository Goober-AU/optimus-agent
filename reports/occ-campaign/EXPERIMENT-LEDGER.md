# OCC Campaign Experiment Ledger

Branch `improve/rlm-multi-agent-comm`, base 159ce68cc (telemusai/optimus-agent v0.1.30).
All benchmark runs are OFFLINE: no model APIs, no network, synthetic credentials only.
Model-dependent claims (agent comprehension, real token counts via a production
tokenizer, official RLM/SILO/MARBLE scores) are explicitly out of scope for these
entries and must never be inferred from the offline numbers below.

## BASE-000 — immutable baseline at 159ce68cc (clean main)

Recorded 2026-10-15 on the campaign host (Windows, private Rust toolchain 1.98.1).
Commands and artifacts:

| Check | Command | Result |
|---|---|---|
| cargo check | `cargo check --locked --workspace --all-targets` | PASS (warnings only) |
| repo python tests | `python -m unittest discover -s tests -p 'test_*.py'` | 282 tests, OK (10 skipped) |
| runtime tests | `uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test` | 465 tests: 8 failures, 17 errors, 30 skipped (pre-existing, Windows-env: bash guard `rm -rf` path handling, MCP stdio reaping, REPL interrupt timing) |
| cargo lib tests (no env) | `cargo test --locked --workspace --lib -- --test-threads=1` | 3252 passed, 22 failed, 7 ignored (all 22 = missing `KERNEL_CONTAINMENT_TEST_PYTHON`) |
| cargo lib tests (env set) | same, in a clean 159ce68cc worktree with `KERNEL_CONTAINMENT_TEST_PYTHON` = controlled CPython 3.11.15 | 3266 passed, 8 failed, 7 ignored — the 8 are pre-existing Windows-only harness/env failures unrelated to OCC (see BUG-LEDGER BASELINE-001 for the list) |

Artifacts (outside the repo, campaign storage):
`C:/Users/openclawuser/optimus-occ-baseline/cargo-lib-tests-baseline.txt`,
`runtime-tests-baseline.txt`, and the clean-env rerun
`cargo-lib-tests-baseline-clean-env.txt`.

Regression gate for all future cargo runs in this campaign:
`KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe`.
Compared against the env-set baseline, the campaign gate is: no new failures
beyond the recorded 8, and all OCC tests green.

Platform note: `scripts/test-native-subagents.mjs` and `scripts/test-subagent-modes.py`
assert Linux `/proc`; they cannot run on this Windows host. Recorded as a platform
blocker, not a pass.

## EXP-001 — RLM depth-limit graceful degradation (research port)

- Reference: alexzhang13/rlm `rlm/core/rlm.py:_subcall` (MIT, SHA d04208af) — an RLM
  at max depth degrades to a plain LM completion, not an error.
- Hypothesis: telling the deepest agent to answer directly (error text + proactive
  system-prompt line) reduces wasted spawn attempts and failure-class noise at the
  depth boundary without touching lifecycle code.
- Implementation: `rlm_max_depth.rs::rlm_depth_limit_reached_error`,
  `subagent_runs.rs` spawn guard uses it, `prompts/rlm.rs` adds the deepest-agent
  line when `allow_recursion` is false.
- Correctness before/after: unit tests (2 new + 1 prompt test) green; the
  machine-readable prefix stays byte-identical so existing parsers are unaffected.
- Tokens/latency/cost: not measurable offline; the change is prompt/error text only.
- Decision: KEEP (commit fe723f86b + changelog fragment).

## EXP-002 — OCC-1 codec + optimus.agent-comm.v1 contract (foundation)

- References: silo-bench (round-engine shape), CLSR/LSF paper (grammar+contract
  direction), MOC (evidence refs), rlm repo (compaction -> evidence references).
- Hypothesis: one typed contract behind every text encoding (OCC-1 now, OCC-2/OCC-3
  later) with deterministic parse/validate keeps task/result semantics from forking
  per representation and makes corruption detectable (see BUG-001).
- Implementation: `crates/pi-coding-agent/src/core/occ/{occ1,contract}.rs`
  (committed 8f98b218b, extended 8b14c8cb7 with next_actions).
- Offline verification: 10 scaffold unit tests green pre-worker-handoff.
- Worker suites (in flight at time of writing): adversarial codec matrix (A),
  delta messaging (B), consolidation + pruning (C), fidelity + encoding-size
  comparison with holdout (E).
- Decision: KEEP (foundation; subject to worker-suite results).

## EXP-003 — BUG-001 fix: index-gap rejection in from_occ1

- Hypothesis: gap validation (highest index present, all 1..=highest must exist)
  removes a silent-truncation data-loss path with no cost to well-formed messages.
- Implementation: `indexed_count` -> `Result<usize, ContractError>` + regression
  test `index_gaps_are_rejected_not_silently_truncated`.
- Decision: pending verification run (tree shared with in-flight worker edits).
