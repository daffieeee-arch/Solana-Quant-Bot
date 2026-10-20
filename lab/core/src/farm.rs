//! The farm label of a pool trigger: each pool's trades of about the last hour with their farmer volume
//! (engine.py `farm_win`, `_farm_add` :570-586, `_pool_flagged` :599-609, the rollover prune :691-693), and whether
//! that hour was complete (engine.py `_farm_window_complete`, :799-813). M1 C1b; the porting spec is
//! research/m1-c1b-signals-spec-2026-10-19.md §3.7.
//!
//! - The window starts empty at the feed's first event; it is never seeded from farm_hist (which only sets the legs'
//!   farmer flags, C1a-2).
//! - Trades are added after the slot's signals (step 4b), so a decision at t never sees slot t's trades.
//! - The two prunes are lazy and take n at other slots than the query: Rust copies them, it does not "fix" them. Where
//!   n grows at an epoch change, Python can drop items a later window would include.
//! - Live (M3-08b): backfills reach the windows as history (signals.rs `add_history`, engine.py:417-421) and the live
//!   farm_window_complete rule is `live_complete`.
//! - Live (M3-11): a recorded trade's farmer flag computed again after a late backfill (LiveFeed `_reflag`) reaches its
//!   window entry (`fix`, engine.py `_farm_fix` :588-597), or waits in `fixes` for the trade's `add` (`_farm_add` :579),
//!   swept at the hour rollover (:694-695). Mirrored, not fixed: the first match only.

use crate::clock::{Clock, DAY_MS, F6_FARM_MS, F6_FARM_SHARE, SINCE_CREATION_MS};
use crate::exact::gt;
use crate::legs::{Id, Leg, Order, FARMER};
use crate::mem::{self, Mem};
use std::collections::HashMap;

/// (slot, order, SOL amount, farmer SOL amount) of one trade; tuples order as Python's.
pub type FarmItem = (u64, Order, i128, i128);

/// Per pool, its trades sorted by (slot, order, ...), the last hour or so (engine.py `farm_win`).
#[derive(Clone, Debug, Default)]
pub struct FarmWindow {
    win: HashMap<Id, Vec<FarmItem>>,
    /// Live (M3-11): (pool, order) -> the farmer flag a late backfill gave a trade not in the window yet (engine.py
    /// `farm_fixes`); taken by that trade's `add`, swept at the rollover. Empty in a replay.
    fixes: HashMap<(Id, Order), bool>,
}

impl FarmWindow {
    /// The slot's legs of a pool in the SOL-market map, mayhem included (engine.py `_farm_add`; the caller looks the
    /// pool up). Buys and sells only, each once:
    ///
    /// - live: a trade's farmer flag is a waiting fix's (taken, `fixes.pop`) before the duplicate check, so a duplicate
    ///   uses it up too (engine.py:579);
    /// - the duplicate check bisects on the full 4-tuple, as `bisect_left(xs, item)`: with (s, o, a, 0) present an
    ///   (s, o, a, a) lands after it and is stored too; the reverse order is skipped (a live backfill's path only);
    /// - then the lazy prune: when the first item is older than L - 2n, with L the last item's slot and n the hour at
    ///   L, the items before L - n go.
    pub fn add(&mut self, pool: Id, reversed: bool, legs: &[Leg], clock: &Clock) {
        let xs = self.win.entry(pool).or_default(); // an empty list too, as the defaultdict (the rollover drops it)
        for l in legs {
            let Leg::Pool(l) = l else { continue };
            if !l.kind.is_trade() {
                continue;
            }
            let a = l.sol_side(reversed).1;
            let farmer = if self.fixes.is_empty() { l.flag(FARMER) } else { self.fixes.remove(&(pool, l.order)).unwrap_or(l.flag(FARMER)) };
            let item = (l.order.slot, l.order, a, if farmer { a } else { 0 });
            let i = xs.partition_point(|x| *x < item);
            if xs.get(i).is_some_and(|x| (x.0, x.1) == (item.0, item.1)) {
                continue;
            }
            xs.insert(i, item);
        }
        if let (Some(first), Some(last)) = (xs.first(), xs.last()) {
            let n = clock.slots(F6_FARM_MS, last.0);
            if first.0 < last.0.saturating_sub(2 * n) {
                let keep = last.0.saturating_sub(n);
                let cut = xs.partition_point(|x| x.0 < keep);
                xs.drain(..cut);
            }
        }
    }

    /// The backtest's farming flag: farmers made more than F6_FARM_SHARE of the pool's SOL volume over
    /// [t - n, t - 1], n the hour at t (engine.py `_pool_flagged`). A pool without trades there is organic (0 > 0).
    pub fn flagged(&self, pool: Id, t: u64, clock: &Clock) -> bool {
        let xs = self.win.get(&pool).map_or(&[][..], Vec::as_slice);
        let lo = t.saturating_sub(clock.slots(F6_FARM_MS, t));
        let (mut vol, mut farm) = (0i128, 0i128);
        for x in xs[xs.partition_point(|x| x.0 < lo)..].iter().take_while(|x| x.0 < t) {
            vol += x.2;
            farm += x.3;
        }
        gt(farm, F6_FARM_SHARE * vol as f64)
    }

    /// Live (M3-11): a recorded trade's farmer flag computed again (engine.py `_farm_fix`, :588-597). The first entry of
    /// the pool's window at (order.slot, order) gets it (`bisect_left(xs, (order[0], order))`): of a trade stored twice,
    /// (s, o, a, 0) and (s, o, a, a), only the first changes. A trade not in the window (its slot not ended yet, or its
    /// pool without a window) gets it at its `add`; a later fix of the same trade replaces it.
    pub fn fix(&mut self, pool: Id, order: Order, farmer: bool) {
        if let Some(xs) = self.win.get_mut(&pool) {
            let i = xs.partition_point(|x| (x.0, x.1) < (order.slot, order));
            if let Some(x) = xs.get_mut(i).filter(|x| (x.0, x.1) == (order.slot, order)) {
                x.3 = if farmer { x.2 } else { 0 };
                return;
            }
        }
        self.fixes.insert((pool, order), farmer);
    }

    /// The hour rollover's prune (engine.py:691-695): pools whose list is empty or whose last trade is before
    /// slot - n, n the hour at the rollover slot; and the waiting fixes of trades before slot - n (never added).
    pub fn rollover(&mut self, slot: u64, clock: &Clock) {
        let lo = slot.saturating_sub(clock.slots(F6_FARM_MS, slot));
        self.win.retain(|_, xs| xs.last().is_some_and(|x| x.0 >= lo));
        self.fixes.retain(|(_, o), _| o.slot >= lo);
    }

    /// The fixes waiting for their trade (for the tests and the summary).
    pub fn fixes(&self) -> usize {
        self.fixes.len()
    }

    pub fn items(&self, pool: Id) -> Option<&[FarmItem]> {
        self.win.get(&pool).map(Vec::as_slice)
    }

    /// Pools with a list (for the summary and tests).
    pub fn pools(&self) -> usize {
        self.win.len()
    }

    /// Items in all lists.
    pub fn len(&self) -> usize {
        self.win.values().map(Vec::len).sum()
    }

    pub fn is_empty(&self) -> bool {
        self.win.is_empty()
    }

    /// The memory log (C4-2): the pools with a list and the items in them. `add`'s lazy prune keeps a list within about
    /// two hours of its last item; the hour rollover drops a pool without a trade in the last hour. Live (M3-11): the
    /// waiting fixes, swept with the hour.
    pub fn mem(&self) -> [Mem; 3] {
        [
            Mem::new("farm_pools", self.win.len(), mem::map(&self.win)),
            Mem::new("farm_items", self.len(), self.win.values().map(mem::vec).sum()),
            Mem::new("farm_fixes", self.fixes.len(), mem::map(&self.fixes)),
        ]
    }
}

/// Which `_farm_window_complete` rule a feed has (engine.py:799-813).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WindowRule {
    /// A feed of every pool's every trade (ReplayFeed.all_pools, feed.py:88): the hour [t - 1 h, t) is complete
    /// from an hour after the feed's first slot on.
    AllPools,
    /// LiveFeed: per pool, seen for an hour, backfilled, or followed since its creation (engine.py:806-813;
    /// `live_complete`, M3-08b).
    Live,
}

impl WindowRule {
    /// `AllPools`' 1 or 0 for a pool row at t: an hour after the feed's first slot. `Live` reads the pool's own state:
    /// `live_complete`.
    pub fn all_pools(first_slot: Option<u64>, t: u64, clock: &Clock) -> u8 {
        u8::from(first_slot.is_some_and(|f| t.saturating_sub(f) >= clock.slots(F6_FARM_MS, t)))
    }
}

/// engine.py `_farm_window_complete` on a live feed, for a pool row at t: 0 for a pool never seen; 1 when it has a
/// backfill; a late probe (creation-filters spec v0.2 §4 option A: its backfill reached past the replay window, LiveFeed's
/// late_probe_pools) 1 only once it was seen a day before t (`t - seen >= slots(DAY_MS, t)`); else 1 when it was seen an
/// hour before t (`t - seen >= hour(t)`); else 1 when it was first seen within SINCE_CREATION_MS of its known create slot
/// (`seen - created <= slots(SINCE_CREATION_MS, t)`, Python's signed ints: a pool seen before its create slot counts),
/// else 0.
pub fn live_complete(seen: Option<u64>, backfilled: bool, late: bool, created: Option<u64>, t: u64, clock: &Clock) -> u8 {
    let Some(seen) = seen else { return 0 };
    if backfilled {
        return 1;
    }
    let age = i128::from(t) - i128::from(seen);
    if late {
        return u8::from(age >= i128::from(clock.slots(DAY_MS, t)));
    }
    if age >= i128::from(clock.slots(F6_FARM_MS, t)) {
        return 1;
    }
    u8::from(created.is_some_and(|c| i128::from(seen) - i128::from(c) <= i128::from(clock.slots(SINCE_CREATION_MS, t))))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::tests::dev2;
    use crate::f7::tests::pool as leg;
    use crate::legs::PoolKind;

    const S: i64 = 1_000_000_000;

    /// A reversed pool's trade of `a` lamports (SOL is the base), farmer or not.
    fn trade(slot: u64, tx: u32, a: i64, farmer: bool) -> Leg {
        let Leg::Pool(mut l) = leg((slot, tx), 1, 3, PoolKind::Sell, (a, 0), (500 * S, 0)) else { unreachable!() };
        l.flags = if farmer { FARMER } else { 0 };
        Leg::Pool(l)
    }

    #[test]
    fn flagged_is_farm_above_half_the_volume_before_t() {
        let c = dev2(); // epoch 1042: n = 13441
        let t = 450_200_000;
        let mut w = FarmWindow::default();
        assert!(!w.flagged(1, t, &c)); // no trades: 0 > 0 is false
        w.add(1, true, &[trade(t - 13_441, 0, 10, true), trade(t - 100, 0, 10, false)], &c);
        assert!(!w.flagged(1, t, &c)); // farm exactly 0.5 * vol: organic
        w.add(1, true, &[trade(t - 50, 0, 1, true)], &c);
        assert!(w.flagged(1, t, &c));
        // [t - n, t - 1]: the item at t - n counts, slot t does not, and t - n - 1 does not.
        let mut w = FarmWindow::default();
        w.add(1, true, &[trade(t - 13_442, 0, 100, true), trade(t - 10, 0, 10, false)], &c);
        assert!(!w.flagged(1, t, &c));
        w.add(1, true, &[trade(t, 0, 100, true)], &c);
        assert!(!w.flagged(1, t, &c) && w.flagged(1, t + 1, &c));
        // A normal pool's amount is quote_net; withdraws, deposits and boosts are no trades.
        let mut w = FarmWindow::default();
        let Leg::Pool(mut b) = leg((t - 5, 0), 2, 3, PoolKind::Buy, (0, 7 * S), (0, 500 * S)) else { unreachable!() };
        b.flags = FARMER;
        w.add(2, false, &[Leg::Pool(b), leg((t - 5, 1), 2, 3, PoolKind::Deposit, (9 * S, 9 * S), (0, 500 * S))], &c);
        assert_eq!(w.items(2), Some(&[(t - 5, b.order, i128::from(7 * S), i128::from(7 * S))][..]));
        assert!(w.flagged(2, t, &c));
    }

    #[test]
    fn farm_share_is_exact_above_2_53() {
        // farm 2^53 + 1 of vol 2^54 + 2: float(vol) rounds to 2^54, so 0.5 * vol is 2^53, and 2^53 + 1 > 2^53 is True
        // in Python (lab-py); float(farm) is 2^53, so the naive comparison says organic.
        let c = dev2();
        let t = 450_200_000;
        let mut w = FarmWindow::default();
        let p53 = 1_i64 << 53;
        w.add(1, true, &[trade(t - 3, 0, p53 + 1, true), trade(t - 2, 0, p53 + 1, false)], &c);
        assert!(w.flagged(1, t, &c));
        assert!((p53 + 1) as f64 <= 0.5 * ((2 * p53 + 2) as f64)); // the naive comparison says organic
    }

    #[test]
    fn the_duplicate_check_bisects_on_the_full_tuple() {
        let c = dev2();
        let t = 450_200_000;
        let o = |l: &Leg| l.order();
        // (s, o, a, 0) then (s, o, a, a): the second lands after the first, the check misses it, both are kept.
        let mut w = FarmWindow::default();
        let (organic, farmer) = (trade(t, 0, 10, false), trade(t, 0, 10, true));
        w.add(1, true, &[organic], &c);
        w.add(1, true, &[farmer], &c);
        assert_eq!(w.items(1), Some(&[(t, o(&organic), 10, 0), (t, o(&organic), 10, 10)][..]));
        // The reverse order: (s, o, a, 0) lands on (s, o, a, a)'s index and is skipped.
        let mut w = FarmWindow::default();
        w.add(1, true, &[farmer], &c);
        w.add(1, true, &[organic], &c);
        assert_eq!(w.items(1), Some(&[(t, o(&organic), 10, 10)][..]));
        // An exact duplicate is skipped; an older trade goes in its place.
        w.add(1, true, &[farmer, trade(t - 1, 5, 3, false)], &c);
        assert_eq!(w.items(1).map(|x| x.iter().map(|i| i.0).collect::<Vec<_>>()), Some(vec![t - 1, t]));
    }

    #[test]
    fn the_lazy_prune_uses_n_at_the_last_item() {
        // dev2 1036 -> 1037: n grows from 11352 (317.13 ms) to 13510 (266.46 ms), lab-py.
        let c = dev2();
        let e = 1037 * crate::clock::EPOCH_SLOTS;
        assert_eq!((c.slots(F6_FARM_MS, e - 1), c.slots(F6_FARM_MS, e)), (11_352, 13_510));
        let slots = |w: &FarmWindow| w.items(1).map(|x| x.iter().map(|i| i.0).collect::<Vec<_>>());
        // L = e + 50 in 1037, the first item in 1036. The first is not before L - 2 n(L) = L - 27020: nothing goes
        // (with n at the first item, L - 22704, it would).
        let l = e + 50;
        let mut w = FarmWindow::default();
        w.add(1, true, &[trade(l - 25_000, 0, 1, false), trade(l - 12_000, 0, 100, true)], &c);
        w.add(1, true, &[trade(l, 0, 1, false)], &c);
        assert_eq!(slots(&w), Some(vec![l - 25_000, l - 12_000, l]));
        // The first is before L - 2 n(L): the items before L - n(L) = L - 13510 go. The farmer trade at L - 12000 stays
        // (L - 11352 would drop it), so a query at L + 1 is flagged.
        let mut w = FarmWindow::default();
        w.add(1, true, &[trade(l - 30_000, 0, 1, false), trade(l - 12_000, 0, 100, true)], &c);
        w.add(1, true, &[trade(l, 0, 1, false)], &c);
        assert_eq!(slots(&w), Some(vec![l - 12_000, l]));
        assert!(w.flagged(1, l + 1, &c));
        // L = e - 50 in 1036: the first is before L - 2n = L - 22704, so the items before L - n = L - 11352 go. The
        // farmer trade at L - 12000 goes too, though a query at e + 50 (n 13510: [L - 13410, L + 99]) would reach it:
        // organic.
        let l = e - 50;
        let mut w = FarmWindow::default();
        w.add(1, true, &[trade(l - 23_000, 0, 1, false), trade(l - 12_000, 0, 100, true)], &c);
        assert_eq!(w.items(1).map(<[_]>::len), Some(2)); // first not before L - 2n: nothing goes
        w.add(1, true, &[trade(l, 0, 1, false)], &c);
        assert_eq!(slots(&w), Some(vec![l]));
        assert!(!w.flagged(1, e + 50, &c));
        let e = 450_144_000; // 1041 -> 1042: n 13542 -> 13441
        // Not pruned while the first item is within L - 2n, though it is older than L - n.
        let mut w = FarmWindow::default();
        w.add(1, true, &[trade(e - 20_000, 0, 100, true), trade(e + 1_000, 0, 1, false)], &c);
        assert_eq!(w.items(1).map(<[_]>::len), Some(2));
        assert!(!w.flagged(1, e + 1_001, &c)); // and the query still ignores it
    }

    #[test]
    fn the_rollover_drops_quiet_and_empty_pools() {
        let c = dev2();
        let s = 450_200_000; // n 13441
        let mut w = FarmWindow::default();
        w.add(1, true, &[trade(s - 13_441, 0, 1, false)], &c);
        w.add(2, true, &[trade(s - 13_442, 0, 1, false)], &c);
        w.add(3, true, &[leg((s - 5, 0), 3, 3, PoolKind::Deposit, (1, 1), (500 * S, 0))], &c); // an empty list
        assert_eq!((w.pools(), w.items(3).map(<[_]>::len)), (3, Some(0)));
        w.rollover(s, &c);
        assert_eq!((w.pools(), w.items(1).is_some(), w.len()), (1, true, 1));
        // n at the rollover slot: at s = e + 50 in 1037 (n 13510) a pool whose last trade is s - 12000, in 1036
        // (n 11352 there), stays, and a query at s + 1 still reads it.
        let s = 1037 * crate::clock::EPOCH_SLOTS + 50;
        let mut w = FarmWindow::default();
        w.add(1, true, &[trade(s - 12_000, 0, 100, true)], &c);
        w.add(2, true, &[trade(s - 13_511, 0, 1, false)], &c);
        w.rollover(s, &c);
        assert_eq!((w.pools(), w.items(1).is_some()), (1, true));
        assert!(w.flagged(1, s + 1, &c));
    }

    #[test]
    fn a_fix_changes_the_trades_entry_or_waits_for_its_add() {
        // M3-11 (engine.py `_farm_fix` :588-597, `_farm_add` :579): a recorded trade's farmer flag computed again.
        let c = dev2();
        let t = 450_200_000;
        let o = |l: &Leg| l.order();
        let (x, y) = (trade(t - 100, 0, 10, false), trade(t - 50, 0, 9, false));
        // In the window: its entry gets the flag, and the label follows (10 of 19 is above half).
        let mut w = FarmWindow::default();
        w.add(1, true, &[x, y], &c);
        assert!(!w.flagged(1, t, &c));
        w.fix(1, o(&x), true);
        assert_eq!((w.items(1), w.fixes()), (Some(&[(t - 100, o(&x), 10, 10), (t - 50, o(&y), 9, 0)][..]), 0));
        assert!(w.flagged(1, t, &c));
        w.fix(1, o(&x), false); // and back
        assert!(!w.flagged(1, t, &c) && w.items(1).is_some_and(|i| i[0].3 == 0));
        // Not in the window yet (its slot not ended, or a pool without a window): kept; the trade's add takes it before
        // the duplicate check, once; a later fix of the same trade replaces the kept one.
        let mut w = FarmWindow::default();
        w.fix(1, o(&x), false);
        w.fix(1, o(&x), true);
        w.fix(2, o(&y), true);
        assert!(w.items(1).is_none() && w.fixes() == 2);
        w.add(1, true, &[x, x], &c); // the second x is a duplicate: no fix left for it, (s, o, 10, 0) lands on (s, o, 10, 10)
        assert_eq!((w.items(1), w.fixes()), (Some(&[(t - 100, o(&x), 10, 10)][..]), 1));
        // A kept fix of another pool's trade is not taken; a kept fix overrides the trade's own farmer flag.
        let farmer = trade(t - 50, 0, 9, true);
        w.add(1, true, &[farmer], &c);
        assert_eq!((w.items(1).map(<[_]>::len), w.fixes()), (Some(2), 1));
        w.fix(3, o(&farmer), false);
        w.add(3, true, &[farmer], &c);
        assert_eq!((w.items(3), w.fixes()), (Some(&[(t - 50, o(&farmer), 9, 0)][..]), 1));
        let names: Vec<(&str, usize)> = w.mem().iter().map(|m| (m.name, m.entries)).collect();
        assert_eq!(names, [("farm_pools", 2), ("farm_items", 3), ("farm_fixes", 1)]);
    }

    #[test]
    fn a_fix_changes_the_first_entry_of_a_trade_stored_twice() {
        // engine.py:591-594: bisect_left(xs, (order[0], order)) and the first match only. A recorded trade (s, o, a, 0)
        // and its backfill copy (s, o, a, a) are both stored (the_duplicate_check_bisects_on_the_full_tuple): a fix to
        // organic changes the first (already 0), the copy keeps its farmer volume, and the label stays flagged.
        let c = dev2();
        let t = 450_200_000;
        let (organic, farmer) = (trade(t - 10, 0, 10, false), trade(t - 10, 0, 10, true));
        let o = organic.order();
        let mut w = FarmWindow::default();
        w.add(1, true, &[organic], &c);
        w.add(1, true, &[farmer], &c);
        w.fix(1, o, false);
        assert_eq!(w.items(1), Some(&[(t - 10, o, 10, 0), (t - 10, o, 10, 10)][..]));
        assert!(!w.flagged(1, t, &c)); // 10 of 20 is not above half; with a farmer's trade at t - 5 it is
        w.add(1, true, &[trade(t - 5, 0, 1, true)], &c);
        assert!(w.flagged(1, t, &c));
        // A fix to farmer changes the first too: both entries count now.
        w.fix(1, o, true);
        assert_eq!(&w.items(1).unwrap()[..2], &[(t - 10, o, 10, 10), (t - 10, o, 10, 10)]);
        // The key is (slot, order): a fix of an order at another slot finds nothing and is kept.
        w.fix(1, crate::legs::Order { slot: t - 9, ..o }, false);
        assert_eq!(w.fixes(), 1);
    }

    #[test]
    fn the_rollover_sweeps_the_fixes_of_trades_never_added() {
        // engine.py:694-695: at the hour rollover the kept fixes of trades before slot - n go (n the hour at the slot).
        let c = dev2();
        let s = 450_200_000; // n 13441
        let mut w = FarmWindow::default();
        let (old, edge) = (trade(s - 13_442, 0, 1, false).order(), trade(s - 13_441, 0, 1, false).order());
        w.fix(1, old, true);
        w.fix(1, edge, true);
        w.fix(2, trade(s + 5, 0, 1, false).order(), false);
        w.rollover(s, &c);
        assert_eq!(w.fixes(), 2);
        w.add(1, true, &[trade(s - 13_441, 0, 4, false)], &c); // the edge's fix is still taken
        assert_eq!((w.items(1), w.fixes()), (Some(&[(s - 13_441, edge, 4, 4)][..]), 1));
    }

    #[test]
    fn the_window_is_complete_an_hour_after_the_first_slot() {
        // R1: first_slot 450128159 in epoch 1041 (h 13542): the flip is at 450141701.
        let c = dev2();
        let f = Some(450_128_159);
        assert_eq!(WindowRule::all_pools(f, 450_141_700, &c), 0);
        assert_eq!(WindowRule::all_pools(f, 450_141_701, &c), 1);
        assert_eq!(WindowRule::all_pools(None, 450_141_701, &c), 0);
    }

    #[test]
    fn the_live_rule_reads_the_pools_own_hour_backfill_and_creation() {
        // engine.py:806-813 at t in epoch 1041 (h 13542 slots, SINCE_CREATION_MS 226 slots at 265.83 ms).
        let c = dev2();
        let t = 450_141_701;
        assert_eq!((c.slots(F6_FARM_MS, t), c.slots(SINCE_CREATION_MS, t)), (13_542, 226));
        let live = |seen, backfilled, created| live_complete(seen, backfilled, false, created, t, &c);
        assert_eq!(live(None, true, Some(t)), 0); // never seen
        assert_eq!((live(Some(t - 13_542), false, None), live(Some(t - 13_541), false, None)), (1, 0)); // an hour
        assert_eq!(live(Some(t - 10), true, None), 1); // a backfill
        let seen = t - 10;
        assert_eq!((live(Some(seen), false, Some(seen - 226)), live(Some(seen), false, Some(seen - 227))), (1, 0)); // since creation
        assert_eq!(live(Some(seen), false, Some(seen + 5)), 1); // seen before its create slot: Python's negative difference
        assert_eq!(live(Some(t + 3), false, None), 0); // a seen slot after t: no hour (Python's negative t - seen)
    }

    #[test]
    fn a_late_probe_counts_incomplete_until_a_day_of_its_own_history() {
        // creation-filters spec v0.2 §4 option A (LAB review on #475; m3_live_cases.late_probe_window does it through the
        // engine): 0 at an hour and at 23 hours, 1 at a day of t's clock; a backfill or no late mark as before.
        let c = dev2();
        let t = 450_141_701;
        let (hour, day) = (c.slots(F6_FARM_MS, t), c.slots(DAY_MS, t));
        assert_eq!(day, (86_400_000.0f64 / 265.83).round() as u64);
        let late = |seen, backfilled| live_complete(Some(seen), backfilled, true, None, t, &c);
        assert_eq!((late(t - hour, false), late(t - c.slots(23.0 * F6_FARM_MS, t), false)), (0, 0));
        assert_eq!((late(t - day + 1, false), late(t - day, false)), (0, 1));
        assert_eq!(late(t - 10, true), 1); // backfilled anyway
        assert_eq!(live_complete(Some(t - hour), false, false, None, t, &c), 1); // not late: the hour as before
        assert_eq!(live_complete(None, true, true, None, t, &c), 0); // never seen
    }
}
