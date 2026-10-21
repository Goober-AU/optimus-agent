//! OCC-3 eligibility negotiation: the deterministic gate ladder that
//! decides whether a message may use latent communication.
//!
//! Pure function of its inputs (mirrors `selection.rs` policy style):
//! no I/O, no clocks, no connector calls. The caller supplies the
//! cached capability state; live drift surfaces at send time and is
//! classified into a fallback by the error taxonomy.
//!
//! # Decision order
//!
//! 1. OCC-3 disabled by configuration or policy -> fall back.
//! 2. Sender or receiver capability absent -> fall back.
//! 3. Model identities differ in ANY component -> fall back
//!    (strict same-exact-model rule; no cross-model projection).
//! 4. No connector registered with verified capabilities for the
//!    identity and method -> fall back.
//! 5. Envelope budget: the encoded OCC-3 envelope plus control
//!    summary exceeds the transport bound -> fall back (R1: never
//!    truncate).
//! 6. All gates pass -> OCC-3 with the negotiated method.

use crate::core::occ::dialect::OccCapability;

use super::registry::Occ3ConnectorRegistry;
use super::types::{Occ3Method, Occ3ModelIdentity};

/// Everything the ladder needs to decide. Built by the caller from
/// session state, peer advertisement and cached connector snapshots.
#[derive(Debug, Clone)]
pub struct Occ3NegotiationInput {
    /// User/session-level enable flag (defaults OFF until activation).
    pub enabled: bool,
    /// Sender's verified model identity (from its connector).
    pub sender_identity: Option<Occ3ModelIdentity>,
    /// Receiver's advertised model identity (from peer capability).
    pub receiver_identity: Option<Occ3ModelIdentity>,
    /// Peer OCC capability advertisement; absent field = not OCC-3.
    pub peer_occ3: Option<Occ3CapabilityAdvertisement>,
    /// Preferred latent method.
    pub method: Occ3Method,
    /// Live connector registry with cached capability snapshots.
    pub registry: Occ3ConnectorRegistry,
    /// Transport envelope budget in bytes (DEFAULT_AGENT_MESSAGE_MAX_CHARS).
    pub max_envelope_chars: usize,
    /// Size of the control summary that would be embedded.
    pub control_summary_chars: usize,
}

/// The OCC-3 slice of a peer's `OccCapability` advertisement. Kept as a
/// separate struct so `dialect.rs` can embed it with `#[serde(default)]`
/// for backward compatibility with older peers.
#[derive(Debug, Clone, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
pub struct Occ3CapabilityAdvertisement {
    /// Peer reports OCC-3 protocol support.
    pub supported: bool,
    /// Peer's verified model identity fingerprint, if advertised.
    pub model_fingerprint: Option<String>,
    /// Peer's connector id, if advertised.
    pub connector_id: Option<String>,
    /// Methods the peer can consume.
    pub methods: Vec<Occ3Method>,
}

/// Outcome of the ladder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Occ3Decision {
    /// Use OCC-3 with this method via this connector.
    Occ3 {
        method: Occ3Method,
        connector_id: String,
        model_fingerprint: String,
    },
    /// Do not use OCC-3; the existing selection path decides OCC-1/2/text.
    Fallback { reason: Occ3FallbackReason },
}

/// Why OCC-3 was rejected (surfaced to diagnostics, never silent).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Occ3FallbackReason {
    DisabledByPolicy,
    SenderCapabilityAbsent,
    ReceiverCapabilityAbsent,
    ModelIdentityMismatch,
    NoConnector,
    EnvelopeBudgetExceeded,
}

impl std::fmt::Display for Occ3FallbackReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Occ3FallbackReason::DisabledByPolicy => "occ3 disabled by policy",
            Occ3FallbackReason::SenderCapabilityAbsent => "sender occ3 capability absent",
            Occ3FallbackReason::ReceiverCapabilityAbsent => "receiver occ3 capability absent",
            Occ3FallbackReason::ModelIdentityMismatch => "model identity mismatch",
            Occ3FallbackReason::NoConnector => "no verified occ3 connector",
            Occ3FallbackReason::EnvelopeBudgetExceeded => "envelope budget exceeded",
        };
        f.write_str(s)
    }
}

/// Run the gate ladder. Deterministic; see module docs.
pub fn negotiate_occ3(input: &Occ3NegotiationInput) -> Occ3Decision {
    // 1. Policy gate.
    if !input.enabled {
        return decline(Occ3FallbackReason::DisabledByPolicy);
    }

    // 2. Capability presence.
    let Some(peer) = input.peer_occ3.as_ref() else {
        return decline(Occ3FallbackReason::ReceiverCapabilityAbsent);
    };
    if !peer.supported {
        return decline(Occ3FallbackReason::ReceiverCapabilityAbsent);
    }
    let Some(sender_identity) = input.sender_identity.as_ref() else {
        return decline(Occ3FallbackReason::SenderCapabilityAbsent);
    };

    // 3. Strict same-exact-model rule: every component must compare
    // equal. Receiver identity comes from its advertisement; connector
    // verification happens at consume time server-side.
    let Some(receiver_identity) = input.receiver_identity.as_ref() else {
        return decline(Occ3FallbackReason::ModelIdentityMismatch);
    };
    if sender_identity != receiver_identity {
        return decline(Occ3FallbackReason::ModelIdentityMismatch);
    }
    let fingerprint = sender_identity.fingerprint();

    // The peer must advertise the exact same fingerprint.
    if peer.model_fingerprint.as_deref() != Some(fingerprint.as_str()) {
        return decline(Occ3FallbackReason::ModelIdentityMismatch);
    }

    // The peer must consume the method.
    if !peer.methods.contains(&input.method) {
        return decline(Occ3FallbackReason::ModelIdentityMismatch);
    }

    // 4. Connector with verified capabilities for this identity.
    let connector_ids = input.registry.connectors_for(input.method, &fingerprint);
    let connector_id = match connector_ids
        .iter()
        .find(|id| peer.connector_id.as_deref() == Some(id.as_str()))
    {
        Some(id) => id.clone(),
        None => return decline(Occ3FallbackReason::NoConnector),
    };

    // 5. Envelope budget (R1: never truncate).
    // Conservative estimate: envelope overhead ~1.2 KiB of JSON
    // scaffolding plus the handle reference and control summary.
    let estimated = 1200 + input.control_summary_chars;
    if estimated > input.max_envelope_chars {
        return decline(Occ3FallbackReason::EnvelopeBudgetExceeded);
    }

    // 6. All gates pass.
    Occ3Decision::Occ3 {
        method: input.method,
        connector_id,
        model_fingerprint: fingerprint,
    }
}

fn decline(reason: Occ3FallbackReason) -> Occ3Decision {
    Occ3Decision::Fallback { reason }
}

/// Extract the OCC-3 advertisement from a peer `OccCapability`.
/// Older peers without the field degrade to "not supported".
pub fn advertisement_from_capability(capability: &OccCapability) -> Occ3CapabilityAdvertisement {
    capability.occ3.clone().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity(model_id: &str) -> Occ3ModelIdentity {
        Occ3ModelIdentity {
            model_id: model_id.to_string(),
            architecture: "GlmMoeDsaForCausalLM".into(),
            checkpoint_revision: "aca966e4".into(),
            weight_fingerprint: "w1".into(),
            tokenizer_fingerprint: "t1".into(),
            chat_template_fingerprint: "c1".into(),
            quantisation: "fp8-e4m3".into(),
            tensor_geometry: "6144".into(),
            kv_layout: "dsa".into(),
            latent_method: "prefix-v1".into(),
            runtime: "sglang-0.5.21".into(),
        }
    }

    fn input_with(
        enabled: bool,
        sender: Option<Occ3ModelIdentity>,
        receiver: Option<Occ3ModelIdentity>,
        peer: Option<Occ3CapabilityAdvertisement>,
    ) -> Occ3NegotiationInput {
        Occ3NegotiationInput {
            enabled,
            sender_identity: sender,
            receiver_identity: receiver,
            peer_occ3: peer,
            method: Occ3Method::KvHandoff,
            registry: Occ3ConnectorRegistry::empty(),
            max_envelope_chars: 16384,
            control_summary_chars: 400,
        }
    }

    fn full_peer(fp: &str) -> Occ3CapabilityAdvertisement {
        Occ3CapabilityAdvertisement {
            supported: true,
            model_fingerprint: Some(fp.to_string()),
            connector_id: Some("mock-occ3".into()),
            methods: vec![Occ3Method::KvHandoff, Occ3Method::StatebridgePrefix],
        }
    }

    #[test]
    fn disabled_by_policy_falls_back() {
        let d = negotiate_occ3(&input_with(
            false,
            Some(identity("glm-5.3")),
            Some(identity("glm-5.3")),
            Some(full_peer("x")),
        ));
        assert!(matches!(
            d,
            Occ3Decision::Fallback {
                reason: Occ3FallbackReason::DisabledByPolicy
            }
        ));
    }

    #[test]
    fn missing_peer_capability_falls_back() {
        let d = negotiate_occ3(&input_with(
            true,
            Some(identity("glm-5.3")),
            Some(identity("glm-5.3")),
            None,
        ));
        assert!(matches!(
            d,
            Occ3Decision::Fallback {
                reason: Occ3FallbackReason::ReceiverCapabilityAbsent
            }
        ));
    }

    #[test]
    fn different_model_is_rejected_even_with_same_name() {
        // Same model_id, different checkpoint: strict rule rejects.
        let a = identity("glm-5.3");
        let mut b = identity("glm-5.3");
        b.checkpoint_revision = "different".into();
        let d = negotiate_occ3(&input_with(
            true,
            Some(a),
            Some(b),
            Some(full_peer("ignored")),
        ));
        assert!(matches!(
            d,
            Occ3Decision::Fallback {
                reason: Occ3FallbackReason::ModelIdentityMismatch
            }
        ));
    }

    #[test]
    fn cross_model_is_rejected() {
        let d = negotiate_occ3(&input_with(
            true,
            Some(identity("glm-5.3")),
            Some(identity("deepseek-v4.1")),
            Some(full_peer("ignored")),
        ));
        assert!(matches!(
            d,
            Occ3Decision::Fallback {
                reason: Occ3FallbackReason::ModelIdentityMismatch
            }
        ));
    }

    #[test]
    fn fingerprint_mismatch_in_advertisement_rejected() {
        let id = identity("glm-5.3");
        let fp = id.fingerprint();
        let d = negotiate_occ3(&input_with(
            true,
            Some(id.clone()),
            Some(id.clone()),
            Some(full_peer(&format!("not-{fp}"))),
        ));
        assert!(matches!(
            d,
            Occ3Decision::Fallback {
                reason: Occ3FallbackReason::ModelIdentityMismatch
            }
        ));
    }

    #[test]
    fn no_connector_falls_back() {
        let id = identity("glm-5.3");
        let fp = id.fingerprint();
        let d = negotiate_occ3(&input_with(
            true,
            Some(id.clone()),
            Some(id),
            Some(full_peer(&fp)),
        ));
        assert!(matches!(
            d,
            Occ3Decision::Fallback {
                reason: Occ3FallbackReason::NoConnector
            }
        ));
    }

    #[test]
    fn oversized_summary_falls_back() {
        let id = identity("glm-5.3");
        let fp = id.fingerprint();
        let mut input = input_with(
            true,
            Some(id.clone()),
            Some(id.clone()),
            Some(full_peer(&fp)),
        );
        // Register a connector so the earlier gates pass and the budget
        // check is the deciding gate.
        let connector = crate::core::occ::occ3::mock::MockOcc3Connector::new(
            id.clone(),
            vec![Occ3Method::KvHandoff],
        );
        let caps = connector.capabilities();
        input
            .registry
            .register(std::sync::Arc::new(connector), caps);
        input.control_summary_chars = 16384;
        let d = negotiate_occ3(&input);
        assert!(matches!(
            d,
            Occ3Decision::Fallback {
                reason: Occ3FallbackReason::EnvelopeBudgetExceeded
            }
        ));
    }

    #[test]
    fn full_ladder_accepts_with_connector() {
        let id = identity("glm-5.3");
        let fp = id.fingerprint();
        let mut input = input_with(
            true,
            Some(id.clone()),
            Some(id.clone()),
            Some(full_peer(&fp)),
        );
        let connector = crate::core::occ::occ3::mock::MockOcc3Connector::new(
            id.clone(),
            vec![Occ3Method::KvHandoff],
        );
        let caps = connector.capabilities();
        input
            .registry
            .register(std::sync::Arc::new(connector), caps);
        let d = negotiate_occ3(&input);
        match d {
            Occ3Decision::Occ3 {
                method,
                connector_id,
                model_fingerprint,
            } => {
                assert_eq!(method, Occ3Method::KvHandoff);
                assert_eq!(connector_id, "mock-occ3");
                assert_eq!(model_fingerprint, fp);
            }
            other => panic!("expected Occ3, got {other:?}"),
        }
    }

    #[test]
    fn advertisement_from_old_capability_is_absent() {
        let legacy = OccCapability {
            occ1: true,
            dialects: Default::default(),
            occ3: None,
        };
        let adv = advertisement_from_capability(&legacy);
        assert!(!adv.supported);
    }
}
