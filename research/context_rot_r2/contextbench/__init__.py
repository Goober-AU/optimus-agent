"""ContextBench offline evaluation pipeline (rot2 worker W10).

Implements the offline-capable parts of the ContextBench evaluation design
(research/context_rot_r2/CONTEXTBENCH-EVAL-DESIGN.md):

- dataset preparation over the pinned ContextBench checkout, with a
  deterministic repo-level dev/holdout split (no repo overlap);
- gold-context metrics (file and span precision/recall) mirroring the
  upstream formulas;
- a matched A/B harness scaffold for retrieval-policy arms.

Everything in this package is deterministic and offline. Model-dependent
quantities (agent runs, patch success) are reported as UNAVAILABLE, never
fabricated.

Metric formulas are mirrors of the upstream ContextBench implementation
(Apache-2.0) at commit 1436c28a8eb95496da4ea69ad458b9f8a8eb7d61; each
mirrored function cites the upstream file and lines. The upstream tree is
read-only for us; nothing is imported from it at runtime.
"""

UPSTREAM_REPO_URL = "https://github.com/EuniAI/ContextBench"
UPSTREAM_COMMIT = "1436c28a8eb95496da4ea69ad458b9f8a8eb7d61"
UPSTREAM_LICENSE = "Apache-2.0"
DATASET_URL = "https://huggingface.co/datasets/Contextbench/ContextBench"

# The dataset mirror ships inside the pinned checkout, so no Hugging Face
# download is required. This is the only network waiver this package relies
# on, and in practice even that is unused: everything is read from the local
# clone.
GOLD_PARQUET_RELPATH = "data/contextbench_verified.parquet"
GOLD_PARQUET_SHA256 = (
    "e9dcfd504cbfb849ac815a79040c793d0d92f94eecc9b5a4ee3e1445a2f8a791"
)
GOLD_PARQUET_ROWS = 500
GOLD_PARQUET_REPOS = 58

# Offline marker for quantities that require a live model run.
PATCH_SUCCESS_UNAVAILABLE = "UNAVAILABLE"

SPLIT_VERSION = "w10.1"
