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
    // Gate evaluation before record/quality inspection or diagnostic errors.
    let execution: serde_json::Value = serde_json::from_slice(
        &of1_range_recorder::acquisition::read_limited(
            &Path::new(&args[0]).join("execution.json"),
            1024 * 1024,
        )
        .map_err(invalid)?,
    )
    .map_err(invalid)?;
    if execution["sample_identity"]["b7"]["cohort_role"] == "RESERVED_EVALUATION" {
        let sample =
            serde_json::from_value(execution["sample_identity"].clone()).map_err(invalid)?;
        of1_range_recorder::campaign::Guard::output(
            &sample,
            Path::new(
                execution["run_root"]
                    .as_str()
                    .ok_or_else(|| invalid("SEALED_INPUT"))?,
            ),
            Path::new(&args[2]),
            execution["batch_binding"]["plan_sha256"]
                .as_str()
                .ok_or_else(|| invalid("SEALED_INPUT"))?,
            0,
        )
        .map_err(invalid)?;
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
