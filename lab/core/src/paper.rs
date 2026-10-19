//! The paper engine of a replay with risk inert (`--ignore-risk`): SignalEngine's signals plus busy, the pending
//! entries, the positions (PositionSim), the graduations, the costs and the end of data, writing the journal's
//! signals, orders, fills, exits and positions in paper/engine.py's order. M1 C2-4b of the Rust live route; the porting
//! spec is research/m1-c2-positionsim-spec-2026-10-19.md §3.2-§3.12.
//!
//! - `Paper` is SignalEngine's `Hooks`: steps 1, 2 and 2b of `_end_slot` before the history, busy and the Pending per
//!   row past the farm gates, the sweep's keep-set, step 5 after the farm windows. `PaperEngine` adds the quiet entries
//!   between slots (engine.py:310, 353-360) and the end of data (:1289-1308).
//! - The journal's rows come out as plain `Op`s in the engine's write order; the caller writes them (INSERT OR REPLACE
//!   and the positions delete), so C4 can move the writer to its own thread.
//! - Risk (M1 C3-2, research/m1-c3-risk-spec-2026-10-19.md §2): `RiskSource::Ignore` is `--ignore-risk`, every group
//!   trading 0.5 SOL with no limit; `RiskSource::Files` reads each group's risk at every new dated minute and at every
//!   entry, as `_load_risk` does (engine.py:471, 532-537, 869). Before the first dated minute every row past the gates
//!   is skipped with 'not loaded' (:151). The gate (`_risk_block`, :824-848), the 'at entry: …' orders (:868-877), the
//!   size (:878-883), the day's loss and its reset (:455-460, 1285-1287) and the pool cooldown after a total loss
//!   (:1054-1057) follow. C3-3: the heartbeat rows (:461-465, 477-480) and each group's paper wallet (:1252-1283), the
//!   wallet files as `Op::Wallet`s for lab-live to write. The daily close is Python's (spec D3); the restart paths M3.
//! - The curve entry takes the engine's own view instead of a curve history (spec D4): the state after the signal slot
//!   (kept on the Pending), Recent's last leg before the entry, and the seed from the store's curve_hold plus the run's
//!   own per-(mint, trader) sums.
//! - Python's dicts keep insertion order, and step 5, the minute's position puts and the end of data iterate them: the
//!   open positions and the graduations waiting are kept in that order here too (`Ordered`).

use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fmt;

use crate::costs::Scenario;
use crate::exact::{ge, gt, lt};
use crate::exits::{check_groups, rule_for, variant, ExitRule, VariantError};
use crate::flow::LiveFlowIndex;
use crate::legs::{Event, Id, Leg, Order, Venue};
use crate::n2::N2Keys;
use crate::positions::{
    insertion_index, open_curve_entry, open_pool_position, Cfg, Opened, PosError, PosResult, PositionSim, SimVenue, Step, MAX_HOLD, SEED_POOL,
};
use crate::risk::Risk;
use crate::signals::{Family, Hooks, SignalEngine, SignalError, SignalRow, SlotLegs, Status};
use crate::tau::Tau;
use crate::universe::Segment;
use crate::venues::{self as v, Curve, Pool};

/// A jump of more slots between events is a feed gap's heartbeat row (engine.py:75).
const GAP_SLOTS: u64 = 25;
/// Every order's size under ignore-risk: `int(g.risk.size_sol * SOL)` with Risk(0.5, ...) (engine.py:882-883); and every
/// N2 order's with risk on, `int(0.5 * SOL)` (:881).
pub const SIZE: i128 = 500_000_000;
/// The engine's slippage tolerance (engine.py:216 `tol=0.05`).
pub const TOL: f64 = 0.05;

// ---------------------------------------------------------------------------------------------- inputs

/// What a graduation needs to know (spec D7): per token its complete_slot and pool (feed.py TokenInfo), per SOL market
/// (quote_class 'sol' or 'reversed', as ReplayFeed loads them, feed.py:96-99) its create_slot.
#[derive(Clone, Debug, Default)]
pub struct GradInfo {
    tokens: HashMap<Id, (Option<u64>, Option<Id>)>,
    pools: HashMap<Id, Option<u64>>,
}

impl GradInfo {
    pub fn add_token(&mut self, mint: Id, complete_slot: Option<u64>, pool: Option<Id>) {
        self.tokens.insert(mint, (complete_slot, pool));
    }

    pub fn add_pool(&mut self, pool: Id, create_slot: Option<u64>) {
        self.pools.insert(pool, create_slot);
    }
}

/// The run's settings beside the signal engine's.
#[derive(Clone, Debug)]
pub struct PaperCfg {
    /// q per group, in the signal engine's group order (the primary first).
    pub q: Vec<f64>,
    /// The journal's state 'cost_scenario' (Scenario.from_cfg("base"), engine.py:255).
    pub scn: Scenario,
    pub tau: Tau,
    pub tol: f64,
    pub grad: GradInfo,
    /// The store's curve_hold before the run: mint -> its non-zero (trader, net tokens) (spec §4.1).
    pub curve_hold: HashMap<Id, Vec<(Id, i128)>>,
}

impl PaperCfg {
    /// The reference's settings: tau 'empirical' and tol 0.05 (engine.py:216).
    pub fn new(q: Vec<f64>, scn: Scenario, grad: GradInfo, curve_hold: HashMap<Id, Vec<(Id, i128)>>) -> Self {
        PaperCfg { q, scn, tau: Tau::Empirical, tol: TOL, grad, curve_hold }
    }
}

/// Where the groups' risk comes from (spec §3).
pub enum RiskSource {
    /// `--ignore-risk`: `Risk::ignore()` for every group, loaded at the first dated minute; no re-read at the entry.
    Ignore,
    /// Each group's risk file, read at every new dated minute and at every entry (all groups each time, as
    /// `_load_risk`): the group's index in the signal engine's order, to risk.py `load`'s `(Risk, None)` or
    /// `(None, reason)`. lab-live reads the files (lab/live/src/risk.rs); lab-core reads none.
    Files(Box<dyn FnMut(usize) -> Result<Risk, String>>),
}

impl fmt::Debug for RiskSource {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            RiskSource::Ignore => "RiskSource::Ignore",
            RiskSource::Files(_) => "RiskSource::Files",
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum PaperError {
    /// The groups name a variant M1 does not run, or run it where M1 does not (exits.rs, spec §1.6).
    Variant(VariantError),
    /// A setting the reference never has: d = 0, a q outside [0, 1], a q per group missing.
    Config(String),
    Signal(SignalError),
    Position(PosError),
}

impl fmt::Display for PaperError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PaperError::Variant(e) => write!(f, "paper: {e}"),
            PaperError::Config(s) => write!(f, "paper: {s}"),
            PaperError::Signal(e) => write!(f, "paper: {e}"),
            PaperError::Position(e) => write!(f, "paper: {e}"),
        }
    }
}

impl std::error::Error for PaperError {}

impl From<SignalError> for PaperError {
    fn from(e: SignalError) -> Self {
        PaperError::Signal(e)
    }
}

// ---------------------------------------------------------------------------------------------- the rows

/// A position's journal key (family, variant, key, t).
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct PosKey {
    pub family: Family,
    pub variant: String,
    pub key: Id,
    pub t: u64,
}

/// An exits row (journal.py:41-46), as `_settle_if_done` (engine.py:1036-1041) or a failed entry (:932-936) writes it.
#[derive(Clone, Debug, PartialEq)]
pub struct ExitRow {
    pub k: PosKey,
    pub day: Option<String>,
    pub entry_slot: u64,
    pub exit_decision_slot: Option<u64>,
    pub exit_slot: Option<u64>,
    pub exit_reason: Option<&'static str>,
    pub tokens: i128,
    pub cost: i128,
    pub proceeds: i128,
    pub pnl: i128,
    pub ret: Option<f64>,
    pub exit_attempts: u64,
    pub cf_graduation: bool,
    pub seed_pool: bool,
    pub vault_capped: bool,
    pub reverted_direct: u64,
    pub reverted_router: u64,
    pub sells_dropped: u64,
    pub sells_scaled: u64,
    /// (peak_slot, peak_mark), NULL without marks (engine.py `_peak`).
    pub peak: Option<(u64, i128)>,
    /// The exit rule's windows (engine.py `_windows`): n_flow (NULL for tp:T) and the time cap.
    pub n_flow_slots: Option<u64>,
    pub cap_slots: u64,
}

/// A heartbeat row (journal.py heartbeat; engine.py:461-465, 477-480): a new dated minute's, or a feed gap's (note
/// "feed gap a..b").
#[derive(Clone, Debug, PartialEq)]
pub struct Heartbeat {
    pub minute: i64,
    pub day: Option<String>,
    pub last_slot: Option<u64>,
    /// The last event seen, "slot:tx:outer:inner".
    pub last_event: Option<Order>,
    pub events: u64,
    pub open_positions: usize,
    pub gap_slots: u64,
    pub note: String,
}

/// One paper wallet transaction (engine.py `_wallet_tx`, :1252-1265); the other fields are constants (signature,
/// time_unix, symbol and tip null, status "paper").
#[derive(Clone, Debug, PartialEq)]
pub struct WalletTx {
    pub slot: u64,
    pub kind: &'static str,
    pub mint: Id,
    pub amount_lamports: i128,
    pub fee_lamports: i128,
    pub landed_slot_delta: Option<u64>,
    pub family: Family,
    pub variant: String,
}

/// A group's wallet file as `_write_wallet` writes it whole (engine.py:1267-1283).
#[derive(Clone, Debug, PartialEq)]
pub struct WalletFile {
    /// The group's index in the signal engine's order (lab-live names its file).
    pub group: usize,
    pub slot: Option<u64>,
    pub time_unix: Option<i64>,
    pub balance_lamports: i128,
    /// Newest first, at most 50.
    pub txs: Vec<WalletTx>,
}

/// A group's paper wallet: 10 SOL at the start, the realized P&L and the last 50 transactions (engine.py Group, :154).
#[derive(Clone, Debug, Default)]
struct Wallet {
    realized: i128,
    txs: VecDeque<WalletTx>,
    dirty: bool,
}

/// The paper wallet's start (engine.py:154, `int(10 * SOL)`) and depth (`deque(maxlen=50)`).
pub const WALLET_START: i128 = 10_000_000_000;
const WALLET_DEPTH: usize = 50;

/// One journal write, in the engine's order: a put (INSERT OR REPLACE) of a row of signals, orders, fills, exits,
/// positions or heartbeat, or the delete of a position (journal.py:83-89); and the wallet files, written whole.
#[derive(Clone, Debug, PartialEq)]
pub enum Op {
    Signal(SignalRow, Status),
    Order { k: PosKey, day: Option<String>, entry_slot: u64, size: i128, status: &'static str, reason: Option<String> },
    Fill { k: PosKey, side: &'static str, day: Option<String>, slot: Option<u64>, tokens: i128, quote: i128 },
    Exit(Box<ExitRow>),
    /// status 'open'; mark: (mark_proceeds, mark_slot, mark_pnl) of the last mark, NULL without one.
    PutPosition { k: PosKey, entry_slot: u64, cost: i128, tokens: i128, mark: Option<(i128, u64, i128)> },
    DeletePosition(PosKey),
    Heartbeat(Box<Heartbeat>),
    Wallet(Box<WalletFile>),
}

/// A curve entry's holdings right after the fill (`Holdings.seed(legs[:i])`): (trader, hist, cf) by trader. The seed
/// proof (C2-6) hashes them against the replay's.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SeedTrace {
    pub k: PosKey,
    pub holdings: Vec<(Id, i128, i128)>,
}

/// The run's counters for the summary and the measure line.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PaperCounts {
    /// Rows past the farm gates: entered, 'busy', 'not loaded'.
    pub entered: u64,
    pub busy: u64,
    pub not_loaded: u64,
    /// Rows the risk gate skipped, and 'at entry: …' orders, by "<group>: <reason>".
    pub risk_skips: BTreeMap<String, u64>,
    pub at_entry: BTreeMap<String, u64>,
    /// Orders per status, exits per reason.
    pub orders: BTreeMap<&'static str, u64>,
    pub exits: BTreeMap<&'static str, u64>,
    /// Curve orders, and those whose mint has a curve_hold seed from before the run.
    pub curve_entries: u64,
    pub curve_seeded: u64,
    /// Graduations sold into the standard seed pool (no pool, at the end of data) and into their own pool.
    pub seed_pool_sales: u64,
    pub pool_sales: u64,
    pub max_open: usize,
}

// ---------------------------------------------------------------------------------------------- the state

/// A Pending entry (engine.py `Pending`, :190-201) with the curve's state after the signal slot (spec D4).
#[derive(Clone, Debug)]
struct Pending {
    family: Family,
    group: usize,
    vi: usize,
    variant: String,
    key: Id,
    seg: Segment,
    t: u64,
    entry_slot: u64,
    s_dec: Option<Curve>,
}

impl Pending {
    fn k(&self) -> PosKey {
        PosKey { family: self.family, variant: self.variant.clone(), key: self.key, t: self.t }
    }

    fn busy(&self) -> BusyKey {
        (self.family, self.group, self.vi, self.key)
    }
}

/// A graduation waiting for its pool: the pool and its create slot (the sale is at open + 1).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Grad {
    pool: Id,
    open_slot: u64,
}

/// An open position (engine.py `Open`, :203-211). `sim` is None when our own buy completed the curve; its Result is
/// then `r`.
#[derive(Debug)]
struct OpenPos {
    p: Pending,
    sim: Option<Box<PositionSim>>,
    r: Option<PosResult>,
    opened: u64,
    grad: Option<Grad>,
    /// (n_flow_slots, cap_slots) of the exit rule.
    windows: (Option<u64>, u64),
}

impl OpenPos {
    fn r(&self) -> &PosResult {
        match &self.sim {
            Some(s) => s.result(),
            None => self.r.as_ref().expect("a position without a PositionSim keeps its Result"),
        }
    }

    fn r_mut(&mut self) -> &mut PosResult {
        match &mut self.sim {
            Some(s) => s.result_mut(),
            None => self.r.as_mut().expect("a position without a PositionSim keeps its Result"),
        }
    }

    fn done(&self) -> bool {
        self.sim.as_ref().is_some_and(|s| s.is_done())
    }
}

/// busy's key: (family, group, variant index, key); variant names are distinct across groups (engine.py:237-239).
type BusyKey = (Family, usize, usize, Id);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Busy {
    /// 'open': a Pending or an open position.
    Open,
    /// The last exit's slot (or t for a skipped, failed or slot-less exit): t <= it is busy.
    Until(u64),
}

/// A Python dict of key -> list of positions: keys in insertion order, a key deleted when its list empties and put at
/// the end when it comes back (engine.py `open`, `grad_wait`, `_drop`).
#[derive(Clone, Debug, Default)]
struct Ordered {
    seq: u64,
    order: BTreeMap<u64, Id>,
    map: HashMap<Id, (u64, Vec<usize>)>,
}

impl Ordered {
    fn push(&mut self, key: Id, id: usize) {
        let seq = &mut self.seq;
        let order = &mut self.order;
        let e = self.map.entry(key).or_insert_with(|| {
            *seq += 1;
            order.insert(*seq, key);
            (*seq, Vec::new())
        });
        e.1.push(id);
    }

    fn get(&self, key: Id) -> Option<&Vec<usize>> {
        self.map.get(&key).map(|e| &e.1)
    }

    fn contains(&self, key: Id) -> bool {
        self.map.contains_key(&key)
    }

    /// Removes the position from the key's list; the key goes when its list is empty. True when it was there.
    fn remove(&mut self, key: Id, id: usize) -> bool {
        let Some(e) = self.map.get_mut(&key) else { return false };
        let Some(i) = e.1.iter().position(|&x| x == id) else { return false };
        e.1.remove(i);
        if e.1.is_empty() {
            self.drop_key(key);
        }
        true
    }

    fn drop_key(&mut self, key: Id) {
        if let Some((seq, _)) = self.map.remove(&key) {
            self.order.remove(&seq);
        }
    }

    /// `list(d.items())`: the keys and their lists as they are now.
    fn snapshot(&self) -> Vec<(Id, Vec<usize>)> {
        self.order.values().map(|k| (*k, self.map[k].1.clone())).collect()
    }

    fn clear(&mut self) {
        self.order.clear();
        self.map.clear();
    }

    #[cfg(test)]
    fn positions(&self) -> usize {
        self.map.values().map(|e| e.1.len()).sum()
    }
}

/// The paper engine's own state: the hooks on SignalEngine.
#[derive(Debug)]
pub struct Paper {
    cfg: PaperCfg,
    pos: Cfg,
    /// The primary group's q: the graduation sale's insertion point (engine.py:1008, `self.q`).
    primary_q: f64,
    busy: HashMap<BusyKey, Busy>,
    pending: BTreeMap<u64, Vec<Pending>>,
    /// The keys of the Pendings, the open positions' keys waiting for a graduation, and their counts: the sweep's
    /// keep-set (engine.py:677-678).
    pending_keys: HashMap<Id, u32>,
    grad_keys: HashMap<Id, u32>,
    opens: HashMap<usize, OpenPos>,
    next_id: usize,
    open: Ordered,
    grad_wait: Ordered,
    /// The run's curve legs per mint: trader -> net tokens (spec §3.5).
    run_sums: HashMap<Id, HashMap<Id, i128>>,
    risk_src: RiskSource,
    /// Per group (the signal engine's order): its risk or the reason it has none ('not loaded' before the first dated
    /// minute), its day's realized loss (signed: a gain lowers it), and its pool cooldowns: key -> the slot before which
    /// no new entry (engine.py Group, :150-153).
    risk: Vec<Result<Risk, String>>,
    day_loss: Vec<i128>,
    cool: Vec<HashMap<Id, u64>>,
    day: Option<String>,
    wallets: Vec<Wallet>,
    /// The events the engine has seen and the last one (engine.py run, :319-320), for the heartbeat.
    pub(crate) events: u64,
    pub(crate) last_event: Option<Order>,
    minute: Option<i64>,
    last_slot: Option<u64>,
    pub ops: Vec<Op>,
    pub counts: PaperCounts,
    /// Some: each curve entry's holdings after the fill (`lab-live paper --trace`).
    pub seeds: Option<Vec<SeedTrace>>,
    error: Option<PaperError>,
}

impl Paper {
    fn new(sig: &SignalEngine, cfg: PaperCfg, risk_src: RiskSource) -> Result<Paper, PaperError> {
        check_groups(&sig.groups).map_err(PaperError::Variant)?;
        let config = |s: String| Err(PaperError::Config(s));
        if cfg.q.len() != sig.groups.len() {
            return config(format!("{} groups but {} q values", sig.groups.len(), cfg.q.len()));
        }
        for (g, q) in sig.groups.iter().zip(&cfg.q) {
            if g.d_ms.is_none() && g.d == 0 {
                // pending[t] would be filled after step 2 popped it, then entered late with no legs (spec §3.3)
                return config(format!("group {:?}: d = 0 is refused (no reference runs it)", g.name));
            }
            if !(0.0..=1.0).contains(q) {
                return config(format!("group {:?}: q {q} outside [0, 1]", g.name));
            }
        }
        let primary_q = cfg.q[sig.groups.iter().position(|g| g.primary).expect("SignalEngine has one primary group")];
        let pos = Cfg { tol: cfg.tol, tau: cfg.tau, max_hold: MAX_HOLD, window_end: None, mayhem_agent: sig.universe.mayhem_agent };
        let n = sig.groups.len();
        Ok(Paper {
            cfg,
            pos,
            primary_q,
            busy: HashMap::new(),
            pending: BTreeMap::new(),
            pending_keys: HashMap::new(),
            grad_keys: HashMap::new(),
            opens: HashMap::new(),
            next_id: 0,
            open: Ordered::default(),
            grad_wait: Ordered::default(),
            run_sums: HashMap::new(),
            risk_src,
            risk: vec![Err("not loaded".to_owned()); n],
            day_loss: vec![0; n],
            cool: vec![HashMap::new(); n],
            day: None,
            wallets: vec![Wallet::default(); n],
            events: 0,
            last_event: None,
            minute: None,
            last_slot: None,
            ops: Vec::new(),
            counts: PaperCounts::default(),
            seeds: None,
            error: None,
        })
    }

    /// Open positions now (graduations waiting included).
    pub fn open_positions(&self) -> usize {
        self.opens.len()
    }

    /// Pending entries now.
    pub fn pending(&self) -> usize {
        self.pending.values().map(Vec::len).sum()
    }

    fn take_error(&mut self) -> Result<(), PaperError> {
        self.error.take().map_or(Ok(()), Err)
    }

    // -- risk

    fn ignore_risk(&self) -> bool {
        matches!(self.risk_src, RiskSource::Ignore)
    }

    /// `_load_risk` (engine.py:532-537): every group's risk, read again.
    fn load_risk(&mut self) {
        match &mut self.risk_src {
            RiskSource::Ignore => self.risk.iter_mut().for_each(|r| *r = Ok(Risk::ignore())),
            RiskSource::Files(read) => {
                for (g, r) in self.risk.iter_mut().enumerate() {
                    *r = read(g);
                }
            }
        }
    }

    /// `_risk_block(family, key, t, group)` (engine.py:824-848): the group's limits, in their order, or None.
    fn risk_block(&self, family: Family, key: Id, t: u64, group: usize) -> Option<String> {
        let r = match &self.risk[group] {
            Err(reason) => return Some(reason.clone()),
            Ok(r) => r,
        };
        if r.kill_switch {
            return Some("kill switch".to_owned());
        }
        if is_n2(family) {
            return None; // the shadow control is not limited (except by the kill switch)
        }
        if !r.families.iter().any(|f| f == family.as_str()) {
            return Some("family not enabled".to_owned());
        }
        if self.cool[group].get(&key).is_some_and(|&c| c > t) {
            return Some("pool cooldown".to_owned());
        }
        let opens: Vec<&OpenPos> = self.opens.values().filter(|o| o.p.group == group && !is_n2(o.p.family)).collect();
        let pend = self.pending.values().flatten().filter(|p| p.group == group && !is_n2(p.family)).count();
        if (opens.len() + pend) as i64 >= r.max_open_positions {
            return Some("max open positions".to_owned());
        }
        let exposure: i128 = opens.iter().map(|o| o.r().cost).sum::<i128>() + r.size_lamports() * (pend as i128 + 1);
        if gt(exposure, r.max_exposure_sol * 1e9) {
            return Some("max exposure".to_owned());
        }
        if ge(-self.day_loss[group], r.daily_loss_limit_sol * 1e9) {
            return Some("daily loss limit".to_owned());
        }
        None
    }

    /// The order's size (engine.py:878-883): the file's size_sol in lamports for F7, 0.5 SOL for N2; 0.5 SOL under
    /// ignore-risk.
    fn size(&self, p: &Pending) -> i128 {
        match &self.risk[p.group] {
            Ok(r) if !self.ignore_risk() && !is_n2(p.family) => r.size_lamports(),
            _ => SIZE,
        }
    }

    /// `_book_loss` (engine.py:1285-1287): N2 books nothing.
    fn book_loss(&mut self, p: &Pending, pnl: i128) {
        if !is_n2(p.family) {
            self.day_loss[p.group] += pnl;
        }
    }

    /// The pool cooldown of `_exit_effects` (engine.py:1053-1057): a total loss (tokens held, proceeds under 10 % of the
    /// venue cost) closes the key for the group from the exit slot (or the settle slot) for pool_cooldown_h, in the
    /// clock's slots at the settle slot.
    fn cooldown(&mut self, eng: &SignalEngine, p: &Pending, r: (Option<u64>, i128, i128, i128), slot: u64) {
        let (exit_slot, tokens, proceeds, cost) = r;
        let Ok(risk) = &self.risk[p.group] else { return };
        if is_n2(p.family) || risk.pool_cooldown_h <= 0.0 || tokens == 0 || !lt(proceeds, 0.1 * cost as f64) {
            return;
        }
        let end = exit_slot.filter(|&s| s != 0).unwrap_or(slot) + eng.clock.slots(risk.pool_cooldown_h * 3_600_000.0, slot);
        let c = self.cool[p.group].entry(p.key).or_insert(0);
        *c = (*c).max(end);
    }

    // -- the paper wallets and the heartbeat

    /// `_wallet_tx(p, kind, slot, amount, settled)` (engine.py:1252-1265): F7 only, newest first, 50 deep.
    fn wallet_tx(&mut self, p: &Pending, kind: &'static str, slot: u64, amount: i128, settled: Option<i128>) {
        if is_n2(p.family) {
            return;
        }
        let fee = self.cfg.scn.per_tx();
        let w = &mut self.wallets[p.group];
        if let Some(s) = settled {
            w.realized += s;
        }
        w.txs.push_front(WalletTx {
            slot,
            kind,
            mint: p.key,
            amount_lamports: amount,
            fee_lamports: fee,
            landed_slot_delta: (kind == "buy").then_some(p.entry_slot - p.t),
            family: p.family,
            variant: p.variant.clone(),
        });
        w.txs.truncate(WALLET_DEPTH);
        w.dirty = true;
    }

    /// `_write_wallet(block_time)` (engine.py:1267-1283): each group whose wallet changed, its balance (10 SOL + the
    /// realized P&L - its open F7 positions at their entry total) at the last ended slot.
    fn write_wallets(&mut self, block_time: Option<i64>) {
        for g in 0..self.wallets.len() {
            if !self.wallets[g].dirty {
                continue;
            }
            let open_cost: i128 =
                self.opens.values().filter(|o| o.p.group == g && !is_n2(o.p.family)).map(|o| self.cfg.scn.entry_total(o.r().cost)).sum();
            let w = &mut self.wallets[g];
            self.ops.push(Op::Wallet(Box::new(WalletFile {
                group: g,
                slot: self.last_slot,
                time_unix: block_time,
                balance_lamports: WALLET_START + w.realized - open_cost,
                txs: w.txs.iter().cloned().collect(),
            })));
            w.dirty = false;
        }
    }

    fn heartbeat(&mut self, eng: &SignalEngine, minute: i64, gap_slots: u64, note: String) {
        self.ops.push(Op::Heartbeat(Box::new(Heartbeat {
            minute,
            day: eng.day.clone(),
            last_slot: self.last_slot,
            last_event: self.last_event,
            events: self.events,
            open_positions: self.opens.len(),
            gap_slots,
            note,
        })));
    }

    fn inc(m: &mut HashMap<Id, u32>, k: Id) {
        *m.entry(k).or_default() += 1;
    }

    fn dec(m: &mut HashMap<Id, u32>, k: Id) {
        let n = m.get_mut(&k).expect("a counted key");
        *n -= 1;
        if *n == 0 {
            m.remove(&k);
        }
    }

    /// The exit rule `_enter` builds (engine.py:884-890): a flow kind gets the position's own LiveFlowIndex.
    fn rule(&self, eng: &SignalEngine, p: &Pending) -> ExitRule {
        let kind = variant(&p.variant).expect("check_groups passed every name").kind;
        let flows = kind.needs_flows().then(|| LiveFlowIndex::new(p.seg.reversed()));
        rule_for(&p.variant, &self.cfg.scn, p.t, &eng.clock, flows).expect("a rule of M1's table")
    }

    // -- entries

    /// `_quiet_entries(before)` (engine.py:353-360): entries due in slots without an event, on the state they have.
    fn quiet_entries(&mut self, eng: &SignalEngine, before: u64) {
        let due: Vec<u64> = self.pending.range(..before).map(|(&e, _)| e).collect();
        for e in due {
            for p in self.pending.remove(&e).unwrap_or_default() {
                Self::dec(&mut self.pending_keys, p.key);
                self.enter(eng, p, e, &[]);
            }
        }
    }

    /// `_enter(p, slot, slot_legs)` (engine.py:863-959), a replay's path (no forced orders, M3).
    fn enter(&mut self, eng: &SignalEngine, p: Pending, slot: u64, slot_legs: &[Leg]) {
        if !self.ignore_risk() {
            // The file can change between the signal and the entry: read it again, and enter only with a valid file and
            // the kill switch off (N2 included); the limits are not checked again (engine.py:868-877).
            self.load_risk();
            let why = match &self.risk[p.group] {
                Err(reason) => Some(reason.clone()),
                Ok(r) if r.kill_switch => Some("kill switch".to_owned()),
                Ok(_) => None,
            };
            if let Some(why) = why {
                *self.counts.orders.entry("skipped").or_default() += 1;
                *self.counts.at_entry.entry(format!("{}: {why}", eng.groups[p.group].name)).or_default() += 1;
                self.ops.push(Op::Order { k: p.k(), day: eng.day.clone(), entry_slot: slot, size: 0, status: "skipped",
                                          reason: Some(format!("at entry: {why}")) });
                self.busy.insert(p.busy(), Busy::Until(p.t));
                return;
            }
        }
        let size = self.size(&p);
        let mut rule = self.rule(eng, &p);
        let windows = (rule.n_flow(), rule.max_slots());
        let (d, q) = (p.entry_slot - p.t, self.cfg.q[p.group]);
        let opened = match p.seg {
            Segment::Curve => {
                self.counts.curve_entries += 1;
                let hold = self.cfg.curve_hold.get(&p.key);
                self.counts.curve_seeded += u64::from(hold.is_some_and(|h| !h.is_empty()));
                let before = eng.recent.back(p.key).map(curve_post);
                let run = self.run_sums.get(&p.key);
                let seed = hold.into_iter().flatten().copied().chain(run.into_iter().flatten().map(|(&t, &n)| (t, n)));
                open_curve_entry(p.s_dec, before, slot_legs, seed, p.t, size, d, q, rule, self.pos)
                    .map(|o| (o, 0, slot_legs.to_vec()))
            }
            seg => {
                // the pool's Recent legs and the entry slot's (engine.py:902). Its state leg (:903-905) never applies:
                // Recent is the last state here, so an empty Recent has none (spec §3.4).
                let mut legs: Vec<Leg> = eng.recent.get(p.key).map_or_else(Vec::new, |dq| dq.iter().copied().collect());
                let a = legs.len();
                legs.extend_from_slice(slot_legs);
                if let Some(f) = rule.flows_mut() {
                    f.add(&legs); // engine.py:906-907
                }
                let venue = if seg.reversed() { SimVenue::PoolReversed } else { SimVenue::PoolNormal };
                open_pool_position(venue, &legs, None, p.t, size, d, q, rule, self.pos).map(|o| (o, a, legs))
            }
        };
        let (opened, a, legs) = match opened {
            Ok(x) => x,
            Err(e) => {
                self.error.get_or_insert(PaperError::Position(e));
                return;
            }
        };
        let (k, day) = (p.k(), eng.day.clone());
        let order = |status, reason: Option<&str>| Op::Order { k: k.clone(), day: day.clone(), entry_slot: slot, size, status,
                                                              reason: reason.map(str::to_owned) };
        let (sim, i, r) = match opened {
            Opened::No(r) => {
                let status = if r.skipped { "skipped" } else { "entry_failed" };
                *self.counts.orders.entry(status).or_default() += 1;
                let reason = r.exit_reason.map(|x| x.as_str());
                self.ops.push(order(status, reason));
                if r.entry_failed {
                    let s = self.cfg.scn.settle(true, 0, 0, 0);
                    self.book_loss(&p, s.pnl); // the failed transaction's fee (engine.py:931)
                    self.wallet_tx(&p, "buy", slot, s.pnl, Some(s.pnl));
                    *self.counts.exits.entry(reason.unwrap_or("")).or_default() += 1;
                    self.ops.push(Op::Exit(Box::new(ExitRow {
                        k: k.clone(),
                        day: day.clone(),
                        entry_slot: slot,
                        exit_decision_slot: None,
                        exit_slot: None,
                        exit_reason: reason,
                        tokens: 0,
                        cost: 0,
                        proceeds: 0,
                        pnl: s.pnl,
                        ret: None,
                        exit_attempts: 0,
                        cf_graduation: false,
                        seed_pool: false,
                        vault_capped: false,
                        reverted_direct: 0,
                        reverted_router: 0,
                        sells_dropped: 0,
                        sells_scaled: 0,
                        peak: None,
                        n_flow_slots: windows.0,
                        cap_slots: windows.1,
                    })));
                }
                self.busy.insert(p.busy(), Busy::Until(p.t));
                return;
            }
            Opened::Completed(r) => (None, None, Some(r)),
            Opened::Open(sim, i) => (Some(sim), Some(i), None),
        };
        *self.counts.orders.entry("filled").or_default() += 1;
        self.ops.push(order("filled", None));
        let (tokens, cost) = sim.as_ref().map_or_else(|| r.as_ref().map(|r| (r.tokens, r.cost)).unwrap(), |s| (s.result().tokens, s.result().cost));
        self.ops.push(Op::Fill { k: k.clone(), side: "buy", day, slot: Some(slot), tokens, quote: cost });
        if let (Some(seeds), Some(s)) = (self.seeds.as_mut(), sim.as_ref().filter(|s| s.venue() == SimVenue::Curve)) {
            seeds.push(SeedTrace { k: k.clone(), holdings: s.holdings().sorted() });
        }
        let id = self.next_id;
        self.next_id += 1;
        let key = p.key;
        let mut o = OpenPos { p, sim, r, opened: slot, grad: None, windows };
        if o.sim.is_none() {
            // our own buy completed the curve: on_complete at the entry slot (replay.py:666-669)
            let grad = on_complete(&self.cfg.grad, &mut self.counts, o.p.key, o.r_mut(), slot);
            o.grad = grad;
            self.opens.insert(id, o);
            if let Some(g) = grad {
                self.grad_wait.push(g.pool, id);
                Self::inc(&mut self.grad_keys, key);
            }
            self.put_position(id);
            let p = self.opens[&id].p.clone();
            self.wallet_tx(&p, "buy", slot, -self.cfg.scn.entry_total(cost), None); // engine.py:948
            if grad.is_some() {
                self.open.push(key, id);
            } else {
                self.settle_if_done(eng, id, true, slot);
            }
            self.counts.max_open = self.counts.max_open.max(self.opens.len());
            return;
        }
        self.opens.insert(id, o);
        self.put_position(id);
        let p = self.opens[&id].p.clone();
        self.wallet_tx(&p, "buy", slot, -self.cfg.scn.entry_total(cost), None); // engine.py:948
        self.open.push(key, id);
        self.counts.max_open = self.counts.max_open.max(self.opens.len());
        let i = i.expect("an open position has its insertion index");
        if i < legs.len() {
            // the entry slot's legs after ours (engine.py:956-958)
            let step = self.sim_mut(id).on_slot(slot, &legs[a..], i - a);
            self.after_step(id, step);
        }
        self.settle_if_done(eng, id, false, slot);
    }

    fn sim_mut(&mut self, id: usize) -> &mut PositionSim {
        self.opens.get_mut(&id).and_then(|o| o.sim.as_deref_mut()).expect("an open position with a PositionSim")
    }

    /// A completion inside the position's feed: the engine's on_complete (engine.py:977-1000).
    fn after_step(&mut self, id: usize, step: Step) {
        if let Step::Completed { slot } = step {
            let o = self.opens.get_mut(&id).expect("an open position");
            let key = o.p.key;
            let grad = on_complete(&self.cfg.grad, &mut self.counts, key, o.r_mut(), slot);
            if let Some(g) = grad {
                o.grad = Some(g);
                self.grad_wait.push(g.pool, id);
                Self::inc(&mut self.grad_keys, key);
            }
        }
    }

    // -- exits

    /// `_put_position` (engine.py:1242-1250): the position with its last mark after the exit's costs.
    fn put_position(&mut self, id: usize) {
        let o = &self.opens[&id];
        let r = o.r();
        let scn = &self.cfg.scn;
        let mark = r.last_mark.map(|(slot, m)| {
            let net = scn.exit_net(m, true);
            (net, slot, net - scn.entry_total(r.cost))
        });
        self.ops.push(Op::PutPosition { k: o.p.k(), entry_slot: r.entry_slot, cost: r.cost, tokens: r.tokens, mark });
    }

    /// `_settle_if_done` (engine.py:1025-1043): a closed position's sell fill and exit, the positions delete, busy, and
    /// the position leaves the open ones. A graduation waiting for its pool is not settled yet.
    fn settle_if_done(&mut self, eng: &SignalEngine, id: usize, force: bool, slot: u64) {
        let o = &self.opens[&id];
        if (!force && !o.done()) || o.grad.is_some() {
            return;
        }
        let (r, k) = (o.r(), o.p.k());
        let s = self.cfg.scn.settle(r.entry_failed, r.cost, r.proceeds, i128::from(r.exit_attempts));
        let day = eng.day.clone();
        let reason = r.exit_reason.map(|x| x.as_str());
        self.ops.push(Op::Fill { k: k.clone(), side: "sell", day: day.clone(), slot: r.exit_slot, tokens: r.tokens, quote: r.proceeds });
        let row = ExitRow {
            k: k.clone(),
            day,
            entry_slot: r.entry_slot,
            exit_decision_slot: r.exit_decision_slot,
            exit_slot: r.exit_slot,
            exit_reason: reason,
            tokens: r.tokens,
            cost: r.cost,
            proceeds: r.proceeds,
            pnl: s.pnl,
            ret: s.ret,
            exit_attempts: r.exit_attempts,
            cf_graduation: r.cf_graduation,
            seed_pool: r.seed_pool,
            vault_capped: r.vault_capped,
            reverted_direct: r.reverted_direct,
            reverted_router: r.reverted_router,
            sells_dropped: r.sells_dropped,
            sells_scaled: r.sells_scaled,
            peak: r.peak,
            n_flow_slots: o.windows.0,
            cap_slots: o.windows.1,
        };
        let until = r.exit_slot.unwrap_or(o.p.t);
        let busy = o.p.busy();
        let (p, effect) = (o.p.clone(), (r.exit_slot, r.tokens, r.proceeds, r.cost));
        *self.counts.exits.entry(reason.unwrap_or("")).or_default() += 1;
        self.ops.push(Op::Exit(Box::new(row)));
        self.ops.push(Op::DeletePosition(k));
        // `_exit_effects` (engine.py:1046-1058): the day's loss, the pool cooldown, busy (the wallet is C3-3)
        self.book_loss(&p, s.pnl);
        let (exit_slot, tokens, _, cost) = effect;
        let amount = if tokens != 0 { s.pnl + self.cfg.scn.entry_total(cost) } else { s.pnl };
        self.wallet_tx(&p, "sell", exit_slot.filter(|&x| x != 0).unwrap_or(slot), amount, Some(s.pnl)); // engine.py:1052
        self.cooldown(eng, &p, effect, slot);
        self.busy.insert(busy, Busy::Until(until));
        self.drop_position(id);
    }

    /// `_drop` (engine.py:1060-1071): out of the open ones and out of any graduation's wait.
    fn drop_position(&mut self, id: usize) {
        let o = self.opens.remove(&id).expect("an open position");
        self.open.remove(o.p.key, id);
        for (pool, ids) in self.grad_wait.snapshot() {
            if ids.contains(&id) && self.grad_wait.remove(pool, id) {
                Self::dec(&mut self.grad_keys, o.p.key);
            }
        }
    }

    /// `_graduation_exits(pool, slot, legs)` (engine.py:1002-1023): the waiting positions whose pool has opened sell
    /// at open + 1 in it, with pool_sell_capped (orientation-blind, as Python), at the primary group's q when the sale
    /// slot is the target and has legs, else on the pool's last state.
    fn graduation_exits(&mut self, eng: &SignalEngine, pool: Id, slot: u64, legs: &[Leg]) {
        let ids = self.grad_wait.get(pool).cloned().unwrap_or_default();
        for id in ids {
            let g = self.opens[&id].grad.expect("a position waiting for its pool");
            let target = g.open_slot + 1;
            if slot < target {
                continue;
            }
            let last = || eng.recent.back(pool).map(pool_post);
            let pstate = if slot == target && !legs.is_empty() {
                let idx = insertion_index(legs, target, self.primary_q);
                if idx > 0 { Some(pool_post(&legs[idx - 1])) } else { last() }
            } else {
                last()
            };
            let o = self.opens.get_mut(&id).expect("an open position");
            let key = o.p.key;
            let r = o.r_mut();
            r.exit_decision_slot = Some(g.open_slot);
            r.exit_slot = Some(target);
            let f = pstate.and_then(|s| v::pool_sell_capped(&s, r.tokens));
            r.proceeds = f.map_or(0, |f| f.trader_quote);
            r.vault_capped = f.is_some_and(|f| f.tokens < r.tokens);
            r.exit_attempts += 1;
            o.grad = None;
            if self.grad_wait.remove(pool, id) {
                Self::dec(&mut self.grad_keys, key);
            }
            self.counts.pool_sales += 1;
            self.settle_if_done(eng, id, true, slot);
        }
        if self.grad_wait.get(pool).is_none_or(|v| v.is_empty()) {
            self.grad_wait.drop_key(pool);
        }
    }

    /// `_end_of_data` (engine.py:1289-1308): the entries left, every open position ended, every graduation still
    /// waiting sold into the standard seed pool at (its decision slot, or the last slot) + 1.
    fn end_of_data(&mut self, eng: &SignalEngine) {
        self.quiet_entries(eng, u64::MAX);
        let last = self.last_slot.unwrap_or(0);
        for (_, ids) in self.open.snapshot() {
            for id in ids {
                let Some(o) = self.opens.get_mut(&id) else { continue };
                let Some(sim) = o.sim.as_deref_mut() else { continue }; // waiting for its pool: below
                let step = sim.end();
                self.after_step(id, step);
                self.settle_if_done(eng, id, false, last);
            }
        }
        for (_, ids) in self.grad_wait.snapshot() {
            for id in ids {
                let Some(o) = self.opens.get_mut(&id) else { continue };
                let r = o.r_mut();
                r.seed_pool = true;
                r.exit_slot = Some(r.exit_decision_slot.unwrap_or(last) + 1);
                r.proceeds = v::pool_sell(&SEED_POOL, r.tokens).map_or(0, |f| f.trader_quote);
                r.exit_attempts += 1;
                o.grad = None;
                self.counts.seed_pool_sales += 1;
                self.settle_if_done(eng, id, true, last);
            }
        }
        self.grad_wait.clear();
        self.grad_keys.clear();
        self.write_wallets(None); // engine.py:1306 (the day's close is Python's, spec D3)
    }
}

/// The engine's on_complete (engine.py:977-1000) for the curve position of `mint`, completed at `slot`: cf_graduation
/// unless the token completed historically at or before it, reason 'graduation'; with a SOL-market pool that has a
/// create slot the sale waits for open + 1 (the Grad); else it sells now into the standard seed pool (uncapped) at
/// slot + 1, one attempt.
fn on_complete(grad: &GradInfo, counts: &mut PaperCounts, mint: Id, r: &mut PosResult, slot: u64) -> Option<Grad> {
    let tok = grad.tokens.get(&mint);
    if tok.is_none_or(|&(complete, _)| complete.is_none_or(|c| slot < c)) {
        r.cf_graduation = true;
    }
    r.exit_reason = Some(crate::positions::Reason::Graduation);
    let pool = tok.and_then(|t| t.1);
    match pool.and_then(|p| grad.pools.get(&p).copied().flatten().map(|open_slot| Grad { pool: p, open_slot })) {
        Some(g) => Some(g),
        None => {
            r.seed_pool = true;
            r.exit_decision_slot = Some(slot);
            r.exit_slot = Some(slot + 1);
            r.proceeds = v::pool_sell(&SEED_POOL, r.tokens).map_or(0, |f| f.trader_quote);
            r.exit_attempts += 1;
            counts.seed_pool_sales += 1;
            None
        }
    }
}

/// `family.startswith("N2_")`.
fn is_n2(f: Family) -> bool {
    f == Family::N2F7
}

fn curve_post(l: &Leg) -> Curve {
    match l {
        Leg::Curve(c) => c.post(),
        Leg::Pool(p) => panic!("a pool leg at {:?} where the curve's state was asked", p.order),
    }
}

fn pool_post(l: &Leg) -> Pool {
    match l {
        Leg::Pool(p) => p.post(),
        Leg::Curve(c) => panic!("a curve leg at {:?} where a pool's state was asked", c.order),
    }
}

impl Hooks for Paper {
    /// `_clock` (engine.py:446-476): a new UTC day resets every group's day loss (:455-460); a new minute puts every
    /// open position and loads the risk (:461-471). Then steps 1, 2 and 2b.
    fn before_history(&mut self, eng: &SignalEngine, slot: u64, block_time: Option<i64>, by_key: &SlotLegs) {
        if let Some(bt) = block_time {
            if self.day != eng.day {
                self.day = eng.day.clone();
                self.day_loss.iter_mut().for_each(|l| *l = 0);
            }
            let minute = bt.div_euclid(60);
            if self.minute != Some(minute) {
                let gap = self.last_slot.map_or(0, |l| slot.saturating_sub(l + 1));
                self.heartbeat(eng, minute, gap, String::new());
                self.minute = Some(minute);
                for (_, ids) in self.open.snapshot() {
                    for id in ids {
                        self.put_position(id);
                    }
                }
                self.write_wallets(Some(bt));
                self.load_risk();
            }
            if let Some(l) = self.last_slot.filter(|&l| slot - l > GAP_SLOTS) {
                self.heartbeat(eng, minute, slot - l - 1, format!("feed gap {l}..{slot}"));
            }
        }
        // 1. the open positions on the slot's keys (engine.py:373-384)
        for (_, key, legs) in by_key.iter() {
            let Some(ids) = self.open.get(key).cloned() else { continue };
            for id in ids {
                let Some(o) = self.opens.get_mut(&id) else { continue };
                if o.opened >= slot {
                    continue;
                }
                let Some(sim) = o.sim.as_deref_mut() else { continue }; // graduated on our buy: waits for its pool
                if let Some(f) = sim.rule_mut().flows_mut() {
                    f.add(legs); // before the position decides on them
                }
                let step = sim.on_slot(slot, legs, 0);
                self.after_step(id, step);
                self.settle_if_done(eng, id, false, slot);
                if self.opens.get(&id).is_some_and(|o| !o.done()) {
                    self.put_position(id);
                }
            }
        }
        // 2. the entries due (engine.py:385-387)
        for p in self.pending.remove(&slot).unwrap_or_default() {
            Self::dec(&mut self.pending_keys, p.key);
            let legs = by_key.get(p.seg.venue(), p.key).unwrap_or(&[]);
            self.enter(eng, p, slot, legs);
        }
        // 2b. graduations waiting for this pool's open + 1 (engine.py:388-391)
        for (venue, key, legs) in by_key.iter() {
            if venue == Venue::Pool && self.grad_wait.contains(key) {
                self.graduation_exits(eng, key, slot, legs);
            }
        }
    }

    /// `_signal` past the farm gates (engine.py:743-763): busy, then the risk gate (`_risk_block`; 'not loaded' before
    /// the first dated minute), then busy 'open' and the Pending at t + d with d the group's.
    fn on_pass_row(&mut self, eng: &SignalEngine, row: &SignalRow) -> Status {
        let g = &eng.groups[row.group];
        let vi = g.variants.iter().position(|v| *v == row.variant).expect("a row of the group's variant");
        let bk = (row.family, row.group, vi, row.key);
        match self.busy.get(&bk) {
            Some(Busy::Open) => {
                self.counts.busy += 1;
                return Status::Skipped("busy".into());
            }
            Some(&Busy::Until(s)) if row.t <= s => {
                self.counts.busy += 1;
                return Status::Skipped("busy".into());
            }
            _ => {}
        }
        if let Some(why) = self.risk_block(row.family, row.key, row.t, row.group) {
            self.counts.not_loaded += u64::from(why == "not loaded");
            *self.counts.risk_skips.entry(format!("{}: {why}", g.name)).or_default() += 1;
            return Status::Skipped(why.into());
        }
        self.busy.insert(bk, Busy::Open);
        let d = g.d_ms.map_or(g.d, |ms| eng.clock.slots(ms as f64, row.t)); // `_d_at` at t (engine.py:667-672)
        let s_dec = if row.seg == Segment::Curve { eng.recent.back(row.key).map(curve_post) } else { None };
        let p = Pending {
            family: row.family,
            group: row.group,
            vi,
            variant: row.variant.clone(),
            key: row.key,
            seg: row.seg,
            t: row.t,
            entry_slot: row.t + d,
            s_dec,
        };
        Self::inc(&mut self.pending_keys, p.key);
        self.pending.entry(p.entry_slot).or_default().push(p);
        self.counts.entered += 1;
        Status::Entered
    }

    /// The sweep's keep-set (engine.py:677-678): open, pending and graduation keys, and the keys of the positions
    /// waiting for one.
    fn keep(&self, key: Id) -> bool {
        self.open.contains(key) || self.pending_keys.contains_key(&key) || self.grad_wait.contains(key) || self.grad_keys.contains_key(&key)
    }

    /// The run's curve sums (spec §3.5), then step 5 (engine.py:429-438): time passes for the open positions on keys
    /// without an event here, and the graduations of pools without one.
    fn after_farm(&mut self, eng: &SignalEngine, slot: u64, by_key: &SlotLegs) {
        for (venue, key, legs) in by_key.iter() {
            if venue != Venue::Curve {
                continue;
            }
            let sums = self.run_sums.entry(key).or_default();
            for l in legs {
                if let Leg::Curve(c) = l {
                    let t = i128::from(c.t);
                    *sums.entry(c.trader).or_default() += if c.is_buy { t } else { -t };
                }
            }
        }
        let active: HashSet<Id> = by_key.iter().map(|(_, k, _)| k).collect(); // keys only, the venue aside (engine.py:429)
        for (key, ids) in self.open.snapshot() {
            if active.contains(&key) {
                continue;
            }
            for id in ids {
                let Some(o) = self.opens.get_mut(&id) else { continue };
                if o.opened >= slot {
                    continue;
                }
                let Some(sim) = o.sim.as_deref_mut() else { continue };
                sim.advance(slot);
                self.settle_if_done(eng, id, false, slot);
            }
        }
        for (pool, _) in self.grad_wait.snapshot() {
            if by_key.get(Venue::Pool, pool).is_none() {
                self.graduation_exits(eng, pool, slot, &[]);
            }
        }
        self.last_slot = Some(slot);
    }

    fn emit(&mut self, row: SignalRow, status: Status) {
        self.ops.push(Op::Signal(row, status));
    }
}

/// The paper engine of a replay (engine.py `Engine.run` under `--ignore-risk`): the signal engine with `Paper` as its
/// hooks. Push the feed's events in chain order, then `finish`; drain `ops` as they come.
#[derive(Debug)]
pub struct PaperEngine {
    pub sig: SignalEngine,
    pub paper: Paper,
    cur: Option<u64>,
}

impl PaperEngine {
    /// Under ignore-risk. Refuses what the reference never runs: a variant outside M1 or a flow variant outside
    /// [pool_reversed] (check_groups), d = 0, a q outside [0, 1].
    pub fn new(sig: SignalEngine, cfg: PaperCfg) -> Result<Self, PaperError> {
        Self::with_risk(sig, cfg, RiskSource::Ignore)
    }

    /// With the groups' risk from `risk` (`RiskSource::Files`: risk on, as paper.engine without `--ignore-risk`).
    pub fn with_risk(sig: SignalEngine, cfg: PaperCfg, risk: RiskSource) -> Result<Self, PaperError> {
        let paper = Paper::new(&sig, cfg, risk)?;
        Ok(PaperEngine { sig, paper, cur: None })
    }

    /// One event (engine.py `run`, :301-316): a new slot ends the one before it, then the entries due in the quiet
    /// slots between them (:310).
    pub fn push(&mut self, ev: &Event, keys: &mut impl N2Keys) -> Result<(), PaperError> {
        let slot = ev.leg.slot();
        self.sig.push_with(ev, keys, &mut self.paper)?;
        if self.cur != Some(slot) {
            self.cur = Some(slot);
            self.paper.quiet_entries(&self.sig, slot);
        }
        // after the slot before it ended (engine.py:319-320), so a heartbeat there counts up to the event before
        self.paper.events += 1;
        self.paper.last_event = Some(ev.leg.order());
        self.paper.take_error()
    }

    /// The feed's end: its last slot, then the end of data (engine.py:321-324, 1289-1308).
    pub fn finish(&mut self, keys: &mut impl N2Keys) -> Result<(), PaperError> {
        self.sig.finish_with(keys, &mut self.paper)?;
        self.paper.take_error()?;
        self.paper.end_of_data(&self.sig);
        self.paper.take_error()
    }
}

#[cfg(test)]
mod tests;
