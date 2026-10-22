# OCC-3 DGX Deployment

**Status: OFFLINE CLOSURE COMPLETE - LIVE DEPLOYMENT PENDING.** No successful deployment exists. The offline gates are closed (final offline verification 16/16 checks PASS, 2026-10-21T23:32Z); the disposable recovery rehearsal is complete after four rounds (final round 8/8 scenarios with every recovery verified, production untouched); two live windows have each aborted pre-mutation at a distinct image-build guard with zero production impact (window 1 at the package-tree bound, TS2 at the apply-guard directory-identity check), and both repairs are committed (`ea9b9e509`, `973b191b5`) with window TS3 pending the second repair review. The two 2026-10-21 attempts failed with verified root causes and were manually recovered. This report makes no live success claims; the deployment narrative must be completed from the record of a real, verified deployment.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-22 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative (branch `feat/occ3-generic-sglang-bridge`, head `973b191b5`). Full hashes in the evidence register. STATE.json and ROOT-HANDOFF.md are living campaign documents; their inline digests are the values at this refresh.

## Deployment status (from evidence, 2026-10-22)

- Offline closure complete: final offline verification 16/16 checks PASS - worktree freeze 143/143 hash-verified, gate-evidence sha256 pins, rehearsal-4 8/8 pinned, producer re-issue pinned, repo `bash scripts/check.sh` 282 tests, cargo workspace two-stage, occ3 focused sets (7 targets), occ3 examples, extension pytest, terminal six pinned suites (350), `test_repl` (110 ran, ok, 3 skipped) and `occ2_e2e_negotiation` (2 passed) under the p4 kernel environment, worktree unchanged after the suites [E-FV]. The verified content landed as commits C10-C23 (tip `4ae80950d`); on top: the window-1 repair `ea9b9e509` (C24), the docs-only reports refresh `63b2aba9e` (C25) and the window-2 repair `973b191b5` (C26, current branch tip) [E-STATE, E-RH].
- Disposable recovery rehearsal: four rounds on the DGX Docker 29.6.2 daemon with throwaway fixtures only. Rounds 1-3 each aborted deterministically at a newly found controller defect (CD-1/CD-2, CD-3, CD-4; history and repairs in ROLLBACK.md section 2.3); round 4 (result 2026-10-21T22:14Z) completed 8/8 scenarios with every recovery verified, zero residue, and the production containers identical before and after [E-RH4].
- Live window 1 (TS=20261022T0030Z): phase-0 preconditions all PASS (including an authenticated `GET /v1/models` 200 with credentials resolved via env references, never printed); the derived-image build failed at the package-tree guard (`package_tree_file_oversize`: the 2 MiB per-file source bound vs four base-image compiled `.so` files of 3,278,632-15,956,840 B) and the executor aborted pre-mutation per the runbook: no candidate container, no supervisor, no authorization consumed, no rollback events, health 200 before and after, production untouched [E-LIVE1].
- Live window TS2 (TS=20261022T0145Z): phase-0 preconditions all PASS (GO verified against the window-1 REPAIR-REVIEW ACCEPT; 24/24 digest pins; worktree `NO_DRIFT` at `63b2aba9e`); P1.1-P1.4 PASS (upload 52/52 byte-exact, base tag pinned to the exact original base image); P1.5 build failed at the apply guard - receipt `package_package_apply_failed_discard_tree`, inner check `source_directory_identity_drift`: docker overlay2 directory copy-up collapses merged-view `st_nlink` on the 9 patched-file parent directories while dev/ino/mode/uid/gid stay stable (window-1's oversize guard was RESOLVED by `ea9b9e509`; this is a new downstream defect). The executor aborted pre-mutation per the runbook: no candidate container, no supervisor, no rollback events, health 200 before and after, production untouched; window-1's report preserved byte-identical when the canonical path was reused [E-LIVE2].
- Both build-guard defects surfaced only at the real docker image build on the DGX: the offline image-build closure had recorded the runtime limit "No image build", plain offline filesystems preserve directory nlink across temp create/remove, and the rehearsal fixtures were created via docker create and never ran `native_image_build` [E-LIVE1, E-LIVE2].
- Window TS3: pending the independent second-repair review ACCEPT for commit `973b191b5` (directory identity pins dev/ino/mode+tail, excludes nlink, wholesale replacement still caught; apply() receipt codes now propagate; 2 regression tests + 5 expectation updates; extension suite 1415 passed / 324 skipped / 77 subtests); upload-manifest-v4 re-pinned (46 files, only native_package.py changed) and dispatch digests updated; executor-3 dispatched in WAIT-GO [E-RH, E-UM4, E-DD].
- Live attempts under the current approval: 2 (both pre-mutation aborts - window 1 at the package-tree guard, TS2 at the apply guard); the one authorized controlled swap remains unused [E-STATE, E-LIVE1, E-LIVE2].

## Planned outline

1. Target environment summary (pointer to DGX-ENVIRONMENT.md).
2. Preconditions: offline regressions and disposable recovery tests green; fresh one-attempt authorization; source attestation; ingress-reload approval.
3. Artifact pipeline: derived image / native package staging, artifact probe (CPU-only, same mounts), parity verification.
4. The one controlled swap: candidate created stopped, element-wise parity, mutation, exit-aware startup, service proofs (health, authenticated chat, tools, ingress), the seven supervisor gates, external native proofs supplied twice.
5. Verification of the deployed extension (routes, identity, capabilities) and ordinary GLM inference non-regression.
6. Rollback path (pointer to ROLLBACK.md) and failure history.
7. Exact deployed extension version and image digest.

## Evidence already available (offline)

- Verified environment and launch configuration: occ3-campaign/dgx-inspect/ENVIRONMENT.md (sha256 e0667968a38430e9359084242fe0553f0571fad2cdca4a4a4f4a08a345e0afb7) and occ3-campaign/v3-controlled-deploy/original-sanitized.json (sha256 b1e15312a042f39bb2620139b7de5251517f242feb46bc571cf81fbb1cae89f4).
- Deployment tooling and procedure: extensions/sglang-occ3/deploy/README.md (sha256 c363a736f85530156553f42fb7bfff6b506c085e8dd5d305f4f71f0479d102c1); v3 controller/supervisor/lifecycle (see ROLLBACK.md register).
- Honest failure history of both 2026-10-21 attempts: occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md (sha256 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9); occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md (sha256 5e0a9c06f4c9a5f8e91322fcb20258964a753e333d5d00351f0aef68390b80a7).
- Offline dry-run packaging (not applied/installed): occ3-campaign/v3-controlled-deploy/root-native-package-dry-verify.json (sha256 151494f9619fdf21a957ac74792a8fbbed0696b2d71b71cbfc69c118a4132d51).
- Campaign authorization boundary: occ3-campaign/v3-controlled-deploy/AUTHORIZATION.txt (sha256 aa13cd2c99822f17cb142b6e1c04158fd0728a421244a64175ac53afc8d82c03).
- Final offline verification (16/16 checks PASS): occ3-campaign/v3-controlled-deploy/FINAL-VERIFY-RESULT.json (sha256 8d54c074ee3120fae92049dd5f07fd3dd60ed0bba7aa0634f5577ac9d21aad73).
- Disposable rehearsal rounds (final round 8/8, production untouched): occ3-campaign/v3-controlled-deploy/REHEARSAL-RESULT.json (sha256 40698dcad02f1fa4c35f3605a900f8bf84aae3680412fa2c3d4e5b2ffc362dca), REHEARSAL-RESULT-2.json (sha256 00269632eee9635808d75cc2d96e576561a4f0c56d1c806fe8f25b33620e7050), REHEARSAL-RESULT-3.json (sha256 7f2784cb6473ed2dc8aaa5e6e96bf43983ac22646a82a86914c6f2499a3d59bc), REHEARSAL-RESULT-4.json (sha256 1605fca0590d099b8d4b44301e5be3feb11ce90050ecb6ad9342c9127dbe910a).
- Live window-1 abort evidence (pre-mutation, zero production impact, no rollback events): occ3-campaign/v3-controlled-deploy/LIVE-PHASE-EXECUTION-REPORT-20261022t0030z.json (sha256 ce877c412649c488d8c72290cd47d7492863e3db4640f0c176bfd54d76219be8; preserved byte-identical when the canonical path was reused for TS2).
- Live window TS2 abort evidence (pre-mutation, zero production impact, no rollback events, disposable-probe isolation of the root cause, evidence bundle sha256 fd70a314db23f2b23978df76682002d598b6a0b01f903171d2a465d5d0930a3d): occ3-campaign/v3-controlled-deploy/LIVE-PHASE-EXECUTION-REPORT.json (sha256 dc7ae197ab24e22dc318144644934efb415cf9b84e722f715767d6847b9b0f5e).
- Phase-suite producer re-issue (offline selftest passed): occ3-campaign/v3-controlled-deploy/PRODUCER-RESULT.json (sha256 699901dd9bb557915d2a0549d02d2d2327796e5dd12c0b39347ee93fa431ae73).
- Dispatch digests with the `ea9b9e509` and `973b191b5` repair blocks and the re-pinned upload manifests (digest-verified on the DGX): occ3-campaign/v3-controlled-deploy/DISPATCH-DIGESTS.json (sha256 d53c8c2b336a7e00d953e8986884c50713447c41992255475b294e2e2ed6f9e8).
- Re-pinned upload manifest v4 for window TS3 (46 files; only native_package.py changed vs v3): occ3-campaign/v3-controlled-deploy/upload-manifest-v4.json (sha256 c9981e9d910286b5f5a87f3d9ffd8ad09ba156946e570b9f4bd7b687c47bacd1).
- Root handoff (window-1 adjudication, TS2 execution and abort, repair `973b191b5` record, TS3 gating): occ3-campaign/v3-controlled-deploy/ROOT-HANDOFF.md (sha256 1b3aacc1453ec4739f0aaca9e6eb36cd54841ce776c3d7b9ae693eb66468f722; living log, digest at this refresh).
- Campaign state (gate statuses; `live_window.ts2.attempts_total=2`, `production_swap` NOT_STARTED): occ3-campaign/v3-controlled-deploy/STATE.json (sha256 4a73ae642299cf3e0a02c62c40344cc6e140a1770a907311128b2d37827c77ff; living file, digest at this refresh).

## Evidence needed before the deployment narrative can be completed (PENDING LIVE EVIDENCE)

1. A successful controlled swap under the current approval: journal, parity artifact, supervisor gate records, native proof receipts.
2. Live `/occ3/v1/status` 200 output with the exact plugin version and image digest.
3. Post-swap verification artifacts: ordinary GLM inference, radix cache behaviour, ingress path.

The disposable recovery rehearsal precondition that must precede the swap is satisfied (round 4: 8/8 scenarios, every recovery verified, production untouched [E-RH4]); the remaining precondition chain for window TS3 is the independent second-repair review ACCEPT for commit `973b191b5` [E-RH].

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256.
