//! Fixed-campaign sealed worker coordinator. Outcomes stay in the admitted
//! work root; stdout/stderr expose only a fixed operational state.
use crate::{batch, invalid, report};
use of1_range_recorder::{
    acquisition::read_limited,
    campaign::{EvaluationContinuationApproval, EvaluationRelease, Guard, ProcessingApproval},
    durable::{Clock, SystemClock},
    sha256,
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs, io,
    path::{Path, PathBuf},
    time::Instant,
};

/// Prepare an immutable worker-only revision of the original fixed-window plan.
/// This grants no processing or visibility authority.
/// # Errors
/// Invalid fixed-window identity, expired-stage state or changed plan fails closed.
pub fn continuation_plan(original: &Path, decoder: &str, projector: &str) -> io::Result<Value> {
    let (mut plan, _, sample, run) = evaluation_plan(original)?;
    plan.validate_sources()?;
    let b = sample.b7.as_ref().ok_or_else(|| invalid("B7"))?;
    let work = Path::new(&b.campaign_root).join(format!("work/w{:02}", b.window_ordinal));
    if original != work.join("plan.json") {
        return Err(invalid("ORIGINAL_PLAN_PATH"));
    }
    let (guard, context) =
        Guard::evaluation_continuation_context(&sample, &run).map_err(invalid)?;
    let generation = context["continuation_count"]
        .as_u64()
        .ok_or_else(|| invalid("GENERATION"))?
        + 1;
    plan.workers.batch_decoder_sha256 = decoder.into();
    plan.workers.projector_sha256 = projector.into();
    plan.validate_sources()?;
    let raw = serde_json::to_vec_pretty(&plan).map_err(invalid)?;
    if raw.len() > usize::try_from(batch::MAX_PLAN_BYTES).map_err(invalid)? {
        return Err(invalid("PLAN_LIMIT"));
    }
    let base = Path::new(&b.campaign_root).join("proposals");
    let folder = base.join(format!("w{:02}", b.window_ordinal));
    let target = folder.join(format!("continuation-{generation}.json"));
    guard.space(2 * 1024 * 1024).map_err(invalid)?;
    for directory in [&base, &folder] {
        if !directory.exists() {
            fs::create_dir(directory)?;
        }
        if !fs::symlink_metadata(directory)?.is_dir()
            || directory.canonicalize()? != directory.clone()
        {
            return Err(invalid("CONTINUATION_PLAN_DIRECTORY"));
        }
    }
    if target.exists() {
        if read_limited(&target, batch::MAX_PLAN_BYTES).map_err(invalid)? != raw {
            return Err(invalid("IMMUTABLE_CONTINUATION_PLAN"));
        }
    } else {
        batch::write_new(&target, &raw)?;
    }
    fs::File::open(&folder)?.sync_all()?;
    Ok(
        json!({"schema":"OF1_B7_EVALUATION_CONTINUATION_PLAN_1","plan_path":target,
        "plan_sha256":sha256(&raw),"window":b.window_ordinal,"generation":generation,
        "approved":false,"outcomes_released":false}),
    )
}

/// Full native verification of retained complete slots, then an outcome-free
/// template for one additional approved processing interval.
/// # Errors
/// Invalid source, old publication, checkpoint or proposed software fails closed.
pub fn continuation_proposal(path: &Path, driver: &Path, python: &Path) -> io::Result<Value> {
    let started = Instant::now();
    let (plan, hash, sample, run) = evaluation_plan(path)?;
    plan.validate_sources()?;
    let b = sample.b7.as_ref().ok_or_else(|| invalid("B7"))?;
    let work = Path::new(&b.campaign_root).join(format!("work/w{:02}", b.window_ordinal));
    let old_path = work.join("plan.json");
    let (old, old_hash) = batch::read_plan(&old_path)?;
    let mut expected = old.clone();
    expected.workers = plan.workers.clone();
    if serde_json::to_value(&expected).map_err(invalid)?
        != serde_json::to_value(&plan).map_err(invalid)?
    {
        return Err(invalid("CONTINUATION_CHANGED_SELECTION"));
    }
    let (guard, context) =
        Guard::evaluation_continuation_context(&sample, &run).map_err(invalid)?;
    let generation = context["continuation_count"]
        .as_u64()
        .ok_or_else(|| invalid("GENERATION"))?
        + 1;
    if path
        != Path::new(&b.campaign_root).join(format!(
            "proposals/w{:02}/continuation-{generation}.json",
            b.window_ordinal
        ))
    {
        return Err(invalid("CONTINUATION_PLAN_PATH"));
    }
    let retained =
        crate::slot_parts::verify_retained_evaluation(&old, &old_hash, &work, &guard, &sample)?;
    let verified_seconds = started.elapsed().as_secs_f64();
    let evaluation = of1_range_recorder::campaign::EvaluationBinding {
        method_sha256: of1_range_recorder::campaign::METHOD_SHA256.into(),
        acceptance_sha256: of1_range_recorder::campaign::METHOD_ACCEPTANCE_SHA256.into(),
        previous_ledger_sha256: context["ledger_sha256"]
            .as_str()
            .ok_or_else(|| invalid("LEDGER"))?
            .into(),
        driver_files: of1_range_recorder::campaign::driver_files(driver).map_err(invalid)?,
        python_sha256: of1_range_recorder::campaign::executable_file_hash(python)
            .map_err(invalid)?,
    };
    let a = EvaluationContinuationApproval {
        authority: of1_range_recorder::durable::acquisition::Authority::Fixture,
        window: b.window_ordinal,
        previous_ledger_sha256: evaluation.previous_ledger_sha256.clone(),
        original_processing_sha256: context["original_processing_sha256"]
            .as_str()
            .ok_or_else(|| invalid("PROCESSING"))?
            .into(),
        preceding_continuation_sha256: context["preceding_continuation_sha256"]
            .as_str()
            .map(str::to_owned),
        work_tree_sha256: Guard::evaluation_work_tree_sha256(&work).map_err(invalid)?,
        original_plan_sha256: old_hash,
        plan_sha256: hash,
        plan_path: path.to_owned(),
        original_producer_source_sha256: retained["original_producer_source_sha256"]
            .as_str()
            .ok_or_else(|| invalid("SOURCE"))?
            .into(),
        original_lock_sha256: retained["original_lock_sha256"]
            .as_str()
            .ok_or_else(|| invalid("LOCK"))?
            .into(),
        collector_source_sha256: crate::source_sha256(),
        collector_lock_sha256: sha256(include_bytes!("../Cargo.lock")),
        worker_sha256s: vec![
            plan.workers.batch_decoder_sha256,
            plan.workers.projector_sha256,
            of1_range_recorder::durable::acquisition::current_executable_sha256()
                .map_err(invalid)?,
        ],
        evaluation,
    };
    Ok(
        json!({"schema":"OF1_B7_EVALUATION_CONTINUATION_PROPOSAL_1","approved":false,
        "target_sha256":Guard::evaluation_continuation_target(&sample,&a).map_err(invalid)?,
        "binding":a,"sealed_slots_verified":retained["sealed_slots"],
        "remaining_slots":retained["remaining_slots"],"native_verification_seconds":verified_seconds,
        "preparation_seconds":started.elapsed().as_secs_f64(),"max_runtime_ms":1_200_000_u64,
        "max_work_bytes":4_294_967_296_u64,"outcomes_released":false,"research_ready":false}),
    )
}

/// Read only whitelisted operational receipts and native accounting. No
/// stdout/stderr, parquet, worker result or research count is opened.
struct OperationReceipt {
    stage: String,
    elapsed_seconds: f64,
    total_step_seconds: Option<f64>,
    preflight_seconds: Option<f64>,
    failed: bool,
}
/// # Errors
/// Invalid campaign binding or malformed operation receipt is rejected.
fn operation_receipt(path: &Path) -> io::Result<OperationReceipt> {
    let raw = read_limited(path, 8192).map_err(invalid)?;
    let v: Value = serde_json::from_slice(&raw).map_err(invalid)?;
    if v["schema"] != "OF1_COLLECTION_OPERATION_1" {
        return Err(invalid("OPERATION_SCHEMA"));
    }
    let stage = v["stage"]
        .as_str()
        .ok_or_else(|| invalid("OPERATION_STAGE"))?;
    if !matches!(
        stage,
        "DECODE_PART"
            | "PROJECT_PART"
            | "VERIFY_DECODE_PART"
            | "VERIFY_PARQUET_PART"
            | "SEAL_COMPLETE_SLOT"
    ) {
        return Err(invalid("OPERATION_STAGE"));
    }
    let elapsed = v["elapsed_seconds"]
        .as_f64()
        .ok_or_else(|| invalid("OPERATION_DURATION"))?;
    let total = v
        .get("total_step_seconds")
        .map(|n| n.as_f64().ok_or_else(|| invalid("OPERATION_DURATION")))
        .transpose()?;
    let preflight = v
        .get("preflight_seconds")
        .map(|n| n.as_f64().ok_or_else(|| invalid("OPERATION_DURATION")))
        .transpose()?;
    if !elapsed.is_finite()
        || elapsed < 0.0
        || total.is_some_and(|n| !n.is_finite() || n < elapsed)
        || preflight
            .is_some_and(|n| !n.is_finite() || n < 0.0 || total.is_some_and(|t| n + elapsed > t))
    {
        return Err(invalid("OPERATION_DURATION"));
    }
    let code = v
        .get("returncode")
        .ok_or_else(|| invalid("OPERATION_EXIT"))?;
    let error = v.get("error").ok_or_else(|| invalid("OPERATION_EXIT"))?;
    if !(code.is_i64() || code.is_null()) || !(error.is_string() || error.is_null()) {
        return Err(invalid("OPERATION_EXIT"));
    }
    Ok(OperationReceipt {
        stage: stage.into(),
        elapsed_seconds: elapsed,
        total_step_seconds: total,
        preflight_seconds: preflight,
        failed: code != 0 || !error.is_null(),
    })
}

/// Read only whitelisted operational receipts and native accounting.
/// # Errors
/// Invalid campaign binding or malformed operation receipt is rejected.
pub fn processing_status(path: &Path) -> io::Result<Value> {
    let (plan, _, sample, run) = evaluation_plan(path)?;
    plan.validate_sources()?;
    let b = sample.b7.as_ref().ok_or_else(|| invalid("B7"))?;
    let root = Path::new(&b.campaign_root).join(format!("work/w{:02}", b.window_ordinal));
    let mut status = Guard::evaluation_operational_status(&sample, &run).map_err(invalid)?;
    let mut stages = BTreeMap::<String, (u64, f64, f64, u64, f64, u64)>::new();
    let (mut started, mut receipted) = (BTreeSet::new(), BTreeSet::new());
    let mut failed = 0_u64;
    let mut last = None;
    for entry in fs::read_dir(&root)? {
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_str().ok_or_else(|| invalid("OPERATION_NAME"))?;
        if !name.starts_with("operation-") {
            continue;
        }
        let extension = Path::new(name)
            .extension()
            .and_then(|v| v.to_str())
            .ok_or_else(|| invalid("OPERATION_NAME"))?;
        if !matches!(extension, "json" | "stdout" | "stderr") {
            continue;
        }
        let digits = &name[10..name.len() - extension.len() - 1];
        if digits.len() != 4 || !digits.bytes().all(|b| b.is_ascii_digit()) {
            return Err(invalid("OPERATION_NAME"));
        }
        started.insert(digits.to_owned());
        if extension != "json" {
            continue;
        }
        receipted.insert(digits.to_owned());
        let receipt = operation_receipt(&entry.path())?;
        if receipt.failed {
            failed += 1;
        }
        let bucket = stages.entry(receipt.stage.clone()).or_default();
        bucket.0 += 1;
        bucket.1 += receipt.elapsed_seconds;
        if let Some(total) = receipt.total_step_seconds {
            bucket.2 += total;
            bucket.3 += 1;
        }
        if let Some(preflight) = receipt.preflight_seconds {
            bucket.4 += preflight;
            bucket.5 += 1;
        }
        if last
            .as_ref()
            .is_none_or(|(n, _): &(String, String)| n.as_str() < name)
        {
            last = Some((name.to_owned(), receipt.stage));
        }
    }
    let receipted_count = stages.values().map(|v| v.0).sum::<u64>();
    status["receipted_operation_count"] = json!(receipted_count);
    status["successful_operation_count"] = json!(receipted_count - failed);
    status["failed_operation_count"] = json!(failed);
    status["unreceipted_operation_count"] = json!(started.difference(&receipted).count());
    status["last_started_operation"] = started.last().map_or(Value::Null, |n| json!(n));
    status["receipted_stage_timings"] = json!(
        stages
            .iter()
            .map(
                |(name, (count, worker, total, measured_total, preflight, measured_preflight))| (
                    name.clone(),
                    json!({"count":count,"worker_seconds":worker,
                    "total_step_seconds":if count==measured_total {Some(total)} else {None},
                    "total_step_measured_count":measured_total,
                    "preflight_seconds":if count==measured_preflight {Some(preflight)} else {None},
                    "preflight_measured_count":measured_preflight})
                )
            )
            .collect::<BTreeMap<_, _>>()
    );
    status["last_receipted_stage"] = last.map_or(Value::Null, |(_, stage)| json!(stage));
    Ok(status)
}

/// Admit only the currently reverified work tree and exact approved plan.
/// # Errors
/// Expired, stale, unauthorised or changed proposals are rejected.
pub fn admit_continuation(
    path: &Path,
    driver: &Path,
    python: &Path,
    approval: &Path,
) -> io::Result<Value> {
    let a: EvaluationContinuationApproval =
        serde_json::from_slice(&read_limited(approval, 1024 * 1024).map_err(invalid)?)
            .map_err(invalid)?;
    let mut expected: EvaluationContinuationApproval =
        serde_json::from_value(continuation_proposal(path, driver, python)?["binding"].clone())
            .map_err(invalid)?;
    expected.authority = a.authority.clone();
    if a != expected {
        return Err(invalid("CONTINUATION_BINDING_CHANGED"));
    }
    let (plan, _, sample, run) = evaluation_plan(path)?;
    plan.validate_sources()?;
    let (mut guard, _) = Guard::evaluation_continuation_context(&sample, &run).map_err(invalid)?;
    guard
        .admit_evaluation_continuation(&sample, a, &SystemClock.sample().map_err(invalid)?)
        .map_err(invalid)?;
    Ok(
        json!({"state":"SEALED_CONTINUATION_ADMITTED","outcomes_released":false,"research_ready":false}),
    )
}

/// Only immutable acquisition identities; no archival/domain decode or admission.
/// # Errors
/// Rejects incomplete acquisition, wrong cohort/location or campaign binding.
pub fn prepare_source(root: &Path) -> io::Result<Value> {
    let root = root.canonicalize()?;
    let run = of1_range_recorder::monitor::read_run_context(&root)?;
    let sample = run
        .aggregate_plan
        .sample_identity
        .as_ref()
        .ok_or_else(|| invalid("EVALUATION_REQUIRED"))?;
    let guard = Guard::evaluation_preparation_context(sample, &root).map_err(invalid)?;
    if run.snapshot.stage != "COMPLETE" || run.prepared.is_none() {
        return Err(invalid("COMPLETE_ACQUISITION_REQUIRED"));
    }
    let mut source = batch::source_identity(&root)?;
    let b = sample.b7.as_ref().ok_or_else(|| invalid("B7"))?;
    source["source_id"] = json!(format!("b7-w{:02}", b.window_ordinal));
    Ok(json!({
        "schema":"OF1_B7_EVALUATION_SOURCE_1", "source":source,
        "method_sha256":of1_range_recorder::campaign::METHOD_SHA256,
        "acceptance_sha256":of1_range_recorder::campaign::METHOD_ACCEPTANCE_SHA256,
        "ledger_sha256":guard.accounting().map_err(invalid)?["ledger_sha256"],
        "preparer_sha256":of1_range_recorder::durable::acquisition::current_executable_sha256().map_err(invalid)?
    }))
}

/// Construct the fixed full-window plan from a reverified preparation record.
/// # Errors
/// Refuses changed sources/roles/ledger/software, missing receipts or invalid workers.
pub fn prepare_plan(source_path: &Path, decoder: &str, projector: &str) -> io::Result<Value> {
    let saved: Value =
        serde_json::from_slice(&read_limited(source_path, batch::MAX_PLAN_BYTES).map_err(invalid)?)
            .map_err(invalid)?;
    let root = Path::new(
        saved["source"]["run_root"]
            .as_str()
            .ok_or_else(|| invalid("SOURCE_REQUIRED"))?,
    );
    let current = prepare_source(root)?;
    if saved != current {
        return Err(invalid("PREPARATION_CHANGED"));
    }
    let source: batch::Source =
        serde_json::from_value(current["source"].clone()).map_err(invalid)?;
    let run = of1_range_recorder::monitor::read_run_context(root)?;
    let sample = run
        .aggregate_plan
        .sample_identity
        .as_ref()
        .ok_or_else(|| invalid("B7"))?;
    let prepared = run
        .prepared
        .as_ref()
        .ok_or_else(|| invalid("PAYLOAD_REQUIRED"))?;
    let mut selections = Vec::new();
    let mut batches = Vec::new();
    for (i, slot) in (sample.start_slot..sample.end_slot_exclusive).enumerate() {
        let request = prepared.requests().iter().find(|r| matches!(
            r.kind, of1_range_recorder::durable::acquisition::RequestKind::CarRange { slot: s, .. } if s == slot
        )).ok_or_else(|| invalid("SLOT_RECEIPT_REQUIRED"))?;
        selections.push(batch::Selection {
            source_id: source.source_id.clone(),
            slot,
            role: "ORIGINAL_SELECTION".into(),
        });
        batches.push(batch::Batch {
            batch_id: format!("batch-{i:03}"),
            source_id: source.source_id.clone(),
            slots: vec![slot],
            receipt_sequences: vec![request.sequence],
            output_directory: format!("batch-{i:03}"),
        });
    }
    let plan = batch::Plan {
        schema: batch::SCHEMA.into(),
        collection_id: format!("{}-sealed", source.source_id),
        workers: batch::Workers {
            batch_decoder_sha256: decoder.into(),
            projector_sha256: projector.into(),
        },
        sources: vec![source],
        logical_selection: selections,
        batches,
        slot_part_profile: Some(report::PART_PROFILE.into()),
        research_ready: false,
    };
    plan.validate_sources()?;
    serde_json::to_value(plan).map_err(invalid)
}

/// # Errors
/// Public legacy commands cannot inspect reserved evaluation sources.
pub fn deny_source(root: &Path) -> io::Result<()> {
    let run = of1_range_recorder::monitor::read_run_context(root)?;
    if let Some(sample) = run
        .aggregate_plan
        .sample_identity
        .as_ref()
        .filter(|s| s.b7.is_some())
    {
        of1_range_recorder::campaign::development_processing_only(sample).map_err(invalid)?;
    }
    Ok(())
}
/// # Errors
/// Public diagnostics/export commands never acquire a sealed processing permit.
pub fn deny_plan(path: &Path) -> io::Result<()> {
    let (plan, hash) = batch::read_plan(path)?;
    if let Some((sample, run)) = batch::campaign_sample(&plan)?
        && sample
            .b7
            .as_ref()
            .is_some_and(|b| b.cohort_role == "RESERVED_EVALUATION")
    {
        let binding = sample.b7.as_ref().ok_or_else(|| invalid("B7"))?;
        let root =
            Path::new(&binding.campaign_root).join(format!("work/w{:02}", binding.window_ordinal));
        Guard::output(&sample, &run, &root, &hash, 0).map_err(invalid)?;
    }
    Ok(())
}

fn evaluation_plan(
    path: &Path,
) -> io::Result<(
    batch::Plan,
    String,
    of1_range_recorder::sample::SampleIdentity,
    PathBuf,
)> {
    let (plan, hash) = batch::read_plan(path)?;
    let (sample, run) =
        batch::campaign_sample(&plan)?.ok_or_else(|| invalid("EVALUATION_REQUIRED"))?;
    if sample
        .b7
        .as_ref()
        .is_none_or(|b| b.cohort_role != "RESERVED_EVALUATION")
        || plan.slot_part_profile.as_deref() != Some(report::PART_PROFILE)
    {
        return Err(invalid("EVALUATION_REQUIRED"));
    }
    Ok((plan, hash, sample, run))
}
/// # Errors
/// No admission or mutation; exact source, method and software approval target.
pub fn proposal(path: &Path, driver: &Path, python: &Path) -> io::Result<Value> {
    let (p, h, sample, _) = evaluation_plan(path)?;
    p.validate_sources()?;
    let b = sample.b7.as_ref().ok_or_else(|| invalid("B7"))?;
    let status = Guard::status(Path::new(&b.campaign_root)).map_err(invalid)?;
    let a = ProcessingApproval {
        authority: of1_range_recorder::durable::acquisition::Authority::Fixture,
        window: b.window_ordinal,
        plan_sha256: h,
        worker_sha256s: vec![
            p.workers.batch_decoder_sha256,
            p.workers.projector_sha256,
            of1_range_recorder::durable::acquisition::current_executable_sha256()
                .map_err(invalid)?,
        ],
        evaluation: Some(of1_range_recorder::campaign::EvaluationBinding {
            method_sha256: of1_range_recorder::campaign::METHOD_SHA256.into(),
            acceptance_sha256: of1_range_recorder::campaign::METHOD_ACCEPTANCE_SHA256.into(),
            previous_ledger_sha256: status["ledger_sha256"]
                .as_str()
                .ok_or_else(|| invalid("ledger"))?
                .into(),
            driver_files: of1_range_recorder::campaign::driver_files(driver).map_err(invalid)?,
            python_sha256: of1_range_recorder::campaign::executable_file_hash(python)
                .map_err(invalid)?,
        }),
    };
    Ok(
        json!({"schema":"OF1_B7_SEALED_PROCESSING_PROPOSAL_1","approved":false,"target_sha256":Guard::bound_processing_target(&sample,&a).map_err(invalid)?,"binding":a,"authority_note":"FIXTURE is a template, never production authority","max_runtime_ms":900_000,"max_artifact_bytes":4_294_967_296_u64,"network_enabled":false,"research_ready":false}),
    )
}
/// Reverify all immutable native outputs before the one final release.
/// # Errors
/// No partial release on changed sources, children, producers or manifest hashes.
pub fn release(root: &Path, approval: &Path) -> io::Result<Value> {
    let a: EvaluationRelease =
        serde_json::from_slice(&read_limited(approval, 1024 * 1024).map_err(invalid)?)
            .map_err(invalid)?;
    let expected = Guard::evaluation_final_proposal(
        root,
        &a.assessment_software_sha256,
        a.terminal_decision_sha256.as_deref(),
    )
    .map_err(invalid)?;
    if a.binding != expected.binding
        || a.manifests != expected.manifests
        || a.unavailable != expected.unavailable
        || a.verifier_sha256 != expected.verifier_sha256
    {
        return Err(invalid("RELEASE_BINDING"));
    }
    let at = SystemClock.sample().map_err(invalid)?;
    let deadline = Guard::release_preflight(root, &a, &at).map_err(invalid)?;
    verify_released_inputs(root, &a, &mut || {
        let now = SystemClock.sample().map_err(invalid)?;
        if now.boot_id != at.boot_id || now.boot_ms > deadline {
            return Err(invalid("RELEASE_DEADLINE"));
        }
        Ok(())
    })?;
    Guard::release_evaluation(root, a, &SystemClock.sample().map_err(invalid)?).map_err(invalid)?;
    Ok(
        json!({"state":"FINAL_SNAPSHOT_RELEASED","single_final_assessment":true,"untouched_holdout_after_access":false,"research_ready":false}),
    )
}
fn verify_released_inputs(
    root: &Path,
    a: &EvaluationRelease,
    check: &mut dyn FnMut() -> io::Result<()>,
) -> io::Result<()> {
    for (i, expected) in &a.manifests {
        check()?;
        let work = root.join(format!("work/w{i:02}"));
        let (plan, hash) = batch::read_plan(&work.join("plan.json"))?;
        plan.validate_sources()?;
        let raw = read_limited(&work.join("collection.json"), 2 * 1024 * 1024).map_err(invalid)?;
        if sha256(&raw) != *expected {
            return Err(invalid("RELEASE_SNAPSHOT"));
        }
        let manifest: Value = serde_json::from_slice(&raw).map_err(invalid)?;
        if manifest["plan_sha256"] != hash {
            return Err(invalid("RELEASE_PLAN"));
        }
        check()?;
        crate::development_cohort::evaluation_window(&work, &manifest, check)?;
        check()?;
    }
    Ok(())
}
/// # Errors
/// Explicit final release only. Generic readers/exporters remain default-deny.
pub fn read_released(root: &Path) -> io::Result<Value> {
    let a = Guard::released_evaluation(root).map_err(invalid)?;
    if a.verifier_sha256
        != of1_range_recorder::durable::acquisition::current_executable_sha256().map_err(invalid)?
    {
        return Err(invalid("RELEASE_SOFTWARE"));
    }
    let started = std::time::Instant::now();
    verify_released_inputs(root, &a, &mut || {
        if started.elapsed().as_secs() < 900 {
            Ok(())
        } else {
            Err(invalid("READ_DEADLINE"))
        }
    })?;
    let mut windows = Vec::new();
    for (i, h) in &a.manifests {
        let path = root.join(format!("work/w{i:02}/collection.json"));
        let v: Value =
            serde_json::from_slice(&read_limited(&path, 2 * 1024 * 1024).map_err(invalid)?)
                .map_err(invalid)?;
        windows.push(json!({"window_ordinal":i,"manifest_sha256":h,"manifest":v}));
    }
    Ok(
        json!({"schema":"OF1_B7_FINAL_RELEASED_MANIFESTS_1","release":a,"windows":windows,"research_ready":false,"assessment_performed":false}),
    )
}

/// # Errors
/// One exact already released window, with original facts/instruction evidence.
/// No new decoding, analytical label or generic reader permission.
pub fn read_window(root: &Path, ordinal: u64) -> io::Result<Value> {
    let a = Guard::released_evaluation(root).map_err(invalid)?;
    if a.verifier_sha256
        != of1_range_recorder::durable::acquisition::current_executable_sha256().map_err(invalid)?
    {
        return Err(invalid("RELEASE_SOFTWARE"));
    }
    if let Some(reason) = a.unavailable.get(&ordinal) {
        return Ok(
            json!({"window_ordinal":ordinal,"state":"UNAVAILABLE","reason":reason,"method_sha256":a.binding.method_sha256,"research_ready":false}),
        );
    }
    let hash = a
        .manifests
        .get(&ordinal)
        .ok_or_else(|| invalid("RELEASE_ORDINAL"))?;
    let path = root.join(format!("work/w{ordinal:02}/collection.json"));
    let raw = read_limited(&path, 2 * 1024 * 1024).map_err(invalid)?;
    if sha256(&raw) != *hash {
        return Err(invalid("RELEASE_SNAPSHOT"));
    }
    let v: Value = serde_json::from_slice(&raw).map_err(invalid)?;
    let started = std::time::Instant::now();
    let mut result = crate::development_cohort::evaluation_window(
        path.parent().ok_or_else(|| invalid("RELEASE_ROOT"))?,
        &v,
        &mut || {
            if started.elapsed().as_secs() < 900 {
                Ok(())
            } else {
                Err(invalid("READ_DEADLINE"))
            }
        },
    )?;
    result["collection_manifest_sha256"] = hash.clone().into();
    result["release"] = serde_json::to_value(a).map_err(invalid)?;
    crate::resources::bounded_json(&result, "RELEASED_WINDOW", 16 * 1024 * 1024)?;
    Ok(result)
}
