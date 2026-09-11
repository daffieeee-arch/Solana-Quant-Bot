//! Deterministic Bronze JSON and self-contained quality HTML; operational
//! processing time and executable identity belong in a separate execution receipt.
use crate::{archive, codec, invalid};
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

/// Explicit aggregate resident-output budget, in addition to per-frame bounds.
/// # Errors
/// Fails before publishing a partial report when aggregate decoding exceeds scope.
pub fn charge(total: &mut usize, bytes: usize, limit: usize) -> io::Result<()> {
    let next = total
        .checked_add(bytes)
        .ok_or_else(|| invalid("BRONZE_AGGREGATE_LIMIT"))?;
    if next > limit {
        return Err(invalid("BRONZE_AGGREGATE_LIMIT"));
    }
    *total = next;
    Ok(())
}

/// Full run/receipt/plan gate followed by one complete slot's atomic transactions.
/// # Errors
/// Fails before publication on run, receipt, CID, graph or input identity changes.
pub fn decode_run(root: &Path) -> io::Result<Value> {
    let verification = verify_recorded(root, None)?;
    if verification["stages"]["raw_receipts"] != "VERIFIED" {
        return Err(invalid("RAW_RECEIPTS_UNVERIFIED"));
    }
    let run = read_run_context(root)?;
    if run.aggregate_plan.epoch != 978 {
        return Err(invalid("UNSUPPORTED_EPOCH_OUTSIDE_BOUNDED_978_LANE"));
    }
    let payloads = run
        .published
        .iter()
        .filter(|p| matches!(p.receipt.request.kind, RequestKind::CarRange { .. }))
        .collect::<Vec<_>>();
    if payloads.is_empty() {
        return Ok(
            json!({"schema":SCHEMA,"run_id":run.snapshot.id,"bindings":verification["bindings"],"input_kind":"METADATA_ONLY","records":[],"domain_decoding":"UNAVAILABLE_NO_PAYLOAD","decoded_transactions":null,"research_ready":false}),
        );
    }
    if payloads.len() != 1 {
        return Err(invalid("UNSUPPORTED_MULTI_RANGE_INPUT"));
    }
    if verification["stages"]["car_slot"] != "VERIFIED" {
        return Err(invalid(format!(
            "CAR_SLOT_UNVERIFIED: {}",
            verification["integrity"]["error"]
        )));
    }
    let projected = project_slot(&run, payloads[0], &verification)?;
    let after = verify_recorded(root, None)?;
    if after["bindings"] != verification["bindings"] || after["run_id"] != verification["run_id"] {
        return Err(invalid("INPUT_CHANGED_DURING_DECODE"));
    }
    Ok(projected)
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
    let decoder_source_sha256 = crate::source_sha256();
    let mut pump_count = 0;
    let mut pump_unknown = 0;
    let mut record_bytes = 0;
    let mut metadata_bytes = 0;
    for envelope in &archive.envelopes {
        let result = codec::decode(envelope, &archive.continuations);
        let (disposition, error, transaction) = match result {
            Ok(tx) => {
                charge(
                    &mut metadata_bytes,
                    usize::try_from(
                        tx["decoded_metadata_bytes"]
                            .as_u64()
                            .ok_or_else(|| invalid("METADATA_SIZE"))?,
                    )
                    .map_err(invalid)?,
                    MAX_DECODED_METADATA_BYTES,
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
        let record = json!({"schema":SCHEMA,"input_kind":input_kind,"receipt_evidence":published.receipt.evidence,"decoder_source_sha256":decoder_source_sha256,"disposition":disposition,"reason":error,
            "source":{"run_id":run.snapshot.id,"raw_path":published.raw_path,"raw_sha256":published.receipt.sha256,"raw_bytes":raw.len(),"receipt_sequence":published.receipt.request.sequence,"bindings":verification["bindings"],"acquisition_executable_sha256":run.aggregate_plan.executable_sha256,
                "acquired_at_unix_ms":published.receipt.acquired_at.wall_ms.to_string(),"acquired_at_role":"OPERATIONAL_PROVENANCE_NOT_FEATURE","transaction_node_cid_hex":envelope.cid_hex,"raw_section_offset":envelope.raw_offset,"raw_section_length":envelope.raw_length,"car_section_offset":start+envelope.raw_offset as u64,"physical_node_index":envelope.physical_node_index,
                "inline_transaction_data_hex":hex::encode(&envelope.data.bytes),"inline_status_metadata_hex":hex::encode(&envelope.metadata.bytes),"dataframe_next_cids":envelope.data.next,"metadata_next_cids":envelope.metadata.next},
            "effective_at":{"slot":slot.to_string(),"entry_index":envelope.entry_index,"transaction_index_in_entry":envelope.transaction_index_in_entry,"transaction_index_in_slot":envelope.transaction_index_in_slot,"source_transaction_index":envelope.source_transaction_index.map(|n|n.to_string())},
            "atomic_observation_package":true,"observed_at":null,"actionable_at":null,"execution_opportunity_at":null,"observation_model_id":null,
            "slice_class":"ENGINEERING_VALIDATION_ONLY","transaction":transaction});
        charge(
            &mut record_bytes,
            serde_json::to_vec(&record).map_err(invalid)?.len(),
            MAX_RECORD_JSON_BYTES,
        )?;
        records.push(record);
    }
    let records_sha256 = sha256(&serde_json::to_vec(&records).map_err(invalid)?);
    Ok(
        json!({"schema":SCHEMA,"run_id":run.snapshot.id,"input_kind":input_kind,"receipt_evidence":published.receipt.evidence,"slot":slot.to_string(),"raw_sha256":published.receipt.sha256,"raw_bytes":raw.len(),"car_range_start":start,"car_range_end_exclusive":end_exclusive,
        "bindings":verification["bindings"],"stages":{"capture":"PUBLISHED","raw_receipts":"VERIFIED","car_slot":"VERIFIED","domain_decoding":"BOUNDED_ATOMIC_TRANSACTION_STATUS"},
        "verified_nodes":archive.verified_nodes,"verified_links":archive.verified_links,"entry_nodes":archive.entries,"transaction_envelopes":archive.envelopes.len(),"dispositions":counts,"reasons":reasons,
        "pump_program_involvement_transactions":if pump_unknown==0{Some(pump_count)}else{None},"pump_program_known_positive":pump_count,"pump_program_unknown":pump_unknown,"pump_event_decode":"NOT_PERFORMED","root_to_slot_membership":"UNAVAILABLE","signature_crypto_verification":"NOT_PERFORMED",
        "resource_accounting":{"record_json_bytes":record_bytes,"decoded_metadata_bytes":metadata_bytes,"max_record_json_bytes":MAX_RECORD_JSON_BYTES,"max_decoded_metadata_bytes":MAX_DECODED_METADATA_BYTES},
        "slice_class":"ENGINEERING_VALIDATION_ONLY","research_ready":false,"silver":"NOT_PRODUCED","physical_parquet_writer":"NOT_SELECTED",
        "records_sha256":records_sha256,"records":records,
        "limitations":["No token names, tickers, launch dates or lifecycle inference","No Pump event decoding or universal historical schema claim","Token balances, rewards, return data and unknown protobuf fields remain unprojected; original protobuf retained","No outcome-independent sample, economic/executable price, strategy or edge claim","No reconstructed observation/actionability model"]}),
    )
}

fn escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

/// A standalone browser report, deliberately without remote resources or scripts.
#[must_use]
pub fn html(report: &Value) -> String {
    let input_label = escape(
        report["input_kind"]
            .as_str()
            .unwrap_or("UNAVAILABLE_INPUT_KIND"),
    );
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
        "<section><h2>Slot {} · dekking</h2><p><strong>{}</strong> enveloppen · <strong>{}</strong> volledig gedecodeerd · missing {} · unsupported {} · quarantined {}</p><p>Pump-programmaverwijzing: {} bekend positief, {} onbekend. Geen Pump-eventdecode.</p><p class=mono>Raw SHA-256: {}</p><h3>Programma's in de gedecodeerde pakketten</h3><p>Gedeclareerde top-level instructies + vastgelegde CPI; geen zelfstandig uitvoeringsbewijs. Vote-transacties zijn behouden.</p><ul>",
        escape(report["slot"].as_str().unwrap_or("UNAVAILABLE")),
        count(&report["transaction_envelopes"]),
        count(&report["dispositions"]["DECODED"]),
        count(&report["dispositions"]["MISSING"]),
        count(&report["dispositions"]["UNSUPPORTED"]),
        count(&report["dispositions"]["QUARANTINED"]),
        count(&report["pump_program_known_positive"]),
        count(&report["pump_program_unknown"]),
        escape(report["raw_sha256"].as_str().unwrap_or("UNAVAILABLE"))
    );
    for (program, number) in program_counts {
        let _ = write!(
            overview,
            "<li><code>{}</code>: {number} transactiepakketten</li>",
            escape(&program)
        );
    }
    overview.push_str("</ul></section>");
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
                "<tr><td>{}</td><td>{}</td><td class=mono>{}</td><td>{}</td><td>{}</td><td><pre>{}</pre></td><td>{}</td></tr>",
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
    format!(
        "<!doctype html><html lang=nl><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'><title>Raw → Bronze — kwaliteitsrapport</title><style>body{{font:16px system-ui;background:#111827;color:#e5e7eb;margin:32px}}h1{{color:#67e8f9}}a{{color:#67e8f9}}.notice{{padding:16px;background:#253047;border-left:4px solid #fbbf24}}pre,.mono{{font:12px ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid #374151;padding:8px;text-align:left;vertical-align:top}}th{{background:#253047;position:sticky;top:0}}summary{{cursor:pointer}}article{{overflow:auto}}</style><h1>Raw → Bronze</h1><p class=notice><strong>{input_label}</strong><br>ENGINEERING_VALIDATION_ONLY · transaction-wire + statusmetadata, geen Pump-eventdecode, Silver, research-ready dataset of edgeclaim. Root-to-slot membership: UNAVAILABLE. Ontbrekend is nooit nul.</p><p><a href=quality.json>Volledig JSON-rapport + records</a> · <a href=bronze.jsonl>Bronze JSONL</a> · <a href=execution.json>Uitvoeringsidentiteit</a></p>{overview}<details><summary>Bronbinding, dekking en beperkingen</summary><pre>{}</pre></details><h2>Alle transactie-enveloppen in bronvolgorde</h2><article><table><thead><tr><th>Volgorde</th><th>Decode</th><th>Eerste signature</th><th>Status</th><th>Fee (lamports)</th><th>Programma's (top-level/CPI)</th><th>Reden</th></tr></thead><tbody>{rows}</tbody></table></article></html>",
        escape(&serde_json::to_string_pretty(&summary).unwrap_or_default())
    )
}
