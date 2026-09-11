//! Read-only, receipt-bound B4 integrity report. No writer resume, lock, repair,
//! acquisition authority or domain decoding. Old failures are separate history.
use crate::{
    acquisition::{read_limited, verify_prepared_payload},
    car::{VerificationLimits, verify_slot_sections},
    durable::acquisition::{PreparedPayload, Published, RequestKind, current_executable_sha256},
    monitor::{RecordedRun, read_run_context},
    sha256,
};
use serde_json::{Value, json};
use std::{collections::BTreeMap, io, path::Path};

const JSON_LIMIT: u64 = 1_048_576;
const ASSEMBLY_LIMIT: u64 = 134_217_728;
const RESPONSE_LIMIT: u64 = 16_777_216;

fn invalid(message: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}

fn limited_json(path: &Path) -> io::Result<(Value, Vec<u8>)> {
    let bytes = read_limited(path, JSON_LIMIT).map_err(invalid)?;
    Ok((serde_json::from_slice(&bytes).map_err(invalid)?, bytes))
}

/// This digest binds compiled verification sources, not an asserted Git commit
/// or build attestation. The CLI additionally measures its own executable bytes.
#[must_use]
pub fn source_sha256() -> String {
    let sources: &[&[u8]] = &[
        include_bytes!("recorded_verification.rs"),
        include_bytes!("bin/of1-verify-recorded.rs"),
        include_bytes!("car.rs"),
        include_bytes!("monitor/recorded.rs"),
        include_bytes!("monitor/mod.rs"),
        include_bytes!("acquisition.rs"),
        include_bytes!("acquisition_http.rs"),
        include_bytes!("durable/acquisition.rs"),
        include_bytes!("durable.rs"),
        include_bytes!("dataset_location.rs"),
        include_bytes!("lib.rs"),
        include_bytes!("../Cargo.toml"),
        include_bytes!("../Cargo.lock"),
    ];
    let mut framed = Vec::new();
    for source in sources {
        framed.extend_from_slice(&(source.len() as u64).to_le_bytes());
        framed.extend_from_slice(source);
    }
    sha256(&framed)
}

fn bindings(run: &RecordedRun) -> io::Result<Value> {
    let find = |id: &str| {
        run.snapshot
            .artifacts
            .iter()
            .find(|a| a.id == id)
            .map(|a| a.sha256.clone())
    };
    let receipts = run.published.iter().map(|p| {
        let sequence = p.receipt.request.sequence;
        Ok(json!({
            "sequence":sequence, "path":format!("published/{sequence:010}/receipt.json"),
            "sha256":find(&format!("receipt-{sequence}")).ok_or_else(|| invalid("missing receipt artifact"))?,
            "raw_sha256":p.receipt.sha256, "raw_bytes":p.receipt.response_entity_bytes
        }))
    }).collect::<io::Result<Vec<_>>>()?;
    Ok(json!({
        "manifest_sha256":find("run-manifest").ok_or_else(|| invalid("missing manifest artifact"))?,
        "payload_manifest_sha256":find("payload-manifest"),
        "aggregate_sha256":sha256(&serde_json::to_vec(&run.aggregate_plan).map_err(invalid)?),
        "prepared_payload_sha256":run.prepared.as_ref().map(PreparedPayload::sha256).transpose().map_err(invalid)?,
        "metadata_receipt_sha256":run.prepared.as_ref().map(PreparedPayload::metadata_receipt_sha256),
        "receipts":receipts
    }))
}

fn raw(object: &Published) -> io::Result<Vec<u8>> {
    let bytes = read_limited(
        &object.raw_path,
        object.receipt.response_entity_bytes.min(RESPONSE_LIMIT),
    )
    .map_err(invalid)?;
    if bytes.len() as u64 != object.receipt.response_entity_bytes
        || sha256(&bytes) != object.receipt.sha256
    {
        return Err(invalid("Raw changed after receipt audit"));
    }
    Ok(bytes)
}

fn prior_failure(paths: Option<(&Path, &Path)>, run: &RecordedRun) -> io::Result<Value> {
    let Some((failure_path, result_path)) = paths else {
        return Ok(Value::Null);
    };
    let (failure, failure_bytes) = limited_json(failure_path)?;
    let (result, result_bytes) = limited_json(result_path)?;
    let expected_error = "CAR_CBOR_OR_ARCHIVAL_SCHEMA_INVALID";
    let raw_hash = result["raw"]["sha256"]
        .as_str()
        .ok_or_else(|| invalid("historical Raw hash missing"))?;
    let object = run
        .published
        .iter()
        .find(|p| p.receipt.request.sequence >= 4 && p.receipt.sha256 == raw_hash)
        .ok_or_else(|| invalid("historical Raw identity mismatch"))?;
    let sequence = object.receipt.request.sequence;
    let receipt_path = object.raw_path.with_file_name("receipt.json");
    let receipt_bytes = read_limited(&receipt_path, JSON_LIMIT).map_err(invalid)?;
    let payload_path = Path::new(&run.snapshot.dataset_root).join("payload.json");
    let (payload, _) = limited_json(&payload_path)?;
    let command = failure["message"]
        .as_str()
        .and_then(|s| s.lines().next())
        .and_then(|s| s.strip_prefix("Error: Command failed: "))
        .map(|s| s.split_whitespace().collect::<Vec<_>>())
        .ok_or_else(|| invalid("historical invocation missing"))?;
    // These old command paths are annotations only: never execute or open them.
    // Bind the recorded failed invocation to this exact run and payload lease.
    if command.len() != 6
        || command[1] != "verify-payload"
        || Some(command[0]) != result["executable_path"].as_str()
        || command[2] != run.snapshot.dataset_root
        || Some(command[4]) != payload["stage"]["lease_sha256"].as_str()
        || result["raw"]["path"].as_str() != object.raw_path.to_str()
        || result["raw"]["bytes"].as_u64() != Some(object.receipt.response_entity_bytes)
        || result["receipt"]["path"].as_str() != receipt_path.to_str()
        || result["receipt"]["sha256"] != sha256(&receipt_bytes)
        || !run
            .snapshot
            .artifacts
            .iter()
            .any(|a| a.id == format!("receipt-{sequence}") && a.sha256 == sha256(&receipt_bytes))
    {
        return Err(invalid("historical invocation/receipt binding mismatch"));
    }
    // The preserved operator failure and run result are historical evidence,
    // not a newly executed old binary or a replacement for canonical receipts.
    if result["run_id"] != run.snapshot.id
        || result["root"] != run.snapshot.dataset_root
        || result["executable_sha256"] != run.aggregate_plan.executable_sha256
        || failure["executable_sha256"] != run.aggregate_plan.executable_sha256
        || failure["network_calls"] != 0
        || result["integrity"]["offline_exit_code"] != 1
        || result["integrity"]["error"] != expected_error
        || !failure["message"].as_str().is_some_and(|s| {
            s.contains(&format!(
                "OF1_STOP: offline integrity quarantine: {expected_error}"
            ))
        })
    {
        return Err(invalid("historical failure/run/Raw identity mismatch"));
    }
    Ok(
        json!({"error":expected_error,"binary_sha256":run.aggregate_plan.executable_sha256,
        "artifact_sha256":sha256(&failure_bytes),"run_result_sha256":sha256(&result_bytes),
        "raw_sha256":raw_hash,"status":"HISTORICAL_FAILURE_PRESERVED"}),
    )
}

fn check_payload(run: &RecordedRun) -> io::Result<(&'static str, Option<String>, Vec<Value>)> {
    let Some(prepared) = &run.prepared else {
        return Ok(("NOT_ACQUIRED", None, vec![]));
    };
    let metadata = run
        .published
        .iter()
        .filter(|p| p.receipt.request.sequence < 4)
        .cloned()
        .collect::<Vec<_>>();
    verify_prepared_payload(&run.aggregate_plan, &metadata, prepared).map_err(invalid)?;
    let mut assembled = BTreeMap::<u64, (u64, Vec<u8>)>::new();
    let mut total = 0_u64;
    for request in prepared.requests() {
        let RequestKind::CarRange {
            slot,
            start,
            end_exclusive,
            ..
        } = request.kind
        else {
            return Err(invalid("non-CAR prepared request"));
        };
        let Some(object) = run.published.iter().find(|p| p.receipt.request == *request) else {
            return Ok((
                "INCOMPLETE",
                Some("PLANNED_RAW_NOT_PUBLISHED".into()),
                vec![],
            ));
        };
        let length = end_exclusive
            .checked_sub(start)
            .ok_or_else(|| invalid("invalid range"))?;
        total = total
            .checked_add(length)
            .ok_or_else(|| invalid("assembly overflow"))?;
        if total > ASSEMBLY_LIMIT.min(run.aggregate_plan.budget.max_total_response_entity_bytes) {
            return Err(invalid("offline assembly byte limit"));
        }
        let entry = assembled.entry(slot).or_insert((start, Vec::new()));
        if entry.0 != start {
            return Err(invalid("noncontiguous slot range"));
        }
        entry
            .1
            .try_reserve_exact(usize::try_from(length).map_err(invalid)?)
            .map_err(invalid)?;
        entry.1.extend(raw(object)?);
        entry.0 = end_exclusive;
    }
    let mut slots = Vec::new();
    for (slot, (_, bytes)) in assembled {
        let result = verify_slot_sections(
            slot,
            &bytes,
            VerificationLimits {
                max_total_bytes: usize::try_from(ASSEMBLY_LIMIT).map_err(invalid)?,
                max_section_bytes: usize::try_from(ASSEMBLY_LIMIT).map_err(invalid)?,
                max_nodes: 4096,
                max_links: 16_384,
            },
        );
        match result {
            Ok(report) => slots.push(json!({"slot":slot,"archival_node_counts":report.archival_node_counts,"report":report})),
            Err(error) => return Ok(("QUARANTINED", Some(error.to_string()), vec![])),
        }
    }
    if slots.is_empty() || !prepared.index_reported_absent().is_empty() {
        return Ok((
            "INCOMPLETE",
            Some("INDEX_REPORTS_ABSENT_SLOTS".into()),
            slots,
        ));
    }
    Ok(("VERIFIED", None, slots))
}

/// Verify one bounded recorded run without touching writer state. Receipt or
/// identity errors return no report; CAR incompatibility returns explicit
/// quarantine with no partial slot facts. This is not B5/domain decoding.
/// # Errors
/// Rejects changed, unbound, oversized, nonregular or corrupt inputs.
pub fn verify_recorded(root: &Path, prior: Option<(&Path, &Path)>) -> io::Result<Value> {
    // Bound resources before the existing streaming audit uses manifest limits.
    let (manifest, _) = limited_json(&root.join("run.json"))?;
    let budget = &manifest["plan"]["budget"];
    if !budget["max_total_response_entity_bytes"]
        .as_u64()
        .is_some_and(|n| n > 0 && n <= ASSEMBLY_LIMIT)
        || !budget["max_response_entity_bytes"]
            .as_u64()
            .is_some_and(|n| n > 0 && n <= RESPONSE_LIMIT)
    {
        return Err(invalid("offline reader resource bounds"));
    }
    let before = read_run_context(root)?;
    let bound = bindings(&before)?;
    let historical = prior_failure(prior, &before)?;
    let (car_status, error, slots) = check_payload(&before)?;
    let after = read_run_context(root)?;
    if bindings(&after)? != bound
        || after.snapshot.id != before.snapshot.id
        || prior_failure(prior, &after)? != historical
    {
        return Err(invalid("recorded inputs changed during verification"));
    }
    Ok(json!({
        "schema":"OF1_OFFLINE_VERIFICATION_1", "run_id":before.snapshot.id,"dataset_root":before.snapshot.dataset_root,
        "verifier":{"name":"of1-verify-recorded","version":"1", "binary_sha256":current_executable_sha256().map_err(invalid)?,"source_sha256":source_sha256()},
        "bindings":bound,
        "stages":{"capture":if before.snapshot.stage=="COMPLETE" {"COMPLETE"}else{"INCOMPLETE"},"raw_receipts":"VERIFIED","car_slot":car_status,"domain_decoding":"NOT_PERFORMED"},
        "integrity":{"error":error,"root_to_slot_membership":"UNAVAILABLE","whole_car_sha256_verified":false,"slots":slots},
        "prior_failure":historical,"evidence":"RAW_ENGINEERING_CHECK_ONLY","research_ready":false
    }))
}
