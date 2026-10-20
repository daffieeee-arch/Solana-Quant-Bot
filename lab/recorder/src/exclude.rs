//! Learned exclusions for `tx_new_reversed` (creation-filters spec v0.2 P1, M3, S4).
//!
//! Every decoded PumpSwap trade names its protocol fee recipient and, when set, its buyback fee recipient
//! (`ix_accounts`). They are in nearly every trade and in no creation, so excluding them leaves the stream its
//! creations and the rest. The measuring hour of 2026-10-20: 12 protocol and 310 buyback recipients named all 52,787
//! trades on `tx_new_reversed`, the 64 most frequent 99.0 % of them. The recipients' token accounts (the spec's third
//! role) named no trade the two wallets did not, and took places in the cap (97.4 % with them): they are not learned.
//!
//! A candidate enters the set only if (a) it is no guard account, (b) no creation transaction this recorder received
//! holds it (its full keys, static and loaded) and (c) it is not in `creation_accounts.txt` (every account of the
//! forward store's reversed creations, 1053-1054; its sha256 and address count are in the start note and in each
//! segment's `learned_excludes`). Without that file nothing is learned (fail-closed: the static list only).
//!
//! What rule (b) can and cannot see (LAB review on #476): a learned member in a direct create_pool transaction makes
//! the server leave that transaction out of `tx_new_reversed`, so the recorder never receives it there and (b) cannot
//! fire for it. (b) catches the creations that arrive on the other streams: `tx_new_pools` (graduations), the probe and
//! watchlist streams, and backfills. For direct creations the guard is (a) and (c), the measuring hour's fixture (none of
//! its recipients was a creation account), and the deploy's checks: `paper.reconcile` against OF1 (spec M2) and the
//! `pool_creations` cross-check of the second measuring hour.
//! The set holds up to 64 candidates by score (trades, halving every 6 h); a member leaves when an outsider scores
//! twice its own, when a creation names it, or after 7 days without a trade. It is kept in
//! `<root>/new_reversed_exclude.json` (written atomically), so a restart keeps it.

use crate::segment::write_json_atomic;
use crate::watch::{valid, PUMP_AMM};
use anyhow::{Context, Result};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::path::{Path, PathBuf};

pub const CAP: usize = 64;
pub const EXPIRE_SECS: u64 = 7 * 86_400;
pub const HALF_LIFE_SECS: f64 = 6.0 * 3600.0;
/// A member is replaced only by an outsider scoring this many times its own (no churn between near equals).
pub const SWAP_FACTOR: f64 = 2.0;
/// Candidates tracked at most; past it the lowest scores (not members) go, down to half.
pub const MAX_CANDIDATES: usize = 4096;
/// Rule (b)'s keys kept at most; past it nothing new is learned this run (fail-closed), the members stay.
pub const MAX_RUN_CREATION_KEYS: usize = 1_000_000;
pub const FILE: &str = "new_reversed_exclude.json";
/// The `ix_accounts` roles learned from a trade.
pub const ROLES: [&str; 2] = ["protocol_fee_recipient", "buyback_fee_recipient"];
/// Rule (a): never excluded (programs, and PumpSwap accounts in every creation).
pub const GUARD: [&str; 8] = [
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ComputeBudget111111111111111111111111111111",
    PUMP_AMM,
    // PumpSwap's global_config and event_authority (in each of the measuring hour's 185 creations)
    "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw",
    "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR",
];

#[derive(Clone, Debug, PartialEq)]
struct Cand {
    score: f64,
    /// When `score` was last brought up to date (unix seconds).
    at: u64,
    last_seen: u64,
}

impl Cand {
    fn score_at(&self, now: u64) -> f64 {
        self.score * 0.5f64.powf(now.saturating_sub(self.at) as f64 / HALF_LIFE_SECS)
    }
}

pub struct Excludes {
    path: Option<PathBuf>,
    /// Rule (c)'s accounts; None: learning is off.
    store_creations: Option<HashSet<String>>,
    /// The sha256 of rule (c)'s file as read (the provable input).
    store_sha256: Option<String>,
    run_creations: HashSet<String>,
    /// MAX_RUN_CREATION_KEYS reached: nothing new is learned this run.
    full: bool,
    cands: BTreeMap<String, Cand>,
    members: BTreeSet<String>,
}

impl Excludes {
    /// Learning off: nothing is learned and nothing written; the list stays empty.
    pub fn off() -> Self {
        Self {
            path: None,
            store_creations: None,
            store_sha256: None,
            run_creations: HashSet::new(),
            full: false,
            cands: BTreeMap::new(),
            members: BTreeSet::new(),
        }
    }

    /// `creation_accounts`: rule (c)'s file. `root`: where the set is kept. The notes say why learning is off, or
    /// what a start kept (the caller logs them).
    pub fn open(root: &Path, creation_accounts: &Path, now: u64) -> (Self, Vec<String>) {
        let (store, sha256) = match read_accounts(creation_accounts) {
            Ok(s) => s,
            Err(e) => return (Self::off(), vec![format!("learned new_reversed excludes off: {e:#}")]),
        };
        let mut ex = Self { path: Some(root.join(FILE)), store_creations: Some(store), store_sha256: Some(sha256), ..Self::off() };
        let input = format!("{} creation accounts from {} (sha256 {})", ex.store_len(), creation_accounts.display(), ex.store_sha256.as_deref().unwrap_or("-"));
        let note = match ex.load(now) {
            Ok(n) => format!("learned new_reversed excludes on: {n} kept from {FILE}; {input}"),
            Err(e) => format!("learned new_reversed excludes start empty: {e:#}; {input}"),
        };
        (ex, vec![note])
    }

    pub fn enabled(&self) -> bool {
        self.store_creations.is_some()
    }

    fn store_len(&self) -> usize {
        self.store_creations.as_ref().map_or(0, HashSet::len)
    }

    /// The saved set and candidates: those expired or no longer allowed (a new creation_accounts.txt) are dropped.
    fn load(&mut self, now: u64) -> Result<usize> {
        let path = self.path.clone().context("no root")?;
        let text = match std::fs::read(&path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(0),
            Err(e) => return Err(e).with_context(|| format!("read {}", path.display())),
        };
        let v: Value = serde_json::from_slice(&text).with_context(|| format!("parse {}", path.display()))?;
        for (a, c) in v["candidates"].as_object().into_iter().flatten() {
            let (Some(score), Some(at), Some(last_seen)) = (c["score"].as_f64(), c["at"].as_u64(), c["last_seen"].as_u64()) else {
                continue;
            };
            if self.allowed(a) && last_seen + EXPIRE_SECS > now {
                self.cands.insert(a.clone(), Cand { score, at, last_seen });
            }
        }
        for a in v["members"].as_array().into_iter().flatten().filter_map(Value::as_str) {
            if self.cands.contains_key(a) && self.members.len() < CAP {
                self.members.insert(a.to_owned());
            }
        }
        Ok(self.members.len())
    }

    /// Rules (a), (b) and (c).
    fn allowed(&self, a: &str) -> bool {
        !GUARD.contains(&a) && !self.run_creations.contains(a) && self.store_creations.as_ref().is_some_and(|s| !s.contains(a))
    }

    /// Rule (b): the full account keys of a creation transaction of this run. A candidate or member among them leaves
    /// for this run; true when a member left (the caller resubscribes at once).
    pub fn on_creation<I: IntoIterator<Item = String>>(&mut self, keys: I) -> bool {
        if !self.enabled() {
            return false;
        }
        let mut left = false;
        for k in keys {
            self.cands.remove(&k);
            left |= self.members.remove(&k);
            if self.run_creations.len() < MAX_RUN_CREATION_KEYS {
                self.run_creations.insert(k);
            } else if !self.run_creations.contains(&k) {
                self.full = true; // an unknown creation key can no longer be kept: learn nothing new
            }
        }
        left
    }

    /// A decoded trade's `ix_accounts` (JSON): each recipient the rules allow scores one.
    pub fn on_trade(&mut self, ix_accounts: &str, now: u64) {
        if !self.enabled() || self.full {
            return;
        }
        let Ok(v) = serde_json::from_str::<Value>(ix_accounts) else {
            return;
        };
        for role in ROLES {
            if let Some(a) = v[role].as_str() {
                self.learn(a, now);
            }
        }
    }

    fn learn(&mut self, a: &str, now: u64) {
        if !self.allowed(a) {
            return;
        }
        let c = self.cands.entry(a.to_owned()).or_insert(Cand { score: 0.0, at: now, last_seen: now });
        c.score = c.score_at(now) + 1.0;
        c.at = now;
        c.last_seen = now;
        if self.cands.len() > MAX_CANDIDATES {
            self.prune(now);
        }
    }

    /// Down to half of MAX_CANDIDATES: the lowest scores that are not members go.
    fn prune(&mut self, now: u64) {
        let mut low: Vec<(f64, String)> =
            self.cands.iter().filter(|(a, _)| !self.members.contains(*a)).map(|(a, c)| (c.score_at(now), a.clone())).collect();
        low.sort_by(|x, y| x.0.total_cmp(&y.0).then_with(|| x.1.cmp(&y.1)));
        let excess = self.cands.len().saturating_sub(MAX_CANDIDATES / 2);
        for (_, a) in low.into_iter().take(excess) {
            self.cands.remove(&a);
        }
    }

    /// At most once per rate-limit window (the caller's): expired candidates leave, free places are filled by score,
    /// and a member is replaced by an outsider scoring SWAP_FACTOR times its own. The note of a change, else None.
    pub fn update(&mut self, now: u64) -> Option<String> {
        if !self.enabled() {
            return None;
        }
        let before = self.members.clone();
        self.cands.retain(|_, c| c.last_seen + EXPIRE_SECS > now);
        let cands = &self.cands;
        self.members.retain(|a| cands.contains_key(a));
        let mut rest: Vec<(f64, String)> =
            self.cands.iter().filter(|(a, _)| !self.members.contains(*a)).map(|(a, c)| (c.score_at(now), a.clone())).collect();
        rest.sort_by(|x, y| y.0.total_cmp(&x.0).then_with(|| x.1.cmp(&y.1))); // best first, then by address
        let mut rest = rest.into_iter().peekable();
        while self.members.len() < CAP {
            match rest.next() {
                Some((_, a)) => {
                    self.members.insert(a);
                }
                None => break,
            }
        }
        while let Some(&(best, _)) = rest.peek() {
            let weakest = self
                .members
                .iter()
                .map(|a| (self.cands[a].score_at(now), a.clone()))
                .min_by(|x, y| x.0.total_cmp(&y.0).then_with(|| x.1.cmp(&y.1)));
            let Some((low, out)) = weakest else { break };
            if best <= SWAP_FACTOR * low {
                break;
            }
            let (_, a) = rest.next().expect("peeked");
            self.members.remove(&out);
            self.members.insert(a);
        }
        let added = self.members.difference(&before).count();
        let removed = before.difference(&self.members).count();
        (added + removed > 0).then(|| format!("new_reversed excludes: {} learned (+{added} -{removed})", self.members.len()))
    }

    /// The learned set, sorted (the request's order).
    pub fn list(&self) -> Vec<String> {
        self.members.iter().cloned().collect()
    }

    pub fn summary(&self) -> Value {
        let input = self.store_sha256.as_ref().map(|sha| json!({"count": self.store_len(), "sha256": sha}));
        json!({"enabled": self.enabled(), "learned": self.members.len(), "candidates": self.cands.len(),
               "run_creation_keys": self.run_creations.len(), "full": self.full, "creation_accounts": input})
    }

    /// Written atomically (nothing when learning is off).
    pub fn save(&self, now: u64) -> Result<()> {
        let Some(path) = &self.path else { return Ok(()) };
        let cands: serde_json::Map<String, Value> =
            self.cands.iter().map(|(a, c)| (a.clone(), json!({"score": c.score, "at": c.at, "last_seen": c.last_seen}))).collect();
        write_json_atomic(path, &json!({"written_unix": now, "members": self.list(), "candidates": cands}))
    }
}

/// `creation_accounts.txt`: one address per line; blank lines and `#` comments are skipped. Its addresses and the sha256
/// of its bytes (the input as read, for the start note and each segment). Refused: an unreadable file, one that is not
/// UTF-8, one without an address (an empty input is no evidence) and a line that is no address.
pub fn read_accounts(path: &Path) -> Result<(HashSet<String>, String)> {
    let bytes = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    let sha256 = hex::encode(Sha256::digest(&bytes));
    let text = String::from_utf8(bytes).with_context(|| format!("{} is not UTF-8", path.display()))?;
    let mut set = HashSet::new();
    for (i, line) in text.lines().map(str::trim).enumerate() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        anyhow::ensure!(valid(line), "{} line {}: not an address", path.display(), i + 1);
        set.insert(line.to_owned());
    }
    anyhow::ensure!(!set.is_empty(), "{} holds no address", path.display());
    Ok((set, sha256))
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: u64 = 1_792_454_400; // 2026-10-20 00:00 UTC

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lab-recorder-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// A valid address per number (32 bytes: n and 31 sevens).
    fn addr(n: u8) -> String {
        let mut k = vec![7u8; 32];
        k[0] = n;
        bs58::encode(k).into_string()
    }

    /// Learning on, with rule (c)'s file holding `store`.
    fn on(root: &Path, store: &[String], now: u64) -> Excludes {
        let f = root.join("creation_accounts.txt");
        std::fs::write(&f, format!("# test\n\n{}\n", store.join("\n"))).unwrap();
        let (ex, notes) = Excludes::open(root, &f, now);
        assert!(ex.enabled() && notes[0].starts_with("learned new_reversed excludes on:"), "{notes:?}");
        ex
    }

    fn trade(protocol: &str, buyback: Option<&str>) -> String {
        let mut v = json!({"protocol_fee_recipient": protocol, "protocol_fee_recipient_token_account": addr(250), "pool": addr(251)});
        if let Some(b) = buyback {
            v["buyback_fee_recipient"] = json!(b);
        }
        v.to_string()
    }

    #[test]
    fn the_three_rules_keep_guard_creation_and_store_accounts_out() {
        let root = tmp("ex-rules");
        let mut ex = on(&root, &[addr(3)], T0);
        ex.on_creation([addr(2), addr(9)]);
        // (a) the system program as protocol recipient (buyback trades), (b) a key of this run's creation, (c) the store's;
        // addr(1) and addr(4) are fine; the token account (addr 250) is no role
        for _ in 0..3 {
            ex.on_trade(&trade("11111111111111111111111111111111", Some(&addr(1))), T0);
            ex.on_trade(&trade(&addr(2), Some(&addr(3))), T0);
            ex.on_trade(&trade(&addr(4), None), T0);
        }
        ex.on_trade("not json", T0);
        assert_eq!(ex.update(T0 + 60).as_deref(), Some("new_reversed excludes: 2 learned (+2 -0)"));
        assert_eq!(ex.list(), {
            let mut l = vec![addr(1), addr(4)];
            l.sort();
            l
        });
        // a creation naming a member: it leaves at once, for this run
        assert!(ex.on_creation([addr(4)]));
        assert!(!ex.on_creation([addr(4)]));
        ex.on_trade(&trade(&addr(4), None), T0 + 61);
        assert_eq!(ex.list(), vec![addr(1)]);
        assert_eq!(ex.update(T0 + 120), None); // the member left already; nothing else changed
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_cap_takes_the_best_scores_and_an_outsider_must_score_twice_the_weakest() {
        let root = tmp("ex-cap");
        let mut ex = on(&root, &[addr(255)], T0);
        // 70 candidates: addr(i) scores i + 1 trades
        for i in 0..70u8 {
            for _ in 0..=i {
                ex.learn(&addr(i), T0);
            }
        }
        assert_eq!(ex.update(T0).as_deref(), Some("new_reversed excludes: 64 learned (+64 -0)"));
        assert!(!ex.list().contains(&addr(5)) && ex.list().contains(&addr(6))); // the six weakest (1..6 trades) out
        // an outsider at 13 trades does not replace the weakest member (7 trades): 13 < 14
        for _ in 0..7 {
            ex.learn(&addr(5), T0);
        }
        assert_eq!(ex.update(T0), None);
        // at 15 it does
        for _ in 0..2 {
            ex.learn(&addr(5), T0);
        }
        assert_eq!(ex.update(T0).as_deref(), Some("new_reversed excludes: 64 learned (+1 -1)"));
        assert!(ex.list().contains(&addr(5)) && !ex.list().contains(&addr(6)));
        // scores halve every 6 h: an old leader falls behind a fresh candidate
        let old = ex.cands[&addr(69)].score_at(T0 + 12 * 3600);
        assert!((old - 70.0 / 4.0).abs() < 1e-9, "{old}");
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_restart_keeps_the_set_and_seven_days_without_a_trade_expire_a_member() {
        let root = tmp("ex-keep");
        let mut ex = on(&root, &[addr(255)], T0);
        ex.on_trade(&trade(&addr(1), Some(&addr(2))), T0);
        ex.on_trade(&trade(&addr(1), None), T0 + 86_400);
        assert!(ex.update(T0 + 86_400).is_some());
        ex.save(T0 + 86_400).unwrap();
        // the restart: both members back
        let mut back = on(&root, &[addr(255)], T0 + 86_400 + 5);
        assert_eq!(back.list(), ex.list());
        // addr(2)'s last trade was at T0: gone at T0 + 7 days, addr(1) (T0 + 1 day) stays a day longer
        assert_eq!(back.update(T0 + EXPIRE_SECS - 1), None);
        assert_eq!(back.update(T0 + EXPIRE_SECS).as_deref(), Some("new_reversed excludes: 1 learned (+0 -1)"));
        assert_eq!(back.list(), vec![addr(1)]);
        back.save(T0 + EXPIRE_SECS).unwrap();
        // a new creation_accounts.txt naming addr(1): dropped at the start
        let again = on(&root, &[addr(1)], T0 + EXPIRE_SECS + 5);
        assert!(again.list().is_empty());
        // a restart past the expiry: dropped as well
        let late = on(&root, &[addr(255)], T0 + 86_400 + EXPIRE_SECS);
        assert!(late.list().is_empty());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_start_note_and_the_summary_name_the_inputs_sha256_and_count() {
        // LAB review on #476: the input is provable from the recorder's own output
        let root = tmp("ex-input");
        let f = root.join("creation_accounts.txt");
        let body = format!("# header\n{}\n\n{}\n", addr(1), addr(2));
        std::fs::write(&f, &body).unwrap();
        let sha = hex::encode(Sha256::digest(body.as_bytes()));
        let (ex, notes) = Excludes::open(&root, &f, T0);
        assert_eq!(notes, [format!("learned new_reversed excludes on: 0 kept from {FILE}; 2 creation accounts from {} (sha256 {sha})", f.display())]);
        assert_eq!(ex.summary()["creation_accounts"], json!({"count": 2, "sha256": sha}));
        assert_eq!(Excludes::off().summary()["creation_accounts"], Value::Null);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn without_a_valid_creation_accounts_file_nothing_is_learned() {
        let root = tmp("ex-off");
        for (body, why) in [(None, "read "), (Some("# only a comment\n\n"), "holds no address"), (Some("abc\n"), "line 1: not an address")] {
            let f = root.join("creation_accounts.txt");
            let _ = std::fs::remove_file(&f);
            if let Some(b) = body {
                std::fs::write(&f, b).unwrap();
            }
            let (mut ex, notes) = Excludes::open(&root, &f, T0);
            assert!(!ex.enabled() && notes[0].starts_with("learned new_reversed excludes off:") && notes[0].contains(why), "{notes:?}");
            ex.on_trade(&trade(&addr(1), None), T0);
            assert_eq!((ex.update(T0 + 60), ex.list().len()), (None, 0));
            ex.save(T0).unwrap();
            assert!(!root.join(FILE).exists());
        }
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_measuring_hours_recipients_meet_none_of_its_creations() {
        // The fixture (fixtures/measure-20261020-recipients.json, from recorder-measure-20261020): every account of
        // the hour's 185 creations (ix_accounts, signers, fee_payer; backfills included) and the protocol and buyback
        // recipients of its 52,787 trades on tx_new_reversed, with their trade counts.
        let v: Value = serde_json::from_str(include_str!("../fixtures/measure-20261020-recipients.json")).unwrap();
        let creations: HashSet<String> = v["creation_accounts"].as_array().unwrap().iter().map(|a| a.as_str().unwrap().to_owned()).collect();
        let recipients: Vec<(String, u64)> =
            v["recipients"].as_object().unwrap().iter().map(|(a, n)| (a.clone(), n.as_u64().unwrap())).collect();
        assert_eq!((creations.len(), recipients.len()), (v["counts"]["creation_accounts"].as_u64().unwrap() as usize,
                                                         v["counts"]["recipients"].as_u64().unwrap() as usize));
        // the data alone: no recipient is a creation account
        assert!(recipients.iter().all(|(a, _)| !creations.contains(a)));
        // and the guard with the creations as this run's (rule b): the learned set meets none of them
        let root = tmp("ex-hour");
        let mut ex = on(&root, &[addr(255)], T0);
        ex.on_creation(creations.iter().cloned());
        for (a, n) in &recipients {
            for _ in 0..*n {
                ex.learn(a, T0);
            }
        }
        ex.update(T0);
        assert_eq!(ex.list().len(), CAP);
        assert!(ex.list().iter().all(|a| !creations.contains(a)));
        std::fs::remove_dir_all(&root).unwrap();
    }
}
