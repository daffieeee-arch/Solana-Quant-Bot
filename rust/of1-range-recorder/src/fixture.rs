//! Synthetic format fixtures, never observed OF1 data. No payload or transport implementation.

use crate::{
    Budget, Error, FormatSource, OfflinePlan, RECORD_BYTES, Result, SCHEMA, SLOTS_PER_EPOCH, sha256,
};

pub const INDEX_SHA256: &str = "3919f15479264300e451c53ce7b276cf90391488b9cbbd7f4c9c1ea70f41c60c";

/// Expand a small sealed record set into the exact epoch-index shape.
/// # Errors
/// Reports unrepresentable local sizes or a changed sealed fixture hash.
pub fn index_bytes() -> Result<Vec<u8>> {
    let mut bytes =
        vec![0; usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).map_err(|_| Error::Overflow)?];
    for (record, offset, length) in [(10usize, 128u64, 64u32), (11, 192, 80), (13, 400, 16)] {
        let pos = record * 12;
        bytes[pos..pos + 8].copy_from_slice(&offset.to_le_bytes());
        bytes[pos + 8..pos + 12].copy_from_slice(&length.to_le_bytes());
    }
    if sha256(&bytes) != INDEX_SHA256 {
        return Err(Error::IndexHash);
    }
    Ok(bytes)
}

#[must_use]
pub fn plan() -> OfflinePlan {
    OfflinePlan {
        schema: SCHEMA.into(),
        mode: "OFFLINE_FIXTURE".into(),
        slice_class: "ENGINEERING_VALIDATION_ONLY".into(),
        approved: false,
        network_enabled: false,
        format_source: FormatSource::pinned(),
        epoch: 978,
        epoch_first_slot: 422_496_000,
        epoch_end_exclusive: 422_928_000,
        start_slot: 422_496_010,
        end_slot: 422_496_014,
        object_size: 1024,
        index_sha256: INDEX_SHA256.into(),
        // These are explicitly fixture-context fingerprints, NOT runtime/code attestations.
        source_fingerprint: sha256(b"FIXTURE_ONLY:synthetic-car-object-size-1024"),
        code_fingerprint: sha256(b"FIXTURE_ONLY:not-a-runtime-code-attestation"),
        toolchain_fingerprint: sha256(b"FIXTURE_ONLY:not-a-runtime-toolchain-attestation"),
        budget: Budget {
            max_slots: 4,
            max_requests: 6,
            max_response_entity_bytes: 32,
            max_total_response_entity_bytes: 160,
            max_index_bytes: SLOTS_PER_EPOCH * RECORD_BYTES,
            max_plan_entry_bytes: 65_536,
            max_disk_bytes: 6 * 1024 * 1024,
            max_runtime_ms: 60_000,
            response_timeout_ms: 1000,
            request_retries: 0,
            concurrency: 1,
        },
    }
}
