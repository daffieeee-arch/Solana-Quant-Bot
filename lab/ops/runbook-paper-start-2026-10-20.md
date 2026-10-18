# Runbook: paper start (K3 + group H), Tuesday 2026-10-20

For the coordinator, step by step, once the owner has given the OK and the Triton credentials.

**The day (owner decision, night of 2026-10-16):** the paper start is Tuesday **2026-10-20**, on route B (§1). The recorder and the paper engine then run at least 24 hours before the Alpenglow switch (2026-10-21, about 14:50 UTC).

**Scope:** paper only. No wallet, no keys, no transactions. K3 (E14) runs unchanged, and group H (the arms of H4 and H5, `research/week4-spec-paper-h4h5-2026-10-16.md`) runs beside it. This runbook fills in `README.md` ("Installeren en starten") for this start. Where they differ, this runbook applies to this start.

**Conventions:**
- Run every command on the VPS as `chupa`, in one shell, with these variables set:
  ```bash
  LAB=~/Solana-project/data-old-faithful-one/lab
  PY=~/.local/share/solana-quant/toolchains/lab-py/bin/python
  ```
- `C` is the run commit, **pinned by the owner on 2026-10-17: `c1e36a03318bd4474f25003492e72d12253b9e7b`** (#340, `FarmerFlags.flag` about 2× faster, so a restart catches up about twice as fast). It contains #299 (the arms), #301 (the streaming end() fix), #331 and #339 (the HWM restart) and #338.
  - At C the four config files hash to the values in §3.1 (checked 2026-10-17).
  - The measuring hour (§4.1) runs at the same C.
  - The night bundle (#341, #348, #349: main at or after 7928a6b) is deployed in the first night after the start, not on the start day.

## Timeline for 2026-10-20 (UTC)

| time | step | section |
|---|---|---|
| ~09:30 | Preconditions 0.1–0.7; settings and installation. | §0, §2, §3 |
| ~10:00 | The measuring hour: the recorder alone, `--budget-usd 0.5 --max-runtime-secs 3600`, in its own root. | §4.1 steps 0–1 |
| ~11:00 | The hour ends: the chain test on it (step 2, a few minutes); Lab Backtest reads GB per stream, probes and backfills. | §4.1 step 2, "After the hour" |
| ~11:15 | Budget decision with the owner: the day's `--budget-usd` goes into `RECORDER_ARGS` in `live.env`. | §2, §4.1 |
| ~11:30 | Start the recorder, then the paper engine; the first checks. | §4.2, §4.3 |
| by 14:50 at the latest | Both running: 24 hours before the switch. | 0.6 |

If the measuring hour has to be repeated or the budget decision waits, the start moves with it, as long as both run by 14:50 UTC. After that, the coordinator and the owner decide whether to start that day.

## 0. Preconditions

| # | check | how |
|---|---|---|
| 0.1 | The owner's OK for the paper start, the Triton credentials, and the day's budget. | owner |
| 0.2 | NAS job 92 is green: arm parity at dcff935, "0 unexplained" in both comparisons for both arms. Expected `unsold_busy`: 2 for flow300_H6, 1 for flow300_tp100_H6. | `parity.md` of job 92 |
| 0.3 | NAS job 91 is green (see below), as rerun with `--from-compare` on its output (with the compare that knows `farm_label_order`). | `compare.md`, `reconcile.json` and `state.jsonl` of job 91 |
| 0.4 | The recorder binary is the **5e2d1b7** build (#315, main a1c1712; QUEUE item 64). It has: `--pump-creates` (#287); durable budget (#265); rustls 0.23.46 (#290); Alpenglow block footers; no silent loss on a segment write error, exit 74 (#308); fail-closed on an unknown spend, with `--budget-assume-spent` (#308); and the meter saved before exit 74 (#315). NAS: 65/65 tests, GLIBC_2.34, cargo audit clean. | `readlink $LAB/bin/lab-recorder` → `lab-recorder-5e2d1b7-bookworm`; `sha256sum $LAB/bin/lab-recorder-5e2d1b7-bookworm` → `aeb4bea8…` (as noted at the build) |
| 0.5 | At least 50 GB free; the disk guard is active (unit `solana-disk-guard.timer`, script `~/Solana-project/ops/disk-guard.sh`, outside this repo); no other Triton consumer is running; no `locks/disk-low` or `locks/disk-stopped`. | `df -h ~`; `systemctl --user list-timers solana-disk-guard.timer`; `ls $LAB/locks` |
| 0.6 | Alpenglow: the switch is at epoch 1064, about 2026-10-21 14:50 UTC. **The start falls before it**: the recorder and the engine run at least 24 hours on the old consensus, then through the switch. The recorder handles it (footers), and so does the Clock (the last measured epoch until 1,000 slots of 1064). | `$LAB/recorder/alpenglow.json` appears at the first footer, after the switch |
| 0.7 | **PAPER_STORE on the VPS** (route B, §1): `$LAB/store/fwd1053-1054.duckdb`, mode 444, sha256 `be816aa5ad09bef331e894526bd8375601e0aab08f2c525225f000fa50066a40`, equal to the NAS copy. The coordinator copied it on 2026-10-16. | `stat -c %a $LAB/store/fwd1053-1054.duckdb` → `444`; `sha256sum $LAB/store/fwd1053-1054.duckdb` |

Job 91 (arms on, 2c3e5fe) counts as green when all of these hold:
- unexplained = 0, with `farm_incomplete` and `farm_label_order` as allowed causes:
  - `farm_label_order`: the farm label differs although live received every trade of the pool's farm hour, because the pool's backfill arrived after its first recorded trades;
  - compare.md counts it apart (1 trigger of 943 in job 91);
  - it counts only where the label decided the signal (one side skipped `farm flagged` in an organic-only group), never for an order, fill or exit that differs on its own (Codex 4). Checked 2026-10-18 for job 92y: its 2 rows are the H arms `flow300_H6` and `flow300_tp100_H6` at that trigger, skipped `farm flagged` in the replay and `busy` live (the same earlier H position on both sides), without orders; K3 there is the same on both sides;
- RSS plateaus in `state.jsonl`;
- reconcile is clean outside the simulated gaps: `ok_outside_gaps` in `reconcile.json`. Its `ok` also counts the rows the gap segments miss by design (49,249 in job 91, all in the 15 gap segments).

Job 91 ran without the end() fix (#301). That fix changes only the exit of a position that cannot sell at its deadline, not memory or coverage.

## 1. PAPER_STORE

The engine needs a store for `--live`. The store is the snapshot of the tokens and pools known before the start, plus the farmer history. The coordinator's rule (2026-10-16) is to use the newest store whose data has already been used.

**Route B is the route for 2026-10-20.**
- Per addendum §4, E20 runs only once epoch 1063 is complete, or at the latest on 2026-10-24 12:00 UTC. The clean data is therefore not spent on the 20th, and route A cannot be ready.
- The start accepts the gap of about ten days between the store's end (about 2026-10-11) and the start. This includes the `unknown pools` the engine log counts in the first days: pools created in the gap that keep trading. The farm labels are complete only from the first live slot + 24 h. See below.
- Route A stays for a later restart, if the coordinator and owner decide so (for example at the clock start).

**Route A: the clean data is spent before the start.** That means:
- E20 has written `clean.lock`;
- E20, H3, H4 and H5 have each written the `result.json` of a real run with a decision (E20's in `research/clean-test/clean-<commit7>/result/`).

Then the NAS, where clean-v1 lives, builds a store over the last two complete clean epochs: 1062–1063, or 1061–1062 if 1063 is not extracted yet. `python -m backtest.store paper` enforces the condition itself:
- It refuses every window in 1055–1063 until the lock and all four results are there. Without them it does not even list the clean root.
- It writes the evidence (the lock's sha256 and the result paths) into the store's meta, under `clean_spent`.

**Route B: otherwise.** Use **`fwd1053-1054.duckdb`**: slots 454,896,000–455,760,000, ending at the start of epoch 1055 (about 2026-10-11).
- It is the store that shadow job 91 measured with.
- It is already built, so there is no build step, only a copy.

**Route A, the NAS job.** The coordinator runs it from a checkout at C. It takes about 5 min: an epoch builds in about 140 s, chunk by chunk, within a 3 GB memory scope.

The paths are set as `backtest/nas_clean_test.sh` sets them, on the runner's `/lab` mount. Otherwise the store module falls back to its `/home/chupa/…` defaults and always refuses.

```bash
DATA=/lab/data-old-faithful-one/lab
LAB_PY=/lab/venv/bin/python
cd <checkout at C>/lab && LAB_STORE_DIR=$DATA/store LAB_CLEAN_RESULTS=$DATA/research/clean-test \
  LAB_CLEAN_CHUNKS=$DATA/events/clean-v1/chunks LAB_CHUNKS=$DATA/events/v1/chunks LAB_SPILL_DIR=/lab/tmp/paper-store \
  PYTHONPATH=. $LAB_PY -m backtest.store paper --epochs 2
# "refused: … not spent yet: no clean.lock … / no E20 result … / no H5 result …"   -> route B
# "store: …/store/paper-1062-1063.duckdb" and a meta with clean_spent              -> route A
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
                                                           # route A: paper <from> <to> ['clean', 'h3', 'h4', 'h5']
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

`live.env` takes the store from `$S` of §1. Run §2 in the same shell as §1, or set `S` again (`S=fwd1053-1054.duckdb` for route B).

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
git -C ~/Solana-project/Solana-bot worktree add --detach ~/Solana-project/worktrees/paper-run c1e36a03318bd4474f25003492e72d12253b9e7b
R=~/Solana-project/worktrees/paper-run
git -C $R rev-parse HEAD                                   # note it
sha256sum $R/lab/paper/k3-paper-2026-10-12.yaml            # 04b5e652eb0301ae… (E14, unchanged; the journal's config_sha256)
sha256sum $R/lab/paper/h-arms-2026-10-16.yaml $R/lab/paper/risk.yaml $R/lab/paper/risk-h.yaml
                                                           # at dcff935: 0fdd1750…, f4847145…, 18c495b5…; note them
# 3.2 the recorder binary (0.4): already in place
readlink $LAB/bin/lab-recorder                             # lab-recorder-5e2d1b7-bookworm
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

Exit 75 means the cap was reached, or today's spend is unknown (`recorder.log` says which; see §5). Do not restart; report it.

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
- **Until the restart fix with the high-water mark is deployed** (`paper/restart.py`, its PR planned for 2026-10-18): no planned restarts after day 3 (from the start + about 2.5 days), and deploys are bundled. A restart within the first 3 days replays everything from the start with the same farmer seed, so the journal comes out the same; a deploy of the fix on day 1 or 2 is such a restart.
- K3's rows come out the same with or without H (`tests/test_arms_replay.py`).
- H's rows so far stay in the journal.

**The recorder.** There is no older build to go back to: the earlier builds (339c40e, 2133bc6) are removed, and none of them has every fix of 0.4, so **do not** roll back. For a recorder problem:
1. stop the units;
2. report it to the coordinator;
3. a new NAS build replaces the binary (`/lab/ops/build-recorder-bookworm.sh <sha>`).

**Replacing the recorder binary** (the first nightly bundle brings the build of #358, the budget reservation; the coordinator notes its sha256 at the NAS build). Stop the old recorder cleanly first. A clean stop writes the last segment and leaves no unfinished segment directory. The new binary counts such a directory as unknown spend (exit 75) when today's `budget.json` is an older recorder's, without a reservation.
```bash
systemctl --user stop lab-paper lab-recorder                     # clean stop: the last segment is written
ln -sfn lab-recorder-<sha7>-bookworm $LAB/bin/lab-recorder        # the build copied from the NAS, as in 0.4
sha256sum $LAB/bin/lab-recorder-<sha7>-bookworm                  # as noted at the NAS build
systemctl --user start lab-recorder lab-paper
grep -c reserved_bytes $LAB/recorder/budget.json                  # 1: the new binary saved its first reservation
```

**The engine.** The paper engine and the retention timer run from the run checkout. Stop them before you replace it; the recorder runs from `$LAB/bin` and keeps running.
```bash
systemctl --user disable --now lab-paper.timer lab-live-retention.timer
systemctl --user stop lab-paper
git -C ~/Solana-project/Solana-bot worktree remove ~/Solana-project/worktrees/paper-run
# 3.1 at the earlier commit (worktree add, the sha checks)
systemctl --user start lab-paper
systemctl --user enable --now lab-paper.timer lab-live-retention.timer
```
The journal stays; writes are idempotent per key.

**The recorder stops with "today's spend is unknown"** (exit 75, `recorder.log`). This happens after an unreadable `budget.json` or `_segment.json` of today, or after an interrupted segment write when there is no `budget.json` of today. From the build of #358 on, it also happens when today's `budget.json` has no reservation (`reserved_bytes`), for example one written by the older recorder. Check what is broken. Then the owner restarts it once for that day with the spend the Triton dashboard shows:
```bash
sed -i 's/^RECORDER_ARGS="\(.*\)"$/RECORDER_ARGS="\1 --budget-assume-spent <YYYY-MM-DD>=<bytes>"/' ~/.config/solana-lab/live.env
systemctl --user start lab-recorder lab-paper
sed -i 's/ --budget-assume-spent [^"]*//' ~/.config/solana-lab/live.env       # off again (it holds for that day only)
```
Exit 74 (a segment could not be written, e.g. a full disk; from the build of #358 on also `budget.json`) restarts by itself and resumes after the last segment on disk. From #358 on, the restart counts the budget reservation on disk: at most one sustainable minute more than came, with a `budget` row.

**A disk emergency** writes `locks/disk-stopped` (README, "Disk guard"). Only the coordinator removes it, after checking.
