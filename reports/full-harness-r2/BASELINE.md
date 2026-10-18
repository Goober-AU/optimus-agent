# Full-Harness Campaign Round 2 — Baseline (IMMUTABLE)

Recorded 2026-10-18. Do not modify after fixes begin.

## Source
- Repo: https://github.com/telemusai/optimus-agent (fork Goober-AU/optimus-agent)
- Worktree: C:/Users/openclawuser/optimus-fh2-20261018
- Branch: improve/harness-validation-r2
- Base commit: 5feb475968c42757e30ffbc6b7518b331c8ab284 (origin/main, merge of PR #174)
- Workspace version: 0.1.35 (Cargo.toml and resources/agent/package.json agree)
- Working tree at record time: clean except new campaign docs (reports/full-harness-r2/)

Round 1 of this campaign shipped as PR #172 and is part of v0.1.35. Round 2 verifies that
merged state plus PR #174 (streaming TPS family sum).

## Environment
- OS: Microsoft Windows 11 Pro (10.0.26200)
- CPU: AMD EPYC 74F3, 12 logical cores visible in this session
- rustc 1.98.1 (48a229cea 2026-09-01), cargo 1.98.1 (797e8a9bc 2026-08-05)
- Toolchain isolated: RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup,
  CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo (not on default PATH)
- Python 3.11.15 (uv-managed cpython-3.11.15-windows-x86_64-none), uv 0.11.12
- node v24.16.0 at C:/Program Files/nodejs
- clang-repl: NOT INSTALLED (clang integration tests skip; classified BLOCKED)
- `python3` shim provided at /c/Users/openclawuser/fh2-bin for test.sh compatibility

## Baseline results (pristine main, no campaign changes)

| Gate | Command | Result |
|---|---|---|
| Build | cargo check --locked --workspace --all-targets | PASS, exit 0, 386 s (under 6-worker load), pre-existing warnings only |
| Cargo lib | cargo test --locked --workspace --lib -- --test-threads=1 | see artifacts |
| Python root | PYTHONPATH= python -m unittest discover -s tests -p 'test_*.py' | see artifacts |
| Python runtime | uv-managed python -m unittest discover -s prime-agent-runtime/test | see artifacts |

Raw outputs: reports/full-harness-r2/artifacts/.
