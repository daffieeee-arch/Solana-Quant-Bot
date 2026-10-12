# Live units (systemd --user): recorder, paper engine, token names, retention

Unit files for the live side, kept in the repo. **Nothing here is installed or started by the
repo.** Installing is a step for the measuring hour (`data-old-faithful-one/lab/research/meetuur-draaiboek-2026-10-12.md`)
and the paper start, each **only after the owner's OK**. Paper only: no wallet, no keys, no
transactions.

| unit | what | limits |
|---|---|---|
| `lab-recorder.service` | `lab-recorder --root <lab>/recorder $RECORDER_ARGS` (Triton Dragon's Mouth to segments) | MemoryMax 2G, CPU 100%, Nice 5. `Restart=on-failure`; exit 75 (the day's budget is spent) is never restarted. It does not start while `locks/disk-low` exists |
| `lab-recorder.timer` | starts the recorder at 00:05 UTC, after a budget stop the day before | |
| `lab-paper.service` | `python -m paper.engine --live <lab>/recorder --config paper/k3-paper-2026-10-12.yaml` (journal `<lab>/journal/paper-k3/journal`); its live watchlist writes the recorder's `watchlist-current.json` | MemoryMax 4G, CPU 100%, Nice 5. `Requires=` the recorder: an explicit stop of the recorder stops it, a budget exit does not (it waits for new segments) |
| `lab-token-meta-rpc.service` | `python -m backtest.token_meta_rpc --enable --follow`: names of new reversed-pool tokens. **Paid Triton RPC** (~$0.02 a day) | MemoryMax 1G, Nice 10, daily call cap 5,000. Starts only when `~/.config/solana-lab/token-meta-rpc.approved` exists (the owner's OK) |
| `lab-live-retention.service` / `.timer` | hourly: `live-retention.sh` deletes complete recorder segments older than 3 days (owner decision 2026-10-10). Journals, reports, budget, watchlists, token_meta and logs stay | the disk guard's `io-stall` skips a round; `disk-low` keeps 1 day |

Paths are the VPS's (`%h` = `/home/chupa`):
- data: `~/Solana-project/data-old-faithful-one/lab`;
- recorder binary: `<lab>/bin/lab-recorder`, a symlink to the built version;
- run checkout: `~/Solana-project/worktrees/paper-run`, a worktree at the frozen commit;
- logs: `<lab>/logs/{recorder,paper-engine,token-meta-rpc,live-retention}.log`.

Settings live outside the repo in `~/.config/solana-lab/`:

| file | contents | mode |
|---|---|---|
| `live.env` | `RECORDER_ARGS`, `PAPER_STORE` (`live.env.example`) | 644 |
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
   sha256sum ~/Solana-project/worktrees/paper-run/lab/paper/k3-paper-2026-10-12.yaml   # af5c42a7...
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
   cp ~/Solana-project/worktrees/paper-run/lab/ops/systemd/lab-*.{service,timer} ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemd-analyze --user verify ~/.config/systemd/user/lab-*.service    # no errors
   ```
5. **Start** (the measuring hour: the recorder only, or recorder plus engine):
   ```bash
   systemctl --user start lab-recorder.service
   systemctl --user start lab-paper.service                             # paper start / chain test
   systemctl --user enable --now lab-live-retention.timer
   systemctl --user enable --now lab-recorder.timer                     # paper: restart after a budget stop
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
- `systemctl --user stop lab-paper lab-recorder` (the paper engine first);
- `systemctl --user disable --now lab-recorder.timer lab-live-retention.timer`;
- for the RPC route also `lab-token-meta-rpc` and `rm ~/.config/solana-lab/token-meta-rpc.approved`.

**Restarts.** The paper engine restarts by replaying the retained segments into the same journal (no
state snapshots yet). After a long outage the replay starts at the oldest retained segment (3
days), so journal rows older than that are not recomputed. The disk guard's emergency level stops
`run-*.scope` lab jobs, not these units: retention keeps their disk use bounded (about 3 days of
segments).

`tests/test_live_retention.py` tests the retention script on a temporary root; `systemd-analyze
--user verify lab/ops/systemd/*` checks the unit files. Before installing, it only reports the
missing binary and run checkout.
