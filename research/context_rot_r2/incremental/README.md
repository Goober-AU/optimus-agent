# W9 — incremental and selective compaction experiments (production path)

Branch: `rot2/w9-incremental`. Campaign section 13: full-summary regeneration
vs summary update vs deterministic canonical state vs delta-based behavior,
measured through the REAL production pipeline, offline, with the committed
multicycle adapter as the driver.

Everything here is pipeline mechanics: the summarizer is the adapter's
deterministic lossy fixture (copies protected blocks verbatim, fixed head/tail
loss elsewhere, 400-char previous-summary decay), so differences between arms
measure what the PIPELINE protects, not model skill. No network, no real
provider, no model-dependent claims.

## Method

- Driver: `driver.py` invokes `cargo test -p pi-coding-agent --test
  rot2_multicycle rot2_production_multicycle -- --ignored` in this worktree with
  the isolated target dir `C:/Users/openclawuser/rot2-w9-target`, passing
  `ROT2_SCENARIO` / `ROT2_RESULT` exactly like `orchestrate.py`. The
  environment is scrubbed of `PRIME_AGENT_*` variables (one deliberate probe
  sets `PRIME_AGENT_SUMMARY_UPDATE_POLICY` and shows the adapter path ignores
  it).
- Scenarios: `research/context_rot_r2/scenario.py` (committed, unchanged);
  variants are deterministic post-processing in `variants.py`. Same
  `scenario_id` means the same probe anchors, so baseline vs variant is a
  paired comparison over identical anchors.
- Runs: 16 adapter invocations (see `run_experiments.py`). Raw scenario/result
  JSONs in `runs/`; analysis outputs in `results/`; summary tables via
  `table.py`.
- Tokens: chars / 4 (fixed conversion, per the W9 brief).
- Scenario shape: 19 probes (8 constraints, 4 decisions, 4 evidence,
  2 corrections/supersession, 1 subagent artifact), 8 compaction cycles,
  3 filler turns per cycle, `contextWindow` 20 000, `reserveTokens` 4 000,
  `keepRecentTokens` 20. Seeds: `r2-w9-inc-a`, `r2-w9-inc-b` (blake2b-derived,
  M5-fixed). The +2 retired-decision probes appear only in the
  correction-variant runs (21 probes).

## E-INC-1 — summary_update_policy arms through the real path

### What is actually exposed

`arm_settings` (rot2_multicycle.rs) maps only `taskState` to
`task_state=prompt`; every other arm name gets `task_state=off` plus
`default_compaction_settings()`, which pins `summary_update_policy=Some("off")`.
`compact()` reads `settings.summary_update_policy` directly and never consults
`PRIME_AGENT_SUMMARY_UPDATE_POLICY` on this path. Verified empirically:

- a `policy-consolidate-repeated-v1` arm produced a byte-identical
  request/summary series to `default` (all 8 cycles, all fields);
- setting `PRIME_AGENT_SUMMARY_UPDATE_POLICY=consolidate-repeated-v1` in the
  environment changed nothing (all cycle fields identical).

So policy variation is NOT reachable through the adapter; per the brief, the
policy experiment measures what IS exposed: the two real arms, the request
composition, and growth/decay over 8 cycles x 2 seeds.

### Request composition (what each arm sends the summarizer)

The request is exactly `<conversation> chunk</conversation> +
<previous-summary> carry </previous-summary> + prompt (+ protected ledger
block)`. Cycle-over-cycle deltas confirm additivity with zero residual on
both arms (verified on all 8 cycles):

| term | default (chars) | taskState (chars) | policy-invariant? |
| --- | --- | --- | --- |
| chunk + wrapper, cycle 0 | 10 831 | 10 831 | yes |
| chunk + wrapper, steady | 12 814 | 12 814 | yes |
| carried previous summary, steady | 1 236 | 2 846 | yes (policy never touches the carry) |
| prompt, cycle 0 (first) | 879 + 276 note | 879 + 276 note | variant only by policy: consolidate adds +1 393 (348 tokens)/call |
| prompt, steady (update) | 1 257 + 276 note | 1 257 + 276 note | as above |
| ledger render + instruction | 0 | 2 091 + 274 | taskState only |
| **total steady request** | **15 583 (3 896 tok)** | **19 562 (4 891 tok)** | |

The consolidate-repeated-v1 policy's only mechanical effect on this path is
+1 393 request chars (+348 tokens) per summarizer call after the first
compaction (prompt constant length difference). Its designed effect
(instruction-driven consolidation) is model-dependent and NOT EXECUTED
offline.

The taskState arm pays for the ledger twice per steady cycle: once as the
protected block (2 091 + 274 chars) and once inside the carried summary
(+1 610 chars, because the lossy summarizer copies the protected block into
the summary, which is then carried verbatim). Net steady-state cost of the
ledger: +3 979 chars (+995 tokens) per cycle over default.

### Growth model (both arms, both seeds)

`summary(c) = scaffold + carried-decay(min(summary(c-1), 400 chars)) + lossy
excerpt of the cycle chunk (+ verbatim protected ledger copy for taskState)`.
The carry term is capped, so growth is non-compounding: the series reaches its
fixed point at cycle 1 and never moves again.

| arm | seed | summary chars by cycle | stabilize | steady |
| --- | --- | --- | --- | --- |
| default | a | 738, 1236, 1236, ... | cycle 1 | 1 236 (309 tok) |
| default | b | 741, 1236, 1236, ... | cycle 1 | 1 236 |
| taskState | a | 2348, 2846, 2846, ... | cycle 1 | 2 846 (712 tok) |
| taskState | b | 2367, 2862, 2862, ... | cycle 1 | 2 862 (716 tok) |

### Decay model (retention by cycle)

| arm | seed | retention by cycle | final |
| --- | --- | --- | --- |
| default | a | 1/19, 0, 0, 0, 0, 0, 0, 0 | 0.0 |
| default | b | 1/19, 0, 0, 0, 0, 0, 0, 0 | 0.0 |
| taskState | a | 14/19 flat, all 8 cycles | 0.737 |
| taskState | b | 13/19 flat, all 8 cycles | 0.684 |

- default: step decay. One anchor survives the first compaction (a head/tail
  excerpt); the second compaction's 400-char carried-summary decay erases
  everything. Retention after c>=2 compactions is 0/19.
- taskState: no decay across 8 cycles — once material is in the ledger it is
  merge-carried forever. The 5 lost anchors are the known v1 gaps (4 evidence
  probes: EVIDENCE_RE matches none of the fixture's tool-result lines; 1
  subagent probe: CustomMessage entries are invisible to `entry_text`), plus
  one id-collision loss in seed b (W9-F2 below).

## E-INC-2a — per-cycle retention curves, determinism, path independence

- Determinism: an identical rerun of the seed-a 8-cycle scenario reproduced
  every cycle field on both arms (byte-equal measurement records; only session
  entry UUIDs differ, which the comparison excludes).
- Path independence: for every k in 1..7, the k-cycle run's final cycle equals
  cycle k-1 of the 8-cycle run on both arms (same entry history =>
  same measurement). Retention curves are therefore a property of the entry
  history, not of the run length.
- Curves: see the decay table above; per-probe survival detail in
  `results/e-inc2a.json`.

## E-INC-2b — late material (constraints planted in cycle 3)

**Answer: late material does NOT survive better — it survives worse.**
taskState final constraint retention drops from 8/8 (a) and 7/8 (b) to 4/8
(a) and 3/8 (b) when the same lines are planted in cycle 3 instead of opening
the session. Two pipeline mechanisms cause it (both verified against the
rendered ledger of `w9-a-late3c`):

- **W9-F1 — keyword-less constraint lines are invisible when isolated.** In
  the baseline, all constraints share ONE user message with the line "never
  drop ..." (SUPERSEDE_RE matches "drop"), so `is_correction=true` and every
  clause of that message is captured. Planted as separate entries,
  "no new dependencies ...", "preserve ... verbatim" and "stays
  single-writer" match neither CONSTRAINT_RE nor SUPERSEDE_RE and are never
  captured. Constraint capture depends on sibling text, not on the
  constraint's own semantics.
- **W9-F2 — retired-id collision silently deletes newly captured items.**
  `build_ledger` assigns ids by insertion count
  (`format!("C{}", constraints.len()+1)`) and ends with
  `ledger.constraints.retain(|item| !retired.contains(&item.id))`. At cycle 0
  the two correction items retire each other (ids C1/C3). At cycle 3 the
  newly captured "always run the reporting suite ..." item is numbered C3,
  collides with the carried retired set, and is deleted with no supersession
  record. The rendered ledger shows C2, C4, C5, C6 with C3 missing. Seed b's
  baseline loses q-constraint-3 the same way — retention is seed-dependent
  through id accidents, not through content.
- **W9-F3 — baseline anchors overstate current-state retention.** In the
  baselines, the "never drop ..." and "always run ..." clauses retire each
  other; both anchors survive only inside SUPERSEDED (history) render lines,
  not as ACTIVE CONSTRAINTS. Anchor-presence scoring counts them as retained.

## E-INC-2c — correction late (supersession planted in cycle 5)

**Answer: the new decision is kept, but the old decision is NOT retired — in
both early and late placements.** The old "use architecture A" decisions stay
in DECISIONS (current) with their anchors at every cycle after planting
(lingering at cycle 7 in all taskState runs).

- **W9-F4 — supersession retires the corrections, not the old decision.**
  The retirement loop scores candidates by shared "specific" tokens; the two
  CORRECTION lines share more tokens with each other than either shares
  with the old decision line, so they retire each other (rendered:
  "C10 replaced by C8: CORRECTION ..." and "C8 replaced by C10: CORRECTION
  ...") while the old decisions remain current. The user's "forget that"
  instruction is not honored: old and new coexist as current state.
- **W9-F5 — late supersession costs one extra constraint anchor vs early.**
  `w9-a-latecorr5c` retains 15/21 vs 16/21 for the early baseline
  `w9-a-8c-retired`; the only difference is q-constraint-6 ("preserve the
  auth audit trail verbatim"). Moving the corrections to cycle 5 shifts the
  auto-numbered constraint ids at the merge boundary and the carried
  retired-id set deletes the renumbered item (same mechanism as W9-F2).

## E-INC-3 — token cost accounting (chars / 4)

| run | arm | cycles | mean req chars | mean req tok | mean sum chars | mean sum tok | ledger cyc | total req tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r2-e1-multicycle (baseline) | default | 5 | 14 765 | 3 691 | 1 137 | 284 | 0 | 18 457 |
| r2-e1-multicycle (baseline) | taskState | 5 | 18 429 | 4 607 | 2 751 | 688 | 5 | 23 037 |
| w9-a-8c | default | 8 | 15 071 | 3 768 | 1 174 | 293 | 0 | 30 142 |
| w9-a-8c | taskState | 8 | 18 849 | 4 712 | 2 784 | 696 | 8 | 37 698 |
| w9-b-8c | default | 8 | 15 072 | 3 768 | 1 174 | 293 | 0 | 30 145 |
| w9-b-8c | taskState | 8 | 18 880 | 4 720 | 2 800 | 700 | 8 | 37 760 |
| w9-a-late3c | taskState | 8 | 17 669 | 4 417 | 2 151 | 538 | 8 | 35 338 |
| w9-b-late3c | taskState | 8 | 17 698 | 4 425 | 2 167 | 542 | 8 | 35 397 |
| w9-a-latecorr5c | taskState | 8 | 18 141 | 4 535 | 2 432 | 608 | 8 | 36 283 |
| w9-b-latecorr5c | taskState | 8 | 18 171 | 4 543 | 2 447 | 612 | 8 | 36 342 |

- Per-cycle cost is constant after cycle 1 on every arm (fixed-point
  summaries), so totals scale linearly with cycle count.
- The taskState arm costs +995 tokens/cycle steady-state (ledger render 523 +
  instruction 69 + duplicated carry 403).
- The late-material runs are CHEAPER than their baselines (4 417-4 543 vs
  4 712-4 720 tokens/cycle) because they retain less: the missing constraints
  never enter the ledger. Cost savings here are information loss, not
  efficiency.
- The consolidate-repeated-v1 policy would add +348 tokens per summarizer
  call after the first compaction (prompt constant) with zero measured
  offsetting benefit offline (its consolidation effect is model-dependent and
  NOT EXECUTED).

## Findings index (candidates for the r2 bug ledger)

- W9-F1: constraint capture depends on sibling text (is_correction marks the
  whole message), so isolated keyword-less constraints are invisible to the
  ledger.
- W9-F2: auto-renumbered ledger ids are unstable across merges; the blanket
  `retain(!retired.contains(id))` silently deletes newly captured items that
  reuse a retired id. Makes retention seed- and placement-dependent.
- W9-F3: mutual retirement demotes constraints to SUPERSEDED (history) while
  anchor scoring counts them retained; baseline retention overstates
  current-state correctness.
- W9-F4: supersession misfire — the two corrections retire each other instead
  of the old decision; "forget that" is not honored, old and new coexist as
  current decisions.
- W9-F5: late supersession loses one more constraint anchor than early (id
  renumbering at the merge boundary, W9-F2 again).
- Known v1 gaps confirmed on the production path at 8 cycles: evidence
  regex misses all 4 fixture evidence lines; subagent CustomMessages are
  invisible to `entry_text` (matches EXPERIMENT-LEDGER.md findings 3-4).

## Limitations

- The deterministic lossy summarizer follows the request structure, not
  instruction text: the consolidate policy's designed effect is a
  model-dependent claim and is NOT EXECUTED offline (no fabrication).
- 8-cycle, 19/21-probe, 2-seed scenario with fixed filler; the decay model
  describes this fixture's pipeline mechanics, not a live model's behavior.
- Anchor presence is the retention metric; W9-F3 shows presence can overstate
  current-state correctness (SUPERSEDED lines carry the anchor).

## Files

- `driver.py` — adapter invocation (env contract, isolated target dir)
- `variants.py` — late-constraints / late-corrections / retired-probe variants
- `run_experiments.py` — the 16-run inventory
- `analyze.py` — produces `results/e-inc{1,2a,2b,2c,3}.json`
- `table.py` — renders the summary tables
- `tests/test_variants.py` — offline unit tests (6, all passing)
- `runs/` — raw scenario + result JSON per run
