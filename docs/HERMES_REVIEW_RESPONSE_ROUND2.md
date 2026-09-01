# Hermes review response — PR #1, round 2

> **Document status: HISTORICAL.** Legacy review evidence only. Hermes is not part of the active V2 architecture; this is not a runbook or authorization. See [`HANDOFF_V2.md`](HANDOFF_V2.md).

## Status

Hermes and a fresh-context reviewer returned `REQUEST_CHANGES` on head `b10f84f7070d9bbd431180ec55dccf99f989b1f5`.

The cleanup, documentation corrections, local gates, and current workflow were accepted. The remaining blocker was that the policy still inspected selected textual assignments instead of the effective workflow structure.

No merge, deployment, app start, Triton activation, backfill restart, or ClickHouse mutation was performed while addressing this review.

## Round-2 findings accepted

The previous policy could miss:

1. job-level inline live overrides;
2. step-level inline live overrides;
3. quoted safety keys;
4. job-level `permissions: write-all` or inline write maps;
5. additional checkout steps that omitted `persist-credentials: false`;
6. the fact that `git diff --check` without an explicit revision range mainly inspected the clean worktree.

These are valid security findings.

## Corrections

### 1. Semantic, fail-closed workflow parsing

`scripts/ci-repository-policy.mjs` now parses the workflow into a semantic object using a deliberately constrained YAML subset.

Supported forms include block maps/sequences, inline maps/sequences, quoted keys, quoted values, comments, and scalar types. Duplicate keys are rejected before validation. Unsupported YAML features fail closed instead of being approximated.

This avoids another raw-substring policy and does not add a package or lockfile dependency.

### 2. Scope-aware safety validation

The validator now requires:

- top-level permissions exactly `{ contents: read }`;
- no nested or job-level permissions override;
- safe workflow-level values for `CI`, `MODE`, `TRITON_LIVE_ENABLED`, and `ENTRY_SHADOW_MODE`;
- no occurrence of those safety keys elsewhere in the parsed workflow tree, including job, step, container, inline-map, and quoted-key forms;
- no reusable workflow jobs or job secrets;
- exactly one checkout step;
- explicit `persist-credentials: false` on that checkout;
- only the two reviewed official actions;
- no `secrets.*`, live unlock in `run`, `GITHUB_ENV` mutation, or deployment command.

### 3. Negative tests

`tests/ci-policy.test.ts` now contains twelve semantic/adversarial cases. They include all five exact round-2 reproductions plus duplicate inline keys, a second safe checkout, nested container overrides, and unsupported YAML failing closed.

### 4. Real PR patch validation

The workflow no longer relies on bare `git diff --check`:

- pull requests check the explicit base/head SHA range;
- push runs inspect the committed HEAD patch.

### 5. Documentation

`docs/CI.md` now describes the parser's supported subset, fail-closed behavior, permission model, checkout invariants, negative tests, and explicit revision-range patch check.

## Required re-review

Review the current `chore/repo-alignment-ci` head, not the superseded round-2 head.

Verify at minimum:

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

Independently mutate a temporary copy of the workflow with each round-2 bypass and confirm that `validateWorkflowConfiguration()` returns an error.

Use a fresh-context reviewer again and return exactly one verdict:

- `APPROVE`
- `REQUEST_CHANGES`
- `HOLD`

Do not merge or deploy automatically.
