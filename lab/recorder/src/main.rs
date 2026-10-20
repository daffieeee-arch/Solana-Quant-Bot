//! lab-recorder: the live side of the paper engine.
//!
//! One gRPC connection to Triton's Dragon's Mouth (Yellowstone), commitment confirmed, zstd:
//! full transactions only for the watchlist (pools, open-position pools, wallets; vote=false,
//! failed=false) and for pump.fun graduations (new canonical pools), block meta and confirmed
//! slot statuses, and optionally vault balances and
//! PumpSwap pool accounts with a data slice. Transactions are decoded by the extractor's own
//! code (`../extractor/src`, compiled in), so the rows equal Old Faithful's; they are written as
//! rolling segments for `paper.live.LiveFeed` (see segment.rs). A byte meter per stream and
//! minute enforces the daily budget (meter.rs).
//!
//!   lab-recorder --root DIR [--endpoint URL] [--watchlist FILE] [--open-pools FILE]
//!                [--budget-usd 2] [--usd-per-gb 0.08] [--wire-ratio 1] [--segment-slots 150]
//!                [--segment-secs 60] [--close-lag 1] [--max-tx-accounts 10000] [--pool-accounts] [--no-new-pools]
//!                [--new-reversed] [--pool-creations] [--pump-creates] [--probe-depth-sol 120] [--probe-minutes 180]
//!                [--fresh-vault-minutes 60] [--no-backfill] [--backfill-wait-secs 20]
//!                [--reload-secs 10] [--min-update-secs 60] [--from-slot N] [--max-runtime-secs N]
//!                [--no-downscale] [--budget-assume-spent YYYY-MM-DD=BYTES] [--dry-run]
//!
//! The endpoint can also come from LAB_TRITON_GRPC_ENDPOINT; the token only from
//! LAB_TRITON_X_TOKEN (never logged). Exit code 75: the day's budget is spent, or today's spend is
//! unknown (meter.rs; the owner's restart override is --budget-assume-spent). Exit code 74: a
//! segment or budget.json could not be written; the restart resumes after the last segment on disk
//! and counts the budget reservation on disk.

#[allow(dead_code)]
#[path = "../../extractor/src/idl.rs"]
mod idl;
#[allow(dead_code)]
#[path = "../../extractor/src/sink.rs"]
mod sink;
#[allow(dead_code)]
#[path = "../../extractor/src/extract.rs"]
mod extract;

mod convert;
mod meter;
mod recorder;
mod segment;
mod watch;

use anyhow::{bail, Context, Result};
use extract::{Coverage, LabPlugin};
use futures::channel::oneshot;
use futures::stream::{BoxStream, SelectAll};
use futures::{SinkExt, StreamExt};
use std::collections::HashMap;
use meter::{BudgetWriteError, Meter, GB};
use recorder::{Action, Config, Recorder};
use segment::{SegmentWriteError, Segments};
use sink::Sink;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use watch::{Extra, Files, Filter, Opts};
use yellowstone_grpc_client::{ClientTlsConfig, GeyserGrpcClient, SubscribeRequestSink};
use yellowstone_grpc_proto::prelude::SubscribeUpdate;
use yellowstone_grpc_proto::tonic::codec::CompressionEncoding;
use yellowstone_grpc_proto::tonic::Status;

pub const EXIT_BUDGET: i32 = 75;
/// A segment could not be written. Its closed slots were only in memory, past the last segment on
/// disk; a reconnect in this process would resume after them and lose them. The recorder ends
/// instead (systemd restarts it) and resumes after the last complete segment on disk; the server
/// replays the rest through from_slot (a recorded gap where its replay window is shorter).
/// The same for budget.json: without a reservation on disk no byte may come (meter.rs), and a
/// reconnect would stream on unmetered (LAB review: past the cap, on every tick).
pub const EXIT_SEGMENT: i32 = 74;
/// Without any update on the main stream for this long the connection is renewed.
const WATCHDOG_SECS: u64 = 30;
/// Consecutive sessions without data before the recorder gives up (a human looks).
const MAX_FAILURES: u32 = 20;

struct Args {
    root: PathBuf,
    endpoint: Option<String>,
    watchlist: PathBuf,
    open_pools: PathBuf,
    budget_usd: f64,
    usd_per_gb: f64,
    wire_ratio: f64,
    segment_slots: u64,
    segment_secs: u64,
    close_lag: u64,
    max_tx_accounts: usize,
    pool_accounts: bool,
    new_pools: bool,
    new_reversed: bool,
    pump_creates: bool,
    pool_creations: bool,
    probe_depth_sol: f64,
    probe_minutes: u64,
    fresh_vault_minutes: u64,
    backfill: bool,
    backfill_wait_secs: u64,
    reload_secs: u64,
    min_update_secs: u64,
    from_slot: Option<u64>,
    max_runtime_secs: u64,
    no_downscale: bool,
    /// The owner's restart override for a day whose spend is unknown: (UTC day, bytes).
    budget_assume: Option<(u64, u64)>,
    dry_run: bool,
}

fn parse_args() -> Result<Args> {
    let mut it = std::env::args().skip(1);
    let mut root: Option<PathBuf> = None;
    let mut a = Args {
        root: PathBuf::new(),
        endpoint: std::env::var("LAB_TRITON_GRPC_ENDPOINT").ok(),
        watchlist: PathBuf::new(),
        open_pools: PathBuf::new(),
        budget_usd: 2.0,
        usd_per_gb: 0.08,
        wire_ratio: 1.0,
        segment_slots: 150,
        segment_secs: 60,
        close_lag: 1,
        max_tx_accounts: 10_000,
        pool_accounts: false,
        new_pools: true,
        new_reversed: false,
        pump_creates: false,
        pool_creations: false,
        probe_depth_sol: 120.0,
        probe_minutes: 180,
        fresh_vault_minutes: 60,
        backfill: true,
        backfill_wait_secs: 20,
        reload_secs: 10,
        min_update_secs: 60,
        from_slot: None,
        max_runtime_secs: 0,
        no_downscale: false,
        budget_assume: None,
        dry_run: false,
    };
    let (mut watchlist, mut open_pools) = (None, None);
    while let Some(flag) = it.next() {
        let mut val = || it.next().with_context(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--root" => root = Some(PathBuf::from(val()?)),
            "--endpoint" => a.endpoint = Some(val()?),
            "--watchlist" => watchlist = Some(PathBuf::from(val()?)),
            "--open-pools" => open_pools = Some(PathBuf::from(val()?)),
            "--budget-usd" => a.budget_usd = val()?.parse()?,
            "--usd-per-gb" => a.usd_per_gb = val()?.parse()?,
            "--wire-ratio" => a.wire_ratio = val()?.parse()?,
            "--segment-slots" => a.segment_slots = val()?.parse()?,
            "--segment-secs" => a.segment_secs = val()?.parse()?,
            "--close-lag" => a.close_lag = val()?.parse()?,
            "--max-tx-accounts" => a.max_tx_accounts = val()?.parse()?,
            "--pool-accounts" => a.pool_accounts = true,
            "--no-new-pools" => a.new_pools = false,
            "--new-reversed" => a.new_reversed = true,
            "--pump-creates" => a.pump_creates = true,
            "--pool-creations" => a.pool_creations = true,
            "--probe-depth-sol" => a.probe_depth_sol = val()?.parse()?,
            "--probe-minutes" => a.probe_minutes = val()?.parse()?,
            "--fresh-vault-minutes" => a.fresh_vault_minutes = val()?.parse()?,
            "--no-backfill" => a.backfill = false,
            "--backfill-wait-secs" => a.backfill_wait_secs = val()?.parse()?,
            "--reload-secs" => a.reload_secs = val()?.parse()?,
            "--min-update-secs" => a.min_update_secs = val()?.parse()?,
            "--from-slot" => a.from_slot = Some(val()?.parse()?),
            "--max-runtime-secs" => a.max_runtime_secs = val()?.parse()?,
            "--dry-run" => a.dry_run = true,
            "--no-downscale" => a.no_downscale = true,
            "--budget-assume-spent" => a.budget_assume = Some(parse_assume(&val()?)?),
            other => bail!("unknown argument {other}"),
        }
    }
    a.root = root.context("pass --root")?;
    a.watchlist = watchlist.unwrap_or_else(|| a.root.join("watchlist-current.json"));
    a.open_pools = open_pools.unwrap_or_else(|| a.root.join("open-pools.json"));
    if !(a.budget_usd > 0.0 && a.usd_per_gb > 0.0 && a.wire_ratio > 0.0) {
        bail!("budget, price and wire ratio must be positive");
    }
    if a.segment_slots == 0 || a.max_tx_accounts == 0 {
        bail!("--segment-slots and --max-tx-accounts must be positive");
    }
    Ok(a)
}

/// `--budget-assume-spent YYYY-MM-DD=BYTES`: today's spend when it cannot be known (meter.rs), for
/// that UTC day only.
fn parse_assume(s: &str) -> Result<(u64, u64)> {
    let (date, bytes) = s.split_once('=').context("--budget-assume-spent takes YYYY-MM-DD=BYTES")?;
    let day = meter::day_from_date(date).with_context(|| format!("--budget-assume-spent: {date} is not a date YYYY-MM-DD"))?;
    Ok((day, bytes.parse().context("--budget-assume-spent: BYTES must be a whole number")?))
}

/// The exit code for an error that ends the recorder: a segment or budget.json that could not be
/// written. The meter is settled first, so budget.json holds exactly what came (LAB review P2, #308);
/// when that fails too, its reservation on disk still covers it.
fn write_exit(e: &anyhow::Error, meter: &mut Meter) -> Option<i32> {
    let what = e.chain().find_map(|c| {
        c.downcast_ref::<SegmentWriteError>().map(ToString::to_string).or_else(|| c.downcast_ref::<BudgetWriteError>().map(ToString::to_string))
    })?;
    save_before_exit(meter);
    log::error!("{what}; exiting with {EXIT_SEGMENT}: the restart resumes after the last segment on disk");
    Some(EXIT_SEGMENT)
}

/// `Ok(EXIT_SEGMENT)` for a segment or budget.json write error, the error itself otherwise.
fn end_on(e: anyhow::Error, meter: &mut Meter) -> Result<i32> {
    match write_exit(&e, meter) {
        Some(code) => Ok(code),
        None => Err(e),
    }
}

/// Before the stream: the budget reservation on disk (meter.rs), then the segments. The reservation comes first:
/// Segments::open removes the unfinished segment directories that made today's spend unknown, so an owner's override
/// is on disk before its evidence goes (LAB review). None: the reservation could not be saved (nothing removed; exit
/// 74). A failed open settles the reservation again: no byte has come.
fn reserve_and_open(meter: &mut Meter, root: &Path, seg_slots: u64, seg_secs: u64) -> Result<Option<(Segments, Option<u64>)>> {
    if let Err(e) = meter.reserve_ahead() {
        log::error!("{e:#}; exiting with {EXIT_SEGMENT} before streaming");
        return Ok(None);
    }
    match Segments::open(root, seg_slots, seg_secs) {
        Ok(opened) => Ok(Some(opened)),
        Err(e) => {
            save_before_exit(meter);
            Err(e)
        }
    }
}

/// budget.json settled (no byte comes after it), before an exit that does not flush the meter.
fn save_before_exit(meter: &mut Meter) {
    if let Err(err) = meter.settle() {
        log::error!("budget: {err:#} before the exit; the reservation on disk covers the spend");
    }
}

fn opts(a: &Args) -> Opts {
    Opts {
        pool_accounts: a.pool_accounts,
        new_pools: a.new_pools,
        pool_creations: a.pool_creations,
        new_reversed: a.new_reversed,
        pump_creates: a.pump_creates,
        backfill: a.backfill,
        max_tx_accounts: a.max_tx_accounts,
    }
}

/// F7's triggers come minutes after a reversed pool is created (99% the same day), so without
/// creation detection (--new-reversed) no new pool is probed or followed. --pool-creations is a
/// measurement of pool writes and starts nothing (creation-filters spec v0.2 P3).
fn creation_warning(new_reversed: bool) -> Option<&'static str> {
    (!new_reversed).then_some(
        "F7 coverage ~0% without creation detection: pass --new-reversed (new reversed pools are found only \
         through it; --pool-creations measures pool writes and starts nothing; graduations alone cover M1, not F7)",
    )
}

fn unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

fn unix_ns() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| u64::try_from(d.as_nanos()).unwrap_or(u64::MAX))
}

enum Exit {
    Stop(String),
    Shutdown,
    Disconnected(String),
}

/// What one session reached, kept by the caller also when the session ends with an error.
#[derive(Default)]
struct SessionState {
    /// The main subscription was accepted.
    subscribed: bool,
    /// At least one update arrived on the main stream.
    got_data: bool,
    /// The server refused the main subscription while it asked for from_slot (a resume rejected
    /// at subscribe time counts like an accepted resume without data).
    resume_rejected: bool,
}

/// One connection: subscribe (resuming after the last closed slot when `resume`), then process
/// updates until the stream breaks, the watchdog fires, the budget stops us or we are told to stop.
async fn session(
    rec: &mut Recorder,
    a: &Args,
    files: &mut Files,
    endpoint: &str,
    resume: bool,
    deadline: Option<Instant>,
    st: &mut SessionState,
) -> Result<Exit> {
    let token = std::env::var("LAB_TRITON_X_TOKEN").ok();
    let mut client = GeyserGrpcClient::build_from_shared(endpoint.to_owned())?
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
    let (mut main_req, vault_req) = rec.requests(None);
    if !resume {
        main_req.from_slot = None;
    }
    // Resume from the slot we need, or from the start of the server's replay window when that
    // begins later (the slots in between are a recorded gap). Unknown window: try the slot itself.
    if let Some(wanted) = main_req.from_slot {
        match client.subscribe_replay_info().await {
            Ok(info) => {
                if let Some(first) = info.first_available.filter(|first| wanted < *first) {
                    log::warn!("replay window starts at slot {first}: slots {wanted}..{} cannot be replayed", first - 1);
                    rec.note_replay_gap(unix(), wanted, first);
                    main_req.from_slot = Some(first);
                }
            }
            Err(e) => log::warn!("replay info unavailable ({e}); resuming from slot {wanted}"),
        }
    }
    log::info!("subscribing: {} from slot {:?}", rec.active.summary(), main_req.from_slot);
    let resuming = main_req.from_slot.is_some();
    let (mut sink, mut stream) = match client.subscribe_with_request(Some(main_req)).await {
        Ok(subscription) => subscription,
        Err(e) => {
            st.resume_rejected = resuming;
            return Err(e).context("subscribe");
        }
    };
    // The vault stream is its own subscription on the same connection (its own data slice).
    let (mut vsink, mut vstream) = client.subscribe_with_request(Some(vault_req)).await.context("subscribe vaults")?;
    st.subscribed = true;
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    let mut last_main = Instant::now();
    let mut last_reload = Instant::now();
    // Backfill subscriptions (one per new probe, same connection): their updates tagged with the
    // backfill id; None marks a stream's end. Each is closed when its replay reached to_slot.
    let mut backfills: SelectAll<BoxStream<'static, (String, Option<Result<SubscribeUpdate, Status>>)>> = SelectAll::new();
    let mut backfill_open: HashMap<String, (SubscribeRequestSink, oneshot::Sender<()>)> = HashMap::new();
    loop {
        tokio::select! {
            Some((id, m)) = backfills.next(), if !backfills.is_empty() => {
                match m {
                    Some(Ok(u)) => rec.on_backfill_update(&id, u)?,
                    Some(Err(status)) => rec.backfill_failed(&id, &format!("{status}")),
                    None => rec.backfill_failed(&id, "the stream ended before its to_slot"),
                }
                for done in rec.take_finished_backfills() {
                    if let Some((_sink, cancel)) = backfill_open.remove(&done) {
                        let _ = cancel.send(());
                    }
                }
            },
            m = stream.next() => match m {
                Some(Ok(u)) => {
                    st.got_data = true;
                    last_main = Instant::now();
                    rec.on_update(u, unix_ns())?;
                }
                Some(Err(status)) => return Ok(Exit::Disconnected(format!("main stream: {status}"))),
                None => return Ok(Exit::Disconnected("main stream ended".into())),
            },
            m = vstream.next() => match m {
                Some(Ok(u)) => rec.on_update(u, unix_ns())?,
                Some(Err(status)) => return Ok(Exit::Disconnected(format!("vault stream: {status}"))),
                None => return Ok(Exit::Disconnected("vault stream ended".into())),
            },
            _ = tick.tick() => {
                if last_main.elapsed() > Duration::from_secs(WATCHDOG_SECS) {
                    return Ok(Exit::Disconnected(format!("no update for {WATCHDOG_SECS} s")));
                }
                if deadline.is_some_and(|d| Instant::now() >= d) {
                    return Ok(Exit::Shutdown);
                }
                let reload = if last_reload.elapsed() >= Duration::from_secs(a.reload_secs) {
                    last_reload = Instant::now();
                    files.reload()
                } else {
                    None
                };
                for (id, req) in rec.take_backfill_requests() {
                    match client.subscribe_with_request(Some(req)).await {
                        Ok((bsink, bstream)) => {
                            let (cancel, cancelled) = oneshot::channel::<()>();
                            let tag = id.clone();
                            backfills.push(
                                bstream
                                    .take_until(cancelled)
                                    .map(Some)
                                    .chain(futures::stream::once(async { None }))
                                    .map(move |m| (tag.clone(), m))
                                    .boxed(),
                            );
                            backfill_open.insert(id, (bsink, cancel));
                        }
                        Err(e) => rec.backfill_failed(&id, &format!("subscribe: {e:#}")),
                    }
                }
                for done in rec.take_finished_backfills() {
                    if let Some((_sink, cancel)) = backfill_open.remove(&done) {
                        let _ = cancel.send(());
                    }
                }
                match rec.on_tick(unix(), reload)? {
                    Action::None => {}
                    Action::Resubscribe(m, v) => {
                        sink.send(*m).await.context("update the main subscription")?;
                        vsink.send(*v).await.context("update the vault subscription")?;
                    }
                    Action::Stop(reason) => return Ok(Exit::Stop(reason)),
                }
            }
            _ = term.recv() => return Ok(Exit::Shutdown),
            _ = tokio::signal::ctrl_c() => return Ok(Exit::Shutdown),
        }
    }
}

async fn run(a: Args) -> Result<i32> {
    std::fs::create_dir_all(&a.root)?;
    let cap = (a.budget_usd / a.usd_per_gb * GB) as u64;
    let mut meter = Meter::load_with(&a.root, cap, a.wire_ratio, unix(), a.budget_assume)?;
    // A measuring run keeps every stream on; --budget-usd stays the hard stop.
    meter.no_downscale = a.no_downscale;
    let mut files = Files::new(a.watchlist.clone(), a.open_pools.clone());
    let watch = files.reload().unwrap_or_default();
    if a.dry_run {
        let f = Filter::new(&watch, &Extra::default(), 0, &opts(&a));
        let main = f.request(a.from_slot);
        let vaults = f.vault_request();
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "filter": f.summary(),
                "main": {"transactions": main.transactions.iter()
                             .map(|(k, v)| (k.clone(), serde_json::json!({"include": v.account_include.len(),
                                  "required": v.account_required.len(), "exclude": v.account_exclude.len()})))
                             .collect::<std::collections::BTreeMap<_, _>>(),
                         "accounts": main.accounts.keys().collect::<Vec<_>>(), "blocks_meta": main.blocks_meta.len(),
                         "slots": main.slots.len(), "commitment": main.commitment, "from_slot": main.from_slot,
                         "accounts_data_slice": main.accounts_data_slice.iter().map(|s| [s.offset, s.length]).collect::<Vec<_>>()},
                "vaults": {"accounts": vaults.accounts.values().map(|v| v.account.len()).sum::<usize>()},
                "budget": meter.state(),
                "watchlist_source": watch.source,
                "warnings": creation_warning(a.new_reversed).into_iter().collect::<Vec<_>>(),
                "invalid_addresses": watch.invalid,
            }))?
        );
        return Ok(0);
    }
    // One recorder per root: two would share the budget (each meters on its own) and collide on
    // segment numbers. The lock lives as long as the process.
    let lock = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(a.root.join("lock"))?;
    match lock.try_lock() {
        Ok(()) => {}
        Err(std::fs::TryLockError::WouldBlock) => {
            bail!("another recorder holds {}: one recorder per root (budget and segment numbers)", a.root.join("lock").display())
        }
        Err(std::fs::TryLockError::Error(e)) => return Err(e).context("lock the root"),
    }
    if meter.stopped {
        if meter.unknown.is_none() {
            log::error!("today's budget is spent ({:.2} GB of {:.2} GB); not starting before the next UTC day", meter.spent as f64 / GB, cap as f64 / GB);
        } // an unknown spend is logged by the meter, with the override
        return Ok(EXIT_BUDGET);
    }
    if let Some(w) = creation_warning(a.new_reversed) {
        log::warn!("{w}");
    }
    // A configuration error reserves nothing (each failed start would add a reservation; LAB review).
    let endpoint = a.endpoint.clone().context("pass --endpoint or set LAB_TRITON_GRPC_ENDPOINT")?;
    let Some((segs, last)) = reserve_and_open(&mut meter, &a.root, a.segment_slots, a.segment_secs)? else {
        return Ok(EXIT_SEGMENT);
    };
    // The extractor's plugin decodes; its own Parquet sink stays unused (the segments write).
    let plugin = match LabPlugin::new(Arc::new(Sink::start(a.root.join(".extractor-sink-unused"))), Arc::new(Coverage::new(0, 0))) {
        Ok(p) => p,
        Err(e) => {
            save_before_exit(&mut meter); // no byte has come: the reservation comes down again
            return Err(e);
        }
    };
    let cfg = Config {
        opts: opts(&a),
        min_update_secs: a.min_update_secs,
        probe_lamports: (a.probe_depth_sol * 1e9) as u64,
        probe_secs: a.probe_minutes * 60,
        fresh_vault_secs: a.fresh_vault_minutes * 60,
        backfill_wait_secs: a.backfill_wait_secs,
        backfill_max: 8,
    };
    let resume_from = a.from_slot.map(|s| s.saturating_sub(1)).or(last);
    let mut rec = Recorder::new(cfg, plugin, segs, meter, watch, resume_from, a.close_lag);
    let deadline = (a.max_runtime_secs > 0).then(|| Instant::now() + Duration::from_secs(a.max_runtime_secs));
    // failures: sessions in a row without data (transport errors included), for the backoff and
    // giving up. resume_failures: sessions whose resume subscription was accepted but brought no
    // update; only those make the recorder drop from_slot (transport errors never do: the server
    // may still replay the outage once it is reachable).
    let mut failures = 0u32;
    let mut resume_failures = 0u32;
    loop {
        let resume = resume_failures < 3;
        let mut st = SessionState::default();
        let exit = match session(&mut rec, &a, &mut files, &endpoint, resume, deadline, &mut st).await {
            Ok(e) => e,
            Err(e) => match write_exit(&e, &mut rec.meter) {
                Some(code) => return Ok(code),
                None => Exit::Disconnected(format!("{e:#}")),
            },
        };
        match exit {
            Exit::Stop(reason) => {
                log::error!("daily budget reached: {reason}; stopped until the next UTC day");
                return Ok(EXIT_BUDGET);
            }
            Exit::Shutdown => {
                if let Err(e) = rec.shut_down() {
                    return end_on(e, &mut rec.meter);
                }
                log::info!("shut down after slot {:?}", rec.slots.last_closed);
                return Ok(0);
            }
            Exit::Disconnected(reason) => {
                count_session(&st, resume, &mut failures, &mut resume_failures);
                let note = if resume { reason } else { format!("{reason} (resumed without from_slot)") };
                log::warn!("disconnected: {note}");
                rec.on_disconnect(unix(), &note);
                if failures >= MAX_FAILURES {
                    if let Err(e) = rec.close_segment(Some("giving up")) {
                        return end_on(e, &mut rec.meter);
                    }
                    save_before_exit(&mut rec.meter);
                    bail!("{failures} sessions in a row without data; giving up");
                }
                tokio::time::sleep(Duration::from_secs((1u64 << failures.min(6)).min(60))).await;
            }
        }
    }
}

/// Bookkeeping after a session that broke off. Data resets both counts (backoff 2 s). A session
/// without data adds to `failures`; it adds to `resume_failures` only when its resume subscription
/// was accepted, or refused by the server at subscribe time; transport errors before that (connect,
/// DNS, TLS) never cost from_slot.
fn count_session(st: &SessionState, resume: bool, failures: &mut u32, resume_failures: &mut u32) {
    if st.got_data {
        *failures = 1;
        *resume_failures = 0;
    } else {
        *failures += 1;
        if (st.subscribed || st.resume_rejected) && resume {
            *resume_failures += 1;
        }
    }
}

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let args = parse_args()?;
    // ClickHouse is not used by the extractor's plugin here either.
    std::env::set_var("JETSTREAMER_CLICKHOUSE_MODE", "off");
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().thread_name("lab-recorder").build()?;
    let code = runtime.block_on(run(args))?;
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::{count_session, end_on, parse_assume, reserve_and_open, unix, BudgetWriteError, Meter, SegmentWriteError, SessionState, EXIT_SEGMENT};

    #[test]
    fn the_budget_reservation_is_on_disk_before_the_segments_remove_an_unfinished_directory() {
        // LAB review: Segments::open removes the directory that made today's spend unknown. The owner's override is
        // on disk first; when it cannot be saved nothing is removed, and the restart finds the same unknown spend.
        let root = std::env::temp_dir().join(format!("lab-recorder-reserve-open-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("segments/000000000/blocks")).unwrap();
        let (cap, now) = (25_000_000_000, unix());
        let today = now / 86_400;
        std::fs::write(root.join("budget.json"), format!("{{\"day\": {today}, \"spent_bytes\": 1000}}")).unwrap();
        assert!(Meter::load_with(&root, cap, 1.0, now, None).unwrap().unknown.is_some());
        let mut meter = Meter::load_with(&root, cap, 1.0, now, Some((today, 5_000_000_000))).unwrap();
        std::fs::create_dir_all(root.join("budget.json.tmp")).unwrap(); // budget.json cannot be written
        assert!(reserve_and_open(&mut meter, &root, 150, 60).unwrap().is_none());
        assert!(root.join("segments/000000000").exists()); // the evidence stays
        std::fs::remove_dir_all(root.join("budget.json.tmp")).unwrap();
        assert!(reserve_and_open(&mut meter, &root, 150, 60).unwrap().is_some());
        assert!(!root.join("segments/000000000").exists());
        let again = Meter::load_with(&root, cap, 1.0, unix(), None).unwrap(); // the next start, without the override
        assert!(again.spent >= 5_000_000_000 && again.unknown.is_none());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_segment_or_budget_write_error_settles_the_meter_and_ends_the_recorder_other_errors_do_not() {
        let root = std::env::temp_dir().join(format!("lab-recorder-exit74-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let mut meter = Meter::load(&root, 25_000_000_000, 1.0, unix()).unwrap();
        meter.reserve_ahead().unwrap();
        meter.add("tx_pools", 12_345).unwrap(); // the running minute: below the reservation on disk
        assert!(end_on(anyhow::anyhow!("main stream: unavailable"), &mut meter).is_err()); // a disconnect: the session goes on
        assert!(Meter::load(&root, 25_000_000_000, 1.0, unix()).unwrap().spent > 12_345); // the reservation, not settled
        let w = SegmentWriteError { seq: 3, stage: "sync", kind: Some(std::io::ErrorKind::StorageFull) };
        assert_eq!(end_on(anyhow::Error::new(w).context("on_tick"), &mut meter).unwrap(), EXIT_SEGMENT);
        // settled: the restart's meter starts from exactly what came
        assert_eq!(Meter::load(&root, 25_000_000_000, 1.0, unix()).unwrap().spent, 12_345);
        let b = BudgetWriteError("injected".into());
        assert_eq!(end_on(anyhow::Error::new(b).context("on_update"), &mut meter).unwrap(), EXIT_SEGMENT);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn only_the_new_reversed_stream_detects_creations() {
        // creation-filters spec v0.2 P3: --pool-creations alone starts no probe, so it no longer silences the warning
        assert!(creation_warning(false).is_some_and(|w| w.contains("--new-reversed")));
        assert_eq!(creation_warning(true), None);
    }

    #[test]
    fn the_budget_override_names_its_day() {
        assert_eq!(parse_assume("2026-10-12=5000").unwrap(), (20_738, 5_000));
        for bad in ["2026-10-12", "2026-02-30=1", "x=1", "2026-10-12=1.5", "2026-10-12=-1"] {
            assert!(parse_assume(bad).is_err(), "{bad}");
        }
    }

    fn after(states: &[(bool, bool)]) -> (u32, u32) {
        let (mut failures, mut resume_failures) = (0, 0);
        for &(subscribed, got_data) in states {
            let resume = resume_failures < 3;
            let st = SessionState { subscribed, got_data, resume_rejected: false };
            count_session(&st, resume, &mut failures, &mut resume_failures);
        }
        (failures, resume_failures)
    }

    #[test]
    fn transport_errors_never_cost_the_resume() {
        // A long outage: the connection fails 15 times in a row; from_slot stays.
        let (failures, resume_failures) = after(&[(false, false); 15]);
        assert_eq!((failures, resume_failures), (15, 0));
    }

    #[test]
    fn three_accepted_resumes_without_data_drop_from_slot_and_data_resets() {
        assert_eq!(after(&[(true, false), (false, false), (true, false), (true, false)]), (4, 3));
        assert_eq!(after(&[(true, false), (true, false), (true, true)]), (1, 0));
        // Once from_slot is dropped (resume = false), live sessions without data do not count again.
        assert_eq!(after(&[(true, false); 5]).1, 3);
    }

    #[test]
    fn a_resume_refused_at_subscribe_counts_and_the_recorder_goes_live() {
        // The replay info failed and the server refuses from_slot at subscribe time, every time:
        // after three such sessions the recorder resumes without from_slot instead of giving up.
        let (mut failures, mut resume_failures) = (0, 0);
        let mut resumes = Vec::new();
        for _ in 0..5 {
            let resume = resume_failures < 3;
            resumes.push(resume);
            let st = SessionState { subscribed: false, got_data: false, resume_rejected: resume };
            count_session(&st, resume, &mut failures, &mut resume_failures);
        }
        assert_eq!(resumes, vec![true, true, true, false, false]);
        assert_eq!((failures, resume_failures), (5, 3));
    }
}
