# OCC-2 + OCC-3 Campaign Commit Plan

Status: PLAN ONLY. No git command was executed by the pr-prep worker (no add, commit, push, stash, reset, checkout). The parent integrates. This file
proposes commit grouping, exact file lists, commit messages, and the gate to re-run after each commit.

Ground rules honored (AGENTS.md): conventional commits; no emojis anywhere; `.changes` fragments (one fragment per user-visible change; this PR
carries three, like the prior campaign's two); stage exact file paths only, never `git add -A`/`git add .`; verify `git status` before each commit;
never merge your own PR (the user approves and merges); no `Cargo.toml`/`Cargo.lock` changes exist in this change set, so no dependency commit is
needed.

Branch: `improve/occ2-occ3-communication`, base `ce8480d59` (v0.1.32). All campaign changes are uncommitted in the worktree at plan time. Suggested
sequence: six commits, each compiling and test-green on its own.

## Change-set inventory (source of truth, verified by pr-prep)

Tracked modifications (git diff --stat vs base: 10 files, +998/-25):

1. `.gitignore` (+5) - research runtime ignores
2. `crates/pi-coding-agent/src/core/agent_messages.rs` (+611/-3) - OCC-2 seam: Occ2SendPolicy, wrap_outgoing_body, sentinel split, strike ledger, decode helper, occ_capability field, S1 seams, 7 unit tests
3. `crates/pi-coding-agent/src/core/agent_session/subagent_runs.rs` (+89/-2) - S1 negotiation policy, S2 spawn seam
4. `crates/pi-coding-agent/src/core/occ/activity.rs` (+76/-5) - strict Symbolic classify arm + registry-aware roster projection + 1 unit test
5. `crates/pi-coding-agent/src/core/occ/mod.rs` (+11/-2) - module declarations + level-model doc
6. `crates/pi-coding-agent/src/core/settings_manager.rs` (+18) - `agentCommunication.occ2Enabled` (default false)
7. `crates/pi-coding-agent/src/modes/daemon/daemon_mode.rs` (+29) - `occ_capability_for` helper + two struct-literal fills
8. `crates/pi-coding-agent/src/modes/daemon/daemon_session_list.rs` (+53) - roster round-trip test over a real dual payload
9. `crates/pi-coding-agent/src/modes/interactive/communication_activity.rs` (+78/-13) - SYM third slot + 2 tests
10. `crates/pi-coding-agent/src/modes/interactive/components/subagent_summary_line.rs` (+28) - three-slot width pin test

New code files: `crates/pi-coding-agent/src/core/occ/occ2.rs` (2,237 lines incl. 14 tests), `crates/pi-coding-agent/src/core/occ/dialect.rs` (1,122
lines incl. 8 tests), `crates/pi-coding-agent/tests/occ2_comm_fidelity.rs` (994 lines, 12 tests t01-t12).

Fragments: `.changes/occ2-symbolic-dialects.md`, `.changes/occ2-native-integration.md`, `.changes/occ2-telemetry.md`.

Untracked research/report trees: `research/tasks/` (7 files), `research/taskbench/` (4), `research/benchmarks/` (survey + adapter + 4 run dirs;
`runs/*/workspace/` and `.venv` are gitignored), `research/symbolic_comm/` (13), `research/latent_handoff/` (16, incl. 3 results JSONs ~1.0 MB total),
`research/integration/` (2), `reports/occ23-campaign/` (FINAL-REPORT, EXPERIMENT-LEDGER, BUG-LEDGER, OCC3-SCORECARD, PR-DESCRIPTION, PR-EVIDENCE, this file).

## Commit sequence

### Commit 1 - `feat(occ): add the OCC-2 symbolic codec and dialect registry`

Files:

- `crates/pi-coding-agent/src/core/occ/occ2.rs` (new)

- `crates/pi-coding-agent/src/core/occ/dialect.rs` (new)

- `crates/pi-coding-agent/src/core/occ/mod.rs`

- `.changes/occ2-symbolic-dialects.md` (new)

Standalone: `mod.rs` only registers the two modules and updates the level-model doc; no other file references them yet. Body: strict symbolic wire
codec over the full `optimus.agent-comm.v1` contract (dual payloads with `#FALLBACK-OCC1`, strict decode, `occ1_fallback`); dialects as data
(`DialectCard`, `DialectRegistry`, `OccCapability`, hand-designed `sym-v1` with pinned codebook sha256).

Gate after commit: `cargo test -p pi-coding-agent --lib occ::` = 203 expected (181 base + 14 + 8; the 204th is the activity classify test that lands
with commit 2).

### Commit 2 - `feat(agent): dual-encode agent messages as OCC-2 behind occ2Enabled (default off)`

Files:

- `crates/pi-coding-agent/src/core/agent_messages.rs`

- `crates/pi-coding-agent/src/core/agent_session/subagent_runs.rs`

- `crates/pi-coding-agent/src/core/settings_manager.rs`

- `crates/pi-coding-agent/src/core/occ/activity.rs`

- `crates/pi-coding-agent/tests/occ2_comm_fidelity.rs` (new)

- `crates/pi-coding-agent/src/modes/daemon/daemon_mode.rs` (PARTIAL - see split note)

- `.changes/occ2-native-integration.md` (new)

Body: `wrap_outgoing_body` + `Occ2SendPolicy` at both send seams (S1 direct/broadcast, S2 spawn prompt); sentinel split before prompt build with the
dual payload kept verbatim in the durable record; strict-then-fallback decode; pure `Occ2StrikeLedger`; `occ_capability` negotiation field; the
`agentCommunication.occ2Enabled` setting (default false = byte-identical OCC-0/OCC-1); the strict `Symbolic` classify arm (fires only on strict
`occ2::decode`); the 12-test fidelity suite over the 69-message corpus (18 holdout).

Why `activity.rs` belongs here: the fidelity suite's t09 imports `classify_with_registry`, and the `occ2-native-integration.md` fragment itself claims
the strict classify arm.

`daemon_mode.rs` split note (required for a compiling intermediate): commit 2 must include the two `AgentSessionMessageAgentSummary` struct literals
(final-file sites ~:13754 and ~:13844) staged in their recorded native-integration intermediate form, `occ_capability: None` at both (that is what the
occ2-native worker landed before telemetry superseded it; the ledger and the native fragment record these two sites). Everything else in the file (the
`use crate::core::occ::dialect::OccCapability` import, the `occ_capability_for` helper, the two literal upgrades from `None` to
`self.occ_capability_for(session)` / the `PassiveRlmRoot` match) belongs to commit 3. Practical staging: apply the full campaign diff, then hand-set
the two literals to `None`, commit 2, then restore the final form for commit 3. `native_supervisor.rs` constructs the same struct through
`..Default::default()` and needs no edit.

Gates after commit: `cargo test -p pi-coding-agent --test occ_comm_fidelity` = 7/7; `cargo test -p pi-coding-agent --test occ2_comm_fidelity` = 12/12;
`--lib occ::` = 204; `--lib communication_` = 9 (the base set, unchanged: the new activity test is named
`occ2_bodies_classify_by_the_consumed_path` and does not match the filter; the four new `communication_`
tests land with commit 3).

### Commit 3 - `feat(tui): SYM communication indicator and settings-gated OCC-2 advertisement`

Files:

- `crates/pi-coding-agent/src/modes/daemon/daemon_mode.rs` (REMAINDER)

- `crates/pi-coding-agent/src/modes/interactive/communication_activity.rs`

- `crates/pi-coding-agent/src/modes/interactive/components/subagent_summary_line.rs`

- `crates/pi-coding-agent/src/modes/daemon/daemon_session_list.rs`

- `.changes/occ2-telemetry.md` (new)

Body: `occ_capability_for(session)` advertisement (present exactly when the session's `agentCommunication.occ2Enabled` is on; resident passive roots
follow family settings, saved trees stay conservative); SYM activated as the third indicator slot (TXT/OCC/SYM, same dedupe/family scope/pulse
lifetime; Latent stays reserved and never renders); roster kind "symbolic" content-free through roster and agents-view projections.

Gates after commit: `cargo test -p pi-coding-agent --lib communication_` = 13/13; re-run `occ_comm_fidelity` 7/7 and `occ2_comm_fidelity` 12/12; the
existing occ1 wire-equality test stays green (it lives in the `daemon_session_list.rs` suite).

### Commit 4 - `chore(gitignore): ignore OCC-2/OCC-3 campaign research runtime artifacts`

Files: `.gitignore` (adds `research/latent_handoff/.venv/`, `research/**/__pycache__/`, `research/**/*.json.tmp`).

### Commit 5 - `bench(occ): campaign research harnesses, task suite, and official SILO-BENCH pilots`

Files:

- `research/tasks/` (gen_tasks.py, checkers.py, tasks_dev.json, tasks_holdout.json, gen_corpus.py, msgs_corpus.json, BASELINE.md)

- `research/taskbench/` (runner.py, results_taskbench.json, taskbench_runs.jsonl, TASKBENCH-BASELINE.md)

- `research/benchmarks/` (.gitignore, BENCHMARK-SURVEY.md, PILOT-REPORT.md, adapter/*, runs/pilot-levelI-n2-msg-glm53/*,
  runs/pilot-levelI-n2-msg-glm53flash/*, runs/occ2-levelI-n2-msg-glm53flash-occ1/*, runs/occ2-levelI-n2-msg-glm53flash-sym/* - the workspace/ subtrees
  stay gitignored)

- `research/symbolic_comm/` (all 13 files incl. evolution-log.jsonl, evolution-winner.json, requirements.txt)

- `research/latent_handoff/` (all 16 files incl. results/*.json)

- `research/integration/` (OCC2-INTEGRATION-DESIGN.md, OCC3-INTEGRATION-DESIGN.md)

Notes: the run directories carry the 49 committed non-workspace artifacts (~150 KiB: scorecards, per-case summaries, proxy_rewrites audit trails)
recorded by the merge worker. Parent decision points: (a) `research/latent_handoff/results/qwen05-results.json` (409 KB) and `smollm2-results.json`
(500 KB) are the raw records behind RESULTS-EXP-{A,B}.md - recommended in for reproducibility, but they are the bulk of the tree; (b)
`results/archive-broken-run/` (75 KB) is the pre-fix negative record - keep or trim, parent's call; (c) if the sym run's scorecard lands before
committing, include it here and fill the PR-DESCRIPTION PENDING slot. Optional split if preferred: benchmarks/tasks in one commit, symbolic_comm +
latent_handoff + integration designs in another; the grouping above keeps the campaign's research as one unit.

### Commit 6 - `docs(occ): OCC-2/OCC-3 campaign reports, PR description, and evidence index`

Files: `reports/occ23-campaign/FINAL-REPORT.md`, `reports/occ23-campaign/EXPERIMENT-LEDGER.md`, `reports/occ23-campaign/BUG-LEDGER.md`,
`reports/occ23-campaign/OCC3-SCORECARD.md` (landed by the occ3 report worker during pr-prep; NO-GO verdict, consistent with the PR body),
`reports/occ23-campaign/PR-DESCRIPTION.md`, `reports/occ23-campaign/PR-EVIDENCE.md`. This COMMIT-PLAN.md is integration metadata: commit it only if
the audit trail is wanted in-repo (the prior campaign committed its PR-DESCRIPTION/PR-EVIDENCE); default recommendation is to keep it out.

## Pre-push checklist (parent)

1. Fill or carry the PENDING slots in PR-DESCRIPTION.md (sym SILO scorecard, evolved-dialect freeze + holdout, model-pair matrix, Python 44/44 on the integrated tree).
2. Full gates on the committed tree: `cargo test -p pi-coding-agent --test occ_comm_fidelity` (7/7), `--test occ2_comm_fidelity` (12/12), `--lib occ::` (204), `--lib communication_` (13/13), `cargo check --workspace` (exit 0), `python -m unittest discover -s prime-agent-runtime/test -p "test_occ1_codec.py"` (44/44), `python research/tasks/gen_tasks.py` (306/306). Respect the campaign CPU rule (`CARGO_BUILD_JOBS=3`) while other workers may still be building.
3. `bash scripts/check.sh` equivalent: `python3` is not on PATH on this host; run the steps individually (cargo workspace checks, repo Python tests, layout check) as the prior campaign did.
4. Re-stat the diff and sync the PR-DESCRIPTION header numbers if the tree moved (commit 6 edits only docs; the +998/-25 figure is the code change set).
5. Create the PR with the title from PR-DESCRIPTION.md and the body from the finalized PR-DESCRIPTION.md (write the body to a temp file; `gh pr create --title ... --body-file ...`). Never merge it yourself; the user reviews and merges.

## Gate matrix summary

| After commit | Command | Expected |
|---|---|---|
| 1 | `cargo test -p pi-coding-agent --lib occ::` | 203 passed |
| 2 | `cargo test -p pi-coding-agent --test occ_comm_fidelity` | 7 passed |
| 2 | `cargo test -p pi-coding-agent --test occ2_comm_fidelity` | 12 passed |
| 2 | `cargo test -p pi-coding-agent --lib occ::` | 204 passed |
| 2 | `cargo test -p pi-coding-agent --lib communication_` | 9 passed (base set; the 4 new tests land in commit 3) |
| 3 | `cargo test -p pi-coding-agent --lib communication_` | 13 passed |
| 3 | re-run both fidelity suites | 7/7 and 12/12 |
| 6 | `cargo check --workspace` | exit 0 (single final run) |

Caveat on the intermediate counts (commit-2 `communication_` = 9, commit-1 `occ::` = 203): these are plan-time
expectations derived from the test inventory, not recorded gate runs; the recorded gates are the final-tree numbers (204, 7/7, 12/12, 13/13). If an intermediate count differs, the final-tree gates are the
authority.
