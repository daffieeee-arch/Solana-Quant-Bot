//! Two-stage acquisition admission and immutable storage, using the fixture store's
//! reservation, directory-publication, clock and filesystem primitives. Metadata is
//! captured before its hash exists; payload authority is a separately bound lease.
//! A locked writer caches validated segments. Full audits occur at resume, reserve,
//! admission and publication, never once per short socket read.

use super::{
    BLOCK, Clock, ClockSample, FaultPoint, RetryComparison, StoreError, StoreResult, children,
    clock_follows, decode, disk_charge, encode, exact_names, executable_hash, identity,
    read_bounded, regular, rounded, sync_dir, valid_clock, valid_etag,
};
use crate::{FormatSource, HOST, RECORD_BYTES, SLOTS_PER_EPOCH, sha256};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

pub const SEGMENT_BYTES: usize = 65_536;
const RECORD_LIMIT: u64 = 131_072;
const MANIFEST_LIMIT: u64 = 1_048_576;
pub const AGGREGATE_SCHEMA: &str = "OF1_ACQUISITION_AGGREGATE_1";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AggregateBudget {
    pub max_slots: u64,
    pub max_plan_bytes: u64,
    pub max_requests: u64,
    pub max_response_entity_bytes: u64,
    pub max_total_response_entity_bytes: u64,
    pub max_disk_bytes: u64,
    pub required_free_disk_bytes: u64,
    pub max_memory_bytes: u64,
    pub max_runtime_ms: u64,
    pub response_timeout_ms: u64,
    pub request_retries: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AggregatePlan {
    pub schema: String,
    pub epoch: u64,
    pub format_source: FormatSource,
    /// Declared and approval-bound source identity; not independent build attestation.
    pub code_sha: String,
    pub toolchain_fingerprint: String,
    /// Compared with the running executable, including on restart.
    pub executable_sha256: String,
    pub budget: AggregateBudget,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "SCREAMING_SNAKE_CASE", deny_unknown_fields)]
pub enum Authority {
    Fixture,
    Approved {
        approval_id: String,
        operator: String,
        approved_at_ms: u64,
        not_after_ms: u64,
        approved_plan_sha256: String,
        cost_confirmation: String,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct StageBudget {
    pub max_requests: u64,
    pub max_response_entity_bytes_total: u64,
    pub max_runtime_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct MetadataLease {
    pub schema: String,
    pub authority: Authority,
    pub budget: StageBudget,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PayloadLease {
    pub schema: String,
    pub authority: Authority,
    pub budget: StageBudget,
    pub prepared_payload_sha256: String,
    pub metadata_receipt_sha256: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "SCREAMING_SNAKE_CASE", deny_unknown_fields)]
pub enum RequestKind {
    Index,
    CarSha256,
    CarCid,
    CarHead,
    CarRange {
        slot: u64,
        start: u64,
        end_exclusive: u64,
        total: u64,
        strong_etag: Option<String>,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub sequence: u64,
    pub kind: RequestKind,
}

impl Request {
    #[must_use]
    pub fn method(&self) -> &'static str {
        if matches!(self.kind, RequestKind::CarHead) {
            "HEAD"
        } else {
            "GET"
        }
    }

    #[must_use]
    pub fn path(&self, epoch: u64) -> String {
        let suffix = match self.kind {
            RequestKind::Index => "-slot-ranges.raw",
            RequestKind::CarSha256 => ".sha256",
            RequestKind::CarCid => ".cid",
            RequestKind::CarHead | RequestKind::CarRange { .. } => ".car",
        };
        format!("/{epoch}/epoch-{epoch}{suffix}")
    }

    #[must_use]
    pub fn allowance(&self) -> u64 {
        match self.kind {
            RequestKind::Index => SLOTS_PER_EPOCH * RECORD_BYTES,
            RequestKind::CarSha256 | RequestKind::CarCid => 4096,
            RequestKind::CarHead => 0,
            RequestKind::CarRange {
                start,
                end_exclusive,
                ..
            } => end_exclusive.saturating_sub(start),
        }
    }

    /// # Errors
    /// Rejects unsupported framing, identity or size before entity reads.
    pub fn entity_length(&self, head: &ResponseHead) -> StoreResult<u64> {
        if !valid_etag(head.strong_etag.as_deref()) {
            return Err(StoreError::Corrupt);
        }
        if let RequestKind::CarRange {
            start,
            end_exclusive,
            total,
            strong_etag,
            ..
        } = &self.kind
        {
            if head
                .content_range
                .as_ref()
                .is_some_and(|r| r.total != *total)
            {
                return Err(StoreError::SourceDrift);
            }
            if head.status != 206
                || head.content_range
                    != Some(RangeResponse {
                        start: *start,
                        end_exclusive: *end_exclusive,
                        total: *total,
                    })
                || head.content_length != self.allowance()
            {
                return Err(StoreError::Corrupt);
            }
            if &head.strong_etag != strong_etag {
                return Err(StoreError::SourceDrift);
            }
            Ok(head.content_length)
        } else {
            if head.status != 200 || head.content_range.is_some() {
                return Err(StoreError::Corrupt);
            }
            if matches!(self.kind, RequestKind::CarHead) {
                if head.content_length == 0 {
                    return Err(StoreError::Corrupt);
                }
                return Ok(0);
            }
            if head.content_length == 0
                || head.content_length > self.allowance()
                || matches!(self.kind, RequestKind::Index)
                    && head.content_length != self.allowance()
            {
                return Err(StoreError::Corrupt);
            }
            Ok(head.content_length)
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RangeResponse {
    pub start: u64,
    pub end_exclusive: u64,
    pub total: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ResponseHead {
    pub status: u16,
    pub content_length: u64,
    pub content_range: Option<RangeResponse>,
    pub strong_etag: Option<String>,
    pub raw_headers: Vec<u8>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PreparedPayload {
    pub(crate) metadata_receipt_sha256: String,
    pub(crate) index_sha256: String,
    pub(crate) source_fingerprint: String,
    pub(crate) object_size: u64,
    pub(crate) strong_etag: Option<String>,
    pub(crate) declared_car_sha256: String,
    pub(crate) root_cid: Vec<u8>,
    pub(crate) start_slot: u64,
    pub(crate) end_slot: u64,
    pub(crate) requests: Vec<Request>,
    pub(crate) index_reported_absent: Vec<u64>,
}

impl PreparedPayload {
    /// # Errors
    /// Serialization failure denies admission.
    pub fn sha256(&self) -> StoreResult<String> {
        Ok(sha256(&encode(self)?))
    }
    #[must_use]
    pub fn requests(&self) -> &[Request] {
        &self.requests
    }
    #[must_use]
    pub fn metadata_receipt_sha256(&self) -> &str {
        &self.metadata_receipt_sha256
    }
    #[must_use]
    pub fn index_sha256(&self) -> &str {
        &self.index_sha256
    }
    #[must_use]
    pub fn start_slot(&self) -> u64 {
        self.start_slot
    }
    #[must_use]
    pub fn end_slot(&self) -> u64 {
        self.end_slot
    }
    #[must_use]
    pub fn index_reported_absent(&self) -> &[u64] {
        &self.index_reported_absent
    }
}

#[must_use]
pub fn metadata_requests() -> Vec<Request> {
    [
        RequestKind::Index,
        RequestKind::CarSha256,
        RequestKind::CarCid,
        RequestKind::CarHead,
    ]
    .into_iter()
    .enumerate()
    .map(|(sequence, kind)| Request {
        sequence: sequence as u64,
        kind,
    })
    .collect()
}

/// # Errors
/// Serialization failure is fail-closed; approval receipt itself is not self-hashed.
pub fn metadata_proposal_sha256(plan: &AggregatePlan, budget: &StageBudget) -> StoreResult<String> {
    Ok(sha256(&encode(&(
        "OF1_METADATA_PROPOSAL_1",
        plan,
        budget,
        metadata_requests(),
    ))?))
}

/// # Errors
/// Serialization failure denies admission.
pub fn payload_proposal_sha256(
    plan: &AggregatePlan,
    budget: &StageBudget,
    prepared: &PreparedPayload,
) -> StoreResult<String> {
    Ok(sha256(&encode(&(
        "OF1_PAYLOAD_PROPOSAL_1",
        plan,
        budget,
        prepared,
    ))?))
}

/// # Errors
/// An unreadable executable cannot establish a restart identity.
pub fn current_executable_sha256() -> StoreResult<String> {
    executable_hash()
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct StageRecord {
    authority: Authority,
    budget: StageBudget,
    lease_sha256: String,
    started_at: ClockSample,
    deadline_wall_ms: u64,
    deadline_boot_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    run_id: String,
    aggregate_sha256: String,
    plan: AggregatePlan,
    metadata_lease: MetadataLease,
    metadata_stage: StageRecord,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct PayloadRecord {
    lease: PayloadLease,
    stage: StageRecord,
    prepared: PreparedPayload,
    metadata_attempt_count: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Reservation {
    run_id: String,
    aggregate_sha256: String,
    lease_sha256: String,
    attempt_id: u64,
    request: Request,
    reserved_entity_bytes: u64,
    at: ClockSample,
}

pub struct Permit(Reservation);

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct StreamIdentity {
    reservation: Reservation,
    head: ResponseHead,
    at: ClockSample,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct SegmentReceipt {
    attempt_id: u64,
    offset: u64,
    length: u64,
    sha256: String,
    at: ClockSample,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub schema: String,
    pub run_id: String,
    pub aggregate_sha256: String,
    pub lease_sha256: String,
    pub attempt_id: u64,
    pub request: Request,
    pub source_host: String,
    pub source_path: String,
    pub response: ResponseHead,
    pub response_headers_sha256: String,
    pub response_entity_bytes: u64,
    pub sha256: String,
    pub acquired_at: ClockSample,
    pub retry_comparison: RetryComparison,
    pub evidence: String,
    pub domain_counts: String,
}

#[derive(Clone, Debug)]
pub struct Published {
    pub receipt: Receipt,
    pub raw_path: PathBuf,
}

#[derive(Clone, Debug, Serialize)]
pub struct Progress {
    pub stage: String,
    pub attempts_reserved: u64,
    pub charged_entity_bytes: u64,
    pub published_response_entity_bytes: u64,
    pub published_requests: u64,
    pub unpublished_attempts: u64,
    pub unreceipted_response_entity_bytes: Option<u64>,
    pub disk_charge_bytes: u64,
    pub current_rss_bytes: u64,
    pub peak_rss_bytes: u64,
    pub available_disk_bytes: u64,
    pub deadline_wall_ms: u64,
    pub deadline_boot_ms: u64,
    pub current_lease_sha256: String,
    pub evidence: String,
    pub domain_counts: String,
}

#[derive(Clone)]
struct StreamRecord {
    identity: StreamIdentity,
    bytes: Vec<u8>,
    segments: u64,
    latest_at: ClockSample,
}

#[derive(Default)]
struct Audit {
    attempts: Vec<Reservation>,
    streams: BTreeMap<u64, StreamRecord>,
    published: BTreeMap<u64, Published>,
}

pub struct AcquisitionStore<C: Clock> {
    root: PathBuf,
    lock: File,
    root_identity: (u64, u64),
    manifest: Manifest,
    manifest_bytes: Vec<u8>,
    payload: Option<PayloadRecord>,
    clock: C,
    high_water: ClockSample,
    cache: Audit,
    inflight: Option<u64>,
    poisoned: bool,
    fault: Option<FaultPoint>,
}

impl<C: Clock> AcquisitionStore<C> {
    /// Capture authority is separate from the historical fixture-only plan schema.
    /// # Errors
    /// Invalid identities, unsupported authority, an existing root or resource failure deny creation.
    pub fn create(
        root: &Path,
        plan: AggregatePlan,
        lease: MetadataLease,
        clock: C,
    ) -> StoreResult<Self> {
        validate_aggregate(&plan)?;
        validate_metadata(&plan, &lease)?;
        let started = clock.sample()?;
        valid_clock(&started)?;
        let stage = make_stage(&lease.authority, &lease.budget, &lease, &started)?;
        resources(root.parent().ok_or(StoreError::Identity)?, &plan.budget)?;
        let mut nonce = [0u8; 32];
        File::open("/dev/urandom")?.read_exact(&mut nonce)?;
        let manifest = Manifest {
            schema: "OF1_ACQUISITION_STORE_1".into(),
            run_id: hex::encode(nonce),
            aggregate_sha256: sha256(&encode(&plan)?),
            plan,
            metadata_lease: lease,
            metadata_stage: stage,
        };
        let manifest_bytes = encode(&manifest)?;
        fs::create_dir(root)?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(root.join("writer.lock"))?;
        lock.try_lock().map_err(|_| StoreError::Locked)?;
        let mut store = Self {
            root: root.into(),
            root_identity: identity(&fs::symlink_metadata(root)?),
            lock,
            manifest,
            manifest_bytes,
            payload: None,
            clock,
            high_water: started,
            cache: Audit::default(),
            inflight: None,
            poisoned: false,
            fault: None,
        };
        for name in ["attempts", "pending", "published"] {
            fs::create_dir(root.join(name))?;
        }
        store.write_new(&root.join("run.json"), &store.manifest_bytes)?;
        sync_dir(root)?;
        sync_dir(root.parent().ok_or(StoreError::Identity)?)?;
        store.refresh()?;
        Ok(store)
    }

    /// Expiration forbids dispatch, not forensic inspection or a separately approved next stage.
    /// # Errors
    /// Revalidates executable, plan, current lease, every captured hash, durable charge and clock.
    pub fn resume(
        root: &Path,
        plan: &AggregatePlan,
        expected_current_lease_sha256: &str,
        clock: C,
    ) -> StoreResult<Self> {
        validate_aggregate(plan)?;
        let root_meta = fs::symlink_metadata(root)?;
        if !root_meta.is_dir() {
            return Err(StoreError::Identity);
        }
        regular(&root.join("writer.lock"))?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .open(root.join("writer.lock"))?;
        lock.try_lock().map_err(|_| StoreError::Locked)?;
        let manifest_bytes = read_bounded(&root.join("run.json"), MANIFEST_LIMIT)?;
        let manifest: Manifest = decode(&manifest_bytes)?;
        if manifest.schema != "OF1_ACQUISITION_STORE_1"
            || &manifest.plan != plan
            || manifest.aggregate_sha256 != sha256(&encode(plan)?)
            || !hash(&manifest.run_id)
        {
            return Err(StoreError::Identity);
        }
        validate_metadata(plan, &manifest.metadata_lease)?;
        validate_stage(
            &manifest.metadata_stage,
            &manifest.metadata_lease.authority,
            &manifest.metadata_lease.budget,
            &manifest.metadata_lease,
        )?;
        let payload = if root.join("payload.json").exists() {
            Some(decode::<PayloadRecord>(&read_bounded(
                &root.join("payload.json"),
                MANIFEST_LIMIT,
            )?)?)
        } else {
            None
        };
        let mut store = Self {
            root: root.into(),
            lock,
            root_identity: identity(&root_meta),
            high_water: manifest.metadata_stage.started_at.clone(),
            manifest,
            manifest_bytes,
            payload,
            clock,
            cache: Audit::default(),
            inflight: None,
            poisoned: false,
            fault: None,
        };
        if store.current_lease_sha256() != expected_current_lease_sha256 {
            return Err(StoreError::Identity);
        }
        store.refresh()?;
        store.sample()?;
        Ok(store)
    }

    /// Permanently closes metadata dispatch. Spent metadata charges remain in this same ledger.
    /// # Errors
    /// Requires complete accepted metadata, exact offline rederivation, a new lease and feasible aggregate remainder.
    pub fn admit_payload(
        &mut self,
        lease: PayloadLease,
        prepared: &PreparedPayload,
    ) -> StoreResult<()> {
        let result = self.admit_payload_inner(lease, prepared);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn admit_payload_inner(
        &mut self,
        lease: PayloadLease,
        prepared: &PreparedPayload,
    ) -> StoreResult<()> {
        self.usable()?;
        if self.payload.is_some() || self.inflight.is_some() {
            return Err(StoreError::Identity);
        }
        self.refresh()?;
        let metadata = self.metadata_objects()?;
        crate::acquisition::verify_prepared_payload(&self.manifest.plan, &metadata, prepared)?;
        validate_payload(&self.manifest, &lease, prepared, &self.cache.attempts)?;
        let started = self.sample()?;
        let stage = make_stage(&lease.authority, &lease.budget, &lease, &started)?;
        let record = PayloadRecord {
            lease,
            stage,
            prepared: prepared.clone(),
            metadata_attempt_count: self.cache.attempts.len() as u64,
        };
        let bytes = encode(&record)?;
        if bytes.len() as u64 > self.manifest.plan.budget.max_plan_bytes {
            return Err(StoreError::Budget);
        }
        // Retained intent + canonical hard link: a torn/unmatched intent is never replayed.
        let intent = self.root.join("pending/payload-intent.json");
        self.write_new(&intent, &bytes)?;
        sync_dir(&self.root.join("pending"))?;
        self.trip(FaultPoint::BeforeStagePublish)?;
        fs::hard_link(intent, self.root.join("payload.json"))?;
        sync_dir(&self.root)?;
        self.trip(FaultPoint::AfterStagePublish)?;
        self.payload = Some(record);
        self.refresh()
    }

    #[must_use]
    pub fn aggregate(&self) -> &AggregatePlan {
        &self.manifest.plan
    }

    #[must_use]
    pub fn aggregate_plan(&self) -> &AggregatePlan {
        &self.manifest.plan
    }

    #[must_use]
    pub fn prepared_payload(&self) -> Option<&PreparedPayload> {
        self.payload.as_ref().map(|p| &p.prepared)
    }

    #[must_use]
    pub fn authority(&self) -> &Authority {
        &self.stage().authority
    }

    #[must_use]
    pub fn current_lease_sha256(&self) -> &str {
        &self.stage().lease_sha256
    }

    /// # Errors
    /// Rejects missing metadata instead of returning a partial manifest identity.
    pub fn metadata_receipt_sha256(&self) -> StoreResult<String> {
        crate::acquisition::metadata_receipt_sha256(&self.metadata_objects()?)
    }

    /// # Errors
    /// Unknown sequence fails before dispatch.
    pub fn request(&self, sequence: u64) -> StoreResult<&Request> {
        if sequence < 4 {
            return Ok(
                &METADATA_REQUESTS[usize::try_from(sequence).map_err(|_| StoreError::Identity)?]
            );
        }
        self.payload
            .as_ref()
            .and_then(|p| p.prepared.requests.get(usize::try_from(sequence - 4).ok()?))
            .filter(|r| r.sequence == sequence)
            .ok_or(StoreError::Identity)
    }

    /// # Errors
    /// Only constructed fixed epoch paths are returned.
    pub fn source_path(&self, sequence: u64) -> StoreResult<String> {
        Ok(self.request(sequence)?.path(self.manifest.plan.epoch))
    }

    /// # Errors
    /// Complete publications, stale stage authority, locks, deadlines and all hard caps deny dispatch.
    pub fn reserve(&mut self, sequence: u64) -> StoreResult<Permit> {
        let result = self.reserve_inner(sequence);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn reserve_inner(&mut self, sequence: u64) -> StoreResult<Permit> {
        self.usable()?;
        if self.inflight.is_some() {
            return Err(StoreError::Poisoned);
        }
        self.refresh()?;
        if self.cache.published.contains_key(&sequence) {
            return Err(StoreError::AlreadyPublished);
        }
        if self.payload.is_some() != (sequence >= 4) {
            return Err(StoreError::Identity);
        }
        let request = self.request(sequence)?.clone();
        // Deterministic inventory order: only the first remaining logical request is eligible.
        let first = if self.payload.is_some() { 4 } else { 0 };
        if (first..sequence).any(|s| !self.cache.published.contains_key(&s)) {
            return Err(StoreError::Identity);
        }
        let allowance = request.allowance();
        let count = self.cache.attempts.len() as u64;
        let budget = &self.manifest.plan.budget;
        let stage = self.stage();
        let stage_attempts: Vec<_> = self
            .cache
            .attempts
            .iter()
            .filter(|a| a.lease_sha256 == stage.lease_sha256)
            .collect();
        let retries = self
            .cache
            .attempts
            .iter()
            .filter(|a| a.request.sequence == sequence)
            .count() as u64;
        if count >= budget.max_requests
            || retries > u64::from(budget.request_retries)
            || sum_charge(self.cache.attempts.iter())?
                .checked_add(allowance)
                .ok_or(StoreError::Budget)?
                > budget.max_total_response_entity_bytes
            || stage_attempts.len() as u64 >= stage.budget.max_requests
            || sum_charge(stage_attempts.into_iter())?
                .checked_add(allowance)
                .ok_or(StoreError::Budget)?
                > stage.budget.max_response_entity_bytes_total
        {
            return Err(StoreError::Budget);
        }
        let stage_sha = stage.lease_sha256.clone();
        self.space(capture_disk_allowance(allowance)?)?;
        let at = self.dispatch_now(None)?;
        let reservation = Reservation {
            run_id: self.manifest.run_id.clone(),
            aggregate_sha256: self.manifest.aggregate_sha256.clone(),
            lease_sha256: stage_sha,
            attempt_id: count,
            request,
            reserved_entity_bytes: allowance,
            at,
        };
        let intent = self
            .root
            .join("pending")
            .join(format!("reserve-{count:010}.json"));
        self.write_new(&intent, &encode(&reservation)?)?;
        sync_dir(&self.root.join("pending"))?;
        self.trip(FaultPoint::BeforeReservationPublish)?;
        fs::hard_link(
            intent,
            self.root.join("attempts").join(format!("{count:010}.json")),
        )?;
        sync_dir(&self.root.join("attempts"))?;
        self.trip(FaultPoint::AfterReservationPublish)?;
        self.cache.attempts.push(reservation.clone());
        self.inflight = Some(count);
        self.dispatch_now(Some(&reservation))?;
        Ok(Permit(reservation))
    }

    /// # Errors
    /// A local fixture permit is never accepted by the production connector.
    pub fn network_authorized(&mut self, permit: &Permit) -> StoreResult<()> {
        self.permit(permit)?;
        if !matches!(self.authority(), Authority::Approved { .. }) {
            return Err(StoreError::Identity);
        }
        self.remaining_ms(permit).map(|_| ())
    }

    /// # Errors
    /// Covers the same persisted stage and attempt deadlines across DNS/TLS/reads/restarts.
    pub fn remaining_ms(&mut self, permit: &Permit) -> StoreResult<u64> {
        self.permit(permit)?;
        let at = self.dispatch_now(Some(&permit.0))?;
        let stage = self.stage();
        let timeout = self.manifest.plan.budget.response_timeout_ms;
        [
            stage.deadline_wall_ms - at.wall_ms,
            stage.deadline_boot_ms - at.boot_ms,
            timeout - (at.wall_ms - permit.0.at.wall_ms),
            timeout - (at.boot_ms - permit.0.at.boot_ms),
        ]
        .into_iter()
        .min()
        .ok_or(StoreError::Deadline)
    }

    /// # Errors
    /// Preserves raw response headers before entity interpretation; identity drift is terminal.
    pub fn begin_stream(&mut self, permit: &Permit, head: ResponseHead) -> StoreResult<()> {
        let result = self.begin_stream_inner(permit, head);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn begin_stream_inner(&mut self, permit: &Permit, head: ResponseHead) -> StoreResult<()> {
        self.permit(permit)?;
        if self.cache.streams.contains_key(&permit.0.attempt_id) {
            return Err(StoreError::Corrupt);
        }
        let reparsed = match crate::acquisition_http::parse_response_head(
            &head.raw_headers,
            &permit.0.request,
        ) {
            Ok(parsed) => parsed,
            Err(crate::acquisition_http::HeaderError::SourceDrift) => {
                return self.reject_response(permit, &head.raw_headers, "SOURCE_DRIFT");
            }
            Err(_) => return Err(StoreError::Corrupt),
        };
        if reparsed != head {
            return Err(StoreError::Corrupt);
        }
        let at = self.dispatch_now(Some(&permit.0))?;
        let record = StreamIdentity {
            reservation: permit.0.clone(),
            head,
            at: at.clone(),
        };
        let path = self.stream_path(permit.0.attempt_id);
        self.space(3 * RECORD_LIMIT)?;
        fs::create_dir(&path)?;
        self.write_new(&path.join("headers.bin"), &record.head.raw_headers)?;
        self.write_new(&path.join("head.json"), &encode(&record)?)?;
        sync_dir(&path)?;
        sync_dir(&self.root.join("pending"))?;
        if let Err(e) = permit.0.request.entity_length(&record.head) {
            return if matches!(e, StoreError::SourceDrift) {
                self.quarantine(permit, "SOURCE_DRIFT")
            } else {
                Err(e)
            };
        }
        for prior in self.cache.streams.values() {
            if same_object(
                &permit.0.request,
                &prior.identity.reservation.request,
                self.manifest.plan.epoch,
            ) && (object_size(&permit.0.request, &record.head)
                != object_size(&prior.identity.reservation.request, &prior.identity.head)
                || record.head.strong_etag != prior.identity.head.strong_etag)
            {
                return self.quarantine(permit, "SOURCE_DRIFT");
            }
        }
        self.cache.streams.insert(
            permit.0.attempt_id,
            StreamRecord {
                identity: record,
                bytes: Vec::new(),
                segments: 0,
                latest_at: at,
            },
        );
        Ok(())
    }

    /// # Errors
    /// A rejected response is retained without becoming a complete Raw publication.
    pub fn reject_response(
        &mut self,
        permit: &Permit,
        raw_headers: &[u8],
        reason: &str,
    ) -> StoreResult<()> {
        self.permit(permit)?;
        if raw_headers.len() > crate::acquisition_http::MAX_HEADER_BYTES
            || reason.is_empty()
            || reason.len() > 256
        {
            self.poisoned = true;
            return Err(StoreError::Corrupt);
        }
        let path = self
            .root
            .join("pending")
            .join(format!("rejected-{:010}", permit.0.attempt_id));
        self.space(RECORD_LIMIT)?;
        fs::create_dir(&path)?;
        self.write_new(&path.join("headers.bin"), raw_headers)?;
        self.write_new(
            &path.join("failure.json"),
            &encode(&(permit.0.clone(), reason, sha256(raw_headers)))?,
        )?;
        sync_dir(&path)?;
        sync_dir(&self.root.join("pending"))?;
        self.poisoned = true;
        if reason == "SOURCE_DRIFT" {
            return self.quarantine(permit, reason);
        }
        Ok(())
    }

    /// # Errors
    /// Resource admission precedes every bounded read; there is no presumed future disk space.
    pub fn prepare_stream_read(&mut self, permit: &Permit, max_bytes: u64) -> StoreResult<()> {
        self.permit(permit)?;
        let stream = self
            .cache
            .streams
            .get(&permit.0.attempt_id)
            .ok_or(StoreError::Corrupt)?;
        let expected = permit.0.request.entity_length(&stream.identity.head)?;
        if max_bytes == 0
            || max_bytes > SEGMENT_BYTES as u64
            || (stream.bytes.len() as u64)
                .checked_add(max_bytes)
                .ok_or(StoreError::Budget)?
                > expected
        {
            return Err(StoreError::Budget);
        }
        self.space(
            rounded(max_bytes)?
                .checked_add(rounded(expected)?)
                .and_then(|n| n.checked_add(4 * RECORD_LIMIT))
                .ok_or(StoreError::Budget)?,
        )?;
        self.remaining_ms(permit).map(|_| ())
    }

    /// # Errors
    /// Immutable segments retain exact bytes. Socket fragments are coalesced by the adapter.
    pub fn append_stream(&mut self, permit: &Permit, bytes: &[u8]) -> StoreResult<()> {
        let result = self.append_stream_inner(permit, bytes);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn append_stream_inner(&mut self, permit: &Permit, bytes: &[u8]) -> StoreResult<()> {
        self.prepare_stream_read(permit, bytes.len() as u64)?;
        let stream = self
            .cache
            .streams
            .get(&permit.0.attempt_id)
            .ok_or(StoreError::Corrupt)?;
        let offset = stream.bytes.len();
        let segment = stream.segments;
        if segment
            > permit
                .0
                .reserved_entity_bytes
                .div_ceil(SEGMENT_BYTES as u64)
        {
            return Err(StoreError::Budget);
        }
        let mut conflicting = false;
        for (id, prior) in &self.cache.streams {
            if *id == permit.0.attempt_id || prior.identity.reservation.request != permit.0.request
            {
                continue;
            }
            let overlap_end = (offset + bytes.len()).min(prior.bytes.len());
            if overlap_end > offset
                && bytes[..overlap_end - offset] != prior.bytes[offset..overlap_end]
            {
                conflicting = true;
            }
        }
        let at = self.dispatch_now(Some(&permit.0))?;
        let receipt = SegmentReceipt {
            attempt_id: permit.0.attempt_id,
            offset: offset as u64,
            length: bytes.len() as u64,
            sha256: sha256(bytes),
            at: at.clone(),
        };
        let parent = self.stream_path(permit.0.attempt_id);
        let candidate = parent.join(format!("incomplete-{segment:010}"));
        fs::create_dir(&candidate)?;
        self.write_new(&candidate.join("raw.bin"), bytes)?;
        self.write_new(&candidate.join("segment.json"), &encode(&receipt)?)?;
        sync_dir(&candidate)?;
        self.remaining_ms(permit)?;
        let destination = parent.join(format!("segment-{segment:010}"));
        if destination.exists() {
            return Err(StoreError::Corrupt);
        }
        fs::rename(candidate, destination)?;
        sync_dir(&parent)?;
        if conflicting {
            // Keep the disagreeing received segment as well as the terminal reason.
            return self.quarantine(permit, "CONFLICTING_BYTES");
        }
        let stream = self
            .cache
            .streams
            .get_mut(&permit.0.attempt_id)
            .ok_or(StoreError::Corrupt)?;
        stream
            .bytes
            .try_reserve_exact(bytes.len())
            .map_err(|_| StoreError::Budget)?;
        stream.bytes.extend_from_slice(bytes);
        stream.segments += 1;
        stream.latest_at = at;
        Ok(())
    }

    /// # Errors
    /// Only exact exhaustion and a full persisted audit permit paired publication.
    pub fn finish_stream(&mut self, permit: Permit) -> StoreResult<Receipt> {
        self.finish_stream_observed(permit, || {})
    }

    /// Read-only, nonblocking stage observation after validation, before publication I/O.
    /// The callback supplies no authority or data and cannot change the durable ordering.
    /// # Errors
    /// Identical to `finish_stream`; consumes the permit even on failure.
    pub fn finish_stream_observed(
        &mut self,
        permit: Permit,
        publishing: impl FnOnce(),
    ) -> StoreResult<Receipt> {
        let result = self.finish_inner(&permit, publishing);
        // Consume the non-cloneable permit even when publication fails.
        drop(permit);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn finish_inner(&mut self, permit: &Permit, publishing: impl FnOnce()) -> StoreResult<Receipt> {
        self.permit(permit)?;
        self.refresh()?;
        let stream = self
            .cache
            .streams
            .get(&permit.0.attempt_id)
            .ok_or(StoreError::Corrupt)?;
        let expected = permit.0.request.entity_length(&stream.identity.head)?;
        if stream.bytes.len() as u64 != expected {
            return Err(StoreError::Corrupt);
        }
        let raw = stream.bytes.clone();
        let head = stream.identity.head.clone();
        let comparison = self.comparison(&permit.0, &raw)?;
        let receipt = Receipt {
            schema: "OF1_ACQUISITION_RECEIPT_1".into(),
            run_id: self.manifest.run_id.clone(),
            aggregate_sha256: self.manifest.aggregate_sha256.clone(),
            lease_sha256: permit.0.lease_sha256.clone(),
            attempt_id: permit.0.attempt_id,
            request: permit.0.request.clone(),
            source_host: HOST.into(),
            source_path: permit.0.request.path(self.manifest.plan.epoch),
            response_headers_sha256: sha256(&head.raw_headers),
            response: head,
            response_entity_bytes: expected,
            sha256: sha256(&raw),
            acquired_at: self.dispatch_now(Some(&permit.0))?,
            retry_comparison: comparison,
            evidence: evidence(self.authority()).into(),
            domain_counts: "UNAVAILABLE_NOT_DECODED_IN_B4".into(),
        };
        let candidate = self
            .root
            .join("pending")
            .join(format!("{:010}", permit.0.attempt_id));
        self.space(
            rounded(expected)?
                .checked_add(2 * RECORD_LIMIT)
                .ok_or(StoreError::Budget)?,
        )?;
        publishing();
        fs::create_dir(&candidate)?;
        sync_dir(&self.root.join("pending"))?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(candidate.join("raw.bin"))?;
        file.write_all(&raw)?;
        self.trip(FaultPoint::AfterRawWrite)?;
        file.sync_all()?;
        self.trip(FaultPoint::AfterRawSync)?;
        self.write_new(&candidate.join("receipt.json"), &encode(&receipt)?)?;
        self.trip(FaultPoint::AfterReceiptSync)?;
        sync_dir(&candidate)?;
        self.trip(FaultPoint::BeforePublish)?;
        self.refresh()?;
        self.remaining_ms(permit)?;
        let destination = self
            .root
            .join("published")
            .join(format!("{:010}", permit.0.request.sequence));
        if destination.exists() {
            return Err(StoreError::AlreadyPublished);
        }
        fs::rename(candidate, destination)?;
        self.trip(FaultPoint::AfterPublish)?;
        sync_dir(&self.root.join("published"))?;
        sync_dir(&self.root.join("pending"))?;
        self.trip(FaultPoint::AfterPublishSync)?;
        self.inflight = None;
        self.refresh()?;
        if self
            .cache
            .published
            .get(&receipt.request.sequence)
            .map(|p| &p.receipt)
            != Some(&receipt)
        {
            return Err(StoreError::Corrupt);
        }
        Ok(receipt)
    }

    pub fn abort_stream(&mut self) {
        self.poisoned = true;
    }
    pub fn inject_fault(&mut self, fault: FaultPoint) {
        self.fault = Some(fault);
    }

    /// # Errors
    /// Returns a path only after exact manifest/receipt/content revalidation.
    pub fn published(&self, sequence: u64) -> StoreResult<Option<Published>> {
        let audit = self.audit()?;
        Ok(audit.published.get(&sequence).cloned())
    }

    /// Writer progress uses validated cached state; it cannot promote unsealed bytes.
    /// # Errors
    /// Missing resource counters or arithmetic overflow fail closed.
    pub fn progress(&self) -> StoreResult<Progress> {
        let (available, rss, peak) = resource_sample(&self.root)?;
        // After ambiguous mutation failure a stale cache must never report a refund or
        // hide a completed pair. An unauditable poisoned run has no numeric summary.
        let recovered;
        let state = if self.poisoned {
            recovered = self.audit()?;
            &recovered
        } else {
            &self.cache
        };
        let published = state.published.values().try_fold(0u64, |n, p| {
            n.checked_add(p.receipt.response_entity_bytes)
                .ok_or(StoreError::Budget)
        })?;
        Ok(Progress {
            stage: if self.payload.is_some() {
                "PAYLOAD"
            } else {
                "METADATA"
            }
            .into(),
            attempts_reserved: state.attempts.len() as u64,
            charged_entity_bytes: sum_charge(state.attempts.iter())?,
            published_response_entity_bytes: published,
            published_requests: state.published.len() as u64,
            unpublished_attempts: (state.attempts.len() - state.published.len()) as u64,
            unreceipted_response_entity_bytes: None,
            disk_charge_bytes: disk_charge(&self.root)?,
            current_rss_bytes: rss,
            peak_rss_bytes: peak,
            available_disk_bytes: available,
            deadline_wall_ms: self.stage().deadline_wall_ms,
            deadline_boot_ms: self.stage().deadline_boot_ms,
            current_lease_sha256: self.current_lease_sha256().into(),
            evidence: evidence(self.authority()).into(),
            domain_counts: "UNAVAILABLE_NOT_DECODED_IN_B4".into(),
        })
    }

    fn stage(&self) -> &StageRecord {
        self.payload
            .as_ref()
            .map_or(&self.manifest.metadata_stage, |p| &p.stage)
    }
    fn stage_for(&self, request: &Request) -> StoreResult<&StageRecord> {
        if request.sequence < 4 {
            Ok(&self.manifest.metadata_stage)
        } else {
            self.payload
                .as_ref()
                .map(|p| &p.stage)
                .ok_or(StoreError::Identity)
        }
    }
    fn stream_path(&self, id: u64) -> PathBuf {
        self.root.join("pending").join(format!("stream-{id:010}"))
    }
    fn metadata_objects(&self) -> StoreResult<Vec<Published>> {
        (0..4)
            .map(|n| {
                self.cache
                    .published
                    .get(&n)
                    .cloned()
                    .ok_or(StoreError::Identity)
            })
            .collect()
    }
    fn usable(&self) -> StoreResult<()> {
        if self.poisoned {
            Err(StoreError::Poisoned)
        } else {
            Ok(())
        }
    }
    fn permit(&self, permit: &Permit) -> StoreResult<()> {
        self.usable()?;
        self.identity_guard()?;
        if self.inflight != Some(permit.0.attempt_id)
            || permit.0.run_id != self.manifest.run_id
            || permit.0.lease_sha256 != self.current_lease_sha256()
            || self
                .cache
                .attempts
                .get(usize::try_from(permit.0.attempt_id).map_err(|_| StoreError::Corrupt)?)
                != Some(&permit.0)
        {
            return Err(StoreError::Identity);
        }
        Ok(())
    }
    fn identity_guard(&self) -> StoreResult<()> {
        if identity(&fs::symlink_metadata(&self.root)?) != self.root_identity
            || identity(&regular(&self.root.join("writer.lock"))?)
                != identity(&self.lock.metadata()?)
        {
            return Err(StoreError::Locked);
        }
        if read_bounded(&self.root.join("run.json"), MANIFEST_LIMIT)? != self.manifest_bytes {
            return Err(StoreError::Identity);
        }
        Ok(())
    }
    fn sample(&mut self) -> StoreResult<ClockSample> {
        let at = self.clock.sample()?;
        clock_follows(&self.high_water, &at)?;
        self.high_water = at.clone();
        Ok(at)
    }
    fn dispatch_now(&mut self, attempt: Option<&Reservation>) -> StoreResult<ClockSample> {
        let at = self.sample()?;
        let stage = self.stage();
        within_stage(stage, &at)?;
        if let Some(a) = attempt {
            within_attempt(stage, a, &at, self.manifest.plan.budget.response_timeout_ms)?;
        }
        resources(&self.root, &self.manifest.plan.budget)?;
        Ok(at)
    }
    fn trip(&mut self, point: FaultPoint) -> StoreResult<()> {
        if self.fault == Some(point) {
            self.fault = None;
            self.poisoned = true;
            return Err(StoreError::Injected(point));
        }
        Ok(())
    }
    fn space(&self, additional: u64) -> StoreResult<()> {
        resources(&self.root, &self.manifest.plan.budget)?;
        if disk_charge(&self.root)?
            .checked_add(additional)
            .ok_or(StoreError::Budget)?
            > self.manifest.plan.budget.max_disk_bytes
        {
            return Err(StoreError::Budget);
        }
        Ok(())
    }
    fn write_new(&self, path: &Path, bytes: &[u8]) -> StoreResult<()> {
        self.space(rounded(bytes.len() as u64)?)?;
        let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        Ok(())
    }
    fn quarantine<T>(&self, permit: &Permit, reason: &str) -> StoreResult<T> {
        self.write_new(
            &self.root.join("pending/quarantine.json"),
            &encode(&(permit.0.clone(), reason))?,
        )?;
        sync_dir(&self.root.join("pending"))?;
        Err(if reason == "SOURCE_DRIFT" {
            StoreError::SourceDrift
        } else {
            StoreError::ConflictingBytes
        })
    }
    fn comparison(&self, attempt: &Reservation, bytes: &[u8]) -> StoreResult<RetryComparison> {
        let mut overlap = false;
        for (id, prior) in &self.cache.streams {
            if *id >= attempt.attempt_id || prior.identity.reservation.request != attempt.request {
                continue;
            }
            let length = prior.bytes.len().min(bytes.len());
            if prior.bytes[..length] != bytes[..length] {
                return Err(StoreError::ConflictingBytes);
            }
            overlap |= length != 0;
        }
        Ok(if overlap {
            RetryComparison::RetainedOverlapMatched
        } else {
            RetryComparison::NoPriorBytes
        })
    }
    fn refresh(&mut self) -> StoreResult<()> {
        let audit = self.audit()?;
        let mut high = self.manifest.metadata_stage.started_at.clone();
        if let Some(p) = &self.payload {
            clock_follows(&high, &p.stage.started_at)?;
            high = p.stage.started_at.clone();
        }
        for a in &audit.attempts {
            high.wall_ms = high.wall_ms.max(a.at.wall_ms);
            high.boot_ms = high.boot_ms.max(a.at.boot_ms);
        }
        for s in audit.streams.values() {
            high.wall_ms = high.wall_ms.max(s.latest_at.wall_ms);
            high.boot_ms = high.boot_ms.max(s.latest_at.boot_ms);
        }
        for p in audit.published.values() {
            high.wall_ms = high.wall_ms.max(p.receipt.acquired_at.wall_ms);
            high.boot_ms = high.boot_ms.max(p.receipt.acquired_at.boot_ms);
        }
        clock_follows(&self.high_water, &high)
            .or_else(|_| clock_follows(&high, &self.high_water))?;
        self.high_water.wall_ms = self.high_water.wall_ms.max(high.wall_ms);
        self.high_water.boot_ms = self.high_water.boot_ms.max(high.boot_ms);
        self.cache = audit;
        Ok(())
    }

    fn audit(&self) -> StoreResult<Audit> {
        self.identity_guard()?;
        if executable_hash()? != self.manifest.plan.executable_sha256 {
            return Err(StoreError::Identity);
        }
        let mut names = vec![
            "writer.lock",
            "run.json",
            "attempts",
            "pending",
            "published",
        ];
        if self.payload.is_some() {
            names.push("payload.json");
        }
        exact_names(&self.root, &names)?;
        self.space(0)?;
        if let Some(payload) = &self.payload {
            if read_bounded(&self.root.join("payload.json"), MANIFEST_LIMIT)? != encode(payload)? {
                return Err(StoreError::Identity);
            }
            validate_stage(
                &payload.stage,
                &payload.lease.authority,
                &payload.lease.budget,
                &payload.lease,
            )?;
        }
        let mut audit = Audit::default();
        let mut previous = self.manifest.metadata_stage.started_at.clone();
        for path in children(
            &self.root.join("attempts"),
            self.manifest.plan.budget.max_requests,
        )? {
            let a: Reservation = decode(&read_bounded(&path, RECORD_LIMIT)?)?;
            let id = audit.attempts.len() as u64;
            if path.file_name().and_then(|n| n.to_str()) != Some(format!("{id:010}.json").as_str())
                || a.attempt_id != id
                || a.run_id != self.manifest.run_id
                || a.aggregate_sha256 != self.manifest.aggregate_sha256
                || self.request(a.request.sequence)? != &a.request
                || a.lease_sha256 != self.stage_for(&a.request)?.lease_sha256
                || a.reserved_entity_bytes != a.request.allowance()
            {
                return Err(StoreError::Corrupt);
            }
            if let Some(p) = &self.payload {
                if (id < p.metadata_attempt_count) != (a.request.sequence < 4) {
                    return Err(StoreError::Corrupt);
                }
            } else if a.request.sequence >= 4 {
                return Err(StoreError::Corrupt);
            }
            clock_follows(&previous, &a.at)?;
            within_stage(self.stage_for(&a.request)?, &a.at)?;
            previous = a.at.clone();
            let prior = audit
                .attempts
                .iter()
                .filter(|p| p.request.sequence == a.request.sequence)
                .count() as u64;
            if prior > u64::from(self.manifest.plan.budget.request_retries) {
                return Err(StoreError::Corrupt);
            }
            audit.attempts.push(a);
        }
        if sum_charge(audit.attempts.iter())?
            > self.manifest.plan.budget.max_total_response_entity_bytes
        {
            return Err(StoreError::Corrupt);
        }
        for stage in [&self.manifest.metadata_stage]
            .into_iter()
            .chain(self.payload.as_ref().map(|p| &p.stage))
        {
            let attempts: Vec<_> = audit
                .attempts
                .iter()
                .filter(|a| a.lease_sha256 == stage.lease_sha256)
                .collect();
            if attempts.len() as u64 > stage.budget.max_requests
                || sum_charge(attempts.into_iter())? > stage.budget.max_response_entity_bytes_total
            {
                return Err(StoreError::Corrupt);
            }
        }
        self.audit_pending(&mut audit)?;
        self.audit_published(&mut audit)?;
        for attempt in &audit.attempts {
            for sequence in 0..attempt.request.sequence {
                let p = audit.published.get(&sequence).ok_or(StoreError::Corrupt)?;
                clock_follows(&p.receipt.acquired_at, &attempt.at)?;
            }
        }
        self.audit_payload_binding(&audit)?;
        Ok(audit)
    }

    fn audit_payload_binding(&self, audit: &Audit) -> StoreResult<()> {
        if let Some(p) = &self.payload {
            let metadata: Vec<_> = (0..4)
                .map(|n| audit.published.get(&n).cloned().ok_or(StoreError::Corrupt))
                .collect::<StoreResult<_>>()?;
            let attempts = audit
                .attempts
                .iter()
                .take(usize::try_from(p.metadata_attempt_count).map_err(|_| StoreError::Corrupt)?)
                .cloned()
                .collect::<Vec<_>>();
            if attempts.len() as u64 != p.metadata_attempt_count {
                return Err(StoreError::Corrupt);
            }
            validate_payload(&self.manifest, &p.lease, &p.prepared, &attempts)?;
            for object in &metadata {
                clock_follows(&object.receipt.acquired_at, &p.stage.started_at)?;
            }
            crate::acquisition::verify_prepared_payload(
                &self.manifest.plan,
                &metadata,
                &p.prepared,
            )?;
        }
        Ok(())
    }

    fn audit_pending(&self, audit: &mut Audit) -> StoreResult<()> {
        let mut payload_intent_seen = false;
        for path in children(
            &self.root.join("pending"),
            self.manifest.plan.budget.max_disk_bytes / BLOCK,
        )? {
            let name = path
                .file_name()
                .and_then(|s| s.to_str())
                .ok_or(StoreError::Corrupt)?;
            if name == "quarantine.json" {
                let (a, reason): (Reservation, String) =
                    decode(&read_bounded(&path, RECORD_LIMIT)?)?;
                if audit
                    .attempts
                    .get(usize::try_from(a.attempt_id).map_err(|_| StoreError::Corrupt)?)
                    != Some(&a)
                {
                    return Err(StoreError::Corrupt);
                }
                return Err(match reason.as_str() {
                    "SOURCE_DRIFT" => StoreError::SourceDrift,
                    "CONFLICTING_BYTES" => StoreError::ConflictingBytes,
                    _ => StoreError::Corrupt,
                });
            }
            if name == "payload-intent.json" {
                let p = self.payload.as_ref().ok_or(StoreError::Corrupt)?;
                if read_bounded(&path, MANIFEST_LIMIT)? != encode(p)? {
                    return Err(StoreError::Corrupt);
                }
                payload_intent_seen = true;
                continue;
            }
            if let Some(text) = name
                .strip_prefix("reserve-")
                .and_then(|n| n.strip_suffix(".json"))
            {
                let id = text.parse::<usize>().map_err(|_| StoreError::Corrupt)?;
                let a = audit.attempts.get(id).ok_or(StoreError::Corrupt)?;
                if name != format!("reserve-{id:010}.json")
                    || read_bounded(&path, RECORD_LIMIT)? != encode(a)?
                {
                    return Err(StoreError::Corrupt);
                }
                continue;
            }
            if let Some(text) = name.strip_prefix("stream-") {
                let id = text.parse::<usize>().map_err(|_| StoreError::Corrupt)?;
                if name != format!("stream-{id:010}") {
                    return Err(StoreError::Corrupt);
                }
                let a = audit.attempts.get(id).ok_or(StoreError::Corrupt)?;
                let record = self.read_stream(a)?;
                for later in audit.attempts.iter().skip(id + 1) {
                    clock_follows(&record.latest_at, &later.at)?;
                }
                audit.streams.insert(id as u64, record);
                continue;
            }
            if let Some(text) = name.strip_prefix("rejected-") {
                let id = text.parse::<usize>().map_err(|_| StoreError::Corrupt)?;
                if name != format!("rejected-{id:010}") {
                    return Err(StoreError::Corrupt);
                }
                Self::audit_rejected(&path, id, audit)?;
                continue;
            }
            // A pre-publication pair remains forensic staging, never silently promoted.
            let id = name.parse::<usize>().map_err(|_| StoreError::Corrupt)?;
            if name != format!("{id:010}") || audit.attempts.get(id).is_none() {
                return Err(StoreError::Corrupt);
            }
            for file in children(&path, 2)? {
                let file_name = file
                    .file_name()
                    .and_then(|s| s.to_str())
                    .ok_or(StoreError::Corrupt)?;
                if !["raw.bin", "receipt.json"].contains(&file_name) {
                    return Err(StoreError::Corrupt);
                }
                regular(&file)?;
            }
        }
        if payload_intent_seen != self.payload.is_some() {
            return Err(StoreError::Corrupt);
        }
        for a in &audit.attempts {
            if read_bounded(
                &self
                    .root
                    .join("pending")
                    .join(format!("reserve-{:010}.json", a.attempt_id)),
                RECORD_LIMIT,
            )? != encode(a)?
            {
                return Err(StoreError::Corrupt);
            }
        }
        self.audit_retry_compatibility(audit)
    }

    fn audit_rejected(path: &Path, id: usize, audit: &Audit) -> StoreResult<()> {
        exact_names(path, &["headers.bin", "failure.json"])?;
        let (a, reason, digest): (Reservation, String, String) =
            decode(&read_bounded(&path.join("failure.json"), RECORD_LIMIT)?)?;
        let raw = read_bounded(
            &path.join("headers.bin"),
            crate::acquisition_http::MAX_HEADER_BYTES as u64,
        )?;
        if audit.attempts.get(id) != Some(&a)
            || sha256(&raw) != digest
            || reason.is_empty()
            || reason.len() > 256
        {
            return Err(StoreError::Corrupt);
        }
        Ok(())
    }

    fn audit_retry_compatibility(&self, audit: &Audit) -> StoreResult<()> {
        // Exact source/overlap compatibility is rechecked over all captured attempts.
        for (id, stream) in &audit.streams {
            for (prior_id, prior) in &audit.streams {
                if prior_id >= id {
                    continue;
                }
                let request = &stream.identity.reservation.request;
                let old_request = &prior.identity.reservation.request;
                if same_object(request, old_request, self.manifest.plan.epoch)
                    && (object_size(request, &stream.identity.head)
                        != object_size(old_request, &prior.identity.head)
                        || stream.identity.head.strong_etag != prior.identity.head.strong_etag)
                {
                    return Err(StoreError::SourceDrift);
                }
                if request == old_request {
                    let overlap = stream.bytes.len().min(prior.bytes.len());
                    if stream.bytes[..overlap] != prior.bytes[..overlap] {
                        return Err(StoreError::ConflictingBytes);
                    }
                }
            }
        }
        Ok(())
    }

    fn read_stream(&self, attempt: &Reservation) -> StoreResult<StreamRecord> {
        let path = self.stream_path(attempt.attempt_id);
        let identity: StreamIdentity =
            decode(&read_bounded(&path.join("head.json"), RECORD_LIMIT)?)?;
        let headers = read_bounded(
            &path.join("headers.bin"),
            crate::acquisition_http::MAX_HEADER_BYTES as u64,
        )?;
        if &identity.reservation != attempt
            || identity.head.raw_headers != headers
            || crate::acquisition_http::parse_response_head(&headers, &attempt.request)
                .map_err(|_| StoreError::Corrupt)?
                != identity.head
        {
            return Err(StoreError::Corrupt);
        }
        let expected = attempt.request.entity_length(&identity.head)?;
        within_attempt(
            self.stage_for(&attempt.request)?,
            attempt,
            &identity.at,
            self.manifest.plan.budget.response_timeout_ms,
        )?;
        let mut record = StreamRecord {
            latest_at: identity.at.clone(),
            identity,
            bytes: Vec::new(),
            segments: 0,
        };
        for child in children(&path, expected.div_ceil(SEGMENT_BYTES as u64) + 4)? {
            let name = child
                .file_name()
                .and_then(|n| n.to_str())
                .ok_or(StoreError::Corrupt)?;
            if ["head.json", "headers.bin"].contains(&name) {
                continue;
            }
            if name != format!("segment-{:010}", record.segments) {
                return Err(StoreError::Corrupt);
            }
            exact_names(&child, &["raw.bin", "segment.json"])?;
            let receipt: SegmentReceipt =
                decode(&read_bounded(&child.join("segment.json"), RECORD_LIMIT)?)?;
            let raw = read_bounded(&child.join("raw.bin"), SEGMENT_BYTES as u64)?;
            if receipt.attempt_id != attempt.attempt_id
                || receipt.offset != record.bytes.len() as u64
                || raw.is_empty()
                || receipt.length != raw.len() as u64
                || sha256(&raw) != receipt.sha256
                || (record.bytes.len() as u64)
                    .checked_add(raw.len() as u64)
                    .ok_or(StoreError::Corrupt)?
                    > expected
            {
                return Err(StoreError::Corrupt);
            }
            clock_follows(&record.latest_at, &receipt.at)?;
            within_attempt(
                self.stage_for(&attempt.request)?,
                attempt,
                &receipt.at,
                self.manifest.plan.budget.response_timeout_ms,
            )?;
            record
                .bytes
                .try_reserve_exact(raw.len())
                .map_err(|_| StoreError::Budget)?;
            record.bytes.extend(raw);
            record.latest_at = receipt.at;
            record.segments += 1;
        }
        Ok(record)
    }

    fn audit_published(&self, audit: &mut Audit) -> StoreResult<()> {
        for path in children(
            &self.root.join("published"),
            self.manifest.plan.budget.max_requests,
        )? {
            exact_names(&path, &["raw.bin", "receipt.json"])?;
            let receipt: Receipt =
                decode(&read_bounded(&path.join("receipt.json"), RECORD_LIMIT)?)?;
            let a = audit
                .attempts
                .get(usize::try_from(receipt.attempt_id).map_err(|_| StoreError::Corrupt)?)
                .ok_or(StoreError::Corrupt)?;
            let stream = audit
                .streams
                .get(&receipt.attempt_id)
                .ok_or(StoreError::Corrupt)?;
            let expected = a.request.entity_length(&stream.identity.head)?;
            let raw = read_bounded(&path.join("raw.bin"), a.reserved_entity_bytes)?;
            if receipt.schema != "OF1_ACQUISITION_RECEIPT_1"
                || receipt.run_id != self.manifest.run_id
                || receipt.aggregate_sha256 != self.manifest.aggregate_sha256
                || receipt.lease_sha256 != a.lease_sha256
                || receipt.request != a.request
                || receipt.source_host != HOST
                || receipt.source_path != a.request.path(self.manifest.plan.epoch)
                || receipt.response != stream.identity.head
                || receipt.response_headers_sha256 != sha256(&receipt.response.raw_headers)
                || receipt.response_entity_bytes != expected
                || raw.len() as u64 != expected
                || sha256(&raw) != receipt.sha256
                || raw != stream.bytes
                || receipt.evidence != evidence(&self.stage_for(&a.request)?.authority)
                || receipt.domain_counts != "UNAVAILABLE_NOT_DECODED_IN_B4"
                || path.file_name().and_then(|n| n.to_str())
                    != Some(format!("{:010}", a.request.sequence).as_str())
                || self
                    .root
                    .join("pending")
                    .join(format!("{:010}", a.attempt_id))
                    .exists()
            {
                return Err(StoreError::Corrupt);
            }
            clock_follows(&stream.latest_at, &receipt.acquired_at)?;
            within_attempt(
                self.stage_for(&a.request)?,
                a,
                &receipt.acquired_at,
                self.manifest.plan.budget.response_timeout_ms,
            )?;
            for later in audit
                .attempts
                .iter()
                .skip(usize::try_from(a.attempt_id).map_err(|_| StoreError::Corrupt)? + 1)
            {
                clock_follows(&receipt.acquired_at, &later.at)?;
            }
            let mut overlap = false;
            for (id, prior) in &audit.streams {
                if *id < a.attempt_id && prior.identity.reservation.request == a.request {
                    let length = raw.len().min(prior.bytes.len());
                    if raw[..length] != prior.bytes[..length] {
                        return Err(StoreError::ConflictingBytes);
                    }
                    overlap |= length != 0;
                }
            }
            let comparison = if overlap {
                RetryComparison::RetainedOverlapMatched
            } else {
                RetryComparison::NoPriorBytes
            };
            if receipt.retry_comparison != comparison {
                return Err(StoreError::Corrupt);
            }
            if audit
                .published
                .insert(
                    a.request.sequence,
                    Published {
                        receipt,
                        raw_path: path.join("raw.bin"),
                    },
                )
                .is_some()
            {
                return Err(StoreError::Corrupt);
            }
        }
        Ok(())
    }
}

const METADATA_REQUESTS: [Request; 4] = [
    Request {
        sequence: 0,
        kind: RequestKind::Index,
    },
    Request {
        sequence: 1,
        kind: RequestKind::CarSha256,
    },
    Request {
        sequence: 2,
        kind: RequestKind::CarCid,
    },
    Request {
        sequence: 3,
        kind: RequestKind::CarHead,
    },
];

fn hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn short_text(value: &str) -> bool {
    !value.is_empty() && value.len() <= 128 && !value.bytes().any(|b| b.is_ascii_control())
}

fn validate_aggregate(plan: &AggregatePlan) -> StoreResult<()> {
    if plan.schema != AGGREGATE_SCHEMA
        || plan.format_source != FormatSource::pinned()
        || plan.code_sha.len() != 40
        || !plan
            .code_sha
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || !hash(&plan.toolchain_fingerprint)
        || !hash(&plan.executable_sha256)
        || plan.executable_sha256 != executable_hash()?
        || plan
            .epoch
            .checked_add(1)
            .and_then(|e| e.checked_mul(SLOTS_PER_EPOCH))
            .is_none()
    {
        return Err(StoreError::Identity);
    }
    let b = &plan.budget;
    if [
        b.max_slots,
        b.max_plan_bytes,
        b.max_requests,
        b.max_response_entity_bytes,
        b.max_total_response_entity_bytes,
        b.max_disk_bytes,
        b.required_free_disk_bytes,
        b.max_memory_bytes,
        b.max_runtime_ms,
        b.response_timeout_ms,
    ]
    .contains(&0)
        || b.max_slots >= SLOTS_PER_EPOCH
        || b.max_response_entity_bytes < SLOTS_PER_EPOCH * RECORD_BYTES
        || b.max_response_entity_bytes > b.max_total_response_entity_bytes
        || b.response_timeout_ms > b.max_runtime_ms
        || b.max_plan_bytes > MANIFEST_LIMIT
    {
        return Err(StoreError::Budget);
    }
    // Full audits may coexist with cached retained bytes and one final publication copy.
    // Bound these allocations before any request; runtime RSS is also sampled at I/O gates.
    let allocation = b
        .max_total_response_entity_bytes
        .checked_mul(2)
        .and_then(|n| {
            b.max_response_entity_bytes
                .checked_mul(2)
                .and_then(|v| n.checked_add(v))
        })
        .and_then(|n| {
            b.max_requests
                .checked_mul(RECORD_LIMIT * 2)
                .and_then(|v| n.checked_add(v))
        })
        .and_then(|n| n.checked_add(16 * 1024 * 1024))
        .ok_or(StoreError::Budget)?;
    if allocation > b.max_memory_bytes {
        return Err(StoreError::Budget);
    }
    Ok(())
}

fn validate_stage_budget(plan: &AggregatePlan, b: &StageBudget) -> StoreResult<()> {
    if b.max_requests == 0
        || b.max_response_entity_bytes_total == 0
        || b.max_runtime_ms == 0
        || b.max_requests > plan.budget.max_requests
        || b.max_response_entity_bytes_total > plan.budget.max_total_response_entity_bytes
        || b.max_runtime_ms > plan.budget.max_runtime_ms
        || b.max_runtime_ms < plan.budget.response_timeout_ms
    {
        return Err(StoreError::Budget);
    }
    Ok(())
}

fn validate_authority(authority: &Authority, proposed: &str) -> StoreResult<()> {
    if let Authority::Approved {
        approval_id,
        operator,
        approved_at_ms,
        not_after_ms,
        approved_plan_sha256,
        cost_confirmation,
    } = authority
        && (!short_text(approval_id)
            || !short_text(operator)
            || *approved_at_ms == 0
            || not_after_ms <= approved_at_ms
            || approved_plan_sha256 != proposed
            || cost_confirmation != "CONFIRMED_NO_CREDIT_SPEND")
    {
        return Err(StoreError::Identity);
    }
    Ok(())
}

fn validate_metadata(plan: &AggregatePlan, lease: &MetadataLease) -> StoreResult<()> {
    if lease.schema != "OF1_METADATA_LEASE_1" {
        return Err(StoreError::Identity);
    }
    validate_stage_budget(plan, &lease.budget)?;
    if lease.budget.max_requests < 4
        || lease.budget.max_response_entity_bytes_total < SLOTS_PER_EPOCH * RECORD_BYTES + 8192
    {
        return Err(StoreError::Budget);
    }
    validate_authority(
        &lease.authority,
        &metadata_proposal_sha256(plan, &lease.budget)?,
    )
}

fn validate_payload(
    manifest: &Manifest,
    lease: &PayloadLease,
    prepared: &PreparedPayload,
    metadata_attempts: &[Reservation],
) -> StoreResult<()> {
    if lease.schema != "OF1_PAYLOAD_LEASE_1"
        || lease.prepared_payload_sha256 != prepared.sha256()?
        || lease.metadata_receipt_sha256 != prepared.metadata_receipt_sha256
        || matches!(manifest.metadata_lease.authority, Authority::Fixture)
            != matches!(lease.authority, Authority::Fixture)
        || prepared.requests.is_empty()
    {
        return Err(StoreError::Identity);
    }
    validate_stage_budget(&manifest.plan, &lease.budget)?;
    validate_authority(
        &lease.authority,
        &payload_proposal_sha256(&manifest.plan, &lease.budget, prepared)?,
    )?;
    let budget = &manifest.plan.budget;
    let requests = metadata_attempts.len() as u64;
    let charge = sum_charge(metadata_attempts.iter())?;
    if metadata_attempts.iter().any(|a| a.request.sequence >= 4)
        || requests
            .checked_add(lease.budget.max_requests)
            .ok_or(StoreError::Budget)?
            > budget.max_requests
        || charge
            .checked_add(lease.budget.max_response_entity_bytes_total)
            .ok_or(StoreError::Budget)?
            > budget.max_total_response_entity_bytes
        || manifest
            .metadata_lease
            .budget
            .max_runtime_ms
            .checked_add(lease.budget.max_runtime_ms)
            .ok_or(StoreError::Budget)?
            > budget.max_runtime_ms
    {
        return Err(StoreError::Budget);
    }
    let retries = u64::from(budget.request_retries) + 1;
    let payload_bytes = prepared.requests.iter().try_fold(0u64, |n, r| {
        n.checked_add(r.allowance()).ok_or(StoreError::Budget)
    })?;
    if (prepared.requests.len() as u64)
        .checked_mul(retries)
        .ok_or(StoreError::Budget)?
        > lease.budget.max_requests
        || payload_bytes
            .checked_mul(retries)
            .ok_or(StoreError::Budget)?
            > lease.budget.max_response_entity_bytes_total
    {
        return Err(StoreError::Budget);
    }
    Ok(())
}

fn make_stage(
    authority: &Authority,
    budget: &StageBudget,
    lease: &impl Serialize,
    started: &ClockSample,
) -> StoreResult<StageRecord> {
    valid_clock(started)?;
    let mut runtime = budget.max_runtime_ms;
    if let Authority::Approved {
        approved_at_ms,
        not_after_ms,
        ..
    } = authority
    {
        if started.wall_ms < *approved_at_ms || started.wall_ms >= *not_after_ms {
            return Err(StoreError::Deadline);
        }
        runtime = runtime.min(not_after_ms - started.wall_ms);
    }
    Ok(StageRecord {
        authority: authority.clone(),
        budget: budget.clone(),
        lease_sha256: sha256(&encode(lease)?),
        started_at: started.clone(),
        deadline_wall_ms: started
            .wall_ms
            .checked_add(runtime)
            .ok_or(StoreError::Clock)?,
        deadline_boot_ms: started
            .boot_ms
            .checked_add(runtime)
            .ok_or(StoreError::Clock)?,
    })
}

fn validate_stage(
    stage: &StageRecord,
    authority: &Authority,
    budget: &StageBudget,
    lease: &impl Serialize,
) -> StoreResult<()> {
    if &make_stage(authority, budget, lease, &stage.started_at)? != stage {
        return Err(StoreError::Identity);
    }
    Ok(())
}

fn within_stage(stage: &StageRecord, at: &ClockSample) -> StoreResult<()> {
    clock_follows(&stage.started_at, at)?;
    if at.wall_ms >= stage.deadline_wall_ms || at.boot_ms >= stage.deadline_boot_ms {
        return Err(StoreError::Deadline);
    }
    Ok(())
}

fn within_attempt(
    stage: &StageRecord,
    attempt: &Reservation,
    at: &ClockSample,
    timeout: u64,
) -> StoreResult<()> {
    clock_follows(&attempt.at, at)?;
    within_stage(stage, at)?;
    if at.wall_ms - attempt.at.wall_ms >= timeout || at.boot_ms - attempt.at.boot_ms >= timeout {
        return Err(StoreError::Deadline);
    }
    Ok(())
}

fn sum_charge<'a>(mut attempts: impl Iterator<Item = &'a Reservation>) -> StoreResult<u64> {
    attempts.try_fold(0u64, |n, a| {
        n.checked_add(a.reserved_entity_bytes)
            .ok_or(StoreError::Budget)
    })
}

fn capture_disk_allowance(entity: u64) -> StoreResult<u64> {
    rounded(entity)?
        .checked_mul(2)
        .and_then(|n| {
            entity
                .div_ceil(SEGMENT_BYTES as u64)
                .checked_mul(3 * BLOCK)
                .and_then(|v| n.checked_add(v))
        })
        .and_then(|n| n.checked_add(4 * RECORD_LIMIT))
        .ok_or(StoreError::Budget)
}

fn same_object(a: &Request, b: &Request, epoch: u64) -> bool {
    a.path(epoch) == b.path(epoch)
}
fn object_size(request: &Request, head: &ResponseHead) -> u64 {
    if matches!(request.kind, RequestKind::CarRange { .. }) {
        head.content_range.as_ref().map_or(0, |r| r.total)
    } else {
        head.content_length
    }
}
fn evidence(authority: &Authority) -> &'static str {
    if matches!(authority, Authority::Fixture) {
        "Fixture"
    } else {
        "UNREVIEWED_AUTHENTIC_RAW"
    }
}

/// Kernel counters are observed at bounded I/O gates; this is not an OS memory sandbox.
/// # Errors
/// Missing counters, arithmetic overflow or unavailable filesystem information fail closed.
pub fn resource_sample(root: &Path) -> StoreResult<(u64, u64, u64)> {
    let stat = rustix::fs::statvfs(root).map_err(std::io::Error::from)?;
    let available = stat
        .f_bavail
        .checked_mul(stat.f_frsize)
        .ok_or(StoreError::Budget)?;
    let mut text = String::new();
    File::open("/proc/self/status")?
        .take(65_537)
        .read_to_string(&mut text)?;
    if text.len() > 65_536 {
        return Err(StoreError::Corrupt);
    }
    let read = |key: &str| -> StoreResult<u64> {
        let line = text
            .lines()
            .find(|line| line.starts_with(key))
            .ok_or(StoreError::Corrupt)?;
        let words: Vec<_> = line.split_whitespace().collect();
        if words.len() != 3 || words[2] != "kB" {
            return Err(StoreError::Corrupt);
        }
        words[1]
            .parse::<u64>()
            .map_err(|_| StoreError::Corrupt)?
            .checked_mul(1024)
            .ok_or(StoreError::Budget)
    };
    Ok((available, read("VmRSS:")?, read("VmHWM:")?))
}

fn resources(root: &Path, budget: &AggregateBudget) -> StoreResult<()> {
    let (free, rss, _) = resource_sample(root)?;
    if free < budget.required_free_disk_bytes || rss > budget.max_memory_bytes {
        return Err(StoreError::Budget);
    }
    Ok(())
}
