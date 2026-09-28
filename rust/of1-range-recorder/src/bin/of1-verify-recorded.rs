//! Standalone offline forensic verifier. Stdout only; never resume an old writer.
use of1_range_recorder::{
    raw_inspection_html,
    recorded_verification::{inspect_recorded, verify_recorded},
};
use std::{error::Error, path::Path};

fn run() -> Result<bool, Box<dyn Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if let Some(root) = args.first() {
        let run = of1_range_recorder::monitor::read_run_context(Path::new(root))?;
        if let Some(sample) = run
            .aggregate_plan
            .sample_identity
            .as_ref()
            .filter(|s| s.b7.is_some())
        {
            of1_range_recorder::campaign::development_processing_only(sample)?;
        }
    }
    let report = match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        [root, "--inspect-json" | "--inspect-html"] => inspect_recorded(Path::new(root))?,
        [root] => verify_recorded(Path::new(root), None)?,
        [root, "--prior-failure", failure, "--prior-run-result", result] => verify_recorded(Path::new(root), Some((Path::new(failure), Path::new(result))))?,
        _ => return Err("usage: of1-verify-recorded RUN_ROOT [--inspect-json | --inspect-html | --prior-failure FAILURE_JSON --prior-run-result RESULT_JSON]".into()),
    };
    if args.last().is_some_and(|a| a == "--inspect-html") {
        println!("{}", raw_inspection_html::render(&report)?);
    } else {
        println!("{}", serde_json::to_string_pretty(&report)?);
    }
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
