"""Pinned MTP method wiring/rotation tests. Fixtures are not Torch math proof.

Exact SGLang source bodies are extracted only after the root applier has verified
and applied the raw hash-pinned overlay. Backend helpers are explicit spies.
"""
from __future__ import annotations

import ast
import contextlib
from types import SimpleNamespace as NS

import pytest

from test_latent_capture_patch import (FixtureLatentError, IndexFixture, boundary,
    deny_backend_import, fixture_torch, method, pinned, source_node)


def draft_prefill_fixture(pinned, boundary, *, ids, lengths, prefixes, sampled, next_prompt=None,
                          attached=True, reject_mm=False):
    calls = []
    torch = fixture_torch()
    tail_fn = method(pinned, "speculative/eagle_utils.py", None, "_eagle_prefill_tail_tokens")
    reqs = [object() for _ in lengths]
    # No canonical descriptor is fabricated here; helper boundary sees only
    # opaque immutable fixture geometry, not a substitute authorization schema.
    context = tuple((p, n) for p, n in zip(prefixes, lengths)) if attached else None
    batch = NS(input_ids=IndexFixture(ids), extend_lens=lengths, reqs=reqs,
               forward_mode=NS(is_idle=lambda: False), occ3_context=context,
               chunked_req_next_prompt_token=next_prompt,
               chunked_req=reqs[0] if next_prompt is not None else None,
               sampling_info=object())
    target_hidden, shifted, draft_hidden = object(), object(), object()
    captured_fb = []
    def init_new(batch, runner, **kwargs):
        fb = NS(return_logprob=True, replace_embeds="unshifted", replace_positions="unshifted",
                input_embeds="stale", spec_info=batch.spec_info)
        captured_fb.append(fb)
        return fb
    def draft_embeds(runner, fb, source_batch, tail):
        calls.append("draft-embeds")
        assert source_batch is batch and fb.occ3_context is context
        assert fb.occ3_force_eager is True
        assert fb.replace_embeds is None and fb.replace_positions is None
        assert fb.spec_info.hidden_states is target_hidden
        return shifted
    boundary.draft_input_embeds = draft_embeds
    def forward(fb):
        calls.append("draft-forward")
        if attached:
            assert fb.input_embeds is shifted
        assert fb.spec_info.hidden_states is target_hidden
        return NS(logits_output=NS(next_token_logits=object(), hidden_states=draft_hidden))
    runner = NS(canary_manager=None, forward=forward)
    worker = NS(draft_runner=runner, speculative_algorithm=NS(is_standalone=lambda: False),
                seed_dsa_topk_from_draft_extend=False, topk=1)
    tails = []
    def tail_spy(*args):
        value = tail_fn(*args)
        tails.append(value.values[:])
        return value
    scope = dict(torch=torch, _eagle_prefill_tail_tokens=tail_spy,
                 EagleDraftExtendInput=lambda **k: NS(**k), CaptureHiddenMode=NS(NULL="NULL", LAST="LAST"),
                 ForwardBatch=NS(init_new=init_new), contextlib=contextlib,
                 maybe_detect_nan=lambda *a: None, maybe_detect_inf=lambda *a: None,
                 get_spec=lambda: NS(speculative_use_rejection_sampling=False),
                 renorm_draft_probs=lambda *a: object(), fast_topk=lambda *a, **k: ("prob", "index"),
                 EagleDraftInput=lambda **k: NS(**k))
    fn = method(pinned, "speculative/eagle_worker_v2.py", "EagleDraftWorker", "_draft_extend_for_prefill", scope)
    sample = IndexFixture(sampled)
    return NS(run=lambda: fn(worker, batch, target_hidden, sample, IndexFixture(ids) if reject_mm else None),
              batch=batch, calls=calls, tails=tails, sample=sample, context=context,
              shifted=shifted, hidden=target_hidden, draft_hidden=draft_hidden, fbs=captured_fb)


@pytest.mark.parametrize("ids,lengths,prefixes,sampled,next_prompt,rotated,tails", [
    ([10, 11, 12], [3], [0], [99], None, [11, 12, 99], [99]),
    ([10, 11, 12], [3], [0], [99], 13, [11, 12, 13], [13]),
    ([13, 14], [2], [3], [99], 15, [14, 15], [15]),
    ([15], [1], [5], [99], None, [99], [99]),
    ([10, 11, 12, 20, 21], [3, 2], [0, 7], [99, 88], None, [11, 12, 99, 21, 88], [99, 88]),
    ([10, 11, 12, 20, 21], [3, 2], [0, 7], [99, 88], 13, [11, 12, 13, 21, 88], [13, 88]),
])
def test_exact_rotation_uses_real_sample_or_next_prompt_tail(pinned, boundary, ids, lengths, prefixes,
                                                             sampled, next_prompt, rotated, tails):
    fixture = draft_prefill_fixture(pinned, boundary, ids=ids, lengths=lengths, prefixes=prefixes,
                                    sampled=sampled, next_prompt=next_prompt)
    result = fixture.run()
    assert fixture.batch.input_ids.values == rotated
    assert fixture.tails == [tails]
    assert fixture.sample.values == sampled  # middle-chunk tail clone does not overwrite sample
    assert fixture.calls == ["draft-embeds", "draft-forward"]
    assert result.hidden_states is fixture.draft_hidden
    assert result.bonus_tokens is fixture.sample
    assert fixture.fbs[0].occ3_context is fixture.context


def test_opt_in_draft_is_not_a_fake_multimodal_path(pinned, boundary):
    fixture = draft_prefill_fixture(pinned, boundary, ids=[10, 11], lengths=[2], prefixes=[0],
                                    sampled=[99], reject_mm=True)
    with pytest.raises(FixtureLatentError, match="profile"):
        fixture.run()
    assert fixture.calls == []


def test_ordinary_rotation_remains_lazy_and_identical(pinned, boundary, monkeypatch):
    fixture = draft_prefill_fixture(pinned, boundary, ids=[10, 11, 12], lengths=[3], prefixes=[0],
                                    sampled=[99], next_prompt=13, attached=False)
    deny_backend_import(monkeypatch)
    result = fixture.run()
    assert fixture.batch.input_ids.values == [11, 12, 13]
    assert fixture.calls == ["draft-forward"]
    assert result.hidden_states is fixture.draft_hidden


@pytest.mark.parametrize("provided", [False, True])
def test_exact_nextn_wrapper_explicit_embedding_kwarg(pinned, provided):
    embeds, previous_target_hidden, inner_output, output = object(), object(), object(), object()
    calls = []
    fb = NS(spec_info=NS(hidden_states=previous_target_hidden))
    ids, positions = object(), object()
    def inner(*args, **kwargs):
        calls.append((args, kwargs))
        assert args == (ids, positions, fb)
        assert fb.spec_info.hidden_states is previous_target_hidden
        return inner_output
    wrapper = NS(model=inner, lm_head=object(), logits_processor=lambda *a: output)
    fn = method(pinned, "models/deepseek_nextn.py", "DeepseekV3ForCausalLMNextN", "forward")
    assert fn(wrapper, ids, positions, fb, input_embeds=embeds if provided else None) is output
    assert calls[0][1] == ({"input_embeds": embeds} if provided else {})
    # Inner NextN's genuine embedding and MTP hidden paths are unmodified.
    path = "models/deepseek_nextn.py"
    assert ast.dump(source_node(pinned.original[path], "DeepseekModelNextN", "forward")) == ast.dump(source_node(pinned.patched[path], "DeepseekModelNextN", "forward"))


def extend_kwargs_fixture(pinned, *, context=True, draft=False, input_embeds=None, replace=None):
    fb = NS(occ3_context=("consume",) if context else None,
            input_embeds=input_embeds, replace_embeds=replace, replace_positions=None,
            input_ids=IndexFixture([1, 2, 3]))
    runner = NS(_pp_kwargs=lambda p: {"pp_proxy_tensors": p}, is_draft_worker=draft, is_generation=True)
    fn = method(pinned, "model_executor/model_runner.py", "ModelRunner", "_extend_forward_kwargs")
    return NS(fb=fb, runner=runner, run=lambda: fn(runner, fb, None))


def test_target_embedding_helper_validates_without_outer_fullchunk_buffer(pinned, boundary):
    calls = []
    fixture = extend_kwargs_fixture(pinned)
    boundary.target_input_embeds = lambda runner, fb: calls.append((runner, fb))
    assert fixture.run() == {"pp_proxy_tensors": None}
    assert calls == [(fixture.runner, fixture.fb)]


def test_draft_uses_shifted_effective_embeds_without_target_rebuild_or_cast(pinned, boundary):
    shifted = object()  # no bfloat16 method: no implicit dtype-changing cast allowed
    fixture = extend_kwargs_fixture(pinned, draft=True, input_embeds=shifted)
    boundary.target_input_embeds = lambda *a: pytest.fail("Draft called target embedding helper")
    assert fixture.run()["input_embeds"] is shifted


def test_capture_or_verify_none_does_not_substitute_mtp_hidden(pinned, boundary):
    fixture = extend_kwargs_fixture(pinned)
    boundary.target_input_embeds = lambda *a: None
    assert fixture.run() == {"pp_proxy_tensors": None}


@pytest.mark.parametrize("draft", [False, True])
def test_stale_unshifted_replace_overrides_are_rejected(pinned, boundary, draft):
    fixture = extend_kwargs_fixture(pinned, draft=draft, replace=object())
    with pytest.raises(FixtureLatentError, match="profile"):
        fixture.run()


def test_target_existing_input_embeddings_rejected_not_silently_scattered(pinned, boundary):
    fixture = extend_kwargs_fixture(pinned, input_embeds=object())
    boundary.target_input_embeds = lambda *a: pytest.fail("Stale override reached helper")
    with pytest.raises(FixtureLatentError, match="profile"):
        fixture.run()


def test_ordinary_embedding_override_retains_original_bfloat16_behavior(pinned, monkeypatch):
    cast = object()
    fixture = extend_kwargs_fixture(pinned, context=False, input_embeds=NS(bfloat16=lambda: cast))
    deny_backend_import(monkeypatch)
    assert fixture.run()["input_embeds"] is cast


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_root_only_actual_image_nextn_embedding_reference(device):
    """Root-only real tensor/wrapper gate. Not full-model or TP/GPU-state proof.

    Import the installed patched SGLang module only after all eleven installed
    file hashes match. Use a tiny deterministic native embedding reference; no
    checkpoint/model is loaded. Root must separately run the real TP8 capture,
    StateBridge, scheduler commit, event-lifetime and end-to-end image gates.
    """
    import hashlib
    import importlib
    import json
    import os
    from pathlib import Path
    from test_latent_capture_patch import MANIFEST

    if os.environ.get("OCC3_ROOT_IMAGE_VALIDATION") != "1":
        pytest.skip("Root-only actual-image CPU/CUDA reference gate is not enabled")
    image_root = Path(os.environ["OCC3_ROOT_IMAGE_SRT"]).resolve(strict=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        assert hashlib.sha256((image_root / entry["path"]).read_bytes()).hexdigest() == entry["after_sha256"]
    # Opt-in requires real installed dependencies. Missing dependencies fail,
    # rather than replacing Torch or skipping an explicitly requested image gate.
    torch = importlib.import_module("torch")
    nextn = importlib.import_module("sglang.srt.models.deepseek_nextn")
    assert Path(nextn.__file__).resolve() == image_root / "models/deepseek_nextn.py"
    if device == "cuda":
        assert torch.cuda.is_available(), "Explicit actual-image CUDA gate needs a real GPU"
    weight = torch.arange(32, device=device, dtype=torch.float32).reshape(8, 4)
    ids = torch.tensor([1, 2, 3], device=device)
    effective = torch.nn.functional.embedding(ids, weight)
    prefix_rows = torch.tensor([[100., 101., 102., 103.], [104., 105., 106., 107.]], device=device)
    effective[:2] = prefix_rows
    target_mtp = torch.ones_like(effective)
    fb = NS(spec_info=NS(hidden_states=target_mtp))
    seen = []
    def native_inner(input_ids, positions, forward_batch, input_embeds=None):
        assert forward_batch.spec_info.hidden_states is target_mtp
        seen.append(input_embeds)
        return (torch.nn.functional.embedding(input_ids, weight)
                if input_embeds is None else input_embeds)
    wrapper = NS(model=native_inner, lm_head=None,
                 logits_processor=lambda ids, hidden, head, batch: hidden)
    positions = torch.arange(3, device=device)
    result = nextn.DeepseekV3ForCausalLMNextN.forward(wrapper, ids, positions, fb, input_embeds=effective)
    assert len(seen) == 1 and seen[0] is effective and result is effective
    torch.testing.assert_close(result[:2], prefix_rows)
    torch.testing.assert_close(result[2], weight[3])
    assert fb.spec_info.hidden_states is target_mtp
    if device == "cuda":
        event = torch.cuda.Event()
        event.record(torch.cuda.current_stream())
        event.synchronize()
        assert event.query()
