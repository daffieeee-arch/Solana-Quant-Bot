//! Versioned acquisition clock semantics. Legacy plans retain dual-clock expiry;
//! newly approved plans use same-boot elapsed time only. UTC is never clamped.

use crate::durable::{ClockSample, StoreError, StoreResult};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ClockPolicy {
    pub schema: String,
    pub version: u32,
    pub initialization_window_ms: u64,
    pub approval_validity_ms: u64,
}

impl ClockPolicy {
    #[must_use]
    pub fn standard() -> Self {
        Self {
            schema: "OF1_BOOT_CLOCK_POLICY_1".into(),
            version: 1,
            initialization_window_ms: 600_000,
            approval_validity_ms: 1_200_000,
        }
    }

    /// # Errors
    /// No different version, zero duration or implicit approval extension exists.
    pub fn validate(&self) -> StoreResult<()> {
        if self == &Self::standard() {
            Ok(())
        } else {
            Err(StoreError::Identity)
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ApprovalAnchor {
    pub t0: ClockSample,
    pub initialize_by_boot_ms: u64,
    pub expires_at_boot_ms: u64,
}

impl ApprovalAnchor {
    /// Construct once from the real approval-time pair; never resample on restart.
    /// # Errors
    /// Invalid policy/clock, zero approval UTC or arithmetic overflow fails closed.
    pub fn new(policy: &ClockPolicy, t0: ClockSample) -> StoreResult<Self> {
        policy.validate()?;
        valid_sample(&t0)?;
        if t0.wall_ms == 0 {
            return Err(StoreError::Clock);
        }
        t0.wall_ms
            .checked_add(policy.approval_validity_ms)
            .ok_or(StoreError::Clock)?;
        Ok(Self {
            initialize_by_boot_ms: t0
                .boot_ms
                .checked_add(policy.initialization_window_ms)
                .ok_or(StoreError::Clock)?,
            expires_at_boot_ms: t0
                .boot_ms
                .checked_add(policy.approval_validity_ms)
                .ok_or(StoreError::Clock)?,
            t0,
        })
    }

    /// # Errors
    /// Recomputes both boot deadlines and the declared UTC provenance endpoints.
    pub fn validate(
        &self,
        policy: &ClockPolicy,
        approved_at_ms: u64,
        not_after_ms: u64,
    ) -> StoreResult<()> {
        if self != &Self::new(policy, self.t0.clone())?
            || approved_at_ms != self.t0.wall_ms
            || self.t0.wall_ms.checked_add(policy.approval_validity_ms) != Some(not_after_ms)
        {
            return Err(StoreError::Identity);
        }
        Ok(())
    }

    /// # Errors
    /// Initialization is allowed at T0+10 minutes, but never at/after boot expiry.
    pub fn admit_initialization(&self, policy: &ClockPolicy, at: &ClockSample) -> StoreResult<()> {
        if self != &Self::new(policy, self.t0.clone())? {
            return Err(StoreError::Identity);
        }
        check_follows(Some(policy), &self.t0, at)?;
        if at.boot_ms > self.initialize_by_boot_ms || at.boot_ms >= self.expires_at_boot_ms {
            return Err(StoreError::Deadline);
        }
        Ok(())
    }
}

/// Compare actual pairs without inventing wall-clock values or applying tolerance.
/// # Errors
/// Every version rejects invalid/changed boot identity or decreasing boot elapsed
/// time. Only absent (historical) policy also rejects UTC moving backwards.
pub fn check_follows(
    policy: Option<&ClockPolicy>,
    before: &ClockSample,
    after: &ClockSample,
) -> StoreResult<()> {
    if let Some(policy) = policy {
        policy.validate()?;
    }
    valid_sample(before)?;
    valid_sample(after)?;
    if before.boot_id != after.boot_id
        || after.boot_ms < before.boot_ms
        || policy.is_none() && after.wall_ms < before.wall_ms
    {
        return Err(StoreError::Clock);
    }
    Ok(())
}

fn valid_sample(at: &ClockSample) -> StoreResult<()> {
    if at.boot_id.is_empty()
        || at.boot_id.len() > 128
        || at.boot_id.bytes().any(|b| b.is_ascii_control())
    {
        Err(StoreError::Clock)
    } else {
        Ok(())
    }
}
