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
