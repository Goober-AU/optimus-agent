# REFERENCES — pinned sources inspected in the W5 reference-research work order

Date: 2026-10-19. Worker: W5 (rot2/w5-research). All clones live outside the
Optimus source tree (worktree `refs/` or the shared
`C:/Users/openclawuser/optimus-context-rot-refs/` directory) and are never
committed.

## Freshly cloned for this work order (into `refs/`)

| repo | url | pinned commit | license | what was inspected |
| --- | --- | --- | --- | --- |
| knowledge-triage | https://github.com/searchsim-org/cikm26-knowledge-triage | `a6ceb01a3368cee25ef7ebcf05ebdab8c9be24a4` | Apache-2.0 | full `knowledge_triage/` package (operators, kb, classifier, classifier_cascade, cascade_singleton, safety_margin, preservation), `experiments/multiturn_stability.py`, `experiments/_helpers.py`, `results/multiturn_stability.json`, `results/typeretrieve_vs_vanilla.json`, `results/vanilla_vs_typecompact.json`, `data/sample/` (327-file AAC sample + manifest), README, LICENSE, pyproject |

Paper: Zerhoudi, Mitrovic, Granitzer, "The Compaction Cliff in Long-Running AI
Agent Memory", CIKM '26, DOI 10.1145/3799682.3840567. Paper numbers cited in our
study come from the repo's committed result JSONs, not from re-running their
LLM experiments (impossible offline).

| repo | url | pinned commit | license | what was inspected |
| --- | --- | --- | --- | --- |
| ContextBench | https://github.com/EuniAI/ContextBench | `1436c28a8eb95496da4ea69ad458b9f8a8eb7d61` | Apache-2.0 (code) | `contextbench/` package (metrics/compute.py, parsers/gold.py, evaluate.py, run.py, core/repo.py), `data/*.parquet` + CSVs (1,136 instances; verified subset 500), docs (datasets, process_trajectories, run_agent), README, LICENSE |

Paper: Li et al., "ContextBench: A Benchmark for Context Retrieval in Coding
Agents", arXiv:2602.05892 (2026). Dataset also on Hugging Face
(`Contextbench/ContextBench`); HF dataset-card license not verified offline.

## Pre-cloned references (shared directory, not re-cloned)

| repo | url | pinned commit | license | what was inspected |
| --- | --- | --- | --- | --- |
| compactbench | https://github.com/compactbench/compactbench | `b2bb0e2254010e7842801b9101e56fd9a6542615` | Apache-2.0 | `src/compactbench/` (compactors/, contracts/, engine/, runner/, scoring/, providers/ incl. mock.py, cli.py, config.py), `benchmarks/public/` suites, `baselines/qwen2.5-1.5b-instruct/manifest.yaml`, docs (methodology, leaderboard, submitting), submissions template. Package version 0.2.0. Offline mock-provider smoke runs executed (see COMPACTBENCH-ADAPTER-DESIGN.md section 0). |
| nolima | https://github.com/youtal321/nolima | `cb14780b249fecf2851127b2101a062c1b2c6430` | Adobe Research License (non-commercial research only) | SHA + license only for this work order; content study belongs to the long-context worker. License is NOT permissive: do not copy code into Optimus. |
| context-rot (Chroma) | https://github.com/chroma-core/context-rot | `af80a08018f2b7257c0336d04bcd02f936088106` | MIT | SHA + license only for this work order; iteration-1 protocol shape (C1) already documented in `reports/context-rot/FINAL-REPORT.md`. |

## Optimus-side sources used (within the file map of this brief)

- `reports/context-rot/FINAL-REPORT.md`, `BUG-LEDGER.md`, `MULTICYCLE-DRIFT.md`,
  `COMPACTION-FIDELITY.md` (iteration-1 results; ledger categories, E1/E2/E4).
- `research/context_rot/ARCHITECTURE-AUDIT.md`, `README.md`, `REFERENCES.md`
  (prior reference pins; compaction seams).
- `crates/pi-coding-agent/tests/compaction_recovery.rs` (offline driver
  pattern for the real pipeline: fixture provider, `prepare_compaction` ->
  `compact` -> `append_compaction` -> `SessionManager` restore).
- `AGENTS.md`, workspace `Cargo.toml`, `crates/pi-coding-agent/Cargo.toml`
  (binary targets; `optimus-rust` is the app binary, no compaction harness
  exists today).

## Reproduction environments

- TypeCompact repro: system Python 3.11 (stdlib only) + the knowledge-triage
  checkout auto-located at `refs/cikm26-knowledge-triage`; third-party imports
  (`tenacity`, `openai`) stubbed in-process because the package imports them
  at module load; no network, no model calls. Output:
  `research/context_rot_r2/repro/typecompact_repro.json` (n=20) and
  `typecompact_repro_full_suite.json` (n=197).
- CompactBench smoke: scratch venv `refs/cb-venv` (pip install -e of the
  compactbench checkout + pyarrow/pandas for parquet inspection). Mock provider
  runs only; no keys, no spend.

## Unavailable / not verified

- AgentArtifactCorpus full release (gated HF dataset with a Data Use
  Agreement): NOT downloaded. Only the in-repo 327-file sample was used.
- CompactBench hidden Elite Ranked set: inaccessible by design (maintainer
  runner only). Any ranked-score claim would require a submission PR.
- ContextBench HF dataset-card license: not verified offline.
- Knowledge Triage companion skill repo (searchsim-org/knowledge-triage-skill):
  not cloned, not inspected.
- NoLiMa/LongBench/RULER/LongMemEval/LLMLingua corpora: pre-cloned in the
  shared refs dir but out of this brief's file map; only SHAs/licenses recorded
  above where inspected.
