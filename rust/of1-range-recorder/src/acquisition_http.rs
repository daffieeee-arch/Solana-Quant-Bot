//! Pure bounded HTTP/1.1 response framing, shared by acquisition and receipt replay.
//! No socket, TLS, resolver, endpoint selection or acquisition authority exists here.

use crate::durable::acquisition::{RangeResponse, Request, ResponseHead};
use std::collections::BTreeMap;
use thiserror::Error;

pub const MAX_HEADER_BYTES: usize = 16_384;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum HeaderError {
    #[error("HTTP_HEADER_LIMIT")]
    Limit,
    #[error("HTTP_FRAMING_INVALID")]
    Framing,
    #[error("HTTP_STATUS_UNSUPPORTED: {0}")]
    Status(u16),
    #[error("HTTP_REQUEST_CONTRACT_MISMATCH")]
    Contract,
    #[error("SOURCE_DRIFT")]
    SourceDrift,
}

/// Parse exactly one bounded header and validate it against the selected operation.
/// A response need not repeat `Connection: close`: request framing plus a bounded
/// post-body EOF check, performed by the transport, closes the connection.
/// # Errors
/// Rejects invalid status/framing, duplicate fields, redirects, transfer encoding,
/// compression, multipart and any mismatch with the immutable request contract.
pub fn parse_response_head(raw: &[u8], request: &Request) -> Result<ResponseHead, HeaderError> {
    if raw.len() > MAX_HEADER_BYTES {
        return Err(HeaderError::Limit);
    }
    let text = std::str::from_utf8(raw).map_err(|_| HeaderError::Framing)?;
    let mut lines = text
        .strip_suffix("\r\n\r\n")
        .ok_or(HeaderError::Framing)?
        .split("\r\n");
    let status = match parse_status(lines.next().ok_or(HeaderError::Framing)?) {
        Err(HeaderError::Status(412))
            if matches!(
                &request.kind,
                crate::durable::acquisition::RequestKind::CarRange {
                    strong_etag: Some(_),
                    ..
                }
            ) =>
        {
            return Err(HeaderError::SourceDrift);
        }
        other => other?,
    };
    let fields = parse_fields(lines)?;
    if fields.contains_key("transfer-encoding")
        || fields.contains_key("location")
        || fields.contains_key("upgrade")
        || fields
            .get("content-encoding")
            .is_some_and(|v| !v.eq_ignore_ascii_case("identity"))
        || fields
            .get("content-type")
            .is_some_and(|v| v.to_ascii_lowercase().starts_with("multipart/"))
    {
        return Err(HeaderError::Framing);
    }
    let content_length = decimal(fields.get("content-length").ok_or(HeaderError::Framing)?)?;
    let content_range = fields
        .get("content-range")
        .map(|v| parse_range(v))
        .transpose()?;
    let strong_etag = fields.get("etag").map(|v| parse_etag(v)).transpose()?;
    let head = ResponseHead {
        status,
        content_length,
        content_range,
        strong_etag,
        raw_headers: raw.to_vec(),
    };
    request.entity_length(&head).map_err(|error| match error {
        crate::durable::StoreError::SourceDrift => HeaderError::SourceDrift,
        _ => HeaderError::Contract,
    })?;
    Ok(head)
}

fn parse_status(line: &str) -> Result<u16, HeaderError> {
    let mut fields = line.splitn(3, ' ');
    if fields.next() != Some("HTTP/1.1") {
        return Err(HeaderError::Framing);
    }
    let code = fields.next().ok_or(HeaderError::Framing)?;
    if code.len() != 3
        || !fields
            .next()
            .is_some_and(|v| v.bytes().all(|b| (32..=126).contains(&b)))
    {
        return Err(HeaderError::Framing);
    }
    let code = u16::try_from(decimal(code)?).map_err(|_| HeaderError::Framing)?;
    if !matches!(code, 200 | 206) {
        return Err(HeaderError::Status(code));
    }
    Ok(code)
}

fn parse_fields<'a>(
    lines: impl Iterator<Item = &'a str>,
) -> Result<BTreeMap<String, &'a str>, HeaderError> {
    let mut fields = BTreeMap::new();
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(HeaderError::Framing)?;
        if name.is_empty()
            || !name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&b))
            || !value.bytes().all(|b| b == b'\t' || (32..=126).contains(&b))
        {
            return Err(HeaderError::Framing);
        }
        if fields
            .insert(name.to_ascii_lowercase(), value.trim())
            .is_some()
        {
            return Err(HeaderError::Framing);
        }
    }
    Ok(fields)
}

fn decimal(value: &str) -> Result<u64, HeaderError> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(HeaderError::Framing);
    }
    value.parse().map_err(|_| HeaderError::Framing)
}

fn parse_range(value: &str) -> Result<RangeResponse, HeaderError> {
    let (range, total) = value
        .strip_prefix("bytes ")
        .and_then(|v| v.split_once('/'))
        .ok_or(HeaderError::Framing)?;
    let (first, last) = range.split_once('-').ok_or(HeaderError::Framing)?;
    let result = RangeResponse {
        start: decimal(first)?,
        end_exclusive: decimal(last)?.checked_add(1).ok_or(HeaderError::Framing)?,
        total: decimal(total)?,
    };
    if result.start >= result.end_exclusive || result.end_exclusive > result.total {
        return Err(HeaderError::Framing);
    }
    Ok(result)
}

fn parse_etag(value: &str) -> Result<String, HeaderError> {
    if value.len() < 2
        || value.len() > 256
        || !value.starts_with('"')
        || !value.ends_with('"')
        || !value.as_bytes()[1..value.len() - 1]
            .iter()
            .all(|b| (33..=126).contains(b) && *b != b'"')
    {
        return Err(HeaderError::Framing);
    }
    Ok(value.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::durable::acquisition::RequestKind;

    fn head_request() -> Request {
        Request {
            sequence: 3,
            kind: RequestKind::CarHead,
        }
    }

    #[test]
    fn head_keeps_object_length_and_exact_headers_but_has_no_entity() {
        let raw =
            b"HTTP/1.1 200 OK\r\nContent-Length: 987654321\r\nETag: \"epoch-fixture\"\r\n\r\n";
        let request = head_request();
        let head = parse_response_head(raw, &request).unwrap();
        assert_eq!(head.content_length, 987_654_321);
        assert_eq!(head.raw_headers, raw);
        assert_eq!(request.entity_length(&head).unwrap(), 0);
    }

    #[test]
    fn rejects_ambiguous_headers_and_nonidentity_encodings() {
        for extra in [
            "content-length: 20\r\n",
            "Transfer-Encoding: chunked\r\n",
            "Content-Encoding: gzip\r\n",
            "Content-Type: multipart/byteranges\r\n",
            "Location: /elsewhere\r\n",
            "Upgrade: h2c\r\n",
            " folded: value\r\n",
        ] {
            let raw = format!("HTTP/1.1 200 OK\r\nContent-Length: 20\r\n{extra}\r\n");
            assert!(parse_response_head(raw.as_bytes(), &head_request()).is_err());
        }
    }

    #[test]
    fn rejects_header_overflow_redirects_and_weak_or_injected_etags() {
        assert_eq!(
            parse_response_head(&vec![b'a'; MAX_HEADER_BYTES + 1], &head_request()),
            Err(HeaderError::Limit)
        );
        assert_eq!(
            parse_response_head(
                b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\n\r\n",
                &head_request()
            ),
            Err(HeaderError::Status(302))
        );
        for tag in ["W/\"weak\"", "unquoted", "\"bad\"extra\"", "\"bad\r\ntag\""] {
            let raw = format!("HTTP/1.1 200 OK\r\nContent-Length: 20\r\nETag: {tag}\r\n\r\n");
            assert!(parse_response_head(raw.as_bytes(), &head_request()).is_err());
        }
    }
}
