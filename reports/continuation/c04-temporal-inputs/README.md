# c04 temporal-memory benchmark inputs (LoCoMo dev, GLM-5.3)

Prepared inputs for the next already-approved temporal-memory benchmark arm pair.
Not a new research project and not a proposal: everything here is bounded,
offline-prepared, and launch-ready except one cost-gate decision that belongs to
the parent (see Blocker).

Owned directory: `reports/continuation/c04-temporal-inputs/` only. Nothing outside
it was created or modified; the repo, references, profiles, credentials, existing
campaign data and stores are untouched.

## Artifacts

| Path | Content |
| --- | --- |
| `manifests/locomo-c04-tsoff-s0p1.json` | Fresh capture manifest, arm A: `settings.evidenceTimestamps=false` |
| `manifests/locomo-c04-tson-s0p1.json` | Fresh capture manifest, arm B: `settings.evidenceTimestamps=true` |
| `declarations/locomo-c04-model-questions.jsonl` | 6 gold-free caller question declarations (actual `bench.prepare_questions` output) |
| `scoring/qs_conv-41.jsonl` | PRIVATE scoring rows for exactly the 6 selected questions (contains gold; never printed in reports) |
| `selection.json` | Env + question selection record, rules, hashes, cost bounds (gold-free) |
| `sources.json` | SHA256 of every input source and artifact |
| `validation.py` | Offline validation of both manifests (see below; passes) |
| `common/locomo/` | Regenerated current-converter outputs (dev env/questions). `full/manifest.json` is a private gold planning manifest, never read for selection |
| `runs/` | Empty; per-arm output roots created by the driver at launch |

## Selection (before any outcome inspection)

- Benchmark family: LoCoMo **dev** split (300 questions, 10 environments, seed
  20261007). Dev question rows were regenerated with the current converter
  (`optimus-locomo-conversion/2.0.0`, `optimus-local-scoring/1.0.0`); the dev qid
  set is identical to the existing campaign dev split (verified, 300/300). No
  heldout questions and no gold were read at any selection step.
- Environment: order the 10 dev env ids by `SHA256("c04-temporal-inputs-env-v1:<env_id>")`
  ascending; rank 1 = **conv-41**. Full ranking is in `selection.json`.
- History: conv-41's **full** conversation (32 sessions, 663 events, 103,579
  chars) is embedded verbatim in both manifests. No gold-driven source subsetting.
- Temporal questions: conv-41 dev questions with `category=="2"` (temporal under
  the current converter labels), ordered by
  `SHA256("c04-temporal-q-v1:<qid>")`, take first <=8. **4 available, 4 selected**:
  `conv-41_q91`, `conv-41_q20`, `conv-41_q46`, `conv-41_q54`.
- Controls (fixed ID-hash, non-temporal dev questions of the same env, ordered by
  `SHA256("c04-temporal-control-v1:<qid>")`, first <=2): `conv-41_q170` (category
  5, adversarial), `conv-41_q77` (category 4, single-hop).
- Total: **6 model-facing questions per arm** (4 temporal + 2 controls).

## Arms

Both arms are byte-identical in env (verbatim full history, roles, event `ts`),
question declarations, prompts (`answerInstruction`, `importInstructions`), model
(`dgx-glm53/glm-5.3`), and every other setting. The only functional difference is
`settings.evidenceTimestamps` (false vs true); `run_id`, `variant` and
`output_dir` differ as run identity only (verified by deep-diff in `validation.py`).

Settings carried over from the existing baseline manifests (model-free, unchanged):
`maxExtractionTokens=32000`, `maxImportChunkChars=8000`, `questionTimeoutS=240`,
`ingestTimeoutS=21600`, locomo `answerInstruction`, comprehensive
`importInstructions`. Added per this task's requirements: `memoryLearning=false`
(no learning during answers), `recallQueryDistillation=false`,
`recallRerank=false` (helper flags off), `disableTools=true`,
`answerMaxTokens=8192` (same both arms), and the new capture opt-in
`captureProtocol="optimus-memory-capture/temporal-authority/1.0.0"`. No
`answerThinkingLevel` override and no other provider-control changes.

## Source-time authority

- All timestamps come from the converter's real `event.ts` (LoCoMo session dates,
  2022-12-17..2023-08-16), one session date per session, 663/663 events dated.
  No synthetic clock, no invented dates. Naive ISO calendar fields are preserved
  as UTC (`naive_iso_policy=preserve_calendar_fields_as_UTC` in the driver).
- Arm tsoff (`evidenceTimestamps=false`): source timestamps are validated but no
  row/message timestamps are written, so imported evidence carries no observation
  time. Arm tson (`evidenceTimestamps=true`): each row/message gets the event's
  real `event.ts` in ms.
- Role/source policy is identical in both arms (driver SourceAuthorityV1: locomo
  user and assistant events both imported as user evidence).

## Cost bounds (per arm)

| Bound | Value |
| --- | --- |
| Environments | 1 (conv-41) |
| Sessions ingested | 32 |
| Events | 663 (103,579 chars) |
| Extraction chunks / LLM extraction calls | **64 (tsoff), 68 (tson)** |
| Answer calls | 6 |
| Preflight connectivity probe | 1 |
| Chunk budget | 8000 chars (`maxImportChunkChars`), `maxExtractionTokens=32000` |

Chunk estimate method: exact Python replication of the driver's
`write_session_files` rows plus `jobs.rs` import-chunk packing (labels, URIs,
UTF-16 budgets). It reproduces the historical campaign conv-41 ingest exactly
(33 chunks at the legacy 15000-char config in `runs/locomo/s2`). The tson arm
estimates slightly higher because timestamped evidence labels are larger, which
can shift chunk boundaries; the two arms are deliberately NOT chunk-matched.

## BLOCKER: the <=20 planned extraction chunks/arm gate cannot be met

- conv-41 has 32 sessions; the driver creates one import job per session and at
  least one extraction chunk per job, so **>=32 chunks/arm is a floor at any
  chunk budget** (verified: 32 chunks even at the 75000-char cap). At the
  baseline 8000-char budget the plan is 64/68 chunks per arm.
- This exceeds the planned <=20 gate, so per instruction the blocker is reported
  **before** choosing any replacement. No replacement env was selected and no
  silent cherry-picking was done; the delivered manifests keep the rule-correct
  rank-1 env (conv-41).
- Parent options (cost gate and settings are the parent's call):
  1. Approve the actual cost for conv-41 at the baseline config: ~64/68
     extraction + 6 answer + 1 preflight calls per arm (132 extraction calls
     total across both arms). Or move to `maxImportChunkChars=15000` (the older
     baseline): 36/37 chunks per arm.
  2. Approve a replacement env. Only conv-30 (hash rank 9 of 10, 19 sessions,
     4 temporal dev questions, 17 dev questions) fits <=20 chunks at 15000
     (19 chunks). Every other dev env has 25-32 sessions and cannot fit <=20.
     Full per-env table is in `selection.json`/below. Say the word and the same
     artifacts are regenerated for it in minutes.
  3. Reduce sessions per env (not possible without dropping history, which the
     task forbids: full conversation history is required).

Outcome-blind per-env dev metadata (sessions / chars / dev questions / temporal
cat-2 dev questions / est. chunks at 8000 ts-off):

| rank | env | sessions | chars | dev q | cat2 | chunks@8000 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | conv-41 | 32 | 103,579 | 29 | 4 | 64 |
| 2 | conv-44 | 28 | 97,009 | 23 | 4 | 65 |
| 3 | conv-48 | 30 | 89,125 | 36 | 6 | 64 |
| 4 | conv-42 | 29 | 84,812 | 40 | 6 | 59 |
| 5 | conv-50 | 30 | 93,877 | 31 | 5 | 60 |
| 6 | conv-43 | 29 | 102,062 | 37 | 4 | 64 |
| 7 | conv-49 | 25 | 71,882 | 29 | 5 | 52 |
| 8 | conv-47 | 31 | 93,148 | 28 | 5 | 65 |
| 9 | conv-30 | 19 | 50,864 | 17 | 4 | 37 (19 @15000) |
| 10 | conv-26 | 19 | 70,184 | 30 | 5 | 41 (23 @15000, 19 @40000) |

## Exact CLI/API used (all offline, project interpreter, no model calls)

```
# from the repo-continuation root, TEMP/TMP/TMPDIR -> <campaign>/tmp-continuation
bench/.venv/Scripts/python.exe -B -m bench.converters.run_converters --family locomo     --data-root <campaign>/data --out-dir reports/continuation/c04-temporal-inputs/common
bench/.venv/Scripts/python.exe -B -m bench.prepare_questions --bench locomo     --qs-dir reports/continuation/c04-temporal-inputs/scoring     --out reports/continuation/c04-temporal-inputs/declarations/locomo-c04-model-questions.jsonl
bench/.venv/Scripts/python.exe -B reports/continuation/c04-temporal-inputs/validation.py
```

Python APIs: `bench.converters.locomo.build` (via the CLI above),
`bench.evidence.prepare_question_declaration` (via `bench.prepare_questions`),
`bench.protocol.evaluator_identity` (offline identity probe for `sources.json`).
Driver contract read from source:
`crates/pi-coding-agent/examples/memory_bench.rs` (sha256
`9ab215d92ea737c997341ab351e6618f4bedfb5dcaf40a925f840d2a15c5b0e7`, matches the
applied temporal-authority patch receipt).

## Launch contract (parent-owned)

```
# per arm, from the repo-continuation root, TEMP/TMP/TMPDIR redirected:
MEMORY_BENCH_MODELS_JSON=<explicit models.json> <memory_bench binary>     reports/continuation/c04-temporal-inputs/manifests/locomo-c04-tsoff-s0p1.json
MEMORY_BENCH_MODELS_JSON=<explicit models.json> <memory_bench binary>     reports/continuation/c04-temporal-inputs/manifests/locomo-c04-tson-s0p1.json
```

The driver creates `runs/locomo-c04-tsoff-s0p1/` and `runs/locomo-c04-tson-s0p1/`
itself (scratch envs, noJev `_control` isolation, fresh-output gate). One arm at
a time if campaign consumer slots require it. Scoring later (offline,
deterministic locomo F1; no judge network needed for these eval types):

```
bench/.venv/Scripts/python.exe -B -m bench.eval.evaluate     --run .../runs/locomo-c04-tsoff-s0p1/run.jsonl --bench locomo --variant c04tsoff     --dedup last --qs-dir reports/continuation/c04-temporal-inputs/scoring --out <scores>.json
```

## Limitations

- 4 temporal questions is the entire conv-41 temporal dev pool (bound was <=8).
  With 2 controls the per-arm question set is 6; differences between arms on 4
  temporal items are coarse-grained evidence only.
- The two arms use independent fresh capture stores. Extraction is an LLM call
  per chunk: chunk boundaries, extraction outputs and store contents vary between
  arms and runs (tson labels are slightly larger, shifting boundaries). This is
  inherent variance, not a controlled shared-store retrieval comparison.
- The chunk estimate is a pre-launch plan; actual counts are recorded by the
  driver (`ingest_llm_calls` in `env.jsonl`).
- `scoring/` and `common/locomo/full/` are private content-bearing evidence
  (gold). Do not publish; gold is never printed in reports.
- Manifest settings changes after approval (e.g. chunk budget) require
  regenerating and re-hashing the manifests; `sources.json` pins current hashes.
