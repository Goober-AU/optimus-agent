# SILO-BENCH adapter (OCC campaign)

Minimal, runnable adapter that executes the OFFICIAL SILO-BENCH engine
against the local OpenAI-compatible model gateway
(`http://127.0.0.1:43120/ollama/v1`, models `glm-5.3` / `glm-5.3-flash`).

Design rule: the adapter never reimplements SILO-BENCH logic. It imports
`init_case` / `run_round` / `evaluate` from the pinned read-only clone
(`silo_repo` in `config.json`, pin `e74127782ed1c42fff474249961f022c063d76f2`)
and only adds orchestration, timeouts, and scorecard writing.

## Files

| file | purpose |
|---|---|
| `config.json` | gateway endpoint, token file path, model names, bounded run specs (explicit task file lists) |
| `runner.py` | imports the official engine, runs one case or a whole run spec, writes per-case summary JSON |
| `pilot_run.py` | drives each case as a subprocess with a hard per-case timeout, then scores the run |
| `scorer.py` | aggregates official per-case metrics (S, P, C, D) plus adapter metrics (tokens, latency, message volume); writes JSON + markdown scorecards |

## Setup

```
uv venv --python 3.14 adapter/.venv
uv pip install --python adapter/.venv/Scripts/python.exe openai pydantic pyyaml srsly tenacity httpx
```

SILO-BENCH declares `requires-python >= 3.13`; the adapter venv uses 3.14.3.
All engine dependencies are installed from the official `pyproject.toml` list.

## Running

```
# single case (smoke)
adapter/.venv/Scripts/python.exe adapter/runner.py --config adapter/config.json \
    --run pilot-levelI-n2-msg-glm53 --task-file I-01_n2.json \
    --workspace runs/pilot-levelI-n2-msg-glm53/workspace \
    --summary-out runs/pilot-levelI-n2-msg-glm53/cases/case-I-01_n2.json

# full pilot run (per-case subprocess + timeout + scorecards)
adapter/.venv/Scripts/python.exe adapter/pilot_run.py --config adapter/config.json \
    --run pilot-levelI-n2-msg-glm53
```

Outputs land in `research/benchmarks/runs/<run_name>/`:
`cases/case-*.json` (per-case summaries), `workspace/` (official engine
artifacts: rounds, contexts, logs), `scorecard.json`, `scorecard.md`.

## What is official and what is adapter-added

- Official (imported, not modified): case init, round execution, tool dispatch,
  tool-call parsing, LLM client, metrics S/P/C/D, evaluation. Files:
  `src/engine.py`, `src/msg|broadcast|sfs/tools.py`, `src/utils/parsing.py`,
  `src/utils/llm.py`, `src/utils/metrics.py`, `src/utils/prompts.py`.
- Adapter-added: subprocess isolation with per-case wall-clock timeout
  (the official LLM client retries forever), per-case and per-LLM-call
  latency accounting from the official logs, inter-agent message byte
  volume, run-level aggregation, scorecard writing.

## Token accounting

- `prompt_tokens`: real server-side counts returned by the gateway in
  `usage.prompt_tokens` (summed by the official engine into
  `metadata.execution.total_input_tokens`).
- `completion_tokens`: the gateway counts hidden reasoning tokens in
  `usage.completion_tokens`, so output-token totals OVERESTIMATE visible
  wire volume and must not be used for wire-cost claims. This is flagged
  in every scorecard.

## OCC modes

The pilot runs `comm_mode: occ0_plain_english`: inter-agent messages pass
through the official engine verbatim, with no encoding applied (the
SILO-BENCH default). Later OCC-2 runs will add encoded-communication
variants at the same measurement points (message bytes, prompt tokens,
S/P accuracy), keeping the official engine as the loop.

Agents emit tool calls inside a fence parsed by the official regex
`src/utils/parsing.py:TOOL_CALL_RE`; an example block is:

```
<tool_call>
  <tool>send_message</tool>
  <parameters>
    <target_id>1</target_id>
    <content>my local max is 914</content>
  </parameters>
</tool_call>
```
