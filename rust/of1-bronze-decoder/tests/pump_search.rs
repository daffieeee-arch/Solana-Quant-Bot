//! Exact authentic sections versus explicitly synthetic context perturbations.
use of1_bronze_decoder::{
    archive::{self, Envelope},
    codec, pump,
};
use of1_range_recorder::sha256;
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/authentic-pump-sections.json")).unwrap()
}
fn transaction(i: usize) -> Value {
    let f = fixture();
    let v = &f["fixtures"][i];
    let bytes = hex::decode(v["section_hex"].as_str().unwrap()).unwrap();
    assert_eq!(sha256(&bytes), v["section_sha256"]);
    let start = bytes.iter().position(|b| b & 128 == 0).unwrap() + 1;
    let cid = &bytes[start..start + 36];
    assert_eq!(hex::encode(cid), v["cid_hex"]);
    assert_eq!(sha256(&bytes[start + 36..]), hex::encode(&cid[4..]));
    let C::Array(a) = serde_cbor::from_slice(&bytes[start + 36..]).unwrap() else {
        panic!("array")
    };
    let env = Envelope {
        cid_hex: hex::encode(cid),
        physical_node_index: 0,
        raw_offset: usize::try_from(v["raw_offset"].as_u64().unwrap()).unwrap(),
        raw_length: bytes.len(),
        entry_index: 0,
        transaction_index_in_entry: 0,
        transaction_index_in_slot: usize::try_from(
            v["order"]["transaction_index_in_slot"].as_u64().unwrap(),
        )
        .unwrap(),
        source_transaction_index: None,
        data: archive::frame(&a[1]).unwrap(),
        metadata: archive::frame(&a[2]).unwrap(),
    };
    let tx = codec::decode(&env, &BTreeMap::new()).unwrap();
    for (k, expected) in v["expected"].as_object().unwrap() {
        assert_eq!(&tx[k], expected, "{k}");
    }
    tx
}

#[test]
fn five_authentic_pairs_keep_real_bytes_one_event_layout_match_is_not_silver() {
    for i in 0..5 {
        let tx = transaction(i);
        assert_eq!(tx["pump_program_involvement"], true);
        let p = pump::inspect(&tx).unwrap();
        let o = p["observations"].as_array().unwrap();
        assert_eq!(o.len(), 2);
        assert_eq!(
            o[0]["layout_error"]["reason"],
            if i == 0 {
                "UNEXPECTED_TRAILING_BYTES"
            } else {
                "WRONG_DISCRIMINATOR"
            }
        );
        assert_eq!(o[0]["bytes"], if i == 0 { 26 } else { 24 });
        assert_eq!(o[1]["bytes"], if i == 0 { 366 } else { 367 });
        assert_eq!(o[1]["context"]["parent"]["program_id"], codec::PUMP_PROGRAM);
        if i == 0 {
            assert_eq!(o[1]["layout_outcome"], "LAYOUT_COMPATIBLE_ONLY");
            assert!(o[1]["layout_error"].is_null());
            assert_eq!(o[1]["structural_fields"]["ix_name"], "buy");
            assert!(o[1]["structural_fields"]["quote_decimals"].is_null());
            assert_eq!(
                o[1]["structural_fields"]["reserve_role"],
                "EVENT_REPORTED_NOT_ACCOUNT_STATE"
            );
        } else {
            assert_eq!(o[1]["layout_error"]["reason"], "UNSUPPORTED_VARIANT");
        }
        assert_eq!(
            p["candidate_selection"],
            "NOT_PERFORMED_NO_CALLER_SUPPLIED_COUNT"
        );
        assert_eq!(p["silver"], "NOT_PRODUCED");
        assert_eq!(p["committed_pump_state"], "NOT_ESTABLISHED");
        assert_eq!(p, pump::inspect(&tx).unwrap());
    }
}

#[test]
fn nested_cpi_parentage_is_recorded_not_guessed_from_top_level() {
    let p = pump::inspect(&transaction(3)).unwrap();
    assert_eq!(
        p["observations"][0]["context"]["parent"]["program_id"],
        "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9"
    );
    assert_eq!(p["observations"][1]["context"]["parent"]["inner_order"], 0);
    assert_eq!(p["observations"][1]["context"]["stack_height"], 3);
}

#[test]
fn synthetic_missing_heights_and_impossible_jump_never_fabricate_parentage() {
    for value in [Value::Null, json!(9), json!(1)] {
        let mut tx = transaction(3);
        tx["inner_instructions"][0]["stack_height"] = value;
        let p = pump::inspect(&tx).unwrap();
        for o in p["observations"].as_array().unwrap() {
            assert!(o["context"]["parent"].is_null());
        }
        assert_eq!(p["silver"], "NOT_PRODUCED");
    }
}

#[test]
fn synthetic_nonpump_program_with_identical_bytes_is_not_a_pump_match() {
    let mut tx = transaction(0);
    for field in ["instructions", "inner_instructions"] {
        for ix in tx[field].as_array_mut().unwrap() {
            ix["program_id"] = json!("11111111111111111111111111111111");
        }
    }
    assert!(
        pump::inspect(&tx).unwrap()["observations"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}

#[test]
fn synthetic_missing_cpi_failed_transaction_and_declared_instruction_do_not_prove_state() {
    let mut tx = transaction(0);
    tx["status"] = json!("ERROR");
    tx["inner_instructions"] = Value::Null;
    let p = pump::inspect(&tx).unwrap();
    assert_eq!(p["cpi_coverage"], "UNAVAILABLE");
    assert_eq!(p["transaction_status"], "ERROR");
    assert_eq!(p["observations"].as_array().unwrap().len(), 1);
    assert_eq!(
        p["observations"][0]["context"]["executed"],
        "NOT_ESTABLISHED_BY_DECLARATION"
    );
    assert_eq!(p["committed_pump_state"], "NOT_ESTABLISHED");
}

#[test]
fn source_pin_is_exact_and_changed_hash_rejected() {
    use pump_protocol_v2::registry::{SourceManifest, validate_source_manifest};
    let mut source: SourceManifest = serde_json::from_slice(pump::SOURCE).unwrap();
    validate_source_manifest(&source).unwrap();
    source.sha256 = "f".repeat(64);
    assert!(validate_source_manifest(&source).is_err());
}

#[test]
fn frequencies_count_presence_top_and_cpi_separately_without_filtering_failures() {
    let mut tx = transaction(0);
    tx["pump_structural_analysis"] = pump::inspect(&tx).unwrap();
    let good = json!({"disposition":"DECODED","transaction":tx});
    let mut failed = good.clone();
    failed["transaction"]["status"] = json!("ERROR");
    let s = pump::summary(&[good, failed]);
    assert_eq!(s["transaction_status_counts"], json!({"OK":1,"ERROR":1}));
    let p = s["program_frequencies"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["program_id"] == codec::PUMP_PROGRAM)
        .unwrap();
    assert_eq!(p["transaction_presence"], 2);
    assert_eq!(p["declared_top_level_references"], 2);
    assert_eq!(p["recorded_cpi_references"], 2);
}
