use of1_range_recorder::{
    OfflinePlan,
    durable::{Clock, ClockSample, FaultPoint, Response, Store, StoreError},
    fixture, sha256,
};
use std::{
    collections::VecDeque,
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use tempfile::{TempDir, tempdir};

#[derive(Clone)]
struct TestClock(Arc<Mutex<ClockSample>>);

impl TestClock {
    fn new() -> Self {
        Self(Arc::new(Mutex::new(ClockSample {
            wall_ms: 10_000,
            boot_ms: 2_000,
            boot_id: "fixture-boot-a".into(),
        })))
    }

    fn advance(&self, elapsed: u64) {
        let mut sample = self.0.lock().unwrap();
        sample.wall_ms += elapsed;
        sample.boot_ms += elapsed;
    }

    fn change(&self, change: impl FnOnce(&mut ClockSample)) {
        change(&mut self.0.lock().unwrap());
    }
}

impl Clock for TestClock {
    fn sample(&self) -> Result<ClockSample, StoreError> {
        Ok(self.0.lock().unwrap().clone())
    }
}

#[derive(Clone)]
struct ScriptClock(Arc<Mutex<VecDeque<ClockSample>>>);

impl ScriptClock {
    fn elapsed(values: &[u64]) -> Self {
        Self(Arc::new(Mutex::new(
            values
                .iter()
                .map(|elapsed| ClockSample {
                    wall_ms: 10_000 + elapsed,
                    boot_ms: 2_000 + elapsed,
                    boot_id: "fixture-boot-a".into(),
                })
                .collect(),
        )))
    }
}

impl Clock for ScriptClock {
    fn sample(&self) -> Result<ClockSample, StoreError> {
        self.0.lock().unwrap().pop_front().ok_or(StoreError::Clock)
    }
}

struct Harness {
    _directory: TempDir,
    root: PathBuf,
    index: PathBuf,
    plan: OfflinePlan,
    clock: TestClock,
}

impl Harness {
    fn new() -> Self {
        let directory = tempdir().unwrap();
        let root = directory.path().join("capture");
        let index = directory.path().join("index.raw");
        fs::write(&index, fixture::index_bytes().unwrap()).unwrap();
        let mut plan = fixture::plan();
        // One fixture slot has two 32-byte requests. A two-attempt total cap is valid.
        plan.end_slot = plan.start_slot + 1;
        plan.budget.max_requests = 6;
        plan.budget.max_total_response_entity_bytes = 192;
        plan.budget.request_retries = 2;
        plan.budget.max_runtime_ms = 1_000;
        plan.budget.response_timeout_ms = 100;
        Self {
            _directory: directory,
            root,
            index,
            plan,
            clock: TestClock::new(),
        }
    }

    fn create(&self) -> Store<TestClock> {
        Store::create(
            &self.root,
            self.plan.clone(),
            &self.index,
            self.clock.clone(),
        )
        .unwrap()
    }

    fn resume(&self) -> Result<Store<TestClock>, StoreError> {
        Store::resume(
            &self.root,
            self.plan.clone(),
            &self.index,
            self.clock.clone(),
        )
    }
}

fn response(sequence: u64) -> Response {
    Response {
        status: 206,
        start: 128 + sequence * 32,
        end_exclusive: 160 + sequence * 32,
        total: 1024,
    }
}

fn published_path(root: &Path, sequence: u64, name: &str) -> PathBuf {
    root.join("published")
        .join(format!("{sequence:010}"))
        .join(name)
}

fn publish(store: &mut Store<TestClock>, sequence: u64, byte: u8) {
    let permit = store.reserve(sequence).unwrap();
    store
        .commit(permit, response(sequence), &[byte; 32])
        .unwrap();
}

fn captured_tree(root: &Path) -> Vec<(PathBuf, Option<Vec<u8>>)> {
    fn visit(root: &Path, path: &Path, entries: &mut Vec<(PathBuf, Option<Vec<u8>>)>) {
        for entry in fs::read_dir(path).unwrap() {
            let entry = entry.unwrap();
            let path = entry.path();
            let relative = path.strip_prefix(root).unwrap().to_path_buf();
            if entry.file_type().unwrap().is_dir() {
                entries.push((relative, None));
                visit(root, &path, entries);
            } else {
                entries.push((relative, Some(fs::read(path).unwrap())));
            }
        }
    }
    let mut entries = Vec::new();
    visit(root, root, &mut entries);
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    entries
}

#[test]
fn publication_is_a_verified_immutable_raw_receipt_pair() {
    let h = Harness::new();
    let mut store = h.create();
    publish(&mut store, 0, 0xA5);
    let raw = fs::read(published_path(&h.root, 0, "raw.bin")).unwrap();
    let receipt = fs::read(published_path(&h.root, 0, "receipt.json")).unwrap();
    assert_eq!(store.published(0).unwrap().unwrap().bytes, raw);
    assert_eq!(raw, [0xA5; 32]);
    assert!(matches!(
        store.reserve(0),
        Err(StoreError::AlreadyPublished)
    ));
    drop(store);
    let mut resumed = h.resume().unwrap();
    assert!(matches!(
        resumed.reserve(0),
        Err(StoreError::AlreadyPublished)
    ));
    assert_eq!(
        fs::read(published_path(&h.root, 0, "raw.bin")).unwrap(),
        raw
    );
    assert_eq!(
        fs::read(published_path(&h.root, 0, "receipt.json")).unwrap(),
        receipt
    );
    let summary = resumed.summary().unwrap();
    assert_eq!(summary.attempts_reserved, 1);
    assert_eq!(summary.charged_entity_bytes, 32);
    assert_eq!(summary.verified_response_entity_bytes, 32);
    assert_eq!(summary.published_requests, 1);
    assert_eq!(summary.unpublished_attempts, 0);
}

#[test]
fn new_bytes_with_old_receipt_never_pass_published_or_resume_verification() {
    let h = Harness::new();
    let mut store = h.create();
    publish(&mut store, 0, 0xA5);
    fs::write(published_path(&h.root, 0, "raw.bin"), [0x5A; 32]).unwrap();
    assert!(store.published(0).is_err());
    drop(store);
    assert!(h.resume().is_err());
}

#[test]
fn receipt_from_another_range_cannot_authorize_correctly_hashed_bytes() {
    let h = Harness::new();
    let mut store = h.create();
    publish(&mut store, 0, 0xA5);
    publish(&mut store, 1, 0xA5);
    // Equal payload hashes do not make different source ranges interchangeable.
    fs::copy(
        published_path(&h.root, 0, "receipt.json"),
        published_path(&h.root, 1, "receipt.json"),
    )
    .unwrap();
    assert!(store.published(1).is_err());
    drop(store);
    assert!(h.resume().is_err());
}

#[test]
fn incomplete_or_unknown_receipt_fields_fail_closed() {
    for mutation in 0..3 {
        let h = Harness::new();
        let mut store = h.create();
        publish(&mut store, 0, 0xA5);
        let path = published_path(&h.root, 0, "receipt.json");
        let mut receipt: serde_json::Value =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        match mutation {
            0 => {
                receipt.as_object_mut().unwrap().remove("run_id");
            }
            1 => {
                receipt["unreviewed_override"] = true.into();
            }
            _ => {
                receipt["response_entity_bytes"] = 31.into();
            }
        }
        fs::write(path, serde_json::to_vec(&receipt).unwrap()).unwrap();
        assert!(store.published(0).is_err(), "mutation {mutation}");
        drop(store);
        assert!(h.resume().is_err(), "mutation {mutation}");
    }
}

#[test]
fn missing_published_raw_or_receipt_is_corruption_not_unavailable() {
    for name in ["raw.bin", "receipt.json"] {
        let h = Harness::new();
        let mut store = h.create();
        publish(&mut store, 0, 0xA5);
        fs::remove_file(published_path(&h.root, 0, name)).unwrap();
        assert!(store.published(0).is_err());
        drop(store);
        assert!(h.resume().is_err());
    }
}

#[test]
fn create_never_overwrites_and_only_one_writer_may_resume() {
    let h = Harness::new();
    let store = h.create();
    let manifest = fs::read(h.root.join("run.json")).unwrap();
    assert!(Store::create(&h.root, h.plan.clone(), &h.index, h.clock.clone()).is_err());
    assert!(matches!(h.resume(), Err(StoreError::Locked)));
    assert_eq!(fs::read(h.root.join("run.json")).unwrap(), manifest);
    drop(store);
    assert!(h.resume().is_ok());
}

#[test]
fn interrupted_attempts_survive_multiple_restarts_and_exhaust_exact_two_request_cap() {
    let mut h = Harness::new();
    h.plan.budget.max_requests = 2;
    h.plan.budget.max_total_response_entity_bytes = 64;
    h.plan.budget.request_retries = 3;
    let mut store = h.create();
    drop(store.reserve(0).unwrap());
    drop(store);
    let mut resumed = h.resume().unwrap();
    assert_eq!(resumed.summary().unwrap().attempts_reserved, 1);
    drop(resumed.reserve(0).unwrap());
    drop(resumed);
    let mut resumed = h.resume().unwrap();
    assert!(matches!(resumed.reserve(0), Err(StoreError::Budget)));
    assert!(matches!(resumed.reserve(1), Err(StoreError::Poisoned)));
    drop(resumed);
    let mut resumed = h.resume().unwrap();
    assert!(matches!(resumed.reserve(1), Err(StoreError::Budget)));
    drop(resumed);
    let resumed = h.resume().unwrap();
    let summary = resumed.summary().unwrap();
    assert_eq!(summary.attempts_reserved, 2);
    assert_eq!(summary.charged_entity_bytes, 64);
    assert_eq!(summary.verified_response_entity_bytes, 0);
    assert_eq!(summary.published_requests, 0);
    assert_eq!(summary.unpublished_attempts, 2);
}

#[test]
fn per_request_retry_budget_is_not_reset_or_transferred_on_resume() {
    let mut h = Harness::new();
    h.plan.budget.request_retries = 0;
    let mut store = h.create();
    drop(store.reserve(0).unwrap());
    drop(store);
    let mut resumed = h.resume().unwrap();
    assert!(matches!(resumed.reserve(0), Err(StoreError::Budget)));
    drop(resumed);
    let mut resumed = h.resume().unwrap();
    publish(&mut resumed, 1, 0xA5);
    assert_eq!(resumed.summary().unwrap().attempts_reserved, 2);
}

#[test]
fn charged_entity_cap_counts_interrupted_attempts_not_only_verified_bytes() {
    let mut h = Harness::new();
    h.plan.budget.max_total_response_entity_bytes = 64;
    let mut store = h.create();
    drop(store.reserve(0).unwrap());
    drop(store);
    let mut resumed = h.resume().unwrap();
    publish(&mut resumed, 0, 0xA5);
    assert!(matches!(resumed.reserve(1), Err(StoreError::Budget)));
    let summary = resumed.summary().unwrap();
    assert_eq!(summary.attempts_reserved, 2);
    assert_eq!(summary.charged_entity_bytes, 64);
    assert_eq!(summary.verified_response_entity_bytes, 32);
}

#[test]
fn invalid_response_spends_the_attempt_without_publishing_or_replaying_it() {
    for invalid in 0..6 {
        let h = Harness::new();
        let mut store = h.create();
        let permit = store.reserve(0).unwrap();
        let mut header = response(0);
        let mut bytes = vec![0xA5; 32];
        match invalid {
            0 => header.status = 200,
            1 => header.start += 1,
            2 => header.end_exclusive += 1,
            3 => header.total += 1,
            4 => {
                bytes.pop();
            }
            _ => bytes.push(1),
        }
        assert!(
            store.commit(permit, header, &bytes).is_err(),
            "invalid {invalid}"
        );
        drop(store);
        let resumed = h.resume().unwrap();
        let summary = resumed.summary().unwrap();
        assert_eq!(summary.attempts_reserved, 1);
        assert_eq!(summary.charged_entity_bytes, 32);
        assert_eq!(summary.published_requests, 0);
        assert!(resumed.published(0).unwrap().is_none());
    }
}

#[test]
fn permit_from_another_run_is_not_authority_to_publish() {
    let left = Harness::new();
    let right = Harness::new();
    let mut a = left.create();
    let mut b = right.create();
    let permit = a.reserve(0).unwrap();
    assert!(matches!(
        b.commit(permit, response(0), &[0xA5; 32]),
        Err(StoreError::Identity)
    ));
    assert!(!published_path(&right.root, 0, "raw.bin").exists());
}

#[test]
fn absolute_deadline_includes_downtime_and_rejects_equality() {
    let h = Harness::new();
    let store = h.create();
    let before = store.summary().unwrap();
    assert_eq!(before.deadline_wall_ms, 11_000);
    assert_eq!(before.deadline_boot_ms, 3_000);
    drop(store);
    h.clock.advance(900);
    let resumed = h.resume().unwrap();
    assert_eq!(
        resumed.summary().unwrap().deadline_wall_ms,
        before.deadline_wall_ms
    );
    drop(resumed);
    h.clock.advance(100);
    assert!(matches!(h.resume(), Err(StoreError::Deadline)));
}

#[test]
fn deadline_also_blocks_an_already_open_store_and_an_outstanding_permit() {
    let h = Harness::new();
    let mut store = h.create();
    let permit = store.reserve(0).unwrap();
    h.clock.advance(1_000);
    assert!(matches!(
        store.commit(permit, response(0), &[0xA5; 32]),
        Err(StoreError::Deadline)
    ));
    assert!(!published_path(&h.root, 0, "raw.bin").exists());
    let h = Harness::new();
    let mut store = h.create();
    h.clock.advance(1_000);
    assert!(matches!(store.reserve(0), Err(StoreError::Deadline)));
}

#[test]
fn response_timeout_is_bounded_by_original_reservation_not_restart_time() {
    let h = Harness::new();
    let mut store = h.create();
    let permit = store.reserve(0).unwrap();
    h.clock.advance(100);
    assert!(matches!(
        store.commit(permit, response(0), &[0xA5; 32]),
        Err(StoreError::Deadline)
    ));
    drop(store);
    let resumed = h.resume().unwrap();
    assert_eq!(resumed.summary().unwrap().attempts_reserved, 1);
    assert_eq!(resumed.summary().unwrap().deadline_wall_ms, 11_000);
}

#[test]
fn changed_boot_and_wall_or_monotonic_rollback_fail_closed() {
    for mode in 0..3 {
        let h = Harness::new();
        let mut store = h.create();
        h.clock.advance(200);
        drop(store.reserve(0).unwrap());
        drop(store);
        h.clock.change(|sample| match mode {
            0 => sample.boot_id = "fixture-boot-b".into(),
            1 => sample.wall_ms -= 1,
            _ => sample.boot_ms -= 1,
        });
        assert!(matches!(h.resume(), Err(StoreError::Clock)), "mode {mode}");
    }
}

#[test]
fn resume_rejects_changed_plan_source_index_code_and_toolchain_identity() {
    for field in [
        "source_fingerprint",
        "code_fingerprint",
        "toolchain_fingerprint",
        "index_sha256",
    ] {
        let h = Harness::new();
        drop(h.create());
        let mut plan = serde_json::to_value(&h.plan).unwrap();
        plan[field] = sha256(b"different identity").into();
        let changed: OfflinePlan = serde_json::from_value(plan).unwrap();
        assert!(
            Store::resume(&h.root, changed, &h.index, h.clock.clone()).is_err(),
            "{field}"
        );
    }
    let mut h = Harness::new();
    drop(h.create());
    h.plan.budget.max_requests += 1;
    assert!(h.resume().is_err());
    let h = Harness::new();
    drop(h.create());
    let mut bytes = fs::read(&h.index).unwrap();
    bytes[120] ^= 1;
    fs::write(&h.index, bytes).unwrap();
    assert!(h.resume().is_err());
}

#[test]
fn disk_budget_covers_metadata_not_only_index_and_payload_minimum() {
    let mut h = Harness::new();
    h.plan.budget.max_disk_bytes = 5_184_000 + 64;
    assert!(matches!(
        Store::create(&h.root, h.plan.clone(), &h.index, h.clock.clone()),
        Err(StoreError::Budget)
    ));
    assert!(!published_path(&h.root, 0, "raw.bin").exists());
}

#[test]
fn reservation_fault_boundaries_do_not_return_uncommitted_authority() {
    for (point, ambiguous) in [
        (FaultPoint::BeforeReservationPublish, true),
        (FaultPoint::AfterReservationPublish, false),
    ] {
        let h = Harness::new();
        let mut store = h.create();
        store.inject_fault(point);
        assert!(matches!(store.reserve(0), Err(StoreError::Injected(_))));
        assert!(matches!(store.reserve(1), Err(StoreError::Poisoned)));
        drop(store);
        if ambiguous {
            let before = captured_tree(&h.root);
            assert!(matches!(h.resume(), Err(StoreError::Corrupt)));
            assert!(matches!(
                Store::inspect(&h.root, h.plan.clone(), &h.index, h.clock.clone()),
                Err(StoreError::Corrupt)
            ));
            assert_eq!(captured_tree(&h.root), before);
            continue;
        }
        let resumed = h.resume().unwrap();
        let summary = resumed.summary().unwrap();
        assert_eq!(summary.attempts_reserved, 1);
        assert_eq!(summary.charged_entity_bytes, 32);
        assert_eq!(summary.published_requests, 0);
    }
}

#[test]
fn every_publication_fault_exposes_either_no_generation_or_one_exact_generation() {
    for (point, published) in [
        (FaultPoint::AfterRawWrite, false),
        (FaultPoint::AfterRawSync, false),
        (FaultPoint::AfterReceiptSync, false),
        (FaultPoint::BeforePublish, false),
        (FaultPoint::AfterPublish, true),
        (FaultPoint::AfterPublishSync, true),
    ] {
        let h = Harness::new();
        let mut store = h.create();
        let permit = store.reserve(0).unwrap();
        store.inject_fault(point);
        assert!(matches!(
            store.commit(permit, response(0), &[0xA5; 32]),
            Err(StoreError::Injected(_))
        ));
        assert!(matches!(store.reserve(1), Err(StoreError::Poisoned)));
        drop(store);
        let resumed = h.resume().unwrap();
        let summary = resumed.summary().unwrap();
        assert_eq!(summary.attempts_reserved, 1);
        assert_eq!(summary.charged_entity_bytes, 32);
        assert_eq!(summary.published_requests, u64::from(published));
        assert_eq!(summary.unpublished_attempts, u64::from(!published));
        match resumed.published(0).unwrap() {
            Some(object) => {
                assert!(published);
                assert_eq!(object.bytes, [0xA5; 32]);
            }
            None => assert!(!published),
        }
    }
}

#[test]
fn abandoned_raw_stage_is_retained_and_charged_after_successful_retry() {
    let h = Harness::new();
    let mut store = h.create();
    let permit = store.reserve(0).unwrap();
    store.inject_fault(FaultPoint::AfterRawSync);
    assert!(matches!(
        store.commit(permit, response(0), &[0xA5; 32]),
        Err(StoreError::Injected(_))
    ));
    let pending = h.root.join("pending/0000000000/raw.bin");
    assert_eq!(fs::read(&pending).unwrap(), [0xA5; 32]);
    drop(store);
    let mut resumed = h.resume().unwrap();
    publish(&mut resumed, 0, 0x5A);
    assert_eq!(fs::read(&pending).unwrap(), [0xA5; 32]);
    assert_eq!(resumed.published(0).unwrap().unwrap().bytes, [0x5A; 32]);
    let summary = resumed.summary().unwrap();
    assert_eq!(summary.attempts_reserved, 2);
    assert_eq!(summary.charged_entity_bytes, 64);
    assert_eq!(summary.verified_response_entity_bytes, 32);
    assert_eq!(summary.unpublished_attempts, 1);
}

#[test]
fn expired_run_remains_inspectable_but_cannot_resume_and_inspection_is_read_only() {
    let h = Harness::new();
    let mut store = h.create();
    publish(&mut store, 0, 0xA5);
    let summary = store.summary().unwrap();
    drop(store);
    h.clock.advance(1_000);
    let before = captured_tree(&h.root);
    let inspected = Store::inspect(&h.root, h.plan.clone(), &h.index, h.clock.clone()).unwrap();
    assert_eq!(inspected, summary);
    assert_eq!(captured_tree(&h.root), before);
    assert!(matches!(h.resume(), Err(StoreError::Deadline)));
    assert_eq!(captured_tree(&h.root), before);
}

#[test]
fn inspection_of_expired_corrupt_evidence_fails_without_repair_or_file_changes() {
    let h = Harness::new();
    let mut store = h.create();
    publish(&mut store, 0, 0xA5);
    drop(store);
    h.clock.advance(1_000);
    fs::write(published_path(&h.root, 0, "raw.bin"), [0x5A; 32]).unwrap();
    let before = captured_tree(&h.root);
    assert!(Store::inspect(&h.root, h.plan.clone(), &h.index, h.clock.clone()).is_err());
    assert_eq!(captured_tree(&h.root), before);
}

#[test]
fn a_published_receipt_never_survives_missing_or_corrupt_reservation_evidence() {
    for corrupt in [false, true] {
        let h = Harness::new();
        let mut store = h.create();
        publish(&mut store, 0, 0xA5);
        drop(store);
        let reservation = h.root.join("attempts/0000000000.json");
        if corrupt {
            fs::write(reservation, b"{\"attempt_id\":0}").unwrap();
        } else {
            fs::remove_file(reservation).unwrap();
        }
        let before = captured_tree(&h.root);
        assert!(h.resume().is_err());
        assert!(Store::inspect(&h.root, h.plan.clone(), &h.index, h.clock.clone()).is_err());
        assert_eq!(captured_tree(&h.root), before);
    }
}

#[test]
fn missing_last_reservation_cannot_refund_an_unpublished_attempt_from_its_pending_copy() {
    let h = Harness::new();
    let mut store = h.create();
    drop(store.reserve(0).unwrap());
    drop(store);
    assert!(h.root.join("pending/reserve-0000000000.json").is_file());
    fs::remove_file(h.root.join("attempts/0000000000.json")).unwrap();
    let before = captured_tree(&h.root);
    assert!(matches!(h.resume(), Err(StoreError::Corrupt)));
    assert!(matches!(
        Store::inspect(&h.root, h.plan.clone(), &h.index, h.clock.clone()),
        Err(StoreError::Corrupt)
    ));
    assert_eq!(captured_tree(&h.root), before);
}

#[test]
fn deadline_crossed_during_reservation_fsync_spends_attempt_without_returning_permit() {
    let h = Harness::new();
    // Creation and pre-publication reservation are timely; final post-fsync check expires.
    let clock = ScriptClock::elapsed(&[0, 0, 1_000]);
    let mut store = Store::create(&h.root, h.plan.clone(), &h.index, clock.clone()).unwrap();
    assert!(matches!(store.reserve(0), Err(StoreError::Deadline)));
    assert!(clock.0.lock().unwrap().is_empty());
    assert!(h.root.join("attempts/0000000000.json").is_file());
    assert!(!published_path(&h.root, 0, "raw.bin").exists());
    drop(store);
    let summary = Store::inspect(&h.root, h.plan.clone(), &h.index, h.clock.clone()).unwrap();
    assert_eq!(summary.attempts_reserved, 1);
    assert_eq!(summary.charged_entity_bytes, 32);
    assert_eq!(summary.published_requests, 0);
}

#[test]
fn deadline_crossed_during_final_audit_prevents_raw_receipt_publication() {
    let h = Harness::new();
    // Creation, reservation, post-fsync, acquisition and pre-audit checks pass. The final
    // post-audit clock crosses the original boundary before publication can occur.
    let clock = ScriptClock::elapsed(&[0, 0, 0, 0, 0, 1_000]);
    let mut store = Store::create(&h.root, h.plan.clone(), &h.index, clock.clone()).unwrap();
    let permit = store.reserve(0).unwrap();
    assert!(matches!(
        store.commit(permit, response(0), &[0xA5; 32]),
        Err(StoreError::Deadline)
    ));
    assert!(clock.0.lock().unwrap().is_empty());
    assert!(!published_path(&h.root, 0, "raw.bin").exists());
    assert!(h.root.join("pending/0000000000/receipt.json").is_file());
    drop(store);
    let summary = Store::inspect(&h.root, h.plan.clone(), &h.index, h.clock.clone()).unwrap();
    assert_eq!(summary.attempts_reserved, 1);
    assert_eq!(summary.charged_entity_bytes, 32);
    assert_eq!(summary.published_requests, 0);
}
