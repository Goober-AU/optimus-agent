# Performance Scorecard

The campaign's charter required measured evidence before retaining any
optimisation. No new optimisation was retained in this PR; every candidate
was either (a) a correctness fix whose benefit is functional, (b) rejected
for lack of comparative evidence, or (c) deferred to the reproducible
harness shipped in scripts/full_harness_validation.py --performance.

| Area | Before | After | Verdict |
|------|--------|-------|---------|
| Bash spawn/containment | taskkill /T + pid journal (leaks detached descendants) | job object kill-on-close | Correctness fix; tree-kill now guaranteed. No latency regression observed in suite runtimes. |
| Streaming clones (PR #167) | — | unchanged | Verified no regression: 899/899 pi-ai lib incl. clone-elision partial tests |
| ExecutionScope status() | O(all requests+tools ever) | O(live) via prune_settled | Bounded growth test: 16+ entries -> 2 after 8 runs |
| Kernel output path | silent ~1.9 MB drop on 2 MB print | capped + notice | Correctness fix |
| Memory retrieval | — | unchanged | 175/175 lib tests; cached-vs-reference parity tests still pass |

Benchmarks NOT run in this campaign (honest): LongMemEval/LoCoMo/PersonaMem
quality suites (need authorised data + live models); perf examples
(12 inventoried by the runner: memory-search, compaction replay x2, disk
write, session loop, memory bench driver). Reproduce with:
  python scripts/full_harness_validation.py --performance
