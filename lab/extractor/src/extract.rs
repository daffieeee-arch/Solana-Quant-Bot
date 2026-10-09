//! Jetstreamer plugin: finds pump.fun and PumpSwap events in every transaction and turns them
//! into Parquet rows.
//!
//! Anchor `emit_cpi!` events are self-CPI inner instructions whose data starts with
//! `EVENT_IX_TAG`. Only the program itself can sign such a call (through its event-authority
//! PDA), so an event instruction of a *successful* transaction is authentic.

use crate::idl::{DecodeStatus, Idl, Kind, Val, EVENT_IX_TAG};
use crate::sink::{Columns, Row, Sink};
use jetstreamer_firehose::firehose::{BlockData, FirehoseErrorContext, TransactionData};
use jetstreamer_plugin::{Plugin, PluginFuture};
use solana_message::VersionedMessage;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

type Key = [u8; 32];

fn key(b58: &str) -> Key {
    let v = bs58::decode(b58).into_vec().expect("valid base58 constant");
    v.try_into().expect("32-byte key")
}

const SYSTEM_PROGRAM: &str = "11111111111111111111111111111111";
const COMPUTE_BUDGET: &str = "ComputeBudget111111111111111111111111111111";
/// Jito tip accounts (tips to other relays are not counted).
const JITO_TIP_ACCOUNTS: [&str; 8] = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
];

/// Context columns that precede the IDL fields in every event table.
pub const EVENT_CONTEXT: [(&str, Kind); 20] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("signature", Kind::Utf8),
    ("outer_ix", Kind::U64),
    ("inner_ix", Kind::U64),
    ("stack_height", Kind::U64),
    ("event_seq", Kind::U64),
    ("outer_program", Kind::Utf8),
    ("parent_program", Kind::Utf8),
    ("parent_ix_disc", Kind::Utf8),
    ("fee_payer", Kind::Utf8),
    ("tx_fee", Kind::U64),
    ("cu_consumed", Kind::U64),
    ("cu_price_micro", Kind::U64),
    ("cu_limit", Kind::U64),
    ("priority_fee", Kind::U64),
    ("jito_tip", Kind::U64),
    ("tx_version", Kind::Utf8),
    ("decode_status", Kind::Utf8),
    ("payload_len", Kind::U64),
];

const FAILED_COLS: [(&str, Kind); 15] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("signature", Kind::Utf8),
    ("fee_payer", Kind::Utf8),
    ("tx_fee", Kind::U64),
    ("cu_consumed", Kind::U64),
    ("cu_price_micro", Kind::U64),
    ("cu_limit", Kind::U64),
    ("priority_fee", Kind::U64),
    // Tip transfers of a failed transaction are rolled back; this is what it tried to pay.
    ("jito_tip_attempted", Kind::U64),
    ("tx_version", Kind::Utf8),
    ("touches_pump", Kind::Bool),
    ("touches_amm", Kind::Bool),
    ("top_ix_discs", Kind::Utf8),
    ("error", Kind::Utf8),
];

const ANOMALY_COLS: [(&str, Kind); 9] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("signature", Kind::Utf8),
    ("outer_ix", Kind::U64),
    ("inner_ix", Kind::U64),
    ("program", Kind::Utf8),
    ("kind", Kind::Utf8),
    ("event", Kind::Utf8),
    ("payload_hex", Kind::Utf8),
];

const BLOCK_COLS: [(&str, Kind); 7] = [
    ("slot", Kind::U64),
    ("parent_slot", Kind::U64),
    ("block_time", Kind::I64),
    ("block_height", Kind::U64),
    ("executed_tx_count", Kind::U64),
    ("entry_count", Kind::U64),
    ("skipped", Kind::Bool),
];

fn cols(spec: &[(&str, Kind)]) -> Columns {
    Arc::new(spec.iter().map(|(n, k)| ((*n).to_owned(), *k)).collect())
}

struct ProgramIdl {
    short: &'static str,
    id: Key,
    idl: Idl,
    /// discriminator -> (event index, table name, columns)
    tables: HashMap<[u8; 8], (usize, Arc<str>, Columns)>,
}

#[derive(Default)]
pub struct Counters {
    pub txs_seen: AtomicU64,
    pub txs_touching: AtomicU64,
    pub txs_failed_touching: AtomicU64,
    pub events: AtomicU64,
    pub anomalies: AtomicU64,
    pub blocks: AtomicU64,
    pub skipped_markers: AtomicU64,
    pub sink_errors: AtomicU64,
    pub firehose_errors: AtomicU64,
    pub http_429: AtomicU64,
    pub missing_meta: AtomicU64,
}

/// Old Faithful's storage (Backblaze B2 behind Cloudflare) answers new requests with HTTP 429
/// when the archive owner's account is over its request limit. This is global and comes and
/// goes; streams that are already open keep flowing. So a thread that hits a 429 waits here
/// (1, 2, 4 ... 15 minutes, spread per thread) while the other threads keep streaming.
const BACKOFF_429_FIRST_SECS: u64 = 60;
const BACKOFF_429_MAX_SECS: u64 = 900;
/// Exit code used when one slot keeps failing: retrying later will not help.
pub const EXIT_STUCK: i32 = 76;
/// Base fee per signature in lamports.
const LAMPORTS_PER_SIGNATURE: u64 = 5_000;

pub struct LabPlugin {
    max_same_slot_errors: u64,
    /// Per firehose thread: the slot of the last error and how often it failed in a row.
    last_error: Mutex<HashMap<usize, (u64, u64)>>,
    /// Per firehose thread: the slot of the last HTTP 429 and how many came in a row.
    last_429: Mutex<HashMap<usize, (u64, u32)>>,
    programs: [ProgramIdl; 2],
    system: Key,
    compute_budget: Key,
    jito: [Key; 8],
    failed_cols: Columns,
    anomaly_cols: Columns,
    block_cols: Columns,
    failed_table: Arc<str>,
    anomaly_table: Arc<str>,
    block_table: Arc<str>,
    sink: Arc<Sink>,
    pub counters: Arc<Counters>,
    /// One flag per slot in the requested range, set when the firehose reports the slot as a
    /// block or as a skipped slot.
    coverage: Arc<Coverage>,
    pub fatal: Arc<AtomicBool>,
}

pub struct Coverage {
    pub start: u64,
    seen: Mutex<Vec<bool>>,
}

impl Coverage {
    pub fn new(start: u64, end: u64) -> Self {
        let len = usize::try_from(end - start).expect("range fits in memory");
        Self { start, seen: Mutex::new(vec![false; len]) }
    }
    fn mark(&self, slot: u64) {
        if slot < self.start {
            return;
        }
        if let Ok(i) = usize::try_from(slot - self.start) {
            if let Some(v) = self.seen.lock().expect("coverage lock").get_mut(i) {
                *v = true;
            }
        }
    }
    /// Missing slots as half-open ranges.
    pub fn missing_ranges(&self) -> Vec<(u64, u64)> {
        let seen = self.seen.lock().expect("coverage lock");
        let mut out = Vec::new();
        let mut run: Option<u64> = None;
        for (i, &s) in seen.iter().enumerate() {
            let slot = self.start + i as u64;
            match (s, run) {
                (false, None) => run = Some(slot),
                (true, Some(r)) => {
                    out.push((r, slot));
                    run = None;
                }
                _ => {}
            }
        }
        if let Some(r) = run {
            out.push((r, self.start + seen.len() as u64));
        }
        out
    }
}

impl ProgramIdl {
    fn new(short: &'static str, idl_text: &str) -> anyhow::Result<Self> {
        let idl = Idl::parse(idl_text)?;
        let id = key(&idl.address);
        let mut tables = HashMap::new();
        for (i, ev) in idl.events.iter().enumerate() {
            let mut c: Vec<(String, Kind)> = EVENT_CONTEXT.iter().map(|(n, k)| ((*n).to_owned(), *k)).collect();
            for f in &ev.fields {
                c.push((f.name.clone(), Idl::kind_of(&f.ty)));
            }
            let name: Arc<str> = Arc::from(format!("{short}/{}", ev.name));
            tables.insert(ev.discriminator, (i, name, Arc::new(c)));
        }
        Ok(Self { short, id, idl, tables })
    }
}

pub const PUMP_IDL: &str = include_str!("../idl/pump.json");
pub const PUMP_AMM_IDL: &str = include_str!("../idl/pump_amm.json");

impl LabPlugin {
    pub fn new(sink: Arc<Sink>, coverage: Arc<Coverage>) -> anyhow::Result<Self> {
        Ok(Self {
            max_same_slot_errors: std::env::var("LAB_MAX_SAME_SLOT_ERRORS").ok().and_then(|v| v.parse().ok()).unwrap_or(12),
            last_error: Mutex::new(HashMap::new()),
            last_429: Mutex::new(HashMap::new()),
            programs: [ProgramIdl::new("pump", PUMP_IDL)?, ProgramIdl::new("pump_amm", PUMP_AMM_IDL)?],
            system: key(SYSTEM_PROGRAM),
            compute_budget: key(COMPUTE_BUDGET),
            jito: JITO_TIP_ACCOUNTS.map(key),
            failed_cols: cols(&FAILED_COLS),
            anomaly_cols: cols(&ANOMALY_COLS),
            block_cols: cols(&BLOCK_COLS),
            failed_table: Arc::from("failed_txs"),
            anomaly_table: Arc::from("anomalies"),
            block_table: Arc::from("blocks"),
            sink,
            counters: Arc::new(Counters::default()),
            coverage,
            fatal: Arc::new(AtomicBool::new(false)),
        })
    }

    fn send(&self, rows: Vec<Row>) {
        if let Err(e) = self.sink.send(rows) {
            self.counters.sink_errors.fetch_add(1, Ordering::Relaxed);
            if !self.fatal.swap(true, Ordering::SeqCst) {
                log::error!("lab sink failed: {e:#}");
            }
        }
    }

    /// Extract all rows for one transaction. Public for tests.
    pub fn rows_for(&self, tx: &TransactionData) -> Vec<Row> {
        let msg = &tx.transaction.message;
        let meta = &tx.transaction_status_meta;
        let static_keys = msg.static_account_keys();
        let loaded_w = &meta.loaded_addresses.writable;
        let loaded_r = &meta.loaded_addresses.readonly;
        let key_at = |i: usize| -> Option<&[u8]> {
            if i < static_keys.len() {
                return Some(static_keys[i].as_ref());
            }
            let j = i - static_keys.len();
            if j < loaded_w.len() {
                return Some(loaded_w[j].as_ref());
            }
            loaded_r.get(j - loaded_w.len()).map(AsRef::as_ref)
        };
        let n_keys = static_keys.len() + loaded_w.len() + loaded_r.len();

        let mut touches = [false; 2];
        for i in 0..n_keys {
            if let Some(k) = key_at(i) {
                for (p, prog) in self.programs.iter().enumerate() {
                    if k == prog.id.as_slice() {
                        touches[p] = true;
                    }
                }
            }
        }
        if !touches[0] && !touches[1] {
            return Vec::new();
        }
        self.counters.txs_touching.fetch_add(1, Ordering::Relaxed);

        let b58 = |b: &[u8]| bs58::encode(b).into_string();
        let signature = tx.signature.to_string();
        let fee_payer = static_keys.first().map(|k| b58(k.as_ref())).unwrap_or_default();
        let outer = msg.instructions();
        let inner_groups = meta.inner_instructions.as_deref().unwrap_or(&[]);

        // Costs. Legacy/v0 set the compute budget with ComputeBudget instructions; v1 messages
        // carry it in their config (the runtime ignores ComputeBudget instructions there).
        let (tx_version, v1_config) = match msg {
            VersionedMessage::Legacy(_) => ("legacy", None),
            VersionedMessage::V0(_) => ("v0", None),
            VersionedMessage::V1(m) => ("v1", Some(m.config)),
        };
        let mut cu_price: Option<u64> = None;
        let mut cu_limit: Option<u64> = None;
        let mut jito_tip: u64 = 0;
        let mut scan_transfer = |program_idx: u8, accounts: &[u8], data: &[u8]| {
            if key_at(usize::from(program_idx)) != Some(self.system.as_slice()) || data.len() < 12 {
                return;
            }
            if u32::from_le_bytes(data[0..4].try_into().unwrap()) != 2 {
                return;
            }
            let lamports = u64::from_le_bytes(data[4..12].try_into().unwrap());
            if let Some(to) = accounts.get(1).and_then(|&a| key_at(usize::from(a))) {
                if self.jito.iter().any(|j| j.as_slice() == to) {
                    jito_tip = jito_tip.saturating_add(lamports);
                }
            }
        };
        for ix in outer {
            scan_transfer(ix.program_id_index, &ix.accounts, &ix.data);
        }
        for group in inner_groups {
            for ix in &group.instructions {
                scan_transfer(ix.instruction.program_id_index, &ix.instruction.accounts, &ix.instruction.data);
            }
        }
        if let Some(cfg) = v1_config {
            cu_limit = cfg.compute_unit_limit.map(u64::from);
            if let (Some(fee), Some(limit)) = (cfg.priority_fee, cfg.compute_unit_limit) {
                if limit > 0 {
                    cu_price = u64::try_from(u128::from(fee) * 1_000_000 / u128::from(limit)).ok();
                }
            }
        }
        for ix in outer.iter().filter(|_| v1_config.is_none()) {
            if key_at(usize::from(ix.program_id_index)) != Some(self.compute_budget.as_slice()) {
                continue;
            }
            match ix.data.first() {
                Some(3) if ix.data.len() >= 9 => cu_price = Some(u64::from_le_bytes(ix.data[1..9].try_into().unwrap())),
                Some(2) if ix.data.len() >= 5 => cu_limit = Some(u64::from(u32::from_le_bytes(ix.data[1..5].try_into().unwrap()))),
                _ => {}
            }
        }
        let opt = |v: Option<u64>| v.map_or(Val::Null, Val::U64);
        let cu_consumed = opt(meta.compute_units_consumed);
        // Charged fee minus the per-signature base fee = what was paid for priority.
        let sigs = u64::from(msg.header().num_required_signatures);
        let priority_fee = meta.fee.saturating_sub(sigs * LAMPORTS_PER_SIGNATURE);

        if meta.status.is_err() {
            self.counters.txs_failed_touching.fetch_add(1, Ordering::Relaxed);
            let mut discs = Vec::new();
            for ix in outer {
                let k = key_at(usize::from(ix.program_id_index));
                if self.programs.iter().any(|p| Some(p.id.as_slice()) == k) {
                    discs.push(hex::encode(&ix.data[..ix.data.len().min(8)]));
                }
            }
            let mut err = format!("{:?}", meta.status.as_ref().err());
            err.truncate(300);
            return vec![Row {
                table: self.failed_table.clone(),
                columns: self.failed_cols.clone(),
                values: vec![
                    Val::U64(tx.slot),
                    Val::U64(tx.transaction_slot_index as u64),
                    Val::Str(signature),
                    Val::Str(fee_payer),
                    Val::U64(meta.fee),
                    cu_consumed,
                    opt(cu_price),
                    opt(cu_limit),
                    Val::U64(priority_fee),
                    Val::U64(jito_tip),
                    Val::Str(tx_version.to_owned()),
                    Val::Bool(touches[0]),
                    Val::Bool(touches[1]),
                    Val::Str(discs.join(",")),
                    Val::Str(err),
                ],
            }];
        }

        let mut rows = Vec::new();
        if meta.inner_instructions.is_none() {
            // Old Faithful had no usable metadata for this transaction: events cannot be seen.
            self.counters.missing_meta.fetch_add(1, Ordering::Relaxed);
            self.counters.anomalies.fetch_add(1, Ordering::Relaxed);
            rows.push(self.anomaly(tx, &signature, 0, 0, "", "missing_inner_instructions", "", &[]));
        }
        let mut event_seq: u64 = 0;
        for group in inner_groups {
            let outer_idx = usize::from(group.index);
            let Some(outer_ix) = outer.get(outer_idx) else {
                self.counters.anomalies.fetch_add(1, Ordering::Relaxed);
                rows.push(self.anomaly(tx, &signature, outer_idx, 0, "", "unresolved_outer_index", "", &[]));
                continue;
            };
            let outer_program = key_at(usize::from(outer_ix.program_id_index)).map(b58).unwrap_or_default();
            for (j, inner) in group.instructions.iter().enumerate() {
                let ix = &inner.instruction;
                let data = &ix.data;
                if data.len() < 16 || data[..8] != EVENT_IX_TAG {
                    continue;
                }
                let Some(pk) = key_at(usize::from(ix.program_id_index)) else {
                    self.counters.anomalies.fetch_add(1, Ordering::Relaxed);
                    rows.push(self.anomaly(tx, &signature, outer_idx, j, "", "unresolved_program_index", "", data));
                    continue;
                };
                let Some(prog) = self.programs.iter().find(|p| p.id.as_slice() == pk) else { continue };
                let disc: [u8; 8] = data[8..16].try_into().unwrap();
                let body = &data[16..];

                // Parent = nearest earlier instruction one level up; the outer instruction when
                // the event is emitted directly below it.
                let (parent_program, parent_disc) = {
                    let earlier = &group.instructions[..j];
                    let parent = match inner.stack_height {
                        Some(h) => earlier
                            .iter()
                            .rev()
                            .find(|p| p.stack_height == Some(h.saturating_sub(1)))
                            .map(|p| (p.instruction.program_id_index, &p.instruction.data)),
                        // Without stack heights: the nearest earlier non-event instruction of the
                        // same program, else the outer instruction if it is that program.
                        None => earlier
                            .iter()
                            .rev()
                            .find(|p| {
                                key_at(usize::from(p.instruction.program_id_index)) == Some(pk)
                                    && !p.instruction.data.starts_with(&EVENT_IX_TAG)
                            })
                            .map(|p| (p.instruction.program_id_index, &p.instruction.data)),
                    };
                    let parent = parent.or_else(|| {
                        (inner.stack_height == Some(2) || key_at(usize::from(outer_ix.program_id_index)) == Some(pk))
                            .then_some((outer_ix.program_id_index, &outer_ix.data))
                    });
                    match parent {
                        Some((pidx, pdata)) => (
                            key_at(usize::from(pidx)).map_or(Val::Null, |k| Val::Str(b58(k))),
                            Val::Str(hex::encode(&pdata[..pdata.len().min(8)])),
                        ),
                        None => (Val::Null, Val::Null),
                    }
                };

                let Some((ev_idx, table, columns)) = prog.tables.get(&disc) else {
                    self.counters.anomalies.fetch_add(1, Ordering::Relaxed);
                    rows.push(self.anomaly(tx, &signature, outer_idx, j, prog.short, "unknown_discriminator", &hex::encode(disc), data));
                    continue;
                };
                let ev = &prog.idl.events[*ev_idx];
                let decoded = prog.idl.decode_event(ev, body);
                if matches!(decoded.status, DecodeStatus::Extra | DecodeStatus::Error) {
                    self.counters.anomalies.fetch_add(1, Ordering::Relaxed);
                    rows.push(self.anomaly(tx, &signature, outer_idx, j, prog.short, decoded.status.as_str(), &ev.name, data));
                }
                let mut values = Vec::with_capacity(columns.len());
                values.extend([
                    Val::U64(tx.slot),
                    Val::U64(tx.transaction_slot_index as u64),
                    Val::Str(signature.clone()),
                    Val::U64(outer_idx as u64),
                    Val::U64(j as u64),
                    inner.stack_height.map_or(Val::Null, |h| Val::U64(u64::from(h))),
                    Val::U64(event_seq),
                    Val::Str(outer_program.clone()),
                    parent_program,
                    parent_disc,
                    Val::Str(fee_payer.clone()),
                    Val::U64(meta.fee),
                    cu_consumed.clone(),
                    opt(cu_price),
                    opt(cu_limit),
                    Val::U64(priority_fee),
                    Val::U64(jito_tip),
                    Val::Str(tx_version.to_owned()),
                    Val::Str(decoded.status.as_str().to_owned()),
                    Val::U64(body.len() as u64),
                ]);
                values.extend(decoded.values);
                event_seq += 1;
                self.counters.events.fetch_add(1, Ordering::Relaxed);
                rows.push(Row { table: table.clone(), columns: columns.clone(), values });
            }
        }
        rows
    }

    #[allow(clippy::too_many_arguments)]
    fn anomaly(&self, tx: &TransactionData, sig: &str, outer: usize, inner: usize, program: &str, kind: &str, event: &str, data: &[u8]) -> Row {
        Row {
            table: self.anomaly_table.clone(),
            columns: self.anomaly_cols.clone(),
            values: vec![
                Val::U64(tx.slot),
                Val::U64(tx.transaction_slot_index as u64),
                Val::Str(sig.to_owned()),
                Val::U64(outer as u64),
                Val::U64(inner as u64),
                Val::Str(program.to_owned()),
                Val::Str(kind.to_owned()),
                Val::Str(event.to_owned()),
                Val::Str(hex::encode(data)),
            ],
        }
    }

    fn block_row(&self, block: &BlockData) -> Row {
        let values = match block {
            BlockData::Block { parent_slot, slot, block_time, block_height, executed_transaction_count, entry_count, .. } => vec![
                Val::U64(*slot),
                Val::U64(*parent_slot),
                block_time.map_or(Val::Null, Val::I64),
                block_height.map_or(Val::Null, Val::U64),
                Val::U64(*executed_transaction_count),
                Val::U64(*entry_count),
                Val::Bool(false),
            ],
            BlockData::PossibleLeaderSkipped { slot } => {
                vec![Val::U64(*slot), Val::Null, Val::Null, Val::Null, Val::Null, Val::Null, Val::Bool(true)]
            }
        };
        Row { table: self.block_table.clone(), columns: self.block_cols.clone(), values }
    }
}

impl Plugin for LabPlugin {
    fn name(&self) -> &'static str {
        "lab-pump-events"
    }

    fn on_transaction<'a>(
        &'a self,
        _thread_id: usize,
        _db: Option<Arc<clickhouse::Client>>,
        transaction: &'a TransactionData,
    ) -> PluginFuture<'a> {
        Box::pin(async move {
            self.counters.txs_seen.fetch_add(1, Ordering::Relaxed);
            if transaction.is_vote {
                return Ok(());
            }
            let rows = self.rows_for(transaction);
            self.send(rows);
            Ok(())
        })
    }

    fn on_error<'a>(
        &'a self,
        _thread_id: usize,
        _db: Option<Arc<clickhouse::Client>>,
        error: &'a FirehoseErrorContext,
    ) -> PluginFuture<'a> {
        Box::pin(async move {
            self.counters.firehose_errors.fetch_add(1, Ordering::Relaxed);
            let msg = &error.error_message;
            if msg.contains("429") {
                self.counters.http_429.fetch_add(1, Ordering::Relaxed);
                let n = {
                    let mut last = self.last_429.lock().expect("429 lock");
                    let entry = last.entry(error.thread_id).or_insert((error.slot, 0));
                    // Progress since the last 429 resets the backoff.
                    if entry.0 != error.slot {
                        *entry = (error.slot, 0);
                    }
                    entry.1 = entry.1.saturating_add(1);
                    entry.1
                };
                let base = (BACKOFF_429_FIRST_SECS << (n - 1).min(4)).min(BACKOFF_429_MAX_SECS);
                let wait = base + (error.thread_id as u64 * 17) % 60;
                log::warn!("thread {} got HTTP 429 at slot {} ({n} in a row); waiting {wait}s", error.thread_id, error.slot);
                tokio::time::sleep(std::time::Duration::from_secs(wait)).await;
                return Ok(());
            }
            let same_slot = {
                let mut last = self.last_error.lock().expect("error lock");
                let entry = last.entry(error.thread_id).or_insert((error.slot, 0));
                if entry.0 == error.slot {
                    entry.1 += 1;
                } else {
                    *entry = (error.slot, 1);
                }
                entry.1
            };
            if same_slot >= self.max_same_slot_errors {
                log::error!("slot {} failed {same_slot} times in a row (last: {msg}); giving up on this chunk", error.slot);
                std::process::exit(EXIT_STUCK);
            }
            Ok(())
        })
    }

    fn on_block<'a>(
        &'a self,
        _thread_id: usize,
        _db: Option<Arc<clickhouse::Client>>,
        block: &'a BlockData,
    ) -> PluginFuture<'a> {
        Box::pin(async move {
            if block.was_skipped() {
                self.counters.skipped_markers.fetch_add(1, Ordering::Relaxed);
            } else {
                self.counters.blocks.fetch_add(1, Ordering::Relaxed);
            }
            self.coverage.mark(block.slot());
            self.send(vec![self.block_row(block)]);
            Ok(())
        })
    }
}
