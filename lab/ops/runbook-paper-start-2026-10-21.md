# Runbook: paper start (K3 + group H), about 2026-10-21

For the coordinator, step by step, once the owner has given the OK and the Triton credentials.

**Scope:** paper only. No wallet, no keys, no transactions. K3 (E14) runs unchanged, and group H (the arms of H4 and H5, `research/week4-spec-paper-h4h5-2026-10-16.md`) runs beside it. This runbook fills in `README.md` ("Installeren en starten") for this start. Where they differ, this runbook applies to this start.

**Conventions:**
- Run every command on the VPS as `chupa`, in one shell, with these variables set:
  ```bash
  LAB=~/Solana-project/data-old-faithful-one/lab
  PY=~/.local/share/solana-quant/toolchains/lab-py/bin/python
  ```
- `C` is the run commit: main at or after dcff935, which contains #299 (the arms) and #301 (the streaming end() fix).

## 0. Preconditions

| # | check | how |
|---|---|---|
| 0.1 | The owner's OK for the paper start, the Triton credentials, and the day's budget. | owner |
| 0.2 | NAS job 92 is green: arm parity at dcff935, "0 unexplained" in both comparisons for both arms. Expected `unsold_busy`: 2 for flow300_H6, 1 for flow300_tp100_H6. | `parity.md` of job 92 |
| 0.3 | NAS job 91 is green (see below). | `compare.md` and `state.jsonl` of job 91 |
| 0.4 | The recorder binary is the 339c40e build: `--pump-creates` (#287), durable budget (#265), rustls 0.23.46 (#290), Alpenglow block footers. `lab/recorder` and `lab/extractor` have not changed since. | `readlink $LAB/bin/lab-recorder` → `lab-recorder-339c40e-bookworm`; `sha256sum $LAB/bin/lab-recorder-339c40e-bookworm` → `01a7ef4f…` (as noted at the build) |
| 0.5 | At least 50 GB free; the disk guard is active; no other Triton consumer is running; no `locks/disk-low` or `locks/disk-stopped`. | `df -h ~`; `systemctl --user list-timers \| grep disk-guard`; `ls $LAB/locks` |
| 0.6 | Alpenglow: the switch is at epoch 1064, about 2026-10-21 14:50 UTC. The recorder handles it (footers), and so does the Clock (the last measured epoch until 1,000 slots of 1064). Note whether the start falls before or after the switch. | `$LAB/recorder/alpenglow.json` appears at the first footer |

Job 91 (arms on, 2c3e5fe) counts as green when all of these hold:
- unexplained = 0, with `farm_incomplete` an allowed cause;
- RSS plateaus in `state.jsonl`;
- reconcile is clean.

Job 91 ran without the end() fix (#301). That fix changes only the exit of a position that cannot sell at its deadline, not memory or coverage.

## 1. PAPER_STORE

The engine needs a store for `--live`. The store is the snapshot of the tokens and pools known before the start, plus the farmer history. The coordinator's rule (2026-10-16) is to use the newest store whose data has already been used.

**Route A: the clean data is spent before the start.** That means:
- E20 has written `clean.lock`;
- H3, H4 and H5 have each written the `result.json` of a real run with a decision.

Then the NAS, where clean-v1 lives, builds a store over the last two complete clean epochs: 1062–1063, or 1061–1062 if 1063 is not extracted yet. `python -m backtest.store paper` enforces the condition itself:
- It refuses every window in 1055–1063 until the lock and all three results are there. Without them it does not even list the clean root.
- It writes the evidence (the lock's sha256 and the result paths) into the store's meta, under `clean_spent`.

**Route B: otherwise.** Use **`fwd1053-1054.duckdb`**: slots 454,896,000–455,760,000, ending at the start of epoch 1055 (about 2026-10-11).
- It is the store that shadow job 91 measured with.
- It is already built, so there is no build step, only a copy.

**Route A, the NAS job.** The coordinator runs it from a checkout at C. It takes about 5 min: an epoch builds in about 140 s, chunk by chunk, within a 3 GB memory scope.

```bash
cd <checkout at C>/lab && LAB_SPILL_DIR=/lab/tmp/paper-store PYTHONPATH=. $LAB_PY -m backtest.store paper --epochs 2
# "refused: … not spent yet: no clean.lock … / no H5 result …"   -> route B
# "store: …/store/paper-1062-1063.duckdb" and a meta with clean_spent  -> route A
```

**Copy to the VPS** (about 1 min for 4.5 GB at ≤ 85 MiB/s):

```bash
S=fwd1053-1054.duckdb                                      # route B; route A: S=paper-<a>-<b>.duckdb (the job's name)
NAS=/mnt/fastdisk/solana-lab/data-old-faithful-one/lab/store/$S
rsync -a --bwlimit=85000 truenas-lab:$NAS $LAB/store/
ssh truenas-lab sha256sum $NAS
sha256sum $LAB/store/$S                                    # the same as on the NAS
chmod 444 $LAB/store/$S
$PY -c "import duckdb,json; c=duckdb.connect('$LAB/store/$S', read_only=True, config={'memory_limit':'512MB'}); m=json.loads(c.execute('SELECT json FROM meta').fetchone()[0]); print(m['period'], m['slot_start'], m['slot_end_exclusive'], sorted(m.get('clean_spent', {}).get('results', {})))"
                                                           # route B: forward 454896000 455760000 []
                                                           # route A: paper <from> <to> ['h3', 'h4', 'h5']
```

**What a store that ends before the start means** (route A shortens the gap to hours or days; route B leaves about ten days). All of it is known, and all of it is journaled.
- **Unknown pools and tokens.** Pools and tokens created between the store's end and the recorder's start are unknown until their creation arrives live.
  - Pools arrive through `tx_new_reversed` and `pool_creations`; tokens arrive through `--pump-creates`.
  - Their trades are dropped until then, and the engine log notes them as unknown pools.
  - F7's pools are almost all created on the day of the trigger, so F7 hardly suffers.
- **Farm labels.** These become complete only at the first live slot + 24 h (journal state `farmer_seed`, field `complete_from`).
  - Group H (`require_farm_complete`) opens nothing in the first 24 h; its signals are `skipped` with "farm history incomplete".
  - K3 trades from the start and journals `farm_complete = 0`.
  - Per the spec, the first 24 h do not count in any arm count.

## 2. Settings in `~/.config/solana-lab/`

```bash
mkdir -p ~/.config/solana-lab
cat > ~/.config/solana-lab/live.env <<EOF
RECORDER_ARGS="--pool-creations --new-reversed --pump-creates --budget-usd 2"
PAPER_STORE=$LAB/store/$S
PAPER_ARGS="--groups paper/h-arms-2026-10-16.yaml"
EOF
cat ~/.config/solana-lab/live.env                          # PAPER_STORE is the absolute path of §1's store
chmod 644 ~/.config/solana-lab/live.env
install -m 600 /dev/null ~/.config/solana-lab/recorder.env
# the owner fills in (an editor, not the command line): LAB_TRITON_GRPC_ENDPOINT=… and LAB_TRITON_X_TOKEN=…
```

- **`--pump-creates`** (owner's OK, 2026-10-16) gives LiveFeed the creator of every new token, for F7's creator rule.
- **`--budget-usd 2`** is a cap of 25 GB per day at $0.08/GB.
  - Shadow job 85 estimated **23.2 GB/day**, plus about 0.1 GB for the pump creates. That is close to the cap.
  - When the meter projects past the cap, it downscales: the cheapest streams go first, and the pools of open positions and the watchlist go last.
  - Raising the cap is the owner's call. Watch `budget.json` in the first hours.
- **`rpc.env` and `token-meta-rpc.approved`** (token names through paid Triton RPC) only after the owner's separate OK; see the README.
- **Secrets:** the values never go into the repo, a log, a prompt or a command line.

## 3. Installation, in order

```bash
# 3.1 the run checkout at C (frozen for the paper run)
git -C ~/Solana-project/Solana-bot fetch -q origin main
git -C ~/Solana-project/Solana-bot worktree add --detach ~/Solana-project/worktrees/paper-run <C>
R=~/Solana-project/worktrees/paper-run
git -C $R rev-parse HEAD                                   # note it
sha256sum $R/lab/paper/k3-paper-2026-10-12.yaml            # 04b5e652eb0301ae… (E14, unchanged; the journal's config_sha256)
sha256sum $R/lab/paper/h-arms-2026-10-16.yaml $R/lab/paper/risk.yaml $R/lab/paper/risk-h.yaml
                                                           # at dcff935: 0fdd1750…, f4847145…, 18c495b5…; note them
# 3.2 the recorder binary (0.4): already in place
readlink $LAB/bin/lab-recorder                             # lab-recorder-339c40e-bookworm
# 3.3 the live units only (lab-notebooks.service is installed and running: do not overwrite it)
cd $R/lab/ops/systemd
cp lab-recorder.service lab-recorder.timer lab-paper.service lab-paper.timer \
   lab-live-retention.service lab-live-retention.timer ~/.config/systemd/user/
cd ~
systemctl --user daemon-reload
systemd-analyze --user verify ~/.config/systemd/user/lab-{recorder,paper,live-retention}.service   # no errors
```

## 4. Measuring hour, start, checks

### 4.1 The measuring hour

Skip this if it has been done. It follows `research/meetuur-draaiboek-2026-10-12.md`, with the current binary and the paths of §2. It runs the recorder only, in its own root, so the paper root and journal stay empty.

```bash
M=$LAB/recorder-measure-$(date -u +%Y%m%d)
# step 0: dry run, no connection (check the filters as the draaiboek lists them)
$LAB/bin/lab-recorder --root $M --pool-creations --new-reversed --pump-creates --max-runtime-secs 3600 \
  --budget-usd 0.5 --no-downscale --dry-run
# step 1: the hour (the credentials only in this shell; never echo them)
set -a; . ~/.config/solana-lab/recorder.env; set +a
systemd-run --user --scope -p MemoryMax=2G -p CPUQuota=100% -- $LAB/bin/lab-recorder --root $M \
  --pool-creations --new-reversed --pump-creates --max-runtime-secs 3600 --budget-usd 0.5 --no-downscale >> $M.log 2>&1
# step 2: the chain test on the hour (a separate journal; little or no K3 trigger in one hour)
cd $R/lab && systemd-run --user --scope -p MemoryMax=3G -p CPUQuota=100% -- $PY -m paper.engine --live $M --catch-up \
  --config paper/k3-paper-2026-10-12.yaml --groups paper/h-arms-2026-10-16.yaml \
  --store $LAB/store/$S --journal $LAB/journal/measure-$(date -u +%Y%m%d)/journal --no-backup; cd ~
```

**After the hour:**
- the owner notes the hour's usage in the Triton dashboard; wire-ratio = Triton GB / the recorder's `spent_gb`;
- Lab Backtest analyses GB per stream (from `_segment.json`), probes and backfills, and latency;
- the coordinator and owner decide the budget.

Exit 75 means the cap was reached: do not restart, report it.

### 4.2 Start

The paper root is `$LAB/recorder`, the journal `$LAB/journal/paper-k3/journal`, both empty. Start the recorder first; the paper engine `Requires=` it.

```bash
systemctl --user start lab-recorder.service
sleep 120; ls $LAB/recorder/segments | tail -3; tail -n 20 $LAB/logs/recorder.log     # segments come; no errors
systemctl --user start lab-paper.service
systemctl --user enable --now lab-live-retention.timer lab-recorder.timer lab-paper.timer
systemctl --user enable lab-recorder.service lab-paper.service                        # at boot
```

### 4.3 Checks

Run these every 10 min in the first hour, then daily. Set the journal path first: `J=$LAB/journal/paper-k3/journal/journal.sqlite`.

```bash
systemctl --user status lab-recorder lab-paper --no-pager | grep -E "Active|Memory"   # paper ≤ 4G, recorder ≤ 2G
tail -n 20 $LAB/logs/recorder.log $LAB/logs/paper-engine.log
cat $LAB/recorder/budget.json                                                       # spent_gb against 25 GB
sqlite3 -readonly $J "SELECT k, v FROM state WHERE k IN ('config_sha256', 'groups_sha256', 'risk_sha256_at_start', 'risk_sha256_at_start:H', 'groups', 'farmer_seed')"
sqlite3 -readonly $J "SELECT max(minute), count(*) FROM heartbeat"                   # a row every minute
sqlite3 -readonly $J "SELECT count(*), sum(gap_slots) FROM heartbeat WHERE note LIKE 'feed gap%'"   # feed gaps
sqlite3 -readonly $J "SELECT variant, status, reason, count(*) FROM signals GROUP BY 1, 2, 3"     # K3 and the arms
sqlite3 -readonly $J "SELECT stream, round(sum(bytes) / 1e9, 2) FROM usage GROUP BY 1 ORDER BY 2 DESC"   # GB per stream
cat $LAB/recorder/alpenglow.json 2>/dev/null                                         # after the switch
```

**What to look at:**
- **The daily report** is `$LAB/journal/paper-k3/journal/<day>/report.md`, written when the day closes. It has:
  - K3's stop-rule counter;
  - the section for group H ("measurement, no test");
  - latency, farm labels and heartbeat.
- **The paper wallets** are `wallet.json` (K3) and `wallet-h.json` (H), in the journal directory.
- **Coverage** (the share of organic F7 triggers seen live, spec §5) is measured afterwards against OF1, in the forward replay (`hypothesis-h4h5-forward-2026-10-16.md`). The live proxies:
  - `$LAB/recorder/watchlist-current.json` refreshes every minute;
  - probes and backfills show in `_segment.json`;
  - few unknown pools in the engine log.
- **Investigation thresholds for H** (spec §4):
  - drains above 50 % for three days in a row;
  - coverage below 50 %;
  - take-profits that fill below ×1.8 in more than 10 % of the cases.
  
  If one is crossed, report it to the coordinator; do not change the arms.

### 4.4 K3's stop-rule clock

The plan for weeks 2–8 targets Monday 26 October, at the latest Monday 2 November (owner). The clock does not start at the paper start. Its start goes into the running journal's state, with a date and a reason (coordinator, 2026-10-16); there is no fresh journal.

```bash
cd $R/lab
$PY -m paper.clock --journal $LAB/journal/paper-k3/journal                     # shows it: "no clock start set"
$PY -m paper.clock --journal $LAB/journal/paper-k3/journal --start 2026-10-26 --reason "<why this day>"
cd ~
```

The command refuses a start that:
- is in the past;
- is after 2026-11-02;
- falls on a day the journal already has a K3 entry for, or has K3 entries after it (set it the day before; the engine may run).

Until its day comes, `--replace` moves it, and the earlier start stays in the record. From its day on, it stays.

From the clock start, the daily report's stop-rule counter and capital simulation count K3's trades entered on or after that day. The report shows:
- the clock day;
- when the decision is due: after 28 days with ≥ 200 trades, else at 200 trades, at most day 42.

Before the clock start, the report counts every paper day, without a decision.

## 5. Stop and rollback

**Stop everything** (README). The timers go first, or they start the units again.

```bash
systemctl --user disable --now lab-paper.timer lab-recorder.timer lab-live-retention.timer
systemctl --user stop lab-paper lab-recorder
```

**Group H off, K3 on.** Remove the `PAPER_ARGS` line from `live.env`, then run `systemctl --user restart lab-paper`.
- After a restart the engine replays the retained segments (3 days) into the same journal; the README's known limitation covers this.
- K3's rows come out the same with or without H (`tests/test_arms_replay.py`).
- H's rows so far stay in the journal.

**The recorder.** There is no older build to go back to: 54ac6e2 lacks #265, #287 and #290 (the rustls advisories), so **do not** roll back to it. For a recorder problem:
1. stop the units;
2. report it to the coordinator;
3. a new NAS build replaces the binary (`/lab/ops/build-recorder-bookworm.sh <sha>`).

**The engine.** Remove the run checkout:
```bash
git -C ~/Solana-project/Solana-bot worktree remove ~/Solana-project/worktrees/paper-run
```
Then repeat 3.1 at the earlier commit and run `systemctl --user restart lab-paper`. The journal stays; writes are idempotent per key.

**A disk emergency** writes `locks/disk-stopped` (README, "Disk guard"). Only the coordinator removes it, after checking.
