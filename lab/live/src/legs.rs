//! The engine's event stream from OF1 chunks, store-exact (M1 C1a; the porting spec is
//! research/m1-c1a-legs-spec-2026-10-19.md): what paper.feed.ReplayFeed hands the engine, built from the chunk rows
//! and the store snapshot instead of the store's tables. This module is the logic without Arrow (legs_read.rs reads
//! the Parquet into it):
//!
//! - `Keys`: one interner for every pubkey (snapshot mints and pools first, then traders), each key stored once.
//! - `PoolRow`: the store's `pool` row, reduced to what the leg and the farmer flag read (store.py `_pool_trades`,
//!   `_pool_other`, the SOL-market join).
//! - `h24_of`: a chunk's day in slots from its blocks (store.py:486-488).
//! - `FarmHist`: the store's farmer flag per (pool, trader): the trades of [s - h24, s - 1] (h24 of the trade's
//!   chunk), a farmer when there are 2 or more and 20 |net| < gross, exactly (store.py:500-519). `keeps` gives each
//!   emitted chunk the slot below which no window of it or of a later chunk reaches.
//! - `pool_leg`: a PoolRow and the pool's context into the engine's leg (streams.py `pool_leg`).
//! - `emit_chunk`: a chunk's events in chain order, typed (lab_core::legs::Event, the feed's event: M1 C1b), for
//!   `lab-live legs` and `lab-live signals` alike.
//! - `event_line` and `Summary`: the dump paper/legs_dump.py writes too, line for line, rendered from the typed event.
//!
//! The sort unit is the chunk: all its rows, clipped to the range, fully sorted before any is emitted, and the chunks
//! in slot order. key_ties (two rows with one (slot, tx, outer, inner)) and duplicates are an error.

use anyhow::{bail, Context, Result};
use hashbrown::{DefaultHashBuilder, HashTable};
use lab_core::legs::{CurveLeg, Event, Id, Leg, Order, PoolCtx, PoolKind, PoolLeg, DIRECT, EXACT_OUT, FARMER, NONE, X0, X1};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::hash::BuildHasher;

pub const DAY_MS: f64 = 86_400_000.0;
/// The store's slot time when a chunk has no usable blocks (store.py `ms or 267.3`).
pub const REF_MS: f64 = 267.3;

/// One interner for every pubkey: ids are dense, the snapshot's keys first. Each key is stored once, in one buffer
/// (`ends[i]` is the end of key i); the table holds only ids and finds a key through the buffer: ~50 B a 44-character
/// key plus the buffer's and the table's growth slack (up to ~2x), against ~160-200 B for a map of owned strings plus
/// a list of them.
#[derive(Default)]
pub struct Keys {
    table: HashTable<Id>,
    hasher: DefaultHashBuilder,
    buf: String,
    ends: Vec<u32>,
}

fn key_at<'a>(buf: &'a str, ends: &[u32], id: Id) -> &'a str {
    let i = id as usize;
    let start = if i == 0 { 0 } else { ends[i - 1] as usize };
    &buf[start..ends[i] as usize]
}

impl Keys {
    pub fn id(&mut self, key: &str) -> Id {
        let hash = self.hasher.hash_one(key);
        if let Some(&id) = self.table.find(hash, |&i| key_at(&self.buf, &self.ends, i) == key) {
            return id;
        }
        let id = Id::try_from(self.ends.len()).expect("fewer than 2^32 keys");
        assert!(id != NONE, "fewer than 2^32 - 1 keys");
        self.buf.push_str(key);
        self.ends.push(u32::try_from(self.buf.len()).expect("keys in less than 4 GiB"));
        let (buf, ends, hasher) = (&self.buf, &self.ends, &self.hasher);
        self.table.insert_unique(hash, id, |&i| hasher.hash_one(key_at(buf, ends, i)));
        id
    }

    pub fn get(&self, key: &str) -> Option<Id> {
        self.table.find(self.hasher.hash_one(key), |&i| key_at(&self.buf, &self.ends, i) == key).copied()
    }

    /// The key of an id; None for NONE.
    pub fn name(&self, id: Id) -> Option<&str> {
        (id != NONE).then(|| key_at(&self.buf, &self.ends, id))
    }

    pub fn len(&self) -> usize {
        self.ends.len()
    }

    /// The memory log (C4-2): the table (an id and a control byte per slot), the key buffer and the ends.
    pub fn heap_bytes(&self) -> usize {
        self.table.capacity() * (std::mem::size_of::<Id>() + 1) + self.buf.capacity() + self.ends.capacity() * std::mem::size_of::<u32>()
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
    /// The flag of a trade at slot `s` in chunk `chunk` with that chunk's `h24`: its trades in [s - h24, s - 1]. A new
    /// chunk re-anchors the window (h24 changes, so its start can move back) after dropping what no window of this
    /// chunk or a later one reaches: the trades before `keep` (`keeps`; at most s - h24). Inside a chunk the window
    /// slides forward.
    pub fn flag(&mut self, s: u64, h24: u64, chunk: u32, keep: u64) -> bool {
        let lo = s.saturating_sub(h24);
        debug_assert!(keep <= lo, "keep {keep} above the window start {lo}");
        if self.anchor != chunk + 1 {
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

    /// The memory log (C4-2): the trades held and their bytes. The trades before a chunk's `keep` go only at the
    /// (pool, trader)'s next trade in a later chunk, so a pair that never trades again keeps its list.
    pub fn mem(&self) -> (usize, usize) {
        (self.a.len(), lab_core::mem::vec(&self.a))
    }
}

/// For emitted chunks k (in slot order) with their first emitted slot `start_k` (the replay's from in the first, else
/// the chunk's lo) and h24: the slot below which no farmer window of chunk k or a later one reaches,
/// min over j >= k of (start_j - h24_j). It never decreases, so dropping below it is exact even when a later chunk's
/// h24 is longer by more than a chunk (its window then reaches further back than an earlier chunk's); keeps[0] is the
/// replay's H0 (before the store's start).
pub fn keeps(chunks: &[(u64, u64)]) -> Vec<u64> {
    let mut out: Vec<u64> = chunks.iter().map(|&(start, h24)| start.saturating_sub(h24)).collect();
    for k in (1..out.len()).rev() {
        out[k - 1] = out[k - 1].min(out[k]);
    }
    out
}

/// The farmer flags of one chunk's pool trades, in chain order (rows sorted): set on the rows, history kept in `hist`.
/// Returns the change in the histories' trades and their bytes (`FarmHist::mem`), so the memory log (C4-2) keeps a running
/// total instead of walking every history.
pub fn flag_chunk(rows: &mut [PoolRow], hist: &mut HashMap<(Id, Id), FarmHist>, h24: u64, chunk: u32, keep: u64) -> (isize, isize) {
    let (mut trades, mut bytes) = (0isize, 0isize);
    for r in rows.iter_mut() {
        let Some((buy, amount)) = r.token_side() else { continue };
        let h = hist.entry((r.pool, r.trader)).or_default();
        let before = h.mem();
        if h.flag(r.order.slot, h24, chunk, keep) {
            r.flags |= FARMER;
        } else {
            r.flags &= !FARMER;
        }
        h.add(r.order.slot, if buy { amount } else { -amount });
        let after = h.mem();
        trades += after.0 as isize - before.0 as isize;
        bytes += after.1 as isize - before.1 as isize;
    }
    (trades, bytes)
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

/// One event's dump line, from the typed event: its leg's venue picks the fields, its slot's block time is the event's.
pub fn event_line(keys: &Keys, ev: &Event) -> String {
    match &ev.leg {
        Leg::Curve(l) => curve_line(keys, l, ev.block_time),
        Leg::Pool(l) => pool_line(keys, l, ev.block_time),
    }
}

/// The dump's kind of an event: `curve`, or `pool/` and the pool event's kind.
pub fn event_kind(ev: &Event) -> &'static str {
    match &ev.leg {
        Leg::Curve(_) => "curve",
        Leg::Pool(l) => match l.kind {
            PoolKind::Buy => "pool/buy",
            PoolKind::Sell => "pool/sell",
            PoolKind::Boost => "pool/boost",
            PoolKind::Withdraw => "pool/withdraw",
            PoolKind::Deposit => "pool/deposit",
        },
    }
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
/// tie (none: gated before), only [from, to); each pool row becomes its leg with the pool's context. `emit` gets each
/// event as the feed hands it to the engine (paper/feed.py `Event`: the leg and its slot's block time); its error
/// ends the emission.
pub fn emit_chunk(
    curve: &[CurveLeg],
    pool: &[PoolRow],
    bt: impl Fn(u64) -> Option<i64>,
    ctx: &mut HashMap<Id, PoolCtx>,
    (from, to): (u64, u64),
    mut emit: impl FnMut(&Event) -> Result<()>,
) -> Result<()> {
    let (mut i, mut j) = (0, 0);
    loop {
        let take_curve = match (curve.get(i), pool.get(j)) {
            (None, None) => break,
            (Some(_), None) => true,
            (None, Some(_)) => false,
            // curve first on a tie, as heapq.merge takes its first stream first; unreachable after check_ties (an
            // equal order on both sides is refused there), kept so the merge alone is ReplayFeed's
            (Some(c), Some(p)) => c.order <= p.order,
        };
        if take_curve {
            let l = &curve[i];
            i += 1;
            if (from..to).contains(&l.order.slot) {
                emit(&Event::new(Leg::Curve(*l), bt(l.order.slot)))?;
            }
        } else {
            let r = &pool[j];
            j += 1;
            if (from..to).contains(&r.order.slot) {
                let leg = pool_leg(r, ctx.entry(r.pool).or_default())?;
                emit(&Event::new(Leg::Pool(leg), bt(r.order.slot)))?;
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
    fn keys_are_interned_once_with_dense_ids() {
        let mut k = Keys::default();
        let names: Vec<String> = (0..5_000).map(|i| format!("Key{i:040}")).collect();
        for (i, n) in names.iter().enumerate() {
            assert_eq!(k.id(n), i as Id);
        }
        for (i, n) in names.iter().enumerate().rev() {
            assert_eq!((k.id(n), k.get(n), k.name(i as Id)), (i as Id, Some(i as Id), Some(n.as_str())));
        }
        assert_eq!((k.len(), k.get("absent"), k.name(NONE)), (5_000, None, None));
        assert_eq!(k.id(""), 5_000); // an empty key is a key too
        assert_eq!((k.name(5_000), k.name(4_999)), (Some(""), Some(names[4_999].as_str())));
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
        let k = keeps(&[(100, 120), (700, 300)]);
        assert_eq!(k, [0, 400]);
        flag_chunk(a, &mut hist, 120, 0, k[0]);
        flag_chunk(b, &mut hist, 300, 1, k[1]);
        let flagged = rows.iter().filter(|r| r.flags & FARMER != 0).count();
        assert!(flagged > 10, "{flagged}");
        for i in 0..rows.len() {
            let h24 = if rows[i].order.slot < 700 { 120 } else { 300 };
            assert_eq!(rows[i].flags & FARMER != 0, brute(&rows, i, h24), "row {i} {:?}", rows[i].order);
        }
    }

    /// History rows (added before the first chunk) and chunks of `len` slots whose h24 jumps up by more than a chunk, the
    /// replay starting `skip` slots into the first: the flags against the brute-force rule, each trade with its own
    /// chunk's h24.
    fn history_and_jumping_h24(seed: u64, len: u64, h24s: &[u64], skip: u64) {
        let mut seed = seed;
        let mut next = || {
            seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
            seed >> 33
        };
        let from = 5_000;
        let chunk_of = |slot: u64| ((slot - from) / len) as usize;
        let mut rows = Vec::new();
        let first = from.saturating_sub(*h24s.iter().max().unwrap() + 3 * len);
        let (mut slot, mut washes) = (first, 0u64);
        while slot < from + len * h24s.len() as u64 {
            slot += 1 + next() % 7;
            let (trader, kind) = match next() % 5 {
                0 | 1 => {
                    washes += 1; // the wash trader alternates, so its windows are often near net 0
                    (1, if washes % 2 == 0 { PoolKind::Buy } else { PoolKind::Sell })
                }
                2 => (2, PoolKind::Sell),
                3 => (3, PoolKind::Buy),
                _ => (NONE, PoolKind::Buy),
            };
            rows.push(trade(slot, next() as u32 % 3, 9, trader, kind, 40 + (next() % 3) as i64));
        }
        rows.sort_by_key(|r| r.order);
        rows.dedup_by_key(|r| r.order);
        rows.retain(|r| r.order.slot < from + len * h24s.len() as u64);
        let start = |k: usize| from + k as u64 * len + if k == 0 { skip } else { 0 };
        let starts: Vec<(u64, u64)> = h24s.iter().enumerate().map(|(k, &h)| (start(k), h)).collect();
        let k = keeps(&starts);
        let h0 = k[0];
        let mut hist: HashMap<(Id, Id), FarmHist> = HashMap::new();
        let split = rows.partition_point(|r| r.order.slot < from + skip);
        for r in rows[..split].iter().filter(|r| r.order.slot >= h0) {
            let (buy, a) = r.token_side().unwrap();
            hist.entry((r.pool, r.trader)).or_default().add(r.order.slot, if buy { a } else { -a });
        }
        let mut emitted = rows[split..].to_vec();
        let mut at = 0;
        for c in 0..h24s.len() {
            let n = emitted[at..].partition_point(|r| chunk_of(r.order.slot) == c);
            flag_chunk(&mut emitted[at..at + n], &mut hist, h24s[c], c as u32, k[c]);
            at += n;
        }
        let mut flagged = 0;
        for (i, r) in emitted.iter().enumerate() {
            let want = brute(&rows, split + i, h24s[chunk_of(r.order.slot)]);
            assert_eq!(r.flags & FARMER != 0, want, "trade {:?} in chunk {}", r.order, chunk_of(r.order.slot));
            flagged += usize::from(want);
        }
        assert!(flagged > 5, "{flagged}");
    }

    #[test]
    fn the_farmer_flag_with_history_is_the_stores_also_when_h24_jumps_by_more_than_a_chunk() {
        // h24 up by 500 over chunks of 100 slots: the old rule (keep = chunk_lo - h24 of the current chunk) dropped what
        // the next chunk's window still reaches
        history_and_jumping_h24(11, 100, &[50, 550, 60, 900], 0);
        history_and_jumping_h24(12, 100, &[300, 120, 700], 0);
        history_and_jumping_h24(13, 250, &[400, 400, 400], 0);
        // a start inside the first chunk: its rows before the start are history, H0 is the start's window
        history_and_jumping_h24(14, 200, &[150, 160, 900], 120);
        history_and_jumping_h24(15, 200, &[600, 50, 50], 199);
        assert_eq!(keeps(&[(1000, 10), (1100, 500), (1200, 20)]), [600, 600, 1180]);
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
        emit_chunk(&curve, &pool, |s| (s != 12).then_some(s as i64), &mut HashMap::new(), (10, 30), |ev| {
            let (s, k, l) = (ev.leg.slot(), event_kind(ev), event_line(&keys, ev));
            seen.push((s, k.to_owned(), l.split('\t').nth(3).unwrap().to_owned()));
            sum.add(s, k, &l);
            Ok(())
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

    #[test]
    fn typed_events_render_the_c1a_lines_byte_for_byte() {
        // Every field of both venues and every pool kind (args, limits, boost_left, lp amounts present and absent, a
        // NULL trader, a slot without a block time, the pool context): the lines and summary the emission wrote before
        // it handed out typed events (C1a, 9260095/f95bf33), against the same rows through the events now.
        let mut keys = Keys::default();
        let (m, p, q, t) = (keys.id("MintAAA"), keys.id("PoolBBB"), keys.id("PoolCCC"), keys.id("TraderDDD"));
        let curve = vec![
            CurveLeg {
                order: Order { slot: 100, tx: 3, outer: 1, inner: 2 }, mint: m, trader: t, t: 1_234_567, q: 89_000_000_001, fee: 950_000,
                vt: 1_000_000_000_000_000, vq: 30_000_000_000, rt: 793_000_000_000_000, rq: 12_345_678_901, args: [5, 6, 7, 8], fee_bps: 95,
                creator_bps: 30, args_set: 0b1011, variant: CurveVariant::SellV2, is_buy: false, direct: false,
            },
            CurveLeg {
                order: Order { slot: 101, tx: 0, outer: 0, inner: 0 }, mint: m, trader: NONE, t: 7, q: 3, fee: 0, vt: 10, vq: 20, rt: 5, rq: 4,
                args: [0; 4], fee_bps: 0, creator_bps: 0, args_set: 0, variant: CurveVariant::Other, is_buy: true, direct: true,
            },
        ];
        let row = |slot, tx, pool, trader, kind, flags| PoolRow {
            order: Order { slot, tx, outer: 0, inner: 4 }, pool, trader, b: 1_000_000_000_000, vault: 85_000_000_000, vq: 1_500_000_000,
            base: 20_000_000, e_delta: 2_000_000_001, quote_user: 1_990_000_000, x: [3_000_000_000, 19_000_000], bps: [20, 5, 5, 1], kind,
            flags,
        };
        let pool = vec![
            row(100, 1, p, t, PoolKind::Buy, EXACT_OUT | DIRECT | FARMER | X0 | X1),
            row(100, 4, q, NONE, PoolKind::Sell, REVERSED | X0),
            row(101, 1, p, t, PoolKind::Boost, X0),
            row(102, 0, p, t, PoolKind::Withdraw, DIRECT | X0 | X1),
            row(102, 9, q, t, PoolKind::Deposit, REVERSED | X0),
        ];
        let (mut lines, mut sum) = (Vec::new(), Summary::new(100, 103, 2));
        emit_chunk(&curve, &pool, |s| (s != 101).then_some(1_790_000_000 + s as i64), &mut HashMap::new(), (100, 103), |ev| {
            let line = event_line(&keys, ev);
            sum.add(ev.leg.slot(), event_kind(ev), &line);
            lines.push(line);
            Ok(())
        })
        .unwrap();
        assert_eq!(lines[1], "curve\tMintAAA\t100\t3\t1\t2\t1790000100\t6681\tsell_v2\tfalse\t1234567\t89000000001\t950000\t\
                              999999998765433\t119000000001\t792999998765433\t101345678902\t1000000000000000\t30000000000\t\
                              793000000000000\t12345678901\t95\t30\tTraderDDD\t5\t6\tnull\t8\tfalse");
        assert_eq!(lines[4], "pool\tPoolBBB\t101\t1\t0\t4\tnull\t132\tboost\tfalse\t20000000\t2000000001\t1000000000000\t\
                              86500000000\t0\t0\t0\t0\t1500000000\t999980000000\t88500000001\t0\t0\t0\t0\t1500000000\tTraderDDD\tnull\t\
                              null\t3000000000\ttrue\tnull\tnull\tfalse");
        let want = concat!(
            r#"{"events":7,"from":100,"kinds":{"curve":2,"pool/boost":1,"pool/buy":1,"pool/deposit":1,"pool/sell":1,"pool/withdraw":1}"#,
            r#","sha256":"5ebe6470d455ac492a3e4b8af91845b448ee518c61ee2783d9ccdd93d3358612","to":103,"window":2"#,
            r#","windows":[[100,102,5,"138b6c07c9d3068050b30d7987c929c0c2eac4c028b0b8941a2059a035a8a367"],"#,
            r#"[102,103,2,"a063ab011418266dc05251fda1866e71d87836e3e5c378262751b0be35228e21"]]}"#,
        );
        assert_eq!(sum.finish().to_string(), want);
    }
}
