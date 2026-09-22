//! Missing span/order regressions salvaged selectively from #111; no domain decode.
use of1_range_recorder::car::{
    ByteSpan, CarError, VerificationLimits, inspect_slot_sections, verify_slot_sections,
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

fn sealed() -> Vec<u8> {
    hex::decode(fixture()["sections_hex"].as_str().unwrap()).unwrap()
}

fn limits() -> VerificationLimits {
    VerificationLimits {
        max_total_bytes: 8192,
        max_section_bytes: 4096,
        max_nodes: 32,
        max_links: 64,
    }
}

fn span<'a>(bytes: &'a [u8], location: &ByteSpan) -> &'a [u8] {
    &bytes[location.offset..location.offset + location.length]
}

// Deliberately synthetic canonical CBOR encoders. These produce test mutations,
// not new protocol authority or replacements for the sealed cross-language vector.
fn uint(value: u64) -> Vec<u8> {
    match value {
        0..=23 => vec![u8::try_from(value).unwrap()],
        24..=255 => vec![24, u8::try_from(value).unwrap()],
        256..=65535 => [
            vec![25],
            u16::try_from(value).unwrap().to_be_bytes().to_vec(),
        ]
        .concat(),
        65536..=4_294_967_295 => [
            vec![26],
            u32::try_from(value).unwrap().to_be_bytes().to_vec(),
        ]
        .concat(),
        _ => [vec![27], value.to_be_bytes().to_vec()].concat(),
    }
}

fn cid(raw: &[u8]) -> Vec<u8> {
    [vec![1, 0x71, 0x12, 32], Sha256::digest(raw).to_vec()].concat()
}

fn link(identity: &[u8]) -> Vec<u8> {
    [vec![0xd8, 42, 0x58, 37, 0], identity.to_vec()].concat()
}

fn varint(mut value: usize) -> Vec<u8> {
    let mut result = Vec::new();
    loop {
        let next = u8::try_from(value & 127).unwrap();
        value >>= 7;
        result.push(next | if value == 0 { 0 } else { 128 });
        if value == 0 {
            return result;
        }
    }
}

fn section(raw: &[u8]) -> Vec<u8> {
    [varint(raw.len() + 36), cid(raw), raw.to_vec()].concat()
}

fn raw_sections(nodes: &[Vec<u8>]) -> Vec<u8> {
    nodes.iter().flat_map(|node| section(node)).collect()
}

fn frame(data: &[u8]) -> Vec<u8> {
    assert!(data.len() < 24);
    [
        vec![
            0x85,
            6,
            0xf6,
            0xf6,
            0xf6,
            0x40 + u8::try_from(data.len()).unwrap(),
        ],
        data.to_vec(),
    ]
    .concat()
}

fn transaction(slot: u64, data: &[u8], position: Option<Vec<u8>>) -> Vec<u8> {
    [
        vec![if position.is_some() { 0x85 } else { 0x84 }, 0],
        data.to_vec(),
        frame(&[0]),
        uint(slot),
        position.unwrap_or_default(),
    ]
    .concat()
}

fn graph(slot: u64, transactions: &[Vec<u8>], ordered_cids: &[Vec<u8>]) -> Vec<u8> {
    let entry = [
        vec![0x84, 1, 0, 0x58, 32],
        vec![0xaa; 32],
        vec![0x80 + u8::try_from(ordered_cids.len()).unwrap()],
        ordered_cids
            .iter()
            .flat_map(|identity| link(identity))
            .collect(),
    ]
    .concat();
    let rewards = [vec![0x83, 5], uint(slot), frame(&[])].concat();
    let block = [
        vec![0x86, 2],
        uint(slot),
        vec![0x80, 0x81],
        link(&cid(&entry)),
        vec![0x82, 0, 0],
        link(&cid(&rewards)),
    ]
    .concat();
    let mut nodes = transactions.to_vec();
    nodes.extend([entry, rewards, block]);
    raw_sections(&nodes)
}

#[test]
fn sealed_vector_retains_one_atomic_opaque_transaction_and_original_verifier_report() {
    let bytes = sealed();
    assert_eq!(bytes.len(), 468);
    let report = inspect_slot_sections(SLOT, &bytes, limits()).unwrap();
    assert_eq!(
        report.integrity,
        verify_slot_sections(SLOT, &bytes, limits()).unwrap()
    );
    assert_eq!(report.archival_nodes.len(), 5);
    assert_eq!(report.transaction_envelopes.len(), 1);
    assert_eq!(report.integrity.root_to_slot_membership, "UNAVAILABLE");
    assert_eq!(
        report.integrity.source_observation,
        "NOT_ATTESTED_BY_VERIFIER"
    );
    assert_eq!(
        report.solana_transaction_decode,
        "UNAVAILABLE_OPAQUE_DATAFRAME"
    );
    assert_eq!(report.pump_decode, "UNAVAILABLE_WITHOUT_SOLANA_DECODE");
    let tx = &report.transaction_envelopes[0];
    assert_eq!(tx.slot, SLOT.to_string());
    assert_eq!(tx.transaction_position.as_deref(), Some("0"));
    assert_eq!(tx.atomic_package, "OPAQUE_DATA_AND_METADATA_TOGETHER");
    assert_eq!(span(&bytes, &tx.data.data_span), b"hello");
    assert_eq!(span(&bytes, &tx.metadata.data_span), &[0]);
    assert_eq!(
        tx.data.next_cid_hex,
        [report.archival_nodes[0].cid_hex.clone()]
    );
    let rewards = &report.archival_nodes[3];
    assert_eq!(rewards.frames.len(), 1);
    assert!(span(&bytes, &rewards.frames[0].data_span).is_empty());
    assert_eq!(span(&bytes, &tx.data.raw_cbor_span)[0], 0x86);
    assert_eq!(span(&bytes, &tx.metadata.raw_cbor_span)[0], 0x85);
    let continuation = &report.archival_nodes[0].frames[0];
    assert_eq!(span(&bytes, &continuation.data_span), b" world");
    assert_eq!(
        continuation.checksum.as_deref(),
        Some("-5057754212900164546")
    );
    assert_eq!(continuation.payload_and_checksum, "NOT_EVALUATED");
    // hello/world is a sealed structural vector, not a valid decoded transaction.
    assert_eq!(tx.solana_transaction_decode, "UNAVAILABLE_OPAQUE_DATAFRAME");
    for (node, source) in report
        .archival_nodes
        .iter()
        .zip(fixture()["nodes"].as_array().unwrap())
    {
        assert_eq!(
            hex::encode(span(&bytes, &node.raw_cbor_span)),
            source["payload_hex"]
        );
        assert_eq!(
            hex::encode(span(&bytes, &node.section_span)),
            source["section_hex"]
        );
        assert_eq!(node.cid_hex, source["cid_hex"]);
    }
}

#[test]
fn archival_order_comes_from_links_not_car_physical_order() {
    let first = transaction(SLOT, &frame(b"A"), None);
    let second = transaction(SLOT, &frame(b"B"), Some(vec![0xf6]));
    let bytes = graph(
        SLOT,
        &[first.clone(), second.clone()],
        &[cid(&second), cid(&first)],
    );
    let report = inspect_slot_sections(SLOT, &bytes, limits()).unwrap();
    assert_eq!(report.archival_nodes[0].cid_hex, hex::encode(cid(&first)));
    assert_eq!(report.archival_nodes[1].cid_hex, hex::encode(cid(&second)));
    let txs = &report.transaction_envelopes;
    assert_eq!(txs[0].transaction_cid_hex, hex::encode(cid(&second)));
    assert_eq!(txs[1].transaction_cid_hex, hex::encode(cid(&first)));
    assert_eq!(txs[0].entry_transaction_ordinal, 0);
    assert_eq!(txs[1].entry_transaction_ordinal, 1);
    assert_eq!(txs[1].archival_ordinal, 1);
    assert!(txs.iter().all(|tx| tx.transaction_position.is_none()));
    assert_eq!(
        report.ordering,
        "BLOCK_ENTRY_TRANSACTION_LINK_ORDER_NOT_EXECUTION_ORDER"
    );
}

#[test]
fn raw_u64_and_negative_cbor_boundaries_are_decimal_strings_not_float_or_zero() {
    let negative = [vec![0x3b], u64::MAX.to_be_bytes().to_vec()].concat();
    let data = [
        vec![0x85, 6],
        negative,
        uint(u64::MAX),
        uint(u64::MAX),
        vec![0x40],
    ]
    .concat();
    let tx = transaction(u64::MAX, &data, Some(uint(0)));
    let bytes = graph(u64::MAX, std::slice::from_ref(&tx), &[cid(&tx)]);
    let report = inspect_slot_sections(u64::MAX, &bytes, limits()).unwrap();
    let tx = &report.transaction_envelopes[0];
    assert_eq!(tx.slot, "18446744073709551615");
    assert_eq!(tx.transaction_position.as_deref(), Some("0"));
    assert_eq!(tx.data.checksum.as_deref(), Some("-18446744073709551616"));
    assert_eq!(tx.data.frame_index.as_deref(), Some("18446744073709551615"));
    assert_eq!(tx.data.total.as_deref(), Some("18446744073709551615"));
    let json = serde_json::to_value(&report).unwrap();
    assert!(json["transaction_envelopes"][0]["slot"].is_string());
}

#[test]
fn repeated_transaction_reference_is_not_silently_deduplicated_or_given_a_position() {
    let tx = transaction(SLOT, &frame(b"A"), None);
    let bytes = graph(SLOT, std::slice::from_ref(&tx), &[cid(&tx), cid(&tx)]);
    // Keep the old integrity-only acceptance/output unchanged.
    assert!(verify_slot_sections(SLOT, &bytes, limits()).is_ok());
    assert_eq!(
        inspect_slot_sections(SLOT, &bytes, limits()),
        Err(CarError::AmbiguousOrder)
    );
}

#[test]
fn repeated_entry_reference_is_ambiguous_even_when_integrity_closure_is_valid() {
    let tx = transaction(SLOT, &frame(b"A"), Some(uint(0)));
    let entry = [
        vec![0x84, 1, 0, 0x58, 32],
        vec![0xaa; 32],
        vec![0x81],
        link(&cid(&tx)),
    ]
    .concat();
    let rewards = [vec![0x83, 5], uint(SLOT), frame(&[])].concat();
    let block = [
        vec![0x86, 2],
        uint(SLOT),
        vec![0x80, 0x82],
        link(&cid(&entry)),
        link(&cid(&entry)),
        vec![0x82, 0, 0],
        link(&cid(&rewards)),
    ]
    .concat();
    let bytes = raw_sections(&[tx, entry, rewards, block]);
    assert!(verify_slot_sections(SLOT, &bytes, limits()).is_ok());
    assert_eq!(
        inspect_slot_sections(SLOT, &bytes, limits()),
        Err(CarError::AmbiguousOrder)
    );
}

#[test]
fn conflicting_declared_position_is_rejected_without_overriding_link_order() {
    for position in [17, u64::MAX] {
        let tx = transaction(SLOT, &frame(b"A"), Some(uint(position)));
        let bytes = graph(SLOT, std::slice::from_ref(&tx), &[cid(&tx)]);
        assert!(verify_slot_sections(SLOT, &bytes, limits()).is_ok());
        assert_eq!(
            inspect_slot_sections(SLOT, &bytes, limits()),
            Err(CarError::TransactionIndex)
        );
    }
}
