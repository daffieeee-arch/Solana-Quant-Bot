//! Signal rows of the Python paper engine on small synthetic feeds, and SignalEngine on the same feeds.
//!
//! The expectations are the engine's own output (paper/engine.py at f95bf33, unmodified): a generator ran
//! `Engine.run` on a stub feed (`all_pools = True`, as ReplayFeed) per case with the C1b reference config (spec D3:
//! variants 0.04:600, d_ms 600, ignore_risk, the primary K3 with the case's n2_k, and group H) and read the journal's
//! signals back, so the last write wins. Keys are base58 of sha256(label); the seeds are lab-py's `_seed(key)` and
//! `_seed("paper-N2", key, t)` for every (key, slot) with an event. Statuses are listed per case, not compared (D10).
//! A gate is the reason when it is a farm gate, else "pass".
//!
//! - s1: curves and pools around dev2's 1041 -> 1042 clock change: the universe cases (quote NULL, PUMP quote, mayhem,
//!   absent mint, age; pool creator rules), the H gates, a mid-hour rollover after a trigger, an N2 collision, the
//!   rollover order, a slot without a block time.
//! - s2: the 24 h pool_seen prune reads pool_last.
//! - s3: the decision-bound edges dlo - 1, dlo, dhi, dhi + 1.
//! - t2: a pool modelled on 8iXGs3rS… as stored ('other': no rows) and as a 'sol' pool (one F7 row).
//! - x1-x7 (from the C1b review): a pool before a curve in a new hour's first slot, a pool creator's sell after the
//!   trigger leg in slot t, a pool's N2 depth from a deposit, the strict edge of the 24 h prune, and the three farm
//!   prunes where n grows at an epoch change (dev2 1036 -> 1037).
//!
//! The farm windows are compared item by item, so a prune that drops items without changing a label shows too.
//!
//! The generator is lab/core/tools/signal_cases.py (run from lab/ with the lab Python; it prints the data below).

use super::*;
use crate::clock::Bounds;
use crate::f7::tests::{curve, pool};
use crate::legs::{PoolKind, PoolLeg, FARMER};
use crate::n2::tests::FakeKeys;
use crate::n2::HourEvent;
use crate::universe::{MintMeta, PoolMeta};

/// A leg's fields: a curve trade (is_buy, q, post vq, post rq) or a pool event (kind, base, quote_net, pre b, pre e,
/// farmer).
enum L {
    C(bool, u64, u64, u64),
    P(PoolKind, i64, i64, i64, i64, bool),
}
use L::{C, P};

/// (mint, create_slot, creator, mayhem, quote_mint)
type Mint = (Id, Option<u64>, Id, Option<bool>, Option<&'static str>);
/// (slot, tx, block_time, key, trader, leg)
type Ev = (u64, u32, Option<i64>, Id, Id, L);
/// (family, variant, key, t, day, orientation, depth, trigger_trader, farm, pool_seen_slot, farm_window_complete, gate)
type Row = (&'static str, &'static str, Id, u64, Option<&'static str>, &'static str, i128, Option<Id>, &'static str, Option<u64>,
            Option<u8>, &'static str);
/// A farm window item: (slot, tx, SOL amount, farmer SOL amount)
type Item = (u64, u32, i128, i128);

struct Case {
    names: &'static [&'static str],
    seeds: &'static [u64],
    trigger_seeds: &'static [(Id, u64, u64)],
    clock: &'static [(u64, f64)],
    dev: (u64, u64),
    n2_k: usize,
    /// Python's (dlo, dhi).
    bounds: (u64, u64),
    mints: &'static [Mint],
    /// (pool, reversed, mayhem, the id of COALESCE(mint, pool), coin_creator)
    pools: &'static [(Id, bool, bool, Option<Id>, Id)],
    /// In chain order.
    events: &'static [Ev],
    expect: &'static [Row],
    /// The engine's hour_events at the end.
    hour_events: &'static [(Segment, &'static [HourEvent])],
    /// The engine's farm_win at the end, per pool.
    farm: &'static [(Id, &'static [Item])],
}

/// The reference groups: K3 (primary, everywhere, farm all) and H (pool_reversed, organic, require_farm_complete).
pub(super) fn groups(n2_k: usize) -> Vec<GroupSpec> {
    vec![
        GroupSpec {
            name: "K3".into(), primary: true, variants: vec!["tp4_T600".into()], segments: None, organic: false, n2_k,
            require_farm_complete: false, d: 1, d_ms: Some(600),
        },
        GroupSpec {
            name: "H".into(), primary: false, variants: vec!["flow300_H6".into(), "flow300_tp100_H6".into()],
            segments: Some([false, false, true]), organic: true, n2_k: 0, require_farm_complete: true, d: 1, d_ms: Some(600),
        },
    ]
}

fn events(c: &Case) -> Vec<Event> {
    c.events
        .iter()
        .map(|&(slot, tx, bt, key, trader, ref l)| {
            let leg = match *l {
                L::C(is_buy, q, vq, rq) => curve((slot, tx), key, trader, is_buy, q, vq, rq),
                L::P(kind, base, quote_net, b, e, farmer) => match pool((slot, tx), key, trader, kind, (base, quote_net), (b, e)) {
                    Leg::Pool(p) => Leg::Pool(PoolLeg { flags: if farmer { FARMER } else { 0 }, ..p }),
                    leg => leg,
                },
            };
            Event::new(leg, bt)
        })
        .collect()
}

/// SignalEngine on a case's feed, with a trace; the rows as emitted (overwrites included).
fn run(c: &Case) -> (SignalEngine, Vec<SignalRow>) {
    let clock = Clock::new(c.clock.iter().copied().collect());
    let bounds = Bounds::new(c.dev, &clock);
    assert_eq!((bounds.dlo, bounds.dhi), c.bounds);
    let mut u = Universe::new(bounds, 0); // MAYHEM_AGENT is id 0 in every case
    for &(id, create, creator, mayhem, quote) in c.mints {
        u.add_mint(id, MintMeta::new(create, creator, mayhem, quote));
    }
    for &(id, reversed, mayhem, mint_tok, coin_creator) in c.pools {
        u.add_pool(id, PoolMeta { reversed, mayhem, mint_tok, coin_creator });
    }
    let mut eng = SignalEngine::new(u, clock, groups(c.n2_k), WindowRule::AllPools).unwrap();
    eng.trace = Some(Vec::new());
    let mut keys = FakeKeys {
        names: c.names.to_vec(),
        seeds: c.seeds.to_vec(),
        triggers: c.trigger_seeds.iter().map(|&(k, t, s)| ((k, t), s)).collect(),
    };
    let mut rows = Vec::new();
    for ev in events(c) {
        eng.push(&ev, &mut keys, &mut |r| rows.push(r)).unwrap();
    }
    eng.finish(&mut keys, &mut |r| rows.push(r)).unwrap();
    (eng, rows)
}

/// A row's C1b columns: (family, variant, key, t, day, orientation, depth, trigger_trader, farm, pool_seen_slot,
/// farm_window_complete, gate).
type Cols = (&'static str, String, Id, u64, Option<String>, &'static str, i128, Option<Id>, &'static str, Option<u64>,
             Option<u8>, &'static str);

fn cols(r: &SignalRow) -> Cols {
    (r.family.as_str(), r.variant.clone(), r.key, r.t, r.day.clone(), r.seg.orientation(), r.depth,
     (r.trigger_trader != NONE).then_some(r.trigger_trader), r.farm.as_str(), r.pool_seen_slot, r.farm_window_complete,
     r.gate.reason().unwrap_or("pass"))
}

fn owned(r: &Row) -> Cols {
    (r.0, r.1.to_owned(), r.2, r.3, r.4.map(str::to_owned), r.5, r.6, r.7, r.8, r.9, r.10, r.11)
}

/// The rows as the journal keeps them: by (family, variant, key, t), the last write wins.
fn journal(rows: &[SignalRow]) -> BTreeMap<(&'static str, String, Id, u64), Cols> {
    rows.iter().map(|r| ((r.family.as_str(), r.variant.clone(), r.key, r.t), cols(r))).collect()
}

fn check(c: &Case) -> (SignalEngine, Vec<SignalRow>) {
    let (eng, rows) = run(c);
    let want: BTreeMap<_, Cols> = c.expect.iter().map(|r| ((r.0, r.1.to_owned(), r.2, r.3), owned(r))).collect();
    assert_eq!(journal(&rows), want);
    for seg in Segment::ALL {
        let py = c.hour_events.iter().find(|h| h.0 == seg).map_or(&[][..], |h| h.1);
        assert_eq!(eng.hours.events(seg), py, "{seg:?}");
    }
    // The farm windows item by item, so a prune that keeps the label but drops other items shows too.
    let mut farm: Vec<(Id, Vec<Item>)> = c
        .pools
        .iter()
        .filter_map(|p| eng.farm.items(p.0).map(|xs| (p.0, xs.iter().map(|x| (x.0, x.1.tx, x.2, x.3)).collect())))
        .collect();
    farm.sort_unstable_by_key(|x| x.0);
    let py: Vec<(Id, Vec<Item>)> = c.farm.iter().map(|&(p, xs)| (p, xs.to_vec())).collect();
    assert_eq!(farm, py);
    assert!(rows.iter().all(|r| r.farm_complete.is_none())); // no farm_seed in a replay
    (eng, rows)
}

#[test]
fn s1_mixed_equals_the_engine() {
    let (eng, rows) = check(&S1_MIXED);
    let c = &eng.counts;
    // Universe, asked once per key and slot: CX (absent), CM (mayhem), CN (quote NULL), CP (PUMP quote) and CO (age) at
    // 450136500, 450136690 and 450136700; PO (not a SOL market) at 450138380 and 450138400, PM (mayhem) there and at
    // 450136500.
    let r = |x: Reject| c.rejects[x as usize];
    assert_eq!([Reject::CurveAbsent, Reject::CurveMayhem, Reject::QuoteNull, Reject::QuoteNotSol, Reject::CurveAge].map(r), [3; 5]);
    assert_eq!([Reject::PoolAbsent, Reject::PoolMayhem, Reject::NoCreateSlot].map(r), [2, 3, 0]);
    // F7 passes: CA, CA2; PR1, PR3, PR4, PR2, PR5, PR6; PN1, PN2, PA (PN1 at 450138300 and PA at 450138200 blocked).
    assert_eq!((c.f7, c.overwrites), ([2, 3, 6], 1));
    assert_eq!(c.rows[Family::N2F7 as usize].iter().sum::<u64>(), 8); // 7 in the journal: one overwritten
    assert_eq!(c.windows.keys().copied().collect::<Vec<_>>(), [1041, 1042]);
    // The write order at 450146500: by key (PR3, PR4); per trigger K3's F7, its N2 pick, then H's F7 rows. PRC's N2 row
    // is written twice, PR4's (depth 520) last.
    let at: Vec<(Family, &str, Id, i128)> =
        rows.iter().filter(|r| r.t == 450_146_500).map(|r| (r.family, r.variant.as_str(), r.key, r.depth)).collect();
    let (s, b) = (400 * crate::clock::SOL, 520 * crate::clock::SOL);
    assert_eq!(at, [(Family::F7, "tp4_T600", 20, s), (Family::N2F7, "tp4_T600", 22, s), (Family::F7, "flow300_H6", 20, s),
                    (Family::F7, "flow300_tp100_H6", 20, s), (Family::F7, "tp4_T600", 21, b), (Family::N2F7, "tp4_T600", 22, b),
                    (Family::F7, "flow300_H6", 21, b), (Family::F7, "flow300_tp100_H6", 21, b)]);
    // An N2 row carries the trigger's depth and farm, no trader, and the N2 key's own pool_seen_slot: PRC's first slot,
    // not the trigger's 450146470.
    let n2 = rows.iter().rfind(|r| r.family == Family::N2F7 && r.t == 450_146_500).unwrap();
    assert_eq!((n2.key, n2.depth, n2.farm, n2.trigger_trader, n2.pool_seen_slot), (22, b, Farm::Organic, NONE, Some(450_136_500)));
    // The rollover after the first key's F7: at 450144003 CA2 triggers, then its hour index rolls (mid-hour, the
    // clock change); at 450152531 PR5 triggers, the hour rolls, then PR6 triggers.
    let tr = eng.trace.as_ref().unwrap();
    let hours: Vec<&Trace> = tr.iter().filter(|x| matches!(x, Trace::Hour { .. })).collect();
    assert_eq!(hours, [&Trace::Hour { slot: 450_136_500, venue: Venue::Curve, key: 10 },
                       &Trace::Hour { slot: 450_144_003, venue: Venue::Curve, key: 16 },
                       &Trace::Hour { slot: 450_152_531, venue: Venue::Pool, key: 23 }]);
    let i = tr.iter().position(|x| *x == Trace::Hour { slot: 450_152_531, venue: Venue::Pool, key: 23 }).unwrap();
    assert!(matches!(tr[i - 1], Trace::Trig { t: 450_152_531, key: 23, .. }) && matches!(tr[i + 1], Trace::Trig { t: 450_152_531, key: 24, .. }));
    assert_eq!(c.rollovers, 3);
}

#[test]
fn s2_pool_seen_follows_pool_last() {
    // PL1 kept trading: its row keeps pool_seen 450200000. PL2 was quiet for more than 24 h at X's rollover
    // (450550000): forgotten, so its row's pool_seen is its new first slot.
    let (eng, _) = check(&S2_POOL_SEEN);
    assert_eq!((eng.pool_seen.get(&1), eng.pool_seen.get(&2)), (Some(&450_200_000), Some(&450_600_000)));
}

#[test]
fn s3_decides_from_dlo_to_dhi_inclusive() {
    let (eng, _) = check(&S3_BOUNDS);
    assert_eq!(eng.counts.f7, [0, 0, 2]);
}

#[test]
fn t2_is_no_sol_market_and_signals_as_one() {
    let (eng, rows) = check(&T2_OTHER);
    assert_eq!((rows.len(), eng.hours.len(), eng.farm.pools(), eng.counts.rejects[Reject::PoolAbsent as usize]), (0, 0, 0, 2));
    let (_, rows) = check(&T2_SOL);
    assert_eq!(rows.len(), 1);
}

#[test]
fn x1_a_pool_before_a_curve_rolls_the_hour_first() {
    // At 450152531 (a bucket start) P (tx 0) triggers, its hour index rolls the hour, then C (tx 1) triggers: P's N2
    // picks Q from the old hour, C finds no candidate (D is gone). A curve-first order would pick D for C instead.
    let (eng, _) = check(&X1_POOL_THEN_CURVE);
    let tr = eng.trace.as_ref().unwrap();
    let s = 450_152_531;
    let at: Vec<&Trace> = tr.iter().filter(|x| matches!(x, Trace::Hour { slot, .. } | Trace::Trig { t: slot, .. } if *slot == s)).collect();
    assert!(matches!(at[..], [Trace::Trig { key: 6, .. }, Trace::Hour { venue: Venue::Pool, key: 6, .. }, Trace::Trig { key: 8, .. }]));
}

#[test]
fn x2_x6_x7_the_farm_prunes_take_n_where_python_does() {
    // dev2 1036 -> 1037: n 11352 -> 13510. x2: the lazy prune does not fire at L (n at L). x6: it fires and keeps from
    // L - n(L). x7: the rollover at S keeps a pool whose last trade is S - 12000 (n at S). Each trigger is flagged by a
    // farmer trade that n taken at another slot would drop.
    let c = crate::clock::tests::dev2();
    let e = 1037 * crate::clock::EPOCH_SLOTS;
    assert_eq!((c.slots(crate::clock::F6_FARM_MS, e - 1), c.slots(crate::clock::F6_FARM_MS, e)), (11_352, 13_510));
    for case in [&X2_LAZY_PRUNE_N_AT_LAST, &X6_LAZY_PRUNE_KEEPS_FROM_N_AT_LAST, &X7_ROLLOVER_N_AT_SLOT] {
        let (_, rows) = check(case);
        assert!(rows.iter().all(|r| r.farm == Farm::Flagged) && rows.len() == 3);
    }
}

#[test]
fn x3_a_pool_creator_sell_after_the_trigger_leg_blocks() {
    // R: the coin creator sells SOL-side at (t, 1), after the trigger leg (t, 0): no rows. R2: the same sell at t2 + 1:
    // three rows at t2.
    let (_, rows) = check(&X3_POOL_CREATOR_SELL_IN_T);
    assert!(rows.iter().all(|r| (r.key, r.t) == (5, 450_201_000)) && rows.len() == 3);
}

#[test]
fn x4_a_pools_n2_depth_is_its_slots_last_leg() {
    // Q's slot ends with a deposit (pre b 520 SOL): Q is outside P's band and P has no N2 row.
    let (eng, rows) = check(&X4_N2_DEPTH_LAST_LEG);
    assert_eq!(eng.hours.events(Segment::PoolReversed)[0], (6, 450_199_900, 520 * crate::clock::SOL));
    assert!(rows.iter().all(|r| r.family == Family::F7));
}

#[test]
fn x5_the_24h_prune_keeps_pool_last_on_the_edge() {
    // At S = 450407910, S - slots(24 h, S) = 450085317: PL (last trade there) keeps its pool_seen, PM (one slot
    // earlier) gets its new first slot.
    let (eng, _) = check(&X5_POOL_SEEN_EDGE);
    assert_eq!((eng.pool_seen.get(&4), eng.pool_seen.get(&5)), (Some(&450_085_317), Some(&450_408_030)));
}

/// s1_mixed: Mixed curves and pools around dev2's 1041 -> 1042 clock change; the hour grid moves at 450144000.
const S1_MIXED: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 creator, 2 coin, 3 tokcreator, 4 absentcoin, 5 trader1, 6 trader2, 7 trader3, 8 buyer,
    //   9 CA, 10 CB, 11 CO, 12 CN, 13 CP, 14 CM, 15 CX, 16 CA2, 17 CB2, 18 PR1, 19 PR2, 20 PR3, 21 PR4, 22 PRC, 23 PR5,
    //   24 PR6, 25 PRD, 26 PN1, 27 PN2, 28 PA, 29 PM, 30 PO, 31 TN1, 32 TN2, 33 absentmint, 34 farmer0, 35 farmer1,
    //   36 farmer2
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "DgX9xEoN7RZGWevFVCy13JuzKsnmAx9B3VLfvoJxwqKn",
        "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz", "46HSSXjqnRQLXSsGtB8NCUbXRgBKioSFTrpf4Dc27PFh",
        "CoAmuVDu4dJFKCDVraPgT9ibXG986PXGU7sX9T8vvzqB", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "2mt5opXwYMEVLrYiH6VkAouidkLoATE1mkJviuGkpFw3", "HPc8UWhisYKDZNUhw7xFojQHo6qH4c6pKqNdx3Sducp6",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "65JxD3Po5QtFKGsamyrUmxvhDg7f3CkYALoZ9HUQoZmY",
        "DtThmDRxQkEBonrQ4ivWSxfCv5tL7CV7r78GdZcVrk7z", "HznFUhBgBgLN5eiQhdKQUmH2hA7ZQdQrj6gBo2TzVcb6",
        "BkfSqZqyx8Xhq4BrsbvBt8AHXh8UUMhM2xoTw2hJAVFy", "GPEgg2bgBYpSS7LrFxadH5idhbGfHcZE2imB8ip26WqA",
        "BhEp3DLChhZPZr1Q5NqasVKGc1MYkg5FCXLP4ppwbJHR", "BgHSKdtvqfBP9TxgXhbrfDhkQRHUrB69dosx9curttmr",
        "9GzKhVChsXn1Yxy9bic4moStSbs8ZV19NBuGnGizTpJ4", "7apRVmXgzWBJ4GzmdH2ur5n4bu5DS87Qmxz1vwDurPqo",
        "64obnW5sGfUfpd5GSnCbdsVAPnGtHWa1tQJUKXj8BbhT", "2jw4AwzcVSfUM6ZH1yfaaffJfDWY5DwcACmYKpJCu3Rw",
        "5ujbsc9Rwj2eXnoCzb8uiTbtG5HCJFX2qRM6FB7Kj7Ko", "548dvNU8mBFDRcDwrjrLSjH1E11VcxYk5z7MYcBTiQ71",
        "2hoi5S34tBBrUaAGKjTjoq8KovfyazXAq7ocN2LbDZS6", "vZ2bUr54aZ3XTkUP9ui14WxHNauWiWgo1kVX3gVyXQW",
        "3YwM4LqE3rusWiYV99LWvMjGsGFRGNsGR3YHKUxCF294", "Bb1wGG1YkrYj2sHCsS5G3ruhYUFhvZiEs7vMgo3YxUiB",
        "G6vgbotQtyrqvp7G1xsV3bunzzaxaUj27414AJfdmxPb", "9rLeAmAr6iZrrvGr5Fyo8gRdRwgGuUnCuUC6MafGUhu6",
        "A4eAxnzwSMw4ft14u9Z6YAajnqfh4WRW8nsc38pDXeHf", "8F1ToGLmo6CddMMpxgVSZX1ot2g3ru2WGUDZPmSAcSdd",
        "8jG7SH8M4oghAz49ns7qBd7SDU57XtWrzih3iSHY8nHi", "4MaRBAunCyLmHysMB1vQY37dc3dv4aqeoTYoHvWMjdHX",
        "B9Aqbfcdngchpj6aTTmjUQkuArM7xv5H1ZuhA53mfkbG", "7xdWbw36hasZwSsdTWA6zLiEEmbGpFPzB6CeNybNnfy8",
        "FxyFmzeif77VVWk5r3iTcZ48QHzL25mXtrXSfjv8FP2u", "FEXPHq767EB2CnfurDawMbFxDScMxf66Sq3ZUKBPdoMu",
        "2QzZK8PPZTR3zMF91XK38d871sjLCNuEvG3Ye4oqwfHU",
    ],
    seeds: &[
        4271053018750731645, 13748525418858597173, 8141434438437126355, 6099392943997992218, 14824421928595032355,
        1685726804653979033, 6127977479432806642, 17426226416885555510, 3885450303126769205, 5853608528728919298,
        15634082467686070938, 17273232173589162008, 27533108283130299, 15263499352881168422, 4692748130926918633,
        14415499357525301945, 6341732615337065513, 9976909279681904061, 11671658484963864352, 7238894320158930348,
        3278500430634485256, 16985682420364692747, 15259768178606174663, 5874976114869379077, 13708615995477115493,
        9550522514178945706, 15483014792857294009, 9942313146950859409, 5614626279494395326, 17110313752555454720,
        11851439212034039915, 6978572681498765340, 3827731799773311201, 11640985811201085518, 12028920896407514907,
        4576720765619097587, 918198615551361880,
    ],
    trigger_seeds: &[
        (9, 450136690, 17999262700399658416), (9, 450136700, 16091849761221122259), (10, 450136500, 11130923853869681370),
        (11, 450136500, 1862534409665862036), (11, 450136690, 772480202387348496), (11, 450136700, 14779655666623273088),
        (12, 450136500, 7229618679409971863), (12, 450136690, 5863918578284071490), (12, 450136700, 6454828346522657865),
        (13, 450136500, 17266726787005239026), (13, 450136690, 10818490351556249131),
        (13, 450136700, 11937894670203265399), (14, 450136500, 18371449594506079192),
        (14, 450136690, 14797055555258775569), (14, 450136700, 4429209991573106799), (15, 450136500, 14541317481186623765),
        (15, 450136690, 11066824124398261382), (15, 450136700, 8787265854742786781), (16, 450143990, 5047234904678315636),
        (16, 450144003, 955460049695716375), (17, 450144003, 15776694997027485748), (18, 450136990, 6854191941840404089),
        (18, 450137000, 10977124746023931828), (19, 450145000, 9901826456252477630), (19, 450145001, 12570347071131887508),
        (19, 450145002, 8038556500409073047), (19, 450145100, 4626166153806429264), (19, 450150495, 13388173170600913959),
        (19, 450150500, 1853531840740426930), (20, 450146470, 18402403483549006409), (20, 450146500, 2504608055656087538),
        (21, 450146470, 6880311616620946780), (21, 450146500, 10722708467079833846), (22, 450136500, 17680808269306177376),
        (22, 450146000, 4427301249109937574), (23, 450152491, 17876063674094205247), (23, 450152531, 7471205731890015303),
        (24, 450152491, 9286544886933402479), (24, 450152531, 3164834968499556594), (25, 450150000, 10314423614533766151),
        (26, 450137980, 7124759934721474354), (26, 450137995, 12083764045418625128), (26, 450138000, 18443847197712817347),
        (26, 450138280, 8327031945072078234), (26, 450138297, 8442789225831164871), (26, 450138300, 4364496868646085585),
        (27, 450138080, 14400451257831522399), (27, 450138100, 15527917010558759401), (28, 450138180, 7698612117925311260),
        (28, 450138197, 13972191073810857795), (28, 450138200, 4158164012319461675), (28, 450138490, 8100723409184035038),
        (28, 450138500, 15439628365507733361), (29, 450136500, 6748452336644047712), (29, 450138380, 7537099282342985221),
        (29, 450138400, 15285323323997238646), (30, 450138380, 5561067099718865296), (30, 450138400, 3010500952788664061),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 1,
    bounds: (446529755, 452284004),
    mints: &[
        (9, Some(450100000), 1, Some(false), Some("11111111111111111111111111111111")),
        (10, Some(450100000), 1, Some(false), Some("11111111111111111111111111111111")),
        (16, Some(450100000), 1, Some(false), Some("11111111111111111111111111111111")),
        (17, Some(450100000), 1, Some(false), Some("11111111111111111111111111111111")),
        (11, Some(446000000), 1, Some(false), Some("11111111111111111111111111111111")),
        (12, Some(450100000), 1, Some(false), None),
        (13, Some(450100000), 1, Some(false), Some("pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn")),
        (14, Some(450100000), 1, Some(true), Some("11111111111111111111111111111111")),
        (31, Some(450100000), 3, Some(false), Some("pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn")),
        (32, Some(450100000), NONE, Some(false), Some("11111111111111111111111111111111")),
    ],
    pools: &[
        (18, true, false, Some(18), 2), (19, true, false, Some(19), 2), (20, true, false, Some(20), 2),
        (21, true, false, Some(21), 2), (22, true, false, Some(22), 2), (23, true, false, Some(23), 2),
        (24, true, false, Some(24), 2), (25, true, false, Some(25), 2), (26, false, false, Some(31), 2),
        (27, false, false, Some(32), 2), (28, false, false, Some(33), 4), (29, false, true, Some(29), 2),
    ],
    events: &[
        (450136500, 0, Some(1790054600), 10, 8, C(true, 1000000000, 100000000000, 40000000000)),
        (450136500, 1, Some(1790054600), 11, 8, C(true, 1000000000, 100000000000, 42000000000)),
        (450136500, 2, Some(1790054600), 12, 8, C(true, 1000000000, 100000000000, 44000000000)),
        (450136500, 3, Some(1790054600), 13, 8, C(true, 1000000000, 100000000000, 44000000000)),
        (450136500, 4, Some(1790054600), 14, 8, C(true, 1000000000, 100000000000, 44000000000)),
        (450136500, 5, Some(1790054600), 15, 8, C(true, 1000000000, 100000000000, 44000000000)),
        (450136500, 10, Some(1790054600), 22, 8, P(PoolKind::Sell, 1000000000, 0, 310000000000, 0, false)),
        (450136500, 11, Some(1790054600), 29, 8, P(PoolKind::Buy, 0, 1000000000, 0, 300000000000, false)),
        (450136690, 0, Some(1790054676), 9, 8, C(true, 6000000000, 100000000000, 43000000000)),
        (450136690, 0, Some(1790054676), 11, 8, C(true, 6000000000, 100000000000, 43000000000)),
        (450136690, 0, Some(1790054676), 12, 8, C(true, 6000000000, 100000000000, 43000000000)),
        (450136690, 0, Some(1790054676), 13, 8, C(true, 6000000000, 100000000000, 43000000000)),
        (450136690, 0, Some(1790054676), 14, 8, C(true, 6000000000, 100000000000, 43000000000)),
        (450136690, 0, Some(1790054676), 15, 8, C(true, 6000000000, 100000000000, 43000000000)),
        (450136700, 0, Some(1790054680), 9, 5, C(false, 5000000000, 100000000000, 40000000000)),
        (450136700, 0, Some(1790054680), 11, 5, C(false, 5000000000, 100000000000, 40000000000)),
        (450136700, 0, Some(1790054680), 12, 5, C(false, 5000000000, 100000000000, 40000000000)),
        (450136700, 0, Some(1790054680), 13, 5, C(false, 5000000000, 100000000000, 40000000000)),
        (450136700, 0, Some(1790054680), 14, 5, C(false, 5000000000, 100000000000, 40000000000)),
        (450136700, 0, Some(1790054680), 15, 5, C(false, 5000000000, 100000000000, 40000000000)),
        (450136990, 0, Some(1790054796), 18, 8, P(PoolKind::Sell, 20000000000, 0, 400000000000, 0, false)),
        (450137000, 0, Some(1790054800), 18, 6, P(PoolKind::Buy, 20000000000, 0, 420000000000, 0, false)),
        (450137980, 0, Some(1790055192), 26, 8, P(PoolKind::Buy, 0, 10000000000, 0, 500000000000, false)),
        (450137995, 0, Some(1790055198), 26, 2, P(PoolKind::Sell, 0, 1000000000, 0, 510000000000, false)),
        (450138000, 0, Some(1790055200), 26, 2, P(PoolKind::Sell, 0, 25000000000, 0, 509000000000, false)),
        (450138000, 1, Some(1790055200), 26, 7, P(PoolKind::Sell, 0, 20000000000, 0, 484000000000, false)),
        (450138080, 0, Some(1790055232), 27, 8, P(PoolKind::Buy, 0, 10000000000, 0, 500000000000, false)),
        (450138100, 0, Some(1790055240), 27, 2, P(PoolKind::Sell, 0, 25000000000, 0, 510000000000, false)),
        (450138100, 1, Some(1790055240), 27, 5, P(PoolKind::Sell, 0, 25000000000, 0, 485000000000, false)),
        (450138180, 0, Some(1790055272), 28, 8, P(PoolKind::Buy, 0, 10000000000, 0, 500000000000, false)),
        (450138197, 0, Some(1790055278), 28, 4, P(PoolKind::Sell, 0, 500000000, 0, 510000000000, false)),
        (450138200, 0, Some(1790055280), 28, 5, P(PoolKind::Sell, 0, 25000000000, 0, 509000000000, false)),
        (450138280, 0, Some(1790055312), 26, 8, P(PoolKind::Buy, 0, 10000000000, 0, 464000000000, false)),
        (450138297, 0, Some(1790055318), 26, 3, P(PoolKind::Sell, 0, 1000000000, 0, 474000000000, false)),
        (450138300, 0, Some(1790055320), 26, 5, P(PoolKind::Sell, 0, 20000000000, 0, 473000000000, false)),
        (450138380, 0, Some(1790055352), 30, 8, P(PoolKind::Buy, 0, 10000000000, 0, 500000000000, false)),
        (450138380, 0, Some(1790055352), 29, 8, P(PoolKind::Buy, 0, 10000000000, 0, 500000000000, false)),
        (450138400, 0, Some(1790055360), 30, 5, P(PoolKind::Sell, 0, 25000000000, 0, 510000000000, false)),
        (450138400, 0, Some(1790055360), 29, 5, P(PoolKind::Sell, 0, 25000000000, 0, 510000000000, false)),
        (450138490, 0, Some(1790055396), 28, 8, P(PoolKind::Buy, 0, 30000000000, 0, 484000000000, false)),
        (450138500, 0, Some(1790055400), 28, 5, P(PoolKind::Sell, 0, 25000000000, 0, 514000000000, false)),
        (450143990, 0, Some(1790057596), 16, 8, C(true, 6000000000, 100000000000, 41000000000)),
        (450144003, 0, Some(1790057601), 16, 5, C(false, 5000000000, 100000000000, 38000000001)),
        (450144003, 1, Some(1790057601), 17, 8, C(true, 1000000000, 100000000000, 43000000000)),
        (450145000, 0, Some(1790058000), 19, 34, P(PoolKind::Sell, 10000000000, 0, 250000000000, 0, true)),
        (450145001, 0, Some(1790058000), 19, 35, P(PoolKind::Sell, 10000000000, 0, 250000000000, 0, true)),
        (450145002, 0, Some(1790058000), 19, 36, P(PoolKind::Sell, 10000000000, 0, 250000000000, 0, true)),
        (450145100, 0, Some(1790058040), 19, 7, P(PoolKind::Buy, 1000000000, 0, 255000000000, 0, false)),
        (450146000, 0, None, 22, 8, P(PoolKind::Sell, 1000000000, 0, 450000000000, 0, false)),
        (450146470, 0, Some(1790058588), 20, 8, P(PoolKind::Sell, 20000000000, 0, 380000000000, 0, false)),
        (450146470, 1, Some(1790058588), 21, 8, P(PoolKind::Sell, 20000000000, 0, 500000000000, 0, false)),
        (450146500, 0, None, 20, 5, P(PoolKind::Buy, 16000000000, 0, 400000000000, 0, false)),
        (450146500, 1, Some(1790058600), 21, 6, P(PoolKind::Buy, 21000000000, 0, 520000000000, 0, false)),
        (450150000, 0, Some(1790060000), 25, 8, P(PoolKind::Sell, 1000000000, 0, 1000000000000, 0, false)),
        (450150495, 0, Some(1790060198), 19, 8, P(PoolKind::Sell, 5000000000, 0, 254000000000, 0, false)),
        (450150500, 0, Some(1790060200), 19, 6, P(PoolKind::Buy, 15000000000, 0, 259000000000, 0, false)),
        (450152491, 0, Some(1790060996), 23, 8, P(PoolKind::Sell, 20000000000, 0, 980000000000, 0, false)),
        (450152491, 1, Some(1790060996), 24, 8, P(PoolKind::Sell, 20000000000, 0, 1500000000000, 0, false)),
        (450152531, 0, Some(1790061012), 23, 5, P(PoolKind::Buy, 40000000000, 0, 1000000000000, 0, false)),
        (450152531, 1, Some(1790061012), 24, 6, P(PoolKind::Buy, 44000000000, 0, 1100000000000, 0, false)),
    ],
    expect: &[
        ("F7", "tp4_T600", 9, 450136700, Some("2026-09-22"), "curve", 45000000000, Some(5), "n/a", None, None, "pass"),
        ("N2_F7", "tp4_T600", 11, 450136700, Some("2026-09-22"), "curve", 45000000000, None, "n/a", None, None, "pass"),
        ("F7", "flow300_H6", 18, 450137000, Some("2026-09-22"), "reversed", 420000000000, Some(6), "organic", Some(450136990), Some(0), "farm window incomplete"),
        ("F7", "flow300_tp100_H6", 18, 450137000, Some("2026-09-22"), "reversed", 420000000000, Some(6), "organic", Some(450136990), Some(0), "farm window incomplete"),
        ("F7", "tp4_T600", 18, 450137000, Some("2026-09-22"), "reversed", 420000000000, Some(6), "organic", Some(450136990), Some(0), "pass"),
        ("F7", "tp4_T600", 26, 450138000, Some("2026-09-22"), "normal", 484000000000, Some(7), "organic", Some(450137980), Some(0), "pass"),
        ("F7", "tp4_T600", 27, 450138100, Some("2026-09-22"), "normal", 485000000000, Some(5), "organic", Some(450138080), Some(0), "pass"),
        ("N2_F7", "tp4_T600", 26, 450138100, Some("2026-09-22"), "normal", 485000000000, None, "organic", Some(450137980), Some(0), "pass"),
        ("F7", "tp4_T600", 28, 450138500, Some("2026-09-22"), "normal", 514000000000, Some(5), "organic", Some(450138180), Some(0), "pass"),
        ("N2_F7", "tp4_T600", 26, 450138500, Some("2026-09-22"), "normal", 514000000000, None, "organic", Some(450137980), Some(0), "pass"),
        ("F7", "tp4_T600", 16, 450144003, Some("2026-09-22"), "curve", 43000000001, Some(5), "n/a", None, None, "pass"),
        ("N2_F7", "tp4_T600", 10, 450144003, Some("2026-09-22"), "curve", 43000000001, None, "n/a", None, None, "pass"),
        ("F7", "flow300_H6", 21, 450146500, Some("2026-09-22"), "reversed", 520000000000, Some(6), "organic", Some(450146470), Some(0), "farm window incomplete"),
        ("F7", "flow300_tp100_H6", 21, 450146500, Some("2026-09-22"), "reversed", 520000000000, Some(6), "organic", Some(450146470), Some(0), "farm window incomplete"),
        ("F7", "tp4_T600", 21, 450146500, Some("2026-09-22"), "reversed", 520000000000, Some(6), "organic", Some(450146470), Some(0), "pass"),
        ("F7", "flow300_H6", 20, 450146500, Some("2026-09-22"), "reversed", 400000000000, Some(5), "organic", Some(450146470), Some(0), "farm window incomplete"),
        ("F7", "flow300_tp100_H6", 20, 450146500, Some("2026-09-22"), "reversed", 400000000000, Some(5), "organic", Some(450146470), Some(0), "farm window incomplete"),
        ("F7", "tp4_T600", 20, 450146500, Some("2026-09-22"), "reversed", 400000000000, Some(5), "organic", Some(450146470), Some(0), "pass"),
        ("N2_F7", "tp4_T600", 22, 450146500, Some("2026-09-22"), "reversed", 520000000000, None, "organic", Some(450136500), Some(0), "pass"),
        ("F7", "flow300_H6", 19, 450150500, Some("2026-09-22"), "reversed", 259000000000, Some(6), "flagged", Some(450145000), Some(1), "farm flagged"),
        ("F7", "flow300_tp100_H6", 19, 450150500, Some("2026-09-22"), "reversed", 259000000000, Some(6), "flagged", Some(450145000), Some(1), "farm flagged"),
        ("F7", "tp4_T600", 19, 450150500, Some("2026-09-22"), "reversed", 259000000000, Some(6), "flagged", Some(450145000), Some(1), "pass"),
        ("F7", "flow300_H6", 24, 450152531, Some("2026-09-22"), "reversed", 1100000000000, Some(6), "organic", Some(450152491), Some(1), "pass"),
        ("F7", "flow300_tp100_H6", 24, 450152531, Some("2026-09-22"), "reversed", 1100000000000, Some(6), "organic", Some(450152491), Some(1), "pass"),
        ("F7", "tp4_T600", 24, 450152531, Some("2026-09-22"), "reversed", 1100000000000, Some(6), "organic", Some(450152491), Some(1), "pass"),
        ("F7", "flow300_H6", 23, 450152531, Some("2026-09-22"), "reversed", 1000000000000, Some(5), "organic", Some(450152491), Some(1), "pass"),
        ("F7", "flow300_tp100_H6", 23, 450152531, Some("2026-09-22"), "reversed", 1000000000000, Some(5), "organic", Some(450152491), Some(1), "pass"),
        ("F7", "tp4_T600", 23, 450152531, Some("2026-09-22"), "reversed", 1000000000000, Some(5), "organic", Some(450152491), Some(1), "pass"),
        ("N2_F7", "tp4_T600", 25, 450152531, Some("2026-09-22"), "reversed", 1000000000000, None, "organic", Some(450150000), Some(1), "pass"),
        ("N2_F7", "tp4_T600", 23, 450152531, Some("2026-09-22"), "reversed", 1100000000000, None, "organic", Some(450152491), Some(1), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[(23, 450152531, 1000000000000), (24, 450152531, 1100000000000)]),
    ],
    farm: &[
        (19, &[
            (450145000, 0, 10000000000, 10000000000), (450145001, 0, 10000000000, 10000000000),
            (450145002, 0, 10000000000, 10000000000), (450145100, 0, 1000000000, 0), (450150495, 0, 5000000000, 0),
            (450150500, 0, 15000000000, 0),
        ]),
        (20, &[(450146470, 0, 20000000000, 0), (450146500, 0, 16000000000, 0)]),
        (21, &[(450146470, 1, 20000000000, 0), (450146500, 1, 21000000000, 0)]),
        (22, &[(450136500, 10, 1000000000, 0), (450146000, 0, 1000000000, 0)]),
        (23, &[(450152491, 0, 20000000000, 0), (450152531, 0, 40000000000, 0)]),
        (24, &[(450152491, 1, 20000000000, 0), (450152531, 1, 44000000000, 0)]),
        (25, &[(450150000, 0, 1000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None), ('skipped', 'busy'), ('skipped', 'farm flagged'), ('skipped', 'farm window incomplete')]

/// s2_pool_seen: The 24 h pool_seen prune reads pool_last (dev2, epochs 1042-1043).
const S2_POOL_SEEN: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 PL1, 2 PL2, 3 X, 4 coin, 5 buyer, 6 trader1
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "99Qfyqn8WNrK83b99bfXTqX3krap3D5tRKpAeYs3ro38",
        "GQpzMfC199pBnihVY9oKzZ4ou6YK6qxRoNJLk82enJxR", "65N9XRM5po8GpFU76FSCxBr4g98bHw9aEKQX4Sr5EPFi",
        "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz", "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG",
        "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
    ],
    seeds: &[
        4271053018750731645, 13895318557209122925, 3631851548037218354, 10692495523296687009, 8141434438437126355,
        3885450303126769205, 1685726804653979033,
    ],
    trigger_seeds: &[
        (1, 450200000, 9526194496063660354), (1, 450350000, 8454740063170798786), (1, 450500000, 12375935471852675563),
        (1, 450600010, 14230133320595333179), (1, 450600020, 8415634613635118420), (2, 450200000, 15873915124700643091),
        (2, 450600000, 6644815795885556256), (2, 450600010, 2313817794331403334), (3, 450550000, 5916621923590455635),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 0,
    bounds: (446529755, 452284004),
    mints: &[],
    pools: &[(1, true, false, Some(1), 4), (2, true, false, Some(2), 4), (3, true, false, Some(3), 4)],
    events: &[
        (450200000, 0, Some(1790080000), 1, 5, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (450200000, 1, Some(1790080000), 2, 5, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (450350000, 0, Some(1790140000), 1, 5, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (450500000, 0, Some(1790200000), 1, 5, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (450550000, 0, Some(1790220000), 3, 5, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (450600000, 0, Some(1790240000), 2, 5, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (450600010, 0, Some(1790240004), 1, 5, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (450600010, 0, Some(1790240004), 2, 6, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
        (450600020, 0, Some(1790240008), 1, 6, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
    ],
    expect: &[
        ("F7", "flow300_H6", 2, 450600010, Some("2026-09-24"), "reversed", 320000000000, Some(6), "organic", Some(450600000), Some(1), "pass"),
        ("F7", "flow300_tp100_H6", 2, 450600010, Some("2026-09-24"), "reversed", 320000000000, Some(6), "organic", Some(450600000), Some(1), "pass"),
        ("F7", "tp4_T600", 2, 450600010, Some("2026-09-24"), "reversed", 320000000000, Some(6), "organic", Some(450600000), Some(1), "pass"),
        ("F7", "flow300_H6", 1, 450600020, Some("2026-09-24"), "reversed", 320000000000, Some(6), "organic", Some(450200000), Some(1), "pass"),
        ("F7", "flow300_tp100_H6", 1, 450600020, Some("2026-09-24"), "reversed", 320000000000, Some(6), "organic", Some(450200000), Some(1), "pass"),
        ("F7", "tp4_T600", 1, 450600020, Some("2026-09-24"), "reversed", 320000000000, Some(6), "organic", Some(450200000), Some(1), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[
            (2, 450600000, 300000000000), (1, 450600010, 300000000000), (2, 450600010, 320000000000),
            (1, 450600020, 320000000000),
        ]),
    ],
    farm: &[
        (1, &[(450600010, 0, 20000000000, 0), (450600020, 0, 15000000000, 0)]),
        (2, &[(450600000, 0, 20000000000, 0), (450600010, 0, 15000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None)]

/// s3_bounds: The decision-bound edges dlo - 1, dlo, dhi, dhi + 1 (reversed pools; fwd1053's clock, live-like bounds).
const S3_BOUNDS: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 coin, 2 buyer, 3 trader1, 4 E0, 5 E1, 6 E2, 7 E3
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "AH6x9wXhBSVpz5jWRyutMHAHDZquj1ZGtNn8xpvdFdz", "3JHEc733bB5zqTNeibbmG5CwR9Exmn2FypAJaiQo2gKZ",
        "8czgZhFHkT8YMX2t5qFQwnmzuDy6VcjhQcmcbMDtqmXy", "2dVJfy4DCwWEjPzjKpsZV8QFhfkutLfk4wxjqU6K1yXk",
    ],
    seeds: &[
        4271053018750731645, 8141434438437126355, 3885450303126769205, 1685726804653979033, 4255485767233080684,
        15011624534951556435, 13337741246173430053, 17897536528104595812,
    ],
    trigger_seeds: &[
        (4, 455023989, 5777193571094939163), (4, 455023999, 3995906609732971403), (5, 455023990, 826778387148595632),
        (5, 455024000, 17783299290684195709), (6, 455375500, 13393357926646451015), (6, 455375510, 17140127669184198354),
        (7, 455375501, 16910011638661493021), (7, 455375511, 18025414964646404704),
    ],
    clock: &[(1053, 218.29)],
    dev: (454700000, 455400000),
    n2_k: 1,
    bounds: (455024000, 455375510),
    mints: &[],
    pools: &[
        (4, true, false, Some(4), 1), (5, true, false, Some(5), 1), (6, true, false, Some(6), 1),
        (7, true, false, Some(7), 1),
    ],
    events: &[
        (455023989, 0, Some(1792009595), 4, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (455023990, 0, Some(1792009596), 5, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (455023999, 0, Some(1792009599), 4, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
        (455024000, 0, Some(1792009600), 5, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
        (455375500, 0, Some(1792150200), 6, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (455375501, 0, Some(1792150200), 7, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (455375510, 0, Some(1792150204), 6, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
        (455375511, 0, Some(1792150204), 7, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
    ],
    expect: &[
        ("F7", "flow300_H6", 5, 455024000, Some("2026-10-14"), "reversed", 320000000000, Some(3), "organic", Some(455023990), Some(0), "farm window incomplete"),
        ("F7", "flow300_tp100_H6", 5, 455024000, Some("2026-10-14"), "reversed", 320000000000, Some(3), "organic", Some(455023990), Some(0), "farm window incomplete"),
        ("F7", "tp4_T600", 5, 455024000, Some("2026-10-14"), "reversed", 320000000000, Some(3), "organic", Some(455023990), Some(0), "pass"),
        ("N2_F7", "tp4_T600", 4, 455024000, Some("2026-10-14"), "reversed", 320000000000, None, "organic", Some(455023989), Some(0), "pass"),
        ("F7", "flow300_H6", 6, 455375510, Some("2026-10-16"), "reversed", 320000000000, Some(3), "organic", Some(455375500), Some(1), "pass"),
        ("F7", "flow300_tp100_H6", 6, 455375510, Some("2026-10-16"), "reversed", 320000000000, Some(3), "organic", Some(455375500), Some(1), "pass"),
        ("F7", "tp4_T600", 6, 455375510, Some("2026-10-16"), "reversed", 320000000000, Some(3), "organic", Some(455375500), Some(1), "pass"),
        ("N2_F7", "tp4_T600", 7, 455375510, Some("2026-10-16"), "reversed", 320000000000, None, "organic", Some(455375501), Some(1), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[
            (6, 455375500, 300000000000), (7, 455375501, 300000000000), (6, 455375510, 320000000000),
            (7, 455375511, 320000000000),
        ]),
    ],
    farm: &[
        (6, &[(455375500, 0, 20000000000, 0), (455375510, 0, 15000000000, 0)]),
        (7, &[(455375501, 0, 20000000000, 0), (455375511, 0, 15000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None), ('skipped', 'farm window incomplete')]

/// t2_other: T2, a pool modelled on 8iXGs3rS… (class 'other', normal, PUMP-quoted token, coin_creator 5cHQC…), as
/// stored: not a SOL market.
const T2_OTHER: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 t2mint, 2 5cHQC, 3 buyer, 4 seller, 5 8iXGs (T2), 6 t2creator
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "DB7fBB6Cj1do2bwYTE6yFBMBbEpuge1p93AAZX1xXpBV",
        "FBZyTSj3oA22CZYnKHrSjk4UznEjNwUETkrRH11NEwTm", "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG",
        "C3nuLmBXJxkW4j8Ynx75KhSm5p5eDQ7jELM55oJteMfP", "8iXGs3rSqshaWD9iCZEXu5XLvD5CUhuu7DSA7kd3Cg92",
        "EMVCJrfkxyfS1DsQx8SztP6cTrwxKU2pVH2b5qFRvgzs",
    ],
    seeds: &[
        4271053018750731645, 17576817509869192156, 9066582049340593710, 3885450303126769205, 10045694772211852626,
        16848164125403183493, 12893943211481798525,
    ],
    trigger_seeds: &[(5, 450145098, 16348766208291932271), (5, 450145128, 9133285012746616007)],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 1,
    bounds: (446529755, 452284004),
    mints: &[(1, Some(450100000), 6, Some(false), Some("pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"))],
    pools: &[],
    events: &[
        (450145098, 0, Some(1790058039), 5, 3, P(PoolKind::Buy, 0, 40000000000, 0, 2524283573690, false)),
        (450145128, 0, Some(1790058051), 5, 4, P(PoolKind::Sell, 0, 195002882750, 0, 2564283573690, false)),
    ],
    expect: &[
    ],
    hour_events: &[
    ],
    farm: &[],
};
// Python's statuses: []

/// t2_sol: the same legs with a 'sol' PoolMeta: one F7 row.
const T2_SOL: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 t2mint, 2 5cHQC, 3 buyer, 4 seller, 5 8iXGs (T2), 6 t2creator
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "DB7fBB6Cj1do2bwYTE6yFBMBbEpuge1p93AAZX1xXpBV",
        "FBZyTSj3oA22CZYnKHrSjk4UznEjNwUETkrRH11NEwTm", "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG",
        "C3nuLmBXJxkW4j8Ynx75KhSm5p5eDQ7jELM55oJteMfP", "8iXGs3rSqshaWD9iCZEXu5XLvD5CUhuu7DSA7kd3Cg92",
        "EMVCJrfkxyfS1DsQx8SztP6cTrwxKU2pVH2b5qFRvgzs",
    ],
    seeds: &[
        4271053018750731645, 17576817509869192156, 9066582049340593710, 3885450303126769205, 10045694772211852626,
        16848164125403183493, 12893943211481798525,
    ],
    trigger_seeds: &[(5, 450145098, 16348766208291932271), (5, 450145128, 9133285012746616007)],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 1,
    bounds: (446529755, 452284004),
    mints: &[(1, Some(450100000), 6, Some(false), Some("pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"))],
    pools: &[(5, false, false, Some(1), 2)],
    events: &[
        (450145098, 0, Some(1790058039), 5, 3, P(PoolKind::Buy, 0, 40000000000, 0, 2524283573690, false)),
        (450145128, 0, Some(1790058051), 5, 4, P(PoolKind::Sell, 0, 195002882750, 0, 2564283573690, false)),
    ],
    expect: &[
        ("F7", "tp4_T600", 5, 450145128, Some("2026-09-22"), "normal", 2564283573690, Some(4), "organic", Some(450145098), Some(0), "pass"),
    ],
    hour_events: &[
        (Segment::PoolNormal, &[(5, 450145098, 2524283573690), (5, 450145128, 2564283573690)]),
    ],
    farm: &[
        (5, &[(450145098, 0, 40000000000, 0), (450145128, 0, 195002882750, 0)]),
    ],
};
// Python's statuses: [('entered', None)]

/// x1_pool_then_curve: A new hour's first slot: pool P (tx 0) triggers and rolls the hour before curve C (tx 1)
/// triggers; each has an N2 candidate in the old hour (curve D at 44 SOL, reversed pool Q at 400 SOL). Only P's N2 sees
/// the old hour.
const X1_POOL_THEN_CURVE: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 coin, 2 buyer, 3 trader1, 4 trader2, 5 creator, 6 P, 7 Q, 8 C, 9 D
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "2mt5opXwYMEVLrYiH6VkAouidkLoATE1mkJviuGkpFw3", "DgX9xEoN7RZGWevFVCy13JuzKsnmAx9B3VLfvoJxwqKn",
        "7DdxS1qVtBCSfdJGThi4j5xxxHqCi7PWbFM3G2H217rH", "63QRh6CpNaGpV13A7eV99sdKhBE2z1Yh8knawNfMqqbm",
        "8DEF9uyVVXkzFmLetaujnijJm4RxwCrfrV8iXZTXtGNQ", "5FoqQNyUfj7H4XergdMdT3x6HRJuodUuiKWZCDaQzhUE",
    ],
    seeds: &[
        4271053018750731645, 8141434438437126355, 3885450303126769205, 1685726804653979033, 6127977479432806642,
        13748525418858597173, 15267589225482652708, 6023727921692053277, 6476562634710485851, 18151107366038800331,
    ],
    trigger_seeds: &[
        (6, 450152521, 9361683525773602508), (6, 450152531, 11680437541729427507), (7, 450150010, 2498355266622871800),
        (8, 450152521, 8402772624408747836), (8, 450152531, 17307084695204480545), (9, 450150000, 11894088865740031937),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 1,
    bounds: (446529755, 452284004),
    mints: &[
        (8, Some(450100000), 5, Some(false), Some("11111111111111111111111111111111")),
        (9, Some(450100000), 5, Some(false), Some("11111111111111111111111111111111")),
    ],
    pools: &[(6, true, false, Some(6), 1), (7, true, false, Some(7), 1)],
    events: &[
        (450150000, 0, Some(1790060000), 9, 2, C(true, 1000000000, 100000000000, 44000000000)),
        (450150010, 0, Some(1790060004), 7, 2, P(PoolKind::Sell, 1000000000, 0, 400000000000, 0, false)),
        (450152521, 0, Some(1790061008), 6, 2, P(PoolKind::Sell, 20000000000, 0, 380000000000, 0, false)),
        (450152521, 1, Some(1790061008), 8, 2, C(true, 6000000000, 100000000000, 43000000000)),
        (450152531, 0, Some(1790061012), 6, 3, P(PoolKind::Buy, 16000000000, 0, 400000000000, 0, false)),
        (450152531, 1, Some(1790061012), 8, 4, C(false, 5000000000, 100000000000, 40000000000)),
    ],
    expect: &[
        ("F7", "flow300_H6", 6, 450152531, Some("2026-09-22"), "reversed", 400000000000, Some(3), "organic", Some(450152521), Some(0), "farm window incomplete"),
        ("F7", "flow300_tp100_H6", 6, 450152531, Some("2026-09-22"), "reversed", 400000000000, Some(3), "organic", Some(450152521), Some(0), "farm window incomplete"),
        ("F7", "tp4_T600", 6, 450152531, Some("2026-09-22"), "reversed", 400000000000, Some(3), "organic", Some(450152521), Some(0), "pass"),
        ("F7", "tp4_T600", 8, 450152531, Some("2026-09-22"), "curve", 45000000000, Some(4), "n/a", None, None, "pass"),
        ("N2_F7", "tp4_T600", 7, 450152531, Some("2026-09-22"), "reversed", 400000000000, None, "organic", Some(450150010), Some(0), "pass"),
    ],
    hour_events: &[
        (Segment::Curve, &[(8, 450152531, 40000000000)]),
        (Segment::PoolReversed, &[(6, 450152531, 400000000000)]),
    ],
    farm: &[
        (6, &[(450152521, 0, 20000000000, 0), (450152531, 0, 16000000000, 0)]),
        (7, &[(450150010, 0, 1000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None), ('skipped', 'farm window incomplete')]

/// x2_lazy_prune_n_at_last: dev2 1036 -> 1037 (n 11352 -> 13510): at the add of L the lazy prune takes n at L, so
/// nothing goes (the first item, L - 25000, is not before L - 27020) and the farmer trade at L - 12000 flags the
/// trigger at L + 1.
const X2_LAZY_PRUNE_N_AT_LAST: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 coin, 2 buyer, 3 trader1, 4 farmer, 5 R
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "6W7EqZRUb6YenE3FfNafrCZW1HKQg1W9zGSwrFFANHZr", "AS5CnoZ7ezMhUXphn32H5Y2YAXnChdZAjjwywYYXuhgP",
    ],
    seeds: &[
        4271053018750731645, 8141434438437126355, 3885450303126769205, 1685726804653979033, 7755037554681482877,
        2450596062508755528,
    ],
    trigger_seeds: &[
        (5, 447959050, 9626576276834708659), (5, 447966050, 12045610831590950668), (5, 447972050, 337408112785200356),
        (5, 447979050, 18382934411321197689), (5, 447984050, 18013356007846469708), (5, 447984051, 5401686602232562322),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 0,
    bounds: (446529755, 452284004),
    mints: &[],
    pools: &[(5, true, false, Some(5), 1)],
    events: &[
        (447959050, 0, Some(1789183620), 5, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (447966050, 0, Some(1789186420), 5, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (447972050, 0, Some(1789188820), 5, 4, P(PoolKind::Sell, 100000000000, 0, 300000000000, 0, true)),
        (447979050, 0, Some(1789191620), 5, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (447984050, 0, Some(1789193620), 5, 2, P(PoolKind::Sell, 2000000000, 0, 300000000000, 0, false)),
        (447984051, 0, Some(1789193620), 5, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
    ],
    expect: &[
        ("F7", "flow300_H6", 5, 447984051, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447959050), Some(1), "farm flagged"),
        ("F7", "flow300_tp100_H6", 5, 447984051, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447959050), Some(1), "farm flagged"),
        ("F7", "tp4_T600", 5, 447984051, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447959050), Some(1), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[(5, 447984050, 300000000000), (5, 447984051, 320000000000)]),
    ],
    farm: &[
        (5, &[
            (447959050, 0, 1000000000, 0), (447966050, 0, 1000000000, 0), (447972050, 0, 100000000000, 100000000000),
            (447979050, 0, 1000000000, 0), (447984050, 0, 2000000000, 0), (447984051, 0, 15000000000, 0),
        ]),
    ],
};
// Python's statuses: [('entered', None), ('skipped', 'farm flagged')]

/// x3_pool_creator_sell_in_t: A pool creator's SOL-side sell after the trigger leg in slot t blocks: the creator window
/// ends at t, not t - 1 (reversed pool R, no token, so the creator is the coin creator). The control R2 has the same
/// legs with the sell at t2 + 1: it triggers.
const X3_POOL_CREATOR_SELL_IN_T: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 coin, 2 buyer, 3 trader1, 4 R, 5 R2
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "AS5CnoZ7ezMhUXphn32H5Y2YAXnChdZAjjwywYYXuhgP", "FReG9wb88KFaFJMGwVRatBGf7SofiQe2q69m8j8sTjzS",
    ],
    seeds: &[
        4271053018750731645, 8141434438437126355, 3885450303126769205, 1685726804653979033, 2450596062508755528,
        5346141749299589402,
    ],
    trigger_seeds: &[
        (4, 450199990, 16150258419711442649), (4, 450200000, 4388777429073519059), (5, 450200990, 9722213026944918205),
        (5, 450201000, 15462789144693814808), (5, 450201001, 6522922779947687114),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 0,
    bounds: (446529755, 452284004),
    mints: &[],
    pools: &[(4, true, false, Some(4), 1), (5, true, false, Some(5), 1)],
    events: &[
        (450199990, 0, Some(1790079996), 4, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (450200000, 0, Some(1790080000), 4, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
        (450200000, 1, Some(1790080000), 4, 1, P(PoolKind::Buy, 1000000000, 0, 305000000000, 0, false)),
        (450200990, 0, Some(1790080396), 5, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (450201000, 0, Some(1790080400), 5, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
        (450201001, 0, Some(1790080400), 5, 1, P(PoolKind::Buy, 1000000000, 0, 305000000000, 0, false)),
    ],
    expect: &[
        ("F7", "flow300_H6", 5, 450201000, Some("2026-09-22"), "reversed", 320000000000, Some(3), "organic", Some(450200990), Some(0), "farm window incomplete"),
        ("F7", "flow300_tp100_H6", 5, 450201000, Some("2026-09-22"), "reversed", 320000000000, Some(3), "organic", Some(450200990), Some(0), "farm window incomplete"),
        ("F7", "tp4_T600", 5, 450201000, Some("2026-09-22"), "reversed", 320000000000, Some(3), "organic", Some(450200990), Some(0), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[
            (4, 450199990, 300000000000), (4, 450200000, 305000000000), (5, 450200990, 300000000000),
            (5, 450201000, 320000000000), (5, 450201001, 305000000000),
        ]),
    ],
    farm: &[
        (4, &[(450199990, 0, 20000000000, 0), (450200000, 0, 15000000000, 0), (450200000, 1, 1000000000, 0)]),
        (5, &[(450200990, 0, 20000000000, 0), (450201000, 0, 15000000000, 0), (450201001, 0, 1000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None), ('skipped', 'farm window incomplete')]

/// x4_n2_depth_last_leg: A pool's N2 depth is its slot's last leg of any kind: Q's deposit after its sell (pre b 520
/// SOL) puts Q outside P's band [336, 504], where the sell's 420 SOL would be inside.
const X4_N2_DEPTH_LAST_LEG: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 coin, 2 buyer, 3 trader1, 4 lp, 5 P, 6 Q
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "hua7iYdYEU96CCfA1uWt8iQTkDTRpnbUnVTePPPssKS", "7DdxS1qVtBCSfdJGThi4j5xxxHqCi7PWbFM3G2H217rH",
        "63QRh6CpNaGpV13A7eV99sdKhBE2z1Yh8knawNfMqqbm",
    ],
    seeds: &[
        4271053018750731645, 8141434438437126355, 3885450303126769205, 1685726804653979033, 1836843492878865578,
        15267589225482652708, 6023727921692053277,
    ],
    trigger_seeds: &[
        (5, 450199990, 8194615372044284101), (5, 450200000, 8465436760116941490), (6, 450199900, 748035349776831953),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 1,
    bounds: (446529755, 452284004),
    mints: &[],
    pools: &[(5, true, false, Some(5), 1), (6, true, false, Some(6), 1)],
    events: &[
        (450199900, 0, Some(1790079960), 6, 2, P(PoolKind::Sell, 100000000000, 0, 420000000000, 0, false)),
        (450199900, 1, Some(1790079960), 6, 4, P(PoolKind::Deposit, 10000000000, 0, 520000000000, 0, false)),
        (450199990, 0, Some(1790079996), 5, 2, P(PoolKind::Sell, 20000000000, 0, 400000000000, 0, false)),
        (450200000, 0, Some(1790080000), 5, 3, P(PoolKind::Buy, 17000000000, 0, 420000000000, 0, false)),
    ],
    expect: &[
        ("F7", "flow300_H6", 5, 450200000, Some("2026-09-22"), "reversed", 420000000000, Some(3), "organic", Some(450199990), Some(0), "farm window incomplete"),
        ("F7", "flow300_tp100_H6", 5, 450200000, Some("2026-09-22"), "reversed", 420000000000, Some(3), "organic", Some(450199990), Some(0), "farm window incomplete"),
        ("F7", "tp4_T600", 5, 450200000, Some("2026-09-22"), "reversed", 420000000000, Some(3), "organic", Some(450199990), Some(0), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[
            (6, 450199900, 520000000000), (5, 450199990, 400000000000), (5, 450200000, 420000000000),
        ]),
    ],
    farm: &[
        (5, &[(450199990, 0, 20000000000, 0), (450200000, 0, 17000000000, 0)]),
        (6, &[(450199900, 0, 100000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None), ('skipped', 'farm window incomplete')]

/// x5_pool_seen_edge: The 24 h prune is strict: at the rollover S, PL (pool_last exactly S - slots(24 h, S)) keeps its
/// pool_seen and PM (one slot earlier) loses it.
const X5_POOL_SEEN_EDGE: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 coin, 2 buyer, 3 trader1, 4 PL, 5 PM, 6 X
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "3mskfc1JphAZRfiGH7iuEc6L9zM8HezEFCMn9Y3tcWj7", "8F1ToGLmo6CddMMpxgVSZX1ot2g3ru2WGUDZPmSAcSdd",
        "65N9XRM5po8GpFU76FSCxBr4g98bHw9aEKQX4Sr5EPFi",
    ],
    seeds: &[
        4271053018750731645, 8141434438437126355, 3885450303126769205, 1685726804653979033, 11701245891596156828,
        17110313752555454720, 10692495523296687009,
    ],
    trigger_seeds: &[
        (4, 450085317, 5527306272562580609), (4, 450408010, 17765833184581519224), (4, 450408020, 16072046977588857989),
        (5, 450085316, 781821926159364437), (5, 450408030, 589465419610530695), (5, 450408040, 9720448295212578349),
        (6, 450407910, 3391419786320335020),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 0,
    bounds: (446529755, 452284004),
    mints: &[],
    pools: &[(4, true, false, Some(4), 1), (5, true, false, Some(5), 1), (6, true, false, Some(6), 1)],
    events: &[
        (450085316, 0, Some(1790034126), 5, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (450085317, 0, Some(1790034126), 4, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (450407910, 0, Some(1790163164), 6, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (450408010, 0, Some(1790163204), 4, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (450408020, 0, Some(1790163208), 4, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
        (450408030, 0, Some(1790163212), 5, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (450408040, 0, Some(1790163216), 5, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
    ],
    expect: &[
        ("F7", "flow300_H6", 4, 450408020, Some("2026-09-23"), "reversed", 320000000000, Some(3), "organic", Some(450085317), Some(1), "pass"),
        ("F7", "flow300_tp100_H6", 4, 450408020, Some("2026-09-23"), "reversed", 320000000000, Some(3), "organic", Some(450085317), Some(1), "pass"),
        ("F7", "tp4_T600", 4, 450408020, Some("2026-09-23"), "reversed", 320000000000, Some(3), "organic", Some(450085317), Some(1), "pass"),
        ("F7", "flow300_H6", 5, 450408040, Some("2026-09-23"), "reversed", 320000000000, Some(3), "organic", Some(450408030), Some(1), "pass"),
        ("F7", "flow300_tp100_H6", 5, 450408040, Some("2026-09-23"), "reversed", 320000000000, Some(3), "organic", Some(450408030), Some(1), "pass"),
        ("F7", "tp4_T600", 5, 450408040, Some("2026-09-23"), "reversed", 320000000000, Some(3), "organic", Some(450408030), Some(1), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[
            (6, 450407910, 300000000000), (4, 450408010, 300000000000), (4, 450408020, 320000000000),
            (5, 450408030, 300000000000), (5, 450408040, 320000000000),
        ]),
    ],
    farm: &[
        (4, &[(450408010, 0, 20000000000, 0), (450408020, 0, 15000000000, 0)]),
        (5, &[(450408030, 0, 20000000000, 0), (450408040, 0, 15000000000, 0)]),
        (6, &[(450407910, 0, 1000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None)]

/// x6_lazy_prune_keeps_from_n_at_last: dev2 1036 -> 1037: the lazy prune fires at the add of L (the first item,
/// L - 30000, is before L - 2 n(L)) and keeps from L - n(L) = L - 13510, so the farmer trade at L - 12000 stays (L - n
/// at the first item would drop it).
const X6_LAZY_PRUNE_KEEPS_FROM_N_AT_LAST: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 coin, 2 buyer, 3 trader1, 4 farmer, 5 R
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "6W7EqZRUb6YenE3FfNafrCZW1HKQg1W9zGSwrFFANHZr", "AS5CnoZ7ezMhUXphn32H5Y2YAXnChdZAjjwywYYXuhgP",
    ],
    seeds: &[
        4271053018750731645, 8141434438437126355, 3885450303126769205, 1685726804653979033, 7755037554681482877,
        2450596062508755528,
    ],
    trigger_seeds: &[
        (5, 447954050, 13558141070668857719), (5, 447964050, 11583052199124585411), (5, 447972050, 337408112785200356),
        (5, 447984050, 18013356007846469708), (5, 447984051, 5401686602232562322),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 0,
    bounds: (446529755, 452284004),
    mints: &[],
    pools: &[(5, true, false, Some(5), 1)],
    events: &[
        (447954050, 0, Some(1789181620), 5, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (447964050, 0, Some(1789185620), 5, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (447972050, 0, Some(1789188820), 5, 4, P(PoolKind::Sell, 100000000000, 0, 300000000000, 0, true)),
        (447984050, 0, Some(1789193620), 5, 2, P(PoolKind::Sell, 2000000000, 0, 300000000000, 0, false)),
        (447984051, 0, Some(1789193620), 5, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
    ],
    expect: &[
        ("F7", "flow300_H6", 5, 447984051, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447954050), Some(1), "farm flagged"),
        ("F7", "flow300_tp100_H6", 5, 447984051, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447954050), Some(1), "farm flagged"),
        ("F7", "tp4_T600", 5, 447984051, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447954050), Some(1), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[(5, 447984050, 300000000000), (5, 447984051, 320000000000)]),
    ],
    farm: &[
        (5, &[(447972050, 0, 100000000000, 100000000000), (447984050, 0, 2000000000, 0), (447984051, 0, 15000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None), ('skipped', 'farm flagged')]

/// x7_rollover_n_at_slot: dev2 1036 -> 1037: the rollover at S (in 1037, X first) takes n at S (13510), so R, whose
/// last trade is a farmer trade at S - 12000 in 1036, stays (n at that trade, 11352, would drop it); R's trigger at
/// S + 5 is flagged.
const X7_ROLLOVER_N_AT_SLOT: Case = Case {
    // ids: 0 MAYHEM_AGENT, 1 coin, 2 buyer, 3 trader1, 4 farmer, 5 R, 6 X, 7 Z
    names: &[
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "D6Ctou6S3Ck3Hd2pNt48y5jv4Q4AvSvd4vnsFAGrKtwz",
        "8PNeMNJQFFAU5phCnn12MVHk6sAorobNqatfvDvRpVkG", "DpCtSmtWcsjbwK2x9EjvkT4ZnT1UJbpSwhF7JicVQwzx",
        "6W7EqZRUb6YenE3FfNafrCZW1HKQg1W9zGSwrFFANHZr", "AS5CnoZ7ezMhUXphn32H5Y2YAXnChdZAjjwywYYXuhgP",
        "65N9XRM5po8GpFU76FSCxBr4g98bHw9aEKQX4Sr5EPFi", "DecPCG7KoJJsm8CSXN2DASr4XMMPT8K6Dy75Fkbrm5zS",
    ],
    seeds: &[
        4271053018750731645, 8141434438437126355, 3885450303126769205, 1685726804653979033, 7755037554681482877,
        2450596062508755528, 10692495523296687009, 10368531939130489044,
    ],
    trigger_seeds: &[
        (5, 447972050, 337408112785200356), (5, 447984050, 18013356007846469708), (5, 447984055, 7024290735027441274),
        (6, 447984050, 11798057099072089273), (7, 447954050, 5248363885761662756),
    ],
    clock: &[
        (1033, 316.36), (1034, 315.9), (1035, 316.53), (1036, 317.13), (1037, 266.46), (1038, 266.83), (1039, 267.12),
        (1040, 266.17), (1041, 265.83), (1042, 267.83), (1043, 267.93), (1044, 268.26), (1045, 267.65), (1046, 267.35),
    ],
    dev: (446256000, 452304000),
    n2_k: 0,
    bounds: (446529755, 452284004),
    mints: &[],
    pools: &[(5, true, false, Some(5), 1), (6, true, false, Some(6), 1), (7, true, false, Some(7), 1)],
    events: &[
        (447954050, 0, Some(1789181620), 7, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (447972050, 0, Some(1789188820), 5, 4, P(PoolKind::Sell, 100000000000, 0, 300000000000, 0, true)),
        (447984050, 0, Some(1789193620), 6, 2, P(PoolKind::Sell, 1000000000, 0, 300000000000, 0, false)),
        (447984050, 1, Some(1789193620), 5, 2, P(PoolKind::Sell, 20000000000, 0, 300000000000, 0, false)),
        (447984055, 0, Some(1789193622), 5, 3, P(PoolKind::Buy, 15000000000, 0, 320000000000, 0, false)),
    ],
    expect: &[
        ("F7", "flow300_H6", 5, 447984055, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447972050), Some(1), "farm flagged"),
        ("F7", "flow300_tp100_H6", 5, 447984055, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447972050), Some(1), "farm flagged"),
        ("F7", "tp4_T600", 5, 447984055, Some("2026-09-12"), "reversed", 320000000000, Some(3), "flagged", Some(447972050), Some(1), "pass"),
    ],
    hour_events: &[
        (Segment::PoolReversed, &[
            (6, 447984050, 300000000000), (5, 447984050, 300000000000), (5, 447984055, 320000000000),
        ]),
    ],
    farm: &[
        (5, &[(447972050, 0, 100000000000, 100000000000), (447984050, 1, 20000000000, 0), (447984055, 0, 15000000000, 0)]),
        (6, &[(447984050, 0, 1000000000, 0)]),
    ],
};
// Python's statuses: [('entered', None), ('skipped', 'farm flagged')]
