//! A flow position's pool flow: the organic net SOL inflow over slot windows, fed leg by leg (paper/engine.py
//! `LiveFlowIndex`, :97-132). The flow exits read it (exits.rs `FlowTime`, `FlowTpTime`). M1 C2; the porting spec is
//! research/m1-c2-positionsim-spec-2026-10-19.md §1.6 and §3.8.
//!
//! - Each flow position has its own index (engine.py:886), fed with the pool's recent legs and the entry slot's legs at
//!   the entry (:902, 907-908), then with every later slot's legs before the position sees them (:379-380). So a
//!   decision at the last slot can find legs of a later slot in it already; `flow(lo, hi)` leaves them out.
//! - Only buys and sells count, each order once. A farmer's leg takes its place with 0, any other leg adds its SOL
//!   amount signed by its side (legs.rs `sol_side`; in a reversed pool a sell is the token buy and base the SOL).
//! - A late leg (a slot below the last one) is inserted after the legs of its slot (`bisect_right`) and the sums after
//!   it are built again, as Python does. Sums are i128: Python's ints do not overflow, and a few reversed-pool amounts
//!   near 2^63 already would in i64.
//! - `flow` prunes: once its lower bound passes more than 256 legs and more than half of them, the legs before it go,
//!   and so do their orders from the seen set (a pruned order can count again if it comes back). The prune is a side
//!   effect of the query, so the exit rules call `flow` exactly where Python calls it.
//! - Live only (M3): a late farmer-flag fix (live.py:614, engine.py:422-428) does not change a leg already in here.

use crate::legs::{Leg, Order, FARMER};
use std::collections::HashSet;

/// The prune's floor: the query's lower bound must pass more legs than this (engine.py:129).
pub const PRUNE_MIN: usize = 256;

/// One flow position's index of its pool's legs (engine.py `LiveFlowIndex`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveFlowIndex {
    /// The pool is reversed (WSOL its base): the position's orientation (engine.py:886, `p.orientation`).
    reversed: bool,
    /// The legs' slots, ascending (a late leg after the legs of its slot).
    slots: Vec<u64>,
    /// Prefix sums: `cum[k + 1] - cum[k]` is leg k's flow. One longer than `slots`; after a prune `cum[0]` is no
    /// longer 0, only the differences count.
    cum: Vec<i128>,
    /// The orders added (until a prune drops those before its lower bound).
    seen: HashSet<Order>,
    /// The queries in call order, (lo, hi, value): the tests compare them with Python's.
    #[cfg(test)]
    pub(crate) log: Vec<(u64, u64, i128)>,
}

impl LiveFlowIndex {
    /// The memory log (C4-2): the legs' slots and prefix sums and the orders seen (`crate::mem`).
    pub fn heap_bytes(&self) -> usize {
        crate::mem::vec(&self.slots) + crate::mem::vec(&self.cum) + crate::mem::set(&self.seen)
    }

    /// An empty index for a pool position; `reversed` is the position's orientation (engine.py:886,
    /// `LiveFlowIndex(p.orientation)`).
    pub fn new(reversed: bool) -> Self {
        LiveFlowIndex {
            reversed,
            slots: Vec::new(),
            cum: vec![0],
            seen: HashSet::new(),
            #[cfg(test)]
            log: Vec::new(),
        }
    }

    pub fn reversed(&self) -> bool {
        self.reversed
    }

    /// The legs kept.
    pub fn len(&self) -> usize {
        self.slots.len()
    }

    pub fn is_empty(&self) -> bool {
        self.slots.is_empty()
    }

    /// engine.py `add` (:107-123): the buys and sells not seen yet, in the order given. Boosts, deposits and withdraws
    /// are skipped. A pool position's legs are pool legs only; a curve leg here fails, as in Python (a CurveLeg has no
    /// `kind`).
    pub fn add<'a>(&mut self, legs: impl IntoIterator<Item = &'a Leg>) {
        for l in legs {
            let Leg::Pool(l) = l else { panic!("flow index: a curve leg at {:?}; a pool position has pool legs", l.order()) };
            if !l.kind.is_trade() || self.seen.contains(&l.order) {
                continue;
            }
            self.seen.insert(l.order);
            let (token_buy, a, _) = l.sol_side(self.reversed);
            let v = if l.flag(FARMER) { 0 } else if token_buy { a } else { -a };
            let slot = l.order.slot;
            if self.slots.last().is_some_and(|&last| slot < last) {
                // a late leg: in its place (bisect_right), then the sums after it again (engine.py:114-121)
                let i = self.slots.partition_point(|&s| s <= slot);
                self.slots.insert(i, slot);
                let steps: Vec<i128> = self.cum[i..].windows(2).map(|w| w[1] - w[0]).collect();
                self.cum.truncate(i + 1);
                for x in std::iter::once(v).chain(steps) {
                    let last = self.cum[self.cum.len() - 1];
                    self.cum.push(last + x);
                }
                continue;
            }
            self.slots.push(slot);
            let last = self.cum[self.cum.len() - 1];
            self.cum.push(last + v);
        }
    }

    /// engine.py `flow` (:125-132): the net organic SOL inflow over lo <= slot <= hi (bisect_left, bisect_right).
    /// Then the prune: when lo passes more than PRUNE_MIN legs and more than half of the legs kept (`i > len // 2`),
    /// the legs before lo go, with their orders (slot < lo) from the seen set.
    pub fn flow(&mut self, lo: u64, hi: u64) -> i128 {
        let i = self.slots.partition_point(|&s| s < lo);
        let j = self.slots.partition_point(|&s| s <= hi);
        let v = self.cum[j] - self.cum[i];
        if i > PRUNE_MIN && i > self.slots.len() / 2 {
            self.seen.retain(|o| o.slot >= lo);
            self.slots.drain(..i);
            self.cum.drain(..i);
        }
        #[cfg(test)]
        self.log.push((lo, hi, v));
        v
    }
}

#[cfg(test)]
mod cases;

#[cfg(test)]
pub(crate) mod tests {
    use super::cases::{Op, CASES};
    use super::*;
    use crate::legs::{PoolKind, PoolLeg};

    /// A pool leg with these fields; the rest are fixed (sol_side's depth, which no flow reads).
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn pool_leg(slot: u64, tx: u32, outer: u16, inner: u16, kind: PoolKind, base: i64, quote_net: i64, farmer: bool) -> Leg {
        Leg::Pool(PoolLeg {
            order: Order { slot, tx, outer, inner },
            pool: 1,
            trader: 2,
            b: 1_000_000,
            e: 1_000_000,
            virt: 0,
            fees: [0; 4],
            base,
            quote_net,
            e_delta: 0,
            x: [0; 2],
            kind,
            flags: if farmer { FARMER } else { 0 },
        })
    }

    /// Leg k of a run (tools/exit_cases.py `run_legs`, the same formula; the cases carry Python's sums to check it):
    /// slot slot0 + k / per_slot, tx k % per_slot; a buy when k % 3 == 0, else a sell; base base0 + 1_000 k,
    /// quote_net twice that; a farmer when k % 5 == 4.
    pub(crate) fn run_legs(slot0: u64, n: u32, per_slot: u32, base0: i64) -> Vec<Leg> {
        (0..n)
            .map(|k| {
                let base = base0 + 1_000 * i64::from(k);
                let kind = if k % 3 == 0 { PoolKind::Buy } else { PoolKind::Sell };
                pool_leg(slot0 + u64::from(k / per_slot), k % per_slot, 0, 0, kind, base, 2 * base, k % 5 == 4)
            })
            .collect()
    }

    fn sorted_seen(f: &LiveFlowIndex) -> Vec<(u64, u32, u16, u16)> {
        let mut s: Vec<_> = f.seen.iter().map(|o| (o.slot, o.tx, o.outer, o.inner)).collect();
        s.sort_unstable();
        s
    }

    #[test]
    fn the_index_follows_pythons_live_flow_index_op_by_op() {
        // lab-py paper.engine.LiveFlowIndex on the same legs (tools/exit_cases.py flow): each query's value, and after
        // every op the legs kept, the first and last sum, the seen orders (all of them on the small cases).
        for case in CASES {
            let mut f = LiveFlowIndex::new(case.reversed);
            for (k, (op, want)) in case.ops.iter().enumerate() {
                let at = format!("{} op {k}", case.name);
                match op {
                    Op::Add(legs) => {
                        let legs: Vec<Leg> = legs.iter().map(|&(s, tx, o, i, kind, b, q, fm)| pool_leg(s, tx, o, i, kind, b, q, fm)).collect();
                        f.add(&legs);
                    }
                    Op::Run { slot0, n, per_slot, base0, check } => {
                        let legs = run_legs(*slot0, *n, *per_slot, *base0);
                        let got = legs.iter().fold((0u64, 0i128, 0u32), |(s, b, fm), l| match l {
                            Leg::Pool(p) => (s + p.order.slot, b + i128::from(p.base), fm + u32::from(p.flag(FARMER))),
                            Leg::Curve(_) => unreachable!(),
                        });
                        assert_eq!(got, *check, "{at}: the run's legs are not the generator's");
                        f.add(&legs);
                    }
                    Op::Flow(lo, hi, v) => assert_eq!(f.flow(*lo, *hi), *v, "{at}: flow({lo}, {hi})"),
                }
                let got = (f.slots.len(), f.seen.len(), f.slots.first().copied(), f.cum[0], f.cum[f.cum.len() - 1]);
                assert_eq!(got, (want.len, want.seen, want.first, want.cum0, want.cum_last), "{at}");
                assert_eq!(f.cum.len(), f.slots.len() + 1, "{at}");
                if let Some((slots, cum, seen)) = want.full {
                    assert_eq!((f.slots.as_slice(), f.cum.as_slice()), (slots, cum), "{at}");
                    assert_eq!(sorted_seen(&f), seen, "{at}");
                }
            }
        }
    }

    #[test]
    fn a_curve_leg_is_refused() {
        use crate::legs::{CurveLeg, CurveVariant, NONE};
        let c = CurveLeg {
            order: Order { slot: 5, tx: 0, outer: 0, inner: 0 }, mint: 0, trader: NONE, t: 1, q: 1, fee: 0, vt: 0, vq: 0, rt: 0, rq: 0,
            args: [0; 4], fee_bps: 0, creator_bps: 0, args_set: 0, variant: CurveVariant::Buy, is_buy: true, direct: true,
        };
        let r = std::panic::catch_unwind(|| LiveFlowIndex::new(true).add(&[Leg::Curve(c)]));
        assert!(r.is_err());
    }
}
