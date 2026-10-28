# OCC-3 model compatibility and activation gates

## 1. The rule

OCC-3 is same-exact-model only. Two agents may use OCC-3 only when both `Occ3ModelIdentity` values compare
equal component by component, and both sessions are enrolled against the same authenticated runtime.
Identical display names prove nothing.

## 2. Where each gate lives

`crates/pi-coding-agent/src/core/occ3_connectors/activation.rs` decides whether a session may use OCC-3 at
all. It is the I/O layer, not the pure bridge tree, so it may use the network.

| Gate | Check | Decline code |
|---|---|---|
| policy | `agentCommunication.occ3Enabled` is true | `disabled_by_policy` |
| policy | `agentCommunication.occ3Experimental` is true | `experimental_ack_required` |
| configuration | the model has an `occ3` block | `not_configured` |
| configuration | `occ3.enabled` is true | `disabled_on_model` |
| configuration | `protocol == "occ3-v1"` and `apiVersion == "v1"` | `unsupported_protocol` |
| configuration | `sameModelOnly` is true | `same_model_only_required` |
| configuration | `occ3.validation == live-verified` | `not_live_verified` |
| configuration | `occ3.connector` has an implementation | `unknown_connector` |
| configuration | endpoint is absolute http(s), no userinfo, query or fragment | `invalid_endpoint` |
| readiness | authenticated `discover_capabilities()` succeeds inside 5 s | `connector_unavailable` |
| readiness | the runtime advertises the requested method | `method_unsupported` |
| readiness | the runtime's verified `model_id` equals the session's model id | `model_identity_mismatch` |

`Occ3NativeSession::enroll` then repeats identity binding against the enrollment and the connector's cached
capabilities, and the negotiation ladder repeats the peer-advertisement check. Three independent checks, not
one.

## 3. What is deliberately not evidence

The activation module never infers support from a model name, a provider string, the string "GLM", a shared
tokenizer, an equal hidden dimension, an OpenAI-compatible endpoint shape, an Ollama endpoint, or a cached
capability recorded from a different runtime. There is no code path where a name match enables the route.

## 4. Why `live-verified` is a gate rather than a formality

`models.json` records the operator's evidence state for the route. `unverified` and `documentation-verified`
are real values that mean the handshake has not been recorded, and activation refuses them with a named code
that tells the operator what to do next. The check is deliberately not satisfied by the handshake alone,
because the handshake proves the runtime answers, not that the operator intended this model to use it.

## 5. Runtime identity of the current deployment (read-only, unchanged by this PR)

Collected from the running DGX deployment:

- host `TRY-72998-gpu01`, 8x NVIDIA B300 SXM6
- container `neo-glm53`, image `sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469`
- served model `glm-5.3`, architecture `GlmMoeDsaForCausalLM`, hidden 6144, 78 hidden layers
- `--tp-size 8`, `--context-length 1048576`, FP8 KV cache, EAGLE speculative decoding, `reasoning_effort: max`
- SGLang 0.5.21

The expected cross-language identity fingerprint for that runtime is
`sha256:db2c8830820da8bd3370bbfacf0799e33085ef7b8b1ca27840b88e3e8101b732`, as recorded in
`reports/occ3/MODEL-COMPATIBILITY.md`. Identity is verified to download-metadata depth, not byte depth; that
limitation is unchanged and is not papered over here.

## 6. Fallback

An activation decline is a clean no-op. The session keeps its ordinary text, OCC-1 and OCC-2 behaviour, the
`occ3_handoff` tool is not installed, and no private message is replayed through a fallback channel. A
decline is logged as a reason code plus session id only: no endpoint, no key, no handle.
