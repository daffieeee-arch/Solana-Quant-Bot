// These offline validation tests require the dev/ci-test safety contract.
// The separately selected release fixture targets do not run this target.
#[test]
fn debug_assertions_remain_enabled() {
    assert!(std::hint::black_box(cfg!(debug_assertions)));
    assert!(std::panic::catch_unwind(|| debug_assert!(std::hint::black_box(false))).is_err());
}

#[test]
fn integer_overflow_still_panics() {
    let maximum = std::hint::black_box(u64::MAX);
    assert!(std::panic::catch_unwind(|| maximum + std::hint::black_box(1)).is_err());
}
