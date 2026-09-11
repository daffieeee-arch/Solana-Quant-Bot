# CI.md — public zero-cost validation boundary

> **Document status: ACTIVE.** The repository is **public**. Ordinary CI remains
> validation-only and secret-free. Roadmap Sync stays a separate trusted-main
> boundary with `PROJECT_TOKEN`. B2A removed retired Phase 8 image/deployment
> workflows. This document is not acquisition authorization.

## Public-repository posture

Making the repository public is an operational choice to restore GitHub-hosted
Actions capacity for standard public runners. It does **not**:

- authorize OF1/Triton network calls;
- weaken zero-cost / Triton-only product boundaries;
- expose dataset roots (those stay outside Git on WSL);
- make `PROJECT_TOKEN` optional for Roadmap Sync.

The public cutover is an operator action, not a secret-scan guarantee from this
PR: no reproducible history-scan receipt is included here. Local-only credential
files must remain outside Git. Legacy helpers that read such files are not
authorization to commit them. This CI change does not alter repository visibility,
billing, credentials or access controls.

## Tracked workflow inventory

| Workflow | Current role | V2 disposition |
|---|---|---|
| [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | canonical general validation | **ACTIVE** |
| [`.github/workflows/roadmap-sync.yml`](../.github/workflows/roadmap-sync.yml) | Project #4 reconciliation | **ACTIVE**; modify only through reviewed governance work |

Exactly two workflow files. Never collapse CI and Roadmap Sync into one broad
privilege claim.

## Canonical general CI design

`ci.yml` runs **one** `tests-build-zero-cost` job on `ubuntu-24.04` with a
**35-minute** budget. The single-job shape is intentional:

1. fail-closed policy can deep-compare the entire workflow;
2. no job-level secrets, containers, services or write permissions;
3. one checkout with `persist-credentials: false`;
4. only approved actions: pinned `actions/checkout` and `actions/setup-node`.

### Triggers

- pull requests targeting `main`
- pushes to `main`, `chore/**`, `feature/**`, `phase2/**`, `ci/**`, `cursor/**`, `v2/**`
- `workflow_call` and `workflow_dispatch`

`v2/**` is included so delivery branches get push CI even before a PR exists.

### Environment hard-stops

- `MODE=paper`
- `TRITON_LIVE_ENABLED=false`
- `ENTRY_SHADOW_MODE=true`
- top-level `permissions: contents: read` only
- concurrency cancels superseded runs on the same ref

### Ordered gates

1. pin Rust `1.97.1` (rustfmt/clippy);
2. static-validate then fetch locked Pump protocol dependencies;
3. static-validate then fetch locked OF1 planner dependencies;
4. `npm ci`;
5. repository/zero-cost policy + offline research citation gate;
6. focused policy/Pump/zero-cost tests, then full Vitest suite;
7. TypeScript typecheck + retained Research Cockpit/inertness build;
8. Rust format checks for reducer, namespace lock, Jetstreamer/Solana snapshots, Pump protocol;
9. isolated Pump protocol and OF1 planner offline evidence gates;
10. reducer clippy/test/build (`--locked`);
11. committed-diff whitespace + clean tracked worktree.

Green CI never upgrades fixture evidence into authentic acquisition, Research
Ready, edge or live claims. See the non-claims section below.

## Roadmap Sync boundary

Roadmap Sync observes PR/issue events via `pull_request_target` but always
checks out the **trusted default branch** and never executes PR-head code. It
requires repository secret `PROJECT_TOKEN`. Missing token fails closed; do not
weaken that gate to make a PR look green.

Public issue/PR text is not maintainer approval. The checked-in `contentIntake`
policy admits trusted authors and explicitly reviewed external content before
metadata parsing or inheritance. Unreviewed external input cannot assign Project
delivery/evidence fields or block trusted reconciliation with malformed metadata.
Existing excluded Project items remain untouched. See the
[public-repository controls](operations/GITHUB_PUBLIC_REPOSITORY_CONTROLS.md).

## Required GitHub settings (operator)

The minimal profile was enabled and read back on 2026-09-11; reread GitHub for
current state. No second approving account is required:

1. Actions enabled for public repositories / spending limit healthy enough for
   public runners;
2. PR and required status check `tests-build-zero-cost` on `main`, including admins;
3. Automatically delete head branches on merge;
4. No force pushes or deletion of `main`; resolve review conversations;
5. Keep `PROJECT_TOKEN` as a repository secret with least privilege for Projects.

Repository secret scanning, push protection and Dependabot alerts are enabled.
Automatic dependency-update PRs remain disabled. This is not a clean-history
secret-scan receipt and cannot revoke a credential exposed outside GitHub.

Branch inventory process: [`operations/BRANCH_HYGIENE.md`](operations/BRANCH_HYGIENE.md).

## Future CI evolution (not in this change)

Parallel Rust/Node jobs, Rust cache actions, or path filters may be added only
through a reviewed policy update that keeps:

- `contents: read` for ordinary CI;
- no provider/live secrets in CI;
- Roadmap Sync privilege isolation;
- fail-closed workflow deep comparison.

Do not add deployment, GHCR push, SSH, or public-RPC jobs to ordinary CI.

## Local checks

Authoritative command list: [`AGENTS.md`](../AGENTS.md) and
[`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md). Run the doctor first on a
new host. GitHub CI remains the complete gate when local toolchains diverge.

## What CI does not prove

- authentic OF1 acquisition or dataset provenance;
- historical Pump activation beyond fixtures;
- account-state completeness;
- PIT feature integrity unless covered by specific future tests;
- strategy edge, capacity or profitability;
- executable quote/fill realism;
- Triton health/cost behavior;
- VPS or live-execution readiness.

## Historical note

Older Phase 8 image workflows and PR-1 local/CI count comparisons remain
historical evidence only. They do not authorize restoring retired deployment
paths.
