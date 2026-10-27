"""Offline injection-lineage regression using pinned native method bodies.

No SGLang model or CUDA runtime is loaded. Set OCC3_SGLANG_SOURCE to the
unpatched, pinned SGLang checkout.
"""
from __future__ import annotations
import ast
import contextlib
import dataclasses
import hashlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from occ3_sglang import latent_worker as worker
from occ3_sglang.attachment_contract import ConsumeWorkerDescriptor
from occ3_sglang.latent_types import RuntimeProfile, RequestSlice

ROOT = Path(__file__).resolve().parents[3]
SG = Path(os.environ.get("OCC3_SGLANG_SOURCE", "")) / "python/sglang/srt"
NS = types.SimpleNamespace

def source_method(text, owner, name, scope):
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner)
    node = next(n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(SG / owner), "exec"), scope)
    return scope[name]


EXTRA_SOURCE_PINS = {'model_executor/runner/eager_runner.py': 'a04d7344319eccbeacefad3f60e559c716f412e680ed0f712645ef149238a6b5', 'model_executor/cuda_graph_buffer_registry.py': '839a1a077a6c7ec50148d4a6481e37880baa8947432db7ac09574b7e601f0d78'}


def patched_source(path):
    text = (SG / path).read_text(encoding="utf-8")
    if path in EXTRA_SOURCE_PINS:
        assert hashlib.sha256(text.encode()).hexdigest() == EXTRA_SOURCE_PINS[path]
    for manifest_path in (ROOT / "extensions/sglang-occ3/patches").glob("*.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            if entry["path"] == path:
                assert hashlib.sha256(text.encode()).hexdigest() == entry["before_sha256"]
                for replacement in entry["replacements"]:
                    assert text.count(replacement["before"]) == 1
                    text = text.replace(replacement["before"], replacement["after"], 1)
                assert hashlib.sha256(text.encode()).hexdigest() == entry["after_sha256"]
    return text


def profile(**changes):
    return RuntimeProfile("epoch", 9, "model-hash", "glm", **changes)


def consume():
    return ConsumeWorkerDescriptor(
        attempt_id="consume", reservation_id="reserve-consume", native_rid="receiver-rid",
        principal_digest="a" * 64, attachment_digest="e" * 64, receiver_session_id="receiver",
        message_id="message", consume_nonce="nonce", opaque_handle="synthetic-state",
        state_generation=123, runtime_generation=9, source_capture_commit_id="commit",
        runtime_epoch="epoch", model_fingerprint="model-hash", method_id=profile().method,
        tp_layout_digest="b" * 64, expected_rank_count=8, expected_prefix_tokens=8,
        hidden_dim=6144, dtype="bfloat16", deadline=900.0)


class ShapeOnlyBuffer:
    """No tensor data; records that the actual hook reached its copy call."""
    is_cuda = True
    dtype = "bfloat16"
    def __init__(self, rows=8, copies=None):
        self.shape = (rows, 6144)
        self.copies = copies if copies is not None else []
    def __getitem__(self, value):
        return self
    def copy_(self, value):
        self.copies.append(True)
    def record_stream(self, stream):
        pass


@dataclasses.dataclass
class ShapeOnlyBatch:
    occ3_context: object
    occ3_force_eager: bool = True
    occ3_effective_embeds: object = None
    occ3_capture_rows: object = None
    batch_size: int = 1
    input_ids: object = None
    positions: object = None
    input_embeds: object = None
    forward_mode: object = None
    attn_cp_metadata: object = None
    global_num_tokens_cpu: object = None
    token_indices_to_pool: object = None
    def needs_forward_metadata_init(self):
        return False


def observe_injection_span(no_copy, outcome):
    if not (SG / "model_executor/runner/eager_runner.py").is_file():
        pytest.skip("Pinned SGLang source absent; set OCC3_SGLANG_SOURCE")
    descriptor = consume()
    item = RequestSlice(descriptor, 0, 8, 8 if outcome == "no_prefix" else 0, 0, True)
    mode = NS(is_extend=lambda **kw: True, is_target_verify=lambda: False,
              is_split_prefill=lambda: False, is_decode=lambda: False,
              is_cuda_graph=lambda: False)
    batch = ShapeOnlyBatch((item,), input_ids=NS(shape=(8,)), positions=object(), forward_mode=mode)
    record = NS(prefix=ShapeOnlyBuffer(), events=[], inflight=0, injected_positions=set(),
                injection_commit_id=None, capture_commit_id="commit", state="leased")
    stream = NS(wait_event=lambda event: None)
    runtime = NS(poisoned=False, resolve=lambda descriptor: record,
                 preflight=lambda phase, fn, **kw: fn(), profile=profile(), device="synthetic",
                 event=lambda: object(), torch=NS(cuda=NS(current_stream=lambda device: stream)))
    layer_module = types.ModuleType("sglang.srt.layers.layer_boundary")
    layer_module.get_attn_tp_context = lambda: NS(input_scattered=False)
    seen = []
    copies = []
    def model_body(ids, positions, actual_batch, *args):
        seen.append(actual_batch)
        if outcome == "before_copy":
            raise RuntimeError("synthetic forward failure")
        worker.inject_target_embeddings(actual_batch, ShapeOnlyBuffer(copies=copies))
        if outcome == "after_copy":
            raise RuntimeError("synthetic forward failure")
        return object()
    native_forward = source_method(patched_source("models/deepseek_v2.py"),
        "DeepseekV2ForCausalLM", "forward", {
            "get_attn_tp_context": lambda: NS(maybe_input_scattered=lambda fb: contextlib.nullcontext())})
    model = NS(model=model_body, capture_aux_hidden_states=False,
               pp_group=NS(is_last_rank=True), lm_head=None,
               logits_processor=lambda *args: NS())
    model.forward = types.MethodType(native_forward, model)
    scope = dict(contextlib=contextlib, replace=dataclasses.replace,
                 envs=NS(SGLANG_EAGER_INPUT_NO_COPY=NS(get=lambda: no_copy)),
                 is_cp_active=lambda fb: False,
                 device_timer_ctx=lambda *args: contextlib.nullcontext(), _is_hip=False)
    eager_source = patched_source("model_executor/runner/eager_runner.py")
    extract_source = patched_source("model_executor/cuda_graph_buffer_registry.py")
    extract = source_method(extract_source, "CudaGraphBufferRegistry", "extract_buffer", {})
    registry = NS(_slots={}, fill_from=lambda *args, **kwargs: None)
    registry.extract_buffer = types.MethodType(extract, registry)
    load = source_method(eager_source, "EagerRunner", "load_batch", scope)
    execute = source_method(eager_source, "EagerRunner", "_execute_extend", scope)
    eager = NS(enable_pdmux=False, _eager_registry=registry)
    eager.load_batch = types.MethodType(load, eager)
    runner = NS(is_draft_worker=False, model=model,
                _extend_forward_kwargs=lambda *args: {}, prefill_cuda_graph_runner=None,
                device_timer=None, attn_backend=object(), device="cuda", hisparse_coordinator=None,
                decode_cuda_graph_runner=None, pp_group=NS(is_last_rank=True),
                _prepare_eager_forward_batch=lambda fb: None,
                _maybe_execute_deferred_mamba_cow_and_clear=lambda fb: None)
    eager.model_runner = runner
    runner.eager_runner = NS(execute=lambda fb, **kwargs: execute(eager, fb, **kwargs))
    scope.update(has_forward_context=lambda: True, get_global_dwdp_manager=lambda: None,
                 EagerRunner=type("UnusedEagerType", (), {}), ModelRunnerOutput=lambda **kw: NS(**kw))
    forward = source_method(patched_source("model_executor/model_runner.py"), "ModelRunner", "_forward_raw", scope)
    with patch.object(worker, "_RUNTIME", runtime), patch.dict(sys.modules, {layer_module.__name__: layer_module}), \
            patch.object(worker, "record_forward_start", lambda *args: None), \
            patch.object(worker, "record_forward_completion", lambda *args: None):
        if outcome in ("before_copy", "after_copy"):
            with pytest.raises(RuntimeError, match="synthetic forward failure"):
                forward(runner, batch, None)
        else:
            forward(runner, batch, None)
        assert copies == ([True] if outcome in ("success", "after_copy") else [])
        assert seen[0] is not batch
        assert batch.occ3_effective_embeds is None
        observed = {"injected_count": len(record.injected_positions),
                    "commit_present": record.injection_commit_id is not None}
        if outcome == "success":
            assert seen[0].occ3_effective_embeds is None
            event_count, commit = len(record.events), record.injection_commit_id
            worker.mark_injection(seen[0])  # A repeated marker must be a no-op.
            assert len(record.events) == event_count
            batch.occ3_context = (RequestSlice(descriptor, 0, 8, 8, 0, True),)
            forward(runner, batch, None)  # A later non-prefix chunk cannot reuse the span.
            assert seen[1] is not seen[0] and seen[1].occ3_effective_embeds is None
            assert len(record.injected_positions) == 8 and record.injection_commit_id == commit
            assert copies == [True]
    return observed


@pytest.mark.parametrize("no_copy", [False, True])
@pytest.mark.parametrize("outcome", ["success", "before_copy", "after_copy", "no_prefix"])
def test_native_eager_forward_preserves_injection_journal(no_copy, outcome):
    observed = observe_injection_span(no_copy, outcome)
    assert observed == {"injected_count": 8 if outcome == "success" else 0,
                        "commit_present": outcome == "success"}
