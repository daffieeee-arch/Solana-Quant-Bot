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
// spec-token-transfers-2026-10-15.md v2 (LAB research, sha256 b17d9306): token_deltas,
// token_transfers and sol_funding, for a measurable insider share and funder graphs. Extractor only
// (LAB_TRANSFERS=1 with LAB_PUMP_MINTS); the recorder includes this file but never enables them.

const TOKEN_PROGRAM: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const WSOL_MINT: &str = "So11111111111111111111111111111111111111112";
/// sol_funding: the smallest SOL funding of a new wallet that counts (0.001 SOL).
pub const FUNDING_MIN_LAMPORTS: u64 = 1_000_000;

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
    // The owner is a program-derived address (off the ed25519 curve): a curve or pool vault.
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
    // Invoked by a DEX or router: the trade itself (the store has it), not a graph edge.
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
}

/// The extractor's token transfer tables (LAB_TRANSFERS=1): the pump.fun mint set and the columns.
pub struct Transfers {
    mints: std::collections::HashSet<String>,
    /// Mints created in the chunk (CreateEvent), learned while extracting.
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

    /// A pump.fun mint: in the store's set, created in this chunk, or with the pump.fun "pump" suffix
    /// (most mints; a superset that also takes rare vanity mints of other launchpads).
    pub fn is_pump(&self, mint: &str) -> bool {
        mint.ends_with("pump") || self.mints.contains(mint) || self.learned.read().map_or(false, |l| l.contains(mint))
    }

    fn learn(&self, rows: &[Row]) {
        for r in rows.iter().filter(|r| &*r.table == "pump/CreateEvent") {
            if let Some(i) = r.columns.iter().position(|(n, _)| n == "mint") {
                if let Some(Val::Str(m)) = r.values.get(i) {
                    if let Ok(mut l) = self.learned.write() {
                        l.insert(m.clone());
                    }
                }
            }
        }
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
            for (j, inner) in group.instructions.iter().enumerate() {
                // The invoker: the nearest earlier instruction one level up; the top-level one at
                // height 2 or without heights.
                let parent = match inner.stack_height {
                    Some(h) if h > 2 => group.instructions[..j]
                        .iter()
                        .rev()
                        .find(|p| p.stack_height == Some(h - 1))
                        .and_then(|p| key(p.instruction.program_id_index)),
                    _ => outer_program,
                };
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
    }
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
    /// token_deltas, token_transfers and sol_funding (LAB_TRANSFERS=1; the extractor only).
    pub transfers: Option<Transfers>,
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
            transfers: Transfers::from_env()?,
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
    /// not touch pump.fun); the other tables are exactly as without them.
    pub fn rows_for(&self, tx: &TransactionData) -> Vec<Row> {
        let mut rows = self.core_rows(tx);
        if let Some(t) = &self.transfers {
            let extra = t.rows(&tx_view(tx), &rows, &self.labels);
            rows.extend(extra);
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
        parse_token_ix, store_legs, summarize_calls, system_non_wallet_target, Bal, Class, IxView, TokenIx, Transfers, TxView,
        FUNDING_MIN_LAMPORTS, PROGRAM_LABELS, PUMP_AMM_IDL, PUMP_IDL, TOKEN_METADATA_INIT, WSOL_MINT,
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
        let tx = TxView {
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
        let ok = TxView { slot: 1, tx_index: 0, fee_payer: b58(&payer), failed: false, keys: keys.clone(), ixs: ixs(), pre_lamports: &pre,
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
        let tx = TxView { slot: 1, tx_index: 0, fee_payer: String::new(), failed: false, keys: vec![], ixs: vec![], pre_lamports: &[], post_lamports: &[],
                          pre_tokens: vec![bal(0, "Other", "A", 5)], post_tokens: vec![bal(0, "Other", "A", 1)] };
        assert!(t.rows(&tx, &[created], &Default::default()).is_empty()); // "Other" is no pump mint
        assert!(t.is_pump("NewMint"));
    }
}

