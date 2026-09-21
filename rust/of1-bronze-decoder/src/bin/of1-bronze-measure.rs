//! Read-only measurement of the existing decoder and exact serialization.
use of1_bronze_decoder::{report, resources};
use of1_range_recorder::durable::acquisition::current_executable_sha256;
use std::{io, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if args.is_empty() || args.len() > 1 + report::MAX_SELECTION_SLOTS {
        return Err(
            "usage: of1-bronze-measure RECORDED_RUN [RECEIPT_SEQUENCE ...] (offline only)".into(),
        );
    }
    let root = PathBuf::from(&args[0]);
    let sequences = args[1..]
        .iter()
        .map(|v| {
            v.to_str()
                .ok_or("receipt encoding")?
                .parse::<u64>()
                .map_err(|_| "receipt integer")
        })
        .collect::<Result<Vec<_>, _>>()?;
    let report = if sequences.is_empty() {
        report::decode_run(&root)?
    } else {
        report::decode_receipts(&root, &sequences)?
    };
    let mut measured = resources::measure(&report)?;
    measured["decoder_source_sha256"] = of1_bronze_decoder::source_sha256().into();
    measured["executable_sha256"] = current_executable_sha256()?.into();
    serde_json::to_writer_pretty(io::stdout().lock(), &measured)?;
    Ok(())
}
