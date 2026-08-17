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
- exactly one tracked workflow file: `.github/workflows/ci.yml`;
- exactly one `quality` job on `ubuntu-24.04`; self-hosted runners, extra jobs, job containers, and services are forbidden;
- top-level permissions are exactly `contents: read`;
- job-level permission overrides are forbidden;
- GitHub's automatic `GITHUB_TOKEN` is therefore read-only;
- checkout credentials are not persisted;
- exactly one `actions/checkout` step is allowed, and it must explicitly set `persist-credentials: false`;
- only the reviewed `actions/checkout@v7.0.1` and `actions/setup-node@v7.0.0` actions are allowed;
- no repository or production secrets are referenced;
- `MODE=paper`, `TRITON_LIVE_ENABLED=false`, and `ENTRY_SHADOW_MODE=true` are defined once at workflow level;
- safety variables may not be overridden by a job, step, container, inline map, quoted key, or another nested mapping;
- the ordered action inputs and `run` commands must exactly match the reviewed validation-only workflow;
- no Docker push, SSH/SCP, kubectl, TrueNAS deployment, Triton activation, backfill action, or ClickHouse mutation.

## Semantic workflow policy

`scripts/ci-repository-policy.mjs` parses the workflow into a semantic object before validating it. It does not use substring presence as proof of safety. `yaml@2.9.0` is a direct dev/CI dependency; production runtime code does not import it.

The adapter uses a standards-compliant YAML 1.2 parser with strict parsing and duplicate-key rejection. The repository still accepts only the deliberately small canonical subset used by this workflow:

- block mappings and sequences with two-space indentation;
- quoted and unquoted scalar keys;
- quoted and unquoted scalar values;
- inline/flow mappings and sequences;
- YAML-correct comments and plain scalars, including attached `#` characters that are not comments;
- booleans, numbers, nulls, and strings.

Duplicate keys are rejected in block and inline mappings. Unsupported YAML features such as anchors, aliases, merge keys, explicit tags, document directives/markers, tabs, and block/folded scalars fail closed. Keeping the workflow in this canonical subset makes the security policy deterministic without adding a production runtime dependency.

The validator checks effective structure at every relevant scope:

1. the tracked workflow file set must be exactly `.github/workflows/ci.yml`;
2. triggers, concurrency, permissions, environment, job, runner, ordered steps, actions, inputs, and commands must match the canonical validation-only structure exactly;
3. top-level permissions must be exactly `{ contents: read }`;
4. top-level safety environment values must have their exact safe values and safety keys may not appear elsewhere;
5. nested/job permissions, extra jobs, self-hosted runners, containers, services, and reusable jobs are forbidden;
6. every checkout is checked independently and exactly one is allowed;
7. dot/index/whole-context secret references, live unlocks, `GITHUB_ENV`, `${{ github.env }}`, unapproved actions/inputs, arbitrary commands, and deployment commands are rejected.

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
- YAML comment differentials such as `echo safe#; ssh host`;
- block-scalar indentation/chomping variants;
- secret index and whole-context expressions;
- `github.env` and expression-based live unlocks;
- self-hosted runners, extra jobs, containers, and services;
- arbitrary commands, action-input drift, trigger drift, and a second tracked workflow;
- unsupported YAML syntax failing closed.

Current Phase-2 totals are 18 policy tests, 40 targeted critical tests, and 636 tests across 63 files in the complete suite. The twenty-five Pump historical-research tests run through the complete suite.

## Checks

The backend build runs `verify:research-transport` after TypeScript compilation. It executes the built v1 CLI under ESM/CJS import guards and fails if any HTTP(S), net/TLS, fetch/WebSocket, or `@solana/web3.js` transport-capable module enters the research graph.

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
