# OCC-3 Cache Isolation

**Status: STUB — awaiting live evidence.** The contamination mechanism is fully mapped at source level and the isolation design (salt namespaces, skip-insert, private pages) exists, but no live cache-isolation measurement has been performed on the deployed server; the extension is not installed there.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Radix cache key structure on the deployed runtime (RadixKey, salted child keys, EAGLE bigram views).
2. The contamination risk: override requests insert under plain token ids; lookup disabled but insert not.
3. Isolation levers: cache_salt namespace, extra_key, skip_radix_cache_insert, session pinning; private KV pages out of shared-cache reuse.
4. Live verification protocol: normal traffic hit-rate unchanged; salted channel reachable only with the salt; contamination probes negative.
5. Cache safety gate (spec GATE 6) evidence mapping.

## Evidence already available (offline)

- Exact source-level analysis of lookup/insert behaviour and isolation levers: occ3-campaign/research/sglang-audit.md (sha256 e48d08488fa65f9bc664f5aba30cedd6f3dd38ec3bcbfbebe14e4878efdb3049).
- Scheduler cache IPC overlay: extensions/sglang-occ3/patches/scheduler-cache-ipc.v1.json (sha256 e07c4b84407733a277f7135a017d0e505e275846beedd28f68a303ab97fe275c).
- Extension cache patch tests (offline): extensions/sglang-occ3/tests/test_latent_cache_patch.py (sha256 999603d2f14dc5f7ccf5dcbb260a2870ab0064dfee4165da77128f2800e8d1c2) and the related latent-patch suites under extensions/sglang-occ3/tests/.
- Design statement on private pages: extensions/sglang-occ3/README.md (sha256 3fe79fadd0a9e5e55cac27e1feca1ee7431d71ec53579f141a920ac9bd454702).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. Live measurements on the DGX: radix hit rates for normal traffic before/after extension install.
2. A live demonstration that a salted latent channel is invisible to default-namespace requests and vice versa.
3. A live contamination probe (identical token ids, salted vs unsalted) with negative result.
4. Eviction-window behaviour under real agent traffic (cached_tokens logging).

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
