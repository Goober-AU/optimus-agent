"""Actual Torch references when available. Never substitute fake Torch math."""
import importlib.util
import pytest

from occ3_sglang.statebridge import align_hidden_sequence, anchor_tiled, workspace_bound
from occ3_sglang.latent_types import LatentError


@pytest.fixture
def real_torch():
    try:
        spec = importlib.util.find_spec("torch")
    except (ValueError, ImportError):
        spec = None
    if spec is None:
        pytest.skip("Actual Torch not installed; no numerical proof from stubs")
    import torch
    if not hasattr(torch, "linalg"):
        pytest.skip("Existing suite Torch stub is not numerical validation")
    return torch


def test_workspace_units_aggregate():
    assert workspace_bound(128,6144,8,256,0) <= 128*1024*1024
    assert workspace_bound(128,6144,8,256,0.3) > 128*1024*1024
    assert workspace_bound(128,6144,8,128,0.3) <= 128*1024*1024


@pytest.mark.parametrize("n", [8,16,32,64,128])
def test_real_cpu_alignment_shape_norm_and_no_dense_square(real_torch, n):
    t = real_torch; t.manual_seed(17)
    d = max(512, n*4)
    h,e = t.randn(n,d),t.randn(n,d)
    z, info = align_hidden_sequence(h,e,3.0)
    assert z.shape == h.shape and info.union_rank <= 2*(n-1)
    assert t.allclose(z.norm(dim=-1),t.full((n,),3.0),atol=2e-5)
    assert t.isfinite(z).all()


def test_real_cpu_refuses_zero_information(real_torch):
    t=real_torch
    with pytest.raises(LatentError):
        align_hidden_sequence(t.ones(8,64),t.ones(8,64),1)


def test_reduced_dense_reference_observed_rows(real_torch):
    t=real_torch; t.manual_seed(18)
    h,e=t.randn(8,48),t.randn(8,48)
    z,_=align_hidden_sequence(h,e,1.0)
    hc,ec=h-h.mean(0),e-e.mean(0)
    def power(a,p):
        v,q=t.linalg.eigh(a)
        return (q*v.clamp_min(1e-6).pow(p))@q.T
    eye=t.eye(48)
    ch,ce=hc.T@hc/8+1e-3*eye,ec.T@ec/8+1e-3*eye
    x,y=hc@power(ch,-.5),ec@power(ce,-.5)
    u,_,v=t.linalg.svd(x.T@y)
    reference=x@(u@v)@power(ce,.5)+e.mean(0)
    reference=reference/reference.norm(dim=-1,keepdim=True)
    assert t.allclose(z,reference,atol=3e-3,rtol=3e-3)
