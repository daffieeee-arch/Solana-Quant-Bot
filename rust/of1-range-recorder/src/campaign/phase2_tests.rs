use super::*;

fn completed_phase_one_fixture() -> (tempfile::TempDir, SampleIdentity, ClockSample) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("campaign");
    let mut g = Guard::new(
        &root,
        true,
        Limits {
            free: 0,
            ..Limits::production()
        },
    )
    .unwrap();
    let at = SystemClock.sample().unwrap();
    let mut next = g.state.clone();
    for ordinal in 0..8_u64 {
        let index = usize::try_from(ordinal).unwrap();
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
        )
        .unwrap();
        let path = if ordinal == 0 {
            root.join("work/w00/continuation-1/collection.json")
        } else {
            root.join(format!("work/w{ordinal:02}/collection.json"))
        };
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let manifest = serde_json::json!({
            "schema": if ordinal == 0 { "OF1_CONTINUED_BATCH_COLLECTION_1" }
                else { "OF1_PARTED_BATCH_COLLECTION_1" },
            "sample_identity": b7::sample(index, &root).unwrap(),
            "state": "COMPLETE", "research_ready": false,
            "completeness": {"all_selected_slots_accounted": true},
            "slot_outcomes": (0..16_u64).map(|offset| serde_json::json!({
                "slot": b7::STARTS[index] + offset, "state": "ACCOUNTED"
            })).collect::<Vec<_>>()
        });
        let raw = bytes(&manifest).unwrap();
        let hash = sha256(&raw);
        write(&path, &raw).unwrap();
        write(
            &PathBuf::from(format!("{}.sha256", path.display())),
            hash.as_bytes(),
        )
        .unwrap();
        next.processing.insert(
            ordinal,
            Processing {
                approval,
                stage,
                complete: true,
                continuation: None,
                evaluation_continuations: Vec::new(),
                sealed_manifest_sha256: (ordinal >= 4).then_some(hash),
            },
        );
    }
    g.commit(next).unwrap();
    drop(g);
    let sample = b7::sample(8, &root).unwrap();
    (dir, sample, at)
}

#[test]
fn phase_two_proposal_binds_eight_closed_manifests_and_current_ledger() {
    let (_dir, sample, at) = completed_phase_one_fixture();
    let root = Path::new(&sample.b7.as_ref().unwrap().campaign_root);
    let proposal = Guard::phase2_proposal(&sample).unwrap();
    assert_eq!(
        proposal["phase_one_evidence"]["closed_manifests"]
            .as_array()
            .unwrap()
            .len(),
        8
    );
    assert_eq!(
        proposal["phase_one_evidence"]["phase_two_windows"]
            .as_array()
            .unwrap()
            .len(),
        8
    );
    let approval: PhaseApproval = serde_json::from_value(proposal["approval"].clone()).unwrap();
    let old_ledger = approval.phase_one_ledger_sha256.clone();
    let mut wrong = approval.clone();
    wrong.phase_one_evidence_sha256 = "a".repeat(64);
    assert!(Guard::admit_phase2(&sample, wrong, &at).is_err());
    assert_eq!(Guard::status(root).unwrap()["ledger_sha256"], old_ledger);
    let mut wrong = approval.clone();
    wrong.phase_one_ledger_sha256 = "b".repeat(64);
    assert!(Guard::admit_phase2(&sample, wrong, &at).is_err());
    assert!(Guard::phase2_proposal(&b7::sample(12, root).unwrap()).is_err());
    let sidecar = root.join("work/w07/collection.json.sha256");
    let original = fs::read(&sidecar).unwrap();
    fs::write(&sidecar, b"0".repeat(64)).unwrap();
    assert!(Guard::phase2_proposal(&sample).is_err());
    fs::write(&sidecar, original).unwrap();
    Guard::admit_phase2(&sample, approval.clone(), &at).unwrap();
    assert!(Guard::admit_phase2(&sample, approval, &at).is_err());
}
