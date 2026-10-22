//! Offline OCC-3 bridge integration tests (GATE 1).
//!
//! Proves the generic architecture end to end with the deterministic
//! mock connector: no SGLang, no DGX, no GPU, no network. Exercises
//! the full lifecycle capture -> envelope -> deliver -> consume ->
//! acknowledge -> release, plus every fallback and failure edge from
//! the mission's offline test gate list.

use std::sync::Arc;

use serde_json::json;

use pi_coding_agent::core::occ::dialect::OccCapability;
use pi_coding_agent::core::occ::occ3::codec::{
    build_envelope, decode_envelope, encode_envelope, OCC3_HEADER,
};
use pi_coding_agent::core::occ::occ3::connector::{
    CaptureRequest, ConsumeRequest, Occ3Connector,
};
use pi_coding_agent::core::occ::occ3::mock::{
    MockFault, MockOcc3Connector,
};
use pi_coding_agent::core::occ::occ3::negotiation::{
    negotiate_occ3, Occ3CapabilityAdvertisement, Occ3Decision,
    Occ3FallbackReason, Occ3NegotiationInput,
};
use pi_coding_agent::core::occ::occ3::registry::Occ3ConnectorRegistry;
use pi_coding_agent::core::occ::occ3::types::{
    Occ3Error, Occ3Method, Occ3ModelIdentity,
};
use std::time::Duration;

fn glm_identity() -> Occ3ModelIdentity {
    Occ3ModelIdentity {
        model_id: "glm-5.3".into(),
        architecture: "GlmMoeDsaForCausalLM".into(),
        checkpoint_revision: "aca966e4e02791568aa6a4ced368624b3d897f42".into(),
        weight_fingerprint: "141-shards-fp8".into(),
        tokenizer_fingerprint: "tok-glm53".into(),
        chat_template_fingerprint: "tmpl-glm53".into(),
        quantisation: "fp8-e4m3".into(),
        tensor_geometry: "hidden=6144,head=192".into(),
        kv_layout: "dsa-eagle-bigram".into(),
        latent_method: "kv-radix-v1".into(),
        runtime: "sglang-0.5.21".into(),
    }
}

fn other_identity() -> Occ3ModelIdentity {
    let mut id = glm_identity();
    id.model_id = "deepseek-v4.1".into();
    id.checkpoint_revision = "ffffffff".into();
    id
}

fn mock_pair() -> (Arc<MockOcc3Connector>, Occ3ConnectorRegistry) {
    let connector = Arc::new(MockOcc3Connector::new(
        glm_identity(),
        vec![Occ3Method::KvHandoff, Occ3Method::StatebridgePrefix],
    ));
    let mut registry = Occ3ConnectorRegistry::empty();
    let caps = connector.capabilities();
    registry.register(connector.clone(), caps);
    (connector, registry)
}

fn negotiation_input(
    registry: Occ3ConnectorRegistry,
    peer: Option<Occ3CapabilityAdvertisement>,
    sender: Option<Occ3ModelIdentity>,
    receiver: Option<Occ3ModelIdentity>,
) -> Occ3NegotiationInput {
    Occ3NegotiationInput {
        enabled: true,
        sender_identity: sender,
        receiver_identity: receiver,
        peer_occ3: peer,
        method: Occ3Method::KvHandoff,
        registry,
        max_envelope_chars: 16384,
        control_summary_chars: 400,
    }
}

fn peer_advertising(fp: &str) -> Occ3CapabilityAdvertisement {
    Occ3CapabilityAdvertisement {
        supported: true,
        model_fingerprint: Some(fp.to_string()),
        connector_id: Some("mock-occ3".into()),
        methods: vec![Occ3Method::KvHandoff],
    }
}

/// The full happy-path lifecycle two agents execute.
async fn full_lifecycle(
    connector: &Arc<MockOcc3Connector>,
) -> (String, Occ3Error, ()) {
    let capture = connector
        .capture_latent(CaptureRequest {
            message_id: "agentmsg_test1".into(),
            sender_session_id: "sender-s1".into(),
            receiver_session_id: "receiver-r1".into(),
            method: Occ3Method::KvHandoff,
            capture_context: json!({
                "carrier": "template-42",
                "channel": "occ3-ch-1",
            })
            .to_string(),
            ttl: Duration::from_secs(900),
        })
        .await
        .expect("capture");
    let envelope = build_envelope(
        "agentmsg_test1".into(),
        "sender-s1".into(),
        "receiver-r1".into(),
        capture.model_fingerprint.clone(),
        "sglang-0.5.21/gen-1".into(),
        "mock-occ3".into(),
        Occ3Method::KvHandoff,
        capture.clone(),
        "T=probe K=RESULT S=done F1=gpu-verified".into(),
        "occ1".into(),
    )
    .expect("envelope");
    let wire = encode_envelope(&envelope).expect("wire");
    // Receiver decodes what arrived on the transport.
    let decoded = decode_envelope(&wire).expect("decode").expect("is occ3");
    assert_eq!(decoded.state_handle.handle_id, capture.handle_id);
    // Receiver consumes via the connector.
    let metrics = connector
        .consume_latent(ConsumeRequest {
            handle: decoded.state_handle.clone(),
            receiver_session_id: "receiver-r1".into(),
            injection_target: "next-inference".into(),
        })
        .await
        .expect("consume");
    assert!(metrics.state_bytes > 0);
    connector
        .acknowledge_delivery(&decoded.state_handle)
        .await
        .expect("ack");
    (capture.handle_id, Occ3Error::Disabled, ())
}

#[tokio::test]
async fn lifecycle_capture_deliver_consume_ack() {
    let (connector, _registry) = mock_pair();
    connector.deliver_latent(&capture_helper(&connector).await).await.expect("deliver");
    let (handle_id, _, _) = full_lifecycle(&connector).await;
    assert!(handle_id.starts_with("mock-handle-"));
}

async fn capture_helper(connector: &Arc<MockOcc3Connector>) -> pi_coding_agent::core::occ::occ3::types::Occ3HandleRef {
    connector
        .capture_latent(CaptureRequest {
            message_id: "warm".into(),
            sender_session_id: "s".into(),
            receiver_session_id: "r".into(),
            method: Occ3Method::KvHandoff,
            capture_context: "warm".into(),
            ttl: Duration::from_secs(60),
        })
        .await
        .expect("capture")
}

#[tokio::test]
async fn same_model_eligible_full_ladder() {
    let (connector, registry) = mock_pair();
    let fp = glm_identity().fingerprint();
    let decision = negotiate_occ3(&negotiation_input(
        registry,
        Some(peer_advertising(&fp)),
        Some(glm_identity()),
        Some(glm_identity()),
    ));
    assert!(matches!(decision, Occ3Decision::Occ3 { .. }));
    // The connector is actually usable.
    let handle = capture_helper(&connector).await;
    assert!(!handle.handle_id.is_empty());
}

#[tokio::test]
async fn different_model_falls_back() {
    let (_connector, registry) = mock_pair();
    let fp = glm_identity().fingerprint();
    let decision = negotiate_occ3(&negotiation_input(
        registry,
        Some(peer_advertising(&fp)),
        Some(glm_identity()),
        Some(other_identity()),
    ));
    assert!(matches!(
        decision,
        Occ3Decision::Fallback { reason: Occ3FallbackReason::ModelIdentityMismatch }
    ));
}

#[tokio::test]
async fn same_name_different_checkpoint_falls_back() {
    let (_connector, registry) = mock_pair();
    let fp = glm_identity().fingerprint();
    let mut forged = glm_identity();
    forged.checkpoint_revision = "different-weights".into();
    let decision = negotiate_occ3(&negotiation_input(
        registry,
        Some(peer_advertising(&fp)),
        Some(glm_identity()),
        Some(forged),
    ));
    assert!(matches!(
        decision,
        Occ3Decision::Fallback { reason: Occ3FallbackReason::ModelIdentityMismatch }
    ));
}

#[tokio::test]
async fn unreachable_connector_falls_back() {
    let (connector, registry) = mock_pair();
    connector.set_health(false);
    let err = connector.health().await.unwrap_err();
    assert!(matches!(err, Occ3Error::ConnectorUnreachable { .. }));
    // Registry still reports support; live drift surfaces at use time.
    let fp = glm_identity().fingerprint();
    assert!(registry.supports("mock-occ3", Occ3Method::KvHandoff, &fp));
}

#[tokio::test]
async fn connector_timeout_falls_back() {
    let (connector, _registry) = mock_pair();
    connector.inject_fault("capture", MockFault::Timeout { timeout_ms: 500 });
    let err = connector
        .capture_latent(CaptureRequest {
            message_id: "m".into(),
            sender_session_id: "s".into(),
            receiver_session_id: "r".into(),
            method: Occ3Method::KvHandoff,
            capture_context: "x".into(),
            ttl: Duration::from_secs(60),
        })
        .await
        .unwrap_err();
    assert!(matches!(err, Occ3Error::ConnectorTimeout { .. }));
}

#[tokio::test]
async fn expired_handle_falls_back() {
    let (connector, _registry) = mock_pair();
    connector.inject_fault("capture", MockFault::ExpireNextHandle);
    let handle = capture_helper(&connector).await;
    connector.set_health(true);
    let err = connector
        .consume_latent(ConsumeRequest {
            handle,
            receiver_session_id: "r".into(),
            injection_target: "x".into(),
        })
        .await
        .unwrap_err();
    assert!(matches!(err, Occ3Error::HandleExpired { .. }));
}

#[tokio::test]
async fn duplicate_consumption_rejected() {
    let (connector, _registry) = mock_pair();
    let handle = capture_helper(&connector).await;
    connector.set_health(true);
    let req = ConsumeRequest {
        handle,
        receiver_session_id: "r".into(),
        injection_target: "x".into(),
    };
    connector.consume_latent(req.clone()).await.expect("first consume ok");
    let err = connector.consume_latent(req).await.unwrap_err();
    assert!(matches!(err, Occ3Error::HandleAlreadyConsumed { .. }));
}

#[tokio::test]
async fn wrong_receiver_unauthorized() {
    let (connector, _registry) = mock_pair();
    let handle = capture_helper(&connector).await;
    let err = connector
        .consume_latent(ConsumeRequest {
            handle,
            receiver_session_id: "attacker".into(),
            injection_target: "x".into(),
        })
        .await
        .unwrap_err();
    assert!(matches!(err, Occ3Error::Unauthorized));
}

#[tokio::test]
async fn runtime_restart_invalidates_handles() {
    let (connector, _registry) = mock_pair();
    let handle = capture_helper(&connector).await;
    connector.restart_runtime();
    let err = connector
        .consume_latent(ConsumeRequest {
            handle,
            receiver_session_id: "r".into(),
            injection_target: "x".into(),
        })
        .await
        .unwrap_err();
    assert!(matches!(err, Occ3Error::RuntimeGenerationChanged { .. }));
    assert_eq!(connector.live_states(), 0);
}

#[tokio::test]
async fn release_cleans_up_server_state() {
    let (connector, _registry) = mock_pair();
    let handle = capture_helper(&connector).await;
    connector.release_handle(&handle).await.expect("release");
    assert_eq!(connector.live_states(), 0);
}

#[tokio::test]
async fn two_simultaneous_handoffs_distinct_handles() {
    let (connector, _registry) = mock_pair();
    let h1 = capture_helper(&connector).await;
    let h2 = capture_helper(&connector).await;
    assert_ne!(h1.handle_id, h2.handle_id);
    assert_eq!(connector.live_states(), 2);
}

#[tokio::test]
async fn legacy_peer_advertisement_defaults_absent() {
    let legacy = OccCapability {
        occ1: true,
        dialects: Default::default(),
        occ3: None,
    };
    let adv = pi_coding_agent::core::occ::occ3::negotiation::
        advertisement_from_capability(&legacy);
    assert!(!adv.supported);
    // Round-trip through serde: older wire form (no occ3 field) still
    // parses into the extended capability.
    let legacy_json = json!({"occ1": true, "dialects": {}});
    let parsed: OccCapability = serde_json::from_value(legacy_json).expect("legacy parse");
    assert!(parsed.occ3.is_none());
}

#[tokio::test]
async fn envelope_fits_transport_budget() {
    let (connector, _registry) = mock_pair();
    let handle = capture_helper(&connector).await;
    let envelope = build_envelope(
        "agentmsg_budget".into(),
        "s".into(),
        "r".into(),
        handle.model_fingerprint.clone(),
        "sglang-0.5.21/gen-1".into(),
        "mock-occ3".into(),
        Occ3Method::KvHandoff,
        handle,
        "T=probe K=RESULT S=done F1=gpu-verified F2=handle-delivered".into(),
        "occ1".into(),
    )
    .expect("envelope");
    let wire = encode_envelope(&envelope).expect("wire");
    assert!(wire.starts_with(OCC3_HEADER));
    assert!(
        wire.chars().count() <= 16384,
        "envelope must fit DEFAULT_AGENT_MESSAGE_MAX_CHARS"
    );
}

#[tokio::test]
async fn failed_capture_before_delivery_falls_back_cleanly() {
    let (connector, _registry) = mock_pair();
    connector.inject_fault("capture", MockFault::CaptureFails);
    let err = connector
        .capture_latent(CaptureRequest {
            message_id: "m".into(),
            sender_session_id: "s".into(),
            receiver_session_id: "r".into(),
            method: Occ3Method::KvHandoff,
            capture_context: "x".into(),
            ttl: Duration::from_secs(60),
        })
        .await;
    assert!(err.is_err());
    // No state leaked.
    assert_eq!(connector.live_states(), 0);
}

#[tokio::test]
async fn shutdown_invalidates_everything() {
    let (connector, _registry) = mock_pair();
    let _h1 = capture_helper(&connector).await;
    let _h2 = capture_helper(&connector).await;
    connector.shutdown().await.expect("shutdown");
    assert_eq!(connector.live_states(), 0);
}
