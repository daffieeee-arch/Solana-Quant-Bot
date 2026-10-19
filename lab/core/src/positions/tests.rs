use std::collections::BTreeMap;

use super::cases::{After, Op, Opened as Want, St, CASES, CLOCKS, L, MAYHEM, RERUNS, SCN};
use super::*;
use crate::clock::Clock;
use crate::costs::{RentMode, Scenario};
use crate::exits::{rule_for, variant};
use crate::flow::LiveFlowIndex;
use crate::legs::{CurveVariant, Order, X0, X1};

fn order((slot, tx, outer, inner): (u64, u32, u16, u16)) -> Order {
    Order { slot, tx, outer, inner }
}

fn leg(l: &L) -> Leg {
    match *l {
        L::C((o, trader, is_buy, (t, q, fee), [vt, vq, rt, rq], [fee_bps, creator_bps], variant, args, direct)) => {
            let set = args.iter().enumerate().fold(0u8, |m, (i, a)| m | (u8::from(a.is_some()) << i));
            Leg::Curve(CurveLeg {
                order: order(o),
                mint: 0,
                trader,
                t,
                q,
                fee,
                vt,
                vq,
                rt,
                rq,
                args: args.map(|a| a.unwrap_or(0)),
                fee_bps,
                creator_bps,
                args_set: set,
                variant,
                is_buy,
                direct,
            })
        }
        L::P((o, trader, kind, exact_out, (base, quote_net, e_delta), [b, e, virt], fees, [x0, x1], direct)) => {
            let flags = [(exact_out, EXACT_OUT), (direct, DIRECT), (x0.is_some(), X0), (x1.is_some(), X1)]
                .iter()
                .fold(0u8, |m, &(on, f)| if on { m | f } else { m });
            Leg::Pool(PoolLeg {
                order: order(o),
                pool: 0,
                trader,
                b,
                e,
                virt,
                fees,
                base,
                quote_net,
                e_delta,
                x: [x0.unwrap_or(0), x1.unwrap_or(0)],
                kind,
                flags,
            })
        }
    }
}

fn scenario() -> Scenario {
    let (name, fee, tip, failed, land, rent, mode, adverse) = SCN;
    Scenario {
        name: name.into(),
        fee_per_tx: fee,
        tip,
        failed_tx: failed,
        land_prob: land,
        ata_rent: rent,
        rent_mode: RentMode::parse(mode).unwrap(),
        adverse_per_side: adverse,
    }
}

fn clock(i: usize) -> Clock {
    Clock::new(CLOCKS[i].1.iter().copied().collect())
}

fn tau(s: &str) -> Tau {
    match s {
        "empirical" => Tau::Empirical,
        "zero" => Tau::Zero,
        "inf" => Tau::Inf,
        _ => panic!("tau {s}"),
    }
}

fn cfg(tol: f64) -> Cfg {
    Cfg { tol, tau: Tau::Empirical, max_hold: MAX_HOLD, window_end: None, mayhem_agent: MAYHEM }
}

fn curve_post(l: &Leg) -> Curve {
    match l {
        Leg::Curve(c) => c.post(),
        Leg::Pool(_) => panic!("a pool leg"),
    }
}

/// What open_curve_entry gets from the engine (spec D4), from a curve's whole history: the state after the decision
/// slot and before the entry slot, the entry slot's legs, and the seed of the legs before it: the first half summed
/// per trader (as the store's curve_hold), the rest leg by leg (as the run's sums).
type Entry = (Option<Curve>, Option<Curve>, usize, usize, Vec<(Id, i128)>);

fn curve_entry(legs: &[Leg], t: u64, s: u64) -> Entry {
    let (a, b) = (legs.partition_point(|l| l.slot() < s), legs.partition_point(|l| l.slot() <= s));
    let s_dec = legs.iter().rev().find(|l| l.slot() <= t).map(curve_post);
    let before = legs[..a].last().map(curve_post);
    let net = |l: &Leg| match l {
        Leg::Curve(c) => (c.trader, if c.is_buy { i128::from(c.t) } else { -i128::from(c.t) }),
        Leg::Pool(_) => panic!("a pool leg"),
    };
    let mut hold: BTreeMap<Id, i128> = BTreeMap::new();
    for l in &legs[..a / 2] {
        let (tr, n) = net(l);
        *hold.entry(tr).or_default() += n;
    }
    let seed = hold.into_iter().chain(legs[a / 2..a].iter().map(net)).collect();
    (s_dec, before, a, b, seed)
}

fn state(s: State) -> St {
    match s {
        State::Curve(c) => St::C([c.vt, c.vq, c.rt, c.rq, c.fee_bps, c.creator_bps]),
        State::Pool(p) => St::P([p.b, p.e, p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps, p.virt]),
    }
}

fn same_state(a: &St, b: &St) -> bool {
    match (a, b) {
        (St::C(x), St::C(y)) => x == y,
        (St::P(x), St::P(y)) => x == y,
        _ => false,
    }
}

type Fields = (u64, u64, i128, i128, i128, Option<u64>, Option<u64>, i128, Option<&'static str>);
type Flags = (bool, bool, u64, bool, bool, bool, u64, u64, u64, u64, u64);
type Marks = (u64, Option<(u64, i128)>, Option<(u64, i128)>);

fn result(r: &PosResult) -> (Fields, Flags, Marks) {
    (
        (r.decision_slot, r.entry_slot, r.size, r.tokens, r.cost, r.exit_decision_slot, r.exit_slot, r.proceeds, r.exit_reason.map(Reason::as_str)),
        (
            r.entry_failed,
            r.skipped,
            r.exit_attempts,
            r.cf_graduation,
            r.seed_pool,
            r.vault_capped,
            r.reverted_direct,
            r.reverted_router,
            r.sells_dropped,
            r.sells_scaled,
            r.mayhem_reverted,
        ),
        (r.marks, r.peak, r.last_mark),
    )
}

fn want_result(w: &super::cases::R) -> (Fields, Flags, Marks) {
    (
        (w.decision_slot, w.entry_slot, w.size, w.tokens, w.cost, w.exit_decision_slot, w.exit_slot, w.proceeds, w.exit_reason),
        (
            w.entry_failed,
            w.skipped,
            w.exit_attempts,
            w.cf_graduation,
            w.seed_pool,
            w.vault_capped,
            w.reverted_direct,
            w.reverted_router,
            w.sells_dropped,
            w.sells_scaled,
            w.mayhem_reverted,
        ),
        (w.marks, w.peak, w.last_mark),
    )
}

fn after(step: Option<Step>, completed: Option<u64>, sim: &PositionSim) -> After {
    let p = sim.probe();
    (step.map(Step::closed), completed, (p.pending, p.pending_min, p.last_slot, p.decided_for, p.deadline_done, p.done))
}

#[test]
fn positions_follow_the_replay_call_by_call() {
    // lab-py backtest.replay (tools/position_cases.py): the entry, then feed_batch or the engine's calls one by one
    // (the return, the completion, pending and the clock after each), the whole Result, the venue state and the
    // holdings at the end. Curve entries come from the engine's view (D4); Python reads the whole history.
    let scn = scenario();
    for case in &CASES {
        let at = case.name;
        let legs: Vec<Leg> = case.legs.iter().map(leg).collect();
        let s = case.t + case.d;
        let cfg = Cfg {
            tol: f64::from_bits(case.tol),
            tau: tau(case.tau),
            max_hold: case.max_hold,
            window_end: case.window_end,
            mayhem_agent: MAYHEM,
        };
        let (name, c) = case.rule;
        let flows = variant(name).unwrap().kind.needs_flows().then(|| LiveFlowIndex::new(case.venue == "reversed"));
        let mut rule = rule_for(name, &scn, case.t, &clock(c), flows).unwrap();
        if let Some(f) = rule.flows_mut() {
            f.add(legs.iter().filter(|l| l.slot() <= s)); // the engine's legs up to the entry slot (engine.py:906-907)
        }
        let (q, size) = (f64::from_bits(case.q), case.size);
        let (opened, a, b) = if case.venue == "curve" {
            assert!(case.prior.is_none());
            let (s_dec, before, a, b, seed) = curve_entry(&legs, case.t, s);
            let o = open_curve_entry(s_dec, before, &legs[a..b], seed, case.t, size, case.d, q, rule, cfg).expect(at);
            (match o {
                Opened::Open(sim, k) => Opened::Open(sim, a + k),
                o => o,
            }, a, b)
        } else {
            let venue = if case.venue == "reversed" { SimVenue::PoolReversed } else { SimVenue::PoolNormal };
            let prior = case.prior.map(|(slot, [b, e, virt], [lp, pr, cr, cb])| {
                (slot, Pool { b, e, lp_bps: lp, protocol_bps: pr, creator_bps: cr, cashback_bps: cb, virt })
            });
            let (a, b) = (legs.partition_point(|l| l.slot() < s), legs.partition_point(|l| l.slot() <= s));
            (open_pool_position(venue, &legs, prior, case.t, size, case.d, q, rule, cfg).expect(at), a, b)
        };
        let r = match (opened, &case.opened) {
            (Opened::No(r), Want::No) => {
                assert!(case.ops.is_empty(), "{at}");
                r
            }
            (Opened::Completed(r), Want::Completed(slot)) => {
                assert_eq!(r.entry_slot, *slot, "{at}");
                r
            }
            (Opened::Open(mut sim, i), Want::Open(want_i)) => {
                assert_eq!(i, *want_i, "{at}: the insertion index");
                for (k, (op, want)) in case.ops.iter().enumerate() {
                    let got = match *op {
                        Op::Batch => {
                            let done = feed_batch(&mut sim, &legs, i);
                            after(None, done, &sim)
                        }
                        Op::Entry => {
                            let start = i - a;
                            let step = (start < b - a).then(|| sim.on_slot(s, &legs[a..b], start));
                            let done = match step {
                                Some(Step::Completed { slot }) => Some(slot),
                                _ => None,
                            };
                            after(step, done, &sim)
                        }
                        Op::Slot(slot) => {
                            let (x, y) = (legs.partition_point(|l| l.slot() < slot), legs.partition_point(|l| l.slot() <= slot));
                            if let Some(f) = sim.rule_mut().flows_mut() {
                                f.add(&legs[x..y]);
                            }
                            let step = sim.on_slot(slot, &legs[x..y], 0);
                            let done = match step {
                                Step::Completed { slot } => Some(slot),
                                _ => None,
                            };
                            after(Some(step), done, &sim)
                        }
                        Op::Advance(u) => {
                            let step = sim.advance(u);
                            assert!(!matches!(step, Step::Completed { .. }), "{at}");
                            after(None, None, &sim)
                        }
                        Op::End => {
                            let step = sim.end();
                            let done = match step {
                                Step::Completed { slot } => Some(slot),
                                _ => None,
                            };
                            after(Some(step), done, &sim)
                        }
                    };
                    assert_eq!(&got, want, "{at}: call {k}");
                }
                let st = case.state.as_ref().expect(at);
                assert!(same_state(&state(sim.state()), st), "{at}: the venue state {:?}", sim.state());
                assert_eq!(sim.holdings().sorted(), case.holdings.to_vec(), "{at}: the holdings");
                sim.into_result()
            }
            (o, _) => panic!("{at}: opened {o:?}"),
        };
        assert_eq!(result(&r), want_result(&case.result), "{at}");
        assert_eq!(r.venue, if case.venue == "curve" { Venue::Curve } else { Venue::Pool }, "{at}");
    }
}

#[test]
fn single_reruns_are_pythons() {
    // lab-py replay._rerun_curve / _rerun_pool (tools/position_cases.py): every limit at the fill and one past it (own
    // and tau's, scaled sells, the tau sells' int / int where flooring first differs), the leg's tiers and virt, and
    // 400 random legs of every kind and tau mode. The result, the diag, and the holdings untouched.
    for (label, st, l, tau_s, (exact, enabled), hold, want, diag_want) in &RERUNS {
        let leg = leg(l);
        let mut h = Holdings::new(*exact, *enabled);
        if let Some((hist, cf)) = *hold {
            h.apply(leg.trader(), hist, cf);
        }
        let mut diag = Diag::default();
        let got = match (st, &leg) {
            (&St::C([vt, vq, rt, rq, fee_bps, creator_bps]), Leg::Curve(c)) => {
                let s = Curve { vt, vq, rt, rq, fee_bps, creator_bps };
                rerun_curve(&s, c, tau(tau_s), &h, &mut diag, MAYHEM).map(|(s, d)| (state(State::Curve(s)), d))
            }
            (&St::P([b, e, lp_bps, protocol_bps, creator_bps, cashback_bps, virt]), Leg::Pool(p)) => {
                let s = Pool { b, e, lp_bps, protocol_bps, creator_bps, cashback_bps, virt };
                rerun_pool(&s, p, tau(tau_s), &h, &mut diag).map(|(s, d)| (state(State::Pool(s)), d))
            }
            _ => panic!("{label}: a leg of the other venue"),
        };
        match (&got, want) {
            (None, None) => {}
            (Some((gs, gd)), Some((ws, wd))) => assert!(same_state(gs, ws) && gd == wd, "{label}: {gs:?} {gd}"),
            _ => panic!("{label}: got {:?}, Python {}", got.map(|g| g.1), want.is_some()),
        }
        assert_eq!((diag.sells_dropped, diag.sells_scaled), *diag_want, "{label}");
        assert_eq!(h.get(leg.trader()), *hold, "{label}: a re-run only reads the holdings");
    }
}

// ------------------------------------------------------------------------------------------ unit tests

fn pleg(slot: u64, tx: u32) -> Leg {
    Leg::Pool(PoolLeg {
        order: Order { slot, tx, outer: 0, inner: 0 },
        pool: 0,
        trader: 1,
        b: 1_000,
        e: 1_000,
        virt: 0,
        fees: [0; 4],
        base: 1,
        quote_net: 1,
        e_delta: 1,
        x: [0; 2],
        kind: PoolKind::Buy,
        flags: 0,
    })
}

#[test]
fn the_insertion_index_counts_transactions_not_legs() {
    // slot 10: txs 3, 3, 5, 9, 9 (three transactions); slot 12: one
    let legs = [pleg(8, 0), pleg(10, 3), pleg(10, 3), pleg(10, 5), pleg(10, 9), pleg(10, 9), pleg(12, 1)];
    let ins = |slot, q| insertion_index(&legs, slot, q);
    assert_eq!([ins(10, 0.0), ins(10, 0.33), ins(10, 0.34), ins(10, 0.67), ins(10, 0.99), ins(10, 1.0)], [1, 1, 3, 4, 4, 6]);
    // no leg at the slot: the next slot's first leg; one transaction: before it below q 1.0, after it at 1.0
    assert_eq!([ins(9, 0.5), ins(11, 0.99), ins(13, 0.0), ins(0, 0.5), ins(12, 0.99), ins(12, 1.0)], [1, 6, 7, 0, 6, 7]);
    assert_eq!(insertion_index(&[], 5, 0.5), 0);
    // int(q * len) truncates: 0.49 of two transactions is the first
    let two = [pleg(10, 1), pleg(10, 2)];
    assert_eq!([insertion_index(&two, 10, 0.49), insertion_index(&two, 10, 0.5)], [0, 1]);
}

#[test]
#[should_panic(expected = "insertion at q")]
fn a_negative_q_is_refused() {
    insertion_index(&[pleg(10, 1)], 10, -0.1);
}

#[test]
fn holdings_scale_and_drop_as_pythons() {
    // exact (curve): c >= h sells all; h or c not positive drops; a >= h gives c; else a * c // h
    let mut h = Holdings::new(true, true);
    h.seed(1, 100);
    assert_eq!(h.sell_amount(1, 500), 500); // c == h
    h.apply(1, 0, -40); // cf 60 of hist 100
    assert_eq!([h.sell_amount(1, 100), h.sell_amount(1, 150), h.sell_amount(1, 50), h.sell_amount(1, 7)], [60, 60, 30, 4]);
    assert_eq!(h.sell_amount(2, 9), 9); // never seen: (0, 0)
    // a new trader's reverted buy: hist t, cf 0 is kept, so its sell is dropped (cf.get(trader, h) would pass it)
    h.apply(3, 50, 0);
    assert_eq!((h.get(3), h.sell_amount(3, 50)), (Some((50, 0)), 0));
    h.apply(4, -10, -20); // h <= 0
    assert_eq!(h.sell_amount(4, 5), 0);
    // inexact (a normal pool): a - (h - c), not below 0
    let mut p = Holdings::new(false, true);
    p.apply(1, 100, 70);
    assert_eq!([p.sell_amount(1, 100), p.sell_amount(1, 20), p.sell_amount(1, 30)], [70, 0, 0]);
    p.apply(2, 50, 0);
    assert_eq!(p.sell_amount(2, 50), 0);
    // disabled (a reversed pool): every sell whole
    let mut d = Holdings::new(false, false);
    d.apply(1, 100, 0);
    assert_eq!(d.sell_amount(1, 100), 100);
    // NONE is one trader
    let mut n = Holdings::new(true, true);
    n.seed(crate::legs::NONE, 10);
    n.apply(crate::legs::NONE, 0, -5);
    assert_eq!((n.len(), n.sell_amount(crate::legs::NONE, 10)), (1, 5));
    assert_eq!(n.sorted(), vec![(crate::legs::NONE, 10, 5)]);
}

#[test]
fn the_peak_is_the_first_slot_of_the_highest_mark() {
    let mut r = PosResult::new(1, 2, 5, Venue::Pool);
    assert_eq!((r.marks, r.peak, r.last_mark), (0, None, None));
    for (slot, mark) in [(5, 10), (6, 30), (4, 30), (8, 30), (9, 20), (10, 0)] {
        r.mark(slot, mark);
    }
    // max by (mark, -slot): 30 at the earliest of its slots, even one decided later
    assert_eq!((r.marks, r.peak, r.last_mark), (6, Some((4, 30)), Some((10, 0))));
    let mut z = PosResult::new(1, 2, 5, Venue::Curve);
    z.mark(7, 0);
    z.mark(3, 0);
    assert_eq!(z.peak, Some((3, 0))); // all-zero marks: 0, not None
}

fn tp4(t: u64) -> ExitRule {
    rule_for("tp4_T600", &scenario(), t, &clock(0), None).unwrap()
}

fn cleg(slot: u64, tx: u32, trader: Id, is_buy: bool, t: u64, post: [u64; 4]) -> Leg {
    Leg::Curve(CurveLeg {
        order: Order { slot, tx, outer: 0, inner: 0 },
        mint: 0,
        trader,
        t,
        q: 1_000,
        fee: 10,
        vt: post[0],
        vq: post[1],
        rt: post[2],
        rq: post[3],
        args: [0; 4],
        fee_bps: 95,
        creator_bps: 30,
        args_set: 0,
        variant: CurveVariant::Buy,
        is_buy,
        direct: true,
    })
}

#[test]
fn a_curve_entry_without_a_state_before_it_fails_closed() {
    let c = Curve { vt: 1_073_000_000_000_000, vq: 30_000_000_000, rt: 793_100_000_000_000, rq: 0, fee_bps: 95, creator_bps: 30 };
    let post = [c.vt as u64, c.vq as u64, c.rt as u64, 0];
    let slot_legs = [cleg(11, 0, 5, true, 1_000, post)];
    // at the slot's start (q 0) with no earlier leg: Recent is empty, which the engine never reaches
    let err = open_curve_entry(Some(c), None, &slot_legs, [], 10, 500_000_000, 1, 0.0, tp4(10), cfg(0.05)).unwrap_err();
    assert_eq!(err, PosError::NoStateBefore { entry_slot: 11 });
    assert!(err.to_string().contains("no earlier leg"));
    // after the slot's leg (q 1.0; 0.99 of one transaction is still before it) the entry has its state
    let err = open_curve_entry(Some(c), None, &slot_legs, [], 10, 500_000_000, 1, 0.99, tp4(10), cfg(0.05)).unwrap_err();
    assert_eq!(err, PosError::NoStateBefore { entry_slot: 11 });
    let o = open_curve_entry(Some(c), None, &slot_legs, [(5, 7)], 10, 500_000_000, 1, 1.0, tp4(10), cfg(0.05)).unwrap();
    let Opened::Open(sim, k) = o else { panic!("{o:?}") };
    assert_eq!((k, sim.holdings().sorted()), (1, vec![(5, 1_007, 1_007)]));
    // legs of another slot, or of a pool, are refused
    let wrong = [cleg(12, 0, 5, true, 1_000, post)];
    let err = open_curve_entry(Some(c), Some(c), &wrong, [], 10, 500_000_000, 1, 0.5, tp4(10), cfg(0.05)).unwrap_err();
    assert_eq!(err, PosError::NotSlotLegs { entry_slot: 11 });
    let err = open_curve_entry(Some(c), Some(c), &[pleg(11, 0)], [], 10, 500_000_000, 1, 0.5, tp4(10), cfg(0.05)).unwrap_err();
    assert_eq!(err, PosError::NotSlotLegs { entry_slot: 11 });
}

#[test]
#[should_panic(expected = "fed to a position")]
fn a_leg_of_the_other_venue_fails() {
    let st = Pool { b: 1_000_000_000, e: 1_000_000_000, lp_bps: 20, protocol_bps: 5, creator_bps: 5, cashback_bps: 0, virt: 0 };
    let o = open_pool_position(SimVenue::PoolNormal, &[], Some((5, st)), 10, 1_000_000, 1, 0.5, tp4(10), cfg(0.05)).unwrap();
    let Opened::Open(mut sim, _) = o else { panic!("{o:?}") };
    let c = [cleg(11, 0, 5, true, 1_000, [1_000_000, 1_000_000, 500_000, 0])];
    sim.on_slot(11, &c, 0); // the entry slot: no decision before the re-run
}

#[test]
fn a_state_leg_at_or_after_the_entry_fails_closed() {
    let st = Pool { b: 1_000_000_000, e: 1_000_000_000, lp_bps: 20, protocol_bps: 5, creator_bps: 5, cashback_bps: 0, virt: 0 };
    for at in [11, 12] {
        let err = open_pool_position(SimVenue::PoolNormal, &[], Some((at, st)), 10, 1_000_000, 1, 0.5, tp4(10), cfg(0.05)).unwrap_err();
        assert_eq!(err, PosError::PriorNotBefore { prior_slot: at, entry_slot: 11 });
        assert!(err.to_string().contains("not before it"));
    }
    let ok = open_pool_position(SimVenue::PoolNormal, &[], Some((10, st)), 10, 1_000_000, 1, 0.5, tp4(10), cfg(0.05)).unwrap();
    assert!(matches!(ok, Opened::Open(_, 0)));
}

#[test]
fn the_names_are_pythons() {
    let all = [
        (Reason::Rule(ExitReason::TakeProfit), "take_profit"),
        (Reason::Rule(ExitReason::StopLoss), "stop_loss"),
        (Reason::Rule(ExitReason::Time), "time"),
        (Reason::Rule(ExitReason::Outflow), "outflow"),
        (Reason::EndOfData, "end_of_data"),
        (Reason::WindowEnd, "window_end"),
        (Reason::Graduation, "graduation"),
        (Reason::EntrySlippage, "entry_slippage"),
        (Reason::NoPoolState, "no_pool_state"),
        (Reason::NoCurveState, "no_curve_state"),
        (Reason::NoFill, "no_fill"),
        (Reason::CurveComplete, "curve_complete"),
        (Reason::AfterWindowEnd, "after_window_end"),
    ];
    for (r, s) in all {
        assert_eq!(r.as_str(), s);
    }
    assert_eq!((MAX_HOLD, SEED_POOL.b, SEED_POOL.e, SEED_POOL.fee_list()), (400_000, 206_900_000_000_000, 84_990_359_060, [2, 93, 30, 0]));
    assert!(Step::Done.closed() && Step::Completed { slot: 1 }.closed() && !Step::Open.closed());
}
