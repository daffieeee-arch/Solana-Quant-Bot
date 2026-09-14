//! Authentic sealed CAR sections execute the wire/status decoder. All modified
//! boundaries below are explicitly synthetic, never relabelled authentic data.
use of1_bronze_decoder::{
    archive::{self, Envelope},
    codec, pump_sell, pump_sell16,
};
use of1_range_recorder::sha256;
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn fixture(which: usize) -> Value {
    serde_json::from_str::<Value>(include_str!("fixtures/authentic-sell16-sections.json")).unwrap()
        ["fixtures"][which]
        .clone()
}
fn transaction(which: usize) -> Value {
    let f = fixture(which);
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
    let env = Envelope {
        cid_hex: hex::encode(cid),
        physical_node_index: 0,
        raw_offset: usize::try_from(f["raw_offset"].as_u64().unwrap()).unwrap(),
        raw_length: raw.len(),
        entry_index: 0,
        transaction_index_in_entry: 0,
        transaction_index_in_slot: usize::try_from(
            f["transaction_index_in_slot"].as_u64().unwrap(),
        )
        .unwrap(),
        source_transaction_index: None,
        data: archive::frame(&a[1]).unwrap(),
        metadata: archive::frame(&a[2]).unwrap(),
    };
    codec::decode(&env, &BTreeMap::new()).unwrap()
}
fn diagnose(tx: &Value) -> Value {
    pump_sell::inspect(tx).unwrap().remove(0)
}
fn reject(tx: &Value) -> Value {
    let d = diagnose(tx);
    assert_eq!(d["disposition"], "NOT_ADMITTED", "{d}");
    assert_eq!(d["matching_registered_candidates"], json!([]));
    assert!(d["selected_candidate"].is_null());
    d
}
fn event_position(tx: &Value) -> usize {
    usize::try_from(
        diagnose(tx)["event_context"]["inner_order"]
            .as_u64()
            .unwrap(),
    )
    .unwrap()
}
fn offset(field: &str) -> usize {
    let r: Value = serde_json::from_str(include_str!("fixtures/pump-sell-reference.json")).unwrap();
    usize::try_from(r["event_offsets"][field][0].as_u64().unwrap()).unwrap()
}
fn mutated_event(field: &str, bytes: &[u8]) -> Value {
    let mut tx = transaction(0);
    let p = event_position(&tx);
    let mut raw = hex::decode(tx["inner_instructions"][p]["data_hex"].as_str().unwrap()).unwrap();
    raw[offset(field)..offset(field) + bytes.len()].copy_from_slice(bytes);
    tx["inner_instructions"][p]["data_hex"] = json!(hex::encode(raw));
    tx
}

#[test]
fn authentic_tokenkeg16_is_own_nested_event_with_no_accumulator_and_no_invented_privileges() {
    let tx = transaction(0);
    let d = diagnose(&tx);
    assert_eq!(tx["status"], "OK");
    assert_eq!(d["disposition"], "MATCHED_RECORDED_EVENT_FACTS");
    assert_eq!(d["selected_candidate"], pump_sell16::CANDIDATE);
    assert_eq!(
        d["matching_registered_candidates"],
        json!([pump_sell16::CANDIDATE])
    );
    assert_eq!(d["instruction_bytes"], 24);
    assert_eq!(
        d["instruction_sha256"],
        fixture(0)["expected_instruction_sha256"]
    );
    assert_eq!(
        d["event_context"]["event_cpi_sha256"],
        fixture(0)["expected_event_sha256"]
    );
    assert_eq!(d["event_context"]["event_cpi_bytes"], 367);
    assert_eq!(d["instruction"]["amount_raw_u64"], "249959768138");
    assert_eq!(d["instruction"]["min_sol_output_raw_u64"], "0");
    assert_eq!(d["event_reported"]["sol_amount_raw_u64"], "29277335");
    assert_eq!(
        d["event_reported"]["mint_address"],
        "7RZ6uLrxEgBgRouteBpYhZh6WWa1sVFYEgpW15Lbpump"
    );
    assert_eq!(d["event_reported"]["mayhem_mode"], false);
    assert_eq!(d["event_reported"]["cashback_raw_u64"], "0");
    assert_eq!(d["cashback_coin_account_flag"], "UNAVAILABLE_NOT_READ");
    assert_eq!(d["event_context"]["selected_invocation"]["inner_order"], 5);
    assert_eq!(d["event_context"]["inner_order"], 8);
    assert_eq!(d["event_context"]["subtree"]["start_inner_order"], 5);
    assert_eq!(
        d["event_context"]["subtree"]["end_inner_order_exclusive"],
        9
    );
    let rows = d["accounts"].as_array().unwrap();
    assert_eq!(rows.len(), 16);
    assert_eq!(rows[14]["role"], "bonding_curve_v2");
    assert_eq!(rows[15]["role"], "buyback_fee_recipient");
    assert_eq!(rows[1]["documented_fee_recipient"], true);
    for r in rows {
        assert_eq!(r["address_match"], true);
        assert_eq!(r["message_minimum_privileges_match"], true);
        assert!(r["required_privileges_match"].is_null());
        assert!(r["cpi_signer"].is_null());
        assert!(r["cpi_writable"].is_null());
        assert_eq!(r["cpi_privileges_verified"], false);
    }
    assert_eq!(rows[6]["message_signer"], true);
    assert_eq!(d, diagnose(&tx));
}

#[test]
fn authentic_four_mayhem16_remain_rejected_with_separate_pda_and_signature_gaps() {
    for n in 1..5 {
        let tx = transaction(n);
        let d = reject(&tx);
        assert_eq!(tx["status"], "OK");
        assert_eq!(d["evaluated_profile"], pump_sell16::UNRESOLVED);
        assert_eq!(d["reason"], "ACCOUNT_MISMATCH");
        assert_eq!(
            d["instruction_sha256"],
            fixture(n)["expected_instruction_sha256"]
        );
        assert_eq!(
            d["event_context"]["event_cpi_sha256"],
            fixture(n)["expected_event_sha256"]
        );
        assert_eq!(d["event_reported"]["mayhem_mode"], true);
        assert_eq!(d["accounts"][1]["documented_fee_recipient"], true);
        assert_eq!(d["accounts"][1]["fee_recipient_list"], "RESERVED_MAYHEM");
        assert_eq!(d["accounts"][14]["address_match"], false);
        assert_eq!(d["accounts"][6]["message_signer"], false);
        assert_eq!(d["accounts"][6]["message_minimum_privileges_match"], false);
        assert_eq!(d["accounts"][6]["cpi_privileges_verified"], false);
        assert!(d["accounts"][6]["cpi_signer"].is_null());
        assert_eq!(
            d["observed_profile_details"]["user_matches_official_mayhem_sol_vault_pda"],
            true
        );
        assert_eq!(d["observed_profile_details"]["mayhem_sol_vault_bump"], 253);
        assert_eq!(d["proof_gaps"].as_array().unwrap().len(), 3);
    }
}

#[test]
fn synthetic_each_account_and_no_generic_16_or_17_count_fix() {
    for pos in 0..16 {
        let mut tx = transaction(0);
        let key = usize::try_from(
            tx["inner_instructions"][5]["account_indexes"][pos]
                .as_u64()
                .unwrap(),
        )
        .unwrap();
        tx["account_keys"][key] = json!(bs58::encode([77; 32]).into_string());
        reject(&tx);
    }
    for count in [0, 14, 15, 17, 18] {
        let mut tx = transaction(0);
        tx["inner_instructions"][5]["account_indexes"]
            .as_array_mut()
            .unwrap()
            .resize(count, json!(0));
        reject(&tx);
    }
    let mut tx = transaction(0);
    tx["inner_instructions"][5]["account_indexes"][15] =
        tx["inner_instructions"][5]["account_indexes"][1].clone();
    reject(&tx);
}

#[test]
fn synthetic_cashback_mayhem_tracking_fee_and_event_correspondence_fail_closed() {
    for field in ["cashback_fee_basis_points", "cashback"] {
        reject(&mutated_event(field, &1_u64.to_le_bytes()));
    }
    for field in ["mayhem_mode", "track_volume"] {
        reject(&mutated_event(field, &[1]));
    }
    for field in ["mint", "user", "token_amount", "fee_recipient", "creator"] {
        let mut tx = transaction(0);
        let p = event_position(&tx);
        let mut b = hex::decode(tx["inner_instructions"][p]["data_hex"].as_str().unwrap()).unwrap();
        b[offset(field)] ^= 1;
        tx["inner_instructions"][p]["data_hex"] = json!(hex::encode(b));
        reject(&tx);
    }
    let mut tx = transaction(0);
    let key = usize::try_from(
        tx["inner_instructions"][5]["account_indexes"][9]
            .as_u64()
            .unwrap(),
    )
    .unwrap();
    tx["account_keys"][key] = json!("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
    reject(&tx);
    let mut tx = transaction(0);
    tx["compatible_candidate_count"] = json!(1);
    tx["message_account_layout"]["required_signatures"] = json!(0);
    reject(&tx);
}

#[test]
fn synthetic_full_instruction_event_bounds_and_invalid_borsh_remain_rejected() {
    for (pos, len) in [(5, 24), (8, 367)] {
        let original = transaction(0);
        let bytes = hex::decode(
            original["inner_instructions"][pos]["data_hex"]
                .as_str()
                .unwrap(),
        )
        .unwrap();
        for size in [8, len - 1, len + 1] {
            let mut tx = original.clone();
            let mut changed = bytes.clone();
            changed.resize(size, 0);
            tx["inner_instructions"][pos]["data_hex"] = json!(hex::encode(changed));
            reject(&tx);
        }
    }
    for field in ["is_buy", "track_volume", "mayhem_mode"] {
        reject(&mutated_event(field, &[2]));
    }
    for field in ["ix_name", "shareholders"] {
        reject(&mutated_event(field, &u32::MAX.to_le_bytes()));
    }
    // Retain exact integer representation at both boundaries, without float conversion.
    for amount in [0, u64::MAX] {
        let mut tx = mutated_event("token_amount", &amount.to_le_bytes());
        let mut b = hex::decode(tx["inner_instructions"][5]["data_hex"].as_str().unwrap()).unwrap();
        b[8..16].copy_from_slice(&amount.to_le_bytes());
        tx["inner_instructions"][5]["data_hex"] = json!(hex::encode(b));
        assert_eq!(
            diagnose(&tx)["instruction"]["amount_raw_u64"],
            amount.to_string()
        );
        assert_eq!(diagnose(&tx)["disposition"], "MATCHED_RECORDED_EVENT_FACTS");
    }
}

#[test]
fn synthetic_context_status_and_missing_cpi_capacity_cannot_publish_success() {
    for status in [Value::Null, json!("ERROR"), json!("UNKNOWN")] {
        let mut tx = transaction(0);
        tx["status"] = status;
        reject(&tx);
    }
    let mut tx = transaction(0);
    tx["transaction_error"] = json!({"InstructionError":"synthetic"});
    reject(&tx);
    for pos in [5, 8, 9] {
        for height in [Value::Null, json!(1), json!(99)] {
            let mut tx = transaction(0);
            tx["inner_instructions"][pos]["stack_height"] = height;
            reject(&tx);
        }
    }
    let mut tx = transaction(0);
    tx["message_account_layout"] = Value::Null;
    reject(&tx);
    let mut tx = transaction(0);
    tx["inner_instructions"][8]["outer_index"] = json!(1);
    reject(&tx);
    let mut tx = transaction(0);
    tx["inner_instructions"][8]["account_indexes"] = json!([0]);
    reject(&tx);
    // A correct event moved across the later sibling boundary is not its own event.
    let mut tx = transaction(0);
    tx["inner_instructions"][8]["stack_height"] = json!(2);
    reject(&tx);
    let mut tx = transaction(0);
    let event = tx["inner_instructions"][8].clone();
    tx["inner_instructions"]
        .as_array_mut()
        .unwrap()
        .insert(9, event);
    for (n, ix) in tx["inner_instructions"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .enumerate()
    {
        ix["inner_order"] = json!(n);
    }
    assert_eq!(reject(&tx)["reason"], "AMBIGUOUS_EVENT");
}

#[test]
fn synthetic_bound_bronze_to_silver_keeps_source_status_exact_bytes_and_unknowns() {
    let f = fixture(0);
    let mut r = json!({"disposition":"DECODED","transaction":transaction(0),"atomic_observation_package":true,
        "source":{"raw_sha256":f["raw_sha256"],"transaction_node_cid_hex":f["cid_hex"],"bindings":{"fixture":"SYNTHETIC_PACKAGE_NOT_AUTHENTIC_RECEIPT"}},
        "decoder_source_sha256":"synthetic-package-test","effective_at":{"slot":f["slot"],"transaction_index_in_slot":f["transaction_index_in_slot"]},
        "input_kind":"AUTHENTIC_WIRE_IN_SYNTHETIC_PROVENANCE_PACKAGE","receipt_evidence":"Fixture"});
    let facts = pump_sell::facts(&r).unwrap();
    assert_eq!(facts.len(), 1);
    let fact = &facts[0];
    assert_eq!(
        fact["bronze_record_sha256"],
        sha256(&serde_json::to_vec(&r).unwrap())
    );
    assert_eq!(fact["source"], r["source"]);
    assert_eq!(fact["candidate_id"], pump_sell16::CANDIDATE);
    assert_eq!(
        fact["instruction_data_hex"],
        "33e685a4017f83ad4a60c3323a0000000000000000000000"
    );
    assert_eq!(fact["event_reported"]["fee_raw_u64"], "278135");
    assert_eq!(fact["event_reported"]["buyback_fee_raw_u64"], "139067");
    assert_eq!(fact["quote_mint_identity"], "UNKNOWN");
    assert_eq!(fact["research_ready"], false);
    for field in [
        "quote_decimals",
        "base_decimals",
        "name",
        "ticker",
        "launch_at",
        "executable_price",
    ] {
        assert!(fact[field].is_null());
    }
    assert_eq!(facts, pump_sell::facts(&r).unwrap());
    r["source"]["bindings"] = Value::Null;
    assert!(pump_sell::facts(&r).is_err());
    r["disposition"] = json!("QUARANTINED");
    assert!(pump_sell::facts(&r).unwrap().is_empty());
}

#[test]
fn source_receipt_keeps_bounded_profiles_and_prior_source_receipts_unchanged() {
    let s: Value = serde_json::from_slice(pump_sell16::SOURCE).unwrap();
    assert_eq!(s["candidate_id"], pump_sell16::CANDIDATE);
    assert_eq!(s["base_sell_receipt_sha256"], sha256(pump_sell::SOURCE));
    assert_eq!(
        s["nested_context_receipt_sha256"],
        sha256(of1_bronze_decoder::pump_sell_context::SOURCE)
    );
    assert_eq!(
        s["sources"][0]["sha256"],
        "b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49"
    );
    assert_eq!(
        s["account_layout"]["cashback_coin_account_flag"],
        "UNAVAILABLE_NOT_READ"
    );
    assert_eq!(s["mayhem_diagnostic"]["admission"], false);
}
