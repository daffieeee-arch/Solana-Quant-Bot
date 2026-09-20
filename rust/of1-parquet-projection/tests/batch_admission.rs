//! Synthetic admission boundaries; not authentic acquisition or protocol evidence.
use of1_parquet_projection::{batch, hash};
use serde_json::{Value, json};

fn fixture() -> (Value, Value, Value, Value, Value) {
    let sample = json!({"start_slot":100,"end_slot_exclusive":104});
    let receipts = (4..8)
        .map(|i| json!({"sequence":i,"raw_bytes":10}))
        .collect::<Vec<_>>();
    let bindings = json!({"manifest_sha256":"source","receipts":receipts});
    let workers = json!({"batch_decoder_sha256":"a".repeat(64),"projector_sha256":"b".repeat(64)});
    let logical = (100..104)
        .map(|slot| json!({"source_id":"native","slot":slot,"role":"ORIGINAL_SELECTION"}))
        .collect::<Vec<_>>();
    let batches = vec![
        json!({"batch_id":"first","source_id":"native","slots":[100,101],"receipt_sequences":[4,5],"output_directory":"first"}),
        json!({"batch_id":"second","source_id":"native","slots":[102,103],"receipt_sequences":[6,7],"output_directory":"second"}),
    ];
    let plan = json!({"schema":"OF1_BATCH_COLLECTION_PLAN_1","collection_id":"synthetic","research_ready":false,"workers":workers,"logical_selection":logical,"sources":[{"source_id":"native","run_root":"/synthetic/run","run_id":"native-run","bindings":bindings,"sample_identity":sample}],"batches":batches});
    let raw = serde_json::to_string(&plan).unwrap();
    let binding = json!({"schema":"OF1_BATCH_BINDING_1","plan_json":raw,"plan_sha256":hash(raw.as_bytes()),"batch_id":"first","source_id":"native","selected_slots":[100,101],"receipt_sequences":[4,5],"original_bindings":bindings,"sample_identity":sample,"source_run_root":"/synthetic/run","source_run_id":"native-run","logical_selection":logical,"workers":workers});
    let execution = json!({"batch_binding":binding,"run_root":"/synthetic/run","executable_sha256":"a".repeat(64)});
    let requests=(4..8).map(|i|json!({"sequence":i,"kind":{"kind":"CAR_RANGE","slot":96+i,"start":i*10,"end_exclusive":i*10+10}})).collect::<Vec<_>>();
    (
        binding,
        execution,
        bindings,
        sample,
        json!({"prepared":{"requests":requests}}),
    )
}
fn reseal_plan(binding: &mut Value, execution: &mut Value, edit: impl FnOnce(&mut Value)) {
    let mut plan: Value = serde_json::from_str(binding["plan_json"].as_str().unwrap()).unwrap();
    edit(&mut plan);
    binding["logical_selection"] = plan["logical_selection"].clone();
    let raw = serde_json::to_string(&plan).unwrap();
    binding["plan_sha256"] = json!(hash(raw.as_bytes()));
    binding["plan_json"] = json!(raw);
    execution["batch_binding"] = binding.clone();
}

#[test]
fn physical_subset_keeps_complete_native_selection_and_binding() {
    let (binding, execution, source, sample, payload) = fixture();
    assert_eq!(
        batch::selected(&binding, &execution, &source, &sample, &payload).unwrap(),
        vec![100, 101]
    );
    assert_eq!(sample["end_slot_exclusive"], 104);
    assert_eq!(source["receipts"].as_array().unwrap().len(), 4);
}
#[test]
fn cannot_relabel_or_reduce_original_sample_even_with_rehashed_plan() {
    for mode in ["context", "subset", "sample"] {
        let (mut b, mut e, s, sample, p) = fixture();
        reseal_plan(&mut b, &mut e, |plan| match mode {
            "context" => {
                plan["logical_selection"][0]["role"] = json!("POSTHOC_DESCRIPTIVE_CONTEXT");
            }
            "subset" => {
                plan["logical_selection"]
                    .as_array_mut()
                    .unwrap()
                    .truncate(2);
                plan["batches"].as_array_mut().unwrap().truncate(1);
            }
            _ => plan["sources"][0]["sample_identity"] = Value::Null,
        });
        assert!(batch::selected(&b, &e, &s, &sample, &p).is_err(), "{mode}");
    }
}
#[test]
fn partition_gap_overlap_duplicate_source_or_output_is_rejected() {
    for mode in [
        "gap",
        "overlap",
        "source",
        "output",
        "unsorted",
        "logical_gap",
    ] {
        let (mut b, mut e, s, sample, p) = fixture();
        reseal_plan(&mut b, &mut e, |plan| match mode {
            "gap" => {
                plan["batches"].as_array_mut().unwrap().pop();
            }
            "overlap" => plan["batches"][1]["receipt_sequences"] = json!([5, 7]),
            "source" => {
                let duplicate = plan["sources"][0].clone();
                plan["sources"].as_array_mut().unwrap().push(duplicate);
            }
            "output" => plan["batches"][1]["output_directory"] = json!("first"),
            "logical_gap" => {
                plan["logical_selection"][3]["slot"] = json!(104);
                plan["batches"][1]["slots"][1] = json!(104);
            }
            _ => plan["logical_selection"][1]["slot"] = json!(99),
        });
        assert!(batch::selected(&b, &e, &s, &sample, &p).is_err(), "{mode}");
    }
}
#[test]
fn receipt_slot_hash_worker_and_original_source_are_exact() {
    for mode in [
        "slot",
        "receipt",
        "source",
        "hash",
        "worker",
        "raw_bytes",
        "huge",
    ] {
        let (mut b, mut e, mut s, sample, mut p) = fixture();
        match mode {
            "slot" => p["prepared"]["requests"][0]["kind"]["slot"] = json!(99),
            "receipt" => {
                s["receipts"].as_array_mut().unwrap().remove(0);
            }
            "source" => s["manifest_sha256"] = json!("changed"),
            "hash" => b["plan_sha256"] = json!("0".repeat(64)),
            "worker" => e["executable_sha256"] = json!("0".repeat(64)),
            "raw_bytes" => p["prepared"]["requests"][0]["kind"]["end_exclusive"] = json!(51),
            _ => p["prepared"]["requests"][0]["kind"]["start"] = json!(u64::MAX),
        }
        assert!(batch::selected(&b, &e, &s, &sample, &p).is_err(), "{mode}");
    }
}

/// Only a sealed-input fixture: it does not impersonate a decoded source run.
fn sealed_input_fixture(root: &std::path::Path, projector_hash: &str) -> String {
    let binding = json!({"schema":"SYNTHETIC_SEAL_BOUNDARY_ONLY","workers":{"projector_sha256":projector_hash}});
    let mut execution = json!({"schema":"OF1_BRONZE_EXECUTION_1","batch_binding":binding});
    std::fs::write(
        root.join("batch.json"),
        serde_json::to_vec(&binding).unwrap(),
    )
    .unwrap();
    for (name, key, bytes) in [
        ("bronze.jsonl", "bronze_jsonl_sha256", b"".as_slice()),
        ("silver.jsonl", "silver_jsonl_sha256", b"".as_slice()),
        ("quality.json", "quality_sha256", b"{}".as_slice()),
        (
            "quality.html",
            "html_sha256",
            b"synthetic seal fixture".as_slice(),
        ),
    ] {
        std::fs::write(root.join(name), bytes).unwrap();
        execution[key] = json!(hash(bytes));
    }
    std::fs::write(root.join("COMPLETE"), hash(b"{}")).unwrap();
    let bytes = serde_json::to_vec(&execution).unwrap();
    std::fs::write(root.join("execution.json"), &bytes).unwrap();
    hash(&bytes)
}

#[test]
fn sealed_batch_hashes_actual_test_executable_with_separate_executable_bound() {
    use of1_parquet_projection::{MAX_EXECUTABLE_BYTES, admission, shards, storage};
    assert_eq!(storage::MAX_FILE_BYTES, 64 * 1024 * 1024);
    assert_eq!(MAX_EXECUTABLE_BYTES, 256 * 1024 * 1024);
    let executable = std::env::current_exe().unwrap();
    let (identity, size) = shards::file_hash(&executable, MAX_EXECUTABLE_BYTES).unwrap();
    // The retained debug test executable is >64 MiB; release/stripped test
    // builds remain valid without asserting a platform-specific binary size.
    if size > storage::MAX_FILE_BYTES {
        assert_eq!(
            shards::file_hash(&executable, storage::MAX_FILE_BYTES)
                .unwrap_err()
                .to_string(),
            "FILE_LIMIT"
        );
    }
    let d = tempfile::tempdir().unwrap();
    let seal_hash = sealed_input_fixture(d.path(), &identity);
    let result = admission::seal(d.path(), &seal_hash).unwrap();
    assert_eq!(
        result["execution"]["batch_binding"]["workers"]["projector_sha256"],
        identity
    );
    let wrong = tempfile::tempdir().unwrap();
    let wrong_hash = sealed_input_fixture(wrong.path(), &"0".repeat(64));
    assert_eq!(
        admission::seal(wrong.path(), &wrong_hash)
            .unwrap_err()
            .to_string(),
        "BATCH_PROJECTOR_EXECUTABLE_MISMATCH"
    );
}

#[test]
fn executable_identity_bound_does_not_expand_any_sealed_data_file_bound() {
    use of1_parquet_projection::{MAX_EXECUTABLE_BYTES, admission, shards, storage};
    let identity = shards::file_hash(&std::env::current_exe().unwrap(), MAX_EXECUTABLE_BYTES)
        .unwrap()
        .0;
    let d = tempfile::tempdir().unwrap();
    let seal_hash = sealed_input_fixture(d.path(), &identity);
    let raw = d.path().join("bronze.jsonl");
    std::fs::File::create(&raw)
        .unwrap()
        .set_len(storage::MAX_FILE_BYTES + 1)
        .unwrap();
    assert_eq!(
        shards::file_hash(&raw, storage::MAX_FILE_BYTES)
            .unwrap_err()
            .to_string(),
        "FILE_LIMIT"
    );
    assert!(storage::bounded_read(&raw).is_err());
    assert_eq!(
        admission::seal(d.path(), &seal_hash)
            .unwrap_err()
            .to_string(),
        "FILE_LIMIT"
    );
}
