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

fn buy_diagnostic(tx: &Value) -> Value {
    of1_bronze_decoder::pump_buy::inspect(tx).unwrap().remove(0)
}

fn event_index(tx: &Value) -> usize {
    tx["inner_instructions"]
        .as_array()
        .unwrap()
        .iter()
        .position(|v| v["outer_index"] == 3 && v["inner_order"] == 7)
        .unwrap()
}

#[test]
fn authentic_buy_all_26_bytes_18_accounts_and_direct_event_without_silver() {
    let tx = transaction(0);
    let d = buy_diagnostic(&tx);
    assert_eq!(
        d["full_data_hex"],
        "66063d1201daebea07af1c6fd9000000c0fc9b01000000000101"
    );
    assert_eq!(
        d["full_data_sha256"],
        "8fb2c1427bf529c080cd5882b2bc1c48f523f3173cf6ea52a705071084216874"
    );
    assert_eq!(d["full_instruction_layout_match"], false);
    assert_eq!(
        d["full_instruction_error"]["reason"],
        "UNEXPECTED_TRAILING_BYTES"
    );
    assert_eq!(
        d["unexplained_suffix"],
        json!({"offset":25,"hex":"01","semantics":"UNKNOWN","discarded":false})
    );
    assert_eq!(d["prefix_diagnostic"]["amount_raw_u64"], "933872054023");
    assert_eq!(d["prefix_diagnostic"]["max_sol_cost_raw_u64"], "27000000");
    assert_eq!(d["prefix_diagnostic"]["track_volume"], true);
    assert_eq!(d["account_count"], 18);
    assert_eq!(d["account_address_correspondence"], true);
    assert_eq!(d["accounts"][16]["role"], "bonding_curve_v2");
    assert_eq!(d["accounts"][17]["role"], "buyback_fee_recipient");
    assert_eq!(
        d["accounts"][17]["observed"],
        "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD"
    );
    for key in [
        "mint_matches",
        "user_matches",
        "token_amount_matches_prefix",
        "track_volume_matches_prefix",
        "buy_representation",
    ] {
        assert_eq!(d["event_correlation"][key], true, "{key}");
    }
    assert_eq!(d["event_context"]["outer_index"], 3);
    assert_eq!(d["event_context"]["inner_order"], 7);
    assert_eq!(
        d["event_context"]["event_sha256"],
        "b42b7ea3175d3b53e8c71e1f158f35629be8b79f064b25a1eae96c71fab019f6"
    );
    assert_eq!(d["silver"], "NOT_PRODUCED");
    assert_eq!(d["account_contents_verified"], false);
    assert_eq!(d["committed_state"], "NOT_ESTABLISHED");
    assert_eq!(d, buy_diagnostic(&tx));
}

#[test]
fn authentic_pdas_match_separate_sealed_web3_reference() {
    let reference: Value =
        serde_json::from_str(include_str!("fixtures/pump-buy-account-reference.json")).unwrap();
    let d = buy_diagnostic(&transaction(0));
    for check in reference["checks"].as_array().unwrap() {
        let i = usize::try_from(check["position"].as_u64().unwrap()).unwrap();
        assert_eq!(d["accounts"][i]["expected"], check["expected"]);
        assert_eq!(d["accounts"][i]["observed"], check["observed"]);
        assert_eq!(d["accounts"][i]["pda_bump"], check["bump"]);
    }
}

#[test]
fn synthetic_suffix_variants_are_never_silently_discarded_or_admitted() {
    for tail in ["00", "01", "ff", "0100", ""] {
        let mut tx = transaction(0);
        let original = tx["instructions"][3]["data_hex"].as_str().unwrap();
        tx["instructions"][3]["data_hex"] = json!(format!("{}{tail}", &original[..50]));
        let d = buy_diagnostic(&tx);
        assert_eq!(d["unexplained_suffix"]["hex"], tail);
        assert_eq!(d["full_instruction_layout_match"], tail.is_empty());
        assert_eq!(d["silver"], "NOT_PRODUCED");
        assert_eq!(d["admission"], "NOT_ADMITTED_DIAGNOSTIC_ONLY");
    }
}

#[test]
fn synthetic_truncated_or_invalid_bool_has_no_prefix_interpretation() {
    for data in [
        "66063d1201daebea",
        "66063d1201daebea07af1c6fd9000000c0fc9b01000000000201",
    ] {
        let mut tx = transaction(0);
        tx["instructions"][3]["data_hex"] = json!(data);
        let d = buy_diagnostic(&tx);
        assert!(d["prefix_diagnostic"].is_null());
        assert_eq!(d["full_instruction_layout_match"], false);
        assert_eq!(d["silver"], "NOT_PRODUCED");
    }
}

#[test]
fn synthetic_wrong_count_duplicate_or_unresolved_account_fails_pattern() {
    for indexes in [
        json!([17, 3, 15, 2, 5, 4, 0, 9, 12, 7, 20, 10, 8, 6, 18, 14, 16]),
        json!([
            17, 3, 15, 2, 5, 4, 0, 9, 12, 7, 20, 10, 8, 6, 18, 14, 16, 1, 1
        ]),
        json!([
            17, 3, 15, 2, 5, 4, 0, 9, 12, 7, 20, 10, 8, 6, 18, 14, 16, 16
        ]),
        json!([
            17, 3, 15, 2, 5, 4, 0, 9, 12, 7, 20, 10, 8, 6, 18, 14, 16, 255
        ]),
    ] {
        let mut tx = transaction(0);
        tx["instructions"][3]["account_indexes"] = indexes;
        assert_eq!(buy_diagnostic(&tx)["account_address_correspondence"], false);
    }
}

#[test]
fn synthetic_each_account_mutation_breaks_correspondence_not_transaction_decode() {
    for position in 0..18 {
        let mut tx = transaction(0);
        let index = usize::try_from(
            tx["instructions"][3]["account_indexes"][position]
                .as_u64()
                .unwrap(),
        )
        .unwrap();
        tx["account_keys"][index] = json!(bs58::encode([42; 32]).into_string());
        assert_eq!(
            buy_diagnostic(&tx)["account_address_correspondence"],
            false,
            "position {position}"
        );
    }
}

#[test]
fn synthetic_missing_or_insufficient_message_privileges_remain_unknown_or_false() {
    for header in [
        Value::Null,
        json!({"static_keys":21,"required_signatures":0,"readonly_signed":0,"readonly_unsigned":12,"loaded_writable":0,"loaded_readonly":0}),
        json!({"static_keys":21,"required_signatures":1,"readonly_signed":0,"readonly_unsigned":20,"loaded_writable":0,"loaded_readonly":0}),
        json!({"static_keys":20,"required_signatures":1,"readonly_signed":0,"readonly_unsigned":12,"loaded_writable":0,"loaded_readonly":0}),
    ] {
        let mut tx = transaction(0);
        tx["message_account_layout"] = header;
        assert_eq!(buy_diagnostic(&tx)["account_address_correspondence"], false);
    }
}

#[test]
fn synthetic_globally_writable_idl_readonly_is_not_falsely_rejected() {
    let mut tx = transaction(0);
    tx["message_account_layout"]["readonly_unsigned"] = json!(0);
    assert_eq!(buy_diagnostic(&tx)["account_address_correspondence"], true);
}

#[test]
fn synthetic_event_missing_duplicate_wrong_parent_or_authority_is_unavailable() {
    for mode in 0..6 {
        let mut tx = transaction(0);
        let event = event_index(&tx);
        match mode {
            0 => tx["inner_instructions"] = Value::Null,
            1 => {
                let e = tx["inner_instructions"][event].clone();
                tx["inner_instructions"].as_array_mut().unwrap().push(e);
            }
            2 => tx["inner_instructions"][event]["stack_height"] = json!(3),
            3 => tx["inner_instructions"][event]["outer_index"] = json!(2),
            4 => tx["inner_instructions"][event]["account_indexes"] = json!([0]),
            _ => {
                let first = tx["inner_instructions"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .position(|v| v["outer_index"] == 3)
                    .unwrap();
                tx["inner_instructions"][first]["stack_height"] = Value::Null;
            }
        }
        let d = buy_diagnostic(&tx);
        assert!(d["event_context"].is_null(), "mode {mode}");
        assert!(d["event_association_failure"].is_string());
        assert!(d["event_correlation"]["mint_matches"].is_null());
        assert_eq!(d["silver"], "NOT_PRODUCED");
    }
}

#[test]
fn synthetic_event_fields_mismatch_is_visible_and_not_a_state_fact() {
    // Source-defined offsets after the event-CPI tag and TradeEvent discriminator.
    for (offset, key) in [
        (16, "mint_matches"),
        (65, "user_matches"),
        (56, "token_amount_matches_prefix"),
        (233, "track_volume_matches_prefix"),
    ] {
        let mut tx = transaction(0);
        let event = event_index(&tx);
        let mut bytes = hex::decode(
            tx["inner_instructions"][event]["data_hex"]
                .as_str()
                .unwrap(),
        )
        .unwrap();
        bytes[offset] ^= 1;
        tx["inner_instructions"][event]["data_hex"] = json!(hex::encode(bytes));
        let d = buy_diagnostic(&tx);
        assert_eq!(d["event_correlation"][key], false, "{key}");
        assert_eq!(d["silver"], "NOT_PRODUCED");
    }
    let mut tx = transaction(0);
    tx["status"] = json!("ERROR");
    let d = buy_diagnostic(&tx);
    assert_eq!(d["event_correlation"]["transaction_status_ok"], false);
    assert_eq!(d["committed_state"], "NOT_ESTABLISHED");
}

#[test]
fn source_receipt_retains_25_byte_idl_and_separate_remaining_account_document() {
    let s: Value = serde_json::from_slice(of1_bronze_decoder::pump_buy::SOURCE).unwrap();
    assert_eq!(
        s["sources"][0]["sha256"],
        "b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49"
    );
    assert_eq!(
        s["sources"][2]["sha256"],
        "8b05e0906eaf1746508b0b2f909ac7c344ce7be33a6564d595335c9fda399868"
    );
    assert_eq!(s["instruction_contract"]["bytes"], 25);
    assert_eq!(s["instruction_contract"]["extra_byte_semantics"], "UNKNOWN");
    assert_eq!(s["account_rules"].as_array().unwrap().len(), 18);
    assert_eq!(s["idl_history"].as_array().unwrap().len(), 15);
    assert_eq!(
        s["client"]["git_head_status"],
        "REGISTRY_DECLARED_GITHUB_COMMIT_NOT_RETRIEVABLE_404"
    );
}

#[test]
fn report_shows_original_rejection_all_accounts_and_unknown_suffix() {
    let mut tx = transaction(0);
    tx["pump_structural_analysis"] = pump::inspect(&tx).unwrap();
    let record = json!({"disposition":"DECODED","effective_at":{"slot":"422496004","transaction_index_in_slot":142},"transaction":tx});
    let report = json!({"analysis":pump::summary(&[record])});
    let html = of1_bronze_decoder::report::html(&report);
    for text in [
        "volledige bytes, accounts en bewijsgrens",
        "UNEXPECTED_TRAILING_BYTES",
        "bonding_curve_v2",
        "buyback_fee_recipient",
        "Onverklaarde suffix",
        "Geen Silver",
    ] {
        assert!(html.contains(text), "{text}");
    }
    assert_eq!(html, of1_bronze_decoder::report::html(&report));
}
