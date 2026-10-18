# Campaign Brief — Full-Harness Validation Round 2 (shared worker context)

Repo: https://github.com/telemusai/optimus-agent (fork: Goober-AU/optimus-agent)
Worktree: C:/Users/openclawuser/optimus-fh2-20261018
Branch: improve/harness-validation-r2
Base: origin/main 5feb47596 (v0.1.35 + PR #174)
Workspace version: 0.1.35

Round 1 of this campaign already merged as PR #172 (v0.1.35). Round 2 starts on that
merged state. Do not re-litigate round-1 decisions; build on them.

## Hard rules

1. Stay inside your assigned file map. Do not repo-wide grep. Search only inside the
   directories/files you were given. The host is shared and must stay responsive.
2. Only edit files in your map. If a fix needs a file outside your map, stop and report
   it to the parent; do not edit it.
3. Do NOT run git commands that change state: no add, commit, checkout, stash, reset,
   clean. The parent commits.
4. No new dependencies. No emojis in code, comments, or docs.
5. Tests must be offline and deterministic: no real provider APIs, no network, no paid
   tokens, no real user memory stores or sessions. Use temp dirs and fixtures.
6. Never weaken or delete an existing test to get green. Never mark a failure as a pass.
7. If you cannot reproduce or safely fix something, report DISCOVERED + BLOCKED with the
   exact reproducer and residual risk. Do not invent evidence.
8. Verify claims by running the test. A green run is the only acceptable proof.

## Shell environment (required for every cargo/python command)

Use this exact prelude in every bash call:

export RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup; \
export CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo; \
export PATH="/c/Users/openclawuser/optimus-rust-toolchain/cargo/bin":/c/Users/openclawuser/fh2-bin:/c/Users/openclawuser/.local/bin:"/c/Program Files/nodejs":$PATH; \
export CARGO_TARGET_DIR=<YOUR OWN TARGET DIR, given in your task>; \
cd "C:/Users/openclawuser/optimus-fh2-20261018"

Notes:
- Rust 1.98.1, cargo 1.98.1, Python 3.11.15, node v24.16.0, uv 0.11.12.
- `python3` shim exists in /c/Users/openclawuser/fh2-bin.
- Never run two cargo builds in the same target dir at once. Yours is private.
- Use `-j 3` on cargo commands to keep the host responsive.
- Long commands: run them with bash() and inspect the returned handle; do not sleep-poll.

## Build and test commands

cargo check --locked -p <crate> --all-targets
cargo test  --locked -p <crate> --lib -- <filter> --test-threads=1
cargo test  --locked -p pi-coding-agent --test <integration_test_name> -- --test-threads=1

Python runtime tests (repo source, not the installed copy):
  cd prime-agent-runtime && PYTHONPATH= /c/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe -m unittest test.<module> -v

## Known environment gaps (NOT product bugs; classify as BLOCKED, do not chase)

- clang-repl is not installed on this host.
- `uv run --project prime-agent-runtime` needs the repo .venv; use the python above.
- Installed prime-agent-runtime shadows repo src: clear PYTHONPATH for runtime tests.

## Round-1 baseline (for context, already merged)

Round 1 recorded: workspace lib green except one pi-ai golden test (fixed in round 1),
Python runtime 32/32 files green after fixes. If you see a failure, reproduce it in
isolation first and check whether it predates round 2.

## Definition of done for a worker

Produce, in your final report to the parent:
1. VERIFIED list: feature -> test/command -> result (PASS/FAIL).
2. DEFECTS: id, severity (P0..P4), file:line, root cause, reproducer, fix, test added.
3. BLOCKED: what, why, exact blocker.
4. Files you changed (exact paths) and the focused test commands you ran with results.
5. Anything in your area you deliberately did NOT touch and why.

Also write your full evidence to:
  reports/full-harness-r2/drafts/<your-area>.md
