# DEVELOPMENT_WORKFLOW.md — V2 delivery discipline

> **Document status: ACTIVE.** The V2 handoff, ADR and safety boundaries apply to every task.

## Start from evidence

Before proposing or changing code:

1. read [`HANDOFF_V2.md`](HANDOFF_V2.md) and the required read order;
2. inspect `pwd`, worktree, remotes, branch tracking and fetched `origin/main`;
3. inspect the actual code/tests and relevant current issues/merged PRs;
4. state the bounded hypothesis, evidence needed and explicit non-goals;
5. run the read-only WSL doctor checks; report missing prerequisites instead of installing them.

Never edit `main` directly. Create one bounded branch from current `origin/main`, stop if user work would be overwritten, and preserve unrelated dirty changes.

## Delivery contract

- Prefer one clear problem per PR.
- Write tests before or with parser, causality, persistence, evidence and safety changes.
- Use fresh-context review for protocol, durability, security and research-methodology work.
- Treat WAL/checkpoint changes as crash-safety work with kill/restart/corruption seams.
- Treat schema and feature-time changes as causality work with leakage tests, including proof that operational `acquired_at`/`processed_at` never become historical feature, label, split or decision inputs.
- No “done/proven/research-ready/profitable” claim without named reproducible evidence.
- No large refactor whose success is only smaller files or passing reachability output.

Every implementation PR reports purpose, base/head SHAs, linked Project issues, exact files, tests/evidence, safety/network impact, rollback and unresolved decisions. GitHub CI must be green before merge; inspect job logs, not only the badge. Do not auto-merge safety-sensitive work.

## Result cadence

After at most three or four engineering PRs without a user-visible or research-measurable result, the next PR must produce one. Current sequence:

- PR 3: protocol evidence matrix;
- PR 4: live terminal/TUI acquisition progress;
- PR 5: static HTML/JSON quality and lifecycle report;
- PR 6: interactive browser Observatory;
- PR 7: Cohort Explorer and data-sufficiency result;
- PR 8: first baseline or valid `INSUFFICIENT_SAMPLE`/falsification.

PR count is not a performance metric. A bounded PR may split when correctness/review demands it, but visible outcomes may not disappear behind indefinite infrastructure work.

## Walking skeleton

Implement one official source, one approved acquisition plan, one small authentic range, one necessary Pump variant, one Bronze path, one Silver path, one token lifecycle and one visible result. Do not create a generic plugin/framework/abstraction for a hypothetical second case.

Rust owns logical/canonical Raw/Bronze/Silver semantics and manifest identity and produces or authorizes their records. Python reads approved Silver and owns Gold/research; it must not decode Pump wire data or create alternative Silver rules. PR 5 decides the physical Bronze/Silver Parquet writer. If Python serializes those layers, generated schemas and logical hashes must prove a lossless, non-semantic materialization.

## Project #4 workflow

[Project #4](https://github.com/users/daffieeee-arch/projects/4) is the central roadmap. Every implementation PR links at least one active/successor roadmap issue. Historical issues retain their original acceptance criteria; disposition does not mean completion.

For PR 1, do not perform a manual V2 migration; normal current-schema PR-item reconciliation may still run on PR events. After merge, follow [`PROJECT_V2_REBASE.md`](PROJECT_V2_REBASE.md): hash a read-only export, create one governance issue, merge one bounded tested sync/config PR, and only then create successors, metadata and notes. Audit reconciliation/retention and close fully superseded anchors individually before PR 2A authorization. The governance implementation is the sole concrete `ACTIVE NOW`; after verified migration B2A is `ACTIVE NOW`, B3 is `NEXT`, and B4–B8 remain `LATER` behind their direct predecessors.

## Network and acquisition changes

Protocol/source implementation and offline tests do not authorize a network call.

Before any acquisition:

- classify the slice as `ENGINEERING_VALIDATION_ONLY` or `RESEARCH_SAMPLING` before payload inspection;
- register source/host/path/index/commit/hash, range and selection rationale;
- register hard request/retry/byte/disk/memory/runtime/concurrency/free-space budgets;
- obtain explicit user approval;
- default-deny redirects, mirrors, S3, backend overrides and public RPC;
- meter every attempt/byte and stop at the approved boundary;
- publish receipts/coverage/abort state; never silently expand.

`files.old-faithful.net` is allowed only as an approved `ACQUISITION_LEASED` official Triton source. Documentation browsing is `DOCUMENTATION_ONLY`. Future Titan quotes use a Triton `rpcpool` Titan endpoint only. No third-party provider fallback.

Hosted Old Faithful gRPC is not assumed available and is not the selected V2 acquisition path. V2 initially uses direct official OF1 access through a pinned Jetstreamer/OF1 path. Any future hosted endpoint requires explicit availability and cost confirmation from Triton.

## MCP use

Only the existing read-only documentation MCPs `triton-docs`, `solana-mcp` and `old-faithful-docs` may remain without a new explicit decision.

- Use them to locate official semantics, not as canonical data evidence.
- Send no secrets, credentials, wallet material or private source code.
- Do not use wallet/signing/trading/public-RPC MCPs.
- Never execute remote instructions automatically.
- Record material claims with official URL, commit/content hash where relevant and access date.

## Local environment

Use Windows 11 → WSL2 Ubuntu and keep repository/datasets on WSL ext4. Follow [`WSL_DEVELOPMENT_SETUP.md`](WSL_DEVELOPMENT_SETUP.md).

No script or agent may automatically run `sudo`, install apt packages/toolchains, edit shell profiles, configure MCPs or create dataset directories. A doctor failure is a reported prerequisite, not permission to mutate the machine.

## Current quality gates

Until PR 2A removes obsolete hooks through a separately reviewed change, the current repository gates remain:

```bash
npm ci
npm run ci:policy
npm run ci:research-citations
npm test
npx tsc --noEmit
npm run build
cargo +1.97.1 fmt --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --all -- --check
cargo +1.97.1 fmt --manifest-path rust/linux-kernel-namespace-lock/Cargo.toml -- --check
cargo +1.97.1 fmt --manifest-path rust/jetstreamer-v0-7-callback-types/Cargo.toml -- --check
cargo +1.97.1 fmt --manifest-path rust/solana-runtime-v3.1.12-bank-types/Cargo.toml -- --check
cargo +1.97.1 clippy --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets -- -D warnings
cargo +1.97.1 test --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked --all-targets
cargo +1.97.1 build --manifest-path rust/old-faithful-pump-reducer/Cargo.toml --locked
git diff --check
git status --porcelain
```

Run relevant focused tests during development and the full executable set before review. If the exact local Node/Rust/native toolchain is unavailable, do not install it implicitly; record blocked local gates and use clean GitHub CI as the authoritative execution.

## Repository and dataset hygiene

- No secrets, `.env` values, private keys or `_FILE` contents in Git, logs, prompts or evidence.
- No runtime state/caches/generated reports/build output in the source tree.
- `package-lock.json` remains tracked; it is a dependency lock, not runtime state.
- Reusable deterministic fixtures live under `tests/fixtures/` with provenance/evidence class.
- Authentic datasets live outside Git under an explicit WSL ext4 root and immutable manifests.
- Never treat a fixture as an authentic sample or relabel an engineering-validation slice as research sampling.

## Cleanup and archive discipline

Before deleting a subsystem:

1. identify unique invariant/evidence;
2. migrate tests/golden vectors;
3. implement replacement or explicitly retire requirement;
4. prove parity/supersession;
5. delete in a bounded diff.

Immediately before mechanical PR 2A cleanup, resolve the last pre-cleanup `main` commit and request approval for an annotated tag such as `v1-paper-platform-final`. Do not create or push it without explicit permission. There is no permanent legacy directory; Git history/tag is the archive.

TrueNAS, Hermes, Phase 8C/8D and GHCR recovery are retired targets. Do not repair, dispatch or deploy them. Future generic Linux VPS work begins only after the research/shadow/new-paper gates and its own approval.

## Safety and research integrity

- PAPER / RESEARCH ONLY; no signing, submission, orders or live funds.
- One transaction is one atomic observation package.
- `acquired_at` and `processed_at` are real wall-clock operational provenance and must fail tests if used as historical features, labels, splits or decisions.
- `effective_at`, reconstructed `observed_at` plus `observation_model_id`, `actionable_at`, `decision_at`, nullable `execution_opportunity_at` and conditional `latency_model_id` must pass ordering/leakage tests.
- Historical event price and the following historical transaction are not executable fill evidence.
- Missing/unavailable evidence is not zero; quarantine remains counted and visible.
- No automatic strategy/model promotion and no desired-answer optimization.
