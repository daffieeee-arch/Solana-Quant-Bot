//! Synthetic clock/bytes only. No authority fixture here dispatches a network call.
use of1_range_recorder::{
    FormatSource, RECORD_BYTES, SLOTS_PER_EPOCH,
    acquisition_http::parse_response_head,
    clock_contract::{ApprovalAnchor, ClockPolicy, check_follows},
    durable::{
        Clock, ClockSample, StoreError,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, SEGMENT_BYTES, StageBudget, current_executable_sha256,
            metadata_proposal_sha256,
        },
    },
};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
};

#[derive(Clone)]
struct ManualClock(Arc<Mutex<ClockSample>>);
impl Clock for ManualClock {
    fn sample(&self) -> Result<ClockSample, StoreError> {
        Ok(self.0.lock().unwrap().clone())
    }
}
impl ManualClock {
    fn set(&self, wall_ms: u64, boot_ms: u64) {
        let mut at = self.0.lock().unwrap();
        at.wall_ms = wall_ms;
        at.boot_ms = boot_ms;
    }
}

struct Case {
    _temp: tempfile::TempDir,
    root: PathBuf,
    plan: AggregatePlan,
    lease: MetadataLease,
    clock: ManualClock,
}
impl Case {
    fn new(approved: bool) -> Self {
        let temp = tempfile::tempdir().unwrap();
        let at = ClockSample {
            wall_ms: 1_000_000,
            boot_ms: 100_000,
            boot_id: "sealed-clock-fixture".into(),
        };
        let policy = ClockPolicy::standard();
        let plan = AggregatePlan {
            schema: AGGREGATE_SCHEMA.into(),
            epoch: 978,
            format_source: FormatSource::pinned(),
            code_sha: "a".repeat(40),
            toolchain_fingerprint: "b".repeat(64),
            executable_sha256: current_executable_sha256().unwrap(),
            sample_identity: None,
            download_rate: None,
            clock_policy: Some(policy.clone()),
            budget: AggregateBudget {
                max_slots: 128,
                max_plan_bytes: 1_048_576,
                max_requests: 16,
                max_response_entity_bytes: 16 * 1024 * 1024,
                max_total_response_entity_bytes: 128 * 1024 * 1024,
                max_disk_bytes: 256 * 1024 * 1024,
                required_free_disk_bytes: 1,
                max_memory_bytes: 512 * 1024 * 1024,
                max_runtime_ms: 1_800_000,
                response_timeout_ms: 30_000,
                request_retries: 2,
            },
        };
        let budget = StageBudget {
            max_requests: 12,
            max_response_entity_bytes_total: 15_576_576,
            max_runtime_ms: 900_000,
        };
        let authority = if approved {
            Authority::Approved {
                approval_id: "SYNTHETIC_OFFLINE_CLOCK_TEST".into(),
                operator: "FIXTURE_NOT_OPERATOR_GO".into(),
                approved_at_ms: at.wall_ms,
                not_after_ms: at.wall_ms + 1_200_000,
                approved_plan_sha256: metadata_proposal_sha256(&plan, &budget).unwrap(),
                cost_confirmation: "CONFIRMED_NO_CREDIT_SPEND".into(),
                clock_anchor: Some(ApprovalAnchor::new(&policy, at.clone()).unwrap()),
            }
        } else {
            Authority::Fixture
        };
        Self {
            root: temp.path().join("run"),
            _temp: temp,
            plan,
            lease: MetadataLease {
                schema: "OF1_METADATA_LEASE_1".into(),
                authority,
                budget,
            },
            clock: ManualClock(Arc::new(Mutex::new(at))),
        }
    }
    fn create(&self) -> Result<AcquisitionStore<ManualClock>, StoreError> {
        AcquisitionStore::create(
            &self.root,
            self.plan.clone(),
            self.lease.clone(),
            self.clock.clone(),
        )
    }
    fn resume(&self, hash: &str) -> Result<AcquisitionStore<ManualClock>, StoreError> {
        AcquisitionStore::resume(&self.root, &self.plan, hash, self.clock.clone())
    }
}

fn begin(
    store: &mut AcquisitionStore<ManualClock>,
    seq: u64,
    length: u64,
) -> of1_range_recorder::durable::acquisition::Permit {
    let request = store.request(seq).unwrap().clone();
    let raw = format!("HTTP/1.1 200 OK\r\nContent-Length: {length}\r\n\r\n");
    let head = parse_response_head(raw.as_bytes(), &request).unwrap();
    let permit = store.reserve(seq).unwrap();
    store.begin_stream(&permit, head).unwrap();
    permit
}

#[test]
fn policy_anchor_windows_are_versioned_exact_and_overflow_checked() {
    let policy = ClockPolicy::standard();
    let at = ClockSample {
        wall_ms: 1,
        boot_ms: 0,
        boot_id: "boot".into(),
    };
    let anchor = ApprovalAnchor::new(&policy, at.clone()).unwrap();
    assert_eq!(anchor.initialize_by_boot_ms, 600_000);
    assert_eq!(anchor.expires_at_boot_ms, 1_200_000);
    anchor.validate(&policy, 1, 1_200_001).unwrap();
    for bad in [
        ClockSample {
            wall_ms: 0,
            ..at.clone()
        },
        ClockSample {
            wall_ms: u64::MAX,
            ..at.clone()
        },
        ClockSample {
            boot_ms: u64::MAX,
            ..at.clone()
        },
        ClockSample {
            boot_id: String::new(),
            ..at.clone()
        },
    ] {
        assert!(ApprovalAnchor::new(&policy, bad).is_err());
    }
    let mut bad = policy.clone();
    bad.initialization_window_ms = 0;
    assert!(bad.validate().is_err());
    bad = policy.clone();
    bad.version += 1;
    assert!(bad.validate().is_err());
    let mut forged = anchor.clone();
    forged.expires_at_boot_ms += 1;
    assert!(forged.validate(&policy, 1, 1_200_001).is_err());
    assert!(forged.admit_initialization(&policy, &at).is_err());
    let start = ClockSample {
        wall_ms: 0,
        boot_ms: 600_000,
        ..at.clone()
    };
    assert!(anchor.admit_initialization(&policy, &start).is_ok());
    assert!(
        anchor
            .admit_initialization(
                &policy,
                &ClockSample {
                    boot_ms: 600_001,
                    ..start
                }
            )
            .is_err()
    );
}

#[test]
fn policy_and_anchor_are_initial_plan_lease_and_restart_identity() {
    let mut case = Case::new(true);
    let modern = metadata_proposal_sha256(&case.plan, &case.lease.budget).unwrap();
    let mut legacy = case.plan.clone();
    legacy.clock_policy = None;
    assert_ne!(
        modern,
        metadata_proposal_sha256(&legacy, &case.lease.budget).unwrap()
    );
    let bytes = serde_json::to_vec(&legacy).unwrap();
    assert!(
        !String::from_utf8(bytes.clone())
            .unwrap()
            .contains("clock_policy")
    );
    assert_eq!(
        serde_json::to_vec(&serde_json::from_slice::<AggregatePlan>(&bytes).unwrap()).unwrap(),
        bytes
    );
    let store = case.create().unwrap();
    let lease = store.current_lease_sha256().to_owned();
    drop(store);
    case.plan.clock_policy = None;
    assert!(matches!(case.resume(&lease), Err(StoreError::Identity)));
}

#[test]
fn missing_or_mismatched_anchor_fails_before_creating_any_run() {
    for legacy_policy in [false, true] {
        let mut case = Case::new(true);
        if legacy_policy {
            case.plan.clock_policy = None;
        } else if let Authority::Approved { clock_anchor, .. } = &mut case.lease.authority {
            *clock_anchor = None;
        }
        assert!(case.create().is_err());
        assert!(!case.root.exists());
    }
    let case = Case::new(true);
    case.clock.set(999_998, 700_001);
    assert!(matches!(case.create(), Err(StoreError::Deadline)));
    assert!(!case.root.exists());
}

#[test]
fn utc_minus_two_is_raw_provenance_across_segments_publication_and_restart() {
    for approved in [false, true] {
        let case = Case::new(approved);
        let mut store = case.create().unwrap();
        let lease = store.current_lease_sha256().to_owned();
        let size = SLOTS_PER_EPOCH * RECORD_BYTES;
        let p = begin(&mut store, 0, size);
        case.clock.set(999_999, 100_010);
        store.append_stream(&p, &vec![0; SEGMENT_BYTES]).unwrap();
        case.clock.set(999_998, 100_020);
        let remainder = vec![0; usize::try_from(size).unwrap() - SEGMENT_BYTES];
        for chunk in remainder.chunks(SEGMENT_BYTES) {
            store.append_stream(&p, chunk).unwrap();
        }
        let receipt = store.finish_stream(p).unwrap();
        assert_eq!(receipt.acquired_at, case.clock.sample().unwrap());
        assert_eq!(receipt.acquired_at.wall_ms, 999_998);
        let hash = receipt.sha256.clone();
        drop(store);
        case.clock.set(999_900, 100_025);
        let mut store = case.resume(&lease).unwrap();
        assert_eq!(store.published(0).unwrap().unwrap().receipt.sha256, hash);
        let p = begin(&mut store, 1, 64);
        case.clock.set(999_800, 100_030);
        store
            .append_stream(
                &p,
                b"0000000000000000000000000000000000000000000000000000000000000000",
            )
            .unwrap();
        let receipt = store.finish_stream(p).unwrap();
        assert_eq!(receipt.acquired_at.wall_ms, 999_800);
        assert!(!case.root.join("pending/clock-fatal.json").exists());
    }
}

#[test]
fn forward_utc_jump_does_not_expire_or_extend_the_original_boot_deadline() {
    let case = Case::new(true);
    case.clock.set(2, 700_000);
    let mut store = case.create().unwrap();
    assert_eq!(store.progress().unwrap().deadline_boot_ms, 1_300_000);
    case.clock.set(u64::MAX, 1_299_999);
    let p = store.reserve(0).unwrap();
    assert_eq!(store.remaining_ms(&p).unwrap(), 1);
    case.clock.set(1, 1_300_000);
    assert!(matches!(store.remaining_ms(&p), Err(StoreError::Deadline)));
    let lease = store.current_lease_sha256().to_owned();
    assert!(
        case.root
            .join(format!("pending/stage-expired-{lease}.json"))
            .is_file()
    );
    drop(store);
    // Dropping the observed clock below the durable stop cannot revive expiry.
    case.clock.set(1, 1_299_999);
    assert!(matches!(case.resume(&lease), Err(StoreError::Clock)));
    case.clock.set(1, 1_300_001);
    assert!(matches!(case.resume(&lease), Err(StoreError::Clock)));
}

#[test]
fn real_downtime_and_suspend_consume_stage_time_without_a_deadline_reset() {
    let case = Case::new(false);
    let mut store = case.create().unwrap();
    let lease = store.current_lease_sha256().to_owned();
    let _p = store.reserve(0).unwrap();
    let deadline = store.progress().unwrap().deadline_boot_ms;
    drop(store);
    case.clock.set(2, 1_000_000);
    let mut store = case.resume(&lease).unwrap();
    assert_eq!(store.progress().unwrap().deadline_boot_ms, deadline);
    assert!(matches!(store.reserve(0), Err(StoreError::Deadline)));
    drop(store);
    let mut store = case.resume(&lease).unwrap();
    assert!(matches!(store.reserve(0), Err(StoreError::Deadline)));
    assert_eq!(store.progress().unwrap().attempts_reserved, 1);
}

#[test]
fn observed_boot_rollback_or_changed_boot_remains_terminal_after_restart() {
    for changed_boot in [false, true] {
        let case = Case::new(false);
        let mut store = case.create().unwrap();
        let lease = store.current_lease_sha256().to_owned();
        let p = store.reserve(0).unwrap();
        case.clock.set(1, 100_100);
        store.remaining_ms(&p).unwrap();
        if changed_boot {
            case.clock.0.lock().unwrap().boot_id = "another-boot".into();
        } else {
            case.clock.set(999_998, 100_099);
        }
        assert!(matches!(store.remaining_ms(&p), Err(StoreError::Clock)));
        drop(store);
        case.clock.0.lock().unwrap().boot_id = "sealed-clock-fixture".into();
        case.clock.set(1_000_000, 100_101);
        assert!(matches!(case.resume(&lease), Err(StoreError::Clock)));
        let marker: serde_json::Value =
            serde_json::from_slice(&fs::read(case.root.join("pending/clock-fatal.json")).unwrap())
                .unwrap();
        assert_eq!(marker["high_water"]["wall_ms"], 1);
        assert_eq!(marker["high_water"]["boot_ms"], 100_100);
    }
}

#[test]
fn timeout_does_not_revive_same_permit_but_one_charged_retry_is_permitted() {
    let case = Case::new(false);
    let mut store = case.create().unwrap();
    let lease = store.current_lease_sha256().to_owned();
    let p = store.reserve(0).unwrap();
    case.clock.set(2, 130_000);
    assert!(matches!(store.remaining_ms(&p), Err(StoreError::Deadline)));
    drop(store);
    let mut store = case.resume(&lease).unwrap();
    let retry = store.reserve(0).unwrap();
    assert_eq!(store.progress().unwrap().attempts_reserved, 2);
    assert_eq!(store.progress().unwrap().charged_entity_bytes, 10_368_000);
    assert!(matches!(store.remaining_ms(&p), Err(StoreError::Identity))); // Old permit is not the inflight attempt.
    assert_eq!(store.remaining_ms(&retry).unwrap(), 30_000);
    assert!(
        case.root
            .join("pending/attempt-expired-0000000000.json")
            .is_file()
    );
}

#[test]
fn legacy_wall_rollback_is_still_rejected_and_no_marker_is_added() {
    let mut case = Case::new(false);
    case.plan.clock_policy = None;
    let mut store = case.create().unwrap();
    let p = store.reserve(0).unwrap();
    case.clock.set(999_998, 100_001);
    assert!(matches!(store.remaining_ms(&p), Err(StoreError::Clock)));
    assert!(!case.root.join("pending/clock-fatal.json").exists());
    assert!(
        check_follows(
            None,
            &ClockSample {
                wall_ms: 5,
                boot_ms: 1,
                boot_id: "b".into()
            },
            &ClockSample {
                wall_ms: 3,
                boot_ms: 2,
                boot_id: "b".into()
            }
        )
        .is_err()
    );
}

#[test]
fn clock_policy_does_not_weaken_attempt_or_entity_caps() {
    let case = Case::new(false);
    let mut store = case.create().unwrap();
    let lease = store.current_lease_sha256().to_owned();
    for attempt in 0..3 {
        let _p = store.reserve(0).unwrap();
        drop(store);
        case.clock.set(900_000 - attempt, 100_001 + attempt);
        store = case.resume(&lease).unwrap();
    }
    assert!(matches!(store.reserve(0), Err(StoreError::Budget)));
    assert_eq!(store.progress().unwrap().attempts_reserved, 3);
    assert_eq!(store.progress().unwrap().charged_entity_bytes, 15_552_000);
}

#[test]
fn expired_metadata_is_inspectable_and_only_a_distinct_payload_lease_can_continue() {
    use of1_range_recorder::{
        acquisition::derive_payload_from_metadata, durable::acquisition::PayloadLease,
    };
    let case = Case::new(false);
    let mut store = case.create().unwrap();
    let mut index = vec![0; usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap()];
    index[..8].copy_from_slice(&64u64.to_le_bytes());
    index[8..12].copy_from_slice(&32u32.to_le_bytes());
    let objects = [
        index,
        b"0000000000000000000000000000000000000000000000000000000000000000".to_vec(),
        b"bafyreielu2mwxw6ymjjfmwxgfk72evqfx4d4cly2z7mbg52bosbwzdlaqe\n".to_vec(),
        vec![],
    ];
    for (seq, raw) in objects.iter().enumerate() {
        let p = begin(
            &mut store,
            seq as u64,
            if seq == 3 { 1024 } else { raw.len() as u64 },
        );
        for part in raw.chunks(SEGMENT_BYTES) {
            store.append_stream(&p, part).unwrap();
        }
        store.finish_stream(p).unwrap();
    }
    let published = (0..4)
        .map(|seq| store.published(seq).unwrap().unwrap())
        .collect::<Vec<_>>();
    let prepared =
        derive_payload_from_metadata(&case.plan, &published, 422_496_000, 422_496_001).unwrap();
    let old_lease = store.current_lease_sha256().to_owned();
    let manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(case.root.join("run.json")).unwrap()).unwrap();
    drop(store);
    case.clock.set(2, 1_000_000);
    // Sealed synthetic terminal-observation fixture: all four publications
    // preceded this stage-expiry sample. It does not modify real run evidence.
    let marker = serde_json::json!({"schema":"OF1_CLOCK_STOP_1","run_id":manifest["run_id"],"aggregate_sha256":manifest["aggregate_sha256"],
        "lease_sha256":old_lease,"kind":"STAGE_DEADLINE","attempt_id":null,"high_water":case.clock.sample().unwrap(),"observed_at":case.clock.sample().unwrap()});
    let marker_path = case
        .root
        .join(format!("pending/stage-expired-{old_lease}.json"));
    fs::write(&marker_path, serde_json::to_vec(&marker).unwrap()).unwrap();
    let mut store = case.resume(&old_lease).unwrap();
    assert_eq!(store.progress().unwrap().published_requests, 4);
    let lease = PayloadLease {
        schema: "OF1_PAYLOAD_LEASE_1".into(),
        authority: Authority::Fixture,
        budget: StageBudget {
            max_requests: 3,
            max_response_entity_bytes_total: 96,
            max_runtime_ms: 900_000,
        },
        prepared_payload_sha256: prepared.sha256().unwrap(),
        metadata_receipt_sha256: prepared.metadata_receipt_sha256().into(),
    };
    store.admit_payload(lease, &prepared).unwrap();
    assert_ne!(store.current_lease_sha256(), old_lease);
    let _p = store.reserve(4).unwrap();
    assert_eq!(store.progress().unwrap().attempts_reserved, 5);
    assert_eq!(
        fs::read(&marker_path).unwrap(),
        serde_json::to_vec(&marker).unwrap()
    );
}

#[test]
fn corrupted_or_foreign_terminal_marker_never_reopens_dispatch() {
    for corruption in ["lease_sha256", "run_id", "high_water", "truncated"] {
        let case = Case::new(false);
        let mut store = case.create().unwrap();
        let lease = store.current_lease_sha256().to_owned();
        let p = store.reserve(0).unwrap();
        case.clock.set(2, 1_000_000);
        assert!(matches!(store.remaining_ms(&p), Err(StoreError::Deadline)));
        drop(store);
        let path = case
            .root
            .join(format!("pending/stage-expired-{lease}.json"));
        let mut marker: serde_json::Value =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        if corruption == "truncated" {
            fs::write(&path, b"{").unwrap();
        } else {
            marker[corruption] = serde_json::json!("wrong");
            fs::write(&path, serde_json::to_vec(&marker).unwrap()).unwrap();
        }
        assert!(case.resume(&lease).is_err());
    }
}

#[test]
fn failed_stop_persistence_poisoned_process_and_torn_marker_fail_closed() {
    let case = Case::new(false);
    let mut store = case.create().unwrap();
    let lease = store.current_lease_sha256().to_owned();
    let permit = store.reserve(0).unwrap();
    // Isolated fixture collision simulates a failed create-new stop write,
    // without filling a real disk or modifying acquisition evidence.
    fs::create_dir(
        case.root
            .join(format!("pending/stage-expired-{lease}.json")),
    )
    .unwrap();
    case.clock.set(2, 1_000_000);
    assert!(matches!(
        store.remaining_ms(&permit),
        Err(StoreError::Io(_))
    ));
    case.clock.set(1_000_000, 100_001);
    assert!(matches!(
        store.remaining_ms(&permit),
        Err(StoreError::Poisoned)
    ));
    drop(store);
    assert!(case.resume(&lease).is_err());
}
