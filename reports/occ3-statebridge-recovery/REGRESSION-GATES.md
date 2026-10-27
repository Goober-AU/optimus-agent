# Regression gates

## Test environments actually used

| purpose | command | result |
|---|---|---|
| extension unit tests (default) | `cd extensions/sglang-occ3 && .venv/Scripts/python.exe -m pytest tests/ -q` | see below |
| source-attested tests | same with `OCC3_SGLANG_SOURCE=C:/Users/openclawuser/occ3-latent-validation/references/sglang` (pinned commit `e00930c5489053f26d86b179cee0d087f846acbb`) | 53/53 in `test_chat_attachment.py`; 22/22 in `test_predictor_alignment.py` |
| Rust workspace | `cargo check --locked --workspace --all-targets`, `bash scripts/check.sh` | NOT RUN: no cargo/rustc on this host. Must run in CI or on a machine with the pinned toolchain. |

`OCC3_SGLANG_SOURCE` must be set per file or per session: `test_native_research_regressions.py`
installs `sglang` stubs at import time and pollutes `test_chat_attachment.py` in a shared
session (53 errors, reproduced on files this branch does not touch).

## New tests in this branch

| file | covers | count |
|---|---|---|
| `tests/test_prefix_placement.py` | marker resolution, refusal on duplicate/zero/unstable markers, offset arithmetic, reservation at the marker row, legacy prepend parity, injection rows, descriptor offset mismatch | 15 |
| `tests/test_predictor_alignment.py` | pinned-source attestation of prefill/verify pairing, commit position arithmetic, ring slot math | 22 |
| `tests/test_span_audit.py` | exact-id span classification, marker absence, discontiguity, bounded summary, performance bound | 46 |
| `tests/test_semantic_leakage.py` | private fact / answer / nonce / handle never in the ordinary payload; fallback cannot pass | see file |

## Red-first discipline

Every behaviour change in this branch has a test that failed before the change:

- `test_admit_request_reserves_rows_at_the_marker_offset` and
  `test_injection_writes_only_the_reserved_marker_rows` failed with
  `TypeError: ConsumeWorkerDescriptor.__init__() got an unexpected keyword argument 'prefix_offset'`
  and the legacy assertion `(0, 4) == (4, 4)` before the change.
- `test_admit_request_keeps_legacy_prepend_at_offset_zero` pins the byte-identical legacy
  behaviour so an ordinary receiver request cannot change shape.
- The injection content tests in `test_latent_worker.py` were re-run unchanged apart from one
  equivalent-slice assertion (`hidden_states[:count]` is now `hidden_states[local:local+count]`,
  which is the same slice at offset 0).

## Full-suite status at this commit

Before the conftest restore fix: `1 failed, 1732 passed, 358 skipped, 2 xfailed, 77 subtests`
in 168 s. The failure was `test_native_research_regressions.py::test_only_accepted_verify_rows_enter_the_retained_ring`
with `AttributeError: module 'torch' has no attribute 'arange'`, and it passed when that file
ran alone.

Root cause: `tests/conftest.py::_install_stubs` replaced `sys.modules["torch"]` (and the
`sglang` namespace) by assignment inside the `stub_http_server` fixture with no teardown, so
every later test in the session saw a stub `torch`. `_install_stubs` never restores what it
overwrites.

Fix in this branch: the fixture now saves and restores exactly the modules `_install_stubs`
overwrites. After the fix the full extension suite is green:

```
1986 passed, 105 skipped, 2 xfailed, 77 subtests passed in 175.65s
```

The skip count also drops from 358 to 105 because the restored real `torch` and `sglang`
namespace no longer force skip paths. No production code changed for this; it is a test
isolation defect, recorded as BUG-LEDGER.md RC-7.
