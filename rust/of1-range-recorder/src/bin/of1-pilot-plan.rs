//! Read-only proposal, not a lease, writer or downloader. Selection precedes index access.
use of1_range_recorder::{
    SLOTS_PER_EPOCH,
    acquisition::derive_payload_from_metadata,
    durable::acquisition::{PreparedPayload, RequestKind},
    monitor::read_run_context,
    sha256,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{error::Error, fs::OpenOptions, io::Write, path::Path};

const PROPOSAL: &str = include_str!("../../../../research/columnar-query/pilot-proposal.json");
const DOMAIN: &[u8] = b"OF1_PILOT_CENTER_1\0";

fn select(
    epoch: u64,
    first: u64,
    end: u64,
    exclusions: &[(u64, u64)],
    seed: &[u8],
) -> Option<(u64, [u8; 32], u64)> {
    let mut selected: Option<(u64, [u8; 32], u64)> = None;
    let mut eligible = 0;
    for center in first.checked_add(1)?..end.checked_sub(1)? {
        if exclusions
            .iter()
            .any(|&(a, b)| center - 1 < b && a < center + 2)
        {
            continue;
        }
        eligible += 1;
        let mut hash = Sha256::new();
        hash.update(DOMAIN);
        hash.update(seed);
        hash.update(epoch.to_le_bytes());
        hash.update(center.to_le_bytes());
        let digest: [u8; 32] = hash.finalize().into();
        if selected
            .is_none_or(|(old_center, old_digest, _)| (digest, center) < (old_digest, old_center))
        {
            selected = Some((center, digest, 0));
        }
    }
    selected.map(|(center, digest, _)| (center, digest, eligible))
}

fn feasibility(
    bytes: u64,
    requests: u64,
    absent: usize,
    budget: &Value,
    retained_stage_bytes: u64,
) -> Vec<&'static str> {
    let mut errors = Vec::new();
    if absent > 0 {
        errors.push("INDEX_REPORTED_ABSENT_NO_REPLACEMENT");
    }
    if bytes > 16 * 1024 * 1024 {
        errors.push("BRONZE_SELECTION_RAW_CAP_16_MIB");
    }
    if requests > 3 {
        errors.push("MORE_THAN_ONE_RANGE_PER_SLOT_NOT_SUPPORTED_BY_BRONZE");
    }
    if requests * 3 + 7 > budget["max_requests"].as_u64().unwrap_or(0) {
        errors.push("AGGREGATE_REQUEST_CAP");
    }
    if bytes * 3 + 15_576_576
        > budget["max_total_response_entity_bytes"]
            .as_u64()
            .unwrap_or(0)
    {
        errors.push("AGGREGATE_ENTITY_CAP");
    }
    if bytes * 3 > retained_stage_bytes {
        errors.push("FULL_RETRY_RESERVATION_EXCEEDS_RETAINED_STAGE_CAP_NO_INCREASE_NO_REPLACEMENT");
    }
    errors
}

fn range_totals(prepared: &PreparedPayload) -> Result<(u64, Vec<Value>), Box<dyn Error>> {
    let mut total = 0_u64;
    let mut ranges = Vec::new();
    for request in prepared.requests() {
        let RequestKind::CarRange {
            slot,
            start,
            end_exclusive,
            total: object_size,
            ..
        } = request.kind
        else {
            return Err("non-CAR planning result".into());
        };
        let bytes = end_exclusive.checked_sub(start).ok_or("range overflow")?;
        total = total.checked_add(bytes).ok_or("sum overflow")?;
        ranges.push(json!({"slot":slot,"start":start,"end_exclusive":end_exclusive,"entity_bytes":bytes,"object_size":object_size}));
    }
    Ok((total, ranges))
}

fn plan(root: &Path) -> Result<Value, Box<dyn Error>> {
    let proposal: Value = serde_json::from_str(PROPOSAL)?;
    let epoch = proposal["epoch"].as_u64().ok_or("epoch missing")?;
    if epoch != 978
        || proposal["context_before_slots"] != 1
        || proposal["context_after_slots"] != 1
        || proposal["windows"] != 1
        || proposal["domain"].as_str() != Some(std::str::from_utf8(DOMAIN)?)
    {
        return Err("unsupported frozen pilot geometry".into());
    }
    let exclusions: Vec<(u64, u64)> =
        serde_json::from_value(proposal["engineering_exclusions"].clone())?;
    let seed = proposal["seed"].as_str().ok_or("seed missing")?;
    let first = epoch * SLOTS_PER_EPOCH;
    let end = first + SLOTS_PER_EPOCH;
    // No source bytes, object lengths, success counts or Pump predicates enter this function.
    let (center, digest, eligible) =
        select(epoch, first, end, &exclusions, seed.as_bytes()).ok_or("empty population")?;
    let run = read_run_context(root)?;
    if run.aggregate_plan.epoch != epoch || run.published.len() < 4 {
        return Err("metadata epoch/count mismatch".into());
    }
    let metadata = &run.published[..4];
    let metadata_bytes: u64 = metadata
        .iter()
        .map(|p| p.receipt.response_entity_bytes)
        .sum();
    let budget = serde_json::to_value(&run.aggregate_budget)?;
    let base = json!({
        "schema":"OF1_OFFLINE_PILOT_SELECTION_1", "network_authorized":false,
        "proposal_sha256":sha256(PROPOSAL.as_bytes()), "proposal":proposal,
        "tool_source_sha256":sha256(include_bytes!("of1-pilot-plan.rs")),
        "selection":{"center_slot":center, "start_slot":center-1, "end_slot_exclusive":center+2,
                     "selected_slots":3, "eligible_centers":eligible, "epoch_slots":SLOTS_PER_EPOCH,
                     "rank_sha256":hex::encode(digest), "replacement_allowed":false},
        "metadata_run_id":run.snapshot.id, "existing_aggregate_caps_unchanged":budget,
        "retained_payload_stage_caps_unchanged":run.stage_budget,
        "source_format":run.aggregate_plan.format_source,
        "acquisition_identity":"NOT_PREPARED_NO_LEASE: old metadata is calculation evidence only; a new executable-bound metadata/payload decision is required before acquisition",
        "record_counts":"UNKNOWN_UNTIL_DECODE", "pump_presence":"UNKNOWN_NOT_A_SELECTION_CRITERION",
        "time_bounds":"UNAVAILABLE_NO_VERIFIED_EPOCH_UTC_CATALOGUE",
        "runtime_limits_ms":{"metadata":600_000,"payload":360_000,"aggregate":1_800_000},
        "downstream_limits":{"bronze_epoch":978,"bronze_slots_per_run":3,"bronze_selection_raw_bytes":16_777_216,
            "bronze_nodes_per_slot":4096,"bronze_links_per_slot":16384,"frame_bytes":2_097_152,
            "bronze_record_json_bytes_per_slot":16_777_216,"bronze_metadata_bytes_per_slot":16_777_216,
            "parquet_rows_per_layer":5000,"parquet_input_and_file_bytes":67_108_864,"parquet_record_bytes":16_777_216},
        "downstream_admission":"NOT_PROVEN: index bytes cannot bound expanded JSON, metadata or rows. On cap failure retain complete Raw and report INCOMPLETE; no truncation or replacement.",
        "batching":"One original run, one three-slot window. If >5000 records/layer or >64MiB file, implement manifest-bound lossless per-slot/row-group shards before publication; do not copy a fake one-slot run or raise caps.",
        "research_class_gate":"NOT_IMPLEMENTED: current decoder/writer enforce ENGINEERING_VALIDATION_ONLY; selection alone does not change evidence or permit research promotion"
    });
    let mut result = base;
    match derive_payload_from_metadata(&run.aggregate_plan, metadata, center - 1, center + 2) {
        Ok(prepared) => {
            let (total, ranges) = range_totals(&prepared)?;
            let n = u64::try_from(ranges.len())?;
            let errors = feasibility(
                total,
                n,
                prepared.index_reported_absent().len(),
                &budget,
                run.stage_budget.max_response_entity_bytes_total,
            );
            result["metadata_binding"] = serde_json::to_value(&prepared)?;
            result["ranges"] = json!(ranges);
            result["budgets"] = json!({"unique_payload_entity_bytes":total,"metadata_max_attempts":7,
                "payload_max_attempts":n*3,"max_total_attempts":7+n*3,"retry_attempts_per_operation":3,
                "metadata_reserved_entity_bytes":15_576_576,"payload_three_attempt_reservation_required":total*3,
                "combined_three_attempt_reservation_required":15_576_576+total*3,
                "payload_reservation_cap_unchanged":run.stage_budget.max_response_entity_bytes_total,
                "payload_two_attempt_calculation_not_approved":total*2,
                "retry_decision":"Keep selection. If full retry envelope exceeds retained cap: STOP; separately review a tighter attempt policy, never silently increase caps or draw another window.",
                "minimum_raw_disk_bytes":metadata_bytes+total,
                "disk_reservation_note":"raw lower bound only; receipts/journal/partial files require runtime disk checks within unchanged cap",
                "concurrency":1,"cost_basis":"Resource caps, not paid OF1 query-cost evidence. No call authorized."});
            result["range_feasibility"] = json!(if errors.is_empty() {
                "WITHIN_KNOWN_STATIC_CAPS_ONLY"
            } else {
                "STOP_NO_REPLACEMENT"
            });
            result["stop_reasons"] = json!(errors);
        }
        Err(error) => {
            result["range_feasibility"] = json!("STOP_NO_REPLACEMENT");
            result["stop_reasons"] = json!([format!("ORIGINAL_PLANNER_REJECTED: {error}")]);
        }
    }
    Ok(result)
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 {
        return Err("of1-pilot-plan RECORDED_RUN NEW_PROPOSAL_JSON (offline only)".into());
    }
    let result = plan(Path::new(&args[0]))?;
    let mut raw = serde_json::to_vec_pretty(&result)?;
    raw.push(b'\n');
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&args[1])?;
    file.write_all(&raw)?;
    file.sync_all()?;
    println!(
        "{}",
        json!({"proposal_sha256":sha256(&raw),"selection":result["selection"],"feasibility":result["range_feasibility"]})
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn complete_rank_agrees_with_acquisition_sample_identity() {
        let fixed = of1_range_recorder::sample::SampleIdentity::fixed_pilot();
        let first = 978 * SLOTS_PER_EPOCH;
        let (center, _, count) = select(
            978,
            first,
            first + SLOTS_PER_EPOCH,
            &[(first, first + 5)],
            fixed.seed.as_bytes(),
        )
        .unwrap();
        assert_eq!(center, fixed.selected_center);
        assert_eq!(center - 1, fixed.start_slot);
        assert_eq!(center + 2, fixed.end_slot_exclusive);
        assert_eq!(count, 431_993);
        fixed.validate(978).unwrap();
    }
    #[test]
    fn deterministic_and_context_exclusions() {
        let first = 978 * SLOTS_PER_EPOCH;
        let a = select(978, first, first + 30, &[(first, first + 5)], b"fixed").unwrap();
        assert_eq!(
            a,
            select(978, first, first + 30, &[(first, first + 5)], b"fixed").unwrap()
        );
        assert!(a.0 >= first + 6 && a.0 <= first + 28);
        assert_eq!(a.2, 23);
        assert!(select(978, first, first + 3, &[(first, first + 3)], b"fixed").is_none());
    }
    #[test]
    fn every_seed_stays_in_population_and_never_examines_outcomes() {
        for seed in 0_u64..100 {
            let (center, _, count) =
                select(978, 100, 140, &[(110, 120)], &seed.to_le_bytes()).unwrap();
            assert!((101..139).contains(&center));
            assert!(center + 2 <= 110 || center > 120);
            assert_eq!(count, 26);
        }
    }
    #[test]
    fn no_replacement_for_missing_or_oversized_selection() {
        let budget = json!({"max_requests":16,"max_total_response_entity_bytes":134_217_728});
        assert!(feasibility(100, 3, 0, &budget, 8_795_718).is_empty());
        assert_eq!(
            feasibility(100, 2, 1, &budget, 8_795_718),
            ["INDEX_REPORTED_ABSENT_NO_REPLACEMENT"]
        );
        assert!(
            feasibility(16_777_217, 3, 0, &budget, 8_795_718)
                .contains(&"BRONZE_SELECTION_RAW_CAP_16_MIB")
        );
        assert!(feasibility(100, 4, 0, &budget, 8_795_718).contains(&"AGGREGATE_REQUEST_CAP"));
        assert!(
            feasibility(100_000_000, 3, 0, &budget, 8_795_718).contains(&"AGGREGATE_ENTITY_CAP")
        );
    }
    #[test]
    fn exact_selected_retry_cost_is_not_a_cap_increase() {
        let budget = json!({"max_requests":16,"max_total_response_entity_bytes":134_217_728});
        assert_eq!(
            feasibility(3_663_577, 3, 0, &budget, 8_795_718),
            ["FULL_RETRY_RESERVATION_EXCEEDS_RETAINED_STAGE_CAP_NO_INCREASE_NO_REPLACEMENT"]
        );
        assert_eq!(3_663_577_u64 * 3 - 8_795_718, 2_195_013);
    }
}
