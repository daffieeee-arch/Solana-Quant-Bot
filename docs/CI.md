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
- exactly four tracked workflow files: canonical CI, no-push image verification, manual publish, and read-only existing-digest recovery; only `.github/workflows/ci.yml` is the canonical general quality workflow described in this section;
- exactly one `quality` job on `ubuntu-24.04`; self-hosted runners, extra jobs, job containers, and services are forbidden;
- top-level permissions are exactly `contents: read`;
- job-level permission overrides are forbidden;
- GitHub's automatic `GITHUB_TOKEN` is therefore read-only;
- checkout credentials are not persisted;
- exactly one `actions/checkout` step is allowed, and it must explicitly set `persist-credentials: false`;
- only the reviewed `actions/checkout@v7.0.1` and `actions/setup-node@v7.0.0` actions are allowed;
- the Rust toolchain is installed by the exact canonical `rustup` command and pinned to `1.97.1` with only `clippy` and `rustfmt` components;
- no repository or production secrets are referenced;
- `MODE=paper`, `TRITON_LIVE_ENABLED=false`, and `ENTRY_SHADOW_MODE=true` are defined once at workflow level;
- safety variables may not be overridden by a job, step, container, inline map, quoted key, or another nested mapping;
- the ordered action inputs and `run` commands, including every Rust gate, must exactly match the reviewed validation-only workflow;
- a separate offline `npm run ci:research-citations` step is mandatory after repository policy and before tests; comments, renaming, attached-hash command drift, omission, or moving it outside the canonical job fail policy;
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

1. the tracked workflow file set must be exactly `.github/workflows/ci.yml`, `phase8d-images-verify.yml`, `phase8d-images-publish.yml`, and `phase8d-images-recover.yml`; the canonical structure checks below apply specifically to `ci.yml`;
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
- Rust toolchain drift, removal/replacement of any Rust gate, loss of `--locked`/`--all-targets`, or weakening of `-D warnings`;
- citation-step omission, comment-only substitution, name/command drift, mutable GitHub default-branch evidence URLs, missing claim/source IDs, scratch/hash drift, stale activation promotion, or authorization escalation;
- unsupported YAML syntax failing closed.

The complete suite includes adversarial repository-policy, zero-cost, Pump lifecycle/research, Phase-3 Bronze, transport-policy, forensic-generator, and immutable-manifest coverage. Exact totals are reported from the fresh CI run rather than kept as a stale Phase-specific constant here.

## Checks

The backend build runs `verify:research-transport` after TypeScript compilation. A TypeScript-AST policy follows every statically discoverable local import transitively (including helpers outside `dist/research`), allows only explicit file/crypto/base58 modules, and rejects non-literal loads plus direct or constant-foldable capability reflection using lexical TypeScript-symbol binding rather than name-global substitution. Non-constant reflection is contained by the runtime boundary rather than claimed as statically decidable: ESM/CJS guards block HTTP/1, HTTP/2, HTTPS, net/TLS, UDP/DNS, browser transports, process loaders and arbitrary native addons; `process.getBuiltinModule` is all-deny. The built v1 CLI and Bronze import run under those guards and a generated Linux seccomp cBPF filter. The gate compiles a temporary hardened launcher from tracked C source, installs `NO_NEW_PRIVS` plus the filter, and requires a socket self-test to receive `EPERM`; a missing C compiler, unsupported architecture/kernel, compile failure, or ineffective filter fails the build. It does not depend on the host util-linux `setpriv` version.

Phase 8C adds two further build gates: `verify:phase8c-contracts` validates the unapplied image/dataset/TrueNAS contracts, and `verify:cockpit-runtime` follows the compiled cockpit graph transitively, runs the real cockpit-only entrypoint under an outbound-deny seccomp profile, proves exactly one explicit listener, no runtime writes, graceful shutdown and listener cleanup. These remain validation-only and perform no deployment or live Grafana operation.

1. `npm ci` from the committed lockfile;
2. `npm run ci:policy`;
3. `npm run ci:research-citations` with no network access;
4. targeted policy, citation, zero-cost, Pump replay, vertical-slice, and TP/SL lifecycle tests;
5. complete Vitest suite;
6. `npx tsc --noEmit`;
7. backend and frontend build;
8. exact Rust `1.97.1` toolchain installation with `clippy` and `rustfmt`;
9. reducer `cargo fmt --check` plus separate format checks for the Linux namespace-lock, Jetstreamer callback snapshot, and Solana runtime snapshot crates;
10. locked all-target reducer clippy with `-D warnings`;
11. locked all-target reducer tests, including all Linux System V writer-exclusion and crash/recovery integration tests;
12. locked reducer build;
13. committed patch whitespace validation:
   - pull requests use the explicit GitHub base and head SHAs;
   - pushes inspect the committed HEAD patch;
14. verification that checks did not modify tracked files.

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
npm run ci:research-citations
npx vitest run \
  tests/ci-policy.test.ts \
  tests/ci-research-citations.test.ts \
  tests/zero-cost.test.ts \
  tests/pump-replay.test.ts \
  tests/pump-vertical-slice.test.ts \
  tests/lifecycle-tp-sl.test.ts
npm test
npx tsc --noEmit
npm run build
npm run verify:phase8d1-supply-chain
rustup toolchain install 1.97.1 --profile minimal --component clippy,rustfmt
cargo +1.97.1 fmt --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --all -- --check
cargo +1.97.1 fmt --manifest-path rust/linux-kernel-namespace-lock/Cargo.toml -- --check
cargo +1.97.1 fmt --manifest-path rust/jetstreamer-v0-7-callback-types/Cargo.toml -- --check
cargo +1.97.1 fmt --manifest-path rust/solana-runtime-v3.1.12-bank-types/Cargo.toml -- --check
cargo +1.97.1 clippy --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets -- -D warnings
cargo +1.97.1 test --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets
cargo +1.97.1 build --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked
git diff --check origin/main...HEAD
git status --porcelain
git ls-files -ci --exclude-standard
```

## Changes to CI

Treat workflow and policy changes as security-sensitive. Keep the parser tests adversarial, inspect every action and permission scope, and require fresh-context review before merge. Do not add deployment behavior to this workflow without a separate approved design.

## Phase 8D1 remote image workflows

`phase8d-images-verify.yml` is no-push PR/reusable verification with only `contents: read`. `phase8d-images-publish.yml` is manual-only and has already produced two immutable versions under a durable HOLD. `phase8d-images-recover.yml` is a separate not-yet-dispatched read-only recovery with only `contents/actions/packages: read`; it cannot build, push, delete, overwrite or alter package settings. Every external action is full-SHA pinned.
