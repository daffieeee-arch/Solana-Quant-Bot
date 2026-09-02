//! Bounded Pump protocol evidence for the V2 walking skeleton.
//!
//! This crate intentionally supports one structural candidate only: the legacy
//! SOL-paired `buy` instruction and its `TradeEvent` representation at one
//! pinned official source revision. It does not establish historical activation.

pub mod decode;
pub mod evidence;
pub mod registry;

pub use decode::{
    BuyInstruction, Quarantine, QuarantineReason, Shareholder, TradeEvent, decode_buy_instruction,
    decode_trade_event_cpi, require_economic_identity, validate_reference_agreement,
};
pub use evidence::{
    EVIDENCE_JSON_PATH, EVIDENCE_MARKDOWN_PATH, SOURCE_MANIFEST_PATH, VECTOR_MANIFEST_PATH,
    generated_evidence_json, generated_evidence_markdown, generated_vector_manifest_json,
};
pub use registry::{
    ActivationStatus, EvidenceStatus, ExactPubkey, ProtocolRegistryEntry, QuoteAssetEvidence,
    QuoteAssetKind, QuoteMintEvidence, SourceManifest, ValidatedProtocolCandidate,
    canonical_registry_entry, resolve_registry_candidate, validate_source_manifest,
};
