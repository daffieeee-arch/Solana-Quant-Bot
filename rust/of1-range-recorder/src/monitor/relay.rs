//! Explicit local Unix-datagram capability, disabled in default builds.
//! Sender calls are nonblocking and lossy. Only the independent collector writes
//! telemetry; no collector acknowledgement is needed for acquisition progress.

use super::{
    MAX_ERRORS, MAX_RUNS, MAX_SNAPSHOT_BYTES, Rates, SAMPLE_INTERVAL_MS, Snapshot, invalid, wall_ms,
};
use crate::durable::acquisition::{AggregateBudget, Progress, Receipt, StageBudget};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::{self, Write},
    os::unix::net::UnixDatagram,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub struct Monitor {
    pub snapshot: Snapshot,
    socket: Option<UnixDatagram>,
    destination: PathBuf,
    started: Instant,
    elapsed_before_process: u64,
    last_emit_ms: Option<u64>,
    rates: Rates,
    deadline_wall_ms: u64,
}

impl Monitor {
    /// A missing/slow collector never fails construction or the acquisition.
    #[must_use]
    pub fn new(mut snapshot: Snapshot, destination: &Path) -> Self {
        snapshot.mode = "LIVE".into();
        snapshot.stage = "PREPARING".into();
        snapshot.session_id = format!("live-{}-{}", std::process::id(), wall_ms());
        snapshot.sequence = 0;
        snapshot.completed_at_ms = None;
        snapshot.errors.clear();
        snapshot.traffic.received_basis = if snapshot.traffic.attempts == 0 {
            "PROCESS_OBSERVED"
        } else {
            "DURABLE_LOWER_BOUND"
        }
        .into();
        snapshot.traffic.speed_bps = None;
        snapshot.traffic.download_eta_ms = None;
        snapshot.traffic.eta_scope = None;
        snapshot.traffic.speed_samples.clear();
        let rates = Rates::new(snapshot.traffic.received_bytes);
        let elapsed_before_process = wall_ms().saturating_sub(snapshot.started_at_ms);
        let deadline_wall_ms = wall_ms().saturating_add(snapshot.budgets.runtime_remaining_ms);
        let socket = UnixDatagram::unbound()
            .ok()
            .and_then(|socket| socket.set_nonblocking(true).ok().map(|()| socket));
        Self {
            snapshot,
            socket,
            destination: destination.into(),
            started: Instant::now(),
            elapsed_before_process,
            last_emit_ms: None,
            rates,
            deadline_wall_ms,
        }
    }

    pub fn reserve(&mut self, sequence: u64, progress: &Progress) {
        let delta_attempts = progress
            .attempts_reserved
            .saturating_sub(self.snapshot.traffic.attempts);
        let delta_reserved = progress
            .charged_entity_bytes
            .saturating_sub(self.snapshot.traffic.reserved_bytes);
        self.snapshot.budgets.attempts_remaining = self
            .snapshot
            .budgets
            .attempts_remaining
            .saturating_sub(delta_attempts);
        self.snapshot.budgets.stage_attempts_remaining = self
            .snapshot
            .budgets
            .stage_attempts_remaining
            .saturating_sub(delta_attempts);
        self.snapshot.budgets.entity_bytes_remaining = self
            .snapshot
            .budgets
            .entity_bytes_remaining
            .saturating_sub(delta_reserved);
        self.snapshot.budgets.stage_entity_bytes_remaining = self
            .snapshot
            .budgets
            .stage_entity_bytes_remaining
            .saturating_sub(delta_reserved);
        self.progress_counters(progress);
        if let Some(op) = self
            .snapshot
            .operations
            .iter_mut()
            .find(|o| o.sequence == sequence)
        {
            op.state = "RESERVED".into();
            op.received_bytes = Some(0);
            op.attempts += 1;
            op.error = None;
            op.status_code = None;
        }
        self.snapshot.stage = if sequence < 4 {
            "METADATA"
        } else {
            "DOWNLOADING"
        }
        .into();
        self.snapshot.traffic.speed_bps = None;
        self.snapshot.traffic.download_eta_ms = None;
        self.snapshot.refresh_selection();
        self.emit(true);
    }

    pub fn head(&mut self, sequence: u64, status: u16, entity_length: u64) {
        if let Some(op) = self
            .snapshot
            .operations
            .iter_mut()
            .find(|o| o.sequence == sequence)
        {
            op.state = "DOWNLOADING".into();
            op.status_code = Some(status);
            op.expected_bytes = Some(entity_length);
        }
        self.snapshot.stage = "DOWNLOADING".into();
        self.snapshot.refresh_selection();
        self.emit(true);
    }

    /// Called on each successful response entity read, before durable segment
    /// coalescing. Network traffic is not counted as already published data.
    pub fn received(&mut self, sequence: u64, bytes: u64) {
        if let Some(op) = self
            .snapshot
            .operations
            .iter_mut()
            .find(|o| o.sequence == sequence)
        {
            op.received_bytes = Some(op.received_bytes.unwrap_or(0).saturating_add(bytes));
            self.snapshot.traffic.received_bytes =
                self.snapshot.traffic.received_bytes.saturating_add(bytes);
        }
        self.snapshot.refresh_selection();
        self.emit(false);
    }

    pub fn verifying(&mut self, sequence: u64) {
        self.stage(sequence, "VERIFYING");
    }
    pub fn publishing(&mut self, sequence: u64) {
        self.stage(sequence, "PUBLISHING");
    }

    fn stage(&mut self, sequence: u64, stage: &str) {
        if let Some(op) = self
            .snapshot
            .operations
            .iter_mut()
            .find(|o| o.sequence == sequence)
        {
            op.state = stage.into();
        }
        self.snapshot.stage = stage.into();
        self.snapshot.traffic.speed_bps = None;
        self.snapshot.traffic.download_eta_ms = None;
        self.emit(true);
    }

    pub fn published(&mut self, sequence: u64, receipt: &Receipt) {
        if let Some(op) = self
            .snapshot
            .operations
            .iter_mut()
            .find(|o| o.sequence == sequence)
        {
            op.state = "PUBLISHED".into();
            op.published_bytes = receipt.response_entity_bytes;
            op.expected_bytes = Some(receipt.response_entity_bytes);
            op.status_code = Some(receipt.response.status);
            op.error = None;
        }
        self.snapshot.integrity.receipts = "VERIFIED_PUBLISHED_ONLY".into();
        if let Ok(bytes) = serde_json::to_vec(receipt) {
            let id = format!("receipt-{sequence}");
            let artifact = super::Artifact {
                id: id.clone(),
                label: format!("Operation {sequence} receipt"),
                path: format!("published/{sequence:010}/receipt.json"),
                sha256: crate::sha256(&bytes),
            };
            if let Some(existing) = self.snapshot.artifacts.iter_mut().find(|a| a.id == id) {
                *existing = artifact;
            } else if self.snapshot.artifacts.len() < super::MAX_ARTIFACTS {
                self.snapshot.artifacts.push(artifact);
            }
        }
        self.snapshot.refresh_selection();
        self.emit(true);
    }

    pub fn failed(&mut self, sequence: u64, reason: &str) {
        let reason: String = reason.chars().take(256).collect();
        if let Some(op) = self
            .snapshot
            .operations
            .iter_mut()
            .find(|o| o.sequence == sequence)
        {
            op.state = "FAILED".into();
            op.error = Some(reason.clone());
        }
        self.snapshot.stage = "STOPPED".into();
        self.snapshot.errors.push(reason);
        if self.snapshot.errors.len() > MAX_ERRORS {
            self.snapshot.errors.remove(0);
        }
        self.snapshot.traffic.speed_bps = None;
        self.snapshot.traffic.download_eta_ms = None;
        self.snapshot.refresh_selection();
        self.emit(true);
    }

    pub fn complete(&mut self) {
        self.snapshot.refresh_selection();
        if self.snapshot.selection.operations_published != self.snapshot.selection.operations_total
        {
            self.failed(
                u64::MAX,
                "INCOMPLETE: planned operation remains unpublished",
            );
            return;
        }
        self.snapshot.stage = "COMPLETE".into();
        self.snapshot.completed_at_ms = Some(wall_ms());
        self.snapshot.integrity.receipts = "VERIFIED".into();
        self.snapshot.traffic.speed_bps = None;
        self.snapshot.traffic.download_eta_ms = None;
        self.emit(true);
    }

    pub fn sync_progress(
        &mut self,
        progress: &Progress,
        aggregate: &AggregateBudget,
        stage: &StageBudget,
        stage_attempts: u64,
        stage_reserved: u64,
    ) {
        self.progress_counters(progress);
        self.snapshot.budgets.attempts_remaining = aggregate
            .max_requests
            .saturating_sub(progress.attempts_reserved);
        self.snapshot.budgets.entity_bytes_remaining = aggregate
            .max_total_response_entity_bytes
            .saturating_sub(progress.charged_entity_bytes);
        self.snapshot.budgets.stage_attempts_remaining =
            stage.max_requests.saturating_sub(stage_attempts);
        self.snapshot.budgets.stage_entity_bytes_remaining = stage
            .max_response_entity_bytes_total
            .saturating_sub(stage_reserved);
        self.emit(true);
    }

    fn progress_counters(&mut self, progress: &Progress) {
        self.snapshot.traffic.attempts = progress.attempts_reserved;
        self.snapshot.traffic.reserved_bytes = progress.charged_entity_bytes;
        self.snapshot.storage.used_bytes = progress.disk_charge_bytes;
        self.snapshot.storage.available_bytes = progress.available_disk_bytes;
        self.snapshot.budgets.runtime_remaining_ms =
            progress.deadline_wall_ms.saturating_sub(wall_ms());
        self.deadline_wall_ms = progress.deadline_wall_ms;
    }

    /// At most five intra-request sends per second; phase edges may force a send.
    /// No retry, file write, fsync, wait, collector acknowledgement or writer lock.
    pub fn emit(&mut self, force: bool) {
        let elapsed = u64::try_from(self.started.elapsed().as_millis()).unwrap_or(u64::MAX);
        if !force
            && self
                .last_emit_ms
                .is_some_and(|last| elapsed.saturating_sub(last) < SAMPLE_INTERVAL_MS)
        {
            return;
        }
        self.last_emit_ms = Some(elapsed);
        self.snapshot.elapsed_ms = self.elapsed_before_process.saturating_add(elapsed);
        self.snapshot.updated_at_ms = wall_ms();
        self.snapshot.budgets.runtime_remaining_ms = self
            .deadline_wall_ms
            .saturating_sub(self.snapshot.updated_at_ms);
        self.snapshot.sequence += 1;
        self.rates.observe(&mut self.snapshot, elapsed);
        if self.snapshot.traffic.download_eta_ms.is_none() {
            self.snapshot.traffic.eta_scope = None;
        }
        let sent = self
            .snapshot
            .validate()
            .ok()
            .and_then(|()| serde_json::to_vec(&self.snapshot).ok())
            .and_then(|bytes| {
                self.socket
                    .as_ref()?
                    .send_to(&bytes, &self.destination)
                    .ok()
                    .filter(|n| *n == bytes.len())
            });
        if sent.is_none() {
            self.snapshot.dropped_samples = self.snapshot.dropped_samples.saturating_add(1);
        }
    }
}

fn output_directory(directory: &Path, dataset: Option<&Path>) -> io::Result<PathBuf> {
    let canonical = crate::dataset_location::validate_dataset_location(directory)?;
    if dataset.is_some_and(|root| canonical.starts_with(root)) {
        return Err(invalid(
            "telemetry must be outside immutable run directories",
        ));
    }
    for ancestor in canonical.ancestors() {
        if ancestor.join("run.json").try_exists()? || ancestor.join("writer.lock").try_exists()? {
            return Err(invalid(
                "telemetry directory is inside an immutable acquisition run",
            ));
        }
    }
    if !canonical.try_exists()? {
        fs::create_dir(&canonical)?;
    }
    if !fs::symlink_metadata(&canonical)?.is_dir() {
        return Err(invalid("telemetry output is not a directory"));
    }
    Ok(canonical)
}

/// Publish a replaceable operational snapshot outside immutable evidence. There
/// is deliberately no fsync: losing telemetry does not lose or authorize Raw.
/// # Errors
/// Refuses invalid/big snapshots, output in a run, or more than sixteen run IDs.
pub fn write_snapshot(snapshot: &Snapshot, directory: &Path) -> io::Result<PathBuf> {
    snapshot.validate()?;
    let dataset = Path::new(&snapshot.dataset_root).canonicalize()?;
    let directory = output_directory(directory, Some(&dataset))?;
    let target = directory.join(format!("latest-{}.json", snapshot.id));
    let mut runs = 0;
    for entry in fs::read_dir(&directory)? {
        let entry = entry?;
        if entry.path().extension().is_some_and(|e| e == "json") {
            runs += 1;
        }
        if runs > MAX_RUNS || runs == MAX_RUNS && !target.try_exists()? {
            return Err(invalid("monitor run retention limit reached"));
        }
    }
    let temporary = directory.join(format!(".{}-{}.tmp", snapshot.id, std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    let result = (|| {
        file.write_all(&serde_json::to_vec(snapshot).map_err(invalid)?)?;
        drop(file);
        fs::rename(&temporary, &target)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result?;
    Ok(target)
}

pub struct Relay {
    socket: UnixDatagram,
    socket_path: PathBuf,
    directory: PathBuf,
    latest: BTreeMap<String, (String, u64, u64)>,
}

impl Relay {
    /// # Errors
    /// Uses a fresh local socket path outside run roots; never replaces an
    /// existing socket or creates an acquisition listener.
    pub fn bind(socket_path: &Path, directory: &Path) -> io::Result<Self> {
        let directory = output_directory(directory, None)?;
        let parent = socket_path
            .parent()
            .ok_or_else(|| invalid("socket requires parent"))?;
        let parent = output_directory(parent, None)?;
        let socket_path = parent.join(
            socket_path
                .file_name()
                .ok_or_else(|| invalid("socket requires filename"))?,
        );
        let socket = UnixDatagram::bind(&socket_path)?;
        socket.set_read_timeout(Some(Duration::from_millis(250)))?;
        Ok(Self {
            socket,
            socket_path,
            directory,
            latest: BTreeMap::new(),
        })
    }

    /// # Errors
    /// Invalid messages are rejected before publication; the receiver does not
    /// acknowledge, reserve, acquire, lock, or modify a run.
    pub fn receive_once(&mut self) -> io::Result<bool> {
        let mut bytes = vec![0u8; MAX_SNAPSHOT_BYTES + 1];
        let count = match self.socket.recv(&mut bytes) {
            Ok(count) => count,
            Err(error)
                if [io::ErrorKind::WouldBlock, io::ErrorKind::TimedOut].contains(&error.kind()) =>
            {
                return Ok(false);
            }
            Err(error) => return Err(error),
        };
        if count > MAX_SNAPSHOT_BYTES {
            return Err(invalid("oversized monitor datagram"));
        }
        let snapshot: Snapshot = serde_json::from_slice(&bytes[..count]).map_err(invalid)?;
        snapshot.validate()?;
        if !self.latest.contains_key(&snapshot.id) && self.latest.len() >= MAX_RUNS {
            return Err(invalid("monitor run retention limit reached"));
        }
        if self
            .latest
            .get(&snapshot.id)
            .is_some_and(|(session, sequence, updated_at_ms)| {
                snapshot.updated_at_ms < *updated_at_ms
                    || session != &snapshot.session_id && snapshot.updated_at_ms == *updated_at_ms
                    || session == &snapshot.session_id && snapshot.sequence <= *sequence
            })
        {
            return Err(invalid("monitor sequence regression"));
        }
        write_snapshot(&snapshot, &self.directory)?;
        self.latest.insert(
            snapshot.id,
            (
                snapshot.session_id,
                snapshot.sequence,
                snapshot.updated_at_ms,
            ),
        );
        Ok(true)
    }
}

impl Drop for Relay {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.socket_path);
    }
}

/// # Errors
/// Initial bind errors fail the collector only. Invalid samples are dropped with
/// a bounded error, never retried or reflected into acquisition authority.
pub fn run_relay(socket_path: &Path, directory: &Path) -> io::Result<()> {
    let mut relay = Relay::bind(socket_path, directory)?;
    loop {
        if let Err(error) = relay.receive_once() {
            eprintln!(
                "OF1_MONITOR_SAMPLE_REJECTED: {}",
                error.to_string().chars().take(256).collect::<String>()
            );
        }
    }
}
