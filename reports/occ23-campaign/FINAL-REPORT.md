# OCC-2 + OCC-3 Campaign Final Report (Phase 13 DRAFT)

Branch `improve/occ2-occ3-communication`, base `ce8480d59` (telemusai/optimus-agent v0.1.32).
Campaign window: 2026-10-16. Host: Windows, private Rust toolchain, 6 physical / 12 logical CPUs.
Models: local gateway `127.0.0.1:43120` (build `optimus-gateway-monitoring-repair-20260918.1`),
live routes glm-5.3, glm-5.3-flash, glm-5.2, deepseek-v4-pro, deepseek-v4.1-flash.

STATUS: DRAFT for parent finalization. Slots whose runs had not finished when this draft was
written are marked as not executed. Every other number traces to a file on disk (sources: section 7).
This report covers the OCC-2 track plus the campaign's shared sections; the OCC-3 scorecard is
a separate deliverable, `reports/occ23-campaign/OCC3-SCORECARD.md` (section 9 summarizes the
verdict).

Measurement conventions used throughout:

- REAL tokens: `usage.prompt_tokens` from the gateway, the real server-side count of exactly
  the bytes sent. Wire-cost claims use only these.
- PROXY tokens: whitespace+punctuation split (tokenizer approximation), used where no model
  call exists (offline codec corpora).
- Gateway `usage.completion_tokens` INCLUDES hidden reasoning tokens; it is a server-cost
  proxy only and is never used for wire-volume claims.
- The campaign ran on an internal gateway; cash cost is zero, so "cost" is reported as gateway
  call counts plus real token totals.

---

## 1. Executive summary

What was built (all default-off, behind `agentCommunication.occ2Enabled`, default `false`):

- `core/occ/occ2.rs` (2,237 lines incl. tests): strict symbolic wire codec over the full
  `optimus.agent-comm.v1` contract; dual payloads (symbolic section + `#FALLBACK-OCC1`
  sentinel + full OCC-1 text) that must agree on decode or are rejected; `occ1_fallback`
  for registry-free recovery; typed error inventory (unknown dialect, hash mismatch,
  malformed line, duplicate field, index gap, dangling ref, partial verification group).
- `core/occ/dialect.rs` (1,122 lines incl. tests): `DialectCard` with sha256 codebook hash,
  `DialectRegistry` (insert/resolve/validate, duplicate rejection), `OccCapability`
  here()/supports()/negotiate(). Built-in `sym-v1` card, codebook sha256 pinned in test:
  `1efce9992ee24ff779a0430d998b37704d484f9cbb90c2fb78feec063add1547`.
- Native integration: `wrap_outgoing_body` + `Occ2SendPolicy` at both send seams
  (`agent_message.send` direct/broadcast, RLM child-spawn prompts); sentinel split before
  prompt build; strict-then-OCC-1 decode; pure `Occ2StrikeLedger` (3-strike session disable);
  `occ_capability` negotiation field on agent summaries; 16,384-char size guard that sends
  plain OCC-1 rather than truncating.
- Telemetry: settings-gated `OccCapability::here()` advertisement; SYM activated as the third
  communication-indicator slot (TXT/OCC/SYM); strict `Symbolic` classify arm (fires only when
  `occ2::decode` succeeds; fallback-consumed reports Occ1; LAT stays reserved).

What was measured (headline numbers; details in section 2):

- Task accuracy, real models (taskbench, 50 tasks/cell, strict scoring): OCC-0 English 96%
  (48/50) on both listeners; OCC-1 100% (50/50) on both. glm-5.3-flash matches glm-5.3
  accuracy at roughly half the listener latency (p50 0.81 s vs 1.69 s).
- Wire cost, real tokenizer (byte-identical B templates): OCC-1 vs OCC-0 mean +15.3 tokens,
  median -1.0; family deltas A -15.1, C -25.0, E -10.0 vs B +32.1, D +94.7. The KEY=VALUE
  grammar alone is NOT cheaper; compactness comes from field-packing discipline.
- Symbolic codec, offline corpus (69 typed messages, 18 holdout): symbolic <= OCC-1
  per-message on 69/69; aggregate 23,316 B vs 24,361 B (-4.3%); -8.0% proxy tokens; dual
  encode 137.5 us / decode 82.3 us against a 5,000 us budget; all 69 dual payloads inside
  the 16,384-char transport bound.
- Real-token dialect sample (DEV 8-msg, glm-5.3 tokenizer): OCC-0 86.0 tok/msg, OCC-1 143.6,
  dialect no-pack 116.6, dialect full-pack 111.1. Packing saves 22.6% vs OCC-1.
- Official SILO-BENCH (unmodified pinned engine, Level I n2, msg protocol, 10 cases):
  OCC-0 glm-5.3 0.8 case accuracy / 0.944 mean S; OCC-0 glm-5.3-flash 1.0 / 1.0;
  OCC-1 glm-5.3-flash 1.0 / 1.0 (191,634 real prompt tokens, 97 calls). The OCC-1/sym-v1
  comparison completed for the sym-v1 flash cell (occ1 glm-5.3 cell stopped mid-flight and excluded).

What shipped: 10 tracked files changed (+998/-25) plus three new code files (occ2.rs,
dialect.rs, tests/occ2_comm_fidelity.rs with 12 tests) and three `.changes` fragments
(occ2-symbolic-dialects, occ2-native-integration, occ2-telemetry). Campaign research trees
under `research/` and `reports/occ23-campaign/` are untracked artifacts for the parent to
integrate. The feature flag is OFF by default; OFF means byte-identical OCC-0/OCC-1 traffic.

## 2. OCC-2 scorecard

The campaign prompt's required comparison. Token columns are marked REAL (server-side
tokenizer), PROXY (split approximation), or SAMPLE (real tokenizer, small DEV subset).
"Interpretation accuracy" = the listener side reproduces the sender's typed content:
codec parse/round-trip for structured modes, official mean S where the benchmark provides it.

| Mode | Speaker | Listener | Tasks | Interpretation acc | Task acc | Input tokens | Output tokens | Cost (calls / real tok) | Latency | Fallback rate |
|---|---|---|---|---|---|---|---|---|---|---|
| English (OCC-0), taskbench | glm-5.3 | glm-5.3 | 50 | n/a (prose) | 96% (48/50) | B-prompt 239.0 mean REAL | B-compl 88.8 (incl. hidden) | 150 calls/mode (50 A + 100 B) | B p50 1.69 s | n/a |
| English (OCC-0), taskbench | glm-5.3 | glm-5.3-flash | 50 | n/a | 96% (48/50) | B-prompt not separately reported | B-compl 77.6 (incl. hidden) | shares A calls; 50 B calls | B p50 0.81 s | n/a |
| English (OCC-0), SILO official | glm-5.3 | glm-5.3 (2 agents) | 10 Level-I n2 | official mean S 0.944 | 0.8 (8/10) | 184,416 REAL total | 115,801 (incl. hidden) | 127 calls | case p50 52.0 s / p95 904.2 s | n/a |
| English (OCC-0), SILO official | glm-5.3-flash | glm-5.3-flash (2 agents) | 10 Level-I n2 | official mean S 1.0 | 1.0 (10/10) | 149,034 REAL total | 182,834 (incl. hidden) | 100 calls | case p50 38.1 s / p95 288.7 s | n/a |
| OCC-1, taskbench | glm-5.3 | glm-5.3 | 50 | 0 parse failures in 50 | 100% (50/50) | B-prompt +15.3 mean / -1.0 median vs OCC-0 REAL | B-compl 80.4 (incl. hidden) | 150 calls/mode (50 A + 100 B) | B p50 1.68 s | 0 retries needed |
| OCC-1, taskbench | glm-5.3 | glm-5.3-flash | 50 | 0 parse failures in 50 | 100% (50/50) | same A messages | B-compl 73.0 (incl. hidden) | shares the 100 A calls | B p50 0.82 s | 0 retries needed |
| OCC-1, SILO official | glm-5.3-flash | glm-5.3-flash (2 agents) | 10 Level-I n2 | official mean S 1.0 | 1.0 (10/10) | 191,634 REAL total | 173,395 (incl. hidden) | 97 calls | case p50 43.5 s / p95 418.4 s | n/a |
| OCC-1, SILO official, glm-5.3 | glm-5.3 | glm-5.3 (2 agents) | 10 Level-I n2 | not run (stopped mid-flight; excluded from claims) | - | - | - | - | - | - |
| Hand-designed OCC-2 (sym-v1), codec corpus | deterministic codec (no model) | deterministic codec | 69 msgs (18 holdout) | 69/69 round-trip (100%) | n/a (no task suite) | symbolic -8.0% PROXY vs OCC-1; 23,316 B vs 24,361 B | n/a (no listener generation) | offline, 0 model calls | dual encode 137.5 us / decode 82.3 us | 0 in tests (guard fires only on engineered oversized bodies) |
| Hand-designed OCC-2 (sym-v1), SILO official | glm-5.3-flash | glm-5.3-flash (2 agents) | 10 Level-I n2 | 0.9 (9 completed, 1 wall-clock timeout; same I-10 timeout mode as the OCC-0 glm-5.3 pilot) | 1.0 | 253,864 real prompt tokens (88 calls); inter-agent payloads 153.8 B/msg vs OCC-1 289.8 (47.0% smaller) | - | - | - | - |
| Hand-designed OCC-2 (sym-v1), model-in-loop sample | glm-5.3-flash (proposer) | glm-5.3-flash | EVOLVE 31 / DEV 20 sample | listener fidelity 1.0, comprehension 10/10 (gen 0) | not tasked | median 117 wire tokens SAMPLE | not the metric | ~13.5 s per candidate eval | p95 listener latency 14.6 s | 0 |
| Evolved OCC-2 dialect (sym-v1-pt-pt, frozen) | glm-5.3 (speaker) / glm-5.3-flash (listener) | DEV split 20 msgs | deterministic fidelity 1.0 (20/20); listener payload-equality 1.00 (10/10); median 101.0 wire tokens vs OCC-1 133.5 same split (-24.3% paired) | HOLDOUT NOT EXECUTED (evaluation halted); card published as artifact, not validated default |
| Model-pair matrix (5 of 10 cells implemented in eval.py) | not executed | no pair-compatibility numbers claimed | - | |

Supporting detail (all REAL tokens unless marked):

Taskbench wire cost, per-family B-prompt deltas (occ1 - occ0, glm-5.3 listener):
A -15.1, B +32.1, C -25.0, D +94.7, E -10.0. OCC-1 also pays fixed protocol overhead:
A-system prompt +90 tokens, A-completion +135.9 tokens (incl. hidden reasoning).
Message chars mean 406.2 (occ0) vs 373.3 (occ1).

Taskbench latency (wall clock): A occ0 p50 4.42 s / p95 8.76 s; A occ1 5.38 / 13.48;
B occ0 glm-5.3 1.69 / 5.13; B occ0 flash 0.81 / 3.62 (excluding one 149.85 s gateway
hiccup with a correct answer); B occ1 glm-5.3 1.68 / 3.30; B occ1 flash 0.82 / 1.58.

Codec corpus (offline, 69 typed messages): plain 23,505 B; JSON 39,148 B (serde compact);
OCC-1 24,361 B; symbolic 23,316 B; dual 48,781 B. Symbolic <= OCC-1 on 69/69 messages;
listener context (symbolic section) is 52.2% smaller than the dual wire form; fixture
comparison (28 scenarios): occ2 14,027 B vs occ1 14,659 B, smaller on every scenario.

Official SILO adapter metrics (Level I, n2, msg, max_rounds 20, 1200 s case budget):
occ0 glm-5.3: mean C 4159.7 tokens/round, mean D 1.83, 9 completed / 1 timeout (I-10),
mean rounds 7.9, 33 inter-agent messages (7,657 B). occ0 flash: mean C 5142.9, mean D 2.0,
10/10 completed, rounds 6.0, 40 messages (10,558 B). occ1 flash: mean C 5620.4, mean D 2.1,
rounds 5.6, 42 messages (12,173 B), per-case-mean LLM latency p50 4.29 s.

Reading of the scorecard (honest): on task accuracy OCC-1 already saturates the taskbench
(100%) and beats English on exact-form answers (the two occ0 failures were
answer-normalization misses: A-dev-010 "Okafor" vs "release engineer okafor",
C-dev-010 "Stage the new public key." vs "stage the new public key"). On wire cost,
uncontrolled OCC-1 is a wash or a loss (mean +15.3 tokens; family D +94.7 when the speaker
explodes one record into one field per attribute, 48 fields vs 6). The symbolic layer wins
per-message everywhere but only by 4.3% aggregate on this corpus because OCC-1 key lines
are already short; the packing layer is where the remaining headroom is (-22.6% vs OCC-1 on
the real-token SAMPLE). End-to-end sym-v1 task numbers: SILO official 0.9 case accuracy / 1.0 mean S (one shared wall-clock timeout case).

## 3. Implementation record

Shipped components (files, sizes, tests):

| Component | Files | Content | Tests |
|---|---|---|---|
| Symbolic codec | `core/occ/occ2.rs` (2,237 lines incl. tests) | strict OCC/2 header (`D=<dialect>@<version> C=<sha256[:12]>`), symbolic field lines, dual payloads with `#FALLBACK-OCC1`, `occ1_fallback`, typed error inventory | 14 unit tests |
| Dialect registry | `core/occ/dialect.rs` (1,122 lines incl. tests) | `DialectCard` (grammar, symbol table, sha256 codebook hash, compatibility, metrics, fallback policy), `DialectRegistry`, `OccCapability` negotiation; built-in sym-v1, codebook sha256 pinned and cross-checked against an independent Python sha256 | 8 unit tests |
| Native integration | `agent_messages.rs` (+611/-3), `subagent_runs.rs` (+89/-2), `settings_manager.rs` (+18), `daemon_mode.rs` (+29) | `wrap_outgoing_body` + `Occ2SendPolicy` at S1 (direct/broadcast) and S2 (spawn prompts); sentinel split before prompt build; strict-then-occ1 decode; `Occ2StrikeLedger`; `occ_capability` field; `occ_capability_for(session)` settings-gated advertisement; `agentCommunication.occ2Enabled` default OFF | covered by the fidelity suites |
| Telemetry | `activity.rs` (+76/-5), `communication_activity.rs` (+78/-13), `subagent_summary_line.rs` (+28), `daemon_session_list.rs` (+53) | strict Symbolic classify arm; SYM third indicator slot (TXT/OCC/SYM, neon-blue); roster kind "symbolic" content-free; LAT reserved | 4 new lib tests + updated pins |
| Integration test suite | `tests/occ2_comm_fidelity.rs` (994 lines) | 12 tests over `research/tasks/msgs_corpus.json` (69 msgs, 18 declared holdout): dual round trips, fault injection (unknown dialect, hash mismatch, corruption), sentinel split, size guard, negotiation absence, durability/compaction survival, classify strict arm, three-strike, latency, aggregate report | 12 tests |

Line counts follow the codec worker's final report (authoritative); the merge
inventory's 2,238/1,123 differs only by a trailing-newline count.

Authorized cross-file edit: `daemon_mode.rs` +6 lines (`occ_capability: None` at two
struct literals, ~:13772 and ~:13853, with owner comments), later superseded by the
telemetry worker's `occ_capability_for` helper. Deferred to parent at merge (recorded in the
`.changes` fragment): evolved-dialect card loading at session start, the runtime recorder
that feeds `Occ2StrikeLedger` from real decode incidents, model-pair and task-family gating
on card metadata, compaction-prompt note. Known asymmetry until daemon-side fill was landed:
flag-on direct sends stay OCC-1 when no capability is advertised; S2 spawn prompts
dual-encode here()->here().

Gate table (all commands in section 7; CPU rule CARGO_BUILD_JOBS=3 observed):

| Gate | Result |
|---|---|
| OCC-1 regression suite `occ_comm_fidelity` | 7/7 (reproduced on base, then green in 4 further runs by occ2-native, 2 by occ2-telemetry) |
| New `occ2_comm_fidelity` suite | 12/12 (4 green runs occ2-native, 2 occ2-telemetry) |
| `core::occ` unit tests (incl. 22 new: 14 occ2 + 8 dialect) | 204 passed / 0 failed, 3 consecutive runs (codec worker; 4th confirmation run) |
| `communication_` lib tests (touched crate scope) | 13/13 (4 new + 9 existing, incl. 3 updated to the 3-slot format; 2 runs) |
| lib suite in touched scope | 286/0; pre-existing failures verified NOT campaign-caused (stash A/B comparison) |
| `cargo check --workspace` | exit 0, clean (single final run per CPU rule) |
| rustfmt | clean |
| Python OCC-1 codec suite (`prime-agent-runtime/test/test_occ1_codec.py`, 44 tests) | 44/44 OK on the integrated tree (parent-run; the Rust changes must leave the Python codec untouched) |
| Corpus/task generators self-test | 306/306 (300 gold + solver re-derivations + 6 negative controls), byte-identical regeneration |

## 4. Research references (pinned)

OCC-2 track (from `research/symbolic_comm/RESEARCH-NOTES.md` section 6):

| Source | Pin | License | Role |
|---|---|---|---|
| CLSR paper (Pei et al., ICML 2026, PMLR v306 pei26a) | local PDF `pei26a_clsr.pdf` | PMLR open access | primary algorithm source (evolutionary bootstrapping, Pareto selection, router cards) |
| CLSR code `pzqpzq/LSF_MDia` | verified 404 (web + GitHub API, re-verified 2026-10-16) | none | nothing to pin; recorded as a negative result |
| MOC (Guan et al., arXiv 2606.02359) | `9c67c92507570704a7df73e452552a3f49e83897` | NO license file: ideas only, no code copied | ISM merge structure, hop aggregation, never-drop-on-failure |
| AgentPrune (Zhang et al., ICLR'25) | `c544dd6a1858c02c6d5d371d23c6e6ff55e0be21` | NO license file: ideas only | utility-driven selection, never-prune classes, pruning floor |
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
| MARBLE / MultiAgentBench | `8d60fa17` | MIT | secondary benchmark (infeasibility documented, section 5) |
| MAST | `a70542e5` | no license file: ideas only | failure taxonomy (prior campaign) |
| rlm | `d04208af` | MIT | runtime lineage (prior campaign) |

License handling: MOC, AgentPrune, and MAST ship no license file and are treated as
all-rights-reserved; only ideas were re-implemented, with citations. No code was copied from
any no-license reference. CLSR has no released code at all.

## 5. Negative findings and honest caveats

1. OCC-1 is not cheaper than English without packing discipline. Real-tokenizer taskbench:
   mean +15.3 B-prompt tokens, median -1.0, cheaper on only 25/50 tasks; family D +94.7
   tokens when the speaker emits one field per attribute (48 fields vs 6). OCC-1 also pays
   +90 A-system format tokens and +135.9 A-completion tokens (incl. hidden reasoning).
   Compactness is won or lost in field packing, not in the KEY=VALUE grammar itself; OCC-2's
   grammar must encode "one field per record, attributes inline in the value".
2. Gateway `usage.completion_tokens` includes hidden reasoning tokens (probe: glm-5.3-flash
   content "ok" reported 92 completion tokens; glm-5.2 content "2" reported 95). All
   output-token columns above are server-cost proxies only; wire-volume claims use
   `usage.prompt_tokens` only.
3. `LSF_MDia` (the CLSR paper's promised code release) does not exist: verified 404 on web
   and GitHub API again this campaign. No executable artifact to evaluate or pin.
4. MARBLE main environments are infeasible offline (Java+Node, Docker+Postgres, code
   execution, internet APIs). Only the werewolf environment is plausibly runnable, untested;
   first check is whether the gateway honors request-level `tools` (native function calling,
   werewolf_agent.py L481/L828). Documented, not run.
5. Proposer-prompt hangs: CLSR-style long structured-synthesis prompts hang on BOTH glm-5.3
   and glm-5.3-flash (no return within 60-240 s at temperature 0; simple JSON calls return
   in 1-3 s; client-side behavior is fine, gateway-side). Contingency applied: shortened and
   chunked prompts. One model-assisted recombination was accepted after the workaround; the
   unmitigated long-prompt path is recorded as a negative result with probe evidence.
6. sym-v1 wins per-message (69/69) but the aggregate saving over OCC-1 is only ~4.3%
   (corpus 23,316 vs 24,361 B; fixture 14,027 vs 14,659 B, smaller on every scenario),
   because OCC-1's key lines are already short. Note: the draft brief described this as
   "+4.3% aggregate bytes"; the on-disk measurements show sym-v1 SMALLER by 4.3% on both
   corpora, and this report states the measured direction. The larger saving lives in the
   packing layer (-22.6% vs OCC-1 on the real-token SAMPLE) and in dual-payload avoidance
   (a dual body is ~2x OCC-1; the listener only sees the symbolic section).
7. Card amortization: a dialect card costs ~200-300 tokens in context for ~32 tokens/message
   saved vs OCC-1; break-even is ~7-9 structured messages per session. Sessions with fewer
   should stay OCC-1 (selection-policy input, not yet implemented).
8. Saved-tree capability gap: fully saved passive RLM trees have no resident settings source
   and advertise no capability (conservative OCC-1). Documented, intentional, reported in the
   telemetry `.changes` fragment.
9. SILO I-06 (Checksum) under glm-5.3: both agents exchanged local XOR checksums and a
   correction, but agent 1 still submitted stale data (S=0.5) - a textbook
   communication-reasoning gap, not a transport failure. I-10 (Standard Deviation) timed out
   at the 1200 s case budget in round 6 of 20 - a wall-clock budget artifact, not an engine
   failure. glm-5.3-flash beat glm-5.3 on this suite (10/10 vs 8/10) because the big model
   burned 67,232 completion tokens on I-06 and exceeded the clock on I-10.
10. The single-process desktop-heap issue is from the PRIOR campaign's report; it is not
    part of this campaign and no claim is made here.
11. Runtime caveats: the three-strike ledger is pure and tested but has no runtime recorder
    yet (daemon never decodes bodies today); deltas are excluded from OCC-2 wrapping in v1
    (codec covers `AgentCommMessage` only); `sym-v1` is explicitly a starting-point card
    (R3 in the integration design).
12. deepseek-v4-flash is retired (HTTP 410 since 2026-09-25); Azure-routed models returned
    503 (auth) during the campaign and stayed off the critical path. Model lists go stale;
    re-probe /health before each session.

## 6. Bugs found / fixed this campaign

The product BUG-LEDGER (`reports/occ23-campaign/BUG-LEDGER.md`) is empty: no OCC-2 product
bugs were found; all campaign gates were green on first landing, and the lib-suite
comparison against a stashed tree verified the pre-existing Windows failures were not
campaign-caused. The defects that WERE found and fixed lived in campaign research harnesses
and are recorded in the EXPERIMENT-LEDGER:

1. OCC-3 harness probe defect 1: probe prompts lacked chat-template turns, so even the
   full-text positive control scored 0/10. Fixed in the v2 protocol (chat-template turns).
2. OCC-3 harness probe defect 2: `interventions.py` logits probe mutated the shared KV
   cache (missing `use_cache=False`); A-vs-A KL was 1.99 instead of ~0. Fixed; the
   corrected protocol requires with-cache-correct AND no-cache-wrong per trial.
3. OCC-3 harness probe defect 3: Experiment B fed raw post-norm hidden states (std 2.4)
   into `inputs_embeds` without LatentMAS norm-rescale, drifting 48-85 per step. Fixed by
   porting the rescale (and optional ridge map) per RESEARCH-NOTES sections 1.2-1.3.
4. Gateway/official-engine incompatibility (worked around, not a repo bug): the gateway
   extracts SILO's fenced XML tool-call blocks into native OpenAI `tool_calls` with empty
   `message.content`, which the official engine cannot parse. `adapter/gateway_proxy.py`
   forwards request bytes verbatim (keeping real `usage.prompt_tokens`) and reconstructs
   the fenced blocks in `message.content`; it fired on 100/117 (responses/blocks) in the
   glm-5.3 pilot and 102/129 in the flash pilot, fully audited in
   `runs/<run>/proxy_rewrites.jsonl`. deepseek models pass the fence natively.
5. Ops: concurrent cargo builds from two workers saturated all 12 logical CPUs (12 MSVC
   link.exe). Resolved by 8-core affinity masks plus the durable campaign rule
   CARGO_BUILD_JOBS=3, package-scoped tests, one workspace check at the end.

## 7. Reproducibility

Rust environment (Git Bash, every cargo call; CPU rule: `CARGO_BUILD_JOBS=3`):

```
export RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup
export CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo
export PATH="/c/Users/openclawuser/optimus-rust-toolchain/cargo/bin:/c/Program Files/nodejs:$PATH"
export KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe
# per-worker isolated target dirs, e.g. CARGO_TARGET_DIR=/c/Users/openclawuser/optimus-occ23-target-w3
cargo test -p pi-coding-agent --test occ_comm_fidelity      # 7/7
cargo test -p pi-coding-agent --test occ2_comm_fidelity     # 12/12
cargo test -p pi-coding-agent --lib occ::                    # 204 passed
cargo test -p pi-coding-agent --lib communication_           # 13/13
cargo check --workspace                                     # exit 0 (once, at end)
```

Python (deterministic generators and suites; Python 3.11.15 on PATH):

```
python research/tasks/gen_tasks.py      # 306/306 self-test, byte-identical regeneration
python research/tasks/gen_corpus.py     # 69-msg corpus validation + stats
python -m unittest discover -s prime-agent-runtime/test -p "test_occ1_codec.py"   # 44 tests, 44/44 OK
```

Real-model measurements (require the local gateway at 127.0.0.1:43120 and its token file;
the token is never printed):

```
python research/taskbench/runner.py smoke                       # 5 tasks x 2 modes
python research/taskbench/runner.py run --tasks-per-family 10   # the 300-call baseline matrix
python research/taskbench/runner.py aggregate                   # rebuild results_taskbench.json

# official SILO-BENCH (engine venv requires Python >= 3.13):
uv venv --python 3.14 research/benchmarks/adapter/.venv
uv pip install --python research/benchmarks/adapter/.venv/Scripts/python.exe \
    openai pydantic pyyaml srsly tenacity httpx
research/benchmarks/adapter/.venv/Scripts/python.exe research/benchmarks/adapter/pilot_run.py \
    --config research/benchmarks/adapter/config.json --run <run-name>
# landed runs: pilot-levelI-n2-msg-glm53{,flash}, occ2-levelI-n2-msg-glm53flash-occ1
# in flight at draft time: occ2-levelI-n2-msg-glm53flash-sym (no scorecard yet)

# dialect evolution (research/symbolic_comm):
python research/symbolic_comm/run_all.py    # fidelity + mutation suites + evolve entry points
```

Artifacts: taskbench raw records in `research/taskbench/taskbench_runs.jsonl` (re-analyzable
without new model calls); scorecards in `research/benchmarks/runs/<run>/scorecard.{json,md}`
plus per-case summaries and `proxy_rewrites.jsonl`; evolution log
`research/symbolic_comm/evolution-log.jsonl` and candidate card
`research/symbolic_comm/evolution-winner.json`. Holdout discipline: `tasks_holdout.json`
(150 tasks) and the 18 holdout corpus stories were never touched by any tuning call; the
evolution pool freezes before the first HOLDOUT call, with card checksum and DEV summary
reported before holdout evaluation.

## 8. Remaining work and next steps

1. Evolved OCC-2 dialect: frozen (sym-v1-pt-pt, checksum 17836322...); DEV-validated only. HOLDOUT evaluation was halted before execution and remains open
   (`evolution-winner.json`, id `sym-v1-pt-pt`, checksum
   `17836322dced3bb56e10bb0466abb5cb8d463ac6e4aba471f906ad5f24a4d49b`, lineage: packing
   toggles + microtemplate induction + symbol pruning), but it is NOT frozen, has no
   EVOLUTION-REPORT.md yet, and HOLDOUT is untouched. Freeze discipline: report card
   checksum + DEV summary, then holdout eval, then the frozen-card compatibility matrix.
2. Official OCC-1 vs sym comparison: sym-v1 flash completed (0.9/1.0, 153.8 vs 289.8 B/msg); occ1 glm-5.3 cell stopped mid-flight and excluded
   (9 of 10 case files present, no scorecard). After it lands: occ1 with glm-5.3, then the
   evolved card at the same measurement points. `OCC2-OFFICIAL-RESULTS.md` does not exist
   yet and is the designated landing spot.
3. Model-pair matrix: not executed (5 cells implemented).
   Gen-0 early signal only: sym-v1 listener fidelity 1.0, median 117 wire tokens, on the
   EVOLVE split with the flash listener.
4. Post-merge gates: Python 44/44 on the integrated tree; final re-stat of the diff;
   parent-owned merge items from the `.changes` fragments (evolved-card loading, strike
   runtime recorder, pair gating, compaction note).
5. SILO milestone scale-up: 3-5 seeds per case for variance, n5 agent counts, Level II/III,
   broadcast and sfs protocols, larger `case_timeout_s` (I-10 showed 1200 s can cut off
   converging cases), and the OCC-2 encoded-channel comparison at identical measurement
   points.
6. OCC-3 go/no-go: EXECUTED - NO-GO per pre-registration (V1-V3 PASS, G1 PASS, G2 not
   measured at 4096 tokens, G3 FAIL for Qwen at 2,443.1 MiB peak RSS vs the 1 GiB budget,
   G4 untested). Scorecard delivered: `reports/occ23-campaign/OCC3-SCORECARD.md`;
   section 9 summarizes. Production design retained for a future revisit; no
   production-integration work remains for this campaign.

## 9. OCC-3 result (NO-GO per pre-registration; full scorecard: reports/occ23-campaign/OCC3-SCORECARD.md)

- Verdict: NO-GO for production integration in this campaign, decided per the
  pre-registered framework (`research/integration/OCC3-INTEGRATION-DESIGN.md` section 1;
  verdict entry `reports/occ23-campaign/EXPERIMENT-LEDGER.md` 2026-10-16 ~12:5xZ). Gate
  outcomes: V1-V3 PASS (Qwen chat probe; SmolLM2 under its valid induction probe), G1 PASS,
  G2 NOT MEASURED at the pre-registered >= 4096-token scale (probe contexts are 59-108
  tokens; the 0.72x receiver ratio at small scale is not a G2 claim), G3 FAIL for Qwen
  (harness peak RSS 2,443.1 MiB vs the 1 GiB sidecar budget, about 2.4x over; the 1.99 MB
  per-handoff artifact itself is far under the 128 MiB cap - model residency is the breach),
  G4 UNTESTED (no over-bound context trials). GO requires G1 + G3 + (G2 or G4); the
  decision is NO-GO, not INVALID (no V gate failed). No production code exists or is
  claimed; `core/occ/mod.rs` keeps saying "not implemented"; the LAT indicator stays
  reserved; the production design is retained on disk for a future revisit (its own
  triggers: stable cross-instance cache API, trained receiver head, over-bound context
  need; plus a GPU / larger-context rerun for G2/G3).
- Handoff result (Experiment A, protocol v2): the KV-cache handoff is real and causally
  proven on both harness models, each under the probe that model can actually do. Qwen2.5-0.5B
  chat probe: 50/50 with-cache, 50/50 full-text control, 0/50 no-cache, 50/50 proven trials,
  paired bootstrap diff 1.0, 95% CI [1.0, 1.0]. SmolLM2-135M: chat probe invalid as an
  instrument (its own full-text control 15/50 - a capability ceiling, not a harness
  failure); raw induction probe 49/50 / 49/50 / 0/50, 49/50 proven, CI [0.94, 1.0].
- Latent continuation (Experiment B) is a genuine NEGATIVE finding, distinct from the clean
  handoff: on Qwen2.5-0.5B accuracy degrades monotonically with latent steps (none 10/10
  at 0 steps, 8/10 at 1, 8/10 at 2, 2/10 at 4, 0/10 at 8; rescale and rescale_ridge degrade
  at least as fast: 9/10 at 1 step, 3/10 at 2, 0/10 from 4), while RANDOM latent vectors
  hurt far less (10/10 at 1-4 steps, 9/10 at 8): real latent continuations corrupt the cache
  more than injected noise does. Stated plainly: latent continuation degraded accuracy on
  the untrained small models tested. That does not rule out trained or future variants
  (the Interlat pattern trains a receiver head precisely because raw latent reuse does not
  transfer); it rules out shipping THIS mechanism now. SmolLM2 shows no effect in either
  direction on its valid probe (9-10/10 at every step count).
- New finding (intervention battery): the consistent-shuffle cache control is a NULL
  intervention - 20/20 correct with KL exactly 0.0 on Qwen. Attention over a fully
  attended prefix is permutation-invariant when k and v move together (RoPE is already baked
  into the cached keys; softmax weights stay attached to their pairs). The pre-registered
  "shuffled order" perturbation therefore tests nothing on this probe class and is
  reclassified as an order-invariance control; the informative pairing control is the
  unpaired shuffle (1/20, mean KL 7.56). All content-destroying perturbations separate
  cleanly (0/20, KL 2.4-15.3), so V3 specificity holds.
- Cost honesty: an 81-token KV artifact is 1,991,147 B fp32 (995,573 B fp16) against a
  ~49 B text payload - roughly 40,000x, at 24 KiB per cached token on Qwen (45 KiB on
  SmolLM2). The cache wins receiver wall-clock at this scale (0.85 s vs 1.19 s re-prefill,
  0.72x) and continuation without re-encode; it does not win bytes. A 4096-token Qwen cache
  projects to ~96 MiB fp32 (inside the 128 MiB per-handoff cap), but fp32 model residency
  breaches the 1 GiB sidecar budget (G3).
- Full tables - the campaign-required mode x model scorecard (no communication / full text
  / OCC-1 compact text / KV-cache handoff / latent continuation), the intervention battery
  (A-K plus H2 and the shuffle null), the latent-step grid, per-trial records, and the
  gate-by-gate verdict - are in `reports/occ23-campaign/OCC3-SCORECARD.md`; raw JSON in
  `research/latent_handoff/results/{qwen05,smollm2}-results.json`.

---

Draft written by the campaign report worker from the on-disk ledgers and reports
(EXPERIMENT-LEDGER, TASKBENCH-BASELINE, PILOT-REPORT, BASELINE, integration designs,
`.changes` fragments, run scorecards, git diff vs base). Not-executed slots are stated in the tables above
listed in section 8; the parent finalizes.