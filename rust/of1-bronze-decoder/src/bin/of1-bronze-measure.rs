//! Read-only measurement of the existing decoder and exact serialization.
use of1_bronze_decoder::{report, resources};
use of1_range_recorder::durable::acquisition::current_executable_sha256;
use std::{io, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if args.len() != 1 {
        return Err("usage: of1-bronze-measure RECORDED_RUN (offline only)".into());
    }
    let report = report::decode_run(&PathBuf::from(&args[0]))?;
    let mut measured = resources::measure(&report)?;
    measured["decoder_source_sha256"] = of1_bronze_decoder::source_sha256().into();
    measured["executable_sha256"] = current_executable_sha256()?.into();
    serde_json::to_writer_pretty(io::stdout().lock(), &measured)?;
    Ok(())
}
