//! The N2 shadow: the hour's candidates per bucket (engine.py `hour`, `hour_events`, `_hour_index` :686-709) and the
//! deterministic random pick per trigger (engine.py `_n2`, :711-724; strategies.py `_seed`, `_mix`, :573-581). M1 C1b;
//! the porting spec is research/m1-c1b-signals-spec-2026-10-19.md §3.8.
//!
//! - The hour is `S - S % h`, h the hour in slots at S. When the slot time changes at an epoch boundary the grid moves,
//!   so the hour can roll over mid-hour.
//! - Candidates are sorted by their key *string* and drawn with sha256 seeds. lab/core has only ids and no sha256
//!   (it stays dependency-free), so the caller supplies both through `N2Keys`.
//! - `mix` is the splitmix64 finalizer alone, without the generator's gamma step.

use crate::clock::{Clock, HOUR_MS, SOL};
use crate::exact::{ge, le};
use crate::legs::Id;
use crate::mem::{self, Mem};
use crate::universe::Segment;
use std::cmp::Ordering;

/// What N2 needs from the key strings (lab/live holds them).
pub trait N2Keys {
    /// The order of two keys' strings: Python's `sorted` on str (base58 is ASCII, so byte order).
    fn cmp(&self, a: Id, b: Id) -> Ordering;
    /// `_seed(key)`: the first 8 bytes of sha256(key), little-endian.
    fn key_seed(&mut self, k: Id) -> u64;
    /// `_seed("paper-N2", key, t)`: the first 8 bytes of sha256("paper-N2|{key}|{t}"), little-endian, t in decimal.
    fn trigger_seed(&mut self, k: Id, t: u64) -> u64;
}

/// The splitmix64 finalizer (strategies.py `_mix`), wrapping as numpy's uint64.
pub fn mix(mut x: u64) -> u64 {
    x = (x ^ (x >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    x ^ (x >> 31)
}

/// One candidate event: (key, slot, depth).
pub type HourEvent = (Id, u64, i128);

/// The current hour and its candidate events per bucket (curve, pool normal, pool reversed), in arrival order.
#[derive(Clone, Debug, Default)]
pub struct HourIndex {
    pub hour: Option<u64>,
    events: [Vec<HourEvent>; 3],
}

impl HourIndex {
    /// The hour of slot S on the clock at S (engine.py:687-688). When it differs from the current hour (the first call
    /// always does), the buckets are emptied and the hour's length in slots is returned: the caller then runs the
    /// rollover's prunes, with n taken at S.
    pub fn roll(&mut self, slot: u64, clock: &Clock) -> Option<u64> {
        let h = clock.slots(HOUR_MS, slot);
        let hour = slot - slot % h;
        if self.hour == Some(hour) {
            return None;
        }
        self.hour = Some(hour);
        self.events.iter_mut().for_each(Vec::clear);
        Some(h)
    }

    pub fn push(&mut self, seg: Segment, key: Id, slot: u64, depth: i128) {
        self.events[seg.index()].push((key, slot, depth));
    }

    pub fn events(&self, seg: Segment) -> &[HourEvent] {
        &self.events[seg.index()]
    }

    /// Entries in all buckets.
    pub fn len(&self) -> usize {
        self.events.iter().map(Vec::len).sum()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The memory log (C4-2): the hour's candidate events, emptied at every new hour.
    pub fn mem(&self) -> Mem {
        Mem::new("hour_events", self.len(), self.events.iter().map(mem::vec).sum())
    }

    /// The trigger's N2 matches: up to k keys of its bucket that traded this hour at a similar depth (engine.py `_n2`).
    /// The candidates are this hour's earlier slots and the keys before the trigger's in this slot.
    pub fn pick(&self, seg: Segment, key: Id, t: u64, depth: i128, k: usize, keys: &mut impl N2Keys) -> Vec<Id> {
        pick(self.events(seg), seg, key, t, depth, k, keys)
    }
}

/// N2 over a bucket's events (engine.py:711-724):
///
/// - the band is inclusive: curve depth +- 5 SOL (integers), pool 0.8 to 1.2 times depth (exact against the floats);
/// - the trigger's own key is no candidate; a key qualifies when any one of its events is in the band;
/// - the keys sorted by string; each drawn as mix(seed(key) ^ seed("paper-N2", trigger key, t));
/// - the first k by (draw, position): numpy's stable argsort.
pub fn pick(cands: &[HourEvent], seg: Segment, key: Id, t: u64, depth: i128, k: usize, keys: &mut impl N2Keys) -> Vec<Id> {
    if k == 0 {
        return Vec::new();
    }
    let in_band = |dep: i128| {
        if seg == Segment::Curve {
            depth - 5 * SOL <= dep && dep <= depth + 5 * SOL
        } else {
            ge(dep, 0.8 * depth as f64) && le(dep, 1.2 * depth as f64)
        }
    };
    let mut ids: Vec<Id> = cands.iter().filter(|&&(c, _, dep)| c != key && in_band(dep)).map(|c| c.0).collect();
    ids.sort_unstable();
    ids.dedup();
    if ids.is_empty() {
        return ids;
    }
    ids.sort_by(|&a, &b| keys.cmp(a, b));
    let seed = keys.trigger_seed(key, t);
    let mixed: Vec<u64> = ids.iter().map(|&c| mix(keys.key_seed(c) ^ seed)).collect();
    let mut order: Vec<usize> = (0..ids.len()).collect();
    order.sort_by_key(|&i| mixed[i]); // stable: equal draws keep the string order
    order.into_iter().take(k).map(|i| ids[i]).collect()
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::clock::tests::dev2;
    use std::collections::HashMap;

    /// N2Keys over fixed names and seeds: `names[id]`, `seeds[id]`, and trigger seeds by (id, t). The seeds come from
    /// lab-py (hashlib), so `pick` can be checked against Python's `_n2`.
    #[derive(Default)]
    pub(crate) struct FakeKeys {
        pub names: Vec<&'static str>,
        pub seeds: Vec<u64>,
        pub triggers: HashMap<(Id, u64), u64>,
    }

    impl N2Keys for FakeKeys {
        fn cmp(&self, a: Id, b: Id) -> Ordering {
            self.names[a as usize].cmp(self.names[b as usize])
        }
        fn key_seed(&mut self, k: Id) -> u64 {
            self.seeds[k as usize]
        }
        fn trigger_seed(&mut self, k: Id, t: u64) -> u64 {
            *self.triggers.get(&(k, t)).unwrap_or_else(|| panic!("no trigger seed for ({k}, {t})"))
        }
    }

    #[test]
    fn mix_is_the_splitmix64_finalizer() {
        // lab-py strategies._mix on uint64 (numpy).
        assert_eq!(mix(0), 0);
        assert_eq!(mix(1), 6_238_072_747_940_578_789);
        assert_eq!(mix(0x9E37_79B9_7F4A_7C15), 16_294_208_416_658_607_535);
        assert_eq!(mix(u64::MAX), 13_029_008_266_876_403_067);
        assert_eq!(mix(12_345_678_901_234_567_890), 14_636_805_034_154_084_279);
    }

    /// Five base58-like keys and their sha256 seeds, and the trigger seeds of key 0 at t, from lab-py:
    /// `_seed(name)` and `_seed("paper-N2", name, t)`; the expected picks are `Engine._n2`'s on the same hour_events.
    fn five() -> FakeKeys {
        let names = vec!["7orRHgZjwNmvpcSBHUMQ8wqW6rwKpqE1hJJR43AkQa1Z", "8iXGs3rSqshaWD9iCZEXu5XLvD5CUhuu7DSA7kd3Cg92",
                         "A1YQFKJoFcia2WUqkfFrEBnDbJZ2AFg7XiWq2unwNhAj", "2CFywHXDPjDK2iRQsb95vnjgncDUZeQKJ6MceJ4ALpdc",
                         "6Ljyr7EoLpPyvbWUZ6Urh126cYLxDXVsRoHve1WWxok9"];
        let mut f = FakeKeys { names, seeds: N2_SEEDS.to_vec(), triggers: HashMap::new() };
        f.triggers.insert((0, 450_197_213), N2_TRIGGER_SEED);
        f
    }

    const N2_SEEDS: [u64; 5] = [3_889_069_239_855_593_721, 16_848_164_125_403_183_493, 1_499_288_943_959_168_397,
                                348_353_214_577_540_706, 313_765_029_953_787_783];
    const N2_TRIGGER_SEED: u64 = 1_665_859_808_466_201_397;

    #[test]
    fn real_seeds_pick_as_pythons_n2() {
        // lab-py Engine._n2 on this curve bucket, trigger key 0 at t 450197213, depth 45 SOL: keys 1 (45), 2 (50; its
        // 39 is out), 3 (40), 4 (49; its 51 is out) qualify; key 0 is the trigger's own.
        let mut f = five();
        let t = 450_197_213;
        let ev: Vec<HourEvent> = [(1, 45), (2, 50), (0, 47), (3, 40), (4, 51), (4, 49), (2, 39)]
            .iter()
            .enumerate()
            .map(|(i, &(k, d))| (k, t - 10 + i as u64, d * SOL))
            .collect();
        let picks: Vec<Vec<Id>> = (1..=5).map(|k| pick(&ev, Segment::Curve, 0, t, 45 * SOL, k, &mut f)).collect();
        assert_eq!(picks, [vec![3], vec![3, 4], vec![3, 4, 1], vec![3, 4, 1, 2], vec![3, 4, 1, 2]]);
    }

    #[test]
    fn the_hour_rolls_on_the_clock_at_the_slot() {
        let c = dev2();
        let mut h = HourIndex::default();
        // The first call always rolls (hour None).
        assert_eq!(h.roll(450_140_000, &c), Some(13_542));
        h.push(Segment::Curve, 1, 450_140_000, 5);
        assert_eq!((h.hour, h.roll(450_140_001, &c), h.len()), (Some(450_136_080), None, 1));
        // At 450144000 the hour is 13441 slots and the grid moves: bucket 450136080 -> 450139090, mid-hour.
        assert_eq!(h.roll(450_144_000, &c), Some(13_441));
        assert_eq!((h.hour, h.len()), (Some(450_139_090), 0));
        assert_eq!(h.roll(450_152_530, &c), None);
        assert_eq!((h.roll(450_152_531, &c), h.hour), (Some(13_441), Some(450_152_531)));
    }

    #[test]
    fn the_band_is_inclusive_and_excludes_the_own_key() {
        // Equal draws keep the string order, so a FakeKeys with all seeds 0 returns the keys sorted.
        let mut f = FakeKeys { names: vec!["a", "b", "c", "d", "e", "f"], seeds: vec![0; 6], triggers: HashMap::new() };
        f.triggers.insert((0, 7), 0);
        let s = SOL;
        let curve = [(0, 1, 50 * s), (1, 1, 45 * s), (2, 2, 55 * s), (3, 3, 45 * s - 1), (4, 3, 55 * s + 1), (5, 4, 60 * s), (5, 5, 52 * s)];
        assert_eq!(pick(&curve, Segment::Curve, 0, 7, 50 * s, 10, &mut f), vec![1, 2, 5]); // 5: one of its events is in
        assert_eq!(pick(&curve, Segment::Curve, 0, 7, 50 * s, 2, &mut f), vec![1, 2]);
        assert_eq!(pick(&curve, Segment::Curve, 0, 7, 50 * s, 0, &mut f), Vec::<Id>::new());
        // Pool: 0.8 and 1.2 times depth, exact above 2^53. 0.8 * (10**18 + 7) and 1.2 * (10**18 + 7) are 8e17 and 1.2e18.
        let d: i128 = 1_000_000_000_000_000_007;
        let (lo, hi) = (800_000_000_000_000_000_i128, 1_200_000_000_000_000_000_i128);
        let pool = [(1, 1, lo), (2, 1, hi), (3, 1, lo - 1), (4, 1, hi + 1), (0, 1, d)];
        assert_eq!(pick(&pool, Segment::PoolReversed, 0, 7, d, 10, &mut f), vec![1, 2]);
        // A cast to f64 would round 8e17 - 1 up to 8e17 and let key 3 in; Python compares the int and the float exactly.
        assert!(((lo - 1) as f64) >= 0.8 * d as f64);
    }

    #[test]
    fn equal_draws_keep_the_string_order_not_the_id_order() {
        // ids in the reverse order of their names: Python sorts the candidate keys as strings before the stable argsort,
        // so with equal draws the picks come in name order (e, d, c, ...), never in id order.
        let mut f = FakeKeys { names: vec!["t", "e", "d", "c", "b", "a"], seeds: vec![0; 6], triggers: HashMap::new() };
        f.triggers.insert((0, 7), 0);
        let ev: Vec<HourEvent> = (1..=5).map(|k| (k, 1, 50 * SOL)).collect();
        assert_eq!(pick(&ev, Segment::Curve, 0, 7, 50 * SOL, 3, &mut f), vec![5, 4, 3]);
    }

    #[test]
    fn the_pick_follows_the_draw_then_the_position() {
        let mut f = FakeKeys { names: vec!["t", "b", "a", "c"], seeds: vec![0, 30, 10, 10], triggers: HashMap::new() };
        f.triggers.insert((0, 9), 0);
        let ev = [(1, 1, 0), (2, 1, 0), (3, 1, 0), (2, 2, 0)];
        // Sorted by name: a (2), b (1), c (3); draws mix(10), mix(30), mix(10): a and c tie, a first by position.
        let want: Vec<Id> = {
            let mut v = vec![(mix(10), 0, 2), (mix(30), 1, 1), (mix(10), 2, 3)];
            v.sort();
            v.into_iter().map(|x| x.2).collect()
        };
        assert_eq!(pick(&ev, Segment::Curve, 0, 9, 0, 3, &mut f), want);
        assert_eq!(pick(&ev, Segment::Curve, 0, 9, 0, 1, &mut f), want[..1]);
        // The trigger seed is xored in: with 30 ^ 10 the draws become mix(30), mix(10), mix(30).
        f.triggers.insert((0, 9), 30 ^ 10);
        let want: Vec<Id> = {
            let mut v = vec![(mix(30), 0, 2), (mix(10), 1, 1), (mix(30), 2, 3)];
            v.sort();
            v.into_iter().map(|x| x.2).collect()
        };
        assert_eq!(pick(&ev, Segment::Curve, 0, 9, 0, 3, &mut f), want);
    }
}
