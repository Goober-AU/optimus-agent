# OCC-3 Coding Benchmarks

**Status: STUB — awaiting live evidence.** Real coding-agent evaluations with OCC-3 enabled have not been run. The harness exists offline with task fixtures, but no GPU-backed results exist.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Benchmark goal: coding-agent task success with OCC-3 handoffs vs OCC-1/OCC-2/text control planes under matched model and effort.
2. Harness description: task fixtures (asymmetric information), runner modes, analysis scripts, proof boundaries.
3. Runs and results: per-task success, retries, fallback events.
4. Correctness failure analysis and iteration until diminishing returns (spec PHASE 5).

## Evidence already available (offline)

- Offline harness: extensions/sglang-occ3/benchmarks/bench_occ3.py (sha256 42f892fb9da44da99334183b6b79c4f1f50817519e471c7e3818c12458c170b2), analyze.py (sha256 56328016a65443395dd5bd76000682eca584b6b89269b1125ddcbd585f290213), README.md (sha256 0958ff5b127694c07a5d4a3b3615c1cb1dda95eabd82095cee82c3d2f32ea5ed), tasks_asymmetric.json (sha256 e8cf8512b5a80b1b8939ff785cf7c5080525b79ac28067ef134373ee3d22648a).
- Campaign state marking benchmarks unexecuted: occ3-campaign/v3-controlled-deploy/STATE.json (sha256 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. Benchmark runs on the deployed runtime with the extension installed and OCC-3 enabled for eligible same-model pairs.
2. Control-plane baselines (OCC-1/OCC-2/text) over the same tasks.
3. Raw logs, per-task outcomes and the analysis artifacts produced by the harness.

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
