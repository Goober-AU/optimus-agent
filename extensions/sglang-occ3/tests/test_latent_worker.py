"""CPU ownership/geometry tests. GPU tensor execution is in optional tests only."""
from array import array
from dataclasses import replace
from types import SimpleNamespace as NS
import sys
import pytest

from occ3_sglang import latent_worker as w
from occ3_sglang.latent_types import LatentError
from test_latent_types import capture, profile
from test_latent_registry import consume, registry, ready, publication


def req(descriptor=None):
    return NS(rid="rid", output_ids=array("q"), origin_input_ids=array("q",[1,2,3]),
        origin_input_ids_unpadded=array("q",[1,2,3]), full_untruncated_fill_ids=array("q",[1,2,3]),
        extend_range=NS(end=3), session=None, multimodal_inputs=None, positional_embed_overrides=None,
        occ3_worker_descriptor=descriptor, retraction_count=0)


def test_optout_helpers_are_inert_without_runtime():
    assert w.batch_context([req()]) is None
    assert w.capture_prenorm(NS(occ3_context=None),object()) is None
    assert w.target_input_embeds(None,NS(occ3_context=None)) is None
    assert w.mark_injection(NS()) is None
    assert w.admit_request(None,req(),None) is None
    assert w.record_forward_start(None,NS(occ3_context=None)) is None


def test_context_snapshots_and_cohort():
    r=req(capture()); r.output_ids.extend([5,6])
    ctx=w.batch_context([r],[3],[7],[True])
    r.output_ids.append(7)
    assert ctx[0].output_start == 2 and ctx[0].prefix_len == 7 and ctx[0].final_chunk
    with pytest.raises(LatentError):
        w.batch_context([r,req()],[3,3],[0,0],[True,True])


def test_receiver_admission_marks_every_cache_guard(monkeypatch):
    d=consume(native_rid="rid")
    record=NS(state="leased",admitted_consume=False,state_generation=123)
    runtime=NS(poisoned=False, resolve=lambda value, **kw: record,
               preflight=lambda phase, check: check())
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    scheduler=NS(occ3_runtime=runtime)
    r=req(); w.admit_request(scheduler,r,d)
    assert list(r.origin_input_ids)==[0]*8+[1,2,3]
    assert r.origin_input_ids_unpadded == r.origin_input_ids
    assert r.occ3_private_prefix and r.occ3_private_kv and r.skip_radix_cache_insert
    with pytest.raises(LatentError):
        w.admit_request(scheduler,r,d)


def test_missing_worker_refuses_not_fake_readiness():
    with pytest.raises(LatentError):
        w.admit_request(NS(),req(),capture())


def test_capture_budget_all_rank_units():
    runtime=object.__new__(w.WorkerRuntime)
    runtime.profile=profile(); runtime.element_size=2
    assert runtime.capture_budget(128) > 8*128*6144*2
    assert runtime.capture_budget(128) < 128*1024*1024
    runtime.profile=profile(tile_rows=1024)
    with pytest.raises(LatentError):
        runtime.capture_budget(128)


def test_profile_loader_uses_no_client_fields(monkeypatch):
    import types
    from dataclasses import asdict
    module=types.ModuleType("occ3_sglang.native_profile")
    static=asdict(profile()); static.pop("runtime_epoch"); static.pop("runtime_generation")
    module.load_pinned_profile=lambda:static
    module.get_runtime_incarnation=lambda:dict(runtime_epoch="fresh",runtime_generation=99,
        incarnation_digest="a"*64,profile_sha256="b"*64,source_attestation_sha256="c"*64)
    monkeypatch.setitem(sys.modules,"occ3_sglang.native_profile",module)
    actual=w.load_runtime_profile()
    assert actual.runtime_epoch == "fresh" and actual.runtime_generation == 99
    static["runtime_epoch"]="forged"
    with pytest.raises(LatentError):
        w.load_runtime_profile()


def test_all_rank_preflight_rejects_partial_admission_before_mutation():
    runtime=object.__new__(w.WorkerRuntime); runtime.poisoned=False
    runtime.gather=lambda own:[own,{"phase":"admit","binding":None,"error":"expired"}]
    touched=[]
    with pytest.raises(LatentError,match="ranks"):
        runtime.preflight("admit",lambda:{"ok":True})
        touched.append(True)
    assert runtime.poisoned and not touched


def test_uniform_expiry_fails_all_without_claiming_rank_success():
    runtime=object.__new__(w.WorkerRuntime); runtime.poisoned=False
    runtime.gather=lambda own:[own]*8
    def expired(): raise LatentError("expired")
    with pytest.raises(LatentError,match="expired"):
        runtime.preflight("admit",expired)
    assert not runtime.poisoned


def test_target_class_uses_canonical_identity_not_name(monkeypatch):
    import types
    module=types.ModuleType("sglang.srt.models.glm4_moe")
    class DeepseekBase: pass
    class Canonical(DeepseekBase): pass
    module.GlmMoeDsaForCausalLM=Canonical
    monkeypatch.setitem(sys.modules,module.__name__,module)
    w.validate_target_model_class(Canonical())
    with pytest.raises(LatentError): w.validate_target_model_class(DeepseekBase())
    module.GlmMoeDsaForCausalLM=DeepseekBase  # Alias semantics also use identity.
    w.validate_target_model_class(DeepseekBase())
    impostor=type("GlmMoeDsaForCausalLM",(),{})
    with pytest.raises(LatentError): w.validate_target_model_class(impostor())


def test_initialized_kv_metadata_binds_semantic_and_physical_layout(monkeypatch):
    # Metadata-only fixture; not a CUDA allocation or native runtime proof.
    import types
    module=types.ModuleType("sglang.srt.mem_cache.memory_pool")
    class DSAPool: pass
    module.DSATokenToKVPool=DSAPool
    monkeypatch.setitem(sys.modules,module.__name__,module)
    torch=NS(float8_e4m3fn=object(),uint8=object())
    pool=DSAPool()
    pool.__dict__.update(dtype=torch.float8_e4m3fn,store_dtype=torch.uint8,use_dsa=True,
        page_size=64,kernel_page_blocks=1,layer_num=78,start_layer=0,end_layer=77,
        layer_shard_enabled=False,post_capture_active=False,kv_lora_rank=512,qk_rope_head_dim=64,
        index_head_dim=128,size=1024,kv_cache_dim=656,dsa_kv_cache_store_fp8=True,
        index_kpool=1,index_kpool_compress=False)
    pool.kv_buffer=[NS(is_cuda=True,dtype=torch.uint8,shape=(1088,1,656),is_contiguous=lambda:True)]*78
    allocator=NS(get_kvcache=lambda:pool)
    runner=NS(token_to_kv_pool=pool,token_to_kv_pool_allocator=allocator,kv_cache_dtype_str="fp8_e4m3",
        kv_cache_dtype=torch.float8_e4m3fn,model_config=NS(hf_config=NS(kv_lora_rank=512,qk_rope_head_dim=64)))
    scheduler=NS(token_to_kv_pool_allocator=allocator)
    result=w.validate_kv_layout(runner,scheduler,torch)
    assert result["semantic_dtype"]=="float8_e4m3fn" and result["storage_dtype"]=="uint8"
    assert result["shape"]==[1088,1,656] and result["page_size"]==64
    runner.kv_cache_dtype=object()
    with pytest.raises(LatentError): w.validate_kv_layout(runner,scheduler,torch)
    runner.kv_cache_dtype=torch.float8_e4m3fn
    pool.kv_buffer[0].dtype=torch.float8_e4m3fn
    with pytest.raises(LatentError): w.validate_kv_layout(runner,scheduler,torch)


def test_poisoned_runtime_cleanup_fences_without_readmitting(monkeypatch):
    desc=consume(native_rid="rid")
    events=[]; record=NS(consumer=desc,descriptor=capture())
    runtime=NS(poisoned=True,registry=NS(source_for_attempt=lambda _:record,
        retire=lambda **kw:events.append("retire")),preflight=lambda phase,check:check(),
        fence=lambda:events.append("fence"))
    scheduler=NS(occ3_runtime=runtime); monkeypatch.setattr(w,"_RUNTIME",runtime)
    r=req(desc)
    w.fence_private_kv_release(scheduler,r)
    assert events==["fence"]  # Terminal journal may follow native KV release.
    w.abort_request(scheduler,r,"abort")
    assert events==["fence","fence","retire"] and runtime.poisoned
    with pytest.raises(LatentError): w._runtime(scheduler)


def test_failed_cleanup_fence_cannot_retire_or_report_free(monkeypatch):
    desc=consume(native_rid="rid"); calls=[]
    def fail(): raise RuntimeError("synthetic CUDA event failure")
    runtime=NS(poisoned=True,registry=NS(source_for_attempt=lambda _:NS(consumer=desc),
        retire=lambda **kw:calls.append("retire")),preflight=lambda phase,check:check(),fence=fail)
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    with pytest.raises(RuntimeError): w.fence_private_kv_release(NS(occ3_runtime=runtime),req(desc))
    assert not calls and runtime.poisoned


def test_full_native_chunk_injection_retains_only_two_integer_span(monkeypatch):
    # Shape/call fixture only. Actual 384MiB CUDA allocation is root-only.
    import types
    from occ3_sglang.latent_types import RequestSlice
    module=types.ModuleType("sglang.srt.layers.layer_boundary")
    module.get_attn_tp_context=lambda:NS(input_scattered=False)
    monkeypatch.setitem(sys.modules,module.__name__,module)
    copied=[]
    class Buffer:
        is_cuda=True; dtype="bfloat16"
        def __init__(self,n): self.shape=(n,6144)
        def __getitem__(self,key):
            copied.append((self.shape[0],key.start,key.stop))
            return self
        def copy_(self,value): return self
        def record_stream(self,stream): pass
    hidden=Buffer(32768); prefix=Buffer(128)
    desc=consume(expected_prefix_tokens=128)
    record=NS(prefix=prefix,events=[],live_refs=[],injected_positions=set(),capture_commit_id="commit")
    stream=NS(wait_event=lambda event:None)
    runtime=NS(poisoned=False,profile=profile(),device="cuda",resolve=lambda d:record,
        preflight=lambda phase,check:check(),torch=NS(cuda=NS(current_stream=lambda device:stream)),event=lambda:object())
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    context=(RequestSlice(desc,0,32768,0,0,False),)
    fb=NS(occ3_context=context,occ3_force_eager=True,batch_size=1,forward_mode=NS(is_extend=lambda:True))
    w.inject_target_embeddings(fb,hidden)
    assert fb.occ3_effective_embeds==(0,128) and not record.live_refs
    assert copied==[(32768,None,128),(128,0,128)]
    assert 32768*6144*2==384*1024*1024  # No extra fullchunk clone allowed.
    w.mark_injection(fb)
    assert fb.occ3_effective_embeds is None and len(record.injected_positions)==128


def test_outside_prefix_draft_does_not_materialize_embedding(monkeypatch):
    from occ3_sglang.latent_types import RequestSlice
    desc=consume(); record=NS(prefix=NS(shape=(8,6144),is_cuda=True))
    runtime=NS(poisoned=False,profile=profile(),resolve=lambda d:record,preflight=lambda phase,check:check())
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    fb=NS(occ3_context=(RequestSlice(desc,0,32768,32768,0,False),),
        occ3_force_eager=True,batch_size=1,input_ids=NS(numel=lambda:32768))
    class Runner:
        @property
        def model(self): raise AssertionError("outside-prefix lookup must stay native")
    assert w._effective(Runner(),fb,1) is None
    assert w.prefix_overlap(128,0,32768)==128
    assert w.prefix_overlap(128,128,32768)==0
    assert w.prefix_overlap(128,127,1,1)==0


def test_retire_control_refreshes_events_but_keeps_exact_idempotency_binding():
    from occ3_sglang.latent_types import control_request
    from test_latent_registry import Event
    r,_=registry(); record=ready(r); event=Event(); record.events.append(event)
    runtime=object.__new__(w.WorkerRuntime)
    runtime.profile=profile(); runtime.registry=r; runtime.commands={}; runtime.poisoned=False
    runtime.gather=lambda value:[value]*8
    request=control_request("retire-op","retire",dict(source_handle="handle",source_session_id="source",principal_digest="a"*64))
    assert runtime.control(request)["result"]["gpu_freed"] is False
    event.done=True
    assert runtime.control(request)["result"]["gpu_freed"] is True
    different=control_request("retire-op","retire",dict(source_handle="other",source_session_id="source",principal_digest="a"*64))
    with pytest.raises(LatentError,match="replay"): runtime.control(different)


@pytest.mark.parametrize("code",["expired","retired"])
def test_uniform_expected_cancel_has_exact_binding_and_not_rank_poison(code):
    from occ3_sglang.latent_types import UniformRequestCancellation
    runtime=object.__new__(w.WorkerRuntime); runtime.poisoned=False
    runtime.gather=lambda value:[value]*8
    descriptor=capture()
    def fail(): raise LatentError(code)
    with pytest.raises(UniformRequestCancellation) as caught:
        runtime.preflight("prepare",fail,descriptor=descriptor)
    assert (caught.value.code,caught.value.native_rid,caught.value.attempt_id)==(code,"rid","attempt")
    assert not runtime.poisoned


def test_same_expiry_for_different_rank_request_cannot_be_typed_cancel():
    from occ3_sglang.latent_types import UniformRequestCancellation
    runtime=object.__new__(w.WorkerRuntime); runtime.poisoned=False
    runtime.gather=lambda value:[value,{**value,"request":"different-binding"}]
    def expired(): raise LatentError("expired")
    with pytest.raises(LatentError,match="ranks") as caught:
        runtime.preflight("prepare",expired,descriptor=capture())
    assert type(caught.value) is not UniformRequestCancellation and runtime.poisoned


def test_expiry_after_prepare_before_forward_tracks_stream_but_never_submits(monkeypatch):
    from occ3_sglang.latent_types import RequestSlice,UniformRequestCancellation
    runtime=object.__new__(w.WorkerRuntime); runtime.poisoned=False; trace=[]
    runtime.gather=lambda value:[value]*8
    runtime.event=lambda:trace.append("stream_registered")
    def expired(descriptor,check_deadline=False):
        assert check_deadline
        raise LatentError("expired")
    runtime.resolve=expired
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    class Mode:
        def __int__(self): return 1
    fb=NS(occ3_context=(RequestSlice(capture(),0,3,0,0,True),),occ3_force_eager=True,
        batch_size=1,forward_mode=Mode())
    with pytest.raises(UniformRequestCancellation): w.record_forward_start(NS(is_draft_worker=False),fb)
    assert trace==["stream_registered"]


def test_source_stage_from_stale_forward_cannot_reach_gpu_or_ring(monkeypatch):
    from occ3_sglang.latent_types import RequestSlice
    r,_=registry(); record=ready(r)
    runtime=object.__new__(w.WorkerRuntime)
    runtime.registry=r; runtime.poisoned=False; runtime.torch=object()
    runtime.gather=lambda value:[value]*8
    runtime.event=lambda:pytest.fail("stale stage must refuse before GPU work")
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    request=req(capture()); request.output_ids.append(17)
    stage=NS(committed=False,iteration=7,context=(RequestSlice(capture(),0,3,0,0,True),))
    with pytest.raises(LatentError,match="lineage"):
        w.commit_result(NS(occ3_runtime=runtime),request,NS(occ3_capture_stage=stage),0,0,1,forward_iter=8)
    assert not record.retained and record.accepted_count==0 and not stage.committed


@pytest.mark.parametrize("origin,output",[(list([1,2,3]),array("q")),(array("q",[1,2,3]),list())])
def test_admission_preserves_native_origin_container_and_normalizes_output_concat(monkeypatch,origin,output):
    descriptor=consume(native_rid="rid")
    record=NS(state="leased",admitted_consume=False,state_generation=123)
    runtime=NS(poisoned=False,resolve=lambda value,**kw:record,preflight=lambda phase,check:check())
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    request=req(); request.origin_input_ids=origin; request.output_ids=output
    w.admit_request(NS(occ3_runtime=runtime),request,descriptor)
    assert type(request.full_untruncated_fill_ids) is type(origin)
    assert list(request.full_untruncated_fill_ids)==[0]*8+[1,2,3]


def test_cleanup_only_fence_clears_partial_inflight_before_physical_reap(monkeypatch):
    from test_latent_registry import Event
    r,_=registry(); record=ready(r); r.publish(publication()); descriptor=consume()
    r.reserve_consume(descriptor)
    runtime=object.__new__(w.WorkerRuntime)
    runtime.registry=r; runtime.poisoned=True; runtime.gather=lambda value:[value]*8
    runtime.event=lambda:Event(done=True); calls=[]
    runtime.fence=lambda:calls.append("actual-fence-fixture")
    w._begin_gpu(runtime,record)
    assert record.inflight==1
    assert r.retire(handle="handle")["gpu_freed"] is False
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    request=req(descriptor); request.rid=descriptor.native_rid
    w.fence_private_kv_release(NS(occ3_runtime=runtime),request)
    assert calls==["actual-fence-fixture"] and record.inflight==0
    r.reap(); assert record.gpu_freed and r.charged_bytes==0


def test_actual_prepare_clock_crosses_between_verify_rounds_and_cleanup_is_exact(monkeypatch):
    from occ3_sglang.latent_types import UniformRequestCancellation
    r,_=registry(); record=ready(r); record.state="capturing"
    runtime=object.__new__(w.WorkerRuntime)
    runtime.registry=r; runtime.poisoned=False; runtime.gather=lambda value:[value]*8
    fences=[]; runtime.fence=lambda:fences.append("fence")
    now=[100.0]
    monkeypatch.setattr(w.time,"time",lambda:now[0])
    monkeypatch.setattr(w.time,"monotonic",lambda:now[0])
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    scheduler=NS(occ3_runtime=runtime); request=req(capture())
    batch=NS(reqs=[request],forward_mode=NS(is_extend=lambda:False))
    w.prepare_batch(scheduler,batch)
    assert batch.occ3_context[0].output_start==0
    request.output_ids.append(17); now[0]=401.0
    with pytest.raises(UniformRequestCancellation) as caught:
        w.prepare_batch(scheduler,batch)
    assert caught.value.native_rid==request.rid and not runtime.poisoned
    w.abort_request(scheduler,request,"expired")
    assert fences==["fence"] and record.revoked and record.gpu_freed


def test_failed_cleanup_cannot_clear_inflight_guard(monkeypatch):
    from test_latent_registry import Event
    r,_=registry(); record=ready(r); record.inflight=2; record.events=[Event(done=True)]
    runtime=object.__new__(w.WorkerRuntime)
    runtime.registry=r; runtime.poisoned=True; runtime.gather=lambda value:[value]*8
    def fail(): raise RuntimeError("actual fence fixture failed")
    runtime.fence=fail
    monkeypatch.setattr(w,"_RUNTIME",runtime)
    with pytest.raises(LatentError): w.abort_request(NS(occ3_runtime=runtime),req(capture()),"cancel")
    r.retire(handle="handle")
    assert record.inflight==2 and not record.gpu_freed and r.charged_bytes==4096
