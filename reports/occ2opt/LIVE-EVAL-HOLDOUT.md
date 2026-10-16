# OCC-2 live eval — holdout split (live-holdout-sym-v2, DGX GLM 5.3)

Run id: live-holdout-sym-v2-d2-r1-scripted-20261016T175430Z. Sealed 54-item split (msgs_corpus2_holdout.json),
opened only after all tuning was frozen (card clarified + dev run b complete). Fresh children: speaker
eval-spk-h1 (sub-ff03fc56, retired), listener eval-lst-h1 (sub-369d3681).

## Speaker stage — COMPLETE, PERFECT
- 54/54 live replies, 54/54 strict fixed-point decode clean, 0 faults, 0 child_lost.
- Kind coverage: TASK 8, PROGRESS 28, EVIDENCE 5, RESULT 8, ACK 3, CORRECTION 1, CANCELLED 1 (never-compress
  kinds all present). All 12 canaries logged.

## Listener stage — COMPLETE, PERFECT
- 54/54 live replies from fresh child eval-lst-h1; driver scorer: QA 162/162 (accuracy 1.0,
  incl-protocol 1.0), comprehension 54/54 exact-rebuild.

## Score stage — ALL GATES GREEN (22 checks, 0 failures)
- speaker fidelity 54/54 (rate 1.0), protocol faults 0, child_lost 0.
- canaries 12/12 accuracy 1.0, drift FALSE (per-batch all 1.0).
- TOKENS (qwen2.5-0.5b counter): wire 87.52 tok/item vs OCC-1 144.30 -> **0.6065x** (-39.4%)
  vs dev split's 0.6143x; chars 187.67 vs 322.98. Matches the offline holdout preview (0.607x).
- codebook amortization: 3466 ch / 1187 tok, 6 carrier items (6 injections, emission rate 1.0),
  amortized 131.89 tok/item at batch 10.

## Notes
- Incident log: two dry-run contamination events caught and recovered with full evidence archiving
  (see EXPERIMENT-LEDGER.md); all live artifacts verified byte-identical post-recovery.
