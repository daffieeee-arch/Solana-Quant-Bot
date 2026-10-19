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

use crate::curve_hold;
use crate::journal::Journal;
use crate::legs::Keys;
use crate::legs_read::{Replay, ReplayMem};
use crate::memlog::{MemLog, Report};
use crate::source::{Item, Source};
use crate::paper_core::{op_write, parse_scenario, Write};
use crate::signals::load;
use crate::signals_core::{counts_json, feed_state, parse_groups, Cell, KeyNames, Seeds};
use anyhow::{bail, ensure, Context, Result};
use lab_core::exits::{check_groups, variant_for_spec};
use lab_core::farm::WindowRule;
use lab_core::legs::{Id, NONE};
use lab_core::paper::{GradInfo, Op, PaperCfg, PaperEngine, PosKey, RiskSource, SeedTrace};
use lab_core::signals::{SignalEngine, Status};
use lab_core::universe::Segment;
use rusqlite::types::Value as Sql;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io::{BufWriter, Write as _};
use std::path::PathBuf;
use std::time::Instant;

pub struct PaperArgs {
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

/// The replay's paper journal (see the module doc); the summary.
pub fn run(a: &PaperArgs) -> Result<Value> {
    let t0 = Instant::now();
    let groups_text = text(&a.groups, "--groups")?;
    let groups = parse_groups(&groups_text).with_context(|| format!("--groups {}", a.groups.display()))?;
    let risk = if a.ignore_risk {
        RiskSource::Ignore
    } else {
        let paths: Vec<PathBuf> = groups
            .specs
            .iter()
            .zip(&groups.risk)
            .map(|(g, r)| r.as_ref().map(PathBuf::from).with_context(|| format!("group {}: risk on, but its groups text names no risk file", g.name)))
            .collect::<Result<_>>()?;
        RiskSource::Files(Box::new(move |g: usize| crate::risk::load(&paths[g])))
    };
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
        for p in snap.pools.values() {
            grad.add_pool(adm.keys.id(&p.pool), p.create_slot);
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
            chunks_root: root.join("chunks"), segments: None, snapshot: root.join("snap"), from: 1000, to: 3000, groups: root.join("groups.json"),
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
                     "legs_buffer", "block_times"] {
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
}
