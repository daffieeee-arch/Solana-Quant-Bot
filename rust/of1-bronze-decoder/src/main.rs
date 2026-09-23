//! Offline-only CLI. Output must be a NEW directory outside the preserved run.
use of1_bronze_decoder::{delivery::DeliveryOrder, report, resources};
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
    if args.len() != 2 && args.len() != 3 {
        return Err(
            "usage: of1-bronze-decoder RECORDED_RUN NEW_OUTPUT_DIRECTORY [canonical|reverse|odd-even] (offline only)".into(),
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
    let order = DeliveryOrder::parse(
        args.get(2)
            .map_or(Some("canonical"), |v| v.to_str())
            .ok_or("delivery order must be UTF-8")?,
    )?;
    let (report, delivery) = report::decode_run_with_delivery(&root, order)?;
    let quality = resources::bounded_json(&report, "QUALITY_JSON", resources::MAX_QUALITY_BYTES)?;
    let html = report::html(&report);
    resources::check_size("QUALITY_HTML", html.len(), resources::MAX_HTML_BYTES)?;
    let bronze = resources::bounded_jsonl(
        report["records"].as_array().ok_or("records absent")?,
        resources::MAX_JSONL_BYTES,
    )?;
    let silver = resources::bounded_jsonl(
        report["silver_records"]
            .as_array()
            .ok_or("silver records absent")?,
        resources::MAX_JSONL_BYTES,
    )?;
    let execution = json!({"schema":"OF1_BRONZE_EXECUTION_1","decoder_source_sha256":of1_bronze_decoder::source_sha256(),"processed_at_unix_ms":start.to_string(),"finished_at_unix_ms":SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis().to_string(),"operational_timestamps_are_not_features":true,"executable_sha256":current_executable_sha256()?,"quality_sha256":sha256(&quality),"bronze_jsonl_sha256":sha256(&bronze),"html_sha256":sha256(html.as_bytes()),"lock_sha256":sha256(include_bytes!("../Cargo.lock")),"run_root":root,"no_acquisition_or_writer_resume":true});
    let mut execution = execution;
    // The explicit diagnostic mode shares existing publication caps. Ordinary
    // two-argument runs keep their compact execution receipt.
    if args.len() == 3 {
        execution["native_delivery"] = delivery;
    }
    if let Some(sample) = report.get("sample_identity") {
        execution["sample_identity"] = sample.clone();
        execution["slice_class"] = report["slice_class"].clone();
    }
    execution["silver_jsonl_sha256"] = json!(sha256(&silver));
    execution["silver_fact_count"] = json!(
        report["silver_records"]
            .as_array()
            .ok_or("silver records absent")?
            .len()
    );
    let execution_bytes =
        resources::bounded_json(&execution, "EXECUTION_JSON", resources::MAX_EXECUTION_BYTES)?;
    resources::publication_bytes(
        &[
            quality.len(),
            bronze.len(),
            silver.len(),
            html.len(),
            execution_bytes.len(),
        ],
        resources::MAX_PUBLICATION_BYTES,
    )?;
    // Fail on an existing output. Partial output after a crash is explicit: the
    // publication marker is written LAST and never interpreted as resumable input.
    fs::create_dir(&output)?;
    write_new(&output.join("quality.json"), &quality)?;
    write_new(&output.join("bronze.jsonl"), &bronze)?;
    write_new(&output.join("silver.jsonl"), &silver)?;
    write_new(&output.join("quality.html"), html.as_bytes())?;
    write_new(&output.join("execution.json"), &execution_bytes)?;
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
