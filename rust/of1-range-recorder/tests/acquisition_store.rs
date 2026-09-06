//! Offline typed metadata/payload durability parity. Synthetic bytes are never source observations.
use of1_range_recorder::{
    FormatSource, RECORD_BYTES, SLOTS_PER_EPOCH,
    acquisition::{derive_payload_from_metadata, verify_payload},
    acquisition_http::parse_response_head,
    car::VerificationLimits,
    durable::{
        Clock, ClockSample, FaultPoint, RetryComparison, StoreError,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, PayloadLease, PreparedPayload, Request, RequestKind, SEGMENT_BYTES,
            StageBudget, current_executable_sha256, metadata_proposal_sha256, metadata_requests,
            payload_proposal_sha256,
        },
    },
    sha256,
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
impl TestClock {
    fn advance(&self, ms: u64) {
        let mut c = self.0.lock().unwrap();
        c.wall_ms += ms;
        c.boot_ms += ms;
    }
}

struct Harness {
    _dir: TempDir,
    root: PathBuf,
    plan: AggregatePlan,
    lease: MetadataLease,
    clock: TestClock,
}
impl Harness {
    fn new() -> Self {
        let dir = tempdir().unwrap();
        Self {
            root: dir.path().join("run"),
            _dir: dir,
            plan: AggregatePlan {
                schema: AGGREGATE_SCHEMA.into(),
                epoch: 978,
                format_source: FormatSource::pinned(),
                code_sha: "a".repeat(40),
                toolchain_fingerprint: "b".repeat(64),
                executable_sha256: current_executable_sha256().unwrap(),
                budget: AggregateBudget {
                    max_slots: 128,
                    max_plan_bytes: 1_048_576,
                    max_requests: 16,
                    max_response_entity_bytes: 16 * 1024 * 1024,
                    max_total_response_entity_bytes: 128 * 1024 * 1024,
                    max_disk_bytes: 256 * 1024 * 1024,
                    required_free_disk_bytes: 512 * 1024 * 1024,
                    max_memory_bytes: 512 * 1024 * 1024,
                    max_runtime_ms: 1_800_000,
                    response_timeout_ms: 30_000,
                    request_retries: 2,
                },
            },
            lease: MetadataLease {
                schema: "OF1_METADATA_LEASE_1".into(),
                authority: Authority::Fixture,
                budget: StageBudget {
                    max_requests: 12,
                    max_response_entity_bytes_total: 15_576_576,
                    max_runtime_ms: 900_000,
                },
            },
            clock: TestClock(Arc::new(Mutex::new(ClockSample {
                wall_ms: 1_000_000,
                boot_ms: 100_000,
                boot_id: "fixture-boot".into(),
            }))),
        }
    }
    fn create(&self) -> AcquisitionStore<TestClock> {
        AcquisitionStore::create(
            &self.root,
            self.plan.clone(),
            self.lease.clone(),
            self.clock.clone(),
        )
        .unwrap()
    }
    fn resume(&self, hash: &str) -> Result<AcquisitionStore<TestClock>, StoreError> {
        AcquisitionStore::resume(&self.root, &self.plan, hash, self.clock.clone())
    }
}

fn index() -> Vec<u8> {
    let mut bytes = vec![0; usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap()];
    bytes[..8].copy_from_slice(&64u64.to_le_bytes());
    bytes[8..12].copy_from_slice(&32u32.to_le_bytes());
    bytes
}
fn cid() -> Vec<u8> {
    let mut bytes = vec![1u8, 0x71, 0x12, 32];
    bytes.extend([0; 32]);
    let alphabet = b"abcdefghijklmnopqrstuvwxyz234567";
    let mut result = vec![b'b'];
    let mut bits = 0u32;
    let mut n = 0;
    for byte in bytes {
        bits = (bits << 8) | u32::from(byte);
        n += 8;
        while n >= 5 {
            n -= 5;
            result.push(alphabet[((bits >> n) & 31) as usize]);
        }
    }
    if n > 0 {
        result.push(alphabet[((bits << (5 - n)) & 31) as usize]);
    }
    result.push(b'\n');
    result
}
fn bytes(request: &Request) -> Vec<u8> {
    match request.kind {
        RequestKind::Index => index(),
        RequestKind::CarSha256 => format!("{}  epoch-978.car\n", "0".repeat(64)).into_bytes(),
        RequestKind::CarCid => cid(),
        RequestKind::CarHead => Vec::new(),
        RequestKind::CarRange { .. } => vec![7; usize::try_from(request.allowance()).unwrap()],
    }
}
fn head(
    request: &Request,
    length: u64,
    etag: &str,
) -> of1_range_recorder::durable::acquisition::ResponseHead {
    let raw = match request.kind {
        RequestKind::CarRange {
            start,
            end_exclusive,
            total,
            ..
        } => format!(
            "HTTP/1.1 206 Partial Content\r\nContent-Length: {length}\r\nContent-Range: bytes {start}-{}/{total}\r\nETag: {etag}\r\n\r\n",
            end_exclusive - 1
        ),
        _ => format!("HTTP/1.1 200 OK\r\nContent-Length: {length}\r\nETag: {etag}\r\n\r\n"),
    };
    parse_response_head(raw.as_bytes(), request).unwrap()
}
fn complete(store: &mut AcquisitionStore<TestClock>, sequence: u64) {
    let request = store.request(sequence).unwrap().clone();
    let data = bytes(&request);
    let length = if matches!(request.kind, RequestKind::CarHead) {
        1024
    } else {
        data.len() as u64
    };
    let permit = store.reserve(sequence).unwrap();
    store
        .begin_stream(&permit, head(&request, length, "\"fixture\""))
        .unwrap();
    for part in data.chunks(SEGMENT_BYTES) {
        store.append_stream(&permit, part).unwrap();
    }
    store.finish_stream(permit).unwrap();
}
fn metadata(store: &mut AcquisitionStore<TestClock>) -> PreparedPayload {
    for sequence in 0..4 {
        complete(store, sequence);
    }
    let objects = (0..4)
        .map(|n| store.published(n).unwrap().unwrap())
        .collect::<Vec<_>>();
    derive_payload_from_metadata(store.aggregate_plan(), &objects, 422_496_000, 422_496_001)
        .unwrap()
}
fn payload_lease(prepared: &PreparedPayload) -> PayloadLease {
    PayloadLease {
        schema: "OF1_PAYLOAD_LEASE_1".into(),
        authority: Authority::Fixture,
        budget: StageBudget {
            max_requests: 4,
            max_response_entity_bytes_total: 96,
            max_runtime_ms: 900_000,
        },
        prepared_payload_sha256: prepared.sha256().unwrap(),
        metadata_receipt_sha256: prepared.metadata_receipt_sha256().into(),
    }
}

// Generated structural bytes only: enlarge the existing opaque DataFrame and
// rehash the dependent fixture links without editing any committed golden bytes.
fn car_spanning_request_boundary(boundary: usize) -> Vec<u8> {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../schemas/acquisition/of1/car-structural-fixture.json"
    ))
    .unwrap();
    let mut replacements: Vec<(Vec<u8>, Vec<u8>)> = Vec::new();
    let mut sections = Vec::new();
    for (number, node) in fixture["nodes"].as_array().unwrap().iter().enumerate() {
        let old_cid = hex::decode(node["cid_hex"].as_str().unwrap()).unwrap();
        let mut payload = if number == 0 {
            // [DataFrame, null checksum, null index, null total, opaque bytes].
            let mut bytes = vec![0x85, 6, 0xf6, 0xf6, 0xf6, 0x5a];
            let length = boundary + 1024;
            bytes.extend(u32::try_from(length).unwrap().to_be_bytes());
            bytes.resize(bytes.len() + length, 0x61);
            bytes
        } else {
            hex::decode(node["payload_hex"].as_str().unwrap()).unwrap()
        };
        for (before, after) in &replacements {
            if let Some(position) = payload.windows(before.len()).position(|v| v == before) {
                payload[position..position + before.len()].copy_from_slice(after);
            }
        }
        let mut new_cid = vec![1, 0x71, 0x12, 32];
        new_cid.extend(hex::decode(sha256(&payload)).unwrap());
        let mut length = u64::try_from(new_cid.len() + payload.len()).unwrap();
        loop {
            let byte = u8::try_from(length & 127).unwrap();
            length >>= 7;
            sections.push(byte | if length == 0 { 0 } else { 128 });
            if length == 0 {
                break;
            }
        }
        sections.extend(&new_cid);
        sections.extend(payload);
        replacements.push((old_cid, new_cid));
    }
    sections
}

fn publish_bytes(store: &mut AcquisitionStore<TestClock>, sequence: u64, data: &[u8], size: u64) {
    let request = store.request(sequence).unwrap().clone();
    let permit = store.reserve(sequence).unwrap();
    store
        .begin_stream(&permit, head(&request, size, "\"fixture\""))
        .unwrap();
    for segment in data.chunks(SEGMENT_BYTES) {
        store.append_stream(&permit, segment).unwrap();
    }
    store.finish_stream(permit).unwrap();
}

#[test]
fn admitted_raw_ranges_reassemble_a_car_section_across_restart_without_extra_fetches() {
    let mut h = Harness::new();
    let boundary = usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap();
    h.plan.budget.max_response_entity_bytes = u64::try_from(boundary).unwrap();
    let sections = car_spanning_request_boundary(boundary);
    assert!(sections.len() > boundary);
    let offset = 64_u64;
    let object_size = offset + u64::try_from(sections.len()).unwrap();
    let mut recorded_index = index();
    recorded_index[8..12].copy_from_slice(&u32::try_from(sections.len()).unwrap().to_le_bytes());
    let mut store = h.create();
    publish_bytes(&mut store, 0, &recorded_index, boundary as u64);
    for sequence in 1..=2 {
        let request = store.request(sequence).unwrap().clone();
        let data = bytes(&request);
        publish_bytes(&mut store, sequence, &data, data.len() as u64);
    }
    publish_bytes(&mut store, 3, &[], object_size);
    let objects = (0..4)
        .map(|sequence| store.published(sequence).unwrap().unwrap())
        .collect::<Vec<_>>();
    let prepared =
        derive_payload_from_metadata(store.aggregate_plan(), &objects, 422_496_000, 422_496_001)
            .unwrap();
    assert_eq!(prepared.requests().len(), 2);
    let mut lease = payload_lease(&prepared);
    lease.budget.max_requests = 6;
    lease.budget.max_response_entity_bytes_total = 3 * sections.len() as u64;
    store.admit_payload(lease, &prepared).unwrap();
    let lease_hash = store.current_lease_sha256().to_string();
    publish_bytes(&mut store, 4, &sections[..boundary], boundary as u64);
    assert_eq!(store.progress().unwrap().attempts_reserved, 5);
    drop(store);
    let mut store = h.resume(&lease_hash).unwrap();
    let limits = VerificationLimits {
        max_total_bytes: usize::try_from(h.plan.budget.max_total_response_entity_bytes).unwrap(),
        max_section_bytes: sections.len(),
        max_nodes: 4096,
        max_links: 16384,
    };
    assert!(verify_payload(&store, &prepared, limits).is_err());
    assert_eq!(store.progress().unwrap().attempts_reserved, 5);
    publish_bytes(
        &mut store,
        5,
        &sections[boundary..],
        (sections.len() - boundary) as u64,
    );
    // The request chunk cap is not a CAR section cap. No further request is needed.
    assert!(matches!(
        verify_payload(
            &store,
            &prepared,
            VerificationLimits { max_section_bytes: boundary, ..limits },
        ),
        Err(StoreError::Integrity(reason)) if reason == "CAR_VERIFICATION_LIMIT"
    ));
    let report = verify_payload(&store, &prepared, limits).unwrap();
    assert_eq!(report.slots.len(), 1);
    assert_eq!(report.slots[0].verified_nodes, 5);
    assert_eq!(report.slots[0].verified_links, 4);
    assert_eq!(report.slots[0].captured_section_bytes, sections.len());
    assert_eq!(report.root_to_slot_membership, "UNAVAILABLE");
    assert!(!report.whole_car_sha256_verified);
    assert_eq!(report.domain_counts, "UNAVAILABLE_NOT_DECODED_IN_B4");
    assert_eq!(store.progress().unwrap().attempts_reserved, 6);
    assert_eq!(store.progress().unwrap().published_requests, 6);
}

#[test]
fn fixed_inventory_has_no_car_entity_under_metadata_authority() {
    let inventory = metadata_requests();
    assert_eq!(
        inventory.iter().map(Request::allowance).collect::<Vec<_>>(),
        [5_184_000, 4096, 4096, 0]
    );
    assert_eq!(inventory[3].method(), "HEAD");
    assert_eq!(inventory[1].path(978), "/978/epoch-978.sha256");
    let h = Harness::new();
    let mut store = h.create();
    assert!(store.request(4).is_err());
    assert!(matches!(store.reserve(3), Err(StoreError::Identity)));
}

#[test]
fn metadata_bootstrap_publishes_exact_realistic_index_and_zero_entity_head() {
    let h = Harness::new();
    let mut store = h.create();
    let prepared = metadata(&mut store);
    let progress = store.progress().unwrap();
    assert_eq!(progress.attempts_reserved, 4);
    assert_eq!(progress.charged_entity_bytes, 5_192_192);
    assert!(progress.disk_charge_bytes < 16 * 1024 * 1024);
    assert_eq!(
        fs::read(store.published(0).unwrap().unwrap().raw_path).unwrap(),
        index()
    );
    let head = store.published(3).unwrap().unwrap();
    assert_eq!(head.receipt.response.content_length, 1024);
    assert_eq!(head.receipt.response_entity_bytes, 0);
    assert!(fs::read(head.raw_path).unwrap().is_empty());
    assert_eq!(
        prepared.metadata_receipt_sha256(),
        store.metadata_receipt_sha256().unwrap()
    );
    assert_eq!(progress.evidence, "Fixture");
    assert_eq!(progress.domain_counts, "UNAVAILABLE_NOT_DECODED_IN_B4");
    let hash = store.current_lease_sha256().to_owned();
    drop(store);
    assert_eq!(
        h.resume(&hash)
            .unwrap()
            .progress()
            .unwrap()
            .charged_entity_bytes,
        5_192_192
    );
}

#[test]
fn partial_index_retry_retains_charge_prefix_and_original_deadlines() {
    let h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    let before = store.progress().unwrap();
    let request = store.request(0).unwrap().clone();
    let p = store.reserve(0).unwrap();
    store
        .begin_stream(&p, head(&request, request.allowance(), "\"fixture\""))
        .unwrap();
    store.append_stream(&p, &index()[..17_001]).unwrap();
    store.abort_stream();
    drop(store);
    h.clock.advance(500);
    let mut store = h.resume(&hash).unwrap();
    complete(&mut store, 0);
    let published = store.published(0).unwrap().unwrap();
    assert_eq!(
        published.receipt.retry_comparison,
        RetryComparison::RetainedOverlapMatched
    );
    let after = store.progress().unwrap();
    assert_eq!(after.attempts_reserved, 2);
    assert_eq!(after.charged_entity_bytes, 10_368_000);
    assert_eq!(after.deadline_wall_ms, before.deadline_wall_ms);
    assert_eq!(after.unreceipted_response_entity_bytes, None);
    assert!(
        h.root
            .join("pending/stream-0000000000/segment-0000000000/raw.bin")
            .exists()
    );
}

#[test]
fn stage_review_gap_does_not_renew_metadata_or_refund_shared_charges() {
    let h = Harness::new();
    let mut store = h.create();
    let prepared = metadata(&mut store);
    let metadata_hash = store.current_lease_sha256().to_owned();
    drop(store);
    h.clock.advance(950_000);
    let mut store = h.resume(&metadata_hash).unwrap();
    store
        .admit_payload(payload_lease(&prepared), &prepared)
        .unwrap();
    assert!(matches!(
        store.request(4).unwrap().kind,
        RequestKind::CarRange { .. }
    ));
    let hash = store.current_lease_sha256().to_owned();
    complete(&mut store, 4);
    let progress = store.progress().unwrap();
    assert_eq!(progress.attempts_reserved, 5);
    assert_eq!(progress.charged_entity_bytes, 5_192_224);
    assert_eq!(progress.stage, "PAYLOAD");
    drop(store);
    let mut store = h.resume(&hash).unwrap();
    assert!(matches!(
        store.reserve(0),
        Err(StoreError::AlreadyPublished)
    ));
}

#[test]
fn unused_metadata_retry_allowance_is_not_spent_but_prior_attempts_never_refund() {
    let h = Harness::new();
    let mut store = h.create();
    let prepared = metadata(&mut store);
    let mut lease = payload_lease(&prepared);
    lease.budget.max_requests = 12;
    store.admit_payload(lease, &prepared).unwrap();
    assert_eq!(store.progress().unwrap().attempts_reserved, 4);
    let h = Harness::new();
    let mut store = h.create();
    let prepared = metadata(&mut store);
    let mut lease = payload_lease(&prepared);
    lease.budget.max_requests = 13;
    assert!(matches!(
        store.admit_payload(lease, &prepared),
        Err(StoreError::Budget)
    ));
}

#[test]
fn fixture_metadata_cannot_be_promoted_by_approved_payload_authority() {
    let h = Harness::new();
    let mut store = h.create();
    let prepared = metadata(&mut store);
    let mut lease = payload_lease(&prepared);
    lease.authority = Authority::Approved {
        approval_id: "not-a-live-go".into(),
        operator: "offline-test".into(),
        approved_at_ms: 999_000,
        not_after_ms: 2_000_000,
        approved_plan_sha256: payload_proposal_sha256(&h.plan, &lease.budget, &prepared).unwrap(),
        cost_confirmation: "CONFIRMED_NO_CREDIT_SPEND".into(),
    };
    assert!(matches!(
        store.admit_payload(lease, &prepared),
        Err(StoreError::Identity)
    ));
}

#[test]
fn authentic_authority_requires_exact_proposal_hash_and_live_time_boundary() {
    let mut h = Harness::new();
    h.lease.authority = Authority::Approved {
        approval_id: "offline-admission-test-only".into(),
        operator: "offline-test".into(),
        approved_at_ms: 999_000,
        not_after_ms: 1_001_000,
        approved_plan_sha256: "0".repeat(64),
        cost_confirmation: "CONFIRMED_NO_CREDIT_SPEND".into(),
    };
    assert!(matches!(
        AcquisitionStore::create(&h.root, h.plan.clone(), h.lease.clone(), h.clock.clone()),
        Err(StoreError::Identity)
    ));
    let digest = metadata_proposal_sha256(&h.plan, &h.lease.budget).unwrap();
    if let Authority::Approved {
        approved_plan_sha256,
        ..
    } = &mut h.lease.authority
    {
        *approved_plan_sha256 = digest;
    }
    let mut store = h.create();
    let p = store.reserve(0).unwrap();
    assert!(store.network_authorized(&p).is_ok()); // No connector or network call is constructed.
    h.clock.advance(1000);
    assert!(matches!(store.remaining_ms(&p), Err(StoreError::Deadline)));
}

#[test]
fn forged_prepared_ranges_and_metadata_hash_cannot_be_admitted() {
    let h = Harness::new();
    let mut store = h.create();
    let prepared = metadata(&mut store);
    let mut json = serde_json::to_value(&prepared).unwrap();
    json["requests"][0]["kind"]["start"] = serde_json::json!(65);
    // Actual tagged enum shape is checked below, not assumed from display formatting.
    json["requests"][0]["kind"] = serde_json::json!({"kind":"CAR_RANGE","slot":422_496_000,"start":65,"end_exclusive":96,"total":1024,"strong_etag":"\"fixture\""});
    let forged: PreparedPayload = serde_json::from_value(json).unwrap();
    assert!(matches!(
        store.admit_payload(payload_lease(&forged), &forged),
        Err(StoreError::Identity)
    ));
}

#[test]
fn bootstrap_index_and_every_receipt_hash_are_revalidated_on_resume() {
    let h = Harness::new();
    let mut store = h.create();
    complete(&mut store, 0);
    let hash = store.current_lease_sha256().to_owned();
    let path = store.published(0).unwrap().unwrap().raw_path;
    drop(store);
    let mut raw = fs::read(&path).unwrap();
    raw[20] = 1;
    fs::write(path, raw).unwrap();
    assert!(matches!(h.resume(&hash), Err(StoreError::Corrupt)));
}

#[test]
fn attempts_expiration_clock_rollback_and_changed_executable_fail_closed() {
    let mut h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    let permit = store.reserve(0).unwrap();
    h.clock.advance(30_000);
    assert!(matches!(
        store.remaining_ms(&permit),
        Err(StoreError::Deadline)
    ));
    drop(store);
    let mut store = h.resume(&hash).unwrap();
    assert_eq!(store.progress().unwrap().attempts_reserved, 1);
    let _retry = store.reserve(0).unwrap();
    drop(store);
    h.clock.0.lock().unwrap().wall_ms -= 1;
    assert!(matches!(h.resume(&hash), Err(StoreError::Clock)));
    h.clock.advance(2);
    h.plan.executable_sha256 = "0".repeat(64);
    assert!(matches!(h.resume(&hash), Err(StoreError::Identity)));
}

#[test]
fn missing_canonical_reservation_is_ambiguous_not_a_fresh_attempt() {
    let h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    store.inject_fault(FaultPoint::BeforeReservationPublish);
    assert!(matches!(store.reserve(0), Err(StoreError::Injected(_))));
    drop(store);
    assert!(matches!(h.resume(&hash), Err(StoreError::Corrupt)));
}

#[test]
fn after_reservation_crash_keeps_the_exact_durable_charge() {
    let h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    store.inject_fault(FaultPoint::AfterReservationPublish);
    assert!(matches!(store.reserve(0), Err(StoreError::Injected(_))));
    assert_eq!(store.progress().unwrap().attempts_reserved, 1);
    drop(store);
    let store = h.resume(&hash).unwrap();
    assert_eq!(store.progress().unwrap().charged_entity_bytes, 5_184_000);
}

#[test]
fn source_validator_and_conflicting_retained_bytes_stop_across_restart() {
    for changed_source in [false, true] {
        let h = Harness::new();
        let mut store = h.create();
        let hash = store.current_lease_sha256().to_owned();
        let request = store.request(0).unwrap().clone();
        let p = store.reserve(0).unwrap();
        store
            .begin_stream(&p, head(&request, request.allowance(), "\"a\""))
            .unwrap();
        store.append_stream(&p, &[1, 2, 3]).unwrap();
        store.abort_stream();
        drop(store);
        let mut store = h.resume(&hash).unwrap();
        let p = store.reserve(0).unwrap();
        if changed_source {
            assert!(matches!(
                store.begin_stream(&p, head(&request, request.allowance(), "\"b\"")),
                Err(StoreError::SourceDrift)
            ));
        } else {
            store
                .begin_stream(&p, head(&request, request.allowance(), "\"a\""))
                .unwrap();
            assert!(matches!(
                store.append_stream(&p, &[1, 9, 3]),
                Err(StoreError::ConflictingBytes)
            ));
        }
        drop(store);
        assert!(matches!(
            h.resume(&hash),
            Err(StoreError::SourceDrift | StoreError::ConflictingBytes)
        ));
    }
}

#[test]
fn partial_or_changed_sealed_segment_is_never_repaired() {
    let h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    let request = store.request(0).unwrap().clone();
    let p = store.reserve(0).unwrap();
    store
        .begin_stream(&p, head(&request, request.allowance(), "\"a\""))
        .unwrap();
    store.append_stream(&p, &[1, 2, 3]).unwrap();
    drop(store);
    fs::write(
        h.root
            .join("pending/stream-0000000000/segment-0000000000/raw.bin"),
        [9, 9, 9],
    )
    .unwrap();
    assert!(matches!(h.resume(&hash), Err(StoreError::Corrupt)));
}

#[test]
fn hard_attempt_and_disk_caps_do_not_refund_after_reopen() {
    let h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    for _ in 0..3 {
        let _permit = store.reserve(0).unwrap();
        drop(store);
        store = h.resume(&hash).unwrap();
    }
    assert!(matches!(store.reserve(0), Err(StoreError::Budget)));
    let mut h = Harness::new();
    h.plan.budget.max_disk_bytes = 1024 * 1024;
    let mut store = h.create();
    assert!(matches!(store.reserve(0), Err(StoreError::Budget)));
    assert_eq!(store.progress().unwrap().attempts_reserved, 0);
}

#[test]
fn rejection_headers_remain_auditable_but_never_publish_raw() {
    let h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    let p = store.reserve(0).unwrap();
    let raw = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n";
    store.reject_response(&p, raw, "HTTP_STATUS_404").unwrap();
    drop(store);
    let store = h.resume(&hash).unwrap();
    assert!(store.published(0).unwrap().is_none());
    assert_eq!(
        fs::read(h.root.join("pending/rejected-0000000000/headers.bin")).unwrap(),
        raw
    );
    assert_eq!(store.progress().unwrap().attempts_reserved, 1);
}

#[test]
fn publication_faults_preserve_atomic_pairs_and_never_replay_a_published_request() {
    for point in [
        FaultPoint::AfterRawWrite,
        FaultPoint::AfterRawSync,
        FaultPoint::AfterReceiptSync,
        FaultPoint::BeforePublish,
        FaultPoint::AfterPublish,
        FaultPoint::AfterPublishSync,
    ] {
        let h = Harness::new();
        let mut store = h.create();
        let hash = store.current_lease_sha256().to_owned();
        let request = store.request(0).unwrap().clone();
        let p = store.reserve(0).unwrap();
        store
            .begin_stream(&p, head(&request, request.allowance(), "\"fixture\""))
            .unwrap();
        for part in index().chunks(SEGMENT_BYTES) {
            store.append_stream(&p, part).unwrap();
        }
        store.inject_fault(point);
        assert!(
            matches!(store.finish_stream(p), Err(StoreError::Injected(found)) if found == point)
        );
        drop(store);
        let mut store = h.resume(&hash).unwrap();
        assert_eq!(store.progress().unwrap().attempts_reserved, 1);
        if matches!(
            point,
            FaultPoint::AfterPublish | FaultPoint::AfterPublishSync
        ) {
            assert!(store.published(0).unwrap().is_some());
            assert!(matches!(
                store.reserve(0),
                Err(StoreError::AlreadyPublished)
            ));
        } else {
            assert!(store.published(0).unwrap().is_none());
            assert!(h.root.join("pending/0000000000/raw.bin").exists());
        }
    }
}

#[test]
fn partial_sealing_or_missing_published_partner_is_not_implicitly_repaired() {
    let h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    let request = store.request(0).unwrap().clone();
    let p = store.reserve(0).unwrap();
    store
        .begin_stream(&p, head(&request, request.allowance(), "\"fixture\""))
        .unwrap();
    drop(store);
    fs::create_dir(
        h.root
            .join("pending/stream-0000000000/incomplete-0000000000"),
    )
    .unwrap();
    assert!(h.resume(&hash).is_err());

    let h = Harness::new();
    let mut store = h.create();
    complete(&mut store, 0);
    let hash = store.current_lease_sha256().to_owned();
    drop(store);
    fs::remove_file(h.root.join("published/0000000000/receipt.json")).unwrap();
    assert!(h.resume(&hash).is_err());
}

#[test]
fn no_stage_may_exceed_aggregate_runtime_and_expired_stage_cannot_restart() {
    let h = Harness::new();
    let mut store = h.create();
    let prepared = metadata(&mut store);
    let mut too_long = payload_lease(&prepared);
    too_long.budget.max_runtime_ms = 900_001;
    assert!(matches!(
        store.admit_payload(too_long, &prepared),
        Err(StoreError::Budget)
    ));
    let hash = store.current_lease_sha256().to_owned();
    drop(store);
    let mut store = h.resume(&hash).unwrap();
    store
        .admit_payload(payload_lease(&prepared), &prepared)
        .unwrap();
    let hash = store.current_lease_sha256().to_owned();
    drop(store);
    h.clock.advance(900_000);
    let mut store = h.resume(&hash).unwrap();
    assert!(matches!(store.reserve(4), Err(StoreError::Deadline)));
    assert_eq!(store.progress().unwrap().attempts_reserved, 4);
}

#[test]
fn explicit_resource_guards_deny_impossible_free_disk_and_memory_without_dispatch() {
    for disk in [true, false] {
        let mut h = Harness::new();
        if disk {
            h.plan.budget.required_free_disk_bytes = u64::MAX;
        } else {
            h.plan.budget.max_memory_bytes = 1024 * 1024;
        }
        assert!(matches!(
            AcquisitionStore::create(&h.root, h.plan, h.lease, h.clock),
            Err(StoreError::Budget)
        ));
        assert!(!h.root.exists());
    }
}

#[test]
fn lease_publication_crashes_cannot_replay_admission_or_reset_the_deadline() {
    for point in [
        FaultPoint::BeforeStagePublish,
        FaultPoint::AfterStagePublish,
    ] {
        let h = Harness::new();
        let mut store = h.create();
        let prepared = metadata(&mut store);
        let metadata_hash = store.current_lease_sha256().to_owned();
        let lease = payload_lease(&prepared);
        let payload_hash = of1_range_recorder::sha256(&serde_json::to_vec(&lease).unwrap());
        let start = h.clock.sample().unwrap();
        store.inject_fault(point);
        assert!(
            matches!(store.admit_payload(lease, &prepared), Err(StoreError::Injected(found)) if found == point)
        );
        drop(store);
        h.clock.advance(1000);
        if point == FaultPoint::BeforeStagePublish {
            assert!(matches!(h.resume(&metadata_hash), Err(StoreError::Corrupt)));
        } else {
            let mut store = h.resume(&payload_hash).unwrap();
            assert_eq!(
                store.progress().unwrap().deadline_wall_ms,
                start.wall_ms + 900_000
            );
            assert_eq!(store.progress().unwrap().attempts_reserved, 4);
            assert!(matches!(
                store.admit_payload(payload_lease(&prepared), &prepared),
                Err(StoreError::Identity)
            ));
        }
    }
}

#[test]
fn unrelated_metadata_etags_are_not_misclassified_as_same_object_drift() {
    let h = Harness::new();
    let mut store = h.create();
    for sequence in 0..4 {
        let request = store.request(sequence).unwrap().clone();
        let raw = bytes(&request);
        let length = if sequence == 3 {
            1024
        } else {
            raw.len() as u64
        };
        let permit = store.reserve(sequence).unwrap();
        store
            .begin_stream(
                &permit,
                head(&request, length, &format!("\"object-{sequence}\"")),
            )
            .unwrap();
        for part in raw.chunks(SEGMENT_BYTES) {
            store.append_stream(&permit, part).unwrap();
        }
        store.finish_stream(permit).unwrap();
    }
    assert_eq!(store.progress().unwrap().published_requests, 4);
}

#[test]
fn exclusive_writer_and_manifest_identity_hold_at_every_permit() {
    let h = Harness::new();
    let mut store = h.create();
    let hash = store.current_lease_sha256().to_owned();
    assert!(matches!(h.resume(&hash), Err(StoreError::Locked)));
    let manifest = h.root.join("run.json");
    let mut json: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
    json["plan"]["epoch"] = serde_json::json!(979);
    fs::write(manifest, serde_json::to_vec(&json).unwrap()).unwrap();
    assert!(matches!(store.reserve(0), Err(StoreError::Identity)));
}
