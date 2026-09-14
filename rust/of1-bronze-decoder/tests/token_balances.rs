//! Sealed authentic CAR fixtures and explicitly synthetic protobuf boundaries.
use of1_bronze_decoder::{
    archive::{self, Envelope},
    codec,
    proto::Status,
    pump_buy_exact_quote_v2 as buy, pump_sell,
    token_balances::{self as balances, Amount, Balance},
};
use of1_range_recorder::sha256;
use prost::Message;
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::collections::BTreeMap;

const MINT: &str = "So11111111111111111111111111111111111111112";
const OWNER: &str = "11111111111111111111111111111111";
const PROGRAM: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
fn balance() -> Balance {
    Balance {
        account_index: Some(0),
        mint: Some(MINT.into()),
        owner: Some(OWNER.into()),
        program_id: Some(PROGRAM.into()),
        ui_token_amount: Some(Amount {
            amount: Some("1234567".into()),
            decimals: Some(6),
            ..Amount::default()
        }),
    }
}
fn projection(pre: Vec<Vec<u8>>, post: Vec<Vec<u8>>, keys: &[String]) -> Value {
    let s = Status {
        pre_token_balances_unprojected: pre,
        post_token_balances_unprojected: post,
        ..Status::default()
    };
    let bytes = s.encode_to_vec();
    let decoded = Status::decode(bytes.as_slice()).unwrap();
    balances::project(&decoded, keys, &bytes, &bytes)
}
fn one(b: &Balance) -> Value {
    projection(vec![b.encode_to_vec()], vec![], &[OWNER.into()])["observations"][0].clone()
}
fn raw_one(bytes: &[u8]) -> Value {
    projection(vec![bytes.to_vec()], vec![], &[OWNER.into()])["observations"][0].clone()
}

#[test]
fn empty_repeated_is_unavailable_not_zero_and_missing_message_never_constructs_amount() {
    let empty = projection(vec![], vec![], &[]);
    assert_eq!(empty["observation_count"], 0);
    assert_eq!(
        empty["collection_status"],
        "NO_OBSERVATIONS_RECORDED_EMPTY_OR_UNAVAILABLE"
    );
    let mut b = balance();
    b.ui_token_amount = None;
    let v = one(&b);
    assert_eq!(v["disposition"], "MISSING");
    assert!(v["amount_u64"].is_null());
    assert!(v["decimals"].is_null());
    assert_eq!(v["presence"]["ui_token_amount"], false);
    assert_eq!(v["field_states"]["decimals"], "UNAVAILABLE_MISSING_MESSAGE");
    b.ui_token_amount = Some(Amount::default());
    let v = one(&b);
    assert_eq!(v["amount_string"], "");
    assert!(v["amount_u64"].is_null());
    assert_eq!(v["decimals"], 0);
    assert_eq!(v["presence"]["decimals"], false);
    assert_eq!(v["disposition"], "MISSING");
}

#[test]
fn omitted_scalar_defaults_and_explicit_zero_remain_distinguishable() {
    let mut b = balance();
    b.account_index = None;
    b.ui_token_amount.as_mut().unwrap().decimals = None;
    let omitted = one(&b);
    assert_eq!(omitted["account_index"], 0);
    assert_eq!(omitted["account_key"], OWNER);
    assert_eq!(omitted["decimals"], 0);
    assert_eq!(omitted["exact_decimal_amount"], "1234567");
    assert_eq!(omitted["presence"]["account_index"], false);
    assert_eq!(omitted["presence"]["decimals"], false);
    assert_eq!(omitted["field_states"]["account_index"], "PROTO3_DEFAULT");
    assert_eq!(omitted["field_states"]["decimals"], "PROTO3_DEFAULT");
    b.account_index = Some(0);
    b.ui_token_amount.as_mut().unwrap().decimals = Some(0);
    let explicit = one(&b);
    assert_eq!(explicit["presence"]["account_index"], true);
    assert_eq!(explicit["presence"]["decimals"], true);
    assert_eq!(
        explicit["exact_decimal_amount"],
        omitted["exact_decimal_amount"]
    );
    assert_ne!(
        explicit["raw_token_balance_hex"],
        omitted["raw_token_balance_hex"]
    );
}

#[test]
fn empty_address_defaults_are_preserved_not_promoted_to_an_identity() {
    let mut b = balance();
    b.mint = None;
    b.owner = Some(String::new());
    b.program_id = None;
    let v = one(&b);
    assert_eq!(v["mint"], "");
    assert_eq!(v["owner"], "");
    assert_eq!(v["program_id"], "");
    assert_eq!(v["presence"]["mint"], false);
    assert_eq!(v["presence"]["owner"], true);
    assert_eq!(v["disposition"], "MISSING");
    assert!(v["exact_decimal_amount"].is_null());
    b.mint = Some("not a public key".into());
    assert_eq!(one(&b)["disposition"], "QUARANTINED");
}

#[test]
fn exact_u64_strings_and_decimal_rendering_never_use_ui_float() {
    for (amount, decimals, display) in [
        ("18446744073709551615", 6, "18446744073709.551615"),
        ("9007199254740993", 0, "9007199254740993"),
        ("1", 6, "0.000001"),
        ("0", 6, "0.000000"),
        ("0001", 2, "0.01"),
    ] {
        let mut b = balance();
        let ui = b.ui_token_amount.as_mut().unwrap();
        ui.amount = Some(amount.into());
        ui.decimals = Some(decimals);
        ui.ui_amount = Some(f64::NAN);
        ui.ui_amount_string = Some("WRONG_DISPLAY".into());
        let v = one(&b);
        assert_eq!(v["disposition"], "PROJECTED");
        assert_eq!(v["amount_string"], amount);
        assert_eq!(v["amount_u64"], amount.parse::<u64>().unwrap().to_string());
        assert_eq!(v["exact_decimal_amount"], display);
        assert_eq!(
            v["ui_amount_bits_hex"],
            format!("{:016x}", f64::NAN.to_bits())
        );
        assert_eq!(v["ui_amount_string"], "WRONG_DISPLAY");
        assert!(serde_json::to_vec(&v).is_ok());
    }
    for invalid in ["18446744073709551616", "-1", "+1", "1.0", "1e3", " 0"] {
        let mut b = balance();
        b.ui_token_amount.as_mut().unwrap().amount = Some(invalid.into());
        let v = one(&b);
        assert_eq!(v["disposition"], "QUARANTINED");
        assert!(v["amount_u64"].is_null());
        assert_eq!(v["amount_string"], invalid);
    }
}

#[test]
fn account_index_and_decimal_u8_bounds_are_checked_not_truncated() {
    let mut b = balance();
    b.account_index = Some(255);
    let keys = (0..256)
        .map(|n| format!("resolved-{n}"))
        .collect::<Vec<_>>();
    let v = projection(vec![b.encode_to_vec()], vec![], &keys)["observations"][0].clone();
    assert_eq!(v["account_index"], 255);
    assert_eq!(v["account_key"], "resolved-255");
    b.account_index = Some(256);
    assert_eq!(
        projection(vec![b.encode_to_vec()], vec![], &keys)["observations"][0]["disposition"],
        "QUARANTINED"
    );
    b.account_index = Some(1);
    assert_eq!(one(&b)["disposition"], "QUARANTINED");
    b.account_index = Some(0);
    b.ui_token_amount.as_mut().unwrap().decimals = Some(255);
    assert_eq!(one(&b)["exact_decimal_amount"].as_str().unwrap().len(), 257);
    b.ui_token_amount.as_mut().unwrap().decimals = Some(256);
    let v = one(&b);
    assert_eq!(v["decimals"], 256);
    assert!(v["exact_decimal_amount"].is_null());
    assert_eq!(v["disposition"], "QUARANTINED");
}

#[test]
fn malformed_nested_wire_keeps_original_bytes_and_an_explicit_quarantine() {
    // Wrong wire, truncated field/message, uint32 overflow, varint overflow,
    // unknown unsupported group, zero tag, and invalid UTF-8.
    for bytes in [
        vec![0x0a, 0],
        vec![0x08],
        vec![0x1a, 2, 0x10],
        vec![0x08, 0x80, 0x80, 0x80, 0x80, 0x10],
        vec![0x1a, 6, 0x10, 0x80, 0x80, 0x80, 0x80, 0x10],
        vec![
            0x08, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 2,
        ],
        vec![0x3b],
        vec![0],
        vec![0x12, 1, 0xff],
    ] {
        let v = raw_one(&bytes);
        assert_eq!(v["disposition"], "QUARANTINED", "{bytes:?}: {v}");
        assert_eq!(v["raw_token_balance_hex"], hex::encode(&bytes));
        assert_eq!(v["raw_token_balance_sha256"], sha256(&bytes));
    }
}

#[test]
fn protobuf_last_scalar_and_message_merge_semantics_keep_original_unknown_bytes() {
    let mut b = balance();
    b.ui_token_amount.as_mut().unwrap().decimals = Some(9);
    let mut raw = b.encode_to_vec();
    // A second singular amount message updates only decimals; amount survives.
    raw.extend([0x1a, 2, 0x10, 6]);
    // Unknown field 100 = 123 remains in raw, with no extra domain meaning.
    raw.extend([0xa0, 0x06, 123]);
    let v = raw_one(&raw);
    assert_eq!(v["decimals"], 6);
    assert_eq!(v["amount_string"], "1234567");
    assert_eq!(v["exact_decimal_amount"], "1.234567");
    assert_eq!(v["raw_token_balance_hex"], hex::encode(&raw));
    raw.extend([0x08, 1, 0x08, 0]);
    assert_eq!(raw_one(&raw)["account_index"], 0);
}

#[test]
fn repeated_observations_preserve_side_order_and_duplicates_without_deduplication() {
    let a = balance().encode_to_vec();
    let mut b = balance();
    b.ui_token_amount.as_mut().unwrap().amount = Some("9".into());
    let c = projection(
        vec![a.clone(), a.clone()],
        vec![b.encode_to_vec(), a],
        &[OWNER.into()],
    );
    let o = c["observations"].as_array().unwrap();
    assert_eq!(o.len(), 4);
    assert_eq!(
        (o[0]["side"].clone(), o[0]["ordinal"].clone()),
        (json!("PRE"), json!(0))
    );
    assert_eq!(
        (o[1]["side"].clone(), o[1]["ordinal"].clone()),
        (json!("PRE"), json!(1))
    );
    assert_eq!(
        (o[2]["side"].clone(), o[2]["ordinal"].clone()),
        (json!("POST"), json!(0))
    );
    assert_eq!(o[0]["raw_token_balance_hex"], o[1]["raw_token_balance_hex"]);
    assert_eq!(o[2]["amount_u64"], "9");
}

fn authentic_fixture(n: usize, sell: bool) -> Value {
    let bytes = if sell {
        include_str!("fixtures/authentic-sell16-sections.json")
    } else {
        include_str!("fixtures/authentic-buy-exact-quote-v2-sections.json")
    };
    serde_json::from_str::<Value>(bytes).unwrap()["fixtures"][n].clone()
}
fn authentic_record(n: usize, sell: bool) -> Value {
    let f = authentic_fixture(n, sell);
    let raw = hex::decode(f["section_hex"].as_str().unwrap()).unwrap();
    assert_eq!(sha256(&raw), f["section_sha256"]);
    let start = raw.iter().position(|b| b & 128 == 0).unwrap() + 1;
    let cid = &raw[start..start + 36];
    assert_eq!(sha256(&raw[start + 36..]), hex::encode(&cid[4..]));
    let C::Array(a) = serde_cbor::from_slice(&raw[start + 36..]).unwrap() else {
        panic!("array")
    };
    assert_eq!(a.len(), 5);
    assert_eq!(a[0], C::Integer(0));
    let t = codec::decode(
        &Envelope {
            cid_hex: hex::encode(cid),
            physical_node_index: 0,
            raw_offset: 0,
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
        },
        &BTreeMap::new(),
    )
    .unwrap();
    // The older sell fixture retains its authentic section but no standalone
    // receipt. Do not invent one: only that test package wrapper is synthetic.
    let raw_hash = if sell {
        &f["raw_sha256"]
    } else {
        &f["source_raw_sha256"]
    };
    let bindings = if sell {
        json!({"fixture":"SYNTHETIC_PACKAGE_FOR_SEALED_AUTHENTIC_BYTES"})
    } else {
        f["source_bindings"].clone()
    };
    let mut record = json!({"disposition":"DECODED","atomic_observation_package":true,"decoder_source_sha256":of1_bronze_decoder::source_sha256(),"input_kind":if sell{"SEALED_AUTHENTIC_BYTES_SYNTHETIC_PACKAGE"}else{"AUTHENTIC_RANGE"},"receipt_evidence":if sell{"FIXTURE_PACKAGE_ONLY"}else{"AUTHENTIC_RAW"},"slice_class":f["slice_class"],"sample_identity":f["sample_identity"],"source":{"raw_sha256":raw_hash,"transaction_node_cid_hex":f["cid_hex"],"bindings":bindings},"effective_at":{"slot":f["slot"],"transaction_index_in_slot":f["transaction_index_in_slot"]},"transaction":t});
    if sell {
        record.as_object_mut().unwrap().remove("sample_identity");
        record.as_object_mut().unwrap().remove("slice_class");
    }
    record
}
fn fact(record: &Value, sell: bool) -> Value {
    let mut facts = if sell {
        pump_sell::facts(record)
    } else {
        buy::facts(record)
    }
    .unwrap();
    assert_eq!(facts.len(), 1);
    facts.remove(0)
}

#[test]
fn authentic_four_buys_and_tokenkeg_sell_bind_explicit_metadata_decimals_not_account_state() {
    for (n, sell) in [(0, false), (1, false), (2, false), (3, false), (0, true)] {
        let r = authentic_record(n, sell);
        let f = fact(&r, sell);
        let before = f.clone();
        let c = balances::trade_context(&r, &f);
        assert_eq!(c["binding_status"], "BOUND_RECORDED_BASE_UNITS", "{c}");
        assert_eq!(c["decimals"], 6);
        assert_eq!(c["decimals_evidence"], "EXPLICIT_WIRE");
        assert_eq!(
            c["matched_observation_indexes"].as_array().unwrap().len(),
            4
        );
        assert_eq!(c["roles"].as_array().unwrap().len(), 2);
        assert_eq!(
            c["event_token_amount_u64"],
            f["event_reported"]["token_amount_raw_u64"]
        );
        assert_eq!(c["account_contents"], "NOT_A_FULL_ACCOUNT_SNAPSHOT");
        assert_eq!(c["cpi_privileges"], "UNAVAILABLE_NOT_RECORDED");
        assert!(f["base_decimals"].is_null());
        assert_eq!(f, before);
        assert_eq!(c, balances::trade_context(&r, &f));
        let tx = &r["transaction"];
        let nested = &tx["token_balance_context"];
        let metadata = hex::decode(tx["protobuf_metadata_hex"].as_str().unwrap()).unwrap();
        assert_eq!(nested["protobuf_metadata_sha256"], sha256(&metadata));
        let status = Status::decode(metadata.as_slice()).unwrap();
        let original = status
            .pre_token_balances_unprojected
            .iter()
            .chain(&status.post_token_balances_unprojected)
            .collect::<Vec<_>>();
        for (i, o) in nested["observations"]
            .as_array()
            .unwrap()
            .iter()
            .enumerate()
        {
            assert_eq!(o["raw_token_balance_hex"], hex::encode(original[i]));
            assert_eq!(o["raw_token_balance_sha256"], sha256(original[i]));
            assert_eq!(
                tx["account_keys"][usize::try_from(o["account_index"].as_u64().unwrap()).unwrap()],
                o["account_key"]
            );
        }
    }
}

#[test]
fn authentic_failed_transaction_keeps_metadata_without_successful_trade_admission() {
    let r = authentic_record(4, false);
    assert_eq!(r["transaction"]["status"], "ERROR");
    assert!(buy::facts(&r).unwrap().is_empty());
    assert!(
        r["transaction"]["token_balance_context"]["observation_count"]
            .as_u64()
            .unwrap()
            > 0
    );
}

#[test]
fn mismatched_or_conflicting_units_change_only_additive_context_not_trade_admission() {
    let r = authentic_record(0, false);
    let f = fact(&r, false);
    let c = balances::trade_context(&r, &f);
    let i = usize::try_from(c["matched_observation_indexes"][0].as_u64().unwrap()).unwrap();
    for (field, value) in [
        ("mint", json!(OWNER)),
        ("owner", json!(MINT)),
        ("program_id", json!(PROGRAM)),
        ("account_key", json!(OWNER)),
        ("decimals", json!(7)),
    ] {
        let mut changed = r.clone();
        changed["transaction"]["token_balance_context"]["observations"][i][field] = value;
        let after = balances::trade_context(&changed, &f);
        assert_eq!(after["binding_status"], "CONFLICTING", "{field}: {after}");
        assert!(after["event_token_amount_decimal"].is_null());
        assert_eq!(fact(&changed, false)["event_reported"], f["event_reported"]);
    }
    let mut changed = r.clone();
    changed["transaction"]["token_balance_context"]["observations"] = json!([]);
    let after = balances::trade_context(&changed, &f);
    assert_eq!(after["binding_status"], "UNAVAILABLE");
    assert!(after["decimals"].is_null());
    assert_eq!(fact(&changed, false)["instruction"], f["instruction"]);
}

#[test]
fn missing_side_is_not_zero_and_duplicate_side_never_selects_a_convenient_delta() {
    let r = authentic_record(0, false);
    let f = fact(&r, false);
    let c = balances::trade_context(&r, &f);
    let i = usize::try_from(
        c["roles"][0]["pre_observation_indexes"][0]
            .as_u64()
            .unwrap(),
    )
    .unwrap();
    let mut duplicate = r.clone();
    let observation = duplicate["transaction"]["token_balance_context"]["observations"][i].clone();
    duplicate["transaction"]["token_balance_context"]["observations"]
        .as_array_mut()
        .unwrap()
        .push(observation);
    let c = balances::trade_context(&duplicate, &f);
    assert_eq!(c["binding_status"], "CONFLICTING");
    assert!(c["roles"][0]["transaction_delta_raw_signed"].is_null());
    let mut missing = r.clone();
    missing["transaction"]["token_balance_context"]["observations"]
        .as_array_mut()
        .unwrap()
        .remove(i);
    let c = balances::trade_context(&missing, &f);
    assert!(c["roles"][0]["transaction_delta_raw_signed"].is_null());
    assert_eq!(
        c["roles"][0]["delta_status"],
        "UNAVAILABLE_MISSING_OR_AMBIGUOUS_SIDE"
    );
    // Raw amount delta can differ from this admitted event. No equality gate or
    // inferred fee/net proceeds is introduced into the existing trade facts.
    let mut changed = r;
    let observation = &mut changed["transaction"]["token_balance_context"]["observations"][i];
    observation["amount_u64"] = json!(u64::MAX.to_string());
    let c = balances::trade_context(&changed, &f);
    assert!(
        c["roles"][0]["transaction_delta_raw_signed"]
            .as_str()
            .unwrap()
            .starts_with('-')
    );
    assert_eq!(
        c["delta_semantics"],
        "TRANSACTION_WIDE_NOT_SINGLE_INSTRUCTION_QUANTITY"
    );
    assert_eq!(fact(&changed, false)["event_reported"], f["event_reported"]);
}

#[test]
fn pinned_source_receipt_binds_exact_proto_and_explains_lossy_conversion_defaults() {
    let s: Value = serde_json::from_slice(balances::SOURCE).unwrap();
    assert_eq!(s["commit"], "6c1ba34691f17ac902ae3d2e1147eed5723b9cef");
    assert_eq!(
        sha256(include_bytes!("../sources/confirmed_block.proto")),
        s["sources"][0]["sha256"]
    );
    let all: Value = serde_json::from_str(include_str!("../sources.json")).unwrap();
    assert!(
        serde_json::to_string(&all)
            .unwrap()
            .contains(s["sources"][1]["sha256"].as_str().unwrap())
    );
    assert!(
        s["presence"]["repeated_empty"]
            .as_str()
            .unwrap()
            .contains("None and Some(empty)")
    );
}
