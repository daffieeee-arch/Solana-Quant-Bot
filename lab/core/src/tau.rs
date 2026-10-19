//! The tolerance of a historical trade whose own slippage limit is unknown (router calls, curve trades without their
//! arguments): backtest/replay.py `Tau`. M1 C2; the porting spec is research/m1-c2-positionsim-spec-2026-10-19.md §3.7.
//!
//! - zero: any worse fill fails; inf: none fails; empirical (the paper engine's): a tolerance drawn per trade from the
//!   distribution of direct calls (tau_empirical.json per venue, side and size bucket), deterministically by the trade's
//!   place, `hash(order) % 10_000`; 0.99 or more means no limit.
//! - The quantiles are generated constants (tau/table.rs, from tools/tau_table.py; backtest/tests/test_tau_table.py pins
//!   them to the JSON), so lab/core reads no JSON.

use crate::legs::{Order, Venue};

mod table;
pub use table::{SHA256 as TABLE_SHA256, SIZE_BUCKETS};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tau {
    Empirical,
    Zero,
    Inf,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

/// replay.py `_bucket`: the first size edge the amount is below, else the last bucket.
pub fn bucket(amount: i128) -> usize {
    SIZE_BUCKETS.iter().position(|&e| amount < e).unwrap_or(SIZE_BUCKETS.len())
}

impl Tau {
    /// `Tau.tol(leg, venue, side, amount)`: None is no limit.
    pub fn tol(self, order: Order, venue: Venue, side: Side, amount: i128) -> Option<f64> {
        match self {
            Tau::Inf => None,
            Tau::Zero => Some(0.0),
            Tau::Empirical => {
                let t = if venue == Venue::Curve { &table::CURVE } else { &table::POOL };
                let qs = t[side as usize][bucket(amount)];
                if qs.is_empty() {
                    return None;
                }
                // u = (hash(order) % 10_000) / 10_000 * (len - 1): the int quotient is exact in f64 (both below 2^53)
                let u = f64::from(order.tau_u()) / 10_000.0 * (qs.len() - 1) as f64;
                let lo = u as usize;
                let hi = (lo + 1).min(qs.len() - 1);
                let x = qs[lo] + (qs[hi] - qs[lo]) * (u - lo as f64);
                (x < 0.99).then_some(x)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn o(slot: u64, tx: u32, outer: u16, inner: u16) -> Order {
        Order { slot, tx, outer, inner }
    }

    #[test]
    fn buckets_follow_the_size_edges() {
        let b = [0, 499_999_999, 500_000_000, 1_999_999_999, 2_000_000_000, 9_999_999_999, 10_000_000_000, i128::MAX].map(bucket);
        assert_eq!(b, [0, 0, 1, 1, 2, 2, 3, 3]);
        assert_eq!(TABLE_SHA256, "270e7434120778d7571f66dfcfa779b8a593ee566457f7ac081de37f9dfc923b");
    }

    #[test]
    fn the_empirical_draw_is_pythons() {
        // lab-py Tau("empirical").tol(leg with this order, venue, side, amount): (order, hash % 10000, venue, side, amount,
        // the result's bits or None), every venue, side and bucket at five orders.
        for (ord, h, venue, side, amount, want) in super::vectors::TAU {
            let order = o(ord.0, ord.1, ord.2, ord.3);
            assert_eq!(order.tau_u(), h, "{ord:?}");
            let got = Tau::Empirical.tol(order, venue, side, amount);
            assert_eq!(got.map(f64::to_bits), want, "{ord:?} {venue:?} {side:?} {amount}");
        }
        let any = o(1, 0, 0, 0);
        assert_eq!((Tau::Zero.tol(any, Venue::Pool, Side::Sell, 5), Tau::Inf.tol(any, Venue::Pool, Side::Sell, 5)), (Some(0.0), None));
    }
}

#[cfg(test)]
mod vectors;
