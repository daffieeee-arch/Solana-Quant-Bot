//! The watchlist and the subscription it becomes.
//!
//! Inputs (re-read when they change):
//! - `watchlist-current.json`, the watchlist builder's snapshot (LAB research, format v1):
//!   `{valid_for_minute, valid_from_slot, pools: [{pool, mint, rules}], wallets: [...], counts}`,
//!   optionally `vaults: [...]` (token accounts to follow by balance only);
//! - `open-pools.json`, written by the paper engine: pools with an open position, always kept.
//!
//! Streams, in downscale order (the first is dropped first): `pool_accounts`, `vaults`,
//! `tx_wallets`, `tx_watchlist` (watchlist pools; the open-position pools stay). Blocks, slot
//! statuses and `tx_new_pools` (pump.fun graduations, ~0.003 GB/day) always stay; only the hard
//! stop ends them.

use serde_json::{json, Value};
use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::time::SystemTime;
use yellowstone_grpc_proto::prelude::{
    subscribe_request_filter_accounts_filter::Filter as AccountsFilterKind,
    subscribe_request_filter_accounts_filter_memcmp::Data as MemcmpData, CommitmentLevel, SubscribeRequest,
    SubscribeRequestAccountsDataSlice, SubscribeRequestFilterAccounts, SubscribeRequestFilterAccountsFilter,
    SubscribeRequestFilterAccountsFilterMemcmp, SubscribeRequestFilterBlocksMeta, SubscribeRequestFilterSlots,
    SubscribeRequestFilterTransactions,
};

pub const PUMP_AMM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
/// Anchor discriminator of the PumpSwap `Pool` account (IDL accounts[Pool]).
pub const POOL_DISC: [u8; 8] = [241, 154, 109, 4, 17, 177, 109, 188];
/// Pool account bytes 43..244: base_mint, quote_mint, lp_mint, the two vaults, lp_supply,
/// coin_creator, is_mayhem_mode (Borsh layout of the IDL's Pool type after the discriminator).
pub const POOL_SLICE: (u64, u64) = (43, 201);
/// pump.fun's withdraw authority: in every graduation (migrate to PumpSwap) and in no other
/// PumpSwap transaction (lab/checks/creation_filter.py on 1052, two windows of 100k slots: 100% of
/// migration/sol and migration/other, 0 other transactions). Direct create_pool calls have no
/// such account; they would cost ~8-56 GB/day to catch.
pub const MIGRATION_AUTHORITY: &str = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg";
/// SPL token account `amount` (both token programs).
pub const VAULT_SLICE: (u64, u64) = (64, 8);

pub const DOWNSCALE: [&str; 4] = ["pool_accounts", "vaults", "tx_wallets", "tx_watchlist"];

fn valid(key: &str) -> bool {
    bs58::decode(key).into_vec().is_ok_and(|v| v.len() == 32)
}

/// Addresses of a JSON list of strings or objects (the first present of `fields`).
fn addresses(v: &Value, fields: &[&str], invalid: &mut u64) -> Vec<String> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for item in v.as_array().map(Vec::as_slice).unwrap_or(&[]) {
        let a = match item {
            Value::String(s) => Some(s.as_str()),
            Value::Object(o) => fields.iter().find_map(|f| o.get(*f).and_then(Value::as_str)),
            _ => None,
        };
        match a {
            Some(a) if valid(a) => {
                if seen.insert(a.to_owned()) {
                    out.push(a.to_owned());
                }
            }
            _ => *invalid += 1,
        }
    }
    out
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Watch {
    pub open_pools: Vec<String>,
    pub pools: Vec<String>,
    pub wallets: Vec<String>,
    pub vaults: Vec<String>,
    /// valid_for_minute, valid_from_slot and counts of the watchlist file.
    pub source: Value,
    pub invalid: u64,
}

impl Watch {
    pub fn parse(watchlist: Option<&Value>, open_pools: Option<&Value>) -> Self {
        let mut w = Watch::default();
        if let Some(v) = watchlist {
            w.pools = addresses(&v["pools"], &["pool"], &mut w.invalid);
            w.wallets = addresses(&v["wallets"], &["wallet", "address"], &mut w.invalid);
            w.vaults = addresses(&v["vaults"], &["vault", "account"], &mut w.invalid);
            w.source = json!({"valid_for_minute": v["valid_for_minute"], "valid_from_slot": v["valid_from_slot"], "counts": v["counts"]});
        }
        if let Some(v) = open_pools {
            w.open_pools = addresses(&v["pools"], &["pool"], &mut w.invalid);
        }
        w
    }
}

/// The two input files, re-read when their modification time changes.
pub struct Files {
    pub watchlist: PathBuf,
    pub open_pools: PathBuf,
    mtimes: (Option<SystemTime>, Option<SystemTime>),
}

fn read_json(path: &Path) -> Option<Value> {
    let bytes = std::fs::read(path).ok()?;
    match serde_json::from_slice(&bytes) {
        Ok(v) => Some(v),
        Err(e) => {
            log::warn!("{} is not valid JSON ({e}); keeping the previous watchlist", path.display());
            None
        }
    }
}

impl Files {
    pub fn new(watchlist: PathBuf, open_pools: PathBuf) -> Self {
        Self { watchlist, open_pools, mtimes: (None, None) }
    }

    fn mtime(p: &Path) -> Option<SystemTime> {
        std::fs::metadata(p).and_then(|m| m.modified()).ok()
    }

    /// The watchlist when a file changed since the last call (None: unchanged or unreadable).
    pub fn reload(&mut self) -> Option<Watch> {
        let now = (Self::mtime(&self.watchlist), Self::mtime(&self.open_pools));
        if now == self.mtimes {
            return None;
        }
        let w = read_json(&self.watchlist);
        if w.is_none() && now.0.is_some() {
            return None; // present but unreadable (being written): try again later
        }
        self.mtimes = now;
        Some(Watch::parse(w.as_ref(), read_json(&self.open_pools).as_ref()))
    }
}

/// What the subscription asks for at a downscale level.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Filter {
    /// Open-position pools first, then watchlist pools.
    pub tx_pools: Vec<String>,
    pub tx_wallets: Vec<String>,
    pub vaults: Vec<String>,
    pub pool_accounts: bool,
    /// Graduation transactions (new canonical pools for M1).
    pub new_pools: bool,
    pub level: usize,
    /// Addresses left out by `max_tx_accounts`.
    pub truncated: u64,
}

impl Filter {
    pub fn new(w: &Watch, level: usize, pool_accounts: bool, new_pools: bool, max_tx_accounts: usize) -> Self {
        let on = |stream: &str| DOWNSCALE.iter().position(|s| *s == stream).map_or(true, |i| i >= level);
        let mut pools: Vec<String> = w.open_pools.clone();
        if on("tx_watchlist") {
            let open: BTreeSet<&String> = w.open_pools.iter().collect();
            pools.extend(w.pools.iter().filter(|p| !open.contains(p)).cloned());
        }
        let wallets = if on("tx_wallets") { w.wallets.clone() } else { Vec::new() };
        let total = pools.len() + wallets.len();
        let mut f = Filter {
            truncated: total.saturating_sub(max_tx_accounts) as u64,
            vaults: if on("vaults") { w.vaults.clone() } else { Vec::new() },
            pool_accounts: pool_accounts && on("pool_accounts"),
            new_pools,
            level,
            tx_pools: pools,
            tx_wallets: wallets,
        };
        f.tx_pools.truncate(max_tx_accounts);
        f.tx_wallets.truncate(max_tx_accounts - f.tx_pools.len());
        f
    }

    fn tx(accounts: &[String]) -> SubscribeRequestFilterTransactions {
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            account_include: accounts.to_vec(),
            ..Default::default()
        }
    }

    /// The main subscription: watchlist transactions, graduations, block meta, confirmed slot
    /// statuses and, when on, the pool account stream. An empty address list never becomes a
    /// filter (an empty `account_include` without `account_required` matches every transaction).
    pub fn request(&self, from_slot: Option<u64>) -> SubscribeRequest {
        let mut transactions = HashMap::new();
        if !self.tx_pools.is_empty() {
            transactions.insert("tx_pools".to_owned(), Self::tx(&self.tx_pools));
        }
        if !self.tx_wallets.is_empty() {
            transactions.insert("tx_wallets".to_owned(), Self::tx(&self.tx_wallets));
        }
        if self.new_pools {
            transactions.insert(
                "tx_new_pools".to_owned(),
                SubscribeRequestFilterTransactions {
                    vote: Some(false),
                    failed: Some(false),
                    account_required: vec![PUMP_AMM.to_owned(), MIGRATION_AUTHORITY.to_owned()],
                    ..Default::default()
                },
            );
        }
        let mut accounts = HashMap::new();
        let mut accounts_data_slice = Vec::new();
        if self.pool_accounts {
            accounts.insert(
                "pool_accounts".to_owned(),
                SubscribeRequestFilterAccounts {
                    owner: vec![PUMP_AMM.to_owned()],
                    filters: vec![SubscribeRequestFilterAccountsFilter {
                        filter: Some(AccountsFilterKind::Memcmp(SubscribeRequestFilterAccountsFilterMemcmp {
                            offset: 0,
                            data: Some(MemcmpData::Bytes(POOL_DISC.to_vec())),
                        })),
                    }],
                    ..Default::default()
                },
            );
            accounts_data_slice.push(SubscribeRequestAccountsDataSlice { offset: POOL_SLICE.0, length: POOL_SLICE.1 });
        }
        SubscribeRequest {
            transactions,
            accounts,
            accounts_data_slice,
            blocks_meta: HashMap::from([("blocks".to_owned(), SubscribeRequestFilterBlocksMeta {})]),
            slots: HashMap::from([(
                "slots".to_owned(),
                SubscribeRequestFilterSlots { filter_by_commitment: Some(true), interslot_updates: Some(false) },
            )]),
            commitment: Some(CommitmentLevel::Confirmed as i32),
            from_slot,
            ..Default::default()
        }
    }

    /// The vault subscription (its own stream on the same connection, for its own data slice).
    /// With no vaults it asks for nothing (the server only sends pings).
    pub fn vault_request(&self) -> SubscribeRequest {
        let mut accounts = HashMap::new();
        if !self.vaults.is_empty() {
            accounts.insert("vaults".to_owned(), SubscribeRequestFilterAccounts { account: self.vaults.clone(), ..Default::default() });
        }
        SubscribeRequest {
            accounts,
            accounts_data_slice: vec![SubscribeRequestAccountsDataSlice { offset: VAULT_SLICE.0, length: VAULT_SLICE.1 }],
            commitment: Some(CommitmentLevel::Confirmed as i32),
            ..Default::default()
        }
    }

    pub fn summary(&self) -> Value {
        json!({"level": self.level, "dropped": &DOWNSCALE[..self.level.min(DOWNSCALE.len())],
               "tx_pools": self.tx_pools.len(), "tx_wallets": self.tx_wallets.len(), "vaults": self.vaults.len(),
               "pool_accounts": self.pool_accounts, "new_pools": self.new_pools, "truncated": self.truncated})
    }
}

/// `watchlist.json` of a segment: the pools and wallets whose transactions were filtered for the
/// whole segment (the reconciliation compares only those). In the segment after a change that is
/// the intersection of the old and new filter; the rest is listed as partial.
pub fn segment_watchlist(before: &Filter, now: &Filter, source: &Value) -> Value {
    let both = |a: &[String], b: &[String]| -> (Vec<String>, Vec<String>) {
        let a: BTreeSet<&String> = a.iter().collect();
        let b: BTreeSet<&String> = b.iter().collect();
        (a.intersection(&b).map(|s| (*s).clone()).collect(), a.symmetric_difference(&b).map(|s| (*s).clone()).collect())
    };
    let (pools, pools_partial) = both(&before.tx_pools, &now.tx_pools);
    let (wallets, wallets_partial) = both(&before.tx_wallets, &now.tx_wallets);
    json!({
        "pools": pools.iter().map(|p| json!({"pool": p})).collect::<Vec<_>>(),
        "wallets": wallets,
        "pools_partial": pools_partial,
        "wallets_partial": wallets_partial,
        "vaults": now.vaults,
        // Graduation transactions were filtered for the whole segment (paper.reconcile checks them).
        "new_pools": before.new_pools && now.new_pools,
        "filter": now.summary(),
        "source": source,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: &str = "So11111111111111111111111111111111111111112";
    const B: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
    const C: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
    const D: &str = "ComputeBudget111111111111111111111111111111";

    fn watch() -> Watch {
        Watch::parse(
            Some(&json!({"valid_for_minute": 5, "pools": [{"pool": A, "mint": C, "rules": ["F7"]}, {"pool": "bad"}, {"pool": B}],
                         "wallets": [C, {"wallet": D}], "vaults": [D], "counts": {"pools": 2}})),
            Some(&json!({"pools": [B]})),
        )
    }

    #[test]
    fn the_builder_format_is_read_and_bad_addresses_skipped() {
        let w = watch();
        assert_eq!((w.pools.len(), w.wallets.len(), w.vaults.len(), w.open_pools.len(), w.invalid), (2, 2, 1, 1, 1));
        assert_eq!(w.source["valid_for_minute"], 5);
    }

    #[test]
    fn downscale_drops_streams_in_order_and_keeps_open_pools() {
        let w = watch();
        let full = Filter::new(&w, 0, true, true, 10_000);
        assert_eq!(full.tx_pools, vec![B.to_owned(), A.to_owned()]); // open pool first, no duplicate
        assert!(full.pool_accounts && !full.vaults.is_empty() && !full.tx_wallets.is_empty());
        let l2 = Filter::new(&w, 2, true, true, 10_000);
        assert!(!l2.pool_accounts && l2.vaults.is_empty() && !l2.tx_wallets.is_empty());
        let l4 = Filter::new(&w, 4, true, true, 10_000);
        assert_eq!(l4.tx_pools, vec![B.to_owned()]);
        assert!(l4.tx_wallets.is_empty());
        let capped = Filter::new(&w, 0, false, false, 3);
        assert_eq!((capped.tx_pools.len() + capped.tx_wallets.len(), capped.truncated), (3, 1));
    }

    #[test]
    fn requests_never_ask_for_everything() {
        let empty = Filter::new(&Watch::default(), 0, false, false, 10_000);
        let r = empty.request(Some(7));
        assert!(r.transactions.is_empty() && r.accounts.is_empty());
        assert_eq!((r.from_slot, r.commitment), (Some(7), Some(CommitmentLevel::Confirmed as i32)));
        assert!(empty.vault_request().accounts.is_empty());
        let full = Filter::new(&watch(), 0, true, true, 10_000);
        let r = full.request(None);
        for f in r.transactions.values() {
            assert!(!f.account_include.is_empty() || !f.account_required.is_empty());
            assert_eq!((f.vote, f.failed), (Some(false), Some(false)));
        }
        let np = &r.transactions["tx_new_pools"];
        assert_eq!(np.account_required, vec![PUMP_AMM.to_owned(), MIGRATION_AUTHORITY.to_owned()]);
        assert!(np.account_include.is_empty() && np.account_exclude.is_empty());
        // Graduations stay at every downscale level.
        assert!(Filter::new(&watch(), 4, true, true, 10_000).request(None).transactions.contains_key("tx_new_pools"));
        let pa = &r.accounts["pool_accounts"];
        assert_eq!(pa.owner, vec![PUMP_AMM.to_owned()]);
        assert!(!pa.filters.is_empty());
        assert_eq!(r.accounts_data_slice.len(), 1);
        assert_eq!(full.vault_request().accounts["vaults"].account, vec![D.to_owned()]);
    }

    #[test]
    fn the_segment_after_a_change_reconciles_only_what_both_filters_had() {
        let w = watch();
        let before = Filter::new(&w, 0, false, true, 10_000);
        let mut w2 = w.clone();
        w2.pools = vec![A.to_owned(), D.to_owned()];
        let now = Filter::new(&w2, 0, false, true, 10_000);
        let v = segment_watchlist(&before, &now, &Value::Null);
        let pools: Vec<&str> = v["pools"].as_array().unwrap().iter().map(|p| p["pool"].as_str().unwrap()).collect();
        assert_eq!(pools, vec![A, B]);
        assert_eq!(v["pools_partial"], json!([D]));
    }
}
