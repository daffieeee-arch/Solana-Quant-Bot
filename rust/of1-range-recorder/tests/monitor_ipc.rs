//! Explicit Unix-only fixture lane. No provider connector or acquisition authority.
#![cfg(all(feature = "monitor", feature = "tls-fixture"))]

use of1_range_recorder::{
    durable::acquisition::Progress,
    monitor::{MAX_RUNS, Monitor, Relay, Snapshot, write_snapshot},
};
use std::{
    fs,
    os::unix::net::UnixDatagram,
    path::Path,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

fn snapshot(root: &Path) -> Snapshot {
    let now = u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis(),
    )
    .unwrap();
    serde_json::from_value(serde_json::json!({
        "schema_version":"OF1_MONITOR_1","id":"a".repeat(64),"session_id":"fixture-session","sequence":0,
        "updated_at_ms":now,"kind":"LOCAL_SIMULATION","mode":"LIVE","stage":"PREPARING","label":"Local fixture",
        "dataset_root":root,"source":"LOCAL_LOOPBACK_TLS","epoch":978,"selected_slots":null,
        "started_at_ms":now,"completed_at_ms":null,"elapsed_ms":0,
        "selection":{"operations_total":1,"operations_published":0,"planned_bytes":1000,"received_selection_bytes":0,"published_bytes":0,"verified_bytes":0},
        "traffic":{"received_bytes":0,"received_basis":"PROCESS_OBSERVED","reserved_bytes":0,"attempts":0,"retries":0,"speed_bps":null,"download_eta_ms":null,"eta_scope":null,"speed_samples":[]},
        "storage":{"used_bytes":0,"available_bytes":100_000,"cap_bytes":100_000},
        "budgets":{"attempts_remaining":4,"entity_bytes_remaining":4000,"stage_attempts_remaining":4,"stage_entity_bytes_remaining":4000,"runtime_remaining_ms":10000},
        "operations":[{"sequence":0,"method":"GET","path":"/978/epoch-978-slot-ranges.raw","range":null,"state":"PENDING","expected_bytes":1000,"received_bytes":0,"published_bytes":0,"attempts":0,"status_code":null,"error":null}],
        "integrity":{"receipts":"PENDING","car":"NOT_ACQUIRED","root_to_slot":"UNAVAILABLE"},
        "domain_counts":"UNAVAILABLE_NOT_DECODED_IN_B4","artifacts":[],"errors":[],"dropped_samples":0
    })).unwrap()
}

fn setup() -> (tempfile::TempDir, std::path::PathBuf) {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("run");
    fs::create_dir(&root).unwrap();
    (temp, root)
}

fn progress(attempts: u64, reserved: u64) -> Progress {
    let now = u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis(),
    )
    .unwrap();
    Progress {
        stage: "METADATA".into(),
        attempts_reserved: attempts,
        charged_entity_bytes: reserved,
        published_response_entity_bytes: 0,
        published_requests: 0,
        unpublished_attempts: attempts,
        unreceipted_response_entity_bytes: None,
        disk_charge_bytes: 4096,
        current_rss_bytes: 1024,
        peak_rss_bytes: 1024,
        available_disk_bytes: 100_000,
        deadline_wall_ms: now + 10000,
        deadline_boot_ms: 10000,
        current_lease_sha256: "b".repeat(64),
        evidence: "Fixture".into(),
        domain_counts: "UNAVAILABLE_NOT_DECODED_IN_B4".into(),
    }
}

#[test]
fn absent_collector_is_nonfatal_and_never_writes_to_run() {
    let (temp, root) = setup();
    let before = fs::read_dir(&root).unwrap().count();
    let mut monitor = Monitor::new(snapshot(&root), &temp.path().join("absent.sock"));
    monitor.reserve(0, &progress(1, 1000));
    monitor.head(0, 200, 1000);
    monitor.received(0, 123);
    monitor.emit(true);
    assert_eq!(monitor.snapshot.operations[0].received_bytes, Some(123));
    assert_eq!(monitor.snapshot.selection.published_bytes, 0);
    assert!(monitor.snapshot.dropped_samples >= 3);
    assert_eq!(fs::read_dir(&root).unwrap().count(), before);
}

#[test]
fn slow_collector_cannot_backpressure_the_writer() {
    let (temp, root) = setup();
    let socket = temp.path().join("slow.sock");
    let _collector = UnixDatagram::bind(&socket).unwrap();
    let mut monitor = Monitor::new(snapshot(&root), &socket);
    let started = Instant::now();
    for _ in 0..1000 {
        monitor.emit(true);
    }
    assert!(monitor.snapshot.dropped_samples > 0);
    assert!(started.elapsed() < Duration::from_secs(10));
    assert_eq!(fs::read_dir(&root).unwrap().count(), 0);
}

#[test]
fn in_request_datagram_publishes_measured_bytes_without_claiming_raw_publication() {
    let (temp, root) = setup();
    let socket = temp.path().join("relay.sock");
    let output = temp.path().join("snapshots");
    let mut relay = Relay::bind(&socket, &output).unwrap();
    let mut monitor = Monitor::new(snapshot(&root), &socket);
    monitor.reserve(0, &progress(1, 1000));
    assert!(relay.receive_once().unwrap());
    monitor.head(0, 200, 1000);
    assert!(relay.receive_once().unwrap());
    monitor.received(0, 250);
    monitor.emit(true);
    assert!(relay.receive_once().unwrap());
    let saved: Snapshot = serde_json::from_slice(
        &fs::read(output.join(format!("latest-{}.json", monitor.snapshot.id))).unwrap(),
    )
    .unwrap();
    assert_eq!(saved.stage, "DOWNLOADING");
    assert_eq!(saved.operations[0].received_bytes, Some(250));
    assert_eq!(saved.selection.received_selection_bytes, 250);
    assert_eq!(saved.traffic.received_bytes, 250);
    assert_eq!(saved.selection.published_bytes, 0);
    assert_eq!(saved.traffic.reserved_bytes, 1000);
    assert_eq!(saved.traffic.attempts, 1);
    assert_eq!(saved.budgets.attempts_remaining, 3);
    assert!(saved.traffic.download_eta_ms.is_none());
}

#[test]
fn retry_resets_unique_progress_but_preserves_measured_traffic_and_spending() {
    let (temp, root) = setup();
    let mut monitor = Monitor::new(snapshot(&root), &temp.path().join("absent.sock"));
    monitor.reserve(0, &progress(1, 1000));
    monitor.head(0, 200, 1000);
    monitor.received(0, 300);
    monitor.failed(0, "SHORT_BODY");
    assert_eq!(monitor.snapshot.selection.received_selection_bytes, 0);
    monitor.reserve(0, &progress(2, 2000));
    monitor.head(0, 200, 1000);
    monitor.received(0, 100);
    assert_eq!(monitor.snapshot.selection.received_selection_bytes, 100);
    assert_eq!(monitor.snapshot.traffic.received_bytes, 400);
    assert_eq!(monitor.snapshot.traffic.reserved_bytes, 2000);
    assert_eq!(monitor.snapshot.traffic.retries, 1);
    assert_eq!(monitor.snapshot.budgets.stage_entity_bytes_remaining, 2000);
    assert!(monitor.snapshot.errors.iter().any(|e| e == "SHORT_BODY"));
}

#[test]
fn relay_rejects_duplicate_sequence_and_older_restart_session() {
    let (temp, root) = setup();
    let socket = temp.path().join("relay.sock");
    let output = temp.path().join("snapshots");
    let mut relay = Relay::bind(&socket, &output).unwrap();
    let sender = UnixDatagram::unbound().unwrap();
    let mut old = snapshot(&root);
    old.sequence = 3;
    sender
        .send_to(&serde_json::to_vec(&old).unwrap(), &socket)
        .unwrap();
    assert!(relay.receive_once().unwrap());
    sender
        .send_to(&serde_json::to_vec(&old).unwrap(), &socket)
        .unwrap();
    assert!(relay.receive_once().is_err());
    let mut new = old.clone();
    new.session_id = "new-process".into();
    new.sequence = 1;
    new.updated_at_ms += 10;
    sender
        .send_to(&serde_json::to_vec(&new).unwrap(), &socket)
        .unwrap();
    assert!(relay.receive_once().unwrap());
    old.sequence = 4;
    sender
        .send_to(&serde_json::to_vec(&old).unwrap(), &socket)
        .unwrap();
    assert!(relay.receive_once().is_err());
    let saved: Snapshot =
        serde_json::from_slice(&fs::read(output.join(format!("latest-{}.json", old.id))).unwrap())
            .unwrap();
    assert_eq!(saved.session_id, "new-process");
}

#[test]
fn malformed_or_oversized_datagram_never_publishes_a_snapshot() {
    let (temp, _root) = setup();
    let socket = temp.path().join("relay.sock");
    let output = temp.path().join("snapshots");
    let mut relay = Relay::bind(&socket, &output).unwrap();
    let sender = UnixDatagram::unbound().unwrap();
    sender.send_to(b"{partial", &socket).unwrap();
    assert!(relay.receive_once().is_err());
    sender.send_to(&vec![b' '; 65_537], &socket).unwrap();
    assert!(relay.receive_once().is_err());
    assert_eq!(fs::read_dir(&output).unwrap().count(), 0);
}

#[test]
fn telemetry_refuses_immutable_directory_and_canonical_alias() {
    let (temp, root) = setup();
    fs::write(root.join("run.json"), b"immutable evidence").unwrap();
    let value = snapshot(&root);
    assert!(write_snapshot(&value, &root.join("telemetry")).is_err());
    let alias = temp.path().join("alias");
    std::os::unix::fs::symlink(&root, &alias).unwrap();
    assert!(write_snapshot(&value, &alias.join("telemetry")).is_err());
    assert!(!root.join("telemetry").exists());
    assert_eq!(
        fs::read(root.join("run.json")).unwrap(),
        b"immutable evidence"
    );
}

#[test]
fn telemetry_run_retention_is_bounded_and_existing_snapshot_replaces_atomically() {
    let (temp, root) = setup();
    let output = temp.path().join("snapshots");
    let mut value = snapshot(&root);
    for i in 0..MAX_RUNS {
        value.id = format!("{i:064x}");
        write_snapshot(&value, &output).unwrap();
    }
    value.sequence += 1;
    let path = write_snapshot(&value, &output).unwrap();
    let saved: Snapshot = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    assert_eq!(saved.sequence, 1);
    value.id = "f".repeat(64);
    assert!(write_snapshot(&value, &output).is_err());
    assert_eq!(fs::read_dir(&output).unwrap().count(), MAX_RUNS);
}

#[test]
fn countdown_moves_during_request_and_stopped_state_has_no_live_eta() {
    let (temp, root) = setup();
    let mut monitor = Monitor::new(snapshot(&root), &temp.path().join("absent.sock"));
    monitor.reserve(0, &progress(1, 1000));
    let before = monitor.snapshot.budgets.runtime_remaining_ms;
    std::thread::sleep(Duration::from_millis(25));
    monitor.emit(true);
    assert!(monitor.snapshot.budgets.runtime_remaining_ms < before);
    monitor.snapshot.traffic.download_eta_ms = Some(500);
    monitor.snapshot.traffic.eta_scope = Some("SELECTION".into());
    monitor.failed(0, "SHORT_BODY");
    assert!(monitor.snapshot.traffic.download_eta_ms.is_none());
    assert!(monitor.snapshot.traffic.eta_scope.is_none());
    let mut resumed = snapshot(&root);
    resumed.traffic.attempts = 1;
    resumed.traffic.received_bytes = 500;
    let resumed = Monitor::new(resumed, &temp.path().join("absent.sock"));
    assert_eq!(
        resumed.snapshot.traffic.received_basis,
        "DURABLE_LOWER_BOUND"
    );
}
