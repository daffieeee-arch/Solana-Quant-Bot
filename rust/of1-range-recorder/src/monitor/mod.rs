//! Bounded operational observations, separate from immutable acquisition evidence.
//! Rust owns byte/selection/rate meanings. An optional local collector grants no
//! acquisition authority and cannot become a prerequisite for a request.

mod recorded;
#[cfg(feature = "monitor")]
mod relay;
#[cfg(test)]
mod tests;

pub use recorded::{RecordedRun, read_metadata, read_run, read_run_context};
#[cfg(feature = "monitor")]
pub use relay::{Monitor, Relay, run_relay, write_snapshot};

use crate::durable::acquisition::{Request, RequestKind};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    io,
    path::{Component, Path},
    time::{SystemTime, UNIX_EPOCH},
};

pub const SCHEMA: &str = "OF1_MONITOR_1";
pub const MAX_SNAPSHOT_BYTES: usize = 65_536;
pub const MAX_OPERATIONS: usize = 32;
pub const MAX_SAMPLES: usize = 64;
pub const MAX_ERRORS: usize = 16;
pub const MAX_ARTIFACTS: usize = 40;
pub const MAX_RUNS: usize = 16;
pub const SAMPLE_INTERVAL_MS: u64 = 200;
const JS_SAFE: u64 = 9_007_199_254_740_991;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub schema_version: String,
    pub id: String,
    pub session_id: String,
    pub sequence: u64,
    pub updated_at_ms: u64,
    pub kind: String,
    pub mode: String,
    pub stage: String,
    pub label: String,
    pub dataset_root: String,
    pub source: String,
    pub epoch: u64,
    pub selected_slots: Option<Slots>,
    pub started_at_ms: u64,
    pub completed_at_ms: Option<u64>,
    pub elapsed_ms: u64,
    pub selection: Selection,
    pub traffic: Traffic,
    pub storage: Storage,
    pub budgets: Budgets,
    pub operations: Vec<Operation>,
    pub integrity: Integrity,
    pub domain_counts: String,
    pub artifacts: Vec<Artifact>,
    pub errors: Vec<String>,
    pub dropped_samples: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Slots {
    pub start: u64,
    pub end_exclusive: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub operations_total: u64,
    pub operations_published: u64,
    pub planned_bytes: Option<u64>,
    pub received_selection_bytes: u64,
    pub published_bytes: u64,
    pub verified_bytes: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Traffic {
    pub received_bytes: u64,
    pub received_basis: String,
    pub reserved_bytes: u64,
    pub attempts: u64,
    pub retries: u64,
    pub speed_bps: Option<f64>,
    pub download_eta_ms: Option<u64>,
    pub eta_scope: Option<String>,
    pub speed_samples: Vec<SpeedSample>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpeedSample {
    pub elapsed_ms: u64,
    pub bps: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Storage {
    pub used_bytes: u64,
    pub available_bytes: u64,
    pub cap_bytes: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Budgets {
    pub attempts_remaining: u64,
    pub entity_bytes_remaining: u64,
    pub stage_attempts_remaining: u64,
    pub stage_entity_bytes_remaining: u64,
    pub runtime_remaining_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Operation {
    pub sequence: u64,
    pub method: String,
    pub path: String,
    pub range: Option<String>,
    pub state: String,
    pub expected_bytes: Option<u64>,
    pub received_bytes: Option<u64>,
    pub published_bytes: u64,
    pub attempts: u64,
    pub status_code: Option<u16>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Integrity {
    pub receipts: String,
    pub car: String,
    pub root_to_slot: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Artifact {
    pub id: String,
    pub label: String,
    pub path: String,
    pub sha256: String,
}

/// Expected entity lengths come from exact request semantics, never allowances
/// for variable-size SHA/CID sidecars and never a whole-epoch CAR HEAD length.
#[must_use]
pub fn operation(request: &Request, epoch: u64) -> Operation {
    Operation {
        sequence: request.sequence,
        method: request.method().into(),
        path: request.path(epoch),
        range: match &request.kind {
            RequestKind::CarRange {
                start,
                end_exclusive,
                ..
            } => Some(format!("bytes={start}-{}", end_exclusive - 1)),
            _ => None,
        },
        state: "PENDING".into(),
        expected_bytes: match request.kind {
            RequestKind::Index | RequestKind::CarHead | RequestKind::CarRange { .. } => {
                Some(request.allowance())
            }
            RequestKind::CarSha256 | RequestKind::CarCid => None,
        },
        received_bytes: Some(0),
        published_bytes: 0,
        attempts: 0,
        status_code: None,
        error: None,
    }
}

impl Snapshot {
    /// Recompute unique selection progress. A failed attempt is retained in
    /// traffic/operation diagnostics, but is not additional usable selection.
    pub fn refresh_selection(&mut self) {
        self.selection.operations_total = self.operations.len() as u64;
        self.selection.operations_published = self
            .operations
            .iter()
            .filter(|o| o.state == "PUBLISHED")
            .count() as u64;
        self.selection.planned_bytes = self
            .operations
            .iter()
            .try_fold(0u64, |n, o| n.checked_add(o.expected_bytes?));
        self.selection.published_bytes = self.operations.iter().map(|o| o.published_bytes).sum();
        self.selection.verified_bytes = self.selection.published_bytes;
        self.selection.received_selection_bytes = self
            .operations
            .iter()
            .map(|o| {
                if o.state == "PUBLISHED" {
                    o.published_bytes
                } else if o.state == "FAILED" {
                    0
                } else {
                    o.received_bytes.unwrap_or(0)
                }
            })
            .sum();
        self.traffic.retries = self
            .operations
            .iter()
            .map(|o| o.attempts.saturating_sub(1))
            .sum();
    }

    /// # Errors
    /// Refuses unsupported/oversized telemetry instead of letting it become an
    /// unbounded side channel. These are observation checks, not run admission.
    #[allow(clippy::too_many_lines)] // Keep the one bounded wire-contract audit together.
    pub fn validate(&self) -> io::Result<()> {
        if self.schema_version != SCHEMA
            || !crate::is_hash(&self.id)
            || self.session_id.is_empty()
            || self.session_id.len() > 96
            || !self
                .session_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-')
            || ![
                "AUTHENTIC_METADATA",
                "AUTHENTIC_PAYLOAD",
                "LOCAL_SIMULATION",
            ]
            .contains(&self.kind.as_str())
            || !["LIVE", "RECORDED"].contains(&self.mode.as_str())
            || ![
                "PREPARING",
                "METADATA",
                "DOWNLOADING",
                "VERIFYING",
                "PUBLISHING",
                "COMPLETE",
                "STOPPED",
            ]
            .contains(&self.stage.as_str())
            || self.label.len() > 256
            || self.dataset_root.len() > 4096
            || !Path::new(&self.dataset_root).is_absolute()
            || self.source
                != if self.kind == "LOCAL_SIMULATION" {
                    "LOCAL_LOOPBACK_TLS"
                } else {
                    crate::HOST
                }
            || self.operations.len() > MAX_OPERATIONS
            || self.operations.is_empty()
            || self.traffic.speed_samples.len() > MAX_SAMPLES
            || self
                .traffic
                .speed_bps
                .is_some_and(|v| !v.is_finite() || v < 0.0)
            || self
                .traffic
                .speed_samples
                .iter()
                .any(|s| !s.bps.is_finite() || s.bps < 0.0)
            || self.errors.len() > MAX_ERRORS
            || self.artifacts.len() > MAX_ARTIFACTS
            || self.integrity.root_to_slot != "UNAVAILABLE"
            || self.domain_counts != "UNAVAILABLE_NOT_DECODED_IN_B4"
            || !["PROCESS_OBSERVED", "DURABLE_LOWER_BOUND", "RECEIPTS_ONLY"]
                .contains(&self.traffic.received_basis.as_str())
            || self
                .traffic
                .eta_scope
                .as_deref()
                .is_some_and(|s| !["SELECTION", "CURRENT_OPERATION"].contains(&s))
            || self.errors.iter().any(|e| e.len() > 512)
            || self
                .selected_slots
                .as_ref()
                .is_some_and(|s| s.start >= s.end_exclusive)
        {
            return Err(invalid("unsupported or oversized monitor snapshot"));
        }
        let mut sequences = BTreeSet::new();
        for op in &self.operations {
            if !sequences.insert(op.sequence)
                || !["GET", "HEAD"].contains(&op.method.as_str())
                || op.path.len() > 2048
                || !op.path.starts_with('/')
                || op.range.as_ref().is_some_and(|r| r.len() > 128)
                || op.error.as_ref().is_some_and(|e| e.len() > 512)
                || ![
                    "PENDING",
                    "RESERVED",
                    "DOWNLOADING",
                    "VERIFYING",
                    "PUBLISHING",
                    "PUBLISHED",
                    "FAILED",
                ]
                .contains(&op.state.as_str())
            {
                return Err(invalid("invalid monitor operation"));
            }
        }
        let mut artifact_ids = BTreeSet::new();
        for artifact in &self.artifacts {
            if !artifact_ids.insert(&artifact.id)
                || artifact.id.len() > 64
                || artifact.id.is_empty()
                || !artifact
                    .id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-')
                || artifact.label.len() > 256
                || !crate::is_hash(&artifact.sha256)
                || !artifact_path(&artifact.path)
            {
                return Err(invalid("invalid monitor artifact"));
            }
        }
        let value = serde_json::to_value(self).map_err(invalid)?;
        safe_numbers(&value)?;
        if serde_json::to_vec(&value).map_err(invalid)?.len() > MAX_SNAPSHOT_BYTES {
            return Err(invalid("monitor snapshot exceeds datagram bound"));
        }
        Ok(())
    }
}

#[allow(clippy::cast_precision_loss)]
fn safe_numbers(value: &serde_json::Value) -> io::Result<()> {
    match value {
        serde_json::Value::Number(n)
            if n.as_f64()
                .is_none_or(|n| !n.is_finite() || n < 0.0 || n > JS_SAFE as f64) =>
        {
            Err(invalid("unsafe monitor number"))
        }
        serde_json::Value::Array(values) => values.iter().try_for_each(safe_numbers),
        serde_json::Value::Object(values) => values.values().try_for_each(safe_numbers),
        _ => Ok(()),
    }
}

fn artifact_path(path: &str) -> bool {
    if path.len() > 256
        || Path::new(path)
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return false;
    }
    if ["run.json", "payload.json"].contains(&path) {
        return true;
    }
    let parts = path.split('/').collect::<Vec<_>>();
    parts.len() == 3
        && parts[0] == "published"
        && parts[1].len() == 10
        && parts[1].bytes().all(|b| b.is_ascii_digit())
        && parts[2] == "receipt.json"
}

#[allow(clippy::needless_pass_by_value)] // Shared map_err adapter accepts heterogeneous owned errors.
pub(crate) fn invalid(message: impl ToString) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}
pub(crate) fn wall_ms() -> u64 {
    u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis(),
    )
    .unwrap_or(JS_SAFE)
}

/// Pure rolling measurements: no filesystem, network, clock or decoder access.
#[cfg(any(feature = "monitor", test))]
#[derive(Default)]
pub(crate) struct Rates {
    last_ms: u64,
    last_bytes: u64,
    measurements: u64,
}

#[cfg(any(feature = "monitor", test))]
impl Rates {
    pub(crate) fn new(bytes: u64) -> Self {
        Self {
            last_bytes: bytes,
            ..Self::default()
        }
    }

    #[allow(
        clippy::cast_precision_loss,
        clippy::cast_possible_truncation,
        clippy::cast_sign_loss
    )]
    pub(crate) fn observe(&mut self, snapshot: &mut Snapshot, process_ms: u64) {
        let interval = process_ms.saturating_sub(self.last_ms);
        if interval < SAMPLE_INTERVAL_MS {
            return;
        }
        let bytes = snapshot
            .traffic
            .received_bytes
            .saturating_sub(self.last_bytes);
        let bps = bytes as f64 * 1000.0 / interval as f64;
        self.last_ms = process_ms;
        self.last_bytes = snapshot.traffic.received_bytes;
        self.measurements += 1;
        snapshot.traffic.speed_samples.push(SpeedSample {
            elapsed_ms: snapshot.elapsed_ms,
            bps,
        });
        if snapshot.traffic.speed_samples.len() > MAX_SAMPLES {
            snapshot.traffic.speed_samples.remove(0);
        }
        snapshot.traffic.speed_bps = (snapshot.stage == "DOWNLOADING").then_some(bps);
        snapshot.traffic.download_eta_ms = None;
        snapshot.traffic.eta_scope = None;
        if snapshot.stage != "DOWNLOADING" || process_ms < 1000 || self.measurements < 3 {
            return;
        }
        let recent: Vec<_> = snapshot
            .traffic
            .speed_samples
            .iter()
            .rev()
            .take(5)
            .collect();
        let mean = recent.iter().map(|s| s.bps).sum::<f64>() / recent.len() as f64;
        let target = snapshot
            .selection
            .planned_bytes
            .map(|total| {
                (
                    total.saturating_sub(snapshot.selection.received_selection_bytes),
                    "SELECTION",
                )
            })
            .or_else(|| {
                snapshot
                    .operations
                    .iter()
                    .find(|op| op.state == "DOWNLOADING")
                    .and_then(|op| {
                        Some((
                            op.expected_bytes?.saturating_sub(op.received_bytes?),
                            "CURRENT_OPERATION",
                        ))
                    })
            });
        if let Some((remaining, scope)) = target {
            let eta = (remaining as f64 / mean * 1000.0).ceil();
            if mean > 0.0 && eta.is_finite() && eta <= JS_SAFE as f64 {
                snapshot.traffic.download_eta_ms = Some(eta as u64);
                snapshot.traffic.eta_scope = Some(scope.into());
            }
        }
    }
}
