//! OCC-3: latent communication between agents running the same exact
//! model on a compatible local runtime.
//!
//! This module is the GENERIC bridge: engine-agnostic, pure and
//! offline-testable, exactly like the rest of the OCC tree. It never
//! sees tensors, GPU pointers, CUDA types or engine protocols.
//!
//! Architecture:
//! - [`types`]: model identity, handles, envelope, delivery status.
//! - [`connector`]: the versioned `Occ3Connector` trait that runtime
//!   implementations (SGLang today; vLLM, llama.cpp tomorrow) satisfy.
//!   Implementations live OUTSIDE this tree.
//! - [`registry`]: live connector lookup with cached capability
//!   snapshots from real handshakes.
//! - [`negotiation`]: the deterministic eligibility ladder; strict
//!   same-exact-model rule enforced here.
//! - [`codec`]: the textual envelope that rides the ordinary
//!   agent-message string (same seam as OCC-2).
//! - [`mock`]: the deterministic fake connector used by the offline
//!   bridge tests (GATE 1); doubles as the reference implementation
//!   for real connectors.
//!
//! Campaign rules: no network, no model APIs, no filesystem access,
//! no clocks inside this tree. The embedding layer (daemon) owns I/O
//! and lifecycle; failures map to [`types::Occ3Error`] and always
//! carry a fallback classification.

pub mod codec;
pub mod connector;
pub mod inference;
pub mod mock;
pub mod negotiation;
pub mod registry;
pub mod types;

pub use codec::{
    build_envelope, decode_envelope, encode_envelope, render_preview, OCC3_HEADER,
};
pub use connector::{CaptureRequest, ConsumeRequest, Occ3Connector};
pub use negotiation::{
    advertisement_from_capability, negotiate_occ3, Occ3CapabilityAdvertisement,
    Occ3Decision, Occ3FallbackReason, Occ3NegotiationInput,
};
pub use registry::Occ3ConnectorRegistry;
pub use types::{
    Occ3ConnectorCapabilities, Occ3DeliveryStatus, Occ3Envelope, Occ3Error,
    Occ3HandleRef, Occ3Method, Occ3ModelIdentity, Occ3TransferMetrics,
    OCC3_MAX_SUMMARY_CHARS,
};
