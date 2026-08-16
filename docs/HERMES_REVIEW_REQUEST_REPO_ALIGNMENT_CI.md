# HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md

## Review request

Review the latest head of `chore/repo-alignment-ci` against GitHub `main` and return one explicit verdict:

- `APPROVE`
- `REQUEST_CHANGES`
- `HOLD`

Do not merge, deploy, start the app, enable Triton, restart the backfill, or mutate ClickHouse.

## Review history

Round 1 reviewed original PR head `7d1f82f9255bafdeb4c1396bd09567079fb5cdf3` and returned `REQUEST_CHANGES` for:

1. residual tracked runtime/ignored files and incomplete matching;
2. MarketIdentity enforcement overclaims;
3. a comment/quoted-value bypass in the zero-cost workflow policy;
4. supporting documentation inaccuracies about app state, GitHub token, and runtime lockfiles.

Read [`docs/HERMES_REVIEW_RESPONSE_ROUND1.md`](HERMES_REVIEW_RESPONSE_ROUND1.md) before re-reviewing. Review the **latest branch head**, not only the original commit.

## Base and recovery point

- PR base: GitHub `main` at `b42450affb6522e6b6c9d67f32285b5c070fb087`
- Immutable functional/runtime baseline: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Baseline tag: `offline-pump-baseline-20260815`
- Configured TrueNAS image: `solana-bot:contra-audit16-offline-pump-3e95a3c`
- Last read-only app observation: `STOPPED`, `active_containers=0`

The branch must not move or reinterpret the functional baseline tag.

## Intended scope

### Documentation alignment

- Replace the obsolete README.
- Distinguish moving repository tip, immutable functional baseline, configured image, and actual app state.
- State MarketIdentity accurately: full contract shadow-only, exact `gx:<mint>` hard gate currently enforced.
- Distinguish `OFFLINE_ZERO_COST` from `NETWORK_ISOLATED_REPLAY`.
- Update Phase 2 sequence: Pump-only offline research before protocol expansion.

### Repository hygiene

Remove from the current tree while preserving history:

- root `data/`
- `.backtest-cache/`
- all `data-bot*/`
- all `data-stream*/`
- ignored legacy helpers `scripts/gen-inline-yaml.py` and `scripts/reinstall-bot.py`

No live TrueNAS runtime path is deleted by this Git branch operation. Reusable deterministic samples belong under `tests/fixtures/`.

### CI and policy

- `.github/workflows/ci.yml`
- `scripts/ci-repository-policy.mjs`
- `tests/ci-policy.test.ts`
- `npm run ci:policy`
- `docs/CI.md`

The workflow must use only the automatic read-only `GITHUB_TOKEN`, persist no checkout credentials, consume no repository/production secrets, force paper/shadow/zero-cost values, and perform no deployment or infrastructure mutation.

## Required review procedure

### 1. Synchronize safely

```bash
cd /opt/data/solana-paper-scanner
git fetch origin
git switch chore/repo-alignment-ci
git pull --ff-only
```

Stop if local work would be overwritten.

### 2. Inspect complete diff

```bash
git diff --stat origin/main...HEAD
git diff origin/main...HEAD
```

Confirm no functional changes under `src/`, frontend, existing tests, or package-lock. The only intended new test is `tests/ci-policy.test.ts`.

### 3. Verify repository hygiene

```bash
git ls-files '.backtest-cache/**' 'data/**' 'data-bot*/**' 'data-stream*/**'
git ls-files -ci --exclude-standard
```

Expected: no output from either command.

Confirm `scripts/gen-inline-yaml.py` and `scripts/reinstall-bot.py` are absent from the current tree.

### 4. Run gates

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
git status --porcelain
```

Expected full-suite total after the four policy tests: 596, unless a clearly explained dependency/test-runner difference occurs.

### 5. Adversarial policy validation

Confirm the policy rejects:

```yaml
# TRITON_LIVE_ENABLED: 'false'
TRITON_LIVE_ENABLED: 'true'
```

Also confirm it rejects quoted/unquoted `true` and duplicate active assignments.

### 6. Inspect latest GitHub Actions run

Verify the actual latest branch run and logs, including:

- read-only permissions;
- `persist-credentials: false`;
- no repository/production secrets;
- policy and negative policy tests pass;
- full suite, typecheck, and build pass;
- no deployment/live/backfill/ClickHouse action.

### 7. Documentation against code/runtime

Verify:

- full MarketIdentity contract is shadow-only;
- exact `gx:<mint>` is the current hard identity gate;
- Triton clients remain behind explicit live construction;
- ordinary zero-cost runtime can still use free context;
- replay is network-isolated;
- Pump is the only proven complete protocol;
- configured app was last observed stopped;
- loaded-address coverage remains open;
- ClickHouse duplicate wording is appropriately qualified.

### 8. Fresh-context reviewer

Give a fresh reviewer this document, the response document, full diff, workflow/policy, AGENTS, and HANDOFF. Ask whether all round-1 blockers are closed without changing runtime behavior.

## Output

Return:

1. verdict;
2. exact branch/head;
3. local and GitHub CI evidence;
4. cleanup verdict;
5. MarketIdentity documentation verdict;
6. policy-bypass verdict;
7. CI security verdict;
8. dependency-audit classification;
9. remaining required changes;
10. confirmation that no merge/deployment/live/backfill/database action occurred.

## Merge policy

Do not merge automatically. Merge only after green CI, independent approval, and explicit user permission. No TrueNAS deployment is required for this repository-alignment/CI change.
