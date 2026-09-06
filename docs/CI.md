# CI.md — current validation boundary after retired-platform cleanup

> **Document status: ACTIVE.** B2A removed the retired Phase 8 image/deployment workflows and their build-only assertions while preserving ordinary CI and trusted-main Roadmap Sync. B3 added only a separately locked, offline-verified protocol crate and completed at `Fixture` evidence.

## Tracked workflow inventory

| Workflow | Current role | V2 disposition |
|---|---|---|
| `.github/workflows/ci.yml` | canonical general validation | **ACTIVE** |
| `.github/workflows/roadmap-sync.yml` | Project #4 reconciliation | **ACTIVE**; modify only through reviewed governance work |

The repository has exactly these two workflow files after B2A. “General CI has no secret/write access” applies to `ci.yml`; Roadmap Sync is a distinct trusted-default-branch boundary using the protected `PROJECT_TOKEN`. Never collapse those boundaries into one broad safety claim.

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
2. statically validate every B3 dependency section, exact direct declaration, locked package/source and crate-owned Rust input before any Cargo fetch;
3. fetch the separately locked B3 crate dependencies;
4. `npm ci` from `package-lock.json`;
5. repository/zero-cost and offline research citation policies;
6. focused policy/zero-cost/Pump tests and the complete Vitest suite;
7. TypeScript typecheck and retained Research Cockpit/cockpit-inertness build;
8. Rust reducer, supporting snapshots and B3 crate format checks;
9. run the complete all-edge B3 graph audit, clippy, tests, build scripts and matrix check under the network-deny launcher;
10. locked reducer all-target clippy/test/build;
11. committed-diff whitespace and clean tracked-worktree validation.

The B2A `npm run build` retains research-transport, Phase 8A offline and cockpit-inertness checks, but no longer builds the frozen paper dashboard or invokes Phase 8C/8D deployment/supply-chain validation. This narrows retired reachability; it does not prove authentic data or the future Observatory.

## Policy boundary

B4's isolated Rust planner adds a separate pre-fetch manifest/lock check, locked fetch, and syscall-isolated formatting/clippy/tests/build/deterministic-report gate. Its [contract](research/OF1_RUST_PLANNER.md) is Fixture planning only; these checks do not authorize acquisition. No B3 gate or Roadmap Sync workflow is replaced.

`scripts/ci-repository-policy.mjs` parses the canonical workflow and fails closed on unauthorized structure, permissions, actions, commands, secret references, safety-variable drift and missing Rust/citation gates. Adversarial tests preserve that protection while the exact workflow allowlist shrinks from five to two. Tracked `.hermes/**` material is forbidden after B2A.

Roadmap Sync is a separate privileged boundary. It always uses trusted default-branch code under `pull_request_target`; never change it to execute PR-head code or PR-produced artifacts. See [`operations/GITHUB_PROJECTS_ROADMAP.md`](operations/GITHUB_PROJECTS_ROADMAP.md).

Ordinary Roadmap Sync on an `opened`, `edited` or `synchronize` event is expected for eligible roadmap-linked pull requests. It may reconcile the PR item and its explicit `Roadmap:` owner; it does not authorize manual Project edits, provider calls, an acquisition run or another content migration.

## Local checks

The exact active list is maintained in [`AGENTS.md`](../AGENTS.md) and [`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md). Run focused checks during editing and every executable full gate before review.

GitHub CI remains the authoritative complete gate. The 2026-09-05 WSL host measurement in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) item 25 is not a local Node `22.23.2` / `rustup` receipt and does not authorize installation, native rebuilds or profile changes. Run the doctor first and record blocked commands.

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

## Historical PR 1 verification

PR 1 ran locally executable offline/documentation checks, link validation and `git diff --check`, then relied on the pull-request run for gates blocked by the unmodified local toolchain. Its counts below remain historical and are not B2A results.

Keep execution environments explicit. On the initial PR #69 commit, local `npm test` reached 100/110 test files and 1,411/1,423 tests; the remaining failures were missing local native/Rust prerequisites. GitHub CI run `33542154130` in its clean pinned environment passed 110/110 test files and 1,524/1,524 tests. These are separate observations, not interchangeable totals.
