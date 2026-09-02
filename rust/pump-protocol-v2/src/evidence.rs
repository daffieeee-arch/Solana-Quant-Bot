use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};

use crate::registry::{
    PUMP_PROGRAM_ID, SOURCE_BLOB, SOURCE_COMMIT, SOURCE_MANIFEST_ID, SOURCE_PATH, SOURCE_SHA256,
    SOURCE_URL, canonical_registry_entry,
};

pub const EVIDENCE_JSON_PATH: &str = "schemas/protocol/pump/legacy-sol-buy-trade-v1-evidence.json";
pub const EVIDENCE_MARKDOWN_PATH: &str = "docs/research/PUMP_PROTOCOL_EVIDENCE_MATRIX_V2.md";
pub const SOURCE_MANIFEST_PATH: &str =
    "schemas/protocol/pump/pump-public-docs-9c82f61-source-manifest.json";
pub const VECTOR_MANIFEST_PATH: &str =
    "rust/pump-protocol-v2/tests/fixtures/legacy-sol-buy-trade.json";
const EVENT_RAW_FIELDS: [&str; 32] = [
    "mint:pubkey",
    "sol_amount:u64",
    "token_amount:u64",
    "is_buy:bool",
    "user:pubkey",
    "timestamp:i64",
    "virtual_sol_reserves:u64",
    "virtual_token_reserves:u64",
    "real_sol_reserves:u64",
    "real_token_reserves:u64",
    "fee_recipient:pubkey",
    "fee_basis_points:u64",
    "fee:u64",
    "creator:pubkey",
    "creator_fee_basis_points:u64",
    "creator_fee:u64",
    "track_volume:bool",
    "total_unclaimed_tokens:u64",
    "total_claimed_tokens:u64",
    "current_sol_volume:u64",
    "last_update_timestamp:i64",
    "ix_name:string",
    "mayhem_mode:bool",
    "cashback_fee_basis_points:u64",
    "cashback:u64",
    "buyback_fee_basis_points:u64",
    "buyback_fee:u64",
    "shareholders:vec<Shareholder>",
    "quote_mint:pubkey",
    "quote_amount:u64",
    "virtual_quote_reserves:u64",
    "real_quote_reserves:u64",
];

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Matrix {
    schema_version: &'static str,
    generated_by: &'static str,
    source: SourceRow,
    registry: crate::registry::ProtocolRegistryEntry,
    selected_surface: SelectedSurface,
    vectors: VectorSummary,
    implementation_lanes: ImplementationLanes,
    compatibility: Compatibility,
    unresolved_questions: [&'static str; 7],
    prohibited_claims: [&'static str; 5],
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SourceRow {
    repository: &'static str,
    commit: &'static str,
    committed_at: &'static str,
    path: &'static str,
    git_blob_sha1: &'static str,
    sha256: &'static str,
    retrieved_at: &'static str,
    official_url: &'static str,
    license_status: &'static str,
    supporting_readme_sha256: &'static str,
    source_manifest_path: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SelectedSurface {
    instruction: &'static str,
    instruction_discriminator_hex: &'static str,
    instruction_raw_fields: [&'static str; 3],
    event: &'static str,
    event_discriminator_hex: &'static str,
    event_cpi_envelope_hex: &'static str,
    event_raw_fields: [&'static str; 32],
    unit_policy: &'static str,
    reserve_policy: &'static str,
    association_policy: &'static str,
    implementation_safety_bounds: [&'static str; 4],
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VectorSummary {
    manifest_path: &'static str,
    manifest_sha256: String,
    accepted: usize,
    accepted_by_kind: [CountRow; 2],
    accepted_vectors: [VectorRow; 2],
    typed_quarantines: usize,
    typed_quarantines_by_reason: Vec<CountRow>,
    registry_rejections: usize,
    registry_rejections_by_reason: Vec<CountRow>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct VectorRow {
    name: &'static str,
    kind: &'static str,
    origin: &'static str,
    raw_sha256: &'static str,
    evidence: &'static str,
}

#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
struct CountRow {
    key: &'static str,
    count: u8,
}

#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
struct FailureVector {
    name: &'static str,
    kind: &'static str,
    origin: &'static str,
    evidence: &'static str,
    source_manifest_id: &'static str,
    input_or_mutation: &'static str,
    expected_outcome: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ImplementationLanes {
    primary: Lane,
    structural_reference: Lane,
    independent_protocol_authority: Lane,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Lane {
    status: &'static str,
    implementation: &'static str,
    limitation: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Compatibility {
    structural: &'static str,
    fixture: &'static str,
    observed: &'static str,
    historical_activation: &'static str,
    research_readiness: &'static str,
}

fn quarantine_vectors() -> [FailureVector; 10] {
    [
        FailureVector {
            name: "buy-truncated-discriminator",
            kind: "BUY_INSTRUCTION",
            origin: "SYNTHETIC_ADVERSARIAL_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "take first 7 bytes of accepted buy vector",
            expected_outcome: "TRUNCATED_DISCRIMINATOR",
        },
        FailureVector {
            name: "buy-wrong-discriminator",
            kind: "BUY_INSTRUCTION",
            origin: "SYNTHETIC_ADVERSARIAL_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "xor accepted buy byte 0 with 0xff",
            expected_outcome: "WRONG_DISCRIMINATOR",
        },
        FailureVector {
            name: "buy-truncated-payload",
            kind: "BUY_INSTRUCTION",
            origin: "SYNTHETIC_ADVERSARIAL_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "take first 24 bytes of accepted 25-byte buy vector",
            expected_outcome: "TRUNCATED_PAYLOAD",
        },
        FailureVector {
            name: "buy-trailing-payload",
            kind: "BUY_INSTRUCTION",
            origin: "SYNTHETIC_ADVERSARIAL_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "append one zero byte to accepted buy vector",
            expected_outcome: "UNEXPECTED_TRAILING_BYTES",
        },
        FailureVector {
            name: "buy-invalid-bool",
            kind: "BUY_INSTRUCTION",
            origin: "SYNTHETIC_ADVERSARIAL_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "replace accepted buy byte 24 with 0x02",
            expected_outcome: "UNSUPPORTED_SCHEMA",
        },
        FailureVector {
            name: "trade-unsupported-variant",
            kind: "TRADE_EVENT_CPI",
            origin: "RETAINED_SYNTHETIC_FIXTURE_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "replace retained TradeEvent ix_name buy with suy",
            expected_outcome: "UNSUPPORTED_VARIANT",
        },
        FailureVector {
            name: "buy-reference-disagreement",
            kind: "REFERENCE_COMPARISON",
            origin: "SYNTHETIC_EXPECTED_VALUE_MUTATION",
            evidence: "STRUCTURAL_REFERENCE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "increment decoded buy amount in structural reference by one",
            expected_outcome: "REFERENCE_DISAGREEMENT",
        },
        FailureVector {
            name: "trade-duplicate-shareholder",
            kind: "TRADE_EVENT_CPI",
            origin: "RETAINED_SYNTHETIC_FIXTURE_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "set shareholder count to two and repeat first address",
            expected_outcome: "DUPLICATE_SHAREHOLDER",
        },
        FailureVector {
            name: "economics-unknown-quote-mint",
            kind: "ECONOMIC_PROMOTION_GATE",
            origin: "CANONICAL_REGISTRY_CANDIDATE",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "canonical candidate has no observed exact quote mint",
            expected_outcome: "UNKNOWN_QUOTE_MINT_EVIDENCE",
        },
        FailureVector {
            name: "economics-unknown-decimals",
            kind: "ECONOMIC_PROMOTION_GATE",
            origin: "SYNTHETIC_REGISTRY_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "mark quote asset and mint observed while both decimals remain unknown",
            expected_outcome: "UNKNOWN_DECIMAL_EVIDENCE",
        },
    ]
}

fn registry_rejection_vectors() -> [FailureVector; 5] {
    [
        FailureVector {
            name: "registry-missing-source",
            kind: "REGISTRY_SELECTION",
            origin: "SYNTHETIC_SOURCE_IDENTITY_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "omit source manifest before candidate selection",
            expected_outcome: "MISSING_SOURCE_IDENTITY",
        },
        FailureVector {
            name: "registry-source-identity-mismatch",
            kind: "REGISTRY_SELECTION",
            origin: "SYNTHETIC_SOURCE_IDENTITY_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "replace pinned source path with idl/other.json",
            expected_outcome: "SOURCE_IDENTITY_MISMATCH",
        },
        FailureVector {
            name: "registry-source-hash-mismatch",
            kind: "REGISTRY_SELECTION",
            origin: "SYNTHETIC_SOURCE_IDENTITY_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "replace source manifest sha256 with 32 zero bytes",
            expected_outcome: "SOURCE_HASH_MISMATCH",
        },
        FailureVector {
            name: "registry-unsupported-version",
            kind: "REGISTRY_SELECTION",
            origin: "SYNTHETIC_CANDIDATE_COUNT_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "resolve exact source receipt against zero compatible candidates",
            expected_outcome: "UNSUPPORTED_VERSION",
        },
        FailureVector {
            name: "registry-ambiguous-match",
            kind: "REGISTRY_SELECTION",
            origin: "SYNTHETIC_CANDIDATE_COUNT_MUTATION",
            evidence: "STRUCTURAL_FIXTURE_ONLY",
            source_manifest_id: SOURCE_MANIFEST_ID,
            input_or_mutation: "resolve exact source receipt against two compatible candidates",
            expected_outcome: "AMBIGUOUS_VERSION_MATCH",
        },
    ]
}

#[must_use]
/// Render the sealed vector/rejection manifest used by tests and matrix counts.
///
/// # Panics
///
/// Panics only if serialization of compile-time-owned values fails.
pub fn generated_vector_manifest_json() -> String {
    let value = json!({
        "schemaVersion": "PUMP_PROTOCOL_V2_VECTOR_SET_V1",
        "candidateId": "pump-mainnet-legacy-sol-buy-trade-9c82f61-v1",
        "sourceManifestId": SOURCE_MANIFEST_ID,
        "acceptedVectors": [
            {
                "name": "buy-selected-schema-u64-values",
                "kind": "BUY_INSTRUCTION",
                "origin": "SYNTHETIC_FROM_PINNED_OFFICIAL_SCHEMA",
                "evidence": "STRUCTURAL_FIXTURE_ONLY",
                "dataHex": "66063d1201daebea80841e000000000040420f000000000001",
                "rawSha256": "c4c16e3627555b799443dc7d2eaad89b3a5d55217b1ce0da55fcef2ae84647ad",
                "expected": {
                    "amount": "2000000",
                    "maxSolCost": "1000000",
                    "trackVolume": true
                }
            },
            {
                "name": "trade-buy-current-official",
                "kind": "TRADE_EVENT_CPI",
                "origin": "RETAINED_SYNTHETIC_FIXTURE_REFERENCE",
                "evidence": "STRUCTURAL_FIXTURE_ONLY",
                "sourcePath": "tests/fixtures/pump-silver/event-vectors.json",
                "sourceSha256": "1d98b6fa69fc5c482389c37518709a55e7512ab0599f533682def581b6dbf4f9",
                "sourceVectorName": "trade-buy-current-official",
                "rawSha256": "9c0bfb4a8374b259bdbf22820dd174c156d2630f408d18cb51163d2ff8cb7c06",
                "expected": {
                    "solAmount": "1000000",
                    "tokenAmount": "2000000",
                    "isBuy": true,
                    "timestamp": "1725000000",
                    "virtualSolReserves": "31000000000",
                    "virtualTokenReserves": "1071000000000000",
                    "realSolReserves": "1000000000",
                    "realTokenReserves": "791100000000000",
                    "ixName": "buy",
                    "quoteAmount": "1000000"
                }
            }
        ],
        "quarantineVectors": quarantine_vectors(),
        "registryRejectionVectors": registry_rejection_vectors()
    });
    let mut output = serde_json::to_string_pretty(&value).expect("vector serialization is static");
    output.push('\n');
    output
}

#[allow(clippy::too_many_lines)]
fn matrix() -> Matrix {
    let vector_manifest = generated_vector_manifest_json();
    let quarantines = quarantine_vectors();
    let registry_rejections = registry_rejection_vectors();
    Matrix {
        schema_version: "PUMP_PROTOCOL_EVIDENCE_MATRIX_V1",
        generated_by: "pump-protocol-v2/0.1.0",
        source: SourceRow {
            repository: "https://github.com/pump-fun/pump-public-docs",
            commit: SOURCE_COMMIT,
            committed_at: "2026-07-15T18:22:27Z",
            path: SOURCE_PATH,
            git_blob_sha1: SOURCE_BLOB,
            sha256: SOURCE_SHA256,
            retrieved_at: "2026-09-02T21:09:20Z",
            official_url: SOURCE_URL,
            license_status: "NO_LICENSE_FILE_AT_PIN",
            supporting_readme_sha256: crate::registry::README_SHA256,
            source_manifest_path: SOURCE_MANIFEST_PATH,
        },
        registry: canonical_registry_entry(),
        selected_surface: SelectedSurface {
            instruction: "buy",
            instruction_discriminator_hex: "66063d1201daebea",
            instruction_raw_fields: ["amount:u64", "max_sol_cost:u64", "track_volume:bool"],
            event: "TradeEvent[is_buy=true,ix_name=buy]",
            event_discriminator_hex: "bddb7fd34ee661ee",
            event_cpi_envelope_hex: "e445a52e51cb9a1d",
            event_raw_fields: EVENT_RAW_FIELDS,
            unit_policy: "RAW_INTEGER_ONLY",
            reserve_policy: "EVENT_REPORTED_RESERVES_NOT_ACCOUNT_STATE",
            association_policy: "DATA_ONLY_NO_TRANSACTION_ACCOUNT_OR_PROGRAM_ASSOCIATION_VALIDATION",
            implementation_safety_bounds: [
                "TradeEvent CPI bytes <= 4096",
                "ix_name UTF-8 bytes <= 32",
                "shareholder count <= 128 (the byte cap may bind first)",
                "shareholder addresses unique",
            ],
        },
        vectors: VectorSummary {
            manifest_path: VECTOR_MANIFEST_PATH,
            manifest_sha256: hex::encode(Sha256::digest(vector_manifest.as_bytes())),
            accepted: 2,
            accepted_by_kind: [
                CountRow {
                    key: "BUY_INSTRUCTION",
                    count: 1,
                },
                CountRow {
                    key: "TRADE_EVENT_CPI",
                    count: 1,
                },
            ],
            accepted_vectors: [
                VectorRow {
                    name: "buy-selected-schema-u64-values",
                    kind: "BUY_INSTRUCTION",
                    origin: "SYNTHETIC_FROM_PINNED_OFFICIAL_SCHEMA",
                    raw_sha256: "c4c16e3627555b799443dc7d2eaad89b3a5d55217b1ce0da55fcef2ae84647ad",
                    evidence: "STRUCTURAL_FIXTURE_ONLY",
                },
                VectorRow {
                    name: "trade-buy-current-official",
                    kind: "TRADE_EVENT_CPI",
                    origin: "RETAINED_SYNTHETIC_FIXTURE_REFERENCE:tests/fixtures/pump-silver/event-vectors.json@1d98b6fa69fc5c482389c37518709a55e7512ab0599f533682def581b6dbf4f9",
                    raw_sha256: "9c0bfb4a8374b259bdbf22820dd174c156d2630f408d18cb51163d2ff8cb7c06",
                    evidence: "STRUCTURAL_FIXTURE_ONLY",
                },
            ],
            typed_quarantines: quarantines.len(),
            typed_quarantines_by_reason: quarantines
                .iter()
                .map(|vector| CountRow {
                    key: vector.expected_outcome,
                    count: 1,
                })
                .collect(),
            registry_rejections: registry_rejections.len(),
            registry_rejections_by_reason: registry_rejections
                .iter()
                .map(|vector| CountRow {
                    key: vector.expected_outcome,
                    count: 1,
                })
                .collect(),
        },
        implementation_lanes: ImplementationLanes {
            primary: Lane {
                status: "AVAILABLE",
                implementation: "source-bound bounded Rust/Borsh decoder in rust/pump-protocol-v2",
                limitation: "one selected buy/TradeEvent candidate only",
            },
            structural_reference: Lane {
                status: "STRUCTURAL_REFERENCE_ONLY",
                implementation: "sealed expected values plus retained TypeScript/Rust fixture oracles",
                limitation: "not an independent protocol authority and not authentic chain evidence",
            },
            independent_protocol_authority: Lane {
                status: "UNAVAILABLE",
                implementation: "none admitted in bounded B3 dependency graph",
                limitation: "pump-rust-client 0.1.7 was not a second activation authority; pump-fun/carbon at bcc206b4715a2f02025d398e865b0d2036e0419b was rejected as a broad SDK/network graph",
            },
        },
        compatibility: Compatibility {
            structural: "STRUCTURAL_CANDIDATE",
            fixture: "FIXTURE_COMPATIBLE",
            observed: "UNKNOWN_NO_AUTHENTIC_CHAIN_BYTES",
            historical_activation: "UNKNOWN_NO_SLOT_RANGE_EVIDENCE",
            research_readiness: "UNPROVEN",
        },
        unresolved_questions: [
            "Which deployment or slot range used this exact schema?",
            "What exact quote-mint semantics apply to each historical observation?",
            "Which source proves base and quote decimal domains for each observation?",
            "Does authentic OF1 evidence agree with the primary decoder and structural-reference lane?",
            "Which event-envelope variants occur in the approved historical slice?",
            "Can an independently version-pinned generated parser be admitted without broad SDK/runtime dependencies?",
            "Are the implementation safety bounds and unique-shareholder rule valid for authentic observations?",
        ],
        prohibited_claims: [
            "historical Pump support proven",
            "live support proven",
            "Research Ready",
            "executable price or liquidity proven",
            "profitability established",
        ],
    }
}

#[must_use]
/// Render the canonical machine-readable evidence matrix.
///
/// # Panics
///
/// Panics only if serialization of compile-time-owned values fails.
pub fn generated_evidence_json() -> String {
    let mut output =
        serde_json::to_string_pretty(&matrix()).expect("matrix serialization is static");
    output.push('\n');
    output
}

#[must_use]
pub fn generated_evidence_markdown() -> String {
    let vector_manifest_sha =
        hex::encode(Sha256::digest(generated_vector_manifest_json().as_bytes()));
    let quarantine_reasons = quarantine_vectors()
        .iter()
        .map(|vector| format!("`{}`", vector.expected_outcome))
        .collect::<Vec<_>>()
        .join(", ");
    let registry_reasons = registry_rejection_vectors()
        .iter()
        .map(|vector| format!("`{}`", vector.expected_outcome))
        .collect::<Vec<_>>()
        .join(", ");
    let event_raw_fields = EVENT_RAW_FIELDS
        .iter()
        .map(|field| format!("`{field}`"))
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        "# Pump Protocol Evidence Matrix V2\n\n> **Document status: ACTIVE — BOUNDED ENGINEERING EVIDENCE.** This matrix is generated by `pump-protocol-v2/0.1.0`; edit the generator, not this file. The paired [machine-readable matrix](../../schemas/protocol/pump/legacy-sol-buy-trade-v1-evidence.json), [source receipt](../../schemas/protocol/pump/pump-public-docs-9c82f61-source-manifest.json) and [sealed vector manifest](../../rust/pump-protocol-v2/tests/fixtures/legacy-sol-buy-trade.json) are reviewed together.\n\n## Conclusion\n\nThe selected legacy SOL-accounting `buy`/`TradeEvent` candidate is **STRUCTURAL_CANDIDATE** and **FIXTURE_COMPATIBLE** only. Observed compatibility, historical activation, live support, Research Ready status, executable liquidity and profitability are **not proven**. Because the independent protocol-authority lane is `UNAVAILABLE`, this matrix cannot support a fully proven or activation-ready B3 conclusion.\n\n## Official source identity\n\n| Property | Value |\n|---|---|\n| Repository | `pump-fun/pump-public-docs` |\n| Commit | `{SOURCE_COMMIT}` |\n| Path | `{SOURCE_PATH}` |\n| Raw SHA-256 | `{SOURCE_SHA256}` |\n| Git blob SHA-1 | `{SOURCE_BLOB}` |\n| Retrieved | `2026-09-02T21:09:20Z` |\n| Supporting README | `README.md` SHA-256 `{}` (native-SOL accounting context only) |\n| Official bytes | [pinned Pump IDL]({SOURCE_URL}) |\n| Source receipt | [`{SOURCE_MANIFEST_PATH}`](../../{SOURCE_MANIFEST_PATH}) |\n| License/provenance | Official public repository; no license file at the pin, so the full IDL is not vendored |\n| Activation meaning | Structural schema evidence only; no deployment or slot-range claim |\n\n## Registry identity\n\n| Property | Value |\n|---|---|\n| Registry entry | `pump-mainnet-legacy-sol-buy-trade-9c82f61-v1` |\n| Network | `solana-mainnet-beta` |\n| Program ID | `{PUMP_PROGRAM_ID}` |\n| Decoder | `pump-protocol-v2/0.1.0` |\n| Candidate gate | exact source receipt plus exactly one compatible candidate |\n\n## Selected surface\n\n| Surface | Discriminator | Supported raw fields | Boundary |\n|---|---|---|---|\n| `buy` instruction | `66063d1201daebea` | `amount:u64`, `max_sol_cost:u64`, `track_volume:bool` | Exact 25-byte instruction; trailing or truncated bytes quarantine |\n| `TradeEvent` for `buy` | `bddb7fd34ee661ee` | All 32 pinned Borsh fields, raw integer quantities | Requires `is_buy=true`, `ix_name=buy`, exact payload exhaustion |\n| Anchor CPI envelope | `e445a52e51cb9a1d` | Framing only | Fixture-compatible convention; not the IDL event discriminator |\n\nTradeEvent raw fields: {event_raw_fields}.\n\nThe decoder is data-only: B3 does not validate transaction account association, program ownership or execution success. Event-reported reserves remain `EVENT_REPORTED_RESERVES_NOT_ACCOUNT_STATE`. The selected source does not prove an exact quote mint, quote decimals or base decimals; those values remain explicitly `UNKNOWN` and cannot support normalized economic output.\n\nImplementation safety bounds are local fail-closed policy, not official schema facts: TradeEvent CPI bytes <= 4096, ix_name UTF-8 bytes <= 32, shareholder count <= 128 (the byte cap may bind first), and unique shareholder addresses. Their validity for authentic observations remains unresolved.\n\n## Evidence and fail-closed cases\n\n| Measure | Result |\n|---|---:|\n| Sealed vector manifest | SHA-256 `{vector_manifest_sha}` |\n| Accepted sealed vectors | 2 (1 instruction, 1 event) |\n| Typed decoder/economic quarantines | {} |\n| Typed registry/source rejections | {} |\n| Primary decoder | AVAILABLE — source-bound bounded Rust/Borsh |\n| Structural reference | STRUCTURAL_REFERENCE_ONLY — retained sealed fixture oracles |\n| Independent protocol authority | UNAVAILABLE in this bounded dependency graph |\n| Observed compatibility | UNKNOWN — no authentic chain bytes |\n| Historical activation | UNKNOWN — no slot-range evidence |\n\nAccepted vector identities:\n\n- `buy-selected-schema-u64-values`: synthetic schema fixture, raw SHA-256 `c4c16e3627555b799443dc7d2eaad89b3a5d55217b1ce0da55fcef2ae84647ad`.\n- `trade-buy-current-official`: retained synthetic structural golden, raw SHA-256 `9c0bfb4a8374b259bdbf22820dd174c156d2630f408d18cb51163d2ff8cb7c06`; the name does not make it authentic.\n\nTyped quarantine reasons: {quarantine_reasons}.\n\nTyped registry/source rejection reasons: {registry_reasons}.\n\nEvery reported case includes its origin, evidence class, source identity, input or deterministic mutation and expected outcome in the sealed vector manifest; tests execute and compare the complete name/outcome set.\n\n## Unresolved questions\n\n- Which deployment or slot range used this exact schema?\n- What exact quote-mint semantics apply to each historical observation?\n- Which sources prove both decimal domains per observation?\n- Will authentic OF1 bytes agree with the primary and structural-reference lanes?\n- Which CPI envelope variants occur in the approved historical slice?\n- Can a separately pinned generated parser be admitted without a broad SDK/runtime graph?\n- Are the implementation safety bounds and unique-shareholder rule valid for authentic observations?\n\n## Reproduction\n\n```bash\ncargo +1.97.1 run --locked --offline --manifest-path rust/pump-protocol-v2/Cargo.toml --bin pump-protocol-evidence -- --check\n```\n\nThe evidence binary's `--check` mode does not rewrite committed evidence; Cargo may create ignored local build artifacts. The bounded CI wrapper blocks network syscalls for build scripts, tests and the evidence binary. No RPC, Triton, Old Faithful, wallet, signing, market-data or execution call occurs.\n",
        crate::registry::README_SHA256,
        quarantine_vectors().len(),
        registry_rejection_vectors().len(),
    )
}
