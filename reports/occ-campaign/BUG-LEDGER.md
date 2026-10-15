# OCC Campaign Bug and Failure Ledger

Campaign branch: `improve/rlm-multi-agent-comm` (base 159ce68cc, telemusai/optimus-agent v0.1.30).

Statuses: NEW / REPRODUCED / ROOT_CAUSED / FIX_IN_PROGRESS / FIXED / VERIFIED / DEFERRED / BLOCKED / BASELINE_EXISTING.

## BUG-001 — OCC-1 index gaps silently truncate instead of rejecting

- Status: FIXED (verification pending the adversarial codec suite run)
- Found by: worker `occ-codec-tests` (adversarial test design review), confirmed by parent code read.
- Benchmark/task ID: offline OCC-1 codec (fidelity suite scenarios with multi-finding payloads).
- Source revision: 8f98b218b (scaffold) + 8b14c8cb7.
- Observed behaviour: `AgentCommMessage::from_occ1` accepted a message with
  `F1` and `F3` but no `F2`; `indexed_count` stopped at the first missing
  index, so `F3` was silently dropped and the message decoded as a shorter
  valid message.
- Expected behaviour: a highest index of N with any of 1..=N absent is
  corruption; decode must reject it (corrupted compact messages are
  rejected safely, never partially accepted).
- Reproduction: build `Occ1Message` with TASK/KIND/STATUS + `F1` + `F3`,
  call `from_occ1`; old code returned Ok with one finding.
- Root cause: `indexed_count` in `contract.rs` counted contiguous presence
  (`while has(prefix+count+1) count += 1`) instead of validating contiguity
  against the highest index present.
- Affected code: `crates/pi-coding-agent/src/core/occ/contract.rs`
  (`indexed_count`, used for F/E/ART/DEP index families).
- Severity: high (silent data loss in agent-to-agent results; a corrupted
  message could drop findings/evidence without any error).
- Fix: `indexed_count` returns `Result<usize, ContractError>`; it scans all
  keys with the prefix, takes the highest numeric suffix, and errors when
  any index 1..=highest is missing. All four call sites propagate the error.
- Verification tests: `contract::tests::index_gaps_are_rejected_not_silently_truncated`
  (F-gap and E-gap both rejected); worker A's adversarial suite stages
  matching cases.
- Regression results: core::occ suite 178 passed, 0 failed after the fix
  (worker A's gap tests stayed active and assert the new error text);
  fidelity suite t05 pins the rejecting behavior.
- Fix commit: cc1ec0870

## BUG-003 — PATH bin-dir prepend suppressed by inherited mid-list copy

- Status: FIXED (commit in this PR)
- Found by: worker fix-bash-shell-path (both PATH-assertion tests).
- Root cause: `shell_env_with_bin_dir` skipped the prepend entirely when
  bin_dir appeared ANYWHERE in the inherited PATH, so an inherited
  mid-list copy left managed tools shadowed by harness prefixes — the
  documented prepend invariant (bin_dir leads exactly once) was violated.
  Platform-independent logic, triggered by this host's env shape.
- Fix: unconditionally place bin_dir first and filter existing copies;
  empty entries still dropped; idempotent.
- Regression: `shell_env_moves_an_inherited_mid_list_bin_dir_to_the_front`
  (fails without the fix); both original tests now assert first-entry +
  exactly-once precisely.

## BUG-004 — Legacy directory-lock reclaim unreachable (95c9308cd2 regression)

- Status: FIXED (commit in this PR)
- Found by: worker fix-dir-lock.
- Root cause: two blanket "directory at the lock path means Held" guards
  made the legacy-reclaim path dead code on every platform, regressing
  the ported TypeScript contract (a legacy lock dir with ownerless/stale
  pid must be judged and reclaimed).
- Fix: guard scoped to non-legacy directories (entries empty or exactly
  {pid}); registry-shaped directories stay Held untouched; aside removal
  loses only the pid marker via remove_dir, never remove_dir_all.
- Regression: dead-owner legacy lock reclaims; registry-shaped lock stays
  Held with files intact.

## BUG-005 — bash guard blanket-refused Windows rm; backslash paths misclassified

- Status: FIXED (commit in this PR)
- Found by: worker fix-rt-bash-guard (9 rm subTest errors + relocation
  and cdpath errors).
- Root cause: (a) the rm branch refused all rm on non-POSIX platforms
  instead of analyzing operands; (b) `_literal()` rejected backslash, but
  after posix-mode shlex a surviving backslash is quote-protected, so
  quoted Windows absolute paths were misclassified as dynamic.
- Fix: per-operand rm analysis on Windows (.. / hidden / outside-repo
  checks retained), backslash-quoted paths are literal, git -C sites
  treat untranslatable paths as unknown, and the genuinely ambiguous
  driveless-rooted class (/tmp) gets a targeted refusal.
- Regression: two tests pin both fixes (one portable via os.name mock,
  one skipUnless nt).

## BASELINE-001 — Windows test-env failures on clean main

- Status: BASELINE_EXISTING (partially: 14 of 22 cleared by setting the env var)
- Suite: `cargo test --locked --workspace --lib` at 159ce68cc on this
  Windows host.
- Run 1 (no env): 3252 passed, 22 failed, 7 ignored. All 22 failures panic
  on missing `KERNEL_CONTAINMENT_TEST_PYTHON`
  (`env::var("KERNEL_CONTAINMENT_TEST_PYTHON").unwrap()` /
  `.expect(...)` in kernel_job and repl_manager tests).
- Run 2 (clean-main worktree, env var set to the controlled uv-managed
  CPython 3.11.15 interpreter): 3266 passed, 8 failed, 7 ignored
  (`cargo-lib-tests-baseline-clean-env.txt`).
- Classification of the remaining 8:
  - test-harness/environment defects on Windows, not OCC-path product bugs:
    `dir_lock::reclaims_a_legacy_directory_lock_without_an_owner`
    (expects `Reclaimed`, gets `Held` — legacy-lock reclaim path differs on
    Windows), `core::tools::bash::spawn_context_env_uses_the_shell_env_owner`
    (`PATH` order assertion fails under Git Bash environment), `sdk`
    faux-provider fixture expects `.port-env/tmp` under the repo (missing
    dir on this checkout), `ipython bootstrap` restore failure,
    `extensions` session-shutdown helper env, `rlm_session_t10` admission
    waiter snapshot, `kernel_job::native_job_nested_runtime_bash_and_worker_job`,
    `shell::shell_env_prepends_the_bin_dir_once`.
  - None touch the OCC modules or the files modified by this campaign.
- Action for this campaign: the regression gate sets
  `KERNEL_CONTAINMENT_TEST_PYTHON` for every cargo test run; the 8 failures
  are recorded as the clean-main Windows baseline and are not attributed to
  campaign changes unless a run regresses beyond them. Fixing them is out of
  campaign scope (none block the OCC work), recorded honestly here.
- RESOLUTION UPDATE (follow-up request): all 8 are now fixed in this branch.
  Classifications: 6 test defects (sdk scratch dir, kernel_job env
  derivation, ipython fixture CRLF, extensions runner fixture isolation,
  t10 async poll, mcp/lifecycle runtime probes), 2 product bugs
  (BUG-003 PATH prepend, BUG-004 legacy dir-lock reclaim; the latter is a
  platform-independent regression from 95c9308cd2, not Windows-specific).
  The runtime suite's 9F/17E resolved into: bash guard product bugs
  (BUG-005, fixed), POSIX-only test assumptions (fixed with reason-tagged
  skips or portable probes), and repl/harness platform semantics (fixed by
  the follow-up lane).
