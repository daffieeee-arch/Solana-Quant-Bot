use arrow_array::{Array, ArrayRef, BooleanArray, RecordBatch, StringArray, UInt64Array};
use of1_parquet_projection::{
    columns::{self, Layer},
    hash, storage,
};
use serde_json::{Value, json};
use std::{fs, path::Path, sync::Arc};

fn record(layer: Layer) -> Value {
    json!({"schema":layer.record_schema(),"slice_class":"ENGINEERING_VALIDATION_ONLY","effective_at":{"slot":"422496004","transaction_index_in_slot":153},"decoder_source_sha256":"a".repeat(64),"source":{"raw_sha256":"b".repeat(64),"provenance":"SYNTHETIC_TEST_ONLY"},"disposition":"DECODED","transaction":{"status":"OK","fee_lamports":"5000","name":null,"economic_identity":"UNAVAILABLE","wire_hex":"00ff","protobuf_metadata_hex":"0102"}})
}
fn line(v: &Value) -> Vec<u8> {
    let mut b = serde_json::to_vec(v).unwrap();
    b.push(b'\n');
    b
}
fn seal(root: &Path, bronze: &[u8], silver: &[u8]) -> String {
    fs::create_dir(root).unwrap();
    let quality = b"{}";
    let html = b"fixture";
    for (name, bytes) in [
        ("bronze.jsonl", bronze),
        ("silver.jsonl", silver),
        ("quality.json", quality),
        ("quality.html", html),
    ] {
        fs::write(root.join(name), bytes).unwrap();
    }
    fs::write(root.join("COMPLETE"), hash(quality)).unwrap();
    let execution=serde_json::to_vec(&json!({"schema":"OF1_BRONZE_EXECUTION_1","bronze_jsonl_sha256":hash(bronze),"silver_jsonl_sha256":hash(silver),"quality_sha256":hash(quality),"html_sha256":hash(html)})).unwrap();
    fs::write(root.join("execution.json"), &execution).unwrap();
    hash(&execution)
}
#[test]
fn physical_roundtrip_duplicate_rows_and_order_are_deterministic() {
    let d = tempfile::tempdir().unwrap();
    let mut bytes = line(&record(Layer::Bronze));
    bytes.extend(bytes.clone());
    let sha = seal(&d.path().join("input"), &bytes, b"");
    let a = storage::materialize(&d.path().join("input"), &sha, &d.path().join("one")).unwrap();
    let b = storage::materialize(&d.path().join("input"), &sha, &d.path().join("two")).unwrap();
    assert_eq!(a, b);
    assert_eq!(a["files"]["bronze.parquet"]["audit"]["rows"], 2);
    for name in [
        "bronze.parquet",
        "silver.parquet",
        "manifest.json",
        "COMPLETE",
    ] {
        assert_eq!(
            fs::read(d.path().join("one").join(name)).unwrap(),
            fs::read(d.path().join("two").join(name)).unwrap()
        );
    }
    assert!(storage::materialize(&d.path().join("input"), &sha, &d.path().join("one")).is_err());
}
#[test]
fn unsigned_and_signed_boundaries_survive_real_parquet() {
    let d = tempfile::tempdir().unwrap();
    let path = d.path().join("bounds.parquet");
    let mut bytes = Vec::new();
    for (n, s) in [
        (0, i64::MIN),
        (9_007_199_254_740_993, -1),
        (1 << 63, 0),
        (u64::MAX, i64::MAX),
    ] {
        let mut r = record(Layer::Silver);
        r["instruction"] = json!({"amount_raw_u64":n.to_string()});
        r["event_reported"] = json!({"timestamp_raw_i64":s.to_string()});
        bytes.extend(line(&r));
    }
    storage::write_layer(Layer::Silver, &bytes, &path).unwrap();
    assert_eq!(
        storage::verify_layer(Layer::Silver, &path, &hash(&bytes)).unwrap()["rows"],
        4
    );
}
#[test]
fn missing_null_and_unavailable_never_become_zero() {
    let b = columns::batch(Layer::Bronze, &[line(&record(Layer::Bronze))], 0).unwrap();
    for (name, state) in [
        ("name", "NULL"),
        ("ticker", "MISSING"),
        ("economic_identity", "VALUE"),
    ] {
        let s = b
            .column_by_name(&format!("{name}_state"))
            .unwrap()
            .as_any()
            .downcast_ref::<StringArray>()
            .unwrap();
        assert_eq!(s.value(0), state);
        assert_eq!(b.column_by_name(name).unwrap().is_null(0), state != "VALUE");
    }
}
#[test]
fn float_overflow_noncanonical_and_wrong_types_rejected() {
    for v in [
        json!(1.5),
        json!(1.0),
        json!(-1),
        json!("18446744073709551616"),
        json!("01"),
        json!(false),
    ] {
        let mut r = record(Layer::Bronze);
        r["transaction"]["fee_lamports"] = v;
        assert!(columns::batch(Layer::Bronze, &[line(&r)], 0).is_err());
    }
    for v in [
        json!("9223372036854775808"),
        json!("-9223372036854775809"),
        json!(1.0),
    ] {
        let mut r = record(Layer::Silver);
        r["event_reported"] = json!({"timestamp_raw_i64":v});
        assert!(columns::batch(Layer::Silver, &[line(&r)], 0).is_err());
    }
}
#[test]
fn failed_unsupported_missing_and_quarantined_not_filtered() {
    let mut lines = Vec::new();
    for disposition in ["DECODED", "MISSING", "UNSUPPORTED", "QUARANTINED"] {
        let mut r = record(Layer::Bronze);
        r["disposition"] = json!(disposition);
        r["transaction"] = Value::Null;
        lines.push(line(&r));
    }
    let b = columns::batch(Layer::Bronze, &lines, 0).unwrap();
    assert_eq!(b.num_rows(), 4);
    assert_eq!(b.column_by_name("fee_lamports").unwrap().null_count(), 4);
}
#[test]
fn changed_projection_null_state_bytes_and_order_fail_independent_audit() {
    let b = columns::batch(Layer::Bronze, &[line(&record(Layer::Bronze))], 0).unwrap();
    for (name, replacement) in [
        (
            "fee_lamports",
            Arc::new(UInt64Array::from(vec![1])) as ArrayRef,
        ),
        ("name_state", Arc::new(StringArray::from(vec!["MISSING"]))),
        (
            "pump_program_involvement",
            Arc::new(BooleanArray::from(vec![false])),
        ),
        ("record_ordinal", Arc::new(UInt64Array::from(vec![1]))),
    ] {
        let mut cols = b.columns().to_vec();
        cols[b.schema().index_of(name).unwrap()] = replacement;
        let changed = RecordBatch::try_new(b.schema(), cols).unwrap();
        assert!(storage::verify_batch(Layer::Bronze, &changed, 0).is_err());
    }
}
#[test]
fn source_change_or_bad_seal_rejected_before_publication() {
    let d = tempfile::tempdir().unwrap();
    let input = d.path().join("input");
    let sha = seal(&input, &line(&record(Layer::Bronze)), b"");
    assert!(storage::materialize(&input, &"0".repeat(64), &d.path().join("out")).is_err());
    assert!(!d.path().join("out").exists());
    fs::write(input.join("bronze.jsonl"), b"changed").unwrap();
    assert!(storage::materialize(&input, &sha, &d.path().join("out")).is_err());
}
#[test]
fn silver_parent_and_provenance_binding_exact() {
    let d = tempfile::tempdir().unwrap();
    let bronze = record(Layer::Bronze);
    let b = line(&bronze);
    let mut s = record(Layer::Silver);
    s["bronze_record_sha256"] = json!(hash(&b[..b.len() - 1]));
    let sha = seal(&d.path().join("input"), &b, &line(&s));
    storage::materialize(&d.path().join("input"), &sha, &d.path().join("valid")).unwrap();
    s["source"]["raw_sha256"] = json!("c".repeat(64));
    let sha = seal(&d.path().join("bad-input"), &b, &line(&s));
    assert!(
        storage::materialize(&d.path().join("bad-input"), &sha, &d.path().join("bad")).is_err()
    );
}
#[test]
fn corruption_and_different_original_hash_fail() {
    let d = tempfile::tempdir().unwrap();
    let p = d.path().join("p");
    let bytes = line(&record(Layer::Bronze));
    storage::write_layer(Layer::Bronze, &bytes, &p).unwrap();
    assert!(storage::verify_layer(Layer::Bronze, &p, &"0".repeat(64)).is_err());
    let mut file = fs::read(&p).unwrap();
    file.truncate(file.len() - 9);
    fs::write(&p, file).unwrap();
    assert!(storage::verify_layer(Layer::Bronze, &p, &hash(&bytes)).is_err());
}
#[test]
fn rejects_duplicate_json_keys_missing_lf_invalid_hex_and_unknown_schema() {
    for bytes in [b"{\"a\":1,\"a\":2}\n".as_slice(), b"{}", b"invalid\n"] {
        assert!(columns::parse_record(Layer::Bronze, bytes).is_err());
    }
    let mut r = record(Layer::Bronze);
    r["transaction"]["wire_hex"] = json!("xx");
    assert!(columns::batch(Layer::Bronze, &[line(&r)], 0).is_err());
    r["schema"] = json!("FUTURE_UNREVIEWED");
    assert!(columns::parse_record(Layer::Bronze, &line(&r)).is_err());
}
#[test]
fn partial_output_is_not_completed() {
    let d = tempfile::tempdir().unwrap();
    let mut r = record(Layer::Bronze);
    r["transaction"]["fee_lamports"] = json!(1.5);
    let sha = seal(&d.path().join("input"), &line(&r), b"");
    assert!(storage::materialize(&d.path().join("input"), &sha, &d.path().join("out")).is_err());
    assert!(!d.path().join("out/COMPLETE").exists());
}
#[test]
fn duckdb_boundary_fixture_export_when_explicitly_requested() {
    let temporary = tempfile::tempdir().unwrap();
    let path = std::env::var("COLUMNAR_TEST_FIXTURE_DIR").map_or_else(
        |_| temporary.path().join("fixture"),
        std::path::PathBuf::from,
    );
    let root = path.as_path();
    fs::create_dir(root).unwrap();
    let mut bytes = Vec::new();
    for (n, s) in [
        (0, i64::MIN),
        (9_007_199_254_740_993, -1),
        (1 << 63, 0),
        (u64::MAX, i64::MAX),
    ] {
        let mut r = record(Layer::Silver);
        r["instruction"] = json!({"amount_raw_u64":n.to_string()});
        r["event_reported"] = json!({"timestamp_raw_i64":s.to_string()});
        r["base_decimals"] = Value::Null;
        bytes.extend(line(&r));
    }
    storage::write_layer(Layer::Silver, &bytes, &root.join("boundary.parquet")).unwrap();
    let b = record(Layer::Bronze);
    let mut bronze = line(&b);
    let mut s = record(Layer::Silver);
    s["bronze_record_sha256"] = json!(hash(&bronze[..bronze.len() - 1]));
    bronze.extend(bronze.clone());
    storage::write_layer(
        Layer::Bronze,
        &bronze,
        &root.join("duplicate-bronze.parquet"),
    )
    .unwrap();
    storage::write_layer(Layer::Silver, &line(&s), &root.join("one-silver.parquet")).unwrap();
}

#[test]
fn fingerprint_covers_all_physical_columns_and_nullability() {
    for layer in [Layer::Bronze, Layer::Silver] {
        let descriptor = columns::schema_descriptor(layer);
        let schema = columns::schema(layer);
        let fields = descriptor["fields"].as_array().unwrap();
        assert_eq!(fields.len(), schema.fields().len());
        for (field, expected) in fields.iter().zip(schema.fields()) {
            assert_eq!(field["name"], expected.name().as_str());
            assert_eq!(field["nullable"], expected.is_nullable());
            assert_eq!(field["arrow_type"], format!("{:?}", expected.data_type()));
        }
        for field in fields.iter().take(3) {
            assert_eq!(field["nullable"], false);
        }
        let mut changed = descriptor.clone();
        changed["fields"][0]["nullable"] = json!(true);
        assert_ne!(
            hash(&serde_json::to_vec(&descriptor).unwrap()),
            hash(&serde_json::to_vec(&changed).unwrap())
        );
    }
}
