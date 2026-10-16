# Live listener stage - parent instructions

Run dir: C:/Users/openclawuser/optimus-occ2opt-20261016/research/occ2opt_eval/runs/live-dev-scripted-20261016T142348Z

1. Spawn two DGX GLM 5.3 listener children (eval-lst-1, eval-lst-2).
2. For every row in prompts/listener.jsonl, send the prompt text and
   request an explicit reply to the parent.
3. Save every reply as ONE line in replies/listener.jsonl:
   {"child": "eval-lst-1", "item": "<item id>", "reply": "<full reply text>",
    "sent_utc": "...", "received_utc": "...", "latency_ms": 1234}
4. Then run: python research/occ2opt_eval/driver.py --split dev --mode scripted --batch 10 --live --stage score --confirm-live

Note: 0 items were skipped at this stage (speaker protocol faults or lost
children); see state-speaker.json.
