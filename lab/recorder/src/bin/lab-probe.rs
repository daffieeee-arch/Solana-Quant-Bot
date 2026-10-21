//! lab-probe: a short measurement beside the recorder. It counts messages, bytes and receive times, and nothing
//! else: no segments, no decoding, no budget.json (the recorder's meter state is never read or written).
//!
//!   lab-probe --slots --out FILE --minutes N --max-bytes B [--dry-minutes M] [--wire-ratio R] [--endpoint URL]
//!   lab-probe --tx-program name=<program id> [--tx-program …] --out FILE --minutes N --max-bytes B
//!             [--commitment processed|confirmed | --deshred] [--signatures-out FILE.csv] [...]
//!   lab-probe … --print-request        # the subscription it would send, without connecting
//!
//! Mode A (`--slots`, MR-AG1): slot updates only, every status (`filter_by_commitment=false`) with the inter-slot
//! ones (`interslot_updates=true`). Per slot the local receive time (unix ns) of the first update of each status;
//! the summary has the ms per slot, PROCESSED→CONFIRMED and CONFIRMED→FINALIZED (median, p90), the DEAD slots and
//! the slot numbers that never came (gaps).
//!
//! Mode B (`--tx-program`, the venue bytes census): one named transaction filter per program
//! (`account_include=[id]`, `vote=false`, `failed` unset, commitment confirmed as in the recorder). Every update
//! counts once in the total; a transaction that k of our filters matched (its `filters` field) counts 1/k to each,
//! so the filters add up to the total (pings and other updates go to `other`). `--commitment processed` asks the
//! same filters at processed commitment (the default stays confirmed). `--signatures-out FILE` streams one CSV row
//! per transaction update: `signature,slot,bank_id,filters,received_unix_ns` (filters |-joined, sorted; bank_id as the
//! update gives it, 0 when absent). The file is created new (never overwritten), flushed every minute and at the end,
//! and its own size counts against `--max-bytes` too. Two probes side by side, one processed and one confirmed, give
//! the per-transaction lead of a processed stream (`lab/checks/probe_lead.py`). `--deshred` puts the same filters
//! (account_include=[id], vote=false) on the SubscribeDeshred stream instead: transactions rebuilt from shreds before
//! any execution, matched on static and lookup-table keys, so it also carries transactions that later fail or never
//! land. It has no commitment (refused with it); its signature rows leave bank_id empty; bytes, caps, exit codes and
//! guards are the same.
//!
//! Bytes are counted as the recorder's meter counts them (`wire.rs`: `encoded_len` × `--wire-ratio`, rounded up);
//! `--max-bytes` is in those metered bytes, the unit of the recorder's daily budget. The endpoint comes from
//! `--endpoint` or LAB_TRITON_GRPC_ENDPOINT, the token only from LAB_TRITON_X_TOKEN (recorder.env). The token is never
//! printed, logged or written to the output; the endpoint (URL, host and host:port) is replaced by `<endpoint>` in
//! every error the probe logs or writes. `--out` is refused when it is named budget.json or its directory holds one
//! (a recorder root): the atomic write would replace the recorder's meter state.
//!
//! The output JSON is written atomically at the start, every minute and at the end, so a kill keeps the data up to
//! the last minute. Exit codes: 0 the run reached --minutes; 76 the metered bytes reached --max-bytes (hard stop);
//! 77 after --dry-minutes the projected bytes of the full run were above --max-bytes; 78 the run ended early (the
//! stream broke or went quiet, a signal). The recorder's own codes (74, 75) are not used.

#[path = "../wire.rs"]
mod wire;

use anyhow::{bail, Context, Result};
use futures::StreamExt;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use yellowstone_grpc_client::{ClientTlsConfig, GeyserGrpcClient, SubscribeDeshredRequestSink, SubscribeRequestSink};
use yellowstone_grpc_proto::prelude::{
    subscribe_update::UpdateOneof, subscribe_update_deshred::UpdateOneof as DeshredOneof, CommitmentLevel,
    SubscribeDeshredRequest, SubscribeRequestFilterDeshredTransactions, SubscribeUpdateDeshred, SubscribeUpdateSlot,
    SubscribeRequest, SubscribeRequestFilterSlots,
    SubscribeRequestFilterTransactions, SubscribeUpdate,
};
use yellowstone_grpc_proto::prost::Message as _;
use yellowstone_grpc_proto::tonic::codec::CompressionEncoding;

pub const EXIT_MAX_BYTES: i32 = 76;
pub const EXIT_PROJECTED: i32 = 77;
pub const EXIT_INCOMPLETE: i32 = 78;
/// Without any update for this long the run ends (78). Longer than the recorder's 30 s: a census of a quiet
/// program has only the server's pings in between.
const WATCHDOG_SECS: u64 = 60;
const NS_PER_SEC: u64 = 1_000_000_000;
/// SlotStatus by its proto number (geyser.proto: PROCESSED 0 … DEAD 6), in that order.
const STATUS: [&str; 7] = ["processed", "confirmed", "finalized", "first_shred_received", "completed", "created_bank", "dead"];
const PROCESSED: usize = 0;
const CONFIRMED: usize = 1;
const FINALIZED: usize = 2;
const FIRST_SHRED: usize = 3;
const DEAD: usize = 6;
/// The order of a slot's statuses in the output rows.
const LIFECYCLE: [usize; 7] = [FIRST_SHRED, 5, 4, PROCESSED, CONFIRMED, FINALIZED, DEAD];

#[derive(Debug, Clone, PartialEq)]
enum Mode {
    Slots,
    /// (filter name, program id), in the order given.
    Tx(Vec<(String, String)>),
}

#[derive(Debug)]
struct Args {
    mode: Mode,
    out: PathBuf,
    minutes: u64,
    max_bytes: u64,
    dry_minutes: Option<u64>,
    wire_ratio: f64,
    endpoint: Option<String>,
    print_request: bool,
    /// Mode B only (mode A always asks processed with every status).
    commitment: CommitmentLevel,
    signatures_out: Option<PathBuf>,
    /// Mode B on the SubscribeDeshred stream (transactions from shreds, before execution) instead of Subscribe.
    deshred: bool,
}

fn parse_args(args: impl IntoIterator<Item = String>, endpoint: Option<String>) -> Result<Args> {
    let mut it = args.into_iter();
    let (mut slots, mut programs) = (false, Vec::<(String, String)>::new());
    let (mut out, mut minutes, mut max_bytes, mut dry_minutes) = (None, None, None, None);
    let mut a = Args { mode: Mode::Slots, out: PathBuf::new(), minutes: 0, max_bytes: 0, dry_minutes: None, wire_ratio: 1.0, endpoint, print_request: false,
                      commitment: CommitmentLevel::Confirmed, signatures_out: None, deshred: false };
    let mut commitment = None;
    while let Some(flag) = it.next() {
        let mut val = || it.next().with_context(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--slots" => slots = true,
            "--tx-program" => programs.push(parse_program(&val()?)?),
            "--out" => out = Some(PathBuf::from(val()?)),
            "--minutes" => minutes = Some(val()?.parse::<u64>().context("--minutes takes a whole number")?),
            "--max-bytes" => max_bytes = Some(val()?.parse::<u64>().context("--max-bytes takes a whole number")?),
            "--dry-minutes" => dry_minutes = Some(val()?.parse::<u64>().context("--dry-minutes takes a whole number")?),
            "--wire-ratio" => a.wire_ratio = val()?.parse().context("--wire-ratio takes a number")?,
            "--endpoint" => a.endpoint = Some(val()?),
            "--print-request" => a.print_request = true,
            "--commitment" => commitment = Some(val()?),
            "--deshred" => a.deshred = true,
            "--signatures-out" => a.signatures_out = Some(PathBuf::from(val()?)),
            other => bail!("unknown argument {other}"),
        }
    }
    a.mode = match (slots, programs.is_empty()) {
        (true, true) => Mode::Slots,
        (false, false) => Mode::Tx(programs),
        (true, false) => bail!("pass --slots or --tx-program, not both"),
        (false, true) => bail!("pass --slots or at least one --tx-program name=<program id>"),
    };
    if let Mode::Tx(p) = &a.mode {
        let names: BTreeSet<_> = p.iter().map(|(n, _)| n).collect();
        let ids: BTreeSet<_> = p.iter().map(|(_, id)| id).collect();
        if names.len() != p.len() || ids.len() != p.len() {
            bail!("each --tx-program needs its own name and its own program id");
        }
    }
    if a.mode == Mode::Slots && (commitment.is_some() || a.signatures_out.is_some() || a.deshred) {
        bail!("--commitment, --deshred and --signatures-out are for --tx-program (--slots always asks every status at processed)");
    }
    if a.deshred && commitment.is_some() {
        bail!("--deshred has no commitment: its transactions come from shreds, before execution");
    }
    a.commitment = match commitment.as_deref() {
        None | Some("confirmed") => CommitmentLevel::Confirmed,
        Some("processed") => CommitmentLevel::Processed,
        Some(other) => bail!("--commitment {other:?}: processed or confirmed"),
    };
    if !(a.wire_ratio.is_finite() && a.wire_ratio > 0.0) {
        bail!("--wire-ratio must be positive");
    }
    if a.print_request {
        return Ok(a);
    }
    a.out = out.context("pass --out FILE")?;
    refuse_budget("--out", &a.out)?;
    if let Some(sig) = &a.signatures_out {
        refuse_budget("--signatures-out", sig)?;
        if sig == &a.out {
            bail!("--signatures-out must be another file than --out");
        }
    }
    a.minutes = minutes.filter(|m| *m > 0).context("pass --minutes N (N > 0)")?;
    a.max_bytes = max_bytes.filter(|b| *b > 0).context("pass --max-bytes B (B > 0): the hard stop")?;
    if let Some(m) = dry_minutes {
        if m == 0 || m >= a.minutes {
            bail!("--dry-minutes must be at least 1 and below --minutes");
        }
        a.dry_minutes = Some(m);
    }
    Ok(a)
}

/// The recorder's meter state is never the output, nor beside it: an --out named budget.json, or in a directory
/// that holds one (a recorder root), is refused (LAB review on #501).
fn refuse_budget(flag: &str, out: &Path) -> Result<()> {
    if out.file_name().is_some_and(|n| n.eq_ignore_ascii_case("budget.json")) {
        bail!("{flag} {}: budget.json is the recorder's meter state; write the probe's output elsewhere", out.display());
    }
    let dir = out.parent().filter(|d| !d.as_os_str().is_empty()).unwrap_or(Path::new("."));
    if dir.join("budget.json").exists() {
        bail!("{flag} {}: its directory holds a budget.json (a recorder root); write the probe's output elsewhere", out.display());
    }
    Ok(())
}

/// `text` with the endpoint (the URL as given, its host:port and its host) replaced by `<endpoint>`.
fn redact(text: &str, endpoint: &str) -> String {
    let authority = endpoint.split_once("://").map_or(endpoint, |(_, rest)| rest).split(['/', '?']).next().unwrap_or("");
    let authority = authority.rsplit_once('@').map_or(authority, |(_, host)| host);
    let host = authority.rsplit_once(':').map_or(authority, |(host, _)| host);
    let mut out = text.to_owned();
    for s in [endpoint.trim_end_matches('/'), authority, host] {
        if !s.is_empty() {
            out = out.replace(s, "<endpoint>");
        }
    }
    out
}

/// `name=<program id>`: a short name ([A-Za-z0-9_]) and a base58 address of 32 bytes.
fn parse_program(s: &str) -> Result<(String, String)> {
    let (name, id) = s.split_once('=').context("--tx-program takes name=<program id>")?;
    if name.is_empty() || name.len() > 64 || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
        bail!("--tx-program: the name {name:?} must be 1-64 characters of A-Z, a-z, 0-9 or _");
    }
    if bs58::decode(id).into_vec().map_or(true, |k| k.len() != 32) {
        bail!("--tx-program {name}: {id:?} is not a base58 address of 32 bytes");
    }
    Ok((name.to_owned(), id.to_owned()))
}

/// The one subscription of a run (`commitment` for mode B).
fn request(mode: &Mode, commitment: CommitmentLevel) -> SubscribeRequest {
    match mode {
        Mode::Slots => SubscribeRequest {
            slots: [("slots".to_owned(), SubscribeRequestFilterSlots { filter_by_commitment: Some(false), interslot_updates: Some(true) })].into(),
            commitment: Some(CommitmentLevel::Processed as i32),
            ..Default::default()
        },
        Mode::Tx(programs) => SubscribeRequest {
            transactions: programs
                .iter()
                .map(|(name, id)| {
                    let f = SubscribeRequestFilterTransactions { vote: Some(false), failed: None, account_include: vec![id.clone()], ..Default::default() };
                    (name.clone(), f)
                })
                .collect(),
            commitment: Some(commitment as i32),
            ..Default::default()
        },
    }
}

/// `--deshred`: the same per-program filters on the SubscribeDeshred stream (account_include=[id], vote=false; no
/// failed flag and no commitment there: nothing has executed yet). Static and lookup-table keys both match.
fn deshred_request(mode: &Mode) -> SubscribeDeshredRequest {
    let Mode::Tx(programs) = mode else { return SubscribeDeshredRequest::default() };
    SubscribeDeshredRequest {
        deshred_transactions: programs
            .iter()
            .map(|(name, id)| {
                let f = SubscribeRequestFilterDeshredTransactions { vote: Some(false), account_include: vec![id.clone()], ..Default::default() };
                (name.clone(), f)
            })
            .collect(),
        ..Default::default()
    }
}

fn deshred_summary(req: &SubscribeDeshredRequest) -> Value {
    json!({
        "stream": "deshred",
        "deshred_transactions": req.deshred_transactions.iter().map(|(k, v)| (k.clone(), json!({"account_include": v.account_include,
                     "account_exclude": v.account_exclude, "account_required": v.account_required, "vote": v.vote,
                     "include_update_parent": v.include_update_parent}))).collect::<BTreeMap<_, _>>(),
        "slots": req.slots.len(),
    })
}

/// `--signatures-out`: one CSV row per transaction update, streamed (created new, never overwritten).
struct Signatures {
    file: std::io::BufWriter<std::fs::File>,
    rows: u64,
    bytes: u64,
}

const SIGNATURES_HEADER: &str = "signature,slot,bank_id,filters,received_unix_ns\n";

impl Signatures {
    fn create(path: &Path) -> Result<Self> {
        let f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .with_context(|| format!("--signatures-out {}: cannot create it new (it exists or the directory is missing)", path.display()))?;
        let mut s = Signatures { file: std::io::BufWriter::new(f), rows: 0, bytes: 0 };
        s.put(SIGNATURES_HEADER)?;
        Ok(s)
    }

    fn put(&mut self, line: &str) -> Result<()> {
        use std::io::Write as _;
        self.file.write_all(line.as_bytes())?;
        self.bytes += line.len() as u64;
        Ok(())
    }

    /// The update's row when it is a transaction; the file's size so far.
    fn add(&mut self, u: &SubscribeUpdate, now_ns: u64) -> Result<u64> {
        self.add_row(signature_row(u, now_ns))
    }

    fn add_deshred(&mut self, u: &SubscribeUpdateDeshred, now_ns: u64) -> Result<u64> {
        self.add_row(deshred_signature_row(u, now_ns))
    }

    fn add_row(&mut self, line: Option<String>) -> Result<u64> {
        if let Some(line) = line {
            self.put(&line)?;
            self.rows += 1;
        }
        Ok(self.bytes)
    }

    fn flush(&mut self, sync: bool) -> Result<()> {
        use std::io::Write as _;
        self.file.flush()?;
        if sync {
            self.file.get_ref().sync_all()?;
        }
        Ok(())
    }
}

/// `signature,slot,bank_id,filters,received_unix_ns` of a transaction update (None for any other update). The
/// names are [A-Za-z0-9_] and the signature base58, so no field needs quoting.
fn signature_row(u: &SubscribeUpdate, now_ns: u64) -> Option<String> {
    let Some(UpdateOneof::Transaction(t)) = &u.update_oneof else { return None };
    let sig = t.transaction.as_ref().map(|i| bs58::encode(&i.signature).into_string()).unwrap_or_default();
    let mut filters: Vec<&str> = u.filters.iter().map(String::as_str).collect();
    filters.sort_unstable();
    filters.dedup();
    Some(format!("{sig},{},{},{},{now_ns}\n", t.slot, t.bank_id, filters.join("|")))
}

/// The same row for a deshred transaction; bank_id is empty (a deshred transaction belongs to no bank yet).
fn deshred_signature_row(u: &SubscribeUpdateDeshred, now_ns: u64) -> Option<String> {
    let Some(DeshredOneof::DeshredTransaction(t)) = &u.update_oneof else { return None };
    let sig = t.transaction.as_ref().map(|i| bs58::encode(&i.signature).into_string()).unwrap_or_default();
    let mut filters: Vec<&str> = u.filters.iter().map(String::as_str).collect();
    filters.sort_unstable();
    filters.dedup();
    Some(format!("{sig},{},,{},{now_ns}\n", t.slot, filters.join("|")))
}

/// What `--print-request` shows: the request's filters, no endpoint and no token.
fn request_summary(req: &SubscribeRequest) -> Value {
    json!({
        "commitment": req.commitment.and_then(|c| CommitmentLevel::try_from(c).ok()).map(|c| c.as_str_name()),
        "slots": req.slots.iter().map(|(k, v)| (k.clone(), json!({"filter_by_commitment": v.filter_by_commitment,
                     "interslot_updates": v.interslot_updates}))).collect::<BTreeMap<_, _>>(),
        "transactions": req.transactions.iter().map(|(k, v)| (k.clone(), json!({"account_include": v.account_include,
                     "account_exclude": v.account_exclude, "account_required": v.account_required, "vote": v.vote,
                     "failed": v.failed}))).collect::<BTreeMap<_, _>>(),
        "accounts": req.accounts.len(), "blocks": req.blocks.len(), "blocks_meta": req.blocks_meta.len(),
        "entry": req.entry.len(), "from_slot": req.from_slot,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Stop {
    Done,
    MaxBytes,
    Projected,
}

impl Stop {
    fn end(self) -> (String, i32) {
        match self {
            Stop::Done => ("done".into(), 0),
            Stop::MaxBytes => ("max-bytes reached".into(), EXIT_MAX_BYTES),
            Stop::Projected => ("projected over max-bytes after dry-minutes".into(), EXIT_PROJECTED),
        }
    }
}

#[derive(Debug, Default, Clone, Copy)]
struct Count {
    messages: u64,
    raw: u64,
    metered: u64,
}

/// One filter's share in mode B: `matched` updates matched it, each counted 1/k (k of our filters matched it).
#[derive(Debug, Default, Clone)]
struct Share {
    id: String,
    matched: u64,
    messages: f64,
    raw: f64,
    metered: f64,
}

#[derive(Debug, Default, Clone)]
struct SlotRow {
    parent: Option<u64>,
    /// The local receive time (unix ns) of the first update of each status, by proto number.
    first_ns: [Option<u64>; 7],
    updates: u32,
    dead_error: Option<String>,
}

struct Probe {
    mode: Mode,
    wire_ratio: f64,
    minutes: u64,
    max_bytes: u64,
    dry_minutes: Option<u64>,
    start_ns: u64,
    total: Count,
    // mode A
    slots: BTreeMap<u64, SlotRow>,
    unknown_status: u64,
    // mode B
    shares: BTreeMap<String, Share>,
    other: Count,
    multi: u64,
    projection: Option<Value>,
    /// Mode B's commitment ("confirmed" or "processed"); None in mode A and on the deshred stream.
    commitment: Option<String>,
    /// The gRPC stream: "subscribe" or "deshred".
    stream: &'static str,
}

impl Probe {
    fn new(a: &Args, start_ns: u64) -> Self {
        let shares = match &a.mode {
            Mode::Tx(p) => p.iter().map(|(n, id)| (n.clone(), Share { id: id.clone(), ..Share::default() })).collect(),
            Mode::Slots => BTreeMap::new(),
        };
        Probe {
            mode: a.mode.clone(),
            wire_ratio: a.wire_ratio,
            minutes: a.minutes,
            max_bytes: a.max_bytes,
            dry_minutes: a.dry_minutes,
            start_ns,
            total: Count::default(),
            slots: BTreeMap::new(),
            unknown_status: 0,
            shares,
            other: Count::default(),
            multi: 0,
            projection: None,
            commitment: (matches!(a.mode, Mode::Tx(_)) && !a.deshred).then(|| a.commitment.as_str_name().to_ascii_lowercase()),
            stream: if a.deshred { "deshred" } else { "subscribe" },
        }
    }

    /// One update as it came (`now_ns` its local receive time). Some(MaxBytes) once the metered bytes reach the cap.
    fn on_update(&mut self, u: &SubscribeUpdate, now_ns: u64) -> Option<Stop> {
        let slot = match &u.update_oneof {
            Some(UpdateOneof::Slot(s)) => Some(s),
            _ => None,
        };
        self.count(u.encoded_len(), &u.filters, slot, now_ns)
    }

    /// One update of the deshred stream (`--deshred`): counted and attributed like a transaction update.
    fn on_deshred(&mut self, u: &SubscribeUpdateDeshred, now_ns: u64) -> Option<Stop> {
        self.count(u.encoded_len(), &u.filters, None, now_ns)
    }

    fn count(&mut self, raw: usize, filters: &[String], slot: Option<&SubscribeUpdateSlot>, now_ns: u64) -> Option<Stop> {
        let metered = wire::metered(raw, self.wire_ratio);
        self.total.messages += 1;
        self.total.raw += raw as u64;
        self.total.metered += metered;
        match (&self.mode, slot) {
            (Mode::Slots, Some(s)) => {
                let row = self.slots.entry(s.slot).or_default();
                row.updates += 1;
                row.parent = row.parent.or(s.parent);
                match usize::try_from(s.status).ok().filter(|i| *i < STATUS.len()) {
                    Some(i) => {
                        if row.first_ns[i].is_none() {
                            row.first_ns[i] = Some(now_ns);
                        }
                        if i == DEAD && row.dead_error.is_none() {
                            row.dead_error = s.dead_error.clone();
                        }
                    }
                    None => self.unknown_status += 1,
                }
            }
            (Mode::Tx(_), _) => {
                let ours: BTreeSet<&str> = filters.iter().map(String::as_str).filter(|f| self.shares.contains_key(*f)).collect();
                if ours.is_empty() {
                    self.other.messages += 1;
                    self.other.raw += raw as u64;
                    self.other.metered += metered;
                } else {
                    let k = ours.len() as f64;
                    if ours.len() > 1 {
                        self.multi += 1;
                    }
                    for f in ours {
                        let s = self.shares.get_mut(f).expect("one of ours");
                        s.matched += 1;
                        s.messages += 1.0 / k;
                        s.raw += raw as f64 / k;
                        s.metered += metered as f64 / k;
                    }
                }
            }
            (Mode::Slots, _) => {
                self.other.messages += 1;
                self.other.raw += raw as u64;
                self.other.metered += metered;
            }
        }
        (self.total.metered >= self.max_bytes).then_some(Stop::MaxBytes)
    }

    /// Once a second: Done at --minutes; after --dry-minutes (once) the projection of the full run.
    fn on_tick(&mut self, now_ns: u64) -> Option<Stop> {
        let elapsed = now_ns.saturating_sub(self.start_ns);
        if let Some(m) = self.dry_minutes.filter(|m| self.projection.is_none() && elapsed >= m * 60 * NS_PER_SEC) {
            // the full run at the rate so far, rounded up, in integers (elapsed > 0: at least one dry minute)
            let full = u128::from(self.minutes * 60 * NS_PER_SEC);
            let projected = u64::try_from((u128::from(self.total.metered) * full).div_ceil(u128::from(elapsed))).unwrap_or(u64::MAX);
            let over = projected > self.max_bytes;
            self.projection = Some(json!({"after_minutes": m, "elapsed_secs": elapsed as f64 / 1e9, "metered_bytes": self.total.metered,
                                          "projected_metered_bytes": projected, "max_bytes": self.max_bytes, "over": over}));
            if over {
                return Some(Stop::Projected);
            }
        }
        (elapsed >= self.minutes * 60 * NS_PER_SEC).then_some(Stop::Done)
    }

    fn report(&self, status: &str, exit_code: Option<i32>, now_ns: u64) -> Value {
        let count = |c: &Count| json!({"messages": c.messages, "raw_bytes": c.raw, "metered_bytes": c.metered});
        let mut v = json!({
            "tool": "lab-probe",
            "format": 1,
            "mode": match self.mode { Mode::Slots => "slots", Mode::Tx(_) => "tx" },
            "status": status,
            "exit_code": exit_code,
            "started_unix_ns": self.start_ns,
            "written_unix_ns": now_ns,
            "elapsed_secs": now_ns.saturating_sub(self.start_ns) as f64 / 1e9,
            "minutes": self.minutes,
            "max_bytes": self.max_bytes,
            "dry_minutes": self.dry_minutes,
            "wire_ratio": self.wire_ratio,
            "commitment": self.commitment,
            "stream": self.stream,
            "projection": self.projection,
            "total": count(&self.total),
            "other": count(&self.other),
        });
        match &self.mode {
            Mode::Slots => {
                v["summary"] = slot_summary(&self.slots, self.unknown_status);
                v["slots"] = self.slots.iter().map(|(n, r)| slot_row(*n, r)).collect();
            }
            Mode::Tx(_) => {
                v["matched_by_several"] = json!(self.multi);
                v["filters"] = self
                    .shares
                    .iter()
                    .map(|(n, s)| (n.clone(), json!({"program": s.id, "matched": s.matched, "messages": s.messages,
                                                       "raw_bytes": s.raw, "metered_bytes": s.metered})))
                    .collect::<serde_json::Map<_, _>>()
                    .into();
            }
        }
        v
    }
}

fn slot_row(slot: u64, r: &SlotRow) -> Value {
    let mut row = serde_json::Map::new();
    row.insert("slot".into(), json!(slot));
    row.insert("parent".into(), json!(r.parent));
    for i in LIFECYCLE {
        row.insert(format!("{}_ns", STATUS[i]), json!(r.first_ns[i]));
    }
    row.insert("updates".into(), json!(r.updates));
    row.insert("dead_error".into(), json!(r.dead_error));
    row.into()
}

/// Linear interpolation between the closest ranks (numpy's default), on sorted values; None when empty.
fn quantile(sorted: &[f64], q: f64) -> Option<f64> {
    let last = sorted.len().checked_sub(1)?;
    let pos = q * last as f64;
    let (lo, hi) = (pos.floor() as usize, pos.ceil() as usize);
    Some(sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo as f64))
}

fn stats(mut ms: Vec<f64>) -> Value {
    ms.sort_by(f64::total_cmp);
    json!({"n": ms.len(), "median_ms": quantile(&ms, 0.5), "p90_ms": quantile(&ms, 0.9)})
}

/// `to - from` in ms, signed (an update can come out of order), subtracted in integers: unix ns are beyond f64's
/// 53 bits.
fn ms_between(from_ns: u64, to_ns: u64) -> f64 {
    (i128::from(to_ns) - i128::from(from_ns)) as f64 / 1e6
}

/// Milliseconds from status `a` to status `b` of the same slot, for every slot that has both.
fn between(slots: &BTreeMap<u64, SlotRow>, a: usize, b: usize) -> Vec<f64> {
    slots.values().filter_map(|r| Some(ms_between(r.first_ns[a]?, r.first_ns[b]?))).collect()
}

/// Milliseconds between status `i` of slot s and of slot s + 1, for every such pair.
fn per_slot(slots: &BTreeMap<u64, SlotRow>, i: usize) -> Vec<f64> {
    slots
        .iter()
        .filter_map(|(n, r)| {
            let next = slots.get(&(n + 1))?;
            Some(ms_between(r.first_ns[i]?, next.first_ns[i]?))
        })
        .collect()
}

fn slot_summary(slots: &BTreeMap<u64, SlotRow>, unknown_status: u64) -> Value {
    let (first, last) = (slots.keys().next().copied(), slots.keys().next_back().copied());
    // Slot numbers between the first and the last that never had any update, and in how many runs.
    let (mut missing, mut runs) = (0u64, 0u64);
    let mut prev: Option<u64> = None;
    for n in slots.keys() {
        if let Some(p) = prev.filter(|p| n - p > 1) {
            missing += n - p - 1;
            runs += 1;
        }
        prev = Some(*n);
    }
    let dead: Vec<u64> = slots.iter().filter(|(_, r)| r.first_ns[DEAD].is_some()).map(|(n, _)| *n).collect();
    json!({
        "slots_seen": slots.len(),
        "first_slot": first,
        "last_slot": last,
        "ms_per_slot": {"first_shred_received": stats(per_slot(slots, FIRST_SHRED)), "processed": stats(per_slot(slots, PROCESSED))},
        "processed_to_confirmed": stats(between(slots, PROCESSED, CONFIRMED)),
        "confirmed_to_finalized": stats(between(slots, CONFIRMED, FINALIZED)),
        "dead_slots": dead.len(),
        "dead": dead,
        "gaps": {"missing_slots": missing, "runs": runs},
        "status_counts": (0..STATUS.len()).map(|i| (STATUS[i].to_owned(), json!(slots.values().filter(|r| r.first_ns[i].is_some()).count())))
                             .collect::<serde_json::Map<_, _>>(),
        "unknown_status_updates": unknown_status,
    })
}

/// The output, atomically: a temporary file beside it, fsynced, renamed over it, the directory fsynced.
fn write_json_atomic(path: &Path, v: &Value) -> Result<()> {
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
    std::fs::write(&tmp, serde_json::to_vec_pretty(v)?).with_context(|| format!("write {}", tmp.display()))?;
    std::fs::File::open(&tmp)?.sync_all()?;
    std::fs::rename(&tmp, path).with_context(|| format!("rename to {}", path.display()))?;
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::File::open(dir)?.sync_all()?;
    }
    Ok(())
}

fn unix_ns() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| u64::try_from(d.as_nanos()).unwrap_or(u64::MAX))
}

/// An update of the run's stream: Subscribe or SubscribeDeshred.
enum Got {
    Update(Box<SubscribeUpdate>),
    Deshred(Box<SubscribeUpdateDeshred>),
}

/// The request sink of the run's subscription, held for as long as its stream runs.
#[allow(dead_code)]
enum Sink {
    Subscribe(SubscribeRequestSink),
    Deshred(SubscribeDeshredRequestSink),
}

async fn run(a: Args) -> Result<i32> {
    let (req, dreq) = (request(&a.mode, a.commitment), deshred_request(&a.mode));
    if a.print_request {
        let summary = if a.deshred { deshred_summary(&dreq) } else { request_summary(&req) };
        println!("{}", serde_json::to_string_pretty(&summary)?);
        return Ok(0);
    }
    let endpoint = a.endpoint.clone().context("pass --endpoint or set LAB_TRITON_GRPC_ENDPOINT")?;
    let mut probe = Probe::new(&a, unix_ns());
    write_json_atomic(&a.out, &probe.report("connecting", None, unix_ns()))?; // an unwritable --out fails before any byte
    let mut sigs = a.signatures_out.as_deref().map(Signatures::create).transpose()?;
    // The recorder's client settings (main.rs session): TLS with the native roots, zstd, the token as x-token.
    let token = std::env::var("LAB_TRITON_X_TOKEN").ok();
    let shown = |text: String| redact(&text, &endpoint);
    let connected = async {
        let mut client = GeyserGrpcClient::build_from_shared(endpoint.clone())?
            .x_token(token)?
            .tls_config(ClientTlsConfig::new().with_native_roots())?
            .accept_compressed(CompressionEncoding::Zstd)
            .max_decoding_message_size(256 * 1024 * 1024)
            .connect_timeout(Duration::from_secs(10))
            .http2_keep_alive_interval(Duration::from_secs(30))
            .keep_alive_while_idle(true)
            .connect()
            .await
            .context("connect")?;
        if a.deshred {
            let (sink, stream) = client.subscribe_deshred_with_request(Some(dreq)).await.context("subscribe deshred")?;
            anyhow::Ok((client, Sink::Deshred(sink), stream.map(|m| m.map(|u| Got::Deshred(Box::new(u)))).boxed_local()))
        } else {
            let (sink, stream) = client.subscribe_with_request(Some(req)).await.context("subscribe")?;
            anyhow::Ok((client, Sink::Subscribe(sink), stream.map(|m| m.map(|u| Got::Update(Box::new(u)))).boxed_local()))
        }
    }
    .await;
    // The client and the request sink stay alive for the whole run (the stream is theirs).
    let (_client, _sink, mut stream) = match connected {
        Ok(s) => s,
        Err(e) => {
            let why = shown(format!("not started: {e:#}"));
            log::error!("{why}");
            write_json_atomic(&a.out, &probe.report(&why, Some(EXIT_INCOMPLETE), unix_ns()))?;
            return Ok(EXIT_INCOMPLETE);
        }
    };
    probe.start_ns = unix_ns(); // the clock of --minutes and --dry-minutes runs from the accepted subscription
    let mode = if a.mode == Mode::Slots { "slots" } else if a.deshred { "deshred" } else { "tx" };
    log::info!("subscribed ({mode} mode); writing {}", a.out.display());
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    let ctrl_c = tokio::signal::ctrl_c();
    tokio::pin!(ctrl_c);
    let (mut last_update, mut last_write) = (Instant::now(), Instant::now());
    let (status, code) = loop {
        tokio::select! {
            m = stream.next() => match m {
                Some(Ok(g)) => {
                    last_update = Instant::now();
                    let now = unix_ns();
                    let stop = match &g {
                        Got::Update(u) => probe.on_update(u, now),
                        Got::Deshred(d) => probe.on_deshred(d, now),
                    };
                    if let Some(stop) = stop {
                        break stop.end();
                    }
                    if let Some(s) = sigs.as_mut() {
                        let added = match &g {
                            Got::Update(u) => s.add(u, now),
                            Got::Deshred(d) => s.add_deshred(d, now),
                        };
                        match added {
                            Ok(bytes) if bytes >= a.max_bytes => break ("max-bytes reached by --signatures-out".into(), EXIT_MAX_BYTES),
                            Ok(_) => {}
                            Err(e) => break (format!("--signatures-out: {e:#}"), EXIT_INCOMPLETE),
                        }
                    }
                }
                Some(Err(status)) => break (shown(format!("stream: {status}")), EXIT_INCOMPLETE),
                None => break ("the stream ended".into(), EXIT_INCOMPLETE),
            },
            _ = tick.tick() => {
                if last_update.elapsed() > Duration::from_secs(WATCHDOG_SECS) {
                    break (format!("no update for {WATCHDOG_SECS} s"), EXIT_INCOMPLETE);
                }
                if let Some(stop) = probe.on_tick(unix_ns()) {
                    break stop.end();
                }
                if last_write.elapsed() >= Duration::from_secs(60) {
                    last_write = Instant::now();
                    if let Err(e) = write_json_atomic(&a.out, &probe.report("running", None, unix_ns())) {
                        log::warn!("the minute's output: {e:#}");
                    }
                    if let Some(Err(e)) = sigs.as_mut().map(|s| s.flush(false)) {
                        log::warn!("the minute's signatures: {e:#}");
                    }
                }
            }
            _ = term.recv() => break ("terminated".into(), EXIT_INCOMPLETE),
            _ = &mut ctrl_c => break ("interrupted".into(), EXIT_INCOMPLETE),
        }
    };
    let mut report = probe.report(&status, Some(code), unix_ns());
    if let Some(s) = sigs.as_mut() {
        s.flush(true)?;
        report["signatures"] = json!({"file": a.signatures_out.as_ref().map(|p| p.display().to_string()), "rows": s.rows, "bytes": s.bytes});
    }
    write_json_atomic(&a.out, &report)?;
    let t = probe.total;
    log::info!("{status}: {} messages, {} raw bytes, {} metered bytes; exit {code}", t.messages, t.raw, t.metered);
    Ok(code)
}

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let args = parse_args(std::env::args().skip(1), std::env::var("LAB_TRITON_GRPC_ENDPOINT").ok())?;
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().thread_name("lab-probe").build()?;
    let code = runtime.block_on(run(args))?;
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::{
        deshred_request, parse_args, quantile, redact, request, signature_row, wire, write_json_atomic, Args, Mode, Probe,
        Signatures, Stop, EXIT_MAX_BYTES, EXIT_PROJECTED, NS_PER_SEC, SIGNATURES_HEADER,
    };
    use serde_json::Value;
    use yellowstone_grpc_proto::prelude::{
        subscribe_update::UpdateOneof, CommitmentLevel, SlotStatus, SubscribeUpdate, SubscribeUpdatePing, SubscribeUpdateSlot,
        SubscribeUpdateTransaction, SubscribeUpdateTransactionInfo,
    };
    use yellowstone_grpc_proto::prelude::{
        subscribe_update_deshred::UpdateOneof as DeshredOneof, SubscribeUpdateDeshred, SubscribeUpdateDeshredTransaction,
        SubscribeUpdateDeshredTransactionInfo,
    };
    use yellowstone_grpc_proto::prost::Message as _;

    const PUMP: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
    const PUMP_AMM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
    const DLMM: &str = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo";
    const T0: u64 = 1_792_000_000 * NS_PER_SEC;
    const MIN: u64 = 60 * NS_PER_SEC;

    fn args(s: &str) -> anyhow::Result<Args> {
        parse_args(s.split_whitespace().map(str::to_owned), None)
    }

    fn tx(filters: &[&str], size: usize) -> SubscribeUpdate {
        let info = SubscribeUpdateTransactionInfo { signature: vec![7; size], ..Default::default() };
        SubscribeUpdate {
            filters: filters.iter().map(|f| (*f).to_owned()).collect(),
            update_oneof: Some(UpdateOneof::Transaction(SubscribeUpdateTransaction { transaction: Some(info), slot: 1, bank_id: 0 })),
            created_at: None,
        }
    }

    fn ping() -> SubscribeUpdate {
        SubscribeUpdate { filters: vec![], update_oneof: Some(UpdateOneof::Ping(SubscribeUpdatePing {})), created_at: None }
    }

    fn slot(n: u64, status: SlotStatus) -> SubscribeUpdate {
        let dead_error = (status == SlotStatus::SlotDead).then(|| "dead".to_owned());
        let s = SubscribeUpdateSlot { slot: n, parent: Some(n - 1), status: status as i32, dead_error, bank_id: None };
        SubscribeUpdate { filters: vec!["slots".into()], update_oneof: Some(UpdateOneof::Slot(s)), created_at: None }
    }

    fn census() -> Probe {
        let a = args(&format!("--tx-program pump={PUMP} --tx-program pump_amm={PUMP_AMM} --tx-program meteora_dlmm={DLMM} --out x --minutes 60 --max-bytes 1000000000")).unwrap();
        Probe::new(&a, T0)
    }

    #[test]
    fn an_update_counts_once_in_the_total_and_one_kth_to_each_of_the_k_filters_it_matched() {
        let mut p = census();
        let updates = [
            tx(&["pump", "pump_amm"], 300),    // a pump.fun → PumpSwap route: half to each
            tx(&["pump", "pump_amm", "meteora_dlmm"], 500),
            tx(&["meteora_dlmm"], 200),
            tx(&["pump_amm", "pump_amm", "not_ours"], 100), // a name twice and an unknown name: k = 1
            ping(),                            // no filter of ours: other
        ];
        let len: Vec<f64> = updates.iter().map(|u| u.encoded_len() as f64).collect();
        for u in &updates {
            assert_eq!(p.on_update(u, T0 + 1), None);
        }
        let (s, close) = (&p.shares, |a: f64, b: f64| (a - b).abs() < 1e-9);
        assert_eq!(p.total.messages, 5);
        assert_eq!(p.total.raw as f64, len.iter().sum::<f64>());
        assert!(close(s["pump"].messages, 0.5 + 1.0 / 3.0) && close(s["pump"].raw, len[0] / 2.0 + len[1] / 3.0));
        assert!(close(s["pump_amm"].messages, 0.5 + 1.0 / 3.0 + 1.0));
        assert!(close(s["meteora_dlmm"].raw, len[1] / 3.0 + len[2]));
        assert_eq!((s["pump"].matched, s["pump_amm"].matched, s["meteora_dlmm"].matched), (2, 3, 2));
        assert_eq!((p.other.messages, p.other.raw as f64, p.multi), (1, len[4], 2));
        // the filters and other add up to the total, in messages and in bytes
        let shared: f64 = s.values().map(|x| x.raw).sum::<f64>() + p.other.raw as f64;
        assert!(close(shared, p.total.raw as f64));
        assert!(close(s.values().map(|x| x.messages).sum::<f64>() + p.other.messages as f64, 5.0));
        let r = p.report("done", Some(0), T0 + MIN);
        assert_eq!(r["filters"]["pump"]["program"], PUMP);
        assert_eq!(r["matched_by_several"], 2);
    }

    #[test]
    fn the_metered_bytes_stop_the_run_at_the_update_that_reaches_the_cap() {
        let one = tx(&["pump"], 400);
        let m = wire::metered(one.encoded_len(), 1.5);
        let a = args(&format!("--tx-program pump={PUMP} --out x --minutes 60 --max-bytes {} --wire-ratio 1.5", 3 * m)).unwrap();
        let mut p = Probe::new(&a, T0);
        assert_eq!(p.on_update(&one, T0), None);
        assert_eq!(p.on_update(&one, T0), None);
        assert_eq!(p.on_update(&one, T0), Some(Stop::MaxBytes)); // reached, not passed: the hard stop
        assert_eq!(p.total.metered, 3 * m);
        assert_eq!(Stop::MaxBytes.end().1, EXIT_MAX_BYTES);
        // the recorder's rule (wire.rs, as meter.rs counts): the protobuf size times the ratio, rounded up
        assert_eq!((wire::metered(1000, 1.0), wire::metered(3, 0.5), wire::metered(10, 1.25)), (1000, 2, 13));
    }

    #[test]
    fn after_the_dry_minutes_a_run_that_projects_over_the_cap_stops_and_one_below_runs_to_its_end() {
        let one = tx(&["pump"], 1000);
        let b = one.encoded_len() as u64;
        // 10 updates in 5 minutes project 120 over 60 minutes: over a cap of 100, under one of 200
        for (cap, expect) in [(100 * b, Some(Stop::Projected)), (200 * b, None)] {
            let a = args(&format!("--tx-program pump={PUMP} --out x --minutes 60 --max-bytes {cap} --dry-minutes 5")).unwrap();
            let mut p = Probe::new(&a, T0);
            for i in 0..10 {
                p.on_update(&one, T0 + i * 30 * NS_PER_SEC);
            }
            assert_eq!(p.on_tick(T0 + 5 * MIN - 1), None); // before the dry minutes: no projection yet
            assert!(p.projection.is_none());
            assert_eq!(p.on_tick(T0 + 5 * MIN), expect);
            let pr = p.projection.clone().unwrap();
            assert_eq!(pr["projected_metered_bytes"], 120 * b);
            assert_eq!(pr["over"], expect.is_some());
            if expect.is_none() {
                assert_eq!(p.on_tick(T0 + 59 * MIN), None);
                assert_eq!(p.on_tick(T0 + 60 * MIN), Some(Stop::Done)); // --minutes: exit 0
                assert_eq!(Stop::Done.end().1, 0);
            }
        }
        assert_eq!(Stop::Projected.end().1, EXIT_PROJECTED);
    }

    #[test]
    fn slot_statuses_give_per_slot_times_and_the_timing_summary() {
        let a = args("--slots --out x --minutes 60 --max-bytes 1000000000").unwrap();
        let mut p = Probe::new(&a, T0);
        let ms = |x: u64| T0 + x * 1_000_000;
        // slots 100, 101, 103 and 104 (102 never comes: a gap), 400 ms apart; 104 dies
        for (n, start) in [(100, 0), (101, 400), (103, 1200), (104, 1600)] {
            p.on_update(&slot(n, SlotStatus::SlotFirstShredReceived), ms(start));
            p.on_update(&slot(n, SlotStatus::SlotCreatedBank), ms(start + 10));
            if n == 104 {
                p.on_update(&slot(n, SlotStatus::SlotDead), ms(start + 50));
                continue;
            }
            p.on_update(&slot(n, SlotStatus::SlotCompleted), ms(start + 300));
            p.on_update(&slot(n, SlotStatus::SlotProcessed), ms(start + 350));
            p.on_update(&slot(n, SlotStatus::SlotProcessed), ms(start + 999)); // a repeat keeps the first time
            let to_confirmed = match n { 100 => 200, 101 => 300, _ => 500 };
            p.on_update(&slot(n, SlotStatus::SlotConfirmed), ms(start + 350 + to_confirmed));
            p.on_update(&slot(n, SlotStatus::SlotFinalized), ms(start + 13_000));
        }
        let r = p.report("done", Some(0), ms(20_000));
        let s = &r["summary"];
        assert_eq!(s["slots_seen"], 4);
        assert_eq!((s["first_slot"].as_u64(), s["last_slot"].as_u64()), (Some(100), Some(104)));
        // per slot: 100→101 and 103→104 (102 is missing); both 400 ms
        assert_eq!(s["ms_per_slot"]["first_shred_received"]["n"], 2);
        assert_eq!(s["ms_per_slot"]["first_shred_received"]["median_ms"], 400.0);
        assert_eq!(s["ms_per_slot"]["processed"]["n"], 1); // 104 never processed
        let pc = &s["processed_to_confirmed"];
        assert_eq!((pc["n"].as_u64(), pc["median_ms"].as_f64(), pc["p90_ms"].as_f64()), (Some(3), Some(300.0), Some(460.0)));
        let cf = &s["confirmed_to_finalized"];
        assert_eq!((cf["n"].as_u64(), cf["median_ms"].as_f64()), (Some(3), Some(12_350.0))); // 13000 - 350 - 300
        assert_eq!((s["dead_slots"].as_u64(), s["dead"][0].as_u64()), (Some(1), Some(104)));
        assert_eq!((s["gaps"]["missing_slots"].as_u64(), s["gaps"]["runs"].as_u64()), (Some(1), Some(1)));
        let rows = r["slots"].as_array().unwrap();
        assert_eq!(rows.len(), 4);
        assert_eq!(rows[0]["processed_ns"], ms(350)); // the first PROCESSED, not the repeat
        assert_eq!((rows[0]["updates"].as_u64(), rows[0]["parent"].as_u64()), (Some(7), Some(99)));
        assert_eq!((rows[3]["dead_ns"].as_u64(), rows[3]["dead_error"].as_str()), (Some(ms(1650)), Some("dead")));
        assert_eq!(rows[3]["confirmed_ns"], Value::Null);
        assert_eq!(p.total.messages, 4 * 2 + 3 * 5 + 1);
    }

    #[test]
    fn quantiles_interpolate_between_ranks() {
        assert_eq!(quantile(&[], 0.5), None);
        assert_eq!(quantile(&[5.0], 0.9), Some(5.0));
        assert_eq!(quantile(&[1.0, 2.0, 3.0, 4.0], 0.5), Some(2.5));
        assert!((quantile(&[1.0, 2.0, 3.0, 4.0], 0.9).unwrap() - 3.7).abs() < 1e-12);
    }

    #[test]
    fn the_requests_are_the_measurements_and_nothing_else() {
        let r = request(&Mode::Slots, CommitmentLevel::Confirmed);
        let f = &r.slots["slots"];
        assert_eq!((f.filter_by_commitment, f.interslot_updates), (Some(false), Some(true)));
        assert!(r.transactions.is_empty() && r.accounts.is_empty() && r.blocks.is_empty() && r.blocks_meta.is_empty());
        assert!(r.entry.is_empty() && r.block_footer.is_empty() && r.from_slot.is_none());
        assert_eq!(r.commitment, Some(CommitmentLevel::Processed as i32));
        let a = args(&format!("--tx-program pump={PUMP} --tx-program meteora_dlmm={DLMM} --out x --minutes 5 --max-bytes 9")).unwrap();
        let r = request(&a.mode, a.commitment);
        assert_eq!(r.transactions.len(), 2);
        let t = &r.transactions["meteora_dlmm"];
        assert_eq!((t.vote, t.failed, t.account_include.clone()), (Some(false), None, vec![DLMM.to_owned()]));
        assert!(t.account_exclude.is_empty() && t.account_required.is_empty() && t.signature.is_none());
        assert!(r.slots.is_empty() && r.accounts.is_empty() && r.block_footer.is_empty());
        assert_eq!(r.commitment, Some(CommitmentLevel::Confirmed as i32));
    }

    #[test]
    fn the_arguments_need_one_mode_a_cap_and_valid_programs() {
        assert!(args(&format!("--tx-program pump={PUMP} --out x --minutes 5 --max-bytes 9 --dry-minutes 1")).is_ok());
        for bad in [
            "--slots --out x --minutes 5".to_owned(), // no cap
            "--slots --out x --max-bytes 9".to_owned(),
            "--slots --minutes 5 --max-bytes 9".to_owned(),
            "--slots --out x --minutes 5 --max-bytes 0".to_owned(),
            "--slots --out x --minutes 5 --max-bytes 9 --dry-minutes 5".to_owned(), // not below --minutes
            "--slots --out x --minutes 5 --max-bytes 9 --wire-ratio 0".to_owned(),
            "--out x --minutes 5 --max-bytes 9".to_owned(), // no mode
            format!("--slots --tx-program pump={PUMP} --out x --minutes 5 --max-bytes 9"),
            format!("--tx-program pump={PUMP} --tx-program pump={DLMM} --out x --minutes 5 --max-bytes 9"), // a name twice
            format!("--tx-program a={PUMP} --tx-program b={PUMP} --out x --minutes 5 --max-bytes 9"),       // an id twice
            "--tx-program pump=notbase58!! --out x --minutes 5 --max-bytes 9".to_owned(),
            "--tx-program pump=abc --out x --minutes 5 --max-bytes 9".to_owned(), // base58, not 32 bytes
            format!("--tx-program pu-mp={PUMP} --out x --minutes 5 --max-bytes 9"),
            format!("--tx-program {PUMP} --out x --minutes 5 --max-bytes 9"),
            "--slots --out x --minutes 5 --max-bytes 9 --bogus".to_owned(),
        ] {
            assert!(args(&bad).is_err(), "{bad}");
        }
        assert!(args("--slots --print-request").is_ok()); // shows the request without a run's arguments
    }

    #[test]
    fn the_output_is_never_the_recorders_budget_nor_beside_it_and_a_run_writes_only_its_own_file() {
        // LAB review on #501: an --out named budget.json, or in a directory holding one (a recorder root), would let the
        // atomic write replace the recorder's meter state. Both are refused; elsewhere the budget stays byte for byte.
        let root = std::env::temp_dir().join(format!("lab-probe-out-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("probe")).unwrap();
        std::fs::create_dir_all(root.join("other")).unwrap();
        let budget = br#"{"day": 20747, "spent_bytes": 123, "reserved_bytes": 456}"#;
        std::fs::write(root.join("budget.json"), budget).unwrap();
        let run = |out: &std::path::Path| args(&format!("--tx-program pump={PUMP} --out {} --minutes 60 --max-bytes 1000000", out.display()));
        for refused in [root.join("budget.json"), root.join("probe.json"), root.join("other/budget.json"), root.join("other/Budget.JSON")] {
            assert!(run(&refused).is_err(), "{}", refused.display());
        }
        let out = root.join("probe/probe.json");
        let a = run(&out).unwrap();
        let mut p = Probe::new(&a, T0);
        write_json_atomic(&out, &p.report("connecting", None, T0)).unwrap();
        for i in 0..50 {
            p.on_update(&tx(&["pump"], 500), T0 + i);
            if i % 10 == 0 {
                write_json_atomic(&out, &p.report("running", None, T0 + i)).unwrap(); // the minute's write
            }
        }
        write_json_atomic(&out, &p.report("done", Some(0), T0 + MIN)).unwrap();
        let names = |d: &std::path::Path| {
            let mut n: Vec<_> = std::fs::read_dir(d).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
            n.sort();
            n
        };
        assert_eq!(names(&root), ["budget.json", "other", "probe"]);
        assert_eq!(names(&root.join("probe")), ["probe.json"]); // no temporary file left, nothing else written
        assert!(names(&root.join("other")).is_empty());
        assert_eq!(std::fs::read(root.join("budget.json")).unwrap(), budget);
        let v: Value = serde_json::from_slice(&std::fs::read(&out).unwrap()).unwrap();
        assert_eq!((v["status"].as_str(), v["total"]["messages"].as_u64()), (Some("done"), Some(50)));
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn errors_name_no_endpoint() {
        let e = "https://grpc.example-node.net:443/";
        let text = "connect: transport error: dns error for grpc.example-node.net:443 (https://grpc.example-node.net:443) at grpc.example-node.net";
        let shown = redact(text, e);
        assert!(!shown.contains("example-node"), "{shown}");
        assert_eq!(shown, "connect: transport error: dns error for <endpoint> (<endpoint>) at <endpoint>");
        assert_eq!(redact("stream: status: Unavailable", "http://10.0.0.1:10000"), "stream: status: Unavailable");
        assert!(!redact("refused by 10.0.0.1", "http://10.0.0.1:10000").contains("10.0.0.1"));
    }

    #[test]
    fn the_commitment_goes_into_the_request_for_the_census_only() {
        let base = format!("--tx-program pump={PUMP} --out x --minutes 5 --max-bytes 9");
        let a = args(&base).unwrap();
        assert_eq!(request(&a.mode, a.commitment).commitment, Some(CommitmentLevel::Confirmed as i32)); // the default, as before
        let a = args(&format!("{base} --commitment processed")).unwrap();
        assert_eq!(request(&a.mode, a.commitment).commitment, Some(CommitmentLevel::Processed as i32));
        assert_eq!(Probe::new(&a, T0).report("done", Some(0), T0)["commitment"], "processed");
        let a = args(&format!("{base} --commitment confirmed")).unwrap();
        assert_eq!(request(&a.mode, a.commitment).commitment, Some(CommitmentLevel::Confirmed as i32));
        // mode A keeps its own request: every status at processed, whatever is passed
        assert_eq!(request(&Mode::Slots, CommitmentLevel::Confirmed).commitment, Some(CommitmentLevel::Processed as i32));
        assert_eq!(Probe::new(&args("--slots --out x --minutes 5 --max-bytes 9").unwrap(), T0).report("x", None, T0)["commitment"], Value::Null);
        for bad in [
            format!("{base} --commitment finalized"),
            "--slots --out x --minutes 5 --max-bytes 9 --commitment processed".to_owned(),
            "--slots --out x --minutes 5 --max-bytes 9 --signatures-out s.csv".to_owned(),
            format!("{base} --signatures-out x"), // the same file as --out
            format!("{base} --signatures-out budget.json"),
        ] {
            assert!(args(&bad).is_err(), "{bad}");
        }
        assert_eq!(args(&format!("{base} --signatures-out s.csv")).unwrap().signatures_out.unwrap().to_str(), Some("s.csv"));
    }

    #[test]
    fn every_transaction_update_becomes_one_signature_row_and_the_file_is_never_overwritten() {
        let dir = std::env::temp_dir().join(format!("lab-probe-sigs-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("sigs.csv");
        let mut s = Signatures::create(&path).unwrap();
        let mut a = tx(&["pump_amm", "pump", "pump"], 64); // sorted, once each
        if let Some(UpdateOneof::Transaction(t)) = a.update_oneof.as_mut() {
            t.slot = 459_700_001;
            t.bank_id = 7;
        }
        let b = tx(&["meteora_dlmm"], 64);
        let mut size = 0;
        for (u, now) in [(&a, T0 + 5), (&ping(), T0 + 6), (&b, T0 + 9)] {
            size = s.add(u, now).unwrap();
        }
        s.flush(true).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert_eq!(size, text.len() as u64); // the size the --max-bytes check sees is the file's
        let sig = bs58::encode(vec![7u8; 64]).into_string();
        assert_eq!(text, format!("{SIGNATURES_HEADER}{sig},459700001,7,pump|pump_amm,{}\n{sig},1,0,meteora_dlmm,{}\n", T0 + 5, T0 + 9));
        assert_eq!(s.rows, 2); // the ping has no row
        assert_eq!(signature_row(&ping(), T0), None);
        drop(s);
        assert!(Signatures::create(&path).is_err()); // an existing file is never replaced
        assert_eq!(std::fs::read_to_string(&path).unwrap(), text);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    fn deshred(filters: &[&str], size: usize, slot: u64) -> SubscribeUpdateDeshred {
        let info = SubscribeUpdateDeshredTransactionInfo { signature: vec![9; size], ..Default::default() };
        SubscribeUpdateDeshred {
            filters: filters.iter().map(|f| (*f).to_owned()).collect(),
            update_oneof: Some(DeshredOneof::DeshredTransaction(SubscribeUpdateDeshredTransaction { transaction: Some(info), slot })),
            created_at: None,
        }
    }

    #[test]
    fn deshred_asks_the_same_program_filters_without_votes_or_a_commitment() {
        let a = args(&format!("--tx-program pump={PUMP} --tx-program meteora_dlmm={DLMM} --deshred --out x --minutes 5 --max-bytes 9")).unwrap();
        assert!(a.deshred);
        let r = deshred_request(&a.mode);
        assert_eq!(r.deshred_transactions.len(), 2);
        let f = &r.deshred_transactions["meteora_dlmm"];
        assert_eq!((f.vote, f.account_include.clone()), (Some(false), vec![DLMM.to_owned()]));
        assert!(f.account_exclude.is_empty() && f.account_required.is_empty() && f.include_update_parent.is_none());
        assert!(r.slots.is_empty() && r.ping.is_none());
        let rep = Probe::new(&a, T0).report("done", Some(0), T0);
        assert_eq!((rep["stream"].as_str(), rep["commitment"].clone()), (Some("deshred"), Value::Null));
        let sub = args(&format!("--tx-program pump={PUMP} --out x --minutes 5 --max-bytes 9")).unwrap();
        assert_eq!(Probe::new(&sub, T0).report("done", Some(0), T0)["stream"], "subscribe"); // the default stays Subscribe
        for bad in [
            format!("--tx-program pump={PUMP} --deshred --commitment processed --out x --minutes 5 --max-bytes 9"),
            "--slots --deshred --out x --minutes 5 --max-bytes 9".to_owned(),
        ] {
            assert!(args(&bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn a_deshred_update_is_counted_attributed_capped_and_written_like_a_transaction() {
        let one = deshred(&["pump_amm", "pump"], 64, 459_700_123);
        let m = wire::metered(one.encoded_len(), 1.0);
        let a = args(&format!("--tx-program pump={PUMP} --tx-program pump_amm={PUMP_AMM} --deshred --out x --minutes 60 --max-bytes {}", 2 * m)).unwrap();
        let mut p = Probe::new(&a, T0);
        assert_eq!(p.on_deshred(&one, T0), None);
        assert!((p.shares["pump"].messages - 0.5).abs() < 1e-9 && p.shares["pump_amm"].matched == 1);
        assert_eq!(p.on_deshred(&one, T0), Some(Stop::MaxBytes)); // the same cap in metered bytes
        assert_eq!((p.total.messages, p.total.raw), (2, 2 * one.encoded_len() as u64));
        let dir = std::env::temp_dir().join(format!("lab-probe-deshred-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let mut s = Signatures::create(&dir.join("d.csv")).unwrap();
        s.add_deshred(&one, T0 + 3).unwrap();
        let ping = SubscribeUpdateDeshred { filters: vec![], update_oneof: Some(DeshredOneof::Ping(Default::default())), created_at: None };
        s.add_deshred(&ping, T0 + 4).unwrap();
        s.flush(true).unwrap();
        let sig = bs58::encode(vec![9u8; 64]).into_string();
        assert_eq!(std::fs::read_to_string(dir.join("d.csv")).unwrap(),
                   format!("{SIGNATURES_HEADER}{sig},459700123,,pump|pump_amm,{}\n", T0 + 3)); // bank_id empty
        assert_eq!(s.rows, 1);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
