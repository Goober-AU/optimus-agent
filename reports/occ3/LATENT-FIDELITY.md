# OCC-3 Latent Fidelity

**Status: STUB — awaiting live evidence.** No semantic-fidelity measurement exists. The specification forbids claiming a latent transfer is semantically useful until the receiver proves it; the extension README states the same proof boundary. This report requires benchmark results that do not yet exist.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Fidelity definition: task-relevant information transfer measured at the receiver, not token or tensor movement.
2. Verification signals available by design: cached_tokens hit confirmation, request-bound consumption receipts.
3. Benchmark design: same-model OCC-3 vs text baseline under matched conditions; receiver-only-facts tasks (receiver must not have the facts in its own context).
4. External fidelity evidence and its limits (OLMo3 55.1% cross-model retention; AVP quality-gate collapse with prompt length) as motivation for same-model-only design.
5. Results tables and failure analysis (spec GATE 9).

## Evidence already available (offline)

- Research synthesis of fidelity evidence and its fragility: occ3-campaign/research/latent-methods.md (sha256 be2ddecfdc855bfcc7aa1fbe59dec091b136424f4435af97caa5f059ec82b749).
- Proof-boundary statement (a cached_tokens count or HTTP 200 cannot establish semantic use): extensions/sglang-occ3/README.md (sha256 3fe79fadd0a9e5e55cac27e1feca1ee7431d71ec53579f141a920ac9bd454702).
- Receipt contract (what a receiver-side proof can and cannot assert): occ3-campaign/v3-controlled-deploy/NATIVE-INFERENCE-ARCHITECTURE.md (sha256 0afb026dbf2d6329fd4c50b03265bd46a28b647c12881b063c42f2ea64087651).
- Offline benchmark harness (no results yet): extensions/sglang-occ3/benchmarks/ (bench_occ3.py sha256 42f892fb9da44da99334183b6b79c4f1f50817519e471c7e3818c12458c170b2).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. Live OCC-3 vs text-baseline benchmark results on the deployed runtime (receiver-source-facts-absent design).
2. Per-task success deltas with statistical treatment and the raw run logs.
3. Fidelity under full-MAX reasoning budgets (131072 default output tokens) — current bounded handling does not prove compatibility (occ3-campaign/v3-controlled-deploy/STATE.json, sha256 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d).

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
