//! Exact integer fill math for the pump.fun bonding curve and PumpSwap pools: backtest/venues.py, byte for
//! byte. Checked against recorded trades, not a claim of coverage of every protocol variant:
//!
//!   curve sell             q = floor(t * Vq / (Vt + t))
//!   curve buy, exact out   q = floor(t * Vq / (Vt - t)) + 1
//!   curve buy, exact in    t = floor((q - 1) * Vt / (Vq + q - 1))
//!   pool sell              gross = floor(E * a / (B + a))
//!   pool buy, exact out    q = ceil(E * b / (B - b))
//!   pool buy, exact in     b = floor(B * (n - 1) / (E + n - 1))
//!   every fee component    ceil(amount * bps / 10000)
//!
//! Amounts are i128 with Python's floor division: products reach ~1e30 (tokens 1e15 x lamports 1e14), and a
//! pool's virtual quote (`virt`) is signed (pump-public-docs, NEGATIVE_VIRTUAL_QUOTE_RESERVES.md). The state
//! after a fill keeps every other field of the state before it (Python's `dataclasses.replace`).

pub const BPS: i128 = 10_000;
/// Separate from settlement costs: rule 2 corrects zero-cashback PumpSwap exact-in only.
/// Cashback retains its existing model, without a new source-parity claim.
pub const POOL_EXACT_IN_RULE: u8 = 2;

/// Python's `a // b`: the floor of the quotient, for either sign.
pub fn floor_div(a: i128, b: i128) -> i128 {
    let q = a / b;
    if a % b != 0 && ((a < 0) != (b < 0)) {
        q - 1
    } else {
        q
    }
}

/// Python's `-(-a // b)`: the ceiling of the quotient.
pub fn ceil_div(a: i128, b: i128) -> i128 {
    -floor_div(-a, b)
}

/// One fee component: ceil(amount * bps / 10000).
pub fn fee(amount: i128, bps: i128) -> i128 {
    ceil_div(amount * bps, BPS)
}

/// Largest n with n + sum(ceil(n * bps / 10000)) <= budget.
pub fn net_for_budget(budget: i128, bps: &[i128]) -> i128 {
    if budget <= 0 {
        return 0;
    }
    let cost = |n: i128| n + bps.iter().map(|&b| fee(n, b)).sum::<i128>();
    let mut n = floor_div(budget * BPS, BPS + bps.iter().sum::<i128>());
    while n > 0 && cost(n) > budget {
        n -= 1;
    }
    while cost(n + 1) <= budget {
        n += 1;
    }
    n
}

// --------------------------------------------------------------------------------------------- curve

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Curve {
    pub vt: i128, // virtual token reserves
    pub vq: i128, // virtual quote reserves
    pub rt: i128, // real token reserves
    pub rq: i128, // real quote reserves
    pub fee_bps: i128,
    pub creator_bps: i128,
}

impl Curve {
    pub fn complete(&self) -> bool {
        self.rt == 0
    }

    fn moved(&self, t: i128, q: i128) -> Curve {
        Curve { vt: self.vt - t, vq: self.vq + q, rt: self.rt - t, rq: self.rq + q, ..*self }
    }

    fn fees(&self, q: i128) -> i128 {
        fee(q, self.fee_bps) + fee(q, self.creator_bps)
    }
}

/// A fill: tokens moved, the quote moving into or out of the venue's pricing state, the trader's fees, the
/// quote the trader pays (buy) or receives (sell), and the venue state after it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Fill<S> {
    pub tokens: i128,
    pub curve_quote: i128,
    pub fees: i128,
    pub trader_quote: i128,
    pub state: S,
    pub completes: bool,
}

/// Spend at most `budget` lamports (fees included). Caps at the remaining real tokens.
pub fn curve_buy_exact_in(c: &Curve, budget: i128) -> Option<Fill<Curve>> {
    if c.complete() || budget <= 0 {
        return None;
    }
    let q = net_for_budget(budget, &[c.fee_bps, c.creator_bps]);
    if q <= 1 {
        return None;
    }
    let t = floor_div((q - 1) * c.vt, c.vq + q - 1);
    if t >= c.rt {
        return curve_buy_exact_out(c, c.rt);
    }
    if t <= 0 {
        return None;
    }
    let f = c.fees(q);
    Some(Fill { tokens: t, curve_quote: q, fees: f, trader_quote: q + f, state: c.moved(t, q), completes: false })
}

/// Buy exactly t tokens (t <= remaining real tokens).
pub fn curve_buy_exact_out(c: &Curve, t: i128) -> Option<Fill<Curve>> {
    if c.complete() || t <= 0 || t > c.rt || t >= c.vt {
        return None;
    }
    let q = floor_div(t * c.vq, c.vt - t) + 1;
    let f = c.fees(q);
    let s = c.moved(t, q);
    Some(Fill { tokens: t, curve_quote: q, fees: f, trader_quote: q + f, state: s, completes: s.rt == 0 })
}

pub fn curve_sell(c: &Curve, t: i128) -> Option<Fill<Curve>> {
    if c.complete() || t <= 0 {
        return None;
    }
    let q = floor_div(t * c.vq, c.vt + t).min(c.rq);
    let f = c.fees(q);
    Some(Fill { tokens: t, curve_quote: q, fees: f, trader_quote: (q - f).max(0), state: c.moved(-t, -q), completes: false })
}

// ---------------------------------------------------------------------------------------------- pool

/// A pool's pricing state: base reserves `b` and effective quote reserves `e` (quote vault + virtual quote).
/// `virt` only sets the price; the real vault is `e - virt`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Pool {
    pub b: i128,
    pub e: i128,
    pub lp_bps: i128,
    pub protocol_bps: i128,
    pub creator_bps: i128,
    pub cashback_bps: i128,
    pub virt: i128,
}

impl Pool {
    pub fn real(&self) -> i128 {
        self.e - self.virt
    }

    pub fn fee_list(&self) -> [i128; 4] {
        [self.lp_bps, self.protocol_bps, self.creator_bps, self.cashback_bps]
    }

    /// (LP fee, every fee): the LP fee stays in the pool, the others leave it.
    fn fees(&self, x: i128) -> (i128, i128) {
        let lp = fee(x, self.lp_bps);
        (lp, lp + fee(x, self.protocol_bps) + fee(x, self.creator_bps) + fee(x, self.cashback_bps))
    }

    fn with(&self, b: i128, e: i128) -> Pool {
        Pool { b, e, ..*self }
    }
}

/// Spend at most `budget` quote (fees included), like buy_exact_quote_in.
pub fn pool_buy_exact_in(p: &Pool, budget: i128) -> Option<Fill<Pool>> {
    if budget <= 0 || p.b <= 0 || p.e <= 0 {
        return None;
    }
    let (n, lp, f) = if p.cashback_bps == 0 {
        // Fees are fixed on the initial quotient BEFORE shaving the rounding excess.
        // Source: pinned SDK buyQuoteInput and recorded events in tests/fixtures/README.md.
        let n = floor_div(budget * BPS, BPS + p.fee_list().iter().sum::<i128>());
        let (lp, f) = p.fees(n);
        (n - (n + f - budget).max(0), lp, f)
    } else {
        // Cashback is not established by that evidence; preserve its existing model.
        let n = net_for_budget(budget, &p.fee_list());
        let (lp, f) = p.fees(n);
        (n, lp, f)
    };
    if n <= 1 {
        return None;
    }
    let b = floor_div(p.b * (n - 1), p.e + n - 1);
    if b <= 0 || b >= p.b {
        return None;
    }
    Some(Fill { tokens: b, curve_quote: n, fees: f, trader_quote: n + f, state: p.with(p.b - b, p.e + n + lp), completes: false })
}

pub fn pool_buy_exact_out(p: &Pool, b: i128) -> Option<Fill<Pool>> {
    if b <= 0 || b >= p.b || p.e <= 0 {
        return None;
    }
    let q = ceil_div(p.e * b, p.b - b);
    let (lp, f) = p.fees(q);
    Some(Fill { tokens: b, curve_quote: q, fees: f, trader_quote: q + f, state: p.with(p.b - b, p.e + q + lp), completes: false })
}

/// Sell `a` tokens; None if the payout exceeds the real vault (the program rejects it).
pub fn pool_sell(p: &Pool, a: i128) -> Option<Fill<Pool>> {
    if a <= 0 || p.b <= 0 || p.e <= 0 {
        return None;
    }
    let gross = floor_div(p.e * a, p.b + a);
    if gross > p.real() {
        return None;
    }
    let (lp, f) = p.fees(gross);
    Some(Fill {
        tokens: a,
        curve_quote: gross,
        fees: f,
        trader_quote: (gross - f).max(0),
        state: p.with(p.b + a, p.e - (gross - lp)),
        completes: false,
    })
}

/// Our own exit: sell as many of `a` tokens as the real vault can pay out (the rest stays unsold and is written
/// off by the caller). Fill.tokens tells how many were sold.
pub fn pool_sell_capped(p: &Pool, a: i128) -> Option<Fill<Pool>> {
    let f = pool_sell(p, a);
    if f.is_some() || a <= 0 || p.b <= 0 || p.e <= 0 || p.virt <= 0 {
        return f;
    }
    let (mut lo, mut hi) = (0, a); // largest k with gross(k) <= real; gross is increasing in k
    while lo < hi {
        let mid = floor_div(lo + hi + 1, 2);
        if floor_div(p.e * mid, p.b + mid) <= p.real() {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }
    if lo > 0 {
        pool_sell(p, lo)
    } else {
        None
    }
}

/// Remove liquidity: base and real quote leave pro rata; virtual quote stays.
pub fn pool_withdraw(p: &Pool, lp_in: i128, lp_supply: i128) -> Option<Pool> {
    if lp_supply <= 0 {
        return None;
    }
    Some(p.with(p.b - floor_div(p.b * lp_in, lp_supply), p.e - floor_div(p.real() * lp_in, lp_supply)))
}

pub fn pool_deposit(p: &Pool, base_in: i128, quote_in: i128) -> Pool {
    p.with(p.b + base_in, p.e + quote_in)
}

// ------------------------------------------------------------------------------------ reversed pools
// A reversed pool has WSOL as base and the token as quote. Buying the token is a pool sell of WSOL (fees come
// out of the tokens received); selling it is a pool buy of WSOL paid in tokens. Both return a Fill in token
// terms: tokens = tokens bought or spent, trader_quote = SOL paid or received.

pub fn rev_buy_token(p: &Pool, sol: i128) -> Option<Fill<Pool>> {
    let f = pool_sell(p, sol)?;
    if f.trader_quote <= 0 {
        return None;
    }
    Some(Fill { tokens: f.trader_quote, curve_quote: f.curve_quote, fees: f.fees, trader_quote: sol, state: f.state, completes: false })
}

pub fn rev_sell_token(p: &Pool, tokens: i128) -> Option<Fill<Pool>> {
    let f = pool_buy_exact_in(p, tokens)?;
    Some(Fill { tokens: f.trader_quote, curve_quote: f.curve_quote, fees: f.fees, trader_quote: f.tokens, state: f.state, completes: false })
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn curve() -> Curve {
        Curve { vt: 1_073_000_000_000_000, vq: 30_000_000_000, rt: 793_100_000_000_000, rq: 0, fee_bps: 95, creator_bps: 5 }
    }

    fn pool(b: i128, e: i128, lp: i128, pr: i128, cr: i128, cb: i128, virt: i128) -> Pool {
        Pool { b, e, lp_bps: lp, protocol_bps: pr, creator_bps: cr, cashback_bps: cb, virt }
    }

    fn cf(f: Option<Fill<Curve>>) -> Option<(i128, i128, i128, i128, i128, i128, i128, i128, bool)> {
        f.map(|f| (f.tokens, f.curve_quote, f.fees, f.trader_quote, f.state.vt, f.state.vq, f.state.rt, f.state.rq, f.completes))
    }

    fn pf(f: Option<Fill<Pool>>) -> Option<(i128, i128, i128, i128, i128, i128)> {
        f.map(|f| (f.tokens, f.curve_quote, f.fees, f.trader_quote, f.state.b, f.state.e))
    }

    #[test]
    fn recorded_exact_in_source_amounts() {
        let source = include_str!("../tests/fixtures/pumpswap-exact-in.tsv");
        let mut count = 0;
        for row in source.lines().skip(1) {
            let cols: Vec<_> = row.split('\t').collect();
            let n = |i: usize| cols[i].parse::<i128>().unwrap();
            let p = pool(n(7), n(8), n(10), n(11), n(12), 0, n(9));
            let f = pool_buy_exact_in(&p, n(13)).unwrap();
            assert_eq!((f.tokens, f.curve_quote, f.fees, f.trader_quote, f.state.b, f.state.e),
                       (n(14), n(15), n(16) + n(17) + n(18), n(19), n(20), n(21)), "{row}");
            assert_eq!(f.state.virt, p.virt);
            count += 1;
        }
        assert_eq!(count, 18);
    }

    #[test]
    fn cashback_retains_legacy_model_not_a_source_parity_claim() {
        let p = pool(123305752584269, 151538264082, 20, 5, 95, 5, 17504872179);
        assert_eq!(pf(pool_buy_exact_in(&p, 2585182)),
                   Some((2077539380, 2553264, 31918, 2585182, 123303675044889, 151540822453)));
        for budget in [-1, 0, 1, 2] {
            assert!(pool_buy_exact_in(&Pool { cashback_bps: 0, ..p }, budget).is_none());
        }
    }

    /// The values backtest/venues.py gives for the same inputs (computed 2026-10-16).
    #[test]
    fn the_same_numbers_as_venues_py() {
        assert_eq!((fee(1_000_001, 95), fee(0, 95), fee(-1_000_001, 95)), (9501, 0, -9500));
        assert_eq!((net_for_budget(500_000_000, &[95, 5]), net_for_budget(1, &[95, 5]), net_for_budget(0, &[95])), (495049504, 0, 0));
        let c = curve();
        assert_eq!(cf(curve_buy_exact_in(&c, 500_000_000)),
                   Some((17418831101315, 495049504, 4950496, 500000000, 1055581168898685, 30495049504, 775681168898685, 495049504, false)));
        assert_eq!(cf(curve_buy_exact_out(&c, 17_000_000_000_000)),
                   Some((17000000000000, 482954546, 4829547, 487784093, 1056000000000000, 30482954546, 776100000000000, 482954546, false)));
        assert_eq!(cf(curve_sell(&c, 17_000_000_000_000)), Some((17000000000000, 0, 0, 0, 1090000000000000, 30000000000, 810100000000000, 0, false)));
        let c2 = Curve { vt: 280_000_000_000_000, vq: 115_000_000_000, rt: 1_000_000_000, rq: 84_000_000_000, fee_bps: 95, creator_bps: 5 };
        assert_eq!(cf(curve_buy_exact_in(&c2, 50_000_000_000)),
                   Some((1000000000, 410716, 4108, 414824, 279999000000000, 115000410716, 0, 84000410716, true)));
        let p = pool(206_900_000_000_000, 84_990_359_060, 2, 93, 30, 0, 0);
        assert_eq!(pf(pool_buy_exact_in(&p, 500_000_000)), Some((1195225029453, 493827159, 6172841, 500000000, 205704774970547, 85484284985)));
        assert_eq!(pf(pool_buy_exact_out(&p, 1_000_000_000_000)), Some((1000000000000, 412774935, 5159687, 417934622, 205900000000000, 85403216550)));
        assert_eq!(pf(pool_sell(&p, 1_000_000_000_000)), Some((1000000000000, 408804035, 5110052, 403693983, 207900000000000, 84581636786)));
        let pn = pool(206_900_000_000_000, 84_990_359_060, 20, 5, 5, 0, -30_000_000); // negative virtual quote
        assert_eq!(pn.real(), 85020359060);
        assert_eq!(pf(pool_sell(&pn, 1_000_000_000_000)), Some((1000000000000, 408804035, 1226415, 407577620, 207900000000000, 84582372634)));
        let pb = pool(1_000_000_000, 10_000_000_000, 20, 5, 5, 0, 9_000_000_000); // a boost: the real vault is 1 SOL
        assert_eq!(pf(pool_sell(&pb, 2_000_000_000)), None);
        assert_eq!(pf(pool_sell_capped(&pb, 2_000_000_000)), Some((111111111, 999999999, 3000000, 996999999, 1111111111, 9002000001)));
        let w = pool_withdraw(&p, 1_000, 7_000).unwrap();
        assert_eq!((w.b, w.e, w.lp_bps, w.protocol_bps, w.creator_bps, w.virt), (177342857142858, 72848879195, 2, 93, 30, 0));
        let r = pool(500_000_000_000, 900_000_000_000_000, 20, 5, 5, 0, 0);
        assert_eq!(pf(rev_buy_token(&r, 1_000_000_000)), Some((1791017964070, 1796407185628, 5389221558, 1000000000, 501000000000, 898207185628744)));
        assert_eq!(pf(rev_sell_token(&r, 1_800_000_000_000)), Some((1800000000000, 1794616151544, 5383848456, 995024875, 499004975125, 901798205383848)));
    }

    #[test]
    fn floor_and_ceil_follow_python() {
        for (a, b, fl) in [(7, 2, 3), (-7, 2, -4), (7, -2, -4), (-7, -2, 3), (6, 3, 2), (-6, 3, -2)] {
            assert_eq!(floor_div(a, b), fl, "{a} // {b}");
            assert_eq!(ceil_div(a, b), -floor_div(-a, b));
        }
    }

    fn arb_curve() -> impl Strategy<Value = Curve> {
        // vt 1e14..1.1e15, vq 1e9..5e11, real tokens up to 80 % of vt, fees 0..200 bps.
        (100_000_000_000_000i128..1_100_000_000_000_000, 1_000_000_000i128..500_000_000_000, 1u32..80, 0i128..200_000_000_000,
         0i128..200, 0i128..200)
            .prop_map(|(vt, vq, share, rq, fb, cb)| Curve { vt, vq, rt: vt * share as i128 / 100, rq, fee_bps: fb, creator_bps: cb })
    }

    fn arb_pool() -> impl Strategy<Value = Pool> {
        (1_000_000i128..1_000_000_000_000_000, 1_000_000i128..1_000_000_000_000_000, 0i128..100, 0i128..100, 0i128..100, 0i128..50,
         -1_000_000_000i128..1_000_000_000)
            .prop_map(|(b, e, lp, pr, cr, cb, virt)| Pool { b, e, lp_bps: lp, protocol_bps: pr, creator_bps: cr, cashback_bps: cb, virt })
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(2_000))]

        #[test]
        fn net_for_budget_is_the_largest_affordable_amount(budget in 0i128..1_000_000_000_000_000, fb in 0i128..300, cb in 0i128..300) {
            let n = net_for_budget(budget, &[fb, cb]);
            let cost = |n: i128| n + fee(n, fb) + fee(n, cb);
            prop_assert!(n >= 0);
            if budget > 0 {
                prop_assert!(cost(n) <= budget || n == 0);
                prop_assert!(cost(n + 1) > budget);
            }
        }

        #[test]
        fn a_curve_buy_never_costs_more_than_its_budget_and_k_never_falls(c in arb_curve(), budget in 2i128..100_000_000_000_000) {
            if let Some(f) = curve_buy_exact_in(&c, budget) {
                prop_assert!(f.trader_quote <= budget);
                prop_assert!(f.tokens > 0 && f.tokens <= c.rt);
                prop_assert!(f.state.vt * f.state.vq >= c.vt * c.vq); // rounding favours the curve
                if !f.completes {
                    // exact in and exact out agree: buying those tokens exactly costs at most the quote spent
                    let o = curve_buy_exact_out(&c, f.tokens).unwrap();
                    prop_assert!(o.curve_quote <= f.curve_quote);
                }
            }
        }

        #[test]
        fn a_curve_round_trip_never_makes_money(c in arb_curve(), t in 1i128..10_000_000_000_000) {
            if let Some(buy) = curve_buy_exact_out(&c, t) {
                if let Some(sell) = curve_sell(&buy.state, t) {
                    prop_assert!(sell.trader_quote <= buy.trader_quote);
                }
            }
        }

        #[test]
        fn pool_fills_keep_k_and_never_pay_out_more_than_the_real_vault(p in arb_pool(), x in 1i128..100_000_000_000_000) {
            if let Some(f) = pool_buy_exact_in(&p, x) {
                prop_assert!(f.trader_quote <= x);
                prop_assert!(f.state.b * f.state.e >= p.b * p.e);
                let o = pool_buy_exact_out(&p, f.tokens).unwrap();
                prop_assert!(o.curve_quote < f.curve_quote); // ceil(E b / (B - b)) <= n - 1
            }
            if let Some(f) = pool_sell(&p, x) {
                prop_assert!(f.curve_quote <= p.real());
                prop_assert!(f.state.b * f.state.e >= p.b * p.e);
                prop_assert!(f.trader_quote >= 0);
            }
            if let Some(f) = pool_sell_capped(&p, x) {
                prop_assert!(f.tokens <= x && f.curve_quote <= p.real());
            }
        }

        #[test]
        fn reversed_fills_are_the_pool_fills_in_token_terms(p in arb_pool(), x in 1i128..1_000_000_000_000) {
            prop_assert_eq!(rev_buy_token(&p, x).map(|f| f.state), pool_sell(&p, x).filter(|f| f.trader_quote > 0).map(|f| f.state));
            prop_assert_eq!(rev_sell_token(&p, x).map(|f| (f.tokens, f.trader_quote)),
                            pool_buy_exact_in(&p, x).map(|f| (f.trader_quote, f.tokens)));
        }
    }
}
