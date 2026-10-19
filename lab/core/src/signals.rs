//! The F7 signals of a replay, slot by slot, as the paper engine writes them (engine.py `run` :301-323, `_end_slot`
//! steps 3, 4 and 4b :392-413, `_f7` :611-648, `_signal` :726-763). M1 C1b of
//! lab/docs/rust-live-route-design-2026-10-16.md; the porting spec is research/m1-c1b-signals-spec-2026-10-19.md
//! (§3.4 the per-slot order, §3.9 the rows).
//!
//! - A slot's `by_key` keeps the engine's first-appearance order of (venue, key); no HashMap iteration orders anything.
//! - Per slot: step 3 (pool_seen, pool_last, recent) for every key, then per key F7 and the hour index (the rollover
//!   can fire after the first key's F7), then the pools' farm windows.
//! - Rows go to the caller's `emit` in the engine's write order: by key, per trigger the groups, per group the F7 rows
//!   over the variants, then per N2 pick its rows. The journal's INSERT OR REPLACE is the caller's, so where two
//!   triggers of a slot pick the same N2 key the later row wins.
//! - Only the farm gates are decided here; a row that passes them has no status yet (spec D10). Busy, risk and the
//!   pending entry are C2/C3, behind `Gate::Pass`.
//! - A replay row has NULL block_time, the latency columns and d_ms (ReplayFeed events carry no arrival), and NULL
//!   farm_complete (no farm_seed).

use crate::clock::{day_of, Clock, EPOCH_SLOTS, HISTORY_SLOTS, POOL_SEEN_KEEP_MS};
use crate::f7::{f7_signal, trigger, Recent};
use crate::farm::{FarmWindow, WindowRule};
use crate::legs::{Event, Id, Leg, Venue, NONE};
use crate::n2::{HourIndex, N2Keys};
use crate::universe::{Reject, Segment, Universe};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fmt;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Family {
    F7,
    N2F7,
}

impl Family {
    pub fn as_str(self) -> &'static str {
        match self {
            Family::F7 => "F7",
            Family::N2F7 => "N2_F7",
        }
    }
}

/// A trigger's farm label: curves have none; a pool is flagged when farmers made most of its last hour's volume.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Farm {
    NotApplicable,
    Flagged,
    Organic,
}

impl Farm {
    pub fn as_str(self) -> &'static str {
        match self {
            Farm::NotApplicable => "n/a",
            Farm::Flagged => "flagged",
            Farm::Organic => "organic",
        }
    }
}

/// The farm gates of `_signal`, in its order (engine.py:737-742). `Pass` goes on to busy and risk (C2/C3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Gate {
    Pass,
    FarmFlagged,
    FarmHistoryIncomplete,
    FarmWindowIncomplete,
}

impl Gate {
    pub const ALL: [Gate; 4] = [Gate::Pass, Gate::FarmFlagged, Gate::FarmHistoryIncomplete, Gate::FarmWindowIncomplete];

    /// The journal's reason of a gated row; None for `Pass`.
    pub fn reason(self) -> Option<&'static str> {
        match self {
            Gate::Pass => None,
            Gate::FarmFlagged => Some("farm flagged"),
            Gate::FarmHistoryIncomplete => Some("farm history incomplete"),
            Gate::FarmWindowIncomplete => Some("farm window incomplete"),
        }
    }
}

/// A variant group as the journal's state 'groups' gives it (engine.py `Group`, the groups_state of :1386-1391).
/// `d`, `d_ms` are kept for C2; nothing here reads them.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GroupSpec {
    pub name: String,
    pub primary: bool,
    pub variants: Vec<String>,
    /// Trades only these segments (indexed by `Segment::index`); None everywhere. An empty list in the file is None,
    /// as Python's `set(segments) if segments else None`.
    pub segments: Option<[bool; 3]>,
    /// farm 'organic' (else 'all'): flagged triggers are skipped.
    pub organic: bool,
    pub n2_k: usize,
    pub require_farm_complete: bool,
    pub d: u64,
    pub d_ms: Option<u64>,
}

impl GroupSpec {
    pub fn trades(&self, seg: Segment) -> bool {
        self.segments.is_none_or(|s| s[seg.index()])
    }
}

/// `_signal`'s decision up to busy (engine.py:737-742): the farm gates first, in this order, then `Pass`. A NULL
/// farm_complete (replay) or farm_window_complete (curves) gates nothing, as Python's `None == 0`.
pub fn gate(g: &GroupSpec, farm: Farm, farm_complete: Option<u8>, window: Option<u8>) -> Gate {
    if g.organic && farm == Farm::Flagged {
        Gate::FarmFlagged
    } else if g.require_farm_complete && farm_complete == Some(0) {
        Gate::FarmHistoryIncomplete
    } else if g.require_farm_complete && window == Some(0) {
        Gate::FarmWindowIncomplete
    } else {
        Gate::Pass
    }
}

/// One signals row (journal.py:17-30) as far as the stream decides it. The columns not here are NULL in a replay:
/// block_time, received_unix_ns, created_unix_ns, latency_ms, segment_written_unix, segment_lag_ms, d_ms.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SignalRow {
    pub family: Family,
    /// The group's index in the engine's groups.
    pub group: usize,
    pub variant: String,
    pub key: Id,
    pub t: u64,
    pub day: Option<String>,
    /// Venue and orientation.
    pub seg: Segment,
    pub depth: i128,
    /// NONE is NULL: N2 rows, and a trigger leg without a trader.
    pub trigger_trader: Id,
    pub farm: Farm,
    pub farm_complete: Option<u8>,
    /// Pools: the first slot this key was seen (an N2 row: the N2 key's).
    pub pool_seen_slot: Option<u64>,
    pub farm_window_complete: Option<u8>,
    pub gate: Gate,
}

impl SignalRow {
    /// 'skipped' for a gated row; NULL (undecided until C2/C3) for one that passed the gates.
    pub fn status(&self) -> Option<&'static str> {
        self.gate.reason().map(|_| "skipped")
    }

    pub fn reason(&self) -> Option<&'static str> {
        self.gate.reason()
    }
}

/// One slot's legs per (venue, key) in first-appearance order (engine.py:312 `by_key`): a list and an index into it.
#[derive(Clone, Debug, Default)]
pub struct SlotLegs {
    keys: Vec<(Venue, Id, Vec<Leg>)>,
    index: HashMap<(Venue, Id), usize>,
}

impl SlotLegs {
    pub fn push(&mut self, venue: Venue, key: Id, leg: Leg) {
        let n = self.keys.len();
        let i = *self.index.entry((venue, key)).or_insert(n);
        if i == n {
            self.keys.push((venue, key, Vec::new()));
        }
        self.keys[i].2.push(leg);
    }

    pub fn iter(&self) -> impl Iterator<Item = (Venue, Id, &[Leg])> {
        self.keys.iter().map(|(v, k, legs)| (*v, *k, legs.as_slice()))
    }

    pub fn clear(&mut self) {
        self.keys.clear();
        self.index.clear();
    }

    /// (venue, key) entries.
    pub fn len(&self) -> usize {
        self.keys.len()
    }

    pub fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }

    pub fn events(&self) -> usize {
        self.keys.iter().map(|k| k.2.len()).sum()
    }
}

/// The summary's counters.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Counts {
    pub events: u64,
    pub slots: u64,
    /// Universe rejections where F7 asked (in [dlo, dhi]), indexed by `Reject as usize`.
    pub rejects: [u64; 8],
    /// Event-local trigger passes and F7 passes per segment (`Segment::index`).
    pub triggers: [u64; 3],
    pub f7: [u64; 3],
    /// Rows per family (`Family as usize`) and gate (`Gate as usize`), overwrites included.
    pub rows: [[u64; 4]; 2],
    pub rollovers: u64,
    /// Rows written again within their slot (an N2 key two triggers picked): the later write wins in the journal.
    pub overwrites: u64,
    /// The windows (n_in, n_cr, n_gap, H) per epoch with events.
    pub windows: BTreeMap<u64, [u64; 4]>,
}

/// A debugging trace line (`lab-live signals --trace`): an hour rollover, and the key whose hour index rolled it;
/// an F7 pass with its depth and farm label.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Trace {
    Hour { slot: u64, venue: Venue, key: Id },
    Trig { t: u64, venue: Venue, key: Id, depth: i128, farm: Farm },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SignalError {
    /// The groups or the feed rule cannot be run.
    Config(String),
    /// A curve token reached the age test without a create_slot (Python raises at engine.py:561).
    NoCreateSlot { mint: Id, t: u64 },
}

impl fmt::Display for SignalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SignalError::Config(s) => write!(f, "signals: {s}"),
            SignalError::NoCreateSlot { mint, t } => write!(f, "signals: token id {mint} has no create_slot (F7 at t {t}; Python raises here)"),
        }
    }
}

impl std::error::Error for SignalError {}

/// A trigger that passed F7: what each of its rows carries.
#[derive(Clone, Copy)]
struct Trig {
    t: u64,
    seg: Segment,
    depth: i128,
    farm: Farm,
}

/// The engine's signal state and its F7 decisions. Every field starts empty at the feed's first event.
#[derive(Clone, Debug)]
pub struct SignalEngine {
    pub universe: Universe,
    pub clock: Clock,
    /// The primary first by convention; the order changes no final row (variant names are distinct across groups).
    pub groups: Vec<GroupSpec>,
    pub window_rule: WindowRule,
    /// Live only: the farm_seed's complete_from (engine.py:730-731); None in a replay, so farm_complete is NULL.
    pub farm_complete_from: Option<u64>,
    pub first_slot: Option<u64>,
    pub day: Option<String>,
    pub recent: Recent,
    pub farm: FarmWindow,
    pub hours: HourIndex,
    pub pool_seen: HashMap<Id, u64>,
    pub pool_last: HashMap<Id, u64>,
    pub counts: Counts,
    /// Some: F7 passes and rollovers are recorded here for the caller to drain.
    pub trace: Option<Vec<Trace>>,
    cur: Option<(u64, Option<i64>)>,
    by_key: SlotLegs,
    written: HashSet<(Family, usize, usize, Id)>,
}

impl SignalEngine {
    /// Refuses: not exactly one primary group, a group or variant name used twice (engine.py:237-239), and the live
    /// farm-window rule (M3).
    pub fn new(universe: Universe, clock: Clock, groups: Vec<GroupSpec>, window_rule: WindowRule) -> Result<Self, SignalError> {
        let config = |s: String| Err(SignalError::Config(s));
        if groups.iter().filter(|g| g.primary).count() != 1 {
            return config(format!("exactly one primary group, not {}", groups.iter().filter(|g| g.primary).count()));
        }
        let names: HashSet<&str> = groups.iter().map(|g| g.name.as_str()).collect();
        let variants: Vec<&str> = groups.iter().flat_map(|g| g.variants.iter().map(String::as_str)).collect();
        if names.len() != groups.len() || variants.iter().collect::<HashSet<_>>().len() != variants.len() {
            return config("groups and their variants must have distinct names".to_owned());
        }
        if window_rule == WindowRule::Live {
            return config("the live farm-window rule is not ported (M3, engine.py:806-813)".to_owned());
        }
        Ok(SignalEngine {
            universe,
            clock,
            groups,
            window_rule,
            farm_complete_from: None,
            first_slot: None,
            day: None,
            recent: Recent::default(),
            farm: FarmWindow::default(),
            hours: HourIndex::default(),
            pool_seen: HashMap::new(),
            pool_last: HashMap::new(),
            counts: Counts::default(),
            trace: None,
            cur: None,
            by_key: SlotLegs::default(),
            written: HashSet::new(),
        })
    }

    /// One event of the feed, in chain order (engine.py `run`, :301-316): an event of a new slot first ends the slot
    /// before it. The slot's block time is its first event's.
    pub fn push(&mut self, ev: &Event, keys: &mut impl N2Keys, emit: &mut impl FnMut(SignalRow)) -> Result<(), SignalError> {
        let slot = ev.leg.slot();
        if self.cur.map(|c| c.0) != Some(slot) {
            self.flush(keys, emit)?;
            self.cur = Some((slot, ev.block_time));
        }
        self.by_key.push(ev.venue, ev.key, ev.leg);
        Ok(())
    }

    /// The end of the feed: its last slot runs too (engine.py:321-323).
    pub fn finish(&mut self, keys: &mut impl N2Keys, emit: &mut impl FnMut(SignalRow)) -> Result<(), SignalError> {
        self.flush(keys, emit)
    }

    fn flush(&mut self, keys: &mut impl N2Keys, emit: &mut impl FnMut(SignalRow)) -> Result<(), SignalError> {
        let Some((slot, bt)) = self.cur.take() else { return Ok(()) };
        let by_key = std::mem::take(&mut self.by_key);
        let done = self.end_slot(slot, bt, &by_key, keys, emit);
        self.by_key = by_key;
        self.by_key.clear();
        done
    }

    /// One slot with at least one event (engine.py `_end_slot`, its signal parts). Steps 1-2 (positions, entries) and
    /// the graduation exits are C2 and touch no signal state.
    pub fn end_slot(
        &mut self,
        slot: u64,
        block_time: Option<i64>,
        by_key: &SlotLegs,
        keys: &mut impl N2Keys,
        emit: &mut impl FnMut(SignalRow),
    ) -> Result<(), SignalError> {
        if self.first_slot.is_none() {
            self.first_slot = Some(slot);
        }
        if let Some(bt) = block_time {
            self.day = day_of(bt); // `_clock`: a slot without a block time keeps the day
        }
        self.counts.slots += 1;
        self.counts.events += by_key.events() as u64;
        self.counts.windows.entry(slot / EPOCH_SLOTS).or_insert_with(|| self.clock.windows(slot));
        // 3. history (engine.py:392-401): every key, the pools' first and last slot (mayhem pools too).
        for (venue, key, legs) in by_key.iter() {
            if venue == Venue::Pool {
                let seen = self.pool_seen.entry(key).or_insert(slot);
                *seen = (*seen).min(slot);
                let last = self.pool_last.entry(key).or_insert(slot);
                *last = (*last).max(slot);
            }
            self.recent.extend(key, legs, slot);
        }
        // 4. signals (engine.py:402-406): per key its F7, then its hour index.
        self.written.clear();
        for (venue, key, legs) in by_key.iter() {
            self.f7(venue, key, legs, slot, keys, emit)?;
            self.hour_index(venue, key, legs, slot);
        }
        // 4b. the pools' farm windows, after the signals (engine.py:409-413).
        for (venue, key, legs) in by_key.iter() {
            if venue == Venue::Pool {
                if let Some(p) = self.universe.pool(key) {
                    let reversed = p.reversed;
                    self.farm.add(key, reversed, legs, &self.clock);
                }
            }
        }
        Ok(())
    }

    /// engine.py `_f7`: the decision bounds first, then the universe, the groups trading the segment, the event-local
    /// trigger, the windows, and the rows.
    fn f7(
        &mut self,
        venue: Venue,
        key: Id,
        legs: &[Leg],
        t: u64,
        keys: &mut impl N2Keys,
        emit: &mut impl FnMut(SignalRow),
    ) -> Result<(), SignalError> {
        if !self.universe.bounds.decides(t) {
            return Ok(());
        }
        let k = match self.universe.f7_key(venue, key) {
            Ok(k) => k,
            Err(Reject::NoCreateSlot) => return Err(SignalError::NoCreateSlot { mint: key, t }),
            Err(r) => {
                self.counts.rejects[r as usize] += 1;
                return Ok(());
            }
        };
        if !self.groups.iter().any(|g| g.trades(k.seg)) {
            return Ok(());
        }
        let Some((leg, depth)) = trigger(legs, &k, self.universe.mayhem_agent) else { return Ok(()) };
        self.counts.triggers[k.seg.index()] += 1;
        if !f7_signal(self.recent.get(key), k.seg, t, k.creator, &self.clock) {
            return Ok(());
        }
        self.counts.f7[k.seg.index()] += 1;
        let farm = match k.seg {
            Segment::Curve => Farm::NotApplicable,
            _ if self.farm.flagged(key, t, &self.clock) => Farm::Flagged,
            _ => Farm::Organic,
        };
        if let Some(tr) = self.trace.as_mut() {
            tr.push(Trace::Trig { t, venue, key, depth, farm });
        }
        // engine.py:642-648: per group its F7 rows, then per N2 pick its rows (depth and farm the trigger's, no trader).
        let trig = Trig { t, seg: k.seg, depth, farm };
        let mut rows = Vec::new();
        for (gi, g) in self.groups.iter().enumerate().filter(|(_, g)| g.trades(k.seg)) {
            for (vi, v) in g.variants.iter().enumerate() {
                rows.push(((Family::F7, gi, vi, key), self.row(Family::F7, (gi, v), key, leg.trader(), &trig)));
            }
            for m in self.hours.pick(k.seg, key, t, depth, g.n2_k, keys) {
                for (vi, v) in g.variants.iter().enumerate() {
                    rows.push(((Family::N2F7, gi, vi, m), self.row(Family::N2F7, (gi, v), m, NONE, &trig)));
                }
            }
        }
        for (id, row) in rows {
            self.counts.overwrites += u64::from(!self.written.insert(id));
            self.counts.rows[row.family as usize][row.gate as usize] += 1;
            emit(row);
        }
        Ok(())
    }

    /// engine.py `_signal` up to the farm gates, for a group's variant; pool_seen_slot and farm_window_complete for the
    /// row's own key.
    fn row(&self, family: Family, (group, variant): (usize, &str), key: Id, trader: Id, trig: &Trig) -> SignalRow {
        let Trig { t, seg, depth, farm } = *trig;
        let pool = seg.venue() == Venue::Pool;
        let farm_complete = self.farm_complete_from.map(|c| u8::from(t >= c));
        let window = if pool { self.window_rule.complete(self.first_slot, t, &self.clock) } else { None };
        SignalRow {
            family,
            group,
            variant: variant.to_owned(),
            key,
            t,
            day: self.day.clone(),
            seg,
            depth,
            trigger_trader: trader,
            farm,
            farm_complete,
            pool_seen_slot: if pool { self.pool_seen.get(&key).copied() } else { None },
            farm_window_complete: window,
            gate: gate(&self.groups[group], farm, farm_complete, window),
        }
    }

    /// engine.py `_hour_index`: a new hour first (its prunes with n at this slot), then the key's N2 candidacy with its
    /// last leg's depth.
    fn hour_index(&mut self, venue: Venue, key: Id, legs: &[Leg], slot: u64) {
        if let Some(h) = self.hours.roll(slot, &self.clock) {
            self.counts.rollovers += 1;
            if let Some(tr) = self.trace.as_mut() {
                tr.push(Trace::Hour { slot, venue, key });
            }
            self.farm.rollover(slot, &self.clock);
            // (farm_fixes: live only, M3.) C2 keeps the keys with an open, pending or graduating position.
            self.recent.sweep(slot.saturating_sub(HISTORY_SLOTS.max(h)), |_| false);
            // pool_seen of pools without a trade for 24 h goes; the test reads pool_last (engine.py:697-701).
            let old = slot.saturating_sub(self.clock.slots(POOL_SEEN_KEEP_MS, slot));
            let seen = &mut self.pool_seen;
            self.pool_last.retain(|p, last| {
                let keep = *last >= old;
                if !keep {
                    seen.remove(p);
                }
                keep
            });
        }
        if let Some((seg, depth)) = legs.last().and_then(|l| self.universe.n2_bucket(key, l)) {
            self.hours.push(seg, key, slot, depth);
        }
    }
}

#[cfg(test)]
mod cases;

#[cfg(test)]
mod tests {
    use super::cases::groups;
    use super::*;
    use crate::clock::tests::dev2;
    use crate::clock::{Bounds, SOL};
    use crate::f7::tests::{curve, pool, t1_legs, MAYHEM, T1_CREATOR, T1_MINT, T1_TRADER};
    use crate::legs::{PoolKind, WSOL};
    use crate::n2::tests::FakeKeys;
    use crate::universe::{MintMeta, PoolMeta};

    const ONES: &str = "11111111111111111111111111111111";
    const PUMP_QUOTE: &str = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn";

    fn engine(u: Universe, n2_k: usize) -> SignalEngine {
        SignalEngine::new(u, dev2(), groups(n2_k), WindowRule::AllPools).unwrap()
    }

    fn dev2_universe() -> Universe {
        Universe::new(Bounds::new((446_256_000, 452_304_000), &dev2()), MAYHEM)
    }

    /// The legs through push and finish, with the block time the case generator gives a slot.
    fn feed(eng: &mut SignalEngine, legs: &[Leg]) -> Result<Vec<SignalRow>, SignalError> {
        let mut keys = FakeKeys::default();
        let mut rows = Vec::new();
        for &l in legs {
            let bt = 1_790_000_000 + (l.slot() as i64 - 450_000_000) * 2 / 5;
            eng.push(&Event::new(l, Some(bt)), &mut keys, &mut |r| rows.push(r))?;
        }
        eng.finish(&mut keys, &mut |r| rows.push(r))?;
        Ok(rows)
    }

    #[test]
    fn t1_is_rejected_for_its_pump_quote_and_its_controls_signal() {
        // The 13 real legs; lab-py Engine on the same legs (the cases generator): as stored no row, no N2 candidacy;
        // quoted in 1111 or WSOL one F7 row.
        let tok = |quote, mayhem| MintMeta::new(Some(450_196_501), T1_CREATOR, Some(mayhem), quote);
        for (quote, mayhem, cause) in [(Some(PUMP_QUOTE), false, Reject::QuoteNotSol), (None, false, Reject::QuoteNull),
                                       (Some(ONES), true, Reject::CurveMayhem)] {
            let mut u = dev2_universe();
            u.add_mint(T1_MINT, tok(quote, mayhem));
            let mut eng = engine(u, 1);
            assert_eq!(feed(&mut eng, &t1_legs()).unwrap(), vec![], "{quote:?} {mayhem}");
            assert_eq!((eng.hours.len(), eng.counts.rejects[cause as usize], eng.counts.triggers), (0, 8, [0; 3]));
        }
        let mut eng = engine(dev2_universe(), 1); // the mint absent
        assert_eq!((feed(&mut eng, &t1_legs()).unwrap().len(), eng.hours.len(), eng.counts.rejects[Reject::CurveAbsent as usize]), (0, 0, 8));
        for quote in [ONES, WSOL] {
            let mut u = dev2_universe();
            u.add_mint(T1_MINT, tok(Some(quote), false));
            let mut eng = engine(u, 1);
            let rows = feed(&mut eng, &t1_legs()).unwrap();
            let row = SignalRow {
                family: Family::F7, group: 0, variant: "tp4_T600".into(), key: T1_MINT, t: 450_197_213, day: Some("2026-09-22".into()),
                seg: Segment::Curve, depth: 53_657_221_584, trigger_trader: T1_TRADER, farm: Farm::NotApplicable, farm_complete: None,
                pool_seen_slot: None, farm_window_complete: None, gate: Gate::Pass,
            };
            assert_eq!(rows, vec![row]);
            // engine.hour_events: each slot's last leg's post rq.
            let rq: Vec<(Id, u64, i128)> = [(450_196_501, 207_348_765_062), (450_196_514, 151_729_232_918), (450_196_532, 100_656_355_106),
                                            (450_196_603, 62_718_517_929), (450_196_616, 44_632_444_539), (450_197_178, 7),
                                            (450_197_179, 53_657_221_584), (450_197_213, 8)]
                .map(|(s, d)| (T1_MINT, s, d)).to_vec();
            assert_eq!(eng.hours.events(Segment::Curve), rq);
            assert_eq!((eng.counts.triggers, eng.counts.f7), ([1, 0, 0], [1, 0, 0]));
        }
    }

    #[test]
    fn mint_absent_applies_to_curves_only() {
        // A curve without a token: no row, no candidacy. A SOL-market pool whose mint is no token signals, with the
        // coin creator as its creator: the coin creator's small sell in the creator window blocks it.
        let mut u = dev2_universe();
        u.add_pool(60, PoolMeta { reversed: false, mayhem: false, mint_tok: Some(61), coin_creator: 62 });
        u.add_pool(70, PoolMeta { reversed: false, mayhem: false, mint_tok: Some(71), coin_creator: 72 });
        let t = 450_200_000;
        let s = SOL as i64;
        let mut legs = vec![curve((t - 10, 0), 50, 3, true, 6 * SOL as u64, 100 * SOL as u64, 40 * SOL as u64),
                            curve((t, 0), 50, 4, false, 5 * SOL as u64, 100 * SOL as u64, 40 * SOL as u64)];
        for (p, coin) in [(60, 62), (70, 0)] {
            legs.push(pool((t - 20, 1), p, 3, PoolKind::Buy, (0, 10 * s), (0, 500 * s)));
            legs.push(pool((t - 3, 2), p, coin, PoolKind::Sell, (0, s / 2), (0, 510 * s))); // 70: not its coin creator
            legs.push(pool((t, 3), p, 4, PoolKind::Sell, (0, 25 * s), (0, 509 * s)));
        }
        legs.sort_by_key(Leg::order);
        let mut eng = engine(u, 0);
        let rows = feed(&mut eng, &legs).unwrap();
        assert_eq!(rows.iter().map(|r| (r.key, r.t, r.depth)).collect::<Vec<_>>(), [(70, t, 509 * SOL)]);
        assert!(eng.hours.events(Segment::Curve).is_empty());
        assert_eq!((eng.counts.rejects[Reject::CurveAbsent as usize], eng.counts.triggers), (2, [0, 2, 0]));
    }

    #[test]
    fn a_null_quote_token_is_no_n2_candidate() {
        // A (SOL quote) triggers at 45 SOL; B (quote NULL) and C (SOL, an N2 candidate) traded at 44 SOL before.
        let mut u = dev2_universe();
        for (m, q) in [(1, Some(ONES)), (2, None), (3, Some(ONES))] {
            u.add_mint(m, MintMeta::new(Some(450_100_000), 9, None, q));
        }
        let t = 450_200_000;
        let sol = SOL as u64;
        let legs = [curve((t - 20, 0), 2, 5, true, sol, 100 * sol, 44 * sol), curve((t - 20, 1), 3, 5, true, sol, 100 * sol, 44 * sol),
                    curve((t - 10, 0), 1, 5, true, 6 * sol, 100 * sol, 40 * sol), curve((t, 0), 1, 4, false, 5 * sol, 100 * sol, 40 * sol)];
        let mut eng = engine(u, 3);
        let mut keys = FakeKeys { names: vec!["", "a", "b", "c"], seeds: vec![0; 4], ..Default::default() };
        keys.triggers.insert((1, t), 0);
        let mut rows = Vec::new();
        for l in legs {
            eng.push(&Event::new(l, None), &mut keys, &mut |r| rows.push(r)).unwrap();
        }
        eng.finish(&mut keys, &mut |r| rows.push(r)).unwrap();
        assert_eq!(rows.iter().map(|r| (r.family, r.key)).collect::<Vec<_>>(), [(Family::F7, 1), (Family::N2F7, 3)]);
        assert_eq!(eng.counts.rejects[Reject::QuoteNull as usize], 1);
        assert!(eng.hours.events(Segment::Curve).iter().all(|e| e.0 != 2));
    }

    #[test]
    fn a_token_without_create_slot_is_an_error_when_reached() {
        // Python raises a TypeError at the age test; a mayhem token without create_slot is rejected before it.
        let mut u = dev2_universe();
        u.add_mint(1, MintMeta::new(None, 9, None, Some(ONES)));
        u.add_mint(2, MintMeta::new(None, 9, Some(true), Some(ONES)));
        let leg = |m| curve((450_200_000, 0), m, 5, true, 1, 1, 1);
        assert_eq!(feed(&mut engine(u.clone(), 1), &[leg(2)]), Ok(vec![]));
        assert_eq!(feed(&mut engine(u.clone(), 1), &[leg(1)]), Err(SignalError::NoCreateSlot { mint: 1, t: 450_200_000 }));
        // Outside [dlo, dhi] F7 never asks.
        assert_eq!(feed(&mut engine(u, 1), &[curve((400_000_000, 0), 1, 5, true, 1, 1, 1)]), Ok(vec![]));
    }

    #[test]
    fn the_gates_come_in_the_engines_order() {
        let [k3, h] = <[GroupSpec; 2]>::try_from(groups(1)).unwrap();
        assert_eq!(gate(&k3, Farm::Flagged, Some(0), Some(0)), Gate::Pass); // farm all, no require_farm_complete
        assert_eq!(gate(&h, Farm::Flagged, Some(0), Some(0)), Gate::FarmFlagged);
        assert_eq!(gate(&h, Farm::Organic, Some(0), Some(0)), Gate::FarmHistoryIncomplete); // live only
        assert_eq!(gate(&h, Farm::Organic, None, Some(0)), Gate::FarmWindowIncomplete);
        assert_eq!(gate(&h, Farm::Organic, Some(1), Some(1)), Gate::Pass);
        assert_eq!(gate(&h, Farm::NotApplicable, None, None), Gate::Pass); // None == 0 is False
        assert_eq!(Gate::ALL.map(Gate::reason), [None, Some("farm flagged"), Some("farm history incomplete"), Some("farm window incomplete")]);
        let row = |gate| SignalRow {
            family: Family::N2F7, group: 1, variant: "flow300_H6".into(), key: 1, t: 2, day: None, seg: Segment::PoolReversed, depth: 3,
            trigger_trader: NONE, farm: Farm::Flagged, farm_complete: None, pool_seen_slot: Some(1), farm_window_complete: Some(1), gate,
        };
        assert_eq!((row(Gate::Pass).status(), row(Gate::FarmFlagged).status()), (None, Some("skipped")));
        assert_eq!((Family::N2F7.as_str(), Farm::NotApplicable.as_str()), ("N2_F7", "n/a"));
    }

    #[test]
    fn farm_complete_follows_the_live_seed() {
        // Live only (farm_seed's complete_from): NULL in a replay, else 1 from complete_from on.
        let mut u = dev2_universe();
        u.add_pool(1, PoolMeta { reversed: true, mayhem: false, mint_tok: None, coin_creator: 9 });
        let s = SOL as i64;
        let t = 450_200_000;
        let legs = [pool((t - 10, 0), 1, 3, PoolKind::Sell, (20 * s, 0), (300 * s, 0)),
                    pool((t, 0), 1, 4, PoolKind::Buy, (15 * s, 0), (320 * s, 0))];
        let mut eng = engine(u, 0);
        eng.farm_complete_from = Some(t + 1);
        let rows = feed(&mut eng, &legs).unwrap();
        assert_eq!(rows.iter().map(|r| (r.variant.as_str(), r.farm_complete, r.gate)).collect::<Vec<_>>(),
                   [("tp4_T600", Some(0), Gate::Pass), ("flow300_H6", Some(0), Gate::FarmHistoryIncomplete),
                    ("flow300_tp100_H6", Some(0), Gate::FarmHistoryIncomplete)]);
    }

    #[test]
    fn the_day_is_the_slots_first_block_time() {
        let mut eng = engine(dev2_universe(), 1);
        let mut keys = FakeKeys::default();
        let by = |slot| {
            let mut b = SlotLegs::default();
            b.push(Venue::Curve, 1, curve((slot, 0), 1, 2, true, 1, 1, 1));
            b
        };
        let mut day = |eng: &mut SignalEngine, slot, bt| {
            eng.end_slot(slot, bt, &by(slot), &mut keys, &mut |_| ()).unwrap();
            eng.day.clone()
        };
        assert_eq!(day(&mut eng, 450_200_000, None), None);
        assert_eq!(day(&mut eng, 450_200_001, Some(1_790_000_000)).as_deref(), Some("2026-09-21"));
        assert_eq!(day(&mut eng, 450_200_002, None).as_deref(), Some("2026-09-21")); // no block time: the day stays
        assert_eq!(day(&mut eng, 450_200_003, Some(0)), None); // 0 is falsy in `_day`
        assert_eq!((eng.first_slot, eng.counts.slots, eng.counts.events), (Some(450_200_000), 4, 4));
        // Through push: the slot's block time is its first event's.
        let mut eng = engine(dev2_universe(), 1);
        let mut keys = FakeKeys::default();
        for bt in [None, Some(1_790_000_000)] {
            eng.push(&Event::new(curve((450_200_000, 0), 1, 2, true, 1, 1, 1), bt), &mut keys, &mut |_| ()).unwrap();
        }
        eng.finish(&mut keys, &mut |_| ()).unwrap();
        assert_eq!((eng.day.as_deref(), eng.counts.slots), (None, 1));
    }

    #[test]
    fn slot_legs_keep_the_first_appearance_order() {
        let mut b = SlotLegs::default();
        let (p, c) = (pool((7, 0), 5, 1, PoolKind::Buy, (0, 0), (0, 0)), curve((7, 1), 3, 1, true, 1, 1, 1));
        b.push(Venue::Pool, 5, p);
        b.push(Venue::Curve, 3, c);
        b.push(Venue::Pool, 5, p);
        b.push(Venue::Curve, 5, c); // another venue: another entry
        assert_eq!(b.iter().map(|(v, k, l)| (v, k, l.len())).collect::<Vec<_>>(),
                   [(Venue::Pool, 5, 2), (Venue::Curve, 3, 1), (Venue::Curve, 5, 1)]);
        assert_eq!((b.len(), b.events()), (3, 4));
        b.clear();
        assert!(b.is_empty());
    }

    #[test]
    fn the_engine_refuses_groups_it_cannot_run() {
        let new = |g: Vec<GroupSpec>, rule| SignalEngine::new(dev2_universe(), dev2(), g, rule).map(|_| ());
        assert_eq!(new(groups(1), WindowRule::AllPools), Ok(()));
        assert!(matches!(new(groups(1), WindowRule::Live), Err(SignalError::Config(_))));
        let mut two = groups(1);
        two[1].primary = true;
        assert!(new(two, WindowRule::AllPools).is_err());
        let mut none = groups(1);
        none[0].primary = false;
        assert!(new(none, WindowRule::AllPools).is_err());
        let mut dup = groups(1);
        dup[1].variants[0] = "tp4_T600".into();
        assert!(new(dup, WindowRule::AllPools).is_err());
        let mut name = groups(1);
        name[1].name = "K3".into();
        assert!(new(name, WindowRule::AllPools).is_err());
        assert!(groups(1)[1].trades(Segment::PoolReversed) && !groups(1)[1].trades(Segment::PoolNormal) && groups(1)[0].trades(Segment::Curve));
    }
}
