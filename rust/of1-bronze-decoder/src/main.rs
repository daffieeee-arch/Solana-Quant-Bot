//! Offline-only CLI. Output must be a NEW directory outside the preserved run.
use of1_bronze_decoder::report;
use of1_range_recorder::{durable::acquisition::current_executable_sha256, sha256};
use serde_json::json;
use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

fn write_new(path: &std::path::Path, bytes: &[u8]) -> io::Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if args.len() != 2 {
        return Err(
            "usage: of1-bronze-decoder RECORDED_RUN NEW_OUTPUT_DIRECTORY (offline only)".into(),
        );
    }
    let root = fs::canonicalize(&args[0])?;
    let output = PathBuf::from(&args[1]);
    let parent = fs::canonicalize(output.parent().ok_or("output parent required")?)?;
    let output = parent.join(output.file_name().ok_or("output name required")?);
    if output.starts_with(&root) || output.exists() {
        return Err("output must be new and outside preserved run".into());
    }
    let start = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis();
    let report = report::decode_run(&root)?;
    let quality = serde_json::to_vec_pretty(&report)?;
    let html = report::html(&report);
    let mut bronze = Vec::new();
    for record in report["records"].as_array().ok_or("records absent")? {
        serde_json::to_writer(&mut bronze, record)?;
        bronze.push(b'\n');
    }
    let mut silver = Vec::new();
    for fact in report["silver_records"]
        .as_array()
        .ok_or("silver records absent")?
    {
        serde_json::to_writer(&mut silver, fact)?;
        silver.push(b'\n');
    }
    let execution = json!({"schema":"OF1_BRONZE_EXECUTION_1","decoder_source_sha256":of1_bronze_decoder::source_sha256(),"processed_at_unix_ms":start.to_string(),"finished_at_unix_ms":SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis().to_string(),"operational_timestamps_are_not_features":true,"executable_sha256":current_executable_sha256()?,"quality_sha256":sha256(&quality),"bronze_jsonl_sha256":sha256(&bronze),"html_sha256":sha256(html.as_bytes()),"lock_sha256":sha256(include_bytes!("../Cargo.lock")),"run_root":root,"no_acquisition_or_writer_resume":true});
    let mut execution = execution;
    execution["silver_jsonl_sha256"] = json!(sha256(&silver));
    execution["silver_fact_count"] = json!(
        report["silver_records"]
            .as_array()
            .ok_or("silver records absent")?
            .len()
    );
    // Fail on an existing output. Partial output after a crash is explicit: the
    // publication marker is written LAST and never interpreted as resumable input.
    fs::create_dir(&output)?;
    write_new(&output.join("quality.json"), &quality)?;
    write_new(&output.join("bronze.jsonl"), &bronze)?;
    write_new(&output.join("silver.jsonl"), &silver)?;
    write_new(&output.join("quality.html"), html.as_bytes())?;
    write_new(
        &output.join("execution.json"),
        &serde_json::to_vec_pretty(&execution)?,
    )?;
    // Establish all artifact names before a durable completion marker can exist.
    fs::File::open(&output)?.sync_all()?;
    write_new(&output.join("COMPLETE"), sha256(&quality).as_bytes())?;
    fs::File::open(&output)?.sync_all()?;
    fs::File::open(&parent)?.sync_all()?;
    println!(
        "{}",
        serde_json::to_string_pretty(
            &json!({"output":output,"transaction_envelopes":report["transaction_envelopes"],"dispositions":report["dispositions"],"reasons":report["reasons"],"pump_program_involvement_transactions":report["pump_program_involvement_transactions"],"execution":execution})
        )?
    );
    Ok(())
}
