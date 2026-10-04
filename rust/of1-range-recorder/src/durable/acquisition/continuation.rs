//! One explicit continuation of the fixed ordinal-4 payload; not lease renewal.
use super::{
    AcquisitionStore, AggregatePlan, Audit, Authority, Clock, ClockPolicy, Path, PreparedPayload,
    Progress, RECORD_LIMIT, Request, RequestKind, Reservation, StageBudget, StageRecord,
    StoreError, StoreResult, children, decode, encode, executable_hash, fs, hash, make_stage,
    read_bounded, regular, sha256, sum_charge, sync_dir, validate_authority, validate_stage,
};
use serde::{Deserialize, Serialize};

pub const CONTINUATION_SCHEMA: &str = "OF1_B7_PAYLOAD_CONTINUATION_1";
pub const CONTINUATION_WAIT_MS: u64 = 60_000;
pub const CONTINUATION_MS: u64 = 600_000;
pub const CONTINUATION_BYTES: u64 = 4_175_463;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RetainedFile {
    pub path: String,
    pub sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ContinuationBinding {
    pub run_id: String,
    pub aggregate_sha256: String,
    pub original_payload_lease_sha256: String,
    pub previous_ledger_sha256: String,
    pub prepared_payload_sha256: String,
    pub original_executable_sha256: String,
    pub continuation_executable_sha256: String,
    pub retained_files: Vec<RetainedFile>,
    pub requests: Vec<Request>,
    pub prior_attempts: u64,
    pub prior_entity_bytes: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PayloadContinuation {
    pub schema: String,
    pub authority: Authority,
    pub binding: ContinuationBinding,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ContinuationRecord {
    pub approval: PayloadContinuation,
    pub stage: StageRecord,
}
impl PayloadContinuation {
    /// # Errors
    /// No approval is inferred from a proposal hash.
    pub fn target(&self) -> StoreResult<String> {
        Ok(sha256(&encode(&(
            CONTINUATION_SCHEMA,
            &self.binding,
            continuation_budget(),
            CONTINUATION_WAIT_MS,
        ))?))
    }
}
pub(crate) fn continuation_budget() -> StageBudget {
    StageBudget {
        max_requests: 3,
        max_response_entity_bytes_total: CONTINUATION_BYTES,
        max_runtime_ms: CONTINUATION_MS,
    }
}
fn fixed_sample(plan: &AggregatePlan, fixture: bool) -> StoreResult<()> {
    let s = plan.sample_identity.as_ref().ok_or(StoreError::Identity)?;
    s.validate(plan.epoch)?;
    let b = s.b7.as_ref().ok_or(StoreError::Identity)?;
    if b.window_ordinal != 4
        || b.phase != 1
        || b.cohort_role != "RESERVED_EVALUATION"
        || s.start_slot != 422_802_704
        || s.end_slot_exclusive != 422_802_720
        || (!fixture && b.campaign_root != crate::b7::PRODUCTION_ROOT)
        || plan.clock_policy.as_ref() != Some(&ClockPolicy::standard())
    {
        return Err(StoreError::Identity);
    }
    Ok(())
}
fn inventory_at(root: &Path, at: &Path, files: &mut Vec<RetainedFile>) -> StoreResult<()> {
    for p in children(at, 4096)? {
        if files.len() >= 4096 {
            return Err(StoreError::Budget);
        }
        let meta = fs::symlink_metadata(&p)?;
        if meta.is_dir() {
            inventory_at(root, &p, files)?;
        } else {
            regular(&p)?;
            let bytes = read_bounded(&p, 16_777_216)?;
            files.push(RetainedFile {
                path: p
                    .strip_prefix(root)
                    .map_err(|_| StoreError::Identity)?
                    .to_str()
                    .ok_or(StoreError::Identity)?
                    .into(),
                sha256: sha256(&bytes),
            });
        }
    }
    Ok(())
}
pub(super) fn inventory(root: &Path) -> StoreResult<Vec<RetainedFile>> {
    let mut files = Vec::new();
    inventory_at(root, root, &mut files)?;
    files.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(files)
}
pub(crate) fn verify_retained(root: &Path, files: &[RetainedFile]) -> StoreResult<()> {
    if files.is_empty() || files.len() > 4096 {
        return Err(StoreError::Identity);
    }
    let mut previous = "";
    for f in files {
        let p = Path::new(&f.path);
        if f.path.as_str() <= previous
            || !hash(&f.sha256)
            || !p
                .components()
                .all(|c| matches!(c, std::path::Component::Normal(_)))
            || f.path.starts_with("continuation")
            || f.path.contains("continuation-")
        {
            return Err(StoreError::Identity);
        }
        // Reject symlink traversal, including any ancestor.
        let mut full = root.to_path_buf();
        for part in p.components() {
            full.push(part);
            if fs::symlink_metadata(&full)?.file_type().is_symlink() {
                return Err(StoreError::Identity);
            }
        }
        if sha256(&read_bounded(&full, 16_777_216)?) != f.sha256 {
            return Err(StoreError::Corrupt);
        }
        previous = &f.path;
    }
    Ok(())
}
pub(crate) fn validate_continuation(
    root: &Path,
    plan: &AggregatePlan,
    run_id: &str,
    old: &StageRecord,
    prepared: &PreparedPayload,
    record: &ContinuationRecord,
) -> StoreResult<()> {
    let a = &record.approval;
    let b = &a.binding;
    let fixture = matches!(old.authority, Authority::Fixture);
    fixed_sample(plan, fixture)?;
    if a.schema != CONTINUATION_SCHEMA
        || b.run_id != run_id
        || b.aggregate_sha256 != sha256(&encode(plan)?)
        || b.original_payload_lease_sha256 != old.lease_sha256
        || b.prepared_payload_sha256 != prepared.sha256()?
        || b.original_executable_sha256 != plan.executable_sha256
        || !hash(&b.continuation_executable_sha256)
        || !hash(&b.previous_ledger_sha256)
        || b.prior_attempts != 18
        || b.prior_entity_bytes == 0
        || b.requests
            != prepared
                .requests()
                .iter()
                .filter(|r| r.sequence >= 17)
                .cloned()
                .collect::<Vec<_>>()
        || b.requests.iter().map(|r| r.sequence).collect::<Vec<_>>() != [17, 18, 19]
        || b.requests
            .iter()
            .try_fold(0u64, |n, r| n.checked_add(r.allowance()))
            .is_none_or(|n| n > CONTINUATION_BYTES)
        || matches!(a.authority, Authority::Fixture) != fixture
        || record.stage.started_at.boot_id != old.started_at.boot_id
        || record.stage.started_at.boot_ms < old.deadline_boot_ms
    {
        return Err(StoreError::Identity);
    }
    if !fixture {
        if plan.executable_sha256
            != "0336be3805229c768ff5f8a9463697a576c7093bc69ea2be55df61af9a91c081"
            || b.prior_entity_bytes != 25_029_130
        {
            return Err(StoreError::Identity);
        }
        let exact = [
            (527_324_333_656, 527_325_633_996),
            (527_325_633_996, 527_326_925_102),
            (527_326_925_102, 527_328_509_119),
        ];
        for (i, r) in b.requests.iter().enumerate() {
            if r.kind
                != (RequestKind::CarRange {
                    slot: 422_802_717 + i as u64,
                    start: exact[i].0,
                    end_exclusive: exact[i].1,
                    total: 709_264_399_796,
                    strong_etag: None,
                })
            {
                return Err(StoreError::Identity);
            }
        }
        if prepared.index_sha256
            != "649754195dd6182846a9754ffd7fa26a487e0b65bea66794e7d8bf180321a59b"
            || prepared.source_fingerprint
                != "c9dd698fab16c73ec2e832a52cf2f1fb9b729133d9cacb13d5da7257cde8ba9d"
            || prepared.metadata_receipt_sha256
                != "6cfc96b965d8a5d36bb7ff03e4805f81bdec2672f0026b1bbc7005653caa457a"
        {
            return Err(StoreError::Identity);
        }
    }
    validate_authority(plan.clock_policy.as_ref(), &a.authority, &a.target()?)?;
    validate_stage(
        &record.stage,
        plan.clock_policy.as_ref(),
        &a.authority,
        &continuation_budget(),
        a,
    )?;
    verify_retained(root, &b.retained_files)
}

impl<C: Clock> AcquisitionStore<C> {
    fn continuation_binding(&self) -> StoreResult<ContinuationBinding> {
        if self.continuation.is_some() {
            return Err(StoreError::Identity);
        }
        let p = self.payload.as_ref().ok_or(StoreError::Identity)?;
        fixed_sample(
            &self.manifest.plan,
            matches!(p.stage.authority, Authority::Fixture),
        )?;
        if self.cache.attempts.len() != 18
            || self.cache.published.keys().copied().collect::<Vec<_>>()
                != (0..17).collect::<Vec<_>>()
            || self
                .cache
                .attempts
                .iter()
                .map(|a| a.request.sequence)
                .collect::<Vec<_>>()
                != (0..18).collect::<Vec<_>>()
            || self.clock.sample()?.boot_ms < p.stage.deadline_boot_ms
        {
            return Err(StoreError::Identity);
        }
        let failed = self.root.join("pending/rejected-0000000017");
        let (_, reason, _): (Reservation, String, String) =
            decode(&read_bounded(&failed.join("failure.json"), RECORD_LIMIT)?)?;
        let header = read_bounded(
            &failed.join("headers.bin"),
            crate::acquisition_http::MAX_HEADER_BYTES as u64,
        )?;
        if reason != "HTTP_REJECTED" || !header.starts_with(b"HTTP/1.1 429 ") {
            return Err(StoreError::Identity);
        }
        Ok(ContinuationBinding {
            run_id: self.manifest.run_id.clone(),
            aggregate_sha256: self.manifest.aggregate_sha256.clone(),
            original_payload_lease_sha256: p.stage.lease_sha256.clone(),
            previous_ledger_sha256: self
                .campaign
                .as_ref()
                .ok_or(StoreError::Identity)?
                .payload_continuation_head()?,
            prepared_payload_sha256: p.prepared.sha256()?,
            original_executable_sha256: self.manifest.plan.executable_sha256.clone(),
            continuation_executable_sha256: executable_hash()?,
            retained_files: inventory(&self.root)?,
            requests: p
                .prepared
                .requests()
                .iter()
                .filter(|r| r.sequence >= 17)
                .cloned()
                .collect(),
            prior_attempts: 18,
            prior_entity_bytes: sum_charge(self.cache.attempts.iter())?,
        })
    }
    /// # Errors
    /// Read-only fixed-case proposal. The private preparation store cannot dispatch.
    pub fn payload_continuation_proposal(
        root: &Path,
        plan: &AggregatePlan,
        old_lease: &str,
        clock: C,
    ) -> StoreResult<serde_json::Value> {
        let store = Self::resume_inner(root, plan, old_lease, clock, true)?;
        let approval = PayloadContinuation {
            schema: CONTINUATION_SCHEMA.into(),
            authority: Authority::Fixture,
            binding: store.continuation_binding()?,
        };
        Ok(
            serde_json::json!({"schema":CONTINUATION_SCHEMA,"approval_target_sha256":approval.target()?,
            "approval":approval,"approved":false,"network_started":false,"budget":continuation_budget(),"wait_ms":CONTINUATION_WAIT_MS}),
        )
    }
    /// # Errors
    /// One create-once record; ambiguous cross-journal publication is a hard stop.
    pub fn admit_payload_continuation(
        root: &Path,
        plan: &AggregatePlan,
        old_lease: &str,
        approval: PayloadContinuation,
        clock: C,
    ) -> StoreResult<Progress> {
        let mut store = Self::resume_inner(root, plan, old_lease, clock, true)?;
        if approval.binding != store.continuation_binding()? {
            return Err(StoreError::Identity);
        }
        let at = store.sample()?;
        let stage = make_stage(
            plan.clock_policy.as_ref(),
            &approval.authority,
            &continuation_budget(),
            &approval,
            &at,
        )?;
        let record = ContinuationRecord { approval, stage };
        let p = store.payload.as_ref().ok_or(StoreError::Identity)?;
        validate_continuation(
            root,
            plan,
            &store.manifest.run_id,
            &p.stage,
            &p.prepared,
            &record,
        )?;
        // Persist intent, then native campaign authority, then atomic run publication.
        // Any crash between them remains an explicit blocked seam, never a new budget.
        let encoded = encode(&record)?;
        let intent = root.join("pending/continuation-intent.json");
        store.write_new(&intent, &encoded)?;
        sync_dir(&root.join("pending"))?;
        store
            .campaign
            .as_mut()
            .ok_or(StoreError::Identity)?
            .admit_payload_continuation(
                &record.approval.binding.previous_ledger_sha256,
                &record.stage.lease_sha256,
            )?;
        fs::hard_link(intent, root.join("continuation.json"))?;
        sync_dir(root)?;
        store.continuation = Some(record);
        store.preparation_only = false;
        store.refresh()?;
        store.progress()
    }
    pub(super) fn audit_continuation(&self, audit: &Audit) -> StoreResult<()> {
        let Some(c) = &self.continuation else {
            return Ok(());
        };
        let p = self.payload.as_ref().ok_or(StoreError::Corrupt)?;
        validate_continuation(
            &self.root,
            &self.manifest.plan,
            &self.manifest.run_id,
            &p.stage,
            &p.prepared,
            c,
        )?;
        if audit.attempts.len() < 18
            || sum_charge(audit.attempts.iter().take(18))? != c.approval.binding.prior_entity_bytes
        {
            return Err(StoreError::Corrupt);
        }
        let mut previous = c.stage.started_at.boot_ms;
        for (i, a) in audit.attempts.iter().skip(18).enumerate() {
            if i >= 3
                || a.request != c.approval.binding.requests[i]
                || a.lease_sha256 != c.stage.lease_sha256
                || a.at.boot_ms
                    < previous
                        .checked_add(CONTINUATION_WAIT_MS)
                        .ok_or(StoreError::Clock)?
            {
                return Err(StoreError::Corrupt);
            }
            if let Some(p) = audit.published.get(&a.request.sequence) {
                previous = p.receipt.acquired_at.boot_ms;
            } else if audit.attempts.len() != 19 + i {
                return Err(StoreError::Corrupt);
            }
        }
        // New attempts also consume the original payload request/entity ceilings.
        let all = audit
            .attempts
            .iter()
            .filter(|a| a.request.sequence >= 4)
            .collect::<Vec<_>>();
        if all.len() as u64 > p.lease.budget.max_requests
            || sum_charge(all.into_iter())? > p.lease.budget.max_response_entity_bytes_total
        {
            return Err(StoreError::Budget);
        }
        Ok(())
    }
    /// # Errors
    /// Native enforced pacing uses original saved clocks, also after restart.
    pub fn continuation_wait_ms(&mut self) -> StoreResult<u64> {
        let Some(c) = &self.continuation else {
            return Ok(0);
        };
        let mut earliest = c.stage.started_at.boot_ms;
        for a in self.cache.attempts.iter().skip(18) {
            let p = self
                .cache
                .published
                .get(&a.request.sequence)
                .ok_or(StoreError::Identity)?;
            earliest = p.receipt.acquired_at.boot_ms;
        }
        if self.root.join("continuation-stop.json").exists() {
            return Err(StoreError::Identity);
        }
        let earliest = earliest
            .checked_add(CONTINUATION_WAIT_MS)
            .ok_or(StoreError::Clock)?;
        let at = self.dispatch_now(None)?;
        Ok(earliest.saturating_sub(at.boot_ms))
    }
    /// # Errors
    /// Persist a terminal continuation stop without exposing transport/body data.
    pub fn stop_payload_continuation(&mut self) -> StoreResult<()> {
        if let Some(c) = &self.continuation {
            let p = self.root.join("continuation-stop.json");
            if !p.exists() {
                self.write_new(&p, &encode(&c.stage.lease_sha256)?)?;
                sync_dir(&self.root)?;
            }
        }
        Ok(())
    }
}
