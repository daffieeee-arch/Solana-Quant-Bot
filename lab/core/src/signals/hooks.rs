//! The hooks (C2-4a; research/m1-c2-positionsim-spec-2026-10-19.md §3.2-§3.4, §5.1): each is called where engine.py
//! `_end_slot` (:362-444) and `_signal` (:726-763) run its step, on the state the engine has there, and they change no
//! signal row and no signal state. A recording implementation logs the calls; small ones decide statuses and busy.
//!
//! The feed (dev2, epoch 1042, hours of 13441 slots from H0 = 450152531; reversed pools, no farmers): Q and X trade once
//! early; R triggers at H0 + 100 (K3 past the gates, H's farm window incomplete); P, F and G trigger at S = H0 + 13451,
//! the next hour's first slot and the first of 2026-09-22 (UTC). P's N2 pick is Q, from the old hour (as x1 of
//! cases.rs); then P's hour index rolls the hour and sweeps the keys without an event since S - 13441, Q and X. F and G
//! both pick P, from the new hour.

use super::cases::groups;
use super::*;
use crate::clock::tests::dev2;
use crate::clock::{Bounds, SOL};
use crate::f7::tests::{curve, pool, MAYHEM};
use crate::legs::PoolKind;
use crate::n2::tests::FakeKeys;
use crate::universe::PoolMeta;
use std::cell::RefCell;

const H0: u64 = 450_152_531;
const S: u64 = H0 + 13_441 + 10;
/// 2026-09-22 00:00 UTC.
const MIDNIGHT: i64 = 1_790_035_200;
const P: Id = 1;
const Q: Id = 2;
const R: Id = 3;
const X: Id = 4;
const F: Id = 5;
const G: Id = 6;

fn feed() -> (SignalEngine, Vec<Event>) {
    let mut u = Universe::new(Bounds::new((446_256_000, 452_304_000), &dev2()), MAYHEM);
    for p in [P, Q, R, X, F, G] {
        u.add_pool(p, PoolMeta { reversed: true, mayhem: false, mint_tok: None, coin_creator: 9, create_slot: None });
    }
    let s = SOL as i64;
    // A reversed pool's SOL inflow (a WSOL sell: a token buy) and its trigger (a WSOL buy of `a` at depth `b`), in SOL.
    let inflow = |slot, p, b| pool((slot, 0), p, 20, PoolKind::Sell, (20 * s, 0), (b * s, 0));
    let trig = |(slot, tx), p, a, b| pool((slot, tx), p, 21, PoolKind::Buy, (a * s, 0), (b * s, 0));
    let legs = [
        inflow(H0 + 5, Q, 300),
        inflow(H0 + 6, X, 1_000), // in no N2 band
        inflow(H0 + 90, R, 580),
        trig((H0 + 100, 0), R, 30, 600), // N2 band 480-720 SOL: no candidate
        inflow(S - 15, P, 330),
        inflow(S - 14, F, 200),
        inflow(S - 13, G, 200),
        trig((S, 0), P, 15, 320), // 256-384 SOL: Q
        trig((S, 1), F, 15, 330), // 264-396 SOL: P
        trig((S, 2), G, 15, 270), // 216-324 SOL: P, not F
    ];
    // 0.4 s a slot, S on the midnight.
    let evs = legs.iter().map(|&l| Event::new(l, Some(MIDNIGHT + (l.slot() as i64 - S as i64) * 2 / 5))).collect();
    (SignalEngine::new(u, dev2(), groups(1), WindowRule::AllPools).unwrap(), evs)
}

/// One candidate per pick: only the seeds are read.
fn keys() -> FakeKeys {
    FakeKeys { names: vec![""; 7], seeds: vec![0; 7], triggers: [((P, S), 0), ((F, S), 0), ((G, S), 0)].into_iter().collect() }
}

/// The feed through push_with and finish_with.
fn run(hooks: &mut impl Hooks) -> SignalEngine {
    let (mut eng, evs) = feed();
    let mut k = keys();
    for ev in &evs {
        eng.push_with(ev, &mut k, hooks).unwrap();
    }
    eng.finish_with(&mut k, hooks).unwrap();
    eng
}

/// The feed through C1b's push and finish.
fn run_c1b() -> (SignalEngine, Vec<SignalRow>) {
    let (mut eng, evs) = feed();
    let (mut k, mut rows) = (keys(), Vec::new());
    for ev in &evs {
        eng.push(ev, &mut k, &mut |r| rows.push(r)).unwrap();
    }
    eng.finish(&mut k, &mut |r| rows.push(r)).unwrap();
    (eng, rows)
}

/// A hook call as the recorder saw it.
#[derive(Clone, Debug, PartialEq, Eq)]
enum Call {
    /// The slot, eng.day, and how many of the slot's keys step 3 (last_seen) and 4b (the farm window) hold the slot of.
    Before(u64, Option<String>, usize, usize),
    /// The row asked about, and the slot of its key's last leg (`Recent::back`).
    Pass(Family, String, Id, Option<u64>),
    Emit(Family, String, Id, Status),
    Keep(Id),
    After(u64, usize, usize),
}

/// Hooks that log every call, enter every row past the gates and keep the keys they entered.
#[derive(Default)]
struct Recorder {
    log: RefCell<Vec<Call>>,
    entered: HashSet<Id>,
    rows: Vec<SignalRow>,
}

fn taken(eng: &SignalEngine, slot: u64, by_key: &SlotLegs) -> (usize, usize) {
    let hist = by_key.iter().filter(|&(_, k, _)| eng.recent.last_seen(k) == Some(slot)).count();
    let farm = by_key.iter().filter(|&(_, k, _)| eng.farm.items(k).is_some_and(|xs| xs.iter().any(|x| x.0 == slot))).count();
    (hist, farm)
}

impl Hooks for Recorder {
    fn before_history(&mut self, eng: &SignalEngine, slot: u64, _: Option<i64>, by_key: &SlotLegs) {
        let (h, f) = taken(eng, slot, by_key);
        self.log.get_mut().push(Call::Before(slot, eng.day.clone(), h, f));
    }

    fn on_pass_row(&mut self, eng: &SignalEngine, row: &SignalRow) -> Status {
        self.log.get_mut().push(Call::Pass(row.family, row.variant.clone(), row.key, eng.recent.back(row.key).map(Leg::slot)));
        self.entered.insert(row.key);
        Status::Entered
    }

    fn keep(&self, key: Id) -> bool {
        self.log.borrow_mut().push(Call::Keep(key));
        self.entered.contains(&key)
    }

    fn after_farm(&mut self, eng: &SignalEngine, slot: u64, by_key: &SlotLegs) {
        let (h, f) = taken(eng, slot, by_key);
        self.log.get_mut().push(Call::After(slot, h, f));
    }

    fn emit(&mut self, row: SignalRow, status: Status) {
        self.log.get_mut().push(Call::Emit(row.family, row.variant.clone(), row.key, status));
        self.rows.push(row);
    }
}

#[test]
fn the_hooks_run_where_the_engine_runs_each_step() {
    let mut rec = Recorder::default();
    let eng = run(&mut rec);
    let mut log = rec.log.into_inner();
    let k = log.iter().position(|c| matches!(c, Call::Keep(_))).unwrap();
    log[k..k + 2].sort_by_key(|c| if let Call::Keep(key) = c { *key } else { 0 }); // the sweep asks in HashMap order
    let (d1, d2) = (Some("2026-09-21".to_owned()), Some("2026-09-22".to_owned()));
    // Per slot: steps 1-2b before the history and the farm windows hold the slot, step 5 after both.
    let quiet = |slot| [Call::Before(slot, d1.clone(), 0, 0), Call::After(slot, 1, 1)];
    // A row past the gates is decided, after step 3, right before it is written.
    let pass = |fam, v: &str, key, back| [Call::Pass(fam, v.into(), key, Some(back)), Call::Emit(fam, v.into(), key, Status::Entered)];
    let gated = |v: &str| Call::Emit(Family::F7, v.into(), R, Status::Skipped("farm window incomplete".into()));
    let mut want = Vec::new();
    for slot in [H0 + 5, H0 + 6, H0 + 90] {
        want.extend(quiet(slot));
    }
    want.push(Call::Before(H0 + 100, d1.clone(), 0, 0));
    want.extend(pass(Family::F7, "tp4_T600", R, H0 + 100));
    want.extend([gated("flow300_H6"), gated("flow300_tp100_H6"), Call::After(H0 + 100, 1, 1)]);
    for slot in [S - 15, S - 14, S - 13] {
        want.extend(quiet(slot));
    }
    // S: `_clock`'s new day comes before step 1. P's rows, its N2 row for Q (Q's last leg at H0 + 5); P's hour index
    // rolls the hour and the sweep asks for Q (entered a moment ago: kept) and X. Then F's rows and G's.
    want.push(Call::Before(S, d2, 0, 0));
    let rows = |key, n2| {
        [(Family::F7, "tp4_T600", key, S), (Family::N2F7, "tp4_T600", n2, if n2 == Q { H0 + 5 } else { S }),
         (Family::F7, "flow300_H6", key, S), (Family::F7, "flow300_tp100_H6", key, S)]
    };
    want.extend(rows(P, Q).into_iter().flat_map(|(f, v, key, back)| pass(f, v, key, back)));
    want.extend([Call::Keep(Q), Call::Keep(X)]);
    for (key, n2) in [(F, P), (G, P)] {
        want.extend(rows(key, n2).into_iter().flat_map(|(f, v, key, back)| pass(f, v, key, back)));
    }
    want.push(Call::After(S, 3, 3));
    assert_eq!(log, want);
    // Q's history stays for its pending entry (spec §8.2 risk 5); X's goes.
    assert_eq!((eng.recent.back(Q).map(Leg::slot), eng.recent.back(X)), (Some(H0 + 5), None));
}

#[test]
fn the_hooks_change_no_row_and_no_signal_state() {
    // C1b's push and finish (NoHooks) against push_with with the recorder: the same rows in the same order and the same
    // signal state, but the history the sweep kept for the recorder.
    let (c1b, rows) = run_c1b();
    let mut rec = Recorder::default();
    let eng = run(&mut rec);
    assert_eq!((rows.len(), &rec.rows), (15, &rows));
    assert_eq!(eng.counts, c1b.counts);
    for seg in Segment::ALL {
        assert_eq!(eng.hours.events(seg), c1b.hours.events(seg));
    }
    for p in [P, Q, R, X, F, G] {
        assert_eq!(eng.farm.items(p), c1b.farm.items(p));
    }
    assert_eq!((&eng.pool_seen, &eng.pool_last, &eng.day, eng.first_slot), (&c1b.pool_seen, &c1b.pool_last, &c1b.day, c1b.first_slot));
    assert_eq!((eng.recent.len(), c1b.recent.len(), c1b.recent.back(Q), c1b.recent.back(X)), (5, 4, None, None));
    // C1b's columns: a row's own status and reason are its Status before any hook.
    for r in &rows {
        assert_eq!((Status::of(r.gate).status(), Status::of(r.gate).reason()), (r.status(), r.reason()));
    }
}

/// A row's journal key: (family, variant, key, t).
type RowId = (Family, &'static str, Id, u64);

/// Hooks that answer from a table by the row's key, Undecided elsewhere.
struct Decide {
    table: Vec<(RowId, Status)>,
    asked: usize,
    rows: Vec<(SignalRow, Status)>,
}

impl Decide {
    fn answer(&self, row: &SignalRow) -> Option<Status> {
        let id = (row.family, row.variant.as_str(), row.key, row.t);
        self.table.iter().find(|(k, _)| *k == id).map(|(_, s)| s.clone())
    }
}

impl Hooks for Decide {
    fn on_pass_row(&mut self, _: &SignalEngine, row: &SignalRow) -> Status {
        self.asked += 1;
        self.answer(row).unwrap_or(Status::Undecided)
    }

    fn emit(&mut self, row: SignalRow, status: Status) {
        self.rows.push((row, status));
    }
}

#[test]
fn a_hooks_status_is_the_rows_it_was_asked_about_only() {
    let (_, rows) = run_c1b();
    let risk = Status::Skipped(Cow::Owned(format!("risk config invalid: {}", "x")));
    let table = vec![((Family::N2F7, "tp4_T600", Q, S), Status::Skipped("busy".into())), ((Family::F7, "flow300_H6", P, S), Status::Entered),
                     ((Family::F7, "tp4_T600", G, S), risk.clone()), ((Family::F7, "flow300_H6", R, H0 + 100), Status::Entered)];
    let mut d = Decide { table, asked: 0, rows: Vec::new() };
    run(&mut d);
    assert_eq!(d.rows.iter().map(|r| &r.0).collect::<Vec<_>>(), rows.iter().collect::<Vec<_>>());
    // Every row keeps C1b's status but the three past the gates the table names; R's H row is gated, so never asked.
    let changed: Vec<(Family, &str, Id, &Status)> =
        d.rows.iter().filter(|(r, s)| *s != Status::of(r.gate)).map(|(r, s)| (r.family, r.variant.as_str(), r.key, s)).collect();
    assert_eq!(changed, [(Family::N2F7, "tp4_T600", Q, &Status::Skipped("busy".into())), (Family::F7, "flow300_H6", P, &Status::Entered),
                         (Family::F7, "tp4_T600", G, &risk)]);
    for (r, s) in &d.rows {
        assert_eq!(s, &d.answer(r).filter(|_| r.gate == Gate::Pass).unwrap_or_else(|| Status::of(r.gate)));
    }
    assert_eq!(d.asked, 13); // the rows past the gates: R's K3 row and P's, F's and G's four
    assert_eq!((risk.status(), risk.reason(), Status::Entered.status(), Status::Entered.reason()),
               (Some("skipped"), Some("risk config invalid: x"), Some("entered"), None));
}

/// The engine's busy test alone (engine.py:743-744, 759): an entered key stays 'open' (no exits here).
#[derive(Default)]
struct Busy {
    open: HashSet<(Family, String, Id)>,
    rows: Vec<(SignalRow, Status)>,
}

impl Hooks for Busy {
    fn on_pass_row(&mut self, _: &SignalEngine, row: &SignalRow) -> Status {
        if self.open.insert((row.family, row.variant.clone(), row.key)) {
            Status::Entered
        } else {
            Status::Skipped("busy".into())
        }
    }

    fn emit(&mut self, row: SignalRow, status: Status) {
        self.rows.push((row, status));
    }
}

#[test]
fn a_second_n2_row_in_a_slot_finds_the_first_ones_busy() {
    // F and G both pick P at S. The engine's second `_signal` finds busy 'open' and its row replaces 'entered' with
    // 'skipped busy' (C2 spec §3.3: reproduced, not fixed); the hooks decide each row before the next.
    let mut b = Busy::default();
    run(&mut b);
    let p: Vec<&Status> = b.rows.iter().filter(|(r, _)| (r.family, r.key) == (Family::N2F7, P)).map(|x| &x.1).collect();
    assert_eq!(p, [&Status::Entered, &Status::Skipped("busy".into())]);
    assert_eq!(b.rows.iter().filter(|x| x.1 == Status::Entered).count(), 12);
}

#[test]
fn a_push_that_starts_a_slot_has_ended_the_slot_before() {
    // The caller's quiet entries go there (engine.py:310): the slot before has run step 5, and eng.day is still its day.
    let (mut eng, evs) = feed();
    let (mut k, mut rec) = (keys(), Recorder::default());
    let mut ended = Vec::new();
    for (i, ev) in evs.iter().enumerate() {
        eng.push_with(ev, &mut k, &mut rec).unwrap();
        if i > 0 && evs[i - 1].leg.slot() != ev.leg.slot() {
            let last = rec.log.get_mut().last().cloned();
            assert!(matches!(last, Some(Call::After(s, ..)) if s == evs[i - 1].leg.slot()), "{last:?}");
            ended.push((evs[i - 1].leg.slot(), eng.day.clone()));
        }
    }
    assert_eq!(ended.last(), Some(&(S - 13, Some("2026-09-21".to_owned()))));
    assert_eq!(ended.len(), 7);
    eng.finish_with(&mut k, &mut rec).unwrap();
    assert_eq!(rec.log.get_mut().last(), Some(&Call::After(S, 3, 3)));
}

#[test]
fn slot_legs_by_venue_and_key() {
    // An entry's `by_key.get((venue, key), [])` (engine.py:387) and step 5's `("pool", pool) not in by_key` (:437).
    let mut b = SlotLegs::default();
    let (p, c) = (pool((7, 0), 5, 1, PoolKind::Buy, (0, 0), (0, 0)), curve((7, 1), 5, 1, true, 1, 1, 1));
    b.push(Venue::Pool, 5, p);
    b.push(Venue::Curve, 5, c);
    b.push(Venue::Pool, 5, p);
    assert_eq!((b.get(Venue::Pool, 5), b.get(Venue::Curve, 5)), (Some(&[p, p][..]), Some(&[c][..])));
    assert_eq!((b.get(Venue::Pool, 6), b.get(Venue::Curve, 6)), (None, None));
}
