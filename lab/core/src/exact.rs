//! Python's int-against-float comparisons, exactly (spec D7). Python compares an int with a float by value;
//! `(i as f64) >= x` rounds i first, which differs above 2^53. fwd1053 has pool reserves up to 1e18, so F7's
//! `a >= 0.04 * depth`, the farm share `farm > 0.5 * vol` and N2's pool band `0.8 * depth <= dep <= 1.2 * depth`
//! compare an exact integer with the f64 Python computed (`0.04 * (depth as f64)` rounds as Python's int.__float__).
//!
//! For an integer i: i >= x when i >= ceil(x); i > x when i > floor(x); i <= x when i <= floor(x); i < x when not
//! i >= x. All false on NaN.
//!
//! Also Python's other int-and-float steps the position path takes (M1 C2): `int(i * f)` (`mul_trunc`) and int / int
//! (`true_div`), which Python rounds correctly while `(n as f64) / (d as f64)` rounds twice.

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

/// i < x, exactly.
pub fn lt(i: i128, x: f64) -> bool {
    !x.is_nan() && !ge(i, x)
}

/// `int(i * f)`: the int rounded to the nearest f64 (ties to even, as int.__float__), times f, truncated toward zero.
/// Python raises where the product is not finite; so does this (fail closed).
pub fn mul_trunc(i: i128, f: f64) -> i128 {
    let p = (i as f64) * f;
    assert!(p.is_finite() && p.abs() < TWO_127, "int({i} * {f}): the product {p} is no int");
    p as i128
}

const MANT: i32 = 53; // f64's significand bits (DBL_MANT_DIG)

/// `n / d` for ints, as CPython's long_true_divide: correctly rounded, half to even. Both within 2^53: one f64
/// division is already exact. Else the quotient is computed with 2 or 3 extra bits and a sticky bit, then rounded once
/// (`(n as f64) / (d as f64)` rounds n first and then the quotient, and differs from Python in about a quarter of the
/// cases at n ~ 1e21). `0 / -d` is -0.0, as Python's. d must be nonzero and below 2^73 (the shifted numerator stays in
/// u128; the position path divides by token and lamport amounts below 2^63).
pub fn true_div(n: i128, d: i128) -> f64 {
    assert!(d != 0, "division by zero");
    let neg = (n < 0) != (d < 0);
    let sign = |x: f64| if neg { -x } else { x };
    let (a, b) = (n.unsigned_abs(), d.unsigned_abs());
    if a == 0 {
        return sign(0.0);
    }
    if a <= 1 << MANT && b <= 1 << MANT {
        return sign(a as f64 / b as f64);
    }
    let bits = |x: u128| 128 - x.leading_zeros() as i32;
    assert!(bits(b) <= 73, "true_div: a divisor of {} bits", bits(b));
    // x = a * 2^-shift has a quotient by b of MANT + 2 or MANT + 3 bits (CPython: shift = diff - DBL_MANT_DIG - 2).
    let shift = bits(a) - bits(b) - MANT - 2;
    let mut inexact = false;
    let x = if shift <= 0 {
        a << (-shift) as u32
    } else {
        inexact = a & ((1u128 << shift) - 1) != 0;
        a >> shift
    };
    let mut q = x / b;
    inexact |= x % b != 0;
    let extra = bits(q) - MANT; // 2 or 3 bits to round away
    debug_assert!(extra == 2 || extra == 3, "{extra}");
    let mask = 1u128 << (extra - 1);
    // the bits to round away, the half bit (mask), the sticky bits below it and the last kept bit (2 * mask): CPython
    // takes the whole low digit, so a tie sees whether the kept part is odd (LAB review on #374)
    let low = (q & (4 * mask - 1)) | u128::from(inexact);
    if low & mask != 0 && low & (3 * mask - 1) != 0 {
        q += mask; // round half to even: above half, or exactly half with an odd last kept bit
    }
    q &= !(2 * mask - 1);
    // q has at most MANT significant bits (or is a power of two after a carry): exact as an f64, as is 2^shift
    sign(q as f64 * 2f64.powi(shift))
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
            assert!(!ge(i, f64::NAN) && !gt(i, f64::NAN) && !le(i, f64::NAN) && !lt(i, f64::NAN));
        }
    }

    #[test]
    fn less_than_is_exact_above_2_53() {
        // replay.py:659, 711: fill.tokens < expected.tokens * (1 - tol); lab-py: 2**53 < 2.0**53 + 2.0 is True.
        let x = (P53 + 2) as f64;
        assert!(lt(P53 + 1, x) && !lt(P53 + 2, x) && lt(P53, x));
        assert!(!lt((P53 + 1) as f64 as i128 + 2, x)); // (2^53 + 1) as f64 is 2^53, the naive side's view
        assert!(lt(i128::MAX, f64::INFINITY) && !lt(i128::MIN, f64::NEG_INFINITY) && lt(-1, -0.5) && !lt(0, -0.0));
    }

    #[test]
    fn mul_trunc_is_pythons_int_of_a_product() {
        // lab-py: int(500_000_000 * (1 + 0.005)) = 502499999 (1.005 is a hair below), as the pessimistic entry_total
        // shows; int(-7 * 0.5) = -3; int((2**53 + 1) * 1.0) = 2**53.
        assert_eq!((mul_trunc(500_000_000, 1.0 + 0.005), mul_trunc(-7, 0.5), mul_trunc(P53 + 1, 1.0)), (502_499_999, -3, P53));
        // costs.py per_tx: int((1 / 0.97 - 1) * 505000) = 15618
        assert_eq!(mul_trunc(505_000, 1.0 / 0.97 - 1.0), 15_618);
        assert!(std::panic::catch_unwind(|| mul_trunc(1, f64::INFINITY)).is_err());
        assert!(std::panic::catch_unwind(|| mul_trunc(1, f64::NAN)).is_err());
    }

    /// (n, d, the bits of Python's n / d): lab-py 3.13 on these pairs. The last twelve are random n in [1e20, 1e22) and
    /// d in [1e9, 1e12) where `float(n) / float(d)` differs from Python's `n / d`.
    const TRUE_DIV: [(i128, i128, u64); 23] = [
        (1, 3, 0x3fd5555555555555),
        (-1, 3, 0xbfd5555555555555),
        (0, -5, 0x8000000000000000),
        (9007199254740993, 3, 0x4325555555555556),
        (1000000000000000000007, 1000000000003, 0x41cdcd64ffff9db2),
        (-100000000000000000000000011, 999999937, 0xc37634579d50c609),
        (1267650600228229401496703205377, 1152921504606846975, 0x4270000000000000),
        (3541774862152233910272, 1180591620717411303424, 0x4008000000000000),
        (170141183460469231731687303715884105727, 4722366482869645213697, 0x4360000000000000),
        (123456789012345678901234567, 98765432109876543, 0x41d2a05f1d270000),
        (5, 9444732965739290427391, 0x3b84000000000000),
        (5753954285471724645447, 539316327100, 0x4203df5d895e50cd),
        (6133893002689957530457, 179799380744, 0x421fc5b2f590ce97),
        (5047359879066261078889, 674185688443, 0x41fbe3c7ace239bc),
        (6206756822351445698171, 615361993659, 0x4202c98bf8ad18ec),
        (4790351084582280057160, 234452101953, 0x42130765034483da),
        (3663522275978558698998, 504240363842, 0x41fb10da3cfeb1e0),
        (769346468035139684328, 752756018992, 0x41ce758b1cf918cd),
        (3307572934566759113152, 329376804260, 0x4202b45b5adf1f1e),
        (8812541583819993624129, 648625418298, 0x42094e8adbdc0639),
        (3866025599153060207046, 739935513951, 0x41f376c56916097c),
        (6417361675883944371168, 643632090574, 0x42029253586ef9bf),
        (3845052097659395125894, 59859223120, 0x422de96607f27185),
    ];

    /// Exact ties (the quotient halfway between two f64): lab-py rounds them to the even neighbour. The first eight are
    /// small (2^53 + 3 is 2^53 + 4, 2^53 + 1 is 2^53); the rest are (m + 1/2) * 2^e times k over k at random scales.
    const TIES: [(i128, i128, u64); 20] = [
        (9007199254740995, 1, 0x4340000000000002),
        (27021597764222985, 3, 0x4340000000000002),
        (1152921504606847360, 1, 0x43b0000000000002),
        (9007199254740993, 1, 0x4340000000000000),
        (18014398509481986, 1, 0x4350000000000000),
        (18014398509481990, 1, 0x4350000000000002),
        (-9007199254740995, 1, 0xc340000000000002),
        (9007199254740995, -1, 0xc340000000000002),
        (14385243481321580528, 632, 0x4354375b6a6ae768),
        (4682015779936591404859392, 282, 0x448c205b63e8540e),
        (1641785715696713481282650112, 162, 0x4520c41e7e5fe382),
        (2903452557338029060, 130, 0x4353d63879f74d60),
        (8172460749282509015040, 345, 0x43f48bdb817af708),
        (103840562859320335700383242387456, 244, 0x46157c711f07a224),
        (223523405277364105921953792, 63, 0x45077a87a114f27e),
        (146257092192158047272960, 5, 0x4498c6e3e9a462ce),
        (12904250573858523970535424, 756, 0x448cea8c64ffc604),
        (2399941153614413140530244354048, 574, 0x45ab05052af2003c),
        (300802573048699300714905600, 81, 0x4508931a7c213c12),
        (13956791393201396736000, 1000, 0x43e8360f7a6d7ff4),
    ];

    #[test]
    fn true_div_is_pythons() {
        for (n, d, want) in TRUE_DIV.into_iter().chain(TIES) {
            assert_eq!(true_div(n, d).to_bits(), want, "{n} / {d}");
            if n != 0 {
                assert_eq!(true_div(-n, -d).to_bits(), want, "{n} / {d} negated twice"); // (0 / 5 is +0.0)
            }
        }
        let naive_differs = TRUE_DIV[11..].iter().filter(|&&(n, d, w)| ((n as f64) / (d as f64)).to_bits() != w).count();
        assert_eq!(naive_differs, 12); // the cases where double rounding is wrong
        assert!(std::panic::catch_unwind(|| true_div(1, 0)).is_err());
    }

    /// The exact quotient's neighbours: true_div(n, d) is the f64 nearest to n / d, by integer arithmetic; on an exact tie
    /// only the even neighbour (its last significand bit 0) counts as nearest.
    fn nearest(n: i128, d: i128, q: f64) -> bool {
        // n / d against the midpoints between q and its neighbours, for n, d > 0 and q = m * 2^e with m < 2^53
        let (m, e) = {
            let b = q.to_bits();
            let exp = ((b >> 52) & 0x7ff) as i32;
            ((b & ((1 << 52) - 1)) | (1 << 52), exp - 1075)
        };
        // |n / d - m 2^e| <= 2^(e-1) <=> |n 2^-e - m d| * 2 <= d (scaled to integers on the larger side)
        let (lhs, rhs) = if e >= 0 { (n, (i128::from(m) * d) << e) } else { (n << (-e), i128::from(m) * d) };
        let (twice, step) = (2 * (lhs - rhs).abs(), if e >= 0 { d << e } else { d });
        twice < step || (twice == step && m % 2 == 0)
    }

    #[test]
    fn true_div_rounds_to_the_nearest_on_random_pairs() {
        let mut seed = 0x2026_1019_u64;
        let mut next = || {
            seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
            seed >> 11
        };
        for _ in 0..20_000 {
            let d = i128::from(next() % 1_000_000_000_000 + 1);
            let n = (i128::from(next()) * i128::from(next() % 100_000 + 1)).max(d); // up to ~1.8e18, quotients >= 1
            assert!(nearest(n, d, true_div(n, d)), "{n} / {d}");
        }
        // and on constructed exact ties: (m + 1/2) * 2^e * k / k, odd and even m
        for _ in 0..20_000 {
            let m = i128::from(next() % (1 << 52)) + (1 << 52);
            let e = (next() % 40 + 1) as u32;
            let k = i128::from(next() % 1_000 + 1);
            let n = k * ((2 * m + 1) << (e - 1));
            assert!(nearest(n, k, true_div(n, k)), "tie {n} / {k}");
        }
    }
}
