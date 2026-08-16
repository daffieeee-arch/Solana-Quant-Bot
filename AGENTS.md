# AGENTS.md — Solana Paper Trading Bot

Read [`docs/HANDOFF.md`](docs/HANDOFF.md) first. This file is the compact, cross-tool instruction set for Hermes, Cursor, Codex, and other coding agents.

## Project goal

Build a professional Solana **paper-trading and research** platform. Pump.fun is the only protocol currently validated end-to-end offline. No wallet signing, real orders, or live funds are allowed.

## Source of truth and baselines

- GitHub default branch: `main`
- Create every task branch from the current `origin/main`; never edit `main` directly.
- Current repository tip is expected to move as docs, CI, and research tooling improve.
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Baseline tag: `offline-pump-baseline-20260815`
- Running baseline image: `solana-bot:contra-audit16-offline-pump-3e95a3c`

Do not call a moving repository HEAD the runtime baseline. Always distinguish repository tip, functional baseline, and running image SHA.

## Core architecture rules

- **Primary live Solana backend:** Triton One for Dragon's Mouth/geyser, RPC, DAS, and Titan when explicitly enabled.
- Allowed non-provider components include ClickHouse, Old Faithful/Jetstreamer, Grafana, frontend libraries, CoinGecko/CoinDesk market context, and external UI links.
- Helius, Birdeye, QuickNode, Alchemy, public Solana RPC/WS, and similar providers must not silently re-enter the primary backend path.
- **Zero-cost default:** `TRITON_LIVE_ENABLED=false` means no live Triton client construction.
- **MarketIdentity fail-closed:** no entry without a canonical market identity, proven decimals, fresh source data, and a bounded pricing/exit path. `gx:<mint>` is never canonical.
- **WAL/ledger is authoritative state.** Quarantine is an append-only administrative ledger event.
- **Pump-only proven baseline.** Other protocols remain fail-closed until separately decoded, identified, tested, and reviewed.
- MarketIdentity entry enforcement remains off; shadow evaluation only.

## Safety constraints

- Never execute real blockchain transactions or live trades.
- Never enable live Triton, Titan, RPC, DAS, or Dragon's Mouth traffic without explicit user approval, positive balance, duration limits, metering, and hard-stop safeguards.
- Never commit secrets, private keys, `_FILE` contents, `.env` values, or runtime credentials.
- Never restart the paused Old Faithful/Jetstreamer backfill without explicit approval.
- Never mutate, optimize, finalize, or stop ClickHouse casually.
- Deploy only after explicit approval, green quality gates, an immutable image tag, a rollback point, and runtime Git-SHA verification.

## Offline terminology

- `OFFLINE_ZERO_COST`: no paid Triton consumption. Free external context fetches may still occur in the ordinary runtime.
- `NETWORK_ISOLATED_REPLAY`: no external network calls at all; deterministic fixtures and mocks only.

Do not use these terms interchangeably.

## MCP policy

| MCP | Use |
|---|---|
| `triton-docs` | Current Triton/Dragon's Mouth/Titan contracts and capabilities |
| `solana-mcp` | Solana program, instruction, account, and transaction semantics |
| `truenas-mcp` | TrueNAS apps, mounts, jobs, and resources; read-only by default |
| `old-faithful-docs` | Old Faithful and Jetstreamer archive semantics |
| `clickhouse` | Bounded read-only ClickHouse metadata and queries through `hermes_ro` |
| `grafana` | Dashboards and observability; never use it to activate live cost tests |

Verify protocol/API claims against official documentation rather than model memory.

## Required engineering discipline

Use `systematic-debugging`, `test-driven-development`, `requesting-code-review`, `codebase-inspection`, and fresh-context reviewer subagents where relevant. Treat WAL changes as crash-safety work. Use protocol-specific reviewers for parser and MarketIdentity changes.

## Quality gates

Run before every pull request and merge:

```bash
npm ci
npm run ci:policy
npm test
npx tsc --noEmit
npm run build
git diff --check
git status --porcelain
```

GitHub CI must be green. CI receives no production secrets, forces zero-cost mode, and never deploys.

## Repository hygiene

- Runtime state and caches do not belong in the current tree: `.backtest-cache/`, `data-bot*/`, `data-stream*/`, `dist/`, logs, ledgers, lockfiles, and generated reports.
- Reusable deterministic data belongs under `tests/fixtures/` with provenance.
- Historical runtime artifacts may remain in old commits; do not rewrite published history without a separate approved migration.

## Read order

1. `docs/HANDOFF.md`
2. `docs/CURRENT_STATE.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DECISIONS.md`
5. `docs/KNOWN_ISSUES.md`
6. `docs/DEVELOPMENT_WORKFLOW.md`
7. `docs/CI.md`
8. `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md` when reviewing the repository-alignment PR
9. `docs/triton-cost-safety.md`
10. `docs/offline-hardening-decimals-cost.md`
11. `docs/pump-source-classification.md`
12. `docs/protocol-coverage.md`