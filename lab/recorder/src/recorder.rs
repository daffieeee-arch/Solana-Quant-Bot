//! The recorder core: Yellowstone updates in, segments out. No network here (main.rs connects
//! and reconnects), so the tests drive it with synthetic updates.

use crate::convert::{self, Converted};
use crate::extract::LabPlugin;
use crate::idl::{Kind, Val};
use crate::meter::{Decision, Meter};
use crate::segment::{columns, write_json_atomic, Segments, SlotBuffer, ANOMALY_COLS, ARRIVAL_COLS, BLOCK_COLS, FOOTER_COLS};
use crate::sink::{Columns, Row};
use crate::watch::{Extra, Filter, Opts, SegWatch, Watch, DOWNSCALE, WSOL};
use anyhow::Result;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use yellowstone_grpc_proto::prelude::{
    subscribe_update::UpdateOneof, CommitmentLevel, SlotStatus, SubscribeRequest, SubscribeRequestFilterSlots,
    SubscribeRequestFilterTransactions, SubscribeUpdate, SubscribeUpdateAccount,
    SubscribeUpdateBlockFooter, SubscribeUpdateBlockMeta,
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

/// Slots past the last closed slot a backfill replays to, so it overlaps the probe's own
/// transactions (LiveFeed deduplicates the overlap by event position).
const BACKFILL_MARGIN_SLOTS: u64 = 150;

struct Backfill {
    pool: String,
    from_slot: u64,
    to_slot: u64,
    rows: Vec<Row>,
    started_unix: u64,
    done: bool,
    failed: Option<String>,
    /// The connection was told to close its subscription.
    closed: bool,
    /// Written to a segment (rows and record).
    reported: bool,
}

pub struct Config {
    pub opts: Opts,
    /// Watchlist changes are applied at most this often (downscales and probes at once).
    pub min_update_secs: u64,
    /// A new pool with at least this much WSOL becomes a probe: full transactions for
    /// `probe_secs`. Below it, its WSOL vault is followed for `fresh_vault_secs` (reversed pools
    /// only; 0 = off) and the pool becomes a probe when the vault reaches the depth.
    pub probe_lamports: u64,
    pub probe_secs: u64,
    pub fresh_vault_secs: u64,
    /// A segment waits this long for running backfills before it closes without them.
    pub backfill_wait_secs: u64,
    /// At most this many backfills at once (more probes are not backfilled).
    pub backfill_max: usize,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            opts: Opts::default(),
            min_update_secs: 60,
            probe_lamports: 120_000_000_000,
            probe_secs: 3 * 3600,
            fresh_vault_secs: 3600,
            backfill_wait_secs: 20,
            backfill_max: 8,
        }
    }
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
    backfills_skipped: u64,
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
    /// The open segment's filter over its filter changes (its watchlist.json).
    seg_watch: SegWatch,
    last_update_unix: u64,
    block_cols: Columns,
    footer_cols: Columns,
    anomaly_cols: Columns,
    arrival_cols: Columns,
    vault_cols: Columns,
    pool_cols: Columns,
    stats: Stats,
    /// The first Alpenglow block footer was handled (this run, or an earlier one: <root>/alpenglow.json).
    alpenglow_noted: bool,
    seen_pools: HashSet<Vec<u8>>,
    reconnects: Vec<Value>,
    at_last_level_noted: bool,
    /// Probe pool -> until (unix seconds).
    probes: BTreeMap<String, u64>,
    /// WSOL vault of a new pool -> (pool, until, its create slot).
    fresh: HashMap<String, (String, u64, Option<u64>)>,
    /// Backfills by id ("<pool>-<from_slot>"), and the subscriptions still to open.
    backfills: BTreeMap<String, Backfill>,
    backfill_requests: Vec<(String, SubscribeRequest)>,
    /// Probes or new vaults were added: resubscribe at the next tick, without the rate limit
    /// (a new pool's first trades come within minutes).
    extra_dirty: bool,
    /// Probes or new vaults expired: resubscribe with the rate limit.
    extra_stale: bool,
}

fn val<'a>(r: &'a Row, name: &str) -> Option<&'a Val> {
    r.columns.iter().position(|(n, _)| n == name).and_then(|i| r.values.get(i))
}

fn val_str(r: &Row, name: &str) -> Option<String> {
    match val(r, name) {
        Some(Val::Str(s)) => Some(s.clone()),
        _ => None,
    }
}

fn val_u64(r: &Row, name: &str) -> Option<u64> {
    match val(r, name) {
        Some(Val::U64(v)) => Some(*v),
        _ => None,
    }
}

fn b58(b: &[u8]) -> String {
    bs58::encode(b).into_string()
}

fn opt_u64(v: Option<u64>) -> Val {
    v.map_or(Val::Null, Val::U64)
}

/// The stream an update is counted on: its filter (watchlist pools, then probes, before the
/// rest), or "control".
fn stream_of(u: &SubscribeUpdate) -> &str {
    for first in ["tx_pools", "tx_probes"] {
        if u.filters.iter().any(|f| f == first) {
            return first;
        }
    }
    u.filters.first().map_or("control", String::as_str)
}

impl Recorder {
    pub fn new(cfg: Config, plugin: LabPlugin, segs: Segments, meter: Meter, watch: Watch, last_closed: Option<u64>, close_lag: u64) -> Self {
        let active = Filter::new(&watch, &Extra::default(), 0, &cfg.opts);
        Self {
            plugin,
            slots: SlotBuffer::new(close_lag, last_closed),
            segs,
            meter,
            watch,
            pending_watch: None,
            level: 0,
            seg_watch: SegWatch::start(&active),
            active,
            last_update_unix: 0,
            block_cols: columns(&BLOCK_COLS),
            footer_cols: columns(&FOOTER_COLS),
            anomaly_cols: columns(&ANOMALY_COLS),
            alpenglow_noted: false,
            arrival_cols: columns(&ARRIVAL_COLS),
            vault_cols: columns(&VAULT_COLS),
            pool_cols: columns(&POOL_COLS),
            stats: Stats::default(),
            seen_pools: HashSet::new(),
            reconnects: Vec::new(),
            at_last_level_noted: false,
            probes: BTreeMap::new(),
            fresh: HashMap::new(),
            backfills: BTreeMap::new(),
            backfill_requests: Vec::new(),
            extra_dirty: false,
            extra_stale: false,
            cfg,
        }
    }

    /// A pool created in this transaction (its CreatePoolEvent row): a probe at once when it holds
    /// enough WSOL; else, for reversed pools, follow its WSOL vault (from the create_pool accounts).
    fn on_creation(&mut self, r: &Row, now: u64) {
        let (Some(pool), Some(base), Some(quote)) = (val_str(r, "pool"), val_str(r, "base_mint"), val_str(r, "quote_mint")) else {
            return;
        };
        let (wsol, vault_name) = if base == WSOL {
            (val_u64(r, "pool_base_amount").or(val_u64(r, "base_amount_in")), "pool_base_token_account")
        } else if quote == WSOL {
            (val_u64(r, "pool_quote_amount").or(val_u64(r, "quote_amount_in")), "pool_quote_token_account")
        } else {
            return;
        };
        let created = val_u64(r, "slot");
        if wsol.is_some_and(|w| w >= self.cfg.probe_lamports) {
            self.probe(pool, now, created);
        } else if base == WSOL {
            let vault = val_str(r, "ix_accounts")
                .and_then(|j| serde_json::from_str::<Value>(&j).ok())
                .and_then(|v| v[vault_name].as_str().map(str::to_owned));
            if let Some(v) = vault {
                self.follow_vault(v, pool, now, created);
            }
        }
    }

    /// Full transactions of `pool` for probe_secs; its earlier transactions since `created` (its
    /// create slot) come from a backfill subscription (from_slot), for LiveFeed's farmer history.
    fn probe(&mut self, pool: String, now: u64, created: Option<u64>) {
        if self.cfg.probe_secs > 0 && !self.probes.contains_key(&pool) {
            self.probes.insert(pool.clone(), now + self.cfg.probe_secs);
            self.extra_dirty = true;
            if let Some(from) = created {
                self.request_backfill(pool, from, now);
            }
        }
    }

    fn follow_vault(&mut self, vault: String, pool: String, now: u64, created: Option<u64>) {
        if self.cfg.fresh_vault_secs > 0 && !self.fresh.contains_key(&vault) {
            self.fresh.insert(vault, (pool, now + self.cfg.fresh_vault_secs, created));
            self.extra_dirty = true;
        }
    }

    fn request_backfill(&mut self, pool: String, from_slot: u64, now: u64) {
        let running = self.backfills.values().filter(|b| !b.done).count();
        if !self.active.backfill || running >= self.cfg.backfill_max {
            self.stats.backfills_skipped += 1;
            return;
        }
        // Up to where the main stream surely has the probe: the last closed slot plus a margin.
        let to_slot = self.slots.last_closed.unwrap_or(from_slot).max(from_slot) + BACKFILL_MARGIN_SLOTS;
        let id = format!("{pool}-{from_slot}");
        let req = SubscribeRequest {
            transactions: HashMap::from([(
                "backfill".to_owned(),
                SubscribeRequestFilterTransactions {
                    vote: Some(false),
                    failed: Some(false),
                    account_include: vec![pool.clone()],
                    ..Default::default()
                },
            )]),
            slots: HashMap::from([(
                "backfill_slots".to_owned(),
                SubscribeRequestFilterSlots { filter_by_commitment: Some(true), interslot_updates: Some(false) },
            )]),
            commitment: Some(CommitmentLevel::Confirmed as i32),
            from_slot: Some(from_slot),
            ..Default::default()
        };
        self.backfills.insert(id.clone(), Backfill { pool, from_slot, to_slot, rows: Vec::new(), started_unix: now, done: false,
                                                    failed: None, closed: false, reported: false });
        self.backfill_requests.push((id, req));
    }

    /// New backfill subscriptions for the connection to open: (id, request).
    pub fn take_backfill_requests(&mut self) -> Vec<(String, SubscribeRequest)> {
        std::mem::take(&mut self.backfill_requests)
    }

    /// An update of backfill `id`: its transactions' rows are kept until the replay reaches its
    /// to_slot (a confirmed slot at or after it), then it is done.
    pub fn on_backfill_update(&mut self, id: &str, u: SubscribeUpdate) -> Result<()> {
        self.meter.add("backfill", u.encoded_len());
        let Some(b) = self.backfills.get_mut(id) else { return Ok(()) };
        if b.done {
            return Ok(());
        }
        match u.update_oneof {
            Some(UpdateOneof::Transaction(t)) => {
                let Some(info) = t.transaction.as_ref() else { return Ok(()) };
                if let Ok(Converted::Tx(tx)) = convert::transaction(t.slot, info) {
                    let rows = self.plugin.rows_for(&tx);
                    if let Some(b) = self.backfills.get_mut(id) {
                        b.rows.extend(rows);
                    }
                }
            }
            Some(UpdateOneof::Slot(s)) if s.status == SlotStatus::SlotConfirmed as i32 && s.slot >= b.to_slot => b.done = true,
            _ => {}
        }
        Ok(())
    }

    /// Backfill `id` failed (e.g. its from_slot is older than the server keeps) or its stream
    /// ended before to_slot: it is reported as failed with whatever rows arrived.
    pub fn backfill_failed(&mut self, id: &str, reason: &str) {
        if let Some(b) = self.backfills.get_mut(id) {
            if !b.done {
                b.done = true;
                b.failed = Some(reason.chars().take(300).collect());
            }
        }
    }

    /// Backfills whose subscription the connection should close now (each id once).
    pub fn take_finished_backfills(&mut self) -> Vec<String> {
        let mut out = Vec::new();
        for (id, b) in self.backfills.iter_mut() {
            if b.done && !b.closed {
                b.closed = true;
                out.push(id.clone());
            }
        }
        out
    }

    /// The open segment may close: no backfill is still running unless it has waited too long.
    fn backfills_settled(&self, now: u64) -> bool {
        self.backfills.values().all(|b| b.done || now >= b.started_unix + self.cfg.backfill_wait_secs)
    }

    fn filter(&self, level: usize) -> Filter {
        let extra = Extra { probes: self.probes.keys().cloned().collect(), fresh_vaults: self.fresh.keys().cloned().collect() };
        Filter::new(&self.watch, &extra, level, &self.cfg.opts)
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
                        let now = now_ns / 1_000_000_000;
                        for r in rows.iter().filter(|r| &*r.table == "pump_amm/CreatePoolEvent") {
                            self.on_creation(r, now);
                        }
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
                            // A fixed category and the count only: nothing derived from the update, not even
                            // its slot or index (CodeQL #9 taints the whole update, account updates included).
                            log::error!("cannot convert a transaction update: {} ({} so far)", e.0, self.stats.convert_errors);
                        }
                    }
                }
            }
            Some(UpdateOneof::BlockMeta(b)) => {
                let row = self.block_row(&b);
                self.slots.push(b.slot, vec![row]);
            }
            Some(UpdateOneof::BlockFooter(f)) => {
                let mut rows = vec![Row {
                    table: Arc::from("block_footers"),
                    columns: self.footer_cols.clone(),
                    values: vec![
                        Val::U64(f.slot),
                        Val::U64(f.bank_id),
                        Val::U64(f.block_producer_time_nanos),
                        Val::U64(now_ns),
                        opt_u64(created_ns),
                    ],
                }];
                rows.extend(self.first_footer(&f, now_ns));
                // Not gated by the slot's close: a footer comes when the validator reports it, which
                // can be after its slot (and the next) confirmed. Consumers join on (slot, bank_id).
                self.segs.add_rows(rows);
            }
            Some(UpdateOneof::Slot(s)) => {
                if s.status == SlotStatus::SlotConfirmed as i32 {
                    for (slot, rows) in self.slots.confirm(s.slot) {
                        self.segs.add_slot(slot, rows);
                    }
                }
            }
            Some(UpdateOneof::Account(a)) => {
                let row = if stream == "pool_accounts" || stream == "pool_creations" {
                    self.pool_row(&a, now_ns, &stream)
                } else {
                    self.vault_row(&a, now_ns, &stream)
                };
                if let Some(r) = row {
                    self.segs.add_rows(vec![r]);
                }
            }
            _ => {}
        }
        Ok(())
    }

    /// The first Alpenglow block footer this recorder root ever received (the switch, epoch 1064):
    /// logged, kept in <root>/alpenglow.json and returned as an anomaly row of its slot. Nothing
    /// after that, also not after a restart (the file says it was noted).
    fn first_footer(&mut self, f: &SubscribeUpdateBlockFooter, now_ns: u64) -> Option<Row> {
        if self.alpenglow_noted {
            return None;
        }
        self.alpenglow_noted = true;
        let path = self.segs.root().join("alpenglow.json");
        if path.exists() {
            return None;
        }
        // The log names only the file: the slot and the footer's fields go into it, not into the log
        // (CodeQL rust/cleartext-logging treats every field of a subscription update as account data).
        log::warn!("first Alpenglow block footer received (slot and times in {})", path.display());
        let note = json!({"first_footer_slot": f.slot, "bank_id": f.bank_id, "block_producer_time_ns": f.block_producer_time_nanos,
                          "received_unix_ns": now_ns});
        if let Err(e) = write_json_atomic(&path, &note) {
            log::error!("cannot write {}: {e:#}", path.display());
        }
        Some(Row {
            table: Arc::from("anomalies"),
            columns: self.anomaly_cols.clone(),
            values: vec![
                Val::U64(f.slot),
                Val::Null,
                Val::Null,
                Val::Null,
                Val::Null,
                Val::Str("recorder".to_owned()),
                Val::Str("alpenglow_first_footer".to_owned()),
                Val::Null,
                Val::Null,
            ],
        })
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

    /// A vault balance (`vaults` or `new_vaults`). A new pool's WSOL vault at the probe depth
    /// makes the pool a probe.
    fn vault_row(&mut self, a: &SubscribeUpdateAccount, now_ns: u64, stream: &str) -> Option<Row> {
        let acc = a.account.as_ref()?;
        let amount = acc.data.get(..8).map(|d| u64::from_le_bytes(d.try_into().expect("8 bytes")));
        if amount.is_none() {
            self.stats.short_accounts += 1;
        }
        if amount.is_some_and(|w| w >= self.cfg.probe_lamports) {
            if let Some((pool, _, created)) = self.fresh.get(&b58(&acc.pubkey)).cloned() {
                self.probe(pool, now_ns / 1_000_000_000, created);
            }
        }
        Some(Row {
            table: Arc::from(if stream == "new_vaults" { "new_vaults" } else { "vaults" }),
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

    /// A pool account update. `pool_accounts`: a pool's first update in this process (the account
    /// is written by every trade, so later updates carry nothing new). `pool_creations`: every
    /// update (to verify the creation-only hypothesis); a new reversed pool's WSOL vault is followed.
    fn pool_row(&mut self, a: &SubscribeUpdateAccount, now_ns: u64, stream: &str) -> Option<Row> {
        let acc = a.account.as_ref()?;
        let d = &acc.data;
        if d.len() < 201 {
            self.stats.short_accounts += 1;
            return None;
        }
        if stream == "pool_creations" {
            if b58(&d[0..32]) == WSOL {
                self.follow_vault(b58(&d[96..128]), b58(&acc.pubkey), now_ns / 1_000_000_000, Some(a.slot));
            }
        } else if !self.seen_pools.insert(acc.pubkey.clone()) {
            return None;
        }
        let k = |i: usize| Val::Str(b58(&d[i..i + 32]));
        Some(Row {
            table: Arc::from(if stream == "pool_creations" { "pool_creations" } else { "pool_accounts" }),
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

    /// The server's replay window starts after the slot we wanted to resume from: those slots are
    /// lost; recorded with the next segment's reconnects.
    pub fn note_replay_gap(&mut self, now_unix: u64, wanted: u64, first_available: u64) {
        self.reconnects.push(json!({"unix": now_unix, "after_slot": self.slots.last_closed, "replay_from": first_available,
                                    "lost_slots": [wanted, first_available - 1],
                                    "reason": format!("replay window starts at slot {first_available}")}));
    }

    pub fn on_disconnect(&mut self, now_unix: u64, reason: &str) {
        let running: Vec<String> = self.backfills.iter().filter(|(_, b)| !b.done).map(|(id, _)| id.clone()).collect();
        for id in running {
            self.backfill_failed(&id, "disconnected");
        }
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
                "backfills_skipped": self.stats.backfills_skipped,
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
        // Finished backfills go into this segment (rows under backfill/); running ones are listed.
        let mut records = Vec::new();
        for (id, b) in self.backfills.iter_mut() {
            if b.done && !b.reported {
                b.reported = true;
                let rows = std::mem::take(&mut b.rows);
                records.push(json!({"id": id, "pool": b.pool, "from_slot": b.from_slot, "to_slot": b.to_slot,
                                    "rows": rows.len(), "status": if b.failed.is_some() { "failed" } else { "complete" },
                                    "error": b.failed}));
                self.segs.add_backfill(rows);
            } else if !b.done {
                records.push(json!({"id": id, "pool": b.pool, "from_slot": b.from_slot, "to_slot": b.to_slot, "status": "running"}));
            }
        }
        self.backfills.retain(|_, b| !(b.reported && b.closed));
        let wl = self.seg_watch.json(&self.active, &self.watch.source);
        let mut meta = self.segment_meta(note);
        if !records.is_empty() {
            meta.insert("backfills".into(), Value::Array(records));
        }
        if let Some(dir) = self.segs.close(&wl, meta)? {
            log::info!("segment {} written (slots up to {:?})", dir.display(), self.slots.last_closed);
        }
        self.seg_watch = SegWatch::start(&self.active);
        Ok(())
    }

    /// A new subscription filter. The open segment stays open: its watchlist.json keeps what every
    /// filter inside it had (SegWatch).
    fn change(&mut self, f: Filter, now_unix: u64) -> Result<Action> {
        if f.same_streams(&self.active) {
            self.active.level = f.level; // a level step over streams that are off: no resubscribe
            return Ok(Action::None);
        }
        self.seg_watch.change(&self.active, &f);
        self.active = f;
        self.last_update_unix = now_unix;
        log::info!("subscription now {}", self.active.summary());
        Ok(Action::Resubscribe(Box::new(self.active.request(None)), Box::new(self.active.vault_request())))
    }

    /// Once a second: the budget, a changed watchlist, and a due segment.
    pub fn on_tick(&mut self, now_unix: u64, reload: Option<Watch>) -> Result<Action> {
        match self.meter.tick(now_unix)? {
            Decision::Stop(reason) => {
                self.meter.note(format!("hard stop: {reason}"));
                self.meter.flush()?; // this minute's usage goes into the last segment
                self.close_segment(Some("hard stop"))?;
                return Ok(Action::Stop(reason));
            }
            Decision::Downscale(reason) => {
                // Step over streams that are off already (not configured, or dropped by the
                // filter): one decision drops one stream that is actually on. The filter carries
                // its level, so the comparison leaves the level out.
                while self.level < DOWNSCALE.len() {
                    self.level += 1;
                    let f = self.filter(self.level);
                    if !f.same_streams(&self.active) {
                        self.meter.note(format!("downscale to level {}: {} off ({reason})", self.level, DOWNSCALE[self.level - 1]));
                        return self.change(f, now_unix);
                    }
                }
                self.active.level = self.level; // the remaining levels changed nothing; the level still moved on
                if !self.at_last_level_noted {
                    self.at_last_level_noted = true;
                    self.meter.note(format!("all optional streams are off; the hard stop follows at the cap ({reason})"));
                }
            }
            Decision::Restore => {
                self.level = 0;
                self.at_last_level_noted = false;
                self.meter.note("new UTC day: all streams back on".to_owned());
                let f = self.filter(0);
                return self.change(f, now_unix);
            }
            Decision::Continue => {}
        }
        if let Some(w) = reload {
            self.pending_watch = Some(w);
        }
        let (n_probes, n_fresh) = (self.probes.len(), self.fresh.len());
        self.probes.retain(|_, until| *until > now_unix);
        self.fresh.retain(|_, (_, until, _)| *until > now_unix);
        if (self.probes.len(), self.fresh.len()) != (n_probes, n_fresh) {
            self.extra_stale = true;
        }
        let rate_ok = now_unix >= self.last_update_unix + self.cfg.min_update_secs;
        if self.extra_dirty || (self.extra_stale && rate_ok) {
            self.extra_dirty = false;
            self.extra_stale = false;
            if let Some(w) = self.pending_watch.take() {
                self.watch = w;
            }
            let f = self.filter(self.level);
            let a = self.change(f, now_unix)?;
            if !matches!(a, Action::None) {
                return Ok(a);
            }
        }
        if self.pending_watch.is_some() && rate_ok {
            self.watch = self.pending_watch.take().expect("checked");
            let f = self.filter(self.level);
            let a = self.change(f, now_unix)?;
            if !matches!(a, Action::None) {
                return Ok(a);
            }
        }
        if self.segs.is_due() && self.backfills_settled(now_unix) {
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
        let cfg = Config { opts: Opts { pool_accounts: true, ..Opts::default() }, ..Config::default() };
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

    /// Error lines of this test binary (it has no other logger).
    struct Lines(std::sync::Mutex<Vec<String>>);

    impl log::Log for Lines {
        fn enabled(&self, m: &log::Metadata) -> bool {
            m.level() <= log::Level::Error
        }
        fn log(&self, r: &log::Record) {
            if self.enabled(r.metadata()) {
                self.0.lock().unwrap().push(r.args().to_string());
            }
        }
        fn flush(&self) {}
    }

    static LINES: Lines = Lines(std::sync::Mutex::new(Vec::new()));

    #[test]
    fn a_conversion_error_is_counted_and_logs_only_its_category() {
        let _ = log::set_logger(&LINES);
        log::set_max_level(log::LevelFilter::Error);
        let root = tmp("rec-convert-error");
        let mut r = recorder(&root, 25_000_000_000, 2);
        let mut u = tx(1000, 4);
        let Some(UpdateOneof::Transaction(t)) = u.update_oneof.as_mut() else { panic!() };
        let msg = t.transaction.as_mut().unwrap().transaction.as_mut().unwrap().message.as_mut().unwrap();
        msg.account_keys[0].pop(); // fee payer key of 31 bytes
        r.on_update(u, 1).unwrap();
        assert_eq!(r.stats.convert_errors, 1);
        let lines = LINES.0.lock().unwrap();
        let mine: Vec<_> = lines.iter().filter(|l| l.starts_with("cannot convert")).collect();
        assert_eq!(mine, ["cannot convert a transaction update: address of the wrong length (1 so far)"]);
        drop(lines);
        std::fs::remove_dir_all(&root).unwrap();
    }

    /// Nothing optional configured and an empty watchlist: every level asks for the same streams.
    fn quiet_recorder(root: &Path) -> Recorder {
        let (segs, last) = Segments::open(root, 1000, 3600).unwrap();
        let meter = Meter::load(root, 1_440_000, 1.0, T0).unwrap();
        let watch = Watch::parse(Some(&json!({"pools": [], "wallets": [], "vaults": []})), None);
        let cfg = Config { opts: Opts { backfill: false, ..Opts::default() }, ..Config::default() };
        Recorder::new(cfg, plugin(), segs, meter, watch, last, 1)
    }

    #[test]
    fn a_downscale_without_effect_keeps_both_levels_in_step() {
        let root = tmp("rec-levels-down");
        let mut r = quiet_recorder(&root);
        r.meter.add("vaults", 30_000); // over 20x the sustainable rate: downscale at once
        assert!(matches!(r.on_tick(T0 + 5, None).unwrap(), Action::None)); // no level changes a stream
        assert_eq!((r.level, r.active.level), (DOWNSCALE.len(), DOWNSCALE.len()));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_restore_with_the_same_streams_resubscribes_nothing_and_resets_both_levels() {
        let root = tmp("rec-levels-restore");
        let mut r = quiet_recorder(&root);
        r.level = DOWNSCALE.len();
        r.active.level = DOWNSCALE.len();
        assert!(matches!(r.on_tick(T0 + 86_400 + 1, None).unwrap(), Action::None)); // a new UTC day
        assert_eq!((r.level, r.active.level), (0, 0));
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
        assert_eq!(DOWNSCALE[r.active.level - 1], "pool_accounts");
        // The cap: the last segment is written with the usage and the stop.
        for u in [meta(1002), confirmed(1002), confirmed(1003)] {
            r.on_update(u, 1).unwrap();
        }
        r.meter.add("tx_pools", 2_000_000);
        assert!(matches!(r.on_tick(T0 + 70, None).unwrap(), Action::Stop(_)));
        let last = segment_meta(&root, r.segs.seq - 1);
        let usage = last["usage"].as_array().unwrap();
        assert!(usage.iter().any(|u| u["stream"] == "budget" && u["note"].as_str().unwrap().contains("hard stop")));
        assert_eq!(r.segs.seq, 1); // the downscale changed the filter without closing a segment
        assert!(Meter::load(&root, 1_440_000, 1.0, T0 + 100).unwrap().stopped);
        std::fs::remove_dir_all(&root).unwrap();
    }

    fn footer(slot: u64) -> SubscribeUpdate {
        update("block_footers", UpdateOneof::BlockFooter(SubscribeUpdateBlockFooter {
            slot,
            bank_id: slot + 7,
            block_producer_time_nanos: 1_791_763_200_000_000_000 + slot,
            ..Default::default()
        }))
    }

    #[test]
    fn block_footers_become_rows_and_the_first_one_is_noted_once() {
        let root = tmp("rec-footers");
        let mut r = recorder(&root, 25_000_000_000, 2);
        for u in [meta(1000), footer(1000), confirmed(1000), meta(1001), footer(1001), confirmed(1001), meta(1002), confirmed(1002)] {
            r.on_update(u, 5).unwrap();
        }
        assert!(matches!(r.on_tick(T0 + 1, None).unwrap(), Action::None));
        let m = segment_meta(&root, 0);
        assert_eq!((m["rows"]["block_footers"].as_u64(), m["rows"]["anomalies"].as_u64()), (Some(2), Some(1)));
        let note: Value = serde_json::from_slice(&std::fs::read(root.join("alpenglow.json")).unwrap()).unwrap();
        assert_eq!((note["first_footer_slot"].as_u64(), note["bank_id"].as_u64()), (Some(1000), Some(1007)));
        // A restart on the same root: the switch was noted already, its footers are only rows.
        drop(r);
        let mut r = recorder(&root, 25_000_000_000, 2);
        for u in [meta(1003), footer(1003), confirmed(1003), meta(1004), confirmed(1004), meta(1005), confirmed(1005)] {
            r.on_update(u, 6).unwrap();
        }
        assert!(matches!(r.on_tick(T0 + 2, None).unwrap(), Action::None));
        let m = segment_meta(&root, 1);
        assert_eq!((m["rows"]["block_footers"].as_u64(), m["rows"]["anomalies"].as_u64()), (Some(1), None));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_footer_after_its_slot_closed_is_still_a_row_and_notes_the_switch() {
        // The footer is sent when the validator reports it, not with its block: here after slot 1001 confirmed.
        let root = tmp("rec-late-footer");
        let mut r = recorder(&root, 25_000_000_000, 2);
        for u in [meta(1000), confirmed(1000), meta(1001), confirmed(1001), footer(1000), meta(1002), confirmed(1002)] {
            r.on_update(u, 5).unwrap();
        }
        assert_eq!(r.slots.late, 0);
        assert!(matches!(r.on_tick(T0 + 1, None).unwrap(), Action::None));
        let m = segment_meta(&root, 0);
        assert_eq!((m["rows"]["block_footers"].as_u64(), m["rows"]["anomalies"].as_u64()), (Some(1), Some(1)));
        assert!(root.join("alpenglow.json").exists());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_slot_jump_closes_in_order_and_a_resume_inside_it_takes_the_next_slot() {
        // Alpenglow's switch: ~2 vote-only slots never confirm and the slot numbers jump (1002..1009).
        let root = tmp("rec-jump");
        let mut r = recorder(&root, 25_000_000_000, 100);
        for u in [tx(1000, 1), meta(1000), confirmed(1000), meta(1001), confirmed(1001), meta(1010), confirmed(1010)] {
            r.on_update(u, 1).unwrap();
        }
        assert_eq!(r.slots.last_closed, Some(1001));
        r.on_disconnect(T0, "test"); // 1010 was not closed yet: dropped, resent after the reconnect
        let (main, _) = r.requests(None);
        assert_eq!(main.from_slot, Some(1002)); // inside the jump: the stream resumes at the next slot that exists
        for u in [tx(1010, 3), meta(1010), confirmed(1010), meta(1011), confirmed(1011)] {
            r.on_update(u, 2).unwrap();
        }
        assert_eq!((r.slots.last_closed, r.slots.late, r.slots.unconfirmed), (Some(1010), 0, 0));
        r.close_segment(None).unwrap();
        let m = segment_meta(&root, 0);
        assert_eq!((m["first_slot"].as_u64(), m["last_slot"].as_u64(), m["rows"]["arrivals"].as_u64()), (Some(1000), Some(1010), Some(2)));
        std::fs::remove_dir_all(&root).unwrap();
    }

    fn full_disk(_: &Path) -> Result<()> {
        Err(std::io::Error::new(std::io::ErrorKind::StorageFull, "injected").into())
    }

    #[test]
    fn a_failed_segment_write_keeps_its_rows_and_the_restart_resumes_after_the_last_segment_on_disk() {
        let root = tmp("rec-write-fails");
        let mut r = recorder(&root, 25_000_000_000, 2);
        for u in [tx(1000, 4), meta(1000), confirmed(1000), meta(1001), confirmed(1001), meta(1002), confirmed(1002)] {
            r.on_update(u, 1).unwrap();
        }
        assert!(matches!(r.on_tick(T0 + 1, None).unwrap(), Action::None)); // segment 0: slots 1000..1001
        r.segs.sync = full_disk;
        for u in [meta(1003), confirmed(1003), meta(1004), confirmed(1004)] {
            r.on_update(u, 1).unwrap();
        }
        let e = r.on_tick(T0 + 2, None).unwrap_err(); // segment 1 (1002..1003) is due and cannot be written
        assert!(e.downcast_ref::<crate::segment::SegmentWriteError>().is_some());
        assert_eq!(r.slots.last_closed, Some(1003)); // in memory, past the last segment on disk
        assert!(r.segs.has_slots() && r.segs.seq == 1); // its rows are still there
        // The restart (main.rs: EXIT_SEGMENT, then systemd) resumes after the last complete segment on
        // disk, so the server replays 1002 on; the unfinished directory is removed.
        drop(r);
        let r = recorder(&root, 25_000_000_000, 2);
        let (main, _) = r.requests(None);
        assert_eq!(main.from_slot, Some(1002));
        assert_eq!(segment_meta(&root, 0)["last_slot"].as_u64(), Some(1001));
        assert!(!root.join("segments/000000001").exists());
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

    fn creation(pool: &str, base: &str, quote: &str, wsol: u64, vault: &str) -> Row {
        let cols = columns(&[("pool", Kind::Utf8), ("base_mint", Kind::Utf8), ("quote_mint", Kind::Utf8),
                             ("pool_base_amount", Kind::U64), ("pool_quote_amount", Kind::U64), ("ix_accounts", Kind::Utf8)]);
        let (b, q) = if base == WSOL { (wsol, 1) } else { (1, wsol) };
        let accounts = json!({"pool": pool, "pool_base_token_account": vault, "pool_quote_token_account": vault}).to_string();
        Row {
            table: Arc::from("pump_amm/CreatePoolEvent"),
            columns: cols,
            values: vec![Val::Str(pool.into()), Val::Str(base.into()), Val::Str(quote.into()), Val::U64(b), Val::U64(q), Val::Str(accounts)],
        }
    }

    fn resubscribe(a: Action) -> (Box<SubscribeRequest>, Box<SubscribeRequest>) {
        match a {
            Action::Resubscribe(m, v) => (m, v),
            other => panic!("expected a resubscribe, got {other:?}"),
        }
    }

    #[test]
    fn new_pools_become_probes_at_once_or_when_their_wsol_vault_is_deep_enough() {
        let root = tmp("rec-probes");
        let mut r = recorder(&root, 25_000_000_000, 1000);
        let deep = b58(&fake_key(11));
        let shallow = b58(&fake_key(12));
        let vault = b58(&fake_key(13));
        let mint = b58(&fake_key(14));
        // 200 SOL at creation: a probe at the next tick, without waiting for the rate limit.
        r.on_creation(&creation(&deep, WSOL, &mint, 200_000_000_000, &vault), T0);
        let (main, _) = resubscribe(r.on_tick(T0 + 1, None).unwrap());
        assert_eq!(main.transactions["tx_probes"].account_include, vec![deep.clone()]);
        // 85 SOL: its WSOL vault is followed; at 160 SOL the pool becomes a probe too.
        r.on_creation(&creation(&shallow, WSOL, &mint, 85_000_000_000, &vault), T0 + 2);
        let (_, vaults) = resubscribe(r.on_tick(T0 + 3, None).unwrap());
        assert_eq!(vaults.accounts["new_vaults"].account, vec![vault.clone()]);
        let update = |amount: u64| {
            SubscribeUpdate {
                filters: vec!["new_vaults".to_owned()],
                update_oneof: Some(UpdateOneof::Account(SubscribeUpdateAccount {
                    account: Some(SubscribeUpdateAccountInfo { pubkey: fake_key(13), data: amount.to_le_bytes().to_vec(), ..Default::default() }),
                    slot: 1000,
                    ..Default::default()
                })),
                created_at: None,
            }
        };
        r.on_update(update(100_000_000_000), (T0 + 4) * 1_000_000_000).unwrap();
        assert!(matches!(r.on_tick(T0 + 5, None).unwrap(), Action::None));
        r.on_update(update(160_000_000_000), (T0 + 6) * 1_000_000_000).unwrap();
        let (main, _) = resubscribe(r.on_tick(T0 + 7, None).unwrap());
        let mut probes = main.transactions["tx_probes"].account_include.clone();
        probes.sort();
        let mut want = vec![deep.clone(), shallow.clone()];
        want.sort();
        assert_eq!(probes, want);
        // A sol pool (graduation) below the depth is neither probed nor followed.
        r.on_creation(&creation(&b58(&fake_key(15)), &mint, WSOL, 85_000_000_000, &b58(&fake_key(16))), T0 + 8);
        assert!(matches!(r.on_tick(T0 + 9, None).unwrap(), Action::None));
        // Probes and followed vaults expire (applied with the rate limit).
        let (main, vaults) = resubscribe(r.on_tick(T0 + 3 * 3600 + 100, None).unwrap());
        assert!(!main.transactions.contains_key("tx_probes") && !vaults.accounts.contains_key("new_vaults"));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_probe_backfills_its_pool_from_creation_into_the_segment() {
        let root = tmp("rec-backfill");
        let mut r = recorder(&root, 25_000_000_000, 2);
        let pool = b58(&fake_key(21));
        let mint = b58(&fake_key(22));
        let mut row = creation(&pool, WSOL, &mint, 200_000_000_000, &b58(&fake_key(23)));
        row.columns = {
            let mut c = (*row.columns).clone();
            c.push(("slot".to_owned(), Kind::U64));
            Arc::new(c)
        };
        row.values.push(Val::U64(990));
        r.on_creation(&row, T0);
        let reqs = r.take_backfill_requests();
        assert_eq!(reqs.len(), 1);
        let (id, req) = &reqs[0];
        assert_eq!(req.from_slot, Some(990));
        assert_eq!(req.transactions["backfill"].account_include, vec![pool.clone()]);
        assert_eq!((req.transactions["backfill"].vote, req.transactions["backfill"].failed), (Some(false), Some(false)));
        // The replay: one transaction of the pool, then a confirmed slot past to_slot (990 + 150).
        r.on_backfill_update(id, tx(995, 2)).unwrap();
        r.on_backfill_update(id, update("backfill_slots", UpdateOneof::Slot(SubscribeUpdateSlot {
            slot: 1139, status: SlotStatus::SlotConfirmed as i32, ..Default::default() }))).unwrap();
        assert!(r.take_finished_backfills().is_empty()); // not there yet
        r.on_backfill_update(id, confirmed(1140)).unwrap();
        assert_eq!(r.take_finished_backfills(), vec![id.clone()]);
        // A second probe whose backfill keeps running holds the segment for backfill_wait_secs.
        let mut row2 = creation(&b58(&fake_key(24)), WSOL, &mint, 200_000_000_000, &b58(&fake_key(25)));
        row2.columns = row.columns.clone();
        row2.values.push(Val::U64(995));
        r.on_creation(&row2, T0 + 1);
        assert_eq!(r.take_backfill_requests().len(), 1);
        for u in [meta(1000), confirmed(1000), meta(1001), confirmed(1001), confirmed(1002)] {
            r.on_update(u, 1).unwrap();
        }
        r.on_tick(T0 + 2, None).unwrap(); // the probes' resubscribe
        assert!(matches!(r.on_tick(T0 + 3, None).unwrap(), Action::None));
        assert!(!root.join("segments/000000000/_segment.json").exists()); // waiting for the running backfill
        r.on_tick(T0 + 1 + 20, None).unwrap();
        let m = segment_meta(&root, 0);
        let b = m["backfills"].as_array().unwrap();
        assert_eq!(b.iter().map(|x| x["status"].as_str().unwrap()).collect::<Vec<_>>(), vec!["complete", "running"]);
        assert_eq!(b[0]["rows"], 1);
        assert!(root.join("segments/000000000/backfill/anomalies/part-00000.parquet").exists());
        // A failed backfill (from_slot older than the server keeps) is reported with no rows.
        let id2 = b[1]["id"].as_str().unwrap().to_owned();
        r.backfill_failed(&id2, "from_slot is not available");
        assert_eq!(r.take_finished_backfills(), vec![id2]);
        std::fs::remove_dir_all(&root).unwrap();
    }
}
