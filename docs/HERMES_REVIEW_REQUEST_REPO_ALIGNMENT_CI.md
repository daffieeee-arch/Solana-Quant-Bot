# HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md

## Review request

Review the current head of `chore/repo-alignment-ci` against `origin/main` as a fresh senior reviewer.

Read first:

1. `docs/HERMES_REVIEW_RESPONSE_ROUND1.md`
2. `docs/HERMES_REVIEW_RESPONSE_ROUND2.md`
3. `AGENTS.md`
4. `docs/HANDOFF.md`
5. `docs/CI.md`

Return one explicit verdict: `APPROVE`, `REQUEST_CHANGES`, or `HOLD`.

Do not merge, deploy, start the stopped app, activate Triton, restart the backfill, or mutate ClickHouse.

## Invariants

- PR base remains `origin/main` at `b42450affb6522e6b6c9d67f32285b5c070fb087` unless explicitly reported otherwise.
- Immutable functional/runtime baseline remains `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`.
- Tag `offline-pump-baseline-20260815` must still point to that functional baseline.
- Configured TrueNAS image remains based on `3e95a3c`.
- Last verified app state is `STOPPED`, `active_containers=0` unless a fresh read-only query proves otherwise.
- No existing `src/`, frontend, existing test, or `package-lock.json` behavior is intended to change in this PR.

## Synchronize safely

```bash
cd /opt/data/solana-paper-scanner
git fetch origin
git switch chore/repo-alignment-ci
git pull --ff-only
git rev-parse HEAD
git merge-base origin/main HEAD
```

Stop if local work would be overwritten.

## Diff and cleanup review

```bash
git diff --stat origin/main...HEAD
git diff origin/main...HEAD
git ls-files '.backtest-cache/**' 'data/**' 'data-bot*/**' 'data-stream*/**'
git ls-files -ci --exclude-standard
```

Expected:

- no tracked runtime/cache files;
- no tracked ignored files;
- legacy destructive helpers absent;
- no functional trading/provider/parser/ledger changes;
- only the policy test file is newly added under `tests/`.

## Semantic CI-policy review

Inspect `scripts/ci-repository-policy.mjs`, `scripts/lib/*.mjs`, `.github/workflows/ci.yml`, and `tests/ci-policy.test.ts`.

Verify that the parser and validator:

1. parse block and inline mappings/sequences;
2. normalize quoted keys;
3. reject duplicate block and inline keys;
4. fail closed on unsupported YAML features;
5. require top-level permissions exactly `contents: read`;
6. reject every nested/job permission override;
7. allow safety environment keys only in top-level `env` with exact safe values;
8. reject job, step, container, inline-map, quoted-key, or other nested overrides;
9. require exactly one checkout and explicit `persist-credentials: false`;
10. reject unapproved actions, reusable workflow jobs, secret references, `GITHUB_ENV` mutation, live unlocks, and deployment commands.

Independently reproduce these mutations in a temporary copy:

- job-level inline `env: { TRITON_LIVE_ENABLED: true }`;
- step-level inline live override;
- quoted live key;
- job-level `permissions: write-all`;
- job-level inline write permission map;
- second checkout without `persist-credentials`;
- second checkout with explicit false;
- duplicate block and inline keys;
- nested `container.env` live override.

Every mutation must fail.

## Quality gates

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
git diff --check origin/main...HEAD
git status --porcelain
git ls-files -ci --exclude-standard
```

A test-count increase is expected because additional policy tests were added. Explain the exact totals.

## Workflow inspection

Confirm:

- official reviewed action versions only;
- automatic `GITHUB_TOKEN` limited to `contents: read`;
- one checkout with `persist-credentials: false`;
- no `secrets.*` or production credentials;
- no deployment, SSH/SCP, Docker push, TrueNAS, live Triton, backfill, or ClickHouse action;
- pull-request whitespace checking uses explicit base/head SHAs;
- push whitespace checking inspects the committed HEAD patch;
- checks leave the tracked tree clean.

Inspect the latest GitHub Actions run on the current PR head where access permits. A green run does not replace local adversarial verification.

## Dependency audit

Report the existing dependency findings separately. Do not run `npm audit fix --force` and do not accept nonsensical breaking downgrades.

## Review output

Report:

1. verdict;
2. exact base/head/merge-base;
3. cleanup and tracked-ignore result;
4. semantic policy mutation results;
5. targeted/full test totals;
6. typecheck/build/diff/tree status;
7. GitHub Actions status and evidence;
8. dependency findings;
9. any remaining blocker with minimal fix;
10. confirmation that no merge/deployment/live/backfill/database action occurred.
