# OCC-3 Security

**Status: STUB — awaiting live evidence.** The security design exists and an initial independent safety review of the V2/V3 controller boundary is on file, but a security report for this campaign must cover the deployed, live extension (authentication, isolation, resource limits under real load), which does not exist yet.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. This stub contains an outline and evidence inventory only; per the specification, documentation must contain actual evidence, and no empty report should be created merely to satisfy the filename checklist, so substantive sections are deferred until the required evidence exists.

## Planned outline

1. Threat model: privileged administrative key holder, cross-agent isolation, handle leakage, session/restart staleness.
2. Trust boundaries: authenticated daemon broker vs server-owned enrollment table; principal extraction from middleware, never from occ3 body fields.
3. Transport security: authenticated path (api key / control headers), no credential rotation, no new credential transport.
4. Control-plane hardening: header allowlist and value hygiene in the Rust connector; fail-closed routes; 410 legacy APIs.
5. Resource limits: capture byte budgets across TP ranks, TTLs, concurrency caps, header ceilings.
6. Audit and logging rules: content-free journals, digest-only receipts, redaction discipline (and the recorded credential-fragment incidents and their corrections).

## Evidence already available (offline)

- Trust-boundary and authorization decisions: occ3-campaign/v3-controlled-deploy/SOURCE-PROTOCOL-DECISIONS.md (sha256 b90f77916a1126027b7db0a401fda6a6b4ad94bce3eee4f840be3b1e3e9dd5e4).
- Initial independent safety review (S01-Sxx findings, V3 closure requirements): occ3-campaign/v3-controlled-deploy/INDEPENDENT-SAFETY-REVIEW.md (sha256 636d5d467d6543a913b7c09cb79f5c036b92504955982803154cd6aa61fc7bc1).
- Connector control-header allowlist and sensitive-value handling: crates/pi-coding-agent/src/core/occ3_connectors/sglang.rs (sha256 64d8cb468687ec1c084d0aab02e89ff768eca18b1bb201dd0b3326755edd9250).
- Plugin route authentication posture: extensions/sglang-occ3/occ3_sglang/api.py (sha256 e45b3b77c6fbff798f67815ef5b992066e1f0900d114632bd28f89afb146a9b1).
- Credential-handling rules and recorded incidents: occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md and occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md (hashes as in ROLLBACK.md).

## Evidence needed before this report can be drafted (PENDING LIVE EVIDENCE)

1. Security review of the final V3 controller implementation (the initial review covered the baseline and demanded a second review after integration).
2. Live verification that the deployed extension enforces authentication and session-table membership on every /occ3/v1 route.
3. Handle leakage / session staleness / expiry tests against the real runtime.
4. Resource-limit behaviour under real load (full-MAX reasoning turns, 131072 default output budget).

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256. This stub adds no register rows of its own because it asserts no new claims.
