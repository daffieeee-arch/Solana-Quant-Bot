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
//!                [--no-downscale] [--dry-run]
//!
//! The endpoint can also come from LAB_TRITON_GRPC_ENDPOINT; the token only from
//! LAB_TRITON_X_TOKEN (never logged). Exit code 75: the day's budget is spent.

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
use meter::{Meter, GB};
use recorder::{Action, Config, Recorder};
use segment::Segments;
use sink::Sink;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use watch::{Extra, Files, Filter, Opts};
use yellowstone_grpc_client::{ClientTlsConfig, GeyserGrpcClient, SubscribeRequestSink};
use yellowstone_grpc_proto::prelude::SubscribeUpdate;
use yellowstone_grpc_proto::tonic::codec::CompressionEncoding;
use yellowstone_grpc_proto::tonic::Status;

pub const EXIT_BUDGET: i32 = 75;
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
/// creation detection (--pool-creations or --new-reversed) no new pool is probed or followed.
fn creation_warning(a: &Args) -> Option<&'static str> {
    (!a.pool_creations && !a.new_reversed).then_some(
        "F7 coverage ~0% without creation detection: pass --pool-creations and/or --new-reversed \
         (new reversed pools are found only through them; graduations alone cover M1, not F7)",
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
    Disconnected(String, bool),
}

/// One connection: subscribe (resuming after the last closed slot when `resume`), then process
/// updates until the stream breaks, the watchdog fires, the budget stops us or we are told to stop.
async fn session(rec: &mut Recorder, a: &Args, files: &mut Files, endpoint: &str, resume: bool, deadline: Option<Instant>) -> Result<Exit> {
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
    log::info!("subscribing: {} from slot {:?}", rec.active.summary(), main_req.from_slot);
    let (mut sink, mut stream) = client.subscribe_with_request(Some(main_req)).await.context("subscribe")?;
    // The vault stream is its own subscription on the same connection (its own data slice).
    let (mut vsink, mut vstream) = client.subscribe_with_request(Some(vault_req)).await.context("subscribe vaults")?;
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    let mut last_main = Instant::now();
    let mut last_reload = Instant::now();
    let mut got_data = false;
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
                    got_data = true;
                    last_main = Instant::now();
                    rec.on_update(u, unix_ns())?;
                }
                Some(Err(status)) => return Ok(Exit::Disconnected(format!("main stream: {status}"), got_data)),
                None => return Ok(Exit::Disconnected("main stream ended".into(), got_data)),
            },
            m = vstream.next() => match m {
                Some(Ok(u)) => rec.on_update(u, unix_ns())?,
                Some(Err(status)) => return Ok(Exit::Disconnected(format!("vault stream: {status}"), got_data)),
                None => return Ok(Exit::Disconnected("vault stream ended".into(), got_data)),
            },
            _ = tick.tick() => {
                if last_main.elapsed() > Duration::from_secs(WATCHDOG_SECS) {
                    return Ok(Exit::Disconnected(format!("no update for {WATCHDOG_SECS} s"), got_data));
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
    let mut meter = Meter::load(&a.root, cap, a.wire_ratio, unix())?;
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
                "warnings": creation_warning(&a).into_iter().collect::<Vec<_>>(),
                "invalid_addresses": watch.invalid,
            }))?
        );
        return Ok(0);
    }
    if meter.stopped {
        log::error!("today's budget is spent ({:.2} GB of {:.2} GB); not starting before the next UTC day", meter.spent as f64 / GB, cap as f64 / GB);
        return Ok(EXIT_BUDGET);
    }
    if let Some(w) = creation_warning(&a) {
        log::warn!("{w}");
    }
    let endpoint = a.endpoint.clone().context("pass --endpoint or set LAB_TRITON_GRPC_ENDPOINT")?;
    let (segs, last) = Segments::open(&a.root, a.segment_slots, a.segment_secs)?;
    // The extractor's plugin decodes; its own Parquet sink stays unused (the segments write).
    let plugin = LabPlugin::new(Arc::new(Sink::start(a.root.join(".extractor-sink-unused"))), Arc::new(Coverage::new(0, 0)))?;
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
    let mut failures = 0u32;
    loop {
        // After three failed resumes the server probably no longer has those slots: continue
        // live; the gap shows in the segment's reconnects and in the reconciliation.
        let resume = failures < 3;
        let exit = match session(&mut rec, &a, &mut files, &endpoint, resume, deadline).await {
            Ok(e) => e,
            Err(e) => Exit::Disconnected(format!("{e:#}"), false),
        };
        match exit {
            Exit::Stop(reason) => {
                log::error!("daily budget reached: {reason}; stopped until the next UTC day");
                return Ok(EXIT_BUDGET);
            }
            Exit::Shutdown => {
                rec.close_segment(Some("shutdown"))?;
                log::info!("shut down after slot {:?}", rec.slots.last_closed);
                return Ok(0);
            }
            Exit::Disconnected(reason, got_data) => {
                failures = if got_data { 1 } else { failures + 1 };
                let note = if resume { reason } else { format!("{reason} (resumed without from_slot)") };
                log::warn!("disconnected: {note}");
                rec.on_disconnect(unix(), &note);
                if failures >= MAX_FAILURES {
                    rec.close_segment(Some("giving up"))?;
                    bail!("{failures} sessions in a row without data; giving up");
                }
                tokio::time::sleep(Duration::from_secs((1u64 << failures.min(6)).min(60))).await;
            }
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
