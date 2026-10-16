# OCC-2 + OCC-3 Campaign Gap Audit (campaign-close)

Worker: campaign-close (gap audit + holdout check + workspace hygiene).
Date: 2026-10-16. Worktree: `C:/Users/openclawuser/optimus-occ23-20261016`
(branch `improve/occ2-occ3-communication`, base v0.1.32 `ce8480d59`).

Scope and method: this audit read only the campaign-owned trees
(`research/tasks/`, `research/taskbench/`, `research/latent_handoff/`,
`research/symbolic_comm/`, `research/benchmarks/`, `research/integration/`,
`reports/occ23-campaign/`, the root `.gitignore`, and `git status` output).
No repository-wide content search was performed. No file was modified except
this one. Nothing here is fabricated: every status below is backed by a named
on-disk artifact or by the absence of one.

## 1. Verdict at a glance

| # | Campaign requirement | Status | Key evidence |
|---|---|---|---|
| 1 | Local latent benchmark on shared TASKS A-E (fact handoff, state update, procedural transfer, multi-fact reasoning, dependent calculation) | UNMET | `research/latent_handoff/` contains zero references to `tasks_dev.json` / `tasks_holdout.json` / `checkers.py`; both results JSONs contain no task-family fields (see 2.3) |
| 2 | Model-pair matrix for OCC-2 (heterogeneous speaker/listener cells) | UNMET | `research/symbolic_comm/eval.py` implements 5 of the 10 planned cells; no `eval-results.json` or any matrix output exists on disk; FINAL-REPORT row and PR-DESCRIPTION slot say PENDING (see 2.4) |
| 3 | Evolved-dialect holdout evaluation (frozen card sym-v1-pt-mi-pt-sp, checksum 70031322...) | ABSENT - not run | No `eval-results.json`, no `holdout-log.jsonl`, no `dialect-card.json` in `research/symbolic_comm/`; the full pipeline (`run_all.py`) never reached its eval step (see 3) |
| 4 | Task suite A-E built and validated | MET | `research/tasks/`: 150 DEV + 150 HOLDOUT, 5 families, self-test 306/306, deterministic regeneration (BASELINE.md) |
| 5 | Text-protocol baseline on the shared suite (OCC-0 vs OCC-1) | MET | `research/taskbench/`: DEV split, first 10 tasks per family, 50/cell, 2 listeners, scored via `research/tasks/checkers.py` (TASKBENCH-BASELINE.md) |
| 6 | Official third-party benchmark runs | MET (partial set) | SILO-BENCH Level-I n2: occ0 glm-5.3, occ0 flash, occ1 flash, sym-v1 flash scorecards on disk; occ1 glm-5.3 run IN PROGRESS at audit time |
| 7 | OCC-3 pre-registered go/no-go executed | MET | OCC3-SCORECARD.md; NO-GO per pre-registration (V1-V3 PASS, G1 PASS, G2 not measured, G3 FAIL, G4 untested) |
| 8 | Honest disclosure of the above gaps in campaign reports | MET | FINAL-REPORT section 8 and PR-DESCRIPTION PENDING slots already declare items 1-3 as open; this audit verifies the declarations against disk |

Bottom line: the campaign's shipped code (default-off OCC-2 codec + telemetry)
and its negative OCC-3 verdict are not undermined by these gaps, but three
campaign-prompt evaluation requirements were never executed, and the audit
found no hidden or partial attempts at them.

## 2. Job 1 - task-suite usage audit

### 2.1 What the shared suite is

`research/tasks/` (built by the bench-tasks worker, ledger 2026-10-16 ~05:0xZ):
`tasks_dev.json` (150) + `tasks_holdout.json` (150), families A fact handoff /
B state update / C procedural transfer / D multi-fact reasoning /
E dependent calculation, `checkers.py` per-family scoring, deterministic
generator, self-test 306/306. The suite is the campaign's designated
measurement instrument for "did the receiver get the facts".

### 2.2 Who used the suite

- `research/taskbench/` (OCC-2 text-protocol baseline): YES. Runner loads the
  DEV split, first 10 tasks per family (50/cell), modes occ0/occ1, listeners
  glm-5.3 and glm-5.3-flash, strict scoring through `research/tasks/checkers.py`
  (TASKBENCH-BASELINE.md section 1). This is the only full task-suite
  evaluation in the campaign, and it covers text encodings only.
- `research/symbolic_comm/qa.py`: uses the checker RULES (loads
  `research/tasks/checkers.py`) but generates its questions from
  `msgs_corpus.json` payloads, not from the A-E tasks. No
  `tasks_dev.json`/`tasks_holdout.json` load path exists in `symbolic_comm/`.
- `research/benchmarks/` (official SILO-BENCH): uses the official engine's own
  Level-I tasks, not the A-E suite.

### 2.3 Who did not: the latent handoff harness (OCC-3)

`research/latent_handoff/` was searched file by file (all 9 .py/.md files plus
both results JSONs). Findings:

- Zero occurrences of `tasks_dev`, `tasks_holdout`, `checkers`, or any
  task-suite path in any latent_handoff source or document.
- `research/latent_handoff/results/qwen05-results.json` (409,033 B) and
  `smollm2-results.json` (500,340 B): no `task_id`, no family markers, no
  A-E/dev/holdout identifiers (regex-verified).
- The harness's own probes are synthetic private-fact constructions
  (`kv_handoff.py`): `make_trial` samples 5 word=number pairs from a fixed
  47-word pool; the receiver answers "What number is paired with X?".
  The auxiliary probe is a raw induction-format pair completion. Neither
  touches the campaign task families.
- The experiment ledger confirms the sequence: the 12:1xZ parent snapshot
  explicitly awaited "any task-suite (A-E) evals" from the harness worker; the
  harness worker's final report (12:5xZ) and OCC3-SCORECARD.md contain none
  (OCC3-SCORECARD.md has zero task-suite mentions, verified).

Answer to the audit question: NO worker ran the latent handoff through the
shared task suite. Not partially, not on a subset, not on DEV only.

### 2.4 Model-pair matrix for OCC-2

- Planned: 10 cells (C1, C2, S1-S4, X1-X4) per
  `research/symbolic_comm/RESEARCH-NOTES.md` section 5, motivated by CLSR
  Table 4 (cross-family misreads; small-speaker -> big-listener worst case).
- Implemented: `research/symbolic_comm/eval.py` `MODEL_PAIRS` defines 5 cells
  (C1, X1-X4). The other 5 (C2, S1-S4) were never coded.
- Run: NO. `eval.py` writes `eval-results.json` (default `--out`) and
  `holdout-log.jsonl`; neither file exists anywhere under `research/`.
  The only multi-model numbers on disk are the gen-0 frontier rows in
  `evolution-log.jsonl` (listener fidelity on the EVOLVE split with the
  glm-5.3-flash listener) and the DEV 8-msg real-token sample in the ledger.
  Those are single-listener measurements, not a pair matrix.
- Consistent with FINAL-REPORT section 2 ("Model-pair matrix (10 cells):
  PENDING") and PR-DESCRIPTION PENDING slots.

## 3. Job 2 - evolved-dialect holdout status

Exact status: ABSENT. The holdout evaluation of the frozen dialect
`sym-v1-pt-mi-pt-sp` (generation 5, checksum
`700313226ff397b3b50e8112dc67c2b90a7dd2d24d2dc8a492c7cf240aae28f6`) was not
run to completion; no holdout numbers exist on disk.

Artifacts that DO exist in `research/symbolic_comm/`:

- `evolution-winner.json` (3,684 B, mtime 2026-10-16 06:18 local): the winning
  card with the checksum above, lineage `packing_toggle:FE=off ->
  mt_induction:TRACE 'trace-oc' x12 -> packing_toggle:ART=off ->
  symbol_prune:21->10`.
- `evolution-log.jsonl` (14,627 B, last append 06:45 local): 8 entries -
  run_start (splits EVOLVE 31 / DEV 20 / HOLDOUT 18, listener glm-5.3-flash,
  measure deepseek-v4.1-flash, seed 20261016), one rejected model_synthesis,
  generations 0 and 1 with frontier rows, one accepted model_recombine, three
  rejected model-assisted proposals. Reproducibility note: the log stops at
  generation 1 while the winner is generation 5, so generations 2-5 were never
  appended (or were written outside the log). The evolution record is
  therefore incomplete relative to the winner.

Artifacts that DO NOT exist (all searched for by name across `research/`):

- `eval-results.json` - the eval.py output holding per-split fidelity,
  comprehension, Q&A accuracy, wire tokens vs OCC-0/OCC-1/JSON baselines,
  and the model-pair matrix.
- `holdout-log.jsonl` - the S4.5-mandated log of every HOLDOUT call
  (checksum, purpose, results).
- `dialect-card.json` - the frozen-card copy that `run_all.py` step 2 creates
  from `evolution-winner.json` before evaluation.

The intended pipeline (`research/symbolic_comm/run_all.py`) is
evolve -> copy winner to `dialect-card.json` -> `eval.py` writes
`eval-results.json`. The first step produced a winner; steps 2 and 3 left no
artifacts. The experiment ledger (11:5xZ entry) recorded "HOLDOUT eval in
progress (worker quiet since freeze)"; nothing from that eval landed on disk.
Consequences for the requested numbers: holdout fidelity, comprehension
accuracy, and wire tokens vs baselines for checksum 70031322... have NO
values. The nearest existing numbers are NOT holdout and NOT the evolved
card: gen-0 frontier (sym-v1, checksum 6e6eaf7a...: listener fidelity 1.0,
median 117 wire tokens, EVOLVE split, flash listener) and the DEV 8-msg
real-token sample (OCC-0 86.0 / OCC-1 143.6 / dialect-no-pack 116.6 /
dialect-full-pack 111.1 tok/msg).

Holdout integrity: `msgs_corpus.json`'s 18 holdout messages and
`research/tasks/tasks_holdout.json` (150 tasks) were never consumed by any
tuning or evaluation call (corpus.py honors the holdout flag; no eval output
exists). The holdout is clean but unevaluated.

## 4. Honest impact on campaign conclusions

1. Latent eval not on the A-E suite. The OCC-3 NO-GO verdict stands: it was
   driven by the resource gate G3 (Qwen sidecar peak 2,443 MiB vs 1 GiB
   budget) and the negative Experiment B (latent continuation derails beyond
   2 steps), both independent of the task suite. But the POSITIVE claim is
   narrower than the suite would have made it: KV handoff is demonstrated
   only as single-fact recall ("What number is paired with X?", one of 5
   facts per trial, ~81-token contexts) and induction completion. State
   machines (B), runbook position (C), multi-fact set composition (D), and
   dependent calculation (E) were never carried through a latent handoff.
   If OCC-3 is revisited, the A-E latent run is the unexecuted acceptance
   path, not new scope.
2. No model-pair matrix. All OCC-2 comprehension evidence is single-family:
   glm-5.3 / glm-5.3-flash listeners (deepseek-v4.1-flash appears only as the
   token-measure model, never as a listener). The cross-family misread risk
   that RESEARCH-NOTES section 5 exists to quantify is unmeasured. Runtime
   exposure is contained (flag default OFF; OCC-1 fallback; strike ledger),
   but note that the S4.5 tier-2 enablement policy requires HOLDOUT
   fidelity 1.0 per (speaker, listener, family) triple, and with no holdout
   numbers that policy can never fire. As it stands, OCC-2 has zero
   cross-family comprehension evidence and cannot be enabled under its own
   rules.
3. Evolved dialect unvalidated on holdout. The winner was selected on
   EVOLVE/DEV splits. The S4.5 ship condition ("beats OCC-1 on wire tokens
   at equal fidelity and comprehension" on HOLDOUT) was never evaluated for
   the evolved card. The shipped built-in card is the hand-designed sym-v1
   (checksum pinned in Rust tests), whose end-to-end evidence is one SILO
   flash run (case accuracy 0.9, mean S 1.0) plus the offline 69-message
   corpus. The evolved card must not be presented as validated; it is a
   research artifact pending `dialect-card.json` freeze + eval.
4. Report staleness (found during audit, for the parent's finalization):
   the sym-v1 official SILO run COMPLETED on disk after the FINAL-REPORT
   draft was written. `research/benchmarks/runs/occ2-levelI-n2-msg-glm53flash-sym/scorecard.json`:
   case accuracy 0.9 (9/10 completed, 1 errored), mean S 1.0, 253,864 real
   prompt tokens, 88 calls, 36 inter-agent messages (5,538 B, 153.8 B/message
   vs occ1 289.8), case latency p50 65.6 s / p95 751.1 s. The reports still
   mark this row PENDING. Separately, the OCC-1 glm-5.3 official run is IN
   PROGRESS right now (`runs/occ2-levelI-n2-msg-glm53-occ1/`: 5 case files,
   no scorecard, proxy log growing at audit time 06:51 local).

## 5. Workspace hygiene (job 3)

### 5.1 What would be committed

`git status --porcelain` snapshot at 2026-10-16 06:52 local:

- Tracked modifications: 10 files (9 under `crates/pi-coding-agent/` +
  `.gitignore`), matching the merge inventory (+998/-25). New code files:
  `occ2.rs`, `dialect.rs`, `tests/occ2_comm_fidelity.rs`. Three `.changes/`
  fragments. No stray tracked-file edits.
- Untracked: 125 files, ~2.65 MiB total, of which `research/` is ~2.3 MiB
  (112 files), `reports/occ23-campaign/` 7 files, `.changes/` 3, new crates
  files 3. The entire research and campaign-report trees are untracked by
  design (parent integrates; COMMIT-PLAN.md proposes the grouping).

(a) No model weights or venvs would be committed. Verified by rule and by
`git check-ignore -v`:

| Path | Ignoring rule | Status |
|---|---|---|
| `research/latent_handoff/.venv/` | root `.gitignore:57` | covered (check-ignore hit) |
| `research/**/__pycache__/`, `__pycache__/`, `*.pyc` | root `.gitignore:58` + global | covered; 1 pycache dir exists (`research/benchmarks/adapter/`), ignored |
| `runs/*/workspace/` | `research/benchmarks/.gitignore` | covered; 5 workspace dirs exist (108-449 files each), all ignored |
| `research/benchmarks/adapter/.venv/` | `research/benchmarks/.gitignore` | covered (dir does not currently exist) |
| `*.log` | root `.gitignore:3` | covered; the 4 latent_handoff run logs are ignored |
| `research/**/*.json.tmp` | root `.gitignore:59` | covered; no such files exist |

HF cache: no Hugging Face cache exists inside the worktree (no `.cache/`,
no weight files `.safetensors/.bin/.pt/.pth/.gguf/.onnx` anywhere under
`research/`). The harness loads from the user-level cache outside the repo.
Model weights: none would be committed.

(b) Stray debug files: NONE. No untracked files at repo root (only the
expected modified `.gitignore`). No `_*.py` scratch files exist in
`research/latent_handoff/` (verified by direct listing; the 9 source/doc
files are all named deliverables). No `.tmp/.bak/.orig/.swp/.DS_Store`
files under `research/`. The 4 `run-*.log` files in
`research/latent_handoff/results/` are run console logs, fully superseded by
the results JSONs, and are already gitignored - no action needed.

(c) `archive-broken-run/` documentation: PARTIAL. The broken first run IS
documented (RESULTS-EXP-A.md "Bugfix history": 0/10 on every condition
including the full-text positive control, with the three root causes; full
diagnosis in RESEARCH-NOTES.md section 4.0), but the archived artifact
location `results/archive-broken-run/smollm2-results.json` (75,097 B) is not
named in RESULTS-EXP-A.md, RESULTS-EXP-B.md, or RESEARCH-NOTES.md.
Recommendation (for the file owner or parent; not applied by this audit):
add one pointer line to the RESULTS-EXP-A.md bugfix history, e.g.
"Raw pre-fix artifacts: results/archive-broken-run/smollm2-results.json".
Keep the file: it is the raw negative record behind the documented bugfix
history and costs 75 KB.

(d) File sizes: nothing over 2 MiB would be committed. Largest untracked
files: `research/latent_handoff/results/smollm2-results.json` 500,340 B;
`qwen05-results.json` 409,033 B; `research/taskbench/taskbench_runs.jsonl`
202,248 B; `tasks_holdout.json` 126,280 B; `tasks_dev.json` 125,758 B;
`msgs_corpus.json` 90,616 B; `occ2.rs` 86,126 B. All are raw records the
campaign reports cite; keeping them is consistent with the COMMIT-PLAN's
reproducibility recommendation.

### 5.2 Hygiene warnings for the parent

- The `research/benchmarks/runs/occ2-levelI-n2-msg-glm53-occ1/` run is being
  written at audit time (case files accumulating, no scorecard yet). If
  commit 5 of COMMIT-PLAN.md stages `research/benchmarks/` before that run
  finishes, the tree will contain a partial run dir. Re-stat and either
  include the finished scorecard or exclude the incomplete run.
- COMMIT-PLAN.md's inventory lists 4 run dirs; 5 exist now (the in-progress
  one above). Its commit-5 file list should be refreshed at staging time.
- FINAL-REPORT.md section 2 and PR-DESCRIPTION.md still say the sym-v1
  official run is PENDING/"in flight"; the scorecard landed (numbers in
  section 4 above). Fill before push.

## 6. Recommendations (parent-owned; nothing executed by this audit)

1. Keep all three gaps explicitly stated in the final report and PR body
   (they already are, in PENDING/non-claims form; convert them to stated
   limitations once the parent decides what completes before push).
2. If any pre-push completion is attempted, the cheapest meaningful one is
   the frozen-dialect eval: copy `evolution-winner.json` to
   `dialect-card.json` and run `python research/symbolic_comm/eval.py`
   (gateway up, ~1-2 h budget), which produces the missing holdout numbers
   and the 5-cell pair matrix in one pass and writes `holdout-log.jsonl`.
   This also fills the PR's evolved-dialect and model-pair PENDING slots.
   If not run, the honest statement is: "evolved card unevaluated on
   holdout; not shipped".
3. The A-E latent suite run and the missing 5 matrix cells (C2, S1-S4) are
   future-campaign scope; record them as such.
4. Apply the one-line archive-broken-run pointer in RESULTS-EXP-A.md (owner
   or parent).
5. Re-run `git status` immediately before staging commits; the worktree is
   still moving (in-flight official run, other workers landing reports).
