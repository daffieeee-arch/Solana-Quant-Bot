#![cfg(feature = "loopback-fixture")]

use of1_range_recorder::{
    OfflinePlan,
    durable::{Clock, ClockSample, FaultPoint, Store, StoreError},
    fixture,
    transport::{LoopbackFixture, TransportError},
};
use std::{
    fs,
    io::{Read, Write},
    net::{Ipv4Addr, Shutdown, SocketAddrV4, TcpListener, TcpStream},
    path::PathBuf,
    sync::{Arc, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
use tempfile::{TempDir, tempdir};

#[derive(Clone)]
struct TestClock(Arc<Mutex<ClockSample>>);

impl TestClock {
    fn new() -> Self {
        Self(Arc::new(Mutex::new(ClockSample {
            wall_ms: 100_000,
            boot_ms: 10_000,
            boot_id: "FIXTURE_ONLY:transport-test-boot".into(),
        })))
    }

    fn advance(&self, ms: u64) {
        let mut at = self.0.lock().unwrap();
        at.wall_ms += ms;
        at.boot_ms += ms;
    }
}

impl Clock for TestClock {
    fn sample(&self) -> Result<ClockSample, StoreError> {
        Ok(self.0.lock().unwrap().clone())
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
        let index = directory.path().join("index.raw");
        fs::write(&index, fixture::index_bytes().unwrap()).unwrap();
        let mut plan = fixture::plan();
        plan.end_slot = plan.start_slot + 1;
        plan.budget.max_requests = 6;
        plan.budget.max_total_response_entity_bytes = 192;
        plan.budget.request_retries = 2;
        plan.budget.response_timeout_ms = 5_000;
        Self {
            root: directory.path().join("capture"),
            _directory: directory,
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

    fn resume(&self) -> Store<TestClock> {
        Store::resume(
            &self.root,
            self.plan.clone(),
            &self.index,
            self.clock.clone(),
        )
        .unwrap()
    }
}

struct Server {
    port: u16,
    thread: JoinHandle<Vec<u8>>,
}

impl Server {
    fn bytes(bytes: Vec<u8>) -> Self {
        Self::serve(move |socket| {
            let _ = socket.write_all(&bytes);
        })
    }

    fn serve(reply: impl FnOnce(&mut TcpStream) + Send + 'static) -> Self {
        let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let port = listener.local_addr().unwrap().port();
        let thread = thread::spawn(move || {
            let until = Instant::now() + Duration::from_secs(10);
            let mut socket = loop {
                match listener.accept() {
                    Ok((socket, peer)) => {
                        assert!(peer.ip().is_loopback());
                        break socket;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        assert!(Instant::now() < until, "fixture request never arrived");
                        thread::sleep(Duration::from_millis(1));
                    }
                    Err(e) => panic!("fixture accept failed: {e}"),
                }
            };
            socket
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            socket
                .set_write_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                socket.read_exact(&mut byte).unwrap();
                request.extend_from_slice(&byte);
                assert!(request.len() <= 16_384);
            }
            reply(&mut socket);
            let _ = socket.shutdown(Shutdown::Write);
            request
        });
        Self { port, thread }
    }

    fn finish(self) -> String {
        String::from_utf8(self.thread.join().unwrap()).unwrap()
    }
}

fn headers(sequence: u64, extra: &str) -> String {
    let start = 128 + sequence * 32;
    let end = start + 31;
    format!(
        "HTTP/1.1 206 Partial Content\r\nContent-Length: 32\r\nContent-Range: bytes {start}-{end}/1024\r\nConnection: close\r\n{extra}\r\n"
    )
}

fn reply(sequence: u64, body: &[u8], extra: &str) -> Vec<u8> {
    let mut response = headers(sequence, extra).into_bytes();
    response.extend_from_slice(body);
    response
}

fn capture(
    store: &mut Store<TestClock>,
    sequence: u64,
    response: Vec<u8>,
) -> Result<of1_range_recorder::durable::Receipt, TransportError> {
    let server = Server::bytes(response);
    let result = LoopbackFixture::new(server.port)
        .unwrap()
        .capture(store, sequence);
    server.finish();
    result
}

#[test]
fn loopback_endpoint_is_numeric_local_only() {
    assert!(matches!(
        LoopbackFixture::new(0),
        Err(TransportError::Endpoint)
    ));
    // Public constructor accepts no hostname, URL, proxy, environment or backend parameter.
    assert!(LoopbackFixture::new(49152).is_ok());
}

#[test]
fn exact_206_range_request_publishes_original_entity_bytes() {
    let h = Harness::new();
    let mut store = h.create();
    let payload: Vec<_> = (0..32).collect();
    let server = Server::bytes(reply(0, &payload, "ETag: \"fixture-a\"\r\n"));
    let receipt = LoopbackFixture::new(server.port)
        .unwrap()
        .capture(&mut store, 0)
        .unwrap();
    let request = server.finish().to_ascii_lowercase();
    assert!(request.starts_with("get /978/epoch-978.car http/1.1\r\n"));
    assert!(request.contains("range: bytes=128-159\r\n"));
    assert!(request.contains("connection: close\r\n"));
    assert_eq!(receipt.response_entity_bytes, 32);
    assert_eq!(receipt.evidence, "Fixture");
    assert_eq!(store.published(0).unwrap().unwrap().bytes, payload);
    assert_eq!(store.summary().unwrap().attempts_reserved, 1);
}

#[test]
fn response_framing_rejects_missing_duplicate_or_ambiguous_headers() {
    let normal = headers(0, "");
    for head in [
        normal.replace("Partial Content", "invalid\u{1}reason"),
        normal.replace("Partial Content", "non-ASCII é"),
        normal.replace("Content-Length: 32\r\n", ""),
        normal.replace("Content-Range: bytes 128-159/1024\r\n", ""),
        normal.replace("Connection: close\r\n", ""),
        normal.replace(
            "Content-Length: 32",
            "Content-Length: 32\r\nContent-Length: 32",
        ),
        normal.replace("Content-Length: 32", "Content-Length: 32, 32"),
        normal.replace("Content-Length: 32", "Content-Length: +32"),
        normal.replace("Content-Length: 32", "Content-Length: 18446744073709551616"),
        normal.replace(
            "Content-Range: bytes 128-159/1024",
            "Content-Range: bytes 128-159/1024\r\nContent-Range: bytes 128-159/1024",
        ),
        normal.replace("Connection: close", "Connection: keep-alive"),
        headers(0, "Transfer-Encoding: chunked\r\n"),
        headers(0, "Transfer-Encoding: identity\r\n"),
        headers(0, " folded: header\r\n"),
    ] {
        let h = Harness::new();
        let mut store = h.create();
        let mut bytes = head.into_bytes();
        bytes.extend_from_slice(&[0x42; 32]);
        assert!(capture(&mut store, 0, bytes).is_err());
        assert!(!h.root.join("published/0000000000").exists());
        assert_eq!(store.summary().unwrap().attempts_reserved, 1);
    }
}

#[test]
fn incorrect_ranges_status_redirect_and_encoding_never_publish() {
    let normal = headers(0, "");
    for head in [
        normal.replace("206 Partial Content", "200 OK"),
        normal.replace("206 Partial Content", "302 Found"),
        normal.replace("206 Partial Content", "416 Range Not Satisfiable"),
        normal.replace("bytes 128-159/1024", "bytes 129-160/1024"),
        normal.replace("bytes 128-159/1024", "bytes 128-159/1025"),
        normal.replace("bytes 128-159/1024", "bytes 128-159/*"),
        normal.replace("Content-Length: 32", "Content-Length: 33"),
        headers(0, "Content-Encoding: gzip\r\n"),
        headers(0, "Location: https://invalid.example/\r\n"),
    ] {
        let h = Harness::new();
        let mut store = h.create();
        let mut bytes = head.into_bytes();
        bytes.extend_from_slice(&[0x42; 32]);
        assert!(capture(&mut store, 0, bytes).is_err());
        assert!(!h.root.join("published/0000000000").exists());
    }
}

#[test]
fn strict_stream_length_rejects_truncation_and_extra_bytes() {
    for length in [0, 1, 31, 33, 100] {
        let h = Harness::new();
        let mut store = h.create();
        let error = capture(&mut store, 0, reply(0, &vec![0x42; length], "")).unwrap_err();
        if length < 32 {
            assert!(matches!(error, TransportError::Truncated));
        } else {
            assert!(matches!(error, TransportError::TrailingResponseBytes));
        }
        assert!(store.published(0).unwrap().is_none());
        assert_eq!(store.summary().unwrap().charged_entity_bytes, 32);
    }
}

#[test]
fn bounded_headers_reject_unterminated_or_oversized_input() {
    for bytes in [
        b"HTTP/1.1 206 Partial Content\r\nContent-Length:".to_vec(),
        vec![b'A'; 16_385],
    ] {
        let h = Harness::new();
        let mut store = h.create();
        assert!(capture(&mut store, 0, bytes).is_err());
        assert!(store.published(0).unwrap().is_none());
    }
}

#[test]
fn interrupted_prefix_retry_matches_across_restart() {
    let h = Harness::new();
    let mut store = h.create();
    assert!(matches!(
        capture(&mut store, 0, reply(0, &[0x42; 7], "ETag: \"same\"\r\n")),
        Err(TransportError::Truncated)
    ));
    drop(store);
    h.clock.advance(1);
    let mut resumed = h.resume();
    capture(&mut resumed, 0, reply(0, &[0x42; 32], "ETag: \"same\"\r\n")).unwrap();
    let summary = resumed.summary().unwrap();
    assert_eq!(summary.attempts_reserved, 2);
    assert_eq!(summary.charged_entity_bytes, 64);
    assert_eq!(summary.verified_response_entity_bytes, 32);
    assert_eq!(summary.unpublished_attempts, 1);
}

#[test]
fn changed_etag_is_source_drift() {
    let h = Harness::new();
    let mut store = h.create();
    assert!(capture(&mut store, 0, reply(0, &[0x42; 7], "ETag: \"before\"\r\n")).is_err());
    drop(store);
    let mut resumed = h.resume();
    let error = capture(
        &mut resumed,
        0,
        reply(0, &[0x42; 32], "ETag: \"after\"\r\n"),
    )
    .unwrap_err();
    assert!(matches!(
        error,
        TransportError::Store(StoreError::SourceDrift)
    ));
    assert!(!h.root.join("published/0000000000").exists());
    assert!(matches!(resumed.published(0), Err(StoreError::SourceDrift)));
}

#[test]
fn differing_retry_prefix_is_conflicting_bytes() {
    for validator in ["", "ETag: \"same\"\r\n"] {
        let h = Harness::new();
        let mut store = h.create();
        assert!(capture(&mut store, 0, reply(0, &[0x42; 7], validator)).is_err());
        drop(store);
        let mut resumed = h.resume();
        let error = capture(&mut resumed, 0, reply(0, &[0x43; 32], validator)).unwrap_err();
        assert!(matches!(
            error,
            TransportError::Store(StoreError::ConflictingBytes)
        ));
        assert!(!h.root.join("published/0000000000").exists());
        assert!(matches!(
            resumed.published(0),
            Err(StoreError::ConflictingBytes)
        ));
    }
}

#[test]
fn failed_attempts_do_not_refund_requests_or_bytes() {
    let mut h = Harness::new();
    h.plan.budget.max_requests = 2;
    h.plan.budget.max_total_response_entity_bytes = 64;
    let mut store = h.create();
    assert!(capture(&mut store, 0, reply(0, &[0x42; 7], "")).is_err());
    drop(store);
    let mut resumed = h.resume();
    assert!(capture(&mut resumed, 0, reply(0, &[0x42; 7], "")).is_err());
    drop(resumed);
    let mut exhausted = h.resume();
    // No listener: budget rejection must happen before any connect attempt.
    assert!(matches!(
        LoopbackFixture::new(9).unwrap().capture(&mut exhausted, 0),
        Err(TransportError::Store(StoreError::Budget))
    ));
    assert_eq!(exhausted.summary().unwrap().attempts_reserved, 2);
    assert_eq!(exhausted.summary().unwrap().charged_entity_bytes, 64);
}

#[test]
fn retry_limit_survives_restart() {
    let mut h = Harness::new();
    h.plan.budget.request_retries = 0;
    let mut store = h.create();
    assert!(capture(&mut store, 0, reply(0, &[0x42; 7], "")).is_err());
    drop(store);
    let mut resumed = h.resume();
    assert!(matches!(
        LoopbackFixture::new(9).unwrap().capture(&mut resumed, 0),
        Err(TransportError::Store(StoreError::Budget))
    ));
}

#[test]
fn deadline_stop_prevents_dispatch_after_restart() {
    let h = Harness::new();
    let mut store = h.create();
    capture(&mut store, 0, reply(0, &[0x42; 32], "")).unwrap();
    drop(store);
    h.clock.advance(h.plan.budget.max_runtime_ms);
    assert!(matches!(
        Store::resume(&h.root, h.plan.clone(), &h.index, h.clock.clone()),
        Err(StoreError::Deadline)
    ));
}

#[test]
fn response_timeout_is_not_reset_by_slow_drip() {
    let mut h = Harness::new();
    h.plan.budget.response_timeout_ms = 150;
    let mut store = h.create();
    let server = Server::serve(|socket| {
        for byte in headers(0, "").bytes().chain([0x42; 32]) {
            if socket.write_all(&[byte]).is_err() {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
    });
    let started = Instant::now();
    assert!(
        LoopbackFixture::new(server.port)
            .unwrap()
            .capture(&mut store, 0)
            .is_err()
    );
    assert!(started.elapsed() < Duration::from_secs(2));
    server.finish();
    assert!(store.published(0).unwrap().is_none());
}

#[test]
fn publication_crash_reopens_without_repeating_http() {
    let h = Harness::new();
    let mut store = h.create();
    store.inject_fault(FaultPoint::AfterPublish);
    assert!(matches!(
        capture(&mut store, 0, reply(0, &[0x42; 32], "")),
        Err(TransportError::Store(StoreError::Injected(
            FaultPoint::AfterPublish
        )))
    ));
    drop(store);
    let mut resumed = h.resume();
    assert_eq!(resumed.published(0).unwrap().unwrap().bytes, [0x42; 32]);
    assert!(matches!(
        LoopbackFixture::new(9).unwrap().capture(&mut resumed, 0),
        Err(TransportError::Store(StoreError::AlreadyPublished))
    ));
    assert_eq!(resumed.summary().unwrap().attempts_reserved, 1);
}

#[test]
fn transport_restart_progress_is_ordered_and_domain_counts_unavailable() {
    let h = Harness::new();
    let mut store = h.create();
    capture(&mut store, 0, reply(0, &[0x42; 32], "")).unwrap();
    let before = store.summary().unwrap();
    drop(store);
    h.clock.advance(10);
    let mut resumed = h.resume();
    capture(&mut resumed, 1, reply(1, &[0x43; 32], "")).unwrap();
    let after = resumed.summary().unwrap();
    assert_eq!(after.published_requests, 2);
    assert_eq!(after.attempts_reserved, 2);
    assert_eq!(after.verified_response_entity_bytes, 64);
    assert_eq!(after.deadline_wall_ms, before.deadline_wall_ms);
    assert_eq!(after.deadline_boot_ms, before.deadline_boot_ms);
    assert_eq!(after.domain_counts, "UNAVAILABLE_NOT_DECODED_IN_B4");
    assert_eq!(resumed.published(0).unwrap().unwrap().bytes, [0x42; 32]);
    assert_eq!(resumed.published(1).unwrap().unwrap().bytes, [0x43; 32]);
}
