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
| `tx_new_pools` | `account_required` = [PumpSwap, pump.fun withdraw authority `39azUYFW…`]; on unless `--no-new-pools` | every pump.fun graduation (new canonical pool, M1). Measured on 1052 (`lab/checks/creation_filter.py`, two windows of 100k slots): 100% of migrations, 0 other transactions, ~0.003 GB/day. Direct `create_pool` calls (mostly reversed pools) have no cheap rule (8–56 GB/day) |
| `blocks` | block meta | block time, height, tx count per slot (the engine's clock) |
| `slots` | slot status, `filter_by_commitment` | a slot is written once confirmed (+ `--close-lag`) |
| `tx_new_reversed` (off by default, `--new-reversed`) | `account_include` [PumpSwap], `account_exclude` 15 generic accounts (protocol and buyback fee recipients, Jupiter v6, a few routers; `watch.rs` `NEW_REVERSED_EXCLUDE`) | direct pool creations, mostly reversed pools (97% of the reversed pools ≥ 150 SOL on a day are created that day). The certain fallback: ~7–8 GB/day with ~100% of direct creations (creation-filter measurement, 1052). The exclusion list also lets through deposits, withdrawals and other non-trade PumpSwap transactions |
| `pool_creations` (off by default, `--pool-creations`) | owner PumpSwap + memcmp Pool discriminator + memcmp 16 zero bytes at 271 (`protocol_fees`, `creator_fees`), data slice 43..244 | **hypothesis, to verify in the first measuring hour:** both counters are 0 at creation and grow with the first trade, so each new pool gives exactly one update for almost no bytes. Measure updates per hour and whether every new reversed pool appears (compare with OF1's CreatePoolEvents). Every update is written (`pool_creations/`), not deduplicated |
| `tx_probes` | `account_include` = the recorder's own probe pools | a new WSOL pool becomes a probe (full transactions for `--probe-minutes`, 180) at once when it is created with ≥ `--probe-depth-sol` (120) WSOL, or when its followed WSOL vault reaches that depth. 120, not F7's 150: the trigger sell is the first trade that sees 150, so a switch at 150 comes a minute late (LAB research, watchlist measurement: the F7 watchlist enters at 120 too). Probes are applied within a second (no rate limit): the first F7 trigger comes 0–2 minutes after creation (LAB research smoke test) |
| `new_vaults` | the WSOL vaults of new reversed pools below the probe depth, for `--fresh-vault-minutes` (60), data slice 64..72 | finds the ~85-SOL pools that become deep later. Their trades before the probe starts are not recorded (the farmer flag of those pools is incomplete) |
| `backfill` | per new probe a short second subscription: `account_include` [pool], `from_slot` = its create slot, until the replay passes the probe start (+150 slots) | the pool's transactions before the probe, for LiveFeed's farmer history (without them the flag flips for 14.4% of organic triggers: LAB research's trigger_gap). Rows go to `backfill/` of the segment; `--no-backfill` turns it off. Fallback (signatures from `new_vaults` + `getTransaction`) only if Triton's from_slot window proves too short |
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
filtered during the whole segment: what every filter inside it had), `backfill/` (earlier transactions of new probes)
and `_segment.json`, written last: seq, first/last slot, row counts, usage rows per minute and
stream, the budget state, the filter level, reconnects, late rows and totals.

A segment closes after `--segment-slots` (150) slots or `--segment-secs` (60), once no backfill
is running (it waits at most `--backfill-wait-secs`, 20). A filter change does not close it: its
`watchlist.json` lists the pools and wallets that every filter inside the segment had. Only whole confirmed slots are written; rows of a slot that is already written are
counted as late and dropped. After a disconnect the stream resumes with `from_slot` = last written
slot + 1; after three failed resumes it continues live and the gap shows in `reconnects` and in
the reconciliation.

## Budget

Every update is metered (protobuf size before transport compression, times `--wire-ratio`) per
stream and minute. The daily cap is `--budget-usd` / `--usd-per-gb` (default $2 / $0.08 = 25 GB,
UTC day, kept in `<root>/budget.json` across restarts). When the day projects above the cap, the
lowest-priority stream is dropped (`pool_accounts`, `pool_creations`, `tx_new_reversed`,
`new_vaults`, `backfill`, `tx_probes`, `vaults`, `tx_wallets`, then the watchlist pools; open-position pools,
graduations, blocks and slots stay); a minute above 20x the sustainable
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

Probe costs, measured in the store (one day of epoch 1046): 57% of all trades of same-day reversed
pools fall in their first 10 minutes. Probing every new reversed pool for 10 minutes would be
~13.5 GB/day; probing those created with ≥ 150 SOL for 3 hours is ~5 GB/day and covers 51% of the
day's deep pools from their first slot. Most others start at ~85 SOL; `new_vaults` catches the
ones that grow.

## Not used: yellowstone-block-machine

`subscribe_block` (0.10.2) forces every entry, all sysvar accounts and inter-slot updates at
processed commitment, and drops the request sink (no filter updates without reconnecting).
Measured on OF1 epoch 1043 that is ~1,216 entries per block, ~392 M entries per day: tens of GB
per day before any of our data. At `confirmed` commitment Yellowstone sends a slot's updates
before its confirmed status, which is all the recorder needs; `paper.reconcile` proves
completeness against OF1.
