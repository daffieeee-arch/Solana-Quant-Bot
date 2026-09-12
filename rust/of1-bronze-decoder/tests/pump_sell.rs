//! Byte-exact authentic CAR section plus separately labelled synthetic mutations.
use of1_bronze_decoder::{
    archive::{self, Envelope},
    codec, pump,
    pump_sell::{self, Rejection},
    report,
};
use of1_range_recorder::sha256;
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn reference() -> Value {
    serde_json::from_str(include_str!("fixtures/pump-sell-reference.json")).unwrap()
}
fn transaction(which: usize) -> Value {
    let f: Value =
        serde_json::from_str(include_str!("fixtures/authentic-pump-sections.json")).unwrap();
    let v = &f["fixtures"][which];
    let raw = hex::decode(v["section_hex"].as_str().unwrap()).unwrap();
    assert_eq!(sha256(&raw), v["section_sha256"]);
    let start = raw.iter().position(|b| b & 128 == 0).unwrap() + 1;
    let cid = &raw[start..start + 36];
    assert_eq!(hex::encode(cid), v["cid_hex"]);
    assert_eq!(sha256(&raw[start + 36..]), hex::encode(&cid[4..]));
    let C::Array(a) = serde_cbor::from_slice(&raw[start + 36..]).unwrap() else {
        panic!("array")
    };
    let env = Envelope {
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
    };
    codec::decode(&env, &BTreeMap::new()).unwrap()
}
fn diagnosis(tx: &Value) -> Value {
    pump_sell::inspect(tx).unwrap().remove(0)
}
fn bytes(key: &str) -> Vec<u8> {
    hex::decode(reference()[key].as_str().unwrap()).unwrap()
}
fn replace_event(tx: &mut Value, data: &[u8]) {
    tx["inner_instructions"][2]["data_hex"] = json!(hex::encode(data));
}
fn offset(field: &str) -> usize {
    usize::try_from(reference()["event_offsets"][field][0].as_u64().unwrap()).unwrap()
}
fn reject(tx: &Value) {
    let d = diagnosis(tx);
    assert_eq!(d["disposition"], "NOT_ADMITTED", "{d}");
    assert!(d["selected_candidate"].is_null());
    assert_eq!(d["matching_registered_candidates"], json!([]));
}

#[test]
fn authentic_153_full_instruction_17_accounts_and_all_event_fields_match_sealed_reference() {
    let tx = transaction(1);
    let r = reference();
    let d = diagnosis(&tx);
    assert_eq!(d["disposition"], "MATCHED_RECORDED_EVENT_FACTS");
    assert_eq!(d["instruction_data_hex"], r["instruction_hex"]);
    assert_eq!(d["instruction_sha256"], r["instruction_sha256"]);
    assert_eq!(d["instruction_bytes"], 24);
    assert_eq!(d["instruction"]["amount_raw_u64"], "26761699489951");
    assert_eq!(d["instruction"]["min_sol_output_raw_u64"], "650823659");
    assert_eq!(d["event_context"]["event_cpi_sha256"], r["event_sha256"]);
    assert_eq!(d["event_context"]["event_cpi_bytes"], 367);
    assert_eq!(d["event_context"]["outer_index"], 2);
    assert_eq!(d["event_context"]["inner_order"], 2);
    assert_eq!(d["event_context"]["stack_height"], 2);
    for (k, v) in r["event_fields"].as_object().unwrap() {
        assert_eq!(&d["event_reported"][k], v, "{k}");
    }
    let rows = d["accounts"].as_array().unwrap();
    assert_eq!(rows.len(), 17);
    for (i, row) in rows.iter().enumerate() {
        assert_eq!(row["observed"], r["accounts"][i]);
        assert_eq!(row["address_match"], true);
        assert_eq!(row["required_privileges_match"], true);
    }
    for (pos, p) in r["pda_checks"].as_object().unwrap() {
        let row = &rows[pos.parse::<usize>().unwrap()];
        assert_eq!(row["expected"], p["address"]);
        assert_eq!(row["pda_bump"], p["bump"]);
    }
    assert_eq!(
        d["matching_registered_candidates"],
        json!([pump_sell::CANDIDATE])
    );
    assert_eq!(d, diagnosis(&tx));
}

#[test]
fn source_receipt_binds_exact_original_source_and_declared_scope() {
    let s: Value = serde_json::from_slice(pump_sell::SOURCE).unwrap();
    assert_eq!(s["sources"][0]["sha256"], reference()["source_idl_sha256"]);
    assert_eq!(s["sources"][0]["commit"], reference()["source_commit"]);
    assert_eq!(
        s["instruction"]["discriminator_hex"],
        sha256(b"global:sell")[..16]
    );
    assert_eq!(s["instruction"]["bytes"], 24);
    assert_eq!(
        s["instruction"]["fields"],
        json!([{"name":"amount","type":"u64"},{"name":"min_sol_output","type":"u64"}])
    );
    assert_eq!(s["account_rules"].as_array().unwrap().len(), 17);
    assert_eq!(s["event"]["required_is_buy"], false);
    assert_eq!(s["event"]["required_ix_name"], "sell");
    assert_eq!(s["candidate_id"], pump_sell::CANDIDATE);
}

#[test]
fn synthetic_instruction_every_truncation_suffix_discriminator_and_integer_boundaries() {
    let original = bytes("instruction_hex");
    for n in 0..24 {
        assert!(pump_sell::decode_instruction(&original[..n]).is_err());
    }
    for byte in [0, 1, 255] {
        let mut b = original.clone();
        b.push(byte);
        assert_eq!(
            pump_sell::decode_instruction(&b),
            Err(Rejection::TrailingInstruction)
        );
    }
    let mut b = original.clone();
    b[0] ^= 1;
    assert_eq!(
        pump_sell::decode_instruction(&b),
        Err(Rejection::WrongDiscriminator)
    );
    for amount in [0, u64::MAX] {
        for min in [0, u64::MAX] {
            let mut b = original.clone();
            b[8..16].copy_from_slice(&amount.to_le_bytes());
            b[16..24].copy_from_slice(&min.to_le_bytes());
            let i = pump_sell::decode_instruction(&b).unwrap();
            assert_eq!((i.amount, i.min_sol_output), (amount, min));
        }
    }
}

#[test]
fn synthetic_event_truncations_both_discriminators_bool_string_vec_and_exhaustion() {
    let original = bytes("event_hex");
    for n in 0..original.len() {
        assert!(pump_sell::decode_event(&original[..n]).is_err(), "{n}");
    }
    for offset in [0, 8] {
        let mut b = original.clone();
        b[offset] ^= 1;
        assert!(pump_sell::decode_event(&b).is_err());
    }
    let mut b = original.clone();
    b.push(0);
    assert!(matches!(
        pump_sell::decode_event(&b),
        Err(Rejection::TrailingEvent)
    ));
    for field in ["is_buy", "track_volume", "mayhem_mode"] {
        let mut b = original.clone();
        b[offset(field)] = 2;
        assert!(pump_sell::decode_event(&b).is_err());
    }
    for field in ["ix_name", "shareholders"] {
        let mut b = original.clone();
        b[offset(field)..offset(field) + 4].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(pump_sell::decode_event(&b).is_err());
    }
    let mut b = original.clone();
    b[offset("ix_name") + 4] = 255;
    assert!(pump_sell::decode_event(&b).is_err());
    let mut b = original;
    b[offset("is_buy")] = 1;
    assert!(matches!(
        pump_sell::decode_event(&b),
        Err(Rejection::UnsupportedEventVariant)
    ));
}

#[test]
fn synthetic_event_exact_u64_i64_limits_remain_integers() {
    for value in [0, u64::MAX] {
        let mut b = bytes("event_hex");
        let pos = offset("sol_amount");
        b[pos..pos + 8].copy_from_slice(&value.to_le_bytes());
        assert_eq!(pump_sell::decode_event(&b).unwrap().sol_amount, value);
    }
    for value in [i64::MIN, -1, 0, i64::MAX] {
        let mut b = bytes("event_hex");
        let pos = offset("timestamp");
        b[pos..pos + 8].copy_from_slice(&value.to_le_bytes());
        assert_eq!(pump_sell::decode_event(&b).unwrap().timestamp, value);
    }
}

#[test]
fn synthetic_each_account_replacement_is_not_an_admitted_candidate() {
    for pos in 0..17 {
        let mut tx = transaction(1);
        let index = usize::try_from(
            tx["instructions"][2]["account_indexes"][pos]
                .as_u64()
                .unwrap(),
        )
        .unwrap();
        tx["account_keys"][index] = json!(bs58::encode([77; 32]).into_string());
        reject(&tx);
    }
    for size in [0, 16, 18] {
        let mut tx = transaction(1);
        tx["instructions"][2]["account_indexes"]
            .as_array_mut()
            .unwrap()
            .resize(size, json!(0));
        reject(&tx);
    }
    for index in [json!(999), json!(-1), Value::Null] {
        let mut tx = transaction(1);
        tx["instructions"][2]["account_indexes"][0] = index;
        reject(&tx);
    }
}

#[test]
fn synthetic_missing_and_invalid_loaded_message_privileges_fail() {
    for field in [
        "required_signatures",
        "static_keys",
        "loaded_writable",
        "loaded_readonly",
        "readonly_unsigned",
    ] {
        let mut tx = transaction(1);
        tx["message_account_layout"][field] = json!(999);
        reject(&tx);
    }
    let mut tx = transaction(1);
    tx["message_account_layout"] = Value::Null;
    reject(&tx);
    let mut tx = transaction(1);
    tx["message_account_layout"]["required_signatures"] = json!(0);
    reject(&tx);
    let mut tx = transaction(1);
    tx["message_account_layout"]["readonly_unsigned"] = json!(7);
    reject(&tx);
    let mut tx = transaction(1);
    tx["message_account_layout"]["readonly_unsigned"] = json!(5);
    assert_eq!(
        diagnosis(&tx)["disposition"],
        "MATCHED_RECORDED_EVENT_FACTS"
    );
}

#[test]
fn synthetic_event_missing_duplicate_other_outer_authority_or_stack_fails() {
    let mut tx = transaction(1);
    tx["inner_instructions"] = Value::Null;
    reject(&tx);
    let mut tx = transaction(1);
    tx["inner_instructions"].as_array_mut().unwrap().remove(2);
    reject(&tx);
    let mut tx = transaction(1);
    let event = tx["inner_instructions"][2].clone();
    tx["inner_instructions"].as_array_mut().unwrap().push(event);
    reject(&tx);
    for height in [Value::Null, json!(1), json!(3), json!(9)] {
        let mut tx = transaction(1);
        tx["inner_instructions"][2]["stack_height"] = height;
        reject(&tx);
    }
    let mut tx = transaction(1);
    tx["inner_instructions"][0]["stack_height"] = Value::Null;
    reject(&tx);
    let mut tx = transaction(1);
    tx["inner_instructions"][2]["outer_index"] = json!(3);
    reject(&tx);
    let mut tx = transaction(1);
    tx["inner_instructions"][2]["account_indexes"] = json!([0]);
    reject(&tx);
    let mut tx = transaction(1);
    tx["inner_instructions"][2]["program_id_index"] = json!(0);
    reject(&tx);
}

#[test]
fn synthetic_event_mint_user_amount_or_selected_variant_mismatch_fails() {
    for field in [
        "mint",
        "user",
        "token_amount",
        "mayhem_mode",
        "track_volume",
    ] {
        let mut tx = transaction(1);
        let mut b = bytes("event_hex");
        b[offset(field)] ^= 1;
        replace_event(&mut tx, &b);
        reject(&tx);
    }
    let mut tx = transaction(1);
    let mut b = bytes("event_hex");
    b[offset("ix_name") + 4..offset("ix_name") + 8].copy_from_slice(b"nope");
    replace_event(&mut tx, &b);
    reject(&tx);
}

#[test]
fn synthetic_failed_unknown_status_or_error_metadata_cannot_admit() {
    for status in [json!("ERROR"), Value::Null, json!("UNKNOWN")] {
        let mut tx = transaction(1);
        tx["status"] = status;
        reject(&tx);
    }
    let mut tx = transaction(1);
    tx["transaction_error"] = json!({"error":"InstructionError"});
    reject(&tx);
}

#[test]
fn authentic_second_direct_and_nested_selection_does_not_use_caller_count() {
    assert_eq!(
        diagnosis(&transaction(2))["disposition"],
        "MATCHED_RECORDED_EVENT_FACTS"
    );
    for i in [3, 4] {
        let mut tx = transaction(i);
        tx["compatible_candidate_count"] = json!(0);
        let d = diagnosis(&tx);
        assert_eq!(d["disposition"], "MATCHED_RECORDED_EVENT_FACTS");
        tx["inner_instructions"][0]["stack_height"] = Value::Null;
        tx["compatible_candidate_count"] = json!(1);
        reject(&tx);
    }
}

#[test]
fn original_buy_diagnosis_and_all_b3_probes_remain_unchanged() {
    let tx = transaction(0);
    assert!(pump_sell::inspect(&tx).unwrap().is_empty());
    let p = pump::inspect(&tx).unwrap();
    assert_eq!(
        p["buy_source_diagnostics"][0]["full_instruction_error"]["reason"],
        "UNEXPECTED_TRAILING_BYTES"
    );
    assert_eq!(
        p["buy_source_diagnostics"][0]["unexplained_suffix"]["hex"],
        "01"
    );
    assert_eq!(p["silver"], "NOT_PRODUCED");
    let b3 =
        pump_protocol_v2::decode::probe_trade_event_cpi_layout(&bytes("event_hex")).unwrap_err();
    assert_eq!(
        serde_json::to_value(b3).unwrap()["reason"],
        "UNSUPPORTED_VARIANT"
    );
}

fn synthetic_record() -> Value {
    json!({"disposition":"DECODED","transaction":transaction(1),"atomic_observation_package":true,"source":{"raw_sha256":reference()["raw_sha256"],"transaction_node_cid_hex":reference()["raw_cid_hex"],"bindings":{"fixture":"SYNTHETIC_BINDING_ONLY"}},"decoder_source_sha256":"synthetic-test","effective_at":{"slot":"422496004","transaction_index_in_slot":153},"input_kind":"SYNTHETIC_RECORD_WITH_AUTHENTIC_WIRE","receipt_evidence":"Fixture"})
}

#[test]
fn silver_binds_whole_bronze_package_and_preserves_all_unknowns_and_named_fee_semantics() {
    let r = synthetic_record();
    let facts = pump_sell::facts(&r).unwrap();
    assert_eq!(facts.len(), 1);
    let f = &facts[0];
    assert_eq!(
        f["bronze_record_sha256"],
        sha256(&serde_json::to_vec(&r).unwrap())
    );
    assert_eq!(f["source"], r["source"]);
    assert_eq!(f["effective_at"], r["effective_at"]);
    assert_eq!(f["transaction_status"], "OK");
    assert_eq!(f["transaction_fee_lamports"], "7505017");
    assert_eq!(f["event_reported"]["fee_raw_u64"], "8921826");
    assert_eq!(f["event_reported"]["quote_mint_raw_hex"], "00".repeat(32));
    assert_eq!(f["quote_mint_identity"], "UNKNOWN");
    for key in [
        "quote_decimals",
        "base_decimals",
        "name",
        "ticker",
        "launch_at",
        "executable_price",
        "net_proceeds",
        "observed_at",
        "actionable_at",
        "execution_opportunity_at",
    ] {
        assert!(f[key].is_null(), "{key}");
    }
    assert_eq!(f["committed_account_state"], "NOT_ESTABLISHED");
    assert_eq!(f["historical_activation"], "UNKNOWN");
    assert_eq!(f["receipt_evidence"], "Fixture");
    assert_eq!(f["account_contents_verified"], false);
    assert_eq!(f["research_ready"], false);
    assert_eq!(f["atomic_observation_package"], true);
    assert_eq!(facts, pump_sell::facts(&r).unwrap());
}

#[test]
fn missing_provenance_or_unpaired_package_cannot_publish_silver() {
    for key in ["raw_sha256", "bindings", "transaction_node_cid_hex"] {
        let mut r = synthetic_record();
        r["source"][key] = Value::Null;
        assert!(pump_sell::facts(&r).is_err());
    }
    let mut r = synthetic_record();
    r["atomic_observation_package"] = json!(false);
    assert!(pump_sell::facts(&r).is_err());
    let mut r = synthetic_record();
    r["disposition"] = json!("QUARANTINED");
    assert!(pump_sell::facts(&r).unwrap().is_empty());
}

#[test]
fn visible_report_distinguishes_supported_sell_but_keeps_buy_rejection_and_economic_limits() {
    let mut r = synthetic_record();
    r["transaction"]["pump_sell_analysis"] = json!(pump_sell::inspect(&r["transaction"]).unwrap());
    let output = json!({"records":[r.clone()],"silver_records":pump_sell::facts(&r).unwrap()});
    let html = report::html(&output);
    assert!(html.contains("1 gekoppelde instructie/event-pakketten"));
    assert!(html.contains("26761699489951"));
    assert!(html.contains("17 accountposities"));
    assert!(html.contains("geen gelezen accounttoestand"));
    assert!(html.contains("silver.jsonl"));
    assert_eq!(html, report::html(&output));
}
