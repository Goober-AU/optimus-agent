# DEBRIS ASSESSMENT — dead-worktree residue in w16 and w5

Worker W86. Date: 2026-10-20. Branch `rot2/w86-debris`.
Mission: read-only verdict on the unaccounted changes W68's preflight flagged in
`optimus-rot2-w16-supersession` (uncommitted `task_state.rs` edits + untracked
`rot2_supersession.rs`) and `optimus-rot2-w5-research` (untracked `refs/`), against
the integration tip `optimus-rot2-20261020` (HEAD `ac5edde2b`).
No file in either debris worktree was modified; no stash, branch, or delete
operation was performed. No tests were run (assessment only; the verdicts rest on
byte-level and semantic comparison, not on execution).

## Item 1 — w16 uncommitted `task_state.rs` edits (+260/-21): SAFE-TO-DISCARD

State: the w16 branch (`rot2/w16-supersession`, HEAD `4b0add723`) last touched
`task_state.rs` in the campaign base `c912b6840`; the committed blob `eeaed0805`
IS the pre-integration base. The uncommitted diff is therefore pure drafting work
on top of the base, not a modification of integrated code.

Every semantic unit of the uncommitted diff is present at the tip:

- Byte-identical on the tip: the supersession module-doc paragraph; the
  `SUPERSEDE_RE` capture-only doc (W9-F1); `CORRECTION_CUE_RE`;
  `PROHIBITION_RE`; `SCAFFOLD`; `is_correction_clause`; the `correction` field on
  `TaskStateItem` (+ `is_false`); the `old_text` field on `Supersession`; the
  `capture_wide` clause-capture rename; per-clause correction classification;
  the supersession ordinal gate; the correction-target exclusion; the
  SCAFFOLD-filtered anchor scoring (`score = specific*3 + anchors`); the
  `old_text` population in the superseded push; and the SUPERSEDED render that
  shows the retired item as history.
- The four unit tests added by the diff
  (`standing_prohibitions_are_not_corrections_but_cues_are`,
  `two_corrections_never_retire_each_other`,
  `a_correction_cannot_retire_a_newer_decision`,
  `the_split_off_superseded_sentence_retires_nothing`) are byte-identical on the
  tip.
- Present at the tip in strictly evolved form (draft -> integrated refinement):
  `captured_at` (text -> entry-position map) became `capture_order` (id -> capture
  order, commit `1754a3e1b`); the corrections-first render sort became
  `prioritize_corrections` with cap exemption (commit `a9035ce8a`, D21); the
  skip-if-already-retired superseded check became an update-in-place re-point to
  the newest correction; positional ids (`C3`/`D1`/`E2`) became SHA-256
  content-derived ids (`content_id`, commit `1754a3e1b`).

Of the 41 removed-side lines in a w16-uncommitted -> tip diff, 36 are not
literally present on the tip; each was manually confirmed to be one of the
evolutions above, i.e. a replacement, not a loss. Nothing in the uncommitted
work is absent from the tip.

Attribution context: W51's three integration commits for this patch are
`a112034dc` (prohibition scrubbing + per-clause classification),
`611d2312c` (scaffold-anchored pairing + `old_text`), and `3de7ef222`
(retired-item history render + `tests/rot2_supersession.rs`). The two other
`task_state.rs` commits at the tip (`1754a3e1b`, `a9035ce8a`) came from the
ledgerfix/D21 line of work and carry the evolutions listed above.

## Item 2 — w16 untracked `tests/rot2_supersession.rs`: SAFE-TO-DISCARD

The untracked file (1,191 lines, 4 tests:
`w7_base_correction_is_current_and_a_decision_is_history`,
`w9f4_late_correction_retires_the_old_decision`,
`hard_scenario_ordinary_constraints_do_not_churn`,
`build_ledger_is_idempotent_for_the_same_window`) is BYTE-IDENTICAL to the
tip's `crates/pi-coding-agent/tests/rot2_supersession.rs`, integrated in commit
`3de7ef222`. It is not an older draft and holds no extra tests.

## Item 3 — w5 untracked `refs/`: SAFE-TO-DISCARD (one offline caveat)

Inventory (587 MB total):

| entry | size | what it is |
| --- | --- | --- |
| `refs/cb-venv` | 10,372 files, 282.0 MB | Python 3.11 venv; editable `compactbench` 0.2.0 install pointing at `C:/Users/openclawuser/optimus-context-rot-refs/compactbench` (outside this worktree; unaffected by discarding `refs/`). Rebuildable. |
| `refs/cikm26-knowledge-triage` | 432 files, 10.4 MB | Pristine clone (clean `git status --porcelain --ignored=matching`) of `https://github.com/searchsim-org/cikm26-knowledge-triage` at `a6ceb01a`. |
| `refs/ContextBench` | 7,580 files, 295.2 MB | Pristine clone (clean, incl. ignored) of `https://github.com/EuniAI/ContextBench` at `1436c28a`. |
| `refs/experiments` | 0 files | Empty; contains only an empty `results/` directory. |

Everything W5 derived from these refs is committed and integrated: the branch's
three commits (`3734baa7f`, `f21d07294`, `a796d4b38`) landed at the tip as
`3eba1c52f`, `212aaf876`, `cc18c29a4`, and the deliverables
(`research/context_rot_r2/KNOWLEDGE-TRIAGE-STUDY.md`, `CONTEXTBENCH-EVAL-DESIGN.md`,
`COMPACTBENCH-ADAPTER-DESIGN.md`, `REFERENCES.md`, `repro/typecompact_repro.py`)
are byte-identical between the w5 worktree and the tip. `REFERENCES.md` pins both
clone commits and documents exactly what was inspected, so the provenance record
survives the discard.

Caveat (the only offline-irreplaceable content): the ContextBench parquet
dataset (`data/full.parquet`, 1,136 rows, and `contextbench_verified.parquet`)
exists locally only inside this clone. The eval design deliberately does not
execute it in this campaign (model-dependent, out of scope), and the dataset is
public and pinned, so under the campaign's rules nothing of value is lost. If a
future OFFLINE ContextBench data run is ever planned, extract
`refs/ContextBench/data/` first; otherwise discard freely.

## Bonus observation — shared `stash@{0}` (not flagged by the preflight)

Both debris worktrees share one repo-level stash:
`stash@{0}: On rot2/w11-ledgerfix: w11 dirty overlap with w13 D10 (discarded)`
(`branch_summarization.rs` +104/-10, 2026-10-19). Its D10 content (tool results
in branch summaries, `is_tool_result_entry`, both test names) is fully present
at the tip, and the stash message itself already marks it discarded. It can be
dropped when convenient; per the rules I did not touch it.

## Verdict summary

| item | verdict |
| --- | --- |
| w16 uncommitted `task_state.rs` edits | SAFE-TO-DISCARD (nothing unique; 100% on the tip, mostly byte-identical, remainder strictly evolved) |
| w16 untracked `tests/rot2_supersession.rs` | SAFE-TO-DISCARD (byte-identical to the integrated file) |
| w5 `refs/` | SAFE-TO-DISCARD (pristine public clones + rebuildable venv + empty dir; pinned commits recorded on the tip; extract `refs/ContextBench/data/` first ONLY if a future offline data run is planned) |

Deviations from brief: none. Open questions: none.
