//! The Parquet side of the legs reader (legs.rs): the chunk event tables into CurveLegs and PoolRows exactly as the
//! store's SQL builds its `curve` and `pool` rows (backtest/store.py `_curve_trades`, `_pool_trades`, `_pool_other`,
//! the SOL-market join; DuckDB's casts: research/m1-c1a-legs-spec-2026-10-19.md §2-§3), and `run`, the replay over
//! [from, to):
//!
//! 1. refused: the hold-out, a range outside the snapshot's store period, one not covered by complete chunks, a chunk
//!    that is not the store's (its slots, the extractor build in its manifest, its h24), and one whose farmer windows
//!    reach before `from` without the store's history for exactly this replay (farm_hist.parquet, C1a-2);
//! 2. each emitted chunk's h24 from its own blocks, over the whole chunk, equal to the store's;
//! 3. the farmer history over [H0, from) from the snapshot (research/m1-c1a2-spec-2026-10-19.md), H0 the earliest
//!    slot a window of an emitted trade reaches (legs.rs `keeps`);
//! 4. chunk by chunk: its curve and pool rows read (admitted by the snapshot: its mints for curve rows, its SOL markets
//!    for pool rows), sorted, gated (no key ties), the farmer flags set, and the events emitted in chain order.

use crate::legs::{check_ties, emit_chunk, flag_chunk, h24_of, keeps, FarmHist, Keys, PoolRow, Summary, REVERSED};
use crate::rows::{chunks, Part};
use crate::snapshot::{FarmHistMeta, Snapshot};
use anyhow::{bail, Context, Result};
use arrow_array::{Array, BooleanArray, Int64Array, LargeStringArray, RecordBatch, StringArray, StringViewArray, UInt64Array};
use lab_core::legs::{
    CurveLeg, CurveVariant, Id, Order, PoolCtx, PoolKind, BOOST_BUY_DISC, DEFAULT_KEY, DIRECT, EXACT_OUT, HOLDOUT, NONE, POOL_EXACT_OUT,
    PUMP, PUMP_AMM, X0, X1,
};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::arrow::ProjectionMask;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

/// The snapshot's keys and what the feed admits: curve rows of its mints, pool rows of its SOL markets (with their
/// orientation). Interned first, so their ids are dense from 0.
pub struct Admission {
    pub keys: Keys,
    is_mint: Vec<bool>,
    /// per id: 0 not a SOL market, 1 normal, 2 reversed
    pool: Vec<u8>,
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
        Ok(Admission { keys, is_mint, pool })
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

/// One column of a batch, typed; `Absent` when the file has no such column (reads as NULL).
enum Col<'a> {
    U(&'a UInt64Array),
    I(&'a Int64Array),
    B(&'a BooleanArray),
    S(&'a StringArray),
    L(&'a LargeStringArray),
    V(&'a StringViewArray),
    Absent,
}

impl<'a> Col<'a> {
    fn of(batch: &'a RecordBatch, name: &str) -> Result<Self> {
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

    fn null(&self, i: usize) -> bool {
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
    fn u64(&self, i: usize) -> Result<Option<u64>> {
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
    fn bigint(&self, i: usize) -> Result<Option<i64>> {
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
    fn integer(&self, i: usize) -> Result<Option<u32>> {
        match self.bigint(i)? {
            None => Ok(None),
            Some(v) => {
                let v = i32::try_from(v).context("a value beyond INTEGER")?;
                Ok(Some(u32::try_from(v).context("a negative basis-point value")?))
            }
        }
    }

    fn bool(&self, i: usize) -> Result<Option<bool>> {
        if self.null(i) {
            return Ok(None);
        }
        match self {
            Col::B(a) => Ok(Some(a.value(i))),
            _ => bail!("a boolean column of another type"),
        }
    }

    fn str(&self, i: usize) -> Result<Option<&'a str>> {
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
fn order(c: &[Col], i: usize, slot: u64) -> Result<Order> {
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
fn each_row(dir: &Path, table: &str, cols: &[&str], required: &[&str], mut f: impl FnMut(&[Col], usize) -> Result<()>) -> Result<u64> {
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
fn read_farm_hist(path: &Path, adm: &mut Admission, m: &FarmHistMeta, h0: u64, hist: &mut HashMap<(Id, Id), FarmHist>) -> Result<(u64, u64)> {
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
        let Some(mint) = c[4].str(i)?.and_then(|m| adm.mint(m)) else {
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
            let Some((pool, reversed)) = c[4].str(i)?.and_then(|p| adm.pool(p)) else {
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
        let Some((pool, reversed)) = c[4].str(i)?.and_then(|p| adm.pool(p)) else {
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
            let Some((pool, reversed)) = c[4].str(i)?.and_then(|p| adm.pool(p)) else {
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

/// The replay over [from, to): the summary (and with `out`, the lines), see the module doc.
pub fn run(a: &LegsArgs) -> Result<Value> {
    if a.from >= a.to || a.window == 0 {
        bail!("--from must be below --to, and --window above 0");
    }
    if a.from < HOLDOUT.1 && a.to > HOLDOUT.0 {
        bail!("[{}, {}) overlaps the hold-out [{}, {}): it is never read", a.from, a.to, HOLDOUT.0, HOLDOUT.1);
    }
    let mut snap = Snapshot::load_universe(&a.snapshot)?;
    let (store_lo, store_hi) = snap.store_slots.context("the snapshot's meta.json has no store_slots")?;
    if a.from < store_lo || a.to > store_hi {
        bail!("[{}, {}) is outside the snapshot's store period [{store_lo}, {store_hi}): no ReplayFeed reference there", a.from, a.to);
    }
    if snap.version < 2 || snap.chunks.is_empty() {
        bail!("the snapshot (version {}) has no chunk_list: export it again with paper.snapshot_export (snapshot_version 2)", snap.version);
    }
    let store_chunks = std::mem::take(&mut snap.chunks);
    let farm_meta = snap.farm_hist;
    let mut adm = Admission::from_snapshot(&snap)?;
    drop(snap);
    let parts = chunks(&a.chunks_root)?;
    let emit = covering(&parts, a.from, a.to, "the range")?;
    // The store's own chunks: their slots, the extractor build that wrote them, and their h24 (review on #369).
    let mut blocks = Vec::new();
    for p in &emit {
        let c = store_chunks.iter().find(|c| (c.lo, c.hi) == (p.lo, p.hi))
            .with_context(|| format!("chunk {} ([{}, {})) is not one of the store's chunks", p.dir.display(), p.lo, p.hi))?;
        let sha = manifest_sha(p)?;
        if sha != c.extractor_sha256 {
            bail!("chunk {}: extractor_sha256 {sha:?}, the store was built from {:?}", p.dir.display(), c.extractor_sha256);
        }
        let bl = Blocks::read(p)?;
        if bl.h24 != c.h24 {
            bail!("chunk {}: h24 {} from its blocks, {} in the store", p.dir.display(), bl.h24, c.h24);
        }
        blocks.push(bl);
    }
    // The farmer windows of the emitted trades reach back to H0 (spec §3.4), and never before the store's start; each
    // chunk keeps what its own and later windows reach (legs.rs `keeps`).
    let starts: Vec<(u64, u64)> = emit.iter().zip(&blocks).map(|(p, b)| (p.lo.max(a.from), b.h24)).collect();
    let keep = keeps(&starts);
    let h0 = keep[0].max(store_lo);
    let mut counts = Counts::default();
    let mut hist: HashMap<(Id, Id), FarmHist> = HashMap::new();
    let mut farm = Value::Null;
    if h0 < a.from {
        let m = farm_meta.with_context(|| {
            format!("rows from {} need the store's farmer history from {h0} (the store starts at {store_lo}): export the snapshot \
                     with --history-from {} --history-to {}", a.from, a.from, a.to)
        })?;
        if m.hi != a.from || m.lo > h0 || a.to > m.to {
            bail!("the snapshot's farmer history [{}, {}) for rows to {} does not cover [{h0}, {}) for rows to {}", m.lo, m.hi, m.to,
                  a.from, a.to);
        }
        let (rows, kept) = read_farm_hist(&a.snapshot.join("farm_hist.parquet"), &mut adm, &m, h0, &mut hist)?;
        if rows != m.rows {
            bail!("farm_hist.parquet has {rows} rows, its meta.json {}", m.rows);
        }
        log::info!("farmer history [{}, {}): {rows} rows, {kept} from H0 {h0} on, {} keys, RSS {} MB", m.lo, m.hi, hist.len(),
                   crate::mem::mb(crate::mem::rss_bytes()));
        farm = json!({"lo": m.lo, "hi": m.hi, "to": m.to, "rows": rows, "kept": kept, "keys": hist.len()});
    }
    let mut summary = Summary::new(a.from, a.to, a.window);
    let mut file = match &a.out {
        Some(p) => Some(std::io::BufWriter::new(fs::File::create(p).with_context(|| format!("create {}", p.display()))?)),
        None => None,
    };
    let mut ctx: HashMap<Id, PoolCtx> = HashMap::new();
    let mut h24s = Vec::new();
    for (chunk, (p, bl)) in (0u32..).zip(emit.iter().zip(&blocks)) {
        let mut curve = read_curve(p, &mut adm, (a.from, a.to), &mut counts)?;
        let mut pool = read_pool(p, &mut adm, (a.from, a.to), &mut counts)?;
        curve.sort_unstable_by_key(|l| l.order);
        pool.sort_unstable_by_key(|r| r.order);
        check_ties(&curve, &pool).with_context(|| format!("{}", p.dir.display()))?;
        flag_chunk(&mut pool, &mut hist, bl.h24, chunk, keep[chunk as usize]);
        let mut err = None;
        emit_chunk(&adm.keys, &curve, &pool, |s| bl.at(s), &mut ctx, (a.from, a.to), |slot, kind, line| {
            if let Some(f) = file.as_mut().filter(|_| slot >= a.out_from) {
                if let Err(e) = writeln!(f, "{line}") {
                    err.get_or_insert(e);
                }
            }
            summary.add(slot, kind, &line);
        })?;
        if let Some(e) = err {
            return Err(e).context("write the lines");
        }
        log::info!("chunk {}: {} curve and {} pool rows of [{}, {}), h24 {}, {} keys, RSS {} MB", p.dir.display(), curve.len(), pool.len(),
                   a.from.max(p.lo), a.to.min(p.hi), bl.h24, adm.keys.len(), crate::mem::mb(crate::mem::rss_bytes()));
        h24s.push(json!([p.lo, p.hi, bl.h24]));
    }
    if let Some(mut f) = file {
        f.flush()?;
    }
    let mut v = summary.finish();
    v["h24"] = json!(h24s);
    v["h0"] = json!(h0);
    v["farm_hist"] = farm;
    v["chunks_verified"] = json!(true);
    v["admission"] = counts.json();
    v["keys"] = json!(adm.keys.len());
    v["peak_rss_bytes"] = json!(crate::mem::peak_rss_bytes());
    Ok(v)
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::idl::{Kind, Val};
    use crate::sink::write_parquet;
    use arrow_array::{StringArray, UInt64Array};
    use sha2::{Digest, Sha256};

    fn tmp(name: &str) -> PathBuf {
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

    fn put(dir: &Path, table: &str, part: usize, cols: &[(&str, Kind)], rows: &[Vec<Val>]) -> PathBuf {
        let c: Vec<(String, Kind)> = cols.iter().map(|(n, k)| ((*n).to_owned(), *k)).collect();
        write_parquet(dir, table, part, &c, rows).unwrap()
    }

    /// The export (paper/snapshot_export.py) of a store over [1000, 3000): mint M1; pools P1 (normal), P3 (reversed),
    /// PX (quoted in another token: not a SOL market).
    fn export(dir: &Path) {
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
                                                "chunk_h24": [[1000, 2000, 216_000], [2000, 3000, 864]]}).to_string()).unwrap();
    }

    fn edit_meta(dir: &Path, f: impl FnOnce(&mut Value)) {
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

    const TRADE: [(&str, Kind); 23] = [
        ("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64), ("mint", Kind::Utf8),
        ("is_buy", Kind::Bool), ("token_amount", Kind::U64), ("quote_amount", Kind::U64), ("sol_amount", Kind::U64), ("fee", Kind::U64),
        ("creator_fee", Kind::U64), ("parent_ix_disc", Kind::Utf8), ("fee_basis_points", Kind::U64), ("creator_fee_basis_points", Kind::U64),
        ("virtual_token_reserves", Kind::U64), ("virtual_quote_reserves", Kind::U64), ("virtual_sol_reserves", Kind::U64),
        ("real_token_reserves", Kind::U64), ("real_quote_reserves", Kind::U64), ("real_sol_reserves", Kind::U64), ("user", Kind::Utf8),
        ("outer_program", Kind::Utf8), ("parent_ix_args", Kind::Utf8),
    ];

    /// A curve buy of 1,000 tokens for 50 (sol_amount; quote_amount NULL as in pre-v2 rows), fees 2 + 1.
    fn trade(slot: u64, tx: u64, mint: &str, args: Option<&str>) -> Vec<Val> {
        vec![u(slot), u(tx), u(0), u(1), s(mint), Val::Bool(true), u(1_000), Val::Null, u(50), u(2), u(1), s("66063d1201daebea"), u(95),
             u(30), u(900_000), Val::Null, u(30_000), u(700_000), Val::Null, u(20_000), s("T"), s(PUMP), args.map_or(Val::Null, s)]
    }

    fn pool_cols(specific: &[&'static str]) -> Vec<(&'static str, Kind)> {
        let mut c = vec![("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64), ("pool", Kind::Utf8),
                         ("user", Kind::Utf8), ("outer_program", Kind::Utf8), ("parent_ix_disc", Kind::Utf8),
                         ("pool_base_token_reserves", Kind::U64), ("pool_quote_token_reserves", Kind::U64),
                         ("lp_fee_basis_points", Kind::U64), ("protocol_fee_basis_points", Kind::U64), ("coin_creator", Kind::Utf8),
                         ("coin_creator_fee_basis_points", Kind::U64), ("virtual_quote_reserves", Kind::Utf8),
                         ("cashback_fee_basis_points", Kind::U64)];
        c.extend(specific.iter().map(|n| (*n, Kind::U64)));
        c
    }

    const BUY: [&str; 6] = ["base_amount_out", "quote_amount_in_with_lp_fee", "user_quote_amount_in", "quote_amount_in", "max_quote_amount_in",
                            "min_base_amount_out"];
    const SELL: [&str; 4] = ["base_amount_in", "quote_amount_out_without_lp_fee", "user_quote_amount_out", "min_quote_amount_out"];

    /// A pool trade of `base` against `quote` (fees 20/5, no coin creator, virtual quote 7).
    fn ptrade(slot: u64, pool: &str, trader: &str, disc: Option<&str>, base: u64, quote: u64, sell: bool) -> Vec<Val> {
        let mut r = vec![u(slot), u(0), u(0), u(2), s(pool), s(trader), s(PUMP_AMM), disc.map_or(Val::Null, s), u(1_000_000), u(500_000),
                         u(20), u(5), s(DEFAULT_KEY), u(5), s("7"), Val::Null];
        if sell {
            r.extend([u(base), u(quote), u(quote - 1), u(quote - 9)]);
        } else {
            r.extend([u(base), u(quote + 1), u(quote + 2), u(quote), u(quote * 2), u(base / 2)]);
        }
        r
    }

    /// Two chunks of the store, [1000, 2000) at 400 ms a slot (h24 216,000) and [2000, 3000) at 100 s a slot (h24 864):
    /// a curve trade of M1 and one of an unknown mint; P1 trades of B and of the wash trader W (buy 1200, sell 1300,
    /// buy 1400, sell 2100), a P3 buy, a PX buy and a boost-crank inner buy; a P1 boost and withdraw.
    fn chunks(root: &Path) {
        let c1 = root.join("1000-2000");
        let c2 = root.join("2000-3000");
        for (dir, lo, hi) in [(&c1, 1000, 2000), (&c2, 2000, 3000)] {
            fs::create_dir_all(dir).unwrap();
            fs::write(dir.join("_manifest.json"), json!({"status": "complete", "slot_start": lo, "slot_end_exclusive": hi,
                                                          "extractor_sha256": "S"}).to_string()).unwrap();
        }
        let blocks = [("slot", Kind::U64), ("block_time", Kind::I64), ("skipped", Kind::Bool)];
        put(&c1, "blocks", 0, &blocks, &[vec![u(1000), Val::I64(1_000_000), Val::Bool(false)], vec![u(1100), Val::I64(1_000_040), Val::Bool(false)],
                                         vec![u(1200), Val::Null, Val::Bool(true)], vec![u(1900), Val::I64(1_000_360), Val::Bool(false)]]);
        put(&c2, "blocks", 0, &blocks, &[vec![u(2000), Val::I64(2_000_000), Val::Bool(false)], vec![u(2400), Val::I64(2_040_000), Val::Bool(false)]]);
        let args = r#"{"spendable_sol_in": 500, "min_tokens_out": "7", "track_volume": true}"#;
        put(&c1, "pump/TradeEvent", 0, &TRADE, &[trade(1100, 1, "MX", None), trade(1100, 0, "M1", Some(args))]);
        put(&c1, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[
            ptrade(1400, "P1", "W", None, 100, 1_000, false),
            ptrade(1100, "P1", "B", Some("66063d1201daebea"), 40, 400, false),
            ptrade(1150, "PX", "B", None, 40, 400, false),
            ptrade(1160, "P1", "K", Some(BOOST_BUY_DISC), 40, 400, false),
            ptrade(1150, "P3", "R", None, 40, 400, false),
            ptrade(1200, "P1", "W", None, 100, 1_000, false),
        ]);
        put(&c1, "pump_amm/SellEvent", 0, &pool_cols(&SELL), &[ptrade(1300, "P1", "W", None, 100, 1_000, true)]);
        put(&c2, "pump_amm/SellEvent", 0, &pool_cols(&SELL), &[ptrade(2100, "P1", "W", None, 100, 1_000, true)]);
        let boost = ["slot", "tx_index", "outer_ix", "inner_ix", "pool", "authority", "base_reserves_after", "base_amount_burned",
                     "real_quote_reserves_after", "quote_amount_in_used", "virtual_quote_reserves", "boost_vault_remaining"];
        let bk: Vec<(&str, Kind)> = boost.iter().map(|n| (*n, if matches!(*n, "pool" | "authority" | "virtual_quote_reserves") { Kind::Utf8 } else { Kind::U64 })).collect();
        put(&c1, "pump_amm/BoostBuyAndBurnEvent", 0, &bk,
            &[vec![u(1500), u(0), u(0), u(2), s("P1"), s("A"), u(990_000), u(10_000), u(500_300), u(300), s("9"), u(4)]]);
        let wd = ["slot", "tx_index", "outer_ix", "inner_ix", "pool", "user", "outer_program", "pool_base_token_reserves",
                  "pool_quote_token_reserves", "base_amount_out", "quote_amount_out", "lp_token_amount_in", "lp_mint_supply"];
        let wk: Vec<(&str, Kind)> = wd.iter().map(|n| (*n, if matches!(*n, "pool" | "user" | "outer_program") { Kind::Utf8 } else { Kind::U64 })).collect();
        put(&c1, "pump_amm/WithdrawEvent", 0, &wk,
            &[vec![u(1600), u(0), u(0), u(2), s("P1"), s("L"), s(PUMP_AMM), u(990_000), u(500_000), u(9_900), u(5_000), u(77), Val::Null]]);
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
    fn the_hold_out_a_range_outside_the_store_missing_history_ties_and_gaps_are_refused() {
        let root = tmp("refuse");
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        chunks(&root.join("chunks"));
        let err = |a: LegsArgs| format!("{:#}", run(&a).unwrap_err());
        assert!(err(args(&root, HOLDOUT.0 - 10, HOLDOUT.0 + 10)).contains("hold-out"));
        assert!(err(args(&root, HOLDOUT.1 - 1, HOLDOUT.1 + 10)).contains("hold-out"));
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
