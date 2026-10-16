# Communication benchmark survey: SILO-BENCH (primary) and MARBLE (secondary)

Worker: bench-official. Worktree `improve/occ2-occ3-communication` (base v0.1.32).
Purpose: pick real, offline-runnable multi-agent communication benchmarks for
the OCC-2/OCC-3 campaign and document exactly what they measure and what it
takes to run them on this Windows host against the local model gateway
(`http://127.0.0.1:43120/ollama/v1`, models glm-5.3 / glm-5.3-flash).

Clones (read-only, pinned):

| repo | path | pin |
|---|---|---|
| SILO-BENCH | `C:\Users\openclawuser\optimus-rlm-references\silo-bench` | `e74127782ed1c42fff474249961f022c063d76f2` |
| MARBLE | `C:\Users\openclawuser\optimus-rlm-references\MARBLE` | `8d60fa17b5596b44458a52d4296061b9fc13d6f2` |

## 1. SILO-BENCH

"A Scalable Environment for Evaluating Distributed Coordination in Multi-Agent
LLM Systems" (arXiv 2603.01045). Role-free benchmark: N agents each hold a
private data shard, must discover a communication strategy themselves, and
every agent must submit the final answer. No pre-assigned roles, no leader
election baked in.

### 1.1 Repo layout

```
silo-bench/
  benchmarks/               # 180 pre-generated case files + silo_bench_full.json (97 MB)
    I-01_n2.json ... I-10_n100.json      # {case}_{agents} naming; one case instance per file
    benchmark_summary.json               # 30-case index with optimal topologies
  benchmark_generator/      # main.py + paradigm_i/ii/iii.py generators
  src/
    engine.py               # init_case (L67), run_round (L203), evaluate (L440), _count_messages (L536)
    models.py               # pydantic v2 models (AgentState, Context, CaseMetadata...)
    batch_run.py            # CLI matrix runner
    msg/ broadcast/ sfs/     # per-protocol tool implementations (tools.py, run_round.py, evaluate.py)
    utils/
      llm.py                # OpenAI-compatible client (L20 call_llm), tenacity infinite retry
      metrics.py            # S (L45), P (L96), C (L214), D (L238)
      parsing.py            # regex tool-call fence parser (L14 TOOL_CALL_RE)
      prompts.py            # protocol system prompts with tool docs (L259 generate_system_prompt)
      config.py             # env-var/yaml config (SILO_API_BASE / SILO_API_KEY / SILO_MODEL)
  configs/config.example.yaml
  pyproject.toml            # requires-python >= 3.13; openai, pydantic, pyyaml, srsly, tenacity
```

### 1.2 Task schema (real example, `benchmarks/I-01_n2.json`, "Global Max")

Top-level keys: `case_id`, `case_name`, `paradigm`, `leetcode`, `metadata`
(num_agents, optimal_topology, optimal_message_count, theoretical_complexity,
is_segmented), `task_description` (template with `{agent_id}` / `{input_shard}`),
`agent_configs` (one per agent), `expected_output`, `evaluation_metrics`.

`agent_configs[i]` carries `agent_id`, a `system_prompt` placeholder that the
engine overrides at runtime with the protocol prompt, a rendered `user_prompt`
containing the private shard, and the raw `input_shard`. Real excerpt:

```json
{
  "agent_id": 0,
  "user_prompt": "**Task: Global Maximum**\n\nFind the GLOBAL MAXIMUM value across all agents' data.\n\n**Your Data:**\nYou (Agent 0) hold: [309, -772, ..., -809]\n\n**Algorithm:**\n1. Compute the maximum in your local shard\n2. Coordinate with other agents to determine the global maximum\n\n**Output:**\nA single integer representing the global maximum value.\n\n**Communication Protocol:**\nTopology: Star/Tree aggregation -> broadcast.\nAll agents must submit the same final answer using submit_result(global_max).",
  "input_shard": [309, -772, -949, 518, -437, -499, -543, -715, 508, -791, 385, 516, 827, 116, -822, 209, -136, -935, -939, -809]
}
```

`expected_output` for the distributed tasks is
`{"type": "distributed", "per_agent_values": [827, 827], "is_segmented": false,
"verification_logic": "len(agent_outputs) == num_agents and all(...)"}`; for
segmented Level II/III tasks the per-agent values are lists (e.g. II-11 Prefix
Sum per-agent segment lists; III-21 Distributed Sort sorted sublists).

### 1.3 Task families

30 cases x 6 agent counts (2/5/10/20/50/100) = 180 files:

- Level I (I-01..I-10) Aggregation, O(N): global max, word frequency, vote,
  any-match, range count, XOR checksum, average, set-union size, top-K, std dev.
- Level II (II-11..II-20) Mesh/stencil, O(N): prefix sum, moving average,
  cross-boundary palindrome, 1D life game, pattern search, trapping rain,
  diff array, list ranking, merge neighbors, pipeline hash (segmented outputs).
- Level III (III-21..III-30) Global shuffle, O(N log N)-O(N^2): distributed
  sort, median of medians, graph components, BFS distance, k-means iteration,
  global distinct, collaborative filtering, PageRank step, load balance,
  matrix multiply (segmented).

The generator (`benchmark_generator/main.py`, paradigm_i/ii/iii.py) can emit
fresh instances; the committed `benchmarks/` files are the official set.

### 1.4 Multi-agent structure and message flow

- Round-synchronized lockstep. `init_case` (engine.py L67) writes round-000000
  with each agent's context (protocol system prompt + private-shard user
  prompt). `run_round` (L203) advances one round: for each not-yet-submitted
  agent, one LLM call, parse tool calls, execute tools, append tool results as
  the next user message.
- Messages written in round t become visible in round t+1 (`receive_messages`
  scans all earlier rounds for unread messages; msg/tools.py L45).
- Agents communicate only through protocol tools; the engine itself never
  edits message content. Inter-agent traffic is plain natural language by
  default (this is exactly OCC-0).
- Termination: all agents called `submit_result`, or max_rounds (default 100).
- Tool-call protocol: the model must emit fenced XML blocks matching
  `TOOL_CALL_RE` (src/utils/parsing.py L14); parameters are parsed with
  `<parameters><name>value</name></parameters>` XML. `wait` and
  `submit_result` are round-ending (engine.py L319 ROUND_ENDING_TOOLS).
- Protocols: `msg` = P2P directed messages (`send_message`, `receive_messages`,
  `wait`, `submit_result`); `broadcast` = one message to all; `sfs` = shared
  key-value file system (`list_files`, `read_file`, `write_file`,
  `delete_file`, `wait`, `submit_result`).

### 1.5 Official metrics (file:line)

All in `src/utils/metrics.py`, computed by `engine.evaluate` (L440) into
`results.json` per case:

| metric | definition | code |
|---|---|---|
| S success rate | fraction of agents whose normalized submission equals `per_agent_values[i]` | compute_success_rate, metrics.py L45; normalization `_normalize_value` L16 (parses int/float/JSON from strings) |
| P partial correctness | per-level continuous quality: I = fraction of agents within 1% tolerance; II = per-segment element accuracy; III = longest correctly ordered subsequence / expected length | compute_partial_correctness L96 (LIS helper L81) |
| C token consumption | (total input + output tokens) / rounds executed | compute_token_consumption L214 |
| D communication density | total messages / (N * (N-1)) | compute_communication_density L238; message counting engine.py L536 |

A case is `success: true` iff S == 1.0 (all agents correct). Submissions are
read from the final round directory (evaluate, engine.py L455-470); a missing
submission counts as wrong.

### 1.6 Runtime dependencies and endpoint compatibility

- Python >= 3.13 (pyproject). The adapter venv uses CPython 3.14.3; the code
  itself contains no 3.12+ syntax (verified by AST parse with
  feature_version=(3,11), 0 issues in src/).
- Deps: openai, pydantic v2, pyyaml, srsly, tenacity (+ httpx transitively).
  All install cleanly on 3.14 via uv.
- LLM access: `src/utils/llm.py` (L11 call_llm) uses the official OpenAI SDK against
  `SILO_API_BASE`/`SILO_API_KEY`/`SILO_MODEL` (env > yaml > default). Plain
  `chat.completions.create(model, messages)`; no native function calling, no
  streaming, no temperature. Fully compatible with an OpenAI-compatible
  chat endpoint such as our gateway.
- Offline: yes. Tasks are committed JSON; no network beyond the model API.

### 1.7 License

LICENSE file is the Unlicense ("free and unencumbered software released into
the public domain"). Note a discrepancy: pyproject.toml declares `MIT`; the
README badge and LICENSE file say Unlicense. Either way, permissive/public
domain; running and adapting it locally is unambiguous.

### 1.8 Feasibility on this host (verified, not estimated)

Verified by the pilot in `research/benchmarks/adapter/` and
`PILOT-REPORT.md`: the official engine runs unmodified against the gateway
with two Windows-host caveats, both handled in the adapter:

1. GLM tool-call stripping. The gateway extracts SILO's fenced XML tool-call
   blocks from the model's visible output and re-packages them as native
   `tool_calls` (finish_reason="tool_calls", empty `message.content`). The
   official engine parses only `message.content`, so GLM rounds degenerate
   into "No valid tool calls detected" loops. deepseek models pass the fence
   through natively. Adapter fix: a localhost content-reconstruction proxy
   (`adapter/gateway_proxy.py`) that forwards requests verbatim and merges
   the extracted blocks back into `message.content`. Requests are never
   modified, so `usage.prompt_tokens` stay the real server-side counts.
2. Infinite client retry. `call_llm` retries forever on any exception
   (tenacity `@retry(wait=wait_fixed(2))`, llm.py L10), and the SDK default
   retry adds more. A hung case would never end. Adapter fix: per-case
   subprocess with a hard wall-clock timeout (`adapter/pilot_run.py`).

Cost note: `usage.completion_tokens` at this gateway includes hidden
reasoning tokens, so output-token totals overestimate visible wire volume
and must not be used for wire-cost claims (flagged in every scorecard).

## 2. MARBLE

"MultiAgentBench: Evaluating the Collaboration and Competition of LLM agents"
(arXiv 2503.01935). MARBLE is the follow-up codebase: a modular multi-agent
framework with several environments, an LLM planner, and LLM-judged metrics.

### 2.1 Repo layout (top level)

```
MARBLE/
  marble/
    main.py                 # CLI entry: --config_path -> Config -> Engine.start()
    engine/engine.py        # 1172-line engine: star/graph/chain/tree coordinate modes
    agent/base_agent.py     # BaseAgent: perceive/act, send_message/receive_message
    agent/werewolf_agent.py + werewolf_prompts/*.yaml
    environments/           # base, minecraft, database (db), coding, research, werewolf, world, web
    environments/minecraft_utils/  # server + client + build_judger
    environments/db_env_docker/    # docker-compose, Postgres init, prometheus
    evaluator/evaluator.py         # LLM-judge metrics (618 lines)
    evaluator/werewolf_evaluator.py
    graph/agent_graph.py    # communication graph
    llms/model_prompting.py # litellm wrapper (model_prompting)
    memory/                 # shared/short-term/long-term memory
    configs/                # test_config*.yaml, coding_configs/, werewolf_config.zip
  multiagentbench/          # task jsonl per env: minecraft_main.jsonl (100), coding_main.jsonl,
                           # database_main.jsonl, research_main.jsonl, bargaining_main.jsonl
  scripts/                 # per-env run scripts (bash)
  data/                    # map.json, map_description.json, score.json (minecraft maps)
  pyproject.toml           # poetry, python >=3.9,<3.12, litellm, beartype, scikit-learn,
                           # psycopg2, pymysql, keybert, arxiv, semanticscholar, ...
```

### 2.2 Task format (real example, `multiagentbench/minecraft/minecraft_main.jsonl`, line 1)

One JSON object per line; 100 lines for minecraft. Real excerpt (abridged):

```json
{
  "agents": [
    {"agent_id": "agent1", "agent_port": 5000, "type": "BaseAgent",
     "profile": "agent1 is a team member good at finding correct materials in the container and place the block in the correct place..."},
    {"agent_id": "agent2", "agent_port": 5001, "type": "BaseAgent", "profile": "..."},
    {"agent_id": "agent3", "agent_port": 5002, "type": "BaseAgent", "profile": "..."}
  ],
  "coordinate_mode": "graph",
  "engine_planner": {"initial_progress": "Starting the simulation."},
  "environment": {"host": "localhost", "max_iterations": 20, "name": "Minecraft Environment",
                  "port": 25565, "task_id": 0, "task_name": "test", "type": "Minecraft"},
  "llm": "gpt-4o-mini",
  "memory": {"type": "SharedMemory"},
  "metrics": {"evaluate_llm": {"model": "gpt-3.5-turbo", "provider": "openai"}},
  "output": {"file_path": "result/minecraft/discussion_output_gpt-4o-mini_0.jsonl"},
  "relationships": [["agent1", "agent2", "collaborate with"], ["agent1", "agent3", "collaborate with"], ["agent2", "agent3", "collaborate with"]],
  "task": {"content": "This is in the game of Minecraft. Build a building according to a blueprint. ... [blueprint entries: material / facing / position ...]"}
}
```

`jsonl2yaml.py` converts these to per-run YAML configs
(`configs/test_config_minecraft/test_config_<model>_<i>.yaml` are committed
examples generated this way).

### 2.3 Multi-agent structure and message flow

- Engine (marble/engine/engine.py): `start()` dispatches to a coordinate mode
  (star = central planner, graph = agent-graph with LLM planner routing,
  chain, tree). Each iteration: assign task per agent -> `agent.act(task)`
  (LLM call with tools) -> collect results + inter-agent communications ->
  planner summarizes and assigns next tasks, up to `max_iterations` or task
  completion.
- Agents (marble/agent/base_agent.py): `perceive`/`act`, plus
  `send_message` (L319) / `receive_message` (L339) routed through the agent
  graph into shared memory; sessions accumulate per target agent.
- Communication is plain natural-language text between named agents with
  relationship edges ("collaborate with"), written to SharedMemory.

### 2.4 Official metrics

- Environment-grounded milestones (M-Score): for minecraft, the build_judger
  compares built blocks to the blueprint and produces milestone completion;
  the paper aggregates milestone achievement rates. For DB/coding/research,
  per-env evaluators (evaluator.py: `evaluate_kpi` L130, `evaluate_task_db`
  L284, `evaluate_task_research` L170, `evaluate_code_quality` L509).
- K-M-Score (LLM-judged): `evaluate_communication` (evaluator.py L67) and
  `evaluate_planning` (L95) send prompts from `evaluator_prompts.json` to the
  configured judge LLM and parse integer scores; `parse_score` L331. The
  judge model is configurable per task record (`metrics.evaluate_llm`).
- Token consumption: summed per-iteration from agents (`update`, evaluator.py
  L49).

Consequence: MARBLE scores are judge-model-dependent (the paper used
gpt-3.5/gpt-4o as judges). Running it here means glm-5.3 would judge glm-5.3
unless a separate judge model is configured.

### 2.5 Runtime dependencies

Poetry project, python >=3.9,<3.12 (3.11.15 on PATH is inside the range).
Heavy deps: litellm, beartype, scikit-learn, psycopg2-binary, pymysql,
keybert, arxiv, semanticscholar, pypdf2, levenshtein, javascript (js bridge).
Environment-specific extras: Minecraft (Java server jar + Node mineflayer
bridge per agent on ports 5000-5002 + server on 25565; scripts under
marble/environments/minecraft_utils), DB (docker-compose with Postgres,
prometheus; init.sql), research (network access to Semantic Scholar/arXiv).

### 2.6 License

MIT (LICENSE, (c) 2024 Haofei Yu). Citation asks to cite the MultiAgentBench
paper (Zhu et al., 2025).

### 2.7 Feasibility on this host (honest assessment)

- LLM layer: gateway-compatible in principle, with an open question.
  `model_prompting` (marble/llms/model_prompting.py) is a thin
  litellm.completion call; litellm's `openai/` provider accepts a custom
  base_url/api_key. Werewolf's evaluator and agents take
  base_url/api_key/model_name per config (e.g. werewolf_config_4o_mini.yaml).
  Caveat verified in source: the werewolf agent uses NATIVE function calling
  (`gpt_tool_call(messages, tools)` then `json.loads(tool_calls[0]
  .function.arguments)`, werewolf_agent.py L481 and L828), so the gateway
  must honor request-level `tools` and return well-formed native tool_calls.
  The gateway does return native tool_calls (it even synthesizes them from
  plain-text XML blocks), but request-driven function calling against it is
  UNTESTED here; that is the first thing to check before any werewolf run.
- Environments:
  - Minecraft (primary MARBLE benchmark): NOT runnable offline here without
    substantial setup - needs a Minecraft Java server, the mineflayer Node
    bridge (npm install, one process per agent), and the committed
    `scripts/` are Linux/bash oriented. The 100 committed task records are
    real and offline, but execution is not.
  - Database: needs Docker (Windows Docker Desktop) with the provided
    compose files and seeded Postgres; possible but heavyweight, and the
    committed per-model configs reference gpt-3.5/4o-mini/llama.
  - Coding: needs sandboxed code execution; configs committed.
  - Research: needs internet (Semantic Scholar, arXiv APIs). Not offline.
  - Werewolf: text-only social deduction, 9 roles (3 wolf, 3 villager, seer,
    witch, guard), multi-day voting game; pure LLM + name lists; the
    official task configs live in
    `marble/configs/test_config/werewolf_config.zip` (per-model YAMLs).
    Runnable in principle against the gateway on Python 3.11; cost is the
    constraint (a full game is dozens-to-hundreds of LLM calls across 9
    players and multiple days) and scoring is partially LLM-judged
    (werewolf_evaluator re-simulates from checkpoints and evaluates
    win/loss plus speech quality with an LLM judge).
- Windows caveats: repo paths inside configs use backslashes joined into
  bash scripts (run_simulation.sh has literal `marble\environments\...`
  in a bash context), .zip config delivery, and colorama/names deps. All
  fixable but none verified end-to-end here.

Verdict: MARBLE stays secondary. If the campaign wants a MARBLE datapoint,
werewolf is the only environment plausibly runnable offline on this host,
at meaningful setup + evaluation cost, with judge-model dependence. The
minecraft/database/coding/research suites are out of scope for an offline
Windows pilot.

## 3. Decision for the campaign

- Primary: SILO-BENCH. Real committed tasks, pure-LLM multi-agent loop with
  explicit inter-agent messages, official deterministic metrics, OpenAI-
  compatible by design, runs offline, permissive license. The adapter in
  `research/benchmarks/adapter/` runs it against the gateway today.
- Secondary: MARBLE (werewolf environment only, if budget allows; all other
  environments need external services).
- Our synthetic suite (research/tasks/, 300 two-agent tasks, families A-E)
  stays the fast iteration loop; SILO-BENCH adds an external, publishable
  anchor with independent scoring and multi-round multi-agent dynamics the
  synthetic suite does not have (N>2 agents, protocol constraints, round
  synchronization, per-agent submission unanimity requirement).
