//! Deterministic Bronze JSON and self-contained quality HTML; operational
//! processing time and executable identity belong in a separate execution receipt.
use crate::{archive, codec, invalid, pump, pump_sell};
use of1_range_recorder::{
    acquisition::read_limited,
    durable::acquisition::{Published, RequestKind},
    monitor::{RecordedRun, read_run_context},
    recorded_verification::verify_recorded,
    sha256,
};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fmt::Write as _, io, path::Path};

pub const SCHEMA: &str = "OF1_BRONZE_TRANSACTION_1";
pub const MAX_RECORD_JSON_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_DECODED_METADATA_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_SELECTION_SLOTS: usize = 3;
pub const MAX_SELECTION_RAW_BYTES: usize = archive::MAX_SLOT_BYTES;
pub const MAX_SELECTION_RECORD_BYTES: usize = MAX_SELECTION_SLOTS * MAX_RECORD_JSON_BYTES;
pub const MAX_SELECTION_METADATA_BYTES: usize = MAX_SELECTION_SLOTS * MAX_DECODED_METADATA_BYTES;

/// Explicit aggregate resident-output budget, in addition to per-frame bounds.
/// # Errors
/// Fails before publishing a partial report when aggregate decoding exceeds scope.
pub fn charge(total: &mut usize, bytes: usize, limit: usize) -> io::Result<()> {
    let next = total.checked_add(bytes).ok_or_else(|| {
        invalid(format!(
            "BRONZE_AGGREGATE_LIMIT current={total} incoming={bytes} limit={limit} overflow=true"
        ))
    })?;
    if next > limit {
        return Err(invalid(format!(
            "BRONZE_AGGREGATE_LIMIT current={total} incoming={bytes} next={next} limit={limit}"
        )));
    }
    *total = next;
    Ok(())
}

/// Full original run/receipt/plan gate followed by at most three native slots.
/// # Errors
/// Fails before publication on run, receipt, CID, graph or input identity changes.
pub fn decode_run(root: &Path) -> io::Result<Value> {
    let run = read_run_context(root)?;
    if run.aggregate_plan.epoch != 978 {
        return Err(invalid("UNSUPPORTED_EPOCH_OUTSIDE_BOUNDED_978_LANE"));
    }
    let mut payloads = run
        .published
        .iter()
        .filter(|p| matches!(p.receipt.request.kind, RequestKind::CarRange { .. }))
        .collect::<Vec<_>>();
    // Plan order is authoritative; never infer ordering from directory enumeration.
    payloads.sort_by_key(|p| p.receipt.request.sequence);
    check_selection(&payloads)?;
    let verification = verify_recorded(root, None)?;
    if verification["stages"]["raw_receipts"] != "VERIFIED" {
        return Err(invalid("RAW_RECEIPTS_UNVERIFIED"));
    }
    if payloads.is_empty() {
        let mut report = json!({"schema":SCHEMA,"run_id":run.snapshot.id,"bindings":verification["bindings"],"input_kind":"METADATA_ONLY","records":[],"silver_records":[],"silver":"NOT_PRODUCED","domain_decoding":"UNAVAILABLE_NO_PAYLOAD","decoded_transactions":null,"research_ready":false});
        attach_sample(&mut report, run.aggregate_plan.sample_identity.as_ref())?;
        return Ok(report);
    }
    if verification["stages"]["car_slot"] != "VERIFIED" {
        return Err(invalid(format!(
            "CAR_SLOT_UNVERIFIED: {}",
            verification["integrity"]["error"]
        )));
    }
    let mut slots = Vec::new();
    let mut record_bytes = 0;
    let mut metadata_bytes = 0;
    for published in payloads {
        let slot = project_slot(&run, published, &verification).map_err(|e| {
            invalid(format!(
                "{e} request_sequence={}",
                published.receipt.request.sequence
            ))
        })?;
        for (total, key, limit) in [
            (
                &mut record_bytes,
                "record_json_bytes",
                MAX_SELECTION_RECORD_BYTES,
            ),
            (
                &mut metadata_bytes,
                "decoded_metadata_bytes",
                MAX_SELECTION_METADATA_BYTES,
            ),
        ] {
            charge(
                total,
                usize::try_from(
                    slot["resource_accounting"][key]
                        .as_u64()
                        .ok_or_else(|| invalid("SLOT_ACCOUNTING"))?,
                )
                .map_err(invalid)?,
                limit,
            )?;
        }
        slots.push(slot);
    }
    let mut projected = combine_slots(slots, record_bytes, metadata_bytes)?;
    attach_sample(&mut projected, run.aggregate_plan.sample_identity.as_ref())?;
    let after = verify_recorded(root, None)?;
    if after["bindings"] != verification["bindings"] || after["run_id"] != verification["run_id"] {
        return Err(invalid("INPUT_CHANGED_DURING_DECODE"));
    }
    Ok(projected)
}

/// Class is obtained solely from the checked immutable source aggregate.
fn attach_sample(
    value: &mut Value,
    sample: Option<&of1_range_recorder::sample::SampleIdentity>,
) -> io::Result<()> {
    if let Some(sample) = sample {
        value["sample_identity"] = serde_json::to_value(sample).map_err(invalid)?;
        value["slice_class"] = json!(sample.sample_class);
        // Sample class never promotes fixture evidence or establishes suitability.
        if value.get("research_ready").is_some() {
            value["research_ready"] = json!(false);
        }
        if value.get("limitations").is_some() {
            value["limitations"][3] = json!(
                "Preregistered sample identity only; no economic/executable price, research-readiness, strategy or edge claim"
            );
        }
    }
    Ok(())
}

/// One complete native range per slot; no reshaped run, split-range stitching or
/// unbounded all-epoch report. The existing verifier proves the actual graph.
fn check_selection(payloads: &[&Published]) -> io::Result<()> {
    if payloads.len() > MAX_SELECTION_SLOTS {
        return Err(invalid("BRONZE_SELECTION_SLOT_LIMIT"));
    }
    let mut total = 0;
    let mut previous = None;
    for p in payloads {
        let RequestKind::CarRange {
            slot,
            start,
            end_exclusive,
            ..
        } = p.receipt.request.kind
        else {
            return Err(invalid("PAYLOAD_KIND"));
        };
        if previous.is_some_and(|s| s >= slot) {
            return Err(invalid("UNSUPPORTED_DUPLICATE_OR_NONORDERED_SLOT_RANGE"));
        }
        previous = Some(slot);
        charge(
            &mut total,
            usize::try_from(
                end_exclusive
                    .checked_sub(start)
                    .ok_or_else(|| invalid("RANGE_LENGTH"))?,
            )
            .map_err(invalid)?,
            MAX_SELECTION_RAW_BYTES,
        )?;
    }
    Ok(())
}

fn combine_slots(
    mut slots: Vec<Value>,
    record_bytes: usize,
    metadata_bytes: usize,
) -> io::Result<Value> {
    if slots.len() == 1 {
        return Ok(slots.remove(0));
    }
    let first = slots.first().ok_or_else(|| invalid("NO_SLOTS"))?;
    let evidence = first["receipt_evidence"].clone();
    let mut result = json!({"schema":SCHEMA,"run_id":first["run_id"],"input_kind":if evidence=="Fixture"{"FIXTURE_RECORDED_CAR_SELECTION"}else{"AUTHENTIC_RECORDED_CAR_SELECTION"},"receipt_evidence":evidence,
        "bindings":first["bindings"],"stages":first["stages"],"slice_class":"ENGINEERING_VALIDATION_ONLY","root_to_slot_membership":"UNAVAILABLE","research_ready":false,
        "silver":"NOT_PRODUCED","physical_parquet_writer":"NOT_SELECTED","signature_crypto_verification":"NOT_PERFORMED","pump_event_decode":"NOT_PERFORMED","limitations":first["limitations"]});
    let mut records = Vec::new();
    let mut silver_records = Vec::new();
    let mut counts = BTreeMap::<String, u64>::new();
    let mut reasons = BTreeMap::<String, u64>::new();
    for key in [
        "raw_bytes",
        "verified_nodes",
        "verified_links",
        "entry_nodes",
        "transaction_envelopes",
        "pump_program_known_positive",
        "pump_program_unknown",
    ] {
        result[key] = json!(slots.iter().try_fold(0_u64, |sum, s| {
            sum.checked_add(
                s[key]
                    .as_u64()
                    .ok_or_else(|| invalid("SLOT_COUNT_MISSING"))?,
            )
            .ok_or_else(|| invalid("SLOT_COUNT_OVERFLOW"))
        })?);
    }
    for slot in &mut slots {
        if slot["receipt_evidence"] != evidence || slot["bindings"] != result["bindings"] {
            return Err(invalid("SELECTION_BINDING_OR_EVIDENCE_MISMATCH"));
        }
        for (field, total) in [("dispositions", &mut counts), ("reasons", &mut reasons)] {
            for (reason, n) in slot[field]
                .as_object()
                .ok_or_else(|| invalid("SLOT_COUNTS"))?
            {
                *total.entry(reason.clone()).or_default() +=
                    n.as_u64().ok_or_else(|| invalid("SLOT_COUNTS"))?;
            }
        }
        let rows = slot
            .as_object_mut()
            .ok_or_else(|| invalid("SLOT_REPORT"))?
            .remove("records")
            .ok_or_else(|| invalid("SLOT_RECORDS"))?;
        let Value::Array(mut rows) = rows else {
            return Err(invalid("SLOT_RECORDS"));
        };
        records.append(&mut rows);
        let Value::Array(mut facts) = slot
            .as_object_mut()
            .ok_or_else(|| invalid("SLOT_REPORT"))?
            .remove("silver_records")
            .ok_or_else(|| invalid("SLOT_SILVER"))?
        else {
            return Err(invalid("SLOT_SILVER"));
        };
        silver_records.append(&mut facts);
    }
    if counts.values().sum::<u64>() != records.len() as u64
        || result["transaction_envelopes"] != records.len()
    {
        return Err(invalid("SELECTION_ENVELOPE_COVERAGE"));
    }
    result["pump_program_involvement_transactions"] = if result["pump_program_unknown"] == 0 {
        result["pump_program_known_positive"].clone()
    } else {
        Value::Null
    };
    result["resource_accounting"] = json!({"record_json_bytes":record_bytes,"decoded_metadata_bytes":metadata_bytes,"max_record_json_bytes":MAX_SELECTION_RECORD_BYTES,"max_decoded_metadata_bytes":MAX_SELECTION_METADATA_BYTES,"max_selection_raw_bytes":MAX_SELECTION_RAW_BYTES,"max_slots":MAX_SELECTION_SLOTS,"per_slot_limits_unchanged":true});
    result["dispositions"] = json!(counts);
    result["reasons"] = json!(reasons);
    result["records_sha256"] = json!(sha256(&serde_json::to_vec(&records).map_err(invalid)?));
    result["analysis"] = pump::summary(&records);
    result["pump_event_decode"] = json!("PINNED_STRUCTURAL_PROBES_ONLY");
    result["records"] = json!(records);
    result["slots"] = json!(slots);
    result["silver"] = json!(if silver_records.is_empty() {
        "NOT_PRODUCED"
    } else {
        "BOUNDED_RECORDED_SELL_FACTS"
    });
    result["silver_fact_count"] = json!(silver_records.len());
    result["silver_records"] = json!(silver_records);
    Ok(result)
}

fn project_slot(
    run: &RecordedRun,
    published: &Published,
    verification: &Value,
) -> io::Result<Value> {
    let input_kind = match published.receipt.evidence.as_str() {
        "Fixture" => "FIXTURE_RECORDED_CAR_SLOT",
        "UNREVIEWED_AUTHENTIC_RAW" => "AUTHENTIC_RECORDED_CAR_SLOT",
        _ => return Err(invalid("UNSUPPORTED_RECEIPT_EVIDENCE")),
    };
    let RequestKind::CarRange {
        slot,
        start,
        end_exclusive,
        ..
    } = published.receipt.request.kind
    else {
        return Err(invalid("PAYLOAD_KIND"));
    };
    let raw = read_limited(&published.raw_path, archive::MAX_SLOT_BYTES as u64).map_err(invalid)?;
    if sha256(&raw) != published.receipt.sha256 || raw.len() as u64 != end_exclusive - start {
        return Err(invalid("RAW_CHANGED"));
    }
    let archive = archive::inspect(slot, &raw)?;
    let mut counts = BTreeMap::from([
        ("DECODED", 0_u64),
        ("MISSING", 0),
        ("QUARANTINED", 0),
        ("UNSUPPORTED", 0),
    ]);
    let mut reasons = BTreeMap::<String, u64>::new();
    let mut records = Vec::new();
    let mut silver_records = Vec::new();
    let decoder_source_sha256 = crate::source_sha256();
    let mut pump_count = 0;
    let mut pump_unknown = 0;
    let mut record_bytes = 0;
    let mut metadata_bytes = 0;
    for envelope in &archive.envelopes {
        let result = codec::decode(envelope, &archive.continuations);
        let (disposition, error, transaction) = match result {
            Ok(mut tx) => {
                inspect_pump(&mut tx)?;
                charge_metadata(
                    &mut metadata_bytes,
                    &tx,
                    slot,
                    envelope.transaction_index_in_slot,
                )?;
                if tx["pump_program_involvement"] == true {
                    pump_count += 1;
                }
                if tx["pump_program_involvement"].is_null() {
                    pump_unknown += 1;
                }
                ("DECODED", Value::Null, tx)
            }
            Err(e) => {
                pump_unknown += 1;
                let reason = e.to_string();
                let kind = if reason.starts_with("MISSING_") {
                    "MISSING"
                } else if reason.starts_with("UNSUPPORTED_") {
                    "UNSUPPORTED"
                } else {
                    "QUARANTINED"
                };
                *reasons.entry(reason.clone()).or_default() += 1;
                (kind, json!(reason), Value::Null)
            }
        };
        *counts
            .get_mut(disposition)
            .ok_or_else(|| invalid("DISPOSITION"))? += 1;
        let mut record = json!({"schema":SCHEMA,"input_kind":input_kind,"receipt_evidence":published.receipt.evidence,"decoder_source_sha256":decoder_source_sha256,"disposition":disposition,"reason":error,
            "source":{"run_id":run.snapshot.id,"raw_path":published.raw_path,"raw_sha256":published.receipt.sha256,"raw_bytes":raw.len(),"receipt_sequence":published.receipt.request.sequence,"bindings":verification["bindings"],"acquisition_executable_sha256":run.aggregate_plan.executable_sha256,
                "acquired_at_unix_ms":published.receipt.acquired_at.wall_ms.to_string(),"acquired_at_role":"OPERATIONAL_PROVENANCE_NOT_FEATURE","transaction_node_cid_hex":envelope.cid_hex,"raw_section_offset":envelope.raw_offset,"raw_section_length":envelope.raw_length,"car_section_offset":start+envelope.raw_offset as u64,"physical_node_index":envelope.physical_node_index,
                "inline_transaction_data_hex":hex::encode(&envelope.data.bytes),"inline_status_metadata_hex":hex::encode(&envelope.metadata.bytes),"dataframe_next_cids":envelope.data.next,"metadata_next_cids":envelope.metadata.next},
            "effective_at":{"slot":slot.to_string(),"entry_index":envelope.entry_index,"transaction_index_in_entry":envelope.transaction_index_in_entry,"transaction_index_in_slot":envelope.transaction_index_in_slot,"source_transaction_index":envelope.source_transaction_index.map(|n|n.to_string())},
            "atomic_observation_package":true,"observed_at":null,"actionable_at":null,"execution_opportunity_at":null,"observation_model_id":null,
            "slice_class":"ENGINEERING_VALIDATION_ONLY","transaction":transaction});
        attach_sample(&mut record, run.aggregate_plan.sample_identity.as_ref())?;
        charge_record(
            &mut record_bytes,
            &record,
            slot,
            envelope.transaction_index_in_slot,
        )?;
        append_sell_facts(&record, &mut record_bytes, &mut silver_records).map_err(|e| {
            record_limit_context(&e, slot, envelope.transaction_index_in_slot, "silver")
        })?;
        records.push(record);
    }
    let records_sha256 = sha256(&serde_json::to_vec(&records).map_err(invalid)?);
    let mut result = json!({"schema":SCHEMA,"run_id":run.snapshot.id,"input_kind":input_kind,"receipt_evidence":published.receipt.evidence,"slot":slot.to_string(),"raw_sha256":published.receipt.sha256,"raw_bytes":raw.len(),"car_range_start":start,"car_range_end_exclusive":end_exclusive,
        "bindings":verification["bindings"],"stages":{"capture":"PUBLISHED","raw_receipts":"VERIFIED","car_slot":"VERIFIED","domain_decoding":"BOUNDED_ATOMIC_TRANSACTION_STATUS"},
        "verified_nodes":archive.verified_nodes,"verified_links":archive.verified_links,"entry_nodes":archive.entries,"transaction_envelopes":archive.envelopes.len(),"dispositions":counts,"reasons":reasons,
        "pump_program_involvement_transactions":if pump_unknown==0{Some(pump_count)}else{None},"pump_program_known_positive":pump_count,"pump_program_unknown":pump_unknown,"pump_event_decode":"PINNED_STRUCTURAL_PROBES_ONLY","root_to_slot_membership":"UNAVAILABLE","signature_crypto_verification":"NOT_PERFORMED",
        "resource_accounting":{"record_json_bytes":record_bytes,"decoded_metadata_bytes":metadata_bytes,"max_record_json_bytes":MAX_RECORD_JSON_BYTES,"max_decoded_metadata_bytes":MAX_DECODED_METADATA_BYTES},
        "slice_class":"ENGINEERING_VALIDATION_ONLY","research_ready":false,"silver":if silver_records.is_empty(){"NOT_PRODUCED"}else{"BOUNDED_RECORDED_SELL_FACTS"},"silver_fact_count":silver_records.len(),"silver_records":silver_records,"physical_parquet_writer":"NOT_SELECTED",
        "records_sha256":records_sha256,"analysis":pump::summary(&records),"records":records,
        "limitations":["No token names, tickers, launch dates or lifecycle inference","Buy structural probes remain unadmitted; separate sell facts are recorded instruction/events, not account state or historical activation","Token balances, rewards, return data and unknown protobuf fields remain unprojected; original protobuf retained","No outcome-independent sample, economic/executable price, strategy or edge claim","No reconstructed observation/actionability model"]});
    attach_sample(&mut result, run.aggregate_plan.sample_identity.as_ref())?;
    Ok(result)
}

fn charge_record(total: &mut usize, record: &Value, slot: u64, index: usize) -> io::Result<()> {
    charge(
        total,
        serde_json::to_vec(record).map_err(invalid)?.len(),
        MAX_RECORD_JSON_BYTES,
    )
    .map_err(|e| record_limit_context(&e, slot, index, "bronze"))
}

fn charge_metadata(total: &mut usize, tx: &Value, slot: u64, index: usize) -> io::Result<()> {
    let bytes = usize::try_from(
        tx["decoded_metadata_bytes"]
            .as_u64()
            .ok_or_else(|| invalid("METADATA_SIZE"))?,
    )
    .map_err(invalid)?;
    charge(total, bytes, MAX_DECODED_METADATA_BYTES).map_err(|e| {
        invalid(format!(
            "{e} budget=decoded_metadata_bytes slot={slot} transaction_index={index}"
        ))
    })
}

fn record_limit_context(error: &io::Error, slot: u64, index: usize, layer: &str) -> io::Error {
    invalid(format!(
        "{error} budget=record_json_bytes slot={slot} transaction_index={index} layer={layer}"
    ))
}

fn inspect_pump(tx: &mut Value) -> io::Result<()> {
    if tx["pump_program_involvement"] == true {
        tx["pump_structural_analysis"] = pump::inspect(tx)?;
        tx["pump_sell_analysis"] = json!(pump_sell::inspect(tx)?);
    }
    // Keep the prior buy-probe field; the new sell lane is explicitly separate.
    tx["pump_event_decode"] = json!("PINNED_STRUCTURAL_PROBES_ONLY");
    Ok(())
}

fn append_sell_facts(
    record: &Value,
    record_bytes: &mut usize,
    silver_records: &mut Vec<Value>,
) -> io::Result<()> {
    for fact in pump_sell::facts(record)? {
        // Derived output uses the SAME existing per-slot/selection JSON cap.
        charge(
            record_bytes,
            serde_json::to_vec(&fact).map_err(invalid)?.len(),
            MAX_RECORD_JSON_BYTES,
        )?;
        silver_records.push(fact);
    }
    Ok(())
}

fn escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn overview_html(report: &Value) -> String {
    let count = |v: &Value| {
        v.as_u64()
            .map_or_else(|| "UNAVAILABLE".to_owned(), |n| n.to_string())
    };
    let mut program_counts = BTreeMap::<String, u64>::new();
    if let Some(records) = report["records"].as_array() {
        for record in records {
            if let Some(programs) = record["transaction"]["program_ids"].as_array() {
                for program in programs.iter().filter_map(Value::as_str) {
                    *program_counts.entry(program.to_owned()).or_default() += 1;
                }
            }
        }
    }
    let mut overview = format!(
        "<section><h2>{} · dekking</h2><p><strong>{}</strong> enveloppen · <strong>{}</strong> volledig gedecodeerd · missing {} · unsupported {} · quarantined {}</p><p>Pump-programmaverwijzing: {} bekend positief, {} onbekend. Layoutonderzoek is apart van candidate-admission en gecommitteerde toestand.</p><p>Raw-bytes: {}</p>",
        report["slot"].as_str().map_or_else(
            || "Meervoudige selectie".into(),
            |s| format!("Slot {}", escape(s))
        ),
        count(&report["transaction_envelopes"]),
        count(&report["dispositions"]["DECODED"]),
        count(&report["dispositions"]["MISSING"]),
        count(&report["dispositions"]["UNSUPPORTED"]),
        count(&report["dispositions"]["QUARANTINED"]),
        count(&report["pump_program_known_positive"]),
        count(&report["pump_program_unknown"]),
        count(&report["raw_bytes"])
    );
    overview.push_str("<table><tr><th>Slot</th><th>Raw-bytes</th><th>Enveloppen</th><th>Decoded</th><th>Missing / unsupported / quarantined</th><th>Pump + / onbekend</th><th>Raw SHA-256</th></tr>");
    let slots: Vec<&Value> = report["slots"]
        .as_array()
        .map_or_else(|| vec![report], |s| s.iter().collect());
    for slot in slots {
        let _ = write!(
            overview,
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{} / {} / {}</td><td>{} / {}</td><td class=mono>{}</td></tr>",
            escape(slot["slot"].as_str().unwrap_or("UNAVAILABLE")),
            count(&slot["raw_bytes"]),
            count(&slot["transaction_envelopes"]),
            count(&slot["dispositions"]["DECODED"]),
            count(&slot["dispositions"]["MISSING"]),
            count(&slot["dispositions"]["UNSUPPORTED"]),
            count(&slot["dispositions"]["QUARANTINED"]),
            count(&slot["pump_program_known_positive"]),
            count(&slot["pump_program_unknown"]),
            escape(slot["raw_sha256"].as_str().unwrap_or("UNAVAILABLE"))
        );
    }
    overview.push_str("</table><h3>Behouden transactiestatus en votes</h3><pre>");
    overview.push_str(&escape(&format!(
        "Status: {} · vote-program-transacties: {}",
        report["analysis"]["transaction_status_counts"],
        report["analysis"]["vote_program_transactions_retained"]
    )));
    overview.push_str("</pre><h2>Pump: behouden buy-layoutonderzoek en aparte sell-route</h2><p>De buy-probes hieronder produceren geen Silver. De afzonderlijke sell-route toont alleen volledig brongebonden instructie/event-feiten. Programmaverwijzing ≠ ondersteunde instructie/event ≠ gecommitteerde accounttoestand. Root-to-slot membership en economische identiteit blijven UNAVAILABLE.</p><pre>");
    overview.push_str(&escape(
        &serde_json::to_string_pretty(&report["analysis"]["pump_layout_outcomes"])
            .unwrap_or_default(),
    ));
    overview.push_str("</pre>");
    if let Some(cases) = report["analysis"]["pump_cases"].as_array() {
        for case in cases {
            overview.push_str(&buy_diagnostic_html(case));
            let _ = write!(
                overview,
                "<details><summary>Slot {} · transactie {} · {}</summary><pre>{}</pre></details>",
                escape(
                    case["effective_at"]["slot"]
                        .as_str()
                        .unwrap_or("UNAVAILABLE")
                ),
                case["effective_at"]["transaction_index_in_slot"],
                escape(case["signature"].as_str().unwrap_or("UNAVAILABLE")),
                escape(&serde_json::to_string_pretty(case).unwrap_or_default())
            );
        }
    }
    overview.push_str("<details><summary>Programmafrequenties</summary><p>Unieke transactiepakketten per programma; gedeclareerde top-level + vastgelegde CPI, geen succesvol uitgevoerde toestandsveranderingen. Exacte afzonderlijke referentietellingen staan in JSON.</p><ul>");
    for (program, number) in program_counts {
        let _ = write!(
            overview,
            "<li><code>{}</code>: {number} transactiepakketten</li>",
            escape(&program)
        );
    }
    overview.push_str("</ul></details></section>");
    overview.push_str(&sell_html(report));
    overview
}

fn sell_outcomes_html(report: &Value) -> String {
    let mut out = String::new();
    out.push_str("<p>Geneste CPI-privileges: <strong>UNAVAILABLE</strong>. Message-signer/writable zijn alleen message-capaciteit, geen opgenomen CPI-vlaggen. Succes of een PDA-match vult die vlaggen niet in.</p><table class=sell-outcomes><thead><tr><th>Slot / transactie</th><th>Instructiecontext</th><th>Uitkomst</th><th>Eigen event-CPI</th></tr></thead><tbody>");
    if let Some(records) = report["records"].as_array() {
        for record in records {
            for d in record["transaction"]["pump_sell_analysis"]
                .as_array()
                .into_iter()
                .flatten()
            {
                let _ = write!(
                    out,
                    "<tr><td>{} / {}</td><td>outer {} · inner {}</td><td>{}</td><td>inner {} · height {}</td></tr>",
                    escape(
                        record["effective_at"]["slot"]
                            .as_str()
                            .unwrap_or("UNAVAILABLE")
                    ),
                    record["effective_at"]["transaction_index_in_slot"],
                    d["outer_index"],
                    d["instruction_inner_order"],
                    escape(d["disposition"].as_str().unwrap_or("UNAVAILABLE")),
                    d["event_context"]["inner_order"],
                    d["event_context"]["stack_height"]
                );
            }
        }
    }
    out.push_str("</tbody></table>");
    out
}

fn sell_trace_html(d: &Value) -> String {
    let mut out = String::new();
    if let Some(trace) = d["event_context"]["ordered_group_trace"].as_array() {
        let _ = write!(
            out,
            "<h4>Opgenomen invocation-volgorde</h4><p>Sell-subtree: [{} , {}) · eigen event inner {}. Parentidentiteit volgt uit de stack; rootprogramma niet geïnterpreteerd.</p><table class=sell-trace><thead><tr><th>Inner</th><th>Hoogte</th><th>Parent-inner (null = outer)</th><th>Programma</th></tr></thead><tbody>",
            d["event_context"]["subtree"]["start_inner_order"],
            d["event_context"]["subtree"]["end_inner_order_exclusive"],
            d["event_context"]["inner_order"]
        );
        for call in trace {
            let _ = write!(
                out,
                "<tr><td>{}</td><td>{}</td><td>{}</td><td class=mono>{}</td></tr>",
                call["inner_order"],
                call["stack_height"],
                call["parent_inner_order"],
                escape(call["program_id"].as_str().unwrap_or("UNAVAILABLE"))
            );
        }
        out.push_str("</tbody></table>");
    }
    out
}

fn sell_html(report: &Value) -> String {
    let facts = report["silver_records"].as_array().map_or(0, Vec::len);
    let mut out = format!(
        "<section id=pump-sell><h2>Pump sell — brongebonden Silver-eventfeiten</h2><p class=notice><strong>{facts} gekoppelde instructie/event-pakketten.</strong> Dit zijn vastgelegde feiten uit een succesvolle transactie, geen gelezen accounttoestand, netto-opbrengst, Research Ready dataset of edge. B4 en B5 blijven open. Quote-mintidentiteit en decimals onbekend; eventfees niet als optelbare kosten of netto-opbrengst behandelen.</p><p><a href=silver.jsonl>Silver JSONL</a> · <a href=quality.json>Volledige diagnose en bronbinding</a></p>"
    );
    if facts == 0 {
        out.push_str("<p>Geen Silver geproduceerd voor deze invoer.</p>");
    }
    out.push_str(&sell_outcomes_html(report));
    if let Some(records) = report["records"].as_array() {
        for record in records {
            if let Some(ds) = record["transaction"]["pump_sell_analysis"].as_array() {
                for d in ds {
                    let _ = write!(
                        out,
                        "<section class=sell-observation><h3>Slot {} · transactie {} · outer {}</h3><p>Uitkomst: <strong>{}</strong> · reden: <code>{}</code></p><p>Volledige instructie ({} bytes):</p><pre>{}</pre><pre>{}</pre><p>Kandidaat uit waarnemingspredicaten:</p><pre>{}</pre><h4>Alle {} accountposities</h4><article><table class=sell-accounts><thead><tr><th>Positie</th><th>Rol</th><th>Adres</th><th>Adresmatch</th><th>Message-minimumflags</th><th>Werkelijke CPI-flags</th></tr></thead><tbody>",
                        escape(
                            record["effective_at"]["slot"]
                                .as_str()
                                .unwrap_or("UNAVAILABLE")
                        ),
                        record["effective_at"]["transaction_index_in_slot"],
                        d["outer_index"],
                        escape(d["disposition"].as_str().unwrap_or("UNAVAILABLE")),
                        escape(&d["reason"].to_string()),
                        d["instruction_bytes"],
                        escape(d["instruction_data_hex"].as_str().unwrap_or("UNAVAILABLE")),
                        escape(
                            &serde_json::to_string_pretty(&d["instruction"]).unwrap_or_default()
                        ),
                        escape(
                            &serde_json::to_string_pretty(&d["candidate_predicates"])
                                .unwrap_or_default()
                        ),
                        d["account_count"]
                    );
                    if let Some(rows) = d["accounts"].as_array() {
                        for r in rows {
                            let _ = write!(
                                out,
                                "<tr><td>{}</td><td>{}</td><td class=mono>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
                                r["position"],
                                escape(r["role"].as_str().unwrap_or("UNAVAILABLE")),
                                escape(r["observed"].as_str().unwrap_or("UNAVAILABLE")),
                                r["address_match"],
                                r.get("message_minimum_privileges_match")
                                    .unwrap_or(&r["required_privileges_match"]),
                                if d["instruction_inner_order"].is_null() {
                                    "Directe instructie; geen CPI-flags"
                                } else {
                                    "UNAVAILABLE"
                                }
                            );
                        }
                    }
                    out.push_str("</tbody></table></article>");
                    out.push_str(&sell_trace_html(d));
                    let _ = write!(
                        out,
                        "<details><summary>Event-CPI, exacte eventvelden en provenance</summary><pre>{}</pre><pre>{}</pre><p>Raw SHA-256: <code>{}</code> · bronontvangst SHA-256: <code>{}</code></p></details></section>",
                        escape(
                            &serde_json::to_string_pretty(&d["event_context"]).unwrap_or_default()
                        ),
                        escape(
                            &serde_json::to_string_pretty(&d["event_reported"]).unwrap_or_default()
                        ),
                        escape(
                            record["source"]["raw_sha256"]
                                .as_str()
                                .unwrap_or("UNAVAILABLE")
                        ),
                        escape(
                            d["source_evidence_sha256"]
                                .as_str()
                                .unwrap_or("UNAVAILABLE")
                        )
                    );
                }
            }
        }
    }
    out.push_str("</section>");
    out
}

fn buy_diagnostic_html(case: &Value) -> String {
    let mut out = String::new();
    let Some(diagnostics) = case["analysis"]["buy_source_diagnostics"].as_array() else {
        return out;
    };
    for d in diagnostics {
        let _ = write!(
            out,
            "<section class=buy-diagnostic><h2>Pump buy: volledige bytes, accounts en bewijsgrens</h2><p>Slot {} · transactie {} · outer {}</p><p class=notice><strong>Geen Silver.</strong> De accountindeling en eventcorrelatie worden afzonderlijk gecontroleerd. Een overeenkomst bewijst geen toegestane extra instructiebyte, accountinhoud of gecommitteerde toestand.</p><h3>Volledige instructie: {} bytes</h3><pre>{}</pre><p>Volledige layoutmatch: <strong>{}</strong>. Oorspronkelijke fout: <code>{}</code>.</p><p>Onverklaarde suffix vanaf offset {}: <code>{}</code> — betekenis UNKNOWN, niet verwijderd.</p><h3>Alleen interpretatie van bekende prefixvelden</h3><pre>{}</pre><h3>18 accountposities — adres- en minimumprivilegecorrespondentie</h3><p>Patrooncorrespondentie: {}. Accountinhoud niet geverifieerd. PDA-afleiding uit event-creator is geen bewijs van de creator in de curve-account.</p><article><table><thead><tr><th>Positie</th><th>Rol</th><th>Adres</th><th>Adresmatch</th><th>Privileges</th><th>Basis</th></tr></thead><tbody>",
            escape(
                case["effective_at"]["slot"]
                    .as_str()
                    .unwrap_or("UNAVAILABLE")
            ),
            case["effective_at"]["transaction_index_in_slot"],
            d["outer_index"],
            d["full_bytes"],
            escape(d["full_data_hex"].as_str().unwrap_or("UNAVAILABLE")),
            d["full_instruction_layout_match"],
            escape(&d["full_instruction_error"].to_string()),
            d["unexplained_suffix"]["offset"],
            escape(
                d["unexplained_suffix"]["hex"]
                    .as_str()
                    .unwrap_or("UNAVAILABLE")
            ),
            escape(&serde_json::to_string_pretty(&d["prefix_diagnostic"]).unwrap_or_default()),
            d["account_address_correspondence"]
        );
        if let Some(rows) = d["accounts"].as_array() {
            for row in rows {
                let _ = write!(
                    out,
                    "<tr><td>{}</td><td>{}</td><td class=mono>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
                    row["position"],
                    escape(row["role"].as_str().unwrap_or("UNAVAILABLE")),
                    escape(row["observed"].as_str().unwrap_or("UNAVAILABLE")),
                    row["address_match"],
                    row["required_privileges_match"],
                    escape(row["address_basis"].as_str().unwrap_or("UNAVAILABLE"))
                );
            }
        }
        let _ = write!(
            out,
            "</tbody></table></article><h3>Bijbehorende event-CPI en correlaties</h3><pre>{}</pre><pre>{}</pre><p>Bronontvangst SHA-256: <code>{}</code>. Volledige instructie SHA-256: <code>{}</code>. Raw SHA-256: <code>{}</code>.</p><p><strong>Kleinste ontbrekende bewijsstap:</strong> een Pump-autoritatieve decode-/compatibiliteitsregel voor alle instructiebytes. Een generieke Anchor-handler is daarvoor onvoldoende. Quote-identiteit, decimals en historische activatie blijven afzonderlijk onbekend.</p></section>",
            escape(&serde_json::to_string_pretty(&d["event_context"]).unwrap_or_default()),
            escape(&serde_json::to_string_pretty(&d["event_correlation"]).unwrap_or_default()),
            escape(
                d["source_evidence_sha256"]
                    .as_str()
                    .unwrap_or("UNAVAILABLE")
            ),
            escape(d["full_data_sha256"].as_str().unwrap_or("UNAVAILABLE")),
            escape(case["raw_sha256"].as_str().unwrap_or("UNAVAILABLE"))
        );
    }
    out
}

/// A standalone browser report, deliberately without remote resources or scripts.
#[must_use]
pub fn html(report: &Value) -> String {
    let input_label = escape(
        report["input_kind"]
            .as_str()
            .unwrap_or("UNAVAILABLE_INPUT_KIND"),
    );
    let overview = overview_html(report);
    let summary = report
        .as_object()
        .map(|m| {
            m.iter()
                .filter(|(k, _)| *k != "records")
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect::<serde_json::Map<_, _>>()
        })
        .unwrap_or_default();
    let mut rows = String::new();
    if let Some(records) = report["records"].as_array() {
        for r in records {
            let tx = &r["transaction"];
            let signature = tx["signatures"][0].as_str().unwrap_or("UNAVAILABLE");
            let programs = tx["program_ids"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .unwrap_or_default();
            let _ = write!(
                rows,
                "<tr><td>{}</td><td>{}</td><td>{}</td><td class=mono>{}</td><td>{}</td><td>{}</td><td><pre>{}</pre></td><td>{}</td></tr>",
                escape(r["effective_at"]["slot"].as_str().unwrap_or("UNAVAILABLE")),
                r["effective_at"]["transaction_index_in_slot"],
                escape(r["disposition"].as_str().unwrap_or("UNKNOWN")),
                escape(signature),
                escape(tx["status"].as_str().unwrap_or("UNAVAILABLE")),
                escape(tx["fee_lamports"].as_str().unwrap_or("UNAVAILABLE")),
                escape(&programs),
                escape(&r["reason"].to_string())
            );
        }
    }
    let page = format!(
        "<!doctype html><html lang=nl><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'><title>Raw → Bronze — kwaliteitsrapport</title><style>body{{font:16px system-ui;background:#111827;color:#e5e7eb;margin:32px}}h1{{color:#67e8f9}}a{{color:#67e8f9}}.notice{{padding:16px;background:#253047;border-left:4px solid #fbbf24}}pre,.mono{{font:12px ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid #374151;padding:8px;text-align:left;vertical-align:top}}th{{background:#253047;position:sticky;top:0}}summary{{cursor:pointer}}article{{overflow:auto}}</style><h1>Raw → Bronze → beperkte Silver-eventfeiten</h1><p class=notice><strong>{input_label}</strong><br>ENGINEERING_VALIDATION_ONLY · transaction-wire + statusmetadata, behouden buy-diagnose en aparte brongebonden sell-feiten. Geen research-ready dataset of edgeclaim. Root-to-slot membership: UNAVAILABLE. Ontbrekend is nooit nul.</p><p><a href=quality.json>Volledig JSON-rapport + records</a> · <a href=bronze.jsonl>Bronze JSONL</a> · <a href=execution.json>Uitvoeringsidentiteit</a></p>{overview}<details><summary>Bronbinding, dekking en beperkingen</summary><pre>{}</pre></details><details><summary>Alle transactie-enveloppen in bronvolgorde</summary><article><table><thead><tr><th>Slot</th><th>Volgorde</th><th>Decode</th><th>Eerste signature</th><th>Status</th><th>Fee (lamports)</th><th>Programma's (top-level/CPI)</th><th>Reden</th></tr></thead><tbody>{rows}</tbody></table></article></details></html>",
        escape(&serde_json::to_string_pretty(&summary).unwrap_or_default())
    );
    if report["slice_class"] == "RESEARCH_SAMPLING" {
        page.replace(
            "ENGINEERING_VALIDATION_ONLY · transaction-wire",
            "RESEARCH_SAMPLING (identiteit, geen geschiktheidspromotie) · transaction-wire",
        )
    } else {
        page
    }
}
