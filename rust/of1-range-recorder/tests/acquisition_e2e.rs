#![cfg(feature = "tls-fixture")]

use serde_json::Value;
use std::process::Command;

#[test]
fn executed_tls_metadata_crash_restart_payload_scenario_matches_committed_evidence() {
    let scratch = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_of1-acquisition-fixture-evidence"))
        .arg("--check")
        .arg(scratch.path())
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let metrics: Value = serde_json::from_slice(
        &std::fs::read(
            scratch
                .path()
                .join("of1-acquisition-fixture/measurements.json"),
        )
        .unwrap(),
    )
    .unwrap();
    assert_eq!(metrics["evidence"], "Fixture");
    assert!(metrics["final"]["peak_rss_bytes"].as_u64().unwrap() > 0);
    assert!(metrics["final"]["disk_charge_bytes"].as_u64().unwrap() > 0);
    assert_eq!(metrics["final"]["attempts_reserved"], 6);
    assert_eq!(metrics["final"]["published_requests"], 5);
    assert_eq!(metrics["final"]["unpublished_attempts"], 1);
}
