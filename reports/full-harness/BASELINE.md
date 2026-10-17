# Full-Harness Campaign — Baseline Record (IMMUTABLE)

Recorded: 2026-10-17 (UTC+11 local). Do not modify after fixes begin.

## Source
- Repo: https://github.com/telemusai/optimus-agent
- Worktree: C:/Users/openclawuser/optimus-fh-20261017
- Branch: improve/full-harness-validation (from origin/main)
- Base commit: c227b0ae35419f81adea52d177df203aed4f58c7 (merge of PR #171)
- Workspace version: 0.1.34
- Note: prompt-stated baseline 340429e66f was superseded; origin/main moved 3 merges ahead
  (PR #169 session TPS + shutdown socket, PR #170 realtime throughput TPS display, PR #171 OCC evidence kinds).
  Campaign targets current main per instructions ("Verify actual HEAD before starting").

## Environment
- OS: Microsoft Windows 11 Pro
- CPU: AMD EPYC 74F3 24-Core
- RAM: 110 GB
- Disk free: ~443 GB on C:
- rustc: 1.98.1 (48a229cea 2026-09-01)
- cargo: 1.98.1 (797e8b9bc 2026-08-05)
- Toolchain: RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup (isolated, not on default PATH)
- Python: 3.11.15
- uv: located at C:/Users/openclawuser/.local/bin/uv.exe — NOT on PATH by default
  (this is the previously-disclosed cause of env-dependent suite failures in PR #167)
- KERNEL_CONTAINMENT_TEST_PYTHON: C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe (exists)
- Runtime tests need PYTHONPATH cleared (installed runtime shadows repo src)

## Baseline test runs (pristine main, no campaign changes)
To be appended below as each gate completes. Raw outputs preserved under reports/full-harness/artifacts/.


## Baseline results (2026-10-17, pristine c227b0ae3)

| Gate | Command | Result |
|---|---|---|
| Build check | cargo check --locked --workspace --all-targets | PASS (4m38s; pre-existing warnings only) |
| Cargo lib tests | cargo test --locked --workspace --lib -- --test-threads=1 | 1 FAIL: pi-ai `partial_snapshots_match_a_golden_multi_block_event_sequence` — panics left=None right=Some("chatcmpl-gold") at openai_completions.rs:6021; REPRODUCES IN ISOLATION (not load). All other crates: pass (898/899 in pi-ai; rest green). |
| Python root tests | per-file `python -m unittest tests.<name>` | 9/9 files PASS |
| Python runtime tests | per-file `uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test -p <file>` | 31/32 files PASS. test_repl.py: 2 failures — `test_bash_activity_and_notice_wait_for_group_reap` (both awaited variants) and `test_ready_handshake_and_startup_time` (667ms > 500ms). All PASS in isolation on quiet CPU -> load-induced flake class, NOT product bug. |

Classification per campaign rules:
- pi-ai partial-snapshot failure: genuine product/test defect (deterministic in isolation). Investigated as wave-2 item.
- test_repl.py failures: environment/load-induced flake (host CPU at ~95% during baseline). Classify as known flake; re-verify post-fix under quiet conditions.
- Known env gaps (PR #167 class): uv not on PATH (root agent supplies), KERNEL_CONTAINMENT_TEST_PYTHON python lacks dill (repo .venv has dill 0.4.1), node not on bash PATH (exists at C:/Program Files/nodejs/node.exe), clang-repl absent, python3 alias absent (test.sh:5 uses python3).

Raw artifacts: reports/full-harness/artifacts/baseline-python-root.json, baseline-python-runtime.json
