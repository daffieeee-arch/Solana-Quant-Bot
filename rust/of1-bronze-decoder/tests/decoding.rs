//! Three exact authentic archival sections; all mutations/composed graphs below
//! are SYNTHETIC adversarial fixtures, never new authentic observations.
use bincode::Options;
use of1_bronze_decoder::{
    archive::{self, Envelope, Frame, MAX_FRAME_BYTES},
    codec, proto, report,
};
use of1_range_recorder::sha256;
use prost::Message;
use serde_cbor::Value as Cbor;
use serde_json::{Value, json};
use std::{collections::BTreeMap, hash::Hasher};

fn vectors() -> Value {
    serde_json::from_str(include_str!("fixtures/authentic-sections.json")).unwrap()
}
fn envelope(index: usize) -> Envelope {
    let v = &vectors()["fixtures"][index];
    let raw = hex::decode(v["section_hex"].as_str().unwrap()).unwrap();
    assert_eq!(sha256(&raw), v["section_sha256"]);
    let prefix = raw.iter().position(|b| b & 128 == 0).unwrap() + 1;
    let cid = &raw[prefix..prefix + 36];
    assert_eq!(hex::encode(cid), v["cid_hex"]);
    assert_eq!(sha256(&raw[prefix + 36..]), hex::encode(&cid[4..]));
    let Cbor::Array(a) = serde_cbor::from_slice(&raw[prefix + 36..]).unwrap() else {
        panic!("array")
    };
    Envelope {
        cid_hex: hex::encode(cid),
        physical_node_index: 0,
        raw_offset: usize::try_from(v["raw_offset"].as_u64().unwrap()).unwrap(),
        raw_length: raw.len(),
        entry_index: 0,
        transaction_index_in_entry: 0,
        transaction_index_in_slot: usize::try_from(
            v["order"]["transaction_index_in_slot"].as_u64().unwrap(),
        )
        .unwrap(),
        source_transaction_index: None,
        data: archive::frame(&a[1]).unwrap(),
        metadata: archive::frame(&a[2]).unwrap(),
    }
}
fn opts() -> impl Options {
    bincode::DefaultOptions::new()
        .with_fixint_encoding()
        .reject_trailing_bytes()
}
fn status(e: &Envelope) -> proto::Status {
    proto::Status::decode(
        codec::metadata_bytes(&e.metadata.bytes)
            .unwrap()
            .0
            .as_slice(),
    )
    .unwrap()
}
fn set_status(e: &mut Envelope, status: &proto::Status) {
    e.metadata.bytes = status.encode_to_vec();
    e.metadata.checksum = None;
}
fn frame(bytes: Vec<u8>) -> Frame {
    Frame {
        checksum: None,
        index: None,
        total: None,
        bytes,
        next: vec![],
    }
}

#[test]
fn authentic_three_sections_decode_actual_signatures_status_fee_programs_without_vote_filter() {
    for i in 0..3 {
        let e = envelope(i);
        let tx = codec::decode(&e, &BTreeMap::new()).unwrap();
        let expected = &vectors()["fixtures"][i]["expected"];
        for key in [
            "signatures",
            "fee_lamports",
            "status",
            "program_ids",
            "wire_sha256",
            "metadata_sha256",
        ] {
            assert_eq!(tx[key], expected[key], "{key}");
        }
        assert_eq!(tx["metadata_codec"], "ZSTD_PROTOBUF");
        assert_eq!(tx["pump_program_involvement"], false);
        assert_eq!(tx["pump_event_decode"], "NOT_PERFORMED");
        assert!(tx["name"].is_null());
    }
}
#[test]
fn authentic_metadata_is_atomic_missing_or_invalid_does_not_create_transaction_success() {
    let mut e = envelope(0);
    e.metadata.bytes.clear();
    assert_eq!(
        codec::decode(&e, &BTreeMap::new()).unwrap_err().to_string(),
        "MISSING_STATUS_METADATA"
    );
    e.metadata.bytes = vec![255];
    assert!(
        codec::decode(&e, &BTreeMap::new())
            .unwrap_err()
            .to_string()
            .starts_with("STATUS_PROTOBUF")
    );
}
#[test]
fn wire_truncation_trailing_bytes_and_bad_signature_count_fail() {
    for transform in [0, 1, 2] {
        let mut e = envelope(0);
        match transform {
            0 => {
                e.data.bytes.pop();
            }
            1 => e.data.bytes.push(0),
            _ => e.data.bytes[0] = 0,
        }
        assert!(codec::decode(&e, &BTreeMap::new()).is_err());
    }
}
#[test]
fn exact_u64_max_fee_balances_and_optional_presence_no_float() {
    let mut e = envelope(0);
    let mut s = status(&e);
    s.fee = u64::MAX;
    s.pre_balances[0] = u64::MAX;
    s.compute_units_consumed = Some(u64::MAX);
    s.cost_units = None;
    set_status(&mut e, &s);
    let t = codec::decode(&e, &BTreeMap::new()).unwrap();
    assert_eq!(t["fee_lamports"], u64::MAX.to_string());
    assert_eq!(t["pre_balances_lamports"][0], u64::MAX.to_string());
    assert_eq!(t["compute_units_consumed"], u64::MAX.to_string());
    assert!(t["cost_units"].is_null());
}
#[test]
fn actual_error_enum_is_decoded_unknown_and_trailing_errors_rejected() {
    let mut e = envelope(0);
    let mut s = status(&e);
    let error = solana_transaction_error::TransactionError::AccountNotFound;
    s.err = Some(proto::TransactionError {
        err: opts().serialize(&error).unwrap(),
    });
    set_status(&mut e, &s);
    let t = codec::decode(&e, &BTreeMap::new()).unwrap();
    assert_eq!(t["status"], "ERROR");
    assert_eq!(t["transaction_error"], "AccountNotFound");
    s.err.as_mut().unwrap().err = vec![255; 4];
    set_status(&mut e, &s);
    assert_eq!(
        codec::decode(&e, &BTreeMap::new()).unwrap_err().to_string(),
        "UNSUPPORTED_TRANSACTION_ERROR"
    );
    s.err.as_mut().unwrap().err = opts().serialize(&error).unwrap();
    s.err.as_mut().unwrap().err.push(0);
    set_status(&mut e, &s);
    assert!(codec::decode(&e, &BTreeMap::new()).is_err());
}
#[test]
fn metadata_presence_and_balance_coverage_cannot_silently_default() {
    let mut e = envelope(0);
    let mut s = status(&e);
    s.pre_balances.pop();
    set_status(&mut e, &s);
    assert_eq!(
        codec::decode(&e, &BTreeMap::new()).unwrap_err().to_string(),
        "ACCOUNT_BALANCE_COVERAGE"
    );
    let mut e = envelope(0);
    let mut s = status(&e);
    s.log_messages_none = true;
    s.log_messages.push("contradiction".into());
    set_status(&mut e, &s);
    assert_eq!(
        codec::decode(&e, &BTreeMap::new()).unwrap_err().to_string(),
        "STATUS_PRESENCE_CONTRADICTION"
    );
}
#[test]
fn crc64_fnv_signed_checksums_and_corruption() {
    let bytes = b"bounded checksum fixture".to_vec();
    let mut f = frame(bytes.clone());
    f.checksum = Some(crc::Crc::<u64>::new(&crc::CRC_64_GO_ISO).checksum(&bytes));
    assert_eq!(
        codec::checked_frame(&f, &BTreeMap::new()).unwrap().1,
        "CRC64_GO_ISO"
    );
    let mut h = fnv::FnvHasher::default();
    h.write(&bytes);
    f.checksum = Some(h.finish());
    assert_eq!(
        codec::checked_frame(&f, &BTreeMap::new()).unwrap().1,
        "FNV1A64_LEGACY"
    );
    f.bytes[0] ^= 1;
    assert_eq!(
        codec::checked_frame(&f, &BTreeMap::new())
            .unwrap_err()
            .to_string(),
        "DATAFRAME_CHECKSUM_MISMATCH"
    );
    for signed in [i64::MIN, -1] {
        let v = Cbor::Array(vec![
            Cbor::Integer(6),
            Cbor::Integer(i128::from(signed)),
            Cbor::Null,
            Cbor::Null,
            Cbor::Bytes(vec![]),
        ]);
        assert_eq!(
            archive::frame(&v).unwrap().checksum,
            Some(u64::from_ne_bytes(signed.to_ne_bytes()))
        );
    }
    for outside in [i128::from(i64::MIN) - 1, i128::from(u64::MAX) + 1] {
        let v = Cbor::Array(vec![
            Cbor::Integer(6),
            Cbor::Integer(outside),
            Cbor::Null,
            Cbor::Null,
            Cbor::Bytes(vec![]),
        ]);
        assert!(archive::frame(&v).is_err());
    }
}
#[test]
fn continuation_order_checksum_missing_cycle_total_branch_and_cap() {
    let mut first = frame(vec![1]);
    first.index = Some(0);
    first.total = Some(3);
    first.next = vec!["b".into(), "c".into()];
    let mut b = frame(vec![2]);
    b.index = Some(1);
    let mut c = frame(vec![3]);
    c.index = Some(2);
    let mut rest = BTreeMap::from([("b".into(), b), ("c".into(), c)]);
    first.checksum = Some(crc::Crc::<u64>::new(&crc::CRC_64_GO_ISO).checksum(&[1, 2, 3]));
    assert_eq!(
        codec::checked_frame(&first, &rest).unwrap().0,
        vec![1, 2, 3]
    );
    assert!(
        archive::assemble(&first, &BTreeMap::new())
            .unwrap_err()
            .to_string()
            .starts_with("MISSING_")
    );
    rest.get_mut("b").unwrap().next.push("c".into());
    assert_eq!(
        archive::assemble(&first, &rest).unwrap_err().to_string(),
        "UNSUPPORTED_DATAFRAME_BRANCH"
    );
    rest.get_mut("b").unwrap().next.clear();
    rest.get_mut("c").unwrap().next.push("b".into());
    assert_eq!(
        archive::assemble(&first, &rest).unwrap_err().to_string(),
        "DATAFRAME_DUPLICATE_OR_CYCLE"
    );
    rest.get_mut("c").unwrap().next.clear();
    first.total = Some(4);
    assert_eq!(
        archive::assemble(&first, &rest).unwrap_err().to_string(),
        "DATAFRAME_INDEX_TOTAL"
    );
    assert_eq!(
        archive::assemble(&frame(vec![0; MAX_FRAME_BYTES + 1]), &BTreeMap::new())
            .unwrap_err()
            .to_string(),
        "DATAFRAME_BYTE_LIMIT"
    );
}
#[test]
fn zstd_truncation_trailing_garbage_amplification_and_raw_proto() {
    let valid = envelope(0).metadata.bytes;
    for len in [4, valid.len() / 2, valid.len() - 1] {
        assert!(codec::metadata_bytes(&valid[..len]).is_err());
    }
    let mut trailing = valid.clone();
    trailing.extend_from_slice(b"garbage");
    assert!(codec::metadata_bytes(&trailing).is_err());
    let large = zstd::stream::encode_all(vec![0; MAX_FRAME_BYTES + 1].as_slice(), 1).unwrap();
    assert_eq!(
        codec::metadata_bytes(&large).unwrap_err().to_string(),
        "ZSTD_OUTPUT_LIMIT"
    );
    assert_eq!(
        codec::metadata_bytes(&[0x10, 1]).unwrap(),
        (vec![0x10, 1], "PROTOBUF_UNCOMPRESSED")
    );
}
#[test]
fn invalid_inner_program_and_order_are_quarantined() {
    let mut e = envelope(0);
    let mut s = status(&e);
    s.inner_instructions_none = false;
    s.inner_instructions = vec![proto::InnerInstructions {
        index: 0,
        instructions: vec![proto::InnerInstruction {
            program_id_index: 999,
            accounts: vec![],
            data: vec![],
            stack_height: Some(2),
        }],
    }];
    set_status(&mut e, &s);
    assert_eq!(
        codec::decode(&e, &BTreeMap::new()).unwrap_err().to_string(),
        "INNER_PROGRAM_INDEX"
    );
    s.inner_instructions[0].instructions[0].program_id_index = 0;
    s.inner_instructions.push(s.inner_instructions[0].clone());
    set_status(&mut e, &s);
    assert_eq!(
        codec::decode(&e, &BTreeMap::new()).unwrap_err().to_string(),
        "INNER_INSTRUCTION_ORDER"
    );
}
#[test]
fn v0_lookup_addresses_resolve_from_same_status_not_rpc() {
    let mut e = envelope(0);
    let original: solana_transaction::versioned::VersionedTransaction =
        opts().deserialize(&e.data.bytes).unwrap();
    let solana_message::VersionedMessage::Legacy(m) = original.message else {
        panic!("legacy fixture")
    };
    let lookup = solana_message::v0::MessageAddressTableLookup {
        account_key: m.account_keys[0],
        writable_indexes: vec![0],
        readonly_indexes: vec![1],
    };
    let tx = solana_transaction::versioned::VersionedTransaction {
        signatures: original.signatures,
        message: solana_message::VersionedMessage::V0(solana_message::v0::Message {
            header: m.header,
            account_keys: m.account_keys,
            recent_blockhash: m.recent_blockhash,
            instructions: m.instructions,
            address_table_lookups: vec![lookup],
        }),
    };
    e.data.bytes = opts().serialize(&tx).unwrap();
    let mut s = status(&e);
    s.loaded_writable_addresses = vec![vec![7; 32]];
    s.loaded_readonly_addresses = vec![vec![8; 32]];
    s.pre_balances.extend([0, 0]);
    s.post_balances.extend([0, 0]);
    set_status(&mut e, &s);
    assert_eq!(
        codec::decode(&e, &BTreeMap::new()).unwrap()["version"],
        "V0"
    );
    s.loaded_readonly_addresses.clear();
    set_status(&mut e, &s);
    assert_eq!(
        codec::decode(&e, &BTreeMap::new()).unwrap_err().to_string(),
        "LOADED_ADDRESS_COUNTS"
    );
}
#[test]
fn deterministic_bytes_and_html_escaping() {
    let e = envelope(0);
    let a = serde_json::to_vec(&codec::decode(&e, &BTreeMap::new()).unwrap()).unwrap();
    let b = serde_json::to_vec(&codec::decode(&e, &BTreeMap::new()).unwrap()).unwrap();
    assert_eq!(a, b);
    let r = json!({"records":[{"transaction":{"signatures":["<script>evil</script>"],"program_ids":[]},"reason":"<img onerror=x>"}]});
    let html = report::html(&r);
    assert!(!html.contains("<script>"));
    assert!(!html.contains("<img onerror"));
    assert!(html.contains("UNAVAILABLE"));
    assert_eq!(html, report::html(&r));
}
#[test]
fn source_proto_hash_and_offline_graph_manifest() {
    let sources: Value = serde_json::from_str(include_str!("../sources.json")).unwrap();
    let source = sources["sources"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["path"] == "storage-proto/proto/confirmed_block.proto")
        .unwrap();
    assert_eq!(
        sha256(include_bytes!("../sources/confirmed_block.proto")),
        source["sha256"]
    );
    assert_eq!(source["commit"], "6c1ba34691f17ac902ae3d2e1147eed5723b9cef");
    let manifest = include_str!("../Cargo.toml");
    assert!(!manifest.contains("network-of1"));
    let lock = include_str!("../Cargo.lock");
    for forbidden in [
        "solana-rpc-client",
        "solana-signer",
        "reqwest",
        "hyper",
        "tokio",
        "rocksdb",
        "openssl",
    ] {
        assert!(!lock.contains(&format!("name = \"{forbidden}\"")));
    }
    assert_eq!(
        codec::PUMP_PROGRAM,
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    );
}

#[test]
fn missing_cpi_evidence_is_unknown_pump_involvement_not_false() {
    let mut e = envelope(0);
    let mut s = status(&e);
    s.inner_instructions_none = true;
    s.inner_instructions.clear();
    set_status(&mut e, &s);
    let decoded = codec::decode(&e, &BTreeMap::new()).unwrap();
    assert!(decoded["pump_program_involvement"].is_null());
    assert!(decoded["inner_instructions"].is_null());
}
#[test]
fn aggregate_budget_checks_exact_limit_and_overflow_before_publication() {
    let mut total = 0;
    report::charge(&mut total, 7, 10).unwrap();
    report::charge(&mut total, 3, 10).unwrap();
    assert_eq!(
        report::charge(&mut total, 1, 10).unwrap_err().to_string(),
        "BRONZE_AGGREGATE_LIMIT current=10 incoming=1 next=11 limit=10"
    );
    assert_eq!(total, 10);
    let mut maximum = usize::MAX;
    assert_eq!(
        report::charge(&mut maximum, 1, usize::MAX)
            .unwrap_err()
            .to_string(),
        format!(
            "BRONZE_AGGREGATE_LIMIT current={} incoming=1 limit={} overflow=true",
            usize::MAX,
            usize::MAX
        )
    );
    assert_eq!(maximum, usize::MAX);
}

#[test]
fn recorded_pilot_budget_crossing_stays_a_stop_not_a_limit_increase() {
    // Recorded accounting amounts, not fabricated transaction bytes or a new cap.
    assert_eq!(report::MAX_RECORD_JSON_BYTES, 16_777_216);
    assert_eq!(report::MAX_SELECTION_RECORD_BYTES, 50_331_648);
    let mut charged = 16_756_921;
    let error = report::charge(&mut charged, 49_607, report::MAX_RECORD_JSON_BYTES).unwrap_err();
    assert_eq!(
        error.to_string(),
        "BRONZE_AGGREGATE_LIMIT current=16756921 incoming=49607 next=16806528 limit=16777216"
    );
    assert_eq!(
        charged, 16_756_921,
        "failed admission must not advance accounting"
    );
}
