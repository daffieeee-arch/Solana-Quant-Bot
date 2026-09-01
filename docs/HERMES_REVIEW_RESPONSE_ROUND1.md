# HERMES_REVIEW_RESPONSE_ROUND1.md

> **Document status: HISTORICAL.** Legacy review evidence only. Hermes is not part of the active V2 architecture; this is not a runbook or authorization. See [`HANDOFF_V2.md`](HANDOFF_V2.md).

## Context

Hermes and fresh-context reviewer returned `REQUEST_CHANGES` on PR #1. This document maps every blocking and documentation finding to the follow-up change. Review the latest `chore/repo-alignment-ci` head, not the original `7d1f82f` head.

No runtime trading/provider/parser/ledger code was changed. No merge, deployment, app start, Triton activation, backfill restart, or ClickHouse mutation was performed.

## Finding 1 — repository cleanup and policy false PASS

### Review evidence

The first PR head still tracked:

- `data-stream2/`
- `data-stream3/`
- root `data/` runtime/deployment artifacts
- ignored legacy scripts `scripts/gen-inline-yaml.py` and `scripts/reinstall-bot.py`

The original regex did not match `data-stream2`/`data-stream3`, and the policy did not generically detect tracked ignored files.

### Follow-up

The latest branch removes from the current tree, while preserving published history:

- `data-stream2/`
- `data-stream3/`
- all root `data/` files (runtime state, reports, locks, generated/deployment scratch; no test fixture dependency)
- `scripts/gen-inline-yaml.py`
- `scripts/reinstall-bot.py`

`.gitignore` now ignores root `data/`, all `data-bot*`, and all `data-stream*` paths.

The policy now:

- runs `git ls-files -ci --exclude-standard` and fails for every tracked ignored file;
- uses generic root patterns for `data`, `data-bot[^/]*`, and `data-stream[^/]*`;
- explicitly rejects the two legacy destructive helpers;
- requires the new policy test file.

No history rewrite was performed. Reusable deterministic samples remain under `tests/fixtures/`.

## Finding 2 — MarketIdentity enforcement overclaim

### Review evidence

The full identity/decimals/freshness evaluation is shadow-only. `scanner.ts` emits `WOULD_ACCEPT`/`WOULD_REJECT`, while only the exact `gx:<mint>` identity has a hard gate before the legacy entry flow.

### Follow-up

README, AGENTS, HANDOFF, CURRENT_STATE, ARCHITECTURE, DECISIONS, KNOWN_ISSUES, and workflow documentation now state explicitly:

- complete MarketIdentity contract is fail-closed **in shadow evaluation**;
- shadow rejection does not generally block the legacy entry flow;
- exact `gx:<mint>` is the currently enforced hard identity rejection;
- broader enforcement remains off and requires live shadow evidence plus explicit approval.

## Finding 3 — zero-cost workflow-policy bypass

### Review evidence

The original policy relied on raw substring checks and could be bypassed with:

```yaml
# TRITON_LIVE_ENABLED: 'false'
TRITON_LIVE_ENABLED: 'true'
```

### Follow-up

The policy now exports a comment-aware YAML-scalar reader and validates active assignments. It requires exactly one active value for:

- `contents: read`
- `persist-credentials: false`
- `CI: true`
- `MODE: paper`
- `TRITON_LIVE_ENABLED: false`
- `ENTRY_SHADOW_MODE: true`

It rejects quoted/unquoted live values, duplicate assignments, `secrets.*`, and deployment fragments.

`tests/ci-policy.test.ts` adds four cases:

1. safe workflow accepted;
2. reviewer's comment bypass rejected;
3. quoted and unquoted `true` rejected;
4. duplicate active assignments rejected.

The targeted CI step includes this test file.

## Documentation corrections

- TrueNAS image is described as configured/deployed, not running.
- Last read-only observation is recorded as `STOPPED`, `active_containers=0`.
- CI documentation states that GitHub supplies an automatic read-only `GITHUB_TOKEN`; no repository or production secrets are consumed and checkout credentials are not persisted.
- “lockfiles” is replaced with “runtime lock files”; `package-lock.json` remains required.
- Dependency audit findings are documented as existing follow-up work, not fixed automatically.

## Re-review requirements

Hermes should:

1. fetch/switch to the latest `chore/repo-alignment-ci` head;
2. inspect `origin/main...HEAD` and confirm no `src/`, frontend, or existing functional test changes beyond the new policy test;
3. run:

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
git diff --check
git status --porcelain
git ls-files -ci --exclude-standard
```

4. verify the final command emits no tracked ignored files;
5. inspect the latest GitHub Actions run and logs;
6. use a fresh-context reviewer;
7. return `APPROVE`, `REQUEST_CHANGES`, or `HOLD`.

Do not merge or deploy during review.
