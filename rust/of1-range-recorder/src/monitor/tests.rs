use super::*;
use crate::{
    FormatSource,
    acquisition_http::parse_response_head,
    durable::{
        Clock, ClockSample, StoreResult, SystemClock,
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
    store_with_rate(None)
}

fn store_with_rate(
    download_rate: Option<crate::rate::DownloadRate>,
) -> (
    tempfile::TempDir,
    std::path::PathBuf,
    AcquisitionStore<SystemClock>,
) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("run");
    let store = AcquisitionStore::create(
        &root,
        fixture_plan(download_rate, None),
        fixture_lease(),
        SystemClock,
    )
    .unwrap();
    (dir, root, store)
}

fn fixture_plan(
    download_rate: Option<crate::rate::DownloadRate>,
    clock_policy: Option<crate::clock_contract::ClockPolicy>,
) -> AggregatePlan {
    AggregatePlan {
        schema: AGGREGATE_SCHEMA.into(),
        sample_identity: None,
        download_rate,
        clock_policy,
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
    }
}

fn fixture_lease() -> MetadataLease {
    MetadataLease {
        schema: "OF1_METADATA_LEASE_1".into(),
        authority: Authority::Fixture,
        budget: StageBudget {
            max_requests: 12,
            max_response_entity_bytes_total: 15_576_576,
            max_runtime_ms: 600_000,
        },
    }
}

fn publish<C: Clock>(store: &mut AcquisitionStore<C>, sequence: u64, bytes: &[u8]) {
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

fn rate_policy() -> crate::rate::DownloadRate {
    serde_json::from_value(serde_json::json!({
        "unit": "RESPONSE_ENTITY_BYTES", "bytes_per_second": 87_500_000,
        "burst_bytes": 65_536, "concurrency": 1,
        "scope": "SAME_USER_OFFICIAL_OF1_ALL_RUNS"
    }))
    .unwrap()
}

#[test]
fn historical_rate_absence_is_preserved_and_recorded_waits_stay_unknown() {
    let mut snapshot = model_snapshot();
    assert!(snapshot.rate_limit.is_none());
    assert!(
        serde_json::to_value(&snapshot)
            .unwrap()
            .get("rate_limit")
            .is_none()
    );
    snapshot.rate_limit = Some(RateLimit::recorded(rate_policy()));
    snapshot.validate().unwrap();
    let value = serde_json::to_value(&snapshot).unwrap();
    assert!(value["rate_limit"]["waiting"].is_null());
    assert!(value["rate_limit"]["process_wait_ns"].is_null());
    snapshot.rate_limit.as_mut().unwrap().waiting = Some(false);
    snapshot.rate_limit.as_mut().unwrap().process_wait_ns = Some(0);
    assert!(snapshot.validate().is_err()); // Import cannot manufacture no waiting.
}

#[test]
fn read_only_import_uses_actual_plan_rate_without_inventing_historical_waits() {
    let (_temporary, root, _store) = store_with_rate(Some(rate_policy()));
    let before = fs::read(root.join("run.json")).unwrap();
    let snapshot = read_run(&root).unwrap();
    let limit = snapshot.rate_limit.as_ref().unwrap();
    assert_eq!(limit.policy, rate_policy());
    assert!(limit.waiting.is_none());
    assert!(limit.process_wait_ns.is_none());
    assert_eq!(fs::read(root.join("run.json")).unwrap(), before);
    assert_eq!(snapshot.mode, "RECORDED");
    assert!(snapshot.traffic.speed_bps.is_none());
    snapshot.validate().unwrap();
}

#[test]
fn rate_snapshot_rejects_unknown_units_bounds_and_contradictory_measurements() {
    let mut snapshot = model_snapshot();
    snapshot.mode = "LIVE".into();
    snapshot.stage = "DOWNLOADING".into();
    snapshot.rate_limit = Some(RateLimit {
        policy: rate_policy(),
        waiting: Some(true),
        process_wait_ns: Some(749_029),
    });
    snapshot.validate().unwrap();
    let original = serde_json::to_value(&snapshot).unwrap();
    for (field, value) in [
        ("bytes_per_second", serde_json::json!(0)),
        ("bytes_per_second", serde_json::json!(87_500_001)),
        ("burst_bytes", serde_json::json!(65_537)),
        ("concurrency", serde_json::json!(2)),
        ("unit", serde_json::json!("PHYSICAL_WIRE_BYTES")),
        ("scope", serde_json::json!("PER_CONNECTION")),
    ] {
        let mut changed = original.clone();
        changed["rate_limit"]["policy"][field] = value;
        let rejected: Snapshot = serde_json::from_value(changed).unwrap();
        assert!(rejected.validate().is_err(), "{field}");
    }
    snapshot.rate_limit.as_mut().unwrap().process_wait_ns = Some(JS_SAFE + 1);
    assert!(snapshot.validate().is_err());
    snapshot.rate_limit.as_mut().unwrap().process_wait_ns = None;
    assert!(snapshot.validate().is_err());
    snapshot.rate_limit.as_mut().unwrap().process_wait_ns = Some(0);
    snapshot.stage = "COMPLETE".into();
    assert!(snapshot.validate().is_err());
    let mut changed = original;
    changed["rate_limit"]["policy"]["bytes_per_second"] = serde_json::json!(-1);
    assert!(serde_json::from_value::<Snapshot>(changed).is_err());
}

#[cfg(feature = "monitor")]
#[test]
fn limiter_observations_preserve_submillisecond_waits_and_restart_scope() {
    let directory = tempfile::tempdir().unwrap();
    let mut monitor = Monitor::new(model_snapshot(), &directory.path().join("absent.sock"));
    monitor.rate_policy(&rate_policy());
    monitor.head(0, 200, 1000);
    let original_deadline = monitor.snapshot.budgets.runtime_remaining_ms;
    monitor.rate_wait_started(749_029);
    assert_eq!(
        monitor.snapshot.rate_limit.as_ref().unwrap().waiting,
        Some(true)
    );
    assert_eq!(
        monitor
            .snapshot
            .rate_limit
            .as_ref()
            .unwrap()
            .process_wait_ns,
        Some(0)
    );
    monitor.rate_wait_finished(800_111);
    monitor.received(0, 512);
    monitor.rate_wait_started(1);
    monitor.rate_wait_finished(12);
    assert_eq!(
        monitor.snapshot.rate_limit.as_ref().unwrap().waiting,
        Some(false)
    );
    assert_eq!(
        monitor
            .snapshot
            .rate_limit
            .as_ref()
            .unwrap()
            .process_wait_ns,
        Some(800_123)
    );
    assert_eq!(monitor.snapshot.traffic.received_bytes, 512);
    assert_eq!(monitor.snapshot.selection.published_bytes, 0);
    assert!(monitor.snapshot.budgets.runtime_remaining_ms <= original_deadline);
    monitor.rate_wait_started(1);
    monitor.failed(0, "DEADLINE_EXCEEDED");
    assert_eq!(
        monitor.snapshot.rate_limit.as_ref().unwrap().waiting,
        Some(false)
    );
    monitor.snapshot.validate().unwrap();
    let restarted = Monitor::new(monitor.snapshot, &directory.path().join("absent.sock"));
    assert_eq!(
        restarted
            .snapshot
            .rate_limit
            .as_ref()
            .unwrap()
            .process_wait_ns,
        Some(0)
    );
    assert!(restarted.snapshot.errors.is_empty());
}

#[derive(Clone)]
struct MonitorClock(std::rc::Rc<std::cell::RefCell<ClockSample>>);
impl MonitorClock {
    fn new() -> Self {
        Self(std::rc::Rc::new(std::cell::RefCell::new(ClockSample {
            wall_ms: 1_000_000,
            boot_ms: 1000,
            boot_id: "monitor-fixture-boot".into(),
        })))
    }
    fn set(&self, wall_ms: u64, boot_ms: u64) {
        self.0.borrow_mut().wall_ms = wall_ms;
        self.0.borrow_mut().boot_ms = boot_ms;
    }
}
impl Clock for MonitorClock {
    fn sample(&self) -> StoreResult<ClockSample> {
        Ok(self.0.borrow().clone())
    }
}

fn clock_context() -> ClockContext {
    ClockContext {
        policy: crate::clock_contract::ClockPolicy::standard(),
        boot_id: "monitor-fixture-boot".into(),
        started_at_boot_ms: 1000,
        deadline_boot_ms: 601_000,
        observed_boot_ms: None,
        runtime_status: "UNAVAILABLE_CLOCK".into(),
    }
}

#[test]
fn boot_runtime_does_not_follow_utc_jumps_or_fabricate_zero_after_reboot() {
    let mut context = clock_context();
    let clock = MonitorClock::new();
    clock.set(999_998, 1010);
    assert_eq!(
        context.observe(Some(&clock.sample().unwrap())),
        Some(599_990)
    );
    clock.set(9_999_999, 1020);
    assert_eq!(
        context.observe(Some(&clock.sample().unwrap())),
        Some(599_980)
    );
    assert_eq!(context.observe(None), None);
    assert_eq!(context.runtime_status, "UNAVAILABLE_CLOCK");
    assert_eq!(context.observed_boot_ms, Some(1020));
    clock.set(100, 1019);
    assert_eq!(context.observe(Some(&clock.sample().unwrap())), None);
    assert_eq!(context.runtime_status, "UNAVAILABLE_BOOT_ROLLBACK");
    let mut other = clock.sample().unwrap();
    other.boot_id = "different-boot".into();
    assert_eq!(context.observe(Some(&other)), None);
    assert_eq!(context.runtime_status, "UNAVAILABLE_BOOT_MISMATCH");
    assert_eq!(context.observed_boot_ms, Some(1020));
    clock.set(1, 601_000);
    assert_eq!(context.observe(Some(&clock.sample().unwrap())), Some(0));
    assert_eq!(context.deadline_boot_ms, 601_000);
}

#[test]
fn new_policy_utc_recoil_receipts_import_restart_and_raw_utc_remain_exact() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().join("run");
    let clock = MonitorClock::new();
    let plan = fixture_plan(None, Some(crate::clock_contract::ClockPolicy::standard()));
    let mut store =
        AcquisitionStore::create(&root, plan.clone(), fixture_lease(), clock.clone()).unwrap();
    let original_manifest = fs::read(root.join("run.json")).unwrap();
    clock.set(999_998, 1010);
    let mut index = vec![0; 5_184_000];
    index[..8].copy_from_slice(&64u64.to_le_bytes());
    index[8..12].copy_from_slice(&32u32.to_le_bytes());
    publish(&mut store, 0, &index);
    let lease = store.current_lease_sha256().to_owned();
    drop(store);
    clock.set(999_996, 1020);
    let mut store = AcquisitionStore::resume(&root, &plan, &lease, clock.clone()).unwrap();
    publish(&mut store, 1, &[b'0'; 64]);
    clock.set(999_994, 1030);
    publish(
        &mut store,
        2,
        b"bafyreielu2mwxw6ymjjfmwxgfk72evqfx4d4cly2z7mbg52bosbwzdlaqe\n",
    );
    clock.set(999_992, 1040);
    publish(&mut store, 3, b"");
    let before: Vec<_> = (0..4)
        .map(|id| fs::read(root.join(format!("published/{id:010}/receipt.json"))).unwrap())
        .collect();
    let imported = recorded::read_run_context_at(&root, Some(&clock.sample().unwrap()))
        .unwrap()
        .snapshot;
    assert_eq!(imported.completed_at_ms, Some(999_992)); // Last causal receipt, not maximum UTC.
    assert_eq!(imported.started_at_ms, 1_000_000);
    assert_eq!(imported.elapsed_ms, 40);
    assert_eq!(imported.budgets.runtime_remaining_ms, Some(599_960));
    assert_eq!(imported.selection.operations_published, 4);
    assert_eq!(
        imported.clock_context.as_ref().unwrap().observed_boot_ms,
        Some(1040)
    );
    clock.set(99_999_999, 1050);
    let later = recorded::read_run_context_at(&root, Some(&clock.sample().unwrap()))
        .unwrap()
        .snapshot;
    assert_eq!(later.budgets.runtime_remaining_ms, Some(599_950));
    assert_eq!(later.elapsed_ms, 40);
    let mut rollback = clock.sample().unwrap();
    rollback.boot_ms = 1039;
    let backward = recorded::read_run_context_at(&root, Some(&rollback))
        .unwrap()
        .snapshot;
    assert!(backward.budgets.runtime_remaining_ms.is_none());
    assert_eq!(
        backward.clock_context.as_ref().unwrap().runtime_status,
        "UNAVAILABLE_BOOT_ROLLBACK"
    );
    let mut rebooted = clock.sample().unwrap();
    rebooted.boot_id = "new-boot".into();
    let recorded = recorded::read_run_context_at(&root, Some(&rebooted))
        .unwrap()
        .snapshot;
    assert!(recorded.budgets.runtime_remaining_ms.is_none());
    assert_eq!(
        recorded.clock_context.as_ref().unwrap().runtime_status,
        "UNAVAILABLE_BOOT_MISMATCH"
    );
    assert_eq!(recorded.elapsed_ms, 40); // Historical same-boot duration remains proven.
    assert_eq!(fs::read(root.join("run.json")).unwrap(), original_manifest);
    for (id, receipt) in before.iter().enumerate() {
        assert_eq!(
            &fs::read(root.join(format!("published/{id:010}/receipt.json"))).unwrap(),
            receipt
        );
    }
    let target = root.join("published/0000000003/receipt.json");
    for (field, value) in [
        ("boot_id", serde_json::json!("wrong-boot")),
        ("boot_ms", serde_json::json!(999)),
        ("boot_ms", serde_json::json!(601_000)),
        ("boot_ms", serde_json::json!(31_040)),
    ] {
        let mut corrupt: serde_json::Value = serde_json::from_slice(&before[3]).unwrap();
        corrupt["acquired_at"][field] = value;
        fs::write(&target, serde_json::to_vec(&corrupt).unwrap()).unwrap();
        assert!(recorded::read_run_context_at(&root, Some(&clock.sample().unwrap())).is_err());
    }
    fs::write(target, &before[3]).unwrap();
    let first = root.join("published/0000000000/receipt.json");
    let mut later_than_next_reservation: serde_json::Value =
        serde_json::from_slice(&before[0]).unwrap();
    later_than_next_reservation["acquired_at"]["boot_ms"] = serde_json::json!(1021);
    fs::write(
        &first,
        serde_json::to_vec(&later_than_next_reservation).unwrap(),
    )
    .unwrap();
    assert!(recorded::read_run_context_at(&root, Some(&clock.sample().unwrap())).is_err());
    fs::write(first, &before[0]).unwrap();
    assert_payload_stage_order(&mut store, &plan, &clock, &root);
}

fn assert_payload_stage_order(
    store: &mut AcquisitionStore<MonitorClock>,
    plan: &AggregatePlan,
    clock: &MonitorClock,
    root: &std::path::Path,
) {
    let published = (0..4)
        .map(|id| store.published(id).unwrap().unwrap())
        .collect::<Vec<_>>();
    let prepared = crate::acquisition::derive_payload_from_metadata(
        plan,
        &published,
        422_496_000,
        422_496_001,
    )
    .unwrap();
    clock.set(999_990, 1060);
    store
        .admit_payload(
            crate::durable::acquisition::PayloadLease {
                schema: "OF1_PAYLOAD_LEASE_1".into(),
                authority: Authority::Fixture,
                budget: StageBudget {
                    max_requests: 3,
                    max_response_entity_bytes_total: 96,
                    max_runtime_ms: 120_000,
                },
                prepared_payload_sha256: prepared.sha256().unwrap(),
                metadata_receipt_sha256: prepared.metadata_receipt_sha256().into(),
            },
            &prepared,
        )
        .unwrap();
    let payload_path = root.join("payload.json");
    let original_payload = fs::read(&payload_path).unwrap();
    let admitted = recorded::read_run_context_at(root, Some(&clock.sample().unwrap()))
        .unwrap()
        .snapshot;
    assert_eq!(admitted.budgets.runtime_remaining_ms, Some(120_000));
    for (boot, boot_id) in [
        (1060, "other-boot"),
        (999, "monitor-fixture-boot"),
        (1039, "monitor-fixture-boot"),
    ] {
        let mut corrupt: serde_json::Value = serde_json::from_slice(&original_payload).unwrap();
        corrupt["stage"]["started_at"]["boot_ms"] = serde_json::json!(boot);
        corrupt["stage"]["started_at"]["boot_id"] = serde_json::json!(boot_id);
        corrupt["stage"]["deadline_boot_ms"] = serde_json::json!(boot + 120_000);
        fs::write(&payload_path, serde_json::to_vec(&corrupt).unwrap()).unwrap();
        assert!(recorded::read_run_context_at(root, Some(&clock.sample().unwrap())).is_err());
    }
    fs::write(payload_path, original_payload).unwrap();
}

#[test]
fn fatal_clock_stop_is_readable_without_resuming_or_invalidating_prior_raw() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().join("run");
    let clock = MonitorClock::new();
    let plan = fixture_plan(None, Some(crate::clock_contract::ClockPolicy::standard()));
    let mut store = AcquisitionStore::create(&root, plan, fixture_lease(), clock.clone()).unwrap();
    publish(&mut store, 0, &vec![0; 5_184_000]);
    clock.set(999_998, 999);
    assert!(store.reserve(1).is_err());
    let marker_path = root.join("pending/clock-fatal.json");
    let original_marker = fs::read(&marker_path).unwrap();
    let snapshot = recorded::read_run_context_at(&root, None).unwrap().snapshot;
    assert_eq!(snapshot.stage, "STOPPED");
    assert_eq!(snapshot.selection.operations_published, 1);
    assert!(snapshot.budgets.runtime_remaining_ms.is_none());
    assert!(
        snapshot
            .errors
            .iter()
            .any(|error| error.contains("RECORDED_CLOCK_STOP_FATAL_CLOCK"))
    );
    assert_eq!(fs::read(&marker_path).unwrap(), original_marker);
    let mut malformed: serde_json::Value = serde_json::from_slice(&original_marker).unwrap();
    malformed["lease_sha256"] = serde_json::json!("0".repeat(64));
    fs::write(&marker_path, serde_json::to_vec(&malformed).unwrap()).unwrap();
    assert!(recorded::read_run_context_at(&root, None).is_err());
    fs::write(marker_path, original_marker).unwrap();
}
