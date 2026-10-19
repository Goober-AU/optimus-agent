# EXPERIMENT LEDGER — context rot iteration 2

Every arm, every run, every NOT-EXECUTED item. Production-path measurements
run the real Rust pipeline (`prepare_compaction` -> `compact` ->
`append_compaction` -> `SessionManager` restore) via
`crates/pi-coding-agent/tests/rot2_multicycle.rs`; Python only orchestrates.

## R2-E1 — multicycle retention, default vs taskState (deterministic lossy summarizer)

- date: 2026-10-19
- scenario: `r2-e1-multicycle` (19 probes: 8 constraints, 4 decisions, 4 evidence,
  2 corrections/supersession, 1 subagent artifact), 5 compaction cycles, filler turns
  between cycles. Seeds: blake2b digest (M5 fixed). Scenario JSON:
  `research/context_rot_r2/runs/r2-e1-multicycle.scenario.json`.
- summarizer: deterministic lossy fixture (copies protected blocks verbatim, fixed
  head/tail loss elsewhere, previous-summary decay) — identical across arms, so
  differences measure PIPELINE protection, not model skill.
- model calls: none (offline fixture). This is a pipeline-mechanics result, NOT a
  model-quality result.

| arm | retention | constraints | decisions | evidence | supersession | subagent | mean req chars |
| --- | --- | --- | --- | --- | --- | --- | --- |
| default | 0/19 (0.0%) | 0/8 | 0/4 | 0/4 | 0/2 | 0/1 | 14 765 |
| taskState | 14/19 (73.7%) | 8/8 | 4/4 | 0/4 | 2/2 | 0/1 | 18 429 |

- finding 1 (D4 confirmed on the production path): with an even mildly lossy
  summarizer, FIVE repeated compactions lose every unprotected anchor in the
  default arm. Summary-of-summary is a real information-loss mechanism, not a
  theoretical one.
- finding 2: the v1 ledger fully protects constraints, decisions and explicit
  corrections (14/14) at +3 664 mean request chars (~916 tokens/cycle, matching
  iteration-1's +913..1 751).
- finding 3 (v1 gap A): tool-result evidence is lost (0/4). Root cause:
  `task_state.rs` `EVIDENCE_RE` matches only `test result:|error[E\d+]|N files
  changed|panicked at|FAILED`; `cargo test: N passed; 0 failed`, `build succeeded`
  and benchmark lines do not match. Evidence extraction is content-regex based
  instead of role-based.
- finding 4 (v1 gap B): subagent artifacts are lost (0/1). Root cause:
  `entry_text` handles only `Message` entries; `CustomMessage` entries
  (`agent_message` from children) are invisible to the ledger.
- consequence for typed_state v2 (W4): classify by role/provenance first
  (tool results => VERIFIED_TOOL_RESULT/SOURCE_EVIDENCE by role, not regex;
  custom agent messages => SUBAGENT_STATE), which closes both gaps.
- decision: KEEP the taskState arm as the CONTROL B baseline for r2; the typed
  state v2 arm must beat 14/19 with evidence+subagent retained, at comparable
  or lower token cost.

## R2-E0 — adapter smoke (d4-drift, 3 probes, 5 cycles)

- same adapter, smaller scenario; default lost all anchors by cycle 2;
  taskState retained constraint+decision, lost evidence. Superseded by R2-E1
  (same mechanism, larger probe set).

## NOT EXECUTED (yet)

- live-model arms (real GLM 5.3 summarizer + real probe answering): BLOCKED on
  typed-state integration; planned as R2-E2 with the delimiter answer protocol.
- CompactBench official suite: adapter design complete (see
  KNOWLEDGE-TRIAGE-STUDY.md / COMPACTBENCH-ADAPTER-DESIGN.md), not yet run.
- ContextBench coding tasks: eval design complete, not yet run.
