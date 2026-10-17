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
  Candle prices are quote / base in raw units as recorded: in a reversed pool (base WSOL) that is
  tokens per SOL, so a chart of SOL per token inverts it once (and only once).
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
- `restart.py`: live restarts with a high-water mark: up to it the journal leads (see "Restarts" below).
- `risk.yaml` and `risk.py`: owner-only limits, re-read every minute of event time and again at
  every entry (a kill switch or an invalid file between a signal and its entry opens nothing: the
  order is journaled as skipped, "at entry: ..."). An invalid file means nothing is opened, and the
  reason is journaled.
- `report.py`: daily report. Contents:
  - per variant: trades, net SOL per trade, win and fill rate, and the difference to N2;
  - the stop-rule counter;
  - the 10 SOL capital simulation;
  - heartbeat and feed gaps.
- `stoprule.py`: which exits the stop rule counts (primary group, filled trades, from the clock start), the
  clock's state and the verdict thresholds. Stdlib only: the report and the web app's Paper tab
  (`lab/ui/paper.py`, whose server has no numpy) call the same functions, so they cannot count differently.
- `parity.py`: engine against backtest on one dev chunk (signals, farm label, fills, exits).

The paper candidate is a config file (`k3-paper-2026-10-12.yaml`, the owner's ADJUST decision:
K3, "niet bewezen", as a measurement): segments, farm label, variants, the delay, q, N2 and the risk file.
**The delay is `d_ms: 600` (owner decision 2026-10-14).** At each decision, the clock turns it into slots (`Clock.slots`: rounded, at least 1) by the measured slot time of that epoch.
- **History (266–316 ms):** 2 slots, the pre-registered d = 2.
- **From epoch 1053 (218 ms) and after Alpenglow (epoch 1064):** 3 slots.
- **Why 600, not 550:** the edges are 240 ms and 400 ms. The first choice, 550 ms, sat on the rounding edge at 220 ms, so at the 218 ms of epoch 1053 on, d flipped between 2 and 3.
- **Without a config:** runs keep `--d` in slots, or take `--d-ms` (> 0), never both.
- **With a config:** it holds exactly one of `d` and `d_ms`.
`--config` loads it and journals its sha256 and the risk file's; it is frozen once the stop-rule
clock runs. The engine labels every F7 pool trigger like the backtest (flagged when farmers made
more than half of the pool's SOL volume in the hour before t); `--farm organic` skips flagged ones.
Risk: one position per pool, at most 5 open, -1 SOL per day, and `pool_cooldown_h` (24 h without
entries in a pool after a total loss there). Open positions carry `mark_proceeds` / `mark_slot` /
`mark_pnl` (a sale of the whole position now, after costs); `<journal>/wallet.json` is the UI's
wallet format marked `"mode": "paper"` (10 SOL plus realized P&L, paper buys and sells).

**Variant groups** (`research/week4-spec-paper-h4h5-2026-10-16.md`, sha256 abc0f29f): the config is the primary
group (K3, its file and sha256 unchanged); `--groups paper/h-arms-2026-10-16.yaml` (live.env `PAPER_ARGS`) adds
groups of their own, and the journal keeps `groups_sha256` beside `config_sha256`. Group **H** trades the arms of
H4 and H5, `flow300_H6` and `flow300_tp100_H6`, on the organic F7 triggers in pool_reversed (d_ms 600, q 0.5,
no N2), a measurement and no test.
- **Its own budget:** `risk-h.yaml` (0.5 SOL, at most 5 open, 2.5 SOL exposure, -1 SOL per day, a 24 h pool
  cooldown), its own daily loss and its own paper wallet `<journal>/wallet-h.json`. K3's limits, wallet and stop
  rule are its own: K3's journal rows are the same with and without the group (`tests/test_arms_replay.py`).
- **Busy per arm on organic triggers only:** a flagged trigger opens and blocks nothing in the group (the backtest
  and the clean tests simulate both labels together; the forward replay counts the difference).
- **Full history only** (`require_farm_complete`): the group opens only when the farm label had it, i.e.
  `farm_complete` (LiveFeed's farmer seed) and `farm_window_complete` (the pool's hour [t - 1 h, t) covered: seen
  for an hour, followed since its creation, or a replay of every pool). Signals journal `pool_seen_slot` and
  `farm_window_complete`.
- **Exits:** the backtest's own rules (`strategies.f7_exit_rule`: FlowTime, FlowTpTime), the flow from a
  `LiveFlowIndex` per open position fed with the pool's legs (the backtest's FlowIndex, only the legs since the
  entry). Exits journal `peak_mark` / `peak_slot` and the windows `n_flow_slots` / `cap_slots`.
- **Report:** the daily report's stop-rule counter and capital simulation count K3 only; group H gets a section
  per arm ("measurement, no test"): trades, net per trade (1 %-trimmed and mean), the day's and the run's net SOL,
  win and fill rate, drains, exit reasons, take-profit proceeds/cost (p5 / median / p95), median hold, and from 5
  days with a position the day series' mean with a day-bootstrap interval (H4's form).
- **Parity:** `python -m paper.parity --arm flow300_H6 --from A --to B --out DIR` compares an arm with the backtest
  (busy over organic triggers only: equal on every organic trigger; the tournament's busy: equal in pools without
  a flagged trigger). `paper.shadow --groups <groups file>` runs the groups beside its variants.

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
  `complete_from` is the first live slot + 1 day when the store ends before the recorder's start, else the later
  of the first live slot and the store's start + 1 day (as lab/ops/README.md says).
  `farmer_seed` keeps the **first** start's; every start's seed is listed once, in order, in `farmer_seeds`
  (LAB review B6).
  Every start replays the retained segments. After the first 3 days the oldest of them is ~3 days old, and the
  farmer history before it is gone. A restart seeds at the start of its replay window (36 h before the journal's
  high-water mark, see "Restarts" below): the signals of its first day, [first live slot, `complete_from`), have
  incomplete history. Up to the HWM the restart writes nothing and takes the journal's decisions, so the first run's
  rows stay. Only signals after the HWM and before `complete_from` are journaled with incomplete history
  (`farm_complete` 0, group H `skipped`, "farm history incomplete"): after an outage of more than ~1.5 days. The daily
  report lists every start ("Farmer seed per start") and, for a restart, whether that happened.
- A row that comes twice (a probe's backfill delivered after its first recorded rows, or slots
  recorded again after a restart) counts once: row keys (pool or mint, slot, tx, outer, inner) of
  the last 3,000 slots are remembered in `seen_keys` (LiveFeed), with their source.
- A backfill trade from before the first slot the engine saw goes to the watchlist history in a
  minute estimated with the clock, never into the open minute.
- A probe's backfill can arrive after its first recorded trades, when the recorder follows the pool from mid-life.
  Those trades' farmer flags were computed without the pool's earlier trades: in shadow job 91 this made pool 4reQ…
  `organic` instead of `flagged` (`farm_label_order`). When the backfill comes, LiveFeed computes the flags of the
  pool's recorded trades of the last 3,000 slots again (`_reflag`). The engine's farming window takes the changed
  ones (`_farm_fix`), so the farm label after it equals the backtest's. The legs already handed out keep their
  flag: a position's flow window and the live watchlist's past minutes.

## Live watchlist

LAB research's #180 state machine (`wallets.watchlist`: F7-reversed in at >= 120 SOL real depth
and organic, out below 100; M1 for 6 h), fed by the engine with the pool trades it sees and the
recorder's backfills of new probes, as per-(pool, UTC minute) rows like the batch measurement.
Every minute the list for the next minute goes to `<recorder root>/watchlist-current.json` (the
recorder's input) and one line to `watchlist-log.jsonl`. `engine --live` runs it unless
`--no-watchlist`. While the engine is more than a minute behind the wall clock (replaying the retained segments at a
start, or catching up), the list is not written, and neither is `open-pools.json`: the recorder keeps the lists it
has (the last run's, or none on a first start: then only its base filters and probes; `recorder/src/watch.rs`,
test `missing_or_old_input_files_keep_the_base_filters_without_a_reload_loop`). The log gets one line per minute,
also across restarts. On complete data (the dev replay) the lists equal the batch measurement's per
minute, except that the batch already lists a new M1 pool for its own creation minute (it knows
every pool up front); live it is on from the next minute (`tests/test_watchlist_live.py`).

**Limitation: only received pools.** The engine sees what the recorder received: list pools,
open positions, probes (with their backfill from creation), graduation and creation transactions,
and other pools that appear in those transactions. A pool that is not received cannot enter by
its own trades. F7 pools come in as probes (99% of F7 triggers are in pools created the same day);
M1 pools from their graduation. A pool that left the list is only seen again as a probe, an open
position or inside another received transaction. `paper.shadow` measures what this costs: the
live list against the batch list per minute.

## Restarts (live): the high-water mark

`paper/restart.py`. With `--live`, every minute's commit journals its high-water mark (HWM): state `hwm` (the slot
and minute of the commit) and `hwm_state` (per group the daily loss, the running pool cooldowns and the paper
wallet's realized P&L and last transactions, and the busy entries that can still block). Nothing is committed
between minutes (`Journal.export_day` does not commit), so the journal holds exactly what the run did up to its HWM.

A start on a journal with a HWM replays the segments of the last 36 h before it (`restart.WINDOW_H`: from the first
segment closed then on, LiveFeed's `from_seq`), not every retained one. That covers the oldest open position (an H
position is held at most 6 h) with the 24 h farmer history behind its legs, and the live watchlist's longest rule
(24 h), with 6 h to spare; this start's farm labels are complete 12 h before the HWM. Measured on the VPS (K3 + H, one
CPU, every event of a dev chunk in one-minute segments: an upper bound for the recorder's filtered stream): 5 s per
segment, so at most ~3 h for the window. Up to the HWM it:
- it writes no rows (heartbeat, usage, signals, orders, fills, exits, positions), no daily exports or reports and no
  wallet files;
- it takes the decisions from the journal: a signal enters as journaled (N2 matches included), an entry fills with
  the journal's size unless the journal has it skipped or failed (a risk file changed since does not matter), and an
  exit closes the position with the journal's values. What the replay computes itself only counts as a difference.
So the positions open at the HWM are the first run's, rebuilt over the same events from their entry on.

At the HWM the journal's state is taken: its open positions, its pending entries, and busy, cooldowns, the daily loss
and the wallets from `hwm_state`. A journal-open position the replay does not hold is closed at the HWM at its last
journaled mark, `exit_reason` `restart_lost` (no sell fill). This happens when its entry lies before the window,
when its entry does not fill again, or when the replay exits before the HWM where the journal does not. A position of a
variant whose group is not configured now (runbook §5, "Group H off") is closed so too, without touching any current
group's daily loss, wallet or cooldowns. At the HWM the open positions' pools go to `open-pools.json` at once (they are
current); after that the file waits until the engine has caught up. The counts go to state `restarts` and the daily report ("## Restarts"). There, the last 6 h before
the HWM are also compared: what the replay would have decided itself against the journal. This only happens when
that window starts after this start's farmer history is complete.

LiveFeed keeps what a restart cannot learn again from the retained segments in the journal table `learned`:
- pools learned live, loaded when they were learned before the window's first segment (the others are learned
  again, in order);
- graduated tokens;
- the first block of each epoch, so the measured slot time, and with it every delay in slots, stays the same.

ReplayFeed runs and parity are unchanged: no HWM, no `learned` rows.

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
