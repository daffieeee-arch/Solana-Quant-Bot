# Hermes review response — PR #1, round 3

## Status

Hermes and a fresh-context reviewer returned `REQUEST_CHANGES` on head `ad95b32d471d8f4355ebf6425a587695bcbfb735`.

The committed workflow itself, repository cleanup, documentation, and ordinary quality gates were accepted. The remaining blockers were policy differentials: the custom YAML lexer mishandled attached `#` characters, non-canonical block-scalar headers could be approximated, secret/environment expressions were only partly detected, and the validator did not constrain the full workflow/job/runner/command surface or a second workflow file.

This response describes the fixes in the current branch head. Reviewers must verify the actual current SHA rather than treating this document as proof.

## Root cause

Two design assumptions were insufficient:

1. a hand-written YAML subset parser was being treated as semantically equivalent to GitHub Actions YAML;
2. a blacklist of selected unsafe fragments was being treated as proof that an otherwise open-ended workflow remained validation-only.

Both assumptions failed under valid YAML and GitHub Actions syntax.

## Fixes

### Standards-compliant YAML parsing

- Added patched `yaml@2.9.0` as a direct dev/CI dependency.
- Replaced the custom lexer/parser with a strict YAML 1.2 adapter.
- Duplicate mapping keys fail parsing.
- YAML comments now follow YAML semantics; an attached `#` remains part of a plain scalar.
- Anchors, aliases, merge keys, explicit tags, document directives/markers, and block/folded scalars are explicitly rejected for the canonical workflow.
- Removed the obsolete `scripts/lib/strict-yaml-flow.mjs` implementation.

The dependency is used only by CI/repository policy code. Trading and production runtime source do not import it.

### Exact validation-only contract

The policy now requires:

- exactly one tracked workflow file: `.github/workflows/ci.yml`;
- the exact reviewed trigger and concurrency configuration;
- top-level `contents: read` and no nested permission override;
- the exact four top-level zero-cost environment values and no narrower safety-key occurrence;
- exactly one `quality` job on `ubuntu-24.04`;
- no self-hosted runner, extra job, reusable job, container, or service;
- exactly the reviewed ordered steps, actions, action inputs, conditions, and run commands;
- exactly one checkout with `persist-credentials: false`;
- no dot/index/whole-context secret reference;
- no `GITHUB_ENV`, `${{ github.env }}`, safety-flag command reference, arbitrary command, or deployment command.

Exact structural validation is the primary boundary. Targeted scanners remain as defense in depth and provide specific diagnostics.

## Regression tests

Six adversarial policy tests were added. They cover:

1. attached-hash YAML command differentials and block-scalar header variants;
2. bracket and whole-context secrets;
3. `github.env` and expression-based live unlocks;
4. self-hosted runners, extra jobs, containers, and services;
5. arbitrary commands and action-input drift;
6. trigger drift and a second tracked workflow.

TDD evidence:

- before implementation: 6 new failures, 12 existing policy tests green;
- after implementation: 18/18 policy tests green.

Measured suite totals:

- targeted critical suite: 40/40 in 5 files;
- complete suite: 610/610 in 62 files.

## Intentional dependency diff

`package.json` and `package-lock.json` now intentionally add only `yaml@2.9.0` as a devDependency. Version 2.9.0 is newer than the patched threshold for `GHSA-48c2-rrv3-qjmp` (`>=2.8.3`). This is CI tooling, not production runtime behavior.

## Scope and safety

- No existing `src/` file changed.
- No frontend file changed.
- No existing non-policy test changed.
- No trading, provider, parser, ledger, MarketIdentity, accounting, or deployment behavior changed.
- No merge, deployment, app-start, Triton-live, backfill, or ClickHouse mutation was performed.

## Required independent review

A fresh-context reviewer must still:

1. review the full `origin/main...HEAD` diff;
2. reproduce all original and round-3 adversarial mutations in a disposable exact-head checkout;
3. verify the exact workflow file set and canonical AST boundary;
4. run policy, targeted/full tests, typecheck, build, explicit diff-check, audit, and clean-tree checks;
5. inspect the GitHub Actions run for the pushed head where authentication permits;
6. return one explicit verdict without modifying the reviewed tree.
