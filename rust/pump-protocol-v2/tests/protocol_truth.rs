use std::path::{Path, PathBuf};

use pump_protocol_v2::BuyInstruction;
use pump_protocol_v2::decode::{QuarantineReason, decode_buy_instruction, decode_trade_event_cpi};
use pump_protocol_v2::evidence::{
    EVIDENCE_JSON_PATH, EVIDENCE_MARKDOWN_PATH, VECTOR_MANIFEST_PATH, generated_evidence_json,
    generated_evidence_markdown, generated_vector_manifest_json,
};
use pump_protocol_v2::registry::{
    ActivationStatus, EvidenceStatus, ExactPubkey, RegistryError, SOURCE_MANIFEST_ID,
    SourceManifest, ValidatedProtocolCandidate, canonical_registry_entry,
    resolve_registry_candidate, validate_source_manifest,
};
use pump_protocol_v2::{require_economic_identity, validate_reference_agreement};
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

const BUY_VECTOR_HEX: &str = "66063d1201daebea80841e000000000040420f000000000001";

fn repository_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("crate is located under rust/")
        .to_path_buf()
}

fn read(path: &str) -> String {
    std::fs::read_to_string(repository_root().join(path)).expect("fixture must be readable")
}

fn source_manifest() -> SourceManifest {
    serde_json::from_str(&read(
        "schemas/protocol/pump/pump-public-docs-9c82f61-source-manifest.json",
    ))
    .expect("source manifest schema")
}

fn validated_candidate() -> ValidatedProtocolCandidate {
    let manifest = source_manifest();
    resolve_registry_candidate(Some(&manifest), 1).expect("exact source-bound candidate")
}

fn trade_vector_bytes() -> Vec<u8> {
    let source = read("tests/fixtures/pump-silver/event-vectors.json");
    assert_eq!(
        hex::encode(Sha256::digest(source.as_bytes())),
        "1d98b6fa69fc5c482389c37518709a55e7512ab0599f533682def581b6dbf4f9"
    );
    let fixture: Value =
        serde_json::from_str(&source).expect("retained event fixture is valid JSON");
    let vector = fixture["vectors"]
        .as_array()
        .expect("vectors array")
        .iter()
        .find(|value| value["name"] == "trade-buy-current-official")
        .expect("bounded trade fixture");
    hex::decode(vector["dataHex"].as_str().expect("fixture dataHex")).expect("fixture hex")
}

fn reason<T>(result: Result<T, pump_protocol_v2::Quarantine>) -> QuarantineReason {
    match result {
        Ok(_) => panic!("case must quarantine"),
        Err(quarantine) => quarantine.reason,
    }
}

#[test]
fn validates_the_exact_official_source_manifest_and_rejects_hash_drift() {
    let manifest = source_manifest();
    validate_source_manifest(&manifest).expect("exact manifest must validate");

    let mut drifted = manifest;
    drifted.sha256 = "00".repeat(32);
    assert_eq!(
        validate_source_manifest(&drifted),
        Err(RegistryError::SourceHashMismatch)
    );

    let mut identity_drifted = source_manifest();
    "idl/other.json".clone_into(&mut identity_drifted.path);
    assert_eq!(
        validate_source_manifest(&identity_drifted),
        Err(RegistryError::SourceIdentityMismatch)
    );
}

#[test]
fn registry_is_exact_and_never_claims_historical_activation() {
    let registry = canonical_registry_entry();
    assert_eq!(
        registry.registry_entry_id,
        "pump-mainnet-legacy-sol-buy-trade-9c82f61-v1"
    );
    assert_eq!(
        registry.program_id,
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    );
    assert_eq!(registry.instruction_discriminator_hex, "66063d1201daebea");
    assert_eq!(registry.event_discriminator_hex, "bddb7fd34ee661ee");
    assert_eq!(registry.instruction_accounts.len(), 16);
    assert_eq!(registry.instruction_accounts[0].name, "global");
    assert_eq!(registry.instruction_accounts[6].name, "user");
    assert!(registry.instruction_accounts[6].signer);
    assert_eq!(registry.instruction_accounts[15].name, "fee_program");
    assert_eq!(
        registry.activation_status,
        ActivationStatus::StructuralCandidate
    );
    assert_eq!(registry.evidence_status, EvidenceStatus::FixtureCompatible);
    assert_eq!(registry.historical_slot_range, None);
    assert_eq!(registry.quote_mint.value, None);
    assert_eq!(registry.quote_decimals.value, None);
    assert_eq!(registry.base_decimals.value, None);
    assert_eq!(
        registry.registry_rejection_rules,
        [
            "MISSING_SOURCE_IDENTITY",
            "SOURCE_IDENTITY_MISMATCH",
            "SOURCE_HASH_MISMATCH",
            "UNSUPPORTED_VERSION",
            "AMBIGUOUS_VERSION_MATCH",
        ]
    );
    assert_ne!(
        registry.activation_status,
        ActivationStatus::ProvenAtSlotRange
    );
}

#[test]
fn exact_pubkey_evidence_is_fixed_width_lowercase_hex() {
    let identity = ExactPubkey::from_bytes([0xab; 32]);
    assert_eq!(identity.as_bytes(), &[0xab; 32]);
    assert_eq!(
        serde_json::to_string(&identity).expect("exact pubkey serialization"),
        format!("\"{}\"", "ab".repeat(32))
    );
}

#[test]
fn discriminator_derivation_matches_anchor_namespaces() {
    assert_eq!(
        &Sha256::digest(b"global:buy")[..8],
        &[102, 6, 61, 18, 1, 218, 235, 234]
    );
    assert_eq!(
        &Sha256::digest(b"event:TradeEvent")[..8],
        &[189, 219, 127, 211, 78, 230, 97, 238]
    );
    let mut event_tag = Sha256::digest(b"anchor:event")[..8].to_vec();
    event_tag.reverse();
    assert_eq!(event_tag, [228, 69, 165, 46, 81, 203, 154, 29]);
}

#[test]
fn resolves_one_source_bound_candidate_and_quarantines_missing_or_ambiguous_identity() {
    let manifest = source_manifest();
    assert!(resolve_registry_candidate(Some(&manifest), 1).is_ok());
    assert_eq!(
        resolve_registry_candidate(None, 1),
        Err(RegistryError::MissingSourceIdentity)
    );
    assert_eq!(
        resolve_registry_candidate(Some(&manifest), 0),
        Err(RegistryError::UnsupportedVersion)
    );
    assert_eq!(
        resolve_registry_candidate(Some(&manifest), 2),
        Err(RegistryError::AmbiguousVersionMatch)
    );
    let mut hash_drift = manifest.clone();
    hash_drift.sha256 = "00".repeat(32);
    assert_eq!(
        resolve_registry_candidate(Some(&hash_drift), 1),
        Err(RegistryError::SourceHashMismatch)
    );
}

#[test]
fn decodes_the_exact_buy_instruction_and_preserves_raw_u64_boundaries() {
    let bytes = hex::decode(BUY_VECTOR_HEX).expect("valid hex");
    assert_eq!(
        hex::encode(Sha256::digest(&bytes)),
        "c4c16e3627555b799443dc7d2eaad89b3a5d55217b1ce0da55fcef2ae84647ad"
    );
    let candidate = validated_candidate();
    let decoded = decode_buy_instruction(&candidate, &bytes).expect("selected buy vector");
    assert_eq!(decoded.amount, 2_000_000);
    assert_eq!(decoded.max_sol_cost, 1_000_000);
    assert!(decoded.track_volume);

    let mut boundary = hex::decode("66063d1201daebea").expect("discriminator");
    boundary.extend_from_slice(&u64::MAX.to_le_bytes());
    boundary.extend_from_slice(&0_u64.to_le_bytes());
    boundary.push(0);
    let decoded = decode_buy_instruction(&candidate, &boundary).expect("integer boundary");
    assert_eq!(decoded.amount, u64::MAX);
    assert_eq!(decoded.max_sol_cost, 0);
    assert!(!decoded.track_volume);
}

#[test]
fn decodes_the_retained_trade_event_as_exact_integer_fixture_evidence() {
    let bytes = trade_vector_bytes();
    assert_eq!(bytes.len(), 400);
    assert_eq!(
        hex::encode(Sha256::digest(&bytes)),
        "9c0bfb4a8374b259bdbf22820dd174c156d2630f408d18cb51163d2ff8cb7c06"
    );
    let decoded =
        decode_trade_event_cpi(&validated_candidate(), &bytes).expect("bounded trade event");
    assert_eq!(decoded.mint, [0x1f; 32]);
    assert_eq!(decoded.sol_amount, 1_000_000);
    assert_eq!(decoded.token_amount, 2_000_000);
    assert!(decoded.is_buy);
    assert_eq!(decoded.user, [0x20; 32]);
    assert_eq!(decoded.timestamp, 1_725_000_000);
    assert_eq!(decoded.virtual_sol_reserves, 31_000_000_000);
    assert_eq!(decoded.virtual_token_reserves, 1_071_000_000_000_000);
    assert_eq!(decoded.real_sol_reserves, 1_000_000_000);
    assert_eq!(decoded.real_token_reserves, 791_100_000_000_000);
    assert_eq!(decoded.fee_recipient, [0x21; 32]);
    assert_eq!(decoded.fee_basis_points, 100);
    assert_eq!(decoded.fee, 10_000);
    assert_eq!(decoded.creator, [0x22; 32]);
    assert_eq!(decoded.creator_fee_basis_points, 50);
    assert_eq!(decoded.creator_fee, 5_000);
    assert!(decoded.track_volume);
    assert_eq!(decoded.total_unclaimed_tokens, 9);
    assert_eq!(decoded.total_claimed_tokens, 7);
    assert_eq!(decoded.current_sol_volume, 1_000_000);
    assert_eq!(decoded.last_update_timestamp, 1_725_000_001);
    assert_eq!(decoded.ix_name, "buy");
    assert!(!decoded.mayhem_mode);
    assert_eq!(decoded.cashback_fee_basis_points, 0);
    assert_eq!(decoded.cashback, 0);
    assert_eq!(decoded.buyback_fee_basis_points, 0);
    assert_eq!(decoded.buyback_fee, 0);
    assert_eq!(decoded.shareholders.len(), 1);
    assert_eq!(decoded.shareholders[0].address, [0x22; 32]);
    assert_eq!(decoded.shareholders[0].share_bps, 10_000);
    assert_eq!(
        hex::encode(decoded.quote_mint),
        "069b8857feab8184fb687f634618c035dac439dc1aeb3b5598a0f00000000001"
    );
    assert_eq!(decoded.quote_amount, 1_000_000);
    assert_eq!(decoded.virtual_quote_reserves, 31_000_000_000);
    assert_eq!(decoded.real_quote_reserves, 1_000_000_000);
}

#[test]
fn buy_discriminator_bounds_schema_and_trailing_bytes_fail_closed() {
    let candidate = validated_candidate();
    let valid = hex::decode(BUY_VECTOR_HEX).expect("valid hex");
    assert_eq!(
        reason(decode_buy_instruction(&candidate, &valid[..7])),
        QuarantineReason::TruncatedDiscriminator
    );

    let mut wrong = valid.clone();
    wrong[0] ^= 0xff;
    assert_eq!(
        reason(decode_buy_instruction(&candidate, &wrong)),
        QuarantineReason::WrongDiscriminator
    );
    assert_eq!(
        reason(decode_buy_instruction(&candidate, &valid[..24])),
        QuarantineReason::TruncatedPayload
    );

    let mut trailing = valid.clone();
    trailing.push(0);
    assert_eq!(
        reason(decode_buy_instruction(&candidate, &trailing)),
        QuarantineReason::UnexpectedTrailingBytes
    );

    let mut invalid_bool = valid;
    invalid_bool[24] = 2;
    assert_eq!(
        reason(decode_buy_instruction(&candidate, &invalid_bool)),
        QuarantineReason::UnsupportedSchema
    );
}

#[test]
fn trade_event_bounds_and_unsupported_variants_fail_closed() {
    let candidate = validated_candidate();
    let valid = trade_vector_bytes();
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &valid[..7])),
        QuarantineReason::TruncatedDiscriminator
    );

    let mut wrong_envelope = valid.clone();
    wrong_envelope[0] ^= 1;
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &wrong_envelope)),
        QuarantineReason::WrongDiscriminator
    );

    let mut wrong_event = valid.clone();
    wrong_event[8] ^= 1;
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &wrong_event)),
        QuarantineReason::WrongDiscriminator
    );

    assert_eq!(
        reason(decode_trade_event_cpi(
            &candidate,
            &valid[..valid.len() - 1],
        )),
        QuarantineReason::TruncatedPayload
    );

    let mut trailing = valid.clone();
    trailing.push(0);
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &trailing)),
        QuarantineReason::UnexpectedTrailingBytes
    );

    let mut oversized = valid.clone();
    oversized.resize(4_097, 0);
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &oversized)),
        QuarantineReason::UnsupportedSchema
    );

    let marker = [3_u8, 0, 0, 0, b'b', b'u', b'y'];
    let offset = valid
        .windows(marker.len())
        .position(|window| window == marker)
        .expect("ix_name marker");
    let mut overlong_ix_name = valid.clone();
    overlong_ix_name[offset..offset + 4].copy_from_slice(&33_u32.to_le_bytes());
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &overlong_ix_name)),
        QuarantineReason::UnsupportedSchema
    );

    let mut shareholder_marker = vec![1_u8, 0, 0, 0];
    shareholder_marker.extend_from_slice(&[0x22; 32]);
    shareholder_marker.extend_from_slice(&10_000_u16.to_le_bytes());
    let shareholder_offset = valid
        .windows(shareholder_marker.len())
        .position(|window| window == shareholder_marker)
        .expect("shareholder marker");
    let mut too_many_shareholders = valid.clone();
    too_many_shareholders[shareholder_offset..shareholder_offset + 4]
        .copy_from_slice(&129_u32.to_le_bytes());
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &too_many_shareholders)),
        QuarantineReason::UnsupportedSchema
    );

    let mut unsupported = valid;
    unsupported[offset + 4] = b's';
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &unsupported)),
        QuarantineReason::UnsupportedVariant
    );
}

#[test]
fn structural_reference_must_agree_and_unknown_economics_cannot_be_promoted() {
    let primary = decode_buy_instruction(
        &validated_candidate(),
        &hex::decode(BUY_VECTOR_HEX).expect("valid hex"),
    )
    .expect("selected buy vector");
    let structural_reference = BuyInstruction {
        amount: 2_000_000,
        max_sol_cost: 1_000_000,
        track_volume: true,
    };
    validate_reference_agreement(&primary, &structural_reference)
        .expect("sealed expected-value reference");
    let mut disagreement = primary.clone();
    disagreement.amount += 1;
    assert_eq!(
        reason(validate_reference_agreement(&primary, &disagreement)),
        QuarantineReason::ReferenceDisagreement
    );

    let mut registry = canonical_registry_entry();
    assert_eq!(
        reason(require_economic_identity(&registry)),
        QuarantineReason::UnknownQuoteMintEvidence
    );
    registry.quote_asset_kind.status = EvidenceStatus::ObservedCompatible;
    registry.quote_mint.value = Some(ExactPubkey::from_bytes([0x42; 32]));
    registry.quote_mint.status = EvidenceStatus::Disputed;
    assert_eq!(
        reason(require_economic_identity(&registry)),
        QuarantineReason::UnknownQuoteMintEvidence
    );
    registry.quote_mint.status = EvidenceStatus::ObservedCompatible;
    assert_eq!(
        reason(require_economic_identity(&registry)),
        QuarantineReason::UnknownDecimalEvidence
    );
    registry.quote_decimals.value = Some("09".to_owned());
    registry.base_decimals.value = Some("6".to_owned());
    registry.quote_decimals.status = EvidenceStatus::ObservedCompatible;
    registry.base_decimals.status = EvidenceStatus::ObservedCompatible;
    assert_eq!(
        reason(require_economic_identity(&registry)),
        QuarantineReason::UnknownDecimalEvidence
    );
    registry.quote_decimals.value = Some("9".to_owned());
    require_economic_identity(&registry)
        .expect("synthetic typed identity exercises the generic promotion gate only");
}

#[test]
fn duplicate_shareholder_fixture_invariant_fails_closed() {
    let candidate = validated_candidate();
    let mut bytes = trade_vector_bytes();
    let mut marker = vec![1_u8, 0, 0, 0];
    marker.extend_from_slice(&[0x22; 32]);
    marker.extend_from_slice(&10_000_u16.to_le_bytes());
    let offset = bytes
        .windows(marker.len())
        .position(|window| window == marker)
        .expect("shareholder vector marker");
    bytes[offset..offset + 4].copy_from_slice(&2_u32.to_le_bytes());
    let duplicate = bytes[offset + 4..offset + 38].to_vec();
    bytes.splice(offset + 38..offset + 38, duplicate);
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &bytes)),
        QuarantineReason::DuplicateShareholder
    );

    let mut truncated_duplicate = trade_vector_bytes();
    truncated_duplicate[offset..offset + 4].copy_from_slice(&2_u32.to_le_bytes());
    truncated_duplicate.truncate(offset + 38);
    truncated_duplicate.extend_from_slice(&[0x22; 32]);
    assert_eq!(
        reason(decode_trade_event_cpi(&candidate, &truncated_duplicate)),
        QuarantineReason::DuplicateShareholder
    );
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct VectorManifest {
    schema_version: String,
    candidate_id: String,
    source_manifest_id: String,
    accepted_vectors: Vec<Value>,
    quarantine_vectors: Vec<FailureVectorRecord>,
    registry_rejection_vectors: Vec<FailureVectorRecord>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct FailureVectorRecord {
    name: String,
    kind: String,
    origin: String,
    evidence: String,
    source_manifest_id: String,
    input_or_mutation: String,
    expected_outcome: String,
}

fn serialized_reason(reason: QuarantineReason) -> String {
    serde_json::to_value(reason)
        .expect("reason serialization")
        .as_str()
        .expect("reason string")
        .to_owned()
}

fn registry_error_name(error: &RegistryError) -> &'static str {
    match error {
        RegistryError::MissingSourceIdentity => "MISSING_SOURCE_IDENTITY",
        RegistryError::SourceIdentityMismatch => "SOURCE_IDENTITY_MISMATCH",
        RegistryError::SourceHashMismatch => "SOURCE_HASH_MISMATCH",
        RegistryError::AmbiguousVersionMatch => "AMBIGUOUS_VERSION_MATCH",
        RegistryError::UnsupportedVersion => "UNSUPPORTED_VERSION",
    }
}

type ObservedOutcomes = Vec<(&'static str, String)>;

#[allow(clippy::too_many_lines)]
fn executed_failure_outcomes() -> (ObservedOutcomes, ObservedOutcomes) {
    let candidate = validated_candidate();
    let buy = hex::decode(BUY_VECTOR_HEX).expect("buy vector");
    let trade = trade_vector_bytes();
    let mut quarantines = Vec::new();
    let mut record = |name, observed_reason| {
        quarantines.push((name, serialized_reason(observed_reason)));
    };

    record(
        "buy-truncated-discriminator",
        reason(decode_buy_instruction(&candidate, &buy[..7])),
    );
    let mut wrong = buy.clone();
    wrong[0] ^= 0xff;
    record(
        "buy-wrong-discriminator",
        reason(decode_buy_instruction(&candidate, &wrong)),
    );
    record(
        "buy-truncated-payload",
        reason(decode_buy_instruction(&candidate, &buy[..24])),
    );
    let mut trailing = buy.clone();
    trailing.push(0);
    record(
        "buy-trailing-payload",
        reason(decode_buy_instruction(&candidate, &trailing)),
    );
    let mut invalid_bool = buy.clone();
    invalid_bool[24] = 2;
    record(
        "buy-invalid-bool",
        reason(decode_buy_instruction(&candidate, &invalid_bool)),
    );

    let marker = [3_u8, 0, 0, 0, b'b', b'u', b'y'];
    let ix_offset = trade
        .windows(marker.len())
        .position(|window| window == marker)
        .expect("ix_name marker");
    let mut unsupported = trade.clone();
    unsupported[ix_offset + 4] = b's';
    record(
        "trade-unsupported-variant",
        reason(decode_trade_event_cpi(&candidate, &unsupported)),
    );

    let primary = decode_buy_instruction(&candidate, &buy).expect("accepted buy");
    let mut disagreed = primary.clone();
    disagreed.amount += 1;
    record(
        "buy-reference-disagreement",
        reason(validate_reference_agreement(&primary, &disagreed)),
    );

    let mut shareholder_marker = vec![1_u8, 0, 0, 0];
    shareholder_marker.extend_from_slice(&[0x22; 32]);
    shareholder_marker.extend_from_slice(&10_000_u16.to_le_bytes());
    let shareholder_offset = trade
        .windows(shareholder_marker.len())
        .position(|window| window == shareholder_marker)
        .expect("shareholder marker");
    let mut duplicate = trade;
    duplicate[shareholder_offset..shareholder_offset + 4].copy_from_slice(&2_u32.to_le_bytes());
    let shareholder = duplicate[shareholder_offset + 4..shareholder_offset + 38].to_vec();
    duplicate.splice(
        shareholder_offset + 38..shareholder_offset + 38,
        shareholder,
    );
    record(
        "trade-duplicate-shareholder",
        reason(decode_trade_event_cpi(&candidate, &duplicate)),
    );

    let registry = canonical_registry_entry();
    record(
        "economics-unknown-quote-mint",
        reason(require_economic_identity(&registry)),
    );
    let mut decimal_unknown = registry;
    decimal_unknown.quote_asset_kind.status = EvidenceStatus::ObservedCompatible;
    decimal_unknown.quote_mint.value = Some(ExactPubkey::from_bytes([0x42; 32]));
    decimal_unknown.quote_mint.status = EvidenceStatus::ObservedCompatible;
    record(
        "economics-unknown-decimals",
        reason(require_economic_identity(&decimal_unknown)),
    );

    let manifest = source_manifest();
    let missing = resolve_registry_candidate(None, 1).expect_err("missing source must reject");
    let mut hash_drift = manifest.clone();
    hash_drift.sha256 = "00".repeat(32);
    let hash = resolve_registry_candidate(Some(&hash_drift), 1)
        .expect_err("source hash drift must reject");
    let mut identity_drift = manifest.clone();
    "idl/other.json".clone_into(&mut identity_drift.path);
    let identity = resolve_registry_candidate(Some(&identity_drift), 1)
        .expect_err("source identity drift must reject");
    let unsupported = resolve_registry_candidate(Some(&manifest), 0)
        .expect_err("zero compatible candidates must reject");
    let ambiguous =
        resolve_registry_candidate(Some(&manifest), 2).expect_err("ambiguity must reject");
    let registry_rejections = vec![
        (
            "registry-missing-source",
            registry_error_name(&missing).to_owned(),
        ),
        (
            "registry-source-identity-mismatch",
            registry_error_name(&identity).to_owned(),
        ),
        (
            "registry-source-hash-mismatch",
            registry_error_name(&hash).to_owned(),
        ),
        (
            "registry-unsupported-version",
            registry_error_name(&unsupported).to_owned(),
        ),
        (
            "registry-ambiguous-match",
            registry_error_name(&ambiguous).to_owned(),
        ),
    ];
    (quarantines, registry_rejections)
}

#[test]
fn vector_manifest_and_generated_evidence_are_deterministic_and_committed() {
    let manifest: VectorManifest = serde_json::from_str(
        &std::fs::read_to_string(repository_root().join(VECTOR_MANIFEST_PATH))
            .expect("vector manifest read"),
    )
    .expect("vector manifest schema");
    assert_eq!(manifest.schema_version, "PUMP_PROTOCOL_V2_VECTOR_SET_V1");
    assert_eq!(
        manifest.candidate_id,
        "pump-mainnet-legacy-sol-buy-trade-9c82f61-v1"
    );
    assert_eq!(manifest.source_manifest_id, SOURCE_MANIFEST_ID);
    assert_eq!(manifest.accepted_vectors.len(), 2);
    assert_eq!(manifest.quarantine_vectors.len(), 10);
    assert_eq!(manifest.registry_rejection_vectors.len(), 5);
    for vector in manifest
        .quarantine_vectors
        .iter()
        .chain(&manifest.registry_rejection_vectors)
    {
        assert!(!vector.kind.is_empty());
        assert!(!vector.origin.is_empty());
        assert!(!vector.evidence.is_empty());
        assert_eq!(vector.source_manifest_id, SOURCE_MANIFEST_ID);
        assert!(!vector.input_or_mutation.is_empty());
    }
    for vector in &manifest.accepted_vectors {
        assert!(vector["rawSha256"].as_str().is_some());
        let expected = vector["expected"]
            .as_object()
            .expect("accepted expected map");
        for (name, value) in expected {
            if name != "trackVolume" && name != "isBuy" && name != "ixName" {
                assert!(
                    value.is_string(),
                    "{name} must remain an exact decimal string"
                );
            }
        }
    }

    let expected_quarantines = manifest
        .quarantine_vectors
        .iter()
        .map(|vector| (vector.name.as_str(), vector.expected_outcome.clone()))
        .collect::<Vec<_>>();
    let expected_registry = manifest
        .registry_rejection_vectors
        .iter()
        .map(|vector| (vector.name.as_str(), vector.expected_outcome.clone()))
        .collect::<Vec<_>>();
    let (observed_quarantines, observed_registry) = executed_failure_outcomes();
    assert_eq!(observed_quarantines, expected_quarantines);
    assert_eq!(observed_registry, expected_registry);

    assert_eq!(read(VECTOR_MANIFEST_PATH), generated_vector_manifest_json());
    assert_eq!(read(EVIDENCE_JSON_PATH), generated_evidence_json());
    assert_eq!(read(EVIDENCE_MARKDOWN_PATH), generated_evidence_markdown());
    assert_eq!(generated_evidence_json(), generated_evidence_json());
    assert_eq!(generated_evidence_markdown(), generated_evidence_markdown());
}
