//! Existing atomic-part verification shared by explicit continuation and new B7 windows.
use crate::{
    batch::{self, Plan},
    collection::{self, Logical},
    continuation::{add_counts, publish_exact},
    invalid, report, resources,
};
use of1_range_recorder::{acquisition::read_limited, sha256};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, io, path::Path};

pub(crate) struct Context<'a> {
    pub plan: &'a Plan,
    pub plan_hash: &'a str,
    pub root: &'a Path,
    pub decision_sha256: Option<&'a str>,
}

type Guard = of1_range_recorder::campaign::Guard;
type Sample = of1_range_recorder::sample::SampleIdentity;

fn gate(path: &Path, root: &Path, reserve: u64) -> io::Result<(Plan, String, Guard, Sample)> {
    let (plan, hash) = batch::read_plan(path)?;
    plan.validate_sources()?;
    if plan.slot_part_profile.as_deref() != Some(report::PART_PROFILE) {
        return Err(invalid("REGISTERED_SLOT_PART_PROFILE_REQUIRED"));
    }
    let (sample, _) = batch::campaign_sample(&plan)?.ok_or_else(|| invalid("B7_REQUIRED"))?;
    let binding = sample.b7.as_ref().ok_or_else(|| invalid("B7_REQUIRED"))?;
    let expected =
        Path::new(&binding.campaign_root).join(format!("work/w{:02}", binding.window_ordinal));
    if root != expected || root.canonicalize()? != expected || path != root.join("plan.json") {
        return Err(invalid("REGISTERED_SLOT_PART_ROOT_REQUIRED"));
    }
    let (guard, sample) = batch::campaign_output(&plan, &hash, root, reserve)?
        .ok_or_else(|| invalid("B7_REQUIRED"))?;
    Ok((plan, hash, guard, sample))
}

/// # Errors
/// An existing native processing lease and verified sources are required.
pub fn inventory(path: &Path, root: &Path) -> io::Result<Value> {
    let (plan, hash, guard, sample) = gate(path, root, 0)?;
    let mut batches = Vec::new();
    for batch in &plan.batches {
        guard.processing_tick(&sample).map_err(invalid)?;
        let source = plan.source(&batch.source_id)?;
        let inv = report::receipt_part_inventory(&source.run_root, batch.receipt_sequences[0])?;
        batches.push(json!({"batch_id":batch.batch_id,"output_directory":batch.output_directory,"inventory":inv}));
    }
    guard.processing_tick(&sample).map_err(invalid)?;
    Ok(json!({"profile":report::PART_PROFILE,"plan_sha256":hash,"batches":batches}))
}

/// # Errors
/// Reuse only complete part publications with matching plan, source and binaries.
pub fn verify_part(
    path: &Path,
    root: &Path,
    id: &str,
    ordinal: usize,
    with_parquet: bool,
) -> io::Result<Value> {
    let (plan, hash, guard, sample) = gate(path, root, 0)?;
    let batch = plan.batch(id)?;
    let part = root
        .join(&batch.output_directory)
        .join(format!("part-{ordinal:04}"));
    let checked = batch::verify_output_identity(
        &plan,
        &hash,
        batch,
        &part.join("decode"),
        &crate::source_sha256(),
        &sha256(include_bytes!("../Cargo.lock")),
        Some(ordinal),
    )?;
    if with_parquet {
        let (quality, _) = collection::json_file(
            &part.join("decode/quality.json"),
            resources::MAX_QUALITY_BYTES,
        )?;
        collection::parquet(
            &plan,
            batch,
            &part.join("parquet"),
            &checked["execution"],
            &quality,
        )?;
    }
    guard.processing_tick(&sample).map_err(invalid)?;
    Ok(json!({"state":"VERIFIED_PART","ordinal":ordinal,"parquet_verified":with_parquet}))
}

/// # Errors
/// No slot is complete until every part, raw coverage and logical/physical hash verifies.
pub fn seal_slot(path: &Path, root: &Path, id: &str) -> io::Result<Value> {
    let (plan, hash, guard, sample) = gate(path, root, 2 * 1024 * 1024)?;
    let mut tick = || guard.processing_tick(&sample).map_err(invalid);
    let batch = plan.batch(id)?;
    let value = inspect_slot(
        &Context {
            plan: &plan,
            plan_hash: &hash,
            root,
            decision_sha256: None,
        },
        batch,
        &mut tick,
    )?;
    let raw = resources::bounded_json(
        &value,
        "SLOT_PART_MANIFEST",
        usize::try_from(batch::MAX_PLAN_BYTES).map_err(invalid)?,
    )?;
    let output = root.join(&batch.output_directory).join("slot.json");
    publish_exact(&output, &raw, &mut tick)?;
    publish_exact(
        &output.with_extension("json.sha256"),
        sha256(&raw).as_bytes(),
        &mut tick,
    )?;
    fs::File::open(output.parent().ok_or_else(|| invalid("SLOT_PARENT"))?)?.sync_all()?;
    Ok(
        json!({"state":"ACCOUNTED","slot":batch.slots[0],"manifest":output,"sha256":sha256(&raw),"layers":value["layers"]}),
    )
}

/// # Errors
/// All slots must be sealed. Existing charges and the original deadline remain binding.
#[allow(clippy::too_many_lines)] // One atomic verification/publication sequence; unchanged limits.
pub fn complete(path: &Path, root: &Path) -> io::Result<Value> {
    let (plan, hash, mut guard, sample) = gate(path, root, 5 * 1024 * 1024)?;
    let mut tick = || guard.processing_tick(&sample).map_err(invalid);
    let context = Context {
        plan: &plan,
        plan_hash: &hash,
        root,
        decision_sha256: None,
    };
    let (mut bronze, mut silver) = (Logical::default(), Logical::default());
    let (mut statuses, mut diagnoses) = (BTreeMap::new(), BTreeMap::new());
    let mut slots = Vec::new();
    let mut children = Vec::new();
    for batch in &plan.batches {
        let value = inspect_slot(&context, batch, &mut tick)?;
        let raw = resources::bounded_json(
            &value,
            "SLOT_PART_MANIFEST",
            usize::try_from(batch::MAX_PLAN_BYTES).map_err(invalid)?,
        )?;
        let p = root.join(&batch.output_directory).join("slot.json");
        if read_limited(&p, batch::MAX_PLAN_BYTES).map_err(invalid)? != raw
            || read_limited(&p.with_extension("json.sha256"), 64).map_err(invalid)?
                != sha256(&raw).as_bytes()
        {
            return Err(invalid("PARTS_SLOT_NOT_SEALED"));
        }
        for part in value["parts"]
            .as_array()
            .ok_or_else(|| invalid("SLOT_PARTS"))?
        {
            tick()?;
            let relative = part["decode_directory"]
                .as_str()
                .ok_or_else(|| invalid("PART_DIRECTORY"))?;
            collection::inspect_records(
                &plan,
                batch,
                &root.join(relative),
                &mut bronze,
                &mut silver,
                &crate::source_sha256(),
                collection::number(&part["range"]["start_transaction"])?,
            )?;
        }
        add_counts(&mut statuses, &value["transaction_status_counts"])?;
        add_counts(&mut diagnoses, &value["pump_layout_outcomes"])?;
        slots.push(json!({"slot":batch.slots[0],"state":"ACCOUNTED","transaction_envelopes":value["transaction_envelopes"],
            "silver_fact_count":value["silver_fact_count"],"transaction_status_counts":value["transaction_status_counts"],
            "dispositions":value["dispositions"],"pump_layout_outcomes":value["pump_layout_outcomes"],
            "raw_sha256":value["raw_sha256"],"part_count":value["parts"].as_array().map(Vec::len)}));
        children.push(json!({"manifest_path":format!("{}/slot.json",batch.output_directory),"sha256":sha256(&raw)}));
    }
    let mut manifest = json!({"schema":"OF1_PARTED_BATCH_COLLECTION_1","state":"COMPLETE","plan":plan,"plan_sha256":hash,
        "profile":report::PART_PROFILE,"slot_outcomes":slots,"slots":children,"sample_identity":sample,
        "layers":{"bronze":bronze.value(),"silver":silver.value()},"transaction_status_counts":statuses,"pump_layout_outcomes":diagnoses,
        "diagnosis_denominator":"Outcomes, not distinct rejected instructions or transactions",
        "completeness":{"all_selected_slots_accounted":true,"missing_selected_raw_slots":0,"research_suitability":"NOT_ESTABLISHED"},
        "producer_source_sha256":crate::source_sha256(),"research_ready":false,"root_to_slot_membership":"UNAVAILABLE"});
    if let Some(a) = guard.evaluation_processing(&sample).map_err(invalid)? {
        manifest["evaluation_processing"] = serde_json::to_value(a).map_err(invalid)?;
    }
    let raw = resources::bounded_json(&manifest, "PARTED_COLLECTION", 2 * 1024 * 1024)?;
    publish_exact(&root.join("collection.json"), &raw, &mut tick)?;
    publish_exact(
        &root.join("collection.json.sha256"),
        sha256(&raw).as_bytes(),
        &mut tick,
    )?;
    let report = json!({"schema":"OF1_B7_PARTED_WINDOW_REPORT_1","collection_manifest_sha256":sha256(&raw),"sample_identity":sample,
        "slot_outcomes":manifest["slot_outcomes"],"layers":manifest["layers"],"transaction_status_counts":statuses,
        "pump_layout_outcomes":diagnoses,"coverage":manifest["completeness"],"accounting":guard.accounting().map_err(invalid)?,
        "research_ready":false,"limits":"Diagnoses count outcomes, not unique instructions. Complete Bronze accounting does not prove universal Pump coverage, account state or research sufficiency. GAP, UNAVAILABLE and QUARANTINED remain distinct."});
    let report_raw = resources::bounded_json(&report, "PARTED_REPORT", 1024 * 1024)?;
    let pretty = serde_json::to_string_pretty(&report)
        .map_err(invalid)?
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;");
    let html = format!(
        "<!doctype html><html lang=nl><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>B7 DEVELOPMENT</title><style>body{{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style><h1>B7 DEVELOPMENT [{},{})</h1><p>Volledig verantwoord venster; Research Ready: false. Bronze-dekking, transactiesucces en Silver-toelating zijn afzonderlijk. Ontbrekend is geen nul.</p><p><a href=campaign-report.json>JSON</a> · <a href=collection.json>Bron-/slot-/deelmanifest</a></p><pre>{pretty}</pre></html>",
        sample.start_slot, sample.end_slot_exclusive
    );
    resources::check_size("PARTED_HTML", html.len(), 1024 * 1024)?;
    publish_exact(&root.join("campaign-report.json"), &report_raw, &mut tick)?;
    if sample
        .b7
        .as_ref()
        .is_some_and(|b| b.cohort_role == "DEVELOPMENT")
    {
        publish_exact(&root.join("index.html"), html.as_bytes(), &mut tick)?;
    }
    publish_exact(
        &root.join("campaign-report.COMPLETE"),
        sha256(&report_raw).as_bytes(),
        &mut tick,
    )?;
    fs::File::open(root)?.sync_all()?;
    guard
        .complete_processing(
            sample
                .b7
                .as_ref()
                .ok_or_else(|| invalid("B7_REQUIRED"))?
                .window_ordinal,
        )
        .map_err(invalid)?;
    Ok(
        json!({"state":"VERIFIED_PROCESSING_COMPLETE","manifest_sha256":sha256(&raw),"layers":manifest["layers"],"research_ready":false}),
    )
}

pub(crate) fn inspect_slot(
    context: &Context<'_>,
    batch: &batch::Batch,
    check: &mut dyn FnMut() -> io::Result<()>,
) -> io::Result<Value> {
    check()?;
    let source = context.plan.source(&batch.source_id)?;
    let inv = report::receipt_part_inventory(&source.run_root, batch.receipt_sequences[0])?;
    let n = collection::number(&inv["parts"])?;
    let total = collection::number(&inv["transaction_envelopes"])?;
    let base = context.root.join(&batch.output_directory);
    verify_part_directories(&base, n)?;
    let (mut bronze, mut silver) = (Logical::default(), Logical::default());
    let (mut outcomes, mut statuses, mut diagnoses, mut reasons) = (
        BTreeMap::new(),
        BTreeMap::new(),
        BTreeMap::new(),
        BTreeMap::new(),
    );
    let mut parts = Vec::new();
    for ordinal in 0..n {
        check()?;
        let part = base.join(format!("part-{ordinal:04}"));
        let decode = part.join("decode");
        let parquet = part.join("parquet");
        let checked = batch::verify_output_identity(
            context.plan,
            context.plan_hash,
            batch,
            &decode,
            &crate::source_sha256(),
            &sha256(include_bytes!("../Cargo.lock")),
            Some(usize::try_from(ordinal).map_err(invalid)?),
        )?;
        let (quality, qhash) =
            collection::json_file(&decode.join("quality.json"), resources::MAX_QUALITY_BYTES)?;
        let start = ordinal * report::PART_PACKAGES as u64;
        let end = total.min(start + report::PART_PACKAGES as u64);
        let expected = json!({"profile":report::PART_PROFILE,"ordinal":ordinal,"start_transaction":start,
            "end_transaction_exclusive":end,"slot_transaction_envelopes":total,"max_packages":report::PART_PACKAGES});
        if quality["slot_part"] != expected
            || checked["execution"]["slot_part"] != expected
            || quality["raw_sha256"] != inv["raw_sha256"]
            || quality["transaction_envelopes"] != end - start
            || quality["whole_slot_accounted"] != false
        {
            return Err(invalid("CONTINUATION_PART_RANGE_OR_SOURCE"));
        }
        let (manifest, mhash) = collection::parquet(
            context.plan,
            batch,
            &parquet,
            &checked["execution"],
            &quality,
        )?;
        let before = (bronze.rows, silver.rows);
        let counts = collection::inspect_records(
            context.plan,
            batch,
            &decode,
            &mut bronze,
            &mut silver,
            &crate::source_sha256(),
            start,
        )?;
        if bronze.rows - before.0 != end - start
            || manifest["layers"]["bronze"]["rows"] != end - start
            || manifest["layers"]["silver"]["rows"] != silver.rows - before.1
            || quality["silver_fact_count"] != silver.rows - before.1
        {
            return Err(invalid("CONTINUATION_PART_COUNT_PARITY"));
        }
        let actual = counts.get(&batch.slots[0]).cloned().unwrap_or_default();
        for kind in ["DECODED", "MISSING", "UNSUPPORTED", "QUARANTINED"] {
            if actual.get(kind).copied().unwrap_or(0)
                != collection::number(&quality["dispositions"][kind])?
            {
                return Err(invalid("CONTINUATION_PART_DISPOSITION"));
            }
        }
        add_counts(&mut outcomes, &quality["dispositions"])?;
        add_counts(
            &mut statuses,
            &quality["analysis"]["transaction_status_counts"],
        )?;
        add_counts(&mut diagnoses, &quality["analysis"]["pump_layout_outcomes"])?;
        add_counts(&mut reasons, &quality["reasons"])?;
        parts.push(json!({"ordinal":ordinal,"range":expected,"quality_sha256":qhash,"execution_sha256":sha256(&read_limited(&decode.join("execution.json"),resources::MAX_EXECUTION_BYTES as u64).map_err(invalid)?),
            "decode_directory":format!("{}/part-{ordinal:04}/decode",batch.output_directory),
            "parquet_manifest_path":format!("{}/part-{ordinal:04}/parquet/manifest.json",batch.output_directory),
            "parquet_manifest_sha256":mhash,"physical_files":manifest["files"],"layers":layer_identities(&manifest)?}));
    }
    if bronze.rows != total {
        return Err(invalid("CONTINUATION_SLOT_COVERAGE"));
    }
    check()?;
    Ok(
        json!({"schema":"OF1_VERIFIED_ATOMIC_SLOT_PARTS_1","state":"ACCOUNTED","slot":batch.slots[0],
        "batch_id":batch.batch_id,"decision_sha256":context.decision_sha256,"plan_sha256":context.plan_hash,"profile":report::PART_PROFILE,
        "original_source":source,"raw_sha256":inv["raw_sha256"],"transaction_envelopes":total,
        "dispositions":outcomes,"transaction_status_counts":statuses,"pump_layout_outcomes":diagnoses,"reasons":reasons,
        "silver_fact_count":silver.rows,"parts":parts,"layers":{"bronze":bronze.value(),"silver":silver.value()},
        "producer_source_sha256":crate::source_sha256(),"research_ready":false}),
    )
}

// Full schemas remain in each hash-bound Parquet manifest. Repeating them
// for every part would consume the unchanged aggregate publication budget.
fn layer_identities(manifest: &Value) -> io::Result<Value> {
    let mut layers = manifest["layers"].clone();
    for name in ["bronze", "silver"] {
        let layer = layers[name]
            .as_object_mut()
            .ok_or_else(|| invalid("PARQUET_LAYER"))?;
        if layer.remove("schema").is_none() || !layer.contains_key("schema_sha256") {
            return Err(invalid("PARQUET_SCHEMA_BINDING"));
        }
    }
    Ok(layers)
}

fn verify_part_directories(base: &Path, n: u64) -> io::Result<()> {
    let expected = (0..n)
        .map(|i| format!("part-{i:04}"))
        .collect::<std::collections::BTreeSet<_>>();
    let mut actual = std::collections::BTreeSet::new();
    for entry in fs::read_dir(base)? {
        let entry = entry?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| invalid("PART_FILENAME"))?;
        if ["slot.json", "slot.json.sha256"].contains(&name.as_str())
            && entry.file_type()?.is_file()
        {
            continue;
        }
        if !entry.file_type()?.is_dir() || !expected.contains(&name) || !actual.insert(name) {
            return Err(invalid("UNEXPECTED_SLOT_PART"));
        }
    }
    if actual != expected {
        return Err(invalid("INCOMPLETE_SLOT_PARTS"));
    }
    Ok(())
}
