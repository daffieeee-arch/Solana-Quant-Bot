# Live units (systemd --user): recorder, paper engine, token names, retention

Unit files for the live side, kept in the repo. **Nothing here is installed or started by the
repo.** Installing is a step for the measuring hour (`data-old-faithful-one/lab/research/meetuur-draaiboek-2026-10-12.md`)
and the paper start, each **only after the owner's OK**. Paper only: no wallet, no keys, no
transactions.

The paper start of about 2026-10-21 (store, settings, order, checks, stop and rollback) is
written out as exact commands in [`runbook-paper-start-2026-10-21.md`](runbook-paper-start-2026-10-21.md).

| unit | what | limits |
|---|---|---|
| `lab-recorder.service` | `lab-recorder --root <lab>/recorder $RECORDER_ARGS` (Triton Dragon's Mouth to segments) | MemoryMax 2G, CPU 100%, Nice 5. `Restart=on-failure`; exit 75 (the day's budget is spent, or today's spend is unknown) is never restarted; exit 74 (a segment could not be written) is, and resumes after the last segment on disk. It does not start while `locks/disk-low` exists |
| `lab-recorder.timer` | starts the recorder at 00:05 UTC, after a budget stop the day before | |
| `lab-paper.service` | `python -m paper.engine --live <lab>/recorder --config paper/k3-paper-2026-10-12.yaml` (journal `<lab>/journal/paper-k3/journal`); its live watchlist writes the recorder's `watchlist-current.json` | MemoryMax 4G, CPU 100%, Nice 5. `Requires=` the recorder: an explicit stop of the recorder stops it, a budget exit does not (it waits for new segments) |
| `lab-paper.timer` | every 15 min: starts the paper engine if it is not running. After five failures within an hour systemd's start limit leaves the service stopped; the timer brings it back once the hour has passed. Starting a running engine does nothing | it cannot start the engine while `locks/disk-low` exists: the recorder it requires does not start then |
| `lab-token-meta-rpc.service` | `python -m backtest.token_meta_rpc --enable --follow`: names of new reversed-pool tokens. **Paid Triton RPC** (~$0.02 a day) | MemoryMax 1G, Nice 10, daily call cap 5,000. Starts only when `~/.config/solana-lab/token-meta-rpc.approved` exists (the owner's OK) |
| `lab-live-retention.service` / `.timer` | hourly: `live-retention.sh` deletes complete recorder segments older than 3 days (owner decision 2026-10-10). Before that `paper.keeper` keeps what the retention rule promises: the trades of tokens with a signal, their arrival times, 1-minute candles of every pool and curve, and every segment's `blocks/` and Alpenglow `block_footers/` whole (the slot clock and the producer latency before and after the switch; ~14 MB/day at 200 ms slots), under `<lab>/journal/paper-k3/kept/<seq>/`. A segment is deleted only once its `DONE.json` exists. Journals, reports, budget, watchlists, token_meta and logs stay | the disk guard's `io-stall` skips a round; `disk-low` keeps 1 day. A failing keeper leaves the segments in place (logged) |

Paths are the VPS's (`%h` = `/home/chupa`):
- data: `~/Solana-project/data-old-faithful-one/lab`;
- recorder binary: `<lab>/bin/lab-recorder`, a symlink to the built version;
- run checkout: `~/Solana-project/worktrees/paper-run`, a worktree at the frozen commit;
- logs: `<lab>/logs/{recorder,paper-engine,token-meta-rpc,live-retention}.log`.

Settings live outside the repo in `~/.config/solana-lab/`:

| file | contents | mode |
|---|---|---|
| `live.env` | `RECORDER_ARGS`, `PAPER_STORE` (`live.env.example`). **`PAPER_STORE` is required**: the engine refuses `--live` without a readable store. Use the newest store: pools created between its end and the recorder's start are unknown, their trades are dropped (counted and logged as `unknown pools`), and the farmer history is complete only from the **first live slot + 1 day** when the store ends before the recorder's start (as fwd1053-1054 does for the paper start), else from the later of the first live slot and the store's start + 1 day (journal state `farmer_seed`, `complete_from`; paper/live.py `_init_seed`) | 644 |
| `recorder.env` | `LAB_TRITON_GRPC_ENDPOINT`, `LAB_TRITON_X_TOKEN` (`recorder.env.example`, names only) | **600** |
| `rpc.env` | `LAB_TRITON_RPC_URL`, which holds the token (`rpc.env.example`) | **600** |
| `token-meta-rpc.approved` | exists only after the owner approved paid RPC calls; one line with date and words | 644 |

Values come from the owner and never go into the repo, a log, a prompt or a command line.

## Installeren en starten

Only after the owner's OK for this step. Run by the coordinator.

1. **Run checkout** at the commit to run (the frozen K3 config included):
   ```bash
   git -C ~/Solana-project/Solana-bot worktree add --detach ~/Solana-project/worktrees/paper-run <commit>
   git -C ~/Solana-project/worktrees/paper-run rev-parse HEAD     # note it
   sha256sum ~/Solana-project/worktrees/paper-run/lab/paper/k3-paper-2026-10-12.yaml   # note it: the engine journals it (config_sha256)
   ```
2. **Recorder binary**: the NAS build (bookworm, glibc ≤ 2.39, `cargo test` green, sha256 noted):
   ```bash
   install -m 755 lab-recorder-<commit> ~/Solana-project/data-old-faithful-one/lab/bin/
   ln -sfn lab-recorder-<commit> ~/Solana-project/data-old-faithful-one/lab/bin/lab-recorder
   ```
3. **Settings**:
   ```bash
   mkdir -p ~/.config/solana-lab
   cp ~/Solana-project/worktrees/paper-run/lab/ops/live.env.example ~/.config/solana-lab/live.env   # edit RECORDER_ARGS
   install -m 600 /dev/null ~/.config/solana-lab/recorder.env    # the owner fills in endpoint and token
   ```
   For the measuring hour, set the measuring-hour line of `live.env.example` (`--max-runtime-secs
   3600 --budget-usd 0.5 --no-downscale --pump-creates`). The recorder then stops by itself after the
   hour with exit 0, and is not restarted.
4. **Units**:
   ```bash
   cd ~/Solana-project/worktrees/paper-run/lab/ops/systemd    # the live units only: lab-notebooks.service runs already
   cp lab-recorder.service lab-recorder.timer lab-paper.service lab-paper.timer \
      lab-live-retention.service lab-live-retention.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemd-analyze --user verify ~/.config/systemd/user/lab-{recorder,paper,live-retention}.service    # no errors
   ```
5. **Start** (the measuring hour: the recorder only, or recorder plus engine):
   ```bash
   systemctl --user start lab-recorder.service
   systemctl --user start lab-paper.service                             # paper start / chain test
   systemctl --user enable --now lab-live-retention.timer
   systemctl --user enable --now lab-recorder.timer                     # paper: restart after a budget stop
   systemctl --user enable --now lab-paper.timer                        # paper: restart after a start-limit stop
   systemctl --user enable lab-recorder.service lab-paper.service       # paper: start at boot
   ```
   The token names through RPC run only after the owner's separate OK:
   ```bash
   install -m 600 /dev/null ~/.config/solana-lab/rpc.env               # the owner fills in the URL
   echo "approved by the owner on <date>: <words>" > ~/.config/solana-lab/token-meta-rpc.approved
   systemctl --user start lab-token-meta-rpc.service
   ```

**Check:**
- `systemctl --user status lab-recorder lab-paper`;
- `tail <lab>/logs/recorder.log`;
- `<lab>/recorder/budget.json` (`spent_gb`);
- new segments under `<lab>/recorder/segments/`;
- `watchlist-current.json` refreshed every minute;
- the journal's heartbeat.

**Stop:**
- first the timers, or they start the units again: `systemctl --user disable --now lab-paper.timer lab-recorder.timer lab-live-retention.timer`;
- then `systemctl --user stop lab-paper lab-recorder` (the paper engine first);
- for the RPC route also `lab-token-meta-rpc` and `rm ~/.config/solana-lab/token-meta-rpc.approved`.

**Disk guard.** At its emergency level (< 20 GB free) `~/Solana-project/ops/disk-guard.sh` (unit `solana-disk-guard.timer`, outside this repo) also stops
`lab-paper.service`, `lab-token-meta-rpc.service` and `lab-recorder.service`, in that order, and
writes `<lab>/locks/disk-stopped` first. The three units have `ConditionPathExists=!` on that file:
while it exists no timer (or reboot) starts them again, even after `disk-low` is gone, so they do not
flap around the threshold (coordinator decision 2026-10-13). Only the coordinator removes the file,
after checking the disk and the journal; then `systemctl --user start lab-recorder lab-paper`. A
skipped condition is not a failure (it does not count toward the start limit). At `disk-low` the
recorder does not start, and retention keeps 1 day.

**Timer ticks and the recorder.** `lab-paper.service` `Requires=` the recorder, so every
`lab-paper.timer` tick that starts the engine also starts the recorder. After a budget stop the
recorder exits 75 at once (the day's budget is spent): harmless, but visible in the logs every
15 minutes until 00:05 UTC.

### A restart: the high-water mark

After a restart the engine replays the retained segments (3 days) into the same journal, up to the
journal's high-water mark (HWM, the last minute it committed) with the journal's decisions and without
writing (`paper/README.md`, "Restarts"; `paper/restart.py`). So the rows up to the HWM stay the first
run's, the open positions at the HWM are rebuilt over the same events, and busy, cooldowns, daily loss
and wallets are taken from the HWM's state. Pools, graduations and the slot clock learned before the
retained segments come from the journal table `learned`.
- What stays approximate, and journaled: after an outage of ~3 days, a position whose entry lies before
  the retained segments is closed at the HWM at its last mark (`restart_lost`); after more than ~2 days,
  the signals after the HWM in the restart's first 24 h have incomplete farmer history (`farm_complete`
  0, group H skipped). The daily report's "## Restarts" counts every difference.
- While the engine replays or catches up (more than a minute behind the wall clock) it writes neither
  `watchlist-current.json` nor `open-pools.json`: the recorder keeps the lists it has (the last run's;
  on a first start none, then only its base filters and probes).

`tests/test_live_retention.py` tests the retention script on a temporary root; `systemd-analyze
--user verify lab/ops/systemd/*` checks the unit files. Before installing, it only reports the
missing binary and run checkout.
