# PR description draft: OCC-2 symbolic dialects + OCC-3 latent research

Proposed title: `feat(occ): symbolic dialects (OCC-2) + latent communication research (OCC-3)`. The title says "symbolic", not "learned": the shipped
`sym-v1` card is hand-designed; the evolution engine produced and froze `sym-v1-pt-pt` as a research artifact (DEV-only validation; HOLDOUT not executed).

Branch `improve/occ2-occ3-communication`, base `ce8480d59` (v0.1.32). Change set: 10 tracked files +998/-25; new `core/occ/occ2.rs` (2,237 lines incl.
tests), `core/occ/dialect.rs` (1,122), `tests/occ2_comm_fidelity.rs` (994, 12 tests); three `.changes` fragments; the campaign research trees
(`research/`) and reports (`reports/occ23-campaign/`). No dependency changes; no Python runtime file touched.

## Summary and motivation

OCC-1 (v0.1.30) made agent-to-agent task/result traffic typed and corruption-detectable, but its compactness is bounded by KEY=VALUE line overhead:
the campaign's real-tokenizer baseline shows OCC-1 is a wash on wire cost (benchmark table below). Compactness lives in field-packing discipline, not
the grammar. OCC-2 answers that: a second, symbolic encoding of the same `optimus.agent-comm.v1` contract behind a data-driven dialect registry, so
compactness rules (short symbols, packed records, value abbreviations, microtemplates) become card data that can be evolved offline and negotiated per
agent pair. OCC-3 (latent communication) is researched, not shipped: the harness ran the pre-registered experiments, recorded an honest negative
result for latent continuation plus resource-gate failures, and nothing integrates.

## What ships (all behind `agentCommunication.occ2Enabled`, default OFF)

- `core/occ/occ2.rs` (14 unit tests): strict symbolic wire codec over the full typed contract. Header `OCC/2 D=<dialect>@<version> C=<codebook
  sha256[:12]>`; compact symbolic field lines; strict decode (unknown dialect, version miss, codebook hash mismatch, malformed line, duplicate field,
  indexed gap, dangling ref, partial verification group => Err, never invented content); dual payloads (symbolic section + `#FALLBACK-OCC1` sentinel +
  full OCC-1 text) that must agree on decode or are rejected; `occ1_fallback` for registry-free recovery.

- `core/occ/dialect.rs` (8 unit tests): dialects as data. `DialectCard` (grammar, symbol table, sha256 over the canonical codebook serialization,
  compatibility, metrics, fallback policy), `DialectRegistry` (unknown dialect => conservative OCC-1), `OccCapability` negotiation; the built-in
  `sym-v1` card, its codebook sha256 pinned in a codec test:
  `1efce9992ee24ff779a0430d998b37704d484f9cbb90c2fb78feec063add1547`.

- Native integration (`agent_messages.rs`, `subagent_runs.rs`, `settings_manager.rs`): `wrap_outgoing_body` + `Occ2SendPolicy` at both send seams
  (`agent_message.send` direct/broadcast, RLM child-spawn prompt); sentinel split before prompt build (recipient sees the symbolic section; the
  durable transcript stores the dual payload verbatim, so `occ1_fallback` reconstructs the payload after restarts with no cards loaded);
  strict-then-fallback decode; pure `Occ2StrikeLedger` (3-strike pair disable); optional `occ_capability` negotiation field on agent summaries; a
  16,384-char size guard that sends plain OCC-1 rather than truncating; deltas excluded from wrapping in v1.

- Telemetry (`activity.rs`, `communication_activity.rs`, `subagent_summary_line.rs`, `daemon_session_list.rs`, `daemon_mode.rs`): settings-gated
  `OccCapability::here()` advertisement via `occ_capability_for(session)` (absent = OCC-1); SYM activated as the third communication-indicator slot
  (TXT/OCC/SYM); a strict `Symbolic` classify arm firing only when `occ2::decode` succeeds (fallback-consumed reports Occ1); LAT stays reserved.

## OCC-3: research harness, measured results, no integration

- Harness (`research/latent_handoff/`, CPU, SmolLM2-135M-Instruct + Qwen2.5-0.5B-Instruct, LatentMAS mechanism ported to transformers 5.20.0):
  KV-cache handoff, bounded latent continuation, intervention battery. Three harness defects were found and fixed under a pre-registered acceptance
  rule (full-text positive control must pass; per-trial proof: with-cache correct AND no-cache wrong).

- Experiment A (KV-cache handoff): gate PASS on both harness models, each under the probe that model can do. Qwen2.5-0.5B chat probe: 50/50
  with-cache, 50/50 full-text control, 0/50 no-cache, 50/50 proven (paired bootstrap diff 1.0). SmolLM2-135M chat probe fails its own positive control
  (15/50; capability ceiling, not harness failure) but the raw induction probe passes 49/49/0/49, bootstrap CI [0.94, 1.0]. The intervention battery
  separates the correct cache from no/zeroed/random/unrelated/perturbed caches (KL 0 vs large), with one new null finding: consistent within-pair k/v
  shuffle is a null intervention.

- Experiment B (latent continuation): genuine NEGATIVE. Qwen2.5-0.5B private-fact probe accuracy degrades monotonically with latent steps (no
  transform 10/10 at 0 steps, 8/10 at 1-2, 2/10 at 4, 0/10 at 8; rescale/ridge at least as fast: 3/10 at 2, 0/10 from 4) while RANDOM latent vectors
  hurt far less (10/10 at 1-4, 9/10 at 8): real latent continuations corrupt the cache more than injected noise.

- Verdict per the pre-registered go/no-go framework (GO = V1-V3 + G1 + G3 + (G2|G4)): V gates and G1 pass; G3 FAILS for the only chat-probe-capable
  model (fp32 sidecar RSS 2.44 GiB vs the 1 GiB cap); G2/G4 unmeasured at their defined scales. Decision: NO-GO for production integration this
  campaign. The harness, corrected protocol, negative findings, the OCC-3 scorecard
  (`reports/occ23-campaign/OCC3-SCORECARD.md`) and the production design (`research/integration/OCC3-INTEGRATION-DESIGN.md`) ship as research; no
  runtime code, and the LAT indicator stays reserved.

## Evidence summary

Gates (commands and artifacts in `reports/occ23-campaign/PR-EVIDENCE.md`):

| Gate | Result |
|---|---|
| `core::occ` unit tests (`--lib occ::`) | 204 passed / 0 failed (base 181 + 22 new codec/registry + 1 classify; 3 consecutive runs + confirmation) |
| OCC-1 regression suite `occ_comm_fidelity` | 7/7 (reproduced on base first; 4 further green runs by occ2-native, 2 by occ2-telemetry) |
| New `occ2_comm_fidelity` suite (69-msg corpus, 18 holdout) | 12/12 (4 green runs occ2-native, 2 occ2-telemetry) |
| `communication_` lib tests (touched scope) | 13/13 (4 new + 9 existing, 3 updated to the 3-slot format) |
| Lib suite in touched scope | 286/0; pre-existing failures verified NOT campaign-caused (stash A/B) |
| `cargo check --workspace` / rustfmt | exit 0 / clean (single final run per the campaign CPU rule) |
| Python OCC-1 codec suite (44 tests) | 44/44 OK on the integrated tree |
| OCC-1 files | `occ1.rs`, `contract.rs`, `delta.rs`, `consolidation*`, `prune*`, `codec_tests.rs`, `occ_comm_fidelity.rs` + fixtures byte-identical to base |

Benchmarks (real models through the local gateway; scorecard paths in PR-EVIDENCE):

| Measurement | Result |
|---|---|
| Taskbench accuracy (50 tasks/cell, strict, glm-5.3 speaker) | OCC-0 96% (48/50) both listeners; OCC-1 100% (50/50) both; glm-5.3-flash matches glm-5.3 at half the listener p50 latency (0.81 s vs 1.69 s) |
| Taskbench wire cost (real tokenizer, byte-identical B templates) | OCC-1 vs OCC-0 mean +15.3 / median -1.0 B-prompt tokens; families A -15.1, C -25.0, E -10.0 vs B +32.1, D +94.7 |
| Codec corpus (offline, 69 messages, 18 holdout) | symbolic <= OCC-1 on 69/69; aggregate 23,316 B vs 24,361 B (-4.3%); -8.0% PROXY tokens; dual encode 137.5 us / decode 82.3 us vs 5,000 us budget; all 69 duals within the 16,384-char bound |
| Real-token dialect sample (DEV 8-msg, glm-5.3 tokenizer) | OCC-0 86.0, OCC-1 143.6, sym-v1 no-pack 116.6, sym-v1 full-pack 111.1 tok/msg (packing -22.6% vs OCC-1) |
| Model-in-loop dialect eval (glm-5.3-flash listener, EVOLVE split) | sym-v1 listener fidelity 1.0, comprehension 10/10, median 117 wire tokens (10-message EVOLVE sample, median) vs OCC-1 143.6 (8-message DEV sample, mean) (-18.5%) |
| Official SILO-BENCH (pinned engine, Level I n2, msg, 10 cases) | OCC-0 glm-5.3 0.8 case accuracy / 0.944 mean S; OCC-0 glm-5.3-flash 1.0 / 1.0; OCC-1 glm-5.3-flash 1.0 / 1.0 (191,634 real prompt tokens, 97 calls); sym-v1 glm-5.3-flash 0.9 / 1.0 (253,864 real prompt tokens, 88 calls; one wall-clock timeout case, message bytes 153.8/msg vs OCC-1 289.8) |

## Rollout and safety

- Default OFF everywhere: `agentCommunication.occ2Enabled` defaults to `false`; an agent summary without `occ_capability` means OCC-1. Flag off =>
  queueing, receipts, rate limits, roster and prompt-id framing untouched; sends byte-identical OCC-0/OCC-1 (pinned by unit test and fidelity t05; the
  OCC-1 suites are unchanged).

- Sender fallback chain: flag off, no negotiated dialect, non-contract body, encode error, or a dual payload over the 16,384-char bound => the
  original body is sent unchanged (plain OCC-1; never truncated).

- Recipient fallback chain: strict `occ2::decode` first (any dialect/hash/grammar fault is an error, never a guess); an OCC/2 body then decodes
  through its embedded `#FALLBACK-OCC1` section, which needs no dialect cards and survives daemon restarts and compaction; dual sections must agree or
  the payload is rejected. A pure three-strike ledger disables the symbolic path per (sender, listener) pair after 3 strict-decode failures (runtime
  recorder wiring is future work).

- Telemetry reflects the wire, not the flag: SYM lights only for delivered bodies that strict-decode as symbolic; with the flag off this daemon never
  sends OCC/2 itself, so SYM can only light for externally-authored OCC/2 bodies.

## Testing

Every shipped claim is pinned offline and deterministically: round-trip fidelity and fault injection over the 69-message corpus including the 18
declared holdout (t02, t04); identity-when-disabled and the size guard (t05, `occ2_disabled_policy_is_the_identity`); the prompt/record seam split
(t06); negotiation absence (t07); durability and compaction-survival with an empty registry (t08); the strict classify arm (t09, the `activity.rs`
unit test, and a roster round-trip over a real `encode_dual` payload in `daemon_session_list.rs`); three-strike pair disable (t10); latency budget
(t11); the sym-v1 codebook hash (occ2.rs codec test) and the registry validation (dialect.rs tests). Real-model claims are reproducible from the recorded raw artifacts with
the commands in PR-EVIDENCE.

## Non-claims (explicit)

- No end-to-end task-accuracy claim for OCC-2 beyond the SILO run above (sym-v1 0.9 case accuracy vs OCC-1 1.0 on the same 10 cases, one shared wall-clock timeout); the -24.3% wire-token
  figure is a measured sample (median, EVOLVE split, one listener), not a production result.

- `usage.completion_tokens` at the gateway includes hidden reasoning tokens and is never used for wire-cost claims; only `usage.prompt_tokens` (real
  server-side counts) are. PROXY tokens (whitespace+punctuation split) are marked wherever no model call exists; real-token SAMPLE numbers are marked
  SAMPLE.

- The campaign ran on an internal gateway; cash cost is zero and "cost" means gateway call counts plus real prompt-token totals. Single run per
  benchmark cell; no repeat-run variance.

- No code was copied from no-license references (MOC, AgentPrune, MAST: ideas only, with citations); the CLSR paper's promised code release does not
  exist (verified 404).

- Future work, not claimed: evolved-dialect freeze + holdout evaluation, the model-pair matrix, the strike-ledger runtime recorder, evolved-card
  loading at session start, model-pair/task-family gating on card metadata, and any OCC-3 runtime (NO-GO above).

## Final evaluation status

- Official SILO-BENCH sym-v1 run (pinned engine, Level I n2, msg protocol, glm-5.3-flash,
  10 cases, one wall-clock timeout): case accuracy 0.9, mean S 1.0, 88 LLM calls, 253,864
  prompt tokens, 36 inter-agent messages at 153.8 bytes/message (OCC-1 same protocol:
  289.8 bytes/message). Scorecard: `research/benchmarks/runs/occ2-levelI-n2-msg-glm53flash-sym/`.
  The planned occ1 glm-5.3 comparison run was stopped mid-flight and is not included.

- Evolved dialect: the full-protocol evolution run (deterministic + model-assisted
  operators, 4 accepted model-generated children) completed and froze
  `sym-v1-pt-pt` (generation 5, stagnation stop; checksum
  `17836322dced3bb56e10bb0466abb5cb8d463ac6e4aba471f906ad5f24a4d49b`) to
  `research/symbolic_comm/dialect-card.json`. DEV split: deterministic fidelity 1.0
  (20/20), wire tokens median 101.0 vs OCC-1 133.5 (-24.3%), listener payload-equality
  1.00 (10/10). The HOLDOUT evaluation was not executed; the frozen card is therefore
  published as an artifact, not as a validated default.

- Model-pair compatibility matrix: implemented in `research/symbolic_comm/eval.py`
  (5 of the 10 planned cells) but not executed; no pair-compatibility numbers are claimed.

- Python OCC-1 codec suite on the integrated tree: 44/44 OK.

All model-in-the-loop evaluation runs above were executed through the local model
gateway (glm-5.3 / glm-5.3-flash / deepseek-v4.1-flash chat completions); token counts
are real server-side tokenizer usage from that gateway.

Full evidence index: `reports/occ23-campaign/PR-EVIDENCE.md`. Campaign detail: `reports/occ23-campaign/FINAL-REPORT.md` (sections 1-3 and 8 are this
PR body's backbone).
Full evidence index: `reports/occ23-campaign/PR-EVIDENCE.md`. Campaign detail: `reports/occ23-campaign/FINAL-REPORT.md` (sections 1-3 and 8 are this
PR body's backbone).
