# LoCoMo Regression Gate v2 — Temporal-Discipline Binary (imp3)

**Campaign:** optimus-memory-bench-20261007-040424 (continuation / iteration3-inputs)
**Worktree:** `imp3-gate` @ branch `improve/mem3-gate2`, commit `bdffcad50`
**Date:** 2026-10-12
**Status:** COMPLETE — verdict: **NO REGRESSION (slight improve, within noise)**

## Purpose

The PR adds temporal discipline to memory core (recall "at" date labels, latest-wins
tie-break, header guidance; commit `955aafde2` on the imp3 lineage). This gate re-runs
the iteration-2 LoCoMo 300-q dev regression baseline with ONLY the binary changed:
same question set, same shard partition, same settings (improved-defaults arm, all
recall helpers off — pure defaults), same scoring.

## What ran

| Shard | Envs | Questions | Status |
|---|---|---|---|
| improved-s0 | conv-26, 42, 47, 50 | 129 | complete (18:05 UTC) |
| improved-s1 | conv-30, 43, 48 | 90 | complete (17:05) |
| improved-s2 | conv-41, 44, 49 | 81 | complete (17:02) |

- Binary: `imp3/target/release/examples/memory_bench.exe` sha256 `ab1e1474...`
  (commit `bdffcad50`, clean tree, built after the last source change) vs anchor
  binary `4ece283e...` (commit `7997601778`, improve/memory-iteration-2 gatebuild).
- Model: dgx-glm53 / glm-5.3, no Jev (verified per-shard in preflight).
- Settings: byte-identical to iteration-2 improved-defaults (recallOn=true,
  memoryLearning=false, maxExtractionTokens=32000, maxImportChunkChars=15000,
  disableTools=true, evidenceTimestamps=true, no distill/rerank/extract/expansion/prospective).
- Question set: identical 300 qids (declarations sha `9e88a6fd...`, same as iteration-2).
- Scoring: deterministic local token-F1 (`optimus-local-scoring/1.0.0`), no judge, no network.
  Anchor re-scored with this exact code reproduces the published numbers (0.2039/0.2626).
- Timeline: launched 13:30 UTC, ingest ~29-71 min/env, questions ~25-70s each; all done by 18:05.

## Result (vs iteration-2 anchor, binary 4ece283e @ 7997601778)

| Metric | Anchor (it2) | Gate v2 (imp3) | Delta |
|---|---|---|---|
| **cats 1-4 mean F1** | **0.2626** | **0.2766** | **+0.0141** |
| cats 1-4 acc@0.5 | 21.46% | 23.61% | +2.15pp |
| overall mean F1 | 0.2039 | 0.2148 | +0.0109 |
| overall acc@0.5 | 16.67% | 18.33% | +1.66pp |

Per category (mean F1):

| Category | Anchor | Gate v2 | Delta | n |
|---|---|---|---|---|
| 1 multi-hop | 0.1610 | 0.1307 | -0.0303 | 43 |
| **2 temporal** | 0.2279 | **0.2485** | **+0.0206** | 48 |
| 3 open-domain | 0.1616 | 0.2806 | +0.1190 | 15 |
| 4 single-hop | 0.3220 | 0.3361 | +0.0141 | 127 |
| 5 adversarial | 0.0 | 0.0 | 0.0 | 67 |

Statistics: paired per-question delta on cats 1-4: mean +0.0140, se 0.0210, t=0.67,
p≈0.50 (not significant). No single category difference is significant (cat-1 p≈0.48,
cat-2 p≈0.69, cat-3 p≈0.12 on n=15, cat-4 p≈0.62). cats1-4 acc Wilson95 overlap
heavily: anchor [0.167, 0.272] vs gate-v2 [0.186, 0.295].

## Interpretation

- **The gating requirement (no regression) holds**: every gating metric moved up.
  Memory lift vs the nomem floor (0.0073) is preserved at ~38x.
- **Temporal (cat 2) moved up** (+0.0206) as hoped from latest-wins tie-break + at-labels;
  the change is in the expected direction but within noise (p≈0.69).
- **Multi-hop (cat 1) moved down** (-0.0303, p≈0.48): not significant, n=43. Watch it
  in the next gate; the date-label tokens could be crowding short recall windows.
- **Open-domain (cat 3) +0.119**: seductive but n=15 — a single question flips ~0.067.
- Cat 5 remains non-scoring (answer-form mismatch, documented in the iteration-2 gate;
  both arms answer "(b) <text>" which the strict scorer rejects).

## Run-quality notes

- 6 flagged rows (iteration-2 had 0): five transient DGX 503 provider errors
  (conv-30_q66, conv-30_q93, conv-43_q3, conv-41_q170, conv-44_q157) and one
  8192-token length-cap row with an empty visible answer (conv-49_q4; the anchor
  scored that qid 0.0 as well). 3 of 5 are cat-5 (0 in both arms); the 2 cats1-4
  rows carried anchor F1 0.083/0.05. Worst-case cats1-4 distortion < 0.001 — far
  below the observed +0.0141. No retry was performed (documented instead).
- One ingest-time 503 on s2/conv-41 was auto-retried by the driver (importRunRetries).

## Files

- `manifests/locomo-gate3-improved-{s0,s1,s2}.json` — gate manifests
- `runs/improved-{s0,s1,s2}` + `runs/improved-merged` — run data (untracked)
- `scores/improved-defaults.json` — official gate-v2 score (sha256 `e6573244...`)
- `scores/anchor-rescore.json` — anchor re-scored with this bench code (identical to published)
- `GATE-RESULT.json` — machine-readable result
- `sources.json`, `launch_shard.py`, `score_gate.py`, `status.py` — provenance + tooling

**Gate decision: PASS — no regression on the LoCoMo dev gate; temporal category improved (+0.0206 F1), overall +0.0109, cats1-4 +0.0141, none statistically significant; the PR requirement "no regression" is satisfied.**
