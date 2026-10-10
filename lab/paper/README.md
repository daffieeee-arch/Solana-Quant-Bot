# Paper engine (v0, offline)

Trades F7 on paper from an event feed, with an N2 shadow, and journals everything. Spec:
`data-old-faithful-one/lab/research/w3-paper-engine-spec-2026-10-11.md`.

- `feed.py`: `Feed` interface; `ReplayFeed` reads the dev store (never the hold-out); `LiveFeed`
  is a stub until the recorder exists.
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
- `risk.yaml` and `risk.py`: owner-only limits, re-read every minute of event time. An invalid
  file means nothing is opened, and the reason is journaled.
- `report.py`: daily report. Contents:
  - per variant: trades, net SOL per trade, win and fill rate, and the difference to N2;
  - the stop-rule counter;
  - the 10 SOL capital simulation;
  - heartbeat and feed gaps.
- `parity.py`: engine against backtest on one dev chunk.

v0 restarts by replaying deterministically into the same journal; state snapshots come with the
live feed.

```
python -m paper.engine --from 450142000 --to 450362000 --journal <dir> [--ignore-risk] [--variants 0.04:600]
python -m paper.parity --from 450144000 --to 450360000 --out <dir>
```
