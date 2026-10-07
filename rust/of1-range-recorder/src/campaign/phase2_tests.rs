use super::*;

fn completed_phase_one_fixture(
    regular_first_manifest: bool,
) -> (tempfile::TempDir, SampleIdentity, ClockSample) {
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
        let path = if ordinal == 0 && !regular_first_manifest {
            root.join("work/w00/continuation-1/collection.json")
        } else {
            root.join(format!("work/w{ordinal:02}/collection.json"))
        };
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let manifest = serde_json::json!({
            "schema": if ordinal == 0 && !regular_first_manifest { "OF1_CONTINUED_BATCH_COLLECTION_1" }
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
                sealed_manifest_sha256: Some(hash),
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
    let (_dir, sample, at) = completed_phase_one_fixture(false);
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
    let changed_path = root.join("work/w01/collection.json");
    let changed_sidecar = root.join("work/w01/collection.json.sha256");
    let original_manifest = fs::read(&changed_path).unwrap();
    let original_hash = fs::read(&changed_sidecar).unwrap();
    let mut changed: serde_json::Value = serde_json::from_slice(&original_manifest).unwrap();
    changed["layers"]["bronze"]["ordered_logical_sha256"] = serde_json::json!("0".repeat(64));
    let changed_bytes = bytes(&changed).unwrap();
    fs::write(&changed_path, &changed_bytes).unwrap();
    fs::write(&changed_sidecar, sha256(&changed_bytes)).unwrap();
    assert!(Guard::phase2_proposal(&sample).is_err());
    fs::write(&changed_path, original_manifest).unwrap();
    fs::write(&changed_sidecar, original_hash).unwrap();
    Guard::admit_phase2(&sample, approval.clone(), &at).unwrap();
    assert!(Guard::admit_phase2(&sample, approval, &at).is_err());
}

#[test]
fn phase_two_fixture_accepts_regular_first_manifest_with_journal_binding() {
    let (_dir, sample, at) = completed_phase_one_fixture(true);
    let proposal = Guard::phase2_proposal(&sample).unwrap();
    let approval: PhaseApproval = serde_json::from_value(proposal["approval"].clone()).unwrap();
    Guard::admit_phase2(&sample, approval, &at).unwrap();
}

#[test]
fn w08_metadata_continuation_rechecks_phase_two_and_all_phase_one_manifests() {
    let (_dir, sample, at) = completed_phase_one_fixture(false);
    let root = Path::new(&sample.b7.as_ref().unwrap().campaign_root);
    let proposal = Guard::phase2_proposal(&sample).unwrap();
    let approval: PhaseApproval = serde_json::from_value(proposal["approval"].clone()).unwrap();
    Guard::admit_phase2(&sample, approval, &at).unwrap();
    let mut g = Guard::open(root, true).unwrap();
    let mut next = g.state.clone();
    next.runs.insert(
        8,
        Run {
            aggregate: "a".repeat(64),
            metadata_lease: "b".repeat(64),
            payload_lease: None,
            payload_continuation: None,
            metadata_continuation: None,
            requests: 2,
            entity: 5_188_096,
            attempts: BTreeMap::from([(0, 5_184_000), (1, 4_096)]),
        },
    );
    next.requests += 2;
    next.entity += 5_188_096;
    g.commit(next).unwrap();
    let head = g.metadata_continuation_head(8).unwrap();
    assert_eq!(head, g.head_hash);
    assert!(g.metadata_continuation_head(9).is_err());
    assert!(
        g.admit_metadata_continuation(8, &"f".repeat(64), &"e".repeat(64))
            .is_err()
    );

    let original_phase2 = g.state.phase2.take();
    assert!(g.metadata_continuation_head(8).is_err());
    g.state.phase2 = original_phase2;

    let sidecar = root.join("work/w07/collection.json.sha256");
    let original = fs::read(&sidecar).unwrap();
    fs::write(&sidecar, b"0".repeat(64)).unwrap();
    assert!(g.metadata_continuation_head(8).is_err());
    fs::write(&sidecar, original).unwrap();
    assert_eq!(g.metadata_continuation_head(8).unwrap(), head);

    g.admit_metadata_continuation(8, &head, &"e".repeat(64))
        .unwrap();
    assert!(g.metadata_continuation_head(8).is_err());
}
