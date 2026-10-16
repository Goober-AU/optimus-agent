# DRIVER-IMPL — DGX eval driver implementation report

Campaign: OCC-2 optimization (occ2opt). Owner: occ2-baseline.
Artifact: `research/occ2opt_eval/driver.py` (+ README.md, runs/ evidence).
Design source: `reports/occ2opt/DGX-EVAL-DRIVER.md` (accepted unchanged).

## 1. What was built

A single-file offline orchestrator implementing the accepted design:

- **T1 paired topology bookkeeping**: per-batch item plans, pipeline
  assignment (speaker eval-spk-1/2, listener eval-lst-1/2), canary items
  (2 per batch, deterministic, non-fault in dry-runs), slot ledger
  (peak 4).
- **Two modes**: scripted-speaker (typed payload -> wire) and free-speaker
  (plain_english -> wire, stricter scoring), identical scoring pipeline.
- **Three live stages** (speaker plan -> listener plan -> score), each a
  pure offline file operation; the model interaction is the parent's
  agent_message loop per the generated PARENT-INSTRUCTIONS.md.
- **Scoring**: kernel OCC-1 codec (via frozen symbolic_comm/contract.py,
  PYTHONPATH-free import bootstrap) for speaker fidelity + listener payload
  reconstruction; frozen research/tasks/checkers.py for A/D/E QA golds.
- **Provenance**: per-item JSONL records (prompt/reply sha256 + chars,
  latencies, scenario tags, split, batch position, canary flag, fault
  stage) and a run-manifest (corpus hash pinned against
  research/tasks2/receipts.json, seeds, batches, frozen decision log,
  slot ledger, labels).
- **Acceptance gates** at score time: manifest completeness, child-loss
  accounting, canary drift, fault-rate reporting (both accuracy variants),
  labeling rules (chars-not-tokens, X1-X4 deferred, C1 cell, channel).
- **Safety**: `--live` requires `--confirm-live` AND
  `OPTIMUS_OCC2OPT_EVAL_LIVE=1`, else exit 2 with a REFUSED message.
  The driver contains no network/model code at all (self-test asserts the
  source references no network APIs).

## 2. Gate result: dry-run self-test GREEN

`python research/occ2opt_eval/driver.py --self-test` -> **71/71 checks passed.**

Covered checks:
1. scripted-speaker dry-run on dev (60 items, 6 batches of 10): gates GREEN
   (23/23), and the summary matches the designed fault injection EXACTLY:
   - speaker: 59 scored, 57 fidelity_ok, 1 protocol fault
     (fidelity_rate 0.9661)
   - listener: 58 scored, 1 skipped (speaker format fault), 1 protocol
     fault
   - QA: 172/174 correct (accuracy 0.9885; accuracy incl. protocol faults
     0.9718 = 172/177)
   - comprehension: 55/58 (rate 0.9483)
   - canaries: 12 items, 36/36 (accuracy 1.0), drift detected: none
   - child_lost 0, unaccounted 0
2. free-speaker dry-run: identical numbers (synthesis is mode-independent;
   mode label differs).
3. Determinism: a second scripted run is byte-identical in log.jsonl and
   summary.json.
4. Live refusal guards (subprocess, env scrubbed):
   - `--live` without `--confirm-live`: exit 2,
     "REFUSED: --live requires --confirm-live (nothing model-touching may
     happen by accident)"
   - `--live --confirm-live` without the env var: exit 2,
     "REFUSED: --live requires the environment variable
     OPTIMUS_OCC2OPT_EVAL_LIVE=1"
   - both guards present: speaker stage plans offline (exit 0) and creates
     NO replies/ directory (nothing fabricated).
5. Negative controls (teeth proof):
   - NC1: tampering run-manifest.json's corpus hash -> gate 1 fails.
   - NC2: deleting a speaker reply AND its state entry (unaccounted item)
     -> gate 2 fails, victim item flagged.
6. Source hygiene: no network API references in driver.py.

Fault-propagation proof (from log.jsonl, run dry-dev-scripted-b10):
a designed speaker semantic fault (NEXT-drop) yields
speaker_fidelity_ok=false, listener QA still 3/3 (NEXT is not QA-covered),
comprehension_ok=false — exactly the semantics the design intended: the
listener is scored on the wire it actually received.

## 3. How the parent launches a live batch

Precondition: `export OPTIMUS_OCC2OPT_EVAL_LIVE=1` in the parent's shell.

Stage 1 - plan (offline, writes prompts + instructions):

```
python research/occ2opt_eval/driver.py --split dev --mode scripted \
    --batch 10 --live --stage speaker --confirm-live
```

Creates `research/occ2opt_eval/runs/live-dev-scripted-<stamp>/` with
`run-manifest.json`, `prompts/speaker.jsonl`,
`prompts/PARENT-INSTRUCTIONS.md`. Note the printed run dir.

Stage 2 - the parent drives the speakers (the ONLY model-touching step):

- spawn two DGX GLM 5.3 children (`eval-spk-1`, `eval-spk-2`), one per
  pipeline column in prompts/speaker.jsonl;
- for each row: send the full prompt text via agent_message, request an
  explicit reply to the parent;
- save every reply as one JSONL line in `<run-dir>/replies/speaker.jsonl`:
  `{"child": "eval-spk-1", "item": "<id>", "reply": "<full text>",
  "sent_utc": "...", "received_utc": "...", "latency_ms": 1234}`;
- a child that dies or never answers: leave the item absent (the driver
  accounts it as child_lost; never invent a reply).

Stage 3 - plan listeners (offline):

```
python research/occ2opt_eval/driver.py --split dev --mode scripted \
    --batch 10 --live --stage listener --confirm-live --run-dir <run-dir>
```

Validates every speaker reply, skips protocol-faulted items, writes
`prompts/listener.jsonl` + updated instructions.

Stage 4 - the parent drives the listeners: same loop with `eval-lst-1/2`,
collecting `<run-dir>/replies/listener.jsonl` (same row shape).

Stage 5 - score + gates (offline):

```
python research/occ2opt_eval/driver.py --split dev --mode scripted \
    --batch 10 --live --stage score --confirm-live --run-dir <run-dir>
```

Writes `log.jsonl` + `summary.json`; exit 0 iff all five acceptance gates
pass. Quotable numbers require a GREEN gate report per the design doc.

Batch protocol per the design: fresh children per batch (delete + respawn);
one split per child session; holdout runs only after every tuning decision
is frozen (the manifest records the frozen decision log); canaries ride in
every batch and their drift is checked at score time.

## 4. Deviations / notes

- `accuracy_excl_protocol_faults` from the design doc is implemented as
  `accuracy_incl_protocol_faults` (answers over ALL attempted items, fault
  items scoring zero) so that the two quoted variants genuinely differ;
  the doc's intent (both denominators always quoted together) is met.
- Listener protocol faults are fully unscored (QA included) rather than
  partially scored; simpler and stricter accounting, documented here.
- Live stages never fabricate or transform replies; the only transformation
  is parsing/scoring at score time.
- Constraints held: no network, no HTTP model calls, no git commits, no
  repo-wide greps, frozen paths untouched (git-verified; only
  research/occ2opt_eval/ and this report were created).

## 5. Evidence paths

- Driver: `research/occ2opt_eval/driver.py` (56,899 bytes, py_compile clean)
- README: `research/occ2opt_eval/README.md`
- Dry-run artifacts: `research/occ2opt_eval/runs/dry-dev-scripted-b10/`
  (log.jsonl 60 records, run-manifest, prompts, replies, summary, gates)
- Self-test artifacts: `research/occ2opt_eval/runs/selftest-{scripted,free,
  scripted-again,nc1,nc2}/`
