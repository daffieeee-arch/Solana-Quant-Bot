//! Fixed-campaign sealed worker coordinator. Outcomes stay in the admitted
//! work root; stdout/stderr expose only a fixed operational state.
use crate::{batch, invalid, report};
use of1_range_recorder::{
    acquisition::read_limited,
    campaign::{EvaluationRelease, Guard, ProcessingApproval},
    durable::{Clock, SystemClock},
    sha256,
};
use serde_json::{Value, json};
use std::{
    io,
    path::{Path, PathBuf},
};

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
