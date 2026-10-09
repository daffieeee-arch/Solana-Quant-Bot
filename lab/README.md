# lab/ — lean, results-first Solana work (since 2026-10-09)

See [`../AGENTS.md`](../AGENTS.md) for the rules. Weekly checkpoints go to [`reports/`](reports/).

| Week | Deliverable | Status |
|------|-------------|--------|
| 1 | pump.fun + PumpSwap event dataset from Old Faithful (Parquet), reserve-continuity check | in progress |
| 2 | Backtest tournament (5–8 strategies, sizes 0.5–25 SOL, own price impact, fees, 1–2 slot delay, one-time hold-out) | planned |
| 3+ | Paper trading of the top 1–3 via Triton Yellowstone gRPC; stop rule after 4 weeks / 200 trades | planned |

## Layout

- `extractor/` — Rust binary `lab-extractor`. A [Jetstreamer 0.7.0](https://github.com/anza-xyz/jetstreamer) plugin that streams a slot range from Old Faithful (`files.old-faithful.net`) and writes every pump.fun and PumpSwap event to Parquet.
  - `idl/` — the official IDLs, copied unchanged from [`pump-fun/pump-public-docs`](https://github.com/pump-fun/pump-public-docs/tree/2293f9a66c654e9fe82dc5e8f4618538f24bb35f/idl). Events are decoded generically from these files; no layout is written by hand. Pump only appends event fields, so older events decode as a prefix (`decode_status = prefix`, missing tail fields are null).
- `scripts/env.sh` — pinned toolchain (Rust 1.97.1) and build environment for the VPS.
- `scripts/backfill.sh` — runs the extractor chunk by chunk inside a CPU/memory-limited systemd user scope; resumable.
- `checks/continuity.py` — correctness check: per mint (bonding curve) and per pool, the reserves after trade *n* must equal the reserves before trade *n+1*.

## Dataset

Written outside Git to `$LAB_DATA_ROOT/events/v1/chunks/<start>-<end>/` (default `LAB_DATA_ROOT=/home/chupa/Solana-project/data-old-faithful-one/lab`):

- `pump/<Event>/part-*.parquet` and `pump_amm/<Event>/part-*.parquet` — one table per IDL event. Each row has context columns (`slot`, `tx_index`, `signature`, `outer_ix`, `inner_ix`, `stack_height`, `event_seq`, `outer_program`, `parent_program`, `parent_ix_disc`, `fee_payer`, `tx_fee`, `cu_consumed`, `cu_price_micro`, `cu_limit`, `jito_tip`, `decode_status`, `payload_len`, and from epoch 1043 on `parent_ix_name`, `parent_ix_args` (the emitting instruction's IDL-decoded arguments as JSON, e.g. `max_sol_cost` / `min_tokens_out` slippage limits), `parent_ix_data` (hex, only when the IDL decode was not exact) and `signers` (only when there is more than one signer; otherwise the fee payer is the signer)) followed by the IDL fields. Pubkeys are base58 strings; 128-bit integers and nested values are strings/JSON.
- `failed_txs/` — failed transactions that touched pump or PumpSwap (fees, compute budget, error; from epoch 1043 on also signers and the first pump/PumpSwap instruction with decoded arguments). No signature column: `(slot, tx_index)` identifies the transaction.
- `blocks/` — slot, parent slot, block time, block height, skipped markers.
- `anomalies/` — events with an unknown discriminator or a payload that does not fit the IDL (raw hex kept).
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
