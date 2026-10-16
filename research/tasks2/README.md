# research/tasks2 — occ2opt expanded message corpus (msgs_corpus2)

3x expansion of the frozen v1 corpus (`research/tasks/msgs_corpus.json`,
69 messages) for the OCC-2 optimization campaign. The v1 artifacts are
never modified; this is a new artifact set alongside them.

## Artifacts

| File | Role |
|---|---|
| `manifest.json` | FROZEN generation config (seed bases, split targets, story roster, QA policy, holdout discipline). Written before any generation. |
| `gen_corpus2.py` | Deterministic generator. Phases: `--phase evolvedev` (writes evolve+dev), `--phase holdout` (writes holdout, LAST, independent seed base), `--phase stats`. |
| `msgs_corpus2_evolve.json` | 90 messages, 12 stories. |
| `msgs_corpus2_dev.json` | 60 messages, 8 stories. |
| `msgs_corpus2_holdout.json` | 54 messages, 7 stories. Generated last; nothing adapted to it afterward. |
| `selftest.py` | Deterministic self-test over the files (schema, invariants, round trip, disjointness, QA, coverage, manifest consistency, determinism). Writes `receipts.json` on a green run. |
| `receipts.json` | Generation receipt: file hashes, per-split stats, check counts, generation notes. |

## Entry schema (extends the v1 fixture shape)

```
{"id": "e2-e01-01", "split": "evolve", "story": "occ2b-e01",
 "scenarios": ["parent_to_child"], "holdout": false,
 "plain_english": "...",            # deterministic OCC-0 rendering (v1 renderer)
 "payload": {...},                  # v1 payload shape, unchanged
 "qa": [{"family": "A", "q": "...", "a": "..."}]}
```

`payload` is identical in shape to v1 (fixture-compatible with
`crates/pi-coding-agent/tests/fixtures/occ/scenarios.json`), so
`AgentCommMessage.from_payload(entry["payload"])` works unchanged.

## Coverage

- 20/20 mission scenario types in EVERY split (parent_to_child,
  child_to_parent, sibling_handoff, multi_hop, large_findings,
  small_findings, contradictory_evidence, source_code_evidence,
  failed_tests, successful_tests, partial_completion, cancellation,
  new_dependencies, state_updates, missing_evidence,
  multiple_source_refs, task_reassignment, long_history,
  repeated_messages, model_switching).
- All 9 message kinds, all 7 task statuses, all 8 evidence kinds,
  verification pass/fail/unknown, parent_task_id reassignments,
  designed repeated messages (3 digest pairs, within-story only).

## Numbers (see receipts.json for the full breakdown)

- 204 messages / 612 QA items total; evolve 90 / dev 60 / holdout 54.
- OCC-1 wire: 69,611 bytes total, max single message 749 bytes.
- Self-test: 5098/5098 checks passed.

## Splits and holdout discipline

Split assignment is story-level; a story's whole chain lives in exactly
one split (checked). The holdout split was generated last, in a separate
invocation, from an independent seed base, after evolve+dev were final.
Pre-holdout authoring fixes (before any holdout generation): the
long_history tag rule for long-pace stories and chain-count trims in
occ2b-d03, occ2b-h03, occ2b-h07 to match the frozen manifest targets.
Nothing was adapted to holdout content after its generation. If any
post-generation fix is ever needed, all three splits regenerate from
scratch and the regeneration is recorded in receipts.json.

## Reproduction (from the worktree root)

```
python research/tasks2/gen_corpus2.py --phase evolvedev
python research/tasks2/gen_corpus2.py --phase holdout
python research/tasks2/selftest.py
```

Cross-process determinism verified: regeneration is byte-identical
(hashes in receipts.json). Read-only imports: `research/tasks/gen_corpus.py`,
`research/tasks/checkers.py`, `research/symbolic_comm/contract.py`,
`prime-agent-runtime/src/rlm/occ1.py` (kernel codec — the round-trip
oracle is the real codec, not a port).
