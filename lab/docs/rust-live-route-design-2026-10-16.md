# Design: the Rust live route (paper only), 2026-10-16

For the coordinator and the owner. The owner has given the Rust live route high priority.
- **Goal:** the code that may one day trade live should run beside the Python paper engine as early as possible, and prove itself on real data for weeks.
- **Reference:** the Python paper engine (`lab/paper`) stays the reference.
- **Paper only:** no keys, no signing, no sending. Sending is a later, separate small binary (§11), out of scope here.

Every choice names its source. Facts from this repo cite the file. External facts were checked on 2026-10-16 in the Triton docs (MCP `triton-docs`), solana.com, the `rpcpool/yellowstone-grpc` repository and crates.io.

## 0. Summary of the decisions

| # | decision | why |
|---|---|---|
| 1 | One Rust process `lab-live`. A single-threaded engine core owns all state; a network task feeds it, and a writer thread journals. | Determinism (the same order as a replay) makes parity provable. ~90 transactions/s on average needs one core (§10). |
| 2 | Its input in live shadow: **the recorder's own stream**, which moves to `processed` with client-side confirmation. It reaches the engine over a local relay. | **0 extra Triton GB**, and the earliest data. The recorder keeps writing confirmed segments, as Triton recommends (§4, §7). |
| 3 | Decoding comes from `lab/extractor` (the OF1-identical rows); the client, filters, meter and reconnect come from `lab/recorder`. | The rows are already byte-identical to OF1, and the tests exist (§2). |
| 4 | A shared crate `lab/core` holds venue math, costs, exit rules, FlowIndex, PositionSim and the F7 trigger. Rust uses it directly; the Python backtest uses it through PyO3 + maturin. | The same math by construction instead of "parity afterwards"; faster tournaments as a by-product (§12). |
| 5 | Parity: the Rust engine replays the same OF1 chunks or recorded segments, and writes the paper journal's SQLite schema. `paper.parity` compares the two journals: **0 unexplained**. | The proven method of jobs 90/92 (§3). |
| 6 | Alpenglow: yellowstone-grpc-client **15.0.0** with proto **14.0.1**; per-`(slot, bank_id)` buffers; decisions at `processed`, settled at `finalized`. | Triton's Alpenglow guide and the solana.com upgrade page (§7). |
| 7 | Metrics: Prometheus text on localhost and a JSON stats file. A separate watchdog process. cargo-fuzz and proptest. | §13–15. |

## 1. Goal and limits

**One Rust process.** Dragon's Mouth stream → pool and curve state → F7 trigger → the K3 decision and the H4/H5 arms (group H) → a simulated fill with the same cost model → the journal.

**Real latency.** Measure data → decision with a timestamp per step (§10).
- The Python paper engine cannot measure this. LiveFeed reads only **complete segments** (one per `--segment-secs` 60 or 150 slots; `lab/recorder/src/segment.rs`, `paper/live.py`).
- So it decides up to a minute after the trigger, against a *simulated* `d` (`signals.segment_lag_ms` in the journal).
- A live bot decides in the stream. Only a streaming engine shows whether 600 ms is reachable.

**Not in scope:**
- signing, sending, keys, wallets (§11);
- replacing the Python engine;
- deciding the stop rule. The stop rule stays the Python paper journal's (`paper/stoprule.py`; AGENTS.md).

**Paper only.** The process has no code path that builds or sends a transaction. A test asserts that the binary has no signing crate (`solana-sdk` signer, `ed25519-dalek`) in its dependency tree (`cargo tree -e normal`).

## 2. Reuse and what is new

### Reuse from `lab/recorder`

| what | where | change |
|---|---|---|
| The Yellowstone client: TLS, x-token, zstd, a 256 MiB message size, HTTP/2 keepalive | `src/main.rs session()` | Upgrade to client 15.0.0 and proto 14.0.1 (§7). |
| Filters, watchlist, probes, backfills, downscale levels | `src/watch.rs`, `src/recorder.rs` | None: the relay passes what the recorder receives. |
| The budget meter, fail-closed on an unknown spend | `src/meter.rs` (#265, #308) | None: one stream, one meter. |
| Reconnect with `from_slot`, `SubscribeReplayInfo`, lost slots recorded | `src/main.rs` | The recorder keeps it. The engine follows the relay. |
| `SlotBuffer` (closing per confirmed slot), segments, exit 74/75 | `src/segment.rs` | Made bank-aware (§9). |
| The conversion of Yellowstone to the extractor's transaction | `src/convert.rs` | None. |

### Reuse from `lab/extractor`

- The IDL and event decoding: `idl.rs` and `extract.rs` (`LabPlugin::rows_for`).
- Pump and PumpSwap events with **pre-trade reserves**, fees, mayhem and v2/v3: the same rows as OF1, column for column (`lab/recorder/README.md`).
- The Parquet sink for the replay input (`sink.rs`).

### New

The Python reference is in brackets.

- **Pool and curve state per key** from the events: pre-trade reserves, fee and creator bps, vq signed. [`backtest/replay.py` `_hist_state`, `backtest/venues.py`]
- **FarmerFlags:** per (pool, wallet), a 24 h window of net and gross token delta; a farmer when |net| < 5 % of gross with ≥ 2 trades. [`backtest/store.py` FARMER_*, `paper/live.py` farm seed]
- **The farm-hour window:** the share of farmer SOL volume in [t − 1 h, t), plus `farm_window_complete`. [`backtest/strategies.py` F6_FARM_*, `paper/engine.py _farm_window_complete`]
- **The F7 trigger:**
  - a sell ≥ max(3 SOL, 4 % of depth), depth ≥ 150 SOL;
  - net inflow > 0 over 50 ms;
  - no creator sell over 200 ms; no other trigger over 20 ms;
  - not the creator, coin_creator or mayhem agent.
  - [`backtest/strategies.py` `f7_signals_sql`, `f7_signal`]
- **FlowIndex** for the flow exits. [`backtest/strategies.py`, `paper/engine.py LiveFlowIndex`]
- **PositionSim:**
  - the entry at t + d at slot position q;
  - exits by kind: tp/T, trail, flow, flowtp, a time cap;
  - mayhem refused; a drained pool.
  - [`backtest/replay.py`]
- **Costs:** `Scenario`, `settle`. [`backtest/costs.py`]
- **Risk per group, busy, cooldown, and the paper wallets.** [`paper/risk.py`, `paper/engine.py`]
- **The journal writer:** the same SQLite schema. [`paper/journal.py TABLES`]

Most of "new" is a port of existing Python. §12 moves it to a shared crate, so the port is written once and used by both sides.

## 3. Parity: how we prove the Rust decisions equal the Python engine

1. **The same input.** Both engines read the same events:
   - **OF1 chunks** (`events/v1`) with the same store snapshot: the store's tables exported to Parquet once by a small Python tool, so the Rust side needs no DuckDB;
   - **or recorded segments** plus `PAPER_STORE` (the shadow and LiveFeed path).
2. **The same output format.** The Rust engine writes the paper journal's SQLite schema (`paper/journal.py TABLES`: signals, orders, fills, exits, positions, heartbeat, state). It also writes the state keys `groups`, `feed`, `cost_scenario` (#309).
3. **The comparison.** `paper.parity` gets `--journal-a/--journal-b` (Python against Rust). It compares row for row on (family, variant, key, t): status, entry slot, tokens, cost, exit slot and reason, proceeds, attempts, P&L.
   - **Requirement: 0 unexplained.** The causes are named like the existing ones (`unsold_busy`, `farm_incomplete`, …).
4. **Fixtures:** the dev chunk 450144000–450360000 (jobs 90/92), fwd1053-1054 (shadow 91), then a recorded day.
5. **In CI:** a small recorded fixture (a few segments) with a checked-in expected journal hash, run with `cargo test` on the NAS.
6. **Live (M3):** each day the Rust live journal is compared with the Python paper journal of the same day. Only `finalized` decisions count (§9); the decisions on a bank that was later discarded are reported apart.

## 4. Modes

| mode | input | Triton cost | when |
|---|---|---|---|
| (a) **replay** | OF1 chunks or recorded segments, plus the snapshot | none | M1/M2; also every NAS test |
| (b1) **live shadow on the recorder's stream** (recommended) | the recorder subscribes at `processed`. It relays every update over a Unix socket to `lab-live`, and still writes confirmed segments by buffering per (slot, bank_id) until the slot's confirmed/finalized status | **+0 GB**: the same filters, one stream. `processed` adds only the banks that are later discarded (< 1 % of slots under Alpenglow: Triton's guide) | M3 |
| (b2) **live shadow with its own stream** | its own Dragon's Mouth subscription, e.g. on Riptide (`riptide.rpcpool.com`, a lower-latency path for shared subscriptions; [Triton: Riptide](https://docs.triton.one/project-yellowstone/riptide)) | **~15–23 GB/day**: 23.16 GB/day with the paper filters (shadow job 85, `compare.md`), less with only the engine's pools. At $0.08/GB that is **$1.20–1.85/day, $36–56/month**. Needs the owner's OK | only when a check against a second path is wanted |
| (c) live trading | — | — | out of scope (§11) |

**Why b1.**
- One stream means one budget and one meter, and the decider sees exactly what the recorder records, so parity holds by construction.
- `processed` is Triton's recommendation for low latency, with commitment handled client-side: "For maximum performance, however, we recommend handling commitment levels client side" ([Dragon's Mouth gRPC Subscriptions](https://docs.triton.one/project-yellowstone/dragons-mouth-grpc-subscriptions)).

**The relay.**
- Each frame is length-prefixed: the update's prost bytes plus the recorder's receive time in ns.
- The recorder never blocks on it. When the socket buffer is full it drops the frame, counts it, and sends a gap marker.
- After a gap the decider marks itself stale until the next complete slot. That is a recorded gap, never a silent one.
- The cost is microseconds; it is measured in §10.

## 5. Milestones and estimates

The estimates are agent working days plus NAS test cycles. Rust is built and tested on the NAS bookworm runner only; there are no Rust builds on the VPS.

| milestone | content | estimate | done when |
|---|---|---|---|
| **M0** | `lab/core` skeleton: venue math (curve/pool buy and sell, exact-in/out, fee tiers, signed vq, synthetic migration), proptest, PyO3 bindings for `venues`; the Python backtest calls them behind a flag | 2–3 days | the A/B of a dev tournament is byte-identical, flag on vs off |
| **M1** | `lab-live` replay mode for **K3**: snapshot export, F7 trigger, PositionSim, costs, risk, journal writer; `paper.parity` against Python | 1.5–2.5 weeks | 0 unexplained on the dev chunk and fwd1053-1054 |
| **M2** | the **H4/H5 arms**: FlowIndex, flow and flowtp exits, farm window, `require_farm_complete`, groups and their own wallets | ~1 week | 0 unexplained with both arms (as jobs 90/92) |
| **M3** | **live shadow (b1)**: recorder at `processed` with bank-aware buffering and the relay; latency stamps; metrics; watchdog; Alpenglow handling | 1–1.5 weeks, then ≥ 2 weeks of running | a daily parity report with 0 unexplained among finalized decisions; latency histograms per step |
| **M4** | the shared core in the backtest: PositionSim and exits through PyO3 in `backtest/tournament.py` | 1–2 weeks | tournament A/B byte-identical; time measured |
| **M5** | robustness: cargo-fuzz on the decoders, proptest on the math, starting in M0 and continuous; CodeQL and cargo audit as now | continuous | fuzz targets in the NAS nightly; no open finding |

**VPS limits for M3:** `lab-live` gets its own unit with `MemoryMax=2G`, `CPUQuota=100%` and `Nice=5`, like `lab-recorder`. The recorder stays at 2G. Together they fit the owner's VPS rules: 16 GB, sessions at 4G, live services first.

## 6. Risks, and what we deliberately do not do

**Risks:**
- **Drift between two implementations.** Mitigated by the shared core (§12) and the parity requirement (§3).
- **Alpenglow behaviour that is new in production.** Bank replacement, the vote-only slots at the switch, and the second Clock write. Mitigated by §7–9 and by deciding nothing during the activation window.
- **Python semantics that are hard to port:** LiveFeed's backfill and farm seed, the watchlist builder, the busy and late-leg rules. Mitigated by porting with the same tests, and by keeping the watchlist builder in Python. The engine reads `watchlist-current.json` like the recorder does.
- **Network latency from the VPS to Triton** dominates the end-to-end time. It is measured, not assumed. Riptide (b2) is the comparison if needed.
- **Slow iteration:** Rust builds only on the NAS.

**What we do not do:**
- send, sign, or hold keys;
- use Deshred (pre-execution, paid beta) or Preconfirmations (Harmonic/BAM), though both are earlier signals ([Dragon's Mouth: Deshred](https://docs.triton.one/project-yellowstone/dragons-mouth-grpc-subscriptions), [Preconfirmations gRPC](https://docs.triton.one/chains/solana/preconfirmations-grpc)); later, with measurements;
- use yellowstone-block-machine: it forces every entry, all sysvars and processed, ~43 GB/day (an earlier analysis);
- replace the Python engine as the reference;
- let the Rust journal decide the stop rule.

## 7. Alpenglow (epoch 1064, about 2026-10-21 14:50–15:00 UTC)

**Activation:**
- The feature gate activates at the start of epoch 1064 (slot 459,648,000).
- TowerBFT runs for the first 5,000 slots; Alpenglow consensus takes over at **slot 459,653,000**.
- It needs Agave 4.3.
- Source: [solana.com/upgrades/alpenglow](https://solana.com/upgrades/alpenglow).

**Slot time:**
- solana.com: from 400 ms to **200 ms**, "reduced in four stages that completed in October 2026".
- Triton's guide: "Slots stay 250 ms until a separate upgrade cuts them to 200 ms."
- Our own measurement: **218.3 ms per slot** in epoch 1054 (store meta `ms_per_slot`, shadow job 85).
- **Consequence:** never a constant. The engine uses the Clock: ms per slot per epoch, measured, with the last measured epoch as fallback (`backtest/strategies.py Clock`). Live, it measures ms per slot from the block times and the footers' `block_producer_time_ns`.
- `d_ms 600` is about **2.4–3 slots** (`delay_slots`). At the switch the Clock carries the last epoch's rate until 1,000 slots of 1064 are measured (#225 / the Clock fallback).

**Finality:**
- From about 12.8 s to a **target of about 150 ms**: one round at ≥ 80 % stake, two at ≥ 60 %.
- `confirmed` and `finalized` "become the same thing"; `confirmed` will be removed later.
- Sources: [solana.com](https://solana.com/upgrades/alpenglow); [Triton: Alpenglow and your Triton stack](https://blog.triton.one/alpenglow-and-your-triton-stack-what-to-update-before-mainnet/).

**Commitment choice:**
- The engine subscribes (through the recorder) at **`processed`**: decide early, settle late.
- Each decision is journaled when made. Once the bank it rests on is `finalized`, it is marked settled; if the bank is discarded, it is marked `rolled_back`.
- Do not switch to `finalized` before activation (12.8 s waits; Triton's guide). The recorder's segments stay at confirmed until then, and finalized after.

**Multiple banks per slot:**
- From Agave 4.3, every Geyser event carries a `bank_id`. Buffer per **`(slot, bank_id)`** and keep the bank that reaches confirmed.
- `bank_id` is a per-node counter: match blocks across connections by blockhash only.
- Source: [solana.com](https://solana.com/upgrades/alpenglow); Triton's guide.
- **UpdateParent:** a leader that switches parents (fast leader handoff, Agave 4.4) clears the bank it started; the plugin reports it as an `update parent` message.
- Source: [solana.com](https://solana.com/upgrades/alpenglow); [yellowstone-grpc CHANGELOG 2026-09-25](https://github.com/rpcpool/yellowstone-grpc/blob/master/CHANGELOG.md).

**The activation window:**
- "For 2 slots during the switch, blocks contain only vote transactions. The cluster then deletes those slots and restarts from a single agreed block."
- **Rule:** in [459,648,000, the first finalized slot after 459,653,000 + a margin), the engine decides only on finalized data. Switch detection comes from the stream (the first block footer, `alpenglow.json` as the recorder writes it), "not on a date" (Triton's guide; JSON-RPC `getAgGenesisCert` is the RPC route).

**Vote-only slots and slot gaps:**
- Votes leave the blocks.
- The slot numbers jump at the switch. A `from_slot` inside the jump resumes at the next slot that exists, which the recorder already handles (#225, `a_slot_jump_closes_in_order...`).
- The `SlotBuffer` never waits for a missing slot number.

**Other changes:**
- **Ticks are gone:** block boundaries come from block meta and the block footer (solana.com; Triton).
- **The Clock sysvar is written twice per slot:** deduplicate by content, not by (slot, account) (CHANGELOG 2026-09-25, `DedupStream`).

**Client and proto versions:**
- Triton recommends **yellowstone-grpc-client 15.0.0 + yellowstone-grpc-proto 14.0.1** (Triton's guide; [crates.io](https://crates.io/crates/yellowstone-grpc-client)).
- The recorder pins 14.0.1 / 13.0.0 (`lab/recorder/Cargo.toml`). That still connects (fields are added, not renamed), but it does not see the typed footer certificates (breaking in proto 14.0.1: certificates moved to typed fields 9–11).
- Upgrade in M3 together with the relay, with cargo audit and CodeQL as always.

## 8. Rust client choices

**The client:**
- Use `yellowstone-grpc-client` (tonic 0.14, prost 0.14), as the recorder does; no hand-made tonic client.
- For the **engine's own subscription (b2)**, use `subscribe_with_reconnect`: a bank-aware reconnecting stream that requires `processed`. After a reconnect it emits `ReconnectEvent::DiscardBanks` (with finalized winners) before the replacement updates, and deduplicates.
- Source: the client source, `yellowstone-grpc-client/src/lib.rs` (`subscribe_with_reconnect`, "bank replay requires processed commitment without startup snapshots"); CHANGELOG 2026-09-25.
- The **recorder** keeps its own reconnect: confirmed, `SubscribeReplayInfo`, `lost_slots` recorded. With b1 it moves to `processed` and can adopt the same API; M3 decides that on a test.

**Zero-copy:**
- prost decodes into owned buffers; the prebuilt proto crate cannot be configured for `bytes::Bytes`.
- The costs that matter are decoding only the pump and PumpSwap instructions (the extractor already does) and avoiding allocations per row: the extractor's `Row` is `Vec<Val>`.
- Fixed-layout account data (pool accounts, vault slices) is read in place (`bytemuck`).
- **Rule: profile first** (§10). Optimize what the histograms show; keep the extractor's decode for parity.

**The tokio architecture:**
- **Network:** a task per subscription (main, vault, backfills) only reads and forwards (`try_send`) into a bounded queue.
- **Engine:** one synchronous thread, not a tokio task, owns all state with no locks. It processes updates in arrival order and evaluates a slot's triggers once that slot's bank is complete (§9). The same thread runs the replay mode, so the order is identical by construction.
- **Writer:** a thread with SQLite in WAL mode (as `paper/journal.py`), committing per minute and on every decision.
- **Metrics:** atomics and HdrHistograms, read by the exporter.

**Backpressure and slow consumers:**
- The Yellowstone server keeps a bounded queue per client. When it is full, it ends the stream with `Status::internal("lagged to send an update")` (`client_channel_full`); a lagging broadcast receiver ends with "lagged to receive geyser messages".
- Source: `yellowstone-grpc-geyser/src/grpc.rs`; the [Preconfirmations docs](https://docs.triton.one/chains/solana/preconfirmations-grpc) state the same rule: "A stream that cannot keep up ends with an explicit RESOURCE_EXHAUSTED status. Nothing is ever thinned silently".
- So the network task must **never await the engine**.
- Our queue depth and its oldest item's age (now − receive time) are metrics. A full internal queue is a "slow consumer" incident: count it, raise an alert, mark a gap, and resume through `from_slot` once the engine has caught up.

**Reconnect with `from_slot`:**
- Replay covers only the server's retained window (`SubscribeReplayInfo.first_available`). It starts at a slot boundary, so duplicates are possible and must be deduplicated.
- Source: [Dragon's Mouth: Replaying from a slot](https://docs.triton.one/project-yellowstone/dragons-mouth-grpc-subscriptions).
- A server fix of 2026-10-12 (geyser 16.1.2) delivers a bank that was in progress at a processed `from_slot` "whole when it seals" (CHANGELOG).

## 9. Correctness

**State from events, not from accounts:**
- Every PumpSwap and Pump event row carries the **pre-trade** reserves and fees. The replay is validated against OF1: exact replay of every historical fill (`backtest/replay.py`, week2-defaults `validation_gates`).
- The `Pool` account is written on every trade (~15 M updates/day for all pools), but an account update has no position within the slot beyond `write_version`. It cannot place our entry at q between two transactions.
- So the state comes from events, ordered by (slot, `tx_index`, outer, inner). The accounts (vault slices, `pool_accounts`) serve only to seed a pool first seen mid-life, and as a check.
- Signed vq: `effective_quote = vault + virtual_quote_reserves` (i128) ([pump-public-docs: Negative virtual quote reserves](https://github.com/pump-fun/pump-public-docs/blob/main/docs/NEGATIVE_VIRTUAL_QUOTE_RESERVES.md)).

**Order within a slot:**
- `SubscribeUpdateTransactionInfo.index` is the position in the block.
- At processed, the docs promise no order of arrival within a slot (we found no source either way), so the engine never relies on it.
- The engine evaluates a trigger at slot t only when **t's bank is complete** (its block meta at processed, or the slot status), with every index < the trigger's present. The entry at t + d (d ≥ 2 slots) is evaluated the same way. This adds at most the rest of slot t to the decision; it is measured as its own step (§10).

**Forks and rollbacks:**
- Every state change is kept per `(slot, bank_id)` as an overlay on the finalized state, until that bank is finalized (about 150 ms after Alpenglow) or discarded (UpdateParent, a skip, `DiscardBanks`).
- Decisions on a discarded bank are journaled `rolled_back` and never settle.
- Under TowerBFT (before slot 459,653,000) the overlay window is longer (confirmed ~1 s, finalized 12.8 s). The overlay then settles at confirmed, as the Python engine's segments do.

## 10. Performance and measurement

**Timestamps per step** (ns, `CLOCK_REALTIME`; the VPS clock is NTP-synchronised, offset ~0.1 ms):

| stamp | where |
|---|---|
| `block_time` / `block_producer_time_ns` (footer) | the chain |
| `created_at` | Triton's server, in the update |
| `t_recv` | `stream.next()` returns in the recorder |
| `t_relay` | the engine reads the frame |
| `t_decoded` | the extractor's rows |
| `t_state` | the pool state is updated |
| `t_slot_complete` | the trigger's bank is complete |
| `t_decision` | the decision is journaled |
| `t_journal` | the writer has committed |

- An HdrHistogram per interval (`hdrhistogram` 7.6, [crates.io](https://crates.io/crates/hdrhistogram)).
- Stream lag = now − the block time of the last slot.
- Every decision row also gets the stamps, so `paper.report`'s latency section can show measured against simulated `d`.

**Load and memory:**
- Shadow job 85 counted 16.4 M transactions over 2.18 days, **~90 tx/s on average**, with peaks of ×10.
- Rust decoding plus state needs a fraction of a core. The memory budget is **1–2 GB** (`MemoryMax=2G`):
  - per pool: reserves and a FlowIndex of 300 s;
  - per (pool, wallet): the 24 h farmer sums (a compact struct);
  - legs at ~48 B: 24 h of watched pools at ~5 M legs/day is ~240 MB.
- Every map is bounded by pruning, as `TOKEN_KEEP_DAYS` / `pool_seen` are in Python (#299).

**Profiling plan:**
1. Replay a recorded day at full speed on the NAS: throughput and the step histograms.
2. `perf` + `cargo flamegraph` on that replay.
3. Heap through jemalloc statistics (`tikv-jemallocator`) or `dhat`.
4. `tokio-console` for the network tasks.
5. Optimize only what the histograms show.

## 11. Later: real sending (a chapter only, not built)

- **A separate small signing binary.** It holds the key, has its own kill file and its own limits, and takes only orders from `lab-live` over a local socket. `lab-live` never sees a key.
- **Transactions:**
  - pump and PumpSwap instructions as in pump-public-docs;
  - a tight compute-unit limit and a priority fee per write-locked account (percentile-based);
  - `maxRetries: 0`, `skipPreflight: true`;
  - our own retries, with a blockhash from `confirmed` or `finalized`, never `processed`.
  - Source: [Triton: Transaction sending advice](https://docs.triton.one/chains/solana/cascade/sending-txs).
- **The route:**
  - **Triton Cascade:** SWQoS over staked connections; delivery, not inclusion ([Triton: Transaction handling](https://docs.triton.one/chains/solana/cascade)).
  - **Jito:**
    - `sendTransaction` takes a priority fee plus a tip, suggested 70/30.
    - `sendBundle` takes up to 5 transactions with a tip only; the minimum tip is 1,000 lamports; there are 8 tip accounts, one picked at random, with the tip in the same transaction.
    - The default limit is 1 request/s per IP per region.
    - Source: [Jito: Low latency transaction send](https://docs.jito.wtf/lowlatencytxnsend/).
  - **BAM:** direct bundle ingest, whitelist-only so far.
- **Landing measurement:** record the send time and signature; measure the time to `processed` and `finalized`, and the execution result. Preconfirmations are an earlier signal, not a confirmation (Triton docs).
- **The paper parallel:** the Rust journal's simulated fills become the benchmark for real fills.

## 12. One shared Rust core (`lab/core`) for backtest and live

**What moves, in order of value against risk:**

| step | Python today | lines | test |
|---|---|---|---|
| 1 | `backtest/venues.py`: curve and pool math, fees, exact-in/out, rev | 221 | proptest invariants and a byte-equal A/B of a tournament |
| 2 | `backtest/costs.py`: Scenario, settle | 63 | unit tests + A/B |
| 3 | `strategies.py` exit rules and FlowIndex: tp/T, trail, flow, flowtp, caps | ~300 of 1,008 | A/B of exits per variant |
| 4 | `replay.py` PositionSim: entry at q, the holdings, mayhem, a drained pool, end() | ~550 of 724 | A/B of a dev tournament and the stream-end test |
| 5 | the F7 trigger and the farm window (today SQL in the backtest; per event in Python live) | — | the trigger lists, equal |

- **Bindings:** PyO3 **0.30.1** with maturin **1.15.0** ([crates.io](https://crates.io/crates/pyo3)), an abi3 wheel for the lab Python (3.13).
- Pass whole legs per position, not per call: the call overhead per position is microseconds against ~1,500 positions/s today.
- `Python::detach` (the `allow_threads` of before PyO3 0.26) frees the GIL for parallel tournaments ([PyO3 guide: parallelism](https://pyo3.rs/main/parallelism)).
- **Migration:** behind a flag per step (`LAB_CORE=rust`). Each step merges only with a byte-identical A/B of the tournament rows, the way #301 compared 393,359 rows.
- **By-product:** faster tournaments. That is not a goal; it is measured per step.
- **What stays Python:** the store, SQL triggers over history, reports, the watchlist builder, research. The core holds only the per-event math both sides need.

## 13. Metrics and alerts

- **Export:** the `metrics` crate 0.24 with `metrics-exporter-prometheus` 0.18 on `127.0.0.1` (and on Tailscale only with the owner's OK for the NAS Grafana). The same numbers also go to a JSON stats file every 10 s, which the web UI and the watchdog read.
- **Metrics:**
  - latency histograms per step (§10);
  - stream lag against slot time;
  - reconnects and gaps (slots);
  - queue depth and age;
  - budget (spent GB);
  - open positions and P&L per group;
  - rolled-back decisions;
  - the parity status of the last day.
- **Alerts (a push to the owner):**
  - a stop or exit;
  - a gap above N slots;
  - budget at 80 % and 100 %;
  - lag above X s;
  - the day loss limit hit;
  - a missing heartbeat.
- The channel (ntfy or Telegram) is the owner's choice; the coordinator raises it. Grafana on the NAS gets only the owner's OK.

## 14. An independent kill switch (watchdog)

- **A separate small process** (`lab-watchdog`, its own unit). It is not part of `lab-live`.
- **It reads:** the heartbeat in the stats file, the journal's day P&L per group, and the wallet balances (the paper wallets now; real balances later, read-only).
- **It stops `lab-live`** (`systemctl --user stop`, plus a kill file in `locks/`, as `disk-stopped` does today) when:
  - the day loss is above the risk file's limit;
  - no heartbeat for 60 s;
  - balances differ from the journal;
  - the stats file is missing.
- **On restart:** the units carry `ConditionPathExists=!` on that file, so only the coordinator removes it.
- **For live trading it becomes mandatory.** The signing binary (§11) also refuses while the kill file exists.

## 15. Robustness

- **cargo-fuzz** (libFuzzer, `libfuzzer-sys` 0.4) on the decoders, which parse untrusted chain data: the instruction and event decode (`idl.rs`, `extract.rs`), `convert.rs` and the account slices. They run nightly on the NAS. A crash corpus goes into the repo as regression tests.
- **proptest** 1.12 on the core math:
  - constant-product invariants: k never falls after fees;
  - exact-in and exact-out are inverse within rounding;
  - no overflow with signed vq;
  - the fee tiers are monotone in market cap.
- **Differential tests** against the Python implementation on random legs, until step 4 of §12.
- **CI as now:** CodeQL Rust on the branch through `workflow_dispatch` before every merge (memory: Rust CodeQL does not run on PRs), cargo audit through the bookworm runner, and dependency review.

## Open questions for the owner and coordinator

1. **b1** (the recorder moves to `processed` and relays; +0 GB) or **b2** (its own stream, +$36–56/month)? Recommended: b1.
2. Should M0 (the shared core with PyO3) come before M1? Recommended: yes, its first step only (venues), because M1 needs that math in Rust anyway.
3. The alert channel (ntfy or Telegram), and Grafana on the NAS: the owner's choice.
