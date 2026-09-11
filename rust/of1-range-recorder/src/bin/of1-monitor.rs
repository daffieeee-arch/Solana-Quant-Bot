//! Read-only evidence import and local operational telemetry relay. No transport
//! constructor, lease admission, resume, provider endpoint or acquisition command.
use of1_range_recorder::monitor::{read_run, run_relay, write_snapshot};
use std::{error::Error, path::Path};

fn run() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["import", root, output] => {
            let snapshot = read_run(Path::new(root))?;
            let path = write_snapshot(&snapshot, Path::new(output))?;
            println!("{}", serde_json::json!({"schema":"OF1_MONITOR_IMPORT_1","snapshot":path,"id":snapshot.id,"kind":snapshot.kind,"mode":"RECORDED","published_bytes":snapshot.selection.published_bytes,"provider_calls":0,"run_modified":false}));
            Ok(())
        }
        ["relay", socket, output] => Ok(run_relay(Path::new(socket), Path::new(output))?),
        _ => Err("usage: of1-monitor import RUN_ROOT SNAPSHOT_DIRECTORY | relay UNIX_SOCKET SNAPSHOT_DIRECTORY".into()),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!(
            "OF1_MONITOR_STOP: {}",
            error.to_string().chars().take(256).collect::<String>()
        );
        std::process::exit(1);
    }
}
