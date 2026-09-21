//! Three authentic sealed CAR sections; negative mutations are synthetic only.
//! No fixture modifies original Raw/receipts or invents an admissible profile.
use of1_bronze_decoder::{
    archive::{self, Envelope},
    codec, pump, pump_nested_buy, pump_sell,
};
use of1_range_recorder::sha256;
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn fixture(n: usize) -> Value {
    serde_json::from_str::<Value>(include_str!("fixtures/authentic-nested-buy-sections.json"))
        .unwrap()["fixtures"][n]
        .clone()
}
fn tx(n: usize) -> Value {
    let f = fixture(n);
    let raw = hex::decode(f["section_hex"].as_str().unwrap()).unwrap();
    assert_eq!(sha256(&raw), f["section_sha256"]);
    assert_eq!(
        raw.len(),
        usize::try_from(f["section_length"].as_u64().unwrap()).unwrap()
    );
    let start = raw.iter().position(|b| b & 128 == 0).unwrap() + 1;
    let cid = &raw[start..start + 36];
    assert_eq!(hex::encode(cid), f["cid_hex"]);
    assert_eq!(sha256(&raw[start + 36..]), hex::encode(&cid[4..]));
    let C::Array(a) = serde_cbor::from_slice(&raw[start + 36..]).unwrap() else {
        panic!("array")
    };
    assert_eq!(a.len(), 5);
    assert_eq!(a[0], C::Integer(0));
    assert_eq!(
        a[3],
        C::Integer(f["slot"].as_str().unwrap().parse::<i128>().unwrap())
    );
    assert_eq!(
        a[4],
        C::Integer(i128::from(f["transaction_index_in_slot"].as_u64().unwrap()))
    );
    let n = |k| usize::try_from(f[k].as_u64().unwrap()).unwrap();
    codec::decode(
        &Envelope {
            cid_hex: hex::encode(cid),
            physical_node_index: n("physical_node_index"),
            raw_offset: n("raw_offset"),
            raw_length: raw.len(),
            entry_index: n("entry_index"),
            transaction_index_in_entry: 0,
            transaction_index_in_slot: n("transaction_index_in_slot"),
            source_transaction_index: None,
            data: archive::frame(&a[1]).unwrap(),
            metadata: archive::frame(&a[2]).unwrap(),
        },
        &BTreeMap::new(),
    )
    .unwrap()
}
fn diagnosis(tx: &Value) -> Value {
    let d = pump_nested_buy::inspect(tx).unwrap().remove(0);
    assert_eq!(d["disposition"], "NOT_ADMITTED");
    assert_eq!(d["silver"], "NOT_PRODUCED");
    assert_eq!(d["matching_registered_candidates"], json!([]));
    assert!(d["selected_candidate"].is_null());
    assert_eq!(d["research_ready"], false);
    d
}
fn gap(d: &Value, name: &str) {
    assert!(
        d["proof_gaps"].as_array().unwrap().contains(&json!(name)),
        "{name}: {d}"
    );
}
fn event_change(offset: usize, bytes: &[u8]) -> Value {
    let mut t = tx(0);
    let mut e = hex::decode(t["inner_instructions"][4]["data_hex"].as_str().unwrap()).unwrap();
    e[offset..offset + bytes.len()].copy_from_slice(bytes);
    t["inner_instructions"][4]["data_hex"] = json!(hex::encode(e));
    t
}
fn renumber(t: &mut Value) {
    for (n, v) in t["inner_instructions"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .enumerate()
    {
        v["inner_order"] = json!(n);
    }
}

#[test]
fn authentic_three_nested_buys_have_exact_args_own_event_and_explicit_two_source_gaps() {
    let expected = [
        ("537155460591", "26305933"),
        ("601504660440", "32187161"),
        ("5131115368584", "112822100"),
    ];
    for (n, (amount, max)) in expected.into_iter().enumerate() {
        let t = tx(n);
        let prior = pump::inspect(&t).unwrap();
        let d = diagnosis(&t);
        assert_eq!(t["status"], "OK");
        assert_eq!(d["evaluated_profile"], pump_nested_buy::PROFILE);
        assert_eq!(d["instruction_sha256"], fixture(n)["instruction_sha256"]);
        assert_eq!(
            d["instruction_data_hex"],
            fixture(n)["instruction_data_hex"]
        );
        assert_eq!(d["instruction_bytes"], 25);
        assert_eq!(d["full_instruction_layout_match"], true);
        assert_eq!(d["instruction"]["amount_raw_u64"], amount);
        assert_eq!(d["instruction"]["max_sol_cost_raw_u64"], max);
        assert_eq!(d["instruction"]["track_volume"], false);
        assert_eq!(
            d["event_context"]["event_cpi_sha256"],
            fixture(n)["event_sha256"]
        );
        assert_eq!(d["event_context"]["event_cpi_bytes"], 366);
        assert_eq!(d["event_context"]["outer_index"], 2);
        assert_eq!(d["event_context"]["inner_order"], 4);
        assert_eq!(d["event_context"]["stack_height"], 3);
        assert_eq!(d["event_context"]["parent"]["inner_order"], 0);
        assert_eq!(
            d["event_context"]["subtree"]["end_inner_order_exclusive"],
            5
        );
        assert_eq!(
            d["event_context"]["ordered_group_trace"]
                .as_array()
                .unwrap()
                .len(),
            6
        );
        assert_eq!(d["event_reported"]["token_amount_raw_u64"], amount);
        assert_eq!(d["event_reported"]["sol_amount_raw_u64"], max);
        assert_eq!(d["event_reported"]["track_volume"], false);
        assert_eq!(d["event_reported"]["mayhem_mode"], true);
        assert!(
            d["event_correlation"]
                .as_object()
                .unwrap()
                .values()
                .all(|v| v == true)
        );
        assert_eq!(d["reason"], "ACCOUNT_MISMATCH");
        assert_eq!(d["accounts"][16]["address_match"], false);
        assert_eq!(d["accounts"][6]["message_signer"], false);
        let rows = d["accounts"].as_array().unwrap();
        assert_eq!(rows.len(), 18);
        assert_eq!(rows.iter().filter(|r| !r["pda_bump"].is_null()).count(), 10);
        assert_eq!(
            rows.iter()
                .filter(|r| !r["pda_bump"].is_null() && r["address_match"] == true)
                .count(),
            9
        );
        assert!(rows.iter().all(|r| r["cpi_signer"].is_null()
            && r["cpi_writable"].is_null()
            && r["cpi_privileges_verified"] == false));
        assert_eq!(d["mayhem_context"]["user_matches_sdk_sol_vault_pda"], true);
        assert_eq!(d["mayhem_context"]["bump"], 253);
        gap(
            &d,
            "REMAINING_BUY_ACCOUNT_16_DIFFERS_FROM_SOURCE_CURVE_V2_PDA",
        );
        gap(&d, "USER_MESSAGE_SIGNATURE_ABSENT_CPI_SIGNER_UNAVAILABLE");
        assert_eq!(pump::inspect(&t).unwrap(), prior);
        assert_eq!(d, diagnosis(&t));
    }
}

#[test]
fn synthetic_instruction_bounds_bool_and_discriminator_cannot_change_old_routes() {
    let original = tx(0);
    let bytes = hex::decode(
        original["inner_instructions"][0]["data_hex"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    for size in [0, 7, 8, 16, 24, 26, 32] {
        let mut t = original.clone();
        let mut b = bytes.clone();
        b.resize(size, 0);
        t["inner_instructions"][0]["data_hex"] = json!(hex::encode(b));
        assert!(pump_nested_buy::inspect(&t).unwrap().is_empty());
    }
    for disc in ["b817ee6167c5d33d", "33e685a4017f83ad"] {
        let mut t = original.clone();
        t["inner_instructions"][0]["data_hex"] =
            json!(format!("{disc}{}", hex::encode(&bytes[8..])));
        assert!(pump_nested_buy::inspect(&t).unwrap().is_empty());
    }
    let mut t = original.clone();
    let mut b = bytes.clone();
    b[24] = 2;
    t["inner_instructions"][0]["data_hex"] = json!(hex::encode(&b));
    assert_eq!(diagnosis(&t)["reason"], "INSTRUCTION_LAYOUT_REJECTED");
    b[24] = 1;
    t["inner_instructions"][0]["data_hex"] = json!(hex::encode(b));
    assert_eq!(
        diagnosis(&t)["event_correlation"]["track_volume_matches"],
        false
    );
    let mut t = original;
    t["inner_instructions"][0]["data_hex"] = json!("bad hex");
    assert!(pump_nested_buy::inspect(&t).is_err());
}

#[test]
fn synthetic_account_count_token_fee_and_pda_checks_are_buy_specific() {
    for pos in 0..18 {
        let mut t = tx(0);
        let key = usize::try_from(
            t["inner_instructions"][0]["account_indexes"][pos]
                .as_u64()
                .unwrap(),
        )
        .unwrap();
        t["account_keys"][key] = json!(bs58::encode([77; 32]).into_string());
        let d = diagnosis(&t);
        assert_ne!(d["accounts"][pos]["address_match"], true, "position {pos}");
    }
    for count in [0, 14, 16, 17, 19] {
        let mut t = tx(0);
        t["inner_instructions"][0]["account_indexes"]
            .as_array_mut()
            .unwrap()
            .resize(count, json!(0));
        assert_eq!(diagnosis(&t)["account_address_correspondence"], false);
        if count < 17 {
            let d = diagnosis(&t);
            gap(&d, "REMAINING_BUY_ACCOUNT_16_CORRESPONDENCE_UNAVAILABLE");
            assert!(!d["proof_gaps"].as_array().unwrap().contains(&json!(
                "REMAINING_BUY_ACCOUNT_16_DIFFERS_FROM_SOURCE_CURVE_V2_PDA"
            )));
        }
    }
    let mut t = tx(0);
    t["account_keys"][17] = json!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
    assert_ne!(diagnosis(&t)["accounts"][8]["address_match"], true);
    let t = event_change(169, &30_u64.to_le_bytes());
    assert_eq!(
        diagnosis(&t)["event_reported"]["fee_basis_points_raw_u64"],
        "30"
    );
    let mut t = tx(0);
    let d = diagnosis(&t);
    t["account_keys"][24] = d["accounts"][16]["expected"].clone();
    let d = diagnosis(&t);
    assert_eq!(d["accounts"][16]["address_match"], true);
    assert_eq!(d["reason"], "SOURCE_PROFILE_NOT_ADMITTED");
    gap(&d, "USER_MESSAGE_SIGNATURE_ABSENT_CPI_SIGNER_UNAVAILABLE");
}

#[test]
fn synthetic_missing_wrong_height_and_incomplete_later_boundaries_reject_own_context() {
    for pos in 0..6 {
        for height in [Value::Null, json!(1), json!(99)] {
            let mut t = tx(0);
            t["inner_instructions"][pos]["stack_height"] = height;
            assert!(diagnosis(&t)["event_context"].is_null(), "pos {pos}");
        }
    }
    let mut t = tx(0);
    t["inner_instructions"][4]["stack_height"] = json!(2);
    assert_eq!(diagnosis(&t)["event_association_failure"], "MISSING_EVENT");
    let mut t = tx(0);
    t["inner_instructions"][4]["account_indexes"] = json!([0]);
    assert_eq!(
        diagnosis(&t)["event_association_failure"],
        "EVENT_AUTHORITY_MISMATCH"
    );
    let mut t = tx(0);
    t["instructions"][2]["account_indexes"]
        .as_array_mut()
        .unwrap()
        .retain(|v| v != 20);
    assert_eq!(
        diagnosis(&t)["event_association_failure"],
        "PARENT_ACCOUNT_MISMATCH"
    );
    let mut t = tx(0);
    t["inner_instructions"][4]["program_id_index"] = json!(14);
    assert_eq!(
        diagnosis(&t)["event_association_failure"],
        "UNSUPPORTED_INVOCATION"
    );
}

#[test]
fn synthetic_neighbor_duplicate_and_swapped_context_never_supply_an_event() {
    let mut t = tx(0);
    let own = t["inner_instructions"].as_array_mut().unwrap().remove(4);
    t["inner_instructions"].as_array_mut().unwrap().push(own);
    renumber(&mut t);
    assert_eq!(diagnosis(&t)["event_association_failure"], "MISSING_EVENT");
    let mut t = tx(0);
    let own = t["inner_instructions"][4].clone();
    t["inner_instructions"]
        .as_array_mut()
        .unwrap()
        .insert(5, own);
    renumber(&mut t);
    assert_eq!(
        diagnosis(&t)["event_association_failure"],
        "AMBIGUOUS_EVENT"
    );
    let mut t = tx(0);
    t["inner_instructions"][3]["inner_order"] = json!(4);
    assert_eq!(
        diagnosis(&t)["event_association_failure"],
        "INVALID_INVOCATION_ORDER"
    );
    let mut t = tx(0);
    t["inner_instructions"][4]["outer_index"] = json!(1);
    assert_eq!(
        diagnosis(&t)["event_association_failure"],
        "INVALID_INVOCATION_ORDER"
    );
    let mut t = tx(0);
    t["inner_instructions"][4]["data_hex"] = tx(2)["inner_instructions"][4]["data_hex"].clone();
    assert_eq!(diagnosis(&t)["event_correlation"]["mint_matches"], false);
}

#[test]
fn synthetic_full_event_consumption_mismatched_fields_and_invalid_types_fail_closed() {
    let original = tx(0);
    let bytes = hex::decode(
        original["inner_instructions"][4]["data_hex"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    for size in [8, 15, 16, 365, 367] {
        let mut t = original.clone();
        let mut b = bytes.clone();
        b.resize(size, 0);
        t["inner_instructions"][4]["data_hex"] = json!(hex::encode(b));
        assert!(diagnosis(&t)["event_context"].is_null());
    }
    for offset in [64, 233, 273] {
        assert!(diagnosis(&event_change(offset, &[2]))["event_context"].is_null());
    }
    for (offset, key) in [
        (16, "mint_matches"),
        (65, "user_matches"),
        (56, "token_amount_matches"),
    ] {
        let mut b = bytes.clone();
        b[offset] ^= 1;
        let mut t = original.clone();
        t["inner_instructions"][4]["data_hex"] = json!(hex::encode(b));
        assert_eq!(diagnosis(&t)["event_correlation"][key], false);
    }
}

#[test]
fn synthetic_status_unknown_privileges_and_integer_edges_never_admit() {
    for status in [Value::Null, json!("ERROR"), json!("UNKNOWN")] {
        let mut t = tx(0);
        t["status"] = status;
        assert_eq!(diagnosis(&t)["reason"], "TRANSACTION_NOT_SUCCESSFUL");
    }
    let mut t = tx(0);
    t["transaction_error"] = json!({"error":"synthetic"});
    assert_eq!(diagnosis(&t)["reason"], "TRANSACTION_NOT_SUCCESSFUL");
    let mut t = tx(0);
    t["message_account_layout"] = Value::Null;
    let d = diagnosis(&t);
    gap(&d, "MESSAGE_MINIMUM_PRIVILEGES_NOT_ESTABLISHED");
    gap(&d, "USER_MESSAGE_SIGNER_UNAVAILABLE_CPI_SIGNER_UNAVAILABLE");
    assert!(!d["proof_gaps"].as_array().unwrap().contains(&json!(
        "USER_MESSAGE_SIGNATURE_ABSENT_CPI_SIGNER_UNAVAILABLE"
    )));
    let mut t = tx(0);
    t["compatible_candidate_count"] = json!(1);
    t["cpi_signer"] = json!(true);
    let d = diagnosis(&t);
    assert!(d["mayhem_context"]["cpi_signer"].is_null());
    for amount in [0, u64::MAX] {
        let mut t = event_change(56, &amount.to_le_bytes());
        let mut b = hex::decode(t["inner_instructions"][0]["data_hex"].as_str().unwrap()).unwrap();
        b[8..16].copy_from_slice(&amount.to_le_bytes());
        b[16..24].copy_from_slice(&amount.to_le_bytes());
        t["inner_instructions"][0]["data_hex"] = json!(hex::encode(b));
        let d = diagnosis(&t);
        assert_eq!(d["instruction"]["amount_raw_u64"], amount.to_string());
        assert_eq!(d["instruction"]["max_sol_cost_raw_u64"], amount.to_string());
        assert_eq!(
            d["event_reported"]["token_amount_raw_u64"],
            amount.to_string()
        );
    }
    for timestamp in [i64::MIN, i64::MAX] {
        assert_eq!(
            diagnosis(&event_change(97, &timestamp.to_le_bytes()))["event_reported"]["timestamp_raw_i64"],
            timestamp.to_string()
        );
    }
}

#[test]
fn source_receipt_and_no_silver_route_keep_previous_provenance_boundary() {
    let s: Value = serde_json::from_slice(pump_nested_buy::SOURCE).unwrap();
    assert_eq!(s["admission"], false);
    assert_eq!(s["instruction"]["bytes"], 25);
    assert_eq!(
        s["base_buy_receipt_sha256"],
        sha256(of1_bronze_decoder::pump_buy::SOURCE)
    );
    for n in 0..3 {
        let t = tx(n);
        assert!(pump_sell::inspect(&t).unwrap().is_empty());
        let before = t.clone();
        diagnosis(&t);
        assert_eq!(t, before);
    }
    let mut t = tx(0);
    t["inner_instructions"] = Value::Null;
    assert!(pump_nested_buy::inspect(&t).unwrap().is_empty());
}
