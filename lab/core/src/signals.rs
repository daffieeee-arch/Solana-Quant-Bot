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
//! - C2 (research/m1-c2-positionsim-spec-2026-10-19.md §3.2-§3.4, §5.1) adds the rest of `_end_slot` through `Hooks`,
//!   called where the engine runs each step: steps 1-2b before the history, busy and the Pending per row past the gates,
//!   the sweep's keep-set, step 5 after the farm windows. `push`, `finish` and `end_slot` are C1b's, with `NoHooks`: no
//!   step runs and a row past the gates stays NULL.
//! - A replay row has NULL block_time, the latency columns and d_ms (ReplayFeed events carry no arrival), and NULL
//!   farm_complete (no farm_seed). Live (M3-11): an event's arrival comes with its push (`push_arrived`), and a row's
//!   `Latency` is its trigger transaction's (engine.py `_latency`).
//! - Live (M3-11): a recorded trade's farmer flag computed again (`add_flag_fix`) reaches the farm windows at the next
//!   slot end, after the backfills (engine.py:422-428), and `Hooks::flag_fixed` counts it.

use crate::clock::{day_of, Clock, EPOCH_SLOTS, HISTORY_SLOTS, POOL_SEEN_KEEP_MS};
use crate::f7::{f7_signal, trigger, Recent};
use crate::farm::{live_complete, FarmWindow, WindowRule};
use crate::legs::{Arrival, Event, Id, Leg, Order, Venue, NONE};
use crate::mem::{self, Mem};
use crate::n2::{HourIndex, N2Keys};
use crate::universe::{Reject, Segment, Universe};
use std::borrow::Cow;
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

    /// engine.py `_d_at` (:667-672): the entry and exit delay of a decision at t, d_ms in the clock's slots at t, else d.
    /// A d_ms of 0 never reaches it: lab-live's parse_groups refuses it (Python's `if g.d_ms` would take d there).
    pub fn d_at(&self, t: u64, clock: &Clock) -> u64 {
        self.d_ms.map_or(self.d, |ms| clock.slots(ms as f64, t))
    }
}

/// The live-only signal columns (engine.py `_latency`, :650-665): the trigger transaction's arrival beside the slot's
/// block time, and the group's d in ms. Reporting only: journal_diff compares block_time and d_ms, not the measured ones.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Latency {
    pub block_time: i64,
    pub received_unix_ns: Option<u64>,
    pub created_unix_ns: Option<i64>,
    /// `round(received / 1e6 - bt * 1000)`: Python's float, half to even.
    pub latency_ms: Option<i64>,
    pub segment_written_unix: Option<u64>,
    /// `(written - bt) * 1000`, integers.
    pub segment_lag_ms: Option<i64>,
    /// `round(d_at(t, group) * ms_per_slot(t))`, half to even.
    pub d_ms: i64,
}

impl Latency {
    /// None (the seven columns NULL) without an arrival, without the slot's block time, or with neither a receive nor a
    /// written time (engine.py:658-659); else the columns, d the group's delay in slots at the trigger's slot and ms that
    /// slot's ms per slot.
    pub fn of(arrival: Option<&Arrival>, block_time: Option<i64>, d: u64, ms: f64) -> Option<Latency> {
        let (a, bt) = (arrival?, block_time?);
        if a.received.is_none() && a.written.is_none() {
            return None;
        }
        let int = |x: i128| i64::try_from(x).unwrap_or(if x < 0 { i64::MIN } else { i64::MAX });
        Some(Latency {
            block_time: bt,
            received_unix_ns: a.received,
            created_unix_ns: a.created,
            // Python's int / 1e6 is the int rounded to a float (half to even, as `as f64`), then divided
            latency_ms: a.received.map(|r| (r as f64 / 1e6 - (i128::from(bt) * 1000) as f64).round_ties_even() as i64),
            segment_written_unix: a.written,
            segment_lag_ms: a.written.map(|w| int((i128::from(w) - i128::from(bt)) * 1000)),
            d_ms: (d as f64 * ms).round_ties_even() as i64,
        })
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

/// One signals row (journal.py:17-30) as far as the stream decides it. `lat` is the seven live-only columns (block_time,
/// received_unix_ns, created_unix_ns, latency_ms, segment_written_unix, segment_lag_ms, d_ms), all NULL when None (a
/// replay: no arrivals).
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
    /// Live (M3-11): the trigger's `Latency` for the row's group (an N2 row carries its F7 trigger's).
    pub lat: Option<Latency>,
}

impl SignalRow {
    /// 'skipped' for a gated row; NULL (undecided until C2/C3) for one that passed the gates. This is C1b's row; with
    /// hooks the journal's status is the `Status` emitted with it.
    pub fn status(&self) -> Option<&'static str> {
        self.gate.reason().map(|_| "skipped")
    }

    pub fn reason(&self) -> Option<&'static str> {
        self.gate.reason()
    }
}

/// A row's status and reason as the journal writes them (engine.py `_signal`, :757-763). Here only the farm gates are
/// decided; past them, `Hooks::on_pass_row` decides (C2's busy and Pending, C3's risk).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Status {
    /// Past the farm gates with nothing deciding it: NULL and NULL (C1b, spec D10).
    Undecided,
    /// 'entered', reason NULL: busy 'open' and a Pending (engine.py:759-763).
    Entered,
    /// 'skipped' and its reason: a farm gate's, 'busy', or the risk gate's (engine.py:737-746, written at :758); owned
    /// where the reason is built at run time (C3's risk file errors, risk.py:35-48).
    Skipped(Cow<'static, str>),
}

impl Status {
    /// A row's status before any hook: 'skipped' with its gate, or Undecided past the gates (as `SignalRow::status` and
    /// `reason`).
    pub fn of(gate: Gate) -> Status {
        gate.reason().map_or(Status::Undecided, |r| Status::Skipped(Cow::Borrowed(r)))
    }

    pub fn status(&self) -> Option<&'static str> {
        match self {
            Status::Undecided => None,
            Status::Entered => Some("entered"),
            Status::Skipped(_) => Some("skipped"),
        }
    }

    pub fn reason(&self) -> Option<&str> {
        match self {
            Status::Skipped(r) => Some(r),
            Status::Undecided | Status::Entered => None,
        }
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

    /// The slot's legs of (venue, key): `by_key.get((venue, key))` of an entry (engine.py:387) and step 5's
    /// `("pool", pool) not in by_key` (:437).
    pub fn get(&self, venue: Venue, key: Id) -> Option<&[Leg]> {
        self.index.get(&(venue, key)).map(|&i| self.keys[i].2.as_slice())
    }

    pub fn clear(&mut self) {
        self.keys.clear();
        self.index.clear();
    }

    /// (venue, key) entries.
    pub fn len(&self) -> usize {
        self.keys.len()
    }

    /// The memory log (C4-2): the slot's (venue, key) entries, cleared at every slot.
    pub fn mem(&self) -> Mem {
        Mem::new("slot_legs", self.keys.len(), mem::vec(&self.keys) + mem::map(&self.index) + self.keys.iter().map(|k| mem::vec(&k.2)).sum::<usize>())
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

/// What a slot's end runs besides the signals: C2's positions (research/m1-c2-positionsim-spec-2026-10-19.md §3.2-§3.4,
/// §5.1). Each method is called where engine.py `_end_slot` (:362-444) and `_signal` (:726-763) run that step, and sees
/// the signal state as the engine's is there, so an implementation keeps the engine's order as it is.
///
/// The rows come through `emit` too, so a PaperEngine writes them and its own journal rows to one sink in the engine's
/// write order. Quiet entries (engine.py:310, 353-360) and the end of data (:1289-1308) lie outside a slot: the caller
/// runs them after a `push_with` whose event started a new slot, and after `finish_with`.
pub trait Hooks {
    /// After `_clock` and before step 3 (engine.py:370-391): steps 1, 2 and 2b, the open positions on the slot's keys,
    /// the entries due and the graduation sales. `eng.day` is this slot's (`_clock` keeps the day of a slot without a
    /// block time); `eng.recent` does not hold the slot's legs yet. `_clock`'s new-day and new-minute parts (:455-476:
    /// the day loss reset, the positions put, the risk loaded) are the hook's, from `block_time`.
    fn before_history(&mut self, _eng: &SignalEngine, _slot: u64, _block_time: Option<i64>, _by_key: &SlotLegs) {}

    /// A row past the farm gates, right before it is emitted (engine.py:743-763): busy, the risk gate, and for an entry
    /// busy 'open' and the Pending. The rows before it are emitted with their hooks run, so the second row of an N2 key
    /// that two triggers of a slot picked finds the first one's busy (C2 spec §3.3). The answer is the row's status and
    /// reason; Undecided leaves both NULL.
    fn on_pass_row(&mut self, _eng: &SignalEngine, _row: &SignalRow) -> Status {
        Status::Undecided
    }

    /// The hourly sweep's keep-set (engine.py `_sweep_history`, :677-678): the keys of open and pending positions, and
    /// the pools a graduation waits for with the keys of the positions waiting. Asked during step 4, after the rows of
    /// the key whose hour index rolled the hour and before the next key's (C2 spec §8.2 risk 5), only for keys without
    /// an event since the sweep's bound, in no set order.
    fn keep(&self, _key: Id) -> bool {
        false
    }

    /// Live (M3-11): a flag fix of `pool` reached the farm windows (engine.py:425-428), after the slot's backfills and
    /// before step 5; `eng.day` is the slot's. The paper engine counts it per day, with whether `pool` has an open
    /// position (state farm_flag_fixes).
    fn flag_fixed(&mut self, _eng: &SignalEngine, _pool: Id) {}

    /// Step 5 after the farm windows (engine.py:429-441): time passes for the open positions on keys without an event in
    /// this slot, and the graduations still waiting are sold. (The feed drains at :416-428 are LiveFeed's: `add_history`,
    /// `add_flag_fix`.)
    fn after_farm(&mut self, _eng: &SignalEngine, _slot: u64, _by_key: &SlotLegs) {}

    /// A row and its status, in the engine's write order; the journal's INSERT OR REPLACE is the implementation's.
    fn emit(&mut self, row: SignalRow, status: Status);
}

/// C1b's hooks (`push`, `finish`, `end_slot`; `lab-live signals`): no step around the signals, no key kept through the
/// sweep, nothing decided past the farm gates, and each row to the closure.
pub struct NoHooks<F>(pub F);

impl<F: FnMut(SignalRow)> Hooks for NoHooks<F> {
    fn emit(&mut self, row: SignalRow, _: Status) {
        (self.0)(row)
    }
}

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
    /// Live (M3-08b): the pools with a backfill (engine.py `backfilled`), pruned with pool_last.
    pub backfilled: HashSet<Id>,
    /// Live (M3-08b): backfills given since the last slot end (`add_history`), drained at the next one.
    history: Vec<(Id, Vec<Leg>)>,
    /// Live (M3-11): flag fixes given since the last slot end (`add_flag_fix`), drained at the next one after `history`.
    fixes: Vec<(Id, Order, bool)>,
    /// Live (M3-11): the open slot's arrivals by tx index (engine.py `slot_arrivals`), and the block time of the slot
    /// being ended (`slot_block_time`): the rows' `Latency`.
    arrivals: HashMap<u32, Arrival>,
    slot_bt: Option<i64>,
    pub counts: Counts,
    /// Some: F7 passes and rollovers are recorded here for the caller to drain.
    pub trace: Option<Vec<Trace>>,
    cur: Option<(u64, Option<i64>)>,
    by_key: SlotLegs,
    written: HashSet<(Family, usize, usize, Id)>,
}

impl SignalEngine {
    /// The memory log (C4-2, read-only): the universe (the snapshot's part), then the state: the recent legs, the farm
    /// windows, the hour's N2 candidates, pool_seen and pool_last (24 h after a pool's last trade), the slot's legs and
    /// the slot's written rows.
    pub fn mem_report(&self) -> Vec<Mem> {
        let mut v = Vec::with_capacity(16);
        v.extend(self.universe.mem());
        v.extend(self.recent.mem());
        v.extend(self.farm.mem());
        v.push(self.hours.mem());
        v.push(Mem::new("pool_seen", self.pool_seen.len(), mem::map(&self.pool_seen)));
        v.push(Mem::new("pool_last", self.pool_last.len(), mem::map(&self.pool_last)));
        v.push(Mem::new("backfilled", self.backfilled.len(), mem::set(&self.backfilled)));
        let legs: usize = self.history.iter().map(|(_, l)| l.len()).sum();
        v.push(Mem::new("history_pending", legs, mem::vec(&self.history) + self.history.iter().map(|(_, l)| mem::vec(l)).sum::<usize>()));
        v.push(Mem::new("flag_fixes_pending", self.fixes.len(), mem::vec(&self.fixes)));
        v.push(Mem::new("slot_arrivals", self.arrivals.len(), mem::map(&self.arrivals)));
        v.push(self.by_key.mem());
        v.push(Mem::new("slot_written", self.written.len(), mem::set(&self.written)));
        v
    }
}

impl SignalEngine {
    /// Refuses: not exactly one primary group, a group or variant name used twice (engine.py:237-239).
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
            backfilled: HashSet::new(),
            history: Vec::new(),
            fixes: Vec::new(),
            arrivals: HashMap::new(),
            slot_bt: None,
            counts: Counts::default(),
            trace: None,
            cur: None,
            by_key: SlotLegs::default(),
            written: HashSet::new(),
        })
    }

    /// One event of the feed, in chain order (engine.py `run`, :301-316): an event of a new slot first ends the slot
    /// before it. The slot's block time is its first event's. C1b's, with no hooks.
    pub fn push(&mut self, ev: &Event, keys: &mut impl N2Keys, emit: &mut impl FnMut(SignalRow)) -> Result<(), SignalError> {
        self.push_with(ev, keys, &mut NoHooks(emit))
    }

    /// `push` with the hooks. After a push whose event started a new slot, the slot before it has ended: the caller's
    /// quiet entries go there (engine.py:310), before the next push.
    pub fn push_with(&mut self, ev: &Event, keys: &mut impl N2Keys, hooks: &mut impl Hooks) -> Result<(), SignalError> {
        self.push_arrived(ev, None, keys, hooks)
    }

    /// Live (M3-11): `push_with` and the event's arrival, kept for its slot's rows by tx index (engine.py:313-314: the
    /// slot's last arrival of a transaction counts; an event without one keeps the one before).
    pub fn push_arrived(&mut self, ev: &Event, arrival: Option<&Arrival>, keys: &mut impl N2Keys, hooks: &mut impl Hooks)
                        -> Result<(), SignalError> {
        let slot = ev.leg.slot();
        if self.cur.map(|c| c.0) != Some(slot) {
            self.flush(keys, hooks)?;
            self.cur = Some((slot, ev.block_time));
        }
        if let Some(a) = arrival {
            self.arrivals.insert(ev.leg.order().tx, *a);
        }
        self.by_key.push(ev.venue, ev.key, ev.leg);
        Ok(())
    }

    /// The end of the feed: its last slot runs too (engine.py:321-323).
    pub fn finish(&mut self, keys: &mut impl N2Keys, emit: &mut impl FnMut(SignalRow)) -> Result<(), SignalError> {
        self.finish_with(keys, &mut NoHooks(emit))
    }

    /// `finish` with the hooks; the end of data (engine.py:1289-1308) is the caller's after it.
    pub fn finish_with(&mut self, keys: &mut impl N2Keys, hooks: &mut impl Hooks) -> Result<(), SignalError> {
        self.flush(keys, hooks)
    }

    /// Live (M3-08): the open slot ends now, as the next slot's first event would end it (`PaperEngine::close_through`,
    /// when the source knows the slot is complete). The slot it ended, if any.
    pub fn close_with(&mut self, keys: &mut impl N2Keys, hooks: &mut impl Hooks) -> Result<Option<u64>, SignalError> {
        let slot = self.open_slot();
        self.flush(keys, hooks)?;
        Ok(slot)
    }

    /// The slot whose events are being collected (it ends with the next slot's first event, `finish` or `close_with`).
    pub fn open_slot(&self) -> Option<u64> {
        self.cur.map(|c| c.0)
    }

    /// Live (M3-08b D1): a backfill's trades of `pool` (LiveFeed `drain_history`: earlier transactions of a pool the
    /// recorder started to follow), history only, never events. Queued, and taken at the next slot end after the
    /// recorded legs' farm windows (engine.py:416-421), as the engine drains them; legs in chain order.
    pub fn add_history(&mut self, pool: Id, legs: Vec<Leg>) {
        self.history.push((pool, legs));
    }

    /// Live (M3-11): a recorded trade of `pool` whose farmer flag a late backfill computed again (LiveFeed
    /// `drain_flag_fixes`, M3-10). Queued like `add_history`, and taken at the next slot end after its backfills
    /// (engine.py:422-428): `FarmWindow::fix`, then `Hooks::flag_fixed`.
    pub fn add_flag_fix(&mut self, pool: Id, order: Order, farmer: bool) {
        self.fixes.push((pool, order, farmer));
    }

    fn flush(&mut self, keys: &mut impl N2Keys, hooks: &mut impl Hooks) -> Result<(), SignalError> {
        let Some((slot, bt)) = self.cur.take() else { return Ok(()) };
        let by_key = std::mem::take(&mut self.by_key);
        let done = self.end_slot_with(slot, bt, &by_key, keys, hooks);
        self.by_key = by_key;
        self.by_key.clear();
        self.arrivals.clear();
        done
    }

    /// One slot with at least one event (engine.py `_end_slot`, its signal parts), with no hooks.
    pub fn end_slot(
        &mut self,
        slot: u64,
        block_time: Option<i64>,
        by_key: &SlotLegs,
        keys: &mut impl N2Keys,
        emit: &mut impl FnMut(SignalRow),
    ) -> Result<(), SignalError> {
        self.end_slot_with(slot, block_time, by_key, keys, &mut NoHooks(emit))
    }

    /// One slot with at least one event (engine.py `_end_slot`, :362-444): `_clock`'s day, the hooks' steps 1-2b, the
    /// history (3), the signals with the hooks' busy and Pending (4), the farm windows (4b), live the backfills (4c) and
    /// flag fixes (4d), the hooks' step 5. The rows' arrivals are the open slot's (`push_arrived`; none here).
    pub fn end_slot_with(
        &mut self,
        slot: u64,
        block_time: Option<i64>,
        by_key: &SlotLegs,
        keys: &mut impl N2Keys,
        hooks: &mut impl Hooks,
    ) -> Result<(), SignalError> {
        self.slot_bt = block_time;
        if self.first_slot.is_none() {
            self.first_slot = Some(slot);
        }
        if let Some(bt) = block_time {
            self.day = day_of(bt); // `_clock`: a slot without a block time keeps the day
        }
        self.counts.slots += 1;
        self.counts.events += by_key.events() as u64;
        self.counts.windows.entry(slot / EPOCH_SLOTS).or_insert_with(|| self.clock.windows(slot));
        // 1, 2, 2b. the open positions, the entries due and the graduation sales (engine.py:373-391), on the history
        // before this slot.
        hooks.before_history(self, slot, block_time, by_key);
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
            self.f7(venue, key, legs, slot, keys, hooks)?;
            self.hour_index(venue, key, legs, slot, &*hooks);
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
        // 4c. live: the backfills given since the last slot end, as history (engine.py:416-421: `_see_pool(backfill=
        // True)`, then `_farm_add`): the pool seen from its legs' first slot to their last and marked backfilled, any
        // pool; their trades into the farm window of a known SOL market (FarmWindow::add skips a trade already there).
        for (pool, legs) in self.history.drain(..) {
            let (Some(lo), Some(hi)) = (legs.iter().map(Leg::slot).min(), legs.iter().map(Leg::slot).max()) else { continue };
            let seen = self.pool_seen.entry(pool).or_insert(lo);
            *seen = (*seen).min(lo);
            let last = self.pool_last.entry(pool).or_insert(hi);
            *last = (*last).max(hi);
            self.backfilled.insert(pool);
            if let Some(p) = self.universe.pool(pool) {
                self.farm.add(pool, p.reversed, &legs, &self.clock);
            }
        }
        // 4d. live: the flag fixes given since the last slot end (engine.py:422-428), after the backfills: each into the
        // farm windows (`_farm_fix`), then counted by the hooks (the day count, not in a restart's replay: M3-13).
        for (pool, order, farmer) in std::mem::take(&mut self.fixes) {
            self.farm.fix(pool, order, farmer);
            hooks.flag_fixed(self, pool);
        }
        // 5. time passes for the open positions on keys without an event here, and the graduations waiting
        // (engine.py:429-441).
        hooks.after_farm(self, slot, by_key);
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
        hooks: &mut impl Hooks,
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
        // engine.py:642-648: per group its trigger's latency, its F7 rows, then per N2 pick its rows (depth, farm and
        // latency the trigger's, no trader).
        let trig = Trig { t, seg: k.seg, depth, farm };
        let arrival = self.arrivals.get(&leg.order().tx);
        let mut rows = Vec::new();
        for (gi, g) in self.groups.iter().enumerate().filter(|(_, g)| g.trades(k.seg)) {
            let at = leg.slot();
            let lat = Latency::of(arrival, self.slot_bt, g.d_at(at, &self.clock), self.clock.ms_per_slot(at));
            for (vi, v) in g.variants.iter().enumerate() {
                rows.push(((Family::F7, gi, vi, key), self.row(Family::F7, (gi, v), key, leg.trader(), &trig, lat)));
            }
            for m in self.hours.pick(k.seg, key, t, depth, g.n2_k, keys) {
                for (vi, v) in g.variants.iter().enumerate() {
                    rows.push(((Family::N2F7, gi, vi, m), self.row(Family::N2F7, (gi, v), m, NONE, &trig, lat)));
                }
            }
        }
        // engine.py:743-763 past the gates, row by row: the hooks decide each row before it is written and before the
        // next row is decided (`_n2` between two `_signal`s reads only the hour index, so the picks may come first).
        for (id, row) in rows {
            self.counts.overwrites += u64::from(!self.written.insert(id));
            self.counts.rows[row.family as usize][row.gate as usize] += 1;
            let status = if row.gate == Gate::Pass { hooks.on_pass_row(self, &row) } else { Status::of(row.gate) };
            hooks.emit(row, status);
        }
        Ok(())
    }

    /// engine.py `_signal` up to the farm gates, for a group's variant; pool_seen_slot and farm_window_complete for the
    /// row's own key.
    fn row(&self, family: Family, (group, variant): (usize, &str), key: Id, trader: Id, trig: &Trig, lat: Option<Latency>) -> SignalRow {
        let Trig { t, seg, depth, farm } = *trig;
        let pool = seg.venue() == Venue::Pool;
        let farm_complete = self.farm_complete_from.map(|c| u8::from(t >= c));
        let window = pool.then(|| self.window_complete(key, t));
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
            lat,
        }
    }

    /// engine.py `_farm_window_complete` (:799-813) for a pool row's own key at t, by the feed's rule.
    pub(crate) fn window_complete(&self, key: Id, t: u64) -> u8 {
        match self.window_rule {
            WindowRule::AllPools => WindowRule::all_pools(self.first_slot, t, &self.clock),
            WindowRule::Live => live_complete(self.pool_seen.get(&key).copied(), self.backfilled.contains(&key),
                                              self.universe.pool(key).and_then(|p| p.create_slot), t, &self.clock),
        }
    }

    /// engine.py `_hour_index`: a new hour first (its prunes with n at this slot), then the key's N2 candidacy with its
    /// last leg's depth.
    fn hour_index(&mut self, venue: Venue, key: Id, legs: &[Leg], slot: u64, hooks: &impl Hooks) {
        if let Some(h) = self.hours.roll(slot, &self.clock) {
            self.counts.rollovers += 1;
            if let Some(tr) = self.trace.as_mut() {
                tr.push(Trace::Hour { slot, venue, key });
            }
            self.farm.rollover(slot, &self.clock); // and the waiting flag fixes (live, engine.py:694-695)
            // The hooks keep the keys with an open, pending or graduating position.
            self.recent.sweep(slot.saturating_sub(HISTORY_SLOTS.max(h)), |k| hooks.keep(k));
            // pool_seen of pools without a trade for 24 h goes, and their backfill mark (live); the test reads pool_last
            // (engine.py:697-701).
            let old = slot.saturating_sub(self.clock.slots(POOL_SEEN_KEEP_MS, slot));
            let (seen, backfilled) = (&mut self.pool_seen, &mut self.backfilled);
            self.pool_last.retain(|p, last| {
                let keep = *last >= old;
                if !keep {
                    seen.remove(p);
                    backfilled.remove(p);
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
mod hooks;

#[cfg(test)]
mod tests {
    use super::cases::groups;
    use super::*;
    use crate::clock::tests::dev2;
    use crate::clock::{Bounds, HOUR_MS, SOL};
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
                pool_seen_slot: None, farm_window_complete: None, gate: Gate::Pass, lat: None,
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
        u.add_pool(60, PoolMeta { reversed: false, mayhem: false, mint_tok: Some(61), coin_creator: 62, create_slot: None });
        u.add_pool(70, PoolMeta { reversed: false, mayhem: false, mint_tok: Some(71), coin_creator: 72, create_slot: None });
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
            lat: None,
        };
        assert_eq!((row(Gate::Pass).status(), row(Gate::FarmFlagged).status()), (None, Some("skipped")));
        assert_eq!((Family::N2F7.as_str(), Farm::NotApplicable.as_str()), ("N2_F7", "n/a"));
    }

    #[test]
    fn farm_complete_follows_the_live_seed() {
        // Live only (farm_seed's complete_from): NULL in a replay, else 1 from complete_from on.
        let mut u = dev2_universe();
        u.add_pool(1, PoolMeta { reversed: true, mayhem: false, mint_tok: None, coin_creator: 9, create_slot: None });
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
    fn the_latency_columns_mirror_pythons_floats_and_none_cases() {
        // engine.py `_latency` (:650-665), values from Python 3: round(r / 1e6 - bt * 1000) half to even on floats.
        let bt = 1_790_000_000i64;
        let r = |extra: u64| Some(bt as u64 * 1_000_000_000 + extra);
        let a = |received, created, written| Arrival { received, created, written };
        let lat = |x: &Arrival| Latency::of(Some(x), Some(bt), 2, 267.83).unwrap();
        for (extra, ms) in [(2_500_000, 2), (3_500_000, 4), (1_234_500_000, 1_234), (400_000_001, 400), (999_499_999, 1_000)] {
            assert_eq!(lat(&a(r(extra), None, None)).latency_ms, Some(ms), "{extra}"); // 999.499999 is 999.5 as a float
        }
        let full = lat(&a(r(7), Some(-3), Some(bt as u64 + 2)));
        assert_eq!(full, Latency { block_time: bt, received_unix_ns: r(7), created_unix_ns: Some(-3), latency_ms: Some(0),
                                   segment_written_unix: Some(bt as u64 + 2), segment_lag_ms: Some(2_000), d_ms: 536 });
        // Without received: no latency_ms; without written: no lag. Written before the block time: a negative lag.
        let w = lat(&a(None, Some(5), Some(bt as u64 - 1)));
        assert_eq!((w.latency_ms, w.segment_lag_ms, w.created_unix_ns), (None, Some(-1_000), Some(5)));
        assert_eq!(lat(&a(r(0), None, None)).segment_lag_ms, None);
        // d_ms: round(d * ms), half to even: 2 * 100.25 = 200.5 -> 200, 6 * 100.25 = 601.5 -> 602; 3 * 267.83 -> 803.
        let d = |d, ms| Latency::of(Some(&a(r(0), None, None)), Some(bt), d, ms).unwrap().d_ms;
        assert_eq!((d(2, 100.25), d(6, 100.25), d(3, 267.83)), (200, 602, 803));
        // The three None cases: no arrival, no block time, neither received nor written (created alone).
        assert_eq!(Latency::of(None, Some(bt), 2, 267.83), None);
        assert_eq!(Latency::of(Some(&a(r(0), None, None)), None, 2, 267.83), None);
        assert_eq!(Latency::of(Some(&a(None, Some(5), None)), Some(bt), 2, 267.83), None);
    }

    /// farm_complete_follows_the_live_seed's pool: a reversed pool's inflow at t - 10 and its trigger at t (tx 0).
    fn latency_engine(ms: f64) -> (SignalEngine, [Leg; 2]) {
        let mut u = dev2_universe();
        u.add_pool(1, PoolMeta { reversed: true, mayhem: false, mint_tok: None, coin_creator: 9, create_slot: None });
        let s = SOL as i64;
        let t = 450_200_000;
        let legs = [pool((t - 10, 0), 1, 3, PoolKind::Sell, (20 * s, 0), (300 * s, 0)),
                    pool((t, 0), 1, 4, PoolKind::Buy, (15 * s, 0), (320 * s, 0))];
        let mut g = groups(0);
        (g[1].d, g[1].d_ms) = (2, None); // H: d 2 slots, K3: d_ms 600
        let mut eng = SignalEngine::new(u, dev2(), g, WindowRule::AllPools).unwrap();
        eng.clock.per_epoch.insert(t / EPOCH_SLOTS, ms);
        (eng, legs)
    }

    #[test]
    fn a_rows_latency_is_its_trigger_transactions_arrival_in_its_group() {
        // M3-11 spec §3: the slot's arrivals by (slot, tx) and its first event's block time; d_ms per group (K3 600 ms is
        // 6 slots at 100.25 ms: 601.5 -> 602; H's d 2: 200.5 -> 200). The trigger's tx has two events (a deposit after
        // the trigger): the later one without an arrival keeps the earlier one's (engine.py:313-314).
        let bt = 1_790_000_000i64;
        let a = Arrival { received: Some(bt as u64 * 1_000_000_000 + 2_500_000), created: Some(1), written: Some(bt as u64 + 1) };
        let other = Arrival { received: Some(1), created: None, written: None };
        let run = |first_bt: Option<i64>, arrivals: [Option<&Arrival>; 3]| {
            let (mut eng, [inflow, trig]) = latency_engine(100.25);
            let Leg::Pool(mut again) = pool((trig.slot(), 0), 1, 4, PoolKind::Deposit, (1, 1), (320 * SOL as i64, 0)) else { unreachable!() };
            again.order.inner = 1;
            let mut keys = FakeKeys::default();
            let mut rows = Vec::new();
            let evs = [(inflow, Some(bt - 4)), (trig, first_bt), (Leg::Pool(again), Some(bt))];
            for ((l, b), arr) in evs.into_iter().zip(arrivals) {
                eng.push_arrived(&Event::new(l, b), arr, &mut keys, &mut NoHooks(|r| rows.push(r))).unwrap();
            }
            eng.finish(&mut keys, &mut |r| rows.push(r)).unwrap();
            rows.iter().map(|r| (r.variant.clone(), r.lat)).collect::<Vec<_>>()
        };
        let rows = run(Some(bt), [Some(&other), Some(&a), None]);
        let lat = |d_ms| Some(Latency { block_time: bt, received_unix_ns: a.received, created_unix_ns: Some(1), latency_ms: Some(2),
                                        segment_written_unix: a.written, segment_lag_ms: Some(1_000), d_ms });
        assert_eq!(rows, [("tp4_T600".into(), lat(602)), ("flow300_H6".into(), lat(200)), ("flow300_tp100_H6".into(), lat(200))]);
        // The slot's block time is its first event's: None there, all seven NULL. No arrival for the trigger's tx (tx 0 of
        // the inflow's slot had one: the arrivals are the slot's). And without arrivals at all (a replay's push): NULL.
        for (first_bt, arrivals) in [(None, [Some(&other), Some(&a), None]), (Some(bt), [Some(&a), None, None]), (Some(bt), [None; 3])] {
            let rows = run(first_bt, arrivals);
            assert!(rows.len() == 3 && rows.iter().all(|r| r.1.is_none()), "{first_bt:?} {rows:?}");
        }
    }

    #[test]
    fn flag_fixes_reach_the_farm_windows_at_the_next_slot_end_after_the_backfills() {
        // M3-11 (engine.py:416-428): given between two pushes, a fix waits for the slot end; there it comes after the
        // slot's own farm adds and the backfills, and before step 5. P's recorded inflow at t - 10 is organic; a fix makes
        // it a farmer's, so P's trigger at t is flagged and group H (organic) skips it.
        struct Seen(Vec<String>);
        impl Hooks for Seen {
            fn flag_fixed(&mut self, eng: &SignalEngine, pool: Id) {
                let items = eng.farm.items(pool).map_or(0, <[_]>::len);
                self.0.push(format!("fix {pool}: backfilled {}, items {items}", eng.backfilled.contains(&pool)));
            }
            fn after_farm(&mut self, _: &SignalEngine, slot: u64, _: &SlotLegs) {
                self.0.push(format!("step 5 {slot}"));
            }
            fn emit(&mut self, row: SignalRow, _: Status) {
                self.0.push(format!("{} {:?}", row.variant, row.gate));
            }
        }
        let (mut eng, [inflow, trig]) = latency_engine(267.83);
        let t = trig.slot();
        let mut keys = FakeKeys::default();
        let mut seen = Seen(Vec::new());
        let ev = |l: Leg| Event::new(l, Some(1_790_000_000));
        eng.push_with(&ev(inflow), &mut keys, &mut seen).unwrap();
        eng.add_flag_fix(1, inflow.order(), true);
        eng.add_history(1, vec![pool((t - 500, 0), 1, 3, PoolKind::Sell, (SOL as i64, 0), (280 * SOL as i64, 0))]);
        assert_eq!(eng.mem_report().iter().find(|m| m.name == "flag_fixes_pending").map(|m| m.entries), Some(1));
        eng.push_with(&ev(trig), &mut keys, &mut seen).unwrap();
        eng.finish_with(&mut keys, &mut seen).unwrap();
        assert_eq!(seen.0, ["fix 1: backfilled true, items 2".into(), format!("step 5 {}", t - 10), "tp4_T600 Pass".into(),
                            "flow300_H6 FarmFlagged".into(), "flow300_tp100_H6 FarmFlagged".into(), format!("step 5 {t}")]);
        assert_eq!((eng.farm.items(1).map(|i| i[1].3 > 0), eng.farm.fixes()), (Some(true), 0));
        // The same without the fix: organic, H passes the farm gate (and stops at AllPools' window gate, its first hour).
        let (mut eng, _) = latency_engine(267.83);
        let mut seen = Seen(Vec::new());
        for l in [inflow, trig] {
            eng.push_with(&ev(l), &mut keys, &mut seen).unwrap();
        }
        eng.finish_with(&mut keys, &mut seen).unwrap();
        assert!(seen.0.contains(&"flow300_H6 FarmWindowIncomplete".to_owned()), "{:?}", seen.0);
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
        assert_eq!(new(groups(1), WindowRule::Live), Ok(())); // M3-08b: the live farm-window rule is ported (no longer refused)
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

    #[test]
    fn the_windowed_structures_stay_bounded_over_thirty_hours() {
        // M1 C4-2 (plateau spec §2): one pool buy per slot for 30 hours of dev2's clock, a new pool every 50 slots (each
        // trades its 50 slots, then never again). A structure that kept every pool would hold all of them; each
        // window-bounded one holds at most its window's pools or legs: recent and the farm windows about two hours (the
        // hourly sweep and rollover, plus the hour since), pool_seen and pool_last 25 hours, the hour's N2 candidates one
        // hour, the slot's legs and written rows one slot.
        let start = 450_100_000u64;
        let h = dev2().slots(HOUR_MS, start);
        let n = 30 * h;
        let mut u = dev2_universe();
        let pools = (n / 50 + 1) as Id;
        for i in 0..pools {
            u.add_pool(1000 + i, PoolMeta { reversed: false, mayhem: false, mint_tok: None, coin_creator: 5, create_slot: None });
        }
        let mut eng = engine(u, 0);
        let mut keys = FakeKeys::default();
        let s = SOL as i64;
        let entries = |eng: &SignalEngine, name: &str| eng.mem_report().iter().find(|m| m.name == name).map(|m| m.entries).unwrap();
        let mut peak: BTreeMap<&str, usize> = BTreeMap::new();
        for k in 0..n {
            let slot = start + k;
            let l = pool((slot, 0), 1000 + (k / 50) as Id, 3, PoolKind::Buy, (0, s / 10), (0, 500 * s));
            let bt = 1_790_000_000 + (slot as i64 - 450_000_000) * 2 / 5;
            eng.push(&Event::new(l, Some(bt)), &mut keys, &mut |_| {}).unwrap();
            if k % 500 != 0 {
                continue;
            }
            let window = |hours: u64| (hours * h / 50 + 2) as usize; // pools that traded within so many hours
            for (name, bound) in [("recent_keys", window(2)), ("recent_legs", (2 * h + 100) as usize), ("farm_pools", window(2)),
                                  ("farm_items", (2 * h + 100) as usize), ("pool_seen", window(25)), ("pool_last", window(25)),
                                  ("hour_events", (h + 1) as usize), ("slot_legs", 1), ("slot_written", 0)] {
                let e = entries(&eng, name);
                assert!(e <= bound, "slot {slot}: {name} {e} above its bound {bound}");
                let p = peak.entry(name).or_default();
                *p = (*p).max(e);
            }
        }
        // The run saw every pool; the bounded structures did not keep them.
        eng.finish(&mut keys, &mut |_| {}).unwrap();
        assert_eq!(eng.counts.events, n);
        assert!(entries(&eng, "pool_seen") < (pools as usize) * 5 / 6 && peak["recent_keys"] < (pools as usize) / 10, "{peak:?}");
        assert!(peak["farm_items"] > 0 && peak["hour_events"] > 0, "the structures were used: {peak:?}");
    }
}
