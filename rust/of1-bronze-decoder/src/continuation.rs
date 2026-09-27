//! One explicit offline continuation of the retained first B7 window.
//! Old producers and output stay immutable; only four pending slots may be written.
use crate::{
    batch::{self, Plan},
    collection::{self, Logical},
    invalid, report, resources,
};
use of1_range_recorder::{
    acquisition::read_limited,
    campaign::{ContinuationApproval, Guard},
    durable::{
        Clock, SystemClock,
        acquisition::{Authority, current_executable_sha256},
    },
    sha256,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs, io,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Decision {
    pub schema: String,
    pub original_plan_path: PathBuf,
    pub original_plan_sha256: String,
    pub checkpoint_path: PathBuf,
    pub checkpoint_sha256: String,
    pub original_producer_source_sha256: String,
    pub original_lock_sha256: String,
    pub original_processing_sha256: String,
    pub ledger_sha256: String,
    pub plan: Plan,
    pub collector_sha256: String,
    pub collector_source_sha256: String,
    pub remaining_slots: Vec<u64>,
    pub profile: String,
}

fn encoded<T: Serialize>(value: &T) -> io::Result<Vec<u8>> {
    serde_json::to_vec_pretty(value).map_err(invalid)
}
fn text(value: &Value) -> io::Result<String> {
    value
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| invalid("CONTINUATION_STRING"))
}
fn work(plan: &Plan) -> io::Result<PathBuf> {
    let (sample, _) = batch::campaign_sample(plan)?.ok_or_else(|| invalid("B7_REQUIRED"))?;
    let batch = sample.b7.ok_or_else(|| invalid("B7_REQUIRED"))?;
    if batch.window_ordinal != 0 {
        return Err(invalid("CONTINUATION_FIRST_WINDOW_ONLY"));
    }
    Ok(Path::new(&batch.campaign_root).join("work/w00"))
}
impl Decision {
    fn root(&self) -> io::Result<PathBuf> {
        Ok(work(&self.plan)?.join("continuation-1"))
    }
    fn plan_hash(&self) -> io::Result<String> {
        Ok(sha256(&encoded(&self.plan)?))
    }
    fn hash(&self) -> io::Result<String> {
        Ok(sha256(&encoded(self)?))
    }
    fn approval(&self, authority: Authority) -> io::Result<ContinuationApproval> {
        Ok(ContinuationApproval {
            authority,
            window: 0,
            previous_ledger_sha256: self.ledger_sha256.clone(),
            original_processing_sha256: self.original_processing_sha256.clone(),
            checkpoint_sha256: self.checkpoint_sha256.clone(),
            decision_sha256: self.hash()?,
            plan_sha256: self.plan_hash()?,
            remaining_slots: self.remaining_slots.clone(),
            worker_sha256s: vec![
                self.plan.workers.batch_decoder_sha256.clone(),
                self.plan.workers.projector_sha256.clone(),
                self.collector_sha256.clone(),
            ],
        })
    }
    fn validate(&self) -> io::Result<()> {
        if self.schema != "OF1_B7_CONTINUATION_DECISION_1"
            || self.profile != report::PART_PROFILE
            || self.collector_source_sha256 != crate::source_sha256()
            || self.collector_sha256 != current_executable_sha256().map_err(invalid)?
            || self.remaining_slots != (422_526_156..422_526_160).collect::<Vec<_>>()
        {
            return Err(invalid("CONTINUATION_IDENTITY_PROFILE_OR_WORKER"));
        }
        let (old, hash) = batch::read_plan(&self.original_plan_path)?;
        if hash != self.original_plan_sha256
            || self.original_plan_path != work(&old)?.join("plan.json")
            || self.checkpoint_path.parent() != Some(work(&old)?.as_path())
            || !self
                .checkpoint_path
                .file_name()
                .and_then(|p| p.to_str())
                .is_some_and(|sample| {
                    sample.starts_with("collection-progress-")
                        && Path::new(sample)
                            .extension()
                            .is_some_and(|ext| ext == "json")
                })
        {
            return Err(invalid("CONTINUATION_ORIGINAL_PLAN_PATH"));
        }
        let mut expected = old;
        expected.workers = self.plan.workers.clone();
        expected.collection_id.push_str("-continuation-1");
        if encoded(&expected)? != encoded(&self.plan)? {
            return Err(invalid("CONTINUATION_SELECTION_CHANGED"));
        }
        self.plan.validate_sources()?;
        let (sample, _) =
            batch::campaign_sample(&self.plan)?.ok_or_else(|| invalid("B7_REQUIRED"))?;
        Guard::continuation_target(&sample, &self.approval(Authority::Fixture)?)
            .map_err(invalid)?;
        Ok(())
    }
}

/// # Errors
/// Bounded canonical decision only; no source, sample, producer or profile drift.
pub fn read(path: &Path) -> io::Result<Decision> {
    let raw = read_limited(path, batch::MAX_PLAN_BYTES).map_err(invalid)?;
    let decision: Decision = serde_json::from_slice(&raw).map_err(invalid)?;
    if raw != encoded(&decision)? {
        return Err(invalid("CONTINUATION_NONCANONICAL_DECISION"));
    }
    decision.validate()?;
    Ok(decision)
}

fn retained(decision: &Decision, check: &mut dyn FnMut() -> io::Result<()>) -> io::Result<Value> {
    let (checkpoint, hash) = collection::json_file(
        &decision.checkpoint_path,
        usize::try_from(batch::MAX_PLAN_BYTES).map_err(invalid)?,
    )?;
    if hash != decision.checkpoint_sha256
        || checkpoint["collector_source_sha256"] != decision.original_producer_source_sha256
        || read_limited(
            &PathBuf::from(format!("{}.sha256", decision.checkpoint_path.display())),
            64,
        )
        .map_err(invalid)?
            != hash.as_bytes()
    {
        return Err(invalid("CONTINUATION_CHECKPOINT_HASH"));
    }
    let verified = collection::inspect_identity(
        &decision.original_plan_path,
        &work(&decision.plan)?,
        &decision.original_producer_source_sha256,
        &decision.original_lock_sha256,
        check,
    )?;
    if checkpoint != verified
        || checkpoint["state"] != "INCOMPLETE"
        || checkpoint["plan_sha256"] != decision.original_plan_sha256
    {
        return Err(invalid("CONTINUATION_CHECKPOINT_CHANGED"));
    }
    let batches = checkpoint["batches"]
        .as_array()
        .ok_or_else(|| invalid("CONTINUATION_BATCHES"))?;
    let slots = checkpoint["slot_outcomes"]
        .as_array()
        .ok_or_else(|| invalid("CONTINUATION_SLOTS"))?;
    if batches.len() != 16
        || slots.len() != 16
        || batches.iter().enumerate().any(|(i, batch)| {
            batch["state"] != if i < 12 { "VERIFIED" } else { "PENDING" }
                || batch["selected_slots"] != json!([422_526_144_u64 + i as u64])
        })
        || slots.iter().enumerate().any(|(i, sample)| {
            sample["slot"] != 422_526_144_u64 + i as u64
                || sample["state"] != if i < 12 { "ACCOUNTED" } else { "PENDING" }
        })
    {
        return Err(invalid("CONTINUATION_REQUIRES_EXACT_TWELVE_SLOT_PREFIX"));
    }
    Ok(checkpoint)
}

/// Pure proposal over verified existing sources/checkpoint. Does not grant time.
/// # Errors
/// Any source, checkpoint, ledger or previous-producer discrepancy stops.
pub fn proposal(
    old_path: &Path,
    checkpoint: &Path,
    decoder: &str,
    projector: &str,
) -> io::Result<Value> {
    let (old, old_hash) = batch::read_plan(old_path)?;
    old.validate_sources()?;
    let (sample, run) = batch::campaign_sample(&old)?.ok_or_else(|| invalid("B7_REQUIRED"))?;
    let (_guard, context) = Guard::continuation_context(&sample, &run).map_err(invalid)?;
    if context["approval"]["plan_sha256"] != old_hash
        || context["approval"]["worker_sha256s"][0] != old.workers.batch_decoder_sha256
        || context["approval"]["worker_sha256s"][1] != old.workers.projector_sha256
    {
        return Err(invalid("CONTINUATION_ORIGINAL_APPROVAL"));
    }
    let (value, checkpoint_hash) = collection::json_file(
        checkpoint,
        usize::try_from(batch::MAX_PLAN_BYTES).map_err(invalid)?,
    )?;
    let mut plan = old;
    decoder.clone_into(&mut plan.workers.batch_decoder_sha256);
    projector.clone_into(&mut plan.workers.projector_sha256);
    plan.collection_id.push_str("-continuation-1");
    let decision = Decision {
        schema: "OF1_B7_CONTINUATION_DECISION_1".into(),
        original_plan_path: old_path.to_path_buf(),
        original_plan_sha256: old_hash,
        checkpoint_path: checkpoint.to_path_buf(),
        checkpoint_sha256: checkpoint_hash,
        original_producer_source_sha256: text(&value["collector_source_sha256"])?,
        original_lock_sha256: sha256(include_bytes!("../Cargo.lock")),
        original_processing_sha256: text(&context["original_processing_sha256"])?,
        ledger_sha256: text(&context["ledger_sha256"])?,
        plan,
        collector_sha256: current_executable_sha256().map_err(invalid)?,
        collector_source_sha256: crate::source_sha256(),
        remaining_slots: (422_526_156..422_526_160).collect(),
        profile: report::PART_PROFILE.into(),
    };
    decision.validate()?;
    let verified = retained(&decision, &mut || Ok(()))?;
    let target = Guard::continuation_target(&sample, &decision.approval(Authority::Fixture)?)
        .map_err(invalid)?;
    Ok(
        json!({"decision":decision,"decision_json":String::from_utf8(encoded(&decision)?).map_err(invalid)?,"decision_sha256":decision.hash()?,
        "approval_target_sha256":target,"approved":false,"network_enabled":false,"max_additional_runtime_ms":900_000,
        "existing_work_reservation_bytes":4_294_967_296_u64,"retained_layers":verified["layers"],"research_ready":false}),
    )
}

/// Append native authority, then create only the registered continuation tree.
/// # Errors
/// Ambiguous publication is retained and never grants a second lease on retry.
pub fn admit(path: &Path, authority_path: &Path) -> io::Result<Value> {
    let decision = read(path)?;
    let authority: Authority =
        serde_json::from_slice(&read_limited(authority_path, 65_536).map_err(invalid)?)
            .map_err(invalid)?;
    let (sample, run) =
        batch::campaign_sample(&decision.plan)?.ok_or_else(|| invalid("B7_REQUIRED"))?;
    let (mut guard, context) = Guard::continuation_context(&sample, &run).map_err(invalid)?;
    if context["ledger_sha256"] != decision.ledger_sha256
        || context["original_processing_sha256"] != decision.original_processing_sha256
    {
        return Err(invalid("CONTINUATION_STALE_LEDGER_OR_APPROVAL"));
    }
    retained(&decision, &mut || Ok(()))?;
    let root = decision.root()?;
    if root.exists() {
        return Err(invalid("CONTINUATION_OUTPUT_ALREADY_EXISTS"));
    }
    guard
        .admit_continuation(
            &sample,
            decision.approval(authority)?,
            &SystemClock.sample().map_err(invalid)?,
        )
        .map_err(invalid)?;
    guard.processing_tick(&sample).map_err(invalid)?;
    fs::create_dir(&root)?;
    for (name, bytes) in [
        ("decision.json", encoded(&decision)?),
        ("plan.json", encoded(&decision.plan)?),
        ("driver.lock", Vec::new()),
    ] {
        guard.processing_tick(&sample).map_err(invalid)?;
        batch::write_new(&root.join(name), &bytes)?;
    }
    fs::File::open(&root)?.sync_all()?;
    fs::File::open(
        root.parent()
            .ok_or_else(|| invalid("CONTINUATION_PARENT"))?,
    )?
    .sync_all()?;
    Ok(
        json!({"state":"ADMITTED_OFFLINE_CONTINUATION","decision_sha256":decision.hash()?,"root":root,
        "additional_runtime_ms":900_000,"deadline_boot_ms":guard.processing_deadline_boot_ms(&sample).map_err(invalid)?,"budget_reset":false}),
    )
}

fn gate(
    decision: &Decision,
    additional: u64,
) -> io::Result<(Guard, of1_range_recorder::sample::SampleIdentity)> {
    let (sample, run) =
        batch::campaign_sample(&decision.plan)?.ok_or_else(|| invalid("B7_REQUIRED"))?;
    let guard = Guard::output(
        &sample,
        &run,
        &decision.root()?,
        &decision.plan_hash()?,
        additional,
    )
    .map_err(invalid)?;
    let a = guard.continuation_decision(&sample).map_err(invalid)?;
    let expected = decision.approval(a.authority.clone())?;
    if a != expected
        || read_limited(
            &decision.root()?.join("decision.json"),
            batch::MAX_PLAN_BYTES,
        )
        .map_err(invalid)?
            != encoded(decision)?
        || read_limited(&decision.root()?.join("plan.json"), batch::MAX_PLAN_BYTES)
            .map_err(invalid)?
            != encoded(&decision.plan)?
    {
        return Err(invalid("CONTINUATION_ADMITTED_DECISION_CHANGED"));
    }
    Ok((guard, sample))
}

/// # Errors
/// Inspection keeps the fixed additional deadline and the original reservation.
pub fn check(path: &Path, additional: u64) -> io::Result<Value> {
    let decision = read(path)?;
    let (guard, sample) = gate(&decision, additional)?;
    Ok(
        json!({"state":"WITHIN_CONTINUATION_LEASE","deadline_boot_ms":guard.processing_deadline_boot_ms(&sample).map_err(invalid)?,
        "remaining_ms":guard.processing_remaining_ms(&sample).map_err(invalid)?,"decision_sha256":decision.hash()?,"accounting":guard.accounting().map_err(invalid)?,"budget_reset":false}),
    )
}

fn remaining_batch<'a>(decision: &'a Decision, id: &str) -> io::Result<&'a batch::Batch> {
    let batch = decision.plan.batch(id)?;
    if batch.slots.len() != 1 || !decision.remaining_slots.contains(&batch.slots[0]) {
        return Err(invalid("CONTINUATION_RETAINED_OR_UNSELECTED_SLOT"));
    }
    Ok(batch)
}

/// Inventory and verification are native; the runner only schedules workers.
/// # Errors
/// Changed source or expired authority never becomes an empty slot.
pub fn inventory(path: &Path) -> io::Result<Value> {
    let decision = read(path)?;
    let (guard, sample) = gate(&decision, 0)?;
    let mut entries = Vec::new();
    for batch in decision
        .plan
        .batches
        .iter()
        .filter(|batch| decision.remaining_slots.contains(&batch.slots[0]))
    {
        guard.processing_tick(&sample).map_err(invalid)?;
        let inv = report::receipt_part_inventory(
            &decision.plan.source(&batch.source_id)?.run_root,
            batch.receipt_sequences[0],
        )?;
        entries.push(
            json!({"batch_id":batch.batch_id,"output_directory":batch.output_directory,"inventory":inv}),
        );
    }
    guard.processing_tick(&sample).map_err(invalid)?;
    Ok(json!({"decision_sha256":decision.hash()?,"batches":entries,"profile":decision.profile}))
}

/// Read-only verification of the preserved prefix under the new lease.
/// # Errors
/// Changed retained bytes or authority stops before any decode.
pub fn verify_retained(path: &Path) -> io::Result<Value> {
    let decision = read(path)?;
    let (guard, sample) = gate(&decision, 0)?;
    let value = retained(&decision, &mut || {
        guard.processing_tick(&sample).map_err(invalid)
    })?;
    Ok(
        json!({"state":"RETAINED_VERIFIED","checkpoint_sha256":decision.checkpoint_sha256,"layers":value["layers"]}),
    )
}

/// Verify a completed part for exact reuse; no new time or output is granted.
/// # Errors
/// Incomplete or different decoder/Parquet publication cannot be resumed.
pub fn verify_part(path: &Path, id: &str, ordinal: usize, with_parquet: bool) -> io::Result<Value> {
    let decision = read(path)?;
    let (guard, sample) = gate(&decision, 0)?;
    let batch = remaining_batch(&decision, id)?;
    let part = decision
        .root()?
        .join(&batch.output_directory)
        .join(format!("part-{ordinal:04}"));
    let value = batch::verify_output_identity(
        &decision.plan,
        &decision.plan_hash()?,
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
            &decision.plan,
            batch,
            &part.join("parquet"),
            &value["execution"],
            &quality,
        )?;
    }
    guard.processing_tick(&sample).map_err(invalid)?;
    Ok(json!({"state":"VERIFIED_PART","ordinal":ordinal,"parquet_verified":with_parquet}))
}

fn add_counts(total: &mut BTreeMap<String, u64>, value: &Value) -> io::Result<()> {
    for (k, value) in value
        .as_object()
        .ok_or_else(|| invalid("CONTINUATION_COUNTS"))?
    {
        let old = total.entry(k.clone()).or_default();
        *old = old
            .checked_add(collection::number(value)?)
            .ok_or_else(|| invalid("CONTINUATION_COUNT_OVERFLOW"))?;
    }
    Ok(())
}

fn inspect_slot(
    decision: &Decision,
    batch: &batch::Batch,
    check: &mut dyn FnMut() -> io::Result<()>,
) -> io::Result<Value> {
    check()?;
    let source = decision.plan.source(&batch.source_id)?;
    let inv = report::receipt_part_inventory(&source.run_root, batch.receipt_sequences[0])?;
    let n = collection::number(&inv["parts"])?;
    let total = collection::number(&inv["transaction_envelopes"])?;
    let base = decision.root()?.join(&batch.output_directory);
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
            &decision.plan,
            &decision.plan_hash()?,
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
            &decision.plan,
            batch,
            &parquet,
            &checked["execution"],
            &quality,
        )?;
        let before = (bronze.rows, silver.rows);
        let counts = collection::inspect_records(
            &decision.plan,
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
        "batch_id":batch.batch_id,"decision_sha256":decision.hash()?,"plan_sha256":decision.plan_hash()?,"profile":decision.profile,
        "original_source":source,"raw_sha256":inv["raw_sha256"],"transaction_envelopes":total,
        "dispositions":outcomes,"transaction_status_counts":statuses,"pump_layout_outcomes":diagnoses,"reasons":reasons,
        "silver_fact_count":silver.rows,"parts":parts,"layers":{"bronze":bronze.value(),"silver":silver.value()},
        "producer_source_sha256":crate::source_sha256(),"research_ready":false}),
    )
}

fn publish_exact(
    path: &Path,
    raw: &[u8],
    check: &mut dyn FnMut() -> io::Result<()>,
) -> io::Result<()> {
    check()?;
    if path.exists() {
        if read_limited(path, raw.len() as u64).map_err(invalid)? != raw {
            return Err(invalid("CONTINUATION_EXISTING_OUTPUT_DIFFERS"));
        }
    } else {
        batch::write_new(path, raw)?;
    }
    check()?;
    Ok(())
}

/// # Errors
/// A missing/corrupt/interrupted part cannot publish a complete slot manifest.
pub fn seal_slot(path: &Path, id: &str) -> io::Result<Value> {
    let decision = read(path)?;
    let (guard, sample) = gate(&decision, 2 * 1024 * 1024)?;
    let mut tick = || guard.processing_tick(&sample).map_err(invalid);
    let batch = remaining_batch(&decision, id)?;
    let value = inspect_slot(&decision, batch, &mut tick)?;
    let raw = resources::bounded_json(
        &value,
        "SLOT_PART_MANIFEST",
        usize::try_from(batch::MAX_PLAN_BYTES).map_err(invalid)?,
    )?;
    let output = decision
        .root()?
        .join(&batch.output_directory)
        .join("slot.json");
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

/// Final native collection includes mixed producer identities without rewriting
/// historical output. One window remains one incomplete research sample.
/// # Errors
/// Every retained/remaining slot and physical/logical identity must verify.
pub fn complete(path: &Path) -> io::Result<Value> {
    let decision = read(path)?;
    let (mut guard, sample) = gate(&decision, 5 * 1024 * 1024)?;
    let mut tick = || guard.processing_tick(&sample).map_err(invalid);
    let old = retained(&decision, &mut tick)?;
    let (mut bronze, mut silver) = (
        Logical::from_verified(&old["layers"]["bronze"])?,
        Logical::from_verified(&old["layers"]["silver"])?,
    );
    let mut slots = Vec::new();
    let (mut statuses, mut diagnoses) = (BTreeMap::new(), BTreeMap::new());
    for batch in decision.plan.batches.iter().take(12) {
        tick()?;
        let (quality, _) = collection::json_file(
            &work(&decision.plan)?
                .join(&batch.output_directory)
                .join("decode/quality.json"),
            resources::MAX_QUALITY_BYTES,
        )?;
        add_counts(
            &mut statuses,
            &quality["analysis"]["transaction_status_counts"],
        )?;
        add_counts(&mut diagnoses, &quality["analysis"]["pump_layout_outcomes"])?;
        slots.push(json!({"slot":batch.slots[0],"state":"ACCOUNTED","producer":"ORIGINAL_RETAINED",
            "transaction_envelopes":quality["transaction_envelopes"],"silver_fact_count":quality["silver_fact_count"],
            "transaction_status_counts":quality["analysis"]["transaction_status_counts"],"dispositions":quality["dispositions"],
            "pump_layout_outcomes":quality["analysis"]["pump_layout_outcomes"],"raw_sha256":quality["raw_sha256"]}));
    }
    let mut new_slots = Vec::new();
    for batch in decision.plan.batches.iter().skip(12) {
        tick()?;
        let value = inspect_slot(&decision, batch, &mut tick)?;
        let raw = resources::bounded_json(
            &value,
            "SLOT_PART_MANIFEST",
            usize::try_from(batch::MAX_PLAN_BYTES).map_err(invalid)?,
        )?;
        let p = decision
            .root()?
            .join(&batch.output_directory)
            .join("slot.json");
        if read_limited(&p, batch::MAX_PLAN_BYTES).map_err(invalid)? != raw
            || read_limited(&p.with_extension("json.sha256"), 64).map_err(invalid)?
                != sha256(&raw).as_bytes()
        {
            return Err(invalid("CONTINUATION_SLOT_NOT_SEALED"));
        }
        for part in value["parts"]
            .as_array()
            .ok_or_else(|| invalid("SLOT_PARTS"))?
        {
            tick()?;
            collection::inspect_records(
                &decision.plan,
                batch,
                &decision.root()?.join(text(&part["decode_directory"])?),
                &mut bronze,
                &mut silver,
                &crate::source_sha256(),
                collection::number(&part["range"]["start_transaction"])?,
            )?;
        }
        add_counts(&mut statuses, &value["transaction_status_counts"])?;
        add_counts(&mut diagnoses, &value["pump_layout_outcomes"])?;
        slots.push(json!({"slot":batch.slots[0],"state":"ACCOUNTED","producer":"CONTINUATION",
            "transaction_envelopes":value["transaction_envelopes"],"silver_fact_count":value["silver_fact_count"],
            "transaction_status_counts":value["transaction_status_counts"],"dispositions":value["dispositions"],
            "pump_layout_outcomes":value["pump_layout_outcomes"],"raw_sha256":value["raw_sha256"],"part_count":value["parts"].as_array().map(Vec::len)}));
        new_slots.push(json!({"manifest_path":format!("{}/slot.json",batch.output_directory),"sha256":sha256(&raw),"manifest":value}));
    }
    let manifest = json!({"schema":"OF1_CONTINUED_BATCH_COLLECTION_1","state":"COMPLETE","decision":decision,
        "decision_sha256":decision.hash()?,"retained_checkpoint_sha256":decision.checkpoint_sha256,"retained_checkpoint":old,
        "retained_output_unchanged":true,"slot_outcomes":slots,"continued_slots":new_slots,
        "layers":{"bronze":bronze.value(),"silver":silver.value()},"transaction_status_counts":statuses,
        "pump_layout_outcomes":diagnoses,"diagnosis_denominator":"Outcomes, not distinct rejected instructions or transactions",
        "completeness":{"all_selected_slots_accounted":true,"missing_selected_raw_slots":0,"research_suitability":"NOT_ESTABLISHED"},
        "sample_identity":sample,"research_ready":false,"root_to_slot_membership":"UNAVAILABLE"});
    let raw = resources::bounded_json(&manifest, "CONTINUED_COLLECTION", 2 * 1024 * 1024)?;
    let root = decision.root()?;
    publish_exact(&root.join("collection.json"), &raw, &mut tick)?;
    publish_exact(
        &root.join("collection.json.sha256"),
        sha256(&raw).as_bytes(),
        &mut tick,
    )?;
    publish_report(&decision, &sample, &manifest, &raw, &mut tick)?;
    guard.complete_processing(0).map_err(invalid)?;
    Ok(
        json!({"state":"VERIFIED_PROCESSING_COMPLETE","manifest_sha256":sha256(&raw),"layers":manifest["layers"],"research_ready":false}),
    )
}

fn publish_report(
    decision: &Decision,
    sample: &of1_range_recorder::sample::SampleIdentity,
    manifest: &Value,
    raw: &[u8],
    tick: &mut dyn FnMut() -> io::Result<()>,
) -> io::Result<()> {
    let root = decision.root()?;
    let report = json!({"schema":"OF1_B7_CONTINUED_WINDOW_REPORT_1","state":"COMPLETE","sample_identity":sample,
        "collection_manifest_sha256":sha256(raw),"decision_sha256":decision.hash()?,"slot_outcomes":manifest["slot_outcomes"],
        "layers":manifest["layers"],"transaction_status_counts":manifest["transaction_status_counts"],"pump_layout_outcomes":manifest["pump_layout_outcomes"],
        "coverage":manifest["completeness"],"retained_checkpoint_sha256":decision.checkpoint_sha256,
        "retained_layers":manifest["retained_checkpoint"]["layers"],"accounting_semantics":"Original charges preserved; actual disk/time measured separately at completion",
        "research_ready":false,"limits":"Complete accounting is not universal Pump decoding or research sufficiency. Event reserves are not account state. No evaluation outcomes or B8."});
    let report_bytes = resources::bounded_json(&report, "CONTINUATION_REPORT", 1024 * 1024)?;
    let pretty = String::from_utf8(encoded(&report)?).map_err(invalid)?;
    let mut table = String::new();
    for slot in manifest["slot_outcomes"]
        .as_array()
        .ok_or_else(|| invalid("REPORT_SLOTS"))?
    {
        use std::fmt::Write as _;
        write!(
            table,
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            collection::number(&slot["slot"])?,
            if slot["producer"] == "ORIGINAL_RETAINED" {
                "Behouden"
            } else {
                "Nieuwe delen"
            },
            collection::number(&slot["transaction_envelopes"])?,
            slot["transaction_status_counts"]["ERROR"],
            collection::number(&slot["silver_fact_count"])?,
            slot["dispositions"]
        )
        .map_err(invalid)?;
    }
    let html = format!(
        "<!doctype html><html lang=nl><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'><title>B7 DEVELOPMENT — volledig venster</title><style>body{{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid #999;padding:6px;text-align:left}}</style><h1>B7 DEVELOPMENT [422526144,422526160)</h1><p>Alle zestien slots verantwoord. Research Ready: false. Bronze-dekking, transactiesucces, Silver-toelating en onderzoekssufficiëntie blijven afzonderlijk.</p><p>De oorspronkelijke twaalf slots zijn geverifieerd en behouden, met hun eigen producent. Vier slots zijn offline verwerkt in atomaire transactiedelen; geen nieuwe acquisitie.</p><p><a href=campaign-report.json>JSON</a> · <a href=collection.json>Volledig bron-/deel-/bestand-/hashmanifest</a></p><h2>Alle slots · ACCOUNTED</h2><p>Null betekent niet beschikbaar, niet nul. Diagnoses zijn geen unieke afwijzingen.</p><table><thead><tr><th>Slot</th><th>Producent</th><th>Packages</th><th>Failures</th><th>Silver-feiten</th><th>Decode-uitkomsten</th></tr></thead><tbody>{table}</tbody></table><details><summary>Volledige tellingen, hashes en beperkingen</summary><pre>{}</pre></details></html>",
        pretty
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
    );
    resources::check_size("CONTINUATION_HTML", html.len(), 1024 * 1024)?;
    // Ambiguous partial report publication is retained and fails closed. Resume
    // between verified parts is supported; no filesystem/journal repair is implied.
    publish_exact(&root.join("campaign-report.json"), &report_bytes, tick)?;
    publish_exact(&root.join("index.html"), html.as_bytes(), tick)?;
    publish_exact(
        &root.join("campaign-report.COMPLETE"),
        sha256(&report_bytes).as_bytes(),
        tick,
    )?;
    fs::File::open(&root)?.sync_all()?;
    Ok(())
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
