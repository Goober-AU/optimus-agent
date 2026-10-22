"""ROOT-ONLY optional CUDA tensor acceptance; never enabled by ordinary tests.

Run only in the exact disposable TP8 image after source overlays pass review.
This does not load a checkpoint or start native inference. End-to-end native
MTP/semantic acceptance must be run separately by the campaign owner.
"""
import os
import pytest

pytestmark=pytest.mark.skipif(os.environ.get("OCC3_ROOT_GPU_VALIDATION")!="1",
                             reason="Root-only CUDA validation explicitly disabled")


def test_real_cuda_alignment_peak_and_retained_geometry():
    import torch
    from occ3_sglang.statebridge import align_hidden_sequence, workspace_bound
    assert torch.cuda.is_available()
    device=torch.device("cuda",torch.cuda.current_device())
    for n in (8,16,32,64,128):
        h=torch.randn(n,6144,device=device,dtype=torch.bfloat16)
        e=torch.randn_like(h)
        torch.cuda.reset_peak_memory_stats(device)
        baseline=torch.cuda.memory_allocated(device)
        z,info=align_hidden_sequence(h,e,4.0)
        torch.cuda.synchronize(device)
        peak=torch.cuda.max_memory_allocated(device)-baseline
        assert z.is_cuda and z.shape==(n,6144) and info.union_rank<=2*(n-1)
        assert peak<=workspace_bound(n,6144,8,256,0)
        assert torch.isfinite(z).all()


@pytest.mark.parametrize("width", [2,4,6])
def test_actual_cuda_accepted_predictor_gather_no_rejected_rows(monkeypatch, width):
    # Real tensor/event unit test with synthetic batch geometry, NOT a GLM run.
    import torch
    from types import SimpleNamespace as NS
    from dataclasses import replace
    from occ3_sglang import latent_worker as worker
    from occ3_sglang.latent_types import RequestSlice
    from test_latent_types import capture,profile
    device=torch.device("cuda",torch.cuda.current_device())
    record=NS(events=[],live_refs=[])
    runtime=NS(torch=torch,device=device,profile=profile(),poisoned=False,
               resolve=lambda descriptor:record, preflight=lambda phase,check:check())
    def event():
        e=torch.cuda.Event(); e.record(); return e
    runtime.event=event
    monkeypatch.setattr(worker,"_RUNTIME",runtime)
    desc=capture()
    context=(RequestSlice(desc,0,width,10,1,False),)
    h=torch.arange(width*6144,device=device,dtype=torch.float32).reshape(width,6144)
    positions=torch.arange(10,10+width,device=device)
    rows=worker.CaptureRows(context,h,positions,"verify",event(),7)
    lpo=NS(occ3_capture_rows=rows)
    predicts=torch.arange(100,100+width,device=device)
    for count in range(1,width+1):
        index=torch.full((1,width),-1,device=device,dtype=torch.int64)
        index[0,:count]=torch.arange(count,device=device)
        stage=worker.stage_verify(NS(occ3_context=context),lpo,predicts,torch.tensor([count],device=device),index,positions)
        assert stage.hidden.is_cuda and stage.tokens.is_cuda
        assert torch.equal(stage.hidden[0,:count],h[:count])
        assert torch.equal(stage.tokens[0,:count],predicts[:count])
        assert torch.equal(stage.positions[0,:count],positions[:count])
        assert stage.iteration==7 and not stage.committed


def test_actual_cuda_shifted_draft_chunk_tail_remains_continuous(monkeypatch):
    # Real CUDA ops; synthetic embedding weights/batch, not native MTP inference.
    import torch
    from types import SimpleNamespace as NS
    from occ3_sglang import latent_worker as worker
    from occ3_sglang.latent_types import RequestSlice
    from test_latent_registry import consume
    from test_latent_types import profile
    device=torch.device("cuda",torch.cuda.current_device())
    prefix=torch.arange(8,device=device,dtype=torch.float32)[:,None].expand(8,6144).clone()+1000
    record=NS(prefix=prefix,events=[],live_refs=[])
    runtime=NS(torch=torch,device=device,profile=profile(),poisoned=False,resolve=lambda d:record,preflight=lambda phase,check:check())
    def event():
        e=torch.cuda.Event(); e.record(); return e
    runtime.event=event
    monkeypatch.setattr(worker,"_RUNTIME",runtime)
    embedding=lambda ids:ids[:,None].expand(-1,6144).float()
    runner=NS(model=NS(get_input_embeddings=lambda:embedding))
    context=(RequestSlice(consume(),0,4,3,0,False),)
    fb=NS(occ3_context=context,occ3_force_eager=True,batch_size=1,
          input_ids=torch.tensor([0,0,0,99],device=device))
    values=worker.draft_input_embeds(runner,fb,NS(occ3_context=context),torch.tensor([99],device=device))
    assert torch.equal(values,prefix[4:8])  # Actual chunk tail lies in latent prefix.


def test_actual_tp8_partial_rank_preflight_refuses_all():
    # Explicit root torchrun-only collective unit test. Never starts a model.
    import torch
    from types import SimpleNamespace as NS
    from occ3_sglang.latent_worker import WorkerRuntime
    from occ3_sglang.latent_types import LatentError
    from test_latent_types import profile
    if os.environ.get("WORLD_SIZE")!="8":
        pytest.skip("Requires root torchrun with eight ranks, not singleton GPU")
    created=not torch.distributed.is_initialized()
    if created:
        from datetime import timedelta
        torch.distributed.init_process_group("gloo",timeout=timedelta(seconds=30))
    runtime=object.__new__(WorkerRuntime)
    runtime.torch=torch; runtime.profile=profile(); runtime.group=NS(cpu_group=torch.distributed.group.WORLD)
    runtime.poisoned=False
    def local():
        if torch.distributed.get_rank()==3:
            raise LatentError("expired")
        return {"accepted":8}
    with pytest.raises(LatentError,match="ranks"):
        runtime.preflight("admit",local)
    assert runtime.poisoned
    if created:
        torch.distributed.destroy_process_group()


def test_actual_cuda_full_chunk_injection_has_no_extra_full_chunk_allocation(monkeypatch):
    # Real CUDA allocation/copy, synthetic batch and native-lookup stand-in.
    # This measures helper marginal memory, NOT full native GLM peak memory.
    import torch
    from types import SimpleNamespace as NS
    from occ3_sglang import latent_worker as worker
    from occ3_sglang.latent_types import RequestSlice
    from test_latent_registry import consume
    from test_latent_types import profile
    import sglang.srt.layers.layer_boundary as boundary
    device=torch.device("cuda",torch.cuda.current_device())
    monkeypatch.setattr(boundary,"get_attn_tp_context",lambda:NS(input_scattered=False))
    hidden=torch.empty((32768,6144),device=device,dtype=torch.bfloat16)
    prefix=torch.randn((128,6144),device=device,dtype=torch.bfloat16)
    record=NS(prefix=prefix,events=[],live_refs=[])
    runtime=NS(torch=torch,device=device,profile=profile(),poisoned=False,
               resolve=lambda d:record,preflight=lambda phase,check:check())
    def event():
        value=torch.cuda.Event(); value.record(); return value
    runtime.event=event
    monkeypatch.setattr(worker,"_RUNTIME",runtime)
    desc=consume(expected_prefix_tokens=128)
    fb=NS(occ3_context=(RequestSlice(desc,0,32768,0,0,False),),
          occ3_force_eager=True,batch_size=1,forward_mode=NS(is_extend=lambda:True))
    torch.cuda.synchronize(device)
    original_pointer=hidden.data_ptr()
    torch.cuda.reset_peak_memory_stats(device); baseline=torch.cuda.memory_allocated(device)
    worker.inject_target_embeddings(fb,hidden)
    torch.cuda.synchronize(device)
    assert torch.cuda.max_memory_allocated(device)-baseline < 4*1024*1024
    assert hidden.data_ptr()==original_pointer and torch.equal(hidden[:128],prefix)
    assert fb.occ3_effective_embeds==(0,128) and not record.live_refs


def test_actual_cuda_partial_helper_submission_retirement_requires_cleanup_fence():
    # Real CUDA stream/events with a synthetic registry; not a GLM request.
    import torch
    from occ3_sglang.latent_worker import WorkerRuntime,_begin_gpu
    from test_latent_registry import registry,ready
    device=torch.device("cuda",torch.cuda.current_device())
    r,_=registry(); record=ready(r)
    runtime=object.__new__(WorkerRuntime)
    runtime.torch=torch; runtime.device=device; runtime.streams={}
    stream=torch.cuda.Stream(device=device)
    with torch.cuda.stream(stream):
        _begin_gpu(runtime,record)
        start=record.events[0]
        record.prefix=torch.empty((128,6144),device=device,dtype=torch.bfloat16)
        record.prefix.fill_(2)
    start.synchronize()  # Start completion alone never authorizes physical release.
    assert r.retire(handle="handle")["gpu_freed"] is False and record.inflight==1
    runtime.fence()  # New actual terminal event for the tracked submitting stream.
    record.inflight=0
    r.reap()
    assert record.gpu_freed and record.prefix is None and r.charged_bytes==0
