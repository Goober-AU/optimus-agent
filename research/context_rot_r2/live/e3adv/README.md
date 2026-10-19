# R2-E3ADV — adversarial correction suite (live GLM 5.3)

Branch `rot2/w23-adv`. Extends the W17-E3 live harness
(`research/context_rot_r2/live/e3/`) with the adversarial correction
variant family the W17-E3 finding 2 called for: corrections that share
NO anchor token with the decision they replace, so the v1 task-state
ledger's token-based supersession cannot pair them by luck. The e3
package is reused unchanged (arm runner `run_arm_e3`, probe protocol,
scenario envelope, cut-point discipline); all new code lives in this
package (`e3adv_` prefix per the brief).

## What runs

- Pipeline: `prepare_compaction` -> `compact` (real GLM 5.3 summarizer
  wire call, exactly the request the Rust pipeline would send, with the
  rendered v1 task-state ledger as protected state on the taskState arm)
  -> `append_compaction` -> restore, per arm, per cycle, on three
  adversarial correction scenarios.
- Prober: after every cycle the restored provider context (compaction
  summary carrier + retained tail) is sent with the scenario's action
  question; the answer protocol demands EXACTLY one line
  `ANSWER: <value>`; temperature 0, 3 repetitions per point, majority
  >= 2 of 3.
- Baseline: 1 pre-compaction probe per family per arm over seeds +
  cycle-1 entries (the context the cycle-1 summarizer sees).

## Scenarios (deterministic blake2b seeds; golds fixed constants)

| family | test | stale value (asserted current by the ledger) | gold (action level) |
|---|---|---|---|
| ADV-C1 | semantic-swap correction, zero shared tokens | approach A: "two-phase commit coordinator" (chosen, implemented via tool diff) | approach B: saga pattern with compensating transactions; never A |
| ADV-C2 | numeric-parameter correction | "the retry budget is 3" (implemented) | 5 attempts with exponential backoff; never 3 |
| ADV-C3 | retraction-only scope change | audit log: ACTIVE CONSTRAINT ("every mutation must be recorded in the immutable audit log") + current DECISION ("build the audit log as an append-only events table") | remaining scope: CSV export + per-tenant usage metering; never the audit log |

Shape per family: 2 arms (default, taskState) x 5 cycles x 1 probe x 3
reps, plus a 1-rep baseline. Cosmetic values (line numbers, counts) draw
from `blake2b(scenario_id)`; anchors and golds are fixed. The
filler/cycle-final helpers are verbatim copies from
`e3/scenarios.py`, so the cut-point envelope is byte-identical to
W17-E3's.

## The adversarial property (verified offline before the run)

The self-test (`e3adv_selftest.py`) proves, against the real
`rust_mirror.build_ledger`:

- ADV-C1: the correction clauses share ZERO word tokens with the old
  decision (supersession score 0);
- ADV-C2: the only shared tokens are the generic parameter name
  ("retry", "budget") - score 2;
- ADV-C3: the retraction shares only the generic words "audit"/"log"
  with the retracted requirement and its build decision - max score 2;
- therefore, at every cycle, for every family:
  `ledger["superseded"] == []` - the old decision stays in DECISIONS
  (current), the correction lands in ACTIVE CONSTRAINTS, and the render
  carries no SUPERSEDED marker.

The ledger pairing threshold is score >= 3 (specific anchors count 3,
generic shared tokens 1). W17-E3's TASK-C/TASK-F corrections scored 4+
("synchronous" / "nightly" are len > 8, i.e. specific anchors); these
families score 0-2 by construction.

## Predictions (pre-registered BEFORE any live call)

Written 2026-10-19, after the offline self-test, before the first wire
call to the GLM endpoint. No matcher, scenario, probe, plan or arm is
tuned after seeing results.

1. default arm: correct at every cycle point, all three families
   (15/15) and all 3 baselines. Rationale: the live summarizer retained
   corrections in W7-E2 and W17-E3 whenever it could read them in the
   raw transcript; the correction here is explicit, recent and
   unambiguous in the cycle-1 chunk, and cycles 2-5 update from a
   cycle-1 summary that already encodes it.
2. taskState arm: at risk of the stale-current/inversion pattern. At
   least one family shows >= 1 incorrect cycle point; the failure text
   cites the stale approach/value/feature as current (two-phase commit
   coordinator / 3 attempts / audit log in scope). Mechanism: the
   ledger asserts the old decision as current at EVERY cycle (it is
   carried in the previous ledger and re-rendered into each
   summarizer request), while LEDGER_INSTRUCTION orders the summarizer
   to "Copy every ACTIVE CONSTRAINT, current DECISION and VERIFIED
   EVIDENCE item into the matching section of the summary". At cycles
   2-5 the transcript chunk is filler only, so nothing but the previous
   summary contests the ledger.
   - pre-stated risk order: C3 >= C2 > C1. C3's retracted feature is
     asserted TWICE (active constraint + current decision) and the
     retraction is just another constraint; C2 is a bare numeric
     contradiction; C1's correction clause carries "instead", the
     strongest in-band reconciling signal.
   - also pre-registered: the alternative outcome where GLM reconciles
     correctly everywhere despite the ledger. In that case the finding
     is that correctness is the summarizer's achievement against the
     ledger, not a property of it (the summary text will show both
     currents; see Findings).
3. 0 probe extraction failures (the ANSWER: protocol held at 0 failures
   in W7-E2/W17-E3 across 736+ calls).
4. Probe anchor tags (SGA-KX1 / RB5-KX1 / RT9-Q3) mostly retained
   verbatim in the restored context (v2 prompt exact-anchor discipline);
   a dropped tag is a pipeline metric, not a probe failure.
5. Ops expectation (W7 finding 3): some taskState summarizer cycles hit
   the 8192 budget and complete on the 16384 ladder step; reasoning
   tokens bill against max_tokens.

## Commands

Credentials are resolved from the two PowerShell scripts named in the
campaign brief (API key + Pomerium JWT); tokens never appear in
outputs.

```
cd research
python -m unittest context_rot_r2.live.e3adv.e3adv_selftest                     # offline self-test
python -m context_rot_r2.live.e3adv.run_e3adv --arm both                        # full suite
python -m context_rot_r2.live.e3adv.run_e3adv --arm default --families C2 --limit-cycles 1 --reps 1   # smoke
python -m context_rot_r2.live.e3adv.run_e3adv --stage aggregate                 # rebuild results.json
```

Every wire call is appended to `raw-calls.jsonl` (request, response,
usage, latency, attempts); identical requests resume from the
content-keyed cache (reps 1+ bypass it: genuine resamples). A budget
guard (250 calls, counted from the raw log) stops all chains.

## Configuration

- model `glm-5.3` (SGLang, `max_model_len` 1,048,576), temperature 0.
- summarizer `max_tokens` 8192 with the W7 bounded length-retry ladder
  (8192 -> 16384 -> 32768 -> 65536), probes 4000.
- prompt variant `v2`, `summary_update_policy=off`, summary budget 3200
  tokens (legacy mode), retained-state anchor enabled (production
  default).
- concurrency 6 across (family, arm) chains; each chain's cycles and
  probes are sequential (summary-of-summary dependency).

## Scoring (`e3adv-v1`)

W17-E3 matchers reused verbatim for the inherited kinds
(`action-approach` for C1, `action-acceptable` for C3); one new kind
for the numeric family:

- `action-number` (C2): primary = gold number present (digit-bounded,
  number words "three"/"five" mapped to digits) AND the stale number
  not asserted outside a rejection/history context; strict = stale
  number absent entirely. "5 attempts (was 3)" is primary-correct,
  strict-incorrect.

Each rep gets primary (negation-aware) and strict scores; round status
is the majority over the 3 reps; extraction failures are reported
separately and never counted as model failures (M1/M3 discipline).

## Results (final)

### Per-family verdict table (arm x family x cycle)

All cells are the majority status over 3 reps; `c` = correct, `x` =
incorrect, `f` = extraction failure. Baseline is the 1-rep
pre-compaction probe.

| arm | family | gold (action level) | base | c1 | c2 | c3 | c4 | c5 |
|---|---|---|---|---|---|---|---|---|
| default | C1 | implement the saga pattern with compensating transactions, never the two-phase commit coordinator | correct | correct | correct | correct | correct | correct |
| default | C2 | retry budget = 5 attempts with exponential backoff, never 3 | correct | correct | correct | correct | correct | correct |
| default | C3 | remaining scope: CSV export + per-tenant usage metering, never the audit log | correct | correct | correct | correct | correct | correct |
| taskState | C1 | (same golds) | correct | correct | correct | correct | correct | correct |
| taskState | C2 | | correct | correct | correct | correct | correct | correct |
| taskState | C3 | | correct | correct | correct | correct | correct | correct |

- 30/30 cycle points correct on BOTH arms; every point scored 3/3
  matching reps (no split majorities anywhere); strict == primary on
  every point; 0 extraction failures. All 6 baselines correct.
- Prediction 1 (default arm 15/15 + baselines): CONFIRMED.
- Prediction 2 (taskState arm: at least one family incorrect, stale
  current cited): NOT CONFIRMED - the pre-registered ALTERNATIVE
  outcome occurred (GLM reconciled everywhere despite the ledger).
  The failure mechanism predicted offline is real and visible in the
  raw calls (below): the ledger asserted the stale decision as
  current at every cycle and the render carried no SUPERSEDED marker;
  what failed was the predicted CONSEQUENCE, not the mechanism.
- Prediction 3 (0 extraction failures): CONFIRMED. Prediction 4
  (anchor tags retained): CONFIRMED - 1/1 anchor retention at every
  cycle, both arms, all families (tags sit in the summary). Prediction
  5 (ops): milder than expected - 0 of 30 summarizer cycles hit the
  8192 budget (all `finish_reason=stop`, no ladder steps); max
  summarizer latency 23.1 s; 0 HTTP retries; 0 validation resamples.

### Findings (one paragraph per family, evidence cited from raw-calls.jsonl)

1. ADV-C1 (semantic swap, zero shared tokens). What the ledger
   rendered, every cycle, taskState arm: ACTIVE CONSTRAINTS
   "C1 [2adc4ae5]: CORRECTION: scrap that plan - blocking round-trips
   stall the checkout path under load." / "C2 [2adc4ae5]: Use the saga
   pattern with compensating transactions instead." plus DECISIONS
   (current) "D1 [67c12634]: Decision: resolve the double-charges
   with a two-phase commit coordinator." - no SUPERSEDED section
   (selftest: supersession score 0; zero shared tokens). The
   summarizer copied D1 verbatim, as LEDGER_INSTRUCTION orders
   ("Copy every ACTIVE CONSTRAINT, current DECISION ... without
   paraphrasing"), but then did the ledger's reconciliation itself,
   identically at cycles 1-5: "**D1 [67c12634]: Decision: resolve the
   double-charges with a two-phase commit coordinator. (TPC-DEC1)**
   - Original approach; implemented and tested (16 passed; 0 failed),
   then scrapped by the user's correction because blocking round-trips
   stall the checkout path under load. Treat as history only - do not
   continue this approach." followed by "**Current approach (per
   C1/C2, SGA-KX1): saga pattern with compensating transactions** on
   the billing service". The probe answered "Saga pattern with
   compensating transactions" (3/3 reps) at every cycle. The default
   arm, with no ledger, produced the same reconciliation natively:
   "**TPC-DEC1 (superseded)**: Resolve double-charges with a two-phase
   commit coordinator - implemented and tested, but rejected by user."
   / "**SGA-KX1 (current)**: Use the saga pattern ... instead of
   two-phase commit".
2. ADV-C2 (numeric parameter, generic-token-only sharing: score 2 <
   3). The ledger asserted both numbers as current every cycle:
   DECISIONS (current) "D1 [fa05c60c]: Decision: the retry budget is
   3 for the webhook client." vs ACTIVE CONSTRAINTS "C2 [b31c62d0]:
   The retry budget is 5 attempts with exponential backoff instead."
   The taskState summary carried the stale number verbatim and
   resolved it in the same breath, cycles 1-5: "**D1 [fa05c60c]:
   Decision: the retry budget is 3 for the webhook client.
   (RTB-DEC1)** - original decision; superseded in practice by the
   user's correction RB5-KX1 (retry budget is 5 attempts with
   exponential backoff) because 3 attempts die out during endpoint
   brownouts - short bursts outrun the recoveries. Operative retry
   budget: 5 attempts with exponential backoff." The probe answered
   "5 attempts" (3/3 reps, strict clean - the stale 3 never appeared
   in any answer) at every cycle, both arms.
3. ADV-C3 (retraction-only, no replacement named). The ledger kept the
   retracted feature current TWICE - ACTIVE CONSTRAINTS carried both
   "C1 [bf39835d]: CONSTRAINT: every mutation must be recorded in the
   immutable audit log. (AUD-REQ1)" and the retraction "C2 [690130c5]:
   SCOPE CHANGE: we no longer need the audit log - drop it entirely.
   (RT9-Q3)", and DECISIONS (current) still held "D1 [5326dc5a]:
   Decision: build the audit log as an append-only events table with a
   database trigger." - supersession score max 2, nothing retired. The
   taskState summary copied all three (as instructed) and then
   annotated the contradiction away in its own words, every cycle:
   "Note: C2 removes the audit log from scope entirely, so C1's
   audit-log recording requirement no longer applies to the current
   deliverable." and, on D1: "original audit-log design; the audit
   log has since been dropped from scope by RT9-Q3, so this decision
   is relevant only as history (and as the wiring to unwind) unless
   audit logging is reinstated." It even derived the correct cleanup
   action ("Next Steps: 1. Remove the audit log entirely: delete
   src/reporting/audit_log.rs and unwind the append-only events
   table / database trigger"). The probe answered "CSV export,
   per-tenant usage metering" (3/3 reps) at every cycle, both arms.

### Interpretation

- The adversarial property held exactly as designed: with no shared
  anchor, the v1 ledger retired NOTHING (superseded == [] at every
  cycle, all families), so the taskState summarizer request asserted
  the stale decision as current at every cycle while the render's
  header promised "A SUPERSEDED item is history" - there just were no
  superseded items. What the model saw was two contradictory currents
  with no arbitration from the ledger.
- GLM 5.3 resolved that contradiction correctly in all 15 taskState
  summarizer outputs (3 families x 5 cycles) by annotating the stale item as history in its
  own words ("Treat as history only", "superseded in practice",
  "no longer applies to the current deliverable") and restating the
  operative value. The correctness is the summarizer's achievement
  AGAINST the ledger, not a property of the ledger: the ledger's
  contribution to the correction handling was to re-inject the stale
  decision verbatim, at +1.5k-1.9k request chars and +0.6k-1.2k
  summary chars per cycle (mean summarizer request 16.8k-17.0k chars
  taskState vs 15.1k-15.3k default), which the model had to argue
  down every single cycle.
- Why this differs from W7-E2's inversion: in W7-E2 the ledger's
  mis-pairing actively asserted the WRONG direction (the correction
  itself was moved to SUPERSEDED/history), and GLM followed the
  protected state faithfully into the wrong answer. Here the ledger
  fails to pair at all, which leaves the correction's own text intact
  inside ACTIVE CONSTRAINTS - a strong enough in-band signal
  ("instead", "drop it entirely") for the live model to reconcile.
  Between the two, the v1 ledger on corrections is either a coin-flip
  that can invert user intent (shared-anchor luck pairs wrong) or a
  dead weight the model must argue against (no shared anchor: stale
  current re-asserted verbatim every cycle). In neither case does it
  DO the correction's work; the typed-state v2 supersession
  (role/provenance-based) remains the fix direction.

### Budget accounting

124 wire calls in `raw-calls.jsonl` (budget 250): 93 probes (96 probe
rounds; the 3 sibling-arm baseline probes have byte-identical request
content - the baseline context is arm-independent - so the W7
content-keyed cache served them from the first arm's record), 30
summarizer records (6 chains x 5 cycles; smoke cycle-1 calls reused),
1 pong. Token totals: probes 101,950 in / 7,207 out (6,269 reasoning);
summarizers 81,138 in / 85,556 out (61,125 reasoning). All summarizer
calls completed at max_tokens 8192 (`finish_reason=stop`, no
length-ladder steps). Probe latency mean 0.7 s / max 1.4 s;
summarizer latency mean 12.3 s / max 23.1 s. 0 HTTP retries.

## Deviations from the W17-E3 harness

Inherited unchanged from W17-E3/W7 (see e3/README.md): summarizer wire
budget 8192 + bounded ladder, probe context rendering with orphan
tool results flattened, research probe system prompt, prompt variant
v2, `summary_update_policy=off`, retained-state anchor enabled,
deterministic uuid-shaped entry ids, `summarizerRequestChars` as UTF-8
bytes, budget counting from the raw log.

New here, documented precisely:

1. The e3 package is IMPORTED, not modified: `run_arm_e3` (arm
   runner), the probe protocol, and the scenario envelope (filler /
   cycle-final user message helpers copied verbatim into
   `e3adv_generators.py` so the cut-point envelope is byte-identical)
   come from e3; only the generators, the `action-number` matcher, and
   the orchestrator are new.
2. No restart round-trip in this suite: none of the three families
   needs it (W17-E3 TASK-F covered it; the brief's shape for e3adv has
   no restart leg).
3. `action-number` matcher (C2): digit-bounded number matching with
   number-word mapping ("five" -> 5) and a number-history extension
   of the rejection context ("was/were/from/before/prior/
   originally/initially"): "5 attempts (was 3)" is primary-correct,
   strict-incorrect. Pre-registered with the rest of the suite before
   any live call (commit 5c18dab81); never fired - no answer in the
   run mentioned the stale value at all.
4. Baseline probes are arm-independent in content (seeds + cycle-1
   entries, no summary carrier); the W7 cache therefore dedupes the
   sibling arm's baseline to one wire call per family. The recorded
   baseline answer is a genuine response to that exact request.

## Files

- `e3adv_generators.py` — three adversarial correction generators
  (deterministic, blake2b-seeded; fixed text constants for the decision/
  correction pairs the self-test asserts against).
- `e3adv_probe.py` — W17-E3 probe machinery reused + the
  `action-number` matcher kind.
- `run_e3adv.py` — live orchestrator (`--arm`, `--families`,
  `--limit-cycles`, `--reps`, `--stage aggregate`), budget guard,
  parallel chains, results.json.
- `e3adv_selftest.py` — offline tests (15): determinism, anchor
  planting, adversarial precondition (6 tests: no supersession
  anywhere, score matrix < 3, C1 zero shared tokens, stale decision
  current at every cycle, rendered ledger carries no SUPERSEDED),
  matcher tables, cut invariant, offline pipeline.
- `r2-e3adv-*.scenario.json` — generated scenario documents.
- `results.json` — full results + metrics + budget.
- `raw-calls.jsonl` — every model call (request, response, usage,
  latency).
