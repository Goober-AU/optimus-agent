# COMPACTBENCH ADAPTER DESIGN — running real Optimus Rust compaction under CompactBench

Worker W5. Date: 2026-10-19. Status: design complete; adapter NOT implemented
(this is a research work order; no Rust changes outside research/).

## 0. Provenance

| item | value |
| --- | --- |
| Upstream repo | https://github.com/compactbench/compactbench (pre-cloned at `C:/Users/openclawuser/optimus-context-rot-refs/compactbench`) |
| Pinned commit | `b2bb0e2254010e7842801b9101e56fd9a6542615` (2026-08-24) |
| Package version | 0.2.0 (`pip show compactbench` against the editable install) |
| License | Apache-2.0 |
| Public suites in repo | `benchmarks/public/starter` (4 templates), `benchmarks/public/elite_practice` (20 templates, 4 families x 5), `benchmarks/public/hard` (empty, `.gitkeep`) |
| Scorer | 2.0.0 semantics (weights locked in `docs/architecture/decisions.md` B3; drift formula changed from additive to ratio in 2.0.0, versions never mix) |

Offline validation performed during this work order (mock provider, no network):

```
# from the compactbench checkout, scratch venv at refs/cb-venv
compactbench run --method built-in:null --suite starter --provider mock \
  --model mock-deterministic --case-count 2 --drift-cycles 1 -o /tmp/cb-null-smoke.jsonl
compactbench run --method research/context_rot_r2/repro/compactbench_smoke_compactor.py:SmokeEchoCompactor \
  --suite starter --provider mock --model mock-deterministic --case-count 1 \
  --drift-cycles 0 \
  --benchmarks-dir /c/Users/openclawuser/optimus-context-rot-refs/compactbench/benchmarks/public \
  -o /tmp/cb-smoke.jsonl
```

Both ran to completion and wrote valid `results.jsonl` (null arm: overall 0.375 on
starter with mock eval; custom `SmokeEchoCompactor` loaded from an out-of-tree
file path and scored, buried_constraint case score 0.2 at 4.5x compression).
This proves the external-method resolution path and the offline loop end to
end on this host.

## 1. The compactor contract (exact shapes)

`compactbench.compactors.base.Compactor` (ABC):

```python
class Compactor(ABC):
    name: ClassVar[str]
    version: ClassVar[str]

    def __init__(self, provider: Provider, model: str) -> None: ...

    @abstractmethod
    async def compact(
        self,
        transcript: Transcript,
        config: dict[str, Any] | None = None,
        previous_artifact: CompactionArtifact | None = None,
    ) -> CompactionArtifact: ...
```

`Transcript` (pydantic, frozen, extra=forbid): `turns: list[Turn]`;
`Turn = {id: int >= 0, role: user|assistant|system, content: str, tags: list[str]}`.
The runner calls `transcript.without_tags()` before invoking the method: tags
mark `distractor` / `critical_constraint` and are an answer key, so the adapter
never sees them.

`CompactionArtifact` (frozen, camelCase aliases accepted on construction):

```python
CompactionArtifact(
    schemaVersion="1.0.0",
    summaryText: str <= 8000 chars,
    structured_state: StructuredState,   # six sections, all required, may be empty
    selectedSourceTurnIds: list[int],
    warnings: list[str] (<=50, each <=500),
    methodMetadata: dict,
)
StructuredState(
    immutable_facts: list[str] (<=200, each <=500),
    locked_decisions: list[str],
    deferred_items: list[str],
    forbidden_behaviors: list[str],
    entity_map: dict[str, str],
    unresolved_items: list[str],
)
```

Over-length `summaryText` must be pre-trimmed by the method (helper
`fit_summary_text`, which records a warning) or the run aborts with a pydantic
`ValidationError`.

Runner mechanics that constrain an adapter:

- One fresh compactor instance per case (`compactor_cls(counting_provider,
  model)` inside the per-case task), so per-instance state is per-case state.
- Per cycle: extend transcript with one (user, assistant) continuation pair
  built from the PREVIOUS artifact (drift vector), call `compact` on the
  extended transcript, then the target model answers every evaluation item
  using ONLY the artifact (`render_artifact_for_prompt`, fenced as
  untrusted data), then deterministic scoring.
- `--concurrency` cases run concurrently; token attribution per case via
  `CountingProvider`.
- `--resume` skips completed cases; `results.jsonl` is an append-only event log
  (`run_start`, `case_complete`, `run_end`).

## 2. Adapter design: real Optimus Rust compaction behind the contract

### 2.1 Shape

Two components, no changes to CompactBench:

1. **Rust harness binary** (new, would live in our repo as e.g.
   `crates/pi-coding-agent/src/bin/compaction_harness.rs`): reads one
   compact-request JSON on stdin, drives the REAL pipeline, writes one
   compact-response JSON on stdout, exits.
2. **Python adapter** `optimus_compactor.py`: a `Compactor` subclass that
   translates `Transcript` -> harness request, spawns the binary, translates
   the harness response -> `CompactionArtifact`. Invoked by the stock runner as
   `--method <path>:OptimusRustCompactor`.

### 2.2 What the Rust side must do (pattern proven by compaction_recovery.rs)

The offline driver pattern that already exists in
`crates/pi-coding-agent/tests/compaction_recovery.rs` is the template:

```
register_api_provider_simple(ApiProviderSimple { api, stream, stream_simple, compact, supports_compaction }, None)   // fixture summarizer
SessionManager::create(dir, Some(dir))                          // isolated temp root
manager.append_message(user_message(..)) / append_message(assistant_message(..))   // durable turns
should_compact_for_model(tokens, &model, &settings)             // trigger
prepare_compaction(&entries, &settings, &|_| tokens)            // -> first_kept_entry_id, messages_to_summarize
compact(&preparation, &summary_model, "unused", None, None, None,
        default_summary_call_runner(None), None, None).await   // -> summary, tokens_before, details
manager.append_compaction(&result.summary, &result.first_kept_entry_id,
                          result.tokens_before, details, None, None, None, None)
manager.flush_now()
build_session_context_with_entry_ids(None)                     // restored context
SessionManager::open(&session_file, None, None)                 // restart path
```

Harness request/response schema (JSON on stdin/stdout):

```
request:  { "session_dir": str,            // per-case persistent dir (adapter-managed)
            "new_turns": [ {"role":"user"|"assistant", "content":str, "turn_id":int} ],
            "settings": { "reserve_tokens": f64, "keep_recent_tokens": f64,
                          "task_state": "off"|"prompt" },
            "summarizer": "fixture" | {"endpoint": str, "model": str},
            "context_window": f64, "max_tokens": f64 }
response: { "summary": str, "first_kept_entry_id": str, "tokens_before": f64,
            "ledger": {...} | null,      // details.taskState when enabled
            "kept_turn_ids": [int], "status": "ok" | "unsafe" | "error" }
```

Turn-id bookkeeping: the adapter owns the mapping turn_id -> session entry (it
appends in order), so `selectedSourceTurnIds` can be exact: all summarized +
kept ids, or the kept tail only (decision below).

### 2.3 Cycle-to-cycle state

The runner reuses one compactor instance across a case's drift cycles and hands
it the extended transcript each time. The adapter keeps the per-case
`session_dir` open across `compact()` calls and appends only `new_turns` since
the last call (delta = turns after the last seen turn_id). The Rust session
itself is then the ledger: the previous compaction entry and `details.taskState`
carry forward exactly as in production, and `previous_artifact` can be ignored
(it is redundant with the session state). Restart semantics come free:
`SessionManager::open` on the same dir is the restore path the benchmark
exercises after compaction.

### 2.4 Artifact construction (mapping our output to their contract)

- `summaryText`: the Rust summary markdown (six fixed sections: Goal /
  Constraints & Preferences / Progress / Key Decisions / Next Steps / Critical
  Context), trimmed by `fit_summary_text` (their 8000-char cap; our summaries
  are well under it).
- `structured_state`: deterministic parse of (a) the summary's fixed sections
  and (b) the `details.taskState` ledger when `task_state = "prompt"`:
  - `forbidden_behaviors` <- ledger constraints phrased as prohibitions
  - `locked_decisions` <- ledger decisions
  - `unresolved_items` <- ledger open items
  - `deferred_items` <- ledger superseded (already-retired entries)
  - `immutable_facts` <- ledger evidence + anchors
  - `entity_map` <- not produced today; leave empty (contract allows empty)
  Section headers are fixed by `validate_summary`, so the parse is mechanical;
  no LLM involved. Parsing in the adapter (Python) keeps the Rust side
  untouched; a later Rust-side JSON emitter would remove the parser.
- `selectedSourceTurnIds`: turn ids whose session entries were consumed by the
  compaction (summarized window + kept tail). We control entry creation order,
  so ids are recoverable from `first_kept_entry_id` plus our turn-id map.
- `warnings`: surface `status != ok` and any cut-point fallback explicitly
  (never silent).

### 2.5 The subprocess boundary and why it is needed

- The pipeline lives in `pi-coding-agent` (Rust, tokio, `SessionManager` state
  on disk). CompactBench is Python/asyncio. The only clean seam without FFI
  risk is a process boundary: build the harness binary once
  (`cargo build -p pi-coding-agent --bin compaction_harness` against the
  shared `CARGO_TARGET_DIR`), invoke per `compact()` call.
- Cost: one process per call, ~tens of ms on this host, dwarfed by the model
  calls around it (mock runs excluded). If it ever matters, a long-lived
  JSON-lines server mode on the same binary (stdin stays open) is a drop-in
  extension; do not build it before profiling says so.
- The binary must not resolve profiles, start the daemon, or touch the network:
  fixture-only by construction (`register_api_provider_simple` + a fixture
  `Model` with a configurable `context_window`). The test file's
  `OPTIMUS_PARITY_STATE_ROOT` isolation pattern (asserted not under `.prime`)
  is the model for the harness's state root handling. `PI_PACKAGE_DIR` may
  need to be set explicitly for resource resolution outside a dev checkout;
  the existing `--build-provenance` flag shows the binary boots headless.

### 2.6 What is measurable offline, and what is not

Offline (no network, no keys), all validated or directly supported by fixtures:

- Adapter mechanics: turn mapping, session persistence across cycles, artifact
  construction, contract validation. Use `--provider mock` and a fixture
  summarizer; the compaction side is fully deterministic.
- Controls: `oracle`, `null`, `truncate-last-n` make no model calls at all.
- Costing: `compactbench run --estimate` projects calls/tokens without any
  provider call.

NOT measurable offline: any score that depends on the target model's answers
(`overall_score`, drift, constraint retention). With `--provider mock` the
evaluation answers are canned strings, so cycle scores are meaningless — mock
runs validate plumbing, never quality. Real scores need a real or local model
in both the summarizer and the evaluator. Per campaign rules (offline only, no
model calls, model-dependent claims BLOCKED), no CompactBench quality number
for Optimus compaction may be claimed from this campaign; only the harness
works, and it is ready for a model-backed run when one is authorized.

## 3. Built-in baselines and how to run the public suites offline

| method | mechanism | notes |
| --- | --- | --- |
| `naive-summary` | one LLM call, prose only | floor baseline |
| `structured-state` | one LLM call, JSON-forced, no prose | six sections map to `StructuredState` |
| `hierarchical-summary` | chunked map-reduce summarization | most LLM calls per case |
| `hybrid-ledger` | per-cycle JSON delta + accumulated ledger + short header | the strongest built-in on their local-model run |
| `oracle` / `null` / `truncate-last-n` | deterministic controls | zero model calls |

Offline command shape for the public practice suites (mock provider works
end-to-end; useful only for plumbing and free-point diagnostics):

```
compactbench run --method built-in:hybrid-ledger --suite elite_practice \
  --provider mock --model mock-deterministic --difficulty elite \
  --case-count 1 --drift-cycles 2 --seed-group <tag> -o out.jsonl
compactbench score --results out.jsonl
```

Their published maintainer baseline (local qwen2.5:1.5b-instruct via Ollama,
`elite_practice`, elite difficulty, 1 case/template, 2 drift cycles,
`baselines/qwen2.5-1.5b-instruct/manifest.yaml`) — THEIR numbers, reference
rows, not ours, and not reproducible offline without the local model:

| arm | overall | calls | tokens | full-context % |
| --- | --- | --- | --- | --- |
| oracle (ceiling) | 0.633 | 196 | 148,857 | 100% |
| hybrid-ledger | 0.432 | 316 | 151,808 | 68% |
| naive-summary | 0.395 | 256 | 293,587 | 62% |
| hierarchical-summary | 0.305 | 570 | 274,167 | 48% |
| truncate-last-n | 0.263 | 196 | 63,698 | 42% |
| structured-state | 0.249 | 256 | 91,605 | 39% |
| null (floor) | 0.235 | 196 | 36,056 | 37% |

None of those runs would qualify for a ranked slot (per-family floor 0.40).

## 4. What is officially scored vs custom, and the claiming rules

- Ranked leaderboard scores come ONLY from the hidden Elite Ranked set, run by
  the maintainer's self-hosted GitHub Actions runner after a submission PR
  (`submissions/<handle>/<method>/` with `method.py`, `config.yaml`,
  `requirements.txt`, README; scaffold at `submissions/_template`). Qualification:
  valid artifacts on every call, declared compression tier clearing its floor
  (Elite-Light >= 2x, Elite-Mid >= 4x, Elite-Aggressive >= 8x), all cases and
  cycles complete, contradiction rate <= 0.10, no Elite family below 0.40.
- `elite_practice` is the public practice mirror of the same families; `starter`
  is a smoke suite; `hard` is empty. Any run we do locally on public suites with
  our own provider is a CUSTOM run, labeled by `suite_key`, `target_model`,
  `endpoint_kind` (`default` vs `custom` — a local endpoint never lands in a
  vendor segment) and `scorer_version`.
- Rules for us: (1) never present a public-suite or mock-provider number as a
  CompactBench score; (2) if we ever submit, the only claimable number is the
  one posted back to the PR by their runner; (3) baseline/control rows are
  unranked reference rows and must be cited as such; (4) scorer 2.0.0 results
  are not comparable to 1.0.0.

## 5. Oracle / null controls design (what we should copy)

- `oracle`: full transcript verbatim as the artifact. Ceiling, not 1.0 — on a
  small local model it is 0.633/0.65-ish because some items are simply hard.
  Read every method score as a position between null and oracle, not as a
  percentage.
- `null`: empty artifact. Floor (0.235 overall on elite_practice/elite with
  that local model). Exposes free points: `planning_soundness` passes 30/30 on
  an empty artifact (currently measures nothing) and `forbidden_behavior_retention`
  is 40% free (evasion passes "did not say X" checks). Their own docs mark both
  as a known limitation being fixed. Any Optimus-side design must include both
  controls in every run, and report `compaction_attributable_drift =
  method_drift / oracle_drift` rather than raw drift.
- `truncate-last-n`: last 10 turns verbatim; the "dumbest thing that could
  work" and the bar a real method must clear. Beating null by 0.028 (their
  local-model run) is the benchmark's core claim about recent-tail policies.
- Their continuation prompt pool is 8 fixed strings chosen by seeded RNG per
  cycle; drift cycles extend the transcript, so later cycles are longer inputs
  even for the oracle (the model's own drift floor, 0.882 on their setup).

## 6. Feasibility verdict

Can we run CompactBench against the real Optimus Rust compaction offline? Yes
for mechanics: the contract is small and fully determined, the external-method
resolution path is proven on this host (section 0), and the Rust side has a
battle-tested offline driver pattern (`compaction_recovery.rs`) with fixture
summarizers, isolated temp roots, and restart coverage. The boundary is one
small harness binary plus a thin Python adapter; no CompactBench changes, no
FFI. What cannot be claimed offline is any quality score: the evaluator needs
a live model, so under this campaign's rules the deliverable stops at a working
harness plus mock-plumbing evidence.
