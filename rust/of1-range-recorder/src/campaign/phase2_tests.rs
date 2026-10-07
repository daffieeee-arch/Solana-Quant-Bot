use super::*;

fn completed_phase_one_fixture(
    regular_first_manifest: bool,
) -> (tempfile::TempDir, SampleIdentity, ClockSample) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("campaign");
    phase2_fixture::completed_phase_one(&root, regular_first_manifest).unwrap();
    let sample = b7::sample(8, &root).unwrap();
    (dir, sample, SystemClock.sample().unwrap())
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
