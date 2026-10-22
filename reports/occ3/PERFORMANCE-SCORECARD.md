# OCC-3 Performance Scorecard

**Status: STUB — awaiting live evidence.** No production performance measurements exist: the extension has never run on the live server, so no pre/post GPU memory, throughput or latency comparison can be reported.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Baseline capture: GPU memory, throughput, latency of the original serving configuration (already snapshot-ready).
2. With-extension measurements under identical load.
3. Latent-channel overheads: capture time, handle handoff time, consume time (Occ3TransferMetrics fields), state sizes.
4. Budget compatibility: full-MAX reasoning turns vs bounded response handling; 131072 default / 262144 ceiling.
5. Spec GATE 11 verdict with evidence.

## Evidence already available (offline)

- Verified baseline environment and GPU commitment: occ3-campaign/dgx-inspect/ENVIRONMENT.md (sha256 e0667968a38430e9359084242fe0553f0571fad2cdca4a4a4f4a08a345e0afb7).
- Budget feasibility analysis (context window 1048576, max_tokens 262144, default 131072; parser bound hazard noted): occ3-campaign/v3-controlled-deploy/NATIVE-BUDGET-FEASIBILITY.md (sha256 7968ac192dfef58ae22a13bd2208261d6fec0705f241ca8412cfad4db7c6d67e).
- Metrics fields defined for transfers: crates/pi-coding-agent/src/core/occ/occ3/types.rs (sha256 336cdcd4bd6005f8ca2a80365ad4c77f9a2a7b8b73b54941584092056566f79a).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. Pre/post-swap GPU memory and throughput measurements on the DGX.
2. Live capture/handoff/consume timings from real OCC-3 transfers.
3. Full-turn time-to-first-byte behaviour for attached (buffered) vs ordinary requests under MAX reasoning.

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
