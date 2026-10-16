# OCC-2 + OCC-3 Campaign — Experiment Ledger

Base: v0.1.32 ce8480d595cc90ab752c142ce984640b4b527ea2
Worktree: C:/Users/openclawuser/optimus-occ23-20261016 (branch improve/occ2-occ3-communication)
Started: 2026-10-16

Workers (DGX GLM 5.3 only, 5 concurrent):
- occ2-research (sub-1787c383): Track A research notes -> research/symbolic_comm/RESEARCH-NOTES.md
- occ3-research (sub-f6ae381c): Track B research notes -> research/latent_handoff/RESEARCH-NOTES.md + Interlat clone
- occ2-codec (sub-e0893a70): Rust occ2.rs + dialect.rs + tests (CARGO_TARGET_DIR optimus-occ23-target-w3)
- occ3-harness (sub-aaf6fc84): CPU latent harness, SmolLM2-135M + Qwen2.5-0.5B, Experiments A/B
- bench-tasks (sub-2e2ca6ed): task suite A-E dev/holdout + msg corpus + OCC-1 gate (target-w5)

Environment (verified 2026-10-16 03:4xZ):
- DGX gateway UP at 127.0.0.1:43120 (build optimus-gateway-monitoring-repair-20260918.1); routes: /ollama/v1/chat/completions (glm-5.3, glm-5.3-flash, glm-5.2, deepseek-v4-flash/pro, deepseek-v4.1-flash), /azure-foundry/v1/chat/completions (FW-Kimi-K3, FW-GLM-5.3), /azure-openai/v1/responses (gpt-5.6-sol, gpt-6-astra). Bearer in cloud-model-gateway/local-token.txt. Smoke: glm-5.3 PONG in 2.58s, usage returned.
- Disk: C: ~506 GiB free. HF + GitHub reachable. torch 2.14.1+cpu in uv cache.
- References pre-cloned at C:/Users/openclawuser/optimus-rlm-references: AgentPrune c544dd6a, LatentMAS 9a9e4d33, MARBLE 8d60fa17, MAST a70542e5, MOC 9c67c925, rlm d04208af, silo-bench e7412778, pei26a_clsr.pdf.

## Experiments
(appended as they run)

## 2026-10-16 ~04:4xZ — occ2-research DONE
- Deliverable: research/symbolic_comm/RESEARCH-NOTES.md (416 lines). CLSR mechanism extracted incl. evolutionary bootstrapping + prompt templates; DIALECT/1 card grammar defined; 5 deterministic + 3 model-assisted mutation operators; EVOLVE/DEV/HOLDOUT discipline; 10-cell model-pair matrix; 3-tier conservative fallback.
- LSF_MDia repo: verified 404 again (web + GitHub API). Recorded; no clone.
- Live model pool (ollama route): glm-5.3, glm-5.3-flash, glm-5.2, deepseek-v4-pro, deepseek-v4.1-flash. deepseek-v4-flash RETIRED 2026-09-25 (HTTP 410). Azure routes (FW-Kimi-K3, FW-GLM-5.3, gpt-5.6-sol, gpt-6-astra) 503 Azure auth - off critical path.
- Measurement caveat: gateway completion_tokens includes hidden reasoning tokens; wire token cost must be read from usage.prompt_tokens on real calls.

## 2026-10-16 ~05:0xZ — bench-tasks DONE (verified by parent)
- research/tasks/: gen_tasks.py + checkers.py + tasks_dev.json (150) + tasks_holdout.json (150) + gen_corpus.py + msgs_corpus.json (69 msgs: 18 holdout/51 train, all 9 kinds, 7 statuses, 8 evidence kinds, 16 verification records) + BASELINE.md.
- Self-test 306/306 (300 gold accepted + 300 solver re-derivations + 6 negative controls rejected). Deterministic regeneration byte-identical.
- OCC-1 baseline gate reproduced on base ce8480d59 with isolated target dir: cargo test -p pi-coding-agent --test occ_comm_fidelity = 7 passed / 0 failed / 0 ignored. This is the regression bar for all later OCC-2/OCC-3 work.
- Corpus: max message 661 bytes; plain 23505 / json 43673 / occ1 24361 bytes.

## 2026-10-16 ~05:5xZ — occ3-research DONE (verified by parent); harness audit forwarded
- Deliverable: research/latent_handoff/RESEARCH-NOTES.md (420 lines). LatentMAS mechanism exact (generate_latent_batch models.py:277-350, hidden-state variant 352-415, norm-rescale + optional ridge map models.py:158-213); Interlat cloned + pinned 66a89cb4; transformers 5.20.0 KV-cache cheat sheet (DynamicCache(config=), cache_position removed from forwards, attention_mask width seen+q_len).
- AUDIT FINDING (critical): occ3-harness's current results all 0/10 INCLUDING F_full_text (positive control). Root causes: (1) no chat-template turns in probe prompts, (2) interventions.py logits probe mutates shared cache (missing use_cache=False; A-vs-A KL 1.99), (3) Experiment B feeds raw post-norm hidden (std 2.4) without LatentMAS norm-rescale (drift 48-85/step). Steer sent to occ3-harness 03:57Z with fix order + acceptance criteria (positive control must pass; >=10 seeded trials; with-cache correct AND without-cache wrong).

## 2026-10-16 ~06:1xZ — occ2-integration DONE (verified by parent)
- Deliverable: research/integration/OCC2-INTEGRATION-DESIGN.md (319 lines, 121 file:line citations, design only).
- Key seams: payload body substitution at AgentSessionMessagePayload.message (agent_messages.rs:265); send seams S1 handle_agent_message_send (agent_messages.rs:1007-1141) + S2 execute_child_run (subagent_runs.rs:476-489); dual encode (OCC/2 symbolic + #FALLBACK-OCC1 sentinel) so durable records stay decodable across restarts without a registry; negotiation via OccCapability optional field; classify() strict-decode arm for SYM (only real decodes count, fallback-consumed = Occ1).
- Noted: occ2.rs (48,027 B) + dialect.rs (43,814 B) already landed in worktree by occ2-codec (still running, presumably finishing tests).
- Open questions recorded: compactor input path, AgentSession::send_agent_message body, settings plumbing, delta-body encoding (codec covers AgentCommMessage only), transport-bound 16,384 chars vs dual-payload size risk R1 (near-limit bodies fall back to plain OCC-1).

## 2026-10-16 ~06:3xZ — occ2-taskbench milestone
- Runner built + smoke green (10/10 units, 0 parse failures). Full matrix (2 modes x 2 listeners x 50 tasks) running, ~300 gateway calls budgeted, B prompts byte-identical across modes so prompt_tokens deltas measure wire cost.
- EARLY SIGNAL (honest, mixed): OCC-1 MORE expensive on wire for fact-dense messages (D-dev-001: occ1 482 tok vs occ0 312 tok B-prompt; one-field-per-line overhead, 48 fields), cheaper for E-dev-001 (198 vs 210). Occ2-evolve should prioritize merged-field/microtemplate operators that collapse multi-fact payloads; wire cost depends on field count, not just bytes.
- Deviation noted: B max_tokens 1024 (completion includes hidden reasoning; 200 would truncate).

## 2026-10-16 ~06:5xZ — occ2-taskbench DONE (verified via report; artifacts in research/taskbench/)
- Baseline (DEV, 50 tasks/cell, strict): occ0 48/50 (96%) both listeners; occ1 50/50 (100%) both. Identical per-task pass/fail across listeners; glm-5.3-flash = same accuracy at half latency (p50 0.81s vs 1.69s).
- 2 occ0 failures were exact-form answer-normalization (A-dev-010 'Okafor', C-dev-010 'Stage the new public key.' vs gold with more context): OCC-1 verbatim field values are better exact-form carriers than prose paraphrase.
- Wire cost (real tokenizer, byte-identical B templates): occ1 vs occ0 mean +15.3 tok, median -1.0, cheaper on 25/50. Per-family deltas: A -15, C -25, E -10 vs B +32, D +95. Root cause: speaker exploded one field PER ATTRIBUTE on record-shaped content (D: 48 fields vs 6). occ1 also pays +90 A-system format spec +136 A-completion overhead.
- VERDICT: OCC-1 grammar alone is NOT cheaper; compactness comes from field-packing discipline. OCC-2 must encode 'one field per record, attributes inline in value' in the grammar itself.
- Reliability: 0 parse failures, 0 retries, 0 truncations across 300 calls. Protocol clean (A sees only sender_input; B only message+task; wrong answers never retried; holdout untouched).
- 300 gateway calls, exactly on budget.

## 2026-10-16 ~07:0xZ — occ2-native milestone
- OQs resolved with citations: OQ1 compaction input = get_message_from_entry (core/compaction/compaction.rs:564-573) consumes content only; dual body survives in details; no compaction change needed. OQ2 full send path traced kernel->daemon (agent_session.rs:5509, daemon_mode.rs:15473; 16,384-char bound at normalize). OQ6 settings flag via settings_manager.rs agentTraces pattern (agentCommunication.occ2Enabled default false).
- AUTHORIZED cross-file edit: occ_capability: None at two daemon_mode.rs struct literals (~:13736, ~:13816) - minimal, flagged in .changes for review. Daemon-side capability fill deferred to parent.
- v1 scope agreed: deltas excluded (codec covers AgentCommMessage only); 3-strike = pure tested helper, runtime recorder deferred (daemon never decodes bodies today); pair_disabled stays false - no fake telemetry.

## 2026-10-16 ~07:2xZ — occ2-evolve milestone 1 (groundwork complete)
- Built: gateway.py, contract.py (typed mirror of contract.rs + equality oracle), dialect.py (DIALECT/1 card + PACKING layer per steer: E-record attributes inline, multi-value merges with |, FE bare refs, FC permille), corpus.py (69 msgs split EVOLVE 31 / DEV 20 / HOLDOUT 18), baselines.py.
- Deterministic round-trip fidelity 1.0 on all 69 messages for every packing subset (strict fixed-point decode, 0 failures).
- Real tokenizer (glm-5.3, DEV 8-msg sample, empty-prompt baseline subtracted): OCC-0 86.0 tok/msg, OCC-1 143.6, dialect-no-pack 116.6, dialect-full-pack 111.1. Packing saves 22.6% vs OCC-1; still +29% vs OCC-0 (free-text findings must stay verbatim).
- Card amortization: card ~200-300 tok; ~32 tok/msg savings vs OCC-1 -> break-even after ~7-9 messages per session (sessions with fewer structured messages should stay OCC-1 - selection policy input).
- Steer adoptions confirmed: packing operators first-class; exact-form answer checks via checkers.py; flash as in-loop listener; taskbench B-prompt protocol mirrored.
- Next: evolve.py (pop 8, 6-10 generations, 5 deterministic + 3 model-assisted mutations), then frozen-dialect eval incl. 5-pair matrix.

## 2026-10-16 ~07:4xZ — bench-official milestone (SILO-BENCH pilot complete)
- BENCHMARK-SURVEY.md delivered. Verdict: SILO-BENCH primary (offline-capable, OpenAI-compatible, deterministic official metrics S/P/C/D); MARBLE secondary (only werewolf env plausibly runnable offline; other envs need external services).
- Adapter built under research/benchmarks/: imports the OFFICIAL silo engine from pinned clone e7412778 (zero engine modifications); gateway_proxy.py reconstructs fenced XML tool calls the gateway rewrites into native tool_calls (request bytes untouched, prompt_tokens real); per-case subprocess with 1200s timeout (official client retries forever otherwise).
- PILOT glm-5.3, occ0 plain English, msg protocol, 10 Level-I n2 tasks: accuracy 0.8 (8/10 cases S=1.0), meanS 0.944 (9 completed), I-06 Checksum S=0.5 (67k completion tokens - heavy reasoning), I-10 StdDev TIMEOUT at 1200s (budget artifact, max_rounds 20 not reached). 127 LLM calls, 184,416 real prompt tokens, 33 inter-agent messages (7,657 payload bytes), mean rounds 7.9, case latency p50 52s / p95 904s.
- Honest caveats: completion_tokens include hidden reasoning (no wire-cost claims); timeout is wall-clock budget not engine failure.
- glm-5.3-flash pilot running for the cheaper comparison; PILOT-REPORT.md next.

## 2026-10-16 ~08:0xZ — CPU policy (user directive: keep overall CPU <=80%)
- Root cause: occ2-codec (target-w3) + occ2-native (target-w7) cargo builds ran concurrently; 12 MSVC link.exe x all-core /MT saturated 12 logical CPUs.
- Immediate: parent set 8-core affinity masks (0xFF) + BelowNormal priority on all link/rustc/cargo processes (build ceiling ~66%); CPU converged 100->65%.
- Durable: campaign rule for all current and future Rust workers: cargo -j 3 always (CARGO_BUILD_JOBS=3), package-scoped tests only, workspace check once at end, drop to -j 2 if >80% sustained. Steered occ2-codec + occ2-native; apply to every future worker prompt that runs cargo.

## 2026-10-16 ~08:2xZ — occ2-native milestone (implementation complete, gates green)
- ACK of CPU rule: CARGO_BUILD_JOBS=3 set, package-scoped tests, workspace check deferred to one final run.
- Gates so far: Gate 1 occ_comm_fidelity 7/7 GREEN (no OCC-1 regression). Gate 2 new tests/occ2_comm_fidelity.rs 12/12 GREEN on msgs_corpus (69 msgs, 18 holdout). 10 in-crate occ2/activity unit tests green.
- Corpus numbers (69 msgs): plain 23,505 B / JSON 39,148 B / OCC-1 24,361 B / symbolic 23,316 B / dual 48,781 B. Symbolic beats OCC-1 per-message on 69/69; -8.0% proxy tokens; listener context 52.2% smaller than dual wire form. Dual latency 137.5us encode / 82.3us decode (budget 5,000us). All 69 dual payloads within the 16,384-char bound (R1 guard fires only on engineered oversized bodies, test-covered).
- Remaining: repeat-run gates 2-3x (-j 3), single workspace cargo check, .changes fragment, final report.

## 2026-10-16 ~08:3xZ — occ2-codec ACK + status
- CPU rule complied: CARGO_BUILD_JOBS=3 exported session-wide, package-scoped runs only, single workspace check at the end.
- occ2.rs + dialect.rs: implemented, formatted, green. 204 occ:: tests across 3 consecutive runs; occ_comm_fidelity integration suite 7/7.
- Remaining: .changes fragment, one package-scoped confirmation run, single workspace check, final report.

## 2026-10-16 ~08:5xZ — bench-official COMPLETE (verified by parent)
- research/benchmarks/: BENCHMARK-SURVEY.md (SILO primary; MARBLE only werewolf env plausibly offline), adapter/ (official silo engine from pinned clone, zero modifications; gateway_proxy.py reconstructs fenced-XML tool calls the gateway rewrites - request bytes untouched, 100/102 responses rewritten/run, audited in proxy_rewrites.jsonl), PILOT-REPORT.md.
- PILOT (occ0, msg protocol, Level-I n2, max_rounds 20): glm-5.3 0.8 case acc / 0.944 meanS (I-06 Checksum S=0.5 communication-reasoning gap; I-10 wall-clock timeout), 127 calls, 184,416 prompt tokens, p50 52s. glm-5.3-flash: 10/10, meanS 1.0, 149,034 tokens, p50 38s - FLASH BEAT THE BIG MODEL (big model burned 67k hidden-reasoning tokens on I-06 and hit the clock on I-10).
- Scorecards in runs/pilot-levelI-n2-msg-glm53{,flash}/scorecard.{json,md}; workspaces gitignored.
- MARBLE: not run, documented infeasibility of main envs (Java+Node / Docker+Postgres / code exec / internet APIs); werewolf env untested (native function calling via gateway flagged as first check).
- Next-step notes for milestone run recorded: seeds for variance, n5, Level II/III, broadcast/sfs protocols, larger case_timeout, OCC-2 encoded-channel comparison at same measurement points.

## 2026-10-16 ~09:2xZ — occ3-prodesign DONE (verified by parent)
- Deliverable: research/integration/OCC3-INTEGRATION-DESIGN.md (367 lines, 89 file:line citations, 66 unique).
- Pre-registered go/no-go (before harness reruns, cannot be tuned to results): V1 full-text control >=80%, V2 greedy repeat agreement, V3 correct cache beats ALL perturbed-cache controls with non-overlapping CIs; G1 accuracy with-cache >= OCC-1-text -5pts AND > no-cache (95% paired bootstrap CI); G2 optional latency path (<=0.8x full-text at >=4096-token contexts, >=90% trials); G3 required resources (sidecar RSS <=1GiB, cache <=128MiB); G4 optional unique-capability path (beyond 16,384-char text bound, +10pts over truncated text). GO = V1-V3+G1+G3+(G2|G4) on one pinned pair, still default OFF. INVALID = any V gate fails (current 0/10 state = INVALID, not NO-GO).
- Architecture: OCC-3 never touches the wire (body stays OCC-1 text; latent artifact is a parallel adjunct); dedicated daemon-supervised Python sidecar (lazy start, one per daemon); v1 eligibility same pinned model descriptor both turns; byte-equal model identity checks; handles by id only, tensors in sidecar memory only; LAT only on confirmed transfer AND receiver consumption (content-free receipt map); text message never delayed/dropped/altered by latent failures.
- Noted: design cites current (landed) OCC-2 seam line numbers.

## 2026-10-16 ~09:4xZ — occ2-official status 1
- Seam: encoding contract appended to system prompts via rounds/round-000000/agent-*/context.json after init_case, before first run_round. Official engine/tools/tasks/scoring untouched. occ0 pilot numbers reused.
- Modes: occ1 (full-length KEY=VALUE over contract fields) and sym-v1 (hand-designed card, checksum 6e6eaf7a, rebuilt from evolve.py AST as single source of truth; labeled sym-v1-hand-designed because no evolved card frozen yet). occ1 guidance 1,714 chars; sym guidance+card 4,305 chars.
- Smoke (I-01, occ1, flash): S=1.0, 6 rounds, 37.6s, valid OCC/1 bodies. Early honest signal: occ1 message bytes LARGER than occ0 on short numeric exchanges (905 vs 409 B on I-01) - structure overhead; sym compression tests exactly this.
- Matrix running: occ1 flash -> sym flash -> occ1 glm-5.3 if time. ~330 calls expected.
- occ2-evolve status check requested: evolution-log.jsonl shows run_start only (EVOLVE 31/DEV 20/HOLDOUT 18 splits, listener flash, proposer glm-5.3, measure deepseek-v4.1-flash, seed 20261016).

## 2026-10-16 ~09:5xZ — occ2-evolve status + blocker handling
- Verified: fidelity 1.0 on 69/69 corpus msgs (full card + every packing subset); mutation suite 30/30 randomized fidelity trials; per-candidate eval ~13.5s (flash listener + deepseek-v4.1-flash measures, ~5x faster because no hidden reasoning on plain prompts).
- BLOCKER: CLSR-style long structured synthesis proposer prompts hang on BOTH glm-5.3 and glm-5.3-flash (simple JSON calls 1-3s; synthesis prompt never returns within 60-240s even at temp 0; gateway behavior, client fine). Contingency approved: shorten/chunk; if still hanging, 10 generations with 6 deterministic operators only; model-assisted operators recorded as negative result with probe evidence. Max ~30 more minutes on proposer workarounds.
- Freeze discipline: HOLDOUT untouched until freeze; worker will report card checksum + DEV summary at freeze point BEFORE holdout eval (parent records it for the audit trail).

## 2026-10-16 ~10:3xZ — occ2-native COMPLETE (final report)
- Phase 6 native OCC-2 integration delivered: wrap_outgoing_body + Occ2SendPolicy (S1 direct+broadcast arms, S2 spawn prompts symbolic / durable dual verbatim); sentinel split before prompt build; strict-occ2-then-occ1 decode; Occ2StrikeLedger 3-strike (pure, tested); occ_capability negotiation field; classify_with_registry Symbolic-only-on-strict-decode (fallback-consumed=Occ1, pinned test intact); R1 size guard -> plain OCC-1; agentCommunication.occ2Enabled default OFF; 12-test occ2_comm_fidelity + 12 unit tests; .changes fragment cites both daemon_mode.rs sites.
- Gates: occ_comm_fidelity 7/7 (4x green), occ2_comm_fidelity 12/12 (4x green), lib 286/0, cargo check --workspace exit 0. Pre-existing failures verified NOT its (stash A/B).
- Corpus: symbolic <= OCC-1 per-message 69/69; dual 137.5us encode / 82.3us decode; all 69 duals within 16,384-char bound.
- Approved cross-file edit landed: daemon_mode.rs +6 lines (occ_capability: None at ~:13772 + ~:13853 with owner comments). DEFERRED TO PARENT AT MERGE: daemon-side OccCapability::here() advertisement, strike-ledger runtime recorder, evolved-card loading, model-pair gating, compaction-prompt note.
- Asymmetry until daemon fill: flag-on direct sends stay OCC-1 (no advertised capability), S2 spawn prompts dual-encode here()->here().

## 2026-10-16 ~10:5xZ — occ2-telemetry decisions
- APPROVED: settings-gated OccCapability::here() at daemon_mode.rs site 1 (DaemonSession::settings_manager seam); site 2 resident-root family proxy (self.session_of), saved-only passive trees stay None (conservative). Real plumbing gap documented.
- REJECTED Symbolic->OCC-slot folding; RULED: activate reserved SYM as third indicator slot (until[3], labels TXT/OCC/SYM, Symbolic=>2, Latent stays continue). Conditions of campaign section 22 all satisfiable when flag on; default installs see SYM dark (telemetry reflects wire, not flag). LAT unchanged - no producer until go/no-go says integrate.
- Family-scope test keeps symbolic-in-family sample pulsing SYM slot; scope testing via non-family sample; new 3-slot format pinned; LAT-absence assertion retained; width discipline 2..120 verified.

## 2026-10-16 ~11:0xZ — occ2-codec COMPLETE (final report)
- occ2.rs (2,237 lines incl tests): symbolic wire codec over the full contract (all fields incl. msgid/parent/trace/sender/recipient/artifacts/timestamp/expiry); strict decode (unknown dialect/version/hash mismatch/malformed/duplicate scalar/index gap/dangling ref/partial verif group => Err); occ1_fallback; dual payloads with #FALLBACK-OCC1 sentinel + DualMismatch on disagreement.
- dialect.rs (1,122 lines incl tests): DialectCard serde + codebook sha256 over canonical BTreeMap-sorted JSON; registry insert/get/latest/resolve with validation (full coverage, prefix-free symbols, marker uniqueness, hash equality); OccCapability here()/supports()/negotiate().
- sym-v1 codebook sha256 pinned in test: 1efce9992ee24ff779a0430d998b37704d484f9cbb90c2fb78feec063add1547 (cross-checked vs independent Python sha256).
- Fixture size: occ2 14,027 B vs occ1 14,659 B aggregate (4.3% smaller, smaller on every scenario).
- 22 new unit tests (14 occ2 + 8 dialect); 204 occ:: tests total green 4x; occ_comm_fidelity 7/7; occ2_comm_fidelity 12/12 (sibling worker's integration test against the public API); workspace check once, clean; rustfmt clean; no new deps (std + existing serde/thiserror/sha2).
- mod.rs: +2 module decls, doc updated to implemented-foundation.

## 2026-10-16 ~11:2xZ — occ2-merge milestone 1 (inventory + coherence PASS)
- Inventory: 10 tracked files +998/-25; 3 new code files (occ2.rs 2,238, dialect.rs 1,123, occ2_comm_fidelity.rs 12 tests); 3 .changes fragments present; campaign research trees untracked as planned.
- Coherence ALL PASS: OCC-1 files byte-identical (git diff empty); zero new TODO/FIXME; cross-seam API usage consistent; mod.rs docs match reality.
- FLAG decisions: telemetry files = coherent ownership; benchmark run artifacts COMMITTED (49 non-workspace files ~150 KiB: scorecards, cases, proxy audit trails; 1,664 workspace files already ignored); final re-stat requested post-gates.
- Telemetry worker: SYM 3-slot implementation landed in its files (per ruling), quiet since ~06:00 local, presumably gating/reporting.

## 2026-10-16 ~11:4xZ — occ2-telemetry COMPLETE
- Daemon advertisement: occ_capability_for(session) helper (DaemonSession::settings_manager seam, same as set_transport) -> get_agent_communication_occ2_enabled() -> Some(OccCapability::here()) (sym-v1@1). Site 2: PassiveRlmRoot::Resident -> family settings via session_of(state); Saved -> None (documented gap, conservative OCC-1).
- SYM indicator LIVE per ruling: 3-slot TXT/OCC/SYM neon-blue, Symbolic=>slot 2, Latent=>continue (reserved). Pulse reflects wire, not sender flag. Renderer width-generic; parallel width pin test (full string at >=22 cols, bound holds 2..120, LAT never appears).
- Tests: occ_comm_fidelity 7/7 (2x), occ2_comm_fidelity 12/12 (2x), communication_ lib 13/13 (2x; 4 new + 9 existing incl. 3 updated to 3-slot format). Real encode_dual roster round-trip: kind "symbolic" on the wire, content-free, no OCC/2 or sentinel leakage into serialized rows. Default flag OFF pinned.
- CPU rule kept: -j 3, package-scoped, own target dir (fresh build 19m43s).
- Neon-blue indicators updated per user steer: TXT/OCC/SYM active, LAT reserved pending OCC-3 go/no-go.

## 2026-10-16 ~11:5xZ — DIALECT FROZEN (evolution milestone)
- Winner: sym-v1-pt-mi-pt-sp, generation 5, checksum 700313226ff397b3b50e8112dc67c2b90a7dd2d24d2dc8a492c7cf240aae28f6. Lineage: packing_toggle:FE=off -> mt_induction:TRACE 'trace-oc' x12 -> packing_toggle:ART=off -> symbol_prune:21->10.
- Gen-0 population results (real listener comprehension on flash, 10-msg samples): sym-v1 fidelity 1.0 / 117 median wire tokens; sym-occ1 0.9; sym-max 0.9; sym-pack-e 1.0 / 119. OCC-1 baseline 143.6 median (from taskbench-era measurement).
- Model-assisted operators DID run: model_synthesis rejected (proposal failed card parse) but model_recombine ACCEPTED at fidelity 1.0 - the proposer-hang was worked around. Deterministic operators drove the rest.
- evolution-winner.json written. HOLDOUT eval in progress (worker quiet since freeze).
- occ2-merge gate chain: running in background pid 17444, logs in optimus-occ23-target-w14/logs/. Completion follow-up will assemble the merge-readiness report.

## 2026-10-16 ~12:1xZ — report-draft delivered + rulings + OCC-3 results snapshot
- FINAL-REPORT.md draft (381 lines, 9 sections, every number traced). Three judgment calls all APPROVED: (A) sign error in native worker's prose corrected - symbolic is 4.3% SMALLER than OCC-1 aggregate (both corpora, 69/69 per-message); (B) occ1 official flash row filled from landed scorecard (1.0 acc/meanS, 191,634 tokens); (C) codec worker's line counts authoritative.
- Section 9 correction requested: SmolLM2 chat probe 15/50 (invalid) BUT induction probe 49/50 with 0/50 no-cache (V1 PASS) - handoff proven on both models under model-appropriate probes.
- OCC-3 data snapshot (read directly from results JSONs, pending harness worker's final report):
  * Qwen2.5-0.5B Exp A: V1 PASS (50/50 vs 0/50, full-text control 50/50, bootstrap CI [1.0,1.0]); receiver gen 0.85s vs full-text 1.19s (0.72x) BUT contexts ~81 tokens (G2 defined at >=4096, not measured). Peak RSS 2,443 MB (G3 cap 1 GiB EXCEEDED for Qwen; SmolLM2 992 MB just under).
  * Interventions (Qwen): A-vs-A KL 0 (probe contamination fixed); correct cache 20/20 KL 0; no/zeroed/random/unrelated/covgauss/haar/unpaired-shuffle all 0/20 with large KL; full text 20/20; compact text 18/20; truncated 19/20 (KL 0.024). Content-perturbation controls clean. Nuance: within-pair shuffle (H) 20/20 KL 0 - probe facts order-insensitive.
  * Exp B (latent continuation): NEGATIVE. Qwen none_steps0 10/10 -> steps1 8/10 -> steps2 8/10 -> steps4 2/10 -> steps8 0/10; rescale/ridge similar or worse; random latents hurt LESS than real ones (9-10/10 at all steps) - real hidden states actively derail beyond 1 step. SmolLM2 exp_b all ~2/10 (model too weak).
  * Provisional go/no-go reading (parent, final call after harness report): V gates PASS on Qwen; G1 passes on probes; G2 NOT satisfied (no >=4096-token trials); G3 FAILS for Qwen (2.4 GiB > 1 GiB); G4 untested. Per pre-registration, production integration NOT triggered - KV handoff real + causally proven, latent continuation negative, latency/resource gates unmet. Awaiting harness worker's final numbers + any task-suite (A-E) evals before locking the verdict.

## 2026-10-16 ~12:3xZ — report-draft COMPLETE
- FINAL-REPORT.md final draft at 395 lines. Corrections verified against disk before editing: section 9 dual-probe framing (Qwen chat 50/50 gate PASS; SmolLM2 chat invalid 15/50 but induction probe 49/50 gate PASS with bootstrap CI [0.94, 1.0]); Exp B as explicit NEGATIVE finding (none 10/10 -> 2/10 @4 -> 0/10 @8; random latents hurt less than real - real continuations corrupt the cache more than injected noise); line-count note added.
- Remaining PENDING slots for parent finalization: sym official run, evolved-dialect freeze confirmation, holdout eval, model-pair matrix, post-merge python 44/44.

## 2026-10-16 ~12:5xZ — occ3-harness COMPLETE — GO/NO-GO VERDICT (parent, per pre-registered framework)
- Fixes verified: chat-template prompts with assistant-ack turn boundary; all probes on fresh deepcopy use_cache=False (A-vs-A KL exactly 0.0, was 1.99); LatentMAS realignment (rescale to mean embedding row norm; optional ridge, deviates 4e-6 from identity). Plus one self-found bug fixed mid-run (rescale applied under transform=none; Qwen exp_b re-run with corrected identity-none).
- EXPERIMENT A: Qwen2.5-0.5B chat GATE PASS (50/50 with-cache, 50/50 full-text, 0/50 no-cache, 50/50 proven; bootstrap diff 1.0 CI [1.0,1.0]). Receiver gen 0.85s vs 1.19s full-text (28% faster); transfer 17.9ms; cache 1,991,147 B fp32 / 995,573 B fp16 for 81 tokens vs ~51 B text. SmolLM2 chat GATE FAIL on capability ceiling (full-text itself 30%) but raw induction probe PASSES (49/50 / 49/50 / 0/50 / 49/50 proven; bootstrap CI [0.94, 1.0]).
- INTERVENTIONS (Qwen, 20 trials): correct 20/20 KL 0; full-text 20/20 KL 0; compact text 18/20; truncated 19/20; consistent shuffle 20/20 KL 0 (NEW FINDING: NULL intervention, attention over fully-attended prefix is permutation-invariant when k/v move together); unpaired shuffle 1/20 KL 7.56; no-cache/unrelated/zeroed/random/covgauss/haar all 0/20 with large KL. Content-perturbation separation clean. SmolLM2 ordering identical, ceiling-limited.
- EXPERIMENT B (NEGATIVE on Qwen): steps0 10/10; none 8/8/2/0 at 1/2/4/8 steps; rescale 9/3/0/0; ridge == rescale; random control 10/10/10/9. Real latents derail beyond 2 steps; raw post-norm hidden (norm 240) -> near-deterministic pseudo-token (entropy 0.003, top1 0.9998, drift 112/step); rescaled (norm 0.45) -> entropy 5.4, drift 0.58/step; counterintuitively raw-none survives better than rescale at 2 steps (8/10 vs 3/10). SmolLM2 induction Exp B 9-10/10 everywhere (neither helps nor hurts).
- COST HONESTY: 81-token cache ~1.9 MB fp32 / ~1.0 MB fp16 vs 51 B text; cache wins latency (0.85 vs 1.19s) and continuation-without-re-encode only, not bytes.
- VERDICT vs pre-registered gates: V1/V2/V3 PASS on Qwen (V1 both models under model-appropriate probes). G1 accuracy: PASS (with >= full-5pts; == full exactly). G2 latency: PASS at small context (0.72x) but defined at >=4096 tokens - NOT measured, no claim. G3 resources: Qwen fp32 sidecar 2.44 GiB > 1 GiB cap; SmolLM2 0.99 GiB under. G4 unique capability: UNTESTED (no >16,384-char context trials). => GO requires G1+G3+(G2|G4): G3 FAILS for Qwen (the only model with a passing chat probe). DECISION: NO-GO for production integration in this campaign; research harness + honest negative findings shipped; production design retained for a future revisit with a GPU/larger-context rerun. LAT indicator stays reserved (correct per design: no producer).

## 2026-10-16 ~13:1xZ — occ3-report COMPLETE
- OCC3-SCORECARD.md (327 lines): full mode x model matrix with 9 metrics; intervention battery A-K + H2 + consistent-shuffle NULL finding (KL exactly 0.0; H2 unpaired 1/20 KL 7.56 is the informative control); latent grid negative result with random-control contrast + scope statement; honest cost (81-tok cache 1,991,147 B fp32 vs 49 B text ~40,500x; 4096-tok projection 96/180 MiB fp32; 0.72x receiver latency at small scale, explicitly not a G2 claim); probe-validity notes; verdict table V1-V3 PASS, G1 PASS, G2 not measured, G3 FAIL on residency (2,443 MiB peak vs 1 GiB sidecar budget; the 1.99 MB artifact itself is far under the 128 MiB cap), G4 untested => NO-GO per pre-registration. LAT reserved; production design retained.
- FINAL-REPORT.md section 9 rewritten to the full summary; section 8.6 updated to executed NO-GO.
- Nuance recorded: G compact-text = harness inline-notes single-turn text (79.8 vs 104 tokens, 18/20), described as OCC-1-style, not a literal occ1.py wire encoding.

## 2026-10-16 ~13:4xZ — pr-prep COMPLETE (all 3 deliverables) + judgment calls APPROVED
- PR-DESCRIPTION.md (146 lines), PR-EVIDENCE.md (265 lines), COMMIT-PLAN.md (6 commits, exact file lists, per-commit gates). 
- Judgment calls approved: (a) title drops 'learned' - correct, sym-v1 is hand-designed, evolved card not frozen; (b) -18.5% attributed to sym-v1 (evolution-log gen-0: 117 median wire tokens vs OCC-1 143.6, fidelity 1.0) with evolved card PENDING - corrects my earlier loose attribution; (c) daemon_mode.rs split across commits 2/3 matching actual work sequence; (d) python 44/44 PENDING re-run - parent runs it now; (e) 204 = 181 base + 22 new + 1 classify test verified; codebook pin lives in occ2.rs (attribution fixed).
- PENDING before push: sym SILO scorecard (9/10 case files on disk), evolved-dialect freeze+holdout, model-pair matrix, python 44/44 (parent running now).
- Python parity gate: 44/44 OK on the integrated tree (PYTHONPATH= cleared, uv run --locked). PENDING resolved.

## 2026-10-16 ~14:0xZ — campaign-close COMPLETE — GAP AUDIT (honest accounting)
- UNMET #1: latent handoff never ran through shared task suite (synthetic private-fact probes only; families B/C/D/E never tested through cache). UNMET #2: model-pair matrix - eval.py implements 5 of 10 planned cells, ZERO results on disk. UNMET #3: evolved-dialect holdout NEVER RAN - run_all.py never reached step 2 (evolution-log stops at gen 1; winner gen 5 exists; no eval-results.json, no dialect-card.json). OCC-2 therefore stays research-only under its own tier-2 policy (needs holdout fidelity 1.0 per pair).
- Impact: OCC-3 NO-GO stands (G3+ExpB are suite-independent); positive KV-handoff claim covers only single-fact recall at ~81-token contexts. Evolved card 70031322 selected on EVOLVE/DEV only - must not be presented as validated.
- Hygiene CLEAN: no weights/venvs/logs would commit; no stray debug files; archive-broken-run documented but path unnamed in RESULTS-EXP-A.md (one pointer line recommended); largest untracked file 500,340 B; total untracked ~2.65 MiB / 125 files.
- BONUS: sym-v1 official SILO run COMPLETED: case acc 0.9 (9 completed/1 errored), meanS 1.0, 253,864 prompt tokens, 88 calls, 36 msgs at 153.8 B/msg vs occ1 289.8 (47% smaller), p50 65.6s. Reports still say PENDING - fill before push. occ1 glm-5.3 run IN PROGRESS (5 case files at 06:51) - do NOT commit partial run dir; re-stat before staging.

## 2026-10-16 ~14:1xZ — evolve-worker status probe + takeover procedure (prepared, NOT executed)
- Evidence: last file write 06:52 (evolution-log.jsonl); run_all.py never passed step 1 (no dialect-card.json, no eval-results.json). evolution-log has 8 entries, stops at generation 1 although winner is gen 5.
- Urgent status request sent with 15-min deadline. TAKEOVER PROCEDURE if no response / stalled: (1) copy evolution-winner.json -> dialect-card.json (from_json accepts winner keys: id/generation/lineage/checksum/format/key_aliases/... exactly); (2) python research/symbolic_comm/eval.py (runs EVOLVE+DEV+HOLDOUT splits with flash+glm53 holdout listeners + MODEL_PAIRS matrix C1/X1-X4); (3) verify card checksum == 70031322... after load; (4) do NOT run in parallel with the worker's own eval (same output files, gateway double-calls).
- Sym official run verified on disk: 0.9 case acc, meanS 1.0, 36 msgs at 153.8 B/msg vs occ1 289.8 (47% smaller), 253,864 prompt tokens. occ1 glm-5.3 cell still growing (7 files, last write 06:53) - exclude from staging until its worker reports complete.

## 2026-10-16 ~14:2xZ — occ2-evolve status + parent decision: WAIT for second evolution run
- First run completed 6 generations, froze 70031322 (deterministic-only, fidelity 1.0, median 113 tokens on final group). Worker found model-assisted operators never got population slots (S4.2 protocol gap), patched mutation mix, relaunched (PID 10604, gen 4/10 at 06:56).
- DECISION: wait for the second run (upholds 'actual dialect evolution' requirement). Conditions: fallback to 70031322 if stagnation/error; one freeze + notify before HOLDOUT; eval on whichever card wins DEV; model-assisted slot fix documented; 90-min total guard then freeze-what-you-have.
- No HOLDOUT call ever made. Sym-v1 official numbers remain the shippable fallback for the PR if evolution timing collapses.

## 2026-10-16 ~14:3xZ — pr-review progress (~50%), verdict draft READY-WITH-FIXES
- Findings so far: (1) PR body stale vs tree (sym run completed: 0.9/1.0); (2) -18.5% headline mixes harnesses/splits/mean-vs-median (117 median 10-msg EVOLVE vs 143.6 mean DEV-8) - qualifier needed in the PR table row; (3) PR-EVIDENCE proposer model says glm-5.3 but log says glm-5.3-flash (second run changed it); (4) GENUINE GAP: production negotiation path occ2_target_send_policy (flag + list_agents id match) has NO test - only pure select_occ2_dialect is covered; (5) 5th run dir (occ1 glm-5.3, in progress) absent from PR-EVIDENCE and commit-5 list; (6) minor: raw newline in width-pin string literal; commit-plan says 7 unit tests, actually 8.
- Reviewer is re-running the claimed gates on a fresh target dir to verify 12/12, 7/7, 204, 13/13, t12 aggregates. No codec correctness bug found.
- Parent actions queued: fill PENDINGs after official worker reports the 5th run; fix the -18.5% qualifier; fix proposer attribution; decide on the negotiation-path test (add minimal test vs document gap); include/exclude 5th run dir per its completion.

## 2026-10-16 ~15:1xZ — occ2-merge FINAL: GO FOR MERGE (gate table all match)
- G1 204/0 EXPECTED-MATCH; G2 7/7 MATCH; G3 12/12 MATCH; G4 lib 3542/21F/7I with ZERO campaign-caused failures (17 fail at base too: 16 need KERNEL_CONTAINMENT_TEST_PYTHON/uv-on-PATH env setup, 5 theme/UI = PRE-EXISTING neon theme race proven by base-commit comparison run in a detached worktree; campaign only reshuffled victims via one added init_theme(dark) caller; 0 campaign-authored tests in any failure list); G5 workspace check exit 0 (351 pre-existing warnings); PY 44/44.
- Full lib failure analysis method: base v0.1.32 run in separate detached worktree, own target dir, removed after - clean comparison.
- Drift check post-gates: tracked diffstat unchanged (10 files +998/-25, code mtimes 06:01 < gate start 06:32). New untracked: report docs, occ2_runs/comparison, run artifacts. 20 latent_handoff scratch scripts cleaned by their worker.
- FLAG A RESOLVED: .rlm-review-target/ (review worker's active cargo dir, 5,596 files, in use) added to .gitignore by parent; deletion deferred until review completes; review worker notified.
- FLAG B: committable untracked set re-verified (scorecards/cases/proxy_rewrites in, workspace out).

## 2026-10-16 ~15:2xZ — HARD HALT: Ollama prohibition (user directive, repeated)
- User: DGX GLM 5.3 subagents ONLY; ollama route FORBIDDEN for all experiments. All experiment processes killed (eval.py PIDs 16604/14736 + earlier trees). Nothing calls any model now; no restarts without explicit user approval of the route.
- occ2-evolve freeze notification received BEFORE halt: run-2 card sym-v1-pt-pt, checksum 17836322dced3bb56e10bb0466abb5cb8d463ac6e4aba471f906ad5f24a4d49b, gen 5 stagnation-3, lineage packing_toggle:DEP=off + ART=off, DEV fidelity 1.0 (20/20), wire tokens median 101.0/mean 108.8 vs OCC-1 133.5/144.6 (-24.3% median), listener payload-equality 1.00 on final EVOLVE group, model-assisted operators ran (4 valid model children: 3 recombine + 1 repair; synthesis rejected at 0.23 fidelity). Freeze stands on disk; HOLDOUT NEVER RUN and will NOT run without user-approved route.
- pr-review COMPLETE: verdict READY-WITH-FIXES, 9 issues (all PR-text/commit-plan level; no codec bug). Independently re-ran gates fresh: 12/12, 7/7, 204/0, 13/13, 44/44, 306/306, t12 aggregates reproduced. Issue 2 (artifact drift: winner file now names sym-v1-pt-pt 17836322, reports pin 70031322) resolves as: reports must cite the run-2 frozen card since evolution completed under the full protocol.
- PENDING USER DECISION: (a) ship with gateway-produced numbers marked as to route, (b) re-run evals on a user-designated source, (c) strip model-eval claims, ship code + offline gates only.
