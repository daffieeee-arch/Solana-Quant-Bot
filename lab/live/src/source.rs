//! M3-07: what feeds `lab-live paper` (research/m3-design-2026-10-19.md §8). A source hands out its range in chain
//! order as items, each with the source's keys and its memory view (`ReplayMem`, for `--mem-log`). The paper loop
//! pushes a slot's events to the engine one by one, exactly as it pushed the replay's events before, so the journal is
//! the same.
//!
//! The replay (OF1 chunks or recorded segments, legs_read.rs `Replay`) is the first source: it hands out whole slots
//! (`Replay::for_each_slot_mem`). Later sources add their own items beside `Slot`:
//! - M3-09b's follow source over a recorder root (follow.rs, research/m3-09b-follow-source-spec-2026-10-20.md §3): after
//!   reading a segment, what it learned (tokens, SOL markets, the journal's learned rows, epochs' ms per slot), its usage
//!   rows, its backfill trades as history and the segment itself; then the slots it releases, each event with its
//!   arrival;
//! - M3-14's relay: gaps.

use crate::legs::Keys;
use crate::legs_read::{Replay, ReplayMem};
use crate::snapshot::{PoolInfo, SeedWindow, TokenInfo};
use anyhow::Result;
pub use lab_core::legs::Arrival;
use lab_core::legs::{Event, Id, Leg, Order};
use serde_json::Value;

/// One slot's events, in chain order: every event of the slot the source has, within its range. The follow source
/// hands out a slot as LiveFeed releases it: a later segment that brings more rows of the last slot released hands
/// them out as another item of the same slot (live.py `events`: such rows are not late).
pub struct Slot<'a> {
    pub slot: u64,
    pub events: &'a [Event],
    /// The follow source: each event's arrival, beside `events` (the same length). None from the replay, whose events
    /// have none (feed.py:32-34).
    pub arrivals: Option<&'a [Arrival]>,
}

// An event's arrival (LiveFeed `Event.arrival`, live.py `_arrival_of`) is lab_core's `Arrival` (M3-11: the engine reads
// it for the signal latency): the earliest received and created time of its transaction in the segment's arrivals table
// (created read as BIGINT: beyond it NULL), and the segment's written_unix. A transaction without an arrival row has
// (None, None, written).

/// A row of the journal's `learned` table (LiveFeed `drain_learned`): what a restart needs. `info` is Python's
/// json.dumps of the row's list, so its text is the journal's.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Learned {
    pub kind: &'static str,
    pub key: String,
    pub seq: u64,
    pub slot: Option<u64>,
    pub info: String,
}

impl Learned {
    /// The row as paper/legs_dump.py writes it: `kind key seq slot info`, tab separated, None as `null`.
    pub fn line(&self) -> String {
        format!("{}\t{}\t{}\t{}\t{}", self.kind, self.key, self.seq, self.slot.map_or_else(|| "null".to_owned(), |s| s.to_string()),
                self.info)
    }
}

/// What a source hands out.
pub enum Item<'a> {
    Slot(Slot<'a>),
    /// follow: a token learned (a CreateEvent) or changed (its completion, its migration), as LiveFeed's `tokens` holds it.
    Token(&'a TokenInfo),
    /// follow: a SOL market learned (LiveFeed's `pools` and `new_pools`).
    Pool(&'a PoolInfo),
    /// follow: an epoch's ms per slot as measured so far (LiveFeed's `clock.per_epoch`).
    EpochMs { epoch: u64, ms: f64 },
    /// follow: a learned row for the journal.
    Learned(&'a Learned),
    /// follow: a backfill trade of `pool`, history only, never an event (LiveFeed `drain_history`: one leg per trade).
    History { pool: Id, legs: &'a [Leg] },
    /// follow (M3-10): a recorded trade whose farmer flag a late backfill changed (LiveFeed `drain_flag_fixes`).
    FlagFix { pool: Id, order: Order, farmer: bool },
    /// follow: a usage row of the segment's `_segment.json`, with its seq (LiveFeed `drain_usage`).
    Usage(&'a Value),
    /// follow: the segment just read, and the farmer window h24 after its blocks (LiveFeed `seq` and `_h24()`).
    Segment { seq: u64, h24: u64 },
    /// follow (M3-11): the farmer seed's window, once, with the first segment's learns (LiveFeed `farm_seed`, set by
    /// `_init_seed` before any event): the engine's farm_complete and the journal's farmer_seed.
    FarmSeed(SeedWindow),
    /// follow (M3-11 v0.3): a token the prune dropped (LiveFeed `_prune`: no completion or pool, older than TOKEN_KEEP_DAYS
    /// days), one item per dropped mint, before the slots released after it: `feed.token(mint)` is None from then on.
    TokenForget { mint: Id },
}

/// A source of the paper engine's input, in chain order (module doc).
pub trait Source {
    /// Hand out every item once, in chain order. An error from `f` stops the source and is returned.
    fn for_each_item(&mut self, f: &mut dyn FnMut(&Keys, &ReplayMem, Item<'_>) -> Result<()>) -> Result<()>;
}

impl Source for Replay {
    fn for_each_item(&mut self, f: &mut dyn FnMut(&Keys, &ReplayMem, Item<'_>) -> Result<()>) -> Result<()> {
        self.for_each_slot_mem(|keys, mem, slot, events| f(keys, mem, Item::Slot(Slot { slot, events, arrivals: None })))
    }
}
