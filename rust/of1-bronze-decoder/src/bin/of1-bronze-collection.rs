//! Offline source/plan/collection verifier. Publication never mutates old output.
use of1_bronze_decoder::{batch, collection};
use of1_range_recorder::sha256;
use std::{fs, path::Path};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let value=match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice(){
        ["source",root]=>batch::source_identity(Path::new(root))?,
        ["plan-check",plan]=>{let (p,h)=batch::read_plan(Path::new(plan))?;p.validate_sources()?;serde_json::json!({"state":"VERIFIED","plan_sha256":h,"slots":p.logical_selection.len(),"batches":p.batches.len()})},
        ["verify-batch",plan,id,output]=>{let(p,h)=batch::read_plan(Path::new(plan))?;p.validate_sources()?;batch::verify_output(&p,&h,p.batch(id)?,Path::new(output))?},
        ["inspect",plan,root]=>collection::inspect(Path::new(plan),Path::new(root))?,
        ["seal",plan,root,output]=>{let v=collection::inspect(Path::new(plan),Path::new(root))?;let bytes=serde_json::to_vec_pretty(&v)?;batch::write_new(Path::new(output),&bytes)?;batch::write_new(Path::new(&format!("{output}.sha256")),sha256(&bytes).as_bytes())?;fs::File::open(Path::new(output).parent().ok_or("output parent")?)?.sync_all()?;v},
        _=>return Err("usage: of1-bronze-collection source RUN | plan-check PLAN | verify-batch PLAN ID DECODE | inspect PLAN ROOT | seal PLAN ROOT NEW_MANIFEST".into()),
    };
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}
