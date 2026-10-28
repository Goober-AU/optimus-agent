//! Production activation of experimental OCC-3.
//!
//! The bridge, connector, native session, admission inbox and activity
//! projection all exist; what was missing is one place that decides
//! whether a real session may use them, and a named reason when it may
//! not. This module is that place.
//!
//! Four facts stay separate on purpose and are never collapsed:
//!
//! * capability available  - the runtime verified a connector and method;
//! * experimental enabled  - the user opted in twice and the model
//!   declares an OCC-3 route;
//! * native delivery completed - a receiver consumption receipt exists;
//! * semantically verified - a measurement, never a configuration.
//!
//! This module only decides the first two. It never claims the last two.
//!
//! Nothing here infers support from a model name, a provider string, a
//! tokenizer, a hidden dimension or an endpoint shape: eligibility comes
//! from `models.json`'s `occ3` block and from the connector's own live
//! handshake, and it is still subject to the same-exact-model rule inside
//! [`Occ3NativeSession::enroll`].
use std::time::Duration;

use crate::core::occ::occ3::connector::Occ3Connector;
use crate::core::occ::occ3::types::{Occ3ConnectorCapabilities, Occ3Method};
use crate::core::occ3_connectors::sglang::{SglangOcc3Config, SglangOcc3Connector};
use url::Url;
use pi_ai::types::{Model, Occ3Capability, Occ3Validation};

/// Upper bound on the readiness handshake so activation can never stall a
/// session start. A timeout is a decline, not an error path.
pub const ACTIVATION_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);

/// Why OCC-3 is not active for this session. Every variant is a clean
/// decline: the session keeps its ordinary text/OCC-1/OCC-2 behaviour.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Occ3Decline {
    /// No `occ3` block on the model.
    NotConfigured,
    /// The model declares OCC-3 but has it switched off.
    DisabledOnModel,
    /// `agentCommunication.occ3Enabled` is false.
    DisabledByPolicy,
    /// `agentCommunication.occ3Experimental` is false.
    ExperimentalAckRequired,
    /// The model's `occ3.validation` is not `live-verified`.
    NotLiveVerified,
    /// The block does not use `occ3-v1`.
    UnsupportedProtocol,
    /// `sameModelOnly` is false; v1 cannot cross a model boundary.
    SameModelOnlyRequired,
    /// The connector id has no implementation.
    UnknownConnector,
    /// The endpoint is not an acceptable absolute http(s) URL.
    InvalidEndpoint,
    /// The live handshake failed or timed out.
    ConnectorUnavailable,
    /// The runtime does not advertise the required native method.
    MethodUnsupported,
    /// The runtime's verified identity did not bind to this model.
    ModelIdentityMismatch,
}

impl Occ3Decline {
    /// Stable, value-free identifier for logs, receipts and diagnostics.
    pub fn as_str(self) -> &'static str {
        match self {
            Occ3Decline::NotConfigured => "not_configured",
            Occ3Decline::DisabledOnModel => "disabled_on_model",
            Occ3Decline::DisabledByPolicy => "disabled_by_policy",
            Occ3Decline::ExperimentalAckRequired => "experimental_ack_required",
            Occ3Decline::NotLiveVerified => "not_live_verified",
            Occ3Decline::UnsupportedProtocol => "unsupported_protocol",
            Occ3Decline::SameModelOnlyRequired => "same_model_only_required",
            Occ3Decline::UnknownConnector => "unknown_connector",
            Occ3Decline::InvalidEndpoint => "invalid_endpoint",
            Occ3Decline::ConnectorUnavailable => "connector_unavailable",
            Occ3Decline::MethodUnsupported => "method_unsupported",
            Occ3Decline::ModelIdentityMismatch => "model_identity_mismatch",
        }
    }

    /// Operator-facing sentence. No endpoint, key or handle is included.
    pub fn message(self) -> &'static str {
        match self {
            Occ3Decline::NotConfigured => "model has no occ3 configuration block",
            Occ3Decline::DisabledOnModel => "model occ3 block is not enabled",
            Occ3Decline::DisabledByPolicy => "agentCommunication.occ3Enabled is false",
            Occ3Decline::ExperimentalAckRequired => {
                "agentCommunication.occ3Experimental is false; OCC-3 semantic reliability is unproven"
            }
            Occ3Decline::NotLiveVerified => {
                "model occ3.validation is not live-verified; run the readiness probe and record its result"
            }
            Occ3Decline::UnsupportedProtocol => "model occ3 block is not occ3-v1",
            Occ3Decline::SameModelOnlyRequired => "occ3-v1 is same-exact-model only",
            Occ3Decline::UnknownConnector => "occ3 connector has no implementation",
            Occ3Decline::InvalidEndpoint => "occ3 endpoint is not an absolute http(s) URL",
            Occ3Decline::ConnectorUnavailable => "occ3 connector readiness handshake failed or timed out",
            Occ3Decline::MethodUnsupported => "runtime does not advertise the required native method",
            Occ3Decline::ModelIdentityMismatch => "runtime verified identity does not bind to this model",
        }
    }
}

impl std::fmt::Display for Occ3Decline {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.message())
    }
}

/// What the caller configured. Built from settings and the model definition
/// only; no session state, no clock, no I/O.
#[derive(Debug, Clone)]
pub struct Occ3ActivationRequest {
    pub occ3_enabled: bool,
    pub occ3_experimental: bool,
    pub model: Model,
    pub endpoint_override: Option<String>,
    pub method: Occ3Method,
}

impl Occ3ActivationRequest {
    pub fn new(model: &Model, occ3_enabled: bool, occ3_experimental: bool) -> Self {
        Self {
            occ3_enabled,
            occ3_experimental,
            model: model.clone(),
            endpoint_override: None,
            method: Occ3Method::StatebridgePrefix,
        }
    }

    pub fn with_endpoint_override(mut self, endpoint: Option<String>) -> Self {
        self.endpoint_override = endpoint;
        self
    }

    pub fn with_method(mut self, method: Occ3Method) -> Self {
        self.method = method;
        self
    }
}

/// The configuration-only gate: no network. Returns the connector config a
/// live handshake would need, or the named decline.
pub fn configured_connector(
    request: &Occ3ActivationRequest,
) -> Result<SglangOcc3Config, Occ3Decline> {
    if !request.occ3_enabled {
        return Err(Occ3Decline::DisabledByPolicy);
    }
    // The acknowledgement is checked before the model so the operator sees
    // the policy reason rather than a configuration reason when both are off.
    if !request.occ3_experimental {
        return Err(Occ3Decline::ExperimentalAckRequired);
    }
    let Some(occ3) = request.model.occ3.as_ref() else {
        return Err(Occ3Decline::NotConfigured);
    };
    if !occ3.enabled {
        return Err(Occ3Decline::DisabledOnModel);
    }
    if occ3.protocol != pi_ai::types::OCC3_PROTOCOL_V1
        || occ3.api_version != pi_ai::types::OCC3_API_VERSION_V1
    {
        return Err(Occ3Decline::UnsupportedProtocol);
    }
    if !occ3.same_model_only {
        return Err(Occ3Decline::SameModelOnlyRequired);
    }
    if occ3.validation != Occ3Validation::LiveVerified {
        return Err(Occ3Decline::NotLiveVerified);
    }
    let endpoint = request
        .endpoint_override
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(occ3.endpoint.as_str());
    if !endpoint_is_acceptable(endpoint) {
        return Err(Occ3Decline::InvalidEndpoint);
    }
    if occ3.connector != "sglang" {
        return Err(Occ3Decline::UnknownConnector);
    }
    let endpoint = Url::parse(endpoint).map_err(|_| Occ3Decline::InvalidEndpoint)?;
    // Credentials are supplied by the host from the environment, never from
    // `models.json`, and are never logged.
    Ok(SglangOcc3Config {
        endpoint,
        api_key: None,
        control_headers: None,
    })
}

/// Absolute http(s) URL, no userinfo, no query, no fragment. The connector
/// repeats this check; doing it here keeps the decline named.
fn endpoint_is_acceptable(endpoint: &str) -> bool {
    let Ok(url) = Url::parse(endpoint) else {
        return false;
    };
    matches!(url.scheme(), "http" | "https")
        && url.host().is_some()
        && url.username().is_empty()
        && url.password().is_none()
        && url.query().is_none()
        && url.fragment().is_none()
}

/// A verified, ready connector. Holding this is the only way to reach
/// [`enroll`], so an unverified route cannot be enrolled by accident.
pub struct Occ3Ready {
    pub connector: std::sync::Arc<dyn Occ3Connector>,
    pub capabilities: Occ3ConnectorCapabilities,
    pub method: Occ3Method,
    pub connector_id: String,
}

impl std::fmt::Debug for Occ3Ready {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Occ3Ready")
            .field("connector_id", &self.connector_id)
            .field("method", &self.method.as_str())
            .field("supported_methods", &self.capabilities.methods)
            .field("protocol_version", &self.capabilities.protocol_version)
            .finish_non_exhaustive()
    }
}

/// Perform the live readiness handshake and prove the runtime is usable.
/// Failures are named declines; nothing here panics or retries.
pub async fn verify(
    request: &Occ3ActivationRequest,
    handshake_timeout: Duration,
) -> Result<Occ3Ready, Occ3Decline> {
    let config = configured_connector(request)?;
    let connector = SglangOcc3Connector::new(config).map_err(|_| Occ3Decline::ConnectorUnavailable)?;
    let connector: std::sync::Arc<dyn Occ3Connector> = std::sync::Arc::new(connector);
    let capabilities = match tokio::time::timeout(handshake_timeout, connector.discover_capabilities()).await {
        Err(_) => return Err(Occ3Decline::ConnectorUnavailable),
        Ok(Err(_)) => return Err(Occ3Decline::ConnectorUnavailable),
        Ok(Ok(capabilities)) => capabilities,
    };
    if !capabilities.methods.contains(&request.method) {
        return Err(Occ3Decline::MethodUnsupported);
    }
    // The advertised identity must be the model we are about to bind to.
    // `Occ3NativeSession::enroll` repeats this against the enrollment; the
    // check here keeps the decline named and prevents a wrong route from
    // being registered process-wide.
    if capabilities.model_identity.model_id != request.model.id {
        return Err(Occ3Decline::ModelIdentityMismatch);
    }
    Ok(Occ3Ready {
        connector_id: connector.connector_id().to_string(),
        connector,
        capabilities,
        method: request.method,
    })
}

/// Process-wide verified connector cache. The readiness handshake and the
/// verified identity are properties of the runtime, not of a session, so one
/// successful verification serves every later enrollment. A changed endpoint
/// or model id re-verifies.
fn ready_cache() -> &'static std::sync::Mutex<Option<(String, std::sync::Arc<Occ3Ready>)>> {
    static CACHE: std::sync::OnceLock<std::sync::Mutex<Option<(String, std::sync::Arc<Occ3Ready>)>>> =
        std::sync::OnceLock::new();
    CACHE.get_or_init(|| std::sync::Mutex::new(None))
}

fn cache_key(request: &Occ3ActivationRequest, config: &SglangOcc3Config) -> String {
    format!("{}|{}", config.endpoint, request.model.id)
}

/// [`verify`] with the process-wide cache. Concurrent callers share one
/// handshake result; a poisoned cache is a miss, never a panic.
pub async fn verify_cached(
    request: &Occ3ActivationRequest,
    handshake_timeout: Duration,
) -> Result<std::sync::Arc<Occ3Ready>, Occ3Decline> {
    let config = configured_connector(request)?;
    let key = cache_key(request, &config);
    if let Some((cached_key, ready)) = ready_cache()
        .lock()
        .map(|cache| cache.clone())
        .unwrap_or(None)
    {
        if cached_key == key {
            return Ok(ready);
        }
    }
    let ready = std::sync::Arc::new(verify(request, handshake_timeout).await?);
    if let Ok(mut cache) = ready_cache().lock() {
        *cache = Some((key, std::sync::Arc::clone(&ready)));
    }
    Ok(ready)
}

/// Drop the cached verification. Called when a runtime restart invalidates
/// every enrollment, so the next activation re-handshakes.
pub fn invalidate_ready_cache() {
    if let Ok(mut cache) = ready_cache().lock() {
        *cache = None;
    }
}

/// Enroll one live session against a verified connector and install the
/// native permit and handoff tool. The session must not be streaming.
pub async fn enroll(
    session: &std::sync::Arc<crate::core::agent_session::AgentSession>,
    model: &Model,
    ready: &Occ3Ready,
    controller: std::sync::Arc<dyn crate::core::agent_messages::AgentSessionMessageController>,
) -> Result<(), String> {
    if session.is_streaming() {
        return Err("occ3 activation refused: session is streaming".into());
    }
    let state = crate::core::agent_session::occ3_state::Occ3NativeSession::enroll(
        &session.session_id(),
        model,
        ready.connector.clone(),
    )
    .await?;
    session.install_experimental_native_occ3(state, controller)
}

/// Register a verified connector in the process-wide registry so the
/// negotiation ladder can see it.
pub fn register(ready: &Occ3Ready) {
    crate::core::occ3_connectors::with_global_registry(|registry| {
        registry.register(ready.connector.clone(), ready.capabilities.clone());
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use pi_ai::types::{API_OPENAI_COMPLETIONS, Occ3Capability};

    fn model_with(occ3: Option<Occ3Capability>) -> Model {
        let mut model = Model::new(
            "glm-5.3",
            "GLM-5.3",
            API_OPENAI_COMPLETIONS,
            "dgx-sglang",
            "http://127.0.0.1:30001/v1",
        );
        model.occ3 = occ3;
        model
    }

    fn capability() -> Occ3Capability {
        Occ3Capability {
            protocol: pi_ai::types::OCC3_PROTOCOL_V1.into(),
            connector: "sglang".into(),
            endpoint: "http://127.0.0.1:30001".into(),
            api_version: pi_ai::types::OCC3_API_VERSION_V1.into(),
            same_model_only: true,
            enabled: true,
            validation: Occ3Validation::LiveVerified,
            fallback: vec!["occ2".into(), "occ1".into(), "text".into()],
        }
    }

    fn request(model: Model) -> Occ3ActivationRequest {
        Occ3ActivationRequest::new(&model, true, true)
    }

    #[test]
    fn a_fully_configured_live_verified_model_produces_a_connector_config() {
        let config = configured_connector(&request(model_with(Some(capability()))))
            .expect("activated");
        assert_eq!(config.endpoint.as_str(), "http://127.0.0.1:30001/");
        assert!(config.api_key.is_none());
        assert!(config.control_headers.is_none());
    }

    #[test]
    fn every_gate_declines_with_its_own_named_reason() {
        // Policy off wins over everything else.
        assert_eq!(
            configured_connector(&Occ3ActivationRequest::new(&model_with(Some(capability())), false, true)).err(),
            Some(Occ3Decline::DisabledByPolicy)
        );
        // Unacknowledged experimental use.
        assert_eq!(
            configured_connector(&Occ3ActivationRequest::new(&model_with(Some(capability())), true, false)).err(),
            Some(Occ3Decline::ExperimentalAckRequired)
        );
        // No occ3 block: a model name alone is never evidence.
        assert_eq!(
            configured_connector(&request(model_with(None))).err(),
            Some(Occ3Decline::NotConfigured)
        );
        let cases: [(fn(&mut Occ3Capability), Occ3Decline); 6] = [
            (|c| c.enabled = false, Occ3Decline::DisabledOnModel),
            (|c| c.protocol = "occ3-v2".into(), Occ3Decline::UnsupportedProtocol),
            (|c| c.api_version = "v2".into(), Occ3Decline::UnsupportedProtocol),
            (|c| c.same_model_only = false, Occ3Decline::SameModelOnlyRequired),
            (|c| c.validation = Occ3Validation::Unverified, Occ3Decline::NotLiveVerified),
            (|c| c.validation = Occ3Validation::DocumentationVerified, Occ3Decline::NotLiveVerified),
        ];
        for (mutate, expected) in cases {
            let mut block = capability();
            mutate(&mut block);
            assert_eq!(configured_connector(&request(model_with(Some(block)))).err(), Some(expected));
        }
        let mut unknown = capability();
        unknown.connector = "vllm".into();
        assert_eq!(
            configured_connector(&request(model_with(Some(unknown)))).err(),
            Some(Occ3Decline::UnknownConnector)
        );
    }

    #[test]
    fn endpoints_that_could_leak_credentials_or_override_the_target_are_rejected() {
        for endpoint in [
            "",
            "127.0.0.1:30001",
            "ftp://127.0.0.1:30001",
            "http://user:secret@127.0.0.1:30001",
            "http://127.0.0.1:30001?token=secret",
            "http://127.0.0.1:30001#frag",
        ] {
            let mut block = capability();
            block.endpoint = endpoint.into();
            let request = request(model_with(Some(block)));
            assert_eq!(
                configured_connector(&request).err(),
                Some(Occ3Decline::InvalidEndpoint),
                "endpoint {endpoint:?} must be rejected"
            );
        }
    }

    #[test]
    fn the_endpoint_override_replaces_the_model_endpoint() {
        let overridden = request(model_with(Some(capability())))
            .with_endpoint_override(Some("  http://127.0.0.1:30002  ".into()));
        let config = configured_connector(&overridden).expect("override accepted");
        assert_eq!(config.endpoint.port(), Some(30002));
        // A blank override is not a value.
        let config = configured_connector(
            &request(model_with(Some(capability()))).with_endpoint_override(Some("   ".into())),
        )
        .expect("blank override falls back");
        assert_eq!(config.endpoint.port(), Some(30001));
    }

    #[test]
    fn the_default_method_is_the_statebridge_prefix_and_it_is_requestable() {
        let request = request(model_with(Some(capability())));
        assert_eq!(request.method, Occ3Method::StatebridgePrefix);
        assert_eq!(configured_connector(&request).is_ok(), true);
        let kv = request.with_method(Occ3Method::KvHandoff);
        assert_eq!(kv.method, Occ3Method::KvHandoff);
    }

    #[tokio::test]
    async fn an_unreachable_runtime_declines_without_erroring_or_hanging() {
        let mut block = capability();
        // Port 1 on loopback refuses immediately; the timeout keeps the
        // worst case bounded even if it did not.
        block.endpoint = "http://127.0.0.1:1".into();
        let request = request(model_with(Some(block)));
        assert_eq!(
            verify(&request, Duration::from_millis(750)).await.err(),
            Some(Occ3Decline::ConnectorUnavailable)
        );
    }

    #[test]
    fn decline_identifiers_are_stable_and_value_free() {
        let all = [
            Occ3Decline::NotConfigured,
            Occ3Decline::DisabledOnModel,
            Occ3Decline::DisabledByPolicy,
            Occ3Decline::ExperimentalAckRequired,
            Occ3Decline::NotLiveVerified,
            Occ3Decline::UnsupportedProtocol,
            Occ3Decline::SameModelOnlyRequired,
            Occ3Decline::UnknownConnector,
            Occ3Decline::InvalidEndpoint,
            Occ3Decline::ConnectorUnavailable,
            Occ3Decline::MethodUnsupported,
            Occ3Decline::ModelIdentityMismatch,
        ];
        let mut seen = std::collections::BTreeSet::new();
        for decline in all {
            assert!(seen.insert(decline.as_str()), "duplicate id for {decline:?}");
            assert!(!decline.as_str().contains(' '));
            assert!(!decline.message().is_empty());
        }
    }
}
