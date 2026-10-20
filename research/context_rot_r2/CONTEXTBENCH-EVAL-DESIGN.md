# CONTEXTBENCH EVAL DESIGN — process-oriented context-retrieval evaluation for Optimus

Worker W5. Date: 2026-10-19. Status: design complete; no runs against real agents
(model-dependent, out of scope for this offline campaign).

## 0. Provenance

| item | value |
| --- | --- |
| Upstream repo | https://github.com/EuniAI/ContextBench (cloned to `refs/ContextBench`) |
| Pinned commit | `1436c28a8eb95496da4ea69ad458b9f8a8eb7d61` (2026-06-12, default branch `main`) |
| Code license | Apache-2.0 (repo `LICENSE`) |
| Dataset | Hugging Face `Contextbench/ContextBench`; mirrors ship in-repo as parquet + CSV. The HF dataset card's own license terms were not verified offline; the repo LICENSE covers the code. |
| Paper | arXiv:2602.05892 (Li et al., 2026), "ContextBench: A Benchmark for Context Retrieval in Coding Agents" |
| Local data verified | `data/full.parquet` = 1,136 rows x 13 cols; sources: Multi 461, Verified 313, Poly 219, Pro 143; languages: python 512, javascript 153, typescript 119, go 104, c 68, rust 63, cpp 60, java 57; 66 repos. `data/contextbench_verified.parquet` = 500 rows (verified subset). |

## 1. Dataset layout and gold-context annotation format

Per-instance parquet schema (verified by loading `full.parquet` and
`contextbench_verified.parquet` locally):

```
instance_id        e.g. "SWE-Bench-Verified__python__maintenance__bugfix__deb49033"
original_inst_id   e.g. "astropy__astropy-13398"        (native benchmark id)
repo               "astropy/astropy"
repo_url           "https://github.com/astropy/astropy.git"
language           python|javascript|typescript|go|c|rust|cpp|java
base_commit        pinned commit for checkout
gold_context       JSON list of {file, start_line, end_line, content}   <- the annotation
patch / test_patch gold diffs
problem_statement  the issue text
f2p / p2p          fail-to-pass / pass-to-pass tests
source             Verified|Pro|Poly|Multi
```

`gold_context` items are human-annotated file/line spans (inclusive line
ranges, with content snippets). The `Gold` parser
(`contextbench/parsers/gold.py`) merges `init_ctx` + `add_ctx` when present
(Multi variant keys) or falls back to `gold_ctx`; paths are normalized
(`/testbed/`, `/workspace/`, leading `/`, `./` stripped). There is no
init/add split in the shipped parquets listed above; `Gold.files()`,
`byte_spans()`, `byte_spans_init()`, `line_spans_init()` derive the
representations per granularity.

## 2. Metrics (contextbench/metrics/compute.py)

Per-instance, at four granularities — file, symbol (tree-sitter definitions),
span (bytes), line:

- `coverage` = |pred ∩ gold| / |gold| (recall of explored context)
- `precision` = |pred ∩ gold| / |pred| (explored-but-useless penalty)
- Edge cases: gold size 0 -> coverage 1.0; pred size 0 -> precision 1.0.

Trajectory-level:

- `AUC-coverage`: mean over steps of running-union coverage (how early the
  agent found the right context).
- `redundancy`: `1 - union_size / sum_of_step_sizes` per granularity (how
  much re-reading happened).
- Edit localization: recall/precision of the predicted patch location against
  `init_ctx` spans.

Scoring is fully deterministic given the checked-out repo (tree-sitter symbol
extraction runs locally). The metric layer needs no LLM.

## 3. How to run a small dev subset

Two stages, both driven by `python -m contextbench`:

1. Run an agent on tasks (needs a model; BLOCKED offline):
   `python -m contextbench.run --agent <name> --bench Verified --limit 5`
   (supports `--instances id1,id2`, `--subset-csv my.csv`, `--dry-run`).
   Built-in agent adapters: agentless, miniswe, swe-agent, openhands, prometheus.
   A custom agent (Optimus) uses `--agent custom` with
   `contextbench/parsers/custom_parser.py::parse_custom`.
2. Evaluate trajectories vs gold:
   `python -m contextbench.evaluate --gold data/full.parquet --pred pred.jsonl
   --cache ./repos --out results.jsonl`
   `--cache` (default `./repos`) holds one base clone per repo;
   `checkout()` creates a detached worktree per commit under
   `CONTEXTBENCH_TMP_ROOT`. Once the cache is warm (base clone + worktree at
   the right commit exist), evaluation is offline: the fast path returns the
   existing worktree without network. `repo_url` may also point at an
   existing local clone.

Practical dev subset (proposed, 20 instances): stratify
`data/selected_500_instances.csv` (500 verified instances) by language with a
fixed seed, take 20, write a subset CSV, warm the repo cache once (network),
then all evaluation runs are offline. Suggested split discipline: dev = the 20
+ any instances used for debugging; holdout = the remaining 480, touched once,
at the end.

## 4. Licensing

- Code: Apache-2.0.
- Data: mirrors of the HF dataset ship inside the repo (parquet/CSV). The
  underlying tasks derive from SWE-bench Verified/Pro, PolyBench, Multi-SWE-bench;
  those carry their own terms (SWE-bench datasets are MIT-licensed upstream, but
  each variant must be checked individually before redistribution). The HF
  dataset card license was not verified offline. Treat the parquet files as
  evaluation inputs, do not redistribute gold annotations without checking the
  HF dataset terms.

## 5. Matched A/B design for Optimus

Question the A/B answers: does compaction policy X change WHICH context the
agent explores (and how efficiently), holding everything else fixed?

Arms (identical model, endpoint, temperature, seeds, budgets, tools):

- A: production compaction policy (current defaults).
- B: candidate policy (e.g. ledger on, evidence-recovery excerpt, or the
  iteration-2 typed retention).

Design requirements:

1. Same instance set, same order, same per-instance seed. Paired per instance;
   report per-instance deltas, not just means.
2. Trajectory capture: Optimus must export a ContextBench-compatible trajectory
   (unified format: `instance_id`, `traj_data.pred_steps` with
   `files`/`spans`/`symbols`, `model_patch`). Source of truth is the session
   JSONL: tool reads of files map to `pred_steps[].files` and view spans; tool
   edits map to the patch. This needs a session->trajectory exporter with a
   custom parser stub (`parse_custom`) — the same artifact shape
   CompactBench-style harnesses would consume. The exporter is deterministic
   and testable offline against recorded sessions.
3. Metrics read: file/symbol/line coverage + precision (final and AUC),
   redundancy, edit-loc recall/precision. Compaction should mostly move
   AUC-coverage and redundancy (re-reading after losing context), and final
   coverage if the agent gives up exploring.
4. Statistics: per-instance paired differences; with n=20 dev instances use
   sign/Wilcoxon and report effect sizes; escalate n only if the dev signal is
   borderline. Holdout runs once.
5. Model dependence: every number in this design is model-dependent. Under
   the campaign's offline rules this stays a design; actual A/B numbers are
   BLOCKED until a model-backed run is authorized. No ContextBench number may
   be claimed from this campaign.

## 6. Notes and limits

- The evaluator filters predicted paths to files that exist in the checkout and
  resolves container-style prefixes by suffix matching
  (`_resolve_repo_relpath`); Optimus paths are repo-relative already, so this
  is a non-issue.
- Span metrics need `line_to_byte` over the checked-out files; a changed line
  count between annotation and checkout breaks byte metrics, which is why the
  worktree is pinned to `base_commit`.
- `Multi.csv` rows list `gold_context_length` as a raw string in some rows
  (e.g. "19139" vs 1287.0 floats) — the CSVs are summaries only; use the
  parquets as the source of truth.
- The repo vendors agent-frameworks as submodules (agentless, swe-agent,
  mini-swe-agent, openhands, prometheus); we do not need them for the
  Optimus path (custom parser instead).
