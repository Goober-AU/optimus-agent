# R2-E2 — live-model multicycle experiment (real GLM 5.3)

Branch `rot2/w7-livemodel`. This is the semantic-evaluation phase of the
R2 multicycle design: the same production-path pipeline as R2-E1, but the
summarizer AND the probe answerer are the real GLM 5.3 endpoint.

## What runs

- Pipeline (Python mirror of the Rust adapter, validated byte-for-byte
  against the stored R2-E1 result; see `validate_fixture.py` and
  `fixture-validation.json`): `prepare_compaction` -> `compact`
  -> `append_compaction` -> restore, per arm (`default`, `taskState`),
  per cycle, on the scenario shapes from `tests/rot2_multicycle.rs`.
- Summarizer wire call: exactly the request the Rust pipeline would send —
  system `SUMMARIZATION_SYSTEM_PROMPT`, user
  `<conversation>\n{chunk}\n</conversation>\n\n` + optional
  `<previous-summary>` block + `build_summarization_prompt` output
  (`SUMMARIZATION_PROMPT_V2` first cycle, `UPDATE_SUMMARIZATION_PROMPT_V2`
  later cycles, policy `off`), the retained-state excerpt wrapped in
  `<user-instructions>`, `KERNEL_PERSIST_SUMMARY_NOTE`, and for the
  `taskState` arm `render_ledger(...)` + `LEDGER_INSTRUCTION`.
  All constants are verbatim from the Rust sources (`rust_prompts.py` is
  auto-extracted from them).
- Prober: after each configured cycle the restored provider context
  (compaction-summary carrier message + retained tail) is sent with each
  probe question; the answer protocol demands EXACTLY one line
  `ANSWER: <value>` (M1/M3 fix; missing delimiter = extraction failure,
  never a model failure), temperature 0, 3 repetitions on final points and
  1 repetition on trend points.

## Files

- `rust_prompts.py` — verbatim prompt constants (auto-extracted).
- `rust_mirror.py` — pipeline mirror: settings, session restore, token
  estimates, cut point, task-state ledger, conversation serialization,
  prompt assembly, `validate_summary`, lossy fixture.
- `driver.py` — arm runner (cycle loop, probe hook, restored-context
  rendering).
- `probe_protocol.py` — question construction, ANSWER extraction,
  versioned matchers (`live-v1`), majority scoring.
- `validate_fixture.py` — fixture-mode validation vs R2-E1 (PASSED).
- `run_r2e2.py` — live orchestrator (GLM endpoint, retries, resume,
  JSONL raw log, aggregation).
- `r2-e2-multicycle.scenario.json`, `r2-e2-hard.scenario.json` —
  generated scenarios (blake2b-seeded; the base spec equals R2-E1's).
- `results.json` — aggregate results + metrics.
- `raw-calls.jsonl` — every model call (request, response, usage, latency).
- `fixture-validation.json` — mirror-vs-R2E1 comparison output.

## Commands

Credentials are resolved from the two PowerShell scripts named in the
campaign brief (API key + Pomerium JWT); tokens never appear in outputs.

```
cd research
python -m context_rot_r2.live.validate_fixture            # offline validation
python -m context_rot_r2.live.run_r2e2 --stage smoke --limit-cycles 1 --arms default
python -m context_rot_r2.live.run_r2e2 --stage base
python -m context_rot_r2.live.run_r2e2 --stage hard
python -m context_rot_r2.live.run_r2e2 --stage aggregate  # rebuild results.json
```

Every call is logged with a deterministic `callId`; restarts resume.

## Configuration

- model `glm-5.3` (SGLang, `max_model_len` 1,048,576), temperature 0.
- summarizer `max_tokens` 8192 with a bounded length-retry ladder
  (8192 -> 16384 -> 32768 -> 65536; see Finding 3 and post-hoc fix 1),
  probes 4000 (brief floor >= 4000; the Rust budget formula at
  reserve=4000 would compute 3200).
- prompt variant `v2`, `summary_update_policy=off`, summary budget 3200
  tokens (legacy mode), retained-state anchor enabled (production default).
- concurrency 6, per-call timeout 420 s, up to 5 retries with backoff.

## Probe plan (budget)

The brief's ~600-call guide cannot fund 3 repetitions at every probe point
(19 probes x 5 points x 2 arms x 3 reps = 570 for the base scenario alone),
so repetitions are spent where the headline numbers live:

- `r2-e2-multicycle` (19 probes): baseline (pre-compaction, 1 rep) +
  points after cycles 1-4 (1 rep) + after cycle 5 (3 reps).
- `r2-e2-hard` (37 probes): points after cycles 2 and 4 (1 rep) + after
  cycle 5 (3 reps).

## Deviations from the Rust path (documented precisely)

1. Summarizer wire budget: 8192 instead of the formula's 3200 (brief floor).
2. Probe context rendering: the restored provider context is sent as chat
   messages (summary carrier user message, user/assistant roles preserved);
   the scenario's synthetic orphan tool results (no preceding assistant tool
   call, rejected by chat/completions servers) are flattened into user
   messages with the same `[Tool result ...]` rendering the summarizer
   request uses. Agent messages stay user-role as production does.
3. Probe system prompt is a minimal research protocol prompt, not the
   production agent system prompt.
4. The Rust adapter's `arm_settings` leaves `compaction.prompt` at `legacy`;
   this experiment uses `v2` per the campaign brief. The fixture validation
   runs with `legacy` to reproduce R2-E1 exactly.
5. Retry pacing/deadlines/provider checkpoints are not mirrored (single
   bounded length retry + HTTP-level retries only). No split turns occur in
   these scenarios (cut points always land on user messages).
6. Session entry ids are deterministic uuid-shaped strings, not real
   SessionManager UUIDs; the ledger renders their 8-char prefixes, so only
   the id VALUES differ from a Rust run (fixture validation masks them).
7. `summarizerRequestChars` is UTF-8 byte length (the Rust adapter sums
   `str::len()` over captured request texts).

## Scoring

- Retention (pipeline): anchor presence in the restored context, identical
  to R2-E1 (`scoring_r2.py` category discipline).
- Probe accuracy (semantic): versioned matchers per kind (`live-v1`):
  constraints/decisions: normalized containment of the gold core phrase or
  full stemmed content-word coverage; evidence: word-boundary number;
  supersession: "architecture b"; task: subagent + section + artifact.
  Extraction failures are reported separately and never counted as model
  failures. Final points use majority (>= 2 of 3).

## Results (final)

### Probe accuracy (semantic, `live-v1` matchers; extraction failures counted separately)

| scenario | arm | round records | correct | notes |
|---|---|---|---|---|
| r2-e2-multicycle (19 probes) | default | 95 | **95 (100%)** | 19/19 at every point 1-5 |
| r2-e2-multicycle | taskState | 95 | **89 (93.7%)** | 19/19 at points 1-2; **17/19 at points 3, 4, 5** — `q-correction-0/1` fail |
| r2-e2-hard (37 probes) | default | 111 | **111 (100%)** | 37/37 at points 2, 4, 5 |
| r2-e2-hard | taskState | 111 | **111 (100%)** | 37/37 at points 2, 4, 5 |

- Baseline round (pre-compaction, base scenario): 19/19.
- Anchor retention (pipeline metric): 19/19 and 37/37 at every cycle, both arms.
- Extraction failures: **0** across all rounds (the one-line `ANSWER:` protocol held).
- The default arm shows no measurable rot at this scale with a real summarizer
  (contrast: the R2-E1 degenerate fixture scored 0/19).

### Finding 1 (headline): the taskState ledger inverts a user correction in the base scenario

The user's explicit correction — "CORRECTION: forget that; use architecture B
for the reporting module instead ... The earlier A decision is superseded." —
ends up **reported as architecture A being current** by the taskState-arm
summary, from cycle 3 onward. The probe ("After the correction, which
architecture is the current choice?") is answered `architecture A` at points
3, 4 and 5 (3-rep majority at point 5).

Root cause (verified in the rendered ledger that rides the summarizer prompt):
the ledger's supersession rule is token/position-based, not semantic.

1. `SUPERSEDE_RE` matches any entry containing "drop"/"superseded" style
   tokens. The opening constraint lines ("never drop the scheduler ledger",
   "The earlier A decision is superseded.") match, so ordinary constraints
   enter the supersession pairing.
2. The resulting pairs are spurious and mutual:
   - `C8 replaced by C2` ("never drop the scheduler ledger" -> replaced by
     another constraint), then `C2 replaced by C3` (constraint churn);
   - `C12 replaced by C10` and `C10 replaced by C12` — the two CORRECTION
     entries (reporting and validator) mutually supersede each other, and
     C10 is the split-off sentence "The earlier A decision is superseded."
3. Because both corrections appear in `superseded[].oldId`, the render lists
   them under **SUPERSEDED (history, not current)** while the pre-correction
   decisions stay in **DECISIONS (current)**: "Decision: use architecture A
   for the reporting module".
4. GLM follows the protected task state faithfully (as the v2 prompt tells
   it to) and writes, from cycle 3 on: "use architecture A for the reporting
   module: current decision per the protected task state; the B correction is
   superseded (history)". The inversion then propagates through every later
   summary. The default arm (no ledger block) handles the same correction
   correctly at every cycle.

This is a genuine ledger defect: the ledger does not merely fail to protect
the correction — it actively asserts the opposite of what the user said.

### Finding 2: the hard variant does NOT reproduce the inversion — same defective rule, different misfire

With 20 constraints the same rule mis-fires differently:

- 15 constraint items are churned into spurious `superseded` pairs (only 5
  constraint lines survive as ACTIVE), and the two "architecture B for the
  reconciler/notifier module" corrections land in a mutual pair that the
  render cap hides ("9 lower-priority item(s) omitted from this block").
- The payments/reporting corrections stay ACTIVE constraints, while the
  pre-correction A decisions also stay listed as current — an **ambiguous**
  ledger rather than an inverting one.
- GLM resolves the ambiguity using the raw correction text in the
  conversation chunk and gets all 4 corrections right at every point
  ("use architecture B ... (user correction; supersedes architecture A)").
  The 37/37 result therefore does not contradict Finding 1: the ledger is
  equally defective (17 spurious supersessions), but its error mode here is
  ambiguity-plus-omission, which the model recovers from, instead of
  explicit inversion, which it does not.

Practical reading: correctness under the taskState arm is luck of how the
regex pairing lands, not a property the ledger guarantees.

### Finding 3 (operational): GLM 5.3 reasoning tokens bill against max_tokens

The taskState-arm compaction requests (ledger block present) burn 8k-32k
reasoning tokens; the 8192 production budget with a single 2x length retry
is not survivable:

- base taskState, first run: cycles 1, 3, 4 failed `validate_summary`
  (`finish_reason=length`) at 8192 and 16384.
- With the retry ladder extended to 65536 (4 steps: 8192 -> 16384 -> 32768 ->
  65536, mirroring the Rust ceiling logic) all 5 cycles complete; one base
  cycle needed 32768 (22,320 completion tokens of which 21,399 reasoning),
  one needed 65536 after 32768 also hit length.
- Default-arm requests are cheap (~2k reasoning tokens, ~30 s worst case).
- taskState-arm summarizer latency reaches 137.8 s per call; the 420 s
  per-call timeout never fired.

### Budget accounting

581 raw wire calls total in `raw-calls.jsonl` (526 distinct requests after
content-keyed dedupe; the remainder are length-ladder retries and
superseded first-run chain variants), vs the ~600-call guide. Zero probe
extraction failures. Per-arm totals:

| scenario | arm | calls | tokens in | tokens out | sum latency | max latency |
|---|---|---|---|---|---|---|
| r2-e2-multicycle | default | 138 | 202,542 | 26,149 | 174 s | 7.9 s |
| r2-e2-multicycle | taskState | 186 | 339,282 | 244,062 | 1,030 s | 137.8 s |
| r2-e2-hard | default | 116 | 278,046 | 27,140 | 169 s | 17.2 s |
| r2-e2-hard | taskState | 121 | 326,151 | 143,880 | 617 s | 131.0 s |

## Post-hoc fixes made during the run (documented)

1. **Length-retry ladder** (rust_mirror.py): extended from the Rust single
   2x retry to a bounded 4-step ladder up to 65,536 (Rust ceiling logic),
   because GLM reasoning tokens exhaust smaller budgets on ledger-bearing
   requests (Finding 3).
2. **Validation retries** (driver.py, run_r2e2.py): one additional
   resampled summarizer attempt (fresh sampling, cache-bypassing) when
   `validate_summary` rejects; never needed in the final runs (0 fresh
   retries recorded).
3. **Content-keyed response cache** (run_r2e2.py): cache lookups keyed by
   exact request content (messages + max_tokens) instead of sequence-based
   call ids, so config changes (ladder extension) cannot replay stale
   responses at shifted ids. Concurrent identical requests may both execute
   (benign; write-back dedupes).
4. **Probe question disambiguation** (probe_protocol.py): hard-scenario
   constraint probes 8-19 aliased onto probes 0-7 because name extraction
   matched the first line with the same template inside the single opening
   seed entry (all 20 constraint lines share one entry). Fixed by extracting
   the name from the anchor's own line. The first hard run scored 26/37 with
   12 probes answered under mismatched questions; the fix re-ran the 12
   probes (72 fresh calls; everything else cached) and produced the final
   37/37. The base scenario was unaffected (its questions were already
   unique and byte-identical to the cached run).
