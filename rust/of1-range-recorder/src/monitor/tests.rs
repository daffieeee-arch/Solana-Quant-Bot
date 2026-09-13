use super::*;
use crate::{
    FormatSource,
    acquisition_http::parse_response_head,
    durable::{
        SystemClock,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, StageBudget, current_executable_sha256, metadata_requests,
        },
    },
};
use std::fs;

// Pure-model cases need no executable hash, writer lock, disk traversal, or
// acquisition-store fixture. The separate recorded-reader cases retain those gates.
fn model_snapshot() -> Snapshot {
    let snapshot: Snapshot = serde_json::from_value(serde_json::json!({
        "schema_version":"OF1_MONITOR_1", "id":"a".repeat(64), "session_id":"model-fixture", "sequence":0,
        "updated_at_ms":1_000_000, "kind":"LOCAL_SIMULATION", "mode":"RECORDED", "stage":"STOPPED",
        "label":"Pure model fixture", "dataset_root":"/tmp/of1-model-fixture", "source":"LOCAL_LOOPBACK_TLS",
        "epoch":978, "selected_slots":null, "started_at_ms":1_000_000, "completed_at_ms":null, "elapsed_ms":0,
        "selection":{"operations_total":4,"operations_published":0,"planned_bytes":null,"received_selection_bytes":0,"published_bytes":0,"verified_bytes":0},
        "traffic":{"received_bytes":0,"received_basis":"RECEIPTS_ONLY","reserved_bytes":0,"attempts":0,"retries":0,"speed_bps":null,"download_eta_ms":null,"eta_scope":null,"speed_samples":[]},
        "storage":{"used_bytes":0,"available_bytes":536_870_912,"cap_bytes":268_435_456},
        "budgets":{"attempts_remaining":16,"entity_bytes_remaining":134_217_728,"stage_attempts_remaining":12,"stage_entity_bytes_remaining":15_576_576,"runtime_remaining_ms":600_000},
        "operations":metadata_requests().iter().map(|r| operation(r, 978)).collect::<Vec<_>>(),
        "integrity":{"receipts":"PARTIAL_PUBLISHED_ONLY","car":"NOT_ACQUIRED","root_to_slot":"UNAVAILABLE"},
        "domain_counts":"UNAVAILABLE_NOT_DECODED_IN_B4",
        "artifacts":[{"id":"run-manifest","label":"Synthetic manifest identity","path":"run.json","sha256":"b".repeat(64)}],
        "errors":[],"dropped_samples":0
    })).unwrap();
    snapshot.validate().unwrap();
    snapshot
}

fn store() -> (
    tempfile::TempDir,
    std::path::PathBuf,
    AcquisitionStore<SystemClock>,
) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("run");
    let plan = AggregatePlan {
        schema: AGGREGATE_SCHEMA.into(),
        sample_identity: None,
        epoch: 978,
        format_source: FormatSource::pinned(),
        code_sha: "a".repeat(40),
        toolchain_fingerprint: "b".repeat(64),
        executable_sha256: current_executable_sha256().unwrap(),
        budget: AggregateBudget {
            max_slots: 2,
            max_plan_bytes: 1_048_576,
            max_requests: 16,
            max_response_entity_bytes: 16_777_216,
            max_total_response_entity_bytes: 134_217_728,
            max_disk_bytes: 268_435_456,
            required_free_disk_bytes: 1,
            max_memory_bytes: 536_870_912,
            max_runtime_ms: 1_800_000,
            response_timeout_ms: 30_000,
            request_retries: 2,
        },
    };
    let lease = MetadataLease {
        schema: "OF1_METADATA_LEASE_1".into(),
        authority: Authority::Fixture,
        budget: StageBudget {
            max_requests: 12,
            max_response_entity_bytes_total: 15_576_576,
            max_runtime_ms: 600_000,
        },
    };
    let store = AcquisitionStore::create(&root, plan, lease, SystemClock).unwrap();
    (dir, root, store)
}

fn publish(store: &mut AcquisitionStore<SystemClock>, sequence: u64, bytes: &[u8]) {
    let permit = store.reserve(sequence).unwrap();
    let length = if sequence == 3 {
        709_264_399_796
    } else {
        bytes.len() as u64
    };
    let head = parse_response_head(
        format!("HTTP/1.1 200 OK\r\nContent-Length: {length}\r\n\r\n").as_bytes(),
        store.request(sequence).unwrap(),
    )
    .unwrap();
    store.begin_stream(&permit, head).unwrap();
    for chunk in bytes.chunks(65_536) {
        store.append_stream(&permit, chunk).unwrap();
    }
    store.finish_stream(permit).unwrap();
}

#[test]
fn recorded_metadata_counts_are_receipt_bound_and_not_epoch_or_budget_progress() {
    let (_temp, root, mut store) = store();
    publish(&mut store, 0, &vec![0; 5_184_000]);
    publish(&mut store, 1, b"synthetic checksum annotation\n");
    publish(&mut store, 2, b"synthetic CID annotation\n");
    publish(&mut store, 3, b"");
    let before = fs::read(root.join("run.json")).unwrap();
    // Store still owns its writer lock. Import must neither need nor release it.
    let snapshot = read_run(&root).unwrap();
    assert_eq!(snapshot.mode, "RECORDED");
    assert_eq!(snapshot.kind, "LOCAL_SIMULATION");
    assert_eq!(snapshot.stage, "COMPLETE");
    assert_eq!(snapshot.selection.operations_published, 4);
    assert_eq!(snapshot.selection.planned_bytes, Some(5_184_055));
    assert_eq!(snapshot.selection.published_bytes, 5_184_055);
    assert_eq!(snapshot.traffic.reserved_bytes, 5_192_192);
    assert_eq!(snapshot.operations[3].expected_bytes, Some(0));
    assert_eq!(snapshot.traffic.received_basis, "RECEIPTS_ONLY");
    assert!(snapshot.traffic.speed_bps.is_none());
    assert!(snapshot.traffic.download_eta_ms.is_none());
    assert!(snapshot.selected_slots.is_none());
    assert_eq!(snapshot.domain_counts, "UNAVAILABLE_NOT_DECODED_IN_B4");
    assert_eq!(snapshot.integrity.car, "NOT_ACQUIRED");
    assert_eq!(snapshot.artifacts.len(), 5);
    assert_eq!(fs::read(root.join("run.json")).unwrap(), before);
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(root.join("writer.lock"))
        .unwrap();
    assert!(lock.try_lock().is_err());
    assert_eq!(store.progress().unwrap().published_requests, 4);
}

#[test]
fn incomplete_recorded_run_does_not_invent_traffic_error_or_elapsed_runtime() {
    let (_temp, root, mut store) = store();
    let _permit = store.reserve(0).unwrap();
    let snapshot = read_run(&root).unwrap();
    assert_eq!(snapshot.traffic.received_basis, "RECEIPTS_ONLY");
    assert_eq!(snapshot.traffic.attempts, 1);
    assert_eq!(snapshot.traffic.reserved_bytes, 5_184_000);
    assert!(snapshot.selection.planned_bytes.is_none());
    assert_eq!(snapshot.operations[0].state, "FAILED");
    assert!(snapshot.operations[0].received_bytes.is_none());
    assert!(
        snapshot.operations[0]
            .error
            .as_ref()
            .unwrap()
            .contains("unavailable")
    );
    assert!(snapshot.completed_at_ms.is_none());
    assert_eq!(snapshot.elapsed_ms, 0); // No receipt interval exists, not an asserted runtime.
}

#[test]
fn recorded_reader_rejects_raw_and_header_hash_drift_without_repair() {
    let (_temp, root, mut store) = store();
    publish(&mut store, 0, &vec![0; 5_184_000]);
    let raw = root.join("published/0000000000/raw.bin");
    let original = fs::read(&raw).unwrap();
    let mut drifted = original.clone();
    drifted[0] = 1;
    fs::write(&raw, &drifted).unwrap();
    assert!(
        read_run(&root)
            .unwrap_err()
            .to_string()
            .contains("hash mismatch")
    );
    assert_eq!(fs::read(&raw).unwrap(), drifted);
    fs::write(&raw, original).unwrap();
    let receipt = root.join("published/0000000000/receipt.json");
    let mut data: serde_json::Value = serde_json::from_slice(&fs::read(&receipt).unwrap()).unwrap();
    data["response_headers_sha256"] = "0".repeat(64).into();
    fs::write(&receipt, serde_json::to_vec(&data).unwrap()).unwrap();
    assert!(read_run(&root).is_err());
}

#[test]
fn recorded_reader_rejects_source_identity_and_unbounded_nonregular_tree() {
    let (_temp, root, _store) = store();
    let manifest = root.join("run.json");
    let before = fs::read(&manifest).unwrap();
    let mut data: serde_json::Value = serde_json::from_slice(&before).unwrap();
    data["aggregate_sha256"] = "0".repeat(64).into();
    fs::write(&manifest, serde_json::to_vec(&data).unwrap()).unwrap();
    assert!(read_run(&root).is_err());
    fs::write(&manifest, before).unwrap();
    std::os::unix::fs::symlink(root.join("run.json"), root.join("injected-link")).unwrap();
    assert!(read_run(&root).is_err());
}

#[test]
fn selection_excludes_failed_retry_bytes_and_metadata_unknown_is_not_zero() {
    let mut snapshot = model_snapshot();
    assert!(snapshot.selection.planned_bytes.is_none());
    snapshot.operations[0].state = "FAILED".into();
    snapshot.operations[0].received_bytes = Some(1000);
    snapshot.traffic.received_bytes = 1000;
    snapshot.operations[0].attempts = 1;
    snapshot.refresh_selection();
    assert_eq!(snapshot.selection.received_selection_bytes, 0);
    snapshot.operations[0].state = "DOWNLOADING".into();
    snapshot.operations[0].received_bytes = Some(100);
    snapshot.operations[0].attempts = 2;
    snapshot.traffic.received_bytes += 100;
    snapshot.refresh_selection();
    assert_eq!(snapshot.selection.received_selection_bytes, 100);
    assert_eq!(snapshot.traffic.received_bytes, 1100);
    assert_eq!(snapshot.traffic.retries, 1);
    assert_eq!(
        operation(&metadata_requests()[3], 978).expected_bytes,
        Some(0)
    );
}

#[test]
fn rates_need_measurements_and_known_selection_never_budget_denominator() {
    let mut snapshot = model_snapshot();
    snapshot.stage = "DOWNLOADING".into();
    let mut rates = Rates::new(0);
    for ms in (200..=1200).step_by(200) {
        snapshot.elapsed_ms = ms;
        snapshot.traffic.received_bytes += 1000;
        rates.observe(&mut snapshot, ms);
        assert!(snapshot.traffic.download_eta_ms.is_none());
    }
    snapshot.operations[1].expected_bytes = Some(20);
    snapshot.operations[2].expected_bytes = Some(40);
    snapshot.operations[0].received_bytes = Some(6000);
    snapshot.refresh_selection();
    snapshot.traffic.received_bytes += 1000;
    rates.observe(&mut snapshot, 1400);
    assert_eq!(snapshot.traffic.speed_bps, Some(5000.0));
    assert!(
        snapshot
            .traffic
            .download_eta_ms
            .unwrap()
            .abs_diff(1_035_612)
            <= 1
    );
    assert_eq!(snapshot.traffic.eta_scope.as_deref(), Some("SELECTION"));
    snapshot.stage = "VERIFYING".into();
    rates.observe(&mut snapshot, 1600);
    assert!(snapshot.traffic.speed_bps.is_none());
    assert!(snapshot.traffic.download_eta_ms.is_none());
    for ms in (1800..=20_000).step_by(200) {
        rates.observe(&mut snapshot, ms);
    }
    assert_eq!(snapshot.traffic.speed_samples.len(), MAX_SAMPLES);
}

#[test]
fn current_operation_eta_is_explicit_when_sidecar_selection_size_is_unknown() {
    let mut snapshot = model_snapshot();
    snapshot.stage = "DOWNLOADING".into();
    snapshot.operations[0].state = "DOWNLOADING".into();
    let mut rates = Rates::new(0);
    for ms in (200..=1200).step_by(200) {
        snapshot.elapsed_ms = ms;
        snapshot.traffic.received_bytes += 1000;
        snapshot.operations[0].received_bytes = Some(snapshot.traffic.received_bytes);
        snapshot.refresh_selection();
        rates.observe(&mut snapshot, ms);
        if ms < 1000 {
            assert!(snapshot.traffic.download_eta_ms.is_none());
        }
    }
    assert!(snapshot.selection.planned_bytes.is_none());
    assert!(snapshot.traffic.download_eta_ms.is_some());
    assert_eq!(
        snapshot.traffic.eta_scope.as_deref(),
        Some("CURRENT_OPERATION")
    );
}

#[test]
fn wire_snapshot_is_bounded_and_rejects_duplicate_or_unsafe_values() {
    let original = model_snapshot();
    let mut snapshot = original.clone();
    snapshot.operations.push(snapshot.operations[0].clone());
    assert!(snapshot.validate().is_err());
    snapshot = original.clone();
    snapshot.updated_at_ms = JS_SAFE + 1;
    assert!(snapshot.validate().is_err());
    snapshot = original.clone();
    snapshot.traffic.speed_bps = Some(f64::NAN);
    assert!(snapshot.validate().is_err());
    snapshot = original.clone();
    snapshot.artifacts[0].path = "../run.json".into();
    assert!(snapshot.validate().is_err());
    snapshot = original;
    snapshot.errors = vec!["error".into(); MAX_ERRORS + 1];
    assert!(snapshot.validate().is_err());
}
