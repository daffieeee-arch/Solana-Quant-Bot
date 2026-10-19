//! One open paper position, slot by slot: backtest/replay.py's PositionSim, the entries the paper engine makes
//! (`open_pool_position`, and the curve entry from the engine's own legs), the re-execution of the token's historical
//! trades after our own (`rerun_curve`, `rerun_pool`) and the other traders' token holdings (`Holdings`). M1 C2-3 of the
//! Rust live route; the porting spec is research/m1-c2-positionsim-spec-2026-10-19.md §3.5-3.7.
//!
//! - Amounts are exact ints as in Python. The steps that mix an int and a float are `exact`'s: `int(i * f)` is
//!   `mul_trunc`, an int / int is `true_div` and an int < float is `lt`.
//! - Not ported:
//!   - the historical mid prices (hist_mid_entry, hist_mid_exit), which are not journaled;
//!   - the batch replay's graduation sale (`_graduation_exit`); the paper engine passes its own on_complete (C2-4b),
//!     so a completed curve returns `Step::Completed` and the caller sells;
//!   - the mark list: a position keeps the count, the running peak (engine.py `_peak`, the highest mark at its first
//!     slot) and the last mark (the positions row's mark).
//! - The curve entry needs no curve history (spec D4): the state at the decision, the state before the entry slot and
//!   the other traders' holdings come from the engine (`open_curve_entry`).
//! - `Cfg::window_end` is the tournament's WINDOW_END; paper keeps it None.

use std::collections::HashMap;
use std::fmt;

use crate::exact::{lt, mul_trunc, true_div};
use crate::exits::{Ctx, ExitReason, ExitRule};
use crate::legs::{CurveLeg, Id, Leg, PoolKind, PoolLeg, Venue, DIRECT, EXACT_OUT};
use crate::tau::{Side, Tau};
use crate::venues::{self as v, floor_div, Curve, Fill, Pool};

/// PositionSim's `max_slots` in the paper engine and the replay (replay.py:633, 688): a position still open this many
/// slots after its entry is ended at its next event.
pub const MAX_HOLD: u64 = 400_000;

/// replay.py SEED_POOL: the standard pool a graduated token's sale goes to when it has no pool of its own.
pub const SEED_POOL: Pool = Pool { b: 206_900_000_000_000, e: 84_990_359_060, lp_bps: 2, protocol_bps: 93, creator_bps: 30, cashback_bps: 0, virt: 0 };

/// Why a position closed, or why it never opened (the orders row's reason, the exits row's exit_reason).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reason {
    /// The exit rule's decision.
    Rule(ExitReason),
    EndOfData,
    WindowEnd,
    /// Set by the caller's on_complete (engine.py:983).
    Graduation,
    EntrySlippage,
    NoPoolState,
    NoCurveState,
    NoFill,
    CurveComplete,
    AfterWindowEnd,
}

impl Reason {
    pub fn as_str(self) -> &'static str {
        match self {
            Reason::Rule(r) => r.as_str(),
            Reason::EndOfData => "end_of_data",
            Reason::WindowEnd => "window_end",
            Reason::Graduation => "graduation",
            Reason::EntrySlippage => "entry_slippage",
            Reason::NoPoolState => "no_pool_state",
            Reason::NoCurveState => "no_curve_state",
            Reason::NoFill => "no_fill",
            Reason::CurveComplete => "curve_complete",
            Reason::AfterWindowEnd => "after_window_end",
        }
    }
}

/// replay.py `Result`, without the mint and the historical mid prices. The marks are kept as their count, the running
/// peak and the last one (`mark`).
#[derive(Clone, Debug, PartialEq)]
pub struct PosResult {
    pub decision_slot: u64,
    pub entry_slot: u64,
    pub venue: Venue,
    pub size: i128,
    pub tokens: i128,
    /// What the entry paid (venue amount and fees).
    pub cost: i128,
    pub exit_decision_slot: Option<u64>,
    pub exit_slot: Option<u64>,
    pub proceeds: i128,
    pub exit_reason: Option<Reason>,
    /// Our entry landed and failed (it pays the failed-tx cost).
    pub entry_failed: bool,
    /// No entry was possible: no transaction, no cost.
    pub skipped: bool,
    pub exit_attempts: u64,
    pub cf_graduation: bool,
    pub seed_pool: bool,
    /// The real vault could not pay for all our tokens; the rest was written off.
    pub vault_capped: bool,
    pub reverted_direct: u64,
    pub reverted_router: u64,
    pub sells_dropped: u64,
    pub sells_scaled: u64,
    pub mayhem_reverted: u64,
    /// How many marks the position took (`len(r.marks)`).
    pub marks: u64,
    /// engine.py `_peak`: (slot, mark) of the highest mark, the earliest slot on a tie; None without marks.
    pub peak: Option<(u64, i128)>,
    /// `r.marks[-1]`: (slot, mark) of the last mark.
    pub last_mark: Option<(u64, i128)>,
}

impl PosResult {
    pub fn new(decision_slot: u64, entry_slot: u64, size: i128, venue: Venue) -> Self {
        PosResult {
            decision_slot,
            entry_slot,
            venue,
            size,
            tokens: 0,
            cost: 0,
            exit_decision_slot: None,
            exit_slot: None,
            proceeds: 0,
            exit_reason: None,
            entry_failed: false,
            skipped: false,
            exit_attempts: 0,
            cf_graduation: false,
            seed_pool: false,
            vault_capped: false,
            reverted_direct: 0,
            reverted_router: 0,
            sells_dropped: 0,
            sells_scaled: 0,
            mayhem_reverted: 0,
            marks: 0,
            peak: None,
            last_mark: None,
        }
    }

    /// `r.marks.append((slot, mark))`. The peak is `max(marks, key=(mark, -slot))`: a higher mark, or the same mark at
    /// an earlier slot (`>`, not `>=`, keeps the first of the tied slots).
    fn mark(&mut self, slot: u64, mark: i128) {
        self.marks += 1;
        if self.peak.is_none_or(|(s, m)| mark > m || (mark == m && slot < s)) {
            self.peak = Some((slot, mark));
        }
        self.last_mark = Some((slot, mark));
    }

    fn skip(mut self, why: Reason) -> Opened {
        self.skipped = true;
        self.exit_reason = Some(why);
        Opened::No(self)
    }

    fn fail(mut self) -> Opened {
        self.entry_failed = true;
        self.exit_reason = Some(Reason::EntrySlippage);
        Opened::No(self)
    }
}

// ------------------------------------------------------------------------------------- the insertion index

/// replay.py `_insertion_index` over a list given by (slot, tx) per index, ordered by slot: the index of the first leg
/// after our transaction at `slot`, placed at `q` of the slot's transactions. k = int(q * the number of distinct
/// consecutive tx values at `slot`); k at or past the end gives the slot's end, else the first leg of the k-th one.
/// `q` must be at least 0 (Python would index from the end).
pub fn insertion_index_by(n: usize, at: impl Fn(usize) -> (u64, i64), slot: u64, q: f64) -> usize {
    assert!(q >= 0.0, "insertion at q = {q}");
    let (mut lo, mut hi) = (0, n);
    while lo < hi {
        let mid = (lo + hi) / 2;
        if at(mid).0 < slot {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    let (i, mut j) = (lo, lo);
    let mut txs: Vec<i64> = Vec::new();
    while j < n && at(j).0 == slot {
        let tx = at(j).1;
        if txs.last() != Some(&tx) {
            txs.push(tx);
        }
        j += 1;
    }
    let k = (q * txs.len() as f64) as usize; // int(): toward zero
    if k >= txs.len() {
        return j;
    }
    (i..j).find(|&x| at(x).1 == txs[k]).expect("the k-th transaction is in the slot")
}

/// `_insertion_index` on legs.
pub fn insertion_index(legs: &[Leg], slot: u64, q: f64) -> usize {
    insertion_index_by(legs.len(), |i| (legs[i].slot(), i64::from(legs[i].order().tx)), slot, q)
}

// ------------------------------------------------------------------------------------------- the holdings

/// replay.py `Holdings`: the other traders' token holdings, historical (`hist`) and counterfactual (`cf`, the re-run
/// trades'), so that a sell of tokens a re-run buy did not get is scaled down or dropped. Curve: exact (from the
/// create); a normal pool: inexact; a reversed pool: disabled (its base is WSOL, not the token).
///
/// seed and apply always write hist and cf together, so one (hist, cf) pair per trader is Python's two dicts, and the
/// pair is kept even at cf 0: a new trader's reverted buy leaves (t, 0), and that trader's sell must then be cut
/// (`cf.get(trader, h)` would let it through whole if the key were missing). NONE (Python's None) is one trader.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Holdings {
    exact: bool,
    enabled: bool,
    pairs: HashMap<Id, (i128, i128)>,
}

impl Holdings {
    pub fn new(exact: bool, enabled: bool) -> Self {
        Holdings { exact, enabled, pairs: HashMap::new() }
    }

    /// The memory log (C4-2): the traders' table (`crate::mem::map`).
    pub fn heap_bytes(&self) -> usize {
        crate::mem::map(&self.pairs)
    }

    /// One seed leg (`seed`): a buy of t is +t, a sell -t, in both. Per-trader sums seed the same.
    pub fn seed(&mut self, trader: Id, net: i128) {
        self.apply(trader, net, net);
    }

    /// How much of a sell of `a` tokens the trader can still make.
    pub fn sell_amount(&self, trader: Id, a: i128) -> i128 {
        if !self.enabled {
            return a;
        }
        let (h, c) = self.pairs.get(&trader).copied().unwrap_or((0, 0)); // cf.get(trader, h): h when absent
        if c >= h {
            return a;
        }
        if self.exact {
            if h <= 0 || c <= 0 {
                return 0;
            }
            return if a >= h { c } else { floor_div(a * c, h) };
        }
        (a - (h - c)).max(0)
    }

    pub fn apply(&mut self, trader: Id, hist: i128, cf: i128) {
        let p = self.pairs.entry(trader).or_insert((0, 0));
        p.0 += hist;
        p.1 += cf;
    }

    /// (hist, cf) of a trader, None when never written.
    pub fn get(&self, trader: Id) -> Option<(i128, i128)> {
        self.pairs.get(&trader).copied()
    }

    pub fn len(&self) -> usize {
        self.pairs.len()
    }

    /// Every (trader, hist, cf), by trader.
    pub fn sorted(&self) -> Vec<(Id, i128, i128)> {
        let mut v: Vec<_> = self.pairs.iter().map(|(&t, &(h, c))| (t, h, c)).collect();
        v.sort_unstable();
        v
    }

    pub fn is_empty(&self) -> bool {
        self.pairs.is_empty()
    }
}

/// The re-run's sells cut by the holdings (replay.py `diag`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Diag {
    pub sells_dropped: u64,
    pub sells_scaled: u64,
}

/// `_apply_hold`: book a leg's historical and counterfactual token delta (`sign` -1 undoes it). A curve leg moves
/// ±t; a pool buy or sell ±base; other pool kinds nothing (and write nothing).
pub fn apply_hold(hold: &mut Holdings, leg: &Leg, cf: i128, sign: i128) {
    let hist = match leg {
        Leg::Curve(l) => {
            let t = i128::from(l.t);
            if l.is_buy { t } else { -t }
        }
        Leg::Pool(l) => match l.kind {
            PoolKind::Buy => i128::from(l.base),
            PoolKind::Sell => -i128::from(l.base),
            _ => return,
        },
    };
    hold.apply(leg.trader(), sign * hist, sign * cf);
}

// ------------------------------------------------------------------------------------------ the re-runs

/// `int(x)` of a float: toward zero. Python raises on inf and NaN; so does this.
fn trunc(x: f64) -> i128 {
    assert!(x.is_finite(), "int({x})");
    x as i128
}

/// replay.py `_rerun_curve`: one historical curve leg again on `state`. Returns the state after it and the trader's
/// counterfactual token delta, or None when the leg fails (its own limits when the trader set them, else the tau
/// tolerance; a complete curve; the Mayhem agent, whose virtual-quote reset is not modelled).
pub fn rerun_curve(state: &Curve, leg: &CurveLeg, tau: Tau, hold: &Holdings, diag: &mut Diag, mayhem_agent: Id) -> Option<(Curve, i128)> {
    if state.complete() || leg.trader == mayhem_agent {
        return None;
    }
    let state = Curve { fee_bps: i128::from(leg.fee_bps), creator_bps: i128::from(leg.creator_bps), ..*state }; // leg.pre's tiers
    let (t, q, fee) = (i128::from(leg.t), i128::from(leg.q), i128::from(leg.fee));
    let tol = |side| tau.tol(leg.order, Venue::Curve, side, q);
    if leg.is_buy {
        if leg.variant.exact_in() {
            let f = v::curve_buy_exact_in(&state, leg.budget().map_or(q + fee, i128::from))?;
            let min_t = match leg.min_tokens() {
                Some(m) if leg.direct => Some(i128::from(m)),
                _ => tol(Side::Buy).map(|x| mul_trunc(t, 1.0 - x)),
            };
            if min_t.is_some_and(|m| f.tokens < m) {
                return None;
            }
            return Some((f.state, f.tokens));
        }
        let f = v::curve_buy_exact_out(&state, t.min(state.rt))?;
        let max_c = match leg.max_cost() {
            Some(m) if leg.direct => Some(i128::from(m)),
            _ => tol(Side::Buy).map(|x| mul_trunc(q + fee, 1.0 + x)),
        };
        if max_c.is_some_and(|m| f.trader_quote > m) {
            return None;
        }
        return Some((f.state, f.tokens));
    }
    let a = hold.sell_amount(leg.trader, t);
    if a == 0 {
        diag.sells_dropped += 1;
        return Some((state, 0));
    }
    if a < t {
        diag.sells_scaled += 1;
    }
    let f = v::curve_sell(&state, a)?;
    let logged_net = (q - fee).max(0);
    let min_o = match leg.min_out() {
        Some(m) if leg.direct => Some(floor_div(i128::from(m) * a, t)),
        _ => tol(Side::Sell).map(|x| trunc(true_div(logged_net * a, t) * (1.0 - x))),
    };
    if min_o.is_some_and(|m| f.trader_quote < m) {
        return None;
    }
    Some((f.state, -a))
}

/// replay.py `_rerun_pool`: one historical pool leg again on `state`, with the fee tiers and virtual quote of the
/// leg's own pre state. A direct leg uses only its own limit (none: no check); a router leg the tau tolerance.
pub fn rerun_pool(state: &Pool, leg: &PoolLeg, tau: Tau, hold: &Holdings, diag: &mut Diag) -> Option<(Pool, i128)> {
    let pre = leg.pre();
    let state = Pool {
        lp_bps: pre.lp_bps,
        protocol_bps: pre.protocol_bps,
        creator_bps: pre.creator_bps,
        cashback_bps: pre.cashback_bps,
        virt: pre.virt,
        ..*state
    };
    let (base, qn, direct) = (i128::from(leg.base), i128::from(leg.quote_net), leg.flag(DIRECT));
    let tol = |side| tau.tol(leg.order, Venue::Pool, side, qn);
    let at = || format!("the pool {} leg at {:?}", leg.kind.as_str(), leg.order);
    match leg.kind {
        PoolKind::Boost => {
            // a fee-free buy with the quote the crank used; the tokens are burned
            if qn <= 1 {
                return Some((state, 0));
            }
            let b = floor_div(state.b * (qn - 1), state.e + qn - 1);
            Some((Pool { b: state.b - b, e: state.e + qn, ..state }, 0))
        }
        PoolKind::Withdraw => {
            // V.pool_withdraw(state, lp_amount, lp_supply): Python fails on a None it reaches
            let supply = i128::from(leg.lp_supply().unwrap_or_else(|| panic!("{}: lp_supply is None (Python fails on it)", at())));
            if supply <= 0 {
                return None;
            }
            let lp_in = i128::from(leg.lp_amount().unwrap_or_else(|| panic!("{}: lp_amount is None (Python fails on it)", at())));
            v::pool_withdraw(&state, lp_in, supply).map(|s| (s, 0))
        }
        PoolKind::Deposit => Some((v::pool_deposit(&state, base, qn), 0)),
        PoolKind::Buy if leg.flag(EXACT_OUT) => {
            let f = v::pool_buy_exact_out(&state, base)?;
            let lim = if direct { leg.limit_quote().map(i128::from) } else { tol(Side::Buy).map(|x| mul_trunc(qn, 1.0 + x)) };
            if lim.is_some_and(|l| f.trader_quote > l) {
                return None;
            }
            Some((f.state, f.tokens))
        }
        PoolKind::Buy => {
            // V.pool_buy_exact_in(state, limit_quote): a drained pool returns None before the budget is read
            if state.b <= 0 || state.e <= 0 {
                return None;
            }
            let budget = i128::from(leg.limit_quote().unwrap_or_else(|| panic!("{}: limit_quote is None (Python fails on it)", at())));
            let f = v::pool_buy_exact_in(&state, budget)?;
            let lim = if direct { leg.limit_base().map(i128::from) } else { tol(Side::Buy).map(|x| mul_trunc(base, 1.0 - x)) };
            if lim.is_some_and(|l| f.tokens < l) {
                return None;
            }
            Some((f.state, f.tokens))
        }
        PoolKind::Sell => {
            let a = hold.sell_amount(leg.trader, base);
            if a == 0 {
                diag.sells_dropped += 1;
                return Some((state, 0));
            }
            if a < base {
                diag.sells_scaled += 1;
            }
            let f = v::pool_sell(&state, a)?;
            let lim = if direct {
                leg.limit_quote().map(|l| floor_div(i128::from(l) * a, base))
            } else {
                tol(Side::Sell).map(|x| trunc(true_div(qn * a, base) * (1.0 - x)))
            };
            if lim.is_some_and(|l| f.trader_quote < l) {
                return None;
            }
            Some((f.state, -a))
        }
    }
}

// ------------------------------------------------------------------------------------------ the position

/// Where the position trades (replay.py `pool_venue`, open_curve_position): our buy, our sale (marks and exit), the
/// holdings and the re-run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SimVenue {
    /// curve_buy_exact_in, curve_sell; exact holdings, seeded.
    Curve,
    /// pool_buy_exact_in, pool_sell_capped; inexact holdings, not seeded.
    PoolNormal,
    /// rev_buy_token (a pool sell of WSOL), rev_sell_token; holdings disabled (sells never scaled).
    PoolReversed,
}

impl SimVenue {
    pub fn venue(self) -> Venue {
        if self == SimVenue::Curve { Venue::Curve } else { Venue::Pool }
    }

    fn holdings(self) -> Holdings {
        match self {
            SimVenue::Curve => Holdings::new(true, true),
            SimVenue::PoolNormal => Holdings::new(false, true),
            SimVenue::PoolReversed => Holdings::new(false, false),
        }
    }

    fn pool_buy(self, p: &Pool, size: i128) -> Option<Fill<Pool>> {
        match self {
            SimVenue::PoolNormal => v::pool_buy_exact_in(p, size),
            SimVenue::PoolReversed => v::rev_buy_token(p, size),
            SimVenue::Curve => unreachable!("a pool buy on the curve"),
        }
    }
}

/// The venue state the position trades against.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Curve(Curve),
    Pool(Pool),
}

impl State {
    /// `getattr(state, "complete", False)`: only a curve completes.
    pub fn complete(&self) -> bool {
        matches!(self, State::Curve(c) if c.complete())
    }
}

/// The engine's settings every position shares.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Cfg {
    /// The entry's and the exit's slippage tolerance (engine `tol`, 0.05).
    pub tol: f64,
    pub tau: Tau,
    /// PositionSim's max_slots (`MAX_HOLD`).
    pub max_hold: u64,
    /// replay.py WINDOW_END (the tournament's); paper: None.
    pub window_end: Option<u64>,
    /// The Mayhem agent's id (replay.py MAYHEM_AGENT): its curve legs are refused.
    pub mayhem_agent: Id,
}

/// What a feeding call did: still open; closed (by this call or before); or closed because the curve completed at
/// `slot`, where Python calls on_complete(slot) (the caller's graduation sale; `_finish` has already run).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Step {
    Open,
    Done,
    Completed { slot: u64 },
}

impl Step {
    pub fn closed(self) -> bool {
        self != Step::Open
    }
}

/// The position's internal clock and pending exit (for traces and tests).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Probe {
    pub pending: Option<u64>,
    pub pending_min: i128,
    pub last_slot: u64,
    pub decided_for: Option<u64>,
    pub deadline_done: bool,
    pub done: bool,
}

/// replay.py `PositionSim`: one open position, advanced slot by slot. `on_slot` re-executes the token's historical legs
/// of one complete slot (from `start`, our entry); `advance(u)` says the token had no legs in (last_slot, u], so time
/// decisions and pending exits in quiet gaps happen when the clock passes them; `end` closes the position when the
/// data ends. Every M1 rule has a deadline, kept as an Option as Python's `is not None` guards.
#[derive(Clone, Debug)]
pub struct PositionSim {
    r: PosResult,
    venue: SimVenue,
    state: State,
    hold: Holdings,
    rule: ExitRule,
    d: u64,
    q: f64,
    cfg: Cfg,
    diag: Diag,
    entry_slot: u64,
    deadline: Option<u64>,
    pending: Option<u64>,
    pending_min: i128,
    last_slot: u64,
    /// The last_slot already decided at a slot change.
    decided_for: Option<u64>,
    deadline_done: bool,
    done: bool,
    /// The legs of the transaction being re-run and their cf deltas (kept to reuse its buffer).
    applied: Vec<(usize, i128)>,
}

impl PositionSim {
    #[allow(clippy::too_many_arguments)]
    pub fn new(r: PosResult, venue: SimVenue, state: State, hold: Holdings, rule: ExitRule, d: u64, q: f64, cfg: Cfg) -> Self {
        let entry_slot = r.entry_slot;
        let deadline = rule.deadline(entry_slot);
        PositionSim {
            r,
            venue,
            state,
            hold,
            rule,
            d,
            q,
            cfg,
            diag: Diag::default(),
            entry_slot,
            deadline,
            pending: None,
            pending_min: 0,
            last_slot: entry_slot,
            decided_for: None,
            deadline_done: false,
            done: false,
            applied: Vec::new(),
        }
    }

    pub fn result(&self) -> &PosResult {
        &self.r
    }

    pub fn into_result(self) -> PosResult {
        self.r
    }

    /// The Result, for the caller's on_complete after a `Step::Completed` (engine.py:977-986: the graduation's reason,
    /// slots, proceeds and attempt are written into the closed position's Result).
    pub fn result_mut(&mut self) -> &mut PosResult {
        &mut self.r
    }

    pub fn is_done(&self) -> bool {
        self.done
    }

    /// The memory log (C4-2): the position as it is boxed, its holdings and its applied sales.
    pub fn heap_bytes(&self) -> usize {
        std::mem::size_of::<Self>() + self.hold.heap_bytes() + crate::mem::vec(&self.applied)
    }

    pub fn venue(&self) -> SimVenue {
        self.venue
    }

    pub fn state(&self) -> State {
        self.state
    }

    pub fn holdings(&self) -> &Holdings {
        &self.hold
    }

    pub fn rule(&self) -> &ExitRule {
        &self.rule
    }

    /// The rule, to feed a flow rule's index with each slot's legs before `on_slot` (engine.py:379-380).
    pub fn rule_mut(&mut self) -> &mut ExitRule {
        &mut self.rule
    }

    pub fn probe(&self) -> Probe {
        Probe {
            pending: self.pending,
            pending_min: self.pending_min,
            last_slot: self.last_slot,
            decided_for: self.decided_for,
            deadline_done: self.deadline_done,
            done: self.done,
        }
    }

    // -- decisions and exits

    /// `self.sell(self.state, r.tokens)`: (tokens sold, quote received).
    fn sale(&self) -> Option<(i128, i128)> {
        let tokens = self.r.tokens;
        match (self.venue, &self.state) {
            (SimVenue::Curve, State::Curve(c)) => v::curve_sell(c, tokens).map(|f| (f.tokens, f.trader_quote)),
            (SimVenue::PoolNormal, State::Pool(p)) => v::pool_sell_capped(p, tokens).map(|f| (f.tokens, f.trader_quote)),
            (SimVenue::PoolReversed, State::Pool(p)) => v::rev_sell_token(p, tokens).map(|f| (f.tokens, f.trader_quote)),
            (venue, state) => unreachable!("a {venue:?} position on {state:?}"),
        }
    }

    /// `_decide`: the mark (0 when the sale fails) and the rule; a reason sets the exit d slots later, at no less
    /// than the mark minus the tolerance.
    fn decide(&mut self, slot: u64) {
        let mark = self.sale().map_or(0, |(_, q)| q);
        self.r.mark(slot, mark);
        let ctx = Ctx { slot, entry_slot: self.entry_slot, mark, cost: self.r.cost };
        if let Some(reason) = self.rule.decide(&ctx) {
            self.r.exit_decision_slot = Some(slot);
            self.r.exit_reason = Some(Reason::Rule(reason));
            self.pending = Some(slot + self.d);
            self.pending_min = mul_trunc(mark, 1.0 - self.cfg.tol);
        }
    }

    /// `_try_exit`: the attempt counts either way; a failure clears pending (the decision slot, reason and minimum
    /// stay).
    fn try_exit(&mut self, fill_slot: u64) -> bool {
        let f = self.sale();
        self.r.exit_attempts += 1;
        match f {
            Some((tokens, quote)) if quote >= self.pending_min => {
                self.r.exit_slot = Some(fill_slot);
                self.r.proceeds = quote;
                self.r.vault_capped = tokens < self.r.tokens;
                self.finish()
            }
            _ => {
                self.pending = None;
                false
            }
        }
    }

    fn finish(&mut self) -> bool {
        self.r.sells_dropped = self.diag.sells_dropped;
        self.r.sells_scaled = self.diag.sells_scaled;
        self.done = true;
        true
    }

    /// `_complete`: Python calls on_complete(slot) and then `_finish`; the caller's on_complete runs on the Completed
    /// step (it does not touch what `_finish` writes).
    fn complete(&mut self, slot: u64) -> Step {
        self.finish();
        Step::Completed { slot }
    }

    /// `_slot_change`: the token's next event is at `slot` (> last_slot).
    fn slot_change(&mut self, slot: u64) -> bool {
        if self.pending.is_none() && !self.state.complete() && self.decided_for != Some(self.last_slot) {
            self.decide(self.last_slot);
            self.decided_for = Some(self.last_slot);
        }
        // a time deadline inside the quiet gap before this slot
        if self.pending.is_none() && !self.deadline_done && self.deadline.is_some_and(|dl| self.last_slot < dl && dl < slot) {
            self.decide(self.deadline.expect("checked"));
            self.deadline_done = true;
        }
        match self.pending {
            Some(p) if p < slot => self.try_exit(p),
            _ => false,
        }
    }

    fn step(&self) -> Step {
        if self.done { Step::Done } else { Step::Open }
    }

    // -- feeding

    /// `advance(upto)`: no events of the token in (last_slot, upto]. last_slot does not move.
    pub fn advance(&mut self, upto: u64) -> Step {
        if !self.done && upto > self.last_slot {
            self.slot_change(upto + 1);
        }
        self.step()
    }

    /// `on_slot(slot, legs, start)`: re-execute the token's legs of one complete slot from `start`.
    pub fn on_slot(&mut self, slot: u64, legs: &[Leg], start: usize) -> Step {
        if self.done {
            return Step::Done;
        }
        if slot != self.last_slot {
            if self.slot_change(slot) {
                return Step::Done;
            }
            self.last_slot = slot;
        }
        let n = legs.len();
        let mut i = start;
        let mut ins = None;
        while i < n {
            let leg = &legs[i];
            if let Some(p) = self.pending.filter(|&p| p == slot) {
                let at = *ins.get_or_insert_with(|| insertion_index(legs, p, self.q));
                if i >= at && self.try_exit(p) {
                    return Step::Done;
                }
            }
            if slot.saturating_sub(self.entry_slot) > self.cfg.max_hold {
                return self.end();
            }
            // Re-execute the whole historical transaction atomically. Holdings move per leg, so a later leg of the
            // same transaction sees the earlier ones; if any leg fails, the applied legs are rolled back.
            let tx = leg.order().tx;
            let (mut j, mut tx_state, mut failed) = (i, self.state, None);
            let mut applied = std::mem::take(&mut self.applied);
            applied.clear();
            let diag_before = self.diag;
            while j < n && legs[j].order().tx == tx {
                match rerun(&tx_state, &legs[j], self.cfg, &self.hold, &mut self.diag) {
                    None => {
                        failed = Some(j);
                        break;
                    }
                    Some((s, cf)) => {
                        tx_state = s;
                        apply_hold(&mut self.hold, &legs[j], cf, 1);
                        applied.push((j, cf));
                        j += 1;
                    }
                }
            }
            while j < n && legs[j].order().tx == tx {
                j += 1;
            }
            match failed {
                None => self.state = tx_state,
                Some(f) => {
                    match &legs[f] {
                        Leg::Curve(l) if l.trader == self.cfg.mayhem_agent => self.r.mayhem_reverted += 1,
                        Leg::Curve(CurveLeg { direct: true, .. }) => self.r.reverted_direct += 1,
                        Leg::Pool(l) if l.flag(DIRECT) => self.r.reverted_direct += 1,
                        _ => self.r.reverted_router += 1,
                    }
                    for &(k, cf) in &applied {
                        apply_hold(&mut self.hold, &legs[k], cf, -1);
                    }
                    self.diag = diag_before;
                    for l in &legs[i..j] {
                        apply_hold(&mut self.hold, l, 0, 1);
                    }
                }
            }
            self.applied = applied;
            i = j;
            if self.state.complete() {
                return self.complete(leg.slot());
            }
        }
        Step::Open
    }

    /// `end()`: the data ended (or the hold limit passed) while holding. A deadline or fill at or after the window end
    /// is not decided: the position closes on the window's last slot.
    pub fn end(&mut self) -> Step {
        if self.done {
            return Step::Done;
        }
        if self.state.complete() {
            return self.complete(self.last_slot);
        }
        if self.pending.is_none() && self.decided_for != Some(self.last_slot) {
            self.decide(self.last_slot);
        }
        let w = self.cfg.window_end;
        let beyond = matches!((w, self.deadline), (Some(w), Some(dl)) if dl + self.d >= w);
        if self.pending.is_none() && !self.deadline_done && !beyond && self.deadline.is_some_and(|dl| dl > self.last_slot) {
            self.decide(self.deadline.expect("checked")); // deadline_done stays (replay.py:526-528)
        }
        if self.pending.is_none() && beyond {
            let last = w.expect("beyond has a window end") - 1;
            self.r.exit_decision_slot = Some(last);
            self.r.exit_reason = Some(Reason::WindowEnd);
            self.pending = Some(last);
            self.pending_min = 0;
        }
        if self.pending.is_none()
            && self.deadline_done
            && self.deadline.is_some_and(|dl| dl > self.last_slot && self.r.exit_decision_slot == Some(dl))
        {
            // advance() took the deadline after the token's last event and its exit failed: no attempt, no end-of-data
            // decision on top (replay.py:532-539)
            self.r.exit_slot = None;
            self.r.proceeds = self.sale().map_or(0, |(_, q)| q);
            self.finish();
            return Step::Done;
        }
        if self.pending.is_none() {
            self.r.exit_decision_slot = Some(self.last_slot);
            self.r.exit_reason = Some(self.r.exit_reason.unwrap_or(Reason::EndOfData));
            self.pending = Some(self.last_slot + self.d);
            self.pending_min = 0;
        }
        let pending = self.pending.expect("pending is set");
        if let Some(w) = w.filter(|&w| pending >= w) {
            self.pending = Some(w - 1);
            self.pending_min = 0;
            let eds = self.r.exit_decision_slot.expect("a pending exit has its decision slot");
            self.r.exit_decision_slot = Some(eds.min(w - 1));
            self.r.exit_reason = Some(Reason::WindowEnd);
        }
        let pending = self.pending.expect("pending is set");
        if !self.try_exit(pending) {
            self.r.exit_slot = self.pending; // None: the failed try cleared it
            self.r.proceeds = self.sale().map_or(0, |(_, q)| q);
            self.finish();
        }
        Step::Done
    }
}

/// The re-run of one leg on the position's venue.
fn rerun(state: &State, leg: &Leg, cfg: Cfg, hold: &Holdings, diag: &mut Diag) -> Option<(State, i128)> {
    match (state, leg) {
        (State::Curve(c), Leg::Curve(l)) => rerun_curve(c, l, cfg.tau, hold, diag, cfg.mayhem_agent).map(|(s, d)| (State::Curve(s), d)),
        (State::Pool(p), Leg::Pool(l)) => rerun_pool(p, l, cfg.tau, hold, diag).map(|(s, d)| (State::Pool(s), d)),
        (state, leg) => panic!("a {} leg at {:?} fed to a position on {state:?}", leg.venue().as_str(), leg.order()),
    }
}

/// replay.py `feed_batch`: feed a token's legs from index i (our insertion point) slot by slot, then end the data.
/// Returns the completion slot when the curve completed (on_complete's call). The batch replay's path; the paper
/// engine feeds the slots itself.
pub fn feed_batch(sim: &mut PositionSim, legs: &[Leg], mut i: usize) -> Option<u64> {
    let n = legs.len();
    let mut completed = None;
    while i < n && !sim.is_done() {
        let slot = legs[i].slot();
        let a = insertion_index(legs, slot, 0.0); // the slot's first leg
        let mut j = i;
        while j < n && legs[j].slot() == slot {
            j += 1;
        }
        if let Step::Completed { slot } = sim.on_slot(slot, &legs[a..j], i - a) {
            completed = Some(slot);
        }
        i = j;
    }
    if !sim.is_done() {
        if let Step::Completed { slot } = sim.end() {
            completed = Some(slot);
        }
    }
    completed
}

// ------------------------------------------------------------------------------------------ the entries

/// What an entry gave.
#[derive(Clone, Debug)]
pub enum Opened {
    /// No position: skipped (no transaction) or a failed entry; `skipped`, `entry_failed` and `exit_reason` say which.
    No(PosResult),
    /// Our own buy completed the curve (cf_graduation is set, no PositionSim): Python calls on_complete(entry_slot).
    Completed(PosResult),
    /// The open position and our insertion index in the legs given. The entry slot's legs from there are re-run by
    /// the caller: `on_slot(entry_slot, legs[a..], i - a)` with `a` the slot's first leg (engine.py:955-958), when
    /// `i < len(legs)`.
    Open(Box<PositionSim>, usize),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PosError {
    /// A curve entry at the slot's start with no leg of the token before it (Recent is empty): the engine never
    /// reaches this (a signal key has a leg at or before the decision); fail closed.
    NoStateBefore { entry_slot: u64 },
    /// The entry slot's legs are not the curve's legs of that slot.
    NotSlotLegs { entry_slot: u64 },
    /// The engine's state leg is not before the entry slot (its slot is the key's last event, before the entry).
    PriorNotBefore { prior_slot: u64, entry_slot: u64 },
}

impl fmt::Display for PosError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PosError::NoStateBefore { entry_slot } => {
                write!(f, "a curve entry at {entry_slot} before the slot's first leg, with no earlier leg of the mint")
            }
            PosError::NotSlotLegs { entry_slot } => write!(f, "the legs of a curve entry at {entry_slot} are not that slot's curve legs"),
            PosError::PriorNotBefore { prior_slot, entry_slot } => {
                write!(f, "a pool entry at {entry_slot} with a state leg at {prior_slot}, not before it")
            }
        }
    }
}

impl std::error::Error for PosError {}

fn pool_of(leg: &Leg) -> &PoolLeg {
    match leg {
        Leg::Pool(l) => l,
        Leg::Curve(l) => panic!("a curve leg at {:?} in a pool's legs", l.order),
    }
}

/// replay.py `open_pool_position`: our token buy of `size` lamports in the pool at decision_slot + d, the expected
/// fill from the state after the decision slot, the fill at our insertion point (`q`) and the slippage check.
/// `legs` are the pool's legs in chain order, covering the decision and the entry slot (the engine's Recent and the
/// entry slot's; the batch replay's whole stream). `prior` is the engine's state leg in front of them (engine.py:903-
/// 905: the last state and its slot, when Recent is empty); its slot must be before the entry, else `PosError`.
#[allow(clippy::too_many_arguments)]
pub fn open_pool_position(
    venue: SimVenue,
    legs: &[Leg],
    prior: Option<(u64, Pool)>,
    decision_slot: u64,
    size: i128,
    d: u64,
    q: f64,
    rule: ExitRule,
    cfg: Cfg,
) -> Result<Opened, PosError> {
    assert!(venue != SimVenue::Curve, "open_pool_position on the curve");
    let entry = decision_slot + d;
    let r = PosResult::new(decision_slot, entry, size, Venue::Pool);
    if cfg.window_end.is_some_and(|w| entry >= w) {
        return Ok(r.skip(Reason::AfterWindowEnd));
    }
    if let Some((s, _)) = prior.filter(|&(s, _)| s >= entry) {
        return Err(PosError::PriorNotBefore { prior_slot: s, entry_slot: entry });
    }
    // the list with the state leg in front: (slot, tx -1, pre = post = the state)
    let off = usize::from(prior.is_some());
    let n = legs.len() + off;
    let at = |i: usize| if i < off { (prior.expect("off").0, -1) } else { (legs[i - off].slot(), i64::from(legs[i - off].order().tx)) };
    let hist = |idx: usize| -> Option<Pool> {
        match (n, prior) {
            (0, _) => None,
            (_, Some((_, st))) if idx <= 1 => Some(st), // legs[0].pre or legs[0].post of the state leg
            _ if idx > 0 => Some(pool_of(&legs[idx - 1 - off]).post()),
            _ => Some(pool_of(&legs[0]).pre()),
        }
    };
    let Some(s_dec) = hist(insertion_index_by(n, at, decision_slot + 1, 0.0)) else {
        return Ok(r.skip(Reason::NoPoolState));
    };
    let expected = venue.pool_buy(&s_dec, size);
    let i = insertion_index_by(n, at, entry, q);
    let state = hist(i).expect("a nonempty list has a state");
    let fill = match (venue.pool_buy(&state, size), expected) {
        (Some(f), Some(e)) if !lt(f.tokens, e.tokens as f64 * (1.0 - cfg.tol)) => f,
        _ => return Ok(r.fail()),
    };
    let mut r = r;
    r.tokens = fill.tokens;
    r.cost = fill.trader_quote;
    Ok(Opened::Open(Box::new(PositionSim::new(r, venue, State::Pool(fill.state), venue.holdings(), rule, d, q, cfg)), i - off))
}

/// The paper engine's curve entry (replay.py open_curve_position on `feed.curve_history`, spec §3.5 D4) from what the
/// engine holds instead of the mint's whole history:
/// - `s_dec`: the state after the decision slot (Recent[key].back().post right after the signal slot's history step);
/// - `before`: the state before the entry slot (Recent[key].back().post at the entry slot's step 2);
/// - `slot_legs`: the mint's legs of the entry slot, in order (empty for a quiet entry);
/// - `seed`: the other traders' net curve tokens before the entry slot as (trader, net) parts, summed per trader (the
///   store's curve_hold before the run and the run's own sums).
///
/// The holdings are then the seed plus the entry slot's legs before our insertion point (`Holdings.seed(legs[:i])`).
#[allow(clippy::too_many_arguments)]
pub fn open_curve_entry(
    s_dec: Option<Curve>,
    before: Option<Curve>,
    slot_legs: &[Leg],
    seed: impl IntoIterator<Item = (Id, i128)>,
    decision_slot: u64,
    size: i128,
    d: u64,
    q: f64,
    rule: ExitRule,
    cfg: Cfg,
) -> Result<Opened, PosError> {
    let entry = decision_slot + d;
    let r = PosResult::new(decision_slot, entry, size, Venue::Curve);
    if cfg.window_end.is_some_and(|w| entry >= w) {
        return Ok(r.skip(Reason::AfterWindowEnd));
    }
    let s_dec = match s_dec {
        Some(s) if !s.complete() => s,
        _ => return Ok(r.skip(Reason::NoCurveState)),
    };
    let Some(expected) = v::curve_buy_exact_in(&s_dec, size) else {
        return Ok(r.skip(Reason::NoFill));
    };
    if !slot_legs.iter().all(|l| matches!(l, Leg::Curve(_)) && l.slot() == entry) {
        return Err(PosError::NotSlotLegs { entry_slot: entry });
    }
    let k = insertion_index(slot_legs, entry, q);
    let state = match k {
        0 => before.ok_or(PosError::NoStateBefore { entry_slot: entry })?,
        _ => match &slot_legs[k - 1] {
            Leg::Curve(l) => l.post(),
            Leg::Pool(_) => unreachable!("checked"),
        },
    };
    if state.complete() {
        return Ok(r.skip(Reason::CurveComplete));
    }
    let fill = match v::curve_buy_exact_in(&state, size) {
        Some(f) if !lt(f.tokens, expected.tokens as f64 * (1.0 - cfg.tol)) => f,
        _ => return Ok(r.fail()),
    };
    let mut r = r;
    r.tokens = fill.tokens;
    r.cost = fill.trader_quote;
    let mut hold = SimVenue::Curve.holdings();
    for (trader, net) in seed {
        hold.seed(trader, net);
    }
    for l in &slot_legs[..k] {
        if let Leg::Curve(c) = l {
            let t = i128::from(c.t);
            hold.seed(c.trader, if c.is_buy { t } else { -t });
        }
    }
    if fill.completes {
        r.cf_graduation = true;
        return Ok(Opened::Completed(r));
    }
    Ok(Opened::Open(Box::new(PositionSim::new(r, SimVenue::Curve, State::Curve(fill.state), hold, rule, d, q, cfg)), k))
}

#[cfg(test)]
mod cases;

#[cfg(test)]
mod tests;
