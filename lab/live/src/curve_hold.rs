//! curve_hold.parquet, the curve seed before a replay's from (paper/snapshot_export.py with --history-from/--history-to;
//! M1 C2-5, research/m1-c2-positionsim-spec-2026-10-19.md D4, §3.5, §4.1).
//!
//! The engine seeds a curve position's Holdings with every earlier curve leg of its mint, from the store's first row on
//! (engine.py:891-899, replay.py:663-664, feed.py:108-111). It keeps per trader only the tokens bought minus sold, a leg
//! without is_buy skipped (Holdings.seed, replay.py:225-230), and an entry of 0 acts like none (replay.py:232-247). So
//! the part before `from` is the export's non-zero net per (mint, trader), for every mint with a curve row in
//! [from, to). paper/tests/curve_hold_proof.py proves the file equal to the unmodified engine's seed before `from`
//! (lab/paper/nas_c2_hold.sh: R1 and R2).
//!
//! The rest of the seed is the mint's legs in [from, S] before the insertion index. For an entry at S < to these are
//! all the replay's own legs (C2-4b). Not so for an entry at S >= to: at the end of the data the engine still enters
//! every pending entry (_end_of_data, engine.py:1289-1290; _quiet_entries, engine.py:353-360), and curve_history has no
//! bound at the feed's hi (feed.py:108-111). On a store that goes on past `to` (dev2 does: R1's to is 450380000, the
//! store ends at 452304000) such an entry's seed and fill read store legs in [to, S], which neither this file nor the
//! replay holds. Open for C2-4b/C2-6: export them too, or assert that the reference has no curve entry at S >= to.
//!
//! `load` refuses a file that is not this replay's, as legs_read.rs refuses another replay's farm_hist:
//! - its `hi` must be the replay's from;
//! - its `to` must be at least the replay's to (a later to only adds mints);
//! - its `lo` must be at most the store's start (the seed reaches back to the store's first row);
//! - every row is counted against meta.json;
//! - each row has a mint and a non-zero BIGINT net. The export CASTs the HUGEINT sum: a DOUBLE column, which is what
//!   DuckDB writes for a HUGEINT, is refused (rows.rs `cell`);
//! - each (mint, trader) is there once.
//!
//! A replay from the store's start needs no file: nothing is before it (R2, the whole of fwd1053).

use crate::legs::Keys;
use crate::snapshot::{each_row, signed, text, CurveHoldMeta};
use anyhow::{bail, Context, Result};
use lab_core::legs::{Id, NONE};
use std::collections::HashMap;
use std::path::Path;

/// The curve seed before a replay's from: per mint, each trader's net tokens (bought - sold), never 0. The NULL trader
/// is NONE, one key of its own (as None in Python's dict). Mints and traders are ids of the replay's `Keys`.
#[derive(Debug, Default)]
pub struct CurveHold {
    pub nets: HashMap<Id, HashMap<Id, i128>>,
    pub rows: u64,
}

impl CurveHold {
    /// The mint's nets before from (None: no trader holds a non-zero net).
    pub fn of(&self, mint: Id) -> Option<&HashMap<Id, i128>> {
        self.nets.get(&mint)
    }

    /// A trader's net in a mint before from (0 when none, as Holdings' `.get(trader, 0)`).
    pub fn net(&self, mint: Id, trader: Id) -> i128 {
        self.of(mint).and_then(|m| m.get(&trader)).copied().unwrap_or(0)
    }
}

/// The curve seed of a replay of [from, to) from the snapshot in `dir` (its meta.json `curve_hold` is `m`), the store
/// starting at `store_lo`; see the module doc.
pub fn load(dir: &Path, m: Option<&CurveHoldMeta>, store_lo: u64, from: u64, to: u64, keys: &mut Keys) -> Result<CurveHold> {
    let Some(m) = m else {
        if from > store_lo {
            bail!("curve entries from {from} need the store's curve holdings from its start {store_lo}: export the snapshot with \
                   --history-from {from} --history-to {to}");
        }
        return Ok(CurveHold::default());
    };
    if m.hi != from || m.lo > store_lo || to > m.to {
        bail!("the snapshot's curve holdings [{}, {}) of the mints trading to {} do not cover [{store_lo}, {from}) for rows to {to}",
              m.lo, m.hi, m.to);
    }
    let hold = read(&dir.join("curve_hold.parquet"), keys)?;
    if hold.rows != m.rows {
        bail!("curve_hold.parquet has {} rows, its meta.json {}", hold.rows, m.rows);
    }
    log::info!("curve holdings [{}, {}) of the mints trading to {}: {} rows over {} mints ({} legs without is_buy skipped by the \
                export), RSS {} MB", m.lo, m.hi, m.to, hold.rows, hold.nets.len(), m.skipped_null_is_buy, crate::mem::mb(crate::mem::rss_bytes()));
    Ok(hold)
}

/// Every row of curve_hold.parquet, checked (module doc), into `CurveHold`.
fn read(path: &Path, keys: &mut Keys) -> Result<CurveHold> {
    let mut hold = CurveHold::default();
    each_row(path, &["mint", "trader", "net_t"], |r| {
        let mint = text(&r[0])?.context("curve_hold.parquet: a row without its mint")?;
        let trader = text(&r[1])?;
        let net = signed(&r[2])?.with_context(|| format!("curve_hold.parquet: {mint} {trader:?} without its net"))?;
        if net == 0 {
            bail!("curve_hold.parquet: {mint} {trader:?} has a net of 0 (the export keeps only non-zero nets)");
        }
        let (m, w) = (keys.id(&mint), trader.as_deref().map_or(NONE, |t| keys.id(t)));
        if hold.nets.entry(m).or_default().insert(w, i128::from(net)).is_some() {
            bail!("curve_hold.parquet: {mint} {trader:?} twice");
        }
        hold.rows += 1;
        Ok(())
    })
    .with_context(|| format!("{}", path.display()))?;
    Ok(hold)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::idl::{Kind, Val};
    use crate::sink::write_parquet;
    use arrow_array::{ArrayRef, Float64Array, RecordBatch, StringArray};
    use arrow_schema::{DataType, Field, Schema};
    use parquet::arrow::ArrowWriter;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::Arc;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-live-hold-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn row(mint: Option<&str>, trader: Option<&str>, net: Option<i64>) -> Vec<Val> {
        let s = |x: Option<&str>| x.map_or(Val::Null, |x| Val::Str(x.to_owned()));
        vec![s(mint), s(trader), net.map_or(Val::Null, Val::I64)]
    }

    /// curve_hold.parquet as paper/snapshot_export.py writes it (mint VARCHAR, trader VARCHAR, net_t BIGINT).
    fn file(dir: &Path, rows: &[Vec<Val>]) {
        let cols: Vec<(String, Kind)> = [("mint", Kind::Utf8), ("trader", Kind::Utf8), ("net_t", Kind::I64)].iter()
            .map(|(n, k)| ((*n).to_owned(), *k)).collect();
        let _ = fs::remove_dir_all(dir.join("t"));
        fs::rename(write_parquet(&dir.join("t"), "curve_hold", 0, &cols, rows).unwrap(), dir.join("curve_hold.parquet")).unwrap();
    }

    /// test_snapshot_export's tiny store, replay [2000, 2500) of a store from 1000: its curve_hold rows (TINY_HOLD).
    fn tiny() -> Vec<Vec<Val>> {
        vec![row(Some("A"), Some("W1"), Some(10)), row(Some("A"), Some("W4"), Some((1 << 53) + 1)), row(Some("A"), None, Some(7)),
             row(Some("E"), Some("W1"), Some(-3)), row(Some("G"), Some("W1"), Some(i64::MIN))]
    }

    const META: CurveHoldMeta = CurveHoldMeta { lo: 1000, hi: 2000, to: 2500, rows: 5, skipped_null_is_buy: 1 };

    #[test]
    fn the_nets_per_mint_and_trader_with_the_null_trader_its_own_key() {
        let dir = tmp("read");
        file(&dir, &tiny());
        let mut keys = Keys::default();
        let w1 = keys.id("W1"); // a key the replay interned before: the same id
        let hold = load(&dir, Some(&META), 1000, 2000, 2500, &mut keys).unwrap();
        let (a, e, g) = (keys.get("A").unwrap(), keys.get("E").unwrap(), keys.get("G").unwrap());
        assert_eq!(hold.rows, 5);
        assert_eq!(hold.nets.len(), 3);
        assert_eq!(hold.of(a).unwrap().len(), 3);
        assert_eq!((hold.net(a, w1), hold.net(a, keys.get("W4").unwrap()), hold.net(a, NONE)), (10, (1 << 53) + 1, 7));
        assert_eq!((hold.net(e, w1), hold.net(g, w1)), (-3, i128::from(i64::MIN)));
        // no row: 0, as Holdings reads a missing trader; a mint without rows has none
        assert_eq!((hold.net(e, NONE), hold.net(a, keys.id("W2"))), (0, 0));
        assert!(hold.of(keys.id("B")).is_none());
        // a replay that ends earlier needs no other file (its mints are a subset)
        assert_eq!(load(&dir, Some(&META), 1000, 2000, 2400, &mut keys).unwrap().rows, 5);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn another_replays_file_or_none_is_refused_unless_the_replay_starts_with_the_store() {
        let dir = tmp("meta");
        file(&dir, &tiny());
        let mut keys = Keys::default();
        let err = |m: Option<&CurveHoldMeta>, store_lo: u64, from: u64, to: u64, keys: &mut Keys| {
            format!("{:#}", load(&dir, m, store_lo, from, to, keys).unwrap_err())
        };
        assert!(err(Some(&META), 1000, 1999, 2500, &mut keys).contains("do not cover")); // another from
        assert!(err(Some(&META), 1000, 2001, 2500, &mut keys).contains("do not cover"));
        assert!(err(Some(&META), 1000, 2000, 2501, &mut keys).contains("do not cover")); // mints trading to 2500 only
        assert!(err(Some(&META), 900, 2000, 2500, &mut keys).contains("do not cover")); // the store starts before its lo
        assert!(err(Some(&CurveHoldMeta { rows: 6, ..META }), 1000, 2000, 2500, &mut keys).contains("has 5 rows, its meta.json 6"));
        // no file: refused when there is a store before from, an empty seed when the replay starts with the store
        assert!(err(None, 1000, 2000, 2500, &mut keys).contains("--history-from 2000 --history-to 2500"));
        let none = load(&dir, None, 2000, 2000, 2500, &mut keys).unwrap();
        assert_eq!((none.rows, none.nets.len()), (0, 0));
        // an export from the store's start: an empty file (R2)
        file(&dir, &[]);
        let empty = CurveHoldMeta { lo: 2000, hi: 2000, to: 2500, rows: 0, skipped_null_is_buy: 0 };
        assert_eq!(load(&dir, Some(&empty), 2000, 2000, 2500, &mut keys).unwrap().rows, 0);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_zero_a_missing_or_twice_given_net_and_a_double_column_are_refused() {
        let dir = tmp("bad");
        let mut keys = Keys::default();
        let mut bad = |rows: Vec<Vec<Val>>, why: &str| {
            file(&dir, &rows);
            let m = CurveHoldMeta { rows: rows.len() as u64, ..META };
            let e = format!("{:#}", load(&dir, Some(&m), 1000, 2000, 2500, &mut keys).unwrap_err());
            assert!(e.contains(why), "{why}: {e}");
        };
        bad(vec![row(Some("A"), Some("W2"), Some(0))], "a net of 0");
        bad(vec![row(Some("A"), Some("W2"), None)], "without its net");
        bad(vec![row(None, Some("W2"), Some(1))], "without its mint");
        bad(vec![row(Some("A"), Some("W1"), Some(1)), row(Some("A"), Some("W1"), Some(2))], "twice");
        bad(vec![row(Some("A"), None, Some(1)), row(Some("A"), None, Some(2))], "twice");
        // a HUGEINT sum without the CAST: DuckDB writes a DOUBLE, never read as a net
        let schema = Arc::new(Schema::new(vec![Field::new("mint", DataType::Utf8, true), Field::new("trader", DataType::Utf8, true),
                                               Field::new("net_t", DataType::Float64, true)]));
        let cols: Vec<ArrayRef> = vec![Arc::new(StringArray::from(vec![Some("A")])), Arc::new(StringArray::from(vec![Some("W4")])),
                                       Arc::new(Float64Array::from(vec![9_007_199_254_740_992.0]))];
        let mut w = ArrowWriter::try_new(fs::File::create(dir.join("curve_hold.parquet")).unwrap(), schema.clone(), None).unwrap();
        w.write(&RecordBatch::try_new(schema, cols).unwrap()).unwrap();
        w.close().unwrap();
        let e = format!("{:#}", load(&dir, Some(&CurveHoldMeta { rows: 1, ..META }), 1000, 2000, 2500, &mut keys).unwrap_err());
        assert!(e.contains("unsupported column type"), "{e}");
        fs::remove_dir_all(&dir).unwrap();
    }
}
