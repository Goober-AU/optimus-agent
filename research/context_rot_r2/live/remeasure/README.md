# R2-E2-RM — offline re-measure: the post-W11 v1 ledger on the R2-E2 live corpus

Answers W22 final-report open question 3: does the integrated W11 ledger fix
(content-derived ids + correction-safe supersession, commit `1754a3e1b`)
remove the R2-E2 inversion (W7 Finding 1)?

**Verdict: yes for the scenario where the inversion caused a live failure
(base), and structurally yes for the hard scenario — with one new render-level
defect candidate in the hard scenario (the render cap hides the now-active
corrections). No correction inverts post-W11 in either scenario.**

PRODUCTION-PATH-MIRROR label: the replay is a Python port of the current Rust
ledger (`w11_port.py` against the W7 `rust_mirror.py`), not the built binary.
All model-dependent statements below are predictions derived from the
corpus-verified mechanism, never measured results.

## Method

The corpus is the committed W7 live run (`../raw-calls.jsonl`, 581 calls:
every summarizer request/response and every probe round). No model calls are
made. Two passes per scenario (`r2-e2-multicycle` 19 probes, `r2-e2-hard`
37 probes, 5 cycles each):

1. **Fixture pass (pre-W11).** `rust_mirror.py` runs unmodified with a
   corpus-backed summarizer: every summarizer request the mirror constructs
   must byte-match a corpus record, and every cycle must reproduce the
   committed `../results.json` (entry ids, cut points, entry counts, summary
   lengths, request char counts, ledger item counts, final summaries). This
   pins the replayed session structure — and therefore the ledger windows —
   to the live run.
2. **Post-W11 pass.** The same cycle loop with `rm.build_ledger` swapped for
   `w11_port.build_ledger` at the exact call site W11 changed
   (`prepare_compaction` -> `build_ledger`). The task-state ledger never
   reads summaries (it merges `details.taskState` over entry windows), so
   the post-W11 ledgers are exact; the corpus summaries are held fixed for
   the compaction entries and the pass asserts the cut points, entry ids and
   windows are identical to the fixture pass.

Scoring uses the same probes with the same versioned matchers
(`probe_protocol.py`, `live-v1`):

- recall kinds (constraint, decision, evidence, task): the matcher runs on
  the full rendered block — the live questions ask "quote the item", and the
  live model could quote from current or history sections. A strict
  normalized-containment variant is reported alongside because the matcher's
  stemmed-coverage fallback is lenient on a full render.
- the currency kind (supersession): the matcher runs on the render's CURRENT
  composite scoped to the corrected module (the render-capped ACTIVE
  CONSTRAINTS + DECISIONS items that name the module) — mirroring the live
  question "which architecture is the CURRENT choice for the X module" and
  the corpus-verified mechanism that GLM follows the render's current
  assertion (it produced the wrong live answers from the pre-W11 render).
- a structural classification is computed per correction from the ledger
  itself: `inverted` (old decision current, correction not), `ambiguous`
  (both current), `clean` (correction current, old decision not), `lost`
  (neither), plus where the render shows the correction text at all.

## The W11 port (diff against the W7 Python mirror)

Ported from `crates/pi-coding-agent/src/core/compaction/task_state.rs` at
HEAD of `rot2/w24-remeasure` (identical to the W11 commit; `git diff
1754a3e1b HEAD -- .../task_state.rs` is empty). `render_ledger` is unchanged
by W11; the mirror's render is reused verbatim. Semantic diff of
`build_ledger`:

| # | pre-W11 mirror (corpus-validated) | post-W11 port | Rust cite |
|---|---|---|---|
| 1 | ids positional: `C{n+1}` / `D{n+1}` / `R{n+1}` / `E{n+1}` | `content_id(prefix, key)`: prefix + first 8 hex chars of SHA-256 of the dedupe key, e.g. `C-a1b2c3d4` (constraints, retractions, decisions, evidence) | `content_id` lines 263-268; call sites 368, 390, 405, 445 |
| 2 | items carry no correction flag | every item carries `correction` (serde default false); constraint items get `is_correction` (message-level SUPERSEDE_RE match) | field line 90; constraint capture line 374; others 394, 413, 559 |
| 3 | no capture order | constraints and decisions captured in the window record `capture_order[id] = captured`; items carried from `previous` are absent and always count as older | lines 321-327 |
| 4 | supersession targets: every item except the correction itself | targets also exclude correction carriers and any item captured at or after the correction (older-only) | line 481; lines 484-487 |
| 5 | a target already in `superseded` is skipped | the existing pair's `newId`/`newText`/`newEntry` are overwritten (newest correction wins) | lines 519-527 |

The full unified diff is reproducible with
`python -c "import difflib, inspect; from context_rot_r2.live import rust_mirror as rm; from context_rot_r2.live.remeasure import w11_port; print(''.join(difflib.unified_diff(inspect.getsource(rm.build_ledger).splitlines(True), inspect.getsource(w11_port.build_ledger).splitlines(True))))"`
(the port references mirror helpers through the `rm.` namespace; the hunks
above are the semantic content).

## Fixture validation: PASS

- Every replayed summarizer request — 5 cycles x 2 arms x 2 scenarios, plus
  the length-ladder retries — byte-matched a corpus record (the lookup key
  is the exact request text, so the match covers the conversation chunk, the
  previous-summary chain, the retained-state anchor, the ledger render and
  the prompt assembly).
- All cycle fields reproduced the committed `results.json`: `firstKeptEntryId`,
  `summarizedEntries`, `retainedEntries`, `summaryChars`,
  `summarizerRequestChars`, `summarizerCalls`, `ledgerItems`,
  `compactedContextTokens`, and the final summaries.
- Post-W11 pass: cut points, entry ids, windows and summaries identical to
  the fixture pass.
- 4 corpus records are left unconsumed
  (`...|taskState|summarizer|8..11`): the abandoned first-run chain of the
  base taskState arm (its cycle-1 compaction failed validation, so its cycle
  2 request was built from an uncompacted session); the final resumed run
  never replays them.

## Verdict — the three-column table (corrections)

Live columns are the committed W7 results (model-answered); the replay
column is this re-measure (ledger/render-level, offline). Live points map to
replay cycles as point N -> cycle N-1.

| scenario | correction (module) | pre-W11 ledger, live taskState arm (committed results.json) | post-W11 replay (this re-measure) | default arm, live (committed results.json) |
|---|---|---|---|---|
| base | q-correction-0 (reporting) | **incorrect at points 3-5** ("architecture A", 3-rep majority at 5); correct at 1-2 while the summary chain still carried the correction | **clean at every cycle 0-4**: the correction is an ACTIVE constraint (rendered; 12/12 within cap) and the A decision is retired to SUPERSEDED with the correction as its replacement; module-scoped matcher passes at every cycle | correct at points 1-5 |
| base | q-correction-1 (validator) | **incorrect at points 3-5**; correct at 1-2 | **clean at every cycle 0-4**; matcher passes at every cycle | correct at points 1-5 |
| hard | q-correction-0 (reconciler) | correct at points 2/4/5 (the render was inverted here, but the model recovered from transcript text — W7 Finding 2) | **structurally clean at every cycle 0-4** (mutual pair gone; the A decision retired by its correction). Render caveat: the correction is cap-omitted from the rendered ACTIVE list (26 active constraints, cap 12) and appears only as a SUPERSEDED replacement text; the module-scoped render matcher fails, the ledger-level matcher passes | correct at points 2/4/5 |
| hard | q-correction-1 (notifier) | correct at 2/4/5 (render inverted; model recovered) | same as q-correction-0 | correct at 2/4/5 |
| hard | q-correction-2 (payments) | correct at 2/4/5 (render ambiguous: correction active AND A decision current) | **structurally clean at every cycle 0-4**; same render-cap caveat | correct at 2/4/5 |
| hard | q-correction-3 (reporting) | correct at 2/4/5 (render ambiguous) | **structurally clean at every cycle 0-4**; same render-cap caveat | correct at 2/4/5 |

Mechanism validation (why the replay column is meaningful): applying the
same scoring to the PRE-W11 renders reproduces the live failure exactly
where it happened — the base corrections are `inverted` at every cycle with
the module-scoped matcher failing (the render listed the corrections under
SUPERSEDED while the A decisions stayed in DECISIONS), which is the
corpus-verified cause of the live "architecture A" answers. The live failure
appeared only at points 3-5 because the summary chain carried the
correction through cycles 0-2 before the render's assertion won; the
ledger-level metric measures the render, not that inertia.

Prediction (model-dependent, not a measurement): with the render now
asserting the corrections as current and the A decisions as history, the
base corrections are expected to be answered correctly at every point — the
live run demonstrated the model follows this render's current assertion in
both directions. The hard corrections are expected to remain correct (the
post-W11 render is structurally strictly better than the pre-W11 hard
render the live model already answered correctly), with lower confidence
because the corrections are no longer listed in the rendered ACTIVE
CONSTRAINTS.

## Ledger shape, pre vs post (stable across all 5 cycles)

| scenario | mode | active constraints | active decisions | superseded pairs | retired ids |
|---|---|---|---|---|---|
| base | pre-W11 | 8 (2 corrections churned out) | 6 (both A decisions current) | 4 (2 spurious churn: C8->C2, C2->C3; 2 mutual: C12<->C10) | 4 |
| base | post-W11 | 12 (corrections active) | 4 (A decisions retired) | 2 (each A decision replaced by its correction) | 2 |
| hard | pre-W11 | 9 | 8 (all four A decisions current) | 17 (15 spurious churn + 2 mutual) | 17 |
| hard | post-W11 | 26 (all seed constraints + corrections active) | 4 | 4 (each A decision replaced by its correction) | 4 |

The W11 supersession change also removes the pre-W11 constraint churn
(ordinary constraints can no longer retire each other: the seed message
matches SUPERSEDE_RE via "never drop ...", so every seed constraint carries
the correction marker and is excluded as a target).

## Render-level probe results (secondary, all cycles)

W7 matcher on the full render (recall kinds) / module-scoped current
composite (supersession); strict normalized containment in parentheses
where it differs:

| scenario | mode | constraints | decisions | evidence | corrections | task |
|---|---|---|---|---|---|---|
| base | pre-W11 | 7/8 (7/8; q-constraint-6 churned out of the render) | 4/4 | 0/4 | 0/2 (inverted) | 0/1 |
| base | post-W11 | 8/8 (8/8; churn gone) | 4/4 | 0/4 | 2/2 (clean, rendered active) | 0/1 |
| hard | pre-W11 | 18/20 (12/20; 8 churned out) | 4/4 | 0/8 | 2/4 (2 inverted, 2 ambiguous) | 0/1 |
| hard | post-W11 | 20/20 (11/20; 9 cap-omitted) | 4/4 | 0/8 | 0/4 at render level (4/4 at ledger level; cap-omitted) | 0/1 |

Live reference points: base taskState 19/19 at points 1-2, 17/19 at 3-5
(only the corrections fail); base default 95/95; hard taskState 111/111;
hard default 111/111. The render-level metric is intentionally stricter
than the live model: it measures only what the Protected Task State block
carries, while the live answers could also use the summary chain and the
retained tail.

## Findings

1. **The R2-E2 inversion is removed (base).** Post-W11, every cycle's
   ledger keeps both corrections as ACTIVE constraints (rendered within the
   cap) and retires the pre-correction A decisions to SUPERSEDED with the
   corrections as their replacements. The pre-W11 mutual pair and the
   constraint churn are gone. The render no longer asserts the opposite of
   the user's correction.
2. **Hard scenario: structurally fixed, but the render cap now hides the
   corrections (defect candidate).** With no churn, 26 constraints are
   active; CONSTRAINTS_CAP=12 renders only the first 12 seed constraints,
   so the four corrections — authoritative user statements the ledger holds
   current — are omitted from the rendered ACTIVE list and appear only as
   SUPERSEDED replacement texts, with a "14 lower-priority item(s) omitted"
   note. Not an inversion (the A decisions also left DECISIONS), but the
   render no longer lists the corrections as active constraints, and the
   summarizer sees only the render. The same cap omits 9 of the 20
   constraint lines (the strict-containment column). Suggested fix
   direction: exempt correction-carrier items from the section caps (or cap
   by priority rather than capture order).
3. **Pre-existing (W11-independent): the ledger never captured this
   corpus's evidence.** EVIDENCE_RE (`test result:|error[E\d+]|...|FAILED`)
   does not match the scenario evidence shapes ("cargo test: 4000 passed",
   "build succeeded with 4137 warnings", "audit report X: 4274 findings",
   "benchmark X: p95 4411ms"), so the VERIFIED EVIDENCE section is empty in
   every render, pre and post. The live evidence probes passed from the
   summary chain, not the ledger.
4. **Scope notes.** The subagent/task probe is outside the ledger's
   contract (no capture rule for agent messages); both live arms answered
   it from the summary. The W11 id-collision fix (W9-F2/W9-F5) is not
   exercised by this corpus: no later window replants content whose
   positional id was retired, so the re-measure validates only the
   correction-safe supersession half of W11 on this corpus.

## Per-correction renders

### base, post-W11, cycle 0 (clean; identical through cycle 4)

```
## Protected Task State (deterministic, provenance-tagged)
Derived from the transcript by fixed rules; no item is inferred or invented. Use it to keep constraints, current decisions and verified evidence exact. A SUPERSEDED item is history, not a current requirement.

**ACTIVE CONSTRAINTS**
- C-d39f7377 [39295c8e]: TASK GOAL: migrate the billing service to API v2 before Friday.
- C-42580380 [39295c8e]: CONSTRAINT: never drop the scheduler ledger R2-CONS-bc725595
- C-1b667e53 [39295c8e]: CONSTRAINT: always run the payments suite before publishing R2-CONS-3516c936
- C-a9d40d77 [39295c8e]: CONSTRAINT: do not modify files under auth/ without review R2-CONS-698afcda
- C-b0a770d9 [39295c8e]: CONSTRAINT: keep the reconciler protocol version at v2 R2-CONS-5d76e931
- C-bbcaa294 [39295c8e]: CONSTRAINT: the billing migration must be reversible R2-CONS-068b308a
- C-92064cc1 [39295c8e]: CONSTRAINT: no new dependencies in gateway without approval R2-CONS-0b032270
- C-e9034e05 [39295c8e]: CONSTRAINT: preserve the ledger audit trail verbatim R2-CONS-76b74091
- C-1f54dfe6 [39295c8e]: CONSTRAINT: the notifier service stays single-writer R2-CONS-b8ef61c5
- C-f254f147 [6a7636bb]: CORRECTION: forget that; use architecture B for the reporting module instead R2-CORR-9d2f87f5.
- C-a8d7bb8a [6a7636bb]: The earlier A decision is superseded.
- C-28efeee2 [bde7e737]: CORRECTION: forget that; use architecture B for the validator module instead R2-CORR-72a6f7e4.

**DECISIONS (current)**
- D-f139556b [8d7229a7]: Decision: use architecture reconciler for the parser R2-DECI-b42dbfea
- D-8ed8db6a [0f6d87da]: Decision: keep the billing client in-process R2-DECI-2c55b71d
- D-90b5b294 [0fc7b1ae]: Decision: reject the gateway rewrite; extend instead R2-DECI-e382f68e
- D-d2826a60 [13d3d0e2]: Decision: the ledger fix lands behind a feature flag R2-DECI-42a5f117

**SUPERSEDED (history, not current)**
- D-a782bae3 [be7a823c] replaced by C-f254f147 [6a7636bb]: CORRECTION: forget that; use architecture B for the reporting module instead R2-CORR-9d2f87f5.
- D-3ee8f96d [715a005d] replaced by C-28efeee2 [bde7e737]: CORRECTION: forget that; use architecture B for the validator module instead R2-CORR-72a6f7e4.

**EXACT ANCHORS**: DECI, CORRECTION, SUPE, CORR
```

Contrast, pre-W11 (from the corpus request, byte-identical to the replay):
DECISIONS (current) held "use architecture A for the reporting module"
(D5) and "...validator module" (D6) while both corrections sat in SUPERSEDED
as a mutual pair (C12 replaced by C10, C10 replaced by C12) — the inversion.

### hard, post-W11, cycle 0 (clean structurally; corrections cap-omitted; identical through cycle 4)

```
## Protected Task State (deterministic, provenance-tagged)
Derived from the transcript by fixed rules; no item is inferred or invented. Use it to keep constraints, current decisions and verified evidence exact. A SUPERSEDED item is history, not a current requirement.

**ACTIVE CONSTRAINTS**
- C-d39f7377 [8a9365a6]: TASK GOAL: migrate the billing service to API v2 before Friday.
- C-bcd72597 [8a9365a6]: CONSTRAINT: never drop the reporting ledger R2-CONS-c03c64d5
- C-a683baa1 [8a9365a6]: CONSTRAINT: always run the indexer suite before publishing R2-CONS-614389ba
- C-a5c479a5 [8a9365a6]: CONSTRAINT: do not modify files under gateway/ without review R2-CONS-08df154e
- C-19b62f2c [8a9365a6]: CONSTRAINT: keep the archiver protocol version at v2 R2-CONS-b5e2af97
- C-bfc4b042 [8a9365a6]: CONSTRAINT: the validator migration must be reversible R2-CONS-611c40b5
- C-e523b49d [8a9365a6]: CONSTRAINT: no new dependencies in scheduler without approval R2-CONS-3e350f73
- C-9c044975 [8a9365a6]: CONSTRAINT: preserve the billing audit trail verbatim R2-CONS-ff67f34f
- C-fe974584 [8a9365a6]: CONSTRAINT: the auth service stays single-writer R2-CONS-6f107ef4
- C-6412df3b [8a9365a6]: CONSTRAINT: never drop the ledger ledger R2-CONS-a2ddeedf
- C-30a5ef54 [8a9365a6]: CONSTRAINT: always run the reconciler suite before publishing R2-CONS-6e0de741
- C-4cbc31b3 [8a9365a6]: CONSTRAINT: do not modify files under notifier/ without review R2-CONS-525ffed6

**DECISIONS (current)**
- D-0d55f3b2 [fb8023b5]: Decision: use architecture archiver for the parser R2-DECI-e59e919f
- D-39f65e70 [b5644cac]: Decision: keep the validator client in-process R2-DECI-255629a6
- D-451f921c [9db086ff]: Decision: reject the scheduler rewrite; extend instead R2-DECI-c760d973
- D-406783e5 [32851fb1]: Decision: the ledger fix lands behind a feature flag R2-DECI-b472e29c

**SUPERSEDED (history, not current)**
- D-23d2bfa9 [38408d79] replaced by C-60dd84cf [52ffb9bc]: CORRECTION: forget that; use architecture B for the reconciler module instead R2-CORR-c5538937.
- D-0d48175a [5a4a6b18] replaced by C-489f4523 [b7efe50b]: CORRECTION: forget that; use architecture B for the notifier module instead R2-CORR-976a0af8.
- D-62c4ee28 [b7122a3e] replaced by C-6c6eb1bd [63a609b1]: CORRECTION: forget that; use architecture B for the payments module instead R2-CORR-db6b6d66.
- D-39a661c9 [d9ef4714] replaced by C-353648f3 [a91a2ecb]: CORRECTION: forget that; use architecture B for the reporting module instead R2-CORR-0b6d89d3.

**EXACT ANCHORS**: CORRECTION, DECI, SUPE, CORR

_(14 lower-priority item(s) omitted from this block: 14 active constraint(s); the full ledger is persisted on the compaction entry.)_
```

The four corrections (C-60dd84cf, C-489f4523, C-6c6eb1bd, C-353648f3) are
active in the ledger but outside the rendered first 12; their texts appear
only as the SUPERSEDED replacement lines above.

## Files

- `w11_port.py` — the post-W11 build_ledger port (provenance comments cite
  the Rust lines).
- `replay.py` — corpus replay driver: fixture pass, post-W11 pass, scoring.
- `results.json` — full output: fixture checks, per-cycle ledgers and
  renders (pre and post), per-probe scoring, per-correction classification.

Reproduce: from `research/`, `python -m context_rot_r2.live.remeasure.replay`.

## Limitations

- Python port of the Rust ledger, not the built binary; the port is a
  surgical delta on the corpus-validated W7 mirror, and the pre-W11 half of
  the port is byte-validated against the corpus.
- The post-W11 summaries are not regenerated (offline); they are held at
  the corpus chain. This does not affect the ledgers (they never read
  summaries), but it means the probe ANSWERS post-W11 are predictions, not
  measurements.
- The live probe points cover cycles 1-5 (base) and 2/4/5 (hard); the
  replay scores all cycles 0-4 for both scenarios.


---

# D21 addendum — correction carriers exempt from the render caps (W28)

W28 (branch `rot2/w28-d21`) fixed the render-level defect candidate this
re-measure reported (Finding 2): the section caps prioritize capture order
over semantic role, so correction carriers could be cap-omitted from the
rendered ACTIVE CONSTRAINTS while their stale targets stayed visible as
SUPERSEDED replacement texts. The fix (`task_state.rs` `render_ledger`,
branch `rot2/w28-d21`) orders every capped section through
`prioritize_corrections`: correction carriers always render, ahead of the
non-correction items, and the cap applies to the remaining slots; the
dropped-count line states how many corrections were protected when the
constraints cap omitted anything ("N protected correction(s) rendered; M
lower-priority item(s) omitted from this block: ...").

This driver now runs a third pass: the post-W11 ledger (unchanged
`w11_port.build_ledger`) rendered through `d21_render.render_ledger`, the
Python port of the fixed render. The render only feeds the recorded cycle
renders and the `postW11D21*` scoring columns; pass 1 still byte-matches
the corpus and pass 2 still reproduces the W24 numbers, so the before/after
comparison below is computed inside one run. A port guard asserts that a
ledger without correction carriers renders byte-identically to the mirror
(the Rust test `no_corrections_render_is_byte_equal_to_the_previous_output`
pins the same property on the Rust side).

## Before/after (strict containment = normalized gold core contained in the
render; corrections = module-scoped CURRENT composite at the render's item
selection)

| scenario | metric | post-W11 render (W24, before) | post-W11 + D21 render (after) |
|---|---|---|---|
| hard | constraint strict containment | 11/20 (9 cap-omitted) | **20/20** |
| hard | constraint W7 matcher (lenient) | 20/20 | 20/20 |
| hard | decision strict containment | 4/4 | 4/4 |
| hard | corrections render-level (module-scoped) | 0/4 (cap-omitted) | **4/4** |
| hard | corrections structural classification | clean, every cycle | clean, every cycle (unchanged) |
| base | constraint strict containment | 8/8 | 8/8 |
| base | corrections render-level | 2/2 (rendered active) | 2/2 (unchanged) |
| base | full render | — | byte-equal to the W24 render |

## Mechanism on this corpus

The hard scenario's seed message matches SUPERSEDE_RE ("never drop ..."),
so with the message-level correction marker every one of the 26 active
constraints is a correction carrier (the W24 ledger table already noted
this marker side effect for supersession safety). The D21 exemption
therefore renders the whole ACTIVE CONSTRAINTS list: all 26 lines, the 4
corrections included, in capture order, with no omitted-count line (nothing
is omitted; 3961 chars, well under RENDER_MAX_CHARS = 6000, identical at
every cycle 0-4). That is how the constraint strict containment reaches
20/20 and the four corrections render in ACTIVE; the ledger itself is
byte-identical to pass 2 (the fix is render-only).

When a ledger does mix correction carriers with plain items, the cap
applies to the remaining slots and the dropped-count line states the
protected count; that shape is covered by the Rust tests
(`corrections_are_exempt_from_the_constraints_cap_in_the_render`,
`w24_hard_corpus_shape_renders_every_correction` for the 22-plain + 4-
correction direct shape: 4 protected, 14 omitted).

### hard, post-W11 + D21, cycle 0 (ACTIVE CONSTRAINTS head; identical through cycle 4)

```
**ACTIVE CONSTRAINTS**
- C-d39f7377 [8a9365a6]: TASK GOAL: migrate the billing service to API v2 before Friday.
- C-bcd72597 [8a9365a6]: CONSTRAINT: never drop the reporting ledger R2-CONS-c03c64d5
... (all 26 active constraints render; the four corrections close the list)
- C-60dd84cf [52ffb9bc]: CORRECTION: forget that; use architecture B for the reconciler module instead R2-CORR-c5538937.
- C-489f4523 [b7efe50b]: CORRECTION: forget that; use architecture B for the notifier module instead R2-CORR-976a0af8.
- C-6c6eb1bd [63a609b1]: CORRECTION: forget that; use architecture B for the payments module instead R2-CORR-db6b6d66.
- C-353648f3 [a91a2ecb]: CORRECTION: forget that; use architecture B for the reporting module instead R2-CORR-0b6d89d3.

**DECISIONS (current)**
- D-0d55f3b2 [fb8023b5]: Decision: use architecture archiver for the parser R2-DECI-e59e919f
- D-39f65e70 [b5644cac]: Decision: keep the validator client in-process R2-DECI-255629a6
- D-451f921c [9db086ff]: Decision: reject the scheduler rewrite; extend instead R2-DECI-c760d973
- D-406783e5 [32851fb1]: Decision: the ledger fix lands behind a feature flag R2-DECI-b472e29c

**SUPERSEDED (history, not current)**
- D-23d2bfa9 [38408d79] replaced by C-60dd84cf [52ffb9bc]: CORRECTION: forget that; use architecture B for the reconciler module instead R2-CORR-c5538937.
- D-0d48175a [5a4a6b18] replaced by C-489f4523 [b7efe50b]: CORRECTION: forget that; use architecture B for the notifier module instead R2-CORR-976a0af8.
- D-62c4ee28 [b7122a3e] replaced by C-6c6eb1bd [63a609b1]: CORRECTION: forget that; use architecture B for the payments module instead R2-CORR-db6b6d66.
- D-39a661c9 [d9ef4714] replaced by C-353648f3 [a91a2ecb]: CORRECTION: forget that; use architecture B for the reporting module instead R2-CORR-0b6d89d3.

**EXACT ANCHORS**: CORRECTION, DECI, SUPE, CORR
```

The pre-D21 render for contrast is the W24 cycle-0 render above (12 seed
lines, "14 lower-priority item(s) omitted", corrections only as SUPERSEDED
replacement texts).

Prediction (model-dependent, not a measurement): the hard-corpus corrections
are now asserted as ACTIVE constraints in the render the summarizer and the
probes see, and every probed constraint line is strict-contained, so the
live hard answers are expected to stay correct with higher confidence; the
live run already answered them correctly from a strictly weaker render.

## Files (D21 addendum)

- `d21_render.py` — the D21 render port (provenance comments cite the Rust
  lines).
- `replay.py` — third pass (`postW11D21`) plus the `postW11D21*` scoring
  columns, `goldCore` recording, the priority-aware module-scoped composite
  and classification, and the no-correction port guard.
- `results.json` — regenerated: pass 1 and pass 2 outputs unchanged from W24;
  pass 3 and the D21 columns added.
