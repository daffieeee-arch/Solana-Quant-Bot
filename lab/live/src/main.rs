//! lab-live: the Rust live route, paper only (lab/docs/rust-live-route-design-2026-10-16.md). No keys, no signing,
//! no sending: nothing in this binary builds or sends a transaction.
//!
//! M1 part 1 is the replay skeleton without decisions:
//!
//!   lab-live replay (--chunks-root DIR | --segments-root DIR) --from SLOT --to SLOT --journal DIR [--order-out FILE]
//!
//! - It reads the extractor's event rows of the OF1 chunks (or recorder segments) in [from, to), in the Python
//!   replay's order (rows.rs).
//! - It writes a paper journal (journal.rs) with the state `feed`, `lab_live_rows` (rows per table, parts,
//!   duplicate keys, and key ties: rows the Python side, which orders by (slot, tx, outer, inner) only, could put in
//!   another order; parity needs 0) and `lab_live_measure` (the time to read and sort, and the peak RSS: every row
//!   is held in memory before the sort). One dev chunk (216,000 slots, 15.5 M event rows) does not fit the NAS
//!   runner's 24 GB that way (job 99u, killed after 33 s): streaming (part by part, compact legs) is a precondition for
//!   M1 parity on whole chunks, not an optimisation (design §10). The read logs its progress with the RSS per table and
//!   per million rows.
//! - `--order-out` writes one line per row, `table slot tx_index outer_ix inner_ix event_seq sha256` (tab
//!   separated, `null` for a missing value). paper/tests/test_lab_live.py compares it with DuckDB's reading of the
//!   same files.
//!
//! M1 part 3, the Rust half: the store snapshot without DuckDB (snapshot.rs, the export of paper/snapshot_export.py):
//!
//!   lab-live snapshot --dir DIR [--before SLOT] [--first-slot SLOT --h24 SLOTS] --out FILE
//!
//! - It loads the export as LiveFeed loads the store (with `--before`, what a live start there knew), and with a first
//!   live slot the farmer-history window of that start.
//! - `--out` gets the snapshot as sorted lines; paper/tests/test_lab_live_snapshot.py compares them with Python's
//!   own load of the same store.
//!
//! M1 C1a, the engine's event stream from the chunks (legs.rs, legs_read.rs; spec research/m1-c1a-legs-spec-2026-10-19.md):
//!
//!   lab-live legs --chunks-root DIR --snapshot DIR --from SLOT --to SLOT --summary FILE [--out FILE [--out-from SLOT]] [--window N]
//!
//! - Every event paper.feed.ReplayFeed hands the engine over [from, to), store-exact (admission by the snapshot, the
//!   store's casts and farmer flag), built from the chunk rows instead of the store's tables.
//! - `--summary` gets the count and sha256 of the lines, per kind and per slot window (as paper/legs_dump.py writes it),
//!   plus each chunk's h24, the kept and dropped rows per table and the peak RSS; `--out` the lines themselves (with
//!   `--out-from`, those of the slots from there on: a window that differs, in a run from the store's start).
//!   paper/tests/test_lab_live_legs.py compares them with paper.legs_dump on the same store.
//!
//! M1 C1b, the engine's F7 signals (signals.rs, signals_core.rs; lab_core's signal state; spec
//! research/m1-c1b-signals-spec-2026-10-19.md):
//!
//!   lab-live signals --chunks-root DIR --snapshot DIR --from SLOT --to SLOT --groups FILE --journal DIR
//!                    [--dev-bounds LO HI] [--summary FILE] [--trace FILE]
//!
//! - The same events as `legs` (now handed out typed: lab_core::legs::Event), through the engine's signal state, into a
//!   paper journal: its signals rows up to the farm gates (a row past them has no status yet: busy and risk are C2/C3),
//!   state 'groups' (the --groups text, normally the Python journal's own), 'feed' and 'lab_live_signals' (the summary).
//! - The dev bounds and the clock are the snapshot's (meta_bounds, ms_per_slot); `--dev-bounds` replaces the bounds.
//! - paper/tests/test_lab_live_signals.py runs paper.engine on the same range and compares the two journals with
//!   `paper.journal_diff --scope signals` (lab/live/nas_m1b.sh: the dev range of dev2 and the whole of fwd1053); the shared
//!   golden cases (paper/tests/data/c1b_signal_cases.json) run in signals_core.rs's tests.
//!
//! M1 C2-5, the curve seed before a replay's from (curve_hold.rs; spec research/m1-c2-positionsim-spec-2026-10-19.md §4.1):
//! curve_hold.parquet of paper.snapshot_export, read per (mint, trader) and checked against the replay's from and to. No
//! command uses it yet; `lab-live paper` (C2-4b) seeds its curve positions with it.

#[allow(dead_code)]
#[path = "../../extractor/src/idl.rs"]
mod idl;
#[allow(dead_code)]
#[path = "../../extractor/src/sink.rs"]
mod sink;

// `lab-live paper` reads it; its lookups (`of`, `net`) serve the tests and the seed proof's debugging.
#[allow(dead_code)]
mod curve_hold;
mod journal;
mod legs;
mod legs_read;
mod mem;
mod memlog;
mod paper;
mod paper_core;
mod risk;
mod rows;
mod signals;
mod signals_core;
mod snapshot;

/// The live heap for `--mem-log` (mem.rs; M1 C4-2): System, counted.
#[global_allocator]
static ALLOC: mem::Counting = mem::Counting;

use anyhow::{bail, Context, Result};
use std::io::Write;
use std::path::PathBuf;

struct Args {
    chunks_root: Option<PathBuf>,
    segments_root: Option<PathBuf>,
    from: u64,
    to: u64,
    journal: PathBuf,
    order_out: Option<PathBuf>,
}

fn parse_args(argv: &[String]) -> Result<Args> {
    let mut it = argv.iter();
    match it.next().map(String::as_str) {
        Some("replay") => {}
        Some("--version") => {
            println!("lab-live {}", env!("CARGO_PKG_VERSION"));
            std::process::exit(0);
        }
        _ => bail!("usage: lab-live replay (--chunks-root DIR | --segments-root DIR) --from SLOT --to SLOT --journal DIR [--order-out FILE]\n       \
                    lab-live snapshot --dir DIR [--before SLOT] [--first-slot SLOT --h24 SLOTS] --out FILE\n       \
                    lab-live legs --chunks-root DIR --snapshot DIR --from SLOT --to SLOT --summary FILE [--out FILE [--out-from SLOT]] [--window N]\n       \
                    lab-live signals --chunks-root DIR --snapshot DIR --from SLOT --to SLOT --groups FILE --journal DIR [--dev-bounds LO HI] \
                    [--summary FILE] [--trace FILE]\n       \
                    lab-live paper (--chunks-root DIR | --segments DIR) --snapshot DIR --from SLOT --to SLOT --groups FILE --variants SPEC[,SPEC...] \
                    --cost-scenario FILE [--ignore-risk] --journal DIR [--summary FILE] [--trace FILE] [--mem-log FILE]"),
    }
    let (mut chunks_root, mut segments_root, mut from, mut to, mut journal, mut order_out) = (None, None, None, None, None, None);
    while let Some(flag) = it.next() {
        let mut val = || it.next().cloned().with_context(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--chunks-root" => chunks_root = Some(PathBuf::from(val()?)),
            "--segments-root" => segments_root = Some(PathBuf::from(val()?)),
            "--from" => from = Some(val()?.parse::<u64>().context("--from")?),
            "--to" => to = Some(val()?.parse::<u64>().context("--to")?),
            "--journal" => journal = Some(PathBuf::from(val()?)),
            "--order-out" => order_out = Some(PathBuf::from(val()?)),
            other => bail!("unknown argument {other}"),
        }
    }
    if chunks_root.is_some() == segments_root.is_some() {
        bail!("give exactly one of --chunks-root and --segments-root");
    }
    let (from, to) = (from.context("--from")?, to.context("--to")?);
    if from >= to {
        bail!("--from must be below --to");
    }
    Ok(Args { chunks_root, segments_root, from, to, journal: journal.context("--journal")?, order_out })
}

fn field(v: Option<u64>) -> String {
    v.map_or_else(|| "null".to_owned(), |x| x.to_string())
}

fn replay(a: &Args) -> Result<()> {
    let (source, root, parts) = match (&a.chunks_root, &a.segments_root) {
        (Some(r), _) => ("chunks", r, rows::chunks(r)?),
        (_, Some(r)) => ("segments", r, rows::segments(r)?),
        _ => unreachable!("checked in parse_args"),
    };
    let t0 = std::time::Instant::now();
    let (events, stats) = rows::read_rows(&parts, a.from, a.to, &rows::EVENT_PREFIXES)?;
    let (read_secs, peak) = (t0.elapsed().as_secs_f64(), mem::peak_rss_bytes()); // read and sorted
    log::info!("{} rows from {} parts read and sorted in {:.1} s, peak RSS {} MB, key ties {}, duplicate keys {}", events.len(),
               stats.parts, read_secs, mem::mb(peak), stats.key_ties, stats.duplicate_keys);
    if let Some(path) = &a.order_out {
        let mut f = std::io::BufWriter::new(std::fs::File::create(path).with_context(|| format!("create {}", path.display()))?);
        for r in &events {
            writeln!(f, "{}\t{}\t{}\t{}\t{}\t{}\t{}", r.table, field(r.u64("slot")), field(r.u64("tx_index")), field(r.u64("outer_ix")),
                     field(r.u64("inner_ix")), field(r.u64("event_seq")), r.digest())?;
        }
        f.flush()?;
    }
    let mut j = journal::Journal::open(&a.journal)?;
    j.state("feed", &serde_json::json!({"kind": "replay", "engine": "lab-live", "source": source, "input": root, "from": a.from, "to": a.to}).to_string())?;
    j.state("lab_live_rows", &serde_json::json!({"parts": stats.parts, "rows": stats.rows, "duplicate_keys": stats.duplicate_keys,
                                                  "key_ties": stats.key_ties}).to_string())?;
    j.state("lab_live_measure", &serde_json::json!({"rows": events.len(), "read_secs": read_secs, "peak_rss_bytes": peak}).to_string())?;
    j.close()?;
    Ok(())
}

struct SnapArgs {
    dir: PathBuf,
    before: Option<u64>,
    window: Option<(u64, u64)>,
    out: PathBuf,
}

fn parse_snapshot_args(argv: &[String]) -> Result<SnapArgs> {
    let mut it = argv.iter();
    let (mut dir, mut before, mut first, mut h24, mut out) = (None, None, None, None, None);
    while let Some(flag) = it.next() {
        let mut val = || it.next().cloned().with_context(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--dir" => dir = Some(PathBuf::from(val()?)),
            "--before" => before = Some(val()?.parse::<u64>().context("--before")?),
            "--first-slot" => first = Some(val()?.parse::<u64>().context("--first-slot")?),
            "--h24" => h24 = Some(val()?.parse::<u64>().context("--h24")?),
            "--out" => out = Some(PathBuf::from(val()?)),
            other => bail!("unknown argument {other}"),
        }
    }
    let window = match (first, h24) {
        (Some(f), Some(h)) => Some((f, h)),
        (None, None) => None,
        _ => bail!("give --first-slot and --h24 together"),
    };
    if let (Some(b), Some((f, _))) = (before, window) {
        if f > b {
            bail!("--first-slot {f} is after --before {b}: a live start knows the store only up to its first slot");
        }
    }
    Ok(SnapArgs { dir: dir.context("--dir")?, before, window, out: out.context("--out")? })
}

fn snapshot_cmd(a: &SnapArgs) -> Result<()> {
    let s = snapshot::Snapshot::load(&a.dir, a.before)?;
    let w = a.window.map(|(first, h24)| s.seed_window(first, h24)).transpose()?;
    log::info!("snapshot {}: {} tokens, {} pools ({} SOL markets), {} pools with seed trades ({} trades, {} traders), peak RSS {} MB",
               a.dir.display(), s.tokens.len(), s.pool_class.len(), s.pools.len(), s.seed.len(),
               s.seed.values().map(Vec::len).sum::<usize>(), s.traders.len(), mem::mb(mem::peak_rss_bytes()));
    let mut f = std::io::BufWriter::new(std::fs::File::create(&a.out).with_context(|| format!("create {}", a.out.display()))?);
    for line in snapshot::dump(&s, w.as_ref()) {
        writeln!(f, "{line}")?;
    }
    f.flush()?;
    Ok(())
}

fn parse_legs_args(argv: &[String]) -> Result<legs_read::LegsArgs> {
    let mut it = argv.iter();
    let (mut chunks_root, mut snapshot, mut from, mut to, mut summary, mut out) = (None, None, None, None, None, None);
    let (mut window, mut out_from) = (10_000, 0);
    while let Some(flag) = it.next() {
        let mut val = || it.next().cloned().with_context(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--chunks-root" => chunks_root = Some(PathBuf::from(val()?)),
            "--snapshot" => snapshot = Some(PathBuf::from(val()?)),
            "--from" => from = Some(val()?.parse::<u64>().context("--from")?),
            "--to" => to = Some(val()?.parse::<u64>().context("--to")?),
            "--summary" => summary = Some(PathBuf::from(val()?)),
            "--out" => out = Some(PathBuf::from(val()?)),
            "--window" => window = val()?.parse::<u64>().context("--window")?,
            "--out-from" => out_from = val()?.parse::<u64>().context("--out-from")?,
            other => bail!("unknown argument {other}"),
        }
    }
    let (from, to) = (from.context("--from")?, to.context("--to")?);
    if from >= to || window == 0 {
        bail!("--from must be below --to, and --window above 0");
    }
    Ok(legs_read::LegsArgs {
        chunks_root: chunks_root.context("--chunks-root")?, snapshot: snapshot.context("--snapshot")?, from, to, window,
        summary: summary.context("--summary")?, out, out_from,
    })
}

fn legs_cmd(a: &legs_read::LegsArgs) -> Result<()> {
    let t0 = std::time::Instant::now();
    let mut v = legs_read::run(a)?;
    v["secs"] = serde_json::json!(t0.elapsed().as_secs_f64());
    std::fs::write(&a.summary, serde_json::to_string_pretty(&v)?).with_context(|| format!("write {}", a.summary.display()))?;
    log::info!("legs [{}, {}): {} events in {:.1} s, sha256 {}, peak RSS {} MB", a.from, a.to, v["events"], t0.elapsed().as_secs_f64(),
               v["sha256"].as_str().unwrap_or("?"), mem::mb(mem::peak_rss_bytes()));
    Ok(())
}

fn parse_signals_args(argv: &[String]) -> Result<signals::SignalsArgs> {
    let mut it = argv.iter();
    let (mut chunks_root, mut snapshot, mut from, mut to, mut groups, mut journal) = (None, None, None, None, None, None);
    let (mut dev_bounds, mut summary, mut trace) = (None, None, None);
    while let Some(flag) = it.next() {
        let mut val = || it.next().cloned().with_context(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--chunks-root" => chunks_root = Some(PathBuf::from(val()?)),
            "--snapshot" => snapshot = Some(PathBuf::from(val()?)),
            "--from" => from = Some(val()?.parse::<u64>().context("--from")?),
            "--to" => to = Some(val()?.parse::<u64>().context("--to")?),
            "--groups" => groups = Some(PathBuf::from(val()?)),
            "--journal" => journal = Some(PathBuf::from(val()?)),
            "--dev-bounds" => {
                let lo = val()?.parse::<u64>().context("--dev-bounds LO")?;
                dev_bounds = Some((lo, val()?.parse::<u64>().context("--dev-bounds HI")?));
            }
            "--summary" => summary = Some(PathBuf::from(val()?)),
            "--trace" => trace = Some(PathBuf::from(val()?)),
            other => bail!("unknown argument {other}"),
        }
    }
    let (from, to) = (from.context("--from")?, to.context("--to")?);
    if from >= to {
        bail!("--from must be below --to");
    }
    if dev_bounds.is_some_and(|(lo, hi)| lo >= hi) {
        bail!("--dev-bounds LO HI needs LO below HI");
    }
    Ok(signals::SignalsArgs {
        chunks_root: chunks_root.context("--chunks-root")?, snapshot: snapshot.context("--snapshot")?, from, to,
        groups: groups.context("--groups")?, journal: journal.context("--journal")?, dev_bounds, summary, trace,
    })
}

fn parse_paper_args(argv: &[String]) -> Result<paper::PaperArgs> {
    let mut it = argv.iter();
    let (mut chunks_root, mut snapshot, mut from, mut to, mut groups, mut journal) = (None, None, None, None, None, None);
    let (mut variants, mut cost_scenario, mut ignore_risk, mut summary, mut trace, mut segments) = (None, None, false, None, None, None);
    let mut mem_log = None;
    while let Some(flag) = it.next() {
        let mut val = || it.next().cloned().with_context(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--chunks-root" => chunks_root = Some(PathBuf::from(val()?)),
            "--segments" => segments = Some(PathBuf::from(val()?)),
            "--snapshot" => snapshot = Some(PathBuf::from(val()?)),
            "--from" => from = Some(val()?.parse::<u64>().context("--from")?),
            "--to" => to = Some(val()?.parse::<u64>().context("--to")?),
            "--groups" => groups = Some(PathBuf::from(val()?)),
            // paper.engine's own form: specs separated by commas (engine.py:1467)
            "--variants" => variants = Some(val()?.split(',').filter(|v| !v.is_empty()).map(str::to_owned).collect::<Vec<_>>()),
            "--cost-scenario" => cost_scenario = Some(PathBuf::from(val()?)),
            "--ignore-risk" => ignore_risk = true,
            "--journal" => journal = Some(PathBuf::from(val()?)),
            "--summary" => summary = Some(PathBuf::from(val()?)),
            "--trace" => trace = Some(PathBuf::from(val()?)),
            "--mem-log" => mem_log = Some(PathBuf::from(val()?)),
            other => bail!("unknown argument {other}"),
        }
    }
    let (from, to) = (from.context("--from")?, to.context("--to")?);
    if from >= to {
        bail!("--from must be below --to");
    }
    let variants = variants.filter(|v| !v.is_empty()).context("--variants: the primary group's specs (paper.engine's default F7 grid is M4)")?;
    let chunks_root = match (chunks_root, &segments) {
        (Some(_), Some(_)) => bail!("--chunks-root or --segments, not both"),
        (Some(c), None) => c,
        (None, Some(_)) => PathBuf::new(),
        (None, None) => bail!("--chunks-root or --segments"),
    };
    Ok(paper::PaperArgs {
        chunks_root, segments, snapshot: snapshot.context("--snapshot")?, from, to,
        groups: groups.context("--groups")?, variants, cost_scenario: cost_scenario.context("--cost-scenario")?, ignore_risk,
        journal: journal.context("--journal")?, summary, trace, mem_log,
    })
}

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let argv: Vec<String> = std::env::args().skip(1).collect();
    match argv.first().map(String::as_str) {
        Some("snapshot") => return snapshot_cmd(&parse_snapshot_args(&argv[1..])?),
        Some("legs") => return legs_cmd(&parse_legs_args(&argv[1..])?),
        Some("signals") => return signals::run(&parse_signals_args(&argv[1..])?).map(|_| ()),
        Some("paper") => return paper::run(&parse_paper_args(&argv[1..])?).map(|_| ()),
        _ => {}
    }
    let a = parse_args(&argv)?;
    replay(&a)
}

#[cfg(test)]
mod tests {
    use super::{parse_args, parse_legs_args, parse_paper_args, parse_signals_args, parse_snapshot_args};

    #[test]
    fn the_paper_command_takes_its_inputs_the_variants_and_the_costs() {
        let base = "--chunks-root /c --snapshot /s --from 10 --to 20 --groups /g --variants 0.04:600,flow300_H6 --cost-scenario /k --journal /j";
        let a = parse_paper_args(&args(&format!("{base} --ignore-risk --summary /m --trace /t --mem-log /l"))).unwrap();
        assert_eq!((a.from, a.to, a.variants.clone(), a.ignore_risk, a.summary.is_some(), a.trace.is_some()),
                   (10, 20, vec!["0.04:600".to_owned(), "flow300_H6".to_owned()], true, true, true));
        assert_eq!((a.mem_log.as_deref(), parse_paper_args(&args(base)).unwrap().mem_log), (Some(std::path::Path::new("/l")), None));
        assert!(!parse_paper_args(&args(base)).unwrap().ignore_risk); // risk on (C3)
        // the recorder's segments instead of the chunks (C4-1): one of the two, never both
        let seg = parse_paper_args(&args(&base.replace("--chunks-root /c", "--segments /seg"))).unwrap();
        assert_eq!(seg.segments.as_deref(), Some(std::path::Path::new("/seg")));
        assert!(parse_paper_args(&args(&format!("{base} --segments /seg"))).err().is_some_and(|e| e.to_string().contains("not both")));
        assert!(parse_paper_args(&args(&base.replace("--chunks-root /c ", ""))).is_err());
        for bad in [base.replace("--variants 0.04:600,flow300_H6 ", ""), base.replace("0.04:600,flow300_H6", ","),
                    base.replace("--cost-scenario /k ", ""), base.replace("--groups /g ", ""), base.replace("--to 20", "--to 10"),
                    format!("{base} --dev-bounds 0 5")] {
            assert!(parse_paper_args(&args(&bad)).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_signals_command_takes_its_inputs_a_range_the_groups_and_a_journal() {
        let base = "--chunks-root /c --snapshot /s --from 10 --to 20 --groups /g --journal /j";
        let a = parse_signals_args(&args(&format!("{base} --dev-bounds 0 9223372036854775807 --summary /m --trace /t"))).unwrap();
        assert_eq!((a.from, a.to, a.dev_bounds, a.summary.is_some(), a.trace.is_some()), (10, 20, Some((0, (1 << 63) - 1)), true, true));
        let a = parse_signals_args(&args(base)).unwrap();
        assert_eq!((a.dev_bounds, a.summary, a.trace), (None, None, None));
        for bad in ["--snapshot /s --from 1 --to 2 --groups /g --journal /j", "--chunks-root /c --from 1 --to 2 --groups /g --journal /j",
                    "--chunks-root /c --snapshot /s --from 1 --to 2 --journal /j", "--chunks-root /c --snapshot /s --from 1 --to 2 --groups /g",
                    "--chunks-root /c --snapshot /s --from 2 --to 2 --groups /g --journal /j",
                    "--chunks-root /c --snapshot /s --from 1 --to 2 --groups /g --journal /j --dev-bounds 5",
                    "--chunks-root /c --snapshot /s --from 1 --to 2 --groups /g --journal /j --dev-bounds 5 5",
                    "--chunks-root /c --snapshot /s --from 1 --to 2 --groups /g --journal /j --out /o"] {
            assert!(parse_signals_args(&args(bad)).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_legs_command_takes_its_inputs_a_range_and_a_summary() {
        let a = parse_legs_args(&args("--chunks-root /c --snapshot /s --from 10 --to 20 --summary /m --out /o --out-from 15 --window 5")).unwrap();
        assert_eq!((a.from, a.to, a.window, a.out.is_some(), a.out_from), (10, 20, 5, true, 15));
        assert_eq!(parse_legs_args(&args("--chunks-root /c --snapshot /s --from 10 --to 20 --summary /m")).unwrap().window, 10_000);
        for bad in ["--snapshot /s --from 1 --to 2 --summary /m", "--chunks-root /c --from 1 --to 2 --summary /m",
                    "--chunks-root /c --snapshot /s --from 2 --to 2 --summary /m", "--chunks-root /c --snapshot /s --from 1 --to 2",
                    "--chunks-root /c --snapshot /s --from 1 --to 2 --summary /m --window 0"] {
            assert!(parse_legs_args(&args(bad)).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_snapshot_command_takes_a_dir_an_out_and_a_whole_window() {
        let a = parse_snapshot_args(&args("--dir /s --before 9 --first-slot 7 --h24 3 --out /o")).unwrap();
        assert_eq!((a.before, a.window), (Some(9), Some((7, 3))));
        assert!(parse_snapshot_args(&args("--dir /s --out /o")).unwrap().window.is_none());
        assert!(parse_snapshot_args(&args("--dir /s --before 9 --first-slot 9 --h24 3 --out /o")).is_ok());
        for bad in ["--out /o", "--dir /s", "--dir /s --out /o --first-slot 9", "--dir /s --out /o --h24 3", "--dir /s --out /o --x 1",
                    "--dir /s --before 8 --first-slot 9 --h24 3 --out /o"] {
            assert!(parse_snapshot_args(&args(bad)).is_err(), "{bad}");
        }
    }

    fn args(s: &str) -> Vec<String> {
        s.split_whitespace().map(str::to_owned).collect()
    }

    #[test]
    fn the_command_line_takes_one_input_and_a_range() {
        let a = parse_args(&args("replay --chunks-root /c --from 10 --to 20 --journal /j --order-out /o")).unwrap();
        assert_eq!((a.from, a.to, a.chunks_root.is_some(), a.segments_root.is_none()), (10, 20, true, true));
        for bad in ["replay --from 1 --to 2 --journal /j", "replay --chunks-root /c --segments-root /s --from 1 --to 2 --journal /j",
                    "replay --chunks-root /c --from 5 --to 5 --journal /j", "replay --chunks-root /c --from 1 --to 2", "decide",
                    "replay --chunks-root /c --from x --to 2 --journal /j"] {
            assert!(parse_args(&args(bad)).is_err(), "{bad}");
        }
    }
}
