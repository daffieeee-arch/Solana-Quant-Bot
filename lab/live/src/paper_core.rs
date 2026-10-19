//! The light half of `lab-live paper` (M1 C2-4b; the porting spec is research/m1-c2-positionsim-spec-2026-10-19.md
//! §5.2, §6.2-§6.3): what lies between the paper engine (lab_core::paper) and the journal and needs no Parquet or
//! SQLite, so the shared golden cases run against it on their own (lab-core, serde_json, sha2, anyhow).
//!
//! - `parse_scenario`: the reference journal's state 'cost_scenario' (spec D6). Its floats are Python's json.dumps of
//!   its floats: serde_json reads them to the same bits only with float_roundtrip (Cargo.toml), and land_prob one ULP
//!   off can move per_tx by a lamport.
//! - `op_write`: a paper engine `Op` as the journal write it is: a put of a row's cells, or the positions delete.
//! - The tests run every case of paper/tests/data/c2_position_cases.json (the engine's own journal writes on small
//!   feeds) through SignalEngine and PaperEngine, write by write; and every case of c1b_signal_cases.json, whose signal
//!   rows now carry the engine's status and reason (spec §6.3).

use anyhow::{bail, ensure, Context, Result};
use lab_core::costs::{RentMode, Scenario};
use lab_core::paper::{ExitRow, Op, PosKey, WalletFile};
use serde_json::Value;

use crate::signals_core::{row_cells, Cell, KeyNames};

/// The keys of Scenario's asdict (backtest/costs.py, engine.py:1494); others are refused (a newer scenario could cost
/// differently).
const SCENARIO_KEYS: [&str; 8] = ["adverse_per_side", "ata_rent", "failed_tx", "fee_per_tx", "land_prob", "name", "rent_mode", "tip"];

/// The journal's state 'cost_scenario' (json.dumps(asdict(scn), sort_keys=True)). Refused: other or missing keys, an
/// unknown rent_mode, a land_prob outside (0, 1], an adverse_per_side outside [0, 1), and a lamport amount that is no
/// integer or is negative.
pub fn parse_scenario(text: &str) -> Result<Scenario> {
    let v: Value = serde_json::from_str(text).context("the cost scenario is no JSON")?;
    let m = v.as_object().context("the cost scenario is no object")?;
    if let Some(k) = m.keys().find(|k| !SCENARIO_KEYS.contains(&k.as_str())) {
        bail!("cost scenario: {k} is no key of the engine's Scenario (costs.py)");
    }
    if let Some(k) = SCENARIO_KEYS.iter().find(|k| !m.contains_key(**k)) {
        bail!("cost scenario: no {k}");
    }
    let lamports = |k: &str| -> Result<i128> {
        let x = m[k].as_i64().filter(|&x| x >= 0).with_context(|| format!("cost scenario: {k} {} is no lamport amount", m[k]))?;
        Ok(i128::from(x))
    };
    let land_prob = m["land_prob"].as_f64().filter(|&p| p > 0.0 && p <= 1.0).context("cost scenario: land_prob outside (0, 1]")?;
    let adverse = m["adverse_per_side"].as_f64().filter(|&a| (0.0..1.0).contains(&a)).context("cost scenario: adverse_per_side outside [0, 1)")?;
    let mode = m["rent_mode"].as_str().context("cost scenario: rent_mode is no string")?;
    Ok(Scenario {
        name: m["name"].as_str().context("cost scenario: name is no string")?.to_owned(),
        fee_per_tx: lamports("fee_per_tx")?,
        tip: lamports("tip")?,
        failed_tx: lamports("failed_tx")?,
        land_prob,
        ata_rent: lamports("ata_rent")?,
        rent_mode: RentMode::parse(mode).with_context(|| format!("cost scenario: rent_mode {mode:?}"))?,
        adverse_per_side: adverse,
    })
}

/// One journal write: a put (INSERT OR REPLACE) of a row's cells, or a delete by the key's cells (journal.py:83-89); or a
/// group's paper wallet file (its index in the signal engine's order), its whole text as json.dump writes it.
#[derive(Clone, Debug, PartialEq)]
pub enum Write {
    Put(&'static str, Vec<(&'static str, Cell)>),
    Delete(&'static str, Vec<(&'static str, Cell)>),
    Wallet(usize, String),
}

/// The wallet file's note (engine.py:1278).
const WALLET_NOTE: &str = "paper trading: no wallet, no transactions; amounts are the engine's simulated fills after costs";

fn json_int<T: ToString>(v: Option<T>) -> String {
    v.map_or_else(|| "null".to_owned(), |x| x.to_string())
}

fn json_str(s: &str) -> String {
    serde_json::to_string(s).expect("a string is JSON")
}

/// `json.dump(w, f)` of `_write_wallet`'s dict (engine.py:1275-1281): its keys in their order, ", " and ": " as Python's
/// separators, None as null. Every value is an int, None or an ASCII string (base58 mints, the family and variant).
pub fn wallet_json(w: &WalletFile, names: &impl KeyNames) -> Result<String> {
    let mut txs = Vec::with_capacity(w.txs.len());
    for t in &w.txs {
        let mint = names.name(t.mint).context("a wallet transaction of an id without a key")?;
        txs.push(format!(
            "{{\"signature\": null, \"slot\": {}, \"time_unix\": null, \"kind\": {}, \"mint\": {}, \"symbol\": null, \"amount_lamports\": {}, \
             \"fee_lamports\": {}, \"tip_lamports\": null, \"status\": \"paper\", \"landed_slot_delta\": {}, \"note\": {}}}",
            t.slot,
            json_str(t.kind),
            json_str(mint),
            t.amount_lamports,
            t.fee_lamports,
            json_int(t.landed_slot_delta),
            json_str(&format!("{} {}", t.family.as_str(), t.variant)),
        ));
    }
    Ok(format!(
        "{{\"mode\": \"paper\", \"address\": null, \"slot\": {}, \"time_unix\": {}, \"balance_lamports\": {}, \"txs\": [{}], \"note\": {}}}",
        json_int(w.slot),
        json_int(w.time_unix),
        w.balance_lamports,
        txs.join(", "),
        json_str(WALLET_NOTE),
    ))
}

fn int(v: i128, what: &str) -> Result<Cell> {
    Ok(Cell::Int(i64::try_from(v).with_context(|| format!("{what} {v} beyond INTEGER"))?))
}

fn opt_u(v: Option<u64>, what: &str) -> Result<Cell> {
    v.map_or(Ok(Cell::Null), |x| int(i128::from(x), what))
}

fn text(s: &str) -> Cell {
    Cell::Text(s.to_owned())
}

fn day(d: &Option<String>) -> Cell {
    d.as_deref().map_or(Cell::Null, text)
}

fn flag(b: bool) -> Cell {
    Cell::Int(i64::from(b))
}

/// A position's key cells (family, variant, key, t).
fn key_cells(k: &PosKey, names: &impl KeyNames) -> Result<Vec<(&'static str, Cell)>> {
    Ok(vec![
        ("family", text(k.family.as_str())),
        ("variant", text(&k.variant)),
        ("key", text(names.name(k.key).context("a position of an id without a key")?)),
        ("t", int(i128::from(k.t), "t")?),
    ])
}

fn exit_cells(x: &ExitRow, names: &impl KeyNames) -> Result<Vec<(&'static str, Cell)>> {
    let mut c = key_cells(&x.k, names)?;
    c.extend([
        ("day", day(&x.day)),
        ("entry_slot", int(i128::from(x.entry_slot), "entry_slot")?),
        ("exit_decision_slot", opt_u(x.exit_decision_slot, "exit_decision_slot")?),
        ("exit_slot", opt_u(x.exit_slot, "exit_slot")?),
        ("exit_reason", x.exit_reason.map_or(Cell::Null, text)),
        ("tokens", int(x.tokens, "tokens")?),
        ("cost", int(x.cost, "cost")?),
        ("proceeds", int(x.proceeds, "proceeds")?),
        ("pnl", int(x.pnl, "pnl")?),
        ("ret", x.ret.map_or(Cell::Null, Cell::Real)),
        ("exit_attempts", int(i128::from(x.exit_attempts), "exit_attempts")?),
        ("cf_graduation", flag(x.cf_graduation)),
        ("seed_pool", flag(x.seed_pool)),
        ("vault_capped", flag(x.vault_capped)),
        ("reverted_direct", int(i128::from(x.reverted_direct), "reverted_direct")?),
        ("reverted_router", int(i128::from(x.reverted_router), "reverted_router")?),
        ("sells_dropped", int(i128::from(x.sells_dropped), "sells_dropped")?),
        ("sells_scaled", int(i128::from(x.sells_scaled), "sells_scaled")?),
        ("peak_mark", x.peak.map_or(Ok(Cell::Null), |(_, m)| int(m, "peak_mark"))?),
        ("peak_slot", opt_u(x.peak.map(|(s, _)| s), "peak_slot")?),
        ("n_flow_slots", opt_u(x.n_flow_slots, "n_flow_slots")?),
        ("cap_slots", int(i128::from(x.cap_slots), "cap_slots")?),
    ]);
    Ok(c)
}

/// An Op as its journal write: the columns paper/engine.py's put names (engine.py:763, 926, 939, 1034-1041, 1247-1249),
/// the others NULL. A signal row's status and reason are the engine's (the hooks'), not C1b's NULL.
pub fn op_write(op: &Op, names: &impl KeyNames) -> Result<Write> {
    Ok(match op {
        Op::Signal(row, status) => {
            let mut c = row_cells(row, names)?;
            for (col, v) in c.iter_mut() {
                match *col {
                    "status" => *v = status.status().map_or(Cell::Null, text),
                    "reason" => *v = status.reason().map_or(Cell::Null, text),
                    _ => {}
                }
            }
            Write::Put("signals", c)
        }
        Op::Order { k, day: d, entry_slot, size, status, reason } => {
            let mut c = key_cells(k, names)?;
            c.extend([
                ("day", day(d)),
                ("entry_slot", int(i128::from(*entry_slot), "entry_slot")?),
                ("size", int(*size, "size")?),
                ("status", text(status)),
                ("reason", reason.as_deref().map_or(Cell::Null, text)),
            ]);
            Write::Put("orders", c)
        }
        Op::Fill { k, side, day: d, slot, tokens, quote } => {
            let mut c = key_cells(k, names)?;
            c.extend([
                ("side", text(side)),
                ("day", day(d)),
                ("slot", opt_u(*slot, "slot")?),
                ("tokens", int(*tokens, "tokens")?),
                ("quote", int(*quote, "quote")?),
            ]);
            Write::Put("fills", c)
        }
        Op::Exit(x) => Write::Put("exits", exit_cells(x, names)?),
        Op::PutPosition { k, entry_slot, cost, tokens, mark } => {
            let mut c = key_cells(k, names)?;
            let (proceeds, slot, pnl) = match mark {
                Some((net, slot, pnl)) => (int(*net, "mark_proceeds")?, int(i128::from(*slot), "mark_slot")?, int(*pnl, "mark_pnl")?),
                None => (Cell::Null, Cell::Null, Cell::Null),
            };
            c.extend([
                ("entry_slot", int(i128::from(*entry_slot), "entry_slot")?),
                ("cost", int(*cost, "cost")?),
                ("tokens", int(*tokens, "tokens")?),
                ("status", text("open")),
                ("mark_proceeds", proceeds),
                ("mark_slot", slot),
                ("mark_pnl", pnl),
            ]);
            Write::Put("positions", c)
        }
        Op::DeletePosition(k) => Write::Delete("positions", key_cells(k, names)?),
        Op::Heartbeat(h) => Write::Put("heartbeat", vec![
            ("minute", Cell::Int(h.minute)),
            ("day", day(&h.day)),
            ("last_slot", opt_u(h.last_slot, "last_slot")?),
            ("last_event", h.last_event.map_or(Cell::Null, |o| Cell::Text(format!("{}:{}:{}:{}", o.slot, o.tx, o.outer, o.inner)))),
            ("events", int(i128::from(h.events), "events")?),
            ("open_positions", int(h.open_positions as i128, "open_positions")?),
            ("gap_slots", int(i128::from(h.gap_slots), "gap_slots")?),
            ("note", text(&h.note)),
        ]),
        Op::Wallet(w) => Write::Wallet(w.group, wallet_json(w, names)?),
    })
}

/// Python's json.dumps of the base scenario (spec §3.1), as the reference journal holds it.
pub const BASE_SCENARIO: &str = concat!(
    r#"{"adverse_per_side": 0.0, "ata_rent": 1513840, "failed_tx": 505000, "fee_per_tx": 605000, "land_prob": 0.97, "#,
    r#""name": "base", "rent_mode": "refunded_on_full_exit", "tip": 100000}"#
);

/// Refuses an empty key set or a column outside the table before a delete reaches SQL (spec §5.2): `ensure` here, the
/// writer's own check in journal.rs.
pub fn delete_key_ok(w: &Write, columns: &[&str]) -> Result<()> {
    if let Write::Delete(table, key) = w {
        ensure!(!key.is_empty(), "a delete from {table} without a key");
        for (c, _) in key {
            ensure!(columns.contains(c), "a delete from {table} by {c}, no column of it");
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests;
