//! Read-only offline report. No acquisition, lease, writer lock or output publication.
use of1_range_recorder::bronze_preparation::{Limits, inspect_raw, render_html};
use std::{
    env,
    error::Error,
    io::{self, Write},
    path::Path,
};

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    if args.is_empty() || args.len() > 2 || args.get(1).is_some_and(|v| v != "--html") {
        return Err("usage: of1-bronze-evidence RAW_ROOT [--html]".into());
    }
    let report = inspect_raw(Path::new(&args[0]), Limits::default())?;
    let output = if args.len() == 2 {
        render_html(&report)?
    } else {
        serde_json::to_string_pretty(&report)? + "\n"
    };
    io::stdout().lock().write_all(output.as_bytes())?;
    Ok(())
}
