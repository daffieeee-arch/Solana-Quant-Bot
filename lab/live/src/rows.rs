//! Replay input: the extractor's event rows of OF1 chunks (`events/v1/chunks/<range>/`) or of recorder segments
//! (`<root>/segments/<seq>/`, the same table layout), read back from their Parquet files as the extractor's own
//! `Val`s and put in the order the Python replay uses: (slot, tx_index, outer_ix, inner_ix), NULLs last as DuckDB
//! sorts them (paper/live.py `segment_events`, backtest/streams.py), then event_seq and the table name so equal
//! keys stay deterministic. Only rows in [from, to) are kept; a row without a slot is not in any range.
//!
//! M1 part 1 (lab/docs/rust-live-route-design-2026-10-16.md §2, §3): the rows only. The legs the engine trades
//! (the store's SQL: SOL side, depth, farmer flags, backfills, duplicates) come in later steps.

use crate::idl::Val;
use anyhow::{anyhow, bail, Context, Result};
use arrow_array::{Array, BooleanArray, Int64Array, LargeStringArray, StringArray, StringViewArray, UInt64Array};
use arrow_schema::DataType;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// The event table families of a chunk or segment: pump.fun and PumpSwap events, one table per event.
pub const EVENT_PREFIXES: [&str; 2] = ["pump", "pump_amm"];

/// A chunk or a segment: its slot range [lo, hi) and its directory.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Part {
    pub lo: u64,
    pub hi: u64,
    pub dir: PathBuf,
}

/// The complete chunks under `root` (their `_manifest.json` says `complete`), by slot (backtest/data.py).
pub fn chunks(root: &Path) -> Result<Vec<Part>> {
    let mut out = Vec::new();
    for e in fs::read_dir(root).with_context(|| format!("read {}", root.display()))? {
        let dir = e?.path();
        let Ok(raw) = fs::read(dir.join("_manifest.json")) else { continue };
        let m: Value = serde_json::from_slice(&raw).with_context(|| format!("{}/_manifest.json", dir.display()))?;
        if m["status"] == "complete" {
            if let (Some(lo), Some(hi)) = (m["slot_start"].as_u64(), m["slot_end_exclusive"].as_u64()) {
                out.push(Part { lo, hi, dir });
            }
        }
    }
    out.sort_by_key(|p| (p.lo, p.hi));
    Ok(out)
}

/// The complete segments under a recorder root (`segments/<seq>/_segment.json` with `complete`), by slot.
pub fn segments(root: &Path) -> Result<Vec<Part>> {
    let mut out = Vec::new();
    let dir = root.join("segments");
    for e in fs::read_dir(&dir).with_context(|| format!("read {}", dir.display()))? {
        let dir = e?.path();
        let Ok(raw) = fs::read(dir.join("_segment.json")) else { continue };
        let m: Value = serde_json::from_slice(&raw).with_context(|| format!("{}/_segment.json", dir.display()))?;
        if m["complete"].as_bool() == Some(true) {
            if let (Some(first), Some(last)) = (m["first_slot"].as_u64(), m["last_slot"].as_u64()) {
                out.push(Part { lo: first, hi: last + 1, dir });
            }
        }
    }
    out.sort_by_key(|p| (p.lo, p.hi));
    Ok(out)
}

/// One event row: its table (e.g. `pump/TradeEvent`), its columns in file order and its values.
#[derive(Clone, Debug, PartialEq)]
pub struct EventRow {
    pub table: Arc<str>,
    pub columns: Arc<Vec<String>>,
    pub values: Vec<Val>,
}

/// The sort key: NULLs last within each field (DuckDB's ascending order), then the table.
type Key = ((bool, u64), (bool, u64), (bool, u64), (bool, u64), (bool, u64));

fn nulls_last(v: Option<u64>) -> (bool, u64) {
    v.map_or((true, 0), |x| (false, x))
}

impl EventRow {
    pub fn get(&self, name: &str) -> Option<&Val> {
        self.columns.iter().position(|c| c == name).map(|i| &self.values[i])
    }

    pub fn u64(&self, name: &str) -> Option<u64> {
        match self.get(name) {
            Some(Val::U64(x)) => Some(*x),
            Some(Val::I64(x)) => u64::try_from(*x).ok(),
            _ => None,
        }
    }

    fn key(&self) -> Key {
        (nulls_last(self.u64("slot")), nulls_last(self.u64("tx_index")), nulls_last(self.u64("outer_ix")),
         nulls_last(self.u64("inner_ix")), nulls_last(self.u64("event_seq")))
    }

    /// sha256 of the row's columns and values, rendered `name=value` and joined by U+001F (null `null`, booleans
    /// `true`/`false`, integers in decimal, strings as they are): the test compares it with DuckDB's reading.
    pub fn digest(&self) -> String {
        let mut h = Sha256::new();
        for (i, (c, v)) in self.columns.iter().zip(&self.values).enumerate() {
            if i > 0 {
                h.update([0x1f]);
            }
            h.update(c.as_bytes());
            h.update(b"=");
            h.update(render(v).as_bytes());
        }
        h.finalize().iter().map(|b| format!("{b:02x}")).collect()
    }
}

pub fn render(v: &Val) -> String {
    match v {
        Val::Null => "null".into(),
        Val::Bool(b) => b.to_string(),
        Val::U64(x) => x.to_string(),
        Val::I64(x) => x.to_string(),
        Val::Str(s) => s.clone(),
    }
}

fn cell(col: &dyn Array, i: usize) -> Result<Val> {
    if col.is_null(i) {
        return Ok(Val::Null);
    }
    let any = col.as_any();
    let bad = || anyhow!("column type {} does not match its array", col.data_type());
    Ok(match col.data_type() {
        DataType::UInt64 => Val::U64(any.downcast_ref::<UInt64Array>().ok_or_else(bad)?.value(i)),
        DataType::Int64 => Val::I64(any.downcast_ref::<Int64Array>().ok_or_else(bad)?.value(i)),
        DataType::Boolean => Val::Bool(any.downcast_ref::<BooleanArray>().ok_or_else(bad)?.value(i)),
        DataType::Utf8 => Val::Str(any.downcast_ref::<StringArray>().ok_or_else(bad)?.value(i).to_owned()),
        DataType::LargeUtf8 => Val::Str(any.downcast_ref::<LargeStringArray>().ok_or_else(bad)?.value(i).to_owned()),
        DataType::Utf8View => Val::Str(any.downcast_ref::<StringViewArray>().ok_or_else(bad)?.value(i).to_owned()),
        other => bail!("unsupported column type {other} (the extractor writes bool, u64, i64 and utf8 only)"),
    })
}

/// The rows of one Parquet file with a slot in [lo, hi).
fn read_file(path: &Path, table: &Arc<str>, lo: u64, hi: u64, out: &mut Vec<EventRow>) -> Result<()> {
    let file = fs::File::open(path).with_context(|| format!("open {}", path.display()))?;
    let reader = ParquetRecordBatchReaderBuilder::try_new(file)?.build()?;
    for batch in reader {
        let batch = batch.with_context(|| format!("read {}", path.display()))?;
        let schema = batch.schema();
        let columns: Arc<Vec<String>> = Arc::new(schema.fields().iter().map(|f| f.name().clone()).collect());
        let Some(si) = columns.iter().position(|c| c == "slot") else { bail!("{}: no slot column", path.display()) };
        let slots = batch.column(si);
        for i in 0..batch.num_rows() {
            let slot = match cell(slots.as_ref(), i)? {
                Val::U64(s) => s,
                Val::I64(s) if s >= 0 => s as u64,
                _ => continue,
            };
            if slot < lo || slot >= hi {
                continue;
            }
            let values = (0..batch.num_columns()).map(|c| cell(batch.column(c).as_ref(), i)).collect::<Result<Vec<_>>>()?;
            out.push(EventRow { table: table.clone(), columns: columns.clone(), values });
        }
    }
    Ok(())
}

/// The event tables of one part: `<prefix>/<Event>` directories holding `part-*.parquet`, by name.
fn tables(dir: &Path, prefixes: &[&str]) -> Result<Vec<(String, Vec<PathBuf>)>> {
    let mut out = Vec::new();
    for prefix in prefixes {
        let Ok(entries) = fs::read_dir(dir.join(prefix)) else { continue };
        for e in entries {
            let tdir = e?.path();
            if !tdir.is_dir() {
                continue;
            }
            let mut files: Vec<PathBuf> = fs::read_dir(&tdir)?
                .filter_map(|f| f.ok().map(|f| f.path()))
                .filter(|p| p.extension().is_some_and(|x| x == "parquet"))
                .collect();
            files.sort();
            if !files.is_empty() {
                let name = format!("{prefix}/{}", tdir.file_name().and_then(|n| n.to_str()).unwrap_or_default());
                out.push((name, files));
            }
        }
    }
    out.sort();
    Ok(out)
}

/// What `read_rows` read: rows per table and keys seen more than once (a recorder restart can record a slot twice;
/// LiveFeed drops the second copy, which a later step mirrors).
#[derive(Debug, Default)]
pub struct Stats {
    pub rows: BTreeMap<String, u64>,
    pub parts: usize,
    pub duplicate_keys: u64,
}

/// Every event row of `parts` with a slot in [lo, hi), in the replay's order.
pub fn read_rows(parts: &[Part], lo: u64, hi: u64, prefixes: &[&str]) -> Result<(Vec<EventRow>, Stats)> {
    let mut rows = Vec::new();
    let mut stats = Stats::default();
    for p in parts.iter().filter(|p| p.lo < hi && p.hi > lo) {
        stats.parts += 1;
        for (name, files) in tables(&p.dir, prefixes)? {
            let table: Arc<str> = Arc::from(name.as_str());
            let before = rows.len();
            for f in files {
                read_file(&f, &table, lo.max(p.lo), hi.min(p.hi), &mut rows)?; // a part's rows belong to its own range
            }
            *stats.rows.entry(name).or_default() += (rows.len() - before) as u64;
        }
    }
    rows.sort_by(|a, b| a.key().cmp(&b.key()).then_with(|| a.table.cmp(&b.table)));
    stats.duplicate_keys = rows.windows(2).filter(|w| w[0].key() == w[1].key() && w[0].table == w[1].table).count() as u64;
    Ok((rows, stats))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::idl::Kind;
    use crate::sink::write_parquet;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-live-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn cols(extra: &[(&str, Kind)]) -> Vec<(String, Kind)> {
        let mut c: Vec<(String, Kind)> = [("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64)]
            .iter()
            .map(|(n, k)| ((*n).to_owned(), *k))
            .collect();
        c.extend(extra.iter().map(|(n, k)| ((*n).to_owned(), *k)));
        c
    }

    fn row(slot: u64, tx: u64, outer: u64, inner: Option<u64>, extra: Val) -> Vec<Val> {
        vec![Val::U64(slot), Val::U64(tx), Val::U64(outer), inner.map_or(Val::Null, Val::U64), extra]
    }

    /// A chunk with two event tables written by the extractor's own writer, rows out of order and one table split
    /// in two parts.
    fn chunk(root: &Path, lo: u64, hi: u64) -> PathBuf {
        let dir = root.join(format!("{lo}-{hi}"));
        let trade = cols(&[("mint", Kind::Utf8)]);
        write_parquet(&dir, "pump/TradeEvent", 0, &trade, &[row(lo + 5, 2, 0, Some(1), Val::Str("B".into())),
                                                           row(lo + 1, 7, 0, None, Val::Str("A".into()))]).unwrap();
        write_parquet(&dir, "pump/TradeEvent", 1, &trade, &[row(lo + 1, 7, 0, Some(3), Val::Str("C".into()))]).unwrap();
        let buy = cols(&[("is_x", Kind::Bool)]);
        write_parquet(&dir, "pump_amm/BuyEvent", 0, &buy, &[row(lo + 1, 7, 0, Some(2), Val::Bool(true)),
                                                           row(hi + 9, 0, 0, Some(0), Val::Bool(false))]).unwrap();
        fs::write(dir.join("_manifest.json"), serde_json::json!({"status": "complete", "slot_start": lo, "slot_end_exclusive": hi}).to_string()).unwrap();
        dir
    }

    #[test]
    fn rows_come_back_in_the_replays_order_with_their_values() {
        let root = tmp("rows");
        chunk(&root, 1000, 2000);
        fs::create_dir_all(root.join("2000-3000")).unwrap(); // no manifest: not a complete chunk
        let parts = chunks(&root).unwrap();
        assert_eq!(parts.len(), 1);
        let (rows, stats) = read_rows(&parts, 1000, 2000, &EVENT_PREFIXES).unwrap();
        let order: Vec<(u64, Option<u64>, &str)> = rows.iter().map(|r| (r.u64("slot").unwrap(), r.u64("inner_ix"), &*r.table)).collect();
        // slot 1001 tx 7: inner 2 (BuyEvent), 3 (TradeEvent part 1), then the NULL inner_ix last; then slot 1005
        assert_eq!(order, vec![(1001, Some(2), "pump_amm/BuyEvent"), (1001, Some(3), "pump/TradeEvent"), (1001, None, "pump/TradeEvent"),
                               (1005, Some(1), "pump/TradeEvent")]);
        assert_eq!(rows[1].get("mint"), Some(&Val::Str("C".into())));
        assert_eq!(rows[0].get("is_x"), Some(&Val::Bool(true)));
        assert_eq!(stats.rows.get("pump/TradeEvent"), Some(&3));
        assert_eq!(stats.rows.get("pump_amm/BuyEvent"), Some(&1)); // its row beyond the range is not read
        let (narrow, _) = read_rows(&parts, 1002, 2000, &EVENT_PREFIXES).unwrap();
        assert_eq!(narrow.len(), 1);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn segments_are_found_by_their_complete_meta_and_read_like_chunks() {
        let root = tmp("segs");
        let seg = root.join("segments/000000000");
        let c = chunk(&root.join("src"), 1000, 1006);
        fs::create_dir_all(seg.parent().unwrap()).unwrap();
        fs::rename(&c, &seg).unwrap();
        fs::write(seg.join("_segment.json"), serde_json::json!({"seq": 0, "first_slot": 1000, "last_slot": 1005, "complete": true}).to_string()).unwrap();
        fs::create_dir_all(root.join("segments/000000001/pump")).unwrap(); // unfinished: no _segment.json
        let parts = segments(&root).unwrap();
        assert_eq!(parts, vec![Part { lo: 1000, hi: 1006, dir: seg.clone() }]);
        assert_eq!(read_rows(&parts, 0, u64::MAX, &EVENT_PREFIXES).unwrap().0.len(), 4);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn snappy_zstd_and_uncompressed_parts_are_read_alike() {
        // The recorder and the extractor write zstd (extractor/src/sink.rs); DuckDB's COPY writes snappy by default:
        // the shadow recorder's segments (paper/shadow.py) and the Python tests' segments (NAS job 95).
        use arrow_array::{ArrayRef, RecordBatch, StringArray, UInt64Array};
        use parquet::arrow::ArrowWriter;
        use parquet::basic::{Compression, ZstdLevel};
        use parquet::file::properties::WriterProperties;
        let root = tmp("codecs");
        let dir = root.join("1000-2000");
        let d = dir.join("pump/TradeEvent");
        fs::create_dir_all(&d).unwrap();
        let codecs = [Compression::SNAPPY, Compression::ZSTD(ZstdLevel::try_new(3).unwrap()), Compression::UNCOMPRESSED];
        for (i, c) in codecs.into_iter().enumerate() {
            let cols: Vec<ArrayRef> = vec![
                Arc::new(UInt64Array::from(vec![1001 + i as u64])),
                Arc::new(UInt64Array::from(vec![0u64])),
                Arc::new(UInt64Array::from(vec![0u64])),
                Arc::new(UInt64Array::from(vec![Some(i as u64)])),
                Arc::new(StringArray::from(vec![format!("M{i}")])),
            ];
            let batch = RecordBatch::try_from_iter(["slot", "tx_index", "outer_ix", "inner_ix", "mint"].into_iter().zip(cols)).unwrap();
            let f = fs::File::create(d.join(format!("part-{i:05}.parquet"))).unwrap();
            let mut w = ArrowWriter::try_new(f, batch.schema(), Some(WriterProperties::builder().set_compression(c).build())).unwrap();
            w.write(&batch).unwrap();
            w.close().unwrap();
        }
        fs::write(dir.join("_manifest.json"), serde_json::json!({"status": "complete", "slot_start": 1000, "slot_end_exclusive": 2000}).to_string()).unwrap();
        let (rows, _) = read_rows(&chunks(&root).unwrap(), 1000, 2000, &EVENT_PREFIXES).unwrap();
        let mints: Vec<Option<Val>> = rows.iter().map(|r| r.get("mint").cloned()).collect();
        assert_eq!(mints, vec![Some(Val::Str("M0".into())), Some(Val::Str("M1".into())), Some(Val::Str("M2".into()))]);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_digest_renders_every_kind_of_value() {
        let r = EventRow {
            table: Arc::from("pump/TradeEvent"),
            columns: Arc::new(vec!["a".into(), "b".into(), "c".into(), "d".into(), "e".into()]),
            values: vec![Val::Null, Val::Bool(true), Val::U64(7), Val::I64(-3), Val::Str("x".into())],
        };
        let want: String = Sha256::digest("a=null\u{1f}b=true\u{1f}c=7\u{1f}d=-3\u{1f}e=x".as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(r.digest(), want);
    }
}
