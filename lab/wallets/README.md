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
| 1 | `step1_report.py` | the Markdown of a finished run (follow + episodes + scores): net per position per variant with a 95 % day-bootstrap interval, venues, days, wallet level, scores per day, the walk-forward check (top-20 of day D followed on D), labels, top 12 |
| 1b | `runners.py`, `runners_report.py` | graduations with open-based outcomes and features at + 5 / + 15 min; base rates, quintile lifts, walk-forward AUC |
| 1b | `cases.py`, `cases_report.py` | case lists from our own data (trending 24 h, dumpers, 7-day stayers / collapsed, each with an `_organic` twin without dev bundles: create-slot graduation or ≤ 5 curve buyers); per-token aggregates are saved, so `--reselect` rebuilds the lists without the store, `--reselect --timelines` adds the missing timelines |
| cases | `phantom.py`, `phantom_report.py` | named tokens (the owner's Phantom list) from a forward store (never the hold-out, never for tuning): most-traded mint per symbol plus the copycats, timeline, dev-bundle flag, insiders, biggest buyers / sellers, and whether F7 / M1 / M1+ would have triggered (engine definitions, descriptive, no replay); pools with a non-SOL quote and windows cut by the store end are flagged; the report merges the stores and is in Dutch |
| check | `overfit.py` | overfitting check for every result report with a candidate (standard since 2026-10-12): Deflated Sharpe Ratio (SR0 from the trials' Sharpe variance and from the null variance 1/T) and the CSCV Probability of Backtest Overfitting with its degradation slope, over the trials of a run's parts (net P&L per UTC day at base costs); `--where` restricts the trials to the set the choice was made over, `--select` deflates the candidate itself; numpy only, no scipy |
| B | `markout.py`, `markout_report.py`, `nas_markout.sh` | spec B (`week3-specs-markout-2026-10-12.md`): for every distinct signal of a run's parts, the venue's mid at the end of slot t + d (d = 1, 2; e / b normal, b / e reversed, vq / vt curve) and at +1 s … +6 h, gross and as a 0.5 SOL round trip through the frozen engine (`LAB_ENGINE`) at base costs; the report gives per family × segment × d × horizon the median, mean, 1 %-trimmed mean with a day-bootstrap CI, the share of dead pools, the N2 twin paired per trigger, the K1/K2 and K3 rows with the spec's §5 verdict, and the exit-rule cost (realized net vs the markout at the median hold); NAS job, one day on the VPS as smoke test |
| E45/E49 | `mechanism_report.py`, `nas_mechanism.sh`; `depthband_report.py`, `nas_depthband.sh`, `nas_depthband_proof.sh` | the pre-registered tests on the clean data, the pool as the unit, three outcomes + NOT JUDGED: E45 (`research/prereg-f7-mechanism-clean-2026-10-21.md`) the log markout of the F7 pool_reversed triggers and the paired difference to the N2 twins at d = Clock(600 ms), h = 5 m; E49 (`research/prereg-f7-dieptebande-clean-2026-10-22.md`) the net markout at 5 m (0.5 SOL, real reserves, base network costs) of the triggers whose pool holds 100-150 SOL at the entry state (markout's `depth_entry`; `fee_bps` and E41's `creator_prior_pools` ride along as descriptives); `nas_depthband_proof.sh` shows on dev that the new markout columns leave every old column bit-identical and reproduce E48's band row |
| E54/C3 | `paidflow_report.py`, `nas_paidflow.sh`, `nas_paidflow_proof.sh`, `explore/paidflow_dev.py` | the pre-registered paid-flow test on the clean data (`research/prereg-f7-betaalde-kopers-clean-2026-10-21.md`), same unit and outcomes as E49: the net markout at 5 m of the F7 pool_reversed triggers preceded by at least 2 distinct buyers paying >= 5,000 lamports (priority fee + Jito tip, per transaction) on token buys in the 60 s before the trigger (markout's `paid_buyers_60s`, the window through the Clock per epoch, slot t included; `paid_max_60s` for the workflow's fee band, descriptive); `explore/paidflow_dev.py` makes the dev reference from the v4 markout rows, `nas_paidflow_proof.sh` shows on dev that the columns leave every old column bit-identical and reproduce that arm row |
| A | `money.py`, `money_report.py`, `nas_money.sh` | spec A (`week3-specs-money-2026-10-12.md`): P&L per wallet × token × UTC day from every trade on the curve and the SOL-quote pools (user-side cash, fees once per transaction, inventory marked at the day's last price capped at the venue's real SOL, open positions carried day by day); behaviour features per wallet-day; the report labels on days 1–9 (creator / fee-payer link, sniper, bundler, farmer, bot, whale, retail; hierarchy, min 5 positions), measures days 10–18 with a day-bootstrap CI and p-value, Holm over the 7 groups, 100 stratified placebo draws, the label overlap and the zero-sum check per venue; NAS job |
| A | `money_checks.py` | checks on a money-v1 run before interpretation (coordinator 2026-10-14): realised (cash − network fees) vs marked (Δ inventory value) per group on the binding days, all-wallet totals per day, farmers per day, the concentration of each group's loss over wallets, and the retail placebo's stratum coverage; `money_report.load(materialize=False)` / `--views` keeps the wallet-day rows as views on the parquet for small-memory re-renders; NAS job |
| check | `vins_compare.py`, `nas_vins.sh` | V_ins recompute after #213 (insider share independent of the stream window): `nas_vins.sh` reruns the M1x tournament on dev with the fixed engine (same command, new run id) under the same §10 rule, then `vins_compare.py` reports per family and period the flipped vetoes (dropped / added) between the old and the new run, the §10 verdicts and Δ(V_ins) rows of both, and the `insider_fallbacks` counter (must be 0 for M1 / M1x; O7 on F7 pool_reversed stays provisional until the token side is implemented) |

Run order for a period: `episodes` → `follow` (needs the store-v2 engine: `LAB_ENGINE=<dir with backtest/>`) → `score --day D` for each day → `step1_report`. The Phantom cases: `LAB_STORE=<fwd store> LAB_ENGINE=<engine> python -m wallets.phantom --symbols A,B --tag 1052 --out DIR` per store, then `phantom_report --cases DIR` (without `LAB_ENGINE` the strategy checks are skipped). Tests: `pytest -q lab/wallets/tests` (the follow tests need `LAB_ENGINE`).

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
| `f7_rev` | reversed PumpSwap pool, not mayhem | last real SOL depth ≥ 120 SOL and organic (farmers ≤ 50 % of the SOL volume in the trailing 60 min, judged when that volume ≥ 1 SOL) | depth < 100 SOL, the hour turns farming, or 6 h without a trade |

The F7 entry threshold is deliberately below the 150 SOL trigger threshold (coordinator decision 2026-10-12, measured in `lab/reports/2026-10-12-watchlist-meting.md`): entering at 150 missed 26 % of the organic triggers because the pool fills from ~85 to 150 SOL and the trigger sell is the first trade that sees 150; 120 / 100 covers 95.6 % of them for 12.2 instead of 10.3 GB/day as full transactions.
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

**Measurement over the development period** (NAS): `nas_watchlist.sh` runs `python -m wallets.watchlist --from-slot A --to-slot B --out DIR [--triggers <tournament run>] [--wallets CSV]` per UTC day (6 h warm-up carried in) and writes `watchlist-<lo>.parquet` (minute, pool, f7_rev, m1), `watchlist-stats-<lo>.json` (per day: members per minute median / p95 / max, events on the list vs all pool events, F7 candidate-sell coverage, coverage of the tournament's F7 triggers, M1 decision points at open + 15 min, M1 trades ≤ 6 h) and, with `--report`, `report.md` with the GB/day estimate at 3 KB (full transaction) and 1 KB (raw) per event. `LAB_WATCHLIST_F7_ENTER` / `LAB_WATCHLIST_F7_EXIT` override the F7 depth thresholds (default 150 / 120 SOL) for threshold variants without a code change. `--build-history PARQUET` writes once, over the whole range, per reversed pool the minute of creation and the first minute with ≥ 50 / ≥ 150 SOL real depth; a day run with `--history` then counts (coordinator / LAB backtest question, 2026-10-12) which F7 candidate sells, tournament triggers and F7 pool-minutes on the list fall in a pool that a candidate universe refreshed from OF1 with 1 or 2 days delay (data through the end of day D−1 / D−2) already contained: existed, ≥ 50 SOL or ≥ 150 SOL seen. The report's universe section gives the per-day shares and the totals, with the dynamic-list coverage inside each universe.

`trigger_gap.py` (LAB backtest question for the recorder's probe step, #182): live, a new reversed pool is seen as a vault balance until it passes 150 SOL and only then as full transactions. `python -m wallets.trigger_gap --triggers <tournament run> --to-slot B --out DIR` takes every F7 pool trigger (distinct pool, t, seller), the seller's trades in that pool over the 24 h before the trigger (the store's farmer window), counts how many fall before the pool's first slot at ≥ 150 SOL and whether the farmer flag (≥ 2 trades, |net tokens| < 5 % of gross) computed from the later trades alone differs from the flag over all of them; `report.md` per farm label, with the recomputed flag checked against the tournament's `trigger_farmer`. One pool-table scan restricted to the trigger pools: a NAS job.

## MEV phase 0 (`mev0.py`, `mev0_report.py`, roadmap #206)

This is a measurement on OF1 history only: nothing is sent and nothing is traded. The spec is `week3-specs-mev0-2026-10-12.md` v4. `nas_mev0.sh` runs `python -m wallets.mev0 --chunks <events/txctx-v1/chunks> --epochs 1040,1045`. It reads tx_ctx and the store, and per epoch writes these files into `mev0-v1/`:

- `arbs-<e>`: one row per arbitrage transaction:
  - kind (same / cross venue, `other_venue_unparsed`);
  - net and gross;
  - fees and tip;
  - the prior trade on its first venue (backrun, distance, reachable at ≥ 2 slots);
  - depth and age;
  - `closed_loop`.
- `attempts-<e>`: failed transactions, an upper bound.
- `gaps-<e>`: unrealized opportunities between two store pools of one mint.
- `signers-<e>`: first and last day per fee payer.
- `harm-<e>`: sandwiches and front-runs, description only.
- `days-<e>` and `run-mev0-<e>.json`.

`python -m wallets.mev0_report --dir mev0-v1 --epochs 1040,1045` then writes the report. It puts the §3 GO/NO-GO table on top, as medians over the full UTC days.

**Arbs are closed loops: the fee payer is flat in every token except SOL and WSOL.** The spec's literal test (flat in the traded mint, SOL + WSOL up) also accepts a routed sale of another token. On the 1054 smoke chunk that was 36 of 309 candidates, carrying 39.7 of 40.1 SOL. The parquet keeps every literal candidate with the flag. Memory: `LAB_WALLETS_MEMORY`. Run it on the NAS.

## Memory limits (DuckDB) per module

VPS defaults fit a 2 GB scope; on the NAS raise them per module: `LAB_EPISODES_MEMORY` (640MB), `LAB_FOLLOW_MEMORY` (1GB, main process) and `LAB_WORKER_MEMORY` (800MB per worker), `LAB_SCORE_MEMORY` (1GB), `LAB_RUNNERS_MEMORY`, `LAB_CASES_MEMORY` and `LAB_WATCHLIST_MEMORY` (1000MB); `LAB_WALLETS_MEMORY` sets all of them at once. Spill: `LAB_SPILL_DIR` / `LAB_SPILL_MAX`.

## Report template: overfitting check

Every result report that names a candidate gets a section **Overfitting-toets** with the table of `python -m wallets.overfit --parts <run>/parts --family <F> --where "<the set the choice was made over>" --select "<the candidate's trial>"`: N trials, T days, the best in-sample trial, its SR and PSR, SR0 and DSR (both variances), the PBO with the number of CSCV splits, the degradation slope and the loss share, and the candidate's own DSR and in-sample rank. The reading line of the output is part of the section, including this sentence: **F7 passed both on dev and still failed the hold-out: the DSR and the PBO test selection, not drift; they never replace a hold-out or a forward test.**
