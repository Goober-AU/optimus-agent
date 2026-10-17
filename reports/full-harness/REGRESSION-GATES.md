# Regression Gates

| Gate | Command | Result |
|------|---------|--------|
| G1 Build | cargo check --locked --workspace --all-targets | PASS |
| G2 Core harness | cargo test -p pi-agent-core --lib; --test agent_loop_cancellation_contract | PASS 93 + 3/3 |
| G3 Subagents/comm | occ2_e2e_negotiation (kernel py); messaging_safety; rlm_child_comm_fix_tests | PASS 2/2, 21/21, 5/5 |
| G4 Tools | bash_executor (10+8), edit (18), node/clang (12), ipython bounds (1) | PASS |
| G5 Memory storage | memory_source_integrity 14/14; memory:: 175/175 | PASS |
| G6 Memory retrieval | cached-vs-reference parity tests (in lib) | PASS |
| G7 Persistence | torn-head 4/4; soak 11/11; compaction 9/9; supervisor backlog 13/13 | PASS |
| G8 Providers | pi-ai lib 899/899 (incl. fixed golden) | PASS |
| G9 Performance | No retained optimisation without measurement | N/A (honest) |
| G10 Portability | Windows only; Linux/macOS NOT EXECUTED | PARTIAL |
| G11 E2E | occ2 negotiation + restart; ipython real kernel; soak flows | PASS |
| G12 Open regressions | None known from campaign changes | PASS |
| Python | 9/9 root, 32/32 runtime, 43/43 controller pytest | PASS |
| Layout | scripts/check-rust-layout.py | PASS |

Env requirements for reproduction: uv on PATH;
KERNEL_CONTAINMENT_TEST_PYTHON=<repo prime-agent-runtime/.venv python (dill 0.4.1)>;
PRIME_AGENT_KERNEL_PYTHON=<kernel python (agent_message capable)>;
node on PATH for node tests; clang-repl absent -> 3 gated skips.
