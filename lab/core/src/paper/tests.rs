//! PaperEngine's own checks; the golden cases from the unmodified engine run in lab/live (paper_core.rs, the shared
//! JSON of paper/tests/c2_position_cases.py).

use super::*;
use crate::clock::tests::dev2;
use crate::clock::{Bounds, SOL};
use crate::costs::RentMode;
use crate::f7::tests::{pool, MAYHEM};
use crate::farm::WindowRule;
use crate::legs::PoolKind;
use crate::n2::tests::FakeKeys;
use crate::signals::GroupSpec;
use crate::universe::{PoolMeta, Universe};

/// dev2's epoch 1042 (267.83 ms a slot): d_ms 600 is 2 slots, tp4_T600's cap 599.
const T: u64 = 450_160_000;
const P: Id = 1;
const Q: Id = 2;
const BT0: i64 = 1_790_000_000;

fn base() -> Scenario {
    Scenario {
        name: "base".into(),
        fee_per_tx: 605_000,
        tip: 100_000,
        failed_tx: 505_000,
        land_prob: 0.97,
        ata_rent: 1_513_840,
        rent_mode: RentMode::RefundedOnFullExit,
        adverse_per_side: 0.0,
    }
}

fn group(name: &str, primary: bool, variants: &[&str], segments: Option<[bool; 3]>, d: u64, d_ms: Option<u64>) -> GroupSpec {
    GroupSpec {
        name: name.into(),
        primary,
        variants: variants.iter().map(|&v| v.into()).collect(),
        segments,
        organic: false,
        n2_k: 0,
        require_farm_complete: false,
        d,
        d_ms,
    }
}

fn k3() -> GroupSpec {
    group("K3", true, &["tp4_T600"], None, 1, Some(600))
}

fn engine(groups: Vec<GroupSpec>, q: Vec<f64>) -> Result<PaperEngine, PaperError> {
    engine_with(groups, q, RiskSource::Ignore)
}

fn engine_with(groups: Vec<GroupSpec>, q: Vec<f64>, risk: RiskSource) -> Result<PaperEngine, PaperError> {
    let mut u = Universe::new(Bounds::new((446_256_000, 452_304_000), &dev2()), MAYHEM);
    for p in [P, Q] {
        u.add_pool(p, PoolMeta { reversed: true, mayhem: false, mint_tok: None, coin_creator: 9 });
    }
    let sig = SignalEngine::new(u, dev2(), groups, WindowRule::AllPools).map_err(PaperError::Signal)?;
    PaperEngine::with_risk(sig, PaperCfg::new(q, base(), GradInfo::default(), HashMap::new()), risk)
}

#[test]
fn what_no_reference_runs_is_refused() {
    let err = |groups, q| engine(groups, q).unwrap_err();
    assert!(matches!(err(vec![group("K3", true, &["tp4_T600"], None, 0, None)], vec![0.5]), PaperError::Config(s) if s.contains("d = 0")));
    assert!(engine(vec![group("K3", true, &["tp4_T600"], None, 0, Some(600))], vec![0.5]).is_ok()); // d_ms decides
    let e = err(vec![k3(), group("H", false, &["trail3_T600"], Some([false, false, true]), 1, None)], vec![0.5, 0.5]);
    assert!(matches!(&e, PaperError::Variant(VariantError::UnknownInGroup { .. })) && e.to_string().contains("M4"), "{e}");
    let e = err(vec![k3(), group("H", false, &["flow300_H6"], Some([true, false, true]), 1, None)], vec![0.5, 0.5]);
    assert!(matches!(e, PaperError::Variant(VariantError::FlowSegments { .. })));
    for q in [-0.1, 1.5, f64::NAN] {
        assert!(matches!(err(vec![k3()], vec![q]), PaperError::Config(s) if s.contains("outside")), "{q}");
    }
    assert!(matches!(err(vec![k3()], vec![0.5, 0.5]), PaperError::Config(s) if s.contains("q values")));
}

#[test]
fn the_ordered_map_keeps_pythons_dict_order() {
    let mut m = Ordered::default();
    m.push(5, 0);
    m.push(3, 1);
    m.push(5, 2);
    assert_eq!(m.snapshot(), vec![(5, vec![0, 2]), (3, vec![1])]);
    assert!(m.remove(5, 0) && !m.remove(5, 0) && !m.remove(7, 0));
    assert!(m.remove(5, 2)); // the key goes with its last position ...
    m.push(5, 3); // ... and comes back at the end
    assert_eq!((m.snapshot(), m.positions(), m.contains(5)), (vec![(3, vec![1]), (5, vec![3])], 2, true));
    m.clear();
    assert!(m.snapshot().is_empty());
}

/// A reversed pool's token buy: a WSOL sell of 2 SOL, at WSOL reserves b_sol and 1e15 token units.
fn inflow(slot: u64, p: Id, b_sol: i64) -> Leg {
    pool((slot, 0), p, 20, PoolKind::Sell, (2 * SOL as i64, 0), (b_sol * SOL as i64, 1_000_000_000_000_000))
}

/// A token sell in a reversed pool takes exactly `sol` SOL out: an exact-out buy of WSOL (as the C1b cases build it).
fn trig((slot, tx): (u64, u32), p: Id, sol: i64, b_sol: i64) -> Leg {
    match pool((slot, tx), p, 21, PoolKind::Buy, (sol * SOL as i64, 0), (b_sol * SOL as i64, 1_000_000_000_000_000)) {
        Leg::Pool(l) => Leg::Pool(crate::legs::PoolLeg { flags: crate::legs::EXACT_OUT, ..l }),
        Leg::Curve(_) => unreachable!(),
    }
}

fn run(legs: &[Leg]) -> (PaperEngine, Vec<Op>) {
    let mut eng = engine(vec![k3()], vec![0.5]).unwrap();
    let mut keys = FakeKeys { names: vec![""; 3], seeds: vec![0; 3], triggers: HashMap::new() };
    let mut ops = Vec::new();
    for &l in legs {
        let bt = BT0 + (l.slot() as i64 - T as i64) * 2 / 5;
        eng.push(&Event::new(l, Some(bt)), &mut keys).unwrap();
        ops.append(&mut eng.paper.ops);
    }
    eng.finish(&mut keys).unwrap();
    ops.append(&mut eng.paper.ops);
    (eng, ops)
}

fn kind(op: &Op) -> String {
    match op {
        Op::Signal(r, s) => format!("signal {} {}", r.key, s.status().unwrap_or("-")),
        Op::Order { status, .. } => format!("order {status}"),
        Op::Fill { side, .. } => format!("fill {side}"),
        Op::Exit(x) => format!("exit {}", x.exit_reason.unwrap_or("-")),
        Op::PutPosition { mark, .. } => format!("put {}", if mark.is_some() { "marked" } else { "unmarked" }),
        Op::DeletePosition(_) => "delete".into(),
        Op::Heartbeat(h) => format!("heartbeat {}", if h.note.is_empty() { "minute" } else { "gap" }),
        Op::Wallet(w) => format!("wallet {}", w.group),
    }
}

#[test]
fn a_pool_position_from_its_signal_to_its_exit() {
    // P triggers at T (K3 enters, d 2 slots); its second trigger at T + 100 finds busy 'open'. The entry at T + 2
    // fills and puts the position (no mark yet); a new minute puts it again with its mark. P's event at T + 700 makes
    // the decision that closes it (the second trigger's 30 SOL out took the price down): the sell fill, the exit row,
    // the positions delete. Q's trigger after it enters, and the end of data closes that one.
    let legs = [inflow(T - 10, P, 580), trig((T, 0), P, 30, 600), inflow(T + 90, P, 570), trig((T + 100, 0), P, 30, 600),
                inflow(T + 700, P, 540), inflow(T + 690, Q, 580), trig((T + 701, 0), Q, 30, 600)];
    let mut legs = legs.to_vec();
    legs.sort_by_key(Leg::order);
    let (eng, all) = run(&legs);
    // the journal's five tables in order; the heartbeat rows and wallet files (C3-3) come between them
    assert!(all.iter().any(|o| matches!(o, Op::Heartbeat(_))) && all.iter().any(|o| matches!(o, Op::Wallet(_))));
    let ops: Vec<Op> = all.into_iter().filter(|o| !matches!(o, Op::Heartbeat(_) | Op::Wallet(_))).collect();
    let seq: Vec<String> = ops.iter().map(kind).collect();
    let first_exit = seq.iter().position(|s| s.starts_with("exit")).unwrap();
    assert_eq!(&seq[..3], ["signal 1 entered", "order filled", "fill buy"], "{seq:?}");
    assert_eq!(seq[3], "put unmarked");
    assert!(seq.contains(&"signal 1 skipped".to_owned()), "{seq:?}");
    assert!(seq[first_exit - 1] == "fill sell" && seq[first_exit + 1] == "delete", "{seq:?}");
    assert!(seq[first_exit + 2..].starts_with(&["signal 2 entered".to_owned()]), "{seq:?}");
    let Op::Exit(x) = &ops[first_exit] else { unreachable!() };
    assert_eq!((x.entry_slot, x.cap_slots, x.n_flow_slots, x.exit_attempts), (T + 2, 599, None, 1));
    assert!(x.exit_slot.is_some_and(|s| s > T + 100 && s < T + 700) && x.peak.is_some(), "{x:?}");
    assert_eq!(x.pnl, eng.paper.cfg.scn.exit_net(x.proceeds, true) - eng.paper.cfg.scn.entry_total(x.cost));
    let busy = ops.iter().find_map(|o| match o {
        Op::Signal(r, s) if r.t == T + 100 => Some(s.clone()),
        _ => None,
    });
    assert_eq!(busy, Some(Status::Skipped("busy".into())));
    // Every position put is deleted again: the final positions table is empty.
    let mut open = std::collections::HashSet::new();
    for op in &ops {
        match op {
            Op::PutPosition { k, .. } => {
                open.insert(k.clone());
            }
            Op::DeletePosition(k) => assert!(open.remove(k), "a delete of {k:?} without a put"),
            _ => {}
        }
    }
    assert!(open.is_empty() && eng.paper.open_positions() == 0 && eng.paper.pending() == 0);
    let c = &eng.paper.counts;
    assert_eq!((c.entered, c.busy, c.not_loaded, c.orders.get("filled"), c.exits.values().sum::<u64>()), (2, 1, 0, Some(&2), 2));
}

#[test]
fn rows_before_the_first_dated_slot_are_not_loaded() {
    // engine.py:151, 471, 827: the risk loads at the first slot with a block time; before it every row past the gates
    // is skipped 'not loaded' and opens nothing.
    let mut eng = engine(vec![k3()], vec![0.5]).unwrap();
    let mut keys = FakeKeys { names: vec![""; 3], seeds: vec![0; 3], triggers: HashMap::new() };
    for l in [inflow(T - 10, P, 580), trig((T, 0), P, 30, 600)] {
        eng.push(&Event::new(l, None), &mut keys).unwrap();
    }
    eng.finish(&mut keys).unwrap();
    let st: Vec<Option<&str>> = eng.paper.ops.iter().filter_map(|o| if let Op::Signal(_, s) = o { Some(s.reason()) } else { None }).collect();
    assert_eq!(st, [Some("not loaded")]);
    assert_eq!((eng.paper.counts.not_loaded, eng.paper.counts.orders.len()), (1, 0));
}

#[test]
fn the_risk_source_is_read_at_the_minute_and_the_entry_and_sizes_the_order() {
    // RiskSource::Files (spec §3): a group without a valid risk skips its rows with the source's reason; with one, the
    // order's size is its size_sol in lamports. The source is read at each new dated minute and again at each entry.
    let legs = [inflow(T - 10, P, 580), trig((T, 0), P, 30, 600), inflow(T + 2, P, 560)];
    let run_with = |answer: Result<Risk, String>| {
        let reads = std::rc::Rc::new(std::cell::Cell::new(0));
        let r = reads.clone();
        let src = RiskSource::Files(Box::new(move |g: usize| {
            assert_eq!(g, 0);
            r.set(r.get() + 1);
            answer.clone()
        }));
        let mut eng = engine_with(vec![k3()], vec![0.5], src).unwrap();
        let mut keys = FakeKeys { names: vec![""; 3], seeds: vec![0; 3], triggers: HashMap::new() };
        for l in legs {
            eng.push(&Event::new(l, Some(BT0 + (l.slot() as i64 - T as i64) * 2 / 5)), &mut keys).unwrap();
        }
        eng.finish(&mut keys).unwrap();
        (eng, reads.get())
    };
    let (eng, reads) = run_with(Err("risk config unreadable: FileNotFoundError".into()));
    let st: Vec<Option<&str>> = eng.paper.ops.iter().filter_map(|o| if let Op::Signal(_, s) = o { Some(s.reason()) } else { None }).collect();
    assert_eq!(st, [Some("risk config unreadable: FileNotFoundError")]);
    assert_eq!(eng.paper.counts.risk_skips.get("K3: risk config unreadable: FileNotFoundError"), Some(&1));
    assert_eq!(reads, 1, "one dated minute, no entry");
    let risk = Risk { size_sol: 0.3, ..Risk::ignore() };
    let (eng, reads) = run_with(Ok(risk));
    let sizes: Vec<i128> = eng.paper.ops.iter().filter_map(|o| if let Op::Order { size, .. } = o { Some(*size) } else { None }).collect();
    assert_eq!(sizes, [300_000_000]);
    assert_eq!(reads, 2, "the minute and the entry");
}

// ---------------------------------------------------------------------------------------------- live (M3-08)

/// The first test's legs (two positions, a busy skip, quiet entries at T + 2 and T + 703).
fn two_positions() -> Vec<Leg> {
    let mut legs = vec![inflow(T - 10, P, 580), trig((T, 0), P, 30, 600), inflow(T + 90, P, 570), trig((T + 100, 0), P, 30, 600),
                        inflow(T + 700, P, 540), inflow(T + 690, Q, 580), trig((T + 701, 0), Q, 30, 600)];
    legs.sort_by_key(Leg::order);
    legs
}

/// `run`, with `close_through` after each slot's last event: `early` closes that slot, else every slot before the
/// next event's (the entries due in between fill at the close).
fn run_closing(legs: &[Leg], early: bool) -> (PaperEngine, Vec<Op>) {
    let mut eng = engine(vec![k3()], vec![0.5]).unwrap();
    let mut keys = FakeKeys { names: vec![""; 3], seeds: vec![0; 3], triggers: HashMap::new() };
    let mut ops = Vec::new();
    for (i, &l) in legs.iter().enumerate() {
        let bt = BT0 + (l.slot() as i64 - T as i64) * 2 / 5;
        eng.push(&Event::new(l, Some(bt)), &mut keys).unwrap();
        ops.append(&mut eng.paper.ops);
        if let Some(next) = legs.get(i + 1).map(Leg::slot).filter(|&s| s != l.slot()) {
            let c = if early { l.slot() } else { next - 1 };
            assert_eq!(eng.close_through(c, &mut keys).unwrap(), Some(l.slot()));
            ops.append(&mut eng.paper.ops);
        }
    }
    eng.finish(&mut keys).unwrap();
    ops.append(&mut eng.paper.ops);
    (eng, ops)
}

#[test]
fn closing_a_complete_slot_early_writes_what_the_next_event_writes() {
    // Spec D1: close_through(c) for any c from the last slot up to the slot before the next event, then that event's
    // push, writes exactly what the push alone writes (the slot's end, then the quiet entries in order).
    let legs = two_positions();
    let (_, want) = run(&legs);
    assert!(want.iter().any(|o| matches!(o, Op::Exit(_))));
    for early in [true, false] {
        let (eng, got) = run_closing(&legs, early);
        assert_eq!(got, want, "early {early}");
        assert_eq!(eng.closed(), Some(T + 700)); // the last slot before T + 701 in both
    }
    // Late: the entry due at T + 2 (no event there) fills at the close through T + 89, before the event at T + 90.
    let mut eng = engine(vec![k3()], vec![0.5]).unwrap();
    let mut keys = FakeKeys { names: vec![""; 3], seeds: vec![0; 3], triggers: HashMap::new() };
    for &l in &legs[..2] {
        eng.push(&Event::new(l, Some(BT0 + (l.slot() as i64 - T as i64) * 2 / 5)), &mut keys).unwrap();
    }
    eng.paper.ops.clear();
    assert_eq!((eng.close_through(T + 89, &mut keys).unwrap(), eng.paper.pending(), eng.paper.open_positions()), (Some(T), 0, 1));
    assert!(eng.paper.ops.iter().any(|o| matches!(o, Op::Fill { .. })), "{:?}", eng.paper.ops);
}

#[test]
fn a_late_event_and_input_after_stop_are_refused() {
    let legs = two_positions();
    let mut eng = engine(vec![k3()], vec![0.5]).unwrap();
    let mut keys = FakeKeys { names: vec![""; 3], seeds: vec![0; 3], triggers: HashMap::new() };
    let ev = |l: Leg| Event::new(l, Some(BT0 + (l.slot() as i64 - T as i64) * 2 / 5));
    eng.push(&ev(legs[0]), &mut keys).unwrap();
    assert_eq!(eng.close_through(T - 5, &mut keys).unwrap(), Some(T - 10));
    assert_eq!(eng.close_through(T - 8, &mut keys).unwrap(), None); // closed never moves back
    assert_eq!(eng.closed(), Some(T - 5));
    let late = eng.push(&ev(inflow(T - 5, Q, 580)), &mut keys).unwrap_err();
    assert!(matches!(late, PaperError::Late { slot, closed } if slot == T - 5 && closed == T - 5), "{late}");
    eng.push(&ev(legs[1]), &mut keys).unwrap(); // T: P's trigger, the entry due at T + 2
    eng.push(&ev(legs[2]), &mut keys).unwrap(); // T + 90 ends T; the entry fills
    assert_eq!((eng.paper.open_positions(), eng.paper.pending()), (1, 0));
    eng.paper.ops.clear();
    // Spec D2: stop writes nothing, leaves the open slot and the position, and refuses what comes after.
    eng.stop();
    assert!(eng.stopped() && eng.paper.ops.is_empty() && eng.sig.open_slot() == Some(T + 90));
    assert!(matches!(eng.push(&ev(legs[3]), &mut keys), Err(PaperError::Stopped)));
    assert!(matches!(eng.close_through(T + 200, &mut keys), Err(PaperError::Stopped)));
    assert!(matches!(eng.finish(&mut keys), Err(PaperError::Stopped)));
    assert!(eng.paper.ops.is_empty() && eng.paper.open_positions() == 1);
}

#[test]
fn a_pool_learned_live_is_known_from_then_on() {
    // Spec D3: R is not in the snapshot. Its trigger is refused (PoolAbsent) until the feed learns it; a second learn
    // of a known pool changes nothing.
    const R: Id = 3;
    let legs = [inflow(T - 10, R, 580), trig((T, 0), R, 30, 600)];
    let entered = |learn: bool| {
        let mut eng = engine(vec![k3()], vec![0.5]).unwrap();
        let mut keys = FakeKeys { names: vec![""; 4], seeds: vec![0; 4], triggers: HashMap::new() };
        let meta = PoolMeta { reversed: true, mayhem: false, mint_tok: None, coin_creator: 9 };
        if learn {
            assert!(eng.learn_pool(R, meta, Some(T - 100)));
            assert!(!eng.learn_pool(R, PoolMeta { mayhem: true, ..meta }, None)); // known: unchanged
            assert!(!eng.learn_pool(P, meta, None));
        }
        for l in legs {
            eng.push(&Event::new(l, Some(BT0 + (l.slot() as i64 - T as i64) * 2 / 5)), &mut keys).unwrap();
        }
        eng.close_through(T, &mut keys).unwrap();
        let n = eng.paper.ops.iter().filter(|o| matches!(o, Op::Signal(r, _) if r.key == R)).count();
        (n, eng.sig.counts.rejects[crate::universe::Reject::PoolAbsent as usize], eng.sig.universe.pool(R).copied())
    };
    let (n, absent, meta) = entered(false);
    assert_eq!((n, meta), (0, None));
    assert!(absent > 0);
    let (n, absent, meta) = entered(true);
    assert_eq!((n, absent), (1, 0));
    assert!(meta.is_some_and(|m| !m.mayhem));
}

#[test]
fn an_epoch_learned_live_sets_the_slots_from_then_on() {
    // Spec D4: K3's d_ms 600 is 2 slots at dev2's 267.83 ms; at 100 ms it is 6, for the signals after the learn only.
    let mut eng = engine(vec![k3()], vec![0.5]).unwrap();
    let mut keys = FakeKeys { names: vec![""; 3], seeds: vec![0; 3], triggers: HashMap::new() };
    let ev = |l: Leg| Event::new(l, Some(BT0 + (l.slot() as i64 - T as i64) * 2 / 5));
    let e = T / crate::clock::EPOCH_SLOTS;
    assert_eq!(eng.sig.clock.slots(600.0, T), 2);
    for l in [inflow(T - 10, P, 580), trig((T, 0), P, 30, 600), inflow(T - 10 + 50, Q, 580)] {
        eng.push(&ev(l), &mut keys).unwrap();
    }
    eng.set_epoch_ms(e, 100.0);
    let c = &eng.sig.clock;
    assert_eq!((c.slots(600.0, T), c.ms_per_slot(T), c.ms_per_slot(T + 432_000)), (6, 100.0, 267.93)); // 1043 its own
    for l in [trig((T + 50, 0), Q, 30, 600), inflow(T + 300, P, 580)] {
        eng.push(&ev(l), &mut keys).unwrap();
    }
    eng.close_through(T + 300, &mut keys).unwrap();
    let entries: Vec<(Id, u64)> = eng.paper.ops.iter().filter_map(|o| match o {
        Op::Fill { k, slot: Some(slot), side: "buy", .. } => Some((k.key, *slot)),
        _ => None,
    }).collect();
    assert_eq!(entries, [(P, T + 2), (Q, T + 56)]);
}
