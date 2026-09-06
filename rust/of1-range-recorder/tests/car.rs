use of1_range_recorder::car::{
    CAR_SOURCE_COMMIT, CarError, VerificationLimits, decode_reported_cid, verify_header,
    verify_slot_sections,
};
use serde_json::Value;
use sha2::{Digest, Sha256};

const SLOT: u64 = 422_496_000;
fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../schemas/acquisition/of1/car-structural-fixture.json"
    ))
    .unwrap()
}
fn bytes(field: &str) -> Vec<u8> {
    hex::decode(fixture()[field].as_str().unwrap()).unwrap()
}
fn limits() -> VerificationLimits {
    VerificationLimits {
        max_total_bytes: 16 * 1024 * 1024,
        max_section_bytes: 16 * 1024 * 1024,
        max_nodes: 4096,
        max_links: 16384,
    }
}
fn nodes() -> Vec<Vec<u8>> {
    fixture()["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|n| hex::decode(n["payload_hex"].as_str().unwrap()).unwrap())
        .collect()
}
fn identity(raw: &[u8]) -> Vec<u8> {
    [vec![1, 0x71, 0x12, 32], Sha256::digest(raw).to_vec()].concat()
}
fn varint(mut value: u64) -> Vec<u8> {
    let mut out = Vec::new();
    loop {
        let low = u8::try_from(value & 127).unwrap();
        value >>= 7;
        out.push(low | if value > 0 { 128 } else { 0 });
        if value == 0 {
            return out;
        }
    }
}
fn section(raw: &[u8]) -> Vec<u8> {
    [
        varint(u64::try_from(36 + raw.len()).unwrap()),
        identity(raw),
        raw.to_vec(),
    ]
    .concat()
}
fn sections(nodes: &[Vec<u8>]) -> Vec<u8> {
    nodes.iter().flat_map(|n| section(n)).collect()
}
fn encoded_uint(value: u64) -> Vec<u8> {
    if value < 24 {
        vec![u8::try_from(value).unwrap()]
    } else if value <= 255 {
        vec![24, u8::try_from(value).unwrap()]
    } else if value <= 65535 {
        [
            vec![25],
            u16::try_from(value).unwrap().to_be_bytes().to_vec(),
        ]
        .concat()
    } else if u32::try_from(value).is_ok() {
        [
            vec![26],
            u32::try_from(value).unwrap().to_be_bytes().to_vec(),
        ]
        .concat()
    } else {
        [vec![27], value.to_be_bytes().to_vec()].concat()
    }
}
fn link(cid: &[u8]) -> Vec<u8> {
    [vec![0xd8, 42, 0x58, 37, 0], cid.to_vec()].concat()
}
fn block(entry: &[u8], rewards: &[u8]) -> Vec<u8> {
    [
        vec![0x86, 2],
        encoded_uint(SLOT),
        vec![0x80, 0x81],
        link(entry),
        vec![0x82],
        encoded_uint(SLOT - 1),
        vec![0],
        link(rewards),
    ]
    .concat()
}

#[test]
fn independent_js_fixture_closes_one_slot_without_root_or_domain_promotion() {
    let fixture = fixture();
    assert_eq!(fixture["evidence"], "Fixture");
    assert_eq!(fixture["authentic_chain_bytes"], false);
    assert_eq!(fixture["source_commit"], CAR_SOURCE_COMMIT);
    let raw = bytes("sections_hex");
    let report = verify_slot_sections(SLOT, &raw, limits()).unwrap();
    assert_eq!(report.verified_nodes, 5);
    assert_eq!(report.verified_links, 4);
    assert_eq!(
        report.terminal_block_cid_hex,
        fixture["expected"]["terminal_block_cid_hex"]
    );
    assert_eq!(report.root_to_slot_membership, "UNAVAILABLE");
    assert_eq!(report.source_observation, "NOT_ATTESTED_BY_VERIFIER");
    assert_eq!(report.dataframe_payload_and_checksum, "NOT_EVALUATED");
    assert_eq!(report.domain_counts, "UNAVAILABLE_NOT_DECODED_IN_B4");
    // Any HTTP chunk boundary is admissible only after exact ordered assembly of stored bytes.
    for size in 1..=raw.len() {
        let assembled: Vec<_> = raw.chunks(size).flatten().copied().collect();
        assert_eq!(
            verify_slot_sections(SLOT, &assembled, limits()).unwrap(),
            report
        );
    }
}

#[test]
fn signed_dataframe_checksum_source_vector_is_opaque_not_reinterpreted_as_unsigned() {
    let node = &nodes()[0];
    assert_eq!(&node[..3], &[0x86, 6, 0x3b]);
    assert_eq!(
        node,
        &[
            134, 6, 59, 70, 48, 192, 168, 213, 38, 83, 193, 1, 2, 70, 32, 119, 111, 114, 108, 100,
            128
        ]
    );
    assert!(verify_slot_sections(SLOT, &bytes("sections_hex"), limits()).is_ok());
}

#[test]
fn source_receipt_is_bound_to_the_same_structural_profile() {
    let receipt: Value = serde_json::from_str(include_str!(
        "../../../schemas/acquisition/of1/car-source-receipt.json"
    ))
    .unwrap();
    assert_eq!(receipt["commit"], CAR_SOURCE_COMMIT);
    assert_eq!(receipt["evidence"], "STRUCTURAL_SOURCE_ONLY");
    assert_eq!(receipt["authentic_chain_bytes"], false);
    assert_eq!(receipt["root_to_slot_membership"], "UNAVAILABLE");
    assert_eq!(receipt["files"].as_array().unwrap().len(), 7);
    for source in receipt["files"].as_array().unwrap() {
        assert_eq!(source["sha256"].as_str().unwrap().len(), 64);
    }
}

#[test]
fn reported_cid_is_canonical_base32_only() {
    let fixture = fixture();
    let root = fixture["root_cid_base32"].as_str().unwrap();
    for value in [root.to_string(), format!("{root}\n"), format!("{root}\r\n")] {
        assert_eq!(decode_reported_cid(&value).unwrap(), bytes("root_cid_hex"));
    }
    for value in [
        root.to_uppercase(),
        format!(" {root}"),
        format!("{root}="),
        format!("{root}\n\n"),
        "zQmunsupported".into(),
        format!("{root} filename"),
    ] {
        assert_eq!(decode_reported_cid(&value), Err(CarError::UnsupportedCid));
    }
    let mut noncanonical = root.as_bytes().to_vec();
    *noncanonical.last_mut().unwrap() = b'b';
    assert_eq!(
        decode_reported_cid(std::str::from_utf8(&noncanonical).unwrap()),
        Err(CarError::UnsupportedCid)
    );
}

#[test]
fn header_root_agreement_does_not_verify_trailing_sections_or_epoch_inclusion() {
    let header = bytes("header_hex");
    let root = bytes("root_cid_hex");
    let report = verify_header(&header, &root, limits()).unwrap();
    assert_eq!(report.header_bytes_consumed, header.len());
    assert_eq!(report.trailing_prefix_bytes_unverified, 0);
    let prefix = [header.clone(), vec![0xff, 0x80]].concat();
    let report = verify_header(&prefix, &root, limits()).unwrap();
    assert_eq!(report.trailing_prefix_bytes_unverified, 2);
    assert_eq!(report.root_to_slot_membership, "UNAVAILABLE");
    assert_eq!(report.declared_root_agreement, "MATCHED_DECLARATIONS_ONLY");
    let mut wrong = root;
    wrong[35] ^= 1;
    assert_eq!(
        verify_header(&header, &wrong, limits()),
        Err(CarError::Root)
    );
    assert!(verify_header(&header[..header.len() - 1], &wrong, limits()).is_err());
}

#[test]
fn every_single_bit_corruption_and_partial_terminal_section_fails_closed() {
    let raw = bytes("sections_hex");
    for index in 0..raw.len() {
        for bit in 0..8 {
            let mut changed = raw.clone();
            changed[index] ^= 1 << bit;
            assert!(
                verify_slot_sections(SLOT, &changed, limits()).is_err(),
                "byte {index} bit {bit}"
            );
        }
    }
    for length in 0..raw.len() {
        assert!(verify_slot_sections(SLOT, &raw[..length], limits()).is_err());
    }
}

#[test]
fn transaction_or_rewards_cannot_claim_a_different_slot_from_the_terminal_block() {
    for node_index in [1, 3] {
        let mut changed = nodes();
        let encoded_slot = encoded_uint(SLOT);
        let position = changed[node_index]
            .windows(encoded_slot.len())
            .position(|window| window == encoded_slot)
            .unwrap();
        changed[node_index][position + encoded_slot.len() - 1] ^= 1;
        // Recomputed node CID is valid; slot mismatch must fail before link-closure checking.
        assert_eq!(
            verify_slot_sections(SLOT, &sections(&changed), limits()),
            Err(CarError::Slot)
        );
    }
}

#[test]
fn noncanonical_cbor_and_invalid_link_tags_fail_even_with_matching_content_hashes() {
    let mut nonminimal = nodes()[0].clone();
    nonminimal.splice(1..2, [24, 6]);
    assert_eq!(
        verify_slot_sections(SLOT, &section(&nonminimal), limits()),
        Err(CarError::Schema)
    );
    let mut wrong_tag = nodes()[1].clone();
    let position = wrong_tag.windows(2).position(|w| w == [0xd8, 42]).unwrap();
    wrong_tag[position + 1] = 43;
    assert_eq!(
        verify_slot_sections(SLOT, &section(&wrong_tag), limits()),
        Err(CarError::Schema)
    );
    let mut wrong_cid_padding = nodes()[1].clone();
    wrong_cid_padding[position + 4] = 1;
    assert_eq!(
        verify_slot_sections(SLOT, &section(&wrong_cid_padding), limits()),
        Err(CarError::UnsupportedCid)
    );
}

#[test]
fn rejects_nonminimal_overflow_and_unterminated_varints() {
    for raw in [vec![0x80, 0], vec![0xff; 11], vec![0x80]] {
        assert!(verify_slot_sections(SLOT, &raw, limits()).is_err());
    }
    let mut raw = bytes("sections_hex");
    raw.splice(0..1, [raw[0] | 128, 0]);
    assert_eq!(
        verify_slot_sections(SLOT, &raw, limits()),
        Err(CarError::Varint)
    );
}

#[test]
fn rejects_unsupported_cid_profile_before_content_admission() {
    let raw = section(&nodes()[0]);
    for (index, value) in [(1, 0), (2, 0x55), (3, 0x13), (4, 31)] {
        let mut changed = raw.clone();
        changed[index] = value;
        assert_eq!(
            verify_slot_sections(SLOT, &changed, limits()),
            Err(CarError::UnsupportedCid)
        );
    }
}

#[test]
fn rejects_wrong_slots_duplicate_cids_and_nonterminal_block() {
    assert_eq!(
        verify_slot_sections(SLOT + 1, &bytes("sections_hex"), limits()),
        Err(CarError::Slot)
    );
    let mut duplicate = nodes();
    duplicate.insert(0, duplicate[0].clone());
    assert_eq!(
        verify_slot_sections(SLOT, &sections(&duplicate), limits()),
        Err(CarError::DuplicateCid)
    );
    let mut misplaced = nodes();
    misplaced.swap(3, 4);
    assert_eq!(
        verify_slot_sections(SLOT, &sections(&misplaced), limits()),
        Err(CarError::Block)
    );
}

#[test]
fn rejects_missing_wrong_kind_and_unreachable_nodes() {
    let mut missing = nodes();
    missing.remove(0);
    assert_eq!(
        verify_slot_sections(SLOT, &sections(&missing), limits()),
        Err(CarError::MissingLink)
    );
    let mut wrong = nodes();
    wrong[4] = block(&identity(&wrong[3]), &identity(&wrong[3]));
    assert_eq!(
        verify_slot_sections(SLOT, &sections(&wrong), limits()),
        Err(CarError::LinkKind)
    );
    let mut unreachable = nodes();
    let extra = [vec![0x84, 1, 1, 0x58, 32], vec![0xaa; 32], vec![0x80]].concat();
    unreachable.insert(0, extra);
    assert_eq!(
        verify_slot_sections(SLOT, &sections(&unreachable), limits()),
        Err(CarError::Unreachable)
    );
}

#[test]
fn rejects_schema_extensions_indefinite_cbor_and_trailing_node_data() {
    for raw in [
        vec![0x9f, 6, 0xff],
        vec![0x81, 3],
        vec![0x81, 4],
        vec![0x81, 7],
    ] {
        assert!(verify_slot_sections(SLOT, &section(&raw), limits()).is_err());
    }
    let mut node = nodes()[0].clone();
    node.push(0);
    assert_eq!(
        verify_slot_sections(SLOT, &section(&node), limits()),
        Err(CarError::Schema)
    );
    let mut raw = bytes("sections_hex");
    raw.push(0);
    assert_eq!(
        verify_slot_sections(SLOT, &raw, limits()),
        Err(CarError::Schema)
    );
}

#[test]
fn explicit_byte_section_node_link_limits_stop_without_fetching_missing_content() {
    let raw = bytes("sections_hex");
    for cap in [
        VerificationLimits {
            max_total_bytes: raw.len() - 1,
            ..limits()
        },
        VerificationLimits {
            max_section_bytes: 8,
            ..limits()
        },
        VerificationLimits {
            max_nodes: 4,
            ..limits()
        },
        VerificationLimits {
            max_links: 3,
            ..limits()
        },
    ] {
        assert_eq!(verify_slot_sections(SLOT, &raw, cap), Err(CarError::Limit));
    }
}
