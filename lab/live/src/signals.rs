//! `lab-live signals` (M1 C1b of lab/docs/rust-live-route-design-2026-10-16.md; the porting spec is
//! research/m1-c1b-signals-spec-2026-10-19.md §5.3): the paper engine's F7 signal rows of a replay of the store's chunks,
//! in a paper journal that `paper.journal_diff --scope signals` compares with paper.engine's own on the same range.
//!
//!   lab-live signals --chunks-root DIR --snapshot DIR --from SLOT --to SLOT --groups FILE --journal DIR
//!                    [--dev-bounds LO HI] [--summary FILE] [--trace FILE]
//!
//! - The events are `lab-live legs`'s, from the same `Replay` (legs_read.rs) with its refusals. Each goes to lab_core's
//!   SignalEngine, which decides up to the farm gates. Busy and risk are C2/C3: a row past the gates keeps a NULL status
//!   and reason, a gated row is 'skipped' with its gate (spec D10).
//! - Refused besides: a snapshot without meta_bounds or ms_per_slot (an export from before version 2), groups the engine
//!   could not run (signals_core.rs `parse_groups`), and a journal that has signal rows already.
//! - The universe is the snapshot's (signals_core.rs `universe`), built before the snapshot is dropped; the creators are
//!   interned then, in this mode only (so `lab-live legs` keeps C1a's keys).
//! - The dev bounds are the snapshot's meta_bounds (the whole store's blocks), as paper.engine main's (feed.py:100,
//!   engine.py:250). `--dev-bounds` replaces them, e.g. live bounds 0 9223372036854775807; paper.engine has no such flag
//!   yet, so C1b's acceptance runs (lab/live/nas_m1b.sh) do not use it.
//! - The journal (journal.rs, journal.py's schema): the signals rows, INSERT OR REPLACE as the engine's (the last write of
//!   a key wins); state 'groups' = the --groups text verbatim (a trailing newline removed), 'feed' = Python's json.dumps
//!   of the replay, 'lab_live_signals' = the summary. No cost_scenario and no heartbeat (C2/C3).
//! - The summary (also `--summary`): events and event slots, first_slot, the bounds [dev0, dev1, burn, dlo, dhi], the
//!   windows per epoch, universe rejections per cause (quote NULL apart, and the tokens with a NULL quote_mint), trigger and
//!   F7 passes per segment, rows per family and gate, hour rollovers, N2 overwrites, the replay's own (h24, H0, farmer
//!   history, admission, keys), peak RSS and seconds. The `lab-live signals` log line has the counts.
//! - `--trace` (debugging only): `hour <slot> <venue> <key>` per hour rollover (the key whose hour index rolled it) and
//!   `trig <t> <venue> <key> <depth> <farm>` per F7 pass.

use crate::journal::Journal;
use crate::legs::Keys;
use crate::legs_read::Replay;
use crate::signals_core::{self, counts_json, feed_state, parse_groups, row_cells, Cell, KeyNames, Market, Seeds, Token, UniverseLoad};
use anyhow::{bail, Context, Result};
use lab_core::clock::{Bounds, Clock};
use lab_core::farm::WindowRule;
use lab_core::legs::Id;
use lab_core::signals::{SignalEngine, SignalRow, Trace};
use lab_core::universe::{Reject, Universe};
use rusqlite::types::Value as Sql;
use serde_json::{json, Value};
use std::fs;
use std::io::{BufWriter, Write};
use std::path::PathBuf;
use std::time::Instant;

pub struct SignalsArgs {
    pub chunks_root: PathBuf,
    pub snapshot: PathBuf,
    pub from: u64,
    pub to: u64,
    pub groups: PathBuf,
    pub journal: PathBuf,
    pub dev_bounds: Option<(u64, u64)>,
    pub summary: Option<PathBuf>,
    pub trace: Option<PathBuf>,
}

impl KeyNames for Keys {
    fn id(&mut self, key: &str) -> Id {
        Keys::id(self, key)
    }
    fn get(&self, key: &str) -> Option<Id> {
        Keys::get(self, key)
    }
    fn name(&self, id: Id) -> Option<&str> {
        Keys::name(self, id)
    }
    fn len(&self) -> usize {
        Keys::len(self)
    }
}

/// What the snapshot gives the engine: the universe, the clock and the bounds, and what the load saw.
pub(crate) struct Loaded {
    pub universe: Universe,
    pub clock: Clock,
    pub bounds: Bounds,
    pub load: UniverseLoad,
}

/// The universe, clock and bounds from the snapshot (inside `Replay::open`, before the snapshot is dropped).
pub(crate) fn load(snap: &crate::snapshot::Snapshot, keys: &mut Keys, dev_bounds: Option<(u64, u64)>) -> Result<Loaded> {
    let export = "export it again with paper.snapshot_export (snapshot_version 2)";
    let meta_bounds = snap.meta_bounds.with_context(|| format!("the snapshot's meta.json has no meta_bounds: {export}"))?;
    if snap.ms_per_slot.is_empty() {
        bail!("the snapshot's meta.json has no ms_per_slot: {export}");
    }
    let clock = Clock::new(snap.ms_per_slot.clone());
    let bounds = Bounds::new(dev_bounds.unwrap_or(meta_bounds), &clock);
    let tokens: Vec<Token> = snap
        .tokens
        .values()
        .map(|t| Token { mint: &t.mint, create_slot: t.create_slot, creator: t.creator.as_deref(), mayhem: t.mayhem, quote_mint: t.quote_mint.as_deref() })
        .collect();
    let markets = snap
        .pools
        .values()
        .map(|p| {
            let reversed = match p.orientation.as_deref() {
                Some("normal") => false,
                Some("reversed") => true,
                o => bail!("pool {}: orientation {o:?} (normal or reversed expected)", p.pool),
            };
            Ok(Market { pool: &p.pool, mint: &p.mint, reversed, mayhem: p.mayhem, coin_creator: p.coin_creator.as_deref() })
        })
        .collect::<Result<Vec<_>>>()?;
    let (universe, load) = signals_core::universe(keys, bounds, &tokens, &markets);
    Ok(Loaded { universe, clock, bounds, load })
}

/// The rows the engine emitted, into the journal; the trace lines, into the trace file.
fn drain(eng: &mut SignalEngine, rows: &mut Vec<SignalRow>, keys: &Keys, j: &mut Journal, trace: Option<&mut BufWriter<fs::File>>) -> Result<()> {
    for r in rows.drain(..) {
        let cells = row_cells(&r, keys)?;
        let vals: Vec<(&str, Sql)> = cells
            .into_iter()
            .map(|(c, v)| (c, match v {
                Cell::Null => Sql::Null,
                Cell::Int(i) => Sql::Integer(i),
                Cell::Real(x) => Sql::Real(x),
                Cell::Text(t) => Sql::Text(t),
            }))
            .collect();
        j.put("signals", &vals)?;
    }
    if let (Some(f), Some(tr)) = (trace, eng.trace.as_mut()) {
        let name = |k: Id| keys.name(k).unwrap_or("null");
        for x in tr.drain(..) {
            match x {
                Trace::Hour { slot, venue, key } => writeln!(f, "hour {slot} {} {}", venue.as_str(), name(key))?,
                Trace::Trig { t, venue, key, depth, farm } => writeln!(f, "trig {t} {} {} {depth} {}", venue.as_str(), name(key), farm.as_str())?,
            }
        }
    }
    Ok(())
}

/// The replay's signals into the journal (see the module doc); the summary.
pub fn run(a: &SignalsArgs) -> Result<Value> {
    let t0 = Instant::now();
    if a.dev_bounds.is_some_and(|(lo, hi)| lo >= hi) {
        bail!("--dev-bounds LO HI needs LO below HI");
    }
    let raw = fs::read_to_string(&a.groups).with_context(|| format!("read {}", a.groups.display()))?;
    let text = raw.trim_end_matches(['\n', '\r']);
    let groups = parse_groups(text).with_context(|| format!("--groups {}", a.groups.display()))?;
    let (mut replay, loaded) = Replay::open(&a.chunks_root, &a.snapshot, a.from, a.to, |snap, adm| load(snap, &mut adm.keys, a.dev_bounds))?;
    let Loaded { universe, clock, bounds, load } = loaded;
    let mut j = Journal::open(&a.journal)?;
    if j.count("signals")? > 0 {
        bail!("{}: the journal has signal rows already: give a new --journal", a.journal.display());
    }
    j.state("groups", text)?;
    j.state("feed", &feed_state(a.from, a.to, a.dev_bounds))?;
    let names: Vec<String> = groups.specs.iter().map(|g| g.name.clone()).collect();
    let mut eng = SignalEngine::new(universe, clock, groups.specs, WindowRule::AllPools)?;
    let mut trace = match &a.trace {
        Some(p) => {
            eng.trace = Some(Vec::new());
            Some(BufWriter::new(fs::File::create(p).with_context(|| format!("create {}", p.display()))?))
        }
        None => None,
    };
    let (mut seeds, mut rows) = (Seeds::default(), Vec::new());
    replay.for_each_event(|keys, ev| {
        eng.push(ev, &mut seeds.with(keys), &mut |r| rows.push(r))?;
        drain(&mut eng, &mut rows, keys, &mut j, trace.as_mut())
    })?;
    let keys = &replay.adm.keys;
    eng.finish(&mut seeds.with(keys), &mut |r| rows.push(r))?; // the last slot runs too (engine.py:321-323)
    drain(&mut eng, &mut rows, keys, &mut j, trace.as_mut())?;
    if let Some(mut f) = trace {
        f.flush()?;
    }
    let mut v = counts_json(&eng.counts);
    v["from"] = json!(a.from);
    v["to"] = json!(a.to);
    v["first_slot"] = json!(eng.first_slot);
    v["bounds"] = json!([bounds.dev0, bounds.dev1, bounds.burn, bounds.dlo, bounds.dhi]);
    v["dev_bounds_from"] = json!(if a.dev_bounds.is_some() { "--dev-bounds" } else { "meta_bounds" });
    v["groups"] = json!(names);
    v["universe"] = json!({"tokens": load.tokens, "sol_markets": load.markets, "tokens_quote_null": load.quote_null, "interned": load.interned});
    v["journal_rows"] = json!(j.count("signals")?);
    v["n2_seeds"] = json!(seeds.len());
    replay.describe(&mut v);
    v["secs"] = json!(t0.elapsed().as_secs_f64());
    j.state("lab_live_signals", &v.to_string())?;
    j.close()?;
    if let Some(p) = &a.summary {
        fs::write(p, serde_json::to_string_pretty(&v)?).with_context(|| format!("write {}", p.display()))?;
    }
    log::info!("{}", log_line(&v));
    Ok(v)
}

/// The summary's counts in one line: events, rows per family and gate, universe rejections per cause (quote NULL among
/// them) and the tokens with a NULL quote_mint, peak RSS and seconds.
pub fn log_line(v: &Value) -> String {
    let gates = |f: &str| {
        let g = v["rows"][f].as_object().map_or_else(Vec::new, |m| m.iter().map(|(k, n)| format!("{k} {n}")).collect());
        format!("{f} {{{}}}", g.join(", "))
    };
    let rejects: Vec<String> = Reject::ALL.iter().map(|r| format!("{} {}", r.as_str(), v["rejects"][r.as_str()])).collect();
    format!("lab-live signals [{}, {}): {} events in {} slots, {} journal rows; rows {}, {}; universe rejections: {}; tokens with a NULL \
             quote_mint {}; peak RSS {} MB, {:.1} s",
            v["from"], v["to"], v["events"], v["event_slots"], v["journal_rows"], gates("F7"), gates("N2_F7"), rejects.join(", "),
            v["universe"]["tokens_quote_null"], crate::mem::mb(v["peak_rss_bytes"].as_u64()), v["secs"].as_f64().unwrap_or(0.0))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::idl::Val;
    use crate::legs_read::tests::{chunks, edit_meta, export, pool_cols, ptrade, put, tmp, BUY, SELL};
    use crate::signals_core::tests::GROUPS;
    use std::path::Path;

    const SOL: u64 = 1_000_000_000;

    fn args(root: &Path, journal: &str) -> SignalsArgs {
        SignalsArgs {
            chunks_root: root.join("chunks"), snapshot: root.join("snap"), from: 1000, to: 3000, groups: root.join("groups.json"),
            journal: root.join(journal), dev_bounds: None, summary: Some(root.join(format!("{journal}.json"))), trace: None,
        }
    }

    /// legs_read's store of two chunks (its tests' export and chunks) with a trigger in the reversed pool P3: a sell of
    /// 10 SOL by Z at 2500 (inflow), then a buy of 20 SOL by Y at 2501 at depth 300 SOL. With 10^12 ms a slot every
    /// window is one slot: the trigger's F7 passes in [dlo, dhi] = [1001, 2999], and each event slot is an hour of its own.
    pub(crate) fn store(root: &Path) {
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        chunks(&root.join("chunks"));
        let c2 = root.join("chunks/2000-3000");
        let mut sell = ptrade(2500, "P3", "Z", None, 10 * SOL, 1_000, true);
        sell[8] = Val::U64(300 * SOL);
        put(&c2, "pump_amm/SellEvent", 1, &pool_cols(&SELL), &[sell]);
        let mut buy = ptrade(2501, "P3", "Y", None, 20 * SOL, 1_000, false);
        buy[8] = Val::U64(300 * SOL);
        put(&c2, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[buy]);
        fs::write(root.join("groups.json"), format!("{GROUPS}\n")).unwrap();
    }

    #[test]
    fn a_replay_writes_its_signals_the_states_and_the_summary() {
        let root = tmp("signals");
        store(&root);
        let snap = root.join("snap");
        let err = |a: &SignalsArgs| format!("{:#}", run(a).unwrap_err());
        // The fixture's export predates meta_bounds and ms_per_slot (version 2 has both).
        assert!(err(&args(&root, "j0")).contains("no meta_bounds"));
        edit_meta(&snap, |v| v["meta_bounds"] = json!([1000, 3000]));
        assert!(err(&args(&root, "j0")).contains("no ms_per_slot"));
        edit_meta(&snap, |v| v["ms_per_slot"] = json!({"0": 1e12}));
        let v = run(&SignalsArgs { trace: Some(root.join("trace.txt")), ..args(&root, "j") }).unwrap();
        assert_eq!((&v["events"], &v["event_slots"], &v["first_slot"], &v["journal_rows"]), (&json!(11), &json!(10), &json!(1100), &json!(3)));
        assert_eq!((&v["bounds"], &v["dev_bounds_from"], &v["groups"]), (&json!([1000, 3000, 1001, 1001, 2999]), &json!("meta_bounds"), &json!(["K3", "H"])));
        assert_eq!((&v["triggers"]["pool_reversed"], &v["f7"]["pool_reversed"], &v["rows"]["F7"]["pass"]), (&json!(1), &json!(1), &json!(3)));
        assert_eq!((&v["rejects"]["quote not SOL"], &v["rejects"]["quote NULL"], &v["rollovers"]), (&json!(1), &json!(0), &json!(10))); // M1 is quoted in So1
        assert_eq!(v["universe"], json!({"tokens": 1, "sol_markets": 2, "tokens_quote_null": 0, "interned": 2})); // C1, MAYHEM_AGENT
        assert_eq!(serde_json::from_str::<Value>(&fs::read_to_string(root.join("j.json")).unwrap()).unwrap(), v);
        let j = Journal::open(&root.join("j")).unwrap();
        assert_eq!(j.get_state("groups").unwrap().as_deref(), Some(GROUPS)); // verbatim, the trailing newline removed
        assert_eq!(j.get_state("feed").unwrap().as_deref(), Some(r#"{"kind": "replay", "from": 1000, "to": 3000}"#));
        assert_eq!(serde_json::from_str::<Value>(&j.get_state("lab_live_signals").unwrap().unwrap()).unwrap(), v);
        assert_eq!((j.get_state("cost_scenario").unwrap(), j.count("heartbeat").unwrap()), (None, 0));
        drop(j);
        // The rows: K3's and H's F7 rows of the trigger; no N2 (the hour is the slot), the status open past the gates.
        let db = rusqlite::Connection::open(root.join("j/journal.sqlite")).unwrap();
        type Key = (String, String, String, i64, Option<String>, String, String);
        type Cols = (i64, Option<String>, Option<String>, Option<String>, String, Option<i64>, Option<i64>, Option<i64>);
        let rows: Vec<(Key, Cols)> = db
            .prepare("SELECT family, variant, key, t, day, venue, orientation, depth, trigger_trader, status, reason, farm, pool_seen_slot, \
                      farm_window_complete, block_time FROM signals ORDER BY variant").unwrap()
            .query_map([], |r| Ok(((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?),
                                   (r.get(7)?, r.get(8)?, r.get(9)?, r.get(10)?, r.get(11)?, r.get(12)?, r.get(13)?, r.get(14)?)))).unwrap()
            .collect::<rusqlite::Result<_>>().unwrap();
        let row = |variant: &str| (("F7".to_owned(), variant.to_owned(), "P3".to_owned(), 2501, Some("1970-01-12".to_owned()), "pool".to_owned(),
                                    "reversed".to_owned()),
                                   ((300 * SOL) as i64, Some("Y".to_owned()), None, None, "organic".to_owned(), Some(2500), Some(1), None));
        assert_eq!(rows, [row("flow300_H6"), row("flow300_tp100_H6"), row("tp4_T600")]);
        let trace = fs::read_to_string(root.join("trace.txt")).unwrap();
        assert_eq!((trace.lines().filter(|l| l.starts_with("hour ")).count(), trace.lines().next()), (10, Some("hour 1100 curve M1")));
        assert!(trace.lines().any(|l| l == "trig 2501 pool P3 300000000000 organic"), "{trace}");
        // --dev-bounds replaces meta_bounds (here: no decision before 2601), and the feed state names them.
        let v = run(&SignalsArgs { dev_bounds: Some((2600, 3000)), ..args(&root, "jd") }).unwrap();
        assert_eq!((&v["journal_rows"], &v["bounds"], &v["dev_bounds_from"]), (&json!(0), &json!([2600, 3000, 2601, 2601, 2999]), &json!("--dev-bounds")));
        let j = Journal::open(&root.join("jd")).unwrap();
        assert_eq!(j.get_state("feed").unwrap().as_deref(), Some(r#"{"kind": "replay", "from": 1000, "to": 3000, "dev_bounds": [2600, 3000]}"#));
        drop(j);
        // Refused: a journal with rows, groups the engine cannot run, the hold-out (as legs), bounds the wrong way round.
        assert!(err(&args(&root, "j")).contains("signal rows already"));
        fs::write(root.join("groups.json"), GROUPS.replace(r#""primary": false"#, r#""primary": true"#)).unwrap();
        assert!(err(&args(&root, "j2")).contains("exactly one primary"));
        fs::write(root.join("groups.json"), GROUPS).unwrap();
        assert!(err(&SignalsArgs { from: 452_304_000, to: 452_305_000, ..args(&root, "j3") }).contains("hold-out"));
        assert!(err(&SignalsArgs { dev_bounds: Some((5, 5)), ..args(&root, "j4") }).contains("--dev-bounds"));
        let line = log_line(&v);
        assert!(line.contains("quote NULL 0") && line.contains("F7 {farm flagged 0") && line.contains("tokens with a NULL quote_mint 0"), "{line}");
        fs::remove_dir_all(&root).unwrap();
    }
}
