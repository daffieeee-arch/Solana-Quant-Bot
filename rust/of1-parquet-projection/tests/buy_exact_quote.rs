//! Synthetic physical-projection checks, not protocol or authentic observations.
use arrow_array::{Array, ArrayRef, RecordBatch, StringArray, UInt64Array};
use of1_parquet_projection::{
    columns::{self, Layer},
    hash, storage,
};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use serde_json::{Value, json};
use std::{fs::File, sync::Arc};

const SELL: &str = "PUMP_SILVER_RECORDED_SELL_1";
const BUY: &str = "PUMP_SILVER_RECORDED_BUY_EXACT_QUOTE_V2_1";

fn record(schema: &str) -> Value {
    json!({
        "schema":schema,"slice_class":"ENGINEERING_VALIDATION_ONLY",
        "input_kind":"FIXTURE_SYNTHETIC_PROJECTION_TEST","receipt_evidence":"Fixture",
        "effective_at":{"slot":"422669516","transaction_index_in_slot":647},
        "decoder_source_sha256":"a".repeat(64),"source":{"raw_sha256":"b".repeat(64)},
        "transaction_status":"OK","committed_account_state":"UNAVAILABLE",
        "base_decimals":null,"quote_decimals":null,
        "event_context":{"cpi_account_flags":{"evidence":"UNAVAILABLE","signer":null}}
    })
}

fn line(record: &Value) -> Vec<u8> {
    let mut bytes = serde_json::to_vec(record).unwrap();
    bytes.push(b'\n');
    bytes
}

fn unsigned<'a>(batch: &'a RecordBatch, name: &str) -> &'a UInt64Array {
    batch
        .column_by_name(name)
        .unwrap()
        .as_any()
        .downcast_ref::<UInt64Array>()
        .unwrap()
}

fn text<'a>(batch: &'a RecordBatch, name: &str) -> &'a StringArray {
    batch
        .column_by_name(name)
        .unwrap()
        .as_any()
        .downcast_ref::<StringArray>()
        .unwrap()
}

#[test]
fn only_reviewed_silver_schemas_are_admitted_and_fingerprinted() {
    assert_eq!(Layer::Silver.record_schemas(), &[SELL, BUY]);
    assert_eq!(
        columns::schema_descriptor(Layer::Silver)["accepted_record_schemas"],
        json!([SELL, BUY])
    );
    for schema in [SELL, BUY] {
        assert!(columns::parse_record(Layer::Silver, &line(&record(schema))).is_ok());
        assert!(columns::parse_record(Layer::Bronze, &line(&record(schema))).is_err());
    }
    for schema in [
        "PUMP_SILVER_RECORDED_BUY_1",
        "PUMP_SILVER_RECORDED_BUY_EXACT_QUOTE_V2_2",
        "OF1_BRONZE_TRANSACTION_1",
        "UNKNOWN",
    ] {
        assert!(columns::parse_record(Layer::Silver, &line(&record(schema))).is_err());
    }
    for invalid_schema in [Value::Null, json!([BUY]), json!(true)] {
        let mut value = record(BUY);
        value["schema"] = invalid_schema;
        assert!(columns::parse_record(Layer::Silver, &line(&value)).is_err());
    }
}

#[test]
fn mixed_schemas_keep_distinct_requested_and_reported_units_exact() {
    let mut sell = record(SELL);
    sell["instruction"] = json!({"amount_raw_u64":"80","min_sol_output_raw_u64":"9"});
    sell["event_reported"] =
        json!({"is_buy":false,"token_amount_raw_u64":"80","sol_amount_raw_u64":"11"});
    let mut buy = record(BUY);
    buy["instruction"] = json!({"spendable_quote_in_raw_u64":"9007199254740993","min_tokens_out_raw_u64":"18446744073709551615"});
    buy["event_reported"] = json!({"is_buy":true,"token_amount_raw_u64":"18446744073709551615","quote_amount_raw_u64":"9007199254740000","sol_amount_raw_u64":"0"});
    let lines = [line(&sell), line(&buy), line(&buy)];
    let batch = columns::batch(Layer::Silver, &lines, 0).unwrap();
    assert_eq!(text(&batch, "record_schema").value(0), SELL);
    assert_eq!(text(&batch, "record_schema").value(1), BUY);
    assert_eq!(unsigned(&batch, "amount_raw_u64").value(0), 80);
    assert!(unsigned(&batch, "amount_raw_u64").is_null(1));
    assert!(unsigned(&batch, "min_sol_output_raw_u64").is_null(1));
    assert!(unsigned(&batch, "spendable_quote_in_raw_u64").is_null(0));
    assert!(unsigned(&batch, "min_tokens_out_raw_u64").is_null(0));
    assert_eq!(
        unsigned(&batch, "spendable_quote_in_raw_u64").value(1),
        9_007_199_254_740_993
    );
    assert_eq!(
        unsigned(&batch, "min_tokens_out_raw_u64").value(1),
        u64::MAX
    );
    assert_eq!(
        unsigned(&batch, "quote_amount_raw_u64").value(1),
        9_007_199_254_740_000
    );
    assert_eq!(unsigned(&batch, "sol_amount_raw_u64").value(1), 0);
    assert_eq!(
        storage::verify_batch(Layer::Silver, &batch, 0).unwrap(),
        lines
    );
}

#[test]
fn real_parquet_roundtrip_keeps_buy_boundaries_nulls_bytes_and_duplicates() {
    let mut lines = Vec::new();
    for amount in [0, 9_007_199_254_740_993, 1 << 63, u64::MAX] {
        let mut buy = record(BUY);
        buy["instruction"] = json!({"spendable_quote_in_raw_u64":amount.to_string(),"min_tokens_out_raw_u64":(u64::MAX-amount).to_string()});
        buy["event_reported"] = json!({"is_buy":true,"quote_amount_raw_u64":amount.to_string()});
        lines.push(line(&buy));
    }
    lines.push(lines[3].clone());
    let bytes = lines.concat();
    let temp = tempfile::tempdir().unwrap();
    let paths = [
        temp.path().join("one.parquet"),
        temp.path().join("two.parquet"),
    ];
    for path in &paths {
        storage::write_layer(Layer::Silver, &bytes, path).unwrap();
        assert_eq!(
            storage::verify_layer(Layer::Silver, path, &hash(&bytes)).unwrap()["rows"],
            5
        );
    }
    assert_eq!(
        std::fs::read(&paths[0]).unwrap(),
        std::fs::read(&paths[1]).unwrap()
    );
    let mut reader = ParquetRecordBatchReaderBuilder::try_new(File::open(&paths[0]).unwrap())
        .unwrap()
        .with_batch_size(10)
        .build()
        .unwrap();
    let batch = reader.next().unwrap().unwrap();
    assert!(reader.next().is_none());
    assert_eq!(
        storage::verify_batch(Layer::Silver, &batch, 0).unwrap(),
        lines
    );
    for (name, state) in [
        ("base_decimals", "NULL"),
        ("quote_decimals", "NULL"),
        ("amount_raw_u64", "MISSING"),
        ("min_sol_output_raw_u64", "MISSING"),
        ("cpi_signer", "NULL"),
        ("cpi_writable", "MISSING"),
        ("committed_account_state", "VALUE"),
    ] {
        assert_eq!(text(&batch, &format!("{name}_state")).value(0), state);
        assert_eq!(
            batch.column_by_name(name).unwrap().is_null(0),
            state != "VALUE"
        );
    }
    assert_eq!(
        text(&batch, "committed_account_state").value(0),
        "UNAVAILABLE"
    );
}

#[test]
fn buy_quantities_reject_float_overflow_negative_and_wrong_types() {
    for field in ["spendable_quote_in_raw_u64", "min_tokens_out_raw_u64"] {
        for value in [
            json!(1.0),
            json!(-1),
            json!("18446744073709551616"),
            json!("01"),
            json!(true),
        ] {
            let mut buy = record(BUY);
            buy["instruction"] = json!({field:value});
            assert!(
                columns::batch(Layer::Silver, &[line(&buy)], 0).is_err(),
                "{field}"
            );
        }
    }
}

#[test]
fn buy_typed_amount_or_state_change_fails_parity() {
    let mut buy = record(BUY);
    buy["instruction"] =
        json!({"spendable_quote_in_raw_u64":"18446744073709551615","min_tokens_out_raw_u64":"17"});
    let batch = columns::batch(Layer::Silver, &[line(&buy)], 0).unwrap();
    for (name, replacement) in [
        (
            "spendable_quote_in_raw_u64",
            Arc::new(UInt64Array::from(vec![0])) as ArrayRef,
        ),
        (
            "min_tokens_out_raw_u64",
            Arc::new(UInt64Array::from(vec![18])),
        ),
        (
            "amount_raw_u64_state",
            Arc::new(StringArray::from(vec!["NULL"])),
        ),
        ("record_schema", Arc::new(StringArray::from(vec![SELL]))),
    ] {
        let mut columns = batch.columns().to_vec();
        columns[batch.schema().index_of(name).unwrap()] = replacement;
        let changed = RecordBatch::try_new(batch.schema(), columns).unwrap();
        assert!(storage::verify_batch(Layer::Silver, &changed, 0).is_err());
    }
}
