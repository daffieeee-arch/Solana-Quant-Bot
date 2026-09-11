//! Authentic bytes retain their provenance; generated boundary cases are synthetic.
//! This suite checks archival envelopes, not Solana/Pump domain decoding.

use of1_range_recorder::car::{
    ArchivalNodeCounts, CAR_SOURCE_COMMIT, CarError, VerificationLimits, verify_slot_sections,
};
use serde_json::Value;
use sha2::{Digest, Sha256};

const SLOT: u64 = 422_496_000;
const RAW_SHA256: &str = "3d93337542751eaacecf039a2fb5384f700f226879616b3fb80117fb9d4a8ae6";

fn limits() -> VerificationLimits {
    VerificationLimits {
        max_total_bytes: 1 << 20,
        max_section_bytes: 1 << 20,
        max_nodes: 4096,
        max_links: 16384,
    }
}

fn uint(value: u64) -> Vec<u8> {
    match value {
        0..=23 => vec![u8::try_from(value).unwrap()],
        24..=255 => vec![24, u8::try_from(value).unwrap()],
        256..=65535 => [
            vec![25],
            u16::try_from(value).unwrap().to_be_bytes().to_vec(),
        ]
        .concat(),
        65536..=0xffff_ffff => [
            vec![26],
            u32::try_from(value).unwrap().to_be_bytes().to_vec(),
        ]
        .concat(),
        _ => [vec![27], value.to_be_bytes().to_vec()].concat(),
    }
}

fn varint(mut value: usize) -> Vec<u8> {
    let mut output = Vec::new();
    loop {
        let low = u8::try_from(value & 127).unwrap();
        value >>= 7;
        output.push(low | if value == 0 { 0 } else { 128 });
        if value == 0 {
            return output;
        }
    }
}

fn cid(payload: &[u8]) -> Vec<u8> {
    [vec![1, 0x71, 0x12, 32], Sha256::digest(payload).to_vec()].concat()
}

fn link(payload: &[u8]) -> Vec<u8> {
    [vec![0xd8, 42, 0x58, 37, 0], cid(payload)].concat()
}

fn section(payload: &[u8]) -> Vec<u8> {
    [varint(payload.len() + 36), cid(payload), payload.to_vec()].concat()
}

fn synthetic_nodes(first: &[u8], second: &[u8]) -> Vec<Vec<u8>> {
    let entry = [vec![0x84, 1, 0, 0x58, 32], vec![0; 32], vec![0x80]].concat();
    let rewards = [
        vec![0x83, 5],
        uint(SLOT),
        vec![0x85, 6, 0xf6, 0xf6, 0xf6, 0x40],
    ]
    .concat();
    let block = [
        vec![0x86, 2],
        uint(SLOT),
        vec![0x81, 0x82],
        first.to_vec(),
        second.to_vec(),
        vec![0x81],
        link(&entry),
        vec![0x82],
        uint(SLOT - 1),
        vec![0],
        link(&rewards),
    ]
    .concat();
    vec![entry, rewards, block]
}

fn sections(nodes: &[Vec<u8>]) -> Vec<u8> {
    nodes.iter().flat_map(|node| section(node)).collect()
}

#[test]
fn preserved_authentic_range_passes_complete_cid_slot_and_link_checks() {
    let encoded =
        include_str!("../../../schemas/acquisition/of1/epoch-978-slot-422496000.observed.car.hex");
    let raw = hex::decode(encoded.split_whitespace().collect::<String>()).unwrap();
    assert_eq!(raw.len(), 45_051);
    assert_eq!(hex::encode(Sha256::digest(&raw)), RAW_SHA256);
    let provenance: Value = serde_json::from_str(include_str!(
        "../../../schemas/acquisition/of1/epoch-978-slot-422496000.observed.provenance.json"
    ))
    .unwrap();
    assert_eq!(provenance["raw_sha256"], RAW_SHA256);
    assert_eq!(provenance["authentic_chain_bytes"], true);
    assert_eq!(provenance["slice_class"], "ENGINEERING_VALIDATION_ONLY");
    assert_eq!(provenance["format_source"]["commit"], CAR_SOURCE_COMMIT);
    assert_eq!(
        provenance["original_verifier_result"],
        "CAR_CBOR_OR_ARCHIVAL_SCHEMA_INVALID"
    );
    let report = verify_slot_sections(SLOT, &raw, limits()).unwrap();
    assert_eq!(report.verified_nodes, 66);
    assert_eq!(report.verified_links, 65);
    assert_eq!(
        report.archival_node_counts,
        ArchivalNodeCounts {
            transaction: 0,
            entry: 64,
            block: 1,
            rewards: 1,
            dataframe: 0,
        }
    );
    assert_eq!(report.root_to_slot_membership, "UNAVAILABLE");
    assert_eq!(report.dataframe_payload_and_checksum, "NOT_EVALUATED");
    assert_eq!(report.domain_counts, "UNAVAILABLE_NOT_DECODED_IN_B4");
    // The separate new report chooses to display counts; preserve old reports.
    assert!(
        serde_json::to_value(&report)
            .unwrap()
            .get("archival_node_counts")
            .is_none()
    );
}

#[test]
fn synthetic_both_shredding_positions_accept_minus_one_zero_and_i64_boundaries() {
    let positive_max = uint(u64::try_from(i64::MAX).unwrap());
    let mut negative_min = positive_max.clone();
    negative_min[0] |= 0x20;
    for value in [vec![0x20], vec![0], positive_max, negative_min] {
        for position in 0..2 {
            let pair = if position == 0 {
                (value.as_slice(), &[0][..])
            } else {
                (&[0][..], value.as_slice())
            };
            let report =
                verify_slot_sections(SLOT, &sections(&synthetic_nodes(pair.0, pair.1)), limits())
                    .unwrap();
            assert_eq!(report.archival_node_counts.block, 1);
        }
    }
}

#[test]
fn synthetic_both_shredding_positions_reject_overflow_wrong_types_and_invalid_cbor() {
    let overflow = u64::try_from(i64::MAX).unwrap() + 1;
    let mut negative_overflow = uint(overflow);
    negative_overflow[0] |= 0x20;
    let mut negative_u64_max = uint(u64::MAX);
    negative_u64_max[0] |= 0x20;
    for invalid in [
        uint(overflow),
        uint(u64::MAX),
        negative_overflow,
        negative_u64_max,
        vec![0xf6],
        vec![0xf4],
        vec![0xf5],
        vec![0x40],
        vec![0x60],
        vec![0x80],
        vec![0xa0],
        vec![0xc0, 0],
        vec![0xfa, 0x3f, 0x80, 0, 0],
        vec![0x18, 0],
        vec![0x38, 0],
        vec![0x1c],
        vec![0x3c],
        vec![0x1f],
        vec![0x3f],
        vec![0xff],
    ] {
        for position in 0..2 {
            let pair = if position == 0 {
                (invalid.as_slice(), &[0][..])
            } else {
                (&[0][..], invalid.as_slice())
            };
            assert_eq!(
                verify_slot_sections(SLOT, &sections(&synthetic_nodes(pair.0, pair.1)), limits()),
                Err(CarError::Schema),
                "position {position}, bytes {invalid:x?}"
            );
        }
    }
}

#[test]
fn synthetic_each_truncated_i64_payload_in_both_positions_stays_rejected() {
    for major in [0x1b, 0x3b] {
        let encoded = [vec![major, 0x7f], vec![0xff; 7]].concat();
        for end in 0..encoded.len() {
            for position in 0..2 {
                let mut raw = [vec![0x86, 2], uint(SLOT), vec![0x81, 0x82]].concat();
                if position == 1 {
                    raw.push(0);
                }
                raw.extend_from_slice(&encoded[..end]);
                assert_eq!(
                    verify_slot_sections(SLOT, &section(&raw), limits()),
                    Err(CarError::Truncated),
                    "position {position}, length {end}"
                );
            }
        }
    }
}

#[test]
fn synthetic_negative_unsigned_slot_parent_and_entry_hash_count_still_fail() {
    for (node, offset, length) in [
        (0, 2, 1),
        (1, 2, uint(SLOT).len()),
        (2, 2, uint(SLOT).len()),
    ] {
        let mut nodes = synthetic_nodes(&[0x20], &[0x20]);
        nodes[node].splice(offset..offset + length, [0x20]);
        assert_eq!(
            verify_slot_sections(SLOT, &sections(&nodes), limits()),
            Err(CarError::Schema)
        );
    }
    let mut nodes = synthetic_nodes(&[0x20], &[0x20]);
    let parent = uint(SLOT - 1);
    let block = nodes.last_mut().unwrap();
    let offset = block
        .windows(parent.len())
        .position(|bytes| bytes == parent)
        .unwrap();
    block.splice(offset..offset + parent.len(), [0x20]);
    assert_eq!(
        verify_slot_sections(SLOT, &sections(&nodes), limits()),
        Err(CarError::Schema)
    );
}

#[test]
fn signed_shredding_fix_does_not_relax_pair_shape_or_full_input_consumption() {
    for pair_header in [0x80, 0x81, 0x83, 0x9f] {
        let mut nodes = synthetic_nodes(&[0x20], &[0x20]);
        nodes[2][2 + uint(SLOT).len() + 1] = pair_header;
        assert_eq!(
            verify_slot_sections(SLOT, &sections(&nodes), limits()),
            Err(CarError::Schema)
        );
    }
    let mut nodes = synthetic_nodes(&[0x20], &[0x20]);
    nodes[2].push(0);
    assert_eq!(
        verify_slot_sections(SLOT, &sections(&nodes), limits()),
        Err(CarError::Schema)
    );
}
