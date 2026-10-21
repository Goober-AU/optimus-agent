# OCC-3 Bug Ledger

**Status: STUB — awaiting live evidence.** Individual defects and their fixes are recorded across campaign artifacts, but no consolidated ledger exists. A complete ledger must enumerate every defect found during the campaign with root cause, fix evidence and regression coverage; several late-campaign items (terminal v2, private-IO O2) are recorded as unaccepted or interrupted and must be carried forward honestly.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Ledger format: bug id, component, symptom, root cause, fix (artifact + sha256), regression test, status (fixed/unaccepted/interrupted).
2. Deployment defects: empty Cmd argument (docker 29.x trailing blank line); mount normalisation from NetworkSettings.Mounts; HTTP-only health wait; no automatic restoration.
3. Validator defects: UTF-8/encoding defect (repro + fix logs); robustness repairs.
4. Terminal server: lifetime repairs v1/v2; red-run findings.
5. Kernel/provenance: null-presence repairs; retained negative stop-seam; created-at retention decision.
6. Protocol/scope: O2 private-IO adjudication; stale ACL pointer, cap edge, error timing candidates (review interrupted, unaccepted).
7. Carry-forward list for the live phase.

## Evidence already available (offline)

- Deployment failure analyses (both attempts, root causes and remediation requirements): occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md (sha256 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9); occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md (sha256 5e0a9c06f4c9a5f8e91322fcb20258964a753e333d5d00351f0aef68390b80a7).
- Validator encoding defect: occ3-campaign/v3-controlled-deploy/native-validator-encoding-defect-fixed.log (sha256 3d9544e142dc9bd0109961a5336d2db68e2b1bf6bb2c381c27e45602dbd49234); repro log (sha256 a2827d372915c226aa05acd4d21e9bf01a2c08d7daf5b99ce40c32c74ec4608e).
- O2 adjudication (unaccepted review findings): occ3-campaign/v3-controlled-deploy/O2-ADJUDICATION-REPORT.md (sha256 cfc00bde6352b92f182de504805f4fa392db26e0707483c423c8b2f1d908ae0a).
- Safe-stop record of interrupted/unaccepted items (terminal v2, private-IO O2): occ3-campaign/v3-controlled-deploy/USER-SAFE-STOP.json (sha256 6adcf0c498365c5cb4ec2700045b2aa292f04cf22d5648507aefc0888879fc4a).
- Campaign state review-hold lists: occ3-campaign/v3-controlled-deploy/STATE.json (sha256 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. Consolidation pass over the campaign artifacts to enumerate every defect with fix evidence and regression coverage.
2. Live-phase defects discovered during disposable recovery, the controlled swap and benchmark runs (none yet).

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
