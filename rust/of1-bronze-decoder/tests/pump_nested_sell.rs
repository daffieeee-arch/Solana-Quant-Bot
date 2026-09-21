//! Authentic CAR sections are unchanged; modified call traces are SYNTHETIC.
use of1_bronze_decoder::{
    archive::{self, Envelope},
    codec, pump, pump_sell, pump_sell_context, report,
};
use of1_range_recorder::sha256;
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn tx(which: usize) -> Value {
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
fn inspect(tx: &Value) -> Vec<Value> {
    pump_sell::inspect(tx).unwrap()
}
fn reject(tx: &Value, reason: &str) {
    let d = inspect(tx);
    assert_eq!(d[0]["disposition"], "NOT_ADMITTED", "{d:?}");
    assert_eq!(d[0]["reason"], reason);
    assert_eq!(d[0]["matching_registered_candidates"], json!([]));
}
fn renumber(tx: &mut Value) {
    for (i, ix) in tx["inner_instructions"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .enumerate()
    {
        ix["inner_order"] = json!(i);
    }
}
fn fixture_record(transaction: &Value) -> Value {
    json!({"disposition":"DECODED","atomic_observation_package":true,"input_kind":"SYNTHETIC_UNIT_PACKAGE_WITH_AUTHENTIC_WIRE","receipt_evidence":"Fixture","decoder_source_sha256":of1_bronze_decoder::source_sha256(),"source":{"raw_sha256":"SYNTHETIC_BINDING_NOT_AUTHENTIC_RECEIPT","bindings":{},"transaction_node_cid_hex":"SYNTHETIC_TEST"},"transaction":transaction})
}

#[test]
fn authentic_both_nested_calls_match_own_event_and_exact_source_bound_fields() {
    for (which, amount, sol, ix_hash, event_hash) in [
        (
            3,
            "15071528820578",
            "2205351813",
            "0a60c596d5fd8c6bdde92ea18638414dab14fdef1fc4be751b5c2dddb1172e5d",
            "244228a947ba866c1b6b76ddc0627b8311ae5c621b84c07dfbd5c0a0562f881a",
        ),
        (
            4,
            "10431201672810",
            "1446431596",
            "203635b5b6fed6391c66cf9f7e1f07a5e46a196d2d87a928bddb97bc6b91d831",
            "2f1d41e703727c6696d9b465b3776ae743790793d660f5ef4ee687cbcc4ce4df",
        ),
    ] {
        let transaction = tx(which);
        let ds = inspect(&transaction);
        assert_eq!(ds.len(), 1);
        let d = &ds[0];
        assert_eq!(d["disposition"], "MATCHED_RECORDED_EVENT_FACTS");
        assert_eq!(d["selected_candidate"], pump_sell_context::CANDIDATE);
        assert_eq!(d["instruction_bytes"], 24);
        assert_eq!(d["account_count"], 17);
        assert_eq!(d["instruction_sha256"], ix_hash);
        assert_eq!(d["instruction"]["amount_raw_u64"], amount);
        assert_eq!(d["instruction"]["min_sol_output_raw_u64"], "0");
        assert_eq!(d["event_reported"]["token_amount_raw_u64"], amount);
        assert_eq!(d["event_reported"]["sol_amount_raw_u64"], sol);
        assert_eq!(d["event_reported"]["is_buy"], false);
        assert_eq!(d["event_reported"]["ix_name"], "sell");
        assert_eq!(d["event_context"]["event_cpi_sha256"], event_hash);
        assert_eq!(d["event_context"]["event_cpi_bytes"], 367);
        assert_eq!(d["event_context"]["inner_order"], 3);
        assert_eq!(d["event_context"]["stack_height"], 3);
        assert_eq!(d["event_context"]["parent"]["inner_order"], 0);
        assert_eq!(
            d["event_context"]["subtree"]["end_inner_order_exclusive"],
            4
        );
        let trace = d["event_context"]["ordered_group_trace"]
            .as_array()
            .unwrap();
        assert_eq!(
            trace
                .iter()
                .map(|v| v["stack_height"].clone())
                .collect::<Vec<_>>(),
            vec![json!(2), json!(3), json!(3), json!(3), json!(2)]
        );
        assert_eq!(
            trace
                .iter()
                .map(|v| v["parent_inner_order"].clone())
                .collect::<Vec<_>>(),
            vec![Value::Null, json!(0), json!(0), json!(0), Value::Null]
        );
        assert!(
            d["accounts"].as_array().unwrap().iter().all(
                |r| r["address_match"] == true && r["message_minimum_privileges_match"] == true
            )
        );
        assert_eq!(ds, inspect(&transaction));
    }
}

#[test]
fn synthetic_missing_heights_anywhere_in_group_never_use_logs_or_defaults() {
    for position in 0..5 {
        for bad in [Value::Null, json!("3"), json!(-1)] {
            let mut t = tx(3);
            t["inner_instructions"][position]["stack_height"] = bad;
            reject(&t, "MISSING_STACK_HEIGHT");
        }
    }
}

#[test]
fn synthetic_invalid_height_jumps_and_event_sibling_cannot_match() {
    for (position, height) in [(0, 3), (0, 0), (1, 4), (2, 1), (3, 5), (4, 1)] {
        let mut t = tx(3);
        t["inner_instructions"][position]["stack_height"] = json!(height);
        reject(&t, "INVALID_STACK_HEIGHT");
    }
    let mut t = tx(3);
    t["inner_instructions"][3]["stack_height"] = json!(2);
    reject(&t, "MISSING_EVENT");
    let mut t = tx(3);
    t["inner_instructions"][3]["stack_height"] = json!(4);
    reject(&t, "MISSING_EVENT");
}

#[test]
fn synthetic_order_duplicate_gap_swap_and_outer_context_are_rejected() {
    for (position, value) in [
        (1, json!(0)),
        (1, json!(2)),
        (1, Value::Null),
        (3, json!(-1)),
    ] {
        let mut t = tx(3);
        t["inner_instructions"][position]["inner_order"] = value;
        reject(&t, "INVALID_INVOCATION_ORDER");
    }
    let mut t = tx(3);
    t["inner_instructions"].as_array_mut().unwrap().swap(1, 2);
    reject(&t, "INVALID_INVOCATION_ORDER");
    let mut t = tx(3);
    t["inner_instructions"][3]["outer_index"] = json!(3);
    reject(&t, "INVALID_INVOCATION_ORDER");
    let mut t = tx(3);
    t["instructions"][2]["index"] = json!(1);
    reject(&t, "INVALID_INVOCATION_ORDER");
}

fn neighbors() -> Value {
    let mut t = tx(3);
    let group = t["inner_instructions"].as_array().unwrap();
    let sequence = [
        group[..4].to_vec(),
        group[..4].to_vec(),
        vec![group[4].clone()],
    ]
    .concat();
    t["inner_instructions"] = json!(sequence);
    renumber(&mut t);
    t
}

#[test]
fn synthetic_two_neighboring_pump_invocations_keep_distinct_event_owners() {
    let t = neighbors();
    let d = inspect(&t);
    assert_eq!(d.len(), 2);
    assert!(
        d.iter()
            .all(|v| v["disposition"] == "MATCHED_RECORDED_EVENT_FACTS")
    );
    assert_eq!(d[0]["event_context"]["inner_order"], 3);
    assert_eq!(d[0]["event_context"]["parent"]["inner_order"], 0);
    assert_eq!(d[1]["event_context"]["inner_order"], 7);
    assert_eq!(d[1]["event_context"]["parent"]["inner_order"], 4);
    assert_eq!(
        d[0]["event_context"]["subtree"]["end_inner_order_exclusive"],
        4
    );
}

#[test]
fn synthetic_missing_own_event_never_borrows_previous_or_next_event() {
    let mut t = neighbors();
    t["inner_instructions"].as_array_mut().unwrap().remove(3);
    renumber(&mut t);
    let d = inspect(&t);
    assert_eq!(d[0]["reason"], "MISSING_EVENT");
    assert_eq!(d[1]["disposition"], "MATCHED_RECORDED_EVENT_FACTS");
    let mut t = neighbors();
    t["inner_instructions"].as_array_mut().unwrap().remove(7);
    renumber(&mut t);
    let d = inspect(&t);
    assert_eq!(d[0]["disposition"], "MATCHED_RECORDED_EVENT_FACTS");
    assert_eq!(d[1]["reason"], "MISSING_EVENT");
}

#[test]
fn synthetic_deeper_pump_child_event_is_not_parent_sells_event() {
    let mut t = tx(3);
    let mut child = t["inner_instructions"][0].clone();
    child["stack_height"] = json!(3);
    t["inner_instructions"]
        .as_array_mut()
        .unwrap()
        .insert(3, child);
    t["inner_instructions"][4]["stack_height"] = json!(4);
    renumber(&mut t);
    let d = inspect(&t);
    assert_eq!(d[0]["reason"], "MISSING_EVENT");
    assert_eq!(d[1]["reason"], "UNSUPPORTED_INVOCATION");
}

#[test]
fn synthetic_duplicate_own_events_fail_even_when_bytes_are_equal() {
    let mut t = tx(3);
    let event = t["inner_instructions"][3].clone();
    t["inner_instructions"]
        .as_array_mut()
        .unwrap()
        .insert(4, event);
    renumber(&mut t);
    reject(&t, "AMBIGUOUS_EVENT");
}

#[test]
fn synthetic_swapped_transaction_event_context_never_admits() {
    let mut a = tx(3);
    let b = tx(4);
    a["inner_instructions"][3]["data_hex"] = b["inner_instructions"][3]["data_hex"].clone();
    let d = inspect(&a);
    assert_eq!(d[0]["disposition"], "NOT_ADMITTED");
    assert!(d[0]["selected_candidate"].is_null());
    let mut t = tx(3);
    t["inner_instructions"][2]["stack_height"] = json!(2);
    reject(&t, "MISSING_EVENT");
}

#[test]
fn synthetic_parent_account_program_and_event_authority_checks_remain_required() {
    for pos in 0..17 {
        let mut t = tx(3);
        let index = t["inner_instructions"][0]["account_indexes"][pos].clone();
        t["instructions"][2]["account_indexes"]
            .as_array_mut()
            .unwrap()
            .retain(|v| *v != index);
        reject(&t, "PARENT_ACCOUNT_MISMATCH");
    }
    for pos in 0..17 {
        let mut t = tx(3);
        let key = usize::try_from(
            t["inner_instructions"][0]["account_indexes"][pos]
                .as_u64()
                .unwrap(),
        )
        .unwrap();
        t["account_keys"][key] = json!(bs58::encode([77; 32]).into_string());
        assert_eq!(inspect(&t)[0]["disposition"], "NOT_ADMITTED");
    }
    let mut t = tx(3);
    t["inner_instructions"][3]["account_indexes"] = json!([0]);
    reject(&t, "EVENT_AUTHORITY_MISMATCH");
    let mut t = tx(3);
    t["inner_instructions"][3]["account_indexes"] = json!([23, 23]);
    reject(&t, "EVENT_AUTHORITY_MISMATCH");
    let mut t = tx(3);
    t["inner_instructions"][3]["program_id_index"] = json!(22);
    reject(&t, "UNSUPPORTED_INVOCATION");
    let mut t = tx(3);
    t["instructions"][2]["program_id_index"] = json!(20);
    reject(&t, "UNSUPPORTED_INVOCATION");
}

#[test]
fn synthetic_message_capability_is_checked_but_cpi_flags_never_invented() {
    let mut t = tx(3);
    t["inner_instructions"][0]["is_signer"] = json!(true);
    t["inner_instructions"][0]["is_writable"] = json!(true);
    let d = inspect(&t);
    assert_eq!(d[0]["disposition"], "MATCHED_RECORDED_EVENT_FACTS");
    for row in d[0]["accounts"].as_array().unwrap() {
        assert_eq!(row["message_minimum_privileges_match"], true);
        assert!(row["required_privileges_match"].is_null());
        assert!(row["cpi_signer"].is_null());
        assert!(row["cpi_writable"].is_null());
        assert_eq!(row["cpi_privileges_verified"], false);
    }
    t["message_account_layout"]["required_signatures"] = json!(0);
    reject(&t, "ACCOUNT_MISMATCH");
    let mut t = tx(3);
    t["message_account_layout"]["readonly_unsigned"] = json!(14);
    reject(&t, "ACCOUNT_MISMATCH");
    let mut t = tx(3);
    t["message_account_layout"] = Value::Null;
    reject(&t, "ACCOUNT_MISMATCH");
}

#[test]
fn synthetic_failed_status_corruption_and_no_provenance_do_not_emit_silver() {
    for status in [json!("ERROR"), json!("UNKNOWN"), Value::Null] {
        let mut t = tx(3);
        t["status"] = status;
        reject(&t, "TRANSACTION_NOT_SUCCESSFUL");
        assert!(pump_sell::facts(&fixture_record(&t)).unwrap().is_empty());
    }
    let mut t = tx(3);
    t["transaction_error"] = json!({"error":"InstructionError"});
    reject(&t, "TRANSACTION_NOT_SUCCESSFUL");
    let original = tx(3)["inner_instructions"][3]["data_hex"]
        .as_str()
        .unwrap()
        .to_owned();
    for n in 0..367 {
        let mut t = tx(3);
        t["inner_instructions"][3]["data_hex"] = json!(&original[..2 * n]);
        assert_eq!(inspect(&t)[0]["disposition"], "NOT_ADMITTED");
    }
    let mut t = tx(3);
    t["inner_instructions"][3]["data_hex"] = json!(format!("{original}00"));
    reject(&t, "TRAILING_EVENT");
    let mut r = fixture_record(&tx(3));
    r["source"]["raw_sha256"] = Value::Null;
    assert!(pump_sell::facts(&r).is_err());
}

#[test]
fn nested_source_receipt_binds_original_sources_not_a_router_or_cpi_flag_schema() {
    let s: Value = serde_json::from_slice(pump_sell_context::SOURCE).unwrap();
    assert_eq!(
        s["pump_source_receipt"]["sha256"],
        sha256(pump_sell::SOURCE)
    );
    assert_eq!(s["candidate_id"], pump_sell_context::CANDIDATE);
    let proto = s["trace_sources"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["path"] == "storage-proto/proto/confirmed_block.proto")
        .unwrap();
    assert_eq!(
        proto["sha256"],
        sha256(include_bytes!("../sources/confirmed_block.proto"))
    );
    assert_eq!(s["association_contract"]["selected_instruction_height"], 2);
    assert_eq!(s["association_contract"]["event_height"], 3);
    assert_eq!(s["privilege_contract"]["infer_from_success_or_pda"], false);
    assert!(s["privilege_contract"]["actual_cpi_signer_flags"].is_null());
}

#[test]
fn all_four_sells_have_visible_deterministic_facts_with_buy_still_rejected() {
    let mut records = Vec::new();
    let mut facts = Vec::new();
    for which in 1..5 {
        let t = tx(which);
        let mut r = fixture_record(&t);
        r["transaction"]["pump_sell_analysis"] = json!(inspect(&r["transaction"]));
        let f = pump_sell::facts(&r).unwrap();
        assert_eq!(f.len(), 1);
        assert_eq!(
            f[0]["bronze_record_sha256"],
            sha256(&serde_json::to_vec(&r).unwrap())
        );
        assert_eq!(f, pump_sell::facts(&r).unwrap());
        if which >= 3 {
            assert_eq!(f[0]["event_context"]["parent"]["inner_order"], 0);
            assert_eq!(
                f[0]["source_evidence_sha256"],
                sha256(pump_sell_context::SOURCE)
            );
        }
        facts.extend(f);
        records.push(r);
    }
    let q = json!({"records":records,"silver_records":facts});
    let html = report::html(&q);
    assert!(html.contains("4 gekoppelde instructie/event-pakketten"));
    assert!(html.contains("Opgenomen invocation-volgorde"));
    assert!(html.contains("Werkelijke CPI-flags"));
    assert!(html.contains("UNAVAILABLE"));
    assert_eq!(html, report::html(&q));
    let buy = pump::inspect(&tx(0)).unwrap();
    assert_eq!(
        buy["buy_source_diagnostics"][0]["full_instruction_error"]["reason"],
        "UNEXPECTED_TRAILING_BYTES"
    );
    assert!(inspect(&tx(0)).is_empty());
}
