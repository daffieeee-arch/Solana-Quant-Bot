//! Manifest-only collection publication. One bounded worker at a time; never a
//! collection-wide record JSON document or a filesystem glob.
use crate::{
    batch::{self, Batch, Plan},
    invalid, resources,
};
use of1_range_recorder::{acquisition::read_limited, sha256};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write as _,
    fs,
    io::{self, BufRead, BufReader, Read},
    path::Path,
};

fn json_file(path: &Path, limit: usize) -> io::Result<(Value, String)> {
    let bytes = read_limited(path, limit as u64).map_err(invalid)?;
    Ok((
        serde_json::from_slice(&bytes).map_err(invalid)?,
        sha256(&bytes),
    ))
}
fn number(v: &Value) -> io::Result<u64> {
    v.as_u64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .ok_or_else(|| invalid("COLLECTION_INTEGER"))
}
fn basename(v: &str) -> bool {
    !v.is_empty() && !v.contains(['/', '\\']) && v != "." && v != ".."
}

#[derive(Default)]
struct Logical {
    rows: u64,
    hash: String,
}
impl Logical {
    fn add(&mut self, bytes: &[u8]) -> io::Result<()> {
        // Ordered length-framed chain, independent of file/batch boundaries.
        // This is not presented as a physical-file SHA256.
        let mut framed = if self.rows == 0 {
            b"OF1_ORDERED_RECORD_CHAIN_1".to_vec()
        } else {
            hex::decode(&self.hash).map_err(invalid)?
        };
        framed.extend((bytes.len() as u64).to_le_bytes());
        framed.extend(bytes);
        self.hash = sha256(&framed);
        self.rows = self
            .rows
            .checked_add(1)
            .ok_or_else(|| invalid("COLLECTION_COUNT_OVERFLOW"))?;
        Ok(())
    }
    fn value(&self) -> Value {
        json!({"rows":self.rows,"ordered_logical_sha256":if self.rows==0 {sha256(b"OF1_ORDERED_RECORD_CHAIN_1")}else{self.hash.clone()},"logical_hash_algorithm":"OF1_ORDERED_RECORD_CHAIN_1"})
    }
}

fn rows(path: &Path, mut visit: impl FnMut(&[u8], &Value) -> io::Result<()>) -> io::Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.len() > resources::MAX_JSONL_BYTES as u64 {
        return Err(invalid("COLLECTION_JSONL_TYPE_SIZE"));
    }
    let mut reader = BufReader::new(fs::File::open(path)?);
    let mut row = Vec::new();
    loop {
        row.clear();
        let read = reader
            .by_ref()
            .take(resources::MAX_INDIVIDUAL_RECORD_BYTES as u64 + 1)
            .read_until(b'\n', &mut row)?;
        if read == 0 {
            break;
        }
        if row.len() > resources::MAX_INDIVIDUAL_RECORD_BYTES
            || row.pop() != Some(b'\n')
            || row.is_empty()
        {
            return Err(invalid("COLLECTION_RECORD_BOUNDARY"));
        }
        let value: Value = serde_json::from_slice(&row).map_err(invalid)?;
        if serde_json::to_vec(&value).map_err(invalid)? != row {
            return Err(invalid("COLLECTION_NONCANONICAL_RECORD"));
        }
        visit(&row, &value)?;
    }
    Ok(())
}

fn inspect_records(
    plan: &Plan,
    batch: &Batch,
    decode: &Path,
    bronze: &mut Logical,
    silver: &mut Logical,
) -> io::Result<BTreeMap<u64, BTreeMap<String, u64>>> {
    let source = plan.source(&batch.source_id)?;
    let mut parents = BTreeMap::new();
    let mut counts = BTreeMap::<u64, BTreeMap<String, u64>>::new();
    let mut previous = None;
    rows(&decode.join("bronze.jsonl"), |bytes, v| {
        let slot = number(&v["effective_at"]["slot"])?;
        let index = number(&v["effective_at"]["transaction_index_in_slot"])?;
        let receipt = number(&v["source"]["receipt_sequence"])?;
        if !batch
            .slots
            .iter()
            .zip(&batch.receipt_sequences)
            .any(|(&s, &r)| s == slot && r == receipt)
            || previous.is_some_and(|p| p > (slot, index))
            || v["source"]["run_id"] != source.run_id
            || v["source"]["bindings"] != source.bindings
            || v["sample_identity"] != source.sample_identity
            || v["decoder_source_sha256"] != crate::source_sha256()
        {
            return Err(invalid("COLLECTION_RECORD_SOURCE_OR_ORDER"));
        }
        if !source.sample_identity.is_null()
            && v["slice_class"] != source.sample_identity["sample_class"]
        {
            return Err(invalid("COLLECTION_RECORD_SAMPLE_RECLASSIFICATION"));
        }
        previous = Some((slot, index));
        if index != counts.get(&slot).map_or(0, |c| c.values().sum()) {
            return Err(invalid("COLLECTION_PACKAGE_INDEX_GAP_OR_DUPLICATE"));
        }
        let disposition = v["disposition"]
            .as_str()
            .ok_or_else(|| invalid("COLLECTION_DISPOSITION"))?;
        if !matches!(
            disposition,
            "DECODED" | "MISSING" | "UNSUPPORTED" | "QUARANTINED"
        ) {
            return Err(invalid("COLLECTION_UNKNOWN_DISPOSITION"));
        }
        *counts
            .entry(slot)
            .or_default()
            .entry(disposition.into())
            .or_default() += 1;
        if parents.len() >= 16_384 {
            return Err(invalid("COLLECTION_PARENT_WORKER_LIMIT"));
        }
        parents.insert(sha256(bytes), (slot, index));
        bronze.add(bytes)
    })?;
    let mut previous = None;
    rows(&decode.join("silver.jsonl"), |bytes, v| {
        let slot = number(&v["effective_at"]["slot"])?;
        let index = number(&v["effective_at"]["transaction_index_in_slot"])?;
        let parent = v["bronze_record_sha256"]
            .as_str()
            .ok_or_else(|| invalid("COLLECTION_SILVER_PARENT"))?;
        if parents.get(parent) != Some(&(slot, index))
            || previous.is_some_and(|p| p > (slot, index))
            || v["sample_identity"] != source.sample_identity
        {
            return Err(invalid("COLLECTION_SILVER_PARENT_OR_ORDER"));
        }
        previous = Some((slot, index));
        silver.add(bytes)
    })?;
    Ok(counts)
}

fn parquet(
    plan: &Plan,
    batch: &Batch,
    path: &Path,
    execution: &Value,
    quality: &Value,
) -> io::Result<(Value, String)> {
    let (manifest, hash) = json_file(
        &path.join("manifest.json"),
        usize::try_from(batch::MAX_PLAN_BYTES).map_err(invalid)?,
    )?;
    let complete = read_limited(&path.join("COMPLETE"), 65).map_err(invalid)?;
    if complete != format!("{hash}\n").as_bytes()
        || manifest["input"]["execution"] != *execution
        || manifest["selection"]["selected_slots"] != json!(batch.slots)
        || manifest["sample_identity"] != quality["sample_identity"]
        || manifest["selection"]["all_expected_packages_accounted"] != true
        || manifest["selection"]["status"] != "ACCOUNTED"
    {
        return Err(invalid("COLLECTION_PARQUET_INPUT_OR_SELECTION"));
    }
    if manifest["writer"]["executable_sha256"] != plan.workers.projector_sha256 {
        return Err(invalid("COLLECTION_PROJECTOR_BINARY_MISMATCH"));
    }
    let files = manifest["files"]
        .as_object()
        .ok_or_else(|| invalid("COLLECTION_PARQUET_FILES"))?;
    let mut names = BTreeSet::new();
    for layer in ["bronze", "silver"] {
        let input_name = format!("{layer}.jsonl");
        let hash_key = format!("{layer}_jsonl_sha256");
        if manifest["layers"][layer]["reconstructed_jsonl_sha256"] != execution[&hash_key]
            || manifest["input"]["files"][&input_name]["sha256"] != execution[&hash_key]
            || manifest["layers"][layer]["reconstructed_jsonl_bytes"]
                != manifest["input"]["files"][&input_name]["bytes"]
        {
            return Err(invalid("COLLECTION_JSONL_PARQUET_HASH_PARITY"));
        }
        let mut ordinal = 0;
        for (index, name) in manifest["layers"][layer]["files"]
            .as_array()
            .ok_or_else(|| invalid("COLLECTION_LAYER_FILES"))?
            .iter()
            .enumerate()
        {
            let name = name
                .as_str()
                .ok_or_else(|| invalid("COLLECTION_FILE_NAME"))?;
            if !basename(name) || !names.insert(name) {
                return Err(invalid("COLLECTION_DUPLICATE_OR_UNSAFE_FILE"));
            }
            let file = files
                .get(name)
                .ok_or_else(|| invalid("COLLECTION_MISSING_FILE_ENTRY"))?;
            let bytes = read_limited(&path.join(name), 64 * 1024 * 1024).map_err(invalid)?;
            let rows = number(&file["audit"]["rows"])?;
            if file["layer"] != layer
                || number(&file["shard_index"])? != index as u64
                || number(&file["ordinal_start"])? != ordinal
                || number(&file["ordinal_end_exclusive"])? != ordinal + rows
                || rows > 5000
                || file["sha256"] != sha256(&bytes)
                || number(&file["bytes"])? != bytes.len() as u64
                || file["audit"]["typed_column_parity"] != "EXACT"
            {
                return Err(invalid("COLLECTION_SHARD_IDENTITY_OR_ORDER"));
            }
            ordinal += rows;
        }
        if number(&manifest["layers"][layer]["rows"])? != ordinal {
            return Err(invalid("COLLECTION_LAYER_COUNT"));
        }
    }
    if names.len() != files.len() {
        return Err(invalid("COLLECTION_UNLISTED_FILE"));
    }
    let slots = manifest["selection"]["slots"]
        .as_array()
        .ok_or_else(|| invalid("COLLECTION_SELECTED_SLOT_SUMMARY"))?;
    if slots.len() != batch.slots.len()
        || slots.iter().zip(&batch.slots).any(|(s, slot)| {
            number(&s["slot"]).ok() != Some(*slot)
                || s["accounted"] != true
                || s["duplicate_package_identity"] != false
                || s["expected_packages"] != s["present_packages"]
        })
    {
        return Err(invalid("COLLECTION_SELECTED_SLOT_NOT_ACCOUNTED"));
    }
    Ok((manifest, hash))
}

/// Read-only audit. Missing workers remain PENDING, not complete collections.
/// # Errors
/// Changed source, partial output, corrupt shards or conflicting parents stop.
pub fn inspect(plan_path: &Path, root: &Path) -> io::Result<Value> {
    let (plan, plan_hash) = batch::read_plan(plan_path)?;
    plan.validate_sources()?;
    let root = fs::canonicalize(root)?;
    let mut bronze = Logical::default();
    let mut silver = Logical::default();
    let mut batches = Vec::new();
    let mut outcomes = Vec::new();
    let mut complete = true;
    for batch in &plan.batches {
        let base = root.join(&batch.output_directory);
        let decode = base.join("decode");
        let projected = base.join("parquet");
        let mut entry = json!({"batch_id":batch.batch_id,"source_id":batch.source_id,"selected_slots":batch.slots,"state":"PENDING","decode_directory":format!("{}/decode",batch.output_directory),"parquet_manifest_path":format!("{}/parquet/manifest.json",batch.output_directory),"parquet_manifest_sha256":null,"bronze_ordinal_start":bronze.rows,"silver_ordinal_start":silver.rows,"slots":[]});
        if !decode.exists() || !projected.exists() {
            if projected.exists() {
                return Err(invalid("COLLECTION_PARQUET_WITHOUT_DECODE"));
            }
            if decode.exists() {
                batch::verify_output(&plan, &plan_hash, batch, &decode)?;
                entry["pending_reason"] = json!("DECODE_VERIFIED_PROJECTION_PENDING");
            } else {
                entry["pending_reason"] = json!("BATCH_NOT_EXECUTED");
            }
            complete = false;
            for slot in &batch.slots {
                let selection = plan
                    .logical_selection
                    .iter()
                    .find(|s| s.slot == *slot)
                    .ok_or_else(|| invalid("COLLECTION_SLOT"))?;
                outcomes.push(json!({"source_id":batch.source_id,"slot":slot,"role":selection.role,"state":"PENDING","transaction_envelopes":null,"dispositions":null}));
            }
        } else {
            let checked = batch::verify_output(&plan, &plan_hash, batch, &decode)?;
            let quality = json_file(&decode.join("quality.json"), resources::MAX_QUALITY_BYTES)?.0;
            let (manifest, manifest_hash) =
                parquet(&plan, batch, &projected, &checked["execution"], &quality)?;
            let bronze_before = bronze.rows;
            let silver_before = silver.rows;
            let counts = inspect_records(&plan, batch, &decode, &mut bronze, &mut silver)?;
            if bronze.rows - bronze_before != number(&manifest["layers"]["bronze"]["rows"])?
                || silver.rows - silver_before != number(&manifest["layers"]["silver"]["rows"])?
            {
                return Err(invalid("COLLECTION_ROW_PARITY"));
            }
            let summaries = quality["slots"]
                .as_array()
                .cloned()
                .unwrap_or_else(|| vec![quality.clone()]);
            let mut summaries_out = Vec::new();
            for slot in &batch.slots {
                let summary = summaries
                    .iter()
                    .find(|s| number(&s["slot"]).ok() == Some(*slot))
                    .ok_or_else(|| invalid("COLLECTION_UNACCOUNTED_SLOT"))?;
                let actual = counts.get(slot).cloned().unwrap_or_default();
                if actual.values().sum::<u64>() != number(&summary["transaction_envelopes"])? {
                    return Err(invalid("COLLECTION_ENVELOPE_PARITY"));
                }
                for kind in ["DECODED", "MISSING", "UNSUPPORTED", "QUARANTINED"] {
                    if actual.get(kind).copied().unwrap_or(0)
                        != number(&summary["dispositions"][kind])?
                    {
                        return Err(invalid("COLLECTION_DISPOSITION_PARITY"));
                    }
                }
                let selection = plan
                    .logical_selection
                    .iter()
                    .find(|s| s.slot == *slot)
                    .ok_or_else(|| invalid("COLLECTION_SLOT"))?;
                let value = json!({"source_id":batch.source_id,"slot":slot,"role":selection.role,"state":"ACCOUNTED","transaction_envelopes":summary["transaction_envelopes"],"dispositions":summary["dispositions"],"reasons":summary["reasons"],"silver_fact_count":summary["silver_fact_count"],"receipt_evidence":summary["receipt_evidence"],"raw_sha256":summary["raw_sha256"]});
                outcomes.push(value.clone());
                summaries_out.push(value);
            }
            entry["state"] = json!("VERIFIED");
            entry["parquet_manifest_sha256"] = json!(manifest_hash);
            entry["slots"] = json!(summaries_out);
        }
        entry["bronze_ordinal_end_exclusive"] = json!(bronze.rows);
        entry["silver_ordinal_end_exclusive"] = json!(silver.rows);
        batches.push(entry);
    }
    plan.validate_sources()?;
    Ok(
        json!({"schema":"OF1_BATCH_COLLECTION_1","plan_sha256":plan_hash,"plan":plan,"state":if complete{"COMPLETE"}else{"INCOMPLETE"},"research_ready":false,"root_to_slot_membership":"UNAVAILABLE","batches":batches,"slot_outcomes":outcomes,"layers":{"bronze":bronze.value(),"silver":silver.value()},"completeness":{"all_selected_slots_accounted":complete,"successful_decoding_separate":true,"research_suitability":"NOT_ESTABLISHED"},"collector_source_sha256":crate::source_sha256()}),
    )
}

/// Bounded existing-diagnostic presentation, under the caller's campaign guard.
/// Reserved evaluation retains only integrity metadata in this visible report.
/// # Errors
/// Original collection verification must succeed before publication.
pub fn campaign_report(
    plan: &Plan,
    root: &Path,
    manifest: &Value,
    accounting: &Value,
) -> io::Result<(Vec<u8>, Vec<u8>)> {
    let (sample, _) = batch::campaign_sample(plan)?.ok_or_else(|| invalid("B7_REQUIRED"))?;
    let role = &sample
        .b7
        .as_ref()
        .ok_or_else(|| invalid("B7_REQUIRED"))?
        .cohort_role;
    let mut slots = Vec::new();
    if role == "DEVELOPMENT" {
        for b in &plan.batches {
            let quality = json_file(
                &root.join(&b.output_directory).join("decode/quality.json"),
                resources::MAX_QUALITY_BYTES,
            )?
            .0;
            slots.push(json!({"slots":b.slots,"transaction_status_counts":quality["analysis"]["transaction_status_counts"],
                "pump_layout_outcomes":quality["analysis"]["pump_layout_outcomes"],"silver_fact_count":quality["silver_fact_count"],
                "dispositions":quality["dispositions"],"diagnostic_report":format!("{}/decode/quality.html",b.output_directory)}));
        }
    }
    let value = json!({"schema":"OF1_B7_WINDOW_REPORT_1","sample_identity":sample,
        "collection_manifest_sha256":sha256(&read_limited(&root.join("collection.json"),batch::MAX_PLAN_BYTES).map_err(invalid)?),
        "state":manifest["state"],"coverage":manifest["completeness"],"campaign_accounting":accounting,
        "layers":if role=="DEVELOPMENT"{manifest["layers"].clone()}else{Value::Null},"slot_diagnostics":slots,
        "reserved_evaluation_outcomes_visible":false,"research_ready":false,
        "limits":"Window feasibility evidence only; native sample identity is not research sufficiency. Missing is not zero; Bronze accounting is not full Pump coverage."});
    let raw = serde_json::to_vec_pretty(&value).map_err(invalid)?;
    let escape = |s: &str| {
        s.replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
            .replace('\"', "&quot;")
    };
    let mut rows = String::new();
    for v in value["slot_diagnostics"]
        .as_array()
        .ok_or_else(|| invalid("B7_DIAGNOSTICS"))?
    {
        write!(&mut rows,"<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td><a href=\"{}\">Rust-diagnose</a></td></tr>",
            escape(&v["slots"].to_string()),escape(&v["transaction_status_counts"].to_string()),escape(&v["dispositions"].to_string()),
            escape(&v["silver_fact_count"].to_string()),escape(v["diagnostic_report"].as_str().ok_or_else(||invalid("B7_LINK"))?)).map_err(invalid)?;
    }
    let html = format!(
        "<!doctype html><html lang=nl><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'><title>B7 venster</title><style>body{{font:16px system-ui;margin:2rem;max-width:1100px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #888;padding:.5rem;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style><h1>B7 · één brongebonden venster</h1><p>Research Ready: false. RESEARCH_SAMPLING is een selectie-identiteit, geen voldoende onderzoekssample. Bronze-dekking, transactiesucces en Silver-toelating zijn afzonderlijk.</p><p><a href=campaign-report.json>JSON</a> · <a href=collection.json>Geverifieerd manifest</a></p><table><caption>Oorspronkelijke Rust-diagnoses per batch</caption><tr><th>Slots</th><th>Transactiestatus</th><th>Bronze-uitkomsten</th><th>Silver-feiten</th><th>Bronnen</th></tr>{rows}</table><details open><summary>Identiteit, aantallen, werkelijk geboekt verbruik en beperkingen</summary><pre>{}</pre></details></html>",
        escape(std::str::from_utf8(&raw).map_err(invalid)?)
    );
    if raw.len() + html.len() > 1024 * 1024 {
        return Err(invalid("B7_REPORT_LIMIT"));
    }
    Ok((raw, html.into_bytes()))
}
