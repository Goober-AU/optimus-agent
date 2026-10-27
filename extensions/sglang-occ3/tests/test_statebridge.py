"""Actual Torch references when available. Never substitute fake Torch math."""
import importlib.util
import hashlib
import os
import textwrap
import types

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


REFERENCE_ENV = "OCC3_STATEBRIDGE_REFERENCE"


class _EmbeddingShim:
    """Minimal input-embedding stand-in for the dense reference method."""

    def __init__(self, weight):
        self.weight = weight
        self.num_embeddings, self.embedding_dim = weight.shape

    def __call__(self, token_ids):
        return self.weight[token_ids]


def _dense_reference_align(torch):
    """Execute the pinned StateBridge _align_hidden_sequence source verbatim.

    The reference checkout is external evidence, not a vendored dependency, so a
    missing file skips rather than weakening the assertion.
    """
    path = os.environ.get(REFERENCE_ENV)
    if not path or not os.path.exists(path):
        pytest.skip("Pinned StateBridge reference checkout not present")
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.lstrip().startswith("def _align_hidden_sequence(")), None)
    assert start is not None, "Configured reference lacks _align_hidden_sequence"
    stop = next((i for i, line in enumerate(lines[start + 1:], start + 1)
                 if line.startswith("    def ")), len(lines))
    source = textwrap.dedent("\n".join(lines[start:stop]))
    assert hashlib.sha256(source.encode("utf-8")).hexdigest() == "13300494436f3cf50159d034e4215afc2b05f20c811546c4642de9a3fac62817"
    namespace = {"torch": torch, "F": torch.nn.functional}
    exec(compile(source, path, "exec"), namespace)
    return namespace["_align_hidden_sequence"]


def _full_rank_hidden(torch, n, d, seed, scale):
    """Full centered row rank stand-in with a decaying spectrum."""
    g = torch.Generator().manual_seed(seed)
    frame = torch.linalg.qr(torch.randn(d, n, generator=g))[0].mT
    rotation = torch.linalg.qr(torch.randn(n, n, generator=g))[0]
    spectrum = torch.tensor([(i + 1.0) ** -1.0 for i in range(n)])
    hidden = (rotation @ torch.diag(spectrum / spectrum[0])).mT @ frame
    return hidden / hidden.norm(dim=-1, keepdim=True).mean() * scale


def test_thin_solver_matches_executed_dense_statebridge_reference(real_torch):
    """The thin union solver must reproduce the pinned dense method, not only a mirror.

    This controlled fixture has full centered row rank and distinct token
    references. Its fitted-row mapping must agree to float32 solver noise;
    real GLM captures may have repeated tokens and lower rank.
    """
    t = real_torch
    align_reference = _dense_reference_align(t)
    n, d, vocab = 32, 256, 512
    weight = t.randn(vocab, d, generator=t.Generator().manual_seed(20261027))
    weight = weight / weight.norm(dim=-1, keepdim=True).mean() * 1.5
    embedding = _EmbeddingShim(weight)
    target_norm = float(weight.norm(dim=-1).mean())
    tokens = t.randperm(vocab, generator=t.Generator().manual_seed(7))[:n]
    hidden = _full_rank_hidden(t, n, d, seed=11, scale=22.0)
    host = types.SimpleNamespace(device=t.device("cpu"), embedding_layer=embedding,
                                 dtype=t.float32, target_norm=target_norm,
                                 adaptive_reg=1e-3, snap_ratio=0.0, vocab_embeds=weight)
    dense = align_reference(host, hidden.unsqueeze(0), tokens.unsqueeze(0)).reshape(n, d).float()
    thin, info = align_hidden_sequence(hidden, weight[tokens], target_norm)
    assert len(set(tokens.tolist())) == n
    assert info.union_rank == 2 * (n - 1)
    relative = float((thin.float() - dense).norm() / dense.norm())
    assert relative < 1e-3, relative
