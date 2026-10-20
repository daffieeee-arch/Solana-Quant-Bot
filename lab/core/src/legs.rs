//! The engine's legs: one pump.fun curve trade or one PumpSwap pool event, as the Python replay builds them
//! (backtest/streams.py `_curve_legs`, `pool_leg`; the dataclasses in backtest/replay.py), compact. M1 C1a of
//! lab/docs/rust-live-route-design-2026-10-16.md; the porting spec is research/m1-c1a-legs-spec-2026-10-19.md.
//!
//! - Keys (mints, pools, traders) are interned ids (`Id`); `NONE` stands for Python's None.
//! - A curve leg keeps the logged post-trade reserves; `pre` is derived (streams.py:35-38).
//! - A pool leg is built from the store row and the pool's context (last virtual quote and fee tiers) at emission
//!   (streams.py:45-68), so it holds the pre-state and the deltas; `post` follows.
//! - All state arithmetic is in i128 through `crate::venues`, as Python's integers.
//! - `Leg` and `Event` are the feed's event as the engine reads it (paper/feed.py `Event`), for the signals (M1 C1b).

use crate::venues::{Curve, Pool};

pub const PUMP: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
pub const PUMP_AMM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
pub const DEFAULT_KEY: &str = "11111111111111111111111111111111";
pub const WSOL: &str = "So11111111111111111111111111111111111111112";
/// A curve quoted in either counts as SOL (backtest/strategies.py SOL_QUOTES; the engine's rule, not the wallets').
pub const SOL_QUOTES: [&str; 2] = [DEFAULT_KEY, WSOL];
pub const MAYHEM_AGENT: &str = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s";
/// Pool buys that fix the token amount (backtest/data.py POOL_EXACT_OUT: buy, buy_v2).
pub const POOL_EXACT_OUT: [&str; 2] = ["66063d1201daebea", "b817ee6167c5d33d"];
/// The boost crank's inner buy (backtest/data.py BOOST_BUY_DISC): dropped, its BoostBuyAndBurnEvent is the leg.
pub const BOOST_BUY_DISC: &str = "694406af000723a2";
/// The hold-out [lo, hi): a replay touching it is refused (paper/feed.py).
pub const HOLDOUT: (u64, u64) = (452_304_000, 454_464_000);

/// An interned key; `NONE` is Python's None (never equal to a real id).
pub type Id = u32;
pub const NONE: Id = u32::MAX;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Venue {
    Curve,
    Pool,
}

impl Venue {
    pub fn as_str(self) -> &'static str {
        match self {
            Venue::Curve => "curve",
            Venue::Pool => "pool",
        }
    }
}

/// An event's place in the chain: (slot, tx_index, outer_ix, inner_ix). Derived Ord is the replay's order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Order {
    pub slot: u64,
    pub tx: u32,
    pub outer: u16,
    pub inner: u16,
}

const XXPRIME_1: u64 = 11_400_714_785_074_694_791;
const XXPRIME_2: u64 = 14_029_467_366_897_019_727;
const XXPRIME_5: u64 = 2_870_177_450_012_600_261;
const MERSENNE_61: u64 = (1 << 61) - 1;

impl Order {
    /// CPython 3.13's `hash((slot, tx, outer, inner))` (Objects/tupleobject.c, xxHash lanes; an int below 2^61 - 1
    /// hashes to itself): the PositionSim's Tau draws on it (backtest/replay.py `hash(leg.order) % 10_000`).
    pub fn py_hash(&self) -> i64 {
        let mut acc = XXPRIME_5;
        for x in [self.slot, u64::from(self.tx), u64::from(self.outer), u64::from(self.inner)] {
            let lane = x % MERSENNE_61;
            acc = acc.wrapping_add(lane.wrapping_mul(XXPRIME_2)).rotate_left(31).wrapping_mul(XXPRIME_1);
        }
        acc = acc.wrapping_add(4 ^ (XXPRIME_5 ^ 3_527_539));
        if acc == u64::MAX {
            return 1_546_275_796;
        }
        acc as i64
    }

    /// `hash(order) % 10_000` with Python's modulo (never negative).
    pub fn tau_u(&self) -> u32 {
        self.py_hash().rem_euclid(10_000) as u32
    }
}

/// The curve instruction behind a TradeEvent, by its parent instruction's discriminator (backtest/data.py PUMP_IX);
/// anything else, or none, is `Other`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum CurveVariant {
    Buy,
    BuyExactSolIn,
    BuyV2,
    BuyV3,
    BuyExactQuoteInV2,
    BuyExactQuoteInV3,
    MultiHopCurveSwap,
    Sell,
    SellV2,
    SellV3,
    Other,
}

impl CurveVariant {
    pub fn from_disc(disc: Option<&str>) -> Self {
        match disc {
            Some("66063d1201daebea") => Self::Buy,
            Some("38fc74089edfcd5f") => Self::BuyExactSolIn,
            Some("b817ee6167c5d33d") => Self::BuyV2,
            Some("07051dc4f5176550") => Self::BuyV3,
            Some("c2ab1c46684d5b2f") => Self::BuyExactQuoteInV2,
            Some("e1f7501ed5b38488") => Self::BuyExactQuoteInV3,
            Some("e19a7516d756f667") => Self::MultiHopCurveSwap,
            Some("33e685a4017f83ad") => Self::Sell,
            Some("5df6823ce7e940b2") => Self::SellV2,
            Some("1c92de7726c469d5") => Self::SellV3,
            _ => Self::Other,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Buy => "buy",
            Self::BuyExactSolIn => "buy_exact_sol_in",
            Self::BuyV2 => "buy_v2",
            Self::BuyV3 => "buy_v3",
            Self::BuyExactQuoteInV2 => "buy_exact_quote_in_v2",
            Self::BuyExactQuoteInV3 => "buy_exact_quote_in_v3",
            Self::MultiHopCurveSwap => "multi_hop_curve_swap",
            Self::Sell => "sell",
            Self::SellV2 => "sell_v2",
            Self::SellV3 => "sell_v3",
            Self::Other => "other",
        }
    }

    /// A buy that spends a quote budget (backtest/replay.py EXACT_IN_CURVE; an unknown instruction counts too).
    pub fn exact_in(self) -> bool {
        matches!(self, Self::BuyExactSolIn | Self::BuyExactQuoteInV2 | Self::BuyExactQuoteInV3 | Self::MultiHopCurveSwap | Self::Other)
    }
}

/// One curve trade (backtest/replay.py CurveLeg). The reserves are the logged post-trade ones; `args` are the
/// instruction's budget, max_cost, min_tokens and min_out (bit i of `args_set` when present).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CurveLeg {
    pub order: Order,
    pub mint: Id,
    pub trader: Id,
    pub t: u64,
    pub q: u64,
    /// fee + creator_fee
    pub fee: u64,
    pub vt: u64,
    pub vq: u64,
    pub rt: u64,
    pub rq: u64,
    pub args: [u64; 4],
    pub fee_bps: u32,
    pub creator_bps: u32,
    pub args_set: u8,
    pub variant: CurveVariant,
    pub is_buy: bool,
    pub direct: bool,
}

impl CurveLeg {
    pub fn post(&self) -> Curve {
        Curve {
            vt: i128::from(self.vt),
            vq: i128::from(self.vq),
            rt: i128::from(self.rt),
            rq: i128::from(self.rq),
            fee_bps: i128::from(self.fee_bps),
            creator_bps: i128::from(self.creator_bps),
        }
    }

    /// The state before the trade (streams.py:35-38): a buy took t tokens out and q quote in.
    pub fn pre(&self) -> Curve {
        let (p, t, q) = (self.post(), i128::from(self.t), i128::from(self.q));
        let s = if self.is_buy { 1 } else { -1 };
        Curve { vt: p.vt + s * t, vq: p.vq - s * q, rt: p.rt + s * t, rq: p.rq - s * q, ..p }
    }

    pub fn arg(&self, i: usize) -> Option<u64> {
        (self.args_set & (1 << i) != 0).then_some(self.args[i])
    }

    pub fn budget(&self) -> Option<u64> {
        self.arg(0)
    }
    pub fn max_cost(&self) -> Option<u64> {
        self.arg(1)
    }
    pub fn min_tokens(&self) -> Option<u64> {
        self.arg(2)
    }
    pub fn min_out(&self) -> Option<u64> {
        self.arg(3)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum PoolKind {
    Buy,
    Sell,
    Boost,
    Withdraw,
    Deposit,
}

impl PoolKind {
    pub fn as_str(self) -> &'static str {
        match self {
            PoolKind::Buy => "buy",
            PoolKind::Sell => "sell",
            PoolKind::Boost => "boost",
            PoolKind::Withdraw => "withdraw",
            PoolKind::Deposit => "deposit",
        }
    }

    pub fn is_trade(self) -> bool {
        matches!(self, PoolKind::Buy | PoolKind::Sell)
    }
}

/// A pool's context while the stream runs (streams.py `ctx`): the last virtual quote and fee tiers, which withdraw
/// and deposit events do not log. Empty (0) at the stream's start.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PoolCtx {
    pub virt: i64,
    pub fees: [u32; 4],
}

pub const EXACT_OUT: u8 = 1;
pub const DIRECT: u8 = 2;
pub const FARMER: u8 = 4;
pub const X0: u8 = 8;
pub const X1: u8 = 16;

/// One pool event as the engine sees it (backtest/replay.py PoolLeg). `pre` is (b, e, fees, virt); `post` moves b by
/// the base amount and e by `e_delta`. `x` holds the kind's optional amounts: a trade's limit_quote and limit_base, a
/// boost's boost_left, a withdraw's or deposit's lp_amount and lp_supply (flags X0, X1 when present).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PoolLeg {
    pub order: Order,
    pub pool: Id,
    pub trader: Id,
    pub b: i64,
    pub e: i64,
    pub virt: i64,
    pub fees: [u32; 4],
    pub base: i64,
    pub quote_net: i64,
    pub e_delta: i64,
    pub x: [u64; 2],
    pub kind: PoolKind,
    pub flags: u8,
}

impl PoolLeg {
    pub fn pre(&self) -> Pool {
        Pool {
            b: i128::from(self.b),
            e: i128::from(self.e),
            lp_bps: i128::from(self.fees[0]),
            protocol_bps: i128::from(self.fees[1]),
            creator_bps: i128::from(self.fees[2]),
            cashback_bps: i128::from(self.fees[3]),
            virt: i128::from(self.virt),
        }
    }

    /// streams.py:56-57, 66: a buy, boost or withdraw takes base out; a sell or deposit puts it in.
    pub fn post(&self) -> Pool {
        let p = self.pre();
        let base = i128::from(self.base);
        let b = match self.kind {
            PoolKind::Sell | PoolKind::Deposit => p.b + base,
            _ => p.b - base,
        };
        Pool { b, e: p.e + i128::from(self.e_delta), ..p }
    }

    pub fn flag(&self, f: u8) -> bool {
        self.flags & f != 0
    }

    fn x(&self, i: usize, f: u8) -> Option<u64> {
        self.flag(f).then_some(self.x[i])
    }

    pub fn limit_quote(&self) -> Option<u64> {
        if self.kind.is_trade() { self.x(0, X0) } else { None }
    }
    pub fn limit_base(&self) -> Option<u64> {
        if self.kind.is_trade() { self.x(1, X1) } else { None }
    }
    pub fn boost_left(&self) -> Option<u64> {
        if self.kind == PoolKind::Boost { self.x(0, X0) } else { None }
    }
    pub fn lp_amount(&self) -> Option<u64> {
        if matches!(self.kind, PoolKind::Withdraw | PoolKind::Deposit) { self.x(0, X0) } else { None }
    }
    pub fn lp_supply(&self) -> Option<u64> {
        if matches!(self.kind, PoolKind::Withdraw | PoolKind::Deposit) { self.x(1, X1) } else { None }
    }

    /// (token_buy, sol_amount, sol_depth) as the store's SOL-side columns (backtest/strategies.py `sol_side`).
    pub fn sol_side(&self, reversed: bool) -> (bool, i128, i128) {
        let pre = self.pre();
        if reversed {
            (self.kind == PoolKind::Sell, i128::from(self.base), pre.b)
        } else {
            (self.kind == PoolKind::Buy, i128::from(self.quote_net), pre.e - pre.virt)
        }
    }
}

/// One leg of either venue (paper/feed.py `Event.leg`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Leg {
    Curve(CurveLeg),
    Pool(PoolLeg),
}

impl Leg {
    pub fn venue(&self) -> Venue {
        match self {
            Leg::Curve(_) => Venue::Curve,
            Leg::Pool(_) => Venue::Pool,
        }
    }

    pub fn order(&self) -> Order {
        match self {
            Leg::Curve(l) => l.order,
            Leg::Pool(l) => l.order,
        }
    }

    pub fn slot(&self) -> u64 {
        self.order().slot
    }

    pub fn trader(&self) -> Id {
        match self {
            Leg::Curve(l) => l.trader,
            Leg::Pool(l) => l.trader,
        }
    }

    /// The feed's key: the mint of a curve leg, the pool of a pool leg.
    pub fn key(&self) -> Id {
        match self {
            Leg::Curve(l) => l.mint,
            Leg::Pool(l) => l.pool,
        }
    }
}

/// One event of the feed in chain order (paper/feed.py `Event`): the venue, the key (mint or pool), the slot's block
/// time (None when the store has none) and the leg. A replay event has no arrival (feed.py:32-34), so no latency.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Event {
    pub venue: Venue,
    pub key: Id,
    pub block_time: Option<i64>,
    pub leg: Leg,
}

impl Event {
    /// The venue and key from the leg, so the three always agree.
    pub fn new(leg: Leg, block_time: Option<i64>) -> Self {
        Event { venue: leg.venue(), key: leg.key(), block_time, leg }
    }
}

/// Live only (M3-11): when the recorder had an event's transaction (paper/feed.py `Event.arrival`, from the segment's
/// arrivals/ and _segment.json): its local receive time and the server's created time in unix ns, and its segment's
/// written_unix (seconds). A replay event has none; the engine reads it for the signal latency (signals.rs `Latency`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Arrival {
    pub received: Option<u64>,
    pub created: Option<i64>,
    pub written: Option<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn o(slot: u64, tx: u32, outer: u16, inner: u16) -> Order {
        Order { slot, tx, outer, inner }
    }

    #[test]
    fn the_order_hash_is_cpythons() {
        // lab-py 3.13.15: hash((450144000, 12, 3, 4)) and two more (research/m1-c1a-legs-spec-2026-10-19.md §5.4).
        assert_eq!(o(450_144_000, 12, 3, 4).py_hash(), 2_365_464_660_030_220_358);
        assert_eq!(o(450_359_999, 1500, 0, 0).py_hash(), 540_364_745_922_108_609);
        assert_eq!(o(455_112_000, 7, 2, 11).py_hash(), -6_079_317_273_388_658_544);
        assert_eq!((o(450_144_000, 12, 3, 4).tau_u(), o(455_112_000, 7, 2, 11).tau_u()), (358, 1456));
        assert!(o(1, 2, 3, 4) < o(1, 2, 3, 5) && o(1, 2, 4, 0) > o(1, 2, 3, 9) && o(2, 0, 0, 0) > o(1, 9, 9, 9));
    }

    #[test]
    fn variants_follow_the_discriminator() {
        assert_eq!(CurveVariant::from_disc(Some("38fc74089edfcd5f")), CurveVariant::BuyExactSolIn);
        assert_eq!(CurveVariant::from_disc(Some("38FC74089EDFCD5F")), CurveVariant::Other); // exact, case-sensitive
        assert_eq!(CurveVariant::from_disc(None).as_str(), "other");
        assert!(CurveVariant::Other.exact_in() && !CurveVariant::Buy.exact_in() && CurveVariant::MultiHopCurveSwap.exact_in());
    }

    #[test]
    fn a_curve_legs_pre_state_undoes_the_trade() {
        let leg = CurveLeg {
            order: o(1, 0, 0, 1), mint: 0, trader: NONE, t: 100, q: 7, fee: 1, vt: 1_000, vq: 500, rt: 800, rq: 300,
            args: [0; 4], fee_bps: 95, creator_bps: 30, args_set: 0, variant: CurveVariant::Buy, is_buy: true, direct: true,
        };
        let pre = leg.pre();
        assert_eq!((pre.vt, pre.vq, pre.rt, pre.rq, pre.fee_bps), (1_100, 493, 900, 293, 95));
        let sell = CurveLeg { is_buy: false, ..leg };
        assert_eq!((sell.pre().vt, sell.pre().vq), (900, 507));
        assert_eq!((leg.budget(), CurveLeg { args: [5, 0, 0, 0], args_set: 1, ..leg }.budget()), (None, Some(5)));
    }

    #[test]
    fn a_pool_legs_post_state_and_sol_side() {
        let buy = PoolLeg {
            order: o(1, 0, 0, 1), pool: 0, trader: 1, b: 1_000, e: 600, virt: 100, fees: [20, 5, 5, 0], base: 10, quote_net: 7,
            e_delta: 6, x: [u64::MAX, 9], kind: PoolKind::Buy, flags: X0 | DIRECT,
        };
        assert_eq!((buy.post().b, buy.post().e, buy.post().virt), (990, 606, 100));
        assert_eq!(buy.sol_side(false), (true, 7, 500));
        assert_eq!(buy.sol_side(true), (false, 10, 1_000));
        assert_eq!((buy.limit_quote(), buy.limit_base(), buy.boost_left()), (Some(u64::MAX), None, None));
        let deposit = PoolLeg { kind: PoolKind::Deposit, e_delta: 7, flags: X0 | X1, ..buy };
        assert_eq!((deposit.post().b, deposit.post().e, deposit.lp_amount(), deposit.lp_supply()), (1_010, 607, Some(u64::MAX), Some(9)));
    }

    #[test]
    fn an_events_venue_and_key_come_from_its_leg() {
        let pool = PoolLeg {
            order: o(7, 1, 2, 3), pool: 4, trader: 5, b: 0, e: 0, virt: 0, fees: [0; 4], base: 0, quote_net: 0, e_delta: 0, x: [0; 2],
            kind: PoolKind::Sell, flags: 0,
        };
        let ev = Event::new(Leg::Pool(pool), Some(1_790_000_000));
        assert_eq!((ev.venue, ev.key, ev.leg.slot(), ev.leg.trader(), ev.leg.order()), (Venue::Pool, 4, 7, 5, o(7, 1, 2, 3)));
        let curve = CurveLeg {
            order: o(8, 0, 0, 0), mint: 9, trader: NONE, t: 0, q: 0, fee: 0, vt: 0, vq: 0, rt: 0, rq: 0, args: [0; 4], fee_bps: 0,
            creator_bps: 0, args_set: 0, variant: CurveVariant::Sell, is_buy: false, direct: true,
        };
        let ev = Event::new(Leg::Curve(curve), None);
        assert_eq!((ev.venue, ev.key, ev.leg.slot(), ev.leg.trader(), ev.block_time), (Venue::Curve, 9, 8, NONE, None));
    }
}
