# GitHub Project #4 operations

> **Document status: ACTIVE.** This is the operations boundary for [Solana Quant Platform — Roadmap & Cockpit, Project #4](https://github.com/users/daffieeee-arch/projects/4). The V2 data/issue migration is specified separately in [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md).

## Current state during PR 1

Project #4 exists and is the central delivery roadmap. It is reconciled by:

- `.github/workflows/roadmap-sync.yml`;
- `scripts/github-projects/sync.mjs`;
- `roadmap/project-config.json`;
- `tests/github-projects-sync.test.ts`.

The current configuration and issue `roadmap-meta` still express the historical phase/order. PR 1 deliberately does **not** change Project fields/views, issue bodies or synchronization code. Normal current-schema reconciliation of PR #69 on PR events is expected; it is not a V2 migration. After PR 1 merges, execute the reviewed governance issue → bounded sync/config PR → verified migration sequence before PR 2A.

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

Current lifecycle behavior is not suitable for the V2 cockpit: it enumerates all open/closed issues and all open/closed/merged PRs, adds a missing historical item, and unconditionally unarchives any archived repository item. The G0 extension must apply eligibility before both add and unarchive, preserve closed archived items, and reactivate only a genuinely reopened issue/PR.

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

1. verify merged PR #69 SHA/CI, then export/hash all current Project-linked repository issues/PRs and complete Project state read-only, retaining an exact #27–#63 migration subset;
2. create one governance issue G0 using only metadata understood by current `main`;
3. create and merge one bounded Roadmap Sync/config PR before creating successors or V2 metadata;
4. in that PR, add/test `V2 Phase`, `V2 Disposition`, expanded Evidence, V2 views, replacement Project description/README and lifecycle-aware retention;
5. preserve existing `Phase`, old metadata, option IDs for unchanged semantics and original acceptance criteria;
6. only after that PR merges, create E0–E7/B2A–B8 and append V2 metadata/cutover notes in bounded batches;
7. reconcile and audit all counts/options/IDs/states/links/views/Evidence/retention outcomes;
8. record actual successor numbers and migration evidence in the repository;
9. close fully covered `SPLIT`/`SUPERSEDED` anchors only through a later individual verified pass, then authorize PR 2A.

`V2 Disposition` values are `ACTIVE NOW`, `NEXT`, `LATER`, `SPLIT`, `SUPERSEDED` and `RETIRED`. Disposition is not completion. #56 remains Done; #62 stays open; #63 stays open during migration and closes individually only after verified E0 cutover. Do not bulk-close.

During migration G0 is the single concrete `ACTIVE NOW`. After verified migration B2A becomes `ACTIVE NOW`, B3 alone is `NEXT`, and B4–B8 remain `LATER` until their direct predecessor is accepted. Program/epic routing cannot create a second concrete delivery head.

The Evidence field becomes, in order: `Not Applicable`, `Unproven`, `Fixture`, `Operationally Verified`, `Engineering Validation`, `Research Candidate`, `Research Ready`, `Shadow`, `Paper Proven`, `Live Proven`. Preserve option IDs for the six existing semantically equal values. Programs/epics use `Not Applicable`; operational governance is `Unproven` until verified then `Operationally Verified`; #56 is `Operationally Verified`; research advances only through its named evidence gates.

The config PR replaces the Project short description and README with the approved E0 data-first text and a link to [`../HANDOFF_V2.md`](../HANDOFF_V2.md), explicitly naming edge discovery or falsification, Observatory before Workstation, Triton-only and no profitability assumption.

Retention keeps open V2 issues/open PRs active, archives merged pre-V2 PR items #1–#68 after export/audit, and requires an explicit reviewed `closed_item_retention_days` before age-based archival of future closed/merged items. Archived closed items remain archived unless actually reopened; no item or GitHub history is deleted. #56 is a pinned continuing control. Tests must cover eligibility before add/unarchive, grace periods and reopen behavior.

The initial migration does not close anchors. After all mappings/notes/fields/views pass, fully covered `SPLIT`/`SUPERSEDED` anchors close individually as `not planned / superseded`; standalone `LATER` requirements remain open. #56 stays Done, #62 stays open, and #63 closes individually only after verified E0 cutover.

The exact #27–#63 ledger, successor catalog, V2 phases, filters, acceptance checks and rollback are in [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md).

## Offline validation

Configuration validation uses no token/network:

```bash
node scripts/github-projects/sync.mjs --dry-run
npm test -- --run tests/github-projects-sync.test.ts
```

Do not manually dispatch a V2 reconciliation during PR 1. Ordinary PR-event reconciliation under current trusted-main configuration is expected and must be allowed to finish. After merge, V2 mutation requires the reviewed export/preflight, merged G0 sync/config support and protected Actions secret path; do not place a token on a command line.

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
