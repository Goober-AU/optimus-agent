# Experiment Ledger

1. Baseline reproduction of PR #167's 16 env failures: reproduced; classified
   as environment (uv PATH, dill-less python, python3 alias). Fixed in the
   runner via prerequisite checks + explicit env resolution; suites then pass.
2. test_repl.py flake under load: reproduced (667ms > 500ms startup; group-reap
   race), passed in isolation. Fixed by load-tolerant budget + reap barrier —
   NOT by weakening the assertion intent.
3. Stash incident: a worker ran git stash for warning-diffing (forbidden by
   campaign rules); all 31 files restored byte-exact by the worker, verified
   by two other workers' markers; stash retained until PR assembly, then
   dropped after final tree verification.
4. core002 refinement test failed in full serial run, passed isolated: root
   cause was uv absent from PATH (env-dependent fixture), not test pollution;
   full serial run green once uv on PATH.
5. pi-ai golden failure reproduced on PRISTINE base worktree: proved the
   test-authoring bug predated the campaign; fixed the loop, not the product.
6. OCC-2 e2e 'delivery missing': four stacked test-design causes (transcript
   shape, cross-worker fromRelationship, explicit-stop resume semantics);
   the negotiated encode/decode/delivery itself was correct end-to-end —
   evidence the PR #166 seam works.
