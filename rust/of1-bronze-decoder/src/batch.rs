//! Explicit physical batches over immutable original run identities. Neither
//! a batch label nor a context role is allowed to rewrite canonical records.
use crate::{invalid, report, resources};
use of1_range_recorder::{
    acquisition::read_limited,
    durable::acquisition::{RequestKind, current_executable_sha256},
    monitor::read_run_context,
    recorded_verification::bindings,
    sha256,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub const SCHEMA: &str = "OF1_BATCH_COLLECTION_PLAN_1";
pub const MAX_PLAN_BYTES: u64 = 1_048_576;
pub const MAX_COLLECTION_SLOTS: usize = 256;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Workers {
    pub batch_decoder_sha256: String,
    pub projector_sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub source_id: String,
    pub run_root: PathBuf,
    pub run_id: String,
    pub bindings: Value,
    pub sample_identity: Value,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub source_id: String,
    pub slot: u64,
    pub role: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Batch {
    pub batch_id: String,
    pub source_id: String,
    pub slots: Vec<u64>,
    pub receipt_sequences: Vec<u64>,
    pub output_directory: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    pub schema: String,
    pub collection_id: String,
    pub workers: Workers,
    pub logical_selection: Vec<Selection>,
    pub sources: Vec<Source>,
    pub batches: Vec<Batch>,
    pub research_ready: bool,
}

fn id(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 96
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
fn hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// Read only bounded JSON; plan bytes themselves are the reproducibility identity.
/// # Errors
/// Rejects unknown fields, nonregular input or invalid bounded partition.
pub fn read_plan(path: &Path) -> io::Result<(Plan, String)> {
    let bytes = read_limited(path, MAX_PLAN_BYTES).map_err(invalid)?;
    let plan: Plan = serde_json::from_slice(&bytes).map_err(invalid)?;
    plan.validate()?;
    Ok((plan, sha256(&bytes)))
}

impl Plan {
    /// Pure physical/logical partition validation; no acquisition or mutation.
    /// # Errors
    /// Fails on overlap, ordering, gaps, missing sources or reclassification.
    pub fn validate(&self) -> io::Result<()> {
        if self.schema != SCHEMA
            || !id(&self.collection_id)
            || self.research_ready
            || !hash(&self.workers.batch_decoder_sha256)
            || !hash(&self.workers.projector_sha256)
            || self.sources.is_empty()
            || self.sources.len() > 32
            || self.logical_selection.is_empty()
            || self.logical_selection.len() > MAX_COLLECTION_SLOTS
            || self.batches.is_empty()
            || self.batches.len() > MAX_COLLECTION_SLOTS
        {
            return Err(invalid("BATCH_PLAN_BOUNDARY"));
        }
        let mut sources = BTreeSet::new();
        for source in &self.sources {
            if !id(&source.source_id)
                || !sources.insert(source.source_id.as_str())
                || !source.run_root.is_absolute()
                || !id(&source.run_id)
            {
                return Err(invalid("BATCH_SOURCE_IDENTITY"));
            }
        }
        let mut previous = None;
        for slot in &self.logical_selection {
            if !sources.contains(slot.source_id.as_str())
                || previous.is_some_and(|p: u64| p.checked_add(1) != Some(slot.slot))
                || !matches!(
                    slot.role.as_str(),
                    "ORIGINAL_SELECTION" | "POSTHOC_DESCRIPTIVE_CONTEXT"
                )
            {
                return Err(invalid("BATCH_LOGICAL_SELECTION_GAP_OVERLAP_OR_ORDER"));
            }
            previous = Some(slot.slot);
        }
        // Explicit gaps are not silently filled: every selected slot appears
        // exactly once in the ordered physical partition, in the same order.
        let mut actual = Vec::new();
        let mut ids = BTreeSet::new();
        let mut outputs = BTreeSet::new();
        let mut receipts = BTreeSet::new();
        for batch in &self.batches {
            if !id(&batch.batch_id)
                || !ids.insert(&batch.batch_id)
                || !id(&batch.output_directory)
                || !outputs.insert(&batch.output_directory)
                || !sources.contains(batch.source_id.as_str())
                || batch.slots.is_empty()
                || batch.slots.len() > report::MAX_SELECTION_SLOTS
                || batch.slots.len() != batch.receipt_sequences.len()
                || batch.slots.windows(2).any(|w| w[0] >= w[1])
                || batch.receipt_sequences.windows(2).any(|w| w[0] >= w[1])
            {
                return Err(invalid("BATCH_PARTITION_BOUNDARY"));
            }
            for (&slot, &sequence) in batch.slots.iter().zip(&batch.receipt_sequences) {
                if sequence < 4 || !receipts.insert((&batch.source_id, sequence)) {
                    return Err(invalid("BATCH_OVERLAPPING_RECEIPT"));
                }
                actual.push((&batch.source_id, slot));
            }
        }
        if actual
            != self
                .logical_selection
                .iter()
                .map(|s| (&s.source_id, s.slot))
                .collect::<Vec<_>>()
        {
            return Err(invalid("BATCH_PARTITION_GAP_OR_OVERLAP"));
        }
        Ok(())
    }

    /// Audit original identities streaming, without decoding the whole run.
    /// # Errors
    /// Rejects changed source, missing native request, role or sample mismatch.
    pub fn validate_sources(&self) -> io::Result<()> {
        self.validate()?;
        for source in &self.sources {
            let actual = source_identity(&source.run_root)?;
            if actual["run_id"] != source.run_id
                || actual["bindings"] != source.bindings
                || actual["sample_identity"] != source.sample_identity
                || actual["run_root"] != json!(source.run_root)
            {
                return Err(invalid("BATCH_ORIGINAL_SOURCE_CHANGED"));
            }
            let run = read_run_context(&source.run_root)?;
            if let Some(sample) = &run.aggregate_plan.sample_identity {
                let selected = self
                    .logical_selection
                    .iter()
                    .filter(|s| s.source_id == source.source_id)
                    .map(|s| s.slot)
                    .collect::<Vec<_>>();
                if sample.end_slot_exclusive - sample.start_slot > MAX_COLLECTION_SLOTS as u64
                    || selected
                        != (sample.start_slot..sample.end_slot_exclusive).collect::<Vec<_>>()
                {
                    return Err(invalid("BATCH_INCOMPLETE_NATIVE_SAMPLE"));
                }
            }
            let prepared = run
                .prepared
                .as_ref()
                .ok_or_else(|| invalid("BATCH_SOURCE_METADATA_ONLY"))?;
            for batch in self
                .batches
                .iter()
                .filter(|b| b.source_id == source.source_id)
            {
                let mut bytes = 0_u64;
                for (&slot, &sequence) in batch.slots.iter().zip(&batch.receipt_sequences) {
                    let request = prepared
                        .requests()
                        .iter()
                        .find(|r| r.sequence == sequence)
                        .ok_or_else(|| invalid("BATCH_UNPLANNED_RECEIPT"))?;
                    let RequestKind::CarRange {
                        slot: request_slot,
                        start,
                        end_exclusive,
                        ..
                    } = request.kind
                    else {
                        return Err(invalid("BATCH_NON_PAYLOAD"));
                    };
                    if request_slot != slot {
                        return Err(invalid("BATCH_SLOT_RECEIPT_MISMATCH"));
                    }
                    bytes = bytes
                        .checked_add(
                            end_exclusive
                                .checked_sub(start)
                                .ok_or_else(|| invalid("BATCH_RANGE"))?,
                        )
                        .ok_or_else(|| invalid("BATCH_RAW_OVERFLOW"))?;
                    if let Some(sample) = &run.aggregate_plan.sample_identity {
                        let role = &self
                            .logical_selection
                            .iter()
                            .find(|s| s.source_id == source.source_id && s.slot == slot)
                            .ok_or_else(|| invalid("BATCH_SLOT_MISSING"))?
                            .role;
                        if slot < sample.start_slot
                            || slot >= sample.end_slot_exclusive
                            || role != "ORIGINAL_SELECTION"
                        {
                            return Err(invalid("BATCH_SAMPLE_RECLASSIFICATION"));
                        }
                    }
                }
                if bytes > report::MAX_SELECTION_RAW_BYTES as u64 {
                    return Err(invalid("BATCH_RAW_LIMIT"));
                }
            }
        }
        Ok(())
    }

    /// # Errors
    /// Rejects a batch not listed in this immutable plan.
    pub fn batch(&self, id: &str) -> io::Result<&Batch> {
        self.batches
            .iter()
            .find(|b| b.batch_id == id)
            .ok_or_else(|| invalid("UNPLANNED_BATCH"))
    }
    /// # Errors
    /// Rejects a source not listed in this immutable plan.
    pub fn source(&self, id: &str) -> io::Result<&Source> {
        self.sources
            .iter()
            .find(|s| s.source_id == id)
            .ok_or_else(|| invalid("UNPLANNED_SOURCE"))
    }
}

/// Stable full run identity, with genuine native sample metadata only.
/// # Errors
/// Existing immutable receipt reader must accept the actual source directory.
pub fn source_identity(root: &Path) -> io::Result<Value> {
    let root = fs::canonicalize(root)?;
    let run = read_run_context(&root)?;
    Ok(
        json!({"run_root":root,"run_id":run.snapshot.id,"bindings":bindings(&run)?,"sample_identity":run.aggregate_plan.sample_identity}),
    )
}

/// # Errors
/// Filesystem write failures never overwrite existing evidence.
pub fn write_new(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut file = OpenOptions::new().create_new(true).write(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}

fn now() -> io::Result<String> {
    Ok(SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(invalid)?
        .as_millis()
        .to_string())
}

/// Decode and publish one worker, or reuse only completely verified matching
/// immutable outputs. A partial directory is not deleted/repaired automatically.
/// # Errors
/// Any source/worker/plan/content mismatch stops; no acquisition is possible.
pub fn execute(plan_path: &Path, batch_id: &str, output: &Path) -> io::Result<Value> {
    let (plan, plan_hash) = read_plan(plan_path)?;
    plan.validate_sources()?;
    if current_executable_sha256().map_err(invalid)? != plan.workers.batch_decoder_sha256 {
        return Err(invalid("BATCH_DECODER_EXECUTABLE_MISMATCH"));
    }
    let batch = plan.batch(batch_id)?;
    let source = plan.source(&batch.source_id)?;
    if output.exists() {
        return verify_output(&plan, &plan_hash, batch, output);
    }
    let parent = fs::canonicalize(
        output
            .parent()
            .ok_or_else(|| invalid("BATCH_OUTPUT_PARENT"))?,
    )?;
    let output = parent.join(
        output
            .file_name()
            .ok_or_else(|| invalid("BATCH_OUTPUT_NAME"))?,
    );
    if plan.sources.iter().any(|s| output.starts_with(&s.run_root)) {
        return Err(invalid("BATCH_OUTPUT_INSIDE_SOURCE"));
    }
    let campaign = campaign_output(
        &plan,
        &plan_hash,
        &output,
        resources::MAX_PUBLICATION_BYTES as u64 + 1024 * 1024,
    )?;
    let start = now()?;
    let mut report = report::decode_receipts(&source.run_root, &batch.receipt_sequences)?;
    let plan_json = String::from_utf8(read_limited(plan_path, MAX_PLAN_BYTES).map_err(invalid)?)
        .map_err(invalid)?;
    if sha256(plan_json.as_bytes()) != plan_hash {
        return Err(invalid("BATCH_PLAN_CHANGED"));
    }
    let binding = json!({"schema":"OF1_BATCH_BINDING_1","plan_json":plan_json,"plan_sha256":plan_hash,"batch_id":batch.batch_id,"source_id":batch.source_id,"selected_slots":batch.slots,"receipt_sequences":batch.receipt_sequences,"original_bindings":source.bindings,"sample_identity":source.sample_identity,"source_run_id":source.run_id,"source_run_root":source.run_root,"logical_selection":plan.logical_selection,"workers":plan.workers});
    report["batch_binding"] = binding.clone();
    let quality = resources::bounded_json(&report, "QUALITY_JSON", resources::MAX_QUALITY_BYTES)?;
    let bronze = resources::bounded_jsonl(
        report["records"]
            .as_array()
            .ok_or_else(|| invalid("RECORDS"))?,
        resources::MAX_JSONL_BYTES,
    )?;
    let silver = resources::bounded_jsonl(
        report["silver_records"]
            .as_array()
            .ok_or_else(|| invalid("SILVER"))?,
        resources::MAX_JSONL_BYTES,
    )?;
    let html = report::html(&report);
    resources::check_size("QUALITY_HTML", html.len(), resources::MAX_HTML_BYTES)?;
    let mut execution = json!({"schema":"OF1_BRONZE_EXECUTION_1","decoder_source_sha256":crate::source_sha256(),"processed_at_unix_ms":start,"finished_at_unix_ms":now()?,"operational_timestamps_are_not_features":true,"executable_sha256":plan.workers.batch_decoder_sha256,"quality_sha256":sha256(&quality),"bronze_jsonl_sha256":sha256(&bronze),"silver_jsonl_sha256":sha256(&silver),"silver_fact_count":report["silver_records"].as_array().map(Vec::len),"html_sha256":sha256(html.as_bytes()),"lock_sha256":sha256(include_bytes!("../Cargo.lock")),"run_root":source.run_root,"no_acquisition_or_writer_resume":true,"batch_binding":binding});
    if let Some(sample) = report.get("sample_identity") {
        execution["sample_identity"] = sample.clone();
        execution["slice_class"] = report["slice_class"].clone();
    }
    let execution =
        resources::bounded_json(&execution, "EXECUTION_JSON", resources::MAX_EXECUTION_BYTES)?;
    let batch_bytes = serde_json::to_vec_pretty(&binding).map_err(invalid)?;
    resources::publication_bytes(
        &[
            quality.len(),
            bronze.len(),
            silver.len(),
            html.len(),
            execution.len(),
            batch_bytes.len(),
        ],
        resources::MAX_PUBLICATION_BYTES,
    )?;
    plan.validate_sources()?;
    if let Some((guard, sample)) = &campaign {
        guard.processing_tick(sample).map_err(invalid)?;
    }
    fs::create_dir(&output)?;
    for (name, bytes) in [
        ("quality.json", quality.as_slice()),
        ("bronze.jsonl", bronze.as_slice()),
        ("silver.jsonl", silver.as_slice()),
        ("quality.html", html.as_bytes()),
        ("execution.json", execution.as_slice()),
        ("batch.json", batch_bytes.as_slice()),
    ] {
        if let Some((guard, sample)) = &campaign {
            guard.processing_tick(sample).map_err(invalid)?;
        }
        write_new(&output.join(name), bytes)?;
    }
    fs::File::open(&output)?.sync_all()?;
    if let Some((guard, sample)) = &campaign {
        guard.processing_tick(sample).map_err(invalid)?;
    }
    write_new(&output.join("COMPLETE"), sha256(&quality).as_bytes())?;
    fs::File::open(&output)?.sync_all()?;
    fs::File::open(&parent)?.sync_all()?;
    verify_output(&plan, &plan_hash, batch, &output)
}

/// Verify each persisted artifact before resume. No timestamp or deadline reset.
/// # Errors
/// Rejects incomplete output, changed worker/plan/bytes or unexpected files.
pub fn verify_output(
    plan: &Plan,
    plan_hash: &str,
    batch: &Batch,
    output: &Path,
) -> io::Result<Value> {
    let mut names = fs::read_dir(output)?
        .map(|e| e.map(|e| e.file_name().to_string_lossy().to_string()))
        .collect::<io::Result<Vec<_>>>()?;
    names.sort();
    if names
        != [
            "COMPLETE",
            "batch.json",
            "bronze.jsonl",
            "execution.json",
            "quality.html",
            "quality.json",
            "silver.jsonl",
        ]
    {
        return Err(invalid("BATCH_PARTIAL_OR_UNEXPECTED_OUTPUT"));
    }
    let binding: Value = serde_json::from_slice(
        &read_limited(&output.join("batch.json"), MAX_PLAN_BYTES).map_err(invalid)?,
    )
    .map_err(invalid)?;
    let source = plan.source(&batch.source_id)?;
    if binding["plan_json"]
        .as_str()
        .is_none_or(|s| sha256(s.as_bytes()) != plan_hash)
    {
        return Err(invalid("BATCH_PLAN_BYTES_MISMATCH"));
    }
    if binding["plan_sha256"] != plan_hash
        || binding["batch_id"] != batch.batch_id
        || binding["source_id"] != batch.source_id
        || binding["selected_slots"] != json!(batch.slots)
        || binding["receipt_sequences"] != json!(batch.receipt_sequences)
        || binding["original_bindings"] != source.bindings
        || binding["sample_identity"] != source.sample_identity
        || binding["workers"] != json!(plan.workers)
        || binding["logical_selection"] != json!(plan.logical_selection)
        || binding["source_run_id"] != source.run_id
        || binding["source_run_root"] != json!(source.run_root)
    {
        return Err(invalid("BATCH_RESUME_BINDING_MISMATCH"));
    }
    let execution: Value = serde_json::from_slice(
        &read_limited(
            &output.join("execution.json"),
            resources::MAX_EXECUTION_BYTES as u64,
        )
        .map_err(invalid)?,
    )
    .map_err(invalid)?;
    if execution["batch_binding"] != binding
        || execution["decoder_source_sha256"] != crate::source_sha256()
        || execution["executable_sha256"] != plan.workers.batch_decoder_sha256
        || execution["lock_sha256"] != sha256(include_bytes!("../Cargo.lock"))
    {
        return Err(invalid("BATCH_RESUME_WORKER_MISMATCH"));
    }
    for (name, key, limit) in [
        (
            "quality.json",
            "quality_sha256",
            resources::MAX_QUALITY_BYTES,
        ),
        (
            "bronze.jsonl",
            "bronze_jsonl_sha256",
            resources::MAX_JSONL_BYTES,
        ),
        (
            "silver.jsonl",
            "silver_jsonl_sha256",
            resources::MAX_JSONL_BYTES,
        ),
        ("quality.html", "html_sha256", resources::MAX_HTML_BYTES),
    ] {
        if sha256(&read_limited(&output.join(name), limit as u64).map_err(invalid)?)
            != execution[key]
        {
            return Err(invalid(format!("BATCH_RESUME_ARTIFACT_MISMATCH:{name}")));
        }
    }
    if read_limited(&output.join("COMPLETE"), 64).map_err(invalid)?
        != execution["quality_sha256"]
            .as_str()
            .ok_or_else(|| invalid("BATCH_QUALITY_HASH"))?
            .as_bytes()
    {
        return Err(invalid("BATCH_COMPLETE_MISMATCH"));
    }
    Ok(
        json!({"batch_id":batch.batch_id,"state":"VERIFIED","batch_binding":binding,"execution":execution,"output":output}),
    )
}

/// One B7 window per collection; original pilot/context collections are unchanged.
/// # Errors
/// Mixed campaigns, windows, roles and paths cannot share a processing lease.
pub fn campaign_sample(
    plan: &Plan,
) -> io::Result<Option<(of1_range_recorder::sample::SampleIdentity, PathBuf)>> {
    let b7 = plan
        .sources
        .iter()
        .filter(|s| !s.sample_identity["b7"].is_null())
        .collect::<Vec<_>>();
    if b7.is_empty() {
        return Ok(None);
    }
    if plan.sources.len() != 1 || b7.len() != 1 {
        return Err(invalid("B7_SINGLE_WINDOW_COLLECTION_REQUIRED"));
    }
    let sample: of1_range_recorder::sample::SampleIdentity =
        serde_json::from_value(b7[0].sample_identity.clone()).map_err(invalid)?;
    sample.validate(978).map_err(invalid)?;
    Ok(Some((sample, b7[0].run_root.clone())))
}
/// # Errors
/// Guard must live through all writes; no unregistered B7 output path is accepted.
pub fn campaign_output(
    plan: &Plan,
    hash: &str,
    path: &Path,
    bytes: u64,
) -> io::Result<
    Option<(
        of1_range_recorder::campaign::Guard,
        of1_range_recorder::sample::SampleIdentity,
    )>,
> {
    campaign_sample(plan)?
        .map(|(sample, run)| {
            let guard =
                of1_range_recorder::campaign::Guard::output(&sample, &run, path, hash, bytes)
                    .map_err(invalid)?;
            Ok((guard, sample))
        })
        .transpose()
}
