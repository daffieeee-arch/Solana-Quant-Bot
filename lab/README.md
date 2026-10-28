# lab/ — lean, results-first Solana work (since 2026-10-09)

See [`../AGENTS.md`](../AGENTS.md) for the rules. Weekly checkpoints go to [`reports/`](reports/).

| Week | Deliverable | Status |
|------|-------------|--------|
| 1 | pump.fun + PumpSwap event dataset from Old Faithful (Parquet), reserve-continuity checks | Delivered; pinned stores and checks are used by the lab |
| 2 | After-cost backtests and one-time hold-out | Run; see the [research register](reports/). No profitable strategy is established by these milestones |
| 3+ | Fixed K3 paper candidate via Triton Yellowstone gRPC, with separate descriptive shadow arms | Running; formal clock target 26 October, latest 2 November 2026. The journal's `clock_start` is authoritative |

The formal stop rule needs **200 filled trades and five trading days**, with a day-bootstrap interval.
Four weeks are the planned duration, not a minimum; six weeks are the limit. Before a recorded clock start,
results are **warmup measurements, not a formal GO/ADJUST/STOP**. Earlier entries do not enter the clock's sample.
GO still requires a separate owner decision before any live money.

New Python settlements use **cost rule 2** after deployment; old journal rows retain their original booked P&L
and historical rule. The Rust route still uses **rule 1**. Reports show the recorded rules, including mixed
history; these routes must not be described as equivalent under different cost models. A merge does not prove
that a pinned running service has been deployed. Runtime pins and checks are recorded in the deployment evidence.

The approximately **600 ms entry delay is simulated**. Recorder-segment latency alone does not demonstrate
actionable stream latency, transaction landing or executable fills.

Bounded follow-ups: add a small automatic lab CI test set; rerun only research calculations affected by the
review fixes (including E73's fixed calendar and E75's corrected gate); measure actual stream-to-decision latency.
These are follow-ups, not new strategy selection or a reason to change K3 during its measurement period.

## Layout

- `extractor/` — Rust binary `lab-extractor`. A [Jetstreamer 0.7.0](https://github.com/anza-xyz/jetstreamer) plugin that streams a slot range from Old Faithful (`files.old-faithful.net`) and writes every pump.fun and PumpSwap event to Parquet.
  - `idl/` — the official IDLs, copied unchanged from [`pump-fun/pump-public-docs`](https://github.com/pump-fun/pump-public-docs/tree/2293f9a66c654e9fe82dc5e8f4618538f24bb35f/idl). Events are decoded generically from these files; no layout is written by hand. Pump only appends event fields, so older events decode as a prefix (`decode_status = prefix`, missing tail fields are null).
- `recorder/` — Rust binary `lab-recorder`: the live side of the paper engine. Subscribes to Triton's Dragon's Mouth (watchlist transactions, block meta, slot statuses; optional vault balances), decodes with the extractor's own code and writes rolling segments for `paper.live.LiveFeed`, with a byte meter and a daily budget. See [`recorder/README.md`](recorder/README.md).
- `core/` — Rust crate `lab-core`: the per-event math that the backtest and the Rust live route share (M0: the venue fills of `backtest/venues.py`, byte for byte), with proptest. `core-py/` builds it as the Python module `labcore` (PyO3, maturin); the backtest uses it behind `LAB_CORE=rust` (`backtest/venues_core.py`). Built and tested on the NAS (`core/nas_m0.sh`). Design: [`docs/rust-live-route-design-2026-10-16.md`](docs/rust-live-route-design-2026-10-16.md).
- `live/` — Rust binary `lab-live`, the Rust paper route, with replay/follow and shared journal-schema tests. Built and tested on the NAS. Its current settlement model remains rule 1; Python rule-2 deployment is not proof of Rust cost-model parity. Design: `docs/rust-live-route-design-2026-10-16.md`.
- `scripts/env.sh` — pinned toolchain (Rust 1.97.1) and build environment for the VPS.
- `scripts/backfill.sh` — runs the extractor chunk by chunk inside a CPU/memory-limited systemd user scope; resumable.
- `checks/continuity.py` — correctness check: per mint (bonding curve) and per pool, the reserves after trade *n* must equal the reserves before trade *n+1*.

## Dataset

Written outside Git to `$LAB_DATA_ROOT/events/v1/chunks/<start>-<end>/` (default `LAB_DATA_ROOT=/home/chupa/Solana-project/data-old-faithful-one/lab`):

- `pump/<Event>/part-*.parquet` and `pump_amm/<Event>/part-*.parquet` — one table per IDL event. Each row has context columns (`slot`, `tx_index`, `signature`, `outer_ix`, `inner_ix`, `stack_height`, `event_seq`, `outer_program`, `parent_program`, `parent_ix_disc`, `fee_payer`, `tx_fee`, `cu_consumed`, `cu_price_micro`, `cu_limit`, `jito_tip`, `decode_status`, `payload_len`, and from epoch 1043 on `parent_ix_name`, `parent_ix_args` (the emitting instruction's IDL-decoded arguments as JSON, e.g. `max_sol_cost` / `min_tokens_out` slippage limits), `parent_ix_data` (hex, only when the IDL decode was not exact) and `signers` (only when there is more than one signer; otherwise the fee payer is the signer); from the account-columns build on (epoch 1052 on) `ix_accounts` (the emitting instruction's accounts by IDL name as JSON, remaining accounts under `_extra`; off with `LAB_IX_ACCOUNTS_JSON=0`) and `ix_pool`, `ix_user`, `ix_global_config`, `ix_base_mint`, `ix_quote_mint` (null where the instruction variant has no such account; prefixed because events have their own `pool`/`user`/mint fields)) followed by the IDL fields. The manifest lists the build's `event_context` columns. Pubkeys are base58 strings; 128-bit integers and nested values are strings/JSON.
- `failed_txs/` — failed transactions that touched pump or PumpSwap (fees, compute budget, error; from epoch 1043 on also signers and the first pump/PumpSwap instruction with decoded arguments). No signature column: `(slot, tx_index)` identifies the transaction.
- `blocks/` — slot, parent slot, block time, block height, skipped markers.
- `anomalies/` — events with an unknown discriminator or a payload that does not fit the IDL (raw hex kept).
- `token_meta/` (extractor builds from ea0c4a6 on) — token metadata created in the transactions the extractor reads: Metaplex Token Metadata create instructions and Token-2022 metadata `Initialize` (mint, name, symbol, uri, update authority). `backtest/token_meta.py` combines it with pump.fun's `CreateEvent` into one row per mint. **A UI lookup only, never a strategy input**: names, symbols and uris must not feed signals, filters or parameters (a build over all chunks also reads the hold-out epochs 1047–1051).
- `tx_ctx/` (extractor builds from this change on; MEV phase 0, measurement only) — one row per transaction the extractor reads: every successful one with a pump/PumpSwap event and every failed one (`failed` true). Fee payer, fee, compute units and price, priority fee, Jito tip; `programs` = the invoked programs in execution order as `depth:label` (top level 1, inner = stack height, runs as `label*n`; DEXes, Jupiter v6 and infrastructure by name, others by id); `venues` and `n_dex` (the DEXes and routers among them); the fee payer's SOL delta (lamports, fee included) and token deltas per mint (`mint:delta;...`, WSOL included). No signature column: `(slot, tx_index)` joins the event tables and `failed_txs`. `checks/tx_ctx_smoke.py` checks it against a chunk's own tables. `n_dex` counts distinct DEX *programs*, so an arbitrage between two pools of the same DEX (e.g. a PumpSwap normal and reversed pool of one token) has `n_dex` = 1. Pools per transaction come from the event tables (`pool` of the PumpSwap events, `mint` of the curve events); for other DEXes the extractor keeps no pool. **Live (recorder) `tx_ctx` has no token deltas and no failed rows until MEV phase 1** (see `recorder/README.md`).
- `_manifest.json` — written last: status (`complete` only if Jetstreamer finished, the writer succeeded and every slot in the range was reported as a block or a skipped slot), row counts, IDL hashes, timing.

Chain order is `(slot, tx_index, outer_ix, inner_ix)`. Rows inside a file are not sorted. Deduplicate on `(signature, outer_ix, inner_ix)` in case a firehose thread restart replays part of a slot.

## Build and run

```bash
source lab/scripts/env.sh
cd lab/extractor && cargo test && cargo build --release && cd ../..
# one epoch in 2 chunks of 216,000 slots with 6 firehose threads
lab/scripts/backfill.sh $((1050*432000)) $((1051*432000)) 216000 6
"$LAB_PY" lab/checks/continuity.py "$LAB_DATA_ROOT/events/v1/chunks"
```
