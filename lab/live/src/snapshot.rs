//! The store snapshot without DuckDB: the export of paper/snapshot_export.py (#320), read as LiveFeed loads it from
//! the store (paper/live.py `_load_snapshot`, `_init_seed`, `_seed_pool`; lab/docs/rust-live-route-design-2026-10-16.md
//! §3, M1 part 3).
//!
//! - `tokens`: every mint (TokenInfo). With `before` (a live start there) only the mints created before it, and a
//!   graduation at or after it is not known yet (complete_slot and pool empty).
//! - `pool_class`: (quote_class, orientation) of every pool created before `before`; `pools`: the PoolInfo of the SOL
//!   markets among them (quote_class sol or reversed), the only pools the engine trades.
//! - `seed`: the store's last day of pool buys and sells, per pool in LiveFeed's order (slot, tx, outer, inner). A live
//!   start seeds the farmer history of a pool from the window `seed_window` gives (as `_init_seed`), when the pool is
//!   first seen (`seed_trades`, as `_seed_pool`).

use crate::idl::Val;
use crate::rows::cell;
use anyhow::{bail, Context, Result};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

/// The pools the engine trades: SOL quoted, normal or reversed (paper/live.py SOL_MARKETS).
pub const SOL_MARKETS: [&str; 2] = ["sol", "reversed"];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TokenInfo {
    pub mint: String,
    pub create_slot: Option<u64>,
    pub creator: Option<String>,
    pub mayhem: Option<bool>,
    pub quote_mint: Option<String>,
    pub complete_slot: Option<u64>,
    pub pool: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PoolInfo {
    pub pool: String,
    pub mint: String,
    pub orientation: Option<String>,
    pub quote_class: Option<String>,
    pub mayhem: bool,
    pub coin_creator: Option<String>,
    pub create_slot: Option<u64>,
}

/// One pool buy or sell of the seed. The trader is an index into `Snapshot::traders` (a seed day is ~21 M trades of
/// ~25 k pools: interned, the addresses are held once).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SeedTrade {
    pub trader: Option<u32>,
    pub slot: u64,
    pub token_buy: Option<bool>,
    pub token_amount: Option<i64>,
}

/// A live start's farmer-history window (paper/live.py `_init_seed`): [lo, hi), and the slot from which the farm
/// labels have their full day of history.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SeedWindow {
    pub lo: u64,
    pub hi: u64,
    pub h24: u64,
    pub first_live_slot: u64,
    pub complete_from: u64,
}

#[derive(Debug, Default)]
pub struct Snapshot {
    pub tokens: BTreeMap<String, TokenInfo>,
    pub pool_class: BTreeMap<String, (Option<String>, Option<String>)>,
    pub pools: BTreeMap<String, PoolInfo>,
    pub seed: BTreeMap<String, Vec<SeedTrade>>,
    /// The seed's traders, by `SeedTrade::trader`.
    pub traders: Vec<String>,
    /// The store's pool rows: [min slot, max slot + 1).
    pub pool_slots: (u64, u64),
    /// The export's seed window [lo, hi).
    pub export_seed: (u64, u64),
    /// The store's period [slot_start, slot_end_exclusive) (meta.json `store_slots`): a replay of the store's events
    /// (`lab-live legs`) lies inside it.
    pub store_slots: Option<(u64, u64)>,
}

/// Each row of a Parquet file as the extractor's values of `names`, in that order, batch by batch: the file is never
/// held whole (a seed day is ~21 M rows). An export without one of the columns is not this format.
fn each_row(path: &Path, names: &[&str], mut f: impl FnMut(&[Val]) -> Result<()>) -> Result<()> {
    let file = fs::File::open(path).with_context(|| format!("open {}", path.display()))?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(file)?;
    let columns: Vec<String> = builder.schema().fields().iter().map(|f| f.name().clone()).collect();
    let ix = names
        .iter()
        .map(|n| columns.iter().position(|c| c == n).with_context(|| format!("{}: no column {n}", path.display())))
        .collect::<Result<Vec<usize>>>()?;
    let mut row = Vec::with_capacity(ix.len());
    for batch in builder.build()? {
        let batch = batch.with_context(|| format!("read {}", path.display()))?;
        for i in 0..batch.num_rows() {
            row.clear();
            for &c in &ix {
                row.push(cell(batch.column(c).as_ref(), i)?);
            }
            f(&row)?;
        }
    }
    Ok(())
}

fn text(v: &Val) -> Result<Option<String>> {
    Ok(match v {
        Val::Null => None,
        Val::Str(s) => Some(s.clone()),
        other => bail!("expected a string, got {other:?}"),
    })
}

fn unsigned(v: &Val) -> Result<Option<u64>> {
    Ok(match v {
        Val::Null => None,
        Val::U64(x) => Some(*x),
        Val::I64(x) => Some(u64::try_from(*x).context("a negative slot")?),
        other => bail!("expected an integer, got {other:?}"),
    })
}

fn signed(v: &Val) -> Result<Option<i64>> {
    Ok(match v {
        Val::Null => None,
        Val::I64(x) => Some(*x),
        Val::U64(x) => Some(i64::try_from(*x).context("an amount beyond i64")?),
        other => bail!("expected an integer, got {other:?}"),
    })
}

fn flag(v: &Val) -> Result<Option<bool>> {
    Ok(match v {
        Val::Null => None,
        Val::Bool(b) => Some(*b),
        other => bail!("expected a boolean, got {other:?}"),
    })
}

impl Snapshot {
    /// The export in `dir` (meta.json, mints, pools and farm_seed Parquet). before: only what was known before that
    /// slot (paper/live.py `_load_snapshot`).
    pub fn load(dir: &Path, before: Option<u64>) -> Result<Self> {
        Self::read(dir, before, true)
    }

    /// The tokens and pools alone, without the seed day (a replay of the store's events: `lab-live legs`).
    pub fn load_universe(dir: &Path) -> Result<Self> {
        Self::read(dir, None, false)
    }

    fn read(dir: &Path, before: Option<u64>, with_seed: bool) -> Result<Self> {
        let meta: Value = serde_json::from_slice(&fs::read(dir.join("meta.json")).with_context(|| format!("read {}/meta.json", dir.display()))?)
            .with_context(|| format!("{}/meta.json", dir.display()))?;
        let slot = |v: &Value, what: &str| v.as_u64().with_context(|| format!("{}/meta.json: no {what}", dir.display()));
        let mut s = Snapshot {
            pool_slots: (slot(&meta["pool_slots"][0], "pool_slots")?, slot(&meta["pool_slots"][1], "pool_slots")?),
            export_seed: (slot(&meta["farm_seed"]["lo"], "farm_seed.lo")?, slot(&meta["farm_seed"]["hi"], "farm_seed.hi")?),
            store_slots: meta["store_slots"][0].as_u64().zip(meta["store_slots"][1].as_u64()),
            ..Default::default()
        };
        let known = |slot: Option<u64>| before.is_none_or(|b| slot.is_none_or(|x| x < b));

        each_row(&dir.join("mints.parquet"), &["mint", "create_slot", "creator", "mayhem", "quote_mint", "complete_slot", "pool"], |r| {
            let mut tok = TokenInfo {
                mint: text(&r[0])?.context("mints.parquet: a mint without its key")?,
                create_slot: unsigned(&r[1])?,
                creator: text(&r[2])?,
                mayhem: flag(&r[3])?,
                quote_mint: text(&r[4])?,
                complete_slot: unsigned(&r[5])?,
                pool: text(&r[6])?,
            };
            if known(tok.create_slot) {
                if !known(tok.complete_slot) {
                    (tok.complete_slot, tok.pool) = (None, None); // graduated later: not known at the start
                }
                s.tokens.insert(tok.mint.clone(), tok);
            }
            Ok(())
        })?;

        each_row(&dir.join("pools.parquet"), &["pool", "mint", "orientation", "quote_class", "mayhem", "coin_creator", "create_slot"], |r| {
            let p = PoolInfo {
                pool: text(&r[0])?.context("pools.parquet: a pool without its key")?,
                mint: text(&r[1])?.context("pools.parquet: a pool without a mint")?,
                orientation: text(&r[2])?,
                quote_class: text(&r[3])?,
                mayhem: flag(&r[4])?.unwrap_or(false),
                coin_creator: text(&r[5])?,
                create_slot: unsigned(&r[6])?,
            };
            if known(p.create_slot) {
                s.pool_class.insert(p.pool.clone(), (p.quote_class.clone(), p.orientation.clone()));
                if p.quote_class.as_deref().is_some_and(|q| SOL_MARKETS.contains(&q)) {
                    s.pools.insert(p.pool.clone(), p);
                }
            }
            Ok(())
        })?;

        if !with_seed {
            return Ok(s);
        }
        let mut ids: std::collections::HashMap<String, u32> = std::collections::HashMap::new();
        let (seed, traders) = (&mut s.seed, &mut s.traders);
        each_row(&dir.join("farm_seed.parquet"), &["pool", "trader", "slot", "token_buy", "token_amount"], |r| {
            let pool = text(&r[0])?.context("farm_seed.parquet: a trade without its pool")?;
            let slot = unsigned(&r[2])?.context("farm_seed.parquet: a trade without its slot")?;
            let trader = match text(&r[1])? {
                Some(w) => Some(match ids.get(&w) {
                    Some(&id) => id,
                    None => {
                        let id = u32::try_from(traders.len()).context("more than 2^32 seed traders")?;
                        traders.push(w.clone());
                        ids.insert(w, id);
                        id
                    }
                }),
                None => None,
            };
            seed.entry(pool).or_default().push(SeedTrade { trader, slot, token_buy: flag(&r[3])?, token_amount: signed(&r[4])? });
            Ok(())
        })?;
        Ok(s)
    }

    /// The farmer-history window of a live start at `first_slot` (paper/live.py `_init_seed`): [first - h24, first)
    /// as far as the store reaches, complete from `first` when the store reaches it, else a day later. An error when the
    /// export's day of trades does not cover it (a start inside the store's range, more than its last day back).
    pub fn seed_window(&self, first_slot: u64, h24: u64) -> Result<SeedWindow> {
        let (start, end) = self.pool_slots;
        let lo = first_slot.saturating_sub(h24).max(start);
        let hi = first_slot.min(end).max(lo);
        let complete_from = if end >= first_slot { first_slot.max(start + h24) } else { first_slot + h24 };
        if hi > lo && (lo < self.export_seed.0 || hi > self.export_seed.1) {
            bail!("the seed window [{lo}, {hi}) of a start at {first_slot} is not in the export's [{}, {})", self.export_seed.0,
                  self.export_seed.1);
        }
        Ok(SeedWindow { lo, hi, h24, first_live_slot: first_slot, complete_from })
    }

    /// A pool's seed trades in the window, in LiveFeed's order (paper/live.py `_seed_pool`).
    pub fn seed_trades<'a>(&'a self, pool: &str, w: &SeedWindow) -> impl Iterator<Item = &'a SeedTrade> + 'a {
        let (lo, hi) = (w.lo, w.hi);
        self.seed.get(pool).into_iter().flatten().filter(move |t| t.slot >= lo && t.slot < hi)
    }
}

fn opt<T: ToString>(v: &Option<T>) -> String {
    v.as_ref().map_or_else(|| "null".to_owned(), ToString::to_string)
}

/// The snapshot as sorted lines, for the parity test against Python's own load (paper/tests/test_lab_live_snapshot.py):
/// `token`, `class` and `pool` lines, and with a window `seed_window` and `seed_trades <count> <sha256>` over the lines
/// `pool trader slot token_buy token_amount` of every pool's window, pools by name.
pub fn dump(s: &Snapshot, w: Option<&SeedWindow>) -> Vec<String> {
    let mut out = Vec::new();
    for t in s.tokens.values() {
        out.push(format!("token\t{}\t{}\t{}\t{}\t{}\t{}\t{}", t.mint, opt(&t.create_slot), opt(&t.creator), opt(&t.mayhem),
                         opt(&t.quote_mint), opt(&t.complete_slot), opt(&t.pool)));
    }
    for (pool, (class, orientation)) in &s.pool_class {
        out.push(format!("class\t{pool}\t{}\t{}", opt(class), opt(orientation)));
    }
    for p in s.pools.values() {
        out.push(format!("pool\t{}\t{}\t{}\t{}\t{}\t{}\t{}", p.pool, p.mint, opt(&p.orientation), opt(&p.quote_class), p.mayhem,
                         opt(&p.coin_creator), opt(&p.create_slot)));
    }
    if let Some(w) = w {
        out.push(format!("seed_window\t{}\t{}\t{}\t{}\t{}", w.lo, w.hi, w.h24, w.first_live_slot, w.complete_from));
        let (mut h, mut n) = (Sha256::new(), 0u64);
        for pool in s.seed.keys() {
            for t in s.seed_trades(pool, w) {
                let trader = t.trader.map(|i| s.traders[i as usize].as_str());
                h.update(format!("{pool}\t{}\t{}\t{}\t{}\n", opt(&trader), t.slot, opt(&t.token_buy), opt(&t.token_amount)).as_bytes());
                n += 1;
            }
        }
        out.push(format!("seed_trades\t{n}\t{}", h.finalize().iter().map(|b| format!("{b:02x}")).collect::<String>()));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::idl::Kind;
    use crate::sink::write_parquet;
    use std::path::PathBuf;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-live-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn cols(spec: &[(&str, Kind)]) -> Vec<(String, Kind)> {
        spec.iter().map(|(n, k)| ((*n).to_owned(), *k)).collect()
    }

    fn s(x: &str) -> Val {
        Val::Str(x.to_owned())
    }

    /// An export as paper/snapshot_export.py writes it: write_parquet puts a table's part into <dir>/<table>/; the
    /// export's files are <dir>/<name>.parquet.
    fn export(dir: &Path) {
        let put = |name: &str, c: &[(&str, Kind)], rows: &[Vec<Val>]| {
            let f = write_parquet(&dir.join("t"), name, 0, &cols(c), rows).unwrap();
            fs::rename(f, dir.join(format!("{name}.parquet"))).unwrap();
        };
        put("mints", &[("mint", Kind::Utf8), ("create_slot", Kind::U64), ("creator", Kind::Utf8), ("mayhem", Kind::Bool),
                       ("quote_mint", Kind::Utf8), ("complete_slot", Kind::U64), ("pool", Kind::Utf8)],
            &[vec![s("M1"), Val::U64(100), s("C1"), Val::Bool(false), s("So1"), Val::U64(500), s("P1")],
              vec![s("M2"), Val::U64(900), s("C2"), Val::Bool(true), s("PUMP"), Val::Null, Val::Null],
              vec![s("M3"), Val::U64(200), s("C3"), Val::Bool(false), s("So1"), Val::U64(950), s("P3")]]);
        put("pools", &[("pool", Kind::Utf8), ("mint", Kind::Utf8), ("orientation", Kind::Utf8), ("quote_class", Kind::Utf8),
                       ("mayhem", Kind::Bool), ("coin_creator", Kind::Utf8), ("create_slot", Kind::U64)],
            &[vec![s("P1"), s("M1"), s("normal"), s("sol"), Val::Bool(false), s("CC1"), Val::U64(500)],
              vec![s("P3"), s("M3"), s("reversed"), s("reversed"), Val::Bool(false), Val::Null, Val::U64(950)],
              vec![s("PX"), s("MX"), s("normal"), s("other"), Val::Bool(false), Val::Null, Val::U64(10)]]);
        put("farm_seed", &[("pool", Kind::Utf8), ("trader", Kind::Utf8), ("slot", Kind::U64), ("tx_index", Kind::I64),
                           ("outer_ix", Kind::I64), ("inner_ix", Kind::I64), ("token_buy", Kind::Bool), ("token_amount", Kind::I64)],
            &[vec![s("P1"), s("W1"), Val::U64(600), Val::I64(0), Val::I64(0), Val::I64(1), Val::Bool(true), Val::I64(5)],
              vec![s("P1"), s("W2"), Val::U64(900), Val::I64(3), Val::I64(0), Val::I64(1), Val::Bool(false), Val::I64(7)],
              vec![s("P3"), s("W1"), Val::U64(960), Val::I64(1), Val::I64(0), Val::I64(1), Val::Bool(true), Val::I64(9)]]);
        fs::write(dir.join("meta.json"), serde_json::json!({"pool_slots": [500, 1000], "farm_seed": {"lo": 550, "hi": 1000, "h24": 450}})
            .to_string()).unwrap();
    }

    #[test]
    fn the_export_loads_as_livefeed_loads_the_store() {
        let dir = tmp("snap");
        export(&dir);
        let all = Snapshot::load(&dir, None).unwrap();
        assert_eq!(all.tokens.len(), 3);
        assert_eq!(all.tokens["M3"].pool.as_deref(), Some("P3"));
        // Every pool's class; PoolInfo only for the SOL markets (a pool quoted in another token is never traded).
        assert_eq!(all.pool_class.keys().collect::<Vec<_>>(), ["P1", "P3", "PX"]);
        assert_eq!(all.pools.keys().collect::<Vec<_>>(), ["P1", "P3"]);
        assert_eq!(all.pool_class["PX"], (Some("other".into()), Some("normal".into())));
        // A start at 920: M2 (created at 900) is known; M3's graduation (950) and pool P3 (950) are not yet.
        let at = Snapshot::load(&dir, Some(920)).unwrap();
        assert_eq!(at.tokens.keys().collect::<Vec<_>>(), ["M1", "M2", "M3"]);
        assert_eq!((at.tokens["M3"].complete_slot, at.tokens["M3"].pool.clone()), (None, None));
        assert_eq!(at.tokens["M1"].complete_slot, Some(500));
        assert_eq!(at.pools.keys().collect::<Vec<_>>(), ["P1"]);
        let early = Snapshot::load(&dir, Some(150)).unwrap();
        assert_eq!(early.tokens.keys().collect::<Vec<_>>(), ["M1"]);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn the_seed_window_follows_init_seed_and_needs_the_exports_day() {
        let dir = tmp("seed");
        export(&dir);
        let s = Snapshot::load(&dir, None).unwrap();
        // A start after the store: the store's last h24 slots, complete from the start.
        let w = s.seed_window(1000, 450).unwrap();
        assert_eq!(w, SeedWindow { lo: 550, hi: 1000, h24: 450, first_live_slot: 1000, complete_from: 1000 });
        let p1: Vec<(u64, &str)> = s.seed_trades("P1", &w).map(|t| (t.slot, s.traders[t.trader.unwrap() as usize].as_str())).collect();
        assert_eq!(p1, [(600, "W1"), (900, "W2")]);
        assert_eq!(s.traders, ["W1", "W2"]); // W1 of P3 is the same id
        // A start a day after the store's end: an empty window, complete a day after the start.
        let far = s.seed_window(5000, 450).unwrap();
        assert_eq!((far.lo, far.hi, far.complete_from), (4550, 4550, 5450));
        assert_eq!(s.seed_trades("P1", &far).count(), 0);
        // A start inside the store, before the export's day: not covered.
        assert!(s.seed_window(900, 450).is_err());
        let lines = dump(&s, Some(&w));
        assert_eq!(lines.iter().filter(|l| l.starts_with("token\t")).count(), 3);
        assert!(lines.contains(&"pool\tP3\tM3\treversed\treversed\tfalse\tnull\t950".to_owned()));
        assert!(lines.last().unwrap().starts_with("seed_trades\t3\t"));
        fs::remove_dir_all(&dir).unwrap();
    }
}
