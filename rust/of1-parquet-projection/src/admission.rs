//! Seal and inventory checks without collecting the record stream. No domain decode.
use crate::{
    columns::{self, Layer},
    hash, invalid, shards, storage,
};
use serde::{
    Deserialize, Deserializer,
    de::{IgnoredAny, MapAccess, Visitor},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    fs::File,
    io::{self, BufReader, Read},
    path::Path,
};

/// Skip the duplicate full record arrays in the bounded Rust quality report.
/// This is serde's JSON parser, not an alternative JSON or protocol parser.
struct Summary(Value);
impl<'de> Deserialize<'de> for Summary {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct MapVisitor;
        impl<'de> Visitor<'de> for MapVisitor {
            type Value = Summary;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("Rust quality object")
            }
            fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Self::Value, M::Error> {
                let mut out = serde_json::Map::new();
                let mut keys = BTreeSet::new();
                while let Some(key) = map.next_key::<String>()? {
                    if !keys.insert(key.clone()) {
                        return Err(serde::de::Error::custom("DUPLICATE_QUALITY_KEY"));
                    }
                    if key == "records" || key == "silver_records" {
                        map.next_value::<IgnoredAny>()?;
                    } else {
                        out.insert(key, map.next_value()?);
                    }
                }
                Ok(Summary(Value::Object(out)))
            }
        }
        deserializer.deserialize_map(MapVisitor)
    }
}
/// Stream-hash the unchanged, bounded sealed files. Publication marker is not decode success.
/// # Errors
/// Missing files or wrong original execution/seal hashes are hard failures.
pub fn seal(input: &Path, execution_sha: &str) -> io::Result<Value> {
    let bytes = storage::bounded_read(&input.join("execution.json"))?;
    if hash(&bytes) != execution_sha {
        return Err(invalid("EXECUTION_RECEIPT_HASH_MISMATCH"));
    }
    let execution: Value = serde_json::from_slice(&bytes).map_err(invalid)?;
    if execution["schema"] != "OF1_BRONZE_EXECUTION_1" {
        return Err(invalid("EXECUTION_SCHEMA"));
    }
    let mut files = BTreeMap::new();
    if !execution["batch_binding"].is_null() {
        let bytes = storage::bounded_read(&input.join("batch.json"))?;
        let binding: Value = serde_json::from_slice(&bytes).map_err(invalid)?;
        if binding != execution["batch_binding"] {
            return Err(invalid("BATCH_EXECUTION_BINDING"));
        }
        let executable = std::env::current_exe()?;
        if binding["workers"]["projector_sha256"]
            != shards::file_hash(&executable, crate::MAX_EXECUTABLE_BYTES)?.0
        {
            return Err(invalid("BATCH_PROJECTOR_EXECUTABLE_MISMATCH"));
        }
        files.insert(
            "batch.json",
            json!({"sha256":hash(&bytes),"bytes":bytes.len()}),
        );
    } else if input.join("batch.json").exists() {
        return Err(invalid("UNBOUND_BATCH_FILE"));
    }
    for (name, key) in [
        ("bronze.jsonl", "bronze_jsonl_sha256"),
        ("silver.jsonl", "silver_jsonl_sha256"),
        ("quality.json", "quality_sha256"),
        ("quality.html", "html_sha256"),
    ] {
        let (sha, size) = shards::file_hash(&input.join(name), storage::MAX_FILE_BYTES)?;
        if execution[key] != sha {
            return Err(invalid(format!("INPUT_HASH_MISMATCH {name}")));
        }
        files.insert(name, json!({"sha256":sha,"bytes":size}));
    }
    let complete = storage::bounded_read(&input.join("COMPLETE"))?;
    if String::from_utf8_lossy(&complete).trim()
        != execution["quality_sha256"].as_str().unwrap_or("")
    {
        return Err(invalid("INPUT_INCOMPLETE"));
    }
    Ok(
        json!({"execution_sha256":execution_sha,"execution":execution,"complete_sha256":hash(&complete),"files":files}),
    )
}
fn number(v: &Value) -> io::Result<u64> {
    v.as_u64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .ok_or_else(|| invalid("INVENTORY_INTEGER"))
}
fn bound_json(path: &Path, sha: &Value) -> io::Result<Value> {
    let bytes = storage::bounded_read(path)?;
    if sha.as_str() != Some(hash(&bytes).as_str()) {
        return Err(invalid("ORIGINAL_RUN_BINDING"));
    }
    serde_json::from_slice(&bytes).map_err(invalid)
}
struct Source {
    sample: Value,
    class: String,
    receipt: Value,
    bindings: Value,
    selected: Vec<u64>,
    synthetic: bool,
}
fn receipt_evidence(
    runroot: &Path,
    run: &Value,
    payload: &Value,
    bindings: &Value,
) -> io::Result<Value> {
    let mode = run["metadata_lease"]["authority"]["mode"]
        .as_str()
        .ok_or_else(|| invalid("ORIGINAL_AUTHORITY"))?;
    if payload["lease"]["authority"]["mode"] != mode {
        return Err(invalid("MIXED_ORIGINAL_AUTHORITY"));
    }
    let evidence = match mode {
        "FIXTURE" => "Fixture",
        "APPROVED" => "UNREVIEWED_AUTHENTIC_RAW",
        _ => return Err(invalid("ORIGINAL_AUTHORITY")),
    };
    let receipts = bindings["receipts"]
        .as_array()
        .ok_or_else(|| invalid("BOUND_RECEIPTS_REQUIRED"))?;
    if receipts.is_empty() || receipts.len() > 256 {
        return Err(invalid("BOUND_RECEIPTS_LIMIT"));
    }
    let mut seen = BTreeSet::new();
    for item in receipts {
        let sequence = item["sequence"]
            .as_u64()
            .ok_or_else(|| invalid("RECEIPT_SEQUENCE"))?;
        let name = format!("published/{sequence:010}/receipt.json");
        if item["path"] != name || !seen.insert(sequence) {
            return Err(invalid("BOUND_RECEIPT_PATH"));
        }
        let receipt = bound_json(&runroot.join(name), &item["sha256"])?;
        if receipt["aggregate_sha256"] != bindings["aggregate_sha256"]
            || receipt["evidence"] != evidence
            || receipt["sha256"] != item["raw_sha256"]
            || receipt["response_entity_bytes"] != item["raw_bytes"]
        {
            return Err(invalid("ORIGINAL_RECEIPT_EVIDENCE_BINDING"));
        }
    }
    Ok(json!(evidence))
}
fn source(quality: &Value, execution: &Value) -> io::Result<Source> {
    if let Some(runroot) = execution["run_root"].as_str() {
        let runroot = Path::new(runroot);
        let bindings = &quality["bindings"];
        let run = bound_json(&runroot.join("run.json"), &bindings["manifest_sha256"])?;
        let payload = bound_json(
            &runroot.join("payload.json"),
            &bindings["payload_manifest_sha256"],
        )?;
        if run["aggregate_sha256"] != bindings["aggregate_sha256"] {
            return Err(invalid("ORIGINAL_AGGREGATE_BINDING"));
        }
        let evidence = receipt_evidence(runroot, &run, &payload, bindings)?;
        if quality["receipt_evidence"] != evidence {
            return Err(invalid("EVIDENCE_RECLASSIFICATION"));
        }
        let start = number(&payload["prepared"]["start_slot"])?;
        let end = number(&payload["prepared"]["end_slot"])?;
        if end <= start || end - start > 128 {
            return Err(invalid("SELECTED_SLOT_LIMIT"));
        }
        let sample = run["plan"]["sample_identity"].clone();
        let class = if sample.is_null() {
            "ENGINEERING_VALIDATION_ONLY"
        } else {
            "RESEARCH_SAMPLING"
        };
        if quality["slice_class"] != class || quality["sample_identity"] != sample {
            return Err(invalid("QUALITY_SAMPLE_RECLASSIFICATION"));
        }
        if sample.is_null() {
            if !execution["sample_identity"].is_null()
                || execution["slice_class"]
                    .as_str()
                    .is_some_and(|s| s != class)
            {
                return Err(invalid("EXECUTION_SAMPLE_RECLASSIFICATION"));
            }
        } else {
            if execution["sample_identity"] != sample
                || execution["slice_class"] != class
                || bindings["sample_identity"] != sample
            {
                return Err(invalid("SAMPLE_EXECUTION_SOURCE_BINDING"));
            }
            validate_sample(&sample)?;
            if sample["start_slot"] != start || sample["end_slot_exclusive"] != end {
                return Err(invalid("SAMPLE_SELECTED_WINDOW_BINDING"));
            }
        }
        let selected = if quality["batch_binding"].is_null() {
            if !execution["batch_binding"].is_null() {
                return Err(invalid("BATCH_QUALITY_BINDING"));
            }
            (start..end).collect()
        } else {
            if quality["batch_binding"]["source_run_id"] != run["run_id"] {
                return Err(invalid("BATCH_ORIGINAL_RUN_ID"));
            }
            crate::batch::selected(
                &quality["batch_binding"],
                execution,
                bindings,
                &sample,
                &payload,
            )?
        };
        Ok(Source {
            sample,
            class: class.into(),
            receipt: evidence,
            bindings: bindings.clone(),
            selected,
            synthetic: false,
        })
    } else {
        synthetic_source(quality, execution)
    }
}
fn synthetic_source(quality: &Value, execution: &Value) -> io::Result<Source> {
    // Explicit synthetic writer fixtures are useful, never an authentic sample bypass.
    if quality["input_kind"] != "FIXTURE_SYNTHETIC_SHARD_TEST"
        || quality["receipt_evidence"] != "Fixture"
        || quality["slice_class"] != "ENGINEERING_VALIDATION_ONLY"
        || !quality["sample_identity"].is_null()
        || !execution["sample_identity"].is_null()
    {
        return Err(invalid("ORIGINAL_RUN_OR_EXPLICIT_FIXTURE_REQUIRED"));
    }
    let selected = quality["selected_slots"]
        .as_array()
        .ok_or_else(|| invalid("SELECTED_SLOTS"))?
        .iter()
        .map(number)
        .collect::<io::Result<Vec<_>>>()?;
    if selected.is_empty() || selected.len() > 128 || selected.windows(2).any(|w| w[0] >= w[1]) {
        return Err(invalid("SELECTED_SLOTS"));
    }
    Ok(Source {
        sample: Value::Null,
        class: "ENGINEERING_VALIDATION_ONLY".into(),
        receipt: json!("Fixture"),
        bindings: Value::Null,
        selected,
        synthetic: true,
    })
}
/// Exact frozen proposal identity; this verifies identity, not acquisition authorization.
/// # Errors
/// Rejects caller-supplied labels, unknown selections, changed seeds or post-hoc plans.
pub fn validate_sample(sample: &Value) -> io::Result<()> {
    let identity: of1_range_recorder::sample::SampleIdentity =
        serde_json::from_value(sample.clone()).map_err(invalid)?;
    identity.validate(978).map_err(invalid)
}
fn record_binding(record: &Value, source: &Source) -> io::Result<()> {
    if record["slice_class"] != source.class || record["sample_identity"] != source.sample {
        return Err(invalid("RECORD_SAMPLE_RECLASSIFICATION"));
    }
    if record["receipt_evidence"] != source.receipt
        || (source.synthetic && record["input_kind"] != "FIXTURE_SYNTHETIC_SHARD_TEST")
    {
        return Err(invalid("RECORD_EVIDENCE_RECLASSIFICATION"));
    }
    if !source.synthetic
        && (record["source"]["bindings"] != source.bindings
            || record["receipt_evidence"] != source.receipt)
    {
        return Err(invalid("RECORD_ORIGINAL_RUN_BINDING"));
    }
    if !source.sample.is_null() && record["source"]["bindings"]["sample_identity"] != source.sample
    {
        return Err(invalid("RECORD_SAMPLE_SOURCE_BINDING"));
    }
    Ok(())
}
#[derive(Default)]
struct Slot {
    present: u64,
    indices: BTreeSet<u64>,
    duplicates: bool,
    outcomes: BTreeMap<String, u64>,
}
/// Scan records and retain only bounded parent fingerprints and slot accounting.
/// # Errors
/// Bad seals, unselected rows, inconsistent parents or attempted reclassification stop.
pub fn inspect(input: &Path, seal: &Value) -> io::Result<Value> {
    let mut deserializer = serde_json::Deserializer::from_reader(BufReader::new(
        File::open(input.join("quality.json"))?.take(storage::MAX_FILE_BYTES + 1),
    ));
    let quality = Summary::deserialize(&mut deserializer).map_err(invalid)?.0;
    deserializer.end().map_err(invalid)?;
    let source = source(&quality, &seal["execution"])?;
    let slot_reports = if let Some(slots) = quality["slots"].as_array() {
        slots.clone()
    } else if !quality["slot"].is_null() {
        vec![quality.clone()]
    } else {
        Vec::new()
    };
    let mut expected = BTreeMap::new();
    for slot in slot_reports {
        let index = number(&slot["slot"])?;
        if !source.selected.contains(&index) || expected.insert(index, slot).is_some() {
            return Err(invalid("QUALITY_SLOT_INVENTORY"));
        }
    }
    let mut parents = BTreeMap::new();
    let mut slots = BTreeMap::<u64, Slot>::new();
    let mut rows = 0;
    let mut reader = BufReader::new(File::open(input.join("bronze.jsonl"))?);
    while let Some(line) = shards::read_line(&mut reader)? {
        rows += 1;
        if rows > storage::MAX_ROWS * shards::MAX_SHARDS {
            return Err(invalid("DATASET_ROW_LIMIT"));
        }
        let v = columns::parse_record(Layer::Bronze, &line)?;
        record_binding(&v, &source)?;
        let index = number(&v["effective_at"]["slot"])?;
        if !source.selected.contains(&index) {
            return Err(invalid("UNSELECTED_RECORD"));
        }
        let slot = slots.entry(index).or_default();
        slot.present += 1;
        let tx = number(&v["effective_at"]["transaction_index_in_slot"])?;
        if slot.indices.len() >= shards::MAX_PARENT_BINDINGS && !slot.indices.contains(&tx) {
            return Err(invalid("PACKAGE_INDEX_LIMIT"));
        }
        if !slot.indices.insert(tx) {
            slot.duplicates = true;
        }
        let disposition = v["disposition"]
            .as_str()
            .ok_or_else(|| invalid("ENVELOPE_OUTCOME_REQUIRED"))?;
        if !["DECODED", "MISSING", "UNSUPPORTED", "QUARANTINED"].contains(&disposition) {
            return Err(invalid("ENVELOPE_OUTCOME_UNKNOWN"));
        }
        *slot.outcomes.entry(disposition.into()).or_default() += 1;
        let key = hash(&line[..line.len() - 1]);
        let binding = (
            hash(&serde_json::to_vec(&v["effective_at"]).map_err(invalid)?),
            hash(&serde_json::to_vec(&v["source"]).map_err(invalid)?),
        );
        if parents.len() >= shards::MAX_PARENT_BINDINGS && !parents.contains_key(&key) {
            return Err(invalid("PARENT_BINDING_LIMIT"));
        }
        if parents
            .insert(key, binding.clone())
            .is_some_and(|old| old != binding)
        {
            return Err(invalid("PARENT_HASH_CONFLICT"));
        }
    }
    check_silver(input, &source, &parents)?;
    let mut result = inventory(&source, &expected, slots);
    if let Some(binding) = quality.get("batch_binding") {
        result["batch_binding"] = binding.clone();
    }
    Ok(result)
}
fn check_silver(
    input: &Path,
    source: &Source,
    parents: &BTreeMap<String, (String, String)>,
) -> io::Result<()> {
    let mut reader = BufReader::new(File::open(input.join("silver.jsonl"))?);
    let mut facts = 0;
    while let Some(line) = shards::read_line(&mut reader)? {
        facts += 1;
        if facts > storage::MAX_ROWS * shards::MAX_SHARDS {
            return Err(invalid("DATASET_ROW_LIMIT"));
        }
        let v = columns::parse_record(Layer::Silver, &line)?;
        record_binding(&v, source)?;
        let key = v["bronze_record_sha256"]
            .as_str()
            .ok_or_else(|| invalid("SILVER_PARENT_REQUIRED"))?;
        let binding = (
            hash(&serde_json::to_vec(&v["effective_at"]).map_err(invalid)?),
            hash(&serde_json::to_vec(&v["source"]).map_err(invalid)?),
        );
        if parents.get(key) != Some(&binding) {
            return Err(invalid("SILVER_PARENT_BINDING_MISMATCH"));
        }
    }
    Ok(())
}
fn inventory(
    source: &Source,
    expected: &BTreeMap<u64, Value>,
    mut slots: BTreeMap<u64, Slot>,
) -> Value {
    let mut total_outcomes = BTreeMap::<String, u64>::new();
    let mut inventory = Vec::new();
    for selected in &source.selected {
        let slot = slots.entry(*selected).or_default();
        let proof = expected.get(selected);
        let wanted = proof.and_then(|p| p["transaction_envelopes"].as_u64());
        let mut accounted = proof.is_some_and(|p| p["stages"]["car_slot"] == "VERIFIED")
            && wanted == Some(slot.present)
            && !slot.duplicates;
        if let Some(n) = wanted {
            accounted &= slot.indices.iter().copied().eq(0..n);
        } else {
            accounted = false;
        }
        if let Some(proof) = proof {
            for name in ["DECODED", "MISSING", "UNSUPPORTED", "QUARANTINED"] {
                accounted &= proof["dispositions"][name].as_u64().unwrap_or(0)
                    == *slot.outcomes.get(name).unwrap_or(&0);
            }
        }
        for (k, v) in &slot.outcomes {
            *total_outcomes.entry(k.clone()).or_default() += v;
        }
        inventory.push(json!({"slot":selected,"expected_packages":wanted,"present_packages":slot.present,"decoded_packages":slot.outcomes.get("DECODED").copied().unwrap_or(0),"outcomes":slot.outcomes,"duplicate_package_identity":slot.duplicates,"accounted":accounted}));
    }
    let complete = inventory.iter().all(|s| s["accounted"] == true);
    json!({"sample_identity":source.sample,"slice_class":source.class,"receipt_evidence":source.receipt,"selection":{"selected_slots":source.selected,"slots":inventory,"all_expected_packages_accounted":complete,"decoded_packages":total_outcomes.get("DECODED").copied().unwrap_or(0),"package_outcomes":total_outcomes,"status":if complete{"ACCOUNTED"}else{"INCOMPLETE"}}})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_pilot_and_both_frozen_b7_roles_share_admission() {
        validate_sample(
            &serde_json::to_value(of1_range_recorder::sample::SampleIdentity::fixed_pilot())
                .unwrap(),
        )
        .unwrap();
        for ordinal in [0, 4, 8, 12] {
            let identity =
                of1_range_recorder::b7::sample(ordinal, Path::new("/tmp/identity-only-fixture"))
                    .unwrap();
            let mut value = serde_json::to_value(identity).unwrap();
            validate_sample(&value).unwrap();
            value["b7"]["cohort_role"] = json!("POSTHOC_CONTEXT");
            assert!(validate_sample(&value).is_err());
        }
    }

    use std::fs;

    #[test]
    fn batch_run_id_must_match_hashed_original_manifest() {
        let dir = tempfile::tempdir().unwrap();
        let put = |name: &str, v: &Value| {
            let p = dir.path().join(name);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            let b = serde_json::to_vec(v).unwrap();
            fs::write(p, &b).unwrap();
            hash(&b)
        };
        let run = json!({"run_id":"actual-run","aggregate_sha256":"aggregate","metadata_lease":{"authority":{"mode":"FIXTURE"}},"plan":{}});
        let payload = json!({"lease":{"authority":{"mode":"FIXTURE"}},"prepared":{"start_slot":100,"end_slot":101}});
        let receipt = json!({"aggregate_sha256":"aggregate","evidence":"Fixture","sha256":"raw","response_entity_bytes":10});
        let bindings = json!({"manifest_sha256":put("run.json",&run),"payload_manifest_sha256":put("payload.json",&payload),"aggregate_sha256":"aggregate","receipts":[{"sequence":4,"path":"published/0000000004/receipt.json","sha256":put("published/0000000004/receipt.json",&receipt),"raw_sha256":"raw","raw_bytes":10}]});
        let quality = json!({"bindings":bindings,"receipt_evidence":"Fixture","slice_class":"ENGINEERING_VALIDATION_ONLY","batch_binding":{"source_run_id":"invented-run"}});
        let execution = json!({"run_root":dir.path()});
        assert!(
            source(&quality, &execution)
                .err()
                .unwrap()
                .to_string()
                .contains("BATCH_ORIGINAL_RUN_ID")
        );
    }
}
