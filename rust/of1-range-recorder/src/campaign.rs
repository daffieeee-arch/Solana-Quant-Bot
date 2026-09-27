//! One bounded B7 campaign ledger, shared by acquisition and offline writers.
//! Immutable snapshots + a durable head; ambiguous crash seams stop, never refund.
use crate::{
    b7,
    durable::{self, ClockSample, StoreError, StoreResult},
    sample::SampleIdentity,
    sha256,
};
use crate::{
    clock_contract::ClockPolicy,
    durable::{
        Clock, SystemClock,
        acquisition::{
            Authority, StageBudget, StageRecord, make_stage, validate_authority, within_stage,
        },
    },
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::Write,
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
};

pub const ATTEMPTS: u64 = 960;
pub const ENTITY_BYTES: u64 = 1_527_045_918;
pub const HARD_ENTITY_BYTES: u64 = 2 * 1024 * 1024 * 1024;
pub const DISK_BYTES: u64 = 32 * 1024 * 1024 * 1024;
pub const FREE_BYTES: u64 = 20 * 1024 * 1024 * 1024;
const RECORD_BYTES: u64 = 1024 * 1024;
const MAX_ENTRIES: u64 = 8192;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Limits {
    attempts: u64,
    entity: u64,
    disk: u64,
    free: u64,
}
impl Limits {
    fn production() -> Self {
        Self {
            attempts: ATTEMPTS,
            entity: ENTITY_BYTES,
            disk: DISK_BYTES,
            free: FREE_BYTES,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Header {
    schema: String,
    campaign: String,
    selection: String,
    report: String,
    fixture: bool,
    root: String,
    device: u64,
    inode: u64,
    limits: Limits,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Run {
    aggregate: String,
    metadata_lease: String,
    payload_lease: Option<String>,
    requests: u64,
    entity: u64,
    attempts: BTreeMap<u64, u64>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct State {
    sequence: u64,
    previous: String,
    requests: u64,
    entity: u64,
    runs: BTreeMap<u64, Run>,
    processing: BTreeMap<u64, Processing>,
    phase2: Option<PhaseApproval>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Head {
    sequence: u64,
    sha256: String,
}

/// Explicit window processing consent; its target also binds the exact workers.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ProcessingApproval {
    pub authority: Authority,
    pub window: u64,
    pub plan_sha256: String,
    pub worker_sha256s: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Processing {
    approval: ProcessingApproval,
    stage: StageRecord,
    complete: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    continuation: Option<Continuation>,
}

/// One additional OFFLINE decision for the retained first-window checkpoint.
/// All fields except authority participate in the explicit approval target.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ContinuationApproval {
    pub authority: Authority,
    pub window: u64,
    pub previous_ledger_sha256: String,
    pub original_processing_sha256: String,
    pub checkpoint_sha256: String,
    pub decision_sha256: String,
    pub plan_sha256: String,
    pub worker_sha256s: Vec<String>,
    pub remaining_slots: Vec<u64>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Continuation {
    approval: ContinuationApproval,
    stage: StageRecord,
}
impl Processing {
    fn active_stage(&self) -> &StageRecord {
        self.continuation.as_ref().map_or(&self.stage, |c| &c.stage)
    }
}
/// Separate phase-two decision, after a named phase-one integrity/resource review.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PhaseApproval {
    pub authority: Authority,
    pub phase_one_evidence_sha256: String,
    pub phase_one_ledger_sha256: String,
}

fn fail() -> StoreError {
    StoreError::Corrupt
}
fn bytes<T: Serialize>(v: &T) -> StoreResult<Vec<u8>> {
    serde_json::to_vec(v).map_err(|_| fail())
}
fn read<T: serde::de::DeserializeOwned>(p: &Path) -> StoreResult<T> {
    serde_json::from_slice(&durable::read_bounded(p, RECORD_BYTES)?).map_err(|_| fail())
}
fn write(p: &Path, b: &[u8]) -> StoreResult<()> {
    let mut f = OpenOptions::new().write(true).create_new(true).open(p)?;
    f.write_all(b)?;
    f.sync_all()?;
    Ok(())
}
fn anchor(root: &Path) -> StoreResult<PathBuf> {
    Ok(root.parent().ok_or(StoreError::Identity)?.join(format!(
        "{}.anchor.json",
        root.file_name()
            .and_then(|s| s.to_str())
            .ok_or(StoreError::Identity)?
    )))
}
fn canonical_new(p: &Path) -> StoreResult<PathBuf> {
    Ok(p.parent()
        .ok_or(StoreError::Identity)?
        .canonicalize()?
        .join(p.file_name().ok_or(StoreError::Identity)?))
}

/// Read-only admission before native campaign creation. Only the first exact
/// run may have two missing ancestors; ordinary location checks are unchanged.
/// # Errors
/// Rejects changed sample/root authority, aliases, Git locations and incomplete
/// parents. This does not validate a lease or repair existing campaign state.
pub fn validate_acquisition_location(
    sample: &SampleIdentity,
    run_root: &Path,
    fixture: bool,
) -> StoreResult<()> {
    sample.validate(978)?;
    let binding = sample.b7.as_ref().ok_or(StoreError::Identity)?;
    let root = Path::new(&binding.campaign_root);
    if crate::dataset_location::validate_dataset_location(root)? != root
        || fixture == (root == Path::new(b7::PRODUCTION_ROOT))
        || run_root != root.join(format!("runs/w{:02}", binding.window_ordinal))
    {
        return Err(StoreError::Identity);
    }
    // symlink_metadata also prevents treating a dangling alias as an unused root.
    match fs::symlink_metadata(root) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound && binding.window_ordinal == 0 => Ok(()),
        Err(e) => Err(e.into()),
        Ok(_) => {
            if crate::dataset_location::validate_dataset_location(run_root)? != run_root {
                return Err(StoreError::Identity);
            }
            Ok(())
        }
    }
}

/// Held for the complete writer operation. Other windows/processes fail closed.
pub struct Guard {
    root: PathBuf,
    lock: File,
    header: Header,
    state: State,
    head_hash: String,
    poisoned: bool,
}
impl Drop for Guard {
    fn drop(&mut self) {
        // Closing this File alone releases flock only after the last duplicate
        // closes. A concurrent subprocess spawn can briefly inherit it before
        // CLOEXEC runs. End the lock at this guard's actual ownership boundary;
        // failure still falls back to File's close, never retries acquisition.
        let _ = self.lock.unlock();
    }
}
impl Guard {
    fn new(root: &Path, fixture: bool, limits: Limits) -> StoreResult<Self> {
        let root = canonical_new(root)?;
        if fixture == (root == Path::new(b7::PRODUCTION_ROOT)) {
            return Err(StoreError::Identity);
        }
        // External create-once anchor makes a missing campaign directory a stop,
        // rather than permission to initialize a second budget for this campaign.
        let a = anchor(&root)?;
        write(&a, b"B7_INITIALIZATION_IN_PROGRESS_FAIL_CLOSED")?;
        durable::sync_dir(a.parent().ok_or(StoreError::Identity)?)?;
        fs::create_dir(&root)?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(root.join("writer.lock"))?;
        lock.try_lock().map_err(|_| StoreError::Locked)?;
        let m = fs::symlink_metadata(&root)?;
        let header = Header {
            schema: "OF1_B7_CAMPAIGN_1".into(),
            campaign: b7::CAMPAIGN_ID.into(),
            selection: b7::SELECTION_SHA256.into(),
            report: b7::REPORT_SHA256.into(),
            fixture,
            root: root.to_string_lossy().into_owned(),
            device: m.dev(),
            inode: m.ino(),
            limits,
        };
        for n in ["journal", "runs", "work"] {
            fs::create_dir(root.join(n))?;
        }
        write(&root.join("campaign.json"), &bytes(&header)?)?;
        let state = State::default();
        let b = bytes(&state)?;
        let h = sha256(&b);
        write(&root.join("journal/0000000000.json"), &b)?;
        write(
            &root.join("head.json"),
            &bytes(&Head {
                sequence: 0,
                sha256: h.clone(),
            })?,
        )?;
        durable::sync_dir(&root.join("journal"))?;
        durable::sync_dir(&root)?;
        // Initialization failure leaves the original anchor and cannot reset it.
        let finish = root.join("anchor-ready.json");
        write(&finish, &bytes(&header)?)?;
        fs::rename(&finish, &a)?;
        durable::sync_dir(a.parent().ok_or(StoreError::Identity)?)?;
        let guard = Self {
            root,
            lock,
            header,
            state,
            head_hash: h,
            poisoned: false,
        };
        guard.space(0)?;
        Ok(guard)
    }
    fn open(root: &Path, fixture: bool) -> StoreResult<Self> {
        let original = root;
        let root = root.canonicalize()?;
        if root != original {
            return Err(StoreError::Identity);
        }
        let meta = fs::symlink_metadata(&root)?;
        let header: Header = read(&root.join("campaign.json"))?;
        if header != read::<Header>(&anchor(&root)?)?
            || header.root != root.to_string_lossy()
            || header.fixture != fixture
            || (fixture && root == Path::new(b7::PRODUCTION_ROOT))
            || header.schema != "OF1_B7_CAMPAIGN_1"
            || header.campaign != b7::CAMPAIGN_ID
            || header.selection != b7::SELECTION_SHA256
            || header.report != b7::REPORT_SHA256
            || (header.device, header.inode) != (meta.dev(), meta.ino())
            || (!fixture
                && (root != Path::new(b7::PRODUCTION_ROOT)
                    || header.limits != Limits::production()))
        {
            return Err(StoreError::Identity);
        }
        durable::regular(&root.join("writer.lock"))?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .open(root.join("writer.lock"))?;
        if lock.metadata()?.nlink() != 1 {
            return Err(StoreError::Locked);
        }
        lock.try_lock().map_err(|_| StoreError::Locked)?;
        let head: Head = read(&root.join("head.json"))?;
        if head.sequence >= MAX_ENTRIES || root.join("head-next.json").exists() {
            return Err(fail());
        }
        let mut previous = String::new();
        let mut state = State::default();
        let files = durable::children(&root.join("journal"), MAX_ENTRIES)?;
        if files.len() as u64 != head.sequence + 1 {
            return Err(fail());
        }
        for sequence in 0..=head.sequence {
            let b = durable::read_bounded(
                &root.join(format!("journal/{sequence:010}.json")),
                RECORD_BYTES,
            )?;
            let next: State = serde_json::from_slice(&b).map_err(|_| fail())?;
            if next.sequence != sequence
                || next.previous != previous
                || next.requests < state.requests
                || next.entity < state.entity
                || next.runs.len() < state.runs.len()
            {
                return Err(fail());
            }
            for (i, old) in &state.processing {
                let new = next.processing.get(i).ok_or_else(fail)?;
                if old.approval != new.approval
                    || old.stage != new.stage
                    || (old.complete && !new.complete)
                    || old
                        .continuation
                        .as_ref()
                        .is_some_and(|c| new.continuation.as_ref() != Some(c))
                {
                    return Err(fail());
                }
            }
            previous = sha256(&b);
            state = next;
        }
        if previous != head.sha256 {
            return Err(fail());
        }
        let guard = Self {
            root,
            lock,
            header,
            state,
            head_hash: previous,
            poisoned: false,
        };
        guard.audit()?;
        guard.space(0)?;
        Ok(guard)
    }
    fn audit(&self) -> StoreResult<()> {
        if self.poisoned
            || self.state.runs.len() > 16
            || self.state.requests > self.header.limits.attempts
            || self.state.entity > self.header.limits.entity
            || self.state.entity > HARD_ENTITY_BYTES
        {
            return Err(StoreError::Budget);
        }
        let mut requests = 0_u64;
        let mut entity = 0_u64;
        for (&i, r) in &self.state.runs {
            if i >= 16
                || r.requests != r.attempts.len() as u64
                || r.entity
                    != r.attempts
                        .values()
                        .try_fold(0_u64, |n, v| n.checked_add(*v))
                        .ok_or(StoreError::Corrupt)?
                || r.requests > 60
                || r.entity
                    > 15_576_576
                        + b7::PAYLOAD_BYTES[usize::try_from(i).map_err(|_| StoreError::Identity)?]
                            * 3
            {
                return Err(fail());
            }
            requests = requests
                .checked_add(r.requests)
                .ok_or(StoreError::Corrupt)?;
            entity = entity.checked_add(r.entity).ok_or(StoreError::Corrupt)?;
        }
        if self
            .state
            .processing
            .keys()
            .any(|i| !self.state.runs.contains_key(i))
            || requests != self.state.requests
            || entity != self.state.entity
        {
            return Err(fail());
        }
        Ok(())
    }
    fn commit(&mut self, mut next: State) -> StoreResult<()> {
        if self.poisoned {
            return Err(StoreError::Poisoned);
        }
        next.sequence = self
            .state
            .sequence
            .checked_add(1)
            .ok_or(StoreError::Budget)?;
        next.previous.clone_from(&self.head_hash);
        if next.sequence >= MAX_ENTRIES {
            return Err(StoreError::Budget);
        }
        let b = bytes(&next)?;
        if b.len() as u64 > RECORD_BYTES {
            return Err(StoreError::Budget);
        }
        self.space((b.len() as u64 + 8192) * 2)?;
        self.poisoned = true;
        write(
            &self
                .root
                .join(format!("journal/{:010}.json", next.sequence)),
            &b,
        )?;
        durable::sync_dir(&self.root.join("journal"))?;
        #[cfg(test)]
        crash_point("JOURNAL_SYNCED");
        let hash = sha256(&b);
        write(
            &self.root.join("head-next.json"),
            &bytes(&Head {
                sequence: next.sequence,
                sha256: hash.clone(),
            })?,
        )?;
        #[cfg(test)]
        crash_point("HEAD_PENDING");
        fs::rename(
            self.root.join("head-next.json"),
            self.root.join("head.json"),
        )?;
        durable::sync_dir(&self.root)?;
        #[cfg(test)]
        crash_point("HEAD_PUBLISHED");
        self.state = next;
        self.head_hash = hash;
        self.poisoned = false;
        self.audit()
    }
    /// # Errors
    /// Checks the entire fixed campaign tree plus the next conservative write;
    /// retained failed/intermediate outputs and allocation overhead stay counted.
    pub fn space(&self, additional: u64) -> StoreResult<()> {
        let a = fs::symlink_metadata(self.root.join("writer.lock"))?;
        let b = self.lock.metadata()?;
        if (a.dev(), a.ino()) != (b.dev(), b.ino()) {
            return Err(StoreError::Locked);
        }
        let mut disk = durable::disk_charge(&self.root)?
            .checked_add(4096)
            .ok_or(StoreError::Budget)?;
        for (&window, processing) in &self.state.processing {
            let path = self.root.join(format!("work/w{window:02}"));
            let used = if path.exists() {
                durable::disk_charge(&path)?
            } else {
                0
            };
            if used > 4 * 1024 * 1024 * 1024 {
                return Err(StoreError::Budget);
            }
            if !processing.complete {
                disk = disk
                    .checked_add(4 * 1024 * 1024 * 1024 - used)
                    .ok_or(StoreError::Budget)?;
            }
        }
        if !self.header.fixture {
            resource_envelope()?;
        }
        let (free, rss, _) = durable::acquisition::resource_sample(&self.root)?;
        if disk
            .checked_add(additional)
            .is_none_or(|n| n > self.header.limits.disk)
            || free < self.header.limits.free.saturating_add(additional)
            || rss > 2 * 1024 * 1024 * 1024
        {
            return Err(StoreError::Budget);
        }
        Ok(())
    }
    /// # Errors
    /// Read-only accounting, never renewal or repair. No analytical outcomes.
    pub fn status(root: &Path) -> StoreResult<serde_json::Value> {
        let header: Header = read(&root.join("campaign.json"))?;
        Self::open(root, header.fixture)?.accounting()
    }
    /// # Errors
    /// Caller retains the writer guard; accounting never releases it for a report.
    pub fn accounting(&self) -> StoreResult<serde_json::Value> {
        self.audit()?;
        Ok(
            serde_json::json!({"schema":"OF1_B7_CAMPAIGN_ACCOUNTING_1","campaign_id":self.header.campaign,
            "selection_sha256":self.header.selection,"accepted_report_sha256":self.header.report,
            "fixture":self.header.fixture,"limits":self.header.limits,"registered_windows":self.state.runs.len(),
            "selected_slots_registered":self.state.runs.len()*16,"attempts_reserved":self.state.requests,
            "entity_bytes_reserved":self.state.entity,"actual_received_unreceipted_bytes":null,
            "disk_charge_bytes":durable::disk_charge(&self.root)?+4096,"ledger_sha256":self.head_hash,
            "processing_windows":self.state.processing.keys().collect::<Vec<_>>(),"read_only":true,"research_ready":false}),
        )
    }
    /// Called only after native stage authority validation, before run mutation.
    /// # Errors
    /// Missing/corrupt journals, duplicate/wrong paths and phase-two without its
    /// separate approval fail closed. Fixture authority can never open real state.
    pub fn acquisition(
        sample: &SampleIdentity,
        run_root: &Path,
        aggregate: &str,
        lease: &str,
        fixture: bool,
        create: bool,
    ) -> StoreResult<Self> {
        validate_acquisition_location(sample, run_root, fixture)?;
        let binding = sample.b7.as_ref().ok_or(StoreError::Identity)?;
        let root = Path::new(&binding.campaign_root);
        let mut g = if create && binding.window_ordinal == 0 && !root.exists() {
            Self::new(
                root,
                fixture,
                Limits {
                    free: if fixture { 0 } else { FREE_BYTES },
                    ..Limits::production()
                },
            )?
        } else {
            Self::open(root, fixture)?
        };
        let expected = g.root.join(format!("runs/w{:02}", binding.window_ordinal));
        if canonical_new(run_root)? != expected {
            return Err(StoreError::Identity);
        }
        if create {
            if g.state.processing.values().any(|p| !p.complete)
                || g.state.runs.contains_key(&binding.window_ordinal)
                || g.state.runs.len() as u64 != binding.window_ordinal
            {
                return Err(StoreError::Identity);
            }
            // A phase-two window cannot use an ordinary metadata GO as implicit expansion authority.
            if binding.phase == 2 && g.state.phase2.is_none() {
                return Err(StoreError::Identity);
            }
            let mut next = g.state.clone();
            next.runs.insert(
                binding.window_ordinal,
                Run {
                    aggregate: aggregate.into(),
                    metadata_lease: lease.into(),
                    payload_lease: None,
                    requests: 0,
                    entity: 0,
                    attempts: BTreeMap::new(),
                },
            );
            g.commit(next)?;
        } else {
            let r = g
                .state
                .runs
                .get(&binding.window_ordinal)
                .ok_or(StoreError::Corrupt)?;
            if r.aggregate != aggregate
                || (r.metadata_lease != lease && r.payload_lease.as_deref() != Some(lease))
            {
                return Err(StoreError::Identity);
            }
        }
        Ok(g)
    }
    /// # Errors
    /// The separately verified native payload lease is charged to the same run.
    pub fn admit_payload(&mut self, window: u64, lease: &str) -> StoreResult<()> {
        let mut n = self.state.clone();
        let r = n.runs.get_mut(&window).ok_or(StoreError::Identity)?;
        if r.payload_lease.is_some() {
            return Err(StoreError::Identity);
        }
        r.payload_lease = Some(lease.into());
        self.commit(n)
    }
    /// # Errors
    /// Permanently charge before the run's own reservation and before any network.
    /// A failed/unfinished attempt has exactly the same charge as a successful one.
    pub fn reserve(&mut self, window: u64, attempt: u64, allowance: u64) -> StoreResult<()> {
        let mut n = self.state.clone();
        let r = n.runs.get_mut(&window).ok_or(StoreError::Identity)?;
        if attempt != r.requests || r.attempts.contains_key(&attempt) {
            return Err(StoreError::Corrupt);
        }
        r.attempts.insert(attempt, allowance);
        r.requests = r.requests.checked_add(1).ok_or(StoreError::Budget)?;
        r.entity = r.entity.checked_add(allowance).ok_or(StoreError::Budget)?;
        n.requests = n.requests.checked_add(1).ok_or(StoreError::Budget)?;
        n.entity = n.entity.checked_add(allowance).ok_or(StoreError::Budget)?;
        if n.requests > self.header.limits.attempts
            || n.entity > self.header.limits.entity
            || r.requests > 60
            || r.entity
                > 15_576_576
                    + b7::PAYLOAD_BYTES
                        [usize::try_from(window).map_err(|_| StoreError::Identity)?]
                        * 3
        {
            return Err(StoreError::Budget);
        }
        self.commit(n)
    }
    /// # Errors
    /// Reconcile the native run's precharges without deleting unmatched campaign charges.
    pub fn verify_charges(&self, window: u64, charges: &[(u64, u64)]) -> StoreResult<()> {
        let r = self.state.runs.get(&window).ok_or(StoreError::Identity)?;
        if r.attempts.len() != charges.len()
            || charges.iter().any(|(a, b)| r.attempts.get(a) != Some(b))
        {
            return Err(StoreError::Corrupt);
        }
        Ok(())
    }
    /// Hash-only proposal, not consent or initialization.
    /// # Errors
    /// Invalid window/worker identity cannot produce an approval target.
    pub fn processing_target(
        sample: &SampleIdentity,
        plan_sha256: &str,
        workers: &[String],
    ) -> StoreResult<String> {
        sample.validate(978)?;
        if sample.b7.is_none()
            || !hex_hash(plan_sha256)
            || workers.len() != 3
            || workers.iter().any(|v| !hex_hash(v))
        {
            return Err(StoreError::Identity);
        }
        Ok(sha256(&bytes(&(
            "OF1_B7_PROCESSING_APPROVAL_1",
            sample,
            plan_sha256,
            workers,
            900_000_u64,
            4_u64 * 1024 * 1024 * 1024,
        ))?))
    }
    /// # Errors
    /// Processing is separately admitted for one original window, plan and workers.
    pub fn admit_processing(
        sample: &SampleIdentity,
        run_root: &Path,
        approval: ProcessingApproval,
        at: &ClockSample,
    ) -> StoreResult<()> {
        development_processing_only(sample)?;
        let mut g = Self::for_recorded(sample, run_root)?;
        let binding = sample.b7.as_ref().ok_or(StoreError::Identity)?;
        if approval.window != binding.window_ordinal
            || g.state.processing.contains_key(&approval.window)
            || matches!(approval.authority, Authority::Fixture) != g.header.fixture
        {
            return Err(StoreError::Identity);
        }
        let target =
            Self::processing_target(sample, &approval.plan_sha256, &approval.worker_sha256s)?;
        let policy = ClockPolicy::standard();
        validate_authority(Some(&policy), &approval.authority, &target)?;
        let stage = make_stage(
            Some(&policy),
            &approval.authority,
            &StageBudget {
                max_requests: 1,
                max_response_entity_bytes_total: 1,
                max_runtime_ms: 900_000,
            },
            &approval,
            at,
        )?;
        g.space(4 * 1024 * 1024 * 1024)?;
        let mut n = g.state.clone();
        n.processing.insert(
            approval.window,
            Processing {
                approval,
                stage,
                complete: false,
                continuation: None,
            },
        );
        g.commit(n)?;
        let output = g.root.join(format!("work/w{:02}", binding.window_ordinal));
        fs::create_dir(&output)?;
        write(&output.join("driver.lock"), b"")?;
        durable::sync_dir(&output)?;
        durable::sync_dir(output.parent().ok_or(StoreError::Identity)?)?;
        Ok(())
    }
    fn for_recorded(sample: &SampleIdentity, run_root: &Path) -> StoreResult<Self> {
        sample.validate(978)?;
        let b = sample.b7.as_ref().ok_or(StoreError::Identity)?;
        let run = crate::monitor::read_run_context(run_root)?;
        if run.aggregate_plan.sample_identity.as_ref() != Some(sample) || run.prepared.is_none() {
            return Err(StoreError::Identity);
        }
        let fixture = run
            .published
            .first()
            .ok_or(StoreError::Corrupt)?
            .receipt
            .evidence
            == "Fixture";
        let aggregate = sha256(&bytes(&run.aggregate_plan)?);
        let g = Self::open(Path::new(&b.campaign_root), fixture)?;
        if run_root.canonicalize()? != g.root.join(format!("runs/w{:02}", b.window_ordinal))
            || g.state
                .runs
                .get(&b.window_ordinal)
                .is_none_or(|r| r.aggregate != aggregate || r.payload_lease.is_none())
        {
            return Err(StoreError::Identity);
        }
        Ok(g)
    }

    /// Read-only continuation preflight; caller retains the writer lock while
    /// verifying the original checkpoint and source before admitting a decision.
    /// # Errors
    /// Missing/corrupt state, other windows or existing continuation fail closed.
    pub fn continuation_context(
        sample: &SampleIdentity,
        run_root: &Path,
    ) -> StoreResult<(Self, serde_json::Value)> {
        development_processing_only(sample)?;
        let g = Self::for_recorded(sample, run_root)?;
        let i = sample
            .b7
            .as_ref()
            .ok_or(StoreError::Identity)?
            .window_ordinal;
        let p = g.state.processing.get(&i).ok_or(StoreError::Identity)?;
        if i != 0 || p.complete || p.continuation.is_some() {
            return Err(StoreError::Identity);
        }
        let context = serde_json::json!({"ledger_sha256":g.head_hash,
            "original_processing_sha256":sha256(&bytes(&(&p.approval,&p.stage))?),
            "approval":p.approval,"stage":p.stage});
        Ok((g, context))
    }

    /// # Errors
    /// Only the explicitly bounded remaining four slots and fixed part profile.
    pub fn continuation_target(
        sample: &SampleIdentity,
        a: &ContinuationApproval,
    ) -> StoreResult<String> {
        development_processing_only(sample)?;
        if sample
            .b7
            .as_ref()
            .ok_or(StoreError::Identity)?
            .window_ordinal
            != 0
            || a.window != 0
            || a.remaining_slots != (422_526_156..422_526_160).collect::<Vec<_>>()
            || [
                &a.previous_ledger_sha256,
                &a.original_processing_sha256,
                &a.checkpoint_sha256,
                &a.decision_sha256,
                &a.plan_sha256,
            ]
            .iter()
            .any(|h| !hex_hash(h))
            || a.worker_sha256s.len() != 3
            || a.worker_sha256s.iter().any(|h| !hex_hash(h))
        {
            return Err(StoreError::Identity);
        }
        Ok(sha256(&bytes(&(
            "OF1_B7_CONTINUATION_APPROVAL_1",
            sample,
            a.window,
            &a.previous_ledger_sha256,
            &a.original_processing_sha256,
            &a.checkpoint_sha256,
            &a.decision_sha256,
            &a.plan_sha256,
            &a.worker_sha256s,
            &a.remaining_slots,
            "OF1_ATOMIC_SLOT_PARTS_128_V1",
            900_000_u64,
            4_u64 * 1024 * 1024 * 1024,
        ))?))
    }

    /// Append one additional processing stage. No old lease, charge or
    /// reservation is replaced. The caller has verified the bound checkpoint.
    /// # Errors
    /// Duplicate/stale decisions, authority drift and ambiguous journal seams stop.
    pub fn admit_continuation(
        &mut self,
        sample: &SampleIdentity,
        a: ContinuationApproval,
        at: &ClockSample,
    ) -> StoreResult<()> {
        let target = Self::continuation_target(sample, &a)?;
        let p = self
            .state
            .processing
            .get(&a.window)
            .ok_or(StoreError::Identity)?;
        if p.complete
            || p.continuation.is_some()
            || self.head_hash != a.previous_ledger_sha256
            || sha256(&bytes(&(&p.approval, &p.stage))?) != a.original_processing_sha256
            || matches!(a.authority, Authority::Fixture) != self.header.fixture
        {
            return Err(StoreError::Identity);
        }
        let policy = ClockPolicy::standard();
        validate_authority(Some(&policy), &a.authority, &target)?;
        let stage = make_stage(
            Some(&policy),
            &a.authority,
            &StageBudget {
                max_requests: 1,
                max_response_entity_bytes_total: 1,
                max_runtime_ms: 900_000,
            },
            &a,
            at,
        )?;
        self.space(0)?;
        let window = a.window;
        let used = durable::disk_charge(&self.root.join("work/w00"))?;
        if used
            .checked_add(4 * 1024 * 1024)
            .is_none_or(|n| n > 4 * 1024 * 1024 * 1024)
            || durable::acquisition::resource_sample(&self.root)?.0
                < self.header.limits.free.saturating_add(4 * 1024 * 1024)
        {
            return Err(StoreError::Budget);
        }
        let mut next = self.state.clone();
        next.processing
            .get_mut(&window)
            .ok_or(StoreError::Identity)?
            .continuation = Some(Continuation { approval: a, stage });
        self.commit(next)
    }

    /// # Errors
    /// A new producer may decode only the four slots named in its decision.
    pub fn check_processing_slots(
        &self,
        sample: &SampleIdentity,
        slots: &[u64],
    ) -> StoreResult<()> {
        let p = self
            .state
            .processing
            .get(
                &sample
                    .b7
                    .as_ref()
                    .ok_or(StoreError::Identity)?
                    .window_ordinal,
            )
            .ok_or(StoreError::Identity)?;
        if let Some(c) = &p.continuation
            && (slots.is_empty()
                || slots
                    .iter()
                    .collect::<std::collections::BTreeSet<_>>()
                    .len()
                    != slots.len()
                || slots
                    .iter()
                    .any(|s| !c.approval.remaining_slots.contains(s)))
        {
            return Err(StoreError::Identity);
        }
        Ok(())
    }

    /// # Errors
    /// Read back the admitted continuation, never renew it.
    pub fn continuation_decision(
        &self,
        sample: &SampleIdentity,
    ) -> StoreResult<ContinuationApproval> {
        self.state
            .processing
            .get(
                &sample
                    .b7
                    .as_ref()
                    .ok_or(StoreError::Identity)?
                    .window_ordinal,
            )
            .and_then(|p| p.continuation.as_ref())
            .map(|c| c.approval.clone())
            .ok_or(StoreError::Identity)
    }
    /// # Errors
    /// Only registered native output, under the campaign reservation and deadline.
    /// Fixture outputs remain Fixture; no historical caller label creates a sample.
    pub fn output(
        sample: &SampleIdentity,
        run_root: &Path,
        path: &Path,
        plan_sha: &str,
        additional: u64,
    ) -> StoreResult<Self> {
        development_processing_only(sample)?;
        let g = Self::for_recorded(sample, run_root)?;
        if !g.header.fixture {
            verify_worker_address_space()?;
        }
        let b = sample.b7.as_ref().ok_or(StoreError::Identity)?;
        let p = g
            .state
            .processing
            .get(&b.window_ordinal)
            .ok_or(StoreError::Identity)?;
        let (active_plan, active_workers) = p
            .continuation
            .as_ref()
            .map_or((&p.approval.plan_sha256, &p.approval.worker_sha256s), |c| {
                (&c.approval.plan_sha256, &c.approval.worker_sha256s)
            });
        if p.complete
            || active_plan != plan_sha
            || !active_workers.contains(&durable::acquisition::current_executable_sha256()?)
        {
            return Err(StoreError::Identity);
        }
        within_stage(p.active_stage(), &SystemClock.sample()?)?;
        let allowed = g.root.join(format!("work/w{:02}", b.window_ordinal));
        let path = if path.exists() {
            path.canonicalize()?
        } else {
            canonical_new(path)?
        };
        if !path.starts_with(&allowed) && path != allowed {
            return Err(StoreError::Identity);
        }
        if p.continuation.is_some() && !path.starts_with(allowed.join("continuation-1")) {
            return Err(StoreError::Identity);
        }
        let used = if allowed.exists() {
            durable::disk_charge(&allowed)?
        } else {
            0
        };
        if used
            .checked_add(additional)
            .is_none_or(|n| n > 4 * 1024 * 1024 * 1024)
        {
            return Err(StoreError::Budget);
        }
        // The full work reservation already counts globally; free space must still
        // cover this actual write and the production floor.
        g.space(0)?;
        if durable::acquisition::resource_sample(&g.root)?.0
            < g.header.limits.free.saturating_add(additional)
        {
            return Err(StoreError::Budget);
        }
        Ok(g)
    }
    /// # Errors
    /// Original absolute boot-clock deadline, never renewed by a worker start.
    pub fn processing_deadline_boot_ms(&self, sample: &SampleIdentity) -> StoreResult<u64> {
        let i = sample
            .b7
            .as_ref()
            .ok_or(StoreError::Identity)?
            .window_ordinal;
        Ok(self
            .state
            .processing
            .get(&i)
            .ok_or(StoreError::Identity)?
            .active_stage()
            .deadline_boot_ms)
    }
    /// # Errors
    /// Remaining fixed runtime; a restart never creates a new deadline.
    pub fn processing_remaining_ms(&self, sample: &SampleIdentity) -> StoreResult<u64> {
        let i = sample
            .b7
            .as_ref()
            .ok_or(StoreError::Identity)?
            .window_ordinal;
        let p = self.state.processing.get(&i).ok_or(StoreError::Identity)?;
        let now = SystemClock.sample()?;
        within_stage(p.active_stage(), &now)?;
        Ok(p.active_stage()
            .deadline_boot_ms
            .saturating_sub(now.boot_ms))
    }
    /// # Errors
    /// Existing processing deadline and space remain binding at publication.
    pub fn processing_tick(&self, sample: &SampleIdentity) -> StoreResult<()> {
        let i = sample
            .b7
            .as_ref()
            .ok_or(StoreError::Identity)?
            .window_ordinal;
        let p = self.state.processing.get(&i).ok_or(StoreError::Identity)?;
        if p.complete {
            return Err(StoreError::Identity);
        }
        within_stage(p.active_stage(), &SystemClock.sample()?)?;
        self.space(0)
    }
    /// # Errors
    /// Finish only after the caller verified the full native collection manifest.
    pub fn complete_processing(&mut self, window: u64) -> StoreResult<()> {
        let mut n = self.state.clone();
        let p = n.processing.get_mut(&window).ok_or(StoreError::Identity)?;
        if p.complete {
            return Err(StoreError::Identity);
        }
        within_stage(p.active_stage(), &SystemClock.sample()?)?;
        p.complete = true;
        self.commit(n)
    }
    /// # Errors
    /// Phase two requires a distinct receipt bound to phase-one ledger and evidence.
    pub fn admit_phase2(
        sample: &SampleIdentity,
        approval: PhaseApproval,
        at: &ClockSample,
    ) -> StoreResult<()> {
        sample.validate(978)?;
        let b = sample.b7.as_ref().ok_or(StoreError::Identity)?;
        let header: Header = read(&Path::new(&b.campaign_root).join("campaign.json"))?;
        let mut g = Self::open(Path::new(&b.campaign_root), header.fixture)?;
        if g.state.phase2.is_some()
            || g.head_hash != approval.phase_one_ledger_sha256
            || !hex_hash(&approval.phase_one_evidence_sha256)
            || (0..8).any(|i| g.state.processing.get(&i).is_none_or(|p| !p.complete))
            || matches!(approval.authority, Authority::Fixture) != g.header.fixture
        {
            return Err(StoreError::Identity);
        }
        let target = sha256(&bytes(&(
            "OF1_B7_PHASE2_APPROVAL_1",
            b7::REPORT_SHA256,
            b7::SELECTION_SHA256,
            &approval.phase_one_ledger_sha256,
            &approval.phase_one_evidence_sha256,
        ))?);
        let policy = ClockPolicy::standard();
        validate_authority(Some(&policy), &approval.authority, &target)?;
        make_stage(
            Some(&policy),
            &approval.authority,
            &StageBudget {
                max_requests: 1,
                max_response_entity_bytes_total: 1,
                max_runtime_ms: 600_000,
            },
            &approval,
            at,
        )?;
        let mut n = g.state.clone();
        n.phase2 = Some(approval);
        g.commit(n)
    }
}

/// No evaluation processing or analytical presentation is authorized in this increment.
/// Identity validation/acquisition retain the original role without relabeling.
/// # Errors
/// Reserved evaluation waits for a separately reviewed visibility/processing gate.
pub fn development_processing_only(sample: &SampleIdentity) -> StoreResult<()> {
    sample.validate(978)?;
    if sample.b7.as_ref().ok_or(StoreError::Identity)?.cohort_role != "DEVELOPMENT" {
        return Err(StoreError::ReservedEvaluation);
    }
    Ok(())
}

fn address_space_limit(text: &str) -> StoreResult<()> {
    let fields = text
        .lines()
        .find_map(|line| line.strip_prefix("Max address space"))
        .ok_or(StoreError::WorkerLimit)?
        .split_whitespace()
        .collect::<Vec<_>>();
    if fields.len() != 3 || fields[2] != "bytes" {
        return Err(StoreError::WorkerLimit);
    }
    let soft = fields[0]
        .parse::<u64>()
        .map_err(|_| StoreError::WorkerLimit)?;
    let hard = fields[1]
        .parse::<u64>()
        .map_err(|_| StoreError::WorkerLimit)?;
    if soft == 0 || soft > hard || hard > 2 * 1024 * 1024 * 1024 {
        return Err(StoreError::WorkerLimit);
    }
    Ok(())
}
/// # Errors
/// Native workers require the existing hard `RLIMIT_AS` cap, including direct use.
/// This observes the current process; it never changes shared or process settings.
pub fn verify_worker_address_space() -> StoreResult<()> {
    address_space_limit(&fs::read_to_string("/proc/self/limits")?)
}

fn hex_hash(v: &str) -> bool {
    v.len() == 64
        && v.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn resource_envelope() -> StoreResult<()> {
    let cgroup = fs::read_to_string("/proc/self/cgroup")?;
    let relative = cgroup
        .lines()
        .find_map(|s| s.strip_prefix("0::/"))
        .ok_or(StoreError::Budget)?;
    if Path::new(relative)
        .components()
        .any(|v| matches!(v, std::path::Component::ParentDir))
    {
        return Err(StoreError::Budget);
    }
    let base = Path::new("/sys/fs/cgroup").join(relative);
    // Explicit approved wrapper must provide the effective limits on this scope.
    for (name, limit) in [
        ("memory.high", 5_u64 * 1024 * 1024 * 1024),
        ("memory.max", 6 * 1024 * 1024 * 1024),
        ("pids.max", 256),
    ] {
        let value = fs::read_to_string(base.join(name))?
            .trim()
            .parse::<u64>()
            .map_err(|_| StoreError::Budget)?;
        if value == 0 || value > limit {
            return Err(StoreError::Budget);
        }
    }
    let cpu = fs::read_to_string(base.join("cpu.max"))?;
    let values = cpu
        .split_whitespace()
        .map(str::parse::<u64>)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| StoreError::Budget)?;
    if values.len() != 2
        || values[1] == 0
        || values[0] == 0
        || values[0] > values[1].checked_mul(2).ok_or(StoreError::Budget)?
    {
        return Err(StoreError::Budget);
    }
    Ok(())
}

#[cfg(test)]
fn crash_point(point: &str) {
    if std::env::var("OF1_B7_TEST_CRASH_POINT").ok().as_deref() == Some(point) {
        std::process::exit(74)
    }
}

#[cfg(test)]
mod process_tests;

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(attempts: u64, entity: u64) -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("campaign");
        drop(
            Guard::new(
                &root,
                true,
                Limits {
                    attempts,
                    entity,
                    disk: 16 * 1024 * 1024,
                    free: 0,
                },
            )
            .unwrap(),
        );
        (dir, root)
    }
    fn run(root: &Path, i: usize) -> Guard {
        let sample = b7::sample(i, root).unwrap();
        Guard::acquisition(
            &sample,
            &root.join(format!("runs/w{i:02}")),
            &"a".repeat(64),
            &"b".repeat(64),
            true,
            true,
        )
        .unwrap()
    }
    pub(super) fn continuation_fixture() -> (
        tempfile::TempDir,
        Guard,
        SampleIdentity,
        ContinuationApproval,
    ) {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("campaign");
        let g = Guard::new(
            &root,
            true,
            Limits {
                free: 0,
                ..Limits::production()
            },
        )
        .unwrap();
        drop(g);
        let mut g = run(&root, 0);
        g.reserve(0, 0, 10).unwrap();
        let sample = b7::sample(0, &root).unwrap();
        let approval = ProcessingApproval {
            authority: Authority::Fixture,
            window: 0,
            plan_sha256: "a".repeat(64),
            worker_sha256s: vec![durable::acquisition::current_executable_sha256().unwrap(); 3],
        };
        let at = ClockSample {
            boot_id: "HISTORICAL_CONTINUATION_FIXTURE_BOOT".into(),
            ..SystemClock.sample().unwrap()
        };
        let stage = make_stage(
            Some(&ClockPolicy::standard()),
            &Authority::Fixture,
            &StageBudget {
                max_requests: 1,
                max_response_entity_bytes_total: 1,
                max_runtime_ms: 900_000,
            },
            &approval,
            &at,
        )
        .unwrap();
        let mut next = g.state.clone();
        next.processing.insert(
            0,
            Processing {
                approval,
                stage,
                complete: false,
                continuation: None,
            },
        );
        g.commit(next).unwrap();
        fs::create_dir(root.join("work/w00")).unwrap();
        write(&root.join("work/w00/old-failure"), b"retained failure").unwrap();
        let p = &g.state.processing[&0];
        let a = ContinuationApproval {
            authority: Authority::Fixture,
            window: 0,
            previous_ledger_sha256: g.head_hash.clone(),
            original_processing_sha256: sha256(&bytes(&(&p.approval, &p.stage)).unwrap()),
            checkpoint_sha256: "b".repeat(64),
            decision_sha256: "c".repeat(64),
            plan_sha256: "d".repeat(64),
            worker_sha256s: p.approval.worker_sha256s.clone(),
            remaining_slots: (422_526_156..422_526_160).collect(),
        };
        (dir, g, sample, a)
    }
    #[test]
    fn continuation_adds_one_deadline_without_changing_old_charges_or_checkpoint() {
        let (_dir, mut g, s, a) = continuation_fixture();
        let old = g.state.clone();
        let root = g.root.clone();
        let at = SystemClock.sample().unwrap();
        assert!(g.processing_remaining_ms(&s).is_err());
        g.admit_continuation(&s, a.clone(), &at).unwrap();
        assert_eq!(g.state.processing[&0].approval, old.processing[&0].approval);
        assert_eq!(g.state.processing[&0].stage, old.processing[&0].stage);
        assert_eq!(
            (g.state.requests, g.state.entity, g.state.runs.clone()),
            (old.requests, old.entity, old.runs)
        );
        assert_eq!(
            g.processing_deadline_boot_ms(&s).unwrap(),
            at.boot_ms + 900_000
        );
        assert_eq!(
            g.continuation_decision(&s).unwrap().checkpoint_sha256,
            a.checkpoint_sha256
        );
        assert!(g.admit_continuation(&s, a.clone(), &at).is_err());
        assert!(g.check_processing_slots(&s, &[422_526_155]).is_err());
        assert!(
            g.check_processing_slots(&s, &[422_526_156, 422_526_156])
                .is_err()
        );
        g.check_processing_slots(&s, &[422_526_156]).unwrap();
        drop(g);
        let mut reopened = Guard::open(&root, true).unwrap();
        assert_eq!(
            reopened.processing_deadline_boot_ms(&s).unwrap(),
            at.boot_ms + 900_000
        );
        assert!(reopened.admit_continuation(&s, a, &at).is_err());
        assert_eq!(
            fs::read(root.join("work/w00/old-failure")).unwrap(),
            b"retained failure"
        );
        assert!(
            within_stage(
                reopened.state.processing[&0].active_stage(),
                &ClockSample {
                    boot_ms: at.boot_ms + 900_001,
                    ..at
                }
            )
            .is_err()
        );
    }
    #[test]
    fn continuation_rejects_stale_or_mismatched_authority_without_mutation() {
        for field in [
            "ledger",
            "processing",
            "window",
            "slots",
            "duplicate",
            "worker",
            "hash",
        ] {
            let (_dir, mut g, s, mut a) = continuation_fixture();
            let head = g.head_hash.clone();
            match field {
                "ledger" => a.previous_ledger_sha256 = "f".repeat(64),
                "processing" => a.original_processing_sha256 = "f".repeat(64),
                "window" => a.window = 1,
                "slots" => a.remaining_slots[0] = 422_526_155,
                "duplicate" => a.remaining_slots[1] = a.remaining_slots[0],
                "worker" => a.worker_sha256s.clear(),
                _ => a.decision_sha256 = "invalid".into(),
            }
            assert!(
                g.admit_continuation(&s, a, &SystemClock.sample().unwrap())
                    .is_err(),
                "{field}"
            );
            assert_eq!(g.head_hash, head);
            assert!(g.state.processing[&0].continuation.is_none());
        }
    }
    #[test]
    fn worker_address_space_must_be_a_real_bounded_hard_limit() {
        for limits in [
            "Max address space unlimited unlimited bytes",
            "Max address space 2147483648 unlimited bytes",
            "Max address space 2147483648 6442450944 bytes",
            "missing",
        ] {
            assert!(address_space_limit(limits).is_err());
        }
        address_space_limit("Max address space 2147483648 2147483648 bytes").unwrap();
        address_space_limit("Max address space 1073741824 2147483648 bytes").unwrap();
    }
    #[test]
    fn guard_release_does_not_wait_for_an_inherited_file_description() {
        let (_dir, root) = fixture(3, 99);
        let mut first = run(&root, 0);
        first.reserve(0, 0, 30).unwrap();
        // dup and a forked child's inherited descriptor share the same open
        // file description. Keep it alive deterministically, without a sleep.
        let inherited = first.lock.try_clone().unwrap();
        assert!(matches!(Guard::open(&root, true), Err(StoreError::Locked)));
        drop(first);
        let second = Guard::open(&root, true).unwrap();
        assert_eq!((second.state.requests, second.state.entity), (1, 30));
        second.verify_charges(0, &[(0, 30)]).unwrap();
        assert!(matches!(Guard::open(&root, true), Err(StoreError::Locked)));
        drop(inherited);
        // Closing the old duplicate cannot unlock the new owner's description.
        assert!(matches!(Guard::open(&root, true), Err(StoreError::Locked)));
        drop(second);
        let resumed = Guard::open(&root, true).unwrap();
        assert_eq!((resumed.state.requests, resumed.state.entity), (1, 30));
    }
    #[test]
    fn reserved_identity_is_valid_but_processing_is_not_authorized() {
        let root = Path::new("/fixture/campaign");
        for i in 0..16 {
            let sample = b7::sample(i, root).unwrap();
            sample.validate(978).unwrap();
            assert_eq!(
                development_processing_only(&sample).is_ok(),
                matches!(i,0..=3|8..=11)
            );
        }
    }
    #[test]
    fn shared_precharges_survive_runs_restart_and_failed_attempts() {
        let (_dir, root) = fixture(3, 99);
        let mut first = run(&root, 0);
        first.reserve(0, 0, 30).unwrap();
        assert!(Guard::open(&root, true).is_err());
        drop(first);
        let mut second = run(&root, 1);
        second.reserve(1, 0, 30).unwrap();
        drop(second);
        let mut resumed = Guard::open(&root, true).unwrap();
        assert_eq!((resumed.state.requests, resumed.state.entity), (2, 60));
        // No receipt was published: failed/unfinished attempts still count.
        assert!(resumed.verify_charges(0, &[]).is_err());
        resumed.verify_charges(0, &[(0, 30)]).unwrap();
        assert!(resumed.reserve(0, 0, 30).is_err());
        resumed.reserve(0, 1, 39).unwrap();
        assert!(resumed.reserve(1, 1, 0).is_err());
        drop(resumed);
        let g = Guard::open(&root, true).unwrap();
        assert_eq!((g.state.requests, g.state.entity), (3, 99));
    }
    #[test]
    fn no_budget_reset_duplicate_window_missing_corrupt_or_partial_journal() {
        for mutation in ["missing", "corrupt", "torn", "head-next", "root"] {
            let (_dir, root) = fixture(4, 100);
            drop(run(&root, 0));
            assert!(
                Guard::acquisition(
                    &b7::sample(0, &root).unwrap(),
                    &root.join("runs/w00"),
                    "a",
                    "b",
                    true,
                    true
                )
                .is_err()
            );
            match mutation {
                "missing" => fs::remove_file(root.join("head.json")).unwrap(),
                "corrupt" => fs::write(root.join("journal/0000000001.json"), b"{}").unwrap(),
                "torn" => write(&root.join("journal/0000000002.json"), b"partial").unwrap(),
                "head-next" => write(&root.join("head-next.json"), b"pending").unwrap(),
                _ => fs::remove_dir_all(&root).unwrap(),
            }
            assert!(Guard::open(&root, true).is_err(), "{mutation}");
            assert!(
                Guard::new(&root, true, Limits::production()).is_err(),
                "{mutation}"
            );
        }
    }
    #[test]
    fn allocated_bytes_include_failed_raw_intermediates_reports_and_future_write() {
        let (_dir, root) = fixture(4, 100);
        let g = run(&root, 0);
        let remaining = g.header.limits.disk - durable::disk_charge(&root).unwrap() - 4096;
        g.space(remaining).unwrap();
        assert!(g.space(remaining + 1).is_err());
        let artifact = root.join("work/failed-output");
        write(&artifact, &[0; 8193]).unwrap();
        assert!(g.space(remaining).is_err());
        // Small fixtures reach the same hard arithmetic boundary; no 32 GiB allocation.
    }
    #[test]
    fn wrong_order_path_role_phase_and_real_fixture_mix_are_denied() {
        let (_dir, root) = fixture(4, 100);
        assert!(
            Guard::acquisition(
                &b7::sample(1, &root).unwrap(),
                &root.join("runs/w01"),
                "a",
                "b",
                true,
                true
            )
            .is_err()
        );
        assert!(
            Guard::acquisition(
                &b7::sample(0, &root).unwrap(),
                &root.join("runs/alternate"),
                "a",
                "b",
                true,
                true
            )
            .is_err()
        );
        assert!(Guard::open(&root, false).is_err());
        let mut g = run(&root, 0);
        assert!(g.reserve(0, u64::MAX, 1).is_err());
        assert!(g.reserve(0, 0, u64::MAX).is_err());
        assert_eq!(g.state.entity, 0);
        drop(g);
        assert!(
            Guard::acquisition(
                &b7::sample(8, &root).unwrap(),
                &root.join("runs/w08"),
                "a",
                "b",
                true,
                true
            )
            .is_err()
        );
    }
}
