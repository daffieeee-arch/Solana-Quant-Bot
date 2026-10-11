# lab/recorder — the live side of the paper engine

`lab-recorder` subscribes to Triton's Dragon's Mouth (Yellowstone gRPC) and writes the extractor's
own event rows as rolling segments. `paper.live.LiveFeed` reads those segments and gives the paper
engine the same events `ReplayFeed` gives from the dev store. `paper.reconcile` compares them with
Old Faithful once the epoch is archived.

PAPER ONLY: the recorder only reads chain data. It has no keys, does not sign and does not send
transactions.

## What it subscribes to (one connection, commitment `confirmed`, zstd)

| stream | filter | why |
|---|---|---|
| `tx_pools` | transactions with `account_include` = open-position pools (`open-pools.json`, from the engine) + watchlist pools; `vote=false`, `failed=false` | the trades we simulate against, full rows |
| `tx_wallets` | the same for the watchlist's wallets | followed wallets (whale work) |
| `blocks` | block meta | block time, height, tx count per slot (the engine's clock) |
| `slots` | slot status, `filter_by_commitment` | a slot is written once confirmed (+ `--close-lag`) |
| `vaults` (optional) | `account` = the watchlist's `vaults`, data slice 64..72 (token `amount`) | cheap balance-only following of pools that are not on the full-transaction list |
| `pool_accounts` (off by default, `--pool-accounts`) | owner PumpSwap + memcmp Pool discriminator, data slice 43..244 | new pools (mints, vaults, coin creator). Expensive: see below |

A transaction filter is never sent with an empty address list (that would match every
transaction on chain); the tests check this. The vault stream is a second subscription on the
same connection because a data slice applies to a whole subscription.

Transactions are converted to the firehose's `TransactionData` (`src/convert.rs`) and decoded by
the extractor's `LabPlugin::rows_for`, compiled in from `../extractor/src` (`#[path]` modules),
so a live row equals the OF1 row of the same event column for column.

## Output

`<root>/segments/<seq:09d>/` in the OF1 chunk layout (`pump/<Event>/`, `pump_amm/<Event>/`,
`failed_txs/`, `anomalies/`, `blocks/`), plus `arrivals/` (receive time and the server's
`created_at` per transaction), `vaults/`, `pool_accounts/`, `watchlist.json` (the pools and wallets
filtered during the whole segment; after a change, the intersection of the old and new filter)
and `_segment.json`, written last: seq, first/last slot, row counts, usage rows per minute and
stream, the budget state, the filter level, reconnects, late rows and totals.

A segment closes after `--segment-slots` (150) slots or `--segment-secs` (60), and at every
filter change. Only whole confirmed slots are written; rows of a slot that is already written are
counted as late and dropped. After a disconnect the stream resumes with `from_slot` = last written
slot + 1; after three failed resumes it continues live and the gap shows in `reconnects` and in
the reconciliation.

## Budget

Every update is metered (protobuf size before transport compression, times `--wire-ratio`) per
stream and minute. The daily cap is `--budget-usd` / `--usd-per-gb` (default $2 / $0.08 = 25 GB,
UTC day, kept in `<root>/budget.json` across restarts). When the day projects above the cap, the
lowest-priority stream is dropped (`pool_accounts`, then `vaults`, then `tx_wallets`, then the
watchlist pools; open-position pools, blocks and slots stay); a minute above 20x the sustainable
rate downscales at once. At the cap the recorder writes its last segment and exits with code 75;
it refuses to start again the same day. A new UTC day restores all streams. Every decision is a
`budget` usage row with a note, which the engine journals (`usage` table).

## Inputs

- `--watchlist` (default `<root>/watchlist-current.json`): the watchlist builder's snapshot,
  format v1 (`pools: [{pool, mint, rules}]`, `wallets`, `valid_for_minute`, `valid_from_slot`,
  `counts`), optionally `vaults`. Re-read when it changes; applied at most every
  `--min-update-secs` (60).
- `--open-pools` (default `<root>/open-pools.json`): written by the engine (`LiveFeed.keep_pools`).
- Endpoint: `--endpoint` or `LAB_TRITON_GRPC_ENDPOINT`. Token: only `LAB_TRITON_X_TOKEN` (never
  logged; do not put it in the URL). `--max-tx-accounts` (10,000) caps the transaction filter.

## Build, test, dry run (NAS)

```bash
source lab/scripts/env.sh                      # pinned Rust; the same env as the extractor
cd lab/recorder
cargo test                                     # conversion, segments, meter, filters, the core loop
cargo build --release
# The subscription a watchlist gives, without connecting:
target/release/lab-recorder --root /tmp/rec-dry --watchlist <watchlist-current.json> --dry-run
```

`Cargo.lock` started as a copy of the extractor's and only adds the Yellowstone crates
(`yellowstone-grpc-client =14.0.1`, `yellowstone-grpc-proto =13.0.0`, `tonic-health`, vendored
`protoc`); every Agave/Jetstreamer version is the extractor's.

## Not used: yellowstone-block-machine

`subscribe_block` (0.10.2) forces every entry, all sysvar accounts and inter-slot updates at
processed commitment, and drops the request sink (no filter updates without reconnecting).
Measured on OF1 epoch 1043 that is ~1,216 entries per block, ~392 M entries per day: tens of GB
per day before any of our data. At `confirmed` commitment Yellowstone sends a slot's updates
before its confirmed status, which is all the recorder needs; `paper.reconcile` proves
completeness against OF1.
