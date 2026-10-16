//! Rolling segments in the OF1 chunk layout, read by `paper.live.LiveFeed` and `paper.reconcile`:
//!
//!   <root>/segments/<seq:09d>/
//!     pump/<Event>/part-00000.parquet, pump_amm/<Event>/...   the extractor's event rows
//!     failed_txs/, anomalies/                                   the extractor's other tables
//!     blocks/        slot, parent_slot, block_time, block_height, executed_tx_count, entry_count, skipped
//!     arrivals/      per transaction: slot, tx_index, signature, received_unix_ns, created_unix_ns, stream
//!     vaults/        vault balance updates (account stream with a data slice), when subscribed
//!     pool_accounts/ PumpSwap pool accounts seen on the pool account stream, when subscribed
//!     backfill/      the earlier transactions of new probe pools (from_slot replay), in the same table
//!                    layout: farmer history for LiveFeed, not events (reconcile does not read it)
//!     watchlist.json the transaction filter of this segment (pools, wallets, vaults)
//!     _segment.json  written last (atomic rename), after every file and directory of the segment is
//!                    on disk (fsync): seq, first_slot, last_slot, complete, usage, ...
//!
//! Slots are only written once closed (confirmed, see `SlotBuffer`), so a segment holds whole
//! slots and every row of a slot is in one segment. A segment stays in memory until its
//! `_segment.json` is on disk; a failed write is a `SegmentWriteError`, which ends the recorder so
//! that it resumes after the last segment on disk (main.rs, EXIT_SEGMENT).

use crate::idl::{Kind, Val};
use crate::sink::{write_parquet, Columns, Row};
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

pub const BLOCK_COLS: [(&str, Kind); 7] = [
    // The extractor's `blocks` table (extract.rs BLOCK_COLS).
    ("slot", Kind::U64),
    ("parent_slot", Kind::U64),
    ("block_time", Kind::I64),
    ("block_height", Kind::U64),
    ("executed_tx_count", Kind::U64),
    ("entry_count", Kind::U64),
    ("skipped", Kind::Bool),
];

/// Alpenglow block footers (epoch 1064 on): the producer's block time in nanoseconds, against our
/// receive time and the server's `created_at` (latency from block production).
pub const FOOTER_COLS: [(&str, Kind); 5] = [
    ("slot", Kind::U64),
    ("bank_id", Kind::U64),
    ("block_producer_time_ns", Kind::U64),
    ("received_unix_ns", Kind::U64),
    ("created_unix_ns", Kind::U64),
];

pub const ANOMALY_COLS: [(&str, Kind); 9] = [
    // The extractor's `anomalies` table (extract.rs ANOMALY_COLS).
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("signature", Kind::Utf8),
    ("outer_ix", Kind::U64),
    ("inner_ix", Kind::U64),
    ("program", Kind::Utf8),
    ("kind", Kind::Utf8),
    ("event", Kind::Utf8),
    ("payload_hex", Kind::Utf8),
];

pub const ARRIVAL_COLS: [(&str, Kind); 6] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("signature", Kind::Utf8),
    // Local receive time and the server's `created_at` of the update (latency per transaction).
    ("received_unix_ns", Kind::U64),
    ("created_unix_ns", Kind::U64),
    ("stream", Kind::Utf8),
];

pub fn columns(spec: &[(&str, Kind)]) -> Columns {
    Arc::new(spec.iter().map(|(n, k)| ((*n).to_owned(), *k)).collect())
}

/// Rows of slots that are not confirmed yet. A slot closes once it is confirmed and `lag` more
/// confirmed slots were seen (Yellowstone sends a confirmed slot's updates before its status, the
/// lag only guards against reordering). Rows of a closed slot that still arrive are late: they are
/// counted and dropped, so a segment never changes after it is written.
pub struct SlotBuffer {
    pending: BTreeMap<u64, Vec<Row>>,
    confirmed: BTreeSet<u64>,
    max_confirmed: u64,
    pub last_closed: Option<u64>,
    lag: u64,
    pub late: u64,
    /// Slots that had rows but were closed without a confirmed status (should stay 0).
    pub unconfirmed: u64,
}

impl SlotBuffer {
    pub fn new(lag: u64, last_closed: Option<u64>) -> Self {
        Self { pending: BTreeMap::new(), confirmed: BTreeSet::new(), max_confirmed: 0, last_closed, lag, late: 0, unconfirmed: 0 }
    }

    /// Buffer rows of `slot`; false (and dropped) when the slot is already closed.
    pub fn push(&mut self, slot: u64, rows: Vec<Row>) -> bool {
        if self.last_closed.is_some_and(|c| slot <= c) {
            self.late += rows.len() as u64;
            return false;
        }
        self.pending.entry(slot).or_default().extend(rows);
        true
    }

    /// A confirmed status for `slot`: returns the slots that close now, in order, with their rows.
    pub fn confirm(&mut self, slot: u64) -> Vec<(u64, Vec<Row>)> {
        if self.last_closed.is_some_and(|c| slot <= c) {
            return Vec::new();
        }
        self.confirmed.insert(slot);
        self.max_confirmed = self.max_confirmed.max(slot);
        let upto = self.max_confirmed.saturating_sub(self.lag);
        let mut out = Vec::new();
        let candidates: Vec<u64> = self.confirmed.range(..=upto).copied().collect();
        for s in candidates {
            // Rows of an earlier slot that never got its own status close with it (a later
            // closed slot means they can no longer be written in order).
            let stale: Vec<u64> = self.pending.range(..s).map(|(k, _)| *k).filter(|k| !self.confirmed.contains(k)).collect();
            for k in stale {
                self.unconfirmed += 1;
                out.push((k, self.pending.remove(&k).unwrap_or_default()));
            }
            self.confirmed.remove(&s);
            out.push((s, self.pending.remove(&s).unwrap_or_default()));
        }
        out.sort_by_key(|(s, _)| *s);
        if let Some((s, _)) = out.last() {
            self.last_closed = Some(self.last_closed.map_or(*s, |c| c.max(*s)));
        }
        out
    }

    /// After a disconnect: drop what was not closed (the stream resends it from `last_closed + 1`).
    pub fn reset(&mut self) {
        self.pending.clear();
        self.confirmed.clear();
        self.max_confirmed = 0;
    }

    pub fn pending_slots(&self) -> usize {
        self.pending.len()
    }
}

type Tables = BTreeMap<Arc<str>, (Columns, Vec<Vec<Val>>)>;

/// A segment that could not be written (its directory, a table, the watchlist, the sync or
/// `_segment.json`). Its rows are still in memory (`Segments::close` keeps them), but its closed
/// slots are past the last segment on disk: a reconnect in this process would resume after them.
/// The recorder therefore ends (EXIT_SEGMENT) and resumes after the last complete segment.
/// Only fixed labels and the I/O error kind: nothing derived from the updates.
#[derive(Debug)]
pub struct SegmentWriteError {
    pub seq: u64,
    pub stage: &'static str,
    pub kind: Option<std::io::ErrorKind>,
}

impl SegmentWriteError {
    fn at(seq: u64, stage: &'static str) -> impl FnOnce(anyhow::Error) -> Self {
        move |e| Self { seq, stage, kind: e.chain().find_map(|c| c.downcast_ref::<std::io::Error>()).map(std::io::Error::kind) }
    }
}

impl std::fmt::Display for SegmentWriteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self.kind {
            Some(k) => write!(f, "segment {:09} could not be written ({}: {k:?})", self.seq, self.stage),
            None => write!(f, "segment {:09} could not be written ({})", self.seq, self.stage),
        }
    }
}

impl std::error::Error for SegmentWriteError {}

struct Open {
    first_slot: Option<u64>,
    last_slot: u64,
    started: Instant,
    tables: Tables,
    backfill: Tables,
    slots: u64,
}

fn add_to(tables: &mut Tables, rows: Vec<Row>) {
    for r in rows {
        tables.entry(r.table.clone()).or_insert_with(|| (r.columns.clone(), Vec::new())).1.push(r.values);
    }
}

fn write_tables(dir: &Path, tables: &Tables) -> Result<serde_json::Map<String, Value>> {
    let mut rows = serde_json::Map::new();
    for (table, (cols, values)) in tables {
        if values.is_empty() {
            continue;
        }
        write_parquet(dir, table, 0, cols, values)?;
        rows.insert(table.to_string(), json!(values.len()));
    }
    Ok(rows)
}

impl Open {
    fn new() -> Self {
        Self { first_slot: None, last_slot: 0, started: Instant::now(), tables: BTreeMap::new(), backfill: BTreeMap::new(), slots: 0 }
    }
}

pub struct Segments {
    root: PathBuf,
    pub seq: u64,
    open: Open,
    seg_slots: u64,
    seg_secs: u64,
    /// Puts the segment's files and directories on disk before `_segment.json` (tests inject a check
    /// or a failure).
    pub(crate) sync: fn(&Path) -> Result<()>,
}

/// fsync every file and directory under `dir`, the directories after their entries: a segment's
/// parquet files are on disk before `_segment.json` says it is complete.
pub fn sync_tree(dir: &Path) -> Result<()> {
    for e in fs::read_dir(dir)? {
        let e = e?;
        if e.file_type()?.is_dir() {
            sync_tree(&e.path())?;
        } else {
            fs::File::open(e.path())?.sync_all().with_context(|| format!("fsync {}", e.path().display()))?;
        }
    }
    fs::File::open(dir)?.sync_all().with_context(|| format!("fsync {}", dir.display()))?;
    Ok(())
}

pub fn write_json_atomic(path: &Path, v: &Value) -> Result<()> {
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(v)?).with_context(|| format!("write {}", tmp.display()))?;
    fs::File::open(&tmp)?.sync_all()?;
    fs::rename(&tmp, path)?;
    Ok(())
}

impl Segments {
    /// Open the root: segments without `_segment.json` (an interrupted write of this recorder)
    /// are removed; numbering continues after the last complete one. Returns the last written slot.
    pub fn open(root: &Path, seg_slots: u64, seg_secs: u64) -> Result<(Self, Option<u64>)> {
        let dir = root.join("segments");
        fs::create_dir_all(&dir)?;
        let mut next = 0u64;
        let mut last_slot = None;
        for e in fs::read_dir(&dir)? {
            let e = e?;
            let Some(seq) = e.file_name().to_str().and_then(|n| n.parse::<u64>().ok()) else { continue };
            let meta = e.path().join("_segment.json");
            match fs::read(&meta).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
                Some(m) if m["complete"].as_bool() == Some(true) => {
                    next = next.max(seq + 1);
                    if let Some(s) = m["last_slot"].as_u64() {
                        last_slot = Some(last_slot.map_or(s, |l: u64| l.max(s)));
                    }
                }
                _ => {
                    log::warn!("removing unfinished segment {}", e.path().display());
                    fs::remove_dir_all(e.path())?;
                }
            }
        }
        Ok((Self { root: root.to_owned(), seq: next, open: Open::new(), seg_slots, seg_secs, sync: sync_tree }, last_slot))
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Add the rows of a closed slot.
    pub fn add_slot(&mut self, slot: u64, rows: Vec<Row>) {
        if self.open.first_slot.is_none() {
            self.open.first_slot = Some(slot);
        }
        self.open.last_slot = self.open.last_slot.max(slot);
        self.open.slots += 1;
        add_to(&mut self.open.tables, rows);
    }

    /// Add rows that are not gated by slot closing (vault and pool account updates).
    pub fn add_rows(&mut self, rows: Vec<Row>) {
        add_to(&mut self.open.tables, rows);
    }

    /// Add a finished backfill's rows (written under backfill/ of the open segment).
    pub fn add_backfill(&mut self, rows: Vec<Row>) {
        add_to(&mut self.open.backfill, rows);
    }

    pub fn is_due(&self) -> bool {
        match self.open.first_slot {
            Some(first) => self.open.last_slot + 1 - first >= self.seg_slots || self.open.started.elapsed().as_secs() >= self.seg_secs,
            None => false,
        }
    }

    pub fn has_slots(&self) -> bool {
        self.open.first_slot.is_some()
    }

    /// Write the open segment (nothing when it has no closed slot yet). `meta` is merged into
    /// `_segment.json`; `watchlist` becomes `watchlist.json`. The open segment is cleared only once
    /// `_segment.json` is on disk: after a failed write (a `SegmentWriteError`) every row is still
    /// here, and the directory left without `_segment.json` is removed by the next `open`.
    pub fn close(&mut self, watchlist: &Value, meta: serde_json::Map<String, Value>) -> Result<Option<PathBuf>> {
        let Some(first) = self.open.first_slot else { return Ok(None) };
        let dir = self.root.join("segments").join(format!("{:09}", self.seq));
        self.write(&dir, first, watchlist, meta)?;
        self.open = Open::new();
        self.seq += 1;
        Ok(Some(dir))
    }

    fn write(&self, dir: &Path, first: u64, watchlist: &Value, meta: serde_json::Map<String, Value>) -> std::result::Result<(), SegmentWriteError> {
        let seq = self.seq;
        let at = move |stage: &'static str| SegmentWriteError::at(seq, stage);
        fs::create_dir_all(dir).map_err(anyhow::Error::from).map_err(at("directory"))?;
        let rows = write_tables(dir, &self.open.tables).map_err(at("tables"))?;
        let backfill_rows = write_tables(&dir.join("backfill"), &self.open.backfill).map_err(at("backfill"))?;
        write_json_atomic(&dir.join("watchlist.json"), watchlist).map_err(at("watchlist"))?;
        (self.sync)(dir).map_err(at("sync"))?; // the data on disk first: a "complete" segment never has empty parquet files
        let mut m = serde_json::Map::new();
        m.insert("seq".into(), json!(seq));
        m.insert("first_slot".into(), json!(first));
        m.insert("last_slot".into(), json!(self.open.last_slot));
        m.insert("slots_closed".into(), json!(self.open.slots));
        m.insert("rows".into(), Value::Object(rows));
        if !backfill_rows.is_empty() {
            m.insert("backfill_rows".into(), Value::Object(backfill_rows));
        }
        let written = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(anyhow::Error::from).map_err(at("clock"))?;
        m.insert("written_unix".into(), json!(written.as_secs()));
        m.extend(meta);
        m.insert("complete".into(), json!(true));
        write_json_atomic(&dir.join("_segment.json"), &Value::Object(m)).map_err(at("meta"))?;
        fs::File::open(dir).and_then(|f| f.sync_all()).map_err(anyhow::Error::from).map_err(at("meta sync"))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(table: &str, slot: u64) -> Row {
        Row { table: Arc::from(table), columns: columns(&BLOCK_COLS), values: vec![Val::U64(slot), Val::Null, Val::Null, Val::Null, Val::Null, Val::Null, Val::Bool(false)] }
    }

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-recorder-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        d
    }

    #[test]
    fn slots_close_after_confirmation_and_lag_and_late_rows_are_dropped() {
        let mut b = SlotBuffer::new(1, None);
        b.push(10, vec![row("blocks", 10)]);
        b.push(11, vec![row("blocks", 11)]);
        assert!(b.confirm(10).is_empty()); // lag 1: needs a later confirmed slot
        let out = b.confirm(11);
        assert_eq!(out.iter().map(|(s, r)| (*s, r.len())).collect::<Vec<_>>(), vec![(10, 1)]);
        assert_eq!(b.last_closed, Some(10));
        assert!(!b.push(10, vec![row("blocks", 10)]));
        assert_eq!(b.late, 1);
        let out = b.confirm(13);
        assert_eq!(out.iter().map(|(s, _)| *s).collect::<Vec<_>>(), vec![11]);
        // Confirmed slots without rows close too (they bound the segment).
        let out = b.confirm(14);
        assert_eq!(out.iter().map(|(s, _)| *s).collect::<Vec<_>>(), vec![13]);
    }

    #[test]
    fn a_reset_keeps_the_closed_slots() {
        let mut b = SlotBuffer::new(0, Some(99));
        b.push(100, vec![row("blocks", 100)]);
        b.reset();
        assert_eq!(b.pending_slots(), 0);
        assert!(b.confirm(99).is_empty());
        assert_eq!(b.confirm(100).len(), 1);
    }

    #[test]
    fn segments_are_written_whole_and_numbering_resumes() {
        let root = tmp("segments");
        let (mut s, last) = Segments::open(&root, 3, 3600).unwrap();
        assert_eq!((s.seq, last), (0, None));
        for slot in 100..103 {
            s.add_slot(slot, vec![row("blocks", slot)]);
        }
        assert!(s.is_due());
        let dir = s.close(&json!({"pools": []}), serde_json::Map::new()).unwrap().unwrap();
        assert!(dir.join("blocks/part-00000.parquet").exists());
        let meta: Value = serde_json::from_slice(&fs::read(dir.join("_segment.json")).unwrap()).unwrap();
        assert_eq!((meta["first_slot"].as_u64(), meta["last_slot"].as_u64(), meta["complete"].as_bool()), (Some(100), Some(102), Some(true)));
        // An unfinished segment directory is removed on the next start.
        fs::create_dir_all(root.join("segments/000000001/blocks")).unwrap();
        let (s2, last) = Segments::open(&root, 3, 3600).unwrap();
        assert_eq!((s2.seq, last), (1, Some(102)));
        assert!(!root.join("segments/000000001").exists());
        fs::remove_dir_all(&root).unwrap();
    }

    static SYNCED: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

    /// The injected sync: the parquet files and watchlist.json are there, _segment.json is not yet.
    fn check_then_sync(dir: &Path) -> Result<()> {
        assert!(!dir.join("_segment.json").exists());
        assert!(dir.join("blocks/part-00000.parquet").exists() && dir.join("backfill/blocks/part-00000.parquet").exists());
        assert!(dir.join("watchlist.json").exists());
        SYNCED.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        sync_tree(dir)
    }

    fn failing_sync(_: &Path) -> Result<()> {
        Err(std::io::Error::new(std::io::ErrorKind::StorageFull, "injected").into())
    }

    #[test]
    fn a_failed_write_loses_no_row_and_the_next_open_resumes_after_the_last_complete_segment() {
        let root = tmp("write-fails");
        let (mut s, _) = Segments::open(&root, 2, 3600).unwrap();
        s.add_slot(7, vec![row("blocks", 7)]);
        s.add_slot(8, vec![row("blocks", 8)]);
        s.close(&json!({"pools": []}), serde_json::Map::new()).unwrap().unwrap(); // segment 0: slots 7..8
        s.add_slot(9, vec![row("blocks", 9)]);
        s.add_slot(10, vec![row("blocks", 10)]);
        s.add_backfill(vec![row("blocks", 4)]);
        s.sync = failing_sync;
        let e = s.close(&json!({"pools": []}), serde_json::Map::new()).unwrap_err();
        let w = e.downcast_ref::<SegmentWriteError>().expect("a segment write error");
        assert_eq!((w.seq, w.stage, w.kind), (1, "sync", Some(std::io::ErrorKind::StorageFull)));
        assert_eq!(w.to_string(), "segment 000000001 could not be written (sync: StorageFull)");
        assert!(root.join("segments/000000001/blocks/part-00000.parquet").exists());
        assert!(!root.join("segments/000000001/_segment.json").exists());
        assert_eq!(s.seq, 1);
        // A restart resumes after the last complete segment on disk (slot 8), not after the slots that
        // were only in memory, and removes the unfinished directory.
        let (s2, last) = Segments::open(&root, 2, 3600).unwrap();
        assert_eq!((s2.seq, last), (1, Some(8)));
        assert!(!root.join("segments/000000001").exists());
        // Nothing was dropped from memory: once the disk works, the same segment is written whole.
        s.sync = sync_tree;
        let dir = s.close(&json!({"pools": []}), serde_json::Map::new()).unwrap().unwrap();
        let meta: Value = serde_json::from_slice(&fs::read(dir.join("_segment.json")).unwrap()).unwrap();
        assert_eq!((meta["seq"].as_u64(), meta["first_slot"].as_u64(), meta["last_slot"].as_u64()), (Some(1), Some(9), Some(10)));
        assert_eq!((meta["rows"]["blocks"].as_u64(), meta["backfill_rows"]["blocks"].as_u64()), (Some(2), Some(1)));
        assert!(!s.has_slots() && s.seq == 2);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_data_is_on_disk_before_the_segment_is_complete() {
        let root = tmp("sync-order");
        let (mut s, _) = Segments::open(&root, 2, 3600).unwrap();
        s.sync = check_then_sync;
        s.add_slot(7, vec![row("blocks", 7)]);
        s.add_slot(8, vec![row("blocks", 8)]);
        s.add_backfill(vec![row("blocks", 3)]);
        let dir = s.close(&json!({"pools": []}), serde_json::Map::new()).unwrap().unwrap();
        assert_eq!(SYNCED.load(std::sync::atomic::Ordering::SeqCst), 1);
        let meta: Value = serde_json::from_slice(&fs::read(dir.join("_segment.json")).unwrap()).unwrap();
        assert_eq!((meta["rows"]["blocks"].as_u64(), meta["backfill_rows"]["blocks"].as_u64()), (Some(2), Some(1)));
        // Every file the segment names is readable with its rows.
        use parquet::file::reader::{FileReader, SerializedFileReader};
        let rows = |f: &str| SerializedFileReader::new(fs::File::open(dir.join(f)).unwrap()).unwrap().metadata().file_metadata().num_rows();
        assert_eq!((rows("blocks/part-00000.parquet"), rows("backfill/blocks/part-00000.parquet")), (2, 1));
        sync_tree(&root).unwrap(); // the whole root syncs too (directories after their entries)
        fs::remove_dir_all(&root).unwrap();
    }
}
