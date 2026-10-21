# OCC-3 Experiment Ledger

**Status: STUB — awaiting live evidence.** The campaign has produced many offline experiments and reviews, but the consolidated ledger of GPU-backed experiments with results does not exist yet: no live experiment has been run under the current approval. This ledger must enumerate every experiment (offline and live) with its artifact, command, result and disposition.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Ledger format: experiment id, hypothesis, method, artifacts (with sha256), result, disposition (accepted/rejected/superseded), date.
2. Offline experiments to enumerate: kernel provenance A/B (focused Rust + Python provenance), terminal server v2 red runs, validator encoding defect repro/fix, source attestation A/B, control-headers FA closure, lifecycle L01-L12 worker iterations.
3. Live experiments (none yet): disposable recovery rehearsal, controlled swap, latent channel verification, benchmarks.
4. Cross-references to BUG-LEDGER.md and REGRESSION-GATES.md.

## Evidence already available (offline)

- Kernel provenance A/B: occ3-campaign/v3-controlled-deploy/kernel-provenance-ab-focused-rust.log (sha256 af08e62a692d610d23c489c6be57940ed3c1404e2833ee62040242c8f1d4f8ca) plus kernel-provenance-ab-python-provenance.log (sha256 ee9061420e1dafdfe026371535a8d4b00980acc95fec612aea0e3361558db577).
- Terminal server v2 red runs: occ3-campaign/v3-controlled-deploy/terminal-v2-red-run1.log (sha256 b3ff0943ca92eb52a08c7e6bd80caef5d04a0c9b78db55dca77f51ac744cadb6) and terminal-v2-red-run2.log (sha256 ba3832668292612f84a2d416b0f5af536e3fd3b5d2227ae5664abc17c5fc0cfc).
- Validator encoding defect repro/fix: occ3-campaign/v3-controlled-deploy/native-validator-encoding-defect-repro.log (sha256 a2827d372915c226aa05acd4d21e9bf01a2c08d7daf5b99ce40c32c74ec4608e) and native-validator-encoding-defect-fixed.log (sha256 3d9544e142dc9bd0109961a5336d2db68e2b1bf6bb2c381c27e45602dbd49234).
- Source attestation v2 and comparison: occ3-campaign/v3-controlled-deploy/source-attestation-result-v2.json (sha256 0ae4671372518c9fadb131881f55a2628f501d8555eee18f870186de021d1b53).
- Campaign state with worker/result inventory: occ3-campaign/v3-controlled-deploy/STATE.json (sha256 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. The full experiment list with per-experiment artifacts and hashes (largely enumerable offline, but each live experiment adds its own row).
2. Live GPU experiments: disposable recovery, controlled swap, latent channel verification, fidelity and benchmark runs — none exist yet.

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
