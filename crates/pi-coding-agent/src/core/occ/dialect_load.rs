//! DIALECT/1 research-card promotion: load, validate, translate, register.
//!
//! This module is the promotion path from the offline dialect-evolution
//! research harness (`research/symbolic_comm/dialect.py`) into the
//! production OCC-2 runtime. It is deliberately a *loader*, not a
//! hand-edited card: the frozen research artifact stays read-only on
//! disk and every promoted byte is derived from it through explicit,
//! validated translation rules.
//!
//! What the loader does:
//! 1. Parses a DIALECT/1 card (JSON text) and verifies its content
//!    checksum: sha256 over the canonical JSON body, byte-identical to
//!    the research harness's `DialectCard.checksum()` (recursively
//!    sorted object keys, compact separators, exactly the ten body
//!    keys).
//! 2. Validates schema, version, grammar and resource bounds. Anything
//!    the loader does not fully understand is rejected with an error
//!    naming it; features are never silently dropped.
//! 3. Translates the card into a production [`DialectCard`] (registry
//!    identity for the OCC-2 codec) under the experimental id
//!    `sym-v1-pt-pt-r1`, registered only through the explicit
//!    [`promoted_registry`] constructor. `DialectRegistry::with_sym_v1()`
//!    and the default dialect are untouched.
//! 4. Provides the DIALECT/1 wire codec (`encode_dialect1` /
//!    `decode_dialect1`), a faithful mirror of the research encoder, so
//!    wire text produced by the frozen research card decodes losslessly
//!    here, and production-encoded wire is byte-identical to the
//!    research encoder's output.
//!
//! # Format mapping (DIALECT/1 -> production)
//!
//! | DIALECT/1 construct | Production status |
//! |---|---|
//! | 16 fixed key aliases + `F`/`E`/`ART`/`DEP` families | directly representable: the 26 OCC-1 keys map 1:1 onto the 17 canonical slots + 9 subfields |
//! | lowercase 1-2 char aliases | translated: base symbols are uppercased; a collision with production's prefix-freedom rule is repaired deterministically (recorded in [`SymbolSubstitution`]) |
//! | subfield aliases (`FC`,`FE`,`EL`,`ED`,`ER`,`EV`,`VS`,`VM`,`VR`) | translated: must be single lowercase chars, matching production subfield markers |
//! | value symbol tables (closed enums) | DIALECT/1-wire only: applied by [`ResearchDialectCard::encode_dialect1`]; not part of the OCC-2 symbol-table codec |
//! | microtemplates (prefix, regex) | DIALECT/1-wire only: declarative, compiled with the linear-time `regex` crate |
//! | field defaults (elision) | DIALECT/1-wire only |
//! | packings (`E`,`FE`,`NEXT`,`DEP`,`ART`,`FC`) | DIALECT/1-wire only |
//! | never-compress classes | DIALECT/1-wire only |
//! | `usage_rules` prose | inert metadata: checksum-verified, never rendered into any prompt |
//!
//! # Safety model
//!
//! - Cards are declarative data. Nothing in a card is executed. Regexes
//!   are compiled with the `regex` crate (no arbitrary code execution,
//!   no catastrophic backtracking); everything else is table lookups and
//!   string prefix replacement.
//! - Every serde model uses `deny_unknown_fields`, and the top-level
//!   scan rejects unknown keys: an unsupported construct surfaces as an
//!   error that names it, never as a silent drop.
//! - Resource bounds (card size, template count, string lengths) cap
//!   hostile input.
//! - The promoted production card is never a default and, because its
//!   metrics are honestly unevaluated, `select_occ2_dialect` refuses it
//!   without the development override.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::core::occ::contract::{
    AgentCommMessage, ContractError, EvidenceRef, Finding, Verification, AGENT_COMM_PROTOCOL,
};
use crate::core::occ::occ1::is_known_kind;
use crate::core::occ::dialect::{
    codebook_sha256, slots, subfields, DialectCard, DialectGrammar, DialectRegistry,
    FallbackPolicy, SymbolTable, REQUIRED_SLOTS, REQUIRED_SUBFIELDS,
};

/// Wire header of every DIALECT/1 message (research grammar).
pub const DIALECT1_HEADER: &str = "D/1";
/// Card format marker this loader understands.
pub const DIALECT1_FORMAT: &str = "DIALECT/1";
/// Production dialect id assigned to the promoted card. `-r1` marks the
/// first promotion revision of the research card `sym-v1-pt-pt`.
pub const PROMOTED_DIALECT_ID: &str = "sym-v1-pt-pt-r1";
/// Production version assigned to the promoted card.
pub const PROMOTED_DIALECT_VERSION: u32 = 1;
/// The frozen research card, embedded at compile time so the promoted
/// path needs no filesystem access at runtime. The on-disk artifact
/// stays read-only; tests assert the two stay identical.
pub const FROZEN_CARD_JSON: &str =
    include_str!("../../../../../research/symbolic_comm/dialect-card.json");
/// Checksum of the frozen card, pinned by the research harness.
pub const FROZEN_CARD_CHECKSUM: &str =
    "17836322dced3bb56e10bb0466abb5cb8d463ac6e4aba471f906ad5f24a4d49b";
/// Production dialect id assigned to the promoted DIALECT/2 winner
/// (dialect evolution v2, `research/symbolic_comm2/dialect2-card.json`).
pub const PROMOTED2_DIALECT_ID: &str = "sym-v2-d2-r1";
/// The frozen DIALECT/2 winner card, embedded at compile time. The
/// on-disk research artifact stays read-only; tests assert the two stay
/// identical.
pub const FROZEN2_CARD_JSON: &str =
    include_str!("../../../../../research/symbolic_comm2/dialect2-card.json");
/// Checksum of the frozen v2 card, pinned by the v2 research harness.
pub const FROZEN2_CARD_CHECKSUM: &str =
    "d69eb25ebf7b4359aacd9994880e5c96fb58f993dd333bcb26a7621ffc65876e";
/// Value-encoding fields a DIALECT/2 card may declare.
const V2_ENCODING_FIELDS: [&str; 5] = ["TS", "EXP", "ED", "ER", "FC"];

/// Hard ceiling on card JSON size.
pub const MAX_CARD_JSON_BYTES: usize = 256 * 1024;
/// Hard ceiling on microtemplate count.
pub const MAX_MICROTEMPLATES: usize = 64;
/// Hard ceiling on any single template string (pattern, emit).
pub const MAX_TEMPLATE_STRING_BYTES: usize = 256;
/// Hard ceiling on the inert `usage_rules` prose.
pub const MAX_USAGE_RULES_BYTES: usize = 4096;
/// Hard ceiling on lineage entries.
pub const MAX_LINEAGE_ENTRIES: usize = 64;
/// Hard ceiling on the evolution generation number.
pub const MAX_GENERATION: u64 = 10_000;

/// Sigils that terminate an evidence locator inside a packed `E` line.
const E_SIGILS: [char; 4] = ['@', '#', '~', '!'];
/// Separator that joins multi-value packed lines (`NEXT`, `DEP`, `ART`).
const PACKING_JOIN: char = '|';
/// Structural packings the research format defines.
const KNOWN_PACKINGS: [&str; 6] = ["E", "FE", "NEXT", "DEP", "ART", "FC"];
/// OCC-1 fixed keys a DIALECT/1 card must alias.
const FIXED_KEYS: [&str; 16] = [
    "MSGID", "TASK", "PARENT", "TRACE", "SENDER", "RECIPIENT", "KIND", "STATUS", "SEQ", "V",
    "VS", "VM", "VR", "NEXT", "TS", "EXP",
];
/// OCC-1 indexed key families a DIALECT/1 card must alias. Order mirrors
/// the research format; the digit check disambiguates `F` vs `FC`/`FE`.
const FAMILY_KEYS: [&str; 10] = ["F", "FC", "FE", "E", "EL", "ED", "ER", "EV", "ART", "DEP"];
/// The ten keys of the checksummed card body.
const BODY_KEYS: [&str; 10] = [
    "format",
    "key_aliases",
    "family_aliases",
    "value_symbols",
    "microtemplates",
    "field_defaults",
    "packings",
    "never_compress_kinds",
    "never_compress_verification_fail",
    "usage_rules",
];
/// Closed value-symbol domains, with their contract enum values.
const STATUS_VALUES: [&str; 7] = [
    "pending",
    "in_progress",
    "done",
    "partial",
    "blocked",
    "failed",
    "cancelled",
];
const KIND_VALUES: [&str; 9] = [
    "TASK",
    "PROGRESS",
    "RESULT",
    "EVIDENCE",
    "REQUEST_DETAIL",
    "ACK",
    "CORRECTION",
    "BLOCKED",
    "CANCELLED",
];
const VERIFY_VALUES: [&str; 3] = ["pass", "fail", "unknown"];
const EVIDENCE_VERIFIED_VALUES: [&str; 2] = ["pass", "fail"];
const EVIDENCE_KIND_VALUES: [&str; 8] = [
    "src", "commit", "log", "build", "tool", "artifact", "lines", "finding",
];

/// A loader failure. `Unsupported` always names the rejected construct.
#[derive(Debug, Error)]
pub enum DialectLoadError {
    /// The card JSON exceeded the size bound.
    #[error("card JSON is {size} bytes, over the {MAX_CARD_JSON_BYTES}-byte bound")]
    TooLarge {
        /// Actual size.
        size: usize,
    },
    /// The card JSON did not parse.
    #[error("card JSON failed to parse: {0}")]
    Json(#[from] serde_json::Error),
    /// The checksum did not match the card content.
    #[error("card checksum {stored:?} does not match the recomputed {computed:?}")]
    ChecksumMismatch {
        /// Checksum carried by the card.
        stored: String,
        /// Recomputed checksum.
        computed: String,
    },
    /// Unsupported constructs, listed explicitly.
    #[error("unsupported DIALECT/1 constructs: {}", constructs.join("; "))]
    Unsupported {
        /// The rejected constructs, each self-describing.
        constructs: Vec<String>,
    },
    /// The card violated a DIALECT/1 invariant.
    #[error("invalid DIALECT/1 card: {0}")]
    Invalid(String),
    /// A resource bound was exceeded.
    #[error("resource bound exceeded: {0}")]
    Bounds(String),
    /// The production translation failed.
    #[error("production translation failed: {0}")]
    Translation(String),
}

/// A DIALECT/1 wire codec failure.
#[derive(Debug, Error)]
pub enum Dialect1Error {
    /// The wire input was empty.
    #[error("empty dialect wire input")]
    Empty,
    /// The first line was not the `D/1` header.
    #[error("first line {0:?} is not the D/1 header")]
    BadHeader(String),
    /// A blank line appeared inside the message.
    #[error("blank line in dialect wire")]
    BlankLine,
    /// A line lacked the `=` operator.
    #[error("wire line {0:?} lacks '='")]
    NoOperator(String),
    /// An alias was not defined by the card.
    #[error("unknown dialect alias {0:?}")]
    UnknownAlias(String),
    /// A value symbol was not defined by the card.
    #[error("unknown {domain} symbol {symbol:?}")]
    UnknownSymbol {
        /// The enum domain.
        domain: String,
        /// The offending wire symbol.
        symbol: String,
    },
    /// A microtemplate produced a locator containing a packing sigil.
    #[error("microtemplate produced a sigil-bearing locator {0:?}")]
    SigilLocator(String),
    /// A microtemplate produced `|` inside a multi-value packing.
    #[error("microtemplate produced '|' inside a multi-value {0:?}")]
    PipeInMultiValue(String),
    /// A packed evidence value repeated a sigil segment.
    #[error("duplicate sigil {0:?} in packed evidence {1:?}")]
    DuplicateSigil(char, String),
    /// A packed line range failed the `start-end` shape.
    #[error("bad packed line range {0:?}")]
    BadLineRange(String),
    /// The wire is not a fixed point of encode.
    #[error("wire is not a fixed point of encode; ambiguous transform (re-encoded as {0:?})")]
    NotFixedPoint(String),
    /// A contract-level failure.
    #[error(transparent)]
    Contract(#[from] ContractError),
    /// A field-level rebuild failure.
    #[error("dialect field problem: {0}")]
    Field(String),
}

// ---------------------------------------------------------------------------
// Card JSON model
// ---------------------------------------------------------------------------

/// Raw serde model of a DIALECT/1 card. `deny_unknown_fields` so unknown
/// content is rejected rather than dropped.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CardJson {
    id: String,
    generation: u64,
    #[serde(default)]
    lineage: Vec<String>,
    checksum: String,
    format: String,
    key_aliases: BTreeMap<String, String>,
    family_aliases: BTreeMap<String, String>,
    value_symbols: BTreeMap<String, BTreeMap<String, String>>,
    #[serde(default)]
    microtemplates: Vec<MicrotemplateJson>,
    #[serde(default)]
    field_defaults: BTreeMap<String, String>,
    #[serde(default)]
    packings: Vec<String>,
    #[serde(default)]
    never_compress_kinds: Vec<String>,
    #[serde(default = "default_true")]
    never_compress_verification_fail: bool,
    #[serde(default)]
    usage_rules: String,
    /// DIALECT/2 extension: 1-char wire header (absent keeps "D/1").
    #[serde(default)]
    wire_header: Option<String>,
    /// DIALECT/2 extension: per-field value recodings
    /// (`TS`/`EXP` -> `epoch36`, `ED`/`ER`/`FC` -> `b36`).
    #[serde(default)]
    value_encodings: BTreeMap<String, String>,
    /// DIALECT/2 extension: pack MSGID/TASK/TRACE/TS onto one line.
    #[serde(default)]
    scalar_pack: bool,
}

fn default_true() -> bool {
    true
}

/// Raw serde model of one microtemplate.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct MicrotemplateJson {
    id: String,
    field: String,
    #[serde(rename = "type")]
    kind: String,
    #[serde(rename = "match")]
    pattern: String,
    emit: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    decode_match: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    decode_emit: Option<String>,
}

/// One compiled value transform.
#[derive(Debug, Clone)]
enum MtOp {
    /// Replace a leading literal with another literal.
    Prefix {
        /// Literal to match.
        pattern: String,
        /// Literal to emit in its place.
        emit: String,
    },
    /// Full-match regex rewrite with `\N` group references.
    Regex {
        /// Anchored encode pattern.
        encode: regex::Regex,
        /// Rust `expand` template translated from `\N` references.
        encode_template: String,
        /// Anchored decode pattern.
        decode: regex::Regex,
        /// Rust `expand` template translated from `\N` references.
        decode_template: String,
    },
}

/// One compiled microtemplate.
#[derive(Debug, Clone)]
struct Microtemplate {
    id: String,
    field: String,
    op: MtOp,
}

// ---------------------------------------------------------------------------
// Loaded card
// ---------------------------------------------------------------------------

/// A validated, compiled DIALECT/1 research card.
#[derive(Debug, Clone)]
pub struct ResearchDialectCard {
    id: String,
    generation: u64,
    lineage: Vec<String>,
    checksum: String,
    key_aliases: BTreeMap<String, String>,
    family_aliases: BTreeMap<String, String>,
    value_symbols: BTreeMap<String, BTreeMap<String, String>>,
    microtemplates: Vec<Microtemplate>,
    field_defaults: BTreeMap<String, String>,
    packings: Vec<String>,
    never_compress_kinds: Vec<String>,
    never_compress_verification_fail: bool,
    /// Inert prose. Checksum-verified as card bytes, never rendered
    /// into any prompt.
    usage_rules: String,
    /// DIALECT/2 extension: effective wire header ("D/1" when unset).
    wire_header: String,
    /// DIALECT/2 extension: per-field value recodings.
    value_encodings: BTreeMap<String, String>,
    /// DIALECT/2 extension: MSGID/TASK/TRACE/TS scalar packing.
    scalar_pack: bool,
}

impl ResearchDialectCard {
    /// The research card id (e.g. `sym-v1-pt-pt`).
    pub fn id(&self) -> &str {
        &self.id
    }

    /// The verified content checksum.
    pub fn checksum(&self) -> &str {
        &self.checksum
    }

    /// The evolution generation.
    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// Lineage entries (inert metadata).
    pub fn lineage(&self) -> &[String] {
        &self.lineage
    }

    /// The inert usage-rules prose. Never rendered into prompts.
    pub fn usage_rules(&self) -> &str {
        &self.usage_rules
    }

    /// The effective wire header (`D/1` unless the card overrides it).
    pub fn wire_header(&self) -> &str {
        &self.wire_header
    }

    /// The per-field value recodings (DIALECT/2 extension).
    pub fn value_encodings(&self) -> &BTreeMap<String, String> {
        &self.value_encodings
    }

    /// Whether MSGID/TASK/TRACE/TS are scalar-packed (DIALECT/2).
    pub fn scalar_pack(&self) -> bool {
        self.scalar_pack
    }

    /// True when any DIALECT/2 wire extension is active.
    pub fn has_v2_features(&self) -> bool {
        self.wire_header != DIALECT1_HEADER
            || !self.value_encodings.is_empty()
            || self.scalar_pack
    }

    /// Enabled packings.
    pub fn packings(&self) -> &[String] {
        &self.packings
    }

    /// True when the message must travel with verbatim values: its kind
    /// is in the never-compress list, or verification failed and the
    /// card gates on that. Symbols and packings still apply either way.
    fn is_never_compress(&self, message: &AgentCommMessage) -> bool {
        self.never_compress_kinds
            .iter()
            .any(|kind| kind == &message.kind)
            || (self.never_compress_verification_fail
                && message
                    .verification
                    .as_ref()
                    .is_some_and(|verification| verification.status == "fail"))
    }

    // -- value transforms -------------------------------------------------

    /// First matching microtemplate rewrite, or the value unchanged.
    fn apply_mts_encode(&self, field: &str, value: &str) -> Result<String, Dialect1Error> {
        for template in &self.microtemplates {
            if template.field != field {
                continue;
            }
            if let Some(rewritten) = mt_encode(&template.op, value) {
                return Ok(rewritten);
            }
        }
        Ok(value.to_string())
    }

    /// First matching microtemplate reverse rewrite, or the value unchanged.
    fn apply_mts_decode(&self, field: &str, value: &str) -> String {
        for template in &self.microtemplates {
            if template.field != field {
                continue;
            }
            if let Some(rewritten) = mt_decode(&template.op, value) {
                return rewritten;
            }
        }
        value.to_string()
    }

    /// Normalizes an indexed OCC-1 key (`ART2`, `E1`, `FC3`) to its
    /// family for microtemplate matching. Mirrors the research helper,
    /// including the family iteration order.
    fn mt_field<'a>(&self, key: &'a str) -> &'a str {
        for family in FAMILY_KEYS {
            if is_indexed_key(key, family) {
                return family;
            }
        }
        key
    }

    /// Maps a contract value to its wire symbol. Unknown values travel
    /// verbatim, mirroring the research encoder.
    fn symbolize(&self, domain: &str, value: &str) -> String {
        self.value_symbols
            .get(domain)
            .and_then(|table| table.get(value))
            .cloned()
            .unwrap_or_else(|| value.to_string())
    }

    /// Maps a wire symbol back to its contract value. Unknown symbols
    /// are errors, never guesses.
    fn unsymbolize(&self, domain: &str, symbol: &str) -> Result<String, Dialect1Error> {
        self.value_symbols
            .get(domain)
            .and_then(|table| {
                table
                    .iter()
                    .find(|(_, wire)| wire.as_str() == symbol)
                    .map(|(value, _)| value.clone())
            })
            .ok_or_else(|| Dialect1Error::UnknownSymbol {
                domain: domain.to_string(),
                symbol: symbol.to_string(),
            })
    }

    /// Wire alias for an OCC-1 key. Packed multi-value lines (`DEP`,
    /// `ART`) reuse the bare family alias.
    fn alias_for(&self, key: &str) -> Result<String, Dialect1Error> {
        if let Some(alias) = self.key_aliases.get(key) {
            return Ok(alias.clone());
        }
        if (key == "DEP" || key == "ART") && self.packings.iter().any(|p| p.as_str() == key) {
            if let Some(alias) = self.family_aliases.get(key) {
                return Ok(alias.clone());
            }
        }
        let mut families: Vec<&String> = self.family_aliases.keys().collect();
        families.sort_by(|a, b| b.len().cmp(&a.len()));
        for family in families {
            if is_indexed_key(key, family) {
                let alias = &self.family_aliases[family];
                return Ok(format!("{alias}{}", &key[family.len()..]));
            }
        }
        Err(Dialect1Error::UnknownAlias(key.to_string()))
    }

    /// OCC-1 key for a wire alias, or `None` when unknown. Packed
    /// multi-value lines resolve to the bare family key.
    fn expand_alias(&self, alias: &str) -> Option<String> {
        if let Some(key) = self
            .key_aliases
            .iter()
            .find(|(_, wire)| wire.as_str() == alias)
            .map(|(key, _)| key.clone())
        {
            return Some(key);
        }
        let mut families: Vec<(&String, &String)> = self.family_aliases.iter().collect();
        families.sort_by(|a, b| b.1.len().cmp(&a.1.len()));
        for (family, family_alias) in families {
            if let Some(rest) = alias.strip_prefix(family_alias.as_str()) {
                if !rest.is_empty() && rest.bytes().all(|byte| byte.is_ascii_digit()) {
                    return Some(format!("{family}{rest}"));
                }
            }
        }
        for (family, family_alias) in &self.family_aliases {
            if alias == family_alias.as_str() && self.packings.iter().any(|p| p == family) {
                return Some(family.clone());
            }
        }
        None
    }

    // -- encode -----------------------------------------------------------

    /// Contract -> DIALECT/1 wire text. Deterministic and strict; the
    /// exact mirror of the research encoder's pipeline: default elision
    /// -> evidence-locator microtemplates -> packing -> enum
    /// symbolization -> key aliasing.
    pub fn encode_dialect1(&self, message: &AgentCommMessage) -> Result<String, Dialect1Error> {
        message.validate()?;
        let never = self.is_never_compress(message);
        let mut fields = occ1_fields(message);
        if !never {
            fields.retain(|(key, value)| self.field_defaults.get(key) != Some(value));
        }

        // Pre-packing microtemplates: evidence locators (pure, before
        // the packed tail is appended) and multi-value parts (before
        // joining).
        let mut prepared: Vec<(String, String)> = Vec::with_capacity(fields.len());
        for (key, value) in fields {
            let mut value = value;
            if is_indexed_key(&key, "E") {
                if let Some((kind_text, locator)) = value.split_once(':') {
                    let mut locator = locator.to_string();
                    if !never {
                        locator = self.apply_mts_encode("E", &locator)?;
                        if locator.chars().any(|c| E_SIGILS.contains(&c)) {
                            return Err(Dialect1Error::SigilLocator(locator));
                        }
                    }
                    value = format!("{kind_text}:{locator}");
                }
            } else if !never
                && (is_indexed_key(&key, "ART")
                    || is_indexed_key(&key, "DEP")
                    || key == "NEXT")
            {
                let family = if is_indexed_key(&key, "ART") {
                    "ART"
                } else if is_indexed_key(&key, "DEP") {
                    "DEP"
                } else {
                    "NEXT"
                };
                value = self.apply_mts_encode(family, &value)?;
                if value.contains(PACKING_JOIN) {
                    return Err(Dialect1Error::PipeInMultiValue(value));
                }
            }
            prepared.push((key, value));
        }
        let prepared = if self.packings.is_empty() {
            prepared
        } else {
            self.pack_fields(prepared)
        };

        let mut lines: Vec<String> = Vec::with_capacity(prepared.len() + 1);
        lines.push(DIALECT1_HEADER.to_string());
        for (key, value) in prepared {
            let mut value = value;
            if is_indexed_key(&key, "E") && value.contains(':') {
                let (kind_text, rest) = value.split_once(':').expect("checked above");
                value = format!("{}:{rest}", self.symbolize("EVIDENCE_KIND", kind_text));
            } else if !never
                && key != "NEXT"
                && key != "DEP"
                && key != "ART"
                && !(is_indexed_key(&key, "FC") && self.packings.iter().any(|p| p.as_str() == "FC"))
            {
                value = self.apply_mts_encode(self.mt_field(&key), &value)?;
            }
            if key == "STATUS" {
                value = self.symbolize("STATUS", &value);
            } else if key == "KIND" {
                value = self.symbolize("KIND", &value);
            } else if key == "VS" {
                value = self.symbolize("VERIFY", &value);
            } else if is_indexed_key(&key, "EV") {
                value = self.symbolize("EVIDENCE_VERIFIED", &value);
            }
            let alias = self.alias_for(&key)?;
            lines.push(format!("{alias}={value}"));
        }
        Ok(lines.join("\n"))
    }

    /// Packs OCC-1 field groups into merged wire lines. Mirrors the
    /// research packer exactly, including its fallbacks for sigil-bearing
    /// parts and its output ordering (E, FE, NEXT, DEP, ART, FC, then
    /// leftovers in first-seen key order).
    fn pack_fields(&self, fields: Vec<(String, String)>) -> Vec<(String, String)> {
        let mut index = FieldIndex::new();
        for (key, value) in fields {
            index.push(&key, value);
        }
        let mut out: Vec<(String, String)> = Vec::new();
        let has = |packing: &str| self.packings.iter().any(|p| p == packing);

        if has("E") {
            for (n, base) in index.family_items("E") {
                let Some((kind_text, locator)) = base.split_once(':') else {
                    out.push((format!("E{n}"), base));
                    continue;
                };
                if locator.chars().any(|c| E_SIGILS.contains(&c)) {
                    out.push((format!("E{n}"), base));
                    continue;
                }
                let mut packed = String::new();
                if let Some(line) = index.first(&format!("EL{n}")) {
                    index.mark(&format!("EL{n}"));
                    packed.push('@');
                    packed.push_str(&line);
                }
                if let Some(digest) = index.first(&format!("ED{n}")) {
                    if digest.chars().any(|c| E_SIGILS.contains(&c)) {
                        // Mirrored research fallback: an unsymbolizable
                        // digest forces the whole record back to
                        // unpacked fields.
                        out.push((format!("E{n}"), base));
                        continue;
                    }
                    index.mark(&format!("ED{n}"));
                    packed.push('#');
                    packed.push_str(&digest);
                }
                if let Some(revision) = index.first(&format!("ER{n}")) {
                    if revision.chars().any(|c| E_SIGILS.contains(&c)) {
                        out.push((format!("E{n}"), base));
                        continue;
                    }
                    index.mark(&format!("ER{n}"));
                    packed.push('~');
                    packed.push_str(&revision);
                }
                if let Some(verified) = index.first(&format!("EV{n}")) {
                    index.mark(&format!("EV{n}"));
                    packed.push('!');
                    packed.push_str(&self.symbolize("EVIDENCE_VERIFIED", &verified));
                }
                out.push((format!("E{n}"), format!("{kind_text}:{locator}{packed}")));
            }
        }
        if has("FE") {
            let mut n = 1;
            while let Some(refs) = index.first(&format!("FE{n}")) {
                index.mark(&format!("FE{n}"));
                let stripped = refs
                    .split(',')
                    .map(|reference| {
                        match reference.strip_prefix('E') {
                            Some(rest)
                                if !rest.is_empty()
                                    && rest.bytes().all(|b| b.is_ascii_digit()) =>
                            {
                                rest.to_string()
                            }
                            _ => reference.to_string(),
                        }
                    })
                    .collect::<Vec<_>>()
                    .join(",");
                out.push((format!("FE{n}"), stripped));
                n += 1;
            }
        }
        if has("NEXT") && index.contains("NEXT") {
            let values = index.get("NEXT").cloned().unwrap_or_default();
            index.mark("NEXT");
            if values.iter().any(|value| value.contains(PACKING_JOIN)) {
                for value in values {
                    out.push(("NEXT".to_string(), value));
                }
            } else {
                out.push((
                    "NEXT".to_string(),
                    values.join(&PACKING_JOIN.to_string()),
                ));
            }
        }
        for family in ["DEP", "ART"] {
            if !has(family) {
                continue;
            }
            let items = index.family_items(family);
            if items.is_empty() {
                continue;
            }
            if items.iter().any(|(_, item)| item.contains(PACKING_JOIN)) {
                for (n, item) in items {
                    out.push((format!("{family}{n}"), item));
                }
            } else {
                let joined = items
                    .into_iter()
                    .map(|(_, item)| item)
                    .collect::<Vec<_>>()
                    .join(&PACKING_JOIN.to_string());
                out.push((family.to_string(), joined));
            }
        }
        if has("FC") {
            let mut n = 1;
            while let Some(value) = index.first(&format!("FC{n}")) {
                index.mark(&format!("FC{n}"));
                let packed = pack_fc(&value);
                out.push((format!("FC{n}"), packed.unwrap_or(value)));
                n += 1;
            }
        }
        for key in index.order() {
            if index.is_marked(key) {
                continue;
            }
            if let Some(values) = index.get(key) {
                for value in values {
                    out.push((key.clone(), value.clone()));
                }
            }
        }
        out
    }

    // -- decode -----------------------------------------------------------

    /// DIALECT/1 wire text -> contract. Strict: the wire must be a fixed
    /// point of [`Self::encode_dialect1`], so any ambiguous transform is
    /// rejected instead of guessed.
    pub fn decode_dialect1(&self, wire: &str) -> Result<AgentCommMessage, Dialect1Error> {
        if wire.is_empty() {
            return Err(Dialect1Error::Empty);
        }
        let text = wire.strip_suffix('\n').unwrap_or(wire);
        let mut lines = text.split('\n');
        let header = lines.next().ok_or(Dialect1Error::Empty)?;
        if header != DIALECT1_HEADER {
            return Err(Dialect1Error::BadHeader(header.to_string()));
        }
        let mut expanded: Vec<(String, String)> = Vec::new();
        for raw in lines {
            let raw = raw.strip_suffix('\r').unwrap_or(raw);
            if raw.is_empty() {
                return Err(Dialect1Error::BlankLine);
            }
            let Some((alias, value)) = raw.split_once('=') else {
                return Err(Dialect1Error::NoOperator(raw.to_string()));
            };
            let Some(key) = self.expand_alias(alias) else {
                return Err(Dialect1Error::UnknownAlias(alias.to_string()));
            };
            expanded.push((key, value.to_string()));
        }
        let message = self.rebuild(expanded)?;
        let re_encoded = self.encode_dialect1(&message)?;
        if re_encoded != text {
            return Err(Dialect1Error::NotFixedPoint(re_encoded));
        }
        Ok(message)
    }

    /// Rebuilds the typed contract from expanded (key, value) pairs.
    fn rebuild(&self, expanded: Vec<(String, String)>) -> Result<AgentCommMessage, Dialect1Error> {
        let mut desym: Vec<(String, String)> = Vec::with_capacity(expanded.len());
        for (key, value) in expanded {
            let value = if key == "STATUS" {
                self.unsymbolize("STATUS", &value)?
            } else if key == "KIND" {
                self.unsymbolize("KIND", &value)?
            } else if key == "VS" {
                self.unsymbolize("VERIFY", &value)?
            } else if is_indexed_key(&key, "EV") {
                self.unsymbolize("EVIDENCE_VERIFIED", &value)?
            } else {
                value
            };
            desym.push((key, value));
        }
        let kind = desym
            .iter()
            .find(|(key, _)| key == "KIND")
            .map(|(_, value)| value.clone());
        let verification_fail = desym
            .iter()
            .any(|(key, value)| key == "VS" && value == "fail");
        let never = kind
            .as_deref()
            .is_some_and(|kind| self.never_compress_kinds.iter().any(|k| k == kind))
            || (self.never_compress_verification_fail && verification_fail);

        let fields = if self.packings.is_empty() {
            desym
        } else {
            self.unpack_fields(desym)?
        };

        let mut rebuilt: Vec<(String, String)> = Vec::with_capacity(fields.len() + 3);
        let mut present: HashSet<String> = HashSet::new();
        for (key, value) in fields {
            present.insert(key.clone());
            let mut value = value;
            if is_indexed_key(&key, "E") && value.contains(':') {
                let (symbol, locator) = value.split_once(':').expect("checked above");
                let kind_text = self.unsymbolize("EVIDENCE_KIND", symbol)?;
                let mut locator = locator.to_string();
                if !never {
                    locator = self.apply_mts_decode("E", &locator);
                }
                value = format!("{kind_text}:{locator}");
            } else if !never {
                value = self.apply_mts_decode(self.mt_field(&key), &value);
            }
            rebuilt.push((key, value));
        }
        if !never {
            for (key, default) in &self.field_defaults {
                if !present.contains(key) {
                    rebuilt.push((key.clone(), default.clone()));
                }
            }
        }
        from_occ1_fields(&rebuilt)
    }

    /// Expands packed wire lines back into OCC-1 field pairs.
    fn unpack_fields(
        &self,
        lines: Vec<(String, String)>,
    ) -> Result<Vec<(String, String)>, Dialect1Error> {
        let mut out: Vec<(String, String)> = Vec::new();
        for (key, value) in lines {
            if is_indexed_key(&key, "E") && self.packings.iter().any(|p| p == "E") {
                let Some((kind_text, rest)) = value.split_once(':') else {
                    out.push((key, value));
                    continue;
                };
                if !rest.chars().any(|c| E_SIGILS.contains(&c)) {
                    out.push((key, value));
                    continue;
                }
                let evidence_number = key[1..].to_string();
                let split = rest
                    .char_indices()
                    .find(|(_, c)| E_SIGILS.contains(c))
                    .map(|(position, _)| position)
                    .unwrap_or(rest.len());
                let (locator, tail) = rest.split_at(split);
                out.push((key, format!("{kind_text}:{locator}")));
                let mut seen: Vec<char> = Vec::new();
                for segment in split_before_sigils(tail) {
                    if segment.is_empty() {
                        continue;
                    }
                    let sigil = segment.chars().next().expect("nonempty segment");
                    if seen.contains(&sigil) {
                        return Err(Dialect1Error::DuplicateSigil(sigil, value.clone()));
                    }
                    seen.push(sigil);
                    match sigil {
                        '@' => {
                            let range = &segment[1..];
                            let valid = range
                                .split_once('-')
                                .is_some_and(|(start, end)| {
                                    !start.is_empty()
                                        && !end.is_empty()
                                        && start.bytes().all(|b| b.is_ascii_digit())
                                        && end.bytes().all(|b| b.is_ascii_digit())
                                });
                            if !valid {
                                return Err(Dialect1Error::BadLineRange(range.to_string()));
                            }
                            out.push((format!("EL{evidence_number}"), range.to_string()));
                        }
                        '#' => out.push((
                            format!("ED{evidence_number}"),
                            segment[1..].to_string(),
                        )),
                        '~' => out.push((
                            format!("ER{evidence_number}"),
                            segment[1..].to_string(),
                        )),
                        '!' => {
                            let flag = self.unsymbolize("EVIDENCE_VERIFIED", &segment[1..])?;
                            out.push((format!("EV{evidence_number}"), flag));
                        }
                        _ => unreachable!("split_before_sigils yields only sigil heads"),
                    }
                }
                continue;
            }
            if is_indexed_key(&key, "FE") && self.packings.iter().any(|p| p == "FE") {
                let refs = value
                    .split(',')
                    .map(|reference| {
                        if !reference.is_empty()
                            && reference.bytes().all(|b| b.is_ascii_digit())
                        {
                            format!("E{reference}")
                        } else {
                            reference.to_string()
                        }
                    })
                    .collect::<Vec<_>>()
                    .join(",");
                out.push((key, refs));
                continue;
            }
            if key == "NEXT" && self.packings.iter().any(|p| p == "NEXT") {
                if value.contains(PACKING_JOIN) {
                    for part in value.split(PACKING_JOIN) {
                        out.push(("NEXT".to_string(), part.to_string()));
                    }
                } else {
                    out.push((key, value));
                }
                continue;
            }
            if (key == "DEP" || key == "ART") && self.packings.iter().any(|p| p.as_str() == key) {
                for (n, part) in value.split(PACKING_JOIN).enumerate() {
                    out.push((format!("{key}{}", n + 1), part.to_string()));
                }
                continue;
            }
            if is_indexed_key(&key, "FC") && self.packings.iter().any(|p| p.as_str() == "FC") {
                let unpacked = unpack_fc(&value);
                out.push((key, unpacked.unwrap_or(value)));
                continue;
            }
            out.push((key, value));
        }
        Ok(out)
    }
}

// ---------------------------------------------------------------------------
// Microtemplate application
// ---------------------------------------------------------------------------

/// Applies one template's encode direction. `None` means no match.
fn mt_encode(op: &MtOp, value: &str) -> Option<String> {
    match op {
        MtOp::Prefix { pattern, emit } => value
            .strip_prefix(pattern.as_str())
            .map(|rest| format!("{emit}{rest}")),
        MtOp::Regex {
            encode,
            encode_template,
            ..
        } => {
            let captures = encode.captures(value)?;
            let full = captures
                .get(0)
                .is_some_and(|whole| whole.as_str() == value);
            if !full {
                return None;
            }
            let mut expanded = String::new();
            captures.expand(encode_template, &mut expanded);
            Some(expanded)
        }
    }
}

/// Applies one template's decode direction. `None` means no match.
fn mt_decode(op: &MtOp, value: &str) -> Option<String> {
    match op {
        MtOp::Prefix { pattern, emit } => value
            .strip_prefix(emit.as_str())
            .map(|rest| format!("{pattern}{rest}")),
        MtOp::Regex {
            decode,
            decode_template,
            ..
        } => {
            let captures = decode.captures(value)?;
            let full = captures
                .get(0)
                .is_some_and(|whole| whole.as_str() == value);
            if !full {
                return None;
            }
            let mut expanded = String::new();
            captures.expand(decode_template, &mut expanded);
            Some(expanded)
        }
    }
}

/// Translates a Python `re.sub` replacement (`\N` group references) into
/// a Rust `regex` expand template (`${N}`, literal `$` escaped). Any
/// other escape is rejected as unsupported.
fn translate_template(emit: &str) -> Result<String, DialectLoadError> {
    let mut out = String::with_capacity(emit.len() + 8);
    let mut chars = emit.chars();
    while let Some(c) = chars.next() {
        match c {
            '\\' => {
                let Some(group) = chars.next() else {
                    return Err(DialectLoadError::Unsupported {
                        constructs: vec![
                            "microtemplate emit ends with a lone backslash".to_string(),
                        ],
                    });
                };
                if !group.is_ascii_digit() {
                    return Err(DialectLoadError::Unsupported {
                        constructs: vec![format!(
                            "microtemplate emit escape \\{group}: only \\N group references are supported"
                        )],
                    });
                }
                out.push_str("${");
                out.push(group);
                out.push('}');
            }
            '$' => out.push_str("$$"),
            _ => out.push(c),
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// Packing helpers
// ---------------------------------------------------------------------------

/// `'0.800' -> '800'`; injective on the 3-decimal grid, computed with
/// exact integer arithmetic.
fn pack_fc(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    if bytes.len() != 5 || bytes[1] != b'.' {
        return None;
    }
    let digits = [bytes[0], bytes[2], bytes[3], bytes[4]];
    if !digits.iter().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let permille = digits
        .iter()
        .fold(0u32, |accumulator, digit| accumulator * 10 + (digit - b'0') as u32);
    Some(permille.to_string())
}

/// `'800' -> '0.800'`; only ASCII digit strings in 0..=1000.
fn unpack_fc(value: &str) -> Option<String> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let permille: i64 = value.parse().ok()?;
    if !(0..=1000).contains(&permille) {
        return None;
    }
    Some(format!("{:.3}", permille as f64 / 1000.0))
}

/// Splits a packed tail before each sigil: `"@1-2#c"` -> `["@1-2", "#c"]`.
/// Empty segments are dropped, mirroring the research splitter.
fn split_before_sigils(tail: &str) -> Vec<&str> {
    let mut segments = Vec::new();
    let mut start = 0;
    for (position, c) in tail.char_indices() {
        if E_SIGILS.contains(&c) && position != start {
            segments.push(&tail[start..position]);
            start = position;
        }
    }
    segments.push(&tail[start..]);
    segments.retain(|segment| !segment.is_empty());
    segments
}

// ---------------------------------------------------------------------------
// OCC-1 field helpers
// ---------------------------------------------------------------------------

/// True when `key` is `<family><digits>` for the given family.
fn is_indexed_key(key: &str, family: &str) -> bool {
    key.len() > family.len()
        && key.as_bytes().starts_with(family.as_bytes())
        && key.as_bytes()[family.len()..]
            .iter()
            .all(|byte| byte.is_ascii_digit())
}

/// The OCC-1 field list of a contract message, in the exact order the
/// contract encoder emits (`AgentCommMessage::try_into_occ1`).
fn occ1_fields(message: &AgentCommMessage) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    fn push(out: &mut Vec<(String, String)>, key: &str, value: String) {
        out.push((key.to_string(), value));
    }
    if let Some(id) = &message.message_id {
        push(&mut out, "MSGID", id.clone());
    }
    push(&mut out, "TASK", message.task_id.clone());
    if let Some(parent) = &message.parent_task_id {
        push(&mut out, "PARENT", parent.clone());
    }
    if let Some(trace) = &message.trace_id {
        push(&mut out, "TRACE", trace.clone());
    }
    if let Some(sender) = &message.sender_identity {
        push(&mut out, "SENDER", sender.clone());
    }
    if let Some(recipient) = &message.recipient_identity {
        push(&mut out, "RECIPIENT", recipient.clone());
    }
    push(&mut out, "KIND", message.kind.clone());
    push(&mut out, "STATUS", message.status.clone());
    if let Some(sequence) = message.sequence {
        push(&mut out, "SEQ", sequence.to_string());
    }
    for (index, finding) in message.findings.iter().enumerate() {
        let n = index + 1;
        push(&mut out, &format!("F{n}"), finding.statement.clone());
        if let Some(confidence) = finding.confidence {
            push(&mut out, &format!("FC{n}"), format!("{confidence:.3}"));
        }
        if !finding.evidence.is_empty() {
            push(&mut out, &format!("FE{n}"), finding.evidence.join(","));
        }
    }
    for (index, evidence) in message.evidence.iter().enumerate() {
        let n = index + 1;
        push(
            &mut out,
            &format!("E{n}"),
            format!("{}:{}", evidence.kind, evidence.locator),
        );
        if let Some((start, end)) = evidence.line_range {
            push(&mut out, &format!("EL{n}"), format!("{start}-{end}"));
        }
        if let Some(digest) = &evidence.digest {
            push(&mut out, &format!("ED{n}"), digest.clone());
        }
        if let Some(revision) = &evidence.revision {
            push(&mut out, &format!("ER{n}"), revision.clone());
        }
        if let Some(verified) = evidence.verified {
            push(
                &mut out,
                &format!("EV{n}"),
                if verified { "pass" } else { "fail" }.to_string(),
            );
        }
    }
    for (index, artifact) in message.artifacts.iter().enumerate() {
        push(&mut out, &format!("ART{}", index + 1), artifact.clone());
    }
    if let Some(verification) = &message.verification {
        push(&mut out, "V", verification.claim.clone());
        push(&mut out, "VS", verification.status.clone());
        push(&mut out, "VM", verification.method.clone());
        if let Some(receipt) = &verification.receipt {
            push(&mut out, "VR", receipt.clone());
        }
    }
    for (index, dependency) in message.dependencies.iter().enumerate() {
        push(&mut out, &format!("DEP{}", index + 1), dependency.clone());
    }
    for action in &message.next_actions {
        push(&mut out, "NEXT", action.clone());
    }
    if let Some(timestamp) = &message.timestamp {
        push(&mut out, "TS", timestamp.clone());
    }
    if let Some(expiry) = &message.expiry {
        push(&mut out, "EXP", expiry.clone());
    }
    out
}

/// Builds the typed contract from OCC-1 (key, value) pairs. Strict:
/// duplicates of scalar keys and non-contiguous indexed families are
/// errors; nothing is invented.
fn from_occ1_fields(fields: &[(String, String)]) -> Result<AgentCommMessage, Dialect1Error> {
    let mut scalars: HashMap<&str, &str> = HashMap::new();
    let mut repeated: HashMap<&str, Vec<&str>> = HashMap::new();
    for (key, value) in fields {
        if key == "NEXT"
            || is_indexed_key(key, "F")
            || is_indexed_key(key, "FC")
            || is_indexed_key(key, "FE")
            || is_indexed_key(key, "E")
            || is_indexed_key(key, "EL")
            || is_indexed_key(key, "ED")
            || is_indexed_key(key, "ER")
            || is_indexed_key(key, "EV")
            || is_indexed_key(key, "ART")
            || is_indexed_key(key, "DEP")
        {
            repeated.entry(key.as_str()).or_default().push(value);
            continue;
        }
        if scalars.insert(key.as_str(), value).is_some() {
            return Err(Dialect1Error::Field(format!("duplicate field {key:?}")));
        }
    }
    let required = |key: &str| -> Result<&str, Dialect1Error> {
        scalars
            .get(key)
            .copied()
            .ok_or_else(|| Dialect1Error::Field(format!("missing required field {key:?}")))
    };
    let task_id = required("TASK")?.to_string();
    let kind = required("KIND")?.to_string();
    let status = required("STATUS")?.to_string();
    let mut out = AgentCommMessage::new(kind, task_id, status);
    out.message_id = scalars.get("MSGID").copied().map(str::to_string);
    out.parent_task_id = scalars.get("PARENT").copied().map(str::to_string);
    out.trace_id = scalars.get("TRACE").copied().map(str::to_string);
    out.sender_identity = scalars.get("SENDER").copied().map(str::to_string);
    out.recipient_identity = scalars.get("RECIPIENT").copied().map(str::to_string);
    if let Some(sequence) = scalars.get("SEQ") {
        out.sequence = Some(
            sequence
                .parse::<u64>()
                .map_err(|_| Dialect1Error::Field(format!("SEQ {sequence:?} is not a u64")))?,
        );
    }

    let evidence_count = contiguous_count(&repeated, "E")?;
    for n in 1..=contiguous_count(&repeated, "F")? {
        let finding_key = format!("F{n}");
        let statement = repeated
            .get(finding_key.as_str())
            .and_then(|values| values.first())
            .ok_or_else(|| Dialect1Error::Field(format!("finding F{n} statement missing")))?;
        let confidence_key = format!("FC{n}");
        let confidence = repeated
            .get(confidence_key.as_str())
            .and_then(|values| values.first())
            .map(|value| {
                value
                    .parse::<f32>()
                    .map_err(|_| Dialect1Error::Field(format!("FC{n} {value:?} is not a confidence")))
            })
            .transpose()?;
        let evidence_key = format!("FE{n}");
        let evidence = repeated
            .get(evidence_key.as_str())
            .and_then(|values| values.first())
            .map(|refs| refs.split(',').map(str::to_string).collect())
            .unwrap_or_default();
        out.findings.push(Finding {
            id: format!("F{n}"),
            statement: statement.to_string(),
            confidence,
            evidence,
        });
    }
    for n in 1..=evidence_count {
        let evidence_key = format!("E{n}");
        let encoded = repeated
            .get(evidence_key.as_str())
            .and_then(|values| values.first())
            .ok_or_else(|| Dialect1Error::Field(format!("evidence E{n} missing")))?;
        let Some((kind, locator)) = encoded.split_once(':') else {
            return Err(Dialect1Error::Field(format!(
                "evidence E{n} {encoded:?} must be <kind>:<locator>"
            )));
        };
        let line_range_key = format!("EL{n}");
        let line_range = repeated
            .get(line_range_key.as_str())
            .and_then(|values| values.first())
            .map(|range| parse_line_range(range))
            .transpose()?;
        let digest_key = format!("ED{n}");
        let digest = repeated
            .get(digest_key.as_str())
            .and_then(|values| values.first())
            .copied();
        let revision_key = format!("ER{n}");
        let revision = repeated
            .get(revision_key.as_str())
            .and_then(|values| values.first())
            .copied();
        let verified_key = format!("EV{n}");
        let verified = repeated
            .get(verified_key.as_str())
            .and_then(|values| values.first())
            .map(|flag| match *flag {
                "pass" => Ok(true),
                "fail" => Ok(false),
                other => Err(Dialect1Error::Field(format!(
                    "EV{n} {other:?} must be pass or fail"
                ))),
            })
            .transpose()?;
        out.evidence.push(EvidenceRef {
            id: format!("E{n}"),
            kind: kind.to_string(),
            locator: locator.to_string(),
            line_range,
            digest: digest.map(str::to_string),
            revision: revision.map(str::to_string),
            verified,
        });
    }
    for n in 1..=contiguous_count(&repeated, "ART")? {
        let artifact_key = format!("ART{n}");
        if let Some(value) = repeated
            .get(artifact_key.as_str())
            .and_then(|values| values.first())
        {
            out.artifacts.push(value.to_string());
        }
    }
    let has_verification = ["V", "VS", "VM", "VR"]
        .iter()
        .any(|key| scalars.contains_key(key));
    if has_verification {
        let claim = scalars
            .get("V")
            .ok_or_else(|| {
                Dialect1Error::Field(
                    "verification status, method or receipt requires the claim".into(),
                )
            })?
            .to_string();
        let verification_status = scalars
            .get("VS")
            .ok_or_else(|| {
                Dialect1Error::Field("a verification claim requires its status".into())
            })?
            .to_string();
        let method = scalars
            .get("VM")
            .ok_or_else(|| {
                Dialect1Error::Field("a verification claim requires its method".into())
            })?
            .to_string();
        out.verification = Some(Verification {
            claim,
            status: verification_status,
            method,
            receipt: scalars.get("VR").copied().map(str::to_string),
        });
    }
    for n in 1..=contiguous_count(&repeated, "DEP")? {
        let dependency_key = format!("DEP{n}");
        if let Some(value) = repeated
            .get(dependency_key.as_str())
            .and_then(|values| values.first())
        {
            out.dependencies.push(value.to_string());
        }
    }
    if let Some(actions) = repeated.get("NEXT") {
        out.next_actions = actions.iter().map(|value| value.to_string()).collect();
    }
    out.timestamp = scalars.get("TS").copied().map(str::to_string);
    out.expiry = scalars.get("EXP").copied().map(str::to_string);
    out.protocol = AGENT_COMM_PROTOCOL.to_string();
    out.validate()?;
    Ok(out)
}

/// Count of an indexed family contiguous from 1. A gap (an index past
/// the first missing one) is corruption, not truncation.
fn contiguous_count(
    repeated: &HashMap<&str, Vec<&str>>,
    family: &str,
) -> Result<usize, Dialect1Error> {
    let mut count = 0usize;
    let mut key = format!("{family}{}", count + 1);
    while repeated.contains_key(key.as_str()) {
        count += 1;
        key = format!("{family}{}", count + 1);
    }
    for key in repeated.keys() {
        if let Some(rest) = key.strip_prefix(family) {
            if let Ok(index) = rest.parse::<usize>() {
                if index > count {
                    return Err(Dialect1Error::Field(format!(
                        "indexed family {family} has a gap before index {index}"
                    )));
                }
            }
        }
    }
    Ok(count)
}

/// Parses a `start-end` line range.
fn parse_line_range(range: &str) -> Result<(u32, u32), Dialect1Error> {
    let Some((start, end)) = range.split_once('-') else {
        return Err(Dialect1Error::Field(format!(
            "line range {range:?} lacks '-'"
        )));
    };
    let start: u32 = start
        .parse()
        .map_err(|_| Dialect1Error::Field(format!("line range start {start:?} is not a u32")))?;
    let end: u32 = end
        .parse()
        .map_err(|_| Dialect1Error::Field(format!("line range end {end:?} is not a u32")))?;
    Ok((start, end))
}

/// Insertion-ordered multimap mirroring the research packer's dict
/// semantics (first-seen key order, per-key value order).
struct FieldIndex {
    order: Vec<String>,
    map: HashMap<String, Vec<String>>,
    used: HashSet<String>,
}

impl FieldIndex {
    fn new() -> Self {
        Self {
            order: Vec::new(),
            map: HashMap::new(),
            used: HashSet::new(),
        }
    }

    fn push(&mut self, key: &str, value: String) {
        if !self.map.contains_key(key) {
            self.order.push(key.to_string());
        }
        self.map.entry(key.to_string()).or_default().push(value);
    }

    fn get(&self, key: &str) -> Option<&Vec<String>> {
        self.map.get(key)
    }

    /// First value of a key, owned, so callers can interleave reads with
    /// `mark` without borrow conflicts.
    fn first(&self, key: &str) -> Option<String> {
        self.map
            .get(key)
            .and_then(|values| values.first())
            .cloned()
    }

    fn contains(&self, key: &str) -> bool {
        self.map.contains_key(key)
    }

    fn mark(&mut self, key: &str) {
        self.used.insert(key.to_string());
    }

    fn is_marked(&self, key: &str) -> bool {
        self.used.contains(key)
    }

    fn order(&self) -> impl Iterator<Item = &String> {
        self.order.iter()
    }

    /// Contiguous `E1..En` (or `DEP1..`, `ART1..`) items from 1, marking
    /// each consumed key. Returns `(index, value)` pairs.
    fn family_items(&mut self, family: &str) -> Vec<(usize, String)> {
        let mut items = Vec::new();
        let mut n = 1;
        while let Some(value) = self.first(&format!("{family}{n}")) {
            self.mark(&format!("{family}{n}"));
            items.push((n, value));
            n += 1;
        }
        items
    }
}

// ---------------------------------------------------------------------------
// Loading and validation
// ---------------------------------------------------------------------------

/// Loads and validates a DIALECT/1 card from JSON text. Pure: no
/// filesystem, no network, no code execution from card content.
pub fn load_research_card(json: &str) -> Result<ResearchDialectCard, DialectLoadError> {
    if json.len() > MAX_CARD_JSON_BYTES {
        return Err(DialectLoadError::TooLarge { size: json.len() });
    }
    let value: serde_json::Value = serde_json::from_str(json)?;
    let Some(map) = value.as_object() else {
        return Err(DialectLoadError::Invalid("card is not a JSON object".into()));
    };
    let known_keys: Vec<&str> = [
        "id",
        "generation",
        "lineage",
        "checksum",
        "wire_header",
        "value_encodings",
        "scalar_pack",
    ]
    .into_iter()
    .chain(BODY_KEYS)
    .collect();
    let unknown: Vec<String> = map
        .keys()
        .filter(|key| !known_keys.contains(&key.as_str()))
        .map(|key| format!("top-level key {key:?}"))
        .collect();
    if !unknown.is_empty() {
        return Err(DialectLoadError::Unsupported { constructs: unknown });
    }
    for key in ["id", "generation", "checksum", "format"] {
        if !map.contains_key(key) {
            return Err(DialectLoadError::Invalid(format!(
                "missing required top-level key {key:?}"
            )));
        }
    }
    let format = map["format"].as_str().unwrap_or_default();
    if format != DIALECT1_FORMAT {
        return Err(DialectLoadError::Unsupported {
            constructs: vec![format!(
                "card format {format:?}: only {DIALECT1_FORMAT:?} is supported"
            )],
        });
    }

    // Typed parse (applies the same defaults the research loader does).
    let card: CardJson = serde_json::from_value(value)?;

    // Content checksum: canonical JSON body, byte-identical to the
    // research harness's computation.
    let stored = card.checksum.clone();
    if stored.len() != 64
        || !stored
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err(DialectLoadError::Invalid(format!(
            "checksum {stored:?} is not 64 lowercase hex characters"
        )));
    }
    let computed = canonical_body_checksum(&card)?;
    if computed != stored {
        return Err(DialectLoadError::ChecksumMismatch { stored, computed });
    }

    validate_card(&card)?;
    compile_card(card)
}

/// sha256 over the canonical serialization of the card body (the ten
/// body keys, recursively key-sorted, compact separators).
fn canonical_body_checksum(card: &CardJson) -> Result<String, DialectLoadError> {
    let body = serde_json::to_value(card)?;
    let mut selected = serde_json::Map::new();
    for key in BODY_KEYS {
        let value = body.get(key).cloned().unwrap_or(serde_json::Value::Null);
        selected.insert(key.to_string(), value);
    }
    // DIALECT/2 extensions enter the checksummed body exactly when the
    // research `Dialect2Card.body()` emits them: `wire_header` only when
    // it differs from the v1 header, `value_encodings` only when
    // non-empty, `scalar_pack` only when true. A feature-less v2 card
    // therefore keeps the v1 checksum byte-for-byte.
    if let Some(header) = card.wire_header.as_deref() {
        if header != DIALECT1_HEADER {
            selected.insert(
                "wire_header".to_string(),
                serde_json::Value::String(header.to_string()),
            );
        }
    }
    if !card.value_encodings.is_empty() {
        selected.insert(
            "value_encodings".to_string(),
            serde_json::to_value(&card.value_encodings)?,
        );
    }
    if card.scalar_pack {
        selected.insert("scalar_pack".to_string(), serde_json::Value::Bool(true));
    }
    let canonical = canonical_json(&serde_json::Value::Object(selected));
    Ok(hex_sha256(&canonical))
}

/// Serializes a JSON value with every object's keys sorted, matching
/// Python `json.dumps(body, sort_keys=True, separators=(",", ":"))`.
/// Strings use Python's `ensure_ascii=True` escaping: DIALECT/2 cards
/// carry non-ASCII microtemplate emits, and the canonical form escapes
/// them as `\uXXXX` (serde_json would emit raw UTF-8 and diverge).
fn canonical_json(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = String::from("{");
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                out.push_str(&python_json_string(key));
                out.push(':');
                out.push_str(&canonical_json(&map[key.as_str()]));
            }
            out.push('}');
            out
        }
        serde_json::Value::Array(items) => {
            let mut out = String::from("[");
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                out.push_str(&canonical_json(item));
            }
            out.push(']');
            out
        }
        serde_json::Value::String(text) => python_json_string(text),
        other => serde_json::to_string(other).expect("scalar serializes"),
    }
}

/// One JSON string, escaped exactly like Python's `json.dumps` with
/// `ensure_ascii=True`.
fn python_json_string(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            _ => {
                let code = ch as u32;
                if (0x20..0x7f).contains(&code) {
                    out.push(ch);
                } else if code <= 0xFFFF {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    let value = code - 0x10000;
                    let high = 0xD800 + (value >> 10);
                    let low = 0xDC00 + (value & 0x3FF);
                    out.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

fn hex_sha256(text: &str) -> String {
    let digest = Sha256::digest(text.as_bytes());
    let mut out = String::with_capacity(digest.len() * 2);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// Semantic validation of the parsed card: grammar, enum totality,
/// template sanity, bounds. Anything outside the supported envelope is
/// rejected with the construct named.
fn validate_card(card: &CardJson) -> Result<(), DialectLoadError> {
    let mut unsupported: Vec<String> = Vec::new();

    for packing in &card.packings {
        if !KNOWN_PACKINGS.contains(&packing.as_str()) {
            unsupported.push(format!(
                "packing {packing:?}: known packings are E, FE, NEXT, DEP, ART, FC"
            ));
        }
    }
    if card.packings.len() != card.packings.iter().collect::<HashSet<_>>().len() {
        return Err(DialectLoadError::Invalid("duplicate packing entries".into()));
    }

    let mut seen_aliases: HashMap<&str, &str> = HashMap::new();
    for (key, alias) in &card.key_aliases {
        if !FIXED_KEYS.contains(&key.as_str()) {
            unsupported.push(format!(
                "key alias target {key:?}: not one of the OCC-1 fixed keys"
            ));
            continue;
        }
        if !valid_alias(alias) {
            return Err(DialectLoadError::Invalid(format!(
                "alias {alias:?} for {key} violates [a-z][a-z0-9]? grammar"
            )));
        }
        if let Some(other) = seen_aliases.insert(alias, key) {
            return Err(DialectLoadError::Invalid(format!(
                "alias {alias:?} used by both {other} and {key}"
            )));
        }
    }
    for (family, alias) in &card.family_aliases {
        if !FAMILY_KEYS.contains(&family.as_str()) {
            unsupported.push(format!(
                "family alias target {family:?}: not one of the OCC-1 indexed families"
            ));
            continue;
        }
        if !valid_alias(alias) {
            return Err(DialectLoadError::Invalid(format!(
                "family alias {alias:?} for {family} violates [a-z][a-z0-9]? grammar"
            )));
        }
        if let Some(other) = seen_aliases.insert(alias, family) {
            return Err(DialectLoadError::Invalid(format!(
                "family alias {alias:?} collides with the fixed alias of {other}"
            )));
        }
    }
    for (family, family_alias) in &card.family_aliases {
        for alias in card.key_aliases.values() {
            if let Some(rest) = alias.strip_prefix(family_alias.as_str()) {
                if !rest.is_empty() && rest.bytes().all(|byte| byte.is_ascii_digit()) {
                    return Err(DialectLoadError::Invalid(format!(
                        "fixed alias {alias:?} shadows family alias {family_alias:?} + index"
                    )));
                }
            }
        }
    }

    for (domain, table) in &card.value_symbols {
        let Some(values) = domain_values(domain) else {
            unsupported.push(format!(
                "value-symbol domain {domain:?}: known domains are STATUS, KIND, VERIFY, EVIDENCE_VERIFIED, EVIDENCE_KIND"
            ));
            continue;
        };
        let missing: Vec<&str> = values
            .iter()
            .copied()
            .filter(|value| !table.contains_key(*value))
            .collect();
        if !missing.is_empty() {
            return Err(DialectLoadError::Invalid(format!(
                "value symbols for {domain} not total; missing {missing:?}"
            )));
        }
        let mut symbols: Vec<&String> = table.values().collect();
        symbols.sort();
        symbols.dedup();
        if symbols.len() != table.len() {
            return Err(DialectLoadError::Invalid(format!(
                "duplicate symbols in domain {domain}"
            )));
        }
        for (value, symbol) in table {
            if values.contains(&value.as_str()) && symbol == value {
                return Err(DialectLoadError::Invalid(format!(
                    "symbol {symbol:?} equals the raw value in {domain}"
                )));
            }
            if !valid_value_symbol(symbol) {
                return Err(DialectLoadError::Invalid(format!(
                    "symbol {symbol:?} for {domain}/{value} violates grammar"
                )));
            }
        }
    }

    for kind in &card.never_compress_kinds {
        if !is_known_kind(kind) {
            return Err(DialectLoadError::Invalid(format!(
                "never-compress kind {kind:?} is not a known kind"
            )));
        }
    }
    for key in card.field_defaults.keys() {
        if !FIXED_KEYS.contains(&key.as_str()) {
            return Err(DialectLoadError::Invalid(format!(
                "field default on non-fixed key {key:?}"
            )));
        }
    }
    if let Some(sequence) = card.field_defaults.get("SEQ") {
        if sequence.parse::<u64>().is_err() {
            return Err(DialectLoadError::Invalid(format!(
                "SEQ default {sequence:?} is not a u64"
            )));
        }
    }

    if card.microtemplates.len() > MAX_MICROTEMPLATES {
        return Err(DialectLoadError::Bounds(format!(
            "{} microtemplates over the {MAX_MICROTEMPLATES} bound",
            card.microtemplates.len()
        )));
    }
    let mut ids: HashSet<&str> = HashSet::new();
    let mt_fields: Vec<&str> = FIXED_KEYS
        .iter()
        .chain(FAMILY_KEYS.iter())
        .copied()
        .collect();
    for template in &card.microtemplates {
        if !ids.insert(template.id.as_str()) {
            return Err(DialectLoadError::Invalid(format!(
                "duplicate microtemplate id {:?}",
                template.id
            )));
        }
        if template.field == "*" {
            unsupported.push(format!(
                "microtemplate {:?} targets field \"*\": wildcard fields are not supported",
                template.id
            ));
            continue;
        }
        if !mt_fields.contains(&template.field.as_str()) {
            unsupported.push(format!(
                "microtemplate {:?} targets unknown field {:?}",
                template.id, template.field
            ));
            continue;
        }
        for text in [
            template.pattern.as_str(),
            template.emit.as_str(),
            template.decode_match.as_deref().unwrap_or(""),
            template.decode_emit.as_deref().unwrap_or(""),
        ] {
            if text.len() > MAX_TEMPLATE_STRING_BYTES {
                return Err(DialectLoadError::Bounds(format!(
                    "microtemplate {:?} string over the {MAX_TEMPLATE_STRING_BYTES}-byte bound",
                    template.id
                )));
            }
        }
        match template.kind.as_str() {
            "prefix" => {}
            "regex" => {
                let Some(decode_match) = template.decode_match.as_deref() else {
                    return Err(DialectLoadError::Invalid(format!(
                        "regex microtemplate {:?} needs decode_match",
                        template.id
                    )));
                };
                let Some(_decode_emit) = template.decode_emit.as_deref() else {
                    return Err(DialectLoadError::Invalid(format!(
                        "regex microtemplate {:?} needs decode_emit",
                        template.id
                    )));
                };
                for pattern in [template.pattern.as_str(), decode_match] {
                    if let Err(error) = regex::Regex::new(pattern) {
                        return Err(DialectLoadError::Invalid(format!(
                            "microtemplate {:?} regex invalid: {error}",
                            template.id
                        )));
                    }
                }
                translate_template(&template.emit)?;
                translate_template(template.decode_emit.as_deref().unwrap_or_default())?;
            }
            other => unsupported.push(format!(
                "microtemplate {:?} type {other:?}: known types are prefix, regex",
                template.id
            )),
        }
    }

    // -- DIALECT/2 extensions -------------------------------------------
    if let Some(header) = card.wire_header.as_deref() {
        if header.is_empty() || header.contains('\n') || header.contains('=') {
            return Err(DialectLoadError::Invalid(format!(
                "wire_header {header:?} must be non-empty without newline or '='"
            )));
        }
        if card.key_aliases.values().any(|alias| alias == header) {
            return Err(DialectLoadError::Invalid(format!(
                "wire_header {header:?} collides with a key alias"
            )));
        }
    }
    for (field, encoding) in &card.value_encodings {
        if !V2_ENCODING_FIELDS.contains(&field.as_str()) {
            unsupported.push(format!("value encoding field {field:?}"));
            continue;
        }
        let expected = match field.as_str() {
            "TS" | "EXP" => "epoch36",
            _ => "b36",
        };
        if encoding != expected {
            return Err(DialectLoadError::Invalid(format!(
                "value encoding {encoding:?} for {field} is not {expected:?}"
            )));
        }
    }
    if card.scalar_pack {
        for key in ["MSGID", "TASK", "TRACE", "TS"] {
            if !card.key_aliases.contains_key(key) {
                return Err(DialectLoadError::Invalid(format!(
                    "scalar_pack needs a key alias for {key}"
                )));
            }
        }
    }
    // Prefix microtemplates decode by emit-prefix; two templates of the
    // same family sharing an emit (or with one emit a prefix of another)
    // can silently misroute a decode for values outside the observed
    // corpus. Forbid both cases outright.
    {
        let mut by_family: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
        for template in &card.microtemplates {
            if template.kind == "prefix" {
                by_family
                    .entry(template.field.as_str())
                    .or_default()
                    .push(template.emit.as_str());
            }
        }
        for (family, emits) in by_family {
            let unique: HashSet<&&str> = emits.iter().collect();
            if unique.len() != emits.len() {
                return Err(DialectLoadError::Invalid(format!(
                    "family {family:?} has two prefix templates with the same emit"
                )));
            }
            for a in &emits {
                for b in &emits {
                    if a != b && b.starts_with(a) {
                        return Err(DialectLoadError::Invalid(format!(
                            "family {family:?} emit {a:?} is a prefix of {b:?}"
                        )));
                    }
                }
            }
        }
    }

    if !unsupported.is_empty() {
        return Err(DialectLoadError::Unsupported { constructs: unsupported });
    }
    if card.usage_rules.len() > MAX_USAGE_RULES_BYTES {
        return Err(DialectLoadError::Bounds(format!(
            "usage_rules is {} bytes over the {MAX_USAGE_RULES_BYTES}-byte bound",
            card.usage_rules.len()
        )));
    }
    if card.lineage.len() > MAX_LINEAGE_ENTRIES {
        return Err(DialectLoadError::Bounds(format!(
            "{} lineage entries over the {MAX_LINEAGE_ENTRIES} bound",
            card.lineage.len()
        )));
    }
    if card.generation > MAX_GENERATION {
        return Err(DialectLoadError::Bounds(format!(
            "generation {} over the {MAX_GENERATION} bound",
            card.generation
        )));
    }
    Ok(())
}

fn domain_values(domain: &str) -> Option<&'static [&'static str]> {
    match domain {
        "STATUS" => Some(&STATUS_VALUES),
        "KIND" => Some(&KIND_VALUES),
        "VERIFY" => Some(&VERIFY_VALUES),
        "EVIDENCE_VERIFIED" => Some(&EVIDENCE_VERIFIED_VALUES),
        "EVIDENCE_KIND" => Some(&EVIDENCE_KIND_VALUES),
        _ => None,
    }
}

fn valid_alias(alias: &str) -> bool {
    let bytes = alias.as_bytes();
    (1..=2).contains(&bytes.len())
        && bytes[0].is_ascii_lowercase()
        && bytes.iter().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
}

fn valid_value_symbol(symbol: &str) -> bool {
    let count = symbol.chars().count();
    (1..=2).contains(&count)
        && symbol
            .chars()
            .all(|c| !c.is_whitespace() && c != ':' && c != '=' && c != '\n')
}

/// Compiles the validated raw card into the runtime representation.
fn compile_card(card: CardJson) -> Result<ResearchDialectCard, DialectLoadError> {
    let mut microtemplates = Vec::with_capacity(card.microtemplates.len());
    for template in card.microtemplates {
        let op = match template.kind.as_str() {
            "prefix" => MtOp::Prefix {
                pattern: template.pattern.clone(),
                emit: template.emit.clone(),
            },
            "regex" => MtOp::Regex {
                encode: anchored(&template.pattern)?,
                encode_template: translate_template(&template.emit)?,
                decode: anchored(template.decode_match.as_deref().unwrap_or_default())?,
                decode_template: translate_template(
                    template.decode_emit.as_deref().unwrap_or_default(),
                )?,
            },
            other => {
                return Err(DialectLoadError::Unsupported {
                    constructs: vec![format!("microtemplate {:?} type {other:?}", template.id)],
                });
            }
        };
        microtemplates.push(Microtemplate {
            id: template.id,
            field: template.field,
            op,
        });
    }
    Ok(ResearchDialectCard {
        id: card.id,
        generation: card.generation,
        lineage: card.lineage,
        checksum: card.checksum,
        key_aliases: card.key_aliases,
        family_aliases: card.family_aliases,
        value_symbols: card.value_symbols,
        microtemplates,
        field_defaults: card.field_defaults,
        packings: card.packings,
        never_compress_kinds: card.never_compress_kinds,
        never_compress_verification_fail: card.never_compress_verification_fail,
        usage_rules: card.usage_rules,
        wire_header: card
            .wire_header
            .unwrap_or_else(|| DIALECT1_HEADER.to_string()),
        value_encodings: card.value_encodings,
        scalar_pack: card.scalar_pack,
    })
}

/// Compiles a pattern anchored to the whole string, the Rust equivalent
/// of Python's `re.fullmatch` gate.
fn anchored(pattern: &str) -> Result<regex::Regex, DialectLoadError> {
    regex::Regex::new(&format!("^(?:{pattern})$"))
        .map_err(|error| DialectLoadError::Invalid(format!("regex anchor failed: {error}")))
}

// ---------------------------------------------------------------------------
// DIALECT/2 wire extensions
// ---------------------------------------------------------------------------

/// The fixed scalar-packing field order (DIALECT/2 `scalar_pack`).
const SCALAR_PACK_FIELDS: [&str; 4] = ["MSGID", "TASK", "TRACE", "TS"];
/// Wire alias of the scalar-packed line. `$` can never collide with a
/// card alias (aliases are `[a-z][a-z0-9]?`).
const SCALAR_PACK_ALIAS: &str = "$";

impl ResearchDialectCard {
    /// The wire alias of `field` when the card recodes it with `epoch36`.
    fn epoch_alias(&self, field: &str) -> Option<String> {
        if self.value_encodings.get(field).map(String::as_str) == Some("epoch36") {
            self.key_aliases.get(field).cloned()
        } else {
            None
        }
    }

    fn fc_alias(&self) -> Option<String> {
        if self.value_encodings.get("FC").map(String::as_str) == Some("b36") {
            self.family_aliases.get("FC").cloned()
        } else {
            None
        }
    }

    fn ed_b36(&self) -> bool {
        self.value_encodings.get("ED").map(String::as_str) == Some("b36")
    }

    fn er_b36(&self) -> bool {
        self.value_encodings.get("ER").map(String::as_str) == Some("b36")
    }

    /// Encodes with the DIALECT/2 wire extensions: message-level digest
    /// and revision recoding, header swap, timestamp/epoch and confidence
    /// line recoding, and scalar packing. A card without v2 features
    /// produces output identical to [`Self::encode_dialect1`].
    pub fn encode_dialect2(&self, message: &AgentCommMessage) -> Result<String, Dialect1Error> {
        let never = self.is_never_compress(message);
        let payload = if !never && (self.ed_b36() || self.er_b36()) {
            self.encode_evidence_encodings(message)
        } else {
            message.clone()
        };
        let wire = self.encode_dialect1(&payload)?;
        let mut lines: Vec<String> = wire.split('\n').map(str::to_string).collect();
        if lines.is_empty() {
            return Err(Dialect1Error::Empty);
        }
        lines[0] = self.wire_header.clone();
        for line in lines.iter_mut().skip(1) {
            *line = self.encode_line_v2(line, never);
        }
        if self.scalar_pack {
            lines = self.scalar_pack_lines(lines);
        }
        Ok(lines.join("\n"))
    }

    /// Strict-decodes a DIALECT/2 wire: header check, scalar unpack, line
    /// recoding inverse, the v1 rebuild, evidence recoding inverse, and
    /// the full fixed-point proof.
    pub fn decode_dialect2(&self, wire: &str) -> Result<AgentCommMessage, Dialect1Error> {
        if wire.is_empty() {
            return Err(Dialect1Error::Empty);
        }
        let text = wire.strip_suffix('\n').unwrap_or(wire);
        let mut lines = text.split('\n');
        let header = lines.next().ok_or(Dialect1Error::Empty)?;
        if header != self.wire_header {
            return Err(Dialect1Error::BadHeader(header.to_string()));
        }
        let mut restored: Vec<String> = vec![DIALECT1_HEADER.to_string()];
        for raw in lines {
            let raw = raw.strip_suffix('\r').unwrap_or(raw);
            if raw.is_empty() {
                return Err(Dialect1Error::BlankLine);
            }
            if self.scalar_pack && raw.starts_with(SCALAR_PACK_ALIAS) && raw.contains('=') {
                for part in self.scalar_unpack_line(raw)? {
                    restored.push(self.decode_line_v2(&part));
                }
                continue;
            }
            restored.push(self.decode_line_v2(raw));
        }
        let mut expanded: Vec<(String, String)> = Vec::new();
        for raw in restored.iter().skip(1) {
            let Some((alias, value)) = raw.split_once('=') else {
                return Err(Dialect1Error::NoOperator(raw.clone()));
            };
            let Some(key) = self.expand_alias(alias) else {
                return Err(Dialect1Error::UnknownAlias(alias.to_string()));
            };
            expanded.push((key, value.to_string()));
        }
        let mut message = self.rebuild(expanded)?;
        if self.ed_b36() || self.er_b36() {
            message = self.decode_evidence_encodings(message);
        }
        let re_encoded = self.encode_dialect2(&message)?;
        if re_encoded != text {
            return Err(Dialect1Error::NotFixedPoint(re_encoded));
        }
        Ok(message)
    }

    /// Applies the ED/ER base36 recodings to a message copy (encode
    /// direction: fixed-width lowercase hex to base36).
    fn encode_evidence_encodings(&self, message: &AgentCommMessage) -> AgentCommMessage {
        let mut out = message.clone();
        let ed = self.ed_b36();
        let er = self.er_b36();
        for evidence in out.evidence.iter_mut() {
            if ed {
                if let Some(digest) = evidence.digest.clone() {
                    evidence.digest = Some(hex_to_b36_fixed(&digest, 32));
                }
            }
            if er {
                if let Some(revision) = evidence.revision.clone() {
                    evidence.revision = Some(hex9_to_b36(&revision));
                }
            }
        }
        out
    }

    /// Inverse of [`Self::encode_evidence_encodings`]; non-matching
    /// values pass through untouched (the strict fixed point catches
    /// anything ambiguous).
    fn decode_evidence_encodings(&self, message: AgentCommMessage) -> AgentCommMessage {
        let mut out = message;
        let ed = self.ed_b36();
        let er = self.er_b36();
        for evidence in out.evidence.iter_mut() {
            if ed {
                if let Some(digest) = evidence.digest.clone() {
                    evidence.digest = Some(b36_to_hex_fixed(&digest, 32, 49..=50));
                }
            }
            if er {
                if let Some(revision) = evidence.revision.clone() {
                    evidence.revision = Some(b36_to_hex9(&revision));
                }
            }
        }
        out
    }

    /// One wire line, encode direction: timestamp epoch recoding (both
    /// the 14-digit stamp and the raw ISO form, so never-compress
    /// messages are covered too) and the 3-digit confidence recoding.
    fn encode_line_v2(&self, line: &str, never: bool) -> String {
        let Some((alias, value)) = line.split_once('=') else {
            return line.to_string();
        };
        let ts_alias = self.epoch_alias("TS");
        let exp_alias = self.epoch_alias("EXP");
        let epoch_fields = [ts_alias.as_deref(), exp_alias.as_deref()];
        if epoch_fields.contains(&Some(alias)) {
            if is_stamp14(value) {
                if let Some(epoch) = stamp14_to_epoch(value) {
                    return format!("{alias}={}", to_b36_u64(epoch));
                }
            }
            if is_iso8601_z(value) {
                if let Some(epoch) = iso_to_epoch(value) {
                    return format!("{alias}={}", to_b36_u64(epoch));
                }
            }
            return line.to_string();
        }
        if !never {
            if let Some(fc) = self.fc_alias() {
                if let Some((base, index)) = split_trailing_digits(alias) {
                    if base == fc
                        && !index.is_empty()
                        && value.len() == 3
                        && value.bytes().all(|b| b.is_ascii_digit())
                    {
                        if let Ok(number) = value.parse::<u64>() {
                            return format!("{alias}={}", to_b36_u64(number));
                        }
                    }
                }
            }
        }
        line.to_string()
    }

    /// One wire line, decode direction: base36 epochs restore to ISO
    /// (the v1 MT6/MT7 decode rules then no-op on ISO, so never-compress
    /// messages round-trip too) and base36 confidences restore to digits.
    fn decode_line_v2(&self, line: &str) -> String {
        let Some((alias, value)) = line.split_once('=') else {
            return line.to_string();
        };
        let ts_alias = self.epoch_alias("TS");
        let exp_alias = self.epoch_alias("EXP");
        let epoch_fields = [ts_alias.as_deref(), exp_alias.as_deref()];
        if epoch_fields.contains(&Some(alias)) {
            if is_b36_stamp(value) && !is_stamp14(value) {
                if let Some(epoch) = from_b36_u64(value) {
                    if let Some(iso) = epoch_to_iso(epoch as i64) {
                        return format!("{alias}={iso}");
                    }
                }
            }
            return line.to_string();
        }
        if let Some(fc) = self.fc_alias() {
            if let Some((base, index)) = split_trailing_digits(alias) {
                if base == fc && !index.is_empty() && is_b36_stamp(value) {
                    if let Some(number) = from_b36_u64(value) {
                        if (100..=999).contains(&number) && to_b36_u64(number) == value {
                            return format!("{alias}={number:03}");
                        }
                    }
                }
            }
        }
        line.to_string()
    }

    /// Merges the MSGID/TASK/TRACE/TS lines onto one space-joined line
    /// when all four are present and none of the values contains a
    /// space; otherwise the message keeps separate lines. The merged
    /// line takes the position of the earliest merged line.
    fn scalar_pack_lines(&self, lines: Vec<String>) -> Vec<String> {
        let mut wanted: Vec<(usize, &str, &str)> = Vec::new(); // (index, alias, field)
        for field in SCALAR_PACK_FIELDS {
            if let Some(alias) = self.key_aliases.get(field) {
                wanted.push((0, alias, field));
            }
        }
        let mut found: BTreeMap<&str, (usize, String)> = BTreeMap::new();
        for (index, line) in lines.iter().enumerate() {
            if let Some((alias, value)) = line.split_once('=') {
                for (_i, want_alias, field) in &wanted {
                    if alias == *want_alias && !found.contains_key(*field) {
                        found.insert(field, (index, value.to_string()));
                    }
                }
            }
        }
        if found.len() != SCALAR_PACK_FIELDS.len() {
            return lines;
        }
        let values: Vec<(usize, String)> = SCALAR_PACK_FIELDS
            .iter()
            .map(|field| found[*field].clone())
            .collect();
        if values.iter().any(|(_, value)| value.contains(' ')) {
            return lines;
        }
        let indexes: Vec<usize> = values.iter().map(|(i, _)| *i).collect();
        let merged =
            format!("{SCALAR_PACK_ALIAS}={}", values.iter().map(|(_, v)| v.as_str()).collect::<Vec<_>>().join(" "));
        let mut out: Vec<String> = lines
            .into_iter()
            .enumerate()
            .filter(|(index, _)| !indexes.contains(index))
            .map(|(_, line)| line)
            .collect();
        let position = *indexes.iter().min().expect("four indexes");
        out.insert(position, merged);
        out
    }

    /// Inverse of [`Self::scalar_pack_lines`] for one line.
    fn scalar_unpack_line(&self, line: &str) -> Result<Vec<String>, Dialect1Error> {
        let payload = &line[SCALAR_PACK_ALIAS.len() + 1..];
        let parts: Vec<&str> = payload.split(' ').collect();
        if parts.len() != SCALAR_PACK_FIELDS.len() {
            return Err(Dialect1Error::Field(format!(
                "scalar pack line has {} parts",
                parts.len()
            )));
        }
        Ok(SCALAR_PACK_FIELDS
            .iter()
            .zip(parts)
            .map(|(field, value)| format!("{}={value}", self.key_aliases[*field]))
            .collect())
    }
}

/// Lowercase base36 of a `u64`, no leading zeros ("0" for zero). Matches
/// the research `dialect2.to_b36`.
fn to_b36_u64(number: u64) -> String {
    const DIGITS: &[u8; 36] = b"0123456789abcdefghijklmnopqrstuvwxyz";
    if number == 0 {
        return "0".to_string();
    }
    let mut n = number;
    let mut out = Vec::new();
    while n > 0 {
        out.push(DIGITS[(n % 36) as usize]);
        n /= 36;
    }
    out.reverse();
    String::from_utf8(out).expect("base36 digits are ASCII")
}

/// Inverse of [`to_b36_u64`]; rejects empty strings and non-base36
/// characters.
fn from_b36_u64(text: &str) -> Option<u64> {
    if text.is_empty() {
        return None;
    }
    let mut out: u64 = 0;
    for ch in text.chars() {
        let digit = match ch {
            '0'..='9' => ch as u64 - '0' as u64,
            'a'..='z' => ch as u64 - 'a' as u64 + 10,
            _ => return None,
        };
        out = out.checked_mul(36)?.checked_add(digit)?;
    }
    Some(out)
}

/// Lowercase base36 of a fixed-width hex string of `bytes` bytes. Values
/// that are not exactly `2*bytes` lowercase hex pass through unchanged.
fn hex_to_b36_fixed(value: &str, bytes: usize) -> String {
    if value.len() != 2 * bytes || !value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) {
        return value.to_string();
    }
    let mut digits: Vec<u8> = Vec::with_capacity(bytes);
    let raw = value.as_bytes();
    for pair in raw.chunks_exact(2) {
        let hi = (pair[0] as char).to_digit(16).expect("checked hex");
        let lo = (pair[1] as char).to_digit(16).expect("checked hex");
        digits.push((hi * 16 + lo) as u8);
    }
    bytes_to_b36(&digits)
}

/// Inverse of [`hex_to_b36_fixed`]: base36 text whose length falls in
/// `window` recodes to `2*bytes` lowercase hex; anything else passes
/// through unchanged. The window mirrors the research patterns
/// (`[0-9a-z]{49,50}` for digests, `[0-9a-z]{7}` for revisions).
fn b36_to_hex_fixed(value: &str, bytes: usize, window: std::ops::RangeInclusive<usize>) -> String {
    let length = value.chars().count();
    if !window.contains(&length) || !value.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit()) {
        return value.to_string();
    }
    let Some(recovered) = b36_to_bytes(value) else {
        return value.to_string();
    };
    if recovered.len() != bytes {
        return value.to_string();
    }
    let mut out = String::with_capacity(2 * bytes);
    for byte in recovered {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// Nine lowercase hex digits (36 bits, e.g. a truncated git SHA) to
/// base36; anything else passes through unchanged. Mirrors the research
/// `dialect2._revision_to_b36` (`[0-9a-f]{9}` gate).
fn hex9_to_b36(value: &str) -> String {
    if value.len() == 9
        && value
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    {
        if let Ok(number) = u64::from_str_radix(value, 16) {
            return to_b36_u64(number);
        }
    }
    value.to_string()
}

/// Inverse of [`hex9_to_b36`]: seven base36 characters recode to nine
/// lowercase hex digits; anything else passes through unchanged (the
/// strict fixed point catches ambiguity loudly). Mirrors the research
/// `dialect2._revision_from_b36` (`[0-9a-z]{7}` gate).
fn b36_to_hex9(value: &str) -> String {
    if value.chars().count() == 7
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
    {
        if let Some(number) = from_b36_u64(value) {
            let recovered = format!("{number:09x}");
            if recovered.len() == 9 && to_b36_u64(number) == value {
                return recovered;
            }
        }
    }
    value.to_string()
}

/// Base36 of a big-endian byte string (arbitrary length), no leading
/// zeros. Mirrors Python `to_b36(int.from_bytes(...))`.
fn bytes_to_b36(digits: &[u8]) -> String {
    const B36: &[u8; 36] = b"0123456789abcdefghijklmnopqrstuvwxyz";
    let mut work = digits.to_vec();
    let mut out: Vec<u8> = Vec::new();
    while work.iter().any(|&b| b != 0) {
        let mut remainder: u32 = 0;
        for byte in work.iter_mut() {
            let current = remainder * 256 + *byte as u32;
            *byte = (current / 36) as u8;
            remainder = current % 36;
        }
        out.push(B36[remainder as usize]);
    }
    if out.is_empty() {
        out.push(b'0');
    }
    out.reverse();
    String::from_utf8(out).expect("base36 digits are ASCII")
}

/// Inverse of [`bytes_to_b36`]; `None` on non-base36 characters or when
/// the result exceeds 64 bytes.
fn b36_to_bytes(text: &str) -> Option<Vec<u8>> {
    let mut bytes: Vec<u8> = vec![0u8; (text.len() * 6 / 8) + 1];
    for ch in text.chars() {
        let digit = match ch {
            '0'..='9' => ch.to_digit(10)?,
            'a'..='z' => ch.to_digit(36)?,
            _ => return None,
        };
        let mut carry = digit;
        for byte in bytes.iter_mut().rev() {
            let current = *byte as u32 * 36 + carry;
            *byte = (current & 0xFF) as u8;
            carry = current >> 8;
        }
        if carry != 0 {
            return None;
        }
    }
    while bytes.len() > 1 && bytes[0] == 0 {
        bytes.remove(0);
    }
    Some(bytes)
}

/// True for the 14-digit `YYYYMMDDHHMMSS` stamp.
fn is_stamp14(value: &str) -> bool {
    value.len() == 14 && value.bytes().all(|b| b.is_ascii_digit())
}

/// True for the exact RFC 3339 form `YYYY-MM-DDTHH:MM:SSZ`.
fn is_iso8601_z(value: &str) -> bool {
    value.len() == 20
        && value.as_bytes()[4] == b'-'
        && value.as_bytes()[7] == b'-'
        && value.as_bytes()[10] == b'T'
        && value.as_bytes()[13] == b':'
        && value.as_bytes()[16] == b':'
        && value.ends_with('Z')
        && value
            .bytes()
            .filter(|b| !matches!(b, b'-' | b'T' | b':' | b'Z'))
            .all(|b| b.is_ascii_digit())
}

/// True for the base36 stamp window `[0-9a-z]{1,10}`.
fn is_b36_stamp(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 10
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
}

/// `YYYYMMDDHHMMSS` to Unix epoch seconds (UTC).
fn stamp14_to_epoch(stamp: &str) -> Option<u64> {
    if !is_stamp14(stamp) {
        return None;
    }
    let year: i32 = stamp[0..4].parse().ok()?;
    let month: u32 = stamp[4..6].parse().ok()?;
    let day: u32 = stamp[6..8].parse().ok()?;
    let hour: u32 = stamp[8..10].parse().ok()?;
    let minute: u32 = stamp[10..12].parse().ok()?;
    let second: u32 = stamp[12..14].parse().ok()?;
    let date = chrono::NaiveDate::from_ymd_opt(year, month, day)?;
    let time = date.and_hms_opt(hour, minute, second)?;
    Some(time.and_utc().timestamp().max(0) as u64)
}

/// `YYYY-MM-DDTHH:MM:SSZ` to Unix epoch seconds.
fn iso_to_epoch(stamp: &str) -> Option<u64> {
    let naive = chrono::NaiveDateTime::parse_from_str(stamp, "%Y-%m-%dT%H:%M:%SZ").ok()?;
    Some(naive.and_utc().timestamp().max(0) as u64)
}

/// Unix epoch seconds to `YYYY-MM-DDTHH:MM:SSZ`.
fn epoch_to_iso(epoch: i64) -> Option<String> {
    let datetime = chrono::DateTime::from_timestamp(epoch, 0)?;
    Some(
        datetime
            .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
            .to_string(),
    )
}

/// Splits a trailing run of ASCII digits off an alias
/// (`c12` -> `("c", "12")`).
fn split_trailing_digits(alias: &str) -> Option<(&str, &str)> {
    let mut split = alias.len();
    while split > 0 && alias.as_bytes()[split - 1].is_ascii_digit() {
        split -= 1;
    }
    Some((&alias[..split], &alias[split..]))
}

// ---------------------------------------------------------------------------
// Production translation
// ---------------------------------------------------------------------------

/// One deterministic repair performed during translation: a base symbol
/// (`slot` is a canonical slot name) or a subfield marker (`slot` is a
/// canonical subfield name).
#[derive(Debug, Clone, PartialEq)]
pub struct SymbolSubstitution {
    /// Canonical slot or subfield whose wire symbol/marker was repaired.
    pub slot: String,
    /// The research card's wire alias.
    pub research_alias: String,
    /// The production symbol used instead.
    pub production_symbol: String,
}

/// The OCC-1 key each canonical base slot is expressed through.
fn slot_key(slot: &str) -> Option<&'static str> {
    match slot {
        slots::MESSAGE_ID => Some("MSGID"),
        slots::TASK_ID => Some("TASK"),
        slots::PARENT_TASK_ID => Some("PARENT"),
        slots::TRACE_ID => Some("TRACE"),
        slots::SENDER => Some("SENDER"),
        slots::RECIPIENT => Some("RECIPIENT"),
        slots::KIND => Some("KIND"),
        slots::STATUS => Some("STATUS"),
        slots::SEQUENCE => Some("SEQ"),
        slots::TIMESTAMP => Some("TS"),
        slots::EXPIRY => Some("EXP"),
        slots::NEXT_ACTION => Some("NEXT"),
        slots::VERIFICATION_CLAIM => Some("V"),
        slots::FINDING_STATEMENT => Some("F"),
        slots::EVIDENCE_REF => Some("E"),
        slots::ARTIFACT => Some("ART"),
        slots::DEPENDENCY => Some("DEP"),
        _ => None,
    }
}

/// The OCC-1 key each canonical subfield is expressed through.
fn subfield_key(subfield: &str) -> Option<&'static str> {
    match subfield {
        subfields::FINDING_CONFIDENCE => Some("FC"),
        subfields::FINDING_EVIDENCE => Some("FE"),
        subfields::EVIDENCE_LINE_RANGE => Some("EL"),
        subfields::EVIDENCE_DIGEST => Some("ED"),
        subfields::EVIDENCE_REVISION => Some("ER"),
        subfields::EVIDENCE_VERIFIED => Some("EV"),
        subfields::VERIFICATION_STATUS => Some("VS"),
        subfields::VERIFICATION_METHOD => Some("VM"),
        subfields::VERIFICATION_RECEIPT => Some("VR"),
        _ => None,
    }
}

impl ResearchDialectCard {
    /// Translates the card into a production [`DialectCard`] plus the
    /// list of deterministic symbol repairs the translation performed.
    ///
    /// Base symbols are the uppercased research aliases. When an
    /// uppercased alias violates production's prefix-freedom rule (one
    /// symbol being a prefix of another), the later slot in canonical
    /// order is repaired to the smallest uppercase letter that no
    /// assigned symbol starts with; every repair is reported, nothing
    /// is dropped.
    pub fn translate_to_production(
        &self,
    ) -> Result<(DialectCard, Vec<SymbolSubstitution>), DialectLoadError> {
        self.translate_to_production_as(PROMOTED_DIALECT_ID, PROMOTED_DIALECT_VERSION)
    }

    /// [`Self::translate_to_production`] with an explicit production
    /// identity, used for the second promoted dialect (`sym-v2-d2-r1`).
    fn translate_to_production_as(
        &self,
        dialect_id: &str,
        version: u32,
    ) -> Result<(DialectCard, Vec<SymbolSubstitution>), DialectLoadError> {
        let alias_of = |key: &str| -> Option<String> {
            self.key_aliases
                .get(key)
                .cloned()
                .or_else(|| self.family_aliases.get(key).cloned())
        };
        let mut missing: Vec<String> = Vec::new();
        for &slot in REQUIRED_SLOTS {
            let Some(key) = slot_key(slot) else {
                missing.push(format!("canonical slot {slot:?} has no OCC-1 key"));
                continue;
            };
            if alias_of(key).is_none() {
                missing.push(format!(
                    "no DIALECT/1 alias for OCC-1 key {key:?} (slot {slot:?})"
                ));
            }
        }
        for &subfield in REQUIRED_SUBFIELDS {
            let Some(key) = subfield_key(subfield) else {
                missing.push(format!("canonical subfield {subfield:?} has no OCC-1 key"));
                continue;
            };
            if alias_of(key).is_none() {
                missing.push(format!(
                    "no DIALECT/1 alias for OCC-1 key {key:?} (subfield {subfield:?})"
                ));
            }
        }
        if !missing.is_empty() {
            return Err(DialectLoadError::Translation(missing.join("; ")));
        }

        let mut assigned: Vec<String> = Vec::new();
        let mut substitutions: Vec<SymbolSubstitution> = Vec::new();
        let mut slot_symbols: BTreeMap<String, String> = BTreeMap::new();
        for &slot in REQUIRED_SLOTS {
            let key = slot_key(slot).expect("coverage checked above");
            let alias = alias_of(key).expect("coverage checked above");
            let mut symbol = alias.to_uppercase();
            if symbol.is_empty() || !symbol.chars().all(|c| c.is_ascii_uppercase()) {
                return Err(DialectLoadError::Translation(format!(
                    "alias {alias:?} for {key} does not translate to an uppercase A-Z symbol"
                )));
            }
            if collides(&symbol, &assigned) {
                let repaired = smallest_free_letter(&assigned).ok_or_else(|| {
                    DialectLoadError::Translation(
                        "no unused uppercase letter left for symbol repair".into(),
                    )
                })?;
                substitutions.push(SymbolSubstitution {
                    slot: slot.to_string(),
                    research_alias: alias.clone(),
                    production_symbol: repaired.clone(),
                });
                symbol = repaired;
            }
            assigned.push(symbol.clone());
            slot_symbols.insert(slot.to_string(), symbol);
        }

        let mut markers: BTreeMap<String, String> = BTreeMap::new();
        let mut used_per_base: BTreeMap<&'static str, Vec<char>> = BTreeMap::new();
        for &subfield in REQUIRED_SUBFIELDS {
            let key = subfield_key(subfield).expect("coverage checked above");
            let alias = alias_of(key).expect("coverage checked above");
            let chars: Vec<char> = alias.chars().collect();
            let (base, _) = crate::core::occ::dialect::subfield_base(subfield)
                .expect("required subfields always resolve to a base slot");
            let used = used_per_base.entry(base).or_default();
            let mut marker = None;
            if chars.len() == 1 && chars[0].is_ascii_lowercase() && !used.contains(&chars[0]) {
                marker = Some(chars[0]);
            } else if let Some(last) = chars.last().copied() {
                // Repair heuristic: the distinguishing suffix character of a
                // two-character research alias (`vs` -> `s`), when it is a
                // free lowercase letter within the base slot.
                if last.is_ascii_lowercase() && !used.contains(&last) {
                    marker = Some(last);
                    substitutions.push(SymbolSubstitution {
                        slot: subfield.to_string(),
                        research_alias: alias.clone(),
                        production_symbol: last.to_string(),
                    });
                }
            }
            let marker = marker.unwrap_or_else(|| {
                // Final fallback: the smallest free lowercase letter in the
                // base slot.
                let letter = ('a'..='z')
                    .find(|candidate| !used.contains(candidate))
                    .expect("a base slot has at most nine subfields");
                substitutions.push(SymbolSubstitution {
                    slot: subfield.to_string(),
                    research_alias: alias.clone(),
                    production_symbol: letter.to_string(),
                });
                letter
            });
            used.push(marker);
            markers.insert(subfield.to_string(), marker.to_string());
        }

        let symbol_table = SymbolTable {
            slots: slot_symbols,
            subfields: markers,
        };
        let grammar = DialectGrammar {
            operator: '=',
            list_separator: ',',
        };
        let sha256 = codebook_sha256(&grammar, &symbol_table);
        let card = DialectCard {
            dialect_id: dialect_id.to_string(),
            version,
            grammar,
            symbol_table,
            sha256,
            speaker_models: Vec::new(),
            listener_models: Vec::new(),
            task_types: Vec::new(),
            metrics: Default::default(),
            fallback: FallbackPolicy::Occ1,
        };
        card.validate()
            .map_err(|error| DialectLoadError::Translation(error.to_string()))?;
        Ok((card, substitutions))
    }
}

/// True when the candidate equals or is a prefix of an assigned symbol,
/// or an assigned symbol is a prefix of it.
fn collides(candidate: &str, assigned: &[String]) -> bool {
    assigned
        .iter()
        .any(|symbol| symbol.starts_with(candidate) || candidate.starts_with(symbol))
}

/// The smallest uppercase letter no assigned symbol starts with.
fn smallest_free_letter(assigned: &[String]) -> Option<String> {
    ('A'..='Z')
        .map(|letter| letter.to_string())
        .find(|letter| !assigned.iter().any(|symbol| symbol.starts_with(letter)))
}

// ---------------------------------------------------------------------------
// Promoted bundle and registration
// ---------------------------------------------------------------------------

/// The promoted dialect: the validated research card together with its
/// production registry identity.
#[derive(Debug, Clone)]
pub struct PromotedDialect {
    /// The validated, compiled DIALECT/1 research card.
    pub research: ResearchDialectCard,
    /// The production registry card (OCC-2 codec identity).
    pub production: DialectCard,
    /// Deterministic symbol repairs performed by the translation.
    pub substitutions: Vec<SymbolSubstitution>,
}

impl PromotedDialect {
    /// Loads the frozen research card embedded at compile time.
    pub fn load_embedded() -> Result<Self, DialectLoadError> {
        let research = load_research_card(FROZEN_CARD_JSON)?;
        let (production, substitutions) = research.translate_to_production()?;
        Ok(Self {
            research,
            production,
            substitutions,
        })
    }

    /// Loads the frozen DIALECT/2 winner card embedded at compile time
    /// (`sym-v2-d2-r1`).
    pub fn load_embedded_v2() -> Result<Self, DialectLoadError> {
        let research = load_research_card(FROZEN2_CARD_JSON)?;
        let (production, substitutions) =
            research.translate_to_production_as(PROMOTED2_DIALECT_ID, PROMOTED_DIALECT_VERSION)?;
        Ok(Self {
            research,
            production,
            substitutions,
        })
    }

    /// Encodes one message onto the dialect wire (DIALECT/2 extensions
    /// when the card enables them, DIALECT/1 otherwise).
    pub fn encode(&self, message: &AgentCommMessage) -> Result<String, Dialect1Error> {
        if self.research.has_v2_features() {
            self.research.encode_dialect2(message)
        } else {
            self.research.encode_dialect1(message)
        }
    }

    /// Strict-decodes one dialect wire message (DIALECT/2 extensions when
    /// the card enables them, DIALECT/1 otherwise).
    pub fn decode(&self, wire: &str) -> Result<AgentCommMessage, Dialect1Error> {
        if self.research.has_v2_features() {
            self.research.decode_dialect2(wire)
        } else {
            self.research.decode_dialect1(wire)
        }
    }
}

/// The explicit, experimental registry: `sym-v1` plus the promoted
/// `sym-v1-pt-pt-r1`. The default registry
/// (`DialectRegistry::with_sym_v1()`) is untouched; callers must opt in.
/// Note that `select_occ2_dialect` still refuses the promoted card
/// without the development override because its metrics are honestly
/// unevaluated.
pub fn promoted_registry() -> Result<DialectRegistry, DialectLoadError> {
    let mut registry = DialectRegistry::with_sym_v1();
    let promoted = PromotedDialect::load_embedded()?;
    registry
        .insert(promoted.production)
        .map_err(|error| DialectLoadError::Translation(error.to_string()))?;
    Ok(registry)
}

/// The explicit experimental registry with both promoted cards:
/// `sym-v1`, the promoted `sym-v1-pt-pt-r1`, and the promoted DIALECT/2
/// winner `sym-v2-d2-r1`. Never the default registry;
/// `select_occ2_dialect` still refuses both promoted cards without the
/// development override because their metrics are honestly unevaluated.
pub fn promoted_registry_v2() -> Result<DialectRegistry, DialectLoadError> {
    let mut registry = promoted_registry()?;
    let promoted2 = PromotedDialect::load_embedded_v2()?;
    registry
        .insert(promoted2.production)
        .map_err(|error| DialectLoadError::Translation(error.to_string()))?;
    Ok(registry)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// Tamper the frozen card (with a fixed-up checksum) to exercise
    /// semantic rejection paths.
    fn tamper(mutate: impl FnOnce(&mut serde_json::Value)) -> String {
        tamper_card(FROZEN_CARD_JSON, mutate)
    }

    fn tamper_card(json: &str, mutate: impl FnOnce(&mut serde_json::Value)) -> String {
        let mut value: serde_json::Value = serde_json::from_str(json).expect("card parses");
        mutate(&mut value);
        let canonical = canonical_json(&select_body(&value));
        value["checksum"] = serde_json::Value::String(hex_sha256(&canonical));
        serde_json::to_string(&value).expect("tampered card serializes")
    }

    /// The checksummed body of a card value: the ten v1 body keys plus
    /// the DIALECT/2 keys exactly when the research `body()` emits them.
    fn select_body(value: &serde_json::Value) -> serde_json::Value {
        let mut body = serde_json::Map::new();
        for key in BODY_KEYS {
            body.insert(
                key.to_string(),
                value.get(key).cloned().unwrap_or(serde_json::Value::Null),
            );
        }
        if let Some(header) = value.get("wire_header").and_then(|v| v.as_str()) {
            if header != DIALECT1_HEADER {
                body.insert("wire_header".into(), serde_json::Value::String(header.into()));
            }
        }
        if let Some(encodings) = value.get("value_encodings") {
            if encodings.as_object().is_some_and(|map| !map.is_empty()) {
                body.insert("value_encodings".into(), encodings.clone());
            }
        }
        if value.get("scalar_pack").and_then(|v| v.as_bool()) == Some(true) {
            body.insert("scalar_pack".into(), serde_json::Value::Bool(true));
        }
        serde_json::Value::Object(body)
    }

    #[test]
    fn frozen_card_loads_and_verifies() {
        let card = load_research_card(FROZEN_CARD_JSON).expect("frozen card loads");
        assert_eq!(card.id(), "sym-v1-pt-pt");
        assert_eq!(card.checksum(), FROZEN_CARD_CHECKSUM);
        assert_eq!(card.generation(), 4);
        assert_eq!(
            card.lineage(),
            &["packing_toggle:DEP=off", "packing_toggle:ART=off"]
        );
        assert_eq!(card.packings(), &["E", "FE", "NEXT", "FC"]);
    }

    #[test]
    fn checksum_tamper_is_rejected() {
        let mut value: serde_json::Value =
            serde_json::from_str(FROZEN_CARD_JSON).expect("frozen card parses");
        value["packings"].as_array_mut().unwrap().reverse();
        let raw = serde_json::to_string(&value).expect("serializes");
        let err = load_research_card(&raw).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::ChecksumMismatch { .. }),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn unknown_top_level_key_is_rejected() {
        let json = tamper(|value| {
            value["surprise"] = serde_json::Value::String("x".into());
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Unsupported { ref constructs } if constructs.len() == 1
                && constructs[0].contains("surprise")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn unknown_packing_is_rejected() {
        let json = tamper(|value| {
            value["packings"].as_array_mut().unwrap().push("ZZZ".into());
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Unsupported { ref constructs } if constructs[0].contains("ZZZ")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn duplicate_packing_is_rejected() {
        let json = tamper(|value| {
            value["packings"].as_array_mut().unwrap().push("E".into());
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Invalid(ref message) if message.contains("duplicate packing")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn wildcard_microtemplate_field_is_rejected() {
        let json = tamper(|value| {
            value["microtemplates"].as_array_mut().unwrap().push(serde_json::json!({
                "id": "MTX", "field": "*", "type": "prefix", "match": "x", "emit": "y"
            }));
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Unsupported { ref constructs } if constructs[0].contains("wildcard")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn unknown_microtemplate_type_is_rejected() {
        let json = tamper(|value| {
            value["microtemplates"].as_array_mut().unwrap().push(serde_json::json!({
                "id": "MTX", "field": "TASK", "type": "rot13", "match": "x", "emit": "y"
            }));
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Unsupported { ref constructs } if constructs[0].contains("rot13")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn non_total_value_symbols_are_rejected() {
        let json = tamper(|value| {
            value["value_symbols"]["STATUS"]
                .as_object_mut()
                .unwrap()
                .remove("pending");
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Invalid(ref message) if message.contains("not total")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn alias_grammar_violation_is_rejected() {
        let json = tamper(|value| {
            value["key_aliases"]["TASK"] = serde_json::Value::String("UPPER".into());
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Invalid(ref message) if message.contains("grammar")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn oversized_card_is_rejected() {
        let json = tamper(|value| {
            value["usage_rules"] =
                serde_json::Value::String("x".repeat(MAX_USAGE_RULES_BYTES + 1));
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(matches!(err, DialectLoadError::Bounds(_)), "{err}");
    }

    #[test]
    fn translation_is_valid_and_prefix_free() {
        let card = load_research_card(FROZEN_CARD_JSON).expect("frozen card loads");
        let (production, substitutions) = card.translate_to_production().expect("translates");
        assert_eq!(production.dialect_id, PROMOTED_DIALECT_ID);
        production.validate().expect("production card validates");
        // Expected repairs: the EXP alias "xp" collides with TRACE "x" (the
        // uppercase symbols must stay prefix-free), and the two-character
        // verification subfield aliases "vs"/"vm" must become single
        // lowercase markers.
        assert_eq!(
            substitutions,
            vec![
                SymbolSubstitution {
                    slot: "expiry".to_string(),
                    research_alias: "xp".to_string(),
                    production_symbol: "A".to_string(),
                },
                SymbolSubstitution {
                    slot: "verification.status".to_string(),
                    research_alias: "vs".to_string(),
                    production_symbol: "s".to_string(),
                },
                SymbolSubstitution {
                    slot: "verification.method".to_string(),
                    research_alias: "vm".to_string(),
                    production_symbol: "m".to_string(),
                },
                SymbolSubstitution {
                    slot: "verification.receipt".to_string(),
                    research_alias: "vr".to_string(),
                    production_symbol: "r".to_string(),
                },
            ]
        );
        let symbols: Vec<&String> = production.symbol_table.slots.values().collect();
        for a in &symbols {
            for b in &symbols {
                assert!(
                    a == b || !b.starts_with(a.as_str()),
                    "prefix collision between {a} and {b}"
                );
            }
        }
    }

    #[test]
    fn promoted_registry_is_explicit_not_default() {
        let default = DialectRegistry::with_sym_v1();
        assert!(default.get(PROMOTED_DIALECT_ID, 1).is_none());
        let promoted = promoted_registry().expect("promoted registry builds");
        assert!(promoted.get(PROMOTED_DIALECT_ID, 1).is_some());
        assert!(promoted.get("sym-v1", 1).is_some());
    }

    #[test]
    fn small_round_trip() {
        let card = load_research_card(FROZEN_CARD_JSON).expect("frozen card loads");
        let mut message = AgentCommMessage::new("TASK", "t1", "pending");
        message.message_id = Some("m-1".into());
        message.trace_id = Some("trace-t1".into());
        message.sender_identity = Some("daemon://coordinator".into());
        message.recipient_identity = Some("agent://worker-b".into());
        message.sequence = Some(1);
        message.findings.push(Finding {
            id: "F1".into(),
            statement: "do the thing".into(),
            confidence: Some(0.75),
            evidence: vec!["E1".into()],
        });
        message.evidence.push(EvidenceRef {
            id: "E1".into(),
            kind: "src".into(),
            locator: "crates/pi-coding-agent/src/main.rs".into(),
            line_range: Some((10, 20)),
            digest: None,
            revision: Some("ce8480d59".into()),
            verified: Some(true),
        });
        message.next_actions.push("act".into());
        message.timestamp = Some("2026-10-16T09:02:00Z".into());
        let wire = card.encode_dialect1(&message).expect("encodes");
        assert!(wire.starts_with("D/1\n"));
        let decoded = card.decode_dialect1(&wire).expect("decodes");
        assert_eq!(decoded, message);
    }

    #[test]
    fn strict_decode_rejects_tampering() {
        let card = load_research_card(FROZEN_CARD_JSON).expect("frozen card loads");
        let mut message = AgentCommMessage::new("TASK", "t1", "pending");
        message.timestamp = Some("2026-10-16T09:02:00Z".into());
        let wire = card.encode_dialect1(&message).expect("encodes");
        // An unknown kind symbol is rejected by the card's symbol table.
        let tampered = wire.replace("k=T", "k=Z");
        assert!(card.decode_dialect1(&tampered).is_err());
        // A line reorder breaks the encode fixed point.
        let mut lines: Vec<&str> = wire.split('\n').collect();
        lines.swap(1, 2);
        assert!(card.decode_dialect1(&lines.join("\n")).is_err());
        assert!(card.decode_dialect1("OCC/2 D=sym-v1@1 C=x\nk=T").is_err());
    }

    #[test]
    fn fc_packing_is_exact() {
        assert_eq!(pack_fc("0.800").as_deref(), Some("800"));
        assert_eq!(pack_fc("1.000").as_deref(), Some("1000"));
        assert_eq!(pack_fc("0.075").as_deref(), Some("75"));
        assert_eq!(pack_fc("0.80").as_deref(), None);
        assert_eq!(unpack_fc("800").as_deref(), Some("0.800"));
        assert_eq!(unpack_fc("1000").as_deref(), Some("1.000"));
        assert_eq!(unpack_fc("75").as_deref(), Some("0.075"));
        assert_eq!(unpack_fc("1001").as_deref(), None);
        assert_eq!(unpack_fc("12a4").as_deref(), None);
    }

    #[test]
    fn template_translation_handles_references_and_dollars() {
        assert_eq!(
            translate_template(r"\1\2\3").expect("translates"),
            "${1}${2}${3}"
        );
        assert_eq!(translate_template("a$b").expect("translates"), "a$$b");
        assert!(translate_template(r"\g<name>").is_err());
        assert!(translate_template("x\\").is_err());
    }

    // -- DIALECT/2 promotion tests -------------------------------------

    #[test]
    fn frozen2_card_loads_and_verifies() {
        let card = load_research_card(FROZEN2_CARD_JSON).expect("frozen v2 card loads");
        assert_eq!(card.checksum(), FROZEN2_CARD_CHECKSUM);
        assert_eq!(card.id(), "d2-full-mined-status-di-sp-pt-mine-mine");
        assert_eq!(card.generation(), 8);
        assert_eq!(card.wire_header(), "d");
        assert!(card.scalar_pack());
        assert_eq!(card.value_encodings().len(), 5);
        assert!(card.has_v2_features());
    }

    #[test]
    fn v2_emit_collision_is_rejected() {
        // Two TASK prefix templates with the same emit can silently
        // misroute a decode for values outside the observed corpus.
        let json = tamper_card(FROZEN2_CARD_JSON, |value| {
            value["microtemplates"]
                .as_array_mut()
                .unwrap()
                .push(serde_json::json!({
                    "id": "V2M999",
                    "field": "TASK",
                    "type": "prefix",
                    "match": "occ2b-zz-",
                    "emit": "\u{00b6}",
                }));
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Invalid(ref message) if message.contains("same emit")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn v2_unknown_value_encoding_field_is_rejected() {
        let json = tamper_card(FROZEN2_CARD_JSON, |value| {
            value["value_encodings"]["ZZ"] = serde_json::Value::String("b36".into());
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Unsupported { ref constructs } if constructs.len() == 1
                && constructs[0].contains("ZZ")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn v2_bad_value_encoding_is_rejected() {
        let json = tamper_card(FROZEN2_CARD_JSON, |value| {
            value["value_encodings"]["TS"] = serde_json::Value::String("b36".into());
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Invalid(ref message) if message.contains("epoch36")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn v2_wire_header_alias_collision_is_rejected() {
        let json = tamper_card(FROZEN2_CARD_JSON, |value| {
            value["wire_header"] = serde_json::Value::String("m".into());
        });
        let err = load_research_card(&json).unwrap_err();
        assert!(
            matches!(err, DialectLoadError::Invalid(ref message) if message.contains("collides")),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn v2_featureless_card_keeps_v1_checksum() {
        // A v1 card carrying explicit inert v2 keys (wire_header "D/1",
        // empty value_encodings, scalar_pack false) checksums identically
        // to the plain v1 card: the canonical body selection omits them.
        let json = tamper(|value| {
            value["wire_header"] = serde_json::Value::String(DIALECT1_HEADER.into());
            value["value_encodings"] = serde_json::json!({});
            value["scalar_pack"] = serde_json::Value::Bool(false);
        });
        let card = load_research_card(&json).expect("feature-less v2 keys load");
        assert_eq!(card.checksum(), FROZEN_CARD_CHECKSUM);
        assert!(!card.has_v2_features());
        assert_eq!(card.wire_header(), DIALECT1_HEADER);
    }

    #[test]
    fn promoted2_translates_and_registers() {
        let promoted = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        assert_eq!(promoted.production.dialect_id, PROMOTED2_DIALECT_ID);
        promoted
            .production
            .validate()
            .expect("v2 production card validates");
        let registry = promoted_registry_v2().expect("v2 registry builds");
        assert!(registry.get(PROMOTED2_DIALECT_ID, 1).is_some());
        assert!(registry.get(PROMOTED_DIALECT_ID, 1).is_some());
        assert!(registry.get("sym-v1", 1).is_some());
        // Still explicit, never the default.
        assert!(DialectRegistry::with_sym_v1()
            .get(PROMOTED2_DIALECT_ID, 1)
            .is_none());
    }

    #[test]
    fn base36_helpers_round_trip() {
        assert_eq!(to_b36_u64(0), "0");
        assert_eq!(to_b36_u64(1792141320), "tmzt3c");
        assert_eq!(from_b36_u64("tmzt3c"), Some(1792141320));
        assert_eq!(from_b36_u64(""), None);
        assert_eq!(from_b36_u64("T"), None);
        let digest = "22f20801191539b2c888c695878804959d03ee676b58f1d6322f3808862e2632";
        let b36 = hex_to_b36_fixed(digest, 32);
        assert_eq!(b36.len(), 49);
        assert_eq!(b36_to_hex_fixed(&b36, 32, 49..=50), digest);
        // Non-hex and wrong-width values pass through unchanged.
        assert_eq!(hex_to_b36_fixed("zz", 32), "zz");
        assert_eq!(b36_to_hex_fixed("zz", 32, 49..=50), "zz");
    }
}
