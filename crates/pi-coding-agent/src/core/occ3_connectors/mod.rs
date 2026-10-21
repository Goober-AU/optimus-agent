//! OCC-3 connector implementations (network side of the bridge).
//!
//! The generic bridge in [`crate::core::occ::occ3`] is pure and
//! offline-testable; these modules perform the I/O. A connector is
//! constructed from a model's `occ3` configuration (`models.json`) and
//! registered into the bridge's connector registry. Nothing in the
//! bridge tree may import these modules.

pub mod sglang;
