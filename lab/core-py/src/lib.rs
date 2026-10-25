//! `labcore`: lab-core for the Python backtest (lab/docs/rust-live-route-design-2026-10-16.md §12, M0).
//!
//! Plain integers in and out, so backtest/venues_core.py rebuilds venues.py's own dataclasses from them and the
//! callers cannot tell the two apart. A curve fill is (tokens, curve_quote, fees, trader_quote, vt, vq, rt, rq,
//! completes); a pool fill is (tokens, curve_quote, fees, trader_quote, b, e); None where venues.py returns None.

use lab_core::venues::{self as v, Curve, Fill, Pool};
use pyo3::prelude::*;

type CurveOut = (i128, i128, i128, i128, i128, i128, i128, i128, bool);
type PoolOut = (i128, i128, i128, i128, i128, i128);

fn curve(vt: i128, vq: i128, rt: i128, rq: i128, fee_bps: i128, creator_bps: i128) -> Curve {
    Curve { vt, vq, rt, rq, fee_bps, creator_bps }
}

#[allow(clippy::too_many_arguments)]
fn pool(b: i128, e: i128, lp_bps: i128, protocol_bps: i128, creator_bps: i128, cashback_bps: i128, virt: i128) -> Pool {
    Pool { b, e, lp_bps, protocol_bps, creator_bps, cashback_bps, virt }
}

fn cout(f: Option<Fill<Curve>>) -> Option<CurveOut> {
    f.map(|f| (f.tokens, f.curve_quote, f.fees, f.trader_quote, f.state.vt, f.state.vq, f.state.rt, f.state.rq, f.completes))
}

fn pout(f: Option<Fill<Pool>>) -> Option<PoolOut> {
    f.map(|f| (f.tokens, f.curve_quote, f.fees, f.trader_quote, f.state.b, f.state.e))
}

#[pyfunction]
fn fee(amount: i128, bps: i128) -> i128 {
    v::fee(amount, bps)
}

#[pyfunction]
fn net_for_budget(budget: i128, bps: Vec<i128>) -> i128 {
    v::net_for_budget(budget, &bps)
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn curve_buy_exact_in(vt: i128, vq: i128, rt: i128, rq: i128, fee_bps: i128, creator_bps: i128, budget: i128) -> Option<CurveOut> {
    cout(v::curve_buy_exact_in(&curve(vt, vq, rt, rq, fee_bps, creator_bps), budget))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn curve_buy_exact_out(vt: i128, vq: i128, rt: i128, rq: i128, fee_bps: i128, creator_bps: i128, t: i128) -> Option<CurveOut> {
    cout(v::curve_buy_exact_out(&curve(vt, vq, rt, rq, fee_bps, creator_bps), t))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn curve_sell(vt: i128, vq: i128, rt: i128, rq: i128, fee_bps: i128, creator_bps: i128, t: i128) -> Option<CurveOut> {
    cout(v::curve_sell(&curve(vt, vq, rt, rq, fee_bps, creator_bps), t))
}

macro_rules! pool_fill {
    ($name:ident) => {
        #[pyfunction]
        #[allow(clippy::too_many_arguments)]
        fn $name(b: i128, e: i128, lp_bps: i128, protocol_bps: i128, creator_bps: i128, cashback_bps: i128, virt: i128, x: i128) -> Option<PoolOut> {
            pout(v::$name(&pool(b, e, lp_bps, protocol_bps, creator_bps, cashback_bps, virt), x))
        }
    };
}

pool_fill!(pool_buy_exact_in);
pool_fill!(pool_buy_exact_out);
pool_fill!(pool_sell);
pool_fill!(pool_sell_capped);
pool_fill!(rev_buy_token);
pool_fill!(rev_sell_token);

/// The pool's (b, e) after a withdrawal, or None.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn pool_withdraw(b: i128, e: i128, lp_bps: i128, protocol_bps: i128, creator_bps: i128, cashback_bps: i128, virt: i128, lp_in: i128,
                 lp_supply: i128) -> Option<(i128, i128)> {
    v::pool_withdraw(&pool(b, e, lp_bps, protocol_bps, creator_bps, cashback_bps, virt), lp_in, lp_supply).map(|p| (p.b, p.e))
}

#[pymodule]
fn labcore(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("POOL_EXACT_IN_RULE", v::POOL_EXACT_IN_RULE)?;
    m.add_function(wrap_pyfunction!(fee, m)?)?;
    m.add_function(wrap_pyfunction!(net_for_budget, m)?)?;
    m.add_function(wrap_pyfunction!(curve_buy_exact_in, m)?)?;
    m.add_function(wrap_pyfunction!(curve_buy_exact_out, m)?)?;
    m.add_function(wrap_pyfunction!(curve_sell, m)?)?;
    m.add_function(wrap_pyfunction!(pool_buy_exact_in, m)?)?;
    m.add_function(wrap_pyfunction!(pool_buy_exact_out, m)?)?;
    m.add_function(wrap_pyfunction!(pool_sell, m)?)?;
    m.add_function(wrap_pyfunction!(pool_sell_capped, m)?)?;
    m.add_function(wrap_pyfunction!(rev_buy_token, m)?)?;
    m.add_function(wrap_pyfunction!(rev_sell_token, m)?)?;
    m.add_function(wrap_pyfunction!(pool_withdraw, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
