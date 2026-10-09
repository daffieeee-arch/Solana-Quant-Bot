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
    if args.out.join("_manifest.json").exists() {
        bail!("{} already has a manifest; refusing to overwrite a finished chunk", args.out.display());
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

    let missing = coverage.missing_ranges();
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
        "jetstreamer_version": "0.7.0",
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
        },
        "coverage": {
            "missing_slots": missing_slots,
            "missing_ranges": missing.iter().take(50).map(|(a, b)| [a, b]).collect::<Vec<_>>(),
        },
    });
    let tmp = args.out.join("._manifest.json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(&manifest)?)?;
    std::fs::rename(&tmp, args.out.join("_manifest.json"))?;
    println!("{}", serde_json::to_string(&manifest)?);
    if status == "failed" {
        bail!("chunk failed");
    }
    Ok(())
}
