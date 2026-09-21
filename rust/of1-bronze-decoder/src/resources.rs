//! Exact serde byte accounting, not an alternate decoder or admission bypass.
use crate::invalid;
use serde::Serialize;
use serde_json::{Value, json};
use std::{collections::BTreeMap, io};

pub const MAX_INDIVIDUAL_RECORD_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_JSONL_BYTES: usize = 64 * 1024 * 1024;
pub const MAX_QUALITY_BYTES: usize = 64 * 1024 * 1024;
pub const MAX_HTML_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_EXECUTION_BYTES: usize = 1024 * 1024;
pub const MAX_PUBLICATION_BYTES: usize = 256 * 1024 * 1024;

/// Same serde record bytes, including room for its preserved JSONL newline.
/// # Errors
/// An individually oversized record is not split, truncated or dropped.
pub fn record_bytes(record: &Value) -> io::Result<usize> {
    let bytes = serialized_bytes(record, false)?;
    check_size(
        "INDIVIDUAL_JSONL_RECORD",
        bytes.saturating_add(1),
        MAX_INDIVIDUAL_RECORD_BYTES,
    )?;
    Ok(bytes)
}

/// Explicit byte admission shared by serialization and publication checks.
/// # Errors
/// Exceeding a named bound is a resource error, never a domain disposition.
pub fn check_size(name: &str, bytes: usize, limit: usize) -> io::Result<()> {
    if bytes > limit {
        return Err(invalid(format!(
            "B5_RESOURCE_LIMIT {name} bytes={bytes} limit={limit}"
        )));
    }
    Ok(())
}

#[derive(Default)]
struct Count(usize);
impl io::Write for Count {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0 = self
            .0
            .checked_add(bytes.len())
            .ok_or_else(|| invalid("SIZE_OVERFLOW"))?;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Count the exact canonical serde encoding without allocating an encoded copy.
/// # Errors
/// Serialization errors or arithmetic overflow fail closed.
pub fn serialized_bytes(value: &Value, pretty: bool) -> io::Result<usize> {
    let mut count = Count::default();
    if pretty {
        serde_json::to_writer_pretty(&mut count, value)
    } else {
        serde_json::to_writer(&mut count, value)
    }
    .map_err(invalid)?;
    Ok(count.0)
}

/// Bound before allocating the encoded copy. Compact JSON preserves the exact
/// serde value; whitespace in previous immutable reports is never rewritten.
/// # Errors
/// Invalid serialization or a named byte limit fails before publication.
pub fn bounded_json(value: &Value, name: &str, limit: usize) -> io::Result<Vec<u8>> {
    let bytes = serialized_bytes(value, false)?;
    check_size(name, bytes, limit)?;
    let mut output = Vec::with_capacity(bytes);
    serde_json::to_writer(&mut output, value).map_err(invalid)?;
    Ok(output)
}

/// Preserve ordered records, including duplicates and all explicit unknowns.
/// # Errors
/// A single oversized record or aggregate layer error never drops a record.
pub fn bounded_jsonl(records: &[Value], limit: usize) -> io::Result<Vec<u8>> {
    let mut bytes = 0usize;
    for record in records {
        bytes = bytes
            .checked_add(record_bytes(record)?)
            .and_then(|n| n.checked_add(1))
            .ok_or_else(|| invalid("SIZE_OVERFLOW"))?;
        check_size("JSONL_LAYER", bytes, limit)?;
    }
    let mut output = Vec::with_capacity(bytes);
    for record in records {
        serde_json::to_writer(&mut output, record).map_err(invalid)?;
        output.push(b'\n');
    }
    Ok(output)
}

/// Admit the entire publication, including its completion marker, before mkdir.
/// # Errors
/// Arithmetic overflow and cumulative output excess fail closed.
pub fn publication_bytes(lengths: &[usize], limit: usize) -> io::Result<usize> {
    let bytes = lengths.iter().try_fold(64usize, |sum, n| {
        sum.checked_add(*n).ok_or_else(|| invalid("SIZE_OVERFLOW"))
    })?;
    check_size("PUBLICATION", bytes, limit)?;
    Ok(bytes)
}

#[derive(Default, Serialize)]
struct Layer {
    records: usize,
    record_bytes: usize,
    jsonl_bytes: usize,
    largest_record_bytes: usize,
}
impl Layer {
    fn add(&mut self, bytes: usize) -> io::Result<()> {
        self.records = self
            .records
            .checked_add(1)
            .ok_or_else(|| invalid("SIZE_OVERFLOW"))?;
        self.record_bytes = self
            .record_bytes
            .checked_add(bytes)
            .ok_or_else(|| invalid("SIZE_OVERFLOW"))?;
        self.jsonl_bytes = self
            .record_bytes
            .checked_add(self.records)
            .ok_or_else(|| invalid("SIZE_OVERFLOW"))?;
        self.largest_record_bytes = self.largest_record_bytes.max(bytes);
        Ok(())
    }
}

/// Inspect all successful/failed/unsupported records from the SAME complete decode.
/// Does not publish a dataset or change sample/evidence semantics.
/// # Errors
/// Invalid report shapes, serialization errors or arithmetic overflow fail.
pub fn measure(report: &Value) -> io::Result<Value> {
    let mut slots = BTreeMap::<String, BTreeMap<&str, Layer>>::new();
    let mut total = BTreeMap::new();
    for (layer, field) in [("bronze", "records"), ("silver", "silver_records")] {
        let mut all = Layer::default();
        for record in report[field]
            .as_array()
            .ok_or_else(|| invalid("MEASUREMENT_RECORDS"))?
        {
            let bytes = serialized_bytes(record, false)?;
            let slot = record["effective_at"]["slot"]
                .as_str()
                .ok_or_else(|| invalid("MEASUREMENT_SLOT"))?;
            slots
                .entry(slot.to_owned())
                .or_default()
                .entry(layer)
                .or_default()
                .add(bytes)?;
            all.add(bytes)?;
        }
        total.insert(layer, all);
    }
    for layers in slots.values_mut() {
        for layer in ["bronze", "silver"] {
            layers.entry(layer).or_default();
        }
    }
    let metadata: BTreeMap<_, _> = report["slots"].as_array().map_or_else(
        || {
            BTreeMap::from([(
                report["slot"].as_str().unwrap_or("INVALID").to_owned(),
                report["resource_accounting"]["decoded_metadata_bytes"].clone(),
            )])
        },
        |s| {
            s.iter()
                .map(|v| {
                    (
                        v["slot"].as_str().unwrap_or("INVALID").to_owned(),
                        v["resource_accounting"]["decoded_metadata_bytes"].clone(),
                    )
                })
                .collect()
        },
    );
    Ok(
        json!({"schema":"OF1_BRONZE_RESOURCE_MEASUREMENT_1", "sample_identity":report["sample_identity"],
        "slots":slots,"totals":total,"quality_compact_bytes":serialized_bytes(report,false)?,
        "quality_pretty_bytes":serialized_bytes(report,true)?,"quality_html_bytes":crate::report::html(report).len(),
        "decoded_metadata_bytes_per_slot":metadata,"resource_accounting":report["resource_accounting"],"research_ready":false,"measurement_only":true}),
    )
}
