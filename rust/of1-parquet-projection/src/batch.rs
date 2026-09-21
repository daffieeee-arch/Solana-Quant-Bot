//! Physical subset admission, never a new source/sample label or domain decoder.
use crate::{hash, invalid};
use serde_json::{Value, json};
use std::{collections::BTreeSet, io};

fn array<'a>(v: &'a Value, name: &str) -> io::Result<&'a [Value]> {
    v[name]
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| invalid("BATCH_ARRAY"))
}
fn slots(v: &Value) -> io::Result<Vec<u64>> {
    let values = v
        .as_array()
        .ok_or_else(|| invalid("BATCH_SELECTED_SLOTS"))?;
    values
        .iter()
        .map(|v| v.as_u64().ok_or_else(|| invalid("BATCH_SLOT_INTEGER")))
        .collect()
}

/// Revalidate a Rust worker's explicit subset against its full immutable source.
/// The entire original native sample stays attached; only physical coverage narrows.
/// # Errors
/// Unbound plans, changed source/sample, wrong receipts, or overlap stop projection.
pub fn selected(
    binding: &Value,
    execution: &Value,
    original_bindings: &Value,
    sample: &Value,
    payload: &Value,
) -> io::Result<Vec<u64>> {
    let raw = binding["plan_json"]
        .as_str()
        .ok_or_else(|| invalid("BATCH_PLAN_BYTES"))?;
    if raw.len() > 1_048_576
        || binding["schema"] != "OF1_BATCH_BINDING_1"
        || binding["plan_sha256"] != hash(raw.as_bytes())
        || execution["batch_binding"] != *binding
        || binding["original_bindings"] != *original_bindings
        || binding["sample_identity"] != *sample
        || binding["source_run_root"] != execution["run_root"]
    {
        return Err(invalid("BATCH_SOURCE_BINDING"));
    }
    let plan: Value = serde_json::from_str(raw).map_err(invalid)?;
    if plan["schema"] != "OF1_BATCH_COLLECTION_PLAN_1"
        || plan["research_ready"] != false
        || plan["logical_selection"] != binding["logical_selection"]
        || plan["workers"] != binding["workers"]
        || plan["workers"]["batch_decoder_sha256"] != execution["executable_sha256"]
    {
        return Err(invalid("BATCH_PLAN_BINDING"));
    }
    let sources = array(&plan, "sources")?;
    let batches = array(&plan, "batches")?;
    let logical = array(&plan, "logical_selection")?;
    if sources.is_empty()
        || sources.len() > 32
        || batches.is_empty()
        || batches.len() > 256
        || logical.is_empty()
        || logical.len() > 256
    {
        return Err(invalid("BATCH_PLAN_LIMIT"));
    }
    let mut source_ids = BTreeSet::new();
    for source in sources {
        let id = source["source_id"]
            .as_str()
            .ok_or_else(|| invalid("BATCH_SOURCE_ID"))?;
        if !source_ids.insert(id) {
            return Err(invalid("BATCH_DUPLICATE_SOURCE"));
        }
    }
    let source = sources
        .iter()
        .find(|s| s["source_id"] == binding["source_id"])
        .ok_or_else(|| invalid("BATCH_SOURCE_MISSING"))?;
    if source["bindings"] != *original_bindings
        || source["sample_identity"] != *sample
        || source["run_root"] != execution["run_root"]
        || source["run_id"] != binding["source_run_id"]
    {
        return Err(invalid("BATCH_ORIGINAL_SOURCE_MISMATCH"));
    }
    validate_partition(batches, logical, &source_ids)?;
    let batch = batches
        .iter()
        .find(|b| b["batch_id"] == binding["batch_id"])
        .ok_or_else(|| invalid("BATCH_ID_MISSING"))?;
    if batch["source_id"] != binding["source_id"]
        || batch["slots"] != binding["selected_slots"]
        || batch["receipt_sequences"] != binding["receipt_sequences"]
    {
        return Err(invalid("BATCH_SUBSET_MISMATCH"));
    }
    if !sample.is_null() {
        let start = sample["start_slot"]
            .as_u64()
            .ok_or_else(|| invalid("BATCH_SAMPLE_START"))?;
        let end = sample["end_slot_exclusive"]
            .as_u64()
            .ok_or_else(|| invalid("BATCH_SAMPLE_END"))?;
        let native: Vec<_> = logical
            .iter()
            .filter(|v| v["source_id"] == binding["source_id"])
            .collect();
        if end <= start
            || end - start > 256
            || native.iter().any(|v| v["role"] != "ORIGINAL_SELECTION")
            || native
                .iter()
                .map(|v| v["slot"].as_u64())
                .ne((start..end).map(Some))
        {
            return Err(invalid("BATCH_SAMPLE_SELECTION_RECLASSIFICATION"));
        }
    }
    selected_requests(binding, original_bindings, payload)
}

fn selected_requests(
    binding: &Value,
    original_bindings: &Value,
    payload: &Value,
) -> io::Result<Vec<u64>> {
    let selected = slots(&binding["selected_slots"])?;
    let sequences = slots(&binding["receipt_sequences"])?;
    let requests = array(&payload["prepared"], "requests")?;
    let mut total = 0_u64;
    for (&slot, sequence) in selected.iter().zip(sequences) {
        let request = requests
            .iter()
            .find(|r| r["sequence"] == sequence)
            .ok_or_else(|| invalid("BATCH_ORIGINAL_REQUEST"))?;
        if request["kind"]["kind"] != "CAR_RANGE" || request["kind"]["slot"] != slot {
            return Err(invalid("BATCH_ORIGINAL_SLOT_RECEIPT"));
        }
        let bound = array(original_bindings, "receipts")?
            .iter()
            .find(|r| r["sequence"] == sequence)
            .ok_or_else(|| invalid("BATCH_MISSING_ORIGINAL_RECEIPT"))?;
        let start = request["kind"]["start"]
            .as_u64()
            .ok_or_else(|| invalid("BATCH_RANGE_START"))?;
        let end = request["kind"]["end_exclusive"]
            .as_u64()
            .ok_or_else(|| invalid("BATCH_RANGE_END"))?;
        let bytes = end
            .checked_sub(start)
            .filter(|n| *n > 0)
            .ok_or_else(|| invalid("BATCH_RANGE"))?;
        if bound["raw_bytes"] != json!(bytes) {
            return Err(invalid("BATCH_RAW_LENGTH"));
        }
        total = total
            .checked_add(bytes)
            .ok_or_else(|| invalid("BATCH_RAW_OVERFLOW"))?;
    }
    if total > 16 * 1024 * 1024 {
        return Err(invalid("BATCH_RAW_LIMIT"));
    }
    Ok(selected)
}

fn validate_partition(
    batches: &[Value],
    logical: &[Value],
    source_ids: &BTreeSet<&str>,
) -> io::Result<()> {
    let mut partition = Vec::new();
    let mut ids = BTreeSet::new();
    let mut paths = BTreeSet::new();
    let mut receipts = BTreeSet::new();
    for batch in batches {
        let owner = batch["source_id"]
            .as_str()
            .ok_or_else(|| invalid("BATCH_OWNER"))?;
        let batch_slots = slots(&batch["slots"])?;
        let sequences = slots(&batch["receipt_sequences"])?;
        if !source_ids.contains(owner)
            || batch_slots.is_empty()
            || batch_slots.len() > 3
            || batch_slots.len() != sequences.len()
            || batch_slots.windows(2).any(|w| w[0] >= w[1])
            || sequences.windows(2).any(|w| w[0] >= w[1])
            || !ids.insert(
                batch["batch_id"]
                    .as_str()
                    .ok_or_else(|| invalid("BATCH_ID"))?,
            )
            || !paths.insert(
                batch["output_directory"]
                    .as_str()
                    .ok_or_else(|| invalid("BATCH_OUTPUT"))?,
            )
        {
            return Err(invalid("BATCH_PARTITION"));
        }
        for (slot, sequence) in batch_slots.iter().zip(sequences) {
            if sequence < 4 || !receipts.insert((owner, sequence)) {
                return Err(invalid("BATCH_RECEIPT_OVERLAP"));
            }
            partition.push((owner, *slot));
        }
    }
    let mut expected = Vec::new();
    for entry in logical {
        let owner = entry["source_id"]
            .as_str()
            .ok_or_else(|| invalid("BATCH_LOGICAL_SOURCE"))?;
        let slot = entry["slot"]
            .as_u64()
            .ok_or_else(|| invalid("BATCH_LOGICAL_SLOT"))?;
        if !source_ids.contains(owner)
            || expected
                .last()
                .is_some_and(|(_, previous): &(&str, u64)| previous.checked_add(1) != Some(slot))
            || !matches!(
                entry["role"].as_str(),
                Some("ORIGINAL_SELECTION" | "POSTHOC_DESCRIPTIVE_CONTEXT")
            )
        {
            return Err(invalid("BATCH_LOGICAL_ORDER_ROLE"));
        }
        expected.push((owner, slot));
    }
    if partition != expected {
        return Err(invalid("BATCH_PARTITION_GAP_OVERLAP"));
    }
    Ok(())
}
