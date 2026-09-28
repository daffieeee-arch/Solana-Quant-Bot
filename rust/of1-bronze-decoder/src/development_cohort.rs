//! Read-only, exact-snapshot capability for four already completed DEVELOPMENT
//! windows. No caller-selected cohort, root, pin, lease or general B7 override.
use crate::{batch, collection, invalid, resources};
use of1_range_recorder::{b7, sample::SampleIdentity, sha256};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs, io,
    path::{Component, Path, PathBuf},
};

const PINS: &[u8] = include_bytes!("../sources/b7-development-cohort.json");
const MAX_EXPORT: usize = 16 * 1024 * 1024;

fn text(v: &Value) -> io::Result<&str> {
    v.as_str().ok_or_else(|| invalid("COHORT_STRING"))
}
fn list(v: &Value) -> io::Result<&Vec<Value>> {
    v.as_array().ok_or_else(|| invalid("COHORT_ARRAY"))
}
fn path(root: &Path, relative: &str) -> io::Result<PathBuf> {
    let relative = Path::new(relative);
    if relative
        .components()
        .any(|c| !matches!(c, Component::Normal(_)))
        || relative.as_os_str().is_empty()
    {
        return Err(invalid("COHORT_LITERAL_PATH_REQUIRED"));
    }
    let result = root.join(relative);
    if fs::canonicalize(&result)? != result {
        return Err(invalid("COHORT_SYMLINK_OR_ALIAS"));
    }
    Ok(result)
}
fn pinned(root: &Path, relative: &str, hash: &Value) -> io::Result<(Value, PathBuf)> {
    let p = path(root, relative)?;
    let (v, actual) = collection::json_file(&p, 2 * 1024 * 1024)?;
    if actual != text(hash)? {
        return Err(invalid("COHORT_SNAPSHOT_MISMATCH"));
    }
    Ok((
        v,
        p.parent()
            .ok_or_else(|| invalid("COHORT_PARENT"))?
            .to_path_buf(),
    ))
}

fn header(v: &Value, pin: &Value, ordinal: usize) -> io::Result<()> {
    let sample: SampleIdentity =
        serde_json::from_value(v["sample_identity"].clone()).map_err(invalid)?;
    sample.validate(978).map_err(invalid)?;
    if ordinal >= 4
        || pin["ordinal"] != ordinal
        || v["sample_identity"]
            != json!(b7::sample(ordinal, Path::new(b7::PRODUCTION_ROOT)).map_err(invalid)?)
        || v["state"] != "COMPLETE"
        || v["research_ready"] != false
        || v["layers"] != pin["layers"]
        || v["completeness"]["all_selected_slots_accounted"] != true
        || v["transaction_status_counts"]["ERROR"] != pin["counts"]["failures"]
        || list(&v["slot_outcomes"])?.len() != 16
        || list(&v["slot_outcomes"])?
            .iter()
            .enumerate()
            .any(|(i, s)| s["slot"] != sample.start_slot + i as u64 || s["state"] != "ACCOUNTED")
    {
        return Err(invalid("COHORT_ROLE_SELECTION_OR_COMPLETENESS"));
    }
    Ok(())
}

struct Reader {
    inventory: Option<crate::instruction_inventory::Inventory>,
    bronze: collection::Logical,
    silver: collection::Logical,
    exported_silver: collection::Logical,
    facts: Vec<Value>,
    parts: Vec<Value>,
    seen: BTreeSet<PathBuf>,
    fact_hashes: BTreeSet<String>,
}
impl Reader {
    fn verify_layers(&self, expected: &Value) -> io::Result<()> {
        if json!({"bronze":self.bronze.value(),"silver":self.silver.value()}) != *expected
            || self.exported_silver.value() != self.silver.value()
            || self
                .inventory
                .as_ref()
                .is_some_and(|i| i.logical.value() != self.bronze.value())
        {
            return Err(invalid("COHORT_LOGICAL_HASH_PARITY"));
        }
        Ok(())
    }
    fn reserve_part(&mut self, decode: PathBuf) -> io::Result<()> {
        if !self.seen.insert(decode) || self.seen.len() > 2048 {
            return Err(invalid("COHORT_DUPLICATE_OR_EXCESS_PART"));
        }
        Ok(())
    }
    fn new() -> Self {
        Self {
            inventory: None,
            bronze: collection::Logical::default(),
            silver: collection::Logical::default(),
            exported_silver: collection::Logical::default(),
            facts: Vec::new(),
            parts: Vec::new(),
            seen: BTreeSet::new(),
            fact_hashes: BTreeSet::new(),
        }
    }

    /// Reuse the original publication, physical parity and atomic-parent gates.
    /// The producer identities come from the pinned manifest, never this build.
    fn part(
        &mut self,
        root: &Path,
        descriptor: &Value,
        plan: &batch::Plan,
        plan_hash: &str,
        batch: &batch::Batch,
        part: Option<usize>,
    ) -> io::Result<()> {
        let decode = path(root, text(&descriptor["decode_directory"])?)?;
        self.reserve_part(decode.clone())?;
        let (manifest, parquet) = pinned(
            root,
            text(&descriptor["parquet_manifest_path"])?,
            &descriptor["parquet_manifest_sha256"],
        )?;
        let execution = &manifest["input"]["execution"];
        let checked = batch::verify_output_identity(
            plan,
            plan_hash,
            batch,
            &decode,
            text(&execution["decoder_source_sha256"])?,
            text(&execution["lock_sha256"])?,
            part,
        )?;
        let quality =
            collection::json_file(&decode.join("quality.json"), resources::MAX_QUALITY_BYTES)?.0;
        let (verified, _) =
            collection::parquet(plan, batch, &parquet, &checked["execution"], &quality)?;
        if verified != manifest || manifest["sample_identity"] != plan.sources[0].sample_identity {
            return Err(invalid("COHORT_PART_BINDING"));
        }
        let first = if part.is_some() {
            collection::number(&execution["slot_part"]["start_transaction"])?
        } else {
            0
        };
        let before = (self.bronze.rows, self.silver.rows);
        collection::inspect_records(
            plan,
            batch,
            &decode,
            &mut self.bronze,
            &mut self.silver,
            text(&execution["decoder_source_sha256"])?,
            first,
        )?;
        if self.bronze.rows - before.0 != collection::number(&manifest["layers"]["bronze"]["rows"])?
            || self.silver.rows - before.1
                != collection::number(&manifest["layers"]["silver"]["rows"])?
        {
            return Err(invalid("COHORT_PART_COUNT"));
        }
        let part_id = self.parts.len();
        collection::rows(&decode.join("silver.jsonl"), |raw, record| {
            self.exported_silver.add(raw)?;
            let hash = sha256(raw);
            if !self.fact_hashes.insert(hash.clone()) || self.facts.len() >= 122 {
                return Err(invalid("COHORT_DUPLICATE_OR_EXCESS_FACT"));
            }
            self.facts.push(
                json!({"record_sha256":hash,"record":record,"part_id":part_id,
                "canonical_record_json":std::str::from_utf8(raw).map_err(invalid)?}),
            );
            Ok(())
        })?;
        if let Some(inventory) = &mut self.inventory {
            collection::rows(&decode.join("bronze.jsonl"), |raw, record| {
                inventory.package(raw, record, part_id, &self.facts)
            })?;
        }
        self.parts.push(json!({"part_id":part_id,"batch_id":batch.batch_id,"source_id":batch.source_id,
            "parquet_manifest_path":parquet.join("manifest.json"),"parquet_manifest_sha256":descriptor["parquet_manifest_sha256"],
            "decode_directory":decode,"decoder_source_sha256":execution["decoder_source_sha256"],
            "decoder_binary_sha256":execution["executable_sha256"],"writer":manifest["writer"],
            "physical_files":manifest["files"],"slot_part":execution["slot_part"]}));
        Ok(())
    }

    fn slots(
        &mut self,
        root: &Path,
        slots: &[Value],
        plan: &batch::Plan,
        hash: &str,
        start: usize,
    ) -> io::Result<()> {
        if slots.len() != 16 - start {
            return Err(invalid("COHORT_SLOT_INVENTORY"));
        }
        for (batch, descriptor) in plan.batches.iter().skip(start).zip(slots) {
            let (slot, _) = pinned(
                root,
                text(&descriptor["manifest_path"])?,
                &descriptor["sha256"],
            )?;
            if slot["slot"] != batch.slots[0]
                || slot["state"] != "ACCOUNTED"
                || slot["plan_sha256"] != hash
            {
                return Err(invalid("COHORT_SLOT_BINDING"));
            }
            for (i, part) in list(&slot["parts"])?.iter().enumerate() {
                if part["ordinal"] != i {
                    return Err(invalid("COHORT_PART_ORDER"));
                }
                self.part(root, part, plan, hash, batch, Some(i))?;
            }
        }
        Ok(())
    }
}

fn plan(value: &Value, hash: &Value) -> io::Result<batch::Plan> {
    let p: batch::Plan = serde_json::from_value(value.clone()).map_err(invalid)?;
    p.validate()?;
    let (sample, _) =
        batch::campaign_sample(&p)?.ok_or_else(|| invalid("COHORT_SAMPLE_REQUIRED"))?;
    if sample
        .b7
        .as_ref()
        .is_none_or(|s| s.window_ordinal >= 4 || s.cohort_role != "DEVELOPMENT")
        || text(hash)?.len() != 64
        || hex::decode(text(hash)?).is_err()
    {
        return Err(invalid("COHORT_PLAN_BINDING"));
    }
    p.validate_sources()?;
    Ok(p)
}

/// The only production entrypoint, deliberately without root/pin/role arguments.
/// # Errors
/// Any changed snapshot, missing binding/file, duplicate or failed parent denies
/// the whole result. This function never writes to or resumes the campaign.
pub fn read() -> io::Result<Value> {
    read_mode(false)
}

/// The same four-manifest capability, with an existing-record instruction projection.
/// # Errors
/// Denies any identity, completeness or fact/instruction binding mismatch.
pub fn read_instructions() -> io::Result<Value> {
    read_mode(true)
}
fn read_mode(instructions: bool) -> io::Result<Value> {
    let pins: Value = serde_json::from_slice(PINS).map_err(invalid)?;
    let root = Path::new(b7::PRODUCTION_ROOT);
    if fs::canonicalize(root)? != root {
        return Err(invalid("COHORT_ROOT"));
    }
    let mut windows = Vec::new();
    for (i, pin) in list(&pins["windows"])?.iter().enumerate() {
        let (m, base) = pinned(root, text(&pin["relative_manifest"])?, &pin["sha256"])?;
        header(&m, pin, i)?;
        let mut reader = Reader::new();
        if instructions {
            reader.inventory = Some(crate::instruction_inventory::Inventory::default());
        }
        if i == 0 {
            if m["schema"] != "OF1_CONTINUED_BATCH_COLLECTION_1" {
                return Err(invalid("COHORT_CONTINUED_REQUIRED"));
            }
            let old = &m["retained_checkpoint"];
            let p = plan(&old["plan"], &old["plan_sha256"])?;
            let old_root = base
                .parent()
                .ok_or_else(|| invalid("COHORT_RETAINED_ROOT"))?;
            let batches = list(&old["batches"])?;
            if batches.len() != 16
                || batches
                    .iter()
                    .enumerate()
                    .any(|(n, b)| b["state"] != if n < 12 { "VERIFIED" } else { "PENDING" })
            {
                return Err(invalid("COHORT_RETAINED_PREFIX"));
            }
            for (batch, descriptor) in p.batches.iter().take(12).zip(batches) {
                reader.part(
                    old_root,
                    descriptor,
                    &p,
                    text(&old["plan_sha256"])?,
                    batch,
                    None,
                )?;
            }
            if json!({"bronze":reader.bronze.value(),"silver":reader.silver.value()})
                != old["layers"]
            {
                return Err(invalid("COHORT_RETAINED_HASH"));
            }
            let p = plan(
                &m["decision"]["plan"],
                &m["continued_slots"][0]["manifest"]["plan_sha256"],
            )?;
            reader.slots(
                &base,
                list(&m["continued_slots"])?,
                &p,
                text(&m["continued_slots"][0]["manifest"]["plan_sha256"])?,
                12,
            )?;
        } else {
            if m["schema"] != "OF1_PARTED_BATCH_COLLECTION_1" {
                return Err(invalid("COHORT_PARTED_REQUIRED"));
            }
            let p = plan(&m["plan"], &m["plan_sha256"])?;
            reader.slots(&base, list(&m["slots"])?, &p, text(&m["plan_sha256"])?, 0)?;
        }
        reader.verify_layers(&m["layers"])?;
        let inventory = reader
            .inventory
            .take()
            .map(|v| v.finish(&pin["counts"]))
            .transpose()?;
        windows.push(json!({"ordinal":i,"collection_sha256":pin["sha256"],"collection_path":base.join("collection.json"),
            "sample_identity":m["sample_identity"],"counts":pin["counts"],"layers":m["layers"],
            "coverage":m["completeness"],"slot_outcomes":m["slot_outcomes"],"pump_layout_outcomes":m["pump_layout_outcomes"],
            "diagnosis_denominator":m["diagnosis_denominator"],"parts":reader.parts,"facts":if instructions {vec![]}else{reader.facts}}));
        if let Some(inventory) = inventory {
            windows
                .last_mut()
                .ok_or_else(|| invalid("INVENTORY_WINDOW"))?["instruction_inventory"] = inventory;
        }
    }
    let result = json!({"schema":if instructions {"OF1_B7_DEVELOPMENT_INSTRUCTIONS_ADMISSION_1"}else{"OF1_B7_DEVELOPMENT_ADMISSION_1"},"pins_sha256":sha256(PINS),
        "selection_sha256":b7::SELECTION_SHA256,"reader_source_sha256":crate::source_sha256(),
        "reader_binary_sha256":of1_range_recorder::durable::acquisition::current_executable_sha256().map_err(invalid)?,
        "research_ready":false,"windows":windows});
    resources::bounded_json(&result, "COHORT_EXPORT", MAX_EXPORT)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exported_bytes_must_match_the_verified_logical_chain() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("silver.jsonl");
        fs::write(&path, b"{\"fixture\":1}\n").unwrap();
        let mut checked = collection::Logical::default();
        collection::rows(&path, |raw, _| checked.add(raw)).unwrap();
        let mut exported = collection::Logical::default();
        collection::rows(&path, |raw, _| exported.add(raw)).unwrap();
        let mut reader = Reader::new();
        reader.silver = checked;
        reader.exported_silver = exported;
        let expected = json!({"bronze":reader.bronze.value(),"silver":reader.silver.value()});
        reader.verify_layers(&expected).unwrap();
        fs::write(&path, b"{\"fixture\":2}\n").unwrap();
        let mut changed = collection::Logical::default();
        collection::rows(&path, |raw, _| changed.add(raw)).unwrap();
        reader.exported_silver = changed;
        assert!(reader.verify_layers(&expected).is_err());
    }
    #[test]
    fn retained_or_continued_parts_cannot_be_admitted_twice() {
        let mut reader = Reader::new();
        reader
            .reserve_part(PathBuf::from("retained/slot-0"))
            .unwrap();
        reader
            .reserve_part(PathBuf::from("continued/slot-12/part-0"))
            .unwrap();
        assert!(
            reader
                .reserve_part(PathBuf::from("retained/slot-0"))
                .is_err()
        );
        assert!(
            reader
                .reserve_part(PathBuf::from("continued/slot-12/part-0"))
                .is_err()
        );
        assert_eq!(reader.bronze.rows, 0); // denial before opening or publishing records
        let pins: Value = serde_json::from_slice(PINS).unwrap();
        assert_eq!(pins["windows"].as_array().unwrap().len(), 4);
    }
    fn sample_window(i: usize) -> (Value, Value) {
        let pins: Value = serde_json::from_slice(PINS).unwrap();
        let pin = pins["windows"][i].clone();
        let sample = b7::sample(i, Path::new(b7::PRODUCTION_ROOT)).unwrap();
        let v = json!({"sample_identity":sample,"state":"COMPLETE","research_ready":false,
            "layers":pin["layers"],"transaction_status_counts":{"ERROR":pin["counts"]["failures"]},
            "completeness":{"all_selected_slots_accounted":true},
            "slot_outcomes":(0..16).map(|n|json!({"slot":sample.start_slot+n,"state":"ACCOUNTED"})).collect::<Vec<_>>()});
        (v, pin)
    }
    #[test]
    fn only_four_exact_development_identities_and_complete_snapshots() {
        for i in 0..4 {
            let (v, pin) = sample_window(i);
            header(&v, &pin, i).unwrap();
            for field in ["cohort_role", "selection_sha256", "phase", "window_ordinal"] {
                let mut wrong = v.clone();
                wrong["sample_identity"]["b7"][field] = json!("changed");
                assert!(header(&wrong, &pin, i).is_err(), "{field}");
            }
            let mut evaluation = v.clone();
            evaluation["sample_identity"] =
                json!(b7::sample(4, Path::new(b7::PRODUCTION_ROOT)).unwrap());
            assert!(header(&evaluation, &pin, i).is_err());
            let mut mixed = v.clone();
            mixed["sample_identity"] =
                json!(b7::sample((i + 1) % 4, Path::new(b7::PRODUCTION_ROOT)).unwrap());
            assert!(header(&mixed, &pin, i).is_err());
            let mut partial = v.clone();
            partial["slot_outcomes"][15]["state"] = json!("PENDING");
            assert!(header(&partial, &pin, i).is_err());
            let mut duplicate = v.clone();
            duplicate["slot_outcomes"][15] = duplicate["slot_outcomes"][14].clone();
            assert!(header(&duplicate, &pin, i).is_err());
            let mut changed = v.clone();
            changed["layers"]["silver"]["rows"] = json!(0);
            assert!(header(&changed, &pin, i).is_err());
            let mut missing = v.clone();
            missing.as_object_mut().unwrap().remove("sample_identity");
            assert!(header(&missing, &pin, i).is_err());
        }
    }
    #[test]
    fn manifest_hash_and_literal_paths_fail_before_following_children() {
        let tmp = tempfile::tempdir().unwrap();
        fs::write(tmp.path().join("manifest.json"), b"{}").unwrap();
        pinned(tmp.path(), "manifest.json", &json!(sha256(b"{}"))).unwrap();
        assert!(
            pinned(
                tmp.path(),
                "manifest.json",
                &json!(sha256(b"{\"changed\":true}"))
            )
            .is_err()
        );
        for name in ["../manifest.json", "/etc/passwd", ""] {
            assert!(path(tmp.path(), name).is_err());
        }
        std::os::unix::fs::symlink(
            tmp.path().join("manifest.json"),
            tmp.path().join("alias.json"),
        )
        .unwrap();
        assert!(path(tmp.path(), "alias.json").is_err());
    }
}
