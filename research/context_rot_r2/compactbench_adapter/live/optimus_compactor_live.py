"""CUSTOM-OPTIMUS LIVE-MODEL wrapper for the W12 CompactBench adapter (W26).

Loads the committed W12 adapter module unchanged (the shared checkout stays
read-only per the campaign file-map rule) and flips only its ``_SUMMARIZER_MODE``
module global to ``"live"``: every harness request then selects the
env-configured GLM HTTP summarizer in the Rust binary instead of the
deterministic lossy fixture. Everything else - the delta-append session
handling, the cut-point ladder, the ledger mapping, the controls - is the
W12 adapter byte for byte.

Selected by ``run_live.py`` as ``<this file>:<ClassName>``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ADAPTER = Path(__file__).resolve().parent.parent / "optimus_compactor.py"

_spec = importlib.util.spec_from_file_location("optimus_compactor_w12_base", _ADAPTER)
assert _spec is not None and _spec.loader is not None
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
_base._SUMMARIZER_MODE = "live"

OptimusRustCompactor = _base.OptimusRustCompactor
OptimusRustCompactorTaskState = _base.OptimusRustCompactorTaskState
OptimusRustCompactorPlain = _base.OptimusRustCompactorPlain
EchoFirstNCompactor = _base.EchoFirstNCompactor
FullTranscriptOracleCompactor = _base.FullTranscriptOracleCompactor

__all__ = [
    "OptimusRustCompactor",
    "OptimusRustCompactorTaskState",
    "OptimusRustCompactorPlain",
    "EchoFirstNCompactor",
    "FullTranscriptOracleCompactor",
]
