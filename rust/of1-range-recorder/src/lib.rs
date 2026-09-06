//! Source-bound index planning, durable Raw/receipts, staged admission and offline CAR checks.
//! Default builds have no socket transport. `network-of1` exposes only fixed-host HTTPS
//! after separate metadata/payload approval; fixture connectors never confer that authority.

pub mod acquisition;
pub mod acquisition_http;
pub mod car;
pub mod dataset_location;
pub mod durable;
pub mod fixture;
#[cfg(any(feature = "network-of1", feature = "tls-fixture"))]
pub mod https;
#[cfg(feature = "loopback-fixture")]
pub mod transport;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fs::File, io::Read, path::Path};
use thiserror::Error;

pub const SCHEMA: &str = "OF1_OFFLINE_PLAN_1";
pub const SOURCE_COMMIT: &str = "cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24";
pub const SOURCE_PATH: &str = "jetstreamer-firehose/src/index.rs";
pub const SOURCE_BLOB: &str = "bb9096d56f609b29e3ff063dc49ff4b1b4ef6ea2";
pub const SOURCE_SHA256: &str = "c963bc80e8daeb94b75aac802bd96b1a6cf78defbb29e0fac431b8c150c9f83b";
pub const HOST: &str = "files.old-faithful.net:443";
// Pinned OF1 source convention, not a universal statement about historical chain epochs.
pub const SLOTS_PER_EPOCH: u64 = 432_000;
pub const RECORD_BYTES: u64 = 12;

#[derive(Debug, Error)]
pub enum Error {
    #[error("unsupported schema or source identity")]
    Identity,
    #[error("fixture planner cannot accept acquisition authority")]
    Authority,
    #[error("invalid slot range or epoch bounds")]
    SlotRange,
    #[error("full epoch is outside bounded acquisition scope")]
    FullEpoch,
    #[error("invalid or insufficient explicit budget")]
    Budget,
    #[error("index must be a bounded regular file of the exact epoch size")]
    IndexSize,
    #[error("persisted index SHA-256 differs from the plan")]
    IndexHash,
    #[error("malformed, overlapping or out-of-object index range at slot {0}")]
    IndexRecord(u64),
    #[error("integer overflow")]
    Overflow,
    #[error("local I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("schema decoding failed: {0}")]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FormatSource {
    pub repository: String,
    pub commit: String,
    pub path: String,
    pub blob: String,
    pub sha256: String,
}

impl FormatSource {
    #[must_use]
    pub fn pinned() -> Self {
        Self {
            repository: "anza-xyz/jetstreamer".into(),
            commit: SOURCE_COMMIT.into(),
            path: SOURCE_PATH.into(),
            blob: SOURCE_BLOB.into(),
            sha256: SOURCE_SHA256.into(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Budget {
    pub max_slots: u64,
    pub max_requests: u64,
    pub max_response_entity_bytes: u64,
    pub max_total_response_entity_bytes: u64,
    pub max_index_bytes: u64,
    pub max_plan_entry_bytes: u64,
    pub max_disk_bytes: u64,
    pub max_runtime_ms: u64,
    pub response_timeout_ms: u64,
    pub request_retries: u32,
    pub concurrency: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OfflinePlan {
    pub schema: String,
    pub mode: String,
    pub slice_class: String,
    pub approved: bool,
    pub network_enabled: bool,
    pub format_source: FormatSource,
    pub epoch: u64,
    pub epoch_first_slot: u64,
    pub epoch_end_exclusive: u64,
    pub start_slot: u64,
    pub end_slot: u64,
    pub object_size: u64,
    pub index_sha256: String,
    pub source_fingerprint: String,
    pub code_fingerprint: String,
    pub toolchain_fingerprint: String,
    pub budget: Budget,
}

// Private fields prevent callers from manufacturing validated plans/indexes.
pub struct ValidatedPlan(OfflinePlan);

impl ValidatedPlan {
    #[must_use]
    pub fn plan(&self) -> &OfflinePlan {
        &self.0
    }

    /// Fixed production source identity only; the optional fixture transport cannot contact it.
    #[must_use]
    pub fn source_path(&self) -> String {
        format!("/{0}/epoch-{0}.car", self.0.epoch)
    }

    #[must_use]
    pub fn index_path(&self) -> String {
        format!("/{0}/epoch-{0}-slot-ranges.raw", self.0.epoch)
    }
}

fn is_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// Validate a fixture-only plan. Does not attest caller-provided provenance or authorize I/O.
/// # Errors
/// Rejects unsupported identity/authority, invalid ranges, overflow and impossible budgets.
pub fn validate_plan(plan: OfflinePlan) -> Result<ValidatedPlan> {
    if plan.schema != SCHEMA || plan.format_source != FormatSource::pinned() {
        return Err(Error::Identity);
    }
    if plan.mode != "OFFLINE_FIXTURE"
        || plan.slice_class != "ENGINEERING_VALIDATION_ONLY"
        || plan.approved
        || plan.network_enabled
    {
        return Err(Error::Authority);
    }
    for fingerprint in [
        &plan.index_sha256,
        &plan.source_fingerprint,
        &plan.code_fingerprint,
        &plan.toolchain_fingerprint,
    ] {
        if !is_hash(fingerprint) {
            return Err(Error::Identity);
        }
    }
    let first = plan
        .epoch
        .checked_mul(SLOTS_PER_EPOCH)
        .ok_or(Error::Overflow)?;
    let end = first.checked_add(SLOTS_PER_EPOCH).ok_or(Error::Overflow)?;
    if first != plan.epoch_first_slot
        || end != plan.epoch_end_exclusive
        || plan.start_slot < first
        || plan.end_slot > end
        || plan.start_slot >= plan.end_slot
    {
        return Err(Error::SlotRange);
    }
    if plan.start_slot == first && plan.end_slot == end {
        return Err(Error::FullEpoch);
    }
    let b = &plan.budget;
    if [
        b.max_slots,
        b.max_requests,
        b.max_response_entity_bytes,
        b.max_total_response_entity_bytes,
        b.max_index_bytes,
        b.max_plan_entry_bytes,
        b.max_disk_bytes,
        b.max_runtime_ms,
        b.response_timeout_ms,
        plan.object_size,
    ]
    .contains(&0)
        || b.concurrency != 1
        || plan.end_slot - plan.start_slot > b.max_slots
        || b.max_response_entity_bytes > b.max_total_response_entity_bytes
        || b.max_index_bytes < SLOTS_PER_EPOCH * RECORD_BYTES
        || b.max_disk_bytes < SLOTS_PER_EPOCH * RECORD_BYTES
        || b.response_timeout_ms > b.max_runtime_ms
    {
        return Err(Error::Budget);
    }
    Ok(ValidatedPlan(plan))
}

pub struct PersistedIndex {
    bytes: Vec<u8>,
    sha256: String,
}

impl PersistedIndex {
    /// Read an existing file before interpreting its records. This is not a durable capture API.
    /// # Errors
    /// Rejects nonregular, incomplete, oversized or hash-mismatched persisted indexes.
    pub fn read(plan: &ValidatedPlan, path: &Path) -> Result<Self> {
        if !std::fs::symlink_metadata(path)?.is_file() {
            return Err(Error::IndexSize);
        }
        let file = File::open(path)?;
        let expected = SLOTS_PER_EPOCH * RECORD_BYTES;
        if !file.metadata()?.is_file() || file.metadata()?.len() != expected {
            return Err(Error::IndexSize);
        }
        let mut bytes = Vec::new();
        file.take(expected + 1).read_to_end(&mut bytes)?;
        if u64::try_from(bytes.len()).map_err(|_| Error::Overflow)? != expected {
            return Err(Error::IndexSize);
        }
        let sha256 = sha256(&bytes);
        if sha256 != plan.0.index_sha256 {
            return Err(Error::IndexHash);
        }
        Ok(Self { bytes, sha256 })
    }
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ByteRequest {
    pub sequence: u64,
    pub slot: u64,
    pub index_record: u64,
    pub start: u64,
    pub end_exclusive: u64,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RangePlan {
    pub schema: String,
    pub format_source: FormatSource,
    pub plan_sha256: String,
    pub index_sha256: String,
    pub source_host: String,
    pub source_path: String,
    pub index_path: String,
    pub requests: Vec<ByteRequest>,
    pub index_reported_absent: Vec<u64>,
    pub planned_response_entity_bytes: u64,
    pub evidence: String,
    pub slot_semantic_membership: String,
    pub root_membership: String,
    pub cid_verification: String,
}

/// Derive requests only from the persisted, hash-bound modern index; no fallback or full CAR.
/// # Errors
/// Rejects stale index identity, invalid/overlapping records, overflow or exceeded plan budgets.
pub fn plan_ranges(plan: &ValidatedPlan, index: &PersistedIndex) -> Result<RangePlan> {
    let p = &plan.0;
    if index.sha256 != p.index_sha256 {
        return Err(Error::IndexHash);
    }
    let IndexRanges {
        requests,
        absent,
        total,
    } = plan_index_requests(
        &index.bytes,
        &IndexSelection {
            epoch_first_slot: p.epoch_first_slot,
            start_slot: p.start_slot,
            end_slot: p.end_slot,
            object_size: p.object_size,
            budget: &p.budget,
        },
    )?;
    Ok(RangePlan {
        schema: "OF1_OFFLINE_RANGE_PLAN_1".into(),
        format_source: p.format_source.clone(),
        plan_sha256: sha256(&serde_json::to_vec(p)?),
        index_sha256: index.sha256.clone(),
        source_host: HOST.into(),
        source_path: plan.source_path(),
        index_path: plan.index_path(),
        requests,
        index_reported_absent: absent,
        planned_response_entity_bytes: total,
        evidence: "Fixture".into(),
        slot_semantic_membership: "UNAVAILABLE_NOT_DECODED_IN_B4".into(),
        root_membership: "UNAVAILABLE".into(),
        cid_verification: "UNAVAILABLE".into(),
    })
}

/// Shared index arithmetic only: this grants no transport or evidence authority.
/// Fixture validation and receipt-bound acquisition admission remain separate.
pub(crate) struct IndexSelection<'a> {
    pub epoch_first_slot: u64,
    pub start_slot: u64,
    pub end_slot: u64,
    pub object_size: u64,
    pub budget: &'a Budget,
}

pub(crate) struct IndexRanges {
    pub requests: Vec<ByteRequest>,
    pub absent: Vec<u64>,
    pub total: u64,
}

pub(crate) fn plan_index_requests(index: &[u8], p: &IndexSelection<'_>) -> Result<IndexRanges> {
    let epoch_end = p
        .epoch_first_slot
        .checked_add(SLOTS_PER_EPOCH)
        .ok_or(Error::Overflow)?;
    if index.len() as u64 != SLOTS_PER_EPOCH * RECORD_BYTES {
        return Err(Error::IndexSize);
    }
    if p.start_slot < p.epoch_first_slot || p.start_slot >= p.end_slot || p.end_slot > epoch_end {
        return Err(Error::SlotRange);
    }
    if p.end_slot - p.start_slot == SLOTS_PER_EPOCH {
        return Err(Error::FullEpoch);
    }
    if p.object_size == 0
        || p.budget.max_response_entity_bytes == 0
        || p.end_slot - p.start_slot > p.budget.max_slots
    {
        return Err(Error::Budget);
    }
    let mut requests = Vec::new();
    let mut absent = Vec::new();
    let mut previous_end = 0;
    let mut total: u64 = 0;
    for slot in p.start_slot..p.end_slot {
        let record_number = slot - p.epoch_first_slot;
        let pos = usize::try_from(record_number * RECORD_BYTES).map_err(|_| Error::Overflow)?;
        let record = &index[pos..pos + 12];
        let offset = u64::from_le_bytes(record[..8].try_into().map_err(|_| Error::IndexSize)?);
        let length = u64::from(u32::from_le_bytes(
            record[8..].try_into().map_err(|_| Error::IndexSize)?,
        ));
        if offset == 0 && length == 0 {
            check_entry_budget(
                requests.len(),
                absent.len() + 1,
                p.budget.max_plan_entry_bytes,
            )?;
            absent.try_reserve_exact(1).map_err(|_| Error::Budget)?;
            absent.push(slot);
            continue;
        }
        let end = offset.checked_add(length).ok_or(Error::Overflow)?;
        if offset == 0 || length == 0 || offset < previous_end || end > p.object_size {
            return Err(Error::IndexRecord(slot));
        }
        previous_end = end;
        total = total.checked_add(length).ok_or(Error::Overflow)?;
        if total > p.budget.max_total_response_entity_bytes {
            return Err(Error::Budget);
        }
        // Reject impossible disk/request/output plans BEFORE materializing their chunks.
        if total
            .checked_add(SLOTS_PER_EPOCH * RECORD_BYTES)
            .ok_or(Error::Overflow)?
            > p.budget.max_disk_bytes
        {
            return Err(Error::Budget);
        }
        let chunks = usize::try_from(length.div_ceil(p.budget.max_response_entity_bytes))
            .map_err(|_| Error::Overflow)?;
        let next_count = requests.len().checked_add(chunks).ok_or(Error::Overflow)?;
        if u64::try_from(next_count).map_err(|_| Error::Overflow)? > p.budget.max_requests {
            return Err(Error::Budget);
        }
        check_entry_budget(next_count, absent.len(), p.budget.max_plan_entry_bytes)?;
        requests
            .try_reserve_exact(chunks)
            .map_err(|_| Error::Budget)?;
        let mut start = offset;
        while start < end {
            if u64::try_from(requests.len()).map_err(|_| Error::Overflow)? >= p.budget.max_requests
            {
                return Err(Error::Budget);
            }
            let next = start + (end - start).min(p.budget.max_response_entity_bytes);
            requests.push(ByteRequest {
                sequence: u64::try_from(requests.len()).map_err(|_| Error::Overflow)?,
                slot,
                index_record: record_number,
                start,
                end_exclusive: next,
            });
            start = next;
        }
    }
    // Minimum size check only. Runtime disk/metadata reservations belong to the durable engine.
    if total
        .checked_add(SLOTS_PER_EPOCH * RECORD_BYTES)
        .ok_or(Error::Overflow)?
        > p.budget.max_disk_bytes
    {
        return Err(Error::Budget);
    }
    Ok(IndexRanges {
        requests,
        absent,
        total,
    })
}

#[must_use]
pub fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn check_entry_budget(requests: usize, absent: usize, cap: u64) -> Result<()> {
    let bytes = requests
        .checked_mul(std::mem::size_of::<ByteRequest>())
        .and_then(|n| {
            absent
                .checked_mul(std::mem::size_of::<u64>())
                .and_then(|m| n.checked_add(m))
        })
        .ok_or(Error::Overflow)?;
    if u64::try_from(bytes).map_err(|_| Error::Overflow)? > cap {
        return Err(Error::Budget);
    }
    Ok(())
}
