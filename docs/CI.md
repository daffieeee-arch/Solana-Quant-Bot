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
- expose dataset roots (those stay outside Git on the approved Linux filesystem);
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
**45-minute** budget. The single-job shape is intentional:

1. fail-closed policy can deep-compare the entire workflow;
2. no job-level secrets, containers, services or write permissions;
3. one checkout with `persist-credentials: false`;
4. only approved actions: pinned `actions/checkout`, `actions/setup-node` and
   narrowly scoped `actions/cache`.

The first integrated WSL/VPS run [35531541423](https://github.com/daffieeee-arch/Solana-Quant-Bot/actions/runs/35531541423) reached the former 35-minute hard stop during Parquet/DuckDB, after Node, Pump, OF1 and Bronze passed. The expanded recorded-pipeline suite alone took 612 seconds. The job budget is now 45 minutes; the policy still requires that exact bound and every gate. The timeout receipt is retained with the [integration evidence](operations/VPS_WSL_INTEGRATION.md).

### Triggers

- pull requests targeting `main`
- pushes to `main` only
- `workflow_call` and `workflow_dispatch`

Open a draft PR early for delivery-branch validation, or explicitly dispatch CI
before a PR exists. A PR update runs full validation once instead of once for its
branch push and again for its PR event. Post-merge `main` validation is retained;
PR and main validate different integration states. This reduces duplicate runner
work, not necessarily the wall-clock time of one review cycle.

### Environment hard-stops

- `MODE=paper`
- `TRITON_LIVE_ENABLED=false`
- `ENTRY_SHADOW_MODE=true`
- top-level `permissions: contents: read` only
- concurrency cancels superseded runs on the same ref

### Ordered gates

1. pin Rust `1.97.1` (rustfmt/clippy);
2. static-validate Pump protocol, OF1 planner, Bronze decoder and Parquet projection source/dependency contracts;
3. restore the scoped Rust cache, fetch the four locked graphs and prepare the isolated hash-locked DuckDB reader;
4. `npm ci`;
5. repository/zero-cost policy + offline research citation gate;
6. focused policy/Pump/zero-cost tests, then full Vitest suite;
7. TypeScript typecheck + retained Research Cockpit/inertness build;
8. Rust format checks for reducer, namespace lock, Jetstreamer/Solana snapshots, Pump protocol;
9. isolated Pump protocol, OF1 planner, Bronze and Parquet/DuckDB offline evidence gates;
10. reducer clippy/test/build (`--locked`);
11. committed-diff whitespace + clean tracked worktree.

Green CI never upgrades fixture evidence into authentic acquisition, Research
Ready, edge or live claims. See the non-claims section below.

The [Parquet gate](../scripts/assert-of1-parquet-offline.mjs) has a real `--static`
mode before fetch (no Cargo/compiler execution). `--all` checks the reviewed
graph/features/licenses/build-script hashes, Rust tests and actual DuckDB reads
of Rust-generated Parquet, including coverage denominators, manifest-only shards,
logical-hash parity and sample/evidence reclassification failures, with sockets denied.
The [CI reader preparation](../scripts/prepare-columnar-query-ci.mjs) selects an
already installed CPython 3.13 x64 ABI from the hosted toolcache, records its
version, and installs only the existing DuckDB 1.5.5 hash-locked wheel in a fresh
runner-temp venv. No new action, system package, global Python or research
workspace is introduced. Missing compatible Python or wheel fails, never skips.
Local runs set `COLUMNAR_QUERY_PYTHON` to the existing isolated interpreter and
do not run CI-only preparation. The first VPS integration run verified hosted
reader preparation and real DuckDB checks before the job timeout; complete
integration acceptance is recorded separately in the integration evidence.

### Scoped Rust cache and measured phases

`actions/cache` is pinned to official v6.1.0 commit
`55cc8345863c7cc4c66a329aec7e433d2d1c52a9`. Its exact paths are Cargo registry
`index`, `cache` and `src`, plus the ignored `target` directories of
`of1-range-recorder`, `pump-protocol-v2` and `old-faithful-pump-reducer`.
It does not cache Cargo credentials/configuration, complete home directories,
temporary fixture runs, leases, datasets or operational evidence.

The versioned key binds runner OS/architecture, Rust 1.97.1, every Rust
Cargo.lock/Cargo.toml hash and the checked-out commit SHA. The single restore
prefix retains the same toolchain and lock/manifest identity. Cargo still checks
source/features and rebuilds affected artifacts. A cache hit never skips a gate,
test, assertion or evidence regeneration; a miss is an ordinary cold build.
Default cache branch scoping applies; the trusted-main Roadmap workflow does not
restore this build cache. No cache quota, billing or larger-runner setting changes.

The OF1 gate emits `OF1_CI_PHASE` records on stderr for compilation, test and
fixture subprocesses, followed by `OF1_CI_TIMING` with the overall gate outcome.
GitHub Actions also receives a compact step-summary table, including failure
when a later validation fails after successful subprocesses. These are measured
operational durations only: no commands, environment values or dataset contents
are added, and deterministic evidence/stdout contracts remain unchanged.
Unavailable summary output cannot turn a failed gate green or hide its exception.

Compare a cold run and a warm run of the same revision before making a speed
claim. Runtime-heavy tests still run in full and are not accelerated merely by
restoring compiled artifacts. Subphase measurements guide any later parallelism.

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

Parallel Rust/Node jobs, broader caches, or path filters may be added only
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
