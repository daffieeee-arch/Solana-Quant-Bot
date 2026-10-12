# Paper engine

Trades F7 on paper from an event feed, with an N2 shadow, and journals everything. Spec:
`data-old-faithful-one/lab/research/w3-paper-engine-spec-2026-10-11.md`. No wallet, no keys,
no transactions: paper only.

- `feed.py`: `Feed` interface; `ReplayFeed` reads a store (never a range that touches the
  hold-out).
- `live.py`: `LiveFeed` over the recorder's segments (`lab/recorder`), the replay's own legs;
  backfills of new probes are history (farmer flags, the pool's farming window), never events.
- `watchlist_live.py`: the live watchlist (see below).
- `shadow.py`: shadow live on an archived epoch: the recorder simulated from OF1 chunks, the
  engine on its segments against its replay, every difference with a cause.
- `reconcile.py`: recorder segments against Old Faithful once an epoch is archived.
- `keeper.py`: before `lab/ops/live-retention.sh` deletes a segment, keeps the trades of tokens with a
  signal (recorded and backfill rows), their arrival times and 1-minute candles of every pool and
  curve under `<journal>/../kept/<seq>/` (`DONE.json` last). A segment is deleted only once kept.
- `engine.py`: slot-synchronous engine. It re-executes later trades against the state with our
  position using the backtest's own `PositionSim`, so paper fills equal the replay's.
  - N2 shadow: per F7 trigger, `n2_k` (default 1) random tokens of the same venue and pool
    orientation that traded earlier this hour at a similar depth. They are entered beside F7 and
    are not limited by the risk file (except the kill switch).
  - Heartbeat: one journal row per minute of event time. A jump of more than 25 slots between
    events is also journaled as a feed gap.
  - Each UTC day is exported, reported and copied to `lab/backup/<journal>/<day>/`.
- `journal.py`: SQLite (WAL) plus Parquet per UTC day. Writes are idempotent per event or
  signal key, so a restart gives the same journal.
- `risk.yaml` and `risk.py`: owner-only limits, re-read every minute of event time and again at
  every entry (a kill switch or an invalid file between a signal and its entry opens nothing: the
  order is journaled as skipped, "at entry: ..."). An invalid file means nothing is opened, and the
  reason is journaled.
- `report.py`: daily report. Contents:
  - per variant: trades, net SOL per trade, win and fill rate, and the difference to N2;
  - the stop-rule counter;
  - the 10 SOL capital simulation;
  - heartbeat and feed gaps.
- `parity.py`: engine against backtest on one dev chunk (signals, farm label, fills, exits).

The paper candidate is a config file (`k3-paper-2026-10-12.yaml`, the owner's ADJUST decision:
K3, "niet bewezen", as a measurement): segments, farm label, variants, d, q, N2 and the risk file.
`--config` loads it and journals its sha256 and the risk file's; it is frozen once the stop-rule
clock runs. The engine labels every F7 pool trigger like the backtest (flagged when farmers made
more than half of the pool's SOL volume in the hour before t); `--farm organic` skips flagged ones.
Risk: one position per pool, at most 5 open, -1 SOL per day, and `pool_cooldown_h` (24 h without
entries in a pool after a total loss there). Open positions carry `mark_proceeds` / `mark_slot` /
`mark_pnl` (a sale of the whole position now, after costs); `<journal>/wallet.json` is the UI's
wallet format marked `"mode": "paper"` (10 SOL plus realized P&L, paper buys and sells).

Measured latency (live only; reporting, no effect on fills or parity): LiveFeed gives every Event
its transaction's arrival from the segment (`arrivals/`: earliest `received_unix_ns` and the
server's `created_unix_ns`; `_segment.json`: `written_unix`, when the segment was closed and the
engine can first see it). Every F7 and N2 signal row journals, for its trigger transaction:
`block_time` of t, `received_unix_ns`, `created_unix_ns`, `latency_ms` (receive minus block time),
`segment_written_unix`, `segment_lag_ms` (segment close minus block time) and `d_ms` (the simulated
d in ms). The daily report adds, per day and trigger (variants and N2 share one): median and p90 of
both lags and how many were later than d. block_time and written_unix have a 1 s resolution, so
one row is +-1 s; the medians over many triggers are the measure. Replay runs leave them NULL.

Long live runs keep memory bounded: every 100 segments LiveFeed drops farmer history, curve legs
of mints quiet for 24 h and backfill dedup keys older than the farmer window; arrivals are read
per segment and kept only on its Events. Every hour the engine drops the recent legs and last
state of tokens without an event for an hour and nothing open, pending or waiting.

Live input checks:
- `--live` needs a readable `--store` (the snapshot of tokens and pools); pools neither in it nor
  created in a received transaction are unknown: their trades are dropped, counted
  (`unknown_pools`, `unknown_pool_rows`) and logged per segment with new ones.
- Farmer history: LiveFeed seeds it from the store, per pool when it first trades live, over
  `[first live slot - 24 h, first live slot)` as far as the store reaches. The journal's state
  `farmer_seed` records the range and `complete_from`: before that slot the farm labels and the live
  watchlist's organic verdict lack history (a store ending before the recorder's start: one day).
- A row that comes twice (a probe's backfill delivered after its first recorded rows, or slots
  recorded again after a restart) counts once: row keys of the last 3,000 slots are remembered.
- A backfill trade from before the first slot the engine saw goes to the watchlist history in a
  minute estimated with the clock, never into the open minute.

## Live watchlist

LAB research's #180 state machine (`wallets.watchlist`: F7-reversed in at >= 120 SOL real depth
and organic, out below 100; M1 for 6 h), fed by the engine with the pool trades it sees and the
recorder's backfills of new probes, as per-(pool, UTC minute) rows like the batch measurement.
Every minute the list for the next minute goes to `<recorder root>/watchlist-current.json` (the
recorder's input) and one line to `watchlist-log.jsonl`. `engine --live` runs it unless
`--no-watchlist`. On complete data (the dev replay) the lists equal the batch measurement's per
minute, except that the batch already lists a new M1 pool for its own creation minute (it knows
every pool up front); live it is on from the next minute (`tests/test_watchlist_live.py`).

**Limitation: only received pools.** The engine sees what the recorder received: list pools,
open positions, probes (with their backfill from creation), graduation and creation transactions,
and other pools that appear in those transactions. A pool that is not received cannot enter by
its own trades. F7 pools come in as probes (99% of F7 triggers are in pools created the same day);
M1 pools from their graduation. A pool that left the list is only seen again as a probe, an open
position or inside another received transaction. `paper.shadow` measures what this costs: the
live list against the batch list per minute.

The engine restarts by replaying deterministically into the same journal.

Known differences to the backtest, by design:

- N2 draws its match only from tokens that traded earlier in the trigger's hour. The backtest
  (`n2_matches`) draws from the whole hour, but live trading cannot look ahead.
- Risk limits (`risk.yaml`) apply to F7. `--ignore-risk` turns them off for replay parity runs
  only: the engine refuses it with `--config` or `--live`, so a paper run always has its limits.
- The heartbeat runs on event time.

```
python -m paper.engine --from 450142000 --to 450362000 --journal <dir> [--ignore-risk] [--variants 0.04:600]
python -m paper.engine --live <recorder root> --config paper/k3-paper-2026-10-12.yaml --journal <dir> [--catch-up]
python -m paper.parity --from 450144000 --to 450360000 --out <dir>
python -m paper.shadow --store <fwd1052.duckdb> --out <dir>
```
