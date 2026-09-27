//! Offline bounded worker; no transport or writer resume.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if args.len() != 3 && args.len() != 4 {
        return Err("usage: of1-bronze-batch PLAN BATCH_ID OUTPUT [PART_ORDINAL]".into());
    }
    let result = if let Some(part) = args.get(3) {
        of1_bronze_decoder::batch::execute_part(
            std::path::Path::new(&args[0]),
            args[1].to_str().ok_or("batch id")?,
            std::path::Path::new(&args[2]),
            part.to_str().ok_or("part ordinal")?.parse()?,
        )?
    } else {
        of1_bronze_decoder::batch::execute(
            std::path::Path::new(&args[0]),
            args[1].to_str().ok_or("batch id")?,
            std::path::Path::new(&args[2]),
        )?
    };
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}
