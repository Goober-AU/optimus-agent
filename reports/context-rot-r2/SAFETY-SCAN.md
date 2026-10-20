# SAFETY-SCAN — final diff safety scan (secrets + junk)

Worker W87, 2026-10-20. Worktree `C:/Users/openclawuser/optimus-rot2-w87-safety`,
branch `rot2/w87-safety` at `ac5edde2b` (= integration tip; merge-base
`6000d6bc1` = `origin/main`, 177 commits in `origin/main..HEAD`).

Scope: the full pre-push diff `git diff origin/main...HEAD` (367 files: 346
added, 21 modified; 174,141 diff lines; 15.6 MB). No repo-wide greps; every
finding below comes from the diff itself plus read-only `git show` of files
that appear in the diff. Offline.

Method, item by item:

1. Secrets scan: `git diff origin/main...HEAD` piped through
   `grep -inE "(api[_-]?key|secret|password|token|bearer|credential|nvidia|launchpad|pomerium)"`
   -> 42,299 hits, every hit classified below. Complemented by a
   high-entropy/value-shape sweep over the same diff (prefixes `sk-`,
   `nvapi-`, `ghp_/gho_/github_pat_`, `glpat-`, `xox*`, `AKIA`, `AIza`,
   JWT `eyJ...`, 32/40-hex, 60+ base64, `-----BEGIN PRIVATE KEY-----`,
   `bearer <literal>`, `Authorization` with a literal, `@emails`).
2. Absolute-path scan: forward, backslash and git-bash forms of
   `C:/Users/...` plus `openclawuser`, `optimus-rust-toolchain`,
   `rot2-shared-target`, `Optimus-Assistant` over the diff.
3. Large-file scan: per-file blob size at HEAD (`git ls-tree -r -l`) and
   per-file diff-hunk bytes, cross-checked against the 5 LFS pointer files.
4. Junk scan: `.DS_Store`, `Thumbs.db`, `*.log`, `node_modules`, `target/`,
   backup names (`.bak`, `~`, `.tmp`, `.orig`, `.rej`, `.swp`), editor dirs.

## Verdict summary

| Class | Verdict |
|---|---|
| Credential/key values (any kind) | PASS — none present |
| Private hostname | SHOULD-FIX — one private LaunchPad endpoint URL in 12 files (no credentials; auth-gated endpoint) |
| Machine-local credential-resolver defaults | PASS — all resolvers are env-only and fail closed (W73-S7 fix verified in code) |
| Hardcoded `C:/Users/...` in shipped code | PASS — zero in `crates/` and `resources/`; all hits are research drivers, data provenance and report prose |
| Files > 1 MB not covered by LFS | SHOULD-FIX — one 1.4 MB plain-blob raw-call log committed as `.bak` |
| Junk files | SHOULD-FIX — three `.bak` files; no other junk classes present |

No blocking secret leak. The SHOULD-FIX list is small and mechanical; all
items are integrator-applied (see Fix list).

## Item 1 — secrets scan (42,299 grep hits, all classified)

| Word | Hits | Classification |
|---|---|---|
| `token` | 42,074 | FALSE POSITIVE — token counting and usage fields (`promptTokens`, `reserveTokens`, `estimate_context_tokens`, token budgets) in benchmark telemetry, drivers and compaction code. Breakdown: 38,709 research data/results, 2,419 research drivers/docs, 708 crates code/tests, 225 reports, 6 resources docs, 7 top-level. No literal token values anywhere. |
| `pomerium` | 60 | FALSE POSITIVE — env-var names (`ROT2_LIVE_POMERIUM`, `ROT2_POMERIUM_RESOLVER_SCRIPT`, `R2E2_POMERIUM`), header names (`X-Pomerium-Authorization`), and prose. No JWT values. |
| `api_key` / `api-key` | 51 | FALSE POSITIVE — env-var indirection (`ROT2_LIVE_API_KEY`) and test fixture keys (`turn-flow-fixture-key`, `rot2-wave2-defect-fixture-key`, `unused`). No real key values. |
| `credential` | 49 | FALSE POSITIVE — docstrings, audit prose, resolver env names. The two audit reports quote the old resolver paths with the `C:/Users/openclawuser` prefix elided (`.../profile/agent/credentials/Get-K3PomeriumJwt.ps1`, `.../glm53-resolvers/cd5ef6501b924449917676c6ddb998a8.ps1`); no values, elided prefix. Minor machine-layout reveal in prose only. |
| `bearer` | 18 | FALSE POSITIVE — header construction from variables in every case. |
| `launchpad` | 17 | REAL (11 of them, see below) — 6 other hits are prose naming the environment ("DGX K3 LaunchPad" in reports/READMEs); the service name is public, low risk. |
| `nvidia` | 12 | REAL — all 12 are the private endpoint URL instances (same finding as `launchpad`). |
| `password` | 11 | FALSE POSITIVE — test fixture dialogue ("never store raw passwords") and the PII regex in `typed_state.rs`/`typed_mirror.py`. |
| `secret` | 7 | FALSE POSITIVE — the same PII regex, and prose. |

### REAL finding S1 — private LaunchPad endpoint URL (SHOULD-FIX)

`https://a8349d0f-f602-991e-52c5-963eb2f2e949-in-docker-k3.apps.launchpad.nvidia.com/v1`
—a private, Pomerium-authenticated hostname (instance GUID) — appears in
12 files on added lines:

| File | Line | Role |
|---|---|---|
| `research/context_rot_r2/live/run_r2e2.py` | 55-58 | hardcoded `BASE_URL` default (env `R2E2_GLM_BASE` overrides) |
| `research/context_rot_r2/contextbench/live/harness_client.py` | 29-32 | hardcoded `DEFAULT_BASE_URL` (default arg of `run_cycle`) |
| `research/context_rot_r2/compactbench_adapter/live/run-config.json` | 6 | committed run config `base_url` |
| `research/context_rot_r2/contextbench/live/run_config.json` | 5 | committed run config `baseUrl` |
| `research/context_rot_r2/live/results.json` | 6 | recorded run provenance `baseUrl` |
| `research/context_rot_r2/live/e3/results.json` | 6 | recorded run provenance `baseUrl` |
| `research/context_rot_r2/live/e3adv/results.json` | 6 | recorded run provenance `baseUrl` |
| `research/context_rot_r2/live/e5/live/e3-typed-results.json` | 6 | recorded run provenance `baseUrl` |
| `research/context_rot_r2/live/e5/live/e3adv-typed-c1-results.json` | 6 | recorded run provenance `baseUrl` |
| `research/context_rot_r2/live/e5/live-d29/e3adv-typed-c1-results.json` | 6 | recorded run provenance `baseUrl` |
| `research/context_rot_r2/live/e5/live-d29/e3adv-results.json.bak` | 6 | same, in a junk `.bak` copy |
| `research/context_rot_r2/live/e5/rm/results.json` | 4 | recorded run provenance `baseUrl` |

Assessment: the endpoint requires Bearer + Pomerium JWT, so the URL alone
grants nothing, but it reveals the private instance GUID and machine
layout, and the repo's own REVIEW-AUDIT already flagged these exact
references. Fix: make the two driver defaults env-required (fail closed) or
placeholder; template the two run-config JSONs. The `results.json`
`baseUrl` fields are measurement provenance — scrubbing them breaks
run-integrity claims, so the integrator decides (accept as provenance or
scrub with a note in the PR body).

### Credential-resolver defaults — PASS (verified fixed)

- `research/context_rot_r2/live/run_r2e2.py:494-505` — `R2E2_GLM_RESOLVER` /
  `R2E2_POMERIUM` env-only, raise when unset, no machine default.
- `research/context_rot_r2/contextbench/live/harness_client.py:36-52` —
  `ROT2_GLM_RESOLVER_SCRIPT` / `ROT2_POMERIUM_RESOLVER_SCRIPT` env-only,
  fail closed ("a fresh checkout has no credential store").
- `crates/pi-coding-agent/src/bin/compaction_harness.rs:461-475` —
  `live_env()` requires `ROT2_LIVE_BASE_URL` / `ROT2_LIVE_API_KEY` /
  `ROT2_LIVE_POMERIUM`; empty or unset errors before any state is touched.
- No `CRED_*` constants or `.ps1` literals remain in any code file; the
  only references are the elided quotes in REVIEW-AUDIT.md / REVIEWER-PASS.md.

### Value-shape sweep — all false positives

`sk-` matches were substrings of `task-...` names (`sk-state-...`,
`sk-e-cycle1-...`); hex40/hex32 were pinned-commit SHAs and dataset
instance ids; base64 60+ were `oid sha256:` LFS pointers and a parquet
sha256; zero `eyJ` JWTs, zero `BEGIN PRIVATE KEY`, zero literal
`Authorization` values, zero emails. The one hex32
(`cd5ef6501b924449917676c6ddb998a8.ps1`) is a resolver script filename
quoted (elided path) in REVIEWER-PASS.md — no secret.

## Item 2 — absolute-path scan (141 hits, all on added lines)

Shipped code (`crates/`, `resources/`): ZERO `C:/Users/...` paths. All 141
hits live under `research/` and `reports/`:

| Kind | Files | Assessment |
|---|---|---|
| Report prose (worktree/target/venv provenance) | ~10 report files, 40+ hits | Intentional environment documentation; low risk |
| Research-driver machine-local defaults (non-credential) | see table below | Machine-layout reveal only; brief excludes research drivers from the flag; repo's own REVIEW-AUDIT recommends parameterizing |
| Data provenance fields (`sessionDir`, `logFile`, `manifest`, `suite`) | 69 `live/raw/*-summarizer/*.jsonl`, `dev_smoke20.json`, `typecompact_repro*.json` | Temp-session/refs paths inside telemetry records; data, low risk |

Driver defaults (all env- or CLI-overridable, none are credentials):

| File | Line | Default |
|---|---|---|
| `research/context_rot_r2/compactbench_adapter/optimus_compactor.py` | 69 | `DEFAULT_HARNESS_BIN = C:/Users/openclawuser/rot2-shared-target/debug/compaction-harness.exe` (override `HARNESS`/`ROT2_HARNESS_BIN`) |
| `research/context_rot_r2/compactbench_adapter/probe_harness_behavior.py` | 60 | same path as `or` fallback |
| `research/context_rot_r2/contextbench/live/run_live.py` | 37 | `REFS_ROOT_DEFAULT = C:/Users/openclawuser/optimus-rot2-w5-research/refs/ContextBench` (`--refs-root` overrides) |
| `research/context_rot_r2/orchestrate.py` | 20, 41-43 | `ROT2_REPO`, `CARGO_TARGET_DIR`, `RUSTUP_HOME`, `CARGO_HOME` via `setdefault` |
| `research/context_rot_r2/incremental/driver.py` | 26-29 | `CARGO_TARGET_DIR`, `RUSTUP_HOME`, `CARGO_HOME`, `TOOLCHAIN_BIN` hard constants |
| `research/context_rot_r2/compactbench_adapter/live/run-config.json` | 13, 15, 57 | committed config with `rot2-w26-target` binary/target paths |

None of these resolves credentials; they reveal only the username and
directory layout of the research machine. The committed run-config (a
config, not a driver) is the one REVIEW-AUDIT explicitly recommended
templating.

## Item 3 — large-file scan

The 5 known LFS raw-call logs are correct 132-byte pointers in the diff
(payloads 0.2-7.8 MB via `git-lfs.github.com` oids):
`live/raw-calls.jsonl`, `live/e3/raw-calls.jsonl`, `live/e3adv/raw-calls.jsonl`,
`live/e5/live/e3-typed-raw-calls.jsonl`, `compactbench_adapter/live/raw-calls.jsonl`.

Top diff contributors by hunk bytes:

| Bytes | File | Note |
|---|---|---|
| 1,402,281 | `research/context_rot_r2/live/e5/live-d29/e3adv-raw-calls.jsonl.bak` | REAL FINDING — plain blob, not LFS, `.bak` name |
| 784,383 | `research/context_rot_r2/live/e5/rm/raw-calls.jsonl` | raw-call log, plain blob, not covered by an LFS pattern |
| 694,011 | `research/context_rot_r2/live/results.json` | results JSON |
| 376,534 | `research/context_rot_r2/live/remeasure/results.json` | results JSON |
| 373,216 | `research/context_rot_r2/live/e5/live-d29/e3adv-typed-c1-raw-calls.jsonl` | raw-call log, plain blob, no LFS pattern |
| 356,201 | `research/context_rot_r2/live/r2-e2-hard.results-partial.json` | results JSON |
| 350,425 | `research/context_rot_r2/live/e5/live/e3adv-typed-raw-calls.jsonl` | raw-call log, plain blob, no LFS pattern |
| 323,103 | `research/context_rot_r2/compactbench_adapter/results/w12/elite_practice/built-in-hierarchical-summary.jsonl` | benchmark arm output |
| ... | (8 more 300 KB arm outputs) | benchmark arm outputs |
| 246,600 | `research/context_rot_r2/contextbench/live/raw-calls.jsonl` | raw-call log, plain blob, no LFS pattern |
| 194,855 | `crates/pi-coding-agent/src/core/compaction/typed_state.rs` | source, expected |

REAL FINDING L1 (SHOULD-FIX): exactly one file exceeds 1 MB in the diff and
is not an LFS pointer — `e3adv-raw-calls.jsonl.bak` (1,402,004-byte blob;
1.4 MB of diff). It is a raw-call telemetry log (124 records, full request
and response content of synthetic benchmark scenarios; verified: no
Authorization material, no URLs, no machine paths inside). It duplicates
the LFS-covered category but was committed as a plain blob under a `.bak`
name. Fix: drop it, or convert to an LFS pointer like its siblings.

Note: `crates/.../agent_session.rs` is 1.2 MB at HEAD but contributes under
200 KB of diff; it is modified source already public at origin/main. Not a
finding. Four more raw-call logs (785 KB, 373 KB, 350 KB, 246 KB rows
above) sit just under 1 MB as plain blobs and are not covered by any
`.gitattributes` LFS pattern; same treatment optional for consistency.

## Item 4 — junk scan

- `.DS_Store`, `Thumbs.db`, `*.log`, `node_modules/**`, `target/**`,
  `*.pyc`, `__pycache__`, `.idea`, `.vscode`, `*.orig/rej/swp/tmp`, `~$*`:
  ZERO hits in the 367-file change set.
- `.bak` files: three, all SHOULD-FIX junk:
  - `research/context_rot_r2/live/e5/live-d29/e3adv-raw-calls.jsonl.bak` (1.4 MB, also L1)
  - `research/context_rot_r2/live/e5/live-d29/e3adv-results.json.bak` (141,577 B; duplicates the results record incl. the private `baseUrl`)
  - `research/context_rot_r2/live/e5/live-d29/r2-e3adv-c1.scenario.json.bak` (62,216 B; synthetic scenario copy)
- `research/context_rot_r2/contextbench/runs/.gitignore` (`*` + `!.gitignore`) is an intentional keep-dir marker, not junk.

## Fix list (integrator applies)

1. S1 (private hostname): in `run_r2e2.py` and `harness_client.py`, make the
   base URL env-required (fail closed) or a placeholder; template
   `compactbench_adapter/live/run-config.json` and
   `contextbench/live/run_config.json`. Decide on the 7 `results.json`
   provenance `baseUrl` fields (scrub + PR-body note, or accept).
2. L1/J1: delete `live/e5/live-d29/e3adv-raw-calls.jsonl.bak` (or LFS it).
3. J2: delete `live/e5/live-d29/e3adv-results.json.bak` and
   `live/e5/live-d29/r2-e3adv-c1.scenario.json.bak`.
4. Optional (repo-consistency, own REVIEW-AUDIT recommendation):
   parameterize the remaining machine-local driver defaults
   (`optimus_compactor.py` HARNESS default, `run_live.py` REFS_ROOT_DEFAULT,
   `orchestrate.py`/`incremental/driver.py` toolchain paths) and consider
   LFS patterns for the four plain raw-call logs listed in Item 3.

None of these blocks the push from a secrets standpoint; items 1-3 are
recommended before the public push, item 4 is optional.
