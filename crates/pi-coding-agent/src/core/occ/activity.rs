//! Content-free activity projected from admitted agent messages, never pending sends.

use pi_agent_core::types::{AgentMessage, CustomAgentMessage};
use serde::{Deserialize, Serialize};

use super::dialect::DialectRegistry;
use super::occ1::{Occ1Dialect, Occ1Message};
use super::occ2;
use crate::core::agent_messages::AGENT_MESSAGE_CUSTOM_TYPE;

pub const MAX_ACTIVITY_MESSAGES: usize = 32;
const TRANSCRIPT_SCAN_LIMIT: usize = 128;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CommunicationKind {
    Text,
    Occ1,
    // Reported only for OCC/2 bodies that decoded through the symbolic
    // path (`occ2::decode` with a registered dialect); an OCC/2 body
    // consumed through its embedded OCC-1 fallback reports Occ1.
    Symbolic,
    // Reserved for OCC-3 latent transports; never inferred from text.
    Latent,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CommunicationDelivery {
    pub id: String,
    pub source_session_id: String,
    pub kind: CommunicationKind,
    pub timestamp: i64,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CommunicationActivity {
    pub generation: String,
    pub deliveries: Vec<CommunicationDelivery>,
}

fn bounded_id(value: &serde_json::Value) -> Option<&str> {
    value.as_str().filter(|id| !id.is_empty() && id.len() <= 128 && !id.chars().any(char::is_control))
}

pub fn classify(text: &str) -> CommunicationKind {
    classify_with_registry(text, &DialectRegistry::with_sym_v1())
}

/// Classifies one delivered body against a dialect registry (OCC-2
/// integration design, section 6). `Symbolic` is reported only when the
/// strict OCC-2 decode succeeds on the symbolic section; an OCC/2 body
/// whose strict decode fails but whose OCC-1 fallback section parses is
/// `Occ1`, because that is how it was consumed. Malformed OCC/2 stays
/// `Text`.
pub fn classify_with_registry(text: &str, registry: &DialectRegistry) -> CommunicationKind {
    if text.starts_with("OCC/1") && Occ1Message::decode(text)
        .is_ok_and(|message| Occ1Dialect::standard().validate(&message).is_ok()
            || (!message.has("KIND") && message.has("BASE") && super::delta::Delta::from_occ1(&message).is_ok()))
    {
        return CommunicationKind::Occ1;
    }
    if text.starts_with(occ2::OCC2_HEADER) {
        if occ2::decode(text, registry).is_ok() {
            return CommunicationKind::Symbolic;
        }
        if occ2::occ1_fallback(text).is_ok() {
            return CommunicationKind::Occ1;
        }
    }
    CommunicationKind::Text
}

pub fn from_admitted_messages(messages: &[AgentMessage], session_id: &str, generation: &str) -> CommunicationActivity {
    from_admitted_messages_with_registry(messages, session_id, generation, &DialectRegistry::with_sym_v1())
}

/// `fromAdmittedMessages` with an explicit dialect registry: the registry
/// decides whether an OCC/2 body reports Symbolic or falls back to Occ1.
/// Content-free and bounded exactly like the single-registry entry point.
pub fn from_admitted_messages_with_registry(messages: &[AgentMessage], session_id: &str, generation: &str, registry: &DialectRegistry) -> CommunicationActivity {
    let mut deliveries = Vec::new();
    for message in messages.iter().rev().take(TRANSCRIPT_SCAN_LIMIT) {
        let AgentMessage::Custom(CustomAgentMessage::Custom { custom_type, details: Some(details), timestamp, .. }) = message else { continue; };
        if custom_type != AGENT_MESSAGE_CUSTOM_TYPE || details["target"]["sessionId"].as_str() != Some(session_id) {
            continue;
        }
        let (Some(id), Some(source), Some(text)) = (
            bounded_id(&details["id"]), bounded_id(&details["from"]["sessionId"]), details["message"].as_str(),
        ) else { continue; };
        if source == session_id || deliveries.iter().any(|delivery: &CommunicationDelivery| delivery.id == id) { continue; }
        deliveries.push(CommunicationDelivery {
            id: id.to_owned(), source_session_id: source.to_owned(), kind: recorded_delivery_kind(details).unwrap_or_else(|| classify_with_registry(text, registry)), timestamp: *timestamp,
        });
        if deliveries.len() == MAX_ACTIVITY_MESSAGES { break; }
    }
    deliveries.reverse();
    CommunicationActivity { generation: generation.to_owned(), deliveries }
}

/// The delivery classification recorded once at delivery time
/// (`details.occ2Delivery.kind`, written by the delivery render):
/// `symbolic` -> Symbolic, `text` -> Text, the OCC-1-consumed kinds
/// (`fallback_consumed`, `occ1_native`, `occ2_disabled_by_strikes`)
/// -> Occ1. `None` for legacy records without the field, unknown
/// labels, or non-string values: the caller then classifies from
/// the body as before.
fn recorded_delivery_kind(details: &serde_json::Value) -> Option<CommunicationKind> {
    let kind = details
        .get("occ2Delivery")?
        .get("kind")?
        .as_str()?;
    match kind {
        "symbolic" => Some(CommunicationKind::Symbolic),
        "fallback_consumed" | "occ1_native" | "occ2_disabled_by_strikes" => {
            Some(CommunicationKind::Occ1)
        }
        "text" => Some(CommunicationKind::Text),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::agent_messages::{create_agent_session_message, AgentSessionMessagePayload, AgentSessionMessageSender, AgentSessionMessageEndpoint};

    pub fn delivered(id: &str, text: &str) -> AgentMessage {
        AgentMessage::Custom(create_agent_session_message(&AgentSessionMessagePayload {
            id: id.into(), message: text.into(),
            from: Some(AgentSessionMessageSender { session_id: Some("parent".into()), ..Default::default() }),
            target: AgentSessionMessageEndpoint { session_id: "child".into(), ..Default::default() },
            ..Default::default()
        }, 1000))
    }

    #[test]
    fn communication_classifies_valid_occ_only_and_never_claims_symbolic_or_latent() {
        assert_eq!(classify("hello"), CommunicationKind::Text);
        assert_eq!(classify("OCC/1\nTASK=t\nKIND=RESULT\nSTATUS=done"), CommunicationKind::Occ1);
        assert_eq!(classify("OCC/1\nTASK=t\nSEQ=2\nBASE=1\nSTATUS=done"), CommunicationKind::Occ1);
        assert_eq!(classify("OCC/1\nTASK=t\nSEQ=invalid\nBASE=1"), CommunicationKind::Text);
        for text in ["OCC/1", "OCC/1\nKIND=RESULT", "OCC/2\nSYMBOL=x", "latent vector", "OCC/1\nTASK=t\nKIND=RESULT\nSTATUS=invalid"] {
            assert_eq!(classify(text), CommunicationKind::Text);
        }
    }

    #[test]
    fn occ2_bodies_classify_by_the_consumed_path() {
        use super::super::contract::AgentCommMessage;
        use super::super::dialect::{DialectCard, DialectRegistry};
        use super::super::occ2;

        let registry = DialectRegistry::with_sym_v1();
        let card = DialectCard::sym_v1();
        let message = AgentCommMessage::new("TASK", "t1", "pending");

        // A dual payload decodes strictly: Symbolic.
        let dual = occ2::encode_dual(&message, &card).expect("encode_dual");
        assert_eq!(classify(&dual), CommunicationKind::Symbolic);
        assert_eq!(classify_with_registry(&dual, &registry), CommunicationKind::Symbolic);

        // A symbolic-only payload also decodes strictly: Symbolic.
        let symbolic = occ2::encode(&message, &card).expect("encode");
        assert_eq!(classify(&symbolic), CommunicationKind::Symbolic);

        // Unknown dialect (empty registry) consumed via the embedded
        // fallback: Occ1.
        let empty = DialectRegistry::new();
        assert_eq!(classify_with_registry(&dual, &empty), CommunicationKind::Occ1);

        // Hash mismatch: same id and version, different codebook.
        let mut tampered = DialectCard::sym_v1();
        tampered.symbol_table.slots.insert("task_id".to_string(), "Z".to_string());
        tampered.sha256 = super::super::dialect::codebook_sha256(&tampered.grammar, &tampered.symbol_table);
        let mut mismatch_registry = DialectRegistry::new();
        mismatch_registry.insert(tampered).expect("tampered card registers");
        assert_eq!(classify_with_registry(&dual, &mismatch_registry), CommunicationKind::Occ1);

        // Malformed OCC/2 (including the pinned case) stays Text.
        for text in ["OCC/2\nSYMBOL=x", "OCC/2", "OCC/2 D=sym-v1@1"] {
            assert_eq!(classify(text), CommunicationKind::Text);
        }

        // The pinned classification of OCC-1 and plain text is unchanged.
        assert_eq!(classify("OCC/1\nTASK=t\nKIND=RESULT\nSTATUS=done"), CommunicationKind::Occ1);
        assert_eq!(classify("hello"), CommunicationKind::Text);
    }

    #[test]
    fn communication_projection_is_bounded_deduplicated_and_content_free() {
        let messages = (0..100).map(|id| delivered(&id.to_string(), "private message body")).collect::<Vec<_>>();
        let activity = from_admitted_messages(&messages, "child", "g");
        assert_eq!(activity.deliveries.len(), MAX_ACTIVITY_MESSAGES);
        assert_eq!(activity.deliveries[0].id, "68");
        assert!(!serde_json::to_string(&activity).unwrap().contains("private"));
        assert!(from_admitted_messages(&messages, "different-recipient", "g").deliveries.is_empty());
        let duplicate = delivered("same", "hello");
        assert_eq!(from_admitted_messages(&[duplicate.clone(), duplicate], "child", "g").deliveries.len(), 1);
        assert!(from_admitted_messages(&[delivered(&"x".repeat(129), "hello")], "child", "g").deliveries.is_empty());
        // Queued/failed sends have no admitted transcript message.
        assert!(from_admitted_messages(&[], "child", "g").deliveries.is_empty());
    }
}
