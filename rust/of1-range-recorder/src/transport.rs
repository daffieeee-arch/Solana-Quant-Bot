//! Narrow HTTP/1.1 range simulation on 127.0.0.1 only. Disabled in the default build.
//! One capture performs one durable reservation and at most one connection. No retry loop,
//! DNS, proxy, caller URL, production endpoint, TLS or acquisition authorization exists here.

use crate::durable::{
    Clock, MAX_STREAM_CHUNK_BYTES, Permit, Receipt, Response, Store, StoreError, StreamHead,
};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream},
    time::{Duration, Instant},
};
use thiserror::Error;

pub const MAX_HEADER_BYTES: usize = 16_384;

#[derive(Debug, Error)]
pub enum TransportError {
    #[error("durable capture rejected: {0}")]
    Store(#[from] StoreError),
    #[error("local fixture HTTP I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("fixture port must be nonzero; only 127.0.0.1 is supported")]
    Endpoint,
    #[error("HTTP_HEADER_LIMIT")]
    HeaderLimit,
    #[error("HTTP_FRAMING_INVALID")]
    Framing,
    #[error("HTTP_RANGE_MISMATCH")]
    Range,
    #[error("HTTP_STATUS_UNSUPPORTED: {0}")]
    Status(u16),
    #[error("HTTP_ENTITY_TRUNCATED")]
    Truncated,
    #[error("TRAILING_RESPONSE_BYTES")]
    TrailingResponseBytes,
}

pub type TransportResult<T> = Result<T, TransportError>;

/// No address/URL constructor: the port selects only a local simulation listener.
#[derive(Debug)]
pub struct LoopbackFixture {
    port: u16,
}

impl LoopbackFixture {
    /// # Errors
    /// Rejects the unspecified port. No connection occurs during construction.
    pub fn new(port: u16) -> TransportResult<Self> {
        if port == 0 {
            return Err(TransportError::Endpoint);
        }
        Ok(Self { port })
    }

    /// Capture one index-planned range, retaining failed-attempt budget and durable prefixes.
    /// Callers must drop/reopen the store before retrying a failed attempt. Already-published
    /// requests are rejected before dispatch; callers read them through `Store::published`.
    /// # Errors
    /// Fails closed on budgets/deadlines, HTTP ambiguity, source/byte conflicts or storage errors.
    pub fn capture<C: Clock>(
        &self,
        store: &mut Store<C>,
        sequence: u64,
    ) -> TransportResult<Receipt> {
        let request = store.request(sequence)?;
        let (start, end) = (request.start, request.end_exclusive);
        let path = store.source_path();
        let permit = store.reserve(sequence)?;
        let result = self.capture_reserved(store, permit, &path, start, end);
        if result.is_err() {
            store.abort_stream();
        }
        result
    }

    fn capture_reserved<C: Clock>(
        &self,
        store: &mut Store<C>,
        permit: Permit,
        path: &str,
        start: u64,
        end: u64,
    ) -> TransportResult<Receipt> {
        let deadline = AttemptDeadline::new(store.remaining_ms(&permit)?);
        let address = SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::LOCALHOST, self.port));
        let mut stream = TcpStream::connect_timeout(&address, deadline.remaining(store, &permit)?)?;
        let request = format!(
            "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nRange: bytes={start}-{}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n",
            self.port,
            end.checked_sub(1).ok_or(TransportError::Range)?,
        );
        write_request(&mut stream, request.as_bytes(), &deadline, store, &permit)?;
        let header = read_header(&mut stream, &deadline, store, &permit)?;
        let head = parse_header(&header, start, end)?;
        // The store checks the object total and durable validators before any entity byte.
        store.begin_stream(&permit, head)?;
        let mut remaining = end - start;
        let mut buffer = [0u8; MAX_STREAM_CHUNK_BYTES];
        while remaining > 0 {
            let limit = usize::try_from(remaining.min(MAX_STREAM_CHUNK_BYTES as u64))
                .map_err(|_| TransportError::Range)?;
            store.prepare_stream_read(&permit, limit as u64)?;
            stream.set_read_timeout(Some(deadline.remaining(store, &permit)?))?;
            let count = stream.read(&mut buffer[..limit])?;
            if count == 0 {
                return Err(TransportError::Truncated);
            }
            store.append_stream(&permit, &buffer[..count])?;
            remaining -= count as u64;
        }
        // Strict close-delimited fixture connection: one bounded framing probe, never a
        // second entity/chunk or unrestricted read-to-end. Outside response_entity_bytes.
        stream.set_read_timeout(Some(deadline.remaining(store, &permit)?))?;
        if stream.read(&mut [0u8; 1])? != 0 {
            return Err(TransportError::TrailingResponseBytes);
        }
        deadline.remaining(store, &permit)?;
        Ok(store.finish_stream(permit)?)
    }
}

struct AttemptDeadline {
    start: Instant,
    allowance: Duration,
}

impl AttemptDeadline {
    fn new(remaining_ms: u64) -> Self {
        Self {
            start: Instant::now(),
            allowance: Duration::from_millis(remaining_ms),
        }
    }

    fn remaining<C: Clock>(
        &self,
        store: &mut Store<C>,
        permit: &Permit,
    ) -> TransportResult<Duration> {
        let persisted = Duration::from_millis(store.remaining_ms(permit)?);
        let local = self
            .allowance
            .checked_sub(self.start.elapsed())
            .filter(|v| !v.is_zero())
            .ok_or(StoreError::Deadline)?;
        Ok(persisted.min(local))
    }
}

fn write_request<C: Clock>(
    stream: &mut TcpStream,
    mut bytes: &[u8],
    deadline: &AttemptDeadline,
    store: &mut Store<C>,
    permit: &Permit,
) -> TransportResult<()> {
    while !bytes.is_empty() {
        stream.set_write_timeout(Some(deadline.remaining(store, permit)?))?;
        let count = stream.write(bytes)?;
        if count == 0 {
            return Err(std::io::Error::from(std::io::ErrorKind::WriteZero).into());
        }
        bytes = &bytes[count..];
    }
    Ok(())
}

fn read_header<C: Clock>(
    stream: &mut TcpStream,
    deadline: &AttemptDeadline,
    store: &mut Store<C>,
    permit: &Permit,
) -> TransportResult<Vec<u8>> {
    let mut header = Vec::new();
    while header.len() < MAX_HEADER_BYTES {
        stream.set_read_timeout(Some(deadline.remaining(store, permit)?))?;
        let mut byte = [0u8; 1];
        if stream.read(&mut byte)? == 0 {
            return Err(TransportError::Truncated);
        }
        header.push(byte[0]);
        if header.ends_with(b"\r\n\r\n") {
            return Ok(header);
        }
    }
    Err(TransportError::HeaderLimit)
}

fn decimal(value: &str) -> TransportResult<u64> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(TransportError::Framing);
    }
    value.parse().map_err(|_| TransportError::Framing)
}

fn parse_header(bytes: &[u8], start: u64, end: u64) -> TransportResult<StreamHead> {
    let text = std::str::from_utf8(bytes).map_err(|_| TransportError::Framing)?;
    let mut lines = text
        .strip_suffix("\r\n\r\n")
        .ok_or(TransportError::Framing)?
        .split("\r\n");
    let status = lines.next().ok_or(TransportError::Framing)?;
    let mut status = status.splitn(3, ' ');
    if status.next() != Some("HTTP/1.1") {
        return Err(TransportError::Framing);
    }
    let code = status.next().ok_or(TransportError::Framing)?;
    if code.len() != 3 {
        return Err(TransportError::Framing);
    }
    let code = u16::try_from(decimal(code)?).map_err(|_| TransportError::Framing)?;
    if !status
        .next()
        .is_some_and(|reason| reason.bytes().all(|b| (32..=126).contains(&b)))
    {
        return Err(TransportError::Framing);
    }
    if code != 206 {
        return Err(TransportError::Status(code));
    }
    let mut fields = BTreeMap::new();
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(TransportError::Framing)?;
        if name.is_empty()
            || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
            || !value.bytes().all(|b| b == b'\t' || (32..=126).contains(&b))
        {
            return Err(TransportError::Framing);
        }
        if fields
            .insert(name.to_ascii_lowercase(), value.trim())
            .is_some()
        {
            return Err(TransportError::Framing);
        }
    }
    if fields.contains_key("transfer-encoding")
        || fields.contains_key("location")
        || fields
            .get("content-encoding")
            .is_some_and(|value| !value.eq_ignore_ascii_case("identity"))
        || !fields
            .get("connection")
            .is_some_and(|v| v.eq_ignore_ascii_case("close"))
        || fields
            .get("content-type")
            .is_some_and(|v| v.to_ascii_lowercase().starts_with("multipart/"))
    {
        return Err(TransportError::Framing);
    }
    let length = decimal(
        fields
            .get("content-length")
            .ok_or(TransportError::Framing)?,
    )?;
    let range = fields.get("content-range").ok_or(TransportError::Framing)?;
    let (range, total) = range
        .strip_prefix("bytes ")
        .and_then(|v| v.split_once('/'))
        .ok_or(TransportError::Framing)?;
    let (first, last) = range.split_once('-').ok_or(TransportError::Framing)?;
    let first = decimal(first)?;
    let last = decimal(last)?.checked_add(1).ok_or(TransportError::Range)?;
    let total = decimal(total)?;
    if first != start || last != end || length != end - start || last > total {
        return Err(TransportError::Range);
    }
    let strong_etag = fields
        .get("etag")
        .map(|value| {
            if value.len() < 2
                || value.len() > 256
                || !value.starts_with('"')
                || !value.ends_with('"')
                || !value[1..value.len() - 1]
                    .bytes()
                    .all(|b| (33..=126).contains(&b) && b != b'"')
            {
                return Err(TransportError::Framing);
            }
            Ok((*value).to_string())
        })
        .transpose()?;
    Ok(StreamHead {
        response: Response {
            status: code,
            start: first,
            end_exclusive: last,
            total,
        },
        strong_etag,
    })
}
