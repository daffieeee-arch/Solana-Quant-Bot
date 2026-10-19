//! Python's int-against-float comparisons, exactly (spec D7). Python compares an int with a float by value;
//! `(i as f64) >= x` rounds i first, which differs above 2^53. fwd1053 has pool reserves up to 1e18, so F7's
//! `a >= 0.04 * depth`, the farm share `farm > 0.5 * vol` and N2's pool band `0.8 * depth <= dep <= 1.2 * depth`
//! compare an exact integer with the f64 Python computed (`0.04 * (depth as f64)` rounds as Python's int.__float__).
//!
//! For an integer i: i >= x when i >= ceil(x); i > x when i > floor(x); i <= x when i <= floor(x). All false on NaN.

/// 2^127: every i128 is below it, and -2^127 is i128::MIN.
const TWO_127: f64 = 170_141_183_460_469_231_731_687_303_715_884_105_728.0;

/// i >= x, exactly.
pub fn ge(i: i128, x: f64) -> bool {
    if x.is_nan() || x >= TWO_127 {
        return false;
    }
    x < -TWO_127 || i >= x.ceil() as i128
}

/// i > x, exactly.
pub fn gt(i: i128, x: f64) -> bool {
    if x.is_nan() || x >= TWO_127 {
        return false;
    }
    x < -TWO_127 || i > x.floor() as i128
}

/// i <= x, exactly.
pub fn le(i: i128, x: f64) -> bool {
    if x.is_nan() || x < -TWO_127 {
        return false;
    }
    x >= TWO_127 || i <= x.floor() as i128
}

#[cfg(test)]
mod tests {
    use super::*;

    const P53: i128 = 1 << 53;

    #[test]
    fn small_values_compare_as_usual() {
        assert!(ge(3, 3.0) && ge(3, 2.5) && !ge(2, 2.5) && ge(-2, -2.5) && !ge(-3, -2.5));
        assert!(gt(3, 2.5) && !gt(3, 3.0) && gt(-2, -2.5) && !gt(-3, -2.5));
        assert!(le(3, 3.0) && le(2, 2.5) && !le(3, 2.5) && le(-3, -2.5) && !le(-2, -2.5));
        assert!(ge(0, -0.0) && le(0, -0.0) && !gt(0, -0.0));
    }

    #[test]
    fn above_2_53_the_integer_is_not_rounded() {
        // lab-py: 2**53 + 1 > 2.0**53 is True; float(2**53 + 1) > 2.0**53 is False.
        let x = P53 as f64;
        assert!(ge(P53 + 1, x) && gt(P53 + 1, x) && !le(P53 + 1, x));
        assert!(!gt((P53 + 1) as f64 as i128, x)); // what the naive comparison sees
        assert!(!ge(-P53 - 1, -x) && !gt(-P53 - 1, -x) && le(-P53 - 1, -x));
        // 0.04 * (10**18 + 7) is 4e16 exactly; 4e16 - 1 >= it is False in Python, True after float().
        let x = 0.04 * ((1_000_000_000_000_000_007_i128) as f64);
        assert_eq!(x, 4e16);
        assert!(!ge(39_999_999_999_999_999, x) && ge(40_000_000_000_000_000, x));
        assert!(!le(40_000_000_000_000_001, x) && gt(40_000_000_000_000_001, x));
        // The N2 pool band edge: 1.2 * (2**60 + 3) is 0x1.3333333333333p+60.
        let hi = 1.2 * (((1_i128 << 60) + 3) as f64);
        assert_eq!(hi.to_bits(), 0x43b3_3333_3333_3333);
        let h = hi as i128;
        assert!(le(h, hi) && !le(h + 1, hi) && ge(h, hi) && !ge(h - 1, hi));
    }

    #[test]
    fn around_2_63_and_the_i128_range() {
        // lab-py: 2**63 - 1 >= 2.0**63 is False (float(2**63 - 1) is 2.0**63).
        let x = 9_223_372_036_854_775_808.0;
        assert!(!ge((1 << 63) - 1, x) && le((1 << 63) - 1, x) && ge(1 << 63, x) && !gt(1 << 63, x) && gt((1 << 63) + 1, x));
        assert!(!ge(i128::MAX, f64::INFINITY) && !gt(i128::MAX, 2f64.powi(127)) && le(i128::MAX, 2f64.powi(127)));
        assert!(ge(i128::MIN, f64::NEG_INFINITY) && gt(i128::MIN, -2f64.powi(128)) && !le(i128::MIN, -2f64.powi(128)));
        assert!(ge(i128::MIN, -2f64.powi(127)) && !gt(i128::MIN, -2f64.powi(127)) && le(i128::MIN, -2f64.powi(127)));
    }

    #[test]
    fn nan_is_false_for_all() {
        for i in [i128::MIN, -1, 0, 1, i128::MAX] {
            assert!(!ge(i, f64::NAN) && !gt(i, f64::NAN) && !le(i, f64::NAN));
        }
    }
}
