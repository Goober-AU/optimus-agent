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

_to be filled after the run; the verdict table below will use the
pre-stated predictions above, unmodified._

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
