//! Read-only forensic projection. Never opens writer.lock, resumes a lease, checks
//! the current binary against an old executable, repairs, or writes a run file.

use super::{
    Artifact, Budgets, Integrity, Operation, SCHEMA, Selection, Slots, Snapshot, Storage, Traffic,
    invalid, operation, wall_ms,
};
use crate::{
    FormatSource, HOST,
    acquisition::read_limited,
    durable::{
        ClockSample,
        acquisition::{
            AggregateBudget, AggregatePlan, Authority, MetadataLease, PayloadLease,
            PreparedPayload, Published, Receipt, Request, StageBudget, metadata_proposal_sha256,
            metadata_requests, payload_proposal_sha256, resource_sample,
        },
    },
    sha256,
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{self, Read},
    path::{Path, PathBuf},
};

const JSON_LIMIT: u64 = 1_048_576;
const ATTEMPT_LIMIT: usize = 512;
const TREE_LIMIT: usize = 8192;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Stage {
    authority: Authority,
    budget: StageBudget,
    lease_sha256: String,
    started_at: ClockSample,
    deadline_wall_ms: u64,
    deadline_boot_ms: u64,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    run_id: String,
    aggregate_sha256: String,
    plan: AggregatePlan,
    metadata_lease: MetadataLease,
    metadata_stage: Stage,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Payload {
    lease: PayloadLease,
    stage: Stage,
    prepared: PreparedPayload,
    metadata_attempt_count: u64,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct RecordedAttempt {
    run_id: String,
    aggregate_sha256: String,
    lease_sha256: String,
    attempt_id: u64,
    request: Request,
    reserved_entity_bytes: u64,
    at: ClockSample,
}

/// Audited observational counters only; this context is not acquisition authority.
pub struct RecordedRun {
    pub snapshot: Snapshot,
    pub aggregate_budget: AggregateBudget,
    pub stage_budget: StageBudget,
    pub stage_attempts_used: u64,
    pub stage_reserved_bytes: u64,
    /// Historical inputs, not permission to resume the old writer.
    pub aggregate_plan: AggregatePlan,
    pub prepared: Option<PreparedPayload>,
    pub published: Vec<Published>,
}

/// # Errors
/// A missing, partial, substituted or corrupt immutable publication fails closed.
pub fn read_metadata(root: &Path) -> io::Result<Snapshot> {
    read_run(root)
}

/// # Errors
/// Refuses unauditable records; it never invents historical live measurements.
pub fn read_run(root: &Path) -> io::Result<Snapshot> {
    Ok(read_run_context(root)?.snapshot)
}

fn decode<T: DeserializeOwned>(path: &Path) -> io::Result<(T, Vec<u8>)> {
    let bytes = read_limited(path, JSON_LIMIT).map_err(invalid)?;
    Ok((serde_json::from_slice(&bytes).map_err(invalid)?, bytes))
}

fn exact_hash(value: &impl Serialize) -> io::Result<String> {
    Ok(sha256(&serde_json::to_vec(value).map_err(invalid)?))
}

fn check_stage(
    stage: &Stage,
    authority: &Authority,
    budget: &StageBudget,
    lease: &impl Serialize,
) -> io::Result<()> {
    let runtime = match authority {
        Authority::Fixture => budget.max_runtime_ms,
        Authority::Approved {
            approved_at_ms,
            not_after_ms,
            ..
        } => {
            if stage.started_at.wall_ms < *approved_at_ms
                || stage.started_at.wall_ms >= *not_after_ms
            {
                return Err(invalid("recorded stage was outside its approval interval"));
            }
            budget
                .max_runtime_ms
                .min(not_after_ms - stage.started_at.wall_ms)
        }
    };
    if &stage.authority != authority
        || &stage.budget != budget
        || stage.lease_sha256 != exact_hash(lease)?
        || stage.started_at.wall_ms.checked_add(runtime) != Some(stage.deadline_wall_ms)
        || stage.started_at.boot_ms.checked_add(runtime) != Some(stage.deadline_boot_ms)
        || stage.started_at.boot_id.is_empty()
    {
        return Err(invalid("recorded stage/lease identity mismatch"));
    }
    Ok(())
}

fn clock_within(at: &ClockSample, stage: &Stage) -> bool {
    at.boot_id == stage.started_at.boot_id
        && at.wall_ms >= stage.started_at.wall_ms
        && at.boot_ms >= stage.started_at.boot_ms
        && at.wall_ms <= stage.deadline_wall_ms
        && at.boot_ms <= stage.deadline_boot_ms
}

fn directory(path: &Path, limit: usize) -> io::Result<Vec<PathBuf>> {
    if !fs::symlink_metadata(path)?.is_dir() {
        return Err(invalid("monitor requires ordinary directories"));
    }
    let mut entries = Vec::new();
    for entry in fs::read_dir(path)? {
        if entries.len() == limit {
            return Err(invalid("monitor directory limit exceeded"));
        }
        entries.push(entry?.path());
    }
    entries.sort();
    Ok(entries)
}

fn bound_tree(root: &Path, remaining: &mut usize) -> io::Result<()> {
    if *remaining == 0 {
        return Err(invalid("monitor tree limit exceeded"));
    }
    *remaining -= 1;
    let metadata = fs::symlink_metadata(root)?;
    if metadata.is_file() {
        return Ok(());
    }
    if !metadata.is_dir() {
        return Err(invalid("nonregular entry in recorded run"));
    }
    for path in directory(root, *remaining)? {
        bound_tree(&path, remaining)?;
    }
    Ok(())
}

fn raw_hash(path: &Path, expected_bytes: u64) -> io::Result<String> {
    let descriptor = rustix::fs::open(
        path,
        rustix::fs::OFlags::RDONLY
            | rustix::fs::OFlags::CLOEXEC
            | rustix::fs::OFlags::NOFOLLOW
            | rustix::fs::OFlags::NONBLOCK,
        rustix::fs::Mode::empty(),
    )
    .map_err(io::Error::from)?;
    let mut file = File::from(descriptor);
    if !file.metadata()?.is_file() || file.metadata()?.len() != expected_bytes {
        return Err(invalid("recorded Raw length/type mismatch"));
    }
    let mut digest = Sha256::new();
    let mut remaining = expected_bytes;
    let mut buffer = vec![0u8; 65_536];
    while remaining > 0 {
        let limit = usize::try_from(remaining.min(buffer.len() as u64)).map_err(invalid)?;
        let count = file.read(&mut buffer[..limit])?;
        if count == 0 {
            return Err(invalid("short recorded Raw read"));
        }
        digest.update(&buffer[..count]);
        remaining -= count as u64;
    }
    if file.read(&mut buffer[..1])? != 0 {
        return Err(invalid("recorded Raw grew during read"));
    }
    Ok(hex::encode(digest.finalize()))
}

fn artifact(id: &str, label: &str, path: String, bytes: &[u8]) -> Artifact {
    Artifact {
        id: id.into(),
        label: label.into(),
        path,
        sha256: sha256(bytes),
    }
}

/// # Errors
/// Bounded immutable identities, byte hashes, reservations, and source paths must
/// agree. Historical elapsed time is initialization-to-last-receipt, not a
/// fabricated process runtime; interrupted traffic outside receipts is unknown.
#[allow(clippy::too_many_lines)]
pub fn read_run_context(root: &Path) -> io::Result<RecordedRun> {
    let root = crate::dataset_location::validate_dataset_location(root)?;
    let mut remaining_entries = TREE_LIMIT;
    bound_tree(&root, &mut remaining_entries)?;
    let (manifest, manifest_bytes): (Manifest, _) = decode(&root.join("run.json"))?;
    if manifest.schema != "OF1_ACQUISITION_STORE_1"
        || !crate::is_hash(&manifest.run_id)
        || manifest.plan.schema != "OF1_ACQUISITION_AGGREGATE_1"
        || manifest.plan.format_source != FormatSource::pinned()
        || manifest.aggregate_sha256 != exact_hash(&manifest.plan)?
        || manifest.metadata_lease.schema != "OF1_METADATA_LEASE_1"
    {
        return Err(invalid("recorded manifest/aggregate mismatch"));
    }
    check_stage(
        &manifest.metadata_stage,
        &manifest.metadata_lease.authority,
        &manifest.metadata_lease.budget,
        &manifest.metadata_lease,
    )?;
    if let Some(sample) = &manifest.plan.sample_identity {
        sample.validate(manifest.plan.epoch).map_err(invalid)?;
    }
    if let Authority::Approved {
        approved_plan_sha256,
        ..
    } = &manifest.metadata_lease.authority
        && *approved_plan_sha256
            != metadata_proposal_sha256(&manifest.plan, &manifest.metadata_lease.budget)
                .map_err(invalid)?
    {
        return Err(invalid("recorded metadata approval mismatch"));
    }
    let mut artifacts = vec![artifact(
        "run-manifest",
        "Immutable run manifest",
        "run.json".into(),
        &manifest_bytes,
    )];
    let payload: Option<Payload> = if root.join("payload.json").try_exists()? {
        let (payload, bytes): (Payload, _) = decode(&root.join("payload.json"))?;
        check_stage(
            &payload.stage,
            &payload.lease.authority,
            &payload.lease.budget,
            &payload.lease,
        )?;
        if payload.lease.schema != "OF1_PAYLOAD_LEASE_1"
            || matches!(payload.lease.authority, Authority::Fixture)
                != matches!(manifest.metadata_lease.authority, Authority::Fixture)
            || payload.lease.prepared_payload_sha256
                != payload.prepared.sha256().map_err(invalid)?
            || payload.lease.metadata_receipt_sha256 != payload.prepared.metadata_receipt_sha256()
        {
            return Err(invalid("recorded payload identity mismatch"));
        }
        if let Some(sample) = &manifest.plan.sample_identity {
            sample
                .validate_range(
                    manifest.plan.epoch,
                    payload.prepared.start_slot(),
                    payload.prepared.end_slot(),
                )
                .map_err(invalid)?;
        }
        if let Authority::Approved {
            approved_plan_sha256,
            ..
        } = &payload.lease.authority
            && *approved_plan_sha256
                != payload_proposal_sha256(&manifest.plan, &payload.lease.budget, &payload.prepared)
                    .map_err(invalid)?
        {
            return Err(invalid("recorded payload approval mismatch"));
        }
        artifacts.push(artifact(
            "payload-manifest",
            "Immutable payload admission",
            "payload.json".into(),
            &bytes,
        ));
        Some(payload)
    } else {
        None
    };
    let mut requests = metadata_requests();
    if let Some(payload) = &payload {
        requests.extend_from_slice(payload.prepared.requests());
    }
    if requests.len() > super::MAX_OPERATIONS {
        return Err(invalid("monitor selected operation limit"));
    }
    let request_map: BTreeMap<_, _> = requests.iter().map(|r| (r.sequence, r)).collect();
    if request_map.len() != requests.len() {
        return Err(invalid("duplicate planned operation"));
    }
    let stage = payload
        .as_ref()
        .map_or(&manifest.metadata_stage, |p| &p.stage);
    let stage_for = |request: &Request| -> io::Result<&Stage> {
        if request.sequence < 4 {
            Ok(&manifest.metadata_stage)
        } else {
            payload
                .as_ref()
                .map(|p| &p.stage)
                .ok_or_else(|| invalid("unadmitted payload attempt"))
        }
    };
    let mut operations: Vec<Operation> = requests
        .iter()
        .map(|r| operation(r, manifest.plan.epoch))
        .collect();
    let mut attempts = Vec::<RecordedAttempt>::new();
    let mut reserved = 0u64;
    let mut stage_reserved = 0u64;
    let mut stage_attempts = 0u64;
    for path in directory(&root.join("attempts"), ATTEMPT_LIMIT)? {
        let (attempt, _): (RecordedAttempt, _) = decode(&path)?;
        let request = request_map
            .get(&attempt.request.sequence)
            .ok_or_else(|| invalid("unplanned recorded attempt"))?;
        let attempt_stage = stage_for(request)?;
        if path.file_name().and_then(|p| p.to_str())
            != Some(&format!("{:010}.json", attempts.len()))
            || attempt.attempt_id != attempts.len() as u64
            || attempt.run_id != manifest.run_id
            || attempt.aggregate_sha256 != manifest.aggregate_sha256
            || attempt.lease_sha256 != attempt_stage.lease_sha256
            || &attempt.request != *request
            || attempt.reserved_entity_bytes != request.allowance()
            || !clock_within(&attempt.at, attempt_stage)
        {
            return Err(invalid("recorded reservation identity mismatch"));
        }
        reserved = reserved
            .checked_add(attempt.reserved_entity_bytes)
            .ok_or_else(|| invalid("reservation overflow"))?;
        if attempt.lease_sha256 == stage.lease_sha256 {
            stage_attempts += 1;
            stage_reserved += attempt.reserved_entity_bytes;
        }
        let op = operations
            .iter_mut()
            .find(|o| o.sequence == request.sequence)
            .ok_or_else(|| invalid("missing operation"))?;
        op.attempts += 1;
        if op.attempts > u64::from(manifest.plan.budget.request_retries) + 1 {
            return Err(invalid("recorded retry bound exceeded"));
        }
        op.state = "FAILED".into();
        op.received_bytes = None;
        op.error = Some(
            "Recorded attempt has no published receipt; received bytes/reason unavailable".into(),
        );
        attempts.push(attempt);
    }
    if attempts.len() as u64 > manifest.plan.budget.max_requests
        || reserved > manifest.plan.budget.max_total_response_entity_bytes
        || stage_attempts > stage.budget.max_requests
        || stage_reserved > stage.budget.max_response_entity_bytes_total
        || payload.as_ref().is_some_and(|p| {
            p.metadata_attempt_count
                != attempts.iter().filter(|a| a.request.sequence < 4).count() as u64
        })
    {
        return Err(invalid("recorded budget accounting mismatch"));
    }
    let mut last_at = manifest.metadata_stage.started_at.wall_ms;
    let mut published_receipts = BTreeMap::new();
    for path in directory(&root.join("published"), super::MAX_OPERATIONS)? {
        if !fs::symlink_metadata(&path)?.is_dir() {
            return Err(invalid("publication is not an ordinary directory"));
        }
        let names = directory(&path, 2)?;
        if names.len() != 2
            || names[0].file_name().and_then(|s| s.to_str()) != Some("raw.bin")
            || names[1].file_name().and_then(|s| s.to_str()) != Some("receipt.json")
        {
            return Err(invalid("partial publication pair"));
        }
        let (receipt, bytes): (Receipt, _) = decode(&path.join("receipt.json"))?;
        let request = request_map
            .get(&receipt.request.sequence)
            .ok_or_else(|| invalid("unplanned receipt"))?;
        let reservation = attempts
            .get(usize::try_from(receipt.attempt_id).map_err(invalid)?)
            .ok_or_else(|| invalid("unreserved receipt"))?;
        let expected_evidence = if matches!(stage_for(request)?.authority, Authority::Fixture) {
            "Fixture"
        } else {
            "UNREVIEWED_AUTHENTIC_RAW"
        };
        if path.file_name().and_then(|p| p.to_str()) != Some(&format!("{:010}", request.sequence))
            || receipt.schema != "OF1_ACQUISITION_RECEIPT_1"
            || receipt.run_id != manifest.run_id
            || receipt.aggregate_sha256 != manifest.aggregate_sha256
            || receipt.lease_sha256 != reservation.lease_sha256
            || &receipt.request != *request
            || receipt.request != reservation.request
            || attempts
                .iter()
                .rev()
                .find(|a| a.request == receipt.request)
                .map(|a| a.attempt_id)
                != Some(receipt.attempt_id)
            || receipt.source_host != HOST
            || receipt.source_path != request.path(manifest.plan.epoch)
            || receipt.response_headers_sha256 != sha256(&receipt.response.raw_headers)
            || crate::acquisition_http::parse_response_head(&receipt.response.raw_headers, request)
                .map_err(invalid)?
                != receipt.response
            || request.entity_length(&receipt.response).map_err(invalid)?
                != receipt.response_entity_bytes
            || receipt.response_entity_bytes > manifest.plan.budget.max_response_entity_bytes
            || receipt.sha256 != raw_hash(&path.join("raw.bin"), receipt.response_entity_bytes)?
            || !clock_within(&receipt.acquired_at, stage_for(request)?)
            || receipt.acquired_at.wall_ms < reservation.at.wall_ms
            || receipt.acquired_at.boot_ms < reservation.at.boot_ms
            || receipt.evidence != expected_evidence
            || receipt.domain_counts != "UNAVAILABLE_NOT_DECODED_IN_B4"
        {
            return Err(invalid("recorded Raw/receipt identity or hash mismatch"));
        }
        let op = operations
            .iter_mut()
            .find(|o| o.sequence == request.sequence)
            .ok_or_else(|| invalid("missing operation"))?;
        op.state = "PUBLISHED".into();
        op.error = None;
        op.expected_bytes = Some(receipt.response_entity_bytes);
        op.received_bytes = Some(receipt.response_entity_bytes);
        op.published_bytes = receipt.response_entity_bytes;
        op.status_code = Some(receipt.response.status);
        last_at = last_at.max(receipt.acquired_at.wall_ms);
        artifacts.push(artifact(
            &format!("receipt-{}", request.sequence),
            &format!("Operation {} receipt", request.sequence),
            format!("published/{:010}/receipt.json", request.sequence),
            &bytes,
        ));
        if published_receipts
            .insert(request.sequence, receipt)
            .is_some()
        {
            return Err(invalid("duplicate publication"));
        }
    }
    if let Some(payload) = &payload {
        let receipts = (0..4)
            .map(|i| {
                published_receipts
                    .get(&i)
                    .ok_or_else(|| invalid("payload missing metadata receipt"))
            })
            .collect::<io::Result<Vec<_>>>()?;
        if exact_hash(&receipts)? != payload.prepared.metadata_receipt_sha256() {
            return Err(invalid("payload metadata receipt binding mismatch"));
        }
    }
    let fixture = matches!(manifest.metadata_lease.authority, Authority::Fixture);
    let complete = operations.iter().all(|o| o.state == "PUBLISHED");
    let published_bytes = operations.iter().map(|o| o.published_bytes).sum();
    let available = resource_sample(&root).map_err(invalid)?.0;
    let mut snapshot = Snapshot {
        schema_version: SCHEMA.into(),
        id: manifest.run_id.clone(),
        session_id: format!("recorded-{}", wall_ms()),
        sequence: 0,
        updated_at_ms: wall_ms(),
        kind: if fixture {
            "LOCAL_SIMULATION"
        } else if payload.is_some() {
            "AUTHENTIC_PAYLOAD"
        } else {
            "AUTHENTIC_METADATA"
        }
        .into(),
        mode: "RECORDED".into(),
        stage: if complete { "COMPLETE" } else { "STOPPED" }.into(),
        label: format!(
            "{} · epoch {}",
            if fixture {
                "Local fixture simulation"
            } else {
                "Recorded authentic acquisition"
            },
            manifest.plan.epoch
        ),
        dataset_root: root
            .to_str()
            .ok_or_else(|| invalid("non-UTF8 dataset path"))?
            .into(),
        source: if fixture { "LOCAL_LOOPBACK_TLS" } else { HOST }.into(),
        epoch: manifest.plan.epoch,
        selected_slots: payload.as_ref().map(|p| Slots {
            start: p.prepared.start_slot(),
            end_exclusive: p.prepared.end_slot(),
        }),
        started_at_ms: manifest.metadata_stage.started_at.wall_ms,
        completed_at_ms: complete.then_some(last_at),
        elapsed_ms: last_at.saturating_sub(manifest.metadata_stage.started_at.wall_ms),
        selection: Selection {
            operations_total: 0,
            operations_published: 0,
            planned_bytes: None,
            received_selection_bytes: 0,
            published_bytes: 0,
            verified_bytes: 0,
        },
        traffic: Traffic {
            received_bytes: published_bytes,
            received_basis: "RECEIPTS_ONLY".into(),
            reserved_bytes: reserved,
            attempts: attempts.len() as u64,
            retries: 0,
            speed_bps: None,
            download_eta_ms: None,
            eta_scope: None,
            speed_samples: Vec::new(),
        },
        rate_limit: manifest
            .plan
            .download_rate
            .clone()
            .map(super::RateLimit::recorded),
        storage: Storage {
            used_bytes: crate::durable::disk_charge(&root).map_err(invalid)?,
            available_bytes: available,
            cap_bytes: manifest.plan.budget.max_disk_bytes,
        },
        budgets: Budgets {
            attempts_remaining: manifest.plan.budget.max_requests - attempts.len() as u64,
            entity_bytes_remaining: manifest.plan.budget.max_total_response_entity_bytes - reserved,
            stage_attempts_remaining: stage.budget.max_requests - stage_attempts,
            stage_entity_bytes_remaining: stage.budget.max_response_entity_bytes_total
                - stage_reserved,
            runtime_remaining_ms: stage.deadline_wall_ms.saturating_sub(wall_ms()),
        },
        operations,
        integrity: Integrity {
            receipts: if complete {
                "VERIFIED"
            } else {
                "PARTIAL_PUBLISHED_ONLY"
            }
            .into(),
            car: if payload.is_some() {
                "UNAVAILABLE_NOT_CHECKED_BY_MONITOR"
            } else {
                "NOT_ACQUIRED"
            }
            .into(),
            root_to_slot: "UNAVAILABLE".into(),
        },
        domain_counts: "UNAVAILABLE_NOT_DECODED_IN_B4".into(),
        artifacts,
        errors: if complete {
            Vec::new()
        } else {
            vec!["RECORDED_INCOMPLETE: live status and unreceipted traffic unavailable".into()]
        },
        dropped_samples: 0,
    };
    snapshot.refresh_selection();
    snapshot.validate()?;
    Ok(RecordedRun {
        snapshot,
        aggregate_budget: manifest.plan.budget.clone(),
        stage_budget: stage.budget.clone(),
        stage_attempts_used: stage_attempts,
        stage_reserved_bytes: stage_reserved,
        aggregate_plan: manifest.plan,
        prepared: payload.map(|p| p.prepared),
        published: published_receipts
            .into_values()
            .map(|receipt| Published {
                raw_path: root.join(format!(
                    "published/{:010}/raw.bin",
                    receipt.request.sequence
                )),
                receipt,
            })
            .collect(),
    })
}
