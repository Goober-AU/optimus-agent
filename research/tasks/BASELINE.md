# OCC-2/OCC-3 Campaign Baseline: Task Suite, Message Corpus, OCC-1 Gate

Worker: bench-tasks. Worktree: `improve/occ2-occ3-communication` (base `ce8480d59`,
telemusai/optimus-agent v0.1.32). All artifacts live in `research/tasks/`.
Python only; no model calls were made for anything in this file.

## 1. Benchmark task suite

Five task families, each a two-agent communication problem: a sender agent
receives `sender_input` and must communicate the facts a receiver agent needs
to answer `receiver_task`. Every task object carries exactly
`{task_id, family, sender_input, receiver_task, gold_answer, required_facts,
difficulty}`.

| Family | Name | Design | DEV | HOLDOUT |
|---|---|---|---|---|
| A | fact handoff | 4-8 key/value facts; question needs 1-2 of them (single lookup or host:port composition) | 30 | 30 |
| B | state update | 3-7 state-change events over a 4-7 state machine; accepted/rejected events; receiver identifies the final valid state | 30 | 30 |
| C | procedural transfer | 4-8 step runbook plus progress marker; receiver identifies next/previous/located step | 30 | 30 |
| D | multi-fact reasoning | 5-9 service inventory facts; receiver combines >= 3 facts (matches plus near-miss distractors) into a set answer | 30 | 30 |
| E | dependent calculation | 3-10 numeric facts (latency, budget, capacity, queue, durations); receiver completes the dependent calculation | 30 | 30 |

Totals: 150 DEV + 150 HOLDOUT = 300 tasks. Splits are disjoint by
construction (no shared `receiver_task` inside a family; verified: 0 overlap,
300 unique task ids).

Difficulty distribution (labels derived from structural complexity: fact
counts, event counts, question type):

| Split | easy | medium | hard |
|---|---|---|---|
| dev | 71 | 52 | 27 |
| holdout | 68 | 52 | 30 |

Per family (dev easy/medium/hard - holdout easy/medium/hard):
A 15/14/1 - 15/11/4; B 13/7/10 - 14/10/6; C 16/10/4 - 11/13/6;
D 15/15/0 - 19/10/1; E 12/6/12 - 9/8/13.

### Checkers (`checkers.py`)

- A, B, C: exact match after whitespace collapse and casefold.
- D: order-insensitive set comparison on comma-separated names;
  "none" is the canonical empty answer.
- E: numeric comparison (1e-6 relative tolerance); accepts int, float, and
  numeric-string answer forms.

### Self-test results

`python gen_tasks.py` runs and must pass before files are written:

- 300/300 gold answers accepted by their family checker.
- 300/300 independent solver re-derivations: solvers parse the rendered
  `sender_input` text (fact lines, transition tables, event logs, runbooks,
  inventories, measurements) and recompute the answer; solver output equals
  gold for every task. This proves the rendered sender input actually
  contains the information the receiver needs.
- 6/6 negative controls rejected (wrong value, wrong state, wrong step,
  invented entity, extra entity, non-numeric, off-by-one).
- Determinism verified two ways: a second in-process build compares equal,
  and re-running the script reproduces byte-identical JSON files
  (sha256 stable across runs). No wall-clock input anywhere; all randomness
  flows from sha256-derived seeds.

Latest run: `self-test: 306/306 checks passed`
(300 gold + solver checks share the per-task count, plus 6 negatives).
tasks_dev.json tasks_sha256 (canonical form) starts `9514627bfcfe`;
tasks_holdout.json starts `e99c01a109b8`.

## 2. Message corpus for OCC-2 evaluation

`msgs_corpus.json`: 69 typed `optimus.agent-comm.v1` messages
(pre-encoding contract fields, NOT pre-encoded OCC-1 text), schema-compatible
with `crates/pi-coding-agent/tests/fixtures/occ/scenarios.json`
(`[{id, holdout, plain_english, payload}]`; findings carry
`{statement, confidence, evidence}` without id, evidence carries
`{id, kind, locator, line_range?, digest?, revision?, verified?}`).
The corpus follows 20 coherent task stories (occ2-01..occ2-20) across
coordinator, six workers, a security auditor, and a bench runner.

- Split: 51 train / 18 holdout (whole stories flagged holdout).
- Kinds (all 9 covered): TASK 19, PROGRESS 13, RESULT 16, EVIDENCE 5,
  ACK 4, CANCELLED 4, CORRECTION 3, BLOCKED 3, REQUEST_DETAIL 2.
- Statuses (all 7 covered): pending 19, in_progress 18, done 20, partial 3,
  blocked 3, failed 2, cancelled 4.
- Evidence kinds (all 8 covered, 55 refs total): log 12, tool 10, src 9,
  finding 8, commit 6, artifact 4, build 4, lines 2.
- Verification records (16 total): pass 10, fail 3, unknown 3.
- Findings: 68 total; confidences mix valued (0.6-1.0) and null.
- parent_task_id on 4 TASK messages (t77); expiry on 3 messages.
- Encoding sizes over the 69 payloads: plain English 23,505 bytes,
  JSON 43,673 bytes, OCC-1 (Python port of `to_occ1` field order) 24,361
  bytes; largest single OCC-1 message 661 bytes.

Validation (`gen_corpus.py` self-check, all passed): every message passes a
Python mirror of `contract.rs` `validate()` (known kinds/statuses/evidence
kinds, confidence range, evidence links resolve, EVIDENCE carries evidence,
RFC 3339 timestamps, non-empty task ids), unique message ids, payload key
set matches the fixture, plain_english is non-empty ASCII, every payload is
OCC-1 encodable within the codec bounds (128 fields, 4096-byte values,
64 KiB message), and a second build is identical (byte-identical output
verified across separate process runs).

## 3. OCC-1 baseline gate reproduction

Command (Git Bash, from the worktree root):

```
export RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup
export CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo
export PATH="/c/Users/openclawuser/optimus-rust-toolchain/cargo/bin:/c/Program Files/nodejs:$PATH"
export CARGO_TARGET_DIR=/c/Users/openclawuser/optimus-occ23-target-w5
cargo test -p pi-coding-agent --test occ_comm_fidelity
```

Result on base `ce8480d59` (fresh target directory, first build):

- Build: finished in 7m 07s (warnings only).
- Test run: `7 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out`,
  finished in 0.18s.
- Tests: t01_fixture_shape_and_holdout_rule, t02_round_trip_fidelity_all_scenarios,
  t03_encoding_size_comparison, t04_fault_injection_never_silently_passes,
  t05_semantic_faults_are_rejected, t06_latency_within_budget,
  t07_aggregate_headline_report - all ok.

This matches the OCC campaign final report gate (7 tests, all green) and is
the regression bar for later OCC-2/OCC-3 work: any change that breaks
`occ_comm_fidelity` must not land.

## 4. Reproduction

```
python research/tasks/gen_tasks.py     # regenerates tasks_dev.json / tasks_holdout.json + self-test
python research/tasks/gen_corpus.py    # regenerates msgs_corpus.json + validation + stats
```

Both scripts are deterministic (sha256-derived seeds, no wall-clock input)
and exit non-zero on any validation failure.

## 5. File inventory (research/tasks/)

| File | Purpose | Size |
|---|---|---|
| gen_tasks.py | task suite generator + solvers + self-test | 45,056 B |
| checkers.py | per-family answer checkers | 3,208 B |
| tasks_dev.json | 150 DEV tasks (30 per family) | 125,758 B |
| tasks_holdout.json | 150 HOLDOUT tasks (30 per family) | 126,280 B |
| gen_corpus.py | message corpus generator + validator + OCC-1 port | 59,052 B |
| msgs_corpus.json | 69 typed agent-comm messages | 90,616 B |
| BASELINE.md | this file | - |
