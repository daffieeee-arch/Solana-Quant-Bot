# GitHub Project #4 operations

> **Document status: ACTIVE.** This is the operations boundary for [Solana Quant Platform — Roadmap & Cockpit, Project #4](https://github.com/users/daffieeee-arch/projects/4). The V2 data/issue migration is specified separately in [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md).

## Current state during PR 1

Project #4 exists and is the central delivery roadmap. It is reconciled by:

- `.github/workflows/roadmap-sync.yml`;
- `scripts/github-projects/sync.mjs`;
- `roadmap/project-config.json`;
- `tests/github-projects-sync.test.ts`.

The current configuration and issue `roadmap-meta` still express the historical phase/order. PR 1 deliberately does **not** change Project fields/views, issue bodies or synchronization code. After PR 1 merges, execute the reviewed V2 migration before PR 2A.

## Security boundary

The user-owned Project requires a protected `PROJECT_TOKEN`; GitHub's repository-scoped `GITHUB_TOKEN` is insufficient. Never place the token in Git, `.env`, shell history, YAML, issue/PR text, logs, prompts or MCP queries.

The workflow receives `pull_request_target` events but must always execute trusted default-branch code. Never:

- check out a PR head;
- run scripts or artifacts from a PR branch;
- interpolate untrusted title/body into a shell command;
- print request headers/token/API dumps.

Rotate/revoke the token if exposed. Workflow changes require independent security review.

## Current reconciliation semantics

The synchronizer is full-state/idempotent and runs on relevant issue/PR events, pushes to `main`, manual dispatch and scheduled repair. It finds/links the user Project, ensures configured fields/views, adds repository issues/PRs and maps hidden `roadmap-meta` plus GitHub state into Project values.

Current metadata keys are:

| Key | Field |
|---|---|
| `workflow` | Status |
| `priority` | Priority |
| `area` | Area |
| `type` | Work Type |
| `phase` | historical Phase |
| `risk` | Risk |
| `evidence` | Evidence |
| `effort` | Effort |
| `startDate` | Start date |
| `targetDate` | Target date |

Unknown keys and invalid options fail closed. GitHub issue/PR state still determines Done/Cancelled/In Review semantics. Manual edits to synchronized fields may be overwritten.

The current synchronizer does not recognize `v2Phase` or `v2Disposition`. Do not put those keys into issue metadata until a tested default-branch extension accepts them.

## V2 migration boundary

The post-merge migration must:

1. export and hash current issue bodies plus Project items/fields/views read-only;
2. preserve existing `Phase`/metadata and original acceptance criteria;
3. create V2 successor epics/issues first;
4. add separate `V2 Phase` and `V2 Disposition` fields;
5. extend/test sync/config before repository-driven V2 metadata, or obtain explicit approval for a temporary manual ledger;
6. append non-destructive cutover notes in bounded batches;
7. create/verify Now, Next, Data, Observatory, Research, Later and Retired views;
8. reconcile and audit counts/options/states/links;
9. only then retire obsolete views.

`V2 Disposition` values are `ACTIVE NOW`, `NEXT`, `LATER`, `SPLIT`, `SUPERSEDED` and `RETIRED`. Disposition is not completion. #56 remains Done; #62 and #63 remain open. Do not bulk-close.

The exact #27–#63 ledger, successor catalog, V2 phases, filters, acceptance checks and rollback are in [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md).

## Offline validation

Configuration validation uses no token/network:

```bash
node scripts/github-projects/sync.mjs --dry-run
npm test -- --run tests/github-projects-sync.test.ts
```

Do not run a live reconciliation during PR 1. After merge, live mutation requires the reviewed export/preflight and protected Actions secret path; do not place a token on a command line.

## Migration stop conditions

Stop without continuing batches on:

- issue/item count mismatch;
- missing/duplicate successor;
- unexpected issue state or close change;
- original acceptance-criteria loss;
- single-select option-ID/value drift;
- unsupported metadata key;
- view filter warning or unintended result set;
- workflow/security-boundary drift.

Use the hashed preflight export and original issue bodies for explicit rollback. Never “repair” mismatches by overwriting history.

## Project truth boundary

Project #4 is the delivery cockpit, not evidence by itself. Tests, code, manifests, source hashes, issue/PR history and accepted review determine whether a field such as Research Ready or Paper Proven is justified. A Project status cannot turn fixture/synthetic data into authentic research evidence.
