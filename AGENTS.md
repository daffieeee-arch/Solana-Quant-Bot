# AGENTS.md — Solana Quant Bot (lab reset, 2026-10-09)

> **Document status: ACTIVE.** On 2026-10-09 the owner froze V2 and restarted the project as a lean, results-first lab. This file replaces the V2 instructions, which are kept unchanged in [`docs/AGENTS_V2_FROZEN.md`](docs/AGENTS_V2_FROZEN.md) and at tag `v2-frozen-2026-10-09`.

## Goal

A Solana trading bot (pump.fun bonding curve and PumpSwap) that is profitable **after costs** in paper trading. Live trading is out of scope until the stop rule below passes and the owner decides otherwise.

## What changed

- **V2 is frozen.** Tag `v2-frozen-2026-10-09` marks the last V2 `main` commit. Nothing was deleted. Do not extend the V2 code (`src/`, `rust/`, `frontend/`, `scripts/`) or its documents; they are reference material only.
- **New work goes in [`lab/`](lab/README.md).** Keep it small and direct: code, tests, a dataset, a result.
- **Claude Code is the only agent that writes code.** Other assistants may read or review.
- **No approval ceremonies.** No leases, per-window approvals, sealed dossiers or multi-round governance reviews. Express realism and causality as code conventions and tests instead.

## Rhythm and stop rule

- One checkpoint per week with a tangible result: a dataset, a backtest table or a paper-trading report. Write it to `lab/reports/`.
- Week 1: event dataset (pump.fun + PumpSwap) from Old Faithful. Week 2: backtest tournament. Week 3+: paper trading.
- **Stop rule** (owner decision 2026-10-10): paper trading runs one fixed main candidate with a fixed configuration. After 4 weeks and at least 200 paper trades, measure the net P&L per trade after all costs (venue fees, network fees, tips, failed transactions). Compute its 95% confidence interval with a bootstrap over trading days (resample whole days, 10,000 times).
  - **GO:** the lower bound is above 0. Only then can real money be discussed (separate owner decision).
  - **ADJUST:** the mean is above 0 but the lower bound is at or below 0. Paper trading continues; no real money.
  - **STOP:** the mean is at or below 0.
  - Fewer than 200 trades after 4 weeks: the clock runs on until 200 trades, at most 6 weeks.
  - The clock starts no later than 2026-11-02 (target 2026-10-26), with the best candidate available then.

## Safety rules (unchanged and non-negotiable)

- PAPER ONLY. No private keys, wallet signing, transaction submission or live funds.
- Triton One is the only Solana data provider: Old Faithful archive files (`files.old-faithful.net`) for history and, later, Triton Yellowstone gRPC for live paper trading. No public RPC, Helius, QuickNode, Birdeye, DexScreener or other providers.
- Bounded spending: Old Faithful files are free. The Triton prepaid balance is topped up only by the owner; never top up or activate paid services automatically.
  - Owner decision 2026-10-10: an early top-up around 2026-10-14 (minimum deposit $125) is approved for a bounded, filtered live-data measurement (about 1 hour) before paper trading starts.
  - Every Triton client filters to what the bot needs, counts bytes and stops automatically at its budget.
  - The monthly budget and daily cap are set by the owner after that measurement.
- No secrets in Git, logs, prompts or reports.
- Never claim a strategy is profitable without the after-cost backtest or paper result that shows it.

## Realism conventions for backtests and paper trading

- Decisions use only information from transactions **before** the decision; entry is at least 1–2 slots after the signal transaction.
- Fills are simulated against the bonding curve or pool reserves **including our own price impact**, at several sizes (0.5 / 2 / 5 / 10 / 25 SOL), with protocol, creator and LP fees taken from the data plus transaction and priority fees.
- The hold-out period (the most recent week of data) is used once, for the final tournament ranking.
- Tests enforce these conventions; no separate approval step.

## Data and resources

- All Old Faithful data on the VPS lives under `/home/chupa/Solana-project/data-old-faithful-one`; lab datasets go to its `lab/` subdirectory (outside Git).
- Run heavy builds and backfills in a bounded user scope so other services on the VPS stay responsive, for example `systemd-run --user --scope -p CPUQuota=700% -p MemoryMax=11G -- nice -n 10 <command>` (the VPS has 8 cores, 15 GB RAM and no swap, so a memory cap keeps a runaway process from taking other services down).
- Do not stop or modify other projects' processes, services or data.
- **Retention** (owner decision 2026-10-10):
  - Raw live recorder data is kept for 3 days.
  - The paper journal, arrival times, 1-minute candles and the trades of tokens with a signal are kept.
  - Old raw Old Faithful chunks may be deleted, oldest first, after the rebuilt store has been verified and only when the free-disk floor requires it; they can be downloaded again.
  - The floor (provisionally 50 GB) is fixed after measuring the growth of the other projects on the VPS.
- New worktrees go under `/home/chupa/Solana-project/worktrees/`. The current plan and session roles are in `data-old-faithful-one/lab/research/plan-week2-8-2026-10-09.md`.

## Delivery

- Branch from `main`, open a PR, merge when CI is green. Keep [GitHub Project #4](https://github.com/users/daffieeee-arch/projects/4) current through issue metadata and the Roadmap Sync workflow (see `docs/operations/GITHUB_PROJECTS_ROADMAP.md`).
- The V2 CI gates still run on every PR; `lab/` code is tested with `cargo test` in its own crate.
