//! Explicit stage commands. Merely building this binary never dispatches a request.
use of1_range_recorder::{
    FormatSource,
    acquisition::{derive_payload_from_metadata, read_limited, verify_payload},
    car::VerificationLimits,
    durable::{
        SystemClock,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, PayloadLease, PreparedPayload, StageBudget, current_executable_sha256,
            metadata_proposal_sha256, payload_proposal_sha256,
        },
    },
};
use serde::{Serialize, de::DeserializeOwned};
use std::{error::Error, path::Path};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn read<T: DeserializeOwned>(path: &str) -> Result<T> {
    Ok(serde_json::from_slice(&read_limited(
        Path::new(path),
        1_048_576,
    )?)?)
}

fn print(value: &impl Serialize) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

fn open(root: &str, plan: &str, lease_hash: &str) -> Result<AcquisitionStore<SystemClock>> {
    Ok(AcquisitionStore::resume(
        Path::new(root),
        &read(plan)?,
        lease_hash,
        SystemClock,
    )?)
}

fn outside_git(root: &Path) -> Result<()> {
    let parent = root
        .parent()
        .ok_or("dataset root needs an existing parent")?
        .canonicalize()?;
    if parent.ancestors().any(|p| p.join(".git").exists()) {
        return Err("dataset root must be outside Git".into());
    }
    Ok(())
}

fn main() {
    if let Err(error) = run(&std::env::args().skip(1).collect::<Vec<_>>()) {
        // Bounded errors only: never dump headers, environment, credentials or full inputs.
        eprintln!("OF1_STOP: {error}");
        std::process::exit(1);
    }
}

#[allow(clippy::too_many_lines)]
fn run(args: &[String]) -> Result<()> {
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["metadata-proposal", code_sha, toolchain_fingerprint] => {
            for (value, length) in [(*code_sha, 40), (*toolchain_fingerprint, 64)] {
                if value.len() != length || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
                    return Err("code/toolchain identity must be exact lowercase hexadecimal".into());
                }
            }
            let aggregate = AggregatePlan {
                schema: AGGREGATE_SCHEMA.into(), epoch: 978,
                format_source: FormatSource::pinned(), code_sha: (*code_sha).into(),
                toolchain_fingerprint: (*toolchain_fingerprint).into(),
                executable_sha256: current_executable_sha256()?,
                budget: AggregateBudget {
                    max_slots: 128, max_plan_bytes: 1_048_576, max_requests: 16,
                    max_response_entity_bytes: 16_777_216,
                    max_total_response_entity_bytes: 134_217_728,
                    max_disk_bytes: 268_435_456, required_free_disk_bytes: 536_870_912,
                    max_memory_bytes: 536_870_912, max_runtime_ms: 1_800_000,
                    response_timeout_ms: 30_000, request_retries: 2,
                },
            };
            let metadata_budget = StageBudget {
                max_requests: 12, max_response_entity_bytes_total: 15_576_576,
                max_runtime_ms: 600_000,
            };
            print(&serde_json::json!({
                "schema":"OF1_METADATA_RUN_PROPOSAL_1", "approved":false,
                "networkEnabled":false, "readyToRun":false,
                "slice_class":"ENGINEERING_VALIDATION_ONLY",
                "aggregate":aggregate, "metadata_budget":metadata_budget,
                "approval_target_sha256":metadata_proposal_sha256(&aggregate, &metadata_budget)?,
                "required_next_action":"Review exact plan/code/toolchain, current cost and availability; obtain metadata-only GO. No payload authorization."
            }))
        }
        ["metadata-init", root, plan, lease] => {
            outside_git(Path::new(root))?;
            let plan: AggregatePlan = read(plan)?;
            let lease: MetadataLease = read(lease)?;
            if !matches!(lease.authority, Authority::Approved { .. }) {
                return Err("metadata-init requires a separately approved immutable metadata lease".into());
            }
            print(&AcquisitionStore::create(Path::new(root), plan, lease, SystemClock)?.progress()?)
        }
        ["progress", root, plan, lease_hash] => print(&open(root, plan, lease_hash)?.progress()?),
        ["prepare-payload", root, plan, lease_hash, first, end] => {
            let aggregate: AggregatePlan = read(plan)?;
            let store = open(root, plan, lease_hash)?;
            let metadata = (0..4).map(|sequence| {
                store.published(sequence)?.ok_or(of1_range_recorder::durable::StoreError::Corrupt)
            }).collect::<std::result::Result<Vec<_>, _>>()?;
            let prepared = derive_payload_from_metadata(&aggregate, &metadata, first.parse()?, end.parse()?)?;
            print(&prepared)
        }
        ["payload-admit", root, plan, current_lease_hash, lease, prepared] => {
            let mut store = open(root, plan, current_lease_hash)?;
            let lease: PayloadLease = read(lease)?;
            if !matches!(lease.authority, Authority::Approved { .. }) {
                return Err("payload-admit requires a separate payload GO".into());
            }
            store.admit_payload(lease, &read::<PreparedPayload>(prepared)?)?;
            print(&store.progress()?)
        }
        ["payload-proposal", root, plan, lease_hash, prepared, budget] => {
            let store = open(root, plan, lease_hash)?;
            let prepared: PreparedPayload = read(prepared)?;
            let budget: StageBudget = read(budget)?;
            print(&serde_json::json!({
                "schema":"OF1_PAYLOAD_RUN_PROPOSAL_1", "approved":false,
                "prepared_payload_sha256":prepared.sha256()?,
                "metadata_receipt_sha256":prepared.metadata_receipt_sha256(),
                "budget":budget,
                "approval_target_sha256":payload_proposal_sha256(store.aggregate_plan(), &budget, &prepared)?,
                "progress":store.progress()?,
                "next":"Separate payload GO after exact rederivation and aggregate feasibility review. This hash grants no authority."
            }))
        }
        ["verify-payload", root, plan, lease_hash, prepared] => {
            let aggregate: AggregatePlan = read(plan)?;
            let store = open(root, plan, lease_hash)?;
            // Explicit bounded first-slice limits, not universal OF1 support.
            print(&verify_payload(&store, &read(prepared)?, VerificationLimits {
                max_total_bytes: usize::try_from(aggregate.budget.max_total_response_entity_bytes)?,
                    // A section may cross HTTP chunk boundaries; the aggregate
                    // assembly bound, not the individual response cap, bounds it.
                    max_section_bytes: usize::try_from(aggregate.budget.max_total_response_entity_bytes)?,
                max_nodes: 4096, max_links: 16_384,
            })?)
        }
        ["capture-stage", root, plan, lease_hash] => capture_stage(root, plan, lease_hash),
        _ => Err(concat!(
            "usage: of1-acquire metadata-proposal CODE_SHA TOOLCHAIN_SHA256 | ",
            "metadata-init ROOT AGGREGATE_JSON METADATA_LEASE_JSON | ",
            "progress ROOT AGGREGATE_JSON LEASE_SHA256 | ",
            "capture-stage ROOT AGGREGATE_JSON LEASE_SHA256 | ",
            "prepare-payload ROOT AGGREGATE_JSON LEASE_SHA256 FIRST_SLOT END_SLOT | ",
            "payload-admit ROOT AGGREGATE_JSON CURRENT_LEASE_SHA256 PAYLOAD_LEASE_JSON PREPARED_JSON | ",
            "payload-proposal ROOT AGGREGATE_JSON LEASE_SHA256 PREPARED_JSON STAGE_BUDGET_JSON | ",
            "verify-payload ROOT AGGREGATE_JSON LEASE_SHA256 PREPARED_JSON; ",
            "capture-stage is default-disabled and requires separately approved stage GO"
        ).into()),
    }
}

#[cfg(not(feature = "network-of1"))]
fn capture_stage(_: &str, _: &str, _: &str) -> Result<()> {
    Err("network-of1 capability disabled; no store mutation or connection attempted".into())
}

#[cfg(feature = "network-of1")]
fn capture_stage(root: &str, plan: &str, lease_hash: &str) -> Result<()> {
    let mut store = open(root, plan, lease_hash)?;
    let sequences: Vec<_> = if let Some(prepared) = store.prepared_payload() {
        prepared.requests().iter().map(|r| r.sequence).collect()
    } else {
        (0..4).collect()
    };
    for sequence in sequences {
        if store.published(sequence)?.is_some() {
            continue;
        }
        // Exactly one attempt. An error stops this command; a later explicit invocation
        // resumes with spent budgets and original deadlines, never a hidden retry/refund.
        if let Err(error) = of1_range_recorder::https::OfficialHttps::capture(&mut store, sequence)
        {
            print(
                &serde_json::json!({"stage_capture":"STOPPED", "reason":error.to_string(), "progress":store.progress().ok(), "domain_counts":"UNAVAILABLE_NOT_DECODED_IN_B4", "edge_evaluation":"NOT_EVALUATED_ENGINEERING_SLICE"}),
            )?;
            return Err(error.into());
        }
        print(&store.progress()?)?;
    }
    print(
        &serde_json::json!({"stage_capture":"COMPLETE", "next":"STOP_FOR_REVIEW_NO_AUTOMATIC_NEXT_STAGE", "progress":store.progress()?}),
    )
}
