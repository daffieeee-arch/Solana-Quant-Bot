//! lab-core: the per-event math that the Python backtest and the Rust live route both need, written
//! once (lab/docs/rust-live-route-design-2026-10-16.md §12). Paper only: nothing here signs or sends.
//!
//! M0: `venues`, the exact integer fills of the pump.fun bonding curve and PumpSwap pools
//! (backtest/venues.py, byte for byte). M1 C1a: `legs`, the engine's curve and pool legs and their order (the
//! Python replay's dataclasses, compact). Later steps: costs, exit rules and FlowIndex, PositionSim, F7.

pub mod legs;
pub mod venues;
