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
const METAPLEX_METADATA: &str = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s";
const TOKEN_2022: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
/// spl-token-metadata-interface Initialize: sha256("spl_token_metadata_interface:initialize_account")[..8].
const TOKEN_METADATA_INIT: [u8; 8] = [210, 225, 30, 162, 88, 184, 77, 141];
/// Longest name, symbol or uri accepted from instruction data (Metaplex allows 32 / 10 / 200).
const META_STR_MAX: usize = 1024;
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
pub const EVENT_CONTEXT: [(&str, Kind); 30] = [
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
    // The instruction that emitted the event (e.g. pump buy): IDL name, decoded arguments as
    // JSON (slippage limits such as max_sol_cost / min_tokens_out) and raw data (hex, capped).
    ("parent_ix_name", Kind::Utf8),
    ("parent_ix_args", Kind::Utf8),
    // Raw data only when IDL decoding was not exact (`_decode` other than ok/prefix).
    ("parent_ix_data", Kind::Utf8),
    // Required signers, comma separated (fee payer first); null when the fee payer is the only one.
    ("signers", Kind::Utf8),
    // The parent instruction's accounts by IDL name (JSON {name: key}; "_extra" holds remaining
    // accounts beyond the IDL list). Null when LAB_IX_ACCOUNTS_JSON=0 or the parent is unknown.
    ("ix_accounts", Kind::Utf8),
    // The same for five accounts as their own columns, null where the instruction variant has no
    // account of that name. Prefixed because events have their own pool/user/mint fields.
    ("ix_pool", Kind::Utf8),
    ("ix_user", Kind::Utf8),
    ("ix_global_config", Kind::Utf8),
    ("ix_base_mint", Kind::Utf8),
    ("ix_quote_mint", Kind::Utf8),
];

// No signature column: (slot, tx_index) identifies the transaction, and the 88-character
// signatures were ~75% of this table's size.
const FAILED_COLS: [(&str, Kind); 20] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
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
    ("signers", Kind::Utf8),
    // First pump/PumpSwap instruction in execution order (top level or inner), decoded with the
    // IDL: which trade was attempted and with which slippage limits.
    ("ix_program", Kind::Utf8),
    ("ix_name", Kind::Utf8),
    ("ix_args", Kind::Utf8),
    ("ix_data", Kind::Utf8),
    ("ix_stack_height", Kind::U64),
];
/// Raw instruction data kept per row, in bytes (pump/PumpSwap trade instructions are < 64 bytes).
const IX_DATA_CAP: usize = 256;

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

/// Token metadata created in a transaction the plugin reads (it touches pump.fun or PumpSwap): a
/// Metaplex Token Metadata create instruction or Token-2022's metadata Initialize, top level or
/// inner. inner_ix is 0 for the top-level instruction itself and j + 1 for its j-th inner one.
const TOKEN_META_COLS: [(&str, Kind); 11] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("signature", Kind::Utf8),
    ("outer_ix", Kind::U64),
    ("inner_ix", Kind::U64),
    ("source", Kind::Utf8),
    ("mint", Kind::Utf8),
    ("name", Kind::Utf8),
    ("symbol", Kind::Utf8),
    ("uri", Kind::Utf8),
    ("update_authority", Kind::Utf8),
];

/// (source, account position of the mint, of the update authority, name, symbol, uri) of a token
/// metadata create instruction, or None. Borsh strings: u32 length, then UTF-8 bytes.
pub fn decode_token_metadata(metaplex: bool, token2022: bool, data: &[u8]) -> Option<(&'static str, usize, usize, String, String, String)> {
    fn string(d: &[u8], pos: &mut usize) -> Option<String> {
        let len = u32::from_le_bytes(d.get(*pos..*pos + 4)?.try_into().ok()?) as usize;
        if len > META_STR_MAX {
            return None;
        }
        let s = d.get(*pos + 4..*pos + 4 + len)?;
        *pos += 4 + len;
        Some(String::from_utf8_lossy(s).trim_end_matches('\0').to_owned())
    }
    let three = |start: usize| -> Option<(String, String, String)> {
        let mut p = start;
        Some((string(data, &mut p)?, string(data, &mut p)?, string(data, &mut p)?))
    };
    if metaplex {
        // MetadataInstruction: 0 CreateMetadataAccount, 16 V2, 33 V3 (accounts: metadata, mint,
        // mint authority, payer, update authority); 42 Create with CreateArgs::V1 (metadata,
        // master edition, mint, authority, payer, update authority). All start with name, symbol, uri.
        let (source, mint, ua, start) = match *data.first()? {
            0 => ("metaplex_v1", 1, 4, 1),
            16 => ("metaplex_v2", 1, 4, 1),
            33 => ("metaplex_v3", 1, 4, 1),
            42 if data.get(1) == Some(&0) => ("metaplex_create", 2, 5, 2),
            _ => return None,
        };
        let (n, s, u) = three(start)?;
        return Some((source, mint, ua, n, s, u));
    }
    if token2022 && data.len() > 8 && data[..8] == TOKEN_METADATA_INIT {
        // Accounts: metadata, update authority, mint, mint authority.
        let (n, s, u) = three(8)?;
        return Some(("token2022", 2, 1, n, s, u));
    }
    None
}

const BLOCK_COLS: [(&str, Kind); 7] = [
    ("slot", Kind::U64),
    ("parent_slot", Kind::U64),
    ("block_time", Kind::I64),
    ("block_height", Kind::U64),
    ("executed_tx_count", Kind::U64),
    ("entry_count", Kind::U64),
    ("skipped", Kind::Bool),
];

/// Per transaction the plugin reads (every successful one with a pump/PumpSwap event, every failed
/// one touching them): who paid, what it cost, which programs ran and what the fee payer ended up
/// with. MEV phase 0 (measurement only): arbitrage shows as several DEXes in one transaction with a
/// positive net for the payer, a backrun as a trade right after a large one. No signature column:
/// (slot, tx_index) joins the event tables and failed_txs (the signatures would dominate the size).
const TX_CTX_COLS: [(&str, Kind); 15] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("failed", Kind::Bool),
    ("fee_payer", Kind::Utf8),
    ("tx_fee", Kind::U64),
    ("cu_consumed", Kind::U64),
    ("cu_limit", Kind::U64),
    ("cu_price_micro", Kind::U64),
    ("priority_fee", Kind::U64),
    // Transfers to the 8 Jito tip accounts from any account of the transaction (not only the fee
    // payer); of a failed transaction what it tried to pay.
    ("jito_tip", Kind::U64),
    // Invoked programs in execution order, "depth:label" separated by ';' (top level = 1, inner =
    // stack height), a run of the same call as "depth:label*n"; known programs by name (see
    // PROGRAM_LABELS), others by their id. The pump/PumpSwap event self-CPIs are left out.
    ("programs", Kind::Utf8),
    // The DEXes and routers among them, sorted and comma separated; null when none. n_dex counts
    // distinct DEX programs: two pools of one DEX (e.g. PumpSwap normal and reversed) count once;
    // pools per transaction come from the event tables (pool / mint).
    ("venues", Kind::Utf8),
    ("n_dex", Kind::U64),
    // Fee payer: lamports after minus before (the fee included), and the net change of its token
    // accounts per mint (raw amounts, WSOL included) as "mint:delta" separated by ';', non-zero only.
    ("signer_sol_delta", Kind::I64),
    ("signer_token_deltas", Kind::Utf8),
];

/// What a known program is in `tx_ctx`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Class {
    Dex,
    Router,
    Infra,
}

/// Programs named in tx_ctx (ids from the projects' documentation). Anything else is kept by id.
pub const PROGRAM_LABELS: [(&str, &str, Class); 19] = [
    ("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "pump", Class::Dex),
    ("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "pump_amm", Class::Dex),
    ("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "raydium_amm_v4", Class::Dex),
    ("CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C", "raydium_cpmm", Class::Dex),
    ("CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", "raydium_clmm", Class::Dex),
    ("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj", "raydium_launchlab", Class::Dex),
    ("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "meteora_dlmm", Class::Dex),
    ("Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB", "meteora_damm_v1", Class::Dex),
    ("cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG", "meteora_damm_v2", Class::Dex),
    ("dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN", "meteora_dbc", Class::Dex),
    ("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", "orca_whirlpool", Class::Dex),
    ("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "jupiter_v6", Class::Router),
    ("11111111111111111111111111111111", "system", Class::Infra),
    ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "token", Class::Infra),
    ("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb", "token2022", Class::Infra),
    ("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "ata", Class::Infra),
    ("ComputeBudget111111111111111111111111111111", "compute_budget", Class::Infra),
    ("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr", "memo", Class::Infra),
    ("Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo", "memo_v1", Class::Infra),
];

/// (programs, venues, n_dex) of tx_ctx from the invoked programs in execution order as
/// (depth, label, class); see TX_CTX_COLS.
pub fn summarize_calls(calls: &[(u64, String, Option<Class>)]) -> (String, String, u64) {
    let mut parts: Vec<String> = Vec::new();
    let mut i = 0;
    while i < calls.len() {
        let mut j = i + 1;
        while j < calls.len() && calls[j].0 == calls[i].0 && calls[j].1 == calls[i].1 {
            j += 1;
        }
        let (depth, label) = (calls[i].0, &calls[i].1);
        parts.push(if j - i > 1 { format!("{depth}:{label}*{}", j - i) } else { format!("{depth}:{label}") });
        i = j;
    }
    let mut venues: Vec<&str> =
        calls.iter().filter(|c| matches!(c.2, Some(Class::Dex | Class::Router))).map(|c| c.1.as_str()).collect();
    venues.sort_unstable();
    venues.dedup();
    let n_dex = calls
        .iter()
        .filter(|c| c.2 == Some(Class::Dex))
        .map(|c| c.1.as_str())
        .collect::<std::collections::BTreeSet<_>>()
        .len() as u64;
    (parts.join(";"), venues.join(","), n_dex)
}

/// Net change per mint of the token accounts owned by `owner`, from the pre and post token balances
/// as (mint, owner, raw amount): a closed account counts as 0 after, a new one as 0 before. Non-zero
/// mints only, sorted, as "mint:delta;..."; None if there are none.
pub fn owner_token_deltas(owner: &str, pre: &[(&str, &str, &str)], post: &[(&str, &str, &str)]) -> Option<String> {
    let mut by_mint: std::collections::BTreeMap<&str, i128> = std::collections::BTreeMap::new();
    for (sign, list) in [(-1i128, pre), (1i128, post)] {
        for &(mint, o, amount) in list {
            if o == owner {
                *by_mint.entry(mint).or_insert(0) += sign * amount.parse::<i128>().unwrap_or(0);
            }
        }
    }
    let parts: Vec<String> = by_mint.into_iter().filter(|(_, d)| *d != 0).map(|(m, d)| format!("{m}:{d}")).collect();
    (!parts.is_empty()).then(|| parts.join(";"))
}

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
/// (1, 2, 4, 5 minutes, spread per thread) while the other threads keep streaming. The cap stays
/// short because a thread waiting here cannot answer Jetstreamer's work-steal requests.
const BACKOFF_429_FIRST_SECS: u64 = 60;
const BACKOFF_429_MAX_SECS: u64 = 300;

/// True only for an HTTP 429 status in the error text (slot numbers can contain "429").
fn is_http_429(msg: &str) -> bool {
    msg.contains("429 Too Many Requests") || msg.contains("status: 429") || msg.contains("too_many_requests")
}
/// Exit code used when one slot keeps failing: retrying later will not help.
pub const EXIT_STUCK: i32 = 76;
/// Base fee per signature in lamports.
const LAMPORTS_PER_SIGNATURE: u64 = 5_000;

pub struct LabPlugin {
    max_same_slot_errors: u64,
    /// Write the ix_accounts JSON column (LAB_IX_ACCOUNTS_JSON=0 keeps only the fixed columns).
    ix_accounts_json: bool,
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
    token_meta_cols: Columns,
    token_meta_table: Arc<str>,
    tx_ctx_cols: Columns,
    tx_ctx_table: Arc<str>,
    labels: HashMap<Key, (&'static str, Class)>,
    metaplex: Key,
    token2022: Key,
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
            ix_accounts_json: std::env::var("LAB_IX_ACCOUNTS_JSON").map_or(true, |v| v != "0"),
            last_error: Mutex::new(HashMap::new()),
            last_429: Mutex::new(HashMap::new()),
            programs: [ProgramIdl::new("pump", PUMP_IDL)?, ProgramIdl::new("pump_amm", PUMP_AMM_IDL)?],
            system: key(SYSTEM_PROGRAM),
            compute_budget: key(COMPUTE_BUDGET),
            jito: JITO_TIP_ACCOUNTS.map(key),
            failed_cols: cols(&FAILED_COLS),
            anomaly_cols: cols(&ANOMALY_COLS),
            block_cols: cols(&BLOCK_COLS),
            token_meta_cols: cols(&TOKEN_META_COLS),
            token_meta_table: Arc::from("token_meta"),
            tx_ctx_cols: cols(&TX_CTX_COLS),
            tx_ctx_table: Arc::from("tx_ctx"),
            labels: PROGRAM_LABELS.iter().map(|(id, label, class)| (key(id), (*label, *class))).collect(),
            metaplex: key(METAPLEX_METADATA),
            token2022: key(TOKEN_2022),
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
        let signers = if sigs > 1 {
            Val::Str(
                static_keys
                    .iter()
                    .take(usize::try_from(sigs).unwrap_or(usize::MAX))
                    .map(|k| b58(k.as_ref()))
                    .collect::<Vec<_>>()
                    .join(","),
            )
        } else {
            Val::Null
        };
        // Raw instruction bytes are kept only when the IDL decode was not exact.
        let ix_raw = |d: &[u8], args: &Val| match args {
            Val::Str(a) if a.contains("\"_decode\":\"ok\"") || a.contains("\"_decode\":\"prefix\"") => Val::Null,
            _ => Val::Str(hex::encode(&d[..d.len().min(IX_DATA_CAP)])),
        };
        // Decode an instruction with the IDL of the program it calls, if that is pump/PumpSwap.
        let decode_ix = |program: Option<&[u8]>, data: &[u8]| -> (Val, Val) {
            match program.and_then(|k| self.programs.iter().find(|p| p.id.as_slice() == k)) {
                Some(p) => match p.idl.decode_instruction(data) {
                    Some((name, args)) => (Val::Str(name.to_owned()), Val::Str(args)),
                    None => (Val::Null, Val::Null),
                },
                None => (Val::Null, Val::Null),
            }
        };

        // The parent instruction's accounts by IDL name: [ix_accounts, ix_pool, ix_user,
        // ix_global_config, ix_base_mint, ix_quote_mint].
        let parent_accounts = |program: Option<&[u8]>, data: &[u8], accounts: &[u8]| -> [Val; 6] {
            let named = program.and_then(|k| self.programs.iter().find(|p| p.id.as_slice() == k)).and_then(|p| {
                let keys: Vec<Option<String>> = accounts.iter().map(|&a| key_at(usize::from(a)).map(b58)).collect();
                p.idl.named_accounts(data, &keys)
            });
            match named {
                Some(n) => {
                    let [a, b, c, d, e] = n.fixed.map(|v| v.map_or(Val::Null, Val::Str));
                    [if self.ix_accounts_json { Val::Str(n.json) } else { Val::Null }, a, b, c, d, e]
                }
                None => std::array::from_fn(|_| Val::Null),
            }
        };

        // tx_ctx: invoked programs in execution order (top level, then its inner instructions).
        let ctx = {
            let label = |k: Option<&[u8]>| -> (String, Option<Class>) {
                match k.and_then(|k| <[u8; 32]>::try_from(k).ok()) {
                    Some(kk) => match self.labels.get(&kk) {
                        Some((name, class)) => ((*name).to_owned(), Some(*class)),
                        None => (b58(kk.as_slice()), None),
                    },
                    // A program index outside the account keys: impossible in a valid transaction
                    // (the runtime rejects it); kept visible as "?" rather than dropped.
                    None => ("?".to_owned(), None),
                }
            };
            let mut calls: Vec<(u64, String, Option<Class>)> = Vec::new();
            for (i, ix) in outer.iter().enumerate() {
                let (l, c) = label(key_at(usize::from(ix.program_id_index)));
                calls.push((1, l, c));
                for group in inner_groups.iter().filter(|g| usize::from(g.index) == i) {
                    for inner in &group.instructions {
                        let pk = key_at(usize::from(inner.instruction.program_id_index));
                        if inner.instruction.data.starts_with(&EVENT_IX_TAG) && self.programs.iter().any(|p| Some(p.id.as_slice()) == pk) {
                            continue;
                        }
                        let (l, c) = label(pk);
                        calls.push((inner.stack_height.map_or(2, u64::from), l, c));
                    }
                }
            }
            let (programs, venues, n_dex) = summarize_calls(&calls);
            let sol_delta = match (meta.pre_balances.first(), meta.post_balances.first()) {
                (Some(&a), Some(&b)) => i64::try_from(i128::from(b) - i128::from(a)).map_or(Val::Null, Val::I64),
                _ => Val::Null,
            };
            let pre: Vec<(&str, &str, &str)> = meta
                .pre_token_balances
                .as_deref()
                .unwrap_or(&[])
                .iter()
                .map(|b| (b.mint.as_str(), b.owner.as_str(), b.ui_token_amount.amount.as_str()))
                .collect();
            let post: Vec<(&str, &str, &str)> = meta
                .post_token_balances
                .as_deref()
                .unwrap_or(&[])
                .iter()
                .map(|b| (b.mint.as_str(), b.owner.as_str(), b.ui_token_amount.amount.as_str()))
                .collect();
            let deltas = owner_token_deltas(&fee_payer, &pre, &post);
            Row {
                table: self.tx_ctx_table.clone(),
                columns: self.tx_ctx_cols.clone(),
                values: vec![
                    Val::U64(tx.slot),
                    Val::U64(tx.transaction_slot_index as u64),
                    Val::Bool(meta.status.is_err()),
                    Val::Str(fee_payer.clone()),
                    Val::U64(meta.fee),
                    cu_consumed.clone(),
                    opt(cu_limit),
                    opt(cu_price),
                    Val::U64(priority_fee),
                    Val::U64(jito_tip),
                    Val::Str(programs),
                    if venues.is_empty() { Val::Null } else { Val::Str(venues) },
                    Val::U64(n_dex),
                    sol_delta,
                    deltas.map_or(Val::Null, Val::Str),
                ],
            }
        };

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
            let is_ours = |idx: u8, data: &[u8]| {
                let k = key_at(usize::from(idx));
                self.programs.iter().any(|p| Some(p.id.as_slice()) == k) && !data.starts_with(&EVENT_IX_TAG)
            };
            // Execution order: each top-level instruction, then its inner instructions.
            let mut first: Option<(u8, &[u8], u64)> = None;
            'search: for (i, ix) in outer.iter().enumerate() {
                if is_ours(ix.program_id_index, &ix.data) {
                    first = Some((ix.program_id_index, &ix.data, 1));
                    break;
                }
                for group in inner_groups.iter().filter(|g| usize::from(g.index) == i) {
                    for inner in &group.instructions {
                        if is_ours(inner.instruction.program_id_index, &inner.instruction.data) {
                            first = Some((
                                inner.instruction.program_id_index,
                                &inner.instruction.data,
                                inner.stack_height.map_or(0, u64::from),
                            ));
                            break 'search;
                        }
                    }
                }
            }
            let (ix_program, ix_name, ix_args, ix_data, ix_height) = match first {
                Some((idx, data, h)) => {
                    let k = key_at(usize::from(idx));
                    let (name, args) = decode_ix(k, data);
                    let raw = ix_raw(data, &args);
                    (k.map_or(Val::Null, |k| Val::Str(b58(k))), name, args, raw, if h == 0 { Val::Null } else { Val::U64(h) })
                }
                None => (Val::Null, Val::Null, Val::Null, Val::Null, Val::Null),
            };
            return vec![ctx, Row {
                table: self.failed_table.clone(),
                columns: self.failed_cols.clone(),
                values: vec![
                    Val::U64(tx.slot),
                    Val::U64(tx.transaction_slot_index as u64),
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
                    signers,
                    ix_program,
                    ix_name,
                    ix_args,
                    ix_data,
                    ix_height,
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
                let (parent_program, parent_disc, parent_name, parent_args, parent_data, parent_accts) = {
                    let earlier = &group.instructions[..j];
                    let parent = match inner.stack_height {
                        Some(h) => earlier
                            .iter()
                            .rev()
                            .find(|p| p.stack_height == Some(h.saturating_sub(1)))
                            .map(|p| (p.instruction.program_id_index, &p.instruction.data, &p.instruction.accounts)),
                        // Without stack heights: the nearest earlier non-event instruction of the
                        // same program, else the outer instruction if it is that program.
                        None => earlier
                            .iter()
                            .rev()
                            .find(|p| {
                                key_at(usize::from(p.instruction.program_id_index)) == Some(pk)
                                    && !p.instruction.data.starts_with(&EVENT_IX_TAG)
                            })
                            .map(|p| (p.instruction.program_id_index, &p.instruction.data, &p.instruction.accounts)),
                    };
                    let parent = parent.or_else(|| {
                        (inner.stack_height == Some(2) || key_at(usize::from(outer_ix.program_id_index)) == Some(pk))
                            .then_some((outer_ix.program_id_index, &outer_ix.data, &outer_ix.accounts))
                    });
                    match parent {
                        Some((pidx, pdata, paccounts)) => {
                            let pkey = key_at(usize::from(pidx));
                            let (name, args) = decode_ix(pkey, pdata);
                            let raw = ix_raw(pdata, &args);
                            (
                                pkey.map_or(Val::Null, |k| Val::Str(b58(k))),
                                Val::Str(hex::encode(&pdata[..pdata.len().min(8)])),
                                name,
                                args,
                                raw,
                                parent_accounts(pkey, pdata, paccounts),
                            )
                        }
                        None => (Val::Null, Val::Null, Val::Null, Val::Null, Val::Null, std::array::from_fn(|_| Val::Null)),
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
                    parent_name,
                    parent_args,
                    parent_data,
                    signers.clone(),
                ]);
                values.extend(parent_accts);
                values.extend(decoded.values);
                event_seq += 1;
                self.counters.events.fetch_add(1, Ordering::Relaxed);
                rows.push(Row { table: table.clone(), columns: columns.clone(), values });
            }
        }
        if event_seq > 0 {
            rows.push(ctx);
        }
        // Token metadata created in this transaction, top level and inner, in execution order.
        let inner_of = |i: usize| inner_groups.iter().find(|g| usize::from(g.index) == i).map(|g| g.instructions.as_slice()).unwrap_or(&[]);
        for (i, ix) in outer.iter().enumerate() {
            let calls = std::iter::once((0usize, ix.program_id_index, ix.accounts.as_slice(), ix.data.as_slice())).chain(
                inner_of(i).iter().enumerate().map(|(j, x)| (j + 1, x.instruction.program_id_index, x.instruction.accounts.as_slice(), x.instruction.data.as_slice())),
            );
            for (inner_i, program_idx, accounts, data) in calls {
                let program = key_at(usize::from(program_idx));
                let (mp, t22) = (program == Some(self.metaplex.as_slice()), program == Some(self.token2022.as_slice()));
                if !mp && !t22 {
                    continue;
                }
                let Some((source, mint_pos, ua_pos, name, symbol, uri)) = decode_token_metadata(mp, t22, data) else { continue };
                let account = |pos: usize| accounts.get(pos).and_then(|a| key_at(usize::from(*a))).map(b58);
                let Some(mint) = account(mint_pos) else { continue };
                rows.push(Row {
                    table: self.token_meta_table.clone(),
                    columns: self.token_meta_cols.clone(),
                    values: vec![
                        Val::U64(tx.slot),
                        Val::U64(tx.transaction_slot_index as u64),
                        Val::Str(signature.clone()),
                        Val::U64(i as u64),
                        Val::U64(inner_i as u64),
                        Val::Str(source.to_owned()),
                        Val::Str(mint),
                        Val::Str(name),
                        Val::Str(symbol),
                        Val::Str(uri),
                        account(ua_pos).map_or(Val::Null, Val::Str),
                    ],
                });
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
            if is_http_429(msg) {
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

#[cfg(test)]
mod tests {
    use super::{
        decode_token_metadata, is_http_429, key, owner_token_deltas, summarize_calls, Class, PROGRAM_LABELS, PUMP_AMM_IDL,
        PUMP_IDL, TOKEN_METADATA_INIT,
    };
    use crate::idl::Idl;

    fn borsh(parts: &[&str]) -> Vec<u8> {
        parts.iter().flat_map(|p| (p.len() as u32).to_le_bytes().into_iter().chain(p.bytes())).collect()
    }

    #[test]
    fn token_metadata_from_metaplex_and_token2022() {
        let mut v3 = vec![33u8];
        v3.extend(borsh(&["Pepper", "PEPPER", "https://ipfs.io/x"]));
        v3.extend([0xf4, 0x01, 0, 0, 0, 1, 0]); // seller fee, no creators/collection/uses, is_mutable, no details
        assert_eq!(
            decode_token_metadata(true, false, &v3),
            Some(("metaplex_v3", 1, 4, "Pepper".to_owned(), "PEPPER".to_owned(), "https://ipfs.io/x".to_owned()))
        );
        let mut create = vec![42u8, 0];
        create.extend(borsh(&["A", "B", "C"]));
        assert_eq!(decode_token_metadata(true, false, &create).map(|x| (x.0, x.1, x.2)), Some(("metaplex_create", 2, 5)));
        let mut t22 = TOKEN_METADATA_INIT.to_vec();
        t22.extend(borsh(&["Coin", "CN", ""]));
        assert_eq!(decode_token_metadata(false, true, &t22).map(|x| (x.0, x.1, x.3)), Some(("token2022", 2, "Coin".to_owned())));
        // Other instructions, the wrong program, a cut-off string or an absurd length: nothing.
        assert_eq!(decode_token_metadata(true, false, &[1, 0, 0, 0, 0]), None);
        assert_eq!(decode_token_metadata(false, false, &v3), None);
        assert_eq!(decode_token_metadata(true, false, &v3[..8]), None);
        let mut long = vec![33u8];
        long.extend(u32::MAX.to_le_bytes());
        assert_eq!(decode_token_metadata(true, false, &long), None);
        assert_eq!(decode_token_metadata(false, true, &[3, 0, 0, 0]), None); // an ordinary Token-2022 instruction
    }

    #[test]
    fn program_labels_are_valid_unique_and_match_the_idls() {
        let ids: std::collections::BTreeSet<[u8; 32]> = PROGRAM_LABELS.iter().map(|(id, _, _)| key(id)).collect();
        let names: std::collections::BTreeSet<&str> = PROGRAM_LABELS.iter().map(|(_, n, _)| *n).collect();
        assert_eq!((ids.len(), names.len()), (PROGRAM_LABELS.len(), PROGRAM_LABELS.len()));
        for (idl, label) in [(PUMP_IDL, "pump"), (PUMP_AMM_IDL, "pump_amm")] {
            let address = Idl::parse(idl).unwrap().address;
            assert!(PROGRAM_LABELS.iter().any(|(id, n, c)| *id == address && *n == label && *c == Class::Dex));
        }
    }

    #[test]
    fn calls_summarized_with_depth_runs_and_venues() {
        let c = |d: u64, l: &str, k: Option<Class>| (d, l.to_owned(), k);
        // An arbitrage through a router: Jupiter calls PumpSwap, then Raydium CPMM, with token transfers.
        let calls = vec![
            c(1, "compute_budget", Some(Class::Infra)),
            c(1, "compute_budget", Some(Class::Infra)),
            c(1, "jupiter_v6", Some(Class::Router)),
            c(2, "pump_amm", Some(Class::Dex)),
            c(3, "token", Some(Class::Infra)),
            c(3, "token", Some(Class::Infra)),
            c(2, "raydium_cpmm", Some(Class::Dex)),
            c(3, "token", Some(Class::Infra)),
            c(1, "Unknown1111111111111111111111111111111111111", None),
        ];
        let (programs, venues, n_dex) = summarize_calls(&calls);
        assert_eq!(
            programs,
            "1:compute_budget*2;1:jupiter_v6;2:pump_amm;3:token*2;2:raydium_cpmm;3:token;1:Unknown1111111111111111111111111111111111111"
        );
        assert_eq!((venues.as_str(), n_dex), ("jupiter_v6,pump_amm,raydium_cpmm", 2));
        assert_eq!(summarize_calls(&[]), (String::new(), String::new(), 0));
    }

    #[test]
    fn owner_token_deltas_per_mint() {
        let (me, other) = ("Payer", "Pool");
        // WSOL account closed in the transaction (spent), a new token account (bought), another owner ignored,
        // an unchanged account dropped.
        let pre = [("So11111111111111111111111111111111111111112", me, "1500"), ("MintB", me, "7"), ("MintA", other, "999")];
        let post = [("MintA", me, "250"), ("MintB", me, "7"), ("MintA", other, "749")];
        assert_eq!(
            owner_token_deltas(me, &pre, &post).as_deref(),
            Some("MintA:250;So11111111111111111111111111111111111111112:-1500")
        );
        assert_eq!(owner_token_deltas(me, &[("MintB", me, "7")], &[("MintB", me, "7")]), None);
        assert_eq!(owner_token_deltas(me, &[], &[]), None);
    }

    #[test]
    fn http_429_detection_ignores_slot_numbers() {
        assert!(is_http_429(
            "Error reading header: HTTP status client error (429 Too Many Requests) for url (https://files.old-faithful.net/1051/epoch-1051.car)"
        ));
        assert!(!is_http_429("slot 454429001 not found in index https://example/1051"));
        assert!(!is_http_429("Timeout while waiting for operation: seek_to_slot 454290000"));
    }
}
