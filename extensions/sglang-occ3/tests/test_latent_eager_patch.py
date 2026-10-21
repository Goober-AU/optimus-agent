"""Exact pinned graph/dispatcher methods with control-flow doubles only.

These tests do not initialize SGLang, CUDA graphs, or distributed groups.
"""
from __future__ import annotations

import ast
import contextlib
from types import SimpleNamespace as NS

import pytest

from test_latent_capture_patch import (FixtureLatentError, IndexFixture, boundary,
    deny_backend_import, method, pinned, source_node)

GRAPHS = [
    ("model_executor/runner/decode_cuda_graph_runner.py", "DecodeCudaGraphRunner"),
    ("model_executor/runner/prefill_cuda_graph_runner.py", "PrefillCudaGraphRunner"),
    ("speculative/eagle_draft_cuda_graph_runner.py", "EAGLEDraftCudaGraphRunner"),
    ("speculative/eagle_draft_extend_cuda_graph_runner.py", "EAGLEDraftExtendCudaGraphRunner"),
]


class NoMetadata:
    def __init__(self, eager, context):
        self.occ3_force_eager, self.occ3_context = eager, context

    def __getattr__(self, name):
        raise AssertionError(f"Graph eligibility touched metadata first: {name}")


@pytest.mark.parametrize("path,owner", GRAPHS)
@pytest.mark.parametrize("eager,context", [(True, None), (False, ("source",)), (True, ("consume",))])
def test_four_graph_guards_precede_all_metadata(pinned, path, owner, eager, context):
    fn = method(pinned, path, owner, "can_run_graph")
    assert fn(NoMetadata(False, None), NoMetadata(eager, context)) is False


def ordinary_graph_fixture(owner, *, size=1, width=6, supported=True):
    batch = NS(replace_embeds=None, input_embeds=None, batch_size=size,
               spec_info=NS(num_tokens_per_req=width), seq_lens=IndexFixture([5] * size),
               global_num_tokens_cpu=None, input_ids=IndexFixture([3] * size),
               extend_prefix_lens_cpu=[0] * size, return_logprob=False,
               capture_hidden_mode="fixture", forward_mode=NS(is_target_verify=lambda: False))
    runner = NS(captured_req_width=6, max_bs=4, ragged_verify_mode=False,
                require_mlp_tp_gather=False, disable_padding=False, require_mlp_sync=False,
                enable_pdmux=False, _make_graph_key=lambda *a, **k: None,
                _resolve_lora_variant=lambda f: None, is_encoder_decoder=False,
                enable_two_batch_overlap=False,
                model_runner=NS(spec_algorithm=NS(is_ngram=lambda: False)),
                _has_inactive_dp_rank=lambda f: False, max_context_size=None,
                can_replay_locally=lambda **k: supported, enable_lora=False,
                _backend_can_run_prefill_cuda_graph=None)
    return runner, batch


@pytest.mark.parametrize("path,owner", GRAPHS)
@pytest.mark.parametrize("size,width,supported", [(1, 6, True), (9, 6, True), (1, 2, True), (1, 6, False)])
def test_ordinary_graph_predicates_match_original(pinned, monkeypatch, path, owner, size, width, supported):
    scope = {"ForwardMode": NS(MIXED=object())}
    before = method(pinned, path, owner, "can_run_graph", scope, original=True)
    after = method(pinned, path, owner, "can_run_graph", scope)
    runner, fb = ordinary_graph_fixture(owner, size=size, width=width, supported=supported)
    deny_backend_import(monkeypatch)
    assert after(runner, fb) == before(runner, fb)
    original = source_node(pinned.original[path], owner, "can_run_graph")
    patched = source_node(pinned.patched[path], owner, "can_run_graph")
    # The entire native decision is preserved after the one new early guard.
    assert [ast.dump(x) for x in patched.body[1:]] == [ast.dump(x) for x in original.body]


def forward_fixture(pinned, boundary, *, kind, attached, draft=False, fail=False):
    calls = []
    fb = NS(occ3_context=("attached",) if attached else None, occ3_force_eager=attached,
            global_num_tokens_cpu=None, token_indices_to_pool=None,
            forward_mode=NS(is_cpu_graph=lambda: False, is_cuda_graph=lambda: kind == "decode",
                            is_decode=lambda: kind == "decode", is_split_prefill=lambda: kind == "split",
                            is_extend=lambda **k: kind == "extend", is_target_verify=lambda: kind == "verify"))
    ret = object()
    def eager(*args, **kwargs):
        calls.append("eager")
        if fail:
            raise RuntimeError("fixture model failure")
        return ret
    def predicate(name):
        calls.append(name + "-predicate")
        return True
    def execute(name):
        calls.append(name + "-execute")
        return ret
    class FixtureEagerRunner:
        pass
    runner = NS(device="cuda", attn_backend=object(), hisparse_coordinator=None,
                decode_cuda_graph_runner=NS(can_run_graph=lambda f: predicate("decode"), execute=lambda *a, **k: execute("decode")),
                prefill_cuda_graph_runner=NS(can_run_graph=lambda f: predicate("prefill"), execute=lambda *a, **k: execute("prefill")),
                eager_runner=NS(execute=eager), is_draft_worker=draft, device_timer=None,
                _prepare_eager_forward_batch=lambda f: calls.append("prepare"),
                _maybe_execute_deferred_mamba_cow_and_clear=lambda f: calls.append("cow"),
                _extend_forward_kwargs=lambda *a: {}, pp_group=NS(is_last_rank=True))
    boundary.record_forward_start = lambda runner, fb: calls.append("start")
    boundary.mark_injection = lambda f: calls.append("mark")
    boundary.record_forward_completion = lambda runner, fb: calls.append("completion")
    scope = dict(contextlib=contextlib, has_forward_context=lambda: True,
                 get_global_dwdp_manager=lambda: None, EagerRunner=FixtureEagerRunner,
                 _prefill_cuda_graph_allows_context_parallel=lambda *a: True,
                 device_timer_ctx=lambda *a: contextlib.nullcontext(),
                 ModelRunnerOutput=lambda **k: NS(**k))
    fn = method(pinned, "model_executor/model_runner.py", "ModelRunner", "_forward_raw", scope)
    return NS(run=lambda: fn(runner, fb, None), calls=calls, fb=fb, ret=ret)


@pytest.mark.parametrize("kind", ["decode", "extend", "verify"])
@pytest.mark.parametrize("draft", [False, True])
def test_exact_dispatcher_attached_is_eager_before_graph_metadata(pinned, boundary, kind, draft):
    fixture = forward_fixture(pinned, boundary, kind=kind, attached=True, draft=draft)
    result = fixture.run()
    assert result.logits_output is fixture.ret and result.can_run_graph is False
    assert fixture.calls == ["prepare", "cow", "start", "eager"] + ([] if draft else ["mark"]) + ["completion"]


@pytest.mark.parametrize("kind", ["decode", "extend"])
def test_ordinary_dispatcher_still_replays_graphs(pinned, boundary, monkeypatch, kind):
    fixture = forward_fixture(pinned, boundary, kind=kind, attached=False)
    deny_backend_import(monkeypatch)
    result = fixture.run()
    assert result.can_run_graph is True
    assert fixture.calls == (["decode-predicate", "decode-execute"] if kind == "decode"
                             else ["prepare", "cow", "prefill-predicate", "prefill-execute"])


def test_failed_forward_cannot_mark_injection(pinned, boundary):
    fixture = forward_fixture(pinned, boundary, kind="extend", attached=True, fail=True)
    with pytest.raises(RuntimeError, match="fixture model failure"):
        fixture.run()
    assert fixture.calls == ["prepare", "cow", "start", "eager"]


def test_start_registration_failure_prevents_model_submission(pinned, boundary):
    fixture = forward_fixture(pinned, boundary, kind="extend", attached=True)
    def refuse_start(*args):
        raise FixtureLatentError("state")
    boundary.record_forward_start = refuse_start
    with pytest.raises(FixtureLatentError, match="state"):
        fixture.run()
    assert fixture.calls == ["prepare", "cow"]


@pytest.mark.parametrize("context,flag", [(None, True), (("source",), False)])
def test_inconsistent_metadata_fails_before_dispatch(pinned, boundary, context, flag):
    fixture = forward_fixture(pinned, boundary, kind="decode", attached=False)
    fixture.fb.occ3_context, fixture.fb.occ3_force_eager = context, flag
    with pytest.raises(FixtureLatentError, match="state"):
        fixture.run()
    assert fixture.calls == []


def test_split_prefill_is_explicitly_unsupported_not_silently_changed(pinned, boundary):
    fixture = forward_fixture(pinned, boundary, kind="split", attached=True)
    with pytest.raises(FixtureLatentError, match="profile"):
        fixture.run()
    assert fixture.calls == []


@pytest.mark.parametrize("steps", [1, 3, 5])
@pytest.mark.parametrize("graph", [False, True])
def test_original_draft_eager_metadata_and_adaptive_steps_unchanged(pinned, steps, graph):
    calls = []
    fb = NS(forward_mode=NS(is_idle=lambda: False), mark_forward_metadata_ready=lambda: calls.append("ready"))
    output = ("parents", "scores", "tokens", None)
    runner = NS(canary_manager=None)
    worker = NS(req_to_token_pool=object(), cuda_graph_runner=NS(execute=lambda f: calls.append("graph") or output),
                draft_runner=runner, topk=1, speculative_num_steps=steps,
                seed_dsa_topk_from_draft_extend=False,
                draft_attn_backend=NS(init_forward_metadata=lambda f: calls.append("metadata")),
                draft_forward=lambda f: calls.append("draft") or output,
                target_worker=object(), speculative_num_draft_tokens=steps + 1,
                tree_mask_mode=object(), device="fixture")
    batch = NS(spec_info=object())
    scope = dict(prepare_for_draft=lambda *a: (fb, graph), contextlib=contextlib,
                 build_eagle_verify_input=lambda *a, **k: "verify")
    fn = method(pinned, "speculative/eagle_worker_v2.py", "EagleDraftWorker", "draft", scope)
    assert fn(worker, batch) == "verify"
    assert worker.speculative_num_steps == steps
    assert calls == (["graph"] if graph else (["metadata", "ready", "draft"] if steps > 1 else ["draft"]))


def test_no_adaptive_global_or_capture_size_mutations(pinned):
    path = "speculative/eagle_worker_v2.py"
    before, after = ast.parse(pinned.original[path]), ast.parse(pinned.patched[path])
    changed = {("EagleDraftWorker", "_draft_extend_for_prefill"), ("EAGLEWorkerV2", "_forward_prefill_batch")}
    for cls in before.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        other = next(n for n in after.body if isinstance(n, ast.ClassDef) and n.name == cls.name)
        for member in cls.body:
            if isinstance(member, ast.FunctionDef) and (cls.name, member.name) not in changed:
                patched = next(n for n in other.body if isinstance(n, ast.FunctionDef) and n.name == member.name)
                assert ast.dump(member) == ast.dump(patched), (cls.name, member.name)
    for path, owner in GRAPHS:
        before, after = ast.parse(pinned.original[path]), ast.parse(pinned.patched[path])
        original_cls = next(n for n in before.body if isinstance(n, ast.ClassDef) and n.name == owner)
        patched_cls = next(n for n in after.body if isinstance(n, ast.ClassDef) and n.name == owner)
        for member in original_cls.body:
            if isinstance(member, ast.FunctionDef) and member.name != "can_run_graph":
                other = next(n for n in patched_cls.body if isinstance(n, ast.FunctionDef) and n.name == member.name)
                assert ast.dump(member) == ast.dump(other), (path, member.name)
