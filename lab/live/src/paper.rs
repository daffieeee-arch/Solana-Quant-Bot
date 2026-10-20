//! `lab-live paper` (M1 C2-6 and C3 of the Rust live route; the porting specs are
//! research/m1-c2-positionsim-spec-2026-10-19.md §5.2, §6.4 and research/m1-c3-risk-spec-2026-10-19.md): paper.engine's
//! journal of a replay, with risk on or inert (`--ignore-risk`), from the store's chunks: its signals, orders, fills,
//! exits, positions and heartbeat, which `paper.journal_diff` (default scope) compares with paper.engine's own, and each
//! group's paper wallet file.
//!
//!   lab-live paper (--chunks-root DIR | --segments DIR) --snapshot DIR --from SLOT --to SLOT --groups FILE
//!                  --variants SPEC[,SPEC...] --cost-scenario FILE [--ignore-risk] --journal DIR [--summary FILE] [--trace FILE]
//!
//! - The events are `lab-live signals`'s (legs_read.rs `Replay`, its refusals included: the hold-out, the clean period, a
//!   range outside the store, another extraction); the universe, clock and bounds too (signals.rs `load`). With
//!   `--segments` (M1 C4-1) they come from the recorder's segments instead (the live form; their backfill rows are
//!   counted, not read: the summary's `segments`).
//! - The engine is lab_core's PaperEngine. Without `--ignore-risk` each group's risk file is the one the groups name
//!   (`"risk"`, the path paper.engine journaled), read by risk.rs at every new dated minute and at every entry (C3-4).
//!   Refused before the first event, naming M4 where M4 brings it: risk on with a group whose groups text names no risk
//!   file (paper.engine would raise at its first minute), groups outside M1's three variants or a flow variant outside
//!   [pool_reversed] (exits.rs `check_groups`), and `--variants` that are not exactly the primary group's names as M1's
//!   literal specs (`variant_for_spec`: a tp:T name drops tp's digits, so the spec Python was given is checked, not its
//!   name).
//! - The graduation metadata (each token's complete_slot and pool, each SOL market's create_slot) and the curve seed
//!   before `from` (curve_hold.rs) come from the snapshot.
//! - The journal: every Op as journal.py writes it (paper_core.rs `op_write`; positions are put and deleted); state
//!   'groups' and 'cost_scenario' are the files' text verbatim (a trailing newline removed), 'feed' Python's json.dumps of
//!   the replay, 'lab_live_paper' the summary. No venue_math. The heartbeat rows are the engine's (C3-3); the wallet files
//!   (`<journal>/<wallet>`, C3-3) are written whole as paper.engine writes them. The provenance state (config and risk
//!   sha256, spec D8) is M3's, as is the daily export and report, which Python makes from this journal (spec D3).
//! - The summary (also `--summary`): signals' counters (signals_core.rs `counts_json`), the paper engine's (orders per
//!   status, exits per reason, rows per decision, the risk gate's skips and the 'at entry' orders per group and reason,
//!   curve entries and those with a seed from before `from`, graduation sales, wallet files written), the curve entries
//!   at or after `to` (an entry there would read store legs past the replay: C2-6 requires 0),
//!   journal rows per table, the replay's own part, peak RSS and seconds.
//! - `--trace`: `seed <family> <variant> <key> <t> <sha256>` per curve position at its fill: the sha256 of its holdings
//!   (`<trader> <net>` lines, sorted, the NULL trader as `None`, zero nets left out), which the seed proof compares with
//!   `Holdings.seed(curve_history(mint, S)[:i])`; and `settle <family> <variant> <key> <t> <exit_slot> <reason> <pnl>`
//!   per exit row.
//!
//! M3-11 (research/m3-11-paper-loop-spec-2026-10-20.md v0.3), the paper loop on the follow source, against
//! `paper.engine --live --catch-up` (paper/live_reference.py):
//!
//!   lab-live paper --follow ROOT --snapshot DIR [--snapshot-before SLOT] [--h24 N] --groups FILE --variants SPEC[,SPEC...]
//!                  --cost-scenario FILE [--ignore-risk] --journal DIR [--summary FILE] [--mem-log FILE] [--expect-segments N]
//!
//! - The start (spec §2.1): the snapshot as known before the first live slot, every SOL market's mint interned; an empty
//!   clock (REF_MS until an epoch is measured); LiveFeed's dev bounds (0, 2^63 - 1); no curve seed; the live farm-window
//!   rule. Refused before the first event: a group whose segments are unset or hold curve (the live curve entry is
//!   M3-11b), a segment in the hold-out or the clean period, and a snapshot that would know the store past the first
//!   live slot (`follow::check_snapshot_before`: --snapshot-before after the first segment's first slot, or none with a
//!   store that reaches past it).
//! - The follow source's items (spec §2.2): tokens, SOL markets, epochs' ms per slot and dropped tokens into the engine
//!   at once; backfills and flag fixes at the next slot end; each event pushed with its arrival; the farmer seed's
//!   complete_from for farm_complete. The learned and usage rows wait for the engine's next dated minute (`_live_minute`):
//!   those after the last one are not journaled (no flush at the end), as in Python.
//! - The journal's states: groups, cost_scenario (the files' text), feed (`{"kind": "live", "from": null, "to": null}`),
//!   farmer_seed and farmer_seeds (`_journal_seed`; `store` the snapshot's meta.json store), farm_flag_fixes (at each
//!   dated minute and at the end, when it changed) and the summary. Not yet: the HWM states, open-pools.json, the commit
//!   per minute (M3-12).

use crate::curve_hold;
use crate::follow::{self, py_json, Follow};
use crate::journal::Journal;
use crate::legs::Keys;
use crate::legs_read::{Replay, ReplayMem};
use crate::memlog::{MemLog, Report};
use crate::snapshot::SeedWindow;
use crate::source::{Item, Learned, Source};
use crate::paper_core::{op_write, parse_scenario, Write};
use crate::signals::{load, universe_of};
use crate::signals_core::{counts_json, feed_state, parse_groups, Cell, Groups, KeyNames, Seeds};
use anyhow::{bail, ensure, Context, Result};
use lab_core::clock::{day_of, Bounds, Clock};
use lab_core::exits::{check_groups, variant_for_spec};
use lab_core::farm::WindowRule;
use lab_core::legs::{Id, NONE};
use lab_core::paper::{GradInfo, Op, PaperCfg, PaperEngine, PosKey, RiskSource, SeedTrace};
use lab_core::signals::{SignalEngine, Status};
use lab_core::universe::{MintMeta, PoolMeta, Segment};
use rusqlite::types::Value as Sql;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io::{BufWriter, Write as _};
use std::path::PathBuf;
use std::time::Instant;

pub struct PaperArgs {
    /// M3-11: the follow source over a recorder root instead of the replay (`from`, `to` and the chunks then unused).
    pub follow: Option<FollowOpts>,
    pub chunks_root: PathBuf,
    /// Some: the recorder's segments under this directory instead of the chunks (M1 C4-1, legs_read.rs `open_segments`).
    pub segments: Option<PathBuf>,
    pub snapshot: PathBuf,
    pub from: u64,
    pub to: u64,
    pub groups: PathBuf,
    pub variants: Vec<String>,
    pub cost_scenario: PathBuf,
    pub ignore_risk: bool,
    pub journal: PathBuf,
    pub summary: Option<PathBuf>,
    pub trace: Option<PathBuf>,
    /// `--mem-log FILE` (M1 C4-2, memlog.rs): a JSON line per minute of chain time with the memory and the time per event.
    pub mem_log: Option<PathBuf>,
    /// `--keep-farmer-history` (M1 C4-4): no prune of the farmer histories (legs_read.rs `prune_farm`); for the A/B only.
    pub keep_farmer_history: bool,
}

/// `--follow ROOT` and its own options (M3-11; legs --follow's).
pub struct FollowOpts {
    pub root: PathBuf,
    pub snapshot_before: Option<u64>,
    pub h24: Option<u64>,
    pub expect_segments: Option<u64>,
}

/// State 'feed' of a live run, Python's json.dumps of {"kind": "live", "from": None, "to": None} (engine.py:1493).
pub const LIVE_FEED_STATE: &str = r#"{"kind": "live", "from": null, "to": null}"#;

fn sql(c: Cell) -> Sql {
    match c {
        Cell::Null => Sql::Null,
        Cell::Int(i) => Sql::Integer(i),
        Cell::Real(x) => Sql::Real(x),
        Cell::Text(t) => Sql::Text(t),
    }
}

/// A file's text with its trailing newline removed (the journal holds it verbatim).
fn text(path: &PathBuf, what: &str) -> Result<String> {
    let raw = fs::read_to_string(path).with_context(|| format!("read {what} {}", path.display()))?;
    Ok(raw.trim_end_matches(['\n', '\r']).to_owned())
}

/// The literal `--variants` (as paper.engine was given them) against the primary group's names (spec §1.6): each spec
/// must be exactly one of M1's, and their names the group's, in order.
pub fn check_variants(specs: &[String], primary: &[String]) -> Result<()> {
    let names = specs.iter().map(|s| variant_for_spec(s).map(|v| v.name.to_owned())).collect::<Result<Vec<_>, _>>()?;
    ensure!(names == primary, "--variants {specs:?} name {names:?}, but the primary group trades {primary:?}");
    Ok(())
}

/// The sha256 of a curve position's holdings at its fill (module doc).
pub fn seed_sha(holdings: &[(Id, i128, i128)], names: &impl KeyNames) -> String {
    let mut lines: Vec<String> = holdings
        .iter()
        .filter(|(_, h, _)| *h != 0)
        .map(|&(t, h, c)| {
            assert_eq!(h, c, "a seed has hist == cf");
            format!("{} {h}\n", if t == NONE { "None" } else { names.name(t).unwrap_or("?") })
        })
        .collect();
    lines.sort();
    let mut d = Sha256::new();
    for l in &lines {
        d.update(l.as_bytes());
    }
    format!("{:x}", d.finalize())
}

/// What the run counts besides the engines: journal writes per table, the curve entries at or after `to`.
#[derive(Default)]
struct Tally {
    puts: BTreeMap<&'static str, u64>,
    deletes: u64,
    /// The segment of each entered row's position, for its order (an Op::Order carries no venue).
    entered: HashMap<PosKey, Segment>,
    curve_at_or_after_to: u64,
    /// Wallet files written (each a whole file, replaced).
    wallets: u64,
}

/// The engine's ops into the journal, and its seed and settle lines into the trace.
fn drain(eng: &mut PaperEngine, keys: &Keys, to: u64, j: &mut Journal, tally: &mut Tally, wallets: &[Option<String>],
         mut trace: Option<&mut BufWriter<fs::File>>) -> Result<()> {
    if let (Some(f), Some(seeds)) = (trace.as_deref_mut(), eng.paper.seeds.as_mut()) {
        for SeedTrace { k, holdings } in seeds.drain(..) {
            writeln!(f, "seed {} {} {} {} {}", k.family.as_str(), k.variant, keys.name(k.key).unwrap_or("?"), k.t, seed_sha(&holdings, keys))?;
        }
    }
    for op in eng.paper.ops.drain(..) {
        match &op {
            Op::Signal(row, Status::Entered) => {
                tally.entered.insert(PosKey { family: row.family, variant: row.variant.clone(), key: row.key, t: row.t }, row.seg);
            }
            Op::Order { k, entry_slot, .. } if *entry_slot >= to && tally.entered.get(k) == Some(&Segment::Curve) => {
                tally.curve_at_or_after_to += 1;
            }
            Op::Exit(x) => {
                if let Some(f) = trace.as_deref_mut() {
                    let slot = x.exit_slot.map_or_else(|| "None".to_owned(), |s| s.to_string());
                    writeln!(f, "settle {} {} {} {} {slot} {} {}", x.k.family.as_str(), x.k.variant, keys.name(x.k.key).unwrap_or("?"), x.k.t,
                             x.exit_reason.unwrap_or("None"), x.pnl)?;
                }
            }
            _ => {}
        }
        match op_write(&op, keys)? {
            Write::Put(table, cells) => {
                *tally.puts.entry(table).or_default() += 1;
                let row: Vec<(&str, Sql)> = cells.into_iter().map(|(c, v)| (c, sql(v))).collect();
                j.put(table, &row)?;
            }
            Write::Delete(table, key) => {
                tally.deletes += 1;
                let key: Vec<(&str, Sql)> = key.into_iter().map(|(c, v)| (c, sql(v))).collect();
                j.delete(table, &key)?;
            }
            Write::Wallet(g, text) => {
                // <journal dir>/<wallet file>, replaced whole as engine.py:1279-1282 does (a .tmp, then rename)
                let name = wallets.get(g).cloned().flatten().with_context(|| format!("group {g} has no wallet file"))?;
                let path = j.dir.join(&name);
                let tmp = j.dir.join(format!("{name}.tmp"));
                fs::write(&tmp, text).with_context(|| format!("write {}", tmp.display()))?;
                fs::rename(&tmp, &path).with_context(|| format!("rename to {}", path.display()))?;
                tally.wallets += 1;
            }
        }
    }
    Ok(())
}

/// Each group's risk file (risk on), or inert (`--ignore-risk`).
fn risk_source(ignore: bool, groups: &Groups) -> Result<RiskSource> {
    if ignore {
        return Ok(RiskSource::Ignore);
    }
    let paths: Vec<PathBuf> = groups
        .specs
        .iter()
        .zip(&groups.risk)
        .map(|(g, r)| r.as_ref().map(PathBuf::from).with_context(|| format!("group {}: risk on, but its groups text names no risk file", g.name)))
        .collect::<Result<_>>()?;
    Ok(RiskSource::Files(Box::new(move |g: usize| crate::risk::load(&paths[g]))))
}

/// The replay's paper journal (see the module doc); the summary. With `--follow`, the live paper loop (`run_follow`).
pub fn run(a: &PaperArgs) -> Result<Value> {
    if let Some(fo) = &a.follow {
        return run_follow(a, fo);
    }
    let t0 = Instant::now();
    let groups_text = text(&a.groups, "--groups")?;
    let groups = parse_groups(&groups_text).with_context(|| format!("--groups {}", a.groups.display()))?;
    let risk = risk_source(a.ignore_risk, &groups)?;
    check_groups(&groups.specs).context("--groups")?;
    check_variants(&a.variants, &groups.specs[0].variants)?;
    let scn_text = text(&a.cost_scenario, "--cost-scenario")?;
    let scn = parse_scenario(&scn_text).with_context(|| format!("--cost-scenario {}", a.cost_scenario.display()))?;
    let snapshot = a.snapshot.clone();
    let with_snapshot = |snap: &crate::snapshot::Snapshot, adm: &mut crate::legs_read::Admission| {
        let loaded = load(snap, &mut adm.keys, None)?;
        let mut grad = GradInfo::default();
        for t in snap.tokens.values() {
            let pool = t.pool.as_deref().map(|p| adm.keys.id(p));
            grad.add_token(adm.keys.id(&t.mint), t.complete_slot, pool);
        }
        let store_lo = snap.store_slots.context("the snapshot's meta.json has no store_slots")?.0;
        let hold = curve_hold::load(&snapshot, snap.curve_hold.as_ref(), store_lo, a.from, a.to, &mut adm.keys)?;
        Ok((loaded, grad, hold))
    };
    let (mut replay, (loaded, grad, hold)) = match &a.segments {
        Some(dir) => Replay::open_segments(dir, &a.snapshot, a.from, a.to, with_snapshot)?,
        None => Replay::open(&a.chunks_root, &a.snapshot, a.from, a.to, with_snapshot)?,
    };
    let hold_rows = hold.rows;
    let curve_hold: HashMap<Id, Vec<(Id, i128)>> = hold.nets.into_iter().map(|(m, ts)| (m, ts.into_iter().collect())).collect();
    let bounds = loaded.bounds;
    let mut j = Journal::open(&a.journal)?;
    if j.count("signals")? > 0 || j.count("orders")? > 0 {
        bail!("{}: the journal has rows already: give a new --journal", a.journal.display());
    }
    j.state("groups", &groups_text)?;
    j.state("cost_scenario", &scn_text)?;
    j.state("feed", &feed_state(a.from, a.to, None))?;
    let names: Vec<String> = groups.specs.iter().map(|g| g.name.clone()).collect();
    let sig = SignalEngine::new(loaded.universe, loaded.clock, groups.specs, WindowRule::AllPools)?;
    let mut eng = PaperEngine::with_risk(sig, PaperCfg::new(groups.q, scn, grad, curve_hold), risk)?;
    let mut trace = match &a.trace {
        Some(p) => {
            eng.paper.seeds = Some(Vec::new());
            Some(BufWriter::new(fs::File::create(p).with_context(|| format!("create {}", p.display()))?))
        }
        None => None,
    };
    let (mut seeds, mut tally) = (Seeds::default(), Tally::default());
    let mut memlog = a.mem_log.as_deref().map(MemLog::create).transpose()?;
    replay.mem_log = memlog.is_some();
    replay.prune_farm = !a.keep_farmer_history;
    // memlog.rs: the engines' structures, the N2 seeds, the entered rows' segments and the replay's (per chunk), and the
    // replay's hash tables
    let mems = |eng: &PaperEngine, seeds: &Seeds, tally: &Tally, r: &ReplayMem| {
        let mut v = eng.mem_report();
        v.push(seeds.mem());
        v.push(lab_core::mem::Mem::new("entered_rows", tally.entered.len(), lab_core::mem::map(&tally.entered)));
        v.extend(r.mems.iter().cloned());
        Report { mems: v, tables: r.tables.clone() }
    };
    let mut last_slot = 0;
    // M3-07: through the Source interface, slot by slot; each event is pushed and drained as before
    replay.for_each_item(&mut |keys, rmem, item| {
        match item {
            Item::Slot(sl) => {
                last_slot = sl.slot; // every event of the item is of this slot
                for ev in sl.events {
                    let t = Instant::now();
                    eng.push(ev, &mut seeds.with(keys))?;
                    drain(&mut eng, keys, a.to, &mut j, &mut tally, &groups.wallet, trace.as_mut())?;
                    if let Some(m) = memlog.as_mut() {
                        let ns = u64::try_from(t.elapsed().as_nanos()).unwrap_or(u64::MAX);
                        m.event(ns, eng.paper.minute(), last_slot, rmem.segments, || mems(&eng, &seeds, &tally, rmem))?;
                    }
                }
            }
            // the replay hands out slots only (the follow source's items are run_follow's)
            _ => bail!("the replay handed out an item other than a slot"),
        }
        Ok(())
    })?;
    let keys = &replay.adm.keys;
    eng.finish(&mut seeds.with(keys))?; // the last slot, then the end of data (engine.py:321-324)
    drain(&mut eng, keys, a.to, &mut j, &mut tally, &groups.wallet, trace.as_mut())?;
    // the end line: the engines after finish, the replay after its last chunk or unit
    let mem_lines = match memlog {
        Some(m) => {
            let r = replay.last_mem();
            Some(m.end(eng.paper.minute(), last_slot, r.segments, &mems(&eng, &seeds, &tally, r))?)
        }
        None => None,
    };
    if let Some(mut f) = trace {
        f.flush()?;
    }
    let c = &eng.paper.counts;
    let mut v = counts_json(&eng.sig.counts);
    v["from"] = json!(a.from);
    v["to"] = json!(a.to);
    v["first_slot"] = json!(eng.sig.first_slot);
    v["bounds"] = json!([bounds.dev0, bounds.dev1, bounds.burn, bounds.dlo, bounds.dhi]);
    v["groups"] = json!(names);
    v["variants"] = json!(a.variants);
    v["universe"] = json!({"tokens": loaded.load.tokens, "sol_markets": loaded.load.markets, "tokens_quote_null": loaded.load.quote_null,
                           "interned": loaded.load.interned});
    v["paper"] = json!({
        "entered": c.entered, "busy": c.busy, "not_loaded": c.not_loaded, "orders": c.orders, "exits": c.exits,
        "curve_entries": c.curve_entries, "curve_seeded": c.curve_seeded, "curve_hold_rows": hold_rows,
        "seed_pool_sales": c.seed_pool_sales, "pool_sales": c.pool_sales, "max_open": c.max_open,
        "curve_entries_at_or_after_to": tally.curve_at_or_after_to, "wallet_files_written": tally.wallets,
        "risk": if a.ignore_risk { "inert" } else { "files" }, "risk_skips": c.risk_skips, "at_entry": c.at_entry,
    });
    v["journal"] = json!({"puts": tally.puts, "deletes": tally.deletes, "signals": j.count("signals")?, "orders": j.count("orders")?,
                          "fills": j.count("fills")?, "exits": j.count("exits")?, "positions": j.count("positions")?});
    v["n2_seeds"] = json!(seeds.len());
    if let (Some(p), Some(n)) = (&a.mem_log, mem_lines) {
        v["mem_log"] = json!({"file": p.display().to_string(), "lines": n, "heap_bytes_at_end": crate::mem::heap_bytes()});
    }
    replay.describe(&mut v);
    v["secs"] = json!(t0.elapsed().as_secs_f64());
    j.state("lab_live_paper", &v.to_string())?;
    j.close()?;
    if let Some(p) = &a.summary {
        fs::write(p, serde_json::to_string_pretty(&v)?).with_context(|| format!("write {}", p.display()))?;
    }
    log::info!("{}", log_line(&v));
    Ok(v)
}

/// The live loop's own part beside the engine (M3-11 spec §2.2, §4): the learned and usage rows waiting for the engine's
/// next dated minute, the minute last seen, the farmer seed until its first event, the flag-fix days last written, and
/// what it handed on.
#[derive(Default)]
struct Live {
    learned: Vec<Learned>,
    usage: Vec<Value>,
    minute: Option<i64>,
    seed: Option<SeedWindow>,
    seed_journaled: bool,
    store: String,
    fixes_written: BTreeMap<Option<String>, [u64; 2]>,
    items: BTreeMap<&'static str, u64>,
    rows: BTreeMap<&'static str, u64>,
}

/// A JSON value as the journal's cell (Python's sqlite3: None, int, float or str).
fn json_sql(v: &Value) -> Result<Sql> {
    Ok(match v {
        Value::Null => Sql::Null,
        Value::Bool(b) => Sql::Integer(i64::from(*b)),
        Value::Number(n) => match n.as_i64() {
            Some(i) => Sql::Integer(i),
            None => Sql::Real(n.as_f64().with_context(|| format!("{n} beyond INTEGER"))?),
        },
        Value::String(t) => Sql::Text(t.clone()),
        other => bail!("a usage field {other} is no journal cell"),
    })
}

/// State farm_flag_fixes' text (engine.py `json.dumps(self.flag_fix_days, sort_keys=True)`): a day of None as "null".
pub fn flag_fixes_state(days: &BTreeMap<Option<String>, [u64; 2]>) -> String {
    let m: serde_json::Map<String, Value> = days.iter().map(|(d, n)| (d.clone().unwrap_or_else(|| "null".to_owned()), json!(n))).collect();
    py_json(&Value::Object(m))
}

impl Live {
    /// `_journal_seed` (engine.py:333-351) at the first event after the seed: farmer_seed (the first start's) and
    /// farmer_seeds (every start's once; a fresh journal: this one), Python's json.dumps with sort_keys.
    fn journal_seed(&mut self, j: &mut Journal) -> Result<()> {
        let Some(w) = self.seed.filter(|_| !self.seed_journaled) else { return Ok(()) };
        self.seed_journaled = true;
        let seed = json!({"store": self.store, "lo": w.lo, "hi": w.hi, "h24": w.h24, "first_live_slot": w.first_live_slot,
                          "complete_from": w.complete_from});
        j.state("farmer_seed", &py_json(&seed))?;
        j.state("farmer_seeds", &py_json(&json!([seed])))?;
        Ok(())
    }

    /// `_live_minute` (engine.py:482-496) when the engine's dated minute moved on in the slot end just run: the usage rows
    /// (their own minute, else this one), farm_flag_fixes when it changed, the learned rows.
    fn minute(&mut self, eng: &PaperEngine, j: &mut Journal) -> Result<()> {
        let minute = eng.paper.minute();
        if minute == self.minute {
            return Ok(());
        }
        self.minute = minute;
        let Some(now) = minute else { return Ok(()) };
        for u in self.usage.drain(..) {
            let m = match u.get("minute") {
                None => now,
                Some(v) => v.as_i64().with_context(|| format!("a usage row's minute {v}: Python's _day would fail"))?,
            };
            let field = |k: &str| json_sql(u.get(k).unwrap_or(&Value::Null));
            j.put("usage", &[("minute", Sql::Integer(m)), ("day", day_of(m * 60).map_or(Sql::Null, Sql::Text)), ("stream", field("stream")?),
                             ("bytes", field("bytes")?), ("messages", field("messages")?), ("seq", field("seq")?), ("note", field("note")?)])?;
            *self.rows.entry("usage").or_default() += 1;
        }
        self.fixes(eng, j)?;
        for l in self.learned.drain(..) {
            j.put("learned", &[("kind", Sql::Text(l.kind.to_owned())), ("key", Sql::Text(l.key)), ("seq", Sql::Integer(i64::try_from(l.seq)?)),
                               ("slot", l.slot.map_or(Ok(Sql::Null), |s| i64::try_from(s).map(Sql::Integer))?), ("info", Sql::Text(l.info))])?;
            *self.rows.entry("learned").or_default() += 1;
        }
        Ok(())
    }

    /// State farm_flag_fixes when the counts changed since it was last written (`_live_minute`, `_close_day`).
    fn fixes(&mut self, eng: &PaperEngine, j: &mut Journal) -> Result<()> {
        let days = eng.paper.flag_fix_days();
        if *days != self.fixes_written {
            j.state("farm_flag_fixes", &flag_fixes_state(days))?;
            self.fixes_written = days.clone();
        }
        Ok(())
    }
}

/// M3-11: the live paper loop on the follow source (module doc); the summary.
fn run_follow(a: &PaperArgs, fo: &FollowOpts) -> Result<Value> {
    let t0 = Instant::now();
    let groups_text = text(&a.groups, "--groups")?;
    let groups = parse_groups(&groups_text).with_context(|| format!("--groups {}", a.groups.display()))?;
    // spec v0.2 #1: the live curve entry is M3-11b; a group that could trade a curve must not run without it
    for g in &groups.specs {
        if g.segments.is_none_or(|s| s[Segment::Curve.index()]) {
            bail!("group {}: its segments are unset or hold curve, and the live curve entry is not ported (M3-11b): refused with --follow",
                  g.name);
        }
    }
    let risk = risk_source(a.ignore_risk, &groups)?;
    check_groups(&groups.specs).context("--groups")?;
    check_variants(&a.variants, &groups.specs[0].variants)?;
    let scn_text = text(&a.cost_scenario, "--cost-scenario")?;
    let scn = parse_scenario(&scn_text).with_context(|| format!("--cost-scenario {}", a.cost_scenario.display()))?;
    let segs = follow::locked_out(&fo.root)?;
    let meta: Value = serde_json::from_slice(&fs::read(a.snapshot.join("meta.json")).with_context(|| format!("read {}/meta.json", a.snapshot.display()))?)?;
    let snap = follow::snapshot_known(&a.snapshot, fo.snapshot_before)?;
    follow::check_snapshot_before(&snap, fo.snapshot_before, &segs)?;
    let (n_tokens, n_classed) = (snap.tokens.len(), snap.pool_class.len());
    // spec §2.1: LiveFeed's meta_bounds (0, 2^63 - 1) on an empty clock; every SOL market's mint interned (v0.3)
    let clock = Clock::default();
    let bounds = Bounds::new((0, i64::MAX as u64), &clock);
    let (mut src, (universe, grad, load)) = Follow::open_with(&fo.root, snap, fo.h24, true, |snap, adm| {
        for p in snap.pools.values() {
            adm.keys.id(&p.mint);
        }
        let (universe, load) = universe_of(snap, &mut adm.keys, bounds)?;
        let mut grad = GradInfo::default();
        for t in snap.tokens.values() {
            let pool = t.pool.as_deref().map(|p| adm.keys.id(p));
            grad.add_token(adm.keys.id(&t.mint), t.complete_slot, pool);
        }
        Ok((universe, grad, load))
    })?;
    let mut j = Journal::open(&a.journal)?;
    if j.count("signals")? > 0 || j.count("orders")? > 0 {
        bail!("{}: the journal has rows already: give a new --journal", a.journal.display());
    }
    j.state("groups", &groups_text)?;
    j.state("cost_scenario", &scn_text)?;
    j.state("feed", LIVE_FEED_STATE)?;
    let names: Vec<String> = groups.specs.iter().map(|g| g.name.clone()).collect();
    let sig = SignalEngine::new(universe, clock, groups.specs, WindowRule::Live)?;
    let mut eng = PaperEngine::with_risk(sig, PaperCfg::new(groups.q, scn, grad, HashMap::new()), risk)?;
    let (mut seeds, mut tally) = (Seeds::default(), Tally::default());
    let store = meta["store"].as_str().context("the snapshot's meta.json has no store (paper.snapshot_export writes it): farmer_seed names it")?;
    let mut live = Live { store: store.to_owned(), ..Live::default() };
    let mut memlog = a.mem_log.as_deref().map(MemLog::create).transpose()?;
    src.mem_log = memlog.is_some();
    let mems = |eng: &PaperEngine, seeds: &Seeds, tally: &Tally, r: &ReplayMem| {
        let mut v = eng.mem_report();
        v.push(seeds.mem());
        v.push(lab_core::mem::Mem::new("entered_rows", tally.entered.len(), lab_core::mem::map(&tally.entered)));
        v.extend(r.mems.iter().cloned());
        Report { mems: v, tables: r.tables.clone() }
    };
    let mut last_slot = 0;
    src.for_each_item(&mut |keys, rmem, item| {
        let kind = match item {
            Item::Slot(sl) => {
                last_slot = sl.slot;
                let arr = sl.arrivals.context("the follow source gives every event its arrival")?;
                for (ev, arrival) in sl.events.iter().zip(arr) {
                    let t = Instant::now();
                    eng.push_arrived(ev, Some(arrival), &mut seeds.with(keys))?;
                    live.journal_seed(&mut j)?; // engine.py:315-318: after the event joined its slot
                    drain(&mut eng, keys, u64::MAX, &mut j, &mut tally, &groups.wallet, None)?;
                    live.minute(&eng, &mut j)?;
                    if let Some(m) = memlog.as_mut() {
                        let ns = u64::try_from(t.elapsed().as_nanos()).unwrap_or(u64::MAX);
                        m.event(ns, eng.paper.minute(), last_slot, rmem.segments, || mems(&eng, &seeds, &tally, rmem))?;
                    }
                }
                "slot"
            }
            Item::Token(t) => {
                let mint = keys.get(&t.mint).context("a token learned without its key")?;
                let creator = t.creator.as_deref().map_or(NONE, |c| keys.get(c).unwrap_or(NONE));
                ensure!(t.creator.is_none() || creator != NONE, "token {}: its creator has no key", t.mint);
                let pool = t.pool.as_deref().map(|p| keys.get(p).context("a token's pool without its key")).transpose()?;
                eng.learn_token(mint, MintMeta::new(t.create_slot, creator, t.mayhem, t.quote_mint.as_deref()), t.complete_slot, pool);
                "token"
            }
            Item::Pool(p) => {
                let reversed = match p.orientation.as_deref() {
                    Some("normal") => false,
                    Some("reversed") => true,
                    o => bail!("pool {}: orientation {o:?} (normal or reversed expected)", p.pool),
                };
                let id = keys.get(&p.pool).context("a pool learned without its key")?;
                let coin_creator = p.coin_creator.as_deref().map_or(Ok(NONE), |c| keys.get(c).context("a coin creator without its key"))?;
                let mint_tok = Some(keys.get(&p.mint).context("a pool's mint without its key")?);
                eng.learn_pool(id, PoolMeta { reversed, mayhem: p.mayhem, mint_tok, coin_creator, create_slot: p.create_slot });
                "pool"
            }
            Item::EpochMs { epoch, ms } => {
                eng.set_epoch_ms(epoch, ms);
                "epoch_ms"
            }
            Item::Learned(l) => {
                if l.kind == "late_probe" {
                    // creation-filters spec v0.2 §4 option A: the engine's farm window for it (LiveFeed's
                    // late_probe_pools, read by engine.py `_farm_window_complete`); a pool the run never interned has no row
                    if let Some(id) = keys.get(&l.key) {
                        eng.mark_late_probe(id);
                    }
                }
                live.learned.push(l.clone());
                "learned"
            }
            Item::History { pool, legs } => {
                eng.add_history(pool, legs.to_vec());
                "history"
            }
            Item::FlagFix { pool, order, farmer } => {
                eng.fix_farm(pool, order, farmer);
                "flag_fix"
            }
            Item::Usage(u) => {
                live.usage.push(u.clone());
                "usage"
            }
            Item::Segment { .. } => "segment",
            Item::FarmSeed(w) => {
                ensure!(live.seed.is_none(), "a second farmer seed");
                eng.sig.farm_complete_from = Some(w.complete_from);
                live.seed = Some(w);
                "farm_seed"
            }
            Item::TokenForget { mint } => {
                eng.forget_token(mint);
                "token_forget"
            }
        };
        *live.items.entry(kind).or_default() += 1;
        Ok(())
    })?;
    let keys = &src.adm.keys;
    eng.finish(&mut seeds.with(keys))?; // the last slot, then the end of data (engine.py:321-326)
    drain(&mut eng, keys, u64::MAX, &mut j, &mut tally, &groups.wallet, None)?;
    live.minute(&eng, &mut j)?; // a minute the last slot end started
    // `_end_of_data`'s `_close_day` when the run has a day (engine.py:1307-1308: `if self.day`); the learned and usage rows
    // still waiting stay unwritten
    if eng.sig.day.is_some() {
        live.fixes(&eng, &mut j)?;
    }
    let read = src.stats.segments;
    ensure!(read > 0, "the follow source read no complete segment under {}/segments: refused", fo.root.display());
    if let Some(n) = fo.expect_segments.filter(|&n| n != read) {
        bail!("the follow source read {read} complete segments, not the {n} expected: refused");
    }
    ensure!(eng.sig.counts.events > 0, "no event: an empty run is no result: refused");
    // spec v0.3 §5.2: one TokenForget item per dropped token, never one lost
    let forgets = live.items.get("token_forget").copied().unwrap_or(0);
    ensure!(forgets == src.stats.tokens_dropped, "{forgets} TokenForget items for {} tokens dropped", src.stats.tokens_dropped);
    // the end line: the engine after finish, the source after its last segment
    let mem_lines = match memlog {
        Some(m) => {
            let r = src.mem();
            Some(m.end(eng.paper.minute(), last_slot, r.segments, &mems(&eng, &seeds, &tally, &r))?)
        }
        None => None,
    };
    let c = &eng.paper.counts;
    let mut v = counts_json(&eng.sig.counts);
    v["first_slot"] = json!(eng.sig.first_slot);
    v["bounds"] = json!([bounds.dev0, bounds.dev1, bounds.burn, bounds.dlo, bounds.dhi]);
    v["groups"] = json!(names);
    v["variants"] = json!(a.variants);
    v["universe"] = json!({"tokens": load.tokens, "sol_markets": load.markets, "tokens_quote_null": load.quote_null, "interned": load.interned});
    v["paper"] = json!({
        "entered": c.entered, "busy": c.busy, "not_loaded": c.not_loaded, "orders": c.orders, "exits": c.exits,
        "curve_entries": c.curve_entries, "seed_pool_sales": c.seed_pool_sales, "pool_sales": c.pool_sales, "max_open": c.max_open,
        "wallet_files_written": tally.wallets, "risk": if a.ignore_risk { "inert" } else { "files" }, "risk_skips": c.risk_skips,
        "at_entry": c.at_entry, "flag_fix_days": flag_fixes_state(eng.paper.flag_fix_days()),
    });
    v["journal"] = json!({"puts": tally.puts, "deletes": tally.deletes, "signals": j.count("signals")?, "orders": j.count("orders")?,
                          "fills": j.count("fills")?, "exits": j.count("exits")?, "positions": j.count("positions")?,
                          "learned": j.count("learned")?, "usage": j.count("usage")?});
    v["n2_seeds"] = json!(seeds.len());
    v["follow"] = json!({
        "root": fo.root.display().to_string(), "snapshot_before": fo.snapshot_before, "h24": fo.h24, "segments_listed": segs.len(),
        "snapshot": {"tokens": n_tokens, "pools_classed": n_classed}, "stats": src.stats.json(), "admission": src.counts.json(),
        "keys": src.adm.keys.len(), "items": live.items, "rows_written": live.rows,
        "waiting_at_end": {"learned": live.learned.len(), "usage": live.usage.len()},
        "farm_seed": live.seed.map_or(Value::Null, |w| json!({"lo": w.lo, "hi": w.hi, "h24": w.h24, "first_live_slot": w.first_live_slot,
                                                               "complete_from": w.complete_from})),
        "token_forgets": forgets,
    });
    if let (Some(p), Some(n)) = (&a.mem_log, mem_lines) {
        v["mem_log"] = json!({"file": p.display().to_string(), "lines": n, "heap_bytes_at_end": crate::mem::heap_bytes()});
    }
    v["peak_rss_bytes"] = json!(crate::mem::peak_rss_bytes());
    v["secs"] = json!(t0.elapsed().as_secs_f64());
    j.state("lab_live_paper", &v.to_string())?;
    j.close()?;
    if let Some(p) = &a.summary {
        fs::write(p, serde_json::to_string_pretty(&v)?).with_context(|| format!("write {}", p.display()))?;
    }
    log::info!("{}", log_line_follow(&v));
    Ok(v)
}

/// The live loop's summary in one line.
pub fn log_line_follow(v: &Value) -> String {
    let jr = &v["journal"];
    format!("lab-live paper --follow: {} segments, {} events; journal signals {}, orders {}, fills {}, exits {}, learned {}, usage {}; \
             orders {}; exits {}; tokens forgotten {}; peak RSS {} MB, {:.1} s",
            v["follow"]["stats"]["segments"], v["events"], jr["signals"], jr["orders"], jr["fills"], jr["exits"], jr["learned"], jr["usage"],
            v["paper"]["orders"], v["paper"]["exits"], v["follow"]["token_forgets"],
            v["peak_rss_bytes"].as_u64().unwrap_or(0) >> 20, v["secs"].as_f64().unwrap_or(0.0))
}

/// The summary in one line: events, journal rows per table, orders per status, exits per reason, curve entries (with a seed
/// from before from; at or after to), peak RSS and seconds.
pub fn log_line(v: &Value) -> String {
    let jr = &v["journal"];
    format!("lab-live paper [{}, {}): {} events in {} slots; risk {}; journal signals {}, orders {}, fills {}, exits {}, positions {}; \
             orders {}; exits {}; risk skips {}; at entry {}; curve entries {} ({} seeded from before from, {} at or after to); \
             peak RSS {} MB, {:.1} s",
            v["from"], v["to"], v["events"], v["event_slots"], v["paper"]["risk"], jr["signals"], jr["orders"], jr["fills"], jr["exits"],
            jr["positions"], v["paper"]["orders"], v["paper"]["exits"], v["paper"]["risk_skips"], v["paper"]["at_entry"],
            v["paper"]["curve_entries"], v["paper"]["curve_seeded"], v["paper"]["curve_entries_at_or_after_to"],
            crate::mem::mb(v["peak_rss_bytes"].as_u64()), v["secs"].as_f64().unwrap_or(0.0))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::legs_read::tests::{edit_meta, tmp};
    use crate::paper_core::BASE_SCENARIO;
    use crate::signals::tests::store;
    use crate::signals_core::tests::{CaseKeys, GROUPS};
    use std::path::Path;

    fn args(root: &Path, journal: &str) -> PaperArgs {
        PaperArgs {
            follow: None, chunks_root: root.join("chunks"), segments: None, snapshot: root.join("snap"), from: 1000, to: 3000, groups: root.join("groups.json"),
            variants: vec!["0.04:600".into()], cost_scenario: root.join("scn.json"), ignore_risk: true, journal: root.join(journal),
            summary: Some(root.join(format!("{journal}.json"))), trace: None, mem_log: None,
            keep_farmer_history: false,
        }
    }

    #[test]
    fn with_risk_on_each_group_reads_its_own_file() {
        // C3-4: no --ignore-risk. K3's file has the kill switch on, H's not: P3's K3 row (2501) is skipped 'kill switch',
        // H's two rows enter. Only H's wallet changed, so only wallet-h.json is written.
        let root = tmp("paper-risk");
        store(&root);
        edit_meta(&root.join("snap"), |v| {
            v["meta_bounds"] = json!([1000, 3000]);
            v["ms_per_slot"] = json!({"0": 1e12});
        });
        fs::write(root.join("scn.json"), format!("{BASE_SCENARIO}\n")).unwrap();
        let file = |kill: bool| format!("size_sol: 0.5\nmax_open_positions: 5\nmax_exposure_sol: 2.5\ndaily_loss_limit_sol: 1.0\nkill_switch: {kill}\n\
                                         families: [F7]\npool_cooldown_h: 24\n");
        fs::write(root.join("risk.yaml"), file(true)).unwrap();
        fs::write(root.join("risk-h.yaml"), file(false)).unwrap();
        let groups = GROUPS.replace("/x/risk.yaml", &root.join("risk.yaml").display().to_string())
            .replace("/x/risk-h.yaml", &root.join("risk-h.yaml").display().to_string());
        fs::write(root.join("risk-groups.json"), &groups).unwrap();
        let v = run(&PaperArgs { groups: root.join("risk-groups.json"), ignore_risk: false, ..args(&root, "jr") }).unwrap();
        let p = &v["paper"];
        assert_eq!((&p["risk"], &p["risk_skips"], &p["entered"], &p["at_entry"]), (&json!("files"), &json!({"K3: kill switch": 1}), &json!(2), &json!({})), "{p}");
        assert!(root.join("jr").join("wallet-h.json").exists() && !root.join("jr").join("wallet.json").exists());
        assert!(log_line(&v).contains("risk \"files\"") || log_line(&v).contains("risk files"), "{}", log_line(&v));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_variants_must_be_m1s_specs_of_the_primary_groups_names() {
        let k3 = vec!["tp4_T600".to_owned()];
        assert!(check_variants(&["0.04:600".into()], &k3).is_ok());
        assert!(check_variants(&["0.040:600".into()], &k3).is_ok());
        let e = format!("{:#}", check_variants(&["0.045:600".into()], &k3).unwrap_err()); // also named tp4_T600 by Python
        assert!(e.contains("0.045:600") && e.contains("M4"), "{e}");
        let e = format!("{:#}", check_variants(&["flow300_H6".into()], &k3).unwrap_err());
        assert!(e.contains("primary group trades"), "{e}");
        assert!(check_variants(&["0.04:600".into(), "0.04:600".into()], &k3).is_err());
    }

    #[test]
    fn the_seed_hash_is_over_the_sorted_non_zero_nets() {
        // lab-py: hashlib.sha256(b"A 3\nNone 5\n").hexdigest(); the empty seed hashes nothing.
        let mut keys = CaseKeys::default();
        let a = keys.id("A");
        let b = keys.id("B");
        assert_eq!(seed_sha(&[(NONE, 5, 5), (b, 0, 0), (a, 3, 3)], &keys), "28e498e804832e66a8e61f51bef07a5f2429392794fb16539b740d56123494fb");
        assert_eq!(seed_sha(&[], &keys), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    }

    #[test]
    fn a_replay_writes_its_paper_journal_the_states_and_the_summary() {
        // signals.rs's store: the reversed pool P3 triggers at 2501 (K3 and H's two rows pass the gates); with 1e12 ms a
        // slot every delay and window is one slot. Each row enters, opens at 2502 and the end of data closes it.
        let root = tmp("paper");
        store(&root);
        edit_meta(&root.join("snap"), |v| {
            v["meta_bounds"] = json!([1000, 3000]);
            v["ms_per_slot"] = json!({"0": 1e12});
        });
        fs::write(root.join("scn.json"), format!("{BASE_SCENARIO}\n")).unwrap();
        let v = run(&PaperArgs { trace: Some(root.join("trace.txt")), ..args(&root, "j") }).unwrap();
        let p = &v["paper"];
        assert_eq!((&p["entered"], &p["busy"], &p["not_loaded"], &p["curve_entries_at_or_after_to"]), (&json!(3), &json!(0), &json!(0), &json!(0)));
        let orders: u64 = p["orders"].as_object().unwrap().values().map(|n| n.as_u64().unwrap()).sum();
        let filled = p["orders"]["filled"].as_u64().unwrap_or(0);
        let failed = p["orders"]["entry_failed"].as_u64().unwrap_or(0);
        let jr = &v["journal"];
        assert_eq!((orders, &jr["orders"], &jr["signals"], &jr["positions"]), (3, &json!(3), &json!(3), &json!(0)));
        assert_eq!((jr["exits"].as_u64(), jr["deletes"].as_u64()), (Some(filled + failed), Some(filled)));
        assert_eq!(jr["fills"].as_u64(), Some(2 * filled));
        assert_eq!(serde_json::from_str::<Value>(&fs::read_to_string(root.join("j.json")).unwrap()).unwrap(), v);
        let j = Journal::open(&root.join("j")).unwrap();
        assert_eq!(j.get_state("groups").unwrap().as_deref(), Some(GROUPS)); // verbatim, the trailing newline removed
        assert_eq!(j.get_state("cost_scenario").unwrap().as_deref(), Some(BASE_SCENARIO));
        assert_eq!(j.get_state("feed").unwrap().as_deref(), Some(r#"{"kind": "replay", "from": 1000, "to": 3000}"#));
        assert_eq!(serde_json::from_str::<Value>(&j.get_state("lab_live_paper").unwrap().unwrap()).unwrap(), v);
        assert_eq!(j.get_state("venue_math").unwrap(), None);
        // C3-3: a heartbeat row per dated minute (the journal's count is the run's puts), and each group's wallet file,
        // written whole (both groups traded: K3 and H).
        let beats = j.count("heartbeat").unwrap();
        assert!(beats >= 1 && jr["puts"]["heartbeat"].as_u64() == Some(beats), "{beats} heartbeat rows, {jr}");
        drop(j);
        for (file, group_txs) in [("wallet.json", 1), ("wallet-h.json", 2)] {
            let w: Value = serde_json::from_str(&fs::read_to_string(root.join("j").join(file)).unwrap()).unwrap();
            let txs = w["txs"].as_array().unwrap();
            assert!(w["mode"] == "paper" && w["address"].is_null() && txs.len() >= group_txs, "{file}: {w}");
            assert!(txs.iter().all(|t| t["status"] == "paper" && t["signature"].is_null()), "{file}: {w}");
        }
        assert!(p["wallet_files_written"].as_u64().is_some_and(|n| n >= 2), "{p}");
        assert!(!root.join("j").join("wallet.json.tmp").exists());
        let trace = fs::read_to_string(root.join("trace.txt")).unwrap();
        assert_eq!(trace.lines().filter(|l| l.starts_with("settle F7 ")).count() as u64, filled + failed, "{trace}");
        assert!(log_line(&v).contains("lab-live paper [1000, 3000)"));
        // Refused before the first event: no --ignore-risk, a spec that is not M1's, specs that are not the primary's, groups
        // outside M1, bad costs; and as the replay: a journal with rows, the hold-out.
        let err = |a: &PaperArgs| format!("{:#}", run(a).unwrap_err());
        fs::write(root.join("g0.json"), GROUPS.replace(r#""risk": "/x/risk.yaml""#, r#""risk": null"#)).unwrap();
        assert!(err(&PaperArgs { groups: root.join("g0.json"), ignore_risk: false, ..args(&root, "j1") }).contains("names no risk file"));
        assert!(err(&PaperArgs { variants: vec!["0.045:600".into()], ..args(&root, "j1") }).contains("M4"));
        assert!(err(&PaperArgs { variants: vec!["flow300_H6".into()], ..args(&root, "j1") }).contains("primary group trades"));
        fs::write(root.join("g2.json"), GROUPS.replace(r#"["flow300_H6", "flow300_tp100_H6"]"#, r#"["flow300_H6", "trail3_T600"]"#)).unwrap();
        assert!(err(&PaperArgs { groups: root.join("g2.json"), ..args(&root, "j1") }).contains("M4"));
        fs::write(root.join("s2.json"), BASE_SCENARIO.replace(r#""tip": 100000"#, r#""tip": 100000, "latency_ms": 1"#)).unwrap();
        assert!(err(&PaperArgs { cost_scenario: root.join("s2.json"), ..args(&root, "j1") }).contains("latency_ms"));
        assert!(err(&args(&root, "j")).contains("rows already"));
        assert!(err(&PaperArgs { from: 452_304_000, to: 452_305_000, ..args(&root, "j3") }).contains("hold-out"));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_mem_log_writes_a_line_per_minute_and_changes_no_journal_row() {
        // C4-2: the same replay with and without --mem-log gives the same rows in every table and the same wallets; the
        // log has a line per dated minute (the heartbeat's) and an end line, with the engines' and the replay's
        // structures and the time per event.
        let root = tmp("paper-memlog");
        store(&root);
        edit_meta(&root.join("snap"), |v| {
            v["meta_bounds"] = json!([1000, 3000]);
            v["ms_per_slot"] = json!({"0": 1e12});
        });
        fs::write(root.join("scn.json"), format!("{BASE_SCENARIO}\n")).unwrap();
        let plain = run(&args(&root, "j")).unwrap();
        let logged = run(&PaperArgs { mem_log: Some(root.join("mem.jsonl")), ..args(&root, "jm") }).unwrap();
        let rows = |j: &str| {
            let db = rusqlite::Connection::open(root.join(j).join("journal.sqlite")).unwrap();
            ["signals", "orders", "fills", "exits", "positions", "heartbeat"].map(|t| {
                let mut st = db.prepare(&format!("SELECT * FROM {t} ORDER BY 1, 2, 3, 4")).unwrap();
                let n = st.column_count();
                st.query_map([], |r| (0..n).map(|i| r.get::<_, Sql>(i)).collect::<Result<Vec<_>, _>>()).unwrap().map(Result::unwrap)
                    .collect::<Vec<_>>()
            })
        };
        assert_eq!(rows("j"), rows("jm"));
        for w in ["wallet.json", "wallet-h.json"] {
            assert_eq!(fs::read(root.join("j").join(w)).unwrap(), fs::read(root.join("jm").join(w)).unwrap(), "{w}");
        }
        assert_eq!((plain.get("mem_log"), &logged["journal"]), (None, &plain["journal"]));
        let lines: Vec<Value> = fs::read_to_string(root.join("mem.jsonl")).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
        // one line per dated minute the engine passed (its heartbeat rows without a note; a feed-gap row has one), then the end
        let minutes: i64 = rusqlite::Connection::open(root.join("jm").join("journal.sqlite")).unwrap()
            .query_row("SELECT count(*) FROM heartbeat WHERE note = ''", [], |r| r.get(0)).unwrap();
        let minutes = minutes as u64;
        assert!(minutes >= 1);
        assert_eq!((lines.len() as u64, logged["mem_log"]["lines"].as_u64()), (minutes + 1, Some(minutes + 1)), "{lines:?}");
        let (last, end) = (&lines[lines.len() - 2], &lines[lines.len() - 1]);
        assert_eq!((end["end"].as_bool(), &end["minute"], &end["time_ns"]["n"]), (Some(true), &last["minute"], &logged["events"]));
        let n: u64 = lines[..lines.len() - 1].iter().map(|l| l["time_ns"]["n"].as_u64().unwrap()).sum();
        assert_eq!(Some(n), logged["events"].as_u64()); // each event's time in exactly one minute
        for name in ["universe_tokens", "recent_keys", "farm_items", "pool_seen", "busy", "open_positions", "run_curve_sums", "cooldowns",
                     "wallet_txs", "ops", "n2_seeds", "entered_rows", "interned_keys", "farmer_hist", "farmer_hist_trades", "pool_ctx",
                     "legs_buffer", "slot_buf", "block_times"] {
            assert!(end["structures"][name]["entries"].is_u64(), "{name} missing: {}", end["structures"]);
        }
        assert_eq!((&end["structures"]["ops"]["entries"], &end["segments"]), (&json!(0), &Value::Null)); // drained; chunks
        assert!(end["structures"]["recent_keys"]["bytes_est"].is_u64() && end["sqlite_bytes"].is_i64(), "{end}");
        assert!(end["structures"]["interned_keys"]["entries"].as_u64() == logged["keys"].as_u64() && end["heap_bytes"].as_u64() > Some(0), "{end}");
        // C4-5: the two hash tables' shape; their len is the structures' entries
        for name in ["interned_keys", "farmer_hist"] {
            let t = &end["tables"][name];
            assert_eq!(t["len"], end["structures"][name]["entries"], "{name}: {t}");
            assert!(t["buckets"].as_u64().is_some_and(|b| b == 0 || b.is_power_of_two()) && t["tombstones"].is_u64() && t["bytes"].is_u64(), "{name}: {t}");
        }
        fs::remove_dir_all(&root).unwrap();
    }

    // ------------------------------------------------------------------------------------------ M3-11: --follow

    /// The reference groups, both on [pool_reversed] (spec v0.2 #1: no curve path with --follow).
    fn live_groups() -> String {
        GROUPS.replace(r#""segments": null"#, r#""segments": ["pool_reversed"]"#)
    }

    /// A follow run's args over `root` (its snapshot before 1000 with the store /store/x.duckdb, h24 500, risk inert).
    fn follow_args(root: &Path, journal: &str) -> PaperArgs {
        fs::write(root.join("live-groups.json"), live_groups()).unwrap();
        fs::write(root.join("scn.json"), format!("{BASE_SCENARIO}\n")).unwrap();
        edit_meta(&root.join("snap"), |v| v["store"] = json!("/store/x.duckdb"));
        PaperArgs {
            follow: Some(FollowOpts { root: root.to_owned(), snapshot_before: Some(1000), h24: Some(500), expect_segments: None }),
            chunks_root: PathBuf::new(), segments: None, snapshot: root.join("snap"), from: 0, to: 0, groups: root.join("live-groups.json"),
            variants: vec!["0.04:600".into()], cost_scenario: root.join("scn.json"), ignore_risk: true, journal: root.join(journal),
            summary: Some(root.join(format!("{journal}.json"))), trace: None, mem_log: None, keep_farmer_history: false,
        }
    }

    #[test]
    fn a_follow_run_writes_the_live_states_and_counts_a_flag_fix_on_its_slots_day() {
        // M3-11 spec §2.1, §2.2, §4 on follow.rs's reflag fixture (M3-10 §1.2): seg 1's backfill reflags W's buy at 3050, a
        // fix handed out with seg 1's items and taken at the next slot end. No block time anywhere, so the engine's day is
        // None: the fix counts under None ({"null": [1, 0]}, P1 has no position), and no dated minute and no day to close
        // write the state (engine.py:1307: `if self.day`). feed byte for byte; the seed window of the start at 3000 (store
        // [1000, 3000), h24 500) as _journal_seed writes it.
        use crate::follow::tests::{put_trades, seg};
        let root = tmp("paper-follow-fix");
        fs::create_dir_all(root.join("snap")).unwrap();
        crate::legs_read::tests::export(&root.join("snap"));
        let d0 = seg(&root, 0, 3000, 3099, json!({}));
        put_trades(&d0, &[(3050, false, 100)]);
        let d1 = seg(&root, 1, 3100, 3199, json!({"backfills": [{"pool": "P1", "status": "complete", "rows": 2}]}));
        put_trades(&d1.join("backfill"), &[(3040, true, 100), (3045, false, 100)]);
        put_trades(&d1, &[(3150, false, 100), (3155, true, 200), (3160, false, 1)]);
        let a = follow_args(&root, "jf");
        let v = run(&PaperArgs { follow: Some(FollowOpts { expect_segments: Some(2), ..a.follow.unwrap() }), ..follow_args(&root, "jf") }).unwrap();
        let j = Journal::open(&root.join("jf")).unwrap();
        let state = |k: &str| j.get_state(k).unwrap();
        assert_eq!(state("feed").as_deref(), Some(r#"{"kind": "live", "from": null, "to": null}"#));
        let seed = r#"{"complete_from": 3000, "first_live_slot": 3000, "h24": 500, "hi": 3000, "lo": 2500, "store": "/store/x.duckdb"}"#;
        assert_eq!((state("farmer_seed").as_deref(), state("farmer_seeds")), (Some(seed), Some(format!("[{seed}]"))));
        assert_eq!((state("farm_flag_fixes"), &v["paper"]["flag_fix_days"]), (None, &json!(r#"{"null": [1, 0]}"#)));
        assert_eq!((state("groups"), state("cost_scenario").as_deref()), (Some(live_groups()), Some(BASE_SCENARIO)));
        let f = &v["follow"];
        assert_eq!((&f["items"]["flag_fix"], &f["items"]["farm_seed"], &f["token_forgets"], &f["stats"]["farmer_flags_fixed"]),
                   (&json!(1), &json!(1), &json!(0), &json!(1)));
        assert_eq!((&v["events"], &v["journal"]["signals"], &v["journal"]["learned"]), (&json!(4), &json!(0), &json!(0)));
        assert_eq!(v["bounds"], json!([0, i64::MAX, 324_000, 324_000, i64::MAX as u64 - 20_000]));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_flag_fix_days_are_written_as_python_dumps_them() {
        // engine.py `json.dumps(self.flag_fix_days, sort_keys=True)`; a day of None is "null" (LAB review, nit a)
        let days = |v: &[(Option<&str>, [u64; 2])]| v.iter().map(|(d, n)| (d.map(str::to_owned), *n)).collect::<BTreeMap<_, _>>();
        assert_eq!(flag_fixes_state(&days(&[])), "{}");
        assert_eq!(flag_fixes_state(&days(&[(None, [1, 0])])), r#"{"null": [1, 0]}"#);
        assert_eq!(flag_fixes_state(&days(&[(Some("2026-09-23"), [1, 1]), (Some("2026-09-22"), [3, 0])])),
                   r#"{"2026-09-22": [3, 0], "2026-09-23": [1, 1]}"#);
    }

    #[test]
    fn learned_and_usage_rows_wait_for_the_next_dated_minute_and_none_after_the_last() {
        // M3-11 spec §2.2 (v0.2 #3), `_live_minute`: the rows queued when the engine's dated minute moves on are written then
        // (usage with its own minute, else that minute); those queued after the last move are not written.
        // - seg 0 [1000, 1099]: usage (minute 7); blocks at 1010 (minute 10,000) and 1030 (10,001); P1 buys there; the
        //   clock's epoch row;
        // - seg 1 [1100, 1199]: creates PN (a SOL market); P1 buys at 1110 (minute 10,003);
        // - seg 2 [1200, 1299]: usage without a minute; P1 buys at 1210 (10,003);
        // - seg 3 [1300, 1399]: creates PQ; P1 buys at 1310 (10,003);
        // - seg 4 [1400, 1499]: creates PZ; P1 buys at 1410 (10,003).
        // The release lags a slot: 1010 ends when 1030 arrives (after seg 1's items): minute 10,000, seg 0's usage and the
        // epoch and PN rows; 1030 ends after seg 2's items: 10,001, seg 2's usage at 10,001; 1110 ends after seg 3's
        // items: 10,003, PQ's row. Then the minute stays: PZ's row waits and is not written.
        use crate::follow::tests::{blocks, create_pool, put_trades, seg};
        let root = tmp("paper-follow-minutes");
        fs::create_dir_all(root.join("snap")).unwrap();
        crate::legs_read::tests::export(&root.join("snap"));
        let d0 = seg(&root, 0, 1000, 1099, json!({"usage": [{"minute": 7, "stream": "a", "bytes": 5}]}));
        blocks(&d0, &[(1010, Some(600_000), Some(false)), (1030, Some(600_100), Some(false))]);
        put_trades(&d0, &[(1010, false, 100), (1030, false, 100)]);
        let d1 = seg(&root, 1, 1100, 1199, json!({}));
        create_pool(&d1, 0, &[(1100, "PN", "AN", lab_core::legs::WSOL)]);
        blocks(&d1, &[(1110, Some(600_200), Some(false))]);
        put_trades(&d1, &[(1110, false, 100)]);
        let d2 = seg(&root, 2, 1200, 1299, json!({"usage": [{"stream": "b", "bytes": 6}]}));
        blocks(&d2, &[(1210, Some(600_210), Some(false))]);
        put_trades(&d2, &[(1210, false, 100)]);
        for (seq, pool, bt) in [(3, "PQ", 600_215), (4, "PZ", 600_220)] {
            let lo = 1000 + 100 * seq;
            let d = seg(&root, seq, lo, lo + 99, json!({}));
            create_pool(&d, 0, &[(lo, pool, &format!("A{pool}"), lab_core::legs::WSOL)]);
            blocks(&d, &[(lo + 10, Some(bt), Some(false))]);
            put_trades(&d, &[(lo + 10, false, 100)]);
        }
        let v = run(&follow_args(&root, "jm")).unwrap();
        let j = Journal::open(&root.join("jm")).unwrap();
        let db = rusqlite::Connection::open(root.join("jm").join("journal.sqlite")).unwrap();
        let rows = |sql: &str| -> Vec<String> {
            let mut st = db.prepare(sql).unwrap();
            st.query_map([], |r| r.get::<_, String>(0)).unwrap().map(Result::unwrap).collect()
        };
        assert_eq!(rows("SELECT kind || ' ' || key || ' ' || seq || ' ' || coalesce(slot, 'null') FROM learned ORDER BY seq, kind, key"),
                   ["epoch 0 0 1010", "pool PN 1 1100", "pool PQ 3 1300"]);
        assert_eq!(rows("SELECT minute || ' ' || day || ' ' || stream || ' ' || bytes || ' ' || seq || ' ' || coalesce(note, 'null') FROM usage ORDER BY minute"),
                   ["7 1970-01-01 a 5 0 null", "10001 1970-01-07 b 6 2 null"]);
        let f = &v["follow"];
        assert_eq!((&f["rows_written"], &f["waiting_at_end"]), (&json!({"learned": 3, "usage": 2}), &json!({"learned": 1, "usage": 0})));
        assert_eq!((j.get_state("farm_flag_fixes").unwrap(), &v["journal"]["learned"]), (None, &json!(3))); // no fix: never written
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_cli_route_refuses_a_snapshot_past_the_first_live_slot() {
        // Codex finding 3 (on 383a24d), through main.rs parse_paper_args, then run: the store [1000, 3000) and one segment
        // with a buy of P1. From 2600: without --snapshot-before (a newer store) or with 2700 (too late), refused before any
        // journal; with 2600 (equal) or 2590 (paper.shadow's lo nuance) it runs. From 3100 (an older store) it runs without.
        use crate::follow::tests::{put_trades, seg};
        for (first, before, ok) in [(2600, None, false), (2600, Some(2700), false), (2600, Some(2600), true), (2600, Some(2590), true),
                                    (3100, None, true)] {
            let root = tmp(&format!("paper-follow-bound-{first}-{}", before.unwrap_or(0)));
            fs::create_dir_all(root.join("snap")).unwrap();
            crate::legs_read::tests::export(&root.join("snap"));
            let d0 = seg(&root, 0, first, first + 99, json!({}));
            put_trades(&d0, &[(first + 20, false, 50)]);
            let _ = follow_args(&root, "jb"); // the groups, the cost scenario and the snapshot's store
            let mut argv = format!("--follow {} --snapshot {} --h24 500 --groups {} --variants 0.04:600 --cost-scenario {} --journal {}",
                                   root.display(), root.join("snap").display(), root.join("live-groups.json").display(),
                                   root.join("scn.json").display(), root.join("jb").display());
            if let Some(b) = before {
                argv.push_str(&format!(" --snapshot-before {b}"));
            }
            let argv: Vec<String> = argv.split(' ').map(str::to_owned).collect();
            let r = run(&crate::parse_paper_args(&argv).unwrap());
            assert_eq!(r.is_ok(), ok, "{first} {before:?}: {r:?}");
            if !ok {
                assert!(format!("{:#}", r.unwrap_err()).contains("--snapshot-before"));
                assert!(!root.join("jb").exists(), "refused before the journal");
            }
            fs::remove_dir_all(&root).unwrap();
        }
    }

    #[test]
    fn a_group_that_could_trade_a_curve_is_refused_with_follow() {
        // spec v0.2 #1: segments unset (None means everywhere) or holding curve: refused before the first event, no journal
        let root = tmp("paper-follow-curve");
        fs::create_dir_all(root.join("snap")).unwrap();
        crate::legs_read::tests::export(&root.join("snap"));
        crate::follow::tests::seg(&root, 0, 3000, 3099, json!({}));
        let a = follow_args(&root, "jc");
        for (i, groups) in [GROUPS.to_owned(), GROUPS.replace(r#""segments": null"#, r#""segments": ["curve"]"#)].into_iter().enumerate() {
            let path = root.join(format!("g{i}.json"));
            fs::write(&path, groups).unwrap();
            let e = format!("{:#}", run(&PaperArgs { groups: path, ..follow_args(&root, "jc") }).unwrap_err());
            assert!(e.contains("group K3") && e.contains("M3-11b"), "{e}");
        }
        assert!(!a.journal.exists());
        fs::remove_dir_all(&root).unwrap();
    }
}
