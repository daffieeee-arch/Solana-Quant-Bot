//! One authentic CID-bound CAR section plus explicitly synthetic perturbations.
//! The historical argument reading is diagnostic only, never a Silver profile.
use of1_bronze_decoder::{
    archive::{self, Envelope},
    codec, pump_buy, pump_buy_variants,
};
use of1_range_recorder::sha256;
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::collections::BTreeMap;

// Global vector position differs from per-outer inner_order (6).
const EVENT_POSITION: usize = 10;

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/authentic-buy24-section.json")).unwrap()
}

fn transaction() -> Value {
    let f = fixture();
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
    assert_eq!(a[3], C::Integer(422_669_518));
    assert_eq!(a[4], C::Integer(320));
    codec::decode(
        &Envelope {
            cid_hex: hex::encode(cid),
            physical_node_index: 461,
            raw_offset: 254_386,
            raw_length: raw.len(),
            entry_index: 141,
            transaction_index_in_entry: 0,
            transaction_index_in_slot: 320,
            source_transaction_index: None,
            data: archive::frame(&a[1]).unwrap(),
            metadata: archive::frame(&a[2]).unwrap(),
        },
        &BTreeMap::new(),
    )
    .unwrap()
}
fn diagnostic(tx: &Value) -> Value {
    let d = pump_buy_variants::inspect(tx).unwrap().remove(0);
    assert_eq!(d["disposition"], "NOT_ADMITTED");
    assert_eq!(d["silver"], "NOT_PRODUCED");
    assert_eq!(d["research_ready"], false);
    assert!(d["instruction"]["track_volume"].is_null());
    d
}
fn contains_gap(d: &Value, gap: &str) {
    assert!(
        d["proof_gaps"].as_array().unwrap().contains(&json!(gap)),
        "{d}"
    );
}
fn mutate_event(offset: usize, data: &[u8]) -> Value {
    let mut tx = transaction();
    let mut bytes = hex::decode(
        tx["inner_instructions"][EVENT_POSITION]["data_hex"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    bytes[offset..offset + data.len()].copy_from_slice(data);
    tx["inner_instructions"][EVENT_POSITION]["data_hex"] = json!(hex::encode(bytes));
    tx
}

#[test]
fn authentic_24_byte_buy_is_source_version_gap_not_universal_corruption_or_buy_v2() {
    let tx = transaction();
    let d = diagnostic(&tx);
    assert_eq!(tx["status"], "OK");
    assert_eq!(d["evaluated_profile"], pump_buy_variants::PROFILE);
    assert_eq!(d["reason"], "SOURCE_VERSION_BRIDGE_UNAVAILABLE");
    assert_eq!(
        d["instruction"]["data_hex"],
        fixture()["expected_instruction_hex"]
    );
    assert_eq!(
        d["instruction"]["sha256"],
        fixture()["expected_instruction_sha256"]
    );
    assert_eq!(d["instruction"]["amount_raw_u64"], "7258165255436");
    assert_eq!(d["instruction"]["max_sol_cost_raw_u64"], "826980000");
    assert_eq!(d["instruction"]["bytes"], 24);
    assert_eq!(d["instruction"]["bytes_discarded"], 0);
    assert_eq!(d["instruction"]["bytes_synthesized"], 0);
    let comparisons = d["source_comparisons"].as_array().unwrap();
    assert_eq!(comparisons.len(), 3);
    assert_eq!(comparisons[0]["args_layout_match"], false);
    assert_eq!(comparisons[1]["args_layout_match"], true);
    assert_eq!(comparisons[1]["account_count"], 15);
    assert_eq!(
        comparisons[1]["identity"]["commit"],
        "d1b721d7bf8af75cf59f87fd11271109cfb0bdd3"
    );
    assert_eq!(
        comparisons[1]["evidence_status"],
        "RETAINED_HASH_BOUND_HISTORICAL_EXTRACT_ORIGINAL_IDL_BYTES_UNAVAILABLE"
    );
    assert_eq!(comparisons[2]["name"], "current_buy_v2");
    assert_eq!(comparisons[2]["args_layout_match"], false);
    assert!(
        comparisons
            .iter()
            .all(|v| v["full_observation_profile_match"] == false)
    );
    // Legacy probe output is exactly preserved, including its narrower Tokenkeg expectation.
    let old = pump_buy::inspect(&tx).unwrap().remove(0);
    assert_eq!(
        sha256(&serde_json::to_vec(&old).unwrap()),
        fixture()["prior_b3_buy_diagnostic_sha256"]
    );
    assert_eq!(old["full_instruction_error"]["reason"], "TRUNCATED_PAYLOAD");
    assert_eq!(d, diagnostic(&tx));
}

#[test]
fn authentic_modern_accounts_and_own_event_correspond_but_cannot_supply_missing_argument() {
    let d = diagnostic(&transaction());
    assert_eq!(d["account_count"], 18);
    assert_eq!(
        d["token_program"],
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    );
    assert_eq!(d["account_address_correspondence"], true);
    assert_eq!(d["message_minimum_privileges_match"], true);
    let rows = d["accounts"].as_array().unwrap();
    assert_eq!(rows.len(), 18);
    assert_eq!(rows.iter().filter(|r| !r["pda_bump"].is_null()).count(), 10);
    assert!(rows.iter().all(|r| r["address_match"] == true
        && r["cpi_signer"].is_null()
        && r["cpi_writable"].is_null()
        && r["cpi_privileges_verified"] == false));
    assert_eq!(rows[6]["message_signer"], true);
    assert_eq!(rows[1]["documented_fee_recipient"], true);
    assert_eq!(
        d["event_context"]["event_sha256"],
        fixture()["expected_event_sha256"]
    );
    assert_eq!(d["event_context"]["event_bytes"], 366);
    assert_eq!(d["event_context"]["outer_index"], 3);
    assert_eq!(d["event_context"]["inner_order"], 6);
    assert_eq!(d["event_context"]["stack_height"], 2);
    let event = &d["event_reported"];
    assert_eq!(
        event["mint_address"],
        "4LFvcXkW5eqZ118CBih9LQyXq8ZM3uWNLhgJo4nSpump"
    );
    assert_eq!(event["token_amount_raw_u64"], "7258165255436");
    assert_eq!(event["sol_amount_raw_u64"], "787600000");
    assert_eq!(event["track_volume"], false);
    assert_eq!(event["mayhem_mode"], false);
    assert_eq!(d["event_correlation"]["token_amount_matches"], true);
    assert!(d["event_correlation"]["track_volume_argument_matches"].is_null());
    assert!(d["quote_decimals"].is_null());
    assert!(d["base_decimals"].is_null());
    assert_eq!(d["quote_mint_identity"], "UNKNOWN");
}

#[test]
fn synthetic_lengths_discriminators_and_variant_switch_do_not_trim_pad_or_admit() {
    let tx = transaction();
    let bytes = hex::decode(tx["instructions"][3]["data_hex"].as_str().unwrap()).unwrap();
    for size in [0, 7, 8, 16, 23, 25, 26, 32] {
        let mut changed = tx.clone();
        let mut b = bytes.clone();
        b.resize(size, 1);
        changed["instructions"][3]["data_hex"] = json!(hex::encode(b));
        assert!(pump_buy_variants::inspect(&changed).unwrap().is_empty());
    }
    for disc in ["b817ee6167c5d33d", "0000000000000000"] {
        let mut changed = tx.clone();
        changed["instructions"][3]["data_hex"] =
            json!(format!("{disc}{}", hex::encode(&bytes[8..])));
        assert!(pump_buy_variants::inspect(&changed).unwrap().is_empty());
    }
    let mut malformed = tx;
    malformed["instructions"][3]["data_hex"] = json!("not hex");
    assert!(pump_buy_variants::inspect(&malformed).is_err());
}

#[test]
fn synthetic_every_account_token_fee_and_message_requirement_is_checked() {
    for pos in 0..18 {
        let mut tx = transaction();
        let key = usize::try_from(
            tx["instructions"][3]["account_indexes"][pos]
                .as_u64()
                .unwrap(),
        )
        .unwrap();
        tx["account_keys"][key] = json!(bs58::encode([77; 32]).into_string());
        assert_eq!(
            diagnostic(&tx)["account_address_correspondence"],
            false,
            "position {pos}"
        );
    }
    for n in [0, 15, 16, 17, 19] {
        let mut tx = transaction();
        tx["instructions"][3]["account_indexes"]
            .as_array_mut()
            .unwrap()
            .resize(n, json!(0));
        assert_eq!(diagnostic(&tx)["account_address_correspondence"], false);
    }
    for token in [
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "11111111111111111111111111111111",
    ] {
        let mut tx = transaction();
        tx["account_keys"][15] = json!(token);
        assert_eq!(diagnostic(&tx)["account_address_correspondence"], false);
    }
    let mut tx = transaction();
    tx["message_account_layout"]["required_signatures"] = json!(0);
    assert_eq!(diagnostic(&tx)["message_minimum_privileges_match"], false);
    let mut tx = transaction();
    tx["message_account_layout"] = Value::Null;
    assert_eq!(diagnostic(&tx)["message_minimum_privileges_match"], false);
}

#[test]
fn synthetic_own_event_cannot_be_missing_duplicated_neighboring_or_heightless() {
    for height in [Value::Null, json!(1), json!(3), json!(99)] {
        let mut tx = transaction();
        tx["inner_instructions"][EVENT_POSITION]["stack_height"] = height;
        assert!(diagnostic(&tx)["event_context"].is_null());
    }
    let mut tx = transaction();
    tx["inner_instructions"][EVENT_POSITION]["outer_index"] = json!(2);
    assert_eq!(
        diagnostic(&tx)["event_association_failure"],
        "NO_RECORDED_TAGGED_EVENT"
    );
    let mut tx = transaction();
    tx["inner_instructions"] = Value::Null;
    assert_eq!(diagnostic(&tx)["event_association_failure"], "MISSING_CPI");
    let mut tx = transaction();
    let duplicate = tx["inner_instructions"][EVENT_POSITION].clone();
    tx["inner_instructions"]
        .as_array_mut()
        .unwrap()
        .push(duplicate);
    assert_eq!(
        diagnostic(&tx)["event_association_failure"],
        "AMBIGUOUS_RECORDED_EVENTS"
    );
    let mut tx = transaction();
    tx["inner_instructions"][EVENT_POSITION]["account_indexes"] = json!([0]);
    assert_eq!(
        diagnostic(&tx)["event_association_failure"],
        "EVENT_AUTHORITY_MISMATCH"
    );
    let mut tx = transaction();
    tx["inner_instructions"][5]["stack_height"] = Value::Null;
    assert_eq!(
        diagnostic(&tx)["event_association_failure"],
        "UNSUPPORTED_PARENT_CONTEXT"
    );
}

#[test]
fn synthetic_event_bounds_wrong_boolean_and_field_disagreement_remain_explicit() {
    let tx = transaction();
    let bytes = hex::decode(
        tx["inner_instructions"][EVENT_POSITION]["data_hex"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    for n in [8, 15, 16, 365, 367] {
        let mut changed = tx.clone();
        let mut b = bytes.clone();
        b.resize(n, 0);
        changed["inner_instructions"][EVENT_POSITION]["data_hex"] = json!(hex::encode(b));
        assert!(diagnostic(&changed)["event_context"].is_null());
    }
    // 16 tag/discriminator +32 mint +8 SOL +8 token: strict Borsh bool.
    assert_eq!(
        diagnostic(&mutate_event(64, &[2]))["event_association_failure"],
        "UNSUPPORTED_EVENT_LAYOUT"
    );
    for (offset, field) in [
        (16, "mint_matches"),
        (65, "user_matches"),
        (56, "token_amount_matches"),
    ] {
        let mut changed = bytes.clone();
        changed[offset] ^= 1;
        let mut changed_tx = tx.clone();
        changed_tx["inner_instructions"][EVENT_POSITION]["data_hex"] = json!(hex::encode(changed));
        assert_eq!(diagnostic(&changed_tx)["event_correlation"][field], false);
    }
    // Existing B3 parser rejects an is_buy=false / ix_name=buy conflict before
    // exposing event fields; a valid event with another amount stays a separate
    // instruction/event correspondence failure.
    assert_eq!(
        diagnostic(&mutate_event(64, &[0]))["event_association_failure"],
        "UNSUPPORTED_EVENT_LAYOUT"
    );
    contains_gap(
        &diagnostic(&mutate_event(56, &1_u64.to_le_bytes())),
        "INSTRUCTION_EVENT_FIELD_CONFLICT",
    );
}

#[test]
fn synthetic_exact_integer_edges_failure_and_caller_count_never_manufacture_admission() {
    for value in [0, u64::MAX] {
        let mut tx = mutate_event(56, &value.to_le_bytes());
        let mut bytes = hex::decode(tx["instructions"][3]["data_hex"].as_str().unwrap()).unwrap();
        bytes[8..16].copy_from_slice(&value.to_le_bytes());
        bytes[16..24].copy_from_slice(&value.to_le_bytes());
        tx["instructions"][3]["data_hex"] = json!(hex::encode(bytes));
        let d = diagnostic(&tx);
        assert_eq!(d["instruction"]["amount_raw_u64"], value.to_string());
        assert_eq!(d["instruction"]["max_sol_cost_raw_u64"], value.to_string());
        assert_eq!(
            d["event_reported"]["token_amount_raw_u64"],
            value.to_string()
        );
    }
    for value in [i64::MIN, i64::MAX] {
        assert_eq!(
            diagnostic(&mutate_event(97, &value.to_le_bytes()))["event_reported"]["timestamp_raw_i64"],
            value.to_string()
        );
    }
    for status in [Value::Null, json!("ERROR"), json!("UNKNOWN")] {
        let mut tx = transaction();
        tx["status"] = status;
        contains_gap(&diagnostic(&tx), "TRANSACTION_NOT_SUCCESSFUL");
    }
    let mut tx = transaction();
    tx["transaction_error"] = json!({"InstructionError":"synthetic"});
    contains_gap(&diagnostic(&tx), "TRANSACTION_NOT_SUCCESSFUL");
    let mut tx = transaction();
    tx["compatible_candidate_count"] = json!(1);
    assert_eq!(
        diagnostic(&tx)["candidate_selection"],
        "NOT_PERFORMED_NO_SOURCE_BRIDGE"
    );
}

#[test]
fn source_receipt_retains_distinct_evidence_identity_and_historical_availability_limit() {
    let s: Value = serde_json::from_slice(pump_buy_variants::SOURCE).unwrap();
    assert_eq!(s["base_source_receipt_sha256"], sha256(pump_buy::SOURCE));
    assert_eq!(s["admission"], false);
    assert_eq!(s["version_comparisons"][2]["instruction_bytes"], 24);
    assert_ne!(
        s["version_comparisons"][2]["discriminator_hex"],
        s["version_comparisons"][1]["discriminator_hex"]
    );
    let mut raw = hex::decode(fixture()["section_hex"].as_str().unwrap()).unwrap();
    raw[100] ^= 1;
    assert_ne!(sha256(&raw), fixture()["section_sha256"]);
}
