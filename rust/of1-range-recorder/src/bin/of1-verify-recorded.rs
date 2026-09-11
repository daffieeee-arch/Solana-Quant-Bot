//! Standalone offline forensic verifier. Stdout only; never resume an old writer.
use of1_range_recorder::recorded_verification::verify_recorded;
use std::{error::Error, path::Path};

fn run() -> Result<bool, Box<dyn Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let report = match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        [root] => verify_recorded(Path::new(root), None)?,
        [root, "--prior-failure", failure, "--prior-run-result", result] => verify_recorded(Path::new(root), Some((Path::new(failure), Path::new(result))))?,
        _ => return Err("usage: of1-verify-recorded RUN_ROOT [--prior-failure FAILURE_JSON --prior-run-result RESULT_JSON]".into()),
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(report["stages"]["car_slot"] != "QUARANTINED"
        && report["stages"]["car_slot"] != "INCOMPLETE")
}

fn main() {
    match run() {
        Ok(true) => {}
        Ok(false) => std::process::exit(2),
        Err(error) => {
            eprintln!(
                "OF1_RECORDED_VERIFICATION_STOP: {}",
                error.to_string().chars().take(512).collect::<String>()
            );
            std::process::exit(1);
        }
    }
}
