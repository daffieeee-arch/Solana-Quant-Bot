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
