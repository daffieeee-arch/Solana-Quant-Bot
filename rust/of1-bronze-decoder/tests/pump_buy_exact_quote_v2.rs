//! Exact authentic CAR envelopes; every changed byte/context below is synthetic.
use of1_bronze_decoder::{
    archive::{self, Envelope},
    codec, pump, pump_buy_exact_quote_v2 as buy, pump_sell,
};
use of1_range_recorder::sha256;
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn fixture(n: usize) -> Value {
    serde_json::from_str::<Value>(include_str!(
        "fixtures/authentic-buy-exact-quote-v2-sections.json"
    ))
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
        C::Integer(f["slot"].as_str().unwrap().parse().unwrap())
    );
    assert_eq!(
        a[4],
        C::Integer(i128::from(f["transaction_index_in_slot"].as_u64().unwrap()))
    );
    let num = |k| usize::try_from(f[k].as_u64().unwrap()).unwrap();
    codec::decode(
        &Envelope {
            cid_hex: hex::encode(cid),
            physical_node_index: num("physical_node_index"),
            raw_offset: num("raw_offset"),
            raw_length: raw.len(),
            entry_index: num("entry_index"),
            transaction_index_in_entry: num("transaction_index_in_entry"),
            transaction_index_in_slot: num("transaction_index_in_slot"),
            source_transaction_index: None,
            data: archive::frame(&a[1]).unwrap(),
            metadata: archive::frame(&a[2]).unwrap(),
        },
        &BTreeMap::new(),
    )
    .unwrap()
}
fn diagnosis(t: &Value) -> Value {
    buy::inspect(t).unwrap().remove(0)
}
fn record(n: usize) -> Value {
    let f = fixture(n);
    json!({"disposition":"DECODED","atomic_observation_package":true,"decoder_source_sha256":of1_bronze_decoder::source_sha256(),"input_kind":"AUTHENTIC_RANGE","receipt_evidence":"AUTHENTIC_RAW","slice_class":f["slice_class"],"sample_identity":f["sample_identity"],"source":{"raw_sha256":f["source_raw_sha256"],"raw_path":f["source_raw_path"],"transaction_node_cid_hex":f["cid_hex"],"raw_section_offset":f["raw_offset"],"raw_section_length":f["section_length"],"bindings":f["source_bindings"]},"effective_at":{"slot":f["slot"],"transaction_index_in_slot":f["transaction_index_in_slot"]},"transaction":tx(n)})
}
fn event_position(t: &Value) -> usize {
    t["inner_instructions"]
        .as_array()
        .unwrap()
        .iter()
        .position(|v| {
            v["program_id"] == pump_protocol_v2::registry::PUMP_PROGRAM_ID
                && v["data_hex"]
                    .as_str()
                    .unwrap()
                    .starts_with("e445a52e51cb9a1d")
        })
        .unwrap()
}
fn changed_event(offset: usize, bytes: &[u8]) -> Value {
    let mut t = tx(0);
    let n = event_position(&t);
    let mut b = hex::decode(t["inner_instructions"][n]["data_hex"].as_str().unwrap()).unwrap();
    b[offset..offset + bytes.len()].copy_from_slice(bytes);
    t["inner_instructions"][n]["data_hex"] = json!(hex::encode(b));
    t
}
fn rejected(t: &Value) {
    let d = diagnosis(t);
    assert_eq!(d["disposition"], "NOT_ADMITTED", "{d}");
    assert_eq!(d["matching_registered_candidates"], json!([]));
    assert!(d["selected_candidate"].is_null());
}

#[test]
fn authentic_four_successes_match_full_v2_bytes_27_roles_and_own_event_not_legacy_buy() {
    let expected = [
        ("50000000", "116137504513", false),
        ("50000000", "451176803529", false),
        ("1000000000", "19902323483147", true),
        ("1000000000", "13605139088884", true),
    ];
    for (n, (spend, min, mayhem)) in expected.into_iter().enumerate() {
        let t = tx(n);
        let old = pump::inspect(&t).unwrap();
        let d = diagnosis(&t);
        assert_eq!(t["status"], "OK");
        assert_eq!(d["disposition"], "MATCHED_RECORDED_EVENT_FACTS", "{d}");
        assert_eq!(d["selected_candidate"], buy::CANDIDATE);
        assert_eq!(d["matching_registered_candidates"], json!([buy::CANDIDATE]));
        assert_eq!(d["instruction_bytes"], 24);
        assert_eq!(
            d["instruction_data_hex"],
            fixture(n)["instruction_data_hex"]
        );
        assert_eq!(d["instruction_sha256"], fixture(n)["instruction_sha256"]);
        assert_eq!(d["instruction"]["spendable_quote_in_raw_u64"], spend);
        assert_eq!(d["instruction"]["min_tokens_out_raw_u64"], min);
        assert!(d["instruction"].get("track_volume").is_none());
        assert_eq!(d["event_context"]["event_cpi_bytes"], 381);
        assert_eq!(d["event_context"]["outer_index"], 3);
        assert_eq!(d["event_context"]["inner_order"], if n < 2 { 6 } else { 5 });
        assert_eq!(d["event_context"]["stack_height"], 2);
        assert!(d["event_context"]["parent"]["inner_order"].is_null());
        assert_eq!(d["event_reported"]["ix_name"], "buy_exact_quote_in");
        assert_eq!(d["event_reported"]["is_buy"], true);
        assert_eq!(d["event_reported"]["mayhem_mode"], mayhem);
        assert_eq!(d["event_reported"]["quote_mint_raw_hex"], "00".repeat(32));
        assert_eq!(
            d["quote_account_address"],
            "So11111111111111111111111111111111111111112"
        );
        assert!(d["quote_decimals"].is_null());
        assert!(d["base_decimals"].is_null());
        let rows = d["accounts"].as_array().unwrap();
        assert_eq!(rows.len(), 27);
        assert!(
            rows.iter()
                .all(|r| r["address_match"] == true && r["required_privileges_match"] == true)
        );
        assert_eq!(rows.iter().filter(|r| !r["pda_bump"].is_null()).count(), 16);
        assert_eq!(rows[13]["message_signer"], true);
        assert!(rows.iter().all(|r| r["account_contents_verified"] == false));
        assert_eq!(pump::inspect(&t).unwrap(), old);
        assert!(pump_sell::inspect(&t).unwrap().is_empty());
        assert_eq!(d, diagnosis(&t));
    }
}

#[test]
fn authentic_failed_buy_keeps_failure_missing_event_and_never_emits_success() {
    let t = tx(4);
    assert_eq!(t["status"], "ERROR");
    let d = diagnosis(&t);
    assert_eq!(d["reason"], "MISSING_EVENT");
    assert_eq!(d["transaction_status"], "ERROR");
    rejected(&t);
    assert!(buy::facts(&record(4)).unwrap().is_empty());
}

#[test]
fn synthetic_instruction_boundaries_discriminators_and_all_u64_bits_are_exact() {
    let original = hex::decode(fixture(0)["instruction_data_hex"].as_str().unwrap()).unwrap();
    for n in 0..24 {
        assert_eq!(
            buy::decode_instruction(&original[..n]),
            Err(buy::Rejection::TruncatedInstruction)
        );
    }
    for extra in [0, 1, 255] {
        let mut b = original.clone();
        b.push(extra);
        assert_eq!(
            buy::decode_instruction(&b),
            Err(buy::Rejection::TrailingInstruction)
        );
    }
    for disc in ["66063d1201daebea", "b817ee6167c5d33d", "33e685a4017f83ad"] {
        let mut b = original.clone();
        b[..8].copy_from_slice(&hex::decode(disc).unwrap());
        assert_eq!(
            buy::decode_instruction(&b),
            Err(buy::Rejection::WrongDiscriminator)
        );
    }
    for v in [0, u64::MAX, 1_u64 << 53, (1_u64 << 63) + 1] {
        let mut b = original.clone();
        b[8..16].copy_from_slice(&v.to_le_bytes());
        b[16..24].copy_from_slice(&v.to_le_bytes());
        let i = buy::decode_instruction(&b).unwrap();
        assert_eq!((i.spendable_quote_in, i.min_tokens_out), (v, v));
    }
}

#[test]
fn synthetic_all_account_roles_token_programs_pdas_and_direct_signer_reject_mismatch() {
    for pos in 0..27 {
        let mut t = tx(0);
        let key = usize::try_from(
            t["instructions"][3]["account_indexes"][pos]
                .as_u64()
                .unwrap(),
        )
        .unwrap();
        t["account_keys"][key] = json!(bs58::encode([77; 32]).into_string());
        rejected(&t);
    }
    for count in [0, 16, 18, 26, 28] {
        let mut t = tx(0);
        t["instructions"][3]["account_indexes"]
            .as_array_mut()
            .unwrap()
            .resize(count, json!(0));
        rejected(&t);
    }
    let mut t = tx(0);
    t["instructions"][3]["account_indexes"][8] = t["instructions"][3]["account_indexes"][6].clone();
    rejected(&t);
    let mut t = tx(0);
    t["message_account_layout"]["required_signatures"] = json!(0);
    rejected(&t);
    let mut t = tx(0);
    t["message_account_layout"] = Value::Null;
    let d = diagnosis(&t);
    assert!(d["accounts"][13]["message_signer"].is_null());
    rejected(&t);
    for offset in [16, 65, 137, 185] {
        let t = changed_event(offset, &[77; 32]);
        rejected(&t);
    }
}

#[test]
fn synthetic_full_event_exhaustion_label_bool_vector_and_integer_limits() {
    let t = tx(0);
    let p = event_position(&t);
    let bytes = hex::decode(t["inner_instructions"][p]["data_hex"].as_str().unwrap()).unwrap();
    for n in [0, 7, 8, 15, 16, 380, 382, 4097] {
        let mut b = bytes.clone();
        b.resize(n, 0);
        assert!(buy::decode_event(&b).is_err());
    }
    for offset in [0, 8] {
        let mut b = bytes.clone();
        b[offset] ^= 1;
        assert_eq!(buy::decode_event(&b), Err(buy::Rejection::InvalidEvent));
    }
    for offset in [64, 233, 288] {
        let mut b = bytes.clone();
        b[offset] = 2;
        assert_eq!(buy::decode_event(&b), Err(buy::Rejection::InvalidEvent));
    }
    let mut b = bytes.clone();
    b[270] = b's';
    assert_eq!(
        buy::decode_event(&b),
        Err(buy::Rejection::UnsupportedEventVariant)
    );
    let mut b = bytes.clone();
    b[266..270].copy_from_slice(&33_u32.to_le_bytes());
    assert_eq!(buy::decode_event(&b), Err(buy::Rejection::InvalidEvent));
    let mut b = bytes.clone();
    b[321..325].copy_from_slice(&129_u32.to_le_bytes());
    assert_eq!(buy::decode_event(&b), Err(buy::Rejection::InvalidEvent));
    for v in [0, u64::MAX] {
        let mut b = bytes.clone();
        b[48..56].copy_from_slice(&v.to_le_bytes());
        assert_eq!(buy::decode_event(&b).unwrap().sol_amount, v);
    }
    for v in [i64::MIN, i64::MAX] {
        let mut b = bytes.clone();
        b[97..105].copy_from_slice(&v.to_le_bytes());
        assert_eq!(buy::decode_event(&b).unwrap().timestamp, v);
    }
}

#[test]
fn synthetic_own_context_rejects_neighbor_duplicate_missing_height_and_subtree_swap() {
    for pos in 0..7 {
        for h in [Value::Null, json!(1), json!(99)] {
            let mut t = tx(0);
            t["inner_instructions"][pos]["stack_height"] = h;
            rejected(&t);
        }
    }
    let mut t = tx(0);
    t["inner_instructions"][6]["stack_height"] = json!(3);
    rejected(&t);
    let mut t = tx(0);
    t["inner_instructions"][6]["outer_index"] = json!(2);
    rejected(&t);
    let mut t = tx(0);
    t["inner_instructions"][6]["account_indexes"] = json!([0]);
    rejected(&t);
    let mut t = tx(0);
    t["inner_instructions"][6]["program_id_index"] = json!(0);
    rejected(&t);
    let mut t = tx(0);
    let mut e = t["inner_instructions"][6].clone();
    e["inner_order"] = json!(7);
    t["inner_instructions"].as_array_mut().unwrap().push(e);
    assert_eq!(diagnosis(&t)["reason"], "AMBIGUOUS_EVENT");
    rejected(&t);
    let mut t = tx(0);
    t["inner_instructions"][5]["inner_order"] = json!(6);
    rejected(&t);
    let mut t = tx(0);
    let other = tx(1);
    t["inner_instructions"][6]["data_hex"] = other["inner_instructions"][6]["data_hex"].clone();
    rejected(&t);
}

#[test]
fn synthetic_argument_event_pattern_status_and_caller_count_cannot_force_admission() {
    let mut t = tx(0);
    let mut b = hex::decode(t["instructions"][3]["data_hex"].as_str().unwrap()).unwrap();
    b[16..24].copy_from_slice(&u64::MAX.to_le_bytes());
    t["instructions"][3]["data_hex"] = json!(hex::encode(&b));
    rejected(&t);
    b[8..16].copy_from_slice(&0_u64.to_le_bytes());
    b[16..24].copy_from_slice(&0_u64.to_le_bytes());
    t["instructions"][3]["data_hex"] = json!(hex::encode(b));
    rejected(&t);
    for (offset, bytes) in [
        (233, vec![1]),
        (325, vec![77; 32]),
        (357, 1_u64.to_le_bytes().to_vec()),
    ] {
        rejected(&changed_event(offset, &bytes));
    }
    for status in [Value::Null, json!("ERROR"), json!("UNKNOWN")] {
        let mut t = tx(0);
        t["status"] = status;
        rejected(&t);
    }
    let mut t = tx(0);
    t["transaction_error"] = json!({"synthetic":true});
    t["compatible_candidate_count"] = json!(1);
    rejected(&t);
}

#[test]
fn authentic_fact_binds_source_sample_exact_bytes_and_never_relabels_unknowns() {
    for n in 0..4 {
        let r = record(n);
        let unchanged = r.clone();
        let facts = buy::facts(&r).unwrap();
        assert_eq!(facts.len(), 1);
        let f = &facts[0];
        assert_eq!(f["schema"], "PUMP_SILVER_RECORDED_BUY_EXACT_QUOTE_V2_1");
        assert_eq!(
            f["bronze_record_sha256"],
            sha256(&serde_json::to_vec(&r).unwrap())
        );
        assert_eq!(f["source"], r["source"]);
        assert_eq!(f["sample_identity"], r["sample_identity"]);
        assert_eq!(f["slice_class"], "RESEARCH_SAMPLING");
        assert_eq!(f["event_reported"]["is_buy"], true);
        assert_eq!(f["event_reported"]["quote_mint_raw_hex"], "00".repeat(32));
        assert!(f["quote_decimals"].is_null());
        assert!(f["base_decimals"].is_null());
        assert_eq!(f["research_ready"], false);
        assert_eq!(facts, buy::facts(&r).unwrap());
        assert_eq!(r, unchanged);
    }
    for key in ["raw_sha256", "bindings", "transaction_node_cid_hex"] {
        let mut r = record(0);
        r["source"][key] = Value::Null;
        assert!(buy::facts(&r).is_err());
    }
    let mut r = record(0);
    r["atomic_observation_package"] = json!(false);
    assert!(buy::facts(&r).is_err());
    let mut r = record(0);
    r["sample_identity"]["seed"] = json!("changed");
    assert!(buy::facts(&r).is_err());
    let mut r = record(0);
    r["effective_at"]["slot"] = json!("422669519");
    assert!(buy::facts(&r).is_err());
    let mut r = record(0);
    r.as_object_mut().unwrap().remove("sample_identity");
    assert!(buy::facts(&r).is_err());
}

#[test]
fn source_discriminators_and_precise_new_profile_have_no_legacy_parser_override() {
    let source: Value = serde_json::from_slice(buy::SOURCE).unwrap();
    assert_eq!(source["instruction"]["name"], "buy_exact_quote_in_v2");
    assert_eq!(source["instruction"]["bytes"], 24);
    assert_eq!(source["account_rules"].as_array().unwrap().len(), 27);
    assert_eq!(
        source["account_rules"][16]["pda"]["seeds"][1]["path"],
        "bonding_curve.creator"
    );
    assert_eq!(
        &hex::decode(sha256(b"global:buy_exact_quote_in_v2")).unwrap()[..8],
        buy::DISCRIMINATOR
    );
    assert_eq!(source["event"]["ix_name_wire_type"], "string");
    assert_eq!(source["profile"]["quote_decimals"], Value::Null);
    let mut t = tx(0);
    t["instructions"][3]["data_hex"] = json!("66063d1201daebea0000000000000000000000000000000000");
    assert!(buy::inspect(&t).unwrap().is_empty());
}
