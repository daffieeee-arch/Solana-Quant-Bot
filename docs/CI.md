# CI.md — current validation boundary during V2 cutover

> **Document status: ACTIVE transitional description.** This records the workflows that actually exist before PR 2A. Phase 8 image/deployment workflows are retired product paths but remain in the tree during PR 1; do not dispatch them.

## Tracked workflow inventory

| Workflow | Current role | V2 disposition |
|---|---|---|
| `.github/workflows/ci.yml` | canonical general validation | **ACTIVE**; preserve/rebase in PR 2A |
| `.github/workflows/roadmap-sync.yml` | Project #4 reconciliation | **ACTIVE**; extend only through reviewed Project migration work |
| `.github/workflows/phase8d-images-verify.yml` | historical no-push image verification | **RETIRED**, pending PR 2A removal |
| `.github/workflows/phase8d-images-publish.yml` | historical manual image publication | **RETIRED**; do not dispatch |
| `.github/workflows/phase8d-images-recover.yml` | historical digest recovery | **RETIRED**; do not dispatch |

The repository currently has exactly five workflow files. “General CI has no secret/write access” applies to `ci.yml`, not to every workflow: Roadmap Sync requires the separate protected `PROJECT_TOKEN`, and the retired publication workflow has package-write capability. Never collapse those boundaries into one broad safety claim.

## Canonical general CI

`ci.yml` runs one `tests-build-zero-cost` job on GitHub-hosted `ubuntu-24.04` with:

- top-level `contents: read` only;
- checkout credentials not persisted;
- exact Node `22.23.2`;
- exact Rust toolchain `1.97.1` with rustfmt/clippy;
- `MODE=paper`, `TRITON_LIVE_ENABLED=false`, `ENTRY_SHADOW_MODE=true`;
- no repository/production secrets;
- no provider call, Docker push, SSH, deployment, backfill or database mutation.

It triggers for pull requests targeting `main`, pushes to the branch patterns currently declared in the workflow, reusable calls and manual dispatch. `v2/**` is not currently a push pattern; a V2 pull request still triggers through `pull_request`.

## Current ordered gates

1. install the pinned Rust toolchain;
2. `npm ci` from `package-lock.json`;
3. repository/zero-cost policy;
4. offline research citation gate;
5. focused policy/zero-cost/Pump tests;
6. complete Vitest suite;
7. TypeScript typecheck;
8. backend/frontend build;
9. Rust reducer and supporting snapshot format checks;
10. locked all-target clippy/test/build;
11. committed-diff whitespace validation;
12. clean tracked-worktree validation.

The current `npm run build` still invokes Phase 8A/8C/8D contract and supply-chain checks. That is a truthful description of pre-cleanup code, not an active product endorsement. PR 2A must update build scripts, workflow inventory, repository-policy expectations and adversarial tests atomically; PR 1 intentionally does not.

## Policy boundary

`scripts/ci-repository-policy.mjs` parses the canonical workflow and fails closed on unauthorized structure, permissions, actions, commands, secret references, safety-variable drift and missing Rust/citation gates. Adversarial tests remain important evidence. PR 2A must preserve equivalent protection while removing retired workflow expectations.

Roadmap Sync is a separate privileged boundary. It always uses trusted default-branch code under `pull_request_target`; never change it to execute PR-head code or PR-produced artifacts. See [`operations/GITHUB_PROJECTS_ROADMAP.md`](operations/GITHUB_PROJECTS_ROADMAP.md).

For PR #69, ordinary Roadmap Sync on an `opened`, `edited` or `synchronize` event is expected. Under current `main` it may reconcile only the existing #69 item and normal PR-status fields; do not cancel that normal run. This does not authorize a manual dispatch, V2 fields/views, successor creation or bulk issue mutation.

## Local checks

The exact active list is maintained in [`AGENTS.md`](../AGENTS.md) and [`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md). Run focused checks during editing and every executable full gate before review.

The observed local WSL environment at PR 1 lacks the pinned Rust/native toolchain and has the wrong Node major. Do not install or modify it automatically. Record blocked local commands and use the clean GitHub CI run as authoritative execution evidence.

## What CI does not prove

Green CI does not prove:

- authentic OF1 acquisition or dataset provenance;
- historical Pump activation/layout truth beyond fixtures;
- complete account-state availability;
- point-in-time feature integrity unless covered by specific future tests;
- strategy edge, economic capacity or profitability;
- executable quote/fill realism;
- prospective Triton health/cost behavior;
- VPS or live-execution readiness.

CI never turns fixture evidence into real evidence.

## PR 1 verification

PR 1 should run all locally executable offline/documentation checks, link validation and `git diff --check`, then rely on the pull-request run for gates blocked by the unmodified local toolchain. It must not manually dispatch Roadmap Sync or any retired Phase 8 workflow.

Keep execution environments explicit. On the initial PR #69 commit, local `npm test` reached 100/110 test files and 1,411/1,423 tests; the remaining failures were missing local native/Rust prerequisites. GitHub CI run `33542154130` in its clean pinned environment passed 110/110 test files and 1,524/1,524 tests. These are separate observations, not interchangeable totals.
