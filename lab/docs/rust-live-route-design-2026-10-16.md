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
| 2 | Live shadow input: **b1**, the recorder's own stream at `processed` over a local relay (+0 GB), or **b2**, its own engine-only stream (~7–12 GB/day, its own budget). The owner decides before M3; the recommendation is b1 first, then b2 beside it (§4). | b1 means parity on identical input; b2 measures the live configuration and is independent of the recorder (§4). |
| 3 | Decoding comes from `lab/extractor` (the OF1-identical rows); the client, filters, meter and reconnect come from `lab/recorder`. | The rows are already byte-identical to OF1, and the tests exist (§2). |
| 4 | A shared crate `lab/core` holds venue math, costs, exit rules, FlowIndex, PositionSim and the F7 trigger. Rust uses it directly; the Python backtest uses it through PyO3 + maturin. | The same math by construction instead of "parity afterwards"; faster tournaments as a by-product (§12). |
| 5 | Parity: the Rust engine replays the same OF1 chunks or recorded segments, and writes the paper journal's SQLite schema. `paper.parity` compares the two journals: **0 unexplained**. | The proven method of jobs 90/92 (§3). |
| 6 | Alpenglow: yellowstone-grpc-client **15.0.0** with proto **14.0.1**; per-`(slot, bank_id)` buffers; decisions at `processed`, settled at `finalized`. | Triton's Alpenglow guide and the solana.com upgrade page (§7). |
| 7 | Metrics: Prometheus text on localhost and a JSON stats file. A separate watchdog process. cargo-fuzz and proptest. | §13–15. |
| 8 | Cores: a deterministic decision loop on one pinned core (vCPU 7), with network, decoding, journal and metrics in parallel around it; live units first through `CPUWeight=` and `CPUAffinity=`; release with fat LTO and `x86-64-v3` (znver4 only if measured faster); rayon over the NAS's cores for the backtest. | The owner, 2026-10-17; measure first (§16). |

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
   - **The event stream first (M1 C1a).** `lab-live legs` writes every event `paper.feed.ReplayFeed` hands the engine, built from the chunks and the snapshot, as `paper.legs_dump` writes it from the store. The two are compared by count and sha256 per slot window (`paper/tests/test_lab_live_legs.py`; the spec is `research/m1-c1a-legs-spec-2026-10-19.md`). C1a-1 covered fwd1053, which starts at its store's first slot: 26,974,826 events equal (#369, NAS 99z).
   - **C1a-2: a start inside a store, such as the dev chunk.** The store flagged a trade at s in chunk C over [s − h24(C), s − 1], with history from the store's earlier chunks. So the export (`paper.snapshot_export --history-from/--history-to`) writes the store's own buys and sells over [H0, from), with H0 = max(store start, min over the emitted chunks C of (max(from, C.lo) − h24(C))). It writes only the (pool, trader) keys that trade in [from, to), because no other key's history is in a window; for the dev chunk that is 2.3 M of 22.25 M rows. The file is tied to its [from, to).
   - The export's meta.json (snapshot_version 2) also carries the store's chunk_list, each chunk's h24 as the store computed it, meta_bounds, ms_per_slot and the farmer rule. `lab-live legs` refuses a chunk whose slots, extractor build or h24 are not the store's.
   - **The history window is exact for any h24 pattern.** A chunk drops only what no window of it *or of a later chunk* reaches (`keeps`: the suffix minimum of start − h24). The C1a-1 rule (C.lo − h24 of the current chunk) would lose rows when a later chunk's h24 grows by more than a chunk. That does not happen in dev2 or fwd1053, but a test pins the case.
   - **The farmer flag in replay is the store's, not LiveFeed's.** The reference for M1 is ReplayFeed, which reads the flag the store computed. LiveFeed's `FarmerFlags` (paper/live.py) differs from it in three ways:
     1. **The boundary.** It compares `abs(net) < 0.05 * gross` in floats. The store compares exactly (DECIMAL), so the Rust replay uses `20 * |net| < gross` in i128. Near the boundary the float version can flag a trade the store does not (spec §3.4).
     2. **h24.** LiveFeed derives the 24 h window in slots from a rolling slot time. The store uses one h24 per chunk, from that chunk's own blocks.
     3. **The window only moves forward** in LiveFeed. With h24 changing per chunk, the store's window start can move back at a chunk boundary (by ~394 slots in fwd1053).
     - Rust replay stays store-exact. After the paper start, a NAS job measures how many farm flags and signals LiveFeed and ReplayFeed differ by on the same chunk. With that number the coordinator decides before M3 whether Python's LiveFeed becomes exact (an engine PR with LAB review) or M3's Rust LiveFeed copies it with a documented difference.
   - **C1b: the signals.** `lab/core` holds the engine's signal state per slot (`clock`, `exact`, `universe`, `f7`, `farm`, `n2`, `signals`; spec `research/m1-c1b-signals-spec-2026-10-19.md`). It decides only up to the farm gates; busy and risk are C2/C3. Its unit tests pin rows the Python engine wrote on small synthetic feeds (`core/src/signals/cases.rs`). `lab-live signals` (PR 3) writes them into the journal's signals table, compared with `paper.journal_diff --scope signals`.
   - **C1b in lab-live (PR 3).** The legs reader hands out typed events (lab/core's `Event`): `lab-live legs` renders its lines from them, byte for byte as in C1a, and `lab-live signals` feeds the same events to the signal state. The universe, dev bounds and clock come from the snapshot (meta_bounds, and ms_per_slot read to Python's bits); the groups are the Python journal's state 'groups', written back verbatim. A row past the farm gates keeps a NULL status. The shared golden cases (`paper/tests/data/c1b_signal_cases.json`) also run in lab/live's tests, with the real sha256 seeds of N2. The `lab-live signals` log line counts the universe rejections per cause, quote NULL among them.
   - **C1b acceptance (`lab/live/nas_m1b.sh`).** The unit and golden tests of lab/live and all of lab/core; the legs regression on fwd1053 and on dev2's arm range; then R1 (dev2 450128159–450380000) and R2 (the whole of fwd1053), each green under `journal_diff --scope signals`, with T1 and T2 in neither journal and every row inside the universe and the decision bounds. R3 (fwd1053 with live bounds) waits for paper.engine's `--dev-bounds`, after the paper start; `lab-live signals` already takes `--dev-bounds`.
   - **C2: the positions** (spec `research/m1-c2-positionsim-spec-2026-10-19.md`). First the signal state gets hooks (C2-4a, `Hooks` in `core/src/signals.rs`). SignalEngine calls them where `_end_slot` runs each step: steps 1-2b before the history, busy and the Pending for each row past the farm gates, the hourly sweep's keep-set, and step 5 after the farm windows. So C2-4b's PaperEngine adds the positions without moving a step. `lab-live signals` passes no hooks, so its rows stay as in C1b, and nas_m1b.sh R1/R2 must stay green.
5. **In CI:** a small recorded fixture (a few segments) with a checked-in expected journal hash, run with `cargo test` on the NAS.
6. **Live (M3):** each day the Rust live journal is compared with the Python paper journal of the same day. Only `finalized` decisions count (§9); the decisions on a bank that was later discarded are reported apart.

## 4. Modes

| mode | input | Triton cost | when |
|---|---|---|---|
| (a) **replay** | OF1 chunks or recorded segments, plus the snapshot | none | M1/M2; also every NAS test |
| (b1) **live shadow on the recorder's stream** (recommended) | the recorder subscribes at `processed`. It relays every update over a Unix socket to `lab-live`, and still writes confirmed segments by buffering per (slot, bank_id) until the slot's confirmed/finalized status | **+0 GB**: the same filters, one stream. `processed` adds only the banks that are later discarded (< 1 % of slots under Alpenglow: Triton's guide) | M3 |
| (b2) **live shadow with its own stream** | its own Dragon's Mouth subscription with the engine-only filter below. Optionally on Riptide (`riptide.rpcpool.com`, a lower-latency path for shared subscriptions; [Triton: Riptide](https://docs.triton.one/project-yellowstone/riptide)) | **~7–12 GB/day** with the engine-only filter; ~17 GB/day when it follows every deep reversed pool itself. At $0.08/GB that is **$17–29/month** (or ~$41). It has its own budget. Needs the owner's OK | the owner leans to b2 ("as much Triton as possible"); the decision falls before M3 |
| (c) live trading | — | — | out of scope (§11) |

**Why b1 is the default.**
- One stream means one budget and one meter, and the decider sees exactly what the recorder records, so parity holds by construction.
- `processed` is Triton's recommendation for low latency, with commitment handled client-side: "For maximum performance, however, we recommend handling commitment levels client side" ([Dragon's Mouth gRPC Subscriptions](https://docs.triton.one/project-yellowstone/dragons-mouth-grpc-subscriptions)).
- b1 is Triton data too: the same Dragon's Mouth stream, read once.

**The relay (b1).**
- Each frame is length-prefixed: the update's prost bytes plus the recorder's receive time in ns. The recorder forwards each frame before its own decoding.
- The recorder never blocks on it. When the socket buffer is full it drops the frame, counts it, and sends a gap marker.
- After a gap the decider marks itself stale until the next complete slot. That is a recorded gap, never a silent one.
- The cost is microseconds; it is measured in §10.

### b2 in detail: the engine-only filter, its volume, its own budget

**What the engine needs.** K3 and group H trade **`pool_reversed` only** (`paper/k3-paper-2026-10-12.yaml`, `paper/h-arms-2026-10-16.yaml`). So the engine-only stream holds:

| stream | why | from the recorder's filters |
|---|---|---|
| transactions of the **reversed pools on the F7 watchlist**, and of the pools of open positions | triggers, depth, flow, farm labels, exits | `tx_pools`, restricted to the reversed entries |
| **probes** of new reversed pools (≥ 120 SOL), with their backfill | a pool's first hours and its farmer history before it is on the watchlist | `tx_probes` and backfills, reversed only |
| creation of reversed pools | new pools within seconds (99 % of F7's pools are created that day) | `tx_new_reversed`, `pool_creations` |
| block meta, slot statuses, block footers | clock, bank completion, Alpenglow | as the recorder |

**Not needed:**
- `--pump-creates`: no reversed pool has a pump.fun mint (dev2: 0 of 54,027), so F7 takes the pool's `coin_creator` there;
- the normal-pool and M1 watchlist entries;
- vault balances and pool accounts: the state comes from events, §9.

**Volume.** Measured on the store `fwd1054` (epoch 1054, 26.2 h, scaled to 24 h), with the #180 convention of 3 KB per transaction:

| set of transactions | per 24 h | GB/day |
|---|---|---|
| every SOL-market pool | 18.2 M | 54.7 |
| reversed pools, ever ≥ 150 SOL, followed all day | 5.7 M | 17.2 |
| reversed pools, while ≥ 120 SOL | 5.5 M | 16.5 |
| the recorder's whole paper stream (shadow job 85: watchlist + probes + backfills + creations) | ~7.5 M | 23.2 |

- The recorder does not follow every deep pool all day. Its watchlist adds and prunes pools by the F7 and M1 rules, giving 4.96 M watchlist transactions a day.
- Reversed pools carry ~38 % of all pool transactions (7.0 M of 18.2 M a day).
- The engine-only share of the recorder's stream is therefore about 40 % of the watchlist plus the reversed probes, backfills and creations: **~2.4–4 M transactions a day, ~7–12 GB/day.**
- An engine that keeps its own watchlist without the Python builder, following every reversed pool ≥ 150 SOL all day, needs **~17 GB/day**.
- The bytes per transaction come from the measuring hour (`_segment.json` usage per stream). Until then, 3 KB is an estimate.

**Its own budget and meter.**
- `lab-live` runs its own `Meter` (`lab/recorder/src/meter.rs`) in its own root (`<lab>/live-engine/budget.json`).
- It has its own `--budget-usd` (for example $1/day, 12.5 GB) and the same fail-closed rules as the recorder (#265, #308): a hard stop at the cap, and an unknown spend stops the day.
- Its downscale order: probes first, then backfills, then the watchlist pools; the pools of open positions and the creations last.
- The Triton bill is the sum of both streams. The owner sets both caps.

**Its watchlist.**
- At first b2 reads `watchlist-current.json`, which the Python watchlist builder writes from the recorder's data. b2 is then independent of the recorder's **stream**, but not of its watchlist.
- Full independence needs the builder ported to Rust (after M3), or the ~17 GB/day variant.

### b1 against b2, honestly

| | b1: the recorder's stream + relay | b2: its own stream |
|---|---|---|
| Triton cost | +0 | +$17–29/month (+~$41 when fully independent) |
| latency, network | the same path as the recorder | the same, or lower on Riptide. Its own smaller stream has no head-of-line blocking behind the recorder's probes and backfills on one HTTP/2 connection |
| latency, local | +µs for the relay (measured) | none |
| what it measures | the latency of the recorder's configuration | **the configuration a live bot would run**: its own filter, connection, reconnect and budget |
| robustness | one point of failure. A recorder restart (exit 74/75, a deploy) stops the engine too, and the recorder's downscale can drop streams the engine needs | independent reconnect, budget and downscale. A recorder failure does not stop it, and the reverse |
| parity with Python | the same input bytes: differences are only logic | two inputs: a gap on one side is a difference that needs a cause. Both are reconciled against OF1 (`paper.reconcile`) |
| work | the recorder moves to processed and gets the relay | the engine gets its own client, meter and filter, mostly reused from the recorder (§2) |

**Recommendation.**
- **M3a = b1:** cheap, and parity of logic on identical input.
- **M3b = b2**, once M3a is green: the live configuration, measured beside b1 for a week (b1 against b2 latency on the same updates), then on Riptide if the owner wants.
- If the owner chooses b2 directly, M3 is b2 and the parity report attributes gaps through OF1 reconciliation.

## 5. Milestones and estimates

The estimates are agent working days plus NAS test cycles. Rust is built and tested on the NAS bookworm runner only; there are no Rust builds on the VPS.

| milestone | content | estimate | done when |
|---|---|---|---|
| **M0** | `lab/core` skeleton: venue math (curve/pool buy and sell, exact-in/out, fee tiers, signed vq, synthetic migration), proptest, PyO3 bindings for `venues`; the Python backtest calls them behind a flag | 2–3 days | the A/B of a dev tournament is byte-identical, flag on vs off |
| **M1** | `lab-live` replay mode for **K3**: snapshot export, F7 trigger, PositionSim, costs, risk, journal writer; `paper.parity` against Python | 1.5–2.5 weeks | 0 unexplained on the dev chunk and fwd1053-1054 |
| **M2** | the **H4/H5 arms**: FlowIndex, flow and flowtp exits, farm window, `require_farm_complete`, groups and their own wallets | ~1 week | 0 unexplained with both arms (as jobs 90/92) |
| **M3** | **live shadow**: M3a = b1 (recorder at `processed` with bank-aware buffering and the relay), then M3b = b2 beside it (its own client, filter, meter and budget), or b2 directly if the owner so decides (§4); latency stamps; metrics; watchdog; Alpenglow handling | 1–1.5 weeks, then ≥ 2 weeks of running | a daily parity report with 0 unexplained among finalized decisions; latency histograms per step |
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
- **Measured 2026-10-19 (NAS job 99u, #367): the in-memory read does not fit one chunk.** M1 part 1's `lab-live replay` holds every event row of the range in memory before the sort, at roughly 1.5 KB a row. One dev chunk (216,000 slots, 15.5 M event rows, 4.4 GB Parquet) was killed after 33 s at the runner's 24 GB.
  - Streaming is therefore a **precondition** for M1 parity on the whole dev chunk and fwd1053-1054, not an optimisation.
  - Slot windows with row-group pruning do not work on these files. Each part file (200,000 rows, one row group) spans almost the whole chunk, e.g. slots 450144003–450327858, because the extractor writes in parallel. A window would read all 4.4 GB again.
  - The rows are wide: 57–64 columns each. Trades are 14.8 M of the 15.5 M rows (SellEvent 7.1 M, BuyEvent 5.6 M, TradeEvent 2.1 M).
  - The plan, as its own PR before C1's decisions:
    - process part by part in slot order (chunks are disjoint slot ranges; recorded segments are small);
    - turn each row into a compact typed leg as the store builds it, with interned keys and only the engine's fields (~150 B instead of ~1.5 KB a row), instead of holding the generic rows.
    - One chunk then needs ~2 GB. The order key is unique (key_ties 0), so the sort within a part keeps the replay order.
  - #367's baseline (1/16, 1/8 and the quarters of the chunk) gives the bytes per row it starts from.
  - **As built in C1a (`lab-live legs`):** the sort unit is the **chunk**, not the part. Each part file spans almost the whole chunk, so sorting part by part would not give chain order, and a k-way merge of 70+ unsorted parts buys nothing. Per chunk, every row of the range is read with only its columns (a Parquet projection), turned into a compact leg (`CurveLeg` 128 B, `PoolRow` 112 B, keys interned) and sorted once. Then key ties are checked (the gate: none, or an error), the farmer flags are set and the events emitted, before the next chunk. The chunks are disjoint and read in slot order. The first NAS run logs the peak RSS per chunk.

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
- **Every exit closes the token account** (a requirement from the cost model, Codex review of c29c545, decided 2026-10-19):
  - **The model.** The paper and backtest settle every exit as full: the ATA rent comes back, and any remainder is worth 0 (lab/core `costs.rs` and backtest/costs.py `exit_net`). The week-2 prep note already meant this ("terug via CloseAccount bij volledige exit (+~3k CU)").
  - **What the real exit must do.** Empty the account and close it. A non-native account closes only at a zero token amount (SPL Token `interface/src/instruction.rs`, CloseAccount and Burn).
    - **The cheapest route:** in the exit transaction, sell, then `BurnChecked` the remainder, then `CloseAccount`. A later transaction also works, for one more fee. A Token-2022 close costs about 1.5k CU.
    - **Where the remainder comes from:** mostly fee dust. A reversed-pool sale leaves at most one raw unit per fee component (≤ 3–4) in about 0.3 % of the exits. A failed final exit (proceeds 0) leaves the whole balance, and needs the same close.
  - **What can block it (Token-2022):**
    - **withheld transfer fees:** these need the permissionless `HarvestWithheldTokensToMint` first (token-2022 `interface/src/instruction.rs` CloseAccount; the Token-2022 extension guide);
    - **PermissionedBurn:** the owner cannot burn, so the remainder is transferred out instead;
    - **a frozen account or a paused mint:** the account cannot be emptied, and the rent is lost.
  - **The data (2026-10-19):**
    - **Pump-created mints are Token-2022:** CreateEvents of epochs 1033–1051 show 1,055,323 `create_v2` (Token-2022) against 3,883 classic `create`. pump's COIN_CREATION.md describes the mint as "initialized with `decimals = 6`, `mint_authority`, `token_program`, and metadata pointer set to `mint`", with no transfer fee.
    - **The scenario's `ata_rent` of 1,513,840** matches a 170-byte Token-2022 ATA on mainnet.
    - **But most modelled positions are on other mints.** In long-dev-1033-1046, 93 % of the traded positions are reversed pools on 20,204 mints that pump did not create in that window, and their token programs and extensions are unknown. A transfer-fee mint needs the harvest above, and has a larger ATA (≥ 182 B, about 1,574,800 lamports).
    - **Before the first real exit, read the mint accounts** (an RPC call, the owner's OK): the program, the extensions, the freeze and pause state, PermissionedBurn. Either refuse mints the close cannot handle, or count their rent as lost.
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

- **Export:** the `metrics` crate 0.24 with `metrics-exporter-prometheus` 0.18 on `127.0.0.1`. The same numbers also go to a JSON stats file every 10 s, which the web UI and the watchdog read.
- **Grafana** (owner, 2026-10-16: yes):
  - a separate folder "Solana" in the owner's existing Grafana on the NAS, with a **read-only** data source (Prometheus scraping the endpoint, or the JSON file);
  - the endpoint stays on localhost; the link over Tailscale comes later, set up with the owner;
  - the metric names are stable and prefixed `lab_live_`, so the dashboards survive versions.
- **Metrics:**
  - latency histograms per step (§10);
  - stream lag against slot time;
  - reconnects and gaps (slots);
  - queue depth and age;
  - budget (spent GB, per stream);
  - open positions and P&L per group;
  - rolled-back decisions;
  - the parity status of the last day.
- **Alerts** (owner, 2026-10-16: the channel is decided later):
  - The alert point is **generic**: an `alert_webhook_url` in the config, **empty by default**.
  - When empty, no alert is sent: the alerts still go to the log, the stats file and the metric `lab_live_alerts_total{kind}`.
  - When set, each alert is one JSON POST (`{"kind", "text", "unix", "host"}`), with a timeout, no retries in the engine thread, and no secrets in the text. ntfy, Telegram (through a small relay) or a Grafana contact point can sit behind it.
  - The alerts:
    - a stop or exit;
    - a gap above N slots;
    - budget at 80 % and 100 %;
    - lag above X s;
    - the day loss limit hit;
    - a missing heartbeat.

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

## 16. Core use and CPU

The owner asked on 2026-10-17 that the Rust code use the processor and its cores optimally, in favour of the trading bot. That does **not** mean "everything in parallel":
- **The decision loop stays deterministic, on one dedicated core.** The same order as a replay is what makes parity provable (§3).
- **Everything that is not a decision runs in parallel around it:** network, decoding, journal writing, metrics.
- **Measure first, then change, in this order:** the algorithm, then the allocations, then parallelism, then CPU-specific instructions.

The Python paper engine just showed why (profile of 2026-10-17, VPS, K3 + H, 30 one-minute segments):
- `FarmerFlags.flag` summed a wallet's whole 24 h window per trade, which was 60 % of a restart's replay.
- A sliding window made it 47× faster (65.8 s → 1.4 s), so the whole replay ran 2× faster on the same single core.
- More cores would not have found that.

### 16.1 Live (`lab-live`): which thread, which core

| step | thread(s) | vCPU (§16.2) | in → out | budget, p99 per transaction |
|---|---|---|---|---|
| Triton stream(s): receive, stamp `t_recv`, a sequence number | Tokio runtime with 1–2 worker threads, network only | 6 | gRPC → bounded queue of raw updates | ≤ 20 µs |
| Decode: extractor rows (`idl.rs`, `extract.rs`) | 2 decoder threads (more only if the histogram asks) | 6 (and 5, if the Python paper is off) | updates → decoded rows with (slot, tx_index, outer, inner) | ≤ 50 µs |
| Re-sequence: chain order per slot, a slot released once the next one was seen or its block meta/footer arrived (as LiveFeed's lag) | 1 thread | 6 | rows → ordered events | ≤ 10 µs |
| **Decision loop:** state (pools, 24 h farmer sums, flow windows), F7 trigger, risk, positions, exits | **1 thread, pinned** | **7** | events → decisions and journal rows | ≤ 20 µs per event |
| Journal writer: SQLite batches, commit per minute with the HWM (`paper/restart.py`) | 1 thread | 6 | journal rows → disk | off the hot path |
| Metrics: the HdrHistograms per step (§10), the stats file, Prometheus text | 1 thread, low priority | 6 | stamps → files | off the hot path |
| Watchdog (§14) | its own process and unit | 0–3 | stats file, journal → stop | — |

- **Why decoding can be parallel:** it is pure: no state, the same input gives the same rows. The re-sequencer restores the order before the decision loop sees anything, so the decisions do not depend on how many decoders ran or in which order they finished.
- **CPU-bound work stays out of Tokio's async workers.** The Tokio docs say: "you should use a separate thread pool dedicated to CPU bound tasks" (Tokio 1.53, "CPU-bound tasks and blocking code"). `worker_threads` defaults to "the number of cores available to the system" (`runtime::Builder`); we set it to 1–2 explicitly.
- **Queues:**
  - between one producer and one consumer (re-sequencer → decision loop → journal writer, decision loop → metrics): `rtrb` 0.4, "a realtime-safe single-producer single-consumer ring buffer". It is wait-free; "a fixed-capacity buffer is allocated on construction"; a full buffer returns an error instead of blocking.
  - where the decoders feed the re-sequencer (several producers): `crossbeam-channel` 0.5 bounded channels, multi-producer multi-consumer, "an alternative to std::sync::mpsc with more features and better performance". Their docs make no lock-free claim, so they are not used in the decision loop's path.
- **A full queue is a fault, never silent loss.**
  - A full queue into the decision loop counts and alarms; the stream reconnects from the last complete slot, as the recorder does.
  - A full journal queue stops the decision loop (fail closed). The watchdog sees the missing heartbeat (§14).
- **No allocations in the hot path:**
  - pubkeys are interned once into `u32` ids (`[u8; 32]` in one table), with no `String` in the loop;
  - per-pool and per-(pool, wallet) state lives in preallocated slabs;
  - event buffers are reused;
  - the farmer sums are a sliding window, as in Python since the change of 2026-10-17.

  A test counts allocations per event (a counting global allocator in the test build) and expects 0 in steady state.
- **The budget is a hypothesis to measure (§16.6), not a promise.** At ~90 tx/s on average and ×10 peaks (§10), one event has ~1 ms at the peak. The 20 µs budget leaves a margin of ~50×.

### 16.2 The VPS: which core does what

**Facts (measured 2026-10-17):**
- 8 vCPUs, `AMD EPYC 9645` (Zen 5c), KVM, 1 thread per core, 1 NUMA node (`lscpu`).
- The vCPU exposes AVX-512 (F, BW, CD, DQ, VL, VNNI, BF16, VBMI, VBMI2, BITALG, IFMA, VPOPCNTDQ): every feature of rustc's `znver5`, and so of `znver4` (`/proc/cpuinfo` against `rustc --print cfg -C target-cpu=…`).
- User units get only the `cpu`, `memory` and `pids` controllers (`user@1000.service/cgroup.subtree_control`). Two consequences:
  - `AllowedCPUs=` (the `cpuset` controller, systemd.resource-control(5)) does **not** apply to our units.
  - `CPUAffinity=` does apply: it is `sched_setaffinity(2)` on the unit's own processes (systemd.exec(5)). `CPUWeight=` and `CPUQuota=` also apply, through the cpu controller.
- An unprivileged process can only lower its own priority: `ulimit -e` and `ulimit -r` are 0. A higher priority (`Nice=` below 0, `CPUSchedulingPolicy=fifo|rr`) needs `CAP_SYS_NICE` (setpriority(2), sched_setscheduler(2)), and so a system-level change by the owner.
- In a VM, pinning to a vCPU is not pinning to a physical core: the hypervisor still schedules the vCPU. Pinning still gives fewer migrations inside the guest and a warm cache on the vCPU's current core. Watch `steal` (`vmstat`, `%st`), and journal it with the HdrHistograms.

**Proposal for when `lab-live` runs** (the owner wants the live services first):

| unit | CPUAffinity | CPUWeight | CPUQuota | Nice | MemoryMax | now |
|---|---|---|---|---|---|---|
| `lab-live` (hot thread pinned to 7) | 6-7 | 1000 | 200 % | 0 | 2G | — |
| `lab-recorder` | 4 | 1000 | 100 % | 0 | 2G | 2G, 100 %, Nice 5 |
| `lab-paper` (Python, the reference) | 5 | 300 | 100 % | 5 | 4G | 4G, 100 %, Nice 5 |
| Claude sessions, notebooks, UI, retention, NAS jobs started from the VPS | 0-3 | 100 (default) or lower | as now | 10 | as now | — |

- **Weights instead of priorities.** `CPUWeight=` is the relative share under contention: "a higher weight means more CPU time", and the kernel default is 100 (systemd.resource-control(5)). It works without privileges, so it is the way to give the live units precedence inside the user slice.
- **`CPUQuota=`** is a ceiling ("200 %" is two CPUs). It protects the rest of the machine if a live unit runs away; it does not reserve anything.
- **The Claude sessions run in scopes** (`worktrees/start-*.sh`), and `CPUAffinity=` is an exec setting that scopes do not take. Their start scripts therefore get `taskset -c 0-3`. Other projects' services (such as `kalshi-dashboard`) are not ours to change (AGENTS.md): their affinity stays as is.
- **The user slice as a whole** competes with the system slices by its own weight. Raising it is an owner decision (a system-level drop-in), and is not needed for now.
- **Measure it:** the per-step histograms (§10) with and without this layout, on the paper run. Keep the layout only if the decision loop's p99 improves.

### 16.3 The build profile

```toml
[profile.release]            # lab-live, lab-recorder, lab-watchdog
lto = "fat"
codegen-units = 1
panic = "abort"              # a panic ends the process: systemd restarts it, the HWM replays (paper/restart.py)
overflow-checks = true       # as now (lab/live/Cargo.toml)
```

- **LTO and codegen-units:** "codegen-units = 1" and "fat" LTO trade compile time for speed and size, "not always" faster (The Rust Performance Book, *Build configuration*). The NAS builds are not time-critical, so we take them, and confirm with criterion (§16.6).
- **`panic = "abort"`** "can slightly reduce binary size, slightly increase runtime speed" (*Build configuration*), and it fits fail-closed. The open SQLite transaction rolls back and the HWM covers the rest.
  - **Not for `lab/core`:** PyO3 turns a Rust panic into a Python exception and needs unwinding, so `lab/core-py` keeps `panic = "unwind"` in its own profile.
- **Allocator:** "Replacing the system allocator can greatly improve runtime speed and reduce memory usage, but results depend on the program" (*Build configuration*).
  - Candidates: `tikv-jemallocator` (heap statistics for §10's profiling) and `mimalloc`.
  - The choice follows a replay benchmark on the NAS. The steady state allocates nothing anyway (§16.1); the allocator matters for decoding and start-up.
- **target-cpu: the NAS (Zen 4, Ryzen 7 8845HS) builds, the VPS (Zen 5c) runs.** rustc 1.97.1 offers `x86-64-v3` (AVX2), `x86-64-v4` (AVX-512 F/BW/CD/DQ/VL), `znver4` and `znver5`.
  - **The default is `x86-64-v3`.** It runs on both machines, and on any host netcup might move the VM to.
  - **`znver4` only if criterion shows ≥ 5 % on the hot functions.** It runs on both: the VPS exposes every znver4 feature (§16.2), and Zen 4 is the NAS itself.
  - **Never `native`:** it depends on the build machine (on the NAS it is `znver4`), so the binary is not reproducible. **Never `znver5`:** the NAS tests would hit illegal instructions.
  - The rustc book on `-C target-feature`: "Using this flag is unsafe and might result in undefined runtime behavior". So every binary checks at start-up, with `is_x86_feature_detected!`, each feature it was compiled for (`cfg!(target_feature = …)`). It refuses to start with a clear message instead of failing later with SIGILL.
  - The NAS build job keeps its GLIBC check (`objdump -T`, max GLIBC_2.34) and adds the target-cpu to the build log.
- **AMD's own guides** are the reference for any micro-architectural tuning (cache sizes, AVX-512 throughput): *Software Optimization Guide for the AMD Zen4 Microarchitecture* (pub. 57647) and *… Zen5 Microarchitecture* (pub. 58455, rev. 1.00, August 2024). Nothing in this design depends on them yet; they are where to look when a histogram points at a loop the compiler should vectorise.

### 16.4 Backtest and research (M4): `lab/core` with rayon

- **Parallel over independent units:**
  - tokens, or pools, of one variant: the tournament evaluates per trade, without a portfolio limit;
  - variants of one grid.
- **Not parallel:** busy and the risk limits of a paper group are sequential by nature, and stay in one thread.
- **Thread count:** `rayon` 1.12 `ThreadPoolBuilder::num_threads` explicitly, defaulting to the NAS's 16 logical CPUs minus 2, settable with `LAB_CORE_THREADS`. The default "may change in future versions" (rayon docs), so we do not rely on it.
- **The GIL:** `Python::detach` (PyO3 0.30) "temporarily releases the GIL, thus allowing other Python threads to run". Every call from Python into a long Rust computation goes through it.
- **Determinism:** results must not depend on the thread count.
  - Every reduction collects per key and sorts before it sums.
  - Money stays in integer lamports.
  - A test runs 1 and N threads and compares bytes.
- **Measure per step:** wall time with 1, 4, 8 and 14 threads per step (fills, exits, the tournament), as a NAS job. The speed-up table goes into the M4 report. Steps that do not scale stay single-threaded.

### 16.5 The Python side

- **The lesson of 2026-10-17 holds there too:** the way of computing first, then cores.
- Python's paper engine is single-threaded by design (determinism, the GIL). Its one core is vCPU 5 in §16.2.
- Parallel Python is not planned. The Rust live route is the place for parallelism around a deterministic core.

### 16.6 Measure, do not guess

- **criterion** 0.8, "a statistics-driven micro-benchmarking library", on the hot functions, as a NAS job:
  - decoding one transaction;
  - the re-sequencer;
  - one decision-loop step;
  - the farmer window update;
  - encoding one journal row.

  `--save-baseline` per merged commit, so a regression shows up as a number.
- **The HdrHistogram stamps per step (§10)** in the paper run. The daily report shows p50, p99 and p999 per step, plus the steal time.
- **perf + `cargo flamegraph`** on a replay of a recorded day (§10's profiling plan).
- **Optimize only where the measurements point.** Each optimization PR carries its before/after numbers, as the Python `flag()` PR does.

**Sources** (checked 2026-10-17):
- Tokio 1.53, crate docs, "CPU-bound tasks and blocking code", and `runtime::Builder`: https://docs.rs/tokio/latest/tokio/
- rtrb 0.4: https://docs.rs/rtrb/latest/rtrb/
- crossbeam-channel 0.5.17: https://docs.rs/crossbeam-channel/latest/crossbeam_channel/
- rayon 1.12, `ThreadPoolBuilder`: https://docs.rs/rayon/latest/rayon/struct.ThreadPoolBuilder.html
- core_affinity 0.8, `set_for_current`, for pinning the decision loop: https://docs.rs/core_affinity/latest/core_affinity/
- PyO3 0.30.1, `Python::detach`: https://docs.rs/pyo3/latest/pyo3/marker/struct.Python.html
- criterion 0.8.2: https://docs.rs/criterion/latest/criterion/
- The Rust Performance Book, *Build configuration*: https://nnethercote.github.io/perf-book/build-configuration.html
- The rustc book, *Codegen options* (`target-cpu`, `target-feature`): https://doc.rust-lang.org/rustc/codegen-options/index.html
- systemd 255 man pages on the VPS:
  - systemd.exec(5): `CPUAffinity=`, `Nice=`, `CPUSchedulingPolicy=`;
  - systemd.resource-control(5): `CPUWeight=`, `CPUQuota=`, `AllowedCPUs=`;
  - online at https://www.freedesktop.org/software/systemd/man/latest/ (it refused automated fetching, so the installed pages were read);
  - with sched_setaffinity(2), setpriority(2) and sched_setscheduler(2).
- AMD, *Software Optimization Guide for the AMD Zen4 Microarchitecture* (pub. 57647) and *… Zen5 Microarchitecture* (pub. 58455, rev. 1.00): both are listed with their publication numbers in the kernel's collection of AMD reference documents, https://bugzilla.kernel.org/show_bug.cgi?id=206537.

## The owner's answers (2026-10-16, through the coordinator)

1. **M0 before M1:** yes. M0 is in progress (#311).
2. **Grafana:** yes, a separate folder "Solana" with a read-only data source. Metrics on localhost (Prometheus and JSON); the Tailscale link comes later (§13).
3. **The alert channel:** decided later. The alert point is a generic webhook URL in the config, empty by default (§13).
4. **b1 or b2:** still open. The owner wants "as much Triton as possible" and leans to b2. b2's filter, volume, own budget and the comparison are in §4. **The decision falls before M3.**
