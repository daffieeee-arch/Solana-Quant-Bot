# AGENTS.md — Solana Paper Trading Bot

Read [`docs/HANDOFF.md`](docs/HANDOFF.md) first. This is the compact cross-tool instruction set for Hermes, Cursor, Codex, and other coding agents.

## Project goal

Build a professional Solana **paper-trading and research** platform. Pump.fun is the only protocol currently validated end-to-end offline. No wallet signing, real orders, or live funds are allowed.

## Source of truth and baselines

- GitHub integration branch: `main`; create every task branch from current `origin/main` and never edit `main` directly.
- Repository HEAD is allowed to move as docs, CI, and research tooling improve.
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`.
- Baseline tag: `offline-pump-baseline-20260815`.
- Configured baseline image: `solana-bot:contra-audit16-offline-pump-3e95a3c`.
- Last read-only TrueNAS observation on 2026-08-16: app **STOPPED**, `active_containers=0`. Do not call it running without a fresh query.

Always distinguish repository tip, functional baseline, configured image, and actual runtime state.

## Core architecture rules

- **Primary live Solana backend:** Triton One for Dragon's Mouth/geyser, RPC, DAS, and Titan when explicitly enabled.
- Supporting components may include ClickHouse, Old Faithful/Jetstreamer, Grafana, frontend libraries, CoinGecko/CoinDesk context, and external UI links.
- Helius, Birdeye, QuickNode, Alchemy, public Solana RPC/WS, and similar providers must not silently re-enter the primary backend path.
- **Zero-cost default:** `TRITON_LIVE_ENABLED=false` means no live Triton client construction.
- **MarketIdentity status:** the complete canonical-identity/decimals/freshness/exit-path contract is evaluated fail-closed in shadow mode. It records `WOULD_ACCEPT`/`WOULD_REJECT` but does not generally block the legacy entry flow. The currently enforced hard identity gate rejects exact `gx:<mint>` identities.
- **WAL/ledger is authoritative state.** Quarantine is an append-only administrative ledger event.
- **Pump-only proven baseline.** Other protocols remain incomplete until separately decoded, identified, tested, and reviewed.
- Broader MarketIdentity enforcement remains off pending live shadow evidence and explicit approval.

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

Use systematic debugging, TDD, codebase inspection, requesting code review, and fresh-context reviewers where relevant. Treat WAL changes as crash-safety work and use protocol-specific review for parser/MarketIdentity changes.

## Quality gates

```bash
npm ci
npm run ci:policy
npm test
npx tsc --noEmit
npm run build
git diff --check
git status --porcelain
```

GitHub CI must be green. The automatic `GITHUB_TOKEN` is restricted to `contents: read` and checkout credentials are not persisted. CI receives no repository or production secrets and never deploys.

## Repository hygiene

- Runtime state and caches do not belong in the current tree: `data/`, `.backtest-cache/`, `data-bot*/`, `data-stream*/`, `dist/`, logs, ledgers, **runtime lock files**, generated reports, and deployment scratch artifacts.
- `package-lock.json` is a required dependency lockfile and must remain tracked.
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
8. `docs/PUMP_OFFLINE_RESEARCH.md`
9. `docs/PHASE4_OLD_FAITHFUL_ADAPTER.md`
10. `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md`
11. `docs/HERMES_REVIEW_RESPONSE_ROUND1.md`
12. `docs/HERMES_REVIEW_RESPONSE_ROUND2.md`
13. `docs/HERMES_REVIEW_RESPONSE_ROUND3.md`
14. `docs/triton-cost-safety.md`
15. `docs/offline-hardening-decimals-cost.md`
16. `docs/pump-source-classification.md`
17. `docs/protocol-coverage.md`
