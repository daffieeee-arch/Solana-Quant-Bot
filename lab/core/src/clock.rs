//! Milliseconds to slots per epoch, the F7 constants, the decision bounds and the journal's UTC day: the engine's
//! clock (backtest/strategies.py `Clock`, :46-69; `decision_bounds`, :346-348; paper/engine.py:250-253, `_day`).
//! M1 C1b of lab/docs/rust-live-route-design-2026-10-16.md; the porting spec is research/m1-c1b-signals-spec-2026-10-19.md.
//!
//! - The ms constants are computed as Python computes them (`50 * 267.3` in f64), so their bits are Python's.
//! - `slots` rounds half to even, as Python's `round`; `f64::round` would round 2.5 up.
//! - A replay's clock is the store's static map (meta.json `ms_per_slot`). The live clock that measures epochs as it
//!   goes (paper/live.py:645-649) is M3.

use std::collections::BTreeMap;

/// Lamports per SOL (strategies.py:26, costs.py:5), as i128: every amount F7 compares is an i128.
pub const SOL: i128 = 1_000_000_000;
/// The slot time the v1 windows were set at (strategies.py:29).
pub const REF_MS: f64 = 267.3;
pub const EPOCH_SLOTS: u64 = 432_000;

/// A v1 window given in slots, as milliseconds (strategies.py `ms`): `slots * REF_MS` in f64. The constants below
/// are the same products (`ms(50)` is `50.0 * REF_MS`).
pub fn ms(slots: u64) -> f64 {
    slots as f64 * REF_MS
}

pub const HOUR_MS: f64 = 3_600_000.0;
/// About a day without decisions while token state warms up; also the curve age floor (engine.py:253).
pub const BURN_IN_MS: f64 = 324_000.0 * REF_MS;
/// No decisions this close to the end of the data.
pub const END_GUARD_MS: f64 = 20_000.0 * REF_MS;
pub const F7_INFLOW_MS: f64 = 50.0 * REF_MS;
pub const F7_CREATOR_MS: f64 = 200.0 * REF_MS;
pub const F7_GAP_MS: f64 = 20.0 * REF_MS;
/// A pool is flagged when farmers made more than F6_FARM_SHARE of its SOL volume in this window.
pub const F6_FARM_MS: f64 = 3_600_000.0;
pub const F6_FARM_SHARE: f64 = 0.5;
/// Recent legs kept per key (engine.py:74). It also caps the creator window below 133.65 ms a slot.
pub const HISTORY_SLOTS: u64 = 400;
/// pool_seen of a pool without a trade for this long is forgotten (engine.py:79).
pub const POOL_SEEN_KEEP_MS: f64 = 86_400_000.0;
/// Live only: a pool first seen this close to its creation was followed since it was created (engine.py:78).
pub const SINCE_CREATION_MS: f64 = 60_000.0;

/// Milliseconds to slots per epoch, from the store's measured ms per slot (strategies.py `Clock`).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Clock {
    /// epoch -> ms per slot (meta.json `ms_per_slot`, its keys parsed as integers).
    pub per_epoch: BTreeMap<u64, f64>,
}

impl Clock {
    pub fn new(per_epoch: BTreeMap<u64, f64>) -> Self {
        Clock { per_epoch }
    }

    /// The epoch's measured slot time; an epoch not measured takes the last measured epoch before it, then REF_MS
    /// (strategies.py:57-66).
    pub fn ms_per_slot(&self, slot: u64) -> f64 {
        let e = slot / EPOCH_SLOTS;
        match self.per_epoch.get(&e) {
            Some(&v) => v,
            None => self.per_epoch.range(..e).next_back().map_or(REF_MS, |(_, &v)| v),
        }
    }

    /// `max(1, round(duration_ms / ms_per_slot(at)))` with Python's half-even round (strategies.py:68-69).
    pub fn slots(&self, duration_ms: f64, at: u64) -> u64 {
        ((duration_ms / self.ms_per_slot(at)).round_ties_even() as u64).max(1)
    }

    /// The F7 windows at a slot (n_in, n_cr, n_gap) and the hour, for the summary.
    pub fn windows(&self, at: u64) -> [u64; 4] {
        [F7_INFLOW_MS, F7_CREATOR_MS, F7_GAP_MS, HOUR_MS].map(|d| self.slots(d, at))
    }
}

/// The decision bounds of a run (engine.py:250-253; strategies.py `decision_bounds`).
///
/// (dev0, dev1) is `--dev-bounds` or the snapshot's meta_bounds: the whole store's blocks [min, max + 1), never the
/// replay range. `burn` is the curve age floor and equals `dlo` (engine.py:253 uses `ms(324_000)`, which is
/// BURN_IN_MS). Decisions are made for dlo <= t <= dhi, both ends inclusive (engine.py:612).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bounds {
    pub dev0: u64,
    pub dev1: u64,
    pub burn: u64,
    pub dlo: u64,
    pub dhi: u64,
}

impl Bounds {
    /// Saturating at 0 and u64::MAX: a store too short to decide anything decides nothing either way.
    pub fn new((dev0, dev1): (u64, u64), clock: &Clock) -> Self {
        let dlo = dev0.saturating_add(clock.slots(BURN_IN_MS, dev0));
        let dhi = dev1.saturating_sub(clock.slots(END_GUARD_MS, dev1.saturating_sub(1)));
        Bounds { dev0, dev1, burn: dlo, dlo, dhi }
    }

    pub fn decides(&self, t: u64) -> bool {
        self.dlo <= t && t <= self.dhi
    }
}

/// The journal's day of a block time (engine.py `_day`): None for 0, as Python's `if block_time`.
pub fn day_of(block_time: i64) -> Option<String> {
    (block_time != 0).then(|| utc_date(block_time))
}

/// The UTC date "YYYY-MM-DD" of a unix time (H. Hinnant's civil_from_days, as lab/recorder/src/meter.rs:100-113).
pub fn utc_date(unix: i64) -> String {
    let z = unix.div_euclid(86_400) + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// dev2's and fwd1053's meta.json `ms_per_slot` (research/m1-c1b-signals-spec-2026-10-19.md §3.2, appendix).
    pub(crate) fn dev2() -> Clock {
        let v = [(1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
                 (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35)];
        Clock::new(v.into_iter().collect())
    }

    pub(crate) fn fwd1053() -> Clock {
        Clock::new([(1053, 218.29)].into_iter().collect())
    }

    #[test]
    fn the_ms_constants_have_pythons_bits() {
        // lab-py: float.hex of strategies.py's constants.
        assert_eq!(REF_MS.to_bits(), 0x4070_b4cc_cccc_cccd);
        assert_eq!(F7_INFLOW_MS.to_bits(), 0x40ca_1a80_0000_0000); // 13365.0
        assert_eq!(F7_CREATOR_MS.to_bits(), 0x40ea_1a80_0000_0000); // 53460.0
        assert_eq!(F7_GAP_MS.to_bits(), 0x40b4_e200_0000_0000); // 5346.0
        assert_eq!(BURN_IN_MS.to_bits(), 0x4194_a5f6_4000_0000); // 86605200.0
        assert_eq!(END_GUARD_MS.to_bits(), 0x4154_64b4_0000_0000); // 5346000.0
        assert_eq!((F7_INFLOW_MS, F7_CREATOR_MS, F7_GAP_MS, BURN_IN_MS, END_GUARD_MS), (13365.0, 53460.0, 5346.0, 86605200.0, 5346000.0));
        assert_eq!([ms(50), ms(200), ms(20), ms(324_000), ms(20_000)], [F7_INFLOW_MS, F7_CREATOR_MS, F7_GAP_MS, BURN_IN_MS, END_GUARD_MS]);
    }

    #[test]
    fn the_stores_slot_times_parse_to_pythons_bits() {
        // The values PR 3 must parse bit-exact from meta.json (D9); float.hex in lab-py.
        assert_eq!(265.83f64.to_bits(), 0x4070_9d47_ae14_7ae1);
        assert_eq!(267.83f64.to_bits(), 0x4070_bd47_ae14_7ae1);
        assert_eq!(218.29f64.to_bits(), 0x406b_4947_ae14_7ae1);
        assert_eq!("267.35".parse::<f64>().unwrap().to_bits(), 0x4070_b599_9999_999a);
    }

    #[test]
    fn the_windows_per_epoch_are_the_lab_clocks() {
        // lab-py Clock on the stores' meta: (n_in, n_cr, n_gap, H), d_ms 600 and 24 h.
        let (d, f) = (dev2(), fwd1053());
        assert_eq!((d.ms_per_slot(450_100_000), d.windows(450_100_000)), (265.83, [50, 201, 20, 13_542]));
        assert_eq!((d.ms_per_slot(450_200_000), d.windows(450_200_000)), (267.83, [50, 200, 20, 13_441]));
        assert_eq!((f.ms_per_slot(455_000_000), f.windows(455_000_000)), (218.29, [61, 245, 24, 16_492]));
        assert_eq!([d.slots(600.0, 450_100_000), d.slots(600.0, 450_200_000), f.slots(600.0, 455_000_000)], [2, 2, 3]);
        assert_eq!([d.slots(POOL_SEEN_KEEP_MS, 450_100_000), d.slots(POOL_SEEN_KEEP_MS, 450_200_000), f.slots(POOL_SEEN_KEEP_MS, 455_000_000)],
                   [325_020, 322_593, 395_804]);
        // The epoch boundary: the last slot of 1041 and the first of 1042.
        assert_eq!((d.windows(450_143_999)[1], d.windows(450_144_000)[1]), (201, 200));
    }

    #[test]
    fn slots_round_half_to_even_and_at_least_one() {
        // Python: round(600 / 240) = round(2.5) = 2; f64::round gives 3.
        let c = Clock::new([(1042, 240.0)].into_iter().collect());
        assert_eq!(c.slots(600.0, 450_200_000), 2);
        assert_eq!(c.slots(840.0, 450_200_000), 4); // 3.5 -> 4
        assert_eq!(c.slots(1.0, 450_200_000), 1); // round(0.004) = 0 -> 1
        assert_eq!(c.slots(0.0, 450_200_000), 1);
        assert_eq!(Clock::default().slots(F7_CREATOR_MS, 5), 200); // REF_MS: the v1 slot counts
    }

    #[test]
    fn an_unmeasured_epoch_takes_the_last_measured_one_before_it() {
        // lab-py: before the first measured epoch REF_MS; after the last the last; in a gap the last earlier one.
        let d = dev2();
        assert_eq!(d.ms_per_slot(1032 * EPOCH_SLOTS), 267.3);
        assert_eq!(d.ms_per_slot(2000 * EPOCH_SLOTS), 267.35);
        let gap = Clock::new([(10, 300.0), (12, 200.0)].into_iter().collect());
        let m = [11 * EPOCH_SLOTS + 5, 12 * EPOCH_SLOTS, 9 * EPOCH_SLOTS].map(|s| gap.ms_per_slot(s));
        assert_eq!(m, [300.0, 200.0, 267.3]);
    }

    #[test]
    fn bounds_are_pythons_decision_bounds() {
        // lab-py decision_bounds; burn = lo + slots(ms(324_000), lo) = dlo.
        let b = |dev, c: &Clock| { let b = Bounds::new(dev, c); assert_eq!(b.burn, b.dlo); (b.dlo, b.dhi) };
        assert_eq!(b((446_256_000, 452_304_000), &dev2()), (446_529_755, 452_284_004));
        assert_eq!(b((454_896_000, 455_328_000), &fwd1053()), (455_292_744, 455_303_510));
        // Live bounds (0, 2^63 - 1): the end guard at 2^63 - 2 falls back to the clock's last measured epoch.
        assert_eq!(b((0, (1 << 63) - 1), &fwd1053()), (324_000, 9_223_372_036_854_751_317));
        assert_eq!(b((0, (1 << 63) - 1), &dev2()), (324_000, 9_223_372_036_854_755_811));
        // The end guard is taken at dev1 - 1: with dev1 on an epoch boundary that is the previous epoch's slot time
        // (lab-py: 20,111 slots at 265.83 ms, not 19,960 at 267.83).
        let edge = Clock::new([(1041, 265.83), (1042, 267.83)].into_iter().collect());
        assert_eq!(b((446_256_000, 450_144_000), &edge), (446_580_000, 450_123_889));
        let dev = Bounds::new((446_256_000, 452_304_000), &dev2());
        assert!(!dev.decides(dev.dlo - 1) && dev.decides(dev.dlo) && dev.decides(dev.dhi) && !dev.decides(dev.dhi + 1));
    }

    #[test]
    fn days_are_utc_dates_and_none_for_zero() {
        // lab-py engine._day.
        assert_eq!(day_of(1_790_000_000).as_deref(), Some("2026-09-21"));
        assert_eq!(day_of(0), None);
        assert_eq!(day_of(1).as_deref(), Some("1970-01-01"));
        assert_eq!((utc_date(86_399), utc_date(86_400)), ("1970-01-01".into(), "1970-01-02".into()));
        assert_eq!(utc_date(951_782_400), "2000-02-29");
        assert_eq!(utc_date(4_102_444_800), "2100-01-01");
        assert_eq!(utc_date(-1), "1969-12-31");
        assert_eq!(utc_date(1_760_832_000), "2025-10-19");
    }
}
