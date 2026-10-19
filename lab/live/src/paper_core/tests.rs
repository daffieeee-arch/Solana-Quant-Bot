use std::collections::{BTreeMap, HashMap};

use lab_core::legs::NONE;
use lab_core::paper::{GradInfo, PaperCfg, PaperEngine, RiskSource};
use lab_core::universe::{MintMeta, PoolMeta};
use serde_json::{json, Map, Value};

use super::*;
use crate::signals_core::tests::{b, case_engine, event, field, os, ou, projection, s, CaseKeys, Row};
use crate::signals_core::{parse_groups, KeyNames, Seeds};

/// The shared golden cases (paper/tests/c2_position_cases.py and c1b_signal_cases.py write them from the engine).
const C2: &str = include_str!("../../../paper/tests/data/c2_position_cases.json");
const C1B: &str = include_str!("../../../paper/tests/data/c1b_signal_cases.json");
/// M1 C3-2's cases: risk on, each group's risk file in versions (paper/tests/c3_risk_cases.py).
const C3: &str = include_str!("../../../paper/tests/data/c3_risk_cases.json");
/// M3-08's cases: what a live feed learns while it reads (paper/tests/m3_live_cases.py).
const M3: &str = include_str!("../../../paper/tests/data/m3_live_cases.json");
/// The journal's tables and columns (paper/journal.py TABLES), as journal.rs compiles them in.
const SCHEMA: &str = include_str!("../../../paper/journal_schema.json");

fn columns() -> HashMap<String, Vec<String>> {
    let v: Value = serde_json::from_str(SCHEMA).unwrap();
    v["tables"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| {
            let cols = t["cols"].as_str().unwrap().split(", ").map(|c| c.split(' ').next().unwrap().to_owned()).collect();
            (t["name"].as_str().unwrap().to_owned(), cols)
        })
        .collect()
}

fn cell_json(c: &Cell) -> Value {
    match c {
        Cell::Null => Value::Null,
        Cell::Int(i) => json!(i),
        Cell::Real(x) => json!(x),
        Cell::Text(t) => json!(t),
    }
}

/// A write as the generator records it: ["put", table, {every column: value or null}], ["delete", table, {key}] or
/// ["wallet", file, text] (c3_risk_cases.py; `wallets` names each group's file).
fn write_json(w: &Write, cols: &HashMap<String, Vec<String>>, wallets: &[Option<String>]) -> Value {
    match w {
        Write::Put(table, cells) => {
            for (c, _) in cells {
                assert!(cols[*table].iter().any(|x| x == c), "{table} has no column {c}");
            }
            let row: Map<String, Value> = cols[*table]
                .iter()
                .map(|c| (c.clone(), cells.iter().find(|x| x.0 == c).map_or(Value::Null, |x| cell_json(&x.1))))
                .collect();
            json!(["put", table, row])
        }
        Write::Delete(table, key) => {
            delete_key_ok(w, &cols[*table].iter().map(String::as_str).collect::<Vec<_>>()).unwrap();
            json!(["delete", table, key.iter().map(|(c, v)| (c.to_string(), cell_json(v))).collect::<Map<_, _>>()])
        }
        Write::Wallet(g, text) => json!(["wallet", wallets[*g].as_deref().expect("a group with a wallet file"), text]),
    }
}

/// C2's five tables: its goldens record no heartbeat and no wallet.
fn c2_write(w: &Write) -> bool {
    matches!(w, Write::Put(t, _) | Write::Delete(t, _) if ["signals", "orders", "fills", "exits", "positions"].contains(t))
}

/// A case run as the generator runs it: the paper engine on its events (its groups and cost scenario; risk inert, or
/// with `risk` the groups' files as `files` writes them), every journal write in order, and the engine and interner
/// after it.
fn run_paper(case: &Value, scenario: &str) -> Result<(Vec<Write>, PaperEngine, CaseKeys)> {
    let (mut writes, eng, keys) = run_case(case, scenario, None)?;
    writes.retain(c2_write);
    Ok((writes, eng, keys))
}

/// A C3 case's risk files in a directory of their own: each group's first text, then its flips as the feed reaches
/// them (c3_risk_cases.py FlipFeed: before the first event with slot >= at). A null text is a missing file.
struct RiskFiles {
    dir: std::path::PathBuf,
    flips: Vec<(u64, std::path::PathBuf, Option<String>)>,
    next: usize,
}

impl RiskFiles {
    fn new(case: &Value, name: &str) -> Result<(RiskFiles, Vec<std::path::PathBuf>)> {
        let dir = std::env::temp_dir().join(format!("lab-live-c3-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir)?;
        let gs: Value = serde_json::from_str(s(case, "groups")?)?;
        let file = |g: &str| -> Result<std::path::PathBuf> { Ok(dir.join(gs[g]["risk"].as_str().with_context(|| format!("group {g}'s risk"))?)) };
        let mut flips = Vec::new();
        for (g, vs) in field(case, "risk")?.as_object().context("risk")? {
            for (i, v) in vs.as_array().context("versions")?.iter().enumerate() {
                let text = v[1].as_str().map(str::to_owned);
                if i == 0 {
                    write_risk(&file(g)?, text.as_deref())?;
                } else {
                    flips.push((v[0].as_u64().context("at")?, file(g)?, text));
                }
            }
        }
        flips.sort_by_key(|f| f.0); // stable, as the generator's sort
        let groups = parse_groups(s(case, "groups")?)?;
        let paths = groups.specs.iter().map(|g| file(&g.name)).collect::<Result<_>>()?;
        Ok((RiskFiles { dir, flips, next: 0 }, paths))
    }

    /// Whether a file changes before an event at `slot` (a flip not written yet is due by then).
    fn changes_by(&self, slot: u64) -> bool {
        self.flips.get(self.next).is_some_and(|f| f.0 <= slot)
    }

    /// The flips due before an event at `slot`.
    fn before(&mut self, slot: u64) -> Result<()> {
        while let Some((at, path, text)) = self.flips.get(self.next) {
            if *at > slot {
                break;
            }
            write_risk(path, text.as_deref())?;
            self.next += 1;
        }
        Ok(())
    }
}

impl Drop for RiskFiles {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn write_risk(path: &std::path::Path, text: Option<&str>) -> Result<()> {
    match text {
        Some(t) => std::fs::write(path, t)?,
        None => {
            let _ = std::fs::remove_file(path);
        }
    }
    Ok(())
}

/// How a case's slots end (M3-08 spec D1): as the next event ends them (the replay), or live with `close_through` after
/// each slot's last event, through that slot (`Early`) or through the slot before the next event's (`Late`). Live, what
/// the feed learned with the next event goes first, as a source learns what it read before it closes.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Close {
    Replay,
    Early,
    Late,
}

/// One item of a case's `learn` (m3_live_cases.py FORMAT) into the engine's live API: a token replaced, a SOL market
/// added (its mint interned, so its token is looked up when it is used, as Python's `feed.token(p.mint)`; its
/// create_slot in its PoolMeta), a backfill as history (M3-08b), an epoch's ms per slot.
fn learn_item(eng: &mut PaperEngine, x: &Value, keys: &mut CaseKeys) -> Result<()> {
    if let Some(t) = x.get("token") {
        let mayhem = field(t, "mayhem")?;
        let mayhem = if mayhem.is_null() { None } else { Some(mayhem.as_bool().context("mayhem")?) };
        let creator = os(t, "creator")?.map_or(NONE, |c| keys.id(c));
        let meta = MintMeta::new(ou(t, "create_slot")?, creator, mayhem, os(t, "quote_mint")?);
        let pool = os(t, "pool")?.map(|p| keys.id(p));
        let mint = keys.id(s(t, "mint")?);
        eng.learn_token(mint, meta, ou(t, "complete_slot")?, pool);
    } else if let Some(p) = x.get("pool") {
        if matches!(s(p, "quote_class")?, "sol" | "reversed") {
            let reversed = match s(p, "orientation")? {
                "normal" => false,
                "reversed" => true,
                o => bail!("orientation {o}"),
            };
            let coin_creator = os(p, "coin_creator")?.map_or(NONE, |c| keys.id(c));
            let meta = PoolMeta { reversed, mayhem: b(p, "mayhem")?, mint_tok: Some(keys.id(s(p, "mint")?)), coin_creator,
                                  create_slot: ou(p, "create_slot")? };
            let pool = keys.id(s(p, "pool")?);
            eng.learn_pool(pool, meta);
        }
    } else if let Some(h) = x.get("history") {
        // M3-08b: a backfill's trades of the pool, history only (LiveFeed drain_history)
        let legs = field(h, "events")?.as_array().context("history events")?.iter().map(|e| event(e, keys).map(|ev| ev.leg))
            .collect::<Result<Vec<_>>>()?;
        eng.add_history(keys.id(s(h, "pool")?), legs);
    } else {
        eng.set_epoch_ms(field(x, "epoch")?.as_u64().context("epoch")?, field(x, "ms")?.as_f64().context("ms")?);
    }
    Ok(())
}

fn run_case(case: &Value, scenario: &str, risk: Option<&str>) -> Result<(Vec<Write>, PaperEngine, CaseKeys)> {
    run_case_closing(case, scenario, risk, Close::Replay).map(|(w, e, k, _)| (w, e, k))
}

/// The case run with its slots closed as `close` says; with risk files, a slot is closed only where no file changes
/// before the next event (a close reads the files then, the next event's push after its flips: M3-08 spec D1). Also
/// the closes made and skipped.
fn run_case_closing(case: &Value, scenario: &str, risk: Option<&str>, close: Close)
                    -> Result<(Vec<Write>, PaperEngine, CaseKeys, (usize, usize))> {
    let (sig, groups, mut keys) = case_engine(case)?;
    let mut grad = GradInfo::default();
    for t in field(case, "tokens")?.as_array().context("tokens")? {
        let mint = keys.id(s(t, "mint")?);
        let complete = if t.get("complete_slot").is_some() { ou(t, "complete_slot")? } else { None };
        let pool = if t.get("pool").is_some() { os(t, "pool")?.map(|p| keys.id(p)) } else { None };
        grad.add_token(mint, complete, pool);
    }
    let cfg = PaperCfg::new(groups.q, parse_scenario(scenario)?, grad, HashMap::new());
    let mut files = None;
    let mut eng = match risk {
        None => PaperEngine::new(sig, cfg)?,
        Some(name) => {
            let (f, paths) = RiskFiles::new(case, name)?;
            files = Some(f);
            PaperEngine::with_risk(sig, cfg, RiskSource::Files(Box::new(move |g: usize| crate::risk::load(&paths[g]))))?
        }
    };
    let mut seeds = Seeds::default();
    let mut writes = Vec::new();
    // What the feed learns, each item before the first event with a slot >= its at_slot (m3_live_cases.py LearnFeed).
    let learn: Vec<&Value> = case.get("learn").and_then(Value::as_array).map(|v| v.iter().collect()).unwrap_or_default();
    let mut learned = 0;
    let mut learn_upto = |eng: &mut PaperEngine, keys: &mut CaseKeys, slot: u64| -> Result<()> {
        while let Some(x) = learn.get(learned) {
            if field(x, "at_slot")?.as_u64().context("at_slot")? > slot {
                break;
            }
            learn_item(eng, x, keys)?;
            learned += 1;
        }
        Ok(())
    };
    let events = field(case, "events")?.as_array().context("events")?;
    let mut closes = (0, 0);
    for (i, e) in events.iter().enumerate() {
        let ev = event(e, &mut keys)?;
        let slot = ev.leg.slot();
        learn_upto(&mut eng, &mut keys, slot)?;
        if let Some(f) = files.as_mut() {
            f.before(slot)?;
        }
        eng.push(&ev, &mut seeds.with(&keys))?;
        for op in eng.paper.ops.drain(..) {
            writes.push(op_write(&op, &keys)?);
        }
        let next = events.get(i + 1).map(|n| ou(n, "slot")).transpose()?.flatten().filter(|&n| n != slot);
        if let (Some(next), true) = (next, close != Close::Replay) {
            if files.as_ref().is_some_and(|f| f.changes_by(next)) {
                closes.1 += 1;
                continue;
            }
            closes.0 += 1;
            learn_upto(&mut eng, &mut keys, next)?;
            let c = if close == Close::Early { slot } else { next - 1 };
            ensure!(eng.close_through(c, &mut seeds.with(&keys))? == Some(slot), "close_through({c}) did not end slot {slot}");
            for op in eng.paper.ops.drain(..) {
                writes.push(op_write(&op, &keys)?);
            }
        }
    }
    learn_upto(&mut eng, &mut keys, u64::MAX)?;
    eng.finish(&mut seeds.with(&keys))?;
    for op in eng.paper.ops.drain(..) {
        writes.push(op_write(&op, &keys)?);
    }
    Ok((writes, eng, keys, closes))
}

#[test]
fn every_c2_case_gives_the_engines_writes() {
    let doc: Value = serde_json::from_str(C2).unwrap();
    let cols = columns();
    let mut n = 0;
    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let scenario = case["cost_scenario"].as_str().unwrap();
        let (writes, eng, _) = run_paper(case, scenario).unwrap_or_else(|e| panic!("{name}: {e:#}"));
        let got: Vec<Value> = writes.iter().map(|w| write_json(w, &cols, &[])).collect();
        let want = case["expect_ops"].as_array().unwrap();
        if let Some(i) = (0..got.len().max(want.len())).find(|&i| got.get(i) != want.get(i)) {
            panic!("{name}: write {i} of {} (Rust) / {} (Python)\n rust:   {}\n python: {}", got.len(), want.len(),
                   got.get(i).map_or("(none)".into(), Value::to_string), want.get(i).map_or("(none)".into(), Value::to_string));
        }
        assert_eq!((eng.paper.open_positions(), eng.paper.pending()), (0, 0), "{name}");
        n += want.len();
    }
    assert!(n > 0);
}

/// A C2-format case's writes against its expect_ops, write by write; the writes compared.
fn same_writes(name: &str, writes: &[Write], case: &Value, cols: &HashMap<String, Vec<String>>) -> usize {
    let got: Vec<Value> = writes.iter().filter(|w| c2_write(w)).map(|w| write_json(w, cols, &[])).collect();
    let want = case["expect_ops"].as_array().unwrap();
    if let Some(i) = (0..got.len().max(want.len())).find(|&i| got.get(i) != want.get(i)) {
        panic!("{name}: write {i} of {} (Rust) / {} (Python)\n rust:   {}\n python: {}", got.len(), want.len(),
               got.get(i).map_or("(none)".into(), Value::to_string), want.get(i).map_or("(none)".into(), Value::to_string));
    }
    want.len()
}

#[test]
fn every_m3_case_gives_the_engines_writes_with_what_it_learns() {
    // M3-08 spec §3: the feed learns tokens, pools and an epoch's ms per slot while it reads; lab/core's learn_token,
    // learn_pool and set_epoch_ms before the same events give the engine's writes, also with the slots closed live.
    let doc: Value = serde_json::from_str(M3).unwrap();
    let cols = columns();
    let mut n = 0;
    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let scenario = case["cost_scenario"].as_str().unwrap();
        assert!(!case["learn"].as_array().unwrap().is_empty() || case["window_rule"] == "live", "{name}");
        for close in [Close::Replay, Close::Early, Close::Late] {
            let (writes, eng, _, _) = run_case_closing(case, scenario, None, close).unwrap_or_else(|e| panic!("{name} {close:?}: {e:#}"));
            n += same_writes(&format!("{name} {close:?}"), &writes, case, &cols);
            assert_eq!((eng.paper.open_positions(), eng.paper.pending()), (0, 0), "{name} {close:?}");
        }
        // Without the learns, or with the other farm-window rule, the case writes something else (test_live_cases.py SAME:
        // completion_learned_too_late's learn comes too late, window_rule_live learns nothing, M3-08's cases have no
        // window-gated row): it shows what the live API carries.
        let same = |c: &Value| {
            let (writes, _, _, _) = run_case_closing(c, scenario, None, Close::Replay).unwrap();
            let got: Vec<Value> = writes.iter().filter(|w| c2_write(w)).map(|w| write_json(w, &cols, &[])).collect();
            got == *case["expect_ops"].as_array().unwrap()
        };
        let mut bare = case.clone();
        bare["learn"] = json!([]);
        let mut other = case.clone();
        other["window_rule"] = json!(if case["window_rule"] == "live" { "all_pools" } else { "live" });
        let live = case["window_rule"] == "live";
        assert_eq!((same(&bare), same(&other)),
                   (["completion_learned_too_late", "window_rule_live"].contains(&name), !live), "{name}");
    }
    assert!(n > 0);
}

#[test]
fn every_c2_case_gives_the_engines_writes_with_the_slots_closed_live() {
    // M3-08 spec D1: close_through after each slot's last event, through that slot or through the slot before the next
    // event's, writes what the next event's push alone writes (C2's cases: risk inert, so no file is read at a close).
    let doc: Value = serde_json::from_str(C2).unwrap();
    let cols = columns();
    let mut n = 0;
    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let scenario = case["cost_scenario"].as_str().unwrap();
        for close in [Close::Early, Close::Late] {
            let (writes, eng, _, _) = run_case_closing(case, scenario, None, close).unwrap_or_else(|e| panic!("{name} {close:?}: {e:#}"));
            n += same_writes(&format!("{name} {close:?}"), &writes, case, &cols);
            assert_eq!((eng.paper.open_positions(), eng.paper.pending()), (0, 0), "{name} {close:?}");
        }
    }
    assert!(n > 0);
}

#[test]
fn every_c3_case_gives_the_engines_writes_with_the_slots_closed_live_where_the_files_stay() {
    // LAB review on #420: risk on, close_through after a slot's last event wherever no risk file changes before the
    // next event (a close reads the files earlier than that push: spec D1's exception, wall time). Every write, the
    // wallet files included, equals the engine's.
    let doc: Value = serde_json::from_str(C3).unwrap();
    let cols = columns();
    let (mut made, mut skipped) = (0, 0);
    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let scenario = case["cost_scenario"].as_str().unwrap();
        let wallets = parse_groups(case["groups"].as_str().unwrap()).unwrap().wallet;
        for close in [Close::Early, Close::Late] {
            let (writes, eng, _, (m, k)) =
                run_case_closing(case, scenario, Some(name), close).unwrap_or_else(|e| panic!("{name} {close:?}: {e:#}"));
            let got: Vec<Value> = writes.iter().map(|w| write_json(w, &cols, &wallets)).collect();
            let want = case["expect_ops"].as_array().unwrap();
            if let Some(i) = (0..got.len().max(want.len())).find(|&i| got.get(i) != want.get(i)) {
                panic!("{name} {close:?}: write {i} of {} (Rust) / {} (Python)\n rust:   {}\n python: {}", got.len(), want.len(),
                       got.get(i).map_or("(none)".into(), Value::to_string), want.get(i).map_or("(none)".into(), Value::to_string));
            }
            assert_eq!((eng.paper.open_positions(), eng.paper.pending()), (0, 0), "{name} {close:?}");
            (made, skipped) = (made + m, skipped + k);
        }
    }
    assert!(made > 0 && skipped > 0, "closes made {made}, skipped where a file changes {skipped}");
}

#[test]
fn every_c3_case_gives_the_engines_writes_with_risk_on() {
    let doc: Value = serde_json::from_str(C3).unwrap();
    let cols = columns();
    let mut n = 0;
    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let scenario = case["cost_scenario"].as_str().unwrap();
        let (writes, eng, _) = run_case(case, scenario, Some(name)).unwrap_or_else(|e| panic!("{name}: {e:#}"));
        let wallets = parse_groups(case["groups"].as_str().unwrap()).unwrap().wallet;
        let got: Vec<Value> = writes.iter().map(|w| write_json(w, &cols, &wallets)).collect();
        let want = case["expect_ops"].as_array().unwrap();
        if let Some(i) = (0..got.len().max(want.len())).find(|&i| got.get(i) != want.get(i)) {
            panic!("{name}: write {i} of {} (Rust) / {} (Python)\n rust:   {}\n python: {}", got.len(), want.len(),
                   got.get(i).map_or("(none)".into(), Value::to_string), want.get(i).map_or("(none)".into(), Value::to_string));
        }
        assert_eq!((eng.paper.open_positions(), eng.paper.pending()), (0, 0), "{name}");
        n += want.len();
    }
    assert!(n > 0);
}

#[test]
fn the_c1b_cases_carry_the_engines_statuses() {
    // research/m1-c2-positionsim-spec-2026-10-19.md §6.3: with busy, risk and the Pendings the C1b rows match Python's
    // status and reason too (the last write of a key kept, as INSERT OR REPLACE).
    let doc: Value = serde_json::from_str(C1B).unwrap();
    let (mut entered, mut busy) = (0, 0);
    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let (writes, _, _) = run_paper(case, BASE_SCENARIO).unwrap_or_else(|e| panic!("{name}: {e:#}"));
        let mut journal: BTreeMap<(String, String, String, i64), Row> = BTreeMap::new();
        for w in &writes {
            if let Write::Put("signals", cells) = w {
                let m = projection(cells);
                let k = (m["family"].as_str().unwrap().into(), m["variant"].as_str().unwrap().into(), m["key"].as_str().unwrap().into(),
                         m["t"].as_i64().unwrap());
                journal.insert(k, m);
            }
        }
        let rows: Vec<Row> = journal.into_values().collect();
        let want: Vec<Row> = case["expect"].as_array().unwrap().iter().map(|r| r.as_object().unwrap().clone()).collect();
        assert_eq!(rows, want, "{name}");
        entered += rows.iter().filter(|r| r["status"] == "entered").count();
        busy += rows.iter().filter(|r| r["reason"] == "busy").count();
    }
    assert_eq!((entered, busy), (46, 1)); // spec §6.3
}

#[test]
fn the_base_scenario_parses_to_pythons_costs() {
    // lab-py Scenario.from_cfg("base", load_defaults()): per_tx 720,618, entry_total(5e8) 502,234,458, exit_net(0) 793,222
    // (spec §3.9); land_prob to its bits (float.hex 0x1.f0a3d70a3d70ap-1).
    let s = parse_scenario(BASE_SCENARIO).unwrap();
    assert_eq!((s.per_tx(), s.entry_total(500_000_000), s.exit_net(0, true)), (720_618, 502_234_458, 793_222));
    assert_eq!((s.land_prob.to_bits(), s.name.as_str(), s.rent_mode), (0x3fef_0a3d_70a3_d70a, "base", RentMode::RefundedOnFullExit));
    let one = |from: &str, to: &str| parse_scenario(&BASE_SCENARIO.replacen(from, to, 1));
    for (from, to, why) in [
        (r#""tip": 100000"#, r#""tip": 100000, "latency_ms": 400"#, "no key"),
        (r#", "tip": 100000"#, "", "no tip"),
        (r#""refunded_on_full_exit""#, r#""refunded_at_noon""#, "rent_mode"),
        (r#""land_prob": 0.97"#, r#""land_prob": 0.0"#, "land_prob"),
        (r#""adverse_per_side": 0.0"#, r#""adverse_per_side": 1.0"#, "adverse_per_side"),
        (r#""failed_tx": 505000"#, r#""failed_tx": -1"#, "failed_tx"),
        (r#""fee_per_tx": 605000"#, r#""fee_per_tx": 605000.5"#, "fee_per_tx"),
    ] {
        let err = format!("{:#}", one(from, to).unwrap_err());
        assert!(err.contains(why), "{to}: {err}");
    }
}

#[test]
fn a_delete_needs_a_key_of_the_tables_columns() {
    let cols = ["family", "variant", "key", "t"];
    let k = |c: &'static str| Write::Delete("positions", vec![(c, Cell::Int(1))]);
    assert!(delete_key_ok(&k("t"), &cols).is_ok() && delete_key_ok(&k("slot"), &cols).is_err());
    assert!(delete_key_ok(&Write::Delete("positions", vec![]), &cols).is_err());
}
