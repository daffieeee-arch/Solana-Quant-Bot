//! M3-07: what feeds `lab-live paper` (research/m3-design-2026-10-19.md §8). A source hands out its range in chain
//! order as items, each with the source's keys and its memory view (`ReplayMem`, for `--mem-log`). The paper loop
//! pushes a slot's events to the engine one by one, exactly as it pushed the replay's events before, so the journal is
//! the same.
//!
//! The replay (OF1 chunks or recorded segments, legs_read.rs `Replay`) is the first source: it hands out whole slots
//! (`Replay::for_each_slot_mem`). Later sources add their own items beside `Slot`:
//! - M3-09b's follow source over a recorder root that grows: tokens and pools learned live, backfill rows as history;
//! - M3-14's relay: gaps.

use crate::legs::Keys;
use crate::legs_read::{Replay, ReplayMem};
use anyhow::Result;
use lab_core::legs::Event;

/// One slot's events, in chain order: every event of the slot the source has, within its range.
pub struct Slot<'a> {
    pub slot: u64,
    pub events: &'a [Event],
}

/// What a source hands out.
pub enum Item<'a> {
    Slot(Slot<'a>),
}

/// A source of the paper engine's input, in chain order (module doc).
pub trait Source {
    /// Hand out every item once, in chain order. An error from `f` stops the source and is returned.
    fn for_each_item(&mut self, f: &mut dyn FnMut(&Keys, &ReplayMem, Item<'_>) -> Result<()>) -> Result<()>;
}

impl Source for Replay {
    fn for_each_item(&mut self, f: &mut dyn FnMut(&Keys, &ReplayMem, Item<'_>) -> Result<()>) -> Result<()> {
        self.for_each_slot_mem(|keys, mem, slot, events| f(keys, mem, Item::Slot(Slot { slot, events })))
    }
}
