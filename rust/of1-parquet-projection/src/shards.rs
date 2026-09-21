//! Bounded streaming projection. File split changes only physical partitioning.
use crate::{
    admission,
    columns::{self, Layer},
    hash, invalid, storage,
};
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
    io::{self, BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::Path,
};

pub const MAX_SHARDS: usize = 64;
pub const MAX_BATCH_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_PARENT_BINDINGS: usize = 16_384;
/// Cumulative output writes for one projection, including discarded split probes.
/// This is distinct from input/record/shard limits and filesystem allocation overhead.
pub const MAX_DATASET_WRITE_BYTES: u64 = 256 * 1024 * 1024;

struct WriteBudget {
    limit: u64,
    written: u64,
}
impl WriteBudget {
    fn new(limit: u64) -> io::Result<Self> {
        if limit == 0 || limit > MAX_DATASET_WRITE_BYTES {
            return Err(invalid("DATASET_WRITE_BUDGET_LIMIT"));
        }
        Ok(Self { limit, written: 0 })
    }
    fn check(&self, bytes: u64) -> io::Result<()> {
        if self
            .written
            .checked_add(bytes)
            .is_none_or(|n| n > self.limit)
        {
            return Err(invalid("DATASET_CUMULATIVE_WRITE_LIMIT"));
        }
        Ok(())
    }
    fn charge(&mut self, bytes: u64) -> io::Result<()> {
        self.check(bytes)?;
        self.written += bytes;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug)]
pub struct ShardLimits {
    pub rows: usize,
    pub bytes: u64,
}
impl Default for ShardLimits {
    fn default() -> Self {
        Self {
            rows: storage::MAX_ROWS,
            bytes: storage::MAX_FILE_BYTES,
        }
    }
}
impl ShardLimits {
    /// Only tighter test/physical partition settings are allowed.
    /// # Errors
    /// Rejects zero or raised hard caps.
    pub fn validate(self) -> io::Result<()> {
        if self.rows == 0
            || self.rows > storage::MAX_ROWS
            || self.bytes == 0
            || self.bytes > storage::MAX_FILE_BYTES
        {
            return Err(invalid("SHARD_LIMITS"));
        }
        Ok(())
    }
}

/// One bounded LF-preserved record; `read_until` without a cap is deliberately avoided.
/// # Errors
/// Refuses overlong records before allocating more than the record limit.
pub fn read_line(reader: &mut impl BufRead) -> io::Result<Option<Vec<u8>>> {
    let mut row = Vec::new();
    loop {
        let buf = reader.fill_buf()?;
        if buf.is_empty() {
            return if row.is_empty() {
                Ok(None)
            } else {
                Err(invalid("JSONL_LF_REQUIRED"))
            };
        }
        let take = buf
            .iter()
            .position(|b| *b == b'\n')
            .map_or(buf.len(), |n| n + 1);
        if row.len() + take > storage::MAX_RECORD_BYTES {
            return Err(invalid("RECORD_LIMIT"));
        }
        let done = buf[take - 1] == b'\n';
        row.extend_from_slice(&buf[..take]);
        reader.consume(take);
        if done {
            return Ok(Some(row));
        }
    }
}
/// File hashes are streamed, not collected in memory.
/// # Errors
/// Rejects nonregular files or a growing/oversized input.
pub fn file_hash(path: &Path, limit: u64) -> io::Result<(String, u64)> {
    if !fs::symlink_metadata(path)?.is_file() {
        return Err(invalid("REGULAR_FILE_REQUIRED"));
    }
    let mut file = File::open(path)?;
    if file.metadata()?.len() > limit {
        return Err(invalid("FILE_LIMIT"));
    }
    let mut h = Sha256::new();
    let mut count = 0;
    let mut buffer = vec![0u8; 65536];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        count += n as u64;
        if count > limit {
            return Err(invalid("FILE_LIMIT"));
        }
        h.update(&buffer[..n]);
    }
    Ok((hex::encode(h.finalize()), count))
}
struct LimitedFile<'a> {
    file: File,
    count: u64,
    limit: u64,
    budget: &'a mut WriteBudget,
}
impl Write for LimitedFile<'_> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if self.count.saturating_add(bytes.len() as u64) > self.limit {
            return Err(invalid("SHARD_PHYSICAL_FILE_LIMIT"));
        }
        self.budget.check(bytes.len() as u64)?;
        let n = self.file.write(bytes)?;
        self.budget.charge(n as u64)?;
        self.count += n as u64;
        Ok(n)
    }
    fn flush(&mut self) -> io::Result<()> {
        self.file.flush()
    }
}
fn encode(
    layer: Layer,
    input: &Path,
    offsets: &[u64],
    ordinal: u64,
    path: &Path,
    limit: u64,
    budget: &mut WriteBudget,
) -> io::Result<()> {
    let file = OpenOptions::new().write(true).create_new(true).open(path)?;
    let sink = LimitedFile {
        file,
        count: 0,
        limit,
        budget,
    };
    let props = WriterProperties::builder()
        .set_writer_version(WriterVersion::PARQUET_1_0)
        .set_created_by("of1-parquet-projection/0.2.0-shards".into())
        .set_compression(Compression::UNCOMPRESSED)
        .set_dictionary_enabled(false)
        .set_max_row_group_row_count(Some(storage::BATCH_ROWS))
        .set_write_batch_size(storage::BATCH_ROWS)
        .set_data_page_size_limit(1_048_576)
        .set_data_page_row_count_limit(storage::BATCH_ROWS)
        .build();
    let mut writer =
        ArrowWriter::try_new(sink, columns::schema(layer), Some(props)).map_err(invalid)?;
    let mut source = BufReader::new(File::open(input)?);
    source.seek(SeekFrom::Start(offsets[0]))?;
    let mut batch = Vec::new();
    let mut bytes = 0;
    let mut at = ordinal;
    for _ in 1..offsets.len() {
        let row = read_line(&mut source)?.ok_or_else(|| invalid("INPUT_TRUNCATED"))?;
        if !batch.is_empty()
            && (batch.len() == storage::BATCH_ROWS || bytes + row.len() > MAX_BATCH_BYTES)
        {
            writer
                .write(&columns::batch(layer, &batch, at)?)
                .map_err(invalid)?;
            writer.flush().map_err(invalid)?;
            at += batch.len() as u64;
            batch.clear();
            bytes = 0;
        }
        bytes += row.len();
        batch.push(row);
    }
    if !batch.is_empty() {
        writer
            .write(&columns::batch(layer, &batch, at)?)
            .map_err(invalid)?;
        writer.flush().map_err(invalid)?;
    }
    if source.stream_position()? != *offsets.last().ok_or_else(|| invalid("SHARD_OFFSETS"))? {
        return Err(invalid("INPUT_CHANGED_DURING_READ"));
    }
    writer.close().map_err(invalid)?;
    File::open(path)?.sync_all()
}
#[derive(Default)]
struct Logical {
    original: Sha256,
    ordered: Sha256,
    rows: u64,
    bytes: u64,
    coverage: BTreeMap<String, BTreeMap<String, u64>>,
}
impl Logical {
    fn add(&mut self, layer: Layer, row: &[u8]) -> io::Result<()> {
        let v = columns::parse_record(layer, row)?;
        self.original.update(row);
        let raw = &row[..row.len() - 1];
        self.ordered.update((raw.len() as u64).to_le_bytes());
        self.ordered.update(raw);
        self.rows += 1;
        self.bytes += row.len() as u64;
        let slot = v["effective_at"]["slot"]
            .as_str()
            .map_or_else(|| v["effective_at"]["slot"].to_string(), str::to_owned);
        let key = if layer == Layer::Bronze {
            "disposition"
        } else {
            "transaction_status"
        };
        *self
            .coverage
            .entry(slot)
            .or_default()
            .entry(v[key].as_str().unwrap_or("UNAVAILABLE").into())
            .or_default() += 1;
        Ok(())
    }
    fn value(&self) -> Value {
        json!({"rows":self.rows,"reconstructed_jsonl_bytes":self.bytes,"reconstructed_jsonl_sha256":hex::encode(self.original.clone().finalize()),"ordered_logical_sha256":hex::encode(self.ordered.clone().finalize()),"typed_column_parity":"EXACT","coverage":self.coverage})
    }
}
fn audit(layer: Layer, path: &Path, ordinal: u64, total: &mut Logical) -> io::Result<Value> {
    let builder = ParquetRecordBatchReaderBuilder::try_new(File::open(path)?).map_err(invalid)?;
    if builder.schema() != &columns::schema(layer) {
        return Err(invalid("SCHEMA_DRIFT"));
    }
    let reader = builder.with_batch_size(1).build().map_err(invalid)?;
    let mut shard = Logical::default();
    for batch in reader {
        for row in storage::verify_batch(layer, &batch.map_err(invalid)?, ordinal + shard.rows)? {
            shard.add(layer, &row)?;
            total.add(layer, &row)?;
            if shard.rows > storage::MAX_ROWS as u64 || shard.bytes > storage::MAX_FILE_BYTES {
                return Err(invalid("ROUNDTRIP_LIMIT"));
            }
        }
    }
    Ok(shard.value())
}
struct LayerWriter<'a> {
    layer: Layer,
    input: &'a Path,
    output: &'a Path,
    limits: ShardLimits,
    names: Vec<String>,
    files: BTreeMap<String, Value>,
    logical: Logical,
    budget: &'a mut WriteBudget,
}
impl LayerWriter<'_> {
    fn segment(&mut self, offsets: &[u64], ordinal: u64) -> io::Result<()> {
        if self.names.len() >= MAX_SHARDS {
            return Err(invalid("SHARD_COUNT_LIMIT"));
        }
        let name = format!("{}-{:06}.parquet", self.layer.name(), self.names.len());
        let partial = self.output.join(format!("{name}.partial"));
        let result = encode(
            self.layer,
            self.input,
            offsets,
            ordinal,
            &partial,
            self.limits.bytes,
            self.budget,
        );
        if let Err(error) = result {
            // Only a known local temporary candidate is removed, never input/evidence.
            if !error.to_string().contains("SHARD_PHYSICAL_FILE_LIMIT") {
                return Err(error);
            }
            fs::remove_file(&partial)?;
            let rows = offsets.len() - 1;
            if rows <= 1 {
                return Err(invalid("RECORD_CANNOT_FIT_SHARD"));
            }
            let middle = rows / 2;
            self.segment(&offsets[..=middle], ordinal)?;
            return self.segment(&offsets[middle..], ordinal + middle as u64);
        }
        let audited = audit(self.layer, &partial, ordinal, &mut self.logical)?;
        let (sha, bytes) = file_hash(&partial, self.limits.bytes)?;
        let final_path = self.output.join(&name);
        if final_path.exists() {
            return Err(invalid("OUTPUT_EXISTS"));
        }
        fs::rename(&partial, &final_path)?;
        let schema = columns::schema_descriptor(self.layer);
        self.files.insert(name.clone(),json!({"layer":self.layer.name(),"shard_index":self.names.len(),"ordinal_start":ordinal,"ordinal_end_exclusive":ordinal+audited["rows"].as_u64().ok_or_else(||invalid("AUDIT_ROWS"))?,"sha256":sha,"bytes":bytes,"audit":audited,"schema_sha256":hash(&serde_json::to_vec(&schema).map_err(invalid)?)}));
        self.names.push(name);
        Ok(())
    }
}
/// Stream a sealed JSONL into ordered, capped Parquet files with exact readback.
/// # Errors
/// No completion on input, schema, cap, publication or parity failure.
pub fn write_shards(
    layer: Layer,
    input: &Path,
    output: &Path,
    limits: ShardLimits,
) -> io::Result<(Value, BTreeMap<String, Value>)> {
    write_shards_budgeted(
        layer,
        input,
        output,
        limits,
        &mut WriteBudget::new(MAX_DATASET_WRITE_BYTES)?,
    )
}

fn write_shards_budgeted(
    layer: Layer,
    input: &Path,
    output: &Path,
    limits: ShardLimits,
    budget: &mut WriteBudget,
) -> io::Result<(Value, BTreeMap<String, Value>)> {
    limits.validate()?;
    let (expected_sha, _) = file_hash(input, storage::MAX_FILE_BYTES)?;
    let mut reader = BufReader::new(File::open(input)?);
    let mut offsets = vec![0];
    let mut ordinal = 0;
    let mut pos = 0;
    let mut writer = LayerWriter {
        layer,
        input,
        output,
        limits,
        names: Vec::new(),
        files: BTreeMap::new(),
        logical: Logical::default(),
        budget,
    };
    while let Some(row) = read_line(&mut reader)? {
        let next = pos + row.len() as u64;
        if next > storage::MAX_FILE_BYTES {
            return Err(invalid("FILE_LIMIT"));
        }
        if offsets.len() > 1
            && (offsets.len() - 1 == limits.rows || next - offsets[0] > storage::MAX_FILE_BYTES)
        {
            writer.segment(&offsets, ordinal)?;
            ordinal += (offsets.len() - 1) as u64;
            offsets = vec![pos];
        }
        pos = next;
        offsets.push(pos);
    }
    if offsets.len() > 1 || writer.names.is_empty() {
        writer.segment(&offsets, ordinal)?;
    }
    let mut total = writer.logical.value();
    if total["reconstructed_jsonl_sha256"] != expected_sha {
        return Err(invalid("JSONL_ROUNDTRIP_HASH_MISMATCH"));
    }
    total["files"] = json!(writer.names);
    let schema = columns::schema_descriptor(layer);
    total["schema_sha256"] = json!(hash(&serde_json::to_vec(&schema).map_err(invalid)?));
    total["schema"] = schema;
    Ok((total, writer.files))
}
fn publish(path: &Path, bytes: &[u8], budget: &mut WriteBudget) -> io::Result<()> {
    // In particular, no empty COMPLETE marker is created after budget exhaustion.
    budget.check(bytes.len() as u64)?;
    let mut file = LimitedFile {
        file: OpenOptions::new().write(true).create_new(true).open(path)?,
        count: 0,
        limit: bytes.len() as u64,
        budget,
    };
    file.write_all(bytes)?;
    file.file.sync_all()
}
/// One complete source-bound projection; no retroactive class argument or resume.
/// # Errors
/// Any input/source/record/shard drift prevents the physical COMPLETE seal.
pub fn materialize(
    input: &Path,
    execution_sha: &str,
    output: &Path,
    limits: ShardLimits,
) -> io::Result<Value> {
    materialize_with_write_limit(
        input,
        execution_sha,
        output,
        limits,
        MAX_DATASET_WRITE_BYTES,
    )
}

/// Same projection with a tighter cumulative write cap for bounded regression tests.
/// No CLI override exists and a caller cannot raise the production cap.
/// # Errors
/// Rejects invalid caps or any identity, source, shard, budget or publication failure.
pub fn materialize_with_write_limit(
    input: &Path,
    execution_sha: &str,
    output: &Path,
    limits: ShardLimits,
    write_limit: u64,
) -> io::Result<Value> {
    let mut budget = WriteBudget::new(write_limit)?;
    limits.validate()?;
    let input = input.canonicalize()?;
    let parent = output
        .parent()
        .ok_or_else(|| invalid("OUTPUT_PARENT"))?
        .canonicalize()?;
    if parent.starts_with(&input) {
        return Err(invalid("OUTPUT_INSIDE_INPUT"));
    }
    let seal = admission::seal(&input, execution_sha)?;
    let admitted = admission::inspect(&input, &seal)?;
    fs::create_dir(output)?;
    File::open(&parent)?.sync_all()?;
    let mut files = BTreeMap::new();
    let mut layers = BTreeMap::new();
    for layer in [Layer::Bronze, Layer::Silver] {
        let (summary, parts) = write_shards_budgeted(
            layer,
            &input.join(format!("{}.jsonl", layer.name())),
            output,
            limits,
            &mut budget,
        )?;
        if summary["reconstructed_jsonl_sha256"]
            != seal["files"][format!("{}.jsonl", layer.name())]["sha256"]
        {
            return Err(invalid("INPUT_CHANGED_DURING_READ"));
        }
        layers.insert(layer.name(), summary);
        files.extend(parts);
    }
    if admission::seal(&input, execution_sha)? != seal
        || admission::inspect(&input, &seal)? != admitted
    {
        return Err(invalid("INPUT_CHANGED_DURING_PUBLICATION"));
    }
    let mut settings = storage::settings();
    settings["created_by"] = json!("of1-parquet-projection/0.2.0-shards");
    settings["max_rows_per_file"] = json!(limits.rows);
    settings["max_bytes_per_file"] = json!(limits.bytes);
    settings["max_shards_per_layer"] = json!(MAX_SHARDS);
    settings["max_input_jsonl_bytes"] = json!(storage::MAX_FILE_BYTES);
    settings["max_record_bytes"] = json!(storage::MAX_RECORD_BYTES);
    settings["max_batch_record_bytes"] = json!(MAX_BATCH_BYTES);
    settings["row_group_rule"] =
        json!("flush at <=512 rows and <=4MiB raw; one <=16MiB record remains atomic");
    settings["physical_split_rule"] =
        json!("row cap first; on bounded physical overflow bisect at floor(rows/2); no row split");
    settings["max_parent_bindings"] = json!(MAX_PARENT_BINDINGS);
    settings["max_dataset_written_bytes"] = json!(write_limit);
    settings["dataset_write_accounting"] = json!(
        "CUMULATIVE_OUTPUT_BYTES: both layers, discarded split probes, manifest and COMPLETE; no refunds; excludes filesystem allocation overhead"
    );
    let mut manifest = json!({"schema":"OF1_PARQUET_DATASET_2","writer":{"version":env!("CARGO_PKG_VERSION"),"source_sha256":crate::source_sha256(),"executable_sha256":file_hash(&std::env::current_exe()?,crate::MAX_EXECUTABLE_BYTES)?.0,"cargo_lock_sha256":hash(include_bytes!("../Cargo.lock")),"settings":settings},"input":seal,"files":files,"layers":layers,"selection":admitted["selection"],"sample_identity":admitted["sample_identity"],"evidence":{"slice_class":admitted["slice_class"],"receipt_evidence":admitted["receipt_evidence"],"new_domain_decoding":false,"research_ready":false,"root_to_slot_membership":"UNAVAILABLE","unknowns_preserved":true,"historical_activation":"UNPROVEN","physical_writer":"RUST_ARROW_PARQUET_BOUNDED_PROJECTION_ONLY"},"publication":{"state":"FILES_VERIFIED","cumulative_shard_write_bytes":budget.written,"does_not_assert_selection_completeness_or_research_suitability":true}});
    if let Some(binding) = admitted.get("batch_binding") {
        manifest["batch_binding"] = binding.clone();
    }
    let bytes = serde_json::to_vec_pretty(&manifest).map_err(invalid)?;
    if bytes.len() > 1024 * 1024 {
        return Err(invalid("MANIFEST_LIMIT"));
    }
    publish(&output.join("manifest.json"), &bytes, &mut budget)?;
    File::open(output)?.sync_all()?;
    publish(
        &output.join("COMPLETE"),
        format!("{}\n", hash(&bytes)).as_bytes(),
        &mut budget,
    )?;
    File::open(output)?.sync_all()?;
    Ok(manifest)
}

#[cfg(test)]
mod write_budget_tests {
    use super::*;

    #[test]
    fn cumulative_budget_is_exact_checked_and_never_refunded() {
        assert!(WriteBudget::new(0).is_err());
        assert!(WriteBudget::new(MAX_DATASET_WRITE_BYTES + 1).is_err());
        let mut budget = WriteBudget::new(9).unwrap();
        budget.charge(4).unwrap();
        budget.charge(5).unwrap();
        assert_eq!(budget.written, 9);
        assert!(budget.check(1).is_err());
        assert!(budget.check(u64::MAX).is_err());
        assert!(budget.charge(1).is_err());
        assert_eq!(budget.written, 9);
    }

    #[test]
    fn removed_partial_does_not_restore_shared_budget() {
        let d = tempfile::tempdir().unwrap();
        let partial = d.path().join("probe.partial");
        let mut budget = WriteBudget::new(7).unwrap();
        {
            let mut sink = LimitedFile {
                file: File::create(&partial).unwrap(),
                count: 0,
                limit: 5,
                budget: &mut budget,
            };
            sink.write_all(b"first").unwrap();
            assert!(
                sink.write_all(b"x")
                    .unwrap_err()
                    .to_string()
                    .contains("SHARD_PHYSICAL_FILE_LIMIT")
            );
        }
        fs::remove_file(partial).unwrap();
        assert_eq!(budget.written, 5);
        let complete = d.path().join("COMPLETE");
        assert!(
            publish(&complete, b"seal", &mut budget)
                .unwrap_err()
                .to_string()
                .contains("DATASET_CUMULATIVE_WRITE_LIMIT")
        );
        assert!(!complete.exists());
        assert_eq!(budget.written, 5);
    }
}
