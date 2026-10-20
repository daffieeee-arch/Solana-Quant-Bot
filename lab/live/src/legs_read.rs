//! The Parquet side of the legs reader (legs.rs): the chunk event tables into CurveLegs and PoolRows exactly as the
//! store's SQL builds its `curve` and `pool` rows (backtest/store.py `_curve_trades`, `_pool_trades`, `_pool_other`,
//! the SOL-market join; DuckDB's casts: research/m1-c1a-legs-spec-2026-10-19.md §2-§3), and `Replay`, the replay over
//! [from, to) that `lab-live legs` (`run`) and `lab-live signals` (signals.rs) share:
//!
//! 1. refused (`Replay::open`): the hold-out, the clean period, a range outside the snapshot's store period, one not
//!    covered by complete chunks, a chunk that is not the store's (its slots, the extractor build in its manifest, its
//!    h24), another farmer rule than the store's, and a range whose farmer windows reach before `from` without the
//!    store's history for exactly this replay (farm_hist.parquet, C1a-2);
//! 2. each emitted chunk's h24 from its own blocks, over the whole chunk, equal to the store's;
//! 3. the farmer history over [H0, from) from the snapshot (research/m1-c1a2-spec-2026-10-19.md), H0 the earliest
//!    slot a window of an emitted trade reaches (legs.rs `keeps`);
//! 4. chunk by chunk (`Replay::for_each_event`): its curve and pool rows read (admitted by the snapshot: its mints for
//!    curve rows, its SOL markets for pool rows), sorted, gated (no key ties), the farmer flags set, and the events
//!    handed out in chain order, typed (lab_core::legs::Event).
//!
//! **Segments** (M1 C4-1, research/m1-c4-plateau-spec-2026-10-19.md §3): `Replay::open_segments` reads the recorder's
//! segments instead of the chunks (the live form: `<root>/<seq>/{pump,pump_amm,blocks}` and `_segment.json`). Steps 1
//! and 3 are as above (no manifest: a segment is the recorder's, not the extractor's). Step 4 goes segment by segment
//! in seq order, each split at the store's chunk bounds into units; a unit takes its store chunk's h24 and index, so the
//! farmer flags follow the store-exact rule exactly as a chunk replay does, and only one segment's rows are held. The
//! block times are the segment's own. An incomplete segment is skipped and counted; a gap in [from, to) is refused. A
//! segment's `backfill/` (watchlist backfills: rows of earlier slots, LiveFeed's late-row semantics, M3's) is not read,
//! only counted per segment, so a measurement on segments is a lower bound for the live form.

use crate::legs::{check_ties, emit_slots, event_kind, event_line, farm_mem, flag_chunk, h24_of, keeps, prune_hist, FarmHists,
                  Keys, PoolRow, Summary, REVERSED};
use crate::memlog::Table;
use crate::rows::{chunks, Part};
use crate::snapshot::{FarmHistMeta, Snapshot};
use anyhow::{bail, Context, Result};
use arrow_array::{Array, BooleanArray, Int64Array, LargeStringArray, RecordBatch, StringArray, StringViewArray, UInt64Array};
use lab_core::mem::{self as lmem, Mem};
use lab_core::legs::{
    CurveLeg, CurveVariant, Event, Id, Order, PoolCtx, PoolKind, BOOST_BUY_DISC, DEFAULT_KEY, DIRECT, EXACT_OUT, HOLDOUT, NONE,
    POOL_EXACT_OUT, PUMP, PUMP_AMM, X0, X1,
};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::arrow::ProjectionMask;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

/// Epochs 1055-1063: the clean test period, locked (like the hold-out, never replayed here).
pub(crate) const CLEAN: (u64, u64) = (455_760_000, 459_648_000);

/// The snapshot's keys and what the feed admits: curve rows of its mints, pool rows of its SOL markets (with their
/// orientation). Interned first, so their ids are dense from 0.
///
/// M3-09b (follow.rs, `Admission::live`): LiveFeed's admission instead, which grows while it reads: `live_mints` (the
/// snapshot's tokens before the first live slot, then each CreateEvent's) and `live_pools` (every pool classed, SOL
/// markets or not). There the rows of mints and pools not known are counted per segment (`unknown`).
pub struct Admission {
    pub keys: Keys,
    is_mint: Vec<bool>,
    /// per id: 0 not known (the replay: not a SOL market), 1 normal, 2 reversed, 3 known but no SOL market (live only)
    pool: Vec<u8>,
    /// The live form's rows of keys not known (off in the replay).
    pub unknown: Unknown,
}

/// The pool class codes of `Admission::pool`.
pub const POOL_NORMAL: u8 = 1;
pub const POOL_REVERSED: u8 = 2;
pub const POOL_OTHER: u8 = 3;

/// M3-09b: the rows LiveFeed drops for a key it does not know, per key (None: a NULL key), while `on`: curve trades of
/// mints (`_unknown_mints`: `_curve_trades` ANTI JOIN live_mints), buys and sells (`_pool_trades`, boost-crank inner
/// buys excluded) and the boost, withdraw and deposit rows (`_pool_other`) of pools (ANTI JOIN live_pools: a pool of
/// another class is known). follow.rs takes them per segment.
#[derive(Default)]
pub struct Unknown {
    pub on: bool,
    pub mints: BTreeMap<Option<String>, u64>,
    pub trades: BTreeMap<Option<String>, u64>,
    pub other: BTreeMap<Option<String>, u64>,
}

/// The replay's own structures for `--mem-log` (M1 C4-2, memlog.rs), taken once per chunk (or segment unit) after its
/// rows are read and flagged, before its events: the interned keys (with the admission's tables), the 24 h farmer
/// histories (the (pool, trader) pairs, then their trades), the pool contexts and the legs buffer of that chunk; C4-5: the
/// shape of the two hash tables (interned_keys, farmer_hist).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ReplayMem {
    pub mems: Vec<Mem>,
    pub tables: Vec<Table>,
    /// Segments handed out so far (None on chunks).
    pub segments: Option<u64>,
}

/// A running total plus `flag_chunk`'s delta (the farmer histories' trades and bytes).
fn add_mem((trades, bytes): (usize, usize), (dt, db): (isize, isize)) -> (usize, usize) {
    (trades.checked_add_signed(dt).expect("farmer history trades below 0"), bytes.checked_add_signed(db).expect("farmer history bytes below 0"))
}

/// C4-4: prune the farmer histories at `bound` (`Replay::prune_farm`) once it moved by h24 / 24 (about an hour of slots)
/// since the last sweep (`pruned`: the last bound, sweeps, pairs dropped): a sweep walks every pair, so not at every unit.
/// A free function over the replay's fields, so the chunk loop (which borrows the chunk list) can call it.
fn sweep_farm(hist: &mut FarmHists, hist_mem: &mut (usize, usize), pruned: &mut (u64, u64, u64), bound: u64, h24: u64) {
    if bound < pruned.0.saturating_add((h24 / 24).max(1)) {
        return;
    }
    let (pairs, dt, db) = prune_hist(hist, bound);
    *hist_mem = add_mem(*hist_mem, (dt, db));
    *pruned = (bound, pruned.1 + 1, pruned.2 + pairs as u64);
}

/// O(1): the histories' trades and bytes are the replay's running total (`flag_chunk`'s deltas), never a walk over them;
/// the block-time tables (`blocks`: slots, bytes) are counted once at the open (constant for the run). `slot_buf` (M3-07's
/// one slot's events, reused): its capacity as the entries, the largest slot so far (a Vec never shrinks).
#[allow(clippy::too_many_arguments)]
fn replay_mem<C, P>(adm: &Admission, hist: &FarmHists, (trades, bytes): (usize, usize), blocks: (usize, usize),
                    ctx: &HashMap<Id, PoolCtx>, legs: (&Vec<C>, &Vec<P>), slot_buf: &Vec<Event>, segments: Option<u64>) -> ReplayMem {
    let (farm_bytes, farm_table) = farm_mem(hist);
    ReplayMem {
        mems: vec![
            Mem::new("block_times", blocks.0, blocks.1),
            Mem::new("interned_keys", adm.keys.len(), adm.keys.heap_bytes() + lmem::vec(&adm.is_mint) + lmem::vec(&adm.pool)),
            Mem::new("farmer_hist", hist.len(), farm_bytes),
            Mem::new("farmer_hist_trades", trades, bytes),
            Mem::new("pool_ctx", ctx.len(), lmem::map(ctx)),
            Mem::new("legs_buffer", legs.0.len() + legs.1.len(), lmem::vec(legs.0) + lmem::vec(legs.1)),
            Mem::new("slot_buf", slot_buf.capacity(), lmem::vec(slot_buf)),
        ],
        tables: vec![adm.keys.table(), farm_table],
        segments,
    }
}

impl Admission {
    pub fn from_snapshot(s: &Snapshot) -> Result<Self> {
        let mut keys = Keys::default();
        for m in s.tokens.keys() {
            keys.id(m);
        }
        for p in s.pools.keys() {
            keys.id(p);
        }
        let n = keys.len();
        let (mut is_mint, mut pool) = (vec![false; n], vec![0u8; n]);
        for m in s.tokens.keys() {
            is_mint[keys.get(m).expect("interned") as usize] = true;
        }
        for (k, p) in &s.pools {
            // The store's token side tests `orientation = 'normal'`; it is never NULL there (store.py:630).
            pool[keys.get(k).expect("interned") as usize] = match p.orientation.as_deref() {
                Some("normal") => 1,
                Some("reversed") => 2,
                o => bail!("pool {k}: orientation {o:?} (normal or reversed expected)"),
            };
        }
        Ok(Admission { keys, is_mint, pool, unknown: Unknown::default() })
    }

    /// M3-09b: LiveFeed's admission at its start (live.py `_load_snapshot`): the snapshot's tokens (`live_mints`) and every
    /// pool it classes (`live_pools`), the SOL markets with their orientation; the unknown rows counted from here on.
    pub fn live(s: &Snapshot) -> Result<Self> {
        let mut a = Admission { keys: Keys::default(), is_mint: Vec::new(), pool: Vec::new(), unknown: Unknown { on: true, ..Unknown::default() } };
        for m in s.tokens.keys() {
            a.learn_mint(m);
        }
        for (k, (cls, orient)) in &s.pool_class {
            let code = pool_class_code(cls.as_deref(), orient.as_deref()).with_context(|| format!("pool {k}"))?;
            a.learn_pool(k, code);
        }
        Ok(a)
    }

    /// A mint known from here on (`live_mints`).
    pub fn learn_mint(&mut self, k: &str) -> Id {
        let id = self.keys.id(k);
        let i = id as usize;
        if self.is_mint.len() <= i {
            self.is_mint.resize(i + 1, false);
        }
        self.is_mint[i] = true;
        id
    }

    pub fn known_mint(&self, k: &str) -> bool {
        self.mint(k).is_some()
    }

    /// A pool classed from here on (`live_pools`): POOL_NORMAL or POOL_REVERSED for a SOL market, POOL_OTHER else.
    pub fn learn_pool(&mut self, k: &str, code: u8) -> Id {
        let id = self.keys.id(k);
        let i = id as usize;
        if self.pool.len() <= i {
            self.pool.resize(i + 1, 0);
        }
        self.pool[i] = code;
        id
    }

    /// The pool is classed (any class).
    pub fn known_pool(&self, k: &str) -> bool {
        self.keys.get(k).is_some_and(|id| self.pool.get(id as usize).is_some_and(|&c| c != 0))
    }

    /// While `unknown.on`: a pool row dropped for its pool, counted when the pool is not classed at all.
    fn unknown_pool(&mut self, k: Option<&str>, trade: bool) {
        if !self.unknown.on || k.is_some_and(|k| self.known_pool(k)) {
            return;
        }
        let m = if trade { &mut self.unknown.trades } else { &mut self.unknown.other };
        *m.entry(k.map(str::to_owned)).or_default() += 1;
    }

    fn mint(&self, k: &str) -> Option<Id> {
        self.keys.get(k).filter(|&id| self.is_mint.get(id as usize) == Some(&true))
    }

    /// (id, reversed) of a SOL market.
    fn pool(&self, k: &str) -> Option<(Id, bool)> {
        let id = self.keys.get(k)?;
        match self.pool.get(id as usize) {
            Some(1) => Some((id, false)),
            Some(2) => Some((id, true)),
            _ => None,
        }
    }

    fn trader(&mut self, k: Option<&str>) -> Id {
        k.map_or(NONE, |k| self.keys.id(k))
    }
}

/// A pool's class code (`Admission::pool`) from its quote_class and orientation: a SOL market (sol or reversed) by its
/// orientation, which the store's token side reads (never NULL there, store.py:630), any other class POOL_OTHER.
pub fn pool_class_code(cls: Option<&str>, orient: Option<&str>) -> Result<u8> {
    if !cls.is_some_and(|c| crate::snapshot::SOL_MARKETS.contains(&c)) {
        return Ok(POOL_OTHER);
    }
    match orient {
        Some("normal") => Ok(POOL_NORMAL),
        Some("reversed") => Ok(POOL_REVERSED),
        o => bail!("a SOL market of orientation {o:?} (normal or reversed expected)"),
    }
}

/// One column of a batch, typed; `Absent` when the file has no such column (reads as NULL).
pub(crate) enum Col<'a> {
    U(&'a UInt64Array),
    I(&'a Int64Array),
    B(&'a BooleanArray),
    S(&'a StringArray),
    L(&'a LargeStringArray),
    V(&'a StringViewArray),
    Absent,
}

impl<'a> Col<'a> {
    pub(crate) fn of(batch: &'a RecordBatch, name: &str) -> Result<Self> {
        let Some(a) = batch.column_by_name(name) else { return Ok(Col::Absent) };
        let any = a.as_any();
        Ok(if let Some(x) = any.downcast_ref::<UInt64Array>() {
            Col::U(x)
        } else if let Some(x) = any.downcast_ref::<Int64Array>() {
            Col::I(x)
        } else if let Some(x) = any.downcast_ref::<BooleanArray>() {
            Col::B(x)
        } else if let Some(x) = any.downcast_ref::<StringArray>() {
            Col::S(x)
        } else if let Some(x) = any.downcast_ref::<LargeStringArray>() {
            Col::L(x)
        } else if let Some(x) = any.downcast_ref::<StringViewArray>() {
            Col::V(x)
        } else {
            bail!("column {name}: type {} (the extractor writes bool, u64, i64 and utf8 only)", a.data_type())
        })
    }

    pub(crate) fn null(&self, i: usize) -> bool {
        match self {
            Col::U(a) => a.is_null(i),
            Col::I(a) => a.is_null(i),
            Col::B(a) => a.is_null(i),
            Col::S(a) => a.is_null(i),
            Col::L(a) => a.is_null(i),
            Col::V(a) => a.is_null(i),
            Col::Absent => true,
        }
    }

    /// An unsigned integer column's value (a u64 as written; an i64 when it is not negative).
    pub(crate) fn u64(&self, i: usize) -> Result<Option<u64>> {
        if self.null(i) {
            return Ok(None);
        }
        Ok(Some(match self {
            Col::U(a) => a.value(i),
            Col::I(a) => u64::try_from(a.value(i)).context("a negative value where an unsigned one is read")?,
            _ => bail!("an integer column of another type"),
        }))
    }

    /// DuckDB's hard `::BIGINT` of the value: out of range is an error.
    pub(crate) fn bigint(&self, i: usize) -> Result<Option<i64>> {
        if self.null(i) {
            return Ok(None);
        }
        Ok(Some(match self {
            Col::U(a) => i64::try_from(a.value(i)).context("a value beyond BIGINT")?,
            Col::I(a) => a.value(i),
            _ => bail!("an integer column of another type"),
        }))
    }

    /// DuckDB's hard `::INTEGER`: out of range is an error.
    pub(crate) fn integer(&self, i: usize) -> Result<Option<u32>> {
        match self.bigint(i)? {
            None => Ok(None),
            Some(v) => {
                let v = i32::try_from(v).context("a value beyond INTEGER")?;
                Ok(Some(u32::try_from(v).context("a negative basis-point value")?))
            }
        }
    }

    pub(crate) fn bool(&self, i: usize) -> Result<Option<bool>> {
        if self.null(i) {
            return Ok(None);
        }
        match self {
            Col::B(a) => Ok(Some(a.value(i))),
            _ => bail!("a boolean column of another type"),
        }
    }

    pub(crate) fn str(&self, i: usize) -> Result<Option<&'a str>> {
        if self.null(i) {
            return Ok(None);
        }
        Ok(Some(match *self {
            Col::S(a) => a.value(i),
            Col::L(a) => a.value(i),
            Col::V(a) => a.value(i),
            _ => bail!("a string column of another type"),
        }))
    }
}

/// `TRY_CAST(x AS BIGINT)` of the pump_amm virtual quote (a u128 the extractor writes as a decimal string, or a
/// number): a decimal integer outside BIGINT reads as NULL; anything else is an error (the extractor writes none).
fn try_bigint(c: &Col, i: usize) -> Result<Option<i64>> {
    if c.null(i) {
        return Ok(None);
    }
    match c {
        Col::U(a) => Ok(i64::try_from(a.value(i)).ok()),
        Col::I(a) => Ok(Some(a.value(i))),
        _ => {
            let s = c.str(i)?.unwrap_or_default().trim();
            let digits = s.strip_prefix(['-', '+']).unwrap_or(s);
            if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
                bail!("a virtual quote that is no decimal integer: {s:?}");
            }
            Ok(s.parse::<i128>().ok().and_then(|v| i64::try_from(v).ok()))
        }
    }
}

/// An instruction argument of parent_ix_args (DuckDB `TRY_CAST(json_extract_string(args, '$.key') AS HUGEINT)`, every
/// mapped key a u64): a number or a decimal string; null, a missing key, a bool, an object or an array is None.
fn arg(args: Option<&Value>, key: &str) -> Result<Option<u64>> {
    let Some(v) = args.and_then(|a| a.get(key)) else { return Ok(None) };
    match v {
        Value::Number(n) => n.as_u64().map(Some).with_context(|| format!("argument {key} is no u64: {n}")),
        Value::String(s) => {
            let t = s.trim();
            let digits = t.strip_prefix(['-', '+']).unwrap_or(t);
            if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
                bail!("argument {key} is no integer: {s:?}");
            }
            let v: i128 = t.parse().with_context(|| format!("argument {key}: {s:?}"))?;
            Ok(Some(u64::try_from(v).with_context(|| format!("argument {key} beyond u64: {s:?}"))?))
        }
        _ => Ok(None),
    }
}

/// The row's chain order from its key columns (slot, tx_index, outer_ix, inner_ix at 0..4): a NULL is an error (the
/// store's flag join and the feed's sort need them).
pub(crate) fn order(c: &[Col], i: usize, slot: u64) -> Result<Order> {
    let key = |k: usize, what: &str| c[k].u64(i)?.with_context(|| format!("{what} is NULL at slot {slot}"));
    Ok(Order {
        slot,
        tx: u32::try_from(key(1, "tx_index")?).context("tx_index beyond u32")?,
        outer: u16::try_from(key(2, "outer_ix")?).context("outer_ix beyond u16")?,
        inner: u16::try_from(key(3, "inner_ix")?).context("inner_ix beyond u16")?,
    })
}

fn need<T>(v: Option<T>, what: &str, o: &Order) -> Result<T> {
    v.with_context(|| format!("{what} is NULL at {o:?} (Python fails on it)"))
}

/// Kept rows and drop reasons per table (`kept`, `dropped, <reason>`), rows of [from, to) only.
#[derive(Default)]
pub struct Counts {
    rows: BTreeMap<(&'static str, &'static str), u64>,
}

impl Counts {
    fn add(&mut self, table: &'static str, what: &'static str) {
        *self.rows.entry((table, what)).or_default() += 1;
    }

    /// `{"<table>: <what>": count}`
    pub fn json(&self) -> Value {
        Value::Object(self.rows.iter().map(|((t, w), n)| (format!("{t}: {w}"), json!(n))).collect())
    }
}

/// A BIGINT read as the unsigned amount the extractor wrote (never negative).
fn unsigned(v: Option<i64>, what: &str) -> Result<Option<u64>> {
    v.map(|x| u64::try_from(x).with_context(|| format!("{what} is negative"))).transpose()
}

/// Every row of `<dir>/<table>/*.parquet` (sorted), with only `cols` read; `f` gets the batch's columns in that order
/// and the row. A column of `required` that none of the table's files has is an error (DuckDB's binder fails there).
pub(crate) fn each_row(dir: &Path, table: &str, cols: &[&str], required: &[&str], mut f: impl FnMut(&[Col], usize) -> Result<()>) -> Result<u64> {
    let tdir = dir.join(table);
    let Ok(entries) = fs::read_dir(&tdir) else { return Ok(0) }; // no files: the table's part of the SQL is skipped
    let mut files: Vec<PathBuf> = entries.filter_map(|e| e.ok().map(|e| e.path())).filter(|p| p.extension().is_some_and(|x| x == "parquet")).collect();
    files.sort();
    let (mut seen, mut n) = (vec![false; cols.len()], 0u64);
    for path in &files {
        n += each_file_row(path, cols, &mut seen, &mut f)?;
    }
    if n > 0 {
        if let Some(k) = required.iter().find(|r| !cols.iter().zip(&seen).any(|(c, s)| c == *r && *s)) {
            bail!("{}: no file has column {k}", tdir.display());
        }
    }
    Ok(n)
}

/// Every row of one Parquet file with only `cols` read (`seen[k]`: the file has `cols[k]`); its row count.
fn each_file_row<F: FnMut(&[Col], usize) -> Result<()>>(path: &Path, cols: &[&str], seen: &mut [bool], f: &mut F) -> Result<u64> {
    let file = fs::File::open(path).with_context(|| format!("open {}", path.display()))?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(file)?;
    let names: Vec<String> = builder.schema().fields().iter().map(|f| f.name().clone()).collect();
    let roots: Vec<usize> = (0..names.len()).filter(|&i| cols.contains(&names[i].as_str())).collect();
    for (k, c) in cols.iter().enumerate() {
        seen[k] |= names.iter().any(|x| x.as_str() == *c);
    }
    let mask = ProjectionMask::roots(builder.parquet_schema(), roots);
    let mut n = 0u64;
    for batch in builder.with_projection(mask).build()? {
        let batch = batch.with_context(|| format!("read {}", path.display()))?;
        let c = cols.iter().map(|name| Col::of(&batch, name)).collect::<Result<Vec<_>>>()?;
        for i in 0..batch.num_rows() {
            f(&c, i).with_context(|| format!("{}", path.display()))?;
            n += 1;
        }
    }
    Ok(n)
}

/// The farmer history of the snapshot (farm_hist.parquet, paper/snapshot_export.py): the store's buys and sells over
/// [m.lo, m.hi) of the keys that trade in the replay, in strict chain order, each a SOL market's with its token side.
/// The rows from `h0` on join `hist` (the rest no window reaches); returns (rows, rows kept).
fn read_farm_hist(path: &Path, adm: &mut Admission, m: &FarmHistMeta, h0: u64, hist: &mut FarmHists) -> Result<(u64, u64)> {
    const COLS: [&str; 8] = ["slot", "tx_index", "outer_ix", "inner_ix", "pool", "trader", "token_buy", "token_amount"];
    let (mut prev, mut kept, mut seen) = (None::<Order>, 0u64, [false; 8]);
    let n = each_file_row(path, &COLS, &mut seen, &mut |c: &[Col], i: usize| {
        let slot = c[0].u64(i)?.context("a history row without its slot")?;
        let o = order(c, i, slot)?;
        if prev.is_some_and(|p| o <= p) {
            bail!("farm_hist.parquet is not in strict chain order at {o:?}");
        }
        prev = Some(o);
        if slot < m.lo || slot >= m.hi {
            bail!("a history row at slot {slot}, outside the history's [{}, {})", m.lo, m.hi);
        }
        let (pool, _) = c[4].str(i)?.and_then(|p| adm.pool(p))
            .with_context(|| format!("a history row at {o:?} of a pool that is no SOL market of the snapshot"))?;
        let buy = need(c[6].bool(i)?, "token_buy", &o)?;
        let amount = need(c[7].bigint(i)?, "token_amount", &o)?;
        if amount < 0 {
            bail!("a negative token_amount at {o:?} (the store's gross is its sum)");
        }
        if slot >= h0 {
            let trader = adm.trader(c[5].str(i)?);
            hist.entry((pool, trader)).or_default().add(slot, if buy { amount } else { -amount });
            kept += 1;
        }
        Ok(())
    })?;
    if let Some((k, _)) = COLS.iter().zip(seen).find(|(_, s)| !s) {
        bail!("{}: no column {k}", path.display());
    }
    Ok((n, kept))
}

/// The extractor build (`extractor_sha256`) a chunk's manifest names.
fn manifest_sha(p: &Part) -> Result<Option<String>> {
    let path = p.dir.join("_manifest.json");
    let m: Value = serde_json::from_slice(&fs::read(&path).with_context(|| format!("read {}", path.display()))?)
        .with_context(|| format!("{}", path.display()))?;
    Ok(m["extractor_sha256"].as_str().map(str::to_owned))
}

/// A chunk's block times (its non-skipped blocks, one per slot) and h24.
pub struct Blocks {
    lo: u64,
    time: Vec<Option<i64>>,
    pub h24: u64,
}

impl Blocks {
    /// The memory log (C4-2/C4-3, LAB review's nit on #393): the slots held and the table's bytes.
    pub fn mem(&self) -> (usize, usize) {
        (self.time.len(), lmem::vec(&self.time))
    }

    pub fn read(part: &Part) -> Result<Self> {
        let mut by_slot: BTreeMap<u64, Option<i64>> = BTreeMap::new();
        each_row(&part.dir, "blocks", &["slot", "block_time", "skipped"], &["slot", "block_time", "skipped"], |c, i| {
            if c[2].bool(i)? != Some(false) {
                return Ok(()); // skipped, or NULL: not in s.blocks (`WHERE NOT skipped`)
            }
            let slot = c[0].u64(i)?.context("a block without its slot")?;
            let t = c[1].bigint(i)?;
            match by_slot.get(&slot) {
                Some(Some(old)) if t.is_some_and(|t| t != *old) => bail!("slot {slot} has two block times"),
                Some(Some(_)) => {}
                _ => {
                    by_slot.insert(slot, t);
                }
            }
            Ok(())
        })?;
        let list: Vec<(u64, Option<i64>)> = by_slot.iter().map(|(s, t)| (*s, *t)).collect();
        let h24 = h24_of(&list);
        let mut time = vec![None; usize::try_from(part.hi - part.lo).context("a chunk's slots")?];
        for (s, t) in list {
            if s < part.lo || s >= part.hi {
                bail!("{}: block of slot {s} outside the chunk", part.dir.display());
            }
            time[(s - part.lo) as usize] = t;
        }
        Ok(Blocks { lo: part.lo, time, h24 })
    }

    pub fn at(&self, slot: u64) -> Option<i64> {
        slot.checked_sub(self.lo).and_then(|i| self.time.get(i as usize).copied().flatten())
    }
}

const TRADE_COLS: [&str; 23] = [
    "slot", "tx_index", "outer_ix", "inner_ix", "mint", "is_buy", "token_amount", "quote_amount", "sol_amount", "fee", "creator_fee",
    "parent_ix_disc", "fee_basis_points", "creator_fee_basis_points", "virtual_token_reserves", "virtual_quote_reserves",
    "virtual_sol_reserves", "real_token_reserves", "real_quote_reserves", "real_sol_reserves", "user", "outer_program",
    "parent_ix_args",
];

/// The chunk's curve trades of the snapshot's mints with a slot in [from, to) (store.py `_curve_trades`).
pub fn read_curve(part: &Part, adm: &mut Admission, (from, to): (u64, u64), counts: &mut Counts) -> Result<Vec<CurveLeg>> {
    let mut out = Vec::new();
    let t = "pump/TradeEvent";
    each_row(&part.dir, t, &TRADE_COLS, &TRADE_COLS[..22], |c, i| {
        let slot = c[0].u64(i)?.context("a trade without its slot")?;
        if slot < part.lo || slot >= part.hi {
            bail!("a trade at slot {slot} outside its chunk");
        }
        if slot < from || slot >= to {
            return Ok(());
        }
        let key = c[4].str(i)?;
        let Some(mint) = key.and_then(|m| adm.mint(m)) else {
            if adm.unknown.on {
                *adm.unknown.mints.entry(key.map(str::to_owned)).or_default() += 1;
            }
            counts.add(t, "dropped, mint not in the store");
            return Ok(());
        };
        let o = order(c, i, slot)?;
        let q = c[7].u64(i)?.or(c[8].u64(i)?);
        let fee = need(c[9].u64(i)?, "fee", &o)?.checked_add(need(c[10].u64(i)?, "creator_fee", &o)?).context("fee + creator_fee")?;
        let args: Option<Value> = match c[22].str(i)? {
            Some(s) => Some(serde_json::from_str(s).with_context(|| format!("parent_ix_args at {o:?}"))?),
            None => None,
        };
        let a = args.as_ref();
        let budget = match arg(a, "spendable_sol_in")? {
            Some(v) => Some(v),
            None => arg(a, "spendable_quote_in")?,
        };
        let vals = [budget, arg(a, "max_sol_cost")?, arg(a, "min_tokens_out")?, arg(a, "min_sol_output")?];
        let mut leg = CurveLeg {
            order: o,
            mint,
            trader: NONE,
            t: need(c[6].u64(i)?, "token_amount", &o)?,
            q: need(q, "quote_amount and sol_amount", &o)?,
            fee,
            vt: need(c[14].u64(i)?, "virtual_token_reserves", &o)?,
            vq: need(c[15].u64(i)?.or(c[16].u64(i)?), "virtual quote reserves", &o)?,
            rt: need(c[17].u64(i)?, "real_token_reserves", &o)?,
            rq: need(c[18].u64(i)?.or(c[19].u64(i)?), "real quote reserves", &o)?,
            args: vals.map(|v| v.unwrap_or(0)),
            fee_bps: need(c[12].integer(i)?, "fee_basis_points", &o)?,
            creator_bps: need(c[13].integer(i)?, "creator_fee_basis_points", &o)?,
            args_set: vals.iter().enumerate().fold(0, |m, (k, v)| if v.is_some() { m | (1 << k) } else { m }),
            variant: CurveVariant::from_disc(c[11].str(i)?),
            is_buy: need(c[5].bool(i)?, "is_buy", &o)?,
            direct: c[21].str(i)? == Some(PUMP),
        };
        leg.trader = adm.trader(c[20].str(i)?);
        counts.add(t, "kept");
        out.push(leg);
        Ok(())
    })?;
    Ok(out)
}

const BUY_SELL_COLS: [&str; 26] = [
    "slot", "tx_index", "outer_ix", "inner_ix", "pool", "user", "outer_program", "parent_ix_disc", "pool_base_token_reserves",
    "pool_quote_token_reserves", "lp_fee_basis_points", "protocol_fee_basis_points", "coin_creator", "coin_creator_fee_basis_points",
    "virtual_quote_reserves", "cashback_fee_basis_points",
    // buy: base_amount_out, quote_amount_in_with_lp_fee, user_quote_amount_in, quote_amount_in, max_quote_amount_in,
    //      min_base_amount_out; sell: base_amount_in, quote_amount_out_without_lp_fee, user_quote_amount_out,
    //      min_quote_amount_out (the last four names per kind, from index 16)
    "", "", "", "", "", "", "", "", "", "",
];

/// The chunk's pool rows of the snapshot's SOL markets with a slot in [lo, hi) (store.py `_pool_trades`, `_pool_other`
/// and the SOL-market join).
pub fn read_pool(part: &Part, adm: &mut Admission, (lo, hi): (u64, u64), counts: &mut Counts) -> Result<Vec<PoolRow>> {
    let mut out = Vec::new();
    for (kind, table) in [(PoolKind::Buy, "pump_amm/BuyEvent"), (PoolKind::Sell, "pump_amm/SellEvent")] {
        let mut cols = BUY_SELL_COLS;
        let specific: [&str; 6] = if kind == PoolKind::Buy {
            ["base_amount_out", "quote_amount_in_with_lp_fee", "user_quote_amount_in", "quote_amount_in", "max_quote_amount_in", "min_base_amount_out"]
        } else {
            ["base_amount_in", "quote_amount_out_without_lp_fee", "user_quote_amount_out", "min_quote_amount_out", "", ""]
        };
        cols[16..22].copy_from_slice(&specific);
        let used: Vec<&str> = cols.iter().copied().filter(|c| !c.is_empty()).collect();
        let required: Vec<&str> = used.iter().copied().filter(|c| !matches!(*c, "virtual_quote_reserves" | "cashback_fee_basis_points")).collect();
        each_row(&part.dir, table, &used, &required, |c, i| {
            let slot = c[0].u64(i)?.context("a pool event without its slot")?;
            if slot < part.lo || slot >= part.hi {
                bail!("a pool event at slot {slot} outside its chunk");
            }
            if slot < lo || slot >= hi {
                return Ok(());
            }
            let disc = c[7].str(i)?;
            if kind == PoolKind::Buy && disc == Some(BOOST_BUY_DISC) {
                counts.add(table, "dropped, boost-crank inner buy");
                return Ok(());
            }
            let key = c[4].str(i)?;
            let Some((pool, reversed)) = key.and_then(|p| adm.pool(p)) else {
                adm.unknown_pool(key, true);
                counts.add(table, "dropped, not a SOL market of the store");
                return Ok(());
            };
            let o = order(c, i, slot)?;
            let creator_bps = if c[12].str(i)? == Some(DEFAULT_KEY) { 0 } else { c[13].integer(i)?.unwrap_or(0) };
            let cashback = match c[15].u64(i)? {
                Some(v) => u32::try_from(i32::try_from(v).unwrap_or(0)).unwrap_or(0), // TRY_CAST: beyond INTEGER is NULL, then 0
                None => 0,
            };
            let (base, e_delta, quote_user, limit_quote, limit_base) = if kind == PoolKind::Buy {
                let exact_out = disc.is_some_and(|d| POOL_EXACT_OUT.contains(&d));
                (c[16].bigint(i)?, c[17].bigint(i)?, if exact_out { c[18].bigint(i)? } else { c[19].bigint(i)? }, c[20].u64(i)?, c[21].u64(i)?)
            } else {
                let out = c[17].bigint(i)?;
                (c[16].bigint(i)?, out.map(|v| -v), c[18].bigint(i)?, c[19].u64(i)?, None)
            };
            let mut flags = if reversed { REVERSED } else { 0 };
            if disc.is_some_and(|d| POOL_EXACT_OUT.contains(&d)) {
                flags |= EXACT_OUT;
            }
            if c[6].str(i)? == Some(PUMP_AMM) {
                flags |= DIRECT;
            }
            if limit_quote.is_some() {
                flags |= X0;
            }
            if limit_base.is_some() {
                flags |= X1;
            }
            let row = PoolRow {
                order: o,
                pool,
                trader: adm.trader(c[5].str(i)?),
                b: need(c[8].bigint(i)?, "pool_base_token_reserves", &o)?,
                vault: need(c[9].bigint(i)?, "pool_quote_token_reserves", &o)?,
                vq: try_bigint(&c[14], i)?.unwrap_or(0),
                base: need(base, "base amount", &o)?,
                e_delta: need(e_delta, "quote amount", &o)?,
                quote_user: need(quote_user, "user quote amount", &o)?,
                x: [limit_quote.unwrap_or(0), limit_base.unwrap_or(0)],
                bps: [c[10].integer(i)?.unwrap_or(0), c[11].integer(i)?.unwrap_or(0), creator_bps, cashback],
                kind,
                flags,
            };
            counts.add(table, "kept");
            out.push(row);
            Ok(())
        })?;
    }
    let boost = "pump_amm/BoostBuyAndBurnEvent";
    let bcols = ["slot", "tx_index", "outer_ix", "inner_ix", "pool", "authority", "base_reserves_after", "base_amount_burned",
                 "real_quote_reserves_after", "quote_amount_in_used", "virtual_quote_reserves", "boost_vault_remaining"];
    each_row(&part.dir, boost, &bcols, &bcols, |c, i| {
        let slot = c[0].u64(i)?.context("a boost without its slot")?;
        if slot < part.lo || slot >= part.hi {
            bail!("a boost at slot {slot} outside its chunk");
        }
        if slot < lo || slot >= hi {
            return Ok(());
        }
        let key = c[4].str(i)?;
        let Some((pool, reversed)) = key.and_then(|p| adm.pool(p)) else {
            adm.unknown_pool(key, false);
            counts.add(boost, "dropped, not a SOL market of the store");
            return Ok(());
        };
        let o = order(c, i, slot)?;
        let after = need(c[6].u64(i)?, "base_reserves_after", &o)?;
        let burned = need(c[7].u64(i)?, "base_amount_burned", &o)?;
        let used = need(c[9].u64(i)?, "quote_amount_in_used", &o)?;
        let real = need(c[8].u64(i)?, "real_quote_reserves_after", &o)?;
        let to_i64 = |v: u64, what: &str| i64::try_from(v).with_context(|| format!("{what} beyond BIGINT at {o:?}"));
        let left = unsigned(c[11].bigint(i)?, "boost_vault_remaining")?;
        let row = PoolRow {
            order: o,
            pool,
            trader: adm.trader(c[5].str(i)?),
            b: to_i64(after.checked_add(burned).context("base reserves + burned overflow")?, "b")?,
            vault: to_i64(real.checked_sub(used).with_context(|| format!("boost vault underflow at {o:?}"))?, "vault")?,
            vq: try_bigint(&c[10], i)?.unwrap_or(0),
            base: to_i64(burned, "base_amount_burned")?,
            e_delta: to_i64(used, "quote_amount_in_used")?,
            quote_user: to_i64(used, "quote_amount_in_used")?,
            x: [left.unwrap_or(0), 0],
            bps: [0; 4],
            kind: PoolKind::Boost,
            flags: (if reversed { REVERSED } else { 0 }) | if left.is_some() { X0 } else { 0 },
        };
        counts.add(boost, "kept");
        out.push(row);
        Ok(())
    })?;
    for (kind, table, base_c, quote_c, lp_c) in [
        (PoolKind::Withdraw, "pump_amm/WithdrawEvent", "base_amount_out", "quote_amount_out", "lp_token_amount_in"),
        (PoolKind::Deposit, "pump_amm/DepositEvent", "base_amount_in", "quote_amount_in", "lp_token_amount_out"),
    ] {
        let cols = ["slot", "tx_index", "outer_ix", "inner_ix", "pool", "user", "outer_program", "pool_base_token_reserves",
                    "pool_quote_token_reserves", base_c, quote_c, lp_c, "lp_mint_supply"];
        each_row(&part.dir, table, &cols, &cols, |c, i| {
            let slot = c[0].u64(i)?.context("a pool event without its slot")?;
            if slot < part.lo || slot >= part.hi {
                bail!("a pool event at slot {slot} outside its chunk");
            }
            if slot < lo || slot >= hi {
                return Ok(());
            }
            let key = c[4].str(i)?;
            let Some((pool, reversed)) = key.and_then(|p| adm.pool(p)) else {
                adm.unknown_pool(key, false);
                counts.add(table, "dropped, not a SOL market of the store");
                return Ok(());
            };
            let o = order(c, i, slot)?;
            let (lp, supply) = (unsigned(c[11].bigint(i)?, lp_c)?, unsigned(c[12].bigint(i)?, "lp_mint_supply")?);
            let mut flags = if reversed { REVERSED } else { 0 };
            if c[6].str(i)? == Some(PUMP_AMM) {
                flags |= DIRECT;
            }
            if lp.is_some() {
                flags |= X0;
            }
            if supply.is_some() {
                flags |= X1;
            }
            let row = PoolRow {
                order: o,
                pool,
                trader: adm.trader(c[5].str(i)?),
                b: need(c[7].bigint(i)?, "pool_base_token_reserves", &o)?,
                vault: need(c[8].bigint(i)?, "pool_quote_token_reserves", &o)?,
                vq: 0,
                base: need(c[9].bigint(i)?, "base amount", &o)?,
                e_delta: 0,
                quote_user: need(c[10].bigint(i)?, "quote amount", &o)?,
                x: [lp.unwrap_or(0), supply.unwrap_or(0)],
                bps: [0; 4],
                kind,
                flags,
            };
            counts.add(table, "kept");
            out.push(row);
            Ok(())
        })?;
    }
    Ok(out)
}

pub struct LegsArgs {
    pub chunks_root: PathBuf,
    pub snapshot: PathBuf,
    pub from: u64,
    pub to: u64,
    pub window: u64,
    pub summary: PathBuf,
    pub out: Option<PathBuf>,
    /// with `out`: only the lines of slots from here on (a window that differs, after a run from the store's start)
    pub out_from: u64,
}

/// A recorder segment (lab/recorder/src/segment.rs): its seq, its slots [first_slot, last_slot + 1) as a Part, the rows
/// of its tables and of its `backfill/` (from the Parquet footers).
pub struct Segment {
    pub seq: u64,
    pub part: Part,
    pub rows: u64,
    pub backfill_rows: u64,
}

/// The Parquet rows under `dir`, every file of every subdirectory, from the footers.
fn parquet_rows(dir: &Path) -> Result<u64> {
    let Ok(entries) = fs::read_dir(dir) else { return Ok(0) };
    let mut n = 0u64;
    for e in entries {
        let path = e?.path();
        if path.is_dir() {
            n += parquet_rows(&path)?;
        } else if path.extension().is_some_and(|x| x == "parquet") {
            let file = fs::File::open(&path).with_context(|| format!("open {}", path.display()))?;
            let rows = ParquetRecordBatchReaderBuilder::try_new(file)?.metadata().file_metadata().num_rows();
            n += u64::try_from(rows).with_context(|| format!("{}: {rows} rows", path.display()))?;
        }
    }
    Ok(n)
}

/// The complete segments under `root` by seq (their `_segment.json` says `complete`), and the seqs of the incomplete
/// ones (skipped, as LiveFeed does).
pub fn segments(root: &Path) -> Result<(Vec<Segment>, Vec<u64>)> {
    let (mut out, mut incomplete) = (Vec::new(), Vec::new());
    for e in fs::read_dir(root).with_context(|| format!("read {}", root.display()))? {
        let dir = e?.path();
        let Ok(raw) = fs::read(dir.join("_segment.json")) else { continue };
        let m: Value = serde_json::from_slice(&raw).with_context(|| format!("{}/_segment.json", dir.display()))?;
        let (Some(seq), Some(first), Some(last)) = (m["seq"].as_u64(), m["first_slot"].as_u64(), m["last_slot"].as_u64()) else {
            bail!("{}/_segment.json: no seq, first_slot or last_slot", dir.display());
        };
        if m["complete"] != true {
            incomplete.push(seq);
            continue;
        }
        let rows = parquet_rows(&dir.join("pump"))? + parquet_rows(&dir.join("pump_amm"))?;
        let backfill_rows = parquet_rows(&dir.join("backfill"))?;
        out.push(Segment { seq, part: Part { lo: first, hi: last + 1, dir }, rows, backfill_rows });
    }
    out.sort_by_key(|s| s.seq);
    incomplete.sort_unstable();
    for w in out.windows(2) {
        if w[0].seq == w[1].seq {
            bail!("two segments with seq {}", w[0].seq);
        }
    }
    Ok((out, incomplete))
}

/// A segment's part in one store chunk: the slots [lo, hi), the store chunk's index among the replay's chunks and h24.
struct Unit {
    seg: usize,
    lo: u64,
    hi: u64,
    chunk: u32,
    h24: u64,
}

/// What a replay on segments reads: the segments in [from, to), their block times, their units, the incomplete ones
/// skipped, the store chunks' bounds and h24.
struct SegSource {
    segs: Vec<Segment>,
    blocks: Vec<Blocks>,
    units: Vec<Unit>,
    incomplete: Vec<u64>,
    chunks: Vec<(u64, u64, u64)>,
}

/// The chunks covering [lo, hi) without a gap, in slot order; an error otherwise.
fn covering(parts: &[Part], lo: u64, hi: u64, what: &str) -> Result<Vec<Part>> {
    let sel: Vec<Part> = parts.iter().filter(|p| p.lo < hi && p.hi > lo).cloned().collect();
    let mut at = lo;
    for (k, p) in sel.iter().enumerate() {
        if k > 0 && p.lo < at {
            bail!("{what}: chunks {} and {} overlap", sel[k - 1].dir.display(), p.dir.display());
        }
        if p.lo > at {
            bail!("{what}: no complete chunk for slots [{at}, {})", p.lo);
        }
        at = p.hi;
    }
    if at < hi {
        bail!("{what}: no complete chunk for slots [{at}, {hi})");
    }
    Ok(sel)
}

/// A replay over [from, to), opened and checked (steps 1-3 of the module doc), that hands out its events once
/// (step 4). Between the two it holds the snapshot's keys (`adm`), each emitted chunk's blocks and the farmer history.
pub struct Replay {
    pub from: u64,
    pub to: u64,
    /// The snapshot's admission and every key interned so far (traders as the chunks are read).
    pub adm: Admission,
    emit: Vec<Part>,
    blocks: Vec<Blocks>,
    keep: Vec<u64>,
    pub h0: u64,
    hist: FarmHists,
    /// The farmer history read (null without one), for the summary.
    farm: Value,
    pub counts: Counts,
    h24s: Vec<Value>,
    done: bool,
    /// Some: a replay on segments (`open_segments`); `emit` and `blocks` are then empty.
    seg: Option<SegSource>,
    /// `--mem-log` (C4-2): take the replay's structures per chunk or segment unit for `for_each_event_mem`; off, they are
    /// not counted.
    pub mem_log: bool,
    /// The farmer histories' trades and bytes (`FarmHist::mem`): summed once at the open, then `flag_chunk`'s deltas.
    hist_mem: (usize, usize),
    /// With `mem_log`: the replay's structures after the last chunk or unit handed out (the end line's).
    last_mem: ReplayMem,
    /// M1 C4-4: drop the farmer histories no later window reads (`legs::prune_hist`), before a chunk or unit once the
    /// bound moved by about an hour of slots; off with `--keep-farmer-history` (the A/B: the same flags either way).
    pub prune_farm: bool,
    /// (the last bound pruned at, sweeps, pairs dropped)
    pruned: (u64, u64, u64),
    /// The block-time tables of every chunk or segment (`Blocks::mem`): slots and bytes, loaded at the open, constant.
    blocks_mem: (usize, usize),
}

/// Where a replay's rows come from.
enum Source<'a> {
    Chunks(&'a Path),
    Segments(&'a Path),
}

impl Replay {
    /// Steps 1-3 of the module doc. `with_snapshot` sees the snapshot and the admission (its keys: the mints, then the
    /// pools) after the checks of the snapshot and before the snapshot is dropped and the farmer history interns its
    /// traders: `lab-live signals` builds its universe there. `lab-live legs` passes a no-op, so its keys are as in C1a.
    pub fn open<T>(
        chunks_root: &Path,
        snapshot: &Path,
        from: u64,
        to: u64,
        with_snapshot: impl FnOnce(&Snapshot, &mut Admission) -> Result<T>,
    ) -> Result<(Self, T)> {
        Self::open_from(Source::Chunks(chunks_root), snapshot, from, to, with_snapshot)
    }

    /// As `open`, on the recorder's segments under `segments_root` (see the module doc).
    pub fn open_segments<T>(
        segments_root: &Path,
        snapshot: &Path,
        from: u64,
        to: u64,
        with_snapshot: impl FnOnce(&Snapshot, &mut Admission) -> Result<T>,
    ) -> Result<(Self, T)> {
        Self::open_from(Source::Segments(segments_root), snapshot, from, to, with_snapshot)
    }

    fn open_from<T>(
        source: Source,
        snapshot: &Path,
        from: u64,
        to: u64,
        with_snapshot: impl FnOnce(&Snapshot, &mut Admission) -> Result<T>,
    ) -> Result<(Self, T)> {
        if from >= to {
            bail!("--from must be below --to");
        }
        if from < HOLDOUT.1 && to > HOLDOUT.0 {
            bail!("[{from}, {to}) overlaps the hold-out [{}, {}): it is never read", HOLDOUT.0, HOLDOUT.1);
        }
        if from < CLEAN.1 && to > CLEAN.0 {
            bail!("[{from}, {to}) overlaps the clean period [{}, {}) (epochs 1055-1063): it stays locked", CLEAN.0, CLEAN.1);
        }
        let mut snap = Snapshot::load_universe(snapshot)?;
        let (store_lo, store_hi) = snap.store_slots.context("the snapshot's meta.json has no store_slots")?;
        if from < store_lo || to > store_hi {
            bail!("[{from}, {to}) is outside the snapshot's store period [{store_lo}, {store_hi}): no ReplayFeed reference there");
        }
        if snap.version < 2 || snap.chunks.is_empty() {
            bail!("the snapshot (version {}) has no chunk_list: export it again with paper.snapshot_export (snapshot_version 2)", snap.version);
        }
        // the rule FarmHist computes: n >= 2 and 20 |net| < gross over a day of slots
        if snap.farmer != json!({"ratio": 0.05, "min_trades": 2, "window_ms": 86_400_000}) {
            bail!("the store's farmer rule {} is not the one lab-live computes (ratio 0.05, 2 trades, a day)", snap.farmer);
        }
        let store_chunks = std::mem::take(&mut snap.chunks);
        let farm_meta = snap.farm_hist;
        let mut adm = Admission::from_snapshot(&snap)?;
        let with = with_snapshot(&snap, &mut adm)?;
        drop(snap);
        let (emit, blocks, starts, seg) = match source {
            Source::Chunks(chunks_root) => {
                let parts = chunks(chunks_root)?;
                let emit = covering(&parts, from, to, "the range")?;
                // The store's own chunks: their slots, the extractor build that wrote them, and their h24 (review on #369).
                let mut blocks = Vec::new();
                for p in &emit {
                    let c = store_chunks.iter().find(|c| (c.lo, c.hi) == (p.lo, p.hi))
                        .with_context(|| format!("chunk {} ([{}, {})) is not one of the store's chunks", p.dir.display(), p.lo, p.hi))?;
                    let want = c.extractor_sha256.as_deref().with_context(|| format!("the store names no extractor build for chunk {}-{}", c.lo, c.hi))?;
                    let sha = manifest_sha(p)?;
                    if sha.as_deref() != Some(want) {
                        bail!("chunk {}: extractor_sha256 {sha:?}, the store was built from {want}", p.dir.display());
                    }
                    let bl = Blocks::read(p)?;
                    if bl.h24 != c.h24 {
                        bail!("chunk {}: h24 {} from its blocks, {} in the store", p.dir.display(), bl.h24, c.h24);
                    }
                    blocks.push(bl);
                }
                let starts: Vec<(u64, u64)> = emit.iter().zip(&blocks).map(|(p, b)| (p.lo.max(from), b.h24)).collect();
                (emit, blocks, starts, None)
            }
            Source::Segments(root) => {
                // The store's chunks over [from, to), without a gap: each unit takes its chunk's h24 and index.
                let store_parts: Vec<Part> = store_chunks.iter().map(|c| Part { lo: c.lo, hi: c.hi, dir: PathBuf::new() }).collect();
                let sel = covering(&store_parts, from, to, "the store's chunks")?;
                let chunk_h24: Vec<(u64, u64, u64)> = sel.iter()
                    .map(|p| (p.lo, p.hi, store_chunks.iter().find(|c| (c.lo, c.hi) == (p.lo, p.hi)).map_or(0, |c| c.h24)))
                    .collect();
                let (all, incomplete) = segments(root)?;
                let segs: Vec<Segment> = all.into_iter().filter(|s| s.part.lo < to && s.part.hi > from).collect();
                let mut units = Vec::new();
                let mut at = from;
                for (si, s) in segs.iter().enumerate() {
                    let (lo, hi) = (s.part.lo.max(from), s.part.hi.min(to));
                    if lo > at {
                        bail!("the range: no complete segment for slots [{at}, {lo}) (incomplete segments skipped: {incomplete:?})");
                    }
                    if lo < at {
                        bail!("segment {} ([{}, {})) overlaps the one before it", s.seq, s.part.lo, s.part.hi);
                    }
                    for (ci, &(clo, chi, h24)) in (0u32..).zip(&chunk_h24) {
                        let (a, b) = (lo.max(clo), hi.min(chi));
                        if a < b {
                            units.push(Unit { seg: si, lo: a, hi: b, chunk: ci, h24 });
                        }
                    }
                    at = hi;
                }
                if at < to {
                    bail!("the range: no complete segment for slots [{at}, {to}) (incomplete segments skipped: {incomplete:?})");
                }
                let seg_blocks = segs.iter().map(|s| Blocks::read(&s.part)).collect::<Result<Vec<_>>>()?;
                let starts: Vec<(u64, u64)> = chunk_h24.iter().map(|&(lo, _, h24)| (lo.max(from), h24)).collect();
                (Vec::new(), Vec::new(), starts, Some(SegSource { segs, blocks: seg_blocks, units, incomplete, chunks: chunk_h24 }))
            }
        };
        // The farmer windows of the emitted trades reach back to H0 (spec §3.4), and never before the store's start; each
        // chunk keeps what its own and later windows reach (legs.rs `keeps`).
        let keep = keeps(&starts);
        let h0 = keep[0].max(store_lo);
        let mut hist = FarmHists::default();
        let mut farm = Value::Null;
        if h0 < from {
            let m = farm_meta.with_context(|| {
                format!("rows from {from} need the store's farmer history from {h0} (the store starts at {store_lo}): export the snapshot \
                         with --history-from {from} --history-to {to}")
            })?;
            if m.hi != from || m.lo > h0 || to > m.to {
                bail!("the snapshot's farmer history [{}, {}) for rows to {} does not cover [{h0}, {from}) for rows to {to}", m.lo, m.hi, m.to);
            }
            let (rows, kept) = read_farm_hist(&snapshot.join("farm_hist.parquet"), &mut adm, &m, h0, &mut hist)?;
            if rows != m.rows {
                bail!("farm_hist.parquet has {rows} rows, its meta.json {}", m.rows);
            }
            log::info!("farmer history [{}, {}): {rows} rows, {kept} from H0 {h0} on, {} keys, RSS {} MB", m.lo, m.hi, hist.len(),
                       crate::mem::mb(crate::mem::rss_bytes()));
            farm = json!({"lo": m.lo, "hi": m.hi, "to": m.to, "rows": rows, "kept": kept, "keys": hist.len()});
        }
        let replay = Replay {
            hist_mem: hist.values().map(|h| h.mem()).fold((0, 0), |a, b| (a.0 + b.0, a.1 + b.1)),
            blocks_mem: blocks.iter().chain(seg.iter().flat_map(|s| s.blocks.iter())).map(Blocks::mem).fold((0, 0), |a, b| (a.0 + b.0, a.1 + b.1)),
            from, to, adm, emit, blocks, keep, h0, hist, farm, counts: Counts::default(), h24s: Vec::new(), done: false, seg,
            mem_log: false, last_mem: ReplayMem::default(), prune_farm: true, pruned: (0, 0, 0),
        };
        Ok((replay, with))
    }

    /// Step 4 of the module doc, once: chunk by chunk, every event of [from, to) in chain order to `f`, with the keys
    /// interned so far (every key of the event among them). An error of `f` ends the replay.
    pub fn for_each_event(&mut self, mut f: impl FnMut(&Keys, &Event) -> Result<()>) -> Result<()> {
        self.for_each_event_mem(|keys, _, ev| f(keys, ev))
    }

    /// With `mem_log`, the replay's structures after the last chunk or unit handed out (for the end line); empty before.
    pub fn last_mem(&self) -> &ReplayMem {
        &self.last_mem
    }

    /// `for_each_event` with the replay's structures of the current chunk or segment unit (`ReplayMem`; empty unless
    /// `mem_log`).
    pub fn for_each_event_mem(&mut self, mut f: impl FnMut(&Keys, &ReplayMem, &Event) -> Result<()>) -> Result<()> {
        self.for_each_slot_mem(|keys, mem, _, events| {
            for ev in events {
                f(keys, mem, ev)?;
            }
            Ok(())
        })
    }

    /// M3-07: the events slot by slot, each slot's (in chain order, its slot in [from, to)) with the structures of the
    /// chunk or unit it is in. A slot never spans two chunks or units (they split at slot bounds), so a slot is handed
    /// out once it is complete in its chunk or unit, with the same view `for_each_event_mem` gives each of its events.
    pub fn for_each_slot_mem(&mut self, mut f: impl FnMut(&Keys, &ReplayMem, u64, &[Event]) -> Result<()>) -> Result<()> {
        if std::mem::replace(&mut self.done, true) {
            bail!("a replay hands out its events once");
        }
        let (from, to) = (self.from, self.to);
        let mut ctx: HashMap<Id, PoolCtx> = HashMap::new();
        let mut buf: Vec<Event> = Vec::new(); // one slot's events, reused
        if let Some(sg) = self.seg.take() {
            let r = self.each_segment_event(&sg, &mut ctx, &mut buf, &mut f);
            self.seg = Some(sg);
            return r;
        }
        for (chunk, (p, bl)) in (0u32..).zip(self.emit.iter().zip(&self.blocks)) {
            let mut curve = read_curve(p, &mut self.adm, (from, to), &mut self.counts)?;
            let mut pool = read_pool(p, &mut self.adm, (from, to), &mut self.counts)?;
            curve.sort_unstable_by_key(|l| l.order);
            pool.sort_unstable_by_key(|r| r.order);
            check_ties(&curve, &pool).with_context(|| format!("{}", p.dir.display()))?;
            // a chunk's bound is its keep: no window of this chunk or a later one reaches before it
            if self.prune_farm {
                sweep_farm(&mut self.hist, &mut self.hist_mem, &mut self.pruned, self.keep[chunk as usize], bl.h24);
            }
            let d = flag_chunk(&mut pool, &mut self.hist, bl.h24, chunk, self.keep[chunk as usize]);
            self.hist_mem = add_mem(self.hist_mem, d);
            let mem = if self.mem_log { replay_mem(&self.adm, &self.hist, self.hist_mem, self.blocks_mem, &ctx, (&curve, &pool), &buf, None) } else { ReplayMem::default() };
            let keys = &self.adm.keys;
            emit_slots(&curve, &pool, |s| bl.at(s), &mut ctx, (from, to), &mut buf, |slot, evs| f(keys, &mem, slot, evs))?;
            if self.mem_log {
                self.last_mem = replay_mem(&self.adm, &self.hist, self.hist_mem, self.blocks_mem, &ctx, (&curve, &pool), &buf, None);
            }
            log::info!("chunk {}: {} curve and {} pool rows of [{}, {}), h24 {}, {} keys, RSS {} MB", p.dir.display(), curve.len(), pool.len(),
                       from.max(p.lo), to.min(p.hi), bl.h24, keys.len(), crate::mem::mb(crate::mem::rss_bytes()));
            self.h24s.push(json!([p.lo, p.hi, bl.h24]));
        }
        Ok(())
    }

    /// Step 4 on segments: unit by unit (a segment's part in one store chunk), as a chunk is.
    fn each_segment_event(&mut self, sg: &SegSource, ctx: &mut HashMap<Id, PoolCtx>, buf: &mut Vec<Event>,
                          f: &mut impl FnMut(&Keys, &ReplayMem, u64, &[Event]) -> Result<()>)
                          -> Result<()> {
        for u in &sg.units {
            let (s, bl) = (&sg.segs[u.seg], &sg.blocks[u.seg]);
            let mut curve = read_curve(&s.part, &mut self.adm, (u.lo, u.hi), &mut self.counts)?;
            let mut pool = read_pool(&s.part, &mut self.adm, (u.lo, u.hi), &mut self.counts)?;
            curve.sort_unstable_by_key(|l| l.order);
            pool.sort_unstable_by_key(|r| r.order);
            check_ties(&curve, &pool).with_context(|| format!("{}", s.part.dir.display()))?;
            // a unit's bound: its trades' windows start at or after lo - h24, a later chunk's at or after its keep
            let next = self.keep.get(u.chunk as usize + 1).copied().unwrap_or(u64::MAX);
            if self.prune_farm {
                sweep_farm(&mut self.hist, &mut self.hist_mem, &mut self.pruned, u.lo.saturating_sub(u.h24).min(next), u.h24);
            }
            let d = flag_chunk(&mut pool, &mut self.hist, u.h24, u.chunk, self.keep[u.chunk as usize]);
            self.hist_mem = add_mem(self.hist_mem, d);
            // segments handed out so far: this unit's segment is the (seg + 1)-th
            let mem = if self.mem_log {
                replay_mem(&self.adm, &self.hist, self.hist_mem, self.blocks_mem, ctx, (&curve, &pool), buf, Some(u.seg as u64 + 1))
            } else {
                ReplayMem::default()
            };
            let keys = &self.adm.keys;
            emit_slots(&curve, &pool, |x| bl.at(x), ctx, (u.lo, u.hi), buf, |slot, evs| f(keys, &mem, slot, evs))?;
            if self.mem_log {
                self.last_mem = replay_mem(&self.adm, &self.hist, self.hist_mem, self.blocks_mem, ctx, (&curve, &pool), buf, Some(u.seg as u64 + 1));
            }
        }
        for &(lo, hi, h24) in &sg.chunks {
            self.h24s.push(json!([lo, hi, h24]));
        }
        log::info!("{} segments ({} units) of [{}, {}), {} incomplete skipped, {} keys, RSS {} MB", sg.segs.len(), sg.units.len(), self.from,
                   self.to, sg.incomplete.len(), self.adm.keys.len(), crate::mem::mb(crate::mem::rss_bytes()));
        Ok(())
    }

    /// The replay's part of a summary: each emitted chunk's h24, H0, the farmer history, the chunks verified against the
    /// store, the kept and dropped rows per table, the keys interned and the peak RSS; on segments their counts and the
    /// backfill rows not read (spec §3: per segment min, median, max, their total and share of all rows).
    pub fn describe(&self, v: &mut Value) {
        v["h24"] = json!(self.h24s);
        v["h0"] = json!(self.h0);
        v["farm_hist"] = self.farm.clone();
        v["farm_hist_pruned"] = json!({"on": self.prune_farm, "sweeps": self.pruned.1, "pairs_dropped": self.pruned.2, "last_bound": self.pruned.0,
                                        "pairs_left": self.hist.len()});
        v["chunks_verified"] = json!(self.seg.is_none());
        if let Some(sg) = &self.seg {
            let mut bf: Vec<u64> = sg.segs.iter().map(|s| s.backfill_rows).collect();
            bf.sort_unstable();
            let (rows, backfill) = (sg.segs.iter().map(|s| s.rows).sum::<u64>(), bf.iter().sum::<u64>());
            v["segments"] = json!({
                "read": sg.segs.len(), "units": sg.units.len(), "incomplete_skipped": sg.incomplete,
                "rows": rows, "backfill_rows_not_read": {
                    "total": backfill, "min": bf.first(), "median": bf.get(bf.len() / 2), "max": bf.last(),
                    "share_of_all_rows": if rows + backfill > 0 { backfill as f64 / (rows + backfill) as f64 } else { 0.0 },
                },
            });
        }
        v["admission"] = self.counts.json();
        v["keys"] = json!(self.adm.keys.len());
        v["peak_rss_bytes"] = json!(crate::mem::peak_rss_bytes());
    }
}

/// `lab-live legs`: the replay over [from, to) as paper.legs_dump writes it, the summary (and with `out`, the lines);
/// see the module doc.
pub fn run(a: &LegsArgs) -> Result<Value> {
    if a.from >= a.to || a.window == 0 {
        bail!("--from must be below --to, and --window above 0");
    }
    let (mut replay, ()) = Replay::open(&a.chunks_root, &a.snapshot, a.from, a.to, |_, _| Ok(()))?;
    let mut summary = Summary::new(a.from, a.to, a.window);
    let mut file = match &a.out {
        Some(p) => Some(std::io::BufWriter::new(fs::File::create(p).with_context(|| format!("create {}", p.display()))?)),
        None => None,
    };
    replay.for_each_event(|keys, ev| {
        let (slot, line) = (ev.leg.slot(), event_line(keys, ev));
        if let Some(f) = file.as_mut().filter(|_| slot >= a.out_from) {
            writeln!(f, "{line}").context("write the lines")?;
        }
        summary.add(slot, event_kind(ev), &line);
        Ok(())
    })?;
    if let Some(mut f) = file {
        f.flush()?;
    }
    let mut v = summary.finish();
    replay.describe(&mut v);
    Ok(v)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::idl::{Kind, Val};
    use crate::sink::write_parquet;
    use arrow_array::{StringArray, UInt64Array};
    use sha2::{Digest, Sha256};

    pub(crate) fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-live-legs-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn s(x: &str) -> Val {
        Val::Str(x.to_owned())
    }

    fn u(x: u64) -> Val {
        Val::U64(x)
    }

    pub(crate) fn put(dir: &Path, table: &str, part: usize, cols: &[(&str, Kind)], rows: &[Vec<Val>]) -> PathBuf {
        let c: Vec<(String, Kind)> = cols.iter().map(|(n, k)| ((*n).to_owned(), *k)).collect();
        write_parquet(dir, table, part, &c, rows).unwrap()
    }

    /// The export (paper/snapshot_export.py) of a store over [1000, 3000): mint M1; pools P1 (normal), P3 (reversed),
    /// PX (quoted in another token: not a SOL market).
    pub(crate) fn export(dir: &Path) {
        let file = |name: &str, cols: &[(&str, Kind)], rows: &[Vec<Val>]| {
            fs::rename(put(&dir.join("t"), name, 0, cols, rows), dir.join(format!("{name}.parquet"))).unwrap();
        };
        file("mints", &[("mint", Kind::Utf8), ("create_slot", Kind::U64), ("creator", Kind::Utf8), ("mayhem", Kind::Bool),
                        ("quote_mint", Kind::Utf8), ("complete_slot", Kind::U64), ("pool", Kind::Utf8)],
             &[vec![s("M1"), u(900), s("C1"), Val::Bool(false), s("So1"), Val::Null, Val::Null]]);
        file("pools", &[("pool", Kind::Utf8), ("mint", Kind::Utf8), ("orientation", Kind::Utf8), ("quote_class", Kind::Utf8),
                        ("mayhem", Kind::Bool), ("coin_creator", Kind::Utf8), ("create_slot", Kind::U64)],
             &[vec![s("P1"), s("A1"), s("normal"), s("sol"), Val::Bool(false), Val::Null, u(10)],
               vec![s("P3"), s("A3"), s("reversed"), s("reversed"), Val::Bool(false), Val::Null, u(10)],
               vec![s("PX"), s("AX"), s("normal"), s("other"), Val::Bool(false), Val::Null, u(10)]]);
        file("farm_seed", &[("pool", Kind::Utf8), ("trader", Kind::Utf8), ("slot", Kind::U64), ("token_buy", Kind::Bool),
                            ("token_amount", Kind::I64)],
             &[vec![s("P1"), s("W"), u(2500), Val::Bool(true), Val::I64(1)]]);
        fs::write(dir.join("meta.json"), json!({"store_slots": [1000, 3000], "pool_slots": [1000, 3000],
                                                "farm_seed": {"lo": 2000, "hi": 3000, "h24": 1000}, "snapshot_version": 2,
                                                "chunk_list": [{"slots": [1000, 2000], "extractor_sha256": "S"},
                                                               {"slots": [2000, 3000], "extractor_sha256": "S"}],
                                                "chunk_h24": [[1000, 2000, 216_000], [2000, 3000, 864]],
                                                "farmer": {"ratio": 0.05, "min_trades": 2, "window_ms": 86_400_000}}).to_string()).unwrap();
    }

    pub(crate) fn edit_meta(dir: &Path, f: impl FnOnce(&mut Value)) {
        let mut v: Value = serde_json::from_slice(&fs::read(dir.join("meta.json")).unwrap()).unwrap();
        f(&mut v);
        fs::write(dir.join("meta.json"), v.to_string()).unwrap();
    }

    /// farm_hist.parquet as paper/snapshot_export.py writes it, and its meta.json entry.
    fn history(dir: &Path, rows: &[Vec<Val>], m: Value) {
        let cols = [("pool", Kind::Utf8), ("trader", Kind::Utf8), ("slot", Kind::U64), ("tx_index", Kind::I64), ("outer_ix", Kind::I64),
                    ("inner_ix", Kind::I64), ("token_buy", Kind::Bool), ("token_amount", Kind::I64)];
        let _ = fs::remove_dir_all(dir.join("t/farm_hist"));
        fs::rename(put(&dir.join("t"), "farm_hist", 0, &cols, rows), dir.join("farm_hist.parquet")).unwrap();
        edit_meta(dir, |v| v["farm_hist"] = m);
    }

    fn hrow(pool: &str, trader: &str, slot: u64, buy: bool, amount: i64) -> Vec<Val> {
        vec![s(pool), s(trader), u(slot), Val::I64(0), Val::I64(0), Val::I64(2), Val::Bool(buy), Val::I64(amount)]
    }

    pub(crate) const TRADE: [(&str, Kind); 23] = [
        ("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64), ("mint", Kind::Utf8),
        ("is_buy", Kind::Bool), ("token_amount", Kind::U64), ("quote_amount", Kind::U64), ("sol_amount", Kind::U64), ("fee", Kind::U64),
        ("creator_fee", Kind::U64), ("parent_ix_disc", Kind::Utf8), ("fee_basis_points", Kind::U64), ("creator_fee_basis_points", Kind::U64),
        ("virtual_token_reserves", Kind::U64), ("virtual_quote_reserves", Kind::U64), ("virtual_sol_reserves", Kind::U64),
        ("real_token_reserves", Kind::U64), ("real_quote_reserves", Kind::U64), ("real_sol_reserves", Kind::U64), ("user", Kind::Utf8),
        ("outer_program", Kind::Utf8), ("parent_ix_args", Kind::Utf8),
    ];

    /// A curve buy of 1,000 tokens for 50 (sol_amount; quote_amount NULL as in pre-v2 rows), fees 2 + 1.
    pub(crate) fn trade(slot: u64, tx: u64, mint: &str, args: Option<&str>) -> Vec<Val> {
        vec![u(slot), u(tx), u(0), u(1), s(mint), Val::Bool(true), u(1_000), Val::Null, u(50), u(2), u(1), s("66063d1201daebea"), u(95),
             u(30), u(900_000), Val::Null, u(30_000), u(700_000), Val::Null, u(20_000), s("T"), s(PUMP), args.map_or(Val::Null, s)]
    }

    pub(crate) fn pool_cols(specific: &[&'static str]) -> Vec<(&'static str, Kind)> {
        let mut c = vec![("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64), ("pool", Kind::Utf8),
                         ("user", Kind::Utf8), ("outer_program", Kind::Utf8), ("parent_ix_disc", Kind::Utf8),
                         ("pool_base_token_reserves", Kind::U64), ("pool_quote_token_reserves", Kind::U64),
                         ("lp_fee_basis_points", Kind::U64), ("protocol_fee_basis_points", Kind::U64), ("coin_creator", Kind::Utf8),
                         ("coin_creator_fee_basis_points", Kind::U64), ("virtual_quote_reserves", Kind::Utf8),
                         ("cashback_fee_basis_points", Kind::U64)];
        c.extend(specific.iter().map(|n| (*n, Kind::U64)));
        c
    }

    pub(crate) const BUY: [&str; 6] = ["base_amount_out", "quote_amount_in_with_lp_fee", "user_quote_amount_in", "quote_amount_in",
                                       "max_quote_amount_in", "min_base_amount_out"];
    pub(crate) const SELL: [&str; 4] = ["base_amount_in", "quote_amount_out_without_lp_fee", "user_quote_amount_out", "min_quote_amount_out"];

    /// A pool trade of `base` against `quote` (fees 20/5, no coin creator, virtual quote 7).
    pub(crate) fn ptrade(slot: u64, pool: &str, trader: &str, disc: Option<&str>, base: u64, quote: u64, sell: bool) -> Vec<Val> {
        let mut r = vec![u(slot), u(0), u(0), u(2), s(pool), s(trader), s(PUMP_AMM), disc.map_or(Val::Null, s), u(1_000_000), u(500_000),
                         u(20), u(5), s(DEFAULT_KEY), u(5), s("7"), Val::Null];
        if sell {
            r.extend([u(base), u(quote), u(quote - 1), u(quote - 9)]);
        } else {
            r.extend([u(base), u(quote + 1), u(quote + 2), u(quote), u(quote * 2), u(base / 2)]);
        }
        r
    }

    /// The rows of the store's two chunks below, every table's rows in one list, by table: the chunks and the segments
    /// (C4-1) are both written from it, split by slot.
    fn fixture_rows() -> Vec<(&'static str, Vec<(&'static str, Kind)>, Vec<Vec<Val>>)> {
        let blocks = vec![("slot", Kind::U64), ("block_time", Kind::I64), ("skipped", Kind::Bool)];
        let args = r#"{"spendable_sol_in": 500, "min_tokens_out": "7", "track_volume": true}"#;
        let boost = ["slot", "tx_index", "outer_ix", "inner_ix", "pool", "authority", "base_reserves_after", "base_amount_burned",
                     "real_quote_reserves_after", "quote_amount_in_used", "virtual_quote_reserves", "boost_vault_remaining"];
        let bk: Vec<(&str, Kind)> = boost.iter().map(|n| (*n, if matches!(*n, "pool" | "authority" | "virtual_quote_reserves") { Kind::Utf8 } else { Kind::U64 })).collect();
        let wd = ["slot", "tx_index", "outer_ix", "inner_ix", "pool", "user", "outer_program", "pool_base_token_reserves",
                  "pool_quote_token_reserves", "base_amount_out", "quote_amount_out", "lp_token_amount_in", "lp_mint_supply"];
        let wk: Vec<(&str, Kind)> = wd.iter().map(|n| (*n, if matches!(*n, "pool" | "user" | "outer_program") { Kind::Utf8 } else { Kind::U64 })).collect();
        vec![
            ("blocks", blocks, vec![vec![u(1000), Val::I64(1_000_000), Val::Bool(false)], vec![u(1100), Val::I64(1_000_040), Val::Bool(false)],
                                    vec![u(1200), Val::Null, Val::Bool(true)], vec![u(1900), Val::I64(1_000_360), Val::Bool(false)],
                                    vec![u(2000), Val::I64(2_000_000), Val::Bool(false)], vec![u(2400), Val::I64(2_040_000), Val::Bool(false)]]),
            ("pump/TradeEvent", TRADE.to_vec(), vec![trade(1100, 1, "MX", None), trade(1100, 0, "M1", Some(args))]),
            ("pump_amm/BuyEvent", pool_cols(&BUY), vec![
                ptrade(1400, "P1", "W", None, 100, 1_000, false),
                ptrade(1100, "P1", "B", Some("66063d1201daebea"), 40, 400, false),
                ptrade(1150, "PX", "B", None, 40, 400, false),
                ptrade(1160, "P1", "K", Some(BOOST_BUY_DISC), 40, 400, false),
                ptrade(1150, "P3", "R", None, 40, 400, false),
                ptrade(1200, "P1", "W", None, 100, 1_000, false),
            ]),
            ("pump_amm/SellEvent", pool_cols(&SELL), vec![ptrade(1300, "P1", "W", None, 100, 1_000, true), ptrade(2100, "P1", "W", None, 100, 1_000, true)]),
            ("pump_amm/BoostBuyAndBurnEvent", bk, vec![vec![u(1500), u(0), u(0), u(2), s("P1"), s("A"), u(990_000), u(10_000), u(500_300), u(300), s("9"), u(4)]]),
            ("pump_amm/WithdrawEvent", wk,
             vec![vec![u(1600), u(0), u(0), u(2), s("P1"), s("L"), s(PUMP_AMM), u(990_000), u(500_000), u(9_900), u(5_000), u(77), Val::Null]]),
        ]
    }

    /// The fixture's rows of [lo, hi) into `dir`, table by table (a table without a row there is not written).
    fn put_range(dir: &Path, lo: u64, hi: u64) {
        for (table, cols, rows) in fixture_rows() {
            let r: Vec<Vec<Val>> = rows.into_iter().filter(|r| matches!(r[0], Val::U64(x) if x >= lo && x < hi)).collect();
            if !r.is_empty() {
                put(dir, table, 0, &cols, &r);
            }
        }
    }

    /// Two chunks of the store, [1000, 2000) at 400 ms a slot (h24 216,000) and [2000, 3000) at 100 s a slot (h24 864):
    /// a curve trade of M1 and one of an unknown mint; P1 trades of B and of the wash trader W (buy 1200, sell 1300,
    /// buy 1400, sell 2100), a P3 buy, a PX buy and a boost-crank inner buy; a P1 boost and withdraw.
    pub(crate) fn chunks(root: &Path) {
        for (lo, hi) in [(1000, 2000), (2000, 3000)] {
            let dir = root.join(format!("{lo}-{hi}"));
            fs::create_dir_all(&dir).unwrap();
            fs::write(dir.join("_manifest.json"), json!({"status": "complete", "slot_start": lo, "slot_end_exclusive": hi,
                                                          "extractor_sha256": "S"}).to_string()).unwrap();
            put_range(&dir, lo, hi);
        }
    }

    /// The same rows as the recorder's segments (C4-1): [1000, 1150), [1150, 2050) across the store's chunk bound at
    /// 2000, [2050, 3000); the second has a backfill table (not read, counted), and `incomplete` adds one segment that is
    /// not complete (skipped).
    pub(crate) fn segments(root: &Path, incomplete: bool) {
        for (seq, (lo, hi)) in [(1000, 1150), (1150, 2050), (2050, 3000)].into_iter().enumerate() {
            let dir = root.join(format!("{seq:09}"));
            fs::create_dir_all(&dir).unwrap();
            fs::write(dir.join("_segment.json"), json!({"seq": seq, "first_slot": lo, "last_slot": hi - 1, "complete": true}).to_string()).unwrap();
            put_range(&dir, lo, hi);
        }
        put(&root.join("000000001/backfill"), "pump_amm/BuyEvent", 0, &pool_cols(&BUY),
            &[ptrade(1120, "P1", "B2", None, 40, 400, false), ptrade(1121, "P1", "B3", None, 40, 400, false)]);
        if incomplete {
            let dir = root.join("000000003");
            fs::create_dir_all(&dir).unwrap();
            fs::write(dir.join("_segment.json"), json!({"seq": 3, "first_slot": 3000, "last_slot": 3099, "complete": false}).to_string()).unwrap();
        }
    }

    fn args(root: &Path, from: u64, to: u64) -> LegsArgs {
        LegsArgs { chunks_root: root.join("chunks"), snapshot: root.join("snap"), from, to, window: 1000, summary: root.join("summary.json"),
                   out: Some(root.join("lines.txt")), out_from: 0 }
    }

    #[test]
    fn the_chunks_become_the_stores_event_stream() {
        let root = tmp("run");
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        chunks(&root.join("chunks"));
        let v = run(&args(&root, 1000, 3000)).unwrap();
        assert_eq!(v["events"], 9);
        assert_eq!((&v["h0"], &v["farm_hist"], &v["chunks_verified"]), (&json!(1000), &Value::Null, &json!(true)));
        assert_eq!(v["kinds"], json!({"curve": 1, "pool/boost": 1, "pool/buy": 4, "pool/sell": 2, "pool/withdraw": 1}));
        assert_eq!(v["h24"], json!([[1000, 2000, 216_000], [2000, 3000, 864]]));
        assert_eq!(v["windows"].as_array().unwrap().iter().map(|w| w[2].as_u64().unwrap()).collect::<Vec<_>>(), [8, 1]);
        let adm = &v["admission"];
        assert_eq!((&adm["pump/TradeEvent: kept"], &adm["pump/TradeEvent: dropped, mint not in the store"]), (&json!(1), &json!(1)));
        assert_eq!(adm["pump_amm/BuyEvent: dropped, not a SOL market of the store"], 1);
        assert_eq!(adm["pump_amm/BuyEvent: dropped, boost-crank inner buy"], 1);
        let text = fs::read_to_string(root.join("lines.txt")).unwrap();
        let hex: String = Sha256::digest(text.as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(v["sha256"], json!(hex));
        let lines: Vec<Vec<&str>> = text.lines().map(|l| l.split('\t').collect()).collect();
        // chain order: the curve trade (inner 1) before P1's buy (inner 2) in tx 0 of slot 1100, which has a block time
        let head: Vec<(&str, &str, &str)> = lines.iter().map(|l| (l[0], l[2], l[8])).collect();
        assert_eq!(head, [("curve", "1100", "buy"), ("pool", "1100", "buy"), ("pool", "1150", "buy"), ("pool", "1200", "buy"),
                          ("pool", "1300", "sell"), ("pool", "1400", "buy"), ("pool", "1500", "boost"), ("pool", "1600", "withdraw"),
                          ("pool", "2100", "sell")]);
        let c = &lines[0];
        let tau = Order { slot: 1100, tx: 0, outer: 0, inner: 1 }.tau_u().to_string();
        assert_eq!(&c[1..8], ["M1", "1100", "0", "0", "1", "1000040", tau.as_str()]);
        assert_eq!(&c[9..13], ["true", "1000", "50", "3"]); // q from sol_amount; fee + creator fee
        assert_eq!(&c[c.len() - 6..], ["T", "500", "null", "7", "null", "true"]); // budget, max_cost, min_tokens, min_out, direct
        assert_eq!(lines[1][6], "1000040");
        assert_eq!(lines[2][6], "null"); // no block at 1150
        assert_eq!(lines[1][9], "true"); // exact out
        // the farmer: W's buy at 1400 sees buy and sell of 100 (net 0); its sell at 2100 sees only 1300 and 1400 with
        // the second chunk's h24 of 864 (1200 is out), so net 0 again; the first two have fewer than 2 trades
        let farmer: Vec<&str> = lines.iter().filter(|l| l[0] == "pool").map(|l| *l.last().unwrap()).collect();
        assert_eq!(farmer, ["false", "false", "false", "false", "true", "false", "false", "true"]);
        // the reversed pool's buy: its trader R, quote_user 400
        assert_eq!((lines[2][1], lines[2][11], lines[2][26]), ("P3", "400", "R"));
        // the withdraw: the boost's virtual quote (9) and the last trade's fees (20, 5, no creator, no cashback)
        let w = &lines[7];
        assert_eq!(&w[12..19], ["990000", "500009", "20", "5", "0", "0", "9"]);
        assert_eq!(&w[w.len() - 3..], ["77", "null", "false"]); // lp_amount, lp_supply, farmer
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn segments_give_the_chunks_events_and_count_their_backfill() {
        // C4-1: the same rows as segments (one across the store's chunk bound, which splits it into two units with the two
        // chunks' h24) give exactly the chunks' event stream; the backfill rows are counted, not read; an incomplete
        // segment is skipped; a gap is refused.
        let root = tmp("segments");
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        chunks(&root.join("chunks"));
        segments(&root.join("segments"), true);
        let lines = |seg: bool| {
            let snap = root.join("snap");
            let (mut r, ()) = if seg { Replay::open_segments(&root.join("segments"), &snap, 1000, 3000, |_, _| Ok(())).unwrap() }
                              else { Replay::open(&root.join("chunks"), &snap, 1000, 3000, |_, _| Ok(())).unwrap() };
            let mut out = Vec::new();
            r.for_each_event(|keys, ev| {
                out.push(crate::legs::event_line(keys, ev));
                Ok(())
            }).unwrap();
            let mut v = json!({});
            r.describe(&mut v);
            (out, v)
        };
        let (from_chunks, vc) = lines(false);
        let (from_segments, vs) = lines(true);
        assert_eq!(from_chunks.len(), 9);
        assert_eq!(from_segments, from_chunks);
        assert_eq!(vs["h24"], vc["h24"]);
        assert_eq!((&vs["chunks_verified"], &vs["segments"]["read"], &vs["segments"]["units"]), (&json!(false), &json!(3), &json!(4)));
        assert_eq!(vs["segments"]["incomplete_skipped"], json!([3]));
        let bf = &vs["segments"]["backfill_rows_not_read"];
        assert_eq!((&bf["total"], &bf["min"], &bf["median"], &bf["max"]), (&json!(2), &json!(0), &json!(0), &json!(2)));
        let rows = vs["segments"]["rows"].as_u64().unwrap();
        assert!(rows > 0 && (bf["share_of_all_rows"].as_f64().unwrap() - 2.0 / (rows + 2) as f64).abs() < 1e-12, "{vs}");
        // a gap: without the middle segment [1150, 2050) is not covered
        fs::rename(root.join("segments/000000001"), root.join("gone")).unwrap();
        let err = format!("{:#}", Replay::open_segments(&root.join("segments"), &root.join("snap"), 1000, 3000, |_, _| Ok(())).err().unwrap());
        assert!(err.contains("no complete segment for slots [1150, 2050)"), "{err}");
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_slots_are_whole_and_flatten_to_the_events() {
        // M3-07: on chunks and on segments (where a store chunk boundary splits a segment into units), for_each_slot_mem
        // hands out whole slots in increasing order, each event of its slot, with the view for_each_event_mem gives each
        // of its events; flattened they are for_each_event_mem's events in its order
        let root = tmp("slots");
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        chunks(&root.join("chunks"));
        segments(&root.join("segments"), true);
        for seg in [false, true] {
            let open = || {
                let snap = root.join("snap");
                let (mut r, ()) = if seg { Replay::open_segments(&root.join("segments"), &snap, 1000, 3000, |_, _| Ok(())).unwrap() }
                                  else { Replay::open(&root.join("chunks"), &snap, 1000, 3000, |_, _| Ok(())).unwrap() };
                r.mem_log = true;
                r
            };
            let mut by_event = Vec::new();
            open().for_each_event_mem(|keys, m, ev| {
                by_event.push((crate::legs::event_line(keys, ev), m.clone()));
                Ok(())
            }).unwrap();
            let (mut by_slot, mut slots) = (Vec::new(), Vec::new());
            let mut r = open();
            crate::source::Source::for_each_item(&mut r, &mut |keys, m, item| {
                let crate::source::Item::Slot(sl) = item else { panic!("the replay hands out slots only") };
                assert!(!sl.events.is_empty() && sl.events.iter().all(|e| e.leg.slot() == sl.slot), "seg {seg}: slot {}", sl.slot);
                slots.push(sl.slot);
                by_slot.extend(sl.events.iter().map(|e| (crate::legs::event_line(keys, e), m.clone())));
                Ok(())
            }).unwrap();
            assert!(slots.windows(2).all(|w| w[0] < w[1]), "seg {seg}: {slots:?}");
            assert_eq!(by_slot, by_event, "seg {seg}");
            assert!(by_event.len() >= 9 && slots.len() >= 8, "seg {seg}: the fixture's events ({}) and slots ({})", by_event.len(), slots.len());
            assert!(open().for_each_slot_mem(|_, _, _, _| Ok(())).is_ok());
            let mut once = open();
            once.for_each_slot_mem(|_, _, _, _| Ok(())).unwrap();
            assert!(once.for_each_slot_mem(|_, _, _, _| Ok(())).is_err(), "seg {seg}: a replay hands out its events once");
        }
    }

    #[test]
    fn the_mem_log_view_keeps_the_farmer_totals_and_counts_the_segments() {
        // C4-2: with mem_log, every event carries the replay's structures of its chunk or unit. The farmer histories'
        // running totals (flag_chunk's deltas) equal a walk over them at the end; on segments the count runs up to the
        // segments read. The events themselves are the same with and without mem_log.
        let root = tmp("memlog");
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        chunks(&root.join("chunks"));
        segments(&root.join("segments"), true);
        for seg in [false, true] {
            let run = |mem_log: bool| {
                let snap = root.join("snap");
                let (mut r, ()) = if seg { Replay::open_segments(&root.join("segments"), &snap, 1000, 3000, |_, _| Ok(())).unwrap() }
                                  else { Replay::open(&root.join("chunks"), &snap, 1000, 3000, |_, _| Ok(())).unwrap() };
                r.mem_log = mem_log;
                let (mut lines, mut views) = (Vec::new(), Vec::new());
                r.for_each_event_mem(|keys, m, ev| {
                    lines.push(crate::legs::event_line(keys, ev));
                    views.push(m.clone());
                    Ok(())
                }).unwrap();
                let walk = r.hist.values().map(|h| h.mem()).fold((0, 0), |a, b| (a.0 + b.0, a.1 + b.1));
                (lines, views, r.hist_mem, walk, r.hist.len(), r.last_mem().clone())
            };
            let (plain, empty, ..) = run(false);
            let (lines, views, total, walk, pairs, last_mem) = run(true);
            // the view after the last chunk or unit (the end line's): the final totals, and on segments all three read
            let fin = |name: &str| last_mem.mems.iter().find(|m| m.name == name).map(|m| m.entries).unwrap();
            assert_eq!((fin("farmer_hist"), fin("farmer_hist_trades"), last_mem.segments), (pairs, walk.0, seg.then_some(3)), "seg {seg}");
            assert_eq!(lines, plain, "seg {seg}");
            assert!(empty.iter().all(|m| m.mems.is_empty() && m.segments.is_none()), "seg {seg}: no view without mem_log");
            assert_eq!(total, walk, "seg {seg}");
            assert!(walk.0 > 0 && pairs > 0, "seg {seg}: the fixture has farmer history");
            // C4-5: farmer_hist's bytes are its table's allocation and the boxed histories; both tables' shape is in the view
            let names: Vec<&str> = last_mem.tables.iter().map(|t| t.name).collect();
            assert_eq!(names, ["interned_keys", "farmer_hist"], "seg {seg}");
            let ft = &last_mem.tables[1];
            let fin_bytes = last_mem.mems.iter().find(|m| m.name == "farmer_hist").map(|m| m.bytes).unwrap();
            assert_eq!((ft.len, fin_bytes), (pairs, ft.bytes + pairs * std::mem::size_of::<crate::legs::FarmHist>()), "seg {seg}");
            assert!(ft.buckets.is_power_of_two() && ft.len <= ft.full(), "seg {seg}: {ft:?}");
            assert!(empty.iter().all(|m| m.tables.is_empty()), "seg {seg}");
            let last = views.last().unwrap();
            let get = |name: &str| last.mems.iter().find(|m| m.name == name).map(|m| m.entries).unwrap();
            assert_eq!((get("farmer_hist"), get("farmer_hist_trades")), (pairs, walk.0), "seg {seg}: the last unit's view");
            assert!(get("interned_keys") > 0 && get("legs_buffer") > 0, "seg {seg}");
            // M3-07's slot buffer: at the end its capacity holds the largest slot handed out (and the first view's, before
            // any slot, is empty)
            let mut per_slot: BTreeMap<u64, usize> = BTreeMap::new();
            for l in &lines {
                *per_slot.entry(l.split('\t').nth(2).unwrap().parse().unwrap()).or_default() += 1;
            }
            let largest = *per_slot.values().max().unwrap();
            assert!(fin("slot_buf") >= largest && largest > 1, "seg {seg}: {} < {largest}", fin("slot_buf"));
            assert_eq!(views[0].mems.iter().find(|m| m.name == "slot_buf").map(|m| m.entries), Some(0), "seg {seg}");
            // the block-time tables: one entry per non-skipped slot of every chunk or segment read, the same in every view
            assert!(get("block_times") > 0 && views.iter().all(|m| m.mems.iter().any(|x| x.name == "block_times" && x.entries == get("block_times"))),
                    "seg {seg}");
            let segs: Vec<Option<u64>> = views.iter().map(|m| m.segments).collect();
            if seg {
                // three complete segments read; the count never goes down
                assert!(segs.windows(2).all(|w| w[0] <= w[1]) && segs.first() >= Some(&Some(1)) && segs.last() == Some(&Some(3)), "{segs:?}");
            } else {
                assert!(segs.iter().all(Option::is_none), "chunks have no segment count");
            }
        }
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_farm_prune_changes_no_event() {
        // M1 C4-4: the same replay with and without the farmer-history prune (the default and --keep-farmer-history) gives
        // the same event lines on the chunks and on the segments; the summary says whether it pruned.
        let root = tmp("prune");
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        chunks(&root.join("chunks"));
        segments(&root.join("segments"), true);
        for seg in [false, true] {
            let run = |prune: bool| {
                let snap = root.join("snap");
                let (mut r, ()) = if seg { Replay::open_segments(&root.join("segments"), &snap, 1000, 3000, |_, _| Ok(())).unwrap() }
                                  else { Replay::open(&root.join("chunks"), &snap, 1000, 3000, |_, _| Ok(())).unwrap() };
                r.prune_farm = prune;
                let mut lines = Vec::new();
                r.for_each_event(|keys, ev| {
                    lines.push(crate::legs::event_line(keys, ev));
                    Ok(())
                }).unwrap();
                let mut v = json!({});
                r.describe(&mut v);
                (lines, v["farm_hist_pruned"].clone())
            };
            let ((on, p_on), (off, p_off)) = (run(true), run(false));
            assert_eq!(on, off, "seg {seg}");
            assert!(!on.is_empty());
            assert_eq!((&p_on["on"], &p_off["on"], &p_off["sweeps"]), (&json!(true), &json!(false), &json!(0)), "seg {seg}");
            assert!(p_on["pairs_left"].as_u64() <= p_off["pairs_left"].as_u64(), "seg {seg}: {p_on} {p_off}"); // legs.rs's test drops pairs
        }
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_hold_out_a_range_outside_the_store_missing_history_ties_and_gaps_are_refused() {
        let root = tmp("refuse");
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        chunks(&root.join("chunks"));
        let err = |a: LegsArgs| format!("{:#}", run(&a).unwrap_err());
        assert!(err(args(&root, HOLDOUT.0 - 10, HOLDOUT.0 + 10)).contains("hold-out"));
        assert!(err(args(&root, HOLDOUT.1 - 1, HOLDOUT.1 + 10)).contains("hold-out"));
        assert!(err(args(&root, CLEAN.0 - 10, CLEAN.0 + 10)).contains("clean period"));
        assert!(err(args(&root, CLEAN.1 - 1, CLEAN.1 + 10)).contains("clean period"));
        assert!(err(args(&root, 900, 2000)).contains("store period"));
        assert!(err(args(&root, 1000, 3001)).contains("store period"));
        assert!(err(args(&root, 1500, 3000)).contains("farmer history")); // windows reach back to the store's start
        // a second row at W's sell (1300, 0, 0, 2): a key tie
        put(&root.join("chunks/1000-2000"), "pump_amm/SellEvent", 1, &pool_cols(&SELL), &[ptrade(1300, "P1", "Z", None, 1, 10, true)]);
        assert!(err(args(&root, 1000, 2000)).contains("key ties"));
        fs::remove_file(root.join("chunks/1000-2000/pump_amm/SellEvent/part-00001.parquet")).unwrap();
        assert!(run(&args(&root, 1000, 2000)).is_ok());
        fs::remove_file(root.join("chunks/2000-3000/_manifest.json")).unwrap();
        assert!(err(args(&root, 1000, 3000)).contains("no complete chunk"));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_start_inside_the_store_takes_the_farmer_history_of_exactly_its_replay() {
        let root = tmp("hist");
        let snap = root.join("snap");
        fs::create_dir_all(&snap).unwrap();
        export(&snap);
        chunks(&root.join("chunks"));
        let err = |a: LegsArgs| format!("{:#}", run(&a).unwrap_err());
        run(&args(&root, 1000, 3000)).unwrap();
        let full = fs::read_to_string(root.join("lines.txt")).unwrap();
        let last = full.lines().last().unwrap().to_owned(); // W's sell at 2100, a farmer by 1300 and 1400 (h24 864)
        assert!(last.starts_with("pool\tP1\t2100\t") && last.ends_with("\ttrue"), "{last}");
        // [2000, 3000): H0 = 2000 - 864 = 1136, after the store's start; without the history: refused
        assert!(err(args(&root, 2000, 3000)).contains("farmer history"));
        // W's buys and sells before 2000 (a row at 1110 before H0, which no window reaches)
        let rows = [hrow("P1", "W", 1110, true, 100), hrow("P1", "W", 1200, true, 100), hrow("P1", "W", 1300, false, 100),
                    hrow("P1", "W", 1400, true, 100)];
        history(&snap, &rows, json!({"lo": 1100, "hi": 2000, "to": 3000, "rows": 4}));
        let v = run(&args(&root, 2000, 3000)).unwrap();
        assert_eq!((&v["events"], &v["h0"]), (&json!(1), &json!(1136)));
        assert_eq!(v["farm_hist"], json!({"lo": 1100, "hi": 2000, "to": 3000, "rows": 4, "kept": 3, "keys": 1}));
        assert_eq!(fs::read_to_string(root.join("lines.txt")).unwrap(), format!("{last}\n")); // the same line as from the start
        // the history must be this replay's: from its hi, to at most its to, from its lo at most H0, every row counted
        assert!(run(&args(&root, 2000, 2999)).is_ok()); // a replay that ends earlier needs no other history
        history(&snap, &rows, json!({"lo": 1100, "hi": 2000, "to": 2900, "rows": 4}));
        assert!(err(args(&root, 2000, 3000)).contains("does not cover"));
        history(&snap, &rows[1..], json!({"lo": 1200, "hi": 2000, "to": 3000, "rows": 3}));
        assert!(err(args(&root, 2000, 3000)).contains("does not cover"));
        history(&snap, &rows, json!({"lo": 1100, "hi": 2000, "to": 3000, "rows": 4}));
        assert!(err(args(&root, 2500, 3000)).contains("farmer history")); // H0 1636: another replay's history
        history(&snap, &rows, json!({"lo": 1100, "hi": 2000, "to": 3000, "rows": 5}));
        assert!(err(args(&root, 2000, 3000)).contains("rows"));
        // rows out of chain order, outside the history, of a pool that is no SOL market, a negative amount
        for (bad, why) in [(vec![rows[2].clone(), rows[1].clone()], "chain order"), (vec![hrow("P1", "W", 1050, true, 1)], "outside"),
                           (vec![hrow("PX", "W", 1200, true, 1)], "SOL market"), (vec![hrow("P1", "W", 1200, true, -1)], "negative")] {
            history(&snap, &bad, json!({"lo": 1100, "hi": 2000, "to": 3000, "rows": bad.len()}));
            assert!(err(args(&root, 2000, 3000)).contains(why), "{why}");
        }
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_chunks_must_be_the_stores_own() {
        let root = tmp("own");
        let snap = root.join("snap");
        fs::create_dir_all(&snap).unwrap();
        export(&snap);
        chunks(&root.join("chunks"));
        let err = |a: LegsArgs| format!("{:#}", run(&a).unwrap_err());
        assert!(run(&args(&root, 1000, 3000)).is_ok());
        edit_meta(&snap, |v| v["chunk_h24"][1][2] = json!(865)); // the store's h24 of the second chunk differs
        assert!(err(args(&root, 1000, 3000)).contains("h24 864 from its blocks, 865 in the store"));
        assert!(run(&args(&root, 1000, 2000)).is_ok());
        edit_meta(&snap, |v| v["chunk_h24"][1][2] = json!(864));
        edit_meta(&snap, |v| v["chunk_list"][1]["extractor_sha256"] = json!("T")); // another extraction
        assert!(err(args(&root, 1000, 3000)).contains("extractor_sha256"));
        edit_meta(&snap, |v| v["chunk_list"][1]["extractor_sha256"] = Value::Null); // a store that names none
        assert!(err(args(&root, 1000, 3000)).contains("no extractor build"));
        edit_meta(&snap, |v| v["chunk_list"][1]["extractor_sha256"] = json!("S"));
        edit_meta(&snap, |v| v["farmer"]["ratio"] = json!(0.1)); // another farmer rule
        assert!(err(args(&root, 1000, 3000)).contains("farmer rule"));
        edit_meta(&snap, |v| v["farmer"]["ratio"] = json!(0.05));
        assert!(run(&args(&root, 1000, 3000)).is_ok());
        edit_meta(&snap, |v| {
            v["chunk_list"][1] = json!({"slots": [2000, 3500], "extractor_sha256": "S"});
            v["chunk_h24"][1] = json!([2000, 3500, 864]);
        });
        assert!(err(args(&root, 1000, 3000)).contains("not one of the store's chunks"));
        edit_meta(&snap, |v| v["chunk_h24"][1] = json!([2000, 3000, 864])); // chunk_h24 for another chunk than chunk_list's
        assert!(err(args(&root, 1000, 3000)).contains("chunk_h24"));
        edit_meta(&snap, |v| {
            v.as_object_mut().unwrap().remove("snapshot_version");
            v.as_object_mut().unwrap().remove("chunk_list");
        });
        assert!(err(args(&root, 1000, 3000)).contains("no chunk_list")); // an export from before C1a-2
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn duckdbs_casts_and_the_instruction_arguments() {
        let a = StringArray::from(vec![Some("123"), Some("99999999999999999999"), Some("-5"), Some("1.5"), None]);
        let c = Col::S(&a);
        assert_eq!(try_bigint(&c, 0).unwrap(), Some(123));
        assert_eq!(try_bigint(&c, 1).unwrap(), None); // beyond BIGINT: TRY_CAST gives NULL (then 0)
        assert_eq!(try_bigint(&c, 2).unwrap(), Some(-5));
        assert!(try_bigint(&c, 3).is_err()); // the extractor writes no such value: refused, not guessed
        assert_eq!(try_bigint(&c, 4).unwrap(), None);
        let big = UInt64Array::from(vec![u64::MAX, 7]);
        let c = Col::U(&big);
        assert!(c.bigint(0).is_err()); // the hard ::BIGINT
        assert_eq!(try_bigint(&c, 0).unwrap(), None);
        assert!(c.integer(0).is_err());
        assert_eq!(c.integer(1).unwrap(), Some(7));
        let v: Value = serde_json::from_str(r#"{"a": 5, "b": "6", "c": true, "d": null, "e": {"x": 1}, "f": -1, "g": "x"}"#).unwrap();
        assert_eq!((arg(Some(&v), "a").unwrap(), arg(Some(&v), "b").unwrap()), (Some(5), Some(6)));
        for k in ["c", "d", "e", "missing"] {
            assert_eq!(arg(Some(&v), k).unwrap(), None, "{k}");
        }
        assert!(arg(Some(&v), "f").is_err() && arg(Some(&v), "g").is_err());
        assert_eq!(arg(None, "a").unwrap(), None);
    }

    #[test]
    fn the_chunks_must_cover_the_range_once() {
        let p = |lo: u64, hi: u64| Part { lo, hi, dir: PathBuf::from(format!("{lo}")) };
        let parts = [p(0, 10), p(10, 20), p(30, 40)];
        assert_eq!(covering(&parts, 5, 15, "r").unwrap().len(), 2);
        assert!(covering(&parts, 15, 35, "r").is_err()); // the gap [20, 30)
        assert!(covering(&parts, 35, 45, "r").is_err());
        assert!(covering(&[p(0, 10), p(5, 15)], 0, 15, "r").is_err());
    }
}
