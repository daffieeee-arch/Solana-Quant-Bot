# DEVELOPMENT_WORKFLOW.md — Development workflow

## Branch per task

- Fetch current GitHub `main` and create a dedicated feature/chore/research branch.
- Never edit `main` directly.
- Keep commits small and logically scoped.
- Preserve immutable functional tags; do not reinterpret a moving repository tip as the runtime baseline.

Example:

```bash
git fetch origin
git switch main
git pull --ff-only
git switch -c phase2/pump-offline-research
```

Stop if local uncommitted work would be overwritten.

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

- Write targeted tests first for behavior changes.
- Run the full suite before requesting review.
- GitHub CI must be green before merge.
- Review workflow logs, not only the green badge.

## Engineering discipline

- Use systematic debugging: confirm root cause before changing code.
- Use TDD for parser, identity, accounting, persistence, and safety guards.
- Use fresh-context reviewers for security-sensitive and protocol-sensitive changes.
- Treat WAL/ledger changes as crash-safety work.
- Verify protocol claims with official docs/MCPs, not model memory.
- Do not let the implementing model be the only reviewer.

## MCP usage

- `triton-docs`, `solana-mcp`: protocol/API semantics; no paid live probes unless separately approved.
- `clickhouse`: bounded read-only queries through `hermes_ro`.
- `truenas-mcp`: read-only by default; mutations require explicit approval.
- `old-faithful-docs`: archive/backfill semantics.
- `grafana`: read metrics; do not activate cost tests.

## Pull requests

Every non-trivial change should include:

- purpose and scope;
- base/head SHAs;
- functional baseline impact;
- tests and CI evidence;
- safety/deployment impact;
- unresolved assumptions;
- explicit reviewer verdict.

Do not merge a draft PR. Do not auto-merge safety-sensitive work.

## GitHub CI security

- GitHub's automatic `GITHUB_TOKEN` is limited to `contents: read`.
- Checkout credentials are not persisted.
- No repository or production secrets are consumed.
- CI forces `MODE=paper`, `TRITON_LIVE_ENABLED=false`, and `ENTRY_SHADOW_MODE=true`.
- CI is validation-only: no Docker push, SSH, TrueNAS access, deployment, Triton activation, ClickHouse mutation, or backfill action.
- Workflow changes are security-sensitive and need independent review.

## Repository hygiene

The current source tree must not track:

- root `data/` runtime/deployment scratch;
- `.backtest-cache/`;
- `data-bot*/` or `data-stream*/`;
- runtime ledgers, runtime lock files, logs, generated reports, or build output;
- ignored legacy deployment scripts;
- secrets or credential artifacts.

`package-lock.json` is a required dependency lockfile and is not a runtime lock file. Reproducible samples belong under `tests/fixtures/` with provenance.

The policy runs both explicit path checks and `git ls-files -ci --exclude-standard`; a tracked ignored file is a failure unless a future explicit whitelist is independently justified.

## MarketIdentity changes

Current behavior must be stated accurately:

- full identity/decimals/freshness/exit-path contract is evaluated in shadow mode;
- shadow rejections do not generally block legacy entry;
- exact `gx:<mint>` is the current hard identity rejection;
- broader enforcement requires explicit approval and live shadow evidence.

Do not describe future enforcement as already active.

## Deployment

Deployment is separate from CI and requires:

1. explicit user approval;
2. green tests/build/CI;
3. immutable image tag;
4. rollback tag;
5. runtime `SOURCE_GIT_SHA` proof;
6. post-deploy observation;
7. live-cost controls before any Triton reactivation.

The 2026-08-16 read-only review observed the app as stopped. Do not start it as part of repository review.

## Dependency updates

- Investigate `npm audit` findings by dependency chain and actual exposure.
- Prefer minimal compatible updates with tests.
- Never run `npm audit fix --force` blindly.
- Keep dependency changes out of unrelated documentation/CI PRs unless a confirmed blocker requires them.

## Offline-first research

- `OFFLINE_ZERO_COST` is not necessarily network-isolated.
- Backtests and reproducible replay use `NETWORK_ISOLATED_REPLAY` with fixtures/mocks and blocked network access.
- Strategy research must use chronological train/validation/test separation and out-of-sample evaluation before any protocol expansion or renewed live spend.
