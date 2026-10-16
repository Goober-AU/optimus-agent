# research/occ2opt_eval — DGX-only evaluation driver

Runnable orchestrator for the design in `reports/occ2opt/DGX-EVAL-DRIVER.md`.
`driver.py` is the bookkeeping / scoring / provenance engine ONLY: it never
calls a model, an HTTP API, or the network. The DGX GLM 5.3 model calls
happen exclusively through agent_message between campaign subagents
(speaker child + listener child), driven by the PARENT per the generated
PARENT-INSTRUCTIONS.md files.

## Safety

`--live` requires BOTH `--confirm-live` and `OPTIMUS_OCC2OPT_EVAL_LIVE=1`.
Without them the driver refuses (exit 2). Verified by the self-test.

## Commands

Dry-run (offline, synthetic replies, full pipeline + gates):

```
python research/occ2opt_eval/driver.py --split dev --mode scripted --batch 10 --dry-run
python research/occ2opt_eval/driver.py --self-test
```

OCC-2 dialect mode (`--dialect sym-v1` or `--dialect sym-v2-d2-r1`; see
reports/occ2opt/OCC2-EVAL-DRIVER.md): speakers encode into the dialect wire
behind its codebook; listeners get the codebook once per batch on the first
item (runtime parity for the session-scoped legend) and are scored on QA,
payload reconstruction, plus wire-token deltas vs OCC-1 and a measured
codebook-overhead column. Scoring uses the deterministic codec mirrors in
occ2_codec.py (sym-v1: production occ2.rs mirror with the pinned codebook
sha; sym-v2-d2-r1: the promoted DIALECT/2 card via research/symbolic_comm2).

```
python research/occ2opt_eval/driver.py --split dev --mode scripted --batch 10 \
    --dialect sym-v1 --dry-run
```

Live (parent-invoked, three stages):

```
export OPTIMUS_OCC2OPT_EVAL_LIVE=1
python research/occ2opt_eval/driver.py --split dev --mode scripted --batch 10 \
    --live --stage speaker --confirm-live [--run-dir <dir>] [--dialect <id>]
# ... parent drives speaker children, collects replies/speaker.jsonl ...
python research/occ2opt_eval/driver.py ... --live --stage listener --confirm-live --run-dir <dir>
# ... parent drives listener children, collects replies/listener.jsonl ...
python research/occ2opt_eval/driver.py ... --live --stage score --confirm-live --run-dir <dir>
```

Run the score stage with the venv python (transformers available) so
wire-token metrics are recorded; without it the metrics degrade to
`unavailable` and the gates stay green.

## Run directory layout

```
runs/<run-id>/
  run-manifest.json      corpus hashes (vs research/tasks2/receipts.json), seeds,
                         batches, canaries, decision log, slot ledger, labels
  prompts/speaker.jsonl  per-item speaker prompts (+ child hints, pipeline)
  prompts/PARENT-INSTRUCTIONS.md   exact parent loop for the current stage
  prompts/listener.jsonl per-item listener prompts (built from speaker wires)
  replies/speaker.jsonl  {"child","item","reply","sent_utc","received_utc","latency_ms"}
  replies/listener.jsonl same shape
  state-speaker.json     speaker parse outcomes + wire-by-item (fault/loss accounting)
  log.jsonl              per-item records: shas, chars, latencies, scenario tags,
                         speaker fidelity, qa rows, comprehension, faults
  summary.json           counts, both accuracy variants, canary drift, gates report
```

## Scoring

- Speaker fidelity: kernel OCC-1 codec decode + `contract.equal` oracle vs the
  ground-truth typed payload (scripted mode); free mode scores the same way
  against the ground truth (stricter).
- Listener: `research/tasks/checkers.py` (A text / D set / E numeric) per QA
  item; payload reconstruction via `contract.payload_from_listener_json` +
  equality oracle.
- Protocol faults (fenced wire, missing PAYLOAD) are unscored and counted in
  a separate bucket; both accuracy variants (scored-only and
  faults-count-zero) are always quoted together.
- Acceptance gates (manifest completeness, child-loss accounting, canary
  drift, fault-rate reporting, labeling rules) run at score time.

## Self-test coverage (174/174 with transformers; 170/170 degraded)

- scripted + free dry-runs on the dev split with exact expected fault
  detection (2 speaker semantic, 1 speaker format, 1 listener QA, 1 listener
  comprehension, 1 listener format; canaries untouched),
- byte-identical determinism across runs, for the OCC-1 protocol and the
  OCC-2 sym-v1 dialect mode,
- live-mode refusal guards (subprocess, scrubbed env),
- negative controls: tampered corpus hash fails gate 1; an unaccounted item
  fails gate 2,
- no network API references in the driver or codec source,
- OCC-2 mode: both dialect dry-runs green with the same designed fault
  numbers, codebook carriers == batches, listener ordering + child hints,
  codec round-trips over the whole dev split, codebook sha pins, and the
  degraded-tokenizer path (metrics null, gates green).
