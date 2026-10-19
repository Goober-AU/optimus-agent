# Context-rot and compaction-fidelity harness

Offline-first benchmark harness for measuring how an Optimus-style agent degrades
on long contexts, and how much evidence survives compaction. It can also drive a
real model through any OpenAI-compatible endpoint.

Everything here is deterministic, seeded and offline by default. `selftest` and
the unit tests never touch the network.

## Quick start

```bash
# from the worktree root
python -m research.context_rot.cli list
python -m research.context_rot.cli plan  --suite needle-smoke --seed 1234
python -m research.context_rot.cli run   --suite needle-smoke --adapter fake --quiet
python -m research.context_rot.cli report --print
python -m research.context_rot.cli selftest
python -m unittest discover -s research/context_rot/tests -t .
```

`run` writes a new directory under `research/context_rot/runs/`. `selftest` does
the same and then runs 15 end-to-end checks with the fake adapter.

## Layout

```
research/context_rot/
  __init__.py
  README.md
  cli.py            subcommands: list, plan, run, report, selftest
  corpus.py         seeded filler text (prose, logs, code, json, table) + token estimator
  tasks.py          items, gold records, visible/gold boundary, context assembly, truncation
  needles.py        exact-lexical, semantic-paraphrase, low-overlap-semantic, multi-fact
  distractors.py    none | one | multiple_related | contradictory | irrelevant_filler | topically_similar
  adapters.py       ModelAdapter protocol, OpenAICompatAdapter, OfflineEchoAdapter, FakeAdapter
  scoring.py        extraction rules, per-kind matching, abstention, unsupported claims, bootstrap CI
  harness.py        run_one / run_suite, concurrency, JSONL persistence, resumability
  multicycle.py     compaction-cycle simulator, Compactor protocol, deterministic compactor
  stress.py         stress categories A..T
  report.py         markdown + CSV writers, accuracy-vs-length, degradation vs baseline
  tests/            unittest suites (pytest-collectable, no network)
  runs/             gitignored output root
```

## Design guarantees

### 1. Determinism

Generation uses only `random.Random(seed)` instances, never the global RNG, the
clock or the network. `generate_suite(suite, seed)` twice gives byte-identical
JSON (`tests/test_tasks.py::DeterminismTest`). Suites:

| suite | items |
| --- | --- |
| `needle-smoke` | 4, one per needle family |
| `needles` | 8, two per needle family |
| `stress` | 20, one per stress category A..T |
| `mixed` | `needle-smoke` + `stress` (24) |

### 2. Gold isolation

`VisibleItem` holds only model-visible fields; `GoldRecord` holds the answer,
accepted aliases, sub-facts, provenance and a SHA-256 `gold_fingerprint`
(16 hex chars). The model-visible payload is built by exactly one function:

```python
visible_payload(visible: VisibleItem, context_text: str) -> dict   # keys: context, question, answer_kind
```

It takes no `Item` and no `GoldRecord`, so the structural boundary cannot be
bypassed by accident. On top of that, every generated item runs
`assert_gold_absent(text, gold)`:

* all needle answers are **derived** values (suffix rules, clock arithmetic,
  doubling, products, differences), so the answer text does not appear verbatim;
* a boundary-aware scan (`(?<![0-9a-z])gold(?![0-9a-z])`, case-insensitive)
  rejects collisions such as `48` inside `1480`;
* clock literals (`21:45:53`) are masked before scanning a numeric gold, so a
  gold of `45` does not "match" a synthesized timestamp minute. Time golds
  (which contain a colon) are scanned against the unmasked text, so a real
  clock-answer leak still fails the check;
* colliding filler is regenerated with a deterministic number shift
  (`CorpusGenerator(number_shift=...)`), at most 6 attempts;
* contradictory distractors are built from visible parameters only and reject
  any candidate that collides with a gold string.

`tests/test_tasks.py::GoldIsolationTest` and `test_stress.py` scan the rendered
prompt, the context and the visible item dict for every gold string and fail on
a hit. Aliases are accepted surface variants and are not scanned: a needle may
legitimately spell the answer as words (`twelve`) while the gold is `12`.

### 3. Answer extraction and normalisation

`scoring.extract_answer(raw, answer_kind, json_style)` tries rules in order and
reports which one fired in `extraction_rule` (plus `extraction_detail`):

| rule | trigger |
| --- | --- |
| `json:field` | a JSON object with `answer`/`final`/`result`/`value` |
| `delimiter:final_answer` | `FINAL ANSWER:` / `ANSWER:` / `FINAL:` (last occurrence) |
| `delimiter:brackets` | `[[value]]` |
| `last_line` | last non-empty line (value pulled by kind) |
| `last_time` / `last_number` | first clock/number in the trailing text |
| `whole_text` | nothing above matched |
| `empty` | blank completion |

Reasoning preambles are stripped: answer preambles (`the answer is`, `so,`,
`therefore`) and reasoning markers are detected (`extraction_detail["stripped_reasoning"]`).
This matters because the DGX DeepSeek endpoint sometimes emits reasoning inside
`content` before the final line.

### 4. Truncation is never silent

`InstanceConfig.max_prompt_tokens` caps the rendered prompt. When the context
must be shortened, the record carries:

* `truncated: true`
* `tokens_original_est` and `tokens_final_est`
* `truncate_strategy` (`head_tail` | `head_only` | `tail_only`)
* `evidence_survived` and `missing_markers`

If evidence markers are gone, the cell is **INVALID**: `correct: null` and
`invalid_reason: "truncated_evidence"`. It is never scored as a wrong answer and
never enters accuracy. The same rule applies to compaction
(`invalid_reason: "compaction_lost_evidence"`).

### 5. Token counts are estimates

`corpus.TOKEN_ESTIMATOR = "estimate_v1:ceil(chars/4 + digits/12 + newlines/2)"`

* base: `chars / 4.0`
* digits: `digits / 12.0` (ids, hex, timestamps pack denser)
* structure: `newlines / 2.0`
* rounded up, minimum 0; `estimate_messages_tokens` adds 4 tokens per message

Every record carries `token_estimator`, `token_is_estimate: true` and
`prompt_tokens_source: "estimate"`. API-reported usage is stored separately in
`usage` (`usage.prompt_tokens` etc.) and is never mixed with the estimate.
`context_target_len` is a target in **estimated tokens**; generated contexts land
within roughly 10-20 % of it because the value is an estimate, not a tokenizer.

### 6. Result record

`results.jsonl` (one JSON object per cell) always contains: `suite`, `item_id`,
`instance_id`, `category`, `seed`, `model`, `provider`, `adapter_kind`,
`context_target_len`, `evidence_position`, `distractor_kind`,
`requested_json_style`, `prompt_tokens`, `usage`, `latency_ms`,
`extraction_rule`, `extraction_detail`, `correct` (`true|false|null`),
`invalid_reason`, `raw_answer`, `normalized_answer`, `gold_fingerprint`,
`truncated`, `tokens_original_est`, `tokens_final_est`, `evidence_survived`,
`abstained`, `unsupported_claim`, `matched_facts`, `missing_facts`,
`adapter_synthetic`, `error`, `token_estimator`, `timestamp`.

`correct: null` means INVALID (truncated evidence, compaction loss, adapter
error, gold-leak build failure), never "wrong".

### 7. Runs and resumability

Every run creates `runs/<utc-stamp>-<slug>/` containing:

| file | content |
| --- | --- |
| `manifest.json` | git HEAD + branch + dirty flag, python version, platform, full args, seeds, instance configs, counts, timestamps, status, summary |
| `results.jsonl` | one record per cell, appended and fsynced per cell |
| `results.csv` | flat view of the same records |
| `summary.md` | totals, accuracy vs length, degradation vs baseline, per position/distractor/category tables, extraction-rule and invalid-reason counts, truncation accounting, caveats |
| `cycles.jsonl` | only for `--cycles` runs (same manifest fields, including git HEAD, python version, args and seeds) |

Existing directories are never reused: if a new run lands on the same second the
id gets a `-2`, `-3`, ... suffix. An explicit `--run-dir` that is not empty is
refused (`RunDirExistsError`, exit code 2) unless `--resume` is passed.

### 8. Compaction fidelity

`multicycle.py` defines a `Compactor` protocol and a
`DeterministicTruncatingCompactor` (no model): keep the system message, a
truncated summary of dropped messages, and the last `keep_tail` messages. The
simulator asks the item after every cycle and records `evidence_survived`,
`compacted`, `dropped_messages`, token accounting and the score. `--cycles N`
runs this mode and writes `cycles.jsonl` plus a compaction table in `summary.md`.

## Stress categories A..T

| | | | |
| --- | --- | --- | --- |
| A buried constraint | B early rationale | C correction override | D similarly named files |
| E evidence in long bash output | F evidence in a git diff | G subagent state | H partial vs success |
| I updated acceptance criteria | J source revision change | K multiple long subtasks | L compaction during tool call |
| M repeated summaries accumulating errors | N semantically similar irrelevant docs | O long sessions with unrelated requests | P three-fact multi-hop |
| Q must-reject old fact | R exact command / error reproduction | S model switching | T restart after repeated compaction |

Each category has one canonical item, a derived gold value and its own
provenance (`module`, `generator`, `seed`, `derivation`, `params`). Answer kinds
cover numeric, time, code and multi-fact.

Limitation, stated plainly: the category templates are canonical, so the seed
changes the filler text, the evidence placement, the distractor seeds and the
provenance seed, but not the template numbers of a stress item. Needle suites
vary fully with the seed (`tests/test_tasks.py` asserts that a different seed
gives different filler and different needle parameters).

## CLI reference (only implemented flags are listed)

Global: `--version`. Every subcommand has `-h`.

### `list [--json]`

Prints suites, needle kinds, stress categories, distractor kinds, positions,
truncate strategies, extraction rules, adapters, compactors and the token
estimator. `--json` prints the same as JSON.

### `plan [--suite NAME] [--seed N] [--limit N] [--context-lens CSV] [--positions CSV] [--distractors CSV] [--max-prompt-tokens N] [--truncate-strategy NAME] [--json]`

Dry run: item ids and the exact cell list that `run` would execute.

### `run`

| flag | default | meaning |
| --- | --- | --- |
| `--suite NAME` | `needle-smoke` | one of the four suites |
| `--seed N` | `1234` | generation seed |
| `--adapter {fake,echo,openai}` | `fake` | `fake`/`echo` are offline; `openai` calls a real endpoint |
| `--model NAME` | `fake-adapter` | model name sent to the endpoint |
| `--provider NAME` | `openai-compat` | provider label stored in records |
| `--base-url URL` | none | e.g. `http://127.0.0.1:43120/v1` (adapter appends `/chat/completions`) |
| `--api-key KEY` | `$OPENAI_API_KEY` | bearer token |
| `--extra-header "Name: value"` | none | repeatable extra HTTP header |
| `--timeout SECONDS` | `120` | request timeout |
| `--temperature F` | `0.0` | sampling temperature |
| `--max-tokens N` | none | completion cap |
| `--context-lens CSV` | `2000,8000` | context targets in estimated tokens |
| `--positions CSV` | `middle` | `start,early,middle,late,end` or a 0..1 fraction |
| `--distractors CSV` | `none` | distractor kinds to cross with positions |
| `--json-style` | off | request a JSON answer object |
| `--max-prompt-tokens N` | none | truncate contexts above this prompt budget |
| `--truncate-strategy NAME` | `head_tail` | `head_tail`, `head_only`, `tail_only` |
| `--concurrency N` | `1` | worker threads |
| `--limit N` | none | first N suite items |
| `--runs-root DIR` | `research/context_rot/runs` | output root |
| `--slug TEXT` | `run` | directory suffix |
| `--run-dir DIR` | none | explicit new dir, or existing dir with `--resume` |
| `--resume` | off | skip cells already in `results.jsonl` |
| `--cycles N` | `0` | run N compaction cycles instead of one shot |
| `--compactor NAME` | `deterministic` | only `deterministic` is implemented |
| `--compactor-keep-tail N` | `2` | messages kept verbatim by the compactor |
| `--compactor-summary-chars N` | `1200` | characters of dropped messages kept in the compaction summary |
| `--budget-tokens N` | `2500` | compaction trigger, estimated tokens |
| `--fake-accuracy F` | `1.0` | fake adapter base accuracy |
| `--fake-length-penalty F` | `0.0` | fake accuracy loss per 1000 prompt tokens |
| `--quiet` | off | no per-cell progress lines |

Number of cells = `items x context-lens x positions x distractors`.

### `report [--run-dir DIR] [--runs-root DIR] [--out PATH] [--csv PATH] [--print]`

Re-renders `summary.md` and `results.csv` from `results.jsonl` + `manifest.json`.
Without `--run-dir` the latest run under `--runs-root` is used. `--print` writes
the markdown to stdout instead of discarding it. A `--cycles` run has no
`results.jsonl`; for those directories `report` renders the compaction table from
`cycles.jsonl` into `summary-multicycle.md`.

### `selftest [--seed N] [--suite NAME] [--runs-root DIR]`

Offline end-to-end check with `FakeAdapter`. Runs a suite, then asserts:
determinism, gold isolation, record completeness, artefacts written, manifest
git HEAD and python version, refusal of an occupied run dir, truncated evidence
becoming INVALID, compaction cycles running, compaction retention gradient
(start survives, end dies), report rendering and extraction-rule firing. Exit
code 1 on any failure.

## Driving a real model

```bash
python -m research.context_rot.cli run \
  --suite mixed --seed 1234 --adapter openai \
  --base-url http://127.0.0.1:43120/v1 --model DeepSeek-V3.2 \
  --context-lens 2000,8000,32000 --positions early,middle,end \
  --distractors none,contradictory,topically_similar \
  --concurrency 4 --timeout 180 --slug deepseek-mixed
```

The adapter posts `{model, messages, temperature, stream:false}` (plus
`max_tokens` and `response_format={"type":"json_object"}` when configured) and
stores `usage` verbatim. Transport and protocol failures become
`invalid_reason: "adapter_error"`; they never abort the run.

No real-model run is included in this repository. Every number produced so far
came from `FakeAdapter`, which is a scripted responder, not a model: it looks the
answer up in a table supplied by the harness and injects deterministic errors.
Its only purpose is to prove the plumbing and exercise every scoring path.
Reports from synthetic adapters say so at the top.

## Fake adapter behaviour

`p_correct = clamp(base_accuracy - length_penalty_per_1k * (prompt_tokens / 1000), 0, 1)`

The draw is a SHA-256 of (seed, question, prompt tokens, json style), so it is
stable across runs and varies per cell. Output style is deliberately varied:
bare value, `FINAL ANSWER: value`, reasoning preamble plus value, and reasoning
plus delimiter, plus JSON when requested. Predictable misses and abstentions let
the harness exercise wrong-answer, abstention and extraction paths offline.

## Scoring notes and limits

* numeric matching compares parsed numbers; `1,200` matches `1200`
* time matching accepts `HH:MM` and `HH.MM`
* code/exact matching is case-insensitive, tolerates a trailing answer sentence,
  and accepts an alias containing the value
* multi-fact scoring matches each fact by kind and is order-free; it reports
  `matched_facts` / `missing_facts`
* abstention patterns (`ABSTAIN`, `I don't know`, `not in the context`, ...) are
  recorded as `abstained` and scored as a normal miss
* `unsupported_claim` is a heuristic: it needs at least two alphabetic content
  words in the answer and fires when none of them occurs in the context. Ids,
  numbers and short derived values are not judged, because derived answers are
  expected to be absent by design
* degradation is paired by `item_id` against the shortest context length
  (percentile bootstrap, 2000 iterations, seed 1234). Deltas are only computed
  for valid cells on both sides

## Tests

```bash
python -m unittest discover -s research/context_rot/tests -t .   # 125 tests, ~3 s
python -m unittest research.context_rot.tests.test_scoring -v     # one module
```

They cover determinism, gold isolation (prompt, context, visible dict),
extraction rules, kind matching, abstention, unsupported claims, bootstrap CI,
truncation-to-INVALID, run-dir creation and refusal, resume, record completeness,
compaction retention gradient, stress-category coverage, report tables, adapter
request shape (with an injected fake HTTP session, no network) and the CLI.

## Not implemented

* no tokenizer measurement (estimates only)
* one compactor implementation; the protocol is there for others
* no cross-run comparison command (`report` works per run directory)
* no automatic upload or chart rendering; markdown and CSV only
