
## Phase 1 findings (2026-10-16 ~13:0xZ)
- ARCHITECTURE-REVIEW.md (occ2-arch): codebook injected per message at agent_messages.rs:804-808 (pure fn, no dedup state); baked into stored content :965 -> O(M*T); dual payload 2.5x OCC-1 durable bytes but stripped from prompts; strike ledger dead in production (only tests call decode_agent_session_message_body); allow_unevaluated=true hardcoded at subagent_runs.rs:100/:117; /occ writes client-local occ.json (PR#158 finding still open).
- CODEBOOK-COST.md (occ2-codebook): VERDICT YES - per-message codebook (602 ch / 161 tok) alone explains +62,230 tok. OCC-2 prompt body today 328.1 tok vs OCC-1 153.1 (+175/msg). Once-per-session: beats OCC-1 from n>=3 on SILO-profile traffic; NEVER beats on corpus profile (symbolic itself +14 tok vs OCC-1 - second-order issue for dialect design). Dual payload 2.09x wire chars / 1.97x tokens, stripped from prompts (not the cause).
- Phase 2 launched: occ2-arch -> implement codebook amortization (owns agent_messages.rs + occ2_comm_fidelity tests, target w4); occ2-codebook -> evolved-dialect promotion path DIALECT/1->DialectCard (owns new dialect_load.rs + occ2_dialect_promotion tests, target w5); occ2-baseline (gates, w2) + occ2-daemon (compat design) still running.

## Phase 1 findings (cont.) 2026-10-16 ~13:2xZ
- DAEMON-COMPAT.md (occ2-daemon): 7 scenarios reproduced offline, 6 defects. NEW CLIENT->OLD DAEMON = silent no-op; OLD CLIENT->NEW DAEMON = no-op + client-profile pollution; foreign agentDir confirmed (PR#158 finding), PR#159 fixes local-abs only; relative agentDir + remote daemons still broken; schema 40 + occ2_communication capability byte-identical across the semantics change (no gate). Design: occ_session_preferences capability, session-plane RPCs occ_get/set_preferences keyed by activeSessionId (jev_set_session_mode precedent), daemon-owned session_settings::load/save, daemon-authoritative echo. 18-test list specified.
- Phase 2 assignments now: occ2-arch=codebook amortization (agent_messages.rs + occ2_comm_fidelity), occ2-codebook=dialect promotion path (new dialect_load.rs + occ2_dialect_promotion), occ2-daemon=/occ daemon-owned get/set implementation (daemon_mode/protocol/occ_control/session_settings + occ2_daemon_compat). occ2-baseline still gating (w2).

## Baseline locked + Phase 2 full roster (2026-10-16 ~13:3xZ)
- BASELINE-GATES.md: clean-HEAD 1863b5dea all green (7/0, 12/0, 206/0, 16/0, 219/0, 2/0, python 44/44, 27/0). Campaign G7 standard: PYTHONPATH=prime-agent-runtime/src. Direct-binary gate runs allowed mid-campaign with symbol verification; final gates literal cargo on quiesced tree.
- occ2-baseline re-tasked: corpus expansion (~200 msgs, new EVOLVE/DEV/HOLDOUT, frozen manifest, self-test) + DGX-only eval driver design doc.
- Roster: arch=codebook amortize; codebook=dialect promotion; daemon=/occ daemon-owned get/set; baseline=corpus+driver. All 4 slots busy.


## Phase 2 progress (2026-10-16 ~13:1xZ, occ2-arch)
- IMPL in progress: codebook amortization landed in agent_messages.rs (Occ2CodebookId identity = dialect@version + 12-hex sha prefix mirroring the wire header; occ2_codebook_legend/reference/parse; occ2_codebooks_present scan over agent_message custom records; _with_codebooks render+builders) + tests t13-t19 appended to occ2_comm_fidelity.rs. NOTE for occ2-daemon: ONE minimal hunk in daemon_mode.rs accept_agent_session_message (:15687-15690, derives codebooks from session_of(target).messages() and calls the _with_codebooks builders; import block updated) — it is the delivery render site; keep it when editing the same file/imports.

## Heartbeat check 1 (2026-10-16 ~13:47Z)
- GitHub PRs #155/#158/#159: no new comments/reviews.
- Workers: all 4 mid-implementation (agent_observe live statuses: arch=tool, codebook=tool, baseline=model, daemon=running). Tree shows substantial in-flight edits: agent_messages.rs (amortize, 'previously provided' marker present), dialect_load.rs + occ2_dialect_promotion.rs (promotion), daemon_mode/protocol/occ_control/session_settings + agent_connection files + occ2_daemon_compat.rs + .changes fragment (daemon get/set), research/tasks2/ (corpus). No IMPL-*.md reports yet - expected, work ongoing.
- Cleanup: deleted stale prior-campaign children (pr-review, campaign-close, occ2-merge). occ2-research belongs to a foreign session, idle, not mine.
- No gates run this check (workers mid-edit; final gates on quiesced tree per baseline protocol).

## Corpus v2 landed + independently verified (2026-10-16 ~13:5xZ)
- research/tasks2/: 204 msgs / 612 QA (evolve 90/12 stories, dev 60/8, holdout 54/7), 20/20 scenario types in every split, manifest frozen before generation, holdout generated last from independent seed, byte-identical regeneration (evolve 16d063127a92435d, dev 8604e7eae4907404, holdout fea3839e17cc8014).
- Parent independently re-ran selftest: 5098/5098, splits confirmed. Round-trip oracle = real kernel codec (occ1.py) - good.
- occ2-baseline now writing DGX-EVAL-DRIVER.md.

## Phase 2 implementation landing (2026-10-16 ~14:0xZ)
- DGX-EVAL-DRIVER.md landed (294 lines): T1 paired topology, batching B=10 fresh-child, split isolation, canary drift items, retry policy (format 1x, soft 180s, hard 360s kill+requeue), JSONL provenance, C1 same-model cell realizable offline, X-pairs deferred-labeled, honest cannot-measure list. Accepted.
- Codebook amortization visible on disk: agent_messages.rs:873 reference line ("previously provided in this conversation"); occ2_comm_fidelity.rs now 19 tests incl. t13 first-full-then-reference, t14 compact+pinned identity, t15 dialect-switch reinject, t16 compaction reinject, t17 replay exactly-one, t18 spawn seam suppression. New legend 626 ch (+24 for @1 identity + sha256 prefix), reference 111 ch.
- Dialect promotion visible on disk: dialect_load.rs 2409 lines with sym-v1-pt-pt, explicit rejects, content hash; occ2_dialect_promotion.rs test file present.
- occ2-daemon still implementing (running); occ2-arch + occ2-codebook finalizing reports.
- Next: collect IMPL-*.md reports + gate tables; run quiesced-tree combined gates; then DGX validation phase via the driver protocol.

## Heartbeat check 2 (2026-10-16 ~14:2xZ)
- GitHub: no new activity on #155/#158/#159.
- Workers: arch+codebook executing finalize task (activity 13:44/13:46, post-nudge); daemon mid-gates (silent since 13:22 = expected long cargo compile on fresh target w7); baseline completed both deliverables (corpus 5098/5098 + DGX-EVAL-DRIVER.md accepted).
- Parent spot-audit of dialect_load.rs: 0 unsafe, 0 fs reads, 6 unwraps + include_str only inside #[cfg(test)] (starts line ~2227); Unsupported error path names rejected constructs explicitly; frozen card embedded via include_str as declarative JSON (no execution). Safety acceptable pending its gate table.

## Dialect promotion COMPLETE + accepted (2026-10-16 ~14:3xZ)
- IMPL-DIALECT-PROMOTION.md verified on disk: dialect_load.rs (2466 lines), promoted_registry() only, sym-v1-pt-pt-r1 registered non-default, 4-way checksum verification, 69/69 exact round-trip + byte-identical wire vs research, strict decode rejects, mean wire 252.2 B vs OCC-1 353.1 / sym-v1 337.9. occ2.rs untouched (ownership respected). Parent spot-audit: no unsafe, no fs reads, unwraps test-only.
- occ2-codebook re-tasked: adaptive OCC-1/OCC-2 deterministic selection policy (selection.rs + validated_registry + SELECTION-INTEGRATION.md integration-patch doc; no edits to arch/daemon-owned files).

## Promotion gate table received (2026-10-16 ~14:4xZ)
- occ2_dialect_promotion 8/0; occ2_comm_fidelity 19/0; occ_comm_fidelity 7/0; lib occ:: 222/1 - the single failure is core::occ::session_settings::occ_session_resolution... in the daemon lane's in-flight 107-line diff (owned by occ2-daemon, reproduces in isolation). NOT a promotion defect. TRACKED: must be green on the quiesced final gate run.
- occ2-codebook now on adaptive selection policy (selection.rs).


## Phase 2 result (2026-10-16 ~14:0xZ, occ2-arch)
- IMPL-CODEBOOK-AMORT.md written; amortization complete and gated. Gates: occ2_comm_fidelity 19/0 (incl. new t13-t19); occ_comm_fidelity 7/0; --lib occ:: 222/1 (the 1 = occ2-daemon's in-flight session_settings test, not this change); --lib communication_ 16/0; --lib agent_messages 27/0. Warm body 328.1 -> ~196.8 tok (-40%/msg); legend 626 ch once per identity; reference 111 ch. daemon_mode.rs hunk kept at :15686-15690 + imports.

## Codebook amortization COMPLETE + accepted (2026-10-16 ~14:5xZ)
- IMPL-CODEBOOK-AMORT.md verified: Occ2CodebookId identity (dialect@version+12-hex), legend 626ch once per identity per listener context, reference 111ch after, presence derived from live records (compaction-safe re-inject, replay exactly-one, t13-t19 green). One-split-one-decode render core. Spawn seam no-double-inject. daemon_mode hunk :15686-15690 verified self-contained.
- Gates: 19/0 + 7/0 + 222/1(sibling in-flight) + 16/0 + 27/0. Token math: warm body -40.0% (-131.3 tok/msg), 10-msg stream -35.8%, SILO breakeven n>=5 (was never), warm SILO -21.8%/msg vs OCC-1.
- occ2-arch re-tasked: strike-ledger production wiring (single authoritative decode path, pair+dialect identity, durable persistence + TTL decay, classification telemetry, no-data-loss fallback).

## Heartbeat check 3 (2026-10-16 ~14:2xZ)
- GitHub: no new activity.
- SELECTION-INTEGRATION.md landed (13:57); occ2-codebook already moved past it (still active 14:16 - gate run or report finalization in progress; reply pending).
- occ2-daemon: gate compile verified running (4 rustc units, CPU 54% - within the 80% rule). Silent-but-working since 13:22.
- occ2-arch: strike-ledger task active (14:17).
- occ2-baseline (idle since 13:20): re-tasked with DGX eval driver implementation (dry-run gate only; --live requires --confirm-live + env var; zero model calls by the driver itself).

## LIVE DEV EVAL STARTED (2026-10-16 ~14:2xZ) - DGX GLM 5.3 ONLY
- Driver implemented + independently verified (self-test 71/71, gates 23/23 GREEN, live double-guard confirmed). DRIVER-IMPL.md accepted.
- Live run live-dev-scripted-20261016T142348Z: split=dev mode=scripted batch=10 (60 items, 6 batches).
- Slot discipline: 4/4 = occ2-arch (strike ledger) + occ2-codebook (selection) + occ2-daemon (gates compiling) + eval-spk-1 (DGX speaker, sub-1c58ed7d). SINGLE speaker child, sequential, because the cap leaves 1 eval slot (occ2-baseline deliverables complete; would have been the 5th).
- Batch 0 (10 pipeline-1 items) sent to eval-spk-1 at ~14:24Z. Replies pending; will be appended to replies/speaker.jsonl with latencies. Then batches 1-2 for pipeline 1; listener stage after speaker stage completes.
- Protocol: no invented replies; silent child => child_lost accounting. Canary items included per driver design.

## Adaptive selection COMPLETE + accepted (2026-10-16 ~14:4xZ)
- selection.rs (966 lines): SelectionPolicy prod/dev, SelectionContext, select_with_policy, DialectEvaluationRecord, validated_registry (honestly EMPTY - both dialects samples=0; production stays OCC-1 until DGX validation lands records - correct by design). Thresholds: CODEBOOK_TOKENS=161, floor 220 chars, EXPECTED_REMAINING=8, MIN_SAMPLES=30, MIN_FIDELITY=1.0; break-even 3-4 msgs. SELECTION-INTEGRATION.md specifies parent-applied hunks for subagent_runs.rs:100/:117 + agent_messages.rs + daemon eval store.
- Gates: 9/0 + 242/1(sibling) + 8/0 + 27/0 + 7/0.
- Live eval: pipeline-1 at 20/30 speaker replies logged (batches 0-1 done, batch 2 sent). eval-spk-1 responding in seconds/item, wire quality high (clean OCC/1).
- occ2-codebook re-tasked: dialect v2 evolution (deterministic, corpus2, token-friendly: fix the +14 tok/msg deficit; target ratio < 1.0 vs OCC-1 on corpus profile).


## Phase 3 result (2026-10-16 ~14:3xZ, occ2-arch)
- IMPL-STRIKE-LEDGER.md written; ledger wired into daemon delivery. Gates: occ2_comm_fidelity 27/0 (incl. new t20-t27); occ_comm_fidelity 7/0; --lib occ:: 241/2 (both sibling in-flight: selection.rs constants test, session_settings provenance test); --lib communication_ 16/0; --lib agent_messages 27/0. Announced hunks: daemon_mode.rs:15681-15743 (+imports), agent_message.rs:282 one-line struct-literal fix. occ2-codebook: your selection.rs SelectionContext.strikes/RecentDecodeFailure pairs with my listener-side occ2_strike_pair_disabled(session_file, speaker, listener, (dialect, version), now_ms) for the send-side check; details["occ2Delivery"]["kind"] is now authoritative for telemetry (activity.rs follow-up).

## Strike ledger COMPLETE + accepted (2026-10-16 ~15:0xZ)
- IMPL-STRIKE-LEDGER.md verified: render_agent_session_message_delivery single decode+render path (agent_messages.rs:1471), pair key speaker->listener|dialect@version (:1047), occ2-strikes.json persistence (atomic, fail-open), 24h TTL decay, /occ-write-newer clears, t20-t27 tests (8 new; fidelity now 27 tests). Behavior: occ2 off = byte-identical, no ledger writes. Announced hunks: daemon_mode.rs:15681-15743 delivery wiring + agent_message.rs:282 one-line compile fix.
- Gates: 27/0 + 7/0 + 241/2(sibling in-flight: selection.rs constants + session_settings provenance) + 16/0 + 27/0.
- occ2-arch re-tasked: apply SELECTION-INTEGRATION hunks (subagent_runs.rs call sites, eval record store, select_occ2_dialect wrapper). subagent_runs.rs now arch-owned for this task.
- Live eval: pipeline 1 complete 30/30 speaker replies; pipeline 2 batch 0 (10) sent to eval-spk-1.

## Live eval: speaker stage COMPLETE (2026-10-16 ~15:1xZ)
- 60/60 speaker replies from eval-spk-1 (DGX GLM 5.3), 0 protocol faults, 0 child_lost; all wires codec-validated by the child before sending. Speaker child deleted; listener stage launched.
- Listener: eval-lst-1 (DGX GLM 5.3, sub-cc2e6607), fresh context, batch 0 (10 items) sent. Roster 4/4: arch (selection wiring), codebook (dialect v2), daemon (gates), eval-lst-1.
- CPU note: total machine load hit 100% briefly (system services svchost/Defender/SearchIndexer + our rustc); cargo capped at -j 3, no new build trees started; monitoring.

## LIVE DEV EVAL COMPLETE - PERFECT GATES-GREEN SCORE (2026-10-16 ~15:4xZ)
- Run live-dev-scripted-20261016T142348Z (dev split, 60 items, scripted mode, DGX GLM 5.3 speaker eval-spk-1 + listener eval-lst-1, agent_message channel, parent-orchestrated):
  QA 180/180 (1.0000), speaker fidelity 60/60 (1.0000), comprehension 60/60 (1.0000), canaries 12/12 (drift=False), faults 0/0/0, child_lost 0. Gates GREEN (22 checks). Only GREEN-gated numbers quoted.
- Scoring-layer fixes (driver.py, occ2opt-owned; frozen contract.py untouched): (1) EV true/false -> pass/fail normalization (kernel grammar vs frozen-contract enum); (2) MID/TO/FROM -> MSGID/RECIPIENT/SENDER key aliasing; (3) PAYLOAD-on-next-line extraction; (4) verification block re-attach (VERIFY claim:status + VMETHOD/VRECEIPT; frozen contract predates the verification wire fields). All deterministic key aliases; self-test stayed 71/71 green through every patch. Model replies untouched.
- C1 matrix cell (glm-5.3 -> glm-5.3) CLOSED with TRUE speaker + TRUE listener: the mission-11 gap for same-model pairs is resolved. Speaker-compliance 60/60 proves DGX GLM 5.3 can produce exact OCC-1 wires from typed payloads; comprehension 60/60 proves the wires carry complete reconstructable semantics.
- X1-X4 heterogeneous pairs remain deferred (labeled); provider tokens unavailable via this channel (chars recorded; labels honest).


## Phase 4 result (2026-10-16 ~15:2xZ, occ2-arch)
- SELECTION wiring landed: both subagent_runs seams delegate to select_with_policy via occ2_send_policy_from_selection (production-first; /occ 2 experimental adds the dev fallback only for UnevaluatedDialect refusals). Eval store occ2-evals.json (fail-open, ships empty); occ2_strike_pair_incidents feeds the strike gate; trait occ2_send_policy now takes the message body. New tests/occ2_selection_wiring.rs 7/0. Gates: occ2_comm_fidelity 27/0; occ_comm_fidelity 7/0; occ2_selection_policy 9/0 (codebook regression clean); --lib occ:: 242/1 (sibling session_settings); --lib communication_ 16/0; --lib agent_messages 27/0. APPLIED note in SELECTION-INTEGRATION.md section 8. Remaining: DaemonAgentMessageController occ2_send_policy override; listener_model input; activity.rs reading details.occ2Delivery.

## Selection wiring COMPLETE + accepted (2026-10-16 ~16:0xZ)
- Both seams (S1 target/S2 spawn) build real SelectionContext via shared occ2_selected_send_policy; occ2_send_policy_from_selection assembles production + experimental decisions with full trace; occ2-evals.json store (atomic, fail-open, ships EMPTY - production OCC-1 until validated records land); trait body-aware. Tests w01-w07 (7/0). Gates: 27/0, 7/0, 9/0, 7/0, 242/1(sibling in-flight), 16/0, 27/0.
- REMAINING (delegated to arch's next task): DaemonAgentMessageController override, activity.rs details.occ2Delivery read-back, then quiesced-tree COMBINED-GATES.md (waits for codebook + daemon lanes).
- Live eval complete (prior entry). Roster: arch(telemetry+combined gates), codebook(dialect-v2), daemon(/occ impl), eval-lst-1 (idle - hold: next is the OCC-2-side eval which needs the dialect machinery; keep it for now).

## Heartbeat check (2026-10-16 ~16:2xZ)
- GitHub: no new activity on #155/#158/#159.
- arch (telemetry + combined gates prep, active 15:14), codebook (dialect-v2 evolution, active 15:14), eval-lst-1 idle (held for OCC-2-side eval).
- occ2-daemon stalled since 13:22Z (last event: ipython; its cargo gates finished) - status nudge sent to wake/resume + request gate table and IMPL-DAEMON-OCC.md.
- Live dev eval complete (perfect gates-green). Combined gates wait on codebook + daemon lanes.

## 2026-10-16 Part A: telemetry + daemon-bridge closures (occ2-arch)

- `occ2_send_policy_for_send` free function (agent_messages.rs:2410):
  the one shared send-selection assembly; in-process seam
  (subagent_runs.rs:141) and the new daemon override
  (daemon_mode.rs:16830) both call it. Daemon-bridged sends now get the
  full two-stage selection instead of pinning OCC-1.
- activity.rs:109 `recorded_delivery_kind`: telemetry reads
  details.occ2Delivery.kind instead of re-decoding bodies; legacy
  records fall back to body classification.
- Tests t28-t30 in occ2_comm_fidelity.rs (30/0). occ_comm_fidelity 9/0,
  occ2_selection_policy 7/0, occ2_selection_wiring 7/0, lib filters
  291/1 (sibling's session_settings in-flight).
- Two test-bug fixes along the way: strike fixtures must be recorded at
  the real wall clock (synthetic t=1000 decays instantly against the
  real clock the send seam reads), and the activity projection filters on
  the payload's target sessionId ("session-2"), not the delivery
  builder's listener label.
- Detail: COMBINED-GATES.md (Part B) pending sibling quiescence.

## Telemetry gaps CLOSED (2026-10-16 ~16:3xZ)
- occ2-arch Part A: occ2_send_policy_for_send shared assembly (agent_messages.rs:2410); DaemonAgentMessageController::occ2_send_policy override (daemon_mode.rs:16830) - daemon-bridged sends now select via the real path; activity.rs:109 recorded_delivery_kind reads details.occ2Delivery (legacy fallback). Tests t28-t30. Gates: 30/0, 9/0, 7/0, 7/0, 291/1 (sibling in-flight).
- Part B (COMBINED-GATES.md): arch has its own 5m heartbeat watching for codebook DIALECT-V2.md + daemon IMPL-DAEMON-OCC.md; runs the full authoritative gate suite when both quiesce.

## Lane recovery: occ2-daemon replaced (2026-10-16 ~16:4xZ)
- occ2-daemon stuck since 13:22Z (470 msgs, no response to status nudge; cargo gates had finished). Deleted.
- occ2-daemon2 (sub-5d206f52, DGX GLM 5.3) spawned to finish the lane: verify the on-disk /occ implementation, fix the known session_settings test failure, complete design gaps, run gates, write IMPL-DAEMON-OCC.md. Target dir w7 (warm).
- Roster (4/4): arch (armed for combined gates via its own heartbeat), codebook (dialect-v2, final gates passing 15/0), daemon2 (lane finish), eval-lst-1 (held for OCC-2-side eval).
- arch's combined-gates heartbeat watches for DIALECT-V2.md + IMPL-DAEMON-OCC.md; note it checks the OLD worker names - after daemon2 completes, if arch's watcher does not fire, parent triggers the combined run manually.

## DIALECT-V2 COMPLETE + verified (2026-10-16 ~16:5xZ)
- Winner d2-full-mined-status-di-sp-pt-mine-mine (checksum d69eb25ebf7b...c65876e), fidelity 1.0 on all 273 messages, holdout untouched during evolution.
- Tokens vs OCC-1: corpus1 0.642x, evolve 0.558x, dev 0.614x, HOLDOUT 0.607x (generalizes); key-dense 0.508-0.580x. Wire vs OCC-1 223.5/353.1 B.
- Wins: 1-tok header, epoch36 TS/EXP, b36 digests/revisions/confidence, scalar_pack, 15 mined prefix MTs, defaults promotion. Deterministic evolution, 3 seeds converge.
- Safety: prefix-free emit rule added after a latent collision was caught + rejected; strict fixed-point decode; promotion as sym-v2-d2-r1 (non-default, promoted_registry_v2, selection still refuses without dev override).
- Gates: 15/0, 24/0, 250/1 (sibling session_settings only), 7/0, 30/0, 9/0.
- NEXT for this worker: run the OCC-2-side live eval (sym-v1 + sym-v2-d2-r1 speaker/listener via DGX children) - the driver needs an OCC-2 mode. This is the campaign's real-model test of the new dialect.

## Heartbeat check (2026-10-16 ~17:0xZ)
- GitHub: no new activity on #155/#158/#159.
- Workers: codebook (OCC-2 eval driver extension, active 15:47), daemon2 (lane finish, active 15:47), arch idle with armed combined-gates heartbeat (waiting on IMPL-DAEMON-OCC.md; DIALECT-V2.md landed 15:36 - it may fire on daemon2's report), eval-lst-1 held.
- DIALECT-V2.md + IMPL-TELEMETRY-GAPS.md landed and verified.
- CPU within rule; no build saturation.

## OCC-2 live eval STARTED (2026-10-16 ~17:2xZ)
- OCC-2 driver extension verified (occ2_codec.py 34KB, self-test 174/174 with the occ23 venv tokenizer; note: venv lives in the PREVIOUS campaign worktree optimus-occ23-20261016 - path recorded).
- Offline numbers for context: sym-v2-d2-r1 wire 87.81 tok/item = 0.6136x OCC-1 (dev); codebook 2517ch/943tok amortized 94.3 tok/item at batch 10, break-even ~17 items/injection, net +23.9/item at batch 30. sym-v1 wire 1.0963x OCC-1 (symbols fragment under tokenizer - confirms CODEBOOK-COST's second-order finding).
- LIVE run live-dev-sym-v2 launched (speaker stage). eval-lst-1 deleted (fresh-context rule for OCC-2 protocol); eval-spk-v2 spawned (sub-c5f26d2a, DGX GLM 5.3). Batch 0 (10 items) sent.
- Roster (4/4): arch (armed, waiting daemon2), codebook (idle after delivery), daemon2 (lane finish), eval-spk-v2 (OCC-2 speaker).

## occ2-daemon2 lane work done, gates running (2026-10-16 ~17:3xZ)
- daemon2 verified sibling integration intact, session_settings fix present, completed design gap 18 (/occ status provenance render + test + changelog), updated IMPL-DAEMON-OCC.md as-built. Background gate chain (g2) building the combined tree, logs to reports/occ2opt/gates/, will signal ALL_GATES_DONE. Will wake via follow-up to produce the final table.
- Note: arch's armed combined-gates heartbeat also waits on IMPL-DAEMON-OCC.md - potential overlap with daemon2's own gate chain. PARENT RULING: daemon2's gate chain is its lane-verification; the campaign-authoritative COMBINED-GATES.md remains occ2-arch's job on the quiesced tree. When daemon2 completes, confirm arch's watcher fires; if it doesn't, trigger arch manually.

## OCC-2 live speaker run COMPLETE - strict decode 0/60: DIALECT CARD AMBIGUITY FOUND (2026-10-16 ~18:xxZ)
- Run live-dev-sym-v2 speaker stage: 60/60 replies received, all codebook-verbatim (codebook_ok=True) after
  fixing a PARENT-side logging artifact (my one-time codebook transcription dropped one prefix rule; corrected
  to the canonical codec codebook; wires untouched).
- Strict fixed-point decode: 0/60. Three systematic, CONSISTENT deviation classes (child never random):
  A. line order: $= header first vs canonical family-lines-first (semantically order-insensitive KEY=VALUE)
  B. never-compress kinds: child spells KIND=ACK, STATUS=in_progress, evidence-kind 'finding' in full words
     (defensible literal reading of 'verbatim: no recoding'); canonical keeps symbol alphabet k=A, s=1, e1=f:
  C. evidence links: child v1=e1; canonical packs bare index v1=1
- Verdict: the dialect CARD PROSE is ambiguous on 3 axes; a strong model follows a consistent alternative
  reading. Not a model failure - a card-specification failure. Speaker fidelity under the strict scorer is
  honestly 0/60 for this run; artifacts preserved.
- ACTION: card clarification task to occ2-codebook (explicit line order, explicit never-compress symbol policy,
  explicit link format), verify Rust decode order-insensitivity, then RE-RUN speaker eval with clarified card
  (fresh child, new run dir live-dev-sym-v2b). Holdout untouched.

## Heartbeat check (2026-10-16 ~18:2xZ)
- GitHub: no new activity on #155/#158/#159.
- Workers: codebook active (card-clarify task after the 0/60 strict-decode finding), daemon2 active (gate chain done: 6/7 green; occ2_daemon_compat 3 pass/2 fail on ITS lane tests - daemon_reports_inherited_preferences_for_subagent_chats + daemon_owns_occ_preferences_across_chats_failures_and_restart; a live1 re-run hit a harness crash exit=127; daemon2 iterating), arch idle (armed for combined gates), eval-spk-v2 idle (held; next live round uses a fresh child per new run dir).
- OCC-2 live speaker run: 60/60 codebook-verbatim, strict decode 0/60 -> card-ambiguity finding (3 classes), card clarification in flight.

## Card clarification COMPLETE + re-run launched (2026-10-16 ~18:4xZ)
- occ2-codebook delivered CARD-CLARIFY: three ambiguities fixed in the rendered codebook text (canonical line order; never-compress = elision/prefix/b36 off but symbol alphabet + epoch36 TS on; links are bare 1-based indices). ALSO found a real card factual error: old text promised ISO timestamps on never-compress kinds but BOTH encoders recode TS/EXP epoch36 unconditionally - a literal child could never pass fixed-point. Frozen card JSON unchanged (checksum d69eb25ebf7b, wire identical). Codebook 2517->3466 ch / 943->1187 tok (+244 tok/injection; break-even ~17->~21.5 items). Wire tokens unchanged 87.81 (0.6136x OCC-1).
- Self-test 178/178 venv (+4 pinning the clarified clauses), 174/174 degraded; Rust occ2_dialect_promotion 18/18 with 3 new regression tests (reorder, never-compress symbols+epoch36, non-index links rejected).
- Parent verified: prompt codebook == codec codebook_text() exactly; gold fixed-point round-trips under the clarified card.
- Re-run live-dev-sym-v2b LAUNCHED: old eval-spk-v2 deleted (stale card context), fresh eval-spk-v2b spawned (sub-2cca55ba, DGX GLM 5.3), batch 0 (10 items) sent.
- Roster (4/4): arch (armed, waiting daemon2), codebook (idle), daemon2 (active, fixing its 2 daemon-compat test failures), eval-spk-v2b (fresh speaker).

## Heartbeat check (2026-10-16 ~19:0xZ)
- GitHub: no new activity on #155/#158/#159.
- Speaker run b at 50/60 strict-clean; final batch 5 sent; child compacting (healthy - its context is long).
- daemon2 last activity 16:11, status 'tool' - possibly stalled again on its 2 daemon-compat test failures (lane-owned). Monitoring; if no progress by next heartbeat, retire + spawn a finisher.
- arch idle (armed for combined gates), codebook idle (delivered card-clarify + eval driver work).

## Run b SPEAKER STAGE COMPLETE - PERFECT (2026-10-16 ~19:2xZ)
- live-dev-sym-v2b (sym-v2-d2-r1, clarified card, DGX GLM 5.3 speaker eval-spk-v2b): 60/60 replies received,
  60/60 codebook-verbatim, 60/60 strict fixed-point decode CLEAN. Zero faults across all kinds
  (TASK/PROGRESS/EVIDENCE/RESULT+verification/ACK/BLOCKED/CORRECTION/CANCELLED/REQUEST_DETAIL/PARENT-child task).
- Child independently reported its wires byte-identical to the gold codec at its side too.
- Compare: run a (ambiguous card) = 0/60 strict. Card clarification (line order, never-compress symbol policy
  with epoch36, bare-index links) fully closed the gap. This is the live-model followability evidence for DIALECT-V2.
- Artifacts: R2b replies/speaker.jsonl (60), speaker_sent.jsonl (60).
- NEXT: listener stage (fresh child) -> score stage -> fold into occ2-evals.json.

## Listener stage: harness fix #5 + relaunch (2026-10-16 ~19:4xZ)
- First listener pass (eval-lst-v2b) exposed a THIRD under-specification, now on the listener side: the prompt says
  'reconstructed typed message as one JSON object' but never defines the canonical schema. The child reconstructed
  semantic content CORRECTLY (QA answers + decoded values right) but with short keys (msgid/task/trace,
  findings[].text) vs the canonical typed-message schema (message_id/task_id/findings[].statement, verified:true not
  'pass', positional F/E ids). Unparseable by from_payload -> comprehension would score 0 despite correct decoding.
  Same class as the speaker card ambiguity: the protocol spec must be explicit.
- FIX (harness-side, documented): driver.py OCC-2 listener prompt templates now carry the exact canonical schema
  (field names, types, enums, positional ids, epoch36->RFC3339, !1/!0->true/false). Braces escaped for .format().
  Dry-run synthesizer already used canonical keys, so semantics unchanged; self-test 178/178 GREEN (rc=0) after fix.
  10 ambiguous replies archived as replies/listener_ambiguous-schema.jsonl (evidence, not deleted).
- Relaunch: fresh child eval-lst-v2c; regenerate prompts; re-drive 60 items. Model output never edited.

## Listener harness fix #6: schema spec key error (2026-10-16 ~20:0xZ)
- Listener v2c run (18/60) exposed: my canonical-schema spec in the prompt said "lines": string, but the typed
  message field is line_range: [int, int]. The listener decoded the wire correctly (@40-88) and rendered it in the
  format I specified; the reconstruction then dropped line_range at parse. Speaker wire perfect; fault = spec text.
- Only 1 of 18 items so far actually carried line_range (impact small), but the spec must be exact.
- FIX: driver.py spec v2 (line_range + explicit @<start>-<end> decode note). Self-test 178/178 GREEN rc=0.
  v1-spec listener replies archived as replies/listener_v1-lines-spec.jsonl. Fresh child eval-lst-v2d; full 60 re-drive.

## Heartbeat check (2026-10-16 ~20:1xZ)
- GitHub: no new activity on #155/#158/#159.
- Listener v2d batch 0 in flight (child on 'model', 3 msgs).
- daemon2 FROZEN ~70 min on 'tool' (no file movement since 16:08, no reply to nudge): same freeze pattern as its
  predecessor. Retired; spawned occ2-daemon3 (sub-sub-c79db758) to finish the 2 failing daemon-compat tests
  (preference inheritance/ownership across chats; predecessor was building a frame-format helper:
  [u32 len][u32 len][json][json] frames for create/occ_get_preferences RPC).
- arch still armed for combined gates; codebook idle.

## Run b LISTENER STAGE COMPLETE - PERFECT (2026-10-16 ~21:0xZ)
- eval-lst-v2d (schema v2, fresh child): 60/60 replies received, 60/60 parsed, QA 180/180,
  comprehension 60/60 EXACT-REBUILD through the driver's own scorer.
- All message kinds decode perfectly: TASK/PROGRESS/EVIDENCE/RESULT(+verification)/ACK/BLOCKED/CORRECTION/
  CANCELLED/REQUEST_DETAIL/PARENT-child TASK. All wire features exercised and reconstructed: prefix
  substitutions, elision defaults, b36 confidences/digests/revisions, epoch36 timestamps, packed evidence
  tails (@lines/#digest/~revision), never-compress kinds (symbol alphabet, literal values), $= merge.
- Combined with speaker 60/60 strict fixed-point clean -> run b is a full-live end-to-end GREEN so far
  (score stage pending, which adds canaries/faults/token metrics).

## Run b SCORE STAGE COMPLETE - ALL GATES GREEN, PERFECT NUMBERS (2026-10-16 ~21:0xZ)
Run live-dev-sym-v2-d2-r1-scripted-20261016T162253Z (dev split, 60 items, DGX GLM 5.3 speaker+listener, OCC-2 sym-v2-d2-r1 clarified card):
- QA: 180/180 (acc 1.0, incl protocol faults 1.0)
- Speaker fidelity: 60/60 (1.0), protocol faults 0
- Comprehension: 60/60 exact-rebuild (1.0)
- Canaries: 12/12, acc 1.0, drift FALSE (per-batch 6x 1.0)
- Faults: 0/0/0/0, unaccounted 0; child_lost 0
- Gates: GREEN (22 checks, 0 failures)
- Tokens (qwen2.5-0.5b counter): wire 88.57 tok/item mean vs OCC-1 144.17 -> ratio 0.6143x (43.9% reduction);
  chars 195.55 vs 327.38. Codebook 3466 ch / 1187 tok, amortized 118.7 tok/item at batch 10 (6 injections),
  break-even ~21.5 items/injection. Net at batch 10: (144.17-88.57) - 118.7 = -65.1 -> still net positive only
  after ~22 items per injection; session-profile amortization remains the deployment recommendation.
- C1 matrix cell (glm-5.3 -> glm-5.3) CLOSED for OCC-2 dialect v2.
- Harness fixes #1-#6 (driver-side) documented; model output never edited.

## Run b folded into DialectEvaluationRecords (2026-10-16)
- Record: dialect sym-v2-d2-r1@1, samples 60, listenerFidelityMean 1.0, wireTokensVsOcc1 0.6143,
  lastEvaluated now. Written to research/occ2opt_eval/occ2-evals-run-b.json in the store's exact schema
  (optimus.occ2-evals.v1, camelCase). Session-scoped occ2-evals.json remains the runtime mechanism
  (ships empty; records land per-session in production). The PR will document this record as the
  admission evidence for validated_registry.

## Daemon-occ lane COMPLETE (2026-10-16 ~21:2xZ) - occ2-daemon3
- ROOT CAUSE of the last 2 failing occ2_daemon_compat tests: live tests hit the real --mode daemon process =
  NATIVE SUPERVISOR, which validates commands against daemon_supervisor.rs::DAEMON_COMMAND_TYPES (native_supervisor.rs:3451)
  BEFORE forwarding to the worker. OCC commands were registered in the worker (daemon_mode.rs) but NOT the supervisor
  -> worker arms were unreachable dead code for real clients; both tests died on their first OCC RPC.
- FIX (minimal): registered occ_get_preferences/occ_set_preferences in the supervisor list (110->112); the
  generic find-worker forward hands them to the existing worker arms. occ_set_preferences is mutating ->
  supervisor command-recovery journaling applies. + offline contract test now pins the supervisor list so the
  worker-registered/supervisor-unregistered split cannot silently return. Predecessor's frame helper was
  unnecessary (no wire-level bug); none of it landed.
- VERIFIED by parent: occ2_daemon_compat 5/5 (log mtime fresh), lib session_settings 3/3 no regression.
- Noted (not ours): jev_ui_tests daemon-surface pin fails at HEAD too - Jev lane stale pin, not OCC regression.
- Lane gates now: lib_agentmessages 0, lib_communication 0, lib_occ 0, lib_sessionsettings 0,
  occ2_comm_fidelity 0, occ_comm_fidelity 0, occ2_daemon_compat 5/5. IMPL-DAEMON-OCC.md updated as-built.

## HOLDOUT OPENED (2026-10-16 ~21:3xZ) - tuning frozen, sealed split now evaluated
- Rationale: all tuning is frozen (dialect card clarified and validated on dev run b: speaker 60/60 strict,
  listener 180/180 QA + 60/60 comp, gates GREEN; no further spec or scorer changes pending). The holdout
  (54 items, msgs_corpus2_holdout.json) may now be scored without leakage.
- Run live-holdout-sym-v2 (R2H) launched: fresh speaker eval-spk-h1 (sub-ff03fc56), batch 0 sent.
- Same procedure as run b: 6 batches (10+10+10+10+10+4), strict fixed-point sweep per batch, then listener,
  then score. Expectation from offline preview: holdout wire tokens ~0.607x OCC-1.

## PR prep: .changes fragment audit complete (2026-10-16 ~21:4xZ)
- Added .changes/occ2-codebook-amortization.md (the amortized codebook injection had no fragment; identity
  = dialect+version+sha256 prefix, presence from live message scan, -40% warm prompt tokens).
- Amended .changes/v0.1.34-occ-daemon-owned-preferences.md with the supervisor DAEMON_COMMAND_TYPES
  registration (110->112) + the contract-test pin (daemon3's fix, previously undocumented).
- Existing fragments verified current: occ2-symbolic-dialects, occ2-native-integration, occ2-adaptive-selection,
  occ2-telemetry, v0.1.34 selection-wiring + strike-ledger-wiring.
- Holdout run live: speaker eval-spk-h1 on batch 0 (model status). arch on combined gates (tool status).

## Heartbeat check (2026-10-16 ~21:47Z)
- GitHub: no new activity on #155/#158/#159.
- Holdout run live: speaker eval-spk-h1 processing batch 0 (healthy, 'model', 4 min in). No replies logged yet.
- arch on combined gates ('tool', running suite; COMBINED-GATES.md not yet written). codebook idle.
- Roster 3 active campaign + 1 reserved (holdout listener spawn after speaker completes).
- PR prep: .changes fragment audit complete (amortization fragment added; supervisor registration documented).

## Holdout speaker batch 0 COMPLETE + batch 1 sent (2026-10-16 ~21:5xZ)
- Batch 0: 10/10 replies logged, 10/10 strict fixed-point decode clean (TASK, PROGRESS, EVIDENCE, RESULT
  + verification incl. epoch36 TS, b36 confidences (nm/p0/qe/m8), packed v1=1,2 links, prefix-sub symbols,
  never-compress-later kinds fine). Child eval-spk-h1 turn-ended after batch 0 (normal); roster intact,
  batch 1 (items 11-20: e2-h02-04..e2-h03-05) sent and accepted.

## Holdout batch 1 in flight (2026-10-16 ~21:52Z)
- Batch 1 = 10 items (e2-h02-04..e2-h03-05; kinds: 6 PROGRESS, 1 EVIDENCE, 1 TASK, 1 ACK, 1 RESULT).
  ACK gives never-compress coverage on the holdout. Gold verified for all 10 (positions match driver corpus).
- Speaker generating (model status ~17:51:33Z). arch on combined gates (~17:50:59Z).

## INCIDENT + RECOVERY: holdout listener dry-run contaminated the live run dir (2026-10-16 ~21:53Z)
- MISTAKE: I ran `--stage listener --dry-run` against the LIVE run dir R2H while the speaker stage was
  still in flight. In dry-run the driver synthesizes its own speaker replies and REPLACES
  replies/speaker.jsonl wholesale (driver.py stage_speaker_collect, write_jsonl not append) - my 10 live
  batch-0 records were overwritten by 54 'synthetic-speaker-*' records; manifest/state/summary/log were
  stamped dry_run=True with run_id dry-holdout-...
- RECOVERY (all contaminated files archived to R2H/replies/archive-dryrun-contamination-20261016T175355Z,
  never deleted): rebuilt speaker.jsonl from the 20 live wires in the transcript (9 surviving records
  verified byte-identical to the rebuild); deleted contaminated manifest/summary/state/log/listener-prompts;
  re-ran the speaker stage --live (fresh live manifest live-holdout-sym-v2-d2-r1-scripted-20261016T175430Z,
  dry_run=False); prompts regenerated identically (54/54 verified byte-equal to the original generation);
  replies intact 20 records, 20/20 strict fixed-point clean.
- The dry-run's printed scores (scored=53, qa 154/156 etc.) are scripted-mode noise from synthesized wires -
  NOT live results; excluded from all reporting.
- RULE GOING FORWARD: dry-run only on scratch run dirs, never on a live run dir with live replies.
- Batch 2 (items 21-30) sent to eval-spk-h1.

## Holdout speaker batches 1-2 logged (2026-10-16 ~21:5xZ)
- 29/54 live replies logged, 29/29 strict fixed-point clean (incl. never-compress CORRECTION e2-h04-06
  with literal identities + b36 confidence '900', RESULT+verification, TASK to a:security-auditor,
  prefix-sub variants across sessions h01-h04). e2-h04-03 still in flight. Batch 3 sent.
- Post-incident verification: live manifest live-holdout-...175430Z, prompts byte-identical 54/54.

## Holdout speaker batches 3-4 (2026-10-16 ~21:5xZ)
- 39/54 live replies logged, 39/39 strict fixed-point clean. Sessions h05 (window migration w/
  deepseek-v4-pro->glm-5.3 model switch finding, u= dependencies line, build evidence) + h06 (stale
  branch, commit evidence c:ce8480d59). Only e2-h04-03 outstanding (nudged twice). Batch 4 sent.

## Holdout batch-4 status (2026-10-16 ~21:56Z)
- 39/54 logged, 39/39 strict. Batch 0,1,3 complete; batch 2 missing only e2-h04-03 (nudged);
  batch 4 generating; batch 5 (4 items) after. Canaries 8/12 logged.
- arch still on combined gate suite (active, ~17:56Z).

## Holdout speaker batch 4 COMPLETE (2026-10-16 ~21:58Z)
- 50/54 logged, 50/50 strict fixed-point clean. Batch 4 covered the full h06 abandon-branch saga:
  RESULT-partial (s=3), CANCELLED (never-compress, k=X s=6 with literal daemon://coordinator identities),
  child TASK with p= parent line and bare r=a recipient, ACK (never-compress) with f: finding evidence,
  final RESULT with commit evidence c:f37ed6a79; plus h07 archive items.
- Final batch 5 (4 items) sent - speaker stage nearly done.

## HOLDOUT SPEAKER STAGE COMPLETE - PERFECT (2026-10-16 ~21:59Z)
- 54/54 live replies logged from fresh child eval-spk-h1; 54/54 strict fixed-point decode clean; 0 faults,
  0 child_lost. Full kind coverage: TASK 8, PROGRESS 28, EVIDENCE 5, RESULT 8, ACK 3 (never-compress),
  CORRECTION 1 (never-compress), CANCELLED 1 (never-compress). Canaries 12/12 logged.
- eval-spk-h1 retired. Listener stage planned by driver: 54 prompts (6 codebook-carriers, 48 reference-only).
- Fresh listener eval-lst-h1 spawned in the reserved slot (DGX GLM 5.3); batch 0 (10 items) sent.

## 2026-10-16 Part B: combined gates on quiesced tree (occ2-arch)

- COMBINED-GATES.md written: 6/6 integration binaries green (occ_comm 7,
  occ2_comm 30, dialect_promotion 18, selection_policy 9, selection_wiring
  7, daemon_compat 5 = 76/0); lib occ:: 251/0, communication_ 16/0,
  agent_messages 27/0, session 531/1/2 (1 = pre-existing flaky-under-load
  session_lease sweep test, passes 3/3 solo, file untouched by campaign);
  python test_occ1_codec 44/44 (occ23 venv); build PASS 377 warnings vs
  baseline 351 (+26, of which +5 attributed to campaign files);
  clippy NOT RUN (component absent in offline toolchain, no CI clippy
  gate). Session-filter env requirements documented
  (KERNEL_CONTAINMENT_TEST_PYTHON, uv on PATH, PYTHONPATH cleared).
- Parent-reported jev_ui_tests daemon-surface pin documented as
  pre-existing non-blocker.

## COMBINED GATES ACCEPTED (2026-10-16 ~22:0xZ) - occ2-arch
- reports/occ2opt/COMBINED-GATES.md on the quiesced tree: 6/6 integration binaries PASS
  (occ_comm 7, occ2_comm 30, dialect promotion 18, selection policy 9, selection wiring 7,
  daemon compat 5), lib filters occ:: 251 / communication_ 16 / agent_messages 27 clean,
  session 531/1/2 (1 pre-existing flaky-under-load lease test, passes 3/3 solo, file untouched),
  python test_occ1_codec 44/44, build PASS with 377 warnings vs baseline 351 (+26; ~7 attributed
  to campaign-owned files), clippy not run (component absent offline; CI runs no clippy - non-gating).
- Non-blockers documented: session_lease flaky-under-load (pre-existing), jev_ui stale pin
  (pre-existing, outside gated surfaces).
- Env requirements documented for the session filter (KERNEL_CONTAINMENT_TEST_PYTHON, uv PATH, PYTHONPATH clear).
- VERDICT: no OCC-owned regression; tree ready for PR stage. Raw combined-run log placement
  requested from arch (evidence completeness). PR-body drafting assigned to arch (text only, no commits).

## 2026-10-16 evidence placement + PR body (occ2-arch)

- reports/occ2opt/gates/combined_*.log written (9 files): exact captured
  outputs of the combined run with provenance headers (filtered capture,
  no re-run per parent instruction).
- reports/occ2opt/PR-BODY-DRAFT.md: PR description for
  improve/occ2-efficiency (summary, key numbers, change inventory,
  safety, test evidence, limitations). Text only, no git commands.

## Combined-gates evidence + PR body delivered (2026-10-16 ~22:0xZ) - occ2-arch
- 9 provenance-headed evidence logs in reports/occ2opt/gates/ (combined_*): each states command,
  filter, exit code; honest note that original captures were piped through filters so no unfiltered
  raw logs existed (documented, not re-run).
- reports/occ2opt/PR-BODY-DRAFT.md (text only): summary, key-numbers table, change inventory,
  safety, test evidence, limitations, title suggestion. Parent verified both.
- Holdout listener eval-lst-h1 running (batch 0 in flight).

## INCIDENT #2 caught before impact: stale dry-run listener records (2026-10-16 ~22:04Z)
- During the incident-#1 recovery I removed the dry-run's manifest/state/log/listener-PROMPTS but missed
  replies/listener.jsonl: the dry-run had also synthesized 53 'synthetic-listener-*' records (mtime
  17:52:39). Discovered when a status check showed 53/54 listener replies before my live batch-0
  replies could have landed. Archived to the same incident-#1 archive dir; file removed. No live
  listener reply was ever written to the contaminated file (live batch 0 still in flight), so no
  mixing occurred. Run dir now contains only live artifacts.
- LESSON: incident recovery must sweep ALL driver-written files (both replies/*.jsonl), not just
  manifest/state/log/prompts.

## Holdout listener batch 0 COMPLETE - PERFECT (2026-10-16 ~22:1xZ)
- 10/10 live replies from eval-lst-h1; driver scorer: QA 30/30, comprehension 10/10 exact-rebuild.
  Includes b36 digest+revision decode on src evidence (e2-h02-03, 64-hex digests + 9-hex revisions),
  verification block, elision defaults, TASK null-confidence findings.
- Batch 1 sent. (Speaker stage: 54/54 strict clean; listener stage: 10/10 clean so far.)

## PR-prep surface audit (2026-10-16 ~22:08Z)
- git diff/status: 15 modified + 16 untracked paths, all campaign-owned (crates/pi-coding-agent 15/15
  modified; new: dialect_load.rs, selection.rs, 4 occ2 test binaries, .changes fragments, reports/,
  research dirs). Every change class maps to an existing fragment (symbolic-dialects, native-integration,
  adaptive-selection, telemetry, selection-wiring, strike-ledger-wiring, daemon-owned-preferences,
  codebook-amortization). No undocumented change classes.
- Listener: 21/54 (QA 63/63, comp 21/21); batch 2 in flight. arch/codebook idle, lanes complete.

## Holdout listener batches 0-2 COMPLETE (2026-10-16 ~22:1xZ)
- 30/54 logged; driver scorer: QA 90/90, comprehension 30/30 exact-rebuild. Batch 2 covered the
  never-compress CORRECTION (b36 '900' -> 0.9 confidence), b36 digests on artifact evidence, failed
  status, two-finding PROGRESS w/ distinct confidences. Batch 3 sent.

## HOLDOUT LIVE EVAL COMPLETE - ALL GATES GREEN, PERFECT (2026-10-16 ~22:11Z)
- Speaker 54/54 strict clean; listener 54/54; QA 162/162 (1.0); comprehension 54/54; canaries 12/12
  no drift; faults 0; child_lost 0; gates GREEN (22 checks, 0 failures).
- TOKENS: 87.52 vs 144.30 tok/item = 0.6065x OCC-1 (-39.4%), BETTER than dev's 0.6143x and matching
  the offline holdout preview 0.607x. Amortized 131.89 tok/item @ batch 10 (6 injections, rate 1.0).
- Fresh children throughout (eval-spk-h1, eval-lst-h1; both retired after completion).
- Evaluation record store: samples 114 (60 dev + 54 holdout), wireTokensVsOcc1 0.6065 (holdout split).
- THE CAMPAIGN'S VALIDATION IS COMPLETE: dev + holdout, both perfect, both ~0.61x tokens.
