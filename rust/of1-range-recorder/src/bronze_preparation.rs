//! Read-only Raw → archival-envelope preparation, not transaction Bronze/Silver.
//! Hash/receipt validation is shared with the monitor. One selected slot is inspected
//! through the existing CAR parser; opaque `DataFrames` are never a second Pump decoder.

use crate::{
    acquisition::{derive_payload_from_metadata, read_limited},
    car::{SlotArchivalInspection, VerificationLimits, inspect_slot_sections},
    durable::acquisition::{Published, RequestKind},
    monitor::{RecordedRun, read_run_context},
    sha256,
};
use serde::Serialize;
use std::{fmt::Write as _, io, path::Path};

#[derive(Clone, Copy, Debug, Serialize)]
pub struct Limits {
    pub max_slots: u64,
    pub max_slot_bytes: usize,
    pub max_nodes: usize,
    pub max_links: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_slots: 1,
            max_slot_bytes: 16_777_216,
            max_nodes: 65_536,
            max_links: 262_144,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct RawProvenance {
    pub sequence: String,
    pub receipt_path: String,
    pub receipt_sha256: String,
    pub raw_path: String,
    pub raw_sha256: String,
    pub response_entity_bytes: String,
    pub source_host: String,
    pub source_path: String,
    pub acquired_at_wall_ms: String,
    pub timestamp_role: &'static str,
    pub source_evidence: String,
}

/// Each assembled byte maps to exactly one receipt and its original CAR range.
#[derive(Debug, Serialize)]
pub struct RawRange {
    pub sequence: String,
    pub assembled_offset: String,
    pub length: String,
    pub car_offset: String,
    pub raw_sha256: String,
}

#[derive(Debug, Serialize)]
pub struct SlotReport {
    pub slot: String,
    pub state: &'static str,
    pub ranges: Vec<RawRange>,
    pub assembled_sha256: Option<String>,
    pub inspection: Option<SlotArchivalInspection>,
    pub quarantine_reason: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct QualityReport {
    pub schema: &'static str,
    pub component: &'static str,
    pub reader_source_sha256: String,
    pub reader_source_files: Vec<SourceFile>,
    pub reader_identity_scope: &'static str,
    pub delivery_evidence: &'static str,
    pub source_evidence: &'static str,
    pub input_kind: &'static str,
    pub run_id: String,
    pub run_manifest_sha256: String,
    pub payload_manifest_sha256: Option<String>,
    pub source_code_sha: String,
    pub source_toolchain_fingerprint: String,
    pub source_executable_sha256: String,
    pub epoch: String,
    pub limits: Limits,
    pub publications: Vec<RawProvenance>,
    pub missing_metadata_sequences: Vec<String>,
    pub index_reported_absent_slots: Vec<String>,
    pub selected_slots: Vec<SlotReport>,
    pub solana_transaction_decode: &'static str,
    pub pump_decode: &'static str,
    pub decoded_transaction_count: Option<u64>,
    pub decoded_pump_event_count: Option<u64>,
    pub dataframe_payload_and_checksum: &'static str,
    pub historical_activation: &'static str,
    pub root_to_slot_membership: &'static str,
    pub research_ready: bool,
    pub limitations: Vec<&'static str>,
}

#[derive(Debug, Serialize)]
pub struct SourceFile {
    pub path: &'static str,
    pub bytes: usize,
    pub sha256: String,
}

fn reader_sources() -> Vec<SourceFile> {
    [
        (
            "src/bronze_preparation.rs",
            include_bytes!("bronze_preparation.rs").as_slice(),
        ),
        (
            "src/bin/of1-bronze-evidence.rs",
            include_bytes!("bin/of1-bronze-evidence.rs").as_slice(),
        ),
        ("src/car.rs", include_bytes!("car.rs").as_slice()),
        (
            "src/monitor/recorded.rs",
            include_bytes!("monitor/recorded.rs").as_slice(),
        ),
        (
            "src/monitor/mod.rs",
            include_bytes!("monitor/mod.rs").as_slice(),
        ),
        (
            "src/acquisition.rs",
            include_bytes!("acquisition.rs").as_slice(),
        ),
        (
            "src/acquisition_http.rs",
            include_bytes!("acquisition_http.rs").as_slice(),
        ),
        ("src/durable.rs", include_bytes!("durable.rs").as_slice()),
        (
            "src/durable/acquisition.rs",
            include_bytes!("durable/acquisition.rs").as_slice(),
        ),
        (
            "src/dataset_location.rs",
            include_bytes!("dataset_location.rs").as_slice(),
        ),
        ("src/lib.rs", include_bytes!("lib.rs").as_slice()),
        ("Cargo.toml", include_bytes!("../Cargo.toml").as_slice()),
        ("Cargo.lock", include_bytes!("../Cargo.lock").as_slice()),
    ]
    .into_iter()
    .map(|(path, bytes)| SourceFile {
        path,
        bytes: bytes.len(),
        sha256: sha256(bytes),
    })
    .collect()
}

fn invalid(message: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}

fn context_identity(run: &RecordedRun) -> io::Result<String> {
    let receipts: Vec<_> = run.source.publications.iter().map(|p| &p.receipt).collect();
    Ok(sha256(
        &serde_json::to_vec(&(
            &run.source.manifest_sha256,
            &run.source.payload_manifest_sha256,
            &run.source.prepared_payload,
            receipts,
        ))
        .map_err(invalid)?,
    ))
}

fn provenance(run: &RecordedRun, object: &Published) -> io::Result<RawProvenance> {
    let receipt = &object.receipt;
    let sequence = receipt.request.sequence;
    let receipt_path = format!("published/{sequence:010}/receipt.json");
    let hash = run
        .snapshot
        .artifacts
        .iter()
        .find(|a| a.path == receipt_path)
        .ok_or_else(|| invalid("audited receipt artifact missing"))?
        .sha256
        .clone();
    Ok(RawProvenance {
        sequence: sequence.to_string(),
        receipt_path,
        receipt_sha256: hash,
        raw_path: format!("published/{sequence:010}/raw.bin"),
        raw_sha256: receipt.sha256.clone(),
        response_entity_bytes: receipt.response_entity_bytes.to_string(),
        source_host: receipt.source_host.clone(),
        source_path: receipt.source_path.clone(),
        acquired_at_wall_ms: receipt.acquired_at.wall_ms.to_string(),
        timestamp_role: "OPERATIONAL_PROVENANCE_ONLY_NOT_HISTORICAL_FEATURE",
        source_evidence: receipt.evidence.clone(),
    })
}

fn inspect_selected_slot(run: &RecordedRun, slot: u64, limits: Limits) -> io::Result<SlotReport> {
    let prepared = run
        .source
        .prepared_payload
        .as_ref()
        .ok_or_else(|| invalid("missing payload plan"))?;
    let requests: Vec<_> = prepared
        .requests()
        .iter()
        .filter(|r| matches!(r.kind, RequestKind::CarRange { slot: s, .. } if s == slot))
        .collect();
    let mut report = SlotReport {
        slot: slot.to_string(),
        state: "GAP_NOT_PUBLISHED",
        ranges: Vec::new(),
        assembled_sha256: None,
        inspection: None,
        quarantine_reason: None,
    };
    if requests.is_empty() {
        report.state = "INDEX_REPORTED_ABSENT_NOT_CHAIN_SKIP_PROOF";
        return Ok(report);
    }
    let total = requests.iter().try_fold(0_u64, |sum, r| {
        sum.checked_add(r.allowance())
            .ok_or_else(|| invalid("range length overflow"))
    })?;
    if total > limits.max_slot_bytes as u64 {
        return Err(invalid("B5_PREPARATION_SLOT_BYTE_LIMIT"));
    }
    if requests.iter().any(|r| {
        !run.source
            .publications
            .iter()
            .any(|p| p.receipt.request == **r)
    }) {
        return Ok(report);
    }
    let mut assembled = Vec::with_capacity(usize::try_from(total).map_err(invalid)?);
    let mut next = None;
    for request in requests {
        let RequestKind::CarRange {
            start,
            end_exclusive,
            ..
        } = request.kind
        else {
            return Err(invalid("non-CAR range"));
        };
        if next.is_some_and(|offset| offset != start) {
            return Err(invalid("noncontiguous receipt-bound ranges"));
        }
        next = Some(end_exclusive);
        let object = run
            .source
            .publications
            .iter()
            .find(|p| p.receipt.request == *request)
            .ok_or_else(|| invalid("missing publication"))?;
        let bytes = read_limited(&object.raw_path, request.allowance()).map_err(invalid)?;
        if bytes.len() as u64 != request.allowance() || sha256(&bytes) != object.receipt.sha256 {
            return Err(invalid("Raw changed after receipt audit"));
        }
        report.ranges.push(RawRange {
            sequence: request.sequence.to_string(),
            assembled_offset: assembled.len().to_string(),
            length: bytes.len().to_string(),
            car_offset: start.to_string(),
            raw_sha256: object.receipt.sha256.clone(),
        });
        assembled.extend_from_slice(&bytes);
    }
    report.assembled_sha256 = Some(sha256(&assembled));
    match inspect_slot_sections(
        slot,
        &assembled,
        VerificationLimits {
            max_total_bytes: limits.max_slot_bytes,
            max_section_bytes: limits.max_slot_bytes,
            max_nodes: limits.max_nodes,
            max_links: limits.max_links,
        },
    ) {
        Ok(inspection) => {
            report.state = "ARCHIVAL_ENVELOPES_ONLY";
            report.inspection = Some(inspection);
        }
        Err(error) => {
            report.state = "QUARANTINED";
            report.quarantine_reason = Some(error.to_string());
        }
    }
    Ok(report)
}

/// Verify immutable receipt-bound Raw and inspect at most the configured selected slots.
/// This takes no writer lock, changes no run file and never grants or resumes authority.
/// A concurrent source change fails; the caller may retry an independent read later.
/// # Errors
/// Corrupt/substituted source identities, hashes, plans or resource bounds fail closed.
#[allow(clippy::too_many_lines)]
pub fn inspect_raw(root: &Path, limits: Limits) -> io::Result<QualityReport> {
    if limits.max_slots == 0
        || limits.max_slot_bytes == 0
        || limits.max_nodes == 0
        || limits.max_links == 0
    {
        return Err(invalid("B5_PREPARATION_LIMITS_INVALID"));
    }
    let run = read_run_context(root)?;
    let before = context_identity(&run)?;
    let source = &run.source;
    let mut selected_slots = Vec::new();
    if let Some(prepared) = &source.prepared_payload {
        if prepared
            .end_slot()
            .checked_sub(prepared.start_slot())
            .ok_or_else(|| invalid("inverted slot selection"))?
            > limits.max_slots
        {
            return Err(invalid("B5_PREPARATION_SLOT_COUNT_LIMIT"));
        }
        let metadata: Vec<_> = source
            .publications
            .iter()
            .filter(|p| p.receipt.request.sequence < 4)
            .cloned()
            .collect();
        let rederived = derive_payload_from_metadata(
            &source.aggregate_plan,
            &metadata,
            prepared.start_slot(),
            prepared.end_slot(),
        )
        .map_err(invalid)?;
        if rederived != *prepared {
            return Err(invalid("prepared payload differs from captured metadata"));
        }
        for slot in prepared.start_slot()..prepared.end_slot() {
            selected_slots.push(inspect_selected_slot(&run, slot, limits)?);
        }
    }
    let reader_source_files = reader_sources();
    let report = QualityReport {
        schema: "OF1_BRONZE_PREPARATION_1",
        component: "READ_ONLY_ARCHIVAL_ENVELOPE_PREPARATION",
        reader_source_sha256: sha256(&serde_json::to_vec(&reader_source_files).map_err(invalid)?),
        reader_source_files,
        reader_identity_scope: "LISTED_READER_SOURCE_SET_NOT_EXECUTABLE_OR_BUILD_ATTESTATION",
        delivery_evidence: "Fixture",
        source_evidence: if run.snapshot.kind == "LOCAL_SIMULATION" {
            "Fixture"
        } else {
            "UNREVIEWED_AUTHENTIC_RAW"
        },
        input_kind: if source.prepared_payload.is_some() {
            "RECEIPT_BOUND_PAYLOAD_SELECTION"
        } else {
            "METADATA_ONLY"
        },
        run_id: run.snapshot.id.clone(),
        run_manifest_sha256: source.manifest_sha256.clone(),
        payload_manifest_sha256: source.payload_manifest_sha256.clone(),
        source_code_sha: source.aggregate_plan.code_sha.clone(),
        source_toolchain_fingerprint: source.aggregate_plan.toolchain_fingerprint.clone(),
        source_executable_sha256: source.aggregate_plan.executable_sha256.clone(),
        epoch: source.aggregate_plan.epoch.to_string(),
        limits,
        publications: source
            .publications
            .iter()
            .map(|p| provenance(&run, p))
            .collect::<io::Result<_>>()?,
        missing_metadata_sequences: (0..4_u64)
            .filter(|sequence| {
                !source
                    .publications
                    .iter()
                    .any(|p| p.receipt.request.sequence == *sequence)
            })
            .map(|s| s.to_string())
            .collect(),
        index_reported_absent_slots: source
            .prepared_payload
            .as_ref()
            .map(|p| {
                p.index_reported_absent()
                    .iter()
                    .map(u64::to_string)
                    .collect()
            })
            .unwrap_or_default(),
        selected_slots,
        solana_transaction_decode: "UNAVAILABLE_NOT_IMPLEMENTED",
        pump_decode: "UNAVAILABLE_NOT_IMPLEMENTED",
        decoded_transaction_count: None,
        decoded_pump_event_count: None,
        dataframe_payload_and_checksum: "NOT_EVALUATED",
        historical_activation: "UNKNOWN",
        root_to_slot_membership: "UNAVAILABLE",
        research_ready: false,
        limitations: vec![
            "Archival transaction envelopes are opaque source containers, not decoded Solana transactions or Pump events.",
            "Data and metadata remain one atomic package; link ordering is not a proven Solana execution position.",
            "No dataframe decompression/checksum verification, token identity, coin lifecycle, Bronze/Silver dataset or Parquet writer is implemented.",
            "Acquisition timestamps are operational provenance, never historical availability, features or labels.",
            "A selected slot or valid CID is not epoch-root membership, historical Pump activation or executable economics.",
            "Fixture inputs remain Fixture; authentic metadata alone is not an authentic transaction dataset.",
        ],
    };
    if context_identity(&read_run_context(root)?)? != before {
        return Err(invalid("Raw source changed during read-only report"));
    }
    Ok(report)
}

fn escape_html(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

/// Deterministic, self-contained quality view: no JavaScript, remote assets or invented counts.
/// # Errors
/// Serialization or formatting errors prevent output.
pub fn render_html(report: &QualityReport) -> io::Result<String> {
    let mut html = String::from(
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>B5 Raw quality · archival preparation</title><style>body{font:16px system-ui;background:#0c1524;color:#e2ebf4;max-width:1100px;margin:40px auto;padding:0 24px}h1{font-size:30px}h2{margin-top:32px}.badge{display:inline-block;padding:8px 14px;background:#283e59;border-radius:8px;color:#aaddff}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #32455a}code,pre{font:12px ui-monospace,monospace;overflow-wrap:anywhere}pre{white-space:pre-wrap;background:#121f31;padding:20px}p,li{line-height:1.6}summary{cursor:pointer}strong{color:#ffd590}</style><h1>Raw quality · B5 offline preparation</h1>",
    );
    write!(html, "<p class=\"badge\">{} · delivery evidence {}</p><p>Run <code>{}</code></p><p><strong>Not a transaction dataset. No Research Ready claim.</strong> Solana transactions and Pump events: unavailable, not zero.</p><h2>Verified Raw publications</h2><table><tr><th>Operation</th><th>Bytes</th><th>Source evidence</th><th>SHA-256</th></tr>", escape_html(report.input_kind), report.delivery_evidence, escape_html(&report.run_id)).map_err(invalid)?;
    for publication in &report.publications {
        write!(
            html,
            "<tr><td>{}</td><td>{}</td><td>{}</td><td><code>{}</code></td></tr>",
            publication.sequence,
            publication.response_entity_bytes,
            escape_html(&publication.source_evidence),
            escape_html(&publication.raw_sha256)
        )
        .map_err(invalid)?;
    }
    html.push_str("</table><h2>Selected archival envelopes</h2>");
    if report.selected_slots.is_empty() {
        html.push_str("<p>METADATA_ONLY: no CAR payload selected or decoded. No token or transaction count is available.</p>");
    }
    for slot in &report.selected_slots {
        write!(
            html,
            "<p>Slot {} — <strong>{}</strong></p>",
            slot.slot, slot.state
        )
        .map_err(invalid)?;
        if let Some(inspection) = &slot.inspection {
            write!(html, "<p>{} verified CAR nodes; {} opaque atomic archival transaction envelope(s). These are not decoded Solana transactions.</p>", inspection.archival_nodes.len(), inspection.transaction_envelopes.len()).map_err(invalid)?;
        }
        if let Some(reason) = &slot.quarantine_reason {
            write!(html, "<p>Quarantine: {}</p>", escape_html(reason)).map_err(invalid)?;
        }
    }
    html.push_str("<h2>Explicit limits</h2><ul>");
    for limit in &report.limitations {
        write!(html, "<li>{}</li>", escape_html(limit)).map_err(invalid)?;
    }
    writeln!(html, "</ul><details><summary>Complete deterministic provenance and quality JSON</summary><pre>{}</pre></details></html>", escape_html(&serde_json::to_string_pretty(report).map_err(invalid)?)).map_err(invalid)?;
    Ok(html)
}
