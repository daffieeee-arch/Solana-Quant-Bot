# CI.md — GitHub Actions quality gate

## Purpose

The workflow in `.github/workflows/ci.yml` independently validates a clean clone without touching TrueNAS or paid/live infrastructure. It is a validation workflow, not a deployment workflow.

Job name: `tests-build-zero-cost`.

## Triggers

- pull requests targeting `main`;
- pushes to `main` and normal work branches (`chore/**`, `feature/**`, `phase2/**`, `ci/**`, `cursor/**`);
- manual `workflow_dispatch`.

## Security posture

- GitHub-hosted Ubuntu runner;
- top-level permissions are exactly `contents: read`;
- job-level permission overrides are forbidden;
- GitHub's automatic `GITHUB_TOKEN` is therefore read-only;
- checkout credentials are not persisted;
- exactly one `actions/checkout` step is allowed, and it must explicitly set `persist-credentials: false`;
- only the reviewed `actions/checkout@v7.0.1` and `actions/setup-node@v7.0.0` actions are allowed;
- no repository or production secrets are referenced;
- `MODE=paper`, `TRITON_LIVE_ENABLED=false`, and `ENTRY_SHADOW_MODE=true` are defined once at workflow level;
- safety variables may not be overridden by a job, step, container, inline map, quoted key, or another nested mapping;
- no Docker push, SSH/SCP, kubectl, TrueNAS deployment, Triton activation, backfill action, or ClickHouse mutation.

## Semantic workflow policy

`scripts/ci-repository-policy.mjs` parses the workflow into a semantic object before validating it. It does not use substring presence as proof of safety.

The parser supports the deliberately small canonical YAML subset used by this repository:

- block mappings and sequences with two-space indentation;
- quoted and unquoted scalar keys;
- quoted and unquoted scalar values;
- inline/flow mappings and sequences;
- comments outside quoted strings;
- booleans, numbers, nulls, and strings.

Duplicate keys are rejected in block and inline mappings. Unsupported YAML features such as anchors, aliases, merge keys, tags, complex keys, document directives, tabs, and block scalars fail closed. Keeping the workflow in this canonical subset makes the security policy deterministic without adding a runtime dependency.

The validator checks effective structure at every relevant scope:

1. top-level permissions must be exactly `{ contents: read }`;
2. top-level safety environment values must have their exact safe values;
3. safety keys may not appear anywhere else in the parsed tree;
4. nested/job permissions are forbidden;
5. every checkout is checked independently and exactly one is allowed;
6. unapproved actions, secret references, live unlocks, `GITHUB_ENV` mutation, and deployment commands are rejected.

Adversarial tests cover:

- comment-based false-value camouflage;
- quoted and unquoted live values;
- duplicate block and inline keys;
- job-level inline live overrides;
- step-level inline live overrides;
- quoted live keys;
- job-level `write-all` and inline write permission maps;
- second checkout steps with default or explicit credential settings;
- nested `container.env` overrides;
- unsupported YAML syntax failing closed.

## Checks

1. `npm ci` from the committed lockfile;
2. `npm run ci:policy`;
3. targeted policy, zero-cost, Pump replay, vertical-slice, and TP/SL lifecycle tests;
4. complete Vitest suite;
5. `npx tsc --noEmit`;
6. backend and frontend build;
7. committed patch whitespace validation:
   - pull requests use the explicit GitHub base and head SHAs;
   - pushes inspect the committed HEAD patch;
8. verification that checks did not modify tracked files.

## Limits

CI does not prove:

- TrueNAS deployment correctness;
- live Triton connectivity;
- strategy profitability;
- protocol correctness beyond available fixtures;
- historical ClickHouse integrity.

The repository policy is a focused guardrail, not a substitute for GitHub secret scanning or a dedicated dependency/security program.

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
git diff --check origin/main...HEAD
git status --porcelain
git ls-files -ci --exclude-standard
```

## Changes to CI

Treat workflow and policy changes as security-sensitive. Keep the parser tests adversarial, inspect every action and permission scope, and require fresh-context review before merge. Do not add deployment behavior to this workflow without a separate approved design.
