"""Pinned-source control-flow tests, not SGLang/Torch/GPU execution evidence.

The fixture copies raw attested source, then uses root source_patch.prepare/apply.
Only extracted, unmodified method ASTs run against bounded indexing/stream spies.
Set OCC3_B_ATTESTATION_ROOT to the directory containing the two attestation trees.
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

EXT = Path(__file__).resolve().parents[1]
MANIFEST = EXT / "patches/capture-eager-mtp.v1.json"
EXPECTED_PATHS = {
    "models/deepseek_v2.py", "layers/logits_processor.py",
    "speculative/eagle_worker_common.py", "speculative/eagle_worker_v2.py",
    "models/deepseek_nextn.py", "model_executor/model_runner.py",
    "model_executor/runner/decode_cuda_graph_runner.py",
    "model_executor/runner/prefill_cuda_graph_runner.py",
    "speculative/eagle_draft_cuda_graph_runner.py",
    "speculative/eagle_draft_extend_cuda_graph_runner.py", "managers/utils.py",
}
# Additional read-only seams, not overlay-owned files. Raw attested byte hashes.
EXTRA_HASHES = {
    "layers/layer_boundary/residual/access.py": "6960af6d1fad7b3d0984136cbad885f1b9f64f9033880ef4337b06ae63a8298e",
    "layers/layer_boundary/residual/batch.py": "228c6c71aebd8426c8c001b298556f6b56ef78ee79f874e524fffd5abd883c8c",
    "speculative/eagle_utils.py": "7998e60c335e7e0e7bcdc4fa6403c2d697128077491858e810c37af6de1a3df7",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_node(text, owner, name):
    tree = ast.parse(text)
    body = tree.body if owner is None else next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner
    ).body
    return next(n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def extracted(text, owner, name, scope=None):
    """Execute the exact function body. Only decorators are stripped for isolation."""
    node = copy.deepcopy(source_node(text, owner, name))
    node.decorator_list = []
    tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    ast.fix_missing_locations(tree)
    namespace = {"__name__": "_occ3_pinned_method_fixture"}
    namespace.update(scope or {})
    exec(compile(tree, "<exact-pinned-method-fixture>", "exec"), namespace)
    return namespace[name]


def method(fixture, path, owner, name, scope=None, *, original=False):
    texts = fixture.original if original else fixture.patched
    return extracted(texts[path], owner, name, scope)


@pytest.fixture(scope="session")
def pinned(tmp_path_factory):
    default = Path.home() / "Optimus-Assistant/profile/workspace/occ3-campaign/v3-controlled-deploy"
    base = Path(os.environ.get("OCC3_B_ATTESTATION_ROOT", default))
    roots = [base / "original-sglang-source-attestation-v2", base / "original-sglang-source-attestation-eager"]
    if not all(root.is_dir() for root in roots):
        pytest.skip("Exact external attested source absent; set OCC3_B_ATTESTATION_ROOT. No substitute source is used.")
    spec = importlib.util.spec_from_file_location("_occ3_b_source_patch", EXT / "deploy/v3/source_patch.py")
    applier = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = applier
    spec.loader.exec_module(applier)
    manifest = applier.load_manifest(MANIFEST)
    assert {f["path"] for f in manifest["files"]} == EXPECTED_PATHS
    assert len(manifest["files"]) == 11
    root = tmp_path_factory.mktemp("occ3-b-pinned") / "sglang/srt"
    root.mkdir(parents=True)
    raw, original, patched = {}, {}, {}
    for entry in manifest["files"]:
        rel = entry["path"]
        candidates = [(r / rel).read_bytes() for r in roots if (r / rel).is_file()]
        assert candidates, f"Missing attested raw source: {rel}"
        assert all(b == candidates[0] for b in candidates), rel
        raw[rel] = candidates[0]
        assert sha(raw[rel]) == entry["before_sha256"]
        original[rel] = raw[rel].decode("utf-8")
        text = original[rel]
        for replacement in entry["replacements"]:
            assert text.count(replacement["before"]) == 1
            text = text.replace(replacement["before"], replacement["after"], 1)
        assert sha(text.encode("utf-8")) == entry["after_sha256"]
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw[rel])
    plan = applier.prepare(root, manifest)
    assert all((root / rel).read_bytes() == value for rel, value in raw.items())
    proof = applier.apply(plan)
    for entry in manifest["files"]:
        data = (root / entry["path"]).read_bytes()
        assert sha(data) == entry["after_sha256"]
        compile(data, entry["path"], "exec", dont_inherit=True)
        patched[entry["path"]] = data.decode("utf-8")
    for rel, digest in EXTRA_HASHES.items():
        data = next((r / rel).read_bytes() for r in roots if (r / rel).is_file())
        assert sha(data) == digest
        original[rel] = patched[rel] = data.decode("utf-8")
    return NS(root=root, raw=raw, original=original, patched=patched,
              manifest=manifest, plan=plan, proof=proof, applier=applier)


class FixtureLatentError(RuntimeError):
    """Boundary exception spy. Not the backend's validation implementation."""


@pytest.fixture
def boundary(monkeypatch):
    worker = types.ModuleType("occ3_sglang.latent_worker")
    record_types = types.ModuleType("occ3_sglang.latent_types")
    record_types.LatentError = FixtureLatentError
    monkeypatch.setitem(sys.modules, worker.__name__, worker)
    monkeypatch.setitem(sys.modules, record_types.__name__, record_types)
    return worker


def deny_backend_import(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith("occ3_sglang"):
            raise AssertionError(f"Opt-out path imported {name}")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)


class IndexFixture:
    """Small list/index/mutation fixture, not a Torch or numerical substitute."""
    dtype = "fixture-int"
    device = "fixture-device"

    def __init__(self, values, *, parent=None, indices=None):
        self._values = list(values)
        self.parent, self.indices = parent, indices

    @property
    def values(self):
        if self.parent is not None:
            return [self.parent.values[i] for i in self.indices]
        return self._values

    @property
    def shape(self):
        v = self.values
        return (len(v), len(v[0])) if v and isinstance(v[0], list) else (len(v),)

    def __len__(self):
        return len(self.values)

    def numel(self):
        return len(self.values)

    def __getitem__(self, item):
        values = self.values
        if isinstance(item, IndexFixture):
            indices = item.values
            if indices and isinstance(indices[0], list):
                return IndexFixture([[values[i] for i in row] for row in indices])
            return IndexFixture([values[i] for i in indices])
        if isinstance(item, slice):
            indices = list(range(len(values)))[item]
            return IndexFixture([], parent=self, indices=indices)
        return IndexFixture([values[item]])

    def copy_(self, other):
        incoming = list(other.values)
        if self.parent is None:
            self._values[:] = incoming
        else:
            assert len(incoming) == len(self.indices)
            for i, value in zip(self.indices, incoming):
                self.parent._values[i] = value
        return self

    def fill_(self, value):
        return self.copy_(IndexFixture([value] * len(self.values)))

    def clone(self):
        return IndexFixture(copy.deepcopy(self.values))

    def to(self, *args, **kwargs):
        return self

    def reshape(self, *shape):
        return self

    def __add__(self, other):
        return IndexFixture([a + b for a, b in zip(self.values, other.values)])


def fixture_torch():
    return NS(Tensor=IndexFixture, int32="fixture-int32", float32="fixture-float32",
              empty_like=lambda x, **k: IndexFixture([None] * len(x)),
              empty=lambda shape, **k: IndexFixture([]),
              cat=lambda values: IndexFixture([v for x in values for v in x.values]))


def test_raw_hash_unique_replacements_and_syntax(pinned):
    assert pinned.manifest["source_revision"] == "e00930c5489053f26d86b179cee0d087f846acbb"
    assert pinned.manifest["source_version"] == "0.5.21"
    assert len(pinned.plan.files) == 11
    with pytest.raises(pinned.applier.PatchError, match="original_digest_mismatch"):
        pinned.applier.prepare(pinned.root, pinned.manifest)


def test_raw_mismatch_and_duplicate_anchor_fail_closed(pinned, tmp_path):
    for entry in pinned.manifest["files"]:
        path = tmp_path / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pinned.raw[entry["path"]])
    first = pinned.manifest["files"][0]
    target = tmp_path / first["path"]
    target.write_bytes(pinned.raw[first["path"]] + b"\n# altered raw byte\n")
    with pytest.raises(pinned.applier.PatchError, match="original_digest_mismatch"):
        pinned.applier.prepare(tmp_path, pinned.manifest)
    target.write_bytes(pinned.raw[first["path"]])
    duplicate = copy.deepcopy(pinned.manifest)
    duplicate["files"][0]["replacements"][0]["before"] = "\n"
    with pytest.raises(pinned.applier.PatchError, match="replacement_not_unique"):
        pinned.applier.prepare(tmp_path, duplicate)


@pytest.mark.parametrize("consent", [False, True])
@pytest.mark.parametrize("fused", [False, True])
def test_exact_model_final_residual_callback(pinned, boundary, monkeypatch, consent, fused):
    calls = []
    hidden = NS(device="fixture", shape=(2, 4))
    completed = object()
    normalized = object()
    norm_output = method(pinned, "layers/layer_boundary/residual/access.py", None, "norm_output")
    def fake_norm(h, residual=None):
        calls.append("norm")
        return (normalized, completed) if residual is not None else normalized
    def capture(batch, value):
        calls.append(("capture", value))
        batch.occ3_capture_rows = value
    boundary.capture_prenorm = capture
    boundary.inject_target_embeddings = lambda batch, hidden: None
    def residual_norm(h, batch, norm, **kwargs):
        return norm_output(h, object() if fused else None, norm, **kwargs)
    residual = NS(start=lambda b: None, complete_output=lambda h, b: h, norm=residual_norm)
    scope = dict(torch=fixture_torch(), residual_batch=residual,
                 IndexTopKShareState=lambda *a: NS(topk_indices=None),
                 BumpAllocator=lambda **k: None, AuxHiddenStatePacker=lambda n: [])
    model = NS(end_layer=0, start_layer=0, _dsa_forward_uses_topk=lambda: False,
               pp_group=NS(is_first_rank=True, is_last_rank=True), embed_tokens=lambda ids: hidden,
               llama_4_scaling_config=None, layers_to_capture=[], norm=fake_norm)
    fb = NS(can_run_tbo=False, forward_mode=NS(is_idle=lambda: False), occ3_context=("source",) if consent else None)
    fn = method(pinned, "models/deepseek_v2.py", "DeepseekV2Model", "forward", scope)
    if not consent:
        deny_backend_import(monkeypatch)
    assert fn(model, object(), object(), fb) is normalized
    captures = [c for c in calls if isinstance(c, tuple)]
    if consent:
        assert captures == [("capture", completed if fused else hidden)]
        assert calls.index(captures[0]) > calls.index("norm") if fused else calls.index(captures[0]) < calls.index("norm")
    else:
        assert captures == []


@pytest.mark.parametrize("consent", [False, True])
def test_native_target_lookup_injected_in_place_before_residual_start(pinned, boundary, monkeypatch, consent):
    """Control/lifetime seam only; real scatter/prefix Tensor math is A's gate."""
    calls = []
    native_hidden = NS(device="fixture", shape=(2, 4), prefix_marker=None)
    normalized = object()
    def embed(ids):
        calls.append("native-lookup")
        return native_hidden
    def inject(batch, hidden):
        calls.append("inject")
        assert hidden is native_hidden
        # No clone/cpu/to methods exist on this fixture: B passes the native
        # allocation directly and neither copies nor stashes the full buffer.
        hidden.prefix_marker = "in-place"
    boundary.inject_target_embeddings = inject
    boundary.capture_prenorm = lambda batch, rows: None
    norm_output = method(pinned, "layers/layer_boundary/residual/access.py", None, "norm_output")
    def norm(hidden):
        calls.append("norm")
        assert hidden is native_hidden
        assert hidden.prefix_marker == ("in-place" if consent else None)
        return normalized
    residual = NS(start=lambda batch: calls.append("residual-start"),
                  complete_output=lambda hidden, batch: hidden,
                  norm=lambda h, b, n, **k: norm_output(h, None, n, **k))
    scope = dict(torch=fixture_torch(), residual_batch=residual,
                 IndexTopKShareState=lambda *a: NS(topk_indices=None),
                 BumpAllocator=lambda **k: None, AuxHiddenStatePacker=lambda n: [])
    model = NS(end_layer=0, start_layer=0, _dsa_forward_uses_topk=lambda: False,
               pp_group=NS(is_first_rank=True, is_last_rank=True), embed_tokens=embed,
               llama_4_scaling_config=None, layers_to_capture=[], norm=norm)
    fb = NS(can_run_tbo=False, forward_mode=NS(is_idle=lambda: False),
            occ3_context=("consume",) if consent else None)
    fn = method(pinned, "models/deepseek_v2.py", "DeepseekV2Model", "forward", scope)
    if not consent:
        deny_backend_import(monkeypatch)
    assert fn(model, object(), object(), fb) is normalized
    assert calls == (["native-lookup", "inject", "residual-start", "norm"] if consent
                     else ["native-lookup", "residual-start", "norm"])
    assert not hasattr(fb, "occ3_effective_embeds")


@pytest.mark.parametrize("consent", [False, True])
def test_exact_logits_wrapper_keeps_mtp_hidden_separate(pinned, monkeypatch, consent):
    prenorm, mtp, normed, aux = object(), object(), object(), object()
    result = NS(hidden_states=mtp)
    calls = []
    fb = NS(occ3_context=("source",) if consent else None, occ3_capture_rows=prenorm)
    model = NS(model=lambda *a: (normed, aux), capture_aux_hidden_states=True,
               pp_group=NS(is_last_rank=True), lm_head=object(),
               logits_processor=lambda *a: calls.append(a) or result)
    scope = {"get_attn_tp_context": lambda: NS(maybe_input_scattered=lambda f: contextlib.nullcontext())}
    fn = method(pinned, "models/deepseek_v2.py", "DeepseekV2ForCausalLM", "forward", scope)
    deny_backend_import(monkeypatch)
    assert fn(model, object(), object(), fb) is result
    assert result.hidden_states is mtp
    assert calls[0][1] is normed and calls[0][-1] is aux
    assert getattr(result, "occ3_capture_rows", None) is (prenorm if consent else None)


def verify_fixture(pinned, boundary, *, consent, accepted, compact=True, invalid=False):
    """Run the complete pinned verify function with control/index boundary spies."""
    calls = []
    width = 6
    indices = [[0, 2, 4, 5, 1, 3][:n] + [-1] * (width - n) for n in accepted]
    indices = [[i + req * width if i >= 0 else i for i in row] for req, row in enumerate(indices)]
    predict = IndexFixture([100 + i for i in range(width * len(accepted))])
    positions = IndexFixture([20 + i for i in range(width * len(accepted))])
    accept_lens, accept_index = IndexFixture(accepted), IndexFixture(indices)
    rows = [f"predictor-{i}" for i in range(width * len(accepted))]
    mtp = object()
    logits = NS(next_token_logits=object(), hidden_states=mtp, occ3_capture_rows=rows)
    fb = NS(occ3_context=("source",) if consent else None)
    vi = NS(positions=positions)
    batch = NS(spec_info=vi, seq_lens=IndexFixture([20] * len(accepted)), input_ids=IndexFixture([900] * (width * len(accepted))),
               out_cache_loc=object(), has_grammar=False, return_logprob=False,
               forward_mode=NS(is_idle=lambda: False))
    original_predict = predict.values[:]
    def stage(*args):
        calls.append("stage")
        assert args == (fb, logits, predict, accept_lens, accept_index, positions)
        if invalid:
            raise FixtureLatentError("lineage")
        # This spy only checks wiring and declared slice semantics. Real GPU
        # gather/lineage validation is owned by latent_worker native-image tests.
        result = []
        for n, ix in zip(accepted, indices):
            chosen = ix[:n]
            assert all(i >= 0 for i in chosen)
            result.append([(rows[i], predict.values[i], positions.values[i]) for i in chosen])
        return NS(provisional=True, rows=result)
    boundary.stage_verify = stage
    def sample(*args, **kwargs):
        calls.append("sample")
        return predict, accept_lens, accept_index
    def finalize(*args, **kwargs):
        calls.append("compact")
        predict._values[:] = [-9] * len(predict)
        return predict
    torch = fixture_torch()
    torch.get_device_module = lambda d: NS(current_stream=lambda: object())
    scope = dict(torch=torch, record_stream_for_v2_verify=lambda *a: None,
                 eagle_prepare_for_verify=lambda *a: (fb, False), record_stream_each=lambda *a: None,
                 maybe_detect_nan=lambda *a: None, maybe_detect_inf=lambda *a: None,
                 eagle_sample=sample, commit_mamba_states_after_verify=lambda *a: calls.append("mamba"),
                 fill_bonus_tokens_func=lambda *a: None, _finalize_accept_tree_path=finalize,
                 EagleDraftInput=lambda **k: NS(**k), GenerationBatchResult=lambda **k: NS(**k))
    worker = NS(forward_batch_generation=lambda **k: NS(logits_output=logits, routed_experts_output=None, indexer_topk_output=None))
    fn = method(pinned, "speculative/eagle_worker_common.py", None, "run_eagle_verify", scope)
    args = dict(target_worker=worker, req_to_token_pool=object(), token_to_kv_pool_allocator=NS(get_kvcache=lambda: NS()),
                plan_stream=None, plan_stream_ctx=contextlib.nullcontext(), topk=2 if compact else 1,
                num_draft_tokens=width, device="fixture", metadata_ready_pre_pad=False, finalize_tree_path=compact)
    return NS(run=lambda: fn(batch, **args), calls=calls, mtp=mtp, indices=indices,
              tokens=original_predict, rows=rows, accepted=accepted, fb=fb)


@pytest.mark.parametrize("accepted", [[1], [2], [4], [6], [2, 4]])
def test_exact_verify_stages_accepted_target_nodes_before_compaction(pinned, boundary, accepted):
    fixture = verify_fixture(pinned, boundary, consent=True, accepted=accepted)
    result = fixture.run()
    assert fixture.calls[:3] == ["sample", "stage", "mamba"]
    assert fixture.calls.index("stage") < fixture.calls.index("compact")
    assert result.occ3_capture_stage.provisional
    assert result.logits_output.hidden_states is fixture.mtp
    for n, ix, rows in zip(accepted, fixture.indices, result.occ3_capture_stage.rows):
        assert [r[0] for r in rows] == [fixture.rows[i] for i in ix[:n]]
        assert [r[1] for r in rows] == [fixture.tokens[i] for i in ix[:n]]
        assert all(r[1] >= 100 for r in rows)  # neither draft token 900 nor compacted -9
        assert len(rows) == n
    assert result.extra_keep_alive_refs == [fixture.fb]


def test_verify_stage_is_not_a_stop_or_eos_commit(pinned, boundary):
    fixture = verify_fixture(pinned, boundary, consent=True, accepted=[4])
    result = fixture.run()
    # The exact verify method does not guess that all four are retained. EOS /
    # grammar / max-new-token clipping occurs later in C's authoritative commit.
    assert result.occ3_capture_stage.provisional
    assert len(result.occ3_capture_stage.rows[0]) == 4
    fn = source_node(pinned.patched["speculative/eagle_worker_common.py"], None, "run_eagle_verify")
    names = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not names.intersection({"commit_result", "publish", "align", "complete_capture"})


def test_verify_helper_failure_prevents_compaction(pinned, boundary):
    fixture = verify_fixture(pinned, boundary, consent=True, accepted=[2], invalid=True)
    with pytest.raises(FixtureLatentError, match="lineage"):
        fixture.run()
    assert fixture.calls == ["sample", "stage"]


def test_verify_opt_out_does_not_import_backend(pinned, boundary, monkeypatch):
    fixture = verify_fixture(pinned, boundary, consent=False, accepted=[2])
    deny_backend_import(monkeypatch)
    result = fixture.run()
    assert result.occ3_capture_stage is None
    assert fixture.calls == ["sample", "mamba", "compact"]


@pytest.mark.parametrize("consent", [False, True])
def test_exact_prefill_stage_before_publish_and_real_sample(pinned, boundary, monkeypatch, consent):
    calls = []
    sampled, logits = object(), object()
    result = NS(logits_output=logits, next_token_ids=sampled)
    batch = NS(seq_lens=object(), occ3_context=("source",) if consent else None)
    stage = object()
    def stage_prefill(*args):
        assert args == (batch, logits, sampled)
        calls.append("stage")
        return stage
    boundary.stage_prefill = stage_prefill
    target = NS(forward_batch_generation=lambda *a, **k: calls.append("target") or result)
    worker = NS(speculative_algorithm=NS(is_standalone=lambda: False), target_worker=target, _draft_worker=None)
    scope = {"CaptureHiddenMode": NS(NULL="NULL", FULL="FULL")}
    fn = method(pinned, "speculative/eagle_worker_v2.py", "EAGLEWorkerV2", "_forward_prefill_batch", scope)
    if not consent:
        deny_backend_import(monkeypatch)
    assert fn(worker, batch, on_publish=lambda x: calls.append("publish")) is result
    assert calls == (["target", "stage", "publish"] if consent else ["target", "publish"])
    assert getattr(result, "occ3_capture_stage", None) is (stage if consent else None)


def test_copy_to_cpu_never_visits_gpu_capture_sidecars(pinned):
    class GPUOnly:
        def __getattr__(self, name):
            raise AssertionError(f"GPU sidecar accessed by CPU copy: {name}")
    rows, stage, hidden, tokens = GPUOnly(), GPUOnly(), object(), object()
    copied, recorded = [], []
    logits = NS(hidden_states=hidden, occ3_capture_rows=rows, sampling_mask_output=None)
    result = NS(logits_output=logits, next_token_ids=tokens, accept_lens=None,
                block_accept_lens=None, cap_lens=None, routed_experts_output=None,
                indexer_topk_output=None, expert_distribution_metrics=None,
                copy_auxiliary_output_to_cpu=lambda: None,
                copy_done=NS(record=lambda: recorded.append(True)), occ3_capture_stage=stage)
    fn = method(pinned, "managers/utils.py", "GenerationBatchResult", "copy_to_cpu",
                {"_async_d2h": lambda x: copied.append(x) or x})
    fn(result, return_logprob=False)
    assert copied == [hidden, tokens] and recorded == [True]
    assert result.occ3_capture_stage is stage and logits.occ3_capture_rows is rows
    # Existing copy code is byte-for-byte unchanged by the overlay.
    rel = "managers/utils.py"
    assert ast.dump(source_node(pinned.original[rel], "GenerationBatchResult", "copy_to_cpu")) == ast.dump(source_node(pinned.patched[rel], "GenerationBatchResult", "copy_to_cpu"))


def test_mtp_logit_selection_untouched(pinned):
    rel = "layers/logits_processor.py"
    assert ast.dump(source_node(pinned.original[rel], "LogitsProcessor", "_get_hidden_states_to_store")) == ast.dump(source_node(pinned.patched[rel], "LogitsProcessor", "_get_hidden_states_to_store"))


def test_root_only_real_torch_callback_reference(pinned):
    if os.environ.get("OCC3_ROOT_NATIVE_VALIDATION") != "1":
        pytest.skip("Root-only opt-in real Torch CPU/reference validation; not executed by offline B worker.")
    torch = pytest.importorskip("torch", reason="Actual native Torch is absent; no numerical stub is accepted")
    norm_output = method(pinned, "layers/layer_boundary/residual/access.py", None, "norm_output")
    h = torch.tensor([[1.0, -2.0], [3.0, 4.0]], dtype=torch.float32)
    residual = torch.tensor([[0.5, 1.0], [-1.0, 2.0]], dtype=torch.float32)
    captured = []
    def reference_norm(x, r):
        complete = x + r
        return torch.nn.functional.layer_norm(complete, (2,)), complete
    out = norm_output(h, residual, reference_norm, lambda rows: captured.append(rows.clone()))
    torch.testing.assert_close(captured[0], h + residual)
    torch.testing.assert_close(out, torch.nn.functional.layer_norm(h + residual, (2,)))
