//! OCC-3 connector registry: process-wide lookup of live connectors.
//!
//! The registry is the runtime side of the pure bridge: it holds
//! `Arc<dyn Occ3Connector>` implementations (registered by embedding
//! code, e.g. the SGLang client in the daemon) and answers
//! availability queries for the selection policy. It performs no I/O
//! of its own: live capability snapshots are cached and refreshed by
//! the embedding code, never inside this module.

use std::collections::BTreeMap;
use std::sync::Arc;

use super::connector::Occ3Connector;
use super::types::{Occ3ConnectorCapabilities, Occ3Error, Occ3Method};

/// Registry of live OCC-3 connectors plus their last known capability
/// snapshots (populated by the embedding layer after a live handshake).
#[derive(Default, Clone)]
pub struct Occ3ConnectorRegistry {
    connectors: BTreeMap<String, Arc<dyn Occ3Connector>>,
    capabilities: BTreeMap<String, Occ3ConnectorCapabilities>,
    preference: Vec<String>,
}

impl std::fmt::Debug for Occ3ConnectorRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Occ3ConnectorRegistry")
            .field("connectors", &self.connector_ids())
            .field("preference", &self.preference)
            .finish()
    }
}

impl Occ3ConnectorRegistry {
    /// Empty registry: OCC-3 unavailable everywhere.
    pub fn empty() -> Self {
        Self::default()
    }

    /// Register a connector and its verified capability snapshot.
    pub fn register(
        &mut self,
        connector: Arc<dyn Occ3Connector>,
        capabilities: Occ3ConnectorCapabilities,
    ) {
        let id = connector.connector_id().to_string();
        if !self.preference.contains(&id) {
            self.preference.push(id.clone());
        }
        self.capabilities.insert(id.clone(), capabilities);
        self.connectors.insert(id, connector);
    }

    /// Refresh the cached capability snapshot for a connector (called
    /// after a successful live handshake).
    pub fn update_capabilities(
        &mut self,
        connector_id: &str,
        capabilities: Occ3ConnectorCapabilities,
    ) {
        self.capabilities.insert(connector_id.to_string(), capabilities);
    }

    /// Remove a connector (after shutdown or repeated failures).
    pub fn unregister(&mut self, connector_id: &str) {
        self.connectors.remove(connector_id);
        self.capabilities.remove(connector_id);
        self.preference.retain(|id| id != connector_id);
    }

    /// Look up a connector.
    pub fn get(&self, connector_id: &str) -> Option<Arc<dyn Occ3Connector>> {
        self.connectors.get(connector_id).cloned()
    }

    /// Cached capability snapshot for a connector.
    pub fn capabilities(&self, connector_id: &str) -> Option<&Occ3ConnectorCapabilities> {
        self.capabilities.get(connector_id)
    }

    /// All registered connector ids in preference order.
    pub fn connector_ids(&self) -> Vec<String> {
        self.preference
            .iter()
            .filter(|id| self.connectors.contains_key(*id))
            .cloned()
            .collect()
    }

    /// True when no connector is registered.
    pub fn is_empty(&self) -> bool {
        self.connectors.is_empty()
    }

    /// Whether a registered connector supports a method and model
    /// fingerprint, per its cached snapshot. A missing snapshot means
    /// the answer is no: activation requires a live handshake.
    pub fn supports(
        &self,
        connector_id: &str,
        method: Occ3Method,
        model_fingerprint: &str,
    ) -> bool {
        self.capabilities
            .get(connector_id)
            .is_some_and(|caps| {
                caps.methods.contains(&method)
                    && caps.model_identity.fingerprint() == model_fingerprint
            })
    }

    /// List connector ids whose cached snapshot matches the model
    /// fingerprint and method, in preference order.
    pub fn connectors_for(
        &self,
        method: Occ3Method,
        model_fingerprint: &str,
    ) -> Vec<String> {
        self.preference
            .iter()
            .filter(|id| {
                self.connectors.contains_key(*id)
                    && self.supports(id, method, model_fingerprint)
            })
            .cloned()
            .collect()
    }

    /// Resolve the preferred connector for a fingerprint/method pair.
    pub fn find_for(
        &self,
        method: Occ3Method,
        model_fingerprint: &str,
    ) -> Result<Arc<dyn Occ3Connector>, Occ3Error> {
        self.connectors_for(method, model_fingerprint)
            .first()
            .and_then(|id| self.get(id))
            .ok_or_else(|| Occ3Error::UnknownConnector {
                connector_id: format!(
                    "no connector with verified capabilities for {model_fingerprint}"
                ),
            })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // A minimal fake connector for registry tests; full behavioral
    // coverage lives in the mock-connector integration tests.
    struct FakeConnector {
        id: &'static str,
    }

    #[async_trait::async_trait]
    impl Occ3Connector for FakeConnector {
        fn connector_id(&self) -> &str { self.id }
        fn protocol_version(&self) -> u32 { 1 }
        async fn health(&self) -> Result<(), Occ3Error> { Ok(()) }
        async fn discover_capabilities(
            &self,
        ) -> Result<Occ3ConnectorCapabilities, Occ3Error> {
            unimplemented!("registry tests use cached snapshots")
        }
        async fn model_identity(&self) -> Result<super::super::types::Occ3ModelIdentity, Occ3Error> {
            unimplemented!()
        }
        async fn capture_latent(
            &self,
            _request: super::super::connector::CaptureRequest,
        ) -> Result<super::super::types::Occ3HandleRef, Occ3Error> {
            unimplemented!()
        }
        async fn deliver_latent(
            &self,
            _handle: &super::super::types::Occ3HandleRef,
        ) -> Result<(), Occ3Error> {
            unimplemented!()
        }
        async fn consume_latent(
            &self,
            _request: super::super::connector::ConsumeRequest,
        ) -> Result<super::super::types::Occ3TransferMetrics, Occ3Error> {
            unimplemented!()
        }
        async fn acknowledge_delivery(
            &self,
            _handle: &super::super::types::Occ3HandleRef,
        ) -> Result<(), Occ3Error> {
            unimplemented!()
        }
        async fn release_handle(
            &self,
            _handle: &super::super::types::Occ3HandleRef,
        ) -> Result<(), Occ3Error> {
            unimplemented!()
        }
        async fn shutdown(&self) -> Result<(), Occ3Error> { Ok(()) }
    }

    fn caps(model_id: &str, methods: Vec<Occ3Method>) -> Occ3ConnectorCapabilities {
        use super::super::types::Occ3ModelIdentity;
        let identity = Occ3ModelIdentity {
            model_id: model_id.to_string(),
            architecture: "arch".into(),
            checkpoint_revision: "rev".into(),
            weight_fingerprint: "w".into(),
            tokenizer_fingerprint: "t".into(),
            chat_template_fingerprint: "c".into(),
            quantisation: "q".into(),
            tensor_geometry: "g".into(),
            kv_layout: "k".into(),
            latent_method: "m".into(),
            runtime: "r".into(),
        };
        Occ3ConnectorCapabilities {
            protocol_version: 1,
            connector_version: "test".into(),
            model_identity: identity,
            methods,
            tensor_formats: vec![],
            max_state_bytes: 0,
            state_ttl_seconds: 60,
            max_concurrent_handles: 1,
            cache_isolation: "test".into(),
            features: BTreeMap::new(),
        }
    }

    #[test]
    fn empty_registry_is_unavailable() {
        let registry = Occ3ConnectorRegistry::empty();
        assert!(registry.is_empty());
        assert!(registry.connector_ids().is_empty());
        assert!(registry.find_for(Occ3Method::KvHandoff, "fp").is_err());
    }

    #[test]
    fn registered_connector_matches_fingerprint() {
        let mut registry = Occ3ConnectorRegistry::empty();
        let connector = Arc::new(FakeConnector { id: "fake" });
        let snapshot = caps("glm-5.3", vec![Occ3Method::KvHandoff]);
        let fingerprint = snapshot.model_identity.fingerprint();
        registry.register(connector, snapshot);

        assert_eq!(registry.connector_ids(), vec!["fake".to_string()]);
        assert!(registry.supports("fake", Occ3Method::KvHandoff, &fingerprint));
        assert!(!registry.supports("fake", Occ3Method::StatebridgePrefix, &fingerprint));
        assert!(registry.find_for(Occ3Method::KvHandoff, &fingerprint).is_ok());
        assert!(registry.find_for(Occ3Method::KvHandoff, "other").is_err());
    }

    #[test]
    fn unregister_removes_support() {
        let mut registry = Occ3ConnectorRegistry::empty();
        let snapshot = caps("glm-5.3", vec![Occ3Method::StatebridgePrefix]);
        registry.register(Arc::new(FakeConnector { id: "fake" }), snapshot);
        registry.unregister("fake");
        assert!(registry.is_empty());
        assert!(registry.get("fake").is_none());
    }

    #[test]
    fn preference_orders_connectors() {
        let mut registry = Occ3ConnectorRegistry::empty();
        for id in ["zeta", "alpha", "mid"] {
            registry.register(
                Arc::new(FakeConnector { id: leak_static(id) }),
                caps(id, vec![Occ3Method::KvHandoff]),
            );
        }
        assert_eq!(registry.connector_ids(), vec!["zeta", "alpha", "mid"]);
        // Re-registering an existing id moves nothing: preference keeps
        // first-seen order.
        registry.register(
            Arc::new(FakeConnector { id: leak_static("alpha") }),
            caps("alpha", vec![Occ3Method::KvHandoff]),
        );
        assert_eq!(registry.connector_ids(), vec!["zeta", "alpha", "mid"]);
    }

    fn leak_static(s: &str) -> &'static str {
        match s {
            "zeta" => "zeta",
            "alpha" => "alpha",
            "mid" => "mid",
            _ => "other",
        }
    }
}
