#![cfg(feature = "tls-fixture")]
//! A subprocess is needed only to prove cross-process exclusion. The child
//! invokes this exact local test binary and reaches no socket or provider.

use of1_range_recorder::rate::{FixtureDownloadGuard, RateError};

#[test]
fn coordination_child_process() {
    let Some(path) = std::env::var_os("OF1_RATE_TEST_LOCK") else {
        return;
    };
    if let Some(ready) = std::env::var_os("OF1_RATE_TEST_READY") {
        let _guard = FixtureDownloadGuard::acquire(std::path::Path::new(&path)).unwrap();
        std::fs::write(ready, b"LOCK_HELD").unwrap();
        // Parent deliberately terminates this isolated test actor. Bounded if
        // the parent fails, and no lease or provider operation is involved.
        std::thread::sleep(std::time::Duration::from_secs(5));
    } else {
        assert!(matches!(
            FixtureDownloadGuard::acquire(std::path::Path::new(&path)),
            Err(RateError::Busy)
        ));
    }
}

#[test]
fn crashed_holder_releases_lock_without_resetting_or_unlinking_shared_identity() {
    use std::os::unix::fs::MetadataExt;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("crash.lock");
    let ready = dir.path().join("ready");
    let mut child = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "coordination_child_process", "--nocapture"])
        .env("OF1_RATE_TEST_LOCK", &path)
        .env("OF1_RATE_TEST_READY", &ready)
        .stdout(std::process::Stdio::null())
        .spawn()
        .unwrap();
    let started = std::time::Instant::now();
    while !ready.is_file() && started.elapsed() < std::time::Duration::from_secs(2) {
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
    if !ready.is_file() {
        let _ = child.kill();
        let _ = child.wait();
        panic!("isolated rate-lock actor did not become ready");
    }
    let inode = std::fs::metadata(&path).unwrap().ino();
    assert!(matches!(
        FixtureDownloadGuard::acquire(&path),
        Err(RateError::Busy)
    ));
    child.kill().unwrap();
    assert!(!child.wait().unwrap().success());
    let _after_crash = FixtureDownloadGuard::acquire(&path).unwrap();
    assert_eq!(std::fs::metadata(&path).unwrap().ino(), inode);
}

#[test]
fn one_lock_excludes_a_separate_process_not_only_a_second_thread() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("cross-process.lock");
    let held = FixtureDownloadGuard::acquire(&path).unwrap();
    let result = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "coordination_child_process", "--nocapture"])
        .env("OF1_RATE_TEST_LOCK", &path)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "child: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    drop(held);
    assert!(FixtureDownloadGuard::acquire(&path).is_ok());
    assert!(path.is_file()); // Never unlink a potentially still-shared lock inode.
}
