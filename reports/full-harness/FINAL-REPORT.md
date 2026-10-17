# Full-Harness Validation & Optimisation Campaign — Final Report

Branch: improve/full-harness-validation (base c227b0ae3, v0.1.34)
Host: Windows 11 Pro, AMD EPYC 74F3 24-core, 110 GB RAM, rustc 1.98.1

## What was done

Wave 1 (8 read-only auditors) inventoried every major subsystem with file:line
evidence and produced a risk-ranked gap list. Wave 2 (12 fix workers, disjoint
file ownership) reproduced, fixed and regression-tested every defect that could
be safely reproduced offline. All fixes were gated by focused test runs and a
full serialized workspace lib run.

## Baseline (pristine main, immutable record)

See BASELINE.md. Summary: build PASS; cargo lib 1 deterministic failure
(pi-ai golden partial-snapshot loop — a PR #167 test-authoring bug, fixed
here); Python 9/9 root + 31/32 runtime files (test_repl.py flake class);
known env gaps: uv not on PATH, KERNEL_CONTAINMENT_TEST_PYTHON python lacks
dill, no clang-repl, no python3 alias (test.sh:5).

## Fixes shipped (12 commits)

1. fix(agent-loop): sequential-cancel dangling tool calls, execution-scope
   pruning, custom-tail continue rejection, abort wording (P1 x2, P2, P3)
2. fix(bash): streaming UTF-8 decode, completion-vs-cancel race, Windows
   job-object containment with descendant tree-kill (P1 x3)
3. fix(memory): un-wedge import retries, host-id quarantine+re-mint, marked
   span truncation, settings logging, harness quarantine-before-rewrite,
   controller settings parity for all 36 driver keys (P2 x5)
4. fix(session): queue_visible TS-parity default, checked fork writes, honest
   lease guards, fsync honoring (P1, P2 x3)
5. fix(occ): name-addressed OCC-2 negotiation, bounded terminal-notice
   retries, idempotent child publication (P2 x2, P3)
6. fix(kernel): stdout/stderr frames dropped at budget now flag truncation
   (silent ~1.9 MB loss on 2 MB prints), bounded error text, UTF-8-safe
   bash() caps, reap-callback concurrency bug (P0-class, P1)
7. fix(tools): non-UTF-8 edit rejection, MCP arg validation, CRLF-exact
   truncation (P2 x3)
8. fix(tools): node/clang byte-exact budgets, visible truncation notices,
   dispose/execute race, no false notices at exact fit (P2/P3)
9. fix(session): torn-head quarantine instead of destructive reset; get_state
   roster fallback marked stale (P1 x2, PR #168 follow-through)
10. test(slash): stale 42-command guard regenerated to the real 43-command
    registry (the only registry-wide guard was red on main)
11. test(ai): golden partial-snapshot loop contradiction fixed
12. test(infra)+test(compaction)+test(occ2 e2e): validation runner, compaction
    recovery suite, PR #166 closure e2e

## Test evidence

See artifacts/final-gate-results.json and BASELINE.md. Highlights:
- Workspace lib serial: 4695 tests, 0 failures (env vars set)
- New integration suites: occ2 e2e 2/2, compaction 9/9, soak 11/11,
  torn-head 4/4, memory integrity 14/14, edit behavior 18/18,
  node/clang 12/12, bash unicode/containment 8/8, ipython bounds 1/1,
  agent-loop contract 3/3
- Python: 9/9 root files, 32/32 runtime files, 43/43 controller pytest

## Performance

The campaign prioritised correctness; perf claims from PRs #167-#171 were
verified structurally (no regression introduced, clone-elimination tests
still pass) but no new optimisation was retained without a before/after
benchmark. The runner's --performance mode provides the reproducible harness
for future runs (12 perf suites inventoried).

## Known limitations (honest classification)

- clang-repl absent on host: 3 clang integration tests skip (logic covered
  by in-file unit tests)
- Linux/macOS: NOT EXECUTED (Windows host only)
- Node tests require node on PATH (documented; C:/Program Files/nodejs)
- 15 slash-matrix rows honestly UNVERIFIED for TS parity (tree removed
  upstream); dispatch ownership verified for all
- The memory benchmarks (LongMemEval/LoCoMo/PersonaMem) were not run: they
  need authorised datasets + live models; the controller was FIXED so they
  can be driven (recallFusion/recallBm25/persona* keys now accepted)
- BUG-2 (agentOrigin reach fence): pinned as TS-parity intentional with a
  two-sided test; not changed
- SB-5 stall partial-content drop, SB-06 jobs.rs failed-status write,
  daemon_mode.rs id-only peer find (BUG-1 twin), harness.py answerInstruction
  staleness: documented, not fixed (owners/routing in the drafts)

## Reproduction

```
python scripts/full_harness_validation.py --list
python scripts/full_harness_validation.py --smoke
# full gates need: uv on PATH, KERNEL_CONTAINMENT_TEST_PYTHON=<repo .venv python>,
# PRIME_AGENT_KERNEL_PYTHON=<kernel python>, node on PATH for node tests
python scripts/full_harness_validation.py --full
python scripts/full_harness_validation.py --report
```

Drafts with complete file:line audit evidence: reports/full-harness/drafts/
