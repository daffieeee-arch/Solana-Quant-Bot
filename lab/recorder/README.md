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
| `tx_new_reversed` (off by default, `--new-reversed`) | `account_include` [PumpSwap], `account_exclude` 15 generic accounts (protocol and buyback fee recipients, Jupiter v6, a few routers; `watch.rs` `NEW_REVERSED_EXCLUDE`) plus up to 64 learned fee recipients (below) | direct pool creations, mostly reversed pools (97% of the reversed pools ≥ 150 SOL on a day are created that day). The certain fallback: ~7–8 GB/day with ~100% of direct creations (creation-filter measurement, 1052). The exclusion list also lets through deposits, withdrawals and other non-trade PumpSwap transactions |
| `pool_creations` (off by default, `--pool-creations`) | owner PumpSwap + memcmp Pool discriminator + memcmp 16 zero bytes at 271 (`watch.rs` `POOL_WRITE_PROBE`), data slice 43..244 | **a measurement, not a creation detector.** The measuring hour of 2026-10-20 refuted the "fresh fees" hypothesis: the bytes are zero in every pool today, so the stream sees every pool write, old pools too (~1.3 GB/day). It starts no probe and follows no vault, and the feeds learn no pool from it (creation-filters spec v0.2 P3, M1). Every update is written (`pool_creations/`), not deduplicated |
| `tx_pump_creates` (off by default, `--pump-creates`) | `account_required` = [pump.fun, its mint authority `TSLvdd1p…` (PDA "mint-authority")] | every pump.fun create transaction: the CreateEvent (name, symbol, uri, **creator**) and the Metaplex/Token-2022 metadata it creates (`token_meta/`), for the UI's token names. LiveFeed takes the creator of every token created after its store snapshot from it: without it F7's creator rule falls back to the pool's coin creator, which is a sharing config when the creator moved the creator fee (pfee `migrate_bonding_curve_creator`, #233 job 84). ~0.1 GB/day; a downscale drops it last before the watchlist pools |
| `tx_probes` | `account_include` = the recorder's own probe pools | a new WSOL pool becomes a probe (full transactions for `--probe-minutes`, 180) at once when it is created with ≥ `--probe-depth-sol` (120) WSOL, or when its followed WSOL vault reaches that depth. 120, not F7's 150: the trigger sell is the first trade that sees 150, so a switch at 150 comes a minute late (LAB research, watchlist measurement: the F7 watchlist enters at 120 too). Probes are applied within a second (no rate limit): the first F7 trigger comes 0–2 minutes after creation (LAB research smoke test) |
| `new_vaults` | the WSOL vaults of new reversed pools below the probe depth, for `--fresh-vault-minutes` (60), data slice 64..72 | finds the ~85-SOL pools that become deep later. Their trades before the probe starts are not recorded (the farmer flag of those pools is incomplete) |
| `backfill` | per new probe a short second subscription: `account_include` [pool], `from_slot` = its create slot, until the replay passes the probe start (+150 slots) | the pool's transactions before the probe, for LiveFeed's farmer history (without them the flag flips for 14.4% of organic triggers: LAB research's trigger_gap). Rows go to `backfill/` of the segment; `--no-backfill` turns it off. Fallback (signatures from `new_vaults` + `getTransaction`) only if Triton's from_slot window proves too short |
| `vaults` (optional) | `account` = the watchlist's `vaults`, data slice 64..72 (token `amount`) | cheap balance-only following of pools that are not on the full-transaction list |
| `pool_accounts` (off by default, `--pool-accounts`) | owner PumpSwap + memcmp Pool discriminator, data slice 43..244 | new pools (mints, vaults, coin creator). Expensive: see below |

**Pump creates in LiveFeed: kept for 3 days.**
- LiveFeed keeps a token learned from `tx_pump_creates` for `TOKEN_KEEP_DAYS` (3, `paper/live.py`). A token that has not graduated by then is dropped from LiveFeed.
- If it graduates later anyway, its pool's F7 creator rule falls back to the pool's `coin_creator`. That is a sharing config when the creator moved the fee.
- The shadow compare names such a case `creator_unknown` (#233), so it is counted, not silent.
- This is rare: F7's pools are almost all created the day of the create.

**Learned exclusions (`src/exclude.rs`, creation-filters spec v0.2 P1).** With `--new-reversed`, every decoded
PumpSwap trade's `protocol_fee_recipient` and `buyback_fee_recipient` (`ix_accounts`) score as candidates; the up to 64
best go into `tx_new_reversed`'s `account_exclude` after the static list. The measuring hour of 2026-10-20: 12 protocol
and 310 buyback recipients named all 52,787 of its trades on the stream, the 64 most frequent 99.0 %. A candidate is
never learned when it is (a) a guard account (programs, PumpSwap's global_config and event_authority), (b) a key of a
creation transaction the recorder received (static and loaded; a member named by one leaves at once) or (c) in
`--creation-accounts` (default `<root>/creation_accounts.txt`: every account of the reversed creations of epochs
1053-1054, written by `creation_accounts.py`). Without a valid file nothing is learned (fail-closed, the static list
only); `--no-learned-excludes` turns learning off. Scores halve every 6 h; a member is replaced only by a candidate
scoring twice its own, and leaves after 7 days without a trade. Changes apply once per `--min-update-secs` with a
`budget` note; the set is kept in `<root>/new_reversed_exclude.json` across restarts, and each segment's
`_segment.json` has `learned_excludes` (enabled, learned, candidates, creation keys, and `creation_accounts`: the
file's address count and sha256, also in the start note).

Rule (b) sees only what arrives: a learned member in a *direct* create_pool transaction makes the server leave that
transaction out of `tx_new_reversed`, so (b) cannot fire for it. It catches creations on the other streams
(`tx_new_pools`, probes and watchlist, backfills). For direct creations the guard is (a) and (c), the measuring hour's
fixture, and the deploy's checks: `paper.reconcile` against OF1 and the `pool_creations` cross-check of the second
measuring hour. Before that hour, `check_creation_accounts.py` verifies the file (sha256, count) and counts which of the
hour's recipients it holds: those are never learned.

**F7 needs creation detection.** 99% of the F7 triggers come in reversed pools created the same day,
minutes after their creation (LAB research, watchlist measurement). Without `--new-reversed` no new
reversed pool is found, `new_vaults` stays empty and F7 coverage is ~0%; the recorder logs a warning
then (and the dry run lists it). Probes and vault follows start only from a decoded `CreatePoolEvent`
(`tx_new_reversed`, `tx_new_pools` or a backfill); `--pool-creations` starts nothing (creation-filters
spec v0.2 P3).

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

**`new_reversed_noise`** in `_segment.json` (creation-filters spec v0.2 P2 step A, a diagnostic only): the
`tx_new_reversed` transactions of the segment that decode no event, no anomaly and no `tx_ctx` row (74 % of the measuring hour's), their
number and the 32 most frequent account keys of each kind with counts, static keys and loaded (lookup-table) addresses
apart, each key once per transaction. Public addresses; no extra call. Step B's exclusions follow from it by the spec's
fixed rules (at least 5 % of the noise transactions, in no creation, no guard account), after review.

**Alpenglow** (epoch 1064, 2026-10-21).
- **`block_footers/`:** every main request asks for Alpenglow block footers, without their certificates. Each footer becomes a `block_footers/` row: `slot`, `bank_id`, `block_producer_time_ns` (the producer's block time in nanoseconds), `received_unix_ns` and `created_unix_ns`. A footer is not gated by its slot's close, because the validator sends it when it reports it, which can be after the slot (and the next one) confirmed. Like the `vaults/` and `pool_accounts/` rows, it goes into the open segment. Join on (`slot`, `bank_id`); after a reconnect a footer can come twice.
- **The switch:** the first footer a root ever receives marks it. It is logged, written to `<root>/alpenglow.json` and kept as an `anomalies/` row of its slot (program `recorder`, kind `alpenglow_first_footer`), once per root.
- **The slot jump:** the jump in slot numbers at the switch needs nothing special. A resume inside it starts at the next slot that exists.
- **Before the switch:** there are no footers, so the table is absent.

**Live `tx_ctx/` is partial until MEV phase 1** (#206). Since the extractor writes `tx_ctx` (MEV
phase 0, a measurement on OF1 history), the recorder writes it too, through the same `rows_for`.
`convert.rs` does not fill the pre/post token balances and sets every status to Ok, and failed
transactions are not subscribed. So live rows have no `signer_token_deltas` (always null) and
never `failed = true`. Fee payer, costs, `programs`, `venues`, `n_dex` and `signer_sol_delta` are
complete. Filling the token balances and status from the Yellowstone proto is part of phase 1
(paper MEV live).

A segment closes after `--segment-slots` (150) slots or `--segment-secs` (60), once no backfill
is running (it waits at most `--backfill-wait-secs`, 20). A filter change does not close it: its
`watchlist.json` lists the pools and wallets that every filter inside the segment had. Only whole confirmed slots are written; rows of a slot that is already written are
counted as late and dropped (`late_rows` counts rows: a late transaction's arrival row counts
too). A skipped slot sends no block meta and no confirmed status, so the live `blocks/` table has no
`skipped` rows. `paper.reconcile` compares the event tables per slot range, so a missing slot
number is not a gap there.

**A segment that cannot be written** (a table, the sync or `_segment.json` fails, e.g. on a full
disk) loses no row (a `budget.json` that cannot be written ends the recorder the same way, see
"Budget"):
- Its rows stay in memory.
- The recorder exits with code 74, and systemd restarts it.
- The restart removes the unfinished directory and resumes after the last complete segment on disk.
- The server then replays the slots that were only in memory, through `from_slot`. Where its replay window is shorter, they are recorded as `lost_slots`.

A reconnect in the same process would resume after those in-memory slots and lose them.

**Resume after a disconnect.**
- The stream resumes with `from_slot` = last closed slot + 1. The closed slots of the open segment are still in memory and are written with it.
- Before resuming, the recorder asks the server's replay window (`SubscribeReplayInfo`). When it
  starts later (`first_available`), it resumes from there, and the slots in between are recorded
  in `reconnects` (`lost_slots`).
- Only sessions whose resume subscription was accepted but brought no update count as failed
  resumes. After three of them the recorder continues live. Connect, DNS, TLS or subscribe errors
  never drop `from_slot`: once the server is reachable again it can replay the outage.
- After 20 sessions in a row without data the recorder gives up (exit 1).

**One recorder per root.** The recorder holds an exclusive lock on `<root>/lock` (not in a dry run).
A second recorder on the same root stops with a message: two would share the budget and collide on
segment numbers.

## Budget

Every update is metered (protobuf size before transport compression, times `--wire-ratio`) per
stream and minute. The daily cap is `--budget-usd` / `--usd-per-gb` (default $2 / $0.08 = 25 GB,
UTC day, kept in `<root>/budget.json` across restarts). When the day projects above the cap, the
lowest-priority stream is dropped (`pool_accounts`, `pool_creations`, `new_vaults`, `backfill`, `tx_probes`,
`tx_new_reversed`, `vaults`, `tx_wallets`, `tx_pump_creates`, then the watchlist pools; open-position pools,
graduations, blocks and slots stay). Probes go before the creation detection; dropping `tx_new_reversed` adds
the note `creation detection off (downscale)` (creation-filters spec v0.2 M4). A minute above 20x the sustainable
rate downscales at once. At the cap the recorder writes its last segment and exits with code 75;
it refuses to start again the same day. A new UTC day restores all streams. Every decision is a
`budget` usage row with a note, which the engine journals (`usage` table).

**Unknown spend (fail-closed).** At the start, today's spend is the larger of `budget.json` and
the usage rows of today's segments. The recorder cannot know it when, for today (by file time):
- `budget.json` is unreadable;
- a `_segment.json` is unreadable;
- a segment directory has no `_segment.json` (an interrupted write) and today's `budget.json` has
  no reservation (none at all, or one of a recorder from before 2026-10-18).

When the spend is unknown, the recorder stops for the day: it logs the reasons and exits with
code 75, like a spent budget.

**Received bytes never leave the spend** (Codex 3, LAB review, 2026-10-18). `budget.json` holds a
reservation, `reserved_bytes`: the spend the run may reach before its next save. A byte is only
counted below it.
- Before its first byte the recorder saves one: the spend plus one sustainable minute (cap / 1440,
  at least 1 MiB). It does so before the unfinished segment directories are removed, so an owner's
  override is on disk before the evidence for it goes. Once the spend comes within half of it, the
  next one is saved. (A single update larger than half a reservation is covered only once its save
  succeeds; the recorder's updates are a few KB.)
- A save that fails ends the recorder with code 74, like a segment write error. A reconnect in the
  same process would stream on unmetered: past the cap, reconnecting on every tick.
- The stop at the cap never waits for a save, also on a minute's roll. The reservation on disk is past
  the cap already, so a restart stops again. A new reservation that cannot be saved while bytes come in
  ends the run with code 74, also at the cap; the restart then stops at once with 75, without the open
  segment's last slots.
- A configuration error at the start (no endpoint) reserves nothing, and a later failed start settles
  its reservation again, so failed starts do not use up the day.
- A restart counts the larger of `spent_bytes` and `reserved_bytes`. After a crash, a kill or a failed
  write that is the whole reservation: at most one sustainable minute more than came, and a `budget`
  row notes it. A clean end (shutdown, the stop, an exit on a write error) settles the reservation to
  the spend.
- So today's `budget.json` with a reservation covers a segment directory without `_segment.json`. Before,
  `budget.json` was saved at the minute's roll only: a failed segment's minutes and the running minute lay
  past it, and after a kill, or a failed segment write and a failed save, their bytes left the spend.
- At a shutdown the last segment is written before `budget.json`, so a failed save loses no closed slots.

After a check, the owner restarts it once with `--budget-assume-spent YYYY-MM-DD=BYTES`:
- it applies to that UTC day only;
- the spend never goes below what the readable segments hold;
- a `budget` row notes it.

A first start (no `budget.json`, no segments), and a lost `budget.json` beside complete segments,
start as before.

`--no-downscale` (measuring runs, e.g. the first measuring hour): no projected or spike downscale,
every stream stays on; the cap is only the hard stop. Without it a small `--budget-usd` (0.5 is a
cap of 6.25 GB/day) would drop `pool_creations` and `tx_new_reversed` within minutes, because an
hour's rate is projected over the rest of the day.

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
cargo test                                     # conversion, segments, meter, filters, the core loop, lab-probe
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

## lab-probe: slot timing and the venue bytes census

A second binary in this crate (`src/bin/lab-probe.rs`) for short, capped measurements beside the recorder. It records
only counts and receive times: no segments, no decoding, and no `budget.json` (it never reads or writes the
recorder's meter state; a test pins that). Its bytes are counted by the meter's own rule (`src/wire.rs`:
`encoded_len` × `--wire-ratio`, rounded up), and `--max-bytes` is in those metered bytes, the unit of the daily
budget. The endpoint and token come from `recorder.env` as for the recorder (`LAB_TRITON_X_TOKEN`, never printed;
the endpoint is shown as `<endpoint>` in its errors). An `--out` named `budget.json`, or in a directory that holds one
(a recorder root), is refused.

```bash
set -a; . ~/.config/solana-lab/recorder.env; set +a
# Mode A (MR-AG1): every slot status with the inter-slot ones; per slot the receive time of each status
lab-probe --slots --out slots.json --minutes 60 --max-bytes 200000000 --dry-minutes 5
# Mode B (venue bytes census): one transaction filter per program, vote=false, failed unset, confirmed
lab-probe --tx-program pump=6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P --tx-program pump_amm=pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA \
          --out census.json --minutes 30 --max-bytes 2000000000 --dry-minutes 2 --wire-ratio 1
lab-probe --slots --print-request    # the subscription, without connecting
```

- **Mode A output:** `slots` (per slot `parent`, `<status>_ns` for first_shred_received, created_bank, completed,
  processed, confirmed, finalized and dead, `updates`, `dead_error`) and `summary`:
  - `ms_per_slot` from consecutive slot numbers (by first shred and by processed);
  - `processed_to_confirmed` and `confirmed_to_finalized`, n / median / p90 in ms (linear interpolation);
  - `dead_slots` and `gaps` (slot numbers between the first and the last that never had an update, and their runs).
- **Mode B output:** `filters` (per name `program`, `matched`, `messages`, `raw_bytes`, `metered_bytes`). A
  transaction that k of the filters matched (its `filters` field) counts once in `total` and 1/k to each of them, so
  `filters` plus `other` (pings) add up to `total`; `matched_by_several` counts those transactions.
- **Stops and exit codes:** 0 at `--minutes`; 76 when the metered bytes reach `--max-bytes`; 77 when, after
  `--dry-minutes`, the full run projects above `--max-bytes` (`projection` in the output); 78 when the run ends early
  (the stream breaks, no update for 60 s, a signal). The output JSON is written atomically at the start, every minute
  and at the end, so a kill keeps the data up to the last minute.

## Not used: yellowstone-block-machine

`subscribe_block` (0.10.2) forces every entry, all sysvar accounts and inter-slot updates at
processed commitment, and drops the request sink (no filter updates without reconnecting).
Measured on OF1 epoch 1043 that is ~1,216 entries per block, ~392 M entries per day: tens of GB
per day before any of our data. At `confirmed` commitment Yellowstone sends a slot's updates
before its confirmed status, which is all the recorder needs; `paper.reconcile` proves
completeness against OF1.
