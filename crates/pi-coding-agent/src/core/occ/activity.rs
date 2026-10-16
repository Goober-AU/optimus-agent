//! Content-free activity projected from admitted agent messages, never pending sends.

use pi_agent_core::types::{AgentMessage, CustomAgentMessage};
use serde::{Deserialize, Serialize};

use super::occ1::{Occ1Dialect, Occ1Message};
use crate::core::agent_messages::AGENT_MESSAGE_CUSTOM_TYPE;

pub const MAX_ACTIVITY_MESSAGES: usize = 32;
const TRANSCRIPT_SCAN_LIMIT: usize = 128;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CommunicationKind {
    Text,
    Occ1,
    // Reserved for implemented OCC-2/OCC-3 transports; never inferred from text.
    Symbolic,
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
    if text.starts_with("OCC/1") && Occ1Message::decode(text)
        .is_ok_and(|message| Occ1Dialect::standard().validate(&message).is_ok()
            || (!message.has("KIND") && message.has("BASE") && super::delta::Delta::from_occ1(&message).is_ok()))
    {
        CommunicationKind::Occ1
    } else {
        CommunicationKind::Text
    }
}

pub fn from_admitted_messages(messages: &[AgentMessage], session_id: &str, generation: &str) -> CommunicationActivity {
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
            id: id.to_owned(), source_session_id: source.to_owned(), kind: classify(text), timestamp: *timestamp,
        });
        if deliveries.len() == MAX_ACTIVITY_MESSAGES { break; }
    }
    deliveries.reverse();
    CommunicationActivity { generation: generation.to_owned(), deliveries }
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
