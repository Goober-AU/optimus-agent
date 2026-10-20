# REVIEWER PASS — W64 hostile review of the whole PR diff

Worker W64, worktree `optimus-rot2-w64-reviewer`, branch `rot2/w64-reviewer`.
Scope: `origin/main` (`6000d6bc1`) ... `HEAD` (`90fcd17db`, 143 commits, 361
files, +168,122/-197). Method: whole-diff read of every Rust source and test
file under `crates/pi-coding-agent/`, the README/settings/changelog surfaces,
then claims-vs-tree checks of `reports/context-rot-r2/` and the research
drivers named by those reports; bounded `git grep`/`git log`/`git show` only.
Offline. Nothing was fixed; this file is the only tree change.

Verdict up front: the code is in good shape — no debug leftovers, no
commented-out code, no campaign-introduced compiler warnings, and every
defect fix I traced has a real, non-vacuous regression test (spot-runs green
at the final tip, section 5). The embarrassment risk is concentrated in the
push-facing artifacts: the PR body carries paste debris and three factual
status errors, and the changelog fragment carries four duplicated bullet
pairs. Those are the blockers. A recurring root cause: the report refresh
waves (W58/W59/W60) all predate or missed the last fix landings (the W16
supersession integration, D31, D32), so the reviewer-facing documents lag the
tree they describe.

## 1. Blockers (fix before push)

### B1. PR-BODY.md carries six pasted `git log` lines as prose debris

`reports/context-rot-r2/PR-BODY.md` lines 10, 59, 101, 445, 453 and 547 each
contain the freestanding line:

```text
78ccdf254 (docs(context-rot): cross-report consistency sweep at the W50 tip)
```

`78ccdf254` is not an ancestor of HEAD (`git merge-base --is-ancestor` fails);
it is a worker-tree sha pasted into the committed body. Line 10 sits directly
under the header note, before `## Summary` — it is the first thing a reviewer
reads. The W59 sweep (`0e9cf7c6a`, "markers, pasted log lines") removed the
six conflict blocks these lines lived in but kept the lines themselves
(W59's own R1/R2 said the `78ccdf254` occurrences were "inside the six
conflict blocks"; the markers are gone, the paste is not).

Minimal fix: delete the six lines. No other change.

### B2. PR-BODY.md and FINAL-REPORT.md state D31-D33 open; D31 and D32 are fixed at HEAD

`reports/context-rot-r2/PR-BODY.md:444` ("the post-wave findings D31-D33
open") and `reports/context-rot-r2/FINAL-REPORT.md` lines 20, 27, 611, 634,
684, 694, 722, 739 ("D31-D33 the open post-wave findings" and variants).
At HEAD the BUG-LEDGER rows are current and contradict both files:

- D31 FIXED (`6041f71c9`, marked `e6e909e16`),
- D32 FIXED (`53ab2e3d9`, marked `b12560575`),
- D33 VERIFIED-OPEN (correctly disclosed everywhere).

I re-ran the guard suite at the final tip (section 5):
`retired_ids_do_not_vouch_for_verified_claims` (D31) and
`native_compaction_reports_the_salvage_of_a_malformed_prior_ledger` (D32)
pass inside `rot2_wave_adversarial` 9/9. The two headline reports therefore
understate the branch: they disclose two fixed defects as open.

Minimal fix: PR-BODY.md:444 and the eight FINAL-REPORT locations change to
"D33 open; D31 and D32 fixed (`6041f71c9`, `53ab2e3d9`)".

### B3. The changelog fragment has four duplicated bullet pairs, a stale open-gap sentence, and no D30 bullet

`.changes/context-rot-r2.md` (34 lines). Duplicate pairs, verbatim-subject:

| first | second | subject |
| --- | --- | --- |
| line 12 | line 20 | D21 render caps |
| line 13 | line 18 | D18 failure notice |
| line 14 | line 19 | D19 live-disclosure caps |
| line 17 | line 30 | `typed` mode (eight-defect red team) |

Cause: the W45 (`9cf87b266`) and W50 (`5e2491cff`) fragment commits both
landed and unioned; W58's dedupe pass (`16b639ea0`) removed eight of the
twelve duplicates W59 counted and left these four. Additionally:

- both `typed` bullets still carry the pre-W46 gap sentence ("one classifier
  gap remains open (user-stated decisions classify as low-priority episodes
  and retire at the first merge)" / "the known remaining gap is that an
  unprefixed decision stated in plain user text classifies as step-scoped
  low-priority material and retires at the first merge") — D30 is FIXED
  (`10e2b36e6`, BUG-LEDGER row current, smoke seed `ARCH-DECISION-C` at
  `tests/rot2_multicycle.rs:970-978`), so the "known remaining gap" text is
  false at HEAD;
- there is no bullet for the D30 fix itself;
- the "eight defects found and all eight fixed" sentence predates the W48
  wave (D31, D32), so the red-team count it discloses is no longer the whole
  story.

Line 29 is a stray blank line splitting the bullet list in two. The
fragment is the release note source; duplicates and a false open-gap claim
ship to users if the integrator assembles it as-is.

Minimal fix: keep lines 18-20 (the later, better-worded trio), delete lines
12-14; keep one `typed` bullet (line 30 reads better), delete line 17; drop
the stale gap sentence from the survivor; add one D30 bullet ("Fixed a
decision stated by the user in plain text classifying as step-scoped
low-priority material and retiring at the first merge; user-stated decisions
now enter the typed ledger as verified decisions"); optionally reconcile the
red-team count ("ten defects across two waves" or name D31/D32).

### B4. PR-BODY.md claims the v1 supersession rework is not in this PR; it is

`reports/context-rot-r2/PR-BODY.md:450-452`:

> The v1 ledger's deeper supersession semantics are not reworked in this PR
> (the drafted `rot2/w16-supersession` line was superseded by the typed-mode
> fixes); D18/D19/D20 and D21 are fixed on this branch and disclosed above.

False at HEAD. The BUG-LEDGER W9-F1/F3/F4 row says "FIXED (W16 v1
supersession refinement, integrated by W51, this PR)" and the tree carries it:
`task_state.rs` has `CORRECTION_CUE_RE`/`PROHIBITION_RE` clause-level
classification, the `SCAFFOLD` anchor filter, the ordinal gate and the
`old_text` SUPERSEDED render (`a112034dc`, `611d2312c`, `3de7ef222`, all
ancestors), pinned by `tests/rot2_supersession.rs` (4 tests) and the
`rot2_render_cap` suite. FINAL-REPORT.md:44 makes the inverse error in the
other direction ("In flight at this writing and NOT in this tree: the W16
supersession work") — see S1.

Minimal fix: rewrite the bullet to "The v1 ledger's supersession semantics
were reworked (clause-level correction classification with prohibition
scrubbing, scaffold-aware anchor scoring, content-derived ids, the SUPERSEDED
render with the retired text); see the W9-F1/F3/F4 ledger row."

## 2. Should-fix (post-PR acceptable, none change code behavior)

### S1. FINAL-REPORT and COMPACTION-CORRECTNESS misstate the W16 supersession status

`reports/context-rot-r2/FINAL-REPORT.md:44` — "In flight at this writing and
NOT in this tree: the W16 supersession work (`rot2/w16-supersession`, no
commits over this head)". Written by the W49/W50 refresh, false since the
W51 integration landed. `reports/context-rot-r2/COMPACTION-CORRECTNESS.md:216-217`
— "the `rot2/w16-supersession` v1 rework never carried commits and holds
nothing in flight - the supersession mechanism moved to typed v2 (D29)"
contradicts the BUG-LEDGER row and the tree for the same reason. Both files
need one refreshed sentence.

### S2. EXPERIMENT-LEDGER.md carries four pasted `git log` lines

Lines 71, 82, 402 and 508 each contain the freestanding line:

```text
712791dd8 (docs(context-rot): verify the experiment ledger against the artifacts (W60))
```

`712791dd8` is not an ancestor of HEAD (the on-branch W60 commit is
`90fcd17db`). Ironically the W60 verification commit introduced its own
paste debris into the ledger it was verifying. Same class as B1; delete the
four lines.

### S3. BUG-LEDGER.md table debris survives the W59 sweep

Line 111 and line 113 are freestanding `D` lines between the section-5 table
rows; they split the markdown table so the D31 and D32 rows render as plain
text, not table rows. Line 116 is a trailing pasted commit line
(`1671f0b6d (docs(context-rot): mark D31 fixed in the bug ledger)`, not an
ancestor). W59's R3 recorded exactly these; `0e9cf7c6a`/`16b639ea0` removed
three of the six but left these. Delete the three lines.

### S4. No full gate re-run at the final tip; recorded gate numbers are stale

`reports/context-rot-r2/REGRESSION-GATES.md` is pinned to `29f1f0c92` (80
commits over base) and its own W50 note says "No full-gate re-run was
performed by the W50 docs-only sweep; re-run the focused suites at the final
tip before merging." HEAD is `90fcd17db` (143 commits). Since the recorded
pass, the branch landed the W36-W53 fix waves (typed_state, task_state,
agent_session, compaction changes) — exactly the files the gates cover.
Drift I measured: `--lib typed_state` records "60 passed", the module now
has 78 tests (78/78 green, section 5). PR-BODY's own gate table is older
still ("the last committed full pass ran at the `4b0add723` tip", where
`rot2_multicycle` counted 4 passed + 1 ignored against 6 tests today). My spot-runs
are green, but the campaign has no full-matrix run at the tip it intends to
push. Re-run the focused matrix (or at minimum the 12 rot2 suites plus the
compaction_* gate list) at HEAD and refresh both tables.

### S5. A unit test writes a tracked repo file by default

`crates/pi-coding-agent/src/core/context_health_calibration.rs`
(`current_heuristic_scores_poorly_against_semantic_constraint_loss`):
`std::fs::write(&path, &json).expect("write the calibration artifact")`
targets `research/context_rot_r2/health/context_health_calibration.json`
(tracked) unless `ROT2_HEALTH_CALIBRATION_OUT` is set. Two problems: a plain
`cargo test -p pi-coding-agent --lib` can dirty the working tree whenever the
generator output drifts from the committed copy; and the doc comment above
the test claims "a read-only checkout must not fail the measurement" while
the unconditional write `expect`s success — on a read-only checkout the test
panics, contradicting the comment. Minimal fix: write to a temp dir by
default and only write the repo path when `ROT2_HEALTH_CALIBRATION_OUT` is
explicitly set (or drop the write and assert against the committed copy).

### S6. Dead code in the harness binary

`crates/pi-coding-agent/src/bin/compaction_harness.rs:1145-1148`:

```rust
#[allow(dead_code)]
fn unused_json() -> Value {
    json!({})
}
```

A leftover stub with an explicit suppression attribute. Delete it.

### S7. `run_r2e2.py` still defaults into the private credential store

`research/context_rot_r2/live/run_r2e2.py:489-495` — `load_credentials()`
defaults `R2E2_GLM_RESOLVER`/`R2E2_POMERIUM` to
`.../profile/agent/skill-state/dgx-k3-login/glm53-resolvers/cd5ef6501b924449917676c6ddb998a8.ps1`
and `.../profile/agent/credentials/Get-K3PomeriumJwt.ps1`. Env-overridable,
but the machine-local default remains committed and discloses private
credential infrastructure in a public repo. W42's S4 called this exact
function out; the fix wave (`8e7796702`) converted `harness_client.py` to
fail-closed and left this one. Make both fail closed without the env vars,
as `harness_client.py` does. (`compactbench_adapter/live/run-config.json`'s
machine paths are covered by the recorded "artifact provenance" ruling.)

### S8. A test name asserts the opposite of its body

`crates/pi-coding-agent/tests/rot2_typed_adversarial.rs:1109` —
`malformed_typed_state_payload_silently_drops_durable_records` asserts the
D26 fix (records salvaged, `dropped_records == 0`, diagnostics present); it
is the un-ignored former red test and now pins that records are NOT silently
dropped. The name states the defect, not the pinned behavior; a reviewer
auditing D26 greps for it and reads a contradiction. Rename to
`malformed_typed_state_payload_does_not_silently_drop_durable_records`
(the BUG-LEDGER D26 row cites the old name, so update the citation too).

## 3. Nits

- N1. `typed_state.rs:1097-1105` — `reconcile` carries two consecutive doc
  comments (the stale pre-D24 paragraph followed by the current one);
  rustdoc renders both. Delete the first paragraph.
- N2. `reports/context-rot-r2/COMPACTION-CORRECTNESS.md` — filename and H1
  typo (the former name dropped the N from "COMPACTION"). Renaming breaks
  citations in three reports; do it in the same integrator pass or leave
  with a note. RESOLVED by W76 (`rot2/w76-rename`): the file and its H1 are
  renamed and every citation updated.
- N3. Stale counts in the headline reports: FINAL-REPORT H-r2-3 says "74 unit
  tests" for typed_state (78 at HEAD); PR-BODY:472 says "29 files under
  crates/pi-coding-agent" (36 at HEAD, `git diff --name-only` over
  `origin/main...HEAD`). Both were pinned to older tips.
- N4. v1 capture/classification vocabulary mismatch (task_state.rs): the
  per-clause classifier `CORRECTION_CUE_RE` treats "correction" as a cue,
  but capture still requires a message-level `SUPERSEDE_RE` hit or a
  clause-level `CONSTRAINT_RE` hit, neither of which contains "correction" —
  so a bare "Correction: use X" clause (no instead/drop/replace token, no
  modal) is never captured at all. Pre-existing shape, unchanged by this
  campaign; worth a line in the ledger's W9-F1 note if v1 is ever touched
  again.
- N5. v1 `content_id` (task_state.rs) derives 8 hex chars (32 bits) from the
  dedupe key with no collision-resolution loop — unlike typed_state's
  `reconcile`, which re-hashes on collision. Two distinct keys colliding
  would make `seen`/`capture_order` conflate items. Probability is negligible
  at ledger scale; note for symmetry with the v2 design.
- N6. `render_hot_state` / `render_durable_index` (typed_state.rs) have no
  production callers — research/test-only API. The module docs disclose the
  measurement-only contract for the calibration module; these two are the
  same shape but undocumented as such.
- N7. Carried from W42 N3 (still open, still nits): `lossy_summary`
  (`src/bin/compaction_harness.rs:257`) and `is_empty`
  (`typed_state.rs`) lack doc comments; every other new pub item is
  documented.

## 4. Prior-audit items at this tip (W42 / W59 disposition check)

- W42 B1 (D17 changelog claim): RESOLVED — the bullet is rescoped to an API
  addition with "production call-site wiring remains follow-up work"; the
  ledger row matches; `estimate_context_tokens_with_request` remains
  test/adapter-only (disclosed).
- W42 S1 / W59 R1-R4: SUBSTANCE RESOLVED, LANDING REGRESSED — the conflict
  markers are gone, but the paste debris survives in PR-BODY (B1),
  EXPERIMENT-LEDGER (S2) and BUG-LEDGER (S3); the fragment duplicates
  survive (B3); the stale in-flight claims recurred around the newer waves
  (B2, B4, S1).
- W42 S4: PARTIAL — `harness_client.py` and `run_live.py` fail closed;
  `run_r2e2.py` does not (S7).
- W42 S5 / W59 S5: RESOLVED and re-verified here — all five raw call logs
  are LFS pointers at HEAD with correct oid/size (the 8,131,489-byte
  `live/raw-calls.jsonl` working-tree sha256 matches its pointer). The
  "git lfs push must accompany the first push" reminder stands.
- W42 S7: RESOLVED — no `set_var`/`remove_var` remains in
  `rot2_typed_adversarial.rs`.
- W59 R5 (D31 open): RESOLVED by `6041f71c9`; verified green (section 5).
- W59 R6 (D33): still open, correctly disclosed as VERIFIED-OPEN.
- W59 R7 (disclosed open set: D7, D12 soft fence, D20 residue, D10
  "never retry", W9-F1 v1 capture gap, D17 wiring): unchanged and still
  disclosed.

## 5. Verification runs at the final tip (`90fcd17db`)

Windows git-bash, `rustc 1.98.1`, shared `C:/Users/openclawuser/rot2-shared-target`
(was cold; first build 5m17s):

| command | result |
| --- | --- |
| `cargo test -p pi-coding-agent --lib typed_state` | 78 passed, 0 failed, 0 ignored (2.99s) |
| `cargo test -p pi-coding-agent --test rot2_wave_adversarial` | 9 passed, 0 failed, 0 ignored (17.11s) — includes the D31 and D32 guards |
| `cargo test -p pi-coding-agent --test rot2_typed_adversarial` | 24 passed, 0 failed, 0 ignored (0.37s) |
| lib build warnings | 355 — identical to W42's pre-existing base count; no campaign-introduced warnings at HEAD |

Not run by this pass (disclosed): the remaining rot2/compaction integration
suites, the Python research suites, the harness binary adapter checks, and
anything model-dependent (offline rule). The campaign's own numbers for those
are pinned to older tips (S4).

## 6. Code review notes (no action required)

For the record, code paths a hostile reviewer would probe, and why they
hold:

- `prepare_branch_entries` pairing rule (D10): the over-budget assistant
  call is kept when the chronologically following entry is its tool result;
  the reversed walk and `index + 1` successor semantics line up (entries are
  oldest-first in the slice); the rule can overshoot the budget by one
  assistant message, which the doc discloses.
- `allocate` (typed_state): greedy per-item with section-header cost charged
  once; every CURRENT record lands in `included` or `overflow`;
  mandatory overflow both fails the episode (compaction.rs
  `typed_allocation_failure`, before any model call) and is listed in the
  overflow report.
- The typed verification/repair loop (compaction.rs): one bounded
  deterministic repair, re-verified; a repair that still fails fails the
  episode; the split-turn rebuild keeps prefix/summary composition intact;
  `format_file_operations` is appended after verification (recorded as a
  known observation in the ledger).
- `parse_typed_state` salvage: per-record retry with positional-field repair;
  diagnostics ride the serde-skipped `salvage` rider and surface through
  `details.typedVerification.malformedPriorLedger` on both the text and
  native paths (D26/D32).
- `cap_live_disclosure_list` (D19): evicts to `retention - 2` before
  re-inserting the marker so the post-push length is exactly `retention`;
  the marker folds its count forward and keeps the first evicted entry's
  timestamp; the second-round test pins both.
- `migrate_v1_to_v2` (D8): out-of-bounds/header index falls back to the
  entry after the compaction; nothing follows -> field absent and
  `prepare_compaction`'s own fallback governs (tested both ways).
- LFS: `.gitattributes` covers exactly the five raw call logs; HEAD blobs
  are pointers; oids/sizes re-verified against the working tree this pass.

## 7. Files this pass touched

- `reports/context-rot-r2/REVIEWER-PASS.md` (this file) — created.
Nothing else; `git status` clean apart from it.
