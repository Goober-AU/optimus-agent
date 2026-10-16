# Live listener stage - parent instructions

Run dir: C:/Users/openclawuser/optimus-occ2opt-20261016/research/occ2opt_eval/runs/live-holdout-sym-v2

1. Spawn two DGX GLM 5.3 listener children (eval-lst-1, eval-lst-2).
2. OCC-2 mode: every row carries a child_hint (eval-lst-1 or
   eval-lst-2, by batch parity). Send each row to its child_hint
   child, and send the rows of the same batch to the SAME child in
   file order: the first row of each batch carries the dialect
   codebook (carries_codebook true), the rest rely on the child
   remembering it - that mirrors the runtime's once-per-session
   codebook injection and is exactly what is being measured.
3. Save every reply as ONE line in replies/listener.jsonl:
   {"child": "eval-lst-1", "item": "<item id>", "reply": "<full reply text>",
    "sent_utc": "...", "received_utc": "...", "latency_ms": 1234}
4. Then run: python research/occ2opt_eval/driver.py --split holdout --mode scripted --batch 10 --dialect sym-v2-d2-r1 --live --stage score --confirm-live --run-dir C:/Users/openclawuser/optimus-occ2opt-20261016/research/occ2opt_eval/runs/live-holdout-sym-v2

Note: use the venv python for the score stage so wire-token metrics
are recorded (the local Qwen tokenizer); any python works otherwise.

Note: 0 items were skipped at this stage (speaker protocol faults or lost
children); see state-speaker.json.
