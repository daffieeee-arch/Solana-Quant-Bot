//! New-directory-only publication and independently read-back Parquet verification.
use crate::{
    columns::{self, Layer},
    hash, invalid,
};
use arrow_array::{Array, BinaryArray, RecordBatch};
use parquet::{
    arrow::{ArrowWriter, arrow_reader::ParquetRecordBatchReaderBuilder},
    basic::Compression,
    file::properties::{WriterProperties, WriterVersion},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::Path,
};

pub const MAX_FILE_BYTES: u64 = 64 * 1024 * 1024;
pub const MAX_RECORD_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_ROWS: usize = 5000;
pub const BATCH_ROWS: usize = 512;

/// Read a regular file with a hard byte bound (also when it grows during the read).
/// # Errors
/// Rejects nonregular files, I/O failures and oversized inputs.
pub fn bounded_read(path: &Path) -> io::Result<Vec<u8>> {
    if !fs::symlink_metadata(path)?.file_type().is_file() {
        return Err(invalid("REGULAR_FILE_REQUIRED"));
    }
    let file = File::open(path)?;
    if file.metadata()?.len() > MAX_FILE_BYTES {
        return Err(invalid("FILE_LIMIT"));
    }
    let mut bytes = Vec::new();
    file.take(MAX_FILE_BYTES + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_FILE_BYTES {
        return Err(invalid("FILE_LIMIT"));
    }
    Ok(bytes)
}
fn lines(bytes: &[u8]) -> io::Result<Vec<Vec<u8>>> {
    let lines: Vec<_> = bytes
        .split_inclusive(|b| *b == b'\n')
        .map(<[u8]>::to_vec)
        .collect();
    if lines.len() > MAX_ROWS || lines.iter().any(|l| l.len() > MAX_RECORD_BYTES) {
        return Err(invalid("RECORD_LIMIT"));
    }
    Ok(lines)
}
/// Fixed settings: no clock, dictionary, compression or random writer metadata.
#[must_use]
pub fn settings() -> Value {
    json!({"arrow_parquet_version":"59.3.0","compression":"UNCOMPRESSED","dictionary":false,"row_group_rows":BATCH_ROWS,"batch_rows":BATCH_ROWS,"page_bytes":1_048_576,"parquet_version":"1.0","created_by":"of1-parquet-projection/0.1.0"})
}
/// Emit a real Parquet file without overwriting a preexisting path.
/// # Errors
/// Rejects unsupported input records, I/O failures and Arrow/Parquet errors.
pub fn write_layer(layer: Layer, input: &[u8], path: &Path) -> io::Result<()> {
    let lines = lines(input)?;
    let file = OpenOptions::new().write(true).create_new(true).open(path)?;
    let properties = WriterProperties::builder()
        .set_writer_version(WriterVersion::PARQUET_1_0)
        .set_created_by("of1-parquet-projection/0.1.0".into())
        .set_compression(Compression::UNCOMPRESSED)
        .set_dictionary_enabled(false)
        .set_max_row_group_row_count(Some(BATCH_ROWS))
        .set_write_batch_size(BATCH_ROWS)
        .set_data_page_size_limit(1_048_576)
        .set_data_page_row_count_limit(BATCH_ROWS)
        .build();
    let mut writer =
        ArrowWriter::try_new(&file, columns::schema(layer), Some(properties)).map_err(invalid)?;
    for (index, chunk) in lines.chunks(BATCH_ROWS).enumerate() {
        writer
            .write(&columns::batch(layer, chunk, (index * BATCH_ROWS) as u64)?)
            .map_err(invalid)?;
    }
    writer.close().map_err(invalid)?;
    file.sync_all()
}
/// Verify every value/state column against the independently retained record bytes.
/// # Errors
/// Rejects schema, ordering, byte or typed projection drift.
pub fn verify_batch(layer: Layer, batch: &RecordBatch, ordinal: u64) -> io::Result<Vec<Vec<u8>>> {
    if batch.schema() != columns::schema(layer) {
        return Err(invalid("SCHEMA_DRIFT"));
    }
    let raw = batch
        .column_by_name("record_bytes")
        .and_then(|a| a.as_any().downcast_ref::<BinaryArray>())
        .ok_or_else(|| invalid("RECORD_BYTES_REQUIRED"))?;
    if raw.null_count() != 0 {
        return Err(invalid("NULL_RECORD_BYTES"));
    }
    let lines: Vec<_> = (0..raw.len()).map(|i| raw.value(i).to_vec()).collect();
    let expected = columns::batch(layer, &lines, ordinal)?;
    for (field, (actual, wanted)) in batch
        .schema()
        .fields()
        .iter()
        .zip(batch.columns().iter().zip(expected.columns()))
    {
        if actual.to_data() != wanted.to_data() {
            return Err(invalid(format!("COLUMN_PARITY_FAILED {}", field.name())));
        }
    }
    Ok(lines)
}
/// Reopen Parquet and prove exact JSONL round-trip plus typed-column parity.
/// # Errors
/// Fails on corruption, remaining field drift, changed bytes/order or a resource limit.
pub fn verify_layer(layer: Layer, path: &Path, expected_input_sha256: &str) -> io::Result<Value> {
    let builder = ParquetRecordBatchReaderBuilder::try_new(File::open(path)?).map_err(invalid)?;
    if builder.schema() != &columns::schema(layer) {
        return Err(invalid("SCHEMA_DRIFT"));
    }
    let reader = builder
        .with_batch_size(BATCH_ROWS)
        .build()
        .map_err(invalid)?;
    let mut original = Sha256::new();
    let mut logical = Sha256::new();
    let mut count = 0u64;
    let mut size = 0u64;
    let mut coverage = BTreeMap::<String, BTreeMap<String, u64>>::new();
    for batch in reader {
        for line in verify_batch(layer, &batch.map_err(invalid)?, count)? {
            count += 1;
            size += line.len() as u64;
            if count > MAX_ROWS as u64 || size > MAX_FILE_BYTES {
                return Err(invalid("ROUNDTRIP_LIMIT"));
            }
            original.update(&line);
            let record = &line[..line.len() - 1];
            logical.update((record.len() as u64).to_le_bytes());
            logical.update(record);
            let v = columns::parse_record(layer, &line)?;
            let slot = v["effective_at"]["slot"]
                .to_string()
                .trim_matches('"')
                .to_owned();
            let outcome = if layer == Layer::Bronze {
                v["disposition"].as_str()
            } else {
                v["transaction_status"].as_str()
            }
            .unwrap_or("UNAVAILABLE");
            *coverage
                .entry(slot)
                .or_default()
                .entry(outcome.into())
                .or_default() += 1;
        }
    }
    let reconstructed = hex::encode(original.finalize());
    if reconstructed != expected_input_sha256 {
        return Err(invalid("JSONL_ROUNDTRIP_HASH_MISMATCH"));
    }
    Ok(
        json!({"rows":count,"reconstructed_jsonl_bytes":size,"reconstructed_jsonl_sha256":reconstructed,"ordered_logical_sha256":hex::encode(logical.finalize()),"typed_column_parity":"EXACT","coverage":coverage}),
    )
}

fn input_seal(input: &Path, execution_sha: &str) -> io::Result<Value> {
    let execution_bytes = bounded_read(&input.join("execution.json"))?;
    if hash(&execution_bytes) != execution_sha {
        return Err(invalid("EXECUTION_RECEIPT_HASH_MISMATCH"));
    }
    let execution: Value = serde_json::from_slice(&execution_bytes).map_err(invalid)?;
    if execution["schema"] != "OF1_BRONZE_EXECUTION_1" {
        return Err(invalid("EXECUTION_SCHEMA"));
    }
    let mut files = BTreeMap::new();
    for (name, key) in [
        ("bronze.jsonl", "bronze_jsonl_sha256"),
        ("silver.jsonl", "silver_jsonl_sha256"),
        ("quality.json", "quality_sha256"),
        ("quality.html", "html_sha256"),
    ] {
        let bytes = bounded_read(&input.join(name))?;
        let sha = hash(&bytes);
        if execution[key] != sha {
            return Err(invalid(format!("INPUT_HASH_MISMATCH {name}")));
        }
        files.insert(name, json!({"sha256":sha,"bytes":bytes.len()}));
    }
    let complete = bounded_read(&input.join("COMPLETE"))?;
    if String::from_utf8_lossy(&complete).trim()
        != execution["quality_sha256"].as_str().unwrap_or("")
    {
        return Err(invalid("INPUT_INCOMPLETE"));
    }
    Ok(
        json!({"execution_sha256":execution_sha,"execution":execution,"complete_sha256":hash(&complete),"files":files}),
    )
}
fn verify_silver_binding(bronze: &[u8], silver: &[u8]) -> io::Result<()> {
    let mut parents = BTreeMap::<String, (Value, Value)>::new();
    for line in lines(bronze)? {
        let v = columns::parse_record(Layer::Bronze, &line)?;
        // Identical rows are retained physically, not deduplicated by this lookup.
        parents.insert(
            hash(&line[..line.len() - 1]),
            (v["effective_at"].clone(), v["source"].clone()),
        );
    }
    for line in lines(silver)? {
        let v = columns::parse_record(Layer::Silver, &line)?;
        let key = v["bronze_record_sha256"]
            .as_str()
            .ok_or_else(|| invalid("SILVER_PARENT_REQUIRED"))?;
        if parents.get(key) != Some(&(v["effective_at"].clone(), v["source"].clone())) {
            return Err(invalid("SILVER_PARENT_BINDING_MISMATCH"));
        }
    }
    Ok(())
}
fn publish(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}
/// Materialize one sealed, bounded existing decoder output into a new directory.
/// An interrupted directory without COMPLETE is not a dataset; no overwrite/resume.
/// # Errors
/// Fails closed on source/parent/projection drift, resource limits or publication errors.
pub fn materialize(input: &Path, execution_sha: &str, output: &Path) -> io::Result<Value> {
    let input = input.canonicalize()?;
    let parent = output
        .parent()
        .ok_or_else(|| invalid("OUTPUT_PARENT"))?
        .canonicalize()?;
    if parent.starts_with(&input) {
        return Err(invalid("OUTPUT_INSIDE_INPUT"));
    }
    let seal = input_seal(&input, execution_sha)?;
    let bronze = bounded_read(&input.join("bronze.jsonl"))?;
    let silver = bounded_read(&input.join("silver.jsonl"))?;
    verify_silver_binding(&bronze, &silver)?;
    fs::create_dir(output)?;
    File::open(&parent)?.sync_all()?;
    let mut files = BTreeMap::new();
    for (layer, bytes) in [(Layer::Bronze, bronze), (Layer::Silver, silver)] {
        let name = format!("{}.parquet", layer.name());
        let path = output.join(&name);
        write_layer(layer, &bytes, &path)?;
        let audit = verify_layer(layer, &path, &hash(&bytes))?;
        if hash(&bytes) != seal["files"][format!("{}.jsonl", layer.name())]["sha256"] {
            return Err(invalid("INPUT_CHANGED_DURING_READ"));
        }
        files.insert(name,json!({"sha256":hash(&bounded_read(&path)?),"bytes":path.metadata()?.len(),"audit":audit,"schema":columns::schema_descriptor(layer),"schema_sha256":hash(&serde_json::to_vec(&columns::schema_descriptor(layer)).map_err(invalid)?)}));
    }
    if input_seal(&input, execution_sha)? != seal {
        return Err(invalid("INPUT_CHANGED_DURING_PUBLICATION"));
    }
    let manifest = json!({"schema":"OF1_PARQUET_DATASET_1","writer":{"version":env!("CARGO_PKG_VERSION"),"source_sha256":crate::source_sha256(),"executable_sha256":hash(&fs::read(std::env::current_exe()?)?),"cargo_lock_sha256":hash(include_bytes!("../Cargo.lock")),"settings":settings()},"input":seal,"files":files,"evidence":{"slice_class":"ENGINEERING_VALIDATION_ONLY","new_domain_decoding":false,"research_ready":false,"root_to_slot_membership":"UNAVAILABLE","unknowns_preserved":true,"historical_activation":"UNPROVEN","physical_writer":"RUST_ARROW_PARQUET_BOUNDED_PROJECTION_ONLY"}});
    let bytes = serde_json::to_vec_pretty(&manifest).map_err(invalid)?;
    publish(&output.join("manifest.json"), &bytes)?;
    File::open(output)?.sync_all()?;
    publish(
        &output.join("COMPLETE"),
        format!("{}\n", hash(&bytes)).as_bytes(),
    )?;
    File::open(output)?.sync_all()?;
    Ok(manifest)
}
