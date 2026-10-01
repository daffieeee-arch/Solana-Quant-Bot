//! Source-pinned same-test-binary crash children; never compiled into production.
use super::*;
fn fixture(attempts: u64, entity: u64) -> (tempfile::TempDir, PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("campaign");
    drop(
        Guard::new(
            &root,
            true,
            Limits {
                attempts,
                entity,
                disk: 16 * 1024 * 1024,
                free: 0,
            },
        )
        .unwrap(),
    );
    (dir, root)
}
fn run(root: &Path, i: usize) -> Guard {
    Guard::acquisition(
        &b7::sample(i, root).unwrap(),
        &root.join(format!("runs/w{i:02}")),
        &"a".repeat(64),
        &"b".repeat(64),
        true,
        true,
    )
    .unwrap()
}
#[test]
fn campaign_crash_child() {
    let Ok(path) = std::env::var("OF1_B7_TEST_ROOT") else {
        return;
    };
    let mut g = Guard::open(Path::new(&path), true).unwrap();
    g.reserve(0, 0, 10).unwrap();
    panic!("expected controlled child exit");
}
#[test]
fn process_crashes_preserve_charges_or_stop_at_ambiguous_publication() {
    for point in ["JOURNAL_SYNCED", "HEAD_PENDING", "HEAD_PUBLISHED"] {
        let (_dir, root) = fixture(4, 100);
        drop(run(&root, 0));
        let status = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "campaign::process_tests::campaign_crash_child"])
            .env("OF1_B7_TEST_ROOT", &root)
            .env("OF1_B7_TEST_CRASH_POINT", point)
            .status()
            .unwrap();
        assert_eq!(status.code(), Some(74));
        let opened = Guard::open(&root, true);
        if point == "HEAD_PUBLISHED" {
            let mut g = opened.unwrap();
            assert_eq!((g.state.requests, g.state.entity), (1, 10));
            assert!(g.verify_charges(0, &[]).is_err());
            g.verify_charges(0, &[(0, 10)]).unwrap();
            g.reserve(0, 1, 10).unwrap();
        } else {
            assert!(opened.is_err());
        }
    }
}

#[test]
fn continuation_crash_child() {
    let Ok(path) = std::env::var("OF1_B7_CONTINUATION_TEST_ROOT") else {
        return;
    };
    let mut guard = Guard::open(Path::new(&path), true).unwrap();
    let sample = b7::sample(0, Path::new(&path)).unwrap();
    let approval: ContinuationApproval =
        serde_json::from_str(&std::env::var("OF1_B7_CONTINUATION_TEST_APPROVAL").unwrap()).unwrap();
    guard
        .admit_continuation(&sample, approval, &SystemClock.sample().unwrap())
        .unwrap();
    panic!("controlled crash was required");
}
#[test]
fn continuation_crash_never_refunds_or_regrants_processing() {
    for point in ["JOURNAL_SYNCED", "HEAD_PENDING", "HEAD_PUBLISHED"] {
        let (_dir, guard, sample, approval) = super::tests::continuation_fixture();
        let root = guard.root.clone();
        let old = guard.state.clone();
        drop(guard);
        let status = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "campaign::process_tests::continuation_crash_child",
            ])
            .env("OF1_B7_CONTINUATION_TEST_ROOT", &root)
            .env(
                "OF1_B7_CONTINUATION_TEST_APPROVAL",
                serde_json::to_string(&approval).unwrap(),
            )
            .env("OF1_B7_TEST_CRASH_POINT", point)
            .status()
            .unwrap();
        assert_eq!(status.code(), Some(74));
        let opened = Guard::open(&root, true);
        if point == "HEAD_PUBLISHED" {
            let mut resumed = opened.unwrap();
            assert_eq!((resumed.state.requests, resumed.state.entity), (1, 10));
            assert_eq!(
                resumed.state.processing[&0].approval,
                old.processing[&0].approval
            );
            assert_eq!(resumed.state.processing[&0].stage, old.processing[&0].stage);
            assert!(
                resumed
                    .admit_continuation(&sample, approval, &SystemClock.sample().unwrap())
                    .is_err()
            );
        } else {
            assert!(opened.is_err());
        }
        assert_eq!(
            fs::read(root.join("work/w00/old-failure")).unwrap(),
            b"retained failure"
        );
    }
}

#[test]
fn actual_approved_generator_preserves_clock_contract_and_separate_stage_budgets() {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let script =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../scripts/of1-approved-authority.py");
    let policy = ClockPolicy::standard();
    for (kind, requests, entity, runtime) in [
        ("metadata", 12, 15_576_576, 600_000),
        ("payload", 48, 68_136_183, 1_200_000),
        ("processing", 1, 1, 900_000),
        ("continuation", 3, 4_175_463, 600_000),
    ] {
        // One actual pair per new approval; no fixture authority or campaign mutation.
        let at = SystemClock.sample().unwrap();
        let target = "a".repeat(64);
        let input = serde_json::json!({"clock":at,"target_sha256":target,
            "approval_id":"offline-native-approved-validation","operator":"synthetic offline validation only"});
        let mut child = Command::new("python3")
            .arg(&script)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(&serde_json::to_vec(&input).unwrap())
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success(), "{:?}", output.stderr);
        let a: Authority = serde_json::from_slice(&output.stdout).unwrap();
        let budget = StageBudget {
            max_requests: requests,
            max_response_entity_bytes_total: entity,
            max_runtime_ms: runtime,
        };
        validate_authority(Some(&policy), &a, &target).unwrap();
        let stage = make_stage(Some(&policy), &a, &budget, &input, &at).unwrap();
        assert_eq!(stage.deadline_boot_ms - at.boot_ms, runtime);
        assert_eq!(stage.budget, budget);
        let mut wrong = serde_json::to_value(&a).unwrap();
        wrong["clock_anchor"]["initialize_by_boot_ms"] = serde_json::json!(at.boot_ms + runtime);
        wrong["clock_anchor"]["expires_at_boot_ms"] = serde_json::json!(at.boot_ms + runtime);
        wrong["not_after_ms"] = serde_json::json!(at.wall_ms + runtime);
        if runtime == 1_200_000 {
            wrong["clock_anchor"]["initialize_by_boot_ms"] =
                serde_json::json!(at.boot_ms + 1_200_000);
        }
        let wrong: Authority = serde_json::from_value(wrong).unwrap();
        assert!(validate_authority(Some(&policy), &wrong, &target).is_err());
        assert!(validate_authority(Some(&policy), &a, &"b".repeat(64)).is_err());
        let mut changed = at.clone();
        changed.boot_id.push_str("-wrong");
        assert!(make_stage(Some(&policy), &a, &budget, &input, &changed).is_err());
        changed = at.clone();
        changed.boot_ms += 1_200_001;
        changed.wall_ms += 1_200_001;
        assert!(make_stage(Some(&policy), &a, &budget, &input, &changed).is_err());
        println!(
            "{}",
            serde_json::json!({"kind":kind,"generated_authority":a,"budget":budget,
            "native_approved_validation":"PASS","wrong_offsets_boot_expiry_target":"REFUSED"})
        );
    }
}
