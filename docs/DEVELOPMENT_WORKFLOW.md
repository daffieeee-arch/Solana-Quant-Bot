# DEVELOPMENT_WORKFLOW.md — Development and review workflow

## Branching

- GitHub `main` is the integration branch.
- Start every task from current `origin/main`:

```bash
git fetch origin
git switch -c <type>/<short-task> origin/main
```

- Never work directly on `main`.
- Keep one concern per branch and use small logical commits.
- The immutable runtime recovery baseline remains `offline-pump-baseline-20260815`; do not move or recreate that tag.

## Local quality gates

Run before pushing or opening a pull request:

```bash
npm ci
npm run ci:policy
npm test
npx tsc --noEmit
npm run build
git diff --check
git status --porcelain
```

Use targeted TDD tests first, then the full suite. Frontend changes require the full frontend build through `npm run build`.

## Engineering discipline

- `systematic-debugging`: prove root cause before changing behavior.
- `test-driven-development`: create a failing regression test before a fix where practical.
- `requesting-code-review`: use fresh-context reviewers for parser, identity, state, security, cost, and deployment changes.
- `codebase-inspection`: reconstruct actual code/runtime behavior rather than trusting old docs.
- WAL-sensitive work must be reviewed for crash windows, idempotency, and deterministic replay.
- Protocol claims require current official documentation through the appropriate MCP or primary source.

## Pull requests

Every functional or repository-policy change should use a pull request into `main`.

The PR description must include:

- purpose and scope
- base and head SHAs
- files/modules changed
- tests and checks run
- safety impact
- live-cost impact
- deployment impact
- rollback/recovery point
- known limitations

Do not merge until:

1. GitHub CI is green.
2. Hermes or another primary implementer reviews the full diff.
3. At least one independent fresh-context reviewer approves risk-sensitive work.
4. No unresolved secret, live-cost, state, or provenance concern remains.

## GitHub CI

Workflow: `.github/workflows/ci.yml`.

It runs on pushes to normal work branches and pull requests into `main`. It uses a GitHub-hosted runner with:

- read-only repository permission
- no production secrets
- `MODE=paper`
- `TRITON_LIVE_ENABLED=false`
- `ENTRY_SHADOW_MODE=true`
- repository-policy checks
- targeted zero-cost/Pump lifecycle tests
- full test suite
- TypeScript check
- production build

CI never:

- deploys to TrueNAS
- uses SSH into the NAS
- receives Triton, TrueNAS, Grafana, ClickHouse, or dashboard secrets
- enables live data
- starts the backfill

After the first green run, configure branch protection on `main` to require the `tests-build-zero-cost` check and a pull request before merge.

## Repository hygiene

Runtime state and generated output are not source code:

- `.backtest-cache/`
- `data-bot*/`
- `data-stream*/`
- `dist/`
- logs, ledgers, locks, generated reports

These paths must remain untracked. Reusable, deterministic, provenance-documented samples belong in `tests/fixtures/`.

The policy script checks the current tree. Historical commits may still contain legacy artifacts; published history is not rewritten without a separate approved migration.

## MCP usage

- `triton-docs` and `solana-mcp`: protocol/API semantics, not live calls.
- `clickhouse`: bounded read-only queries only.
- `truenas-mcp`: read-only by default; mutations require explicit approval.
- `old-faithful-docs`: archive/backfill semantics.
- `grafana`: observability reads and dashboard work, never a route to live cost tests.

## Deployment

Deployment is always separate from CI and requires explicit user approval.

Required deployment gates:

- green local and GitHub checks
- clean Git tree
- immutable image tag containing the exact Git SHA
- runtime `build.gitSha` match
- rollback tag/point
- secret-mount verification without exposure
- no live Triton unless separately approved and budget-protected

Never use a missing secret mount as justification to restore an inline secret.

## Synchronizing GitHub and TrueNAS

Changes made by Cursor, Codex, or this ChatGPT GitHub integration exist on GitHub first. Before Hermes reviews or continues locally:

```bash
git fetch origin
git switch <branch>
git pull --ff-only
```

Hermes must not overwrite remote work from a stale local branch. Use `git status`, `git branch -vv`, and compare SHAs before editing.

## Offline-first research

- Parser, MarketIdentity, accounting, and strategy logic must be fixture-testable without live services.
- Historical research must be chronological and network-isolated.
- No external price/news fetches during deterministic replay.
- Never optimize parameters on the final test period.

## Never

- commit secrets or private keys
- force-push published branches/tags without separate approval
- merge red CI
- activate live Triton without safeguards and approval
- execute real blockchain transactions
- restart the paused backfill without approval
- mutate ClickHouse during unrelated bot work