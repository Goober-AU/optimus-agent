//! OCC-2 dialect registry: symbolic dialects as data, not code.
//!
//! A dialect is a codebook: a [`SymbolTable`] that maps the fixed field
//! inventory of the `optimus.agent-comm.v1` contract to short wire symbols,
//! plus the two separator characters symbolic field lines use (see
//! [`DialectGrammar`]). The codec in [`crate::core::occ::occ2`] is generic
//! over any validated [`DialectCard`], so dialects are data: the offline
//! discovery harness can evolve new symbol assignments, versions and
//! metrics without code changes.
//!
//! Integrity: every card carries `sha256`, the hash of its canonically
//! serialized codebook (grammar + symbol table; BTreeMap ordering makes
//! the serialization deterministic). The OCC-2 wire header carries the
//! first [`CODEBOOK_PREFIX_LEN`] hex characters of that hash, and a
//! decoder rejects any message whose header hash does not match the
//! registered card for the advertised dialect id and version
//! ([`DialectRegistry::resolve`]).
//!
//! Compatibility: `speaker_models`, `listener_models` and `task_types`
//! record which model pairs and task families a dialect was tuned for
//! (empty means unrestricted); `metrics` carries the offline evaluation
//! numbers behind the dialect. [`OccCapability`] advertises the dialects
//! and versions an agent understands, and
//! [`OccCapability::negotiate`] picks the best shared dialect between
//! two agents deterministically.
//!
//! Fallback: an unknown dialect id or version resolves to
//! [`Resolved::Unknown`]; callers must then conservatively fall back to
//! OCC-1 (see [`crate::core::occ::occ2::occ1_fallback`]). A registered
//! dialect whose header hash prefix does not match is reported as
//! [`Resolved::HashMismatch`] instead of silently accepted.
//!
//! Campaign rules: pure, deterministic, offline. No network, no model
//! APIs, no filesystem, no globals. Iteration is over ordered collections
//! (BTreeMap/BTreeSet) so results are reproducible regardless of hash
//! seeds.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

/// Id of the default hand-designed dialect.
pub const DEFAULT_DIALECT_ID: &str = "sym-v1";
/// Version of the default hand-designed dialect.
pub const DEFAULT_DIALECT_VERSION: u32 = 1;
/// Hex characters of the codebook sha256 carried on the wire header. The
/// full digest is 64 characters; the prefix trades tamper resistance
/// against header size.
pub const CODEBOOK_PREFIX_LEN: usize = 12;
/// Maximum dialect id length in bytes.
pub const MAX_DIALECT_ID_LEN: usize = 32;
/// Maximum base symbol length in bytes.
pub const MAX_SYMBOL_LEN: usize = 8;

/// Canonical names of the base wire slots every dialect must cover. The
/// inventory mirrors the fields the OCC-1 contract encoder emits, so any
/// message encodable in OCC-1 is encodable in any dialect and no
/// information is lost.
pub mod slots {
    /// Message id (OCC-1 `MSGID`).
    pub const MESSAGE_ID: &str = "message_id";
    /// Task id (OCC-1 `TASK`).
    pub const TASK_ID: &str = "task_id";
    /// Parent task id (OCC-1 `PARENT`).
    pub const PARENT_TASK_ID: &str = "parent_task_id";
    /// Trace id (OCC-1 `TRACE`).
    pub const TRACE_ID: &str = "trace_id";
    /// Sender identity (OCC-1 `SENDER`).
    pub const SENDER: &str = "sender";
    /// Recipient identity (OCC-1 `RECIPIENT`).
    pub const RECIPIENT: &str = "recipient";
    /// Message kind (OCC-1 `KIND`).
    pub const KIND: &str = "kind";
    /// Task status (OCC-1 `STATUS`).
    pub const STATUS: &str = "status";
    /// Sequence number (OCC-1 `SEQ`).
    pub const SEQUENCE: &str = "sequence";
    /// Timestamp (OCC-1 `TS`).
    pub const TIMESTAMP: &str = "timestamp";
    /// Expiry (OCC-1 `EXP`).
    pub const EXPIRY: &str = "expiry";
    /// Finding statement, indexed (OCC-1 `F<n>`).
    pub const FINDING_STATEMENT: &str = "finding.statement";
    /// Evidence `<kind>:<locator>`, indexed (OCC-1 `E<n>`).
    pub const EVIDENCE_REF: &str = "evidence.ref";
    /// Artifact path, indexed (OCC-1 `ART<n>`).
    pub const ARTIFACT: &str = "artifact";
    /// Verification claim (OCC-1 `V`).
    pub const VERIFICATION_CLAIM: &str = "verification.claim";
    /// Task dependency, indexed (OCC-1 `DEP<n>`).
    pub const DEPENDENCY: &str = "dependency";
    /// Next action, repeatable (OCC-1 `NEXT`).
    pub const NEXT_ACTION: &str = "next_action";
}

/// Canonical names of the subfield markers every dialect must cover.
pub mod subfields {
    /// Finding confidence (OCC-1 `FC<n>`).
    pub const FINDING_CONFIDENCE: &str = "finding.confidence";
    /// Finding evidence links (OCC-1 `FE<n>`).
    pub const FINDING_EVIDENCE: &str = "finding.evidence";
    /// Evidence line range (OCC-1 `EL<n>`).
    pub const EVIDENCE_LINE_RANGE: &str = "evidence.line_range";
    /// Evidence digest (OCC-1 `ED<n>`).
    pub const EVIDENCE_DIGEST: &str = "evidence.digest";
    /// Evidence revision (OCC-1 `ER<n>`).
    pub const EVIDENCE_REVISION: &str = "evidence.revision";
    /// Evidence verified flag (OCC-1 `EV<n>`).
    pub const EVIDENCE_VERIFIED: &str = "evidence.verified";
    /// Verification status (OCC-1 `VS`).
    pub const VERIFICATION_STATUS: &str = "verification.status";
    /// Verification method (OCC-1 `VM`).
    pub const VERIFICATION_METHOD: &str = "verification.method";
    /// Verification receipt (OCC-1 `VR`).
    pub const VERIFICATION_RECEIPT: &str = "verification.receipt";
}

/// The base slots every dialect must cover, in canonical order.
pub const REQUIRED_SLOTS: &[&str] = &[
    slots::MESSAGE_ID,
    slots::TASK_ID,
    slots::PARENT_TASK_ID,
    slots::TRACE_ID,
    slots::SENDER,
    slots::RECIPIENT,
    slots::KIND,
    slots::STATUS,
    slots::SEQUENCE,
    slots::TIMESTAMP,
    slots::EXPIRY,
    slots::FINDING_STATEMENT,
    slots::EVIDENCE_REF,
    slots::ARTIFACT,
    slots::VERIFICATION_CLAIM,
    slots::DEPENDENCY,
    slots::NEXT_ACTION,
];

/// The subfields every dialect must cover, in canonical order.
pub const REQUIRED_SUBFIELDS: &[&str] = &[
    subfields::FINDING_CONFIDENCE,
    subfields::FINDING_EVIDENCE,
    subfields::EVIDENCE_LINE_RANGE,
    subfields::EVIDENCE_DIGEST,
    subfields::EVIDENCE_REVISION,
    subfields::EVIDENCE_VERIFIED,
    subfields::VERIFICATION_STATUS,
    subfields::VERIFICATION_METHOD,
    subfields::VERIFICATION_RECEIPT,
];

/// The base slot a subfield hangs off, and whether its wire keys carry an
/// index. Findings and evidence are indexed groups; verification is not.
pub fn subfield_base(subfield: &str) -> Option<(&'static str, bool)> {
    match subfield {
        subfields::FINDING_CONFIDENCE | subfields::FINDING_EVIDENCE => {
            Some((slots::FINDING_STATEMENT, true))
        }
        subfields::EVIDENCE_LINE_RANGE
        | subfields::EVIDENCE_DIGEST
        | subfields::EVIDENCE_REVISION
        | subfields::EVIDENCE_VERIFIED => Some((slots::EVIDENCE_REF, true)),
        subfields::VERIFICATION_STATUS
        | subfields::VERIFICATION_METHOD
        | subfields::VERIFICATION_RECEIPT => Some((slots::VERIFICATION_CLAIM, false)),
        _ => None,
    }
}

/// Structural grammar of a dialect: the two separator characters symbolic
/// field lines use. Everything else about a dialect is symbol assignment.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DialectGrammar {
    /// Character separating the symbol from the value on a field line
    /// (`=` in `sym-v1`). Values may contain the operator; the split is at
    /// the first occurrence, so it never splits a value.
    pub operator: char,
    /// Character joining list items inside a value (`,` in `sym-v1`); used
    /// for the evidence references of a finding.
    pub list_separator: char,
}

/// The symbol assignment of a dialect: canonical slot and subfield names
/// to wire symbols and markers.
///
/// Wire keys are built as `<base symbol>[<index>][<subfield marker>]`:
/// base symbols are `A-Z` (unique, and never a prefix of another, so key
/// parsing is unambiguous), indices are decimal `1`-based, and subfield
/// markers are single `a-z` characters unique within their base slot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SymbolTable {
    /// Canonical base slot name (see [`slots`]) to base symbol.
    pub slots: BTreeMap<String, String>,
    /// Canonical subfield name (see [`subfields`]) to marker.
    pub subfields: BTreeMap<String, String>,
}

impl SymbolTable {
    /// Base symbol for a canonical slot name, when assigned.
    pub fn symbol(&self, slot: &str) -> Option<&str> {
        self.slots.get(slot).map(String::as_str)
    }

    /// Subfield marker for a canonical subfield name, when assigned.
    pub fn marker(&self, subfield: &str) -> Option<char> {
        self.subfields
            .get(subfield)
            .and_then(|marker| marker.chars().next())
    }
}

/// Offline evaluation numbers behind a dialect, filled by the discovery
/// harness. Defaults mark the dialect as unevaluated, not as good.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct DialectMetrics {
    /// Messages in the evaluation corpus (0 = unevaluated).
    pub samples: usize,
    /// Mean wire bytes per message as a fraction of the OCC-1 encoding
    /// (1.0 = parity).
    pub bytes_vs_occ1: f64,
    /// Exact round-trip fidelity on the corpus (1.0 required to ship).
    pub round_trip_fidelity: f64,
    /// Mean encode+decode latency per message, in microseconds.
    pub mean_latency_us: f64,
}

/// What a listener should do when it cannot resolve the dialect.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FallbackPolicy {
    /// Re-parse via OCC-1 (conservative default for every dialect until
    /// proven otherwise).
    Occ1,
    /// The dialect must be understood; no fallback is offered.
    None,
}

impl Default for FallbackPolicy {
    fn default() -> Self {
        FallbackPolicy::Occ1
    }
}

/// One dialect version: grammar, symbol table, integrity hash and
/// compatibility metadata. Dialects are data; the codec interprets them.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DialectCard {
    /// Dialect id, `[a-z][a-z0-9-]*` (e.g. `sym-v1`).
    pub dialect_id: String,
    /// Dialect version, >= 1. A different symbol table is a new version,
    /// never a silent mutation of an old one.
    pub version: u32,
    /// Structural grammar (separators).
    pub grammar: DialectGrammar,
    /// Symbol assignment for every required slot and subfield.
    pub symbol_table: SymbolTable,
    /// sha256 (hex) of the canonical serialization of grammar + symbol
    /// table; must equal [`codebook_sha256`] of the same codebook.
    pub sha256: String,
    /// Speaker models this dialect was tuned for; empty = unrestricted.
    pub speaker_models: Vec<String>,
    /// Listener models this dialect was tuned for; empty = unrestricted.
    pub listener_models: Vec<String>,
    /// Task types this dialect was tuned for; empty = unrestricted.
    pub task_types: Vec<String>,
    /// Offline evaluation metrics; defaults mean unevaluated.
    pub metrics: DialectMetrics,
    /// Fallback policy for listeners that cannot resolve the dialect.
    pub fallback: FallbackPolicy,
}

impl DialectCard {
    /// The default hand-designed `sym-v1` dialect: a fixed, readable
    /// symbol table (`TASK=T KIND=K STATUS=S FINDING=F EVIDENCE=E
    /// VERIF=V DEPENDS=D NEXT=N`, plus single-letter symbols for the
    /// remaining contract fields). A starting point only; the discovery
    /// engine will evolve it later.
    pub fn sym_v1() -> Self {
        let grammar = DialectGrammar {
            operator: '=',
            list_separator: ',',
        };
        let slot_list: &[(&str, &str)] = &[
            (slots::MESSAGE_ID, "M"),
            (slots::TASK_ID, "T"),
            (slots::PARENT_TASK_ID, "P"),
            (slots::TRACE_ID, "R"),
            (slots::SENDER, "A"),
            (slots::RECIPIENT, "B"),
            (slots::KIND, "K"),
            (slots::STATUS, "S"),
            (slots::SEQUENCE, "Q"),
            (slots::TIMESTAMP, "C"),
            (slots::EXPIRY, "X"),
            (slots::FINDING_STATEMENT, "F"),
            (slots::EVIDENCE_REF, "E"),
            (slots::ARTIFACT, "G"),
            (slots::VERIFICATION_CLAIM, "V"),
            (slots::DEPENDENCY, "D"),
            (slots::NEXT_ACTION, "N"),
        ];
        let subfield_list: &[(&str, &str)] = &[
            (subfields::FINDING_CONFIDENCE, "c"),
            (subfields::FINDING_EVIDENCE, "e"),
            (subfields::EVIDENCE_LINE_RANGE, "l"),
            (subfields::EVIDENCE_DIGEST, "d"),
            (subfields::EVIDENCE_REVISION, "r"),
            (subfields::EVIDENCE_VERIFIED, "v"),
            (subfields::VERIFICATION_STATUS, "s"),
            (subfields::VERIFICATION_METHOD, "m"),
            (subfields::VERIFICATION_RECEIPT, "r"),
        ];
        let symbol_table = SymbolTable {
            slots: slot_list
                .iter()
                .map(|(name, symbol)| ((*name).to_string(), (*symbol).to_string()))
                .collect(),
            subfields: subfield_list
                .iter()
                .map(|(name, marker)| ((*name).to_string(), (*marker).to_string()))
                .collect(),
        };
        let sha256 = codebook_sha256(&grammar, &symbol_table);
        Self {
            dialect_id: DEFAULT_DIALECT_ID.to_string(),
            version: DEFAULT_DIALECT_VERSION,
            grammar,
            symbol_table,
            sha256,
            speaker_models: Vec::new(),
            listener_models: Vec::new(),
            task_types: Vec::new(),
            metrics: DialectMetrics::default(),
            fallback: FallbackPolicy::Occ1,
        }
    }

    /// Validates the card: dialect id charset and length, version >= 1,
    /// usable separators, exact coverage of the required slot and
    /// subfield inventory, symbol and marker grammar, prefix-freedom of
    /// symbols (so key parsing is unambiguous), marker uniqueness within
    /// each base, and the stored hash matching the recomputed codebook
    /// hash.
    pub fn validate(&self) -> Result<(), DialectError> {
        if !valid_dialect_id(&self.dialect_id) {
            return Err(DialectError::InvalidDialectId(self.dialect_id.clone()));
        }
        if self.version == 0 {
            return Err(DialectError::InvalidVersion(self.version));
        }
        validate_separator(self.grammar.operator)?;
        validate_separator(self.grammar.list_separator)?;
        if self.grammar.operator == self.grammar.list_separator {
            return Err(DialectError::InvalidListSeparator(
                self.grammar.list_separator,
            ));
        }
        for (slot, symbol) in &self.symbol_table.slots {
            if !REQUIRED_SLOTS.contains(&slot.as_str()) {
                return Err(DialectError::UnknownSlot(slot.clone()));
            }
            if !valid_symbol(symbol) {
                return Err(DialectError::InvalidSymbol {
                    slot: slot.clone(),
                    reason: format!("symbols are 1..={MAX_SYMBOL_LEN} uppercase A-Z characters"),
                });
            }
        }
        for required in REQUIRED_SLOTS {
            if !self.symbol_table.slots.contains_key(*required) {
                return Err(DialectError::MissingSlot((*required).to_string()));
            }
        }
        // Prefix-freedom: no symbol may be a prefix of another (equality
        // included), so at most one symbol matches any wire key.
        for (slot_a, symbol_a) in &self.symbol_table.slots {
            for (slot_b, symbol_b) in &self.symbol_table.slots {
                if slot_a != slot_b && symbol_b.starts_with(symbol_a.as_str()) {
                    return Err(DialectError::SymbolCollision {
                        symbol: symbol_a.clone(),
                        slot: slot_a.clone(),
                        other_slot: slot_b.clone(),
                    });
                }
            }
        }
        for (subfield, marker) in &self.symbol_table.subfields {
            if !REQUIRED_SUBFIELDS.contains(&subfield.as_str()) {
                return Err(DialectError::UnknownSubfield(subfield.clone()));
            }
            if marker.chars().count() != 1 || !marker.chars().all(|c| c.is_ascii_lowercase()) {
                return Err(DialectError::InvalidSubfield {
                    subfield: subfield.clone(),
                    reason: "markers are a single lowercase a-z character".to_string(),
                });
            }
        }
        for required in REQUIRED_SUBFIELDS {
            if !self.symbol_table.subfields.contains_key(*required) {
                return Err(DialectError::MissingSubfield((*required).to_string()));
            }
        }
        // Marker uniqueness is scoped to the base slot: two subfields of
        // the same base cannot share a marker.
        for (subfield_a, marker_a) in &self.symbol_table.subfields {
            for (subfield_b, marker_b) in &self.symbol_table.subfields {
                if subfield_a == subfield_b || marker_a != marker_b {
                    continue;
                }
                let (base_a, _) = match subfield_base(subfield_a) {
                    Some(base) => base,
                    None => continue,
                };
                let (base_b, _) = match subfield_base(subfield_b) {
                    Some(base) => base,
                    None => continue,
                };
                if base_a == base_b {
                    return Err(DialectError::SubfieldCollision {
                        marker: marker_a.clone(),
                        subfield: subfield_a.clone(),
                        other: subfield_b.clone(),
                    });
                }
            }
        }
        let computed = codebook_sha256(&self.grammar, &self.symbol_table);
        if computed != self.sha256 {
            return Err(DialectError::HashMismatch {
                stored: self.sha256.clone(),
                computed,
            });
        }
        Ok(())
    }
}

/// A dialect validation failure.
#[derive(Debug, Error)]
pub enum DialectError {
    /// The dialect id was not `[a-z][a-z0-9-]*` within the length bound.
    #[error(
        "dialect id {0:?} is malformed (expected [a-z][a-z0-9-]*, 1..={MAX_DIALECT_ID_LEN} bytes)"
    )]
    InvalidDialectId(String),
    /// The version was zero.
    #[error("dialect version must be >= 1, got {0}")]
    InvalidVersion(u32),
    /// A base symbol violated the symbol grammar.
    #[error("symbol for slot {slot:?} is invalid: {reason}")]
    InvalidSymbol { slot: String, reason: String },
    /// A subfield marker violated the marker grammar.
    #[error("subfield marker for {subfield:?} is invalid: {reason}")]
    InvalidSubfield { subfield: String, reason: String },
    /// The operator character is not usable on a field line.
    #[error("separator {0:?} is not usable (printable ASCII, not alphanumeric and not '#')")]
    InvalidSeparator(char),
    /// The list separator equals the operator.
    #[error("list separator {0:?} equals the operator; they must differ")]
    InvalidListSeparator(char),
    /// The table contains a slot outside the canonical inventory.
    #[error("slot {0:?} is not part of the OCC-2 field inventory")]
    UnknownSlot(String),
    /// The table is missing a required slot.
    #[error("required slot {0:?} is missing from the symbol table")]
    MissingSlot(String),
    /// The table contains a subfield outside the canonical inventory.
    #[error("subfield {0:?} is not part of the OCC-2 field inventory")]
    UnknownSubfield(String),
    /// The table is missing a required subfield.
    #[error("required subfield {0:?} is missing from the symbol table")]
    MissingSubfield(String),
    /// One symbol is a prefix of (or equal to) another.
    #[error("symbol {symbol:?} for slot {slot:?} collides with slot {other_slot:?}; symbols must be prefix-free")]
    SymbolCollision {
        symbol: String,
        slot: String,
        other_slot: String,
    },
    /// Two subfields of one base share a marker.
    #[error(
        "subfield marker {marker:?} for {subfield:?} collides with {other:?} on the same base"
    )]
    SubfieldCollision {
        marker: String,
        subfield: String,
        other: String,
    },
    /// The stored hash does not match the recomputed codebook hash.
    #[error("stored codebook sha256 {stored:?} does not match the recomputed hash {computed:?}")]
    HashMismatch { stored: String, computed: String },
    /// The (dialect id, version) pair is already registered.
    #[error("dialect {dialect:?}@{version} is already registered")]
    Duplicate { dialect: String, version: u32 },
}

fn valid_dialect_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= MAX_DIALECT_ID_LEN
        && id.chars().next().is_some_and(|c| c.is_ascii_lowercase())
        && id
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

fn valid_symbol(symbol: &str) -> bool {
    !symbol.is_empty()
        && symbol.len() <= MAX_SYMBOL_LEN
        && symbol.chars().all(|c| c.is_ascii_uppercase())
}

fn validate_separator(separator: char) -> Result<(), DialectError> {
    let usable =
        (' '..='~').contains(&separator) && !separator.is_ascii_alphanumeric() && separator != '#';
    if usable {
        Ok(())
    } else {
        Err(DialectError::InvalidSeparator(separator))
    }
}

/// View used only for canonical serialization; field order is fixed and
/// BTreeMap iteration is sorted, so the serialization is deterministic.
#[derive(Serialize)]
struct CanonicalCodebook<'a> {
    grammar: &'a DialectGrammar,
    symbol_table: &'a SymbolTable,
}

/// Canonical JSON serialization of a codebook (grammar + symbol table).
/// Deterministic: struct field order is fixed and BTreeMap keys iterate
/// sorted, independent of insertion order.
pub fn canonical_codebook(grammar: &DialectGrammar, symbol_table: &SymbolTable) -> String {
    serde_json::to_string(&CanonicalCodebook {
        grammar,
        symbol_table,
    })
    .expect("canonical codebook serialization is infallible for plain maps and chars")
}

/// sha256 (hex) of the canonical codebook serialization.
pub fn codebook_sha256(grammar: &DialectGrammar, symbol_table: &SymbolTable) -> String {
    format!(
        "{:x}",
        Sha256::digest(canonical_codebook(grammar, symbol_table).as_bytes())
    )
}

/// Outcome of resolving a wire header against a registry.
#[derive(Debug, Clone, PartialEq)]
pub enum Resolved<'a> {
    /// A known dialect whose codebook hash matches the header prefix.
    Dialect(&'a DialectCard),
    /// The dialect id or version is not registered; the caller must
    /// conservatively fall back to OCC-1.
    Unknown {
        /// Advertised dialect id.
        dialect: String,
        /// Advertised version.
        version: u32,
    },
    /// The dialect is registered but the header hash prefix does not
    /// match the registered card; treat as tampering or corruption, not
    /// as a different dialect.
    HashMismatch {
        /// Advertised dialect id.
        dialect: String,
        /// Advertised version.
        version: u32,
        /// Hash prefix carried on the wire header.
        advertised: String,
        /// Hash prefix of the registered card.
        registered: String,
    },
}

/// Registry of dialect cards keyed by (dialect id, version). Pure data:
/// no I/O, no globals; build one per scope that needs it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct DialectRegistry {
    cards: BTreeMap<(String, u32), DialectCard>,
}

impl DialectRegistry {
    /// An empty registry; every message then resolves as unknown and the
    /// caller falls back to OCC-1.
    pub fn new() -> Self {
        Self::default()
    }

    /// A registry preloaded with the default `sym-v1` dialect.
    pub fn with_sym_v1() -> Self {
        let mut registry = Self::new();
        registry
            .insert(DialectCard::sym_v1())
            .expect("the built-in sym-v1 card is valid");
        registry
    }

    /// Inserts a validated card. Duplicate (dialect id, version) pairs
    /// are rejected: a changed codebook is a new version, never an
    /// in-place mutation of a registered one.
    pub fn insert(&mut self, card: DialectCard) -> Result<(), DialectError> {
        card.validate()?;
        let key = (card.dialect_id.clone(), card.version);
        if self.cards.contains_key(&key) {
            return Err(DialectError::Duplicate {
                dialect: card.dialect_id,
                version: card.version,
            });
        }
        self.cards.insert(key, card);
        Ok(())
    }

    /// The card for an exact (dialect id, version), if registered.
    pub fn get(&self, dialect_id: &str, version: u32) -> Option<&DialectCard> {
        self.cards.get(&(dialect_id.to_string(), version))
    }

    /// The highest registered version of a dialect id, if any.
    pub fn latest(&self, dialect_id: &str) -> Option<&DialectCard> {
        self.cards
            .iter()
            .filter(|((id, _), _)| id == dialect_id)
            .next_back()
            .map(|(_, card)| card)
    }

    /// Resolves a wire header (dialect id, version, codebook hash
    /// prefix). Unknown ids and versions resolve to
    /// [`Resolved::Unknown`]: the conservative action is to fall back to
    /// OCC-1. A registered dialect with a non-matching hash prefix is
    /// [`Resolved::HashMismatch`].
    pub fn resolve(&self, dialect_id: &str, version: u32, codebook_prefix: &str) -> Resolved<'_> {
        match self.get(dialect_id, version) {
            None => Resolved::Unknown {
                dialect: dialect_id.to_string(),
                version,
            },
            Some(card) => {
                if card.sha256.starts_with(codebook_prefix) {
                    Resolved::Dialect(card)
                } else {
                    Resolved::HashMismatch {
                        dialect: dialect_id.to_string(),
                        version,
                        advertised: codebook_prefix.to_string(),
                        registered: card.sha256.chars().take(codebook_prefix.len()).collect(),
                    }
                }
            }
        }
    }

    /// Number of registered cards.
    pub fn len(&self) -> usize {
        self.cards.len()
    }

    /// True when no card is registered.
    pub fn is_empty(&self) -> bool {
        self.cards.is_empty()
    }

    /// Iterates the registered cards in (dialect id, version) order.
    pub fn iter(&self) -> impl Iterator<Item = &DialectCard> {
        self.cards.values()
    }
}

/// What an agent advertises about its OCC capability: whether it speaks
/// OCC-1, and which OCC-2 dialect versions it understands.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OccCapability {
    /// OCC-1 support (always true for this build; carried for explicit
    /// negotiation).
    pub occ1: bool,
    /// Dialect id to the set of versions understood.
    pub dialects: BTreeMap<String, BTreeSet<u32>>,
    /// OCC-3 latent capability. Absent in older peers: they fall back to
    /// OCC-1/OCC-2. See `core::occ::occ3::negotiation` for the ladder.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occ3: Option<crate::core::occ::occ3::Occ3CapabilityAdvertisement>,
}

impl OccCapability {
    /// This codec's capability: OCC-1 plus the default `sym-v1`
    /// dialect.
    pub fn here() -> Self {
        Self {
            occ1: true,
            dialects: BTreeMap::from([(
                DEFAULT_DIALECT_ID.to_string(),
                BTreeSet::from([DEFAULT_DIALECT_VERSION]),
            )]),
            occ3: None,
        }
    }

    /// True when the exact dialect version is understood.
    pub fn supports(&self, dialect_id: &str, version: u32) -> bool {
        self.dialects
            .get(dialect_id)
            .is_some_and(|versions| versions.contains(&version))
    }

    /// Best shared dialect with a peer: the highest version both support,
    /// tie-broken by the lexicographically smallest dialect id. `None`
    /// means no shared dialect; the pair falls back to OCC-1.
    pub fn negotiate(&self, peer: &OccCapability) -> Option<(String, u32)> {
        let mut best: Option<(String, u32)> = None;
        for (dialect, versions) in &self.dialects {
            let Some(peer_versions) = peer.dialects.get(dialect) else {
                continue;
            };
            let Some(version) = versions.intersection(peer_versions).copied().max() else {
                continue;
            };
            // BTreeMap iterates dialect ids in ascending order, so on an
            // equal version the lexicographically smaller id is kept.
            if best
                .as_ref()
                .is_none_or(|(_, best_version)| *best_version < version)
            {
                best = Some((dialect.clone(), version));
            }
        }
        best
    }

    /// Compact human-readable advertisement, e.g.
    /// `OCC2CAP occ1=1 sym-v1@1`. Machine exchange uses the serde
    /// forms; this rendering is for logs and debugging.
    pub fn render(&self) -> String {
        let mut out = String::from("OCC2CAP");
        out.push_str(if self.occ1 { " occ1=1" } else { " occ1=0" });
        for (dialect, versions) in &self.dialects {
            let joined: Vec<String> = versions.iter().map(|v| v.to_string()).collect();
            out.push_str(&format!(" {dialect}@{}", joined.join(",")));
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a sym-v1 card with one slot symbol overridden.
    fn sym_v1_with_slot(slot: &str, symbol: &str) -> DialectCard {
        let mut card = DialectCard::sym_v1();
        card.symbol_table
            .slots
            .insert(slot.to_string(), symbol.to_string());
        card.sha256 = codebook_sha256(&card.grammar, &card.symbol_table);
        card
    }

    /// Build a sym-v1 card with one subfield marker overridden.
    fn sym_v1_with_subfield(subfield: &str, marker: &str) -> DialectCard {
        let mut card = DialectCard::sym_v1();
        card.symbol_table
            .subfields
            .insert(subfield.to_string(), marker.to_string());
        card.sha256 = codebook_sha256(&card.grammar, &card.symbol_table);
        card
    }

    #[test]
    fn sym_v1_validates_and_its_hash_is_self_consistent() {
        let card = DialectCard::sym_v1();
        card.validate().unwrap();
        assert_eq!(
            card.sha256,
            codebook_sha256(&card.grammar, &card.symbol_table)
        );
        assert_eq!(card.sha256.len(), 64);
        assert_eq!(DialectCard::sym_v1().sha256, card.sha256);
        assert_eq!(card.fallback, FallbackPolicy::Occ1);
        assert_eq!(card.dialect_id, DEFAULT_DIALECT_ID);
        assert_eq!(card.version, DEFAULT_DIALECT_VERSION);
        // The task's suggested starting table is honored.
        assert_eq!(card.symbol_table.symbol(slots::TASK_ID), Some("T"));
        assert_eq!(card.symbol_table.symbol(slots::KIND), Some("K"));
        assert_eq!(card.symbol_table.symbol(slots::STATUS), Some("S"));
        assert_eq!(
            card.symbol_table.symbol(slots::FINDING_STATEMENT),
            Some("F")
        );
        assert_eq!(card.symbol_table.symbol(slots::EVIDENCE_REF), Some("E"));
        assert_eq!(
            card.symbol_table.symbol(slots::VERIFICATION_CLAIM),
            Some("V")
        );
        assert_eq!(card.symbol_table.symbol(slots::DEPENDENCY), Some("D"));
        assert_eq!(card.symbol_table.symbol(slots::NEXT_ACTION), Some("N"));
    }

    #[test]
    fn the_codebook_hash_covers_every_symbol_and_separator() {
        let baseline = DialectCard::sym_v1();
        let changed_slot = sym_v1_with_slot(slots::TRACE_ID, "Y");
        let changed_subfield = sym_v1_with_subfield(subfields::FINDING_CONFIDENCE, "z");
        let changed_operator = {
            let mut card = DialectCard::sym_v1();
            card.grammar.operator = '|';
            card.sha256 = codebook_sha256(&card.grammar, &card.symbol_table);
            card
        };
        assert_ne!(changed_slot.sha256, baseline.sha256);
        assert_ne!(changed_subfield.sha256, baseline.sha256);
        assert_ne!(changed_operator.sha256, baseline.sha256);
        assert_ne!(changed_slot.sha256, changed_subfield.sha256);
        // The mutated cards validate (their hash was recomputed) and
        // differ only in the codebook.
        changed_slot.validate().unwrap();
        changed_subfield.validate().unwrap();
        changed_operator.validate().unwrap();
    }

    #[test]
    fn canonical_codebook_is_insertion_order_independent() {
        let mut a = SymbolTable {
            slots: BTreeMap::new(),
            subfields: BTreeMap::new(),
        };
        let mut b = SymbolTable {
            slots: BTreeMap::new(),
            subfields: BTreeMap::new(),
        };
        // Insert the same pairs in opposite orders.
        for (name, symbol) in [
            (slots::TASK_ID, "T"),
            (slots::KIND, "K"),
            (slots::STATUS, "S"),
            (slots::FINDING_STATEMENT, "F"),
        ] {
            a.slots.insert(name.to_string(), symbol.to_string());
        }
        for (name, symbol) in [
            (slots::FINDING_STATEMENT, "F"),
            (slots::STATUS, "S"),
            (slots::KIND, "K"),
            (slots::TASK_ID, "T"),
        ] {
            b.slots.insert(name.to_string(), symbol.to_string());
        }
        let grammar = DialectGrammar {
            operator: '=',
            list_separator: ',',
        };
        assert_eq!(
            canonical_codebook(&grammar, &a),
            canonical_codebook(&grammar, &b)
        );
        assert_eq!(codebook_sha256(&grammar, &a), codebook_sha256(&grammar, &b));
    }

    #[test]
    fn sym_v1_card_serde_round_trips() {
        let card = DialectCard::sym_v1();
        let json = serde_json::to_string(&card).unwrap();
        let back: DialectCard = serde_json::from_str(&json).unwrap();
        assert_eq!(back, card);
        // A card serialized through JSON still validates (hash included).
        back.validate().unwrap();
    }

    #[test]
    fn registry_insert_lookup_latest_and_duplicates() {
        let mut registry = DialectRegistry::new();
        assert!(registry.is_empty());
        registry.insert(DialectCard::sym_v1()).unwrap();
        assert_eq!(registry.len(), 1);
        let card = registry
            .get(DEFAULT_DIALECT_ID, DEFAULT_DIALECT_VERSION)
            .expect("sym-v1@1 is registered");
        assert_eq!(card, &DialectCard::sym_v1());
        assert_eq!(
            registry.latest(DEFAULT_DIALECT_ID),
            Some(&DialectCard::sym_v1())
        );
        assert_eq!(registry.latest("nope"), None);

        // A second version of the same dialect registers fine.
        let mut v2 = DialectCard::sym_v1();
        v2.version = 2;
        v2.symbol_table
            .slots
            .insert(slots::TRACE_ID.to_string(), "Y".to_string());
        v2.sha256 = codebook_sha256(&v2.grammar, &v2.symbol_table);
        registry.insert(v2.clone()).unwrap();
        assert_eq!(registry.latest(DEFAULT_DIALECT_ID), Some(&v2));
        // The exact version is still retrievable.
        assert_eq!(
            registry.get(DEFAULT_DIALECT_ID, 1),
            Some(&DialectCard::sym_v1())
        );

        // Duplicate (id, version) is rejected.
        let err = registry
            .insert(DialectCard::sym_v1())
            .expect_err("duplicate rejected");
        assert!(matches!(err, DialectError::Duplicate { .. }));
    }

    #[test]
    fn registry_rejects_invalid_cards() {
        let cases: Vec<(&str, DialectCard, fn(&DialectError) -> bool)> = vec![
            (
                "missing slot",
                {
                    let mut card = DialectCard::sym_v1();
                    card.symbol_table.slots.remove(slots::TASK_ID);
                    card
                },
                |error| matches!(error, DialectError::MissingSlot(slot) if slot == "task_id"),
            ),
            (
                "unknown slot",
                {
                    let mut card = DialectCard::sym_v1();
                    card.symbol_table
                        .slots
                        .insert("bogus".to_string(), "Y".to_string());
                    card
                },
                |error| matches!(error, DialectError::UnknownSlot(slot) if slot == "bogus"),
            ),
            (
                "lowercase symbol",
                sym_v1_with_slot(slots::TASK_ID, "t"),
                |error| matches!(error, DialectError::InvalidSymbol { .. }),
            ),
            (
                "symbol with digits",
                sym_v1_with_slot(slots::TASK_ID, "T1"),
                |error| matches!(error, DialectError::InvalidSymbol { .. }),
            ),
            (
                "prefix collision",
                sym_v1_with_slot(slots::TRACE_ID, "TA"),
                |error| matches!(error, DialectError::SymbolCollision { .. }),
            ),
            (
                "duplicate symbol",
                sym_v1_with_slot(slots::TASK_ID, "Q"),
                |error| matches!(error, DialectError::SymbolCollision { .. }),
            ),
            (
                "two-character marker",
                sym_v1_with_subfield(subfields::FINDING_CONFIDENCE, "cc"),
                |error| matches!(error, DialectError::InvalidSubfield { .. }),
            ),
            (
                "uppercase marker",
                sym_v1_with_subfield(subfields::FINDING_CONFIDENCE, "C"),
                |error| matches!(error, DialectError::InvalidSubfield { .. }),
            ),
            (
                "marker collision within base",
                sym_v1_with_subfield(subfields::EVIDENCE_DIGEST, "l"),
                |error| matches!(error, DialectError::SubfieldCollision { .. }),
            ),
            (
                "alphanumeric operator",
                {
                    let mut card = DialectCard::sym_v1();
                    card.grammar.operator = 'A';
                    card
                },
                |error| matches!(error, DialectError::InvalidSeparator('A')),
            ),
            (
                "sentinel-reserved operator",
                {
                    let mut card = DialectCard::sym_v1();
                    card.grammar.operator = '#';
                    card
                },
                |error| matches!(error, DialectError::InvalidSeparator('#')),
            ),
            (
                "separator equals operator",
                {
                    let mut card = DialectCard::sym_v1();
                    card.grammar.list_separator = '=';
                    card
                },
                |error| matches!(error, DialectError::InvalidListSeparator('=')),
            ),
            (
                "stale stored hash",
                {
                    let mut card = DialectCard::sym_v1();
                    card.sha256 = "0".repeat(64);
                    card
                },
                |error| matches!(error, DialectError::HashMismatch { .. }),
            ),
            (
                "bad dialect id",
                {
                    let mut card = DialectCard::sym_v1();
                    card.dialect_id = "Sym_V1".to_string();
                    card
                },
                |error| matches!(error, DialectError::InvalidDialectId(_)),
            ),
            (
                "zero version",
                {
                    let mut card = DialectCard::sym_v1();
                    card.version = 0;
                    card
                },
                |error| matches!(error, DialectError::InvalidVersion(0)),
            ),
        ];
        let mut registry = DialectRegistry::new();
        for (label, card, predicate) in cases {
            let error = registry
                .insert(card)
                .err()
                .unwrap_or_else(|| panic!("{label}: expected rejection"));
            assert!(predicate(&error), "{label}: unexpected error {error}");
        }
        assert!(registry.is_empty(), "no invalid card may be inserted");
    }

    #[test]
    fn resolve_reports_dialect_unknown_and_hash_mismatch() {
        let registry = DialectRegistry::with_sym_v1();
        let card = DialectCard::sym_v1();
        let prefix = &card.sha256[..CODEBOOK_PREFIX_LEN];

        assert_eq!(
            registry.resolve(DEFAULT_DIALECT_ID, DEFAULT_DIALECT_VERSION, prefix),
            Resolved::Dialect(
                registry
                    .get(DEFAULT_DIALECT_ID, DEFAULT_DIALECT_VERSION)
                    .unwrap()
            )
        );
        // Unknown dialect id.
        assert_eq!(
            registry.resolve("zzz", 1, prefix),
            Resolved::Unknown {
                dialect: "zzz".to_string(),
                version: 1
            }
        );
        // Unknown version of a known dialect.
        assert_eq!(
            registry.resolve(DEFAULT_DIALECT_ID, 9, prefix),
            Resolved::Unknown {
                dialect: DEFAULT_DIALECT_ID.to_string(),
                version: 9
            }
        );
        // Hash prefix mismatch.
        let tampered: String = prefix
            .chars()
            .map(|c| if c == 'f' { '0' } else { 'f' })
            .collect();
        assert_eq!(
            registry.resolve(DEFAULT_DIALECT_ID, DEFAULT_DIALECT_VERSION, &tampered),
            Resolved::HashMismatch {
                dialect: DEFAULT_DIALECT_ID.to_string(),
                version: DEFAULT_DIALECT_VERSION,
                advertised: tampered,
                registered: prefix.to_string(),
            }
        );
    }

    #[test]
    fn capability_negotiation_is_deterministic() {
        let here = OccCapability::here();
        assert!(here.occ1);
        assert!(here.supports(DEFAULT_DIALECT_ID, DEFAULT_DIALECT_VERSION));
        assert!(!here.supports(DEFAULT_DIALECT_ID, 2));
        assert!(!here.supports("other", 1));
        assert!(here.render().contains("sym-v1@1"));

        let plain = OccCapability {
            occ1: true,
            dialects: BTreeMap::new(),
            occ3: None,
};
        assert_eq!(here.negotiate(&plain), None);
        assert_eq!(plain.negotiate(&here), None);
        assert_eq!(
            here.negotiate(&here),
            Some((DEFAULT_DIALECT_ID.to_string(), DEFAULT_DIALECT_VERSION))
        );

        // Highest shared version wins; ties break to the smallest id.
        let peer = OccCapability {
            occ1: true,
            dialects: BTreeMap::from([
                ("alpha".to_string(), BTreeSet::from([1u32, 2])),
                ("beta".to_string(), BTreeSet::from([1u32, 5])),
            ]),
            occ3: None,
};
        assert_eq!(peer.negotiate(&peer), Some(("beta".to_string(), 5)));

        let tie = OccCapability {
            occ1: true,
            dialects: BTreeMap::from([
                ("beta".to_string(), BTreeSet::from([1u32])),
                ("alpha".to_string(), BTreeSet::from([1u32])),
            ]),
            occ3: None,
};
        assert_eq!(tie.negotiate(&tie), Some(("alpha".to_string(), 1)));

        // Partial overlap: only the shared id negotiates.
        let a = OccCapability {
            occ1: true,
            dialects: BTreeMap::from([("a-only".to_string(), BTreeSet::from([7u32]))]),
            occ3: None,
};
        assert_eq!(a.negotiate(&peer), None);
        assert_eq!(peer.negotiate(&a), None);
    }
}
