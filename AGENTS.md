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
- **Stop rule:** after 4 weeks of paper trading and at least 200 paper trades the strategy must be profitable after costs; otherwise adjust or stop.

## Safety rules (unchanged and non-negotiable)

- PAPER ONLY. No private keys, wallet signing, transaction submission or live funds.
- Triton One is the only Solana data provider: Old Faithful archive files (`files.old-faithful.net`) for history and, later, Triton Yellowstone gRPC for live paper trading. No public RPC, Helius, QuickNode, Birdeye, DexScreener or other providers.
- Bounded spending: Old Faithful files are free. The Triton prepaid balance is topped up only by the owner, only when paper trading starts; never top up or activate paid services automatically.
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

## Delivery

- Branch from `main`, open a PR, merge when CI is green. Keep [GitHub Project #4](https://github.com/users/daffieeee-arch/projects/4) current through issue metadata and the Roadmap Sync workflow (see `docs/operations/GITHUB_PROJECTS_ROADMAP.md`).
- The V2 CI gates still run on every PR; `lab/` code is tested with `cargo test` in its own crate.
