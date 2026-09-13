//! Explicit stage commands. Merely building this binary never dispatches a request.
use of1_range_recorder::{
    FormatSource,
    acquisition::{derive_payload_from_metadata, read_limited, verify_payload},
    car::VerificationLimits,
    clock_contract::{ClockPolicy, check_follows},
    dataset_location::validate_dataset_location,
    durable::{
        Clock, SystemClock,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, PayloadLease, PreparedPayload, StageBudget, current_executable_sha256,
            metadata_proposal_sha256, metadata_requests, payload_proposal_sha256,
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

// Same policy and unmodified clock sources as the recorder. This observation
// grants no authority, creates no root/lease and cannot reset an approval T0.
fn clock_preflight(plan: &AggregatePlan) -> Result<serde_json::Value> {
    let policy = plan
        .clock_policy
        .as_ref()
        .ok_or("new clock policy required")?;
    policy.validate()?;
    let first = SystemClock.sample()?;
    let mut previous = first.clone();
    let started = std::time::Instant::now();
    let mut samples = 1u64;
    let mut utc_corrections = Vec::new();
    let mut backwards_utc_samples = 0u64;
    let mut violation = None;
    while started.elapsed() < std::time::Duration::from_secs(5) {
        std::thread::sleep(std::time::Duration::from_millis(1));
        let current = SystemClock.sample()?;
        if let Err(error) = check_follows(Some(policy), &previous, &current) {
            violation = Some(
                serde_json::json!({"reason":error.to_string(),"before":previous,"after":current}),
            );
            previous = current;
            samples += 1;
            break;
        }
        if current.wall_ms < previous.wall_ms {
            backwards_utc_samples += 1;
            if utc_corrections.len() < 16 {
                utc_corrections.push(serde_json::json!({"before":previous,"after":current}));
            }
        }
        samples += 1;
        previous = current;
    }
    Ok(serde_json::json!({
        "schema":"OF1_CLOCK_PREFLIGHT_1", "policy":policy,
        "first":first,"last":previous,"samples":samples,
        "elapsed_observation_ms":u64::try_from(started.elapsed().as_millis())?,
        "backwards_utc_samples":backwards_utc_samples,"utc_correction_examples":utc_corrections,
        "status":if violation.is_some(){"STOP_CLOCK_CONTRACT_VIOLATION"}else{"SAME_BOOT_NONDECREASING_ELAPSED_CLOCK"},
        "violation":violation,
        "utc_role":"UNMODIFIED_OPERATIONAL_PROVENANCE_NOT_ELAPSED_AUTHORITY",
        "read_only":true,"approved":false,"lease_created":false,"networkEnabled":false,
        "limitation":"Bounded present observation, not a guarantee of future clock stability or approval."
    }))
}

fn metadata_budget(fixed_pilot: bool) -> StageBudget {
    StageBudget {
        // New proposal leaves nine attempts within the same sixteen-attempt aggregate.
        // Historical metadata proposal bytes/defaults are not silently rewritten.
        max_requests: if fixed_pilot { 7 } else { 12 },
        max_response_entity_bytes_total: 15_576_576,
        max_runtime_ms: 600_000,
    }
}

// Decision text must consume these actual request descriptors, not maintain a
// second hand-written path catalog (in particular not epoch-N.car.sha256/.cid).
fn metadata_operations(epoch: u64) -> Result<Vec<serde_json::Value>> {
    metadata_requests()
        .iter()
        .map(|request| {
            Ok(serde_json::json!({
                    "sequence": request.sequence,
                    "kind": request.kind,
                    "method": request.method(),
                    "host": of1_range_recorder::HOST,
                    "path": request.path(epoch),
            "max_response_entity_bytes_per_attempt": request.allowance(),
                }))
        })
        .collect()
}

fn open(root: &str, plan: &str, lease_hash: &str) -> Result<AcquisitionStore<SystemClock>> {
    Ok(AcquisitionStore::resume(
        Path::new(root),
        &read(plan)?,
        lease_hash,
        SystemClock,
    )?)
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
        ["clock-sample"] => print(&SystemClock.sample()?),
        ["clock-preflight", plan] => {
            let observation = clock_preflight(&read(plan)?)?;
            print(&observation)?;
            if !observation["violation"].is_null() {
                return Err("clock preflight observed a boot identity/elapsed-clock violation".into());
            }
            Ok(())
        }
        ["dataset-preflight", root] => {
            let canonical_root = validate_dataset_location(Path::new(root))?;
            print(&serde_json::json!({
                "schema":"OF1_DATASET_LOCATION_PREFLIGHT_1",
                "canonical_root":canonical_root,
                "location_status":"OUTSIDE_GIT", "read_only":true,
                "networkEnabled":false, "readyToRun":false
            }))
        }
        [command @ ("metadata-proposal" | "metadata-pilot-proposal"), root, code_sha, toolchain_fingerprint] => {
            // Fail before generating approval material; initialization repeats
            // this same read-only admission check against the current filesystem.
            validate_dataset_location(Path::new(root))?;
            for (value, length) in [(*code_sha, 40), (*toolchain_fingerprint, 64)] {
                if value.len() != length || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
                    return Err("code/toolchain identity must be exact lowercase hexadecimal".into());
                }
            }
            let aggregate = AggregatePlan {
                schema: AGGREGATE_SCHEMA.into(), epoch: 978,
                sample_identity: (*command == "metadata-pilot-proposal").then(of1_range_recorder::sample::SampleIdentity::fixed_pilot),
                download_rate: Some(of1_range_recorder::rate::DownloadRate::standard()),
                clock_policy: Some(ClockPolicy::standard()),
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
            let metadata_budget = metadata_budget(aggregate.sample_identity.is_some());
            print(&serde_json::json!({
                "schema":"OF1_METADATA_RUN_PROPOSAL_1", "approved":false,
                "networkEnabled":false, "readyToRun":false,
                "slice_class":aggregate.sample_identity.as_ref().map_or("ENGINEERING_VALIDATION_ONLY", |s| s.sample_class.as_str()),
                "aggregate":aggregate, "metadata_budget":metadata_budget,
                "metadata_operations": metadata_operations(aggregate.epoch)?,
                "approval_target_sha256":metadata_proposal_sha256(&aggregate, &metadata_budget)?,
                "required_next_action":"Review exact plan/code/toolchain, current cost and availability; obtain metadata-only GO. No payload authorization."
            }))
        }
        ["metadata-init", root, plan, lease] => {
            validate_dataset_location(Path::new(root))?;
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
        ["capture-stage", root, plan, lease_hash, "--monitor-socket", socket] => {
            capture_stage_monitored(root, plan, lease_hash, socket)
        }
        _ => Err(concat!(
            "usage: of1-acquire clock-sample | clock-preflight AGGREGATE_JSON | dataset-preflight ROOT | ",
            "metadata-proposal ROOT CODE_SHA TOOLCHAIN_SHA256 | ",
            "metadata-pilot-proposal ROOT CODE_SHA TOOLCHAIN_SHA256 | ",
            "metadata-init ROOT AGGREGATE_JSON METADATA_LEASE_JSON | ",
            "progress ROOT AGGREGATE_JSON LEASE_SHA256 | ",
            "capture-stage ROOT AGGREGATE_JSON LEASE_SHA256 [--monitor-socket LOCAL_SOCKET] | ",
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
    capture_stage_inner(root, plan, lease_hash, None)
}

#[cfg(not(all(feature = "monitor", feature = "network-of1")))]
fn capture_stage_monitored(_: &str, _: &str, _: &str, _: &str) -> Result<()> {
    Err(
        "network-of1 and monitor capabilities required; no connection or store mutation attempted"
            .into(),
    )
}

#[cfg(all(feature = "monitor", feature = "network-of1"))]
fn capture_stage_monitored(root: &str, plan: &str, lease_hash: &str, socket: &str) -> Result<()> {
    capture_stage_inner(root, plan, lease_hash, Some(Path::new(socket)))
}

#[cfg(feature = "network-of1")]
fn capture_stage_inner(
    root: &str,
    plan: &str,
    lease_hash: &str,
    socket: Option<&Path>,
) -> Result<()> {
    let mut store = open(root, plan, lease_hash)?;
    #[cfg(not(feature = "monitor"))]
    let _ = socket;
    #[cfg(feature = "monitor")]
    let mut monitor = socket.and_then(|socket| {
        if let Ok(context) = of1_range_recorder::monitor::read_run_context(Path::new(root)) {
            Some(of1_range_recorder::monitor::Monitor::new(
                context.snapshot,
                socket,
            ))
        } else {
            eprintln!(
                "MONITOR_UNAVAILABLE: read-only snapshot failed; capture authority unchanged"
            );
            None
        }
    });
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
        #[cfg(feature = "monitor")]
        let result = if let Some(observer) = monitor.as_mut() {
            of1_range_recorder::https::OfficialHttps::capture_observed(
                &mut store, sequence, observer,
            )
        } else {
            of1_range_recorder::https::OfficialHttps::capture(&mut store, sequence)
        };
        #[cfg(not(feature = "monitor"))]
        let result = of1_range_recorder::https::OfficialHttps::capture(&mut store, sequence);
        if let Err(error) = result {
            #[cfg(feature = "monitor")]
            if let Some(observer) = monitor.as_mut()
                && observer.snapshot.stage != "STOPPED"
            {
                observer.failed(sequence, &error.to_string());
            }
            print(
                &serde_json::json!({"stage_capture":"STOPPED", "reason":error.to_string(), "progress":store.progress().ok(), "domain_counts":"UNAVAILABLE_NOT_DECODED_IN_B4", "edge_evaluation":"NOT_EVALUATED_ENGINEERING_SLICE"}),
            )?;
            return Err(error.into());
        }
        print(&store.progress()?)?;
    }
    #[cfg(feature = "monitor")]
    if let Some(observer) = monitor.as_mut() {
        if let Ok(recorded) = of1_range_recorder::monitor::read_run_context(Path::new(root)) {
            observer.snapshot.storage = recorded.snapshot.storage;
            observer.snapshot.budgets = recorded.snapshot.budgets;
            observer.snapshot.artifacts = recorded.snapshot.artifacts;
        }
        observer.complete();
    }
    print(
        &serde_json::json!({"stage_capture":"COMPLETE", "next":"STOP_FOR_REVIEW_NO_AUTOMATIC_NEXT_STAGE", "progress":store.progress()?}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preflight_uses_recorder_policy_without_clamping_utc() {
        let before = of1_range_recorder::durable::ClockSample {
            wall_ms: 1_000_000,
            boot_ms: 100_000,
            boot_id: "fixture-same-boot".into(),
        };
        let mut after = before.clone();
        after.wall_ms -= 2;
        after.boot_ms += 10;
        check_follows(Some(&ClockPolicy::standard()), &before, &after).unwrap();
        assert_eq!(after.wall_ms, 999_998);
        assert!(check_follows(None, &before, &after).is_err());
        after.boot_ms = before.boot_ms - 1;
        assert!(check_follows(Some(&ClockPolicy::standard()), &before, &after).is_err());
    }
    #[test]
    fn decision_operations_use_rust_requests_and_preserved_official_paths() {
        let operations = metadata_operations(978).unwrap();
        // Observed HTTP-200 metadata receipts: pump-search-09379cff-01,
        // sequences 1/2. The original receipts remain outside Git, unchanged.
        let expected = [
            ("GET", "/978/epoch-978-slot-ranges.raw", 5_184_000),
            ("GET", "/978/epoch-978.sha256", 4096),
            ("GET", "/978/epoch-978.cid", 4096),
            ("HEAD", "/978/epoch-978.car", 0),
        ];
        assert_eq!(operations.len(), expected.len());
        for ((operation, request), (method, path, allowance)) in
            operations.iter().zip(metadata_requests()).zip(expected)
        {
            assert_eq!(operation["sequence"], request.sequence);
            assert_eq!(operation["method"], method);
            assert_eq!(operation["path"], path);
            assert_eq!(operation["path"], request.path(978));
            assert_eq!(
                operation["max_response_entity_bytes_per_attempt"],
                allowance
            );
            assert_eq!(operation["host"], of1_range_recorder::HOST);
        }
        // Epoch-specific, not a pinned spelling accidentally reused for another epoch.
        assert_eq!(
            metadata_operations(979).unwrap()[1]["path"],
            "/979/epoch-979.sha256"
        );
    }
    #[test]
    fn new_pilot_reserves_attempt_room_without_changing_legacy_metadata_caps() {
        let old = metadata_budget(false);
        let new = metadata_budget(true);
        assert_eq!(old.max_requests, 12);
        assert_eq!(new.max_requests, 7);
        assert_eq!(new.max_requests + 3 * 3, 16);
        assert_eq!(
            new.max_response_entity_bytes_total,
            old.max_response_entity_bytes_total
        );
        assert_eq!(new.max_runtime_ms, old.max_runtime_ms);
    }
}
