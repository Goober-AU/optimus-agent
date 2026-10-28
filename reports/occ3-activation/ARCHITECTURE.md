# OCC-3 activation architecture

## 1. Layers, and which layer owns what

```
settings            agentCommunication.occ3Enabled / occ3Experimental / occ3Endpoint
models.json         model occ3 block: protocol, connector, endpoint, sameModelOnly,
                    enabled, validation, fallback
                    |
                    v
activation          core/occ3_connectors/activation.rs   (I/O layer: network allowed)
                    gate ladder -> live readiness handshake -> verified connector
                    |
                    v
bridge              core/occ/occ3/                          (pure: no network, no clocks)
                    types, connector trait, registry, negotiation, codec
                    |
                    v
session             core/agent_session/occ3_state.rs       Occ3NativeSession
                    enrollment, source capture, receiver admission, receipts
                    |
                    v
transport           core/occ3_connectors/sglang.rs         authenticated HTTP + SSE
                    |
                    v
runtime             DGX B300 GLM-5.3 FP8 TP8, SGLang 0.5.21, occ3_sglang extension
                    |
                    v
activity            core/occ/activity.rs -> roster metadata -> communication indicator
```

The bridge tree stays pure. Activation lives outside it because it performs I/O, which is why it
was placed in `core/occ3_connectors/` rather than `core/occ/occ3/`.

## 2. What activation changes

Activation enrolls a session and installs two things: the receiver resolver and source capture
hooks on the agent, and the `occ3_handoff` tool on the session. It does not change the model, the
provider, the transport, or the ordinary text path.

## 3. Where the indicator fits

The indicator is not a new subsystem. It is the existing `ActivityState` pulse array with a fourth
slot, fed by `CommunicationKind::Latent` from the existing roster projection, which reads the
existing content-free `communicationActivity` metadata. Nothing new crosses the daemon wire: the
`CommunicationDelivery` shape is unchanged, and `latent` was already a valid `kind` value.

## 4. Deployment

`extensions/sglang-occ3/deploy/v3/` supervises the GPU side: plan validation, original container
preservation, candidate build and attestation, readiness gates, journal, recovery. The persistence
policy decides what happens at the end of a window, and only the service-health signals can force a
restore.

## 5. Boundaries that are deliberately not crossed

- No new tensor-sharing service. The connector and the existing extension carry state.
- The frozen `AgentCommMessage` contract is unchanged.
- No KV-cache communication, no fine-tuning, no learned adapter, no weight changes.
- No second configuration system: the new keys live under the existing `agentCommunication`
  object, and the model capability lives in the existing `occ3` block.
