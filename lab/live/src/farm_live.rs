//! M3-10 (research/m3-10-farmer-flags-spec-2026-10-20.md v0.2 §1.1-§2): LiveFeed's farmer flag (paper/live.py
//! `FarmerFlags`, `_Trades`), ported step by step for the follow source. It is not legs.rs's `FarmHist`, the store-exact
//! flag of the replay: that one re-anchors per chunk and compares 20 |net| < gross in integers; this one is LiveFeed's
//! incremental window, its quirk and its float rule included (decision O3: mirror LiveFeed).
//!
//! - Per (pool, trader) the trades in slot order as (slot, signed, gross): +amount for a token buy, -amount for a sell
//!   (a token_buy None counts as a sell), the amount as gross. A trader None (NONE) is a key like any other.
//! - `flag`: the window [slot - h24, slot). Its start only moves forward (a later query for an earlier slot does not see
//!   the trades an earlier query dropped: LiveFeed's quirk, kept); its end moves to the first trade at or after the slot,
//!   forward or back; a farmer when it holds at least FARMER_MIN_TRADES and |net| < FARMER_RATIO x gross as Python
//!   computes it: the gross rounded to a double, times 0.05, compared exactly with the integer |net| (n < f exactly when
//!   n < ceil(f)).
//! - `add`: appended, or (an older trade, a backfill's) inserted in slot order after its slot's trades but never before
//!   the start; left of the last query's end it joins that query's sums.
//! - `prune`: drop the keys with no trade left in a window or whose last trade is before slot - h24; compact the rest.
//!   Compaction gives memory back at LiveFeed's points (a start past 32 trades and half the list); it never changes an
//!   answer.
//!
//! The sums are i128 and u128: exact for every u64 amount (Python's are unbounded ints). The shared golden cases
//! (paper/tests/data/m3_10_farmer_cases.json, computed by FarmerFlags itself) run in the tests.

use lab_core::legs::Id;
use std::collections::HashMap;

/// backtest/store.py FARMER_RATIO and FARMER_MIN_TRADES (spec v0.2 §1.1; the goldens carry the file's copy).
pub const FARMER_RATIO: f64 = 0.05;
pub const FARMER_MIN_TRADES: usize = 2;
/// LiveFeed compacts a key once its start passed this many trades (96 flat indices of triples) and half its list.
const COMPACT_AFTER: usize = 32;

/// One (pool, trader)'s trades and the window of its last query (live.py `_Trades`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Trades {
    a: Vec<(u64, i128, u128)>,
    start: usize,
    end: usize,
    n: usize,
    net: i128,
    gross: u128,
}

/// LiveFeed's rule on a window's count and sums: at least FARMER_MIN_TRADES and |net| < FARMER_RATIO x gross, the
/// right side a Python float (the gross rounded to a double, half to even, then the product rounded), compared with the
/// integer |net| exactly.
pub fn is_farmer(n: usize, net: i128, gross: u128) -> bool {
    if n < FARMER_MIN_TRADES {
        return false;
    }
    let thr = (FARMER_RATIO * (gross as f64)).ceil(); // finite and >= 0; an integer-valued double
    net.unsigned_abs() < thr as u128 // saturates only beyond 2^128, where every |net| is below
}

impl Trades {
    fn take(&mut self, k: usize) {
        let (_, s, g) = self.a[k];
        (self.n, self.net, self.gross) = (self.n - 1, self.net - s, self.gross - g);
    }

    fn put(&mut self, k: usize) {
        let (_, s, g) = self.a[k];
        (self.n, self.net, self.gross) = (self.n + 1, self.net + s, self.gross + g);
    }

    /// live.py `_Trades.compact`: drop the trades before the start (indices shift, answers do not).
    fn compact(&mut self) {
        if self.start > 0 {
            self.a.drain(..self.start);
            self.end -= self.start;
            self.start = 0;
        }
    }

    /// live.py `FarmerFlags.flag` on this key (module doc).
    pub fn flag(&mut self, slot: u64, h24: u64) -> bool {
        let lo = i128::from(slot) - i128::from(h24);
        let mut i = self.start;
        while i < self.a.len() && i128::from(self.a[i].0) < lo {
            if i < self.end {
                self.take(i);
            }
            i += 1;
        }
        self.start = i;
        if self.end < i {
            self.end = i;
        }
        if i > COMPACT_AFTER && 2 * i > self.a.len() {
            self.compact();
        }
        let mut e = self.end;
        while e < self.a.len() && self.a[e].0 < slot {
            self.put(e);
            e += 1;
        }
        while e > self.start && self.a[e - 1].0 >= slot {
            e -= 1;
            self.take(e);
        }
        self.end = e;
        is_farmer(self.n, self.net, self.gross)
    }

    /// live.py `FarmerFlags.add` on this key (module doc).
    pub fn add(&mut self, slot: u64, token_buy: bool, amount: u64) {
        let signed = if token_buy { i128::from(amount) } else { -i128::from(amount) };
        let item = (slot, signed, u128::from(amount));
        if self.a.len() == self.start || self.a.last().is_some_and(|l| l.0 <= slot) {
            self.a.push(item);
            return;
        }
        let mut i = self.a.len();
        while i > self.start && self.a[i - 1].0 > slot {
            i -= 1;
        }
        self.a.insert(i, item);
        if i < self.end {
            self.end += 1;
            self.put(i);
        }
    }

    /// [start, end, n, net, gross, len] (the goldens' `state`: positions in trades).
    #[cfg(test)]
    pub fn state(&self) -> (usize, usize, usize, i128, u128, usize) {
        (self.start, self.end, self.n, self.net, self.gross, self.a.len())
    }

    /// The trades from the start on (live.py `len(_Trades)`).
    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.a.len() - self.start
    }

    fn bytes(&self) -> usize {
        self.a.capacity() * std::mem::size_of::<(u64, i128, u128)>()
    }
}

/// live.py `FarmerFlags`: the trades per (pool, trader).
#[derive(Debug, Default)]
pub struct FarmerFlags {
    hist: HashMap<(Id, Id), Trades>,
}

impl FarmerFlags {
    /// live.py `flag`: false for a key without trades.
    pub fn flag(&mut self, pool: Id, trader: Id, slot: u64, h24: u64) -> bool {
        self.hist.get_mut(&(pool, trader)).is_some_and(|t| t.flag(slot, h24))
    }

    pub fn add(&mut self, pool: Id, trader: Id, slot: u64, token_buy: bool, amount: u64) {
        self.hist.entry((pool, trader)).or_default().add(slot, token_buy, amount);
    }

    /// live.py `prune`: the keys with no trade left or whose last trade is before slot - h24 go; the others are compacted.
    pub fn prune(&mut self, slot: u64, h24: u64) {
        let lo = i128::from(slot) - i128::from(h24);
        self.hist.retain(|_, t| !(t.a.len() == t.start || t.a.last().is_some_and(|l| i128::from(l.0) < lo)));
        for t in self.hist.values_mut() {
            t.compact();
        }
    }

    /// live.py `entries`: the trades from each key's start on.
    #[cfg(test)]
    pub fn entries(&self) -> usize {
        self.hist.values().map(Trades::len).sum()
    }

    #[cfg(test)]
    pub fn get(&self, pool: Id, trader: Id) -> Option<&Trades> {
        self.hist.get(&(pool, trader))
    }

    #[cfg(test)]
    pub fn keys(&self) -> impl Iterator<Item = &(Id, Id)> {
        self.hist.keys()
    }

    /// The memory log: keys, trades held and an estimate of the bytes (the table and every key's list).
    pub fn mem(&self) -> (usize, usize, usize) {
        let trades = self.hist.values().map(|t| t.a.len()).sum();
        let bytes = lab_core::mem::map(&self.hist) + self.hist.values().map(Trades::bytes).sum::<usize>();
        (self.hist.len(), trades, bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lab_core::legs::NONE;
    use serde_json::Value;

    const CASES: &str = include_str!("../../paper/tests/data/m3_10_farmer_cases.json");

    /// A case's names as ids (a trader null is NONE).
    fn id(names: &mut Vec<String>, v: &Value) -> Id {
        let Some(s) = v.as_str() else { return NONE };
        if let Some(i) = names.iter().position(|n| n == s) {
            return i as Id;
        }
        names.push(s.to_owned());
        (names.len() - 1) as Id
    }

    #[test]
    fn every_golden_case_gives_farmerflags_answers_and_states() {
        // spec v0.2 §6.4: the cases FarmerFlags computed (paper/tests/m3_10_farmer_cases.py), op by op, on a fresh port
        let file: Value = serde_json::from_str(CASES).unwrap();
        assert_eq!(file["constants"], serde_json::json!({"farmer_ratio": FARMER_RATIO, "farmer_min_trades": FARMER_MIN_TRADES}));
        let cases = file["cases"].as_array().unwrap();
        assert!(cases.len() >= 12, "{} cases", cases.len());
        for case in cases {
            let name = case["name"].as_str().unwrap();
            let (mut f, mut names) = (FarmerFlags::default(), Vec::new());
            for op in case["ops"].as_array().unwrap() {
                let op = op.as_array().unwrap();
                let u = |k: usize| op[k].as_u64().unwrap();
                match op[0].as_str().unwrap() {
                    "add" => {
                        let (p, t) = (id(&mut names, &op[1]), id(&mut names, &op[2]));
                        f.add(p, t, u(3), op[4].as_bool().unwrap_or(false), u(5));
                    }
                    "flag" => {
                        let (p, t) = (id(&mut names, &op[1]), id(&mut names, &op[2]));
                        assert_eq!(f.flag(p, t, u(3), u(4)), op[5].as_bool().unwrap(), "{name}: {op:?}");
                    }
                    "state" => {
                        let (p, t) = (id(&mut names, &op[1]), id(&mut names, &op[2]));
                        let w = &op[3];
                        let want = (w[0].as_u64().unwrap() as usize, w[1].as_u64().unwrap() as usize, w[2].as_u64().unwrap() as usize,
                                    w[3].as_str().unwrap().parse::<i128>().unwrap(), w[4].as_str().unwrap().parse::<u128>().unwrap(),
                                    w[5].as_u64().unwrap() as usize);
                        assert_eq!(f.get(p, t).unwrap().state(), want, "{name}: {op:?}");
                    }
                    "prune" => {
                        f.prune(u(1), u(2));
                        let mut left: Vec<(String, Option<String>)> = f.keys()
                            .map(|&(p, t)| (names[p as usize].clone(), (t != NONE).then(|| names[t as usize].clone())))
                            .collect();
                        left.sort_by(|a, b| (&a.0, a.1.is_none(), &a.1).cmp(&(&b.0, b.1.is_none(), &b.1)));
                        let want: Vec<(String, Option<String>)> = op[3].as_array().unwrap().iter()
                            .map(|k| (k[0].as_str().unwrap().to_owned(), k[1].as_str().map(str::to_owned)))
                            .collect();
                        assert_eq!(left, want, "{name}: {op:?}");
                    }
                    "entries" => assert_eq!(f.entries() as u64, u(1), "{name}: {op:?}"),
                    other => panic!("{name}: unknown op {other}"),
                }
            }
        }
    }

    #[test]
    fn the_float_rule_is_pythons_not_the_stores() {
        // |net| < 0.05 * gross with the gross a double: equal to 20 |net| < gross below 2^53, and here above it apart
        let store = |net: u128, gross: u128| 20 * net < gross; // legs.rs FarmHist's integer rule
        let (n1, g1, n2, g2) = (210_213_835_622_347_629_u128, 4_204_276_712_446_952_565_u128, 219_013_231_531_493_632_u128, 4_380_264_630_629_872_696_u128);
        assert!(is_farmer(2, n1 as i128, g1) && !store(n1, g1));
        assert!(!is_farmer(2, n2 as i128, g2) && store(n2, g2));
        assert!(!is_farmer(2, 0, 0) && !is_farmer(1, 0, 100) && is_farmer(2, -4, 100) && !is_farmer(2, 5, 100));
    }

    #[test]
    fn the_constants_are_the_stores_where_the_file_is_at_hand() {
        // spec v0.2 §1.1: pinned to backtest/store.py (the goldens' copy is checked above; this reads the file itself)
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../backtest/store.py");
        let Ok(text) = std::fs::read_to_string(&path) else { return };
        assert!(text.lines().any(|l| l.trim() == "FARMER_RATIO = 0.05"), "{}", path.display());
        assert!(text.lines().any(|l| l.trim() == "FARMER_MIN_TRADES = 2"), "{}", path.display());
    }
}
