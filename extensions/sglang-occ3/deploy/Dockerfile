# Dockerfile -- occ3-bridge SGLang plugin, additive deployment layer for
# the neo-glm53 serving container (NVIDIA DGX B300 host).
#
# The derived image is the EXACT base image the neo-glm53 container runs,
# plus exactly one change: the occ3-bridge pip package installed into the
# same Python environment that runs the SGLang server. The plugin registers
# the entry point `occ3_bridge` in the group `sglang.srt.plugins`, which
# SGLang auto-discovers at process start. There is no hot-load path, so the
# server process must be restarted once (deploy.sh handles that).
#
# Layer size: the plugin adds only its own source (a few dozen KB);
# --no-deps keeps pip from pulling anything else (torch, msgspec and
# fastapi already come from the base image). No apt installs.
#
# BASE_IMAGE must be the digest-pinned reference of the base image the
# container is CURRENTLY running. Do NOT hardcode a digest here: read the
# full digest at runtime on the DGX host, e.g.
#
#   docker inspect --format '{{index .RepoDigests 0}}' neo-glm53
#
# and pass it as a build argument:
#
#   docker build --build-arg BASE_IMAGE=lmsysorg/sglang@sha256:<full-digest> \
#       -f deploy/Dockerfile .
#
# Digest verified on the target DGX during the 2026-10-21 inspection:
#   lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469
# (52.4 GB; SGLang 0.5.21 serving GLM-5.3 FP8, TP8). deploy.sh re-reads the
# digest from docker inspect at deploy time and overrides the default below,
# which intentionally stays on the moving tag.

ARG BASE_IMAGE=lmsysorg/sglang:latest
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="neo-glm53 + occ3-bridge" \
      org.opencontainers.image.description="lmsysorg/sglang base with the occ3-bridge SGLang plugin installed (additive layer; no other changes)" \
      org.opencontainers.image.base.name="lmsysorg/sglang"

# The base image runs SGLang from a venv at /opt/sglang (its PATH is
# inherited), so plain `python3` below is the same interpreter that runs
# the server; the plugin must land in that environment to be discovered.
WORKDIR /opt/occ3-bridge

# Build context is the sglang-occ3 extension root (the parent of deploy/):
# ../occ3_sglang and ../pyproject.toml relative to this file.
# README.md is referenced by pyproject.toml; the glob keeps the build
# working while it is absent from the tree (a placeholder is written in
# its place below -- metadata only, the package is unaffected).
COPY occ3_sglang/ ./occ3_sglang/
COPY pyproject.toml README.md* ./

# --no-deps: the plugin declares no dependencies of its own; everything it
# imports is provided by the base image.
# --no-cache-dir: keep the added layer small.
# The build backend (setuptools) is used from the image when present so the
# build stays offline; otherwise pip falls back to an isolated build, which
# needs network access to fetch setuptools.
RUN if [ ! -f README.md ]; then \
        printf '# occ3-bridge\n\nOCC-3 latent communication plugin for SGLang (salted KV-radix channels).\n' > README.md; \
    fi && \
    if python3 -c 'import setuptools' 2>/dev/null; then \
        python3 -m pip install --no-deps --no-cache-dir --no-build-isolation .; \
    else \
        python3 -m pip install --no-deps --no-cache-dir .; \
    fi
