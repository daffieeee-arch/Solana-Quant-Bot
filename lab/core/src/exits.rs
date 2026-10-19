//! The exit rules of M1's three variants: when an open paper position decides to sell (backtest/strategies.py
//! `TpSlTime`, `FlowTime`, `FlowTpTime`, :139-154, :232-250, :273-283; built as paper/engine.py `_enter` builds them,
//! :884-890). M1 C2; the porting spec is research/m1-c2-positionsim-spec-2026-10-19.md §1.6 (the scope) and §3.8.
//!
//! - M1 has exactly three variant names, in a fixed table (`VARIANTS`): `tp4_T600` (R1, R2 and K3 at the paper start),
//!   `flow300_H6` and `flow300_tp100_H6` (group H). Every other name is refused, with a message naming M4: the other
//!   F7_GRID and EXIT_VARIANTS names, other tp:T specs, TrailTime, HoldTime, TpTime and the stop-loss branch of the
//!   flow kinds come in M4. backtest/tests/test_exit_table.py pins the table to paper.engine.variant, bit for bit.
//! - The journal's groups carry names only, and a tp:T name is lossy (`int(tp * 100)`, engine.py:88: `0.045:600` is
//!   `tp4_T600` too), so lab/live/nas_m1c2.sh pins the literal `--variants 0.04:600` on the Python side.
//! - The return is Python's: `exit_net(mark) / entry_total(cost) - 1`, an int / int division rounded once
//!   (`true_div`), then minus 1.0. (N - T) / T or a plain f64 division decide differently at the edges (92/100 - 1 is
//!   -0.07999999999999996, no stop at -0.08). Both edges are inclusive (>= tp, <= sl); no return lands exactly on
//!   0.04 or -0.08 (x - 1 is exact near 1, and 1 + 0.04, 1 - 0.08 are no f64), while tp 1.0 is an exact 2x.
//! - A flow rule owns its position's `LiveFlowIndex` (Python's rule and Open share one object); the engine feeds it
//!   through `flows_mut`. The rule calls `flow` only where Python's `and` reaches it, because the query prunes.
//! - The windows come from the clock at the signal slot t (engine.py:887, 890): `n_flow` and `max_slots` are the
//!   exits row's n_flow_slots and cap_slots (engine.py `_windows`, :1327-1329).

use crate::clock::{ms, Clock};
use crate::costs::Scenario;
use crate::exact::true_div;
use crate::flow::LiveFlowIndex;
use crate::signals::GroupSpec;
use crate::universe::Segment;
use std::collections::HashSet;
use std::fmt;

/// The rule class a variant builds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// A take-profit and a stop-loss on the net return, or time (a tp:T name; strategies.py:139-154).
    TpSlTime,
    /// An outflow over the last n_flow slots, or time (strategies.py:232-250); its stop-loss is None in M1.
    FlowTime,
    /// FlowTime with a take-profit on the net return checked first (strategies.py:273-283).
    FlowTpTime,
}

impl Kind {
    /// The Python class name.
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::TpSlTime => "TpSlTime",
            Kind::FlowTime => "FlowTime",
            Kind::FlowTpTime => "FlowTpTime",
        }
    }

    /// Needs the pool's flow index (strategies.py FLOW_KINDS, :286; engine.py:886).
    pub fn needs_flows(self) -> bool {
        matches!(self, Kind::FlowTime | Kind::FlowTpTime)
    }
}

/// A variant's time cap, as its Python params give it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cap {
    /// T of a tp:T spec in v1 slots of 267.3 ms: ms(T) milliseconds (engine.py:890).
    V1Slots(u64),
    /// A long exit's cap_ms (strategies.py:293).
    Ms(u64),
}

impl Cap {
    /// The cap in milliseconds, as Python hands it to Clock.slots: `ms(T)` (T x 267.3 in f64) or the int cap_ms.
    pub fn ms(self) -> f64 {
        match self {
            Cap::V1Slots(t) => ms(t),
            Cap::Ms(m) => m as f64,
        }
    }
}

/// One row of M1's variant table: the name the groups state carries and what paper.engine builds for it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Variant {
    pub name: &'static str,
    /// The spec Python is given (`--variants`, a config's `variants`).
    pub spec: &'static str,
    pub kind: Kind,
    /// The take-profit on the net return; None for FlowTime.
    pub tp: Option<f64>,
    /// The stop-loss on the net return: TpSlTime's hard-coded -0.08 (engine.py:890); None for the flow kinds.
    pub sl: Option<f64>,
    /// The flow window in seconds (params x, or x[0] for flowtp); n_flow is Clock.slots(x * 1000, t).
    pub flow_s: Option<u64>,
    pub cap: Cap,
}

impl Variant {
    /// The flow window in milliseconds, `x * 1000` (strategies.py:299, 303).
    pub fn flow_ms(&self) -> Option<f64> {
        self.flow_s.map(|s| (s * 1000) as f64)
    }
}

/// M1's variants (spec §1.6): `paper.engine.variant` of `0.04:600`, `flow300_H6` and `flow300_tp100_H6`, with the rule
/// `_enter` builds for each.
pub static VARIANTS: [Variant; 3] = [
    Variant { name: "tp4_T600", spec: "0.04:600", kind: Kind::TpSlTime, tp: Some(0.04), sl: Some(-0.08), flow_s: None, cap: Cap::V1Slots(600) },
    Variant { name: "flow300_H6", spec: "flow300_H6", kind: Kind::FlowTime, tp: None, sl: None, flow_s: Some(300), cap: Cap::Ms(21_600_000) },
    Variant {
        name: "flow300_tp100_H6",
        spec: "flow300_tp100_H6",
        kind: Kind::FlowTpTime,
        tp: Some(1.0),
        sl: None,
        flow_s: Some(300),
        cap: Cap::Ms(21_600_000),
    },
];

/// Why a variant or a group setup is refused (fail closed, before the first event).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum VariantError {
    /// A name outside M1's table.
    Unknown { variant: String },
    /// A flow variant in a group whose segments are not exactly [pool_reversed].
    FlowSegments { group: String, variant: String, segments: Option<[bool; 3]> },
    /// A variant name used twice across the groups (engine.py:237-239 refuses it too).
    Duplicate { variant: String },
    /// A flow index given to a rule without a flow window, or none given to one with.
    Flows { variant: String, given: bool },
    /// A variant spec (`--variants`) that is not exactly one of M1's: a tp:T spec counts only with tp4_T600's exact
    /// parameters, since its name `tp{int(tp * 100)}_T{T}` loses tp's digits (0.045:600 is named tp4_T600 too).
    Spec { spec: String },
}

impl fmt::Display for VariantError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let m1 = VARIANTS.map(|v| v.name).join(", ");
        match self {
            VariantError::Unknown { variant } => {
                write!(f, "variant {variant:?} is not one of M1's exit variants ({m1}): its exit rule comes in M4")
            }
            VariantError::FlowSegments { group, variant, segments } => {
                let segs = segments.map_or("null".to_owned(), |s| {
                    let on: Vec<&str> = Segment::ALL.iter().filter(|g| s[g.index()]).map(|g| g.as_str()).collect();
                    format!("[{}]", on.join(", "))
                });
                write!(f, "group {group:?}: the flow variant {variant:?} runs in M1 only with segments exactly [pool_reversed], ")?;
                write!(f, "not {segs}; other flow setups come in M4")
            }
            VariantError::Duplicate { variant } => {
                write!(f, "variant {variant:?} is named twice across the groups (Python refuses it too, engine.py:237-239; ")?;
                write!(f, "not an M4 item either)")
            }
            VariantError::Spec { spec } => {
                let specs = VARIANTS.map(|v| v.spec).join(", ");
                write!(f, "variant spec {spec:?} is not exactly one of M1's ({specs}); a tp:T name drops tp's digits, so only ")?;
                write!(f, "the exact parameters count; other specs come in M4")
            }
            VariantError::Flows { variant, given } => {
                let what = if *given { "was given a flow index it has no use for" } else { "needs the pool's flow index" };
                write!(f, "variant {variant:?} {what} (M1's flow kinds: flow300_H6, flow300_tp100_H6; others come in M4)")
            }
        }
    }
}

impl std::error::Error for VariantError {}

/// The table row of a name; any other name is refused (M4).
pub fn variant(name: &str) -> Result<&'static Variant, VariantError> {
    VARIANTS.iter().find(|v| v.name == name).ok_or_else(|| VariantError::Unknown { variant: name.to_owned() })
}

/// `paper.engine.variant(spec)` (engine.py:82-94) within M1: a tp:T spec gives tp4_T600 only when its tp parses to
/// 0.04 exactly (Python's float(), correctly rounded as Rust's parse) and T to 600; an exit variant's name, only M1's.
/// `lab-live paper` takes the literal `--variants` Python was given and checks it here, since the groups state holds
/// names only (coordinator, 2026-10-19). Anything else is refused (M4), also forms Python accepts that this does not
/// parse (a T with a sign or underscores): fail closed.
pub fn variant_for_spec(spec: &str) -> Result<&'static Variant, VariantError> {
    let refused = || VariantError::Spec { spec: spec.to_owned() };
    match spec.split_once(':') {
        Some((tp, t)) => {
            let (tp, t) = (tp.trim().parse::<f64>().map_err(|_| refused())?, t.trim().parse::<u64>().map_err(|_| refused())?);
            VARIANTS
                .iter()
                .find(|v| v.kind == Kind::TpSlTime && v.tp.map(f64::to_bits) == Some(tp.to_bits()) && v.cap == Cap::V1Slots(t))
                .ok_or_else(refused)
        }
        None => VARIANTS.iter().find(|v| v.kind != Kind::TpSlTime && v.name == spec).ok_or_else(refused),
    }
}

/// The segments a flow variant may run in: exactly [pool_reversed] (spec §1.6, refusal rule 2). Stricter than Python,
/// which refuses only null segments and segments with curve (engine.py:164-166).
pub fn flow_segments_ok(segments: Option<[bool; 3]>) -> bool {
    segments == Some([false, false, true])
}

/// `lab-live paper`'s check of the groups state at construction (spec §1.6): every name in M1's table, flow names only
/// in segments exactly [pool_reversed], no name twice. Per group in order its names, then its segments; the duplicates
/// last (Python: Group, then Engine). The first refusal is returned.
pub fn check_groups(groups: &[GroupSpec]) -> Result<(), VariantError> {
    let mut names = HashSet::new();
    for g in groups {
        // Python's order (engine.py:162-166): every name of the group, then its segments
        let vs = g.variants.iter().map(|n| variant(n)).collect::<Result<Vec<_>, _>>()?;
        if let Some(v) = vs.iter().find(|v| v.kind.needs_flows() && !flow_segments_ok(g.segments)) {
            return Err(VariantError::FlowSegments { group: g.name.clone(), variant: v.name.to_owned(), segments: g.segments });
        }
    }
    for name in groups.iter().flat_map(|g| &g.variants) {
        if !names.insert(name.as_str()) {
            return Err(VariantError::Duplicate { variant: name.clone() });
        }
    }
    Ok(())
}

/// Why a rule decides to sell (the exits row's exit_reason).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExitReason {
    TakeProfit,
    StopLoss,
    Time,
    Outflow,
}

impl ExitReason {
    pub fn as_str(self) -> &'static str {
        match self {
            ExitReason::TakeProfit => "take_profit",
            ExitReason::StopLoss => "stop_loss",
            ExitReason::Time => "time",
            ExitReason::Outflow => "outflow",
        }
    }
}

/// What a decision sees (PositionSim's context, replay.py:412-413): the slot decided for, the position's entry slot,
/// the mark (what selling everything would bring, 0 when the sale fails) and the venue cost of the entry.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Ctx {
    pub slot: u64,
    pub entry_slot: u64,
    pub mark: i128,
    pub cost: i128,
}

impl Ctx {
    /// `slots_held = slot - entry_slot`. PositionSim never decides before the entry; a context that does fails.
    pub fn slots_held(&self) -> u64 {
        self.slot.checked_sub(self.entry_slot).expect("a decision before the entry slot")
    }
}

/// The net return of selling at `mark` (strategies.py:147, 281): `exit_net(mark) / entry_total(cost) - 1`, the int
/// division correctly rounded as Python's, then minus 1.0. An entry_total of 0 fails, as Python's ZeroDivisionError.
pub fn net_ret(scn: &Scenario, mark: i128, cost: i128) -> f64 {
    true_div(scn.exit_net(mark, true), scn.entry_total(cost)) - 1.0
}

/// An open position's exit rule (one per position: a flow rule owns its pool flow index).
#[derive(Clone, Debug, PartialEq)]
pub enum ExitRule {
    /// tp4_T600: ret >= tp → take_profit; ret <= sl → stop_loss; slots_held >= max_slots → time.
    TpSlTime { tp: f64, sl: f64, max_slots: u64, scn: Scenario },
    /// flow300_H6: slots_held >= n_flow and flow(slot - n_flow, slot) <= 0 → outflow; slots_held >= max_slots → time.
    /// Python's stop-loss step between them is False without computing ret while sl is None (strategies.py:190-191),
    /// so no return is computed here at all.
    FlowTime { n_flow: u64, max_slots: u64, flows: LiveFlowIndex },
    /// flow300_tp100_H6: ret >= tp → take_profit on every call, then FlowTime.
    FlowTpTime { n_flow: u64, tp: f64, max_slots: u64, flows: LiveFlowIndex, scn: Scenario },
}

impl ExitRule {
    pub fn kind(&self) -> Kind {
        match self {
            ExitRule::TpSlTime { .. } => Kind::TpSlTime,
            ExitRule::FlowTime { .. } => Kind::FlowTime,
            ExitRule::FlowTpTime { .. } => Kind::FlowTpTime,
        }
    }

    /// The time cap in slots (the exits row's cap_slots).
    pub fn max_slots(&self) -> u64 {
        match self {
            ExitRule::TpSlTime { max_slots, .. } | ExitRule::FlowTime { max_slots, .. } | ExitRule::FlowTpTime { max_slots, .. } => *max_slots,
        }
    }

    /// The flow window in slots (the exits row's n_flow_slots; None for TpSlTime, `getattr(rule, "n_flow", None)`).
    pub fn n_flow(&self) -> Option<u64> {
        match self {
            ExitRule::TpSlTime { .. } => None,
            ExitRule::FlowTime { n_flow, .. } | ExitRule::FlowTpTime { n_flow, .. } => Some(*n_flow),
        }
    }

    /// The slot at which time runs out, `entry_slot + max_slots` (every M1 rule has one); PositionSim decides it inside
    /// a quiet gap or at the end of the data (replay.py:398, 444-448, 525-528).
    pub fn deadline(&self, entry_slot: u64) -> Option<u64> {
        Some(entry_slot + self.max_slots())
    }

    pub fn flows(&self) -> Option<&LiveFlowIndex> {
        match self {
            ExitRule::TpSlTime { .. } => None,
            ExitRule::FlowTime { flows, .. } | ExitRule::FlowTpTime { flows, .. } => Some(flows),
        }
    }

    /// The flow index to feed: the pool's legs at the entry, then each later slot's legs before the position decides
    /// on them (engine.py:902, 907-908, 379-380).
    pub fn flows_mut(&mut self) -> Option<&mut LiveFlowIndex> {
        match self {
            ExitRule::TpSlTime { .. } => None,
            ExitRule::FlowTime { flows, .. } | ExitRule::FlowTpTime { flows, .. } => Some(flows),
        }
    }

    /// The rule's call (`exit_rule(ctx)`): the reason to sell, or None. The first check that holds wins.
    pub fn decide(&mut self, ctx: &Ctx) -> Option<ExitReason> {
        let held = ctx.slots_held();
        match self {
            ExitRule::TpSlTime { tp, sl, max_slots, scn } => {
                let ret = net_ret(scn, ctx.mark, ctx.cost);
                if ret >= *tp {
                    Some(ExitReason::TakeProfit)
                } else if ret <= *sl {
                    Some(ExitReason::StopLoss)
                } else {
                    (held >= *max_slots).then_some(ExitReason::Time)
                }
            }
            ExitRule::FlowTime { n_flow, max_slots, flows } => flow_time(*n_flow, *max_slots, flows, ctx, held),
            ExitRule::FlowTpTime { n_flow, tp, max_slots, flows, scn } => {
                if net_ret(scn, ctx.mark, ctx.cost) >= *tp {
                    return Some(ExitReason::TakeProfit);
                }
                flow_time(*n_flow, *max_slots, flows, ctx, held)
            }
        }
    }
}

/// FlowTime's call (strategies.py:244-250). `&&` keeps Python's short circuit: no query (and no prune) before n_flow
/// slots are held. Then the stop-loss step, which is no step while sl is None (M4 adds it), then time.
fn flow_time(n_flow: u64, max_slots: u64, flows: &mut LiveFlowIndex, ctx: &Ctx, held: u64) -> Option<ExitReason> {
    if held >= n_flow && flows.flow(ctx.slot - n_flow, ctx.slot) <= 0 {
        return Some(ExitReason::Outflow);
    }
    (held >= max_slots).then_some(ExitReason::Time)
}

/// The exit rule of a position on `name` whose signal was at slot t, as engine.py `_enter` builds it (:884-890): tp:T
/// as `tp_sl_time(tp, -0.08, clock.slots(ms(T), t), scn)`; a flow name through `f7_exit_rule` (strategies.py:289-304),
/// with n_flow = clock.slots(x * 1000, t) and max_slots = clock.slots(cap_ms, t). `flows` is the position's new index
/// (`LiveFlowIndex(p.orientation)`), given exactly when the kind needs one.
pub fn rule_for(name: &str, scn: &Scenario, t: u64, clock: &Clock, flows: Option<LiveFlowIndex>) -> Result<ExitRule, VariantError> {
    let v = variant(name)?;
    if v.kind.needs_flows() != flows.is_some() {
        return Err(VariantError::Flows { variant: name.to_owned(), given: flows.is_some() });
    }
    let max_slots = clock.slots(v.cap.ms(), t);
    let n_flow = v.flow_ms().map(|d| clock.slots(d, t));
    let (tp, sl) = (v.tp, v.sl);
    Ok(match (v.kind, flows, n_flow) {
        (Kind::TpSlTime, None, None) => {
            ExitRule::TpSlTime { tp: tp.expect("TpSlTime has a tp"), sl: sl.expect("TpSlTime has an sl"), max_slots, scn: scn.clone() }
        }
        (Kind::FlowTime, Some(flows), Some(n_flow)) => ExitRule::FlowTime { n_flow, max_slots, flows },
        (Kind::FlowTpTime, Some(flows), Some(n_flow)) => {
            ExitRule::FlowTpTime { n_flow, tp: tp.expect("FlowTpTime has a tp"), max_slots, flows, scn: scn.clone() }
        }
        _ => unreachable!("the table row of {name} does not fit its kind"),
    })
}

#[cfg(test)]
mod cases;

#[cfg(test)]
mod tests {
    use super::cases::{Op, CASES, CLOCKS, DUPLICATES, PYTHON_NAMES, RUNS, SCENARIOS, SEGMENTS, TABLE, TP_T_NAMES, WINDOWS};
    use super::*;
    use crate::costs::RentMode;
    use crate::flow::tests::{pool_leg, run_legs};
    use crate::legs::{Leg, FARMER};

    fn clock(i: usize) -> Clock {
        Clock::new(CLOCKS[i].1.iter().copied().collect())
    }

    fn scenario(i: usize) -> Scenario {
        let (name, fee, tip, failed, land, rent, mode, adverse) = SCENARIOS[i];
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

    fn new_rule(name: &str, scn: &Scenario, t: u64, c: &Clock) -> ExitRule {
        let flows = variant(name).unwrap().kind.needs_flows().then(|| LiveFlowIndex::new(true));
        rule_for(name, scn, t, c, flows).unwrap()
    }

    fn group(name: &str, primary: bool, variants: &[&str], segments: Option<[bool; 3]>) -> GroupSpec {
        GroupSpec {
            name: name.into(),
            primary,
            variants: variants.iter().map(|&v| v.into()).collect(),
            segments,
            organic: false,
            n2_k: 0,
            require_farm_complete: false,
            d: 1,
            d_ms: Some(600),
        }
    }

    #[test]
    fn the_table_is_pythons_to_the_bit() {
        // lab-py paper.engine.variant(spec) and the rule Engine._enter builds from it (tools/exit_cases.py exits):
        // name, spec, class, tp, sl (the hard-coded -0.08 of engine.py:890), the flow seconds, the cap and its ms.
        assert_eq!(VARIANTS.len(), TABLE.len());
        for (v, (name, spec, kind, tp, sl, flow_s, (cap_kind, cap), cap_ms)) in VARIANTS.iter().zip(TABLE) {
            assert_eq!((v.name, v.spec, v.kind.as_str()), (name, spec, kind));
            assert_eq!((v.tp.map(f64::to_bits), v.sl.map(f64::to_bits), v.flow_s), (tp, sl, flow_s), "{name}");
            let c = match v.cap {
                Cap::V1Slots(t) => ("V1Slots", t),
                Cap::Ms(m) => ("Ms", m),
            };
            assert_eq!((c, v.cap.ms().to_bits()), ((cap_kind, cap), cap_ms), "{name}");
            assert_eq!(v.kind.needs_flows(), v.flow_s.is_some());
            assert!(std::ptr::eq(variant(name).unwrap(), v));
        }
    }

    #[test]
    fn the_windows_are_the_engines() {
        // lab-py: n_flow_slots, cap_slots (engine.py `_windows`) and deadline(entry) of the engine's rule at t: dev2
        // around 1041 -> 1042, fwd1053, epoch 1064 (unmeasured: 218.29 ms), REF_MS, and k + 0.5 ties (half to even).
        let scn = scenario(2);
        for (name, c, t, entry, n_flow, cap, deadline) in WINDOWS {
            let rule = new_rule(name, &scn, t, &clock(c));
            assert_eq!((rule.n_flow(), rule.max_slots(), rule.deadline(entry)), (n_flow, cap, deadline), "{name} {} {t}", CLOCKS[c].0);
            assert_eq!(rule.kind(), variant(name).unwrap().kind);
        }
    }

    #[test]
    fn decisions_follow_pythons_rules_call_by_call() {
        // lab-py: the engine-built rule called with PositionSim's context (tools/exit_cases.py exits): the reason, the
        // net return's bits, every flow query (lo, hi, value) the call made, and the legs the index keeps after it.
        for case in CASES {
            let scn = scenario(case.scn);
            let mut rule = new_rule(case.variant, &scn, case.t, &clock(case.clock));
            assert_eq!((rule.n_flow(), rule.max_slots(), rule.deadline(case.entry)), case.windows, "{}", case.name);
            for (k, op) in case.ops.iter().enumerate() {
                let at = format!("{} op {k}", case.name);
                match op {
                    Op::Add(legs) => {
                        let legs: Vec<Leg> = legs.iter().map(|&(s, tx, o, i, kind, b, q, f)| pool_leg(s, tx, o, i, kind, b, q, f)).collect();
                        rule.flows_mut().expect(&at).add(&legs);
                    }
                    Op::Run { slot0, n, per_slot, base0, check } => {
                        let legs = run_legs(*slot0, *n, *per_slot, *base0);
                        let got = legs.iter().fold((0u64, 0i128, 0u32), |(s, b, fm), l| match l {
                            Leg::Pool(p) => (s + p.order.slot, b + i128::from(p.base), fm + u32::from(p.flag(FARMER))),
                            Leg::Curve(_) => unreachable!(),
                        });
                        assert_eq!(got, *check, "{at}: the run's legs are not the generator's");
                        rule.flows_mut().expect(&at).add(&legs);
                    }
                    Op::Decide { slot, mark, ret, reason, calls, kept } => {
                        if let Some(f) = rule.flows_mut() {
                            f.log.clear();
                        }
                        let ctx = Ctx { slot: *slot, entry_slot: case.entry, mark: *mark, cost: case.cost };
                        let got = rule.decide(&ctx);
                        assert_eq!(got.map(ExitReason::as_str), *reason, "{at}: decide({slot}, {mark})");
                        let log = rule.flows().map_or(&[][..], |f| f.log.as_slice());
                        assert_eq!(log, *calls, "{at}: the flow queries");
                        assert_eq!(rule.flows().map(LiveFlowIndex::len), *kept, "{at}: the legs kept");
                        if let Some(bits) = ret {
                            assert_eq!(net_ret(&scn, *mark, case.cost).to_bits(), *bits, "{at}: the net return");
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn the_spec_examples_of_the_f64_order() {
        // From the generated cases' unit scenario (net = mark, total = cost; lab-py): 104/100 - 1 and 92/100 - 1.
        let unit = scenario(0);
        assert_eq!(net_ret(&unit, 104, 100).to_bits(), 0x3fa4_7ae1_47ae_1480); // 0.040000000000000036 >= 0.04
        assert_eq!(net_ret(&unit, 92, 100).to_bits(), 0xbfb4_7ae1_47ae_1478); // -0.07999999999999996 > -0.08
        let tp = CASES.iter().find(|c| c.name == "tp_unit").unwrap();
        assert!(matches!(tp.ops[2], Op::Decide { mark: 92, reason: None, .. }));
    }

    #[test]
    fn only_the_three_names_are_known() {
        // lab-py: the 36 names of F7_GRID and EXIT_VARIANTS, and the names of other tp:T specs. M1 knows three.
        let m1: Vec<&str> = VARIANTS.iter().map(|v| v.name).collect();
        let mut known = 0;
        for name in PYTHON_NAMES.into_iter().chain(TP_T_NAMES) {
            match variant(name) {
                Ok(v) => {
                    assert!(m1.contains(&name) && v.name == name);
                    known += 1;
                }
                Err(e) => {
                    assert!(!m1.contains(&name));
                    let msg = e.to_string();
                    assert!(msg.contains(name) && msg.contains("M4"), "{msg}");
                }
            }
        }
        assert_eq!(known, 4); // tp4_T600, flow300_H6, flow300_tp100_H6, and the lossy 0.045:600 (named tp4_T600)
        for name in ["0.04:600", "", "TP4_T600", "flow300_h6", "tp4_T600 "] {
            assert!(variant(name).is_err(), "{name:?}");
        }
        let unit = scenario(0);
        let err = rule_for("tp6_T600", &unit, 1, &Clock::default(), None).unwrap_err();
        assert_eq!(err, VariantError::Unknown { variant: "tp6_T600".into() });
    }

    #[test]
    fn a_rule_gets_a_flow_index_exactly_when_it_needs_one() {
        let unit = scenario(0);
        let c = Clock::default();
        let given = rule_for("tp4_T600", &unit, 1, &c, Some(LiveFlowIndex::new(true))).unwrap_err();
        let missing = rule_for("flow300_H6", &unit, 1, &c, None).unwrap_err();
        assert_eq!(given, VariantError::Flows { variant: "tp4_T600".into(), given: true });
        assert_eq!(missing, VariantError::Flows { variant: "flow300_H6".into(), given: false });
        assert!(rule_for("flow300_tp100_H6", &unit, 1, &c, None).is_err());
        assert!(given.to_string().contains("M4") && missing.to_string().contains("flow300_H6"));
    }

    #[test]
    fn flow_names_run_only_in_pool_reversed() {
        // lab-py Group(segments=...) per table name: where Python refuses (null, or with curve) M1 refuses too, and M1
        // takes a flow name only in exactly [pool_reversed]; tp4_T600 runs anywhere.
        for (segments, python_refuses) in SEGMENTS {
            for (v, py) in VARIANTS.iter().zip(python_refuses) {
                let got = check_groups(&[group("G", true, &[v.name], segments)]);
                let refused = got.is_err();
                assert!(!py || refused, "{} {segments:?}: Python refuses, M1 must", v.name);
                assert_eq!(refused, v.kind.needs_flows() && segments != Some([false, false, true]), "{} {segments:?}", v.name);
                if let Err(e) = got {
                    assert_eq!(e, VariantError::FlowSegments { group: "G".into(), variant: v.name.into(), segments });
                    let msg = e.to_string();
                    assert!(msg.contains(v.name) && msg.contains("M4") && msg.contains("pool_reversed"), "{msg}");
                }
            }
        }
        assert!(flow_segments_ok(Some([false, false, true])) && !flow_segments_ok(None) && !flow_segments_ok(Some([false, true, true])));
    }

    #[test]
    fn the_runs_groups_pass_and_names_twice_fail() {
        // lab-py: R1/R2's and the paper start's groups from the engine's loaders and the real yaml files pass; a name
        // twice (across groups or in one) is refused, as Python's Engine refuses it.
        for (run, groups) in RUNS {
            let gs: Vec<GroupSpec> = groups.iter().map(|&(n, p, v, s)| group(n, p, v, s)).collect();
            assert_eq!(check_groups(&gs), Ok(()), "{run}");
        }
        for (label, groups, python_refuses) in DUPLICATES {
            assert!(python_refuses, "{label}");
            let gs: Vec<GroupSpec> = groups.iter().map(|&(n, p, v, s)| group(n, p, v, s)).collect();
            let e = check_groups(&gs).unwrap_err();
            let VariantError::Duplicate { variant } = &e else { panic!("{label}: {e}") };
            assert!(gs.iter().flat_map(|g| &g.variants).filter(|v| *v == variant).count() == 2, "{label}: {e}");
            assert!(e.to_string().contains("M4"), "{e}");
        }
        // An unknown name is refused before a duplicate and before the segments.
        let gs = [group("K3", true, &["tp4_T600"], None), group("H", false, &["tp4_T600", "trail3_T600"], None)];
        assert_eq!(check_groups(&gs), Err(VariantError::Unknown { variant: "trail3_T600".into() }));
    }

    #[test]
    fn a_spec_counts_only_with_m1s_exact_parameters() {
        // lab-py paper.engine.variant: "0.04:600" and "0.040:600" are (0.04, 600) -> tp4_T600; "0.045:600" is also named
        // tp4_T600 but with tp 0.045, so it is refused here; the flow names are their own specs.
        for (spec, name) in [("0.04:600", "tp4_T600"), ("0.040:600", "tp4_T600"), (" 0.04 : 600 ", "tp4_T600"),
                             ("flow300_H6", "flow300_H6"), ("flow300_tp100_H6", "flow300_tp100_H6")] {
            assert_eq!(variant_for_spec(spec).map(|v| v.name), Ok(name), "{spec}");
        }
        for spec in ["0.045:600", "0.0400000000000001:600", "0.04:601", "0.04:600.0", "0.06:600", "0.04:150", "0.04:600:1", "tp4_T600",
                     "flow300_H6_sl8", "tp100_H6", "", ":"] {
            let e = variant_for_spec(spec).unwrap_err();
            assert_eq!(e, VariantError::Spec { spec: spec.to_owned() });
            assert!(e.to_string().contains("M4") && e.to_string().contains("exact"), "{e}");
        }
    }
}
