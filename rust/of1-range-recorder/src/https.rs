//! Default-off TLS transport for exactly one approved OF1 operation per invocation.
//! No proxy, redirect, decompression, endpoint override or automatic retry exists.
//! The separate fixture connector can reach numeric loopback only and rejects live authority.

use crate::{
    acquisition_http::{HeaderError, MAX_HEADER_BYTES, parse_response_head},
    durable::{
        Clock, StoreError,
        acquisition::{
            AcquisitionStore, Authority, Permit, Progress, Receipt, Request, RequestKind,
            SEGMENT_BYTES,
        },
    },
};
use rustls::{ClientConfig, ClientConnection, RootCertStore, StreamOwned, pki_types::ServerName};
use std::{
    fmt::Write as _,
    io::{self, Read, Write},
    net::{SocketAddr, TcpStream},
    sync::Arc,
    time::{Duration, Instant},
};
use thiserror::Error;

pub const OF1_SERVER_NAME: &str = "files.old-faithful.net";

#[cfg(feature = "tls-fixture")]
pub mod fixture;

#[derive(Debug, Error)]
pub enum HttpsError {
    #[error("ACQUISITION_AUTHORITY_MISMATCH")]
    Authority,
    #[error("acquisition rejected: {0}")]
    Store(#[from] StoreError),
    #[error("bounded HTTPS I/O failed: {0}")]
    Io(#[from] io::Error),
    #[error("TLS verification failed: {0}")]
    Tls(#[from] rustls::Error),
    #[error("HTTP verification failed: {0}")]
    Header(#[from] HeaderError),
    #[error("ORIGINAL_ATTEMPT_DEADLINE")]
    Deadline,
    #[error("DNS_RESOLUTION_FAILED_OR_UNBOUNDED")]
    Dns,
    #[error("HTTP_ENTITY_TRUNCATED")]
    Truncated,
    #[error("TRAILING_RESPONSE_BYTES")]
    Trailing,
    #[error("INVALID_LOOPBACK_FIXTURE_PORT_OR_ROOT")]
    Fixture,
}

pub type HttpsResult<T> = Result<T, HttpsError>;

/// Optional operational observations, never acquisition authority or accounting.
/// Implementations must remain bounded/nonblocking; failures cannot affect capture.
pub trait CaptureObserver {
    fn active(&self) -> bool {
        false
    }
    fn reserved(&mut self, _: u64, _: &Progress) {}
    fn head(&mut self, _: u64, _: u16, _: u64) {}
    fn received(&mut self, _: u64, _: u64) {}
    fn verifying(&mut self, _: u64) {}
    fn publishing(&mut self, _: u64) {}
    fn published(&mut self, _: u64, _: &Receipt) {}
    fn failed(&mut self, _: u64, _: &str) {}
}

struct NoObservation;
impl CaptureObserver for NoObservation {}

#[cfg(feature = "monitor")]
impl CaptureObserver for crate::monitor::Monitor {
    fn active(&self) -> bool {
        true
    }
    fn reserved(&mut self, seq: u64, progress: &Progress) {
        self.reserve(seq, progress);
    }
    fn head(&mut self, seq: u64, status: u16, length: u64) {
        self.head(seq, status, length);
    }
    fn received(&mut self, seq: u64, bytes: u64) {
        self.received(seq, bytes);
    }
    fn verifying(&mut self, seq: u64) {
        self.verifying(seq);
    }
    fn publishing(&mut self, seq: u64) {
        self.publishing(seq);
    }
    fn published(&mut self, seq: u64, receipt: &Receipt) {
        self.published(seq, receipt);
    }
    fn failed(&mut self, seq: u64, reason: &str) {
        self.failed(seq, reason);
    }
}

/// Capture one approved operation. Its reservation is durable before DNS/TCP/TLS.
/// This function is absent unless `network-of1` was explicitly compiled.
/// # Errors
/// Rejects non-approved authority before DNS/socket construction, then fails closed
/// on deadlines, bounded HTTP/TLS framing, source drift, storage or budget failures.
#[cfg(feature = "network-of1")]
pub struct OfficialHttps;

#[cfg(feature = "network-of1")]
impl OfficialHttps {
    /// Capture exactly one durably reserved approved request against the fixed official host.
    /// # Errors
    /// Wrong authority fails before DNS or a socket; all failures retain the reservation.
    pub fn capture<C: Clock>(
        store: &mut AcquisitionStore<C>,
        sequence: u64,
    ) -> HttpsResult<Receipt> {
        capture_official(store, sequence, &mut NoObservation)
    }

    /// Same single-attempt authority/durability contract, with optional measurements.
    /// # Errors
    /// Identical to `capture`; observer loss never retries or authorizes a request.
    pub fn capture_observed<C: Clock>(
        store: &mut AcquisitionStore<C>,
        sequence: u64,
        observer: &mut dyn CaptureObserver,
    ) -> HttpsResult<Receipt> {
        capture_official(store, sequence, observer)
    }
}

#[cfg(feature = "network-of1")]
fn capture_official<C: Clock>(
    store: &mut AcquisitionStore<C>,
    sequence: u64,
    observer: &mut dyn CaptureObserver,
) -> HttpsResult<Receipt> {
    if !matches!(store.authority(), Authority::Approved { .. }) {
        return Err(HttpsError::Authority);
    }
    let request = store.request(sequence)?.clone();
    let path = store.source_path(sequence)?;
    let permit = store.reserve(sequence)?;
    observe_reservation(store, sequence, observer);
    let result = (|| {
        store.network_authorized(&permit)?;
        let deadline = Deadline::new(store.remaining_ms(&permit)?)?;
        let address = resolve_official(&deadline)?;
        let roots = webpki_roots::TLS_SERVER_ROOTS
            .iter()
            .cloned()
            .collect::<RootCertStore>();
        let config = tls_config(roots)?;
        capture_reserved(
            store, permit, &request, &path, address, config, deadline, observer,
        )
    })();
    if result.is_err() {
        store.abort_stream();
    }
    observe_result(sequence, &result, observer);
    result
}

/// Fixture trust never reaches an external address or upgrades fixture evidence.
/// The trusted DER certificate is public; the fixture server's key stays in memory.
#[cfg(feature = "tls-fixture")]
pub struct FixtureHttps {
    port: u16,
    config: Arc<ClientConfig>,
}

#[cfg(feature = "tls-fixture")]
impl FixtureHttps {
    /// Construct only a numeric-loopback connector with explicit fixture trust.
    /// # Errors
    /// Rejects port zero or invalid certificate bytes. No socket is constructed here.
    pub fn new(port: u16, root_der: Vec<u8>) -> HttpsResult<Self> {
        if port == 0 {
            return Err(HttpsError::Fixture);
        }
        let mut roots = RootCertStore::empty();
        roots
            .add(root_der.into())
            .map_err(|_| HttpsError::Fixture)?;
        Ok(Self {
            port,
            config: tls_config(roots)?,
        })
    }

    /// Capture one fixture operation using the production HTTP/TLS/storage path.
    /// # Errors
    /// Rejects approved/live authority before any socket, then enforces the same
    /// entity, framing, deadline, durability and retry rules as the official path.
    pub fn capture<C: Clock>(
        &self,
        store: &mut AcquisitionStore<C>,
        sequence: u64,
    ) -> HttpsResult<Receipt> {
        self.capture_observed(store, sequence, &mut NoObservation)
    }

    /// Numeric-loopback fixture capture with the same intra-request observer hooks.
    /// # Errors
    /// Same authority, framing and durable publication failures as `capture`.
    pub fn capture_observed<C: Clock>(
        &self,
        store: &mut AcquisitionStore<C>,
        sequence: u64,
        observer: &mut dyn CaptureObserver,
    ) -> HttpsResult<Receipt> {
        if !matches!(store.authority(), Authority::Fixture) {
            return Err(HttpsError::Authority);
        }
        let request = store.request(sequence)?.clone();
        let path = store.source_path(sequence)?;
        let permit = store.reserve(sequence)?;
        observe_reservation(store, sequence, observer);
        let result = (|| {
            let deadline = Deadline::new(store.remaining_ms(&permit)?)?;
            let address = SocketAddr::from(([127, 0, 0, 1], self.port));
            capture_reserved(
                store,
                permit,
                &request,
                &path,
                address,
                self.config.clone(),
                deadline,
                observer,
            )
        })();
        if result.is_err() {
            store.abort_stream();
        }
        observe_result(sequence, &result, observer);
        result
    }
}

fn observe_reservation<C: Clock>(
    store: &AcquisitionStore<C>,
    sequence: u64,
    observer: &mut dyn CaptureObserver,
) {
    if observer.active()
        && let Ok(progress) = store.progress()
    {
        observer.reserved(sequence, &progress);
    }
}

fn observe_result(
    sequence: u64,
    result: &HttpsResult<Receipt>,
    observer: &mut dyn CaptureObserver,
) {
    match result {
        Ok(receipt) => observer.published(sequence, receipt),
        Err(error) => observer.failed(sequence, &error.to_string()),
    }
}

fn tls_config(roots: RootCertStore) -> HttpsResult<Arc<ClientConfig>> {
    // Explicit provider avoids global defaults and the aws-lc/CMake graph.
    let mut config =
        ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()?
            .with_root_certificates(roots)
            .with_no_client_auth();
    config.alpn_protocols = vec![b"http/1.1".to_vec()];
    // No key-log callback, client certificate, early data or runtime trust override.
    config.enable_early_data = false;
    Ok(Arc::new(config))
}

#[allow(clippy::too_many_arguments)]
fn capture_reserved<C: Clock>(
    store: &mut AcquisitionStore<C>,
    permit: Permit,
    request: &Request,
    path: &str,
    address: SocketAddr,
    config: Arc<ClientConfig>,
    deadline: Deadline,
    observer: &mut dyn CaptureObserver,
) -> HttpsResult<Receipt> {
    let socket = TcpStream::connect_timeout(&address, deadline.remaining()?)?;
    let connection = ClientConnection::new(
        config,
        ServerName::try_from(OF1_SERVER_NAME).map_err(|_| HttpsError::Authority)?,
    )?;
    let mut stream = StreamOwned::new(connection, DeadlineTcp { socket, deadline });
    // Complete TLS verification before an HTTP request can be dispatched.
    while stream.conn.is_handshaking() {
        stream.conn.complete_io(&mut stream.sock)?;
    }
    let encoded = encode_request(request, path)?;
    stream.write_all(encoded.as_bytes())?;
    stream.flush()?;
    let mut raw_head = Vec::new();
    if let Err(error) = read_header(&mut stream, &mut raw_head) {
        if !raw_head.is_empty() && store.remaining_ms(&permit).is_ok() {
            store.reject_response(&permit, &raw_head, "HTTP_REJECTED")?;
        }
        return Err(error);
    }
    let head = match parse_response_head(&raw_head, request) {
        Ok(head) => head,
        Err(error) => {
            let reason = if error == HeaderError::SourceDrift {
                "SOURCE_DRIFT"
            } else {
                "HTTP_REJECTED"
            };
            store.remaining_ms(&permit)?;
            store.reject_response(&permit, &raw_head, reason)?;
            return Err(error.into());
        }
    };
    let mut remaining = request.entity_length(&head)?;
    observer.head(request.sequence, head.status, remaining);
    store.begin_stream(&permit, head)?;
    let mut buffer = vec![0u8; SEGMENT_BYTES].into_boxed_slice();
    while remaining > 0 {
        let limit = usize::try_from(remaining.min(SEGMENT_BYTES as u64))
            .map_err(|_| HttpsError::Truncated)?;
        store.prepare_stream_read(&permit, limit as u64)?;
        // TCP/TLS fragmentation must not amplify fsync/receipt cost. Preserve
        // complete 64 KiB segments, plus a final or interrupted short segment.
        let mut filled = 0;
        while filled < limit {
            match stream.read(&mut buffer[filled..limit]) {
                Ok(0) => {
                    retain_partial(store, &permit, &buffer[..filled])?;
                    return Err(HttpsError::Truncated);
                }
                Ok(count) => {
                    observer.received(request.sequence, count as u64);
                    filled += count;
                }
                Err(error) => {
                    retain_partial(store, &permit, &buffer[..filled])?;
                    return if error.kind() == io::ErrorKind::UnexpectedEof {
                        Err(HttpsError::Truncated)
                    } else {
                        Err(error.into())
                    };
                }
            }
        }
        store.append_stream(&permit, &buffer[..filled])?;
        remaining -= filled as u64;
    }
    // Content-Length (or HEAD's zero-entity semantics) is complete. A TLS peer
    // closing without close_notify is acceptable ONLY at this exact boundary.
    // A bounded extra plaintext byte is always rejected; no read-to-end occurs.
    eof_after_complete_entity(&mut stream)?;
    stream.sock.deadline.remaining()?;
    store.remaining_ms(&permit)?;
    observer.verifying(request.sequence);
    Ok(store.finish_stream_observed(permit, || observer.publishing(request.sequence))?)
}

fn retain_partial<C: Clock>(
    store: &mut AcquisitionStore<C>,
    permit: &Permit,
    bytes: &[u8],
) -> HttpsResult<()> {
    // Never extend an expired deadline to record an error. Full allowance stays
    // charged even when the final fragment cannot be durably represented.
    if !bytes.is_empty() {
        store.remaining_ms(permit)?;
        store.append_stream(permit, bytes)?;
    }
    Ok(())
}

fn encode_request(request: &Request, path: &str) -> HttpsResult<String> {
    if !path.starts_with('/')
        || !path
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/-._".contains(&b))
    {
        return Err(HttpsError::Authority);
    }
    let mut encoded = format!(
        "{} {path} HTTP/1.1\r\nHost: {OF1_SERVER_NAME}\r\nAccept-Encoding: identity\r\nConnection: close\r\n",
        request.method()
    );
    if let RequestKind::CarRange {
        start,
        end_exclusive,
        strong_etag,
        ..
    } = &request.kind
    {
        let last = end_exclusive
            .checked_sub(1)
            .filter(|n| *n >= *start)
            .ok_or(HttpsError::Authority)?;
        write!(encoded, "Range: bytes={start}-{last}\r\n").map_err(|_| HttpsError::Authority)?;
        if let Some(etag) = strong_etag {
            if etag.bytes().any(|b| b < 32 || b == 127) {
                return Err(HttpsError::Authority);
            }
            write!(encoded, "If-Match: {etag}\r\n").map_err(|_| HttpsError::Authority)?;
        }
    }
    encoded.push_str("\r\n");
    Ok(encoded)
}

fn read_header(stream: &mut impl Read, raw: &mut Vec<u8>) -> HttpsResult<()> {
    while raw.len() < MAX_HEADER_BYTES {
        let mut byte = [0];
        if stream.read(&mut byte)? == 0 {
            return Err(HttpsError::Truncated);
        }
        raw.push(byte[0]);
        if raw.ends_with(b"\r\n\r\n") {
            return Ok(());
        }
    }
    Err(HeaderError::Limit.into())
}

fn eof_after_complete_entity(stream: &mut impl Read) -> HttpsResult<()> {
    match stream.read(&mut [0u8; 1]) {
        Ok(0) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => Ok(()),
        Ok(_) => Err(HttpsError::Trailing),
        Err(error) => Err(error.into()),
    }
}

#[derive(Clone)]
struct Deadline {
    ends: Instant,
}

impl Deadline {
    fn new(remaining_ms: u64) -> HttpsResult<Self> {
        if remaining_ms == 0 {
            return Err(HttpsError::Deadline);
        }
        Ok(Self {
            ends: Instant::now()
                .checked_add(Duration::from_millis(remaining_ms))
                .ok_or(HttpsError::Deadline)?,
        })
    }
    fn remaining(&self) -> HttpsResult<Duration> {
        self.ends
            .checked_duration_since(Instant::now())
            .filter(|v| !v.is_zero())
            .ok_or(HttpsError::Deadline)
    }
    fn remaining_io(&self) -> io::Result<Duration> {
        self.remaining()
            .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "original attempt deadline"))
    }
}

struct DeadlineTcp {
    socket: TcpStream,
    deadline: Deadline,
}

impl Read for DeadlineTcp {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        self.socket
            .set_read_timeout(Some(self.deadline.remaining_io()?))?;
        self.socket.read(bytes)
    }
}
impl Write for DeadlineTcp {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.socket
            .set_write_timeout(Some(self.deadline.remaining_io()?))?;
        self.socket.write(bytes)
    }
    fn flush(&mut self) -> io::Result<()> {
        self.deadline.remaining_io()?;
        self.socket.flush()
    }
}

#[cfg(feature = "network-of1")]
fn resolve_official(deadline: &Deadline) -> HttpsResult<SocketAddr> {
    use std::process::{Command, Stdio};
    deadline.remaining()?;
    // One fixed system resolver process. No caller host, argument, binary or env.
    // kill+wait bounds blocking libc/NSS DNS; a detached thread would not.
    let child = Command::new("/usr/bin/getent")
        .args(["ahosts", OF1_SERVER_NAME])
        .env_clear()
        .env("LC_ALL", "C")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let output = wait_resolver(child, deadline)?;
    parse_resolver_output(&output)
}

#[cfg(feature = "network-of1")]
fn wait_resolver(child: std::process::Child, deadline: &Deadline) -> HttpsResult<Vec<u8>> {
    struct ReapedChild(Option<std::process::Child>);
    impl Drop for ReapedChild {
        fn drop(&mut self) {
            if let Some(child) = self.0.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
    let mut guard = ReapedChild(Some(child));
    let child = guard.0.as_mut().ok_or(HttpsError::Dns)?;
    loop {
        let remaining = deadline.remaining()?;
        if let Some(status) = child.try_wait()? {
            if !status.success() {
                return Err(HttpsError::Dns);
            }
            let mut output = Vec::new();
            child
                .stdout
                .take()
                .ok_or(HttpsError::Dns)?
                .take(16_385)
                .read_to_end(&mut output)?;
            if output.len() > 16_384 {
                return Err(HttpsError::Dns);
            }
            deadline.remaining()?;
            guard.0 = None;
            return Ok(output);
        }
        std::thread::sleep(remaining.min(Duration::from_millis(5)));
    }
}

#[cfg(feature = "network-of1")]
fn parse_resolver_output(bytes: &[u8]) -> HttpsResult<SocketAddr> {
    use std::{collections::BTreeSet, net::IpAddr};
    if bytes.len() > 16_384 {
        return Err(HttpsError::Dns);
    }
    let text = std::str::from_utf8(bytes).map_err(|_| HttpsError::Dns)?;
    let mut addresses = BTreeSet::new();
    for (index, line) in text.lines().enumerate() {
        if index >= 128 {
            return Err(HttpsError::Dns);
        }
        let fields: Vec<_> = line.split_whitespace().collect();
        if !(2..=3).contains(&fields.len()) {
            return Err(HttpsError::Dns);
        }
        let address: IpAddr = fields[0].parse().map_err(|_| HttpsError::Dns)?;
        if !matches!(fields[1], "STREAM" | "DGRAM" | "RAW") {
            return Err(HttpsError::Dns);
        }
        if fields[1] == "STREAM" {
            addresses.insert(address);
        }
        if addresses.len() > 32 {
            return Err(HttpsError::Dns);
        }
    }
    // One selected address, not an automatic connection retry across the answer set.
    addresses
        .into_iter()
        .next()
        .map(|address| SocketAddr::new(address, 443))
        .ok_or(HttpsError::Dns)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn eof_exception_is_limited_to_a_completed_entity() {
        struct BareTlsEof;
        impl Read for BareTlsEof {
            fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
                Err(io::ErrorKind::UnexpectedEof.into())
            }
        }
        assert!(eof_after_complete_entity(&mut BareTlsEof).is_ok());
        assert!(matches!(
            eof_after_complete_entity(&mut &b"x"[..]),
            Err(HttpsError::Trailing)
        ));
    }

    #[test]
    fn deadline_is_not_renewed() {
        let deadline = Deadline::new(1).unwrap();
        std::thread::sleep(Duration::from_millis(5));
        assert!(matches!(deadline.remaining(), Err(HttpsError::Deadline)));
        assert_eq!(
            deadline.remaining_io().unwrap_err().kind(),
            io::ErrorKind::TimedOut
        );
    }

    #[cfg(feature = "network-of1")]
    #[test]
    fn resolver_output_has_no_endpoint_override_or_hidden_address_retry() {
        assert_eq!(
            parse_resolver_output(b"192.0.2.2 STREAM files.old-faithful.net\n192.0.2.1 STREAM\n")
                .unwrap(),
            "192.0.2.1:443".parse().unwrap()
        );
        for bytes in [
            b"https://elsewhere STREAM\n".as_slice(),
            b"192.0.2.1 DGRAM\n",
            b"192.0.2.1:443 STREAM\n",
            b"192.0.2.1 BAD\n",
        ] {
            assert!(parse_resolver_output(bytes).is_err());
        }
    }

    #[cfg(feature = "network-of1")]
    #[test]
    fn stalled_resolver_process_is_killed_and_reaped() {
        let child = std::process::Command::new("/bin/sleep")
            .arg("2")
            .stdout(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        let pid = child.id();
        assert!(matches!(
            wait_resolver(child, &Deadline::new(10).unwrap()),
            Err(HttpsError::Deadline)
        ));
        assert!(!std::path::Path::new(&format!("/proc/{pid}")).exists());
    }
}
