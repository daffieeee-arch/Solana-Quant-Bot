# HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md

## Review request

Hermes: read this document first, then review the entire diff of branch `chore/repo-alignment-ci` against GitHub `main`.

This branch was prepared through the GitHub integration after an independent repository review. It intentionally does **not** deploy, enable Triton, restart the backfill, mutate ClickHouse, or change the proven Pump trading logic.

Return one explicit verdict:

- `APPROVE`
- `REQUEST_CHANGES`
- `HOLD` if the branch cannot be safely verified

Do not merge or deploy automatically.

## Base and recovery point

- PR base: GitHub `main` at docs handoff commit `b42450affb6522e6b6c9d67f32285b5c070fb087`
- Immutable functional/runtime baseline remains `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- Baseline tag: `offline-pump-baseline-20260815`
- Running TrueNAS image remains built from `3e95a3c`

The repository alignment must not move or reinterpret the functional baseline tag.

## Why these changes were made

An independent GitHub review found that the core code matched the recent work, but the repository presentation and controls did not:

1. `README.md` still described an obsolete DexScreener/Helius/Birdeye-era scanner.
2. Docs confused moving GitHub HEAD with the immutable runtime baseline.
3. Workflow instructions still told agents to branch from local `fix/audit14`, while GitHub exposes `main`.
4. Tracked runtime/cache directories contradicted `.gitignore` and could mislead Cursor/Hermes.
5. The v1 duplicate claim overstated what the sampled ClickHouse evidence proved.
6. `OFFLINE_ZERO_COST` was being described too much like full network isolation.
7. The loaded-address gap for versioned transactions was not prominent enough.
8. The next task had drifted: professional Pump-only offline research should precede another protocol.
9. GitHub had no independent CI status checks.

## Scope of this branch

### Documentation alignment

- Rewrite `README.md` to reflect the current offline Pump baseline.
- Update `AGENTS.md`, `HANDOFF`, `CURRENT_STATE`, `ARCHITECTURE`, `DECISIONS`, `KNOWN_ISSUES`, and `DEVELOPMENT_WORKFLOW`.
- Distinguish:
  - moving repository tip
  - immutable functional/runtime baseline
  - running image SHA
- Clarify Triton-only scope, zero-cost versus network-isolated replay, protocol HOLDs, and the Phase 2 research sequence.

### Repository hygiene

Remove from the current tree, while preserving published history:

- `.backtest-cache/`
- `data-bot/`
- `data-bot-final/`
- `data-bot-v3/`
- `data-bot-v4/`
- `data-stream/`

These are runtime/cache outputs, not reproducible source. Deliberate reusable samples must live under `tests/fixtures/` with provenance.

Confirm that no test, build, startup, or fixture path relies on the removed tracked files. The live TrueNAS runtime directories are outside this GitHub branch operation and must not be deleted from the NAS.

### CI and policy

- Add `.github/workflows/ci.yml`.
- Add `scripts/ci-repository-policy.mjs`.
- Add `npm run ci:policy`.
- Make `.env.example` explicitly paper-only, shadow-only, and Triton-live disabled.
- Add `docs/CI.md`.

The workflow must:

- use read-only GitHub permissions
- receive no secrets
- force `MODE=paper` and `TRITON_LIVE_ENABLED=false`
- run targeted zero-cost/Pump tests and the full suite
- run TypeScript and build
- never deploy, SSH to TrueNAS, start the backfill, or activate paid services

## Required review procedure

### 1. Synchronize safely

Do not edit from a stale local branch.

```bash
cd /opt/data/solana-paper-scanner
git fetch origin
git switch chore/repo-alignment-ci
git pull --ff-only
```

If local uncommitted work exists, stop and report it. Do not reset or overwrite it.

### 2. Inspect the full diff

```bash
git diff --stat origin/main...HEAD
git diff origin/main...HEAD
```

Check that the branch contains only repository alignment, CI/policy, example config, and current-tree cleanup. Confirm no functional trading/provider/ledger/parser code changed.

### 3. Verify removed paths

```bash
git ls-files '.backtest-cache/**' 'data-bot*/**' 'data-stream*/**'
```

Expected result: no tracked files.

Check whether any removed `strategies-v2.json` value is intentionally required. If a deterministic sample is required, request moving a sanitized, documented version to `tests/fixtures/`; do not restore runtime directories.

### 4. Run quality gates

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
git status --porcelain
```

Expected pre-review baseline remains at least 592 passing tests. A changed total is acceptable only if fully explained; this branch is not intended to change tests.

### 5. Inspect GitHub Actions

Verify:

- actions are official and versioned
- `permissions: contents: read`
- `persist-credentials: false`
- no `secrets.*`
- no deployment or container registry push
- no Triton/live environment unlock
- no unbounded external analysis step
- npm cache is based on `package-lock.json`

Review the actual workflow run and logs after push.

### 6. Review documentation against code

Specifically verify:

- `src/main.ts` really constructs Triton providers only behind the live guard
- `OFFLINE_ZERO_COST` can still permit free market-context fetches
- replay tests are network-isolated
- Pump is the only fully validated protocol
- entry enforcement remains shadow-only
- running image still comes from `3e95a3c`
- loaded-address coverage remains an open issue
- the ClickHouse duplicate wording is appropriately qualified

### 7. Independent reviewer

Use one fresh-context reviewer given only:

- this document
- the branch diff
- `AGENTS.md`
- `docs/HANDOFF.md`
- the workflow and policy script

Ask whether the branch improves clarity and safety without changing runtime behavior.

## Review output format

Return:

1. Verdict: `APPROVE`, `REQUEST_CHANGES`, or `HOLD`
2. Local branch/head reviewed
3. GitHub CI run/status reviewed
4. Tests/build/typecheck/policy results
5. Runtime/cache cleanup verdict
6. Documentation accuracy verdict
7. CI security verdict
8. Any false or stale claim with file/line evidence
9. Any required changes, severity, and minimal fix
10. Confirmation: no deployment, no live Triton, no backfill restart

## Merge policy

Do not merge from Hermes automatically. After approval, report the verdict to the user. Merge only after explicit user approval and green GitHub CI. No TrueNAS deployment is required for this docs/CI/repository-hygiene change.