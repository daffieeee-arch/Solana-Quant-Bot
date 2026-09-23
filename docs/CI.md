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
| [`.github/workflows/codeql.yml`](../.github/workflows/codeql.yml) | CodeQL source-security analysis | **ACTIVE**; only its analysis job may write security results |
| [`.github/workflows/dependency-review.yml`](../.github/workflows/dependency-review.yml) | PR dependency vulnerability review | **ACTIVE**; read-only GitHub token |

Exactly four workflow files. The security workflows were explicitly requested
on 2026-09-21. Ordinary CI, Roadmap Sync and security analysis retain separate
privilege boundaries. Both security workflows are parsed and deep-compared by
`scripts/lib/security-workflow-policy.mjs`; additional files, steps, privileges,
mutable action refs and weakened checks fail the ordinary repository policy.

## CodeQL and dependency review

CodeQL runs on PRs targeting main, main pushes, Monday at 05:43 UTC and manual
dispatch. Four language jobs cover Actions, JavaScript/TypeScript, Python and
Rust, at most two concurrently, with 30 minutes per job. Each uses the standard
GitHub-hosted Ubuntu 24.04 runner; nothing is installed on the VPS. Only the
CodeQL job gets `security-events: write`, solely to publish scan results.
Checkouts do not retain credentials. There are no repository/provider secrets,
deployment steps, shared application caches or paid runners in these workflows.

The CodeQL action is pinned to official v4.38.1 commit
`1c5b675653bb5c22dbe9b12b556ec555138e09fd`; dependency review is pinned to official
v5.0.0 commit `a1d282b36b6f3519aa1f3fc636f609c47dddb294` (verified 2026-09-21).
Action updates need a reviewed pin and policy update. Their internal Node runtime
does not change the project's Node 22.23.2 pin or other VPS projects.

CodeQL uses `build-mode: none` and the default query suite. This is not an
execution sandbox: Rust extraction uses rust-analyzer and can execute Cargo
build scripts/procedural macros and resolve dependencies. Rust 1.97.1 is prepared
on the disposable hosted runner. No application, collector or project tests are
explicitly launched by this scan. Coverage of generated code and semantic
dependencies can be incomplete; a successful scan is not a clean-security or
research-readiness claim. The existing functional CI gates remain required.
See [GitHub's build-mode documentation](https://docs.github.com/en/code-security/reference/code-scanning/codeql/build-options-for-compiled-languages).

Dependency review runs on every PR targeting main, including PRs without
manifest changes so its required check never disappears behind path filters.
The five-minute job fails on newly introduced **moderate, high or critical**
vulnerabilities in runtime, development or unknown dependency scopes. It does
not silently ignore advisories or use warn-only mode. Results are in the check
summary; PR comments are disabled so no PR write permission is needed.
License enforcement and external OpenSSF Scorecard requests are disabled.
Snapshot warnings have a bounded 60-second retry window.
See [the action's reviewed inputs](https://github.com/actions/dependency-review-action/blob/a1d282b36b6f3519aa1f3fc636f609c47dddb294/action.yml).

The operator enables the `dependency-review` required check only after its first
successful PR execution, preserving the existing `tests-build-zero-cost` check
and other protection settings. Initial CodeQL findings remain visible for
triage; this change does not claim to remediate all previously existing code.
Default CodeQL setup must remain off to avoid a duplicate generated workflow;
the committed workflow provides advanced setup.

Dependency review evaluates a PR diff, not the whole existing dependency tree.
The initial Dependabot inventory has four medium alerts across three advisories:
`vitest`/`@vitest/mocker` 3.2.7, `stream-json` 1.9.1 and `uuid` 8.3.2. Those are not
dismissed, upgraded or declared exploitable merely by installing this control.
The GitHub dependency graph covers npm, Cargo and Actions; verify its coverage
of the separately hash-locked DuckDB reader rather than assuming every custom
dependency format is recognized. Lock/hash integrity and known-vulnerability
screening are different checks.

Acceptance requires the policy bypass regressions, existing required CI, the
first successful dependency-review check, uploaded CodeQL analyses for all four
languages, independent review, and post-merge main CI/CodeQL/roadmap read-back.
The visible result is the PR check summaries and GitHub Security code-scanning
results. Provider access, automatic dependency upgrades, backups, hardware
changes and application deployment are outside this task.

## Administrative roadmap cadence

Owner decision 2026-09-23: roadmap-sync runs only after pushes to main, daily
at 04:17 UTC, and deliberate dispatch on main. It has no issue or PR-event
trigger. Trusted-main checkout/config, token scope, minimal permissions and
serial non-cancelling execution remain intact. The synchronizer itself and
all consistency/pagination/retry limits are unchanged.

Technical CI, CodeQL, dependency review and independent review remain delivery
gates. A demonstrated external Projects availability/visibility fault is recorded
separately as open administration; it does not block an otherwise safe protected
merge or further product work. Code/security/evidence faults still block. Keep
issue/PR content current and report last successful sync plus backlog after
>24h without success; no automatic diagnosis/retry loop. See the full
[delivery contract](DEVELOPMENT_WORKFLOW.md#technical-delivery-and-administrative-synchronization--2026-09-23).

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

The `rust-v2-ci-test` key binds the selected CI profile, runner OS/architecture, Rust 1.97.1, every Rust
Cargo.lock/Cargo.toml hash and the checked-out commit SHA. The single restore
prefix retains the same toolchain and lock/manifest identity. Cargo still checks
source/features and rebuilds affected artifacts. A cache hit never skips a gate,
test, assertion or evidence regeneration; a miss is an ordinary cold build.
Default cache branch scoping applies; the trusted-main Roadmap workflow does not
restore this build cache. No cache quota, billing or larger-runner setting changes.

The OF1, Bronze and Parquet gates emit `OF1_CI_PHASE` records on stderr for compilation, test and
fixture/query subprocesses (Bronze/Parquet labels carry their gate prefix), followed by `OF1_CI_TIMING` with the overall gate outcome.
GitHub Actions also receives a compact step-summary table, including failure
when a later validation fails after successful subprocesses. These are measured
operational durations only: no commands, environment values or dataset contents
are added, and deterministic evidence/stdout contracts remain unchanged.
Unavailable summary output cannot turn a failed gate green or hide its exception.

Compare a cold run and a warm run of the same revision before making a speed
claim. Runtime-heavy tests still run in full and are not accelerated merely by
restoring compiled artifacts. Subphase measurements guide any later parallelism.

### Explicit optimized test profile

The three heavy offline gates select `--profile ci-test` for previously
unoptimized test executions and fixture binaries. Each independent Cargo root
(`of1-range-recorder`, `of1-bronze-decoder`, `of1-parquet-projection`) defines the
same profile: `inherits = "dev"`, `opt-level = 1`, `debug = 1`,
`debug-assertions = true`, `overflow-checks = true`. Unwinding and the existing
Cargo defaults otherwise remain inherited. Limited debug information retains
backtrace support while reducing binary size; optimization can increase cold
compilation cost. See [Cargo's profile contract](https://doc.rust-lang.org/cargo/reference/profiles.html)
(accessed 2026-09-21). Normal dev/release profiles, clippy, Pump and the reducer
remain unchanged. Existing explicitly selected release TLS/monitor fixture
lanes retain their prior settings; this PR does not claim to enable assertions
in those unchanged release lanes.

Each changed crate runs two additional `ci_profile` runtime regressions: a
real `debug_assert!` and an overflowing integer operation must panic. This
validation target intentionally requires dev/ci-test safety settings; it is
not selected by the separate release fixture commands. The full prior test
inventory, corruption/crash/boundary assertions and all network-denial probes
remain. The second six-slot Bronze export and real Rust → Parquet → DuckDB
collection chain still run, using the matching `target/ci-test` workers.

Profile definitions are covered by the pre-fetch manifest hashes and cache
manifest identity. The profile name is explicit in the versioned cache prefix;
cache paths/permissions remain unchanged. Cargo separates custom-profile
artifacts from `target/debug` and `target/release`. Fixture plans and execution
receipts keep hashing the actual binary: a different build may have a different
executable hash, which must never be substituted with a prior hash. Canonical
parity comparisons compile the same candidate source/manifests in both profiles,
use the same retained fixture source and distinguish logical records
and SQL results from intentionally different build/execution bindings. Adding
the profile definition legitimately changes the manifest-bound source identity
relative to older commits; historical records and hashes are never rewritten.

Task scope, measurement receipts and acceptance evidence are tracked in
[#123](https://github.com/daffieeee-arch/Solana-Quant-Bot/issues/123) and outside Git
at `/home/chupa/Solana-project/data-old-faithful-one/governance/ci-rust-test-profile-20260921`.
The controlled local recorded-pipeline comparison (20 unchanged cases, two test
threads, two-CPU scope, cold target directories and already present registry
sources, Rust 1.97.1) measured:

| Phase | Previous test profile | ci-test |
|---|---:|---:|
| Compile | 40.580 s | 68.772 s |
| Execute all 20 cases | 693.938 s | 30.419 s |
| Compile + execute | 734.518 s | 99.191 s |
| Test executable size | 48,190,960 bytes | 34,101,272 bytes |

The measured net reduction is 86.5%, including 28.2 seconds of additional
compilation. Cargo artifact receipts report optimization 0 → 1, debug info 2 → 1,
and assertions/overflow checks true in both profiles. This does not isolate
executable hashing as the cause; no integrity routine was changed.

The earlier complete hosted jobs took 2,251/2,469 seconds on PRs and 1,455 seconds
on main; Node took only 49–75 seconds and OF1/Bronze/Parquet dominated. Those are
operational observations across different runners/cache states, not a controlled
speed guarantee. The PR and main run links and actual phase timings for this
delivery are recorded at #123; their totals include compilation and cache work.

Existing PR/main gates, security workflows and the 45-minute job bound remain
required. No fixture repeat, assertion, test case or gate is removed. Broad
parallelism, new cache directories and changed-file test selection are separate
future decisions.

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

## Bounded regression setup for the #130 CI blockers

The namespace collision regression uses the existing `test-support` feature to
run a closed probe against real System V semaphores. One full fingerprint is the
ordinary effective-UID/temporary-namespace SHA-256; the other is a synthetic
fingerprint differing only in its last byte. Both therefore deterministically
use the unchanged primary-key function and the same registry, with different
full identities. This is collision handling evidence, not a discovered SHA-256
collision. The private fingerprint acquisition path never exposes a public key
or digest override or returns test locks. Production namespace hashing is
unchanged. Two temporary namespaces run concurrently; kernel values, exclusivity,
reacquisition and cleanup are checked. Exclusive registry reservation prevents
cleanup of an unrelated registry.

The outbound seccomp test builds the unchanged real filter/launcher in a bounded
setup hook (15 seconds), outside the unchanged 5-second assertion budget.
Compilation has a 10-second bound and a 256-KiB combined output cap; failure kills
only the newly created compiler process group, including compiler children.
One filtered Node process performs all three existing loopback/listener,
TCP-connect and UDP-send checks, with a 1-second watchdog and a 3-second parent
kill bound. Both denials must explicitly be `EPERM`; other errors and all
timeouts fail. `SECCOMP_PHASE` records distinguish generation, compilation,
process startup and the individual probes. No global timeout, security filter,
workflow, required gate or Rust CI profile changes.

The preserved #130 attempt 1 failed during the old 200,000-candidate collision
search before acquiring locks. Attempt 2 timed out the old seccomp test at
5.935 seconds (the same test took 0.960 seconds in attempt 1). The bounded local
baseline measured 1 ms filter generation, 66 ms compilation, 23–27 ms per Node
startup and 176 ms total. It did not reproduce the historical runner delay;
that run has no phase timings, so contention or a specific slow phase remains
unproven. New timing evidence is separate from those unchanged failed receipts.
The task evidence is outside Git under
`/home/chupa/Solana-project/data-old-faithful-one/governance/ci-reliability-130-20260922`.
