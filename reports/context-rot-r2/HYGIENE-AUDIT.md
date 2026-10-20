# HYGIENE-AUDIT - AGENTS.md compliance and commit hygiene (W65)

Branch: `rot2/w65-hygiene` at 90fcd17db, range `origin/main..HEAD`
(merge-base 6000d6bc1, the v0.1.36+6 integration tip). 143 commits,
361 changed files (21 modified, 340 added).

Audit method: AGENTS.md read in full and checked rule by rule; every commit
message (subject and body) parsed; `cargo clippy -p pi-coding-agent
--all-targets` run offline in an isolated target dir; `rustfmt --check`
run on all 35 touched `.rs` files with a per-file baseline diff against
`origin/main`; `git diff --check`, marker scans, secret scans and blob
size checks run over the whole range. Toolchain caveat: the campaign
toolchain (1.98.1) ships no clippy component, so clippy ran on the local
1.99.1 toolchain; rustfmt is 1.9.0 from the campaign toolchain.

## Verdict

No HIGH findings. The branch has no merge commits, no force-push
artifacts, no emojis in commit messages, code or the changelog fragment,
no leaked credentials, and no dependency or daemon-protocol changes. The
findings below are two MEDIUM items the integrator should fix or accept
before merge, four LOW polish items, and informational notes.

| ID | Severity | Finding |
| --- | --- | --- |
| M1 | medium | `.changes/context-rot-r2.md` carries four duplicated bullet pairs |
| M2 | medium | 18 new clippy warnings on branch-added lines (AGENTS.md: fix warnings you introduce) |
| L1 | low | `git diff --check` fails on three lines (2 trailing whitespace, 1 blank line at EOF) |
| L2 | low | 12 commits use the non-conventional `research` type |
| L3 | low | Filename typo `COMPATION-CORRECTNESS.md` (5 references) |
| L4 | low | Machine-local absolute paths as defaults in research drivers (tracked as REVIEW-AUDIT S4) |
| I1 | info | rustfmt drift: branch adds 524 new-content hunks; repo has no fmt gate and baseline is unclean |
| I2 | info | git-lfs pointers at HEAD; pre-conversion blobs remain in history |
| I3 | info | Subject style: 72/143 subjects over 72 chars, 4 duplicate subjects, 1 em-dash, 1 capitalized word |
| I4 | info | Model-output telemetry files contain emoji glyphs (recorded data, not prose) |
| I5 | info | Two pre-existing `never_loop` deny errors make default `cargo clippy --all-targets` fail on this repo |

## 1. AGENTS.md compliance (rule by rule)

Passes:

- No emojis in commits, issues, PR comments, or code: PASS for all 143
  commit messages (subjects and bodies), all Rust and Python source, the
  changelog fragment and all reports. The only emoji bytes in the branch
  are inside eight committed `raw-calls.jsonl` telemetry files (model
  output recorded verbatim, e.g. a checkmark in an assistant reply);
  these are data payloads, not code or prose. See I4.
- Changelog fragment: PASS. `.changes/context-rot-r2.md` exists, one
  fragment, all 33 bullets start with `Added`, `Changed`, or `Fixed`,
  and `resources/agent/CHANGELOG.md` is untouched. Content defect: M1.
- Comments: PASS on inspection. New Rust modules carry rationale
  comments proportional to non-obvious logic; no cheerleading or fluff
  comments found in the 340 added source files.
- Dependencies: PASS. `Cargo.toml` changes only add the
  `compaction-harness` `[[bin]]` target. No new dependencies, so the
  7-day release-age rule is not triggered. `Cargo.lock` untouched.
- Daemon protocol: PASS. No daemon command, event, schema or
  `DAEMON_PROTOCOL_VERSION` files touched.
- TypeScript/npm: PASS. The application stays Rust-only; no npm
  workspace or TypeScript files appear.
- Keybindings: N/A. No TUI input handling changed.
- Parallel-agent git rules: PASS. 143 single-purpose commits; history
  shows no `git add -A` sweep patterns, no merge commits in range, and
  no unrelated files (all 361 changed files map to campaign code,
  research, reports, or the documented `.gitattributes`/README/Cargo
  changes).
- Offline: PASS. Committed tests use faux and `register_api_provider_simple`
  fixtures only; no network calls in any test. Live-model drivers are
  research tools, opt-in through env vars, and are not part of the test
  suites.

Deviations: M1, M2, L1, L2, L3, L4 below.

## 2. Commit-message audit

143 commits in `origin/main..HEAD`. Every subject matches
`type(scope)?: text` (lowercase type, optional scope, colon, space).
Zero hard format violations, no emojis, no trailing periods, no merge
commits, no `--no-verify` evidence. Type distribution: docs 57, fix 31,
test 21, feat 18, research 12, refactor 2, chore 2.

- L2 - `research` type (12 commits). Not a conventional type and not in
  `origin/main` history (which does use non-standard types itself:
  bench, wip, polish, overlay). REVIEW-AUDIT S6 listed 7 of these; the
  current count is 12:
  `59832805f`, `fe6b6d966`, `0b824f358`, `4b8f2165f3`, `2fbb14f33`,
  `c5107fc31`, `fb089ee6e` (no scope), `f932b746ea`, `621dc462f`,
  `8ebc8155b`, `83ae4fb321`, `0f60ee761b`.
  Classification: fixable-by-rebase only (reword to docs/test/chore).
  Acceptable-as-is given main's own permissiveness, and a rebase would
  invalidate the report citations pinned to these SHAs. Flag only.
- I3 - Subject lengths: 72 of 143 subjects exceed 72 chars (50%),
  longest 119. For comparison, `origin/main`'s last 400 subjects run
  23% over 72 chars with a maximum of 129, and AGENTS.md states no
  length limit. Classification: acceptable-as-is, matches repo norm.
- I3 - Duplicate subjects (4 pairs, each pair is a different patch, not
  a duplicate commit): "changelog bullets for the D18-D29 fix waves"
  (5e2491cff0 amends 9cf87b2666), "refresh PR-BODY and FINAL-REPORT..."
  (c72df0c311, 68ca1cc81), "ledger status for the W38 typed-state
  fixes" (f0ddf2dec7, e15c69beca), "W35 adversarial red-team suite"
  (4f7c530fe1 trims, f7bc6306cb creates). Classification:
  acceptable-as-is.
- I3 - One body contains an em-dash (31ce39b84f) and one subject
  starts with a proper noun after the colon (f8ad670c86,
  "ContextBench"). Both fine.
- Compound subject: 6e1c3a1390 pairs a fix with an unrelated settings
  drop ("; drop unused harness settings"). Minor; acceptable.

## 3. Cargo clippy (new warnings introduced by the branch)

Command (offline, isolated target dir):

```
export RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup
export CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo
export CARGO_TARGET_DIR=<isolated>
cargo clippy -p pi-coding-agent --all-targets -- -A clippy::never_loop
```

Attribution method: every located warning was mapped to the branch's
added-line ranges (`git diff -U0 origin/main..HEAD`); warnings on
unchanged lines or in untouched files count as baseline. With clippy
1.99.1 the crate emits 1542 located warnings for pi-coding-agent
(lib 1025, lib test 1263, plus per-target lines); 18 fall on
branch-added lines. These 18 are the new set (M2):

```
src/bin/compaction_harness.rs:269               collapsible_if
src/core/compaction/compaction.rs:881           redundant_closure_with_bool_then (|x| bool)
src/core/compaction/compaction.rs:922           sort_by_key
src/core/compaction/request_tokens.rs:195       clone_on_slice_ref (unnecessary clone)
src/core/compaction/typed_state.rs:1099         empty_line_after_doc_comments
src/core/compaction/typed_state.rs:1107         sort_by_key
src/core/compaction/typed_state.rs:1169         needless_range_loop
src/core/compaction/typed_state.rs:1437         sort_by_key
src/core/compaction/typed_state.rs:3744         needless_borrow
src/core/compaction/typed_state.rs:3843         needless_borrow
src/core/context_health_calibration.rs:687      single_match_else (match on one pattern)
tests/compaction_summary_path.rs:157            useless_format
tests/compaction_summary_path.rs:266            needless_borrow
tests/rot2_defect_wave2.rs:55                    useless_format
tests/rot2_defect_wave2.rs:59                    useless_format
tests/rot2_harness_digest.rs:49                  useless_format
tests/rot2_harness_digest.rs:53                  useless_format
tests/rot2_supersession.rs:294                  collapsible_if
```

All are style-level; none indicate a correctness problem. Eleven are
in production source, seven in tests. Fix command per lint:
`cargo clippy --fix` applies most of them mechanically (review the
diff), or fix by hand. Note fa3634b7a already removed four
campaign-introduced warnings in an earlier wave; these 18 survived it.

I5 - With default settings (no `-A clippy::never_loop`) clippy 1.99.1
FAILS the build with two `deny`-by-default `never_loop` errors in files
this branch never touched (`src/core/tools/bash.rs:309`,
`src/modes/interactive/components/tool_execution.rs:881`). That is
baseline repo state on this clippy version, not a branch defect; it
only means clippy needs the `-A` flag (or those two loops need fixing
in a separate change).

## 4. cargo fmt --check (drift report)

The repo's own gate (`scripts/check.sh`) runs `git diff --check`,
`cargo check`, unittests and the layout check - it contains no
rustfmt step, and `origin/main` is not rustfmt-clean: the same 17
modified files show 470 rustfmt hunks at the base commit.

Branch state (rustfmt 1.9.0, `--check --config skip_children=true`,
edition 2021, 35 touched files):

- 491 hunk locations in the 17 modified files, of which 276 have diff
  bodies that do not exist in the baseline set (new drift introduced
  alongside the branch's edits).
- 248 hunk locations in the 18 new files (all new by definition).
- Total new-content drift: 524 hunks, dominated by assert-macro
  wrapping in the rot2 test files and long-signature wrapping in
  `typed_state.rs`/`compaction.rs`.

Classification: drift only. Running `cargo fmt` on the touched files
would produce a large mechanical diff entangled with review; the repo
has no fmt gate and the baseline is already unformatted. Recommendation:
accept as-is for this PR; if the owner wants the new files clean,
format only the 18 new files post-merge
(`rustfmt --edition 2021 crates/pi-coding-agent/tests/rot2_*.rs ...`).

## 5. Other findings

M1 - `.changes/context-rot-r2.md` contains four duplicated bullet
pairs. The fragment is the release-notes input, so the owner will see
these verbatim:

1. D18 notice: "Fixed a failed automatic compaction being invisible to
   the model..." vs "...leaving the model unaware that its context was
   never compacted..." (same fix, two bullets).
2. D19 disclosure caps: "Fixed the two live-disclosure producers...
   capped at 200 entries..." vs "Fixed unbounded live disclosure
   lists... capped at 200 entries...".
3. Render-cap correction exemption: "Fixed the v1 ledger render caps
   omitting authoritative corrections..." vs "Fixed the task-state
   ledger render caps omitting authoritative corrections from the
   ACTIVE list...".
4. Typed mode: two "Added a `typed` mode to the `taskState`
   compaction setting..." bullets (the second, post-red-team wording
   is the better one).

Fix: delete one bullet of each pair; keep the D18/D19/render-cap
first-worded bullets and the later typed-mode bullet (removes lines
L17-L20 of the current file). The W50 dedupe sweep (0e9cf7c6a) missed
these because the pairs use different wording.

L1 - `git diff --check origin/main..HEAD` reports three whitespace
errors (a reviewer running the range check, or `scripts/check.sh`'s
`git diff --check` step on a dirty tree, surfaces them):

```
crates/pi-coding-agent/tests/rot2_multicycle.rs:1185: trailing whitespace.
crates/pi-coding-agent/tests/rot2_native_typed_chain.rs:662: new blank line at EOF.
research/context_rot_r2/live/e3/README.md:85: trailing whitespace.
```

Note: the rot2_multicycle.rs space sits inside a multi-line string
literal (PAD filler in the fixture); stripping it changes fixture bytes
but no assertion reads the padding, so it is behavior-neutral. Fix by
editing the three lines.

L3 - The report filename `reports/context-rot-r2/COMPATION-CORRECTNESS.md`
misses an N. Five files reference it (`EXPERIMENT-LEDGER.md` three
rows, `FINAL-REPORT.md`, `MULTICYCLE-DRIFT.md`). Fix:

```
git mv reports/context-rot-r2/COMPATION-CORRECTNESS.md \
       reports/context-rot-r2/COMPACTION-CORRECTNESS.md
# then update the five references
```

L4 - Machine-local absolute paths remain as defaults in the research
drivers (already tracked as REVIEW-AUDIT S4; unchanged on the branch):
`run_r2e2.py:490` (credential-script default, env-overridable),
`orchestrate.py:41-43` and `incremental/driver.py:26-29` (hardcoded
toolchain/target dirs), `optimus_compactor.py:69`,
`probe_harness_behavior.py:60`, `test_optimus_compactor.py:8`,
`contextbench/live/run_live.py:37`, `repro/typecompact_repro.py:46`,
plus `compactbench_adapter/live/run-config.json` (committed config with
a machine-local harness path). No credentials are inlined anywhere
(checked: all auth is env-var indirection; no bearer tokens, keys or
JWTs in any committed file including the 340 added telemetry files).
Recommended action stays the S4 one: fail closed without an explicit
env var.

I2 - git-lfs: the five declared `raw-calls.jsonl` paths are stored as
LFS pointers at HEAD (commit b92bfdf58), the LFS objects exist locally
and the git-lfs pre-push hook is installed, so a push uploads them.
Two residual facts for the owner: the pre-conversion full blobs (8.1
MB largest) remain reachable in branch history, so the conversion does
not shrink what a clone fetches; and anyone checking out the branch
now needs git-lfs installed. This matches the documented decision in
`.gitattributes` and REVIEW-AUDIT S5.

I4 - The eight `raw-calls.jsonl` telemetry files contain emoji glyphs
that the live models emitted (checkmarks in recorded assistant
output). They are recorded data, not code or prose; AGENTS.md's emoji
rule concerns commits, issues, PR comments and code. No action.

Also verified: no conflict markers anywhere in the added files; no
ANSI escape debris; no non-UTF-8 files; no TODO/FIXME/WIP leftovers in
added code (the `PLACEHOLDER` strings in the contextbench pipeline are
a documented sentinel status, never a number); the intentional adapter
code mirroring (e.g. `rot2_supersession.rs` mirroring
`compaction_harness.rs` excerpt logic) is the documented campaign
mirror pattern.

## Fix commands (integrator checklist)

```
# M1: dedupe the fragment (delete one bullet of each of the 4 pairs)
$EDITOR .changes/context-rot-r2.md

# M2: fix the 18 new clippy warnings (see section 3 list)
cargo clippy -p pi-coding-agent --all-targets -- -A clippy::never_loop

# L1: whitespace
$EDITOR crates/pi-coding-agent/tests/rot2_multicycle.rs        # line 1185
$EDITOR crates/pi-coding-agent/tests/rot2_native_typed_chain.rs # EOF blank line
$EDITOR research/context_rot_r2/live/e3/README.md              # line 85

# L3: filename typo
git mv reports/context-rot-r2/COMPATION-CORRECTNESS.md \
       reports/context-rot-r2/COMPACTION-CORRECTNESS.md

# L2: optional reword of the 12 research-type subjects (rebase; not planned)
```

Deviation from the brief: none. The clippy run used the local 1.99.1
toolchain because the campaign toolchain 1.98.1 has no clippy
component; results are labeled accordingly above.
