//! The light half of `lab-live signals` (M1 C1b; the porting spec is research/m1-c1b-signals-spec-2026-10-19.md
//! §4.4, §5.2-§5.3, §6.2): what lies between the engine's signal state (lab_core::signals) and the journal and needs no
//! Parquet or SQLite, so the shared golden cases run against it on their own (lab-core, serde_json, sha2, anyhow).
//!
//! - `ms_per_slot`: a store meta's slot times by epoch (a golden case's clock has the same form), to Python's bits.
//! - `universe`: the snapshot's tokens and SOL markets as the engine's universe (engine.py `_universe`, :555-568).
//!   Their creators, coin creators and MAYHEM_AGENT are interned after the mints and pools, so `trader == creator` is
//!   an id compare against the traders the chunks intern later. Only `signals` does this: the `legs` keys stay C1a's.
//! - `parse_groups`: the journal's state 'groups' (engine.py `groups_state`, :1386-1391), with the refusals of §5.3.
//! - `Seeds` and `Sha2Keys`: N2's key order and sha256 seeds (strategies.py `_seed`, :573-574), here so lab/core stays
//!   dependency-free (spec D6); each key's seed once, by id.
//! - `row_cells`: a SignalRow as the journal's signals columns; `feed_state`: state 'feed' as Python's json.dumps;
//!   `counts_json`: the engine's counters for the summary.
//! - The tests run every case of paper/tests/data/c1b_signal_cases.json (the engine's own rows and state on small feeds)
//!   through these and SignalEngine, and compare as the file's FORMAT says.

use anyhow::{bail, ensure, Context, Result};
use lab_core::clock::Bounds;
use lab_core::legs::{Id, MAYHEM_AGENT, NONE};
use lab_core::n2::N2Keys;
use lab_core::signals::{Counts, Family, Gate, GroupSpec, SignalRow};
use lab_core::universe::{MintMeta, PoolMeta, Reject, Segment, Universe};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap};

/// The key strings by id: lab-live's interner (legs.rs `Keys`) or a golden case's.
pub trait KeyNames {
    /// The key's id, interned when it is new.
    fn id(&mut self, key: &str) -> Id;
    fn get(&self, key: &str) -> Option<Id>;
    /// The key of an id; None for NONE.
    fn name(&self, id: Id) -> Option<&str>;
    fn len(&self) -> usize;
}

/// meta.json's `ms_per_slot`, `{"<epoch>": ms per slot, ...}` (a golden case's `clock` has the same form); null or
/// absent is empty. The floats are Python's json.dump of its floats: serde_json reads them to the same bits only with its
/// `float_roundtrip` feature (Cargo.toml; spec D9), and one ULP can flip a window's rounding.
pub fn ms_per_slot(v: &Value) -> Result<BTreeMap<u64, f64>> {
    if v.is_null() {
        return Ok(BTreeMap::new());
    }
    let m = v.as_object().context("not an object of epoch -> ms per slot")?;
    m.iter()
        .map(|(k, x)| {
            let epoch = k.parse::<u64>().with_context(|| format!("epoch {k:?} is no integer"))?;
            let ms = x.as_f64().filter(|ms| ms.is_finite() && *ms > 0.0).with_context(|| format!("epoch {k}: {x} is no slot time"))?;
            Ok((epoch, ms))
        })
        .collect()
}

/// A token of the snapshot's mints (feed.py `TokenInfo`), the fields F7 reads.
#[derive(Clone, Copy, Debug)]
pub struct Token<'a> {
    pub mint: &'a str,
    pub create_slot: Option<u64>,
    pub creator: Option<&'a str>,
    pub mayhem: Option<bool>,
    pub quote_mint: Option<&'a str>,
}

/// A SOL market of the snapshot's pools (feed.py `PoolInfo`, quote_class sol or reversed): its mint is
/// COALESCE(mint, pool), its mayhem COALESCE(mayhem, false) (feed.py:97-99).
#[derive(Clone, Copy, Debug)]
pub struct Market<'a> {
    pub pool: &'a str,
    pub mint: &'a str,
    pub reversed: bool,
    pub mayhem: bool,
    pub coin_creator: Option<&'a str>,
}

/// What building the universe saw: tokens, SOL markets, tokens with a NULL quote_mint (never SOL, counted, no error:
/// spec D5), and the keys it interned (creators, coin creators and MAYHEM_AGENT not interned before).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct UniverseLoad {
    pub tokens: u64,
    pub markets: u64,
    pub quote_null: u64,
    pub interned: u64,
}

/// The engine's universe over the snapshot (spec §3.5, §4.4). The mints and pools are interned first (the admission
/// has done so already in a replay; `id` gives their ids back), then every token's creator, every SOL market's coin
/// creator and MAYHEM_AGENT, so their ids stay dense. A pool's token is looked up by its mint: a mint that is no token
/// leaves the pool tokenless, and its creator then is its coin_creator (engine.py:568).
pub fn universe(keys: &mut impl KeyNames, bounds: Bounds, tokens: &[Token], markets: &[Market]) -> (Universe, UniverseLoad) {
    for t in tokens {
        keys.id(t.mint);
    }
    for m in markets {
        keys.id(m.pool);
    }
    let before = keys.len();
    let mut id = |k: Option<&str>| k.map_or(NONE, |k| keys.id(k));
    let creators: Vec<Id> = tokens.iter().map(|t| id(t.creator)).collect();
    let coin_creators: Vec<Id> = markets.iter().map(|m| id(m.coin_creator)).collect();
    let mut u = Universe::new(bounds, keys.id(MAYHEM_AGENT));
    let mut load = UniverseLoad { tokens: tokens.len() as u64, markets: markets.len() as u64, ..Default::default() };
    for (t, creator) in tokens.iter().zip(creators) {
        load.quote_null += u64::from(t.quote_mint.is_none());
        u.add_mint(keys.id(t.mint), MintMeta::new(t.create_slot, creator, t.mayhem, t.quote_mint));
    }
    for (m, coin_creator) in markets.iter().zip(coin_creators) {
        let p = PoolMeta { reversed: m.reversed, mayhem: m.mayhem, mint_tok: keys.get(m.mint), coin_creator };
        u.add_pool(keys.id(m.pool), p);
    }
    load.interned = (keys.len() - before) as u64;
    (u, load)
}

/// The groups of a run as the journal's state 'groups' has them (engine.py `groups_state`), the primary first, then the
/// others in the state's name order (json.dumps sort_keys; the order changes no final row, spec §3.9). `q` per group is
/// kept for C2 (lab_core's GroupSpec has no field for it).
#[derive(Clone, Debug, PartialEq)]
pub struct Groups {
    pub specs: Vec<GroupSpec>,
    pub q: Vec<f64>,
}

/// The keys of a group in groups_state: lab-live reads them all, and refuses others (a newer engine's groups could mean
/// another decision).
const GROUP_KEYS: [&str; 11] = ["d", "d_ms", "farm", "n2_k", "primary", "q", "require_farm_complete", "risk", "segments", "variants", "wallet"];

/// The groups_state JSON (normally the Python journal's state 'groups'). Refused: not exactly one primary group, a
/// variant name used twice (in one group or across groups: engine.py:237-239), a farm other than all or organic, an
/// unknown segment, and a key groups_state does not have or a missing one. An empty segment list is None, as the
/// engine's `set(segments) if segments else None`.
pub fn parse_groups(text: &str) -> Result<Groups> {
    let v: Value = serde_json::from_str(text).context("the groups are no JSON")?;
    let all = v.as_object().context("the groups are no object of name -> group")?;
    let mut groups: Vec<(GroupSpec, f64)> = Vec::new();
    for (name, g) in all {
        let g = g.as_object().with_context(|| format!("group {name} is no object"))?;
        if let Some(k) = g.keys().find(|k| !GROUP_KEYS.contains(&k.as_str())) {
            bail!("group {name}: {k} is no key of the engine's groups_state (engine.py:1386-1391)");
        }
        if let Some(k) = GROUP_KEYS.iter().find(|k| !g.contains_key(**k)) {
            bail!("group {name}: no {k}");
        }
        let what = |k: &str, kind: &str| format!("group {name}: {k} {} is no {kind}", g[k]);
        let variants = g["variants"].as_array().with_context(|| what("variants", "list"))?;
        let variants = variants.iter().map(|x| x.as_str().map(str::to_owned)).collect::<Option<Vec<_>>>().with_context(|| what("variants", "list of names"))?;
        let organic = match g["farm"].as_str() {
            Some("all") => false,
            Some("organic") => true,
            _ => bail!("group {name}: farm {} (all or organic)", g["farm"]),
        };
        let segments = match &g["segments"] {
            Value::Null => None,
            Value::Array(a) if a.is_empty() => None,
            Value::Array(a) => {
                let mut on = [false; 3];
                for x in a {
                    let seg = x.as_str().and_then(Segment::parse).with_context(|| format!("group {name}: unknown segment {x}"))?;
                    on[seg.index()] = true;
                }
                Some(on)
            }
            _ => bail!(what("segments", "list")),
        };
        let d_ms = match &g["d_ms"] {
            Value::Null => None,
            x => Some(x.as_u64().filter(|&d| d > 0).with_context(|| what("d_ms", "delay above 0"))?),
        };
        for k in ["risk", "wallet"] {
            ensure!(g[k].is_null() || g[k].is_string(), what(k, "path"));
        }
        let spec = GroupSpec {
            name: name.clone(),
            primary: g["primary"].as_bool().with_context(|| what("primary", "boolean"))?,
            variants,
            segments,
            organic,
            n2_k: usize::try_from(g["n2_k"].as_u64().with_context(|| what("n2_k", "count"))?)?,
            require_farm_complete: g["require_farm_complete"].as_bool().with_context(|| what("require_farm_complete", "boolean"))?,
            d: g["d"].as_u64().with_context(|| what("d", "delay in slots"))?,
            d_ms,
        };
        groups.push((spec, g["q"].as_f64().with_context(|| what("q", "number"))?));
    }
    let primaries: Vec<&str> = groups.iter().filter(|g| g.0.primary).map(|g| g.0.name.as_str()).collect();
    ensure!(primaries.len() == 1, "exactly one primary group, not {} ({primaries:?})", primaries.len());
    let mut seen = BTreeSet::new();
    for v in groups.iter().flat_map(|g| &g.0.variants) {
        ensure!(seen.insert(v.as_str()), "variant {v} is in the groups twice (variant names are distinct across groups)");
    }
    groups.sort_by_key(|g| !g.0.primary); // stable: the others stay in name order
    Ok(Groups { specs: groups.iter().map(|g| g.0.clone()).collect(), q: groups.iter().map(|g| g.1).collect() })
}

/// `_seed(*parts)` (strategies.py:573-574): the first 8 bytes of sha256 of the parts joined by '|', little-endian.
pub fn seed(text: &str) -> u64 {
    let d = Sha256::digest(text.as_bytes());
    u64::from_le_bytes(d[..8].try_into().expect("a sha256 has 32 bytes"))
}

/// Each N2 candidate's seed once: keys trade for hours, and N2 draws over every candidate of the hour per trigger.
#[derive(Default)]
pub struct Seeds {
    by_id: HashMap<Id, u64>,
}

impl Seeds {
    /// The N2Keys over these seeds and the key strings.
    pub fn with<'a, K: KeyNames>(&'a mut self, names: &'a K) -> Sha2Keys<'a, K> {
        Sha2Keys { names, seeds: self }
    }

    pub fn len(&self) -> usize {
        self.by_id.len()
    }
}

/// N2's view of the key strings (lab_core::n2::N2Keys): their order is Python's str order (base58 is ASCII, so byte
/// order), each candidate's seed `_seed(key)`, the trigger's `_seed("paper-N2", key, t)`.
pub struct Sha2Keys<'a, K> {
    names: &'a K,
    seeds: &'a mut Seeds,
}

impl<K: KeyNames> Sha2Keys<'_, K> {
    fn name(&self, k: Id) -> &str {
        self.names.name(k).expect("an N2 key is an event's key, interned")
    }
}

impl<K: KeyNames> N2Keys for Sha2Keys<'_, K> {
    fn cmp(&self, a: Id, b: Id) -> Ordering {
        self.name(a).cmp(self.name(b))
    }

    fn key_seed(&mut self, k: Id) -> u64 {
        if let Some(&s) = self.seeds.by_id.get(&k) {
            return s;
        }
        let s = seed(self.name(k));
        self.seeds.by_id.insert(k, s);
        s
    }

    fn trigger_seed(&mut self, k: Id, t: u64) -> u64 {
        seed(&format!("paper-N2|{}|{t}", self.name(k)))
    }
}

/// One journal cell as SQLite stores it (signals.rs makes rusqlite values of them); Real is an exits row's ret.
#[derive(Clone, Debug, PartialEq)]
pub enum Cell {
    Null,
    Int(i64),
    Real(f64),
    Text(String),
}

/// A SignalRow as the journal's signals columns (journal.py:17-30), the ones `_signal` writes in a replay
/// (engine.py:733-763). The columns not named are NULL: block_time, received_unix_ns, created_unix_ns, latency_ms,
/// segment_written_unix, segment_lag_ms and d_ms (a replay has no arrivals). A row past the farm gates has a NULL status
/// and reason, a gated one 'skipped' and the gate (spec D10). An integer beyond SQLite's is an error (Python's too).
pub fn row_cells(r: &SignalRow, names: &impl KeyNames) -> Result<Vec<(&'static str, Cell)>> {
    let text = |s: &str| Cell::Text(s.to_owned());
    let int = |v: Option<u64>, what: &str| -> Result<Cell> {
        v.map_or(Ok(Cell::Null), |x| Ok(Cell::Int(i64::try_from(x).with_context(|| format!("{what} {x} beyond INTEGER"))?)))
    };
    let small = |v: Option<u8>| v.map_or(Cell::Null, |x| Cell::Int(x.into()));
    Ok(vec![
        ("family", text(r.family.as_str())),
        ("variant", text(&r.variant)),
        ("key", text(names.name(r.key).context("a signal row of an id without a key")?)),
        ("t", int(Some(r.t), "t")?),
        ("day", r.day.as_deref().map_or(Cell::Null, text)),
        ("venue", text(r.seg.venue().as_str())),
        ("orientation", text(r.seg.orientation())),
        ("depth", Cell::Int(i64::try_from(r.depth).with_context(|| format!("depth {} beyond INTEGER", r.depth))?)),
        ("trigger_trader", names.name(r.trigger_trader).map_or(Cell::Null, text)),
        ("status", r.status().map_or(Cell::Null, text)),
        ("reason", r.reason().map_or(Cell::Null, text)),
        ("farm", text(r.farm.as_str())),
        ("farm_complete", small(r.farm_complete)),
        ("pool_seen_slot", int(r.pool_seen_slot, "pool_seen_slot")?),
        ("farm_window_complete", small(r.farm_window_complete)),
    ])
}

/// State 'feed' as paper.engine main writes it for a replay (engine.py:1493): Python's json.dumps of
/// {"kind": "replay", "from": A, "to": B}, with "dev_bounds": [LO, HI] after "to" when the bounds were given.
pub fn feed_state(from: u64, to: u64, dev_bounds: Option<(u64, u64)>) -> String {
    match dev_bounds {
        None => format!(r#"{{"kind": "replay", "from": {from}, "to": {to}}}"#),
        Some((lo, hi)) => format!(r#"{{"kind": "replay", "from": {from}, "to": {to}, "dev_bounds": [{lo}, {hi}]}}"#),
    }
}

/// A gate's name in the journal's terms (paper/journal_diff.py `gate`): its reason, or "pass".
pub fn gate_name(g: Gate) -> &'static str {
    g.reason().unwrap_or("pass")
}

/// The engine's counters for the summary: events and event slots, universe rejections per cause where F7 asked (in
/// [dlo, dhi]), event-local trigger and F7 passes per segment, rows per family and gate (overwrites included), hour
/// rollovers, N2 overwrites, and the windows (n_in, n_cr, n_gap, H) per epoch with events.
pub fn counts_json(c: &Counts) -> Value {
    let per_seg = |x: &[u64; 3]| Value::Object(Segment::ALL.iter().map(|s| (s.as_str().to_owned(), json!(x[s.index()]))).collect());
    let rows: Map<String, Value> = [Family::F7, Family::N2F7]
        .iter()
        .map(|f| {
            let by_gate = Gate::ALL.iter().map(|g| (gate_name(*g).to_owned(), json!(c.rows[*f as usize][*g as usize]))).collect();
            (f.as_str().to_owned(), Value::Object(by_gate))
        })
        .collect();
    json!({
        "events": c.events,
        "event_slots": c.slots,
        "rejects": Value::Object(Reject::ALL.iter().map(|r| (r.as_str().to_owned(), json!(c.rejects[*r as usize]))).collect()),
        "triggers": per_seg(&c.triggers),
        "f7": per_seg(&c.f7),
        "rows": rows,
        "rollovers": c.rollovers,
        "overwrites": c.overwrites,
        "windows": Value::Object(c.windows.iter().map(|(e, w)| (e.to_string(), json!(w))).collect()),
    })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use lab_core::clock::Clock;
    use lab_core::farm::WindowRule;
    use lab_core::legs::{CurveLeg, CurveVariant, Event, Leg, Order, PoolKind, PoolLeg, DIRECT, EXACT_OUT, FARMER, X0, X1};
    use lab_core::signals::{Farm, SignalEngine};

    /// The shared golden cases (paper/tests/c1b_signal_cases.py writes them from the engine; FORMAT there).
    const CASES: &str = include_str!("../../paper/tests/data/c1b_signal_cases.json");

    /// dev2's 14 and fwd1053's slot time as the stores' meta.json has them (`json.dump` writes `repr(float)`), with
    /// lab-py's float.hex bits (research/m1-c1b-signals-spec-2026-10-19.md D9, §3.2; queried 2026-10-19).
    pub(crate) const SLOT_TIMES: [(u64, &str, u64); 15] = [
        (1033, "316.36", 0x4073_c5c2_8f5c_28f6), (1034, "315.9", 0x4073_be66_6666_6666), (1035, "316.53", 0x4073_c87a_e147_ae14),
        (1036, "317.13", 0x4073_d214_7ae1_47ae), (1037, "266.46", 0x4070_a75c_28f5_c28f), (1038, "266.83", 0x4070_ad47_ae14_7ae1),
        (1039, "267.12", 0x4070_b1eb_851e_b852), (1040, "266.17", 0x4070_a2b8_51eb_851f), (1041, "265.83", 0x4070_9d47_ae14_7ae1),
        (1042, "267.83", 0x4070_bd47_ae14_7ae1), (1043, "267.93", 0x4070_bee1_47ae_147b), (1044, "268.26", 0x4070_c428_f5c2_8f5c),
        (1045, "267.65", 0x4070_ba66_6666_6666), (1046, "267.35", 0x4070_b599_9999_999a), (1053, "218.29", 0x406b_4947_ae14_7ae1),
    ];

    /// A golden case's interner: ids in first-use order.
    #[derive(Default)]
    pub(crate) struct CaseKeys {
        ids: HashMap<String, Id>,
        names: Vec<String>,
    }

    impl KeyNames for CaseKeys {
        fn id(&mut self, key: &str) -> Id {
            if let Some(&i) = self.ids.get(key) {
                return i;
            }
            let i = self.names.len() as Id;
            self.ids.insert(key.to_owned(), i);
            self.names.push(key.to_owned());
            i
        }
        fn get(&self, key: &str) -> Option<Id> {
            self.ids.get(key).copied()
        }
        fn name(&self, id: Id) -> Option<&str> {
            (id != NONE).then(|| self.names[id as usize].as_str())
        }
        fn len(&self) -> usize {
            self.names.len()
        }
    }

    pub(crate) fn field<'a>(e: &'a Value, k: &str) -> Result<&'a Value> {
        e.get(k).with_context(|| format!("no field {k} in {e}"))
    }

    pub(crate) fn u(e: &Value, k: &str) -> Result<u64> {
        field(e, k)?.as_u64().with_context(|| format!("{k} is no u64 in {e}"))
    }

    pub(crate) fn i(e: &Value, k: &str) -> Result<i64> {
        field(e, k)?.as_i64().with_context(|| format!("{k} is no i64 in {e}"))
    }

    pub(crate) fn small(e: &Value, k: &str) -> Result<u32> {
        Ok(u32::try_from(u(e, k)?)?)
    }

    pub(crate) fn b(e: &Value, k: &str) -> Result<bool> {
        field(e, k)?.as_bool().with_context(|| format!("{k} is no bool in {e}"))
    }

    pub(crate) fn s<'a>(e: &'a Value, k: &str) -> Result<&'a str> {
        field(e, k)?.as_str().with_context(|| format!("{k} is no string in {e}"))
    }

    pub(crate) fn ou(e: &Value, k: &str) -> Result<Option<u64>> {
        let v = field(e, k)?;
        if v.is_null() { Ok(None) } else { v.as_u64().map(Some).with_context(|| format!("{k} is no u64 in {e}")) }
    }

    pub(crate) fn os<'a>(e: &'a Value, k: &str) -> Result<Option<&'a str>> {
        let v = field(e, k)?;
        if v.is_null() { Ok(None) } else { v.as_str().map(Some).with_context(|| format!("{k} is no string in {e}")) }
    }

    const VARIANTS: [CurveVariant; 11] = [
        CurveVariant::Buy, CurveVariant::BuyExactSolIn, CurveVariant::BuyV2, CurveVariant::BuyV3, CurveVariant::BuyExactQuoteInV2,
        CurveVariant::BuyExactQuoteInV3, CurveVariant::MultiHopCurveSwap, CurveVariant::Sell, CurveVariant::SellV2, CurveVariant::SellV3,
        CurveVariant::Other,
    ];

    /// A case's event (FORMAT events, events.curve, events.pool) as lab_core's Event, its keys interned.
    pub(crate) fn event(e: &Value, keys: &mut CaseKeys) -> Result<Event> {
        let order = Order {
            slot: u(e, "slot")?,
            tx: small(e, "tx")?,
            outer: u16::try_from(u(e, "outer")?)?,
            inner: u16::try_from(u(e, "inner")?)?,
        };
        let key = keys.id(s(e, "key")?);
        let trader = os(e, "trader")?.map_or(NONE, |t| keys.id(t));
        let leg = match s(e, "venue")? {
            "curve" => {
                let variant = s(e, "variant")?;
                let args = [ou(e, "budget")?, ou(e, "max_cost")?, ou(e, "min_tokens")?, ou(e, "min_out")?];
                Leg::Curve(CurveLeg {
                    order,
                    mint: key,
                    trader,
                    t: u(e, "t")?,
                    q: u(e, "q")?,
                    fee: u(e, "fee")?,
                    vt: u(e, "vt")?,
                    vq: u(e, "vq")?,
                    rt: u(e, "rt")?,
                    rq: u(e, "rq")?,
                    args: args.map(|a| a.unwrap_or(0)),
                    fee_bps: small(e, "fee_bps")?,
                    creator_bps: small(e, "creator_bps")?,
                    args_set: (0..4).filter(|&k| args[k].is_some()).fold(0, |m, k| m | (1 << k)),
                    variant: VARIANTS.into_iter().find(|v| v.as_str() == variant).with_context(|| format!("variant {variant}"))?,
                    is_buy: b(e, "is_buy")?,
                    direct: b(e, "direct")?,
                })
            }
            "pool" => {
                let kind = match s(e, "kind")? {
                    "buy" => PoolKind::Buy,
                    "sell" => PoolKind::Sell,
                    "boost" => PoolKind::Boost,
                    "withdraw" => PoolKind::Withdraw,
                    "deposit" => PoolKind::Deposit,
                    k => bail!("pool kind {k}"),
                };
                // The kind's optional amounts in x (lab_core PoolLeg); the others must be absent.
                let (x0, x1, unused): (&str, &str, &[&str]) = match kind {
                    PoolKind::Buy | PoolKind::Sell => ("limit_quote", "limit_base", &["boost_left", "lp_amount", "lp_supply"]),
                    PoolKind::Boost => ("boost_left", "", &["limit_quote", "limit_base", "lp_amount", "lp_supply"]),
                    _ => ("lp_amount", "lp_supply", &["limit_quote", "limit_base", "boost_left"]),
                };
                for &k in unused {
                    ensure!(field(e, k)?.is_null(), "a {kind:?} with {k}: {e}");
                }
                let (a0, a1) = (ou(e, x0)?, if x1.is_empty() { None } else { ou(e, x1)? });
                let flag = |on: bool, f: u8| if on { f } else { 0 };
                let flags = flag(b(e, "exact_out")?, EXACT_OUT) | flag(b(e, "direct")?, DIRECT) | flag(b(e, "farmer")?, FARMER)
                    | flag(a0.is_some(), X0) | flag(a1.is_some(), X1);
                Leg::Pool(PoolLeg {
                    order,
                    pool: key,
                    trader,
                    b: i(e, "b")?,
                    e: i(e, "e")?,
                    virt: i(e, "virt")?,
                    fees: [small(e, "lp_bps")?, small(e, "protocol_bps")?, small(e, "creator_bps")?, small(e, "cashback_bps")?],
                    base: i(e, "base")?,
                    quote_net: i(e, "quote_net")?,
                    e_delta: i(e, "e_delta")?,
                    x: [a0.unwrap_or(0), a1.unwrap_or(0)],
                    kind,
                    flags,
                })
            }
            v => bail!("venue {v}"),
        };
        let bt = field(e, "block_time")?;
        Ok(Event::new(leg, if bt.is_null() { None } else { Some(bt.as_i64().context("block_time")?) }))
    }

    /// A row in the C1b projection of paper/journal_diff.py `signal_row` (FORMAT expect): the journal's columns of the
    /// row's cells (NULL where none), `gate`, then status and reason.
    pub(crate) fn projection(cells: &[(&str, Cell)]) -> Map<String, Value> {
        let col = |c: &str| match cells.iter().find(|x| x.0 == c).map(|x| &x.1) {
            None | Some(Cell::Null) => Value::Null,
            Some(Cell::Int(v)) => json!(v),
            Some(Cell::Real(v)) => json!(v),
            Some(Cell::Text(t)) => json!(t),
        };
        let mut m = Map::new();
        for c in ["family", "variant", "key", "t", "day", "venue", "orientation", "depth", "trigger_trader", "farm", "pool_seen_slot",
                  "farm_window_complete", "block_time", "d_ms", "farm_complete"] {
            m.insert(c.to_owned(), col(c));
        }
        let (status, reason) = (col("status"), col("reason"));
        let gated = status == json!("skipped") && reason.as_str().is_some_and(|r| Gate::ALL.iter().any(|g| g.reason() == Some(r)));
        m.insert("gate".to_owned(), if gated { reason.clone() } else { json!("pass") });
        m.insert("status".to_owned(), status);
        m.insert("reason".to_owned(), reason);
        m
    }

    /// A journal row in the C1b projection.
    pub(crate) type Row = Map<String, Value>;

    /// A case run as test_signal_cases.py runs it in Python (a replay of its events from an empty engine, the groups from
    /// its state text, ignore_risk): the journal's rows in the C1b projection, sorted by key with the last write of a key
    /// kept (INSERT OR REPLACE), and the engine's signal state (FORMAT expect_state).
    /// A case's signal engine (its clock, dev bounds, universe and groups from the state text) with the case's interner
    /// and the groups (their q for C2).
    pub(crate) fn case_engine(case: &Value) -> Result<(SignalEngine, Groups, CaseKeys)> {
        let clock = Clock::new(ms_per_slot(field(case, "clock")?)?);
        let dev = field(case, "dev_bounds")?;
        let bounds = Bounds::new((dev[0].as_u64().context("dev_bounds")?, dev[1].as_u64().context("dev_bounds")?), &clock);
        let groups = parse_groups(s(case, "groups")?)?;
        let mut keys = CaseKeys::default();
        let tokens = field(case, "tokens")?.as_array().context("tokens")?;
        let tokens: Vec<Token> = tokens
            .iter()
            .map(|t| {
                let mayhem = field(t, "mayhem")?;
                Ok(Token {
                    mint: s(t, "mint")?,
                    create_slot: ou(t, "create_slot")?,
                    creator: os(t, "creator")?,
                    mayhem: if mayhem.is_null() { None } else { Some(mayhem.as_bool().context("mayhem")?) },
                    quote_mint: os(t, "quote_mint")?,
                })
            })
            .collect::<Result<_>>()?;
        let mut markets = Vec::new();
        for p in field(case, "pools")?.as_array().context("pools")? {
            if !matches!(s(p, "quote_class")?, "sol" | "reversed") {
                continue; // unknown to the engine (feed.py:97-99)
            }
            let reversed = match s(p, "orientation")? {
                "normal" => false,
                "reversed" => true,
                o => bail!("orientation {o}"),
            };
            markets.push(Market { pool: s(p, "pool")?, mint: s(p, "mint")?, reversed, mayhem: b(p, "mayhem")?, coin_creator: os(p, "coin_creator")? });
        }
        let (u_, _) = universe(&mut keys, bounds, &tokens, &markets);
        let eng = SignalEngine::new(u_, clock, groups.specs.clone(), WindowRule::AllPools)?;
        Ok((eng, groups, keys))
    }

    fn run_case(case: &Value) -> Result<(Vec<Row>, Value, SignalEngine)> {
        let (mut eng, _, mut keys) = case_engine(case)?;
        let mut seeds = Seeds::default();
        let mut journal: BTreeMap<(String, String, String, u64), Row> = BTreeMap::new();
        let mut rows = Vec::new();
        let mut write = |rows: &mut Vec<SignalRow>, keys: &CaseKeys| -> Result<()> {
            for r in rows.drain(..) {
                let m = projection(&row_cells(&r, keys)?);
                let k = (r.family.as_str().to_owned(), r.variant.clone(), keys.name(r.key).unwrap().to_owned(), r.t);
                journal.insert(k, m);
            }
            Ok(())
        };
        for e in field(case, "events")?.as_array().context("events")? {
            let ev = event(e, &mut keys)?;
            eng.push(&ev, &mut seeds.with(&keys), &mut |r| rows.push(r))?;
            write(&mut rows, &keys)?;
        }
        eng.finish(&mut seeds.with(&keys), &mut |r| rows.push(r))?;
        write(&mut rows, &keys)?;
        let name = |id: Id| keys.name(id).unwrap().to_owned();
        let bucket = |seg| Value::Array(eng.hours.events(seg).iter().map(|&(k, slot, d)| json!([name(k), slot, i64::try_from(d).unwrap()])).collect());
        let mut farm_win = Map::new();
        for id in 0..keys.len() as Id {
            if let Some(xs) = eng.farm.items(id).filter(|xs| !xs.is_empty()) {
                let items = xs.iter().map(|x| json!([x.0, x.1.tx, x.1.outer, x.1.inner, i64::try_from(x.2).unwrap(), i64::try_from(x.3).unwrap()]));
                farm_win.insert(name(id), Value::Array(items.collect()));
            }
        }
        let pool_seen: Map<String, Value> = eng.pool_seen.iter().map(|(&p, &first)| (name(p), json!([first, eng.pool_last[&p]]))).collect();
        let state = json!({
            "first_slot": eng.first_slot,
            "hour": eng.hours.hour,
            "hour_events": {"curve": bucket(Segment::Curve), "pool_normal": bucket(Segment::PoolNormal), "pool_reversed": bucket(Segment::PoolReversed)},
            "farm_win": farm_win,
            "pool_seen": pool_seen,
        });
        Ok((journal.into_values().collect(), state, eng))
    }

    /// The columns C1b compares: all but status and reason (FORMAT expect; spec §1.3).
    fn c1b(r: &Row) -> Row {
        r.iter().filter(|(k, _)| !matches!(k.as_str(), "status" | "reason")).map(|(k, v)| (k.clone(), v.clone())).collect()
    }

    #[test]
    fn every_golden_case_gives_the_engines_rows_and_state() {
        let doc: Value = serde_json::from_str(CASES).unwrap();
        let cases = doc["cases"].as_array().unwrap();
        let names: Vec<&str> = cases.iter().map(|c| c["name"].as_str().unwrap()).collect();
        // The four required tests (spec §1.5 A4) and the pool counter-case are among them.
        for need in ["pool_mint_absent", "t1_quote_null", "t1_token_absent", "t1_as_stored", "t1_quote_default", "t2_other_pool", "t2_control_sol"] {
            assert!(names.contains(&need), "{need} is no case of {names:?}");
        }
        let mut rows_seen = 0;
        for case in cases {
            let name = case["name"].as_str().unwrap();
            let (rows, state, _) = run_case(case).unwrap_or_else(|e| panic!("{name}: {e:#}"));
            let want: Vec<Row> = case["expect"].as_array().unwrap().iter().map(|r| r.as_object().unwrap().clone()).collect();
            assert_eq!(rows.iter().map(c1b).collect::<Vec<_>>(), want.iter().map(c1b).collect::<Vec<_>>(), "{name}: rows");
            // Past the farm gates Rust leaves the status open (D10); at a gate both sides write 'skipped' and the gate.
            for (r, w) in rows.iter().zip(&want) {
                if r["gate"] == "pass" {
                    assert_eq!((&r["status"], &r["reason"]), (&Value::Null, &Value::Null), "{name}: {r:?}");
                } else {
                    assert_eq!((&r["status"], &r["reason"]), (&json!("skipped"), &r["gate"]), "{name}: {r:?}");
                    assert_eq!((&w["status"], &w["reason"]), (&r["status"], &r["reason"]), "{name}: {w:?}");
                }
            }
            assert_eq!(state, case["expect_state"], "{name}: state");
            rows_seen += rows.len();
        }
        assert!(rows_seen > 50, "{rows_seen}");
    }

    #[test]
    fn t1_and_t2_with_the_sha256_keys() {
        // T1 as stored (PUMP-quoted) and T2's 'other' pool: no row and no N2 candidacy; their controls one F7 row each.
        let doc: Value = serde_json::from_str(CASES).unwrap();
        let case = |n: &str| doc["cases"].as_array().unwrap().iter().find(|c| c["name"] == n).unwrap().clone();
        for n in ["t1_as_stored", "t1_quote_null", "t1_token_absent", "t1_mayhem", "t2_other_pool"] {
            let (rows, state, eng) = run_case(&case(n)).unwrap();
            assert!(rows.is_empty() && eng.hours.is_empty() && eng.farm.is_empty(), "{n}");
            assert_eq!(state["farm_win"], json!({}), "{n}");
        }
        let (_, _, eng) = run_case(&case("t1_quote_null")).unwrap();
        assert_eq!(eng.counts.rejects[Reject::QuoteNull as usize], 8); // counted per slot F7 asked, never an error (D5)
        let (rows, _, _) = run_case(&case("t1_quote_default")).unwrap();
        assert_eq!((rows.len(), &rows[0]["key"], &rows[0]["depth"]), (1, &json!("7orRHgZjwNmvpcSBHUMQ8wqW6rwKpqE1hJJR43AkQa1Z"), &json!(53_657_221_584u64)));
        let (rows, _, _) = run_case(&case("t2_control_sol")).unwrap();
        assert_eq!((rows.len(), &rows[0]["farm"], &rows[0]["depth"]), (1, &json!("organic"), &json!(2_564_283_573_690u64)));
    }

    #[test]
    fn the_seeds_are_pythons() {
        // lab-py: strategies._seed(key) and _seed("paper-N2", key, t).
        let mut keys = CaseKeys::default();
        let (t1, t2) = (keys.id("7orRHgZjwNmvpcSBHUMQ8wqW6rwKpqE1hJJR43AkQa1Z"), keys.id("8iXGs3rSqshaWD9iCZEXu5XLvD5CUhuu7DSA7kd3Cg92"));
        let rest = ["A1YQFKJoFcia2WUqkfFrEBnDbJZ2AFg7XiWq2unwNhAj", "2CFywHXDPjDK2iRQsb95vnjgncDUZeQKJ6MceJ4ALpdc",
                    "6Ljyr7EoLpPyvbWUZ6Urh126cYLxDXVsRoHve1WWxok9"].map(|n| keys.id(n));
        let mut seeds = Seeds::default();
        let mut k = seeds.with(&keys);
        // The same five keys and seeds as lab/core's n2.rs tests (N2_SEEDS), here with real sha256 (LAB review on #372).
        assert_eq!((k.key_seed(t1), k.key_seed(t2)), (3_889_069_239_855_593_721, 16_848_164_125_403_183_493));
        assert_eq!(rest.map(|r| k.key_seed(r)), [1_499_288_943_959_168_397, 348_353_214_577_540_706, 313_765_029_953_787_783]);
        assert_eq!(k.trigger_seed(t1, 450_197_213), 1_665_859_808_466_201_397);
        assert_eq!((k.cmp(t1, t2), k.cmp(t2, t1), k.cmp(t1, t1)), (Ordering::Less, Ordering::Greater, Ordering::Equal)); // '7' < '8'
        assert_eq!(seeds.len(), 5); // each key's seed once
    }

    #[test]
    fn the_stores_slot_times_parse_to_pythons_bits() {
        // As meta.json has them (json.dump, indent 1): every bit as float.hex gives it, through serde_json float_roundtrip.
        let text = format!("{{\n \"ms_per_slot\": {{\n{}\n }}\n}}",
                           SLOT_TIMES.iter().map(|(e, t, _)| format!("  \"{e}\": {t}")).collect::<Vec<_>>().join(",\n"));
        let m = ms_per_slot(&serde_json::from_str::<Value>(&text).unwrap()["ms_per_slot"]).unwrap();
        assert_eq!(m.iter().map(|(e, ms)| (*e, ms.to_bits())).collect::<Vec<_>>(), SLOT_TIMES.map(|(e, _, bits)| (e, bits)));
        assert_eq!(ms_per_slot(&Value::Null).unwrap(), BTreeMap::new());
        for bad in [json!({"x": 1.0}), json!({"1042": "267.83"}), json!({"1042": 0.0}), json!([267.83])] {
            assert!(ms_per_slot(&bad).is_err(), "{bad}");
        }
        // The windows of the spec's §3.2 from the parsed clock.
        let clock = Clock::new(m);
        assert_eq!((clock.windows(450_100_000), clock.windows(450_200_000), clock.windows(455_000_000)),
                   ([50, 201, 20, 13_542], [50, 200, 20, 13_441], [61, 245, 24, 16_492]));
    }

    #[test]
    fn the_feed_state_is_pythons_json_dumps() {
        // lab-py: json.dumps({"kind": "replay", "from": a, "to": b}) and PR 1's dev_bounds after "to".
        assert_eq!(feed_state(450_128_159, 450_380_000, None), r#"{"kind": "replay", "from": 450128159, "to": 450380000}"#);
        assert_eq!(feed_state(454_896_000, 455_328_000, Some((0, (1 << 63) - 1))),
                   r#"{"kind": "replay", "from": 454896000, "to": 455328000, "dev_bounds": [0, 9223372036854775807]}"#);
    }

    pub(crate) const GROUPS: &str = concat!(
        r#"{"H": {"d": 1, "d_ms": 600, "farm": "organic", "n2_k": 0, "primary": false, "q": 0.5, "require_farm_complete": true, "#,
        r#""risk": "/x/risk-h.yaml", "segments": ["pool_reversed"], "variants": ["flow300_H6", "flow300_tp100_H6"], "#,
        r#""wallet": "wallet-h.json"}, "K3": {"d": 1, "d_ms": 600, "farm": "all", "n2_k": 1, "primary": true, "q": 0.5, "#,
        r#""require_farm_complete": false, "risk": "/x/risk.yaml", "segments": null, "variants": ["tp4_T600"], "wallet": "wallet.json"}}"#,
    );

    #[test]
    fn the_groups_state_parses_with_the_primary_first() {
        let g = parse_groups(GROUPS).unwrap();
        assert_eq!(g.specs.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(), ["K3", "H"]);
        assert_eq!(g.q, [0.5, 0.5]);
        let (k3, h) = (&g.specs[0], &g.specs[1]);
        assert_eq!((k3.primary, k3.organic, k3.segments, k3.n2_k, k3.d, k3.d_ms, k3.require_farm_complete), (true, false, None, 1, 1, Some(600), false));
        assert_eq!((h.organic, h.segments, h.n2_k, h.require_farm_complete, h.variants.clone()),
                   (true, Some([false, false, true]), 0, true, vec!["flow300_H6".to_owned(), "flow300_tp100_H6".to_owned()]));
        let one = |from: &str, to: &str| parse_groups(&GROUPS.replacen(from, to, 1));
        assert_eq!(one(r#""segments": ["pool_reversed"]"#, r#""segments": []"#).unwrap().specs[1].segments, None);
        assert_eq!(one(r#""d_ms": 600, "farm": "organic""#, r#""d_ms": null, "farm": "organic""#).unwrap().specs[1].d_ms, None);
        for (from, to, why) in [
            (r#""primary": false"#, r#""primary": true"#, "exactly one primary"),
            (r#""primary": true"#, r#""primary": false"#, "exactly one primary"),
            (r#""flow300_H6""#, r#""tp4_T600""#, "twice"),
            (r#""flow300_tp100_H6""#, r#""flow300_H6""#, "twice"),
            (r#""farm": "organic""#, r#""farm": "flagged""#, "all or organic"),
            (r#"["pool_reversed"]"#, r#"["pool"]"#, "unknown segment"),
            (r#""wallet": "wallet-h.json""#, r#""wallet": "wallet-h.json", "families": ["F7"]"#, "no key of the engine's groups_state"),
            (r#""q": 0.5, "require_farm_complete": true"#, r#""require_farm_complete": true"#, "no q"),
            (r#""n2_k": 0"#, r#""n2_k": -1"#, "n2_k"),
            (r#""d_ms": 600, "farm": "organic""#, r#""d_ms": 0, "farm": "organic""#, "d_ms"),
        ] {
            let err = format!("{:#}", one(from, to).unwrap_err());
            assert!(err.contains(why), "{to}: {err}");
        }
        assert!(parse_groups("[]").is_err() && parse_groups("{").is_err());
    }

    #[test]
    fn the_universe_interns_creators_after_the_mints_and_pools() {
        // Mints, then pools, then creators, coin creators and MAYHEM_AGENT (spec §4.4); a NULL quote counted (D5); a pool's
        // token found by its mint, a mint that is no token (here the pool's own address) leaving it tokenless.
        let mut keys = CaseKeys::default();
        let tokens = [
            Token { mint: "M1", create_slot: Some(450_000_000), creator: Some("C1"), mayhem: None, quote_mint: Some(lab_core::legs::WSOL) },
            Token { mint: "M2", create_slot: Some(450_000_000), creator: None, mayhem: Some(false), quote_mint: None },
        ];
        let markets = [
            Market { pool: "P1", mint: "M1", reversed: false, mayhem: false, coin_creator: Some("CC") },
            Market { pool: "P2", mint: "P2", reversed: true, mayhem: false, coin_creator: Some("C1") },
        ];
        let dev2 = Clock::new(SLOT_TIMES[..14].iter().map(|&(e, _, bits)| (e, f64::from_bits(bits))).collect());
        let (u, load) = universe(&mut keys, Bounds::new((446_256_000, 452_304_000), &dev2), &tokens, &markets);
        assert_eq!((0..keys.len() as Id).map(|i| keys.name(i).unwrap()).collect::<Vec<_>>(), ["M1", "M2", "P1", "P2", "C1", "CC", MAYHEM_AGENT]);
        assert_eq!(load, UniverseLoad { tokens: 2, markets: 2, quote_null: 1, interned: 3 });
        assert_eq!((u.mayhem_agent, u.mint(1).map(|m| (m.quote_null, m.creator))), (6, Some((true, NONE))));
        let k = |p| u.f7_key(lab_core::legs::Venue::Pool, p).unwrap();
        assert_eq!((k(2).creator, k(2).coin, k(3).creator, k(3).coin, k(3).seg), (4, 5, 4, 4, Segment::PoolReversed));
        assert_eq!(u.f7_key(lab_core::legs::Venue::Curve, 1), Err(Reject::QuoteNull));
    }

    #[test]
    fn rows_become_the_journals_columns() {
        let mut keys = CaseKeys::default();
        let (pool, trader) = (keys.id("P"), keys.id("T"));
        let row = SignalRow {
            family: Family::N2F7, group: 1, variant: "flow300_H6".into(), key: pool, t: 450_200_000, day: Some("2026-09-22".into()),
            seg: Segment::PoolReversed, depth: 1 << 60, trigger_trader: NONE, farm: Farm::Flagged, farm_complete: None,
            pool_seen_slot: Some(450_190_000), farm_window_complete: Some(1), gate: Gate::FarmFlagged,
        };
        let cells = row_cells(&row, &keys).unwrap();
        let get = |c: &str| cells.iter().find(|x| x.0 == c).map(|x| x.1.clone());
        let text = |s: &str| Some(Cell::Text(s.into()));
        assert_eq!([get("family"), get("key"), get("venue"), get("orientation"), get("farm"), get("status"), get("reason")],
                   [text("N2_F7"), text("P"), text("pool"), text("reversed"), text("flagged"), text("skipped"), text("farm flagged")]);
        assert_eq!([get("depth"), get("trigger_trader"), get("farm_complete"), get("pool_seen_slot"), get("farm_window_complete")],
                   [Some(Cell::Int(1 << 60)), Some(Cell::Null), Some(Cell::Null), Some(Cell::Int(450_190_000)), Some(Cell::Int(1))]);
        assert_eq!((get("block_time"), get("d_ms")), (None, None)); // NULL in a replay: not written
        let pass = SignalRow { family: Family::F7, seg: Segment::Curve, trigger_trader: trader, farm: Farm::NotApplicable, pool_seen_slot: None,
                               farm_window_complete: None, gate: Gate::Pass, ..row.clone() };
        let cells = row_cells(&pass, &keys).unwrap();
        let get = |c: &str| cells.iter().find(|x| x.0 == c).map(|x| x.1.clone());
        assert_eq!([get("status"), get("reason"), get("trigger_trader"), get("orientation"), get("farm"), get("pool_seen_slot")],
                   [Some(Cell::Null), Some(Cell::Null), text("T"), text("curve"), text("n/a"), Some(Cell::Null)]);
        assert!(row_cells(&SignalRow { depth: i128::from(i64::MAX) + 1, ..row }, &keys).is_err());
        assert_eq!(gate_name(Gate::FarmWindowIncomplete), "farm window incomplete");
    }

    #[test]
    fn the_counters_read_per_cause_segment_and_gate() {
        let doc: Value = serde_json::from_str(CASES).unwrap();
        let case = doc["cases"].as_array().unwrap().iter().find(|c| c["name"] == "h_gates").unwrap();
        let (rows, _, eng) = run_case(case).unwrap();
        let v = counts_json(&eng.counts);
        let written: u64 = v["rows"].as_object().unwrap().values().flat_map(|g| g.as_object().unwrap().values()).map(|n| n.as_u64().unwrap()).sum();
        assert_eq!(written, rows.len() as u64 + eng.counts.overwrites); // rows as emitted; the journal keeps one per key
        assert_eq!(v["rejects"].as_object().unwrap().keys().len(), Reject::ALL.len());
        assert!(v["rejects"].get("quote NULL").is_some() && v["rows"]["F7"].get("farm window incomplete").is_some());
        assert_eq!(v["windows"].as_object().unwrap().keys().collect::<Vec<_>>(), ["1042"]);
    }
}
