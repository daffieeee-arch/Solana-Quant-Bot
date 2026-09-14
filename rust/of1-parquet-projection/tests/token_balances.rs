//! Synthetic physical-column fixtures; no protocol or authentic-data claim.
use arrow_array::{
    Array, BinaryArray, ListArray, RecordBatch, StringArray, StructArray, UInt64Array,
};
use of1_parquet_projection::{
    columns::{self, Layer},
    hash, storage,
};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use serde_json::{Value, json};
use std::fs::File;

fn record(layer: Layer) -> Value {
    json!({"schema":layer.record_schemas()[0],"slice_class":"ENGINEERING_VALIDATION_ONLY",
    "input_kind":"FIXTURE_SYNTHETIC_TOKEN_BALANCE_PROJECTION","receipt_evidence":"Fixture",
    "effective_at":{"slot":"1","transaction_index_in_slot":0},
    "decoder_source_sha256":"a".repeat(64),"source":{"raw_sha256":"b".repeat(64)},
    "base_decimals":null,"quote_decimals":null})
}
fn observation() -> Value {
    json!({"side":"PRE","ordinal":0,"account_index":0,"account_key":"fixture-account",
    "mint":"fixture-mint","owner":"fixture-owner","program_id":"fixture-program",
    "amount_string":"18446744073709551615","amount_u64":"18446744073709551615",
    "decimals":0,"exact_decimal_amount":"18446744073709551615","disposition":"PROJECTED",
    "reason":null,"presence":{"account_index":false,"mint":true,"owner":true,"program_id":true,
    "ui_token_amount":true,"amount":true,"decimals":false,"ui_amount":false,"ui_amount_string":false},
    "field_states":{"account_index":"PROTO3_DEFAULT","decimals":"PROTO3_DEFAULT"},
    "raw_token_balance_hex":"0800120100","raw_token_balance_sha256":"c".repeat(64)})
}
fn bronze(observations: Value) -> Value {
    let mut value = record(Layer::Bronze);
    value["transaction"] = json!({"status":"OK","token_balance_context":{
        "collection_status":"OBSERVED"}});
    value["transaction"]["token_balance_context"]["observations"] = observations;
    value
}
fn silver() -> Value {
    let mut value = record(Layer::Silver);
    value["token_balance_context"] = json!({"binding_status":"BOUND_RECORDED_BASE_UNITS",
        "base_mint":"fixture-mint","base_token_program":"fixture-program",
        "decimals":6,"decimals_evidence":"RECORDED_METADATA","event_token_amount_u64":"9007199254740993",
        "event_token_amount_decimal":"9007199254.740993","matched_observation_indexes":[3,0,3],
        "roles":[{"role":"user","account_index":0,"account_key":"fixture-account","expected_owner":"fixture-owner",
        "pre_observation_indexes":[0,0],"post_observation_indexes":[3],
        "transaction_delta_raw_signed":"-18446744073709551615","delta_status":"TRANSACTION_SCOPE_ONLY"}]});
    value
}
fn line(value: &Value) -> Vec<u8> {
    let mut result = serde_json::to_vec(value).unwrap();
    result.push(b'\n');
    result
}
fn list<'a>(batch: &'a RecordBatch, name: &str) -> &'a ListArray {
    batch
        .column_by_name(name)
        .unwrap()
        .as_any()
        .downcast_ref()
        .unwrap()
}
fn uint<'a>(fields: &'a StructArray, name: &str) -> &'a UInt64Array {
    fields
        .column_by_name(name)
        .unwrap()
        .as_any()
        .downcast_ref()
        .unwrap()
}
fn text<'a>(fields: &'a StructArray, name: &str) -> &'a StringArray {
    fields
        .column_by_name(name)
        .unwrap()
        .as_any()
        .downcast_ref()
        .unwrap()
}

#[test]
fn nested_observations_preserve_order_duplicates_boundaries_default_presence_and_bytes() {
    let mut missing = observation();
    missing["side"] = json!("POST");
    missing["ordinal"] = json!(1);
    missing["decimals"] = Value::Null;
    missing["amount_u64"] = Value::Null;
    missing["exact_decimal_amount"] = Value::Null;
    missing["disposition"] = json!("MISSING");
    missing.as_object_mut().unwrap().remove("owner");
    let values = [line(&bronze(json!([
        observation(),
        missing,
        observation()
    ])))];
    let batch = columns::batch(Layer::Bronze, &values, 0).unwrap();
    let values_array = list(&batch, "token_balance_observations").value(0);
    let fields = values_array.as_any().downcast_ref::<StructArray>().unwrap();
    assert_eq!(fields.len(), 3);
    assert_eq!(uint(fields, "amount_u64").value(0), u64::MAX);
    assert!(uint(fields, "amount_u64").is_null(1));
    assert_eq!(uint(fields, "amount_u64").value(2), u64::MAX);
    assert_eq!(uint(fields, "account_index").value(0), 0);
    assert_eq!(uint(fields, "decimals").value(0), 0);
    assert_eq!(
        text(fields, "decimals_field_state").value(0),
        "PROTO3_DEFAULT"
    );
    assert_eq!(text(fields, "owner_state").value(1), "MISSING");
    assert_eq!(text(fields, "decimals_state").value(1), "NULL");
    assert_eq!(text(fields, "side").value(1), "POST");
    assert_eq!(
        fields
            .column_by_name("raw_token_balance_bytes")
            .unwrap()
            .as_any()
            .downcast_ref::<BinaryArray>()
            .unwrap()
            .value(0),
        [8, 0, 18, 1, 0]
    );
    assert_eq!(
        storage::verify_batch(Layer::Bronze, &batch, 0).unwrap(),
        values
    );
}

#[test]
fn missing_null_empty_collections_remain_distinct_not_zero_observations_claim() {
    let lines = [
        line(&record(Layer::Bronze)),
        line(&bronze(Value::Null)),
        line(&bronze(json!([]))),
    ];
    let batch = columns::batch(Layer::Bronze, &lines, 0).unwrap();
    let lists = list(&batch, "token_balance_observations");
    assert!(lists.is_null(0));
    assert!(lists.is_null(1));
    assert!(!lists.is_null(2));
    assert_eq!(lists.value(2).len(), 0);
    let states = batch
        .column_by_name("token_balance_observations_state")
        .unwrap()
        .as_any()
        .downcast_ref::<StringArray>()
        .unwrap();
    assert_eq!(
        (states.value(0), states.value(1), states.value(2)),
        ("MISSING", "NULL", "VALUE")
    );
    assert_eq!(
        storage::verify_batch(Layer::Bronze, &batch, 0).unwrap(),
        lines
    );
}

#[test]
fn silver_context_is_separate_from_trade_fields_and_keeps_exact_signed_delta_refs() {
    let value = silver();
    let lines = [line(&value)];
    let batch = columns::batch(Layer::Silver, &lines, 0).unwrap();
    assert!(batch.column_by_name("base_decimals").unwrap().is_null(0));
    assert!(batch.column_by_name("quote_decimals").unwrap().is_null(0));
    assert_eq!(
        batch
            .column_by_name("units_decimals")
            .unwrap()
            .as_any()
            .downcast_ref::<UInt64Array>()
            .unwrap()
            .value(0),
        6
    );
    let matched = list(&batch, "units_matched_observation_indexes").value(0);
    assert_eq!(
        matched
            .as_any()
            .downcast_ref::<UInt64Array>()
            .unwrap()
            .values()
            .as_ref(),
        [3, 0, 3]
    );
    let roles = list(&batch, "units_role_balances").value(0);
    let fields = roles.as_any().downcast_ref::<StructArray>().unwrap();
    assert_eq!(
        text(fields, "transaction_delta_raw_signed").value(0),
        "-18446744073709551615"
    );
    assert_eq!(
        text(fields, "delta_status").value(0),
        "TRANSACTION_SCOPE_ONLY"
    );
    let pre = fields
        .column_by_name("pre_observation_indexes")
        .unwrap()
        .as_any()
        .downcast_ref::<ListArray>()
        .unwrap()
        .value(0);
    assert_eq!(
        pre.as_any()
            .downcast_ref::<UInt64Array>()
            .unwrap()
            .values()
            .as_ref(),
        [0, 0]
    );
    assert_eq!(
        storage::verify_batch(Layer::Silver, &batch, 0).unwrap(),
        lines
    );
}

#[test]
fn nested_bad_types_overflow_and_null_observations_fail_without_numeric_repair() {
    for field in ["amount_u64", "account_index", "decimals", "ordinal"] {
        for invalid in [
            json!(-1),
            json!(1.0),
            json!("18446744073709551616"),
            json!("01"),
            json!(false),
        ] {
            let mut value = observation();
            value[field] = invalid;
            assert!(
                columns::batch(Layer::Bronze, &[line(&bronze(json!([value])))], 0).is_err(),
                "{field}"
            );
        }
    }
    for value in [json!([null]), json!([1]), json!(true), json!({})] {
        assert!(columns::batch(Layer::Bronze, &[line(&bronze(value))], 0).is_err());
    }
    let mut value = observation();
    value["presence"]["decimals"] = json!(1);
    assert!(columns::batch(Layer::Bronze, &[line(&bronze(json!([value])))], 0).is_err());
    let mut value = silver();
    value["token_balance_context"]["matched_observation_indexes"] = json!([null]);
    assert!(columns::batch(Layer::Silver, &[line(&value)], 0).is_err());
    let mut value = silver();
    value["token_balance_context"]["roles"][0]["pre_observation_indexes"] = json!([-1]);
    assert!(columns::batch(Layer::Silver, &[line(&value)], 0).is_err());
}

#[test]
fn altered_nested_typed_field_fails_full_record_parity() {
    for (layer, initial, name) in [
        (
            Layer::Bronze,
            bronze(json!([observation()])),
            "token_balance_observations",
        ),
        (Layer::Silver, silver(), "units_role_balances"),
    ] {
        let batch = columns::batch(layer, &[line(&initial)], 0).unwrap();
        let mut changed = initial.clone();
        if layer == Layer::Bronze {
            changed["transaction"]["token_balance_context"]["observations"][0]["decimals"] =
                json!(8);
        } else {
            changed["token_balance_context"]["roles"][0]["transaction_delta_raw_signed"] =
                json!("0");
        }
        let wrong = columns::batch(layer, &[line(&changed)], 0).unwrap();
        let mut arrays = batch.columns().to_vec();
        let index = batch.schema().index_of(name).unwrap();
        arrays[index] = wrong.column(index).clone();
        let modified = RecordBatch::try_new(batch.schema(), arrays).unwrap();
        assert!(storage::verify_batch(layer, &modified, 0).is_err());
    }
}

#[test]
fn nested_parquet_roundtrip_is_deterministic_and_exported_for_duckdb() {
    let temp = tempfile::tempdir().unwrap();
    for (layer, record) in [
        (Layer::Bronze, bronze(json!([observation(), observation()]))),
        (Layer::Silver, silver()),
    ] {
        let bytes = [line(&record), line(&record)].concat();
        let paths = [
            temp.path().join(format!("{}-a.parquet", layer.name())),
            temp.path().join(format!("{}-b.parquet", layer.name())),
        ];
        for path in &paths {
            storage::write_layer(layer, &bytes, path).unwrap();
            assert_eq!(
                storage::verify_layer(layer, path, &hash(&bytes)).unwrap()["rows"],
                2
            );
            let reader = ParquetRecordBatchReaderBuilder::try_new(File::open(path).unwrap())
                .unwrap()
                .build()
                .unwrap();
            let actual: Vec<_> = reader
                .flat_map(|batch| storage::verify_batch(layer, &batch.unwrap(), 0).unwrap())
                .flatten()
                .collect();
            assert_eq!(actual, bytes);
        }
        assert_eq!(
            std::fs::read(&paths[0]).unwrap(),
            std::fs::read(&paths[1]).unwrap()
        );
        if let Some(dir) = std::env::var_os("COLUMNAR_TEST_FIXTURE_DIR") {
            let dir = std::path::PathBuf::from(dir);
            std::fs::create_dir_all(&dir).unwrap();
            storage::write_layer(
                layer,
                &bytes,
                &dir.join(format!("token-balances-{}.parquet", layer.name())),
            )
            .unwrap();
        }
    }
    assert!(
        columns::schema_descriptor(Layer::Bronze)["token_balance_projection"]["fields"]
            .as_array()
            .unwrap()
            .iter()
            .any(|f| f["name"] == "decimals_field_state")
    );
    assert!(
        columns::schema_descriptor(Layer::Silver)["token_balance_projection"]["fields"]
            .as_array()
            .unwrap()
            .iter()
            .any(|f| f["name"] == "pre_observation_indexes")
    );
}
