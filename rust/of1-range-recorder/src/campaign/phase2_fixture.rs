//! Fixture-only phase-one state for exercising the real phase-two CLI boundary.
//! The production campaign root and ordinary network-only builds cannot use it.
use super::{
    Authority, BTreeMap, Clock, ClockPolicy, DRIVER_FILES, EvaluationBinding, Guard, Limits,
    METHOD_ACCEPTANCE_SHA256, METHOD_SHA256, Path, PathBuf, Processing, ProcessingApproval, Run,
    StageBudget, StoreError, StoreResult, SystemClock, b7, bytes, fs, make_stage, sha256, write,
};

/// # Errors
/// Refuses an existing or production root; writes only synthetic phase-one
/// manifests and journal records with Fixture authority.
pub fn completed_phase_one(root: &Path, regular_first_manifest: bool) -> StoreResult<()> {
    if root.exists() || root == Path::new(b7::PRODUCTION_ROOT) {
        return Err(StoreError::Identity);
    }
    let mut g = Guard::new(
        root,
        true,
        Limits {
            free: 0,
            ..Limits::production()
        },
    )?;
    let at = SystemClock.sample()?;
    let mut next = g.state.clone();
    for ordinal in 0..8_u64 {
        let index = usize::try_from(ordinal).map_err(|_| StoreError::Identity)?;
        next.runs.insert(
            ordinal,
            Run {
                aggregate: "a".repeat(64),
                metadata_lease: "b".repeat(64),
                payload_lease: None,
                payload_continuation: None,
                metadata_continuation: None,
                requests: 0,
                entity: 0,
                attempts: BTreeMap::new(),
            },
        );
        let evaluation = (ordinal >= 4).then(|| EvaluationBinding {
            method_sha256: METHOD_SHA256.into(),
            acceptance_sha256: METHOD_ACCEPTANCE_SHA256.into(),
            previous_ledger_sha256: "c".repeat(64),
            driver_files: DRIVER_FILES
                .iter()
                .map(|n| ((*n).into(), "d".repeat(64)))
                .collect(),
            python_sha256: "e".repeat(64),
        });
        let approval = ProcessingApproval {
            authority: Authority::Fixture,
            window: ordinal,
            plan_sha256: "f".repeat(64),
            worker_sha256s: vec!["0".repeat(64); 3],
            evaluation,
        };
        let stage = make_stage(
            Some(&ClockPolicy::standard()),
            &Authority::Fixture,
            &StageBudget {
                max_requests: 1,
                max_response_entity_bytes_total: 1,
                max_runtime_ms: 900_000,
            },
            &approval,
            &at,
        )?;
        let path = if ordinal == 0 && !regular_first_manifest {
            root.join("work/w00/continuation-1/collection.json")
        } else {
            root.join(format!("work/w{ordinal:02}/collection.json"))
        };
        fs::create_dir_all(path.parent().ok_or(StoreError::Identity)?)?;
        let manifest = serde_json::json!({
            "schema": if ordinal == 0 && !regular_first_manifest { "OF1_CONTINUED_BATCH_COLLECTION_1" }
                else { "OF1_PARTED_BATCH_COLLECTION_1" },
            "sample_identity": b7::sample(index, root)?,
            "state": "COMPLETE", "research_ready": false,
            "completeness": {"all_selected_slots_accounted": true},
            "slot_outcomes": (0..16_u64).map(|offset| serde_json::json!({
                "slot": b7::STARTS[index] + offset, "state": "ACCOUNTED"
            })).collect::<Vec<_>>()
        });
        let raw = bytes(&manifest)?;
        let hash = sha256(&raw);
        write(&path, &raw)?;
        write(
            &PathBuf::from(format!("{}.sha256", path.display())),
            hash.as_bytes(),
        )?;
        next.processing.insert(
            ordinal,
            Processing {
                approval,
                stage,
                complete: true,
                continuation: None,
                evaluation_continuations: Vec::new(),
                sealed_manifest_sha256: Some(hash),
            },
        );
    }
    g.commit(next)
}
