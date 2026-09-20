use of1_parquet_projection::{
    invalid,
    shards::{ShardLimits, materialize},
};
use std::{io, path::Path};
fn main() -> io::Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 3 && args.len() != 4 {
        return Err(invalid(
            "usage: of1-parquet-projection INPUT_DIR EXECUTION_JSON_SHA256 NEW_OUTPUT_DIR [ROWS_PER_FILE<=5000]",
        ));
    }
    let mut limits = ShardLimits::default();
    if let Some(rows) = args.get(3) {
        limits.rows = rows.parse().map_err(invalid)?;
    }
    let manifest = materialize(Path::new(&args[0]), &args[1], Path::new(&args[2]), limits)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&manifest).map_err(invalid)?
    );
    Ok(())
}
