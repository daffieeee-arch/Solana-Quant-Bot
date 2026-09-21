use of1_range_recorder::rate::{
    Admission, BURST_BYTES, DownloadRate, ENTITY_BYTES_PER_SECOND, EntityRate, RateError,
};

const SECOND: u64 = 1_000_000_000;

fn admit_at_next(bucket: &mut EntityRate, capacity: u64, now: &mut u64, deadline: u64) {
    if let Admission::Wait { nanoseconds } = bucket.admit(capacity, *now, deadline).unwrap() {
        *now += nanoseconds;
        assert_eq!(bucket.admit(capacity, *now, deadline), Ok(Admission::Ready));
    }
}

#[test]
fn exact_standard_is_700_decimal_megabits_not_mib_or_per_connection() {
    let standard = DownloadRate::standard();
    assert_eq!(standard.bytes_per_second * 8, 700_000_000);
    assert_eq!(standard.burst_bytes, 65_536);
    assert_eq!(standard.concurrency, 1);
    assert_eq!(standard.unit, "RESPONSE_ENTITY_BYTES");
    assert_eq!(standard.scope, "SAME_USER_OFFICIAL_OF1_ALL_RUNS");
    assert!(standard.validate().is_ok());
    for field in ["bytes_per_second", "burst_bytes", "concurrency"] {
        let mut altered = serde_json::to_value(&standard).unwrap();
        altered[field] = (altered[field].as_u64().unwrap() + 1).into();
        assert_eq!(
            serde_json::from_value::<DownloadRate>(altered)
                .unwrap()
                .validate(),
            Err(RateError::Policy)
        );
    }
}

#[test]
fn initially_empty_then_sustained_reads_never_exceed_rate() {
    let mut bucket = EntityRate::empty(0);
    let mut now = 0;
    let mut received = 0;
    for _ in 0..2000 {
        admit_at_next(&mut bucket, BURST_BYTES, &mut now, 10 * SECOND);
        bucket.complete(BURST_BYTES, now).unwrap();
        received += BURST_BYTES;
        assert!(
            u128::from(received) * u128::from(SECOND)
                <= u128::from(now) * u128::from(ENTITY_BYTES_PER_SECOND)
        );
    }
    assert!(now > SECOND);
}

#[test]
fn idle_credit_is_at_most_one_short_burst() {
    let mut bucket = EntityRate::empty(0);
    assert_eq!(
        bucket.admit(BURST_BYTES, SECOND, 2 * SECOND),
        Ok(Admission::Ready)
    );
    bucket.complete(BURST_BYTES, SECOND).unwrap();
    assert!(matches!(
        bucket.admit(1, SECOND, 2 * SECOND),
        Ok(Admission::Wait { .. })
    ));
}

#[test]
fn long_blocking_read_cannot_create_double_receive_burst() {
    let mut bucket = EntityRate::empty(0);
    assert_eq!(
        bucket.admit(BURST_BYTES, SECOND, 4 * SECOND),
        Ok(Admission::Ready)
    );
    bucket.complete(BURST_BYTES, 2 * SECOND).unwrap();
    assert_eq!(
        bucket.admit(BURST_BYTES, 2 * SECOND, 4 * SECOND),
        Ok(Admission::Wait {
            nanoseconds: 748_983
        })
    );
}

#[test]
fn short_read_refunds_only_unused_capacity_and_preserves_exact_bytes() {
    let mut bucket = EntityRate::empty(0);
    assert_eq!(
        bucket.admit(BURST_BYTES, SECOND, 2 * SECOND),
        Ok(Admission::Ready)
    );
    bucket.complete(11, SECOND).unwrap();
    assert_eq!(
        bucket.admit(BURST_BYTES - 11, SECOND, 2 * SECOND),
        Ok(Admission::Ready)
    );
    bucket.complete(BURST_BYTES - 11, SECOND).unwrap();
    assert!(matches!(
        bucket.admit(1, SECOND, 2 * SECOND),
        Ok(Admission::Wait { .. })
    ));
}

#[test]
fn zero_byte_eof_or_error_refunds_capacity_not_a_new_burst() {
    let mut bucket = EntityRate::empty(0);
    assert_eq!(
        bucket.admit(BURST_BYTES, SECOND, 3 * SECOND),
        Ok(Admission::Ready)
    );
    bucket.complete(0, 2 * SECOND).unwrap();
    assert_eq!(
        bucket.admit(BURST_BYTES, 2 * SECOND, 3 * SECOND),
        Ok(Admission::Ready)
    );
    bucket.complete(BURST_BYTES, 2 * SECOND).unwrap();
    assert!(matches!(
        bucket.admit(1, 2 * SECOND, 3 * SECOND),
        Ok(Admission::Wait { .. })
    ));
}

#[test]
fn restart_or_next_exclusive_holder_never_grants_free_credit() {
    let mut now = 0;
    let mut first = EntityRate::empty(now);
    admit_at_next(&mut first, BURST_BYTES, &mut now, SECOND);
    first.complete(BURST_BYTES, now).unwrap();
    let mut second = EntityRate::empty(now);
    assert_eq!(
        second.admit(BURST_BYTES, now, SECOND),
        Ok(Admission::Wait {
            nanoseconds: 748_983
        })
    );
}

#[test]
fn original_deadline_is_not_reset_by_wait_or_restart() {
    let mut bucket = EntityRate::empty(0);
    assert_eq!(
        bucket.admit(BURST_BYTES, 0, 748_983),
        Err(RateError::Deadline)
    );
    assert_eq!(
        bucket.admit(BURST_BYTES, 0, SECOND),
        Ok(Admission::Wait {
            nanoseconds: 748_983
        })
    );
    // A scheduler pause can exceed the original deadline; the next admission
    // must fail rather than receiving another second relative to this retry.
    assert_eq!(
        bucket.admit(BURST_BYTES, SECOND, SECOND),
        Err(RateError::Deadline)
    );
    let mut restarted = EntityRate::empty(SECOND);
    assert_eq!(restarted.admit(1, SECOND, SECOND), Err(RateError::Deadline));
}

#[test]
fn rollback_and_invalid_completion_fail_closed() {
    let mut bucket = EntityRate::empty(100);
    assert_eq!(bucket.admit(1, 99, SECOND), Err(RateError::ClockRollback));
    assert_eq!(bucket.complete(0, 100), Err(RateError::Capacity));
    assert_eq!(
        bucket.admit(BURST_BYTES + 1, 100, SECOND),
        Err(RateError::Capacity)
    );
    assert_eq!(bucket.admit(0, 100, SECOND), Err(RateError::Capacity));
    assert_eq!(bucket.admit(10, 1000, SECOND), Ok(Admission::Ready));
    assert_eq!(bucket.admit(1, 1000, SECOND), Err(RateError::Capacity));
    assert_eq!(bucket.complete(11, 1000), Err(RateError::Capacity));
    assert_eq!(bucket.complete(10, 999), Err(RateError::ClockRollback));
    bucket.complete(10, 1000).unwrap();
}

#[test]
fn integer_credit_handles_maximum_clock_without_overflow_or_floats() {
    let mut bucket = EntityRate::empty(0);
    assert_eq!(
        bucket.admit(BURST_BYTES, u64::MAX - 1, u64::MAX),
        Ok(Admission::Ready)
    );
    bucket.complete(BURST_BYTES, u64::MAX - 1).unwrap();
    assert_eq!(
        bucket.admit(1, u64::MAX - 1, u64::MAX),
        Err(RateError::Deadline)
    );
}
