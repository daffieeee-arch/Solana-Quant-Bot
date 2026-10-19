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
    let mut u = Universe::new(Bounds::new((446_256_000, 452_304_000), &dev2()), MAYHEM);
    for p in [P, Q] {
        u.add_pool(p, PoolMeta { reversed: true, mayhem: false, mint_tok: None, coin_creator: 9 });
    }
    let sig = SignalEngine::new(u, dev2(), groups, WindowRule::AllPools).map_err(PaperError::Signal)?;
    PaperEngine::new(sig, PaperCfg::new(q, base(), GradInfo::default(), HashMap::new()))
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
    let (eng, ops) = run(&legs);
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
