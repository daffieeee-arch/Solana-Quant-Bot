//! Buffered Parquet tables with a dynamic column list, written by one background thread.

use crate::idl::{Kind, Val};
use anyhow::{anyhow, Context, Result};
use arrow_array::builder::{BooleanBuilder, Int64Builder, StringBuilder, UInt64Builder};
use arrow_array::{ArrayRef, RecordBatch};
use arrow_schema::{DataType, Field, Schema};
use parquet::arrow::ArrowWriter;
use parquet::basic::{Compression, ZstdLevel};
use parquet::file::properties::WriterProperties;
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{sync_channel, Receiver, SyncSender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

pub type Columns = Arc<Vec<(String, Kind)>>;

pub struct Row {
    /// Table path relative to the output directory, e.g. `pump/TradeEvent`.
    pub table: Arc<str>,
    pub columns: Columns,
    pub values: Vec<Val>,
}

enum Msg {
    Rows(Vec<Row>),
    Finish,
}

struct Table {
    columns: Columns,
    rows: Vec<Vec<Val>>,
    parts: usize,
    total: u64,
}

const FLUSH_ROWS: usize = 200_000;

fn kind_type(k: Kind) -> DataType {
    match k {
        Kind::Bool => DataType::Boolean,
        Kind::U64 => DataType::UInt64,
        Kind::I64 => DataType::Int64,
        Kind::Utf8 => DataType::Utf8,
    }
}

/// Write rows to `<dir>/<table>/part-<n>.parquet` (via a temporary file and an atomic rename).
pub fn write_parquet(dir: &Path, table: &str, part: usize, columns: &[(String, Kind)], rows: &[Vec<Val>]) -> Result<PathBuf> {
    let schema = Arc::new(Schema::new(
        columns.iter().map(|(n, k)| Field::new(n, kind_type(*k), true)).collect::<Vec<_>>(),
    ));
    let mut arrays: Vec<ArrayRef> = Vec::with_capacity(columns.len());
    for (ci, (name, kind)) in columns.iter().enumerate() {
        let cell = |r: &Vec<Val>| r.get(ci).cloned().unwrap_or(Val::Null);
        let array: ArrayRef = match kind {
            Kind::Bool => {
                let mut b = BooleanBuilder::with_capacity(rows.len());
                for r in rows {
                    match cell(r) {
                        Val::Bool(v) => b.append_value(v),
                        Val::Null => b.append_null(),
                        other => return Err(anyhow!("{table}.{name}: expected bool, got {other:?}")),
                    }
                }
                Arc::new(b.finish())
            }
            Kind::U64 => {
                let mut b = UInt64Builder::with_capacity(rows.len());
                for r in rows {
                    match cell(r) {
                        Val::U64(v) => b.append_value(v),
                        Val::Null => b.append_null(),
                        other => return Err(anyhow!("{table}.{name}: expected u64, got {other:?}")),
                    }
                }
                Arc::new(b.finish())
            }
            Kind::I64 => {
                let mut b = Int64Builder::with_capacity(rows.len());
                for r in rows {
                    match cell(r) {
                        Val::I64(v) => b.append_value(v),
                        Val::Null => b.append_null(),
                        other => return Err(anyhow!("{table}.{name}: expected i64, got {other:?}")),
                    }
                }
                Arc::new(b.finish())
            }
            Kind::Utf8 => {
                let mut b = StringBuilder::with_capacity(rows.len(), rows.len() * 44);
                for r in rows {
                    match cell(r) {
                        Val::Str(v) => b.append_value(v),
                        Val::Null => b.append_null(),
                        other => return Err(anyhow!("{table}.{name}: expected string, got {other:?}")),
                    }
                }
                Arc::new(b.finish())
            }
        };
        arrays.push(array);
    }
    let batch = RecordBatch::try_new(schema.clone(), arrays)?;
    let out_dir = dir.join(table);
    fs::create_dir_all(&out_dir)?;
    let final_path = out_dir.join(format!("part-{part:05}.parquet"));
    let tmp_path = out_dir.join(format!(".part-{part:05}.parquet.tmp"));
    let file = fs::File::create(&tmp_path).with_context(|| format!("create {}", tmp_path.display()))?;
    let props = WriterProperties::builder()
        .set_compression(Compression::ZSTD(ZstdLevel::try_new(3)?))
        .build();
    let mut writer = ArrowWriter::try_new(file, schema, Some(props))?;
    writer.write(&batch)?;
    writer.into_inner()?.sync_all()?;
    fs::rename(&tmp_path, &final_path)?;
    fs::File::open(&out_dir)?.sync_all()?;
    Ok(final_path)
}

/// Background Parquet writer. Rows arrive over a bounded channel (backpressure on the firehose).
pub struct Sink {
    tx: SyncSender<Msg>,
    handle: Mutex<Option<JoinHandle<Result<BTreeMap<String, u64>>>>>,
}

impl Sink {
    pub fn start(dir: PathBuf) -> Self {
        let (tx, rx) = sync_channel::<Msg>(4096);
        let handle = std::thread::Builder::new()
            .name("lab-parquet-writer".into())
            .spawn(move || writer_loop(&dir, &rx))
            .expect("spawn writer thread");
        Self { tx, handle: Mutex::new(Some(handle)) }
    }

    pub fn send(&self, rows: Vec<Row>) -> Result<()> {
        if rows.is_empty() {
            return Ok(());
        }
        self.tx.send(Msg::Rows(rows)).map_err(|_| anyhow!("parquet writer thread stopped"))
    }

    /// Flush every table and return the row count per table. Idempotent.
    pub fn finish(&self) -> Result<BTreeMap<String, u64>> {
        let Some(handle) = self.handle.lock().expect("sink lock").take() else {
            return Err(anyhow!("sink already finished"));
        };
        let _ = self.tx.send(Msg::Finish);
        handle.join().map_err(|_| anyhow!("parquet writer thread panicked"))?
    }
}

fn writer_loop(dir: &Path, rx: &Receiver<Msg>) -> Result<BTreeMap<String, u64>> {
    let mut tables: HashMap<Arc<str>, Table> = HashMap::new();
    let flush = |name: &str, t: &mut Table| -> Result<()> {
        if t.rows.is_empty() {
            return Ok(());
        }
        write_parquet(dir, name, t.parts, &t.columns, &t.rows)?;
        t.total += t.rows.len() as u64;
        t.parts += 1;
        t.rows.clear();
        Ok(())
    };
    while let Ok(msg) = rx.recv() {
        match msg {
            Msg::Rows(rows) => {
                for row in rows {
                    let t = tables.entry(row.table.clone()).or_insert_with(|| Table {
                        columns: row.columns.clone(),
                        rows: Vec::new(),
                        parts: 0,
                        total: 0,
                    });
                    t.rows.push(row.values);
                    if t.rows.len() >= FLUSH_ROWS {
                        flush(&row.table, t)?;
                    }
                }
            }
            Msg::Finish => break,
        }
    }
    let mut counts = BTreeMap::new();
    for (name, t) in &mut tables {
        flush(name, t)?;
        counts.insert(name.to_string(), t.total);
    }
    Ok(counts)
}
