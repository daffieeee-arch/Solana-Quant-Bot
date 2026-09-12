use of1_parquet_projection::{invalid, storage::materialize};
use std::{io, path::Path};
fn main() -> io::Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 3 {
        return Err(invalid(
            "usage: of1-parquet-projection INPUT_DIR EXECUTION_JSON_SHA256 NEW_OUTPUT_DIR",
        ));
    }
    let manifest = materialize(Path::new(&args[0]), &args[1], Path::new(&args[2]))?;
    println!(
        "{}",
        serde_json::to_string_pretty(&manifest).map_err(invalid)?
    );
    Ok(())
}
