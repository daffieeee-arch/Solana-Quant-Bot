//! Sealed byte-stream storage tests. No listener, endpoint or provider is constructed here.
use of1_range_recorder::{
    OfflinePlan,
    durable::{Clock, ClockSample, Response, RetryComparison, Store, StoreError, StreamHead},
    fixture,
};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
};
use tempfile::{TempDir, tempdir};

#[derive(Clone)]
struct TestClock(Arc<Mutex<ClockSample>>);

impl Clock for TestClock {
    fn sample(&self) -> Result<ClockSample, StoreError> {
        Ok(self.0.lock().unwrap().clone())
    }
}

struct Harness {
    _dir: TempDir,
    root: PathBuf,
    index: PathBuf,
    plan: OfflinePlan,
    clock: TestClock,
}

impl Harness {
    fn new() -> Self {
        let dir = tempdir().unwrap();
        let root = dir.path().join("capture");
        let index = dir.path().join("index.raw");
        fs::write(&index, fixture::index_bytes().unwrap()).unwrap();
        let mut plan = fixture::plan();
        plan.end_slot = plan.start_slot + 1;
        plan.budget.max_requests = 6;
        plan.budget.request_retries = 2;
        plan.budget.max_total_response_entity_bytes = 192;
        Self {
            _dir: dir,
            root,
            index,
            plan,
            clock: TestClock(Arc::new(Mutex::new(ClockSample {
                wall_ms: 10_000,
                boot_ms: 2_000,
                boot_id: "fixture-boot".into(),
            }))),
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

    fn interrupted(&self, etag: Option<&str>, bytes: &[u8]) {
        let mut store = self.create();
        let permit = store.reserve(0).unwrap();
        store.begin_stream(&permit, head(0, etag)).unwrap();
        if !bytes.is_empty() {
            store.append_stream(&permit, bytes).unwrap();
        }
        store.abort_stream();
    }
}

fn head(sequence: u64, etag: Option<&str>) -> StreamHead {
    StreamHead {
        response: Response {
            status: 206,
            start: 128 + sequence * 32,
            end_exclusive: 160 + sequence * 32,
            total: 1024,
        },
        strong_etag: etag.map(str::to_owned),
    }
}

#[test]
fn interrupted_prefix_is_retained_compared_and_published_after_restart() {
    let h = Harness::new();
    h.interrupted(Some("\"fixture-a\""), &[7; 13]);
    let mut store = h.resume().unwrap();
    let before = store.summary().unwrap();
    let permit = store.reserve(0).unwrap();
    store
        .begin_stream(&permit, head(0, Some("\"fixture-a\"")))
        .unwrap();
    store.append_stream(&permit, &[7; 9]).unwrap();
    store.append_stream(&permit, &[7; 23]).unwrap();
    let receipt = store.finish_stream(permit).unwrap();
    assert_eq!(
        receipt.retry_comparison,
        Some(RetryComparison::RetainedOverlapMatched)
    );
    assert_eq!(receipt.stream_head, Some(head(0, Some("\"fixture-a\""))));
    assert_eq!(store.published(0).unwrap().unwrap().bytes, [7; 32]);
    let after = store.summary().unwrap();
    assert_eq!(
        (after.attempts_reserved, after.charged_entity_bytes),
        (2, 64)
    );
    assert_eq!(after.verified_response_entity_bytes, 32);
    assert_eq!(after.unreceipted_response_entity_bytes, None);
    assert_eq!(after.deadline_wall_ms, before.deadline_wall_ms);
    assert!(
        h.root
            .join("pending/stream-0000000000/chunk-0000000000/raw.bin")
            .exists()
    );
    drop(store);
    assert_eq!(
        h.resume().unwrap().published(0).unwrap().unwrap().bytes,
        [7; 32]
    );
}

#[test]
fn conflicting_prefix_quarantine_cannot_be_skipped_after_restart() {
    let h = Harness::new();
    h.interrupted(None, &[1; 8]);
    let mut store = h.resume().unwrap();
    let permit = store.reserve(0).unwrap();
    store.begin_stream(&permit, head(0, None)).unwrap();
    assert!(matches!(
        store.append_stream(&permit, &[2; 8]),
        Err(StoreError::ConflictingBytes)
    ));
    drop(store);
    assert!(matches!(h.resume(), Err(StoreError::ConflictingBytes)));
    assert!(h.root.join("pending/quarantine.json").exists());
    assert!(!h.root.join("published/0000000000").exists());
}

#[test]
fn changed_total_is_persistent_source_drift_even_without_previous_entity_bytes() {
    let h = Harness::new();
    let mut store = h.create();
    let permit = store.reserve(0).unwrap();
    let mut changed = head(0, None);
    changed.response.total = 2048;
    assert!(matches!(
        store.begin_stream(&permit, changed),
        Err(StoreError::SourceDrift)
    ));
    drop(store);
    assert!(matches!(h.resume(), Err(StoreError::SourceDrift)));
}

#[test]
fn validator_changes_or_disappearance_are_source_drift_across_ranges() {
    for replacement in [Some("\"fixture-b\""), None] {
        let h = Harness::new();
        h.interrupted(Some("\"fixture-a\""), &[3; 8]);
        let mut store = h.resume().unwrap();
        let permit = store.reserve(1).unwrap();
        assert!(matches!(
            store.begin_stream(&permit, head(1, replacement)),
            Err(StoreError::SourceDrift)
        ));
        drop(store);
        assert!(matches!(h.resume(), Err(StoreError::SourceDrift)));
    }
}

#[test]
fn no_retained_bytes_is_unavailable_comparison_not_synthetic_agreement() {
    let h = Harness::new();
    h.interrupted(None, &[]);
    let mut store = h.resume().unwrap();
    let permit = store.reserve(0).unwrap();
    store.begin_stream(&permit, head(0, None)).unwrap();
    store.append_stream(&permit, &[0xFF; 32]).unwrap();
    let receipt = store.finish_stream(permit).unwrap();
    assert_eq!(
        receipt.retry_comparison,
        Some(RetryComparison::NoPriorBytes)
    );
    assert_eq!(receipt.stream_head.unwrap().strong_etag, None);
}

#[test]
fn comparison_is_only_for_same_planned_range_not_unrelated_object_bytes() {
    let h = Harness::new();
    h.interrupted(None, &[4; 8]);
    let mut store = h.resume().unwrap();
    let permit = store.reserve(1).unwrap();
    store.begin_stream(&permit, head(1, None)).unwrap();
    store.append_stream(&permit, &[9; 32]).unwrap();
    assert_eq!(
        store.finish_stream(permit).unwrap().retry_comparison,
        Some(RetryComparison::NoPriorBytes)
    );
}

#[test]
fn raw_or_chunk_receipt_tampering_fails_resume_without_guessing() {
    for mutation in 0..3 {
        let h = Harness::new();
        h.interrupted(None, &[1; 8]);
        let path = h.root.join("pending/stream-0000000000/chunk-0000000000");
        match mutation {
            0 => fs::write(path.join("raw.bin"), [2; 8]).unwrap(),
            1 => fs::remove_file(path.join("chunk.json")).unwrap(),
            _ => fs::write(path.join("chunk.json"), b"{}").unwrap(),
        }
        assert!(h.resume().is_err());
    }
}

#[test]
fn incomplete_chunk_publication_is_terminal_not_repaired_or_refunded() {
    let h = Harness::new();
    h.interrupted(None, &[1; 8]);
    fs::create_dir(
        h.root
            .join("pending/stream-0000000000/incomplete-0000000001"),
    )
    .unwrap();
    assert!(matches!(h.resume(), Err(StoreError::Corrupt)));
    assert!(h.root.join("attempts/0000000000.json").exists());
}

#[test]
fn published_receipt_cannot_change_its_stream_head_or_comparison() {
    for field in ["stream_head", "retry_comparison", "strip_both"] {
        let h = Harness::new();
        let mut store = h.create();
        let permit = store.reserve(0).unwrap();
        store
            .begin_stream(&permit, head(0, Some("\"fixture-a\"")))
            .unwrap();
        store.append_stream(&permit, &[5; 32]).unwrap();
        store.finish_stream(permit).unwrap();
        let path = h.root.join("published/0000000000/receipt.json");
        let mut value: serde_json::Value =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        if field == "strip_both" {
            value.as_object_mut().unwrap().remove("stream_head");
            value.as_object_mut().unwrap().remove("retry_comparison");
        } else if field == "stream_head" {
            value[field]["strong_etag"] = serde_json::json!("\"wrong\"");
        } else {
            value[field] = serde_json::json!("RETAINED_OVERLAP_MATCHED");
        }
        fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(matches!(store.published(0), Err(StoreError::Corrupt)));
    }
}

#[test]
fn stream_timeout_and_chunk_observation_clock_survive_reopen() {
    let h = Harness::new();
    let mut store = h.create();
    let permit = store.reserve(0).unwrap();
    store.begin_stream(&permit, head(0, None)).unwrap();
    assert_eq!(store.remaining_ms(&permit).unwrap(), 1000);
    {
        let mut clock = h.clock.0.lock().unwrap();
        clock.wall_ms += 250;
        clock.boot_ms += 250;
    }
    store.append_stream(&permit, &[1; 8]).unwrap();
    assert_eq!(store.remaining_ms(&permit).unwrap(), 750);
    drop(store);
    {
        let mut clock = h.clock.0.lock().unwrap();
        clock.wall_ms -= 1;
        clock.boot_ms -= 1;
    }
    assert!(matches!(h.resume(), Err(StoreError::Clock)));
}

#[test]
fn deadline_failure_poisons_the_public_io_authority_even_if_clock_is_moved_back() {
    let h = Harness::new();
    let mut store = h.create();
    let permit = store.reserve(0).unwrap();
    {
        let mut clock = h.clock.0.lock().unwrap();
        clock.wall_ms += 1_000;
        clock.boot_ms += 1_000;
    }
    assert!(matches!(
        store.remaining_ms(&permit),
        Err(StoreError::Deadline)
    ));
    {
        let mut clock = h.clock.0.lock().unwrap();
        clock.wall_ms -= 1_000;
        clock.boot_ms -= 1_000;
    }
    assert!(matches!(
        store.remaining_ms(&permit),
        Err(StoreError::Poisoned)
    ));
    assert!(matches!(
        store.begin_stream(&permit, head(0, None)),
        Err(StoreError::Poisoned)
    ));
}

#[test]
fn unknown_or_weak_validator_and_wrong_range_do_not_start_a_stream() {
    for invalid in [
        Some("W/\"weak\""),
        Some("unquoted"),
        Some("\"bad\r\nvalue\""),
    ] {
        let h = Harness::new();
        let mut store = h.create();
        let permit = store.reserve(0).unwrap();
        assert!(matches!(
            store.begin_stream(&permit, head(0, invalid)),
            Err(StoreError::Corrupt)
        ));
        assert!(!h.root.join("pending/stream-0000000000").exists());
    }
    let h = Harness::new();
    let mut store = h.create();
    let permit = store.reserve(0).unwrap();
    assert!(matches!(
        store.begin_stream(&permit, head(1, None)),
        Err(StoreError::Corrupt)
    ));
}

#[test]
fn extra_or_truncated_entity_cannot_publish_a_pair() {
    for length in [31, 33] {
        let h = Harness::new();
        let mut store = h.create();
        let permit = store.reserve(0).unwrap();
        store.begin_stream(&permit, head(0, None)).unwrap();
        if length == 33 {
            assert!(matches!(
                store.append_stream(&permit, &vec![1; length]),
                Err(StoreError::Budget)
            ));
        } else {
            store.append_stream(&permit, &vec![1; length]).unwrap();
            assert!(matches!(
                store.finish_stream(permit),
                Err(StoreError::Corrupt)
            ));
        }
        assert!(!h.root.join("published/0000000000").exists());
    }
}

#[test]
fn pre_read_disk_check_stops_before_consuming_another_entity_chunk() {
    let mut h = Harness::new();
    h.plan.budget.max_disk_bytes = 5_184_000 + 120_000;
    let mut store = h.create();
    let permit = store.reserve(0).unwrap();
    store.begin_stream(&permit, head(0, None)).unwrap();
    let mut retained = 0;
    while store.prepare_stream_read(&permit, 1).is_ok() {
        store.append_stream(&permit, &[7]).unwrap();
        retained += 1;
        assert!(retained < 32);
    }
    assert!(retained > 0);
    assert!(matches!(
        store.prepare_stream_read(&permit, 1),
        Err(StoreError::Poisoned)
    ));
    assert_eq!(store.summary().unwrap().charged_entity_bytes, 32);
    assert_eq!(store.summary().unwrap().verified_response_entity_bytes, 0);
}

#[test]
fn all_single_split_boundaries_preserve_same_range_overlap_and_raw_bytes() {
    for boundary in 1..32 {
        let h = Harness::new();
        let bytes: Vec<u8> = (0..32).collect();
        h.interrupted(None, &bytes[..boundary]);
        let mut store = h.resume().unwrap();
        let permit = store.reserve(0).unwrap();
        store.begin_stream(&permit, head(0, None)).unwrap();
        store
            .append_stream(&permit, &bytes[..32 - boundary])
            .unwrap();
        store
            .append_stream(&permit, &bytes[32 - boundary..])
            .unwrap();
        let receipt = store.finish_stream(permit).unwrap();
        assert_eq!(
            receipt.retry_comparison,
            Some(RetryComparison::RetainedOverlapMatched)
        );
        assert_eq!(store.published(0).unwrap().unwrap().bytes, bytes);
    }
}

#[test]
fn injected_commit_cannot_bypass_an_existing_stream_or_conflicting_prefix() {
    let h = Harness::new();
    h.interrupted(None, &[1; 8]);
    let mut store = h.resume().unwrap();
    let permit = store.reserve(0).unwrap();
    assert!(matches!(
        store.commit(permit, head(0, None).response, &[2; 32]),
        Err(StoreError::ConflictingBytes)
    ));
    drop(store);
    assert!(matches!(h.resume(), Err(StoreError::ConflictingBytes)));
}
