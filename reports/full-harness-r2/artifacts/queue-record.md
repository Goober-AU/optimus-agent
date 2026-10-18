# Build-queue record (single-permit, all campaign jobs)

One job at a time, `CARGO_BUILD_JOBS=2`, `-j 2`, `--test-threads=1`, BelowNormal, affinity 0-3.

| # | Owner | Exit | Duration (s) | Command |
|---|---|---|---|---|
| 1 | parent-baseline-runtime | 1 | 21.5 | `python -m unittest discover -s test -p test_*.py` |
| 2 | parent-a-core | 101 | 189.3 | `cargo test --locked -p pi-agent-core --lib -- --test-threads=1 -j 2` |
| 3 | parent-b-coding | 1 | 0.5 | `timeout 3600 cargo test --locked -p pi-coding-agent --lib -- --test-threads=1 -j 2` |
| 4 | parent-c-python | 0 | 14.5 | `env PYTHONPATH=C:/Users/openclawuser/optimus-fh2-20261018/prime-agent-runtime/src python -m unittest test_bash test_bash_guard test_winjob -v` |
| 5 | parent-integrate | 101 | 83.8 | `cargo check --locked --workspace --all-targets -j 2` |
| 6 | parent-a-core | 0 | 3.5 | `C:/Program Files/Git/usr/bin/timeout 1800 cargo test --locked -p pi-agent-core --lib -j 2 -- --test-threads=1` |
| 7 | w6-providers | 101 | 1161.3 | `cargo test --locked -p pi-coding-agent --lib -- throughput_ --test-threads=1 -j 2` |
| 8 | parent-b-coding | 101 | 1499.3 | `C:/Program Files/Git/usr/bin/timeout 5400 cargo test --locked -p pi-coding-agent --lib -j 2 -- --test-threads=1` |
| 9 | parent-c-python | 0 | 12.3 | `env PYTHONPATH=C:/Users/openclawuser/optimus-fh2-20261018/prime-agent-runtime/src python -m unittest test_bash test_bash_guard test_winjob -v` |
| 10 | parent-d-integration | 101 | 457.9 | `C:/Program Files/Git/usr/bin/timeout 3600 env PRIME_AGENT_KERNEL_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidate/pyt` |
| 11 | parent-d2-integration | 101 | 174.3 | `C:/Program Files/Git/usr/bin/timeout 3600 env KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidat` |
| 12 | parent-b2-coding | 101 | 840.2 | `C:/Program Files/Git/usr/bin/timeout 5400 env KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidat` |
| 13 | parent-c-python | 0 | 12.2 | `env PYTHONPATH=C:/Users/openclawuser/optimus-fh2-20261018/prime-agent-runtime/src python -m unittest test_bash test_bash_guard test_winjob -v` |
| 14 | parent-f-memory | 0 | 194.5 | `C:/Program Files/Git/usr/bin/timeout 2400 env KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidat` |
| 15 | parent-h-ai | 0 | 197.4 | `C:/Program Files/Git/usr/bin/timeout 2400 cargo test --locked -p pi-ai --lib -j 2 -- --test-threads=1` |
| 16 | parent-g-subagents | 101 | 96.1 | `C:/Program Files/Git/usr/bin/timeout 3000 env KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidat` |
| 17 | parent-t-throughput | 0 | 207.6 | `C:/Program Files/Git/usr/bin/timeout 1800 cargo test --locked -p pi-coding-agent --lib -j 2 -- --test-threads=1 throughput_red_indicator` |
| 18 | parent-n-node | 101 | 12.9 | `C:/Program Files/Git/usr/bin/timeout 2400 env KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidat` |
| 19 | parent-p-stream | 0 | 15.2 | `C:/Program Files/Git/usr/bin/timeout 2400 env KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidat` |
| 20 | parent-n2-node | 0 | 103.4 | `C:/Program Files/Git/usr/bin/timeout 2400 env KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidat` |
| 21 | parent-n3-nodews | 0 | 73.5 | `C:/Program Files/Git/usr/bin/timeout 1800 cargo test --locked -p pi-coding-agent -j 2 --test node_workspace -- --test-threads=1` |
| 22 | parent-rt-runtime | 1 | 65.4 | `env PYTHONPATH=C:/Users/openclawuser/optimus-fh2-20261018/prime-agent-runtime/src python -m unittest discover -s test -p test_*.py` |
| 23 | parent-e2-workspace | 101 | 1133.8 | `C:/Program Files/Git/usr/bin/timeout 7200 env KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidat` |
| 24 | parent-g2-subagents | 101 | 146.5 | `C:/Program Files/Git/usr/bin/timeout 3000 env PRIME_AGENT_KERNEL_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidate/pyt` |
| 25 | parent-uv-runtime | 1 | 63.7 | `uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test -p test_*.py` |
| 26 | parent-j1-jev | 101 | 57.0 | `C:/Program Files/Git/usr/bin/timeout 1800 cargo test --locked -p pi-jev --lib -j 2 -- --test-threads=1 retain_waits_for_actual_sink` |
| 27 | parent-j2-jevbase | 101 | 89.5 | `C:/Program Files/Git/usr/bin/timeout 1800 cargo test --locked -p pi-jev --lib -j 2 -- --test-threads=1 retain_waits_for_actual_sink` |
| 28 | parent-rt1-one | 0 | 2.1 | `C:/Users/openclawuser/optimus-fh2-20261018/prime-agent-runtime/.venv/Scripts/python.exe -m unittest test_execution_status -v` |
| 29 | parent-bwrt-runtime | 1 | 55.1 | `uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test -p test_*.py` |
