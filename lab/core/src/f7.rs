//! The F7 trigger and its filters as the paper engine runs them per slot: the recent legs per key (engine.py step 3,
//! :396-401), the event-local trigger (engine.py `_f7`, :619-634) and the windows over the recent legs
//! (strategies.py `f7_signal`, :443-485, on engine.py's `RecentView`, :173-187). M1 C1b; the porting spec is
//! research/m1-c1b-signals-spec-2026-10-19.md §3.6.
//!
//! - Rust follows the engine, not `f7_signals_sql`: the windows are taken at t on the clock, per epoch.
//! - Every int-against-float test is exact (`crate::exact`), as Python's.
//! - The curve legs hold post reserves, so the trigger size reads `pre().vq` (strategies.py:463); for a sell that is
//!   the engine's `post.vq + q` (engine.py:625), the same integer.

use crate::clock::{Clock, F7_CREATOR_MS, F7_GAP_MS, F7_INFLOW_MS, HISTORY_SLOTS, SOL};
use crate::exact::ge;
use crate::legs::{CurveLeg, Id, Leg, PoolLeg, NONE};
use crate::mem::{self, Mem};
use crate::universe::{F7Key, Segment};
use std::collections::{HashMap, VecDeque};

/// Per key, its legs of the last HISTORY_SLOTS slots and the slot of its last event (engine.py `recent`, `last_seen`).
/// Full legs, because C2's entries read them (engine.py:902-905).
#[derive(Clone, Debug, Default)]
pub struct Recent {
    legs: HashMap<Id, VecDeque<Leg>>,
    last_seen: HashMap<Id, u64>,
}

impl Recent {
    /// Step 3 of a slot (engine.py:396-401): the key's legs of the slot, then the legs before slot - HISTORY_SLOTS go.
    pub fn extend(&mut self, key: Id, legs: &[Leg], slot: u64) {
        let dq = self.legs.entry(key).or_default();
        dq.extend(legs.iter().copied());
        let lo = slot.saturating_sub(HISTORY_SLOTS);
        while dq.front().is_some_and(|l| l.slot() < lo) {
            dq.pop_front();
        }
        self.last_seen.insert(key, slot);
    }

    pub fn get(&self, key: Id) -> Option<&VecDeque<Leg>> {
        self.legs.get(&key)
    }

    pub fn last_seen(&self, key: Id) -> Option<u64> {
        self.last_seen.get(&key).copied()
    }

    /// The key's last leg, whose post state is the engine's `last_state[key]` (engine.py:400; C2's entry state and
    /// s_dec, C2 spec §3.4-§3.5). Step 3 writes both from the slot's last leg, the trim never empties a deque it has
    /// just extended (the slot's own legs are not older than slot - HISTORY_SLOTS), and the sweep drops a key's legs
    /// with its last_seen as the engine drops last_state with it (:679-682).
    pub fn back(&self, key: Id) -> Option<&Leg> {
        self.legs.get(&key)?.back()
    }

    /// The hourly sweep (engine.py `_sweep_history`, :675-684): keys without an event since `before` leave, except
    /// those `keep` names (C2: open, pending or waiting for a graduation). A swept key's legs are all older than
    /// slot - HISTORY_SLOTS, so F7 never sees the sweep.
    pub fn sweep(&mut self, before: u64, keep: impl Fn(Id) -> bool) {
        let legs = &mut self.legs;
        self.last_seen.retain(|&k, &mut s| {
            let stay = s >= before || keep(k);
            if !stay {
                legs.remove(&k);
            }
            stay
        });
    }

    pub fn len(&self) -> usize {
        self.legs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.legs.is_empty()
    }

    /// The memory log (C4-2): the keys (with their last_seen) and the legs they hold. Each key's legs span at most
    /// HISTORY_SLOTS slots after its last extend; a key leaves at the hourly sweep.
    pub fn mem(&self) -> [Mem; 2] {
        [
            Mem::new("recent_keys", self.legs.len(), mem::map(&self.legs) + mem::map(&self.last_seen)),
            Mem::new("recent_legs", self.legs.values().map(VecDeque::len).sum(), self.legs.values().map(mem::deque).sum()),
        ]
    }
}

/// A trigger-sized curve sell: q >= max(3 SOL, 0.04 * pre.vq) (strategies.py:463; engine.py:625).
fn curve_trig_size(l: &CurveLeg) -> bool {
    let q = i128::from(l.q);
    !l.is_buy && q >= 3 * SOL && ge(q, 0.04 * l.pre().vq as f64)
}

/// A trigger-sized pool sell on the SOL side: a >= max(3 SOL, 0.04 * depth) (strategies.py:470-472).
fn pool_trig_size(l: &PoolLeg, reversed: bool) -> bool {
    let (tb, a, dep) = l.sol_side(reversed);
    !tb && a >= 3 * SOL && ge(a, 0.04 * dep as f64)
}

/// The slot's first leg of a key, in chain order, that passes the event-local conditions (engine.py:619-634), with
/// its depth: a curve's pre-trade real SOL (post rq + q), a pool's SOL depth.
///
/// - Curve: a trigger-sized sell, not by the creator (NONE against NONE counts as equal, as Python's `None != None`)
///   or MAYHEM_AGENT, at 20 to 75 SOL real.
/// - Pool, buys and sells only: a trigger-sized sell at >= 150 SOL depth, not by the creator nor the coin creator,
///   where a NONE creator blocks no one (Python's `trader != (creator or "")`).
pub fn trigger<'a>(legs: &'a [Leg], k: &F7Key, mayhem_agent: Id) -> Option<(&'a Leg, i128)> {
    legs.iter().find_map(|leg| match leg {
        Leg::Curve(l) if k.seg == Segment::Curve => {
            let pre_rq = i128::from(l.rq) + i128::from(l.q);
            (curve_trig_size(l) && l.trader != k.creator && l.trader != mayhem_agent && (20 * SOL..=75 * SOL).contains(&pre_rq))
                .then_some((leg, pre_rq))
        }
        Leg::Pool(l) if k.seg != Segment::Curve && l.kind.is_trade() => {
            let dep = l.sol_side(k.seg.reversed()).2;
            (pool_trig_size(l, k.seg.reversed())
                && dep >= 150 * SOL
                && (k.creator == NONE || l.trader != k.creator)
                && (k.coin == NONE || l.trader != k.coin))
                .then_some((leg, dep))
        }
        _ => None,
    })
}

/// The F7 filters at decision slot t over the key's recent legs (strategies.py `f7_signal`), with the windows
/// (n_in, n_cr, n_gap) taken on the clock at t:
///
/// - the net SOL inflow over [t - n_in, t - 1] is > 0;
/// - no sell by the creator in [t - n_cr, t] (all of slot t; pools test the creator only, not the coin creator);
/// - no other trigger-sized sell in [t - n_gap, t - 1] (no depth or trader test).
///
/// Pools count buys and sells only. Step 3 has added all of slot t's legs before this runs, and no leg is later than t.
pub fn f7_signal(recent: Option<&VecDeque<Leg>>, seg: Segment, t: u64, creator: Id, clock: &Clock) -> bool {
    let [lo_in, lo_cr, lo_gap] = [F7_INFLOW_MS, F7_CREATOR_MS, F7_GAP_MS].map(|d| t.saturating_sub(clock.slots(d, t)));
    let legs = recent.into_iter().flatten().filter(|l| l.slot() <= t);
    let (mut inflow, mut blocked) = (0i128, false);
    if seg == Segment::Curve {
        for l in legs.filter_map(|l| if let Leg::Curve(c) = l { Some(c) } else { None }) {
            let s = l.order.slot;
            if lo_in <= s && s < t {
                inflow += if l.is_buy { i128::from(l.q) } else { -i128::from(l.q) };
            }
            blocked |= (lo_cr <= s && !l.is_buy && l.trader == creator) || (lo_gap <= s && s < t && curve_trig_size(l));
        }
    } else {
        let reversed = seg.reversed();
        for l in legs.filter_map(|l| if let Leg::Pool(p) = l { Some(p) } else { None }).filter(|l| l.kind.is_trade()) {
            let (s, (tb, a, _)) = (l.order.slot, l.sol_side(reversed));
            if lo_in <= s && s < t {
                inflow += if tb { a } else { -a };
            }
            blocked |= (lo_cr <= s && !tb && l.trader == creator) || (lo_gap <= s && s < t && pool_trig_size(l, reversed));
        }
    }
    inflow > 0 && !blocked
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::clock::tests::{dev2, fwd1053};
    use crate::legs::{CurveVariant, Order, PoolKind};

    pub(crate) const MAYHEM: Id = 100;

    /// A curve trade at (slot, tx) with its post reserves vq and rq.
    pub(crate) fn curve((slot, tx): (u64, u32), mint: Id, trader: Id, is_buy: bool, q: u64, vq: u64, rq: u64) -> Leg {
        Leg::Curve(CurveLeg {
            order: Order { slot, tx, outer: 0, inner: 0 }, mint, trader, t: 1_000_000, q, fee: 0, vt: 1_000_000_000_000_000, vq, rt: 0, rq,
            args: [0; 4], fee_bps: 95, creator_bps: 30, args_set: 0, variant: if is_buy { CurveVariant::Buy } else { CurveVariant::Sell },
            is_buy, direct: true,
        })
    }

    /// A pool event at (slot, tx) with its amounts (base, quote_net) and pre-state (b, e): a reversed pool's depth is
    /// b and a normal pool's is e (virt 0).
    pub(crate) fn pool((slot, tx): (u64, u32), pool: Id, trader: Id, kind: PoolKind, (base, quote_net): (i64, i64), (b, e): (i64, i64)) -> Leg {
        Leg::Pool(PoolLeg {
            order: Order { slot, tx, outer: 0, inner: 0 }, pool, trader, b, e, virt: 0, fees: [20, 5, 5, 0], base, quote_net, e_delta: 0,
            x: [0; 2], kind, flags: 0,
        })
    }

    const S: u64 = 1_000_000_000;

    /// The T1 mint's 13 real curve legs (dev2, slots 450196501-450197213; the spec's appendix query):
    /// (slot, tx, outer, inner, is_buy, t, q, vt, vq, rt, rq, trader). Creator 2CFyw… = 1, 6Ljyr7Eo… = 6.
    type T1Leg = (u64, u32, u16, u16, bool, u64, u64, u64, u64, u64, u64, Id);
    const T1_LEGS: [T1Leg; 13] = [
        (450196501, 923, 3, 27, true, 80346362644573, 88859178201, 992653637355427, 1186685931639, 712753637355427, 88859178201, 1),
        (450196501, 924, 1, 6, true, 32130101425978, 39695372283, 960523535929449, 1226381303922, 680623535929449, 128554550484, 2),
        (450196501, 924, 3, 6, true, 29896604844106, 39397782299, 930626931085343, 1265779086221, 650726931085343, 167952332783, 3),
        (450196501, 925, 1, 6, true, 28090766603369, 39396432279, 902536164481974, 1305175518500, 622636164481974, 207348765062, 4),
        (450196514, 942, 2, 9, false, 40173181322286, 55619532144, 942709345804260, 1249555986356, 662809345804260, 151729232918, 1),
        (450196532, 804, 2, 9, false, 40173181322287, 51072877812, 982882527126547, 1198483108544, 702982527126547, 100656355106, 1),
        (450196603, 826, 3, 7, false, 32130101425978, 37937837177, 1015012628552525, 1160545271367, 735112628552525, 62718517929, 2),
        (450196616, 754, 3, 7, false, 28090766603369, 31253475448, 1043103395155894, 1129291795919, 763203395155894, 31465042481, 4),
        (450196616, 758, 3, 7, false, 29896604844106, 31465042475, 1073000000000000, 1097826753444, 793100000000000, 6, 3),
        (450196616, 820, 3, 6, true, 41918882589137, 44632444533, 1031081117410863, 1142459197977, 751181117410863, 44632444539, 5),
        (450197178, 473, 2, 6, false, 41918882589137, 44632444532, 1073000000000000, 1097826753445, 793100000000000, 7, 5),
        (450197179, 10, 4, 6, true, 49999999999998, 53657221577, 1023000000000002, 1151483975022, 743100000000002, 53657221584, 6),
        (450197213, 1, 5, 6, false, 49999999999998, 53657221576, 1073000000000000, 1097826753446, 793100000000000, 8, 6),
    ];
    pub(crate) const T1_MINT: Id = 0;
    pub(crate) const T1_CREATOR: Id = 1;
    pub(crate) const T1_TRADER: Id = 6;

    pub(crate) fn t1_legs() -> Vec<Leg> {
        T1_LEGS
            .iter()
            .map(|&(slot, tx, outer, inner, is_buy, t, q, vt, vq, rt, rq, trader)| {
                Leg::Curve(CurveLeg {
                    order: Order { slot, tx, outer, inner }, mint: T1_MINT, trader, t, q, fee: 0, vt, vq, rt, rq, args: [0; 4], fee_bps: 95,
                    creator_bps: 30, args_set: 0, variant: if is_buy { CurveVariant::Buy } else { CurveVariant::Sell }, is_buy, direct: true,
                })
            })
            .collect()
    }

    fn recent_of(legs: &[Leg]) -> Recent {
        let mut r = Recent::default();
        for l in legs {
            r.extend(l.key(), std::slice::from_ref(l), l.slot());
        }
        r
    }

    #[test]
    fn t1_passes_the_trigger_and_the_filters_in_raw_units() {
        // At epoch 1042 windows (50, 200, 20); the universe rejects T1 (PUMP quote), see signals.rs.
        let legs = t1_legs();
        let t = 450_197_213;
        assert_eq!(dev2().windows(t)[..3], [50, 200, 20]);
        let k = F7Key { creator: T1_CREATOR, coin: NONE, seg: Segment::Curve };
        let (leg, depth) = trigger(&legs[12..], &k, MAYHEM).expect("the 450197213 sell triggers");
        assert_eq!((leg.order(), leg.trader(), depth), (Order { slot: t, tx: 1, outer: 5, inner: 6 }, T1_TRADER, 53_657_221_584));
        let r = recent_of(&legs);
        assert!(f7_signal(r.get(T1_MINT), Segment::Curve, t, T1_CREATOR, &dev2()));
        // The 450197178 sell is not trigger-sized: 0.04 * pre.vq = 45.7 SOL > its 44.6 SOL.
        assert!(trigger(&legs[10..11], &k, MAYHEM).is_none());
        // The creator's sells at 450196514 and 450196532 are outside [t - 200, t]. A creator sell after the trigger
        // leg, in slot t, blocks.
        let mut late = legs.clone();
        late.push(curve((t, 2), T1_MINT, T1_CREATOR, false, 1, 1_097_826_753_445, 7));
        assert!(!f7_signal(recent_of(&late).get(T1_MINT), Segment::Curve, t, T1_CREATOR, &dev2()));
    }

    #[test]
    fn the_curve_trigger_conditions() {
        let k = F7Key { creator: 1, coin: NONE, seg: Segment::Curve };
        // pre rq = rq + q; trigger size q >= max(3 SOL, 0.04 * (vq + q)).
        let sell = |trader, q, vq, rq| [curve((10, 0), 0, trader, false, q, vq, rq)];
        assert_eq!(trigger(&sell(2, 5 * S, 100 * S, 30 * S), &k, MAYHEM).map(|x| x.1), Some(35 * S as i128));
        assert!(trigger(&sell(1, 5 * S, 100 * S, 30 * S), &k, MAYHEM).is_none()); // the creator
        assert!(trigger(&sell(MAYHEM, 5 * S, 100 * S, 30 * S), &k, MAYHEM).is_none());
        assert!(trigger(&sell(2, 3 * S - 1, 10 * S, 30 * S), &k, MAYHEM).is_none()); // below 3 SOL
        assert!(trigger(&sell(2, 5 * S, 120 * S, 30 * S), &k, MAYHEM).is_some()); // 0.04 * 125 = 5: equal passes
        assert!(trigger(&sell(2, 5 * S, 120 * S + 1, 30 * S), &k, MAYHEM).is_none());
        assert_eq!(trigger(&sell(2, 5 * S, 100 * S, 15 * S), &k, MAYHEM).map(|x| x.1), Some(20 * S as i128)); // 20 SOL: in
        assert!(trigger(&sell(2, 5 * S, 100 * S, 15 * S - 1), &k, MAYHEM).is_none());
        assert!(trigger(&sell(2, 5 * S, 100 * S, 70 * S), &k, MAYHEM).is_some()); // 75 SOL: in
        assert!(trigger(&sell(2, 5 * S, 100 * S, 70 * S + 1), &k, MAYHEM).is_none());
        assert!(trigger(&[curve((10, 0), 0, 2, true, 5 * S, 100 * S, 30 * S)], &k, MAYHEM).is_none()); // a buy
        // The first passing leg in chain order supplies depth and trader.
        let two = [curve((10, 0), 0, 1, false, 5 * S, 100 * S, 30 * S), curve((10, 1), 0, 3, false, 5 * S, 100 * S, 40 * S),
                   curve((10, 2), 0, 4, false, 5 * S, 100 * S, 50 * S)];
        assert_eq!(trigger(&two, &k, MAYHEM).map(|(l, d)| (l.trader(), d)), Some((3, 45 * S as i128)));
    }

    #[test]
    fn none_semantics_of_the_trader_tests() {
        // Curve: a NONE trader against a NONE creator is equal, so it blocks (Python `None != None` is False).
        let none_creator = F7Key { creator: NONE, coin: NONE, seg: Segment::Curve };
        assert!(trigger(&[curve((10, 0), 0, NONE, false, 5 * S, 100 * S, 30 * S)], &none_creator, MAYHEM).is_none());
        assert!(trigger(&[curve((10, 0), 0, 2, false, 5 * S, 100 * S, 30 * S)], &none_creator, MAYHEM).is_some());
        // Pool: a NONE creator or coin creator blocks no one (`trader != (creator or "")`), a NONE trader included.
        let leg = |trader| [pool((10, 0), 0, trader, PoolKind::Buy, (10 * S as i64, 0), (200 * S as i64, 0))];
        let k = |creator, coin| F7Key { creator, coin, seg: Segment::PoolReversed };
        assert!(trigger(&leg(NONE), &k(NONE, NONE), MAYHEM).is_some());
        assert!(trigger(&leg(NONE), &k(1, 2), MAYHEM).is_some());
        assert!(trigger(&leg(1), &k(1, NONE), MAYHEM).is_none());
        assert!(trigger(&leg(2), &k(NONE, 2), MAYHEM).is_none());
        // MAYHEM_AGENT is no test for pools.
        assert!(trigger(&leg(MAYHEM), &k(1, 2), MAYHEM).is_some());
        // The creator window: a NONE trader's sell blocks a NONE creator (`l.trader == creator`).
        let t = 450_200_000;
        let r = recent_of(&[pool((t - 10, 0), 0, 3, PoolKind::Sell, (5 * S as i64, 0), (200 * S as i64, 0)),
                            pool((t - 5, 0), 0, NONE, PoolKind::Buy, (S as i64, 0), (200 * S as i64, 0))]);
        assert!(!f7_signal(r.get(0), Segment::PoolReversed, t, NONE, &dev2()));
        assert!(f7_signal(r.get(0), Segment::PoolReversed, t, 9, &dev2()));
    }

    #[test]
    fn the_pool_trigger_conditions() {
        // Reversed: token_buy = sell, amount = base, depth = pre.b. Normal: token_buy = buy, amount = quote_net,
        // depth = e - virt.
        let k = |seg| F7Key { creator: 1, coin: 2, seg };
        let rev = |kind, base, b| [pool((10, 0), 0, 3, kind, (base, 0), (b, 0))];
        let depth = trigger(&rev(PoolKind::Buy, 6 * S as i64, 150 * S as i64), &k(Segment::PoolReversed), MAYHEM).map(|x| x.1);
        assert_eq!(depth, Some(150 * S as i128));
        assert!(trigger(&rev(PoolKind::Buy, 6 * S as i64, 150 * S as i64 - 1), &k(Segment::PoolReversed), MAYHEM).is_none());
        assert!(trigger(&rev(PoolKind::Sell, 6 * S as i64, 150 * S as i64), &k(Segment::PoolReversed), MAYHEM).is_none()); // a token buy
        assert!(trigger(&rev(PoolKind::Buy, 8 * S as i64, 200 * S as i64), &k(Segment::PoolReversed), MAYHEM).is_some()); // 0.04 * 200 = 8
        assert!(trigger(&rev(PoolKind::Buy, 8 * S as i64 - 1, 200 * S as i64), &k(Segment::PoolReversed), MAYHEM).is_none());
        assert!(trigger(&rev(PoolKind::Withdraw, 8 * S as i64, 200 * S as i64), &k(Segment::PoolReversed), MAYHEM).is_none());
        let norm = [pool((10, 0), 0, 3, PoolKind::Sell, (0, 8 * S as i64), (0, 200 * S as i64))];
        assert_eq!(trigger(&norm, &k(Segment::PoolNormal), MAYHEM).map(|x| x.1), Some(200 * S as i128));
        assert!(trigger(&norm, &k(Segment::PoolReversed), MAYHEM).is_none()); // read as reversed: depth 0
        for trader in [1, 2] {
            let leg = pool((10, 0), 0, trader, PoolKind::Sell, (0, 8 * S as i64), (0, 200 * S as i64));
            assert!(trigger(&[leg], &k(Segment::PoolNormal), MAYHEM).is_none());
        }
        // Above 2^53 the 4 % test is exact: 0.04 * (10**18 + 7) is 4e16; 4e16 - 1 >= it is False.
        let dep = 1_000_000_000_000_000_007_i64;
        assert!(trigger(&rev(PoolKind::Buy, 40_000_000_000_000_000, dep), &k(Segment::PoolReversed), MAYHEM).is_some());
        assert!(trigger(&rev(PoolKind::Buy, 39_999_999_999_999_999, dep), &k(Segment::PoolReversed), MAYHEM).is_none());
    }

    #[test]
    fn the_curve_windows() {
        let c = dev2(); // epoch 1042: n_in 50, n_cr 200, n_gap 20
        let t = 450_200_000;
        let buy = |s, q| curve((s, 0), 0, 3, true, q, 100 * S, 30 * S);
        let sell = |s, trader, q| curve((s, 1), 0, trader, false, q, 50 * S, 30 * S); // 4 SOL is trigger-sized
        let ok = |legs: &[Leg]| f7_signal(recent_of(legs).get(0), Segment::Curve, t, 1, &c);
        assert!(ok(&[buy(t - 50, S)]));
        assert!(!ok(&[buy(t - 51, S)])); // outside [t - 50, t - 1]: inflow 0
        assert!(!ok(&[buy(t, S)])); // slot t is not in the inflow window
        assert!(!ok(&[buy(t - 10, S), sell(t - 9, 3, S)])); // inflow exactly 0 blocks
        assert!(ok(&[buy(t - 10, S + 1), sell(t - 9, 3, S)]));
        // The creator: any sell in [t - 200, t], slot t included (after the trigger leg too).
        assert!(!ok(&[buy(t - 10, S), sell(t - 200, 1, 1)]));
        assert!(ok(&[buy(t - 10, S), sell(t - 201, 1, 1)]));
        assert!(!ok(&[buy(t - 10, S), sell(t, 2, S), curve((t, 9), 0, 1, false, 1, 100 * S, 30 * S)]));
        assert!(ok(&[buy(t - 10, S), curve((t - 5, 0), 0, 1, true, S, 100 * S, 30 * S)])); // a creator buy is fine
        // A trigger-sized sell in [t - 20, t - 1] blocks, whoever sold it and at any depth.
        assert!(!ok(&[buy(t - 30, 10 * S), sell(t - 20, 2, 4 * S)]));
        assert!(!ok(&[buy(t - 30, 10 * S), sell(t - 1, 1, 4 * S)])); // the creator's: both windows
        assert!(!ok(&[buy(t - 30, 10 * S), curve((t - 20, 1), 0, MAYHEM, false, 4 * S, 50 * S, 900 * S)]));
        assert!(ok(&[buy(t - 30, 10 * S), sell(t - 21, 2, 4 * S)]));
        assert!(ok(&[buy(t - 30, 10 * S), sell(t, 2, 4 * S)])); // the trigger itself, in slot t
        assert!(ok(&[buy(t - 30, 10 * S), sell(t - 5, 2, 3 * S - 1)])); // not trigger-sized
    }

    #[test]
    fn a_trigger_blocks_the_next_n_gap_slots() {
        // The trigger at t is a trigger-sized sell, so it blocks t + 1 .. t + 20 and not t + 21 (epoch 1042).
        let c = dev2();
        let t = 450_200_000;
        let legs = [curve((t - 30, 0), 0, 3, true, 40 * S, 100 * S, 30 * S), curve((t, 0), 0, 4, false, 5 * S, 100 * S, 30 * S)];
        let r = recent_of(&legs);
        assert!(f7_signal(r.get(0), Segment::Curve, t, 1, &c));
        for d in 1..=20 {
            assert!(!f7_signal(r.get(0), Segment::Curve, t + d, 1, &c), "t + {d}");
        }
        // At t + 21 the trigger has left the gap window; a buy at t + 1 keeps the inflow positive.
        let r2 = recent_of(&[legs[0], legs[1], curve((t + 1, 0), 0, 3, true, 40 * S, 100 * S, 30 * S)]);
        assert!(f7_signal(r2.get(0), Segment::Curve, t + 21, 1, &c));
    }

    #[test]
    fn the_pool_windows_count_trades_only_and_the_creator_not_the_coin_creator() {
        let c = fwd1053(); // n_in 61, n_cr 245, n_gap 24
        let t = 455_000_000;
        let rev = |s, trader, kind, base| pool((s, 0), 0, trader, kind, (base, 0), (1_000 * S as i64, 0));
        let ok = |legs: &[Leg]| f7_signal(recent_of(legs).get(0), Segment::PoolReversed, t, 1, &c);
        assert!(ok(&[rev(t - 61, 3, PoolKind::Sell, S as i64)])); // reversed: a sell is a token buy, inflow +
        assert!(!ok(&[rev(t - 62, 3, PoolKind::Sell, S as i64)]));
        assert!(!ok(&[rev(t - 5, 3, PoolKind::Deposit, S as i64), rev(t - 5, 3, PoolKind::Boost, S as i64)])); // no trades
        assert!(!ok(&[rev(t - 10, 3, PoolKind::Sell, S as i64), rev(t - 245, 1, PoolKind::Buy, 1)])); // creator sold SOL-side
        assert!(ok(&[rev(t - 10, 3, PoolKind::Sell, S as i64), rev(t - 246, 1, PoolKind::Buy, 1)]));
        // The creator window ends at t: the creator's sell after the trigger leg, in slot t, blocks.
        let trig = rev(t, 4, PoolKind::Buy, 45 * S as i64);
        assert!(ok(&[rev(t - 10, 3, PoolKind::Sell, S as i64), trig]));
        let late = pool((t, 1), 0, 1, PoolKind::Buy, (1, 0), (1_000 * S as i64, 0));
        assert!(!ok(&[rev(t - 10, 3, PoolKind::Sell, S as i64), trig, late]));
        assert!(ok(&[rev(t - 10, 3, PoolKind::Sell, S as i64), rev(t - 5, 2, PoolKind::Buy, 1)])); // 2 is only the coin creator
        assert!(ok(&[rev(t - 10, 3, PoolKind::Sell, S as i64), rev(t - 5, 1, PoolKind::Withdraw, 1)])); // not a trade
        // Gap: a trigger-sized SOL-side sell in [t - 24, t - 1], at any depth.
        let small = pool((t - 24, 0), 0, 4, PoolKind::Buy, (4 * S as i64, 0), (100 * S as i64, 0)); // at 100 SOL depth
        assert!(!ok(&[rev(t - 30, 3, PoolKind::Sell, 50 * S as i64), small]));
        assert!(!ok(&[rev(t - 30, 3, PoolKind::Sell, 50 * S as i64), rev(t - 1, 4, PoolKind::Buy, 45 * S as i64)]));
        assert!(ok(&[rev(t - 30, 3, PoolKind::Sell, 50 * S as i64), rev(t - 25, 4, PoolKind::Buy, 45 * S as i64)]));
        assert!(ok(&[rev(t - 30, 3, PoolKind::Sell, 50 * S as i64), rev(t - 1, 4, PoolKind::Buy, 40 * S as i64 - 1)]));
        // Normal: token_buy = buy, amount = quote_net.
        let norm = |s, kind, q| pool((s, 0), 0, 3, kind, (0, q), (0, 1_000 * S as i64));
        assert!(f7_signal(recent_of(&[norm(t - 5, PoolKind::Buy, 2), norm(t - 4, PoolKind::Sell, 1)]).get(0), Segment::PoolNormal, t, 1, &c));
        assert!(!f7_signal(recent_of(&[norm(t - 5, PoolKind::Buy, 1), norm(t - 4, PoolKind::Sell, 2)]).get(0), Segment::PoolNormal, t, 1, &c));
    }

    #[test]
    fn recent_keeps_400_slots_and_the_sweep_spares_kept_keys() {
        let mut r = Recent::default();
        let l = |s| curve((s, 0), 7, 3, true, 1, 1, 1);
        r.extend(7, &[l(1_000)], 1_000);
        r.extend(7, &[l(1_400)], 1_400);
        assert_eq!(r.get(7).map(VecDeque::len), Some(2)); // 1000 >= 1400 - 400
        r.extend(7, &[l(1_401)], 1_401);
        assert_eq!(r.get(7).map(|d| d.iter().map(Leg::slot).collect::<Vec<_>>()), Some(vec![1_400, 1_401]));
        r.extend(8, &[l(1_500)], 1_500);
        r.extend(9, &[l(1_200)], 1_200);
        r.sweep(1_402, |k| k == 9);
        assert_eq!((r.get(7).is_none(), r.last_seen(7), r.last_seen(8), r.last_seen(9)), (true, None, Some(1_500), Some(1_200)));
        assert_eq!(r.len(), 2);
    }

    #[test]
    fn the_back_of_a_keys_legs_is_its_last_state() {
        // engine.py:396-401: last_state[key] is the post of the slot's last leg; the trim keeps it, the sweep drops it.
        let mut r = Recent::default();
        assert_eq!(r.back(7), None);
        let a = [curve((1_000, 0), 7, 3, true, 1, 1, 1), curve((1_000, 2), 7, 4, false, 1, 1, 2)];
        r.extend(7, &a, 1_000);
        assert_eq!(r.back(7), Some(&a[1]));
        // 500 slots on, the trim drops slot 1000's legs and the back is the new slot's last leg.
        let b = [curve((1_500, 1), 7, 3, true, 1, 1, 3)];
        r.extend(7, &b, 1_500);
        assert_eq!((r.get(7).map(VecDeque::len), r.back(7)), (Some(1), Some(&b[0])));
        // A quiet key keeps it until the sweep drops its legs with its last_seen.
        r.sweep(1_600, |_| true);
        assert_eq!(r.back(7), Some(&b[0]));
        r.sweep(1_600, |_| false);
        assert_eq!((r.back(7), r.last_seen(7)), (None, None));
    }
}
