# Pre-Push Adversarial Review: OCC-2 + OCC-3 PR

Reviewer: pr-review worker (adversarial pass before push). Date: 2026-10-16.
Worktree: `C:\Users\openclawuser\optimus-occ23-20261016` (branch `improve/occ2-occ3-communication`, base `ce8480d59` = v0.1.32).
Scope: the seven campaign reports, the three `.changes` fragments, the full `git diff` (10 tracked files +998/-25, verified), the new `occ2.rs` / `dialect.rs` / `tests/occ2_comm_fidelity.rs` API surface, and independent re-runs of the claimed gates.

## Verdict: READY-WITH-FIXES

The shipped code is sound. I found no correctness bug in the codec, the send seams, the fallback chains, or the telemetry, and every offline gate re-ran green on a fresh isolated target dir. The blocking problems are in the PR text and the commit plan, not the code: the PR body is stale against the tree it ships, one headline number needs its qualifier in the table itself, one report mis-attributes the evolution proposer model, and the evolved-dialect artifacts moved again after the reports were written. Fix the text items below before creating the PR; none require code changes.

## Gate re-verification (independent, fresh target dir `.rlm-review-target/`, CARGO_BUILD_JOBS=3)

| Gate (claim) | Claimed | Re-run result |
|---|---|---|
| `cargo test -p pi-coding-agent --test occ2_comm_fidelity` | 12/12 | 12/12 passed (fresh build 8m17s) |
| `cargo test -p pi-coding-agent --test occ_comm_fidelity` | 7/7 | 7 passed |
| `cargo test -p pi-coding-agent --lib occ::` | 204 | 204 passed / 0 failed |
| `cargo test -p pi-coding-agent --lib communication_` | 13/13 | 13 passed (4 new + 9 existing confirmed by name) |
| `python -m unittest discover -s prime-agent-runtime/test -p "test_occ1_codec.py"` | 44/44 PENDING | 44/44 OK (the PENDING slot can be filled) |
| `python research/tasks/gen_tasks.py` self-test | 306/306 | 306/306; regeneration byte-identical (sha256 stable across two runs) |
| t12 aggregate (corpus bytes) | plain 23,505 / JSON 39,148 / OCC-1 24,361 / symbolic 23,316 / dual 48,781 B | reproduced exactly |
| t12 proxy tokens | symbolic -8.0% vs OCC-1 | reproduced (-8.0%; bytes +4.3% saving direction) |
| t11 latency | 137.5/82.3 us | 113.4/65.4 us on this machine (budget 5,000 us; see issue 8) |
| OCC-1 files byte-identical to base | yes | verified per-file with `git diff --quiet` (occ1.rs, contract.rs, delta.rs, codec_tests.rs, consolidation*.rs, prune*.rs, occ_comm_fidelity.rs, scenarios.json, consolidation.rs, prune.rs) |
| sym-v1 codebook sha256 pinned | `1efce999...` | pinned in occ2.rs test, suite green |
| diff stat | 10 files +998/-25 | reproduced (`git diff --stat`) |

Static checks that passed: default OFF verified in code (`settings_manager.rs` `nested_bool(...).unwrap_or(false)`; `occ2_send_policy` default returns `Occ2SendPolicy::disabled()`; `wrap_outgoing_body` is the identity when disabled, pinned by `occ2_disabled_policy_is_the_identity` and t05); size guard precedes the transport bound on all three paths (S1 direct and broadcast wrap before `controller.send_agent_message` after daemon-side `normalize_agent_session_message(16,384)`; S2 spawn wraps the prompt with the same `DEFAULT_AGENT_MESSAGE_MAX_CHARS` bound; guard returns the original body, never truncates, tested in `occ2_size_guard_sends_plain_occ1_instead_of_truncating` and t05); malformed OCC/2 cannot crash delivery (strict decode errors are values, `occ1_fallback` is tried next, and all decode paths are bounded by `OCC2_MAX_MESSAGE_BYTES`/`OCC2_MAX_SYMBOLIC_BYTES`; t04 fault-injection covers header/version/hash/symbol/truncation/sentinel-removal corruptions over all 69 corpus messages); broadcast never compound-wraps (each recipient wraps the original body; asserted in `occ2_host_send_seam_wraps_only_when_enabled`); fallback-consumed bodies report Occ1 (covered by `occ2_bodies_classify_by_the_consumed_path` with an empty registry AND a hash-mismatch registry, plus t09).

## Issues, ranked by severity

### 1. HIGH (PR text vs tree): the PR body is stale - the official sym-v1 SILO run completed on disk, but the PR says PENDING everywhere

`research/benchmarks/runs/occ2-levelI-n2-msg-glm53flash-sym/scorecard.json` now exists: case accuracy 0.9 (9/10 completed, I-10 timeout at the 1200 s budget), mean S 1.0, 253,864 real prompt tokens, 88 LLM calls, 36 inter-agent messages / 5,538 payload bytes (153.8 B/message vs occ1's 289.8). PR-DESCRIPTION lists this row as "PENDING", PR-EVIDENCE section 2.5 says "no scorecard at draft time" and its cross-check note 6 says the landing spot does not exist, and FINAL-REPORT section 8 calls it in flight. A reviewer who opens the artifact will see numbers the PR does not contain, and the "no end-to-end task-accuracy claim for OCC-2" non-claim is now too conservative for what the tree carries.

Fix: fill the row in PR-DESCRIPTION's benchmark table (`sym-v1 glm-5.3-flash 0.9 / 1.0`, real prompt tokens 253,864, 88 calls, 36 messages / 5,538 B), update PR-EVIDENCE 2.5 and cross-check 6, and soften the PR-DESCRIPTION non-claim to "no end-to-end task-accuracy claim *for the evolved dialect*; the hand-designed sym-v1 card has one completed official run (0.9/1.0)". Note that 0.9 < the occ1 flash 1.0 in the same cell, so do not present sym-v1 as neutral-or-better on task accuracy; present it as the single-run measurement it is (I-10 is a wall-clock timeout, same failure mode the glm-5.3 occ0 run hit).

### 2. HIGH (artifact vs report): the evolved-dialect artifacts changed AFTER the reports froze them

All four reports (PR-DESCRIPTION PENDING slot, PR-EVIDENCE cross-check 1, FINAL-REPORT 8.1, EXPERIMENT-LEDGER 11:5xZ) pin the evolution winner as `sym-v1-pt-mi-pt-sp`, generation 5, checksum `70031322...`. The current `research/symbolic_comm/evolution-winner.json` (mtime 07:14, during this review) contains a DIFFERENT winner: id `sym-v1-pt-pt`, generation 4, checksum `17836322dced3bb56e10bb0466abb5cb8d463ac6e4aba471f906ad5f24a4d49b`, and `evolution-log.jsonl` ends with `run_end winner sym-v1-pt-pt` plus generations 0-3 only (the first run's generations 4-6 were overwritten by a second evolution run launched at ~14:2xZ per the updated ledger). The campaign record is now internally inconsistent: the reports reference a frozen card whose artifact no longer exists in that form, and the log is truncated relative to both claimed winners (ledger says the first run "completed 6 generations, froze 70031322"; the log on disk has 4).

Fix: before push, either (a) re-freeze to whatever card is actually being shipped and update every checksum/id/generation mention in PR-DESCRIPTION, PR-EVIDENCE cross-check 1, FINAL-REPORT 8.1 (keep the evolved card out of the shipped-code claims either way - it is not registered in any `DialectRegistry`), or (b) keep `70031322` in the text and restore/pin the matching artifact. Do not ship a PR whose PENDING slot names a checksum that is not the one on disk. If the second run is still writing, wait for it or exclude the evolution artifacts from this PR.

### 3. MEDIUM-HIGH (claim hygiene): the -18.5% wire-token headline mixes harnesses, splits, and median-vs-mean

The PR-DESCRIPTION benchmark table row "Model-in-loop dialect eval ... median 117 wire tokens vs OCC-1 baseline 143.6 (-18.5%)" compares: 117 = median, 10-message sample, EVOLVE split, model-in-loop harness, glm-5.3-flash listener (evolution-log gen-0); 143.6 = mean tokens/message, 8-message DEV sample, real-tokenizer sample, glm-5.3 tokenizer (EXPERIMENT-LEDGER 07:2xZ). PR-EVIDENCE cross-check 1 documents this honestly, but the PR body's own table row carries no qualifier, and PR-DESCRIPTION's non-claims section only says "measured sample (median, EVOLVE split, one listener)" without noting the baseline is a different split, sample size, and statistic from a different harness. A hostile reviewer will read -18.5% as apples-to-apples.

Fix: one sentence in the PR-DESCRIPTION row or non-claims: "the OCC-1 side of -18.5% is the DEV 8-message real-tokenizer sample (mean 143.6, glm-5.3 tokenizer); the 117 is a median over a 10-message EVOLVE sample with the flash listener; the comparison is indicative, not a controlled A/B." Alternatively, drop the percentage from the table and keep the two raw numbers with their provenance.

### 4. MEDIUM (test honesty): the production negotiation path `occ2_target_send_policy` has no test

`select_occ2_dialect` (pure function) and `OccCapability::negotiate` are well tested, and the host-handler seam test covers wrap-when-enabled with a stubbed policy. But the real production path - `subagent_runs.rs::occ2_target_send_policy` reading `agentCommunication.occ2Enabled`, calling `controller.list_agents()`, matching `active_session_id` OR `session_id`, and refusing unevaluated cards only behind the dev flag - is untested. This is where a flag-on user's traffic actually decides OCC-1 vs OCC-2. The PR claims "negotiation absence keeps OCC-1" (t07) but t07 tests the pure function, not the daemon wiring. Notably, `occ2_target_send_policy` and `occ2_spawn_send_policy` pass `allow_unevaluated: true` (hardcoded), while `select_occ2_dialect`'s doc comment says production requires an evaluated card - sym-v1 has `metrics.samples == 0`, so today every flag-on send rides the dev override. That is a documented-ish decision (`.changes` says the dev flag allows the unevaluated built-in) but the doc comment on `select_occ2_dialect` ("this is the tier-2 gate of the fallback policy") promises more than the call sites deliver.

Fix (pick one): (a) add one unit test for `occ2_target_send_policy` covering flag-off, capability-absent, and id-match behavior (the session test harness in `agent_session.rs` already builds sessions with controllers); or (b) amend the doc comments and the `.changes` fragment to state plainly that v1 sends use the dev override for sym-v1 because the built-in card ships unevaluated, and that tier-2 gating becomes real only when evaluated cards are loaded. Minimum acceptable: (b) text fix; (a) preferred before a second campaign builds on this seam.

### 5. MEDIUM (PR text): PR-EVIDENCE mis-attributes the evolution proposer model, and the runs table omits a 5th run dir

(a) PR-EVIDENCE 2.4 says "Listener glm-5.3-flash, proposer glm-5.3, measure deepseek-v4.1-flash" but `evolution-log.jsonl` `run_start` records `proposer_model: glm-5.3-flash` (the second run changed it; the first run's start record, which the reports were drafted from, was overwritten). (b) PR-EVIDENCE 2.5 lists three landed runs plus the sym PENDING, and COMMIT-PLAN commit 5 stages four run dirs, but a fifth exists: `runs/occ2-levelI-n2-msg-glm53-occ1/` (occ1 with glm-5.3, 9 case files at review time, last write 07:03, no scorecard - in flight). If commit 5 stages `research/benchmarks/` while that run is mid-write, the tree ships a partial run directory.

Fix: update the proposer attribution after the evolution story settles (issue 2); add the 5th run to the evidence table or explicitly exclude it, and re-stat the run dirs immediately before staging (the ledger's own hygiene warning says the same). Do not stage an in-flight run.

### 6. LOW-MEDIUM (commit plan): commit-2's daemon_mode.rs split is fragile and the test-count annotations are off

The plan requires staging `daemon_mode.rs` in an intermediate state ("apply the full campaign diff, then hand-set the two literals to `None`, commit 2, then restore the final form for commit 3"). This reconstructs a state that never existed as a tested tree; the intermediate gates (commit-1 `occ::` = 203, commit-2 `communication_` = 9) are plan-time expectations, not recorded runs, and the plan itself flags them as such. Also: the commit-2 body says "7 unit tests" for agent_messages.rs but the diff adds 8; and the per-commit expected counts rely on the activity classify test landing in commit 2 while its name (`occ2_bodies_classify_by_the_consumed_path`) does not match the `communication_` filter (correct in the plan, but easy to misread).

Fix: either simplify to a single integration commit for files 2-8 (the intermediate states buy little for a feature that is OFF by default and the whole diff was tested as a unit), or keep the split but drop the untested intermediate-count gates and state "the final-tree gates are the authority; intermediates must merely compile". Correct the 7-vs-8 count.

### 7. LOW (code readability, not behavior): raw newline inside a string literal in the new width-pin test

`subagent_summary_line.rs` `three_slot_communication_indicators_respect_terminal_width` contains `lines.join("` + an actual LF + `")` (a literal newline character inside the string, not `\n`). It compiles (Rust allows raw newlines in string literals) and the test passes, but it looks like an escaped-newline editing accident and will draw a reviewer comment. The pre-existing sibling test uses `lines.join("\n")`.

Fix: replace the literal newline with `\n` in that one line.

### 8. LOW (PR text): benchmark latency/effort numbers are machine-bound but presented as absolute

t11 on this machine: 113.4 us dual encode / 65.4 us decode (PR: 137.5/82.3). Both are far under the 5,000 us budget, so the claim holds, but the PR table presents single-machine numbers without a host qualifier while carefully qualifying token counts. Same for the codec-corpus latency row.

Fix: add "(campaign host, single machine)" or "measurements are single-machine; re-run t11 to reproduce" to the latency rows, or cite the budget-relative statement ("under 3% of the 5 ms budget") which is the robust claim.

### 9. LOW (doc/behavior mismatch, informational): `select_occ2_dialect` doc comment vs call sites

The doc comment on `select_occ2_dialect` says "Cards must be evaluated with round-trip fidelity 1.0 unless `allow_unevaluated` is set (the dev flag): this is the tier-2 gate of the fallback policy." Both production call sites hardcode `allow_unevaluated: true`, so the tier-2 gate never fires in shipped code (sym-v1 ships with `metrics.samples == 0`). The `.changes` fragment discloses the dev-override intent, so this is a doc-precision issue rather than a hidden bypass, but a reviewer reading only the doc comment would believe a gate exists at runtime.

Fix: fold into issue 4's fix (amend the doc comment to state the v1 reality: the built-in card is unevaluated and every v1 send uses the override; tier-2 enforcement lands with evolved-card loading).

## Non-issues I checked and cleared

- "Learned/evolved" language: the PR title and body consistently say hand-designed; mod.rs doc was updated from "learned symbolic dialects (not implemented)" to "symbolic dialects ... implemented; discovery harness future work". Clean.
- Byte-identical-off: pinned by unit test + t05 + unchanged OCC-1 suites; daemon path returns `disabled()` with the flag off. Verified in code, not just prose.
- Unused pub fns: `OccCapability::render` (test-only use; doc says "for logs and debugging"), `DialectRegistry::latest` (test-only), `FallbackPolicy::None` (never constructed - a dead variant), `decode_agent_session_message_body` + `Occ2StrikeLedger` (test-only, documented as future runtime recorder). These are defensible as research API surface but expect a "dead code" review comment; the `.changes` fragments already disclose the ledger deferral.
- Corpus/fixture claims: 69 messages / 18 holdout verified by direct JSON count; kinds 9, statuses 7 as claimed; `scenarios.json` fixture comparison covered by `sym_v1_wire_never_exceeds_occ1_on_the_fixture`.
- Taskbench and SILO occ1/occ0 numbers: traced to `results_taskbench.json` and the scorecard JSONs (accuracy 48/50, 50/50, token means 239.0/254.3, medians 239/228, pilot 0.8/0.944 and 1.0/1.0, occ1 flash 1.0/1.0 with 191,634 tokens / 97 calls) - all match.
- GAP-AUDIT.md: landed during this review (was absent at task start). Its findings (latent A-E suite never run, model-pair matrix unimplemented beyond 5-of-10 cells defined, evolved-dialect holdout absent) are consistent with what I verified and are correctly reflected as PENDING/limitations in the PR. Its section-4 staleness finding matches my issue 1.
- `.rlm-review-target/`: my isolated cargo target dir at repo root. The parent added it to `.gitignore` (FLAG A); it is now covered, must never be git-added, and should be deleted only after this review's gate runs are no longer needed. The `.gitignore` line will appear in the diff (commit 4 or equivalent should carry it).

## Recommended pre-push order

1. Resolve the evolution-artifact inconsistency (issue 2) - it determines what the PENDING slot can say.
2. Fill the sym-v1 SILO row and the Python 44/44 slot (verified green in this review).
3. Apply the -18.5% qualifier (issue 3) and the proposer/run-dir fixes (issue 5).
4. Decide issue 4 (test vs doc fix) and issue 6 (commit granularity).
5. Optional: the two cosmetic fixes (issues 7-9).

None of these change shipped code behavior. With the text fixes applied, this PR is defensible against a hostile upstream review.
