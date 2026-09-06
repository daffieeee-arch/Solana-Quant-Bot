//! Fixture-only immutable Raw/receipt store. No transport, decoder or acquisition authority.
//! A durable reservation precedes every permit. Unknown interrupted bytes stay unknown;
//! their full reserved allowance remains spent. No mutable checkpoint or automatic repair.

pub mod acquisition;

use crate::{
    ByteRequest, OfflinePlan, PersistedIndex, RangePlan, plan_ranges, sha256, validate_plan,
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use thiserror::Error;

const JSON_LIMIT: u64 = 16_384;
const BLOCK: u64 = 4096;
pub const MAX_STREAM_CHUNK_BYTES: usize = 8192;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FaultPoint {
    BeforeStagePublish,
    AfterStagePublish,
    BeforeReservationPublish,
    AfterReservationPublish,
    AfterRawWrite,
    AfterRawSync,
    AfterReceiptSync,
    BeforePublish,
    AfterPublish,
    AfterPublishSync,
}

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("durable I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("store identity or plan mismatch")]
    Identity,
    #[error("corrupt or incompatible store/response")]
    Corrupt,
    #[error("offline integrity quarantine: {0}")]
    Integrity(String),
    #[error("hard budget exhausted")]
    Budget,
    #[error("original deadline or attempt timeout reached")]
    Deadline,
    #[error("clock rollback, invalid clock or boot identity changed")]
    Clock,
    #[error("writer lock unavailable or replaced")]
    Locked,
    #[error("store is poisoned; close and revalidate before continuing")]
    Poisoned,
    #[error("request already published")]
    AlreadyPublished,
    #[error("SOURCE_DRIFT: response total or source validator changed")]
    SourceDrift,
    #[error("CONFLICTING_BYTES: retained same-range overlap disagrees")]
    ConflictingBytes,
    #[error("injected crash seam: {0:?}")]
    Injected(FaultPoint),
}

pub type StoreResult<T> = Result<T, StoreError>;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ClockSample {
    pub wall_ms: u64,
    pub boot_ms: u64,
    pub boot_id: String,
}

pub trait Clock {
    /// # Errors
    /// A clock without usable wall/boot identity cannot authorize a fixture attempt.
    fn sample(&self) -> StoreResult<ClockSample>;
}

/// Linux/WSL boot elapsed time includes process downtime and suspend. Reboot fails closed.
pub struct SystemClock;

impl Clock for SystemClock {
    fn sample(&self) -> StoreResult<ClockSample> {
        let wall_ms = u64::try_from(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|_| StoreError::Clock)?
                .as_millis(),
        )
        .map_err(|_| StoreError::Clock)?;
        let uptime = fs::read_to_string("/proc/uptime")?;
        let value = uptime.split_whitespace().next().ok_or(StoreError::Clock)?;
        let (seconds, fraction) = value.split_once('.').ok_or(StoreError::Clock)?;
        if fraction.len() > 3
            || fraction.is_empty()
            || !fraction.bytes().all(|b| b.is_ascii_digit())
        {
            return Err(StoreError::Clock);
        }
        let millis = format!("{fraction:0<3}")
            .parse::<u64>()
            .map_err(|_| StoreError::Clock)?;
        let boot_ms = seconds
            .parse::<u64>()
            .map_err(|_| StoreError::Clock)?
            .checked_mul(1000)
            .and_then(|s| s.checked_add(millis))
            .ok_or(StoreError::Clock)?;
        Ok(ClockSample {
            wall_ms,
            boot_ms,
            boot_id: fs::read_to_string("/proc/sys/kernel/random/boot_id")?
                .trim()
                .into(),
        })
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Response {
    pub status: u16,
    pub start: u64,
    pub end_exclusive: u64,
    pub total: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct StreamHead {
    pub response: Response,
    pub strong_etag: Option<String>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RetryComparison {
    NoPriorBytes,
    RetainedOverlapMatched,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StreamIdentity {
    reservation: Reservation,
    head: StreamHead,
    at: ClockSample,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ChunkReceipt {
    run_id: String,
    attempt_id: u64,
    offset: u64,
    length: u64,
    sha256: String,
    at: ClockSample,
}

struct StreamRecord {
    identity: StreamIdentity,
    bytes: Vec<u8>,
    chunks: u64,
    latest_at: ClockSample,
}

#[derive(Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum QuarantineReason {
    SourceDrift,
    ConflictingBytes,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Quarantine {
    schema: String,
    reservation: Reservation,
    reason: QuarantineReason,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    run_id: String,
    plan: OfflinePlan,
    range_plan: RangePlan,
    executable_sha256: String,
    started_at: ClockSample,
    deadline_wall_ms: u64,
    deadline_boot_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Reservation {
    run_id: String,
    plan_sha256: String,
    attempt_id: u64,
    request_sequence: u64,
    reserved_entity_bytes: u64,
    at: ClockSample,
}

/// Non-cloneable, run-bound fixture permit; only constructed after reservation + parent fsync.
pub struct Permit(Reservation);

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub schema: String,
    pub run_id: String,
    pub plan_sha256: String,
    pub index_sha256: String,
    pub attempt_id: u64,
    pub request: ByteRequest,
    pub response: Response,
    pub response_entity_bytes: u64,
    pub sha256: String,
    pub acquired_at: ClockSample,
    pub evidence: String,
    pub domain_counts: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stream_head: Option<StreamHead>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub retry_comparison: Option<RetryComparison>,
}

pub struct VerifiedObject {
    pub receipt: Receipt,
    pub bytes: Vec<u8>,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Summary {
    pub attempts_reserved: u64,
    pub charged_entity_bytes: u64,
    pub verified_response_entity_bytes: u64,
    pub published_requests: u64,
    pub unpublished_attempts: u64,
    pub unreceipted_response_entity_bytes: Option<u64>,
    pub deadline_wall_ms: u64,
    pub deadline_boot_ms: u64,
    pub evidence: String,
    pub domain_counts: String,
}

struct Audit {
    attempts: Vec<Reservation>,
    published: BTreeMap<u64, VerifiedObject>,
    high_water: ClockSample,
    streams: BTreeMap<u64, StreamRecord>,
}

pub struct Store<C: Clock> {
    root: PathBuf,
    lock: File,
    root_identity: (u64, u64),
    manifest: Manifest,
    manifest_bytes: Vec<u8>,
    clock: C,
    high_water: ClockSample,
    inflight: Option<u64>,
    poisoned: bool,
    fault: Option<FaultPoint>,
}

impl<C: Clock> Store<C> {
    /// Create a new fixture store only; never overwrite/import any existing run.
    /// # Errors
    /// Rejects invalid plan/index, missing parent, existing root, I/O or insufficient disk budget.
    pub fn create(root: &Path, plan: OfflinePlan, index: &Path, clock: C) -> StoreResult<Self> {
        let validated = validate_plan(plan).map_err(|_| StoreError::Identity)?;
        let persisted =
            PersistedIndex::read(&validated, index).map_err(|_| StoreError::Identity)?;
        let ranges = plan_ranges(&validated, &persisted).map_err(|_| StoreError::Budget)?;
        let started_at = clock.sample()?;
        valid_clock(&started_at)?;
        let runtime = validated.plan().budget.max_runtime_ms;
        let mut nonce = [0u8; 32];
        File::open("/dev/urandom")?.read_exact(&mut nonce)?;
        let manifest = Manifest {
            schema: "OF1_FIXTURE_STORE_1".into(),
            run_id: hex::encode(nonce),
            plan: validated.plan().clone(),
            range_plan: ranges,
            executable_sha256: executable_hash()?,
            deadline_wall_ms: started_at
                .wall_ms
                .checked_add(runtime)
                .ok_or(StoreError::Clock)?,
            deadline_boot_ms: started_at
                .boot_ms
                .checked_add(runtime)
                .ok_or(StoreError::Clock)?,
            started_at: started_at.clone(),
        };
        let manifest_bytes = encode(&manifest)?;
        fs::create_dir(root)?;
        let root_identity = identity(&fs::symlink_metadata(root)?);
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(root.join("writer.lock"))?;
        lock.try_lock().map_err(|_| StoreError::Locked)?;
        let store = Self {
            root: root.into(),
            lock,
            root_identity,
            manifest,
            manifest_bytes,
            clock,
            high_water: started_at,
            inflight: None,
            poisoned: false,
            fault: None,
        };
        for name in ["attempts", "pending", "published"] {
            fs::create_dir(root.join(name))?;
        }
        store.write_new(&root.join("run.json"), &store.manifest_bytes)?;
        // Re-read after planning: an index changed between the two reads must not be published.
        let bytes = read_bounded(index, crate::SLOTS_PER_EPOCH * crate::RECORD_BYTES)?;
        if sha256(&bytes) != store.manifest.plan.index_sha256 {
            return Err(StoreError::Identity);
        }
        store.write_new(&root.join("index.raw"), &bytes)?;
        sync_dir(root)?;
        sync_dir(root.parent().ok_or(StoreError::Corrupt)?)?;
        store.audit()?;
        Ok(store)
    }

    /// Revalidate every canonical artifact and the original clocks before any new permit.
    /// # Errors
    /// Corruption, incompatible executable/context, boot changes and expired deadlines fail closed.
    pub fn resume(root: &Path, plan: OfflinePlan, index: &Path, clock: C) -> StoreResult<Self> {
        Self::open_checked(root, plan, index, clock, true)
    }

    /// Read-only forensic summary after a deadline stop; does not return a writer or permit.
    /// # Errors
    /// All identity/integrity/lock checks still apply; expiration alone does not hide evidence.
    pub fn inspect(root: &Path, plan: OfflinePlan, index: &Path, clock: C) -> StoreResult<Summary> {
        Self::open_checked(root, plan, index, clock, false)?.summary()
    }

    fn open_checked(
        root: &Path,
        plan: OfflinePlan,
        index: &Path,
        clock: C,
        enforce_deadline: bool,
    ) -> StoreResult<Self> {
        let validated = validate_plan(plan).map_err(|_| StoreError::Identity)?;
        let persisted =
            PersistedIndex::read(&validated, index).map_err(|_| StoreError::Identity)?;
        let expected = plan_ranges(&validated, &persisted).map_err(|_| StoreError::Identity)?;
        let root_meta = fs::symlink_metadata(root)?;
        if !root_meta.is_dir() {
            return Err(StoreError::Corrupt);
        }
        regular(&root.join("writer.lock"))?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .open(root.join("writer.lock"))?;
        lock.try_lock().map_err(|_| StoreError::Locked)?;
        let manifest_bytes = read_bounded(
            &root.join("run.json"),
            validated.plan().budget.max_disk_bytes,
        )?;
        let manifest: Manifest = decode(&manifest_bytes)?;
        valid_clock(&manifest.started_at)?;
        if manifest.schema != "OF1_FIXTURE_STORE_1"
            || manifest.range_plan != expected
            || encode(&manifest.plan)? != encode(validated.plan())?
            || manifest.executable_sha256 != executable_hash()?
            || manifest.run_id.len() != 64
            || !manifest.run_id.bytes().all(|b| b.is_ascii_hexdigit())
            || manifest.deadline_wall_ms
                != manifest
                    .started_at
                    .wall_ms
                    .checked_add(manifest.plan.budget.max_runtime_ms)
                    .ok_or(StoreError::Clock)?
            || manifest.deadline_boot_ms
                != manifest
                    .started_at
                    .boot_ms
                    .checked_add(manifest.plan.budget.max_runtime_ms)
                    .ok_or(StoreError::Clock)?
        {
            return Err(StoreError::Identity);
        }
        let mut store = Self {
            root: root.into(),
            lock,
            root_identity: identity(&root_meta),
            high_water: manifest.started_at.clone(),
            manifest,
            manifest_bytes,
            clock,
            inflight: None,
            poisoned: false,
            fault: None,
        };
        let audit = store.audit()?;
        store.high_water = audit.high_water;
        if enforce_deadline {
            store.now()?;
        }
        Ok(store)
    }

    /// Reserve before dispatch; failures and process death never refund the published reservation.
    /// # Errors
    /// Fails on stale state, outstanding permit, duplicate publication, budgets, deadline or I/O.
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
        let audit = self.audit()?;
        if audit.published.contains_key(&sequence) {
            return Err(StoreError::AlreadyPublished);
        }
        let request = self.request(sequence)?;
        let allowance = request.end_exclusive - request.start;
        let charged = sum_charged(&audit.attempts)?;
        let count = u64::try_from(audit.attempts.len()).map_err(|_| StoreError::Budget)?;
        let retries = audit
            .attempts
            .iter()
            .filter(|a| a.request_sequence == sequence)
            .count();
        let budget = &self.manifest.plan.budget;
        if count >= budget.max_requests
            || u64::try_from(retries).map_err(|_| StoreError::Budget)?
                > u64::from(budget.request_retries)
            || charged.checked_add(allowance).ok_or(StoreError::Budget)?
                > budget.max_total_response_entity_bytes
        {
            return Err(StoreError::Budget);
        }
        // Includes reservation temporary hard link and a whole candidate pair before dispatch.
        self.space(
            rounded(allowance)?
                .checked_add(6 * BLOCK)
                .ok_or(StoreError::Budget)?,
        )?;
        let at = self.now()?;
        let reservation = Reservation {
            run_id: self.manifest.run_id.clone(),
            plan_sha256: self.manifest.range_plan.plan_sha256.clone(),
            attempt_id: count,
            request_sequence: sequence,
            reserved_entity_bytes: allowance,
            at,
        };
        let bytes = encode(&reservation)?;
        if bytes.len() as u64 > JSON_LIMIT {
            return Err(StoreError::Budget);
        }
        let temporary = self
            .root
            .join("pending")
            .join(format!("reserve-{count:010}.json"));
        // Retain intent as forensic evidence. A missing canonical partner on resume is
        // ambiguous (pre-publication crash OR lost spent record), never a budget refund.
        self.write_new(&temporary, &bytes)?;
        sync_dir(&self.root.join("pending"))?;
        self.trip(FaultPoint::BeforeReservationPublish)?;
        fs::hard_link(
            &temporary,
            self.root.join("attempts").join(format!("{count:010}.json")),
        )?;
        sync_dir(&self.root.join("attempts"))?;
        self.trip(FaultPoint::AfterReservationPublish)?;
        self.attempt_now(&reservation)?;
        self.inflight = Some(count);
        Ok(Permit(reservation))
    }

    /// Bound the next blocking I/O operation by both original and attempt deadlines.
    /// # Errors
    /// Invalid/outdated permits, corrupt state or expired clocks fail closed.
    pub fn remaining_ms(&mut self, permit: &Permit) -> StoreResult<u64> {
        let result = self.remaining_ms_inner(permit);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn remaining_ms_inner(&mut self, permit: &Permit) -> StoreResult<u64> {
        // Called before each blocking I/O: do not re-read the epoch index per header byte.
        // Durable artifact audits remain at reserve/begin/prepare/append/finish/resume.
        self.permit_identity(permit)?;
        let at = self.attempt_now(&permit.0)?;
        let timeout = self.manifest.plan.budget.response_timeout_ms;
        [
            self.manifest.deadline_wall_ms - at.wall_ms,
            self.manifest.deadline_boot_ms - at.boot_ms,
            timeout - (at.wall_ms - permit.0.at.wall_ms),
            timeout - (at.boot_ms - permit.0.at.boot_ms),
        ]
        .into_iter()
        .min()
        .ok_or(StoreError::Deadline)
    }

    /// Store the exact validated fixture response head before retaining any entity bytes.
    /// # Errors
    /// Wrong framing, source drift, clock/budget failure and duplicate starts poison the writer.
    pub fn begin_stream(&mut self, permit: &Permit, head: StreamHead) -> StoreResult<()> {
        let result = self.begin_stream_inner(permit, head);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn begin_stream_inner(&mut self, permit: &Permit, head: StreamHead) -> StoreResult<()> {
        let audit = self.checked_permit(permit)?;
        if head.response.total != self.manifest.plan.object_size {
            return self.quarantine(&permit.0, QuarantineReason::SourceDrift);
        }
        if !response_matches(
            &head.response,
            self.request(permit.0.request_sequence)?,
            self.manifest.plan.object_size,
        ) || !valid_etag(head.strong_etag.as_deref())
        {
            return Err(StoreError::Corrupt);
        }
        self.compare_prior(&permit.0, &head, &[], &audit)?;
        let at = self.attempt_now(&permit.0)?;
        self.space(3 * BLOCK)?;
        let path = self.stream_path(permit.0.attempt_id);
        fs::create_dir(&path)?;
        self.write_new(
            &path.join("head.json"),
            &encode(&StreamIdentity {
                reservation: permit.0.clone(),
                head,
                at,
            })?,
        )?;
        sync_dir(&path)?;
        sync_dir(&self.root.join("pending"))?;
        self.attempt_now(&permit.0)?;
        Ok(())
    }

    /// Check capacity before reading a bounded next entity chunk from the local transport.
    /// # Errors
    /// Invalid permit, missing head, deadline, length and conservative disk caps fail closed.
    pub fn prepare_stream_read(&mut self, permit: &Permit, max_bytes: u64) -> StoreResult<()> {
        let result = self.prepare_stream_read_inner(permit, max_bytes);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn prepare_stream_read_inner(&mut self, permit: &Permit, max_bytes: u64) -> StoreResult<()> {
        let audit = self.checked_permit(permit)?;
        let stream = audit
            .streams
            .get(&permit.0.attempt_id)
            .ok_or(StoreError::Corrupt)?;
        if max_bytes == 0
            || max_bytes > MAX_STREAM_CHUNK_BYTES as u64
            || stream.bytes.len() as u64 + max_bytes > permit.0.reserved_entity_bytes
        {
            return Err(StoreError::Budget);
        }
        // Chunk directory + hash receipt, eventual full Raw copy + publication receipt,
        // and one terminal quarantine marker. No future capacity is silently assumed.
        self.space(
            rounded(max_bytes)?
                .checked_add(rounded(permit.0.reserved_entity_bytes)?)
                .and_then(|n| n.checked_add(5 * BLOCK))
                .ok_or(StoreError::Budget)?,
        )?;
        self.attempt_now(&permit.0)?;
        Ok(())
    }

    /// Retain one hashed, fsynced prefix chunk. Chunk boundaries do not affect overlap checks.
    /// # Errors
    /// Changed overlap becomes durable quarantine; incomplete chunk publication is never repaired.
    pub fn append_stream(&mut self, permit: &Permit, bytes: &[u8]) -> StoreResult<()> {
        let result = self.append_stream_inner(permit, bytes);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn append_stream_inner(&mut self, permit: &Permit, bytes: &[u8]) -> StoreResult<()> {
        self.prepare_stream_read(permit, bytes.len() as u64)?;
        let audit = self.checked_permit(permit)?;
        let stream = audit
            .streams
            .get(&permit.0.attempt_id)
            .ok_or(StoreError::Corrupt)?;
        let mut prefix = stream.bytes.clone();
        prefix.extend_from_slice(bytes);
        self.compare_prior(&permit.0, &stream.identity.head, &prefix, &audit)?;
        let receipt = ChunkReceipt {
            run_id: self.manifest.run_id.clone(),
            attempt_id: permit.0.attempt_id,
            offset: stream.bytes.len() as u64,
            length: bytes.len() as u64,
            sha256: sha256(bytes),
            at: self.attempt_now(&permit.0)?,
        };
        let path = self.stream_path(permit.0.attempt_id);
        let stage = path.join(format!("incomplete-{:010}", stream.chunks));
        fs::create_dir(&stage)?;
        self.write_new(&stage.join("raw.bin"), bytes)?;
        self.write_new(&stage.join("chunk.json"), &encode(&receipt)?)?;
        sync_dir(&stage)?;
        self.attempt_now(&permit.0)?;
        let destination = path.join(format!("chunk-{:010}", stream.chunks));
        if destination.exists() {
            return Err(StoreError::Corrupt);
        }
        fs::rename(&stage, destination)?;
        sync_dir(&path)?;
        self.audit()?;
        Ok(())
    }

    /// Publish a completed stream using the existing atomic Raw/receipt pair mechanism.
    /// # Errors
    /// Truncation, disagreement, late completion or corrupt persisted chunks fail closed.
    pub fn finish_stream(&mut self, permit: Permit) -> StoreResult<Receipt> {
        let result = (|| {
            let audit = self.checked_permit(&permit)?;
            let stream = audit
                .streams
                .get(&permit.0.attempt_id)
                .ok_or(StoreError::Corrupt)?;
            self.commit_inner(
                permit,
                stream.identity.head.response.clone(),
                &stream.bytes,
                Some(stream.identity.head.clone()),
            )
        })();
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    /// Transport failure never refunds a reservation or deletes retained prefix evidence.
    pub fn abort_stream(&mut self) {
        self.poisoned = true;
    }

    fn checked_permit(&self, permit: &Permit) -> StoreResult<Audit> {
        self.permit_identity(permit)?;
        let audit = self.audit()?;
        if audit
            .attempts
            .get(usize::try_from(permit.0.attempt_id).map_err(|_| StoreError::Corrupt)?)
            != Some(&permit.0)
        {
            return Err(StoreError::Corrupt);
        }
        Ok(audit)
    }

    fn permit_identity(&self, permit: &Permit) -> StoreResult<()> {
        self.usable()?;
        if permit.0.run_id != self.manifest.run_id || self.inflight != Some(permit.0.attempt_id) {
            return Err(StoreError::Identity);
        }
        Ok(())
    }

    /// Publish only a complete Raw/receipt pair; errors poison the writer, never replay a mutation.
    /// Injected bytes are fixture input, not a live response or authenticity claim.
    /// # Errors
    /// Rejects invalid permit/response, overwritten state, late completion, disk failure or crash seam.
    pub fn commit(
        &mut self,
        permit: Permit,
        response: Response,
        bytes: &[u8],
    ) -> StoreResult<Receipt> {
        let result = self.commit_inner(permit, response, bytes, None);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn commit_inner(
        &mut self,
        permit: Permit,
        response: Response,
        bytes: &[u8],
        stream_head: Option<StreamHead>,
    ) -> StoreResult<Receipt> {
        self.usable()?;
        let reservation = permit.0;
        if reservation.run_id != self.manifest.run_id
            || self.inflight != Some(reservation.attempt_id)
        {
            return Err(StoreError::Identity);
        }
        let audit = self.audit()?;
        if audit
            .attempts
            .get(usize::try_from(reservation.attempt_id).map_err(|_| StoreError::Corrupt)?)
            != Some(&reservation)
        {
            return Err(StoreError::Corrupt);
        }
        if audit.streams.contains_key(&reservation.attempt_id) != stream_head.is_some() {
            return Err(StoreError::Corrupt);
        }
        let comparison = self.compare_prior(
            &reservation,
            &stream_head.clone().unwrap_or(StreamHead {
                response: response.clone(),
                strong_etag: None,
            }),
            bytes,
            &audit,
        )?;
        let request = self.request(reservation.request_sequence)?;
        if !response_matches(&response, request, self.manifest.plan.object_size)
            || u64::try_from(bytes.len()).map_err(|_| StoreError::Budget)?
                != reservation.reserved_entity_bytes
        {
            return Err(StoreError::Corrupt);
        }
        let request: ByteRequest = decode(&encode(request)?)?;
        let acquired_at = self.attempt_now(&reservation)?;
        let receipt = Receipt {
            schema: "OF1_FIXTURE_RECEIPT_1".into(),
            run_id: reservation.run_id.clone(),
            plan_sha256: reservation.plan_sha256.clone(),
            index_sha256: self.manifest.plan.index_sha256.clone(),
            attempt_id: reservation.attempt_id,
            request,
            response,
            response_entity_bytes: reservation.reserved_entity_bytes,
            sha256: sha256(bytes),
            acquired_at,
            evidence: "Fixture".into(),
            domain_counts: "UNAVAILABLE_NOT_DECODED_IN_B4".into(),
            retry_comparison: stream_head.as_ref().map(|_| comparison),
            stream_head,
        };
        let candidate = self
            .root
            .join("pending")
            .join(format!("{:010}", reservation.attempt_id));
        self.space(BLOCK)?;
        fs::create_dir(&candidate)?;
        sync_dir(&self.root.join("pending"))?;
        self.space(rounded(bytes.len() as u64)?)?;
        let mut raw = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(candidate.join("raw.bin"))?;
        raw.write_all(bytes)?;
        self.trip(FaultPoint::AfterRawWrite)?;
        raw.sync_all()?;
        self.trip(FaultPoint::AfterRawSync)?;
        self.write_new(&candidate.join("receipt.json"), &encode(&receipt)?)?;
        self.trip(FaultPoint::AfterReceiptSync)?;
        sync_dir(&candidate)?;
        self.trip(FaultPoint::BeforePublish)?;
        self.attempt_now(&reservation)?;
        self.audit()?;
        let destination = self
            .root
            .join("published")
            .join(format!("{:010}", reservation.request_sequence));
        if destination.exists() {
            return Err(StoreError::AlreadyPublished);
        }
        // Full integrity audit may take time; do not use its pre-audit clock as authority.
        self.attempt_now(&reservation)?;
        fs::rename(&candidate, &destination)?;
        self.trip(FaultPoint::AfterPublish)?;
        sync_dir(&self.root.join("published"))?;
        sync_dir(&self.root.join("pending"))?;
        self.trip(FaultPoint::AfterPublishSync)?;
        self.inflight = None;
        let verified = self
            .published(reservation.request_sequence)?
            .ok_or(StoreError::Corrupt)?;
        if verified.receipt != receipt {
            return Err(StoreError::Corrupt);
        }
        Ok(receipt)
    }

    /// Read-only audit; never exposes staging bytes as published Raw evidence.
    /// # Errors
    /// Rejects altered/missing bytes, receipt fields, identity or unrelated store contents.
    pub fn published(&self, sequence: u64) -> StoreResult<Option<VerifiedObject>> {
        Ok(self.audit()?.published.remove(&sequence))
    }

    /// Read-only progress even after a budget/deadline stop; no domain counts are invented.
    /// # Errors
    /// Corrupt artifacts or arithmetic overflow fail closed.
    pub fn summary(&self) -> StoreResult<Summary> {
        let audit = self.audit()?;
        let attempts_reserved = audit.attempts.len() as u64;
        let published_requests = audit.published.len() as u64;
        let verified_response_entity_bytes =
            audit.published.values().try_fold(0u64, |sum, o| {
                sum.checked_add(o.receipt.response_entity_bytes)
                    .ok_or(StoreError::Budget)
            })?;
        Ok(Summary {
            attempts_reserved,
            charged_entity_bytes: sum_charged(&audit.attempts)?,
            verified_response_entity_bytes,
            published_requests,
            unpublished_attempts: attempts_reserved - published_requests,
            unreceipted_response_entity_bytes: None,
            deadline_wall_ms: self.manifest.deadline_wall_ms,
            deadline_boot_ms: self.manifest.deadline_boot_ms,
            evidence: "Fixture".into(),
            domain_counts: "UNAVAILABLE_NOT_DECODED_IN_B4".into(),
        })
    }

    /// Deterministic offline crash injection; an injected error always requires reopening.
    pub fn inject_fault(&mut self, point: FaultPoint) {
        self.fault = Some(point);
    }

    fn trip(&mut self, point: FaultPoint) -> StoreResult<()> {
        if self.fault == Some(point) {
            self.fault = None;
            self.poisoned = true;
            return Err(StoreError::Injected(point));
        }
        Ok(())
    }

    fn usable(&self) -> StoreResult<()> {
        if self.poisoned {
            Err(StoreError::Poisoned)
        } else {
            Ok(())
        }
    }

    /// Exact planned range identity; never accepts a caller-supplied URL.
    /// # Errors
    /// Unknown sequence fails closed.
    pub fn request(&self, sequence: u64) -> StoreResult<&ByteRequest> {
        self.manifest
            .range_plan
            .requests
            .get(usize::try_from(sequence).map_err(|_| StoreError::Corrupt)?)
            .filter(|r| r.sequence == sequence)
            .ok_or(StoreError::Corrupt)
    }

    #[must_use]
    pub fn source_path(&self) -> String {
        format!("/{0}/epoch-{0}.car", self.manifest.plan.epoch)
    }

    #[must_use]
    pub fn object_size(&self) -> u64 {
        self.manifest.plan.object_size
    }

    fn stream_path(&self, attempt_id: u64) -> PathBuf {
        self.root
            .join("pending")
            .join(format!("stream-{attempt_id:010}"))
    }

    fn compare_prior(
        &self,
        reservation: &Reservation,
        head: &StreamHead,
        bytes: &[u8],
        audit: &Audit,
    ) -> StoreResult<RetryComparison> {
        let mut compared = false;
        for (id, stream) in &audit.streams {
            if *id == reservation.attempt_id {
                continue;
            }
            if head.response.total != stream.identity.head.response.total
                || head.strong_etag != stream.identity.head.strong_etag
            {
                return self.quarantine(reservation, QuarantineReason::SourceDrift);
            }
            if stream.identity.reservation.request_sequence == reservation.request_sequence {
                let overlap = bytes.len().min(stream.bytes.len());
                if bytes[..overlap] != stream.bytes[..overlap] {
                    return self.quarantine(reservation, QuarantineReason::ConflictingBytes);
                }
                compared |= overlap != 0;
            }
        }
        Ok(if compared {
            RetryComparison::RetainedOverlapMatched
        } else {
            RetryComparison::NoPriorBytes
        })
    }

    fn quarantine<T>(&self, reservation: &Reservation, reason: QuarantineReason) -> StoreResult<T> {
        // Creation itself is terminal evidence: a torn record fails audit too. Never overwrite
        // a disagreement marker or permit retries to run past it after process restart.
        self.space(BLOCK)?;
        let path = self.root.join("pending/quarantine.json");
        let mut marker = OpenOptions::new().write(true).create_new(true).open(path)?;
        marker.sync_all()?;
        sync_dir(&self.root.join("pending"))?;
        marker.write_all(&encode(&Quarantine {
            schema: "OF1_FIXTURE_QUARANTINE_1".into(),
            reservation: reservation.clone(),
            reason,
        })?)?;
        marker.sync_all()?;
        sync_dir(&self.root.join("pending"))?;
        Err(quarantine_error(reason))
    }

    fn now(&mut self) -> StoreResult<ClockSample> {
        let at = self.clock.sample()?;
        clock_follows(&self.high_water, &at)?;
        if at.wall_ms >= self.manifest.deadline_wall_ms
            || at.boot_ms >= self.manifest.deadline_boot_ms
        {
            return Err(StoreError::Deadline);
        }
        self.high_water = at.clone();
        Ok(at)
    }

    fn attempt_now(&mut self, attempt: &Reservation) -> StoreResult<ClockSample> {
        let at = self.now()?;
        let timeout = self.manifest.plan.budget.response_timeout_ms;
        if at.wall_ms - attempt.at.wall_ms >= timeout || at.boot_ms - attempt.at.boot_ms >= timeout
        {
            return Err(StoreError::Deadline);
        }
        Ok(at)
    }

    fn audit(&self) -> StoreResult<Audit> {
        if identity(&fs::symlink_metadata(&self.root)?) != self.root_identity
            || identity(&regular(&self.root.join("writer.lock"))?)
                != identity(&self.lock.metadata()?)
        {
            return Err(StoreError::Locked);
        }
        exact_names(
            &self.root,
            &[
                "writer.lock",
                "run.json",
                "index.raw",
                "attempts",
                "pending",
                "published",
            ],
        )?;
        if read_bounded(
            &self.root.join("run.json"),
            self.manifest.plan.budget.max_disk_bytes,
        )? != self.manifest_bytes
        {
            return Err(StoreError::Identity);
        }
        let validated =
            validate_plan(self.manifest.plan.clone()).map_err(|_| StoreError::Identity)?;
        PersistedIndex::read(&validated, &self.root.join("index.raw"))
            .map_err(|_| StoreError::Identity)?;
        self.space(0)?;
        let (attempts, mut high_water) = self.read_attempts()?;
        let published = self.read_published(&attempts, &mut high_water)?;
        let streams = self.check_pending(&attempts, &published, &mut high_water)?;
        Ok(Audit {
            attempts,
            published,
            high_water,
            streams,
        })
    }

    fn read_attempts(&self) -> StoreResult<(Vec<Reservation>, ClockSample)> {
        let mut attempts = Vec::new();
        let mut high_water = self.manifest.started_at.clone();
        let mut per_request = BTreeMap::<u64, u64>::new();
        for path in children(
            &self.root.join("attempts"),
            self.manifest.plan.budget.max_requests,
        )? {
            let attempt: Reservation = decode(&read_bounded(&path, JSON_LIMIT)?)?;
            let id = attempts.len() as u64;
            if path.file_name().and_then(|s| s.to_str()) != Some(format!("{id:010}.json").as_str())
                || attempt.attempt_id != id
                || attempt.run_id != self.manifest.run_id
                || attempt.plan_sha256 != self.manifest.range_plan.plan_sha256
            {
                return Err(StoreError::Corrupt);
            }
            let request = self.request(attempt.request_sequence)?;
            if attempt.reserved_entity_bytes != request.end_exclusive - request.start {
                return Err(StoreError::Corrupt);
            }
            clock_follows(&high_water, &attempt.at)?;
            self.within_run(&attempt.at)?;
            high_water = attempt.at.clone();
            let count = per_request.entry(attempt.request_sequence).or_default();
            *count += 1;
            if *count > u64::from(self.manifest.plan.budget.request_retries) + 1 {
                return Err(StoreError::Corrupt);
            }
            attempts.push(attempt);
        }
        if sum_charged(&attempts)? > self.manifest.plan.budget.max_total_response_entity_bytes {
            return Err(StoreError::Corrupt);
        }
        Ok((attempts, high_water))
    }

    fn read_published(
        &self,
        attempts: &[Reservation],
        high_water: &mut ClockSample,
    ) -> StoreResult<BTreeMap<u64, VerifiedObject>> {
        let mut published = BTreeMap::new();
        for path in children(
            &self.root.join("published"),
            self.manifest.plan.budget.max_requests,
        )? {
            exact_names(&path, &["raw.bin", "receipt.json"])?;
            let receipt: Receipt = decode(&read_bounded(&path.join("receipt.json"), JSON_LIMIT)?)?;
            let reservation = attempts
                .get(usize::try_from(receipt.attempt_id).map_err(|_| StoreError::Corrupt)?)
                .ok_or(StoreError::Corrupt)?;
            let request = self.request(reservation.request_sequence)?;
            if receipt.schema != "OF1_FIXTURE_RECEIPT_1"
                || receipt.run_id != self.manifest.run_id
                || receipt.plan_sha256 != self.manifest.range_plan.plan_sha256
                || receipt.index_sha256 != self.manifest.plan.index_sha256
                || &receipt.request != request
                || receipt.response_entity_bytes != reservation.reserved_entity_bytes
                || !response_matches(&receipt.response, request, self.manifest.plan.object_size)
                || receipt.evidence != "Fixture"
                || receipt.domain_counts != "UNAVAILABLE_NOT_DECODED_IN_B4"
                || path.file_name().and_then(|s| s.to_str())
                    != Some(format!("{:010}", request.sequence).as_str())
            {
                return Err(StoreError::Corrupt);
            }
            if let Some(head) = &receipt.stream_head {
                let stream = self.read_stream(reservation)?;
                if &stream.identity.head != head
                    || head.response != receipt.response
                    || stream.bytes.len() as u64 != receipt.response_entity_bytes
                    || sha256(&stream.bytes) != receipt.sha256
                    || receipt.retry_comparison.is_none()
                {
                    return Err(StoreError::Corrupt);
                }
            } else if receipt.retry_comparison.is_some() {
                return Err(StoreError::Corrupt);
            }
            clock_follows(&reservation.at, &receipt.acquired_at)?;
            // A later permit cannot predate a previously completed receipt.
            for later in attempts
                .iter()
                .skip(usize::try_from(receipt.attempt_id).map_err(|_| StoreError::Corrupt)? + 1)
            {
                clock_follows(&receipt.acquired_at, &later.at)?;
            }
            self.within_run(&receipt.acquired_at)?;
            if receipt.acquired_at.wall_ms - reservation.at.wall_ms
                >= self.manifest.plan.budget.response_timeout_ms
                || receipt.acquired_at.boot_ms - reservation.at.boot_ms
                    >= self.manifest.plan.budget.response_timeout_ms
            {
                return Err(StoreError::Corrupt);
            }
            high_water.wall_ms = high_water.wall_ms.max(receipt.acquired_at.wall_ms);
            high_water.boot_ms = high_water.boot_ms.max(receipt.acquired_at.boot_ms);
            let bytes = read_bounded(&path.join("raw.bin"), reservation.reserved_entity_bytes)?;
            if bytes.len() as u64 != reservation.reserved_entity_bytes
                || sha256(&bytes) != receipt.sha256
            {
                return Err(StoreError::Corrupt);
            }
            if published
                .insert(request.sequence, VerifiedObject { receipt, bytes })
                .is_some()
            {
                return Err(StoreError::Corrupt);
            }
        }
        Ok(published)
    }

    fn check_pending(
        &self,
        attempts: &[Reservation],
        published: &BTreeMap<u64, VerifiedObject>,
        high_water: &mut ClockSample,
    ) -> StoreResult<BTreeMap<u64, StreamRecord>> {
        let mut streams = BTreeMap::new();
        // Unpublished staging is retained and charged, never promoted during recovery.
        for path in children(
            &self.root.join("pending"),
            self.manifest.plan.budget.max_disk_bytes / BLOCK,
        )? {
            let name = path
                .file_name()
                .and_then(|s| s.to_str())
                .ok_or(StoreError::Corrupt)?;
            let meta = fs::symlink_metadata(&path)?;
            if name == "quarantine.json" {
                let marker: Quarantine = decode(&read_bounded(&path, JSON_LIMIT)?)?;
                if marker.schema != "OF1_FIXTURE_QUARANTINE_1"
                    || attempts.get(
                        usize::try_from(marker.reservation.attempt_id)
                            .map_err(|_| StoreError::Corrupt)?,
                    ) != Some(&marker.reservation)
                {
                    return Err(StoreError::Corrupt);
                }
                return Err(quarantine_error(marker.reason));
            } else if name.starts_with("stream-") && meta.is_dir() {
                let id = name[7..]
                    .parse::<usize>()
                    .map_err(|_| StoreError::Corrupt)?;
                let reservation = attempts.get(id).ok_or(StoreError::Corrupt)?;
                if name != format!("stream-{id:010}") {
                    return Err(StoreError::Corrupt);
                }
                let stream = self.read_stream(reservation)?;
                for later in attempts.iter().skip(id + 1) {
                    clock_follows(&stream.latest_at, &later.at)?;
                }
                high_water.wall_ms = high_water.wall_ms.max(stream.latest_at.wall_ms);
                high_water.boot_ms = high_water.boot_ms.max(stream.latest_at.boot_ms);
                streams.insert(id as u64, stream);
            } else if meta.is_dir() {
                let id = name.parse::<usize>().map_err(|_| StoreError::Corrupt)?;
                let reservation = attempts.get(id).ok_or(StoreError::Corrupt)?;
                if name != format!("{id:010}")
                    || published.contains_key(&reservation.request_sequence)
                        && published[&reservation.request_sequence].receipt.attempt_id == id as u64
                {
                    return Err(StoreError::Corrupt);
                }
                for file in children(&path, 2)? {
                    if !["raw.bin", "receipt.json"].contains(
                        &file
                            .file_name()
                            .and_then(|s| s.to_str())
                            .ok_or(StoreError::Corrupt)?,
                    ) {
                        return Err(StoreError::Corrupt);
                    }
                    regular(&file)?;
                }
            } else if meta.is_file()
                && name.starts_with("reserve-")
                && path.extension() == Some(std::ffi::OsStr::new("json"))
            {
                // Cannot distinguish a pre-publication crash from a lost canonical spent
                // record. Refuse an unmatched/torn intent, including the final record.
                if meta.len() > JSON_LIMIT {
                    return Err(StoreError::Corrupt);
                }
                let intent: Reservation = decode(&read_bounded(&path, JSON_LIMIT)?)?;
                if name != format!("reserve-{:010}.json", intent.attempt_id)
                    || attempts
                        .get(usize::try_from(intent.attempt_id).map_err(|_| StoreError::Corrupt)?)
                        != Some(&intent)
                {
                    return Err(StoreError::Corrupt);
                }
            } else {
                return Err(StoreError::Corrupt);
            }
        }
        verify_comparison_labels(published, &streams)?;
        Ok(streams)
    }

    fn read_stream(&self, reservation: &Reservation) -> StoreResult<StreamRecord> {
        let path = self.stream_path(reservation.attempt_id);
        let identity: StreamIdentity = decode(&read_bounded(&path.join("head.json"), JSON_LIMIT)?)?;
        if &identity.reservation != reservation
            || !response_matches(
                &identity.head.response,
                self.request(reservation.request_sequence)?,
                self.manifest.plan.object_size,
            )
            || !valid_etag(identity.head.strong_etag.as_deref())
        {
            return Err(StoreError::Corrupt);
        }
        self.stream_clock(reservation, &reservation.at, &identity.at)?;
        let mut latest_at = identity.at.clone();
        let mut bytes = Vec::new();
        let mut chunks = 0;
        for child in children(&path, self.manifest.plan.budget.max_disk_bytes / BLOCK)? {
            if child.file_name().and_then(|n| n.to_str()) == Some("head.json") {
                continue;
            }
            if child.file_name().and_then(|n| n.to_str())
                != Some(format!("chunk-{chunks:010}").as_str())
            {
                return Err(StoreError::Corrupt);
            }
            exact_names(&child, &["raw.bin", "chunk.json"])?;
            let receipt: ChunkReceipt =
                decode(&read_bounded(&child.join("chunk.json"), JSON_LIMIT)?)?;
            let raw = read_bounded(&child.join("raw.bin"), MAX_STREAM_CHUNK_BYTES as u64)?;
            if receipt.run_id != self.manifest.run_id
                || receipt.attempt_id != reservation.attempt_id
                || receipt.offset != bytes.len() as u64
                || receipt.length != raw.len() as u64
                || raw.is_empty()
                || receipt.sha256 != sha256(&raw)
                || (bytes.len() as u64)
                    .checked_add(receipt.length)
                    .ok_or(StoreError::Corrupt)?
                    > reservation.reserved_entity_bytes
            {
                return Err(StoreError::Corrupt);
            }
            self.stream_clock(reservation, &latest_at, &receipt.at)?;
            latest_at = receipt.at;
            bytes.extend(raw);
            chunks += 1;
        }
        Ok(StreamRecord {
            identity,
            bytes,
            chunks,
            latest_at,
        })
    }

    fn stream_clock(
        &self,
        reservation: &Reservation,
        previous: &ClockSample,
        at: &ClockSample,
    ) -> StoreResult<()> {
        clock_follows(previous, at)?;
        self.within_run(at)?;
        if at.wall_ms - reservation.at.wall_ms >= self.manifest.plan.budget.response_timeout_ms
            || at.boot_ms - reservation.at.boot_ms >= self.manifest.plan.budget.response_timeout_ms
        {
            return Err(StoreError::Corrupt);
        }
        Ok(())
    }

    fn within_run(&self, at: &ClockSample) -> StoreResult<()> {
        clock_follows(&self.manifest.started_at, at)?;
        if at.wall_ms >= self.manifest.deadline_wall_ms
            || at.boot_ms >= self.manifest.deadline_boot_ms
        {
            return Err(StoreError::Corrupt);
        }
        Ok(())
    }

    fn space(&self, additional: u64) -> StoreResult<()> {
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
}

fn verify_comparison_labels(
    published: &BTreeMap<u64, VerifiedObject>,
    streams: &BTreeMap<u64, StreamRecord>,
) -> StoreResult<()> {
    // Recompute comparison labels from immutable history, not caller assertions.
    for object in published.values() {
        if streams.contains_key(&object.receipt.attempt_id) != object.receipt.stream_head.is_some()
        {
            return Err(StoreError::Corrupt);
        }
        if let Some(head) = &object.receipt.stream_head {
            let mut overlap = false;
            for (id, prior) in streams {
                if *id >= object.receipt.attempt_id {
                    continue;
                }
                if prior.identity.head.strong_etag != head.strong_etag
                    || prior.identity.head.response.total != head.response.total
                {
                    return Err(StoreError::Corrupt);
                }
                if prior.identity.reservation.request_sequence == object.receipt.request.sequence {
                    let length = prior.bytes.len().min(object.bytes.len());
                    if prior.bytes[..length] != object.bytes[..length] {
                        return Err(StoreError::Corrupt);
                    }
                    overlap |= length != 0;
                }
            }
            if object.receipt.retry_comparison
                != Some(if overlap {
                    RetryComparison::RetainedOverlapMatched
                } else {
                    RetryComparison::NoPriorBytes
                })
            {
                return Err(StoreError::Corrupt);
            }
        }
    }
    Ok(())
}

fn sum_charged(attempts: &[Reservation]) -> StoreResult<u64> {
    attempts.iter().try_fold(0u64, |sum, a| {
        sum.checked_add(a.reserved_entity_bytes)
            .ok_or(StoreError::Budget)
    })
}
fn quarantine_error(reason: QuarantineReason) -> StoreError {
    match reason {
        QuarantineReason::SourceDrift => StoreError::SourceDrift,
        QuarantineReason::ConflictingBytes => StoreError::ConflictingBytes,
    }
}
fn valid_etag(value: Option<&str>) -> bool {
    value.is_none_or(|tag| {
        (2..=256).contains(&tag.len())
            && tag.starts_with('"')
            && tag.ends_with('"')
            && tag.as_bytes()[1..tag.len() - 1]
                .iter()
                .all(|b| (33..=126).contains(b) && *b != b'"')
    })
}
fn response_matches(r: &Response, request: &ByteRequest, total: u64) -> bool {
    r.status == 206
        && r.start == request.start
        && r.end_exclusive == request.end_exclusive
        && r.total == total
}
fn valid_clock(at: &ClockSample) -> StoreResult<()> {
    if at.boot_id.is_empty()
        || at.boot_id.len() > 128
        || at.boot_id.bytes().any(|b| b.is_ascii_control())
    {
        Err(StoreError::Clock)
    } else {
        Ok(())
    }
}
fn clock_follows(before: &ClockSample, after: &ClockSample) -> StoreResult<()> {
    valid_clock(after)?;
    if before.boot_id != after.boot_id
        || after.wall_ms < before.wall_ms
        || after.boot_ms < before.boot_ms
    {
        Err(StoreError::Clock)
    } else {
        Ok(())
    }
}
fn encode(value: &impl Serialize) -> StoreResult<Vec<u8>> {
    serde_json::to_vec(value).map_err(|_| StoreError::Corrupt)
}
fn decode<T: DeserializeOwned>(bytes: &[u8]) -> StoreResult<T> {
    serde_json::from_slice(bytes).map_err(|_| StoreError::Corrupt)
}
fn identity(meta: &fs::Metadata) -> (u64, u64) {
    (meta.dev(), meta.ino())
}
fn regular(path: &Path) -> StoreResult<fs::Metadata> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() {
        return Err(StoreError::Corrupt);
    }
    Ok(meta)
}
fn read_bounded(path: &Path, max: u64) -> StoreResult<Vec<u8>> {
    let size = regular(path)?.len();
    if size > max {
        return Err(StoreError::Corrupt);
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take(size.checked_add(1).ok_or(StoreError::Budget)?)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != size {
        return Err(StoreError::Corrupt);
    }
    Ok(bytes)
}
fn sync_dir(path: &Path) -> StoreResult<()> {
    File::open(path)?.sync_all().map_err(Into::into)
}
fn children(path: &Path, max: u64) -> StoreResult<Vec<PathBuf>> {
    if !fs::symlink_metadata(path)?.is_dir() {
        return Err(StoreError::Corrupt);
    }
    let mut paths = Vec::new();
    for entry in fs::read_dir(path)? {
        if paths.len() as u64 >= max {
            return Err(StoreError::Corrupt);
        }
        paths.push(entry?.path());
    }
    paths.sort();
    Ok(paths)
}
fn exact_names(path: &Path, names: &[&str]) -> StoreResult<()> {
    let actual = children(path, names.len() as u64)?;
    if actual.len() != names.len()
        || actual
            .iter()
            .any(|p| !names.contains(&p.file_name().and_then(|s| s.to_str()).unwrap_or("")))
    {
        return Err(StoreError::Corrupt);
    }
    Ok(())
}
fn rounded(size: u64) -> StoreResult<u64> {
    size.max(1)
        .div_ceil(BLOCK)
        .checked_mul(BLOCK)
        .ok_or(StoreError::Budget)
}
fn disk_charge(path: &Path) -> StoreResult<u64> {
    let meta = fs::symlink_metadata(path)?;
    if meta.is_file() {
        return Ok(
            rounded(meta.len())?.max(meta.blocks().checked_mul(512).ok_or(StoreError::Budget)?)
        );
    }
    if !meta.is_dir() {
        return Err(StoreError::Corrupt);
    }
    let mut sum = BLOCK.max(meta.blocks().checked_mul(512).ok_or(StoreError::Budget)?);
    for entry in fs::read_dir(path)? {
        sum = sum
            .checked_add(disk_charge(&entry?.path())?)
            .ok_or(StoreError::Budget)?;
    }
    Ok(sum)
}
fn executable_hash() -> StoreResult<String> {
    let mut file = File::open(std::env::current_exe()?)?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0u8; 65_536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(hex::encode(digest.finalize()))
}
