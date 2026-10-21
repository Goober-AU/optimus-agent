# OCC-3 DGX Deployment

**Status: STUB — awaiting live evidence.** No successful deployment exists. Both 2026-10-21 attempts failed with verified root causes and were manually recovered; the campaign state records zero live attempts under the current approval and `production_swap` NOT_STARTED. This report must be written from the record of a real, verified deployment.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Target environment summary (pointer to DGX-ENVIRONMENT.md).
2. Preconditions: offline regressions and disposable recovery tests green; fresh one-attempt authorization; source attestation; ingress-reload approval.
3. Artifact pipeline: derived image / native package staging, artifact probe (CPU-only, same mounts), parity verification.
4. The one controlled swap: candidate created stopped, element-wise parity, mutation, exit-aware startup, service proofs (health, authenticated chat, tools, ingress), the seven supervisor gates, external native proofs supplied twice.
5. Verification of the deployed extension (routes, identity, capabilities) and ordinary GLM inference non-regression.
6. Rollback path (pointer to ROLLBACK.md) and failure history.
7. Exact deployed extension version and image digest.

## Evidence already available (offline)

- Verified environment and launch configuration: occ3-campaign/dgx-inspect/ENVIRONMENT.md (sha256 e0667968a38430e9359084242fe0553f0571fad2cdca4a4a4f4a08a345e0afb7) and original-sanitized.json (sha256 b1e15312a042f39bb2620139b7de5251517f242feb46bc571cf81fbb1cae89f4).
- Deployment tooling and procedure: extensions/sglang-occ3/deploy/README.md (sha256 c363a736f85530156553f42fb7bfff6b506c085e8dd5d305f4f71f0479d102c1); v3 controller/supervisor/lifecycle (see ROLLBACK.md register).
- Honest failure history of both attempts: occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md (sha256 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9); occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md (sha256 5e0a9c06f4c9a5f8e91322fcb20258964a753e333d5d00351f0aef68390b80a7).
- Offline dry-run packaging (not applied/installed): occ3-campaign/v3-controlled-deploy/root-native-package-dry-verify.json (sha256 151494f9619fdf21a957ac74792a8fbbed0696b2d71b71cbfc69c118a4132d51).
- Campaign authorization boundary: occ3-campaign/v3-controlled-deploy/AUTHORIZATION.txt (sha256 aa13cd2c99822f17cb142b6e1c04158fd0728a421244a64175ac53afc8d82c03).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. A successful controlled swap under the current approval: journal, parity artifact, supervisor gate records, native proof receipts.
2. Live `/occ3/v1/status` 200 output with the exact plugin version and image digest.
3. Post-swap verification artifacts: ordinary GLM inference, radix cache behaviour, ingress path.
4. The disposable recovery rehearsal results on the DGX that must precede the swap.

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
