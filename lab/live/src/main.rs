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

#[allow(dead_code)]
#[path = "../../extractor/src/idl.rs"]
mod idl;
#[allow(dead_code)]
#[path = "../../extractor/src/sink.rs"]
mod sink;

mod journal;
mod mem;
mod rows;

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
        _ => bail!("usage: lab-live replay (--chunks-root DIR | --segments-root DIR) --from SLOT --to SLOT --journal DIR [--order-out FILE]"),
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

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let a = parse_args(&argv)?;
    replay(&a)
}

#[cfg(test)]
mod tests {
    use super::parse_args;

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
