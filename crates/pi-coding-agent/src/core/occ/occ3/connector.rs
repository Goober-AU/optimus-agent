//! The OCC-3 connector contract: the versioned boundary between the pure
//! generic bridge and a runtime-specific latent-state engine.
//!
//! The bridge (this tree) is deterministic and offline-testable. A
//! connector implementation (e.g. the SGLang extension client, living
//! OUTSIDE this tree) performs all I/O. The trait below is the entire
//! contract surface; it never mentions tensors, GPU pointers, CUDA or
//! any specific inference engine.
//!
//! Control-plane only: handles are opaque ids, never payload bytes.

use async_trait::async_trait;
use std::time::Duration;

use super::types::{
    Occ3ConnectorCapabilities, Occ3Error, Occ3HandleRef, Occ3Method,
    Occ3ModelIdentity, Occ3TransferMetrics,
};

/// What a sender asks the connector to prepare.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureRequest {
    /// Agent-visible task/message identity for auditing.
    pub message_id: String,
    /// Sender session id.
    pub sender_session_id: String,
    /// The receiver session authorised to consume.
    pub receiver_session_id: String,
    /// Latent method to use.
    pub method: Occ3Method,
    /// Connector-specific capture payload (e.g. prompt context reference).
    /// Small by contract: never tensors.
    pub capture_context: String,
    /// Server-side state TTL requested by the bridge.
    pub ttl: Duration,
}

/// What the receiver asks the connector to do with a delivered handle.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConsumeRequest {
    /// Handle to consume.
    pub handle: Occ3HandleRef,
    /// Consuming session (must match the handle's authorised receiver).
    pub receiver_session_id: String,
    /// Where the injected state should land for the receiver's next
    /// inference (connector-specific routing key, small string).
    pub injection_target: String,
}

/// The connector contract. Every method is fallible and every failure is
/// fallback-classified by the bridge. Implementations must be cheap to
/// clone (shared state behind an Arc is the expected shape).
#[async_trait]
pub trait Occ3Connector: Send + Sync {
    /// Stable connector id (e.g. `sglang-occ3`).
    fn connector_id(&self) -> &str;

    /// OCC-3 protocol version this connector implements.
    fn protocol_version(&self) -> u32;

    /// Live health probe (cheap, no latent state created).
    async fn health(&self) -> Result<(), Occ3Error>;

    /// Discover capabilities as verified live. `Ok(capabilities)` means
    /// the runtime answered and its identity was checked.
    async fn discover_capabilities(&self)
        -> Result<Occ3ConnectorCapabilities, Occ3Error>;

    /// Verified live model identity. Must be checked against the
    /// sender/receiver identities by the bridge, never trusted from
    /// configuration alone.
    async fn model_identity(&self) -> Result<Occ3ModelIdentity, Occ3Error>;

    /// Capture a latent state server-side and return an opaque handle.
    async fn capture_latent(&self, request: CaptureRequest)
        -> Result<Occ3HandleRef, Occ3Error>;

    /// Authorise the receiver named in the handle to consume it, after
    /// the bridge has re-verified model-identity compatibility. The
    /// connector re-checks binding (receiver, model fingerprint,
    /// generation, expiry).
    async fn deliver_latent(&self, handle: &Occ3HandleRef)
        -> Result<(), Occ3Error>;

    /// Consume a latent state: inject it into the receiver's next
    /// inference context. On success the handle is marked consumed.
    async fn consume_latent(&self, request: ConsumeRequest)
        -> Result<Occ3TransferMetrics, Occ3Error>;

    /// Acknowledge semantic use after the receiver confirmed the
    /// transferred information was applied (bridge-verified).
    async fn acknowledge_delivery(&self, handle: &Occ3HandleRef)
        -> Result<(), Occ3Error>;

    /// Release server-side resources for a handle immediately.
    async fn release_handle(&self, handle: &Occ3HandleRef)
        -> Result<(), Occ3Error>;

    /// Shutdown hook: invalidate all handles this connector issued.
    async fn shutdown(&self) -> Result<(), Occ3Error>;
}

impl dyn Occ3Connector {
    /// Connector id as an owned string, for registry keys.
    pub fn connector_id_string(&self) -> String {
        self.connector_id().to_string()
    }
}
