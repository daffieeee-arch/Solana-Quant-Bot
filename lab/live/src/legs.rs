//! The engine's event stream from OF1 chunks, store-exact (M1 C1a; the porting spec is
//! research/m1-c1a-legs-spec-2026-10-19.md): what paper.feed.ReplayFeed hands the engine, built from the chunk rows
//! and the store snapshot instead of the store's tables. This module is the logic without Arrow (legs_read.rs reads
//! the Parquet into it):
//!
//! - `Keys`: one interner for every pubkey (snapshot mints and pools first, then traders).
//! - `PoolRow`: the store's `pool` row, reduced to what the leg and the farmer flag read (store.py `_pool_trades`,
//!   `_pool_other`, the SOL-market join).
//! - `h24_of`: a chunk's day in slots from its blocks (store.py:486-488).
//! - `FarmHist`: the store's farmer flag per (pool, trader): the trades of [s - h24, s - 1] (h24 of the trade's
//!   chunk), a farmer when there are 2 or more and 20 |net| < gross, exactly (store.py:500-519).
//! - `pool_leg`: a PoolRow and the pool's context into the engine's leg (streams.py `pool_leg`).
//! - `Line` and `Summary`: the dump paper/legs_dump.py writes too, line for line.
//!
//! The sort unit is the chunk: all its rows, clipped to the range, fully sorted before any is emitted, and the chunks
//! in slot order. key_ties (two rows with one (slot, tx, outer, inner)) and duplicates are an error.

use anyhow::{bail, Context, Result};
use lab_core::legs::{CurveLeg, Id, Order, PoolCtx, PoolKind, PoolLeg, DIRECT, EXACT_OUT, FARMER, NONE, X0, X1};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};

pub const DAY_MS: f64 = 86_400_000.0;
/// The store's slot time when a chunk has no usable blocks (store.py `ms or 267.3`).
pub const REF_MS: f64 = 267.3;

/// One interner for every pubkey: ids are dense, the snapshot's keys first.
#[derive(Default)]
pub struct Keys {
    ids: HashMap<Box<str>, Id>,
    names: Vec<Box<str>>,
}

impl Keys {
    pub fn id(&mut self, key: &str) -> Id {
        if let Some(&id) = self.ids.get(key) {
            return id;
        }
        let id = Id::try_from(self.names.len()).expect("fewer than 2^32 keys");
        assert!(id != NONE, "fewer than 2^32 - 1 keys");
        self.names.push(key.into());
        self.ids.insert(key.into(), id);
        id
    }

    pub fn get(&self, key: &str) -> Option<Id> {
        self.ids.get(key).copied()
    }

    /// The key of an id; None for NONE.
    pub fn name(&self, id: Id) -> Option<&str> {
        (id != NONE).then(|| &*self.names[id as usize])
    }

    pub fn len(&self) -> usize {
        self.names.len()
    }
}

/// The store's `pool` row of one PumpSwap event (a SOL market's), as the leg and the farmer read it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PoolRow {
    pub order: Order,
    pub pool: Id,
    pub trader: Id,
    pub b: i64,
    pub vault: i64,
    /// COALESCE(virtual quote, 0); unused for withdraw and deposit (the store's NULL)
    pub vq: i64,
    pub base: i64,
    /// unused for withdraw and deposit (NULL)
    pub e_delta: i64,
    pub quote_user: i64,
    /// trade: limit_quote, limit_base; boost: boost_left; withdraw/deposit: lp_amount, lp_supply (X0/X1 set)
    pub x: [u64; 2],
    /// lp, protocol, creator, cashback (trades only; `or 0`)
    pub bps: [u32; 4],
    pub kind: PoolKind,
    /// EXACT_OUT, DIRECT, FARMER, X0, X1; REVERSED
    pub flags: u8,
}

pub const REVERSED: u8 = 32;

impl PoolRow {
    pub fn reversed(&self) -> bool {
        self.flags & REVERSED != 0
    }

    /// The store's token_buy and token_amount (store.py:494-497), trades only: (buy, amount).
    pub fn token_side(&self) -> Option<(bool, i64)> {
        if !self.kind.is_trade() {
            return None;
        }
        let buy = (self.kind == PoolKind::Buy) == !self.reversed();
        Some((buy, if self.reversed() { self.quote_user } else { self.base }))
    }

    /// e = vault + vq (store.py:491), BIGINT: an overflow is an error.
    pub fn e(&self) -> Result<i64> {
        self.vault.checked_add(self.vq).with_context(|| format!("vault + vq overflows at {:?}", self.order))
    }
}

/// A chunk's slot time and day (store.py:486-488): `ms = (max bt - min bt) * 1000.0 / greatest(max slot - min slot, 1)`
/// over its non-skipped blocks (a slot without a block time counts for the slots), then `ms or 267.3`, then
/// `round(DAY_MS / ms)` half to even.
pub fn h24_of(blocks: &[(u64, Option<i64>)]) -> u64 {
    let (mut smin, mut smax, mut tmin, mut tmax) = (u64::MAX, 0u64, i64::MAX, i64::MIN);
    for &(s, t) in blocks {
        smin = smin.min(s);
        smax = smax.max(s);
        if let Some(t) = t {
            tmin = tmin.min(t);
            tmax = tmax.max(t);
        }
    }
    let ms = if blocks.is_empty() || tmin > tmax {
        0.0
    } else {
        ((tmax - tmin) as f64 * 1000.0) / ((smax - smin).max(1) as f64)
    };
    let ms = if ms == 0.0 { REF_MS } else { ms };
    (DAY_MS / ms).round_ties_even() as u64
}

/// The store's farmer history of one (pool, trader): its trades in chain order as (slot, signed token amount).
#[derive(Default)]
pub struct FarmHist {
    a: Vec<(u64, i64)>,
    start: usize,
    end: usize,
    n: u64,
    net: i128,
    gross: i128,
    /// the chunk the window was anchored in, + 1 (0: none)
    anchor: u32,
}

impl FarmHist {
    /// The flag of a trade at slot `s` in chunk `chunk` [chunk_lo, ...) with that chunk's `h24`: its trades in
    /// [s - h24, s - 1]. A new chunk re-anchors the window (h24 changes, so its start can move back) after dropping
    /// what no window of the chunk reaches (before chunk_lo - h24); inside a chunk the window slides forward.
    pub fn flag(&mut self, s: u64, h24: u64, chunk: u32, chunk_lo: u64) -> bool {
        let lo = s.saturating_sub(h24);
        if self.anchor != chunk + 1 {
            let keep = chunk_lo.saturating_sub(h24);
            let cut = self.a.partition_point(|x| x.0 < keep);
            self.a.drain(..cut);
            self.start = self.a.partition_point(|x| x.0 < lo);
            self.end = self.a.partition_point(|x| x.0 < s);
            (self.n, self.net, self.gross) = (0, 0, 0);
            for &(_, v) in &self.a[self.start..self.end] {
                self.n += 1;
                self.net += i128::from(v);
                self.gross += i128::from(v).abs();
            }
            self.anchor = chunk + 1;
        } else {
            while self.end < self.a.len() && self.a[self.end].0 < s {
                let v = i128::from(self.a[self.end].1);
                (self.n, self.net, self.gross) = (self.n + 1, self.net + v, self.gross + v.abs());
                self.end += 1;
            }
            while self.start < self.end && self.a[self.start].0 < lo {
                let v = i128::from(self.a[self.start].1);
                (self.n, self.net, self.gross) = (self.n - 1, self.net - v, self.gross - v.abs());
                self.start += 1;
            }
        }
        self.n >= 2 && 20 * self.net.abs() < self.gross
    }

    /// After `flag`: the trade joins the history (same-slot trades never see each other).
    pub fn add(&mut self, s: u64, signed: i64) {
        self.a.push((s, signed));
    }
}

/// The farmer flags of one chunk's pool trades, in chain order (rows sorted): set on the rows, history kept in `hist`.
pub fn flag_chunk(rows: &mut [PoolRow], hist: &mut HashMap<(Id, Id), FarmHist>, h24: u64, chunk: u32, chunk_lo: u64) {
    for r in rows.iter_mut() {
        let Some((buy, amount)) = r.token_side() else { continue };
        let h = hist.entry((r.pool, r.trader)).or_default();
        if h.flag(r.order.slot, h24, chunk, chunk_lo) {
            r.flags |= FARMER;
        } else {
            r.flags &= !FARMER;
        }
        h.add(r.order.slot, if buy { amount } else { -amount });
    }
}

/// A PoolRow and its pool's context into the engine's leg (streams.py `pool_leg`): withdraw and deposit take the
/// context's virtual quote and fees and leave it; a trade sets both; a boost sets the virtual quote and keeps the fees
/// (its own pre-state has none).
pub fn pool_leg(r: &PoolRow, ctx: &mut PoolCtx) -> Result<PoolLeg> {
    let mut leg = PoolLeg {
        order: r.order, pool: r.pool, trader: r.trader, b: r.b, e: 0, virt: 0, fees: [0; 4], base: r.base,
        quote_net: r.quote_user, e_delta: 0, x: r.x, kind: r.kind, flags: r.flags & (DIRECT | EXACT_OUT | FARMER | X0 | X1),
    };
    match r.kind {
        PoolKind::Withdraw | PoolKind::Deposit => {
            leg.virt = ctx.virt;
            leg.fees = ctx.fees;
            leg.e = r.vault.checked_add(ctx.virt).with_context(|| format!("vault + virt overflows at {:?}", r.order))?;
            leg.e_delta = if r.kind == PoolKind::Withdraw { -r.quote_user } else { r.quote_user };
        }
        PoolKind::Buy | PoolKind::Sell | PoolKind::Boost => {
            leg.e = r.e()?;
            leg.e_delta = r.e_delta;
            leg.virt = r.vq;
            ctx.virt = r.vq;
            if r.kind == PoolKind::Boost {
                leg.quote_net = r.e_delta;
                leg.flags |= DIRECT; // the dataclass default: a boost is always direct
            } else {
                ctx.fees = r.bps;
                leg.fees = r.bps;
            }
        }
    }
    Ok(leg)
}

fn opt<T: ToString>(v: Option<T>) -> String {
    v.map_or_else(|| "null".to_owned(), |x| x.to_string())
}

fn b(v: bool) -> &'static str {
    if v { "true" } else { "false" }
}

/// One event's dump line (paper/legs_dump.py `line`, the same fields and rendering): the common fields, then the
/// venue's.
fn common(venue: &str, key: Option<&str>, o: Order, block_time: Option<i64>) -> Vec<String> {
    vec![venue.to_owned(), key.unwrap_or("null").to_owned(), o.slot.to_string(), o.tx.to_string(), o.outer.to_string(),
         o.inner.to_string(), opt(block_time), o.tau_u().to_string()]
}

pub fn curve_line(keys: &Keys, l: &CurveLeg, block_time: Option<i64>) -> String {
    let (pre, post) = (l.pre(), l.post());
    let mut f = common("curve", keys.name(l.mint), l.order, block_time);
    f.extend([l.variant.as_str().to_owned(), b(l.is_buy).to_owned(), l.t.to_string(), l.q.to_string(), l.fee.to_string()]);
    f.extend([pre.vt, pre.vq, pre.rt, pre.rq, post.vt, post.vq, post.rt, post.rq, post.fee_bps, post.creator_bps].map(|x| x.to_string()));
    f.extend([keys.name(l.trader).unwrap_or("null").to_owned(), opt(l.budget()), opt(l.max_cost()), opt(l.min_tokens()),
              opt(l.min_out()), b(l.direct).to_owned()]);
    f.join("\t")
}

pub fn pool_line(keys: &Keys, l: &PoolLeg, block_time: Option<i64>) -> String {
    let (pre, post) = (l.pre(), l.post());
    let mut f = common("pool", keys.name(l.pool), l.order, block_time);
    f.extend([l.kind.as_str().to_owned(), b(l.flag(EXACT_OUT)).to_owned(), l.base.to_string(), l.quote_net.to_string()]);
    for p in [pre, post] {
        f.extend([p.b, p.e, p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps, p.virt].map(|x| x.to_string()));
    }
    f.extend([keys.name(l.trader).unwrap_or("null").to_owned(), opt(l.limit_quote()), opt(l.limit_base()), opt(l.boost_left()),
              b(l.flag(DIRECT)).to_owned(), opt(l.lp_amount()), opt(l.lp_supply()), b(l.flag(FARMER)).to_owned()]);
    f.join("\t")
}

/// The dump's summary (paper/legs_dump.py `dump`): the count and sha256 of all lines, the count per venue and pool
/// kind, and per slot window [from + k*window, ...) its count and sha256.
pub struct Summary {
    from: u64,
    to: u64,
    window: u64,
    events: u64,
    total: Sha256,
    kinds: BTreeMap<String, u64>,
    windows: Vec<(u64, u64, u64, String)>,
    cur: Option<(u64, u64, u64, Sha256)>,
}

impl Summary {
    pub fn new(from: u64, to: u64, window: u64) -> Self {
        Summary { from, to, window, events: 0, total: Sha256::new(), kinds: BTreeMap::new(), windows: Vec::new(), cur: None }
    }

    pub fn add(&mut self, slot: u64, kind: &str, line: &str) {
        let a = self.from + (slot - self.from) / self.window * self.window;
        if self.cur.as_ref().is_none_or(|c| c.0 != a) {
            self.close_window();
            self.cur = Some((a, (a + self.window).min(self.to), 0, Sha256::new()));
        }
        let c = self.cur.as_mut().expect("set above");
        c.2 += 1;
        c.3.update(line.as_bytes());
        c.3.update(b"\n");
        self.total.update(line.as_bytes());
        self.total.update(b"\n");
        self.events += 1;
        *self.kinds.entry(kind.to_owned()).or_default() += 1;
    }

    fn close_window(&mut self) {
        if let Some((a, b, n, h)) = self.cur.take() {
            self.windows.push((a, b, n, hex(h)));
        }
    }

    pub fn finish(mut self) -> serde_json::Value {
        self.close_window();
        serde_json::json!({
            "from": self.from, "to": self.to, "window": self.window, "events": self.events, "sha256": hex(self.total),
            "kinds": self.kinds,
            "windows": self.windows.iter().map(|(a, b, n, h)| serde_json::json!([a, b, n, h])).collect::<Vec<_>>(),
        })
    }
}

fn hex(h: Sha256) -> String {
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// The emission of one chunk: its curve legs and pool rows (each sorted) merged in chain order, curve first on a
/// tie (none: gated before), only [from, to); each pool row becomes its leg with the pool's context. `emit` gets
/// (slot, kind, line).
pub fn emit_chunk(
    keys: &Keys,
    curve: &[CurveLeg],
    pool: &[PoolRow],
    bt: impl Fn(u64) -> Option<i64>,
    ctx: &mut HashMap<Id, PoolCtx>,
    (from, to): (u64, u64),
    mut emit: impl FnMut(u64, &str, String),
) -> Result<()> {
    let (mut i, mut j) = (0, 0);
    loop {
        let take_curve = match (curve.get(i), pool.get(j)) {
            (None, None) => break,
            (Some(_), None) => true,
            (None, Some(_)) => false,
            (Some(c), Some(p)) => c.order <= p.order,
        };
        if take_curve {
            let l = &curve[i];
            i += 1;
            if (from..to).contains(&l.order.slot) {
                emit(l.order.slot, "curve", curve_line(keys, l, bt(l.order.slot)));
            }
        } else {
            let r = &pool[j];
            j += 1;
            if (from..to).contains(&r.order.slot) {
                let leg = pool_leg(r, ctx.entry(r.pool).or_default())?;
                emit(r.order.slot, &format!("pool/{}", r.kind.as_str()), pool_line(keys, &leg, bt(r.order.slot)));
            }
        }
    }
    Ok(())
}

/// The gate: no two rows of the chunk (curve and pool) share an order, which also excludes duplicates. Both sorted.
pub fn check_ties(curve: &[CurveLeg], pool: &[PoolRow]) -> Result<()> {
    if let Some(w) = curve.windows(2).find(|w| w[0].order == w[1].order) {
        bail!("two curve rows at {:?} (key ties must be 0)", w[0].order);
    }
    if let Some(w) = pool.windows(2).find(|w| w[0].order == w[1].order) {
        bail!("two pool rows at {:?} (key ties must be 0)", w[0].order);
    }
    let (mut i, mut j) = (0, 0);
    while i < curve.len() && j < pool.len() {
        match curve[i].order.cmp(&pool[j].order) {
            std::cmp::Ordering::Less => i += 1,
            std::cmp::Ordering::Greater => j += 1,
            std::cmp::Ordering::Equal => bail!("a curve and a pool row at {:?} (key ties must be 0)", curve[i].order),
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use lab_core::legs::CurveVariant;

    fn o(slot: u64, tx: u32, inner: u16) -> Order {
        Order { slot, tx, outer: 0, inner }
    }

    fn trade(slot: u64, tx: u32, pool: Id, trader: Id, kind: PoolKind, amount: i64) -> PoolRow {
        PoolRow {
            order: o(slot, tx, 1), pool, trader, b: 1_000, vault: 500, vq: 0, base: amount, e_delta: 3, quote_user: amount,
            x: [0; 2], bps: [20, 5, 5, 0], kind, flags: REVERSED,
        }
    }

    /// The store's rule by brute force: the (pool, trader)'s trades in [s - h24, s - 1].
    fn brute(rows: &[PoolRow], i: usize, h24: u64) -> bool {
        let r = rows[i];
        let s = r.order.slot;
        let (mut n, mut net, mut gross) = (0, 0i128, 0i128);
        for x in rows {
            if (x.pool, x.trader) == (r.pool, r.trader) && x.order.slot + h24 >= s && x.order.slot < s {
                let (buy, a) = x.token_side().unwrap();
                n += 1;
                net += if buy { i128::from(a) } else { -i128::from(a) };
                gross += i128::from(a);
            }
        }
        n >= 2 && 20 * net.abs() < gross
    }

    #[test]
    fn h24_follows_the_store() {
        // 1,000 slots in 400 s: 400 ms a slot, 216,000 slots a day; one block or none: 267.3 ms.
        assert_eq!(h24_of(&[(100, Some(1_000)), (600, None), (1_100, Some(1_400))]), 216_000);
        assert_eq!(h24_of(&[(5, Some(7))]), (DAY_MS / REF_MS).round_ties_even() as u64);
        assert_eq!(h24_of(&[]), 323_232);
        assert_eq!(h24_of(&[(1, None), (9, None)]), 323_232);
    }

    #[test]
    fn the_farmer_flag_equals_the_store_rule_also_when_h24_changes_between_chunks() {
        // A wash trader W (buy then sell the same amount) and an organic buyer O in one pool, over two chunks with
        // different h24 (the window start moves back at the boundary), against the brute-force rule.
        let mut rows = Vec::new();
        let mut seed = 7u64;
        let mut next = || {
            seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1);
            seed >> 33
        };
        for k in 0..400u64 {
            let slot = 100 + k * 3 + next() % 3;
            let (trader, kind) = match next() % 4 {
                0 | 1 => (1, if k % 2 == 0 { PoolKind::Buy } else { PoolKind::Sell }),
                2 => (2, PoolKind::Buy),
                _ => (NONE, PoolKind::Sell),
            };
            rows.push(trade(slot, k as u32, 9, trader, kind, 50 + (next() % 5) as i64));
        }
        rows.sort_by_key(|r| r.order);
        let mut hist = HashMap::new();
        let split = rows.partition_point(|r| r.order.slot < 700);
        let (a, b) = rows.split_at_mut(split);
        flag_chunk(a, &mut hist, 120, 0, 100);
        flag_chunk(b, &mut hist, 300, 1, 700);
        let flagged = rows.iter().filter(|r| r.flags & FARMER != 0).count();
        assert!(flagged > 10, "{flagged}");
        for i in 0..rows.len() {
            let h24 = if rows[i].order.slot < 700 { 120 } else { 300 };
            assert_eq!(rows[i].flags & FARMER != 0, brute(&rows, i, h24), "row {i} {:?}", rows[i].order);
        }
    }

    #[test]
    fn the_farmer_flag_is_exact_at_its_edges() {
        let two = |a: (u64, i64), b: (u64, i64)| {
            let mut h = FarmHist::default();
            h.add(a.0, a.1);
            h.add(b.0, b.1);
            h
        };
        assert!(two((90, 10), (95, -10)).flag(100, 10, 0, 0)); // [90, 99] holds s - h24: n 2, net 0, gross 20
        assert!(!two((90, 10), (95, -10)).flag(100, 9, 0, 0)); // [91, 99]: n 1
        assert!(!two((90, 21), (95, -19)).flag(100, 50, 0, 0)); // 20 * |2| == 40: not below gross
        let mut h = two((90, 20), (95, -19));
        assert!(h.flag(100, 50, 0, 0)); // 20 < 39
        h.add(100, 5);
        assert!(h.flag(100, 50, 0, 0)); // a trade of the same slot is not in the window
        assert!(!h.flag(101, 50, 0, 0)); // now n 3, net 6, gross 44: 120 >= 44
    }

    #[test]
    fn a_pool_leg_takes_its_context_as_streams_pool_leg() {
        let mut ctx = PoolCtx::default();
        // a withdraw first: virt 0 and fees 0 (the context is empty at the start)
        let w = PoolRow { kind: PoolKind::Withdraw, x: [5, 6], flags: X0 | X1 | DIRECT, ..trade(1, 0, 9, 1, PoolKind::Buy, 40) };
        let l = pool_leg(&w, &mut ctx).unwrap();
        assert_eq!((l.e, l.virt, l.fees, l.post().e, l.post().b, l.lp_amount()), (500, 0, [0; 4], 460, 960, Some(5)));
        // a buy sets virt and fees; a boost sets virt, keeps the fees, has none itself and is direct
        let buy = PoolRow { vq: 70, flags: EXACT_OUT, ..trade(2, 0, 9, 1, PoolKind::Buy, 40) };
        let l = pool_leg(&buy, &mut ctx).unwrap();
        assert_eq!((l.e, l.virt, l.fees, l.flag(EXACT_OUT), l.flag(DIRECT)), (570, 70, [20, 5, 5, 0], true, false));
        let boost = PoolRow { kind: PoolKind::Boost, vq: 90, bps: [0; 4], e_delta: 8, flags: 0, ..trade(3, 0, 9, 1, PoolKind::Buy, 4) };
        let l = pool_leg(&boost, &mut ctx).unwrap();
        assert_eq!((l.e, l.virt, l.fees, l.quote_net, l.flag(DIRECT)), (590, 90, [0; 4], 8, true));
        assert_eq!(ctx, PoolCtx { virt: 90, fees: [20, 5, 5, 0] });
        // a deposit after the boost: the boost's virt and the trade's fees
        let d = PoolRow { kind: PoolKind::Deposit, ..trade(4, 0, 9, 1, PoolKind::Buy, 40) };
        let l = pool_leg(&d, &mut ctx).unwrap();
        assert_eq!((l.e, l.virt, l.fees, l.post().e), (590, 90, [20, 5, 5, 0], 630));
        assert!(pool_leg(&PoolRow { vault: i64::MAX, vq: 1, ..buy }, &mut ctx).is_err());
    }

    #[test]
    fn the_chunk_is_emitted_in_chain_order_and_the_summary_counts_by_window() {
        let mut keys = Keys::default();
        let (m, p, t) = (keys.id("M"), keys.id("P"), keys.id("T"));
        let c = |slot, tx| CurveLeg {
            order: o(slot, tx, 2), mint: m, trader: t, t: 1, q: 1, fee: 0, vt: 10, vq: 10, rt: 5, rq: 5, args: [0; 4], fee_bps: 95,
            creator_bps: 30, args_set: 0, variant: CurveVariant::Sell, is_buy: false, direct: true,
        };
        let curve = vec![c(10, 1), c(12, 0), c(30, 0)];
        let pool = vec![trade(10, 0, p, t, PoolKind::Buy, 5), trade(12, 0, p, NONE, PoolKind::Sell, 5), trade(25, 0, p, t, PoolKind::Buy, 5)];
        check_ties(&curve, &pool).unwrap();
        let mut seen = Vec::new();
        let mut sum = Summary::new(10, 30, 10);
        emit_chunk(&keys, &curve, &pool, |s| (s != 12).then_some(s as i64), &mut HashMap::new(), (10, 30), |s, k, l| {
            seen.push((s, k.to_owned(), l.split('\t').nth(3).unwrap().to_owned()));
            sum.add(s, k, &l);
        })
        .unwrap();
        // (10, tx 0) pool before (10, tx 1) curve; slot 12: curve tx 0 inner 2 after pool tx 0 inner 1; 30 is out
        assert_eq!(seen.iter().map(|x| (x.0, x.1.as_str())).collect::<Vec<_>>(),
                   [(10, "pool/buy"), (10, "curve"), (12, "pool/sell"), (12, "curve"), (25, "pool/buy")]);
        let v = sum.finish();
        assert_eq!((v["events"].as_u64(), v["windows"].as_array().unwrap().len()), (Some(5), 2));
        assert_eq!(v["kinds"]["curve"], 2);
        assert!(check_ties(&[CurveLeg { order: pool[0].order, ..c(10, 0) }], &pool).is_err()); // one order on both sides
        assert!(check_ties(&curve, &[pool[0], pool[0]]).is_err());
    }
}
