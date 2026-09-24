//! Read-only adapter to the existing receipt reader and index planner; no lease.
use of1_range_recorder::{
    acquisition::derive_payload_from_metadata, monitor::read_run_context, sha256,
};
use serde_json::{Value, json};
use std::{error::Error, fs::OpenOptions, io::Write, path::Path};

fn validate(selection: &Value) -> Result<&Vec<Value>, Box<dyn Error>> {
    if selection["schema"] != "B7_FIXED_WINDOWS_1" || selection["network_authorized"] != false {
        return Err("offline selection required".into());
    }
    let windows = selection["windows"].as_array().ok_or("windows missing")?;
    if windows.is_empty() || windows.len() > 16 {
        return Err("bounded window count required".into());
    }
    let mut seen = std::collections::BTreeSet::new();
    for w in windows {
        let a = w["start_slot"].as_u64().ok_or("start missing")?;
        let b = w["end_slot_exclusive"].as_u64().ok_or("end missing")?;
        if a < 422_496_000 || b > 422_928_000 || a.checked_add(16) != Some(b) {
            return Err("invalid epoch/window".into());
        }
        if (a..b).any(|s| !seen.insert(s)) {
            return Err("overlapping windows".into());
        }
    }
    Ok(windows)
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 3 {
        return Err("of1-window-ranges EXISTING_RUN FIXED_WINDOWS_JSON NEW_JSON (offline)".into());
    }
    let input = of1_range_recorder::acquisition::read_limited(Path::new(&args[1]), 65536)?;
    let selection: Value = serde_json::from_slice(&input)?;
    let windows = validate(&selection)?;
    let run = read_run_context(Path::new(&args[0]))?;
    if run.aggregate_plan.epoch != 978 || run.published.len() < 4 {
        return Err("wrong metadata epoch or missing publications".into());
    }
    // Never remove a sample identity or edit the old plan to make derivation succeed.
    let results: Vec<Value> = windows
        .iter()
        .map(|w| {
            match derive_payload_from_metadata(
                &run.aggregate_plan,
                &run.published[..4],
                w["start_slot"].as_u64().unwrap(),
                w["end_slot_exclusive"].as_u64().unwrap(),
            ) {
                Ok(p) => json!({"selection":w,"status":"PLANNED","prepared":p}),
                Err(error) => {
                    json!({"selection":w,"status":"STOP_NO_REPLACEMENT","error":error.to_string()})
                }
            }
        })
        .collect();
    let result = json!({"schema":"OF1_OFFLINE_WINDOW_RANGES_1", "network_authorized":false,
        "selection_sha256":sha256(&input), "run_id":run.snapshot.id,
        "aggregate_plan_sha256":sha256(&serde_json::to_vec(&run.aggregate_plan)?),
        "source_format":run.aggregate_plan.format_source,
        "source_manifest_sha256":sha256(&of1_range_recorder::acquisition::read_limited(&Path::new(&args[0]).join("run.json"), 1_048_576)?),
        "adapter_source_sha256":sha256(include_bytes!("of1-window-ranges.rs")),
        "old_plan_unchanged":true,"historical_metadata_only_not_a_new_source_identity":true,
        "windows":results});
    let mut output = serde_json::to_vec_pretty(&result)?;
    output.push(b'\n');
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&args[2])?;
    file.write_all(&output)?;
    file.sync_all()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn selection() -> Value {
        json!({"schema":"B7_FIXED_WINDOWS_1","network_authorized":false,
            "windows":[{"start_slot":422_496_016,"end_slot_exclusive":422_496_032}]})
    }
    #[test]
    fn reject_authority_overlap_oversize_and_wrong_epoch() {
        assert!(validate(&selection()).is_ok());
        let mut s = selection();
        s["network_authorized"] = json!(true);
        assert!(validate(&s).is_err());
        let mut s = selection();
        s["windows"] = json!([s["windows"][0].clone(), s["windows"][0].clone()]);
        assert!(validate(&s).is_err());
        let mut s = selection();
        s["windows"][0]["end_slot_exclusive"] = json!(u64::MAX);
        assert!(validate(&s).is_err());
        let mut s = selection();
        s["windows"] = json!(vec![s["windows"][0].clone(); 17]);
        assert!(validate(&s).is_err());
    }
}
