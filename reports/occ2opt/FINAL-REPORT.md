# OCC-2 Efficiency Campaign — Final Report (occ2-efficiency)

Branch improve/occ2-efficiency on main 1863b5dea (v0.1.33). All validation DGX GLM 5.3 only.

## Objective
Make OCC-2 beat OCC-1 on end-to-end cost (total tokens, latency) with no task-success regression;
fix codebook repetition, daemon compat, strike-ledger wiring, dialect selection.

## VERDICT: ACHIEVED

| Metric | OCC-1 | OCC-2 | Delta |
|---|---|---|---|
| Dev (60 items) wire tokens/item | 144.17 | 88.57 | 0.6143x (-38.6%) |
| Holdout (54 items, sealed) wire tokens/item | 144.30 | 87.52 | 0.6065x (-39.4%) |
| Warm prompt tokens/codebook injection | 2x codebook | 1x + 1-line refs | -40% |
| Break-even | - | ~21.5 items/injection | session-profile amortization recommended |

Quality (both splits): speaker strict fidelity 60/60 + 54/54; listener QA 180/180 + 162/162;
comprehension 60/60 + 54/54; canaries 12/12 + 12/12 no drift; faults 0; child_lost 0; gates GREEN.

## The four named fixes
1. CODEBOOK REPETITION: amortized codebook injection (identity = dialect+version+sha prefix; presence
   from live message-list scan; compaction/restart-correct). -40% warm prompt tokens.
2. DAEMON COMPAT: /occ daemon-owned preferences now reachable end-to-end: the missing supervisor
   DAEMON_COMMAND_TYPES registration (110->112) was the root cause of both live-test failures; fixed
   with a contract-test pin. 5/5 green.
3. STRIKE LEDGER: wired into the production delivery path (decay-applied pair incidents; send-side
   selection refuses struck pairs). occ2_selection_wiring 7/7.
4. DIALECT SELECTION: deterministic SelectionPolicy over measurable context (listener capability,
   evaluations, strikes, size, codebook presence); production default stays OCC-1 until a dialect
   carries a validated record; occ2_selection_policy 9/7 green.

## Dialect (sym-v2-d2-r1)
Frozen card with clarification (CARD-CLARIFY.md): canonical line order, symbol alphabet under
never-compress, bare index links, epoch36 TS. Wire tokens 87.81 tok/item avg = 0.6136x across corpus.
Card-text-only fix moved live speaker fidelity 0/60 -> 60/60 (run a vs run b, wire bytes identical).

## Combined gates (quiesced tree, COMBINED-GATES.md)
6/6 integration binaries (76/0), lib filters clean (occ 251, communication 16, agent_messages 27,
session 531/1/2 with one pre-existing flake), python codec 44/44, build PASS (+26 warnings vs baseline,
~7 campaign-attributed), clippy non-gating (not in CI). No OCC-owned regression.

## Evidence bundle
- reports/occ2opt/: 20 reports incl. COMBINED-GATES, LIVE-EVAL-RUN-B (dev), LIVE-EVAL-HOLDOUT,
  PR-BODY-DRAFT, CARD-CLARIFY, DIALECT-V2, EXPERIMENT-LEDGER (all runs + incidents).
- gates/: lane logs + combined_* provenance-headed logs.
- Runs: R2b (dev, run id live-dev-sym-v2-d2-r1-scripted-20261016T162253Z), R2H (holdout,
  live-holdout-sym-v2-d2-r1-scripted-20261016T175430Z) - full prompts/replies/manifests/summaries.
- Evaluation records: research/occ2opt_eval/occ2-evals-run-b.json (dev + holdout, schema
  optimus.occ2-evals.v1).
- Reproduce: python research/occ2opt_eval/driver.py --self-test (178/178); stage commands in the run dirs.

## Incidents (documented, recovered, evidence archived)
Two dry-run contamination events on the holdout run dir (driver dry-run rewrites replies/*.jsonl
wholesale); both caught, archived under R2H/replies/archive-dryrun-contamination-*, no live data mixed.
Harness fixes #1-#6: driver-side scoring/spec clarifications only; model output never edited.

## Deferred (honest)
Heterogeneous model-pair matrix (X1-X4) under DGX-only; labeled in all reports.
