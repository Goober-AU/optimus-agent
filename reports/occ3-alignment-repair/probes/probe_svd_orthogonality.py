
"""Probe 6: is the d x d orthogonal factor from a rank-deficient SVD actually
orthogonal, and does det() survive at large d? Cheap: small d, same driver.
"""
import torch, json
out = {}
for (d, r) in ((256, 15), (1024, 15), (2048, 15), (4096, 15)):
    g = torch.Generator().manual_seed(1)
    L = torch.randn(d, r, generator=g)
    Rr = torch.randn(r, d, generator=g)
    M = L @ Rr                      # rank r, d x d
    U, S, Vt = torch.linalg.svd(M, full_matrices=False)
    R = U @ Vt
    ortho_u = float((U.T @ U - torch.eye(d)).abs().max())
    zero_cols = int((U.norm(dim=0) < 0.5).sum())
    det = float(torch.det(R))
    sv = S.detach()
    out[f"d{d}"] = dict(rank=r, orthonormality_err_max=round(ortho_u, 6),
                        zero_norm_columns_of_U=zero_cols,
                        det_R=det, sigma_15=float(sv[r - 1]), sigma_16=float(sv[r]),
                        sigma_max=float(sv[0]))
print(json.dumps(out, indent=2))
