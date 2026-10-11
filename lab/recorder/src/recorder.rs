//! The recorder core: Yellowstone updates in, segments out. No network here (main.rs connects
//! and reconnects), so the tests drive it with synthetic updates.

use crate::convert::{self, Converted};
use crate::extract::LabPlugin;
use crate::idl::{Kind, Val};
use crate::meter::{Decision, Meter};
use crate::segment::{columns, Segments, SlotBuffer, ARRIVAL_COLS, BLOCK_COLS};
use crate::sink::{Columns, Row};
use crate::watch::{segment_watchlist, Filter, Watch, DOWNSCALE};
use anyhow::Result;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use yellowstone_grpc_proto::prelude::{
    subscribe_update::UpdateOneof, SlotStatus, SubscribeRequest, SubscribeUpdate, SubscribeUpdateAccount,
    SubscribeUpdateBlockMeta,
};
use yellowstone_grpc_proto::prost::Message as _;

const VAULT_COLS: [(&str, Kind); 7] = [
    ("slot", Kind::U64),
    ("account", Kind::Utf8),
    ("amount", Kind::U64),
    ("lamports", Kind::U64),
    ("write_version", Kind::U64),
    ("signature", Kind::Utf8),
    ("received_unix_ns", Kind::U64),
];

const POOL_COLS: [(&str, Kind); 12] = [
    ("slot", Kind::U64),
    ("pool", Kind::Utf8),
    ("base_mint", Kind::Utf8),
    ("quote_mint", Kind::Utf8),
    ("lp_mint", Kind::Utf8),
    ("pool_base_token_account", Kind::Utf8),
    ("pool_quote_token_account", Kind::Utf8),
    ("lp_supply", Kind::U64),
    ("coin_creator", Kind::Utf8),
    ("is_mayhem_mode", Kind::Bool),
    ("write_version", Kind::U64),
    ("received_unix_ns", Kind::U64),
];

pub struct Config {
    pub pool_accounts: bool,
    pub max_tx_accounts: usize,
    /// Watchlist changes are applied at most this often (downscales at once).
    pub min_update_secs: u64,
}

#[derive(Debug)]
pub enum Action {
    None,
    /// Send these requests on the open streams (main, vaults).
    Resubscribe(Box<SubscribeRequest>, Box<SubscribeRequest>),
    /// The day's budget is spent: the last segment is written, stop and stay stopped today.
    Stop(String),
}

#[derive(Default)]
struct Stats {
    failed_dropped: u64,
    convert_errors: u64,
    short_accounts: u64,
}

pub struct Recorder {
    cfg: Config,
    plugin: LabPlugin,
    pub slots: SlotBuffer,
    pub segs: Segments,
    pub meter: Meter,
    watch: Watch,
    pending_watch: Option<Watch>,
    level: usize,
    pub active: Filter,
    /// The filter when the open segment started (its watchlist.json is what both had).
    seg_before: Filter,
    last_update_unix: u64,
    block_cols: Columns,
    arrival_cols: Columns,
    vault_cols: Columns,
    pool_cols: Columns,
    stats: Stats,
    seen_pools: HashSet<Vec<u8>>,
    reconnects: Vec<Value>,
    at_last_level_noted: bool,
}

fn b58(b: &[u8]) -> String {
    bs58::encode(b).into_string()
}

fn opt_u64(v: Option<u64>) -> Val {
    v.map_or(Val::Null, Val::U64)
}

/// The stream an update is counted on: its filter (watchlist pools before wallets), or "control".
fn stream_of(u: &SubscribeUpdate) -> &str {
    if u.filters.iter().any(|f| f == "tx_pools") {
        return "tx_pools";
    }
    u.filters.first().map_or("control", String::as_str)
}

impl Recorder {
    pub fn new(cfg: Config, plugin: LabPlugin, segs: Segments, meter: Meter, watch: Watch, last_closed: Option<u64>, close_lag: u64) -> Self {
        let active = Filter::new(&watch, 0, cfg.pool_accounts, cfg.max_tx_accounts);
        Self {
            plugin,
            slots: SlotBuffer::new(close_lag, last_closed),
            segs,
            meter,
            watch,
            pending_watch: None,
            level: 0,
            seg_before: active.clone(),
            active,
            last_update_unix: 0,
            block_cols: columns(&BLOCK_COLS),
            arrival_cols: columns(&ARRIVAL_COLS),
            vault_cols: columns(&VAULT_COLS),
            pool_cols: columns(&POOL_COLS),
            stats: Stats::default(),
            seen_pools: HashSet::new(),
            reconnects: Vec::new(),
            at_last_level_noted: false,
            cfg,
        }
    }

    /// The requests for a (re)connect: the main stream resumes after the last closed slot.
    pub fn requests(&self, from_slot: Option<u64>) -> (SubscribeRequest, SubscribeRequest) {
        (self.active.request(from_slot.or(self.slots.last_closed.map(|s| s + 1))), self.active.vault_request())
    }

    pub fn on_update(&mut self, u: SubscribeUpdate, now_ns: u64) -> Result<()> {
        let stream = stream_of(&u).to_owned();
        self.meter.add(&stream, u.encoded_len());
        let created_ns = u
            .created_at
            .as_ref()
            .and_then(|t| u64::try_from(t.seconds).ok().map(|s| s * 1_000_000_000 + u64::from(t.nanos.max(0) as u32)));
        match u.update_oneof {
            Some(UpdateOneof::Transaction(t)) => {
                let Some(info) = t.transaction.as_ref() else { return Ok(()) };
                match convert::transaction(t.slot, info) {
                    Ok(Converted::Tx(tx)) => {
                        let mut rows = self.plugin.rows_for(&tx);
                        rows.push(Row {
                            table: Arc::from("arrivals"),
                            columns: self.arrival_cols.clone(),
                            values: vec![
                                Val::U64(t.slot),
                                Val::U64(info.index),
                                Val::Str(tx.signature.to_string()),
                                Val::U64(now_ns),
                                opt_u64(created_ns),
                                Val::Str(stream),
                            ],
                        });
                        self.slots.push(t.slot, rows);
                    }
                    Ok(Converted::Failed) => self.stats.failed_dropped += 1,
                    Err(e) => {
                        self.stats.convert_errors += 1;
                        if self.stats.convert_errors <= 10 {
                            log::error!("slot {} tx {}: cannot convert: {e:#}", t.slot, info.index);
                        }
                    }
                }
            }
            Some(UpdateOneof::BlockMeta(b)) => {
                let row = self.block_row(&b);
                self.slots.push(b.slot, vec![row]);
            }
            Some(UpdateOneof::Slot(s)) => {
                if s.status == SlotStatus::SlotConfirmed as i32 {
                    for (slot, rows) in self.slots.confirm(s.slot) {
                        self.segs.add_slot(slot, rows);
                    }
                }
            }
            Some(UpdateOneof::Account(a)) => {
                let row = if stream == "pool_accounts" { self.pool_row(&a, now_ns) } else { self.vault_row(&a, now_ns) };
                if let Some(r) = row {
                    self.segs.add_rows(vec![r]);
                }
            }
            _ => {}
        }
        Ok(())
    }

    fn block_row(&self, b: &SubscribeUpdateBlockMeta) -> Row {
        Row {
            table: Arc::from("blocks"),
            columns: self.block_cols.clone(),
            values: vec![
                Val::U64(b.slot),
                Val::U64(b.parent_slot),
                b.block_time.as_ref().map_or(Val::Null, |t| Val::I64(t.timestamp)),
                b.block_height.as_ref().map_or(Val::Null, |h| Val::U64(h.block_height)),
                Val::U64(b.executed_transaction_count),
                Val::U64(b.entries_count),
                Val::Bool(false),
            ],
        }
    }

    fn vault_row(&mut self, a: &SubscribeUpdateAccount, now_ns: u64) -> Option<Row> {
        let acc = a.account.as_ref()?;
        let amount = acc.data.get(..8).map(|d| u64::from_le_bytes(d.try_into().expect("8 bytes")));
        if amount.is_none() {
            self.stats.short_accounts += 1;
        }
        Some(Row {
            table: Arc::from("vaults"),
            columns: self.vault_cols.clone(),
            values: vec![
                Val::U64(a.slot),
                Val::Str(b58(&acc.pubkey)),
                opt_u64(amount),
                Val::U64(acc.lamports),
                Val::U64(acc.write_version),
                acc.txn_signature.as_ref().map_or(Val::Null, |s| Val::Str(b58(s))),
                Val::U64(now_ns),
            ],
        })
    }

    /// A pool's first update in this process (the account is written by every trade, so later
    /// updates carry nothing new for us).
    fn pool_row(&mut self, a: &SubscribeUpdateAccount, now_ns: u64) -> Option<Row> {
        let acc = a.account.as_ref()?;
        let d = &acc.data;
        if d.len() < 201 {
            self.stats.short_accounts += 1;
            return None;
        }
        if !self.seen_pools.insert(acc.pubkey.clone()) {
            return None;
        }
        let k = |i: usize| Val::Str(b58(&d[i..i + 32]));
        Some(Row {
            table: Arc::from("pool_accounts"),
            columns: self.pool_cols.clone(),
            values: vec![
                Val::U64(a.slot),
                Val::Str(b58(&acc.pubkey)),
                k(0),
                k(32),
                k(64),
                k(96),
                k(128),
                Val::U64(u64::from_le_bytes(d[160..168].try_into().expect("8 bytes"))),
                k(168),
                Val::Bool(d[200] != 0),
                Val::U64(acc.write_version),
                Val::U64(now_ns),
            ],
        })
    }

    pub fn on_disconnect(&mut self, now_unix: u64, reason: &str) {
        self.reconnects.push(json!({"unix": now_unix, "after_slot": self.slots.last_closed,
                                    "dropped_pending_slots": self.slots.pending_slots(), "reason": reason}));
        self.slots.reset();
    }

    fn segment_meta(&mut self, note: Option<&str>) -> serde_json::Map<String, Value> {
        let c = &self.plugin.counters;
        let mut m = serde_json::Map::new();
        m.insert("usage".into(), Value::Array(self.meter.take_usage()));
        m.insert("budget".into(), self.meter.state());
        m.insert("filter".into(), self.active.summary());
        m.insert("reconnects".into(), Value::Array(std::mem::take(&mut self.reconnects)));
        m.insert(
            "totals".into(),
            json!({
                "late_rows": self.slots.late,
                "unconfirmed_slots": self.slots.unconfirmed,
                "failed_dropped": self.stats.failed_dropped,
                "convert_errors": self.stats.convert_errors,
                "short_accounts": self.stats.short_accounts,
                "txs_touching": c.txs_touching.load(Ordering::Relaxed),
                "events": c.events.load(Ordering::Relaxed),
                "anomalies": c.anomalies.load(Ordering::Relaxed),
            }),
        );
        if let Some(n) = note {
            m.insert("note".into(), json!(n));
        }
        m
    }

    /// Write the open segment if it has closed slots; the next segment's watchlist is the
    /// current filter.
    pub fn close_segment(&mut self, note: Option<&str>) -> Result<()> {
        if !self.segs.has_slots() {
            return Ok(());
        }
        let wl = segment_watchlist(&self.seg_before, &self.active, &self.watch.source);
        let meta = self.segment_meta(note);
        if let Some(dir) = self.segs.close(&wl, meta)? {
            log::info!("segment {} written (slots up to {:?})", dir.display(), self.slots.last_closed);
        }
        self.seg_before = self.active.clone();
        Ok(())
    }

    fn change(&mut self, f: Filter, now_unix: u64) -> Result<Action> {
        if f == self.active {
            return Ok(Action::None);
        }
        self.close_segment(None)?;
        self.seg_before = std::mem::replace(&mut self.active, f);
        self.last_update_unix = now_unix;
        log::info!("subscription now {}", self.active.summary());
        Ok(Action::Resubscribe(Box::new(self.active.request(None)), Box::new(self.active.vault_request())))
    }

    /// Once a second: the budget, a changed watchlist, and a due segment.
    pub fn on_tick(&mut self, now_unix: u64, reload: Option<Watch>) -> Result<Action> {
        match self.meter.tick(now_unix)? {
            Decision::Stop(reason) => {
                self.meter.note(format!("hard stop: {reason}"));
                self.meter.tick(now_unix + 60)?; // flush this minute's usage into the last segment
                self.close_segment(Some("hard stop"))?;
                return Ok(Action::Stop(reason));
            }
            Decision::Downscale(reason) => {
                if self.level < DOWNSCALE.len() {
                    self.level += 1;
                    self.meter.note(format!("downscale to level {}: {} off ({reason})", self.level, DOWNSCALE[self.level - 1]));
                    let f = Filter::new(&self.watch, self.level, self.cfg.pool_accounts, self.cfg.max_tx_accounts);
                    return self.change(f, now_unix);
                }
                if !self.at_last_level_noted {
                    self.at_last_level_noted = true;
                    self.meter.note(format!("all optional streams are off; the hard stop follows at the cap ({reason})"));
                }
            }
            Decision::Restore => {
                self.level = 0;
                self.at_last_level_noted = false;
                self.meter.note("new UTC day: all streams back on".to_owned());
                let f = Filter::new(&self.watch, 0, self.cfg.pool_accounts, self.cfg.max_tx_accounts);
                return self.change(f, now_unix);
            }
            Decision::Continue => {}
        }
        if let Some(w) = reload {
            self.pending_watch = Some(w);
        }
        if self.pending_watch.is_some() && now_unix >= self.last_update_unix + self.cfg.min_update_secs {
            self.watch = self.pending_watch.take().expect("checked");
            let f = Filter::new(&self.watch, self.level, self.cfg.pool_accounts, self.cfg.max_tx_accounts);
            let a = self.change(f, now_unix)?;
            if !matches!(a, Action::None) {
                return Ok(a);
            }
        }
        if self.segs.is_due() {
            self.close_segment(None)?;
        }
        Ok(Action::None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::convert::tests::{fake_key, plugin, sample};
    use std::path::{Path, PathBuf};
    use yellowstone_grpc_proto::prelude::{
        SubscribeUpdateAccountInfo, SubscribeUpdateSlot, SubscribeUpdateTransaction, UnixTimestamp,
    };

    const T0: u64 = 1_791_763_200;
    const POOL: &str = "So11111111111111111111111111111111111111112";

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-recorder-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn recorder(root: &Path, cap: u64, seg_slots: u64) -> Recorder {
        let (segs, last) = Segments::open(root, seg_slots, 3600).unwrap();
        let meter = Meter::load(root, cap, 1.0, T0).unwrap();
        let watch = Watch::parse(Some(&json!({"pools": [{"pool": POOL}], "wallets": [], "vaults": [POOL]})), None);
        let cfg = Config { pool_accounts: true, max_tx_accounts: 10_000, min_update_secs: 60 };
        Recorder::new(cfg, plugin(), segs, meter, watch, last, 1)
    }

    fn update(filter: &str, u: UpdateOneof) -> SubscribeUpdate {
        SubscribeUpdate { filters: vec![filter.to_owned()], update_oneof: Some(u), created_at: None }
    }

    fn tx(slot: u64, index: u64) -> SubscribeUpdate {
        update("tx_pools", UpdateOneof::Transaction(SubscribeUpdateTransaction { transaction: Some(sample(slot, index, true, false)), slot, bank_id: 0 }))
    }

    fn meta(slot: u64) -> SubscribeUpdate {
        update("blocks", UpdateOneof::BlockMeta(SubscribeUpdateBlockMeta {
            slot,
            parent_slot: slot - 1,
            block_time: Some(UnixTimestamp { timestamp: 1_791_763_200 }),
            executed_transaction_count: 1000,
            entries_count: 300,
            ..Default::default()
        }))
    }

    fn confirmed(slot: u64) -> SubscribeUpdate {
        update("slots", UpdateOneof::Slot(SubscribeUpdateSlot { slot, status: SlotStatus::SlotConfirmed as i32, ..Default::default() }))
    }

    fn segment_meta(root: &Path, seq: u64) -> Value {
        serde_json::from_slice(&std::fs::read(root.join(format!("segments/{seq:09}/_segment.json"))).unwrap()).unwrap()
    }

    #[test]
    fn confirmed_slots_become_a_segment_in_the_of1_layout() {
        let root = tmp("rec-layout");
        let mut r = recorder(&root, 25_000_000_000, 2);
        for u in [tx(1000, 4), meta(1000), confirmed(1000), meta(1001), confirmed(1001), meta(1002), confirmed(1002)] {
            r.on_update(u, 1).unwrap();
        }
        assert!(matches!(r.on_tick(T0 + 1, None).unwrap(), Action::None));
        let seg = root.join("segments/000000000");
        for t in ["anomalies", "blocks", "arrivals"] {
            assert!(seg.join(t).join("part-00000.parquet").exists(), "{t}");
        }
        let m = segment_meta(&root, 0);
        assert_eq!((m["first_slot"].as_u64(), m["last_slot"].as_u64()), (Some(1000), Some(1001)));
        assert_eq!(m["rows"]["arrivals"], 1);
        let wl: Value = serde_json::from_slice(&std::fs::read(seg.join("watchlist.json")).unwrap()).unwrap();
        assert_eq!(wl["pools"], json!([{"pool": POOL}]));
        // A late transaction of a written slot is counted, not written.
        r.on_update(tx(1000, 5), 2).unwrap();
        assert_eq!(r.slots.late, 2); // its arrival row and its anomaly row
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn usage_reaches_the_segment_and_the_budget_downscales_then_stops() {
        let root = tmp("rec-budget");
        let mut r = recorder(&root, 1_440_000, 1);
        r.on_update(tx(1000, 0), 1).unwrap();
        for u in [meta(1000), confirmed(1000), confirmed(1001)] {
            r.on_update(u, 1).unwrap();
        }
        // Over 20x the sustainable rate within the first minute: downscale at once.
        r.meter.add("vaults", 30_000);
        let (main, vaults) = match r.on_tick(T0 + 5, None).unwrap() {
            Action::Resubscribe(m, v) => (m, v),
            other => panic!("expected a resubscribe, got {other:?}"),
        };
        assert!(main.accounts.is_empty() && !vaults.accounts.is_empty()); // pool accounts off first
        assert_eq!(r.active.level, 1);
        // The cap: the last segment is written with the usage and the stop.
        for u in [meta(1002), confirmed(1002), confirmed(1003)] {
            r.on_update(u, 1).unwrap();
        }
        r.meter.add("tx_pools", 2_000_000);
        assert!(matches!(r.on_tick(T0 + 70, None).unwrap(), Action::Stop(_)));
        let last = segment_meta(&root, r.segs.seq - 1);
        let usage = last["usage"].as_array().unwrap();
        assert!(usage.iter().any(|u| u["stream"] == "budget" && u["note"].as_str().unwrap().contains("hard stop")));
        assert!(segment_meta(&root, 0)["usage"].as_array().unwrap().is_empty()); // its minute was not over yet
        assert!(Meter::load(&root, 1_440_000, 1.0, T0 + 100).unwrap().stopped);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_disconnect_drops_unclosed_slots_and_resumes_after_the_last_closed() {
        let root = tmp("rec-reconnect");
        let mut r = recorder(&root, 25_000_000_000, 100);
        for u in [meta(1000), confirmed(1000), meta(1001), confirmed(1001), tx(1002, 0)] {
            r.on_update(u, 1).unwrap();
        }
        r.on_disconnect(T0, "test");
        let (main, _) = r.requests(None);
        assert_eq!(main.from_slot, Some(1001)); // slot 1000 closed (lag 1), 1001 not yet
        assert_eq!(r.slots.pending_slots(), 0);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn vault_and_pool_account_updates_become_rows() {
        let root = tmp("rec-accounts");
        let mut r = recorder(&root, 25_000_000_000, 1);
        let mut data = vec![0u8; 201];
        data[160..168].copy_from_slice(&5u64.to_le_bytes());
        data[200] = 1;
        let account = |filter: &str, data: Vec<u8>| {
            update(filter, UpdateOneof::Account(SubscribeUpdateAccount {
                account: Some(SubscribeUpdateAccountInfo { pubkey: fake_key(3), data, lamports: 2_039_280, write_version: 9, ..Default::default() }),
                slot: 1000,
                ..Default::default()
            }))
        };
        r.on_update(account("pool_accounts", data.clone()), 1).unwrap();
        r.on_update(account("pool_accounts", data), 1).unwrap(); // the same pool again: no row
        r.on_update(account("vaults", 42u64.to_le_bytes().to_vec()), 1).unwrap();
        for u in [meta(1000), confirmed(1000), confirmed(1001)] {
            r.on_update(u, 1).unwrap();
        }
        r.on_tick(T0 + 1, None).unwrap();
        let m = segment_meta(&root, 0);
        assert_eq!((m["rows"]["pool_accounts"].as_u64(), m["rows"]["vaults"].as_u64()), (Some(1), Some(1)));
        std::fs::remove_dir_all(&root).unwrap();
    }
}
