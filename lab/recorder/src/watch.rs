//! The watchlist and the subscription it becomes.
//!
//! Inputs (re-read when they change):
//! - `watchlist-current.json`, the watchlist builder's snapshot (LAB research, format v1):
//!   `{valid_for_minute, valid_from_slot, pools: [{pool, mint, rules}], wallets: [...], counts}`,
//!   optionally `vaults: [...]` (token accounts to follow by balance only);
//! - `open-pools.json`, written by the paper engine: pools with an open position, always kept.
//!
//! Plus the recorder's own probes: new pools it saw created with enough WSOL (or whose WSOL vault
//! reached the probe depth) get their own transaction filter for a few hours.
//!
//! Streams, in downscale order (the first is dropped first): `pool_accounts`, `pool_creations`,
//! `tx_new_reversed`, `new_vaults`, `tx_probes`, `vaults`, `tx_wallets`, `tx_watchlist` (watchlist
//! pools; the open-position pools stay). Blocks, slot statuses and `tx_new_pools` (pump.fun
//! graduations, ~0.003 GB/day) always stay; only the hard stop ends them.

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
pub const WSOL: &str = "So11111111111111111111111111111111111111112";
/// Pool bytes 271..287: `protocol_fees` and `creator_fees` (u64 each). Hypothesis, to verify in the
/// first measuring hour: both are 0 when a pool is created and grow with its first trade, so an
/// account stream asking for zeros there sees each new pool once (`pool_creations`).
pub const FRESH_FEES: (u64, usize) = (271, 16);
/// `tx_new_reversed`: every PumpSwap transaction without one of these generic accounts. They are
/// the accounts the creation-filter measurement (1052, windows a and b) excluded in both windows:
/// protocol and buyback fee recipients (in every trade, in no creation), Jupiter v6 and a few
/// routers/bots. ~7-8 GB/day with ~100% of direct creations (lab/checks/creation_filter.py).
pub const NEW_REVERSED_EXCLUDE: [&str; 15] = [
    "A7hAgCzFw14fejgCp387JUJRMNyz4j89JKnhtKU8piqW",
    "FWsW1xNtWscwNmKv6wVsU1iTzRN6wmmk3MjxRP5tT7hz",
    "94qWNrtmfn42h3ZjUZwWvK1MEo9uVmmrBPd2hpNjYDjb",
    "7hTckgnGnLQR6sdH7YkqFTAA7VwTfYFaZ6EhEsU3saCX",
    "AVmoTthdrX6tKt4nDjco2D775W2YK3sDhxPcMmzUAmTY",
    "9rPYyANsfQZw3DnDmKE3YCQF5E8oD89UXoHn9JFEhJUz",
    "GXPFM2caqTtQYC2cJ5yJRi9VDkpsYZXzYdwYpGnLmtDL",
    "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7",
    "5cjcW9wExnJJiqgLjq7DEG75Pm6JBgE1hNv4B2vHXUW6",
    "3BpXnfJaUTiwXnJNe7Ej1rcbzqTTQUvLShZaWazebsVR",
    "6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB",
    "DF1ow4tspfHX9JwWJsAb9epbkA8hmpSEAtxXy1V27QBH",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",
    "HTVZVEQMBsNanubDPTs3CxDAEGNFQHJY8c1441iy2S5r",
];

pub const DOWNSCALE: [&str; 8] =
    ["pool_accounts", "pool_creations", "tx_new_reversed", "new_vaults", "tx_probes", "vaults", "tx_wallets", "tx_watchlist"];

/// Which optional streams are configured (on before downscaling).
#[derive(Clone, Debug)]
pub struct Opts {
    pub pool_accounts: bool,
    pub new_pools: bool,
    pub pool_creations: bool,
    pub new_reversed: bool,
    pub max_tx_accounts: usize,
}

impl Default for Opts {
    fn default() -> Self {
        Self { pool_accounts: false, new_pools: true, pool_creations: false, new_reversed: false, max_tx_accounts: 10_000 }
    }
}

/// The recorder's own additions: probe pools (full transactions) and WSOL vaults of new pools.
#[derive(Clone, Debug, Default)]
pub struct Extra {
    pub probes: Vec<String>,
    pub fresh_vaults: Vec<String>,
}

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
    /// Probe pools that are not already in `tx_pools`.
    pub tx_probes: Vec<String>,
    pub tx_wallets: Vec<String>,
    pub vaults: Vec<String>,
    pub fresh_vaults: Vec<String>,
    pub pool_accounts: bool,
    pub pool_creations: bool,
    pub new_reversed: bool,
    /// Graduation transactions (new canonical pools for M1).
    pub new_pools: bool,
    pub level: usize,
    /// Addresses left out by `max_tx_accounts` (pools first, then probes, then wallets kept).
    pub truncated: u64,
}

impl Filter {
    pub fn new(w: &Watch, extra: &Extra, level: usize, o: &Opts) -> Self {
        let on = |stream: &str| DOWNSCALE.iter().position(|s| *s == stream).map_or(true, |i| i >= level);
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut take = |list: &[String]| -> Vec<String> { list.iter().filter(|p| seen.insert((*p).clone())).cloned().collect() };
        let mut pools = take(&w.open_pools);
        if on("tx_watchlist") {
            pools.extend(take(&w.pools));
        }
        let mut probes = if on("tx_probes") { take(&extra.probes) } else { Vec::new() };
        let mut wallets = if on("tx_wallets") { w.wallets.clone() } else { Vec::new() };
        let total = pools.len() + probes.len() + wallets.len();
        let max = o.max_tx_accounts;
        pools.truncate(max);
        probes.truncate(max - pools.len());
        wallets.truncate(max - pools.len() - probes.len());
        Filter {
            truncated: total.saturating_sub(max) as u64,
            tx_pools: pools,
            tx_probes: probes,
            tx_wallets: wallets,
            vaults: if on("vaults") { w.vaults.clone() } else { Vec::new() },
            fresh_vaults: if on("new_vaults") { extra.fresh_vaults.clone() } else { Vec::new() },
            pool_accounts: o.pool_accounts && on("pool_accounts"),
            pool_creations: o.pool_creations && on("pool_creations"),
            new_reversed: o.new_reversed && on("tx_new_reversed"),
            new_pools: o.new_pools,
            level,
        }
    }

    fn tx(accounts: &[String]) -> SubscribeRequestFilterTransactions {
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            account_include: accounts.to_vec(),
            ..Default::default()
        }
    }

    fn memcmp(offset: u64, bytes: Vec<u8>) -> SubscribeRequestFilterAccountsFilter {
        SubscribeRequestFilterAccountsFilter {
            filter: Some(AccountsFilterKind::Memcmp(SubscribeRequestFilterAccountsFilterMemcmp {
                offset,
                data: Some(MemcmpData::Bytes(bytes)),
            })),
        }
    }

    /// The main subscription: watchlist and probe transactions, graduations, optionally all
    /// PumpSwap transactions without the generic trade accounts (new reversed pools), block meta,
    /// confirmed slot statuses and, when on, the pool account streams. An address list is never
    /// sent empty (an empty `account_include` without `account_required` matches every
    /// transaction).
    pub fn request(&self, from_slot: Option<u64>) -> SubscribeRequest {
        let mut transactions = HashMap::new();
        if !self.tx_pools.is_empty() {
            transactions.insert("tx_pools".to_owned(), Self::tx(&self.tx_pools));
        }
        if !self.tx_probes.is_empty() {
            transactions.insert("tx_probes".to_owned(), Self::tx(&self.tx_probes));
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
        if self.new_reversed {
            transactions.insert(
                "tx_new_reversed".to_owned(),
                SubscribeRequestFilterTransactions {
                    vote: Some(false),
                    failed: Some(false),
                    account_include: vec![PUMP_AMM.to_owned()],
                    account_exclude: NEW_REVERSED_EXCLUDE.iter().map(|a| (*a).to_owned()).collect(),
                    ..Default::default()
                },
            );
        }
        let mut accounts = HashMap::new();
        if self.pool_accounts {
            accounts.insert(
                "pool_accounts".to_owned(),
                SubscribeRequestFilterAccounts {
                    owner: vec![PUMP_AMM.to_owned()],
                    filters: vec![Self::memcmp(0, POOL_DISC.to_vec())],
                    ..Default::default()
                },
            );
        }
        if self.pool_creations {
            accounts.insert(
                "pool_creations".to_owned(),
                SubscribeRequestFilterAccounts {
                    owner: vec![PUMP_AMM.to_owned()],
                    filters: vec![Self::memcmp(0, POOL_DISC.to_vec()), Self::memcmp(FRESH_FEES.0, vec![0u8; FRESH_FEES.1])],
                    ..Default::default()
                },
            );
        }
        // One slice for the whole subscription: both pool streams read the same bytes.
        let accounts_data_slice = if accounts.is_empty() {
            Vec::new()
        } else {
            vec![SubscribeRequestAccountsDataSlice { offset: POOL_SLICE.0, length: POOL_SLICE.1 }]
        };
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

    /// The vault subscription (its own stream on the same connection, for its own data slice):
    /// the watchlist's vaults and the WSOL vaults of new pools. With none it asks for nothing (the
    /// server only sends pings).
    pub fn vault_request(&self) -> SubscribeRequest {
        let mut accounts = HashMap::new();
        if !self.vaults.is_empty() {
            accounts.insert("vaults".to_owned(), SubscribeRequestFilterAccounts { account: self.vaults.clone(), ..Default::default() });
        }
        if !self.fresh_vaults.is_empty() {
            accounts.insert("new_vaults".to_owned(), SubscribeRequestFilterAccounts { account: self.fresh_vaults.clone(), ..Default::default() });
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
               "tx_pools": self.tx_pools.len(), "tx_probes": self.tx_probes.len(), "tx_wallets": self.tx_wallets.len(),
               "vaults": self.vaults.len(), "new_vaults": self.fresh_vaults.len(), "pool_accounts": self.pool_accounts,
               "pool_creations": self.pool_creations, "new_reversed": self.new_reversed, "new_pools": self.new_pools,
               "truncated": self.truncated})
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
    let all = |f: &Filter| -> Vec<String> { f.tx_pools.iter().chain(&f.tx_probes).cloned().collect() };
    let (pools, pools_partial) = both(&all(before), &all(now));
    let (wallets, wallets_partial) = both(&before.tx_wallets, &now.tx_wallets);
    json!({
        "pools": pools.iter().map(|p| json!({"pool": p})).collect::<Vec<_>>(),
        "wallets": wallets,
        "pools_partial": pools_partial,
        "wallets_partial": wallets_partial,
        "vaults": now.vaults,
        // Graduation transactions were filtered for the whole segment (paper.reconcile checks them).
        "new_pools": before.new_pools && now.new_pools,
        "new_reversed": before.new_reversed && now.new_reversed,
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

    fn opts(pool_accounts: bool, max: usize) -> Opts {
        Opts { pool_accounts, pool_creations: pool_accounts, new_reversed: pool_accounts, max_tx_accounts: max, ..Opts::default() }
    }

    fn level(i: usize) -> usize {
        DOWNSCALE.iter().position(|s| *s == ["pool_accounts", "pool_creations", "tx_new_reversed", "new_vaults", "tx_probes", "vaults", "tx_wallets", "tx_watchlist"][i]).unwrap()
    }

    #[test]
    fn downscale_drops_streams_in_order_and_keeps_open_pools() {
        let w = watch();
        let extra = Extra { probes: vec![A.to_owned(), C.to_owned()], fresh_vaults: vec![D.to_owned()] };
        let full = Filter::new(&w, &extra, 0, &opts(true, 10_000));
        assert_eq!(full.tx_pools, vec![B.to_owned(), A.to_owned()]); // open pool first, no duplicate
        assert_eq!(full.tx_probes, vec![C.to_owned()]); // A is already a watchlist pool
        assert!(full.pool_accounts && full.pool_creations && full.new_reversed);
        assert!(!full.vaults.is_empty() && !full.fresh_vaults.is_empty() && !full.tx_wallets.is_empty());
        let l3 = Filter::new(&w, &extra, level(2) + 1, &opts(true, 10_000));
        assert!(!l3.pool_accounts && !l3.pool_creations && !l3.new_reversed && !l3.fresh_vaults.is_empty());
        let l5 = Filter::new(&w, &extra, level(4) + 1, &opts(true, 10_000));
        assert!(l5.fresh_vaults.is_empty() && l5.tx_probes.is_empty() && !l5.vaults.is_empty());
        let last = Filter::new(&w, &extra, DOWNSCALE.len(), &opts(true, 10_000));
        assert_eq!(last.tx_pools, vec![B.to_owned()]);
        assert!(last.tx_wallets.is_empty() && last.vaults.is_empty() && last.new_pools);
        let capped = Filter::new(&w, &extra, 0, &opts(false, 3));
        assert_eq!((capped.tx_pools.len(), capped.tx_probes.len(), capped.tx_wallets.len(), capped.truncated), (2, 1, 0, 2));
    }

    #[test]
    fn requests_never_ask_for_everything() {
        let o = Opts { new_pools: false, ..Opts::default() };
        let empty = Filter::new(&Watch::default(), &Extra::default(), 0, &o);
        let r = empty.request(Some(7));
        assert!(r.transactions.is_empty() && r.accounts.is_empty() && r.accounts_data_slice.is_empty());
        assert_eq!((r.from_slot, r.commitment), (Some(7), Some(CommitmentLevel::Confirmed as i32)));
        assert!(empty.vault_request().accounts.is_empty());
        let extra = Extra { probes: vec![C.to_owned()], fresh_vaults: vec![A.to_owned()] };
        let full = Filter::new(&watch(), &extra, 0, &opts(true, 10_000));
        let r = full.request(None);
        for f in r.transactions.values() {
            assert!(!f.account_include.is_empty() || !f.account_required.is_empty());
            assert_eq!((f.vote, f.failed), (Some(false), Some(false)));
        }
        let np = &r.transactions["tx_new_pools"];
        assert_eq!(np.account_required, vec![PUMP_AMM.to_owned(), MIGRATION_AUTHORITY.to_owned()]);
        assert!(np.account_include.is_empty() && np.account_exclude.is_empty());
        let nr = &r.transactions["tx_new_reversed"];
        assert_eq!((nr.account_include.clone(), nr.account_exclude.len()), (vec![PUMP_AMM.to_owned()], NEW_REVERSED_EXCLUDE.len()));
        assert!(NEW_REVERSED_EXCLUDE.iter().all(|a| valid(a)));
        assert_eq!(r.transactions["tx_probes"].account_include, vec![C.to_owned()]);
        // Graduations stay at every downscale level.
        assert!(Filter::new(&watch(), &extra, DOWNSCALE.len(), &opts(true, 10_000)).request(None).transactions.contains_key("tx_new_pools"));
        let pa = &r.accounts["pool_accounts"];
        assert_eq!((pa.owner.clone(), pa.filters.len()), (vec![PUMP_AMM.to_owned()], 1));
        assert_eq!(r.accounts["pool_creations"].filters.len(), 2);
        assert_eq!(r.accounts_data_slice.len(), 1);
        let v = full.vault_request();
        assert_eq!((v.accounts["vaults"].account.clone(), v.accounts["new_vaults"].account.clone()), (vec![D.to_owned()], vec![A.to_owned()]));
    }

    #[test]
    fn the_segment_after_a_change_reconciles_only_what_both_filters_had() {
        let w = watch();
        let o = opts(false, 10_000);
        let before = Filter::new(&w, &Extra::default(), 0, &o);
        let mut w2 = w.clone();
        w2.pools = vec![A.to_owned(), D.to_owned()];
        let now = Filter::new(&w2, &Extra { probes: vec![C.to_owned()], ..Extra::default() }, 0, &o);
        let v = segment_watchlist(&before, &now, &Value::Null);
        let pools: Vec<&str> = v["pools"].as_array().unwrap().iter().map(|p| p["pool"].as_str().unwrap()).collect();
        assert_eq!(pools, vec![A, B]);
        assert_eq!(v["pools_partial"], json!([C, D]));
    }
}
