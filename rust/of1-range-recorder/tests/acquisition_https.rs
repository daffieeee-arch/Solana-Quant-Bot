#![cfg(feature = "tls-fixture")]

use of1_range_recorder::{
    FormatSource, RECORD_BYTES, SLOTS_PER_EPOCH,
    acquisition_http::{HeaderError, parse_response_head},
    durable::{
        Clock, StoreError, SystemClock,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, StageBudget, current_executable_sha256, metadata_proposal_sha256,
        },
    },
    https::{
        CaptureObserver, FixtureHttps, HttpsError, OF1_SERVER_NAME,
        fixture::{FixtureServer, ResponseScript},
    },
    rate::DownloadRate,
    sha256,
};
use std::{fs, time::Instant};

struct Measurements {
    events: Vec<&'static str>,
    bytes: u64,
    reads: u64,
    published_path: std::path::PathBuf,
}
impl CaptureObserver for Measurements {
    fn active(&self) -> bool {
        true
    }
    fn reserved(&mut self, _: u64, p: &of1_range_recorder::durable::acquisition::Progress) {
        assert_eq!(p.attempts_reserved, 1);
        assert_eq!(p.charged_entity_bytes, SLOTS_PER_EPOCH * RECORD_BYTES);
        self.events.push("RESERVED");
    }
    fn head(&mut self, _: u64, status: u16, _: u64) {
        assert_eq!(status, 200);
        self.events.push("HEAD");
    }
    fn received(&mut self, _: u64, bytes: u64) {
        assert!(
            !self.published_path.exists(),
            "measurement must precede publication"
        );
        self.reads += 1;
        self.bytes += bytes;
        self.events.push("READ");
    }
    fn verifying(&mut self, _: u64) {
        self.events.push("VERIFYING");
    }
    fn publishing(&mut self, _: u64) {
        assert!(!self.published_path.exists());
        self.events.push("PUBLISHING");
    }
    fn published(&mut self, _: u64, receipt: &of1_range_recorder::durable::acquisition::Receipt) {
        assert!(self.published_path.exists());
        assert_eq!(receipt.response_entity_bytes, self.bytes);
        self.events.push("PUBLISHED");
    }
    fn failed(&mut self, _: u64, _: &str) {
        self.events.push("FAILED");
    }
}

#[test]
fn paced_tls_reports_real_reads_before_verified_durable_publication() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("run");
    let (plan, lease) = plans(20_000);
    let mut store = AcquisitionStore::create(&root, plan, lease, SystemClock).unwrap();
    let bytes = vec![0; usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap()];
    let mut script = response(200, bytes.len(), bytes.clone(), true);
    script.fragment_bytes = 32_768;
    let server = FixtureServer::start_paced(OF1_SERVER_NAME, vec![script], 1).unwrap();
    let mut measured = Measurements {
        events: vec![],
        bytes: 0,
        reads: 0,
        published_path: root.join("published/0000000000/raw.bin"),
    };
    let receipt = FixtureHttps::new(server.port(), server.root_der())
        .unwrap()
        .capture_observed(&mut store, 0, &mut measured)
        .unwrap();
    server.finish().unwrap();
    assert!(measured.reads > 1);
    assert_eq!(measured.bytes, bytes.len() as u64);
    assert_eq!(&measured.events[..2], &["RESERVED", "HEAD"]);
    assert_eq!(
        &measured.events[measured.events.len() - 3..],
        &["VERIFYING", "PUBLISHING", "PUBLISHED"]
    );
    assert_eq!(receipt.sha256, sha256(&bytes));
    assert_eq!(store.progress().unwrap().attempts_reserved, 1);
}

#[test]
fn partial_response_reports_received_not_published_and_retains_charged_attempt() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("run");
    let (plan, lease) = plans(5000);
    let mut store = AcquisitionStore::create(&root, plan, lease, SystemClock).unwrap();
    let server = FixtureServer::start(
        OF1_SERVER_NAME,
        vec![response(
            200,
            usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap(),
            vec![1; 11],
            true,
        )],
    )
    .unwrap();
    let mut measured = Measurements {
        events: vec![],
        bytes: 0,
        reads: 0,
        published_path: root.join("published/0000000000/raw.bin"),
    };
    let result = FixtureHttps::new(server.port(), server.root_der())
        .unwrap()
        .capture_observed(&mut store, 0, &mut measured);
    server.finish().unwrap();
    assert!(matches!(result, Err(HttpsError::Truncated)));
    assert_eq!(measured.bytes, 11);
    assert_eq!(measured.events.last(), Some(&"FAILED"));
    assert!(!measured.events.contains(&"PUBLISHING"));
    assert!(!measured.published_path.exists());
    assert_eq!(
        store.progress().unwrap().charged_entity_bytes,
        SLOTS_PER_EPOCH * RECORD_BYTES
    );
}

fn plans(timeout: u64) -> (AggregatePlan, MetadataLease) {
    let plan = AggregatePlan {
        schema: AGGREGATE_SCHEMA.into(),
        sample_identity: None,
        download_rate: Some(DownloadRate::standard()),
        epoch: 978,
        format_source: FormatSource::pinned(),
        code_sha: "a".repeat(40),
        toolchain_fingerprint: sha256(b"FIXTURE_TOOLCHAIN"),
        executable_sha256: current_executable_sha256().unwrap(),
        budget: AggregateBudget {
            max_slots: 2,
            max_plan_bytes: 1_048_576,
            max_requests: 12,
            max_response_entity_bytes: 8 * 1024 * 1024,
            max_total_response_entity_bytes: 32 * 1024 * 1024,
            max_disk_bytes: 128 * 1024 * 1024,
            required_free_disk_bytes: 1,
            max_memory_bytes: 512 * 1024 * 1024,
            max_runtime_ms: 120_000,
            response_timeout_ms: timeout,
            request_retries: 2,
        },
    };
    let lease = MetadataLease {
        schema: "OF1_METADATA_LEASE_1".into(),
        authority: Authority::Fixture,
        budget: StageBudget {
            max_requests: 8,
            max_response_entity_bytes_total: 24 * 1024 * 1024,
            max_runtime_ms: 60_000,
        },
    };
    (plan, lease)
}

fn response(status: u16, declared: usize, body: Vec<u8>, close_notify: bool) -> ResponseScript {
    ResponseScript {
        header: format!(
            "HTTP/1.1 {status} OK\r\nContent-Length: {declared}\r\nETag: \"fixture-v1\"\r\n\r\n"
        )
        .into_bytes(),
        body,
        fragment_bytes: 65_536,
        delay_ms: 0,
        close_notify,
    }
}

#[test]
fn tls_metadata_inventory_preserves_exact_raw_headers_and_zero_entity_head_on_restart() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("run");
    let (plan, lease) = plans(20_000);
    let mut store = AcquisitionStore::create(&root, plan.clone(), lease, SystemClock).unwrap();
    let index_len = usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap();
    let index = vec![0; index_len];
    let sha = format!("{}  epoch-978.car\n", "a".repeat(64)).into_bytes();
    let cid = b"bafyreielu2mwxw6ymjjfmwxgfk72evqfx4d4cly2z7mbg52bosbwzdlaqe\n".to_vec();
    let server = FixtureServer::start(
        OF1_SERVER_NAME,
        vec![
            response(200, index_len, index.clone(), true),
            response(200, sha.len(), sha.clone(), false),
            response(200, cid.len(), cid.clone(), true),
            response(200, 500_000_000, vec![], false),
        ],
    )
    .unwrap();
    let connector = FixtureHttps::new(server.port(), server.root_der()).unwrap();
    for (sequence, expected) in [index, sha, cid, vec![]].into_iter().enumerate() {
        let sequence = sequence as u64;
        let receipt = connector.capture(&mut store, sequence).unwrap();
        assert_eq!(receipt.response_entity_bytes, expected.len() as u64);
        assert_eq!(receipt.sha256, sha256(&expected));
        assert_eq!(
            receipt.response_headers_sha256,
            sha256(&receipt.response.raw_headers)
        );
        assert_eq!(receipt.evidence, "Fixture");
        assert_eq!(receipt.domain_counts, "UNAVAILABLE_NOT_DECODED_IN_B4");
        let published = store.published(sequence).unwrap().unwrap();
        assert_eq!(fs::read(published.raw_path).unwrap(), expected);
        let lease_hash = store.current_lease_sha256().to_owned();
        drop(store);
        store = AcquisitionStore::resume(&root, &plan, &lease_hash, SystemClock).unwrap();
    }
    let requests = server.finish().unwrap();
    assert_eq!(requests.len(), 4);
    for (request, path) in requests.iter().zip([
        "GET /978/epoch-978-slot-ranges.raw",
        "GET /978/epoch-978.sha256",
        "GET /978/epoch-978.cid",
        "HEAD /978/epoch-978.car",
    ]) {
        assert!(request.starts_with(path));
        assert!(request.contains("Host: files.old-faithful.net\r\n"));
        assert!(request.contains("Accept-Encoding: identity\r\n"));
        assert!(!request.contains("Proxy"));
    }
    let progress = store.progress().unwrap();
    assert_eq!(progress.attempts_reserved, 4);
    assert_eq!(
        progress.charged_entity_bytes,
        SLOTS_PER_EPOCH * RECORD_BYTES + 8192
    );
    assert_eq!(progress.published_requests, 4);
    assert!(matches!(
        connector.capture(&mut store, 0),
        Err(HttpsError::Store(StoreError::AlreadyPublished))
    ));
}

#[test]
fn tls_certificate_hostname_failure_is_one_charged_attempt_and_no_http_publication() {
    let temp = tempfile::tempdir().unwrap();
    let (plan, lease) = plans(3000);
    let mut store =
        AcquisitionStore::create(&temp.path().join("run"), plan, lease, SystemClock).unwrap();
    let server =
        FixtureServer::start("wrong-host.invalid", vec![response(200, 1, vec![0], true)]).unwrap();
    let connector = FixtureHttps::new(server.port(), server.root_der()).unwrap();
    assert!(connector.capture(&mut store, 0).is_err());
    assert!(server.finish().is_err());
    assert_eq!(store.progress().unwrap().attempts_reserved, 1);
    assert_eq!(store.progress().unwrap().published_requests, 0);
}

#[test]
fn tls_truncation_retains_partial_bytes_and_resume_charges_one_new_explicit_attempt() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("run");
    let (plan, lease) = plans(20_000);
    let mut store = AcquisitionStore::create(&root, plan.clone(), lease, SystemClock).unwrap();
    let length = usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap();
    let server = FixtureServer::start(
        OF1_SERVER_NAME,
        vec![
            response(200, length, vec![0; 65_536], false),
            response(200, length, vec![0; length], true),
        ],
    )
    .unwrap();
    let connector = FixtureHttps::new(server.port(), server.root_der()).unwrap();
    assert!(matches!(
        connector.capture(&mut store, 0),
        Err(HttpsError::Truncated)
    ));
    assert_eq!(store.progress().unwrap().attempts_reserved, 1);
    assert_eq!(store.progress().unwrap().published_requests, 0);
    let lease_hash = store.current_lease_sha256().to_owned();
    drop(store);
    store = AcquisitionStore::resume(&root, &plan, &lease_hash, SystemClock).unwrap();
    connector.capture(&mut store, 0).unwrap();
    assert_eq!(server.finish().unwrap().len(), 2);
    let progress = store.progress().unwrap();
    assert_eq!(progress.attempts_reserved, 2);
    assert_eq!(
        progress.charged_entity_bytes,
        2 * SLOTS_PER_EPOCH * RECORD_BYTES
    );
    assert_eq!(progress.published_requests, 1);
}

#[test]
fn tls_response_deadline_does_not_replay_or_publish() {
    let temp = tempfile::tempdir().unwrap();
    let (plan, lease) = plans(75);
    let mut store =
        AcquisitionStore::create(&temp.path().join("run"), plan, lease, SystemClock).unwrap();
    let mut script = response(200, 5_184_000, vec![], true);
    script.delay_ms = 250;
    let server = FixtureServer::start(OF1_SERVER_NAME, vec![script]).unwrap();
    let connector = FixtureHttps::new(server.port(), server.root_der()).unwrap();
    let started = Instant::now();
    assert!(connector.capture(&mut store, 0).is_err());
    assert!(started.elapsed().as_secs() < 2);
    drop(server);
    assert_eq!(store.progress().unwrap().attempts_reserved, 1);
    assert_eq!(store.progress().unwrap().published_requests, 0);
}

#[test]
fn unexpected_status_length_or_compression_never_reads_an_entity() {
    for header in [
        "HTTP/1.1 302 Found\r\nContent-Length: 0\r\n\r\n",
        "HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n",
        "HTTP/1.1 200 OK\r\nContent-Length: 5184000\r\nContent-Encoding: gzip\r\n\r\n",
    ] {
        let temp = tempfile::tempdir().unwrap();
        let (plan, lease) = plans(3000);
        let mut store =
            AcquisitionStore::create(&temp.path().join("run"), plan, lease, SystemClock).unwrap();
        let mut script = response(200, 0, vec![], true);
        script.header = header.as_bytes().to_vec();
        let server = FixtureServer::start(OF1_SERVER_NAME, vec![script]).unwrap();
        let connector = FixtureHttps::new(server.port(), server.root_der()).unwrap();
        assert!(matches!(
            connector.capture(&mut store, 0),
            Err(HttpsError::Header(_))
        ));
        let _ = server.finish();
        assert_eq!(store.progress().unwrap().attempts_reserved, 1);
        assert_eq!(store.progress().unwrap().published_response_entity_bytes, 0);
        assert_eq!(
            fs::read(
                temp.path()
                    .join("run/pending/rejected-0000000000/headers.bin")
            )
            .unwrap(),
            header.as_bytes()
        );
    }
}

#[test]
fn approved_authority_is_rejected_by_fixture_before_socket_or_reservation() {
    let temp = tempfile::tempdir().unwrap();
    let (plan, mut lease) = plans(3000);
    let now = SystemClock.sample().unwrap().wall_ms;
    lease.authority = Authority::Approved {
        approval_id: "SYNTHETIC_NEGATIVE_TEST_NOT_A_LIVE_APPROVAL".into(),
        operator: "OFFLINE_TEST".into(),
        approved_at_ms: now,
        not_after_ms: now + 60_000,
        approved_plan_sha256: metadata_proposal_sha256(&plan, &lease.budget).unwrap(),
        cost_confirmation: "CONFIRMED_NO_CREDIT_SPEND".into(),
    };
    let mut store =
        AcquisitionStore::create(&temp.path().join("run"), plan, lease, SystemClock).unwrap();
    let server =
        FixtureServer::start(OF1_SERVER_NAME, vec![response(200, 1, vec![0], true)]).unwrap();
    let connector = FixtureHttps::new(server.port(), server.root_der()).unwrap();
    assert!(matches!(
        connector.capture(&mut store, 0),
        Err(HttpsError::Authority)
    ));
    assert_eq!(store.progress().unwrap().attempts_reserved, 0);
    drop(server);
}

#[cfg(feature = "network-of1")]
#[test]
fn official_authority_gate_rejects_fixture_before_dns_socket_or_reservation() {
    let temp = tempfile::tempdir().unwrap();
    let (plan, lease) = plans(3000);
    let mut store =
        AcquisitionStore::create(&temp.path().join("run"), plan, lease, SystemClock).unwrap();
    assert!(matches!(
        of1_range_recorder::https::OfficialHttps::capture(&mut store, 0),
        Err(HttpsError::Authority)
    ));
    assert_eq!(store.progress().unwrap().attempts_reserved, 0);
}

#[cfg(feature = "network-of1")]
#[test]
fn official_rate_policy_gate_rejects_legacy_missing_policy_before_dns_or_reservation() {
    let temp = tempfile::tempdir().unwrap();
    let (mut plan, mut lease) = plans(3000);
    plan.download_rate = None;
    let now = SystemClock.sample().unwrap().wall_ms;
    lease.authority = Authority::Approved {
        approval_id: "SYNTHETIC_NEGATIVE_TEST_NOT_A_LIVE_APPROVAL".into(),
        operator: "OFFLINE_TEST".into(),
        approved_at_ms: now,
        not_after_ms: now + 60_000,
        approved_plan_sha256: metadata_proposal_sha256(&plan, &lease.budget).unwrap(),
        cost_confirmation: "CONFIRMED_NO_CREDIT_SPEND".into(),
    };
    let mut store =
        AcquisitionStore::create(&temp.path().join("run"), plan, lease, SystemClock).unwrap();
    assert!(matches!(
        of1_range_recorder::https::OfficialHttps::capture(&mut store, 0),
        Err(HttpsError::Rate(
            of1_range_recorder::rate::RateError::Policy
        ))
    ));
    assert_eq!(store.progress().unwrap().attempts_reserved, 0);
}

#[test]
fn entity_rate_observations_follow_actual_tls_bytes_not_capacity_or_attempt_allowance() {
    use of1_range_recorder::rate::{BURST_BYTES, ENTITY_BYTES_PER_SECOND};
    struct TimedReads {
        origin: Instant,
        points: Vec<(u128, u64)>,
        total: u64,
        policy_seen: bool,
        waiting: bool,
        waits: u64,
        measured_wait_ns: u64,
    }
    impl CaptureObserver for TimedReads {
        fn rate_policy(&mut self, policy: &DownloadRate) {
            assert_eq!(policy, &DownloadRate::standard());
            self.policy_seen = true;
        }
        fn received(&mut self, _: u64, bytes: u64) {
            assert!(self.policy_seen);
            assert!(!self.waiting);
            self.total += bytes;
            self.points
                .push((self.origin.elapsed().as_nanos(), self.total));
        }
        fn rate_wait_started(&mut self, planned_ns: u64) {
            assert!(planned_ns > 0);
            assert!(!self.waiting);
            self.waiting = true;
            self.waits += 1;
        }
        fn rate_wait_finished(&mut self, actual_ns: u64) {
            assert!(self.waiting);
            self.measured_wait_ns += actual_ns;
            self.waiting = false;
        }
    }
    let temp = tempfile::tempdir().unwrap();
    let (plan, lease) = plans(20_000);
    let mut store =
        AcquisitionStore::create(&temp.path().join("run"), plan, lease, SystemClock).unwrap();
    let bytes = vec![42; usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap()];
    let mut script = response(200, bytes.len(), bytes.clone(), true);
    script.fragment_bytes = 4093; // Deliberately not a durable-segment divisor.
    let server = FixtureServer::start(OF1_SERVER_NAME, vec![script]).unwrap();
    let mut measured = TimedReads {
        origin: Instant::now(),
        points: vec![(0, 0)],
        total: 0,
        policy_seen: false,
        waiting: false,
        waits: 0,
        measured_wait_ns: 0,
    };
    let receipt = FixtureHttps::new(server.port(), server.root_der())
        .unwrap()
        .capture_observed(&mut store, 0, &mut measured)
        .unwrap();
    server.finish().unwrap();
    assert_eq!(measured.total, bytes.len() as u64);
    assert_eq!(receipt.sha256, sha256(&bytes));
    assert_eq!(receipt.response_entity_bytes, measured.total);
    assert!(!measured.waiting);
    assert_eq!(measured.waits == 0, measured.measured_wait_ns == 0);
    assert!(measured.points.len() > 2);
    // For every observed interval: at most rate * duration + one 64KiB burst.
    // A slow scheduler/disk/TLS peer may use less than the limit, never more.
    for (index, &(start_ns, before)) in measured.points.iter().enumerate() {
        for &(end_ns, after) in &measured.points[index..] {
            assert!(
                u128::from(after - before) * 1_000_000_000
                    <= (end_ns - start_ns) * u128::from(ENTITY_BYTES_PER_SECOND)
                        + u128::from(BURST_BYTES) * 1_000_000_000
            );
        }
    }
}

#[test]
fn payload_framing_and_validator_drift_have_distinct_quarantine_reasons() {
    use of1_range_recorder::durable::acquisition::{Request, RequestKind};
    let request = Request {
        sequence: 4,
        kind: RequestKind::CarRange {
            slot: 422_496_000,
            start: 10,
            end_exclusive: 14,
            total: 100,
            strong_etag: Some("\"v1\"".into()),
        },
    };
    let correct = b"HTTP/1.1 206 Partial Content\r\nContent-Length: 4\r\nContent-Range: bytes 10-13/100\r\nETag: \"v1\"\r\n\r\n";
    assert_eq!(
        parse_response_head(correct, &request)
            .unwrap()
            .content_length,
        4
    );
    let text = std::str::from_utf8(correct).unwrap();
    assert_eq!(
        parse_response_head(
            b"HTTP/1.1 412 Precondition Failed\r\nContent-Length: 0\r\n\r\n",
            &request
        ),
        Err(HeaderError::SourceDrift)
    );
    assert_eq!(
        parse_response_head(text.replace("\"v1\"", "\"v2\"").as_bytes(), &request),
        Err(HeaderError::SourceDrift)
    );
    assert_eq!(
        parse_response_head(text.replace("/100", "/101").as_bytes(), &request),
        Err(HeaderError::SourceDrift)
    );
    for changed in [
        text.replace("206", "200"),
        text.replace("10-13", "9-12"),
        text.replace("Length: 4", "Length: 5"),
    ] {
        assert_eq!(
            parse_response_head(changed.as_bytes(), &request),
            Err(HeaderError::Contract)
        );
    }
}
