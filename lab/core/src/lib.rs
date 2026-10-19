//! lab-core: the per-event math that the Python backtest and the Rust live route both need, written
//! once (lab/docs/rust-live-route-design-2026-10-16.md §12). Paper only: nothing here signs or sends.
//!
//! M0: `venues`, the exact integer fills of the pump.fun bonding curve and PumpSwap pools
//! (backtest/venues.py, byte for byte). M1 C1a: `legs`, the engine's curve and pool legs and their order (the
//! Python replay's dataclasses, compact). M1 C1b: the paper engine's F7 signals per slot (`signals`) with the clock
//! (`clock`), exact int-against-float tests (`exact`), the universe (`universe`), the trigger and its windows (`f7`),
//! the farm label (`farm`) and the N2 shadow (`n2`). M1 C2: Python's int/int division and int(i * f) (`exact`), the
//! costs and settle (`costs`), the empirical tolerance of trades without a known limit (`tau`), the exit rules of M1's
//! three variants (`exits`), a flow position's pool flow index (`flow`) and the position itself, slot by slot
//! (`positions`: PositionSim, its entries and the re-run of the token's trades). Later steps: the paper engine.

pub mod clock;
pub mod costs;
pub mod exact;
pub mod exits;
pub mod f7;
pub mod farm;
pub mod flow;
pub mod legs;
pub mod n2;
pub mod positions;
pub mod signals;
pub mod tau;
pub mod universe;
pub mod venues;
