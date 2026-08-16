# CI.md — GitHub Actions quality gate

## Purpose

The CI workflow independently validates a clean clone without touching TrueNAS or paid/live infrastructure.

- Workflow: `.github/workflows/ci.yml`
- Job: `tests-build-zero-cost`

## Triggers

- Pull requests targeting `main`
- Pushes to `main`
- Pushes to `chore/**`, `feature/**`, `phase2/**`, `ci/**`, and `cursor/**`
- Manual `workflow_dispatch`

## Security posture

- GitHub-hosted Ubuntu runner
- Automatic `GITHUB_TOKEN` limited to `contents: read`
- Checkout uses `persist-credentials: false`
- No repository or production secrets are consumed
- `MODE=paper`
- `TRITON_LIVE_ENABLED=false`
- `ENTRY_SHADOW_MODE=true`
- No Docker push, SSH, TrueNAS access, deployment, external service activation, backfill action, or ClickHouse mutation

GitHub always creates a job token. The security claim is therefore not “no GitHub token exists”; it is that the automatic token is read-only and is not persisted, and no additional secrets are supplied.

## Checks

1. Checkout full history without persisting credentials.
2. Install locked dependencies with `npm ci`.
3. Run `npm run ci:policy`:
   - fails on any tracked file ignored by `.gitignore` using `git ls-files -ci --exclude-standard`;
   - rejects root runtime/cache paths including `data/`, `data-bot*`, and `data-stream*`;
   - rejects legacy ignored destructive deployment helpers;
   - verifies `.env.example` safe defaults;
   - scans targeted credential/private-key patterns;
   - parses active YAML scalar assignments and requires exactly one effective zero-cost configuration;
   - rejects workflow secret references and deployment fragments.
4. Run negative CI-policy tests, including comment/quoted-value bypass attempts.
5. Run critical zero-cost and Pump lifecycle tests.
6. Run the complete test suite.
7. Run `npx tsc --noEmit`.
8. Run backend/frontend build.
9. Run `git diff --check` and verify checks did not modify tracked files.

## Policy threat cases

The policy tests must reject at least:

```yaml
# TRITON_LIVE_ENABLED: 'false'
TRITON_LIVE_ENABLED: 'true'
```

They also reject unquoted `true`, double-quoted `"true"`, and duplicate active assignments. Comments do not satisfy required values.

## What CI proves

- clean-clone dependency installation works;
- repository hygiene policy passes;
- critical and full tests pass;
- TypeScript and build pass;
- the workflow remains validation-only and zero-cost by configuration.

CI does not prove:

- TrueNAS deployment/runtime status;
- live Triton connectivity;
- strategy profitability;
- correctness beyond available fixtures/tests;
- ClickHouse/backfill integrity.

## Dependency audit

The current lockfile reports existing audit findings:

- three moderate production-chain findings through `@solana/web3.js -> jayson -> uuid@8.3.2`;
- one high dev-chain finding through Vite/PostCSS/nanoid.

These predate PR #1. Investigate separately; do not use `npm audit fix --force` in this alignment PR.

## Local equivalent

```bash
npm ci
npm run ci:policy
npx vitest run \
  tests/ci-policy.test.ts \
  tests/zero-cost.test.ts \
  tests/pump-replay.test.ts \
  tests/pump-vertical-slice.test.ts \
  tests/lifecycle-tp-sl.test.ts
npm test
npx tsc --noEmit
npm run build
git diff --check
```

## Merge and branch protection

After approval:

- require pull requests for `main`;
- require `tests-build-zero-cost`;
- block force pushes;
- require conversation resolution where available;
- keep deployment outside CI unless a future design receives separate explicit approval.

Treat every workflow/policy change as security-sensitive and review the actual logs, not only the status badge.
