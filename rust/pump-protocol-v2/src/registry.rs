use serde::{Deserialize, Serialize, Serializer};
use thiserror::Error;

pub const SOURCE_MANIFEST_ID: &str = "pump-public-docs-9c82f61-pump-idl";
pub const SOURCE_REPOSITORY: &str = "https://github.com/pump-fun/pump-public-docs";
pub const SOURCE_COMMIT: &str = "9c82f61cb711b044a17f770ab8ce9f9bdf78f333";
pub const SOURCE_PATH: &str = "idl/pump.json";
pub const SOURCE_BLOB: &str = "062e66f032bb9f295353b573be3400070bd55e5b";
pub const SOURCE_SHA256: &str = "b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49";
pub const SOURCE_URL: &str = "https://github.com/pump-fun/pump-public-docs/blob/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/idl/pump.json";
pub const RAW_SOURCE_URL: &str = "https://raw.githubusercontent.com/pump-fun/pump-public-docs/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/idl/pump.json";
pub const README_SHA256: &str = "7609341176750cd2c78a74e8b3eab052053d71cecabd0252ef9c4d8e356e8cca";
pub const PUMP_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
pub const BUY_DISCRIMINATOR: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
pub const EVENT_IX_TAG_LE: [u8; 8] = [228, 69, 165, 46, 81, 203, 154, 29];
pub const TRADE_EVENT_DISCRIMINATOR: [u8; 8] = [189, 219, 127, 211, 78, 230, 97, 238];

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ActivationStatus {
    StructuralCandidate,
    FixtureCompatible,
    ObservedCompatible,
    ProvenAtSlotRange,
    Unknown,
    Disputed,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum EvidenceStatus {
    StructuralCandidate,
    FixtureCompatible,
    ObservedCompatible,
    ProvenAtSlotRange,
    Unknown,
    Disputed,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SourceManifest {
    pub schema_version: String,
    pub source_manifest_id: String,
    pub repository: String,
    pub commit: String,
    pub committed_at: String,
    pub path: String,
    pub git_blob_sha1: String,
    pub byte_length: u64,
    pub sha256: String,
    pub retrieved_at: String,
    pub official_url: String,
    pub raw_url: String,
    pub license_status: String,
    pub evidence_classification: String,
    pub supporting_artifacts: Vec<SupportingArtifact>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SupportingArtifact {
    pub path: String,
    pub git_blob_sha1: String,
    pub byte_length: u64,
    pub sha256: String,
    pub purpose: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EvidenceValue {
    pub value: Option<String>,
    pub status: EvidenceStatus,
    pub basis: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum QuoteAssetKind {
    NativeSolAccounting,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QuoteAssetEvidence {
    pub value: Option<QuoteAssetKind>,
    pub status: EvidenceStatus,
    pub basis: String,
}

/// An exact 32-byte Solana identity. JSON evidence uses canonical lowercase
/// hexadecimal rather than a lossy number array or unchecked free text.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExactPubkey([u8; 32]);

impl ExactPubkey {
    #[must_use]
    pub const fn from_bytes(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}

impl Serialize for ExactPubkey {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&hex::encode(self.0))
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QuoteMintEvidence {
    pub value: Option<ExactPubkey>,
    pub status: EvidenceStatus,
    pub basis: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProtocolRegistryEntry {
    pub registry_entry_id: String,
    pub decoder_version: String,
    pub network: String,
    pub program_id: String,
    pub source_manifest_id: String,
    pub source_commit: String,
    pub source_path: String,
    pub source_sha256: String,
    pub instruction_name: String,
    pub instruction_schema: String,
    pub instruction_discriminator_hex: String,
    pub instruction_accounts: Vec<AccountRequirement>,
    pub event_name: String,
    pub event_schema: String,
    pub event_ix_tag_le_hex: String,
    pub event_discriminator_hex: String,
    pub quote_asset_kind: QuoteAssetEvidence,
    pub quote_mint: QuoteMintEvidence,
    pub quote_decimals: EvidenceValue,
    pub base_decimals: EvidenceValue,
    pub integer_policy: String,
    pub reserve_authority: String,
    pub account_layout_assumptions: Vec<String>,
    pub activation_status: ActivationStatus,
    pub historical_slot_range: Option<String>,
    pub evidence_status: EvidenceStatus,
    pub quarantine_rules: Vec<String>,
    pub registry_rejection_rules: Vec<String>,
}

/// A registry candidate that can only be constructed after the complete pinned
/// source receipt validates and candidate selection is unique.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidatedProtocolCandidate {
    entry: ProtocolRegistryEntry,
}

impl ValidatedProtocolCandidate {
    #[must_use]
    pub fn entry(&self) -> &ProtocolRegistryEntry {
        &self.entry
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AccountRequirement {
    pub index: u8,
    pub name: String,
    pub writable: bool,
    pub signer: bool,
    pub fixed_address: Option<String>,
}

#[derive(Debug, Error, Eq, PartialEq)]
pub enum RegistryError {
    #[error("missing_source_identity")]
    MissingSourceIdentity,
    #[error("source_identity_mismatch")]
    SourceIdentityMismatch,
    #[error("source_hash_mismatch")]
    SourceHashMismatch,
    #[error("ambiguous_version_match")]
    AmbiguousVersionMatch,
    #[error("unsupported_version")]
    UnsupportedVersion,
}

/// Validate the complete pinned official-source receipt.
///
/// # Errors
///
/// Returns a typed identity or hash error for any receipt drift.
pub fn validate_source_manifest(manifest: &SourceManifest) -> Result<(), RegistryError> {
    if manifest.source_manifest_id != SOURCE_MANIFEST_ID
        || manifest.repository != SOURCE_REPOSITORY
        || manifest.commit != SOURCE_COMMIT
        || manifest.path != SOURCE_PATH
        || manifest.git_blob_sha1 != SOURCE_BLOB
        || manifest.official_url != SOURCE_URL
        || manifest.raw_url != RAW_SOURCE_URL
    {
        return Err(RegistryError::SourceIdentityMismatch);
    }
    if manifest.sha256 != SOURCE_SHA256 {
        return Err(RegistryError::SourceHashMismatch);
    }
    if manifest.schema_version != "PUMP_OFFICIAL_SOURCE_MANIFEST_V1"
        || manifest.committed_at != "2026-07-15T18:22:27Z"
        || manifest.retrieved_at != "2026-09-02T21:09:20Z"
        || manifest.byte_length != 169_632
        || manifest.license_status != "NO_LICENSE_FILE_AT_PIN"
        || manifest.evidence_classification != "OFFICIAL_STRUCTURAL_SOURCE"
    {
        return Err(RegistryError::SourceIdentityMismatch);
    }
    if manifest.supporting_artifacts
        != [SupportingArtifact {
            path: "README.md".to_owned(),
            git_blob_sha1: "ec4a68b2aef349f9c5819dd3cf805117003cc56c".to_owned(),
            byte_length: 5_541,
            sha256: README_SHA256.to_owned(),
            purpose: "legacy buy SOL-accounting context; not quote-mint or decimal evidence"
                .to_owned(),
        }]
    {
        return Err(RegistryError::SourceIdentityMismatch);
    }
    Ok(())
}

/// Resolve exactly one candidate bound to the pinned source manifest.
///
/// # Errors
///
/// Returns a typed error when source identity is absent/unknown or candidate
/// matching is not unique.
pub fn resolve_registry_candidate(
    source_manifest: Option<&SourceManifest>,
    compatible_candidate_count: usize,
) -> Result<ValidatedProtocolCandidate, RegistryError> {
    let source_manifest = source_manifest.ok_or(RegistryError::MissingSourceIdentity)?;
    validate_source_manifest(source_manifest)?;
    match compatible_candidate_count {
        0 => Err(RegistryError::UnsupportedVersion),
        1 => Ok(ValidatedProtocolCandidate {
            entry: canonical_registry_entry(),
        }),
        _ => Err(RegistryError::AmbiguousVersionMatch),
    }
}

#[must_use]
pub fn canonical_registry_entry() -> ProtocolRegistryEntry {
    ProtocolRegistryEntry {
        registry_entry_id: "pump-mainnet-legacy-sol-buy-trade-9c82f61-v1".to_owned(),
        decoder_version: "pump-protocol-v2/0.1.0".to_owned(),
        network: "solana-mainnet-beta".to_owned(),
        program_id: PUMP_PROGRAM_ID.to_owned(),
        source_manifest_id: SOURCE_MANIFEST_ID.to_owned(),
        source_commit: SOURCE_COMMIT.to_owned(),
        source_path: SOURCE_PATH.to_owned(),
        source_sha256: SOURCE_SHA256.to_owned(),
        instruction_name: "buy".to_owned(),
        instruction_schema: "amount:u64,max_sol_cost:u64,track_volume:OptionBool(bool)".to_owned(),
        instruction_discriminator_hex: hex::encode(BUY_DISCRIMINATOR),
        instruction_accounts: buy_account_requirements(),
        event_name: "TradeEvent[is_buy=true,ix_name=buy]".to_owned(),
        event_schema: "pump-public-docs.TradeEvent@9c82f61".to_owned(),
        event_ix_tag_le_hex: hex::encode(EVENT_IX_TAG_LE),
        event_discriminator_hex: hex::encode(TRADE_EVENT_DISCRIMINATOR),
        quote_asset_kind: QuoteAssetEvidence {
            value: Some(QuoteAssetKind::NativeSolAccounting),
            status: EvidenceStatus::StructuralCandidate,
            basis: "same-revision official README describes legacy buy in SOL; event quote_mint remains unconstrained".to_owned(),
        },
        quote_mint: QuoteMintEvidence {
            value: None,
            status: EvidenceStatus::Unknown,
            basis: "selected official IDL does not constrain TradeEvent.quote_mint".to_owned(),
        },
        quote_decimals: EvidenceValue {
            value: None,
            status: EvidenceStatus::Unknown,
            basis: "selected official source does not prove a quote-decimal constant".to_owned(),
        },
        base_decimals: EvidenceValue {
            value: None,
            status: EvidenceStatus::Unknown,
            basis: "selected official source does not prove a base-decimal constant".to_owned(),
        },
        integer_policy: "all wire u64/i64 values remain exact Rust integers; no floating-point wire values".to_owned(),
        reserve_authority: "EVENT_REPORTED_RESERVES_NOT_ACCOUNT_STATE".to_owned(),
        account_layout_assumptions: vec![
            "only the official buy account order is identified; no account-state layout is decoded".to_owned(),
            "CPI envelope discriminator is fixture-compatible framing evidence, not an IDL event discriminator".to_owned(),
            "event-reported reserves never substitute for observed account state".to_owned(),
            "4096 event bytes, 32 ix_name bytes, 128 shareholders and unique addresses are conservative implementation bounds, not official IDL facts".to_owned(),
        ],
        activation_status: ActivationStatus::StructuralCandidate,
        historical_slot_range: None,
        evidence_status: EvidenceStatus::FixtureCompatible,
        quarantine_rules: vec![
            "TRUNCATED_DISCRIMINATOR".to_owned(),
            "WRONG_DISCRIMINATOR".to_owned(),
            "TRUNCATED_PAYLOAD".to_owned(),
            "UNEXPECTED_TRAILING_BYTES".to_owned(),
            "UNSUPPORTED_SCHEMA".to_owned(),
            "UNSUPPORTED_VARIANT".to_owned(),
            "REFERENCE_DISAGREEMENT".to_owned(),
            "DUPLICATE_SHAREHOLDER".to_owned(),
            "UNKNOWN_QUOTE_MINT_EVIDENCE".to_owned(),
            "UNKNOWN_DECIMAL_EVIDENCE".to_owned(),
        ],
        registry_rejection_rules: vec![
            "MISSING_SOURCE_IDENTITY".to_owned(),
            "SOURCE_IDENTITY_MISMATCH".to_owned(),
            "SOURCE_HASH_MISMATCH".to_owned(),
            "UNSUPPORTED_VERSION".to_owned(),
            "AMBIGUOUS_VERSION_MATCH".to_owned(),
        ],
    }
}

fn buy_account_requirements() -> Vec<AccountRequirement> {
    [
        ("global", false, false, None),
        ("fee_recipient", true, false, None),
        ("mint", false, false, None),
        ("bonding_curve", true, false, None),
        ("associated_bonding_curve", true, false, None),
        ("associated_user", true, false, None),
        ("user", true, true, None),
        (
            "system_program",
            false,
            false,
            Some("11111111111111111111111111111111"),
        ),
        ("token_program", false, false, None),
        ("creator_vault", true, false, None),
        ("event_authority", false, false, None),
        ("program", false, false, Some(PUMP_PROGRAM_ID)),
        ("global_volume_accumulator", false, false, None),
        ("user_volume_accumulator", true, false, None),
        ("fee_config", false, false, None),
        (
            "fee_program",
            false,
            false,
            Some("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"),
        ),
    ]
    .into_iter()
    .enumerate()
    .map(
        |(index, (name, writable, signer, fixed_address))| AccountRequirement {
            index: u8::try_from(index).expect("buy account index is bounded"),
            name: name.to_owned(),
            writable,
            signer,
            fixed_address: fixed_address.map(str::to_owned),
        },
    )
    .collect()
}
