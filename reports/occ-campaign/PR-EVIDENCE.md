# OCC Campaign PR Evidence Index

Branch `improve/rlm-multi-agent-comm`, base `159ce68cc` (telemusai/optimus-agent v0.1.30 main), tip `e8b48e7ef`.
Campaign window: 2026-10-15. Host: Windows, private Rust toolchain 1.98.1, Git Bash.
Diff vs base at tip: 11 commits, 23 files changed, 12,173 insertions(+), 3 deletions(-); no `Cargo.toml`/`Cargo.lock` changes (no dependency changes).

Purpose: single consolidated, reviewer-facing evidence index for this PR. Every number below traces to a
campaign ledger, report, or recorded artifact (Sources); items marked [re-verified] were re-run at the
committed tip during packaging. Cross-check findings and discrepancies: section 6.

Sources: `reports/occ-campaign/EXPERIMENT-LEDGER.md`, `reports/occ-campaign/BUG-LEDGER.md`,
`reports/occ-campaign/FINAL-REPORT.md`, `reports/occ-campaign/PR-DESCRIPTION.md`,
`docs/research/agent-communication-landscape.md` (720 lines; 8 references cloned at exact SHAs),
`.changes/occ-compact-communication.md`, `.changes/rlm-depth-degradation.md`,
`git log`/`git diff --stat 159ce68cc..HEAD`, and campaign-storage artifacts `check-gate.txt`,
`campaign-lib-tests.txt`, `cargo-lib-tests-baseline-clean-env.txt`, `runtime-tests-baseline.txt`
(campaign storage: `C:/Users/openclawuser/optimus-occ-baseline/`).

Campaign commits (`159ce68cc..e8b48e7ef`): `8f98b218b` OCC scaffold, `8b14c8cb7` contract next actions,
`4df3adba1` research report, `fe723f86b` RLM depth degradation, `974c8dd3a` changelog fragment,
`1b537f5fc` delta messaging, `b930e28cc` fidelity suite, `cc1ec0870` index-gap fix + dialect/encoder
alignment + EADD + worker suites, `690301551` BlankLine doc fix + ledgers, `135a9e3bb` final report,
`e8b48e7ef` PR description + ledger cleanups.

## 1. Test matrix

Every cargo call in this campaign sets `KERNEL_CONTAINMENT_TEST_PYTHON` (the BASELINE-001 regression gate)
and the private toolchain env (section 4). All results are offline and deterministic. `core::occ` 178/178
and fidelity 7/7 are the recorded campaign gate results at tip `e8b48e7ef` (the two commits after
`690301551` are docs plus a comment-only fidelity-suite change).

| Suite | Tests | Result | Command |
|---|---|---|---|
| `core::occ` (campaign total) | 178 | 178 passed, 0 failed | `cargo test --locked -p pi-coding-agent --lib core::occ -- --test-threads=1` |
| — adversarial codec (subset of the 178) | 103 | all green | same run |
| — delta messaging (subset) | 23 | all green (20 at commit `1b537f5fc` + 3 EADD tests in `cc1ec0870`) | same run |
| — consolidation (subset) | 18 | all green | same run |
| — prune (subset) | 21 | all green | same run |
| — contract inline (subset) | 7 | all green | same run |
| — occ1 inline (subset) | 6 | all green | same run |
| Communication-fidelity suite (`t01`-`t07`) | 7 | 7 passed, 0 failed | `cargo test --locked -p pi-coding-agent --test occ_comm_fidelity -- --test-threads=1` |
| RLM depth-limit tests (EXP-001) | 3 per ledger; git diff shows 2 new test functions (see 6.3) | green; machine-readable error prefix byte-identical | included in the `pi-coding-agent` lib run |
| Repo Python tests | 282 | OK (10 skipped) [re-verified at tip] | `python -m unittest discover -s tests -p 'test_*.py'` |
| Runtime Python tests | 465 | 8 failures, 17 errors, 30 skipped - pre-existing Windows-env (BASE-000), unchanged by this PR [re-verified: 465 tests, same failing set - see 6.1] | `uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test` |
| Native layout check | - | PASS: "Native resource layout verified; no TypeScript implementation or npm workspace." [re-verified at tip] | `python scripts/check-rust-layout.py` |
| `cargo check` | - | green (warnings only); artifact `check-gate.txt` (run after `690301551`; the two later commits are docs/comment-only) | `cargo check --locked --workspace --all-targets` |
| Full-workspace lib gate | 3 lib binaries | 4,384 passed, 8 failed, 7 ignored; the 8 are exactly the BASELINE-001 pre-existing set, so 0 new failures | `cargo test --locked --workspace --lib -- --test-threads=1` (env gate set); recorded by the gate-regression agent, artifact `campaign-lib-tests.txt` |

`core::occ` breakdown check: 103 + 23 + 18 + 21 + 7 + 6 = 178.

Full-workspace lib gate detail (artifact `campaign-lib-tests.txt`, completed 2026-10-15 08:15 during
packaging; per-binary `test result:` lines):

| Test binary | Passed | Failed | Ignored |
|---|---|---|---|
| `pi-agent-core` (lib) | 83 | 0 | 0 |
| `pi-ai` (lib) | 855 | 0 | 0 |
| `pi-coding-agent` (lib) | 3,446 | 8 | 7 |
| Workspace total | 4,384 | 8 | 7 |

Clean-main baseline with the env var set (`cargo-lib-tests-baseline-clean-env.txt`): `pi-coding-agent` lib
3,266 passed / 8 failed / 7 ignored, with the identical eight failure names. This PR therefore adds 180
passing tests (178 `core::occ` + 2 depth-limit) and zero new failures; the 8 remaining failures are the
recorded BASELINE-001 Windows harness/env defects, none touching OCC modules or campaign-modified files.

Baseline reference (BASE-000, clean main `159ce68cc`, same host and env gate, per EXPERIMENT-LEDGER):

| Check | Baseline result |
|---|---|
| `cargo check --locked --workspace --all-targets` | PASS (warnings only) |
| Repo Python tests | 282 tests, OK (10 skipped) |
| Runtime Python tests | 465 tests: 8 failures, 17 errors, 30 skipped (pre-existing Windows-env) |
| `cargo test --locked --workspace --lib` (no env var) | 3,252 passed, 22 failed, 7 ignored (all 22 = missing `KERNEL_CONTAINMENT_TEST_PYTHON`) |
| same, env var set (clean worktree) | 3,266 passed, 8 failed, 7 ignored (the BASELINE-001 eight) |

Platform blockers (recorded, not run): `scripts/test-native-subagents.mjs` and
`scripts/test-subagent-modes.py` assert Linux `/proc`; they cannot run on this Windows host.

## 2. Bug fixes

| ID | Status | Commits | Root cause | Regression tests | Verification |
|---|---|---|---|---|---|
| BUG-001 - OCC-1 index gaps silently truncate | FIXED | introduced `8f98b218b` (scaffold, extended `8b14c8cb7`); fixed `cc1ec0870` | `indexed_count` in `contract.rs` counted contiguous presence (`while has(prefix+count+1)`) instead of validating against the highest index present; a message with `F1` + `F3` but no `F2` silently dropped `F3` and decoded as a shorter valid message - silent data loss in agent-to-agent results, severity high | `contract::tests::index_gaps_are_rejected_not_silently_truncated` (F-gap and E-gap both rejected), matching adversarial-suite gap cases, fidelity `t05` | `core::occ` 178 passed, 0 failed after the fix |
| BUG-002 - dialect/encoder misalignment | FIXED | fixed `cc1ec0870` | the standard `Occ1Dialect` required-field table demanded GOAL/REF/WANT/WHY fields the `optimus.agent-comm.v1` encoder never emits, so contract-encoded messages failed standard dialect validation | `every_contract_encoded_kind_passes_the_standard_dialect`, `evidence_kind_without_evidence_is_rejected`, dialect table tests | 103-test adversarial codec suite green; fidelity `t01`/`t02` round-trip all 28 scenarios |
| BASELINE-001 - Windows test-env failures on clean main | BASELINE_EXISTING (not introduced by this PR) | no fix commit; mitigated by the env-var gate (clears 14 of 22) | 22/22 clean-main lib failures panic on missing `KERNEL_CONTAINMENT_TEST_PYTHON`; with it set, 8 Windows harness/env defects remain (legacy dir-lock reclaim, Git Bash PATH order, missing `.port-env/tmp` fixture, ipython bootstrap restore, extensions session-shutdown env, admission-waiter snapshot, nested kernel_job, shell bin-dir prepend) | the campaign gate sets the env var on every cargo run; failures are attributed to this PR only if a run regresses beyond the recorded 8 | campaign lib gate reproduced exactly the 8 (same names), 0 new |

Related corrections shipped with the fixes (`cc1ec0870`, `690301551`): the `EADD=E<n>` wire form for
`FieldUpdate::AddEvidence` (the last non-round-trippable delta variant; 3 regression tests: round-trip,
foreign-reference rejection, out-of-range rejection) and the BlankLine error-doc correction (interior
whitespace-carrying lines are `MissingSeparator`; only truly empty lines are `BlankLine`; pinned by the
codec suite).

## 3. Offline metrics

All numbers are from the fidelity suite over the 28-scenario corpus
(`crates/pi-coding-agent/tests/fixtures/occ/scenarios.json`; 7 holdout: s04, s08, s12, s16, s20, s24, s28;
train and holdout reported separately in the suite output, same direction). Bytes are exact serialized
lengths; "proxy tokens" are a whitespace-plus-punctuation split (a tokenizer approximation).

Encoding comparison (aggregate over the 28 scenarios):

| Encoding | Bytes | Proxy tokens |
|---|---|---|
| Plain English (OCC-0, default) | 28,022 | 5,171 |
| Concise English | 13,665 | 2,710 |
| JSON | 22,904 | 4,357 |
| OCC-1 | 14,659 | 3,069 |

- OCC-1 vs plain English: -47.7% bytes, -40.6% proxy tokens; `occ1 <= plain` in 28/28 scenarios.
- OCC-1 vs JSON: -36.0% bytes, -29.6% proxy tokens; `occ1 <= json` in 28/28 scenarios (suite floor: 80%).
- Concise English is ~7% smaller than OCC-1 but untyped: not machine-validatable, no structural evidence
  references or required next actions, corruption not detectable.

Fault injection and codec measurements (fidelity suite):

| Metric | Denominator | Result |
|---|---|---|
| Wire corruptions | 224 fixed corruptions (14 scenarios x 16) | 128 rejected with pinned error variants; 96 grammatical-but-changed; 0 decoded equal to the original; 0 panics |
| Semantic faults | 25 header-valid but semantically broken messages | 25/25 rejected with `ContractError` |
| Round-trip fidelity | 28 scenarios (7 holdout) | 28/28 exact `PartialEq` equality through validate -> to_occ1 -> encode -> decode -> from_occ1 |
| Codec latency | 100 iterations/scenario, 28 scenarios | encode mean 24.8 us, decode mean 40.5 us (suite budget 5 ms/op, worst case asserted) |

## 4. Reproduction commands

Copied exactly from `FINAL-REPORT.md` section I.

Rust environment (Git Bash on Windows, every cargo call):

```
export RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup
export CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo
export PATH="/c/Users/openclawuser/optimus-rust-toolchain/cargo/bin:/c/Program Files/nodejs:/c/Users/openclawuser/.local/bin:$PATH"
```

Cargo test gate (the env var is the BASELINE-001 regression gate):

```
export KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe
cargo test --locked -p pi-coding-agent --lib -- --test-threads=1          # core::occ: 178 passed
cargo test --locked -p pi-coding-agent --test occ_comm_fidelity -- --test-threads=1   # fidelity: 7 tests
```

Python tests (run with `python`, not `python3`, on this host):

```
python -m unittest discover -s tests -p 'test_*.py'
uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test
```

## 5. Honest limits

- Offline-only: no network, no model APIs, no real credentials (synthetic credentials only).
- "Tokens" are proxy tokens (whitespace-plus-punctuation split). Real tokenizer counts, agent
  comprehension of the encodings, and official RLM/SILO/MARBLE scores are NOT claimed; no online suite
  was run in this phase.
- Windows-host-only verification; the Linux CI environment is expected to be cleaner than the recorded
  Windows baseline. Linux-only probes blocked: `scripts/test-native-subagents.mjs` and
  `scripts/test-subagent-modes.py` (assert Linux `/proc`).
- 8 pre-existing Windows harness failures remain in the lib baseline (BASELINE-001); the runtime Python
  suite keeps its pre-existing 8 failures / 17 errors (Windows-env, unrelated to OCC; none of the
  campaign commits touch Python).
- `scripts/check.sh` hardcodes `python3`, not on PATH on this host; its steps were run individually
  with `python` (cargo check, repo Python tests, layout check).
- Latency was measured for the OCC-1 codec path only (the other encodings have no codec to time).
- The OCC modules are not yet wired into live `agent_message` delivery: this PR is the codec, contract,
  delta, consolidation, prune and the fidelity harness; the daemon transport, family scoping and
  plain-English default are unchanged. No feature flag added.

## 6. Cross-check notes

Every number above was cross-checked against the ledgers and artifacts. Findings:

1. Runtime Python count: the packaging brief said 466; EXPERIMENT-LEDGER (BASE-000), FINAL-REPORT,
   PR-DESCRIPTION and the baseline artifact all record 465 tests (8 failures, 17 errors, 30 skipped).
   The ledger wins: 465 recorded. Re-verified at the committed tip: `Ran 465 tests`, 7 failures,
   17 errors, 30 skipped - the identical failing test set except one flaky subTest of the recorded
   bash-guard flake (`test_rm_options_quotes_and_protected_paths`), which passed on the re-run.
2. Delta test count: the packaging brief said 20; 20 is the count at the first delta commit
   (`1b537f5fc`); after the 3 EADD tests (`cc1ec0870`) the suite is 23, as required by the 178 total
   (103 + 23 + 18 + 21 + 7 + 6). Recorded: 23, with the 20 + 3 breakdown.
3. Depth-limit test count: EXP-001, FINAL-REPORT and PR-DESCRIPTION record "3 new tests (2 unit +
   1 prompt)". The git diff adds 2 test functions
   (`rlm_max_depth.rs::depth_limit_error_keeps_limit_facts_and_adds_degradation_guidance`,
   `prompts/rlm.rs::max_depth_prompt_states_the_deepest_agent_degrades_directly`), and the lib gate
   grew by exactly 180 passing tests (178 `core::occ` + 2). The ledger figure (3) is what the matrix
   records; the diff and lib-gate arithmetic (2) is reported here for resolution before merge.
4. Diff-stat snapshots are commit-sensitive: FINAL-REPORT records 21 files / +11,520 (at `690301551`,
   before its own file), PR-DESCRIPTION records 10 commits / 22 files / +12,023 (at `135a9e3bb`, before
   its own file); the actual tip `e8b48e7ef` is 11 commits / 23 files / +12,173 / -3. All describe the
   same branch at successive documentation commits.
5. All bug-fix, fidelity, fault-injection and encoding-size numbers in sections 2-3 match BUG-LEDGER,
   FINAL-REPORT and PR-DESCRIPTION exactly; no other disagreements were found.
