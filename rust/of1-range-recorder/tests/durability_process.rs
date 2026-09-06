//! Process-death regression: children exit without Rust destructors at durable mutation seams.
//! Only this test harness starts its own sealed test binary. There is no transport or endpoint.

use of1_range_recorder::{
    OfflinePlan,
    durable::{Clock, ClockSample, FaultPoint, Response, Store, StoreError},
    fixture,
};
use std::{fs, path::PathBuf, process::Command};
use tempfile::tempdir;

struct FixedClock;

impl Clock for FixedClock {
    fn sample(&self) -> Result<ClockSample, StoreError> {
        Ok(ClockSample {
            wall_ms: 10_000,
            boot_ms: 2_000,
            boot_id: "fixture-process-boot".into(),
        })
    }
}

fn plan() -> OfflinePlan {
    let mut plan = fixture::plan();
    plan.end_slot = plan.start_slot + 1;
    plan.budget.max_requests = 6;
    plan.budget.max_total_response_entity_bytes = 192;
    plan.budget.request_retries = 2;
    plan
}

fn fault(number: u8) -> FaultPoint {
    match number {
        0 => FaultPoint::BeforeReservationPublish,
        1 => FaultPoint::AfterReservationPublish,
        2 => FaultPoint::AfterRawWrite,
        3 => FaultPoint::AfterRawSync,
        4 => FaultPoint::AfterReceiptSync,
        5 => FaultPoint::BeforePublish,
        6 => FaultPoint::AfterPublish,
        7 => FaultPoint::AfterPublishSync,
        _ => panic!("unrecognized sealed fault number"),
    }
}

#[test]
fn crash_worker() {
    // Invoked only by the parent test below; a normal suite invocation has no fixture root.
    let Some(root) = std::env::var_os("OF1_FIXTURE_CRASH_ROOT") else {
        return;
    };
    let root = PathBuf::from(root);
    let number = std::env::var("OF1_FIXTURE_CRASH_POINT")
        .unwrap()
        .parse::<u8>()
        .unwrap();
    let mut store = Store::create(
        &root.join("capture"),
        plan(),
        &root.join("index.raw"),
        FixedClock,
    )
    .unwrap();
    let result = if number < 2 {
        store.inject_fault(fault(number));
        store.reserve(0).map(|_| ())
    } else {
        let permit = store.reserve(0).unwrap();
        store.inject_fault(fault(number));
        store
            .commit(
                permit,
                Response {
                    status: 206,
                    start: 128,
                    end_exclusive: 160,
                    total: 1024,
                },
                &[0xA5; 32],
            )
            .map(|_| ())
    };
    assert!(matches!(result, Err(StoreError::Injected(_))));
    // Deliberately bypasses Store/File/TempDir destructors. No core file is generated.
    std::process::exit(86);
}

#[test]
fn process_exit_preserves_reservations_atomic_pairs_and_releases_writer_lock() {
    for number in 0..8 {
        let directory = tempdir().unwrap();
        let index = directory.path().join("index.raw");
        fs::write(&index, fixture::index_bytes().unwrap()).unwrap();
        let output = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "crash_worker", "--nocapture"])
            .current_dir(directory.path())
            .env("OF1_FIXTURE_CRASH_ROOT", directory.path())
            .env("OF1_FIXTURE_CRASH_POINT", number.to_string())
            .output()
            .unwrap();
        assert_eq!(
            output.status.code(),
            Some(86),
            "fault {number}: {} {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let resumed = Store::resume(
            &directory.path().join("capture"),
            plan(),
            &index,
            FixedClock,
        );
        if number == 0 {
            assert!(matches!(resumed, Err(StoreError::Corrupt)));
            assert!(matches!(
                Store::inspect(
                    &directory.path().join("capture"),
                    plan(),
                    &index,
                    FixedClock
                ),
                Err(StoreError::Corrupt)
            ));
            continue;
        }
        let resumed = resumed.unwrap();
        let summary = resumed.summary().unwrap();
        let attempts = 1;
        let published = u64::from(number >= 6);
        assert_eq!(summary.attempts_reserved, attempts, "fault {number}");
        assert_eq!(
            summary.charged_entity_bytes,
            attempts * 32,
            "fault {number}"
        );
        assert_eq!(summary.published_requests, published, "fault {number}");
        assert_eq!(summary.unpublished_attempts, attempts - published);
        match resumed.published(0).unwrap() {
            Some(object) => {
                assert_eq!(published, 1);
                assert_eq!(object.bytes, [0xA5; 32]);
            }
            None => assert_eq!(published, 0),
        }
    }
}
