# lab/wallets — whale and smart-wallet tracker (roadmap #167)

Research code of the "LAB research" session. Paper and research only; read-only on the store; every slot range is guarded against the hold-out (`common.guard`). Output goes outside Git to `data-old-faithful-one/lab/wallets/`.

| step | module | what it produces |
|---|---|---|
| 0 | `clusters.py` | cluster events: wallets that buy the same token in the same slot, linked by one transaction (`tx`), a shared fee payer (`pay`) or adjacent transactions with the same Jito tip (`tip`); how fast they sell |
| 0 | `veto.py` | what a veto on those clusters (K1 60 s, K2 10 min, K3 trigger seller in a cluster ≤ 24 h) does to F7 and N2, net, from a tournament run's positions |
| 0 | `report_step0.py` | the Markdown tables of `lab/reports/2026-10-11-whale-step0.md` |
| 1 | `episodes.py` | leader episodes: one per wallet × token position opened by a buy ≥ 1 SOL, with the leader's own realized and marked return and hold |
| 1 | `follow.py` | the follower's return per episode: our buy at t0 + d through the backtest replay (own size, costs, price impact), sold when the leader sold (W_half: half, W_full: 90%) or after 4 h |
| 1 | `score.py` | the point-in-time score for one day: window [D − 7 d, D), only positions closed before D; empirical-Bayes shrinkage, minimum n, Benjamini–Hochberg q-values; labels |
| 1b | `runners.py`, `runners_report.py` | graduations with open-based outcomes and features at + 5 / + 15 min; base rates, quintile lifts, walk-forward AUC |
| 1b | `cases.py`, `cases_report.py` | case lists from our own data (trending 24 h, dumpers, 7-day stayers / collapsed, each with an `_organic` twin without dev bundles: create-slot graduation or ≤ 5 curve buyers); per-token aggregates are saved, so `--reselect` rebuilds the lists without the store, `--reselect --timelines` adds the missing timelines |

Run order for a period: `episodes` → `follow` (needs the store-v2 engine: `LAB_ENGINE=<dir with backtest/>`) → `score --day D` for each day. Tests: `pytest -q lab/wallets/tests` (the follow tests need `LAB_ENGINE`).

## Tables for the private leaderboard page (LAB ui)

`scores-v1/leaderboard-<D>.parquet`, one row per wallet active in the window, addresses only (labels are probabilities, never linked to persons):

| column | meaning |
|---|---|
| `day` | the scoring day D; everything in the row was known before D |
| `wallet` | address |
| `creator`, `sniper`, `hf_bot`, `farmer`, `one_shot`, `bundler`, `split_buyer` | labels (booleans), `label_text` joined |
| `leader_n`, `leader_positions_per_day` | episodes in the window, per day |
| `leader_ret_marked`, `leader_ret_realized` | the wallet's own mean return per episode (remaining tokens marked at the last ≥ 0.01 SOL trade, capped at 50× the buy price / worth 0) |
| `leader_median_hold_s` | median seconds from the trigger buy to the sell that left ≤ 10% of the position |
| `leader_sol_in`, `curve_share`, `reversed_share` | SOL put in; share of episodes on the curve / in reversed pools |
| `follow_ret_d1`, `follow_ret_d2` | follower's mean net return per position at 0.5 SOL, W_half, delay 1 and 2 slots (base costs) |
| `follow_pnl_sol_d2` | the same in SOL per position |
| `follow_shrunk_d1`, `follow_shrunk_d2` | shrunk towards the all-wallet mean: the score |
| `q_d1`, `q_d2`, `top_d2` | FDR q-values; `top_d2` = q < 0.10 at d = 2 |
| `follow_n`, `control` | replayed positions; `control` = the wallet is in the random N2-wallet control group |
| `follow_median_hold_slots`, `leader_exit_share_d2` | our median hold; share of exits caused by the leader selling (rest: 4 h time stop) |
| `leader_sold_fast_share_d2`, `median_slots_to_leader_sell_d2`, `copyable` | share of positions where the leader sold within 10 slots of our entry (we were its exit liquidity); median slots until the leader sold; `copyable` = ≥ 5 positions and ≤ 25% fast sales |
| `rank_d2` | rank by `follow_shrunk_d2` (W_half, 0.5 SOL) |

`scores-v1/scores-<D>.parquet` is the long form (wallet × variant × size × d) with `n`, `filled`, `mean_ret`, `sd_ret`, `mean_pnl_sol`, `win`, `shrunk_ret`, `prior`, `k`, `p_value`, `q_value`, `top`, `rank`.

## Watchlist for live paper trading (`watchlist.py`, triton-edge §6)

The recorder asks Triton for full transactions only for pools we can trade and wallets we follow. `Watchlist` is one state machine for the recorder and for the batch measurement; everything it decides for minute m + 1 uses data through minute m.

| rule | pool | enters | leaves |
|---|---|---|---|
| `f7_rev` | reversed PumpSwap pool, not mayhem | last real SOL depth ≥ 150 SOL and organic (farmers ≤ 50 % of the SOL volume in the trailing 60 min, judged when that volume ≥ 1 SOL) | depth < 120 SOL, the hour turns farming, or 6 h without a trade |
| `m1` | normal SOL-quote pool, not mayhem, graduation outside the create slot | at pool creation | age > 6 h, or 60 min without a trade (a trade inside the 6 h brings it back) |
| wallets | followed wallets | fixed list (`--wallets` CSV) until the step-1 scores exist | — |

A pool with an open paper position stays on the list until the position closes; the paper engine adds those itself.

**Interface for the recorder** (`Watchlist.snapshot()`, JSON, written by whoever runs the state machine, read by the recorder every minute):

```json
{"version": 1, "valid_for_minute": 29870000, "valid_from_slot": 451000123, "generated_slot": 451000120,
 "pools": [{"pool": "<pool address>", "mint": "<mint>", "rules": ["f7_rev"]}, {"pool": "...", "mint": "...", "rules": ["m1"]}],
 "wallets": [{"wallet": "<address>", "source": "fixed"}],
 "counts": {"pools": 2, "f7_rev": 1, "m1": 1, "wallets": 1}}
```

The recorder feeds `Watchlist.step(minute, rows)` with per-(pool, minute) aggregates `(pool, trades, sol, farmer_sol, depth_last_sol)` from its own stream (vault account updates give the depth of silent pools) and `add_pool(PoolInfo(...))` when a new pool appears. `valid_for_minute` is `block_time // 60`.

**Measurement over the development period** (NAS): `nas_watchlist.sh` runs `python -m wallets.watchlist --from-slot A --to-slot B --out DIR [--triggers <tournament run>] [--wallets CSV]` per UTC day (6 h warm-up carried in) and writes `watchlist-<lo>.parquet` (minute, pool, f7_rev, m1), `watchlist-stats-<lo>.json` (per day: members per minute median / p95 / max, events on the list vs all pool events, F7 candidate-sell coverage, coverage of the tournament's F7 triggers, M1 decision points at open + 15 min, M1 trades ≤ 6 h) and, with `--report`, `report.md` with the GB/day estimate at 3 KB (full transaction) and 1 KB (raw) per event.

## Memory limits (DuckDB) per module

VPS defaults fit a 2 GB scope; on the NAS raise them per module: `LAB_EPISODES_MEMORY` (640MB), `LAB_FOLLOW_MEMORY` (1GB, main process) and `LAB_WORKER_MEMORY` (800MB per worker), `LAB_SCORE_MEMORY` (1GB), `LAB_RUNNERS_MEMORY`, `LAB_CASES_MEMORY` and `LAB_WATCHLIST_MEMORY` (1000MB); `LAB_WALLETS_MEMORY` sets all of them at once. Spill: `LAB_SPILL_DIR` / `LAB_SPILL_MAX`.
