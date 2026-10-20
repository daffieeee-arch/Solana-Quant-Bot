//! M3-09b (research/m3-09b-follow-source-spec-2026-10-20.md v0.2), PRs 1 and 2: lab-live's follow source, Python
//! LiveFeed (paper/live.py) on a recorder's segments `<root>/segments/<seq>/`, with LiveFeed's admission (spec §2, the
//! strict tier), and `lab-live legs --follow`, its dump in paper.legs_dump's `--segments` form (spec §4.1).
//!
//! Per complete segment, in seq order (live.py `segment_dirs`, `segment_events`):
//! 1. what it creates, before any of its events is handed out (`_new_tokens_and_pools`): first in its own dirs, then in
//!    its `backfill/` when a backfill there is complete or failed with rows (`_backfill_rows`). Tokens from
//!    pump/CreateEvent (the first row per mint; a known mint is not replaced), completions (CompleteEvent, min slot) and
//!    migrations (CompletePumpAmmMigrationEvent) of known tokens without one, pools from CreatePoolEvent (the first row
//!    per pool), then pool_creations and pool_accounts (grouped per pool). A pool classed once is never classed again; one
//!    of another class is known but no SOL market. Each query in #422's order (first slot, then key);
//! 2. its backfill's pool rows (with the rows still pending), in chain order (`_backfill_rows`): those before the segment
//!    are history now, those inside it are merged with its rows below, those after it wait for the next segment. A
//!    history row (`_history`) at a position already in its pool's farm keys, or seen before, is a backfill duplicate;
//!    else it joins the farm keys, sets the pool's context, and a trade is handed out as history (never an event);
//! 3. its block times: each event's own, the clock (an epoch's first block learned, its span, the ms per slot once the
//!    span covers 1,000 slots, in Python's float steps) and the 200-sample window for h24 (`--h24` fixes it);
//! 4. its own rows, admitted by what is known then (`curve_select` and `pool_select` on live_mints and live_pools): curve
//!    rows of known mints, pool rows of known SOL markets. The others are counted, as #430's LiveFeed counts them: per
//!    query and key its rows, a key new while the set of keys seen (capped at 1,000,000) does not hold it;
//! 5. its events: the curve rows, then the pool rows with the backfill rows inside the segment (one at a recorded row's
//!    position left out), in chain order. A row seen recorded before (`_seen`, the last 3,000 slots: a recorder restart
//!    below its last segment) is a duplicate; one first seen as history is an event. Each pool leg takes its pool's
//!    context as read, history rows' included; a backfilled pool's recorded rows join its farm keys;
//! 6. its usage rows, with its seq; and after every 100th segment that has events, the prune (`_prune`) at its last
//!    event's slot with its h24: tokens learned before last - 3 h24 without a graduation leave the known mints, farm
//!    keys before last - h24 go.
//!
//! The release (live.py `events`): a heap by (order, venue, key); after each segment the slots below the highest slot
//! seen are handed out (lag 1). So the end of segment k's last slot sees segment k+1's learns (the lookahead of #420's
//! review). A row of a slot already released is late; one equal to a queued one, or to one released at the last released
//! slot, a duplicate; both are dropped and counted. After the last segment the rest is handed out.
//!
//! Items (source.rs), in LiveFeed's order: after reading a segment its learns (tokens, SOL markets, epochs' ms per slot,
//! the learned rows), its history, its usage rows and the segment itself (seq, h24); then the slots it releases, each
//! event with its arrival.
//!
//! Not here yet (spec §5 and PR 3): the farmer seed and the restart test (PR 3), the farmer flags and their fixes
//! (M3-10: every flag here is false, history legs' too; the dump's comparison masks them), the curve legs for a live
//! curve entry (M3-11), the paper loop, and polling for new segments (this source is a catch-up: the complete segments
//! at its start).

use crate::legs::{event_kind, event_line, pool_leg, pool_line, Keys, PoolRow, DAY_MS};
use crate::legs_read::{each_row, read_curve, read_pool, Admission, Col, Counts, ReplayMem, CLEAN, POOL_NORMAL, POOL_OTHER, POOL_REVERSED};
use crate::rows::Part;
use crate::snapshot::{PoolInfo, Snapshot, TokenInfo};
use crate::source::{Arrival, Item, Learned, Slot, Source};
use anyhow::{bail, Context, Result};
use lab_core::legs::{Event, Id, Leg, Order, PoolCtx, Venue, DEFAULT_KEY, HOLDOUT, WSOL};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::hash_map::Entry;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

/// LiveFeed keeps at most this many unknown pools and as many unknown mints (live.py `unknown_seen`,
/// `unknown_mints_seen`).
pub const UNKNOWN_CAP: usize = 1_000_000;
const EPOCH_SLOTS: u64 = 432_000;
/// The block-time samples for h24 (live.py `ms_window`).
const MS_WINDOW: usize = 200;
/// An epoch's ms per slot is measured once its span covers this many slots.
const SPAN_MIN: u64 = 1_000;
/// A slot is released once a later slot was seen (live.py `lag_slots`).
const LAG_SLOTS: u64 = 1;
/// Row keys of the last this many slots are remembered (live.py SEEN_KEEP_SLOTS): a row that comes twice counts once.
const SEEN_KEEP_SLOTS: u64 = 3_000;
/// Tokens learned live are dropped this many days after their creation without a graduation (live.py TOKEN_KEEP_DAYS).
const TOKEN_KEEP_DAYS: u64 = 3;
/// The prune runs after every this many segments read, when that segment had events (live.py `segment_events`).
const PRUNE_EVERY: u64 = 100;

/// A complete segment (live.py `segment_dirs`): its seq, directory, slots and `_segment.json`.
pub struct SegMeta {
    pub seq: u64,
    pub dir: PathBuf,
    pub first: u64,
    pub last: u64,
    pub meta: Value,
}

/// Python's truth of a JSON value (`if x`): null, false, 0, "" and empty containers are false.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|x| x != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// The complete segments under `<root>/segments`, by seq and then directory (live.py `segment_dirs`: every
/// `_segment.json` whose `complete` is true; glob's `*` skips hidden entries).
pub fn segment_dirs(root: &Path) -> Result<Vec<SegMeta>> {
    let base = root.join("segments");
    let mut out = Vec::new();
    for e in fs::read_dir(&base).with_context(|| format!("read {}", base.display()))? {
        let dir = e?.path();
        if dir.file_name().and_then(|n| n.to_str()).is_none_or(|n| n.starts_with('.')) {
            continue;
        }
        let Ok(raw) = fs::read(dir.join("_segment.json")) else { continue };
        let meta: Value = serde_json::from_slice(&raw).with_context(|| format!("{}/_segment.json", dir.display()))?;
        if !truthy(&meta["complete"]) {
            continue;
        }
        let slot = |k: &str| meta[k].as_u64().with_context(|| format!("{}/_segment.json: no {k}", dir.display()));
        let (seq, first, last) = (slot("seq")?, slot("first_slot")?, slot("last_slot")?);
        if last < first {
            bail!("{}/_segment.json: last_slot {last} before first_slot {first}", dir.display());
        }
        out.push(SegMeta { seq, dir, first, last, meta });
    }
    out.sort_by(|a, b| (a.seq, &a.dir).cmp(&(b.seq, &b.dir)));
    Ok(out)
}

/// LiveFeed reads `<root>/segments` (paper/legs_dump.py `check_root`): refused are a directory named segments, one that
/// holds segment directories itself, and a root without segments/.
pub fn check_root(root: &Path) -> Result<()> {
    let holds_segments = fs::read_dir(root)
        .map(|it| it.filter_map(|e| e.ok()).any(|e| e.path().join("_segment.json").is_file()))
        .unwrap_or(false);
    if root.file_name().is_some_and(|n| n == "segments") || holds_segments {
        bail!("{} holds the segments themselves; give the recorder's directory, its parent: the follow source reads <root>/segments",
              root.display());
    }
    if !root.join("segments").is_dir() {
        bail!("no segments/ under {}: refused", root.display());
    }
    Ok(())
}

/// Python's json.dumps of a value: its default separators, ensure_ascii, an object's keys sorted (legs_dump writes the
/// usage rows with sort_keys; the learned rows' info is a list).
pub fn py_json(v: &Value) -> String {
    let mut s = String::new();
    py_json_into(v, &mut s);
    s
}

fn py_json_into(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => out.push_str(&i.to_string()),
            (None, Some(u)) => out.push_str(&u.to_string()),
            _ => out.push_str(&py_float(n.as_f64().unwrap_or(f64::NAN))),
        },
        Value::String(s) => py_str(s, out),
        Value::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                py_json_into(x, out);
            }
            out.push(']');
        }
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            out.push('{');
            for (i, k) in keys.into_iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                py_str(k, out);
                out.push_str(": ");
                py_json_into(&m[k.as_str()], out);
            }
            out.push('}');
        }
    }
}

/// A JSON string as Python writes it with ensure_ascii: `"`, `\` and the short control escapes, anything else outside
/// printable ASCII as `\uXXXX` (UTF-16, lower-case hex).
fn py_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut buf = [0u16; 2];
                for u in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{u:04x}"));
                }
            }
        }
    }
    out.push('"');
}

/// Python's repr of a float: the shortest digits that round-trip, fixed while the decimal point is within (-4, 16]
/// digits of them (with `.0` when integral), else `d.ddde+XX`.
fn py_float(x: f64) -> String {
    if x.is_nan() {
        return "NaN".to_owned();
    }
    if x.is_infinite() {
        return if x > 0.0 { "Infinity" } else { "-Infinity" }.to_owned();
    }
    if x == 0.0 {
        return if x.is_sign_negative() { "-0.0" } else { "0.0" }.to_owned();
    }
    let sci = format!("{x:e}"); // the shortest round-trip digits, e.g. -1.2345e-7
    let (mant, exp) = sci.split_once('e').expect("{:e} writes an exponent");
    let exp: i32 = exp.parse().expect("an integer exponent");
    let digits: String = mant.chars().filter(char::is_ascii_digit).collect();
    let n = digits.len() as i32;
    let decpt = exp + 1; // x = 0.DIGITS * 10^decpt
    let mut s = String::new();
    if x < 0.0 {
        s.push('-');
    }
    if -4 < decpt && decpt <= 16 {
        if decpt <= 0 {
            s.push_str("0.");
            s.push_str(&"0".repeat((-decpt) as usize));
            s.push_str(&digits);
        } else if decpt >= n {
            s.push_str(&digits);
            s.push_str(&"0".repeat((decpt - n) as usize));
            s.push_str(".0");
        } else {
            s.push_str(&digits[..decpt as usize]);
            s.push('.');
            s.push_str(&digits[decpt as usize..]);
        }
    } else {
        s.push_str(&digits[..1]);
        if n > 1 {
            s.push('.');
            s.push_str(&digits[1..]);
        }
        let e = decpt - 1;
        s.push_str(&format!("e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs()));
    }
    s
}

/// LiveFeed's rule for one query of one segment (live.py `_unknown_pools`, `_unknown_mints`, #430): each key's rows are
/// added to `rows`; a key that `seen` does not hold is new (`keys` + 1) and joins `seen` while it holds fewer than `cap`,
/// so past the cap a key not held counts as new again. Returns the new keys. The keys go in sorted order (LiveFeed's
/// GROUP BY order differs between processes; only which keys the cap admits could depend on it).
fn count_unknown(by_key: &BTreeMap<Option<String>, u64>, seen: &mut HashSet<Option<String>>, cap: usize, rows: &mut u64, keys: &mut u64) -> u64 {
    let mut new = 0;
    for (k, n) in by_key {
        *rows += n;
        if !seen.contains(k) {
            new += 1;
            *keys += 1;
            if seen.len() < cap {
                seen.insert(k.clone());
            }
        }
    }
    new
}

/// LiveFeed's stats (spec §4.2's list; seeded_pools comes with the farmer seed, PR 3, and farmer_flags_fixed with M3-10).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Stats {
    pub segments: u64,
    pub late_rows: u64,
    pub duplicate_rows: u64,
    pub backfill_rows: u64,
    pub backfill_duplicates: u64,
    pub tokens_dropped: u64,
    pub unknown_pools: u64,
    pub unknown_pool_rows: u64,
    pub unknown_pool_other_rows: u64,
    pub unknown_mints: u64,
    pub unknown_mint_rows: u64,
}

impl Stats {
    pub fn json(&self) -> Value {
        json!({"segments": self.segments, "late_rows": self.late_rows, "duplicate_rows": self.duplicate_rows,
               "backfill_rows": self.backfill_rows, "backfill_duplicates": self.backfill_duplicates, "tokens_dropped": self.tokens_dropped,
               "unknown_pools": self.unknown_pools, "unknown_pool_rows": self.unknown_pool_rows,
               "unknown_pool_other_rows": self.unknown_pool_other_rows, "unknown_mints": self.unknown_mints,
               "unknown_mint_rows": self.unknown_mint_rows})
    }
}

/// A row's position (slot, tx_index, outer_ix, inner_ix): "the first row" of a creation query.
type Pos = (u64, u64, u64, u64);
/// A pool creation as `learn_pools` takes it: pool, base mint, quote mint, slot, coin creator, mayhem.
type NewPool = (String, Option<String>, Option<String>, Option<u64>, Option<String>, bool);
/// A segment's arrivals per (slot, tx_index): the earliest received and created time.
type Arrived = HashMap<(u64, u64), (Option<u64>, Option<i64>)>;

/// What reading one segment gives, in LiveFeed's order.
#[derive(Default)]
struct SegOut {
    tokens: Vec<TokenInfo>,
    pools: Vec<PoolInfo>,
    /// the epochs whose ms per slot this segment set, their last value
    epochs: BTreeMap<u64, f64>,
    learned: Vec<Learned>,
    /// backfill trades as history, one leg each (live.py `history`)
    history: Vec<(Id, Leg)>,
    usage: Vec<Value>,
    events: Vec<(Event, Arrival)>,
}

/// How a row key was first seen (live.py `_seen`): recorded (an event) or a backfill's (history only).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Src {
    Rec,
    Hist,
}

/// A row key of `_seen`: curve (0) or pool (1), the mint or pool, the row's position.
type SeenKey = (u8, Id, Order);

/// The heap's order (live.py `events`): (order, venue, key, tie), the venue's name and the key's text as Python
/// compares them.
type HeapKey = (Order, u8, String, u64);

fn venue_code(v: Venue) -> u8 {
    match v {
        Venue::Curve => 0, // "curve" < "pool"
        Venue::Pool => 1,
    }
}

/// The follow source (module doc).
pub struct Follow {
    root: PathBuf,
    /// LiveFeed's live_mints and live_pools, and every key interned so far.
    pub adm: Admission,
    /// LiveFeed's `tokens`: the snapshot's and those created live, by mint.
    tokens: HashMap<Id, TokenInfo>,
    fixed_h24: Option<u64>,
    ctx: HashMap<Id, PoolCtx>,
    ms_window: VecDeque<(u64, i64)>,
    epoch_span: HashMap<u64, [(u64, i64); 2]>,
    /// LiveFeed's `clock.per_epoch`.
    pub per_epoch: BTreeMap<u64, f64>,
    pub stats: Stats,
    unknown_pools: HashSet<Option<String>>,
    unknown_mints: HashSet<Option<String>>,
    /// Kept and dropped rows per table (legs_read.rs `Counts`; a row dropped as unknown is counted there too).
    pub counts: Counts,
    next_seq: u64,
    heap: BTreeMap<HeapKey, (Event, Arrival)>,
    queued: HashSet<(Order, u8, Id)>,
    out_keys: HashSet<(Order, u8, Id)>,
    last_out: Option<u64>,
    top: Option<u64>,
    tie: u64,
    done: bool,
    /// The cap on the unknown sets (UNKNOWN_CAP; a test sets a small one).
    cap: usize,
    /// live.py `seen_keys`, `seen_order`, `seen_hi`: the row keys of the last SEEN_KEEP_SLOTS slots.
    seen_keys: HashMap<SeenKey, Src>,
    seen_order: VecDeque<(u64, SeenKey)>,
    seen_hi: u64,
    /// live.py `farm_keys`: per backfilled pool the positions of its trades already in the farmer history.
    farm_keys: HashMap<Id, BTreeSet<Order>>,
    /// live.py `backfill_pending`: backfill rows past the segment they came with.
    backfill_pending: Vec<PoolRow>,
    /// Kept and dropped backfill rows per table (the backfill's own: its unknown pools are not counted, as in LiveFeed).
    pub backfill_counts: Counts,
}

impl Follow {
    /// A follow source over `<root>/segments` with the snapshot as known before the first live slot (spec §2.1: the
    /// snapshot loaded with `before`), and `h24` fixed when given (LiveFeed's `h24=`).
    pub fn open(root: &Path, snap: Snapshot, h24: Option<u64>) -> Result<Self> {
        let mut adm = Admission::live(&snap)?;
        let mut tokens = HashMap::with_capacity(snap.tokens.len());
        for (m, t) in snap.tokens {
            tokens.insert(adm.learn_mint(&m), t);
        }
        Ok(Follow {
            root: root.to_owned(), adm, tokens, fixed_h24: h24.filter(|&h| h != 0), ctx: HashMap::new(), ms_window: VecDeque::new(),
            epoch_span: HashMap::new(), per_epoch: BTreeMap::new(), stats: Stats::default(), unknown_pools: HashSet::new(),
            unknown_mints: HashSet::new(), counts: Counts::default(), next_seq: 0, heap: BTreeMap::new(), queued: HashSet::new(),
            out_keys: HashSet::new(), last_out: None, top: None, tie: 0, done: false, cap: UNKNOWN_CAP, seen_keys: HashMap::new(),
            seen_order: VecDeque::new(), seen_hi: 0, farm_keys: HashMap::new(), backfill_pending: Vec::new(), backfill_counts: Counts::default(),
        })
    }

    /// LiveFeed's `_h24()`: the fixed one, else a day at the window's ms per slot (first and last sample), else at 400 ms;
    /// Python's round is half to even.
    pub fn h24(&self) -> u64 {
        if let Some(h) = self.fixed_h24 {
            return h;
        }
        if let (Some(&(s0, t0)), Some(&(s1, t1))) = (self.ms_window.front(), self.ms_window.back()) {
            if self.ms_window.len() >= 2 && s1 > s0 && t1 > t0 {
                return (DAY_MS / (((t1 - t0) as f64 * 1000.0) / ((s1 - s0) as f64))).round_ties_even() as u64;
            }
        }
        (DAY_MS / 400.0).round_ties_even() as u64
    }

    fn learn_row(out: &mut SegOut, kind: &'static str, key: &str, seq: u64, slot: Option<u64>, info: Value) {
        out.learned.push(Learned { kind, key: key.to_owned(), seq, slot, info: py_json(&info) });
    }

    fn token_row(out: &mut SegOut, t: &TokenInfo, seq: u64) {
        let info = json!([t.mint, t.create_slot, t.creator, t.mayhem, t.quote_mint, t.complete_slot, t.pool]);
        Self::learn_row(out, "token", &t.mint, seq, t.complete_slot, info);
    }

    /// live.py `_new_tokens_and_pools` on one directory (a segment's own, or its backfill/).
    fn learn(&mut self, dir: &Path, seq: u64, out: &mut SegOut) -> Result<()> {
        // tokens: the first CreateEvent per mint by (slot, tx_index), then ORDER BY slot, mint
        type Create = (Pos, Option<String>, bool, String);
        let mut first: BTreeMap<String, Create> = BTreeMap::new();
        each_row(dir, "pump/CreateEvent", &["mint", "slot", "tx_index", "user", "is_mayhem_mode", "quote_mint", "outer_ix", "inner_ix"],
                 &["mint", "slot", "tx_index", "user", "is_mayhem_mode", "quote_mint"], |c, i| {
            let mint = c[0].str(i)?.context("a CreateEvent without its mint (the recorder writes none)")?;
            let slot = c[1].u64(i)?.context("a CreateEvent without its slot")?;
            let pos = (slot, c[2].u64(i)?.context("a CreateEvent without its tx_index")?, c[6].u64(i)?.unwrap_or(0), c[7].u64(i)?.unwrap_or(0));
            if first.get(mint).is_none_or(|f| pos < f.0) {
                let quote = c[5].str(i)?.unwrap_or(DEFAULT_KEY).to_owned();
                first.insert(mint.to_owned(), (pos, c[3].str(i)?.map(str::to_owned), c[4].bool(i)?.unwrap_or(false), quote));
            }
            Ok(())
        })?;
        let mut creates: Vec<(String, Create)> = first.into_iter().collect();
        creates.sort_by(|a, b| (a.1 .0 .0, &a.0).cmp(&(b.1 .0 .0, &b.0)));
        for (mint, ((slot, ..), creator, mayhem, quote)) in creates {
            if self.adm.known_mint(&mint) {
                continue;
            }
            let id = self.adm.learn_mint(&mint);
            let t = TokenInfo { mint, create_slot: Some(slot), creator, mayhem: Some(mayhem), quote_mint: Some(quote), complete_slot: None, pool: None };
            out.tokens.push(t.clone());
            self.tokens.insert(id, t);
        }
        // completions: min(slot) per mint, ORDER BY slot (NULLS LAST), mint; a known token without one
        let mut done: BTreeMap<String, Option<u64>> = BTreeMap::new();
        each_row(dir, "pump/CompleteEvent", &["mint", "slot"], &["mint", "slot"], |c, i| {
            if let Some(mint) = c[0].str(i)? {
                let slot = c[1].u64(i)?;
                let e = done.entry(mint.to_owned()).or_insert(slot);
                *e = match (*e, slot) {
                    (Some(a), Some(b)) => Some(a.min(b)),
                    (a, b) => a.or(b),
                };
            }
            Ok(())
        })?;
        let mut done: Vec<(String, Option<u64>)> = done.into_iter().collect();
        done.sort_by(|a, b| (a.1.is_none(), a.1, &a.0).cmp(&(b.1.is_none(), b.1, &b.0)));
        for (mint, slot) in done {
            let Some(t) = self.adm.keys.get(&mint).and_then(|id| self.tokens.get_mut(&id)) else { continue };
            if t.complete_slot.is_none() {
                t.complete_slot = slot;
                let t = t.clone();
                Self::token_row(out, &t, seq);
                out.tokens.push(t);
            }
        }
        // migrations: any_value(pool) per mint (the first non-null by position), ORDER BY min(slot), mint; a known
        // token without a pool
        type Mig = (Option<u64>, Option<(Pos, String)>);
        let mut mig: BTreeMap<String, Mig> = BTreeMap::new();
        each_row(dir, "pump/CompletePumpAmmMigrationEvent", &["mint", "pool", "slot", "tx_index", "outer_ix", "inner_ix"], &["mint", "pool", "slot"], |c, i| {
            if let Some(mint) = c[0].str(i)? {
                let slot = c[2].u64(i)?;
                let e = mig.entry(mint.to_owned()).or_insert((slot, None));
                e.0 = match (e.0, slot) {
                    (Some(a), Some(b)) => Some(a.min(b)),
                    (a, b) => a.or(b),
                };
                if let Some(p) = c[1].str(i)? {
                    let pos = (slot.unwrap_or(u64::MAX), c[3].u64(i)?.unwrap_or(u64::MAX), c[4].u64(i)?.unwrap_or(0), c[5].u64(i)?.unwrap_or(0));
                    if e.1.as_ref().is_none_or(|(q, _)| pos < *q) {
                        e.1 = Some((pos, p.to_owned()));
                    }
                }
            }
            Ok(())
        })?;
        let mut mig: Vec<(String, Mig)> = mig.into_iter().collect();
        mig.sort_by(|a, b| (a.1 .0.is_none(), a.1 .0, &a.0).cmp(&(b.1 .0.is_none(), b.1 .0, &b.0)));
        for (mint, (_, pool)) in mig {
            let Some(t) = self.adm.keys.get(&mint).and_then(|id| self.tokens.get_mut(&id)) else { continue };
            if t.pool.is_none() {
                t.pool = pool.map(|(_, p)| p);
                let t = t.clone();
                Self::token_row(out, &t, seq);
                out.tokens.push(t);
            }
        }
        // pools: CreatePoolEvent (the first row per pool by slot, ORDER BY slot, pool), then pool_creations and
        // pool_accounts (grouped per pool, ORDER BY min(slot), pool)
        let mut created: BTreeMap<String, (Pos, NewPool)> = BTreeMap::new();
        each_row(dir, "pump_amm/CreatePoolEvent", &["pool", "base_mint", "quote_mint", "slot", "coin_creator", "is_mayhem_mode", "tx_index", "outer_ix", "inner_ix"],
                 &["pool", "base_mint", "quote_mint", "slot", "coin_creator", "is_mayhem_mode"], |c, i| {
            let pool = c[0].str(i)?.context("a CreatePoolEvent without its pool (the recorder writes none)")?;
            let slot = c[3].u64(i)?;
            let pos = (slot.unwrap_or(u64::MAX), c[6].u64(i)?.unwrap_or(u64::MAX), c[7].u64(i)?.unwrap_or(0), c[8].u64(i)?.unwrap_or(0));
            if created.get(pool).is_none_or(|(q, _)| pos < *q) {
                let row = (pool.to_owned(), c[1].str(i)?.map(str::to_owned), c[2].str(i)?.map(str::to_owned), slot,
                           c[4].str(i)?.map(str::to_owned), c[5].bool(i)?.unwrap_or(false));
                created.insert(pool.to_owned(), (pos, row));
            }
            Ok(())
        })?;
        let mut rows: Vec<NewPool> = created.into_values().map(|(_, r)| r).collect();
        rows.sort_by(|a, b| (a.3.is_none(), a.3, &a.0).cmp(&(b.3.is_none(), b.3, &b.0)));
        self.learn_pools(rows, seq, out)?;
        for table in ["pool_creations", "pool_accounts"] {
            let mut grouped: BTreeMap<String, NewPool> = BTreeMap::new();
            let cols = ["pool", "base_mint", "quote_mint", "slot", "coin_creator", "is_mayhem_mode"];
            each_row(dir, table, &cols, &cols, |c, i| {
                let pool = c[0].str(i)?.with_context(|| format!("a {table} row without its pool (the recorder writes none)"))?;
                let e = grouped.entry(pool.to_owned()).or_insert_with(|| (pool.to_owned(), None, None, None, None, false));
                let first = |old: &mut Option<String>, v: Option<&str>| {
                    if old.is_none() {
                        *old = v.map(str::to_owned);
                    }
                };
                first(&mut e.1, c[1].str(i)?);
                first(&mut e.2, c[2].str(i)?);
                first(&mut e.4, c[4].str(i)?);
                e.3 = match (e.3, c[3].u64(i)?) {
                    (Some(a), Some(b)) => Some(a.min(b)),
                    (a, b) => a.or(b),
                };
                e.5 |= c[5].bool(i)?.unwrap_or(false);
                Ok(())
            })?;
            let mut rows: Vec<NewPool> = grouped.into_values().collect();
            rows.sort_by(|a, b| (a.3.is_none(), a.3, &a.0).cmp(&(b.3.is_none(), b.3, &b.0)));
            self.learn_pools(rows, seq, out)?;
        }
        Ok(())
    }

    /// The pools of one creation query, in its order: a pool not classed yet is classed (reversed when WSOL is its
    /// base, sol when WSOL is its quote, else other); a SOL market is learned with its PoolInfo, and every one is a
    /// learned row.
    fn learn_pools(&mut self, rows: Vec<NewPool>, seq: u64, out: &mut SegOut) -> Result<()> {
        for (pool, base, quote, slot, creator, mayhem) in rows {
            if self.adm.known_pool(&pool) {
                continue;
            }
            let (cls, orient, code) = if base.as_deref() == Some(WSOL) {
                ("reversed", "reversed", POOL_REVERSED)
            } else if quote.as_deref() == Some(WSOL) {
                ("sol", "normal", POOL_NORMAL)
            } else {
                ("other", "normal", POOL_OTHER)
            };
            self.adm.learn_pool(&pool, code);
            if code == POOL_OTHER {
                Self::learn_row(out, "pool", &pool, seq, slot, json!([cls, orient]));
                continue;
            }
            let mint = (if code == POOL_REVERSED { quote } else { base }).with_context(|| format!("SOL market {pool}: no mint (the recorder writes one)"))?;
            Self::learn_row(out, "pool", &pool, seq, slot, json!([cls, orient, pool, mint, orient, cls, mayhem, creator, slot]));
            out.pools.push(PoolInfo { pool, mint, orientation: Some(orient.to_owned()), quote_class: Some(cls.to_owned()), mayhem,
                                      coin_creator: creator, create_slot: slot });
        }
        Ok(())
    }

    /// One block with its time, in slot order (live.py `segment_events`): the h24 window, the epoch's first block learned,
    /// its span and, once that covers SPAN_MIN slots, its ms per slot in Python's float steps.
    fn clock_block(&mut self, slot: u64, t: i64, seq: u64, out: &mut SegOut) {
        if self.ms_window.len() == MS_WINDOW {
            self.ms_window.pop_front();
        }
        self.ms_window.push_back((slot, t));
        let e = slot / EPOCH_SLOTS;
        let span = match self.epoch_span.entry(e) {
            Entry::Vacant(v) => {
                Self::learn_row(out, "epoch", &e.to_string(), seq, Some(slot), json!([slot, t]));
                v.insert([(slot, t), (slot, t)])
            }
            Entry::Occupied(o) => o.into_mut(),
        };
        span[1] = (slot, t);
        let [(s0, t0), (s1, t1)] = *span;
        if s1 >= s0 && s1 - s0 >= SPAN_MIN {
            let ms = ((t1 - t0) as f64 * 1000.0) / ((s1 - s0) as f64);
            self.per_epoch.insert(e, ms);
            out.epochs.insert(e, ms);
        }
    }

    /// live.py `_seen`: how a row key was first seen in the last SEEN_KEEP_SLOTS slots, None the first time (then it is
    /// remembered as `src`, when its slot is within the window of the highest slot seen). Keys leave in the order they
    /// came, once the oldest is more than SEEN_KEEP_SLOTS behind a new highest slot.
    fn seen(&mut self, key: SeenKey, slot: u64, src: Src) -> Option<Src> {
        if let Some(&prev) = self.seen_keys.get(&key) {
            return Some(prev);
        }
        if slot + SEEN_KEEP_SLOTS >= self.seen_hi {
            self.seen_keys.insert(key, src);
            self.seen_order.push_back((slot, key));
            if slot > self.seen_hi {
                self.seen_hi = slot;
                while self.seen_order.front().is_some_and(|f| f.0 + SEEN_KEEP_SLOTS < slot) {
                    let (_, k) = self.seen_order.pop_front().expect("checked");
                    self.seen_keys.remove(&k);
                }
            }
        }
        None
    }

    /// live.py `_history`: a backfill row, history only, counted once. A position already in the pool's farm keys or
    /// seen before is a backfill duplicate; else it joins the farm keys, sets the pool's context, and a trade is handed
    /// out as history.
    fn history(&mut self, r: &PoolRow, out: &mut SegOut) -> Result<()> {
        let keyed = self.farm_keys.entry(r.pool).or_default().contains(&r.order); // the entry is made here, as defaultdict's
        if keyed || self.seen((1, r.pool, r.order), r.order.slot, Src::Hist).is_some() {
            self.stats.backfill_duplicates += 1;
            return Ok(());
        }
        self.farm_keys.entry(r.pool).or_default().insert(r.order);
        self.stats.backfill_rows += 1;
        let leg = pool_leg(r, self.ctx.entry(r.pool).or_default())?;
        if r.kind.is_trade() {
            out.history.push((r.pool, Leg::Pool(leg)));
        }
        Ok(())
    }

    /// live.py `_prune(last, h24)`, the admission's part: tokens learned live that are older than TOKEN_KEEP_DAYS days
    /// without a graduation leave the known mints (their later curve rows are unknown), and each pool's farm keys before
    /// last - h24 go (a pool left without keys is no longer a backfilled pool). The farmer history and the curve legs
    /// are M3-10's and M3-11's.
    fn prune(&mut self, last: u64, h24: u64) {
        let bound = last.checked_sub(TOKEN_KEEP_DAYS * h24);
        let old: Vec<Id> = self.tokens.iter()
            .filter(|(_, t)| t.complete_slot.is_none() && t.pool.is_none() && t.create_slot.is_some_and(|c| bound.is_some_and(|b| c < b)))
            .map(|(&id, _)| id)
            .collect();
        for id in &old {
            self.tokens.remove(id);
            self.adm.forget_mint(*id);
        }
        self.stats.tokens_dropped += old.len() as u64;
        let cut = last.checked_sub(h24).filter(|&b| b > 0);
        self.farm_keys.retain(|_, keys| {
            if let Some(b) = cut {
                *keys = keys.split_off(&Order { slot: b, tx: 0, outer: 0, inner: 0 });
            }
            !keys.is_empty()
        });
    }

    /// One segment (module doc, steps 1-5).
    fn read_segment(&mut self, s: &SegMeta) -> Result<SegOut> {
        let mut out = SegOut::default();
        self.learn(&s.dir, s.seq, &mut out)?;
        // live.py `_backfill_rows`: the pending rows and, from a done backfill, its creations, then its pool rows (the
        // admission after them; its unknown pools are not counted), in chain order
        let mut back = std::mem::take(&mut self.backfill_pending);
        if let Some(bdir) = backfill_dir(s)? {
            self.learn(&bdir, s.seq, &mut out)?;
            let on = std::mem::replace(&mut self.adm.unknown.on, false);
            let rows = read_pool(&Part { lo: 0, hi: u64::MAX, dir: bdir }, &mut self.adm, (0, u64::MAX), &mut self.backfill_counts);
            self.adm.unknown.on = on;
            let mut rows = rows?;
            rows.sort_by_key(|r| r.order);
            back.extend(rows);
        }
        back.sort_by_key(|r| r.order); // stable: the pending rows first on an equal position
        // before the segment: history now; inside it: merged with its rows below; after it: the next segment's
        for r in back.iter().filter(|r| r.order.slot < s.first) {
            self.history(r, &mut out)?;
        }
        let inside: Vec<PoolRow> = back.iter().filter(|r| (s.first..=s.last).contains(&r.order.slot)).copied().collect();
        self.backfill_pending = back.into_iter().filter(|r| r.order.slot > s.last).collect();
        let arrived = arrivals(&s.dir)?;
        let written = match &s.meta["written_unix"] {
            Value::Null => None,
            v => Some(v.as_u64().with_context(|| format!("{}/_segment.json: written_unix {v} (the recorder writes whole seconds)", s.dir.display()))?),
        };
        let bt = blocks(&s.dir)?;
        for (&slot, &t) in &bt {
            if let Some(t) = t {
                self.clock_block(slot, t, s.seq, &mut out);
            }
        }
        // its own rows, admitted by what is known now, the unknown ones counted
        let part = Part { lo: 0, hi: u64::MAX, dir: s.dir.clone() };
        let mut curve = read_curve(&part, &mut self.adm, (0, u64::MAX), &mut self.counts)?;
        let mut pool = read_pool(&part, &mut self.adm, (0, u64::MAX), &mut self.counts)?;
        self.count_unknowns(s.seq);
        curve.sort_by_key(|l| l.order);
        pool.sort_by_key(|r| r.order);
        let arrival = |o: Order| {
            let (received, created) = arrived.get(&(o.slot, u64::from(o.tx))).copied().unwrap_or((None, None));
            Arrival { received, created, written }
        };
        let at = |slot: u64| bt.get(&slot).copied().flatten();
        out.events.reserve(curve.len() + pool.len());
        // the curve rows first, a row seen before (a recorder restart below its last segment) a duplicate
        for l in &curve {
            if self.seen((0, l.mint, l.order), l.order.slot, Src::Rec).is_some() {
                self.stats.duplicate_rows += 1;
                continue;
            }
            out.events.push((Event::new(Leg::Curve(*l), at(l.order.slot)), arrival(l.order)));
        }
        // then the pool rows with the backfill rows inside the segment merged in (history, never events; one at the
        // position of a recorded row is left out), in chain order: the pool context follows both
        let have: HashSet<Order> = pool.iter().map(|r| r.order).collect();
        let mut merged: Vec<(PoolRow, bool)> = pool.into_iter().map(|r| (r, true)).collect();
        merged.extend(inside.into_iter().filter(|r| !have.contains(&r.order)).map(|r| (r, false)));
        merged.sort_by_key(|(r, _)| r.order);
        for (r, recorded) in &merged {
            if !recorded {
                self.history(r, &mut out)?;
                continue;
            }
            if self.seen((1, r.pool, r.order), r.order.slot, Src::Rec) == Some(Src::Rec) {
                self.stats.duplicate_rows += 1;
                continue;
            }
            // a backfilled pool: its recorded trades join its farm keys, so a later backfill of them counts once
            if let Some(keys) = self.farm_keys.get_mut(&r.pool) {
                keys.insert(r.order);
            }
            let leg = pool_leg(r, self.ctx.entry(r.pool).or_default())?;
            out.events.push((Event::new(Leg::Pool(leg), at(r.order.slot)), arrival(r.order)));
        }
        out.events.sort_by_key(|(e, _)| e.leg.order()); // stable: curve first on an equal order, as Python's list.sort
        match &s.meta["usage"] {
            Value::Null => {}
            Value::Array(rows) => {
                for u in rows {
                    let mut u = u.as_object().with_context(|| format!("{}/_segment.json: a usage row {u} that is no object", s.dir.display()))?.clone();
                    u.insert("seq".to_owned(), json!(s.seq));
                    out.usage.push(Value::Object(u));
                }
            }
            v => bail!("{}/_segment.json: usage {v} is no list", s.dir.display()),
        }
        self.stats.segments += 1;
        // bounded memory in a long live run: the prune at the last event's slot, with this segment's h24
        if self.stats.segments % PRUNE_EVERY == 0 {
            if let Some((e, _)) = out.events.last() {
                let (last, h24) = (e.leg.slot(), self.h24());
                self.prune(last, h24);
            }
        }
        Ok(out)
    }

    /// The unknown rows of the segment just read (`Admission::unknown`), by LiveFeed's rule: pools (trades, then the
    /// other rows) and mints, each set capped.
    fn count_unknowns(&mut self, seq: u64) {
        let u = std::mem::take(&mut self.adm.unknown);
        let st = &mut self.stats;
        let mut new = count_unknown(&u.trades, &mut self.unknown_pools, self.cap, &mut st.unknown_pool_rows, &mut st.unknown_pools);
        new += count_unknown(&u.other, &mut self.unknown_pools, self.cap, &mut st.unknown_pool_other_rows, &mut st.unknown_pools);
        if new > 0 {
            log::warn!("segment {seq}: {new} new unknown pools, their rows dropped ({} unknown pools, {} trade and {} other rows so far)",
                       st.unknown_pools, st.unknown_pool_rows, st.unknown_pool_other_rows);
        }
        let new = count_unknown(&u.mints, &mut self.unknown_mints, self.cap, &mut st.unknown_mint_rows, &mut st.unknown_mints);
        if new > 0 {
            log::warn!("segment {seq}: {new} new unknown mints, their curve rows dropped ({} unknown mints, {} rows so far)",
                       st.unknown_mints, st.unknown_mint_rows);
        }
        self.adm.unknown.on = u.on;
    }

    /// A segment's events into the heap (live.py `events`): late and duplicate rows dropped and counted.
    fn push(&mut self, events: Vec<(Event, Arrival)>) {
        for (e, a) in events {
            let (order, slot) = (e.leg.order(), e.leg.slot());
            if self.last_out.is_some_and(|l| slot < l) {
                self.stats.late_rows += 1; // arrived after its slot was released
                continue;
            }
            let k = (order, venue_code(e.venue), e.key);
            if self.queued.contains(&k) || (self.last_out == Some(slot) && self.out_keys.contains(&k)) {
                self.stats.duplicate_rows += 1;
                continue;
            }
            self.queued.insert(k);
            let name = self.adm.keys.name(e.key).unwrap_or_default().to_owned();
            self.heap.insert((order, k.1, name, self.tie), (e, a));
            self.tie += 1;
            self.top = Some(self.top.map_or(slot, |t| t.max(slot)));
        }
    }

    /// The slots below the highest seen minus the lag, in the heap's order (all of them at the end), into `evs` and
    /// `arrs`.
    fn release(&mut self, end: bool, evs: &mut Vec<Event>, arrs: &mut Vec<Arrival>) {
        evs.clear();
        arrs.clear();
        while let Some(entry) = self.heap.first_entry() {
            let slot = entry.key().0.slot;
            if !end && self.top.is_none_or(|t| slot.saturating_add(LAG_SLOTS) > t) {
                break;
            }
            let ((order, v, _, _), (e, a)) = entry.remove_entry();
            let k = (order, v, e.key);
            self.queued.remove(&k);
            if !end {
                if self.last_out != Some(slot) {
                    self.out_keys.clear();
                }
                self.out_keys.insert(k);
                self.last_out = Some(slot);
            }
            evs.push(e);
            arrs.push(a);
        }
    }

    /// The memory view of `--mem-log` (only the segments read here; the follow source's own structures are listed with
    /// spec §4.5, a later PR).
    fn mem(&self) -> ReplayMem {
        ReplayMem { segments: Some(self.stats.segments), ..ReplayMem::default() }
    }
}

/// The released events as slot items: consecutive events of one slot together.
fn emit_slots(keys: &Keys, mem: &ReplayMem, evs: &[Event], arrs: &[Arrival], f: &mut dyn FnMut(&Keys, &ReplayMem, Item<'_>) -> Result<()>) -> Result<()> {
    let mut i = 0;
    while i < evs.len() {
        let slot = evs[i].leg.slot();
        let j = i + evs[i..].iter().take_while(|e| e.leg.slot() == slot).count();
        f(keys, mem, Item::Slot(Slot { slot, events: &evs[i..j], arrivals: Some(&arrs[i..j]) }))?;
        i = j;
    }
    Ok(())
}

impl Source for Follow {
    fn for_each_item(&mut self, f: &mut dyn FnMut(&Keys, &ReplayMem, Item<'_>) -> Result<()>) -> Result<()> {
        if std::mem::replace(&mut self.done, true) {
            bail!("a follow source hands out its items once");
        }
        let segs: Vec<SegMeta> = segment_dirs(&self.root)?.into_iter().filter(|s| s.seq >= self.next_seq).collect();
        let (mut evs, mut arrs) = (Vec::new(), Vec::new());
        for (n, s) in segs.iter().enumerate() {
            let out = self.read_segment(s)?;
            let (mem, h24) = (self.mem(), self.h24());
            let keys = &self.adm.keys;
            for t in &out.tokens {
                f(keys, &mem, Item::Token(t))?;
            }
            for p in &out.pools {
                f(keys, &mem, Item::Pool(p))?;
            }
            for (&epoch, &ms) in &out.epochs {
                f(keys, &mem, Item::EpochMs { epoch, ms })?;
            }
            for l in &out.learned {
                f(keys, &mem, Item::Learned(l))?;
            }
            for (pool, leg) in &out.history {
                f(keys, &mem, Item::History { pool: *pool, legs: std::slice::from_ref(leg) })?;
            }
            for u in &out.usage {
                f(keys, &mem, Item::Usage(u))?;
            }
            f(keys, &mem, Item::Segment { seq: s.seq, h24 })?;
            self.push(out.events);
            self.next_seq = s.seq + 1;
            self.release(false, &mut evs, &mut arrs);
            emit_slots(&self.adm.keys, &mem, &evs, &arrs, f)?;
            if (n + 1) % 500 == 0 {
                log::info!("follow: {} segments, {} keys, {} queued, RSS {} MB", n + 1, self.adm.keys.len(), self.heap.len(),
                           crate::mem::mb(crate::mem::rss_bytes()));
            }
        }
        self.release(true, &mut evs, &mut arrs);
        let mem = self.mem();
        emit_slots(&self.adm.keys, &mem, &evs, &arrs, f)
    }
}

/// The segment's backfill/ when LiveFeed reads it (`_backfill_rows`): a backfill of `_segment.json` complete or failed
/// with rows, and the directory there.
fn backfill_dir(s: &SegMeta) -> Result<Option<PathBuf>> {
    let list = match &s.meta["backfills"] {
        Value::Null => return Ok(None),
        Value::Array(a) => a,
        v => bail!("{}/_segment.json: backfills {v} is no list", s.dir.display()),
    };
    let mut done = false;
    for b in list {
        let o = b.as_object().with_context(|| format!("{}/_segment.json: a backfill {b} that is no object", s.dir.display()))?;
        done |= matches!(o.get("status").and_then(Value::as_str), Some("complete" | "failed")) && o.get("rows").is_some_and(truthy);
    }
    let dir = s.dir.join("backfill");
    Ok((done && dir.is_dir()).then_some(dir))
}

/// `TRY_CAST(x AS BIGINT)` of an integer column: beyond BIGINT is NULL.
fn try_i64(c: &Col, i: usize) -> Result<Option<i64>> {
    if c.null(i) {
        return Ok(None);
    }
    match c {
        Col::U(a) => Ok(i64::try_from(a.value(i)).ok()),
        Col::I(a) => Ok(Some(a.value(i))),
        _ => bail!("created_unix_ns: an integer column of another type"),
    }
}

fn min_opt<T: Ord>(a: Option<T>, b: Option<T>) -> Option<T> {
    match (a, b) {
        (Some(a), Some(b)) => Some(a.min(b)),
        (a, b) => a.or(b),
    }
}

/// The segment's arrivals (live.py `_arrivals`): per (slot, tx_index) the earliest received and created time.
fn arrivals(dir: &Path) -> Result<Arrived> {
    let mut m = Arrived::new();
    each_row(dir, "arrivals", &["slot", "tx_index", "received_unix_ns", "created_unix_ns"], &["slot", "tx_index", "received_unix_ns"], |c, i| {
        let (Some(slot), Some(tx)) = (c[0].u64(i)?, c[1].u64(i)?) else { return Ok(()) }; // no event has a NULL position
        let (r, cr) = (c[2].u64(i)?, try_i64(&c[3], i)?);
        let e = m.entry((slot, tx)).or_insert((r, cr));
        *e = (min_opt(e.0, r), min_opt(e.1, cr));
        Ok(())
    })?;
    Ok(m)
}

/// The segment's block times (live.py: `any_value(block_time)` per slot of the blocks not skipped, a NULL skipped
/// counting as not skipped), in slot order; two times for one slot are refused.
fn blocks(dir: &Path) -> Result<BTreeMap<u64, Option<i64>>> {
    let mut m: BTreeMap<u64, Option<i64>> = BTreeMap::new();
    each_row(dir, "blocks", &["slot", "block_time", "skipped"], &["slot", "block_time", "skipped"], |c, i| {
        if c[2].bool(i)? == Some(true) {
            return Ok(());
        }
        let slot = c[0].u64(i)?.context("a block without its slot")?;
        let t = c[1].bigint(i)?;
        match m.get(&slot) {
            Some(Some(old)) if t.is_some_and(|t| t != *old) => bail!("slot {slot} has two block times"),
            Some(Some(_)) => {}
            _ => {
                m.insert(slot, t);
            }
        }
        Ok(())
    })?;
    Ok(m)
}

/// `lab-live legs --follow` (spec §4.1).
pub struct FollowArgs {
    pub root: PathBuf,
    pub snapshot: PathBuf,
    pub snapshot_before: Option<u64>,
    pub h24: Option<u64>,
    /// the window grid's origin
    pub from: u64,
    /// the window grid's end (None: the last window's end is nominal)
    pub to: Option<u64>,
    pub window: u64,
    pub summary: PathBuf,
    pub out: Option<PathBuf>,
    pub out_from: u64,
    pub expect_segments: Option<u64>,
}

/// A count and the sha256 of lines (legs_dump `_Digest`).
#[derive(Default)]
struct Lines {
    n: u64,
    sha: Sha256,
}

impl Lines {
    fn add(&mut self, s: &str) {
        self.n += 1;
        self.sha.update(s.as_bytes());
        self.sha.update(b"\n");
    }

    fn result(self) -> Value {
        json!({"count": self.n, "sha256": hex(self.sha)})
    }
}

fn hex(h: Sha256) -> String {
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// One window [a, b) of the dump: its count and the sha256 of its lines, with and without the farmer flag.
struct Window {
    a: i128,
    b: i128,
    n: u64,
    sha: Sha256,
    nf: Sha256,
}

/// paper/legs_dump.py `_summary` with `no_farm`, `slot_end` (its `drain`) and `arrivals`, over the follow source's items.
struct Dump {
    from: u64,
    to: Option<u64>,
    window: u64,
    events: u64,
    total: Sha256,
    nf_total: Sha256,
    kinds: BTreeMap<&'static str, u64>,
    windows: Vec<Value>,
    cur: Option<Window>,
    slot: Option<u64>,
    out: Option<std::io::BufWriter<fs::File>>,
    out_from: u64,
    learned: Lines,
    new_pools: Lines,
    usage: Lines,
    arrivals: Lines,
    history: Lines,
    history_nf: Lines,
    /// history lines without their `at` (the slot whose end drains them)
    pend_history: Vec<String>,
    pend_learned: Vec<String>,
    pend_pools: Vec<String>,
    pend_usage: Vec<String>,
    seq: Option<u64>,
    h24: Option<u64>,
    clock_h24: Vec<Value>,
    per_epoch: BTreeMap<u64, f64>,
    items: BTreeMap<&'static str, u64>,
}

/// The event's line with a pool line's farmer flag (its last field) as `-` (legs_dump `no_farmer`).
fn no_farmer(ev: &Event, s: &str) -> String {
    match ev.venue {
        Venue::Curve => s.to_owned(),
        Venue::Pool => format!("{}\t-", &s[..s.rfind('\t').expect("a pool line has fields")]),
    }
}

fn opt_str<T: ToString>(v: Option<T>) -> String {
    v.map_or_else(|| "null".to_owned(), |x| x.to_string())
}

impl Dump {
    fn new(a: &FollowArgs) -> Result<Self> {
        let out = match &a.out {
            Some(p) => Some(std::io::BufWriter::new(fs::File::create(p).with_context(|| format!("create {}", p.display()))?)),
            None => None,
        };
        Ok(Dump {
            from: a.from, to: a.to, window: a.window, events: 0, total: Sha256::new(), nf_total: Sha256::new(), kinds: BTreeMap::new(),
            windows: Vec::new(), cur: None, slot: None, out, out_from: a.out_from, learned: Lines::default(), new_pools: Lines::default(),
            usage: Lines::default(), arrivals: Lines::default(), history: Lines::default(), history_nf: Lines::default(),
            pend_history: Vec::new(), pend_learned: Vec::new(), pend_pools: Vec::new(), pend_usage: Vec::new(),
            seq: None, h24: None, clock_h24: Vec::new(), per_epoch: BTreeMap::new(), items: BTreeMap::new(),
        })
    }

    fn item(&mut self, keys: &Keys, item: Item<'_>) -> Result<()> {
        let name = match item {
            Item::Slot(sl) => {
                let arr = sl.arrivals.context("the follow source gives every event its arrival")?;
                for (ev, a) in sl.events.iter().zip(arr) {
                    self.event(keys, ev, a)?;
                }
                "slot"
            }
            // created (no graduation yet) or changed (its completion or migration)
            Item::Token(t) => if t.complete_slot.is_none() && t.pool.is_none() { "token_created" } else { "token_changed" },
            Item::Pool(p) => {
                self.pend_pools.push(p.pool.clone());
                "pool"
            }
            Item::EpochMs { epoch, ms } => {
                self.per_epoch.insert(epoch, ms);
                "epoch_ms"
            }
            Item::Learned(l) => {
                self.pend_learned.push(l.line());
                "learned"
            }
            Item::History { legs, .. } => {
                // legs_dump `history_line` without its `at`: the pool, the leg's position and its pool fields (the event
                // line's, after its venue, key, position, block time and tau)
                for leg in legs {
                    let Leg::Pool(l) = leg else { bail!("a curve leg as history (backfills are pool rows)") };
                    let line = pool_line(keys, l, None);
                    let f: Vec<&str> = line.split('\t').collect();
                    self.pend_history.push(format!("{}\t{}", f[1..6].join("\t"), f[8..].join("\t")));
                }
                "history"
            }
            Item::Usage(u) => {
                self.pend_usage.push(py_json(u));
                "usage"
            }
            Item::Segment { seq, h24 } => {
                (self.seq, self.h24) = (Some(seq), Some(h24));
                "segment"
            }
        };
        *self.items.entry(name).or_default() += 1;
        Ok(())
    }

    /// legs_dump `drain` at a slot end (when the next slot's first event arrives, and at the end): the learned rows and new
    /// pools in sorted order per drain, the usage rows in order; and the clock's h24 when it changed, with the segment
    /// being read.
    fn drain(&mut self) {
        let at = opt_str(self.slot);
        for h in std::mem::take(&mut self.pend_history) {
            let line = format!("{at}\t{h}");
            self.history.add(&line);
            self.history_nf.add(&format!("{}\t-", &line[..line.rfind('\t').expect("a history line has fields")]));
        }
        self.pend_learned.sort();
        for l in self.pend_learned.drain(..) {
            self.learned.add(&l);
        }
        self.pend_pools.sort();
        for p in self.pend_pools.drain(..) {
            self.new_pools.add(&p);
        }
        for u in self.pend_usage.drain(..) {
            self.usage.add(&u);
        }
        if let Some(h) = self.h24 {
            if self.clock_h24.last().and_then(|x| x[1].as_u64()) != Some(h) {
                self.clock_h24.push(json!([self.seq, h]));
            }
        }
    }

    fn close_window(&mut self) {
        if let Some(w) = self.cur.take() {
            self.windows.push(json!([w.a, w.b, w.n, hex(w.sha), hex(w.nf)]));
        }
    }

    fn event(&mut self, keys: &Keys, ev: &Event, arr: &Arrival) -> Result<()> {
        let slot = ev.leg.slot();
        if self.slot.is_some_and(|s| s != slot) {
            self.drain();
        }
        self.slot = Some(slot);
        let line = event_line(keys, ev);
        let nf = no_farmer(ev, &line);
        let (lo, w) = (i128::from(self.from), i128::from(self.window));
        let a = lo + (i128::from(slot) - lo).div_euclid(w) * w;
        if self.cur.as_ref().is_none_or(|c| c.a != a) {
            self.close_window();
            let b = self.to.map_or(a + w, |to| (a + w).min(i128::from(to)));
            self.cur = Some(Window { a, b, n: 0, sha: Sha256::new(), nf: Sha256::new() });
        }
        let c = self.cur.as_mut().expect("set above");
        c.n += 1;
        for (h, s) in [(&mut c.sha, &line), (&mut self.total, &line), (&mut c.nf, &nf), (&mut self.nf_total, &nf)] {
            h.update(s.as_bytes());
            h.update(b"\n");
        }
        let o = ev.leg.order();
        self.arrivals.add(&format!("{}\t{}\t{}\t{}\t{}\t{}\t{}", o.slot, o.tx, o.outer, o.inner, opt_str(arr.received), opt_str(arr.created),
                                   opt_str(arr.written)));
        self.events += 1;
        *self.kinds.entry(event_kind(ev)).or_default() += 1;
        if let Some(f) = self.out.as_mut().filter(|_| slot >= self.out_from) {
            writeln!(f, "{line}").context("write the lines")?;
        }
        Ok(())
    }

    fn finish(mut self) -> Result<Value> {
        self.drain(); // the end of the last slot (legs_dump calls slot_end once more after the loop)
        self.close_window();
        if let Some(mut f) = self.out.take() {
            f.flush()?;
        }
        let per_epoch: serde_json::Map<String, Value> = self.per_epoch.iter().map(|(e, ms)| (e.to_string(), json!(ms))).collect();
        Ok(json!({
            "from": self.from, "to": self.to, "window": self.window, "events": self.events, "sha256": hex(self.total),
            "sha256_no_farmer": hex(self.nf_total), "kinds": self.kinds, "windows": self.windows,
            "learned": self.learned.result(), "new_pools": self.new_pools.result(), "usage": self.usage.result(),
            "arrivals": self.arrivals.result(),
            // the history lines carry the leg's farmer flag (M3-10's: false here), so they are compared without it
            "history": self.history.result(), "history_no_farmer": self.history_nf.result(),
            // the farmer flags' fixes: M3-10 (null: not computed, never an empty digest)
            "flag_fixes": null,
            "clock": {"per_epoch": per_epoch, "h24": self.clock_h24},
            "lab_live_items": self.items,
        }))
    }
}

/// `lab-live legs --follow`: the follow source's events and side digests in paper.legs_dump's `--segments` form (spec
/// §4.1), refused when it reads no segment, another number than `expect_segments`, a segment in the hold-out or the clean
/// period, or no event.
pub fn run(a: &FollowArgs) -> Result<Value> {
    if a.window == 0 {
        bail!("--window must be above 0");
    }
    check_root(&a.root)?;
    let segs = segment_dirs(&a.root)?;
    for s in &segs {
        for (name, (lo, hi)) in [("the hold-out", HOLDOUT), ("the clean period (epochs 1055-1063)", CLEAN)] {
            if s.first < hi && s.last >= lo {
                bail!("segment {} ([{}, {}]) is in {name} [{lo}, {hi}): it stays locked", s.seq, s.first, s.last);
            }
        }
    }
    let snap = Snapshot::load_known(&a.snapshot, a.snapshot_before)?;
    let (n_tokens, n_classed) = (snap.tokens.len(), snap.pool_class.len());
    let mut src = Follow::open(&a.root, snap, a.h24)?;
    let mut d = Dump::new(a)?;
    src.for_each_item(&mut |keys, _, item| d.item(keys, item))?;
    let mut v = d.finish()?;
    // LiveFeed's per_epoch is the source's (the items carry each segment's last value): the two must agree
    let per_epoch: serde_json::Map<String, Value> = src.per_epoch.iter().map(|(e, ms)| (e.to_string(), json!(ms))).collect();
    if v["clock"]["per_epoch"] != Value::Object(per_epoch) {
        bail!("the epochs' ms per slot handed out differ from the source's own");
    }
    v["stats"] = src.stats.json();
    v["farm_seed"] = Value::Null; // PR 3
    v["snapshot_before"] = json!(a.snapshot_before);
    v["h24"] = json!(a.h24);
    v["lab_live"] = json!({
        "snapshot": {"tokens": n_tokens, "pools_classed": n_classed},
        "admission": src.counts.json(), "keys": src.adm.keys.len(), "peak_rss_bytes": crate::mem::peak_rss_bytes(),
        "segments_listed": segs.len(),
    });
    let read = src.stats.segments;
    if read == 0 {
        bail!("the follow source read no complete segment under {}/segments: refused", a.root.display());
    }
    if a.expect_segments.is_some_and(|n| n != read) {
        bail!("the follow source read {read} complete segments, not the {} expected: refused", a.expect_segments.unwrap_or_default());
    }
    if v["events"] == 0 {
        bail!("no event: an empty dump is no result: refused");
    }
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::idl::{Kind, Val};
    use crate::legs_read::tests::{export, pool_cols, ptrade, put, tmp, trade, BUY, SELL, TRADE};
    use lab_core::legs::BOOST_BUY_DISC;

    fn s(x: &str) -> Val {
        Val::Str(x.to_owned())
    }

    fn u(x: u64) -> Val {
        Val::U64(x)
    }

    /// A complete segment `seq` of [first, last] under root/segments, with `extra` in its `_segment.json`.
    fn seg(root: &Path, seq: u64, first: u64, last: u64, extra: Value) -> PathBuf {
        let dir = root.join("segments").join(format!("{seq:09}"));
        fs::create_dir_all(&dir).unwrap();
        let mut m = json!({"seq": seq, "first_slot": first, "last_slot": last, "complete": true});
        for (k, v) in extra.as_object().unwrap() {
            m[k] = v.clone();
        }
        fs::write(dir.join("_segment.json"), m.to_string()).unwrap();
        dir
    }

    fn blocks(dir: &Path, rows: &[(u64, Option<i64>, Option<bool>)]) {
        let r: Vec<Vec<Val>> = rows.iter().map(|&(sl, t, sk)| vec![u(sl), t.map_or(Val::Null, Val::I64), sk.map_or(Val::Null, Val::Bool)]).collect();
        put(dir, "blocks", 0, &[("slot", Kind::U64), ("block_time", Kind::I64), ("skipped", Kind::Bool)], &r);
    }

    fn create_pool(dir: &Path, part: usize, rows: &[(u64, &str, &str, &str)]) {
        let cols = [("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64), ("pool", Kind::Utf8),
                    ("base_mint", Kind::Utf8), ("quote_mint", Kind::Utf8), ("coin_creator", Kind::Utf8), ("is_mayhem_mode", Kind::Bool)];
        let r: Vec<Vec<Val>> = rows.iter().map(|&(sl, p, b, q)| vec![u(sl), u(0), u(0), u(3), s(p), s(b), s(q), s(&format!("CR{p}")), Val::Bool(false)]).collect();
        put(dir, "pump_amm/CreatePoolEvent", part, &cols, &r);
    }

    fn pool_creations(dir: &Path, rows: &[(u64, &str, &str, &str)]) {
        let cols = [("slot", Kind::U64), ("pool", Kind::Utf8), ("base_mint", Kind::Utf8), ("quote_mint", Kind::Utf8), ("coin_creator", Kind::Utf8),
                    ("is_mayhem_mode", Kind::Bool)];
        let r: Vec<Vec<Val>> = rows.iter().map(|&(sl, p, b, q)| vec![u(sl), s(p), s(b), s(q), s(&format!("CR{p}")), Val::Bool(false)]).collect();
        put(dir, "pool_creations", 0, &cols, &r);
    }

    fn withdraw(dir: &Path, slot: u64, pool: &str) {
        let names = ["slot", "tx_index", "outer_ix", "inner_ix", "pool", "user", "outer_program", "pool_base_token_reserves",
                     "pool_quote_token_reserves", "base_amount_out", "quote_amount_out", "lp_token_amount_in", "lp_mint_supply"];
        let cols: Vec<(&str, Kind)> = names.iter().map(|n| (*n, if matches!(*n, "pool" | "user" | "outer_program") { Kind::Utf8 } else { Kind::U64 })).collect();
        put(dir, "pump_amm/WithdrawEvent", 0, &cols, &[vec![u(slot), u(0), u(0), u(2), s(pool), s("L"), s(lab_core::legs::PUMP_AMM), u(990_000),
                                                             u(500_000), u(9_900), u(5_000), u(77), Val::Null]]);
    }

    /// The fixture (snapshot before 1000: M1, and P1, P3 and PX (another class) known):
    /// - seg 0 [1000, 1099]: M1's trade at 1010 (kept) and MX's at 1020 (an unknown mint); P1's buy at 1030 (kept), PN's
    ///   buy at 1040 and withdraw at 1045 (an unknown pool's trade and other row), PX's buy at 1046 (a known pool of
    ///   another class: dropped, not unknown) and PN's boost-crank inner buy at 1047 (not counted); its usage and arrivals;
    /// - seg 1 [1100, 1199]: creates PN (sol) and PO (other) at 1100 and MX at 1101, completes M1 at 1150 and migrates it to
    ///   PM; PN's buy at 1110 and MX's trade at 1120 kept, PO's buy at 1130 dropped (another class);
    /// - seg 2 [1200, 1299]: its complete backfill creates PB (pool_creations, 1180); PB's buy at 1210;
    /// - seg 3 [1300, 1399]: a running backfill creating PR (not read), so PR's buy at 1310 is unknown; M1 trades at 1115
    ///   (late: 1120 was released) and at 1120 (the last slot released: not late, handed out as a second item of 1120); PB's
    ///   buy at 1210 again (a duplicate of the queued one) and P1's buy at 1320.
    fn fixture(root: &Path) {
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        let usage = json!([{"minute": 1, "stream": "tx_watchlist", "bytes": 5, "note": "é"}]);
        let d0 = seg(root, 0, 1000, 1099, json!({"usage": usage, "written_unix": 77}));
        blocks(&d0, &[(1000, Some(1_000_000), Some(false)), (1050, Some(1_000_020), None), (1060, None, Some(true))]);
        put(&d0, "pump/TradeEvent", 0, &TRADE, &[trade(1010, 0, "M1", None), trade(1020, 0, "MX", None)]);
        put(&d0, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[ptrade(1030, "P1", "W", None, 100, 1_000, false), ptrade(1040, "PN", "B", None, 40, 400, false),
                                                             ptrade(1046, "PX", "B", None, 40, 400, false),
                                                             ptrade(1047, "PN", "K", Some(BOOST_BUY_DISC), 40, 400, false)]);
        withdraw(&d0, 1045, "PN");
        put(&d0, "arrivals", 0, &[("slot", Kind::U64), ("tx_index", Kind::U64), ("received_unix_ns", Kind::U64), ("created_unix_ns", Kind::U64)],
            &[vec![u(1010), u(0), u(7), u(3)], vec![u(1010), u(0), u(5), Val::Null], vec![u(1030), u(0), u(9), u(u64::MAX)]]);
        let d1 = seg(root, 1, 1100, 1199, json!({}));
        blocks(&d1, &[(1100, Some(1_000_040), Some(false))]);
        create_pool(&d1, 0, &[(1100, "PN", "AN", WSOL), (1100, "PO", "AO", "USDC")]);
        put(&d1, "pump/CreateEvent", 0, &[("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64), ("mint", Kind::Utf8),
                                          ("user", Kind::Utf8), ("is_mayhem_mode", Kind::Bool), ("quote_mint", Kind::Utf8)],
            &[vec![u(1101), u(0), u(0), u(1), s("MX"), s("CX"), Val::Null, Val::Null]]);
        put(&d1, "pump/CompleteEvent", 0, &[("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64), ("mint", Kind::Utf8)],
            &[vec![u(1150), u(0), u(0), u(1), s("M1")]]);
        put(&d1, "pump/CompletePumpAmmMigrationEvent", 0, &[("slot", Kind::U64), ("tx_index", Kind::U64), ("outer_ix", Kind::U64), ("inner_ix", Kind::U64),
                                                            ("mint", Kind::Utf8), ("pool", Kind::Utf8)],
            &[vec![u(1151), u(0), u(0), u(1), s("M1"), s("PM")]]);
        put(&d1, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[ptrade(1110, "PN", "B", None, 40, 400, false), ptrade(1130, "PO", "B", None, 40, 400, false)]);
        put(&d1, "pump/TradeEvent", 0, &TRADE, &[trade(1120, 0, "MX", None)]);
        let d2 = seg(root, 2, 1200, 1299, json!({"backfills": [{"pool": "PB", "status": "complete", "rows": 1}]}));
        pool_creations(&d2.join("backfill"), &[(1180, "PB", "AB", WSOL)]);
        put(&d2, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[ptrade(1210, "PB", "B", None, 40, 400, false)]);
        let d3 = seg(root, 3, 1300, 1399, json!({"backfills": [{"pool": "PR", "status": "running", "rows": 1}]}));
        pool_creations(&d3.join("backfill"), &[(1290, "PR", "AR", WSOL)]);
        put(&d3, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[ptrade(1310, "PR", "B", None, 40, 400, false), ptrade(1210, "PB", "B", None, 40, 400, false),
                                                             ptrade(1320, "P1", "W", None, 100, 1_000, false)]);
        put(&d3, "pump/TradeEvent", 0, &TRADE, &[trade(1115, 0, "M1", None), trade(1120, 5, "M1", None)]);
        // an incomplete segment: skipped
        let d4 = root.join("segments/000000004");
        fs::create_dir_all(&d4).unwrap();
        fs::write(d4.join("_segment.json"), json!({"seq": 4, "first_slot": 1400, "last_slot": 1499, "complete": false}).to_string()).unwrap();
    }

    fn open(root: &Path, h24: Option<u64>) -> Follow {
        Follow::open(root, Snapshot::load_known(&root.join("snap"), Some(1000)).unwrap(), h24).unwrap()
    }

    /// The items as short lines: `slot S key...`, `token MINT complete pool`, `pool P`, `learned LINE`, `segment SEQ`, ...
    fn trace(f: &mut Follow) -> Vec<String> {
        let mut out = Vec::new();
        f.for_each_item(&mut |keys, _, item| {
            out.push(match item {
                Item::Slot(sl) => format!("slot {} {}", sl.slot, sl.events.iter().map(|e| keys.name(e.key).unwrap()).collect::<Vec<_>>().join(",")),
                Item::Token(t) => format!("token {} {:?} {:?}", t.mint, t.complete_slot, t.pool),
                Item::Pool(p) => format!("pool {}", p.pool),
                Item::EpochMs { epoch, ms } => format!("epoch_ms {epoch} {ms}"),
                Item::Learned(l) => format!("learned {}", l.line()),
                Item::History { pool, legs } => format!("history {} {}", keys.name(pool).unwrap(), legs.iter().map(|l| l.slot().to_string()).collect::<Vec<_>>().join(",")),
                Item::Usage(u) => format!("usage {}", py_json(u)),
                Item::Segment { seq, h24 } => format!("segment {seq} {h24}"),
            });
            Ok(())
        }).unwrap();
        out
    }

    #[test]
    fn the_learns_come_before_the_slots_they_precede_and_the_last_slot_waits_for_the_next_segment() {
        // spec §2.2-§2.5: each segment's learns (own dirs, then a complete backfill's) before its events; the release lags
        // one slot, so seg 0's last slot (1030) goes out after seg 1's learns (the lookahead); late and duplicate rows dropped
        let root = tmp("follow-order");
        fixture(&root);
        let mut f = open(&root, Some(500));
        let t = trace(&mut f);
        let want = [
            "learned epoch\t0\t0\t1000\t[1000, 1000000]",
            "usage {\"bytes\": 5, \"minute\": 1, \"note\": \"\\u00e9\", \"seq\": 0, \"stream\": \"tx_watchlist\"}",
            "segment 0 500",
            "slot 1010 M1",
            "token MX None None",
            "token M1 Some(1150) None",
            "token M1 Some(1150) Some(\"PM\")",
            "pool PN",
            "learned token\tM1\t1\t1150\t[\"M1\", 900, \"C1\", false, \"So1\", 1150, null]",
            "learned token\tM1\t1\t1150\t[\"M1\", 900, \"C1\", false, \"So1\", 1150, \"PM\"]",
            "learned pool\tPN\t1\t1100\t[\"sol\", \"normal\", \"PN\", \"AN\", \"normal\", \"sol\", false, \"CRPN\", 1100]",
            "learned pool\tPO\t1\t1100\t[\"other\", \"normal\"]",
            "segment 1 500",
            "slot 1030 P1",
            "slot 1110 PN",
            "pool PB",
            "learned pool\tPB\t2\t1180\t[\"sol\", \"normal\", \"PB\", \"AB\", \"normal\", \"sol\", false, \"CRPB\", 1180]",
            "segment 2 500",
            "slot 1120 MX",
            "segment 3 500",
            "slot 1120 M1",
            "slot 1210 PB",
            "slot 1320 P1",
        ];
        assert_eq!(t, want);
        assert_eq!(f.stats, Stats { segments: 4, late_rows: 1, duplicate_rows: 1, unknown_pools: 2, unknown_pool_rows: 2, unknown_pool_other_rows: 1,
                                    unknown_mints: 1, unknown_mint_rows: 1, ..Stats::default() });
        // the tokens LiveFeed holds: M1 graduated into PM, MX created live
        let tok = |m: &str| f.tokens[&f.adm.keys.get(m).unwrap()].clone();
        assert_eq!((tok("M1").complete_slot, tok("M1").pool.as_deref()), (Some(1150), Some("PM")));
        assert_eq!((tok("MX").create_slot, tok("MX").creator.as_deref(), tok("MX").mayhem, tok("MX").quote_mint.as_deref()),
                   (Some(1101), Some("CX"), Some(false), Some(DEFAULT_KEY)));
        assert!(f.adm.known_pool("PO") && f.adm.known_pool("PB") && !f.adm.known_pool("PR"), "a running backfill is not read");
        assert!(f.for_each_item(&mut |_, _, _| Ok(())).is_err(), "a follow source hands out its items once");
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_unknown_sets_are_capped_and_a_key_past_the_cap_counts_as_new_again() {
        // spec §2.3 (v0.2): per query and key its rows; a key not held is new and joins while the set is below the cap
        let by = |keys: &[&str]| keys.iter().map(|k| (Some((*k).to_owned()), 2u64)).collect::<BTreeMap<_, _>>();
        let (mut seen, mut rows, mut keys) = (HashSet::new(), 0, 0);
        assert_eq!(count_unknown(&by(&["A", "B"]), &mut seen, 1, &mut rows, &mut keys), 2);
        assert_eq!((rows, keys, seen.len()), (4, 2, 1));
        assert_eq!(count_unknown(&by(&["A", "B"]), &mut seen, 1, &mut rows, &mut keys), 1); // B is not held: new again
        assert_eq!((rows, keys), (8, 3));
        let mut none = BTreeMap::new();
        none.insert(None, 3);
        assert_eq!(count_unknown(&none, &mut HashSet::new(), UNKNOWN_CAP, &mut rows, &mut keys), 1); // a NULL key counts too
        assert_eq!((rows, keys), (11, 4));
        // the source's own cap: with 1, the fixture's second unknown pool (PR) is new, and PN's other row in the same
        // segment as its trade is not new
        let root = tmp("follow-cap");
        fixture(&root);
        let mut f = open(&root, Some(500));
        f.cap = 1;
        trace(&mut f);
        assert_eq!((f.stats.unknown_pools, f.unknown_pools.len()), (2, 1));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_clock_takes_pythons_float_steps_and_h24_its_window() {
        // spec §2.7: an epoch's ms per slot once its span covers 1,000 slots, (t1 - t0) * 1000.0 / (s1 - s0) as Python
        // computes it (418.9627228525122 there), h24 from the window's first and last sample, half to even
        let root = tmp("follow-clock");
        fixture(&root);
        let mut f = open(&root, None);
        assert_eq!(f.h24(), 216_000); // no sample: a day at 400 ms
        let mut out = SegOut::default();
        f.clock_block(864_000, 1_000_000, 7, &mut out);
        f.clock_block(864_999, 1_000_400, 7, &mut out);
        assert!(f.per_epoch.is_empty(), "999 slots are no span yet");
        f.clock_block(865_234, 1_000_517, 7, &mut out);
        assert_eq!(f.per_epoch[&2].to_bits(), 418.962_722_852_512_2_f64.to_bits());
        assert_eq!(out.epochs.len(), 1);
        assert_eq!(f.h24(), 206_224); // round(86400000 / 418.9627228525122)
        assert_eq!(out.learned.iter().map(Learned::line).collect::<Vec<_>>(), ["epoch\t2\t7\t864000\t[864000, 1000000]"]);
        // a block of an earlier slot read later moves the span's end back (below 1,000 slots now): the last ms stays
        f.clock_block(864_100, 1_000_050, 8, &mut out);
        assert_eq!(f.per_epoch[&2].to_bits(), 418.962_722_852_512_2_f64.to_bits());
        let fixed = open(&root, Some(864));
        assert_eq!(fixed.h24(), 864);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn json_and_floats_are_written_as_python_writes_them() {
        let v: Value = serde_json::from_str(r#"{"note": "é😀\u0001\u007f\"\\", "b": 1, "a": [1.5, null, true]}"#).unwrap();
        assert_eq!(py_json(&v), r#"{"a": [1.5, null, true], "b": 1, "note": "\u00e9\ud83d\ude00\u0001\u007f\"\\"}"#);
        for (x, want) in [(1e16, "1e+16"), (1e-5, "1e-05"), (0.0001, "0.0001"), (123_456_789_012_345_678.0, "1.2345678901234568e+17"),
                          (1.5e-7, "1.5e-07"), (-2.5, "-2.5"), (400.0, "400.0"), (1e15, "1000000000000000.0"),
                          (0.1 + 0.2, "0.30000000000000004"), (418.962_722_852_512_2, "418.9627228525122")] {
            assert_eq!(py_float(x), want, "{x}");
        }
    }

    #[test]
    fn the_dump_writes_legs_dumps_summary_with_its_drain_points() {
        // spec §4.1: the events, kinds and windows (with and without the farmer flag), the side digests drained at each
        // slot end (learned and new pools sorted per drain), the clock, the stats; refused without segments or events
        let root = tmp("follow-dump");
        fixture(&root);
        let args = |expect: Option<u64>| FollowArgs {
            root: root.clone(), snapshot: root.join("snap"), snapshot_before: Some(1000), h24: Some(500), from: 1000, to: None, window: 100,
            summary: root.join("summary.json"), out: Some(root.join("lines.txt")), out_from: 0, expect_segments: expect,
        };
        let v = run(&args(Some(4))).unwrap();
        assert_eq!(v["events"], 7);
        assert_eq!(v["kinds"], json!({"curve": 3, "pool/buy": 4}));
        let w: Vec<(i64, i64, u64)> = v["windows"].as_array().unwrap().iter().map(|w| (w[0].as_i64().unwrap(), w[1].as_i64().unwrap(), w[2].as_u64().unwrap())).collect();
        assert_eq!(w, [(1000, 1100, 2), (1100, 1200, 3), (1200, 1300, 1), (1300, 1400, 1)]);
        let text = fs::read_to_string(root.join("lines.txt")).unwrap();
        let sha = |t: &str| hex(Sha256::new_with_prefix(t.as_bytes()));
        assert_eq!(v["sha256"], json!(sha(&text)));
        let nf: String = text.lines().map(|l| if l.starts_with("pool\t") { format!("{}\t-\n", &l[..l.rfind('\t').unwrap()]) } else { format!("{l}\n") }).collect();
        assert_eq!(v["sha256_no_farmer"], json!(sha(&nf)));
        // the drains: at the end of 1010 (when 1030 arrives) seg 0's epoch and seg 1's learns, sorted; at the end of
        // 1110 seg 2's PB; new pools PN, then PB
        let mut first = ["epoch\t0\t0\t1000\t[1000, 1000000]".to_owned(),
                             "token\tM1\t1\t1150\t[\"M1\", 900, \"C1\", false, \"So1\", 1150, null]".to_owned(),
                             "token\tM1\t1\t1150\t[\"M1\", 900, \"C1\", false, \"So1\", 1150, \"PM\"]".to_owned(),
                             "pool\tPN\t1\t1100\t[\"sol\", \"normal\", \"PN\", \"AN\", \"normal\", \"sol\", false, \"CRPN\", 1100]".to_owned(),
                             "pool\tPO\t1\t1100\t[\"other\", \"normal\"]".to_owned()];
        first.sort();
        let learned: String = first.iter().chain(["pool\tPB\t2\t1180\t[\"sol\", \"normal\", \"PB\", \"AB\", \"normal\", \"sol\", false, \"CRPB\", 1180]".to_owned()].iter())
            .map(|l| format!("{l}\n")).collect();
        assert_eq!(v["learned"], json!({"count": 6, "sha256": sha(&learned)}));
        assert_eq!(v["new_pools"], json!({"count": 2, "sha256": sha("PN\nPB\n")}));
        assert_eq!(v["usage"]["count"], 1);
        // arrivals: M1's trade at 1010 the earliest of its two rows, P1's buy at 1030 with created beyond BIGINT (NULL),
        // the others none; every event has the segment's written_unix (77 in seg 0, none after)
        let arrivals = "1010\t0\t0\t1\t5\t3\t77\n1030\t0\t0\t2\t9\tnull\t77\n1110\t0\t0\t2\tnull\tnull\tnull\n1120\t0\t0\t1\tnull\tnull\tnull\n\
                        1120\t5\t0\t1\tnull\tnull\tnull\n1210\t0\t0\t2\tnull\tnull\tnull\n1320\t0\t0\t2\tnull\tnull\tnull\n";
        assert_eq!(v["arrivals"], json!({"count": 7, "sha256": sha(arrivals)}));
        // the first drain is at the end of 1010, when 1030 arrives: seg 1 has been read by then (as in legs_dump)
        assert_eq!(v["clock"], json!({"per_epoch": {}, "h24": [[1, 500]]}));
        assert_eq!((&v["history"]["count"], &v["flag_fixes"], &v["farm_seed"]), (&json!(0), &Value::Null, &Value::Null));
        assert_eq!(v["stats"]["unknown_mint_rows"], 1);
        let err = |a: FollowArgs| format!("{:#}", run(&a).unwrap_err());
        assert!(err(args(Some(5))).contains("not the 5 expected"));
        assert!(err(FollowArgs { root: root.join("segments"), ..args(None) }).contains("holds the segments themselves"));
        assert!(err(FollowArgs { root: root.join("snap"), ..args(None) }).contains("no segments/"));
        fs::remove_dir_all(&root).unwrap();
    }

    /// P1's buy at `slot` (transaction `tx`), its fees 20/5 and virtual quote 7 unless given.
    fn buy(slot: u64, tx: u64, fees_virt: Option<(u64, &str)>) -> Vec<Val> {
        let mut r = ptrade(slot, "P1", "W", None, 100, 1_000, false);
        r[1] = u(tx);
        if let Some((lp, virt)) = fees_virt {
            (r[10], r[14]) = (u(lp), s(virt));
        }
        r
    }

    fn sell(slot: u64) -> Vec<Val> {
        ptrade(slot, "P1", "W", None, 100, 1_000, true)
    }

    /// Backfills and a restart (spec §2.4, §2.6; snapshot before 1000: P1 known):
    /// - seg 0 [2000, 2099]: P1 buys at 2010 and 2050;
    /// - seg 1 [2100, 2199], a complete backfill of P1: before the segment 2005 (history), 2010 (the recorded buy again:
    ///   a backfill duplicate) and 2060 tx 3 (history); inside it 2120 (history, fees 30 and virtual quote 9, between the
    ///   recorded sell at 2110 and withdraw at 2130) and 2150 (at a recorded buy's position: left out); after it 2250
    ///   (pending). Recorded: the sell at 2110, the withdraw at 2130, the buy at 2150;
    /// - seg 2 [2200, 2299], a restart below its last segment: the sell at 2110 again (seen recorded: a duplicate) and the
    ///   buy at 2060 tx 3 (seen as history: an event, then late), and a buy at 2210; the pending 2250 is inside it;
    /// - seg 3 [2300, 2399]: a buy at 2310.
    fn backfill_fixture(root: &Path) {
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        let d0 = seg(root, 0, 2000, 2099, json!({}));
        put(&d0, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[buy(2010, 0, None), buy(2050, 0, None)]);
        let d1 = seg(root, 1, 2100, 2199, json!({"backfills": [{"pool": "P1", "status": "complete", "rows": 6}]}));
        put(&d1.join("backfill"), "pump_amm/BuyEvent", 0, &pool_cols(&BUY),
            &[buy(2250, 1, None), buy(2120, 1, Some((30, "9"))), buy(2005, 1, None), buy(2010, 0, None), buy(2060, 3, None), buy(2150, 0, None)]);
        put(&d1, "pump_amm/SellEvent", 0, &pool_cols(&SELL), &[sell(2110)]);
        withdraw(&d1, 2130, "P1");
        put(&d1, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[buy(2150, 0, None)]);
        let d2 = seg(root, 2, 2200, 2299, json!({}));
        put(&d2, "pump_amm/SellEvent", 0, &pool_cols(&SELL), &[sell(2110)]);
        put(&d2, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[buy(2060, 3, None), buy(2210, 0, None)]);
        let d3 = seg(root, 3, 2300, 2399, json!({}));
        put(&d3, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[buy(2310, 0, None)]);
    }

    #[test]
    fn backfills_are_history_before_inside_and_after_and_a_restart_is_deduplicated() {
        let root = tmp("follow-backfill");
        backfill_fixture(&root);
        let mut f = open(&root, Some(500));
        let t: Vec<String> = trace(&mut f).into_iter().filter(|l| l.starts_with("history") || l.starts_with("slot")).collect();
        assert_eq!(t, ["slot 2010 P1", "history P1 2005", "history P1 2060", "history P1 2120", "slot 2050 P1", "slot 2110 P1", "slot 2130 P1",
                       "history P1 2250", "slot 2150 P1", "slot 2210 P1", "slot 2310 P1"]);
        assert_eq!((f.stats.backfill_rows, f.stats.backfill_duplicates, f.stats.duplicate_rows, f.stats.late_rows), (4, 1, 1, 1));
        // P1's farm keys: the backfill's positions and its recorded rows since its first backfill (seg 1 on)
        let keys: Vec<(u64, u32)> = f.farm_keys[&f.adm.keys.get("P1").unwrap()].iter().map(|o| (o.slot, o.tx)).collect();
        assert_eq!(keys, [(2005, 1), (2060, 3), (2110, 0), (2120, 1), (2130, 0), (2150, 0), (2210, 0), (2250, 1), (2310, 0)]);
        assert!(f.backfill_pending.is_empty());
        // the withdraw at 2130 takes the pool context of the history buy at 2120 (fees 30/5, virtual quote 9), not the
        // recorded sell's at 2110 (20/5, 7)
        let a = FollowArgs { root: root.clone(), snapshot: root.join("snap"), snapshot_before: Some(1000), h24: Some(500), from: 2000, to: None,
                             window: 1000, summary: root.join("s.json"), out: Some(root.join("lines.txt")), out_from: 0, expect_segments: Some(4) };
        let v = run(&a).unwrap();
        let text = fs::read_to_string(root.join("lines.txt")).unwrap();
        let w: Vec<&str> = text.lines().find(|l| l.split('\t').nth(8) == Some("withdraw")).unwrap().split('\t').collect();
        assert_eq!(&w[12..19], ["990000", "500009", "30", "5", "0", "0", "9"]);
        assert_eq!((&v["events"], &v["history"]["count"], &v["history_no_farmer"]["count"]), (&json!(7), &json!(4), &json!(4)));
        assert_ne!(v["history"]["sha256"], v["history_no_farmer"]["sha256"]);
        assert_eq!((&v["stats"]["backfill_rows"], &v["stats"]["backfill_duplicates"]), (&json!(4), &json!(1)));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_seen_keys_leave_in_their_order_once_the_oldest_is_three_thousand_slots_behind() {
        // live.py `_seen`: remembered while within SEEN_KEEP_SLOTS of the highest slot seen, forgotten in the order they
        // came (a key behind a newer one waits), never remembered when already too old
        let root = tmp("follow-seen");
        fixture(&root);
        let mut f = open(&root, None);
        let k = |slot: u64| (1u8, 0u32, Order { slot, tx: 0, outer: 0, inner: 0 });
        assert_eq!(f.seen(k(10_000), 10_000, Src::Rec), None);
        assert_eq!(f.seen(k(10_000), 10_000, Src::Hist), Some(Src::Rec));
        assert_eq!(f.seen(k(9_000), 9_000, Src::Hist), None);
        assert_eq!(f.seen(k(6_999), 6_999, Src::Rec), None); // 3,001 behind: not remembered
        assert_eq!(f.seen(k(6_999), 6_999, Src::Rec), None);
        assert_eq!(f.seen(k(12_500), 12_500, Src::Rec), None); // 10,000 stays (within 3,000), 9,000 too
        assert_eq!(f.seen(k(9_000), 9_000, Src::Rec), Some(Src::Hist));
        assert_eq!(f.seen(k(13_001), 13_001, Src::Rec), None); // 10,000 and 9,000 are more than 3,000 behind: they leave
        assert_eq!(f.seen_keys.len(), 2);
        // a key behind a newer one waits: 10,100 came after 12,500, so it stays while 12,500 is within the window
        assert_eq!(f.seen(k(10_100), 10_100, Src::Hist), None);
        assert_eq!(f.seen(k(13_200), 13_200, Src::Rec), None);
        assert_eq!(f.seen(k(10_100), 10_100, Src::Rec), Some(Src::Hist));
        assert_eq!((f.seen(k(10_000), 10_000, Src::Rec), f.seen_keys.len()), (None, 4)); // too old to be remembered again
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_prune_drops_old_tokens_and_farm_keys_at_the_hundredth_segment_with_events() {
        // live.py `_prune` after every 100th segment that has events, at its last event's slot with its h24 (10 here):
        // M1 (created at 900, never graduated) is dropped, so its later trade is an unknown mint; P1's farm key at 2995
        // (a backfill before seg 0) is before 3990 - 10, so P1 is no backfilled pool any more. The event is P3's (P1's own
        // recorded trade would join its farm keys). A 100th segment without events prunes nothing.
        for events in [true, false] {
            let root = tmp(&format!("follow-prune-{events}"));
            fs::create_dir_all(root.join("snap")).unwrap();
            export(&root.join("snap"));
            for k in 0..101u64 {
                let lo = 3000 + 10 * k;
                let meta = if k == 0 { json!({"backfills": [{"status": "failed", "rows": 1}]}) } else { json!({}) };
                let d = seg(&root, k, lo, lo + 9, meta);
                if k == 0 {
                    put(&d.join("backfill"), "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[buy(2995, 0, None)]);
                }
                if k == 99 && events {
                    put(&d, "pump_amm/BuyEvent", 0, &pool_cols(&BUY), &[ptrade(3990, "P3", "W", None, 100, 1_000, false)]);
                }
                if k == 100 {
                    put(&d, "pump/TradeEvent", 0, &TRADE, &[trade(4005, 0, "M1", None)]);
                }
            }
            let mut f = open(&root, Some(10));
            trace(&mut f);
            let p1 = f.adm.keys.get("P1").unwrap();
            if events {
                assert_eq!((f.stats.tokens_dropped, f.stats.unknown_mint_rows, f.farm_keys.contains_key(&p1)), (1, 1, false));
                assert!(!f.adm.known_mint("M1") && !f.tokens.contains_key(&f.adm.keys.get("M1").unwrap()));
            } else {
                assert_eq!((f.stats.tokens_dropped, f.stats.unknown_mint_rows, f.farm_keys.contains_key(&p1)), (0, 0, true));
            }
            fs::remove_dir_all(&root).unwrap();
        }
    }

    #[test]
    fn a_segment_in_the_hold_out_or_the_clean_period_is_refused() {
        let root = tmp("follow-locked");
        fs::create_dir_all(root.join("snap")).unwrap();
        export(&root.join("snap"));
        for (lo, why) in [(HOLDOUT.0 + 5, "hold-out"), (CLEAN.1 - 1, "clean period")] {
            let _ = fs::remove_dir_all(root.join("segments"));
            seg(&root, 0, lo, lo + 10, json!({}));
            let a = FollowArgs { root: root.clone(), snapshot: root.join("snap"), snapshot_before: None, h24: None, from: lo, to: None, window: 100,
                                 summary: root.join("s.json"), out: None, out_from: 0, expect_segments: None };
            assert!(format!("{:#}", run(&a).unwrap_err()).contains(why), "{why}");
        }
        fs::remove_dir_all(&root).unwrap();
    }
}
