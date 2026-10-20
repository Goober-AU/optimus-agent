# D29 re-run - provenance-based supersession pairing (W41-D29)

Branch `rot2/w41-d29`. The R2-E5 Part 3 decisive cell re-run after the D29
fix: the typed arm on the E3ADV C1 family (semantic swap, zero shared
tokens between the correction and the decision it replaces), the exact
scenario and protocol of the committed E5 Part 3 run.

## What changed (commits e616a3126 + 7140b5218)

`typed_state.rs` supersession pairing (D29):

- Token path kept, but non-correction kinds now outrank corrections as
  targets, so a later correction retires its stale decision/requirement
  instead of swallowing the previous correction (the v1 W9-F4 mutual-pair
  shape).
- New provenance path: when no token target qualifies, the correction
  retires the most recent prior CURRENT decision when it is the only
  un-retired correction introduced since that decision and no other
  decision intervened.
- Both paired records carry `retirePath` (token | provenance), persisted
  with the ledger; `render_allocation` appends the pairing annotation
  ("(provenance pairing)" / "(token-overlap pairing)") after the paired
  lines. `record_line`, the verifier, budgets and caps are untouched (the
  D22 render-order fix lands on w38).

Ported line-cited into `live/typed_mirror.py`; the mirror and the Rust
suite produce identical content-derived ids and pairing for C1
(VD-76361d <-> CO-9dea27, provenance).

## Offline evidence (no model calls)

`d29_offline_check.py` -> `d29-c1-offline-results.json` +
`d29-c1-cycle1-hot-block.txt`: the regenerated committed C1 scenario
through the 5-cycle merge chain. At every cycle the ledger retires the
two-phase-commit decision (SUPERSEDED, replaced-by link, retirePath
provenance), the correction stays CURRENT with the saga approach, and
the verifier passes on the render. The render carries the SUPERSEDED
section at cycles 1-4; at cycle 5 the tier-8 line overflows the fixed
800-token hot budget (see below).

Before the fix (committed E5 Part 3): no retirement at any cycle, no
SUPERSEDED section in any of the 6 typed summarizer requests.

## Live cell (GLM 5.3, 22 wire calls of the <= 25 budget)

Command: `python -m context_rot_r2.live.e3adv.run_e3adv --arm typed
--families C1` (5 cycles, 3 reps per cycle point + 1-rep baseline,
temperature 0, ladder 8192->65536; PONG first).

Artifacts: `e3adv-typed-c1-results.json`, `e3adv-typed-c1-raw-calls.jsonl`
(this run's typed-arm wire calls only). The e3adv package's own
results/raw log/scenario were restored to the committed W23/E5 state
afterwards; the backups are the `*.bak` files here.

| check | result |
|---|---|
| probes q-c1-approach | 5/5 cycles + baseline correct, 3/3 strict reps each ("Saga pattern with compensating transactions"; forbidden stems commit/coordinator/2pc never asserted) |
| stale-decision retirement (ledger) | every cycle: VD-76361d SUPERSEDED, replaced-by CO-9dea27, retirePath provenance |
| SUPERSEDED section in the summarizer request | cycles 1-4 yes (and both validation-retry requests); cycle 5 no - see below |
| pairing in the request | every cycle: the correction line renders "(supersedes [VD-76361d]) (provenance pairing)" |
| typed verifier | passed on every accepted cycle summary (0 missing / 0 mis-superseded / 0 unresolved / 0 fabricated) |
| accepted summaries | copy the retirement faithfully ("Superseded (history, not current): [VD-76361d] ... retired by [CO-9dea27]"), no GLM reconciliation-in-its-own-words needed |

Two cycles needed one bounded validation retry each (the first summary
sample failed verification, the resample passed) - the verifier gate
rejecting a summary that mishandles the newly-retired decision, which the
committed run (both currents presented) never exercised.

### Cycle 5 render caveat (budget, not pairing) — resolved W46: accepted and pinned

At cycle 5 the ledger holds 25 records; the fixed 800-token hot budget
fits 24, and the allocator's tier 8 (superseded history, dropped first)
drops the retired decision's line from the hot block. The cycle-5 request
still carries the pairing (the correction's "(supersedes [VD-76361d])
(provenance pairing)" line, tier 1), the ledger retirement, verification
and the probe (3/3 strict). The committed E5 run had 795/800 tokens used
at cycle 5 with zero headroom, so the tier-8 section is budget-boundary
either way; D29 did not touch budgets/caps (the brief's rule), so this is
reported, not fixed there. The pairing annotations themselves are not
budget-counted (render-only, ~2 tokens per paired line).

W46 resolution (accepted): the drop is the allocator's documented
drop-first tier doing its job under a deliberately full budget, not a
pairing defect, and every measured consequence on this cell was benign
(the pairing line rendered, the verifier passed, probes 3/3 strict, the
stale approach never asserted). The drop is also reported, never silent:
the record lands in `allocate`'s overflow report. The contract is now
pinned by `tight_budget_drops_superseded_history_but_keeps_the_pairing_line`
(typed_state.rs): a budget that fits the correction but not the history
line still renders the tier-1 "(supersedes [id])" line, stays safe (no
mandatory material dropped) and lists the dropped record in the overflow.
The alternatives were rejected on evidence: raising the hot-budget floor
(`min(2048, floor(summary_budget/4))` in compaction.rs) raises
steady-state prompt cost on the arm E5 already measures at roughly double
token spend, for a cell where every probe was already correct; making the
history line budget-exempt-but-truncated breaches the allocator's budget
invariant (`allocation.tokens_used <= budget`) with a new render path for
a special case with zero measured benefit. Revisit if a live cell ever
shows a budget-dropped superseded decision paraphrased back as current
(that failure shape is not covered by the current verifier either way).

## Verdict

D29 FIXED: the campaign's structural claim (provenance-aware retirement
of a zero-overlap correction) holds on the decisive cell - before: 0/5
cycles with retirement, no SUPERSEDED render, GLM reconciling alone;
after: 5/5 cycles retired in the ledger with the replaced-by link and
provenance path, SUPERSEDED rendered and copied at cycles 1-4, cycle 5
pairing rendered with the history line budget-dropped (accepted and
pinned, W46 - see the caveat section above), all probes correct.
