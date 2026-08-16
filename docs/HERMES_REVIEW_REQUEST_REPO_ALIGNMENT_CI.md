# HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md

## Review request

Review the current head of `chore/repo-alignment-ci` against `origin/main` as a fresh senior reviewer.

Read first:

1. `docs/HERMES_REVIEW_RESPONSE_ROUND1.md`
2. `docs/HERMES_REVIEW_RESPONSE_ROUND2.md`
3. `docs/HERMES_REVIEW_RESPONSE_ROUND3.md`
4. `AGENTS.md`
5. `docs/HANDOFF.md`
6. `docs/CI.md`

Return one explicit verdict: `APPROVE`, `REQUEST_CHANGES`, or `HOLD`.

Do not merge, deploy, start the stopped app, activate Triton, restart the backfill, or mutate ClickHouse.

## Invariants

- PR base remains `origin/main` at `b42450affb6522e6b6c9d67f32285b5c070fb087` unless explicitly reported otherwise.
- Immutable functional/runtime baseline remains `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`.
- Tag `offline-pump-baseline-20260815` must still point to that functional baseline.
- Configured TrueNAS image remains based on `3e95a3c`.
- Last verified app state is `STOPPED`, `active_containers=0` unless a fresh read-only query proves otherwise.
- No existing `src/`, frontend, or existing test behavior is intended to change in this PR. `package.json` and `package-lock.json` intentionally add only the patched `yaml@2.9.0` dev/CI parser dependency.

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

1. use strict, duplicate-aware YAML 1.2 semantics for block/inline mappings, sequences, quoted keys, comments, and scalars;
2. reject duplicate block and inline keys and fail closed on intentionally unsupported YAML features;
3. require the tracked workflow file set to be exactly `.github/workflows/ci.yml`;
4. require the exact canonical trigger, concurrency, environment, job, `ubuntu-24.04` runner, ordered steps, actions, inputs, and commands;
5. require top-level permissions exactly `contents: read` and reject every nested/job override;
6. allow safety environment keys only in top-level `env` with exact safe values;
7. reject job, step, container, inline-map, quoted-key, or other nested overrides;
8. reject extra jobs, self-hosted runners, job containers, services, reusable workflow jobs, and a second workflow;
9. require exactly one checkout and explicit `persist-credentials: false`;
10. reject unapproved actions/inputs, arbitrary commands, dot/index/whole-context secret references, `GITHUB_ENV`, `${{ github.env }}`, live unlocks, and deployment commands.

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
- attached-hash YAML differential (`echo safe#; ssh host`);
- block-scalar indentation/chomping variants;
- bracket and whole-context secrets;
- `github.env` and expression-based live unlocks;
- extra jobs, self-hosted runners, containers, services, arbitrary commands, and action-input drift;
- trigger drift and a second tracked workflow.

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

Expected committed totals after the round-3 fixes:

- repository policy: PASS with 236 tracked files and 0 tracked ignored files;
- policy tests: 18/18;
- targeted tests: 40/40 in 5 files;
- full suite: 610/610 in 62 files;
- typecheck/build/diff/tree: PASS/clean.

## Workflow inspection

Confirm:

- official reviewed action versions only;
- automatic `GITHUB_TOKEN` limited to `contents: read`;
- one checkout with `persist-credentials: false`;
- no dot/index/whole-context secret references or production credentials;
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
