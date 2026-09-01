# GitHub Project #4 operations

> **Document status: ACTIVE.** This is the operations boundary for [Solana Quant Platform — Roadmap & Cockpit, Project #4](https://github.com/users/daffieeee-arch/projects/4). The V2 data/issue migration is specified separately in [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md).

## Current state during G0

Project #4 exists and is the central delivery roadmap. It is reconciled by:

- `.github/workflows/roadmap-sync.yml`;
- `scripts/github-projects/sync.mjs`;
- `roadmap/project-config.json`;
- `tests/github-projects-sync.test.ts`.

PR #69 was squash-merged as `05c3d885943c2319d6317b43546daca4b5918074`. Its post-merge main CI passed. The main-push Roadmap Sync run was superseded by the issue-state event caused by that merge; the successor ordinary reconciliation passed. The exact run identities and immutable pre-G0 export receipt are recorded in [`PROJECT_V2_G0_PREFLIGHT.md`](PROJECT_V2_G0_PREFLIGHT.md).

The captured pre-G0 `main` configuration and issue `roadmap-meta` express the historical phase/order. Governance issue [#70](https://github.com/daffieeee-arch/solana-paper-scanner/issues/70) is G0 and is the single concrete delivery focus. The bounded G0 PR may implement and test sync/config support, but its branch must not perform a live V2 Project migration. E0–E7/B2A–B8, V2 notes/metadata on issues #27–#63, live archival and PR 2A remain blocked while that PR is under review.

Merging is an activation boundary: the existing push-to-`main` trigger runs trusted-default-branch reconciliation. The explicit merge GO must therefore also authorize the resulting schema/view/retention reconciliation. Do not merge until the retention value and every preflight stop condition are explicitly resolved.

The preflight also found issue #63 already `CLOSED` / `COMPLETED` after the PR #69 merge, contrary to the accepted migration plan. G0 does not repair, reopen or otherwise mutate that state. It remains an explicit stop condition before live migration.

## Security boundary

The user-owned Project requires a protected `PROJECT_TOKEN`; GitHub's repository-scoped `GITHUB_TOKEN` is insufficient. Never place the token in Git, `.env`, shell history, YAML, issue/PR text, logs, prompts or MCP queries.

The workflow receives `pull_request_target` events but must always execute trusted default-branch code. A pull-request branch may run offline validation but may not receive `PROJECT_TOKEN` or mutate the user Project. Mutation is allowed only through the reviewed workflow running code and config from the trusted default branch. Never:

- check out a PR head;
- run scripts or artifacts from a PR branch;
- interpolate untrusted title/body into a shell command;
- print request headers/token/API dumps.

Rotate/revoke the token if exposed. Workflow changes require independent security review.

## Pre-G0 main reconciliation semantics

At the captured baseline, the synchronizer is full-state/idempotent and runs on relevant issue/PR events, pushes to `main`, manual dispatch and scheduled repair. It finds/links the user Project, ensures configured fields/views, adds repository issues/PRs and maps hidden `roadmap-meta` plus GitHub state into Project values.

That baseline lifecycle behavior is not suitable for the V2 cockpit: it enumerates all open/closed issues and all open/closed/merged PRs, adds a missing historical item, and unconditionally unarchives any archived repository item. The G0 extension applies eligibility before both add and unarchive, preserves closed archived items, and reactivates only an item backed by an actual reopen transition.

Pre-G0 metadata keys are:

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

The pre-G0 synchronizer does not recognize `v2Phase` or `v2Disposition`. The G0 branch adds tested support, but do not put those keys into issue metadata until that extension is merged to the default branch and live migration is separately authorized.

## V2 migration boundary

The controlled migration sequence is:

1. **Complete:** verify merged PR #69 SHA/CI and export/hash all current Project-linked repository issues/PRs plus complete Project state read-only, retaining an exact #27–#63 subset.
2. **Complete:** create governance issue G0 as issue #70 using only metadata understood by current `main`.
3. **In progress:** review and make green one bounded Roadmap Sync/config PR. The PR adds/tests `V2 Phase`, `V2 Disposition`, expanded Evidence, V2 views, replacement Project description/README and lifecycle-aware retention without mutating the live Project from the branch.
4. **Pre-merge gate:** approve the explicit retention value, resolve issue #63 and every other stop condition, and give a GO that covers both merge and the resulting trusted-default-branch reconciliation.
5. Merge, then audit the automatic reconciliation of schema, views and retention against the hashed baseline.
6. Only after that audit, create E0–E7/B2A–B8 and append V2 metadata/cutover notes in bounded batches while preserving existing `Phase`, old metadata, semantically equal option IDs and original acceptance criteria.
7. Reconcile through the trusted-default-branch workflow and audit all counts/options/IDs/states/links/views/Evidence/retention outcomes.
8. Record actual successor numbers and migration evidence in the repository.
9. After a separate verified review, retire fully covered `SPLIT`/`SUPERSEDED` anchors individually, then authorize PR 2A.

`V2 Disposition` values are `ACTIVE NOW`, `NEXT`, `LATER`, `SPLIT`, `SUPERSEDED` and `RETIRED`. Disposition is not completion. Issue #56 remains Done and issue #62 stays open. The intended state sequence for issue #63 is suspended because preflight found it already completed; do not infer or execute a correction. Do not bulk-retire anchors.

During migration G0 is the single concrete `ACTIVE NOW`. After verified migration B2A becomes `ACTIVE NOW`, B3 alone is `NEXT`, and B4–B8 remain `LATER` until their direct predecessor is accepted. Program/epic routing cannot create a second concrete delivery head.

The Evidence field becomes, in order: `Not Applicable`, `Unproven`, `Fixture`, `Operationally Verified`, `Engineering Validation`, `Research Candidate`, `Research Ready`, `Shadow`, `Paper Proven`, `Live Proven`. Preserve option IDs for the six existing semantically equal values. Programs/epics use `Not Applicable`; operational governance is `Unproven` until verified then `Operationally Verified`; #56 is `Operationally Verified`; research advances only through its named evidence gates.

The config PR replaces the Project short description and README with the approved E0 data-first text and a link to [`../HANDOFF_V2.md`](../HANDOFF_V2.md), explicitly naming edge discovery or falsification, Observatory before Workstation, Triton-only and no profitability assumption.

Retention keeps open V2 issues/open PRs active and binds the exact 30 active linked merged pre-V2 PR candidates—PRs #1–#26, #64, #65, #67 and #68—to the root snapshot hash, a separate candidate-set hash and a sorted config allowlist. Before its first mutation, initial reconciliation rereads the repository and Project and fails on a merged-set mismatch or an audited candidate that is absent/already archived. G0 proposes an explicit `closed_item_retention_days: 30`; there is no fallback or implicit default, and the value remains subject to review before merge. Archived closed items remain archived unless an actual GitHub reopen transition is evidenced; no item or GitHub history is deleted. Issue #56 is a pinned continuing control. Issue #63 is temporarily pinned to preserve visibility of its unresolved stop condition, without implying a state correction. Tests cover eligibility before both add and unarchive, audited-set/initial-membership drift, the configured grace period, closed-item non-reactivation, evidence-backed reopen behavior and deterministic planned counts. No archival runs from the PR branch; an approved merge causes the push-to-main reconciliation to apply the reviewed policy.

GitHub exposes `ProjectV2Item.updatedAt`, not a dedicated archive timestamp. Reopen eligibility therefore requires the underlying item to be open and its latest `ReopenedEvent.createdAt` to be strictly newer than that Project timestamp. Ambiguity remains archived. This fail-closed comparison and the post-mutation item requery are tested, but the timestamp relationship remains an explicit operational-verification item in the first controlled migration audit.

The initial migration does not retire anchors. After all mappings/notes/fields/views pass, fully covered `SPLIT`/`SUPERSEDED` anchors may be marked individually as `not planned / superseded`; standalone `LATER` requirements remain open. Issue #56 stays Done and issue #62 stays open. Issue #63 follows only the separately approved resolution of its preflight discrepancy.

The exact #27–#63 ledger, successor catalog, V2 phases, filters, acceptance checks and rollback are in [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md).

## Offline validation

Configuration validation uses no token/network:

```bash
node scripts/github-projects/sync.mjs --dry-run
npm test -- --run tests/github-projects-sync.test.ts
npm test -- --run tests/github-projects-lifecycle.test.ts
```

Do not manually dispatch a V2 reconciliation from the G0 PR branch. Ordinary PR-event reconciliation under current trusted-main configuration is expected and must be allowed to finish. Resolve every stop condition before merge. The merge GO must explicitly cover the automatic push-to-main reconciliation through the protected Actions secret path; do not place a token on a command line.

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

The current preflight stop set includes issue #63 already being `CLOSED` / `COMPLETED` although the accepted plan expected it to remain open during initial migration. Do not silently normalize this discrepancy.

Use the hashed preflight export and original issue bodies for explicit rollback. Never “repair” mismatches by overwriting history.

## Project truth boundary

Project #4 is the delivery cockpit, not evidence by itself. Tests, code, manifests, source hashes, issue/PR history and accepted review determine whether a field such as Research Ready or Paper Proven is justified. A Project status cannot turn fixture/synthetic data into authentic research evidence.
