//! Synthetic physical-boundary fixtures, not authentic source/domain evidence.
use of1_parquet_projection::{
    admission,
    columns::Layer,
    hash,
    shards::{self, ShardLimits},
    storage,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{BufReader, Read, Write},
    path::Path,
};

fn row(layer: Layer, index: u64) -> Value {
    json!({"schema":layer.record_schemas()[0],"slice_class":"ENGINEERING_VALIDATION_ONLY","input_kind":"FIXTURE_SYNTHETIC_SHARD_TEST","receipt_evidence":"Fixture","effective_at":{"slot":(100+index/1667).to_string(),"transaction_index_in_slot":index%1667},"decoder_source_sha256":"a".repeat(64),"source":{"raw_sha256":"b".repeat(64),"provenance":"SYNTHETIC_PHYSICAL_BOUNDARY_TEST_ONLY"},"disposition":"DECODED","transaction_status":"OK","transaction":{"status":"OK","fee_lamports":"9007199254740993","name":null,"economic_identity":"UNAVAILABLE","wire_hex":"00ff","protobuf_metadata_hex":"0102"}})
}
fn bytes(record: &Value) -> Vec<u8> {
    let mut bytes = serde_json::to_vec(record).unwrap();
    bytes.push(b'\n');
    bytes
}
fn finish(input: &Path, quality: &Value) -> String {
    fs::write(
        input.join("quality.json"),
        serde_json::to_vec(quality).unwrap(),
    )
    .unwrap();
    fs::write(
        input.join("quality.html"),
        b"Explicit synthetic writer fixture; no CAR or domain observation",
    )
    .unwrap();
    let mut execution = json!({"schema":"OF1_BRONZE_EXECUTION_1"});
    for (file, key) in [
        ("bronze.jsonl", "bronze_jsonl_sha256"),
        ("silver.jsonl", "silver_jsonl_sha256"),
        ("quality.json", "quality_sha256"),
        ("quality.html", "html_sha256"),
    ] {
        execution[key] = json!(
            shards::file_hash(&input.join(file), storage::MAX_FILE_BYTES)
                .unwrap()
                .0
        );
    }
    fs::write(
        input.join("COMPLETE"),
        execution["quality_sha256"].as_str().unwrap(),
    )
    .unwrap();
    let b = serde_json::to_vec(&execution).unwrap();
    fs::write(input.join("execution.json"), &b).unwrap();
    hash(&b)
}
fn synthetic_input(input: &Path, rows: u64, silver_rows: u64) -> String {
    fs::create_dir(input).unwrap();
    let mut bronze = File::create(input.join("bronze.jsonl")).unwrap();
    let mut counts = BTreeMap::<u64, BTreeMap<String, u64>>::new();
    for i in 0..rows {
        let mut record = row(Layer::Bronze, i);
        let outcome = ["DECODED", "MISSING", "UNSUPPORTED", "QUARANTINED"][(i % 4) as usize];
        record["disposition"] = json!(outcome);
        if outcome != "DECODED" {
            record["transaction"] = Value::Null;
        }
        *counts
            .entry(100 + i / 1667)
            .or_default()
            .entry(outcome.into())
            .or_default() += 1;
        bronze.write_all(&bytes(&record)).unwrap();
    }
    let parent = row(Layer::Bronze, 0);
    let parent_line = bytes(&parent);
    let mut silver = File::create(input.join("silver.jsonl")).unwrap();
    let mut fact = row(Layer::Silver, 0);
    fact["bronze_record_sha256"] = json!(hash(&parent_line[..parent_line.len() - 1]));
    fact["instruction"] = json!({"amount_raw_u64":u64::MAX.to_string()});
    fact["event_reported"] = json!({"timestamp_raw_i64":i64::MIN.to_string()});
    for _ in 0..silver_rows {
        silver.write_all(&bytes(&fact)).unwrap();
    }
    let selected = counts.keys().copied().collect::<Vec<_>>();
    let slots=counts.iter().map(|(s,c)|json!({"slot":s.to_string(),"transaction_envelopes":c.values().sum::<u64>(),"stages":{"car_slot":"VERIFIED"},"dispositions":c})).collect::<Vec<_>>();
    finish(
        input,
        &json!({"schema":"OF1_BRONZE_TRANSACTION_1","input_kind":"FIXTURE_SYNTHETIC_SHARD_TEST","receipt_evidence":"Fixture","slice_class":"ENGINEERING_VALIDATION_ONLY","selected_slots":selected,"slots":slots,"research_ready":false}),
    )
}
fn identical(a: &Path, b: &Path, manifest: &Value) {
    for name in manifest["files"]
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .chain(["manifest.json", "COMPLETE"])
    {
        assert_eq!(
            fs::read(a.join(name)).unwrap(),
            fs::read(b.join(name)).unwrap(),
            "{name}"
        );
    }
}
#[test]
fn real_row_shards_duplicate_silver_and_deterministic_export() {
    let temp = tempfile::tempdir().unwrap();
    let root = std::env::var_os("COLUMNAR_TEST_FIXTURE_DIR")
        .map_or_else(|| temp.path().join("export"), std::path::PathBuf::from);
    // Another test may create the shared export root; these names are unique.
    fs::create_dir_all(&root).unwrap();
    let input = root.join("multi-shard-input");
    let seal = synthetic_input(&input, 5001, 5001);
    let first = root.join("multi-shard");
    let second = root.join("multi-shard-repeat");
    let a = shards::materialize(&input, &seal, &first, ShardLimits::default()).unwrap();
    let b = shards::materialize(&input, &seal, &second, ShardLimits::default()).unwrap();
    assert_eq!(a, b);
    identical(&first, &second, &a);
    for layer in ["bronze", "silver"] {
        assert_eq!(a["layers"][layer]["rows"], 5001);
        assert_eq!(a["layers"][layer]["files"].as_array().unwrap().len(), 2);
    }
    assert_eq!(a["selection"]["all_expected_packages_accounted"], true);
    assert_eq!(a["selection"]["decoded_packages"], 1251);
    assert_eq!(a["evidence"]["receipt_evidence"], "Fixture");
    assert_eq!(a["evidence"]["research_ready"], false);
    assert!(shards::materialize(&input, &seal, &first, ShardLimits::default()).is_err());
}
#[test]
fn one_file_and_many_have_exact_same_logical_stream() {
    let d = tempfile::tempdir().unwrap();
    let input = d.path().join("input");
    let sha = synthetic_input(&input, 99, 8);
    let one =
        shards::materialize(&input, &sha, &d.path().join("one"), ShardLimits::default()).unwrap();
    let many = shards::materialize(
        &input,
        &sha,
        &d.path().join("many"),
        ShardLimits {
            rows: 17,
            ..ShardLimits::default()
        },
    )
    .unwrap();
    for layer in ["bronze", "silver"] {
        for key in [
            "rows",
            "reconstructed_jsonl_sha256",
            "ordered_logical_sha256",
            "coverage",
        ] {
            assert_eq!(one["layers"][layer][key], many["layers"][layer][key]);
        }
    }
    assert_eq!(one["selection"], many["selection"]);
    assert_ne!(one["files"], many["files"]);
}
#[test]
fn actual_64mib_physical_boundary_never_splits_a_record() {
    let d = tempfile::tempdir().unwrap();
    let input = d.path().join("large.jsonl");
    let mut f = File::create(&input).unwrap();
    let mut record = row(Layer::Bronze, 0);
    record["transaction"]["wire_hex"] = json!("ab".repeat(700_000));
    let line = bytes(&record);
    for _ in 0..40 {
        f.write_all(&line).unwrap();
    }
    drop(f);
    let out = d.path().join("out");
    fs::create_dir(&out).unwrap();
    let (total, files) =
        shards::write_shards(Layer::Bronze, &input, &out, ShardLimits::default()).unwrap();
    assert_eq!(total["rows"], 40);
    assert!(files.len() > 1, "test must really cross physical64MiB cap");
    for info in files.values() {
        assert!(info["bytes"].as_u64().unwrap() <= storage::MAX_FILE_BYTES);
    }
    assert_eq!(
        total["reconstructed_jsonl_sha256"],
        shards::file_hash(&input, storage::MAX_FILE_BYTES)
            .unwrap()
            .0
    );
    assert!(fs::read_dir(&out).unwrap().all(|e| {
        !e.unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".partial")
    }));
}
#[test]
fn atomic_record_that_cannot_fit_fails_not_truncates() {
    let d = tempfile::tempdir().unwrap();
    let input = d.path().join("one.jsonl");
    fs::write(&input, bytes(&row(Layer::Bronze, 0))).unwrap();
    let out = d.path().join("out");
    fs::create_dir(&out).unwrap();
    assert!(
        shards::write_shards(
            Layer::Bronze,
            &input,
            &out,
            ShardLimits {
                rows: 5000,
                bytes: 64
            }
        )
        .unwrap_err()
        .to_string()
        .contains("RECORD_CANNOT_FIT_SHARD")
    );
    assert!(!out.join("COMPLETE").exists());
}
#[test]
fn record_limit_lf_and_caps_remain_hard() {
    let mut oversized =
        BufReader::new(std::io::repeat(b'x').take(storage::MAX_RECORD_BYTES as u64 + 1));
    assert!(
        shards::read_line(&mut oversized)
            .unwrap_err()
            .to_string()
            .contains("RECORD_LIMIT")
    );
    assert!(shards::read_line(&mut BufReader::new(b"{}".as_slice())).is_err());
    for limits in [
        ShardLimits {
            rows: 5001,
            bytes: 1,
        },
        ShardLimits {
            rows: 1,
            bytes: storage::MAX_FILE_BYTES + 1,
        },
        ShardLimits { rows: 0, bytes: 1 },
    ] {
        assert!(limits.validate().is_err());
    }
}
#[test]
fn missing_selection_and_duplicate_package_stay_incomplete() {
    let d = tempfile::tempdir().unwrap();
    let input = d.path().join("input");
    let _ = synthetic_input(&input, 2, 0);
    let mut q: Value =
        serde_json::from_slice(&fs::read(input.join("quality.json")).unwrap()).unwrap();
    q["selected_slots"] = json!([100, 101]);
    let seal = finish(&input, &q);
    let output = d.path().join("out");
    let manifest = shards::materialize(&input, &seal, &output, ShardLimits::default()).unwrap();
    assert_eq!(
        manifest["selection"]["all_expected_packages_accounted"],
        false
    );
    assert_eq!(
        manifest["selection"]["slots"][1]["expected_packages"],
        Value::Null
    );
    let mut f = File::options()
        .append(true)
        .open(input.join("bronze.jsonl"))
        .unwrap();
    f.write_all(&bytes(&row(Layer::Bronze, 0))).unwrap();
    drop(f);
    let seal = finish(&input, &q);
    let duplicate = shards::materialize(
        &input,
        &seal,
        &d.path().join("duplicate"),
        ShardLimits::default(),
    )
    .unwrap();
    assert_eq!(duplicate["layers"]["bronze"]["rows"], 3);
    assert_eq!(
        duplicate["selection"]["slots"][0]["duplicate_package_identity"],
        true
    );
}
#[test]
fn synthetic_cannot_relabel_sample_or_evidence() {
    let d = tempfile::tempdir().unwrap();
    let input = d.path().join("input");
    let _ = synthetic_input(&input, 1, 0);
    let mut q: Value =
        serde_json::from_slice(&fs::read(input.join("quality.json")).unwrap()).unwrap();
    q["slice_class"] = json!("RESEARCH_SAMPLING");
    let sha = finish(&input, &q);
    assert!(
        shards::materialize(&input, &sha, &d.path().join("bad"), ShardLimits::default()).is_err()
    );
    q["slice_class"] = json!("ENGINEERING_VALIDATION_ONLY");
    let mut v = row(Layer::Bronze, 0);
    v["receipt_evidence"] = json!("UNREVIEWED_AUTHENTIC_RAW");
    fs::write(input.join("bronze.jsonl"), bytes(&v)).unwrap();
    let sha = finish(&input, &q);
    assert!(
        admission::inspect(&input, &admission::seal(&input, &sha).unwrap())
            .unwrap_err()
            .to_string()
            .contains("RECORD_EVIDENCE_RECLASSIFICATION")
    );
    assert!(!d.path().join("bad/COMPLETE").exists());
}

#[test]
fn dataset_budget_is_shared_across_layers_and_prevents_complete() {
    let d = tempfile::tempdir().unwrap();
    let input = d.path().join("input");
    let seal = synthetic_input(&input, 3, 1);
    let full = shards::materialize(
        &input,
        &seal,
        &d.path().join("full"),
        ShardLimits::default(),
    )
    .unwrap();
    let bronze_bytes = full["files"]
        .as_object()
        .unwrap()
        .values()
        .filter(|f| f["layer"] == "bronze")
        .map(|f| f["bytes"].as_u64().unwrap())
        .sum::<u64>();
    let output = d.path().join("stopped-in-silver");
    let error = shards::materialize_with_write_limit(
        &input,
        &seal,
        &output,
        ShardLimits::default(),
        bronze_bytes,
    )
    .unwrap_err();
    assert!(error.to_string().contains("DATASET_CUMULATIVE_WRITE_LIMIT"));
    assert!(output.join("bronze-000000.parquet").exists());
    assert!(!output.join("manifest.json").exists());
    assert!(!output.join("COMPLETE").exists());
    let shard_bytes = full["files"]
        .as_object()
        .unwrap()
        .values()
        .map(|f| f["bytes"].as_u64().unwrap())
        .sum::<u64>();
    assert_eq!(
        full["publication"]["cumulative_shard_write_bytes"],
        shard_bytes
    );
    assert_eq!(
        full["writer"]["settings"]["max_dataset_written_bytes"],
        shards::MAX_DATASET_WRITE_BYTES
    );
    assert_eq!(
        full["writer"]["settings"]["max_bytes_per_file"],
        storage::MAX_FILE_BYTES
    );
    assert_eq!(
        full["writer"]["settings"]["max_rows_per_file"],
        storage::MAX_ROWS
    );
    let before_manifest = d.path().join("stopped-before-manifest");
    assert!(
        shards::materialize_with_write_limit(
            &input,
            &seal,
            &before_manifest,
            ShardLimits::default(),
            shard_bytes
        )
        .unwrap_err()
        .to_string()
        .contains("DATASET_CUMULATIVE_WRITE_LIMIT")
    );
    assert!(!before_manifest.join("manifest.json").exists());
    assert!(!before_manifest.join("COMPLETE").exists());
    for invalid in [0, shards::MAX_DATASET_WRITE_BYTES + 1] {
        let rejected = d.path().join(format!("invalid-{invalid}"));
        assert!(
            shards::materialize_with_write_limit(
                &input,
                &seal,
                &rejected,
                ShardLimits::default(),
                invalid
            )
            .is_err()
        );
        assert!(!rejected.exists());
    }
}

#[test]
fn complete_bytes_are_in_the_same_budget_and_failure_leaves_no_marker() {
    let d = tempfile::tempdir().unwrap();
    let input = d.path().join("input");
    let seal = synthetic_input(&input, 1, 0);
    let mut manifest = shards::materialize(
        &input,
        &seal,
        &d.path().join("full"),
        ShardLimits::default(),
    )
    .unwrap();
    let shards = manifest["publication"]["cumulative_shard_write_bytes"]
        .as_u64()
        .unwrap();
    let mut limit = shards;
    // The only changed content is this decimal bound. Its encoded length settles
    // once its digit count is stable; no manifest/COMPLETE bytes are excluded.
    for _ in 0..8 {
        manifest["writer"]["settings"]["max_dataset_written_bytes"] = json!(limit);
        let next = shards + serde_json::to_vec_pretty(&manifest).unwrap().len() as u64;
        if next == limit {
            break;
        }
        limit = next;
    }
    manifest["writer"]["settings"]["max_dataset_written_bytes"] = json!(limit);
    assert_eq!(
        limit,
        shards + serde_json::to_vec_pretty(&manifest).unwrap().len() as u64
    );
    let output = d.path().join("stopped-before-complete");
    assert!(
        shards::materialize_with_write_limit(&input, &seal, &output, ShardLimits::default(), limit)
            .unwrap_err()
            .to_string()
            .contains("DATASET_CUMULATIVE_WRITE_LIMIT")
    );
    assert!(output.join("manifest.json").exists());
    assert!(!output.join("COMPLETE").exists());
    assert_eq!(
        fs::read(output.join("manifest.json")).unwrap(),
        serde_json::to_vec_pretty(&manifest).unwrap()
    );
}
