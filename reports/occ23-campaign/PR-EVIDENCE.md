# OCC-2 + OCC-3 Campaign PR Evidence Index

Branch `improve/occ2-occ3-communication`, base `ce8480d59` (telemusai/optimus-agent v0.1.32). At this draft the entire campaign change set is
UNCOMMITTED in the worktree (branch tip = base): 10 tracked files +998/-25, three new code files (`core/occ/occ2.rs`, `core/occ/dialect.rs`,
`tests/occ2_comm_fidelity.rs`), three `.changes` fragments, and the untracked research/report trees. Commit grouping is proposed in
`reports/occ23-campaign/COMMIT-PLAN.md`; the parent integrates.

Campaign window: 2026-10-16. Host: Windows, private Rust toolchain, 6 physical / 12 logical CPUs. Models: local gateway `127.0.0.1:43120` (build
`optimus-gateway-monitoring-repair-20260918.1`, health-verified), live routes glm-5.3, glm-5.3-flash, glm-5.2, deepseek-v4-pro, deepseek-v4.1-flash.

Purpose: single reviewer-facing evidence index for this PR. Every number below traces to an on-disk campaign artifact (Sources); items marked
[verified by this packaging pass] were re-derived from the artifacts during pr-prep. Discrepancies: section 6.

Sources: `reports/occ23-campaign/{FINAL-REPORT,EXPERIMENT-LEDGER,BUG-LEDGER}.md`, `research/tasks/BASELINE.md`,
`research/taskbench/TASKBENCH-BASELINE.md`, `research/benchmarks/{PILOT-REPORT,BENCHMARK-SURVEY}.md`, `research/symbolic_comm/RESEARCH-NOTES.md` +
`evolution-log.jsonl` + `evolution-winner.json`, `research/latent_handoff/RESEARCH-NOTES.md` + `RESULTS-EXP-A.md` + `RESULTS-EXP-B.md` +
`results/*.json`, `research/integration/*.md`, the three `.changes` fragments, `git diff` vs base, and the run scorecards under
`research/benchmarks/runs/`.

## 1. Test matrix

Every cargo call set the private toolchain env and `CARGO_BUILD_JOBS=3` (the campaign CPU rule after two workers saturated all 12 logical CPUs;
per-worker isolated `CARGO_TARGET_DIR`). Package-scoped tests only; one workspace check at the end.

| Suite | Tests | Result | Command |
|---|---|---|---|
| `core::occ` unit tests | 204 | 204 passed, 0 failed; 3 consecutive runs (codec worker) + 1 confirmation run | `cargo test -p pi-coding-agent --lib occ::` |
| -- base composition | 181 | base v0.1.32 count: 103 codec + 23 delta + 18 consolidation + 22 prune + 7 contract + 6 occ1 + 2 activity [verified by this packaging pass: `#[test]` count in `git show ce8480d59:...`] | same |
| -- new composition | 23 | 14 occ2 + 8 dialect + 1 activity classify (`occ2_bodies_classify_by_the_consumed_path`) [same verification] | same |
| OCC-1 regression suite | 7 | 7 passed, 0 failed. Reproduced on clean base `ce8480d59` first (bench-tasks, isolated target dir, 7m07s build), then green in 4 further runs by occ2-native and 2 by occ2-telemetry | `cargo test -p pi-coding-agent --test occ_comm_fidelity` |
| New OCC-2 fidelity suite | 12 | 12 passed, 0 failed; 4 green runs by occ2-native, 2 by occ2-telemetry. Tests t01-t12 over `research/tasks/msgs_corpus.json` (69 messages, 18 declared holdout) | `cargo test -p pi-coding-agent --test occ2_comm_fidelity` |
| `communication_` lib tests (touched scope) | 13 | 13 passed (4 new + 9 existing, 3 updated to the 3-slot TXT/OCC/SYM format); 2 runs (telemetry worker) | `cargo test -p pi-coding-agent --lib communication_` |
| Lib suite in touched scope | 286 | 286 passed / 0 failed (occ2-native); pre-existing lib failures verified NOT campaign-caused by a stash A/B comparison on the untouched set | package-scoped lib run over the touched modules |
| `cargo check --workspace` | - | exit 0, clean (single final run per the CPU rule) | `cargo check --workspace` |
| rustfmt | - | clean (codec worker, final pass) | `cargo fmt` (touched files) |
| Corpus/task generators self-test | 306 checks | 306/306 (300 gold accepted + 300 independent solver re-derivations + 6 negative controls rejected); regeneration byte-identical | `python research/tasks/gen_tasks.py` |
| Message corpus validation | 69 msgs | all validation gates pass (contract-mirror validate, unique ids, fixture schema fit, OCC-1 encodability, byte-identical rebuild) | `python research/tasks/gen_corpus.py` |
| Python OCC-1 codec suite | 44 | PENDING re-run on the integrated tree (parent, pre-push). 44/44 held at the prior campaign follow-up tip `f32ab2e97`; this campaign's diff touches no Python file [verified: `git diff --name-only` vs base lists only `.gitignore` + 9 Rust files] | `python -m unittest discover -s prime-agent-runtime/test -p "test_occ1_codec.py"` |

OCC-1 file identity [verified by this packaging pass]: the tracked diff touches `core/occ/{activity,mod}.rs` only; `occ1.rs`, `contract.rs`,
`delta.rs`, `consolidation.rs`, `consolidation_tests.rs`, `prune.rs`, `prune_tests.rs`, `codec_tests.rs`, `delta_tests.rs`,
`tests/occ_comm_fidelity.rs` and `tests/fixtures/occ/scenarios.json` are absent from the diff, i.e. byte-identical to base.

## 2. Benchmark runs and artifacts

### 2.1 Taskbench (real models, two logical agents, strict scoring)

Artifacts: `research/taskbench/runner.py`, `research/taskbench/taskbench_runs.jsonl` (raw per-task records: messages, answers, per-call usage and
latency, retry flags; re-analyzable without new model calls), `research/taskbench/results_taskbench.json` (aggregates),
`research/taskbench/TASKBENCH-BASELINE.md`.

Protocol: Agent A (glm-5.3, speaker) sees only `sender_input`; Agent B (glm-5.3 or glm-5.3-flash, listener) sees only A's message plus
`receiver_task`. B's system prompt is byte-identical across modes, so B `usage.prompt_tokens` deltas measure the message encoding cost on the real
server-side tokenizer. Temperature 0, seed 42; wrong answers never retried; holdout untouched. 300 gateway calls exactly.

| Cell | Accuracy (strict) | B-prompt tokens mean / median | B p50 latency |
|---|---|---|---|
| occ0 x glm-5.3 | 48/50 (96%) | 239.0 / 239.0 | 1.69 s |
| occ0 x glm-5.3-flash | 48/50 (96%) | 239.0 / 239.0 | 0.81 s |
| occ1 x glm-5.3 | 50/50 (100%) | 254.3 / 228.0 | 1.68 s |
| occ1 x glm-5.3-flash | 50/50 (100%) | 254.3 / 228.0 | 0.82 s |

Per-family B-prompt deltas (occ1 - occ0, glm-5.3 listener): A -15.1, B +32.1, C -25.0, D +94.7, E -10.0; occ1 cheaper on 25/50 tasks. OCC-1 fixed
overhead: A-system +90 tokens, A-completion +135.9 (incl. hidden reasoning). Both occ0 failures are exact-form answer-normalization misses (A-dev-010
"Okafor" vs "release engineer okafor"; C-dev-010 "Stage the new public key." vs lowercase gold), the same two tasks under both listeners; OCC-1 field
values carried the verbatim strings and both flipped correct. 0 parse failures, 0 retries, 0 truncations across 300 calls.

### 2.2 Codec corpus (offline, deterministic; fidelity suite t03/t11/t12)

Over `research/tasks/msgs_corpus.json` (69 typed `optimus.agent-comm.v1` messages, 18 holdout, all 9 kinds, 7 statuses, 8 evidence kinds): plain
23,505 B; JSON 39,148 B (serde compact; see cross-check 6.2); OCC-1 24,361 B; symbolic 23,316 B; dual 48,781 B. Symbolic <= OCC-1 per-message on
69/69; aggregate -4.3%; -8.0% PROXY tokens; dual encode mean 137.5 us / decode 82.3 us against the 5,000 us budget; all 69 dual payloads inside the
16,384-char transport bound. Fixture comparison (28 scenarios, `tests/fixtures/occ/scenarios.json`): occ2 14,027 B vs occ1 14,659 B, smaller on every
scenario.

### 2.3 Real-token dialect sample (DEV 8-msg, glm-5.3 tokenizer, empty-prompt baseline subtracted)

OCC-0 86.0 tok/msg; OCC-1 143.6; sym-v1 no-pack 116.6; sym-v1 full-pack 111.1. Packing layer saves 22.6% vs OCC-1. Card amortization: a dialect card
costs ~200-300 context tokens for ~32 tokens/message saved vs OCC-1; break-even ~7-9 structured messages per session. Source: EXPERIMENT-LEDGER
(occ2-evolve milestone 1).

### 2.4 Model-in-loop dialect evaluation (research/symbolic_comm/)

Artifacts: `evolution-log.jsonl` (run_start, per-generation frontiers and candidates with listener-fidelity and wire-token medians, model-assisted
operator outcomes), `evolution-winner.json` (gen-5 candidate card), `RESEARCH-NOTES.md`. Listener glm-5.3-flash, proposer glm-5.3, measure
deepseek-v4.1-flash; splits EVOLVE 31 / DEV 20 / HOLDOUT 18; per-candidate eval ~13.5 s.

Generation 0 frontier: sym-v1 listener fidelity 1.0, median 117.0 wire tokens (mean 111.5), 10/10 comprehension, p95 listener latency 14.586 s;
sym-pack-e fidelity 1.0 / 119; sym-occ1 0.9 / 124; sym-max 0.9 / 115. OCC-1 baseline median 143.6 (the 2.3 sample). Median-to-median: sym-v1 -18.5% vs
OCC-1 at fidelity 1.0. Model-assisted recombination accepted once at fidelity 1.0 (proposer-hang worked around); model synthesis rejected (card parse
failure). Evolution harness defect record and proposer-hang probe evidence: EXPERIMENT-LEDGER 09:5xZ.

### 2.5 Official SILO-BENCH (pinned engine, unmodified)

Engine: silo-bench clone `e74127782ed1c42fff474249961f022c063d76f2` (Unlicense), imported unmodified by `research/benchmarks/adapter/` (init_case /
run_round / evaluate in per-case subprocesses, 1200 s budget). The adapter's `gateway_proxy.py` forwards request bytes verbatim (real
`usage.prompt_tokens`) and reconstructs the fenced XML tool-call blocks the gateway extracts into native `tool_calls`; every rewrite audited in
`runs/<run>/proxy_rewrites.jsonl` (glm-5.3 pilot: 100/117 responses; flash pilot: 102/129; occ1 run: audited). Config: Level I, n2, msg protocol,
max_rounds 20, 10 cases.

| Run | comm_mode | Model | Case acc (S==1.0) | Mean S | Calls | Real prompt tok | Scorecard |
|---|---|---|---|---|---|---|---|
| pilot-levelI-n2-msg-glm53 | occ0 | glm-5.3 | 0.8 (8/10) | 0.944 (9 completed) | 127 | 184,416 | `research/benchmarks/runs/pilot-levelI-n2-msg-glm53/scorecard.{json,md}` |
| pilot-levelI-n2-msg-glm53flash | occ0 | glm-5.3-flash | 1.0 (10/10) | 1.0 | 100 | 149,034 | `research/benchmarks/runs/pilot-levelI-n2-msg-glm53flash/scorecard.{json,md}` |
| occ2-levelI-n2-msg-glm53flash-occ1 | occ1 | glm-5.3-flash | 1.0 (10/10) | 1.0 | 97 | 191,634 | `research/benchmarks/runs/occ2-levelI-n2-msg-glm53flash-occ1/scorecard.{json,md}` |
| occ2-levelI-n2-msg-glm53flash-sym | sym-v1 | glm-5.3-flash | PENDING | PENDING | PENDING | PENDING | 9/10 case files on disk, no scorecard at draft time |

Failure analysis (honest): glm-5.3 I-06 (Checksum) S=0.5 - both agents exchanged local XOR checksums and a correction, agent 1 still submitted stale
data (communication-reasoning gap, not transport); I-10 (Standard Deviation) timed out at the 1200 s budget in round 6 of 20 (wall-clock artifact).
glm-5.3-flash beat glm-5.3 (10/10 vs 8/10): the big model burned 67,232 completion tokens on I-06 and exceeded the clock on I-10. Message volumes:
glm-5.3 33 messages / 7,657 B; flash 40 / 10,558 B; occ1 flash 42 / 12,173 B. MARBLE: not run; main environments infeasible offline (documented in
BENCHMARK-SURVEY.md).

### 2.6 OCC-3 experiments (CPU harness)

Artifacts: `research/latent_handoff/{kv_handoff,latent_continuation,interventions,model_utils,results_writer,run_experiments}.py`,
`results/qwen05-results.json`, `results/smollm2-results.json`, `results/archive-broken-run/smollm2-results.json` (pre-fix record), `RESULTS-EXP-A.md`,
`RESULTS-EXP-B.md`, `RESEARCH-NOTES.md`. Models: Qwen2.5-0.5B-Instruct and SmolLM2-135M-Instruct, torch 2.14.1+cpu, transformers 5.20.0.

- Experiment A (KV-cache handoff, protocol v2): Qwen2.5-0.5B chat probe 50/50 with-cache, 50/50 full-text control, 0/50 no-cache, 50/50 proven, paired
  bootstrap diff 1.0 CI [1.0, 1.0] - GATE PASS. Receiver generation 0.8521 s vs full-text 1.1882 s; transfer (deepcopy) 17.9 ms; cache artifact
  1,991,147 B fp32 / 995,573 B fp16 for an 81-token sender turn vs ~51 B text. SmolLM2-135M: chat probe fails its own positive control (15/50
  full-text; capability ceiling) - INVALID, not NO-GO; raw induction probe 49/50 / 49/50 / 0/50 / 49/50 proven, bootstrap CI [0.94, 1.0] - GATE PASS.

- Intervention battery (Qwen, 20 trials, fresh-deepcopy use_cache=False probes; A-vs-A sanity KL 0.0): correct cache 20/20 KL 0; full-text 20/20 KL 0;
  compact text 18/20; truncated 19/20 (KL 0.024); consistent within-pair shuffle 20/20 KL 0 (null intervention, new finding); unpaired shuffle 1/20
  (KL 7.56); no-cache/unrelated/zeroed/random/covgauss/haar all 0/20 with large KL.

- Experiment B (bounded latent continuation, Qwen private-fact probe): none 10/10 (0 steps), 8/10 (1), 8/10 (2), 2/10 (4), 0/10 (8); rescale 9/3/0/0;
  rescale_ridge 9/3/0/0; random control 10/10/10/9. NEGATIVE: real latent continuations corrupt the cache more than injected noise.

- Go/no-go verdict (pre-registered before harness reruns; GO = V1-V3 + G1 + G3 + (G2|G4), default OFF regardless; full scorecard:
  `reports/occ23-campaign/OCC3-SCORECARD.md`, landed by the occ3 report worker during pr-prep and consistent with every number here): V1-V3 PASS (both models,
  model-appropriate probes); G1 PASS; G2 not measured at its >=4096-token definition; G3 FAIL for Qwen (fp32 RSS peak 2,443.1 MiB vs the 1 GiB sidecar
  cap; SmolLM2 967.8 MiB passes but fails the chat probe); G4 untested. Decision: NO-GO for production integration; harness, protocol, negative
  findings and `research/integration/OCC3-INTEGRATION-DESIGN.md` ship as research. LAT indicator stays reserved (no producer).

## 3. Reference pins

OCC-2 track (from `research/symbolic_comm/RESEARCH-NOTES.md` section 6):

| Source | Pin | License | Role |
|---|---|---|---|
| CLSR paper (Pei et al., ICML 2026, PMLR v306 pei26a) | local PDF `pei26a_clsr.pdf` | PMLR open access | primary algorithm source (evolutionary bootstrapping, Pareto selection, router cards) |
| CLSR code `pzqpzq/LSF_MDia` | verified 404 (web + GitHub API, re-verified 2026-10-16) | none | nothing to pin; recorded as a negative result |
| MOC (Guan et al., arXiv 2606.02359) | `9c67c92507570704a7df73e452552a3f49e83897` | no license file: ideas only, no code copied | ISM merge structure, hop aggregation, never-drop-on-failure |
| AgentPrune (Zhang et al., ICLR'25) | `c544dd6a1858c02c6d5d371d23c6e6ff55e0be21` | no license file: ideas only | utility-driven selection, never-prune classes, pruning floor |
| silo-bench (jwyjohn, arXiv 2603.01045) | `e74127782ed1c42fff474249961f022c063d76f2` | Unlicense (public domain) | official benchmark engine, imported unmodified |
| Model gateway | build `optimus-gateway-monitoring-repair-20260918.1`, health-verified 2026-10-16 | internal | speaker/listener models, real usage counts |

Shared/OCC-3 track (from `research/latent_handoff/RESEARCH-NOTES.md` section 5):

| Source | Pin | License | Role |
|---|---|---|---|
| LatentMAS (Zou et al., arXiv 2511.20639) | `9a9e4d331eb11430bd9e64754c6b252b06d73031` | Apache-2.0 | exact latent-loop mechanism (generate_latent_batch, norm-rescale, cache truncation) |
| Interlat | `66a89cb4d4097b2f86cbe48ed9851d6e8578f821` | Apache-2.0 | trained latent communication reference |
| transformers | 5.20.0 (last 4.x: 4.57.6) | Apache-2.0 | pinned KV-cache API surface |
| torch | 2.14.1+cpu (win_amd64) | BSD-style | CPU harness |
| SmolLM2-135M-Instruct | revision `12fd25f77366fa6b3b4b768ec3050bf629380bac` | Apache-2.0 | harness model |
| Qwen2.5-0.5B-Instruct | revision `7ae557604adf67be50417f59c2c2f167def9a775` | Apache-2.0 | harness model |
| MARBLE / MultiAgentBench | `8d60fa17b5596b44458a52d4296061b9fc13d6f2` | MIT | secondary benchmark (infeasibility documented) |
| MAST | `a70542e541b2104ef8fcd785778179e173fb8d70` | no license file: ideas only | failure taxonomy (prior campaign) |
| rlm | `d04208afbad29ca675ab13478c40ee8bebc84bfe` | MIT | runtime lineage (prior campaign) |

License handling: MOC, AgentPrune and MAST ship no license file and are treated as all-rights-reserved; only ideas were re-implemented, with
citations. No code was copied from any no-license reference. CLSR has no released code at all.

## 4. Reproduction commands

Rust (Git Bash on the campaign host; CPU rule `CARGO_BUILD_JOBS=3`, per-worker `CARGO_TARGET_DIR`):

```
export RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup
export CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo
export PATH="/c/Users/openclawuser/optimus-rust-toolchain/cargo/bin:/c/Program Files/nodejs:$PATH"
export KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe
cargo test -p pi-coding-agent --test occ_comm_fidelity      # 7/7
cargo test -p pi-coding-agent --test occ2_comm_fidelity     # 12/12
cargo test -p pi-coding-agent --lib occ::                   # 204 passed
cargo test -p pi-coding-agent --lib communication_           # 13/13
cargo check --workspace                                     # exit 0 (once, at end)
```

Python (deterministic generators and suites; Python 3.11.15 on PATH):

```
python research/tasks/gen_tasks.py      # 306/306 self-test, byte-identical regeneration
python research/tasks/gen_corpus.py     # 69-msg corpus validation + stats
python -m unittest discover -s prime-agent-runtime/test -p "test_occ1_codec.py"   # 44 tests, PENDING on this tree
```

Real-model measurements (require the local gateway at 127.0.0.1:43120 and its token file; the token is never printed):

```
python research/taskbench/runner.py smoke                       # 5 tasks x 2 modes
python research/taskbench/runner.py run --tasks-per-family 10   # the 300-call baseline matrix
python research/taskbench/runner.py aggregate                   # rebuild results_taskbench.json

# official SILO-BENCH (engine venv requires Python >= 3.13)
uv venv --python 3.14 research/benchmarks/adapter/.venv
uv pip install --python research/benchmarks/adapter/.venv/Scripts/python.exe \
    openai pydantic pyyaml srsly tenacity httpx
research/benchmarks/adapter/.venv/Scripts/python.exe research/benchmarks/adapter/pilot_run.py \
    --config research/benchmarks/adapter/config.json --run <run-name>
# landed runs: pilot-levelI-n2-msg-glm53{,flash}, occ2-levelI-n2-msg-glm53flash-occ1
# in flight at draft time: occ2-levelI-n2-msg-glm53flash-sym (no scorecard yet)

python research/symbolic_comm/run_all.py    # fidelity + mutation suites + evolve entry points
```

Holdout discipline: `research/tasks/tasks_holdout.json` (150 tasks) and the 18 holdout corpus stories were never touched by any tuning call; the
evolution pool freezes before the first HOLDOUT call, with card checksum and DEV summary reported before holdout evaluation.

## 5. Honest limits

- REAL tokens = gateway `usage.prompt_tokens` (server-side count of exactly the bytes sent); wire-cost claims use only these. PROXY tokens =
  whitespace+punctuation split, used only where no model call exists. SAMPLE = real tokenizer on a small DEV subset, single measurement. Gateway
  `usage.completion_tokens` INCLUDES hidden reasoning tokens (probe: glm-5.3-flash content "ok" reported 92 completion tokens) and is a server-cost
  proxy only.

- Internal gateway: cash cost zero; "cost" is reported as gateway call counts plus real prompt-token totals. Single run per benchmark cell; no
  repeat-run variance estimate (temperature 0, seed 42).

- Windows-host-only verification; the prior campaign's platform blockers (Linux `/proc` probes) stand. `scripts/check.sh` hardcodes `python3` (not on
  PATH here); its steps were run individually.

- The three-strike ledger is pure and tested but has no runtime recorder yet (the daemon does not decode bodies today); deltas are excluded from OCC-2
  wrapping in v1; `sym-v1` is explicitly a starting-point card. Saved passive RLM trees advertise no capability (conservative OCC-1) - a documented,
  intentional plumbing gap.

- Pre-existing Windows lib failures were verified NOT campaign-caused (stash A/B by occ2-native); the product BUG-LEDGER for this campaign is empty
  (all campaign gates green on first landing; defects found lived in research harnesses and are recorded in EXPERIMENT-LEDGER section 6).

- deepseek-v4-flash is retired (HTTP 410 since 2026-09-25); Azure routes 503 during the campaign. Model lists go stale; re-probe /health before each
  session.

## 6. Cross-check notes

1. The -18.5% wire-token figure: the pr-prep brief called it "evolved-dialect tokens -18.5% vs OCC-1 at fidelity 1.0". On-disk sources
   (`evolution-log.jsonl` generation 0; EXPERIMENT-LEDGER 11:5xZ) attribute median 117.0 wire tokens to the HAND-DESIGNED sym-v1 card measured in the
   model-in-loop harness; the OCC-1 baseline median 143.6 is the taskbench-era sample (2.3). 117 vs 143.6 = -18.5%. The evolved winner
   `sym-v1-pt-mi-pt-sp` has no recorded evaluation numbers on disk (not frozen, no EVOLUTION-REPORT, holdout untouched). PR-DESCRIPTION therefore
   attributes -18.5% to sym-v1 and marks the evolved card PENDING. The parent should either keep that attribution or land the evolved-card numbers
   before push.

2. Corpus JSON bytes: `research/tasks/BASELINE.md` records 43,673 B (Python `json.dumps`); the fidelity suite and FINAL-REPORT record 39,148 B (serde
   compact). Both serialize the same 69 payloads with different separators; neither is wrong. The PR uses the suite-measured 39,148.

3. New-file line counts: codec worker final report 2,237 / 1,122 / 994 (occ2.rs / dialect.rs / occ2_comm_fidelity.rs); the merge inventory recorded
   2,238 / 1,123 (trailing-newline counting). This packaging pass measured 2,237 / 1,122 / 994 and the PR uses those.

4. `core::occ` 204 decomposition: the prior campaign recorded 178 at the v0.1.30 tip; base v0.1.32 carries 181 (intervening releases added 3).
   181 + 22 new (14 occ2 + 8 dialect) + 1 new activity classify test = 204, matching the recorded gate [verified by this packaging pass].

5. Python 44/44: recorded at the prior campaign follow-up tip `f32ab2e97`, not yet re-run on this tree. The campaign diff touches no Python file
   (only `.gitignore` + 9 Rust files modified; new files are Rust, fragments and docs), so the re-run is a formality the parent executes pre-push.

6. sym official run: `research/benchmarks/runs/occ2-levelI-n2-msg-glm53flash-sym/` contains 9 of 10 case files plus `proxy_rewrites.jsonl`, but no
   `scorecard.json`/`scorecard.md` at draft time; the run is PENDING, and `OCC2-OFFICIAL-RESULTS.md` (designated landing spot) does not exist yet.

7. Taskbench B-prompt means are identical across listener cells (238.96 occ0, 254.3 occ1): expected, since the B prompt template is byte-identical and
   both listeners share the gateway tokenizer; not a copy error. The completion-token columns differ per listener and include hidden reasoning.

8. Diff-stat snapshots in campaign documents were taken at different moments; this index's +998/-25 over 10 tracked files is the `git diff --stat` vs
   base at pr-prep time [verified by this packaging pass] and matches the merge worker's final inventory.
