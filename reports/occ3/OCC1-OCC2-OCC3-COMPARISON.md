# OCC-1 vs OCC-2 vs OCC-3 Comparison

**Status: STUB — awaiting live evidence.** The comparison requires measured benchmark results under matched model and task conditions (spec GATE 10). Only the architectural comparison and the offline selection economics exist today.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Architectural comparison: one typed contract, three encodings (text-structured, symbolic, latent); where each sits in the send seam.
2. Capability and negotiation: OccCapability dialect negotiation vs Occ3CapabilityAdvertisement identity matching.
3. Selection economics: OCC-2 token amortisation model vs OCC-3 handle/reference model; message size bounds.
4. Measured comparison: wire size, tokens, latency, task success under matched conditions.
5. Fallback ladder: OCC-3 -> OCC-2 -> OCC-1 -> text; strike participation.

## Evidence already available (offline)

- OCC family architecture map: occ3-campaign/research/arch.md (sha256 616d1e2e77cc80548282001af5634edf1d963adf95216591d44ac5933b56f001).
- Transport integration and bounds: occ3-campaign/research/daemon-messaging.md (sha256 23bb1a85eaf0c9b3cfcd6962127400e9d464cf77452b86913ddfb8cffb1c8a25).
- OCC-2 selection policy and measured constants (worktree): crates/pi-coding-agent/src/core/occ/selection.rs (sha256 19122d325a35bb2292a6127e19835873e4f296db2b22d55e9744c90704eb2a63); prior campaign reports under reports/occ2opt/ (directory, see that campaign's own evidence).
- OCC-3 negotiation ladder: crates/pi-coding-agent/src/core/occ/occ3/negotiation.rs (sha256 1956cd91398fa334cf553b4de0cbbfb285b2aca3010e0915c7c856b9cc83a8e7).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. Matched-condition benchmark outputs for OCC-1, OCC-2 and OCC-3 on the deployed runtime.
2. Task-success and token/latency deltas per protocol with raw logs.
3. Decision-quality analysis: when the latent channel wins or loses against the symbolic and text baselines.

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
