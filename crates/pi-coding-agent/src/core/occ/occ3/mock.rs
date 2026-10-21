//! Deterministic mock OCC-3 connector for offline bridge tests (GATE 1).
//!
//! Proves the generic architecture without SGLang, DGX, GPUs or any
//! I/O. Also serves as the reference implementation of
//! [`super::connector::Occ3Connector`] for future connectors (vLLM,
//! llama.cpp, ...).
//!
//! Failure injection: every operation consults a scriptable fault
//! table; tests drive specific errors and expiry/generation edges.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;

use super::connector::{CaptureRequest, ConsumeRequest, Occ3Connector};
use super::types::{
    Occ3ConnectorCapabilities, Occ3Error, Occ3HandleRef, Occ3Method,
    Occ3ModelIdentity, Occ3TransferMetrics,
};

/// A fault the mock can be told to inject on the next matching
/// operation. Consumed on first use unless marked sticky.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MockFault {
    Unreachable,
    Timeout { timeout_ms: u64 },
    CaptureFails,
    ConsumeFails,
    ExpireNextHandle,
    BumpGeneration,
}

/// Internal record for one captured state.
#[derive(Debug, Clone)]
struct MockState {
    handle: Occ3HandleRef,
    receiver: String,
    consumed: bool,
    acknowledged: bool,
    size_bytes: u64,
}

/// The mock connector's shared state.
#[derive(Default)]
pub struct MockStateCore {
    states: BTreeMap<String, MockState>,
    generation: u64,
    counter: AtomicU64,
    faults: Vec<(String, MockFault, bool)>, // op, fault, sticky
}

/// Deterministic mock connector.
#[derive(Clone)]
pub struct MockOcc3Connector {
    id: &'static str,
    identity: Occ3ModelIdentity,
    methods: Vec<Occ3Method>,
    healthy: Arc<Mutex<bool>>,
    core: Arc<Mutex<MockStateCore>>,
    ttl_seconds: u64,
}

impl MockOcc3Connector {
    /// A healthy mock serving `identity` with `methods`.
    pub fn new(identity: Occ3ModelIdentity, methods: Vec<Occ3Method>) -> Self {
        Self {
            id: "mock-occ3",
            identity,
            methods,
            healthy: Arc::new(Mutex::new(true)),
            core: Arc::new(Mutex::new(MockStateCore::default())),
            ttl_seconds: 900,
        }
    }

    /// Capability snapshot as the registry expects it.
    pub fn capabilities(&self) -> Occ3ConnectorCapabilities {
        Occ3ConnectorCapabilities {
            protocol_version: 1,
            connector_version: "mock-1".into(),
            model_identity: self.identity.clone(),
            methods: self.methods.clone(),
            tensor_formats: vec!["mock".into()],
            max_state_bytes: 1 << 20,
            state_ttl_seconds: self.ttl_seconds,
            max_concurrent_handles: 8,
            cache_isolation: "mock: isolated".into(),
            features: BTreeMap::new(),
        }
    }

    /// Inject a fault for the next matching operation.
    pub fn inject_fault(&self, op: &str, fault: MockFault) {
        self.core
            .lock()
            .unwrap()
            .faults
            .push((op.to_string(), fault, false));
    }

    /// Set overall health.
    pub fn set_health(&self, healthy: bool) {
        *self.healthy.lock().unwrap() = healthy;
    }

    /// Force-expire every live handle (simulates eviction).
    pub fn expire_all(&self) {
        let mut core = self.core.lock().unwrap();
        for state in core.states.values_mut() {
            state.handle.expires_at_epoch = 0;
        }
    }

    /// Simulate a runtime restart: the generation bump invalidates all
    /// outstanding handles. Records are kept so consumes fail with the
    /// diagnostic `RuntimeGenerationChanged` rather than a generic
    /// expiry; `live_states` filters them out.
    pub fn restart_runtime(&self) {
        let mut core = self.core.lock().unwrap();
        core.generation += 1;
    }

    /// Number of live states: unconsumed, unexpired and created under
    /// the CURRENT runtime generation.
    pub fn live_states(&self) -> usize {
        let core = self.core.lock().unwrap();
        core.states
            .values()
            .filter(|s| {
                !s.consumed
                    && s.handle.expires_at_epoch > 0
                    && s.handle.runtime_generation == core.generation
            })
            .count()
    }

    fn take_fault(&self, op: &str) -> Option<MockFault> {
        let mut core = self.core.lock().unwrap();
        // Only error-class faults are consumed here; state-shaping
        // faults (ExpireNextHandle, BumpGeneration) stay in the table
        // for their dedicated handlers.
        let idx = core.faults.iter().position(|(fop, f, _)| {
            fop == op
                && !matches!(f, MockFault::ExpireNextHandle | MockFault::BumpGeneration)
        })?;
        let (_, fault, sticky) = core.faults.remove(idx);
        if sticky {
            core.faults.push((op.to_string(), fault.clone(), true));
        }
        Some(fault)
    }
}

#[async_trait]
impl Occ3Connector for MockOcc3Connector {
    fn connector_id(&self) -> &str {
        self.id
    }

    fn protocol_version(&self) -> u32 {
        1
    }

    async fn health(&self) -> Result<(), Occ3Error> {
        if *self.healthy.lock().unwrap() {
            Ok(())
        } else {
            Err(Occ3Error::ConnectorUnreachable {
                connector_id: self.id.into(),
            })
        }
    }

    async fn discover_capabilities(
        &self,
    ) -> Result<Occ3ConnectorCapabilities, Occ3Error> {
        if let Some(MockFault::Unreachable) = self.take_fault("discover") {
            return Err(Occ3Error::ConnectorUnreachable {
                connector_id: self.id.into(),
            });
        }
        Ok(self.capabilities())
    }

    async fn model_identity(&self) -> Result<Occ3ModelIdentity, Occ3Error> {
        Ok(self.identity.clone())
    }

    async fn capture_latent(
        &self,
        request: CaptureRequest,
    ) -> Result<Occ3HandleRef, Occ3Error> {
        match self.take_fault("capture") {
            Some(MockFault::Unreachable) => {
                return Err(Occ3Error::ConnectorUnreachable {
                    connector_id: self.id.into(),
                })
            }
            Some(MockFault::Timeout { timeout_ms }) => {
                return Err(Occ3Error::ConnectorTimeout {
                    connector_id: self.id.into(),
                    timeout_ms,
                })
            }
            Some(MockFault::CaptureFails) => {
                return Err(Occ3Error::TransferFailed {
                    reason: "injected capture failure".into(),
                })
            }
            _ => {}
        }
        let mut core = self.core.lock().unwrap();
        let seq = core.counter.fetch_add(1, Ordering::SeqCst);
        // ExpireNextHandle is detected and consumed here, not by
        // take_fault (whose match arms do not handle it).
        let before = core.faults.len();
        core.faults.retain(|(op, f, _)| {
            !(op == "capture" && *f == MockFault::ExpireNextHandle)
        });
        let expire_next = core.faults.len() != before;
        let expires_at = if expire_next { 0 } else { u64::MAX };
        let size = 4096 + seq;
        let handle = Occ3HandleRef {
            handle_id: format!("mock-handle-{seq:08x}"),
            connector_id: self.id.into(),
            model_fingerprint: self.identity.fingerprint(),
            runtime_generation: core.generation,
            expires_at_epoch: expires_at,
            state_size_bytes: size,
        };
        core.states.insert(
            handle.handle_id.clone(),
            MockState {
                handle: handle.clone(),
                receiver: request.receiver_session_id.clone(),
                consumed: false,
                acknowledged: false,
                size_bytes: size,
            },
        );
        Ok(handle)
    }

    async fn deliver_latent(&self, handle: &Occ3HandleRef) -> Result<(), Occ3Error> {
        let mut core = self.core.lock().unwrap();
        let generation = core.generation;
        let Some(state) = core.states.get_mut(&handle.handle_id) else {
            return Err(Occ3Error::HandleExpired {
                handle_id: handle.handle_id.clone(),
            });
        };
        let (state_generation, state_expiry) =
            (state.handle.runtime_generation, state.handle.expires_at_epoch);
        if state_generation != generation {
            return Err(Occ3Error::RuntimeGenerationChanged {
                expected: state_generation,
                current: generation,
            });
        }
        if state_expiry == 0 {
            return Err(Occ3Error::HandleExpired {
                handle_id: handle.handle_id.clone(),
            });
        }
        Ok(())
    }

    async fn consume_latent(
        &self,
        request: ConsumeRequest,
    ) -> Result<Occ3TransferMetrics, Occ3Error> {
        if matches!(self.take_fault("consume"), Some(MockFault::ConsumeFails)) {
            return Err(Occ3Error::TransferFailed {
                reason: "injected consume failure".into(),
            });
        }
        let mut core = self.core.lock().unwrap();
        let generation = core.generation;
        let Some(state) = core.states.get_mut(&request.handle.handle_id) else {
            return Err(Occ3Error::HandleExpired {
                handle_id: request.handle.handle_id.clone(),
            });
        };
        let (state_generation, state_expiry) =
            (state.handle.runtime_generation, state.handle.expires_at_epoch);
        if state_generation != generation {
            return Err(Occ3Error::RuntimeGenerationChanged {
                expected: state_generation,
                current: generation,
            });
        }
        if state_expiry == 0 {
            return Err(Occ3Error::HandleExpired {
                handle_id: request.handle.handle_id.clone(),
            });
        }
        let (already_consumed, authorised_receiver, size) =
            (state.consumed, state.receiver.clone(), state.size_bytes);
        if already_consumed {
            return Err(Occ3Error::HandleAlreadyConsumed {
                handle_id: request.handle.handle_id.clone(),
            });
        }
        if request.receiver_session_id != authorised_receiver {
            return Err(Occ3Error::Unauthorized);
        }
        state.consumed = true;
        Ok(Occ3TransferMetrics {
            capture_ms: 1,
            handoff_ms: 1,
            consume_ms: 1,
            state_bytes: size,
            state_lifetime_ms: 1,
        })
    }

    async fn acknowledge_delivery(&self, handle: &Occ3HandleRef) -> Result<(), Occ3Error> {
        let mut core = self.core.lock().unwrap();
        match core.states.get_mut(&handle.handle_id) {
            Some(state) if state.consumed => {
                state.acknowledged = true;
                Ok(())
            }
            Some(_) => Err(Occ3Error::TransferFailed {
                reason: "acknowledged before consumption".into(),
            }),
            None => Err(Occ3Error::HandleExpired {
                handle_id: handle.handle_id.clone(),
            }),
        }
    }

    async fn release_handle(&self, handle: &Occ3HandleRef) -> Result<(), Occ3Error> {
        self.core
            .lock()
            .unwrap()
            .states
            .remove(&handle.handle_id);
        Ok(())
    }

    async fn shutdown(&self) -> Result<(), Occ3Error> {
        self.core.lock().unwrap().states.clear();
        Ok(())
    }
}
