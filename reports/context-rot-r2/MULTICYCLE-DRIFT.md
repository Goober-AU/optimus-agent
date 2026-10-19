# MULTI-CYCLE DRIFT — repeated compaction on the production path

Question: does repeated compaction erode anchor retention through the real
Rust pipeline, what stops the erosion, and how do late material and late
supersession behave? This report consolidates every committed multicycle
run of the r2 campaign. Report writer W14, 2026-10-20; no new runs.

All runs in this file share one instrument and one label pair:
SYNTHETIC-DIAGNOSTIC (deterministic lossy summarizer fixture, zero model
calls) on the PRODUCTION-PATH (the real `prepare_compaction` ->
`compact` -> `append_compaction` -> `SessionManager` restore pipeline,
driven by `crates/pi-coding-agent/tests/rot2_multicycle.rs` through
`ROT2_SCENARIO`/`ROT2_RESULT`). Differences between arms measure what the
pipeline protects, not model skill.

## 1. Experiment inventory

| experiment | runs | cycles | probes | seeds | variants | artefacts |
| --- | --- | --- | --- | --- | --- | --- |
| R2-E1 (baseline) | 1 | 5 | 19 | r2-e1 (blake2b) | default vs taskState | `research/context_rot_r2/runs/r2-e1-multicycle.{scenario,result,score}.json` |
| E-INC-1 (policy exposure, growth/decay) | 3 | 8 | 19 | a, b | default/taskState plus a `policy-consolidate-repeated-v1` arm inside `w9-a-8c` and an env-var probe run | `incremental/results/e-inc1.json`; runs `w9-a-8c`, `w9-b-8c`, `w9-a-8c-envpolicy` |
| E-INC-2a (curves, determinism, path independence) | 7 + 1 rerun | 1-8 | 19 | a | cycle-truncated reruns 1c..7c + byte-equal rerun | `incremental/results/e-inc2a.json`; runs `w9-a-{1..7}c`, `w9-a-8c-rerun` |
| E-INC-2b (late material) | 2 | 8 | 19 | a, b | constraints planted in cycle 3 | `incremental/results/e-inc2b.json`; runs `w9-{a,b}-late3c` |
| E-INC-2c (late supersession) | 3 | 8 | 21 | a, b | corrections planted in cycle 5 vs early baseline | `incremental/results/e-inc2c.json`; runs `w9-{a,b}-latecorr5c`, `w9-a-8c-retired` |
| E-INC-3 (token cost) | all above | - | - | - | chars/4 accounting incl. the R2-E1 baseline | `incremental/results/e-inc3.json` |

Scenario shape (8-cycle runs): 19 probes (8 constraints, 4 decisions, 4
evidence, 2 corrections/supersession, 1 subagent artifact; the
supersession-variant runs add 2 retired-decision probes = 21), 8 compaction
cycles, 3 filler turns per cycle, `contextWindow` 20 000, `reserveTokens`
4 000, `keepRecentTokens` 20, seeds `r2-w9-inc-a`/`r2-w9-inc-b`
(`research/context_rot_r2/incremental/README.md`, "Method"). The runs
above are sixteen adapter invocations total (3 + 8 + 2 + 3), matching the
README's inventory.

## 2. Retention by cycle

Retention = anchors present in the context after compaction cycle k
(count of `anchorsPresent` per cycle; `incremental/runs/*.result.json`).
Column `cK` below is the result file's `cycle` K-1 (the first compaction
is cycle 0), matching the W9 README's decay tables.

R2-E1, 5 cycles (baseline, `runs/r2-e1-multicycle.result.json`):

| arm | c1 | c2 | c3 | c4 | c5 | final |
| --- | --- | --- | --- | --- | --- | --- |
| default | 1/19 | 0/19 | 0/19 | 0/19 | 0/19 | 0/19 |
| taskState | 14/19 | 14/19 | 14/19 | 14/19 | 14/19 | 14/19 |

E-INC-1, 8 cycles, both seeds (`incremental/runs/w9-{a,b}-8c.result.json`):

| arm | seed | c1 | c2 | c3 | c4 | c5 | c6 | c7 | c8 | final |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| default | a | 1/19 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/19 |
| default | b | 1/19 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/19 |
| taskState | a | 14/19 | 14 | 14 | 14 | 14 | 14 | 14 | 14 | 14/19 |
| taskState | b | 13/19 | 13 | 13 | 13 | 13 | 13 | 13 | 13 | 13/19 |

Two decay regimes, both verified over 8 cycles:

- default: step decay. One anchor survives the first compaction (a head/tail
  excerpt); the second compaction's 400-char carried-summary decay erases
  everything. Retention after two or more compactions is 0/19, in both seeds,
  at every cycle count (`incremental/README.md`, "Decay model").
- taskState: no decay. Once material is in the ledger it is merge-carried
  flat across all 8 cycles. The 5-6 missing anchors are the known v1 gaps:
  4 evidence probes (the fixture's tool-result lines match no `EVIDENCE_RE`
  alternative) and 1 subagent probe (`CustomMessage` entries are invisible
  to `entry_text`), plus one id-collision loss in seed b (W9-F2 below).

E-INC-2a adds two pipeline properties (`incremental/results/e-inc2a.json`):

- determinism: an identical rerun of the seed-a 8-cycle scenario reproduced
  every cycle field on both arms (byte-equal measurement records; only
  session entry UUIDs differ).
- path independence: for every k in 1..7, the k-cycle run's final cycle
  equals cycle k-1 of the 8-cycle run on both arms. The retention curve is a
  property of the entry history, not the run length.

## 3. Growth model (what the summarizer request becomes)

The fixture's summary is `scaffold + carried-decay(min(previous summary,
400 chars)) + lossy excerpt of the cycle chunk (+ verbatim protected ledger
copy for taskState)`. The carry term is capped, so growth is
non-compounding: the series reaches a fixed point at cycle 1 and never
moves again (`incremental/README.md`, "Growth model").

| arm | seed | summary chars by cycle | fixed point |
| --- | --- | --- | --- |
| default | a | 738, 1236, 1236, ... | 1 236 chars (309 tok) at cycle 1 |
| default | b | 741, 1236, 1236, ... | 1 236 |
| taskState | a | 2348, 2846, 2846, ... | 2 846 chars (712 tok) at cycle 1 |
| taskState | b | 2367, 2862, 2862, ... | 2 862 (716 tok) |

Request composition at the fixed point, decomposed from cycle-over-cycle
deltas with zero residual on all 8 cycles
(`incremental/results/e-inc1.json`, `requestComposition`):

| term (steady state) | default | taskState |
| --- | --- | --- |
| chunk + wrapper | 12 814 chars | 12 814 chars |
| carried previous summary | 1 236 | 2 846 |
| update prompt + kernel note | 1 257 + 276 | 1 257 + 276 |
| ledger render + instruction | 0 | 2 091 + 274 |
| total steady request | 15 583 chars (3 896 tok) | 19 562 chars (4 891 tok) |

Request series (`e-inc1.json` `additivity{Default,TaskState}.requestChars`):
default 11 986 -> 15 085 -> 15 583 (flat); taskState 14 355 -> 19 064 ->
19 562 (flat). The taskState arm pays for the ledger twice per steady
cycle: once as the protected block (2 091 + 274 chars) and once inside the
carried summary (+1 610 chars, because the lossy fixture copies the
protected block into the summary, which is then carried). Net steady-state
cost of the ledger: +3 979 chars (+995 tokens) per cycle over default.

Policy exposure (E-INC-1's actual question): the adapter maps only
`taskState`; every other arm name gets `task_state=off` plus
`default_compaction_settings()`, which pins `summary_update_policy=off`,
and `compact()` never consults `PRIME_AGENT_SUMMARY_UPDATE_POLICY` on this
path. Verified empirically: a `policy-consolidate-repeated-v1` arm was
byte-identical to `default` across all 8 cycles, and setting the
environment variable changed nothing (`runs/w9-a-8c-envpolicy.result.json`).
The policy's only mechanical effect offline is +1 393 request chars
(+348 tokens) per summarizer call after the first compaction; its designed
effect (instruction-driven consolidation) is model-dependent and NOT
EXECUTED.

## 4. Late material does not survive better - it survives worse

E-INC-2b plants the same constraint lines in cycle 3 instead of the opening
turn (`incremental/results/e-inc2b.json`). Final taskState constraint
retention drops from 8/8 (seed a) and 7/8 (seed b) to 4/8 and 3/8; total
retention 10/19 and 9/19 (runs `w9-{a,b}-late3c.result.json`, curve
6/19 -> 10/19 at the plant cycle for seed a). Two pipeline mechanisms, both
verified against the rendered ledger:

- W9-F1 - keyword-less constraint lines are invisible when isolated. In
  the baseline all constraints share ONE user message whose line "never
  drop ..." makes `SUPERSEDE_RE` match, so `is_correction=true` and every
  clause of that message is captured. Planted as separate entries, "no new
  dependencies ...", "preserve ... verbatim" and "stays single-writer"
  match neither `CONSTRAINT_RE` nor `SUPERSEDE_RE` and are never captured.
  Constraint capture depends on sibling text, not on the constraint's own
  semantics.
- W9-F2 - retired-id collision silently deletes newly captured items.
  `build_ledger` numbers ids by insertion count and ends with
  `retain(|item| !retired.contains(&item.id))`. At cycle 0 the two
  correction items retire each other (ids C1/C3); at cycle 3 a newly
  captured item is numbered C3, collides with the carried retired set, and
  is deleted with no supersession record (rendered ledger of
  `w9-a-late3c` shows C2, C4, C5, C6 with C3 missing). Seed b's baseline
  loses q-constraint-3 the same way: retention is seed-dependent through id
  accidents, not content.
- W9-F3 - the scoring caveat: in the baselines the "never drop ..." and
  "always run ..." clauses retire each other and both anchors survive only
  inside SUPERSEDED (history) render lines, not as ACTIVE CONSTRAINTS.
  Anchor presence counts them retained, so baseline retention overstates
  current-state correctness.

## 5. Late supersession keeps the new decision but does not retire the old one

E-INC-2c plants the corrections in cycle 5 instead of cycle 0
(`incremental/results/e-inc2c.json`; runs `w9-{a,b}-latecorr5c` vs the
early baseline `w9-a-8c-retired`, 21 probes each).

| run | final retention | by kind (taskState) |
| --- | --- | --- |
| `w9-a-8c-retired` (early) | 16/21 | 8 constraints + 4 decisions + 2 supersession + 2 retired anchors |
| `w9-a-latecorr5c` (late) | 15/21 | 7 + 4 + 2 + 2 |
| `w9-b-latecorr5c` (late) | 14/21 | 6 + 4 + 2 + 2 |

- W9-F4 - supersession misfire: the retirement loop scores candidates by
  shared "specific" tokens; the two CORRECTION lines share more tokens with
  each other than either shares with the old decision, so they retire each
  other (rendered: "C10 replaced by C8: CORRECTION ..." and "C8 replaced
  by C10: CORRECTION ...") while the old "use architecture A" decisions
  remain under DECISIONS (current). The user's "forget that" instruction is
  not honored: old and new coexist as current state, at every cycle after
  planting, in both early and late placements
  (`e-inc2c.json` `lingeringRetiredAnchors`: q-retired-0/1 present at
  cycle 7).
- W9-F5 - late supersession costs one extra constraint anchor vs early
  (15/21 vs 16/21; the only difference is q-constraint-6, "preserve the
  auth audit trail verbatim"). Moving the corrections to cycle 5 shifts
  the auto-numbered constraint ids at the merge boundary and the carried
  retired-id set deletes the renumbered item - the same W9-F2 mechanism.

## 6. Token cost (chars / 4, fixed factor)

From `incremental/results/e-inc3.json` (`means`; the R2-E1 rows restate
`runs/r2-e1-multicycle.result.json` per cycle):

| run | arm | cycles | mean req chars | mean req tok | mean sum chars | mean sum tok | ledger cycles | total req tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2-e1-multicycle | default | 5 | 14 765.2 | 3 691.3 | 1 136.6 | 284.1 | 0 | 18 456.5 |
| r2-e1-multicycle | taskState | 5 | 18 429.4 | 4 607.4 | 2 750.6 | 687.6 | 5 | 23 036.8 |
| w9-a-8c | default | 8 | 15 071.1 | 3 767.8 | 1 173.8 | 293.4 | 0 | 30 142.2 |
| w9-a-8c | taskState | 8 | 18 848.9 | 4 712.2 | 2 783.8 | 695.9 | 8 | 37 697.8 |
| w9-b-8c | default | 8 | 15 072.4 | 3 768.1 | 1 174.1 | 293.5 | 0 | 30 144.8 |
| w9-b-8c | taskState | 8 | 18 880.1 | 4 720.0 | 2 800.1 | 700.0 | 8 | 37 760.2 |
| w9-a-late3c | taskState | 8 | 17 668.9 | 4 417.2 | 2 151.2 | 537.8 | 8 | 35 337.8 |
| w9-b-late3c | taskState | 8 | 17 698.2 | 4 424.6 | 2 167.0 | 541.8 | 8 | 35 396.5 |
| w9-a-latecorr5c | taskState | 8 | 18 141.4 | 4 535.3 | 2 432.0 | 608.0 | 8 | 36 282.8 |
| w9-b-latecorr5c | taskState | 8 | 18 170.8 | 4 542.7 | 2 447.2 | 611.8 | 8 | 36 341.5 |

(Default arms of the variant runs match `w9-{a,b}-8c` and are omitted; see
`e-inc3.json` for every row.)

- Per-cycle cost is constant after cycle 1 on every arm (fixed-point
  summaries), so totals scale linearly with cycle count.
- The taskState arm costs +995 tokens/cycle steady state (ledger render
  523 + instruction 69 + duplicated carry 403;
  `incremental/README.md`, E-INC-3).
- The late-material runs are CHEAPER than their baselines (4 417-4 543 vs
  4 712-4 720 tokens/cycle) because they retain less: the missing
  constraints never enter the ledger. The cost saving is information loss,
  not efficiency.

## 7. What this establishes, and what it does not

Established (pipeline mechanics, deterministic):

1. Summary-of-summary is a real loss mechanism on the production path: an
   even mildly lossy summarizer plus five compactions destroys every
   unprotected anchor (0/19), and two compactions suffice for total loss.
2. A merge-carried ledger stops the decay flat for the kinds it captures
   (constraints, decisions, explicit corrections: 14/14 in seed a), at
   +995 tokens/cycle.
3. Retention is placement- and seed-dependent through id-instability and
   sibling-text capture (W9-F1/F2/F5), not through content.
4. Supersession does not honor "forget that" (W9-F4), and anchor-presence
   scoring overstates current-state retention (W9-F3).

Not established: anything about a live model. The summarizer is a fixture;
the consolidate policy's designed effect is model-dependent and NOT
EXECUTED. The 8-cycle, 19/21-probe, 2-seed scenario with fixed filler
describes this fixture's pipeline mechanics, not live behaviour
(`incremental/README.md`, "Limitations"). The v1 gaps that cap the ledger
arm at 14/19 (evidence regex, subagent custom messages) are defects N1/N2
in `reports/context-rot-r2/BUG-LEDGER.md`; the W9-F1..F5 mechanisms are
candidates for the same ledger and are narrated in
COMPATION-CORRECTNESS.md.
