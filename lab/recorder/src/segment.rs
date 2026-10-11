//! Rolling segments in the OF1 chunk layout, read by `paper.live.LiveFeed` and `paper.reconcile`:
//!
//!   <root>/segments/<seq:09d>/
//!     pump/<Event>/part-00000.parquet, pump_amm/<Event>/...   the extractor's event rows
//!     failed_txs/, anomalies/                                   the extractor's other tables
//!     blocks/        slot, parent_slot, block_time, block_height, executed_tx_count, entry_count, skipped
//!     arrivals/      per transaction: slot, tx_index, signature, received_unix_ns, created_unix_ns, stream
//!     vaults/        vault balance updates (account stream with a data slice), when subscribed
//!     pool_accounts/ PumpSwap pool accounts seen on the pool account stream, when subscribed
//!     watchlist.json the transaction filter of this segment (pools, wallets, vaults)
//!     _segment.json  written last (atomic rename): seq, first_slot, last_slot, complete, usage, ...
//!
//! Slots are only written once closed (confirmed, see `SlotBuffer`), so a segment holds whole
//! slots and every row of a slot is in one segment.

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

struct Open {
    first_slot: Option<u64>,
    last_slot: u64,
    started: Instant,
    tables: BTreeMap<Arc<str>, (Columns, Vec<Vec<Val>>)>,
    slots: u64,
}

impl Open {
    fn new() -> Self {
        Self { first_slot: None, last_slot: 0, started: Instant::now(), tables: BTreeMap::new(), slots: 0 }
    }

    fn add(&mut self, rows: Vec<Row>) {
        for r in rows {
            self.tables.entry(r.table.clone()).or_insert_with(|| (r.columns.clone(), Vec::new())).1.push(r.values);
        }
    }
}

pub struct Segments {
    root: PathBuf,
    pub seq: u64,
    open: Open,
    seg_slots: u64,
    seg_secs: u64,
}

fn write_json_atomic(path: &Path, v: &Value) -> Result<()> {
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
        Ok((Self { root: root.to_owned(), seq: next, open: Open::new(), seg_slots, seg_secs }, last_slot))
    }

    /// Add the rows of a closed slot.
    pub fn add_slot(&mut self, slot: u64, rows: Vec<Row>) {
        if self.open.first_slot.is_none() {
            self.open.first_slot = Some(slot);
        }
        self.open.last_slot = self.open.last_slot.max(slot);
        self.open.slots += 1;
        self.open.add(rows);
    }

    /// Add rows that are not gated by slot closing (vault and pool account updates).
    pub fn add_rows(&mut self, rows: Vec<Row>) {
        self.open.add(rows);
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
    /// `_segment.json`; `watchlist` becomes `watchlist.json`.
    pub fn close(&mut self, watchlist: &Value, meta: serde_json::Map<String, Value>) -> Result<Option<PathBuf>> {
        let Some(first) = self.open.first_slot else { return Ok(None) };
        let open = std::mem::replace(&mut self.open, Open::new());
        let dir = self.root.join("segments").join(format!("{:09}", self.seq));
        fs::create_dir_all(&dir)?;
        let mut rows = serde_json::Map::new();
        for (table, (cols, values)) in &open.tables {
            if values.is_empty() {
                continue;
            }
            write_parquet(&dir, table, 0, cols, values)?;
            rows.insert(table.to_string(), json!(values.len()));
        }
        write_json_atomic(&dir.join("watchlist.json"), watchlist)?;
        let mut m = serde_json::Map::new();
        m.insert("seq".into(), json!(self.seq));
        m.insert("first_slot".into(), json!(first));
        m.insert("last_slot".into(), json!(open.last_slot));
        m.insert("slots_closed".into(), json!(open.slots));
        m.insert("rows".into(), Value::Object(rows));
        m.insert("written_unix".into(), json!(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_secs()));
        m.extend(meta);
        m.insert("complete".into(), json!(true));
        write_json_atomic(&dir.join("_segment.json"), &Value::Object(m))?;
        fs::File::open(&dir)?.sync_all()?;
        self.seq += 1;
        Ok(Some(dir))
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
}
