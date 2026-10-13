# lp_118 fresh-arm verdict — cue-backed fresh re-ingest (round 4)

Worker: glm-lpfresh (LP fresh-arm owner). Branch `improve/mem4-lp-fresharm`.
All judging on DGX GLM-5.3 only (`dgx-glm53/glm-5.3`), no Jev.

## TL;DR

**Fresh re-ingest alone does NOT fix lp_118: score 0.0 (official-lp-v1).**
The ingest-side cue fix works at scale, but the question-side bridge still
starves, and the answer narrated. The fix path remains the committed budget
raise (2048/2048/4096; glm-binary-regate rebuild in flight), optionally with
the no-narration addendum for answer-side robustness.

## Run

- `lpq118_v4A_fresh2`: full 25-session re-ingest + question, 27/27 jobs,
  101 min, binary `imp4-targetA` (cue-fixed; predates the budget raise),
  manifest `lpq118-scratch/manifests/lpq118_v4A_fresh2.json`.
- Restart provenance: the original `lpq118_v4A_fresh` (owner glm-lpq118)
  crashed at 9/27 jobs when its owner session ended (the driver was a
  session-owned bash child; the temporal-authority capture protocol forbids
  resuming partial output). fresh2 was launched detached via WMI
  Win32_Process.Create (parent WmiPrvSE — survives session reaps), with a
  private models.json copy after a transient os-error-3 on the shared c06
  path. The crashed partial (87.5% cue coverage at 9/27 jobs) is preserved
  as evidence; the restart lost 9 applied jobs (~40 min).

## Evidence

| measurement | fresh2 | frozen v3 |
| --- | --- | --- |
| store entries | 734 | 572 |
| entries with prospectiveCues | 702 (95.6%) | 91 (15.9%) |
| gold in store | yes, 5 entries, 5 cues each | yes, 3 entries, 0 cues |
| gold recalled (top-20 window) | **no** | no |
| gold in top-50 lexical ranking | **no** (top score .774, rank-50 .39) | no |
| distillation | empty 64/64, stop=length | empty 64/64 |
| expansion | empty 256/256, stop=length | empty 256/256 |
| rerank | empty 2048/2048, stop=length, input 5758 | empty 512/512 |
| answer | narration ("I'll first check the workspace...") | narration |
| score (official-lp-v1) | **0.0** | 0.0 |

Gold cue quality was high ("cap-and-gown picture still on his mantel",
"how Evan's boy is settling into campus life after all the November
excitement") — but the question ("It's strange not having him around the
house; it's so quiet now") shares only the token "Evan" even with the cues in
the lexical index, so store-level cues alone cannot bridge this implicit
reference. The bridge must come from the query-side helpers
(distillation/expansion reformulation, or rerank over the top-200 pool), and
all three still emit zero visible characters: GLM-5.3 hidden reasoning
exhausts each budget (64/256/2048 in this binary).

## Residual classification

Two independent misses, both known:

1. **Gold still not recalled** — helper starvation at the imp4-targetA
   budgets (the budget raise 2048/2048/4096 is committed but not in this
   binary). Prior evidence: whenever rerank emitted output (1166/1292/1773
   tokens used) it selected the gold entries and the run scored 1.0
   (base_r2, base_r3).
2. **Answer-side narration** — the stochastic G-class failure also seen in
   v3 (1/6); the nonarr addendum suppresses it and showed no regressions
   (lp_18/lp_159/lp_69 all 1.0), but is a separate axis from retrieval.

## Recommendation for the LP full-run launch

Do not launch on the fresh-re-ingest arm alone. Either wait for the
glm-binary-regate rebuild (raised budgets) and re-run this arm — expected to
convert given the base_r2/r3 precedent — or launch with the frozen stores +
raised budgets. The store-side cue work (95.6% coverage) is validated and
makes the retrieval path strictly better; it is necessary but not sufficient
for lp_118's phrasing.

## Artifacts

- `lpq118-scratch/runs/lpq118_v4A_fresh2/` — run.jsonl, scores-official-lp.json, 734-entry store
- `lpq118-scratch/lpq118_v4A_fresh2.log` — driver log (complete)
- `lpq118-scratch/runs/lpq118_v4A_fresh/` — crashed original partial (evidence)
- Ledger: `reports/continuation/iteration4/QUESTION-LEDGER.json` → entries[lp_118].round4.fresh_reingest_arm
