//! Offline interpretation of committed metadata and Raw. No request dispatch here.
//! The fixture planner and this receipt-bound path share only index arithmetic;
//! neither a caller-provided range list nor a fixture authority becomes a live lease.

use crate::{
    Budget, IndexSelection, RECORD_BYTES, SLOTS_PER_EPOCH, car,
    durable::{
        Clock, StoreError, StoreResult,
        acquisition::{
            AcquisitionStore, AggregatePlan, PreparedPayload, Published, Request, RequestKind,
        },
    },
    plan_index_requests, sha256,
};
use serde::Serialize;
use std::{collections::BTreeMap, fs::File, io::Read, path::Path};

fn raw(object: &Published, limit: u64) -> StoreResult<Vec<u8>> {
    let bytes = read_limited(&object.raw_path, limit)?;
    if bytes.len() as u64 != object.receipt.response_entity_bytes
        || sha256(&bytes) != object.receipt.sha256
    {
        return Err(StoreError::Corrupt);
    }
    Ok(bytes)
}

/// Read an exact bounded regular artifact, never a FIFO, symlink or unbounded stream.
/// # Errors
/// Missing, oversized, nonregular or concurrently length-changing input is rejected.
pub fn read_limited(path: &Path, limit: u64) -> StoreResult<Vec<u8>> {
    let descriptor = rustix::fs::open(
        path,
        rustix::fs::OFlags::RDONLY
            | rustix::fs::OFlags::CLOEXEC
            | rustix::fs::OFlags::NOFOLLOW
            | rustix::fs::OFlags::NONBLOCK,
        rustix::fs::Mode::empty(),
    )
    .map_err(std::io::Error::from)?;
    let file = File::from(descriptor);
    let size = file.metadata()?.len();
    if !file.metadata()?.is_file() || size > limit {
        return Err(StoreError::Budget);
    }
    let mut bytes = Vec::new();
    file.take(limit.checked_add(1).ok_or(StoreError::Budget)?)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != size {
        return Err(StoreError::Corrupt);
    }
    Ok(bytes)
}

/// Bind all four exact receipts, including original-header and entity hashes.
/// # Errors
/// Missing, reordered or substituted metadata is not a manifest.
pub fn metadata_receipt_sha256(metadata: &[Published]) -> StoreResult<String> {
    if metadata.len() != 4 {
        return Err(StoreError::Corrupt);
    }
    for (object, request) in metadata
        .iter()
        .zip(super::durable::acquisition::metadata_requests())
    {
        if object.receipt.request != request {
            return Err(StoreError::Corrupt);
        }
        raw(object, request.allowance())?;
    }
    let receipts: Vec<_> = metadata.iter().map(|p| &p.receipt).collect();
    Ok(sha256(
        &serde_json::to_vec(&receipts).map_err(|_| StoreError::Corrupt)?,
    ))
}

fn declared_sha256(bytes: &[u8], epoch: u64) -> StoreResult<String> {
    let text = std::str::from_utf8(bytes).map_err(|_| StoreError::Corrupt)?;
    // Accept only a bare digest or standard sha256sum for this exact CAR basename.
    let fields: Vec<_> = text.split_whitespace().collect();
    let digest = fields.first().ok_or(StoreError::Corrupt)?;
    if !crate::is_hash(digest)
        || fields.len() > 2
        || fields.get(1).is_some_and(|name| {
            name.strip_prefix('*').unwrap_or(name) != format!("epoch-{epoch}.car")
        })
    {
        return Err(StoreError::Corrupt);
    }
    Ok((*digest).into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn declared_hash_is_not_whole_object_verification_or_a_foreign_filename() {
        let digest = "a".repeat(64);
        for text in [
            digest.clone(),
            format!("{digest}\n"),
            format!("{digest}  epoch-978.car\n"),
            format!("{digest} *epoch-978.car"),
        ] {
            assert_eq!(declared_sha256(text.as_bytes(), 978).unwrap(), digest);
        }
        for text in [
            String::new(),
            "A".repeat(64),
            format!("{digest} epoch-979.car"),
            format!("{digest} **epoch-978.car"),
            format!("{digest} ../epoch-978.car"),
            format!("{digest} epoch-978.car extra"),
        ] {
            assert!(declared_sha256(text.as_bytes(), 978).is_err());
        }
    }

    #[test]
    fn bounded_regular_input_rejects_oversize_and_symlinks() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("input");
        std::fs::write(&path, b"abc").unwrap();
        assert_eq!(read_limited(&path, 3).unwrap(), b"abc");
        assert!(matches!(read_limited(&path, 2), Err(StoreError::Budget)));
        assert!(read_limited(dir.path(), 3).is_err());
        std::os::unix::fs::symlink(&path, dir.path().join("link")).unwrap();
        assert!(read_limited(&dir.path().join("link"), 3).is_err());
        let fifo = dir.path().join("fifo");
        rustix::fs::mkfifoat(rustix::fs::CWD, &fifo, rustix::fs::Mode::RUSR).unwrap();
        assert!(read_limited(&fifo, 3).is_err());
    }
}

/// Derive the only admissible payload inventory from four verified stored metadata objects.
/// This produces no network authority; admission rederives it from its own committed Raw.
/// # Errors
/// Incompatible metadata, absent modern index, invalid records, unknown identities or
/// impossible bounded planning fail closed without fetching another index or provider.
pub fn derive_payload_from_metadata(
    aggregate: &AggregatePlan,
    metadata: &[Published],
    start_slot: u64,
    end_slot: u64,
) -> StoreResult<PreparedPayload> {
    let metadata_receipt_sha256 = metadata_receipt_sha256(metadata)?;
    let index = raw(&metadata[0], SLOTS_PER_EPOCH * RECORD_BYTES)?;
    let index_sha256 = sha256(&index);
    let declared_car_sha256 = declared_sha256(&raw(&metadata[1], 4096)?, aggregate.epoch)?;
    let cid_bytes = raw(&metadata[2], 4096)?;
    let root_cid =
        car::decode_reported_cid(std::str::from_utf8(&cid_bytes).map_err(|_| StoreError::Corrupt)?)
            .map_err(|_| StoreError::Corrupt)?;
    let head = &metadata[3].receipt.response;
    if head.content_length == 0 || metadata[3].receipt.response_entity_bytes != 0 {
        return Err(StoreError::Corrupt);
    }
    let object_size = head.content_length;
    let b = &aggregate.budget;
    let budget = Budget {
        max_slots: b.max_slots,
        max_requests: b.max_requests,
        max_response_entity_bytes: b.max_response_entity_bytes,
        max_total_response_entity_bytes: b.max_total_response_entity_bytes,
        max_index_bytes: SLOTS_PER_EPOCH * RECORD_BYTES,
        max_plan_entry_bytes: b.max_plan_bytes,
        max_disk_bytes: b.max_disk_bytes,
        max_runtime_ms: b.max_runtime_ms,
        response_timeout_ms: b.response_timeout_ms,
        request_retries: b.request_retries,
        concurrency: 1,
    };
    let selection = IndexSelection {
        epoch_first_slot: aggregate
            .epoch
            .checked_mul(SLOTS_PER_EPOCH)
            .ok_or(StoreError::Corrupt)?,
        start_slot,
        end_slot,
        object_size,
        budget: &budget,
    };
    let planned = plan_index_requests(&index, &selection).map_err(|e| match e {
        crate::Error::Budget => StoreError::Budget,
        _ => StoreError::Corrupt,
    })?;
    let requests = planned
        .requests
        .into_iter()
        .map(|request| Request {
            sequence: request.sequence + 4,
            kind: RequestKind::CarRange {
                slot: request.slot,
                start: request.start,
                end_exclusive: request.end_exclusive,
                total: object_size,
                strong_etag: head.strong_etag.clone(),
            },
        })
        .collect();
    let source_fingerprint = sha256(
        &serde_json::to_vec(&(
            &aggregate.format_source,
            &index_sha256,
            object_size,
            &head.strong_etag,
            &declared_car_sha256,
            &root_cid,
        ))
        .map_err(|_| StoreError::Corrupt)?,
    );
    Ok(PreparedPayload {
        metadata_receipt_sha256,
        index_sha256,
        source_fingerprint,
        object_size,
        strong_etag: head.strong_etag.clone(),
        declared_car_sha256,
        root_cid,
        start_slot,
        end_slot,
        requests,
        index_reported_absent: planned.absent,
    })
}

pub(crate) fn verify_prepared_payload(
    aggregate: &AggregatePlan,
    metadata: &[Published],
    prepared: &PreparedPayload,
) -> StoreResult<()> {
    if &derive_payload_from_metadata(aggregate, metadata, prepared.start_slot, prepared.end_slot)?
        != prepared
    {
        return Err(StoreError::Identity);
    }
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct PayloadIntegrityReport {
    pub schema: &'static str,
    pub metadata_receipt_sha256: String,
    pub prepared_payload_sha256: String,
    pub index_sha256: String,
    pub declared_car_sha256: String,
    pub whole_car_sha256_verified: bool,
    pub root_to_slot_membership: &'static str,
    pub domain_counts: &'static str,
    pub edge_evaluation: &'static str,
    pub index_reported_absent: Vec<u64>,
    pub slots: Vec<car::SlotIntegrityReport>,
}

/// Verify complete already-published slot ranges offline; no missing proof is fetched.
/// # Errors
/// Missing/mixed ranges, excess allocation, CID mismatch or an invalid archival closure
/// reject integrity promotion. Raw and receipts remain available in the durable store.
pub fn verify_payload<C: Clock>(
    store: &AcquisitionStore<C>,
    prepared: &PreparedPayload,
    limits: car::VerificationLimits,
) -> StoreResult<PayloadIntegrityReport> {
    if store.prepared_payload() != Some(prepared) {
        return Err(StoreError::Identity);
    }
    let mut slots = BTreeMap::<u64, (u64, Vec<u8>)>::new();
    let mut assembled = 0u64;
    for request in &prepared.requests {
        let RequestKind::CarRange {
            slot,
            start,
            end_exclusive,
            ..
        } = &request.kind
        else {
            return Err(StoreError::Corrupt);
        };
        let published = store
            .published(request.sequence)?
            .ok_or(StoreError::Corrupt)?;
        if published.receipt.request != *request {
            return Err(StoreError::Identity);
        }
        let entry = slots.entry(*slot).or_insert((*start, Vec::new()));
        if entry.0 != *start {
            return Err(StoreError::Corrupt);
        }
        let length = end_exclusive - start;
        assembled = assembled.checked_add(length).ok_or(StoreError::Budget)?;
        if assembled > limits.max_total_bytes as u64 {
            return Err(StoreError::Budget);
        }
        entry
            .1
            .try_reserve_exact(usize::try_from(length).map_err(|_| StoreError::Budget)?)
            .map_err(|_| StoreError::Budget)?;
        entry.1.extend(raw(&published, length)?);
        entry.0 = *end_exclusive;
    }
    let mut verified = Vec::new();
    for (slot, (_, bytes)) in slots {
        verified.push(
            car::verify_slot_sections(slot, &bytes, limits)
                .map_err(|error| StoreError::Integrity(error.to_string()))?,
        );
    }
    Ok(PayloadIntegrityReport {
        schema: "OF1_PAYLOAD_INTEGRITY_1",
        metadata_receipt_sha256: prepared.metadata_receipt_sha256.clone(),
        prepared_payload_sha256: prepared.sha256()?,
        index_sha256: prepared.index_sha256.clone(),
        declared_car_sha256: prepared.declared_car_sha256.clone(),
        whole_car_sha256_verified: false,
        root_to_slot_membership: "UNAVAILABLE",
        domain_counts: "UNAVAILABLE_NOT_DECODED_IN_B4",
        edge_evaluation: "NOT_EVALUATED_ENGINEERING_SLICE",
        index_reported_absent: prepared.index_reported_absent.clone(),
        slots: verified,
    })
}
