//! lab-extractor: stream a slot range from Old Faithful with Jetstreamer and write pump.fun and
//! PumpSwap events to Parquet.
//!
//! Usage: lab-extractor --out <dir> (--epoch <n> | --slots <start>:<end>) [--threads <n>]
//!
//! `<end>` is exclusive. The output directory receives one sub-directory per table and a
//! `_manifest.json` that is written last; a chunk without a manifest is incomplete.

mod extract;
mod idl;
mod sink;

use anyhow::{bail, Context, Result};
use extract::{Coverage, LabPlugin, PUMP_AMM_IDL, PUMP_IDL};
use sha2::{Digest, Sha256};
use sink::Sink;
use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Instant;

const SLOTS_PER_EPOCH: u64 = 432_000;
/// Commit of github.com/pump-fun/pump-public-docs that `idl/` was copied from.
const IDL_SOURCE_COMMIT: &str = "2293f9a66c654e9fe82dc5e8f4618538f24bb35f";
/// Keep in sync with the git rev pinned in Cargo.toml.
const JETSTREAMER_SOURCE: &str = "https://github.com/anza-xyz/jetstreamer@1f9d30600fb479acdd5eb956ea2e19455c903590";

struct Args {
    out: PathBuf,
    start: u64,
    end: u64,
    threads: usize,
}

fn parse_args() -> Result<Args> {
    let mut out = None;
    let mut range = None;
    let mut threads = 4usize;
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        let mut val = || it.next().with_context(|| format!("{a} needs a value"));
        match a.as_str() {
            "--out" => out = Some(PathBuf::from(val()?)),
            "--epoch" => {
                let e: u64 = val()?.parse()?;
                range = Some((e * SLOTS_PER_EPOCH, (e + 1) * SLOTS_PER_EPOCH));
            }
            "--slots" => {
                let v = val()?;
                let (s, e) = v.split_once(':').context("--slots expects <start>:<end>")?;
                range = Some((s.parse()?, e.parse()?));
            }
            "--threads" => threads = val()?.parse()?,
            other => bail!("unknown argument {other}"),
        }
    }
    let (start, end) = range.context("pass --epoch or --slots")?;
    if start >= end {
        bail!("empty slot range");
    }
    Ok(Args { out: out.context("pass --out")?, start, end, threads: threads.max(1) })
}

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let args = parse_args()?;
    if args.out.exists() && std::fs::read_dir(&args.out)?.next().is_some() {
        bail!("{} is not empty; refusing to mix files from another run", args.out.display());
    }
    std::fs::create_dir_all(&args.out)?;
    // ClickHouse is not used: an empty DSN disables it (and its helper process) entirely.
    std::env::set_var("JETSTREAMER_CLICKHOUSE_MODE", "off");

    let sink = Arc::new(Sink::start(args.out.clone()));
    let coverage = Arc::new(Coverage::new(args.start, args.end));
    let plugin = LabPlugin::new(sink.clone(), coverage.clone())?;
    let counters = plugin.counters.clone();
    let fatal = plugin.fatal.clone();

    let started = Instant::now();
    let started_at = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_secs();
    // Same wiring as jetstreamer's JetstreamerRunner, without its CLI/TUI/ClickHouse facade.
    let mut runner = jetstreamer_plugin::PluginRunner::new("", args.threads, false, false, None);
    runner.register(Box::new(plugin));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(args.threads * 4 + 4)
        .enable_all()
        .thread_name("lab-firehose")
        .build()?;
    let result = runtime.block_on(Arc::new(runner).run(args.start..args.end, false));
    let elapsed = started.elapsed().as_secs_f64();
    let tables = sink.finish();

    // The firehose does not report skipped slots at the edges of a thread's range. A slot the
    // epoch's slot index does not contain was skipped on chain, so it is not missing data.
    // Any other lookup failure keeps the slot missing.
    let (missing, verified_skipped) = runtime.block_on(async {
        let mut still_missing = Vec::new();
        let mut skipped = 0u64;
        for (a, b) in coverage.missing_ranges() {
            for slot in a..b {
                match jetstreamer_firehose::index::slot_to_range(slot).await {
                    Err(jetstreamer_firehose::index::SlotOffsetIndexError::SlotNotFound(..)) => skipped += 1,
                    _ => match still_missing.last_mut() {
                        Some((_, end)) if *end == slot => *end = slot + 1,
                        _ => still_missing.push((slot, slot + 1)),
                    },
                }
            }
        }
        (still_missing, skipped)
    });
    let missing_slots: u64 = missing.iter().map(|(a, b)| b - a).sum();
    let run_ok = result.is_ok();
    let sink_ok = tables.is_ok() && !fatal.load(Ordering::SeqCst);
    let status = if run_ok && sink_ok && missing_slots == 0 {
        "complete"
    } else if run_ok && sink_ok {
        "incomplete_coverage"
    } else {
        "failed"
    };
    let c = |a: &std::sync::atomic::AtomicU64| a.load(Ordering::Relaxed);
    let manifest = serde_json::json!({
        "status": status,
        "slot_start": args.start,
        "slot_end_exclusive": args.end,
        "threads": args.threads,
        "started_at_unix": started_at,
        "elapsed_seconds": elapsed,
        "jetstreamer": JETSTREAMER_SOURCE,
        "extractor_sha256": std::fs::read("/proc/self/exe").map(|b| hex::encode(Sha256::digest(b))).ok(),
        "idl_source": format!("https://github.com/pump-fun/pump-public-docs/tree/{IDL_SOURCE_COMMIT}/idl"),
        "idl_sha256": {
            "pump.json": hex::encode(Sha256::digest(PUMP_IDL.as_bytes())),
            "pump_amm.json": hex::encode(Sha256::digest(PUMP_AMM_IDL.as_bytes())),
        },
        "error": result.as_ref().err().map(|e| e.to_string()),
        "sink_error": tables.as_ref().err().map(|e| format!("{e:#}")),
        "rows": tables.as_ref().ok(),
        "counters": {
            "txs_seen": c(&counters.txs_seen),
            "txs_touching": c(&counters.txs_touching),
            "txs_failed_touching": c(&counters.txs_failed_touching),
            "events": c(&counters.events),
            "anomalies": c(&counters.anomalies),
            "blocks": c(&counters.blocks),
            "skipped_markers": c(&counters.skipped_markers),
            "sink_errors": c(&counters.sink_errors),
            "firehose_errors": c(&counters.firehose_errors),
            "http_429": c(&counters.http_429),
            "missing_meta": c(&counters.missing_meta),
        },
        "coverage": {
            "missing_slots": missing_slots,
            "skipped_slots_verified_by_index": verified_skipped,
            "missing_ranges": missing.iter().take(50).map(|(a, b)| [a, b]).collect::<Vec<_>>(),
        },
    });
    let tmp = args.out.join("._manifest.json.tmp");
    {
        let mut f = std::fs::File::create(&tmp)?;
        std::io::Write::write_all(&mut f, &serde_json::to_vec_pretty(&manifest)?)?;
        f.sync_all()?;
    }
    std::fs::rename(&tmp, args.out.join("_manifest.json"))?;
    std::fs::File::open(&args.out)?.sync_all()?;
    println!("{}", serde_json::to_string(&manifest)?);
    match status {
        "complete" => Ok(()),
        "incomplete_coverage" => std::process::exit(3),
        _ => bail!("chunk failed"),
    }
}
