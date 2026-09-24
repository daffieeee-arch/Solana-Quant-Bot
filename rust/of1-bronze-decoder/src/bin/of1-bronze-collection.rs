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
        ["seal",plan,root,output]=>{let(p,h)=batch::read_plan(Path::new(plan))?;let _campaign=batch::campaign_output(&p,&h,Path::new(output),2*1024*1024)?;let v=collection::inspect(Path::new(plan),Path::new(root))?;let bytes=serde_json::to_vec_pretty(&v)?;batch::write_new(Path::new(output),&bytes)?;batch::write_new(Path::new(&format!("{output}.sha256")),sha256(&bytes).as_bytes())?;fs::File::open(Path::new(output).parent().ok_or("output parent")?)?.sync_all()?;v},
        ["campaign-processing-proposal",plan]=>{
            let(p,h)=batch::read_plan(Path::new(plan))?;p.validate_sources()?;
            let(sample,_)=batch::campaign_sample(&p)?.ok_or("B7 required")?;
            let workers=vec![p.workers.batch_decoder_sha256,p.workers.projector_sha256,of1_range_recorder::durable::acquisition::current_executable_sha256()?];
            serde_json::json!({"schema":"OF1_B7_PROCESSING_PROPOSAL_1","approved":false,"network_enabled":false,
                "window":sample.b7.as_ref().ok_or("B7 required")?.window_ordinal,"plan_sha256":h,"worker_sha256s":workers,
                "approval_target_sha256":of1_range_recorder::campaign::Guard::processing_target(&sample,&h,&workers)?,
                "max_runtime_ms":900_000,"max_artifact_bytes":4_294_967_296_u64,"research_ready":false})
        },
        ["campaign-admit",plan,approval]=>{
            let(p,h)=batch::read_plan(Path::new(plan))?;p.validate_sources()?;
            let(sample,run)=batch::campaign_sample(&p)?.ok_or("B7 required")?;
            let a:of1_range_recorder::campaign::ProcessingApproval=serde_json::from_slice(&of1_range_recorder::acquisition::read_limited(Path::new(approval),1_048_576)?)?;
            if a.plan_sha256!=h || a.worker_sha256s!=vec![p.workers.batch_decoder_sha256,p.workers.projector_sha256,of1_range_recorder::durable::acquisition::current_executable_sha256()?]{return Err("processing workers/plan mismatch".into())}
            of1_range_recorder::campaign::Guard::admit_processing(&sample,&run,a,&of1_range_recorder::durable::Clock::sample(&of1_range_recorder::durable::SystemClock)?)?;
            serde_json::json!({"state":"ADMITTED_EXISTING_APPROVAL","new_authority":false})
        },
        ["campaign-check",plan,root,reserve]=>{
            let(p,h)=batch::read_plan(Path::new(plan))?;p.validate_sources()?;
            let(g,s)=batch::campaign_output(&p,&h,Path::new(root),reserve.parse()?)?.ok_or("B7 required")?;
            serde_json::json!({"state":"WITHIN_EXISTING_LEASE","remaining_ms":g.processing_remaining_ms(&s)?,"new_authority":false})
        },
        ["campaign-complete",plan,root]=>{
            let(p,h)=batch::read_plan(Path::new(plan))?;
            let(mut guard,sample)=batch::campaign_output(&p,&h,Path::new(root),2*1024*1024)?.ok_or("B7 required")?;
            let v=collection::inspect(Path::new(plan),Path::new(root))?;
            if v["state"]!="COMPLETE" {return Err("complete verified collection required".into())}
            let retained:serde_json::Value=serde_json::from_slice(&of1_range_recorder::acquisition::read_limited(&Path::new(root).join("collection.json"),16*1024*1024)?)?;
            if retained!=v{return Err("final collection identity mismatch".into())}
            let(raw,html)=collection::campaign_report(&p,Path::new(root),&v,&guard.accounting()?)?;
            guard.processing_tick(&sample)?;
            batch::write_new(&Path::new(root).join("campaign-report.json"),&raw)?;
            batch::write_new(&Path::new(root).join("index.html"),&html)?;
            fs::File::open(root)?.sync_all()?;
            batch::write_new(&Path::new(root).join("campaign-report.COMPLETE"),sha256(&raw).as_bytes())?;
            fs::File::open(root)?.sync_all()?;
            guard.complete_processing(sample.b7.ok_or("B7 required")?.window_ordinal)?;
            serde_json::json!({"state":"VERIFIED_PROCESSING_COMPLETE","research_ready":false})
        },
        _=>return Err("usage: of1-bronze-collection source RUN | plan-check PLAN | verify-batch PLAN ID DECODE | inspect PLAN ROOT | seal PLAN ROOT NEW_MANIFEST".into()),
    };
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}
