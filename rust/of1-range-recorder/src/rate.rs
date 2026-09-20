//! One shared official OF1 response-entity limit, not physical network accounting.
//!
//! Cooperating official acquisitions hold the same user-scoped lock for their
//! entire request. Every holder starts with zero credit; release/crash/restart
//! discards unused credit rather than creating another burst allowance. Plaintext
//! read capacity is charged before reading, and unused capacity is refunded.

use serde::{Deserialize, Serialize};
use thiserror::Error;

pub const ENTITY_BYTES_PER_SECOND: u64 = 87_500_000;
pub const BURST_BYTES: u64 = 65_536;
const NANOS_PER_SECOND: u128 = 1_000_000_000;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DownloadRate {
    pub unit: String,
    pub bytes_per_second: u64,
    pub burst_bytes: u64,
    pub concurrency: u64,
    pub scope: String,
}

impl DownloadRate {
    #[must_use]
    pub fn standard() -> Self {
        Self {
            unit: "RESPONSE_ENTITY_BYTES".into(),
            bytes_per_second: ENTITY_BYTES_PER_SECOND,
            burst_bytes: BURST_BYTES,
            concurrency: 1,
            scope: "SAME_USER_OFFICIAL_OF1_ALL_RUNS".into(),
        }
    }

    /// No caller-selected rate, unit, burst or wider concurrency is accepted.
    /// # Errors
    /// Rejects anything other than the approved standard policy.
    pub fn validate(&self) -> Result<(), RateError> {
        if self == &Self::standard() {
            Ok(())
        } else {
            Err(RateError::Policy)
        }
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum RateError {
    #[error("OF1_DOWNLOAD_RATE_POLICY_REQUIRED_OR_INVALID")]
    Policy,
    #[error("OF1_SHARED_DOWNLOAD_ALREADY_ACTIVE")]
    Busy,
    #[error("OF1_RATE_COORDINATION_UNAVAILABLE")]
    Coordination,
    #[error("OF1_RATE_CLOCK_ROLLBACK")]
    ClockRollback,
    #[error("OF1_RATE_WAIT_ORIGINAL_DEADLINE")]
    Deadline,
    #[error("OF1_RATE_CAPACITY_OR_COMPLETION_INVALID")]
    Capacity,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Admission {
    Ready,
    Wait { nanoseconds: u64 },
}

/// Fixed-policy token bucket with integer byte-nanosecond credit only. The caller
/// supplies a monotonic elapsed clock and the original, unchanged deadline.
pub struct EntityRate {
    last_ns: u64,
    credit: u128,
    outstanding: Option<u64>,
}

impl EntityRate {
    #[must_use]
    pub fn empty(now_ns: u64) -> Self {
        Self {
            last_ns: now_ns,
            credit: 0,
            outstanding: None,
        }
    }

    /// Charge a bounded plaintext read before dispatch. Waiting does not charge
    /// tokens or change the caller's deadline; an interrupted wait grants nothing.
    /// # Errors
    /// Rejects rollback, expired/insufficient original deadline, invalid capacity
    /// or a second read while the first read has not reported its actual bytes.
    pub fn admit(
        &mut self,
        bytes: u64,
        now_ns: u64,
        deadline_ns: u64,
    ) -> Result<Admission, RateError> {
        if bytes == 0 || bytes > BURST_BYTES || self.outstanding.is_some() {
            return Err(RateError::Capacity);
        }
        let elapsed = now_ns
            .checked_sub(self.last_ns)
            .ok_or(RateError::ClockRollback)?;
        if now_ns >= deadline_ns {
            return Err(RateError::Deadline);
        }
        self.credit = (self.credit + u128::from(elapsed) * u128::from(ENTITY_BYTES_PER_SECOND))
            .min(u128::from(BURST_BYTES) * NANOS_PER_SECOND);
        self.last_ns = now_ns;
        let cost = u128::from(bytes) * NANOS_PER_SECOND;
        if cost <= self.credit {
            self.credit -= cost;
            self.outstanding = Some(bytes);
            return Ok(Admission::Ready);
        }
        let wait = (cost - self.credit).div_ceil(u128::from(ENTITY_BYTES_PER_SECOND));
        let wait = u64::try_from(wait).map_err(|_| RateError::Capacity)?;
        if now_ns
            .checked_add(wait)
            .is_none_or(|ready| ready >= deadline_ns)
        {
            return Err(RateError::Deadline);
        }
        Ok(Admission::Wait { nanoseconds: wait })
    }

    /// Return only unused read capacity; short reads, EOF and errors are not
    /// silently counted as entity bytes. Actual bytes retain their original cost.
    /// # Errors
    /// Rejects an unreserved completion or more bytes than the granted read size.
    pub fn complete(&mut self, actual_bytes: u64, now_ns: u64) -> Result<(), RateError> {
        if now_ns < self.last_ns {
            return Err(RateError::ClockRollback);
        }
        let reserved = self.outstanding.ok_or(RateError::Capacity)?;
        let unused = reserved
            .checked_sub(actual_bytes)
            .ok_or(RateError::Capacity)?;
        self.credit += u128::from(unused) * NANOS_PER_SECOND;
        // No credit accrues while a read is outstanding: a delayed read could
        // otherwise return a full burst immediately before another full burst.
        // Losing that idle credit is conservative and bounds observed entity
        // reads, not merely the times at which their capacity was admitted.
        self.last_ns = now_ns;
        self.outstanding = None;
        Ok(())
    }
}

/// This guard is private to the official connector. The path cannot come from a
/// plan, environment variable, URL or CLI flag; it is shared by all cooperating
/// processes of this Linux user. Closing releases the lock; never unlink it.
#[cfg(any(feature = "network-of1", feature = "tls-fixture", test))]
pub(crate) struct OfficialDownloadGuard {
    _file: std::fs::File,
}

#[cfg(any(feature = "network-of1", feature = "tls-fixture", test))]
impl OfficialDownloadGuard {
    #[cfg(feature = "network-of1")]
    pub(crate) fn acquire() -> Result<Self, RateError> {
        use std::os::unix::fs::MetadataExt;
        let uid = std::fs::metadata("/proc/self")
            .map_err(|_| RateError::Coordination)?
            .uid();
        Self::acquire_path(
            &std::path::PathBuf::from(format!("/tmp/solana-quant-of1-download-{uid}.lock")),
            uid,
        )
    }

    fn acquire_path(path: &std::path::Path, uid: u32) -> Result<Self, RateError> {
        use std::os::unix::fs::MetadataExt;
        // Safe rustix O_NOFOLLOW plus inode comparison prevents two cooperative
        // actors from silently locking a link alias instead of the fixed inode.
        let fd = rustix::fs::open(
            path,
            rustix::fs::OFlags::RDWR
                | rustix::fs::OFlags::CREATE
                | rustix::fs::OFlags::NOFOLLOW
                | rustix::fs::OFlags::CLOEXEC,
            rustix::fs::Mode::RUSR | rustix::fs::Mode::WUSR,
        )
        .map_err(|_| RateError::Coordination)?;
        let file = std::fs::File::from(fd);
        let metadata = file.metadata().map_err(|_| RateError::Coordination)?;
        let path_metadata = std::fs::symlink_metadata(path).map_err(|_| RateError::Coordination)?;
        if !metadata.is_file()
            || metadata.uid() != uid
            || metadata.nlink() != 1
            || metadata.dev() != path_metadata.dev()
            || metadata.ino() != path_metadata.ino()
            || !path_metadata.is_file()
        {
            return Err(RateError::Coordination);
        }
        file.try_lock().map_err(|error| match error {
            std::fs::TryLockError::WouldBlock => RateError::Busy,
            std::fs::TryLockError::Error(_) => RateError::Coordination,
        })?;
        Ok(Self { _file: file })
    }
}

/// Offline lock regression only; no official transport accepts this object or
/// its isolated fixture path as an override of official coordination.
#[cfg(feature = "tls-fixture")]
pub struct FixtureDownloadGuard {
    _guard: OfficialDownloadGuard,
}

#[cfg(feature = "tls-fixture")]
impl FixtureDownloadGuard {
    /// Exercise the same exclusion primitive on a new local fixture path.
    /// # Errors
    /// Rejects an invalid path/inode or a lock already held by another actor.
    pub fn acquire(path: &std::path::Path) -> Result<Self, RateError> {
        use std::os::unix::fs::MetadataExt;
        let uid = std::fs::metadata("/proc/self")
            .map_err(|_| RateError::Coordination)?
            .uid();
        Ok(Self {
            _guard: OfficialDownloadGuard::acquire_path(path, uid)?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::MetadataExt;

    #[test]
    fn cooperating_runs_exclude_each_other_and_release_without_unlinking() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("shared.lock");
        let uid = std::fs::metadata("/proc/self").unwrap().uid();
        let first = OfficialDownloadGuard::acquire_path(&path, uid).unwrap();
        let inode = std::fs::metadata(&path).unwrap().ino();
        assert!(matches!(
            OfficialDownloadGuard::acquire_path(&path, uid),
            Err(RateError::Busy)
        ));
        drop(first);
        let second = OfficialDownloadGuard::acquire_path(&path, uid).unwrap();
        assert_eq!(std::fs::metadata(&path).unwrap().ino(), inode);
        drop(second);
        assert!(path.is_file());
    }

    #[test]
    fn link_alias_cannot_create_an_independent_coordination_lock() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("shared.lock");
        let alias = dir.path().join("alias.lock");
        let uid = std::fs::metadata("/proc/self").unwrap().uid();
        std::fs::write(&path, []).unwrap();
        std::os::unix::fs::symlink(&path, &alias).unwrap();
        assert!(matches!(
            OfficialDownloadGuard::acquire_path(&alias, uid),
            Err(RateError::Coordination)
        ));
    }
}
