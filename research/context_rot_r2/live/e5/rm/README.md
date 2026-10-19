# R2-E5-RM — the typed-arm benchmark re-run on the fixed implementation

Branch `rot2/w47-e5rm` (integration tip 71e770c5e). The committed R2-E5
results measured the W15 typed implementation BEFORE the D24 (stable ids)
and D29 (provenance pairing) fixes landed. E5's two negative findings —
TASK-E halting at cycle 2 on unresolved references from record-id churn,
and the E3ADV C1 decisive cell carrying no SUPERSEDED render — are both
fixed in the tree, so this re-run updates the record for the affected
cells. Labels: the offline part is PRODUCTION-PATH-MIRROR (a Python port
of the current Rust ledger, not the built binary), the live part is
LIVE-MODEL.

## What the mirror now ports (the tip-ported typed_mirror.py)

The E5 mirror carried W15 + the D29 pairing. This re-run ports the rest of
the tip's typed_state.rs fixes into `../typed_mirror.py` (commit 0bd183a5e;
every function below cites its Rust original):

- **D24** (8bc0811fb): `reconcile` only fills empty record ids. Identity is
  assigned at first sight and carried; a restatement that replaces a
  tool-derived record's text keeps the id (the text is a version, not the
  identity). This is the E5 Finding 2 fix direction.
- **D23** (same commit): the verifier's reference grammar matches exactly
  what the renderer emits — record ids are kind-prefixed
  (`KIND_PREFIXES` + dash + six lowercase hex), entry references carry the
  `@` sigil, the plain entry-shape heuristic is gone — and a bracketed
  token the ledger itself quotes inside a record text is exempt as
  verbatim source material.
- **D25** (a3997b5d6): `merge` is the step boundary — a Step-scoped record
  (PENDING_ACTION, LOW_PRIORITY_EPISODE) carried from the previous ledger
  retires unless the new window restates it; retired ids and entry ids
  live in the bounded `retiredRefs` list and stay resolvable in `verify`;
  every merged ledger is capped at `MAX_DURABLE_RECORDS` (512) with the
  cumulative drop count rendered.
- **D22** (51c9a6a59): a superseded correction/task renders
  "(superseded by [retiring])", not "(supersedes [X])".
- **D27** (same commit): `is_heading_line` admits structural shapes only
  (markdown/bold prefix or an exact render section title).
- D26 (the strict-parse salvage rider) is out of scope for the mirror: the
  research drivers always read well-formed `details.typedState` payloads,
  and the Rust salvage path only differs on payloads that fail strict
  deserialization.

Validation before the port was applied: the committed (W15+D29) mirror
reproduced the committed E5 numbers exactly — the E1 fixture pass through
`run_arm_e3` + the lossy fixture summarizer gave 15/19, 23 records at
every cycle, 777/800 hot tokens, verifier passed 5/5; the C1 offline cell
reproduced `../d29-c1-offline-results.json` byte-for-byte in its
per-cycle checks; the TASK-E chain reproduced the E5 Finding 2 churn
(VT-1cfcd1 absent from cycle 2 on, previous-render refs stranded). After
the port, the e3/e3adv selftests (14 + 15 tests) and the committed d29
offline check stay green.

Reproduce the offline part from research/:

```
python -m context_rot_r2.live.e5.rm.offline_cells
python -m unittest context_rot_r2.live.e3.selftest context_rot_r2.live.e3adv.e3adv_selftest
```

## Part 1 (offline, PRODUCTION-PATH-MIRROR) — results vs the committed E5

### Cell 1 — the E1 19-probe fixture, typed arm

The committed E1 scenario (`../adapter/r2-e5-e1-typedarm.scenario.json`,
arms restricted to `typed`) through the multicycle chain with the lossy
fixture summarizer (the same chain `tests/rot2_multicycle.rs` runs).
Artifacts: `e1-typedarm-results.json`, `e1-final-summary.txt`.

| measure | committed E5 (W15, Rust adapter) | R2-E5-RM (tip mirror) |
|---|---|---|
| retained | **15/19** | **13/19** |
| constraint (8) | 8 | **6** |
| decision (4) | 4 | 4 |
| evidence (4) | 1 (cargo-test line) | 1 (same line, same mechanism) |
| supersession (2) | 2 | 2 |
| task (1) | 0 | 0 |
| ledger records per cycle | 23, 23, 23, 23, 23 | 23, 19, 19, 19, 19 |
| hot allocation tokens (of 800) | 777 every cycle | 777, then 665 |
| verifier | passed 5/5, zero findings | passed 5/5, zero findings |
| mean request chars | 19,587 | 20,498 |

Per-probe (typed arm):

| probe | committed E5 | R2-E5-RM | note |
|---|---|---|---|
| q-constraint-0..5 (6) | retained | retained | USER_CONSTRAINT / USER_CORRECTION / SAFETY classifications, Session-scoped |
| q-constraint-6 | retained | **lost** | "preserve the payments audit trail verbatim" classifies LOW_PRIORITY_EPISODE (Step scope); D25 retires it at the cycle-2 merge (not restated), the render drops the line from cycle 2 on |
| q-constraint-7 | retained | **lost** | "the archiver service stays single-writer", same LOW_PRIORITY_EPISODE/D25 mechanism |
| q-decision-0..3 (4) | retained | retained | DECISIONS (CURRENT) |
| q-evidence-0 | retained | retained | the `(?i)FAILED` match v1's regex missed |
| q-evidence-1..3 | lost | lost | classifier scoping, both arms |
| q-correction-0..1 | retained | retained | old-A decisions retired, SUPERSEDED with replaced-by links |
| q-subagent | lost | lost | agentMessage = machine text |

So the two-probe drop is a D25 step-boundary effect, not a D24/D29
regression: the W15 ledger carried the two Step-scoped episodes forever
(they are non-mandatory tier 7, retained only while the budget holds),
while the tip retires them at the first merge that does not restate them.
The E1 fixture has no restatement of c6/c7 in its status-update windows,
so the tip's typed arm protects 13 of the 19 probes (the 14 the v1
ledger protected minus c6/c7, plus the cargo-test evidence line).

### Cell 2 — the E3ADV C1 decisive cell (D29 + the tip)

The regenerated committed C1 scenario through the 5-cycle merge chain
(the `../d29_offline_check.py` flow). Artifacts: `c1-typed-results.json`,
`c1-cycle1-hot-block.txt`, `c1-cycle5-hot-block.txt`.

| check | committed E5 (pre-D29) | W41 offline (D29 only) | R2-E5-RM (tip) |
|---|---|---|---|
| decision VD-76361d superseded, replaced-by CO-9dea27, retirePath provenance | no (no retirement) | every cycle | every cycle |
| SUPERSEDED render section | never | cycles 1-4 (cycle 5: tier-8 budget drop) | **cycles 1-5** |
| pairing annotation rendered | - | every cycle | every cycle |
| hot tokens (of 800) | - | 408/527/620/713/761, cycle 5 overflowed | 408/418/511/604/**697**, no overflow |
| ledger records | 12/16/19/22/25 | 12/16/19/22/25 | 12/13/16/19/**22** |
| verifier | passed | passed every cycle | passed every cycle |

D29 holds on the integrated tip, and the D25 step boundary removes the
W41 run's cycle-5 caveat: the stale step-scoped episodes retire at each
merge (6 retired refs accumulated by cycle 2), the ledger stays 3 records
smaller, the cycle-5 hot block fits at 697/800, and the SUPERSEDED
section renders at every cycle 1-5.

### Cell 3 — the TASK-E id chain (D24 + the tip)

The TASK-E scenario through the 5-cycle merge chain with the render as
the summary stand-in; the sentinel is the record that holds the
failing-suite line at cycle 1 (`test result: 55 passed; 1 failed`).
Artifacts: `task-e-idchain-results.json`, `task-e-cycle{1,2}-hot-block.txt`.

| check | committed E5 mirror (pre-D24) | R2-E5-RM (tip) |
|---|---|---|
| sentinel id | VT-1cfcd1 at cycle 1, churned to VT-2632d8 at cycle 2 | **VT-1cfcd1 present at every cycle 1-5** |
| sentinel text evolution | failing line, then telemetry restatements | same (tests_01 from cycle 2) |
| previous-render refs resolving in the next ledger | cycle 2: 3+ stranded ([VT-1cfcd1], [VT-25d467], [@d09cc350]); cycle 3: [VT-2632d8] | cycle 2: exactly 2 stranded ([VT-25d467], [@d09cc350]); cycles 3-5: 0 |
| ledger records per cycle | 11/14/17/20/23 | 11/11/14/17/20 |
| verifier on the render | passed every cycle | passed every cycle |

D24 works as designed: the id churn (the E5 Finding 2 root cause) is gone.
The offline chain isolates a residual, distinct strand: at the cycle-2
merge the restatement rule still ABSORBS the cycle-1 tests_01 record into
the sentinel record (its 8-token key supersets the failing line's 4-token
key), and reconcile keeps no tombstone for an absorbed record's id or
entry. The cycle-1 render cites `[VT-25d467] [@d09cc350]`, so a cycle-2
summary that preserves those citations — which the committed run's GLM
summary did — would still fail verification on exactly those two refs.
The Rust D24/D25 tests do not pin this shape (their three-cycle chain has
no absorbed sibling record); this is pre-registered below as the
residual risk for the live cell.

## Part 2 pre-registrations (written BEFORE any live call)

Live GLM 5.3 (DGX K3 LaunchPad endpoint, temperature 0, ladder
8192->65536, PONG first), typed arm only, budget <= 120 new wire calls,
raw log and results kept in this directory (the e3/e3adv packages'
committed state untouched).

### P1 — TASK-E typed, 5 cycles x 3 reps + baseline

1. **The D24 fix holds live: the verifier failure mode of the committed
   run (unresolved references to the churned id) is gone.** The sentinel
   record keeps VT-1cfcd1 at every cycle, so `[VT-1cfcd1]` citations
   resolve.
2. **Residual risk, pre-registered:** the cycle-2 merge absorbs the
   cycle-1 tests_01 record (no tombstone), so `[VT-25d467]` and
   `[@d09cc350]` citations strand. If GLM's cycle-2 summary preserves
   them (the committed run's summary did), the cycle-2 verification fails
   with exactly those unresolved refs — that is a NEW defect of the same
   family (absorbed-record references), not the D24 churn; the render and
   ledger are captured and it gets filed. If GLM cites only current-ledger
   ids, all five cycles complete.
3. **Probes: q-e-error correct at every cycle point that runs (3/3 strict
   reps) and at the baseline** — the exact assertion
   `assertion failed: depth == 4096 (got 3072)` is tier-4 VERIFIED
   EVIDENCE at every cycle in the offline chain.
4. **Ops:** some summarizer requests will need the 16384 ladder step
   (the typed block adds ~800 prompt tokens; the committed run hit the
   8192 budget on 11 of 35 E-family records); reasoning tokens bill
   against max_tokens.

### P2 — E3ADV C1 typed, 5 cycles x 3 reps + baseline

1. **The ledger retires the stale two-phase-commit decision by provenance
   at every cycle (D29 on the tip), and the SUPERSEDED section renders at
   every cycle 1-5** — the W41 run's cycle-5 tier-8 budget drop is gone
   on the tip (the D25 step boundary keeps the ledger 3 records smaller;
   offline evidence above). If the live render at any cycle 1-4 lacks the
   section, that is a defect to file.
2. **Probes: q-c1-approach correct at every cycle point + baseline (3/3
   strict reps)** — "Saga pattern with compensating transactions"; the
   stale two-phase-commit approach never asserted.
3. **Verifier passes on every accepted cycle summary; bounded validation
   retries may fire** (the W41 run needed one retry on two cycles). A
   hard failure is not expected; if one fires, the render is captured.
4. **GLM copies the retirement faithfully** ("Superseded (history, not
   current): [VD-76361d] ... retired by [CO-9dea27]"); reconciliation in
   its own words is not expected to be needed.

## Part 2 (LIVE-MODEL) — results

(filled after the runs)

## Files

- `offline_cells.py` — the three offline cells (reproduce with
  `python -m context_rot_r2.live.e5.rm.offline_cells` from research/).
- `e1-typedarm-results.json`, `e1-final-summary.txt` — Cell 1.
- `c1-typed-results.json`, `c1-cycle{1,5}-hot-block.txt` — Cell 2.
- `task-e-idchain-results.json`, `task-e-cycle{1,2}-hot-block.txt` — Cell 3.
- `run_live.py` — the Part 2 runner (typed arm only, this directory's raw
  log and results).
- `live-task-e-results.json`, `live-c1-results.json`, `raw-calls.jsonl`,
  `results.json` — the live artifacts.
