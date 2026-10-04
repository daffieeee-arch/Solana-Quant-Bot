//! One separately approved metadata continuation per fixed-campaign run.
//! Original authority, charges and retained bytes remain immutable evidence.
use super::continuation::{RetainedFile, inventory, verify_retained};
use super::{
    AcquisitionStore, AggregatePlan, Audit, Authority, Clock, ClockPolicy, Progress, StageBudget,
    StageRecord, StoreError, StoreResult, encode, executable_hash, fs, hash, make_stage,
    metadata_requests, sha256, sum_charge, sync_dir, validate_authority, validate_stage,
};
use serde::{Deserialize, Serialize};
use std::path::Path;

pub const SCHEMA: &str = "OF1_B7_METADATA_CONTINUATION_1";
pub const EXTRA_RUNTIME_MS: u64 = 600_000;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct MetadataContinuationBinding {
    pub run_id: String,
    pub aggregate_sha256: String,
    pub original_metadata_lease_sha256: String,
    pub previous_ledger_sha256: String,
    pub original_executable_sha256: String,
    pub continuation_executable_sha256: String,
    pub sample_identity: crate::sample::SampleIdentity,
    pub expected_index_sha256: String,
    pub expected_source_fingerprint: String,
    pub requests: Vec<super::Request>,
    pub retained_files: Vec<RetainedFile>,
    pub prior_attempts: u64,
    pub prior_entity_bytes: u64,
    pub remaining_budget: StageBudget,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct MetadataContinuation {
    pub schema: String,
    pub authority: Authority,
    pub binding: MetadataContinuationBinding,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct MetadataContinuationRecord {
    pub approval: MetadataContinuation,
    pub stage: StageRecord,
}
impl MetadataContinuation {
    /// # Errors
    /// Serialization failure; a target itself grants no permission.
    pub fn target(&self) -> StoreResult<String> {
        Ok(sha256(&encode(&(SCHEMA, &self.binding))?))
    }
}
fn fixed_sample(plan: &AggregatePlan, fixture: bool) -> StoreResult<crate::sample::SampleIdentity> {
    let sample = plan.sample_identity.as_ref().ok_or(StoreError::Identity)?;
    sample.validate(plan.epoch)?;
    let b = sample.b7.as_ref().ok_or(StoreError::Identity)?;
    if b.phase != 1
        || b.window_ordinal >= 8
        || (!fixture && b.campaign_root != crate::b7::PRODUCTION_ROOT)
        || plan.clock_policy.as_ref() != Some(&ClockPolicy::standard())
    {
        return Err(StoreError::Identity);
    }
    Ok(sample.clone())
}
fn remaining_budget(old: &StageBudget, attempts: u64, entity: u64) -> StoreResult<StageBudget> {
    let budget = StageBudget {
        max_requests: old
            .max_requests
            .checked_sub(attempts)
            .ok_or(StoreError::Budget)?,
        max_response_entity_bytes_total: old
            .max_response_entity_bytes_total
            .checked_sub(entity)
            .ok_or(StoreError::Budget)?,
        max_runtime_ms: EXTRA_RUNTIME_MS,
    };
    if budget.max_requests == 0 || budget.max_response_entity_bytes_total == 0 {
        return Err(StoreError::Budget);
    }
    Ok(budget)
}
pub(crate) fn validate_record(
    root: &Path,
    plan: &AggregatePlan,
    run_id: &str,
    old: &StageRecord,
    record: &MetadataContinuationRecord,
) -> StoreResult<()> {
    let a = &record.approval;
    let b = &a.binding;
    let fixture = matches!(old.authority, Authority::Fixture);
    if a.schema != SCHEMA
        || b.run_id != run_id
        || b.aggregate_sha256 != sha256(&encode(plan)?)
        || b.original_metadata_lease_sha256 != old.lease_sha256
        || b.original_executable_sha256 != plan.executable_sha256
        || !hash(&b.continuation_executable_sha256)
        || !hash(&b.previous_ledger_sha256)
        || b.sample_identity != fixed_sample(plan, fixture)?
        || b.expected_index_sha256 != crate::b7::INDEX_SHA256
        || b.expected_source_fingerprint != crate::b7::SOURCE_FINGERPRINT
        || b.requests != metadata_requests()
        || b.prior_attempts == 0
        || b.remaining_budget
            != remaining_budget(&old.budget, b.prior_attempts, b.prior_entity_bytes)?
        || matches!(a.authority, Authority::Fixture) != fixture
        || record.stage.started_at.boot_id != old.started_at.boot_id
        || record.stage.started_at.boot_ms < old.deadline_boot_ms
    {
        return Err(StoreError::Identity);
    }
    validate_authority(plan.clock_policy.as_ref(), &a.authority, &a.target()?)?;
    validate_stage(
        &record.stage,
        plan.clock_policy.as_ref(),
        &a.authority,
        &b.remaining_budget,
        a,
    )?;
    verify_retained(root, &b.retained_files)
}
impl<C: Clock> AcquisitionStore<C> {
    fn metadata_continuation_binding(&self) -> StoreResult<MetadataContinuationBinding> {
        if self.payload.is_some()
            || self.continuation.is_some()
            || self.metadata_continuation.is_some()
            || self.cache.attempts.is_empty()
            || self.cache.published.len() == 4
            || self.clock.sample()?.boot_ms < self.manifest.metadata_stage.deadline_boot_ms
        {
            return Err(StoreError::Identity);
        }
        let sample = fixed_sample(
            &self.manifest.plan,
            matches!(self.manifest.metadata_stage.authority, Authority::Fixture),
        )?;
        let window = sample
            .b7
            .as_ref()
            .ok_or(StoreError::Identity)?
            .window_ordinal;
        let attempts = self.cache.attempts.len() as u64;
        let entity = sum_charge(self.cache.attempts.iter())?;
        Ok(MetadataContinuationBinding {
            run_id: self.manifest.run_id.clone(),
            aggregate_sha256: self.manifest.aggregate_sha256.clone(),
            original_metadata_lease_sha256: self.manifest.metadata_stage.lease_sha256.clone(),
            previous_ledger_sha256: self
                .campaign
                .as_ref()
                .ok_or(StoreError::Identity)?
                .metadata_continuation_head(window)?,
            original_executable_sha256: self.manifest.plan.executable_sha256.clone(),
            continuation_executable_sha256: executable_hash()?,
            sample_identity: sample,
            expected_index_sha256: crate::b7::INDEX_SHA256.into(),
            expected_source_fingerprint: crate::b7::SOURCE_FINGERPRINT.into(),
            requests: metadata_requests(),
            retained_files: inventory(&self.root)?,
            prior_attempts: attempts,
            prior_entity_bytes: entity,
            remaining_budget: remaining_budget(
                &self.manifest.metadata_lease.budget,
                attempts,
                entity,
            )?,
        })
    }
    /// # Errors
    /// Read-only proposal for an expired, incomplete fixed-campaign metadata stage.
    pub fn metadata_continuation_proposal(
        root: &Path,
        plan: &AggregatePlan,
        old_lease: &str,
        clock: C,
    ) -> StoreResult<serde_json::Value> {
        let store = Self::resume_inner(root, plan, old_lease, clock, true)?;
        let a = MetadataContinuation {
            schema: SCHEMA.into(),
            authority: Authority::Fixture,
            binding: store.metadata_continuation_binding()?,
        };
        Ok(
            serde_json::json!({"schema":SCHEMA,"approval_target_sha256":a.target()?,"approval":a,"approved":false,"network_started":false}),
        )
    }
    /// # Errors
    /// Fresh exact authority is appended once; interrupted admission fails closed.
    pub fn admit_metadata_continuation(
        root: &Path,
        plan: &AggregatePlan,
        old_lease: &str,
        approval: MetadataContinuation,
        clock: C,
    ) -> StoreResult<Progress> {
        let mut store = Self::resume_inner(root, plan, old_lease, clock, true)?;
        if approval.binding != store.metadata_continuation_binding()? {
            return Err(StoreError::Identity);
        }
        let at = store.sample()?;
        let stage = make_stage(
            plan.clock_policy.as_ref(),
            &approval.authority,
            &approval.binding.remaining_budget,
            &approval,
            &at,
        )?;
        let record = MetadataContinuationRecord { approval, stage };
        validate_record(
            root,
            plan,
            &store.manifest.run_id,
            &store.manifest.metadata_stage,
            &record,
        )?;
        let intent = root.join("pending/metadata-continuation-intent.json");
        store.write_new(&intent, &encode(&record)?)?;
        sync_dir(&root.join("pending"))?;
        let window = record
            .approval
            .binding
            .sample_identity
            .b7
            .as_ref()
            .ok_or(StoreError::Identity)?
            .window_ordinal;
        store
            .campaign
            .as_mut()
            .ok_or(StoreError::Identity)?
            .admit_metadata_continuation(
                window,
                &record.approval.binding.previous_ledger_sha256,
                &record.stage.lease_sha256,
            )?;
        fs::hard_link(intent, root.join("metadata-continuation.json"))?;
        sync_dir(root)?;
        store.metadata_continuation = Some(record);
        store.preparation_only = false;
        store.refresh()?;
        store.progress()
    }
    pub(super) fn audit_metadata_continuation(&self, audit: &Audit) -> StoreResult<()> {
        let Some(c) = &self.metadata_continuation else {
            return Ok(());
        };
        validate_record(
            &self.root,
            &self.manifest.plan,
            &self.manifest.run_id,
            &self.manifest.metadata_stage,
            c,
        )?;
        verify_metadata_charges(
            &self.manifest.metadata_lease.budget,
            c,
            audit
                .attempts
                .iter()
                .map(|a| (a.request.sequence, a.reserved_entity_bytes)),
        )
    }
}
pub(crate) fn verify_metadata_charges(
    original_budget: &StageBudget,
    c: &MetadataContinuationRecord,
    attempts: impl Iterator<Item = (u64, u64)>,
) -> StoreResult<()> {
    let metadata = attempts.filter(|(seq, _)| *seq < 4).collect::<Vec<_>>();
    let prior =
        usize::try_from(c.approval.binding.prior_attempts).map_err(|_| StoreError::Corrupt)?;
    if metadata.len() < prior
        || metadata
            .iter()
            .take(prior)
            .try_fold(0u64, |sum, (_, n)| sum.checked_add(*n))
            .ok_or(StoreError::Budget)?
            != c.approval.binding.prior_entity_bytes
        || metadata.len() as u64 > original_budget.max_requests
        || metadata
            .iter()
            .try_fold(0u64, |sum, (_, n)| sum.checked_add(*n))
            .ok_or(StoreError::Budget)?
            > original_budget.max_response_entity_bytes_total
    {
        return Err(StoreError::Corrupt);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    include!("metadata_continuation_approved_tests.rs");
}
