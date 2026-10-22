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

// ----------------------------------------------------------------------------- token transfers
// spec-token-transfers-2026-10-15.md v3 (LAB research, sha256 611a6327): token_deltas,
// token_transfers and sol_funding, for a measurable insider share and funder graphs. Extractor only
// (LAB_TRANSFERS=1 with LAB_PUMP_MINTS); the recorder includes this file but never enables them.

const TOKEN_PROGRAM: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const WSOL_MINT: &str = "So11111111111111111111111111111111111111112";
/// sol_funding: the smallest SOL funding of a new wallet that counts (0.001 SOL).
pub const FUNDING_MIN_LAMPORTS: u64 = 1_000_000;
/// The pump.fun curve events whose `mint` is the curve's token: the mint set learns from these. Not
/// ClaimTokenIncentivesEvent and AdminUpdateTokenIncentivesEvent (their mint is the incentive
/// token, PUMP); an event an IDL update adds teaches nothing until it is listed here.
pub const CURVE_MINT_EVENTS: [&str; 13] = [
    "pump/CreateEvent",
    "pump/TradeEvent",
    "pump/CompleteEvent",
    "pump/CompletePumpAmmMigrationEvent",
    "pump/PostCompleteBuyEvent",
    "pump/DistributeCreatorFeesEvent",
    "pump/DistributeFeeToHoldersEvent",
    "pump/SweepBondingCurveFeeEvent",
    "pump/MigrateBondingCurveCreatorEvent",
    "pump/SetCreatorEvent",
    "pump/SetMetaplexCreatorEvent",
    "pump/AdminCtoEvent",
    "pump/UpdateMayhemVirtualParamsEvent",
];

const TOKEN_DELTA_COLS: [(&str, Kind); 12] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("mint", Kind::Utf8),
    ("owner", Kind::Utf8),
    // NULL when the owner has more than one account of the mint in the transaction.
    ("token_account", Kind::Utf8),
    // i128 as text: a u64 balance can move by more than an i64 holds.
    ("delta_raw", Kind::Utf8),
    ("decimals", Kind::U64),
    // The owner is a program-derived address (off the ed25519 curve): a curve or pool vault. What
    // the store's legs leave unexplained is mostly the token fee of reversed pools (the pool's quote
    // vault keeps its LP fee), so most token_deltas rows are vault rows of such pools.
    ("is_vault", Kind::Bool),
    ("has_pump_event", Kind::Bool),
    ("has_pump_event_same_mint", Kind::Bool),
    ("fee_payer", Kind::Utf8),
    ("failed", Kind::Bool),
];

const TOKEN_TRANSFER_COLS: [(&str, Kind); 19] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    // "<outer>" or "<outer>.<inner>" (the inner position as the event tables' inner_ix).
    ("ix_path", Kind::Utf8),
    ("program", Kind::Utf8),
    ("kind", Kind::Utf8),
    ("mint", Kind::Utf8),
    ("source_account", Kind::Utf8),
    ("source_owner", Kind::Utf8),
    ("dest_account", Kind::Utf8),
    ("dest_owner", Kind::Utf8),
    ("authority", Kind::Utf8),
    ("amount_raw", Kind::U64),
    ("decimals", Kind::U64),
    // The program that invoked the token instruction (label or id); NULL at the top level.
    ("outer_program", Kind::Utf8),
    // Invoked by a DEX or router (spec §2B): the trade itself (the store has it), not a graph edge.
    // This also covers a DEX's LP deposits and withdrawals and its fee claims, which are no trades.
    ("in_trade", Kind::Bool),
    ("owner_unresolved", Kind::Bool),
    ("fee_payer", Kind::Utf8),
    ("has_pump_event", Kind::Bool),
    ("failed", Kind::Bool),
];

const SOL_FUNDING_COLS: [(&str, Kind); 10] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("ix_path", Kind::Utf8),
    // "from" / "to" in the spec; named apart from the SQL keywords.
    ("from_account", Kind::Utf8),
    ("to_account", Kind::Utf8),
    ("lamports", Kind::U64),
    ("kind", Kind::Utf8),
    ("fee_payer", Kind::Utf8),
    ("has_pump_event", Kind::Bool),
    ("failed", Kind::Bool),
];

/// An SPL Token / Token-2022 instruction token_transfers keeps, with the positions of its
/// accounts in the instruction's account list.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TokenIx {
    pub kind: &'static str,
    pub amount: Option<u64>,
    pub decimals: Option<u8>,
    pub source: Option<usize>,
    pub mint: Option<usize>,
    pub dest: Option<usize>,
    pub authority: Option<usize>,
}

/// Transfer (3), TransferChecked (12), MintTo (7) / MintToChecked (14), Burn (8) / BurnChecked (15)
/// and CloseAccount (9); None for any other instruction or a short one.
pub fn parse_token_ix(data: &[u8], n_accounts: usize) -> Option<TokenIx> {
    let amount = data.get(1..9).map(|b| u64::from_le_bytes(b.try_into().expect("8 bytes")));
    let decimals = data.get(9).copied();
    let ix = |kind, amount: Option<u64>, decimals: Option<u8>, source, mint, dest, authority, need: usize| {
        (n_accounts >= need).then_some(TokenIx { kind, amount, decimals, source, mint, dest, authority })
    };
    match *data.first()? {
        3 => ix("transfer", Some(amount?), None, Some(0), None, Some(1), Some(2), 3),
        12 => ix("transfer_checked", Some(amount?), Some(decimals?), Some(0), Some(1), Some(2), Some(3), 4),
        7 => ix("mint_to", Some(amount?), None, None, Some(0), Some(1), Some(2), 3),
        14 => ix("mint_to", Some(amount?), Some(decimals?), None, Some(0), Some(1), Some(2), 3),
        8 => ix("burn", Some(amount?), None, Some(0), Some(1), None, Some(2), 3),
        15 => ix("burn", Some(amount?), Some(decimals?), Some(0), Some(1), None, Some(2), 3),
        // The lamports go to account 1; the tokens have no counterparty.
        9 => ix("close_account", None, None, Some(0), None, None, Some(2), 3),
        // Token-2022 TransferFeeExtension (26), TransferCheckedWithFee (1): [26, 1, amount, decimals,
        // fee]. amount leaves the source; the destination receives amount - fee (withheld in it).
        26 if data.get(1) == Some(&1) => {
            let amount = data.get(2..10).map(|b| u64::from_le_bytes(b.try_into().expect("8 bytes")));
            ix("transfer_checked_with_fee", Some(amount?), Some(*data.get(10)?), Some(0), Some(1), Some(2), Some(3), 4)
        }
        _ => None,
    }
}

/// A System instruction that can fund a wallet: ("transfer", lamports) for Transfer (2), and
/// ("create_account", lamports) for CreateAccount (0) of an account the System program owns
/// without data (a wallet, not a program, token or nonce account). Accounts: from = 0, to = 1.
pub fn parse_system_funding(data: &[u8], n_accounts: usize) -> Option<(&'static str, u64)> {
    if n_accounts < 2 || data.len() < 12 {
        return None;
    }
    let lamports = u64::from_le_bytes(data[4..12].try_into().expect("8 bytes"));
    match u32::from_le_bytes(data[0..4].try_into().expect("4 bytes")) {
        2 => Some(("transfer", lamports)),
        0 if data.len() >= 52 && data[12..52].iter().all(|&b| b == 0) => Some(("create_account", lamports)),
        _ => None,
    }
}

/// The account position a System instruction makes into something other than a wallet: given data
/// or another owner (CreateAccount with space or an owner, CreateAccountWithSeed, Assign, Allocate,
/// their seed variants) or a nonce account (InitializeNonceAccount).
pub fn system_non_wallet_target(data: &[u8], accounts: &[u8]) -> Option<u8> {
    let disc = u32::from_le_bytes(data.get(0..4)?.try_into().ok()?);
    let pos = match disc {
        0 if parse_system_funding(data, accounts.len()).is_none() => 1,
        3 => 1,
        1 | 6 | 8 | 9 | 10 => 0,
        _ => return None,
    };
    accounts.get(pos).copied()
}

/// One token balance row: account position, mint, owner, raw amount, decimals.
#[derive(Debug, Clone, Copy)]
pub struct Bal<'a> {
    pub idx: u8,
    pub mint: &'a str,
    pub owner: &'a str,
    pub amount: u128,
    pub decimals: u8,
}

/// Net change per (mint, owner) over the balance rows (post minus pre), without zero changes:
/// (mint, owner, delta, the account position when the owner has one account of the mint, decimals).
pub fn mint_owner_deltas<'a>(pre: &[Bal<'a>], post: &[Bal<'a>]) -> Vec<(&'a str, &'a str, i128, Option<u8>, u8)> {
    let mut m: std::collections::BTreeMap<(&str, &str), (i128, std::collections::BTreeSet<u8>, u8)> = Default::default();
    for (sign, list) in [(-1i128, pre), (1i128, post)] {
        for b in list {
            let e = m.entry((b.mint, b.owner)).or_insert((0, Default::default(), b.decimals));
            e.0 += sign * i128::try_from(b.amount).unwrap_or(i128::MAX);
            e.1.insert(b.idx);
        }
    }
    m.into_iter()
        .filter(|(_, (d, _, _))| *d != 0)
        .map(|((mint, owner), (d, accounts, dec))| (mint, owner, d, (accounts.len() == 1).then(|| *accounts.iter().next().unwrap()), dec))
        .collect()
}

/// The token flows the store's legs in a transaction explain, summed per (mint, owner), where
/// owner None is the curve's vault (owned by the bonding curve PDA): a curve trade moves the
/// trader's tokens against it; a pool trade against the pool's vault (owner: the pool). Normal
/// pools (WSOL quote) move the base; reversed pools (WSOL base, the token as quote, fee in tokens)
/// move the quote as the store records it: the trader the user amount, the vault
/// quote_amount_in_with_lp_fee in and quote_amount_out_without_lp_fee out.
pub fn store_legs(rows: &[Row]) -> std::collections::BTreeMap<(String, Option<String>), i128> {
    let field = |r: &Row, name: &str| r.columns.iter().position(|(n, _)| n == name).and_then(|i| r.values.get(i).cloned());
    let s = |r: &Row, name: &str| match field(r, name) {
        Some(Val::Str(v)) => Some(v),
        _ => None,
    };
    let u = |r: &Row, name: &str| match field(r, name) {
        Some(Val::U64(v)) => Some(i128::from(v)),
        _ => None,
    };
    let mut out: std::collections::BTreeMap<(String, Option<String>), i128> = Default::default();
    let mut add = |mint: String, owner: Option<String>, d: i128| *out.entry((mint, owner)).or_insert(0) += d;
    for r in rows {
        match &*r.table {
            "pump/TradeEvent" => {
                let (Some(mint), Some(user), Some(t)) = (s(r, "mint"), s(r, "user"), u(r, "token_amount")) else { continue };
                let d = if matches!(field(r, "is_buy"), Some(Val::Bool(true))) { t } else { -t };
                add(mint.clone(), Some(user), d);
                add(mint, None, -d);
            }
            "pump_amm/BuyEvent" | "pump_amm/SellEvent" => {
                let buy = r.table.ends_with("BuyEvent");
                let (Some(user), Some(pool), Some(base_mint), Some(quote_mint)) =
                    (s(r, "user"), s(r, "pool"), s(r, "ix_base_mint"), s(r, "ix_quote_mint"))
                else {
                    continue;
                };
                if quote_mint == WSOL_MINT {
                    let Some(b) = u(r, if buy { "base_amount_out" } else { "base_amount_in" }) else { continue };
                    let d = if buy { b } else { -b };
                    add(base_mint.clone(), Some(user), d);
                    add(base_mint, Some(pool), -d);
                } else if base_mint == WSOL_MINT {
                    let (user_d, vault_d) = if buy {
                        (u(r, "user_quote_amount_in").map(|x| -x), u(r, "quote_amount_in_with_lp_fee"))
                    } else {
                        (u(r, "user_quote_amount_out"), u(r, "quote_amount_out_without_lp_fee").map(|x| -x))
                    };
                    if let Some(x) = user_d {
                        add(quote_mint.clone(), Some(user), x);
                    }
                    if let Some(x) = vault_d {
                        add(quote_mint, Some(pool), x);
                    }
                }
            }
            _ => {}
        }
    }
    out
}

/// A (mint, owner) change the store's legs explain: equal to their sum for that owner, or, for a
/// vault owner, to the curve vault's.
pub fn explained(legs: &std::collections::BTreeMap<(String, Option<String>), i128>, mint: &str, owner: &str, is_vault: bool, delta: i128) -> bool {
    legs.get(&(mint.to_owned(), Some(owner.to_owned()))) == Some(&delta) || (is_vault && legs.get(&(mint.to_owned(), None)) == Some(&delta))
}

/// Off the ed25519 curve: a program-derived address (a vault's owner), not a wallet.
pub fn is_pda(b58: &str) -> bool {
    match bs58::decode(b58).into_vec().ok().and_then(|v| <[u8; 32]>::try_from(v).ok()) {
        Some(k) => !solana_message::Address::from(k).is_on_curve(),
        None => false,
    }
}

/// One instruction of a transaction in execution order, with the program that invoked it.
pub struct IxView<'a> {
    pub path: String,
    pub program: Option<&'a [u8]>,
    pub accounts: &'a [u8],
    pub data: &'a [u8],
    pub parent: Option<&'a [u8]>,
}

/// The parts of a transaction the three tables read.
pub struct TxView<'a> {
    pub slot: u64,
    pub tx_index: u64,
    pub fee_payer: String,
    pub failed: bool,
    pub keys: Vec<&'a [u8]>,
    pub ixs: Vec<IxView<'a>>,
    pub pre_lamports: &'a [u64],
    pub post_lamports: &'a [u64],
    pub pre_tokens: Vec<Bal<'a>>,
    pub post_tokens: Vec<Bal<'a>>,
    /// The transaction's log lines (CPMM's emit! events are "Program data:" lines); empty when the block has none.
    pub logs: &'a [String],
}

/// The extractor's token transfer tables (LAB_TRANSFERS=1): the pump.fun mint set and the columns.
pub struct Transfers {
    mints: std::collections::HashSet<String>,
    /// Mints learned while extracting from the pump.fun curve's events (CreateEvent, TradeEvent, ...):
    /// only those without the suffix and not in the file, so the set stays small.
    learned: std::sync::RwLock<std::collections::HashSet<String>>,
    pub mints_file: Option<String>,
    token: Key,
    token2022: Key,
    system: Key,
    delta_cols: Columns,
    transfer_cols: Columns,
    funding_cols: Columns,
    delta_table: Arc<str>,
    transfer_table: Arc<str>,
    funding_table: Arc<str>,
}

impl Transfers {
    pub fn new(mints: std::collections::HashSet<String>, mints_file: Option<String>) -> Self {
        Self {
            mints,
            learned: Default::default(),
            mints_file,
            token: key(TOKEN_PROGRAM),
            token2022: key(TOKEN_2022),
            system: key(SYSTEM_PROGRAM),
            delta_cols: cols(&TOKEN_DELTA_COLS),
            transfer_cols: cols(&TOKEN_TRANSFER_COLS),
            funding_cols: cols(&SOL_FUNDING_COLS),
            delta_table: Arc::from("token_deltas"),
            transfer_table: Arc::from("token_transfers"),
            funding_table: Arc::from("sol_funding"),
        }
    }

    /// LAB_TRANSFERS=1 turns the tables on; LAB_PUMP_MINTS is then a file with one mint per line (the
    /// store's mints). Off otherwise (the recorder never sets it).
    pub fn from_env() -> anyhow::Result<Option<Self>> {
        if std::env::var("LAB_TRANSFERS").map_or(true, |v| v != "1") {
            return Ok(None);
        }
        let path = std::env::var("LAB_PUMP_MINTS").map_err(|_| anyhow::anyhow!("LAB_TRANSFERS=1 needs LAB_PUMP_MINTS (a file of mints)"))?;
        let text = std::fs::read_to_string(&path).map_err(|e| anyhow::anyhow!("LAB_PUMP_MINTS {path}: {e}"))?;
        let mints = text.lines().map(str::trim).filter(|l| !l.is_empty()).map(str::to_owned).collect();
        Ok(Some(Self::new(mints, Some(path))))
    }

    pub fn n_mints(&self) -> usize {
        self.mints.len()
    }

    /// A pump.fun mint: in the store's set, seen in a pump.fun curve event of this run, or with the
    /// pump.fun "pump" suffix (most mints; a superset that also takes rare vanity mints of other
    /// launchpads).
    pub fn is_pump(&self, mint: &str) -> bool {
        mint.ends_with("pump") || self.mints.contains(mint) || self.learned.read().map_or(false, |l| l.contains(mint))
    }

    /// Learn the mints of the pump.fun curve's events in this transaction (before its token rows, so
    /// a create with its first buy is complete). Only the curve events in CURVE_MINT_EVENTS, whose
    /// `mint` is the curve's token. PumpSwap pools can pair any mint (USDC, PUMP), and the curve
    /// program's token incentive events carry the incentive token (PUMP), so they teach nothing.
    fn learn(&self, rows: &[Row]) {
        let new: Vec<&String> = rows
            .iter()
            .filter(|r| CURVE_MINT_EVENTS.contains(&&*r.table))
            .filter_map(|r| match r.columns.iter().position(|(n, _)| n == "mint").and_then(|i| r.values.get(i)) {
                Some(Val::Str(m)) if !m.ends_with("pump") && !self.mints.contains(m) => Some(m),
                _ => None,
            })
            .collect();
        // A read lock first: most transactions teach nothing new, and the threads share the set.
        if new.is_empty() || self.learned.read().map_or(false, |l| new.iter().all(|m| l.contains(*m))) {
            return;
        }
        if let Ok(mut l) = self.learned.write() {
            l.extend(new.into_iter().cloned());
        }
    }

    /// The mints learned in this run, sorted: the next chunk's LAB_PUMP_MINTS adds them.
    pub fn learned_mints(&self) -> Vec<String> {
        let mut v: Vec<String> = self.learned.read().map_or(Vec::new(), |l| l.iter().cloned().collect());
        v.sort();
        v
    }

    /// The three tables' rows of one transaction. `core` holds its event rows (the store's legs);
    /// `labels` names the invoking programs.
    pub fn rows(&self, tx: &TxView, core: &[Row], labels: &HashMap<Key, (&'static str, Class)>) -> Vec<Row> {
        self.learn(core);
        let b58 = |b: &[u8]| bs58::encode(b).into_string();
        let key_of = |pos: Option<usize>, accounts: &[u8]| pos.and_then(|p| accounts.get(p)).and_then(|&a| tx.keys.get(usize::from(a)).copied());
        let has_event = core.iter().any(|r| matches!(&*r.table, "pump/TradeEvent" | "pump_amm/BuyEvent" | "pump_amm/SellEvent"));
        let legs = store_legs(core);
        let mut out = Vec::new();
        let base = |values: Vec<Val>| -> Vec<Val> {
            let mut v = vec![Val::U64(tx.slot), Val::U64(tx.tx_index)];
            v.extend(values);
            v
        };

        // token_deltas: net change per (mint, owner) of pump mints, minus what the store's legs explain.
        for (mint, owner, d, account, dec) in mint_owner_deltas(&tx.pre_tokens, &tx.post_tokens) {
            if !self.is_pump(mint) {
                continue;
            }
            let vault = is_pda(owner);
            if explained(&legs, mint, owner, vault, d) {
                continue;
            }
            let same_mint = legs.keys().any(|(m, _)| m == mint);
            out.push(Row {
                table: self.delta_table.clone(),
                columns: self.delta_cols.clone(),
                values: base(vec![
                    Val::Str(mint.to_owned()),
                    Val::Str(owner.to_owned()),
                    account.and_then(|a| tx.keys.get(usize::from(a))).map_or(Val::Null, |k| Val::Str(b58(*k))),
                    Val::Str(d.to_string()),
                    Val::U64(u64::from(dec)),
                    Val::Bool(vault),
                    Val::Bool(has_event),
                    Val::Bool(same_mint),
                    Val::Str(tx.fee_payer.clone()),
                    Val::Bool(tx.failed),
                ]),
            });
        }

        // Accounts that are no wallet in this transaction: token accounts and accounts a System
        // instruction gives data or another owner (sol_funding skips them as receivers).
        let non_wallet: std::collections::HashSet<u8> = tx.pre_tokens.iter().chain(tx.post_tokens.iter()).map(|b| b.idx)
            .chain(tx.ixs.iter().filter(|ix| ix.program == Some(self.system.as_slice())).filter_map(|ix| system_non_wallet_target(ix.data, ix.accounts)))
            .collect();
        // token_transfers and sol_funding, per instruction in execution order.
        let bal_of = |key: Option<&[u8]>| {
            key.and_then(|k| tx.keys.iter().position(|x| *x == k))
                .and_then(|pos| tx.post_tokens.iter().chain(tx.pre_tokens.iter()).find(|b| usize::from(b.idx) == pos).copied())
        };
        for ix in &tx.ixs {
            let Some(program) = ix.program else { continue };
            if program == self.token.as_slice() || program == self.token2022.as_slice() {
                let Some(t) = parse_token_ix(ix.data, ix.accounts.len()) else { continue };
                let (src, dst, mint_key, auth) =
                    (key_of(t.source, ix.accounts), key_of(t.dest, ix.accounts), key_of(t.mint, ix.accounts), key_of(t.authority, ix.accounts));
                let (sb, db) = (bal_of(src), bal_of(dst));
                let mint = mint_key.map(b58).or_else(|| sb.or(db).map(|b| b.mint.to_owned()));
                let Some(mint) = mint.filter(|m| self.is_pump(m)) else { continue };
                let unresolved = (src.is_some() && sb.is_none()) || (dst.is_some() && db.is_none());
                let parent = ix.parent.and_then(|p| <[u8; 32]>::try_from(p).ok());
                let (outer_label, in_trade) = match parent {
                    Some(p) => match labels.get(&p) {
                        Some((name, class)) => (Val::Str((*name).to_owned()), matches!(class, Class::Dex | Class::Router)),
                        None => (Val::Str(b58(&p)), false),
                    },
                    None => (Val::Null, false),
                };
                out.push(Row {
                    table: self.transfer_table.clone(),
                    columns: self.transfer_cols.clone(),
                    values: base(vec![
                        Val::Str(ix.path.clone()),
                        Val::Str(if program == self.token.as_slice() { "token" } else { "token2022" }.to_owned()),
                        Val::Str(t.kind.to_owned()),
                        Val::Str(mint),
                        src.map_or(Val::Null, |k| Val::Str(b58(k))),
                        sb.map_or(Val::Null, |b| Val::Str(b.owner.to_owned())),
                        dst.map_or(Val::Null, |k| Val::Str(b58(k))),
                        db.map_or(Val::Null, |b| Val::Str(b.owner.to_owned())),
                        auth.map_or(Val::Null, |k| Val::Str(b58(k))),
                        t.amount.map_or(Val::Null, Val::U64),
                        t.decimals.map(u64::from).or_else(|| sb.or(db).map(|b| u64::from(b.decimals))).map_or(Val::Null, Val::U64),
                        outer_label,
                        Val::Bool(in_trade),
                        Val::Bool(unresolved),
                        Val::Str(tx.fee_payer.clone()),
                        Val::Bool(has_event),
                        Val::Bool(tx.failed),
                    ]),
                });
            } else if program == self.system.as_slice() {
                let Some((kind, lamports)) = parse_system_funding(ix.data, ix.accounts.len()) else { continue };
                if lamports < FUNDING_MIN_LAMPORTS {
                    continue;
                }
                let Some(&to_pos) = ix.accounts.get(1) else { continue };
                // A wallet birth: the receiver held no SOL before the transaction and still holds SOL
                // after it (a failed transaction changes no balance), and is a wallet: on the curve (a
                // key that can sign; PDAs and associated token accounts are not) and in this transaction
                // no token account and not given data or another owner.
                let at = usize::from(to_pos);
                if tx.pre_lamports.get(at) != Some(&0) || (!tx.failed && tx.post_lamports.get(at).map_or(true, |&l| l == 0)) {
                    continue;
                }
                let (Some(from), Some(to)) = (key_of(Some(0), ix.accounts), key_of(Some(1), ix.accounts)) else { continue };
                if non_wallet.contains(&to_pos) || !<[u8; 32]>::try_from(to).map_or(false, |k| solana_message::Address::from(k).is_on_curve()) {
                    continue;
                }
                out.push(Row {
                    table: self.funding_table.clone(),
                    columns: self.funding_cols.clone(),
                    values: base(vec![
                        Val::Str(ix.path.clone()),
                        Val::Str(b58(from)),
                        Val::Str(b58(to)),
                        Val::U64(lamports),
                        Val::Str(kind.to_owned()),
                        Val::Str(tx.fee_payer.clone()),
                        Val::Bool(has_event),
                        Val::Bool(tx.failed),
                    ]),
                });
            }
        }
        out
    }
}

/// The program that invoked inner instruction j of a top-level instruction: the nearest earlier inner
/// instruction one stack level up; the top-level one (`outer`) at height 2 or without heights.
/// `heights` and `programs` are the group's stack heights and program keys; None when no earlier
/// instruction is one level up.
pub fn invoker<'a>(heights: &[Option<u32>], programs: &[Option<&'a [u8]>], j: usize, outer: Option<&'a [u8]>) -> Option<&'a [u8]> {
    match heights.get(j).copied().flatten() {
        Some(h) if h > 2 => (0..j).rev().find(|&k| heights[k] == Some(h - 1)).and_then(|k| programs[k]),
        _ => outer,
    }
}

/// A transaction as the token transfer tables read it: account keys, instructions in execution
/// order (each top-level one, then its inner ones) with the program that invoked each, and the
/// pre-balances.
fn tx_view(tx: &TransactionData) -> TxView<'_> {
    let msg = &tx.transaction.message;
    let meta = &tx.transaction_status_meta;
    let mut keys: Vec<&[u8]> = msg.static_account_keys().iter().map(AsRef::as_ref).collect();
    keys.extend(meta.loaded_addresses.writable.iter().map(AsRef::as_ref));
    keys.extend(meta.loaded_addresses.readonly.iter().map(AsRef::as_ref));
    let key = |i: u8| keys.get(usize::from(i)).copied();
    let inner_groups = meta.inner_instructions.as_deref().unwrap_or(&[]);
    let mut ixs = Vec::new();
    for (i, ix) in msg.instructions().iter().enumerate() {
        let outer_program = key(ix.program_id_index);
        ixs.push(IxView { path: i.to_string(), program: outer_program, accounts: &ix.accounts, data: &ix.data, parent: None });
        for group in inner_groups.iter().filter(|g| usize::from(g.index) == i) {
            let heights: Vec<Option<u32>> = group.instructions.iter().map(|x| x.stack_height).collect();
            let programs: Vec<Option<&[u8]>> = group.instructions.iter().map(|x| key(x.instruction.program_id_index)).collect();
            for (j, inner) in group.instructions.iter().enumerate() {
                let parent = invoker(&heights, &programs, j, outer_program);
                ixs.push(IxView {
                    path: format!("{i}.{j}"),
                    program: key(inner.instruction.program_id_index),
                    accounts: &inner.instruction.accounts,
                    data: &inner.instruction.data,
                    parent,
                });
            }
        }
    }
    // The same conversion for both lists (the balance type is not a direct dependency to name).
    macro_rules! bals {
        ($list:expr) => {
            $list
                .as_deref()
                .unwrap_or(&[])
                .iter()
                .map(|b| Bal {
                    idx: b.account_index,
                    mint: b.mint.as_str(),
                    owner: b.owner.as_str(),
                    amount: b.ui_token_amount.amount.parse().unwrap_or(0),
                    decimals: b.ui_token_amount.decimals,
                })
                .collect::<Vec<Bal>>()
        };
    }
    TxView {
        slot: tx.slot,
        tx_index: tx.transaction_slot_index as u64,
        fee_payer: msg.static_account_keys().first().map(|k| bs58::encode(k).into_string()).unwrap_or_default(),
        failed: meta.status.is_err(),
        pre_tokens: bals!(meta.pre_token_balances),
        post_tokens: bals!(meta.post_token_balances),
        pre_lamports: &meta.pre_balances,
        post_lamports: &meta.post_balances,
        keys,
        ixs,
        logs: meta.log_messages.as_deref().unwrap_or(&[]),
    }
}

fn cols(spec: &[(&str, Kind)]) -> Columns {
    Arc::new(spec.iter().map(|(n, k)| ((*n).to_owned(), *k)).collect())
}

// ----------------------------------------------------------------------------- venue census
// Step 0 of the other venues (owner 2026-10-21; coordinator design, approved): one row per transaction that invokes a
// DEX of PROGRAM_LABELS (pump and PumpSwap included), for a census per venue: transactions, success, signers, a SOL
// volume proxy, launches and graduations, tips and a first MEV estimate. No price decoding: instruction names and the
// pool account come from the projects' IDLs (idl/venues/SOURCES.md, src/venue_ix.rs). Extractor only
// (LAB_VENUE_CENSUS=1); the recorder includes this file but never enables it, and without it every table is as before.

include!("venue_ix.rs");

/// Raydium AMM v4 (no Anchor IDL): (instruction tag = data[0], name, position of the amm account). From
/// raydium-io/raydium-amm @ 91739d8, program/src/instruction.rs: AmmInstruction::unpack for the tags, the builders for
/// the accounts (amm_pool at 1; at 4 in initialize2). The admin instructions keep no pool.
pub const AMM_V4_IXS: [(u8, &str, Option<u8>); 12] = [
    (1, "initialize2", Some(4)),
    (3, "deposit", Some(1)),
    (4, "withdraw", Some(1)),
    (6, "set_params", None),
    (7, "withdraw_pnl", None),
    (9, "swap_base_in", Some(1)),
    (11, "swap_base_out", Some(1)),
    (14, "create_config_account", None),
    (15, "update_config_account", None),
    (16, "swap_base_in_v2", Some(1)),
    (17, "swap_base_out_v2", Some(1)),
    (18, "withdraw_excess_lamports", None),
];

/// One row per transaction that invokes a DEX program (top level or inner; a failed transaction keeps the inner
/// instructions that ran before its failure, so its dex_ixs can name swaps that were rolled back: count swaps of
/// successful transactions only, as the smoke does; smoke 99zzzzt: DAMM v2's 458 such events in the 1054 range).
const VENUE_CENSUS_COLS: [(&str, Kind); 16] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("failed", Kind::Bool),
    ("fee_payer", Kind::Utf8),
    ("tx_fee", Kind::U64),
    // As in tx_ctx: the charged fee minus the base fee per signature; transfers to the Jito tip accounts.
    ("priority_fee", Kind::U64),
    ("jito_tip", Kind::U64),
    ("cu_consumed", Kind::U64),
    // The DEX programs invoked: labels, sorted, comma separated, and how many.
    ("venues", Kind::Utf8),
    ("n_dex", Kind::U64),
    // The top-level programs but infrastructure (compute budget, system, token, ATA, memo), in order, each once, ';'
    // separated, by label or id: who drives the transaction (a router, a bot's own program, the DEX itself).
    ("top_programs", Kind::Utf8),
    // Every DEX instruction in execution order as "label:name:pool", ';' separated: name "?" when the discriminator
    // (tag) is not in the IDL table, pool empty when the instruction has no pool account. Anchor event self-CPIs are
    // left out.
    ("dex_ixs", Kind::Utf8),
    // The fee payer: lamports after minus before (fee and tip included) and the net change of its token accounts per
    // mint as "mint:delta" (raw, WSOL included), ';' separated, as tx_ctx.signer_token_deltas.
    ("signer_sol_delta", Kind::I64),
    ("signer_token_deltas", Kind::Utf8),
    // Every mint of the transaction's token balances (before and after), sorted, ';' separated.
    ("mints", Kind::Utf8),
    ("n_dex_ixs", Kind::U64),
];

/// A transaction as the census reads it: the view, plus fee, required signatures and compute units.
pub struct CensusTx<'a> {
    pub view: TxView<'a>,
    pub fee: u64,
    pub signatures: u64,
    pub cu_consumed: Option<u64>,
}

/// The venue census (LAB_VENUE_CENSUS=1): the instruction tables and the columns.
pub struct Census {
    anchor: HashMap<(&'static str, [u8; 8]), (&'static str, Option<u8>)>,
    amm_v4: HashMap<u8, (&'static str, Option<u8>)>,
    system: Key,
    jito: [Key; 8],
    cols: Columns,
    table: Arc<str>,
}

impl Default for Census {
    fn default() -> Self {
        Self {
            anchor: VENUE_IXS.iter().map(|&(label, disc, name, pool)| ((label, disc), (name, pool))).collect(),
            amm_v4: AMM_V4_IXS.iter().map(|&(tag, name, pool)| (tag, (name, pool))).collect(),
            system: key(SYSTEM_PROGRAM),
            jito: JITO_TIP_ACCOUNTS.map(key),
            cols: cols(&VENUE_CENSUS_COLS),
            table: Arc::from("venue_census"),
        }
    }
}

impl Census {
    pub fn new() -> Self {
        Self::default()
    }

    /// On with LAB_VENUE_CENSUS=1 only.
    pub fn from_env() -> Option<Self> {
        (std::env::var("LAB_VENUE_CENSUS").ok().as_deref() == Some("1")).then(Self::new)
    }

    /// An instruction's name and pool position: the IDL table by discriminator (Anchor), the tag table for AMM v4.
    fn instruction(&self, label: &'static str, data: &[u8]) -> (&'static str, Option<u8>) {
        let found = if label == "raydium_amm_v4" {
            data.first().and_then(|t| self.amm_v4.get(t))
        } else {
            data.get(..8).and_then(|d| <[u8; 8]>::try_from(d).ok()).and_then(|d| self.anchor.get(&(label, d)))
        };
        found.copied().unwrap_or(("?", None))
    }

    /// Does the transaction invoke a DEX program (top level or inner)? Cheap: no view is built for the others.
    pub fn invokes_dex(&self, tx: &TransactionData, labels: &HashMap<Key, (&'static str, Class)>) -> bool {
        let msg = &tx.transaction.message;
        let meta = &tx.transaction_status_meta;
        let statics = msg.static_account_keys();
        let (w, r) = (&meta.loaded_addresses.writable, &meta.loaded_addresses.readonly);
        let is_dex = |i: u8| -> bool {
            let i = usize::from(i);
            let k: Option<&[u8]> = if i < statics.len() {
                Some(statics[i].as_ref())
            } else if i - statics.len() < w.len() {
                Some(w[i - statics.len()].as_ref())
            } else {
                r.get(i - statics.len() - w.len()).map(AsRef::as_ref)
            };
            k.and_then(|k| <[u8; 32]>::try_from(k).ok()).and_then(|k| labels.get(&k)).is_some_and(|(_, c)| *c == Class::Dex)
        };
        msg.instructions().iter().any(|ix| is_dex(ix.program_id_index))
            || meta.inner_instructions.as_deref().unwrap_or(&[]).iter().any(|g| g.instructions.iter().any(|ix| is_dex(ix.instruction.program_id_index)))
    }

    /// The census row of a transaction; None when it invokes no DEX program.
    pub fn row(&self, tx: &CensusTx, labels: &HashMap<Key, (&'static str, Class)>) -> Option<Row> {
        let v = &tx.view;
        let b58 = |b: &[u8]| bs58::encode(b).into_string();
        let label_of = |k: &[u8]| <[u8; 32]>::try_from(k).ok().and_then(|k| labels.get(&k)).copied();
        let mut venues = std::collections::BTreeSet::new();
        let mut top: Vec<String> = Vec::new();
        let mut dex_ixs: Vec<String> = Vec::new();
        let mut jito_tip: u64 = 0;
        for ix in &v.ixs {
            let Some(program) = ix.program else { continue };
            let label = label_of(program);
            if !ix.path.contains('.') && !matches!(label, Some((_, Class::Infra))) {
                let name = label.map_or_else(|| b58(program), |(n, _)| n.to_owned());
                if !top.contains(&name) {
                    top.push(name);
                }
            }
            if program == self.system.as_slice() && ix.data.len() >= 12 && u32::from_le_bytes(ix.data[0..4].try_into().unwrap()) == 2 {
                let to = ix.accounts.get(1).and_then(|&a| v.keys.get(usize::from(a)));
                if to.is_some_and(|to| self.jito.iter().any(|j| j.as_slice() == *to)) {
                    jito_tip = jito_tip.saturating_add(u64::from_le_bytes(ix.data[4..12].try_into().unwrap()));
                }
            }
            if let Some((label, Class::Dex)) = label {
                if ix.data.starts_with(&EVENT_IX_TAG) {
                    continue;
                }
                venues.insert(label);
                let (name, pool) = self.instruction(label, ix.data);
                // A newer account layout than the pinned IDL's (VENUE_ACCOUNT_LAYOUTS): its pool position.
                let pool = layout_pool(label, name, ix.accounts.len()).or(pool);
                // An instruction the IDLs do not know: its first eight bytes, to be named (LAB review on #506).
                let name = if name == "?" { format!("?{}", hex::encode(&ix.data[..ix.data.len().min(8)])) } else { name.to_owned() };
                let pool = pool.and_then(|p| ix.accounts.get(usize::from(p))).and_then(|&a| v.keys.get(usize::from(a))).map(|k| b58(*k)).unwrap_or_default();
                dex_ixs.push(format!("{label}:{name}:{pool}"));
            }
        }
        if venues.is_empty() {
            return None;
        }
        let sol_delta = match (v.pre_lamports.first(), v.post_lamports.first()) {
            (Some(&a), Some(&b)) => i64::try_from(i128::from(b) - i128::from(a)).map_or(Val::Null, Val::I64),
            _ => Val::Null,
        };
        let deltas: Vec<String> = mint_owner_deltas(&v.pre_tokens, &v.post_tokens)
            .into_iter()
            .filter(|(_, owner, ..)| *owner == v.fee_payer)
            .map(|(mint, _, d, ..)| format!("{mint}:{d}"))
            .collect();
        let mints: std::collections::BTreeSet<&str> = v.pre_tokens.iter().chain(v.post_tokens.iter()).map(|b| b.mint).collect();
        let joined = |parts: Vec<String>, sep: &str| if parts.is_empty() { Val::Null } else { Val::Str(parts.join(sep)) };
        let n_ixs = dex_ixs.len() as u64;
        Some(Row {
            table: self.table.clone(),
            columns: self.cols.clone(),
            values: vec![
                Val::U64(v.slot),
                Val::U64(v.tx_index),
                Val::Bool(v.failed),
                Val::Str(v.fee_payer.clone()),
                Val::U64(tx.fee),
                Val::U64(tx.fee.saturating_sub(tx.signatures * LAMPORTS_PER_SIGNATURE)),
                Val::U64(jito_tip),
                tx.cu_consumed.map_or(Val::Null, Val::U64),
                Val::Str(venues.iter().copied().collect::<Vec<_>>().join(",")),
                Val::U64(venues.len() as u64),
                joined(top, ";"),
                Val::Str(dex_ixs.join(";")),
                sol_delta,
                joined(deltas, ";"),
                joined(mints.into_iter().map(str::to_owned).collect(), ";"),
                Val::U64(n_ixs),
            ],
        })
    }
}

// ----------------------------------------------------------------------------- venue decoders
// Step 1 of the other venues (research/venue-decoders-spec-2026-10-21.md v0.2, LAB review). PR 1: the events of Meteora
// DBC, Meteora DAMM v2 and Raydium LaunchLab, which all emit with Anchor's emit_cpi (a self-CPI carrying the event tag,
// as pump does), decoded with their pinned IDLs (idl/venues/SOURCES.md) field by field, and the graduation instructions
// of DBC and LaunchLab (they emit no event). PR 2: Raydium CPMM, which emits with Anchor's emit! (base64 "Program data:"
// lines in the transaction's log): the log lines it wrote itself, paired with its instructions in execution order; a
// swap without its logged event (a truncated log) falls back to its vaults' token balance changes (source 'balance');
// CPMM's pool creations (no event) get an instruction row. Costs, tips and venues of the transaction are in
// venue_census (join on slot, tx_index). Extractor only (LAB_VENUES=1); LabPlugin::for_recorder never has it, and
// without it every table is as before.

pub const DBC_IDL: &str = include_str!("../idl/venues/meteora_dbc.json");
pub const DAMM_V2_IDL: &str = include_str!("../idl/venues/meteora_damm_v2.json");
pub const LAUNCHLAB_IDL: &str = include_str!("../idl/venues/raydium_launchlab.json");
pub const CPMM_IDL: &str = include_str!("../idl/venues/raydium_cpmm.json");
/// DBC's older releases (newest first): events and instructions the current IDL no longer has (fee claims, configs)
/// still appear in historical blocks. Only discriminators the current IDL does not know are taken from them.
pub const DBC_OLDER_IDLS: [(&str, &str); 4] = [
    ("meteora_dbc.release_0.1.6.json", include_str!("../idl/venues/meteora_dbc.release_0.1.6.json")),
    ("meteora_dbc.release_0.1.4.json", include_str!("../idl/venues/meteora_dbc.release_0.1.4.json")),
    ("meteora_dbc.release_0.1.3.json", include_str!("../idl/venues/meteora_dbc.release_0.1.3.json")),
    ("meteora_dbc.release_0.1.2.json", include_str!("../idl/venues/meteora_dbc.release_0.1.2.json")),
];

/// Context columns before the IDL fields in every venue event table (`<label>/<Event>`).
pub const VENUE_EVENT_CONTEXT: [(&str, Kind); 20] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    // The transaction failed: the runtime recorded this event before the failure (an arb route that failed after
    // its swap) and rolled its state back. Not a trade; kept for failed attempts (smoke 99zzzzs).
    ("failed", Kind::Bool),
    // The event's position: for source 'cpi' the event CPI's (its top-level instruction and its index among that
    // one's inner instructions); for 'log' and 'balance' the emitting instruction's own (an emit! has no instruction).
    ("outer_ix", Kind::U64),
    ("inner_ix", Kind::U64),
    // Its position among this transaction's decoded venue events (execution order), and among all of them, the
    // venue_unknown_events rows included (LAB review on #506: an unknown event does not move event_seq).
    ("event_seq", Kind::U64),
    ("event_seq_all", Kind::U64),
    ("fee_payer", Kind::Utf8),
    ("outer_program", Kind::Utf8),
    // The instruction that emitted it: for 'cpi' the nearest earlier instruction of the same program in the same
    // top-level group that is no event (one swap is one (slot, tx_index, outer_ix, parent_inner_ix), whatever number of
    // events it emits: DBC emits EvtSwap and EvtSwap2 for one swap); for 'log' and 'balance' the instruction itself. Its
    // inner index (NULL when it is the top-level one), its first eight bytes (hex), its IDL name and decoded arguments
    // (slippage limits included).
    ("parent_inner_ix", Kind::U64),
    ("parent_ix_disc", Kind::Utf8),
    ("parent_ix_name", Kind::Utf8),
    ("parent_ix_args", Kind::Utf8),
    // ok / prefix / extra / error (idl.rs DecodeStatus); for 'balance': balance, balance_ambiguous (another instruction
    // of the program touches the same pool in the transaction: the amounts are NULL) or balance_missing (a vault has no
    // balance row: NULL).
    ("decode_status", Kind::Utf8),
    // cpi (an emit_cpi self-CPI), log (an emit! "Program data:" line) or balance (CPMM's fallback from its vaults' token
    // balances when its log line is missing, e.g. a truncated log).
    ("source", Kind::Utf8),
    ("payload_len", Kind::U64),
    // Accounts of the parent instruction by IDL name: the trader (payer / signer / owner / creator / user), the pool
    // (pool_state / pool / virtual_pool / ...), the base and quote mints (base_mint / token_a_mint / base_token_mint and
    // their quote counterparts). The events lack some of these (DAMM v2's swap event names no mint, its instruction
    // does; no event names the trader).
    ("ix_trader", Kind::Utf8),
    ("ix_pool", Kind::Utf8),
    ("ix_base_mint", Kind::Utf8),
    ("ix_quote_mint", Kind::Utf8),
];
/// Positions of event_seq and event_seq_all in VENUE_EVENT_CONTEXT and of event_seq_all in VENUE_UNKNOWN_COLS (set
/// after the transaction's events are put in execution order).
const SEQ_COL: usize = 5;
const SEQ_ALL_COL: usize = 6;
const UNKNOWN_SEQ_ALL_COL: usize = 4;

/// One row per graduation instruction of a successful transaction.
const VENUE_GRADUATION_COLS: [(&str, Kind); 12] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("outer_ix", Kind::U64),
    // NULL when the instruction is top level.
    ("inner_ix", Kind::U64),
    ("fee_payer", Kind::Utf8),
    ("venue", Kind::Utf8),
    ("ix_name", Kind::Utf8),
    ("from_pool", Kind::Utf8),
    ("to_venue", Kind::Utf8),
    ("to_pool", Kind::Utf8),
    // DBC's DAMM v1 migration names token_a / token_b: taken as base / quote (DBC creates the pool base first); the
    // store build checks it against the new pool's mints.
    ("base_mint", Kind::Utf8),
    ("quote_mint", Kind::Utf8),
];

/// One row per pool-creation instruction without an event (CPMM's initialize and initialize_with_permission) of a
/// successful transaction: the store's venue_pools reads it (spec §2).
const VENUE_INIT_COLS: [(&str, Kind); 15] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("outer_ix", Kind::U64),
    // NULL when the instruction is top level.
    ("inner_ix", Kind::U64),
    ("fee_payer", Kind::Utf8),
    ("venue", Kind::Utf8),
    ("ix_name", Kind::Utf8),
    ("pool", Kind::Utf8),
    ("creator", Kind::Utf8),
    ("token_0_mint", Kind::Utf8),
    ("token_1_mint", Kind::Utf8),
    ("token_0_vault", Kind::Utf8),
    ("token_1_vault", Kind::Utf8),
    ("lp_mint", Kind::Utf8),
    // The decoded arguments (init amounts, open time, creator fee mode) as JSON.
    ("ix_args", Kind::Utf8),
];

/// Venue events whose discriminator no pinned IDL knows (current or older): kept to be named, not decoded.
const VENUE_UNKNOWN_COLS: [(&str, Kind); 11] = [
    ("slot", Kind::U64),
    ("tx_index", Kind::U64),
    ("outer_ix", Kind::U64),
    ("inner_ix", Kind::U64),
    ("event_seq_all", Kind::U64),
    ("venue", Kind::Utf8),
    ("source", Kind::Utf8),
    ("event_disc", Kind::Utf8),
    ("payload_len", Kind::U64),
    ("parent_ix_name", Kind::Utf8),
    ("parent_ix_disc", Kind::Utf8),
];

/// (program label, instruction, from-pool account, to-pool account, base mint account, quote mint account, to venue).
pub const VENUE_GRADUATIONS: [(&str, &str, &str, &str, &str, &str, &str); 4] = [
    ("meteora_dbc", "migrate_meteora_damm", "virtual_pool", "pool", "token_a_mint", "token_b_mint", "meteora_damm_v1"),
    ("meteora_dbc", "migration_damm_v2", "virtual_pool", "pool", "base_mint", "quote_mint", "meteora_damm_v2"),
    ("raydium_launchlab", "migrate_to_amm", "pool_state", "amm_pool", "base_mint", "quote_mint", "raydium_amm_v4"),
    ("raydium_launchlab", "migrate_to_cpswap", "pool_state", "cpswap_pool", "base_mint", "quote_mint", "raydium_cpmm"),
];
/// (program label, instruction): pool creations without an event, written to venue_inits.
pub const VENUE_INITS: [(&str, &str); 2] = [("raydium_cpmm", "initialize"), ("raydium_cpmm", "initialize_with_permission")];
/// (program label, instruction, base_input): the swaps that fall back to their vaults' balances without a log event.
pub const VENUE_BALANCE_SWAPS: [(&str, &str, bool); 2] = [("raydium_cpmm", "swap_base_input", true), ("raydium_cpmm", "swap_base_output", false)];
/// Programs whose events are emit! log lines (no self-CPI).
const VENUE_LOG_EVENTS: [&str; 1] = ["raydium_cpmm"];
/// Account layouts newer than the pinned IDL: (program label, instruction, number of accounts, the census's pool
/// account, the account names in order). An instruction with that many accounts reads them by these names.
/// LaunchLab's migrate_to_amm since its 2026-09-09 upgrade: no arguments, nine OpenBook accounts gone, 23 accounts
/// (pool_state 23 -> 14, amm_pool 13 -> 5). Source: Raydium's docs, raydium-io/raydium-docs-v1 @ 3682384 (2026-10-20),
/// products/launchlab/instructions.mdx, "MigrateToAmm / MigrateToCpswap", fetched 2026-10-22; raydium-idl (e7e0c96, the
/// pinned IDL) has not been updated. The older 32-account layout keeps the IDL's names.
pub const VENUE_ACCOUNT_LAYOUTS: [(&str, &str, usize, &str, &[&str]); 1] = [(
    "raydium_launchlab",
    "migrate_to_amm",
    23,
    "pool_state",
    &[
        "payer", "base_mint", "quote_mint", "market", "amm_program", "amm_pool", "amm_authority", "amm_lp_mint", "amm_base_vault",
        "amm_quote_vault", "amm_target_orders", "amm_config", "amm_create_fee_destination", "authority", "pool_state", "global_config",
        "base_vault", "quote_vault", "pool_lp_token", "spl_token_program", "associated_token_program", "system_program", "rent_program",
    ],
)];

/// The census's pool position of an instruction under a newer account layout (VENUE_ACCOUNT_LAYOUTS), if any.
fn layout_pool(label: &str, name: &str, n_accounts: usize) -> Option<u8> {
    VENUE_ACCOUNT_LAYOUTS
        .iter()
        .find(|l| l.0 == label && l.1 == name && l.2 == n_accounts)
        .and_then(|l| l.4.iter().position(|a| *a == l.3))
        .and_then(|p| u8::try_from(p).ok())
}
const VENUE_TRADER_ACCOUNTS: [&str; 5] = ["payer", "signer", "owner", "creator", "user"];
const VENUE_POOL_ACCOUNTS: [&str; 7] = ["pool_state", "lb_pair", "whirlpool", "whirlpool_one", "pool", "virtual_pool", "amm"];
const VENUE_BASE_ACCOUNTS: [&str; 4] = ["base_mint", "token_a_mint", "base_token_mint", "token_0_mint"];
const VENUE_QUOTE_ACCOUNTS: [&str; 4] = ["quote_mint", "token_b_mint", "quote_token_mint", "token_1_mint"];

/// An instruction of a venue program: its accounts' positions for the context and, for a graduation, its row.
struct VenueIx {
    /// The IDL (VenueProgram.idls) that defines it: 0 the current one, then the older ones.
    idl: usize,
    name: &'static str,
    /// The IDL's account names in order (the init rows and the balance fallback look accounts up by name).
    accounts: Vec<String>,
    trader: Option<usize>,
    pool: Option<usize>,
    base: Option<usize>,
    quote: Option<usize>,
    /// Newer account layouts by number of accounts (VENUE_ACCOUNT_LAYOUTS).
    layouts: Vec<(usize, Vec<String>)>,
    /// (from pool, to pool, base, quote account names, to venue) of a graduation instruction.
    graduation: Option<(&'static str, &'static str, &'static str, &'static str, &'static str)>,
    /// A pool creation without an event (VENUE_INITS).
    init: bool,
    /// A swap with the balance fallback (VENUE_BALANCE_SWAPS): its base_input.
    balance_swap: Option<bool>,
}

impl VenueIx {
    fn at(&self, name: &str) -> Option<usize> {
        self.accounts.iter().position(|a| a == name)
    }

    /// The account's position in an instruction with `n` accounts: a newer layout of that size, else the IDL's.
    fn at_n(&self, name: &str, n: usize) -> Option<usize> {
        match self.layouts.iter().find(|(k, _)| *k == n) {
            Some((_, names)) => names.iter().position(|a| a == name),
            None => self.at(name),
        }
    }
}

struct VenueProgram {
    label: &'static str,
    /// The current IDL first, then older ones (DBC): a discriminator is looked up in this order.
    idls: Vec<ProgramIdl>,
    ixs: HashMap<[u8; 8], VenueIx>,
    /// Its events are emit! log lines (CPMM), not self-CPIs; the program id in base58 for the log walk.
    log_events: bool,
    address: String,
    /// The event a balance fallback writes (CPMM's SwapEvent).
    fallback_event: Option<[u8; 8]>,
}

impl VenueProgram {
    fn id(&self) -> &[u8] {
        self.idls[0].id.as_slice()
    }

    /// The event table of a discriminator: (the IDL that knows it, event index, table, columns).
    fn event(&self, disc: &[u8; 8]) -> Option<(&ProgramIdl, &(usize, Arc<str>, Columns))> {
        self.idls.iter().find_map(|p| p.tables.get(disc).map(|t| (p, t)))
    }
}

/// The venue decoders (LAB_VENUES=1): DBC, DAMM v2, LaunchLab and CPMM.
pub struct Venues {
    programs: Vec<VenueProgram>,
    grad_cols: Columns,
    grad_table: Arc<str>,
    init_cols: Columns,
    init_table: Arc<str>,
    unknown_cols: Columns,
    unknown_table: Arc<str>,
}

/// One transaction's venue rows and its counts.
#[derive(Default)]
pub struct VenueRows {
    pub rows: Vec<Row>,
    pub events: u64,
    pub graduations: u64,
    pub inits: u64,
    pub unknown_events: u64,
    /// Transactions touching a log-event venue whose log was truncated ("Log truncated").
    pub log_truncated: u64,
    /// Successful transactions whose logged invocations of a log-event venue do not pair with its instructions (no
    /// log lines at all, for one): their swaps fall back to balances.
    pub log_unpaired: u64,
    /// "Program data:" lines of a log-event venue that are no base64 or shorter than a discriminator.
    pub log_undecodable: u64,
    /// Swap rows from the balance fallback.
    pub balance_rows: u64,
}

/// The "Program data:" lines a program logged itself (Anchor's emit!: base64 of discriminator and Borsh body), each with
/// the ordinal of that program's invocation in execution order (0 = its first invoke in the transaction); whether the
/// log was truncated ("Log truncated": the lines after it are lost); how many invocations of the program it shows.
pub struct LogData<'a> {
    pub data: Vec<(usize, &'a str)>,
    pub truncated: bool,
    pub invokes: usize,
}

/// The walk keeps the invoke stack from "Program <id> invoke [d]" / "Program <id> success" / "Program <id> failed: ..."
/// lines; a data line belongs to the program on top. Only the first field of a data line is kept (emit! logs one).
pub fn program_data<'a>(logs: &'a [String], program: &str) -> LogData<'a> {
    let mut stack: Vec<Option<usize>> = Vec::new(); // per open invocation: its ordinal when it is `program`
    let mut out = LogData { data: Vec::new(), truncated: false, invokes: 0 };
    for line in logs {
        if line == "Log truncated" {
            out.truncated = true;
            return out;
        }
        if let Some(rest) = line.strip_prefix("Program data: ") {
            if let Some(Some(ord)) = stack.last() {
                if let Some(first) = rest.split(' ').next() {
                    out.data.push((*ord, first));
                }
            }
            continue;
        }
        let mut t = line.split(' ');
        if t.next() != Some("Program") {
            continue;
        }
        let (Some(id), Some(word)) = (t.next(), t.next()) else { continue };
        if !(32..=44).contains(&id.len()) {
            continue; // "Program log: ...", "Program return: ...", "Program consumption: ..."
        }
        if word == "invoke" {
            if id == program {
                stack.push(Some(out.invokes));
                out.invokes += 1;
            } else {
                stack.push(None);
            }
        } else if word == "success" || word.starts_with("failed") {
            stack.pop();
        }
    }
    out
}

/// Standard base64 (RFC 4648, padding optional); None on any other character.
pub fn base64_decode(s: &str) -> Option<Vec<u8>> {
    let val = |c: u8| -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => u32::from(c - b'A'),
            b'a'..=b'z' => u32::from(c - b'a') + 26,
            b'0'..=b'9' => u32::from(c - b'0') + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        })
    };
    let bytes = s.trim_end_matches('=').as_bytes();
    let mut out = Vec::with_capacity(bytes.len() * 3 / 4);
    let (mut acc, mut bits) = (0u32, 0u32);
    for &c in bytes {
        acc = ((acc << 6) | val(c)?) & 0xffff;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((acc >> bits) & 0xff) as u8);
        }
    }
    Some(out)
}

/// What an event row carries after its context: a body to decode with the IDL, or values built from balances.
enum Payload<'a> {
    Body(&'a [u8]),
    Balance(&'static str, Vec<Val>),
}

impl Venues {
    pub fn new() -> anyhow::Result<Self> {
        let mut programs = Vec::new();
        let older: [&[(&str, &str)]; 4] = [&DBC_OLDER_IDLS[..], &[], &[], &[]];
        let sources = [("meteora_dbc", DBC_IDL), ("meteora_damm_v2", DAMM_V2_IDL), ("raydium_launchlab", LAUNCHLAB_IDL), ("raydium_cpmm", CPMM_IDL)];
        for ((label, text), older) in sources.into_iter().zip(older) {
            let mut idls = vec![ProgramIdl::with_context(label, text, &VENUE_EVENT_CONTEXT)?];
            for (file, t) in older {
                let mut old = ProgramIdl::with_context(label, t, &VENUE_EVENT_CONTEXT)?;
                anyhow::ensure!(old.id == idls[0].id, "{file} is another program's IDL");
                // Only what the newer IDLs do not know: the newest layout of a name wins.
                old.tables.retain(|disc, _| idls.iter().all(|p| !p.tables.contains_key(disc)));
                idls.push(old);
            }
            let first = |names: &[&str], accounts: &[String]| accounts.iter().position(|a| names.contains(&a.as_str()));
            let mut ixs: HashMap<[u8; 8], VenueIx> = HashMap::new();
            for (n, p) in idls.iter().enumerate() {
                for ix in &p.idl.instructions {
                    if ixs.contains_key(&ix.discriminator) {
                        continue; // a newer IDL defines it
                    }
                    let name: &'static str = Box::leak(ix.name.clone().into_boxed_str());
                    let graduation = VENUE_GRADUATIONS.iter().find(|g| g.0 == label && g.1 == ix.name).map(|g| (g.2, g.3, g.4, g.5, g.6));
                    let layouts = VENUE_ACCOUNT_LAYOUTS
                        .iter()
                        .filter(|l| l.0 == label && l.1 == ix.name)
                        .map(|l| (l.2, l.4.iter().map(|a| (*a).to_owned()).collect()))
                        .collect();
                    ixs.insert(ix.discriminator, VenueIx {
                        idl: n,
                        name,
                        accounts: ix.accounts.clone(),
                        layouts,
                        trader: first(&VENUE_TRADER_ACCOUNTS[..], &ix.accounts[..]),
                        pool: first(&VENUE_POOL_ACCOUNTS[..], &ix.accounts[..]),
                        base: first(&VENUE_BASE_ACCOUNTS[..], &ix.accounts[..]),
                        quote: first(&VENUE_QUOTE_ACCOUNTS[..], &ix.accounts[..]),
                        graduation,
                        init: VENUE_INITS.iter().any(|g| g.0 == label && g.1 == ix.name),
                        balance_swap: VENUE_BALANCE_SWAPS.iter().find(|g| g.0 == label && g.1 == ix.name).map(|g| g.2),
                    });
                }
            }
            let log_events = VENUE_LOG_EVENTS.contains(&label);
            let fallback_event = if VENUE_BALANCE_SWAPS.iter().any(|g| g.0 == label) {
                let ev = idls[0].idl.events.iter().find(|e| e.name == "SwapEvent");
                Some(ev.ok_or_else(|| anyhow::anyhow!("{label}: the IDL has no SwapEvent for the balance fallback"))?.discriminator)
            } else {
                None
            };
            let address = idls[0].idl.address.clone();
            programs.push(VenueProgram { label, idls, ixs, log_events, address, fallback_event });
        }
        Ok(Self {
            programs,
            grad_cols: cols(&VENUE_GRADUATION_COLS),
            grad_table: Arc::from("venue_graduations"),
            init_cols: cols(&VENUE_INIT_COLS),
            init_table: Arc::from("venue_inits"),
            unknown_cols: cols(&VENUE_UNKNOWN_COLS),
            unknown_table: Arc::from("venue_unknown_events"),
        })
    }

    /// On with LAB_VENUES=1 only.
    pub fn from_env() -> anyhow::Result<Option<Self>> {
        if std::env::var("LAB_VENUES").ok().as_deref() != Some("1") {
            return Ok(None);
        }
        Self::new().map(Some)
    }

    /// The program labels and ids (for the manifest).
    pub fn ids(&self) -> Vec<(&'static str, String)> {
        self.programs.iter().map(|p| (p.label, p.idls[0].idl.address.clone())).collect()
    }

    /// The venue rows of one transaction, in execution order:
    /// - an event row per emit_cpi self-CPI (DBC, DAMM v2, LaunchLab) and per emit! log line (CPMM), or an unknown-event
    ///   row; a failed transaction's events, recorded before its failure, are kept, flagged failed;
    /// - for a CPMM swap of a successful transaction without its logged event, a fallback row from its vaults' balances;
    /// - a graduation row per graduation instruction and an init row per pool creation without an event, of a successful
    ///   transaction.
    pub fn rows(&self, v: &TxView) -> VenueRows {
        let b58 = |b: &[u8]| bs58::encode(b).into_string();
        let mut out = VenueRows::default();
        let split = |path: &str| -> (u64, Option<u64>) {
            let mut it = path.split('.');
            let outer = it.next().and_then(|x| x.parse().ok()).unwrap_or(0);
            (outer, it.next().and_then(|x| x.parse().ok()))
        };
        let key_of = |accounts: &[u8], pos: Option<usize>| -> Val {
            pos.and_then(|p| accounts.get(p)).and_then(|&a| v.keys.get(usize::from(a))).map_or(Val::Null, |k| Val::Str(b58(k)))
        };
        let outer_program = |outer: u64| -> Val {
            v.ixs.iter().find(|x| x.path == outer.to_string()).and_then(|x| x.program).map_or(Val::Null, |k| Val::Str(b58(k)))
        };
        let disc_of = |d: &[u8]| -> Option<[u8; 8]> { d.get(..8).and_then(|x| <[u8; 8]>::try_from(x).ok()) };
        let hex8 = |d: &[u8]| Val::Str(hex::encode(&d[..d.len().min(8)]));
        // One event's row: decoded (or built from balances), or an unknown-event row (false). The sequence columns are
        // set once the transaction's events are in execution order.
        let event_row = |vp: &VenueProgram, parent: Option<&IxView>, outer: u64, inner: Option<u64>, disc: [u8; 8], payload: Payload, source: &str| -> (bool, Row) {
            let known = parent.and_then(|p| disc_of(p.data).and_then(|d| vp.ixs.get(&d)).map(|x| (p, x)));
            let (name, args) = match known.and_then(|(p, x)| vp.idls[x.idl].idl.decode_instruction(p.data)) {
                Some((name, args)) => (Val::Str(name.to_owned()), Val::Str(args)),
                None => (Val::Null, Val::Null),
            };
            let (p_inner, p_disc) = match parent {
                Some(p) => (split(&p.path).1.map_or(Val::Null, Val::U64), hex8(p.data)),
                None => (Val::Null, Val::Null),
            };
            let body_len = match &payload {
                Payload::Body(b) => Val::U64(b.len() as u64),
                Payload::Balance(..) => Val::Null,
            };
            let Some((pidl, (ev_idx, table, columns))) = vp.event(&disc) else {
                let values = vec![
                    Val::U64(v.slot),
                    Val::U64(v.tx_index),
                    Val::U64(outer),
                    inner.map_or(Val::Null, Val::U64),
                    Val::Null,
                    Val::Str(vp.label.to_owned()),
                    Val::Str(source.to_owned()),
                    Val::Str(hex::encode(disc)),
                    body_len,
                    name,
                    p_disc,
                ];
                return (false, Row { table: self.unknown_table.clone(), columns: self.unknown_cols.clone(), values });
            };
            let pick = |f: fn(&VenueIx) -> Option<usize>| known.map_or(Val::Null, |(p, x)| key_of(p.accounts, f(x)));
            let (status, fields) = match payload {
                Payload::Body(body) => {
                    let decoded = pidl.idl.decode_event(&pidl.idl.events[*ev_idx], body);
                    (decoded.status.as_str(), decoded.values)
                }
                Payload::Balance(status, values) => (status, values),
            };
            let mut values = Vec::with_capacity(columns.len());
            values.extend([
                Val::U64(v.slot),
                Val::U64(v.tx_index),
                Val::Bool(v.failed),
                Val::U64(outer),
                inner.map_or(Val::Null, Val::U64),
                Val::Null,
                Val::Null,
                Val::Str(v.fee_payer.clone()),
                outer_program(outer),
                p_inner,
                p_disc,
                name,
                args,
                Val::Str(status.to_owned()),
                Val::Str(source.to_owned()),
                body_len,
                pick(|x| x.trader),
                pick(|x| x.pool),
                pick(|x| x.base),
                pick(|x| x.quote),
            ]);
            values.extend(fields);
            (true, Row { table: table.clone(), columns: columns.clone(), values })
        };
        // ((instruction index, order within it), decoded, row): sorted into execution order below.
        let mut events: Vec<((usize, usize), bool, Row)> = Vec::new();
        for (n, ix) in v.ixs.iter().enumerate() {
            let Some(program) = ix.program else { continue };
            let Some(vp) = self.programs.iter().find(|p| p.id() == program) else { continue };
            let (outer, inner) = split(&ix.path);
            if !vp.log_events && ix.data.len() >= 16 && ix.data[..8] == EVENT_IX_TAG {
                let disc: [u8; 8] = ix.data[8..16].try_into().unwrap();
                // The parent: the nearest earlier instruction of this program in the same top-level group, no event.
                let parent = v.ixs[..n]
                    .iter()
                    .rev()
                    .take_while(|p| split(&p.path).0 == outer)
                    .find(|p| p.program == Some(program) && !p.data.starts_with(&EVENT_IX_TAG));
                let (decoded, row) = event_row(vp, parent, outer, inner, disc, Payload::Body(&ix.data[16..]), "cpi");
                events.push(((n, 0), decoded, row));
                continue;
            }
            let Some(x) = disc_of(ix.data).and_then(|d| vp.ixs.get(&d)) else { continue };
            if v.failed {
                continue;
            }
            if let Some((from, to, base, quote, to_venue)) = x.graduation {
                out.graduations += 1;
                out.rows.push(Row {
                    table: self.grad_table.clone(),
                    columns: self.grad_cols.clone(),
                    values: vec![
                        Val::U64(v.slot),
                        Val::U64(v.tx_index),
                        Val::U64(outer),
                        inner.map_or(Val::Null, Val::U64),
                        Val::Str(v.fee_payer.clone()),
                        Val::Str(vp.label.to_owned()),
                        Val::Str(x.name.to_owned()),
                        key_of(ix.accounts, x.at_n(from, ix.accounts.len())),
                        Val::Str(to_venue.to_owned()),
                        key_of(ix.accounts, x.at_n(to, ix.accounts.len())),
                        key_of(ix.accounts, x.at_n(base, ix.accounts.len())),
                        key_of(ix.accounts, x.at_n(quote, ix.accounts.len())),
                    ],
                });
            } else if x.init {
                out.inits += 1;
                let args = vp.idls[x.idl].idl.decode_instruction(ix.data).map_or(Val::Null, |(_, a)| Val::Str(a));
                out.rows.push(Row {
                    table: self.init_table.clone(),
                    columns: self.init_cols.clone(),
                    values: vec![
                        Val::U64(v.slot),
                        Val::U64(v.tx_index),
                        Val::U64(outer),
                        inner.map_or(Val::Null, Val::U64),
                        Val::Str(v.fee_payer.clone()),
                        Val::Str(vp.label.to_owned()),
                        Val::Str(x.name.to_owned()),
                        key_of(ix.accounts, x.at("pool_state")),
                        key_of(ix.accounts, x.at("creator")),
                        key_of(ix.accounts, x.at("token_0_mint")),
                        key_of(ix.accounts, x.at("token_1_mint")),
                        key_of(ix.accounts, x.at("token_0_vault")),
                        key_of(ix.accounts, x.at("token_1_vault")),
                        key_of(ix.accounts, x.at("lp_mint")),
                        args,
                    ],
                });
            }
        }
        // emit! venues: their own "Program data:" lines, paired with their instructions in execution order (the k-th
        // logged invocation is the k-th instruction of the program: top level i, then its inner i.j).
        for vp in self.programs.iter().filter(|p| p.log_events) {
            let ids: Vec<usize> = v.ixs.iter().enumerate().filter(|(_, x)| x.program == Some(vp.id())).map(|(n, _)| n).collect();
            if ids.is_empty() {
                continue;
            }
            let logs = program_data(v.logs, &vp.address);
            out.log_truncated += u64::from(logs.truncated);
            // A truncated log pairs its logged prefix, a failed transaction the invocations that ran; a successful one
            // must show every instruction's invocation, else nothing pairs (its swaps fall back to balances).
            let paired = logs.truncated || v.failed || logs.invokes == ids.len();
            out.log_unpaired += u64::from(!paired);
            let mut with_event: std::collections::HashSet<usize> = std::collections::HashSet::new();
            if paired {
                for (k, (ord, text)) in logs.data.iter().enumerate() {
                    let Some(&n) = ids.get(*ord) else { continue };
                    let Some(bytes) = base64_decode(text).filter(|b| b.len() >= 8) else {
                        out.log_undecodable += 1;
                        continue;
                    };
                    let p = &v.ixs[n];
                    let (outer, inner) = split(&p.path);
                    let disc: [u8; 8] = bytes[..8].try_into().unwrap();
                    let (decoded, row) = event_row(vp, Some(p), outer, inner, disc, Payload::Body(&bytes[8..]), "log");
                    with_event.insert(n);
                    events.push(((n, k + 1), decoded, row));
                }
            }
            // The balance fallback: a swap of a successful transaction without its logged event (a failed transaction
            // changed no balance).
            let Some(fallback) = vp.fallback_event.filter(|_| !v.failed) else { continue };
            let Some((_, (_, _, columns))) = vp.event(&fallback) else { continue };
            let ix_of = |n: usize| disc_of(v.ixs[n].data).and_then(|d| vp.ixs.get(&d));
            for &n in &ids {
                let p = &v.ixs[n];
                let Some(x) = ix_of(n) else { continue };
                let Some(base_input) = x.balance_swap else { continue };
                if with_event.contains(&n) {
                    continue;
                }
                let acct = |name: &str| x.at(name).and_then(|i| p.accounts.get(i)).copied();
                let pool = acct("pool_state");
                // Another instruction of the program on the same pool in this transaction: the vault deltas are shared. One
                // the pinned IDL does not know (collect_creator_fee_permissionless is newer) may move the vaults as well.
                let shared = ids.iter().any(|&m| {
                    m != n && ix_of(m).map_or(true, |y| y.at("pool_state").and_then(|i| v.ixs[m].accounts.get(i)).copied() == pool)
                });
                let bal = |list: &[Bal], a: Option<u8>| a.and_then(|a| list.iter().find(|b| b.idx == a)).and_then(|b| u64::try_from(b.amount).ok());
                let (in_vault, out_vault) = (acct("input_vault"), acct("output_vault"));
                let (in_pre, in_post) = (bal(&v.pre_tokens, in_vault), bal(&v.post_tokens, in_vault));
                let (out_pre, out_post) = (bal(&v.pre_tokens, out_vault), bal(&v.post_tokens, out_vault));
                let status = if shared {
                    "balance_ambiguous"
                } else if in_pre.is_none() || in_post.is_none() || out_pre.is_none() || out_post.is_none() {
                    "balance_missing"
                } else {
                    "balance"
                };
                let exact = status == "balance";
                let amount = |a: Option<u64>, b: Option<u64>| match (a, b) {
                    (Some(a), Some(b)) if exact && b >= a => Val::U64(b - a),
                    _ => Val::Null,
                };
                let opt = |x: Option<u64>| if exact { x.map_or(Val::Null, Val::U64) } else { Val::Null };
                let fields: Vec<Val> = columns[VENUE_EVENT_CONTEXT.len()..]
                    .iter()
                    .map(|(name, _)| match name.as_str() {
                        "pool_id" => key_of(p.accounts, x.at("pool_state")),
                        "input_vault_before" => opt(in_pre),
                        "output_vault_before" => opt(out_pre),
                        "input_amount" => amount(in_pre, in_post),
                        "output_amount" => amount(out_post, out_pre),
                        "base_input" => Val::Bool(base_input),
                        "input_mint" => key_of(p.accounts, x.at("input_token_mint")),
                        "output_mint" => key_of(p.accounts, x.at("output_token_mint")),
                        _ => Val::Null,
                    })
                    .collect();
                let (outer, inner) = split(&p.path);
                let (decoded, row) = event_row(vp, Some(p), outer, inner, fallback, Payload::Balance(status, fields), "balance");
                out.balance_rows += 1;
                events.push(((n, 0), decoded, row));
            }
        }
        events.sort_by_key(|e| e.0);
        let (mut seq, mut seq_all) = (0u64, 0u64);
        for (_, decoded, mut row) in events {
            if decoded {
                row.values[SEQ_COL] = Val::U64(seq);
                row.values[SEQ_ALL_COL] = Val::U64(seq_all);
                seq += 1;
                out.events += 1;
            } else {
                row.values[UNKNOWN_SEQ_ALL_COL] = Val::U64(seq_all);
                out.unknown_events += 1;
            }
            seq_all += 1;
            out.rows.push(row);
        }
        out
    }
}

#[cfg(test)]
mod venue_tests {
    use super::*;

    const CPMM: &str = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C";
    const JUP: &str = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4";
    const TOKEN: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
    const WSOL_MINT: &str = "So11111111111111111111111111111111111111112";

    fn lines(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| (*s).to_owned()).collect()
    }

    fn pk(n: u8) -> [u8; 32] {
        let mut k = [n; 32];
        k[0] = 1; // any 32 bytes are a valid base58 key
        k
    }

    fn k58(k: &[u8]) -> String {
        bs58::encode(k).into_string()
    }

    fn b64(bytes: &[u8]) -> String {
        const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut s = String::new();
        for c in bytes.chunks(3) {
            let n = (u32::from(c[0]) << 16) | (u32::from(*c.get(1).unwrap_or(&0)) << 8) | u32::from(*c.get(2).unwrap_or(&0));
            for i in 0..4 {
                if i <= c.len() {
                    s.push(char::from(A[((n >> (18 - 6 * i)) & 63) as usize]));
                } else {
                    s.push('=');
                }
            }
        }
        s
    }

    fn col<'r>(r: &'r Row, name: &str) -> &'r Val {
        let i = r.columns.iter().position(|(n, _)| n == name).unwrap_or_else(|| panic!("no column {name}"));
        &r.values[i]
    }

    fn st(s: &str) -> Val {
        Val::Str(s.to_owned())
    }

    fn ix_disc(name: &str) -> [u8; 8] {
        Idl::parse(CPMM_IDL).unwrap().instructions.iter().find(|i| i.name == name).unwrap().discriminator
    }

    fn swap_event(pool: &[u8; 32], in_before: u64, out_before: u64, amount_in: u64, amount_out: u64, base_input: bool, mints: (&[u8; 32], &[u8; 32])) -> Vec<u8> {
        let ev = Idl::parse(CPMM_IDL).unwrap().events.into_iter().find(|e| e.name == "SwapEvent").unwrap();
        let mut b = ev.discriminator.to_vec();
        b.extend(pool);
        for x in [in_before, out_before, amount_in, amount_out, 0, 0] {
            b.extend(x.to_le_bytes());
        }
        b.push(u8::from(base_input));
        b.extend(mints.0);
        b.extend(mints.1);
        b.extend(2_500u64.to_le_bytes()); // trade_fee
        b.extend(0u64.to_le_bytes()); // creator_fee
        b.push(0); // creator_fee_on_input
        b
    }

    // keys: payer 0, cpmm 1, pool 2, input vault 3, output vault 4, WSOL 5, token 6, jupiter 7, token program 8, config 9
    struct Fixture {
        keys: Vec<[u8; 32]>,
        swap_accounts: Vec<u8>,
    }

    fn fixture() -> Fixture {
        let keys = vec![pk(10), key(CPMM), pk(12), pk(13), pk(14), key(WSOL_MINT), pk(16), key(JUP), key(TOKEN), pk(19)];
        // swap accounts: payer, authority, amm_config, pool_state, input/output token account, input/output vault,
        // input/output token program, input/output mint, observation_state
        Fixture { keys, swap_accounts: vec![0, 9, 9, 2, 0, 0, 3, 4, 8, 8, 5, 6, 9] }
    }

    fn view<'a>(f: &'a Fixture, ixs: Vec<IxView<'a>>, logs: &'a [String], failed: bool, pre: Vec<Bal<'a>>, post: Vec<Bal<'a>>) -> TxView<'a> {
        TxView {
            slot: 9,
            tx_index: 4,
            fee_payer: k58(&f.keys[0]),
            failed,
            keys: f.keys.iter().map(|k| &k[..]).collect(),
            ixs,
            logs,
            pre_lamports: &[],
            post_lamports: &[],
            pre_tokens: pre,
            post_tokens: post,
        }
    }

    #[test]
    fn data_lines_belong_to_the_invocation_on_top() {
        let logs = lines(&[
            "Program ComputeBudget111111111111111111111111111111 invoke [1]",
            "Program ComputeBudget111111111111111111111111111111 success",
            &format!("Program {JUP} invoke [1]"),
            "Program log: Instruction: Route",
            &format!("Program {CPMM} invoke [2]"),
            "Program log: Instruction: SwapBaseInput",
            &format!("Program {TOKEN} invoke [3]"),
            &format!("Program {TOKEN} consumed 6200 of 180000 compute units"),
            &format!("Program {TOKEN} success"),
            "Program data: QUFB",
            &format!("Program {CPMM} consumed 30000 of 170000 compute units"),
            &format!("Program {CPMM} success"),
            "Program data: SlVQ",
            &format!("Program {CPMM} invoke [2]"),
            "Program data: QkJC b3RoZXI=",
            &format!("Program {CPMM} failed: custom program error: 0x1"),
            &format!("Program {JUP} success"),
        ]);
        let d = program_data(&logs, CPMM);
        // The first CPMM invocation's data, not Jupiter's own line, then the second invocation's first field.
        assert_eq!((d.data, d.truncated, d.invokes), (vec![(0, "QUFB"), (1, "QkJC")], false, 2));
        assert_eq!(program_data(&logs, JUP).data, vec![(0, "SlVQ")]);
        let truncated = lines(&[&format!("Program {CPMM} invoke [1]"), "Program data: QUFB", "Log truncated"]);
        let t = program_data(&truncated, CPMM);
        assert_eq!((t.data, t.truncated, t.invokes), (vec![(0, "QUFB")], true, 1));
    }

    #[test]
    fn base64_round_trips() {
        assert_eq!(base64_decode("QUFB"), Some(b"AAA".to_vec()));
        assert_eq!(base64_decode("b3RoZXI="), Some(b"other".to_vec()));
        assert_eq!(base64_decode("AQID/w=="), Some(vec![1, 2, 3, 255]));
        assert_eq!(base64_decode("+/8="), Some(vec![251, 255]));
        assert_eq!(base64_decode("not base64!"), None);
        assert_eq!(base64_decode(""), Some(vec![]));
        let long: Vec<u8> = (0..=255u8).collect();
        assert_eq!(base64_decode(&b64(&long)), Some(long));
    }

    #[test]
    fn cpmm_swap_events_come_from_the_log_in_execution_order() {
        let f = fixture();
        let (cpmm, jup) = (&f.keys[1][..], &f.keys[7][..]);
        let (sbi, sbo) = (ix_disc("swap_base_input"), ix_disc("swap_base_output"));
        let data_in: Vec<u8> = sbi.iter().copied().chain(1_000u64.to_le_bytes()).chain(1u64.to_le_bytes()).collect();
        let data_out: Vec<u8> = sbo.iter().copied().chain(9_000u64.to_le_bytes()).chain(500u64.to_le_bytes()).collect();
        let ixs = || vec![
            IxView { path: "0".into(), program: Some(jup), accounts: &[0][..], data: &[1][..], parent: None },
            IxView { path: "0.0".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data_in[..], parent: Some(jup) },
            IxView { path: "0.1".into(), program: Some(&f.keys[8][..]), accounts: &[3][..], data: &[3][..], parent: Some(cpmm) },
            IxView { path: "1".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data_out[..], parent: None },
        ];
        let (a, b) = (
            swap_event(&f.keys[2], 100, 200, 1_000, 1_900, true, (&f.keys[5], &f.keys[6])),
            swap_event(&f.keys[2], 1_100, 0, 480, 500, false, (&f.keys[5], &f.keys[6])),
        );
        let logs = lines(&[
            &format!("Program {JUP} invoke [1]"),
            &format!("Program {CPMM} invoke [2]"),
            &format!("Program {TOKEN} invoke [3]"),
            &format!("Program {TOKEN} success"),
            &format!("Program data: {}", b64(&a)),
            &format!("Program {CPMM} success"),
            &format!("Program {JUP} success"),
            &format!("Program {CPMM} invoke [1]"),
            &format!("Program data: {}", b64(&b)),
            &format!("Program {CPMM} success"),
        ]);
        let out = Venues::new().unwrap().rows(&view(&f, ixs(), &logs, false, vec![], vec![]));
        assert_eq!((out.events, out.balance_rows, out.log_truncated, out.log_unpaired, out.unknown_events), (2, 0, 0, 0, 0));
        let r: Vec<&Row> = out.rows.iter().filter(|r| &*r.table == "raydium_cpmm/SwapEvent").collect();
        assert_eq!(r.len(), 2);
        let got: Vec<Vec<Val>> = r.iter().map(|r| ["outer_ix", "inner_ix", "event_seq", "event_seq_all", "parent_ix_name", "source", "decode_status", "input_amount", "output_amount", "base_input", "ix_pool", "ix_trader"]
            .iter().map(|c| col(r, c).clone()).collect()).collect();
        assert_eq!(got, vec![
            vec![Val::U64(0), Val::U64(0), Val::U64(0), Val::U64(0), st("swap_base_input"), st("log"), st("ok"), Val::U64(1_000), Val::U64(1_900), Val::Bool(true), st(&k58(&f.keys[2])), st(&k58(&f.keys[0]))],
            vec![Val::U64(1), Val::Null, Val::U64(1), Val::U64(1), st("swap_base_output"), st("log"), st("ok"), Val::U64(480), Val::U64(500), Val::Bool(false), st(&k58(&f.keys[2])), st(&k58(&f.keys[0]))],
        ]);
        assert_eq!(col(r[0], "outer_program"), &st(JUP));
        assert_eq!(col(r[0], "parent_inner_ix"), &Val::U64(0));
        assert_eq!(col(r[0], "parent_ix_disc"), &st(&hex::encode(sbi)));
        assert_eq!((col(r[0], "input_mint"), col(r[0], "trade_fee")), (&st(WSOL_MINT), &Val::U64(2_500)));
    }

    #[test]
    fn a_truncated_log_falls_back_to_the_vault_balances() {
        let f = fixture();
        let cpmm = &f.keys[1][..];
        let sbi = ix_disc("swap_base_input");
        let data: Vec<u8> = sbi.iter().copied().chain(600u64.to_le_bytes()).chain(1u64.to_le_bytes()).collect();
        let bal = |idx: u8, amount: u128| Bal { idx, mint: "m", owner: "o", amount, decimals: 6 };
        let logs = lines(&[&format!("Program {CPMM} invoke [1]"), "Log truncated"]);
        let one = vec![IxView { path: "0".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data[..], parent: None }];
        let venues = Venues::new().unwrap();
        let out = venues.rows(&view(&f, one, &logs, false, vec![bal(3, 1_000), bal(4, 5_000)], vec![bal(3, 1_600), bal(4, 4_100)]));
        assert_eq!((out.events, out.balance_rows, out.log_truncated), (1, 1, 1));
        let r = &out.rows[0];
        assert_eq!(&*r.table, "raydium_cpmm/SwapEvent");
        let got: Vec<Val> = ["source", "decode_status", "payload_len", "input_amount", "output_amount", "input_vault_before", "output_vault_before", "base_input", "pool_id", "output_mint", "trade_fee", "parent_ix_name"]
            .iter().map(|c| col(r, c).clone()).collect();
        assert_eq!(got, vec![st("balance"), st("balance"), Val::Null, Val::U64(600), Val::U64(900), Val::U64(1_000), Val::U64(5_000), Val::Bool(true),
                             st(&k58(&f.keys[2])), st(&k58(&f.keys[6])), Val::Null, st("swap_base_input")]);
        // Two swaps on the same pool share the vault deltas: no amounts.
        let two = vec![
            IxView { path: "0".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data[..], parent: None },
            IxView { path: "1".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data[..], parent: None },
        ];
        let out = venues.rows(&view(&f, two, &logs, false, vec![bal(3, 1_000), bal(4, 5_000)], vec![bal(3, 2_200), bal(4, 3_000)]));
        assert_eq!(out.balance_rows, 2);
        assert!(out.rows.iter().all(|r| col(r, "decode_status") == &st("balance_ambiguous") && col(r, "input_amount") == &Val::Null));
        // A CPMM instruction the pinned IDL does not know may move the vaults too: no amounts.
        let unknown_ix = [7u8; 8];
        let with_unknown = vec![
            IxView { path: "0".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data[..], parent: None },
            IxView { path: "1".into(), program: Some(cpmm), accounts: &[2, 3, 4][..], data: &unknown_ix[..], parent: None },
        ];
        let out = venues.rows(&view(&f, with_unknown, &logs, false, vec![bal(3, 1_000), bal(4, 5_000)], vec![bal(3, 1_600), bal(4, 4_000)]));
        assert_eq!((out.balance_rows, col(&out.rows[0], "decode_status")), (1, &st("balance_ambiguous")));
        // A vault without a balance row: no amounts.
        let one = vec![IxView { path: "0".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data[..], parent: None }];
        let out = venues.rows(&view(&f, one, &logs, false, vec![bal(3, 1_000)], vec![bal(3, 1_600)]));
        assert_eq!(col(&out.rows[0], "decode_status"), &st("balance_missing"));
        // A failed transaction changed no balance: no fallback.
        let one = vec![IxView { path: "0".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data[..], parent: None }];
        let out = venues.rows(&view(&f, one, &logs, true, vec![], vec![]));
        assert_eq!((out.rows.len(), out.balance_rows), (0, 0));
        // A successful transaction whose log shows no invocation at all does not pair: every swap falls back.
        let one = vec![IxView { path: "0".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data[..], parent: None }];
        let out = venues.rows(&view(&f, one, &[], false, vec![bal(3, 1_000), bal(4, 5_000)], vec![bal(3, 1_600), bal(4, 4_100)]));
        assert_eq!((out.log_unpaired, out.balance_rows), (1, 1));
    }

    #[test]
    fn unknown_and_undecodable_log_lines_are_counted_and_seq_all_counts_unknown_events() {
        let f = fixture();
        let cpmm = &f.keys[1][..];
        let sbi = ix_disc("swap_base_input");
        let data: Vec<u8> = sbi.iter().copied().chain(600u64.to_le_bytes()).chain(1u64.to_le_bytes()).collect();
        let unknown: Vec<u8> = [9u8; 8].into_iter().chain([1u8, 2, 3]).collect();
        let ev = swap_event(&f.keys[2], 1, 2, 3, 4, true, (&f.keys[5], &f.keys[6]));
        let logs = lines(&[
            &format!("Program {CPMM} invoke [1]"),
            &format!("Program data: {}", b64(&unknown)),
            "Program data: !!",
            &format!("Program data: {}", b64(&ev)),
            &format!("Program {CPMM} success"),
        ]);
        let ixs = vec![IxView { path: "0".into(), program: Some(cpmm), accounts: &f.swap_accounts[..], data: &data[..], parent: None }];
        let out = Venues::new().unwrap().rows(&view(&f, ixs, &logs, false, vec![], vec![]));
        assert_eq!((out.events, out.unknown_events, out.log_undecodable, out.balance_rows), (1, 1, 1, 0));
        let unk = out.rows.iter().find(|r| &*r.table == "venue_unknown_events").unwrap();
        assert_eq!((col(unk, "venue"), col(unk, "source"), col(unk, "event_disc"), col(unk, "event_seq_all"), col(unk, "parent_ix_name")),
                   (&st("raydium_cpmm"), &st("log"), &st("0909090909090909"), &Val::U64(0), &st("swap_base_input")));
        let sw = out.rows.iter().find(|r| &*r.table == "raydium_cpmm/SwapEvent").unwrap();
        assert_eq!((col(sw, "event_seq"), col(sw, "event_seq_all")), (&Val::U64(0), &Val::U64(1)));
    }

    #[test]
    fn launchlab_migrate_to_amm_reads_its_newer_layout_by_account_count() {
        // Since LaunchLab's 2026-09-09 upgrade migrate_to_amm takes 23 accounts (pool_state 14, amm_pool 5); before, 32
        // (pool_state 23, amm_pool 13). base_mint 1 and quote_mint 2 in both.
        let disc = Idl::parse(LAUNCHLAB_IDL).unwrap().instructions.iter().find(|i| i.name == "migrate_to_amm").unwrap().discriminator;
        let mut keys: Vec<[u8; 32]> = vec![pk(1), key("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj")];
        keys.extend((0..32u8).map(|i| pk(100 + i)));
        let venues = Venues::new().unwrap();
        for (n, from, to) in [(23usize, 14usize, 5usize), (32, 23, 13)] {
            let accounts: Vec<u8> = (0..n as u8).map(|i| i + 2).collect();
            let ixs = vec![IxView { path: "0".into(), program: Some(&keys[1][..]), accounts: &accounts[..], data: &disc[..], parent: None }];
            let v = TxView { slot: 1, tx_index: 0, fee_payer: k58(&keys[0]), failed: false, keys: keys.iter().map(|k| &k[..]).collect(), ixs,
                             logs: &[], pre_lamports: &[], post_lamports: &[], pre_tokens: vec![], post_tokens: vec![] };
            let out = venues.rows(&v);
            assert_eq!(out.graduations, 1, "{n} accounts");
            let r = &out.rows[0];
            assert_eq!((col(r, "from_pool"), col(r, "to_pool"), col(r, "base_mint"), col(r, "quote_mint")),
                       (&st(&k58(&keys[from + 2])), &st(&k58(&keys[to + 2])), &st(&k58(&keys[3])), &st(&k58(&keys[4]))), "{n} accounts");
        }
        assert_eq!((layout_pool("raydium_launchlab", "migrate_to_amm", 23), layout_pool("raydium_launchlab", "migrate_to_amm", 32)), (Some(14), None));
    }

    #[test]
    fn a_cpmm_pool_creation_gets_its_init_row() {
        let f = fixture();
        let cpmm = &f.keys[1][..];
        let init = ix_disc("initialize");
        let data: Vec<u8> = init.iter().copied().chain(5u64.to_le_bytes()).chain(7u64.to_le_bytes()).chain(0u64.to_le_bytes()).collect();
        // initialize: creator 0, amm_config 1, authority 2, pool_state 3, token_0_mint 4, token_1_mint 5, lp_mint 6, ...,
        // token_0_vault 10, token_1_vault 11 (20 accounts)
        let mut accounts = [9u8; 20];
        (accounts[0], accounts[3], accounts[4], accounts[5], accounts[6], accounts[10], accounts[11]) = (0, 2, 5, 6, 9, 3, 4);
        let ixs = || vec![IxView { path: "0".into(), program: Some(cpmm), accounts: &accounts[..], data: &data[..], parent: None }];
        let venues = Venues::new().unwrap();
        let out = venues.rows(&view(&f, ixs(), &lines(&[&format!("Program {CPMM} invoke [1]"), &format!("Program {CPMM} success")]), false, vec![], vec![]));
        assert_eq!(out.inits, 1);
        let r = &out.rows[0];
        let got: Vec<Val> = ["venue", "ix_name", "pool", "creator", "token_0_mint", "token_1_mint", "token_0_vault", "token_1_vault", "outer_ix", "inner_ix"]
            .iter().map(|c| col(r, c).clone()).collect();
        assert_eq!(got, vec![st("raydium_cpmm"), st("initialize"), st(&k58(&f.keys[2])), st(&k58(&f.keys[0])), st(WSOL_MINT), st(&k58(&f.keys[6])),
                             st(&k58(&f.keys[3])), st(&k58(&f.keys[4])), Val::U64(0), Val::Null]);
        assert!(matches!(col(r, "ix_args"), Val::Str(s) if s.contains("init_amount_0")));
        // A failed transaction creates nothing.
        assert_eq!(venues.rows(&view(&f, ixs(), &[], true, vec![], vec![])).inits, 0);
    }
}
// ----------------------------------------------------------------------------- end of venue decoders

/// Outside the venue section (it reads the firehose's transaction, not the view).
impl Venues {
    /// Does the transaction invoke a venue program (top level or inner)? Cheap: no view is built for the others.
    pub fn invokes(&self, tx: &TransactionData) -> bool {
        let msg = &tx.transaction.message;
        let meta = &tx.transaction_status_meta;
        let statics = msg.static_account_keys();
        let (w, r) = (&meta.loaded_addresses.writable, &meta.loaded_addresses.readonly);
        let is_venue = |i: u8| -> bool {
            let i = usize::from(i);
            let k: Option<&[u8]> = if i < statics.len() {
                Some(statics[i].as_ref())
            } else if i - statics.len() < w.len() {
                Some(w[i - statics.len()].as_ref())
            } else {
                r.get(i - statics.len() - w.len()).map(AsRef::as_ref)
            };
            k.is_some_and(|k| self.programs.iter().any(|p| p.id() == k))
        };
        msg.instructions().iter().any(|ix| is_venue(ix.program_id_index))
            || meta.inner_instructions.as_deref().unwrap_or(&[]).iter().any(|g| g.instructions.iter().any(|ix| is_venue(ix.instruction.program_id_index)))
    }
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
    /// venue_census rows (LAB_VENUE_CENSUS=1).
    pub census_rows: AtomicU64,
    /// Venue events, graduation rows and events with a discriminator the venue IDLs do not know (LAB_VENUES=1).
    pub venue_events: AtomicU64,
    pub venue_graduations: AtomicU64,
    pub venue_inits: AtomicU64,
    pub venue_unknown_events: AtomicU64,
    pub venue_log_truncated: AtomicU64,
    pub venue_log_unpaired: AtomicU64,
    pub venue_log_undecodable: AtomicU64,
    pub venue_balance_rows: AtomicU64,
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
    /// token_deltas, token_transfers and sol_funding (LAB_TRANSFERS=1; the extractor only).
    pub transfers: Option<Arc<Transfers>>,
    /// venue_census (LAB_VENUE_CENSUS=1; the extractor only).
    pub census: Option<Arc<Census>>,
    /// The venue decoders (LAB_VENUES=1; the extractor only).
    pub venues: Option<Arc<Venues>>,
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
        Self::with_context(short, idl_text, &EVENT_CONTEXT)
    }

    /// The event tables with `context` columns before the IDL fields (the venue decoders use a leaner context).
    fn with_context(short: &'static str, idl_text: &str, context: &[(&str, Kind)]) -> anyhow::Result<Self> {
        let idl = Idl::parse(idl_text)?;
        let id = key(&idl.address);
        let mut tables = HashMap::new();
        for (i, ev) in idl.events.iter().enumerate() {
            let mut c: Vec<(String, Kind)> = context.iter().map(|(n, k)| ((*n).to_owned(), *k)).collect();
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
    /// The extractor's plugin: the extractor-only tables follow the environment (LAB_TRANSFERS, LAB_VENUE_CENSUS).
    pub fn new(sink: Arc<Sink>, coverage: Arc<Coverage>) -> anyhow::Result<Self> {
        Self::build(sink, coverage, Transfers::from_env()?.map(Arc::new), Census::from_env().map(Arc::new), Venues::from_env()?.map(Arc::new))
    }

    /// The recorder's plugin (LAB review on #500): the extractor-only tables (token transfers, the venue census, and
    /// any later extractor-only table) are off whatever the environment says, and the environment is not read for
    /// them, so an exported LAB_* in the recorder's shell never puts them into live segments.
    pub fn for_recorder(sink: Arc<Sink>, coverage: Arc<Coverage>) -> anyhow::Result<Self> {
        Self::build(sink, coverage, None, None, None)
    }

    fn build(
        sink: Arc<Sink>,
        coverage: Arc<Coverage>,
        transfers: Option<Arc<Transfers>>,
        census: Option<Arc<Census>>,
        venues: Option<Arc<Venues>>,
    ) -> anyhow::Result<Self> {
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
            transfers,
            census,
            venues,
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

    /// Extract all rows for one transaction. Public for tests. With LAB_TRANSFERS=1 every transaction
    /// is also read for the token transfer tables (a plain token transfer or a wallet's funding does
    /// not touch pump.fun); the other tables are exactly as without them. With LAB_VENUE_CENSUS=1 a
    /// transaction that invokes a DEX program also gets its venue_census row (the other tables unchanged).
    /// With LAB_VENUES=1 the events and graduations of DBC, DAMM v2 and LaunchLab get their tables (the others unchanged).
    pub fn rows_for(&self, tx: &TransactionData) -> Vec<Row> {
        let mut rows = self.core_rows(tx);
        if let Some(t) = &self.transfers {
            let extra = t.rows(&tx_view(tx), &rows, &self.labels);
            rows.extend(extra);
        }
        // The census and the venue decoders read the same view of the transaction: built once, only when needed.
        let census = self.census.as_ref().filter(|c| c.invokes_dex(tx, &self.labels));
        let venues = self.venues.as_ref().filter(|v| v.invokes(tx));
        if census.is_none() && venues.is_none() {
            return rows;
        }
        let view = tx_view(tx);
        if let Some(v) = venues {
            let out = v.rows(&view);
            self.counters.venue_events.fetch_add(out.events, Ordering::Relaxed);
            self.counters.venue_graduations.fetch_add(out.graduations, Ordering::Relaxed);
            self.counters.venue_unknown_events.fetch_add(out.unknown_events, Ordering::Relaxed);
            self.counters.venue_inits.fetch_add(out.inits, Ordering::Relaxed);
            self.counters.venue_log_truncated.fetch_add(out.log_truncated, Ordering::Relaxed);
            self.counters.venue_log_unpaired.fetch_add(out.log_unpaired, Ordering::Relaxed);
            self.counters.venue_log_undecodable.fetch_add(out.log_undecodable, Ordering::Relaxed);
            self.counters.venue_balance_rows.fetch_add(out.balance_rows, Ordering::Relaxed);
            rows.extend(out.rows);
        }
        if let Some(c) = census {
            let meta = &tx.transaction_status_meta;
            let ctx = CensusTx {
                view,
                fee: meta.fee,
                signatures: u64::from(tx.transaction.message.header().num_required_signatures),
                cu_consumed: meta.compute_units_consumed,
            };
            if let Some(r) = c.row(&ctx, &self.labels) {
                self.counters.census_rows.fetch_add(1, Ordering::Relaxed);
                rows.push(r);
            }
        }
        rows
    }

    fn core_rows(&self, tx: &TransactionData) -> Vec<Row> {
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
        decode_token_metadata, explained, is_http_429, is_pda, key, mint_owner_deltas, owner_token_deltas, parse_system_funding,
        invoker, parse_token_ix, store_legs, summarize_calls, system_non_wallet_target, Bal, Class, IxView, TokenIx, Transfers, TxView,
        FUNDING_MIN_LAMPORTS, PROGRAM_LABELS, PUMP_AMM_IDL, PUMP_IDL, TOKEN_METADATA_INIT, WSOL_MINT,
        Census, CensusTx, AMM_V4_IXS, EVENT_IX_TAG, VENUE_IXS, LabPlugin, Coverage, Venues, DBC_IDL, DAMM_V2_IDL,
        LAUNCHLAB_IDL, CPMM_IDL, VENUE_EVENT_CONTEXT,
    };
    use crate::idl::{Kind, Val};
    use crate::sink::Row;
    use std::sync::Arc;
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

    // ------------------------------------------------------------------ token transfers

    fn amount_ix(disc: u8, amount: u64, decimals: Option<u8>) -> Vec<u8> {
        let mut d = vec![disc];
        d.extend(amount.to_le_bytes());
        d.extend(decimals);
        d
    }

    #[test]
    fn token_instructions_parse_with_their_account_positions() {
        let t = parse_token_ix(&amount_ix(3, 1_000, None), 3).unwrap();
        assert_eq!((t.kind, t.amount, t.source, t.dest, t.authority, t.mint), ("transfer", Some(1_000), Some(0), Some(1), Some(2), None));
        assert_eq!(parse_token_ix(&amount_ix(3, 1_000, None), 2), None); // too few accounts
        let t = parse_token_ix(&amount_ix(12, 7, Some(6)), 4).unwrap();
        assert_eq!(t, TokenIx { kind: "transfer_checked", amount: Some(7), decimals: Some(6), source: Some(0), mint: Some(1), dest: Some(2), authority: Some(3) });
        assert_eq!(parse_token_ix(&amount_ix(14, 5, Some(6)), 3).map(|t| (t.kind, t.mint, t.dest)), Some(("mint_to", Some(0), Some(1))));
        assert_eq!(parse_token_ix(&amount_ix(15, 5, Some(6)), 3).map(|t| (t.kind, t.source, t.mint, t.dest)), Some(("burn", Some(0), Some(1), None)));
        assert_eq!(parse_token_ix(&[9], 3).map(|t| (t.kind, t.amount, t.dest)), Some(("close_account", None, None)));
        assert_eq!(parse_token_ix(&[1], 4), None); // InitializeAccount
        assert_eq!(parse_token_ix(&[3, 1, 2], 3), None); // cut-off amount
        assert_eq!(parse_token_ix(&amount_ix(12, 7, None), 4), None); // TransferChecked without decimals
        // Token-2022 TransferCheckedWithFee: [26, 1, amount, decimals, fee]
        let mut with_fee = vec![26u8, 1];
        with_fee.extend(500u64.to_le_bytes());
        with_fee.push(6);
        with_fee.extend(5u64.to_le_bytes());
        let t = parse_token_ix(&with_fee, 4).unwrap();
        assert_eq!((t.kind, t.amount, t.decimals, t.source, t.mint, t.dest, t.authority),
                   ("transfer_checked_with_fee", Some(500), Some(6), Some(0), Some(1), Some(2), Some(3)));
        assert_eq!(parse_token_ix(&[26, 0, 1, 2], 4), None); // another transfer-fee instruction
        assert_eq!(parse_token_ix(&with_fee[..10], 4), None); // cut-off decimals
    }

    #[test]
    fn the_invoker_is_the_nearest_earlier_instruction_one_level_up() {
        let (o, a, b, c, d, e, f): (&[u8], &[u8], &[u8], &[u8], &[u8], &[u8], &[u8]) = (b"O", b"A", b"B", b"C", b"D", b"E", b"F");
        // A (2) -> B (3) -> C (4), then D (3) under A again; E (2) and F (no height) under the top level.
        let heights: [Option<u32>; 6] = [Some(2), Some(3), Some(4), Some(3), Some(2), None];
        let programs = [Some(a), Some(b), Some(c), Some(d), Some(e), Some(f)];
        let got: Vec<_> = (0..6).map(|j| invoker(&heights, &programs, j, Some(o))).collect();
        assert_eq!(got, vec![Some(o), Some(a), Some(b), Some(a), Some(o), Some(o)]);
        // A height-3 instruction with no height-2 one before it has no known invoker.
        assert_eq!(invoker(&[Some(3u32)], &[Some(a)], 0, Some(o)), None);
    }

    fn system_ix(disc: u32, lamports: u64, owner: Option<[u8; 32]>) -> Vec<u8> {
        let mut d = disc.to_le_bytes().to_vec();
        d.extend(lamports.to_le_bytes());
        if let Some(o) = owner {
            d.extend(0u64.to_le_bytes()); // space
            d.extend(o);
        }
        d
    }

    #[test]
    fn system_funding_is_a_transfer_or_a_wallet_creation() {
        assert_eq!(parse_system_funding(&system_ix(2, 5_000_000, None), 2), Some(("transfer", 5_000_000)));
        assert_eq!(parse_system_funding(&system_ix(0, 9_000_000, Some([0; 32])), 2), Some(("create_account", 9_000_000)));
        assert_eq!(parse_system_funding(&system_ix(0, 9_000_000, Some([7; 32])), 2), None); // a program-owned account
        assert_eq!(parse_system_funding(&system_ix(2, 5, None), 1), None);
        assert_eq!(parse_system_funding(&system_ix(1, 5, None), 2), None); // Assign
    }

    fn bal(idx: u8, mint: &'static str, owner: &'static str, amount: u128) -> Bal<'static> {
        Bal { idx, mint, owner, amount, decimals: 6 }
    }

    #[test]
    fn deltas_per_mint_and_owner_over_all_their_accounts() {
        let pre = [bal(1, "M", "A", 500), bal(2, "M", "A", 100), bal(3, "M", "B", 50), bal(4, "N", "C", 9)];
        let post = [bal(1, "M", "A", 300), bal(2, "M", "A", 100), bal(5, "M", "D", 250), bal(4, "N", "C", 9)];
        assert_eq!(mint_owner_deltas(&pre, &post), vec![("M", "A", -200, None, 6), ("M", "B", -50, Some(3), 6), ("M", "D", 250, Some(5), 6)]);
    }

    fn event(table: &str, fields: &[(&str, Val)]) -> Row {
        Row {
            table: Arc::from(table),
            columns: Arc::new(fields.iter().map(|(n, v)| ((*n).to_owned(), if matches!(v, Val::Str(_)) { Kind::Utf8 } else { Kind::U64 })).collect()),
            values: fields.iter().map(|(_, v)| v.clone()).collect(),
        }
    }

    fn st(v: &str) -> Val {
        Val::Str(v.to_owned())
    }

    const WSOL: &str = "So11111111111111111111111111111111111111112";

    #[test]
    fn store_legs_explain_trader_and_vault_flows() {
        let rows = [
            event("pump/TradeEvent", &[("mint", st("M")), ("user", st("U")), ("token_amount", Val::U64(500)), ("is_buy", Val::Bool(true))]),
            event("pump_amm/BuyEvent", &[("user", st("U")), ("pool", st("P")), ("ix_base_mint", st("M")), ("ix_quote_mint", st(WSOL)),
                                         ("base_amount_out", Val::U64(70))]),
            // A reversed pool (WSOL base, token R as quote, fee in tokens): selling WSOL for R.
            event("pump_amm/SellEvent", &[("user", st("V")), ("pool", st("Q")), ("ix_base_mint", st(WSOL)), ("ix_quote_mint", st("R")),
                                          ("user_quote_amount_out", Val::U64(90)), ("quote_amount_out_without_lp_fee", Val::U64(95))]),
        ];
        let legs = store_legs(&rows);
        assert_eq!(legs.get(&("M".to_owned(), Some("U".to_owned()))), Some(&570)); // curve 500 + pool 70, summed
        assert_eq!(legs.get(&("M".to_owned(), None)), Some(&-500)); // the curve's vault
        assert_eq!(legs.get(&("M".to_owned(), Some("P".to_owned()))), Some(&-70)); // the pool's vault
        assert_eq!((legs.get(&("R".to_owned(), Some("V".to_owned()))), legs.get(&("R".to_owned(), Some("Q".to_owned())))), (Some(&90), Some(&-95)));
        assert!(explained(&legs, "M", "U", false, 570) && !explained(&legs, "M", "U", false, 500));
        assert!(explained(&legs, "M", "CurveVaultOwner", true, -500) && !explained(&legs, "M", "SomeWallet", false, -500));
    }

    /// A key on (wallet) or off (program-derived) the ed25519 curve, deterministic.
    fn test_key(seed: u8, on_curve: bool) -> [u8; 32] {
        (0u8..=255).map(|i| [seed; 31].iter().copied().chain([i]).collect::<Vec<u8>>())
            .map(|v| <[u8; 32]>::try_from(v).unwrap())
            .find(|k| solana_message::Address::from(*k).is_on_curve() == on_curve)
            .unwrap()
    }

    fn b58(k: &[u8]) -> String {
        bs58::encode(k).into_string()
    }

    fn col<'a>(r: &'a Row, name: &str) -> &'a Val {
        &r.values[r.columns.iter().position(|(n, _)| n == name).unwrap()]
    }

    #[test]
    fn insider_transfers_wallet_births_and_explained_trades() {
        let payer = test_key(1, true);
        let (insider, buyer, fresh) = (test_key(2, true), test_key(3, true), test_key(4, true));
        let (ins_ata, buyer_ata, fresh_ata) = (test_key(5, true), test_key(6, true), test_key(7, true));
        let mint = test_key(8, true);
        let curve = test_key(9, false); // the bonding curve PDA owns the curve's vault
        let curve_ata = test_key(10, true);
        let token = key("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
        let system = key("11111111111111111111111111111111");
        let pump = key("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");
        let keys: Vec<&[u8]> =
            vec![&payer[..], &ins_ata[..], &buyer_ata[..], &fresh_ata[..], &mint[..], &token[..], &system[..], &fresh[..], &curve_ata[..], &pump[..]];
        let (m, ins, buy, cv) = (b58(&mint), b58(&insider), b58(&buyer), b58(&curve));
        let mints = std::collections::HashSet::from([m.clone()]);
        let t = Transfers::new(mints, None);
        let leak: fn(String) -> &'static str = |s| Box::leak(s.into_boxed_str());
        let (m, ins, buy, cv) = (leak(m), leak(ins), leak(buy), leak(cv));
        let fresh_s = b58(&fresh);
        // Pre: insider 5,000, buyer none, curve vault 10,000. The transaction: the buyer buys 500 on
        // the curve (explained), the insider sends 1,000 to a fresh wallet's account (top level), the
        // curve's transfer to the buyer runs inside pump (a trade); the payer funds the fresh wallet
        // with 0.002 SOL (a birth) and the insider with 0.005 SOL (not a birth).
        let pre = vec![bal(1, m, ins, 5_000), bal(8, m, cv, 10_000)];
        let post = vec![bal(1, m, ins, 4_000), bal(2, m, buy, 500), bal(3, m, "FreshOwnerUnused", 0), bal(8, m, cv, 9_500)];
        let post: Vec<Bal> = post.into_iter().map(|b| if b.idx == 3 { Bal { owner: leak(fresh_s.clone()), amount: 1_000, ..b } } else { b }).collect();
        let transfer = amount_ix(12, 1_000, Some(6));
        let curve_out = amount_ix(3, 500, None);
        let birth = system_ix(2, 2 * FUNDING_MIN_LAMPORTS, None);
        let not_birth = system_ix(2, 5 * FUNDING_MIN_LAMPORTS, None);
        let small = system_ix(2, FUNDING_MIN_LAMPORTS - 1, None);
        let tx = TxView { logs: &[],
            slot: 7,
            tx_index: 3,
            fee_payer: b58(&payer),
            failed: false,
            ixs: vec![
                IxView { path: "0".into(), program: Some(&token), accounts: &[1, 4, 3, 0], data: &transfer, parent: None },
                IxView { path: "1.0".into(), program: Some(&token), accounts: &[8, 2, 9], data: &curve_out, parent: Some(&pump) },
                IxView { path: "2".into(), program: Some(&system), accounts: &[0, 7], data: &birth, parent: None },
                IxView { path: "3".into(), program: Some(&system), accounts: &[0, 1], data: &not_birth, parent: None },
                IxView { path: "4".into(), program: Some(&system), accounts: &[0, 7], data: &small, parent: None },
            ],
            pre_lamports: &[10_000_000_000, 2_000_000, 0, 0, 1, 1, 1, 0, 2_000_000, 1],
            post_lamports: &[9_988_000_000, 7_000_000, 2_039_280, 2_039_280, 1, 1, 1, 2_999_999, 2_000_000, 1],
            pre_tokens: pre,
            post_tokens: post,
            keys,
        };
        let core = [event("pump/TradeEvent", &[("mint", st(m)), ("user", st(buy)), ("token_amount", Val::U64(500)), ("is_buy", Val::Bool(true))])];
        let labels = PROGRAM_LABELS.iter().map(|(id, label, class)| (key(id), (*label, *class))).collect();
        let rows = t.rows(&tx, &core, &labels);
        let of = |table: &str| rows.iter().filter(|r| &*r.table == table).collect::<Vec<_>>();
        // token_deltas: the insider's -1,000 and the fresh wallet's +1,000; the buyer's +500 and the
        // curve vault's -500 are the store's leg.
        let deltas: Vec<(Val, Val, Val)> = of("token_deltas").iter().map(|r| (col(r, "owner").clone(), col(r, "delta_raw").clone(), col(r, "is_vault").clone())).collect();
        assert_eq!(deltas.len(), 2, "{deltas:?}");
        assert!(deltas.contains(&(st(ins), st("-1000"), Val::Bool(false))) && deltas.contains(&(st(&fresh_s), st("1000"), Val::Bool(false))));
        assert!(of("token_deltas").iter().all(|r| *col(r, "has_pump_event") == Val::Bool(true) && *col(r, "has_pump_event_same_mint") == Val::Bool(true)));
        assert!(is_pda(cv) && !is_pda(ins));
        // token_transfers: the insider's (a graph edge) and the curve's to the buyer (in a trade).
        let tr = of("token_transfers");
        assert_eq!(tr.len(), 2);
        let top = tr.iter().find(|r| *col(r, "ix_path") == st("0")).unwrap();
        assert_eq!((col(top, "kind"), col(top, "source_owner"), col(top, "dest_owner"), col(top, "amount_raw")),
                   (&st("transfer_checked"), &st(ins), &st(&fresh_s), &Val::U64(1_000)));
        assert_eq!((col(top, "in_trade"), col(top, "outer_program"), col(top, "owner_unresolved")), (&Val::Bool(false), &Val::Null, &Val::Bool(false)));
        let inner = tr.iter().find(|r| *col(r, "ix_path") == st("1.0")).unwrap();
        assert_eq!((col(inner, "in_trade"), col(inner, "outer_program"), col(inner, "mint")), (&Val::Bool(true), &st("pump"), &st(m)));
        // sol_funding: only the fresh wallet's 0.002 SOL (0 before, >= 0.001 SOL).
        let f = of("sol_funding");
        assert_eq!(f.len(), 1);
        assert_eq!((col(f[0], "to_account"), col(f[0], "lamports"), col(f[0], "kind")), (&st(&fresh_s), &Val::U64(2 * FUNDING_MIN_LAMPORTS), &st("transfer")));
    }

    #[test]
    fn wallet_births_skip_token_temporary_assigned_and_pda_receivers() {
        let (payer, wsol, temp, assigned, wallet, nonce) =
            (test_key(11, true), test_key(12, true), test_key(13, true), test_key(14, true), test_key(15, true), test_key(17, true));
        let pda = test_key(16, false);
        let system = key("11111111111111111111111111111111");
        let keys: Vec<&[u8]> = vec![&payer[..], &wsol[..], &temp[..], &pda[..], &assigned[..], &wallet[..], &nonce[..], &system[..]];
        let fund = system_ix(2, 3 * FUNDING_MIN_LAMPORTS, None);
        let mut assign = 1u32.to_le_bytes().to_vec();
        assign.extend([7u8; 32]);
        // A nonce account: created by the System program for itself, but with 80 bytes of data.
        let mut nonce_create = system_ix(0, 1_447_680, Some([0; 32]));
        nonce_create[12..20].copy_from_slice(&80u64.to_le_bytes());
        assert_eq!(parse_system_funding(&nonce_create, 2), None);
        assert_eq!(system_non_wallet_target(&nonce_create, &[0, 6]), Some(6));
        assert_eq!(system_non_wallet_target(&assign, &[4]), Some(4));
        assert_eq!(system_non_wallet_target(&fund, &[0, 5]), None);
        assert_eq!(system_non_wallet_target(&system_ix(0, 9_000_000, Some([0; 32])), &[0, 5]), None); // a wallet creation
        // The payer funds: a WSOL token account, a temporary account closed again in the transaction, a
        // PDA, an account it then assigns to a program, and a wallet. Only the wallet is a birth.
        let ixs = || vec![
            IxView { path: "0".into(), program: Some(&system), accounts: &[0, 6], data: &nonce_create, parent: None },
            IxView { path: "1".into(), program: Some(&system), accounts: &[0, 1], data: &fund, parent: None },
            IxView { path: "2".into(), program: Some(&system), accounts: &[0, 2], data: &fund, parent: None },
            IxView { path: "3".into(), program: Some(&system), accounts: &[0, 3], data: &fund, parent: None },
            IxView { path: "4".into(), program: Some(&system), accounts: &[0, 4], data: &fund, parent: None },
            IxView { path: "5".into(), program: Some(&system), accounts: &[4], data: &assign, parent: None },
            IxView { path: "6".into(), program: Some(&system), accounts: &[0, 5], data: &fund, parent: None },
        ];
        let t = Transfers::new(std::collections::HashSet::new(), None);
        let pre: [u64; 8] = [10_000_000_000, 0, 0, 0, 0, 0, 0, 1];
        let ok = TxView { logs: &[], slot: 1, tx_index: 0, fee_payer: b58(&payer), failed: false, keys: keys.clone(), ixs: ixs(), pre_lamports: &pre,
                          post_lamports: &[9_985_000_000, 3_000_000, 0, 3_000_000, 3_000_000, 3_000_000, 1_447_680, 1],
                          pre_tokens: vec![], post_tokens: vec![bal(1, WSOL_MINT, "PayerOwner", 3_000_000)] };
        let to = |tx: &TxView| t.rows(tx, &[], &Default::default()).iter().filter(|r| &*r.table == "sol_funding")
            .map(|r| (col(r, "to_account").clone(), col(r, "failed").clone())).collect::<Vec<_>>();
        assert_eq!(to(&ok), vec![(st(&b58(&wallet)), Val::Bool(false))]);
        // A failed transaction changes no balance and has no token accounts it would have created: the
        // WSOL and temporary accounts cannot be told from wallets there, so they stay in, flagged failed.
        let failed = TxView { failed: true, post_lamports: &[9_999_995_000, 0, 0, 0, 0, 0, 0, 1], post_tokens: vec![], ixs: ixs(), keys, ..ok };
        let got = to(&failed);
        assert_eq!(got.iter().map(|(a, _)| a.clone()).collect::<Vec<_>>(), [&wsol, &temp, &wallet].map(|k| st(&b58(k))).to_vec());
        assert!(got.iter().all(|(_, f)| *f == Val::Bool(true)));
    }

    #[test]
    fn mints_outside_the_set_are_not_kept_and_created_mints_are_learned() {
        let t = Transfers::new(std::collections::HashSet::from(["Known".to_owned()]), None);
        assert!(t.is_pump("Known") && t.is_pump("AbcdefPump111pump") && !t.is_pump("Other"));
        let created = event("pump/CreateEvent", &[("mint", st("NewMint"))]);
        let tx = TxView { logs: &[], slot: 1, tx_index: 0, fee_payer: String::new(), failed: false, keys: vec![], ixs: vec![], pre_lamports: &[], post_lamports: &[],
                          pre_tokens: vec![bal(0, "Other", "A", 5)], post_tokens: vec![bal(0, "Other", "A", 1)] };
        assert!(t.rows(&tx, &[created], &Default::default()).is_empty()); // "Other" is no pump mint
        assert!(t.is_pump("NewMint"));
        // A curve trade teaches its mint; a PumpSwap pool (any pair, e.g. USDC) and suffix or file
        // mints do not go into the learned set.
        let traded = event("pump/TradeEvent", &[("mint", st("OldCurveMint"))]);
        let pool = event("pump_amm/CreatePoolEvent", &[("mint", st("UsdcLike"))]);
        let suffixed = event("pump/TradeEvent", &[("mint", st("Xyzpump"))]);
        let listed = event("pump/TradeEvent", &[("mint", st("Known"))]);
        t.rows(&tx, &[traded, pool, suffixed, listed], &Default::default());
        assert!(t.is_pump("OldCurveMint") && !t.is_pump("UsdcLike"));
        assert_eq!(t.learned_mints(), vec!["NewMint".to_owned(), "OldCurveMint".to_owned()]);
        // The curve program's token incentive events carry the incentive token (PUMP): nothing learned.
        // Its fee events carry the curve's token and teach it.
        let claim = event("pump/ClaimTokenIncentivesEvent", &[("mint", st("IncentiveTok"))]);
        let admin = event("pump/AdminUpdateTokenIncentivesEvent", &[("mint", st("IncentiveTok"))]);
        let fees = event("pump/DistributeCreatorFeesEvent", &[("mint", st("FeeCurveMint"))]);
        t.rows(&tx, &[claim, admin, fees], &Default::default());
        assert!(!t.is_pump("IncentiveTok") && t.is_pump("FeeCurveMint"));
        assert_eq!(t.learned_mints(), vec!["FeeCurveMint".to_owned(), "NewMint".to_owned(), "OldCurveMint".to_owned()]);
    }

    // ------------------------------------------------------------------------- venue census

    #[test]
    fn venue_ix_matches_the_pinned_idls() {
        // tools/gen_venue_ix.py's rule, again from the files: discriminator, name and the first account named as a pool.
        const POOL: [&str; 8] = ["pool_state", "lb_pair", "whirlpool", "whirlpool_one", "pool", "bonding_curve", "virtual_pool", "amm"];
        fn names(a: &serde_json::Value, out: &mut Vec<String>) {
            for x in a.as_array().map(Vec::as_slice).unwrap_or(&[]) {
                match x.get("accounts") {
                    Some(inner) => names(inner, out),
                    None => out.push(x["name"].as_str().unwrap().to_owned()),
                }
            }
        }
        let files: [(&str, &str); 10] = [
            ("pump", PUMP_IDL),
            ("pump_amm", PUMP_AMM_IDL),
            ("raydium_cpmm", CPMM_IDL),
            ("raydium_clmm", include_str!("../idl/venues/raydium_clmm.json")),
            ("raydium_launchlab", include_str!("../idl/venues/raydium_launchlab.json")),
            ("meteora_dlmm", include_str!("../idl/venues/meteora_dlmm.json")),
            ("meteora_damm_v1", include_str!("../idl/venues/meteora_damm_v1.json")),
            ("meteora_damm_v2", include_str!("../idl/venues/meteora_damm_v2.json")),
            ("meteora_dbc", include_str!("../idl/venues/meteora_dbc.json")),
            ("orca_whirlpool", include_str!("../idl/venues/orca_whirlpool.json")),
        ];
        let mut expect = Vec::new();
        for (label, text) in files {
            let j: serde_json::Value = serde_json::from_str(text).unwrap();
            let id = PROGRAM_LABELS.iter().find(|(_, l, _)| *l == label).unwrap().0;
            assert_eq!(j["address"].as_str(), Some(id), "{label}: the IDL is another program's");
            for ix in j["instructions"].as_array().unwrap() {
                let disc: Vec<u8> = ix["discriminator"].as_array().unwrap().iter().map(|b| u8::try_from(b.as_u64().unwrap()).unwrap()).collect();
                let mut n = Vec::new();
                names(&ix["accounts"], &mut n);
                let pool = n.iter().position(|a| POOL.contains(&a.as_str())).map(|p| u8::try_from(p).unwrap());
                expect.push((label, <[u8; 8]>::try_from(disc).unwrap(), ix["name"].as_str().unwrap().to_owned(), pool));
            }
        }
        let got: Vec<_> = VENUE_IXS.iter().map(|&(l, d, n, p)| (l, d, n.to_owned(), p)).collect();
        assert_eq!(got, expect, "src/venue_ix.rs is not tools/gen_venue_ix.py's output of the pinned IDLs");
        // (label, discriminator) is unique, and every DEX label has a table (AMM v4: its tags).
        let keys: std::collections::HashSet<_> = VENUE_IXS.iter().map(|&(l, d, ..)| (l, d)).collect();
        assert_eq!(keys.len(), VENUE_IXS.len());
        for (_, label, class) in PROGRAM_LABELS {
            if class == Class::Dex {
                assert!(label == "raydium_amm_v4" || VENUE_IXS.iter().any(|x| x.0 == label), "{label} has no instruction table");
            }
        }
        let tags: std::collections::HashSet<u8> = AMM_V4_IXS.iter().map(|x| x.0).collect();
        assert_eq!(tags.len(), AMM_V4_IXS.len());
    }

    /// An Anchor instruction's data: the discriminator of `name` from the table, then arguments.
    fn venue_ix(label: &str, name: &str) -> Vec<u8> {
        let d = VENUE_IXS.iter().find(|x| x.0 == label && x.2 == name).unwrap_or_else(|| panic!("{label} {name}")).1;
        d.iter().copied().chain([7u8; 16]).collect()
    }

    fn labels() -> std::collections::HashMap<[u8; 32], (&'static str, Class)> {
        PROGRAM_LABELS.iter().map(|(id, label, class)| (key(id), (*label, *class))).collect()
    }

    #[test]
    fn census_names_the_pool_of_launchlab_migrate_to_amm_in_its_newer_layout() {
        // pool_state is account 14 of the 23 since LaunchLab's 2026-09-09 upgrade, 23 of the IDL's 32 before.
        let census = Census::new();
        let labels = labels();
        let disc = crate::idl::Idl::parse(LAUNCHLAB_IDL).unwrap().instructions.iter().find(|i| i.name == "migrate_to_amm").unwrap().discriminator;
        let mut keys: Vec<[u8; 32]> = vec![test_key(41, true), key("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj")];
        keys.extend((0..32u8).map(|i| test_key(50 + i, false)));
        for (n, pool) in [(23usize, 14usize), (32, 23)] {
            let accounts: Vec<u8> = (0..n as u8).map(|i| i + 2).collect();
            let view = TxView { logs: &[], slot: 1, tx_index: 0, fee_payer: b58(&keys[0]), failed: false, keys: keys.iter().map(|k| &k[..]).collect(),
                                ixs: vec![IxView { path: "0".into(), program: Some(&keys[1][..]), accounts: &accounts, data: &disc, parent: None }],
                                pre_lamports: &[], post_lamports: &[], pre_tokens: vec![], post_tokens: vec![] };
            let r = census.row(&CensusTx { view, fee: 5_000, signatures: 1, cu_consumed: None }, &labels).unwrap();
            assert_eq!(col(&r, "dex_ixs"), &st(&format!("raydium_launchlab:migrate_to_amm:{}", b58(&keys[pool + 2]))), "{n} accounts");
        }
    }

    #[test]
    fn census_row_of_a_routed_two_dex_trade() {
        let leak: fn(String) -> &'static str = |s| Box::leak(s.into_boxed_str());
        let payer = test_key(31, true);
        let payer_s = leak(b58(&payer));
        let (pool_a, lb_b) = (test_key(32, false), test_key(33, false));
        let [jup, cpmm, dlmm, system, cb] = ["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
            "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "11111111111111111111111111111111", "ComputeBudget111111111111111111111111111111"].map(key);
        let tip = key("96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5");
        // keys: payer 0, jupiter 1, cpmm 2, dlmm 3, system 4, tip account 5, pool A 6, lb pair B 7, compute budget 8
        let keys: Vec<&[u8]> = vec![&payer[..], &jup[..], &cpmm[..], &dlmm[..], &system[..], &tip[..], &pool_a[..], &lb_b[..], &cb[..]];
        let (swap_in, dlmm_swap) = (venue_ix("raydium_cpmm", "swap_base_input"), venue_ix("meteora_dlmm", "swap"));
        let event: Vec<u8> = EVENT_IX_TAG.iter().copied().chain([1u8; 8]).collect();
        let tip_ix = system_ix(2, 1_000_000, None);
        let price = [3u8, 1, 0, 0, 0, 0, 0, 0, 0];
        let view = TxView { logs: &[],
            slot: 9,
            tx_index: 4,
            fee_payer: payer_s.to_owned(),
            failed: false,
            ixs: vec![
                IxView { path: "0".into(), program: Some(&cb), accounts: &[], data: &price, parent: None },
                IxView { path: "1".into(), program: Some(&jup), accounts: &[0, 2, 3], data: &[9, 9], parent: None },
                // CPMM swap_base_input: pool_state is its 4th account (position 3).
                IxView { path: "1.0".into(), program: Some(&cpmm), accounts: &[0, 4, 4, 6, 4], data: &swap_in, parent: Some(&jup) },
                // DLMM swap: lb_pair first.
                IxView { path: "1.1".into(), program: Some(&dlmm), accounts: &[7, 4], data: &dlmm_swap, parent: Some(&jup) },
                IxView { path: "1.2".into(), program: Some(&dlmm), accounts: &[3], data: &event, parent: Some(&dlmm) },
                IxView { path: "2".into(), program: Some(&system), accounts: &[0, 5], data: &tip_ix, parent: None },
            ],
            pre_lamports: &[10_000_000_000, 1, 1, 1, 1, 1, 1, 1, 1],
            post_lamports: &[9_998_985_000, 1, 1, 1, 1, 1_000_001, 1, 1, 1],
            pre_tokens: vec![bal(10, WSOL, payer_s, 1_000_000_000), bal(11, "MintX", "PoolOwnerA", 5_000)],
            post_tokens: vec![bal(10, WSOL, payer_s, 1_050_000_000), bal(11, "MintX", "PoolOwnerA", 5_000), bal(12, "MintY", "PoolOwnerB", 1)],
            keys,
        };
        let r = Census::new().row(&CensusTx { view, fee: 10_000, signatures: 1, cu_consumed: Some(150_000) }, &labels()).unwrap();
        assert_eq!(&*r.table, "venue_census");
        let got: Vec<(&str, Val)> = ["failed", "fee_payer", "tx_fee", "priority_fee", "jito_tip", "cu_consumed", "venues", "n_dex", "top_programs",
            "dex_ixs", "n_dex_ixs", "signer_sol_delta", "signer_token_deltas", "mints"].iter().map(|c| (*c, col(&r, c).clone())).collect();
        let want = vec![
            ("failed", Val::Bool(false)),
            ("fee_payer", st(payer_s)),
            ("tx_fee", Val::U64(10_000)),
            ("priority_fee", Val::U64(5_000)),
            ("jito_tip", Val::U64(1_000_000)),
            ("cu_consumed", Val::U64(150_000)),
            ("venues", st("meteora_dlmm,raydium_cpmm")),
            ("n_dex", Val::U64(2)),
            ("top_programs", st("jupiter_v6")), // compute budget and system are infrastructure
            ("dex_ixs", st(&format!("raydium_cpmm:swap_base_input:{};meteora_dlmm:swap:{}", b58(&pool_a), b58(&lb_b)))), // no event self-CPI
            ("n_dex_ixs", Val::U64(2)),
            ("signer_sol_delta", Val::I64(-1_015_000)),
            ("signer_token_deltas", st(&format!("{WSOL}:50000000"))),
            ("mints", st(&format!("MintX;MintY;{WSOL}"))),
        ];
        assert_eq!(got, want);
        assert_eq!((col(&r, "slot"), col(&r, "tx_index")), (&Val::U64(9), &Val::U64(4)));
    }

    #[test]
    fn census_names_amm_v4_by_tag_keeps_unknown_instructions_and_skips_transactions_without_a_dex() {
        let payer = test_key(41, true);
        let (amm, bot) = (test_key(42, false), test_key(43, true));
        let [ray, orca, system, token] = ["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
            "11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"].map(key);
        // keys: payer 0, bot program 1, AMM v4 2, amm account 3, whirlpool program 4, system 5, token 6
        let keys: Vec<&[u8]> = vec![&payer[..], &bot[..], &ray[..], &amm[..], &orca[..], &system[..], &token[..]];
        let (swap_in, swap_in_v2, unknown) = ([9u8, 1, 2, 3], [16u8, 5, 5], [1u8, 2, 3, 4, 5, 6, 7, 8, 9]);
        let transfer = system_ix(2, 5, None);
        let census = Census::new();
        let labels = labels();
        let row = |failed: bool, ixs: Vec<IxView>| {
            let view = TxView { logs: &[],
                slot: 1, tx_index: 0, fee_payer: b58(&payer), failed, keys: keys.clone(), ixs,
                pre_lamports: &[5_000_000, 1, 1, 1, 1, 1, 1], post_lamports: &[4_990_000, 1, 1, 1, 1, 1, 1], pre_tokens: vec![], post_tokens: vec![],
            };
            census.row(&CensusTx { view, fee: 5_000, signatures: 1, cu_consumed: None }, &labels)
        };
        // A bot's own program drives an AMM v4 swap (tag 9, amm at 1) and an unknown Whirlpool instruction.
        let r = row(false, vec![
            IxView { path: "0".into(), program: Some(&bot), accounts: &[0, 2, 4], data: &[0], parent: None },
            IxView { path: "0.0".into(), program: Some(&ray), accounts: &[6, 3, 0], data: &swap_in, parent: Some(&bot) },
            IxView { path: "0.1".into(), program: Some(&orca), accounts: &[3], data: &unknown, parent: Some(&bot) },
        ]).unwrap();
        assert_eq!(col(&r, "venues"), &st("orca_whirlpool,raydium_amm_v4"));
        assert_eq!(col(&r, "top_programs"), &st(&b58(&bot)));
        // The unknown Whirlpool instruction carries its first eight bytes, to be named (LAB review on #506).
        assert_eq!(col(&r, "dex_ixs"), &st(&format!("raydium_amm_v4:swap_base_in:{};orca_whirlpool:?0102030405060708:", b58(&amm))));
        assert_eq!((col(&r, "priority_fee"), col(&r, "cu_consumed"), col(&r, "mints"), col(&r, "signer_token_deltas")),
                   (&Val::U64(0), &Val::Null, &Val::Null, &Val::Null));
        assert_eq!(col(&r, "signer_sol_delta"), &Val::I64(-10_000));
        // A failed transaction (here with its top level only): still a row, flagged.
        let f = row(true, vec![IxView { path: "0".into(), program: Some(&ray), accounts: &[6, 3], data: &swap_in_v2, parent: None }]).unwrap();
        assert_eq!((col(&f, "failed"), col(&f, "dex_ixs"), col(&f, "top_programs")),
                   (&Val::Bool(true), &st(&format!("raydium_amm_v4:swap_base_in_v2:{}", b58(&amm))), &st("raydium_amm_v4")));
        // No DEX program: no row (a plain transfer).
        assert!(row(false, vec![IxView { path: "0".into(), program: Some(&system), accounts: &[0, 1], data: &transfer, parent: None }]).is_none());
    }

    #[test]
    fn the_recorder_plugin_never_has_the_extractor_only_tables() {
        // LAB review on #500: an exported LAB_VENUE_CENSUS or LAB_TRANSFERS in the recorder's shell must not reach live
        // segments. The only test that sets these variables; the recorder's other tests build for_recorder (no read).
        let dir = std::env::temp_dir().join(format!("lab-plugin-closed-{}", std::process::id()));
        let parts = || (Arc::new(crate::sink::Sink::start(dir.clone())), Arc::new(Coverage::new(0, 0)));
        std::env::set_var("LAB_VENUE_CENSUS", "1");
        std::env::set_var("LAB_TRANSFERS", "1");
        std::env::set_var("LAB_VENUES", "1");
        let (sink, cov) = parts();
        let rec = LabPlugin::for_recorder(sink, cov).unwrap();
        assert!(rec.census.is_none() && rec.transfers.is_none() && rec.venues.is_none());
        // The extractor does read them: LAB_TRANSFERS=1 without LAB_PUMP_MINTS is refused, the census alone is on.
        let (sink, cov) = parts();
        let refused = LabPlugin::new(sink, cov).is_err();
        std::env::remove_var("LAB_TRANSFERS");
        let (sink, cov) = parts();
        let ext = LabPlugin::new(sink, cov).unwrap();
        let census_on = ext.census.is_some() && ext.venues.is_some();
        std::env::remove_var("LAB_VENUE_CENSUS");
        std::env::remove_var("LAB_VENUES");
        let _ = std::fs::remove_dir_all(&dir);
        assert!(refused && census_on);
    }

    // ------------------------------------------------------------------------- venue decoders

    #[test]
    fn every_vendored_idl_parses_in_full_and_every_event_decodes() {
        // Every event of DBC, DAMM v2, LaunchLab and CPMM decodes from zero bytes (options none, vectors empty, enums
        // their first variant) without an error: idl.rs reads all their field types. Each event has its table.
        let venues = Venues::new().unwrap();
        assert_eq!(venues.ids().iter().map(|(l, _)| *l).collect::<Vec<_>>(), ["meteora_dbc", "meteora_damm_v2", "raydium_launchlab", "raydium_cpmm"]);
        // Every vendored IDL parses in full (the decoders' three and the others the census names), so a later decoder
        // finds no parser gap (#506's first build: LaunchLab's tuple variants of objects, DAMM v1's f64).
        for (label, text) in [
            ("pump", PUMP_IDL),
            ("pump_amm", PUMP_AMM_IDL),
            ("meteora_dbc", DBC_IDL),
            ("meteora_damm_v2", DAMM_V2_IDL),
            ("raydium_launchlab", LAUNCHLAB_IDL),
            ("raydium_cpmm", CPMM_IDL),
            ("raydium_clmm", include_str!("../idl/venues/raydium_clmm.json")),
            ("meteora_dlmm", include_str!("../idl/venues/meteora_dlmm.json")),
            ("meteora_damm_v1", include_str!("../idl/venues/meteora_damm_v1.json")),
            ("orca_whirlpool", include_str!("../idl/venues/orca_whirlpool.json")),
        ] {
            let idl = Idl::parse(text).unwrap_or_else(|e| panic!("{label}: {e:#}"));
            assert!(!idl.events.is_empty(), "{label}");
            for ev in &idl.events {
                let d = idl.decode_event(ev, &[0u8; 8192]);
                assert!(d.status.as_str() != "error", "{label}/{} does not decode", ev.name);
                assert_eq!(d.values.len(), ev.fields.len());
            }
        }
        assert_eq!(VENUE_EVENT_CONTEXT.len(), 20);
    }

    fn ix_disc(text: &str, name: &str) -> Vec<u8> {
        Idl::parse(text).unwrap().instructions.iter().find(|i| i.name == name).unwrap_or_else(|| panic!("{name}")).discriminator.to_vec()
    }

    #[test]
    fn a_launchlab_trade_event_gets_its_row_with_the_parent_and_its_accounts() {
        let idl = Idl::parse(LAUNCHLAB_IDL).unwrap();
        let ev = idl.events.iter().find(|e| e.name == "TradeEvent").unwrap();
        let (payer, pool, base, quote) = (test_key(51, true), test_key(52, false), test_key(53, false), key(WSOL));
        let launchlab = key("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj");
        // The event body in the IDL's field order: the pool, then u64s 1, 2, 3, ..., then Buy, Trade, exact_in.
        let mut body = Vec::new();
        let mut n = 0u64;
        for f in &ev.fields {
            match f.name.as_str() {
                "pool_state" => body.extend(pool),
                "trade_direction" => body.push(0), // Buy
                "pool_status" => body.push(2),     // Trade
                "exact_in" => body.push(1),
                _ => {
                    n += 1;
                    body.extend(n.to_le_bytes());
                }
            }
        }
        let mut data: Vec<u8> = EVENT_IX_TAG.to_vec();
        data.extend(ev.discriminator);
        data.extend(&body);
        let buy: Vec<u8> = ix_disc(LAUNCHLAB_IDL, "buy_exact_in").into_iter().chain([0u8; 24]).collect();
        // keys: payer 0, launchlab 1, pool 2, base mint 3, quote mint 4. buy_exact_in: payer 0, pool_state 4, base 9, quote 10.
        let keys: Vec<&[u8]> = vec![&payer[..], &launchlab[..], &pool[..], &base[..], &quote[..]];
        let accounts = [0u8, 1, 1, 1, 2, 1, 1, 1, 1, 3, 4, 1, 1, 1, 1];
        let view = TxView { logs: &[],
            slot: 5,
            tx_index: 2,
            fee_payer: b58(&payer),
            failed: false,
            ixs: vec![
                IxView { path: "0".into(), program: Some(&launchlab), accounts: &accounts, data: &buy, parent: None },
                IxView { path: "0.0".into(), program: Some(&launchlab), accounts: &[1], data: &data, parent: Some(&launchlab) },
            ],
            pre_lamports: &[], post_lamports: &[], pre_tokens: vec![], post_tokens: vec![], keys,
        };
        let out = Venues::new().unwrap().rows(&view);
        assert_eq!((out.rows.len(), out.events, out.graduations, out.unknown_events), (1, 1, 0, 0));
        let r = &out.rows[0];
        assert_eq!(&*r.table, "raydium_launchlab/TradeEvent");
        let ctx: Vec<Val> = ["slot", "tx_index", "outer_ix", "inner_ix", "event_seq", "fee_payer", "outer_program", "parent_ix_name",
            "decode_status", "payload_len", "ix_trader", "ix_pool", "ix_base_mint", "ix_quote_mint"].iter().map(|c| col(r, c).clone()).collect();
        assert_eq!(ctx, vec![Val::U64(5), Val::U64(2), Val::U64(0), Val::U64(0), Val::U64(0), st(&b58(&payer)), st(&b58(&launchlab)),
            st("buy_exact_in"), st("ok"), Val::U64(body.len() as u64), st(&b58(&payer)), st(&b58(&pool)), st(&b58(&base)), st(WSOL)]);
        // The parent is the top-level buy: no inner index, its discriminator; the transaction succeeded.
        assert_eq!((col(r, "parent_inner_ix"), col(r, "parent_ix_disc"), col(r, "failed")), (&Val::Null, &st(&hex::encode(&buy[..8])), &Val::Bool(false)));
        assert_eq!((col(r, "pool_state"), col(r, "total_base_sell"), col(r, "virtual_base")), (&st(&b58(&pool)), &Val::U64(1), &Val::U64(2)));
        assert_eq!((col(r, "trade_direction"), col(r, "pool_status"), col(r, "exact_in")), (&st("Buy"), &st("Trade"), &Val::Bool(true)));
        // A failed transaction keeps the events the runtime recorded before the failure, flagged (not a trade).
        let failed = Venues::new().unwrap().rows(&TxView { failed: true, ..view });
        assert_eq!((failed.events, col(&failed.rows[0], "failed")), (1, &Val::Bool(true)));
    }

    #[test]
    fn graduations_name_both_pools_and_unknown_events_are_counted() {
        let (payer, vpool, dpool, base, cpool, lpool) = (test_key(61, true), test_key(62, false), test_key(63, false), test_key(64, false), test_key(65, false), test_key(66, false));
        let (dbc, launchlab, router) = (key("dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"), key("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"), test_key(67, true));
        let quote = key(WSOL);
        // keys: payer 0, dbc 1, virtual pool 2, damm pool 3, base 4, quote 5, launchlab 6, cpswap pool 7, launchlab pool 8, router 9
        let keys: Vec<&[u8]> = vec![&payer[..], &dbc[..], &vpool[..], &dpool[..], &base[..], &quote[..], &launchlab[..], &cpool[..], &lpool[..], &router[..]];
        let mig = ix_disc(DBC_IDL, "migration_damm_v2");
        // migration_damm_v2: virtual_pool 0, pool 4, base_mint 13, quote_mint 14 (25 accounts).
        let mut dbc_accounts = [1u8; 25];
        (dbc_accounts[0], dbc_accounts[4], dbc_accounts[13], dbc_accounts[14]) = (2, 3, 4, 5);
        let cps = ix_disc(LAUNCHLAB_IDL, "migrate_to_cpswap");
        // migrate_to_cpswap: base_mint 1, quote_mint 2, cpswap_pool 5, pool_state 17 (28 accounts).
        let mut ll_accounts = [6u8; 28];
        (ll_accounts[1], ll_accounts[2], ll_accounts[5], ll_accounts[17]) = (4, 5, 7, 8);
        let unknown: Vec<u8> = EVENT_IX_TAG.iter().copied().chain([9u8; 8]).chain([1u8; 4]).collect();
        let ixs = || vec![
            IxView { path: "0".into(), program: Some(&dbc[..]), accounts: &dbc_accounts[..], data: &mig[..], parent: None },
            IxView { path: "1".into(), program: Some(&router[..]), accounts: &[6][..], data: &[0][..], parent: None },
            IxView { path: "1.0".into(), program: Some(&launchlab[..]), accounts: &ll_accounts[..], data: &cps[..], parent: Some(&router[..]) },
            IxView { path: "1.1".into(), program: Some(&launchlab[..]), accounts: &[6][..], data: &unknown[..], parent: Some(&launchlab[..]) },
        ];
        let view = |failed: bool| TxView { logs: &[], slot: 8, tx_index: 1, fee_payer: b58(&payer), failed, ixs: ixs(), pre_lamports: &[],
                                           post_lamports: &[], pre_tokens: vec![], post_tokens: vec![], keys: keys.clone() };
        let venues = Venues::new().unwrap();
        let out = venues.rows(&view(false));
        assert_eq!((out.graduations, out.events, out.unknown_events), (2, 0, 1));
        let got: Vec<Vec<Val>> = out.rows.iter().filter(|r| &*r.table == "venue_graduations").map(|r| ["venue", "ix_name", "from_pool", "to_venue", "to_pool", "base_mint", "quote_mint", "outer_ix", "inner_ix"]
            .iter().map(|c| col(r, c).clone()).collect()).collect();
        // The unknown event is kept to be named: its venue, discriminator and parent.
        let unk: Vec<&Row> = out.rows.iter().filter(|r| &*r.table == "venue_unknown_events").collect();
        assert_eq!(unk.len(), 1);
        assert_eq!((col(unk[0], "venue"), col(unk[0], "event_disc"), col(unk[0], "parent_ix_name"), col(unk[0], "inner_ix")),
                   (&st("raydium_launchlab"), &st("0909090909090909"), &st("migrate_to_cpswap"), &Val::U64(1)));
        assert_eq!(got, vec![
            vec![st("meteora_dbc"), st("migration_damm_v2"), st(&b58(&vpool)), st("meteora_damm_v2"), st(&b58(&dpool)), st(&b58(&base)), st(WSOL), Val::U64(0), Val::Null],
            vec![st("raydium_launchlab"), st("migrate_to_cpswap"), st(&b58(&lpool)), st("raydium_cpmm"), st(&b58(&cpool)), st(&b58(&base)), st(WSOL), Val::U64(1), Val::U64(0)],
        ]);
        // A failed transaction graduates nothing.
        assert_eq!(venues.rows(&view(true)).graduations, 0);
    }

    #[test]
    fn an_event_only_older_dbc_releases_know_gets_its_table() {
        // EvtClaimProtocolFee is in DBC 0.1.x, not in the current IDL: decoded with the release that has it.
        let old = Idl::parse(super::DBC_OLDER_IDLS[0].1).unwrap();
        let ev = old.events.iter().find(|e| e.name == "EvtClaimProtocolFee").expect("0.1.6 has EvtClaimProtocolFee");
        assert!(Idl::parse(DBC_IDL).unwrap().events.iter().all(|e| e.discriminator != ev.discriminator));
        let (payer, dbc) = (test_key(71, true), key("dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"));
        let data: Vec<u8> = EVENT_IX_TAG.iter().copied().chain(ev.discriminator).chain([0u8; 64]).collect();
        let keys: Vec<&[u8]> = vec![&payer[..], &dbc[..]];
        let view = TxView { logs: &[], slot: 3, tx_index: 0, fee_payer: b58(&payer), failed: false, keys, pre_lamports: &[], post_lamports: &[],
                            pre_tokens: vec![], post_tokens: vec![],
                            ixs: vec![IxView { path: "0.0".into(), program: Some(&dbc), accounts: &[1], data: &data, parent: Some(&dbc) }] };
        let out = Venues::new().unwrap().rows(&view);
        assert_eq!((out.events, out.unknown_events), (1, 0));
        assert_eq!(&*out.rows[0].table, "meteora_dbc/EvtClaimProtocolFee");
    }
}
