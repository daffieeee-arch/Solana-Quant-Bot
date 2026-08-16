# CI.md — GitHub Actions quality gate

## Purpose

The initial CI workflow independently validates the repository without touching TrueNAS or paid/live infrastructure.

Workflow file: `.github/workflows/ci.yml`

Job name: `tests-build-zero-cost`

## Triggers

- Pull requests targeting `main`
- Pushes to `main`
- Pushes to normal work branches such as `chore/**`, `feature/**`, `phase2/**`, `ci/**`, and `cursor/**`
- Manual `workflow_dispatch`

## Security posture

- GitHub-hosted Ubuntu runner
- Repository permission: `contents: read`
- Checkout credentials are not persisted
- No GitHub or production secret is provided to the job
- `MODE=paper`
- `TRITON_LIVE_ENABLED=false`
- `ENTRY_SHADOW_MODE=true`
- No Docker image push
- No SSH, TrueNAS deployment, or external service activation

## Checks

1. Checkout with full Git history for repository-policy checks.
2. Install Node.js 22 and npm dependencies through `npm ci`.
3. Run `npm run ci:policy`:
   - rejects tracked runtime/cache/secret paths
   - checks `.env.example` is paper-only and zero-cost
   - checks for private-key headers and targeted hardcoded credential assignments
   - checks the project remains private and the workflow itself remains read-only/zero-cost
4. Run targeted critical tests:
   - zero-cost guard
   - Pump parser replay
   - Pump vertical slice
   - normal TP/SL lifecycle and network-isolated replay
5. Run the complete test suite.
6. Run `npx tsc --noEmit`.
7. Run `npm run build`.
8. Run `git diff --check`.

## Limitations

The local repository-policy script is a focused guardrail, not a substitute for a dedicated secret-scanning product. Continue pre-push history scans for high-risk changes and enable GitHub secret scanning when available for this private repository.

CI proves that a clean clone passes the project checks. It does not prove:

- TrueNAS deployment correctness
- live Triton connectivity
- strategy profitability
- protocol correctness beyond available fixtures/tests
- historical ClickHouse integrity

## First-run procedure

1. Push `chore/repo-alignment-ci`.
2. Confirm the workflow is present and the job starts.
3. Inspect every step and logs; do not merely rely on a green summary.
4. If green, open the pull request into `main`.
5. Have Hermes review `docs/HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md` and the full PR diff.
6. Merge only after review approval.
7. Configure branch protection on `main`:
   - require a pull request
   - require `tests-build-zero-cost`
   - block force pushes
   - require conversation resolution where available

## Local equivalent

```bash
npm ci
npm run ci:policy
npx vitest run \
  tests/zero-cost.test.ts \
  tests/pump-replay.test.ts \
  tests/pump-vertical-slice.test.ts \
  tests/lifecycle-tp-sl.test.ts
npm test
npx tsc --noEmit
npm run build
git diff --check
```

## Changes to the workflow

Treat CI changes as security-sensitive. Review action versions, permissions, environment variables, cache behavior, and any new network or credential requirement. CI must remain validation-only unless the user separately approves a future deployment design.