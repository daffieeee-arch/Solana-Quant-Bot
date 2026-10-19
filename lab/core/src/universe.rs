//! Which keys F7 decides on and which are N2 candidates: the engine's universe (paper/engine.py `_universe`, :555-568;
//! `_hour_index`, :702-709) over the snapshot's mints and SOL-market pools. M1 C1b; the porting spec is
//! research/m1-c1b-signals-spec-2026-10-19.md §3.5 and §3.8.
//!
//! - A curve needs its token: not mayhem, quoted in SOL_QUOTES, and created in [burn, dev1). A NULL quote_mint is
//!   never SOL (engine.py:559): rejected and counted apart, never an error.
//! - A pool needs only a non-mayhem entry in the SOL-market map (quote_class sol or reversed, feed.py:97-99). Pools
//!   have no age filter and no token-quote test.
//! - The pool's creator is its token's creator when the token exists, NULL creator included; only a pool without a
//!   token falls back to coin_creator (engine.py:568). "Mint absent" therefore applies to curves only.
//! - N2 candidates take the same rules without the age filter.

use crate::clock::Bounds;
use crate::legs::{Id, Leg, Venue, NONE, SOL_QUOTES};
use crate::mem::{self, Mem};

/// Where a key trades: the group segments (engine.py:617) and the N2 buckets `(venue, orientation)` (engine.py:704-709).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Segment {
    Curve,
    PoolNormal,
    PoolReversed,
}

impl Segment {
    pub const ALL: [Segment; 3] = [Segment::Curve, Segment::PoolNormal, Segment::PoolReversed];

    /// The group file's name ("curve", "pool_normal", "pool_reversed").
    pub fn as_str(self) -> &'static str {
        match self {
            Segment::Curve => "curve",
            Segment::PoolNormal => "pool_normal",
            Segment::PoolReversed => "pool_reversed",
        }
    }

    pub fn parse(s: &str) -> Option<Segment> {
        Segment::ALL.into_iter().find(|g| g.as_str() == s)
    }

    /// The signals row's orientation ("curve" for curves, else the pool's).
    pub fn orientation(self) -> &'static str {
        match self {
            Segment::Curve => "curve",
            Segment::PoolNormal => "normal",
            Segment::PoolReversed => "reversed",
        }
    }

    pub fn venue(self) -> Venue {
        if self == Segment::Curve { Venue::Curve } else { Venue::Pool }
    }

    pub fn reversed(self) -> bool {
        self == Segment::PoolReversed
    }

    pub fn index(self) -> usize {
        self as usize
    }

    pub fn of_pool(reversed: bool) -> Segment {
        if reversed { Segment::PoolReversed } else { Segment::PoolNormal }
    }
}

/// A token of the snapshot's mints (feed.py `TokenInfo`), as F7 reads it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MintMeta {
    pub create_slot: Option<u64>,
    /// NONE for a NULL creator.
    pub creator: Id,
    pub mayhem: bool,
    pub sol_quote: bool,
    pub quote_null: bool,
}

impl MintMeta {
    /// From a mints row: a NULL mayhem is false (Python's falsy None); a NULL quote_mint is never SOL.
    pub fn new(create_slot: Option<u64>, creator: Id, mayhem: Option<bool>, quote_mint: Option<&str>) -> Self {
        MintMeta {
            create_slot,
            creator,
            mayhem: mayhem.unwrap_or(false),
            sol_quote: quote_mint.is_some_and(|q| SOL_QUOTES.contains(&q)),
            quote_null: quote_mint.is_none(),
        }
    }
}

/// A SOL-market pool of the snapshot (feed.py `PoolInfo`, quote_class sol or reversed), as F7 reads it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PoolMeta {
    pub reversed: bool,
    /// `COALESCE(mayhem, false)` (feed.py:98).
    pub mayhem: bool,
    /// The id of the pool's mint when it has one; the Universe looks it up among its tokens.
    pub mint_tok: Option<Id>,
    /// NONE for a NULL coin_creator.
    pub coin_creator: Id,
}

/// Why `f7_key` refused a key; the summary counts each cause.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reject {
    CurveAbsent,
    CurveMayhem,
    QuoteNull,
    QuoteNotSol,
    CurveAge,
    PoolAbsent,
    PoolMayhem,
    /// The token passed every other test but has no create_slot. Python raises here (a TypeError at engine.py:561),
    /// so the engine stops with an error instead of counting it.
    NoCreateSlot,
}

impl Reject {
    pub const ALL: [Reject; 8] = [
        Reject::CurveAbsent,
        Reject::CurveMayhem,
        Reject::QuoteNull,
        Reject::QuoteNotSol,
        Reject::CurveAge,
        Reject::PoolAbsent,
        Reject::PoolMayhem,
        Reject::NoCreateSlot,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Reject::CurveAbsent => "curve absent",
            Reject::CurveMayhem => "curve mayhem",
            Reject::QuoteNull => "quote NULL",
            Reject::QuoteNotSol => "quote not SOL",
            Reject::CurveAge => "curve age",
            Reject::PoolAbsent => "pool not a SOL market",
            Reject::PoolMayhem => "pool mayhem",
            Reject::NoCreateSlot => "no create_slot",
        }
    }
}

/// A key F7 decides on: the creator and coin creator the trigger tests against, and its segment.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct F7Key {
    pub creator: Id,
    /// NONE for curves.
    pub coin: Id,
    pub seg: Segment,
}

/// The snapshot's tokens and SOL-market pools by id, the run's bounds and MAYHEM_AGENT's id.
#[derive(Clone, Debug)]
pub struct Universe {
    mints: Vec<Option<MintMeta>>,
    pools: Vec<Option<PoolMeta>>,
    pub bounds: Bounds,
    /// MAYHEM_AGENT interned: a real id, so a NONE trader never equals it.
    pub mayhem_agent: Id,
}

fn put<T>(v: &mut Vec<Option<T>>, id: Id, x: T) {
    let i = id as usize;
    if v.len() <= i {
        v.resize_with(i + 1, || None);
    }
    v[i] = Some(x);
}

impl Universe {
    pub fn new(bounds: Bounds, mayhem_agent: Id) -> Self {
        assert!(mayhem_agent != NONE, "MAYHEM_AGENT must be interned");
        Universe { mints: Vec::new(), pools: Vec::new(), bounds, mayhem_agent }
    }

    pub fn add_mint(&mut self, id: Id, m: MintMeta) {
        put(&mut self.mints, id, m);
    }

    /// A SOL-market pool; other pools are never added (feed.py:97-99).
    pub fn add_pool(&mut self, id: Id, p: PoolMeta) {
        put(&mut self.pools, id, p);
    }

    pub fn mint(&self, id: Id) -> Option<&MintMeta> {
        self.mints.get(id as usize)?.as_ref()
    }

    pub fn pool(&self, id: Id) -> Option<&PoolMeta> {
        self.pools.get(id as usize)?.as_ref()
    }

    /// The memory log (C4-2): the tokens and SOL markets (entries are the ones present; the tables are indexed by id).
    pub fn mem(&self) -> [Mem; 2] {
        [
            Mem::new("universe_tokens", self.mints.iter().flatten().count(), mem::vec(&self.mints)),
            Mem::new("universe_sol_markets", self.pools.iter().flatten().count(), mem::vec(&self.pools)),
        ]
    }

    /// The key's creator, coin creator and segment when F7 may decide on it (engine.py `_universe`), else why not.
    pub fn f7_key(&self, venue: Venue, key: Id) -> Result<F7Key, Reject> {
        if venue == Venue::Curve {
            let m = self.mint(key).ok_or(Reject::CurveAbsent)?;
            if m.mayhem {
                return Err(Reject::CurveMayhem);
            }
            if !m.sol_quote {
                return Err(if m.quote_null { Reject::QuoteNull } else { Reject::QuoteNotSol });
            }
            let c = m.create_slot.ok_or(Reject::NoCreateSlot)?;
            if !(self.bounds.burn <= c && c < self.bounds.dev1) {
                return Err(Reject::CurveAge);
            }
            return Ok(F7Key { creator: m.creator, coin: NONE, seg: Segment::Curve });
        }
        let p = self.pool(key).ok_or(Reject::PoolAbsent)?;
        if p.mayhem {
            return Err(Reject::PoolMayhem);
        }
        // `tok.creator if tok else p.coin_creator`: a token with a NULL creator gives NONE, not coin_creator.
        let creator = match p.mint_tok.and_then(|m| self.mint(m)) {
            Some(tok) => tok.creator,
            None => p.coin_creator,
        };
        Ok(F7Key { creator, coin: p.coin_creator, seg: Segment::of_pool(p.reversed) })
    }

    /// The key's N2 bucket and depth when it is an N2 candidate (engine.py:702-709): a non-mayhem SOL-quoted token
    /// (no age filter) with its last leg's post real SOL, or a non-mayhem SOL-market pool with its last leg's SOL
    /// depth (a leg of any kind).
    pub fn n2_bucket(&self, key: Id, last: &Leg) -> Option<(Segment, i128)> {
        match last {
            Leg::Curve(l) => {
                let m = self.mint(key)?;
                (!m.mayhem && m.sol_quote).then_some((Segment::Curve, i128::from(l.rq)))
            }
            Leg::Pool(l) => {
                let p = self.pool(key)?;
                (!p.mayhem).then_some((Segment::of_pool(p.reversed), l.sol_side(p.reversed).2))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::tests::dev2;
    use crate::legs::{CurveLeg, CurveVariant, Order, PoolKind, PoolLeg, WSOL};

    const PUMP_QUOTE: &str = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn";
    const ONES: &str = "11111111111111111111111111111111";
    const MAYHEM: Id = 100;

    fn uni() -> Universe {
        Universe::new(Bounds::new((446_256_000, 452_304_000), &dev2()), MAYHEM)
    }

    fn curve_leg(mint: Id, rq: u64) -> Leg {
        Leg::Curve(CurveLeg {
            order: Order { slot: 450_197_213, tx: 1, outer: 5, inner: 6 }, mint, trader: 7, t: 0, q: 0, fee: 0, vt: 0, vq: 0, rt: 0, rq,
            args: [0; 4], fee_bps: 0, creator_bps: 0, args_set: 0, variant: CurveVariant::Sell, is_buy: false, direct: true,
        })
    }

    fn pool_leg(pool: Id, kind: PoolKind) -> Leg {
        Leg::Pool(PoolLeg {
            order: Order { slot: 450_145_128, tx: 1, outer: 0, inner: 0 }, pool, trader: 7, b: 900, e: 600, virt: 100, fees: [0; 4],
            base: 0, quote_net: 0, e_delta: 0, x: [0; 2], kind, flags: 0,
        })
    }

    #[test]
    fn a_null_quote_is_never_sol_and_counted_apart() {
        let m = |q| MintMeta::new(Some(450_196_501), 1, None, q);
        assert!(m(Some(ONES)).sol_quote && m(Some(WSOL)).sol_quote);
        assert!(!m(Some(PUMP_QUOTE)).sol_quote && !m(Some(PUMP_QUOTE)).quote_null);
        assert!(!m(None).sol_quote && m(None).quote_null);
        assert!(!m(Some("")).sol_quote);
        assert!(!m(None).mayhem && MintMeta::new(None, 1, Some(true), Some(ONES)).mayhem);
    }

    #[test]
    fn curves_need_a_non_mayhem_sol_token_created_in_burn_to_dev1() {
        let mut u = uni();
        let (burn, dev1) = (u.bounds.burn, u.bounds.dev1);
        let tok = MintMeta::new(Some(450_196_501), 1, Some(false), Some(ONES));
        for (id, m) in [(10, tok), (11, MintMeta { mayhem: true, ..tok }), (12, MintMeta::new(Some(450_196_501), 1, None, Some(PUMP_QUOTE))),
                        (13, MintMeta::new(Some(450_196_501), 1, None, None)), (14, MintMeta { create_slot: Some(burn - 1), ..tok }),
                        (15, MintMeta { create_slot: Some(burn), ..tok }), (16, MintMeta { create_slot: Some(dev1 - 1), ..tok }),
                        (17, MintMeta { create_slot: Some(dev1), ..tok }), (18, MintMeta { create_slot: None, ..tok }),
                        (19, MintMeta::new(Some(450_196_501), NONE, None, Some(WSOL)))] {
            u.add_mint(id, m);
        }
        let k = |id| u.f7_key(Venue::Curve, id);
        assert_eq!(k(10), Ok(F7Key { creator: 1, coin: NONE, seg: Segment::Curve }));
        assert_eq!([k(9), k(11), k(12), k(13)].map(Result::unwrap_err), [Reject::CurveAbsent, Reject::CurveMayhem, Reject::QuoteNotSol, Reject::QuoteNull]);
        assert_eq!((k(14), k(15).is_ok(), k(16).is_ok(), k(17)), (Err(Reject::CurveAge), true, true, Err(Reject::CurveAge)));
        assert_eq!(k(18), Err(Reject::NoCreateSlot));
        assert_eq!(k(19).map(|f| f.creator), Ok(NONE));
        assert_eq!(k(1_000_000), Err(Reject::CurveAbsent)); // beyond the table
        // A curve key is never looked up among the pools, and a pool key never among the mints.
        assert_eq!(u.f7_key(Venue::Pool, 10), Err(Reject::PoolAbsent));
    }

    #[test]
    fn a_pools_creator_is_its_tokens_when_the_token_exists() {
        let mut u = uni();
        u.add_mint(1, MintMeta::new(Some(450_000_000), 50, None, Some(PUMP_QUOTE))); // a PUMP-quoted token: no test on pools
        u.add_mint(2, MintMeta::new(Some(450_000_000), NONE, None, Some(ONES))); // creator NULL
        let p = PoolMeta { reversed: false, mayhem: false, mint_tok: Some(1), coin_creator: 60 };
        u.add_pool(20, p);
        u.add_pool(21, PoolMeta { mint_tok: Some(2), ..p });
        u.add_pool(22, PoolMeta { mint_tok: Some(23), ..p }); // the mint (here the pool's own address) is no token
        u.add_pool(23, PoolMeta { mint_tok: None, reversed: true, ..p });
        u.add_pool(24, PoolMeta { mayhem: true, ..p });
        u.add_pool(25, PoolMeta { mint_tok: None, coin_creator: NONE, ..p });
        let k = |id| u.f7_key(Venue::Pool, id);
        // The token's creator wins even where it differs from coin_creator; coin is always coin_creator.
        assert_eq!(k(20), Ok(F7Key { creator: 50, coin: 60, seg: Segment::PoolNormal }));
        assert_eq!(k(21), Ok(F7Key { creator: NONE, coin: 60, seg: Segment::PoolNormal }));
        assert_eq!(k(22), Ok(F7Key { creator: 60, coin: 60, seg: Segment::PoolNormal }));
        assert_eq!(k(23), Ok(F7Key { creator: 60, coin: 60, seg: Segment::PoolReversed }));
        assert_eq!((k(24), k(26)), (Err(Reject::PoolMayhem), Err(Reject::PoolAbsent)));
        assert_eq!(k(25), Ok(F7Key { creator: NONE, coin: NONE, seg: Segment::PoolNormal }));
    }

    #[test]
    fn n2_candidates_have_no_age_filter() {
        let mut u = uni();
        let old = MintMeta::new(Some(1), 1, None, Some(ONES)); // created long before burn: no F7, but an N2 candidate
        u.add_mint(1, old);
        u.add_mint(2, MintMeta { mayhem: true, ..old });
        u.add_mint(3, MintMeta::new(Some(1), 1, None, None));
        u.add_mint(4, MintMeta::new(Some(1), 1, None, Some(PUMP_QUOTE)));
        assert_eq!(u.f7_key(Venue::Curve, 1), Err(Reject::CurveAge));
        assert_eq!(u.n2_bucket(1, &curve_leg(1, 42)), Some((Segment::Curve, 42)));
        assert_eq!([2, 3, 4, 5].map(|m| u.n2_bucket(m, &curve_leg(m, 42))), [None; 4]);
        let p = PoolMeta { reversed: false, mayhem: false, mint_tok: None, coin_creator: 60 };
        u.add_pool(20, p);
        u.add_pool(21, PoolMeta { reversed: true, ..p });
        u.add_pool(22, PoolMeta { mayhem: true, ..p });
        // The last leg of any kind: normal depth e - virt, reversed depth b.
        assert_eq!(u.n2_bucket(20, &pool_leg(20, PoolKind::Deposit)), Some((Segment::PoolNormal, 500)));
        assert_eq!(u.n2_bucket(21, &pool_leg(21, PoolKind::Boost)), Some((Segment::PoolReversed, 900)));
        assert_eq!((u.n2_bucket(22, &pool_leg(22, PoolKind::Buy)), u.n2_bucket(23, &pool_leg(23, PoolKind::Buy))), (None, None));
    }

    #[test]
    fn segments_name_the_group_files_values() {
        assert_eq!(Segment::ALL.map(Segment::as_str), ["curve", "pool_normal", "pool_reversed"]);
        assert_eq!(Segment::ALL.map(Segment::orientation), ["curve", "normal", "reversed"]);
        assert_eq!(Segment::ALL.map(|s| Segment::parse(s.as_str())), Segment::ALL.map(Some));
        assert_eq!((Segment::parse("pool"), Segment::ALL.map(Segment::index)), (None, [0, 1, 2]));
    }
}
